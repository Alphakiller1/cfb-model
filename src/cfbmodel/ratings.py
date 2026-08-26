"""Opponent-adjusted, blowout-capped power ratings.

A rating is points relative to an average FBS team on a neutral field, so the
projected neutral margin between two teams is the difference of their ratings and
home field adds `HOME_FIELD_POINTS` to the host.

Why capping matters here and not in the NFL
-------------------------------------------
36% of FBS games are decided by 28+ points; the NFL figure is closer to 8%. A
44-point win over a bad team says little more than a 30-point win did, but an
unadjusted mean margin treats the extra 14 as real signal. Every parameter below
was chosen by walk-forward test on 3,256 out-of-sample FBS-vs-FBS games across
2019 and 2021-2025 (2020 excluded -- the COVID schedule was not comparable):

    cap        MAE          cap        MAE
    none    13.478          32      12.991   <- selected
    42      13.019          28      13.009
    35      12.991          21      13.139
                            14      13.487

Capping is worth about half a point of MAE. The cap is smooth rather than a hard
clip -- `cap * tanh(margin / cap)` -- so ordering is preserved above the
threshold instead of every blowout collapsing to one value.

Recency (half-life 12 weeks) and shrinkage were tuned the same way, but their
gains are small (12.991 -> 12.975) and may not survive out of this sample. Cap is
the parameter that carries the effect; treat the other two as marginal.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

# All fitted on 2019, 2021-2025 walk-forward. See module docstring.
BLOWOUT_CAP = 32.0
HOME_FIELD_POINTS = 4.53
RECENCY_HALFLIFE_WEEKS = 12.0
SHRINK = 1.0
ITERATIONS = 15

# FCS opponents are not individually rated -- there are hundreds of them and FBS
# teams play them once. They share one pooled rating so those games still inform
# the FBS side without inventing a rating per FCS program.
FCS = "__FCS__"

# Margin SD used to turn a projected margin into a win probability. Measured at
# 24.2 on 2025 FBS games, well above the NFL's ~13.5.
MARGIN_SD = 24.2


def cap_margin(margin: float, cap: float = BLOWOUT_CAP) -> float:
    """Smoothly compress a margin toward `cap`, preserving order."""
    if cap <= 0:
        return margin
    return cap * math.tanh(margin / cap)


@dataclass(frozen=True)
class Game:
    week: int
    home: str
    away: str
    home_points: int
    away_points: int
    neutral: bool = False
    home_is_fbs: bool = True
    away_is_fbs: bool = True

    @property
    def margin(self) -> float:
        return float(self.home_points - self.away_points)


def _label(team: str, is_fbs: bool) -> str:
    return team if is_fbs else FCS


def build(
    games: list[Game],
    *,
    cap: float = BLOWOUT_CAP,
    home_field: float = HOME_FIELD_POINTS,
    halflife: float | None = RECENCY_HALFLIFE_WEEKS,
    iterations: int = ITERATIONS,
) -> dict[str, float]:
    """Solve `rating_i = weighted mean over games of (own capped margin + opponent rating)`.

    Home field is removed from each margin before rating, so a team is not
    credited for a soft home schedule. Ratings are centred on the FBS mean.
    """
    if not games:
        return {}
    latest_week = max(g.week for g in games)
    observations: list[tuple[str, str, float, float]] = []
    for g in games:
        adjustment = 0.0 if g.neutral else home_field
        margin = cap_margin(g.margin - adjustment, cap)
        weight = 1.0 if halflife is None else 0.5 ** ((latest_week - g.week) / halflife)
        home = _label(g.home, g.home_is_fbs)
        away = _label(g.away, g.away_is_fbs)
        observations.append((home, away, margin, weight))
        observations.append((away, home, -margin, weight))

    teams = sorted({team for team, _, _, _ in observations})
    ratings = dict.fromkeys(teams, 0.0)
    for _ in range(iterations):
        numerator = dict.fromkeys(teams, 0.0)
        denominator = dict.fromkeys(teams, 0.0)
        for team, opponent, margin, weight in observations:
            numerator[team] += weight * (margin + ratings.get(opponent, 0.0))
            denominator[team] += weight
        updated = {
            t: (numerator[t] / denominator[t] if denominator[t] else 0.0) for t in teams
        }
        fbs = [v for t, v in updated.items() if t != FCS]
        centre = statistics.fmean(fbs) if fbs else 0.0
        ratings = {t: v - centre for t, v in updated.items()}
    return {t: v * SHRINK for t, v in ratings.items()}


def projected_margin(
    ratings: dict[str, float], home: str, away: str, *, neutral: bool = False,
    home_field: float = HOME_FIELD_POINTS,
) -> float | None:
    """Expected home margin, or None when either team is unrated."""
    if home not in ratings or away not in ratings:
        return None
    return ratings[home] - ratings[away] + (0.0 if neutral else home_field)


def win_probability(margin: float, *, sd: float = MARGIN_SD) -> float:
    """Normal CDF of the projected margin. Symmetric and never exactly 0 or 1."""
    return 0.5 * (1.0 + math.erf(margin / (sd * math.sqrt(2.0))))
