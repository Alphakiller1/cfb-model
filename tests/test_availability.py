from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cfbmodel import authority, forecast, ledger, player_props as pp
from cfbmodel.sources import availability as av
from cfbmodel.sources.espn_box import GameBox, PlayerLine, TeamGame

SCHOOLS = {"Alabama", "Mississippi State", "Western Michigan", "Miami", "Miami (OH)",
           "USC", "Ole Miss", "NC State", "Oregon State", "Colorado State"}

HDI = {
    "2939": {
        "ReportType": "Update 1", "publishDate": "2026-10-01", "postedTime": "19:10:00",
        "footer": {"date": "2026-10-03", "time": "11:00:00"},
        "games": [
            {"teamName": "Alabama", "rows": [
                {"name": "QB #15 Ty Simpson", "status": "Questionable"},
                {"name": "RB #0 AK Dear", "status": "Out"},
                {"name": "LB #8 Justin Hill", "status": "Out - (1st Half)"},
                {"name": "WR #1 Healthy Guy", "status": "Available"},
                {"name": "QB #14 Backup Kid", "status": "Exempt"},
            ]},
            {"teamName": "Mississippi St.", "rows": [
                {"name": "WR #3 Only Available", "status": "Available"},
            ]},
        ],
    },
    "2971": {  # MAC "Report Pending": no rows filed yet
        "ReportType": "Report Pending", "publishDate": "2026-10-03", "postedTime": None,
        "footer": {"date": "2026-10-03"},
        "games": [{"teamName": "Western Mich.", "rows": []}],
    },
}

PAC12 = {"games": [{
    "dateISO": "2026-10-03", "updatedAtUTC": "2026-10-02T02:02:39Z", "notes": "Second Report",
    "away": {"name": "Oregon State", "players": [
        {"name": "Kourdey Glass", "position": "RB", "status": "Doubtful"},
        {"name": "Jacob Anderson", "position": "OL", "status": "Probable"}]},
    "home": {"name": "Colorado State", "players": []},
}]}


def test_team_names_resolve_to_cfbd_schools():
    assert av.school_for("Mississippi St.", SCHOOLS) == "Mississippi State"
    assert av.school_for("Western Mich.", SCHOOLS) == "Western Michigan"
    assert av.school_for("Miami (FL)", SCHOOLS) == "Miami"
    assert av.school_for("Miami (OH)", SCHOOLS) == "Miami (OH)"
    assert av.school_for("Southern California", SCHOOLS) == "USC"
    assert av.school_for("Nowhere Tech", SCHOOLS) is None


def test_hdi_report_parses_designations_and_skips_available_exempt_and_pending():
    raw = av.parse_hdi("SEC", HDI)
    by_team = {team: rows for _, team, _, _, _, rows in raw}
    assert "Western Mich." not in by_team          # pending is not "healthy"
    assert by_team["Mississippi St."] == []          # filed, nobody listed
    bama = {d.player: d for d in by_team["Alabama"]}
    assert set(bama) == {"Ty Simpson", "AK Dear", "Justin Hill"}
    assert bama["AK Dear"].status == av.OUT and bama["AK Dear"].position == "RB"
    assert bama["Justin Hill"].status == av.OUT_FIRST_HALF
    assert bama["Ty Simpson"].jersey == "15"


def test_pac12_feed_parses_and_an_empty_side_is_not_a_report():
    raw = av.parse_pac12(PAC12)
    assert [team for _, team, *_ in raw] == ["Oregon State"]
    assert {d.player: d.status for d in raw[0][5]} == {
        "Kourdey Glass": av.DOUBTFUL, "Jacob Anderson": av.PROBABLE}


def test_reports_match_the_game_by_date_and_keep_the_latest_update():
    later = dict(HDI["2939"], ReportType="Update 2", postedTime="20:00:00",
                 publishDate="2026-10-02",
                 games=[{"teamName": "Alabama", "rows": [
                     {"name": "QB #15 Ty Simpson", "status": "Out"}]}])
    raw = av.parse_hdi("SEC", HDI) + av.parse_hdi("SEC", {"x": later})
    reports = av.team_reports(raw, SCHOOLS)
    bama = av.report_for(reports, "Alabama", "2026-10-04")   # late kickoff, UTC next day
    assert bama.report == "Update 2" and bama.status_of("Ty Simpson") == av.OUT
    assert av.report_for(reports, "Alabama", "2026-10-10") is None  # next week's game
    assert av.report_for(reports, "Mississippi State", "2026-10-03").designations == ()


def test_name_matching_survives_suffixes_and_punctuation():
    assert av.name_key("Alonza Barnett III") == av.name_key("Alonza Barnett")
    assert av.name_key("D'Andre O'Neil Jr.") == av.name_key("DAndre ONeil")


# -- projections --------------------------------------------------------------
def _box(event, week, home_players, *, season=2026):
    players = [PlayerLine(athlete_id=a, name=name, team_id="1", **stats)
               for a, name, stats in home_players]
    players.append(PlayerLine(athlete_id="oqb", name="Opp", team_id="2",
                              pass_att=28, pass_cmp=17, pass_yds=210, rush_car=30))
    return GameBox(event_id=event, season=season, week=week, start=f"{season}-09-{week:02d}",
                   completed=True, neutral=False,
                   teams=[TeamGame("1", "Home U", "Home U", True, 30.0),
                          TeamGame("2", "Away St", "Away St", False, 20.0)],
                   players=players)


STARTER = {"pass_att": 30, "pass_cmp": 20, "pass_yds": 250, "pass_td": 2}
BACKUP = {"pass_att": 4, "pass_cmp": 2, "pass_yds": 20}
RB1 = {"rush_car": 18, "rush_yds": 90, "rush_td": 1}
RB2 = {"rush_car": 8, "rush_yds": 35}


def _hist():
    lineup = [("qb1", "Star Passer", STARTER), ("qb2", "Backup Kid", BACKUP),
              ("rb1", "Lead Back", RB1), ("rb2", "Second Back", RB2)]
    return pp.History([_box(f"e{w}", w, lineup) for w in (1, 2, 3, 4)])


def _report(**statuses):
    rows = tuple(av.Designation("SEC", "Home U", name, "", "", status, "Update 1",
                                "2026-10-01 19:10:00", "2026-10-03")
                 for name, status in statuses.items())
    return av.TeamReport("Home U", "SEC", "2026-10-03", "Update 1", "2026-10-01 19:10:00", rows)


ENV = pp.Environment(margin=7.0, total=55.0)


def test_out_starter_hands_the_qb_role_to_the_backup():
    base = {p.athlete_id: p for p in pp.project_team(_hist(), "1", "2", 2026, 5, ENV)}
    assert base["qb1"].position == "QB" and "qb2" not in base
    hurt = {p.athlete_id: p for p in pp.project_team(
        _hist(), "1", "2", 2026, 5, ENV, report=_report(**{"Star Passer": av.OUT}))}
    assert "qb1" not in hurt
    assert hurt["qb2"].position == "QB" and hurt["qb2"].metrics["pass_att"] > 20


def test_ruled_out_back_redistributes_carries_and_questionable_stays_flagged():
    base = {p.athlete_id: p for p in pp.project_team(_hist(), "1", "2", 2026, 5, ENV)}
    hurt = {p.athlete_id: p for p in pp.project_team(
        _hist(), "1", "2", 2026, 5, ENV,
        report=_report(**{"Lead Back": av.DOUBTFUL, "Star Passer": av.QUESTIONABLE}))}
    assert "rb1" not in hurt
    assert hurt["rb2"].metrics["rush_car"] > base["rb2"].metrics["rush_car"]
    assert hurt["rb2"].metrics["rush_car"] <= base["rb2"].metrics["rush_car"] * pp.MAX_USAGE_SCALE
    assert hurt["qb1"].availability == av.QUESTIONABLE


def test_usual_starter_ignores_the_report():
    assert pp.usual_starter(_hist(), "1", 2026, 5) == ("qb1", "Star Passer")


# -- edge withholding -----------------------------------------------------------
def _forecast(week=6):
    return forecast.game(
        home="Home", away="Away", team_ratings={"Home": 10.0, "Away": 0.0},
        market_margin=6.0, market_total=50.0, preseason_total=54.0, week=week,
        book=type("Book", (), {
            "book_title": "DraftKings", "home_margin": 5.5, "total": 51.5,
            "last_update": "2026-10-01T12:00:00Z", "commence_time": "2026-10-03T16:00:00Z",
        })(),
        authority=authority.current(),
    )


def test_starting_qb_out_withholds_the_edge_but_not_the_margin():
    from dataclasses import replace

    # A validated-regime forecast that publishes an edge.
    f = replace(_forecast(), edge_points=2.5, edge_withheld_reason=None,
                action=authority.current().action_for(2.5, True))
    status = {"Away": {"starting_qb": "Star Passer", "starting_qb_status": av.OUT}}
    held = forecast.withhold_for_availability(f, status)
    assert held.edge_points is None and held.margin == f.margin
    assert "Away starting QB Star Passer listed out" in held.edge_withheld_reason
    questionable = {"Away": {"starting_qb": "Star Passer", "starting_qb_status": av.QUESTIONABLE}}
    assert forecast.withhold_for_availability(f, questionable).edge_points == f.edge_points


def test_ledger_snapshot_records_the_report_it_was_logged_under(tmp_path):
    now = datetime(2026, 10, 1, 13, tzinfo=timezone.utc)
    info = {"starting_qb": "Star Passer", "starting_qb_status": av.QUESTIONABLE,
            "report": _report(**{"Star Passer": av.QUESTIONABLE,
                                 "Lead Back": av.OUT}).to_json()}
    payload = ledger.update(season=2026, week=6, forecasts=[(_forecast(), now + timedelta(days=2))],
                            season_games=[], availability={"Home": info},
                            path=tmp_path / "ledger.json", recorded_at=now)
    snap = payload["snapshots"][0]["availability"]
    assert snap["away"] is None
    assert snap["home"]["starting_qb_status"] == av.QUESTIONABLE
    assert snap["home"]["listed"] == {"out": 1, "questionable": 1}
