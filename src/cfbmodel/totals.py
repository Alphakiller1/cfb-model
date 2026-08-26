"""Combined-points model, and the projected scores derived from it.

Margin asks who is better, so it uses feature *differences*. A total asks how
much scoring the two teams generate together, so it uses *sums*, plus pace --
drives and plays per game. CFB tempo varies far more than NFL tempo, and two
efficient slow teams can produce a lower total than two mediocre fast ones.

Measured on the same 3,256-game walk-forward, leave-one-season-out:

    league-mean total   MAE 13.6544
    model total         MAE 13.0446
    market total        MAE 12.5055    model is 0.5391 worse

Over/under when the model disagrees with the market total: 1660-1567-29 =
51.44% (95% CI [49.72%, 53.17%], breakeven 52.38%). Same standing as the spread
side -- the interval straddles breakeven, so this is unproven rather than
losing, and it is not a betting signal.

Totals are genuinely hard: the residual SD is 16.36 against an actual total SD of
17.14, so the model explains only a small share of the variance. That is worth
stating plainly rather than dressing up, because a projected scoreline looks far
more precise than it is.

**Projected scores** are just algebra on the two projections:

    home = (total + margin) / 2
    away = (total - margin) / 2

They inherit the error of *both* models, so a scoreline should be read as a
centre of mass, not a prediction of the actual score.
"""

from __future__ import annotations

from dataclasses import dataclass

from cfbmodel import matrix

# Fitted on 3,256 out-of-sample games, opponent-adjusted features plus pace.
COEFFICIENTS: dict[str, float] = {
    "intercept": -72.210648,
    "off_ppa_sum": 8.087215,
    "def_ppa_sum": 11.836823,
    "off_successRate_sum": 31.445594,
    "def_successRate_sum": 35.196387,
    "off_explosiveness_sum": 9.721309,
    "def_explosiveness_sum": 3.607021,
    "off_stuffRate_sum": 2.028150,
    "def_stuffRate_sum": 11.147865,
    "drives_sum": 0.173363,
    "plays_sum": 0.168810,
}

# Residual SD of the total, used to express uncertainty honestly.
TOTAL_SD = 16.36

# League mean total, the fallback when a matchup cannot be modelled.
LEAGUE_MEAN_TOTAL = 52.45

_EFFICIENCY = ("ppa", "successRate", "explosiveness", "stuffRate")
_PACE = ("drives", "plays")


@dataclass(frozen=True)
class Projection:
    total: float | None
    home_score: float | None
    away_score: float | None
    modelled: bool          # False when the total fell back to the league mean


def _pace_complete(form: matrix.TeamForm) -> bool:
    return all(getattr(form, f, None) is not None for f in _PACE)


def total_points(home: matrix.TeamForm, away: matrix.TeamForm) -> float | None:
    """Projected combined points, or None when either side is incomplete."""
    if not (home.complete() and away.complete()):
        return None
    if not (_pace_complete(home) and _pace_complete(away)):
        return None
    c = COEFFICIENTS
    value = c["intercept"]
    for stat in _EFFICIENCY:
        value += c[f"off_{stat}_sum"] * (getattr(home, f"off_{stat}") + getattr(away, f"off_{stat}"))
        value += c[f"def_{stat}_sum"] * (getattr(home, f"def_{stat}") + getattr(away, f"def_{stat}"))
    for pace in _PACE:
        value += c[f"{pace}_sum"] * (getattr(home, pace) + getattr(away, pace))
    return value


def project(
    margin: float | None,
    home: matrix.TeamForm | None,
    away: matrix.TeamForm | None,
) -> Projection:
    """Turn a margin plus both teams' form into a projected scoreline.

    With no usable form the total falls back to the league mean rather than
    refusing to produce a scoreline -- a centred guess is more useful than a
    blank, and `modelled` marks which one the caller got.
    """
    if margin is None:
        return Projection(None, None, None, False)

    total = None
    if home is not None and away is not None:
        total = total_points(home, away)
    modelled = total is not None
    if total is None:
        total = LEAGUE_MEAN_TOTAL

    # Scores cannot be negative; a huge projected margin against a modest total
    # would otherwise produce one.
    home_score = (total + margin) / 2.0
    away_score = (total - margin) / 2.0
    if away_score < 0:
        away_score, home_score = 0.0, total
    elif home_score < 0:
        home_score, away_score = 0.0, total
    return Projection(total, home_score, away_score, modelled)
