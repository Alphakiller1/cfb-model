from __future__ import annotations

from types import SimpleNamespace

from cfbmodel import export, player_props as pp
from cfbmodel.sources.espn_box import GameBox, PlayerLine, TeamGame


def _box(event, week, home_players, away_players, *, season=2026, home="1", away="2"):
    players = [PlayerLine(athlete_id=a, name=a, team_id=home, **stats)
               for a, stats in home_players] + \
              [PlayerLine(athlete_id=a, name=a, team_id=away, **stats)
               for a, stats in away_players]
    return GameBox(event_id=event, season=season, week=week, start=f"{season}-09-{week:02d}",
                   completed=True, neutral=False,
                   teams=[TeamGame(home, "Home U", "Home U", True, 30.0),
                          TeamGame(away, "Away St", "Away St", False, 20.0)],
                   players=players)


QB = {"pass_att": 30, "pass_cmp": 20, "pass_yds": 250, "pass_td": 2, "rush_car": 5, "rush_yds": 20}
RB = {"rush_car": 18, "rush_yds": 90, "rush_td": 1, "rec": 2, "rec_yds": 15}
WR = {"rec": 6, "rec_yds": 80, "rec_td": 1}
OPP = [("oqb", {"pass_att": 28, "pass_cmp": 17, "pass_yds": 210, "rush_car": 30, "rush_yds": 120})]


def _history(last_week_players):
    boxes = [_box(f"e{w}", w, [("qb", QB), ("rb", RB), ("wr", WR)], OPP) for w in (1, 2, 3)]
    boxes.append(_box("e4", 4, last_week_players, OPP))
    return pp.History(boxes)


ENV = pp.Environment(margin=7.0, total=55.0)


def test_team_totals_sum_player_lines():
    box = _box("e", 1, [("qb", QB), ("rb", RB), ("wr", WR)], OPP)
    totals = pp.team_totals(box, "1")
    assert totals["pass_att"] == 30 and totals["rush_car"] == 23 and totals["pass_yds"] == 250


def test_only_players_from_the_last_game_are_projected():
    hist = _history([("qb", QB), ("wr", WR)])        # the back sat out week 4
    names = {p.athlete_id for p in pp.project_team(hist, "1", "2", 2026, 5, ENV)}
    assert "rb" not in names and {"qb", "wr"} <= names


def test_one_quarterback_and_positions_inferred_from_usage():
    hist = _history([("qb", QB), ("rb", RB), ("wr", WR)])
    projections = {p.athlete_id: p for p in pp.project_team(hist, "1", "2", 2026, 5, ENV)}
    assert projections["qb"].position == "QB"
    assert projections["rb"].position == "RB"
    assert projections["wr"].position == "WR"
    assert set(projections["wr"].metrics) == {"rec", "rec_yds"}
    assert projections["qb"].metrics["pass_att"] > 20


def test_projection_follows_the_team_environment():
    hist = _history([("qb", QB), ("rb", RB), ("wr", WR)])
    fav = {p.athlete_id: p for p in pp.project_team(hist, "1", "2", 2026, 5,
                                                   pp.Environment(margin=21.0, total=55.0))}
    dog = {p.athlete_id: p for p in pp.project_team(hist, "1", "2", 2026, 5,
                                                   pp.Environment(margin=-21.0, total=55.0))}
    # Game script: a big favourite runs more than a big underdog.
    assert fav["rb"].metrics["rush_yds"] > dog["rb"].metrics["rush_yds"]


def test_count_stats_carry_a_pmf_and_yards_a_ladder():
    td = pp.distribution("pass_td", 1.8)
    assert abs(sum(td["pmf"].values()) - 1.0) < 0.01
    assert 0.0 < pp.over_probability(td, 1.5) < 1.0
    yards = pp.distribution("pass_yds", 250.0)
    assert yards["p10"] < yards["p50"] < yards["p90"]
    assert pp.over_probability(yards, yards["p50"]) == 0.5
    assert pp.over_probability(yards, 10.0) == 0.9


def test_export_never_fails_the_board_when_the_player_layer_breaks(monkeypatch):
    def boom(*_):
        raise RuntimeError("ESPN down")
    monkeypatch.setattr(pp, "build_slate", boom)
    rows, status = export.player_projections(2026, 5, [])
    assert rows == [] and "ESPN down" in status["issues"][0]


def test_environment_prefers_the_book_then_the_forecast():
    forecast = SimpleNamespace(book_margin=-3.5, book_total=48.5, margin=-2.0,
                               model_margin=1.0, projected_total=50.0, market_total=49.0)
    env, source = pp._environment_for(forecast, home=False)
    assert env.margin == 3.5 and env.total == 48.5 and "DraftKings" in source
    forecast.book_margin = None
    env, source = pp._environment_for(forecast, home=True)
    assert env.margin == -2.0 and env.total == 50.0
