"""Replay CFB opportunity changes and fit outcome distributions before 2026."""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

from cfbmodel import cli, teams, player_props as revised
from cfbmodel.sources import cfbd, espn_box, oddsapi


def replay(baseline_path: Path, output: Path) -> list[dict]:
    name = "cfbmodel._baseline_player_props"
    spec = importlib.util.spec_from_file_location(name, baseline_path)
    old = importlib.util.module_from_spec(spec)
    sys.modules[name] = old
    spec.loader.exec_module(old)
    boxes = []
    for season in (2023, 2024, 2025, 2026):
        boxes.extend(espn_box.season_boxes(season, through_week=11 if season == 2026 else 16))
        print("Read boxes through", season, len(boxes), flush=True)
    rows = []
    for season in (2024, 2025, 2026):
        # Historical closing benchmarks are immutable cached data. The current
        # season uses each ESPN box's posted benchmark, without another API call.
        margins, totals = cli._markets(cfbd.lines(season)) if season < 2026 else ({}, {})
        schools = {name: None for pair in margins for name in pair}
        index = oddsapi.build_index(schools)
        history_boxes = [b for b in boxes if season - 1 <= b.season <= season]
        hist, previous = revised.History(history_boxes), old.History(history_boxes)
        for box in [b for b in boxes if b.season == season and b.week >= 2]:
            home = next((t for t in box.teams if t.home), None)
            away = next((t for t in box.teams if not t.home), None)
            if not home or not away:
                continue
            key = (oddsapi.match_team(home.name, index), oddsapi.match_team(away.name, index))
            home_margin = box.home_margin_line if box.home_margin_line is not None else margins.get(key)
            total = box.total_line if box.total_line is not None else totals.get(key)
            if home_margin is None or total is None:
                continue
            for team in box.teams:
                opponent = box.opponent(team.team_id)
                margin = home_margin * (1 if team.home else -1)
                before = old.project_team(previous, team.team_id, opponent, season, box.week,
                                          old.Environment(margin, total))
                after = revised.project_team(hist, team.team_id, opponent, season, box.week,
                                             revised.Environment(margin, total), profiles_enabled=False)
                actual = {p.athlete_id: p for p in box.players if p.team_id == team.team_id}
                before_by_id = {p.athlete_id: p for p in before}
                for player in after:
                    seen = actual.get(player.athlete_id)
                    baseline = before_by_id.get(player.athlete_id)
                    for stat, mean in player.metrics.items():
                        rows.append({"season": season, "week": box.week, "event_id": box.event_id,
                                     "team_id": team.team_id, "player_id": player.athlete_id,
                                     "position": player.position, "stat": stat, "mean": mean,
                                     "baseline": baseline.metrics.get(stat) if baseline else None,
                                     "actual": float(getattr(seen, stat)) if seen else 0.0,
                                     "recorded_stat_line": seen is not None})
        print("Replayed", season, "cumulative stat forecasts", len(rows), flush=True)
    (output / "player-replay.json.gz").write_bytes(gzip.compress(json.dumps(rows).encode()))
    return rows


def fit(rows):
    import numpy as np
    from scipy.special import gammaln

    training = [r for r in rows if r["season"] <= 2025]
    for season in (2024, 2025):
        if sum(r["season"] == season for r in training) < 5000:
            raise ValueError(f"Insufficient historical fitting coverage for {season}")
    parameters = {"version": "cfb-player-outcomes/2.0.0", "projection_version": revised.MODEL_VERSION,
                  "fit_seasons": [2024, 2025], "fit_season_max": 2025, "held_out_season": 2026,
                  "basis": "Zero recorded production retained; participation and sportsbook void probabilities are not inferred",
                  "ratios": {}, "count_shapes": {}, "sample_counts": {}}
    grouped = defaultdict(list)
    for row in training:
        if row["mean"] > 0:
            grouped[row["stat"]].append(row)
    counts = {"pass_att", "pass_cmp", "pass_td", "pass_int", "rush_car", "rec"}
    for stat, values in grouped.items():
        parameters["sample_counts"][stat] = len(values)
        if stat in counts:
            mu = np.asarray([max(.0001, r["mean"]) for r in values])
            y = np.asarray([r["actual"] for r in values])
            candidates = []
            for shape in (1.5, 3, 6, 12, 24, 48, 96, 1000):
                logp = (gammaln(y + shape) - gammaln(shape) - gammaln(y + 1)
                        + shape * np.log(shape / (shape + mu)) + y * np.log(mu / (shape + mu)))
                candidates.append((-float(np.mean(logp)), shape))
            parameters["count_shapes"][stat] = min(candidates)[1]
        else:
            buckets = revised.RATIO_QUANTILES[stat]
            fitted = []
            for index, (upper, _) in enumerate(buckets):
                lower = buckets[index - 1][0] if index else 0
                sample = [r for r in values if lower < r["mean"] <= upper]
                if len(sample) < 150:
                    sample = values
                ratios = np.asarray([r["actual"] / r["mean"] for r in sample])
                fitted.append({"upper": upper, "n": len(sample),
                               "quantiles": np.quantile(ratios, revised.QUANTILE_LEVELS).tolist()})
            parameters["ratios"][stat] = fitted
    return parameters


def point_metrics(rows):
    groups = defaultdict(list)
    for row in rows:
        if row["baseline"] is not None:
            groups[f"{row['season']}:{row['position']}:{row['stat']}"].append(row)
    out = {}
    for key, values in groups.items():
        target = {}
        for name, field in (("baseline", "baseline"), ("revised", "mean")):
            errors = [r[field] - r["actual"] for r in values]
            target[name] = {"n": len(errors), "mae": sum(abs(e) for e in errors) / len(errors),
                            "rmse": math.sqrt(sum(e * e for e in errors) / len(errors)),
                            "bias": sum(errors) / len(errors)}
        out[key] = target
    return out


def interval_metrics(rows, old):
    grouped = defaultdict(list)
    for row in rows:
        if row["season"] == 2026:
            grouped[row["stat"]].append(row)
    result = {}
    for stat, values in grouped.items():
        result[stat] = {"n": len(values)}
        for name, source in (("baseline_ranges", old), ("revised_ranges", revised)):
            covered, widths, scores = [], [], []
            for row in values:
                d = source.distribution(stat, row["mean"])
                low, high = d.get("p10"), d.get("p90")
                if low is None or high is None:
                    continue
                y = row["actual"]
                covered.append(low <= y <= high)
                widths.append(high - low)
                scores.append(high - low + 10 * max(0, low - y) + 10 * max(0, y - high))
            result[stat][name] = {"coverage_80": sum(covered) / len(covered),
                                  "mean_width": sum(widths) / len(widths),
                                  "interval_score": sum(scores) / len(scores)}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--reuse-replay", action="store_true")
    args = parser.parse_args()
    if args.key_file:
        for line in args.key_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("CFBD_API_KEY="):
                os.environ["CFBD_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    args.out.mkdir(parents=True, exist_ok=True)
    rows = json.loads(gzip.decompress((args.out / "player-replay.json.gz").read_bytes())) if args.reuse_replay else replay(args.baseline, args.out)
    parameters = fit(rows)
    destination = Path(revised.__file__).with_name("prop_parameters.json")
    destination.write_text(json.dumps(parameters, indent=2), encoding="utf-8")
    revised.outcome_parameters.cache_clear()
    name = "cfbmodel._baseline_player_props"
    spec = importlib.util.spec_from_file_location(name, args.baseline)
    old = importlib.util.module_from_spec(spec)
    sys.modules[name] = old
    spec.loader.exec_module(old)
    report = {"protocol": "2024-2025 outcome fitting; 2026 excluded from fitting; retrospective inputs, pre-week features",
              "mean_comparison": point_metrics(rows),
              "held_out_intervals": interval_metrics(rows, old),
              "counts": {str(year): sum(r["season"] == year for r in rows) for year in (2024, 2025, 2026)},
              "outcome_parameters": parameters}
    (args.out / "player-audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Wrote fitted parameters and audit", report["counts"], flush=True)


if __name__ == "__main__":
    main()
