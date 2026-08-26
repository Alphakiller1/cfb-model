import pytest

from cfbmodel import matrix as M


@pytest.mark.parametrize("name", sorted(M.GROUPS))
def test_weight_groups_are_valid(name):
    M.validate_weight_group(M.GROUPS[name], name=name)


def test_bad_group_is_rejected():
    with pytest.raises(M.WeightGroupError, match="sum to 1.0"):
        M.validate_weight_group({"a": 0.5, "b": 0.2})


def test_negative_weight_is_rejected():
    with pytest.raises(M.WeightGroupError, match="negative"):
        M.validate_weight_group({"a": 1.5, "b": -0.5})


def test_success_rate_outweighs_explosiveness():
    """The fitted correction to the scaffold: explosiveness carries little
    independent weight once success rate and PPA are present."""
    assert M.OFFENSE_WEIGHTS["success_rate"] > 10 * M.OFFENSE_WEIGHTS["explosiveness"]


def test_matrix_is_not_promoted():
    assert M.STATUS == "CHALLENGER/UNPROMOTED"


def test_incomplete_form_yields_none_not_zero():
    """A half-populated team must not silently score as an average team."""
    assert M.offense_index(M.TeamForm(success_rate=0.5)) is None
    assert M.defense_index(M.TeamForm(havoc_rate=0.1)) is None


def _full_form(**kw):
    base = dict(success_rate=0.45, ppa_per_play=0.15, points_per_opportunity=4.0,
                explosiveness=1.1, success_rate_allowed=0.42,
                points_per_opportunity_allowed=4.2, ppa_allowed=0.10,
                explosiveness_allowed=1.2, havoc_rate=0.15)
    base.update(kw)
    return M.TeamForm(**base)


def test_complete_form_scores():
    assert M.offense_index(_full_form()) is not None
    assert M.defense_index(_full_form()) is not None


def test_better_offense_scores_higher():
    assert M.offense_index(_full_form(success_rate=0.55)) > M.offense_index(_full_form())


def test_allowing_less_scores_higher_on_defense():
    """Allowed stats are inverted: giving up less must rate better."""
    stingy = _full_form(success_rate_allowed=0.30)
    leaky = _full_form(success_rate_allowed=0.50)
    assert M.defense_index(stingy) > M.defense_index(leaky)
