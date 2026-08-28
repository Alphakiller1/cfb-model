"""Conference ratings aggregated from team ratings."""

import pytest

from cfbmodel import conferences, ratings

# Two leagues with the SAME mean and opposite shapes: Deep is uniform, Topheavy
# is one star carrying eleven ordinary teams. An aggregate that cannot separate
# them is not worth publishing.
TABLE = {
    "D1": 5.0, "D2": 5.0, "D3": 5.0, "D4": 5.0, "D5": 5.0, "D6": 5.0,
    "T1": 25.0, "T2": 1.0, "T3": 1.0, "T4": 1.0, "T5": 1.0, "T6": 1.0,
    "S1": 0.0, "S2": 0.0, "S3": 0.0,          # too small to rank
    ratings.FCS: -30.0,
}
CONFERENCES = {
    **{f"D{i}": "Deep" for i in range(1, 7)},
    **{f"T{i}": "Topheavy" for i in range(1, 7)},
    **{f"S{i}": "Small" for i in range(1, 4)},
    ratings.FCS: "Deep",
}


def _by_name(rows):
    return {row.name: row for row in rows}


class TestRate:
    def test_the_pooled_fcs_rating_is_never_a_conference_member(self):
        """It is a bucket for hundreds of programmes, not a team."""
        deep = _by_name(conferences.rate(TABLE, CONFERENCES))["Deep"]
        assert deep.teams == 6
        assert deep.mean == pytest.approx(5.0)

    def test_a_conference_below_the_floor_is_not_ranked(self):
        assert "Small" not in _by_name(conferences.rate(TABLE, CONFERENCES))

    def test_depth_separates_two_leagues_with_the_same_mean(self):
        rows = _by_name(conferences.rate(TABLE, CONFERENCES))
        deep, top = rows["Deep"], rows["Topheavy"]
        assert deep.mean == pytest.approx(top.mean)
        assert deep.depth == pytest.approx(0.0)
        assert top.depth < 0          # median below mean: carried by its top
        assert top.spread > deep.spread

    def test_top_mean_is_the_ceiling_not_the_average(self):
        top = _by_name(conferences.rate(TABLE, CONFERENCES))["Topheavy"]
        assert top.top_mean == pytest.approx((25.0 + 1.0 + 1.0 + 1.0) / 4)

    def test_best_and_worst_are_named(self):
        top = _by_name(conferences.rate(TABLE, CONFERENCES))["Topheavy"]
        assert top.best[0] == "T1"
        assert top.best[1] == pytest.approx(25.0)
        assert top.worst[1] == pytest.approx(1.0)

    def test_ordering_is_by_mean_descending(self):
        table = dict(TABLE, D1=50.0)
        rows = conferences.rate(table, CONFERENCES)
        assert [row.name for row in rows] == sorted(
            (r.name for r in rows), key=lambda n: -_by_name(rows)[n].mean
        )

    def test_an_unrated_team_is_skipped_not_zeroed(self):
        table = {k: v for k, v in TABLE.items() if k != "D6"}
        assert _by_name(conferences.rate(table, CONFERENCES))["Deep"].teams == 5

    def test_no_conference_metadata_yields_nothing(self):
        assert conferences.rate(TABLE, {}) == []


class TestCrossConferenceGames:
    def _game(self, home, away):
        return ratings.Game(week=1, home=home, away=away,
                            home_points=21, away_points=17)

    def test_only_games_between_different_conferences_count(self):
        games = [
            self._game("D1", "T1"),   # cross
            self._game("D2", "D3"),   # within — ignored
            self._game("T2", "D4"),   # cross
        ]
        counts = conferences.cross_conference_games(games, CONFERENCES)
        assert counts["Deep"] == 2
        assert counts["Topheavy"] == 2

    def test_a_team_with_no_conference_is_ignored(self):
        counts = conferences.cross_conference_games(
            [self._game("D1", "Unknown")], CONFERENCES
        )
        assert counts == {}

    def test_it_surfaces_a_league_the_solve_barely_connected(self):
        counts = conferences.cross_conference_games(
            [self._game("D1", "T1")], CONFERENCES
        )
        rows = _by_name(conferences.rate(TABLE, CONFERENCES, cross_counts=counts))
        assert rows["Deep"].cross_games == 1
        assert rows["Topheavy"].cross_games == 1


class TestWithinConferenceRanks:
    def test_rank_is_one_indexed_and_carries_the_size(self):
        ranks = conferences.team_ranks_within_conference(TABLE, CONFERENCES)
        assert ranks["T1"] == (1, 6)
        assert ranks["T1"][1] == 6

    def test_every_rated_member_gets_a_place(self):
        ranks = conferences.team_ranks_within_conference(TABLE, CONFERENCES)
        places = sorted(rank for team, (rank, _) in ranks.items()
                        if CONFERENCES.get(team) == "Topheavy")
        assert places == [1, 2, 3, 4, 5, 6]
