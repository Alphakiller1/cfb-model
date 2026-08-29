"""Calibration: is a projected margin on the right *scale*?

Accuracy and calibration are different questions and the repo only measured the
first. MAE asks "how far off is each game"; calibration asks "when the model
says 20, do those games average 20?" A shrunk estimator wins on MAE and loses on
calibration, so optimising MAE alone drives the model toward under-dispersion —
and an under-dispersed margin lands on the market underdog every time the price
is wide, which reads as a systematic opinion the model does not hold.

The measured position (updated 2026-08-29), which is why this module exists:

    weeks 5+ (validated regime)   model SD 12.16 vs market 12.67   ratio 0.96
    week 1  (preseason path)      raw model SD 9.61 vs market 16.00 ratio 0.60

The in-regime path is fine. Compression is most severe in Week 1. A six-season
leave-one-season-out audit found that an affine Week 1 correction lowered MAE
from 14.4109 to 12.8499 and reduced the underdog-side rate from 86.8% to 60.5%.
The market remained better at 11.7838, so the correction improves the raw point
estimate without turning its market difference into an edge.

**Under-dispersion is not automatically a defect.** A conditional mean shrinks
when it conditions on less, and that is correct behaviour — the market is itself
under-dispersed against actual outcomes (SD 12.67 vs 19.90). The defect is the
*gap between the two ratios*: 0.96 in-regime against 0.53 in week 1. That gap is
information the market has before kickoff and the model does not — transfer
portal, quarterback specifically rather than blended returning production,
coaching changes, availability.

So this module ships the held-out Week 1 affine calibration, but does not apply
an indiscriminate expansion to other regimes or authorize its residual as an
edge. Two constraints remain:

1. The earlier inverse-cap expansion was rejected monotonically on MAE (12.9749
   at alpha=0 rising to 13.6962 at alpha=1). That was a different, global
   transformation. The adopted Week 1 affine correction cleared held-out MAE;
   no result is generalized beyond the regime where it was measured.
2. The Week 1 correction still trails the market and therefore cannot establish
   a bettable disagreement. The honest next fix is closing the information gap.

`fit` is here so every calibration is measured against outcomes rather than
argued about. `WEEK1` carries the recorded result; later preseason weeks remain
identity until separately validated.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass


class NotEnoughData(ValueError):
    """Too few observations, or no spread in them, to fit anything."""


@dataclass(frozen=True)
class Calibration:
    """An affine correction, `intercept + slope * predicted`.

    Identity by default. `provenance` is required and is not decoration: a
    scale correction that cannot say what it was fitted on is not a measurement.
    """

    intercept: float = 0.0
    slope: float = 1.0
    provenance: str = "unfitted — identity"

    @property
    def is_identity(self) -> bool:
        return self.intercept == 0.0 and self.slope == 1.0

    def apply(self, predicted: float | None) -> float | None:
        if predicted is None:
            return None
        return self.intercept + self.slope * predicted


# Weeks 2-4 remain identity. Week 1 is materially different: it contains the
# season's largest talent mismatches and no current-season observations. On 282
# FBS-vs-FBS Week 1 games (2019, 2021-2025), leave-one-season-out affine
# calibration reduced MAE from 14.4109 to 12.8499 and the underdog-side rate
# versus the closing market from 86.8% to 60.5%. The slope was stable across
# folds (1.5333 +/- 0.0466). It still trails the market's 11.7838 MAE, so this
# corrects the independent point estimate but does not authorize an edge.
WEEK1 = Calibration(
    intercept=1.7236,
    slope=1.5333,
    provenance="LOSO 282 Week 1 FBS-vs-FBS games, 2019 and 2021-2025",
)

# Applied to ratings-only weeks other than Week 1. Identity until separately
# fitted and validated.
PRESEASON = Calibration()

# The full-regime path is an OLS fit and is scale-calibrated by construction on
# its fitting sample, which the walk-forward confirms (dispersion ratio 0.96).
# It is declared here so the asymmetry is explicit rather than an omission.
FULL_REGIME = Calibration(provenance="OLS fit — calibrated by construction")


def _pairs(predicted, actual) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    for x, y in zip(predicted, actual):
        if x is None or y is None:
            continue
        xs.append(float(x))
        ys.append(float(y))
    if len(xs) < 3:
        raise NotEnoughData(f"need at least 3 paired observations, got {len(xs)}")
    return xs, ys


def slope(predicted, actual) -> float:
    """Regression slope of ACTUAL on PREDICTED. 1.0 is calibrated.

    Above 1 means the model is under-dispersed — its 20s should have been 30s.
    Below 1 means it is over-confident. This is the diagnostic MAE cannot give
    you, because MAE is minimised by shrinking toward the mean.
    """
    xs, ys = _pairs(predicted, actual)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        raise NotEnoughData("predictions have no spread; slope is undefined")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def fit(predicted, actual) -> Calibration:
    """Least-squares affine correction putting predictions on the actual scale.

    Returned rather than assigned: adopting it changes what the board claims, so
    it belongs in a commit with the numbers beside it.
    """
    xs, ys = _pairs(predicted, actual)
    b = slope(xs, ys)
    a = statistics.fmean(ys) - b * statistics.fmean(xs)
    return Calibration(
        intercept=a, slope=b,
        provenance=f"fitted on {len(xs)} games",
    )


def dispersion_ratio(predicted, reference) -> float:
    """SD(predicted) / SD(reference). The market is the useful reference.

    Reported alongside the slope because they fail differently: a model can be
    correctly scaled on average and still be too timid at the tails, which is
    precisely the shape that produces a standing underdog lean.
    """
    xs, ys = _pairs(predicted, reference)
    spread = statistics.pstdev(ys)
    if spread == 0:
        raise NotEnoughData("reference has no spread")
    return statistics.pstdev(xs) / spread


@dataclass(frozen=True)
class Report:
    """One regime's calibration, in the form the decision actually needs."""

    label: str
    games: int
    calibration_slope: float | None
    model_sd: float
    market_sd: float | None
    actual_sd: float | None
    model_mae: float | None
    market_mae: float | None

    @property
    def dispersion_vs_market(self) -> float | None:
        if not self.market_sd:
            return None
        return self.model_sd / self.market_sd

    def lines(self) -> list[str]:
        out = [f"  {self.label}  (n={self.games})"]
        if self.calibration_slope is not None:
            verdict = ("under-dispersed" if self.calibration_slope > 1.15
                       else "over-confident" if self.calibration_slope < 0.85
                       else "calibrated")
            out.append(f"    calibration slope (actual ~ model) : "
                       f"{self.calibration_slope:.3f}   [{verdict}]")
        ratio = self.dispersion_vs_market
        if ratio is not None:
            out.append(f"    dispersion vs market               : {ratio:.3f}")
        sds = f"    SD  model {self.model_sd:.2f}"
        if self.market_sd is not None:
            sds += f"   market {self.market_sd:.2f}"
        if self.actual_sd is not None:
            sds += f"   actual {self.actual_sd:.2f}"
        out.append(sds)
        if self.model_mae is not None:
            maes = f"    MAE model {self.model_mae:.4f}"
            if self.market_mae is not None:
                maes += f"   market {self.market_mae:.4f}   " \
                        f"({self.model_mae - self.market_mae:+.4f})"
            out.append(maes)
        return out


def report(
    label: str,
    *,
    model: list[float | None],
    actual: list[float | None] | None = None,
    market: list[float | None] | None = None,
) -> Report:
    """Assemble one regime's numbers. `actual` is optional so a live board can be
    checked for dispersion before any result exists."""
    model_values = [m for m in model if m is not None]
    if len(model_values) < 3:
        raise NotEnoughData(f"need at least 3 model values, got {len(model_values)}")

    def _mae(a, b):
        pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
        return statistics.fmean(abs(x - y) for x, y in pairs) if pairs else None

    return Report(
        label=label,
        games=len(model_values),
        calibration_slope=(slope(model, actual) if actual is not None else None),
        model_sd=statistics.pstdev(model_values),
        market_sd=(statistics.pstdev([m for m in market if m is not None])
                   if market else None),
        actual_sd=(statistics.pstdev([a for a in actual if a is not None])
                   if actual else None),
        model_mae=(_mae(model, actual) if actual else None),
        market_mae=(_mae(market, actual) if actual and market else None),
    )
