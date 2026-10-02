"""Official conference availability reports — the college injury report.

Since 2024-26 the SEC, ACC, Big Ten, Big 12, MAC and Pac-12 have required a
public availability report for every conference game: an initial report three
days out, daily updates, and a game-day report shortly before kickoff. Players
not listed (or listed Available) are expected to play.

Two publishers carry all of them:

    HD Intelligence  POST /api/get-publish-public   SEC, ACC, B10, B12, MAC
    Pac-12           blob-hosted report.json        Pac-12

Both are what the conferences' own sites embed, so this is the official report,
not a scrape of beat coverage. ESPN's college injuries endpoint was checked
first and is dead (three rows, all 2020-22).

What this does not cover: non-conference games and the conferences that publish
no report (AAC, Mountain West, Sun Belt, C-USA, independents). A team with no
report is *unknown*, never "healthy" — callers get no entry, not an empty one.

Every response is trimmed to the fields parsed here (the HD payload is ~7 MB of
embedded logos) and cached as a last-good snapshot, so a vendor outage serves
the most recent report with ``stale`` set rather than silently dropping it.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

HDI_URL = "https://app.hdintelligence.com/api/get-publish-public"
HDI_CONFERENCES = ("SEC", "ACC", "B10", "B12", "MAC")
PAC12_URL = ("https://sbcautostorage.blob.core.windows.net/availability-reports/"
             "pac12-football/prod/report.json")
TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 (cfb-model availability reader)"
# Reports update at most a few times a day; half an hour keeps a build current
# without asking the vendor for 7 MB on every run.
FRESH_SECONDS = 30 * 60
# Past this a last-good snapshot describes a different week's game.
STALE_LIMIT_SECONDS = 4 * 24 * 3600
CACHE_DIR = Path(os.environ.get(
    "CFB_AVAILABILITY_CACHE",
    str(Path(__file__).resolve().parents[3] / "data" / "cache" / "availability"),
))

# Canonical statuses, most to least severe.
OUT, OUT_FIRST_HALF, DOUBTFUL, QUESTIONABLE, GAME_TIME, PROBABLE = (
    "out", "out_first_half", "doubtful", "questionable", "game_time_decision", "probable")
SEVERITY = (OUT, OUT_FIRST_HALF, DOUBTFUL, QUESTIONABLE, GAME_TIME, PROBABLE)
# Designations treated as "will not play" by the projection layer. Matches the
# NFL board's rule (Out / Doubtful); Questionable stays in, flagged.
UNAVAILABLE = frozenset({OUT, DOUBTFUL})

_STATUS = {
    "out": OUT,
    "out - (1st half)": OUT_FIRST_HALF,
    "out (1st half)": OUT_FIRST_HALF,
    "out - 1st half": OUT_FIRST_HALF,
    "doubtful": DOUBTFUL,
    "questionable": QUESTIONABLE,
    "game time decision": GAME_TIME,
    "game-time decision": GAME_TIME,
    "probable": PROBABLE,
}

# Report names abbreviate; CFBD spells out. Applied to whole words only.
_ABBREVIATIONS = (
    (r"\bSt\.$", "State"),
    (r"\bMich\.", "Michigan"),
    (r"\bIll\.", "Illinois"),
    (r"\bKy\.", "Kentucky"),
    (r"\bFla\.", "Florida"),
)
_TEAM_ALIASES = {
    "miamifl": "Miami",
    "southerncalifornia": "USC",
    "olemiss": "Ole Miss",
    "mississippi": "Ole Miss",
    "ncstate": "NC State",
    "northcarolinastate": "NC State",
    "uconn": "UConn",
    "massachusetts": "Massachusetts",
    "umass": "Massachusetts",
}


@dataclass(frozen=True)
class Designation:
    conference: str
    team: str               # as the report spells it
    player: str
    position: str
    jersey: str
    status: str             # canonical, one of SEVERITY
    report: str             # "Initial", "Update 1", "Game Day", ...
    published: str          # report publish date/time as the conference states it
    game_date: str

    def to_json(self) -> dict:
        return asdict(self)


# -- names --------------------------------------------------------------------
def name_key(name: str) -> str:
    """Player-name key that survives suffixes, punctuation and accents."""
    decomposed = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()
    text = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", " ", text)
    return "".join(ch for ch in text if ch.isalnum())


def _team_key(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def school_for(team: str, schools: set[str] | dict) -> str | None:
    """Report team name -> CFBD school, or None when it cannot be resolved."""
    pool = set(schools)
    expanded = team.strip()
    for pattern, replacement in _ABBREVIATIONS:
        expanded = re.sub(pattern, replacement, expanded)
    by_key = {_team_key(school): school for school in pool}
    for candidate in (team, expanded):
        key = _team_key(candidate)
        alias = _TEAM_ALIASES.get(key)
        if alias and alias in pool:
            return alias
        if key in by_key:
            return by_key[key]
    return None


def _status(text: str | None) -> str | None:
    return _STATUS.get(re.sub(r"\s+", " ", (text or "").strip().lower()))


_ROW = re.compile(r"^\s*(?P<pos>[A-Z/]+)\s+#?(?P<num>\d+)\s+(?P<name>.+?)\s*$")


def _split_row(text: str) -> tuple[str, str, str]:
    """'RB #0 AK Dear' -> ('RB', '0', 'AK Dear')."""
    match = _ROW.match(text or "")
    if not match:
        return "", "", (text or "").strip()
    return match["pos"], match["num"], match["name"]


# -- parsing ------------------------------------------------------------------
# One filed team report: (conference, team, game_date, report, published, rows).
RawReport = tuple[str, str, str, str, str, list[Designation]]


def parse_hdi(conference: str, payload: dict) -> list[RawReport]:
    out: list[RawReport] = []
    for report in (payload or {}).values():
        if not isinstance(report, dict):
            continue
        footer = report.get("footer") or {}
        game_date = footer.get("date") or ""
        stage = report.get("ReportType") or ""
        published = " ".join(filter(None, (report.get("publishDate"), report.get("postedTime"))))
        for side in report.get("games") or []:
            rows = side.get("rows") or []
            if not rows:  # "Report Pending": nothing filed yet, which is not "healthy"
                continue
            team = side.get("teamName") or side.get("teamDisplayName") or ""
            listed = []
            for row in rows:
                status = _status(row.get("status"))
                if status is None:  # Available or Exempt
                    continue
                position, jersey, player = _split_row(row.get("name") or "")
                listed.append(Designation(
                    conference=conference, team=team, player=player, position=position,
                    jersey=jersey, status=status, report=stage, published=published,
                    game_date=game_date,
                ))
            out.append((conference, team, game_date, stage, published, listed))
    return out


def parse_pac12(payload: dict) -> list[RawReport]:
    out: list[RawReport] = []
    for game in (payload or {}).get("games") or []:
        stage = game.get("notes") or ""
        published = game.get("updatedAtUTC") or ""
        game_date = game.get("dateISO") or ""
        for side in ("away", "home"):
            block = game.get(side) or {}
            players = block.get("players") or []
            if not players:  # the feed's placeholder for "not yet posted"
                continue
            team = block.get("name") or ""
            listed = []
            for row in players:
                status = _status(row.get("status"))
                if status is None:
                    continue
                listed.append(Designation(
                    conference="Pac-12", team=team, player=(row.get("name") or "").strip(),
                    position=row.get("position") or "", jersey=str(row.get("number") or ""),
                    status=status, report=stage, published=published, game_date=game_date,
                ))
            out.append(("Pac-12", team, game_date, stage, published, listed))
    return out


def _trim_hdi(payload: dict) -> dict:
    """Keep only parsed fields: the raw response is mostly base64 imagery."""
    out = {}
    for key, report in (payload or {}).items():
        if not isinstance(report, dict):
            continue
        footer = report.get("footer") or {}
        out[key] = {
            "ReportType": report.get("ReportType"),
            "publishDate": report.get("publishDate"),
            "postedTime": report.get("postedTime"),
            "footer": {"date": footer.get("date"), "time": footer.get("time")},
            "games": [{"teamName": side.get("teamName"),
                       "rows": [{"name": r.get("name"), "status": r.get("status")}
                                for r in side.get("rows") or []]}
                      for side in report.get("games") or []],
        }
    return out


# -- fetching -----------------------------------------------------------------
def _request(url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _cached(source: str, fetch, trim) -> tuple[dict | None, dict]:
    path = CACHE_DIR / f"{source}.json"
    stored = None
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = None
    age = time.time() - stored["fetched_at"] if stored else None
    if stored and age is not None and age < FRESH_SECONDS:
        return stored["payload"], {"source": source, "state": "cached", "age_seconds": int(age)}
    try:
        payload = trim(fetch())
    except Exception as exc:  # vendor outage: fall back to last good, bounded
        if stored and age is not None and age < STALE_LIMIT_SECONDS:
            return stored["payload"], {"source": source, "state": "stale",
                                       "age_seconds": int(age),
                                       "error": f"{type(exc).__name__}: {exc}"}
        return None, {"source": source, "state": "error",
                      "error": f"{type(exc).__name__}: {exc}"}
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fetched_at": time.time(), "payload": payload}),
                        encoding="utf-8")
    except OSError:
        pass
    return payload, {"source": source, "state": "fresh", "age_seconds": 0}


def fetch_all() -> tuple[list[RawReport], list[dict]]:
    """Every filed team report across reporting conferences, plus per-source status."""
    reports: list[RawReport] = []
    statuses: list[dict] = []
    for conference in HDI_CONFERENCES:
        body = {"sport": "Football", "organization": conference, "conference": conference}
        payload, status = _cached(conference, lambda b=body: _request(HDI_URL, b), _trim_hdi)
        statuses.append(status)
        if payload:
            reports.extend(parse_hdi(conference, payload))
    payload, status = _cached("PAC12", lambda: _request(PAC12_URL), lambda p: p)
    statuses.append(status)
    if payload:
        reports.extend(parse_pac12(payload))
    return reports, statuses


@dataclass(frozen=True)
class TeamReport:
    """One team's latest report for one game. Empty `designations` means the
    team filed and listed nobody - known healthy, unlike a team with no report."""
    school: str
    conference: str
    game_date: str
    report: str
    published: str
    designations: tuple[Designation, ...]

    def status_of(self, player: str) -> str | None:
        key = name_key(player)
        return next((d.status for d in self.designations if name_key(d.player) == key), None)

    def to_json(self) -> dict:
        return {
            "conference": self.conference,
            "report": self.report,
            "published": self.published,
            "game_date": self.game_date,
            "designations": [
                {"player": d.player, "position": d.position, "jersey": d.jersey,
                 "status": d.status}
                for d in self.designations
            ],
        }


def team_reports(raw: list[RawReport],
                 schools) -> dict[tuple[str, str], TeamReport]:
    """(school, game_date) -> the latest report that team filed for that game."""
    out: dict[tuple[str, str], TeamReport] = {}
    for conference, team, game_date, report, published, rows in raw:
        school = school_for(team, schools)
        if school is None or not game_date:
            continue
        key = (school, game_date)
        held = out.get(key)
        if held is not None and published <= held.published:
            continue
        ordered = sorted(rows, key=lambda d: (SEVERITY.index(d.status), d.position, d.player))
        out[key] = TeamReport(school, conference, game_date, report, published, tuple(ordered))
    return out


def report_for(reports: dict[tuple[str, str], TeamReport], school: str,
               kickoff_date: str | None) -> TeamReport | None:
    """The report for this school's game on (or a day either side of) kickoff.

    Report dates are local; kickoff is UTC, so a late game lands a day later.
    """
    if not kickoff_date:
        return None
    from datetime import date, timedelta

    try:
        day = date.fromisoformat(kickoff_date[:10])
    except ValueError:
        return None
    for offset in (0, -1, 1):
        found = reports.get((school, (day + timedelta(days=offset)).isoformat()))
        if found is not None:
            return found
    return None
