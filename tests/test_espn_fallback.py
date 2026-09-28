"""DraftKings lines from ESPN when the Odds API cannot serve, still single-book."""

from cfbmodel import teams
from cfbmodel.sources import espn_odds, oddsapi


def _meta():
    def team(school, mascot):
        return teams.Team(school=school, abbreviation=None, conference=None, color=None,
                          alt_color=None, logo=None, mascot=mascot)
    return {"Alabama": team("Alabama", "Crimson Tide"), "Auburn": team("Auburn", "Tigers"),
            "Miami": team("Miami", "Hurricanes"), "Miami (OH)": team("Miami (OH)", "RedHawks")}


QUOTE = {"event_id": "9", "home_name": "Alabama Crimson Tide", "away_name": "Auburn Tigers",
         "commence_time": "2026-10-03T19:30Z", "home_spread": -10.5, "total": 51.5,
         "home_moneyline": -450.0, "away_moneyline": 340.0}


def test_quota_floor_reads_the_same_book_from_espn(monkeypatch):
    monkeypatch.setattr(oddsapi, "remaining", lambda: 2)

    def paid(*args, **kwargs):
        raise AssertionError("paid call")

    monkeypatch.setattr(oddsapi, "_get", paid)
    monkeypatch.setattr(espn_odds, "lines", lambda requested="draftkings": ([QUOTE], "t"))
    line = oddsapi.fetch_lines(_meta(), min_remaining=20)[("Alabama", "Auburn")]
    assert (line.home_spread, line.total, line.book) == (-10.5, 51.5, "draftkings")
    assert line.home_margin == 10.5


def test_a_reused_cache_from_another_week_is_filled_from_espn(monkeypatch):
    monkeypatch.setenv("CFB_REUSE_ODDS_CACHE", "1")
    stale_week = [{"home_team": "Miami Hurricanes", "away_team": "Miami (OH) RedHawks",
                   "bookmakers": [{"key": "draftkings", "title": "DraftKings", "markets": [
                       {"key": "totals", "outcomes": [{"name": "Over", "point": 47.5}]}]}]}]
    monkeypatch.setattr(oddsapi, "_get", lambda *a, **k: (stale_week, {"source": "cache"}))
    monkeypatch.setattr(espn_odds, "lines", lambda requested="draftkings": ([QUOTE], "t"))
    lines = oddsapi.fetch_lines(_meta())
    assert lines[("Alabama", "Auburn")].home_spread == -10.5
    assert lines[("Miami", "Miami (OH)")].total == 47.5


def test_espn_quotes_from_another_book_are_never_used(monkeypatch):
    monkeypatch.setattr(oddsapi, "remaining", lambda: 2)
    monkeypatch.setattr(espn_odds, "lines", lambda requested="draftkings": ([], "t"))
    try:
        oddsapi.fetch_lines(_meta(), min_remaining=20)
    except oddsapi.QuotaExhausted:
        pass
    else:
        raise AssertionError("with no same-book quote the build must still fail closed")


def test_complete_free_lines_skip_the_paid_request(monkeypatch):
    monkeypatch.setattr(oddsapi, "remaining", lambda: 500)

    def paid(*args, **kwargs):
        raise AssertionError("paid call")

    monkeypatch.setattr(oddsapi, "_get", paid)
    monkeypatch.setattr(espn_odds, "lines", lambda requested="draftkings": ([QUOTE], "t"))
    lines = oddsapi.fetch_lines(_meta(), needed={("Alabama", "Auburn")})
    assert lines[("Alabama", "Auburn")].total == 51.5
