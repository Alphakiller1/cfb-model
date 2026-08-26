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


def test_partial_form_falls_back_to_the_ratings_regime():
    """Incomplete form must not be half-scored; it falls back to ratings-only."""
    half = matrix.TeamForm(off_ppa=0.5)
    partial = fc.game(home="A", away="B", team_ratings=RATINGS, home_form=half)
    bare = fc.game(home="A", away="B", team_ratings=RATINGS)
    assert partial.model_margin == pytest.approx(bare.model_margin)
    assert partial.used_efficiency is False


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


def _full_form():
    return matrix.TeamForm(
        off_ppa=0.15, off_successRate=0.45, off_explosiveness=1.1, off_stuffRate=0.18,
        def_ppa=0.10, def_successRate=0.42, def_explosiveness=1.2, def_stuffRate=0.20)


def test_full_regime_uses_the_jointly_fitted_coefficients():
    """With complete form the model switches regimes: identical forms cancel,
    leaving intercept + rating_margin * base rather than the fallback."""
    f = fc.game(home="A", away="B", team_ratings=RATINGS,
                home_form=_full_form(), away_form=_full_form())
    raw = RATINGS["A"] - RATINGS["B"] + 4.53
    c = matrix.COEFFICIENTS
    assert f.used_efficiency is True
    assert f.model_margin == pytest.approx(c["intercept"] + c["rating_margin"] * raw)


def test_the_two_regimes_are_not_the_same_number():
    """Guards against silently applying the joint fit where it was not measured."""
    full = fc.game(home="A", away="B", team_ratings=RATINGS,
                   home_form=_full_form(), away_form=_full_form())
    bare = fc.game(home="A", away="B", team_ratings=RATINGS)
    assert full.model_margin != pytest.approx(bare.model_margin)
