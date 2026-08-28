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
| Opponent-adjusted efficiency | `/stats/game/advanced` | `offense`, `defense` |
| Market | `/lines` | `spread`, `overUnder` |

## Wired, awaiting a fit

Added 2026-08-27. Each is a *candidate*: `cli fit-preseason` scores it
leave-one-season-out and only a feed that lowers held-out error earns a
coefficient in `preseason.COEFFICIENTS`. Until then they change nothing.

| Feature | Endpoint | Field | Why it matters |
| --- | --- | --- | --- |
| Quarterback returning production | `/player/returning` | `percentPassingPPA` | Two teams can return the same *blended* production with opposite quarterback situations. The blend cannot tell them apart; this is on a call the model already makes, so it is free. |
| Transfer portal net | `/player/portal` | `origin`, `destination`, `rating`, `stars` | Declared in `matrix.TALENT_WEIGHTS` at 0.25 since the scaffold and never fetched. Summed by player quality, not headcount. |
| Portal churn | `/player/portal` | as above | Volume of turnover, separate from its net quality. |
| First-year head coach | `/coaches` | `seasons[].school`, `seasons[].year` | A new staff invalidates part of what last season's margins measured. Contiguous tenure, so a returning coach is correctly a first year. |
| Coaching philosophy shift | `/coaches` + `/stats/season/advanced` | `tempo`, `pass_rate`, `explosiveness`, `havoc` | The predictive content is *which* coach arrived. Carries the hire's tendency profile from his previous school and measures how far the scheme is expected to move. |
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

1. **Run `cli fit-preseason`.** Quarterback returning production and the portal
   are wired and cost nothing extra to fetch. If they lower held-out error,
   adopt them; if not, that is a result.
2. **Tier and conference terms, and FCS stratification.** No new feed needed.
   After rescaling, residuals still split P4-vs-G5 −3.29 against G5-vs-G5 +3.55
   — a ~6.8-point gap driven by all FCS opponents sharing one pooled rating.
   This improves MAE *and* calibration rather than trading one for the other.
3. **Venue home field.** Wired, needs the fit.
4. **Buy charting or injury data, or accept the gap and say so.** The one thing
   not worth doing is pretending a proxy closes it.
