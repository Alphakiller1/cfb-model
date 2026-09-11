# Predictive forecast overhaul — 2026-09-11

## Decision

The public scoreline is now the best supported predictive estimate, not the
independent research estimate. It uses the freshest timestamp-verified
DraftKings margin. Its total is market anchored with only the small independent
share that survived season-held-out testing. The independent margin and total
remain visible for diagnosis and continue to be shadow graded.

Authority remains `RESEARCH_ONLY`. Improving a point forecast is not evidence
of a profitable betting edge.

## Production defect found

`game_advanced_stats` treated any week before a calendar-derived live-week guess
as immutable. The 2026 Week 1 advanced-stat response was first cached on
September 1, before most Week 1 games. It contained 142 rows; the completed feed
contained 406. The September 11 Week 2 production board consequently had:

- 49 games;
- 1 matchup using observed efficiency;
- 48 matchups silently using the preseason-only fallback.

Current-season past-week advanced stats are now volatile runtime snapshots.
They refresh on every build and become immutable only when the season closes.
The controlled branch build restored observed-form coverage to 49 of 49 Week 2
matchups and reported a one-second-old 406-row Week 1 response.

This fixes the advertised feature path. It does not make the independent model
better than the market: several one-game efficiency readings moved in the wrong
direction, which is exactly why early form remains reliability weighted and the
predictive headline stays market anchored.

## Validation design

`scripts/audit_predictive_overhaul.py` assembled 3,702 completed FBS-vs-FBS
games from 2021–2025. For every held-out season it selected a bounded blend
weight on the other four seasons, then scored that untouched season:

`forecast = market + lambda * (independent - market)`

This was run independently for margins and totals, overall, for weeks 1–4, for
weeks 5+, and for each of weeks 1–4. A three-parameter OLS ensemble was also
scored leave-one-season-out as a diagnostic. It was not adopted.

## Results

| Regime | Target | Market MAE | Independent MAE | Nested blend MAE | Mean selected lambda | Decision |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| All weeks | margin | 12.1040 | 12.4961 | 12.1037 | 0.135 | Reject: 0.0003 is immaterial |
| All weeks | total | 12.4639 | 13.0159 | **12.4338** | 0.185 | Small residual signal |
| Weeks 1–4 | margin | **12.0770** | 12.8152 | 12.1306 | 0.075 | Reject |
| Weeks 1–4 | total | 12.5452 | 13.4582 | **12.4861** | 0.170 | Regime-specific only |
| Weeks 5+ | margin | 12.1143 | 12.3755 | 12.1086 | 0.185 | Reject: too small/unstable |
| Weeks 5+ | total | 12.4332 | 12.8488 | **12.4161** | 0.190 | Keep 0.175 |
| Week 1 | margin | **12.0803** | 13.1435 | 12.0888 | 0.010 | Market only |
| Week 1 | total | **12.4568** | 13.0903 | 12.6719 | 0.145 | Market only |
| Week 2 | margin | **11.4940** | 12.6197 | 11.6108 | 0.090 | Market only |
| Week 2 | total | 12.5980 | 14.2660 | **12.5481** | 0.135 | Keep 0.125 |
| Week 3 | margin | **12.9670** | 13.5539 | 13.0029 | 0.045 | Market only |
| Week 3 | total | 11.9454 | 12.5715 | **11.8712** | 0.270 | Keep 0.25 |
| Week 4 | margin | **11.7646** | 12.0445 | 11.9021 | 0.335 | Market only |
| Week 4 | total | **13.1114** | 13.8830 | 13.1290 | 0.200 | Market only |

The margin result is not a failure to find a sufficiently clever transformation.
The market has more information and remains the better estimator. Forcing the
independent model into the headline would make the projections look more
original and less predictive.

The total gains are small and should be treated as challenger evidence, not a
permanent truth. Their weights are deliberately below the mean training-selected
weights. They remain subject to the forward ledger.

## Output and tracking changes

- A fresh verified book quote outranks the slower CFBD consensus as the current
  forecast anchor; consensus remains the historical benchmark and fallback.
- The headline spread, total, and displayed score now reconcile exactly.
- The board labels the independent estimate and forecast separately.
- Export schema 3 records forecast source, independent total, predictive total,
  and the retained model weight.
- Ledger schema 2 records and grades the independent and predictive forecasts
  separately, preserving the ability to falsify this change prospectively.

## Rejected shortcuts

- Increasing early-season model weight: worsened held-out Week 2 margin error.
- Shipping the OLS ensemble: gains were smaller or negative and coefficients
  are harder to audit.
- Adding unvalidated injury, social, or subjective ranking inputs: no historical
  point-in-time archive exists in this repository, so they cannot clear the
  leakage standard.
- Re-fitting dozens of features on the current 2026 results: 43 graded games is
  far too small and would optimize hindsight.
