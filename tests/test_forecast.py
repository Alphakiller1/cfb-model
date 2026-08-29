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


def test_market_gap_is_model_minus_market():
    f = fc.game(home="A", away="B", team_ratings=RATINGS, market_margin=12.0)
    assert f.market_gap == pytest.approx(f.model_margin - 12.0)


def test_the_preseason_path_reports_a_gap_but_withholds_the_edge():
    """The ratings-only estimate conditions on far less than the price does, so
    the difference is the information gap rather than a disagreement."""
    f = fc.game(home="A", away="B", team_ratings=RATINGS, market_margin=12.0)
    assert f.used_efficiency is False
    assert f.market_gap is not None
    assert f.edge_points is None
    assert "information gap" in f.edge_withheld_reason


def test_the_full_regime_publishes_the_edge():
    form = matrix.TeamForm(**{field: 0.5 for field in matrix.TeamForm.FIELDS})
    f = fc.game(home="A", away="B", team_ratings=RATINGS, market_margin=12.0,
                home_form=form, away_form=form, week=8)
    assert f.used_efficiency is True
    assert f.edge_points == pytest.approx(f.market_gap)
    assert f.edge_withheld_reason is None


def test_an_early_week_withholds_the_edge_even_with_form():
    form = matrix.TeamForm(**{field: 0.5 for field in matrix.TeamForm.FIELDS})
    f = fc.game(home="A", away="B", team_ratings=RATINGS, market_margin=12.0,
                home_form=form, away_form=form, week=2)
    assert f.edge_points is None
    assert "validated regime" in f.edge_withheld_reason


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


def test_week_one_applies_held_out_scale_calibration():
    f = fc.game(home="A", away="B", team_ratings=RATINGS, week=1)
    raw = RATINGS["A"] - RATINGS["B"] + 4.53 + fc.RATING_BIAS_CORRECTION
    assert f.raw_model_margin == pytest.approx(raw)
    assert f.model_margin == pytest.approx(1.7236 + 1.5333 * raw)


def test_week_two_does_not_inherit_week_one_calibration():
    f = fc.game(home="A", away="B", team_ratings=RATINGS, week=2)
    assert f.model_margin == pytest.approx(f.raw_model_margin)


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


def _paced_form():
    return matrix.TeamForm(
        off_ppa=0.20, off_successRate=0.46, off_explosiveness=1.20, off_stuffRate=0.17,
        def_ppa=0.12, def_successRate=0.42, def_explosiveness=1.18, def_stuffRate=0.19,
        drives=12.0, plays=68.0)


def test_forecast_carries_a_projected_scoreline():
    f = fc.game(home="A", away="B", team_ratings=RATINGS,
                home_form=_paced_form(), away_form=_paced_form())
    assert f.projected_total is not None
    assert f.projected_home_score is not None
    assert f.total_modelled is True


def test_scoreline_reflects_the_model_margin_not_the_published_one():
    """At lam = 0 the published margin is the market. A 'projected score' built
    from it would be the market's projection wearing the model's label."""
    f = fc.game(home="A", away="B", team_ratings=RATINGS,
                home_form=_paced_form(), away_form=_paced_form(),
                market_margin=1.0)
    assert f.margin == pytest.approx(1.0)          # published = market
    spread = f.projected_home_score - f.projected_away_score
    assert spread == pytest.approx(f.model_margin)  # scoreline follows the model
    assert spread != pytest.approx(1.0)


def test_total_edge_is_model_minus_market():
    f = fc.game(home="A", away="B", team_ratings=RATINGS,
                home_form=_paced_form(), away_form=_paced_form(),
                market_total=50.0)
    assert f.total_edge == pytest.approx(f.projected_total - 50.0)


def test_total_edge_is_none_without_a_market_total():
    f = fc.game(home="A", away="B", team_ratings=RATINGS,
                home_form=_paced_form(), away_form=_paced_form())
    assert f.total_edge is None
