"""CFB inputs for `volatility`: per-game process lines.

Process lines come from the same CFBD per-game advanced rows the efficiency
adjustment already fetches (garbage time excluded), so the live build pays no
extra calls. PPA is left out on purpose: it carries turnover plays at full
weight, and turnovers are exactly the luck noise cancellation is meant to strip.
"""

from __future__ import annotations

PROCESS_FEATURES = ("successRate", "explosiveness", "stuffRate", "plays", "drives")
# Rates whose game-to-game spread forms the consistency prior.
CONSISTENCY_STATS = ("successRate", "explosiveness")


def process_index(rows: list[dict], season: int | None = None
                  ) -> dict[tuple[int, int, str, str], dict]:
    """(season, week, team, opponent) -> that team's offensive process line."""
    out = {}
    for row in rows:
        team, opponent, week = row.get("team"), row.get("opponent"), row.get("week")
        offense = row.get("offense") or {}
        year = row.get("season", season)
        if not team or not opponent or week is None or year is None:
            continue
        out[(int(year), int(week), team, opponent)] = {
            f: offense.get(f) for f in PROCESS_FEATURES}
    return out
