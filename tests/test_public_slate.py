"""Public CFB slate is descriptive only."""

from datetime import datetime, timezone
from types import SimpleNamespace

from cfbmodel import export, matrix


def _form(off_ppa=0.2, stuff=0.2):
    return matrix.TeamForm(
        off_ppa=off_ppa, off_successRate=0.45, off_explosiveness=1.3,
        off_stuffRate=stuff, def_ppa=0.15, def_successRate=0.40,
        def_explosiveness=1.1, def_stuffRate=0.22, plays=68.0,
    )


def _forecast(away="Away U", home="Home U"):
    return SimpleNamespace(home=home, away=away, neutral=False)


def test_public_slate_ranks_form_and_drops_prices():
    forms = {"Home U": _form(0.3, 0.1), "Away U": _form(0.1, 0.3)}
    kickoff = datetime(2026, 9, 19, 19, 0, tzinfo=timezone.utc)
    payload = export.public_slate(
        season=2026, week=4,
        slate_games=[{
            "homeTeam": "Home U", "awayTeam": "Away U",
            "completed": False, "neutralSite": False, "venueId": 1,
        }],
        forecasts=[(_forecast(), kickoff)],
        forms=forms,
        season_games=[{
            "homeTeam": "Home U", "awayTeam": "Away U",
            "completed": True, "homePoints": 31, "awayPoints": 17,
        }],
    )
    assert payload["schema"] == "chase-public-slate/1"
    game = payload["games"][0]
    assert game["away_name"] == "Away U"
    assert game["home_record"] == "1-0"
    assert game["away_record"] == "0-1"
    away_ppa = game["away_form"]["rates"]["off_ppa"]
    home_ppa = game["home_form"]["rates"]["off_ppa"]
    assert home_ppa["rank"] == 1
    assert away_ppa["rank"] == 2
    assert away_ppa["of"] == 2
    assert away_ppa["better"] == "high"
    stuff = game["away_form"]["rates"]["off_stuffRate"]
    assert stuff["better"] == "low"
    assert stuff["rank"] == 2  # 0.3 stuffed is worse than 0.1
    leaked = export._PUBLIC_FORBIDDEN & export._walk_keys(payload)
    assert not leaked


def test_public_slate_omits_incomplete_form():
    payload = export.public_slate(
        season=2026, week=1,
        slate_games=[{"homeTeam": "A", "awayTeam": "B"}],
        forecasts=[],
        forms={"A": matrix.TeamForm()},
    )
    game = payload["games"][0]
    assert "home_form" not in game
    assert "away_form" not in game
