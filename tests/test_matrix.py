import pytest

from cfbmodel import matrix as M


def _form(**kw):
    base = dict(off_ppa=0.15, off_successRate=0.45, off_explosiveness=1.1, off_stuffRate=0.18,
                def_ppa=0.10, def_successRate=0.42, def_explosiveness=1.2, def_stuffRate=0.20)
    base.update(kw)
    return M.TeamForm(**base)


@pytest.mark.parametrize("name", sorted(M.GROUPS))
def test_weight_groups_are_valid(name):
    M.validate_weight_group(M.GROUPS[name], name=name)


def test_bad_group_is_rejected():
    with pytest.raises(M.WeightGroupError, match="sum to 1.0"):
        M.validate_weight_group({"a": 0.5, "b": 0.2})


def test_negative_weight_is_rejected():
    with pytest.raises(M.WeightGroupError, match="negative"):
        M.validate_weight_group({"a": 1.5, "b": -0.5})


def test_success_rate_dominates_both_sides():
    assert M.OFFENSE_WEIGHTS["success_rate"] > 0.5
    assert M.DEFENSE_WEIGHTS["success_rate_allowed_inverse"] > 0.5


def test_opponent_adjustment_revived_explosiveness():
    """Raw explosiveness fitted at 0.016 and looked worthless. Opponent-adjusted
    it is a real signal -- the raw measurement was confounded by schedule."""
    assert M.OFFENSE_WEIGHTS["explosiveness"] > 0.10
    assert M.DEFENSE_WEIGHTS["explosiveness_allowed_inverse"] > 0.10


def test_matrix_is_not_promoted():
    assert M.STATUS == "CHALLENGER/UNPROMOTED"


def test_incomplete_form_yields_none_not_zero():
    """A half-populated team must not silently score as average."""
    assert M.margin_points(M.TeamForm(off_ppa=0.3), _form()) is None
    assert M.margin_points(_form(), M.TeamForm()) is None


def test_identical_forms_cancel():
    assert M.margin_points(_form(), _form()) == pytest.approx(0.0)


def test_better_offense_increases_margin():
    assert M.margin_points(_form(off_successRate=0.58), _form()) > 0


def test_allowing_less_increases_margin():
    """Allowed stats are inverted: a stingier defence must help."""
    assert M.margin_points(_form(def_successRate=0.30), _form()) > 0


def test_being_stuffed_hurts_and_stuffing_helps():
    """Sign convention: offensive stuff rate is bad, defensive stuff rate good."""
    assert M.margin_points(_form(off_stuffRate=0.30), _form()) < 0
    assert M.margin_points(_form(def_stuffRate=0.30), _form()) > 0


def test_every_form_field_has_a_coefficient():
    for field in M.TeamForm.FIELDS:
        assert field in M.COEFFICIENTS
