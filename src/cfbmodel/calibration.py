"""Calibration: is a projected margin on the right *scale*?

Accuracy and calibration are different questions and the repo only measured the
first. MAE asks "how far off is each game"; calibration asks "when the model
says 20, do those games average 20?" A shrunk estimator wins on MAE and loses on
calibration, so optimising MAE alone drives the model toward under-dispersion —
and an under-dispersed margin lands on the market underdog every time the price
is wide, which reads as a systematic opinion the model does not hold.

The measured position (2026-08-27), which is why this module exists:

    weeks 5+ (validated regime)   model SD 12.16 vs market 12.67   ratio 0.96
    week 1  (preseason path)      model SD  8.05 vs market 15.18   ratio 0.53

The in-regime path is fine. The compression is confined to the ratings-only
preseason path, and on a 49-game week-1 board it produced a model-on-market
regression slope of 0.478: the model landed on the underdog in 43 of 49 games
and in 100% of games priced above a touchdown.

**Under-dispersion is not automatically a defect.** A conditional mean shrinks
when it conditions on less, and that is correct behaviour — the market is itself
under-dispersed against actual outcomes (SD 12.67 vs 19.90). The defect is the
*gap between the two ratios*: 0.96 in-regime against 0.53 in week 1. That gap is
information the market has before kickoff and the model does not — transfer
portal, quarterback specifically rather than blended returning production,
coaching changes, availability.

So this module deliberately does **not** ship a fitted expansion. Two reasons:

1. `reports/BASELINE_2019_2025.md` already rejected expansion, monotonically, on
   MAE (12.9749 at α=0 rising to 13.6962 at α=1). Any recalibration will cost
   MAE. That is expected and is not by itself a reason to refuse it — but it
   does mean the decision belongs to a gate record, not to a default.
2. Inflating a point estimate to match a better-informed one manufactures
   confidence the model has not earned. The honest fixes are to close the
   information gap and to stop calling the residual an edge.

`fit` is here so the number can be *measured* against outcomes rather than
argued about, and `PRESEASON` stays identity until someone runs it and records
what they found.
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


# Applied to the ratings-only/preseason path. Identity until fitted; see the
# module docstring for why that default is deliberate rather than a TODO.
# Fit it with `python -m cfbmodel.cli calibrate --seasons 2019,2021-2025`.
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
