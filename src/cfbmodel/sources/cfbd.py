"""collegefootballdata.com client.

Two things this module exists to enforce.

**Point-in-time.** CFBD's season endpoints accept `endWeek`, and it is a real
filter, not a hint: Ohio State's 2025 offensive PPA reads 0.381 over 297 plays
through week 6 and 0.327 over 886 plays for the full season. Asking for season
totals while forecasting week 6 would hand the model the answer. Every function
here that returns team form therefore *requires* the week you are forecasting and
queries strictly before it.

**Caching.** A completed week never changes, so it is cached to disk forever. The
current week is volatile and is never cached. That distinction is the whole cache
policy; getting it backwards either burns quota or serves stale form.

No third-party dependencies -- stdlib only, so this package stays importable
anywhere without a build step.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api.collegefootballdata.com"
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"
TIMEOUT = 60
RETRIES = 3


class CFBDError(RuntimeError):
    pass


class MissingKey(CFBDError):
    """No API key configured."""


def _load_env_key() -> str | None:
    """Read CFBD_API_KEY from the environment, falling back to a local .env."""
    key = os.getenv("CFBD_API_KEY")
    if key:
        return key.strip()
    env = Path(__file__).resolve().parents[3] / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("CFBD_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def _cache_path(path: str) -> Path:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / f"{digest}.json"


def get(path: str, *, cacheable: bool = True) -> list | dict:
    """GET a CFBD path. `cacheable=False` for anything covering the live week."""
    if cacheable:
        hit = _cache_path(path)
        if hit.is_file():
            try:
                return json.loads(hit.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                hit.unlink(missing_ok=True)  # corrupt entry: refetch

    key = _load_env_key()
    if not key:
        raise MissingKey(
            "CFBD_API_KEY is not set. Get a free key at https://collegefootballdata.com/key "
            "and put it in .env (see .env.example)."
        )
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise CFBDError(f"CFBD rejected the key ({exc.code}). Check CFBD_API_KEY.") from exc
            last = exc
        except Exception as exc:  # noqa: BLE001 - network flakiness is retried
            last = exc
        time.sleep(1.5 * (attempt + 1))
    else:
        raise CFBDError(f"CFBD request failed after {RETRIES} attempts: {path} ({last})")

    if cacheable:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(path).write_text(json.dumps(data), encoding="utf-8")
    return data


# ── Games ────────────────────────────────────────────────────────────────────
def games(season: int, *, season_type: str = "regular", week: int | None = None,
          completed_only: bool = False) -> list[dict]:
    """Games for a season. Includes FCS opponents; filter with `fbs_only`."""
    path = f"/games?year={season}&seasonType={season_type}"
    if week is not None:
        path += f"&week={week}"
    # A season still in progress must not be cached, or the tail freezes.
    rows = get(path, cacheable=_season_is_closed(season))
    if completed_only:
        rows = [g for g in rows if g.get("completed")]
    return rows


def _season_is_closed(season: int) -> bool:
    """A season is safe to cache once it is comfortably in the past."""
    return season < _current_season()


def _current_season() -> int:
    now = time.gmtime()
    # A CFB season is labelled by the calendar year it starts in; it runs into January.
    return now.tm_year if now.tm_mon >= 7 else now.tm_year - 1


def fbs_teams(season: int) -> list[dict]:
    return get(f"/teams/fbs?year={season}")


# ── Point-in-time team form ──────────────────────────────────────────────────
def advanced_season_stats(season: int, *, through_week: int) -> list[dict]:
    """Advanced team stats using ONLY games before `through_week`.

    `through_week` is the week being forecast, so the query ends at the week
    before it. Weeks are 1-indexed; forecasting week 1 has no prior form and
    returns an empty list rather than silently leaking the season.
    """
    if through_week <= 1:
        return []
    return get(f"/stats/season/advanced?year={season}&endWeek={through_week - 1}")


def game_advanced_stats(season: int, *, week: int, exclude_garbage_time: bool = True) -> list[dict]:
    """Per-game advanced team stats, one row per team per game.

    Carries `opponent`, which is what makes opponent adjustment possible --
    the season endpoint only gives pooled totals. `excludeGarbageTime` is a real
    filter (Ohio State: 486 plays -> 411 through week 9).

    Note this endpoint exposes a *different* stat set from the season one:
    `pointsPerOpportunity` and `havoc` are season-only and absent here.
    """
    path = f"/stats/game/advanced?year={season}&week={week}"
    if exclude_garbage_time:
        path += "&excludeGarbageTime=true"
    return get(path, cacheable=_season_is_closed(season) or week < _live_week_guess())


def _live_week_guess() -> int:
    """Rough current week, used only to decide what is safe to cache."""
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    start = _dt.datetime(now.year if now.month >= 7 else now.year - 1, 8, 24, tzinfo=_dt.timezone.utc)
    return max(1, ((now - start).days // 7) + 1)


def season_game_stats(season: int, *, through_week: int,
                      exclude_garbage_time: bool = True) -> list[dict]:
    """Every per-game row for `season` strictly BEFORE `through_week`."""
    out: list[dict] = []
    for week in range(1, max(1, through_week)):
        out.extend(game_advanced_stats(season, week=week,
                                       exclude_garbage_time=exclude_garbage_time))
    return out


def ppa_teams(season: int, *, through_week: int) -> list[dict]:
    if through_week <= 1:
        return []
    return get(f"/ppa/teams?year={season}&endWeek={through_week - 1}")


# ── Season-level priors (legitimately known before kickoff) ──────────────────
def talent(season: int) -> list[dict]:
    """Recruiting talent composite. Known before the season starts."""
    return get(f"/talent?year={season}")


def returning_production(season: int) -> list[dict]:
    """Returning production. Known before the season starts."""
    return get(f"/player/returning?year={season}")


def portal(season: int) -> list[dict]:
    """Transfer portal entries for a season.

    Rows carry `origin`, `destination`, `position`, `stars`, `rating`. A player
    who has not signed anywhere yet has a null `destination`, which is a real
    state and not a parse failure -- `roster.py` treats it as a departure with no
    matching arrival.
    """
    return get(f"/player/portal?year={season}")


def coaches(season: int, *, history: int = 0) -> list[dict]:
    """Coaching records. Each row is a coach with a `seasons` list.

    `history` is not optional in practice. `?year=` filters the nested `seasons`
    list down to that single year, so every coach comes back looking like a
    first-year hire with no record -- which is exactly wrong for the two things
    this feed exists to answer (is the staff new, and what does this coach do).
    Passing `history=n` uses `minYear`/`maxYear` instead and returns the real
    run of seasons.
    """
    if history > 0:
        return get(f"/coaches?minYear={season - history}&maxYear={season}")
    return get(f"/coaches?year={season}")


def venues() -> list[dict]:
    """Every venue: elevation, dome, and `location` lat/long.

    Not season-scoped, so it is cached permanently. Lets home field vary by
    venue instead of applying one 4.53-point constant to all 136 programmes.
    """
    return get("/venues")


# ── Market ───────────────────────────────────────────────────────────────────
def lines(season: int, *, week: int | None = None, season_type: str = "regular") -> list[dict]:
    path = f"/lines?year={season}&seasonType={season_type}"
    if week is not None:
        path += f"&week={week}"
    return get(path, cacheable=_season_is_closed(season))


# ── Benchmarks ───────────────────────────────────────────────────────────────
def sp_ratings(season: int) -> list[dict]:
    """SP+ (Bill Connelly). A strong public benchmark to measure against."""
    return get(f"/ratings/sp?year={season}")
