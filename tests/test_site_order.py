"""Board ordering and site/neutral labelling."""

from datetime import datetime, timezone

import pytest

from cfbmodel import authority as auth_mod
from cfbmodel import forecast as fc
from cfbmodel import ratings, site

RATINGS = {"Alpha": 12.0, "Bravo": 3.0, "Charlie": -4.0, "Delta": 1.0}


def _row(away: str, home: str, raw: str | None, *, neutral: bool = False) -> site.Row:
    forecast = fc.game(
        home=home, away=away, team_ratings=RATINGS, neutral=neutral,
        market_margin=7.0, authority=auth_mod.current(), week=6,
    )
    moment = site._parse_kickoff(raw)
    return site.Row(forecast, site._kickoff_label(moment), None, None, kickoff_utc=moment)


def _ordered(rows: list[site.Row]) -> list[site.Row]:
    return sorted(rows, key=lambda r: (r.sort_key, r.forecast.away, r.forecast.home))


class TestParse:
    @pytest.mark.parametrize("raw", [None, "", "   ", "not-a-date"])
    def test_unparseable_is_none(self, raw):
        assert site._parse_kickoff(raw) is None
        assert site._kickoff_label(site._parse_kickoff(raw)) is None

    def test_z_suffix_is_utc(self):
        assert site._parse_kickoff("2026-09-05T23:30:00.000Z") == datetime(
            2026, 9, 5, 23, 30, tzinfo=timezone.utc
        )

    def test_naive_input_is_assumed_utc(self):
        assert site._parse_kickoff("2026-09-05T23:30:00").tzinfo is timezone.utc

    def test_date_only_reads_as_tba_not_as_midnight(self):
        # Midnight UTC is the feed saying "no time", not a 8pm ET Friday kickoff.
        label = site._kickoff_label(site._parse_kickoff("2026-09-05"))
        assert label == "Sat Sep 5 · time TBA"


class TestOrder:
    def test_board_is_chronological(self):
        rows = _ordered([
            _row("Bravo", "Alpha", "2026-09-05T23:30:00.000Z"),
            _row("Charlie", "Delta", "2026-08-29T16:00:00.000Z"),
            _row("Delta", "Bravo", "2026-09-03T00:15:00.000Z"),
        ])
        assert [r.forecast.home for r in rows] == ["Delta", "Bravo", "Alpha"]

    def test_order_ignores_edge_size(self):
        """The old board sorted by |edge|; the early game must still come first."""
        early = _row("Charlie", "Alpha", "2026-08-29T16:00:00.000Z")   # big gap
        late = _row("Delta", "Bravo", "2026-09-05T23:30:00.000Z")      # small gap
        assert abs(early.forecast.market_gap) > abs(late.forecast.market_gap)
        assert _ordered([late, early])[0] is early

    def test_unscheduled_games_sort_last(self):
        rows = _ordered([
            _row("Alpha", "Bravo", None),
            _row("Charlie", "Delta", "2026-09-05T23:30:00.000Z"),
        ])
        assert rows[-1].kickoff_utc is None

    def test_late_saturday_game_keeps_saturday_and_sorts_after_the_early_one(self):
        """02:30 UTC Sunday is 10:30pm ET Saturday -- same slate day, later slot."""
        early = _row("Alpha", "Bravo", "2026-09-05T16:00:00.000Z")
        late = _row("Charlie", "Delta", "2026-09-06T02:30:00.000Z")
        assert _ordered([late, early]) == [early, late]
        if site._EASTERN is not None:
            assert late.kickoff.startswith("Sat Sep 5")


class TestNeutralSite:
    def test_neutral_drops_exactly_the_home_field_points(self):
        home_game = ratings.projected_margin(RATINGS, "Alpha", "Bravo", neutral=False)
        neutral_game = ratings.projected_margin(RATINGS, "Alpha", "Bravo", neutral=True)
        assert home_game - neutral_game == pytest.approx(ratings.HOME_FIELD_POINTS)

    def test_card_says_vs_and_tags_the_site(self):
        card = site._game_card(_row("Alpha", "Bravo", "2026-09-05T23:30:00.000Z",
                                    neutral=True), 2026, RATINGS)
        assert ">VS<" in card and ">AT<" not in card
        assert "neutral site · no home field" in card

    def test_home_game_card_says_at_and_carries_no_neutral_tag(self):
        card = site._game_card(_row("Alpha", "Bravo", "2026-09-05T23:30:00.000Z"),
                               2026, RATINGS)
        assert ">AT<" in card and ">VS<" not in card
        assert "neutral site" not in card
