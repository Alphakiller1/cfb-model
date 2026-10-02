"""Walk-forward audit of the conference-tier bias, on the production forecast path.

The 2026 shadow record shows the largest model-vs-market gap in Power-4 vs
Group-of-5 games (model MAE 16.3 against the book's 11.2). The baseline report
named the mechanism - sparse cross-tier scheduling leaves the two sub-graphs of
the rating solve weakly connected - and listed a tier term as the top open item.

This rebuilds every historical week exactly as `site.build` does (ratings,
opponent-adjusted form, venue home field, consensus market) but strictly from
games before the week, records each FBS-vs-FBS game's tiers, and caches the
observations so candidate corrections can be scored without re-fetching:

    python scripts/audit_tiers.py assemble --seasons 2021-2025
    python scripts/audit_tiers.py fit

Closed seasons go to CFBD's permanent cache, so assembly is paid for once.
"""

from __future__ import annotations

import argparse
import json
import pickle
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cfbmodel import authority, cli, forecast, ratings  # noqa: E402
from cfbmodel.sources import cfbd  # noqa: E402

CACHE = Path(__file__).resolve().parent.parent / "data" / "audit" / "tier_observations.pkl"
P4 = {"SEC", "Big Ten", "Big 12", "ACC", "Pac-12"}
INDEPENDENT_P4 = {"Notre Dame"}


@dataclass(frozen=True)
class Obs:
    season: int
    week: int
    home: str
    away: str
    home_conf: str | None
    away_conf: str | None
    neutral: bool
    model_margin: float
    rating_margin: float          # ratings-only margin incl. home field
    used_efficiency: bool
    market_margin: float | None
    actual_margin: float


def tier(team: str, conference: str | None, season: int) -> str:
    if team in INDEPENDENT_P4:
        return "P4"
    if conference == "Pac-12" and season >= 2024:
        return "G5"           # the two-team remnant is not a power conference
    return "P4" if conference in P4 else "G5"


def _seasons(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def assemble(seasons: list[int], last_week: int) -> list[Obs]:
    auth = authority.current()
    rows: list[Obs] = []
    for season in seasons:
        for week in range(1, last_week + 1):
            try:
                table = cli.build_ratings(season, week)
                forms = cli._forms(season, week)
                market, _ = cli._markets(cli.consensus_lines(season, week))
                season_games: list[dict] = []
                for w in range(1, week + 1):
                    season_games.extend(cfbd.games(season, week=w))
                venues = cli._venue_context(season, season_games)
            except Exception as exc:  # one failed week must not erase the audit
                print(f"skip {season} week {week}: {type(exc).__name__}: {exc}", flush=True)
                continue
            n = 0
            for g in season_games:
                if g.get("week") != week:
                    continue
                if not (g.get("homeClassification") == "fbs"
                        and g.get("awayClassification") == "fbs"):
                    continue
                hp, ap = g.get("homePoints"), g.get("awayPoints")
                if hp is None or ap is None:
                    continue
                home, away = g["homeTeam"], g["awayTeam"]
                neutral = bool(g.get("neutralSite"))
                home_field = cli._home_field(venues, home, away, neutral=neutral,
                                             venue_id=cli._venue_id(g))
                f = forecast.game(
                    home=home, away=away, team_ratings=table, neutral=neutral,
                    home_form=forms.get(home), away_form=forms.get(away),
                    market_margin=market.get((home, away)), authority=auth,
                    week=week, season=season, home_field=home_field, simulations=0,
                )
                base = ratings.projected_margin(table, home, away, neutral=neutral,
                                                home_field=home_field)
                if f.model_margin is None or base is None:
                    continue
                rows.append(Obs(season, week, home, away, g.get("homeConference"),
                                g.get("awayConference"), neutral, f.model_margin, base,
                                f.used_efficiency, market.get((home, away)),
                                float(hp - ap)))
                n += 1
            print(f"{season} week {week}: {n} games", flush=True)
    return rows


# -- fitting ------------------------------------------------------------------
def _orient(o: Obs) -> int:
    """+1 when the home side is the P4 team in a P4-vs-G5 game, -1 when the away
    side is, 0 otherwise."""
    h, a = tier(o.home, o.home_conf, o.season), tier(o.away, o.away_conf, o.season)
    if h == a:
        return 0
    return 1 if h == "P4" else -1


def _mae(xs):
    return statistics.fmean(abs(x) for x in xs) if xs else float("nan")


def fit(rows: list[Obs]) -> dict:
    """Leave-one-season-out: fit a P4-vs-G5 shift on four seasons, score the fifth.

    The candidate is one number - points added toward the P4 side in cross-tier
    games - fitted separately for the early (weeks 1-4) and validated regimes.
    """
    seasons = sorted({o.season for o in rows})
    report: dict = {"seasons": seasons, "folds": [], "segments": {}}
    for label, keep in (("early", lambda o: o.week < forecast.FIRST_VALIDATED_WEEK),
                        ("validated", lambda o: o.week >= forecast.FIRST_VALIDATED_WEEK)):
        part = [o for o in rows if keep(o)]
        cross = [o for o in part if _orient(o)]
        report["segments"][label] = {
            "games": len(part), "cross_tier": len(cross),
            "cross_tier_mean_residual_toward_p4": round(statistics.fmean(
                (o.actual_margin - o.model_margin) * _orient(o) for o in cross), 3)
            if cross else None,
        }
        base_err, fit_err, market_err = [], [], []
        shifts = []
        for held in seasons:
            train = [o for o in cross if o.season != held]
            if not train:
                continue
            shift = statistics.fmean((o.actual_margin - o.model_margin) * _orient(o)
                                     for o in train)
            shifts.append(shift)
            for o in part:
                if o.season != held:
                    continue
                adjusted = o.model_margin + shift * _orient(o)
                base_err.append(o.actual_margin - o.model_margin)
                fit_err.append(o.actual_margin - adjusted)
                if o.market_margin is not None:
                    market_err.append(o.actual_margin - o.market_margin)
            report["folds"].append({"regime": label, "held_out": held,
                                    "shift": round(shift, 3)})
        report["segments"][label].update({
            "mae_current": round(_mae(base_err), 4),
            "mae_with_tier_shift": round(_mae(fit_err), 4),
            "mae_market": round(_mae(market_err), 4),
            "shift_mean": round(statistics.fmean(shifts), 3) if shifts else None,
            "shift_sd": round(statistics.pstdev(shifts), 3) if len(shifts) > 1 else None,
            "full_sample_shift": round(statistics.fmean(
                (o.actual_margin - o.model_margin) * _orient(o) for o in cross), 3)
            if cross else None,
        })
    return report


def _ols(X: list[list[float]], y: list[float]) -> list[float]:
    """Least squares via normal equations (tiny k, stdlib only)."""
    k = len(X[0])
    a = [[sum(r[i] * r[j] for r in X) for j in range(k)] for i in range(k)]
    b = [sum(r[i] * t for r, t in zip(X, y)) for i in range(k)]
    for col in range(k):                      # Gauss-Jordan
        piv = max(range(col, k), key=lambda r: abs(a[r][col]))
        a[col], a[piv], b[col], b[piv] = a[piv], a[col], b[piv], b[col]
        for r in range(k):
            if r != col and a[col][col]:
                f = a[r][col] / a[col][col]
                a[r] = [x - f * y_ for x, y_ in zip(a[r], a[col])]
                b[r] -= f * b[col]
    return [b[i] / a[i][i] for i in range(k)]


CANDIDATES = {
    "current": [],
    "intercept": ["one"],
    "tier": ["tier"],
    "intercept+tier": ["one", "tier"],
    "scale+intercept+tier": ["one", "model", "tier"],
    "home+tier": ["home", "tier"],
}


def _features(o: Obs, names: list[str]) -> list[float]:
    values = {"one": 1.0, "model": o.model_margin, "tier": float(_orient(o)),
              "home": 0.0 if o.neutral else 1.0}
    return [values[n] for n in names]


def joint(rows: list[Obs]) -> dict:
    """Leave-one-season-out over candidate corrections of the residual.

    Each candidate models (actual - model) [or actual, when "model" is in it]
    as a linear function of its features, fitted per regime on four seasons
    and scored on the fifth. Only a candidate that lowers held-out MAE in a
    regime is worth adopting there.
    """
    seasons = sorted({o.season for o in rows})
    out: dict = {}
    for label, keep in (("early", lambda o: o.week < forecast.FIRST_VALIDATED_WEEK),
                        ("validated", lambda o: o.week >= forecast.FIRST_VALIDATED_WEEK)):
        part = [o for o in rows if keep(o)]
        res: dict = {}
        for name, feats in CANDIDATES.items():
            errors, coefs = [], []
            for held in seasons:
                train = [o for o in part if o.season != held]
                test = [o for o in part if o.season == held]
                if not feats:
                    errors += [o.actual_margin - o.model_margin for o in test]
                    continue
                if "model" in feats:
                    X = [_features(o, feats) for o in train]
                    beta = _ols(X, [o.actual_margin for o in train])
                    pred = [sum(b * x for b, x in zip(beta, _features(o, feats))) for o in test]
                else:
                    X = [_features(o, feats) for o in train]
                    beta = _ols(X, [o.actual_margin - o.model_margin for o in train])
                    pred = [o.model_margin + sum(b * x for b, x in zip(beta, _features(o, feats)))
                            for o in test]
                coefs.append(beta)
                errors += [o.actual_margin - p for o, p in zip(test, pred)]
            res[name] = {"mae": round(_mae(errors), 4)}
            if coefs:
                res[name]["coef_mean"] = [round(statistics.fmean(c[i] for c in coefs), 3)
                                          for i in range(len(feats))]
                res[name]["coef_sd"] = [round(statistics.pstdev([c[i] for c in coefs]), 3)
                                        for i in range(len(feats))]
                res[name]["features"] = feats
        market = [o.actual_margin - o.market_margin for o in part if o.market_margin is not None]
        res["market"] = {"mae": round(_mae(market), 4), "n": len(market)}
        res["n"] = len(part)
        out[label] = res
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("assemble")
    a.add_argument("--seasons", default="2021-2025")
    a.add_argument("--last-week", type=int, default=14)
    sub.add_parser("fit")
    args = ap.parse_args()
    if args.cmd == "assemble":
        existing: list[Obs] = pickle.loads(CACHE.read_bytes()) if CACHE.is_file() else []
        wanted = _seasons(args.seasons)
        kept = [o for o in existing if o.season not in wanted]
        rows = kept + assemble(wanted, args.last_week)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_bytes(pickle.dumps(rows))
        print(f"cached {len(rows)} observations -> {CACHE}")
        return 0
    rows = pickle.loads(CACHE.read_bytes())
    print(json.dumps({"tier_shift": fit(rows), "joint": joint(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
