"""Team identity: logos, colours, conference.

CFBD publishes a logo CDN URL and both team colours, which is what lets the board
render a real matchup rather than two strings. Colours are used only as thin
accents -- the page belongs to the Chase palette, and letting 136 school colours
drive the design would make every card look like a different product.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from cfbmodel.sources import cfbd

# Some CFBD logo entries are dark-mode variants suffixed `-dark`; the board is
# dark already, so the light mark reads better.
_DARK_SUFFIX = "-dark.png"


@dataclass(frozen=True)
class Team:
    school: str
    abbreviation: str | None
    conference: str | None
    color: str | None
    alt_color: str | None
    logo: str | None
    # Kept for odds-feed matching: the Odds API names teams "School Mascot".
    mascot: str | None = None

    @property
    def short(self) -> str:
        return self.abbreviation or self.school


def _pick_logo(logos: list[str] | None) -> str | None:
    if not logos:
        return None
    light = [u for u in logos if not u.endswith(_DARK_SUFFIX)]
    return (light or logos)[0]


def _normalise_hex(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value.startswith("#"):
        value = "#" + value
    return value if len(value) in (4, 7) else None


@lru_cache(maxsize=4)
def load(season: int) -> dict[str, Team]:
    """School name -> Team. Keyed by the same name the games endpoint uses."""
    try:
        rows = cfbd.fbs_teams(season)
    except Exception:
        return {}
    out: dict[str, Team] = {}
    for row in rows:
        school = row.get("school")
        if not school:
            continue
        out[school] = Team(
            school=school,
            abbreviation=row.get("abbreviation"),
            conference=row.get("conference"),
            color=_normalise_hex(row.get("color")),
            alt_color=_normalise_hex(row.get("alternateColor")),
            logo=_pick_logo(row.get("logos")),
            mascot=row.get("mascot"),
        )
    return out


def get(season: int, school: str) -> Team:
    """Never raises: an unknown school still renders, just without a mark."""
    return load(season).get(school) or Team(school, None, None, None, None, None, None)
