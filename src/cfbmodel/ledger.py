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


SCHEMA_VERSION = "1.0.0"
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
        return {"schema_version": SCHEMA_VERSION, "snapshots": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload.get("snapshots"), list):
            return payload
    except (json.JSONDecodeError, AttributeError):
        pass
    return {"schema_version": SCHEMA_VERSION, "snapshots": []}


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
        "consensus_abs_error": (abs(float(snapshot["consensus_margin"]) - actual_margin)
                                if snapshot.get("consensus_margin") is not None else None),
        "book_abs_error": (abs(float(snapshot["book_margin"]) - actual_margin)
                           if snapshot.get("book_margin") is not None else None),
        "model_total_abs_error": (abs(float(snapshot["model_total"]) - actual_total)
                                  if snapshot.get("model_total") is not None else None),
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
        "consensus_mae": _mean(rows, "consensus_abs_error"),
        "book_mae": _mean(rows, "book_abs_error"),
        "ats": {name: ats.count(name) for name in ("win", "loss", "push")},
        "model_total_mae": _mean(rows, "model_total_abs_error"),
        "book_total_mae": _mean(rows, "book_total_abs_error"),
        "totals": {name: totals.count(name) for name in ("win", "loss", "push")},
    }


def update(
    *,
    season: int,
    week: int,
    forecasts: list[tuple["Forecast", datetime | None]],
    season_games: list[dict],
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
            "model_lineage": "2026.09-reliability-blended",
            "model_regime": forecast.model_regime,
            "model_margin": forecast.model_margin,
            "consensus_margin": forecast.market_margin,
            "book": forecast.book_name,
            "book_margin": forecast.book_margin,
            "book_total": forecast.book_total,
            "book_last_update": forecast.book_last_update,
            "model_total": forecast.projected_total,
            "status": "pending",
            "authority": "shadow_only",
        })
        known.add(snapshot_id)

    payload["schema_version"] = SCHEMA_VERSION
    payload["updated_at"] = _stamp(now)
    # Bound an accidental runaway while retaining three full seasons.
    payload["snapshots"] = [row for row in payload["snapshots"]
                            if int(row.get("season", season)) >= season - 2][-5000:]
    payload["summary"] = summary(payload, season=season)
    _write(path, payload)
    return payload
