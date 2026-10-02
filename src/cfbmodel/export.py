"""Machine-readable board export.

The dashboard in `site.py` is a rendered page: good for a human, useless to the
content engine, which needs the numbers rather than the markup. This module is
the one place that decides what a CFB board looks like as data, so the HTML
board and any downstream graphic are built from the same forecast objects
instead of one being re-derived by scraping the other.

Two things travel with each team that the engine cannot work out for itself:

* `logo` — CFBD's own mark URL. There are 136 FBS programmes and they are
  renamed, rebranded, and re-conferenced every year, so a hand-kept
  abbreviation map on the engine side would be wrong within a season.
* `neutral` — carried per game, because a neutral site removes the 4.53-point
  home-field term and a graphic that still printed "AT" would be describing a
  different game from the one the model forecast.

The payload states the authority level and `may_bet` at the top. Anything that
renders this is therefore unable to present the numbers without also having been
told what the model is licensed to claim.

`ratings_payload` is the second export this module owns: the power ratings that
the board is built from, ranked, so a downstream graphic ranks the same teams in
the same order the model does instead of re-sorting a printed table.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cfbmodel import authority as auth_mod
from cfbmodel import forecast as fc
from cfbmodel import ratings as ratings_mod
from cfbmodel import simulate, teams
from cfbmodel.ratings import FCS as RATINGS_FCS

SCHEMA_VERSION = "3.3.0"


def _team(season: int, school: str) -> dict[str, Any]:
    team = teams.get(season, school)
    return {
        "school": school,
        "abbreviation": team.abbreviation,
        "conference": team.conference,
        "color": team.color,
        "logo": team.logo,
    }


def _game(season: int, forecast: fc.Forecast, kickoff: datetime | None,
          team_status: dict | None = None) -> dict[str, Any]:
    team_status = team_status or {}
    return {
        "key": f"{forecast.away} @ {forecast.home}",
        "kickoff": kickoff.isoformat().replace("+00:00", "Z") if kickoff else None,
        "neutral": forecast.neutral,
        "away": _team(season, forecast.away),
        "home": _team(season, forecast.home),
        "raw_model_margin": forecast.raw_model_margin,
        "model_margin": forecast.model_margin,
        "market_margin": forecast.market_margin,
        "published_margin": forecast.margin,
        "forecast_source": forecast.forecast_source,
        "edge_points": forecast.edge_points,
        # Always present when both numbers exist. Outside the validated regime
        # this is the information gap, not a disagreement -- `edge_points` is
        # None there and `edge_withheld_reason` says why.
        "market_gap": forecast.market_gap,
        "edge_withheld_reason": forecast.edge_withheld_reason,
        "win_probability": forecast.win_probability,
        "home_field_points": forecast.home_field_points,
        "tier_adjustment": forecast.tier_adjustment,
        "simulations": forecast.simulations,
        "simulated_margin": forecast.simulated_margin,
        "simulated_win_probability": forecast.simulated_win_probability,
        "projected_total": forecast.projected_total,
        "independent_total": forecast.independent_total,
        "total_model_weight": forecast.total_model_weight,
        "market_total": forecast.market_total,
        "projected_away_score": forecast.projected_away_score,
        "projected_home_score": forecast.projected_home_score,
        "total_modelled": forecast.total_modelled,
        "total_basis": forecast.total_basis,
        "used_efficiency": forecast.used_efficiency,
        "model_regime": forecast.model_regime,
        "preseason_margin": forecast.preseason_margin,
        "efficiency_margin": forecast.efficiency_margin,
        "efficiency_reliability": forecast.efficiency_reliability,
        "in_validated_regime": forecast.in_validated_regime,
        "action": forecast.action.value,
        "book": {
            "name": forecast.book_name,
            "margin": forecast.book_margin,
            "total": forecast.book_total,
            "last_update": forecast.book_last_update,
            "commence_time": forecast.book_commence_time,
        } if forecast.book_name else None,
        # Conference availability report per side: the usual starting QB, his
        # designation, and every listed player. None = no report filed (unknown,
        # not healthy).
        "availability": {
            "home": team_status.get(forecast.home),
            "away": team_status.get(forecast.away),
        },
    }


def payload(
    *,
    season: int,
    week: int,
    rows: list[tuple[fc.Forecast, datetime | None]],
    authority: auth_mod.Authority | None = None,
    generated_at: datetime | None = None,
    player_projections: tuple[list[dict], dict] | None = None,
    best_bets: list[dict] | None = None,
    sharp_spots: list[dict] | None = None,
) -> dict[str, Any]:
    """Build the export payload. `rows` is (forecast, kickoff) in any order.

    ``player_projections`` is ``player_props.build_slate``'s (rows, status).
    """
    auth = authority or auth_mod.current()
    stamp = (generated_at or datetime.now(timezone.utc)).replace(microsecond=0)
    # Chronological, matching the dashboard. Unscheduled games sort last so a
    # feed gap never silently leads the board.
    ordered = sorted(
        rows,
        key=lambda pair: (
            (1, 0.0) if pair[1] is None else (0, pair[1].timestamp()),
            pair[0].away,
            pair[0].home,
        ),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "sport": "cfb",
        "season": season,
        "week": week,
        "generated_at": stamp.isoformat().replace("+00:00", "Z"),
        "order": "kickoff_asc",
        "authority": {
            "level": auth.level.value,
            "may_bet": auth.may_bet,
            "evidence": auth.evidence,
            "unmet_gates": list(auth.unmet_gates),
        },
        "regime": {
            "first_validated_week": fc.FIRST_VALIDATED_WEEK,
            "in_validated_regime": week >= fc.FIRST_VALIDATED_WEEK,
        },
        "lam": fc.DEFAULT_LAM,
        "simulations": simulate.DEFAULT_SIMULATIONS,
        "total_model_weights": {
            "by_week": fc.TOTAL_MODEL_WEIGHT_BY_WEEK,
            "mature": fc.MATURE_TOTAL_MODEL_WEIGHT,
        },
        "games": [_game(season, forecast, kickoff,
                        (player_projections or ([], {}))[1].get("teams"))
                  for forecast, kickoff in ordered],
        "best_bets": best_bets or [],
        "sharp_spots": sharp_spots or [],
        "player_projections": (player_projections or ([], {}))[0],
        "player_projections_status": (player_projections or ([], {}))[1],
    }



def player_projections(season: int, week: int, rows: list,
                       reports: dict | None = None) -> tuple[list[dict], dict]:
    """The player layer, fail-soft: a feed or code failure publishes an empty
    list with the reason, never a missing board."""
    from . import player_props

    try:
        return player_props.build_slate(season, week, rows, reports)
    except Exception as exc:  # the game board must still ship
        return [], {"model_version": player_props.MODEL_VERSION, "games": 0, "players": 0,
                    "issues": [f"player projections failed: {type(exc).__name__}: {exc}"]}


def write(payload_dict: dict[str, Any], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload_dict, indent=2) + "\n", encoding="utf-8")
    return out


# -- public research slate ---------------------------------------------------
# chase-analytics.com fetches this, not board.json. Board carries margins,
# prices, and may_bet; this file is observed identity, venue, and opponent-
# adjusted efficiency rates ranked against the FBS pool that published them.
# A rank always sits beside the value it was computed from.

PUBLIC_SLATE_SCHEMA = "chase-public-slate/1"

_PUBLIC_FORM_FIELDS = (
    ("off_successRate", "Offense success rate", "high", "pct"),
    ("off_explosiveness", "Offense explosiveness", "high", "num"),
    ("off_ppa", "Offense PPA per play", "high", "ppa"),
    ("off_stuffRate", "Offense stuffed rate", "low", "pct"),
    ("def_successRate", "Success rate allowed", "low", "pct"),
    ("def_explosiveness", "Explosiveness allowed", "low", "num"),
    ("def_ppa", "PPA allowed", "low", "ppa"),
    ("def_stuffRate", "Stuff rate generated", "high", "pct"),
)

_PUBLIC_FORBIDDEN = {
    "model_margin", "market_margin", "market_gap", "published_margin",
    "edge_points", "edge_withheld_reason", "win_probability",
    "projected_away_score", "projected_home_score", "projected_total",
    "may_bet", "authority", "book", "action", "rating", "efficiency_margin",
    "raw_model_margin", "lean", "pick",
}


def _rank(pool: list[float], value: float, better: str) -> int:
    if better == "high":
        return 1 + sum(1 for other in pool if other > value + 1e-12)
    return 1 + sum(1 for other in pool if other < value - 1e-12)


def _form_rates(form: Any, pools: dict[str, list[float]]) -> dict[str, Any] | None:
    if form is None:
        return None
    rates: dict[str, Any] = {}
    for field, label, better, fmt in _PUBLIC_FORM_FIELDS:
        value = getattr(form, field, None)
        pool = pools.get(field) or []
        if value is None or not pool:
            continue
        number = float(value)
        rates[field] = {
            "label": label,
            "value": round(number, 4),
            "better": better,
            "rank": _rank(pool, number, better),
            "of": len(pool),
            "format": fmt,
        }
    if not rates:
        return None
    blob: dict[str, Any] = {"rates": rates}
    plays = getattr(form, "plays", None)
    if plays is not None:
        blob["plays"] = round(float(plays), 1)
    drives = getattr(form, "drives", None)
    if drives is not None:
        blob["drives"] = round(float(drives), 1)
    return blob


def _records(season_games: list[dict[str, Any]]) -> dict[str, str]:
    wins: dict[str, int] = {}
    losses: dict[str, int] = {}
    for game in season_games:
        if not game.get("completed"):
            continue
        home, away = game.get("homeTeam"), game.get("awayTeam")
        hp, ap = game.get("homePoints"), game.get("awayPoints")
        if home is None or away is None or hp is None or ap is None:
            continue
        if float(hp) > float(ap):
            wins[home] = wins.get(home, 0) + 1
            losses[away] = losses.get(away, 0) + 1
        elif float(ap) > float(hp):
            wins[away] = wins.get(away, 0) + 1
            losses[home] = losses.get(home, 0) + 1
    names = set(wins) | set(losses)
    return {
        name: f"{wins.get(name, 0)}-{losses.get(name, 0)}"
        for name in names
    }


def _recent_results(season_games: list[dict[str, Any]], team: str,
                    n: int = 5) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for game in season_games:
        if not game.get("completed"):
            continue
        home, away = game.get("homeTeam"), game.get("awayTeam")
        hp, ap = game.get("homePoints"), game.get("awayPoints")
        if home is None or away is None or hp is None or ap is None:
            continue
        if team not in (home, away):
            continue
        is_home = team == home
        scored = float(hp if is_home else ap)
        allowed = float(ap if is_home else hp)
        start = str(game.get("startDate") or game.get("start_date") or "")
        rows.append({
            "date": start[:10],
            "home": is_home,
            "opponent": away if is_home else home,
            "scored": int(scored) if scored == int(scored) else scored,
            "allowed": int(allowed) if allowed == int(allowed) else allowed,
            "won": scored > allowed,
        })
    rows.sort(key=lambda row: row.get("date") or "")
    return rows[-n:]


def _travel_bits(venue_ctx: Any, home: str, away: str, *,
                 venue_id: int | None, neutral: bool) -> dict[str, Any]:
    if venue_ctx is None or not hasattr(venue_ctx, "stadium"):
        return {}
    from cfbmodel import venue as venue_mod
    home_venue = venue_ctx.stadium(home, venue_id if not neutral else None)
    away_venue = venue_ctx.stadium(away)
    out: dict[str, Any] = {}
    if home_venue and home_venue.name:
        out["venue"] = home_venue.name
        if home_venue.dome is True:
            out["roof"] = "Dome"
            out["surface"] = "Indoor"
        elif home_venue.dome is False:
            out["roof"] = "Outdoor"
    miles = venue_mod.distance_miles(home_venue, away_venue) if home_venue and away_venue else None
    if miles is not None:
        out["away_travel"] = f"{miles:.0f} miles"
        out["away_travel_km"] = round(miles * 1.60934, 1)
        out["home_travel"] = "Home"
        out["home_travel_km"] = 0
    shift = venue_mod.timezone_shift(home_venue, away_venue) if home_venue and away_venue else None
    if shift is not None:
        out["away_tz_shift"] = round(shift, 2)
        out["home_tz_shift"] = 0
    return out


def public_slate(
    *,
    season: int,
    week: int,
    slate_games: list[dict[str, Any]],
    forecasts: list[tuple[fc.Forecast, datetime | None]],
    forms: dict[str, Any],
    season_games: list[dict[str, Any]] | None = None,
    venue_ctx: Any = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Allowlisted public matchup payload. Never carries a priced or forecast field."""
    stamp = (generated_at or datetime.now(timezone.utc)).replace(microsecond=0)
    pools: dict[str, list[float]] = {field: [] for field, *_ in _PUBLIC_FORM_FIELDS}
    for form in forms.values():
        for field, *_ in _PUBLIC_FORM_FIELDS:
            value = getattr(form, field, None)
            if value is not None:
                pools[field].append(float(value))
    records = _records(season_games or slate_games)
    by_matchup = {(f.away, f.home): (f, kickoff) for f, kickoff in forecasts}
    games: list[dict[str, Any]] = []
    for raw in slate_games:
        away, home = raw.get("awayTeam"), raw.get("homeTeam")
        if not away or not home:
            continue
        pair = by_matchup.get((away, home))
        kickoff = pair[1] if pair else None
        forecast = pair[0] if pair else None
        away_meta = teams.get(season, away)
        home_meta = teams.get(season, home)
        venue_id = None
        try:
            venue_id = int(raw["venueId"]) if raw.get("venueId") is not None else None
        except (TypeError, ValueError):
            venue_id = None
        neutral = bool(raw.get("neutralSite") if forecast is None else forecast.neutral)
        completed = bool(raw.get("completed"))
        state = "final" if completed and raw.get("homePoints") is not None else "scheduled"
        kickoff_iso = (
            kickoff.isoformat().replace("+00:00", "Z") if kickoff else None
        )
        row: dict[str, Any] = {
            "id": f"{away_meta.short}@{home_meta.short}",
            "sport": "cfb",
            "game_state": state,
            "kickoff_utc": kickoff_iso,
            "away": away_meta.short,
            "home": home_meta.short,
            "away_name": away,
            "home_name": home,
            "away_conference": away_meta.conference,
            "home_conference": home_meta.conference,
            "away_logo": away_meta.logo,
            "home_logo": home_meta.logo,
            "away_record": records.get(away),
            "home_record": records.get(home),
            "away_score": raw.get("awayPoints") if completed else None,
            "home_score": raw.get("homePoints") if completed else None,
            "venue_id": venue_id,
            "neutral": True if neutral else None,
            "away_form": _form_rates(forms.get(away), pools),
            "home_form": _form_rates(forms.get(home), pools),
            "away_recent": _recent_results(season_games or slate_games, away) or None,
            "home_recent": _recent_results(season_games or slate_games, home) or None,
        }
        row.update(_travel_bits(venue_ctx, home, away, venue_id=venue_id, neutral=neutral))
        games.append({key: value for key, value in row.items() if value is not None and value != ""})

    games.sort(key=lambda g: (g.get("kickoff_utc") or "9999", g.get("id") or ""))
    payload = {
        "schema": PUBLIC_SLATE_SCHEMA,
        "sport": "cfb",
        "season": season,
        "week": week,
        "generated_at_utc": stamp.isoformat().replace("+00:00", "Z"),
        "games": games,
    }
    leaked = _PUBLIC_FORBIDDEN & set(_walk_keys(payload))
    if leaked:
        raise RuntimeError("public CFB slate leaked private keys: " + ", ".join(sorted(leaked)))
    return payload


def _walk_keys(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            found.add(str(key))
            found |= _walk_keys(value)
    elif isinstance(obj, list):
        for value in obj:
            found |= _walk_keys(value)
    return found


# -- power ratings -----------------------------------------------------------
# The board answers "what happens Saturday"; the rating answers "how good is
# this team". They are different payloads and are versioned separately, because
# a ratings graphic has no kickoff, no market, and no per-game edge to gate.
RATINGS_SCHEMA_VERSION = "1.1.0"


def ratings_payload(
    *,
    season: int,
    ratings: dict[str, float],
    basis: str,
    week: int | None = None,
    games_rated: int = 0,
    top: int | None = None,
    authority: auth_mod.Authority | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Rank a rating map into an export the content engine can draw.

    `basis` is carried rather than inferred because the two ways of asking for a
    rating produce genuinely different numbers. `preseason` is the fitted prior
    (prior seasons + talent + returning production + recruiting) for a season
    that has not kicked off, and `season_to_date` is solved from games already
    played. A graphic that showed one while implying the other would be stating
    a confidence the model has not earned, so `games_rated` travels with it: a
    preseason board is honest only if it says zero games are behind it.
    """
    if basis not in ("preseason", "season_to_date"):
        raise ValueError(f"unknown ratings basis: {basis!r}")
    auth = authority or auth_mod.current()
    stamp = (generated_at or datetime.now(timezone.utc)).replace(microsecond=0)
    ranked = sorted(
        ((value, school) for school, value in ratings.items() if school != RATINGS_FCS),
        key=lambda pair: (-pair[0], pair[1]),
    )
    rated = len(ranked)
    # Measured over every rated FBS team, before the top-N cut. A downstream
    # grade has to be anchored to the league, and a board that shows the top 40
    # cannot work the league out from the forty teams on it.
    values = [value for value, _ in ranked]
    league = {
        "mean": round(statistics.fmean(values), 4) if values else 0.0,
        "sd": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
        "min": round(min(values), 2) if values else None,
        "max": round(max(values), 2) if values else None,
    }
    if top is not None:
        ranked = ranked[:top]
    return {
        "schema_version": RATINGS_SCHEMA_VERSION,
        "sport": "cfb",
        "kind": "power_ratings",
        "season": season,
        "week": week,
        "basis": basis,
        "generated_at": stamp.isoformat().replace("+00:00", "Z"),
        "order": "rating_desc",
        # Stated on the payload so the graphic never has to describe the unit
        # from memory: a rating is points, not a score and not a ranking index.
        "scale": "points vs an average FBS team on a neutral field",
        "home_field_points": ratings_mod.HOME_FIELD_POINTS,
        "blowout_cap": ratings_mod.BLOWOUT_CAP,
        "team_count": rated,
        "games_rated": games_rated,
        "league": league,
        "authority": {
            "level": auth.level.value,
            "may_bet": auth.may_bet,
            "evidence": auth.evidence,
            "unmet_gates": list(auth.unmet_gates),
        },
        "teams": [
            dict(_team(season, school), rank=rank, rating=round(value, 2))
            for rank, (value, school) in enumerate(ranked, 1)
        ],
    }
