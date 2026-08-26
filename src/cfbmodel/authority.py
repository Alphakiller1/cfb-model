"""Authority gate -- what a CFB forecast is *allowed* to be used for.

A probability and a permission are different things, and conflating them is how
an unpromoted model becomes a bet. `forecast.py` produces numbers; nothing
downstream may act on them without an `Authority` saying the evidence supports it.

Ported from `nfl-model`, with CFB's own measured evidence.

Current state, from `reports/BASELINE_2019_2025.md` -- walk-forward on 3,256
out-of-sample FBS-vs-FBS games (2019, 2021-2025; 2020 excluded as non-comparable):

    model  MAE 12.9749
    market MAE 12.1596        model is 0.8154 points WORSE
    ATS when the model disagrees with the spread: 1595-1635-26 = 49.38%
    95% CI [47.66%, 51.11%]   breakeven at -110 is 52.38%

The confidence interval sits entirely below breakeven, so this is not a
"needs more data" result -- on this sample the disagreement is confidently
unprofitable. Authority is RESEARCH_ONLY and `may_bet` is False.

Deliberately hard to override: `promote()` requires every gate passed in
explicitly. There is no boolean that flips it on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Level(str, Enum):
    """Ordered from least to most permission."""

    RESEARCH_ONLY = "RESEARCH_ONLY"   # numbers only; never sized, never staked
    SHADOW = "SHADOW"                 # logged as if traded, no capital
    PROMOTED = "PROMOTED"             # may emit BET


class Action(str, Enum):
    AVOID = "AVOID"        # no usable price, or a failed gate
    MONITOR = "MONITOR"    # priced and modelled, but not promoted
    REVIEW = "REVIEW"      # edge implausible enough to suspect the inputs
    BET = "BET"            # promoted only


# Mirrors nfl-genesis registry/production_requirements.yaml so the two football
# models are held to one standard. Listed explicitly so a reader can see exactly
# what is unmet rather than trusting a single flag.
REQUIRED_GATES: tuple[str, ...] = (
    "historical_seasons_at_least_6",
    "out_of_sample_games_at_least_1500",
    "out_of_sample_bets_at_least_300",
    "timestamped_quote_coverage_at_least_0.98",
    "model_mae_below_closing_market",
    "ats_lower_confidence_bound_above_breakeven",
    "roi_lower_confidence_bound_above_zero_after_cost",
    "probability_space_clv_above_zero",
    "calibration_ece_at_most_0.025",
    "shadow_days_at_least_90",
)

# What the measured evidence actually satisfies today. Deliberately short.
SATISFIED_GATES: tuple[str, ...] = (
    "historical_seasons_at_least_6",        # 2019, 2021-2025
    "out_of_sample_games_at_least_1500",    # 3,256
    "out_of_sample_bets_at_least_300",      # 3,230 model-vs-market disagreements
)

# An "edge" larger than this against a liquid closing line is far more likely to
# be a stale quote or a team-name mapping error than a real disagreement. CFB
# spreads run much wider than NFL ones, so this is expressed in points.
IMPLAUSIBLE_EDGE_POINTS = 21.0


@dataclass(frozen=True)
class Authority:
    level: Level
    unmet_gates: tuple[str, ...] = field(default_factory=tuple)
    evidence: str = ""

    @property
    def may_bet(self) -> bool:
        return self.level is Level.PROMOTED and not self.unmet_gates

    def action_for(self, edge_points: float | None, has_price: bool) -> Action:
        """Map a modelled edge in points to what may actually be done with it."""
        if not has_price or edge_points is None:
            return Action.AVOID
        if abs(edge_points) > IMPLAUSIBLE_EDGE_POINTS:
            # Loud on purpose: against a liquid line this is a bug signal, not
            # an opportunity.
            return Action.REVIEW
        return Action.BET if self.may_bet else Action.MONITOR


def current() -> Authority:
    """The authority the measured CFB evidence actually supports today."""
    unmet = tuple(g for g in REQUIRED_GATES if g not in SATISFIED_GATES)
    return Authority(
        level=Level.RESEARCH_ONLY,
        unmet_gates=unmet,
        evidence=(
            "reports/BASELINE_2019_2025.md - walk-forward on 3,256 out-of-sample "
            "FBS games: model MAE 12.9749 vs closing market 12.1596 (0.8154 worse); "
            "ATS on disagreements 49.38%, 95% CI [47.66%, 51.11%] vs 52.38% breakeven"
        ),
    )


def promote(satisfied: set[str]) -> Authority:
    """Promote only when every required gate is explicitly satisfied.

    Takes the satisfied set as an argument rather than reading a config flag, so
    promotion is always an auditable claim about evidence.
    """
    unmet = tuple(g for g in REQUIRED_GATES if g not in satisfied)
    if unmet:
        return Authority(Level.RESEARCH_ONLY, unmet, "promotion refused")
    return Authority(Level.PROMOTED, (), "all production gates satisfied")
