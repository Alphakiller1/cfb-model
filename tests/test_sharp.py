from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from cfbmodel import authority, forecast, ledger, sharp, site
from cfbmodel.sources import dk_splits

PAGE = """
<div class="tb-se-title flex"> <span>Pittsburgh @ Virginia Tech</span> <span>10/2, 07:00PM</span>
<div>Moneyline</div><div>Odds</div><div>% Handle</div><div>% Bets</div>
<div>Virginia Tech</div><a class="tb-odd-s">&minus;130</a><div>67%</div><div>66%</div>
<div>Pittsburgh</div><a>+110</a><div>33%</div><div>34%</div>
<div>Spread</div><div>Odds</div><div>% Handle</div><div>% Bets</div>
<div>Virginia Tech -2.5</div><a>&minus;105</a><div>40%</div><div>66%</div>
<div>Pittsburgh +2.5</div><a>&minus;115</a><div>60%</div><div>34%</div>
<div>Total</div><div>Odds</div><div>% Handle</div><div>% Bets</div>
<div>Over 54.5</div><a>&minus;112</a><div>55%</div><div>66%</div>
<div>Under 54.5</div><a>&minus;108</a><div>45%</div><div>34%</div>
</div>
<div class="tb-se-title"><span>IND Colts @ </span><span>WAS Commanders</span><span>10/4</span>
<div>Spread</div><div>WAS Commanders +4.5</div><a>-115</a><div>17%</div><div>58%</div>
<div>IND Colts -4.5</div><a>-105</a><div>83%</div><div>42%</div></div>
"""


def test_splits_page_parses_every_market_and_split_titles():
    games = dk_splits.parse(PAGE)
    assert [(g.away, g.home) for g in games] == [("Pittsburgh", "Virginia Tech"),
                                                ("IND Colts", "WAS Commanders")]
    spread = [s for s in games[0].sides if s.market == "spread"]
    assert spread[1].selection == "Pittsburgh +2.5" and spread[1].odds == -115
    assert (spread[1].handle_pct, spread[1].bets_pct) == (60.0, 34.0)
    assert len(games[0].sides) == 6


KICKOFF = datetime(2026, 10, 3, 19, tzinfo=timezone.utc)


def _row(book_margin=2.5, model_margin=-1.0, book_total=54.5, model_total=50.0):
    f = forecast.game(
        home="Virginia Tech", away="Pittsburgh", team_ratings={"Virginia Tech": 1.0,
                                                               "Pittsburgh": 0.0},
        market_margin=book_margin, week=6, authority=authority.current(), simulations=0,
        book=type("Book", (), {"book_title": "DraftKings", "home_margin": book_margin,
                               "total": book_total, "last_update": None,
                               "commence_time": None})(),
    )
    f = replace(f, model_margin=model_margin, independent_total=model_total)
    return site.Row(f, "Sat", None, None, kickoff_utc=KICKOFF)


def _resolve(name):
    return name


def _opening(home_margin, total=54.5):
    return [{"season": 2026, "week": 6, "home": "Virginia Tech", "away": "Pittsburgh",
             "recorded_at": "2026-09-29T12:00:00Z", "book_margin": home_margin,
             "book_total": total}]


def test_spot_flags_money_side_with_reverse_line_movement():
    games = dk_splits.parse(PAGE)[:1]
    # Pitt opened +3.5 and is now +2.5: the number moved toward Pitt, where 60% of
    # the money sits on 34% of the bets - reverse line movement.
    spots = sharp.build([_row()], games, _opening(3.5), season=2026, week=6,
                        resolve=_resolve)
    spread = next(s for s in spots if s.family == "spread")
    assert spread.selection == "Pittsburgh +2.5" and spread.side == "away"
    assert spread.divergence == 26.0
    assert spread.open_line == 3.5 and spread.move == 1.0
    assert spread.reverse is True and "Reverse line movement" in spread.note
    assert spread.model_agrees is True                          # model has Pitt by 1
    assert spread.concentration == 26.0 + 3.0 + 5.0


def test_a_move_away_from_the_money_scores_lower():
    games = dk_splits.parse(PAGE)[:1]
    # Pitt opened +1 and drifted out to +2.5: the market moved against the money.
    away = sharp.build([_row()], games, _opening(1.0), season=2026, week=6,
                       resolve=_resolve)
    spread = next(s for s in away if s.family == "spread")
    assert spread.move == -1.5 and spread.reverse is False
    assert spread.concentration == 26.0
    fresh = sharp.build([_row()], games, [], season=2026, week=6, resolve=_resolve)
    assert next(s for s in fresh if s.family == "spread").open_line is None


def test_no_spot_below_the_divergence_threshold():
    games = dk_splits.parse(PAGE)[:1]
    quiet = [replace(games[0], sides=tuple(
        replace(s, handle_pct=s.bets_pct + 5) for s in games[0].sides))]
    assert sharp.build([_row()], quiet, [], season=2026, week=6, resolve=_resolve) == []


def test_threshold_and_ledger_grading_with_clv(tmp_path):
    games = dk_splits.parse(PAGE)[:1]
    spots = sharp.build([_row()], games, [], season=2026, week=6, resolve=_resolve)
    assert {s.family for s in spots} == {"spread", "total"}
    total = next(s for s in spots if s.family == "total")
    assert total.selection == "Under 54.5" and total.divergence == 11.0

    path = tmp_path / "ledger.json"
    now = KICKOFF - timedelta(days=1)
    ledger.update(season=2026, week=6, forecasts=[], season_games=[],
                  sharp_spots=[s.to_json() for s in spots], path=path, recorded_at=now)
    # Closing quote: Pitt +1.5 (home margin 1.5), total 53.5.
    payload = ledger._load(path)
    payload["snapshots"].append({"season": 2026, "week": 6, "home": "Virginia Tech",
                                 "away": "Pittsburgh", "recorded_at": "2026-10-03T18:00:00Z",
                                 "book_margin": 1.5, "book_total": 53.5, "status": "pending"})
    ledger._write(path, payload)
    result = [{"week": 6, "homeTeam": "Virginia Tech", "awayTeam": "Pittsburgh",
               "homePoints": 20, "awayPoints": 24, "completed": True}]
    payload = ledger.update(season=2026, week=6, forecasts=[], season_games=result,
                            path=path, recorded_at=KICKOFF + timedelta(days=1))
    graded = {s["family"]: s for s in payload["sharp_spots"]}
    assert graded["spread"]["result"] == "win"          # Pitt +2.5 won outright
    assert graded["spread"]["clv"] == 1.0               # took +2.5, closed +1.5
    assert graded["total"]["result"] == "win"           # under 54.5, 44 scored
    assert graded["total"]["clv"] == 1.0                # took 54.5, closed 53.5
    summary = payload["summary"]["sharp_spots"]
    assert summary["spread"]["mean_clv"] == 1.0 and summary["spread"]["win"] == 1
