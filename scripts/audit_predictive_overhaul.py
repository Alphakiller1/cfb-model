"""Nested leave-one-season-out audit for predictive forecast composition.

This is intentionally a branch/research tool.  It asks the practical question
the production board needs answered: when a market estimate and an independent
estimate are both available, how much independent disagreement survives a
season-held-out test?  The blend weight is selected on the training seasons and
then scored on the untouched held-out season.

The same procedure is run for margins and totals, both globally and for the
season-opening (weeks 1-4) and mature (weeks 5+) regimes.  A small OLS ensemble
is included as a diagnostic, but no coefficient is promoted automatically.
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path

from cfbmodel import forecast, fitting, totals
from cfbmodel.authority import current
from cfbmodel.cli import (
    _forms,
    _markets,
    _parse_seasons,
    _preseason_totals,
    build_ratings,
)
from cfbmodel.sources import cfbd


@dataclass(frozen=True)
class Observation:
    season: int
    week: int
    actual_margin: float
    model_margin: float
    market_margin: float
    actual_total: float
    model_total: float | None
    market_total: float | None
    used_efficiency: bool


@dataclass(frozen=True)
class Fold:
    held_out: int
    n: int
    selected_weight: float
    mae: float
    market_mae: float

    @property
    def delta_vs_market(self) -> float:
        return self.mae - self.market_mae


def _mae(predicted: list[float], actual: list[float]) -> float:
    return statistics.fmean(abs(p - a) for p, a in zip(predicted, actual))


def _blend(market: float, model: float, weight: float) -> float:
    """Market plus a bounded fraction of the independent disagreement."""
    return market + weight * (model - market)


def _eligible(rows: list[Observation], target: str) -> list[Observation]:
    if target == "margin":
        return rows
    return [r for r in rows if r.model_total is not None and r.market_total is not None]


def _values(row: Observation, target: str) -> tuple[float, float, float]:
    if target == "margin":
        return row.market_margin, row.model_margin, row.actual_margin
    assert row.model_total is not None and row.market_total is not None
    return row.market_total, row.model_total, row.actual_total


def nested_blend(rows: list[Observation], target: str) -> dict:
    """Choose lambda on training seasons; score it on the held-out season."""
    rows = _eligible(rows, target)
    seasons = sorted({r.season for r in rows})
    grid = [i / 40 for i in range(41)]
    folds: list[Fold] = []
    errors: list[float] = []
    for held_out in seasons:
        train = [r for r in rows if r.season != held_out]
        test = [r for r in rows if r.season == held_out]
        if not train or not test:
            continue
        scored = []
        for weight in grid:
            predictions = []
            actual = []
            for row in train:
                market, model, result = _values(row, target)
                predictions.append(_blend(market, model, weight))
                actual.append(result)
            scored.append((_mae(predictions, actual), weight))
        _, selected = min(scored)
        fold_errors = []
        fold_market_errors = []
        for row in test:
            market, model, result = _values(row, target)
            error = abs(_blend(market, model, selected) - result)
            fold_errors.append(error)
            fold_market_errors.append(abs(market - result))
            errors.append(error)
        folds.append(Fold(
            held_out,
            len(test),
            selected,
            statistics.fmean(fold_errors),
            statistics.fmean(fold_market_errors),
        ))
    return {
        "n": len(errors),
        "mae": statistics.fmean(errors) if errors else None,
        "mean_selected_weight": (
            statistics.fmean(f.selected_weight for f in folds) if folds else None
        ),
        "folds_better_than_market": sum(f.delta_vs_market < 0 for f in folds),
        "folds": [{**asdict(f), "delta_vs_market": f.delta_vs_market} for f in folds],
    }


def loso_ols(rows: list[Observation], target: str) -> dict:
    """Held-out score for actual ~ market + independent model."""
    rows = _eligible(rows, target)
    seasons = sorted({r.season for r in rows})
    errors: list[float] = []
    coefficients: list[dict[str, float]] = []
    for held_out in seasons:
        train = [r for r in rows if r.season != held_out]
        test = [r for r in rows if r.season == held_out]
        train_x, train_y = [], []
        for row in train:
            market, model, result = _values(row, target)
            train_x.append({"market": market, "model": model})
            train_y.append(result)
        fitted = fitting.ols(train_x, train_y, ["market", "model"], ridge=1e-4)
        coefficients.append(fitted)
        for row in test:
            market, model, result = _values(row, target)
            errors.append(abs(fitting.predict(
                fitted, {"market": market, "model": model}
            ) - result))
    keys = ("intercept", "market", "model")
    return {
        "n": len(errors),
        "mae": statistics.fmean(errors) if errors else None,
        "mean_coefficients": {
            key: statistics.fmean(c[key] for c in coefficients)
            for key in keys
        } if coefficients else {},
        "coefficient_sd": {
            key: statistics.pstdev(c[key] for c in coefficients)
            for key in keys
        } if coefficients else {},
    }


def baselines(rows: list[Observation], target: str) -> dict:
    rows = _eligible(rows, target)
    market, model, actual = [], [], []
    for row in rows:
        mkt, mdl, result = _values(row, target)
        market.append(mkt)
        model.append(mdl)
        actual.append(result)
    return {
        "n": len(rows),
        "market_mae": _mae(market, actual),
        "independent_model_mae": _mae(model, actual),
    }


def evaluate(rows: list[Observation], label: str) -> dict:
    out = {"label": label, "seasons": sorted({r.season for r in rows})}
    for target in ("margin", "total"):
        base = baselines(rows, target)
        base["nested_bounded_blend"] = nested_blend(rows, target)
        base["loso_ols_ensemble"] = loso_ols(rows, target)
        out[target] = base
    out["form_coverage"] = {
        "games": len(rows),
        "used_efficiency": sum(r.used_efficiency for r in rows),
    }
    return out


def observations(seasons: list[int], last_week: int) -> list[Observation]:
    rows: list[Observation] = []
    auth = current()
    for season in seasons:
        preseason_totals = _preseason_totals(season)
        for week in range(1, last_week + 1):
            print(f"assemble {season} week {week}", flush=True)
            try:
                rating_table = build_ratings(season, week)
                forms = _forms(season, week)
                market_rows = cfbd.lines(season, week=week)
                margins, market_totals = _markets(market_rows)
                games = cfbd.games(season, week=week, completed_only=True)
            except Exception as exc:  # one failed week should not erase the audit
                print(f"skip {season} week {week}: {exc}", flush=True)
                continue
            for game in games:
                if not (game.get("homeClassification") == "fbs"
                        and game.get("awayClassification") == "fbs"):
                    continue
                hp, ap = game.get("homePoints"), game.get("awayPoints")
                if hp is None or ap is None:
                    continue
                home, away = game["homeTeam"], game["awayTeam"]
                key = (home, away)
                if key not in margins:
                    continue
                result = forecast.game(
                    home=home,
                    away=away,
                    team_ratings=rating_table,
                    neutral=bool(game.get("neutralSite")),
                    home_form=forms.get(home),
                    away_form=forms.get(away),
                    market_margin=margins[key],
                    market_total=market_totals.get(key),
                    preseason_total=totals.preseason_total(home, away, preseason_totals),
                    authority=auth,
                    week=week,
                )
                if result.model_margin is None:
                    continue
                rows.append(Observation(
                    season=season,
                    week=week,
                    actual_margin=float(hp - ap),
                    model_margin=result.model_margin,
                    market_margin=margins[key],
                    actual_total=float(hp + ap),
                    model_total=result.independent_total,
                    market_total=market_totals.get(key),
                    used_efficiency=result.used_efficiency,
                ))
    return rows


def _summary(report: dict) -> None:
    print("\nPredictive audit (all scores season-held-out where fitted)")
    for section in report["sections"]:
        print(f"\n{section['label']}")
        for target in ("margin", "total"):
            data = section[target]
            blend = data["nested_bounded_blend"]
            ols = data["loso_ols_ensemble"]
            print(
                f"  {target:6} n={data['n']:4} market={data['market_mae']:.4f} "
                f"independent={data['independent_model_mae']:.4f} "
                f"blend={blend['mae']:.4f} lambda={blend['mean_selected_weight']:.3f} "
                f"ols={ols['mae']:.4f}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", default="2021-2025")
    parser.add_argument("--last-week", type=int, default=14)
    parser.add_argument("--out", default="_site/research.json")
    args = parser.parse_args()

    rows = observations(_parse_seasons(args.seasons), args.last_week)
    if not rows:
        raise SystemExit("no observations assembled")
    sections = [
        evaluate(rows, "all weeks"),
        evaluate([r for r in rows if r.week <= 4], "weeks 1-4"),
        evaluate([r for r in rows if r.week >= 5], "weeks 5+"),
        *[
            evaluate([r for r in rows if r.week == week], f"week {week}")
            for week in range(1, 5)
        ],
    ]
    report = {
        "schema": "cfb-model/predictive-audit/1",
        "method": "leave one season out; blend lambda selected on training seasons",
        "observations": len(rows),
        "sections": sections,
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _summary(report)
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
