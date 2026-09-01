"""the-odds-api.com client -- live sportsbook lines.

CFBD's `/lines` is the historical/consensus source and stays the model's market
benchmark. This is the *live book* feed: what a named sportsbook is showing right
now, so the board can display a real price alongside the consensus.

**Credits.** Every odds request costs `markets x regions`. A `/v4/sports` call
costs nothing and returns the quota headers, so `remaining()` preflights without
spending. Responses are cached to disk keyed by the request, and the cache is
short-lived because a live line is only interesting while it is live.

The production board is intentionally single-book. A requested DraftKings line
must never fall through to FanDuel (or to the first bookmaker in feed order),
because that would put a correctly formatted but incorrectly sourced price on
the page. Missing coverage is reported as missing coverage.
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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_ncaaf"
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache" / "odds"
CACHE_TTL_SECONDS = 15 * 60
TIMEOUT = 45
DEFAULT_BOOK = "draftkings"

# Ordered options for explicit callers. Production passes exactly one book and
# never falls through; DraftKings is the configured default.
PREFERRED_BOOKS: tuple[str, ...] = (
    "draftkings",
    "fanduel",
    "fanatics",
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


@dataclass(frozen=True)
class OddsStatus:
    state: str
    requested_book: str
    fetched_at: str | None
    remaining: int | None
    events: int
    matched: int
    unmatched: int
    stale: bool = False
    error: str | None = None


_LAST_STATUS = OddsStatus("not_run", DEFAULT_BOOK, None, None, 0, 0, 0)


def _stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def status_report() -> dict:
    return asdict(_LAST_STATUS)


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
            headers = dict(payload.get("headers", {}))
            headers["source"] = "cache"
            headers["fetched_at"] = payload.get("fetched_at") or datetime.fromtimestamp(
                cached.stat().st_mtime, timezone.utc
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            return payload["data"], headers
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
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OddsAPIError(f"Odds API request failed: {exc}") from exc
    headers["source"] = "live"
    headers["fetched_at"] = _stamp()
    if ttl:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = _cache_path(url).with_suffix(".json.tmp")
        temporary.write_text(json.dumps({
            "data": data, "headers": headers, "fetched_at": headers["fetched_at"],
        }), encoding="utf-8")
        temporary.replace(_cache_path(url))
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
    commence_time: str | None = None

    @property
    def home_margin(self) -> float | None:
        """Expected home margin, matching the model's sign convention."""
        return None if self.home_spread is None else -self.home_spread


def _pick_book(bookmakers: list[dict], allowed: tuple[str, ...] = PREFERRED_BOOKS) -> dict | None:
    by_key = {b["key"]: b for b in bookmakers}
    for preferred in allowed:
        if preferred in by_key:
            return by_key[preferred]
    return None


def _number(value, *, low: float, high: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def fetch_lines(team_meta: dict, *, books: tuple[str, ...] | None = None,
                min_remaining: int = 20) -> dict[tuple[str, str], BookLine]:
    """(home_school, away_school) -> the best available book line.

    Refuses to spend when the quota is nearly gone, so a scheduled build cannot
    silently drain the key.
    """
    global _LAST_STATUS
    env_requested = tuple(
        book.strip().lower()
        for book in os.getenv("ODDS_BOOKMAKERS", "").split(",")
        if book.strip()
    )
    requested = env_requested or books or (DEFAULT_BOOK,)
    if len(requested) != 1:
        raise OddsAPIError(
            "The CFB board is single-book; set ODDS_BOOKMAKERS to exactly one sportsbook."
        )
    requested_book = requested[0]
    left = remaining()
    if left is not None and left < min_remaining:
        _LAST_STATUS = OddsStatus(
            "quota_floor", requested_book, None, left, 0, 0, 0,
            error=f"only {left} credits left (floor {min_remaining})",
        )
        raise QuotaExhausted(f"only {left} Odds API credits left (floor {min_remaining})")

    query = {
        "regions": "us",
        "markets": "spreads,totals",
        "oddsFormat": "american",
        "bookmakers": requested_book,
    }
    try:
        data, headers = _get(f"/sports/{SPORT}/odds", query)
    except Exception as exc:
        _LAST_STATUS = OddsStatus(
            "error", requested_book, None, left, 0, 0, 0,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    index = build_index(team_meta)
    out: dict[tuple[str, str], BookLine] = {}
    unmatched = 0
    for event in data:
        home = match_team(event.get("home_team", ""), index)
        away = match_team(event.get("away_team", ""), index)
        if not home or not away:
            unmatched += 1
            continue
        book = _pick_book(event.get("bookmakers", []), requested)
        if not book:
            unmatched += 1
            continue
        spread = total = None
        for market in book.get("markets", []):
            if market["key"] == "spreads":
                for outcome in market.get("outcomes", []):
                    if match_team(outcome.get("name", ""), index) == home:
                        spread = _number(outcome.get("point"), low=-80.0, high=80.0)
            elif market["key"] == "totals":
                for outcome in market.get("outcomes", []):
                    if outcome.get("name", "").lower() == "over":
                        total = _number(outcome.get("point"), low=10.0, high=120.0)
        if spread is None and total is None:
            unmatched += 1
            continue
        out[(home, away)] = BookLine(
            book=book["key"], book_title=book.get("title", book["key"]),
            home_spread=spread, total=total, last_update=book.get("last_update"),
            commence_time=event.get("commence_time"),
        )
    _LAST_STATUS = OddsStatus(
        "fresh" if headers.get("source") == "live" else "cached",
        requested_book=requested_book,
        fetched_at=headers.get("fetched_at"),
        remaining=(int(headers["remaining"]) if headers.get("remaining") is not None else left),
        events=len(data), matched=len(out), unmatched=unmatched,
        # The disk cache is bounded by CACHE_TTL_SECONDS; it is still a fresh
        # quote snapshot, just served without spending another credit.
        stale=False,
    )
    return out
