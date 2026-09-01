"""Command line entry point.

    python -m cfbmodel.cli board --season 2026 --week 1
    python -m cfbmodel.cli ratings --season 2025
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass
from pathlib import Path

from cfbmodel import forecast as fc
from cfbmodel import calibration, coaching, efficiency, export, fitting, matrix
from cfbmodel import conferences, preseason, ratings, roster, site, teams, totals
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
        # The API contains every NCAA division. Lower-division-only games do not
        # inform an FBS rating and, when collapsed into the shared __FCS__ node,
        # add thousands of self-observations to that node. Keep FBS-vs-FCS games;
        # discard only games with no FBS participant.
        if (g.get("homeClassification") != "fbs"
                and g.get("awayClassification") != "fbs"):
            continue
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


@dataclass(frozen=True)
class RatingBundle:
    table: dict[str, float]
    components: dict[str, preseason.Components]
    live: dict[str, float]


def build_rating_bundle(season: int, week: int) -> RatingBundle:
    """Ratings known before `week` of `season`.

    A fitted preseason prior (prior seasons + talent + returning production +
    recruiting) is blended with whatever the current season has produced. Worth
    0.42 points of MAE in weeks 1-4 against the flat carryover it replaced --
    see preseason.py.
    """
    p1 = ratings.build(_to_games(cfbd.games(_prior_season(season), completed_only=True)))
    p2 = ratings.build(_to_games(cfbd.games(_prior_season(_prior_season(season)),
                                            completed_only=True)))
    extra = preseason.roster_features(season)
    components = preseason.components(season, p1, p2, extra)
    prior = {team: component.rating for team, component in components.items()}
    current_rows = [g for g in cfbd.games(season, completed_only=True) if g["week"] < week]
    live = ratings.build(_to_games(current_rows))
    return RatingBundle(preseason.blend(prior, live, week), components, live)


def build_ratings(season: int, week: int) -> dict[str, float]:
    return build_rating_bundle(season, week).table


def _preseason_totals(season: int) -> totals.PreseasonContext | None:
    """Previous-season scoring context, known before the current season starts."""
    prior = _prior_season(season)
    games = _to_games(cfbd.games(prior, completed_only=True))
    return totals.preseason_context(games)


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


def _markets(rows: list[dict]) -> tuple[
    dict[tuple[str, str], float], dict[tuple[str, str], float]
]:
    """Build spread and total maps from one CFBD response."""
    margins: dict[tuple[str, str], float] = {}
    totals_map: dict[tuple[str, str], float] = {}
    for row in rows:
        key = (row["homeTeam"], row["awayTeam"])
        lines = row.get("lines") or []
        spread = _consensus(lines, "spread")
        total = _consensus(lines, "overUnder")
        if spread is not None:
            margins[key] = -spread
        if total is not None:
            totals_map[key] = total
    return margins, totals_map


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
    preseason_totals = _preseason_totals(args.season)
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
            preseason_total=totals.preseason_total(home, away, preseason_totals),
            authority=auth,
            week=args.week,
        )
        rows.append((f, site._parse_kickoff(g.get("startDate"))))

    if rows and not rows[0][0].in_validated_regime:
        print(f"  [!] week {args.week} is before week {fc.FIRST_VALIDATED_WEEK}: forecasts here use a")
        if args.week == 1:
            print("      fitted preseason prior with no current-season form.")
        else:
            weight = matrix.EARLY_EFFICIENCY_RELIABILITY.get(args.week, 0.0)
            print(f"      transition blend; observed form carries {weight:.0%} and the preseason")
            print("      prior carries the balance.")
        print("      Season-opening audit MAE is 12.54 against the market's 11.87.")
        print("      Treat the market as the better estimate here.")
        print()
    # Chronological, matching the dashboard and the export. Ranking by |edge|
    # put the widest spreads on top, which is where a compressed model always
    # lands on the underdog -- an ordering that implied conviction the
    # authority gate says the model has not earned.
    rows.sort(key=lambda pair: ((1, 0.0) if pair[1] is None else (0, pair[1].timestamp()),
                                pair[0].away, pair[0].home))
    print(f"  {'kickoff':<22} {'matchup':<40} {'score':>13} {'total':>12} {'model':>7} {'market':>7} {'edge/gap':>9}  action")
    print(f"  {'-'*22} {'-'*40} {'-'*13} {'-'*12} {'-'*7} {'-'*7} {'-'*9}  ------")
    for f, kickoff in rows:
        when = site._kickoff_label(kickoff) or "unscheduled"
        matchup = f"{f.away} vs {f.home}  (N)" if f.neutral else f"{f.away} @ {f.home}"
        model = f"{f.model_margin:+.1f}" if f.model_margin is not None else "  --"
        mkt = f"{f.market_margin:+.1f}" if f.market_margin is not None else "  --"
        # A withheld gap is bracketed so it never reads as a tradable edge.
        if f.edge_points is not None:
            edge = f"{f.edge_points:+.1f}"
        elif f.market_gap is not None:
            edge = f"({f.market_gap:+.1f})"
        else:
            edge = "  --"
        if f.projected_home_score is not None:
            score = f"{f.projected_away_score:.0f}-{f.projected_home_score:.0f}"
            star = ""
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
        print(f"  {when[:22]:<22} {matchup[:40]:<40} {score:>13} {tot:>12} {model:>7} {mkt:>7} {edge:>9}  {f.action.value}{flag}")
    print(f"\n  {len(rows)} games · score is the MODEL projection (away-home), "
          f"total column is model/market")
    print(f"  (parenthesised) = information gap, not an edge · * = emergency league-mean fallback · "
          f"published margin equals the market at lam=0\n")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Write the board as JSON for downstream renderers (the content engine)."""
    from pathlib import Path

    auth = current()
    r = build_ratings(args.season, args.week)
    forms = _forms(args.season, args.week)
    market = _market(args.season, args.week)
    market_tot = _market_totals(args.season, args.week)
    preseason_totals = _preseason_totals(args.season)
    try:
        from cfbmodel.sources import oddsapi
        from cfbmodel import teams as teams_mod
        book_lines = oddsapi.fetch_lines(teams_mod.load(args.season))
    except Exception:
        book_lines = {}
    slate = [g for g in cfbd.games(args.season, week=args.week)
             if g.get("homeClassification") == "fbs" and g.get("awayClassification") == "fbs"]
    if not slate:
        print(f"  no FBS-vs-FBS games found for {args.season} week {args.week}")
        return 1

    rows = []
    for g in slate:
        home, away = g["homeTeam"], g["awayTeam"]
        f = fc.game(
            home=home, away=away, team_ratings=r,
            neutral=bool(g.get("neutralSite")),
            home_form=forms.get(home), away_form=forms.get(away),
            market_margin=market.get((home, away)),
            market_total=market_tot.get((home, away)),
            preseason_total=totals.preseason_total(home, away, preseason_totals),
            book=book_lines.get((home, away)),
            authority=auth, week=args.week,
        )
        rows.append((f, site._parse_kickoff(g.get("startDate"))))

    out = export.write(
        export.payload(season=args.season, week=args.week, rows=rows, authority=auth),
        Path(args.out),
    )
    neutral = sum(1 for f, _ in rows if f.neutral)
    print(f"  wrote {out}  ({len(rows)} games, {neutral} neutral-site, kickoff order)")
    print(f"  authority: {auth.level.value}  may_bet={auth.may_bet}")
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Measure whether projected margins are on the right scale.

    Walk-forward over completed weeks: build ratings and form from games before
    week W, forecast week W, then compare against what actually happened. The
    diagnostic MAE cannot give you is the calibration slope -- regress actual on
    predicted and you want 1.0. Below 1 is over-confidence; above 1 means the
    model is under-dispersed and will land on the market underdog whenever the
    price is wide.

    Reported per regime, because the two paths behave differently and averaging
    them hides the whole finding.
    """
    seasons = _parse_seasons(args.seasons)
    auth = current()
    buckets: dict[str, dict[str, list]] = {
        "preseason path (ratings only)": {"model": [], "market": [], "actual": []},
        "validated regime (weeks %d+)" % fc.FIRST_VALIDATED_WEEK:
            {"model": [], "market": [], "actual": []},
    }
    total = 0
    for season in seasons:
        for week in range(1, args.last_week + 1):
            try:
                rating_table = build_ratings(season, week)
                forms = _forms(season, week)
                market = _market(season, week)
                slate = [g for g in cfbd.games(season, week=week, completed_only=True)
                         if g.get("homeClassification") == "fbs"
                         and g.get("awayClassification") == "fbs"]
            except Exception as exc:  # a missing week must not abort the sweep
                print(f"  [skip] {season} wk{week}: {exc}")
                continue
            for g in slate:
                home, away = g["homeTeam"], g["awayTeam"]
                hp, ap = g.get("homePoints"), g.get("awayPoints")
                if hp is None or ap is None:
                    continue
                f = fc.game(
                    home=home, away=away, team_ratings=rating_table,
                    neutral=bool(g.get("neutralSite")),
                    home_form=forms.get(home), away_form=forms.get(away),
                    market_margin=market.get((home, away)),
                    authority=auth, week=week,
                )
                if f.model_margin is None:
                    continue
                key = ("validated regime (weeks %d+)" % fc.FIRST_VALIDATED_WEEK
                       if f.used_efficiency and f.in_validated_regime
                       else "preseason path (ratings only)")
                buckets[key]["model"].append(f.model_margin)
                buckets[key]["market"].append(f.market_margin)
                buckets[key]["actual"].append(float(hp - ap))
                total += 1

    if not total:
        print("  no completed games found — check --seasons and your CFBD key")
        return 1

    print(f"\n  Calibration over {total} completed FBS-vs-FBS games\n")
    for label, data in buckets.items():
        try:
            rep = calibration.report(label, model=data["model"],
                                     actual=data["actual"], market=data["market"])
        except calibration.NotEnoughData as exc:
            print(f"  {label}: {exc}")
            continue
        print("\n".join(rep.lines()))
        print()

    pre = buckets["preseason path (ratings only)"]
    if len(pre["model"]) >= 3:
        fitted = calibration.fit(pre["model"], pre["actual"])
        print("  Affine correction that would put the preseason path on the actual scale:")
        print(f"    Calibration(intercept={fitted.intercept:.4f}, "
              f"slope={fitted.slope:.4f},")
        print(f"                provenance=\"{fitted.provenance}\")")
        print()
        print("  Adopting it is a claim about evidence: score it leave-one-season-out")
        print("  within the same regime and record both MAE and side bias in")
        print("  reports/BASELINE_2019_2025.md before changing a shipped coefficient.")
    return 0


def _parse_seasons(spec: str) -> list[int]:
    """`2019,2021-2025` -> [2019, 2021, 2022, 2023, 2024, 2025]."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            out.extend(range(lo, hi + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def cmd_fit_preseason(args: argparse.Namespace) -> int:
    """Refit the preseason rating, scoring the new feeds against the current set.

    Target is a team's eventual end-of-season rating -- the same target
    `preseason.COEFFICIENTS` was fitted on, so the numbers are comparable to the
    table in `reports/BASELINE_2019_2025.md`. Leave-one-season-out, because team
    strength is autocorrelated within a season and a random split would leak.

    Candidates are nested: each row adds one family to the row above, so the
    table reads as "what did this feed buy" rather than as a beauty contest
    between unrelated models.
    """
    seasons = _parse_seasons(args.seasons)
    observations: list[tuple[int, dict[str, float], float]] = []
    covered: dict[str, int] = {}

    for season in seasons:
        try:
            actual = build_ratings_final(season)
            prior = build_ratings_final(_prior_season(season))
            prior2 = build_ratings_final(_prior_season(_prior_season(season)))
        except Exception as exc:
            print(f"  [skip] {season}: {exc}")
            continue
        comps = preseason.components(season, prior, prior2)
        roster_feats = roster.features(season) if args.roster else {}
        coach_feats = coaching.features(season) if args.coaching else {}

        for team, component in comps.items():
            target = actual.get(team)
            if target is None:
                continue
            row: dict[str, float] = {
                "prior_rating": component.prior_rating[0],
                "prior_rating_2": component.prior_rating_2[0],
                "talent": component.talent[0],
                "returning_production": component.returning_production[0],
                "recruiting_points": component.recruiting_points[0],
            }
            for name, value in roster_feats.get(team, {}).items():
                row[name] = value
                covered[name] = covered.get(name, 0) + 1
            for name, value in coach_feats.get(team, {}).items():
                row[name] = value
                covered[name] = covered.get(name, 0) + 1
            observations.append((season, row, target))

    if len(observations) < 50:
        print(f"  only {len(observations)} team-seasons assembled -- check --seasons "
              "and your CFBD key")
        return 1

    current = ["prior_rating", "prior_rating_2", "talent",
               "returning_production", "recruiting_points"]
    roster_names = [n for n in ("qb_returning", "portal_net", "portal_churn")
                    if covered.get(n)]
    coach_names = [n for n in sorted(covered)
                   if n.startswith("coach_") or n == "first_year_coach"]

    candidates = [
        ("carryover only", ["prior_rating"]),
        ("current (shipped)", current),
    ]
    if roster_names:
        candidates.append(("+ qb & portal", current + roster_names))
    if coach_names:
        candidates.append(("+ coaching", current + roster_names + coach_names))

    print(f"\n  Preseason fit over {len(observations)} team-seasons "
          f"({len(set(s for s, _, _ in observations))} seasons)\n")
    if covered:
        print("  new-feature coverage (team-seasons with the field present):")
        for name in sorted(covered):
            print(f"    {name:<28} {covered[name]:>5}")
        print()

    results = []
    for label, names in candidates:
        try:
            results.append(fitting.leave_one_season_out(label, names, observations))
        except (fitting.SingularSystem, ValueError) as exc:
            print(f"  [skip] {label}: {exc}")
    if not results:
        print("  nothing could be fitted")
        return 1

    # A feature whose fold-to-fold SD exceeds the magnitude of its own mean
    # changed sign between folds. That is noise wearing a coefficient, and
    # carrying it into a shipped model is how a fit stops generalising -- so the
    # widest candidate is re-run with those terms dropped.
    widest = max(results, key=lambda r: len(r.names))
    pooled, spread = widest.pooled_coefficients(), widest.coefficient_spread()
    stable = [n for n in widest.names
              if abs(pooled.get(n, 0.0)) > spread.get(n, 0.0)]
    dropped = [n for n in widest.names if n not in stable]
    if dropped and stable:
        try:
            results.append(fitting.leave_one_season_out(
                f"{widest.label} (stable only)", stable, observations))
        except (fitting.SingularSystem, ValueError):
            pass

    print("\n".join(fitting.compare(results)))
    if dropped:
        print(f"\n  Dropped as sign-unstable across folds: {', '.join(dropped)}")
    best = min(results, key=lambda r: r.mae)
    print(f"\n  Best held-out: {best.label}\n")
    pooled = best.pooled_coefficients()
    spread = best.coefficient_spread()
    print("  Coefficients (mean across folds, SD in brackets -- a sign that flips")
    print("  between folds is not a finding):")
    for name in ["intercept", *best.names]:
        if name not in pooled:
            continue
        print(f"    {name:<28} {pooled[name]:>+11.6f}   [{spread.get(name, 0.0):.6f}]")
    if best.label == "current (shipped)":
        print("\n  The new feeds did not lower held-out error. Nothing to adopt --")
        print("  that is a result, not a failure.")
    else:
        print("\n  Adoption is a claim about evidence: paste these into")
        print("  preseason.COEFFICIENTS in a commit that carries this table.")
    return 0


def build_ratings_final(season: int) -> dict[str, float]:
    """End-of-season ratings -- the target the preseason prior is fitted against."""
    return ratings.build(_to_games(cfbd.games(season, completed_only=True)))


# Field names this repo assumes, per endpoint. Kept as data so a CFBD rename
# surfaces here in one place instead of degrading a feature to a constant.
SOURCE_SCHEMA: dict[str, tuple[str, tuple[str, ...]]] = {
    "games": ("/games", ("homeTeam", "awayTeam", "homePoints", "awayPoints",
                         "neutralSite", "startDate", "venueId",
                         "homeClassification", "awayClassification")),
    "returning": ("/player/returning", ("team", "percentPPA", "percentPassingPPA")),
    "portal": ("/player/portal", ("origin", "destination", "position", "stars")),
    "coaches": ("/coaches", ("firstName", "lastName", "seasons")),
    "venues": ("/venues", ("id", "elevation", "latitude", "longitude", "dome")),
    "talent": ("/talent", ("team", "talent")),
}


def cmd_check_sources(args: argparse.Namespace) -> int:
    """Verify assumed field names against the live API, one request each.

    The roster, coaching, and venue features were written to a documented
    schema. Every one of them degrades softly on a missing field -- which is
    right for a flaky feed and wrong for a rename, because a renamed field would
    quietly become "league average forever". This is the check that tells the
    two apart.
    """
    season = args.season
    fetchers = {
        "games": lambda: cfbd.games(season, completed_only=True),
        "returning": lambda: cfbd.returning_production(season),
        "portal": lambda: cfbd.portal(season),
        "coaches": lambda: cfbd.coaches(season),
        "venues": cfbd.venues,
        "talent": lambda: cfbd.talent(season),
    }

    print(f"\n  CFBD schema check — season {season}\n")
    problems = 0
    for name, (path, expected) in SOURCE_SCHEMA.items():
        try:
            rows = fetchers[name]()
        except Exception as exc:
            print(f"  [FAIL] {path:<22} {exc}")
            problems += 1
            continue
        if not rows:
            print(f"  [WARN] {path:<22} returned no rows")
            continue
        # Union across a sample: a single row can legitimately omit a nullable
        # field, and calling that a rename would cry wolf on every run.
        seen: set[str] = set()
        for row in rows[:200]:
            if isinstance(row, dict):
                seen |= set(row)
        missing = [field for field in expected if field not in seen]
        if missing:
            print(f"  [FAIL] {path:<22} missing {', '.join(missing)}")
            problems += 1
        else:
            print(f"  [ ok ] {path:<22} {len(rows):>5} rows, all "
                  f"{len(expected)} fields present")

    print()
    if problems:
        print(f"  {problems} endpoint(s) do not match the documented schema.")
        print("  See docs/DATA_SOURCES.md — fix the client, do not widen the")
        print("  soft-degrade path, or the feature becomes a silent constant.")
        return 1
    print("  Every documented field is present.")
    return 0


def cmd_conferences(args: argparse.Namespace) -> int:
    """Conference ratings, aggregated from the team ratings."""
    season, week = args.season, args.week
    table = build_ratings(season, week) if week else build_ratings_final(season)
    team_meta = teams.load(season)
    conference_of = {name: t.conference for name, t in team_meta.items() if t.conference}
    if not conference_of:
        print("  no conference metadata available")
        return 1

    completed = _to_games(cfbd.games(season, completed_only=True))
    cross = conferences.cross_conference_games(completed, conference_of)
    table_rows = conferences.rate(table, conference_of, cross_counts=cross)
    if not table_rows:
        print("  no conference had enough rated members")
        return 1

    scope = f"week {week}" if week else "final"
    print(f"\n  Conference ratings — {season} ({scope})")
    print("  Points relative to an average FBS team on a neutral field.\n")
    print(f"  {'#':>2}  {'conference':<26} {'teams':>5} {'mean':>7} {'median':>7} "
          f"{'top4':>7} {'depth':>6} {'spread':>7} {'x-conf':>6}  best")
    print(f"  {'-'*2}  {'-'*26} {'-'*5} {'-'*7} {'-'*7} {'-'*7} {'-'*6} {'-'*7} {'-'*6}  ----")
    for rank, row in enumerate(table_rows, start=1):
        best_team, best_value = row.best
        print(f"  {rank:>2}  {row.name[:26]:<26} {row.teams:>5} {row.mean:>+7.2f} "
              f"{row.median:>+7.2f} {row.top_mean:>+7.2f} {row.depth:>+6.2f} "
              f"{row.spread:>7.2f} {row.cross_games:>6}  {best_team} ({best_value:+.1f})")

    print("\n  depth  = median - mean. Positive is a deep league; negative means the")
    print("           mean is carried by its top teams.")
    print("  x-conf = completed out-of-conference games informing the rating. Cross-")
    print("           conference scheduling is sparse, so a league's LEVEL is less")
    print("           firmly identified than the ordering inside it.")

    if args.teams:
        ranks = conferences.team_ranks_within_conference(table, conference_of)
        for row in table_rows:
            members = sorted(
                ((t, table[t]) for t in conference_of
                 if conference_of[t] == row.name and t in table),
                key=lambda pair: -pair[1],
            )
            print(f"\n  {row.name}")
            for team, value in members:
                place, size = ranks.get(team, (0, 0))
                print(f"    {place:>2}/{size:<3} {team:<28} {value:>+7.2f}")
    return 0


def cmd_build_site(args: argparse.Namespace) -> int:
    from pathlib import Path

    from cfbmodel import site

    path = site.build(season=args.season, week=args.week, out=Path(args.out))
    size = path.stat().st_size
    print(f"  wrote {path}  ({size:,} bytes)")
    return 0


def cmd_ratings(args: argparse.Namespace) -> int:
    # A preseason rating is for the season that has not kicked off yet, so it is
    # labelled with that season -- printing 2026's prior under a 2025 heading is
    # the one mistake this command can make that still looks right.
    if args.preseason:
        season, week, basis = args.season + 1, 1, "preseason"
        rated_games: list[ratings.Game] = []
        r = build_ratings(season, week)
    else:
        season, week, basis = args.season, None, "season_to_date"
        rated_games = _to_games(cfbd.games(args.season, completed_only=True))
        r = ratings.build(rated_games)
    ranked = sorted(((v, k) for k, v in r.items() if k != ratings.FCS), reverse=True)
    print(f"\n  {'#':>3}  {'team':<32} rating")
    for i, (v, t) in enumerate(ranked[:args.top], 1):
        print(f"  {i:>3}  {t:<32} {v:+.2f}")
    print(f"\n  {len(ranked)} FBS teams · points vs an average team on a neutral field\n")
    if args.out:
        payload = export.ratings_payload(
            season=season,
            ratings=r,
            basis=basis,
            week=week,
            games_rated=len(rated_games),
            top=args.top,
        )
        path = export.write(payload, Path(args.out))
        print(f"  wrote {len(payload['teams'])} teams to {path}")
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

    e = sub.add_parser("export", help="write the board as JSON for downstream renderers")
    e.add_argument("--season", type=int, required=True)
    e.add_argument("--week", type=int, required=True)
    e.add_argument("--out", required=True)
    e.set_defaults(func=cmd_export)

    cal = sub.add_parser("calibrate",
                         help="measure whether margins are on the right scale")
    cal.add_argument("--seasons", default="2019,2021-2025",
                     help="e.g. 2019,2021-2025")
    cal.add_argument("--last-week", type=int, default=14)
    cal.set_defaults(func=cmd_calibrate)

    fp = sub.add_parser("fit-preseason",
                        help="refit the preseason rating, scoring the new feeds")
    fp.add_argument("--seasons", default="2021-2025", help="e.g. 2021-2025")
    fp.add_argument("--no-roster", dest="roster", action="store_false",
                    help="skip portal and QB-returning candidates")
    fp.add_argument("--no-coaching", dest="coaching", action="store_false",
                    help="skip coaching-change candidates")
    fp.set_defaults(func=cmd_fit_preseason, roster=True, coaching=True)

    cs = sub.add_parser("check-sources",
                        help="verify CFBD field names against the documented schema")
    cs.add_argument("--season", type=int, default=2025)
    cs.set_defaults(func=cmd_check_sources)

    cf = sub.add_parser("conferences", help="conference ratings")
    cf.add_argument("--season", type=int, required=True)
    cf.add_argument("--week", type=int, help="point-in-time; omit for final")
    cf.add_argument("--teams", action="store_true", help="list members within each")
    cf.set_defaults(func=cmd_conferences)

    t = sub.add_parser("ratings", help="power ratings")
    t.add_argument("--season", type=int, required=True)
    t.add_argument("--top", type=int, default=25)
    t.add_argument("--preseason", action="store_true", help="carry into the next season")
    t.add_argument("--out", help="also write the ranked ratings as JSON for the content engine")
    t.set_defaults(func=cmd_ratings)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
