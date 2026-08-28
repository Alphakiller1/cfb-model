"""Roster, coaching, and venue features.

Every test patches the CFBD client, so the suite still runs without a key.
"""

from unittest import mock

import pytest

from cfbmodel import coaching, fitting, roster, venue

PORTAL = [
    # A four-star quarterback leaving X for Y.
    {"origin": "X", "destination": "Y", "position": "QB", "stars": 4, "rating": 0.94},
    # A three-star leaving Y, unsigned — a loss to Y with no matching arrival.
    {"origin": "Y", "destination": None, "position": "WR", "stars": 3},
    # No rating and no stars: unusable, must not be silently counted as zero.
    {"origin": "X", "destination": "Z", "position": "OL"},
]

RETURNING = [
    {"team": "X", "percentPPA": 0.71, "percentPassingPPA": 0.95},
    {"team": "Y", "percentPPA": 0.71, "percentPassingPPA": 0.05},
    {"team": "Z", "percentPPA": 0.40, "percentPassingPPA": None},
]

COACHES = [
    {"firstName": "Ann", "lastName": "Long",
     "seasons": [{"school": "X", "year": 2023}, {"school": "X", "year": 2024},
                 {"school": "X", "year": 2025}]},
    {"firstName": "Bo", "lastName": "Hired",
     "seasons": [{"school": "W", "year": 2023}, {"school": "W", "year": 2024},
                 {"school": "Y", "year": 2025}]},
    {"firstName": "Cy", "lastName": "Back",
     "seasons": [{"school": "Z", "year": 2018}, {"school": "Z", "year": 2025}]},
]


class TestPortal:
    def test_net_sums_quality_not_bodies(self):
        with mock.patch.object(roster.cfbd, "portal", return_value=PORTAL):
            net = roster.portal_net(2025)
        # X loses a 0.94 QB; the unrated OL contributes nothing.
        assert net["X"] == pytest.approx(-0.94)
        # Y gains 0.94 and loses a 3-star (0.85) at half weight for being unsigned.
        assert net["Y"] == pytest.approx(0.94 - 0.85 * roster.UNSIGNED_DEPARTURE_WEIGHT)

    def test_an_unrated_entry_is_dropped_not_zeroed(self):
        with mock.patch.object(roster.cfbd, "portal", return_value=PORTAL):
            assert "Z" not in roster.portal_net(2025)

    def test_churn_counts_every_entry_including_unrated(self):
        with mock.patch.object(roster.cfbd, "portal", return_value=PORTAL):
            counts = roster.portal_counts(2025)
        assert counts["X"] == (0, 2)
        assert counts["Y"] == (1, 1)
        assert counts["Z"] == (1, 0)

    def test_stars_are_the_fallback_when_rating_is_absent(self):
        assert roster._rating({"rating": 0.91, "stars": 2}) == pytest.approx(0.91)
        assert roster._rating({"stars": 5}) == pytest.approx(0.98)
        assert roster._rating({"position": "QB"}) is None

    def test_a_feed_failure_degrades_to_empty_not_to_zeros(self):
        with mock.patch.object(roster.cfbd, "portal", side_effect=RuntimeError("503")):
            assert roster.portal_net(2025) == {}


class TestReturning:
    def test_the_quarterback_share_is_split_out_of_the_blend(self):
        with mock.patch.object(roster.cfbd, "returning_production", return_value=RETURNING):
            data = roster.returning(2025)
        # X and Y have IDENTICAL blended production and opposite QB situations.
        assert data["X"][0] == data["Y"][0] == pytest.approx(0.71)
        assert data["X"][1] == pytest.approx(0.95)
        assert data["Y"][1] == pytest.approx(0.05)

    def test_a_null_quarterback_share_stays_none(self):
        with mock.patch.object(roster.cfbd, "returning_production", return_value=RETURNING):
            assert roster.returning(2025)["Z"][1] is None


class TestCentring:
    def test_missing_entries_are_dropped_rather_than_treated_as_average(self):
        centred = roster.centred({"a": 1.0, "b": 3.0, "c": None})
        assert set(centred) == {"a", "b"}
        assert centred["a"] == pytest.approx(-1.0)
        assert centred["b"] == pytest.approx(1.0)

    def test_all_missing_yields_nothing(self):
        assert roster.centred({"a": None, "b": None}) == {}


class TestCoaching:
    def test_a_continuing_coach_is_not_a_first_year(self):
        with mock.patch.object(coaching.cfbd, "coaches", return_value=COACHES):
            flags = coaching.head_coaches(2025)
        assert flags["X"].first_year is False
        assert flags["X"].seasons_at_school == 3

    def test_a_new_hire_is_a_first_year_at_the_new_school(self):
        with mock.patch.object(coaching.cfbd, "coaches", return_value=COACHES):
            flags = coaching.head_coaches(2025)
        assert flags["Y"].first_year is True
        assert flags["Y"].coach == "Bo Hired"

    def test_a_coach_returning_after_an_absence_is_a_first_year(self):
        """A naive year-diff would inherit the 2018 run and call this continuity."""
        with mock.patch.object(coaching.cfbd, "coaches", return_value=COACHES):
            assert coaching.head_coaches(2025)["Z"].first_year is True

    def test_a_feed_failure_degrades_to_empty(self):
        with mock.patch.object(coaching.cfbd, "coaches", side_effect=RuntimeError):
            assert coaching.head_coaches(2025) == {}


class TestVenue:
    LARAMIE = venue.Venue(1, "War Memorial", 41.312, -105.569, 7220, False)
    ROSE_BOWL = venue.Venue(2, "Rose Bowl", 34.161, -118.168, 860, False)
    MIAMI = venue.Venue(3, "Hard Rock", 25.958, -80.239, 7, False)
    UNKNOWN = venue.Venue(4, "Unlocated", None, None, None, None)

    def test_distance_is_sane(self):
        miles = venue.distance_miles(self.LARAMIE, self.ROSE_BOWL)
        assert 800 < miles < 900

    def test_eastward_travel_is_positive_and_westward_negative(self):
        """Signed on purpose: an absolute value would cancel the asymmetry."""
        assert venue.timezone_shift(self.MIAMI, self.ROSE_BOWL) > 0     # LA -> Miami
        assert venue.timezone_shift(self.ROSE_BOWL, self.MIAMI) < 0     # Miami -> LA
        assert venue.timezone_shift(self.MIAMI, self.ROSE_BOWL) == pytest.approx(
            -venue.timezone_shift(self.ROSE_BOWL, self.MIAMI)
        )

    def test_elevation_gain_is_the_visitors_climb(self):
        assert venue.elevation_gain(self.LARAMIE, self.MIAMI) == pytest.approx(7213)
        assert venue.elevation_gain(self.MIAMI, self.LARAMIE) == pytest.approx(-7213)

    def test_a_neutral_site_has_no_home_field_features(self):
        assert venue.home_field_features(self.LARAMIE, self.ROSE_BOWL, neutral=True) == {}

    def test_an_unknown_venue_yields_nothing_rather_than_zeros(self):
        features = venue.home_field_features(self.LARAMIE, self.UNKNOWN)
        assert "elevation_gain_kft" not in features
        assert "travel_kmiles" not in features

    def test_features_are_scaled_for_readable_coefficients(self):
        features = venue.home_field_features(self.LARAMIE, self.ROSE_BOWL)
        assert features["elevation_gain_kft"] == pytest.approx(6.36)
        assert 0.8 < features["travel_kmiles"] < 0.9

    def test_home_venue_is_taken_from_where_a_team_actually_hosts(self):
        games = [
            {"homeTeam": "X", "venueId": 7, "neutralSite": False},
            {"homeTeam": "X", "venueId": 7, "neutralSite": False},
            {"homeTeam": "X", "venueId": 9, "neutralSite": False},
            {"homeTeam": "X", "venueId": 99, "neutralSite": True},   # ignored
        ]
        with mock.patch.object(venue.cfbd, "games", return_value=games):
            assert venue.team_venues(2025)["X"] == 7


class TestFittingHarness:
    def test_it_recovers_known_coefficients(self):
        rows = [{"a": float(i % 7), "b": float(i % 3)} for i in range(200)]
        target = [1.5 + 2.0 * r["a"] - 0.5 * r["b"] for r in rows]
        fitted = fitting.ols(rows, target, ["a", "b"])
        assert fitted["a"] == pytest.approx(2.0, abs=1e-3)
        assert fitted["b"] == pytest.approx(-0.5, abs=1e-3)

    def test_a_useless_feature_does_not_lower_held_out_error(self):
        observations = []
        for season in (2021, 2022, 2023, 2024):
            for i in range(80):
                signal = float((i * 7 + season) % 11)
                observations.append((
                    season,
                    {"signal": signal, "noise": float((i * 13) % 5)},
                    3.0 + 1.5 * signal,
                ))
        good = fitting.leave_one_season_out("signal", ["signal"], observations)
        padded = fitting.leave_one_season_out("both", ["signal", "noise"], observations)
        assert good.mae < 0.01
        assert padded.mae >= good.mae - 1e-6

    def test_too_few_observations_fails_loudly(self):
        with pytest.raises(fitting.SingularSystem):
            fitting.ols([{"a": 1.0}, {"a": 2.0}], [1.0, 2.0], ["a"])

    def test_one_season_cannot_be_held_out(self):
        with pytest.raises(ValueError):
            fitting.leave_one_season_out("x", ["a"], [(2021, {"a": 1.0}, 1.0)] * 10)
