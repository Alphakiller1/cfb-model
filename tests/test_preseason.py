import pytest

from cfbmodel import preseason as P


def test_blend_with_no_results_is_the_prior():
    prior = {"A": 10.0, "B": -5.0}
    assert P.blend(prior, {}, week=1) == prior


def test_blend_weights_the_season_in_as_weeks_pass():
    prior = {"A": 10.0}
    live = {"A": 0.0}
    early = P.blend(prior, live, week=2)["A"]
    later = P.blend(prior, live, week=5)["A"]
    # More completed weeks means more weight on observed results, i.e. closer to 0.
    assert abs(later) < abs(early)


def test_blend_saturates_rather_than_overshooting():
    prior = {"A": 10.0}
    live = {"A": 0.0}
    late = P.blend(prior, live, week=40)["A"]
    assert late == pytest.approx(0.0)


def test_blend_covers_teams_missing_from_either_side():
    out = P.blend({"A": 5.0}, {"B": 3.0}, week=3)
    assert set(out) == {"A", "B"}


def test_fcs_is_not_given_a_preseason_rating():
    """FCS is a pooled bucket in the ratings, not a team to project."""
    out = P.build(2026, {"__FCS__": -20.0, "Real Team": 5.0})
    assert "__FCS__" not in out


def test_centring_returns_zero_mean():
    centred, mean = P._centred({"a": 10.0, "b": 20.0, "c": 30.0})
    assert mean == pytest.approx(20.0)
    assert sum(centred.values()) == pytest.approx(0.0)


def test_centring_handles_empty_input():
    assert P._centred({}) == ({}, 0.0)


def test_missing_returning_production_uses_the_league_mean():
    """A team with no published figure must not be scored as returning zero."""
    assert P.DEFAULT_RETURNING == pytest.approx(0.533)
