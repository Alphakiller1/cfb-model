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


def _player_row(kickoff, *, pass_yds=250.0, player_id="qb-1"):
    return {
        "game_key": "Away @ Home", "event_id": "401", "team_id": "10",
        "kickoff": kickoff.isoformat(), "team": "Home", "opponent": "Away",
        "player_id": player_id, "player_name": "Test QB", "position": "QB",
        "model_version": "test", "anytime_td": 0.2,
        "stats": {"pass_yds": {"mean": pass_yds, "p10": 150.0},
                  "pass_att": {"mean": 32.0}},
    }


def _box(*players, completed=True):
    from cfbmodel.sources.espn_box import GameBox, PlayerLine, TeamGame

    return GameBox(
        event_id="401", season=2026, week=1, start="", completed=completed,
        neutral=False,
        teams=[TeamGame("10", "Home", "Home", True, 30.0),
               TeamGame("20", "Away", "Away", False, 20.0)],
        players=[PlayerLine(athlete_id=a, name=a, team_id=t, **stats)
                 for a, t, stats in players],
    )


def test_player_projection_is_logged_then_graded_from_the_box_score(tmp_path):
    path = tmp_path / "ledger.json"
    now = datetime(2026, 9, 3, 13, tzinfo=timezone.utc)
    kickoff = now + timedelta(days=2)
    ledger.update(season=2026, week=1, forecasts=[], season_games=[],
                  player_projections=[_player_row(kickoff, pass_yds=240.0)],
                  path=path, recorded_at=now)
    # A later pre-kickoff build re-projects: the pending row is replaced.
    payload = ledger.update(season=2026, week=1, forecasts=[], season_games=[],
                            player_projections=[_player_row(kickoff)],
                            path=path, recorded_at=now + timedelta(hours=6))
    assert len(payload["player_snapshots"]) == 1
    assert payload["player_snapshots"][0]["metrics"]["pass_yds"] == 250.0

    # Not final yet: stays pending.
    payload = ledger.update(season=2026, week=1, forecasts=[], season_games=[],
                            player_boxes=[_box(completed=False)],
                            path=path, recorded_at=kickoff + timedelta(hours=1))
    assert payload["player_snapshots"][0]["status"] == "pending"

    box = _box(("qb-1", "10", {"pass_att": 35.0, "pass_yds": 280.0, "rush_td": 1.0}),
               ("rb-9", "10", {"rush_car": 12.0}))
    payload = ledger.update(season=2026, week=1, forecasts=[], season_games=[],
                            player_boxes=[box], path=path,
                            recorded_at=kickoff + timedelta(days=1))
    row = payload["player_snapshots"][0]
    assert row["status"] == "graded" and row["played"] is True
    assert row["absolute_errors"] == {"pass_yds": 30.0, "pass_att": 3.0}
    assert row["actual_anytime_td"] == 1.0
    assert payload["summary"]["players"]["players_graded"] == 1

    # Graded rows are final: a late projection for the same player-game is ignored.
    payload = ledger.update(season=2026, week=1, forecasts=[], season_games=[],
                            player_projections=[_player_row(kickoff + timedelta(days=9))],
                            path=path, recorded_at=kickoff + timedelta(days=2))
    assert payload["player_snapshots"][0]["status"] == "graded"


def test_player_absent_from_a_published_box_score_grades_as_zero(tmp_path):
    path = tmp_path / "ledger.json"
    now = datetime(2026, 9, 3, 13, tzinfo=timezone.utc)
    kickoff = now + timedelta(days=2)
    ledger.update(season=2026, week=1, forecasts=[], season_games=[],
                  player_projections=[_player_row(kickoff)], path=path, recorded_at=now)
    payload = ledger.update(
        season=2026, week=1, forecasts=[], season_games=[],
        player_boxes=[_box(("qb-2", "10", {"pass_att": 30.0}))],
        path=path, recorded_at=kickoff + timedelta(days=1),
    )
    row = payload["player_snapshots"][0]
    assert row["played"] is False
    assert row["actual_metrics"] == {"pass_yds": 0.0, "pass_att": 0.0}


def test_a_season_of_quote_vintages_is_not_truncated(tmp_path):
    import json

    path = tmp_path / "ledger.json"
    rows = [{"snapshot_id": str(i), "season": 2026, "week": 1 + i // 700,
             "home": f"H{i}", "away": f"A{i}", "recorded_at": "", "status": "graded"}
            for i in range(6000)]
    path.write_text(json.dumps({"schema_version": "2.0.0", "snapshots": rows}),
                    encoding="utf-8")
    payload = ledger.update(season=2026, week=9, forecasts=[], season_games=[],
                            path=path, recorded_at=datetime(2026, 10, 30, tzinfo=timezone.utc))
    assert len(payload["snapshots"]) == 6000
    assert payload["snapshots"][0]["snapshot_id"] == "0"


def test_plays_log_is_one_row_per_game_with_the_side_taken(tmp_path):
    path = tmp_path / "ledger.json"
    now = datetime(2026, 9, 3, 13, tzinfo=timezone.utc)
    kickoff = now + timedelta(days=2)
    ledger.update(season=2026, week=1, forecasts=[(_forecast(), kickoff)],
                  season_games=[], path=path, recorded_at=now)
    payload = ledger.update(
        season=2026, week=1, forecasts=[], season_games=[{
            "week": 1, "homeTeam": "Home", "awayTeam": "Away",
            "homePoints": 35, "awayPoints": 24, "completed": True,
        }], path=path, recorded_at=now + timedelta(days=3),
    )
    plays = ledger.plays(payload, season=2026)
    assert len(plays) == 1
    assert plays[0]["status"] == "graded"
    assert plays[0]["ats_side"] == "Home" and plays[0]["ats_result"] == "win"
