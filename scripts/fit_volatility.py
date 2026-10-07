"""Fit the team volatility ranking against the production forecast path.

Rebuilds every historical week exactly as `site.build` does -- ratings, opponent-
adjusted form, venue home field, tier term, scoring prior -- strictly from games
before that week, and records each FBS-vs-FBS game's model margin and total next
to what happened. Closed seasons come from CFBD's permanent cache, so assembly is
paid for once:

    python scripts/fit_volatility.py assemble --seasons 2021-2025
    python scripts/fit_volatility.py fit

`fit` measures, per market, whether a team's miss size against the model is a
trait (it carries from one half-season to the other, and into the next season)
or noise, and writes the shrinkage that `volatility.py` applies in production
to `reports/volatility_fit.json` and `src/cfbmodel/volatility_fit.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cfbmodel import (authority, cli, forecast, ratings, tiers, volatility,  # noqa: E402
                      volatility_data)
from cfbmodel.sources import cfbd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "audit" / "volatility_games.json"
REPORT = ROOT / "reports" / "volatility_fit.json"
MODULE = ROOT / "src" / "cfbmodel" / "volatility_fit.py"


def assemble(seasons: list[int], last_week: int) -> list[dict]:
    auth = authority.current()
    rows: list[dict] = []
    for season in seasons:
        prior_totals = cli._preseason_totals(season)
        for week in range(1, last_week + 1):
            try:
                table = cli.build_ratings(season, week)
                forms = cli._forms(season, week)
                market, _ = cli._markets(cli.consensus_lines(season, week))
                current_games = cli._current_season_games(season, week)
                # This week's own box stats: what each game's process implied.
                process = volatility_data.process_index(
                    cfbd.game_advanced_stats(season, week=week), season)
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
                f = forecast.game(
                    home=home, away=away, team_ratings=table, neutral=neutral,
                    home_form=forms.get(home), away_form=forms.get(away),
                    market_margin=market.get((home, away)),
                    preseason_total=cli._total_prior(home, away, week, prior_totals,
                                                     current_games),
                    authority=auth, week=week, season=season, simulations=0,
                    home_field=cli._home_field(venues, home, away, neutral=neutral,
                                               venue_id=cli._venue_id(g)),
                    tier_orientation=tiers.orientation(
                        home, g.get("homeConference"), away, g.get("awayConference"),
                        season),
                )
                if f.model_margin is None:
                    continue
                rows.append({
                    "season": season, "week": week, "home": home, "away": away,
                    "model_margin": f.model_margin,
                    "model_total": f.independent_total,
                    "actual_margin": float(hp - ap),
                    "actual_total": float(hp + ap),
                    "home_process": process.get((season, week, home, away)),
                    "away_process": process.get((season, week, away, home)),
                })
                n += 1
            print(f"{season} week {week}: {n} games", flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("assemble")
    a.add_argument("--seasons", default="2021-2025")
    a.add_argument("--last-week", type=int, default=15)
    sub.add_parser("fit")
    args = ap.parse_args()

    if args.cmd == "assemble":
        rows = assemble(cli._parse_seasons(args.seasons), args.last_week)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(rows), encoding="utf-8")
        print(f"wrote {CACHE} ({len(rows)} games)")
        return 0

    rows = json.loads(CACHE.read_text(encoding="utf-8"))
    fields = set(volatility.GradedGame.__dataclass_fields__)
    games = [volatility.GradedGame(**{k: v for k, v in row.items() if k in fields})
             for row in rows]
    seasons = sorted({g.season for g in games})
    result = volatility.fit(
        games, margin_sd=ratings.MARGIN_SD,
        source=f"walk-forward replay of the production path, CFBD {seasons[0]}-{seasons[-1]}",
        process_features=volatility_data.PROCESS_FEATURES,
        consistency_stats=volatility_data.CONSISTENCY_STATS,
    )
    REPORT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    volatility.write_params_module(result, MODULE)
    print("\n".join(volatility.summary(result)))
    print(f"  wrote {REPORT} and {MODULE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
