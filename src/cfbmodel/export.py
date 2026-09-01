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
from cfbmodel import teams
from cfbmodel.ratings import FCS as RATINGS_FCS

SCHEMA_VERSION = "2.0.0"


def _team(season: int, school: str) -> dict[str, Any]:
    team = teams.get(season, school)
    return {
        "school": school,
        "abbreviation": team.abbreviation,
        "conference": team.conference,
        "color": team.color,
        "logo": team.logo,
    }


def _game(season: int, forecast: fc.Forecast, kickoff: datetime | None) -> dict[str, Any]:
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
        "edge_points": forecast.edge_points,
        # Always present when both numbers exist. Outside the validated regime
        # this is the information gap, not a disagreement -- `edge_points` is
        # None there and `edge_withheld_reason` says why.
        "market_gap": forecast.market_gap,
        "edge_withheld_reason": forecast.edge_withheld_reason,
        "win_probability": forecast.win_probability,
        "projected_total": forecast.projected_total,
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
    }


def payload(
    *,
    season: int,
    week: int,
    rows: list[tuple[fc.Forecast, datetime | None]],
    authority: auth_mod.Authority | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the export payload. `rows` is (forecast, kickoff) in any order."""
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
        "games": [_game(season, forecast, kickoff) for forecast, kickoff in ordered],
    }


def write(payload_dict: dict[str, Any], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload_dict, indent=2) + "\n", encoding="utf-8")
    return out


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
