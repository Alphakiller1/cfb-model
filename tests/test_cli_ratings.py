"""The season-to-date ratings table must admit when it is built on nothing.

Unlike the board, `cfbmodel ratings` (without --preseason) takes raw
opponent-adjusted scoring margin with no preseason prior blended in. Two weeks
into a season that ranks strength of opening schedule, and on the real 2026
week-2 data it put an FCS team 4th.
"""

from __future__ import annotations

from cfbmodel import matrix, ratings
from cfbmodel.cli import _warn_if_thin


def _games(count_per_team: int, teams=("A", "B", "C", "D")):
    """`count_per_team` games for each team, round-robin against the others."""
    out = []
    for round_ in range(count_per_team):
        for i in range(0, len(teams), 2):
            home, away = teams[i], teams[(i + 1 + round_) % len(teams)]
            out.append(ratings.Game(
                week=round_ + 1, home=home, away=away,
                home_points=24, away_points=17, neutral=False,
                home_is_fbs=True, away_is_fbs=True))
    return out


def test_a_thin_sample_is_flagged(capsys):
    _warn_if_thin(_games(1))
    assert "season-to-date only" in capsys.readouterr().out


def test_the_flag_names_the_alternatives(capsys):
    _warn_if_thin(_games(1))
    out = capsys.readouterr().out
    assert "--preseason" in out
    assert "blended" in out


def test_a_full_sample_is_not_flagged(capsys):
    _warn_if_thin(_games(matrix.MIN_FORM_GAMES + 2))
    assert capsys.readouterr().out == ""


def test_the_threshold_matches_the_form_gate(capsys):
    """One gate, one number: the ratings caveat and the efficiency gate agree."""
    _warn_if_thin(_games(matrix.MIN_FORM_GAMES))
    assert capsys.readouterr().out == ""
    _warn_if_thin(_games(matrix.MIN_FORM_GAMES - 1))
    assert "season-to-date only" in capsys.readouterr().out


def test_no_games_says_nothing(capsys):
    """The preseason basis rates zero games and must stay quiet."""
    _warn_if_thin([])
    assert capsys.readouterr().out == ""
