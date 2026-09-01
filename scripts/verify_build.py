"""Fail a deployment whose data manifest does not prove a usable current slate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def verify(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for key in ("generated_at", "season", "week", "cfbd", "odds"):
        if key not in payload:
            errors.append(f"missing manifest field: {key}")

    odds = payload.get("odds") or {}
    slate = int(odds.get("slate_games") or 0)
    matched = int(odds.get("slate_matched") or 0)
    if slate <= 0:
        errors.append("current week contains no FBS-vs-FBS games")
    if slate and matched <= 0:
        errors.append("no configured-sportsbook lines matched the slate")
    minimum = float(os.getenv("CFB_MIN_BOOK_COVERAGE", "0.50"))
    if slate and matched / slate < minimum:
        errors.append(
            f"sportsbook coverage {matched}/{slate} is below {minimum:.0%}"
        )
    requested = str(odds.get("requested_book") or "").lower()
    if requested != "draftkings":
        errors.append(f"configured sportsbook is {requested or 'missing'}, expected draftkings")
    if odds.get("state") not in {"fresh", "cached"}:
        errors.append(f"sportsbook source state is {odds.get('state')!r}")

    if os.getenv("CFB_REQUIRE_FRESH_CFBD", "1").lower() in {"1", "true", "yes"}:
        stale = [row["path"] for row in payload.get("cfbd", []) if row.get("stale")]
        if stale:
            errors.append(f"{len(stale)} CFBD endpoint(s) are stale")
        failed = [row["path"] for row in payload.get("cfbd", [])
                  if row.get("state") == "error"]
        if failed:
            errors.append(f"{len(failed)} CFBD endpoint(s) failed")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    errors = verify(args.manifest)
    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        return 1
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    odds = payload["odds"]
    print(
        f"verified {payload['season']} week {payload['week']}: "
        f"{odds['slate_matched']}/{odds['slate_games']} DraftKings lines, "
        f"{len(payload['cfbd'])} CFBD endpoints"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
