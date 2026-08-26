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

from cfbmodel import matrix, ratings
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
    model_margin: float | None
    market_margin: float | None
    margin: float | None
    win_probability: float | None
    edge_points: float | None
    market_anchored: bool
    action: Action
    authority: Authority
    in_validated_regime: bool = True
    used_efficiency: bool = False

    @property
    def has_price(self) -> bool:
        return self.market_margin is not None


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
) -> Forecast:
    """Forecast one game. `market_margin` is the expected HOME margin."""
    auth = authority or current()
    in_regime = True if week is None else week >= FIRST_VALIDATED_WEEK

    base = ratings.projected_margin(team_ratings, home, away, neutral=neutral)
    if base is None:
        model_margin, used_efficiency = None, False
    else:
        model_margin, used_efficiency = _model_margin(base, home_form, away_form)

    if market_margin is None:
        published, anchored, edge = model_margin, False, None
    else:
        anchored = True
        edge = None if model_margin is None else model_margin - market_margin
        published = market_margin if edge is None else market_margin + lam * edge

    win_p = None if published is None else ratings.win_probability(published)
    return Forecast(
        home=home, away=away, neutral=neutral,
        model_margin=model_margin, market_margin=market_margin,
        margin=published, win_probability=win_p, edge_points=edge,
        market_anchored=anchored,
        action=auth.action_for(edge, market_margin is not None),
        authority=auth,
        in_validated_regime=in_regime,
        used_efficiency=used_efficiency,
    )
