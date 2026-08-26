"""Preseason ratings: what the model believes before any games are played.

The in-season model needs results to rate a team, so weeks 1-4 previously ran on
the prior season's ratings scaled by a flat 0.72. That is a weak prior in a sport
where rosters turn over hard, and it showed: weeks 1-4 MAE was 14.2446 against a
market of 12.0043.

This fits a real preseason rating instead, from four things known before kickoff:

    rating ~ prior season rating
           + two seasons back
           + recruiting talent composite (centred on the FBS mean)
           + returning production (share of last year's PPA returning)
           + recruiting class points (centred)

Measured, predicting a team's eventual end-of-season rating (MAE in rating points
over 788 team-seasons, leave-one-season-out):

    carryover 0.72 x prior      5.5966
    prior rating only           5.6046
    + two seasons back          5.4535
    + talent                    5.3281
    + returning production      5.3149
    + recruiting points         5.1202   <- used

And on actual weeks 1-4 margins:

    carryover 0.72 (previous)   14.2446
    preseason prior, no blend   14.1585
    preseason + in-season blend 13.8239   <- used
    market                      12.0043

Worth 0.42 points, and 1.01 in week 1 specifically. **It does not make weeks 1-4
trustworthy.** The gap to the market there is still +1.82 against +0.37 from week
5 on, which is why `forecast.FIRST_VALIDATED_WEEK` stays at 5 and the board keeps
its out-of-regime warning.
"""

from __future__ import annotations

import statistics

from cfbmodel.sources import cfbd

# Fitted on 788 team-seasons (2019, 2021-2025). Talent and recruiting enter
# centred on that season's FBS mean, so the intercept stays interpretable.
COEFFICIENTS = {
    "intercept": -4.491291,
    "prior_rating": 0.392005,
    "prior_rating_2": 0.153911,
    "talent": 0.002221,
    "returning_production": 4.279209,
    "recruiting_points": 0.038215,
}

# Share of the current season folded in per completed week. Tuned on weeks 1-4:
# 0.14/wk gave 13.8239, against 13.9451 at 0.06 and 14.1050 at 0.28.
IN_SEASON_SHARE_PER_WEEK = 0.14

# League-average returning production, used when a team has no published figure.
DEFAULT_RETURNING = 0.533


def _centred(mapping: dict[str, float]) -> tuple[dict[str, float], float]:
    if not mapping:
        return {}, 0.0
    mean = statistics.fmean(mapping.values())
    return {k: v - mean for k, v in mapping.items()}, mean


def _talent(season: int) -> dict[str, float]:
    try:
        rows = cfbd.talent(season)
    except Exception:
        return {}
    return {r["team"]: float(r["talent"]) for r in rows if r.get("talent") is not None}


def _returning(season: int) -> dict[str, float]:
    try:
        rows = cfbd.returning_production(season)
    except Exception:
        return {}
    return {r["team"]: float(r["percentPPA"]) for r in rows if r.get("percentPPA") is not None}


def _recruiting(season: int) -> dict[str, float]:
    try:
        rows = cfbd.get(f"/recruiting/teams?year={season}")
    except Exception:
        return {}
    return {r["team"]: float(r["points"]) for r in rows if r.get("points") is not None}


def build(
    season: int,
    prior_ratings: dict[str, float],
    prior_ratings_2: dict[str, float] | None = None,
) -> dict[str, float]:
    """Preseason rating for every team with a prior-season rating."""
    prior_ratings_2 = prior_ratings_2 or {}
    talent, _ = _centred(_talent(season))
    recruiting, _ = _centred(_recruiting(season))
    returning = _returning(season)

    c = COEFFICIENTS
    out: dict[str, float] = {}
    for team, r1 in prior_ratings.items():
        if team == "__FCS__":
            continue
        out[team] = (
            c["intercept"]
            + c["prior_rating"] * r1
            + c["prior_rating_2"] * prior_ratings_2.get(team, 0.0)
            + c["talent"] * talent.get(team, 0.0)
            + c["returning_production"] * returning.get(team, DEFAULT_RETURNING)
            + c["recruiting_points"] * recruiting.get(team, 0.0)
        )
    return out


def blend(preseason: dict[str, float], in_season: dict[str, float], week: int) -> dict[str, float]:
    """Fold results in as they arrive; `week` is the week being forecast."""
    if not in_season:
        return dict(preseason)
    share = min(1.0, max(0.0, (week - 1) * IN_SEASON_SHARE_PER_WEEK))
    teams = set(preseason) | set(in_season)
    return {
        t: share * in_season.get(t, 0.0) + (1.0 - share) * preseason.get(t, 0.0)
        for t in teams
    }
