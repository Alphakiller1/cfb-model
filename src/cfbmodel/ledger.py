"""Timestamped CFB forecast ledger and deterministic grader.

Every production build records the exact model, consensus, and sportsbook
numbers it displayed. Completed games are graded on the next build. The record
is explicitly a shadow record: authority remains RESEARCH_ONLY, and a stored
disagreement is not retroactively promoted into a bet.
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cfbmodel.forecast import Forecast
    from cfbmodel.sources.espn_box import GameBox


SCHEMA_VERSION = "2.3.0"

# Game snapshots are one row per game per sportsbook quote -- roughly 700 a week
# across FBS. The old 5,000-row cap began deleting the season's earliest graded
# games around week 8; this keeps three full seasons of vintages.
MAX_SNAPSHOTS = 60_000
# Player rows keep only the latest pre-kickoff projection per player-game, so
# the volume is bounded by the slate, not by how often the board rebuilds.
PLAYER_SEASONS_KEPT = 2
DEFAULT_PATH = Path(
    os.getenv(
        "CFB_LEDGER_PATH",
        str(Path(__file__).resolve().parents[2] / "data" / "runtime-cache"
            / "prediction-ledger.json"),
    )
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime | None = None) -> str:
    return (moment or _now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> dict:
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "snapshots": [], "player_snapshots": [],
                "best_bets": [], "sharp_spots": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload.get("snapshots"), list):
            payload.setdefault("player_snapshots", [])
            payload.setdefault("best_bets", [])
            payload.setdefault("sharp_spots", [])
            return payload
    except (json.JSONDecodeError, AttributeError):
        pass
    return {"schema_version": SCHEMA_VERSION, "snapshots": [], "player_snapshots": [],
            "best_bets": [], "sharp_spots": []}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _availability_snapshot(info: dict | None) -> dict | None:
    if not info:
        return None
    report = info.get("report") or {}
    listed = report.get("designations") or []
    return {
        "reported": bool(report),
        "report": report.get("report"),
        "published": report.get("published"),
        "starting_qb": info.get("starting_qb"),
        "starting_qb_status": info.get("starting_qb_status"),
        "listed": {status: sum(d.get("status") == status for d in listed)
                   for status in sorted({d.get("status") for d in listed})},
        "listed_players": [[d.get("position"), d.get("player"), d.get("status")]
                           for d in listed],
    }


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _result_index(games: list[dict]) -> dict[tuple[int, str, str], dict]:
    out = {}
    for game in games:
        if not game.get("completed"):
            continue
        home, away = game.get("homeTeam"), game.get("awayTeam")
        hp, ap = game.get("homePoints"), game.get("awayPoints")
        if home and away and hp is not None and ap is not None:
            out[(int(game.get("week") or 0), home, away)] = game
    return out


def _grade(snapshot: dict, result: dict) -> None:
    actual_margin = float(result["homePoints"] - result["awayPoints"])
    actual_total = float(result["homePoints"] + result["awayPoints"])
    snapshot.update({
        "status": "graded",
        "graded_at": _stamp(),
        "actual_margin": actual_margin,
        "actual_total": actual_total,
        "model_abs_error": (abs(float(snapshot["model_margin"]) - actual_margin)
                            if snapshot.get("model_margin") is not None else None),
        "forecast_abs_error": (abs(float(snapshot["forecast_margin"]) - actual_margin)
                               if snapshot.get("forecast_margin") is not None else None),
        "consensus_abs_error": (abs(float(snapshot["consensus_margin"]) - actual_margin)
                                if snapshot.get("consensus_margin") is not None else None),
        "book_abs_error": (abs(float(snapshot["book_margin"]) - actual_margin)
                           if snapshot.get("book_margin") is not None else None),
        "model_total_abs_error": (abs(float(snapshot["model_total"]) - actual_total)
                                  if snapshot.get("model_total") is not None else None),
        "forecast_total_abs_error": (
            abs(float(snapshot["forecast_total"]) - actual_total)
            if snapshot.get("forecast_total") is not None else None
        ),
        "book_total_abs_error": (abs(float(snapshot["book_total"]) - actual_total)
                                 if snapshot.get("book_total") is not None else None),
    })
    model, line = snapshot.get("model_margin"), snapshot.get("book_margin")
    if model is not None and line is not None and model != line:
        residual = actual_margin - float(line)
        snapshot["ats_result"] = (
            "push" if residual == 0 else
            "win" if residual * (float(model) - float(line)) > 0 else "loss"
        )
    model_total, book_total = snapshot.get("model_total"), snapshot.get("book_total")
    if model_total is not None and book_total is not None and model_total != book_total:
        residual = actual_total - float(book_total)
        snapshot["total_result"] = (
            "push" if residual == 0 else
            "win" if residual * (float(model_total) - float(book_total)) > 0 else "loss"
        )


def _published_boxes(boxes: list["GameBox"]) -> dict[str, "GameBox"]:
    """Event id -> completed box score that actually carries player lines."""
    return {box.event_id: box for box in boxes if box.completed and box.players}


def _grade_player(snapshot: dict, box: "GameBox") -> None:
    line = next((p for p in box.players
                 if p.athlete_id == snapshot["player_id"]
                 and p.team_id == snapshot["team_id"]), None)
    projected = snapshot.get("metrics") or {}
    # The box score is published, so a player absent from it did not record a
    # stat: that is a zero, not a missing observation.
    actual = {key: float(getattr(line, key, 0.0) or 0.0) if line else 0.0
              for key in projected}
    snapshot.update({
        "status": "graded",
        "graded_at": _stamp(),
        "played": line is not None,
        "actual_metrics": actual,
        "absolute_errors": {key: round(abs(float(value) - actual[key]), 3)
                            for key, value in projected.items()},
    })
    if snapshot.get("anytime_td") is not None:
        scored = float(bool(line and (line.rush_td + line.rec_td) > 0))
        snapshot["actual_anytime_td"] = scored
        snapshot["anytime_td_brier"] = round((float(snapshot["anytime_td"]) - scored) ** 2, 4)


def _units(result: str, price: int | None) -> float:
    if result == "win":
        price = price or -110
        return round(price / 100.0 if price > 0 else 100.0 / -price, 3)
    return -1.0 if result == "loss" else 0.0


def _grade_bet(bet: dict, results: dict, boxes: dict) -> None:
    """Grade one best bet in place when its game (or box score) is final."""
    family, line = bet["family"], float(bet["line"])
    if family in ("spread", "total"):
        result = results.get((bet["week"], bet["home"], bet["away"]))
        if result is None:
            return
        margin = float(result["homePoints"] - result["awayPoints"])
        total = float(result["homePoints"] + result["awayPoints"])
        if family == "spread":
            value = (margin if bet["side"] == "home" else -margin) + line
        else:
            value = (total - line) if bet["side"] == "over" else (line - total)
        bet["actual"] = margin if family == "spread" else total
    else:
        box = boxes.get(bet.get("event_id") or "")
        if box is None:
            return
        player = next((p for p in box.players if p.athlete_id == bet.get("player_id")
                       and p.team_id == bet.get("team_id")), None)
        if player is None:
            # Books void a prop when the player does not play.
            bet.update({"status": "graded", "graded_at": _stamp(), "result": "void",
                        "units": 0.0, "actual": None})
            return
        actual = float(getattr(player, bet["stat"], 0.0) or 0.0)
        value = (actual - line) if bet["side"] == "over" else (line - actual)
        bet["actual"] = actual
    outcome = "push" if abs(value) < 1e-9 else "win" if value > 0 else "loss"
    bet.update({"status": "graded", "graded_at": _stamp(), "result": outcome,
                "units": _units(outcome, bet.get("price"))})


def _record_list(payload: dict, key: str, items: list[dict] | None, now: datetime) -> None:
    """Log each pick the first time it is published, and lock it there.

    A reader acts on a pick when it appears, at the number it shows then. So the
    first published version - its side, line and price - is the one graded: a
    later build neither rewrites it at a moved line nor withdraws it when it
    drops off the list. Closing-line value then measures what that timing was
    worth.
    """
    if items is None:
        return
    known = {item["pick_id"] for item in payload[key]}
    for item in items:
        kickoff = _parse(item.get("kickoff"))
        if kickoff is None or kickoff <= now or item["pick_id"] in known:
            continue
        payload[key].append({**item, "recorded_at": _stamp(now), "status": "pending",
                             "authority": "shadow_only"})
        known.add(item["pick_id"])


def _closing_lines(snapshots: list[dict]) -> dict[tuple, dict]:
    """(season, week, home, away) -> the last quote logged before kickoff."""
    out: dict[tuple, dict] = {}
    for row in snapshots:
        key = (row.get("season"), row.get("week"), row.get("home"), row.get("away"))
        if key not in out or row.get("recorded_at", "") > out[key].get("recorded_at", ""):
            out[key] = row
    return out


def _clv(spot: dict, close: dict | None) -> float | None:
    """Points of closing-line value: how much better the logged number was than
    the last pre-kickoff number, from the spot's side."""
    if not close:
        return None
    line = float(spot["line"])
    if spot["family"] == "spread":
        if close.get("book_margin") is None:
            return None
        closing = -close["book_margin"] if spot["side"] == "home" else close["book_margin"]
        return round(line - closing, 2)
    if close.get("book_total") is None:
        return None
    closing = float(close["book_total"])
    return round((closing - line) if spot["side"] == "over" else (line - closing), 2)


def _sharp_summary(payload: dict, season: int | None) -> dict:
    out: dict[str, dict] = {}
    for spot in payload.get("sharp_spots", []):
        if season is not None and spot.get("season") != season:
            continue
        family = out.setdefault(spot["family"], {"win": 0, "loss": 0, "push": 0, "void": 0,
                                                 "pending": 0, "units": 0.0, "clv": []})
        if spot.get("status") != "graded":
            family["pending"] += 1
            continue
        family[spot["result"]] += 1
        family["units"] = round(family["units"] + float(spot.get("units") or 0.0), 3)
        if spot.get("clv") is not None:
            family["clv"].append(float(spot["clv"]))
    for family in out.values():
        values = family.pop("clv")
        family["mean_clv"] = round(statistics.fmean(values), 2) if values else None
        family["clv_positive_rate"] = (round(sum(v > 0 for v in values) / len(values), 3)
                                       if values else None)
    return out


def _best_bet_summary(payload: dict, season: int | None) -> dict:
    out: dict[str, dict] = {}
    for bet in payload.get("best_bets", []):
        if season is not None and bet.get("season") != season:
            continue
        family = out.setdefault(bet["family"], {"win": 0, "loss": 0, "push": 0, "void": 0,
                                                "pending": 0, "units": 0.0})
        if bet.get("status") != "graded":
            family["pending"] += 1
            continue
        family[bet["result"]] += 1
        family["units"] = round(family["units"] + float(bet.get("units") or 0.0), 3)
        if bet.get("clv") is not None:
            family.setdefault("clv", []).append(float(bet["clv"]))
    for family in out.values():
        values = family.pop("clv", [])
        family["mean_clv"] = round(statistics.fmean(values), 2) if values else None
    return out


def _player_summary(payload: dict, season: int | None) -> dict:
    rows = [row for row in payload.get("player_snapshots", [])
            if row.get("status") == "graded"
            and (season is None or row.get("season") == season)]
    errors: dict[str, list[float]] = {}
    for row in rows:
        for metric, value in (row.get("absolute_errors") or {}).items():
            errors.setdefault(metric, []).append(float(value))
    return {
        "scope": "latest pre-kickoff projection per player-game",
        "authority": "shadow_only",
        "players_graded": len(rows),
        "pending_player_snapshots": sum(row.get("status") == "pending"
                                        for row in payload.get("player_snapshots", [])),
        "mae": {metric: round(statistics.fmean(values), 3)
                for metric, values in sorted(errors.items()) if values},
        "anytime_td_brier": _mean(rows, "anytime_td_brier"),
    }


_PLAY_FIELDS = (
    "season", "week", "away", "home", "kickoff", "recorded_at", "model_regime", "book",
    "book_margin", "book_total", "model_margin", "model_total", "status",
    "actual_margin", "actual_total", "ats_result", "total_result", "graded_at", "authority",
    "edge_points", "edge_withheld_reason", "availability",
)


def plays(payload: dict, *, season: int) -> list[dict]:
    """One row per game: the last pre-kickoff snapshot, graded once final.

    This is the readable log of what the board showed and how it came out; the
    full ledger keeps every quote vintage.
    """
    latest: dict[tuple, dict] = {}
    for row in payload.get("snapshots", []):
        if row.get("season") != season:
            continue
        key = (row["season"], row["week"], row["home"], row["away"])
        if key not in latest or row.get("recorded_at", "") > latest[key].get("recorded_at", ""):
            latest[key] = row
    out = []
    for row in latest.values():
        play = {field: row.get(field) for field in _PLAY_FIELDS}
        model, line = row.get("model_margin"), row.get("book_margin")
        if model is not None and line is not None and model != line:
            play["ats_side"] = row["home"] if float(model) > float(line) else row["away"]
        model_total, book_total = row.get("model_total"), row.get("book_total")
        if model_total is not None and book_total is not None and model_total != book_total:
            play["total_side"] = "over" if float(model_total) > float(book_total) else "under"
        out.append(play)
    return sorted(out, key=lambda play: (play["week"], play.get("kickoff") or "",
                                         play["away"]))


def _mean(rows: list[dict], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return round(statistics.fmean(values), 3) if values else None


def summary(payload: dict, *, season: int | None = None) -> dict:
    """Score the latest pre-kickoff snapshot for each game, avoiding duplicates."""
    graded = [row for row in payload.get("snapshots", [])
              if row.get("status") == "graded"
              and (season is None or row.get("season") == season)]
    latest: dict[tuple[int, int, str, str], dict] = {}
    for row in graded:
        key = (row["season"], row["week"], row["home"], row["away"])
        if key not in latest or row.get("recorded_at", "") > latest[key].get("recorded_at", ""):
            latest[key] = row
    rows = list(latest.values())
    ats = [row.get("ats_result") for row in rows if row.get("ats_result")]
    totals = [row.get("total_result") for row in rows if row.get("total_result")]
    return {
        "scope": "latest pre-kickoff snapshot per game",
        "authority": "shadow_only",
        "games_graded": len(rows),
        "pending_snapshots": sum(row.get("status") == "pending"
                                 for row in payload.get("snapshots", [])),
        "model_mae": _mean(rows, "model_abs_error"),
        "forecast_mae": _mean(rows, "forecast_abs_error"),
        "consensus_mae": _mean(rows, "consensus_abs_error"),
        "book_mae": _mean(rows, "book_abs_error"),
        "ats": {name: ats.count(name) for name in ("win", "loss", "push")},
        "model_total_mae": _mean(rows, "model_total_abs_error"),
        "forecast_total_mae": _mean(rows, "forecast_total_abs_error"),
        "book_total_mae": _mean(rows, "book_total_abs_error"),
        "totals": {name: totals.count(name) for name in ("win", "loss", "push")},
        "players": _player_summary(payload, season),
        "best_bets": _best_bet_summary(payload, season),
        "sharp_spots": _sharp_summary(payload, season),
    }


def update(
    *,
    season: int,
    week: int,
    forecasts: list[tuple["Forecast", datetime | None]],
    season_games: list[dict],
    player_projections: list[dict] | None = None,
    player_boxes: list["GameBox"] | None = None,
    availability: dict | None = None,
    best_bets: list[dict] | None = None,
    sharp_spots: list[dict] | None = None,
    path: Path = DEFAULT_PATH,
    recorded_at: datetime | None = None,
) -> dict:
    """Grade pending snapshots, append unseen live quotes, and return the record."""
    payload = _load(path)
    results = _result_index(season_games)
    for snapshot in payload["snapshots"]:
        if snapshot.get("status") != "pending":
            continue
        result = results.get((snapshot["week"], snapshot["home"], snapshot["away"]))
        if result is not None:
            _grade(snapshot, result)

    boxes = _published_boxes(player_boxes or [])
    for snapshot in payload["player_snapshots"]:
        if snapshot.get("status") != "pending":
            continue
        box = boxes.get(snapshot.get("event_id") or "")
        if box is not None:
            _grade_player(snapshot, box)

    closing = _closing_lines(payload["snapshots"])
    for bet in payload["best_bets"]:
        if bet.get("status") == "pending":
            _grade_bet(bet, results, boxes)
            if bet.get("status") == "graded" and bet["family"] in ("spread", "total"):
                bet["clv"] = _clv(bet, closing.get(
                    (bet["season"], bet["week"], bet["home"], bet["away"])))
    for spot in payload["sharp_spots"]:
        if spot.get("status") == "pending":
            _grade_bet(spot, results, boxes)
            if spot.get("status") == "graded":
                spot["clv"] = _clv(spot, closing.get(
                    (spot["season"], spot["week"], spot["home"], spot["away"])))

    now = recorded_at or _now()
    known = {row.get("snapshot_id") for row in payload["snapshots"]}
    for forecast, kickoff in forecasts:
        # Never create a quote after kickoff; that would turn a tracking ledger
        # into hindsight. Missing kickoff is also withheld because timing cannot
        # be proved.
        if kickoff is None or kickoff <= now or not forecast.book_name:
            continue
        quote_key = forecast.book_last_update or _stamp(now)
        snapshot_id = "|".join((
            str(season), str(week), forecast.away, forecast.home,
            str(forecast.book_name), quote_key,
        ))
        if snapshot_id in known:
            continue
        payload["snapshots"].append({
            "snapshot_id": snapshot_id,
            "recorded_at": _stamp(now),
            "season": season,
            "week": week,
            "home": forecast.home,
            "away": forecast.away,
            "kickoff": _stamp(kickoff),
            "model_lineage": "2026.09-independent-venue-fcs",
            "model_regime": forecast.model_regime,
            "model_margin": forecast.model_margin,
            "forecast_margin": forecast.margin,
            "forecast_source": forecast.forecast_source,
            "consensus_margin": forecast.market_margin,
            "book": forecast.book_name,
            "book_margin": forecast.book_margin,
            "book_total": forecast.book_total,
            "book_last_update": forecast.book_last_update,
            "model_total": forecast.independent_total,
            "forecast_total": forecast.projected_total,
            "total_model_weight": forecast.total_model_weight,
            "edge_points": forecast.edge_points,
            "edge_withheld_reason": forecast.edge_withheld_reason,
            # What the conference reports said when the quote was logged. This is
            # the archive a college QB-availability coefficient will be fitted on.
            "availability": {
                side: _availability_snapshot((availability or {}).get(school))
                for side, school in (("home", forecast.home), ("away", forecast.away))
            },
            "status": "pending",
            "authority": "shadow_only",
        })
        known.add(snapshot_id)

    # Players: the latest pre-kickoff projection replaces an earlier pending
    # one for the same player-game; a graded row is never touched again.
    player_index = {row["snapshot_id"]: i for i, row in enumerate(payload["player_snapshots"])}
    for projection in player_projections or []:
        kickoff = _parse(projection.get("kickoff"))
        event_id = str(projection.get("event_id") or "")
        team_id = str(projection.get("team_id") or "")
        player_id = str(projection.get("player_id") or "")
        if kickoff is None or kickoff <= now or not (event_id and team_id and player_id):
            continue
        snapshot_id = "|".join((str(season), event_id, team_id, player_id))
        existing = player_index.get(snapshot_id)
        if (existing is not None
                and payload["player_snapshots"][existing].get("status") != "pending"):
            continue
        row = {
            "snapshot_id": snapshot_id,
            "recorded_at": _stamp(now),
            "season": season,
            "week": week,
            "event_id": event_id,
            "kickoff": _stamp(kickoff),
            "game_key": projection.get("game_key"),
            "team": projection.get("team"),
            "team_id": team_id,
            "opponent": projection.get("opponent"),
            "player_id": player_id,
            "player_name": projection.get("player_name"),
            "position": projection.get("position"),
            "model_version": projection.get("model_version"),
            "metrics": {key: dist.get("mean") for key, dist in
                        (projection.get("stats") or {}).items()
                        if isinstance(dist, dict) and dist.get("mean") is not None},
            "anytime_td": projection.get("anytime_td"),
            "status": "pending",
            "authority": "shadow_only",
        }
        if existing is None:
            player_index[snapshot_id] = len(payload["player_snapshots"])
            payload["player_snapshots"].append(row)
        else:
            payload["player_snapshots"][existing] = row

    # Best bets and sharp spots: the last list published before kickoff is the
    # one graded. A pick dropped from a later pre-kickoff build is withdrawn.
    _record_list(payload, "best_bets", best_bets, now)
    _record_list(payload, "sharp_spots", sharp_spots, now)

    payload["schema_version"] = SCHEMA_VERSION
    payload["updated_at"] = _stamp(now)
    # Bound an accidental runaway while retaining three full seasons.
    payload["snapshots"] = [row for row in payload["snapshots"]
                            if int(row.get("season", season)) >= season - 2][-MAX_SNAPSHOTS:]
    payload["player_snapshots"] = [
        row for row in payload["player_snapshots"]
        if int(row.get("season", season)) > season - PLAYER_SEASONS_KEPT
    ]
    payload["summary"] = summary(payload, season=season)
    _write(path, payload)
    return payload
