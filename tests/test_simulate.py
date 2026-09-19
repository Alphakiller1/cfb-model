"""Monte Carlo scoreline averaging."""

import pytest

from cfbmodel import simulate, totals


def test_the_mean_of_many_draws_recovers_the_centre():
    summary = simulate.average_outcomes(margin=7.0, total=52.0, n=8_000, seed=1)
    closed = totals.scoreline(7.0, 52.0, modelled=True, basis="closed")
    assert summary.home_score == pytest.approx(closed.home_score, abs=0.5)
    assert summary.away_score == pytest.approx(closed.away_score, abs=0.5)
    assert summary.n == 8_000


def test_a_positive_margin_wins_more_often_than_not():
    summary = simulate.average_outcomes(margin=10.0, total=50.0, n=5_000, seed=2)
    assert summary.win_probability > 0.6


def test_the_seed_makes_rebuilds_identical():
    a = simulate.average_outcomes(margin=3.0, total=48.0, n=2_000, seed=99)
    b = simulate.average_outcomes(margin=3.0, total=48.0, n=2_000, seed=99)
    assert a == b


def test_matchup_seed_is_stable_across_processes():
    assert simulate.matchup_seed("Ohio State", "Texas", season=2026, week=3) == (
        simulate.matchup_seed("Ohio State", "Texas", season=2026, week=3)
    )
    assert simulate.matchup_seed("Ohio State", "Texas", season=2026, week=3) != (
        simulate.matchup_seed("Texas", "Ohio State", season=2026, week=3)
    )


def test_zero_draws_are_refused():
    with pytest.raises(ValueError):
        simulate.average_outcomes(margin=0.0, total=50.0, n=0)
