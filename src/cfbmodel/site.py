"""Static dashboard generator.

Emits one self-contained HTML file. CSS is inlined from `static/`; the only
external requests are Google Fonts and the CFBD logo CDN, both of which a GitHub
Pages host can reach.

Design contract: the vendored `chase_tokens.css` is the canonical Chase identity
and is never edited here -- deep navy #08090F, violet #9A6BFF/#7C4DFF, gold
#E8C24A for eyebrow labels only, DM Sans body / Roboto Condensed display /
Oswald wordmark, hairline borders with layered shadows rather than heavy
outlines. `board.css` holds only CFB-specific structure.

The page leads with the authority gate rather than the numbers, for the same
reason nfl-model does: at lam = 0 the published margin *is* the market, and a
dashboard that opened with a big confident number would be lying about that.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cfbmodel import authority as auth_mod
from cfbmodel import forecast as fc
from cfbmodel import matrix, preseason, ratings, teams, totals

_STATIC = Path(__file__).resolve().parent / "static"

_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    "family=DM+Sans:wght@400;500;700&"
    "family=Roboto+Condensed:wght@700;800&"
    "family=Oswald:wght@600;700&display=swap\" rel=\"stylesheet\">"
)

# The tokens file names these; declared here so the page never depends on a
# font stack it did not ask for.
_FONT_VARS = """:root{
--sans:'DM Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
--display:'Roboto Condensed','DM Sans',sans-serif;
--wordmark:'Oswald','Roboto Condensed',sans-serif;
}
.hero-title,.sec-title,.tile-v,.gn-v,.rt-rating,.side-name{font-stretch:125%}
"""

# Human labels for the matrix features, so a breakdown reads as football rather
# than as variable names.
_FEATURE_LABELS = {
    "off_successRate": "Offense · success rate",
    "off_explosiveness": "Offense · explosiveness",
    "off_ppa": "Offense · PPA per play",
    "off_stuffRate": "Offense · stuffed rate",
    "def_successRate": "Defense · success rate allowed",
    "def_explosiveness": "Defense · explosiveness allowed",
    "def_ppa": "Defense · PPA allowed",
    "def_stuffRate": "Defense · stuff rate",
}

_ORDER = ["off_successRate", "off_explosiveness", "off_ppa", "off_stuffRate",
          "def_successRate", "def_explosiveness", "def_ppa", "def_stuffRate"]


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def _css() -> str:
    tokens = (_STATIC / "chase_tokens.css").read_text(encoding="utf-8")
    board = (_STATIC / "board.css").read_text(encoding="utf-8")
    return tokens + "\n" + _FONT_VARS + "\n" + board


def _fmt(value: float | None, places: int = 1, sign: bool = True) -> str:
    if value is None:
        return "—"
    return f"{value:+.{places}f}" if sign else f"{value:.{places}f}"


@dataclass(frozen=True)
class Row:
    forecast: fc.Forecast
    kickoff: str | None
    home_form: matrix.TeamForm | None
    away_form: matrix.TeamForm | None


# ── fragments ────────────────────────────────────────────────────────────────
def _nav(season: int, week: int) -> str:
    return f"""<header class="chase-header"><div class="wrap"><nav class="chase-nav">
<div class="chase-logo"><span class="chase-wordmark">CHASE<em>ANALYTICS</em></span></div>
<div class="nav-links">
<a class="nav-link" href="#board">Board</a>
<a class="nav-link" href="#ratings">Power Ratings</a>
<a class="nav-link" href="#method">Methodology</a>
</div>
<div class="chase-status"><span class="product-tag">CFB MODEL</span>
<span class="pill">{esc(season)} · WK {esc(week)}</span></div>
</nav></div></header>"""


def _authority_block(authority: auth_mod.Authority) -> str:
    met = "".join(
        f'<span class="gate gate-met">✓ {esc(g.replace("_", " "))}</span>'
        for g in auth_mod.SATISFIED_GATES
    )
    unmet = "".join(
        f'<span class="gate gate-unmet">○ {esc(g.replace("_", " "))}</span>'
        for g in authority.unmet_gates
    )
    return f"""<div class="authority">
<div class="authority-head">
<span class="authority-level">{esc(authority.level.value)}</span>
<span class="pill">may_bet = {"true" if authority.may_bet else "false"}</span>
<span class="pill">{len(authority.unmet_gates)} of {len(auth_mod.REQUIRED_GATES)} gates unmet</span>
</div>
<p class="authority-sub">{esc(authority.evidence)}</p>
<div class="authority-gates">{met}{unmet}</div>
</div>"""


def _tiles() -> str:
    data = [
        ("12.5251", "Model MAE", "points per game, out of sample"),
        ("12.1596", "Market MAE", "closing consensus spread"),
        ("51.11%", "ATS on disagreements", "95% CI [49.39, 52.84] · breakeven 52.38"),
        ("3,256", "Games evaluated", "2019, 2021–2025 walk-forward"),
    ]
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><span class="tile-v">{esc(v)}</span>'
        f'<span class="tile-l">{esc(label)}</span><span class="tile-n">{esc(note)}</span></div>'
        for v, label, note in data
    ) + "</div>"


def _side(season: int, school: str, home: bool, score: float | None = None) -> str:
    team = teams.get(season, school)
    logo = (f'<img class="side-logo" src="{esc(team.logo)}" alt="" loading="lazy">'
            if team.logo else '<span class="side-logo"></span>')
    accent = (f'<span class="side-accent" style="background:{esc(team.color)}"></span>'
              if team.color else "")
    conf = f'<div class="side-conf">{esc(team.conference)}</div>' if team.conference else ""
    ident = f'<div class="side-id"><div class="side-name">{esc(school)}</div>{conf}</div>'
    pts = f'<div class="side-score">{score:.0f}</div>' if score is not None else ""
    # A true reflection: away reads accent|logo|name|score, home reads score|name|logo|accent,
    # so the halves mirror around the centre column and the two projected scores sit either
    # side of it like a scoreboard. The earlier row-reverse pushed the accent to the inside
    # edge on the home side, which broke the reflection.
    body = f'{pts}{ident}{logo}{accent}' if home else f'{accent}{logo}{ident}{pts}'
    return f'<div class="side{" side--home" if home else ""}">{body}</div>'


def _bd_row(label: str, away, home, points, *, kind: str = "") -> str:
    """One symmetric breakdown row: same quantity for both teams, then its effect.

    `away`/`home` are the raw inputs so the two columns are directly comparable;
    `points` is what the pair is worth to the home margin. Passing None leaves a
    cell blank rather than printing a zero that looks measured.
    """
    def cell(v):
        if v is None:
            return ""
        return f"{v:,.2f}" if abs(v) >= 100 else f"{v:.3f}" if abs(v) < 1 else f"{v:.2f}"

    if points is None:
        pts, cls = "", ""
    else:
        pts = f"{points:+.2f}"
        cls = "bd-c--pos" if points > 0 else ("bd-c--neg" if points < 0 else "")
    return (f'<div class="bd-row {kind}"><span class="bd-k">{esc(label)}</span>'
            f'<span class="bd-a">{cell(away)}</span><span class="bd-h">{cell(home)}</span>'
            f'<span class="bd-c {cls}">{pts}</span></div>')


def _bd_section(title: str) -> str:
    return f'<div class="bd-sec">{esc(title)}</div>'


def _bd_shell(row: Row, season: int, body: str, footnote: str) -> str:
    f = row.forecast
    return f"""<details class="bd"><summary>Breakdown · how the model got there</summary>
<div class="bd-body">
<div class="bd-head-row"><span>Factor</span><span>{esc(teams.get(season, f.away).short)}</span>
<span>{esc(teams.get(season, f.home).short)}</span><span>Pts</span></div>
{body}
<p class="foot-note" style="margin-top:10px">{footnote}</p>
</div></details>"""


def _projection_rows(row: Row, season: int) -> str:
    """Closing section shared by both regimes: margin, total, and the scoreline."""
    f = row.forecast
    out = [_bd_section("Projection")]
    if f.model_margin is not None:
        out.append(_bd_row("Model margin", None, None, f.model_margin, kind="bd-row--total"))
    if f.projected_total is not None:
        label = "Projected total" + ("" if f.total_modelled else " (league mean)")
        out.append(
            f'<div class="bd-row"><span class="bd-k">{esc(label)}</span>'
            f'<span class="bd-a"></span><span class="bd-h"></span>'
            f'<span class="bd-c">{f.projected_total:.1f}</span></div>')
    if f.market_total is not None:
        out.append(
            f'<div class="bd-row"><span class="bd-k">Market total (consensus)</span>'
            f'<span class="bd-a"></span><span class="bd-h"></span>'
            f'<span class="bd-c">{f.market_total:.1f}</span></div>')
    if f.book_margin is not None or f.book_total is not None:
        label = f"Live book · {f.book_name}" if f.book_name else "Live book"
        spread = f"{f.book_margin:+.1f}" if f.book_margin is not None else "—"
        tot = f"{f.book_total:.1f}" if f.book_total is not None else "—"
        out.append(
            f'<div class="bd-row"><span class="bd-k">{esc(label)}</span>'
            f'<span class="bd-a">{esc(spread)}</span><span class="bd-h">{esc(tot)}</span>'
            f'<span class="bd-c"></span></div>')
    if f.projected_home_score is not None:
        out.append(
            f'<div class="bd-row bd-row--total"><span class="bd-k">Projected score</span>'
            f'<span class="bd-a">{f.projected_away_score:.0f}</span>'
            f'<span class="bd-h">{f.projected_home_score:.0f}</span>'
            f'<span class="bd-c"></span></div>')
    return "".join(out)


def _ratings_breakdown(row: Row, season: int, rating_table: dict[str, float],
                       comps: dict | None) -> str:
    """Breakdown for the ratings-only regime (weeks 1-4, or a team without form).

    There is no efficiency form to decompose, so this decomposes the thing that
    *did* produce the number: the preseason rating itself, term by term, for both
    teams side by side. The earlier version listed "home power rating" and "away
    power rating" as separate one-sided rows, which was neither symmetric nor
    informative about where those ratings came from.
    """
    f = row.forecast
    home_r = rating_table.get(f.home)
    away_r = rating_table.get(f.away)
    if home_r is None or away_r is None:
        return ""

    parts: list[str] = []
    hc = (comps or {}).get(f.home)
    ac = (comps or {}).get(f.away)
    unavailable: list[str] = []
    if hc and ac:
        # The model intercept is identical for both teams and cancels out of the
        # margin, so it is not shown -- a permanent "+0.00" row is noise.
        parts.append(_bd_section("Preseason rating inputs"))
        for (label, a_raw, a_pts), (_, h_raw, h_pts) in zip(ac.rows(), hc.rows()):
            # A term whose input is identically zero for both teams is not a
            # measurement of parity -- it is a feed that has not published yet.
            if a_raw == 0.0 and h_raw == 0.0:
                unavailable.append(label)
                parts.append(_bd_row(f"{label} (not published yet)", None, None, None))
                continue
            parts.append(_bd_row(label, a_raw, h_raw, h_pts - a_pts))

    parts.append(_bd_section("Power rating"))
    parts.append(_bd_row("Rating", away_r, home_r, home_r - away_r, kind="bd-row--total"))

    parts.append(_bd_section("Game context"))
    home_field = 0.0 if f.neutral else ratings.HOME_FIELD_POINTS
    parts.append(_bd_row("Home field" if not f.neutral else "Neutral site",
                         None, None, home_field))
    parts.append(_bd_row("Calibration", None, None, fc.RATING_BIAS_CORRECTION))
    parts.append(_projection_rows(row, season))

    note = ("No opponent-adjusted form exists yet this season, so the rating above is the "
            "preseason projection and the <b>Pts</b> column is each input&rsquo;s effect on "
            "the home margin.")
    if unavailable:
        note += (" " + esc(", ".join(unavailable)) +
                 " has not been published for this season, so it contributes nothing —"
                 " that is a missing feed, not a measured tie.")
    return _bd_shell(row, season, "".join(parts), note)


def _breakdown(row: Row, season: int, rating_table: dict[str, float],
               comps: dict | None = None) -> str:
    """Full-regime breakdown: every opponent-adjusted feature, then the roll-up."""
    f = row.forecast
    if not (row.home_form and row.away_form and row.home_form.complete()
            and row.away_form.complete()):
        return _ratings_breakdown(row, season, rating_table, comps)

    c = matrix.COEFFICIENTS
    parts: list[str] = []
    for group, keys in (("Offense", _ORDER[:4]), ("Defense", _ORDER[4:])):
        parts.append(_bd_section(group))
        for key in keys:
            hv = getattr(row.home_form, key)
            av = getattr(row.away_form, key)
            parts.append(_bd_row(_FEATURE_LABELS[key], av, hv, c[key] * (hv - av)))
    efficiency = matrix.margin_points(row.home_form, row.away_form) or 0.0
    parts.append(_bd_row("Efficiency edge", None, None, efficiency, kind="bd-row--total"))

    parts.append(_bd_section("Power rating & context"))
    home_r = rating_table.get(f.home)
    away_r = rating_table.get(f.away)
    if home_r is not None and away_r is not None:
        gap = home_r - away_r
        home_field = 0.0 if f.neutral else ratings.HOME_FIELD_POINTS
        # Rating and home field are INPUTS to the contribution below, not separate
        # addends -- the coefficient is applied to their sum. Giving them their own
        # Pts values made the column stop reconciling with the model margin.
        parts.append(_bd_row("Rating", away_r, home_r, None))
        parts.append(_bd_row("Home field" if not f.neutral else "Neutral site (no home field)",
                             None, home_field if not f.neutral else None, None))
        # The rating term is down-weighted because opponent-adjusted efficiency
        # now carries much of what the ratings alone used to.
        parts.append(_bd_row(f"Rating + home field, x{c['rating_margin']:.2f}",
                             None, None, c["rating_margin"] * (gap + home_field)))
    parts.append(_bd_row("Intercept", None, None, c["intercept"]))
    parts.append(_projection_rows(row, season))

    note = ("Values are opponent-adjusted, so they are comparable across schedules. "
            "<b>Pts</b> is each factor&rsquo;s effect on the home margin: positive favours "
            f"{esc(f.home)}. Allowed statistics are inverted, so a lower number is a "
            "better defence.")
    return _bd_shell(row, season, "".join(parts), note)


def _game_card(row: Row, season: int, rating_table: dict[str, float],
               comps: dict | None = None) -> str:
    f = row.forecast
    edge_cls = ""
    if f.edge_points is not None:
        edge_cls = "gn-v--edge-pos" if f.edge_points > 0 else "gn-v--edge-neg"
    badge = {
        "BET": "badge-bet", "MONITOR": "badge-monitor",
        "REVIEW": "badge-review", "AVOID": "badge-avoid",
    }.get(f.action.value, "badge-avoid")
    # A league-mean fallback total is marked so a scoreline built on it is not
    # read as a modelled projection.
    total_star = "" if f.total_modelled else "*"
    if f.market_total is not None:
        total_sub = f"mkt {f.market_total:.1f}"
    elif not f.total_modelled:
        total_sub = "league mean"
    else:
        total_sub = ""
    note = "model only — no market price" if not f.has_price else (
        "published margin = market at lam 0")
    if not f.used_efficiency:
        note = "preseason prior — no observed form yet"
    when = f'<div class="game-when">{esc(row.kickoff)}</div>' if row.kickoff else ""
    neutral = '<div class="game-when">neutral site</div>' if f.neutral else ""
    return f"""<article class="game">
<div class="game-top">
{_side(season, f.away, home=False, score=f.projected_away_score)}
<div class="game-mid"><span class="game-at">AT</span>{when}{neutral}</div>
{_side(season, f.home, home=True, score=f.projected_home_score)}
</div>
<div class="game-nums">
<div class="gn"><span class="gn-l">Model</span>
<span class="gn-v{"" if f.model_margin is not None else " gn-v--na"}">{_fmt(f.model_margin)}</span></div>
<div class="gn"><span class="gn-l">Market</span>
<span class="gn-v{"" if f.market_margin is not None else " gn-v--na"}">{_fmt(f.market_margin)}</span></div>
<div class="gn"><span class="gn-l">Total</span>
<span class="gn-v{"" if f.projected_total is not None else " gn-v--na"}">{_fmt(f.projected_total, sign=False)}{total_star}</span>
<span class="gn-sub">{total_sub}</span></div>
<div class="gn"><span class="gn-l">Edge</span>
<span class="gn-v {edge_cls}">{_fmt(f.edge_points)}</span></div>
</div>
{_breakdown(row, season, rating_table, comps)}
<div class="game-foot"><span class="badge {badge}">{esc(f.action.value)}</span>
<span class="foot-note">{esc(note)}</span></div>
</article>"""


def _ratings_table(season: int, table: dict[str, float], limit: int = 40) -> str:
    ranked = sorted(((v, k) for k, v in table.items() if k != ratings.FCS), reverse=True)[:limit]
    if not ranked:
        return '<p class="dim">No ratings available.</p>'
    span = max(abs(v) for v, _ in ranked) or 1.0
    rows = []
    for i, (value, school) in enumerate(ranked, 1):
        team = teams.get(season, school)
        logo = (f'<img class="rt-logo" src="{esc(team.logo)}" alt="" loading="lazy">'
                if team.logo else '<span class="rt-logo"></span>')
        cls = "rt-pos" if value > 0 else "rt-neg"
        width = min(100.0, abs(value) / span * 100.0)
        side = "left:50%" if value > 0 else f"right:50%"
        rows.append(
            f'<tr><td class="rt-rank">{i}</td>'
            f'<td><div class="rt-team">{logo}<span>{esc(school)}</span></div></td>'
            f'<td class="dim">{esc(team.conference or "—")}</td>'
            f'<td class="r"><span class="rt-rating {cls}">{value:+.2f}</span></td>'
            f'<td><div class="rt-bar"><i style="{side};width:{width/2:.1f}%"></i></div></td></tr>')
    return f"""<div class="rt-wrap"><table class="rt">
<thead><tr><th>#</th><th>Team</th><th>Conference</th><th class="r">Rating</th><th>vs average</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>"""


def _methodology() -> str:
    off = "".join(f"<tr><td>{esc(k.replace('_', ' '))}</td><td>{v:.4f}</td></tr>"
                  for k, v in matrix.OFFENSE_WEIGHTS.items())
    dfn = "".join(f"<tr><td>{esc(k.replace('_', ' '))}</td><td>{v:.4f}</td></tr>"
                  for k, v in matrix.DEFENSE_WEIGHTS.items())
    return f"""<div class="mth">
<div class="mth-card"><div class="mth-h">Power ratings</div>
<p>Opponent-adjusted scoring margin, solved iteratively. Home field is removed
before rating, so a soft home schedule earns nothing.</p>
<p>Margins are compressed through <code>cap · tanh(margin / cap)</code> at cap 32.
36% of FBS games are decided by 28+ points — the NFL figure is nearer 8% — so an
unadjusted mean lets garbage time drive the ratings. Capping is worth about half
a point of MAE.</p>
<table class="mth-tbl">
<tr><td>Home field</td><td>{matrix.HOME_FIELD_POINTS:.2f} pts</td></tr>
<tr><td>Blowout cap</td><td>{matrix.BLOWOUT_CAP:.0f}</td></tr>
<tr><td>Recency half-life</td><td>{matrix.RECENCY_HALFLIFE_WEEKS:.0f} wks</td></tr>
<tr><td>Margin SD</td><td>{matrix.MARGIN_SD:.1f} pts</td></tr></table></div>

<div class="mth-card"><div class="mth-h">Offense weights</div>
<p>Standardised importance from a leave-one-season-out fit on opponent-adjusted
features. Success rate dominates.</p>
<table class="mth-tbl">{off}</table></div>

<div class="mth-card"><div class="mth-h">Defense weights</div>
<p>Allowed statistics enter inverted, so giving up less rates better.</p>
<table class="mth-tbl">{dfn}</table></div>

<div class="mth-card"><div class="mth-h">Why opponent adjustment</div>
<p>Raw season efficiency is confounded by schedule, and CFB schedules barely
overlap. Adjusting is worth 0.30 points of MAE overall and 0.70 in weeks 5–7.</p>
<p>It also revived a signal: raw explosiveness fitted at 0.016 and looked
worthless. Opponent-adjusted it fits at 0.153. The feature was never weak — the
measurement was confounded.</p></div>

<div class="mth-card"><div class="mth-h">Totals &amp; projected scores</div>
<p>Margin asks who is better, so it uses feature <em>differences</em>. A total asks how
much scoring the two teams generate together, so it uses <em>sums</em> plus pace —
drives and plays per game. CFB tempo varies far more than the NFL&rsquo;s.</p>
<table class="mth-tbl">
<tr><td>League-mean total</td><td>13.6544</td></tr>
<tr><td>Model total</td><td>13.0446</td></tr>
<tr><td>Market total</td><td>12.5055</td></tr>
<tr><td>Total residual SD</td><td>{totals.TOTAL_SD:.2f}</td></tr></table>
<p>Scores are algebra on the two projections: home = (total + margin) / 2. They inherit
the error of <em>both</em> models, so read a scoreline as a centre of mass, not a
prediction. A total marked <b>*</b> is the league mean, not a modelled figure.</p></div>

<div class="mth-card"><div class="mth-h">Point-in-time</div>
<p>Every feature is queried strictly before the week being forecast. CFBD&rsquo;s
<code>endWeek</code> is a real filter, so a backtest cannot see its own answer.</p></div>

<div class="mth-card"><div class="mth-h">Where this is not validated</div>
<p>Weeks 1–4 run on a fitted preseason prior rather than observed form. Measured
MAE there is 13.79 against a market of 12.00 — a 1.8-point gap, versus 0.37 from
week 5 on. Treat the market as the better estimate early.</p></div>
</div>"""


# ── page ─────────────────────────────────────────────────────────────────────
def render(*, season: int, week: int, rows: list[Row], rating_table: dict[str, float],
           authority: auth_mod.Authority, comps: dict | None = None) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    in_regime = week >= fc.FIRST_VALIDATED_WEEK
    regime_pill = ('<span class="pill pill-ok">validated regime</span>' if in_regime
                   else '<span class="pill pill-warn">outside validated regime</span>')
    early_notice = "" if in_regime else (
        '<div class="notice"><b>Week %d is outside the validated regime.</b> '
        "Forecasts here use a fitted preseason prior rather than observed form, and the "
        "slate is full of mismatches the backtest never covered. Measured weeks 1–4 MAE is "
        "13.79 against a market of 12.00 — a 1.8-point gap, versus 0.37 from week 5 on."
        "</div>" % week)

    cards = "".join(_game_card(r, season, rating_table, comps) for r in rows) or (
        '<p class="dim">No FBS-vs-FBS games found for this week.</p>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CFB Model — Chase Analytics</title>
<meta name="description" content="College football research dashboard: opponent-adjusted power ratings, market-anchored game forecasts, and an explicit authority gate.">
{_FONTS}
<style>{_css()}</style>
</head>
<body>
{_nav(season, week)}
<main>
<div class="wrap">
<section class="hero" style="margin-top:0">
<span class="hero-eyebrow"><span class="hero-eyebrow-dot"></span>CHASE ANALYTICS MODEL LAB</span>
<h1 class="hero-title">College Football Model</h1>
<p class="hero-sub">Opponent-adjusted power ratings and a market-anchored game forecast,
with an explicit gate on what the numbers are allowed to be used for.</p>
<div class="hero-meta">{regime_pill}
<span class="pill">{len(rows)} games</span>
<span class="pill">{len([r for r in rating_table if r != ratings.FCS])} FBS teams rated</span>
<span class="pill">generated {esc(generated)}</span></div>
{_authority_block(authority)}
{_tiles()}
</section>

<section id="board">
<div class="sec-eyebrow">01 · Slate</div>
<h2 class="sec-title">Week {esc(week)} Board</h2>
<p class="sec-blurb">Model margin is the home side. The published margin equals the market
at lam&nbsp;=&nbsp;0, because the model does not beat the closing line — the model column is
shown so the disagreement is visible, not so it can be traded. Expand any game for the
factor-by-factor breakdown.</p>
{early_notice}
<div class="games">{cards}</div>
</section>

<section id="ratings">
<div class="sec-eyebrow">02 · Ratings</div>
<h2 class="sec-title">Power Ratings</h2>
<p class="sec-blurb">Points relative to an average FBS team on a neutral field, so the
projected neutral margin between two teams is the difference of their ratings.</p>
{_ratings_table(season, rating_table)}
</section>

<section id="method">
<div class="sec-eyebrow">03 · Method</div>
<h2 class="sec-title">Methodology</h2>
<p class="sec-blurb">Every constant below was measured on 3,256 out-of-sample games rather
than assumed. Full evidence lives in <code>reports/BASELINE_2019_2025.md</code>.</p>
{_methodology()}
</section>
</div>
</main>
<footer><div class="wrap">
<p><b>Analysis infrastructure, not betting advice.</b> The model does not beat the closing
market and its authority is RESEARCH_ONLY; nothing here is a recommendation to wager.</p>
<p>If gambling stops being fun, call 1-800-GAMBLER.</p>
<p>Chase Analytics model lab · generated {esc(generated)}</p>
</div></footer>
</body>
</html>"""


def build(*, season: int, week: int, out: Path) -> Path:
    """Fetch, forecast, and write the dashboard."""
    from cfbmodel import cli  # local import: cli owns the data assembly

    authority = auth_mod.current()
    rating_table = cli.build_ratings(season, week)
    # Preseason component detail, so an early-season breakdown can show what the
    # rating was actually built from rather than only asserting it.
    try:
        p1 = ratings.build(cli._to_games(cli.cfbd.games(cli._prior_season(season), completed_only=True)))
        p2 = ratings.build(cli._to_games(cli.cfbd.games(cli._prior_season(cli._prior_season(season)),
                                                        completed_only=True)))
        comps = preseason.components(season, p1, p2)
    except Exception:
        comps = None
    forms = cli._forms(season, week)
    market = cli._market(season, week)
    market_total = cli._market_totals(season, week)
    # Live sportsbook lines are optional: no key, no quota, or no match simply
    # means the board renders without a book row.
    try:
        from cfbmodel.sources import oddsapi
        book_lines = oddsapi.fetch_lines(teams.load(season))
    except Exception:
        book_lines = {}
    slate = [g for g in cli.cfbd.games(season, week=week)
             if g.get("homeClassification") == "fbs" and g.get("awayClassification") == "fbs"]

    rows: list[Row] = []
    for g in slate:
        home, away = g["homeTeam"], g["awayTeam"]
        forecast = fc.game(
            home=home, away=away, team_ratings=rating_table,
            neutral=bool(g.get("neutralSite")),
            home_form=forms.get(home), away_form=forms.get(away),
            market_margin=market.get((home, away)),
            market_total=market_total.get((home, away)),
            book=book_lines.get((home, away)),
            authority=authority, week=week,
        )
        kickoff = (g.get("startDate") or "")[:10] or None
        rows.append(Row(forecast, kickoff, forms.get(home), forms.get(away)))

    rows.sort(key=lambda r: abs(r.forecast.edge_points) if r.forecast.edge_points is not None else -1,
              reverse=True)
    html_text = render(season=season, week=week, rows=rows,
                       rating_table=rating_table, authority=authority, comps=comps)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    return out
