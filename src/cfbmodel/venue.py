"""Venue-specific home field.

`ratings.HOME_FIELD_POINTS` is one constant, 4.53, applied to all 136 programmes
and every non-neutral game. It was measured over 4,325 games and is a good
*average*, but it is an average over a population that plainly is not
homogeneous: a sea-level dome and a 7,220-foot stadium with a visitor flying
two time zones east are not the same 4.53 points.

Three effects, each derivable from data CFBD already publishes and none of which
the ratings can infer from margins (a rating that absorbed them would be
crediting the team for its altitude):

* **Elevation.** The visitor's disadvantage scales with the *difference* between
  the venue's elevation and the one the visiting programme trains at, not with
  the venue's raw height.
* **Travel.** Great-circle distance between the two programmes' home venues.
* **Body-clock.** Time-zone crossings, signed: a west-coast team playing an
  early eastern kickoff is the well-documented direction of the effect, and
  treating it symmetrically would cancel exactly the asymmetry that matters.

Nothing here is fitted. `home_field_features` produces the candidate terms and
`cli fit-preseason` decides whether they earn coefficients; until then
`ratings.HOME_FIELD_POINTS` remains the whole model of home field and this
module is inert.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from cfbmodel.sources import cfbd

EARTH_RADIUS_MILES = 3958.8

# CFBD publishes elevation in METRES, as a string ("2024.875" for Falcon
# Stadium). Converted to feet here because every other altitude figure in
# American football is quoted in feet, and a coefficient reading "points per
# 1,000 ft" is checkable against intuition in a way "per 1,000 m" is not.
FEET_PER_METRE = 3.28084

# Hours of body-clock shift per degree of longitude. Time zones are political
# and CFBD's `timezone` string is not always populated, so longitude is the
# robust proxy: 15 degrees is one hour.
DEGREES_PER_HOUR = 15.0


@dataclass(frozen=True)
class Venue:
    venue_id: int
    name: str | None
    latitude: float | None
    longitude: float | None
    elevation: float | None
    dome: bool | None

    @property
    def located(self) -> bool:
        return self.latitude is not None and self.longitude is not None


def _num(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def load() -> dict[int, Venue]:
    """Venue id -> Venue. Empty on any feed failure; callers degrade to the
    constant rather than to a wrong number."""
    try:
        rows = cfbd.venues()
    except Exception:
        return {}
    out: dict[int, Venue] = {}
    for row in rows:
        venue_id = row.get("id")
        if venue_id is None:
            continue
        # Verified against the live API 2026-08-27: coordinates are top-level
        # and elevation is a metres string. `cli check-sources` guards both.
        metres = _num(row.get("elevation"))
        out[int(venue_id)] = Venue(
            venue_id=int(venue_id),
            name=row.get("name"),
            latitude=_num(row.get("latitude")),
            longitude=_num(row.get("longitude")),
            elevation=None if metres is None else metres * FEET_PER_METRE,
            dome=row.get("dome") if isinstance(row.get("dome"), bool) else None,
        )
    return out


def distance_miles(a: Venue, b: Venue) -> float | None:
    """Great-circle distance between two venues."""
    if not (a.located and b.located):
        return None
    lat1, lon1, lat2, lon2 = map(
        math.radians, (a.latitude, a.longitude, b.latitude, b.longitude)
    )
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(min(1.0, math.sqrt(h)))


def timezone_shift(home: Venue, away: Venue) -> float | None:
    """Hours the VISITOR's body clock moves, signed.

    Positive means the visitor travelled east and is playing later than its body
    clock says — the direction with the documented penalty. Negative is a
    westward trip. Returned signed on purpose: an absolute value would cancel
    the asymmetry this feature exists to capture.
    """
    if not (home.located and away.located):
        return None
    # Longitude increases eastward, so a host east of the visitor gives a
    # positive difference and a positive (eastward) shift. No negation.
    return (home.longitude - away.longitude) / DEGREES_PER_HOUR


def elevation_gain(home: Venue, away: Venue) -> float | None:
    """Feet the visitor climbs. Negative when the visitor trains higher."""
    if home.elevation is None or away.elevation is None:
        return None
    return home.elevation - away.elevation


def home_field_features(
    home_venue: Venue | None,
    away_venue: Venue | None,
    *,
    neutral: bool = False,
) -> dict[str, float]:
    """Candidate terms modifying home field for one game.

    Empty on a neutral site: there is no host, so every term here is undefined
    rather than zero. Empty is also the answer when either venue is unknown —
    the caller falls back to the measured constant.
    """
    if neutral or home_venue is None or away_venue is None:
        return {}
    out: dict[str, float] = {}
    gain = elevation_gain(home_venue, away_venue)
    if gain is not None:
        # Thousands of feet, so a coefficient reads as points per 1,000 ft.
        out["elevation_gain_kft"] = gain / 1000.0
    miles = distance_miles(home_venue, away_venue)
    if miles is not None:
        out["travel_kmiles"] = miles / 1000.0
    shift = timezone_shift(home_venue, away_venue)
    if shift is not None:
        out["timezone_shift_hours"] = shift
    if home_venue.dome is not None:
        out["home_dome"] = 1.0 if home_venue.dome else 0.0
    return out


def team_venues(season: int) -> dict[str, int]:
    """Team -> its home venue id, taken from the season's scheduled home games.

    Derived from the schedule rather than a teams endpoint because that is what
    actually determines where a team hosts, including the years a programme
    plays somewhere else while its stadium is rebuilt.
    """
    try:
        rows = cfbd.games(season)
    except Exception:
        return {}
    counts: dict[str, dict[int, int]] = {}
    for game in rows:
        if game.get("neutralSite"):
            continue
        home, venue_id = game.get("homeTeam"), game.get("venueId")
        if not home or venue_id is None:
            continue
        counts.setdefault(home, {})
        counts[home][int(venue_id)] = counts[home].get(int(venue_id), 0) + 1
    return {
        team: max(venues.items(), key=lambda kv: kv[1])[0]
        for team, venues in counts.items() if venues
    }
