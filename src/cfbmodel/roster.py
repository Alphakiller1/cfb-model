"""Roster and staff facts the ratings cannot infer from results.

Power ratings measure what a team *did*. Between seasons, three things change
who that team now is, and none of them leave a trace in last year's margins:

* **The transfer portal.** A team can lose or gain a starting quarterback in
  February. `matrix.TALENT_WEIGHTS` has declared `portal_net_rating` at 0.25
  since the scaffold was written and nothing ever fetched it.
* **Which production returned, not how much.** `preseason.py` uses `percentPPA`,
  a single blended share. A team returning 60% of its production with a new
  quarterback and one returning 60% with the same quarterback are not the same
  team, and the blend cannot tell them apart. The same CFBD endpoint already
  publishes `percentPassingPPA`, so this costs no extra request.
* **A new head coach.** A first-year staff invalidates part of what the prior
  season measured — scheme, tempo, and fourth-down policy all move together.

Every feature here is centred on the FBS mean for the season, so a coefficient
is interpretable and a missing feed contributes zero rather than a spurious
level shift. That matters more than usual: these feeds are patchier than the
core stats, and a silent zero must mean "no information", not "average team".

Nothing in this module is fitted. It produces *candidates*; `cli fit-preseason`
measures whether they earn a coefficient, and `preseason.COEFFICIENTS` carries
only terms that did.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from cfbmodel.sources import cfbd

# A portal entry with no destination is an unsigned departure. Weighted lower
# than a completed transfer because the player may yet return or land elsewhere.
UNSIGNED_DEPARTURE_WEIGHT = 0.5

# Fallback when a team has no published passing-production figure. The league
# mean is substituted at the point of use, not here, so "missing" stays visible.
QB_RETURNING_UNKNOWN = None


@dataclass(frozen=True)
class TeamRoster:
    """Pre-kickoff roster and staff facts for one team.

    Every field is optional. A team with no portal coverage is not a team with
    a net portal rating of zero, and collapsing those two states was exactly
    how `returning_production` ended up meaning nothing in particular.
    """

    portal_net: float | None = None
    portal_in: int = 0
    portal_out: int = 0
    qb_returning: float | None = None
    overall_returning: float | None = None
    first_year_coach: bool | None = None

    @property
    def portal_churn(self) -> int:
        return self.portal_in + self.portal_out


def _rating(row: dict) -> float | None:
    """Player quality from a portal row, preferring the composite rating.

    `stars` is coarse (a 3-star covers a wide band) but is populated far more
    often than `rating`, so it is the fallback rather than the primary. The
    star-to-rating map is the conventional 247 composite banding.
    """
    value = row.get("rating")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    stars = row.get("stars")
    try:
        stars = int(stars)
    except (TypeError, ValueError):
        return None
    return {5: 0.98, 4: 0.92, 3: 0.85, 2: 0.78, 1: 0.72}.get(stars)


def portal_net(season: int) -> dict[str, float]:
    """Team -> net portal quality (arrivals minus departures).

    Sums player ratings rather than counting bodies: losing one starting
    quarterback and gaining three walk-ons is not a net gain of two.
    """
    try:
        rows = cfbd.portal(season)
    except Exception:
        return {}
    net: dict[str, float] = {}
    for row in rows:
        value = _rating(row)
        if value is None:
            continue
        origin = row.get("origin")
        destination = row.get("destination")
        if origin:
            # An unsigned departure is still a loss to the origin school.
            weight = 1.0 if destination else UNSIGNED_DEPARTURE_WEIGHT
            net[origin] = net.get(origin, 0.0) - value * weight
        if destination:
            net[destination] = net.get(destination, 0.0) + value
    return net


def portal_counts(season: int) -> dict[str, tuple[int, int]]:
    """Team -> (arrivals, departures). Churn is a signal even without ratings."""
    try:
        rows = cfbd.portal(season)
    except Exception:
        return {}
    counts: dict[str, list[int]] = {}
    for row in rows:
        if row.get("destination"):
            counts.setdefault(row["destination"], [0, 0])[0] += 1
        if row.get("origin"):
            counts.setdefault(row["origin"], [0, 0])[1] += 1
    return {team: (arrive, depart) for team, (arrive, depart) in counts.items()}


def returning(season: int) -> dict[str, tuple[float | None, float | None]]:
    """Team -> (overall percentPPA, quarterback percentPassingPPA).

    Both come from the one `/player/returning` call `preseason.py` already
    makes, so splitting the quarterback out is free.
    """
    try:
        rows = cfbd.returning_production(season)
    except Exception:
        return {}
    out: dict[str, tuple[float | None, float | None]] = {}
    for row in rows:
        team = row.get("team")
        if not team:
            continue

        def _num(key: str) -> float | None:
            value = row.get(key)
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        out[team] = (_num("percentPPA"), _num("percentPassingPPA"))
    return out


def first_year_coaches(season: int) -> dict[str, bool]:
    """Team -> whether its head coach is in his first season there.

    Derived from the coach's own `seasons` list rather than by diffing two
    years of a roster endpoint: a coach who left and returned is correctly a
    first year *at that school this season*, which a naive diff gets wrong.
    """
    try:
        # See `cfbd.coaches`: `?year=` returns a one-entry `seasons` list, which
        # would make every coach a first-year hire.
        rows = cfbd.coaches(season, history=8)
    except Exception:
        return {}
    out: dict[str, bool] = {}
    for coach in rows:
        seasons = coach.get("seasons") or []
        for entry in seasons:
            school, year = entry.get("school"), entry.get("year")
            if not school or year != season:
                continue
            prior = {
                e.get("year") for e in seasons
                if e.get("school") == school and isinstance(e.get("year"), int)
                and e["year"] < season
            }
            # Contiguous prior season at the same school = not a first year.
            out[school] = (season - 1) not in prior
    return out


def load(season: int) -> dict[str, TeamRoster]:
    """Assemble every pre-kickoff roster fact for one season."""
    net = portal_net(season)
    counts = portal_counts(season)
    ret = returning(season)
    new_coach = first_year_coaches(season)

    teams = set(net) | set(counts) | set(ret) | set(new_coach)
    out: dict[str, TeamRoster] = {}
    for team in teams:
        arrive, depart = counts.get(team, (0, 0))
        overall, qb = ret.get(team, (None, None))
        out[team] = TeamRoster(
            portal_net=net.get(team),
            portal_in=arrive,
            portal_out=depart,
            qb_returning=qb,
            overall_returning=overall,
            first_year_coach=new_coach.get(team),
        )
    return out


def centred(values: dict[str, float | None]) -> dict[str, float]:
    """Centre on the FBS mean, dropping missing entries rather than zeroing them.

    A team absent from the result contributes nothing to its rating. A team
    present with 0.0 is genuinely league-average. Those are different claims and
    the caller can tell them apart.
    """
    present = {k: v for k, v in values.items() if v is not None}
    if not present:
        return {}
    mean = statistics.fmean(present.values())
    return {k: v - mean for k, v in present.items()}


def features(season: int) -> dict[str, dict[str, float]]:
    """Team -> centred candidate features, ready for the preseason fit.

    Keys are the names a fitted coefficient would carry, so what was measured
    and what is applied cannot drift apart.
    """
    rosters = load(season)
    net = centred({t: r.portal_net for t, r in rosters.items()})
    qb = centred({t: r.qb_returning for t, r in rosters.items()})
    churn = centred({t: float(r.portal_churn) or None for t, r in rosters.items()})

    out: dict[str, dict[str, float]] = {}
    for team, roster in rosters.items():
        row: dict[str, float] = {}
        if team in net:
            row["portal_net"] = net[team]
        if team in qb:
            row["qb_returning"] = qb[team]
        if team in churn:
            row["portal_churn"] = churn[team]
        # `first_year_coach` is deliberately NOT emitted here. `coaching.py`
        # owns every staff fact, and having both modules publish the same key
        # meant the fit counted one feature twice.
        if row:
            out[team] = row
    return out
