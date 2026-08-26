from cfbmodel.authority import Action, Level, current, promote


def test_current_is_research_only():
    assert current().level is Level.RESEARCH_ONLY


def test_current_may_not_bet():
    """The measured evidence does not support betting, so the gate says so."""
    assert current().may_bet is False


def test_the_market_gate_is_explicitly_unmet():
    unmet = current().unmet_gates
    assert "model_mae_below_closing_market" in unmet
    assert "ats_lower_confidence_bound_above_breakeven" in unmet


def test_evidence_is_cited_not_asserted():
    assert "BASELINE_2019_2025" in current().evidence


def test_partial_promotion_is_refused():
    a = promote({"historical_seasons_at_least_6"})
    assert a.level is Level.RESEARCH_ONLY
    assert a.may_bet is False


def test_full_promotion_requires_every_gate():
    from cfbmodel.authority import REQUIRED_GATES
    a = promote(set(REQUIRED_GATES))
    assert a.level is Level.PROMOTED
    assert a.may_bet is True


def test_unpromoted_edge_is_monitor_never_bet():
    assert current().action_for(3.0, True) is Action.MONITOR


def test_no_price_is_avoid():
    assert current().action_for(3.0, False) is Action.AVOID
    assert current().action_for(None, True) is Action.AVOID


def test_implausible_edge_is_review():
    """A 30-point disagreement with a liquid line is a bug signal."""
    assert current().action_for(30.0, True) is Action.REVIEW
    assert current().action_for(-30.0, True) is Action.REVIEW


def test_evidence_reports_the_opponent_adjusted_numbers():
    """The gate must cite what the model does now, not a superseded baseline."""
    e = current().evidence
    assert "12.5251" in e
    assert "opponent-adjusted" in e


def test_still_unpromoted_despite_the_ci_straddling_breakeven():
    """An interval containing the breakeven bar is 'unproven', not 'edge'."""
    a = current()
    assert a.may_bet is False
    assert "ats_lower_confidence_bound_above_breakeven" in a.unmet_gates
