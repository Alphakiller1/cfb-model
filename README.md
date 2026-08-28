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

The model used to land on the market underdog in 43 of 49 week-1 games, and in
**every** game priced above a touchdown. That was not an opinion — it was scale.
Measured dispersion against the market is **0.96** in the validated regime and
**0.53** in week 1, and the gap between those two numbers is information the
price has before kickoff that the model does not: transfer portal, quarterback
specifically rather than blended returning production, coaching changes,
availability. A conditional mean shrinks when it conditions on less, and that is
correct behaviour.

So the difference is still published, as `market_gap`, and it is no longer called
an edge: `edge_points` is `None` outside the validated regime and
`edge_withheld_reason` names the rule. Inflating the estimate to match a
better-informed one was the alternative, and it was rejected twice over —
expansion is monotonically worse on MAE (12.9749 → 13.6962, measured), and it
would manufacture confidence the model has not earned.

`calibration.py` measures the slope that MAE cannot see; run `cfbmodel calibrate`
to fit it per regime against actual outcomes. `calibration.PRESEASON` ships as
identity on purpose. Full evidence and the open work — a tier/conference term,
FCS stratification, and the unwired transfer portal — are in
[`reports/BASELINE_2019_2025.md`](reports/BASELINE_2019_2025.md).

## How it fits together

| Module | Role |
| --- | --- |
| `sources/cfbd.py` | CFBD client. Enforces point-in-time queries and caches only closed weeks. |
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

Weeks 1–4 have no current-season form and lean on discounted carryover ratings,
and week-1 slates are full of power-vs-Group-of-5 mismatches the backtest never
covered. On the 2026 week 1 slate the model read Indiana −14.1 against a market of
−40.8, and the market was right. The board prints a loud warning for those weeks.

## Tests

```bash
python -m pytest -q     # 74 passed
```
