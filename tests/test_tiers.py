from cfbmodel import authority, forecast, tiers


def test_tier_rules():
    assert tiers.tier("Georgia", "SEC", 2026) == "P4"
    assert tiers.tier("Notre Dame", "FBS Independents", 2026) == "P4"
    assert tiers.tier("Oregon State", "Pac-12", 2026) == "G5"
    assert tiers.tier("Oregon", "Pac-12", 2022) == "P4"
    assert tiers.tier("Boise State", "Mountain West", 2026) == "G5"


def test_orientation_points_at_the_power_side():
    assert tiers.orientation("Georgia", "SEC", "Kent State", "MAC", 2026) == 1
    assert tiers.orientation("Kent State", "MAC", "Georgia", "SEC", 2026) == -1
    assert tiers.orientation("Georgia", "SEC", "Alabama", "SEC", 2026) == 0


def _game(week, orientation):
    return forecast.game(home="P", away="G", team_ratings={"P": 5.0, "G": 0.0}, week=week,
                         authority=authority.current(), tier_orientation=orientation,
                         simulations=0)


def test_forecast_adds_the_fitted_shift_toward_the_power_side():
    for week, regime in ((2, "early"), (8, "validated")):
        plain, shifted = _game(week, 0), _game(week, 1)
        assert shifted.tier_adjustment == tiers.SHIFT_POINTS[regime]
        assert abs(shifted.model_margin - plain.model_margin - tiers.SHIFT_POINTS[regime]) < 1e-9
        assert _game(week, -1).model_margin < plain.model_margin
        assert plain.tier_adjustment == 0.0
