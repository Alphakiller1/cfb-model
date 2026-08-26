"""Command line entry point.

    python -m cfbmodel.cli board --season 2026 --week 1
    python -m cfbmodel.cli ratings --season 2025
"""

from __future__ import annotations

import argparse
import statistics

from cfbmodel import forecast as fc
from cfbmodel import matrix, ratings
from cfbmodel.authority import current
from cfbmodel.sources import cfbd

# Weight on the prior season when the current one has too few games to stand alone.
# Week 1 has no current-season signal at all, so the prior season is everything.
CARRYOVER = 0.72


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

    Blends the prior season (discounted -- rosters turn over hard in CFB) with
    whatever the current season has produced so far. Early weeks lean almost
    entirely on carryover, which is honest: nothing else is known yet.
    """
    prior = ratings.build(_to_games(cfbd.games(season - 1, completed_only=True)))
    current_rows = [g for g in cfbd.games(season, completed_only=True) if g["week"] < week]
    live = ratings.build(_to_games(current_rows))
    if not live:
        return {t: v * CARRYOVER for t, v in prior.items()}
    # Weight the live season in as it accumulates; by mid-season it dominates.
    share = min(1.0, (week - 1) / 8.0)
    teams = set(prior) | set(live)
    return {
        t: share * live.get(t, 0.0) + (1.0 - share) * CARRYOVER * prior.get(t, 0.0)
        for t in teams
    }


def _forms(season: int, week: int) -> dict[str, matrix.TeamForm]:
    rows = cfbd.advanced_season_stats(season, through_week=week)
    out: dict[str, matrix.TeamForm] = {}
    for r in rows:
        o, d = r.get("offense") or {}, r.get("defense") or {}
        havoc = (d.get("havoc") or {}).get("total")
        out[r["team"]] = matrix.TeamForm(
            success_rate=o.get("successRate"), ppa_per_play=o.get("ppa"),
            points_per_opportunity=o.get("pointsPerOpportunity"),
            explosiveness=o.get("explosiveness"),
            success_rate_allowed=d.get("successRate"),
            points_per_opportunity_allowed=d.get("pointsPerOpportunity"),
            ppa_allowed=d.get("ppa"), explosiveness_allowed=d.get("explosiveness"),
            havoc_rate=havoc,
        )
    return out


def _market(season: int, week: int) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for row in cfbd.lines(season, week=week):
        vals = [l["spread"] for l in (row.get("lines") or []) if l.get("spread") is not None]
        if not vals:
            continue
        consensus = next((l["spread"] for l in row["lines"]
                          if l.get("provider") == "consensus" and l.get("spread") is not None), None)
        spread = float(consensus if consensus is not None else statistics.fmean(vals))
        # CFBD quotes the spread from the home perspective; negate for expected margin.
        out[(row["homeTeam"], row["awayTeam"])] = -spread
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
            authority=auth,
            week=args.week,
        )
        rows.append(f)

    if rows and not rows[0].in_validated_regime:
        print(f"  [!] week {args.week} is before week {fc.FIRST_VALIDATED_WEEK}: no current-season")
        print("      form exists, ratings are discounted carryover, and the slate is full of")
        print("      mismatches the backtest never covered. Model margins below are OUT OF")
        print("      REGIME - the market is the better estimate.")
        print()
    rows.sort(key=lambda f: abs(f.edge_points) if f.edge_points is not None else -1, reverse=True)
    print(f"  {'matchup':<44} {'model':>7} {'market':>7} {'edge':>7} {'win%':>6}  action")
    print(f"  {'-'*44} {'-'*7} {'-'*7} {'-'*7} {'-'*6}  ------")
    for f in rows:
        matchup = f"{f.away} @ {f.home}" + ("  (N)" if f.neutral else "")
        model = f"{f.model_margin:+.1f}" if f.model_margin is not None else "  --"
        mkt = f"{f.market_margin:+.1f}" if f.market_margin is not None else "  --"
        edge = f"{f.edge_points:+.1f}" if f.edge_points is not None else "  --"
        win = f"{100*f.win_probability:.0f}%" if f.win_probability is not None else " --"
        print(f"  {matchup[:44]:<44} {model:>7} {mkt:>7} {edge:>7} {win:>6}  {f.action.value}")
    print(f"\n  {len(rows)} games · model margins are the home side · "
          f"published margin equals the market at lam=0\n")
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

    t = sub.add_parser("ratings", help="power ratings")
    t.add_argument("--season", type=int, required=True)
    t.add_argument("--top", type=int, default=25)
    t.add_argument("--preseason", action="store_true", help="carry into the next season")
    t.set_defaults(func=cmd_ratings)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
