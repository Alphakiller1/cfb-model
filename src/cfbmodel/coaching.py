"""Coaching philosophy, and what changes when a staff does.

A power rating measures what a roster and a scheme did together. When the staff
changes, part of that measurement stops describing the team — and the ratings
have no way to know, because a coaching change leaves no trace in last season's
margins. The market prices it the day it is announced.

The predictive content is not "a new coach arrived", which is a one-bit fact
worth roughly one coefficient. It is **which** coach arrived: a team hiring an
air-raid coordinator from a 78-play-per-game programme is about to become a
different team in a knowable direction. So this module carries two things:

* `ChangeFlags` — first-year staff, and continuity.
* `Philosophy` — a coach's own tendency profile, built from the seasons he
  actually coached: tempo, pass rate, explosiveness, and defensive posture. For
  a first-year coach it is carried from his previous school, which is the whole
  point; for a continuing coach it is his record at the current one.

`expected_shift` is the difference between the team's profile last season and
its coach's profile — how far the scheme is expected to move. That is a
candidate feature, not a fitted one. `cli fit-preseason` decides whether it
earns a coefficient; nothing here modifies a rating on its own.

**What this module cannot see.** Coverage shells, personnel groupings, and
snap-level formation data are not published by CFBD at any tier — they come from
charting providers (PFF, SIS) under commercial licence. Injury and availability
feeds are likewise absent: CFBD has no injuries endpoint, and the public
alternatives are unofficial scrapes with no historical archive, so they cannot
be backtested even if scraped. Both gaps are real and neither is closeable from
this data source; see `docs/DATA_SOURCES.md`.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from cfbmodel.sources import cfbd

# A coach needs some history before his profile means anything. Below this, the
# profile is withheld rather than published from one noisy season.
MIN_PROFILE_SEASONS = 1

# Years of coaching record to request. Tenure only needs to know whether the
# previous season was at the same school, but a run longer than one year is what
# distinguishes a settled programme from a second-year hire.
TENURE_LOOKBACK = 8

# Tendency dimensions, all derivable from the advanced season stats the model
# already fetches. Names are the keys a fitted coefficient would carry.
DIMENSIONS = ("tempo", "pass_rate", "explosiveness", "defensive_havoc")


@dataclass(frozen=True)
class ChangeFlags:
    team: str
    coach: str | None
    first_year: bool
    seasons_at_school: int


@dataclass(frozen=True)
class Philosophy:
    """A coach's tendency profile, centred on the FBS mean of its season."""

    coach: str
    seasons: int
    tempo: float | None = None
    pass_rate: float | None = None
    explosiveness: float | None = None
    defensive_havoc: float | None = None

    def as_dict(self) -> dict[str, float]:
        return {
            name: value for name in DIMENSIONS
            if (value := getattr(self, name)) is not None
        }


def _num(value) -> float | None:
    """Numeric or None. CFBD sends nulls, and some numbers arrive as strings."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _season_entries(coach: dict) -> list[dict]:
    return [entry for entry in (coach.get("seasons") or []) if entry.get("school")]


def head_coaches(season: int) -> dict[str, ChangeFlags]:
    """Team -> who is coaching it and whether the staff is new.

    Tenure counts *contiguous* seasons at the school, so a coach returning after
    an absence is correctly a first year rather than inheriting his old run.
    """
    try:
        # History, not `?year=`: that filters `seasons` to one entry and would
        # make every coach in the league read as a first-year hire.
        rows = cfbd.coaches(season, history=TENURE_LOOKBACK)
    except Exception:
        return {}
    out: dict[str, ChangeFlags] = {}
    for coach in rows:
        name = " ".join(
            part for part in (coach.get("firstName"), coach.get("lastName")) if part
        ) or None
        entries = _season_entries(coach)
        for entry in entries:
            if entry.get("year") != season:
                continue
            school = entry["school"]
            years = sorted(
                e["year"] for e in entries
                if e.get("school") == school and isinstance(e.get("year"), int)
                and e["year"] <= season
            )
            run = 1
            for earlier, later in zip(years, years[1:]):
                run = run + 1 if later == earlier + 1 else 1
            out[school] = ChangeFlags(
                team=school, coach=name,
                first_year=(season - 1) not in set(years),
                seasons_at_school=run,
            )
    return out


def _team_profiles(season: int, *, through_week: int = 20) -> dict[str, dict[str, float]]:
    """Team -> centred tendency profile for one completed season."""
    try:
        rows = cfbd.advanced_season_stats(season, through_week=through_week)
    except Exception:
        return {}
    raw: dict[str, dict[str, float]] = {}
    for row in rows:
        team = row.get("team")
        offense = row.get("offense") or {}
        defense = row.get("defense") or {}
        if not team:
            continue
        values: dict[str, float] = {}
        # Older seasons carry nulls in the nested blocks. Every read goes
        # through `_num`, because a null here must mean "no measurement" and
        # never 0.0 -- these values are centred later, so a spurious zero would
        # become a confident claim of league-average.
        plays, drives = _num(offense.get("plays")), _num(offense.get("drives"))
        if plays and drives:
            # Plays per drive is the tempo measure that survives a bad defence
            # inflating raw play counts.
            values["tempo"] = plays / drives
        passing_block = offense.get("passingPlays") or {}
        rushing_block = offense.get("rushingPlays") or {}
        rate = _num(passing_block.get("rate"))
        if rate is not None:
            values["pass_rate"] = rate
        else:
            passing, rushing = _num(passing_block.get("totalPPA")), _num(rushing_block.get("totalPPA"))
            if passing is not None and rushing is not None and (passing + rushing):
                values["pass_rate"] = passing / (passing + rushing)
        explosiveness = _num(offense.get("explosiveness"))
        if explosiveness is not None:
            values["explosiveness"] = explosiveness
        havoc = defense.get("havoc")
        havoc_total = _num(havoc.get("total")) if isinstance(havoc, dict) else _num(havoc)
        if havoc_total is not None:
            values["defensive_havoc"] = havoc_total
        if values:
            raw[team] = values

    centred: dict[str, dict[str, float]] = {team: {} for team in raw}
    for name in DIMENSIONS:
        present = {t: v[name] for t, v in raw.items() if name in v}
        if not present:
            continue
        mean = statistics.fmean(present.values())
        for team, value in present.items():
            centred[team][name] = value - mean
    return {team: values for team, values in centred.items() if values}


def philosophies(season: int, *, lookback: int = 3) -> dict[str, Philosophy]:
    """Coach -> tendency profile, averaged over the seasons he actually coached.

    Built from seasons strictly BEFORE `season`: a preseason feature that read
    the season it is predicting would be leakage of the plainest kind.
    """
    try:
        rows = cfbd.coaches(season, history=lookback)
    except Exception:
        return {}
    prior_profiles = {
        year: _team_profiles(year)
        for year in range(season - lookback, season)
    }

    out: dict[str, Philosophy] = {}
    for coach in rows:
        name = " ".join(
            part for part in (coach.get("firstName"), coach.get("lastName")) if part
        )
        if not name:
            continue
        collected: dict[str, list[float]] = {}
        counted = 0
        for entry in _season_entries(coach):
            year, school = entry.get("year"), entry["school"]
            profile = prior_profiles.get(year, {}).get(school)
            if not profile:
                continue
            counted += 1
            for key, value in profile.items():
                collected.setdefault(key, []).append(value)
        if counted < MIN_PROFILE_SEASONS:
            continue
        out[name] = Philosophy(
            coach=name, seasons=counted,
            **{key: statistics.fmean(values) for key, values in collected.items()},
        )
    return out


def expected_shift(season: int, *, lookback: int = 3) -> dict[str, dict[str, float]]:
    """Team -> how far its scheme is expected to move, per dimension.

    `coach profile - team's own profile last season`. Near zero for a continuing
    staff, which is correct: continuity is the null hypothesis. Large for a
    first-year hire whose history differs from what the programme has been
    doing, which is exactly the case the ratings misread.
    """
    flags = head_coaches(season)
    profiles = philosophies(season, lookback=lookback)
    last_season = _team_profiles(season - 1)

    out: dict[str, dict[str, float]] = {}
    for team, flag in flags.items():
        philosophy = profiles.get(flag.coach or "")
        if philosophy is None:
            continue
        previous = last_season.get(team, {})
        shift = {
            f"coach_shift_{name}": value - previous.get(name, 0.0)
            for name, value in philosophy.as_dict().items()
        }
        if shift:
            shift["first_year_coach"] = 1.0 if flag.first_year else 0.0
            out[team] = shift
    return out


def features(season: int, *, lookback: int = 3) -> dict[str, dict[str, float]]:
    """Candidate coaching features for the preseason fit."""
    shifts = expected_shift(season, lookback=lookback)
    flags = head_coaches(season)
    out: dict[str, dict[str, float]] = {}
    for team, flag in flags.items():
        row = dict(shifts.get(team, {}))
        row.setdefault("first_year_coach", 1.0 if flag.first_year else 0.0)
        # Tenure saturates: year 8 is not meaningfully more settled than year 6.
        row["coach_tenure"] = float(min(flag.seasons_at_school, 6))
        out[team] = row
    return out
