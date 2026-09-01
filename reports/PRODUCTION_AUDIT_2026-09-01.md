# CFB production-readiness audit — 2026-09-01

## Outcome

The model is materially more accurate in the season-opening transition and the
production system can now prove which data and sportsbook quote produced every
board. It is still **RESEARCH_ONLY**. The upgrade does not promote any betting
authority because the market remains more accurate and the ATS confidence
interval still crosses breakeven.

The audit covered the projection math, matrix, source clients, point-in-time
rules, caching, scheduled deployment, sportsbook matching, exports, grading,
dashboard hierarchy, mobile layout source, and live GitHub Pages behavior.

## Critical findings and disposition

| Severity | Finding | Evidence | Disposition |
| --- | --- | --- | --- |
| P0 | A failed live build silently deployed an old Week 1 page as if healthy. | Scheduled run 33543353554 spent roughly ten minutes retrying a CFBD 502, then deployed committed HTML. | Removed whole-page fallback. Production fails closed and verifies `build.json` before deployment. |
| P0 | `/games` requested every NCAA division. | 2025 returned 3,745 rows; only 888 involved an FBS team. Lower-division-only games then entered the shared `__FCS__` solve. | Added `classification=fbs`, client-side validation, and a second filter in `_to_games`. Payload and timeout exposure drop sharply. |
| P1 | Current-season inputs could become permanent. | Portal, coaching, recruiting, team metadata, and some analytic feeds used the immutable cache path. | Only closed seasons and static venues remain immutable. Current-season feeds use in-process memoization plus timestamped runtime snapshots. |
| P1 | One upstream request could be repeated many times in one build. | Current games and lines were assembled independently by ratings, slate, conference, spread, and total paths. | Added process-local request memoization; spread and total now share one response. |
| P1 | Sportsbook identity was not enforced end to end. | `fetch_lines(books=...)` did not use its argument and `_pick_book` could fall through to another book. Errors were swallowed by the site. | Exact single-book lock, DraftKings default, typed source status, numerical range validation, kickoff cross-check, visible missing-line state, and deployment coverage gate. |
| P1 | Weeks 2–4 treated first observations as mature efficiency. | Code used the full matrix whenever both teams had complete fields, despite documentation saying the full regime starts at week 5. | Added measured form reliability of 30% / 50% / 80% in weeks 2 / 3 / 4. |
| P1 | The stabilized weeks 2–3 estimate remained under-dispersed. | LOSO outcome-on-model slopes were 1.4159 and 1.4155; both were stable across folds. | Added week-specific affine calibration. Week 4 was explicitly rejected because calibration increased MAE. |
| P1 | Applied roster and coaching terms were absent from the game breakdown. | `site.build` recomputed components without `roster_features`; `Components.rows()` exposed only the older five terms. | Ratings and breakdown now share one `RatingBundle`; every applied extra term carries raw input and point contribution. |
| P2 | No durable CFB prediction record existed. | The repository had research backtests but no timestamped current-season quote ledger or grader. | Added a pre-kickoff shadow ledger, deterministic final-score grading, current-season summary, and Actions cache persistence. |
| P2 | Freshness and the configured sportsbook were visually subordinate. | The public page showed a generated timestamp but no source age/status; the live book was buried in an expanded breakdown. | Added an operations strip and first-class DraftKings quote row on every game card, plus explicit unavailable states. |

## Measured model impact

Point-in-time audit sample: 1,548 FBS-vs-FBS games, weeks 1–6, seasons
2021–2025. This sample is intentionally reported separately from the original
3,256-game matrix baseline; it tests the changed season-opening path.

| Metric | Before transition upgrade | After upgrade | Market |
| --- | ---: | ---: | ---: |
| Margin MAE | 13.0086 | **12.5423** | **11.8741** |
| Underdog-side disagreements | 64.5% in the pre-audit implementation; 69.2% after reliability blending alone | **62.5%** after outcome-scale calibration | n/a |
| ATS on disagreements | 797–731 (52.16%) | **801–727 (52.42%)** | n/a |
| ATS 95% Wilson interval | — | **[49.91%, 54.92%]** | breakeven 52.38% |

Week-specific upgraded results:

| Week | Model MAE | Market MAE | Underdog-side share |
| ---: | ---: | ---: | ---: |
| 1 | 13.1435 | 12.0803 | 58.6% |
| 2 | 12.6197 | 11.4940 | 67.2% |
| 3 | 13.5539 | 12.9670 | 57.6% |
| 4 | 12.0445 | 11.7646 | 67.0% |
| 5 | 11.9804 | 11.4883 | 62.1% |
| 6 | 12.0683 | 11.4873 | 61.7% |

The fixed transition coefficients improved historical MAE in each of the five
seasons, but those coefficients are means of leave-one-season-out fits. They are
not independent 2026 evidence. The new shadow ledger is the forward test.

## Matrix changes

The eight mature-regime coefficients were not changed. There was no evidence in
this audit that re-estimating them on a narrower sample would improve future
performance. The matrix instead gained a regime layer:

1. Week 1: preseason estimate plus the existing held-out Week 1 calibration.
2. Week 2: 30% full-efficiency / 70% preseason, followed by the Week 2 affine
   calibration `1.3495 + 1.4159 × estimate`.
3. Week 3: 50% / 50%, followed by `-0.6477 + 1.4155 × estimate`.
4. Week 4: 80% / 20%, no affine correction (the candidate worsened MAE).
5. Week 5 onward: 100% mature full-efficiency matrix.

This is deterministic, matchup-level reasoning: observed form gains influence
as its sample matures, and every transition component is printed in the game
breakdown. It does not force a balanced favorite/underdog count; it corrects only
transformations that reduced held-out outcome error.

## Data and deployment controls

- CFBD requests use the official FBS classification filter and accept both the
  legacy flat game schema and the newer nested schema.
- Current endpoints are fetched once per process. Successful responses become
  timestamped last-good snapshots; stale fallback is bounded by source type and
  is explicit in the manifest.
- The production workflow restores/saves source snapshots and the shadow ledger,
  runs twice daily Tuesday–Saturday, and uses the official CFBD calendar rather
  than a hard-coded August week boundary.
- DraftKings is the only accepted production sportsbook. A line from another
  book cannot be selected and mislabeled.
- `build.json`, `board.json`, and `record.json` sit beside the page. The deploy
  verifier requires a non-empty slate, DraftKings identity, minimum coverage,
  and fresh CFBD inputs.

## Design layer

The page now leads with operational truth rather than a hero claim:

- source freshness and degraded-state issues;
- DraftKings match coverage and remaining quota;
- active model lineage/regime;
- forward shadow-record status.

Each matchup promotes the verified DraftKings spread, total, and update time to
a dedicated quote row. Missing lines are explicit. The secondary analysis strip
keeps independent model margin, CFBD consensus, projected total, and the
withheld gap visually separate. Expanded breakdowns now reconcile to the actual
preseason/transition calculation, including portal, quarterback, and coaching
terms.

## Verification

- 199 automated tests pass.
- Python compilation passes.
- Matrix/core sync passes.
- The production workflow now contains a post-build manifest verifier in
  addition to the HTML smoke test.
- Production run `33565986413` passed and deployed commit `ed14925`: 51 Week 1
  games, 41 exact-identity DraftKings quotes, 14 CFBD endpoints, zero stale or
  failed inputs, and a fresh Odds API snapshot with 496 credits remaining.
- The public `build.json`, `board.json`, and rendered HTML were fetched after
  deployment. They agree on season/week/game count, contain only DraftKings
  sportsbook rows, and expose all 10 legitimately missing book quotes.

Rendered browser QA was requested but could not be completed during the audit
because the Codex browser extension was not connected. No alternate browser
surface was silently substituted. The source-level responsive pass is complete;
the external-browser interaction pass remains a release check once the extension
is connected.

## Remaining risks

1. The market remains better by 0.6682 MAE on the season-opening audit. No
   betting promotion is justified.
2. Injury/availability history is still unavailable from CFBD. Without a
   point-in-time archive, it cannot clear the same backtest standard.
3. Venue-specific home-field candidates exist but have not earned coefficients.
4. DraftKings can legitimately leave games unposted. The UI distinguishes that
   from a failed feed; the deploy threshold is configurable and defaults to 50%.
5. The Actions dependencies currently emit a Node 20 deprecation warning while
   GitHub forces them onto Node 24. It does not affect the build, but should be
   cleared when newer major action versions are available.

## Primary external references checked

- [CFBD current API getting-started guide](https://api.collegefootballdata.com/getting-started)
- [CFBD current games and calendar reference](https://apinext.collegefootballdata.com/api/games)
- [The Odds API v4 official guide](https://the-odds-api.com/liveapi/guides/v4/)
