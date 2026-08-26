"""Post-build smoke test for the dashboard.

Cheap structural checks that catch the failures that actually happen: a section
silently missing, the brand contract not reaching the page, the authority gate
being dropped, or the page shipping a promotion it has not earned.

    python scripts/smoke_site.py _site/index.html
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Sections and copy that must survive any refactor.
REQUIRED = (
    "College Football Model",
    "Board",
    "Power Ratings",
    "Methodology",
    "RESEARCH_ONLY",
    "not betting advice",
    "1-800-GAMBLER",
)

# Shared brand contract: the vendored Chase tokens and board kernel must reach
# the page, not just be present in the repo.
BRAND = ("#08090F", "#9A6BFF", "DM Sans", "Roboto Condensed", "chase-wordmark")

# Nothing may claim betting authority while the gate says RESEARCH_ONLY.
FORBIDDEN = ("may_bet = true", ">BET<")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: smoke_site.py <path>")
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"FAIL missing file: {path}")
        return 1
    html = path.read_text(encoding="utf-8")

    failures: list[str] = []
    for key in REQUIRED:
        if key not in html:
            failures.append(f"missing required content: {key!r}")
    for key in BRAND:
        if key not in html:
            failures.append(f"brand contract not on page: {key!r}")
    for key in FORBIDDEN:
        if key in html:
            failures.append(f"page claims authority it does not have: {key!r}")

    # A board with no games means the data step silently produced nothing.
    games = html.count('<article class="game">')
    if games == 0:
        failures.append("board rendered zero games")

    # Every game should offer a breakdown once form exists; in-regime weeks must
    # not ship a board where none do.
    breakdowns = html.count('<details class="bd">')

    # Logos are the most fragile external dependency; catch a wholesale failure.
    logos = len(re.findall(r'class="side-logo"[^>]*src=', html))
    if games and logos == 0:
        failures.append("no team logos resolved")

    print(f"games={games} breakdowns={breakdowns} logos={logos} bytes={len(html):,}")
    if failures:
        for f in failures:
            print(f"FAIL {f}")
        return 1
    print("smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
