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

import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cfbmodel import matrix

if TYPE_CHECKING:
    from cfbmodel.ratings import Game

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

# Week 1-4 estimator fitted on 1,191 point-in-time FBS-vs-FBS games from
# 2019 and 2021-2025. Each feature uses only the previous completed season.
# Leave-one-season-out MAE improved from 13.3942 for a constant total to
# 13.2209. The modest coefficients are deliberate carryover shrinkage: roster
# churn makes last year's scoring informative, but nowhere near fully persistent.
PRESEASON_COEFFICIENTS: dict[str, float] = {
    "intercept": 54.082055,
    "offense_sum": 0.255660,
    "defense_sum": 0.267813,
}
PRESEASON_MIN_GAMES = 5

_EFFICIENCY = ("ppa", "successRate", "explosiveness", "stuffRate")
_PACE = ("drives", "plays")


@dataclass(frozen=True)
class Projection:
    total: float | None
    home_score: float | None
    away_score: float | None
    modelled: bool
    basis: str = "unavailable"


@dataclass(frozen=True)
class ScoringProfile:
    points_for: float
    points_allowed: float
    games: int


@dataclass(frozen=True)
class PreseasonContext:
    profiles: dict[str, ScoringProfile]
    league_team_points: float


def preseason_context(games: list[Game]) -> PreseasonContext | None:
    """Build scoring priors from a fully completed previous season.

    Only FBS-vs-FBS games enter the profile, matching the slate being forecast.
    A five-game minimum prevents a partial/transition season from masquerading
    as a stable team scoring level.
    """
    accumulator: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for game in games:
        if not (game.home_is_fbs and game.away_is_fbs):
            continue
        accumulator[game.home][0] += game.home_points
        accumulator[game.home][1] += game.away_points
        accumulator[game.home][2] += 1
        accumulator[game.away][0] += game.away_points
        accumulator[game.away][1] += game.home_points
        accumulator[game.away][2] += 1
    profiles = {
        team: ScoringProfile(points_for / games_played,
                             points_allowed / games_played,
                             int(games_played))
        for team, (points_for, points_allowed, games_played) in accumulator.items()
        if games_played >= PRESEASON_MIN_GAMES
    }
    if not profiles:
        return None
    league_team_points = statistics.fmean(
        profile.points_for for profile in profiles.values()
    )
    return PreseasonContext(profiles, league_team_points)


def preseason_total(home: str, away: str,
                    context: PreseasonContext | None) -> float | None:
    """Matchup-specific early-season total from prior scoring and allowance.

    A promoted/new FBS team without a usable history is treated as league
    average for the missing side, rather than forcing the entire slate back to
    one constant. The returned value remains a preseason prior, not current form.
    """
    if context is None:
        return None
    mean = context.league_team_points
    home_profile = context.profiles.get(home)
    away_profile = context.profiles.get(away)
    home_for = home_profile.points_for if home_profile else mean
    away_for = away_profile.points_for if away_profile else mean
    home_allowed = home_profile.points_allowed if home_profile else mean
    away_allowed = away_profile.points_allowed if away_profile else mean
    offense_sum = home_for + away_for - 2.0 * mean
    defense_sum = home_allowed + away_allowed - 2.0 * mean
    c = PRESEASON_COEFFICIENTS
    return (c["intercept"] + c["offense_sum"] * offense_sum
            + c["defense_sum"] * defense_sum)


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
    *,
    preseason: float | None = None,
) -> Projection:
    """Turn a margin plus both teams' form into a projected scoreline.

    If neither current form nor a matchup-specific preseason prior is usable,
    abstain. A fixed league-average substitute would manufacture the same total
    for unrelated matchups and falsely imply information the model does not have.
    """
    if margin is None:
        return Projection(None, None, None, False, "unavailable")

    total = None
    if home is not None and away is not None:
        total = total_points(home, away)
    if total is not None:
        basis = "in_season_form"
    elif preseason is not None:
        total = preseason
        basis = "preseason_scoring_prior"
    else:
        return Projection(None, None, None, False, "unavailable")
    modelled = True

    # Scores cannot be negative; a huge projected margin against a modest total
    # would otherwise produce one.
    home_score = (total + margin) / 2.0
    away_score = (total - margin) / 2.0
    if away_score < 0:
        away_score, home_score = 0.0, total
    elif home_score < 0:
        home_score, away_score = 0.0, total
    return Projection(total, home_score, away_score, modelled, basis)
