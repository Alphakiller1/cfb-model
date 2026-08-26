import pytest

from cfbmodel import matrix, totals


def _form(**kw):
    base = dict(off_ppa=0.20, off_successRate=0.46, off_explosiveness=1.20, off_stuffRate=0.17,
                def_ppa=0.12, def_successRate=0.42, def_explosiveness=1.18, def_stuffRate=0.19,
                drives=12.0, plays=68.0)
    base.update(kw)
    return matrix.TeamForm(**base)


def test_total_needs_complete_efficiency():
    assert totals.total_points(matrix.TeamForm(off_ppa=0.2), _form()) is None


def test_total_needs_pace():
    """Pace is outside TeamForm.FIELDS, so efficiency can be complete without it."""
    no_pace = _form(drives=None, plays=None)
    assert no_pace.complete() is True
    assert totals.total_points(no_pace, _form()) is None


def test_total_is_produced_for_complete_forms():
    value = totals.total_points(_form(), _form())
    assert value is not None
    assert 10 < value < 120


def test_more_efficient_offenses_raise_the_total():
    lo = totals.total_points(_form(), _form())
    hi = totals.total_points(_form(off_successRate=0.58), _form(off_successRate=0.58))
    assert hi > lo


def test_faster_pace_raises_the_total():
    """CFB tempo varies far more than NFL tempo, so pace has to move the total."""
    slow = totals.total_points(_form(drives=10.0), _form(drives=10.0))
    fast = totals.total_points(_form(drives=14.0), _form(drives=14.0))
    assert fast > slow


def test_scores_reconstruct_margin_and_total():
    p = totals.project(14.0, _form(), _form())
    assert p.home_score - p.away_score == pytest.approx(14.0)
    assert p.home_score + p.away_score == pytest.approx(p.total)


def test_scores_are_none_without_a_margin():
    p = totals.project(None, _form(), _form())
    assert (p.total, p.home_score, p.away_score) == (None, None, None)


def test_missing_form_falls_back_to_the_league_mean():
    """A centred guess beats a blank, but the caller must be able to tell."""
    p = totals.project(7.0, None, None)
    assert p.total == pytest.approx(totals.LEAGUE_MEAN_TOTAL)
    assert p.modelled is False
    assert p.home_score - p.away_score == pytest.approx(7.0)


def test_modelled_flag_is_true_with_real_form():
    assert totals.project(7.0, _form(), _form()).modelled is True


def test_scores_never_go_negative():
    """A huge margin against a modest total would otherwise produce one."""
    p = totals.project(90.0, None, None)
    assert p.away_score >= 0
    assert p.home_score >= 0


def test_negative_margin_favours_the_away_side():
    p = totals.project(-10.0, _form(), _form())
    assert p.away_score > p.home_score
