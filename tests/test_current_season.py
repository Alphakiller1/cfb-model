"""Current-season games enter ratings from scores, not the completed flag."""

from cfbmodel.cli import _current_season_games
from cfbmodel.sources import cfbd


def test_this_year_s_scores_count_before_cfbd_flags_the_game_completed(monkeypatch):
    """A lagged `completed` flag used to drop the current season from ratings."""

    def fake_games(season, **kwargs):
        return [{
            "week": kwargs["week"],
            "homeTeam": "Ohio State",
            "awayTeam": "Texas",
            "homePoints": 31,
            "awayPoints": 17,
            "completed": False,
            "homeClassification": "fbs",
            "awayClassification": "fbs",
            "neutralSite": False,
        }]

    monkeypatch.setattr(cfbd, "games", fake_games)
    games = _current_season_games(2026, 3)
    assert len(games) == 2
    assert {g.week for g in games} == {1, 2}
    assert games[0].home_points == 31
