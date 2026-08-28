import json

import pytest

from cfbmodel.sources import cfbd


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cfbd, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(cfbd, "_load_env_key", lambda: "test-key")
    return tmp_path


def _stub_response(monkeypatch, payload):
    """Make one fake HTTP call, and count how often the network is touched."""
    calls = {"n": 0}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()

    def fake_urlopen(*a, **kw):
        calls["n"] += 1
        return FakeResponse()

    monkeypatch.setattr(cfbd.json, "load", lambda f: payload)
    monkeypatch.setattr(cfbd.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_non_empty_response_is_cached(cache_dir, monkeypatch):
    calls = _stub_response(monkeypatch, [{"team": "Georgia"}])
    cfbd.get("/talent?year=2024")
    cfbd.get("/talent?year=2024")
    assert calls["n"] == 1, "second call should have been served from cache"
    assert list(cache_dir.glob("*.json"))


def test_empty_response_is_never_cached(cache_dir, monkeypatch):
    """A feed that has not published yet returns []. Caching that pins the model
    to 'no data' long after the data arrives -- which is what happened to the
    2026 talent composite."""
    calls = _stub_response(monkeypatch, [])
    cfbd.get("/talent?year=2026")
    cfbd.get("/talent?year=2026")
    assert calls["n"] == 2, "an empty response must be refetched, not cached"
    assert not list(cache_dir.glob("*.json"))


def test_empty_then_populated_is_picked_up(cache_dir, monkeypatch):
    """The real failure: empty first, real data later, and the model must see it."""
    calls = _stub_response(monkeypatch, [])
    assert cfbd.get("/talent?year=2026") == []
    _stub_response(monkeypatch, [{"team": "Georgia", "talent": 1003.67}])
    assert len(cfbd.get("/talent?year=2026")) == 1


def test_current_season_talent_is_not_cached(monkeypatch):
    """Talent fills in progressively through the offseason."""
    monkeypatch.setattr(cfbd, "_current_season", lambda: 2026)
    seen = {}

    def fake_get(path, *, cacheable=True):
        seen[path] = cacheable
        return []

    monkeypatch.setattr(cfbd, "get", fake_get)
    cfbd.talent(2026)
    cfbd.talent(2024)
    assert seen["/talent?year=2026"] is False
    assert seen["/talent?year=2024"] is True


def test_closed_season_is_cacheable(monkeypatch):
    monkeypatch.setattr(cfbd, "_current_season", lambda: 2026)
    assert cfbd._season_is_closed(2025) is True
    assert cfbd._season_is_closed(2026) is False
