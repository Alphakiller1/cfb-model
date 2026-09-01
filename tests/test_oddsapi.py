import pytest

from cfbmodel.sources import oddsapi


class FakeTeam:
    def __init__(self, mascot):
        self.mascot = mascot


META = {
    "Ohio State": FakeTeam("Buckeyes"),
    "Miami": FakeTeam("Hurricanes"),
    "Miami (OH)": FakeTeam("RedHawks"),
    "San José State": FakeTeam("Spartans"),
    "Hawai'i": FakeTeam("Rainbow Warriors"),
    "NC State": FakeTeam("Wolfpack"),
    "App State": FakeTeam("Mountaineers"),
    "Massachusetts": FakeTeam("Minutemen"),
    "Texas A&M": FakeTeam("Aggies"),
}
INDEX = oddsapi.build_index(META)


def test_normalise_strips_accents_and_punctuation():
    assert oddsapi.normalise("San José State") == "sanjosestate"
    assert oddsapi.normalise("Hawai'i") == "hawaii"
    assert oddsapi.normalise("Texas A&M") == "texasam"


def test_school_and_mascot_form_both_resolve():
    assert oddsapi.match_team("Ohio State", INDEX) == "Ohio State"
    assert oddsapi.match_team("Ohio State Buckeyes", INDEX) == "Ohio State"


def test_accented_and_punctuated_names_match_the_plain_feed_name():
    assert oddsapi.match_team("San Jose State Spartans", INDEX) == "San José State"
    assert oddsapi.match_team("Hawaii Rainbow Warriors", INDEX) == "Hawai'i"


def test_longest_prefix_wins_so_miami_oh_is_not_stolen():
    """'Miami' prefixes 'Miami (OH)' -- without longest-wins the wrong school
    would take the game."""
    assert oddsapi.match_team("Miami (OH) RedHawks", INDEX) == "Miami (OH)"
    assert oddsapi.match_team("Miami Hurricanes", INDEX) == "Miami"


def test_aliases_cover_genuine_name_divergences():
    """These are different names, not different formatting, so no amount of
    prefix matching resolves them."""
    assert oddsapi.match_team("Appalachian State Mountaineers", INDEX) == "App State"
    assert oddsapi.match_team("UMass Minutemen", INDEX) == "Massachusetts"


def test_unknown_team_returns_none_rather_than_guessing():
    assert oddsapi.match_team("Some FCS School Wildcats", INDEX) is None


def test_alias_is_ignored_when_the_school_is_absent():
    """An alias must not invent a team the metadata does not contain."""
    index = oddsapi.build_index({"Ohio State": FakeTeam("Buckeyes")})
    assert oddsapi.match_team("UMass Minutemen", index) is None


def test_book_line_flips_the_spread_to_the_model_convention():
    """Books quote home spread negative-when-favoured; the model uses expected
    home margin, which is the opposite sign."""
    line = oddsapi.BookLine("fanduel", "FanDuel", home_spread=-8.5, total=47.5, last_update=None)
    assert line.home_margin == pytest.approx(8.5)


def test_book_line_without_a_spread_has_no_margin():
    line = oddsapi.BookLine("fanduel", "FanDuel", home_spread=None, total=47.5, last_update=None)
    assert line.home_margin is None


def test_draftkings_is_the_production_default():
    assert oddsapi.PREFERRED_BOOKS[0] == "draftkings"
    assert oddsapi.DEFAULT_BOOK == "draftkings"


def test_preferred_book_wins_over_feed_order():
    books = [{"key": "betrivers", "title": "BetRivers"}, {"key": "fanduel", "title": "FanDuel"}]
    assert oddsapi._pick_book(books)["key"] == "fanduel"


def test_unknown_book_is_dropped_instead_of_mislabelled():
    books = [{"key": "someneworbook", "title": "New Book"}]
    assert oddsapi._pick_book(books) is None


def test_no_bookmakers_yields_none():
    assert oddsapi._pick_book([]) is None


def test_exact_book_lock_never_falls_through_to_another_book():
    books = [{"key": "fanduel", "title": "FanDuel"}]
    assert oddsapi._pick_book(books, ("draftkings",)) is None


def test_bookmaker_env_locks_the_api_request(monkeypatch):
    captured = {}

    monkeypatch.setenv("ODDS_BOOKMAKERS", "fanduel")
    monkeypatch.setattr(oddsapi, "remaining", lambda: 100)

    def fake_get(path, params):
        captured.update(params)
        return [], {}

    monkeypatch.setattr(oddsapi, "_get", fake_get)

    assert oddsapi.fetch_lines(META) == {}
    assert captured["bookmakers"] == "fanduel"
