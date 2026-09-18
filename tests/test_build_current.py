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


def _slate(week: int, day: int, *, completed: bool) -> list[dict]:
    """Two games for `week`, kicking off on September `day`."""
    return [
        {"week": week, "completed": completed,
         "startDate": f"2026-09-{day:02d}T23:00:00.000Z"},
        {"week": week, "completed": completed,
         "startDate": f"2026-09-{day + 1:02d}T00:30:00.000Z"},
    ]


def test_stale_completion_flags_do_not_pin_the_board_to_a_played_week(monkeypatch):
    """The 2026 outage: the newest schedule available predated week 2's kickoff.

    When the key's call allowance ran out, every build fell back to a snapshot
    captured before week 2 was played, so its `completed` flags all read False
    and "first unfinished week" answered 2 for days while week 3 was the live
    slate. The kickoffs in that same snapshot dated the weeks correctly.
    """
    schedule = (_slate(1, 5, completed=True)
                + _slate(2, 11, completed=False)   # stale: actually played
                + _slate(3, 19, completed=False))
    monkeypatch.setattr(build_current.cfbd, "games", lambda season, **kw: schedule)
    monkeypatch.setattr(
        build_current.dt, "datetime",
        type("Clock", (dt.datetime,),
             {"now": staticmethod(lambda tz=None: _utc(2026, 9, 18))}),
    )
    assert build_current.first_unfinished_week(2026) == 3


def test_kickoffs_keep_the_board_on_the_week_being_played(monkeypatch):
    """Mid-week, before the slate kicks off, the board stays on that week."""
    schedule = (_slate(1, 5, completed=True)
                + _slate(2, 11, completed=True)
                + _slate(3, 19, completed=False))
    monkeypatch.setattr(build_current.cfbd, "games", lambda season, **kw: schedule)
    monkeypatch.setattr(
        build_current.dt, "datetime",
        type("Clock", (dt.datetime,),
             {"now": staticmethod(lambda tz=None: _utc(2026, 9, 16))}),
    )
    assert build_current.first_unfinished_week(2026) == 3


def test_season_over_stays_on_the_final_week(monkeypatch):
    schedule = _slate(1, 5, completed=True) + _slate(2, 11, completed=True)
    monkeypatch.setattr(build_current.cfbd, "games", lambda season, **kw: schedule)
    monkeypatch.setattr(
        build_current.dt, "datetime",
        type("Clock", (dt.datetime,),
             {"now": staticmethod(lambda tz=None: _utc(2026, 12, 20))}),
    )
    assert build_current.first_unfinished_week(2026) == 2
