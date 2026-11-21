"""Fresh public score/kickoff checks for the existing CFBD slate identities."""
from __future__ import annotations

from datetime import datetime, timezone

from . import espn_box, oddsapi


def refresh(games: list[dict], season: int, week: int) -> tuple[list[dict], dict]:
    current = [g for g in games if g.get("week") == week]
    index = oddsapi.build_index({n: None for g in games for n in (g.get("homeTeam"), g.get("awayTeam")) if n})
    by_id, by_name = {}, {}
    for event in espn_box.week_events(season, week):
        sides = {c.get("homeAway"): c for c in (event.get("competitions") or [{}])[0].get("competitors", [])}
        home, away = sides.get("home"), sides.get("away")
        if not home or not away:
            continue
        by_id[(str(home["team"]["id"]), str(away["team"]["id"]))] = event
        pair = tuple(oddsapi.match_team(c["team"].get("displayName") or c["team"].get("location", ""), index) for c in (home, away))
        if None not in pair:
            by_name[pair] = event
    out, matched = [], 0
    required = [g for g in current if g.get("homeClassification") == g.get("awayClassification") == "fbs"]
    for g in games:
        event = None
        if g.get("week") == week:
            event = by_id.get((str(g.get("homeId")), str(g.get("awayId")))) or by_name.get((g.get("homeTeam"), g.get("awayTeam")))
        if not event:
            out.append(g)
            continue
        row = dict(g)
        if g in required:
            matched += 1
        state = event.get("status", {}).get("type", {})
        row["completed"] = bool(state.get("completed"))
        row["startDate"] = event.get("date") or row.get("startDate")
        for c in (event.get("competitions") or [{}])[0].get("competitors", []):
            prefix = "home" if c.get("homeAway") == "home" else "away"
            value = c.get("score")
            row[prefix + "Points"] = float(value) if row["completed"] and value not in (None, "") else None
        out.append(row)
    return out, {"source": "ESPN public FBS scoreboard", "season": season, "week": week,
                 "verified_at": datetime.now(timezone.utc).isoformat(),
                 "matched": matched, "required": len(required), "complete": bool(required) and matched == len(required)}
