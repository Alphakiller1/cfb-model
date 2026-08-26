import math

import pytest

from cfbmodel import ratings as R


def g(week, home, away, hp, ap, **kw):
    return R.Game(week=week, home=home, away=away, home_points=hp, away_points=ap, **kw)


def test_cap_is_monotone_and_bounded():
    assert R.cap_margin(10) < R.cap_margin(20) < R.cap_margin(60)
    assert R.cap_margin(1000) < R.BLOWOUT_CAP * 1.001


def test_cap_is_symmetric():
    assert R.cap_margin(-30) == pytest.approx(-R.cap_margin(30))


def test_cap_barely_touches_small_margins():
    """A one-score game should be essentially uncapped."""
    assert R.cap_margin(3) == pytest.approx(3.0, abs=0.05)


def test_blowout_is_compressed():
    """The point of the cap: a 56-point win is not worth 56."""
    assert R.cap_margin(56) < 35


def test_better_team_rates_higher():
    games = [g(1, "A", "B", 35, 10), g(2, "B", "C", 21, 20), g(3, "A", "C", 30, 14)]
    r = R.build(games)
    # A beat both comfortably at home, so A is clearly top. B is LAST, not second:
    # its only win was by one point at home, which is below the 4.53-point home
    # field, so B underperformed against C even while beating it.
    assert r["A"] > r["C"] > r["B"]


def test_ratings_are_centred_on_zero():
    games = [g(1, "A", "B", 35, 10), g(2, "B", "C", 21, 20), g(3, "A", "C", 30, 14)]
    r = R.build(games)
    fbs = [v for k, v in r.items() if k != R.FCS]
    assert sum(fbs) / len(fbs) == pytest.approx(0.0, abs=1e-9)


def test_home_field_is_removed_before_rating():
    """Two teams that split home wins by identical margins are equal.

    Recency weighting is switched off here so the symmetry is a clean test of
    home-field removal; with it on, the later game is weighted slightly more and
    the two ratings differ by ~0.07 by design.
    """
    games = [g(1, "A", "B", 24, 17), g(2, "B", "A", 24, 17)]
    r = R.build(games, halflife=None)
    assert r["A"] == pytest.approx(r["B"], abs=1e-9)


def test_recency_weighting_favours_the_more_recent_result():
    """The mirror of the test above: with recency on, the later win counts more."""
    games = [g(1, "A", "B", 24, 17), g(2, "B", "A", 24, 17)]
    r = R.build(games, halflife=1.0)
    assert r["B"] > r["A"]


def test_soft_home_schedule_is_not_rewarded():
    home_only = R.build([g(1, "A", "B", 21, 20), g(2, "A", "B", 21, 20)])
    # Winning by 1 at home is *below* a 4.53-point home field, so A should trail.
    assert home_only["A"] < home_only["B"]


def test_fcs_opponents_pool_into_one_rating():
    games = [g(1, "A", "Tiny", 60, 0, away_is_fbs=False),
             g(1, "B", "Other", 55, 3, away_is_fbs=False)]
    r = R.build(games)
    assert R.FCS in r
    assert "Tiny" not in r and "Other" not in r


def test_empty_input_is_empty_not_a_crash():
    assert R.build([]) == {}


def test_projected_margin_adds_home_field():
    r = {"A": 5.0, "B": 0.0}
    assert R.projected_margin(r, "A", "B") == pytest.approx(5.0 + R.HOME_FIELD_POINTS)
    assert R.projected_margin(r, "A", "B", neutral=True) == pytest.approx(5.0)


def test_projected_margin_none_for_unrated_team():
    assert R.projected_margin({"A": 5.0}, "A", "Nobody") is None


def test_win_probability_is_calibrated_at_the_extremes():
    assert R.win_probability(0.0) == 0.5
    assert R.win_probability(100.0) > 0.99
    assert R.win_probability(-100.0) < 0.01


def test_win_probability_is_symmetric():
    assert R.win_probability(7) + R.win_probability(-7) == pytest.approx(1.0)
