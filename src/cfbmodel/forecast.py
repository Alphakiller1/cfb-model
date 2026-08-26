"""Game forecast: ratings + efficiency matrix, anchored to the market.

The anchor weight `lam` is the fraction of the *model's* disagreement with the
closing line that is kept. It defaults to 0.0, and that default is a measurement,
not modesty: across 3,256 out-of-sample games the model's MAE is 12.7883 against
the closing market's 12.1596, and the ATS record on disagreements is 49.32%
(95% CI [47.59%, 51.04%], breakeven 52.38%). Keeping any of the disagreement made
the forecast worse, so the honest anchored answer is the price itself.

`lam` is exposed rather than hard-coded so the number can be re-estimated when
more evidence exists -- but raising it is a claim about evidence and belongs with
a gate record, not a config tweak.

When there is no market (preseason, or an unpriced game) the model view is all
there is, and `market_anchored` is False so downstream can tell the difference.
"""

from __future__ import annotations

from dataclasses import dataclass

from cfbmodel import matrix, ratings
from cfbmodel.authority import Action, Authority, current

# Fraction of model-vs-market disagreement retained. See module docstring.
DEFAULT_LAM = 0.0

# The backtest covered weeks 5+ of FBS-vs-FBS games. Earlier weeks have no
# current-season form and lean entirely on discounted carryover ratings, and
# week-1 slates are full of power-conference-vs-Group-of-5 mismatches that barely
# occur later. Forecasts outside that regime are reported but flagged: on the
# 2026 week 1 slate the model read Indiana -14 against a market of -40.8, and it
# was the market that was right.
FIRST_VALIDATED_WEEK = 5

# Points of margin per unit of combined efficiency-index edge, fitted by
# regressing the ratings residual on the index edge over 3,256 games
# (see reports/BASELINE_2019_2025.md). The intercept is a calibration term: on
# FBS-vs-FBS games with complete prior form, the rating projection alone runs
# about two points high for the home side.
EFFICIENCY_POINTS_PER_INDEX = 0.4842
EFFICIENCY_INTERCEPT = -2.1204


@dataclass(frozen=True)
class Forecast:
    home: str
    away: str
    neutral: bool
    model_margin: float | None          # model's own view, home perspective
    market_margin: float | None         # market's expected home margin
    margin: float | None                # what the model actually publishes
    win_probability: float | None
    edge_points: float | None           # model - market, None without a price
    market_anchored: bool
    action: Action
    authority: Authority
    in_validated_regime: bool = True

    @property
    def has_price(self) -> bool:
        return self.market_margin is not None


def _efficiency_edge(home: matrix.TeamForm | None, away: matrix.TeamForm | None) -> float:
    """Points of margin from the efficiency matrix, 0.0 when form is incomplete.

    Collapsing nine features into two weighted indices costs some accuracy
    against fitting them individually (12.884 vs 12.788 MAE); the indices are
    kept because they are what a *matrix* means -- interpretable groups whose
    weights can be read and argued with.
    """
    if home is None or away is None:
        return 0.0
    parts = []
    for form, sign in ((home, 1.0), (away, -1.0)):
        off, dfn = matrix.offense_index(form), matrix.defense_index(form)
        if off is None or dfn is None:
            return 0.0     # partial form is no form; never half-credit a team
        parts.append(sign * (off + dfn))
    return EFFICIENCY_INTERCEPT + EFFICIENCY_POINTS_PER_INDEX * sum(parts)


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
    """Forecast one game. `market_margin` is the expected HOME margin.

    Pass `week` so the forecast can say whether it falls inside the regime the
    model was actually validated on.
    """
    auth = authority or current()
    in_regime = True if week is None else week >= FIRST_VALIDATED_WEEK

    base = ratings.projected_margin(team_ratings, home, away, neutral=neutral)
    model_margin = None if base is None else base + _efficiency_edge(home_form, away_form)

    if market_margin is None:
        published = model_margin
        anchored = False
        edge = None
    else:
        anchored = True
        edge = None if model_margin is None else model_margin - market_margin
        # lam = 0 publishes the market exactly; lam = 1 publishes the model.
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
    )
