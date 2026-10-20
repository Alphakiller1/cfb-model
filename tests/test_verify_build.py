import json

from scripts.verify_build import verify


def _manifest(**overrides) -> dict:
    payload = {
        "generated_at": "2026-09-18 06:10 UTC",
        "season": 2026,
        "week": 3,
        "odds": {
            "slate_games": 57, "slate_matched": 54, "requested_book": "draftkings",
            "state": "fresh",
        },
        "cfbd": [
            {"path": "/games?year=2026", "state": "stale_snapshot", "stale": True},
            {"path": "/lines?year=2026&week=3", "state": "error", "stale": False},
        ],
    }
    payload.update(overrides)
    return payload


def _write(tmp_path, payload):
    path = tmp_path / "build.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_stale_sources_block_a_release_by_default(tmp_path):
    errors = verify(_write(tmp_path, _manifest()))
    assert any("stale" in e for e in errors)
    assert any("failed" in e for e in errors)


def test_a_spent_allowance_publishes_a_disclosed_degraded_board(tmp_path):
    """The outage lasts until the provider resets the allowance.

    Refusing to publish through it is what pinned the 2026 board to a week that
    had already been played, while the slate, ratings and live market were all
    sound.
    """
    errors = verify(_write(tmp_path, _manifest(cfbd_quota_spent=True)))
    assert errors == []


def test_a_spent_allowance_still_blocks_an_unsound_board(tmp_path):
    """Quota or not, a board with no games or no market is not publishable."""
    empty_slate = _manifest(cfbd_quota_spent=True)
    empty_slate["odds"] = {"slate_games": 0, "slate_matched": 0,
                           "requested_book": "draftkings", "state": "fresh"}
    assert verify(_write(tmp_path, empty_slate))

    thin_book = _manifest(cfbd_quota_spent=True)
    thin_book["odds"] = {"slate_games": 57, "slate_matched": 4,
                         "requested_book": "draftkings", "state": "fresh"}
    assert any("coverage" in e for e in verify(_write(tmp_path, thin_book)))


def _odds(slate, matched, window, window_matched) -> dict:
    return {"slate_games": slate, "slate_matched": matched,
            "window_hours": 72, "window_games": window,
            "window_matched": window_matched,
            "requested_book": "draftkings", "state": "fresh"}


def test_the_sunday_rollover_publishes_before_the_weekend_is_priced(tmp_path):
    """Weeks 7 and 8 of 2026: 4 of 56 games priced on Sunday and Monday.

    The gate refused both days, so the public board, and the site's CFB desk
    that reads it, stayed on the week already played until Tuesday.
    """
    early = _manifest(cfbd=[], odds=_odds(56, 4, 3, 3))
    assert verify(_write(tmp_path, early)) == []

    nothing_due = _manifest(cfbd=[], odds=_odds(56, 0, 0, 0))
    assert verify(_write(tmp_path, nothing_due)) == []


def test_games_inside_the_window_are_still_held_to_the_floor(tmp_path):
    thin = _manifest(cfbd=[], odds=_odds(56, 30, 50, 10))
    assert any("coverage 10/50" in e for e in verify(_write(tmp_path, thin)))

    unpriced = _manifest(cfbd=[], odds=_odds(56, 0, 3, 0))
    assert any("no configured-sportsbook" in e for e in verify(_write(tmp_path, unpriced)))

    no_games = _manifest(cfbd=[], odds=_odds(0, 0, 0, 0))
    assert verify(_write(tmp_path, no_games))


def test_spent_allowance_ships_a_thin_but_usable_book(tmp_path):
    usable = _manifest(cfbd_quota_spent=True)
    usable["odds"] = {"slate_games": 57, "slate_matched": 24,
                      "requested_book": "draftkings", "state": "fresh"}
    assert verify(_write(tmp_path, usable)) == []
