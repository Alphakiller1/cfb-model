"""Player box scores from ESPN's public college football API.

The player projections need, for every FBS team, each game's passing, rushing
and receiving lines by player. CFBD has them too, but its free allowance runs
dry mid-month (it did on 2026-09-28) and a projection layer that disappears for
days at a time is worse than none. ESPN needs no key.

Two endpoints:

    scoreboard?groups=80&week=W&seasontype=2&dates=SEASON   one week's FBS events
    summary?event=ID                                         one game's box score

A completed game's box score never changes, so it is cached permanently under
``data/cache/espn_box`` (CI restores that directory between runs); an
unfinished one is never cached. A week's scoreboard is cached only once every
game on it is complete.

ESPN's college rushing lines follow NCAA scoring: sacks are rushing attempts
for negative yards. That is what sportsbooks grade, so it is kept as published.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from .espn_odds import _get

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
CACHE_DIR = Path(os.environ.get(
    "CFB_ESPN_BOX_CACHE",
    str(Path(__file__).resolve().parents[3] / "data" / "cache" / "espn_box"),
))
REGULAR_SEASON_WEEKS = 16


@dataclass
class PlayerLine:
    athlete_id: str
    name: str
    team_id: str
    pass_cmp: float = 0.0
    pass_att: float = 0.0
    pass_yds: float = 0.0
    pass_td: float = 0.0
    pass_int: float = 0.0
    rush_car: float = 0.0
    rush_yds: float = 0.0
    rush_td: float = 0.0
    rec: float = 0.0
    rec_yds: float = 0.0
    rec_td: float = 0.0


@dataclass
class TeamGame:
    team_id: str
    name: str            # "Ohio State Buckeyes" - matched to CFBD schools by the caller
    location: str        # "Ohio State"
    home: bool
    points: float | None


@dataclass
class GameBox:
    event_id: str
    season: int
    week: int
    start: str
    completed: bool
    neutral: bool
    teams: list[TeamGame]
    players: list[PlayerLine] = field(default_factory=list)
    # Closing line from ESPN's pick centre: home margin (home points minus
    # away, positive = home favoured) and the total. Used by the backtest as
    # the game environment the live build takes from the sportsbook.
    home_margin_line: float | None = None
    total_line: float | None = None

    def opponent(self, team_id: str) -> str | None:
        return next((t.team_id for t in self.teams if t.team_id != team_id), None)

    def team(self, team_id: str) -> TeamGame | None:
        return next((t for t in self.teams if t.team_id == team_id), None)


def _cached(name: str) -> dict | None:
    path = CACHE_DIR / name
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def _store(name: str, payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / name).write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def week_events(season: int, week: int, *, season_type: int = 2) -> list[dict]:
    """Raw ESPN events for one FBS week."""
    name = f"scoreboard_{season}_{season_type}_{week}.json"
    cached = _cached(name)
    if cached is not None:
        return cached.get("events") or []
    query = urllib.parse.urlencode({"groups": 80, "week": week, "seasontype": season_type,
                                    "dates": season, "limit": 400})
    payload = _get(f"{BASE}/scoreboard?{query}")
    events = payload.get("events") or []
    if events and all(((e.get("status") or {}).get("type") or {}).get("completed")
                      for e in events):
        _store(name, {"events": events})
    return events


def _num(text) -> float:
    try:
        return float(str(text).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _pair(text) -> tuple[float, float]:
    """'11/16' -> (11, 16)."""
    parts = str(text or "").split("/")
    if len(parts) != 2:
        return 0.0, 0.0
    return _num(parts[0]), _num(parts[1])


def _players(summary: dict) -> list[PlayerLine]:
    out: dict[tuple[str, str], PlayerLine] = {}
    for side in (summary.get("boxscore") or {}).get("players") or []:
        team_id = str((side.get("team") or {}).get("id") or "")
        for cat in side.get("statistics") or []:
            labels = cat.get("labels") or []
            kind = cat.get("name")
            if kind not in ("passing", "rushing", "receiving"):
                continue
            for row in cat.get("athletes") or []:
                athlete = row.get("athlete") or {}
                aid = str(athlete.get("id") or "")
                if not aid:
                    continue
                stats = dict(zip(labels, row.get("stats") or []))
                line = out.setdefault((team_id, aid), PlayerLine(
                    athlete_id=aid, name=athlete.get("displayName") or "", team_id=team_id))
                if kind == "passing":
                    line.pass_cmp, line.pass_att = _pair(stats.get("C/ATT"))
                    line.pass_yds = _num(stats.get("YDS"))
                    line.pass_td = _num(stats.get("TD"))
                    line.pass_int = _num(stats.get("INT"))
                elif kind == "rushing":
                    line.rush_car = _num(stats.get("CAR"))
                    line.rush_yds = _num(stats.get("YDS"))
                    line.rush_td = _num(stats.get("TD"))
                else:
                    line.rec = _num(stats.get("REC"))
                    line.rec_yds = _num(stats.get("YDS"))
                    line.rec_td = _num(stats.get("TD"))
    return list(out.values())


def _pick_line(summary: dict, home_is_first: bool) -> tuple[float | None, float | None]:
    for pick in summary.get("pickcenter") or []:
        spread = pick.get("spread")
        total = pick.get("overUnder")
        try:
            spread = float(spread) if spread is not None else None
            total = float(total) if total is not None else None
        except (TypeError, ValueError):
            continue
        if spread is None and total is None:
            continue
        # ESPN's `spread` is the home line (negative = home favoured).
        return (-spread if spread is not None else None), total
    return None, None


def game_box(event: dict, season: int, week: int) -> GameBox | None:
    """Parse one scoreboard event, fetching its box score when it has been played."""
    comp = (event.get("competitions") or [{}])[0]
    completed = bool(((event.get("status") or {}).get("type") or {}).get("completed"))
    teams = []
    for c in comp.get("competitors") or []:
        team = c.get("team") or {}
        teams.append(TeamGame(
            team_id=str(team.get("id") or ""),
            name=team.get("displayName") or team.get("location") or "",
            location=team.get("location") or "",
            home=c.get("homeAway") == "home",
            points=_num(c.get("score")) if completed else None,
        ))
    if len(teams) != 2:
        return None
    box = GameBox(event_id=str(event.get("id")), season=season, week=week,
                  start=event.get("date") or "", completed=completed,
                  neutral=bool(comp.get("neutralSite")), teams=teams)
    if not completed:
        return box
    name = f"summary_{box.event_id}.json"
    summary = _cached(name)
    if summary is None:
        try:
            raw = _get(f"{BASE}/summary?event={box.event_id}")
        except Exception:
            return box
        # Keep only what is parsed: a full summary is ~400 KB of news and video.
        summary = {"boxscore": {"players": (raw.get("boxscore") or {}).get("players") or []},
                   "pickcenter": raw.get("pickcenter") or []}
        if summary["boxscore"]["players"]:
            _store(name, summary)
    box.players = _players(summary)
    box.home_margin_line, box.total_line = _pick_line(summary, True)
    return box


def season_boxes(season: int, *, through_week: int | None = None) -> list[GameBox]:
    """Every completed FBS game of a regular season, up to and including a week."""
    last = through_week if through_week is not None else REGULAR_SEASON_WEEKS
    out: list[GameBox] = []
    for week in range(1, last + 1):
        try:
            events = week_events(season, week)
        except Exception:
            continue
        for event in events:
            box = game_box(event, season, week)
            if box and box.completed and box.players:
                out.append(box)
    return out


def roster_positions(team_id: str, *, max_age: float = 20 * 3600) -> dict[str, str]:
    """athlete id -> position abbreviation (QB, RB, WR, TE, ...) for one team.

    Rosters change through the week, so the cache is short-lived.
    """
    import time

    name = f"roster_{team_id}.json"
    path = CACHE_DIR / name
    cached = None
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        cached = _cached(name)
    if cached is None:
        try:
            raw = _get(f"{BASE}/teams/{team_id}/roster")
        except Exception:
            return (_cached(name) or {}).get("positions") or {}
        positions = {}
        for group in raw.get("athletes") or []:
            for athlete in group.get("items") or []:
                pos = (athlete.get("position") or {}).get("abbreviation")
                if athlete.get("id") and pos:
                    positions[str(athlete["id"])] = pos
        cached = {"positions": positions}
        if positions:
            _store(name, cached)
    return cached.get("positions") or {}
