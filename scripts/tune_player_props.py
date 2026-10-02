"""Tune the player-projection knobs time-forward: choose on 2024, report on 2025.

    python scripts/tune_player_props.py

Grid over the usage half-life, the prior-season weight and the starting-QB
attempt floor. Each candidate is scored by its mean MAE ratio to the naive
projection (a player's own weighted average) across stat/position cells, on the
fit season; the winner and the shipped setting are then both scored on the
untouched test season, so the report says whether the change holds out.
"""

from __future__ import annotations

import itertools
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fit_player_props as harness  # noqa: E402

from cfbmodel import player_props as pp  # noqa: E402
from cfbmodel.sources import espn_box  # noqa: E402

GRID = {
    "PLAYER_HALF_LIFE": (2.0, 3.0, 4.0, 6.0),
    "PRIOR_SEASON_PLAYER_WEIGHT": (0.10, 0.25, 0.50),
    "QB_ATTEMPT_FLOOR": (0.80, 0.88, 0.94),
}


# The harness's naive baseline reads pp.PLAYER_HALF_LIFE, which this grid moves.
# Scoring a ratio against a baseline that changes with the candidate rewarded
# settings that were worse in absolute error (first run, 2026-10-02), so the
# baseline is pinned and candidates are ranked on absolute MAE ratios to it.
NAIVE_HALF_LIFE = 3.0


def _naive(hist, team_id, athlete_id, season, week, stat):
    held = pp.PLAYER_HALF_LIFE
    pp.PLAYER_HALF_LIFE = NAIVE_HALF_LIFE
    try:
        return _harness_naive(hist, team_id, athlete_id, season, week, stat)
    finally:
        pp.PLAYER_HALF_LIFE = held


_harness_naive = harness.naive
harness.naive = _naive


def score(hist, season: int) -> tuple[float, dict]:
    rows, _, _ = harness.player_rows(hist, season)
    cells = {}
    for stat in harness.SIZE_BUCKETS:
        for pos in ("QB", "RB", "WR"):
            sel = [r for r in rows if r["stat"] == stat and r["naive"] is not None
                   and (r["pos"] == pos or (pos == "WR" and r["pos"] == "TE"))]
            if len(sel) < 50:
                continue
            model = statistics.fmean(abs(r["actual"] - r["mean"]) for r in sel)
            naive = statistics.fmean(abs(r["actual"] - r["naive"]) for r in sel)
            cells[f"{stat}:{pos}"] = {"n": len(sel), "model": round(model, 3),
                                      "naive": round(naive, 3)}
    ratio = statistics.fmean(c["model"] / c["naive"] for c in cells.values())
    return ratio, cells


def main() -> int:
    boxes = []
    for season in (harness.FIT - 1, harness.FIT, harness.TEST):
        season_boxes = espn_box.season_boxes(season)
        if season in (harness.FIT, harness.TEST):
            harness.attach_lines(season_boxes, season)
        boxes += season_boxes
        print(f"{season}: {len(season_boxes)} boxes", flush=True)
    hist = pp.History(boxes)
    shipped = {k: getattr(pp, k) for k in GRID}

    results = []
    for values in itertools.product(*GRID.values()):
        setting = dict(zip(GRID, values))
        for k, v in setting.items():
            setattr(pp, k, v)
        ratio, _ = score(hist, harness.FIT)
        results.append((ratio, setting))
        print(f"fit {harness.FIT} {setting}: {ratio:.4f}", flush=True)
    results.sort(key=lambda r: r[0])
    best = results[0][1]

    report = {"grid": GRID, "fit": harness.FIT, "test": harness.TEST,
              "shipped": shipped, "chosen": best}
    for label, setting in (("shipped", shipped), ("chosen", best)):
        for k, v in setting.items():
            setattr(pp, k, v)
        ratio, cells = score(hist, harness.TEST)
        report[f"test_{label}"] = {"mean_ratio_to_naive": round(ratio, 4), "cells": cells}
        print(f"test {harness.TEST} {label} {setting}: {ratio:.4f}", flush=True)
    out = Path("reports/player_props_tuning.json")
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
