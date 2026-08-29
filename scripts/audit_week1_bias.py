"""Reproducible Week 1 favorite/underdog calibration audit.

Fits every candidate without the season being scored. This is intentionally a
research script rather than board logic: no correction is adopted merely
because it makes the current slate look more balanced.
"""

from __future__ import annotations

import statistics

from cfbmodel import fitting, preseason, ratings
from cfbmodel import forecast as fc
from cfbmodel.authority import current
from cfbmodel.cli import (_market, _parse_seasons, _prior_season, _to_games)
from cfbmodel.sources import cfbd


SEASONS = _parse_seasons("2019,2021-2025")


def _observations() -> list[tuple[int, dict[str, float], float]]:
    rows: list[tuple[int, dict[str, float], float]] = []
    for season in SEASONS:
        prior_season = _prior_season(season)
        prior2_season = _prior_season(prior_season)
        prior = ratings.build(_to_games(cfbd.games(prior_season, completed_only=True)))
        prior2 = ratings.build(_to_games(cfbd.games(prior2_season, completed_only=True)))
        extras = preseason.roster_features(season)
        components = preseason.components(season, prior, prior2, extras)
        rating_table = {team: component.rating for team, component in components.items()}
        market = _market(season, 1)
        games = [
            g for g in cfbd.games(season, week=1, completed_only=True)
            if g.get("homeClassification") == "fbs"
            and g.get("awayClassification") == "fbs"
        ]
        for game in games:
            home, away = game["homeTeam"], game["awayTeam"]
            line = market.get((home, away))
            if line is None:
                continue
            forecast = fc.game(
                home=home, away=away, team_ratings=rating_table,
                neutral=bool(game.get("neutralSite")), market_margin=line,
                authority=current(), week=1,
            )
            if forecast.model_margin is None:
                continue
            raw = forecast.raw_model_margin
            hc, ac = components[home], components[away]
            row = {
                "raw": raw,
                "signed_square": raw * abs(raw),
                "market": line,
                "prior_gap": hc.prior_rating[0] - ac.prior_rating[0],
                "prior2_gap": hc.prior_rating_2[0] - ac.prior_rating_2[0],
                "talent_gap": hc.talent[0] - ac.talent[0],
                "returning_gap": (hc.returning_production[0]
                                  - ac.returning_production[0]),
                "recruiting_gap": (hc.recruiting_points[0]
                                    - ac.recruiting_points[0]),
                "venue": 0.0 if game.get("neutralSite") else 1.0,
            }
            for name in preseason.EXTRA_COEFFICIENTS:
                row[name] = (extras.get(home, {}).get(name, 0.0)
                             - extras.get(away, {}).get(name, 0.0))
            rows.append((season, {
                **row,
            }, float(game["homePoints"] - game["awayPoints"])))
    return rows


def _loso_predictions(observations, names):
    predictions = []
    for held_out in sorted({season for season, _, _ in observations}):
        train = [(x, y) for season, x, y in observations if season != held_out]
        test = [(x, y) for season, x, y in observations if season == held_out]
        coefficients = fitting.ols(
            [x for x, _ in train], [y for _, y in train], names
        )
        for features, actual in test:
            predictions.append((
                fitting.predict(coefficients, features),
                features["market"], actual,
            ))
    return predictions


def _loso_symmetric_predictions(observations, names):
    """Fit neutral margins with mirrored rows, forcing side symmetry."""
    predictions = []
    for held_out in sorted({season for season, _, _ in observations}):
        training_rows, training_targets = [], []
        for season, features, actual in observations:
            if season == held_out:
                continue
            neutral_target = actual - ratings.HOME_FIELD_POINTS * features["venue"]
            row = {name: features[name] for name in names}
            training_rows.extend((row, {name: -value for name, value in row.items()}))
            training_targets.extend((neutral_target, -neutral_target))
        coefficients = fitting.ols(training_rows, training_targets, names)
        for season, features, actual in observations:
            if season != held_out:
                continue
            prediction = (ratings.HOME_FIELD_POINTS * features["venue"]
                          + fitting.predict(coefficients, features))
            predictions.append((prediction, features["market"], actual))
    return predictions


def _score(label, rows):
    errors = [abs(predicted - actual) for predicted, _, actual in rows]
    dog = favorite = win = loss = push = 0
    buckets = {"0-7": [0, 0], "7-14": [0, 0], "14-28": [0, 0], "28+": [0, 0]}
    for predicted, market, actual in rows:
        gap = predicted - market
        if gap == 0 or market == 0:
            continue
        is_dog = gap * market < 0
        dog += is_dog
        favorite += not is_dog
        ats = (actual - market) * (1 if gap > 0 else -1)
        if ats > 0:
            win += 1
        elif ats < 0:
            loss += 1
        else:
            push += 1
        magnitude = abs(market)
        bucket = "0-7" if magnitude <= 7 else "7-14" if magnitude <= 14 else "14-28" if magnitude <= 28 else "28+"
        buckets[bucket][0] += is_dog
        buckets[bucket][1] += 1
    decisions = dog + favorite
    dog_pct = 100 * dog / decisions if decisions else 0.0
    print(f"{label:<30} n={len(rows):>3}  MAE={statistics.fmean(errors):.4f}  "
          f"dog={dog:>3}/{decisions:<3} ({dog_pct:5.1f}%)  "
          f"ATS={win}-{loss}-{push}")
    print(" " * 32 + "  ".join(
        f"{name} {d}/{n}" for name, (d, n) in buckets.items()
    ))


def main() -> None:
    observations = _observations()
    affine_result = fitting.leave_one_season_out(
        "affine(raw)", ["raw"], observations
    )
    market = [(x["market"], x["market"], y) for _, x, y in observations]
    raw = [(x["raw"], x["market"], y) for _, x, y in observations]
    affine = _loso_predictions(observations, ["raw"])
    nonlinear = _loso_predictions(observations, ["raw", "signed_square"])
    hybrid = _loso_predictions(observations, ["market", "raw"])
    hybrid_tail = _loso_predictions(
        observations, ["market", "raw", "signed_square"]
    )
    direct_names = [
        "prior_gap", "prior2_gap", "talent_gap", "returning_gap",
        "recruiting_gap",
    ]
    direct = _loso_symmetric_predictions(observations, direct_names)
    direct_extra_names = direct_names + list(preseason.EXTRA_COEFFICIENTS)
    direct_extra = _loso_symmetric_predictions(observations, direct_extra_names)
    print(f"Week 1 leave-one-season-out audit over {len(observations)} games")
    _score("market", market)
    _score("raw preseason model", raw)
    _score("affine(raw)", affine)
    _score("nonlinear(raw, tail)", nonlinear)
    _score("direct matchup", direct)
    _score("direct + roster/coaching", direct_extra)
    _score("hybrid(market, raw)", hybrid)
    _score("hybrid(+ raw tail)", hybrid_tail)
    print("\nAffine fold coefficients:")
    for fold in affine_result.folds:
        print(f"  held out {fold.held_out}: intercept="
              f"{fold.coefficients['intercept']:+.4f}  "
              f"slope={fold.coefficients['raw']:.4f}  MAE={fold.mae:.4f}")
    pooled = affine_result.pooled_coefficients()
    spread = affine_result.coefficient_spread()
    print(f"  pooled: intercept={pooled['intercept']:+.4f} "
          f"(SD {spread['intercept']:.4f}), slope={pooled['raw']:.4f} "
          f"(SD {spread['raw']:.4f})")
    print("\nDirect matchup fold coefficients:")
    fold_coefficients = []
    for held_out in sorted({season for season, _, _ in observations}):
        train_rows, targets = [], []
        for season, features, actual in observations:
            if season == held_out:
                continue
            y = actual - ratings.HOME_FIELD_POINTS * features["venue"]
            row = {name: features[name] for name in direct_names}
            train_rows.extend((row, {name: -value for name, value in row.items()}))
            targets.extend((y, -y))
        coefficients = fitting.ols(train_rows, targets, direct_names)
        fold_coefficients.append(coefficients)
        print(f"  held out {held_out}: " + ", ".join(
            f"{name}={coefficients[name]:+.5f}" for name in direct_names
        ))
    print("  pooled (fold SD):")
    for name in ["intercept", *direct_names]:
        values = [fold[name] for fold in fold_coefficients]
        print(f"    {name:<18} {statistics.fmean(values):+11.6f}  "
              f"({statistics.pstdev(values):.6f})")


if __name__ == "__main__":
    main()
