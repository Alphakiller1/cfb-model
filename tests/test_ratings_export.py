"""The ranked power-ratings payload the content engine renders."""

from datetime import datetime, timezone

import pytest

from cfbmodel import authority as auth_mod
from cfbmodel import export
from cfbmodel import ratings as R

STAMP = datetime(2026, 8, 27, 18, 40, tzinfo=timezone.utc)
AUTH = auth_mod.Authority(
    level=auth_mod.Level.RESEARCH_ONLY,
    evidence="walk-forward",
    unmet_gates=("a", "b"),
)

TABLE = {
    "Ohio State": 27.6,
    "Texas": 25.4,
    "Miami": 22.1,
    "LSU": 19.8,
    "Iowa": 14.3,
    "Penn State": 11.2,
    "Kent State": -18.4,
    R.FCS: -31.0,
}


def payload(**kw):
    kw.setdefault("season", 2026)
    kw.setdefault("ratings", TABLE)
    kw.setdefault("basis", "preseason")
    kw.setdefault("authority", AUTH)
    kw.setdefault("generated_at", STAMP)
    return export.ratings_payload(**kw)


def test_teams_come_back_ranked_by_rating():
    teams = payload()["teams"]
    assert [t["rank"] for t in teams] == list(range(1, 8))
    assert [t["school"] for t in teams][:3] == ["Ohio State", "Texas", "Miami"]


def test_the_pooled_fcs_rating_is_not_a_team():
    schools = {t["school"] for t in payload()["teams"]}
    assert R.FCS not in schools


def test_top_n_cuts_the_list_without_changing_the_league_count():
    """The board shows forty; the league it is graded against is all of them."""
    cut = payload(top=3)
    assert len(cut["teams"]) == 3
    assert cut["team_count"] == 7


def test_the_league_spread_is_measured_before_the_cut():
    # Otherwise a top-40 graphic would grade every team against the top 40 and
    # paint the whole board elite.
    assert payload(top=3)["league"] == payload()["league"]
    assert payload()["league"]["sd"] > 0


def test_a_preseason_payload_says_no_games_are_behind_it():
    board = payload(basis="preseason", week=1, games_rated=0)
    assert board["basis"] == "preseason"
    assert board["games_rated"] == 0
    assert board["week"] == 1


def test_an_in_season_payload_carries_its_game_count():
    board = payload(basis="season_to_date", games_rated=812)
    assert board["games_rated"] == 812


def test_an_unknown_basis_is_refused():
    with pytest.raises(ValueError):
        payload(basis="vibes")


def test_the_authority_gate_is_stated_on_the_payload():
    gate = payload()["authority"]
    assert gate["level"] == "RESEARCH_ONLY"
    assert gate["may_bet"] is False
    assert gate["unmet_gates"] == ["a", "b"]


def test_the_unit_travels_so_the_graphic_never_names_it_from_memory():
    board = payload()
    assert "neutral field" in board["scale"]
    assert board["home_field_points"] == R.HOME_FIELD_POINTS
    assert board["kind"] == "power_ratings"
    assert board["sport"] == "cfb"


def test_an_empty_table_does_not_divide_by_zero():
    board = export.ratings_payload(
        season=2026, ratings={}, basis="preseason",
        authority=AUTH, generated_at=STAMP,
    )
    assert board["teams"] == []
    assert board["team_count"] == 0
    assert board["league"]["sd"] == 0.0
