"""the-odds-api.com client -- live sportsbook lines.

CFBD's `/lines` is the historical/consensus source and stays the model's market
benchmark. This is the *live book* feed: what a named sportsbook is showing right
now, so the board can display a real price alongside the consensus.

**Credits.** Every odds request costs `markets x regions`. A `/v4/sports` call
costs nothing and returns the quota headers, so `remaining()` preflights without
spending. Responses are cached to disk keyed by the request, and the cache is
short-lived because a live line is only interesting while it is live.

**Fanatics.** Requested, but the API does not offer it: a `bookmakers=fanatics`
query returns 111 NCAAF events with zero Fanatics books, and NFL shows the same.
`PREFERRED_BOOKS` is ordered, so if the API adds Fanatics it is picked up with no
code change. Today the first available book wins, and FanDuel covers 110 of 111
NCAAF games.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_ncaaf"
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache" / "odds"
CACHE_TTL_SECONDS = 15 * 60
TIMEOUT = 45

# Ordered preference. Fanatics is first so it is used the moment the API carries
# it; everything after is a fallback in rough order of NCAAF coverage.
PREFERRED_BOOKS: tuple[str, ...] = (
    "fanatics",
    "fanduel",
    "draftkings",
    "betmgm",
    "espnbet",
    "betrivers",
    "ballybet",
    "hardrockbet",
)


class OddsAPIError(RuntimeError):
    pass


class MissingKey(OddsAPIError):
    """No API key configured."""


class QuotaExhausted(OddsAPIError):
    """Refusing to spend the last of the quota."""


def _load_key() -> str | None:
    key = os.getenv("ODDS_API_KEY")
    if key:
        return key.strip()
    env = Path(__file__).resolve().parents[3] / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("ODDS_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def _cache_path(url: str) -> Path:
    return CACHE_DIR / (hashlib.sha256(url.encode()).hexdigest()[:20] + ".json")


def _get(path: str, params: dict, *, ttl: int = CACHE_TTL_SECONDS) -> tuple[list | dict, dict]:
    key = _load_key()
    if not key:
        raise MissingKey(
            "ODDS_API_KEY is not set. Get a key at https://the-odds-api.com and put "
            "it in .env (see .env.example)."
        )
    url = f"{BASE}{path}?" + urllib.parse.urlencode({**params, "apiKey": key})
    cached = _cache_path(url)
    if ttl and cached.is_file() and (time.time() - cached.stat().st_mtime) < ttl:
        try:
            payload = json.loads(cached.read_text(encoding="utf-8"))
            return payload["data"], payload.get("headers", {})
        except (json.JSONDecodeError, KeyError):
            cached.unlink(missing_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            data = json.load(resp)
            headers = {
                "remaining": resp.headers.get("x-requests-remaining"),
                "used": resp.headers.get("x-requests-used"),
                "last_cost": resp.headers.get("x-requests-last"),
            }
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise OddsAPIError(f"Odds API rejected the key ({exc.code}).") from exc
        if exc.code == 429:
            raise QuotaExhausted("Odds API quota exhausted (429).") from exc
        raise OddsAPIError(f"Odds API error {exc.code}: {exc.read()[:200]!r}") from exc
    if ttl:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(url).write_text(json.dumps({"data": data, "headers": headers}), encoding="utf-8")
    return data, headers


def remaining() -> int | None:
    """Credits left. Costs nothing -- `/v4/sports` is a free endpoint."""
    try:
        _, headers = _get("/sports/", {}, ttl=60)
    except OddsAPIError:
        return None
    value = headers.get("remaining")
    return int(value) if value is not None else None


# ── team-name matching ───────────────────────────────────────────────────────
# The Odds API says "Ohio State Buckeyes"; CFBD says school "Ohio State" with a
# separate mascot. Accents and punctuation also differ (San José State vs San
# Jose State, Hawai'i vs Hawaii), so matching is done on a stripped key.
def normalise(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(ch for ch in ascii_only.lower() if ch.isalnum())


# Divergences no amount of prefix matching resolves, because the two sources use
# different names rather than different formatting. Found by inverting the match:
# listing FBS schools that no odds name resolved to.
ALIASES: dict[str, str] = {
    "appalachianstate": "App State",
    "appalachianstatemountaineers": "App State",
    "umass": "Massachusetts",
    "umassminutemen": "Massachusetts",
}


def build_index(team_meta: dict) -> dict[str, str]:
    """Lookup key -> CFBD school, for both 'School' and 'School Mascot' forms."""
    index: dict[str, str] = {}
    for school, team in team_meta.items():
        index.setdefault(normalise(school), school)
        mascot = getattr(team, "mascot", None)
        if mascot:
            index.setdefault(normalise(f"{school} {mascot}"), school)
    for alias, school in ALIASES.items():
        if school in team_meta:
            index.setdefault(alias, school)
    return index


def match_team(name: str, index: dict[str, str]) -> str | None:
    """Resolve an Odds-API team name to a CFBD school."""
    key = normalise(name)
    if key in index:
        return index[key]
    # "Ohio State Buckeyes" -> longest school key that prefixes it. Longest wins
    # so "Miami" never steals a game from "Miami (OH)".
    best: str | None = None
    for candidate, school in index.items():
        if key.startswith(candidate) and (best is None or len(candidate) > len(best)):
            best, matched = candidate, school
    return index[best] if best else None


@dataclass(frozen=True)
class BookLine:
    book: str
    book_title: str
    home_spread: float | None      # negative = home favoured, book convention
    total: float | None
    last_update: str | None

    @property
    def home_margin(self) -> float | None:
        """Expected home margin, matching the model's sign convention."""
        return None if self.home_spread is None else -self.home_spread


def _pick_book(bookmakers: list[dict]) -> dict | None:
    by_key = {b["key"]: b for b in bookmakers}
    for preferred in PREFERRED_BOOKS:
        if preferred in by_key:
            return by_key[preferred]
    return bookmakers[0] if bookmakers else None


def fetch_lines(team_meta: dict, *, books: tuple[str, ...] = PREFERRED_BOOKS,
                min_remaining: int = 20) -> dict[tuple[str, str], BookLine]:
    """(home_school, away_school) -> the best available book line.

    Refuses to spend when the quota is nearly gone, so a scheduled build cannot
    silently drain the key.
    """
    left = remaining()
    if left is not None and left < min_remaining:
        raise QuotaExhausted(f"only {left} Odds API credits left (floor {min_remaining})")

    data, _ = _get(f"/sports/{SPORT}/odds", {
        "regions": "us,us2",
        "markets": "spreads,totals",
        "oddsFormat": "american",
    })
    index = build_index(team_meta)
    out: dict[tuple[str, str], BookLine] = {}
    for event in data:
        home = match_team(event.get("home_team", ""), index)
        away = match_team(event.get("away_team", ""), index)
        if not home or not away:
            continue
        book = _pick_book(event.get("bookmakers", []))
        if not book:
            continue
        spread = total = None
        for market in book.get("markets", []):
            if market["key"] == "spreads":
                for outcome in market.get("outcomes", []):
                    if match_team(outcome.get("name", ""), index) == home:
                        spread = outcome.get("point")
            elif market["key"] == "totals":
                for outcome in market.get("outcomes", []):
                    if outcome.get("name", "").lower() == "over":
                        total = outcome.get("point")
        out[(home, away)] = BookLine(
            book=book["key"], book_title=book.get("title", book["key"]),
            home_spread=spread, total=total, last_update=book.get("last_update"),
        )
    return out
