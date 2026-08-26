"""Opponent-adjusted efficiency.

Raw season efficiency is confounded by schedule. A team that played three
Group-of-5 defenses posts a success rate that looks elite and is not, and in a
sport where schedules barely overlap out of conference that confound is large.
This solves it the same way the power ratings do: jointly, by iteration.

    adj_off_i = mean over games of ( off_ij + (league_def_mean - adj_def_j) )
    adj_def_j = mean over games of ( def_ji + (league_off_mean - adj_off_i) )

Measured effect, on the same 3,256-game walk-forward as everything else:

    raw season stats, all plays        MAE 12.8297
    raw season stats, garbage excluded     12.8229
    opponent-adjusted, garbage excluded    12.5251

Worth 0.30 points overall, and 0.70 in weeks 5-7 -- the regime that was worst.
Garbage-time exclusion on its own is worth almost nothing (0.007) because margin
capping in `ratings.py` already blunts blowouts upstream; it is kept on because
it is free and adjusts the right quantity.

**It also changed what the matrix believes.** Raw explosiveness scored 0.016 --
apparently worthless. Opponent-adjusted it scores 0.153 on offense and 0.189 on
defense. Explosiveness was never the weak signal; it was the confounded one.
"""

from __future__ import annotations

import statistics

# Stats available per-game. `pointsPerOpportunity` and `havoc` are season-only,
# and adding them back on top of the adjusted set changed MAE by 0.0001, so they
# are deliberately not used.
ADJUSTABLE_STATS = ("ppa", "successRate", "explosiveness", "stuffRate")

ITERATIONS = 10


def _pairs(rows: list[dict], stat: str) -> list[tuple[str, str, float, float]]:
    out = []
    for row in rows:
        team, opponent = row.get("team"), row.get("opponent")
        offense, defense = row.get("offense") or {}, row.get("defense") or {}
        o, d = offense.get(stat), defense.get(stat)
        if team and opponent and o is not None and d is not None:
            out.append((team, opponent, float(o), float(d)))
    return out


def adjust(rows: list[dict], stat: str, *, iterations: int = ITERATIONS
           ) -> tuple[dict[str, float], dict[str, float]]:
    """Return (adjusted_offense, adjusted_defense) for one stat."""
    pairs = _pairs(rows, stat)
    if not pairs:
        return {}, {}
    teams = sorted({t for t, _, _, _ in pairs})
    offense_mean = statistics.fmean([o for _, _, o, _ in pairs])
    defense_mean = statistics.fmean([d for _, _, _, d in pairs])

    adj_off = dict.fromkeys(teams, offense_mean)
    adj_def = dict.fromkeys(teams, defense_mean)
    for _ in range(iterations):
        acc_off: dict[str, list[float]] = {t: [] for t in teams}
        acc_def: dict[str, list[float]] = {t: [] for t in teams}
        for team, opponent, o, d in pairs:
            # Credit the offense for the defense it actually faced, and vice versa.
            acc_off[team].append(o + (defense_mean - adj_def.get(opponent, defense_mean)))
            acc_def[team].append(d + (offense_mean - adj_off.get(opponent, offense_mean)))
        adj_off = {t: (statistics.fmean(v) if v else offense_mean) for t, v in acc_off.items()}
        adj_def = {t: (statistics.fmean(v) if v else defense_mean) for t, v in acc_def.items()}
    return adj_off, adj_def


def adjust_all(rows: list[dict], *, stats: tuple[str, ...] = ADJUSTABLE_STATS
               ) -> dict[str, tuple[dict[str, float], dict[str, float]]]:
    """Adjust every stat, returning {stat: (offense, defense)}."""
    return {stat: adjust(rows, stat) for stat in stats}


# Pace feeds the totals model, not the margin model. It is deliberately NOT
# opponent-adjusted: how many drives a team gets is mostly a property of how it
# and its opponent play, and adjusting it would double-count the opponent term
# the totals model already carries as a sum.
PACE_STATS = ("drives", "plays")


def pace_means(rows: list[dict], *, stats: tuple[str, ...] = PACE_STATS
               ) -> dict[str, dict[str, float]]:
    """Per-team mean offensive drives and plays per game."""
    acc: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        team = row.get("team")
        offense = row.get("offense") or {}
        if not team:
            continue
        bucket = acc.setdefault(team, {s: [] for s in stats})
        for stat in stats:
            value = offense.get(stat)
            if value is not None:
                bucket[stat].append(float(value))
    return {
        team: {s: (statistics.fmean(v) if v else None) for s, v in d.items()}
        for team, d in acc.items()
    }
