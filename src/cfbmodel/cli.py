"""Command line entry point.

    python -m cfbmodel.cli board --season 2026 --week 1
    python -m cfbmodel.cli ratings --season 2025
"""

from __future__ import annotations

import argparse
import statistics

from cfbmodel import forecast as fc
from cfbmodel import efficiency, matrix, preseason, ratings
from cfbmodel.authority import current
from cfbmodel.sources import cfbd

def _prior_season(season: int) -> int:
    """Most recent season worth carrying. 2020 is skipped: the COVID schedule
    was not comparable, so 2021 carries 2019 forward instead."""
    prior = season - 1
    return 2019 if prior == 2020 else prior


def _to_games(rows: list[dict]) -> list[ratings.Game]:
    out = []
    for g in rows:
        if g.get("homePoints") is None or g.get("awayPoints") is None:
            continue
        out.append(ratings.Game(
            week=g["week"], home=g["homeTeam"], away=g["awayTeam"],
            home_points=g["homePoints"], away_points=g["awayPoints"],
            neutral=bool(g.get("neutralSite")),
            home_is_fbs=g.get("homeClassification") == "fbs",
            away_is_fbs=g.get("awayClassification") == "fbs",
        ))
    return out


def build_ratings(season: int, week: int) -> dict[str, float]:
    """Ratings known before `week` of `season`.

    A fitted preseason prior (prior seasons + talent + returning production +
    recruiting) is blended with whatever the current season has produced. Worth
    0.42 points of MAE in weeks 1-4 against the flat carryover it replaced --
    see preseason.py.
    """
    p1 = ratings.build(_to_games(cfbd.games(_prior_season(season), completed_only=True)))
    p2 = ratings.build(_to_games(cfbd.games(_prior_season(_prior_season(season)),
                                            completed_only=True)))
    prior = preseason.build(season, p1, p2)
    current_rows = [g for g in cfbd.games(season, completed_only=True) if g["week"] < week]
    live = ratings.build(_to_games(current_rows))
    return preseason.blend(prior, live, week)


def _forms(season: int, week: int) -> dict[str, matrix.TeamForm]:
    """Opponent-adjusted efficiency for every team, using games before `week`.

    Raw season stats are confounded by schedule strength; adjusting is worth 0.30
    points of MAE overall and 0.70 in weeks 5-7. See efficiency.py.
    """
    rows = cfbd.season_game_stats(season, through_week=week)
    if not rows:
        return {}
    adjusted = efficiency.adjust_all(rows)
    pace = efficiency.pace_means(rows)
    teams: set[str] = set()
    for off, dfn in adjusted.values():
        teams |= set(off) | set(dfn)
    out: dict[str, matrix.TeamForm] = {}
    for team in teams:
        values = {}
        for stat in efficiency.ADJUSTABLE_STATS:
            off, dfn = adjusted[stat]
            values[f"off_{stat}"] = off.get(team)
            values[f"def_{stat}"] = dfn.get(team)
        for stat in efficiency.PACE_STATS:
            values[stat] = (pace.get(team) or {}).get(stat)
        out[team] = matrix.TeamForm(**values)
    return out


def _consensus(lines: list[dict], field: str) -> float | None:
    values = [l[field] for l in lines if l.get(field) is not None]
    if not values:
        return None
    consensus = next((l[field] for l in lines
                      if l.get("provider") == "consensus" and l.get(field) is not None), None)
    return float(consensus if consensus is not None else statistics.fmean(values))


def _market(season: int, week: int) -> dict[tuple[str, str], float]:
    """Expected HOME margin per matchup."""
    out: dict[tuple[str, str], float] = {}
    for row in cfbd.lines(season, week=week):
        spread = _consensus(row.get("lines") or [], "spread")
        if spread is None:
            continue
        # CFBD quotes the spread from the home perspective; negate for expected margin.
        out[(row["homeTeam"], row["awayTeam"])] = -spread
    return out


def _market_totals(season: int, week: int) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for row in cfbd.lines(season, week=week):
        total = _consensus(row.get("lines") or [], "overUnder")
        if total is not None:
            out[(row["homeTeam"], row["awayTeam"])] = total
    return out


def cmd_board(args: argparse.Namespace) -> int:
    auth = current()
    print(f"\n  CFB board — {args.season} week {args.week}")
    print(f"  authority: {auth.level.value}   may_bet={auth.may_bet}   "
          f"({len(auth.unmet_gates)} gates unmet)")
    print(f"  {auth.evidence}\n")

    r = build_ratings(args.season, args.week)
    forms = _forms(args.season, args.week)
    market = _market(args.season, args.week)
    market_tot = _market_totals(args.season, args.week)
    slate = [g for g in cfbd.games(args.season, week=args.week)
             if g.get("homeClassification") == "fbs" and g.get("awayClassification") == "fbs"]
    if not slate:
        print("  no FBS-vs-FBS games found for that week")
        return 0

    rows = []
    for g in slate:
        home, away = g["homeTeam"], g["awayTeam"]
        f = fc.game(
            home=home, away=away, team_ratings=r,
            neutral=bool(g.get("neutralSite")),
            home_form=forms.get(home), away_form=forms.get(away),
            market_margin=market.get((home, away)),
            market_total=market_tot.get((home, away)),
            authority=auth,
            week=args.week,
        )
        rows.append(f)

    if rows and not rows[0].in_validated_regime:
        print(f"  [!] week {args.week} is before week {fc.FIRST_VALIDATED_WEEK}: forecasts here use a")
        print("      fitted preseason prior rather than observed form, and the slate is full")
        print("      of mismatches the backtest never covered. Measured weeks 1-4 MAE is")
        print("      13.79 against a market of 12.00 - a 1.8-point gap, versus 0.37 from")
        print("      week 5 on. Treat the market as the better estimate here.")
        print()
    rows.sort(key=lambda f: abs(f.edge_points) if f.edge_points is not None else -1, reverse=True)
    print(f"  {'matchup':<40} {'score':>13} {'total':>12} {'model':>7} {'market':>7} {'edge':>7}  action")
    print(f"  {'-'*40} {'-'*13} {'-'*12} {'-'*7} {'-'*7} {'-'*7}  ------")
    for f in rows:
        matchup = f"{f.away} @ {f.home}" + ("  (N)" if f.neutral else "")
        model = f"{f.model_margin:+.1f}" if f.model_margin is not None else "  --"
        mkt = f"{f.market_margin:+.1f}" if f.market_margin is not None else "  --"
        edge = f"{f.edge_points:+.1f}" if f.edge_points is not None else "  --"
        if f.projected_home_score is not None:
            score = f"{f.projected_away_score:.0f}-{f.projected_home_score:.0f}"
            star = "" if f.total_modelled else "*"
            score = f"{score}{star}"
        else:
            score = "--"
        if f.projected_total is not None:
            tot = f"{f.projected_total:.1f}"
            if f.market_total is not None:
                tot = f"{tot}/{f.market_total:.1f}"
        else:
            tot = "--"
        flag = "" if f.used_efficiency else "  [ratings-only]"
        print(f"  {matchup[:40]:<40} {score:>13} {tot:>12} {model:>7} {mkt:>7} {edge:>7}  {f.action.value}{flag}")
    print(f"\n  {len(rows)} games · score is the MODEL projection (away-home), "
          f"total column is model/market")
    print(f"  * = league-mean total, no form yet · "
          f"published margin equals the market at lam=0\n")
    return 0


def cmd_build_site(args: argparse.Namespace) -> int:
    from pathlib import Path

    from cfbmodel import site

    path = site.build(season=args.season, week=args.week, out=Path(args.out))
    size = path.stat().st_size
    print(f"  wrote {path}  ({size:,} bytes)")
    return 0


def cmd_ratings(args: argparse.Namespace) -> int:
    r = build_ratings(args.season + 1, 1) if args.preseason else \
        ratings.build(_to_games(cfbd.games(args.season, completed_only=True)))
    ranked = sorted(((v, k) for k, v in r.items() if k != ratings.FCS), reverse=True)
    print(f"\n  {'#':>3}  {'team':<32} rating")
    for i, (v, t) in enumerate(ranked[:args.top], 1):
        print(f"  {i:>3}  {t:<32} {v:+.2f}")
    print(f"\n  {len(ranked)} FBS teams · points vs an average team on a neutral field\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cfbmodel")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("board", help="forecast a week's slate")
    b.add_argument("--season", type=int, required=True)
    b.add_argument("--week", type=int, required=True)
    b.set_defaults(func=cmd_board)

    s_ = sub.add_parser("build-site", help="write the static dashboard")
    s_.add_argument("--season", type=int, required=True)
    s_.add_argument("--week", type=int, required=True)
    s_.add_argument("--out", default="docs/index.html")
    s_.set_defaults(func=cmd_build_site)

    t = sub.add_parser("ratings", help="power ratings")
    t.add_argument("--season", type=int, required=True)
    t.add_argument("--top", type=int, default=25)
    t.add_argument("--preseason", action="store_true", help="carry into the next season")
    t.set_defaults(func=cmd_ratings)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
