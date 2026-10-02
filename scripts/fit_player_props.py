"""Fit and test the CFB player projection layer, time-forward.

    python scripts/fit_player_props.py                 # fit 2024, test 2025, then ship fit
    python scripts/fit_player_props.py --json reports/player_props_fit.json

1. Team environment: per team stat, least squares of (actual - league mean) on
   (own offence dev, opponent allowed dev, team margin, implied points dev),
   FBS games weeks 3+ with a closing line. Fitted on 2024, scored on 2025
   against two baselines: the league mean and the team's own weighted mean.
2. Players: 2025 weeks 4+, every published projection for a player who then
   played, scored against the naive projection (his weighted per-game average
   over the games he played). Players who did not play are counted, not scored:
   a sportsbook voids those props.
3. Distributions: quantiles of actual / projected by stat and projection size,
   measured on the fit season; 2025 coverage of the 10th-90th band is reported
   (target 80%).

Shipped constants (TEAM_COEF, RATIO_QUANTILES) are refit on 2024 + 2025 and
printed for pasting into src/cfbmodel/player_props.py.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfbmodel import player_props as pp  # noqa: E402
from cfbmodel.sources import cfbd, espn_box  # noqa: E402
from cfbmodel.sources.oddsapi import normalise  # noqa: E402

FIT, TEST = 2024, 2025
SIZE_BUCKETS = {  # projection-size edges per stat for the ratio quantiles
    "pass_yds": (180, 240, 1e9), "pass_att": (26, 34, 1e9), "pass_cmp": (16, 21, 1e9),
    "pass_td": (1.2, 1.9, 1e9), "pass_int": (0.6, 0.9, 1e9),
    "rush_yds": (20, 45, 75, 1e9), "rush_car": (6, 11, 16, 1e9),
    "rec": (1.8, 3.0, 4.5, 1e9), "rec_yds": (20, 35, 55, 1e9),
}


def _consensus(lines: list[dict], key: str) -> float | None:
    values = [ln.get(key) for ln in lines if isinstance(ln.get(key), (int, float))]
    return statistics.median(values) if values else None


def attach_lines(boxes: list[espn_box.GameBox], season: int) -> int:
    """Closing spread/total from the (cached) CFBD lines, by week and school."""
    by_key = {}
    for row in cfbd.lines(season):
        spread = _consensus(row.get("lines") or [], "spread")
        total = _consensus(row.get("lines") or [], "overUnder")
        by_key[(row.get("week"), normalise(row.get("homeTeam") or ""),
                normalise(row.get("awayTeam") or ""))] = (spread, total)
    hit = 0
    for box in boxes:
        home = next(t for t in box.teams if t.home)
        away = next(t for t in box.teams if not t.home)
        spread, total = by_key.get((box.week, normalise(home.location), normalise(away.location)),
                                   (None, None))
        if spread is not None:
            box.home_margin_line = -spread
        if total is not None:
            box.total_line = total
        hit += box.home_margin_line is not None and box.total_line is not None
    return hit


def env_for(box: espn_box.GameBox, team_id: str) -> pp.Environment | None:
    if box.home_margin_line is None or box.total_line is None:
        return None
    team = box.team(team_id)
    margin = box.home_margin_line if team.home else -box.home_margin_line
    return pp.Environment(margin=margin, total=box.total_line)


def team_rows(hist: pp.History, season: int, first_week: int = 3):
    rows = []
    for box in hist.boxes:
        if box.season != season or box.week < first_week:
            continue
        for t in box.teams:
            env = env_for(box, t.team_id)
            opp = box.opponent(t.team_id)
            if env is None or opp is None or not hist.before(t.team_id, season, box.week):
                continue
            feats, league = pp.team_features(hist, t.team_id, opp, season, box.week, env)
            rows.append((feats, league, hist.totals[(box.event_id, t.team_id)]))
    return rows


def fit_team(rows) -> dict[str, tuple[float, ...]]:
    coef = {}
    for s in pp.TEAM_STATS:
        X = np.array([r[0][s] for r in rows], float)
        y = np.array([r[2][s] - r[1][s] for r in rows], float)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        coef[s] = tuple(round(float(b), 4) for b in beta)
    return coef


def score_team(rows, coef) -> dict[str, dict[str, float]]:
    out = {}
    for s in pp.TEAM_STATS:
        actual = np.array([r[2][s] for r in rows])
        league = np.array([r[1][s] for r in rows])
        own = league + np.array([r[0][s][0] for r in rows])
        model = league + np.array([r[0][s] for r in rows]) @ np.array(coef[s])
        out[s] = {"league": float(np.mean(np.abs(actual - league))),
                  "own_mean": float(np.mean(np.abs(actual - own))),
                  "model": float(np.mean(np.abs(actual - np.maximum(model, 0))))}
    return out


def naive(hist: pp.History, team_id: str, athlete_id: str, season: int, week: int, stat: str):
    """Weighted per-game average over the games the player played this season."""
    num = den = 0.0
    games = [b for b in hist.before(team_id, season, week) if b.season == season]
    for age, box in enumerate(reversed(games)):
        line = next((p for p in box.players
                     if p.team_id == team_id and p.athlete_id == athlete_id), None)
        if line is None:
            continue
        w = 0.5 ** (age / pp.PLAYER_HALF_LIFE)
        num += w * getattr(line, stat)
        den += w
    return num / den if den else None


def player_rows(hist: pp.History, season: int, first_week: int = 4):
    rows = []
    dnp = total = 0
    for box in hist.boxes:
        if box.season != season or box.week < first_week:
            continue
        for t in box.teams:
            env = env_for(box, t.team_id)
            opp = box.opponent(t.team_id)
            if env is None or opp is None:
                continue
            for proj in pp.project_team(hist, t.team_id, opp, season, box.week, env):
                total += 1
                actual = next((p for p in box.players if p.team_id == t.team_id
                               and p.athlete_id == proj.athlete_id), None)
                if actual is None:
                    dnp += 1
                    continue
                for stat, mean in proj.metrics.items():
                    rows.append({"stat": stat, "pos": proj.position, "mean": mean,
                                 "naive": naive(hist, t.team_id, proj.athlete_id,
                                                season, box.week, stat),
                                 "actual": getattr(actual, stat)})
    return rows, dnp, total


def ratio_quantiles(rows) -> dict[str, list]:
    out = {}
    for stat, edges in SIZE_BUCKETS.items():
        buckets = []
        lo = 0.0
        for hi in edges:
            ratios = [r["actual"] / r["mean"] for r in rows
                      if r["stat"] == stat and lo < r["mean"] <= hi and r["mean"] > 0]
            if len(ratios) >= 40:
                q = tuple(round(float(np.quantile(ratios, level)), 3)
                          for level in pp.QUANTILE_LEVELS)
                buckets.append((hi if hi < 1e8 else 1e9, q))
            lo = hi
        if buckets:
            out[stat] = buckets
    return out


def coverage(rows, quantiles) -> dict[str, float]:
    pp.RATIO_QUANTILES = quantiles
    out = {}
    for stat in SIZE_BUCKETS:
        inside = n = 0
        for r in rows:
            if r["stat"] != stat:
                continue
            dist = pp.distribution(stat, r["mean"])
            if "p10" not in dist:
                continue
            n += 1
            inside += dist["p10"] <= r["actual"] <= dist["p90"]
        if n:
            out[stat] = round(inside / n, 3)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default="reports/player_props_fit.json")
    args = parser.parse_args()

    boxes = []
    for season in (FIT - 1, FIT, TEST):
        season_boxes = espn_box.season_boxes(season)
        if season in (FIT, TEST):
            print(f"{season}: {len(season_boxes)} games, "
                  f"{attach_lines(season_boxes, season)} with a closing line")
        boxes += season_boxes
    hist = pp.History(boxes)

    fit_rows = team_rows(hist, FIT)
    test_rows = team_rows(hist, TEST)
    coef = fit_team(fit_rows)
    team_score = score_team(test_rows, coef)
    print(f"\n== TEAM ENVIRONMENT  fit {FIT} (n={len(fit_rows)}) -> test {TEST} (n={len(test_rows)})")
    print(f"  {'stat':<9} {'league':>8} {'own':>8} {'model':>8}   coef (own, opp, margin, pts)")
    for s, v in team_score.items():
        print(f"  {s:<9} {v['league']:8.3f} {v['own_mean']:8.3f} {v['model']:8.3f}   {coef[s]}")

    pp.TEAM_COEF = coef
    fit_players, _, _ = player_rows(hist, FIT)
    quantiles = ratio_quantiles(fit_players)
    pp.RATIO_QUANTILES = quantiles
    test_players, dnp, total = player_rows(hist, TEST)
    print(f"\n== PLAYERS {TEST} weeks 4+: {total} projections, {dnp} did not play "
          f"({dnp / max(total, 1):.1%}, voided at a book)")
    print(f"  {'stat':<9} {'pos':<4} {'n':>6} {'mean act':>9} {'naive':>8} {'model':>8} {'gain':>7}")
    player_score = {}
    for stat in SIZE_BUCKETS:
        for pos in ("QB", "RB", "WR"):
            sel = [r for r in test_players if r["stat"] == stat
                   and (r["pos"] == pos or (pos == "WR" and r["pos"] == "TE"))
                   and r["naive"] is not None]
            if len(sel) < 50:
                continue
            a = np.array([r["actual"] for r in sel])
            nv = np.mean(np.abs(a - np.array([r["naive"] for r in sel])))
            md = np.mean(np.abs(a - np.array([r["mean"] for r in sel])))
            player_score[f"{stat}:{pos}"] = {"n": len(sel), "naive": float(nv), "model": float(md)}
            print(f"  {stat:<9} {pos:<4} {len(sel):6d} {a.mean():9.2f} {nv:8.3f} {md:8.3f} "
                  f"{(nv - md) / nv:+7.1%}")
    cover = coverage(test_players, quantiles)
    print(f"\n== 10th-90th BAND COVERAGE on {TEST} (target 0.80): {cover}")

    # Ship: refit on both seasons.
    ship_coef = fit_team(fit_rows + test_rows)
    pp.TEAM_COEF = ship_coef
    ship_quantiles = ratio_quantiles(player_rows(hist, FIT)[0] + player_rows(hist, TEST)[0])
    print("\nTEAM_COEF =", json.dumps(ship_coef, indent=1))
    print("RATIO_QUANTILES =", json.dumps(ship_quantiles))
    Path(args.json).write_text(json.dumps({
        "fit_season": FIT, "test_season": TEST,
        "team_environment": team_score, "players": player_score,
        "did_not_play": {"projections": total, "dnp": dnp},
        "band_coverage": cover,
        "shipped": {"team_coef": ship_coef, "ratio_quantiles": ship_quantiles},
    }, indent=1), encoding="utf-8")
    print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
