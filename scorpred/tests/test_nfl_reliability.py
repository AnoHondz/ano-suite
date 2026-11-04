"""Offline Phase 2.2 split, holdout, singleton and legacy-writer adversaries."""
import copy
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import numpy as np
import pytest
from filelock import FileLock, Timeout

from scripts import nfl_collector as collector
from services import nflverse_store as store
from services.nfl_reliability import register_protocol
from services.nfl_winner_evaluation import (
    EvaluationConfig,
    folds,
    metrics,
    paired_intervals,
)
from services.nfl_winner_features import aware
from tests.test_nfl_winner_research import game, synthetic_dataset


def adaptive_fixture():
    data = synthetic_dataset(groups=32, per_group=6)
    config = EvaluationConfig(split_strategy="adaptive_past_windows", train_groups=4, tune_groups=2,
        calibration_groups=4, min_train=24, min_tune=12, min_calibration=24, min_test=12, bootstrap_samples=5)
    return data, config


def test_adaptive_windows_preserve_samples_groups_and_label_availability():
    data, config = adaptive_fixture()
    for label in data["labels"].values():
        label["usable_after"] = (aware(label["usable_after"]) + timedelta(days=8)).isoformat()
    planned, _ = folds(data, config)
    assert len(planned) >= 2
    seen = set()
    for parts in planned:
        order = ("train", "tune", "calibration_fit", "calibration_select", "test")
        assert len(parts["train"]) >= config.min_train and len(parts["tune"]) >= config.min_tune
        assert all(len(parts[k]) >= 12 for k in ("calibration_fit", "calibration_select"))
        assert len(parts["test"]) >= config.min_test
        assert len({(r["season"], r["week"]) for r in parts["train"]}) >= config.train_groups
        for a, b in pairwise(order):
            cutoff = min(aware(r["prediction_cutoff"]) for r in parts[b])
            assert max(aware(data["labels"][r["game_id"]]["usable_after"]) for r in parts[a]) < cutoff
            assert not {r["game_id"] for r in parts[a]} & {r["game_id"] for r in parts[b]}
        ids = {r["game_id"] for r in parts["test"]}
        assert not seen & ids
        seen.update(ids)


def test_test_outcomes_cannot_choose_split_boundaries_or_hyperparameters():
    data, config = adaptive_fixture()
    planned, _ = folds(data, config)
    altered = copy.deepcopy(data)
    for row in planned[-1]["test"]:
        altered["labels"][row["game_id"]]["home_win"] ^= 1
    changed, _ = folds(altered, config)
    assert planned == changed
    config = EvaluationConfig(split_strategy="adaptive_past_windows", min_train=100000)
    assert not folds(data, config)[0]


def test_sparse_groups_extend_calibration_pastward_instead_of_lowering_floors():
    data, config = adaptive_fixture()
    # Thin alternate weeks; adaptive development windows must grow in groups.
    data["rows"] = [r for r in data["rows"] if r["week"] % 2 or int(r["game_id"].split("-")[-1]) % 6 == 0]
    planned, _ = folds(data, config)
    assert planned
    for parts in planned:
        assert len(parts["calibration_fit"]) >= 12 and len(parts["calibration_select"]) >= 12
        assert len(parts["train"]) >= 24 and len(parts["test"]) >= 12


def test_paired_block_bootstrap_matches_direct_scoring_with_unequal_weeks():
    predictions = []
    random = np.random.default_rng(3)
    groups = []
    for week in range(7):
        part = []
        for index in range(week + 1):
            game_id = f"g{week}-{index}"
            label = index % 2
            part.append((label, float(random.uniform(.1, .9)), .55))
            for name, probability in (("candidate", part[-1][1]), ("home_prior:selected", .55)):
                predictions.append({"game_id": game_id, "season": 2024, "week": week, "model_version": name,
                                    "actual_home_win": label, "home_win_probability": probability})
        groups.append(part)
    result = paired_intervals(predictions, samples=100, seed=42)["candidate"]
    draws, generator = [], np.random.default_rng(42)
    for _ in range(100):
        sample = [r for i in generator.integers(0, 7, 7) for r in groups[i]]
        a, b = metrics([r[0] for r in sample], [r[1] for r in sample]), metrics([r[0] for r in sample], [r[2] for r in sample])
        draws.append([a["log_loss"] - b["log_loss"], a["brier"] - b["brier"]])
    bounds = np.quantile(draws, [.025, .975], axis=0)
    assert np.allclose(result["log_loss_delta_interval"], bounds[:, 0])
    assert np.allclose(result["brier_delta_interval"], bounds[:, 1])
    family = paired_intervals(predictions, samples=100, seed=42, confidence=.99375)["candidate"]
    assert "log_loss_delta_95" not in family and family["confidence"] == .99375


def test_future_holdout_is_registered_once_and_never_reopened(tmp_path):
    path = tmp_path / "nfl.db"
    now = datetime(2026, 10, 19, tzinfo=UTC)
    future = game(12, season=2026, game_id="holdout", gameday="2026-12-01")
    store.replace_dataset("schedules", 2026, [future], path=path, synced_at=now)
    protocol_path = tmp_path / "protocol.json"
    protocol = register_protocol(protocol_path, "frozen", database=path, now=now)
    assert protocol["holdout"]["game_ids"] == ["holdout"] and protocol["holdout"]["sealed"]
    assert "home_score" not in json.dumps(protocol)
    store.replace_dataset("schedules", 2026, [dict(future, home_score=30, away_score=10)], path=path, synced_at=now + timedelta(days=60))
    assert register_protocol(protocol_path, "frozen", database=path, now=now + timedelta(days=70)) == protocol
    with pytest.raises(ValueError, match="never overwrite"):
        register_protocol(protocol_path, "different-dataset", database=path, now=now)
    protocol["holdout"]["sealed"] = False
    protocol_path.write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match="never overwrite"):
        register_protocol(protocol_path, "frozen", database=path, now=now)


def test_cannot_register_already_played_game_as_untouched_holdout(tmp_path):
    path = tmp_path / "nfl.db"
    now = datetime(2026, 10, 19, tzinfo=UTC)
    finished = game(12, scores=True, season=2026, game_id="known", gameday="2026-12-01")
    store.replace_dataset("schedules", 2026, [finished], path=path, synced_at=now)
    with pytest.raises(ValueError, match="already present"):
        register_protocol(tmp_path / "protocol.json", "data", database=path, now=now)


def test_legacy_writer_is_blocked_before_it_can_mutate_records(tmp_path):
    path = tmp_path / "nfl.db"
    row = game(1, scores=True)
    store.replace_dataset("schedules", 2024, [row], path=path)
    before = store.versions("schedules", path=path)
    store.enable_versioned_writes(path=path)
    with store.connection(path) as db:
        for sql in ("DELETE FROM records", "UPDATE records SET observed_at=NULL", "UPDATE records SET payload='{}'",
                    "INSERT INTO records(dataset,season,record_key,payload) VALUES ('schedules',2024,'legacy-new','{}')"):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
    assert store.records("schedules", 2024, path=path) == [row]
    assert store.versions("schedules", path=path) == before
    # The actual current versioned writer still works; old history survives.
    store.replace_dataset("schedules", 2024, [dict(row, home_score=35)], path=path)
    assert len(store.versions("schedules", path=path)) == 2


def test_collector_singleton_and_graceful_stop_without_an_app_restart(tmp_path, monkeypatch):
    path, state = tmp_path / "nfl.db", tmp_path / "collector"
    with FileLock(str(path.resolve()) + ".collector.lock", timeout=0), pytest.raises(Timeout):
        collector.run(path, state, once=True)
    calls = []
    def cycle(*args, **kwargs):
        calls.append(kwargs["collector_identity"])
        (state / "stop.request").write_text("stop after this collection")
        return {"status": "success", "snapshot_count": 1}
    monkeypatch.setattr(collector, "collect_cycle", cycle)
    collector.run(path, state)
    status = json.loads((state / "status.json").read_text())
    assert status["state"] == "stopped" and status["cycles"] == 1 and len(calls) == 1
    assert calls[0]["writer_code_sha256"] == status["code_sha256"]
    assert status["research_only"] and not status["enabled"] and not status["production_active"]


def test_schedule_failure_does_not_hide_successful_feature_collection(tmp_path, monkeypatch):
    path, state = tmp_path / "nfl.db", tmp_path / "collector"
    state.mkdir()
    now = datetime.now(UTC)
    # A fresh PBP partition means this isolated cycle must not use the network.
    from tests.test_nfl_analytics import play
    season = collector.default_season(now)
    store.replace_dataset("pbp", season, [play(season=season, game_id=f"{season}_01_BUF_NE")], path=path, synced_at=now)
    def failed(*args, **kwargs):
        raise OSError("offline fixture")
    monkeypatch.setattr(collector, "refresh_schedules", failed)
    monkeypatch.setattr(collector, "prepare_snapshots", lambda *a, **k: [{"available": True}])
    result = collector.collect_cycle(path, state)
    assert result["status"] == "degraded" and result["snapshot_count"] == 1
    assert result["errors"] == [{"stage": "schedules", "error": "OSError"}]
    assert (state / "cycles.jsonl").exists()
