# Team volatility rankings — October 7, 2026

## The question

For each team and each market (spread, moneyline, over, under): when the
advanced metrics project this team's game, how reliably does the result land
there, and how often does it stray in the direction that loses that bet? Rank 1
conforms most. The question is about conformity to **our** metrics, so every
game is scored against the model's own pre-game numbers, never the market's.

| Market | Per-game score, from the team's side |
| --- | --- |
| Spread | `|actual margin − model margin|` |
| Moneyline | `|won − p| − 2p(1−p)`, `p = Φ(model margin / 24.2)` — surprise beyond what `p` itself implied |
| Over | `max(model total − actual total, 0)` — the shortfall that loses an over |
| Under | `max(actual total − model total, 0)` — the overshoot that loses an under |

The moneyline term subtracts its own expectation, so a team that only plays
coin flips is not called volatile for losing half of them. Over and under are
scored separately because they fail on opposite tails.

## Is it a team trait?

A season is ten to twelve FBS games, and most of the spread in raw team misses
is luck. So the first question is whether a team's volatility **predicts its
future volatility** at all. Data: a walk-forward replay of the production path
(`scripts/fit_volatility.py assemble`), using only games before each week:
ratings, opponent-adjusted form, venue home field, the tier term and the scoring
prior. That gives **3,723 FBS-vs-FBS games, 2021–2025, 664 team-seasons**.

Each team's predicted volatility is its observed mean shrunk toward the field:

    expected = pool + w · (observed − pool),   w = n_eff / (n_eff + k)
    n_eff    = games this season + decay · games last season

`k` and `decay` are chosen per market to predict the **second half** of a team's
season from its first half plus last season. Each season is scored with
parameters chosen on the other four. Skill is the held-out reduction in squared
error against "every team is average". A market counts as a trait only if that
skill is positive overall **and** in a majority of seasons.

| Market | Held-out skill | Seasons better | First → second half r | Season → next r | k | Last-season weight | Verdict |
| --- | ---: | :---: | ---: | ---: | ---: | ---: | --- |
| Spread | −0.42% | 0 / 5 | −0.04 | 0.02 | — | — | **noise** |
| Moneyline | +0.64% | 3 / 5 | 0.05 | 0.07 | 100 | 0.75 | weak trait |
| Over | +3.49% | 3 / 5 | 0.06 | 0.21 | 40 | 1.0 | **trait** |
| Under | +2.97% | 4 / 5 | 0.13 | 0.22 | 40 | 1.0 | **trait** |

**Spread volatility is not a team property.** How far a team's margins stray
from the projection carried nothing forward, half-to-half or season-to-season.
No parameter beat calling every team average in any held-out season.

**Totals volatility is.** Which teams' games run past, or fall short of, the
projected total persists, and persists *across* seasons (r ≈ 0.22) more than
within one. That fits program style and tempo, which outlast a roster. Last
season's games earn full weight. Even so, `k = 40` means a team needs 40 games
of evidence to be half-trusted: after a full prior season plus five current
games, the ranking moves a team about 27% of the way from the field toward its
own record.

**Moneyline is marginal.** It passes the bar, but at +0.6% with 3 of 5 seasons
better, it is the weakest signal shipped and should be read that way.

For scale, a positive skill of a few percent is close to the ceiling. The
second-half target is the mean of five or six noisy games, so even perfect
knowledge of each team's true volatility could explain only a small share of
it.

## How production uses it

`volatility.build` runs on every site build. It reads this season's graded games
from the shadow ledger (the last snapshot recorded before kickoff, so the
model's actual pre-game view) plus last season. Last season comes from the
ledger when it has that season, or from the per-team aggregate shipped in
`volatility_fit.py`. The pool includes FCS opponents' games; only FBS teams are
displayed.

* **Predictive** markets (trait) rank on the shrunk forecast. The steadiest and
  most volatile fifths are graded.
* **Descriptive** markets (noise: here, spread) cannot be shrunk without tying
  every team at the pool. They rank on this season's observed misses, carry no
  grade, and the page says plainly that it is what happened, not a forecast.

The page section is "Team Volatility Rankings". The same payload is published
as `volatility.json` beside `board.json`.

## Refitting

    python scripts/fit_volatility.py assemble --seasons 2021-2025   # CFBD permanent cache
    python scripts/fit_volatility.py fit

`fit` writes the evidence (with per-season folds) to `reports/volatility_fit.json`
and the shipped parameters to `src/cfbmodel/volatility_fit.py`. After the 2026
season closes, the ledger itself becomes the prior season, so a refit is needed
only to re-measure the trait verdicts.

Research context, not a betting signal. Authority remains RESEARCH_ONLY.
