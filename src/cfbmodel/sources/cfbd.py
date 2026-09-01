"""collegefootballdata.com client.

Two things this module exists to enforce.

**Point-in-time.** CFBD's season endpoints accept `endWeek`, and it is a real
filter, not a hint: Ohio State's 2025 offensive PPA reads 0.381 over 297 plays
through week 6 and 0.327 over 886 plays for the full season. Asking for season
totals while forecasting week 6 would hand the model the answer. Every function
here that returns team form therefore *requires* the week you are forecasting and
queries strictly before it.

**Caching.** Closed-season data is immutable. Current-season data is memoised for
one build and retained as a timestamped last-good runtime snapshot, never as a
permanent cache entry. A bounded stale snapshot may bridge a short provider
outage, but its provenance travels to the manifest and dashboard.

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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.collegefootballdata.com"
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"
RUNTIME_CACHE_DIR = Path(
    os.getenv(
        "CFB_RUNTIME_CACHE_DIR",
        str(Path(__file__).resolve().parents[3] / "data" / "runtime-cache" / "cfbd"),
    )
)
TIMEOUT = int(os.getenv("CFBD_TIMEOUT_SECONDS", "25"))
RETRIES = int(os.getenv("CFBD_RETRIES", "2"))


@dataclass(frozen=True)
class FetchStatus:
    """Provenance for one endpoint used during the current build."""

    path: str
    state: str                 # live | memory | historical_cache | stale_snapshot
    fetched_at: str | None
    age_seconds: int | None
    rows: int | None
    stale: bool = False
    error: str | None = None


_MEMORY: dict[str, list | dict] = {}
_STATUS: dict[str, FetchStatus] = {}
_FAILURES: dict[str, str] = {}


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


def _runtime_path(path: str) -> Path:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:20]
    return RUNTIME_CACHE_DIR / f"{digest}.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime | None = None) -> str:
    return (moment or _utc_now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_stamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    except (TypeError, ValueError):
        return None


def _row_count(data: list | dict) -> int | None:
    return len(data) if isinstance(data, (list, dict)) else None


def _record(path: str, state: str, data: list | dict, *, fetched_at: str | None,
            stale: bool = False, error: str | None = None) -> None:
    moment = _parse_stamp(fetched_at)
    age = max(0, int((_utc_now() - moment).total_seconds())) if moment else None
    _STATUS[path] = FetchStatus(
        path=path, state=state, fetched_at=fetched_at, age_seconds=age,
        rows=_row_count(data), stale=stale, error=error,
    )


def clear_run_state() -> None:
    """Clear process-local memoisation and provenance before a new build."""
    _MEMORY.clear()
    _STATUS.clear()
    _FAILURES.clear()


def status_report() -> list[dict]:
    """JSON-safe endpoint provenance, ordered by request path."""
    return [asdict(_STATUS[path]) for path in sorted(_STATUS)]


def _atomic_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def _stale_limit(path: str) -> int:
    """Maximum age of a last-good volatile snapshot after a network failure.

    A stale market is materially different from a stale roster prior. These
    limits keep the build available through a short provider outage while the
    dashboard reports the degradation explicitly.
    """
    if path.startswith("/lines"):
        return 6 * 60 * 60
    if path.startswith("/games") or path.startswith("/stats/game"):
        return 72 * 60 * 60
    return 30 * 24 * 60 * 60


def _read_runtime(path: str, *, max_age: int) -> tuple[list | dict, str] | None:
    snapshot = _runtime_path(path)
    if not snapshot.is_file():
        return None
    try:
        envelope = json.loads(snapshot.read_text(encoding="utf-8"))
        data, fetched_at = envelope["data"], envelope["fetched_at"]
        moment = _parse_stamp(fetched_at)
        if moment is None or (_utc_now() - moment).total_seconds() > max_age:
            return None
        return data, fetched_at
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def get(path: str, *, cacheable: bool = True,
        stale_if_error: int | None = None, timeout: int | None = None,
        attempts: int | None = None) -> list | dict:
    """GET a CFBD path with immutable, in-process, and last-good caching.

    Completed seasons use the permanent cache. Volatile endpoints are fetched
    once per build, then written as last-good snapshots. A short provider outage
    may use a bounded snapshot, but that state is recorded for the build
    manifest and dashboard instead of being silently presented as fresh.
    """
    if path in _MEMORY:
        data = _MEMORY[path]
        previous = _STATUS.get(path)
        _record(path, previous.state if previous else "memory", data,
                fetched_at=previous.fetched_at if previous else None,
                stale=previous.stale if previous else False,
                error=previous.error if previous else None)
        return data

    # A feature may ask for the same source more than once (portal rows feed
    # both quality and churn, for example). After one complete retry sequence,
    # fail that path immediately for the rest of this build instead of turning
    # a provider outage into several minutes of duplicate timeouts.
    if path in _FAILURES:
        raise CFBDError(_FAILURES[path])

    if cacheable:
        hit = _cache_path(path)
        if hit.is_file():
            try:
                data = json.loads(hit.read_text(encoding="utf-8"))
                fetched_at = _stamp(datetime.fromtimestamp(hit.stat().st_mtime, timezone.utc))
                _MEMORY[path] = data
                _record(path, "historical_cache", data, fetched_at=fetched_at)
                return data
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
    request_timeout = TIMEOUT if timeout is None else timeout
    request_attempts = RETRIES if attempts is None else attempts
    last: Exception | None = None
    for attempt in range(request_attempts):
        try:
            with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise CFBDError(f"CFBD rejected the key ({exc.code}). Check CFBD_API_KEY.") from exc
            if exc.code < 500 and exc.code not in (408, 429):
                raise CFBDError(f"CFBD request rejected ({exc.code}): {path}") from exc
            last = exc
        except Exception as exc:  # noqa: BLE001 - network flakiness is retried
            last = exc
        if attempt + 1 < request_attempts:
            time.sleep(1.5 * (attempt + 1))
    else:
        fallback = _read_runtime(
            path, max_age=_stale_limit(path) if stale_if_error is None else stale_if_error
        )
        if fallback is not None:
            data, fetched_at = fallback
            _MEMORY[path] = data
            _record(path, "stale_snapshot", data, fetched_at=fetched_at, stale=True,
                    error=f"{type(last).__name__}: {last}")
            return data
        message = f"CFBD request failed after {request_attempts} attempts: {path} ({last})"
        _FAILURES[path] = message
        _STATUS[path] = FetchStatus(
            path=path, state="error", fetched_at=None, age_seconds=None,
            rows=None, stale=False, error=f"{type(last).__name__}: {last}",
        )
        raise CFBDError(message)

    # Never cache an empty response. A feed that has not published yet returns
    # [], and caching that forever silently pins the model to "no data" long
    # after the data arrives -- which is exactly what happened to the 2026 talent
    # composite: an empty reply was cached, CFBD published 138 teams days later,
    # and only the uncached CI build ever saw them.
    if cacheable and data:
        _atomic_write(_cache_path(path), data)
    fetched_at = _stamp()
    if not cacheable and data:
        _atomic_write(_runtime_path(path), {
            "path": path, "fetched_at": fetched_at, "data": data,
        })
    # Preserve the long-standing empty-feed rule in process memory too. Current
    # offseason feeds legitimately return [] before publication and must be
    # polled again later in the same process.
    if data:
        _MEMORY[path] = data
    _record(path, "live", data, fetched_at=fetched_at)
    return data


# ── Games ────────────────────────────────────────────────────────────────────
def _normalise_game(row: dict) -> dict:
    """Accept both the legacy flat game schema and CFBD's nested 2026 schema."""
    home, away = row.get("homeTeam"), row.get("awayTeam")
    if not isinstance(home, dict) and not isinstance(away, dict):
        return row
    home = home if isinstance(home, dict) else {}
    away = away if isinstance(away, dict) else {}
    venue = row.get("venue") if isinstance(row.get("venue"), dict) else {}
    status = str(row.get("status") or "").lower()
    out = dict(row)
    out.update({
        "homeTeam": home.get("name"),
        "awayTeam": away.get("name"),
        "homePoints": home.get("points"),
        "awayPoints": away.get("points"),
        "homeClassification": home.get("classification"),
        "awayClassification": away.get("classification"),
        "homeConference": home.get("conference"),
        "awayConference": away.get("conference"),
        "venueId": venue.get("id"),
        "completed": row.get("completed", status in {"completed", "final"}),
    })
    return out


def games(season: int, *, season_type: str = "regular", week: int | None = None,
          completed_only: bool = False, classification: str | None = "fbs") -> list[dict]:
    """Games involving the requested classification (FBS by default).

    Asking CFBD for every NCAA division returned roughly 3,700 games per season
    even though fewer than 900 involved an FBS team. The smaller query avoids
    provider timeouts and prevents lower-division-only games from entering the
    shared FCS rating node.
    """
    path = f"/games?year={season}&seasonType={season_type}"
    if classification:
        path += f"&classification={urllib.parse.quote(classification)}"
    if week is not None:
        path += f"&week={week}"
    # A season still in progress must not be cached, or the tail freezes.
    rows = [_normalise_game(g) for g in get(path, cacheable=_season_is_closed(season))]
    if classification == "fbs":
        rows = [g for g in rows if "fbs" in {
            g.get("homeClassification"), g.get("awayClassification")
        }]
    if completed_only:
        rows = [g for g in rows if g.get("completed")]
    return rows


def calendar(season: int) -> list[dict]:
    """Official CFBD week boundaries, used instead of a hard-coded August date."""
    # Calendar is an optional refinement: the clock rule remains deterministic.
    # Do not hold the entire build for the full data-endpoint retry budget when
    # this small metadata endpoint is unavailable.
    return get(f"/calendar?year={season}", cacheable=_season_is_closed(season),
               timeout=8, attempts=1)


def _season_is_closed(season: int) -> bool:
    """A season is safe to cache once it is comfortably in the past."""
    return season < _current_season()


def _current_season() -> int:
    now = time.gmtime()
    # A CFB season is labelled by the calendar year it starts in; it runs into January.
    return now.tm_year if now.tm_mon >= 7 else now.tm_year - 1


def fbs_teams(season: int) -> list[dict]:
    return get(f"/teams/fbs?year={season}", cacheable=_season_is_closed(season))


# ── Point-in-time team form ──────────────────────────────────────────────────
def advanced_season_stats(season: int, *, through_week: int) -> list[dict]:
    """Advanced team stats using ONLY games before `through_week`.

    `through_week` is the week being forecast, so the query ends at the week
    before it. Weeks are 1-indexed; forecasting week 1 has no prior form and
    returns an empty list rather than silently leaking the season.
    """
    if through_week <= 1:
        return []
    return get(f"/stats/season/advanced?year={season}&endWeek={through_week - 1}",
               cacheable=_season_is_closed(season))


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
    return get(f"/ppa/teams?year={season}&endWeek={through_week - 1}",
               cacheable=_season_is_closed(season))


# ── Season-level priors (legitimately known before kickoff) ──────────────────
def talent(season: int) -> list[dict]:
    """Recruiting talent composite. Known before the season starts.

    Published progressively during the offseason, so the current season is not
    cached -- see the empty-response guard in `get`.
    """
    return get(f"/talent?year={season}", cacheable=_season_is_closed(season))


def returning_production(season: int) -> list[dict]:
    """Returning production. Known before the season starts."""
    return get(f"/player/returning?year={season}", cacheable=_season_is_closed(season))


def portal(season: int) -> list[dict]:
    """Transfer portal entries for a season.

    Rows carry `origin`, `destination`, `position`, `stars`, `rating`. A player
    who has not signed anywhere yet has a null `destination`, which is a real
    state and not a parse failure -- `roster.py` treats it as a departure with no
    matching arrival.
    """
    return get(f"/player/portal?year={season}", cacheable=_season_is_closed(season))


def recruiting_teams(season: int) -> list[dict]:
    """Team recruiting classes, progressive until the current cycle closes."""
    return get(f"/recruiting/teams?year={season}", cacheable=_season_is_closed(season))


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
        return get(f"/coaches?minYear={season - history}&maxYear={season}",
                   cacheable=_season_is_closed(season))
    return get(f"/coaches?year={season}", cacheable=_season_is_closed(season))


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
    return get(f"/ratings/sp?year={season}", cacheable=_season_is_closed(season))
