# Data sources — what is reachable, and what is not

The model's standing problem is that it disagrees with the market by more than
the market's own typical error, most severely before week 5. That gap is
information, and this document is the honest inventory of which of it can be
closed from the feeds this repo can actually reach.

Run `python -m cfbmodel.cli check-sources` to verify every field name below
against the live API. The client is written to the schema documented here; if
CFBD renames a field the checker says which one, in one place, rather than the
feature silently degrading to a league-average constant.

## Wired, and fitted

| Feature | Endpoint | Field |
| --- | --- | --- |
| Prior-season rating | `/games` | `homePoints`, `awayPoints` |
| Recruiting talent | `/talent` | `talent` |
| Returning production (blended) | `/player/returning` | `percentPPA` |
| Recruiting class | `/recruiting/teams` | `points` |
| Quarterback returning production | `/player/returning` | `percentPassingPPA` |
| Transfer portal net and churn | `/player/portal` | `origin`, `destination`, `rating`, `stars` |
| First-year coach and stable philosophy shifts | `/coaches` + `/stats/season/advanced` | `seasons[]`, tempo, pass rate, havoc |
| Opponent-adjusted efficiency | `/stats/game/advanced` | `offense`, `defense` |
| Market | `/lines` | `spread`, `overUnder` |
| Live single-book quote | The Odds API `/v4/sports/americanfootball_ncaaf/odds` | DraftKings spread, total, update time |

Closed-season advanced-stat responses used for coaching-style priors are
immutable. Production carries the exact 2023–2025 CFBD responses for those
three queries so a transient provider timeout cannot erase a fitted preseason
feature; current-season schedules, rosters, and prices are never bundled this
way and must pass the live freshness gate.

Runtime freshness is age-based, not request-result-based. A failed retry may use
a last-good snapshot without degrading the board only while it is under its
source SLA: 30 minutes for CFBD consensus lines, six hours for games and game
statistics, and 24 hours for roster/coaching priors. Beyond those limits the
manifest marks the input stale and production verification refuses deployment.

## Wired research candidates

Roster and coaching candidates were fitted on 2026-08-27; the stable terms are
now in `preseason.EXTRA_COEFFICIENTS`. The table preserves the design rationale.
Venue effects remain candidates and do not alter a forecast.

| Feature | Endpoint | Field | Why it matters |
| --- | --- | --- | --- |
| Venue elevation / travel / body clock | `/venues`, `/games` | `elevation` (metres, string), `latitude`, `longitude`, `venueId` | `HOME_FIELD_POINTS` is one 4.53-point constant for all 136 programmes. A sea-level dome and a 7,220-foot stadium two time zones from the visitor are not the same number. |

## Not reachable from CFBD

These were asked for and cannot be delivered from this data source. Saying so is
cheaper than shipping a scraper that looks like coverage and is not.

### Coverage shells and offensive formation

**Not published by CFBD at any tier.** `/plays` carries down, distance, play
type, and yardage — not personnel groupings, not pre-snap formation, not
coverage. Snap-level charting comes from commercial providers:

| Provider | Has | Licence |
| --- | --- | --- |
| PFF (College Premium / Ultimate) | coverage, personnel, alignment, per-player grades | paid, per-seat; redistribution prohibited |
| Sports Info Solutions | formation, coverage, blitz, route charting | paid, enterprise |
| Telemetry / SkillCorner | tracking, player positioning | paid, enterprise |

Any of these would need a licence and an ingestion path before a line of feature
code is worth writing. Nothing in this repo can substitute for them, and a
proxy built from play-by-play (guessing pass/run tendency by down and distance)
would be a different, weaker feature wearing the name.

### Injuries and availability — now wired (2026-10-02), not yet fitted

**CFBD still has no injuries endpoint, and ESPN's college injuries feed is dead**
(three rows, all 2020–22). But the premise this section used to rest on — "college
football has no injury-report mandate" — stopped being true. The SEC (2024), ACC,
Big 12 (2025-26), Big Ten (four reports a week from 2026), Pac-12 and MAC now
require a public availability report for every **conference** game: initial
report three days out, daily updates, a game-day report before kickoff.

`sources/availability.py` reads them from the two publishers the conferences
embed — HD Intelligence (`POST /api/get-publish-public`: SEC, ACC, B10, B12, MAC)
and the Pac-12's `report.json` — and what the board does with them:

| Use | Rule |
| --- | --- |
| Player projections | Out / Doubtful players are removed; their carries and targets flow to available teammates (capped at 1.5x). A listed-out starting QB hands the role to his backup. Questionable stays in, flagged on the row. |
| Edge | If either team's **usual starting QB** (most recency-weighted attempts) is Out / Doubtful / Out 1st half, `edge_points` is withheld with the reason. The margin is **not** adjusted. |
| Board | Each card shows both teams' latest report, the starting QB's designation, and who is listed. A team with no report says so — unknown, not healthy. |
| Ledger | Every game snapshot records both teams' report at quote time. |

Why the margin is not adjusted: the NFL board has a fitted `QB_OUT_POINTS`
(4.19, time-forward). The college equivalent cannot be fitted yet — reports only
exist from 2024 (SEC) and 2025-26 (the rest), and only for conference games.
Withholding the edge is the honest step until there is an archive. HD
Intelligence's `get-archive-public` endpoint returns each conference's
season-to-date report history (every stage per player), and the ledger now
archives forward, so the fit is a 2027 job rather than a never job.

Still not covered: non-conference games, and the AAC, Mountain West, Sun Belt,
C-USA and independents, which publish no report.

## The order worth doing

1. **Maintain the forward shadow record.** Historical improvements do not
   establish 2026 performance; every pre-kickoff quote now enters the ledger.
2. **Venue home field.** Wired, needs the fit.
3. **Fit a college QB-availability term** once the report archive covers enough
   games (HD Intelligence archive + the ledger's forward record).
4. **Buy charting data, or accept the gap and say so.** The one thing not worth
   doing is pretending a proxy closes it.
