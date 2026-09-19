"""Monte Carlo game outcomes, averaged into a projected scoreline.

A single Gaussian draw around the predictive margin is not a forecast; it is
noise. The quantity that is predictive is the mean of many independent draws
from the same residual distribution the ratings already use. That mean converges
on the centre of mass, and the empirical win rate captures the small nonlinearity
from scores that cannot go below zero.

How many draws
--------------
The standard error of the mean margin is ``MARGIN_SD / sqrt(N)`` (24.2 / sqrt(N)).
Against a 12-point MAE, extra precision past a few tenths of a point does not
change a published score:

    N         SE (pts)
    1,000     0.77     -- a displayed score can still jump a point
    10,000    0.24     -- knee: an order of magnitude below MAE
    50,000    0.11     -- five times the cost, half the leftover error

``DEFAULT_SIMULATIONS = 10_000`` is that knee. The stream is seeded from the
matchup so a rebuild with the same inputs reprints the same board.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from cfbmodel import ratings, totals

DEFAULT_SIMULATIONS = 10_000


@dataclass(frozen=True)
class SimSummary:
    n: int
    home_score: float
    away_score: float
    total: float
    margin: float
    win_probability: float


def matchup_seed(home: str, away: str, *, season: int | None = None,
                 week: int | None = None) -> int:
    """Stable seed so the same slate does not jitter between builds."""
    payload = f"{season or 0}|{week or 0}|{away}|{home}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "little")


def average_outcomes(
    *,
    margin: float,
    total: float,
    n: int = DEFAULT_SIMULATIONS,
    seed: int = 0,
    margin_sd: float = ratings.MARGIN_SD,
    total_sd: float = totals.TOTAL_SD,
) -> SimSummary:
    """Draw ``n`` (margin, total) pairs and return the mean scoreline.

    Draws are independent. Real scoring margin and total are weakly dependent;
    modelling that correlation would thin the tails of neither enough to move
    a tenth of a point in the mean, which is the only number published here.
    """
    if n <= 0:
        raise ValueError("simulation count must be positive")
    rng = random.Random(seed)
    home_sum = away_sum = total_sum = margin_sum = 0.0
    home_wins = 0
    for _ in range(n):
        drawn_margin = rng.gauss(margin, margin_sd)
        drawn_total = rng.gauss(total, total_sd)
        line = totals.scoreline(
            drawn_margin, drawn_total, modelled=True, basis="simulation",
        )
        home_sum += line.home_score
        away_sum += line.away_score
        total_sum += line.total
        margin_sum += line.home_score - line.away_score
        if line.home_score > line.away_score:
            home_wins += 1
        elif line.home_score == line.away_score:
            home_wins += 0.5
    return SimSummary(
        n=n,
        home_score=home_sum / n,
        away_score=away_sum / n,
        total=total_sum / n,
        margin=margin_sum / n,
        win_probability=home_wins / n,
    )
