"""Grade saved CFB forecasts from public final scores; never add forecasts."""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cfbmodel import ledger, results_view
from cfbmodel.sources import espn_box, oddsapi

PUBLISHED = "https://alphakiller1.github.io/cfb-model/ledger.json.gz"


def recover(path: Path, *, url: str = PUBLISHED) -> dict:
    local = ledger._load(path)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "chase-cfb-results/2"})
        with urllib.request.urlopen(request, timeout=25) as response:
            published = json.loads(gzip.decompress(response.read()))
        if not isinstance(published.get("snapshots"), list):
            raise ValueError("Invalid published ledger")
        if (ledger._parse(published.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc)) > (
                ledger._parse(local.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc)):
            ledger._write(path, published)
            return published
    except (OSError, ValueError) as exc:
        print(f"Published ledger recovery unavailable: {type(exc).__name__}")
    return local


def final_inputs(history: dict, season: int) -> tuple[list, list]:
    pending = [r for key in ("snapshots", "player_snapshots", "best_bets", "sharp_spots")
               for r in history.get(key, []) if r.get("season") == season and r.get("status") == "pending"
               and ledger._parse(r.get("kickoff")) and ledger._parse(r.get("kickoff")) < ledger._now()]
    schools = {name: None for r in history.get("snapshots", []) for name in (r.get("home"), r.get("away")) if name}
    index = oddsapi.build_index(schools)
    results, boxes = [], []
    for week in sorted({r["week"] for r in pending}):
        for event in espn_box.week_events(season, week):
            box = espn_box.game_box(event, season, week)
            if not box or not box.completed:
                continue
            boxes.append(box)
            home = next((t for t in box.teams if t.home), None)
            away = next((t for t in box.teams if not t.home), None)
            if home and away and home.points is not None and away.points is not None:
                results.append({"week": week, "completed": True,
                                "homeTeam": oddsapi.match_team(home.name, index),
                                "awayTeam": oddsapi.match_team(away.name, index),
                                "homePoints": home.points, "awayPoints": away.points})
    return results, boxes


def publish(history: dict, out: Path, season: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    results_view.publish(history, out, season=season)
    (out / "record.json").write_text(json.dumps(history["summary"], indent=2), encoding="utf-8")
    (out / "plays.json").write_text(json.dumps(ledger.plays(history, season=season)), encoding="utf-8")
    (out / "ledger.json.gz").write_bytes(gzip.compress(json.dumps(history).encode(), mtime=0))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path)
    p.add_argument("--ledger", type=Path, default=ledger.DEFAULT_PATH)
    p.add_argument("--recover-only", action="store_true")
    args = p.parse_args()
    history = recover(args.ledger)
    if args.recover_only:
        return
    if not args.out:
        p.error("--out is required for collection")
    board = json.loads((args.out / "board.json").read_text(encoding="utf-8"))
    season, week = int(board["season"]), int(board["week"])
    results, boxes = final_inputs(history, season)
    history = ledger.update(season=season, week=week, forecasts=[], season_games=results,
                            player_boxes=boxes, path=args.ledger)
    publish(history, args.out, season)
    print("Published saved grades:", history["summary"].get("games_graded"), "games;",
          history["summary"].get("players", {}).get("players_graded"), "players")


if __name__ == "__main__":
    main()
