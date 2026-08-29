"""Game forecast: opponent-adjusted efficiency over power ratings, anchored to
the market.

Two prediction regimes, because they were validated separately and mixing them
would be a fit applied outside where it was measured:

* **Full** -- both teams have complete opponent-adjusted form. Uses the jointly
  fitted model: `intercept + rating_margin * base + efficiency`. MAE 12.5251.
* **Ratings-only fallback** -- either side is missing form (weeks 1-4, or a team
  with no prior games). The joint coefficients cannot be used here: the rating
  term carries only 0.4476 because efficiency carries the rest, so applying it
  alone would shrink every margin toward zero. Falls back to the separately
  validated ratings path with its own bias correction. MAE 12.9749.

The anchor weight `lam` is the fraction of the model's disagreement with the
closing line that is kept. It defaults to 0.0, and that default is a measurement:
across 3,256 out-of-sample games the model's MAE is 12.5251 against the market's
12.1596, and ATS on disagreements is 51.11% (95% CI [49.39%, 52.84%]) against a
52.38% breakeven. The model no longer loses *confidently* -- the interval now
straddles breakeven -- but the point estimate is still short, so the honest
anchored answer remains the price.

Raising `lam` is a claim about evidence and belongs with a gate record, not a
config tweak.
"""

from __future__ import annotations

from dataclasses import dataclass

from cfbmodel import calibration, matrix, ratings, totals
from cfbmodel.authority import Action, Authority, current

# Fraction of model-vs-market disagreement retained. See module docstring.
DEFAULT_LAM = 0.0

# Correction for the ratings-only fallback. `ratings.projected_margin` runs
# +2.123 points high for the home side; this is measured directly, and the
# independently fitted intercept of the old single-regime model matched it to
# three decimals. Only used when efficiency form is unavailable -- the full
# model has its own jointly fitted intercept.
RATING_BIAS_CORRECTION = -2.1204

# The backtest covered weeks 5+ of FBS-vs-FBS games. Earlier weeks have no
# current-season form and lean entirely on discounted carryover ratings, and
# week-1 slates are full of power-vs-Group-of-5 mismatches that barely occur
# later. On the 2026 week 1 slate the model read Indiana -12 against a market of
# -40.8, and the market was right.
FIRST_VALIDATED_WEEK = 5


@dataclass(frozen=True)
class Forecast:
    home: str
    away: str
    neutral: bool
    raw_model_margin: float | None
    model_margin: float | None
    market_margin: float | None
    margin: float | None
    win_probability: float | None
    # Signed difference between the model and the price, published ONLY where it
    # is a disagreement the model has earned. See `_edge` for why the preseason
    # path returns None here and reports `market_gap` instead.
    edge_points: float | None
    # The raw model-minus-market difference, always present when both exist. In
    # the preseason regime this is dominated by information the market has and
    # the model does not, so it is a diagnostic, not an opportunity.
    market_gap: float | None
    market_anchored: bool
    action: Action
    authority: Authority
    in_validated_regime: bool = True
    used_efficiency: bool = False
    # Why `edge_points` is None despite a price being available. None when the
    # edge is published.
    edge_withheld_reason: str | None = None
    projected_total: float | None = None
    projected_home_score: float | None = None
    projected_away_score: float | None = None
    total_modelled: bool = False
    total_basis: str = "unavailable"
    market_total: float | None = None
    # Live sportsbook line, distinct from the CFBD consensus used as the model's
    # benchmark. Present only when an odds feed is configured and matched.
    book_name: str | None = None
    book_margin: float | None = None
    book_total: float | None = None

    @property
    def has_price(self) -> bool:
        return self.market_margin is not None

    @property
    def total_edge(self) -> float | None:
        """Model total minus market total, or None without both."""
        if self.projected_total is None or self.market_total is None:
            return None
        return self.projected_total - self.market_total


def _model_margin(
    base: float,
    home_form: matrix.TeamForm | None,
    away_form: matrix.TeamForm | None,
) -> tuple[float, bool]:
    """Return (margin, used_efficiency)."""
    if home_form is not None and away_form is not None:
        efficiency = matrix.margin_points(home_form, away_form)
        if efficiency is not None:
            c = matrix.COEFFICIENTS
            return c["intercept"] + c["rating_margin"] * base + efficiency, True
    return base + RATING_BIAS_CORRECTION, False


def _edge(
    market_gap: float | None,
    *,
    used_efficiency: bool,
    in_regime: bool,
) -> tuple[float | None, str | None]:
    """Decide whether the model-minus-market difference is publishable as an edge.

    It is not, on the preseason path -- but for a different reason than the one
    first written here, and the correction matters.

    Measured 2026-08-27 over 1,015 completed preseason-path games (2021-2025,
    weeks 1-6), the calibration slope of actual margin on model margin is
    **1.020**. The model is correctly SCALED. An earlier reading of a single
    2026 week-1 board put that slope near 0.5 and concluded the estimate was
    compressed; against real outcomes on a sample twenty times larger it is not.
    Week 1 is the most buy-game-heavy week of the season and 49 games was too
    few to see it.

    What survives is an information gap, not a scale error. On the same games
    the model's MAE is 13.9129 against the market's 12.0770 -- **1.84 points
    worse** -- and its dispersion is 0.81 of the market's. Both estimators are
    calibrated; the market simply conditions on more (transfer portal beyond
    what is fitted here, injuries, availability, everything priced between the
    Sunday opener and kickoff). In the validated regime the same gap is 0.54
    points and dispersion 0.95.

    So the difference between the two numbers is dominated by what the market
    knows and the model does not. Publishing it as an "edge" would assert that
    the model has found something, when what it has found is its own blind spot.
    The difference stays visible as `market_gap` and is not called an edge.
    """
    if market_gap is None:
        return None, None
    if not used_efficiency:
        return None, "preseason prior — difference is the information gap, not an edge"
    if not in_regime:
        return None, f"week < {FIRST_VALIDATED_WEEK} — outside the validated regime"
    return market_gap, None


def game(
    *,
    home: str,
    away: str,
    team_ratings: dict[str, float],
    neutral: bool = False,
    home_form: matrix.TeamForm | None = None,
    away_form: matrix.TeamForm | None = None,
    market_margin: float | None = None,
    lam: float = DEFAULT_LAM,
    authority: Authority | None = None,
    week: int | None = None,
    market_total: float | None = None,
    book: object | None = None,
    preseason_total: float | None = None,
) -> Forecast:
    """Forecast one game. `market_margin` is the expected HOME margin."""
    auth = authority or current()
    in_regime = True if week is None else week >= FIRST_VALIDATED_WEEK

    base = ratings.projected_margin(team_ratings, home, away, neutral=neutral)
    if base is None:
        raw_model_margin, used_efficiency = None, False
    else:
        raw_model_margin, used_efficiency = _model_margin(base, home_form, away_form)

    # Week 1 is a distinct, historically under-dispersed regime. Apply only the
    # correction measured on held-out Week 1 seasons; never stretch later weeks
    # or the full efficiency model by analogy.
    if week == 1 and not used_efficiency:
        model_margin = calibration.WEEK1.apply(raw_model_margin)
    else:
        model_margin = raw_model_margin

    if market_margin is None:
        published, anchored, market_gap = model_margin, False, None
    else:
        anchored = True
        market_gap = None if model_margin is None else model_margin - market_margin
        published = market_margin if market_gap is None else market_margin + lam * market_gap
    edge, withheld = _edge(market_gap, used_efficiency=used_efficiency, in_regime=in_regime)

    win_p = None if published is None else ratings.win_probability(published)
    # The scoreline is the MODEL's projection: model margin + model total. It is
    # deliberately not built from the published margin, which at lam = 0 is just
    # the market -- a "projected score" that silently restated the market's
    # number would be the market's projection wearing the model's label. The
    # board shows the market columns alongside it, so the two stay comparable.
    projection = totals.project(
        model_margin, home_form, away_form, preseason=preseason_total
    )
    return Forecast(
        home=home, away=away, neutral=neutral,
        raw_model_margin=raw_model_margin,
        model_margin=model_margin, market_margin=market_margin,
        margin=published, win_probability=win_p, edge_points=edge,
        market_gap=market_gap,
        market_anchored=anchored,
        action=auth.action_for(edge, market_margin is not None),
        authority=auth,
        in_validated_regime=in_regime,
        used_efficiency=used_efficiency,
        edge_withheld_reason=withheld,
        projected_total=projection.total,
        projected_home_score=projection.home_score,
        projected_away_score=projection.away_score,
        total_modelled=projection.modelled,
        total_basis=projection.basis,
        market_total=market_total,
        book_name=getattr(book, "book_title", None),
        book_margin=getattr(book, "home_margin", None),
        book_total=getattr(book, "total", None),
    )
