"""Build the dashboard for whatever week it currently is.

Season and week can be pinned with IN_SEASON / IN_WEEK (the deploy workflow wires
those to its dispatch inputs); otherwise both are inferred from the clock.

    python scripts/build_current.py --out _site/index.html
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cfbmodel import site  # noqa: E402

# CFB week 1 opens in the last week of August. Weeks are capped at 15 so a
# post-season run does not ask CFBD for a week that does not exist.
SEASON_START_MONTH, SEASON_START_DAY = 8, 24
MAX_WEEK = 15


def current_season(now: dt.datetime) -> int:
    """A season is labelled by the calendar year it starts in."""
    return now.year if now.month >= 7 else now.year - 1


def current_week(now: dt.datetime, season: int) -> int:
    start = dt.datetime(season, SEASON_START_MONTH, SEASON_START_DAY, tzinfo=dt.timezone.utc)
    return max(1, min(MAX_WEEK, ((now - start).days // 7) + 1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_site/index.html")
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    season = args.season or int(os.environ.get("IN_SEASON") or 0) or current_season(now)
    week = args.week or int(os.environ.get("IN_WEEK") or 0) or current_week(now, season)

    print(f"building season={season} week={week}")
    path = site.build(season=season, week=week, out=Path(args.out))
    print(f"wrote {path} ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
