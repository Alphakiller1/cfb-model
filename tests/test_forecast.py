import pytest

from cfbmodel import forecast as fc
from cfbmodel import matrix
from cfbmodel.authority import Action

RATINGS = {"A": 20.0, "B": 5.0}


def test_lam_zero_publishes_the_market():
    """The default is a measurement: the market beat the model, so publish it."""
    f = fc.game(home="A", away="B", team_ratings=RATINGS, market_margin=12.0)
    assert f.margin == pytest.approx(12.0)


def test_lam_one_publishes_the_model():
    f = fc.game(home="A", away="B", team_ratings=RATINGS, market_margin=12.0, lam=1.0)
    assert f.margin == pytest.approx(f.model_margin)


def test_edge_is_model_minus_market():
    f = fc.game(home="A", away="B", team_ratings=RATINGS, market_margin=12.0)
    assert f.edge_points == pytest.approx(f.model_margin - 12.0)


def test_without_a_price_there_is_no_edge_and_no_anchor():
    f = fc.game(home="A", away="B", team_ratings=RATINGS)
    assert f.edge_points is None
    assert f.market_anchored is False
    assert f.action is Action.AVOID


def test_unrated_team_yields_no_model_margin():
    f = fc.game(home="A", away="Nobody", team_ratings=RATINGS)
    assert f.model_margin is None


def test_partial_form_contributes_nothing_rather_than_half():
    """A team with incomplete form must not be scored as partially good."""
    half = matrix.TeamForm(success_rate=0.5)
    full = fc.game(home="A", away="B", team_ratings=RATINGS, home_form=half)
    bare = fc.game(home="A", away="B", team_ratings=RATINGS)
    assert full.model_margin == pytest.approx(bare.model_margin)


def test_early_weeks_are_flagged_out_of_regime():
    f = fc.game(home="A", away="B", team_ratings=RATINGS, week=1)
    assert f.in_validated_regime is False


def test_mid_season_is_in_regime():
    f = fc.game(home="A", away="B", team_ratings=RATINGS, week=10)
    assert f.in_validated_regime is True


def test_bias_correction_applies_without_form():
    """The correction fixes the RATINGS projection, so it must not switch off
    when efficiency form is missing -- that was weeks 1-4, the weakest regime."""
    bare = fc.game(home="A", away="B", team_ratings=RATINGS)
    raw = RATINGS["A"] - RATINGS["B"] + 4.53
    assert bare.model_margin == pytest.approx(raw + fc.RATING_BIAS_CORRECTION)


def test_bias_correction_also_applies_with_form():
    full = matrix.TeamForm(
        success_rate=0.45, ppa_per_play=0.15, points_per_opportunity=4.0,
        explosiveness=1.1, success_rate_allowed=0.42,
        points_per_opportunity_allowed=4.2, ppa_allowed=0.10,
        explosiveness_allowed=1.2, havoc_rate=0.15)
    f = fc.game(home="A", away="B", team_ratings=RATINGS, home_form=full, away_form=full)
    raw = RATINGS["A"] - RATINGS["B"] + 4.53
    # Identical forms cancel in the efficiency term, leaving only the correction.
    assert f.model_margin == pytest.approx(raw + fc.RATING_BIAS_CORRECTION)
