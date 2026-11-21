from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path

import pytest

from cfbmodel import ledger, player_props, props_view, results_view
from cfbmodel.sources.oddsapi import PropQuote


def test_saved_results_select_prestart_vintage_and_keep_zero_production():
    game = {"season": 2026, "week": 1, "home": "Home", "away": "Away", "kickoff": "2026-09-01T19:00:00Z",
            "recorded_at": "2026-09-01T18:00:00Z", "status": "graded", "model_margin": 2,
            "model_abs_error": 3}
    player = {**game, "player_id": "1", "player_name": "Player", "metrics": {"rush_yds": 55},
              "actual_metrics": {"rush_yds": 0}, "played": False, "model_version": "v1"}
    h = {"snapshots": [game, {**game, "recorded_at": "2026-09-01T20:00:00Z", "model_margin": 99}],
         "player_snapshots": [player], "best_bets": [], "sharp_spots": []}
    data = results_view.payload(h, season=2026)
    assert len(data["games"]) == 1 and data["games"][0]["model_margin"] == 2
    assert data["players"][0]["error"] == 55 and data["players"][0]["recorded_stat_line"] is False
    assert data["excluded_invalid_vintages"] == 1


def test_counts_keep_all_mass_and_high_attempt_tail():
    for stat in player_props.COUNT_STATS:
        d = player_props.distribution(stat, 38 if stat in ("pass_att", "rush_car") else 4)
        assert sum(d["pmf"].values()) == pytest.approx(1, abs=1e-10)
        assert all(float(k).is_integer() and v >= 0 for k, v in d["pmf"].items())
    pmf = player_props.distribution("pass_att", 38)["pmf"]
    assert max(map(int, pmf)) > 40 and sum(v for k, v in pmf.items() if int(k) > 40) > .05
    receipt = player_props.parameter_receipt()
    assert receipt["fit_seasons"] == [2024, 2025] and len(receipt["sha256"]) == 64


def test_quotes_need_player_game_and_unambiguous_threshold():
    p = {"player_name": "Runner", "game_key": "Away @ Home"}
    q = PropQuote("Home", "Away", "Runner", "rush_yds", 55.5, -105, -115, "draftkings", "2026-11-20T20:00Z")
    props_view.attach_quotes([p], [q])
    assert p["lines"][0]["line"] == 55.5
    assert p["lines"][0]["over_price"] == -105
    other = PropQuote("Home", "Away", "Runner", "rush_yds", 65.5, -105, -115, "draftkings", q.last_update)
    props_view.attach_quotes([p], [q, other])
    assert p["lines"] == []


def test_recover_uses_newer_published_grades_without_losing_forecasts(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("collector", Path(__file__).parents[1] / "scripts/collect_native_results.py")
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    path = tmp_path / "ledger.json"
    old = {"snapshots": [{"snapshot_id": "frozen"}], "updated_at": "2026-11-20T12:00:00Z"}
    ledger._write(path, old)
    new = {**old, "updated_at": "2026-11-20T14:00:00Z", "player_snapshots": [{"actual": 12}]}
    from io import BytesIO
    monkeypatch.setattr(collector.urllib.request, "urlopen", lambda *a, **k: BytesIO(gzip.compress(json.dumps(new).encode())))
    assert collector.recover(path)["snapshots"] == old["snapshots"]
    assert ledger._load(path)["player_snapshots"] == new["player_snapshots"]
