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
from dataclasses import dataclass

from cfbmodel.sources import cfbd

# Fitted on 788 team-seasons (2019, 2021-2025). Talent and recruiting enter
# centred on that season's FBS mean, so the intercept stays interpretable.
COEFFICIENTS = {
    "intercept": -3.462662,
    "prior_rating": 0.392277,
    "prior_rating_2": 0.158761,
    "talent": 0.003267,
    "returning_production": 3.480384,
    "recruiting_points": 0.032038,
}

# Roster and staff terms, refitted 2026-08-27 over 658 team-seasons (2021-2025),
# leave-one-season-out. Held-out MAE 5.1055 -> 5.0526 in rating points.
#
# `coach_shift_explosiveness` and `coach_tenure` were fitted and then DROPPED:
# their fold-to-fold SD exceeded the magnitude of their own mean, so they
# changed sign between folds. `cli fit-preseason` re-runs the prune every time,
# so a term that stops generalising falls out rather than persisting.
#
# The headline is `first_year_coach` at -2.65 +/- 0.42: a new head office costs
# about two and a half rating points, and it is the one fact here the ratings
# provably cannot infer -- a coaching change leaves no trace in last season's
# margins. See docs/DATA_SOURCES.md and reports/BASELINE_2019_2025.md.
EXTRA_COEFFICIENTS = {
    "qb_returning": 0.128062,
    "portal_net": 0.090720,
    "portal_churn": 0.032986,
    "coach_shift_defensive_havoc": 21.308964,
    "coach_shift_pass_rate": 4.500083,
    "coach_shift_tempo": 1.170290,
    "first_year_coach": -2.648581,
}

# Share of the current season folded in per completed week. Tuned on weeks 1-4:
# 0.14/wk gave 13.8239, against 13.9451 at 0.06 and 14.1050 at 0.28.
IN_SEASON_SHARE_PER_WEEK = 0.14

# League-average returning production, used when a team has no published figure.
DEFAULT_RETURNING = 0.533

# Talent reconstruction. CFBD publishes a team talent composite through 2025 but
# not 2026, and the term would otherwise be silently zero for every team.
#
# Roster talent IS accumulated recruiting -- a team's composite in year Y is
# mostly the players it signed in Y-3..Y -- so it can be rebuilt from recruiting
# classes. Fitted on 1,088 team-years (2019-2025) and validated leave-one-year-out:
#
#     correlation with published talent   0.9350
#     R^2                                 0.8741
#     MAE                                 64.71   (talent SD 260.08)
#
# It also beats the obvious shortcut of using the current class alone (0.9149).
TALENT_LAGS = (0, 1, 2, 3)
TALENT_FROM_RECRUITING = {
    "intercept": -44.602865,
    "lag_0": 1.295870,
    "lag_1": 0.856787,
    "lag_2": 0.518837,
    "lag_3": 0.879389,
}


def _centred(mapping: dict[str, float]) -> tuple[dict[str, float], float]:
    if not mapping:
        return {}, 0.0
    mean = statistics.fmean(mapping.values())
    return {k: v - mean for k, v in mapping.items()}, mean


def _published_talent(season: int) -> dict[str, float]:
    try:
        rows = cfbd.talent(season)
    except Exception:
        return {}
    return {r["team"]: float(r["talent"]) for r in rows if r.get("talent") is not None}


def _reconstruct_talent(season: int) -> dict[str, float]:
    """Rebuild the talent composite from recruiting classes. See TALENT_LAGS."""
    classes = {season - lag: _recruiting(season - lag) for lag in TALENT_LAGS}
    coefficients = TALENT_FROM_RECRUITING
    teams: set[str] = set()
    for table in classes.values():
        teams |= set(table)
    out: dict[str, float] = {}
    for team in teams:
        points = []
        for lag in TALENT_LAGS:
            value = classes[season - lag].get(team)
            if value is None:
                break
            points.append(value)
        else:
            out[team] = coefficients["intercept"] + sum(
                coefficients[f"lag_{lag}"] * value
                for lag, value in zip(TALENT_LAGS, points)
            )
    return out


def talent_composite(season: int) -> tuple[dict[str, float], str]:
    """(talent by team, source). Published when CFBD has it, reconstructed otherwise."""
    published = _published_talent(season)
    if published:
        return published, "published"
    return _reconstruct_talent(season), "reconstructed"


def _talent(season: int) -> dict[str, float]:
    return talent_composite(season)[0]


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


@dataclass(frozen=True)
class Components:
    """What went into one team's preseason rating.

    `raw` is the published input (a talent composite, a returning-production
    share); `points` is what that input contributed to the rating after its
    coefficient. Keeping both means a breakdown can show the reader the actual
    recruiting number as well as what it was worth.
    """

    prior_rating: tuple[float, float]
    prior_rating_2: tuple[float, float]
    talent: tuple[float, float]
    returning_production: tuple[float, float]
    recruiting_points: tuple[float, float]
    intercept: float
    rating: float
    # "published" when CFBD supplied the talent composite, "reconstructed" when it
    # was rebuilt from recruiting classes. Shown in the breakdown so a reader is
    # never told a derived number is a published one.
    talent_source: str = "published"

    def rows(self) -> list[tuple[str, float, float]]:
        """(label, raw, points) in the order they should be displayed."""
        return [
            ("Prior season rating", *self.prior_rating),
            ("Two seasons back", *self.prior_rating_2),
            ("Recruiting talent", *self.talent),
            ("Returning production", *self.returning_production),
            ("Recruiting class", *self.recruiting_points),
        ]


def components(
    season: int,
    prior_ratings: dict[str, float],
    prior_ratings_2: dict[str, float] | None = None,
    extra: dict[str, dict[str, float]] | None = None,
) -> dict[str, Components]:
    """Per-team preseason rating with every term kept separate.

    `extra` carries the roster and staff features (`roster.features` /
    `coaching.features`). A team missing from it simply gets no contribution
    from those terms, which is the right default: every one is centred on the
    FBS mean, so absent means "no information", not "below average".
    """
    prior_ratings_2 = prior_ratings_2 or {}
    extra = extra or {}
    # talent_composite reports which source it used, so the breakdown can label a
    # reconstructed figure rather than presenting it as a published one.
    raw_talent, talent_source = talent_composite(season)
    talent, _ = _centred(raw_talent)
    recruiting, _ = _centred(_recruiting(season))
    returning = _returning(season)

    c = COEFFICIENTS
    out: dict[str, Components] = {}
    for team, r1 in prior_ratings.items():
        if team == "__FCS__":
            continue
        r2 = prior_ratings_2.get(team, 0.0)
        tal = talent.get(team, 0.0)
        ret = returning.get(team, DEFAULT_RETURNING)
        rec = recruiting.get(team, 0.0)
        terms = Components(
            prior_rating=(r1, c["prior_rating"] * r1),
            prior_rating_2=(r2, c["prior_rating_2"] * r2),
            talent=(tal, c["talent"] * tal),
            returning_production=(ret, c["returning_production"] * ret),
            recruiting_points=(rec, c["recruiting_points"] * rec),
            intercept=c["intercept"],
            rating=0.0,
            talent_source=talent_source,
        )
        bonus_values = extra.get(team, {})
        bonus = sum(EXTRA_COEFFICIENTS[name] * value
                    for name, value in bonus_values.items()
                    if name in EXTRA_COEFFICIENTS)
        total = (bonus + terms.intercept + terms.prior_rating[1] + terms.prior_rating_2[1]
                 + terms.talent[1] + terms.returning_production[1]
                 + terms.recruiting_points[1])
        out[team] = Components(
            prior_rating=terms.prior_rating,
            prior_rating_2=terms.prior_rating_2,
            talent=terms.talent,
            returning_production=terms.returning_production,
            recruiting_points=terms.recruiting_points,
            intercept=terms.intercept,
            rating=total,
            talent_source=talent_source,
        )
    return out


def build(
    season: int,
    prior_ratings: dict[str, float],
    prior_ratings_2: dict[str, float] | None = None,
    extra: dict[str, dict[str, float]] | None = None,
) -> dict[str, float]:
    """Preseason rating for every team with a prior-season rating."""
    return {
        t: c.rating
        for t, c in components(season, prior_ratings, prior_ratings_2, extra).items()
    }


def roster_features(season: int) -> dict[str, dict[str, float]]:
    """Assemble the `extra` block. Any feed failure degrades to {} per team."""
    from cfbmodel import coaching, roster

    merged: dict[str, dict[str, float]] = {}
    for source in (roster.features(season), coaching.features(season)):
        for team, values in source.items():
            merged.setdefault(team, {}).update(values)
    return merged


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
