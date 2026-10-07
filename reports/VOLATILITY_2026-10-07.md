# Team volatility rankings — October 7, 2026

## The question

For each team and each market (spread, moneyline, over, under): when the
advanced metrics project this team's game, how reliably does the result land
there, and how often does it stray in the direction that loses that bet? Rank 1
conforms most. Every game is scored against the model's own pre-game numbers,
never the market's.

| Market | Per-game miss, from the team's side |
| --- | --- |
| Spread | `|actual margin − model margin|` |
| Moneyline | `|won − p| − 2p(1−p)`, `p = Φ(model margin / 24.2)`: surprise beyond what `p` itself implied |
| Over | `max(model total − actual total, 0)`: the shortfall that loses an over |
| Under | `max(actual total − model total, 0)`: the overshoot that loses an under |

## Predictive philosophy

The target is always a team's **future actual misses**, because that is what
settles a bet. Three ingredients were built, and each ships only if it improves
held-out prediction of that target.

1. **Noise cancellation.** A stats-implied outcome model asks what the margin and
   total should have been, given each game's own process stats: success rate,
   explosiveness, stuff rate, plays and drives, with garbage time excluded.
   PPA is left out because it carries turnover plays at full weight. On 3,723
   games those stats explain **74% of final margins and 56% of totals**. The rest
   (luck SD 10.5 points) is turnovers, sequencing and bounces. A team's
   **noise-cancelled score (NC)** is its miss measured against that stats-implied
   outcome instead of the final score.
2. **Consistency prior.** Instead of shrinking every team toward the league
   average, shrink it toward what its *process consistency* predicts: the
   game-to-game SD of its success and explosive-play rates, on offence and on
   defence. Form *levels* were tested first (opponent-adjusted PPA, success,
   explosiveness, stuff rate, tempo); no feature correlated with future
   volatility beyond |r| = 0.08, and the prior built on them lost held-out skill
   in every market, so it was replaced. Consistency was the stronger idea: a
   team that sometimes stalls and sometimes doesn't is one a model built on
   averages will miss.
3. **Shrinkage.** `expected = base + w·(observed − base)`, with
   `w = n_eff / (n_eff + k)` and `n_eff = this season's games + decay ·
   last season's`.

Four nested variants were scored per market: plain shrinkage, plus noise
cancellation, plus the consistency prior, and both. Each was scored **two
ways**: every season predicted with parameters chosen on the other four
(leave-one-season-out, LOSO), and every season predicted with parameters chosen
on *earlier seasons only* (time-forward). A market counts as a team trait only
if its variant beats "every team is average" overall **and** in a majority of
seasons, **under both schemes**.

Data: a walk-forward replay of the production path (`scripts/fit_volatility.py
assemble`), using only games before each week: 3,723 FBS-vs-FBS games,
2021–2025, 664 team-seasons.

## Results

Held-out skill against "every team is average" (seasons better in brackets):

| Market | plain | + noise cancellation | + consistency prior | both | Ships |
| --- | --- | --- | --- | --- | --- |
| Spread | −0.27% (1/5) · +0.28% (1/4) | +0.86% (4/5) · −0.36% (2/4) | **+0.22% (4/5) · +0.22% (3/4)** | +1.28% (4/5) · −0.42% (2/4) | consistency prior |
| Moneyline | +0.83% (3/5) · +0.98% (2/4) | same as plain | −0.47% · −1.37% | same | **descriptive** |
| Over | **+3.49% (3/5) · +3.61% (3/4)** | +3.27% · +0.37% (2/4) | +4.51% (4/5) · +2.58% (2/4) | +4.08% · −0.27% | plain |
| Under | **+2.81% (4/5) · +2.89% (3/4)** | same as plain | +2.44% · +3.09% | same | plain |

Each cell is LOSO · time-forward.

What this says:

* **Totals volatility is the real trait.** Over and under pass both schemes
  with plain shrinkage (`k = 40` games, last season at full weight). Which
  programs' games run past or fall short of the projected total carries across
  seasons (r ≈ 0.22), consistent with tempo and style.
* **Spread moves from noise to weakly predictive**, through the consistency
  prior alone. A team's own spread-miss record still predicts nothing (`k` is
  infinite), but its process consistency does, a little. The fitted sign is
  counterintuitive: teams whose success rate swings more get slightly *smaller*
  future spread misses (−0.36 points per SD of offensive swing, on a 14.6-point
  average miss). One reading is that large swings mostly reflect mismatched
  opponents, which the model prices well. The effect is +0.2%, the smallest
  that passes, and should be read as weak.
* **Moneyline is descriptive.** It looked like a weak trait under LOSO alone,
  but fit only on earlier seasons it helps in just 2 of 4. The audit on October
  7 flagged exactly this, and the two-scheme bar now enforces it.
* **Noise cancellation does not improve prediction.** Luck-stripped misses
  forecast future actual misses no better than raw ones, and its LOSO gains on
  spread do not survive time-forward. It ships as a published diagnostic (NC)
  and not as a forecast input (`alpha = 0` in every market).
* **The consistency prior's best totals number (over, +4.5% LOSO) fails
  time-forward**, 2 of 4 seasons, so it does not ship there either.

## How production uses it

`volatility.build` runs on every site build. It reads this season's graded games
from the shadow ledger, taking the last snapshot recorded before kickoff. It
joins each game to both sides' process lines from the same CFBD per-game rows
the efficiency adjustment already fetched (memoised, so no extra calls). Last
season comes from the ledger when it holds it, or from the per-team aggregate
shipped in `volatility_fit.py`.

* **Predictive** markets rank on the forecast, and the top and bottom fifths
  are graded steady and volatile.
* **Descriptive** markets rank on this season's observed misses, carry no
  grade, and say plainly that it is what happened, not a forecast.
* Every team carries its **NC** score on every market, alongside `luck` (raw
  minus process) in `volatility.json`.

## Audit, October 7

The production matrix was re-derived with independent code against the live
2026 ledger. Every per-game miss, pool, rank and forecast reproduced exactly.
The ledger dedupe matched a separate implementation, and the replay's team
alignment was verified game by game. The ledger stores `forecast.model_margin`
and `independent_total`, the same quantities the replay records. Time-forward
re-scoring found the only defect: moneyline's single-scheme pass, now fixed.

## Refitting

    python scripts/fit_volatility.py assemble --seasons 2021-2025   # CFBD permanent cache
    python scripts/fit_volatility.py fit

`fit` prints the ablation, writes the evidence with every fold to
`reports/volatility_fit.json`, and writes the shipped parameters to
`src/cfbmodel/volatility_fit.py`. A market that passes on a refit switches to
predictive automatically.

Research context, not a betting signal. Authority remains RESEARCH_ONLY.
