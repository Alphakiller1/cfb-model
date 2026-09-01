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
from cfbmodel.sources import cfbd  # noqa: E402

# Week 1 moves with the calendar. Anchor the fallback to Labor Day rather than
# a fixed August date. Weeks are capped at 15 so a postseason run does not ask
# CFBD for a nonexistent regular-season week.
MAX_WEEK = 15


def current_season(now: dt.datetime) -> int:
    """A season is labelled by the calendar year it starts in."""
    return now.year if now.month >= 7 else now.year - 1


def current_week(now: dt.datetime, season: int) -> int:
    september_first = dt.datetime(season, 9, 1, tzinfo=dt.timezone.utc)
    labor_day = september_first + dt.timedelta(days=(7 - september_first.weekday()) % 7)
    start = labor_day - dt.timedelta(days=7)
    return max(1, min(MAX_WEEK, ((now - start).days // 7) + 1))


def official_week(now: dt.datetime, season: int) -> int:
    """Resolve the week from CFBD's calendar, with the clock rule as fallback."""
    try:
        periods = [p for p in cfbd.calendar(season)
                   if str(p.get("seasonType", "regular")).lower() == "regular"]
        dated = []
        for period in periods:
            start = dt.datetime.fromisoformat(str(period["startDate"]).replace("Z", "+00:00"))
            end = dt.datetime.fromisoformat(str(period["endDate"]).replace("Z", "+00:00"))
            dated.append((start, end, int(period["week"])))
        for start, end, week in dated:
            if start <= now <= end + dt.timedelta(days=1):
                return week
        begun = [week for start, _, week in dated if start <= now]
        if begun:
            return max(begun)
    except Exception as exc:
        print(f"calendar lookup unavailable ({exc}); using clock fallback")
    return current_week(now, season)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_site/index.html")
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    season = args.season or int(os.environ.get("IN_SEASON") or 0) or current_season(now)
    week = args.week or int(os.environ.get("IN_WEEK") or 0) or official_week(now, season)

    print(f"building season={season} week={week}")
    path = site.build(season=season, week=week, out=Path(args.out))
    print(f"wrote {path} ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
