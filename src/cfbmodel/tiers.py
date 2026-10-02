"""Power-4 vs Group-of-5, and the correction the rating solve needs across it.

Cross-tier games are sparse, so the rating graph's P4 and G5 halves are only
weakly connected and the solve under-separates them. Measured walk-forward on
the production forecast path (scripts/audit_tiers.py, 3,702 FBS games,
2021-2025, leave-one-season-out), the model under-rates the P4 side of a
P4-vs-G5 game by a stable margin:

    weeks 1-4   414 cross-tier games   +3.84 pts (fold SD 0.21)
                MAE on those games 13.02 -> 12.67 (market 11.93)
    weeks 5+     89 cross-tier games   +3.64 pts (fold SD 0.79)

Intercept-only, home-field and rescaling corrections were scored in the same
run and rejected: none lowered held-out error.
"""

from __future__ import annotations

POWER_CONFERENCES = frozenset({"SEC", "Big Ten", "Big 12", "ACC", "Pac-12"})
# Independents that schedule and are priced as a power programme.
POWER_INDEPENDENTS = frozenset({"Notre Dame"})
# The two-team Pac-12 of 2024-25 is not a power league; the rebuilt one is not either.
PAC12_POWER_THROUGH = 2023

SHIFT_POINTS = {"early": 3.84, "validated": 3.64}


def tier(school: str, conference: str | None, season: int) -> str:
    if school in POWER_INDEPENDENTS:
        return "P4"
    if conference == "Pac-12" and season > PAC12_POWER_THROUGH:
        return "G5"
    return "P4" if conference in POWER_CONFERENCES else "G5"


def orientation(home: str, home_conf: str | None, away: str, away_conf: str | None,
                season: int) -> int:
    """+1 when the home side is the P4 team of a P4-vs-G5 game, -1 when the away
    side is, 0 for same-tier games."""
    h, a = tier(home, home_conf, season), tier(away, away_conf, season)
    if h == a:
        return 0
    return 1 if h == "P4" else -1
