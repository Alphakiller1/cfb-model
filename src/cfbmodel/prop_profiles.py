"""Opportunity conservation and descriptive, pre-week CFB player context."""
from __future__ import annotations

import statistics
from collections import defaultdict


def allocate(shares, info, eligible, positions, statuses):
    """Redistribute each half's missing work within position before cross-role fallback.

    Historical shares are conditional on appearances. Normalize those weights
    before distributing a fixed team opportunity pool; never multiply yards per
    carry/reception as a substitute for more opportunities.
    """
    unavailable = {"out", "doubtful"}
    members = set(eligible) | {aid for aid in info if info[aid]["games"] > 0
                               and statuses.get(aid) in unavailable | {"out_first_half"}}
    members |= {aid for aid in info if info[aid]["games"] > 0 and statuses.get(aid) == "probable"}
    base = {aid: dict(shares[aid]) for aid in members}
    for field in ("rush_car", "rec"):
        total = sum(max(0, base[aid].get(field, 0)) for aid in members)
        for aid in members:
            base[aid][field] = max(0, base[aid].get(field, 0)) / max(1.0, total)
    active = members - {aid for aid in members if statuses.get(aid) in unavailable}
    contributors = defaultdict(set)
    phases = []
    pass_exposure = defaultdict(float)
    reserves = {field: max(0, 1 - sum(base[aid][field] for aid in members))
                for field in ("rush_car", "rec")}
    for half in (1, 2):
        available = active - ({aid for aid in active if statuses.get(aid) == "out_first_half"} if half == 1 else set())
        phase = {aid: {field: base[aid][field] if aid in available else 0.0
                       for field in ("rush_car", "rec")} for aid in members}
        for field in ("rush_car", "rec"):
            def group(aid):
                position = positions.get(aid, "WR")
                return "receiver" if field == "rec" and position in ("WR", "TE") else position

            losses = defaultdict(list)
            for aid in members - available:
                if base[aid][field] > 0:
                    losses[group(aid)].append(aid)
            for role, missing in losses.items():
                lost = sum(base[aid][field] for aid in missing)
                recipients = [aid for aid in available if group(aid) == role and base[aid][field] > 0]
                if not recipients:
                    recipients = [aid for aid in available if base[aid][field] > 0
                                  and (field == "rec" or role != "RB" and positions.get(aid) == "RB")]
                total = sum(base[aid][field] for aid in recipients)
                if not total:
                    reserves[field] += lost / 2
                    continue
                for aid in recipients:
                    phase[aid][field] += lost * base[aid][field] / total
                    contributors[aid].update(missing)
        qbs = [aid for aid in available if positions.get(aid) == "QB" and shares[aid].get("pass_att", 0) > 0]
        if qbs:
            starter = min(qbs, key=lambda aid: (-shares[aid]["pass_att"], aid))
            pass_exposure[starter] += .5
        phases.append(phase)
    final = {}
    for aid in active:
        values = dict(shares[aid])
        for opportunity, outputs in (("rush_car", ("rush_yds", "rush_td")), ("rec", ("rec_yds", "rec_td"))):
            volume = sum(phase[aid][opportunity] for phase in phases) / 2
            old = shares[aid].get(opportunity, 0)
            values[opportunity] = volume
            for output in outputs:
                values[output] = shares[aid].get(output, 0) * volume / old if old else 0
        final[aid] = values
    return final, base, active, dict(pass_exposure), contributors, reserves


def describe(projection, *, history, info, team, environment, report, status_of,
             base_metrics, opportunity_metrics, team_id, season, week, distributions):
    lines = info[projection.athlete_id]["lines"]
    recent = lines[:6]
    sample = len(recent)
    primary = "pass_att" if projection.position == "QB" else "rush_car" if projection.position == "RB" else "rec"
    values = [float(getattr(line, primary)) for line in recent]
    mean = statistics.fmean(values) if values else 0
    cv = statistics.pstdev(values) / mean if len(values) > 1 and mean > 0 else None
    warnings = []
    if report is None:
        warnings.append("No current conference availability report: availability is unverified")
    if sample < 3:
        warnings.append("Limited observed role history")
    if cv is not None and cv > .5:
        warnings.append("Recent opportunity is volatile")
    if projection.availability in ("questionable", "game_time_decision"):
        warnings.append("Playing status remains unresolved; projection assumes availability")
    if projection.availability == "out_first_half":
        warnings.append("First-half absence: opportunity is allocated separately for each half")
    peers = [info[aid]["name"] for aid, state in status_of.items()
             if aid != projection.athlete_id and state in ("questionable", "game_time_decision")]
    if peers:
        warnings.append("Unresolved teammate availability can change the allocation: " + ", ".join(sorted(peers)))
    recent_means = {key: statistics.fmean(float(getattr(line, key)) for line in lines[:3])
                    for key in projection.metrics if lines}
    developments = {}
    predictability = {}
    components = {}
    for key, value in projection.metrics.items():
        previous = recent_means.get(key)
        developments[key] = {"last_appearances_mean": round(previous, 3) if previous is not None else None,
                             "appearances": min(3, len(lines)), "projection": round(value, 3),
                             "direction": "progression candidate" if previous is not None and value > previous * 1.15
                             else "regression candidate" if previous is not None and value < previous * .85
                             else "similar to recent output"}
        dist = distributions[key]
        width = (dist.get("p90", value) - dist.get("p10", value)) / max(abs(value), 1)
        rank = 0 if sample >= 5 and cv is not None and cv <= .35 and width <= 1.4 else 1
        if sample < 3 or cv is not None and cv > .5 or width > 2:
            rank = 2
        if report is None or projection.availability in ("questionable", "game_time_decision", "out_first_half") or peers:
            rank = max(rank, 2)
        label = ("relatively stable", "moderate variability", "uncertain role or availability")[rank]
        predictability[key] = {"label": label, "rank": rank, "p10": dist.get("p10"),
                               "median": dist.get("p50"), "p90": dist.get("p90"),
                               "basis": "Outcome spread, observed opportunity and availability; not wager win probability"}
        historical = base_metrics.get(key, 0)
        neutral = opportunity_metrics.get(key, historical)
        components[key] = {"historical_role": historical, "pool_normalization": neutral - historical,
                           "availability_and_role_change": value - neutral, "final": value}
    prior = history.before(team_id, season, week)
    last = prior[-1] if prior else None
    last_seen = next((index for index, box in enumerate(reversed(prior))
                      if any(p.athlete_id == projection.athlete_id and p.team_id == team_id for p in box.players)), None)
    if last_seen and last_seen > 0:
        warnings.append(f"No recorded stat line in the last {last_seen} team games; returning role is uncertain")
        for row in predictability.values():
            row.update({"label": "returning role unobserved", "rank": 2})
    pass_share = team["pass_att"] / max(team["pass_att"] + team["rush_car"], 1)
    return {
        "predictability": predictability, "forecast_components": components,
        "profile": {"data_cutoff": {"season": season, "before_week": week},
                    "role_games": sample, "recent_opportunity_cv": cv,
                    "availability": projection.availability,
                    "availability_report": {key: report.to_json().get(key) for key in
                                            ("conference", "report", "published", "game_date")} if report else None,
                    "absorbs": list(projection.absorbs), "warnings": warnings,
                    "developments": developments,
                    "team_context": {"team_margin": environment.margin, "game_total": environment.total,
                                     "passing_attempt_share": round(pass_share, 3),
                                     "passing_attempts": team["pass_att"], "rushing_attempts": team["rush_car"]},
                    "process_basis": "Pre-week box-score opportunity, team/opponent tendencies and current conference report",
                    "schematic_limits": "Coverage assignments, routes and expected completion data are not supplied by these sources",
                    "role_basis": "Observed game usage; no current depth assignment is supplied",
                    "last_team_game": last.event_id if last else None},
    }
