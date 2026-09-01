"""CFB logic matrix -- fitted on opponent-adjusted efficiency.

Two things live here, and they are not the same thing:

* `OFFENSE_WEIGHTS` / `DEFENSE_WEIGHTS` -- the **interpretation**. Standardised
  importances (|coefficient| x feature SD) normalised within each group, so the
  relative contribution of each family can be read and argued with. This is what
  a *matrix* is for.
* `COEFFICIENTS` -- what the model actually **predicts** with. Collapsing the
  eight features into two weighted indices costs 0.0745 points of MAE (12.5995
  vs 12.5251) and drops ATS from 51.11% to 50.34%, so the forecast uses the full
  fit and the weights document it.

Fitted on 3,256 out-of-sample FBS games (2019, 2021-2025), leave-one-season-out,
with every feature opponent-adjusted and garbage time excluded. See
`efficiency.py` for why the adjustment matters and
`reports/BASELINE_2019_2025.md` for the numbers.

**The explosiveness story, corrected twice.** The original scaffold reasoned that
weaker, more variable CFB defenses make big plays more repeatable, so
explosiveness deserved more weight than the NFL's 0.14. Fitted on *raw* season
stats it scored 0.016 and looked worthless. Fitted on *opponent-adjusted* stats
it scores 0.153 on offense and 0.189 on defense. The prior was right; the raw
measurement was confounded by schedule strength. Success rate still dominates.
"""

from __future__ import annotations

from dataclasses import dataclass

LINEAGE_VERSION = "2026.09-reliability-blended"
STATUS = "CHALLENGER/UNPROMOTED"
SOURCE_LINEAGE = (
    "chase-analytics-brain/core/genesis/sports/cfb.py",
    "Alphakiller1/nfl-genesis/src/genesis/logic_matrix.py",
    "collegefootballdata.com /stats/game/advanced (excludeGarbageTime)",
)

# Interpretation. Sums to 1.0.
OFFENSE_WEIGHTS = {
    "success_rate": 0.5796,
    "explosiveness": 0.1533,
    "ppa_per_play": 0.1488,
    "stuff_rate_inverse": 0.1183,
}

DEFENSE_WEIGHTS = {
    "success_rate_allowed_inverse": 0.5402,
    "explosiveness_allowed_inverse": 0.1889,
    "ppa_allowed_inverse": 0.1451,
    "stuff_rate": 0.1258,
}

# NOT fitted -- preseason quantities, confounded with the opponent-adjusted
# rating once games exist. Research priors only.
TALENT_WEIGHTS = {
    "recruiting_composite_2yr": 0.45,
    "returning_production": 0.30,
    "portal_net_rating": 0.25,
}

# Prediction. Points of margin per unit of (home - away) difference in each
# opponent-adjusted stat. Signs are meaningful: a high offensive stuff rate means
# being stuffed, and low allowed-stats mean a good defence.
COEFFICIENTS: dict[str, float] = {
    "intercept": 0.5920,
    "rating_margin": 0.4476,
    "off_ppa": 5.5250,
    "def_ppa": -6.2664,
    "off_successRate": 47.1558,
    "def_successRate": -49.7084,
    "off_explosiveness": 5.4551,
    "def_explosiveness": -6.6809,
    "off_stuffRate": -12.0629,
    "def_stuffRate": 12.3940,
}

# Measured. See ratings.py and the baseline report.
HOME_FIELD_POINTS = 4.53
BLOWOUT_CAP = 32.0
RECENCY_HALFLIFE_WEEKS = 12.0
MARGIN_SD = 24.2

# Early-season form reliability. One or two games of opponent-adjusted form are
# informative, but treating them as a full-season estimate is noisy. Measured on
# 778 FBS-vs-FBS games from 2021-2025, fixed week-level blends lowered MAE from
# 13.6453 (full efficiency immediately) and 13.6767 (preseason only) to 13.2219.
# The candidate improved every held-out season. Week 5 remains the first regime
# where the full matrix was originally validated.
EARLY_EFFICIENCY_RELIABILITY: dict[int, float] = {
    2: 0.30,
    3: 0.50,
    4: 0.80,
}

GROUPS = {
    "offense": OFFENSE_WEIGHTS,
    "defense": DEFENSE_WEIGHTS,
    "talent": TALENT_WEIGHTS,
}


class WeightGroupError(ValueError):
    """A weight group violates the matrix contract."""


def validate_weight_group(weights: dict[str, float], *, name: str = "group") -> None:
    """Non-negative and summing to one. Mirrors the shared genesis core."""
    if not weights:
        raise WeightGroupError(f"{name}: empty weight group")
    negative = sorted(k for k, v in weights.items() if v < 0)
    if negative:
        raise WeightGroupError(f"{name}: negative weights {negative}")
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-9:
        raise WeightGroupError(f"{name}: must sum to 1.0, got {total!r}")


for _name, _group in GROUPS.items():
    validate_weight_group(_group, name=_name)


@dataclass(frozen=True)
class TeamForm:
    """Opponent-adjusted efficiency for one team.

    Every field is optional because early-season teams legitimately have no prior
    form, and a missing value must never silently become zero.
    """

    off_ppa: float | None = None
    off_successRate: float | None = None
    off_explosiveness: float | None = None
    off_stuffRate: float | None = None
    def_ppa: float | None = None
    def_successRate: float | None = None
    def_explosiveness: float | None = None
    def_stuffRate: float | None = None
    # Pace. Used only by the totals model -- deliberately outside FIELDS so the
    # margin coefficients and `complete()` are unaffected by its availability.
    drives: float | None = None
    plays: float | None = None

    FIELDS = ("off_ppa", "off_successRate", "off_explosiveness", "off_stuffRate",
              "def_ppa", "def_successRate", "def_explosiveness", "def_stuffRate")

    def complete(self) -> bool:
        """Margin-feature completeness. Pace is checked separately by `totals`."""
        return all(getattr(self, f) is not None for f in self.FIELDS)


def margin_points(home: TeamForm, away: TeamForm) -> float | None:
    """Points of margin from the efficiency difference, or None if either side
    has incomplete form."""
    if not (home.complete() and away.complete()):
        return None
    return sum(
        COEFFICIENTS[f] * (getattr(home, f) - getattr(away, f))
        for f in TeamForm.FIELDS
    )
