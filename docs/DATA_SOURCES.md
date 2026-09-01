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

### Injuries and availability

**CFBD has no injuries endpoint.** The public alternatives are unofficial
scrapes of team availability reports and beat coverage. Two problems, and the
second is the disqualifying one:

1. They are inconsistent between programmes — college football has no league
   injury-report mandate the way the NFL does.
2. **There is no historical archive.** A feature that cannot be reconstructed
   point-in-time for 2019–2025 cannot be backtested, so it can never clear the
   walk-forward bar every other number in this repo had to clear. It would be
   adopted on faith.

The realistic paths, in order of cost:

- A paid odds/data vendor with an injury feed **and history** (SportsDataIO,
  Sportradar). This is the only one that can be validated.
- Scrape forward from today and revisit in two seasons, when there is enough
  archive to test against. Cheap, slow, honest.
- Use the market as the injury proxy — raise `forecast.DEFAULT_LAM` above zero
  so the price carries the information the model cannot see. This is not free:
  it makes the model partly a market-follower, and `authority.py` is explicit
  that raising `lam` is a claim about evidence that belongs with a gate record.

## The order worth doing

1. **Maintain the forward shadow record.** Historical improvements do not
   establish 2026 performance; every pre-kickoff quote now enters the ledger.
2. **Venue home field.** Wired, needs the fit.
3. **Buy charting or injury data, or accept the gap and say so.** The one thing
   not worth doing is pretending a proxy closes it.
