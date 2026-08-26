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
from cfbmodel import matrix, preseason, ratings, teams

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


def _side(season: int, school: str, home: bool) -> str:
    team = teams.get(season, school)
    logo = (f'<img class="side-logo" src="{esc(team.logo)}" alt="" loading="lazy">'
            if team.logo else '<span class="side-logo"></span>')
    accent = (f'<span class="side-accent" style="background:{esc(team.color)}"></span>'
              if team.color else "")
    conf = f'<div class="side-conf">{esc(team.conference)}</div>' if team.conference else ""
    ident = f'<div class="side-id"><div class="side-name">{esc(school)}</div>{conf}</div>'
    # A true reflection: away reads accent|logo|name, home reads name|logo|accent, so the
    # halves mirror around the centre column rather than merely right-aligning. The earlier
    # row-reverse pushed the accent to the inside edge on the home side, which broke it.
    body = f'{ident}{logo}{accent}' if home else f'{accent}{logo}{ident}'
    return f'<div class="side{" side--home" if home else ""}">{body}</div>'


def _breakdown(row: Row, season: int) -> str:
    f = row.forecast
    if not (row.home_form and row.away_form and row.home_form.complete()
            and row.away_form.complete()):
        return ""
    c = matrix.COEFFICIENTS
    lines = []
    for key in _ORDER:
        hv = getattr(row.home_form, key)
        av = getattr(row.away_form, key)
        contribution = c[key] * (hv - av)
        cls = "bd-c--pos" if contribution > 0 else ("bd-c--neg" if contribution < 0 else "")
        lines.append(
            f'<div class="bd-row"><span class="bd-k">{esc(_FEATURE_LABELS[key])}</span>'
            f'<span class="bd-a">{av:.3f}</span><span class="bd-h">{hv:.3f}</span>'
            f'<span class="bd-c {cls}">{contribution:+.2f}</span></div>')
    efficiency = matrix.margin_points(row.home_form, row.away_form) or 0.0
    lines.append(
        f'<div class="bd-row bd-row--total"><span class="bd-k">Efficiency edge</span>'
        f'<span class="bd-a"></span><span class="bd-h"></span>'
        f'<span class="bd-c">{efficiency:+.2f}</span></div>')
    return f"""<details class="bd"><summary>Breakdown · how the model got there</summary>
<div class="bd-body">
<div class="bd-head-row"><span>Factor</span><span>{esc(teams.get(season, f.away).short)}</span>
<span>{esc(teams.get(season, f.home).short)}</span><span>Pts</span></div>
{"".join(lines)}
<p class="foot-note" style="margin-top:10px">Values are opponent-adjusted. Points are the
home side&rsquo;s margin contribution: positive favours {esc(f.home)}.</p>
</div></details>"""


def _game_card(row: Row, season: int) -> str:
    f = row.forecast
    edge_cls = ""
    if f.edge_points is not None:
        edge_cls = "gn-v--edge-pos" if f.edge_points > 0 else "gn-v--edge-neg"
    badge = {
        "BET": "badge-bet", "MONITOR": "badge-monitor",
        "REVIEW": "badge-review", "AVOID": "badge-avoid",
    }.get(f.action.value, "badge-avoid")
    note = "model only — no market price" if not f.has_price else (
        "published margin = market at lam 0")
    if not f.used_efficiency:
        note = "preseason prior — no observed form yet"
    when = f'<div class="game-when">{esc(row.kickoff)}</div>' if row.kickoff else ""
    neutral = '<div class="game-when">neutral site</div>' if f.neutral else ""
    return f"""<article class="game">
<div class="game-top">
{_side(season, f.away, home=False)}
<div class="game-mid"><span class="game-at">AT</span>{when}{neutral}</div>
{_side(season, f.home, home=True)}
</div>
<div class="game-nums">
<div class="gn"><span class="gn-l">Model</span>
<span class="gn-v{"" if f.model_margin is not None else " gn-v--na"}">{_fmt(f.model_margin)}</span></div>
<div class="gn"><span class="gn-l">Market</span>
<span class="gn-v{"" if f.market_margin is not None else " gn-v--na"}">{_fmt(f.market_margin)}</span></div>
<div class="gn"><span class="gn-l">Edge</span>
<span class="gn-v {edge_cls}">{_fmt(f.edge_points)}</span></div>
</div>
{_breakdown(row, season)}
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
           authority: auth_mod.Authority) -> str:
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

    cards = "".join(_game_card(r, season) for r in rows) or (
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
    forms = cli._forms(season, week)
    market = cli._market(season, week)
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
            authority=authority, week=week,
        )
        kickoff = (g.get("startDate") or "")[:10] or None
        rows.append(Row(forecast, kickoff, forms.get(home), forms.get(away)))

    rows.sort(key=lambda r: abs(r.forecast.edge_points) if r.forecast.edge_points is not None else -1,
              reverse=True)
    html_text = render(season=season, week=week, rows=rows,
                       rating_table=rating_table, authority=authority)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    return out
