"""Audit the current CFB season with pre-week features and a later-week test.

Research dependencies: numpy. Production does not depend on this script.
The fixed candidate set is deliberately small; publication requires measured
improvement on later weeks, not a fitted explanation of games already played.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
from dataclasses import asdict
from pathlib import Path

from cfbmodel import authority, cli, forecast, matrix, ratings, tiers
from cfbmodel.sources import cfbd


def collect(season: int, through_week: int) -> list[dict]:
    prior = cli._preseason_totals(season)
    all_games = [g for w in range(1, through_week + 1) for g in cfbd.games(season, week=w)]
    venues = cli._venue_context(season, all_games)
    metadata = cli.teams.load(season)
    rows = []
    for week in range(5, through_week + 1):
        table = cli.build_rating_bundle(season, week).table
        forms = cli._forms(season, week)
        current = cli._current_season_games(season, week)
        for game in [g for g in all_games if g.get("week") == week]:
            if (game.get("homeClassification") != "fbs" or game.get("awayClassification") != "fbs"
                    or game.get("homePoints") is None or game.get("awayPoints") is None):
                continue
            home, away = game["homeTeam"], game["awayTeam"]
            hf, af = forms.get(home), forms.get(away)
            if not hf or not af or not hf.complete() or not af.complete():
                continue
            neutral = bool(game.get("neutralSite"))
            hfa = cli._home_field(venues, home, away, neutral=neutral, venue_id=cli._venue_id(game))
            base = ratings.projected_margin(table, home, away, neutral=neutral, home_field=hfa)
            if base is None:
                continue
            result = forecast.game(
                home=home, away=away, team_ratings=table, neutral=neutral,
                home_form=hf, away_form=af, week=week, season=season, simulations=0,
                home_field=hfa, authority=authority.current(),
                preseason_total=cli._total_prior(home, away, week, prior, current),
                tier_orientation=tiers.orientation(home, game.get("homeConference"),
                                                   away, game.get("awayConference"), season),
            )
            if result.model_margin is None:
                continue
            rows.append({
                "season": season, "week": week, "home": home, "away": away,
                "kickoff": game.get("startDate"), "feature_cutoff_week": week - 1,
                "actual_margin": game["homePoints"] - game["awayPoints"],
                "actual_total": game["homePoints"] + game["awayPoints"],
                "baseline_margin": result.model_margin, "baseline_total": result.independent_total,
                "matrix_features": [1.0, base, *[getattr(hf, f) - getattr(af, f) for f in matrix.TeamForm.FIELDS]],
                "home_form": asdict(hf), "away_form": asdict(af),
                "neutral": neutral, "home_field": hfa,
                "home_conference": metadata[home].conference if home in metadata else None,
                "away_conference": metadata[away].conference if away in metadata else None,
            })
        print(f"Week {week}: {len(rows)} cumulative pre-week observations", flush=True)
    return rows


def metrics(actual, predicted) -> dict:
    errors = [float(p) - float(a) for a, p in zip(actual, predicted)]
    if not errors:
        return {"n": 0}
    mse = sum(e * e for e in errors) / len(errors)
    return {"n": len(errors), "mae": sum(abs(e) for e in errors) / len(errors),
            "rmse": math.sqrt(mse), "mse": mse, "bias": sum(errors) / len(errors)}


def fit(rows: list[dict], kind: str) -> list[float]:
    import numpy as np

    target = "total" if kind == "total_bias" else "margin"
    eligible = [r for r in rows if r["baseline_" + target] is not None]
    if kind == "matrix_delta":
        x = np.asarray([r["matrix_features"] for r in eligible], dtype=float)
    elif kind == "margin_scale":
        x = np.asarray([[1, r["baseline_margin"]] for r in eligible], dtype=float)
    else:
        x = np.ones((len(eligible), 1))
    y = np.asarray([r["actual_" + target] - r["baseline_" + target] for r in eligible])
    scale = np.std(x, axis=0)
    scale[scale < 1e-9] = 1
    z = x / scale
    # A fixed 100-observation ridge prior toward the shipped specification.
    delta = np.linalg.solve(z.T @ z + 100 * np.eye(x.shape[1]), z.T @ y) / scale
    return delta.tolist()


def predict(row: dict, kind: str, coefficients: list[float]) -> float:
    target = "total" if kind == "total_bias" else "margin"
    features = row["matrix_features"] if kind == "matrix_delta" else (
        [1, row["baseline_margin"]] if kind == "margin_scale" else [1])
    return row["baseline_" + target] + sum(a * b for a, b in zip(features, coefficients))


def evaluate(rows: list[dict], train_through: int) -> dict:
    train = [r for r in rows if r["week"] <= train_through]
    test = [r for r in rows if r["week"] > train_through]
    decisions = {}
    for kind in ("matrix_delta", "margin_scale", "total_bias"):
        target = "total" if kind == "total_bias" else "margin"
        tr = [r for r in train if r["baseline_" + target] is not None]
        te = [r for r in test if r["baseline_" + target] is not None]
        coefficients = fit(tr, kind)
        baseline = metrics([r["actual_" + target] for r in te], [r["baseline_" + target] for r in te])
        revised = metrics([r["actual_" + target] for r in te], [predict(r, kind, coefficients) for r in te])
        halves = []
        midpoint = (min(r["week"] for r in te) + max(r["week"] for r in te)) // 2
        for sample in ([r for r in te if r["week"] <= midpoint], [r for r in te if r["week"] > midpoint]):
            before = metrics([r["actual_" + target] for r in sample], [r["baseline_" + target] for r in sample])
            after = metrics([r["actual_" + target] for r in sample], [predict(r, kind, coefficients) for r in sample])
            halves.append(1 - after["mae"] / before["mae"])
        mae_gain = 1 - revised["mae"] / baseline["mae"]
        mse_gain = 1 - revised["mse"] / baseline["mse"]
        signs_ok = True
        if kind == "matrix_delta":
            old = [matrix.COEFFICIENTS["intercept"], matrix.COEFFICIENTS["rating_margin"],
                   *[matrix.COEFFICIENTS[f] for f in matrix.TeamForm.FIELDS]]
            signs_ok = all((old[i] + coefficients[i]) * old[i] > 0 for i in range(1, len(old)))
        accepted = len(te) >= 150 and mae_gain >= .005 and mse_gain >= .01 and min(halves) >= -.01 and signs_ok
        final = fit([r for r in rows if r["baseline_" + target] is not None], kind)
        decisions[kind] = {"accepted": accepted, "train_n": len(tr), "validation_n": len(te),
                           "baseline": baseline, "candidate": revised, "mae_improvement": mae_gain,
                           "mse_improvement": mse_gain, "half_mae_improvements": halves,
                           "signs_valid": signs_ok, "selection_coefficients": coefficients,
                           "refit_coefficients": final}
    margin = [k for k in ("matrix_delta", "margin_scale") if decisions[k]["accepted"]]
    winner = min(margin, key=lambda k: decisions[k]["candidate"]["mae"]) if margin else None
    return {"protocol": "2026 weeks 5-7 train; weeks 8-11 validate; fixed ridge=100; no current-week features",
            "acceptance": "validation MAE +0.5%, MSE +1%, neither half MAE worse >1%, n>=150, football coefficient signs",
            "train_through_week": train_through, "test_through_week": max(r["week"] for r in rows),
            "observations": len(rows), "decisions": decisions, "margin_candidate": winner,
            "total_candidate": "total_bias" if decisions["total_bias"]["accepted"] else None,
            "per_week_baseline": {str(w): metrics([r["actual_margin"] for r in rows if r["week"] == w],
                                                 [r["baseline_margin"] for r in rows if r["week"] == w])
                                  for w in sorted({r["week"] for r in rows})}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--through-week", type=int, default=11)
    parser.add_argument("--train-through", type=int, default=7)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--key-file", type=Path)
    args = parser.parse_args()
    if args.key_file:
        for line in args.key_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("CFBD_API_KEY="):
                os.environ["CFBD_API_KEY"] = line.split("=", 1)[1].strip().strip("\"'")
    args.out.mkdir(parents=True, exist_ok=True)
    rows = collect(args.season, args.through_week)
    (args.out / "matrix-features.json.gz").write_bytes(gzip.compress(json.dumps(rows).encode()))
    report = evaluate(rows, args.train_through)
    report["sources"] = cfbd.status_report()
    (args.out / "matrix-audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("sources", "per_week_baseline")}, indent=2))


if __name__ == "__main__":
    main()
