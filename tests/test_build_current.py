import datetime as dt

from scripts import build_current


def _utc(year: int, month: int, day: int) -> dt.datetime:
    return dt.datetime(year, month, day, 12, tzinfo=dt.timezone.utc)


def test_week_fallback_tracks_labor_day_instead_of_fixed_august_date():
    # In 2026 Labor Day is September 7, so September 1 is still Week 1.
    assert build_current.current_week(_utc(2026, 9, 1), 2026) == 1
    assert build_current.current_week(_utc(2026, 9, 8), 2026) == 2


def test_week_fallback_handles_early_labor_day_season():
    # In 2025 Labor Day is September 1; the Week 2 slate begins that day.
    assert build_current.current_week(_utc(2025, 8, 30), 2025) == 1
    assert build_current.current_week(_utc(2025, 9, 2), 2025) == 2


def test_completed_week_is_not_published_during_its_calendar_grace_day(monkeypatch):
    """CFBD's calendar keeps week 1 current through Sep 8 2026, a day after the
    Sep 7 endDate. Every week-1 game was played by then, and a finished week has
    no live book lines left, so publishing it failed the deploy's odds guard.
    The schedule has to win over the calendar."""
    schedule = [
        {"week": 1, "completed": True},
        {"week": 1, "completed": True},
        {"week": 2, "completed": False},
        {"week": 3, "completed": False},
    ]
    monkeypatch.setattr(build_current.cfbd, "games", lambda season, **kw: schedule)
    assert build_current.first_unfinished_week(2026) == 2
    assert build_current.official_week(_utc(2026, 9, 8), 2026) == 2


def test_week_falls_back_to_the_calendar_when_the_schedule_is_unreadable(monkeypatch):
    """A CFBD outage must degrade to the calendar rule, not fail the build."""
    def boom(season, **kw):
        raise RuntimeError("cfbd down")

    monkeypatch.setattr(build_current.cfbd, "games", boom)
    monkeypatch.setattr(build_current.cfbd, "calendar", lambda season: [])
    assert build_current.first_unfinished_week(2026) is None
    assert build_current.official_week(_utc(2026, 9, 8), 2026) == 2


def test_season_over_keeps_the_last_week_rather_than_rolling_past_it(monkeypatch):
    monkeypatch.setattr(
        build_current.cfbd, "games",
        lambda season, **kw: [{"week": 1, "completed": True}, {"week": 2, "completed": True}],
    )
    monkeypatch.setattr(build_current.cfbd, "calendar", lambda season: [])
    assert build_current.first_unfinished_week(2026) is None
