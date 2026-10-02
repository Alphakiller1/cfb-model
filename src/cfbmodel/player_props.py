"""College football player projections: QBs, running backs and receivers.

A projection layer, not a betting model. It never ingests a player price and
never emits an edge; it publishes a mean and a distribution for each stat a
sportsbook posts, so a reader can compare them with a line.

Two stages, the same shape as nfl-model's player layer:

1. **Team environment.** Each team's passing and rushing volume and yardage for
   the coming game, from its own season-to-date output, what the opponent has
   allowed, the game's expected margin (game script: a team that leads runs,
   one that trails throws) and its implied points. The coefficients are fitted
   by ``scripts/fit_player_props.py`` on 2024 and tested on 2025, time-forward.

2. **Player share.** Each player's recency-weighted share of his team's
   attempts, completions, carries, yards and touchdowns, over the games he
   played. A player projects only if he played in his team's most recent game:
   college football has no injury report worth the name, and the last box score
   is the most reliable availability signal there is.

Box scores come from ESPN (``sources/espn_box``), so the layer keeps working
when the CFBD allowance is spent. Rushing follows NCAA scoring (sacks count as
rushes), which is what sportsbooks grade.

Distributions are empirical: for each stat, the quantiles of actual / projected
measured on the fit season, by projection size. A stat whose projection is
small has a much wider relative spread than one whose projection is large.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .sources.espn_box import GameBox, PlayerLine

MODEL_VERSION = "cfb-player-projections/1.0.0"

TEAM_STATS = ("pass_att", "pass_cmp", "pass_yds", "pass_td", "pass_int",
              "rush_car", "rush_yds", "rush_td")

# Recency: a game N team-games ago counts 0.5 ** (N / half-life).
TEAM_HALF_LIFE = 4.0
PLAYER_HALF_LIFE = 3.0
# Pseudo-games of league average that shrink a team's own and allowed means.
TEAM_PSEUDO_GAMES = 2.0
# A prior-season game still carries weight for team volume (weeks 1-3 would
# otherwise have nothing) but much less than this season's.
PRIOR_SEASON_WEIGHT = 0.30
PRIOR_SEASON_PLAYER_WEIGHT = 0.25

# Fitted by scripts/fit_player_props.py: tested fit-2024 -> 2025, shipped on both.
# Per team stat: (own offence, opponent allowed, team margin, implied points),
# each on deviations from the league mean.
TEAM_COEF: dict[str, tuple[float, float, float, float]] = {
    "pass_att": (1.1414, 0.2264, -0.0651, 0.1102),
    "pass_cmp": (1.1084, 0.3042, -0.0374, 0.1333),
    "pass_yds": (0.8456, 0.2553, -0.0884, 2.4517),
    "pass_td": (0.3288, 0.1576, -0.001, 0.0456),
    "pass_int": (-0.0113, 0.2528, -0.0106, 0.0051),
    "rush_car": (1.0539, 0.5186, 0.0719, 0.0016),
    "rush_yds": (0.8155, 0.5473, 1.2159, 0.3889),
    "rush_td": (0.3756, 0.1988, 0.0226, 0.0279),
}

# Empirical quantiles of actual / projected, by stat and projection size.
# QUANTILE_LEVELS index them. Refit with scripts/fit_player_props.py.
QUANTILE_LEVELS = (0.10, 0.25, 0.50, 0.75, 0.90)
RATIO_QUANTILES: dict[str, list[tuple[float, tuple[float, ...]]]] = {
    "pass_yds": [
        (180, (0.211, 0.541, 0.92, 1.272, 1.623)),
        (240, (0.444, 0.728, 0.988, 1.239, 1.52)),
        (1e+09, (0.598, 0.778, 0.969, 1.184, 1.392)),
    ],
    "pass_att": [
        (26, (0.442, 0.754, 1.003, 1.228, 1.462)),
        (34, (0.596, 0.764, 0.981, 1.182, 1.383)),
        (1e+09, (0.652, 0.804, 0.973, 1.17, 1.349)),
    ],
    "pass_cmp": [
        (16, (0.336, 0.642, 0.935, 1.246, 1.505)),
        (21, (0.555, 0.759, 0.989, 1.209, 1.442)),
        (1e+09, (0.617, 0.821, 0.98, 1.157, 1.346)),
    ],
    "pass_td": [
        (1.2, (0.0, 0.0, 0.92, 1.748, 2.667)),
        (1.9, (0.0, 0.547, 0.776, 1.495, 2.109)),
        (1e+09, (0.0, 0.489, 0.986, 1.408, 1.725)),
    ],
    "pass_int": [
        (0.6, (0.0, 0.0, 0.0, 1.787, 2.436)),
        (0.9, (0.0, 0.0, 1.114, 1.474, 2.713)),
        (1e+09, (0.0, 0.0, 0.993, 1.11, 2.171)),
    ],
    "rush_yds": [
        (20, (-1.453, 0.0, 0.877, 2.335, 5.043)),
        (45, (0.082, 0.344, 0.843, 1.651, 2.585)),
        (75, (0.195, 0.458, 0.88, 1.402, 1.978)),
        (1e+09, (0.329, 0.572, 0.887, 1.267, 1.621)),
    ],
    "rush_car": [
        (6, (0.274, 0.583, 1.033, 1.641, 2.285)),
        (11, (0.332, 0.61, 0.959, 1.379, 1.802)),
        (16, (0.468, 0.713, 0.973, 1.279, 1.577)),
        (1e+09, (0.508, 0.751, 0.979, 1.239, 1.44)),
    ],
    "rec": [
        (1.8, (0.0, 0.0, 0.792, 1.865, 3.301)),
        (3, (0.371, 0.482, 0.932, 1.467, 2.073)),
        (4.5, (0.298, 0.562, 0.902, 1.316, 1.784)),
        (1e+09, (0.362, 0.592, 0.869, 1.226, 1.547)),
    ],
    "rec_yds": [
        (20, (0.0, 0.0, 0.603, 1.944, 4.199)),
        (35, (0.139, 0.379, 0.854, 1.575, 2.45)),
        (55, (0.185, 0.398, 0.815, 1.435, 2.072)),
        (1e+09, (0.213, 0.439, 0.792, 1.249, 1.689)),
    ],
}

# Minimum role for a player to be published, by stat family.
MIN_ROLE = {"pass_att": 12.0, "rush_car": 4.0, "rec": 1.5}

MARKETS = {
    "QB": ("pass_yds", "pass_att", "pass_cmp", "pass_td", "pass_int", "rush_yds", "rush_car"),
    "RB": ("rush_yds", "rush_car", "rec", "rec_yds"),
    "WR": ("rec", "rec_yds"),
    "TE": ("rec", "rec_yds"),
}


@dataclass(frozen=True)
class Environment:
    """What the game is expected to look like from one team's side."""

    margin: float          # expected team margin (positive = favoured)
    total: float           # expected game total


@dataclass
class PlayerProjection:
    team_id: str
    opponent_id: str
    athlete_id: str
    name: str
    position: str
    games: int                         # games in the share estimate (this season)
    metrics: dict[str, float] = field(default_factory=dict)
    anytime_td: float | None = None
    # The player's designation on this week's conference availability report
    # (questionable, probable, ...). None when unlisted or no report exists.
    availability: str | None = None
    # Recency-weighted shares of team volume - the "why" behind a projection.
    usage: dict[str, float] = field(default_factory=dict)
    # Teammates listed out whose usage this player inherits this week.
    absorbs: tuple[str, ...] = ()


def team_totals(box: GameBox, team_id: str) -> dict[str, float]:
    out = dict.fromkeys(TEAM_STATS, 0.0)
    for p in box.players:
        if p.team_id != team_id:
            continue
        out["pass_att"] += p.pass_att
        out["pass_cmp"] += p.pass_cmp
        out["pass_yds"] += p.pass_yds
        out["pass_td"] += p.pass_td
        out["pass_int"] += p.pass_int
        out["rush_car"] += p.rush_car
        out["rush_yds"] += p.rush_yds
        out["rush_td"] += p.rush_td
    return out


def _order(box: GameBox) -> tuple:
    return (box.season, box.week, box.start)


class History:
    """Point-in-time index over completed box scores."""

    def __init__(self, boxes: list[GameBox]):
        self.boxes = sorted((b for b in boxes if b.completed and b.players), key=_order)
        self.by_team: dict[str, list[GameBox]] = defaultdict(list)
        self.totals: dict[tuple[str, str], dict[str, float]] = {}
        for box in self.boxes:
            for t in box.teams:
                self.by_team[t.team_id].append(box)
                self.totals[(box.event_id, t.team_id)] = team_totals(box, t.team_id)

    def before(self, team_id: str, season: int, week: int) -> list[GameBox]:
        """Team games strictly before (season, week), oldest first, this season
        and the one before only."""
        return [b for b in self.by_team.get(team_id, [])
                if (b.season, b.week) < (season, week) and b.season >= season - 1]

    def league(self, season: int, week: int) -> dict[str, float]:
        rows = [self.totals[(b.event_id, t.team_id)] for b in self.boxes
                if (b.season, b.week) < (season, week) and b.season >= season - 1
                for t in b.teams]
        if not rows:
            return dict(LEAGUE_FALLBACK)
        return {s: sum(r[s] for r in rows) / len(rows) for s in TEAM_STATS}

    def league_points(self, season: int, week: int) -> float:
        pts = [t.points for b in self.boxes
               if (b.season, b.week) < (season, week) and b.season >= season - 1
               for t in b.teams if t.points is not None]
        return sum(pts) / len(pts) if pts else 28.5


LEAGUE_FALLBACK = {"pass_att": 31.0, "pass_cmp": 19.5, "pass_yds": 232.0, "pass_td": 1.65,
                   "pass_int": 0.75, "rush_car": 37.0, "rush_yds": 165.0, "rush_td": 1.75}


def _team_means(hist: History, team_id: str, season: int, week: int,
                league: dict[str, float], *, allowed: bool) -> tuple[dict[str, float], float]:
    """Recency-weighted per-game means (own output, or what the defence allowed),
    shrunk toward the league. Returns (means, effective games)."""
    games = hist.before(team_id, season, week)
    acc = defaultdict(float)
    weight = 0.0
    for age, box in enumerate(reversed(games)):
        w = 0.5 ** (age / TEAM_HALF_LIFE)
        if box.season != season:
            w *= PRIOR_SEASON_WEIGHT
        side = box.opponent(team_id) if allowed else team_id
        if side is None:
            continue
        row = hist.totals.get((box.event_id, side))
        if row is None:
            continue
        weight += w
        for s in TEAM_STATS:
            acc[s] += w * row[s]
    means = {s: (acc[s] + TEAM_PSEUDO_GAMES * league[s]) / (weight + TEAM_PSEUDO_GAMES)
             for s in TEAM_STATS}
    return means, weight


def team_features(hist: History, team_id: str, opponent_id: str, season: int, week: int,
                  env: Environment) -> dict[str, tuple[float, float, float, float]]:
    """Per stat: (own dev, opponent-allowed dev, team margin, implied points dev)."""
    league = hist.league(season, week)
    own, _ = _team_means(hist, team_id, season, week, league, allowed=False)
    opp, _ = _team_means(hist, opponent_id, season, week, league, allowed=True)
    implied = (env.total + env.margin) / 2.0
    points_dev = implied - hist.league_points(season, week)
    return {s: (own[s] - league[s], opp[s] - league[s], env.margin, points_dev)
            for s in TEAM_STATS}, league


def team_environment(hist: History, team_id: str, opponent_id: str, season: int, week: int,
                     env: Environment) -> dict[str, float]:
    feats, league = team_features(hist, team_id, opponent_id, season, week, env)
    out = {}
    for s, x in feats.items():
        coef = TEAM_COEF[s]
        out[s] = max(0.0, league[s] + sum(c * v for c, v in zip(coef, x)))
    return out


# -- players ------------------------------------------------------------------
_SHARE_FIELDS = {
    "pass_att": "pass_att", "pass_cmp": "pass_cmp", "pass_yds": "pass_yds",
    "pass_td": "pass_td", "pass_int": "pass_int",
    "rush_car": "rush_car", "rush_yds": "rush_yds", "rush_td": "rush_td",
    "rec": "pass_cmp", "rec_yds": "pass_yds", "rec_td": "pass_td",
}


def _infer_position(lines: list[PlayerLine]) -> str:
    att = sum(p.pass_att for p in lines)
    car = sum(p.rush_car for p in lines)
    rec = sum(p.rec for p in lines)
    if att > car and att >= 3.0 * len(lines):
        return "QB"
    if car > 1.5 * rec:
        return "RB"
    return "WR"


def _player_shares(hist: History, team_id: str, season: int, week: int
                   ) -> tuple[dict[str, dict[str, float]], dict[str, dict], set[str]]:
    """Recency-weighted share of each team stat, per athlete, over games played.

    Returns (shares, info, eligible). Eligible athletes played in the team's
    most recent game this season.
    """
    games = hist.before(team_id, season, week)
    current = [b for b in games if b.season == season]
    last = current[-1] if current else None
    eligible = ({p.athlete_id for p in last.players if p.team_id == team_id}
                if last else set())
    num = defaultdict(lambda: defaultdict(float))
    den = defaultdict(lambda: defaultdict(float))
    info: dict[str, dict] = {}
    for age, box in enumerate(reversed(games)):
        w = 0.5 ** (age / PLAYER_HALF_LIFE)
        if box.season != season:
            w *= PRIOR_SEASON_PLAYER_WEIGHT
        team = hist.totals[(box.event_id, team_id)]
        for p in box.players:
            if p.team_id != team_id:
                continue
            item = info.setdefault(p.athlete_id, {"name": p.name, "lines": [], "games": 0})
            item["lines"].append(p)
            if box.season == season:
                item["games"] += 1
            for stat, team_field in _SHARE_FIELDS.items():
                denom = team[team_field]
                if denom <= 0:
                    continue
                num[p.athlete_id][stat] += w * getattr(p, stat)
                den[p.athlete_id][stat] += w * denom
    shares = {aid: {stat: (num[aid][stat] / den[aid][stat]) if den[aid][stat] else 0.0
                    for stat in _SHARE_FIELDS}
              for aid in info}
    return shares, info, eligible


def _passer(shares: dict, eligible: set[str]) -> str | None:
    """Whoever threw most, recency-weighted, among players still in the role."""
    qbs = sorted((aid for aid in eligible if shares[aid]["pass_att"] > 0),
                 key=lambda aid: (-shares[aid]["pass_att"], aid))
    return qbs[0] if qbs else None


# Usage a ruled-out player carried flows to the teammates still available, but a
# team that loses most of its backfield does not get one back three times over.
MAX_USAGE_SCALE = 1.5
_REDISTRIBUTED = ("rush_car", "rush_yds", "rush_td", "rec", "rec_yds", "rec_td")


def usual_starter(hist: History, team_id: str, season: int, week: int
                  ) -> tuple[str, str] | None:
    """(athlete_id, name) of the quarterback the box scores say has been starting."""
    shares, info, eligible = _player_shares(hist, team_id, season, week)
    aid = _passer(shares, eligible)
    return (aid, info[aid]["name"]) if aid else None


def project_team(hist: History, team_id: str, opponent_id: str, season: int, week: int,
                 env: Environment, positions: dict[str, str] | None = None,
                 report=None) -> list[PlayerProjection]:
    """``report`` is the team's `availability.TeamReport` for this game, if filed."""
    from .sources.availability import UNAVAILABLE

    shares, info, eligible = _player_shares(hist, team_id, season, week)
    if not eligible:
        return []
    status_of = ({aid: report.status_of(info[aid]["name"]) for aid in eligible}
                 if report is not None else {})
    ruled_out = {aid for aid, status in status_of.items() if status in UNAVAILABLE}
    if ruled_out:
        eligible = eligible - ruled_out
        shares = {aid: dict(values) for aid, values in shares.items()}
        for stat in _REDISTRIBUTED:
            lost = sum(shares[aid][stat] for aid in ruled_out)
            kept = sum(shares[aid][stat] for aid in eligible)
            if lost > 0 and kept > 0:
                scale = min(MAX_USAGE_SCALE, (kept + lost) / kept)
                for aid in eligible:
                    shares[aid][stat] *= scale
    team = team_environment(hist, team_id, opponent_id, season, week, env)
    team_tds = team["pass_td"] + team["rush_td"]
    out: list[PlayerProjection] = []
    # The passer: whoever threw most among those not ruled out carries the QB
    # role - so a listed-out starter hands the role to his backup.
    starter = _passer(shares, eligible)

    for aid in eligible:
        share = shares.get(aid)
        if not share:
            continue
        position = (positions or {}).get(aid) or _infer_position(info[aid]["lines"])
        if position == "QB" and aid != starter:
            continue
        m: dict[str, float] = {}
        if aid == starter:
            # A starter's attempt share is his share in the games he played, not
            # diluted by blowouts where the backup finished - floor it.
            att_share = max(share["pass_att"], 0.88)
            scale = att_share / share["pass_att"] if share["pass_att"] else 1.0
            m["pass_att"] = team["pass_att"] * att_share
            m["pass_cmp"] = team["pass_cmp"] * min(0.99, share["pass_cmp"] * scale)
            m["pass_yds"] = team["pass_yds"] * min(0.99, share["pass_yds"] * scale)
            m["pass_td"] = team["pass_td"] * min(0.99, share["pass_td"] * scale
                                                 if share["pass_td"] else att_share)
            m["pass_int"] = team["pass_int"] * att_share
            position = "QB"
        m["rush_car"] = team["rush_car"] * share["rush_car"]
        m["rush_yds"] = team["rush_yds"] * share["rush_yds"]
        m["rec"] = team["pass_cmp"] * share["rec"]
        m["rec_yds"] = team["pass_yds"] * share["rec_yds"]
        if position not in MARKETS:
            continue
        if position == "QB":
            keep = m.get("pass_att", 0.0) >= MIN_ROLE["pass_att"]
        elif position == "RB":
            keep = m["rush_car"] >= MIN_ROLE["rush_car"] or m["rec"] >= MIN_ROLE["rec"]
        else:
            keep = m["rec"] >= MIN_ROLE["rec"]
        if not keep:
            continue
        # A market the player has no role in is not published: a back who has
        # not caught a pass would otherwise show "0.0 receptions" lines.
        if m["rec"] < 0.5:
            m.pop("rec"), m.pop("rec_yds")
        # A pocket passer's rushing nets out near zero or below (NCAA scoring
        # counts sacks), where a ratio-of-projection range is meaningless.
        if m["rush_car"] < 2.0 or m["rush_yds"] < 8.0:
            m.pop("rush_car"), m.pop("rush_yds")
        # Touchdowns: the player's share of team scoring, shrunk toward his share
        # of touches (TDs are rare enough that a raw share is mostly noise).
        touch = share["rush_car"] * team["rush_car"] + share["rec"] * team["pass_cmp"]
        touches = team["rush_car"] + team["pass_cmp"]
        touch_share = touch / touches if touches else 0.0
        td_share = 0.5 * (share["rush_td"] * team["rush_td"] + share["rec_td"] * team["pass_td"]) \
            / max(team_tds, 1e-9) + 0.5 * touch_share
        lam = team_tds * td_share
        out.append(PlayerProjection(
            team_id=team_id, opponent_id=opponent_id, athlete_id=aid,
            name=info[aid]["name"], position=position, games=info[aid]["games"],
            metrics={k: m[k] for k in MARKETS[position] if k in m},
            anytime_td=1.0 - math.exp(-lam) if lam > 0 else 0.0,
            availability=status_of.get(aid),
            usage={key: round(share[stat], 3) for key, stat in (
                ("pass_attempts", "pass_att"), ("carries", "rush_car"), ("targets", "rec"))
                if share.get(stat)},
            absorbs=tuple(sorted(
                info[out]["name"] for out in ruled_out
                if any(shares[out].get(stat, 0.0) > 0.05 and share.get(stat, 0.0) > 0
                       for stat in _REDISTRIBUTED)
            )) if ruled_out else (),
        ))
    return out


# -- distributions ------------------------------------------------------------
def ratio_quantiles(stat: str, mean: float) -> tuple[float, ...] | None:
    buckets = RATIO_QUANTILES.get(stat)
    if not buckets:
        return None
    for upper, quantiles in buckets:
        if mean <= upper:
            return quantiles
    return buckets[-1][1]


# Small counts are priced from a negative binomial, not the quantile ladder:
# a ladder interpolates between whole numbers, and on 2025 it put interceptions
# over 0.5 at 55% against 47% observed. Size 8 was the best-calibrated of
# Poisson / NB(3) / NB(8) for all three (scripts/fit_player_props.py).
COUNT_STATS = ("pass_td", "pass_int", "rec")
COUNT_SIZE = 8.0


def _nb_pmf(mean: float, size: float = COUNT_SIZE) -> dict[str, float]:
    p = size / (size + mean)
    pmf: dict[str, float] = {}
    cumulative = 0.0
    for k in range(0, 40):
        mass = math.exp(math.lgamma(k + size) - math.lgamma(size) - math.lgamma(k + 1)
                        + size * math.log(p) + k * math.log(1 - p)) if mean > 0 else float(k == 0)
        cumulative += mass
        if mass >= 5e-4:
            pmf[str(k)] = round(mass, 4)
        if cumulative > 0.9995:
            break
    return pmf


def _pmf_quantile(pmf: dict[str, float], level: float) -> float:
    cumulative = 0.0
    for k in sorted(pmf, key=int):
        cumulative += pmf[k]
        if cumulative >= level:
            return float(k)
    return float(max(pmf, key=int))


def distribution(stat: str, mean: float) -> dict[str, float]:
    """Mean and 10th/50th/90th percentiles. Counts carry a ``pmf``; everything
    else the quantile ladder used to price any line (``q`` at ``q_levels``)."""
    out = {"mean": round(mean, 2)}
    if stat in COUNT_STATS:
        pmf = _nb_pmf(mean)
        out.update({"p10": _pmf_quantile(pmf, 0.10), "p50": _pmf_quantile(pmf, 0.50),
                    "p90": _pmf_quantile(pmf, 0.90), "pmf": pmf})
        return out
    q = ratio_quantiles(stat, mean)
    if q is None:
        return out
    ladder = [round(mean * r, 1) for r in q]
    out.update({"p10": ladder[0], "p50": ladder[2], "p90": ladder[-1],
                "q": ladder, "q_levels": list(QUANTILE_LEVELS)})
    return out


def over_probability(dist: dict, line: float) -> float | None:
    """P(stat > line): summed from the pmf for counts, else interpolated on the
    quantile ladder (flat tails)."""
    if dist.get("pmf"):
        return sum(v for k, v in dist["pmf"].items() if float(k) > line)
    ladder, levels = dist.get("q"), dist.get("q_levels")
    if not ladder or not levels:
        return None
    if line < ladder[0]:
        return 1.0 - levels[0]
    if line >= ladder[-1]:
        return 1.0 - levels[-1]
    for (x0, p0), (x1, p1) in zip(zip(ladder, levels), zip(ladder[1:], levels[1:])):
        if x0 <= line < x1:
            p = p0 if x1 == x0 else p0 + (p1 - p0) * (line - x0) / (x1 - x0)
            return 1.0 - p
    return None


# -- the live slate -----------------------------------------------------------
HEADSHOT = "https://a.espncdn.com/i/headshots/college-football/players/full/{}.png"
PUBLISHED_POSITIONS = {"QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE"}


def _environment_for(forecast, home: bool) -> tuple[Environment | None, str]:
    """The game as the sportsbook sees it when a line is posted, else the
    published forecast (which is the market at lam 0), else the model."""
    margin = getattr(forecast, "book_margin", None)
    total = getattr(forecast, "book_total", None)
    source = "DraftKings spread and total"
    if margin is None or total is None:
        margin = forecast.margin if forecast.margin is not None else forecast.model_margin
        total = forecast.projected_total if forecast.projected_total is not None \
            else forecast.market_total
        source = "published forecast"
    if margin is None or total is None:
        return None, "no game environment"
    return Environment(margin=float(margin) if home else -float(margin),
                       total=float(total)), source


def build_slate(season: int, week: int, forecasts: list[tuple],
                reports: dict | None = None) -> tuple[list[dict], dict]:
    """Projections for every player on this week's board.

    ``forecasts`` is the board's (Forecast, kickoff) rows. Returns (projections,
    status). Never raises for a feed problem: the caller publishes the board
    either way, and the status says what happened.
    """
    from .sources import espn_box
    from .sources.oddsapi import normalise

    from .sources import availability

    status = {"model_version": MODEL_VERSION, "authority": "RESEARCH_ONLY",
              "source": "ESPN box scores", "games": 0, "players": 0, "issues": [],
              "teams": {}}
    try:
        events = espn_box.week_events(season, week)
    except Exception as exc:
        status["issues"].append(f"ESPN scoreboard unavailable: {exc}")
        return [], status
    boxes = espn_box.season_boxes(season - 1) + espn_box.season_boxes(season, through_week=week - 1)
    hist = History(boxes)
    by_pair = {}
    for event in events:
        comp = (event.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): c.get("team") or {} for c in comp.get("competitors") or []}
        home, away = sides.get("home") or {}, sides.get("away") or {}
        if home.get("id") and away.get("id"):
            by_pair[(normalise(home.get("location") or ""), normalise(away.get("location") or ""))] = (
                str(home["id"]), str(away["id"]), str(event.get("id") or ""))

    out: list[dict] = []
    for forecast, kickoff in forecasts:
        ids = by_pair.get((normalise(forecast.home), normalise(forecast.away)))
        if ids is None:
            status["issues"].append(f"no ESPN event for {forecast.away} at {forecast.home}")
            continue
        status["games"] += 1
        for school, opponent, team_id, opp_id, home in (
                (forecast.home, forecast.away, ids[0], ids[1], True),
                (forecast.away, forecast.home, ids[1], ids[0], False)):
            kickoff_date = kickoff.isoformat()[:10] if kickoff else None
            report = availability.report_for(reports or {}, school, kickoff_date)
            usual = usual_starter(hist, team_id, season, week)
            qb_status = report.status_of(usual[1]) if (report and usual) else None
            status["teams"][school] = {
                "starting_qb": usual[1] if usual else None,
                "starting_qb_status": qb_status,
                "report": report.to_json() if report else None,
            }
            env, env_source = _environment_for(forecast, home)
            if env is None:
                continue
            positions = {aid: PUBLISHED_POSITIONS.get(pos, pos)
                         for aid, pos in espn_box.roster_positions(team_id).items()}
            for proj in project_team(hist, team_id, opp_id, season, week, env, positions,
                                     report=report):
                out.append({
                    "game_key": f"{forecast.away} @ {forecast.home}",
                    "event_id": ids[2],
                    "team_id": team_id,
                    "kickoff": kickoff.isoformat().replace("+00:00", "Z") if kickoff else None,
                    "team": school,
                    "opponent": opponent,
                    "home": home,
                    "player_id": proj.athlete_id,
                    "player_name": proj.name,
                    "position": proj.position,
                    "games": proj.games,
                    "headshot_url": HEADSHOT.format(proj.athlete_id),
                    "environment": env_source,
                    "stats": {k: distribution(k, v) for k, v in proj.metrics.items()},
                    "anytime_td": round(proj.anytime_td, 3) if proj.anytime_td is not None else None,
                    "availability": proj.availability,
                    "usage": proj.usage,
                    "absorbs": list(proj.absorbs),
                    "team_implied_points": round((env.total + env.margin) / 2.0, 1),
                    "team_margin": round(env.margin, 1),
                    "model_version": MODEL_VERSION,
                })
    status["players"] = len(out)
    return out, status
