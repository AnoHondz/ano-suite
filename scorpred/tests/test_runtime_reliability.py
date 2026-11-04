import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import app as module
import model_tracker as tracker
from services import scheduler
from services.decision_engine import DecisionEngine
from utils.atomic import atomic_write_json


def test_readiness_fails_on_database_error_but_liveness_stays_up(monkeypatch):
    monkeypatch.setattr(module.db.session, "execute", Mock(side_effect=RuntimeError("down")))
    client = module.app.test_client()
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json["db"] == "unavailable"
    assert client.get("/live").status_code == 200


def test_scheduler_uses_injected_brain_and_shuts_down(monkeypatch):
    monitor, brain = Mock(), Mock()
    brain.refresh_cycle.return_value = {"status": "success", "observed_at": datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(scheduler.BackgroundScheduler, "start", lambda self: None)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    sched = scheduler.start(SimpleNamespace(app_context=nullcontext), Mock(), Mock(), Mock(), monitor, match_brain=brain)
    sched.get_job("ml_predictions").func()
    assert brain.refresh_cycle.call_count == len(module.SUPPORTED_LEAGUE_IDS)
    monitor.record_success.assert_called_once_with("ml_predictions", observed_at=brain.refresh_cycle.return_value["observed_at"])
    scheduler.stop()


def test_parallel_tracking_updates_do_not_lose_records(tmp_path, monkeypatch):
    path = tmp_path / "history.json"
    monkeypatch.setattr(tracker, "_TRACKING_FILE", str(path))
    def save(index):
        return tracker.save_prediction("soccer", f"Home{index}", "Away", "A", {"a": 60, "draw": 20, "b": 20}, "Medium", "2026-04-01")
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(save, range(30)))
    assert len(set(ids)) == 30
    assert len(tracker._load_predictions()) == 30


def test_corrupt_history_is_not_replaced_with_empty_data(tmp_path, monkeypatch):
    path = tmp_path / "history.json"
    path.write_text("{partial", encoding="utf-8")
    monkeypatch.setattr(tracker, "_TRACKING_FILE", str(path))
    with pytest.raises(RuntimeError, match="corrupt"):
        tracker.save_prediction("soccer", "Home", "Away", "A", {"a": 60, "draw": 20, "b": 20}, "Medium", "2026-04-01")
    assert path.read_text(encoding="utf-8") == "{partial"


def test_processes_share_the_tracking_transaction_lock(tmp_path):
    script = """
import model_tracker as tracker
import os
tracker._TRACKING_FILE = os.environ['REVIEW_TRACKING_PATH']
for index in range(6):
    tracker.save_prediction('soccer', str(os.getpid()) + '-' + str(index), 'Away', 'A', {'a':60,'b':20,'draw':20}, 'Medium', '2026-04-01')
"""
    path = tmp_path / "shared.json"
    environment = os.environ | {"REVIEW_TRACKING_PATH": str(path)}
    processes = [subprocess.Popen([sys.executable, "-c", script], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
    for process in processes:
        _, errors = process.communicate(timeout=30)
        assert process.returncode == 0, errors.decode()
    assert len(json.loads(path.read_text())["predictions"]) == 18


def test_failed_atomic_replace_preserves_original(tmp_path, monkeypatch):
    import utils.atomic as atomic
    path = tmp_path / "history.json"
    atomic_write_json(path, {"existing": True})
    monkeypatch.setattr(atomic.os, "replace", Mock(side_effect=OSError("disk error")))
    with pytest.raises(OSError):
        atomic_write_json(path, {"new": True})
    assert json.loads(path.read_text()) == {"existing": True}


def test_transient_windows_reader_does_not_break_atomic_writes(tmp_path, monkeypatch):
    import utils.atomic as atomic
    path = tmp_path / "history.json"
    atomic_write_json(path, {"existing": True})
    original_replace = atomic.os.replace
    attempts = []
    def replace(source, destination):
        attempts.append(source)
        if len(attempts) < 3:
            raise PermissionError("Transient reader lock")
        return original_replace(source, destination)
    monkeypatch.setattr(atomic.os, "replace", replace)
    monkeypatch.setattr(atomic.time, "sleep", lambda _: None)
    atomic_write_json(path, {"new": True})
    assert len(attempts) == 3
    assert json.loads(path.read_text()) == {"new": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_expected_return_uses_probability_and_market_price_not_confidence():
    result = DecisionEngine().build_decision({"home_name": "Home", "away_name": "Away", "probabilities": {"home": 60, "draw": 20, "away": 20}, "confidence": 80, "odds": {"home": 2.0}, "data_quality": 85})
    assert result["model_probability"] == 0.6
    assert result["edge_score"] == 0.1
    assert result["expected_value"] == 0.2


def test_missing_market_never_yields_bet():
    result = DecisionEngine().build_decision({"home_name": "Home", "probabilities": {"home": 80, "draw": 10, "away": 10}, "confidence": 80, "data_quality": 85})
    assert result["action"] == "CONSIDER"
