"""Weekly best bets: the board's strongest disagreements, each with its angle.

Three families, ranked within each by the model's probability that the side
hits:

* **Spreads** - independent model margin against the DraftKings spread.
* **Totals** - independent total against the DraftKings total.
* **Player props** - the projection's distribution against a DraftKings prop
  line (only for the games whose props were pulled this week).

Every pick carries an ``angle``: a few sentences built from the same numbers
that produced it - which efficiency matchups drive the margin and where both
teams rank in FBS, home-field context, the conference availability report, a
player's usage share and the teammates whose touches he inherits. Nothing in an
angle is invented; if the model has no observed form yet, the angle says the
pick rests on power ratings.

**What these are not.** Authority is ``RESEARCH_ONLY``: the walk-forward ATS
rate on model disagreements is 51.1% (95% CI 49.4-52.8%) against a 52.4%
breakeven. These are the board's best *candidates*, published with their own
graded record (``ledger``) so they can prove themselves or not. Probabilities
assume a Normal error around the model with the model's measured error, which
is the model's confidence, not a market-beating claim.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from cfbmodel import matrix

# Spread of the independent model's errors (walk-forward MAE 12.5 -> SD ~15.6
# for a Normal; the README's measured margin SD for CFB is far wider than the
# NFL's). Totals: residual SD 16.36 (README, totals section).
SPREAD_SIGMA = 15.6
TOTAL_SIGMA = 16.4
# A disagreement smaller than this is inside the model's own noise floor and is
# not listed, however the week's slate looks.
MIN_SPREAD_EDGE = 3.0
MIN_TOTAL_EDGE = 4.0
MIN_PROP_PROBABILITY = 0.56
MIN_PROP_EDGE = 0.04          # over the de-vigged book probability
# Above this the "edge" is a data problem (wrong team match, stale line), not a pick.
MAX_SPREAD_EDGE = 21.0
MAX_TOTAL_EDGE = 21.0
LIMITS = {"spread": 5, "total": 3, "prop": 6}
DEFAULT_PRICE = -110

_FEATURES = {
    "off_ppa": ("offense", "EPA per play", "high"),
    "off_successRate": ("offense", "success rate", "high"),
    "off_explosiveness": ("offense", "explosiveness", "high"),
    "off_stuffRate": ("offense", "stuffed-run rate", "low"),
    "def_ppa": ("defense", "EPA per play allowed", "low"),
    "def_successRate": ("defense", "success rate allowed", "low"),
    "def_explosiveness": ("defense", "explosiveness allowed", "low"),
    "def_stuffRate": ("defense", "run-stuff rate", "high"),
}
_STAT_LABEL = {
    "pass_yds": "passing yards", "pass_td": "passing TDs", "pass_att": "pass attempts",
    "rush_yds": "rushing yards", "rush_car": "carries", "rec": "receptions",
    "rec_yds": "receiving yards",
}
_QB_OUT = {"out", "doubtful", "out_first_half"}


@dataclass
class Pick:
    family: str                 # spread | total | prop
    week: int
    season: int
    home: str
    away: str
    kickoff: str | None
    selection: str              # "Iowa +7.5", "Over 51.5", "J. Smith over 74.5 rushing yards"
    side: str                   # home | away | over | under
    line: float
    price: int
    model_number: float
    book_number: float
    edge: float                 # points (games) or probability over book (props)
    probability: float
    angle: str
    tags: list[str] = field(default_factory=list)
    player: str | None = None
    player_id: str | None = None
    team_id: str | None = None
    event_id: str | None = None
    stat: str | None = None

    @property
    def pick_id(self) -> str:
        parts = [str(self.season), str(self.week), self.family, self.away, self.home]
        if self.family == "prop":
            parts += [self.player_id or self.player or "", self.stat or ""]
        return "|".join(parts)

    def to_json(self) -> dict:
        out = asdict(self)
        out["pick_id"] = self.pick_id
        return out


# -- helpers ------------------------------------------------------------------
def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _implied(price: int | None) -> float | None:
    if price is None or price == 0:
        return None
    return 100.0 / (price + 100.0) if price > 0 else -price / (-price + 100.0)


def _fmt_line(value: float) -> str:
    return f"{value:+.1f}".replace("+0.0", "PK") if value else "PK"


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def fbs_ranks(forms: dict, schools: set[str] | None = None) -> dict[str, dict[str, int]]:
    """team -> feature -> FBS rank (1 = best), over teams with complete form."""
    pool = {t: f for t, f in forms.items()
            if f is not None and f.complete() and (schools is None or t in schools)}
    ranks: dict[str, dict[str, int]] = {t: {} for t in pool}
    for key, (_, _, better) in _FEATURES.items():
        ordered = sorted(pool, key=lambda t: getattr(pool[t], key),
                         reverse=(better == "high"))
        for i, team in enumerate(ordered, 1):
            ranks[team][key] = i
    return ranks


def _drivers(row, ranks: dict, toward_home: bool, limit: int = 2) -> list[str]:
    """The efficiency matchups that push the model toward the picked side."""
    home_form, away_form = row.home_form, row.away_form
    if not (home_form and away_form and home_form.complete() and away_form.complete()):
        return []
    c = matrix.COEFFICIENTS
    f = row.forecast
    pushes = []
    for key in _FEATURES:
        points = c[key] * (getattr(home_form, key) - getattr(away_form, key))
        if (points > 0) == toward_home and abs(points) >= 0.5:
            pushes.append((abs(points), key))
    out = []
    for points, key in sorted(pushes, reverse=True)[:limit]:
        unit, label, _ = _FEATURES[key]
        team = f.home if toward_home else f.away
        other = f.away if toward_home else f.home
        rank_t = ranks.get(team, {}).get(key)
        rank_o = ranks.get(other, {}).get(key)
        if rank_t and rank_o:
            out.append(f"{team}'s {unit} ranks {_ordinal(rank_t)} in FBS in {label} "
                       f"to {other}'s {_ordinal(rank_o)} (worth {points:.1f} pts)")
        else:
            out.append(f"{team} has the edge in {unit} {label} (worth {points:.1f} pts)")
    return out


def _availability_notes(team_status: dict, picked: str,
                        opponent: str) -> tuple[list[str], list[str]]:
    """(supporting notes, risk notes) from the conference availability reports."""
    support, risk = [], []
    for school, bucket, frame in ((opponent, support, "helps"), (picked, risk, "risk")):
        info = (team_status or {}).get(school) or {}
        report = info.get("report")
        if not report:
            continue
        qb, status = info.get("starting_qb"), info.get("starting_qb_status")
        if qb and status:
            bucket.append(f"{school} QB {qb} is listed {status.replace('_', ' ')}")
        out = [d for d in report.get("designations") or []
               if d.get("status") in ("out", "doubtful")
               and d.get("position") not in (None, "", "QB")]
        if len(out) >= 3:
            names = ", ".join(f"{d['position']} {d['player']}" for d in out[:3])
            bucket.append(f"{school} lists {len(out)} players out or doubtful ({names}"
                          f"{', ...' if len(out) > 3 else ''})")
    return support, risk


def _regime_note(row) -> str | None:
    f = row.forecast
    if not f.used_efficiency:
        return ("No observed efficiency this week, so this rests on power ratings and "
                "the preseason prior - treat it as low confidence.")
    if 0.0 < f.efficiency_reliability < 1.0:
        return (f"Early season: observed form carries {f.efficiency_reliability:.0%} of the "
                "estimate, the preseason prior the rest.")
    return None


# -- game picks ---------------------------------------------------------------
def spread_pick(row, *, season: int, week: int, ranks: dict, team_status: dict) -> Pick | None:
    f = row.forecast
    if f.model_margin is None or f.book_margin is None:
        return None
    if f.edge_withheld_reason and "availability report" in f.edge_withheld_reason:
        return None  # a starting QB is out: the model is rating the wrong team
    edge = f.model_margin - f.book_margin
    if not MIN_SPREAD_EDGE <= abs(edge) <= MAX_SPREAD_EDGE:
        return None
    home_side = edge > 0
    team, opp = (f.home, f.away) if home_side else (f.away, f.home)
    line = -f.book_margin if home_side else f.book_margin   # the picked team's spread
    model_line = -f.model_margin if home_side else f.model_margin
    prob = _phi(abs(edge) / SPREAD_SIGMA)
    fav = f.home if f.model_margin > 0 else f.away
    sentences = [
        f"The model makes it {fav} by {abs(f.model_margin):.1f}; DraftKings has "
        f"{team} {_fmt_line(line)}, a {abs(edge):.1f}-point disagreement toward {team}."
    ]
    drivers = _drivers(row, ranks, home_side)
    if drivers:
        sentences.append("Driven by: " + "; ".join(drivers) + ".")
    hfp = 0.0 if f.neutral else f.home_field_points
    if not f.neutral and abs(hfp - 4.53) >= 1.0:
        sentences.append(f"Home field is worth {hfp:.1f} pts at this venue "
                         f"({'more' if hfp > 4.53 else 'less'} than the FBS-average 4.5: "
                         "altitude, travel and time zones).")
    support, risk = _availability_notes(team_status, team, opp)
    if support:
        sentences.append("Injury report: " + "; ".join(support) + ".")
    if risk:
        sentences.append("Risk: " + "; ".join(risk) + ".")
    regime = _regime_note(row)
    if regime:
        sentences.append(regime)
    tags = ["early-season"] if not f.in_validated_regime else []
    tags += ["injury-report"] if support else []
    return Pick("spread", week, season, f.home, f.away, _iso(row.kickoff_utc),
                f"{team} {_fmt_line(line)}", "home" if home_side else "away", line,
                DEFAULT_PRICE, round(model_line, 1), line, round(abs(edge), 1),
                round(prob, 3), " ".join(sentences), tags)


def total_pick(row, *, season: int, week: int, ranks: dict, team_status: dict) -> Pick | None:
    f = row.forecast
    if f.independent_total is None or f.book_total is None:
        return None
    edge = f.independent_total - f.book_total
    if not MIN_TOTAL_EDGE <= abs(edge) <= MAX_TOTAL_EDGE:
        return None
    over = edge > 0
    prob = _phi(abs(edge) / TOTAL_SIGMA)
    sentences = [f"The model projects {f.independent_total:.1f} combined points against a "
                 f"DraftKings total of {f.book_total:.1f} ({abs(edge):.1f} "
                 f"{'over' if over else 'under'})."]
    pace = []
    for team in (f.away, f.home):
        form = row.away_form if team == f.away else row.home_form
        r = ranks.get(team, {})
        if form is not None and r.get("off_ppa") and r.get("def_ppa"):
            pace.append(f"{team} offense {_ordinal(r['off_ppa'])} / defense "
                        f"{_ordinal(r['def_ppa'])} in EPA per play")
    if pace:
        sentences.append("Efficiency: " + "; ".join(pace) + ".")
    plays = [x for x in ((row.home_form.plays if row.home_form else None),
                         (row.away_form.plays if row.away_form else None)) if x]
    if len(plays) == 2:
        sentences.append(f"Pace: the two teams average {sum(plays) / 2:.0f} plays a game.")
    for school in (f.away, f.home):
        info = (team_status or {}).get(school) or {}
        if info.get("starting_qb_status"):
            sentences.append(f"{school} QB {info.get('starting_qb')} is listed "
                             f"{info['starting_qb_status'].replace('_', ' ')}.")
    regime = _regime_note(row)
    if regime:
        sentences.append(regime)
    tags = ["early-season"] if not f.in_validated_regime else []
    return Pick("total", week, season, f.home, f.away, _iso(row.kickoff_utc),
                f"{'Over' if over else 'Under'} {f.book_total:.1f}", "over" if over else "under",
                f.book_total, DEFAULT_PRICE, round(f.independent_total, 1), f.book_total,
                round(abs(edge), 1), round(prob, 3), " ".join(sentences), tags)


def _iso(moment) -> str | None:
    return moment.isoformat().replace("+00:00", "Z") if moment else None


# -- props --------------------------------------------------------------------
def prop_picks(projections: list[dict], quotes: list, *, season: int, week: int,
               ranks: dict | None = None) -> list[Pick]:
    from cfbmodel import player_props
    from cfbmodel.sources.availability import name_key

    by_player: dict[tuple[str, str], dict] = {}
    for row in projections:
        by_player[(row.get("team") or "", name_key(row.get("player_name") or ""))] = row
    out: list[Pick] = []
    for quote in quotes:
        row = None
        for team in (quote.home, quote.away):
            row = by_player.get((team, name_key(quote.player)))
            if row:
                break
        if row is None:
            continue
        dist = (row.get("stats") or {}).get(quote.stat)
        if not dist:
            continue
        p_over = player_props.over_probability(dist, quote.line)
        if p_over is None:
            continue
        over_imp, under_imp = _implied(quote.over_price), _implied(quote.under_price)
        if over_imp and under_imp:   # remove the vig
            total = over_imp + under_imp
            over_imp, under_imp = over_imp / total, under_imp / total
        for side, prob, implied, price in (("over", p_over, over_imp, quote.over_price),
                                           ("under", 1.0 - p_over, under_imp, quote.under_price)):
            if implied is None or price is None:
                continue
            if prob < MIN_PROP_PROBABILITY or prob - implied < MIN_PROP_EDGE:
                continue
            out.append(_prop_pick(row, quote, side, prob, implied, price, dist,
                                  season=season, week=week, ranks=ranks or {}))
    return out


def _prop_pick(row, quote, side, prob, implied, price, dist, *, season, week, ranks) -> Pick:
    stat_label = _STAT_LABEL.get(quote.stat, quote.stat)
    mean = dist.get("mean")
    sentences = [f"Projects {mean:.1f} {stat_label} (middle 80%: {dist.get('p10', mean):.0f}-"
                 f"{dist.get('p90', mean):.0f}) against a line of {quote.line:g}; "
                 f"{prob:.0%} to go {side} versus {implied:.0%} priced in."]
    usage = row.get("usage") or {}
    share_bits = []
    for key, label in (("pass_attempts", "of team pass attempts"), ("carries", "of team carries"),
                       ("targets", "of team receptions")):
        if usage.get(key, 0) >= 0.1:
            share_bits.append(f"{usage[key]:.0%} {label}")
    if share_bits:
        sentences.append(f"Usage: {', '.join(share_bits)} (recency-weighted, "
                         f"{row.get('games', 0)} games this season).")
    if row.get("absorbs"):
        sentences.append(f"Inherits work from {', '.join(row['absorbs'])} "
                         "(listed out on the availability report).")
    implied_pts, margin = row.get("team_implied_points"), row.get("team_margin")
    if implied_pts is not None and margin is not None:
        script = ("expected to lead, which leans run-heavy late" if margin >= 10 else
                  "expected to trail, which leans pass-heavy late" if margin <= -10 else
                  "a close game script")
        sentences.append(f"{row.get('team')} is implied for {implied_pts:.1f} points; {script}.")
    opp = row.get("opponent")
    r = ranks.get(opp, {})
    if r.get("def_ppa") and r.get("def_explosiveness"):
        sentences.append(f"{opp}'s defense ranks {_ordinal(r['def_ppa'])} in FBS in EPA per "
                         f"play allowed and {_ordinal(r['def_explosiveness'])} in "
                         "explosiveness allowed.")
    if row.get("availability"):
        sentences.append(f"Note: {row.get('player_name')} is listed "
                         f"{row['availability'].replace('_', ' ')}.")
    name = row.get("player_name") or quote.player
    return Pick("prop", week, season, quote.home, quote.away, row.get("kickoff"),
                f"{name} {side} {quote.line:g} {stat_label}", side, quote.line, price,
                round(mean, 1), quote.line, round(prob - implied, 3), round(prob, 3),
                " ".join(sentences), ["questionable"] if row.get("availability") else [],
                player=name, player_id=row.get("player_id"), team_id=row.get("team_id"),
                event_id=row.get("event_id"), stat=quote.stat)


# -- the slate ----------------------------------------------------------------
def build(rows: list, *, season: int, week: int, forms: dict, team_status: dict,
          projections: list[dict] | None = None, quotes: list | None = None,
          fbs_schools: set[str] | None = None) -> list[Pick]:
    """The week's best bets, strongest first within each family.

    ``fbs_schools`` restricts the ranking pool, so "ranks 12th in FBS" is not
    padded by FCS opponents that also have form.
    """
    ranks = fbs_ranks(forms, fbs_schools) if forms else {}
    picks: list[Pick] = []
    for row in rows:
        for maker in (spread_pick, total_pick):
            pick = maker(row, season=season, week=week, ranks=ranks, team_status=team_status)
            if pick is not None:
                picks.append(pick)
    picks += prop_picks(projections or [], quotes or [], season=season, week=week, ranks=ranks)
    out: list[Pick] = []
    for family, limit in LIMITS.items():
        ranked = sorted((p for p in picks if p.family == family),
                        key=lambda p: (-p.probability, -p.edge))
        seen: set[str] = set()
        for pick in ranked:
            # One pick per game per family (props: one per player).
            key = pick.player_id if family == "prop" else f"{pick.away}@{pick.home}"
            if key in seen:
                continue
            seen.add(key)
            out.append(pick)
            if len([p for p in out if p.family == family]) >= limit:
                break
    return out
