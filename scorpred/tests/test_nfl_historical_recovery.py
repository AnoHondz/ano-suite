"""Adversarial offline tests for explicit Class B reconstruction and receipts."""
import copy
import csv
import gzip
import io
import json
import sqlite3
from datetime import timedelta
from unittest.mock import Mock

import pytest

from services import nfl_historical_recovery as recovery
from services import nfl_winner_evaluation as engine
from services import nfl_winner_features as observed
from services import nfl_winner_research as registry
from services import nflverse_provider as provider
from services import nflverse_store as store
from tests.test_nfl_analytics import play
from tests.test_nfl_winner_research import END, game, synthetic_dataset


def test_frozen_replay_checks_content_and_explicit_evidence_class(tmp_path):
    from scripts.nfl_winner_research import load_replay_dataset
    path = tmp_path / "nfl.db"
    archive(path)
    dataset = recovery.build_reconstructed_dataset([2024], path=path, now=END)
    frozen = tmp_path / "dataset.json"
    frozen.write_text(json.dumps(dataset), encoding="utf-8")
    assert load_replay_dataset(frozen, "B") == dataset
    with pytest.raises(ValueError, match="explicitly"):
        load_replay_dataset(frozen, "A")
    dataset["labels"][dataset["rows"][0]["game_id"]]["home_win"] ^= 1
    frozen.write_text(json.dumps(dataset), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint"):
        load_replay_dataset(frozen, "B")


def archive(path, weeks=12):
    schedules, plays = [], []
    for week in range(1, weeks + 1):
        for index in range(2):
            g = game(week, index, scores=True)
            schedules.append(g)
            for side, other in (("home", "away"), ("away", "home")):
                plays.append(play(1 if side == "home" else 2, game_id=g["game_id"], season=2024,
                    week=week, posteam=g[f"{side}_team"], defteam=g[f"{other}_team"],
                    yards_gained=week * (2 if side == "home" else 1)))
    store.replace_dataset("schedules", 2024, schedules, path=path, synced_at=END)
    store.replace_dataset("pbp", 2024, plays, path=path, synced_at=END)
    return schedules, plays


def test_classes_remain_separate_and_real_observations_are_not_backdated(tmp_path):
    path = tmp_path / "nfl.db"
    archive(path)
    strict = observed.build_dataset([2024], path=path, now=END)
    before = store.versions("pbp", path=path)
    data = recovery.build_reconstructed_dataset([2024], path=path, now=END)
    assert not strict["rows"] and len(data["rows"]) == 16
    row = next(r for r in data["rows"] if r["game_id"] == game(6)["game_id"])
    assert row["evidence_class"] == "B" and row["provenance"]["historically_reconstructed"]
    assert observed.aware(row["provenance"]["source_observed_max"]) > observed.aware(row["prediction_cutoff"])
    assert observed.aware(row["provenance"]["availability_assumed_max"]) < observed.aware(row["prediction_cutoff"])
    assert row["team_features"]["home"]["w3_off_yards_per_play"] == 6
    assert row["features"]["diff_w3_off_yards_per_play"] == 3
    assert row["features"]["diff_w8_off_epa_per_play"] is None
    assert row["features"]["diff_w8_off_success_rate"] is None
    assert row["quarterbacks"]["home"]["player_id"] is None
    assert row["game_id"] not in row["provenance"]["source_game_dependencies"]
    assert store.versions("pbp", path=path) == before
    for item in data["availability_manifest"]:
        assert item["dataset_fingerprint"] == data["dataset_fingerprint"]
        assert item["eligible_feature_count"] + item["missing_feature_count"] == 100
    assert data["dataset_fingerprint"] == recovery.build_reconstructed_dataset([2024], path=path, now=END + timedelta(hours=1))["dataset_fingerprint"]


def test_future_games_injuries_qb_and_full_season_averages_cannot_enter(tmp_path):
    path = tmp_path / "nfl.db"
    _schedules, plays = archive(path)
    target = game(6, scores=True)
    baseline = recovery.ReconstructedBuilder(2024, path=path, now=END).build(target)[0]
    for dataset, rows in (("injuries", [{"season": 2024, "season_type": "REG", "week": 6, "team": "BUF", "gsis_id": "future", "report_status": "Out"}]),
                          ("team_season", [{"season": 2024, "team": "BUF", "season_type": "REG", "passing_epa": 99999}])):
        store.replace_dataset(dataset, 2024, rows, path=path, synced_at=END + timedelta(hours=1))
    future = dict(plays[-1], epa=99999, success=0, passer_player_id="future", yards_gained=9999)
    store.replace_dataset("pbp", 2024, [future], path=path, synced_at=END + timedelta(hours=1))
    later = recovery.ReconstructedBuilder(2024, path=path, now=END + timedelta(hours=2)).build(target)[0]
    assert baseline["features"] == later["features"]
    assert future["game_id"] not in later["provenance"]["source_game_dependencies"]


def test_known_retrospective_event_revision_is_excluded_not_substituted(tmp_path):
    path = tmp_path / "nfl.db"
    _schedules, plays = archive(path)
    changed = dict(plays[0], yards_gained=9999)
    before = store.versions("pbp", path=path)
    store.replace_dataset("pbp", 2024, [changed], path=path, synced_at=END + timedelta(hours=1))
    builder = recovery.ReconstructedBuilder(2024, path=path, now=END + timedelta(hours=2))
    assert builder.blocked[changed["game_id"]] == "retrospectively_revised_or_invalid_pbp"
    row, _ = builder.build(game(7, scores=True))
    assert changed["game_id"] not in row["provenance"]["source_game_dependencies"]
    assert all(v in store.versions("pbp", path=path) for v in before)


def test_late_appended_play_does_not_expand_a_historical_source_game(tmp_path):
    path = tmp_path / "nfl.db"
    _schedules, plays = archive(path)
    added = dict(plays[0], play_id=999, yards_gained=9999)
    store.replace_dataset("pbp", 2024, [added], path=path, synced_at=END + timedelta(hours=1))
    builder = recovery.ReconstructedBuilder(2024, path=path, now=END + timedelta(hours=2))
    assert builder.blocked[added["game_id"]] == "retrospectively_extended_pbp_snapshot"
    row, _ = builder.build(game(7, scores=True))
    assert added["game_id"] not in row["provenance"]["source_game_dependencies"]


def test_same_day_unknown_finish_and_publication_delay_are_enforced(tmp_path):
    path = tmp_path / "nfl.db"
    schedules, _plays = archive(path)
    builder = recovery.ReconstructedBuilder(2024, path=path, now=END)
    earlier = schedules[0]
    start = observed.aware(builder.cached_histories["BUF"][0]["kickoff"])
    same_day = {**game(10), "gameday": earlier["gameday"], "gametime": "23:00"}
    cutoff = start + timedelta(hours=5)
    assert earlier["game_id"] not in {g["game_id"] for g in builder.schedule_at(cutoff, same_day)}
    details, reason = recovery.availability(earlier, recovery.RecoveryPolicy())
    assert reason is None and details["completion_time"] is None
    assert observed.aware(details["available_after_assumed"]) == start + timedelta(hours=180)
    assert earlier["game_id"] not in {g["game_id"] for g in builder.schedule_at(start + timedelta(hours=180), same_day)}
    assert earlier["game_id"] in {g["game_id"] for g in builder.schedule_at(start + timedelta(hours=181), same_day)}
    assert recovery.availability(dict(earlier, status="suspended"), builder.policy)[1] == "ambiguous_completion"
    with pytest.raises(ValueError):
        recovery.RecoveryPolicy(publication_lag_hours=0)


def test_explicit_late_publication_cannot_be_overridden_by_assumed_lag(tmp_path):
    path = tmp_path / "nfl.db"
    _schedules, plays = archive(path)
    source = dict(plays[0], published_at=END.isoformat())
    store.replace_dataset("pbp", 2024, [source], path=path, synced_at=END + timedelta(hours=1))
    builder = recovery.ReconstructedBuilder(2024, path=path, now=END + timedelta(hours=2))
    row, _ = builder.build(game(7, scores=True))
    assert source["game_id"] not in row["provenance"]["source_game_dependencies"]
    assert builder.build(dict(game(7, scores=True), published_at=END.isoformat()))[1] == "target_schedule_publication_after_cutoff"


def test_target_labels_and_future_season_cannot_modify_pregame_predictors(tmp_path):
    path = tmp_path / "nfl.db"
    archive(path)
    builder = recovery.ReconstructedBuilder(2024, path=path, now=END)
    first, _ = builder.build(game(7, scores=True))
    second, _ = builder.build(game(7, scores=True, home_score=999, away_score=0))
    assert first["features"] == second["features"]
    data = recovery.build_reconstructed_dataset([2024], path=path, now=END)
    for row in data["rows"]:
        assert all(data["source_game_catalog"][g]["season"] <= row["season"] for g in row["baselines"]["elo"]["source_game_ids"])
        assert "home_win" not in row["features"] and "home_score" not in row


def test_schedule_revisions_and_future_seasons_are_excluded(tmp_path):
    path = tmp_path / "nfl.db"
    archive(path)
    original = recovery.build_reconstructed_dataset([2024], path=path, now=END)
    future = game(1, scores=True, season=2025, game_id="future-year", gameday="2025-09-01")
    store.replace_dataset("schedules", 2025, [future], path=path, synced_at=END)
    combined = recovery.build_reconstructed_dataset([2024, 2025], path=path, now=END)
    assert original["rows"] == combined["rows"]
    changed = game(7, scores=True, location="Neutral")
    store.replace_dataset("schedules", 2024, [changed], path=path, synced_at=END + timedelta(hours=1))
    builder = recovery.ReconstructedBuilder(2024, path=path, now=END + timedelta(hours=2))
    assert builder.build(changed)[1] == "retrospectively_revised_schedule_or_result"


def test_class_b_training_is_labeled_and_cannot_be_used_as_a_live_artifact(tmp_path):
    data = synthetic_dataset()
    data.update(feature_version=recovery.FEATURE_VERSION, schema=recovery.SCHEMA, evidence_class="B",
                supported_feature_names=list(recovery.SUPPORTED_NAMES))
    for row in data["rows"]:
        row.update(evidence_class="B", feature_version=recovery.FEATURE_VERSION)
        row["provenance"].update(evidence_class="B", historically_reconstructed=True, known_revision_check=True,
            availability_assumed_max=row["provenance"]["source_observed_max"], source_observed_max=END.isoformat())
    config = engine.EvaluationConfig(train_groups=4, tune_groups=2, calibration_groups=4, test_groups=2,
        min_train=24, min_tune=12, min_calibration=24, min_test=12, bootstrap_samples=2)
    report, predictions, artifacts = engine.evaluate(data, config, now=END)
    assert report["evidence_class"] == "B" and report["models"]
    assert all(p["evidence_class"] == "B" and p["historically_reconstructed"] for p in predictions)
    assert all(set(a["model"]["feature_names"]) <= set(recovery.SUPPORTED_NAMES) for a in artifacts.values())
    entry = registry.save_run(data, report, predictions, artifacts, root=tmp_path)
    assert entry["evidence_class"] == "B"
    model_id = next(iter(registry.registry(root=tmp_path)["models"]))
    with pytest.raises(ValueError, match="Class B"):
        registry.load_artifact(model_id, root=tmp_path)
    mixed = copy.deepcopy(data)
    mixed["rows"][0]["evidence_class"] = "A"
    with pytest.raises(ValueError, match="separately"):
        engine.folds(mixed, config)


def test_collection_receipts_are_honest_append_only_and_preserve_observations(tmp_path):
    path = tmp_path / "nfl.db"
    archive(path)
    before = store.versions("pbp", path=path)
    rows = store.records("pbp", 2024, path=path)
    report = store.replace_dataset("pbp", 2024, rows, path=path, synced_at=END + timedelta(hours=1),
        source_identifier="test:offline", collection_started_at=END + timedelta(minutes=59))
    assert store.versions("pbp", path=path) == before
    receipts = store.collection_status(path=path, now=END + timedelta(hours=2))
    latest = next(r for r in receipts if r["dataset"] == "pbp")
    assert latest["id"] == report["collection_receipt_id"]
    assert latest["writer_version"] == store.WRITER_VERSION and latest["source_identifier"] == "test:offline"
    assert latest["associated_games"] == 24 and latest["age_seconds"] == 3600
    assert latest["source_publication_at"] is None and latest["collected_at"] == latest["ingested_at"]
    assert len(latest["content_sha256"]) == 64
    store.record_refresh("pbp", 2024, "failed", error="Timeout", now=END + timedelta(hours=2), path=path)
    failed = next(r for r in store.collection_status(path=path, now=END + timedelta(hours=3)) if r["dataset"] == "pbp")
    assert failed["status"] == "failed" and failed["collected_at"] is None and failed["age_seconds"] == 7200
    with store.connection(path) as db:
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3, 4, 5]
        for table in ("record_versions", "collection_receipts", "collection_games"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute(f"DELETE FROM {table}")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE record_versions SET observed_at='2000-01-01T00:00:00+00:00'")
    assert store.versions("pbp", path=path) == before
    with pytest.raises(ValueError):
        store.replace_dataset("pbp", 2024, rows, path=path, synced_at=END, source_publication_at=END + timedelta(days=1))


def test_network_receipt_uses_collection_clock_not_request_evaluation_time(tmp_path, monkeypatch):
    path = tmp_path / "nfl.db"
    row = game(7, scores=True)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    response = Mock(headers={"Last-Modified": "archive HTTP timestamp", "ETag": "archive-content-tag"})
    response.iter_content.return_value = [gzip.compress(output.getvalue().encode())]
    monkeypatch.setattr(provider.requests, "get", lambda *a, **k: response)
    monkeypatch.setattr(store, "database_path", lambda: path)

    class ReceiptClock:
        @staticmethod
        def now(_timezone):
            return END + timedelta(hours=1)

    monkeypatch.setattr(provider, "datetime", ReceiptClock)
    received = provider.refresh_schedules([2024], now=END)
    assert received == END + timedelta(hours=1)
    latest = store.collection_status(path=path, now=received)[0]
    assert latest["attempted_at"] == END.isoformat()
    assert latest["collected_at"] == received.isoformat() and latest["freshness"] == "fresh"
    assert latest["source_identifier"] == provider.SCHEDULE_URL
    assert latest["source_publication_at"] is None
    assert latest["metadata"]["publication_timestamp_verified"] is False
    assert latest["metadata"]["http_last_modified"] == "archive HTTP timestamp"
    assert not store.records_as_of("schedules", 2024, END, path=path)
    assert len(store.records_as_of("schedules", 2024, received, path=path)) == 1
    response.close.assert_called_once()
