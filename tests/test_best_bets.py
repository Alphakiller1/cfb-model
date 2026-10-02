from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from cfbmodel import authority, best_bets as bb, forecast, ledger, matrix, site
from cfbmodel.sources.espn_box import GameBox, PlayerLine, TeamGame
from cfbmodel.sources.oddsapi import PropQuote

KICKOFF = datetime(2026, 10, 3, 19, tzinfo=timezone.utc)
NOW = KICKOFF - timedelta(days=1)


def _form(level: float) -> matrix.TeamForm:
    return matrix.TeamForm(
        off_ppa=0.20 + level, off_successRate=0.42 + level / 2, off_explosiveness=1.2 + level,
        off_stuffRate=0.18 - level / 4, def_ppa=0.20 - level, def_successRate=0.42 - level / 2,
        def_explosiveness=1.2 - level, def_stuffRate=0.18 + level / 4, drives=12.0, plays=70.0,
    )


def _row(model_margin=10.0, book_margin=3.0, model_total=60.0, book_total=52.5, **changes):
    f = forecast.game(
        home="Home", away="Away", team_ratings={"Home": 10.0, "Away": 0.0},
        market_margin=book_margin, market_total=book_total, preseason_total=54.0, week=6,
        book=type("Book", (), {"book_title": "DraftKings", "home_margin": book_margin,
                               "total": book_total, "last_update": "2026-10-01T12:00:00Z",
                               "commence_time": "2026-10-03T19:00:00Z"})(),
        authority=authority.current(),
    )
    f = replace(f, model_margin=model_margin, independent_total=model_total, **changes)
    return site.Row(f, "Sat", _form(0.10), _form(-0.10), kickoff_utc=KICKOFF)


FORMS = {"Home": _form(0.10), "Away": _form(-0.10), "Other": _form(0.0)}


def test_spread_pick_takes_the_side_the_model_prefers_and_explains_why():
    ranks = bb.fbs_ranks(FORMS)
    pick = bb.spread_pick(_row(), season=2026, week=6, ranks=ranks, team_status={})
    assert pick.selection == "Home -3.0" and pick.side == "home"
    assert pick.edge == 7.0 and 0.5 < pick.probability < 0.8
    assert "7.0-point disagreement toward Home" in pick.angle
    assert "ranks 1st in FBS" in pick.angle          # an efficiency driver, with its rank


def test_underdog_side_and_minimum_edge():
    pick = bb.spread_pick(_row(model_margin=-2.0, book_margin=3.0), season=2026, week=6,
                          ranks={}, team_status={})
    assert pick.selection == "Away +3.0" and pick.side == "away"
    assert bb.spread_pick(_row(model_margin=4.0, book_margin=3.0), season=2026, week=6,
                          ranks={}, team_status={}) is None


def test_starting_qb_out_removes_the_spread_pick():
    row = _row()
    row = replace(row, forecast=replace(
        row.forecast, edge_withheld_reason="Away starting QB X listed out on the "
                                           "availability report"))
    assert bb.spread_pick(row, season=2026, week=6, ranks={}, team_status={}) is None


def test_angle_cites_the_injury_report():
    status = {"Away": {"starting_qb": "Star Passer", "starting_qb_status": "questionable",
                       "report": {"designations": []}}}
    pick = bb.spread_pick(_row(), season=2026, week=6, ranks={}, team_status=status)
    assert "Away QB Star Passer is listed questionable" in pick.angle
    assert "injury-report" in pick.tags


def test_total_pick():
    pick = bb.total_pick(_row(), season=2026, week=6, ranks=bb.fbs_ranks(FORMS), team_status={})
    assert pick.selection == "Over 52.5" and pick.edge == 7.5
    assert "projects 60.0 combined points" in pick.angle
    assert "Pace" in pick.angle


def _projection(**overrides):
    row = {
        "team": "Home", "opponent": "Away", "player_id": "rb-1", "player_name": "Lead Back",
        "team_id": "10", "event_id": "401", "kickoff": "2026-10-03T19:00:00Z", "games": 5,
        "stats": {"rush_yds": {"mean": 95.0, "p10": 50.0, "p50": 95.0, "p90": 140.0,
                               "q": [50.0, 70.0, 95.0, 118.0, 140.0],
                               "q_levels": [0.1, 0.25, 0.5, 0.75, 0.9]}},
        "usage": {"carries": 0.62}, "absorbs": ["Second Back"],
        "team_implied_points": 34.5, "team_margin": 14.0, "availability": None,
    }
    row.update(overrides)
    return row


def test_prop_pick_prices_the_distribution_against_a_de_vigged_line():
    quote = PropQuote("Home", "Away", "Lead Back", "rush_yds", 70.5, -115, -105,
                      "draftkings", None)
    picks = bb.prop_picks([_projection()], [quote], season=2026, week=6,
                          ranks=bb.fbs_ranks(FORMS))
    assert len(picks) == 1
    pick = picks[0]
    assert pick.side == "over" and pick.price == -115
    # Half-way from the de-vigged price (~0.53) toward the raw model (~0.75).
    assert 0.58 < pick.probability < 0.68
    assert "62% of team carries" in pick.angle
    assert "Inherits work from Second Back" in pick.angle
    assert "expected to lead" in pick.angle


def test_no_prop_pick_when_the_line_is_fair():
    quote = PropQuote("Home", "Away", "Lead Back", "rush_yds", 95.5, -110, -110,
                      "draftkings", None)
    assert bb.prop_picks([_projection()], [quote], season=2026, week=6) == []


def test_build_ranks_and_limits_each_family():
    rows = [_row(model_margin=3.0 + i * 2, book_margin=0.0) for i in range(8)]
    rows = [replace(r, forecast=replace(r.forecast, home=f"H{i}", away=f"A{i}"))
            for i, r in enumerate(rows)]
    picks = bb.build(rows, season=2026, week=6, forms=FORMS, team_status={})
    spreads = [p for p in picks if p.family == "spread"]
    assert len(spreads) == bb.LIMITS["spread"]
    assert [p.edge for p in spreads] == sorted((p.edge for p in spreads), reverse=True)


# -- ledger -------------------------------------------------------------------
def _box(players):
    return GameBox(event_id="401", season=2026, week=6, start="", completed=True,
                   neutral=False,
                   teams=[TeamGame("10", "Home", "Home", True, 30.0),
                          TeamGame("20", "Away", "Away", False, 20.0)],
                   players=players)


def test_best_bets_are_logged_before_kickoff_and_graded(tmp_path):
    path = tmp_path / "ledger.json"
    spread = bb.spread_pick(_row(), season=2026, week=6, ranks={}, team_status={})
    total = bb.total_pick(_row(), season=2026, week=6, ranks={}, team_status={})
    quote = PropQuote("Home", "Away", "Lead Back", "rush_yds", 70.5, -115, -105, "dk", None)
    prop = bb.prop_picks([_projection()], [quote], season=2026, week=6)[0]
    picks = [p.to_json() for p in (spread, total, prop)]
    ledger.update(season=2026, week=6, forecasts=[], season_games=[], best_bets=picks,
                  path=path, recorded_at=NOW)
    result = [{"week": 6, "homeTeam": "Home", "awayTeam": "Away",
               "homePoints": 31, "awayPoints": 17, "completed": True}]
    box = _box([PlayerLine(athlete_id="rb-1", name="Lead Back", team_id="10",
                           rush_car=20, rush_yds=88)])
    payload = ledger.update(season=2026, week=6, forecasts=[], season_games=result,
                            player_boxes=[box], best_bets=[], path=path,
                            recorded_at=KICKOFF + timedelta(days=1))
    graded = {b["family"]: b for b in payload["best_bets"]}
    assert graded["spread"]["result"] == "win"     # Home -3 won by 14
    assert graded["total"]["result"] == "loss"     # Over 52.5, 48 scored
    assert graded["prop"]["result"] == "win"       # 88 > 70.5
    summary = payload["summary"]["best_bets"]
    assert summary["spread"]["win"] == 1 and summary["total"]["units"] == -1.0
    assert summary["prop"]["units"] == round(100 / 115, 3)


def test_a_pick_dropped_before_kickoff_is_withdrawn_and_a_dnp_prop_is_void(tmp_path):
    path = tmp_path / "ledger.json"
    spread = bb.spread_pick(_row(), season=2026, week=6, ranks={}, team_status={}).to_json()
    quote = PropQuote("Home", "Away", "Lead Back", "rush_yds", 70.5, -115, -105, "dk", None)
    prop = bb.prop_picks([_projection()], [quote], season=2026, week=6)[0].to_json()
    ledger.update(season=2026, week=6, forecasts=[], season_games=[],
                  best_bets=[spread, prop], path=path, recorded_at=NOW)
    payload = ledger.update(season=2026, week=6, forecasts=[], season_games=[],
                            best_bets=[prop], path=path, recorded_at=NOW + timedelta(hours=6))
    assert [b["family"] for b in payload["best_bets"]] == ["prop"]
    payload = ledger.update(season=2026, week=6, forecasts=[], season_games=[],
                            player_boxes=[_box([PlayerLine(athlete_id="qb-9", name="QB",
                                                           team_id="10", pass_att=30)])],
                            path=path, recorded_at=KICKOFF + timedelta(days=1))
    assert payload["best_bets"][0]["result"] == "void"


def test_game_probabilities_give_the_model_only_its_earned_weight():
    pick = bb.spread_pick(_row(model_margin=13.0, book_margin=3.0), season=2026, week=6,
                          ranks={}, team_status={})
    assert pick.edge == 10.0
    assert 0.54 < pick.probability < 0.56       # not the ~74% a raw Normal would claim
    early = replace(_row(model_margin=13.0, book_margin=3.0).forecast, in_validated_regime=False)
    early_pick = bb.spread_pick(site.Row(early, "Sat", None, None, kickoff_utc=KICKOFF),
                                season=2026, week=3, ranks={}, team_status={})
    assert early_pick.probability < pick.probability
