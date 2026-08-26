"""CFB logic matrix -- fitted, not assumed.

The scaffold this replaces (chase-analytics-brain `core/genesis/sports/cfb.py`)
carried research priors reasoned from the NFL matrix. Most survived contact with
data. One did not, and it is worth stating plainly:

**Explosiveness was over-weighted on a plausible-sounding argument.** The prior
reasoned that because CFB defenses vary far more than NFL ones, big plays are
more repeatable and deserve *more* weight than in the NFL (0.20 vs 0.14). Fitted
against 3,256 out-of-sample games, offensive explosiveness carries essentially no
independent weight (0.016) once success rate and PPA are in the model. Success
rate dominates both sides. Explosiveness is largely already priced by PPA, which
is points-per-play and therefore rewards the same big gains.

Weights are standardised importances -- |coefficient| x feature standard
deviation -- from a leave-one-season-out linear fit on point-in-time features,
normalised within each group. They express *relative* contribution, which is what
a weight group means; the raw regression coefficients live in
`reports/BASELINE_2019_2025.md`.

Adding these features to opponent-adjusted ratings is worth 0.187 points of MAE
(12.975 -> 12.788). That is real but modest, and still 0.63 points short of the
closing market. See `authority.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

LINEAGE_VERSION = "2026.08-fitted"
STATUS = "CHALLENGER/UNPROMOTED"
SOURCE_LINEAGE = (
    "chase-analytics-brain/core/genesis/sports/cfb.py",   # the scaffold this corrects
    "Alphakiller1/nfl-genesis/src/genesis/logic_matrix.py",
    "collegefootballdata.com /stats/season/advanced",
)

# Fitted. Sums to 1.0.
OFFENSE_WEIGHTS = {
    "success_rate": 0.5455,
    "ppa_per_play": 0.2531,
    "points_per_opportunity": 0.1853,
    "explosiveness": 0.0161,
}

# Fitted. Sums to 1.0. "_inverse" means the raw stat is better when lower, so it
# enters the blend negated.
DEFENSE_WEIGHTS = {
    "success_rate_allowed_inverse": 0.3994,
    "points_per_opportunity_allowed_inverse": 0.2705,
    "ppa_allowed_inverse": 0.1727,
    "explosiveness_allowed_inverse": 0.1266,
    "havoc_rate": 0.0308,
}

# NOT fitted -- these remain research priors. CFBD exposes talent and returning
# production, but they are preseason quantities whose effect is confounded with
# the opponent-adjusted rating once a few games exist. Marked as unfitted so no
# one mistakes them for measured values.
TALENT_WEIGHTS = {
    "recruiting_composite_2yr": 0.45,
    "returning_production": 0.30,
    "portal_net_rating": 0.25,
}

# Measured on 2019, 2021-2025 FBS-vs-FBS games. See ratings.py.
HOME_FIELD_POINTS = 4.53
BLOWOUT_CAP = 32.0
RECENCY_HALFLIFE_WEEKS = 12.0
MARGIN_SD = 24.2

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
    """Point-in-time efficiency for one team. All fields optional: early-season
    teams legitimately have no prior form, and a missing value must not silently
    become zero."""

    success_rate: float | None = None
    ppa_per_play: float | None = None
    points_per_opportunity: float | None = None
    explosiveness: float | None = None
    success_rate_allowed: float | None = None
    points_per_opportunity_allowed: float | None = None
    ppa_allowed: float | None = None
    explosiveness_allowed: float | None = None
    havoc_rate: float | None = None

    def complete(self) -> bool:
        return all(getattr(self, f) is not None for f in (
            "success_rate", "ppa_per_play", "points_per_opportunity", "explosiveness",
            "success_rate_allowed", "points_per_opportunity_allowed", "ppa_allowed",
            "explosiveness_allowed", "havoc_rate"))


def offense_index(form: TeamForm) -> float | None:
    """Weighted offensive efficiency index, or None when form is incomplete."""
    values = {
        "success_rate": form.success_rate,
        "ppa_per_play": form.ppa_per_play,
        "points_per_opportunity": form.points_per_opportunity,
        "explosiveness": form.explosiveness,
    }
    if any(v is None for v in values.values()):
        return None
    return sum(OFFENSE_WEIGHTS[k] * v for k, v in values.items())


def defense_index(form: TeamForm) -> float | None:
    """Weighted defensive index. Allowed-stats are negated so higher is better."""
    values = {
        "success_rate_allowed_inverse": None if form.success_rate_allowed is None else -form.success_rate_allowed,
        "points_per_opportunity_allowed_inverse": None if form.points_per_opportunity_allowed is None else -form.points_per_opportunity_allowed,
        "ppa_allowed_inverse": None if form.ppa_allowed is None else -form.ppa_allowed,
        "explosiveness_allowed_inverse": None if form.explosiveness_allowed is None else -form.explosiveness_allowed,
        "havoc_rate": form.havoc_rate,
    }
    if any(v is None for v in values.values()):
        return None
    return sum(DEFENSE_WEIGHTS[k] * v for k, v in values.items())
