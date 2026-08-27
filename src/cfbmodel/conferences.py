"""Conference ratings, from the team ratings that already exist.

A conference rating is not an opinion the model holds separately — it is an
aggregate of the team ratings, and the only interesting question is *which*
aggregate. Three are published because they answer different questions and
disagree in informative ways:

* **Mean** — how good the league is on average. Sensitive to the bottom, which
  is the honest answer to "how hard is this schedule".
* **Median** — the typical member, unmoved by one outlier programme. The Big 12
  with one playoff team and eleven ordinary ones is not the ACC with twelve
  good ones, and the mean alone cannot tell you that.
* **Top-4 mean** — the ceiling, which is what a playoff conversation is
  actually about.

Ranked by mean by default. `depth` (median minus mean) is carried explicitly
because it is the number that separates a top-heavy league from a deep one, and
deriving it from two columns is exactly the arithmetic a reader will not do.

**A caution the numbers cannot carry themselves.** Ratings are solved from
margins, and cross-conference scheduling is sparse — the P4 and G5 sub-graphs
are connected by relatively few games, so their *relative level* is more weakly
identified than the within-conference ordering. A conference rating is therefore
a better guide to who is good inside a league than to how two leagues compare.
`cross_games` reports how many out-of-conference games actually informed each
rating, so the reader can see how much weight the comparison bears.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from cfbmodel import ratings as ratings_mod

# Conferences below this are not ranked: with three or four members an
# aggregate is a description of a handful of teams, not a league.
MIN_MEMBERS = 4

TOP_N = 4


@dataclass(frozen=True)
class ConferenceRating:
    name: str
    teams: int
    mean: float
    median: float
    top_mean: float
    best: tuple[str, float]
    worst: tuple[str, float]
    spread: float
    cross_games: int = 0

    @property
    def depth(self) -> float:
        """Median minus mean. Positive = deep; negative = carried by its top."""
        return self.median - self.mean


def _members(teams: dict[str, str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for team, conference in teams.items():
        if conference:
            out.setdefault(conference, []).append(team)
    return out


def rate(
    rating_table: dict[str, float],
    conferences: dict[str, str],
    *,
    min_members: int = MIN_MEMBERS,
    cross_counts: dict[str, int] | None = None,
) -> list[ConferenceRating]:
    """Aggregate team ratings into conference ratings, best mean first.

    `conferences` is team -> conference name (`teams.load` supplies it).
    """
    out: list[ConferenceRating] = []
    for name, members in _members(conferences).items():
        values = [
            (team, rating_table[team]) for team in members
            if team in rating_table and team != ratings_mod.FCS
        ]
        if len(values) < min_members:
            continue
        ordered = sorted(values, key=lambda pair: -pair[1])
        numbers = [value for _, value in ordered]
        out.append(ConferenceRating(
            name=name,
            teams=len(ordered),
            mean=statistics.fmean(numbers),
            median=statistics.median(numbers),
            top_mean=statistics.fmean(numbers[:TOP_N]),
            best=ordered[0],
            worst=ordered[-1],
            spread=numbers[0] - numbers[-1],
            cross_games=(cross_counts or {}).get(name, 0),
        ))
    return sorted(out, key=lambda c: -c.mean)


def cross_conference_games(
    games: list[ratings_mod.Game], conferences: dict[str, str]
) -> dict[str, int]:
    """Conference -> completed games against a DIFFERENT conference.

    The identifiability check for the caution in the module docstring: a league
    with few out-of-conference results has a level that the solve inferred
    mostly indirectly.
    """
    counts: dict[str, int] = {}
    for game in games:
        home = conferences.get(game.home)
        away = conferences.get(game.away)
        if not home or not away or home == away:
            continue
        counts[home] = counts.get(home, 0) + 1
        counts[away] = counts.get(away, 0) + 1
    return counts


def team_ranks_within_conference(
    rating_table: dict[str, float], conferences: dict[str, str]
) -> dict[str, tuple[int, int]]:
    """Team -> (rank inside its conference, conference size).

    The within-league ordering is the part of a conference rating that is well
    identified, so it is offered separately from the cross-league comparison.
    """
    out: dict[str, tuple[int, int]] = {}
    for members in _members(conferences).values():
        ordered = sorted(
            ((team, rating_table[team]) for team in members if team in rating_table),
            key=lambda pair: -pair[1],
        )
        for index, (team, _) in enumerate(ordered, start=1):
            out[team] = (index, len(ordered))
    return out
