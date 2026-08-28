"""Least squares, and the leave-one-season-out harness that governs adoption.

This package has no dependencies, so the solver is here rather than imported.
It is ordinary normal-equations OLS with partial pivoting and a ridge term, and
the ridge is not decoration: the candidate features are collinear by
construction (a first-year coach correlates with portal churn, which correlates
with returning production), and an unregularised normal-equations solve on
collinear columns produces coefficients that swing wildly between folds and
look like signal.

**Why leave-one-season-out and not a random split.** Team strength is
autocorrelated within a season, so a random split leaks: the same team's other
games sit in both halves. Holding out whole seasons is what
`reports/BASELINE_2019_2025.md` already does for every other number in this
repo, and a new feature has to clear the same bar.

A feature earns a coefficient by lowering held-out error. That is the only
criterion; a plausible mechanism is not one.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

# Small by design. Enough to stabilise collinear columns, not enough to shrink a
# real effect toward nothing.
DEFAULT_RIDGE = 1e-6


class SingularSystem(ValueError):
    """The design matrix is rank-deficient even after ridging."""


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(rhs)
    augmented = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise SingularSystem(f"column {column} is degenerate")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        for row in range(column + 1, n):
            factor = augmented[row][column] / augmented[column][column]
            if factor == 0.0:
                continue
            for k in range(column, n + 1):
                augmented[row][k] -= factor * augmented[column][k]
    out = [0.0] * n
    for row in range(n - 1, -1, -1):
        total = augmented[row][n] - sum(
            augmented[row][k] * out[k] for k in range(row + 1, n)
        )
        out[row] = total / augmented[row][row]
    return out


def ols(
    rows: list[dict[str, float]],
    target: list[float],
    names: list[str],
    *,
    ridge: float = DEFAULT_RIDGE,
) -> dict[str, float]:
    """Fit `target ~ intercept + sum(coef * feature)`.

    A row missing a feature contributes 0 for it, which is why every caller
    centres its features first: 0 then means "league average", the only value
    that makes a missing feed harmless.
    """
    if len(rows) != len(target):
        raise ValueError(f"{len(rows)} rows against {len(target)} targets")
    if len(rows) <= len(names) + 1:
        raise SingularSystem(
            f"{len(rows)} observations cannot fit {len(names) + 1} parameters"
        )
    columns = ["intercept", *names]
    design = [[1.0] + [row.get(name, 0.0) for name in names] for row in rows]

    size = len(columns)
    xtx = [[0.0] * size for _ in range(size)]
    xty = [0.0] * size
    for row, y in zip(design, target):
        for i in range(size):
            xty[i] += row[i] * y
            for j in range(size):
                xtx[i][j] += row[i] * row[j]
    for i in range(1, size):  # never ridge the intercept
        xtx[i][i] += ridge
    return dict(zip(columns, _solve(xtx, xty)))


def predict(coefficients: dict[str, float], row: dict[str, float]) -> float:
    return coefficients.get("intercept", 0.0) + sum(
        value * row.get(name, 0.0)
        for name, value in coefficients.items() if name != "intercept"
    )


@dataclass
class FoldResult:
    held_out: int
    n: int
    mae: float
    coefficients: dict[str, float] = field(default_factory=dict)


@dataclass
class CandidateResult:
    """One feature set, scored out of sample."""

    label: str
    names: list[str]
    folds: list[FoldResult]

    @property
    def mae(self) -> float:
        """Pooled across folds, weighted by fold size — an unweighted mean of
        fold MAEs would let a short season count as much as a full one."""
        total = sum(fold.n for fold in self.folds)
        return sum(fold.mae * fold.n for fold in self.folds) / total if total else 0.0

    @property
    def games(self) -> int:
        return sum(fold.n for fold in self.folds)

    def pooled_coefficients(self) -> dict[str, float]:
        """Mean coefficient across folds, with its spread.

        Reported rather than refitted on everything, because a coefficient that
        changes sign between folds is not a finding and averaging it silently
        would hide that.
        """
        keys = sorted({k for fold in self.folds for k in fold.coefficients})
        return {
            key: statistics.fmean(
                [fold.coefficients.get(key, 0.0) for fold in self.folds]
            )
            for key in keys
        }

    def coefficient_spread(self) -> dict[str, float]:
        keys = sorted({k for fold in self.folds for k in fold.coefficients})
        return {
            key: (statistics.pstdev([fold.coefficients.get(key, 0.0)
                                     for fold in self.folds])
                  if len(self.folds) > 1 else 0.0)
            for key in keys
        }


def leave_one_season_out(
    label: str,
    names: list[str],
    observations: list[tuple[int, dict[str, float], float]],
    *,
    ridge: float = DEFAULT_RIDGE,
) -> CandidateResult:
    """Score one feature set. `observations` is (season, features, target)."""
    seasons = sorted({season for season, _, _ in observations})
    if len(seasons) < 2:
        raise ValueError("need at least two seasons to hold one out")
    folds: list[FoldResult] = []
    for held_out in seasons:
        train = [(f, y) for s, f, y in observations if s != held_out]
        test = [(f, y) for s, f, y in observations if s == held_out]
        if not test or len(train) <= len(names) + 1:
            continue
        try:
            coefficients = ols([f for f, _ in train], [y for _, y in train],
                               names, ridge=ridge)
        except SingularSystem:
            continue
        errors = [abs(predict(coefficients, f) - y) for f, y in test]
        folds.append(FoldResult(
            held_out=held_out, n=len(errors),
            mae=statistics.fmean(errors), coefficients=coefficients,
        ))
    if not folds:
        raise SingularSystem(f"{label}: no fold could be fitted")
    return CandidateResult(label=label, names=names, folds=folds)


def compare(results: list[CandidateResult]) -> list[str]:
    """Render a candidate table, best first, against the first entry as baseline."""
    if not results:
        return ["  no candidates"]
    baseline = results[0]
    width = max(len(r.label) for r in results)
    lines = [
        f"  {'candidate'.ljust(width)}  {'MAE':>9}  {'vs base':>9}  {'n':>6}",
        f"  {'-' * width}  {'-' * 9}  {'-' * 9}  {'-' * 6}",
    ]
    for result in results:
        delta = result.mae - baseline.mae
        marker = "" if result is baseline else ("  <- better" if delta < 0 else "")
        lines.append(
            f"  {result.label.ljust(width)}  {result.mae:9.4f}  "
            f"{delta:+9.4f}  {result.games:6d}{marker}"
        )
    return lines
