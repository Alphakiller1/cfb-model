"""A settled week's advanced rows are cached for good; a fresh week is not."""

from datetime import datetime, timedelta, timezone

from cfbmodel.sources import cfbd

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def _schedule(days_ago: float, completed: bool = True):
    start = (NOW - timedelta(days=days_ago)).isoformat()
    return [{"week": 3, "completed": completed, "startDate": start},
            {"week": 3, "completed": True, "startDate": (NOW - timedelta(days=days_ago + 1)).isoformat()}]


def _patch(monkeypatch, schedule):
    monkeypatch.setattr(cfbd, "_utc_now", lambda: NOW)
    monkeypatch.setattr(cfbd, "_current_season", lambda: 2026)
    calls = []

    def fake_get(path, *, cacheable=True, **kwargs):
        calls.append((path, cacheable))
        return schedule if path.startswith("/games") else [{"team": "A"}]

    monkeypatch.setattr(cfbd, "get", fake_get)
    return calls


def test_a_settled_week_goes_to_the_immutable_cache(monkeypatch):
    calls = _patch(monkeypatch, _schedule(days_ago=6))
    cfbd.game_advanced_stats(2026, week=3)
    assert ("/stats/game/advanced?year=2026&week=3&excludeGarbageTime=true", True) in calls


def test_a_week_inside_the_revision_window_is_refetched(monkeypatch):
    calls = _patch(monkeypatch, _schedule(days_ago=1))
    cfbd.game_advanced_stats(2026, week=3)
    assert ("/stats/game/advanced?year=2026&week=3&excludeGarbageTime=true", False) in calls


def test_an_unfinished_week_is_refetched(monkeypatch):
    calls = _patch(monkeypatch, _schedule(days_ago=6, completed=False))
    cfbd.game_advanced_stats(2026, week=3)
    assert ("/stats/game/advanced?year=2026&week=3&excludeGarbageTime=true", False) in calls
