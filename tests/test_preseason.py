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


def test_talent_reconstruction_coefficients_are_all_positive():
    """More recruiting in any of the four covered classes must mean more talent."""
    for lag in P.TALENT_LAGS:
        assert P.TALENT_FROM_RECRUITING[f"lag_{lag}"] > 0


def test_recent_classes_weigh_more_than_old_ones():
    """A team's current roster is dominated by who it signed most recently."""
    assert P.TALENT_FROM_RECRUITING["lag_0"] > P.TALENT_FROM_RECRUITING["lag_2"]


def test_reconstruction_needs_every_lag(monkeypatch):
    """A team missing one class is skipped rather than scored on a partial history."""
    classes = {2026: {"A": 200.0, "B": 100.0}, 2025: {"A": 200.0},
               2024: {"A": 200.0}, 2023: {"A": 200.0}}
    monkeypatch.setattr(P, "_recruiting", lambda y: classes.get(y, {}))
    out = P._reconstruct_talent(2026)
    assert "A" in out
    assert "B" not in out          # B has only one class


def test_reconstruction_is_monotone_in_recruiting(monkeypatch):
    strong = {y: {"A": 300.0} for y in range(2023, 2027)}
    weak = {y: {"A": 100.0} for y in range(2023, 2027)}
    monkeypatch.setattr(P, "_recruiting", lambda y: strong.get(y, {}))
    hi = P._reconstruct_talent(2026)["A"]
    monkeypatch.setattr(P, "_recruiting", lambda y: weak.get(y, {}))
    lo = P._reconstruct_talent(2026)["A"]
    assert hi > lo


def test_published_talent_wins_when_available(monkeypatch):
    monkeypatch.setattr(P, "_published_talent", lambda y: {"A": 900.0})
    monkeypatch.setattr(P, "_reconstruct_talent", lambda y: {"A": 111.0})
    table, source = P.talent_composite(2025)
    assert source == "published"
    assert table["A"] == 900.0


def test_reconstruction_is_used_only_when_nothing_is_published(monkeypatch):
    monkeypatch.setattr(P, "_published_talent", lambda y: {})
    monkeypatch.setattr(P, "_reconstruct_talent", lambda y: {"A": 111.0})
    table, source = P.talent_composite(2026)
    assert source == "reconstructed"
    assert table["A"] == 111.0


def test_components_report_the_talent_source(monkeypatch):
    """A derived number must never be presented as a published one."""
    monkeypatch.setattr(P, "_published_talent", lambda y: {})
    monkeypatch.setattr(P, "_reconstruct_talent", lambda y: {"A": 500.0, "B": 300.0})
    monkeypatch.setattr(P, "_recruiting", lambda y: {"A": 100.0, "B": 50.0})
    monkeypatch.setattr(P, "_returning", lambda y: {"A": 0.6, "B": 0.5})
    comps = P.components(2026, {"A": 5.0, "B": -5.0})
    assert comps["A"].talent_source == "reconstructed"
