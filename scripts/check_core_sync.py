"""Pin the matrix against its published evidence.

The weights here are mirrored in `chase-analytics-brain/core/genesis/sports/cfb.py`,
which CI cannot see -- that repo is private and separate. So instead of reaching
for it, this pins the published values against the numbers written into
`reports/BASELINE_2019_2025.md`.

That catches the failure that actually matters: someone edits a weight without
re-running the fit, and the page then documents numbers the model no longer uses.
Changing a weight is supposed to be a deliberate act with a report update and a
sync of the brain copy; this makes skipping either one fail loudly.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cfbmodel import matrix  # noqa: E402

# Fitted 2026-08-26 on 3,256 out-of-sample games, opponent-adjusted features.
EXPECTED_OFFENSE = {
    "success_rate": 0.5796,
    "explosiveness": 0.1533,
    "ppa_per_play": 0.1488,
    "stuff_rate_inverse": 0.1183,
}
EXPECTED_DEFENSE = {
    "success_rate_allowed_inverse": 0.5402,
    "explosiveness_allowed_inverse": 0.1889,
    "ppa_allowed_inverse": 0.1451,
    "stuff_rate": 0.1258,
}
EXPECTED_CONSTANTS = {
    "HOME_FIELD_POINTS": 4.53,
    "BLOWOUT_CAP": 32.0,
    "RECENCY_HALFLIFE_WEEKS": 12.0,
    "MARGIN_SD": 24.2,
}

REPORT = Path(__file__).resolve().parent.parent / "reports" / "BASELINE_2019_2025.md"


def main() -> int:
    failures: list[str] = []

    for label, expected, actual in (
        ("offense", EXPECTED_OFFENSE, matrix.OFFENSE_WEIGHTS),
        ("defense", EXPECTED_DEFENSE, matrix.DEFENSE_WEIGHTS),
    ):
        if set(expected) != set(actual):
            failures.append(f"{label}: feature set changed "
                            f"(added {sorted(set(actual)-set(expected))}, "
                            f"removed {sorted(set(expected)-set(actual))})")
            continue
        for key, want in expected.items():
            if abs(actual[key] - want) > 1e-9:
                failures.append(f"{label}.{key}: {actual[key]} != published {want}")

    for name, want in EXPECTED_CONSTANTS.items():
        got = getattr(matrix, name)
        if abs(got - want) > 1e-9:
            failures.append(f"{name}: {got} != published {want}")

    # Every predictive coefficient must correspond to a real form field.
    for field in matrix.TeamForm.FIELDS:
        if field not in matrix.COEFFICIENTS:
            failures.append(f"COEFFICIENTS missing {field}")

    # The report must quote the headline the authority gate cites.
    if REPORT.is_file():
        report = REPORT.read_text(encoding="utf-8")
        for token in ("12.5251", "12.1596", "51.11%"):
            if token not in report:
                failures.append(f"report no longer quotes {token}")
    else:
        failures.append(f"missing {REPORT}")

    if failures:
        for f in failures:
            print(f"FAIL {f}")
        print("\nIf a weight changed on purpose: re-run the fit, update "
              "reports/BASELINE_2019_2025.md and the EXPECTED_* values here, and "
              "sync chase-analytics-brain/core/genesis/sports/cfb.py.")
        return 1
    print("matrix matches published evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
