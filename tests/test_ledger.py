from datetime import datetime, timedelta, timezone

from cfbmodel import authority, forecast, ledger


def _forecast():
    return forecast.game(
        home="Home", away="Away", team_ratings={"Home": 10.0, "Away": 0.0},
        market_margin=6.0, market_total=50.0,
        preseason_total=54.0, week=1,
        book=type("Book", (), {
            "book_title": "DraftKings", "home_margin": 5.5, "total": 51.5,
            "last_update": "2026-09-03T12:00:00Z",
            "commence_time": "2026-09-05T16:00:00Z",
        })(),
        authority=authority.current(),
    )


def test_ledger_records_before_kickoff_then_grades_without_duplicate(tmp_path):
    path = tmp_path / "ledger.json"
    now = datetime(2026, 9, 3, 13, tzinfo=timezone.utc)
    kickoff = now + timedelta(days=2)
    first = ledger.update(
        season=2026, week=1, forecasts=[(_forecast(), kickoff)],
        season_games=[], path=path, recorded_at=now,
    )
    assert len(first["snapshots"]) == 1
    second = ledger.update(
        season=2026, week=1, forecasts=[(_forecast(), kickoff)],
        season_games=[{
            "week": 1, "homeTeam": "Home", "awayTeam": "Away",
            "homePoints": 35, "awayPoints": 24, "completed": True,
        }], path=path, recorded_at=now + timedelta(days=3),
    )
    assert len(second["snapshots"]) == 1
    assert second["summary"]["games_graded"] == 1
    assert second["summary"]["ats"] == {"win": 1, "loss": 0, "push": 0}


def test_ledger_refuses_to_record_after_kickoff(tmp_path):
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    payload = ledger.update(
        season=2026, week=1,
        forecasts=[(_forecast(), now - timedelta(hours=1))],
        season_games=[], path=tmp_path / "ledger.json", recorded_at=now,
    )
    assert payload["snapshots"] == []
