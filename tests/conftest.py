"""Shared fixtures: the suite stays offline."""

import pytest


@pytest.fixture(autouse=True)
def _offline_espn(monkeypatch):
    """ESPN's scoreboard is the free fallback book source; tests never reach it."""
    from cfbmodel.sources import espn_odds

    monkeypatch.setattr(espn_odds, "lines", lambda requested="draftkings": ([], ""))
