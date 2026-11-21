from __future__ import annotations

import pytest

from cfbmodel import prop_profiles


def _inputs():
    shares = {
        "qb": {"pass_att": .95, "rush_car": .2, "rush_yds": .1, "rush_td": .1, "rec": 0, "rec_yds": 0, "rec_td": 0},
        "backup": {"pass_att": .05, "rush_car": 0, "rush_yds": 0, "rush_td": 0, "rec": 0, "rec_yds": 0, "rec_td": 0},
        "lead": {"pass_att": 0, "rush_car": .6, "rush_yds": .6, "rush_td": .7, "rec": .1, "rec_yds": .1, "rec_td": .1},
        "rb2": {"pass_att": 0, "rush_car": .2, "rush_yds": .3, "rush_td": .2, "rec": .1, "rec_yds": .05, "rec_td": .1},
        "wr": {"pass_att": 0, "rush_car": 0, "rush_yds": 0, "rush_td": 0, "rec": .8, "rec_yds": .85, "rec_td": .8},
    }
    info = {aid: {"games": 4, "name": aid} for aid in shares}
    positions = {"qb": "QB", "backup": "QB", "lead": "RB", "rb2": "RB", "wr": "WR"}
    return shares, info, set(shares), positions


def test_a_missing_lead_back_reallocates_the_entire_backfield_pool():
    shares, info, eligible, positions = _inputs()
    final, base, _, exposure, contributors, reserve = prop_profiles.allocate(
        shares, info, eligible, positions, {"lead": "out"})
    assert "lead" not in final
    assert final["rb2"]["rush_car"] == pytest.approx(.8)
    assert final["qb"]["rush_car"] == pytest.approx(.2)
    assert final["wr"]["rec"] == pytest.approx(.8)
    assert final["rb2"]["rec"] == pytest.approx(.2)
    assert sum(row["rush_car"] for row in final.values()) + reserve["rush_car"] == pytest.approx(1)
    assert final["rb2"]["rush_yds"] / final["rb2"]["rush_car"] == pytest.approx(
        shares["rb2"]["rush_yds"] / shares["rb2"]["rush_car"])
    assert "lead" in contributors["rb2"] and exposure["qb"] == 1
    assert base["rb2"]["rush_car"] == pytest.approx(.2)


def test_first_half_absence_is_split_without_two_full_game_quarterbacks():
    shares, info, eligible, positions = _inputs()
    final, _, _, exposure, _, reserve = prop_profiles.allocate(
        shares, info, eligible, positions, {"qb": "out_first_half"})
    assert exposure == {"backup": .5, "qb": .5}
    assert sum(row["rush_car"] for row in final.values()) + reserve["rush_car"] == pytest.approx(1)


def test_no_known_healthy_back_keeps_unobserved_work_out_of_the_qb_projection():
    shares, info, eligible, positions = _inputs()
    final, _, _, _, _, reserve = prop_profiles.allocate(
        shares, info, eligible, positions, {"lead": "out", "rb2": "doubtful"})
    assert final["qb"]["rush_car"] == pytest.approx(.2)
    assert reserve["rush_car"] == pytest.approx(.8)


def test_questionable_is_an_explicit_assumption_without_an_invented_active_probability():
    shares, info, eligible, positions = _inputs()
    final, _, active, exposure, _, _ = prop_profiles.allocate(
        shares, info, eligible, positions, {"lead": "questionable"})
    assert "lead" in active and final["lead"]["rush_car"] == pytest.approx(.6)
    assert exposure["qb"] == 1
