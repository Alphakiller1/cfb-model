"""Calibration diagnostics and the edge-publication rule."""

import random

import pytest

from cfbmodel import calibration as cal
from cfbmodel import forecast as fc
from cfbmodel import matrix

RATINGS = {"A": 20.0, "B": 5.0}


def _form(value: float = 0.5) -> matrix.TeamForm:
    return matrix.TeamForm(**{field: value for field in matrix.TeamForm.FIELDS})


class TestSlope:
    def test_a_perfect_predictor_scores_one(self):
        actual = [-21.0, -7.0, 0.0, 3.0, 14.0, 28.0]
        assert cal.slope(actual, actual) == pytest.approx(1.0)

    def test_a_shrunk_predictor_scores_above_one(self):
        """This is the whole point: MAE hides it, the slope does not."""
        random.seed(11)
        actual = [random.gauss(0, 20) for _ in range(500)]
        shrunk = [0.5 * a for a in actual]
        assert cal.slope(shrunk, actual) == pytest.approx(2.0, abs=0.05)

    def test_an_inflated_predictor_scores_below_one(self):
        random.seed(11)
        actual = [random.gauss(0, 20) for _ in range(500)]
        inflated = [2.0 * a for a in actual]
        assert cal.slope(inflated, actual) == pytest.approx(0.5, abs=0.05)

    def test_none_values_are_dropped_pairwise(self):
        assert cal.slope([1.0, None, 2.0, 3.0], [2.0, 5.0, 4.0, 6.0]) == pytest.approx(2.0)

    def test_too_few_points_fails_loudly(self):
        with pytest.raises(cal.NotEnoughData):
            cal.slope([1.0, 2.0], [1.0, 2.0])

    def test_a_constant_prediction_has_no_slope(self):
        with pytest.raises(cal.NotEnoughData):
            cal.slope([5.0, 5.0, 5.0, 5.0], [1.0, 2.0, 3.0, 4.0])


class TestFit:
    def test_fitting_then_applying_lands_on_slope_one(self):
        random.seed(3)
        actual = [random.gauss(0, 18) for _ in range(400)]
        shrunk = [0.45 * a + random.gauss(0, 2) for a in actual]
        fitted = cal.fit(shrunk, actual)
        corrected = [fitted.apply(value) for value in shrunk]
        assert cal.slope(corrected, actual) == pytest.approx(1.0, abs=1e-9)

    def test_a_fitted_calibration_records_its_sample_size(self):
        fitted = cal.fit([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0])
        assert "4 games" in fitted.provenance
        assert fitted.slope == pytest.approx(2.0)


class TestCalibrationObject:
    def test_the_shipped_preseason_correction_is_identity(self):
        """Unfitted by design — adopting a correction is a claim about evidence."""
        assert cal.PRESEASON.is_identity
        assert cal.PRESEASON.apply(12.5) == 12.5

    def test_apply_passes_none_through(self):
        assert cal.Calibration(1.0, 2.0, "x").apply(None) is None

    def test_apply_is_affine(self):
        assert cal.Calibration(1.5, 2.0, "x").apply(10.0) == pytest.approx(21.5)


class TestReport:
    def test_it_names_under_dispersion(self):
        random.seed(5)
        actual = [random.gauss(0, 20) for _ in range(300)]
        shrunk = [0.5 * a for a in actual]
        report = cal.report("t", model=shrunk, actual=actual, market=actual)
        assert report.calibration_slope > 1.15
        assert "under-dispersed" in "\n".join(report.lines())
        assert report.dispersion_vs_market == pytest.approx(0.5, abs=0.02)

    def test_dispersion_can_be_measured_without_results(self):
        """A live board has no outcomes yet but can still be checked for scale."""
        report = cal.report("live", model=[1.0, 5.0, -3.0, 8.0],
                            market=[2.0, 10.0, -6.0, 16.0])
        assert report.calibration_slope is None
        assert report.dispersion_vs_market == pytest.approx(0.5, abs=1e-6)


class TestEdgePublication:
    """The preseason difference is the information gap, not a disagreement."""

    def test_preseason_path_reports_a_gap_and_withholds_the_edge(self):
        f = fc.game(home="A", away="B", team_ratings=RATINGS, market_margin=30.0)
        assert f.market_gap == pytest.approx(f.model_margin - 30.0)
        assert f.edge_points is None
        assert "information gap" in f.edge_withheld_reason

    def test_full_regime_publishes_the_edge_and_it_equals_the_gap(self):
        f = fc.game(home="A", away="B", team_ratings=RATINGS, market_margin=12.0,
                    home_form=_form(), away_form=_form(0.4), week=9)
        assert f.edge_points == pytest.approx(f.market_gap)
        assert f.edge_withheld_reason is None

    def test_early_weeks_withhold_even_with_complete_form(self):
        f = fc.game(home="A", away="B", team_ratings=RATINGS, market_margin=12.0,
                    home_form=_form(), away_form=_form(0.4), week=3)
        assert f.edge_points is None
        assert "validated regime" in f.edge_withheld_reason

    def test_no_price_means_no_gap_and_no_edge(self):
        f = fc.game(home="A", away="B", team_ratings=RATINGS)
        assert f.market_gap is None
        assert f.edge_points is None
        assert f.edge_withheld_reason is None

    def test_withholding_does_not_change_the_published_margin(self):
        """lam = 0 still publishes the price; this rule is about labelling."""
        f = fc.game(home="A", away="B", team_ratings=RATINGS, market_margin=30.0)
        assert f.margin == pytest.approx(30.0)
