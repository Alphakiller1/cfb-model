# cfb-model

College football forecasts with an explicit **authority gate**, built on the shared
logic-matrix core in [`chase-analytics-brain`](https://github.com/Alphakiller1/chase-analytics-brain).

Sibling boards on the same discipline: [MLB](https://github.com/Alphakiller1/mlb-model)
· [WNBA](https://github.com/Alphakiller1/wnba-edge-model)
· [NFL](https://github.com/Alphakiller1/nfl-model).

> Analysis infrastructure, not betting advice.

## The current honest answer

The model does **not** beat the market, and this repo says so in its output rather
than hiding it. Walk-forward on **3,256 out-of-sample FBS games** (2019, 2021–2025):

| Candidate | MAE | vs market |
| --- | ---: | ---: |
| Closing market | **12.1596** | — |
| Ratings + opponent-adjusted efficiency | 12.5251 | +0.3655 |
| Ratings + raw efficiency | 12.7883 | +0.6287 |
| Ratings only | 12.9749 | +0.8154 |

Totals, on the same games:

| Candidate | MAE |
| --- | ---: |
| Market total | **12.5055** |
| Model total | 13.0446 |
| League-mean total | 13.6544 |

Projected scores are derived from the two: `home = (total + margin) / 2`. They
inherit the error of both models, so a scoreline is a centre of mass rather than
a prediction — the total residual SD is 16.36 against an actual SD of 17.14.

ATS where the model disagrees with the spread: **1651–1579–26 = 51.11%**,
95% CI **[49.39%, 52.84%]**, breakeven 52.38%.

The interval **straddles** breakeven — before opponent adjustment it sat entirely
below it. The model is now indistinguishable from breakeven rather than
confidently losing, which is a change in kind. It is still not evidence of an
edge: an interval containing the bar is what "unproven" looks like, so
`authority.current()` returns `RESEARCH_ONLY` and `may_bet` is `False`. Full
evidence: [`reports/BASELINE_2019_2025.md`](reports/BASELINE_2019_2025.md).

### 2026 season-opening audit

The September production audit re-ran the early-season path on **1,548
point-in-time games from 2021–2025, weeks 1–6**. The prior implementation used
the full efficiency matrix as soon as both teams had one game, even though that
matrix was validated only from week 5. A week-level reliability transition now
weights observed form 30% / 50% / 80% in weeks 2 / 3 / 4. Weeks 2–3 then receive
separately validated outcome-scale calibration; week 4 calibration was tested
and rejected because it increased error.

| Season-opening audit | MAE |
| --- | ---: |
| Market | **11.8741** |
| Updated model | 12.5423 |
| Pre-upgrade model | 13.0086 |

The transition and calibration were selected leave-one-season-out; the fixed
mean coefficients improved all five historical seasons and now require forward
shadow confirmation. Underdog-side disagreements are
62.5%, down from 69.2% before that calibration; ATS is 801–727 (52.42%, 95% CI
[49.91%, 54.92%]). The interval still crosses breakeven and the market still has
lower MAE, so authority remains `RESEARCH_ONLY`.

Production now also fails closed: it requests only FBS-involving games, uses the
official CFBD calendar, locks live prices to DraftKings, writes a machine-readable
freshness manifest, retains bounded last-good endpoint snapshots, and refuses a
deployment with no verified sportsbook coverage. Every pre-kickoff quote enters
a shadow ledger and is graded deterministically when the final score arrives.
Full findings: [`reports/PRODUCTION_AUDIT_2026-09-01.md`](reports/PRODUCTION_AUDIT_2026-09-01.md).

## What is actually different about college football

Three things drive the design, and all three are measured, not assumed:

1. **Garbage time is a third of the sample.** 36% of FBS games are decided by 28+
   points, versus roughly 8% in the NFL. Margins are compressed through
   `cap · tanh(margin / cap)` with cap 32, worth ~0.49 points of MAE.
2. **Home field is worth 4.53 points**, about double the NFL's, measured over
   4,325 non-neutral FBS-vs-FBS games.
3. **Margins are far noisier** — SD 24.2 versus the NFL's ~13.5 — so a given
   rating gap implies a much less certain outcome.
4. **Schedules barely overlap**, so raw efficiency is badly confounded. Adjusting
   for opponent is worth 0.30 points of MAE overall and 0.70 in weeks 5–7 — and
   it revived explosiveness as a signal, which raw stats had buried at 0.016.

## Quick start

```bash
pip install -e .
cp .env.example .env          # then add a free key from collegefootballdata.com/key

python -m cfbmodel.cli ratings --season 2025 --top 25
python -m cfbmodel.cli board --season 2026 --week 1          # kickoff order
python -m cfbmodel.cli export --season 2026 --week 1 --out build/wk1.json
python -m cfbmodel.cli calibrate --seasons 2019,2021-2025    # scale diagnostic

# ranked ratings as JSON, for the content engine's power-ratings carousel
python -m cfbmodel.cli ratings --season 2025 --preseason --top 40 --out build/ratings.json
```

`ratings --preseason` carries into the *next* season and is labelled with it: the payload
says `basis: preseason` and `games_rated: 0`, because a fitted prior and an in-season rating
are indistinguishable as numbers and a renderer has to be able to tell them apart. The
payload also carries the FBS mean and spread over **every** rated team, so a downstream
graphic showing the top 40 grades them against the league rather than against each other.

## Why the board shows no edge before week 5

The raw model landed on the market underdog in 86.8% of 282 historical Week 1
games. A leave-one-season-out affine correction reduced MAE from **14.4109 to
12.8499** and the underdog-side rate to **60.5%**; its slope was stable across
folds at 1.5333 ± 0.0466. The displayed Week 1 model margin now applies that
correction and retains the raw prior in the export and breakdown for audit.

The market still scored better at **11.7838 MAE**. The remaining gap is
information the price has before kickoff that the model does not: transfer
portal detail, quarterback availability, coaching changes, and late roster news.

Weeks 2–4 now use observed form, but only in proportion to its measured
reliability. That makes the estimate more predictive without pretending a
one-game sample is mature. Weeks 2–3 also have their own held-out scale
calibration. The difference from the market is still published as `market_gap`,
not as an edge: `edge_points` is `None` outside the validated regime and
`edge_withheld_reason` names the rule.

`calibration.py` measures the slope that MAE cannot see; run `cfbmodel calibrate`
to fit it per regime against actual outcomes. Full evidence and the open work —
venue-specific home field and licensed historical availability data — are in
[`reports/BASELINE_2019_2025.md`](reports/BASELINE_2019_2025.md).

## How it fits together

| Module | Role |
| --- | --- |
| `sources/cfbd.py` | CFBD client. FBS-scoped requests, dual-schema normalization, point-in-time rules, bounded snapshots, and source provenance. |
| `sources/oddsapi.py` | Exact-book DraftKings feed with quota, range, team-match, and timestamp validation. |
| `ratings.py` | Opponent-adjusted, blowout-capped power ratings. |
| `efficiency.py` | Opponent adjustment — solves offense and defense jointly, like the ratings. |
| `matrix.py` | Fitted matrix. Weight groups are the interpretation; `COEFFICIENTS` is the predictor. |
| `totals.py` | Combined-points model and the projected scoreline derived from it. |
| `forecast.py` | Market-anchored game forecast, with a validated-regime flag. |
| `calibration.py` | Is a margin on the right *scale*? The diagnostic MAE cannot give. |
| `roster.py` | Transfer portal, quarterback-specific returning production. |
| `coaching.py` | First-year staff, and the tendency profile a hire brings. |
| `venue.py` | Elevation, travel, and body-clock terms for home field. |
| `fitting.py` | Stdlib OLS and the leave-one-season-out adoption harness. |
| `export.py` | The board as JSON, for downstream renderers. |
| `ledger.py` | Immutable pre-kickoff quote snapshots and deterministic shadow grading. |
| `authority.py` | What a forecast is *allowed* to be used for. |

### Point-in-time is enforced, not hoped for

CFBD's season endpoints accept `endWeek`, and it is a real filter: Ohio State's
2025 offensive PPA reads 0.381 over 297 plays through week 6 and 0.327 over 886
plays for the full season. Every form query here ends at the week *before* the one
being forecast, so a backtest cannot see its own answer.

### The authority gate

A probability and a permission are different things. `forecast.py` produces
numbers; nothing may act on them without an `Authority` saying the evidence
supports it. Promotion requires all ten gates passed in explicitly — there is no
boolean that flips it on.

```python
>>> from cfbmodel.authority import current
>>> current().level.value, current().may_bet
('RESEARCH_ONLY', False)
```

### Where this is *not* validated

Week 1 has no current-season form. Weeks 2–4 are a measured transition regime,
not the mature full-efficiency regime: observed form carries 30%, 50%, and 80%.
The board prints the regime and exposes both component margins. No disagreement
from these weeks is authorized as an edge.

## Tests

```bash
python -m pytest -q     # 195 passed
```
