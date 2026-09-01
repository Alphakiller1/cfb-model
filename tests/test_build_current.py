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
