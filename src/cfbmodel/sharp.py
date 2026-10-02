"""Sharp money and line movement: where the money is concentrated, and which way
the number has moved.

Two public signals, combined per game and market (spread, total):

* **Money vs tickets** - DraftKings' % of handle against % of bets on each side
  (`sources.dk_splits`). A side drawing far more of the money than of the bets
  has fewer, larger wagers on it: the usual proxy for sharp action.
* **Line movement** - the DraftKings number when the ledger first saw it against
  now. A move *toward* the money side is steam; a move toward it while most
  tickets are on the other side is reverse line movement, the classic sharp tell.

``concentration`` ranks spots: the money-minus-tickets gap, plus 3 points per
point of line move toward the money side, plus 5 when that move runs against
the ticket majority. Every listed spot is logged before kickoff and graded on
the result *and* on closing-line value, so whether following it has paid is a
measured number on the page rather than a slogan.

These are market observations, not model picks; the card says whether the model
agrees.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

MIN_DIVERGENCE = 10.0       # money % minus bets % on the flagged side
# Per market, so the extreme splits typical of totals do not crowd spreads out.
LIMIT = 6
MOVE_WEIGHT = 3.0
RLM_BONUS = 5.0


@dataclass
class Spot:
    season: int
    week: int
    home: str
    away: str
    kickoff: str | None
    family: str             # spread | total
    side: str               # home | away | over | under
    selection: str
    line: float             # current line for the money side (team spread or total)
    price: int | None
    handle_pct: float
    bets_pct: float
    divergence: float
    open_line: float | None
    move: float             # points the number moved toward the money side
    reverse: bool           # move toward money side against the ticket majority
    concentration: float
    model_agrees: bool | None
    note: str

    @property
    def pick_id(self) -> str:
        return "|".join((str(self.season), str(self.week), "sharp", self.family,
                         self.away, self.home))

    def to_json(self) -> dict:
        out = asdict(self)
        out["pick_id"] = self.pick_id
        return out


_NUM = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*$")


def _number(selection: str) -> float | None:
    m = _NUM.search(selection.replace("−", "-"))
    return float(m.group(1)) if m else None


def _match(game, home: str, away: str, resolve) -> bool:
    return resolve(game.home) == home and resolve(game.away) == away


def first_lines(snapshots: list[dict], season: int, week: int) -> dict[tuple[str, str], dict]:
    """(home, away) -> the earliest ledger quote for the game this week."""
    out: dict[tuple[str, str], dict] = {}
    for row in snapshots:
        if row.get("season") != season or row.get("week") != week:
            continue
        key = (row["home"], row["away"])
        if key not in out or row.get("recorded_at", "") < out[key].get("recorded_at", ""):
            out[key] = row
    return out


def _spread_spot(game, row, opening, *, season, week, resolve) -> Spot | None:
    f = row.forecast
    sides = [s for s in game.sides if s.market == "spread"]
    if len(sides) != 2 or f.book_margin is None:
        return None
    money = max(sides, key=lambda s: s.handle_pct - s.bets_pct)
    divergence = money.handle_pct - money.bets_pct
    if divergence < MIN_DIVERGENCE:
        return None
    is_home = resolve(re.sub(r"\s*[+-]?\d+(\.\d+)?$", "", money.selection)) == f.home
    line = -f.book_margin if is_home else f.book_margin       # the money side's spread
    open_line = None
    move = 0.0
    if opening and opening.get("book_margin") is not None:
        first = opening["book_margin"]
        open_line = -first if is_home else first
        move = open_line - line            # e.g. -3 -> -4.5 is 1.5 toward the money side
    reverse = move > 0 and money.bets_pct < 50.0
    model = f.model_margin
    agrees = None if model is None else ((model - f.book_margin) > 0) == is_home
    team = f.home if is_home else f.away
    note = (f"{money.handle_pct:.0f}% of the money on {team} from {money.bets_pct:.0f}% of "
            f"the bets.")
    if open_line is not None and abs(move) >= 0.5:
        note += (f" Line {_fmt(open_line)} -> {_fmt(line)}: "
                 f"{'toward' if move > 0 else 'away from'} the money side.")
    if reverse:
        note += " Reverse line movement: the number moved with the money, against the tickets."
    if agrees is not None:
        note += " The model agrees." if agrees else " The model is on the other side."
    score = divergence + MOVE_WEIGHT * max(move, 0.0) + (RLM_BONUS if reverse else 0.0)
    return Spot(season, week, f.home, f.away, _iso(row.kickoff_utc), "spread",
                "home" if is_home else "away", f"{team} {_fmt(line)}", line, money.odds,
                money.handle_pct, money.bets_pct, divergence, open_line, round(move, 1),
                reverse, round(score, 1), agrees, note)


def _total_spot(game, row, opening, *, season, week) -> Spot | None:
    f = row.forecast
    sides = [s for s in game.sides if s.market == "total"]
    if len(sides) != 2 or f.book_total is None:
        return None
    money = max(sides, key=lambda s: s.handle_pct - s.bets_pct)
    divergence = money.handle_pct - money.bets_pct
    if divergence < MIN_DIVERGENCE:
        return None
    over = money.selection.lower().startswith("over")
    open_line = opening.get("book_total") if opening else None
    move = 0.0
    if open_line is not None:
        move = (f.book_total - open_line) if over else (open_line - f.book_total)
    reverse = move > 0 and money.bets_pct < 50.0
    agrees = None
    if f.independent_total is not None:
        agrees = (f.independent_total > f.book_total) == over
    note = (f"{money.handle_pct:.0f}% of the money on the {'over' if over else 'under'} "
            f"from {money.bets_pct:.0f}% of the bets.")
    if open_line is not None and abs(move) >= 0.5:
        note += (f" Total {open_line:g} -> {f.book_total:g}: "
                 f"{'toward' if move > 0 else 'away from'} the money side.")
    if reverse:
        note += " Reverse line movement against the ticket majority."
    if agrees is not None:
        note += " The model agrees." if agrees else " The model is on the other side."
    score = divergence + MOVE_WEIGHT * max(move, 0.0) + (RLM_BONUS if reverse else 0.0)
    return Spot(season, week, f.home, f.away, _iso(row.kickoff_utc), "total",
                "over" if over else "under", f"{'Over' if over else 'Under'} {f.book_total:g}",
                f.book_total, money.odds, money.handle_pct, money.bets_pct, divergence,
                open_line, round(move, 1), reverse, round(score, 1), agrees, note)


def _fmt(value: float) -> str:
    return "PK" if abs(value) < 0.05 else f"{value:+g}"


def _iso(moment) -> str | None:
    return moment.isoformat().replace("+00:00", "Z") if moment else None


def build(rows: list, splits: list, snapshots: list[dict], *, season: int, week: int,
          resolve) -> list[Spot]:
    """The week's most concentrated sharp spots. ``resolve`` maps a DraftKings team
    name to the board's school name (or None)."""
    opening = first_lines(snapshots, season, week)
    by_pair = {}
    for game in splits:
        home, away = resolve(game.home), resolve(game.away)
        if home and away:
            by_pair[(home, away)] = game
    spots: list[Spot] = []
    for row in rows:
        f = row.forecast
        game = by_pair.get((f.home, f.away))
        if game is None:
            continue
        first = opening.get((f.home, f.away))
        for spot in (_spread_spot(game, row, first, season=season, week=week, resolve=resolve),
                     _total_spot(game, row, first, season=season, week=week)):
            if spot is not None:
                spots.append(spot)
    spots.sort(key=lambda s: -s.concentration)
    return ([s for s in spots if s.family == "spread"][:LIMIT]
            + [s for s in spots if s.family == "total"][:LIMIT])
