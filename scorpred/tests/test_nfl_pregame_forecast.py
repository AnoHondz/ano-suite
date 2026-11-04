"""Hermetic frozen-model, real-time archive and sealed-outcome adversaries."""
import copy
import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from filelock import FileLock, Timeout

from services import nfl_frozen_models as frozen
from services import nfl_pregame_features as inputs
from services import nfl_pregame_forecast as forecast
from services import nflverse_store as store
from services.nfl_historical_recovery import build_reconstructed_dataset
from services.nfl_reliability import reject_sealed_games
from services.nfl_winner_evaluation import EvaluationConfig, evaluate, train_classifier
from services.nfl_winner_features import aware, digest
from services.nfl_winner_research import save_run
from tests.test_nfl_analytics import play, schedule
from tests.test_nfl_winner_research import seed_history

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


def seed_current(path):
    schedules, plays = [], []
    for week in range(1, 7):
        when = NOW - timedelta(days=(7 - week) * 7)
        g = schedule(f"2026_{week:02}_NE_BUF", when, week=week, home_score=24 if week % 2 else 17, away_score=17 if week % 2 else 24)
        schedules.append(g)
        for index, (team, opponent) in enumerate((("BUF", "NE"), ("NE", "BUF"))):
            plays.append(play(index + 1, game_id=g["game_id"], season=2026, week=week, posteam=team, defteam=opponent))
    schedules.append(schedule("2026_07_NE_BUF", NOW + timedelta(days=2), week=7, home_score=None, away_score=None))
    store.replace_dataset("schedules", 2026, schedules, path=path, synced_at=NOW - timedelta(seconds=10))
    store.replace_dataset("pbp", 2026, plays, path=path, synced_at=NOW - timedelta(seconds=10))


@pytest.fixture(scope="module")
def base_pair(tmp_path_factory):
    root = tmp_path_factory.mktemp("frozen-nfl")
    path = root / "base.db"
    seed_history(path, weeks=30, tie_week=None)
    data = build_reconstructed_dataset([2024], path=path, now=datetime(2025, 6, 1, tzinfo=UTC))
    config = EvaluationConfig(split_strategy="adaptive_past_windows", candidate_names=("logistic_baseline",),
        train_groups=4, tune_groups=2, calibration_groups=4, test_groups=2, min_train=8, min_tune=4,
        min_calibration=20, min_test=4, bootstrap_samples=5)
    report, predictions, artifacts = evaluate(data, config)
    assert artifacts
    catalog = root / "source"
    entry = save_run(data, report, predictions, artifacts, root=catalog)
    ids = ["2026_12_NE_BUF"] + [f"2026_12_fixture_{i}" for i in range(108)]
    protocol = {"holdout": {"sealed": True, "game_ids": ids, "kickoffs_at_registration": {g: "2026-11-26T01:00:00+00:00" for g in ids},
                           "earliest_review_at": "2027-03-01T00:00:00+00:00"}, "development_dataset_fingerprint": data["dataset_fingerprint"]}
    protocol["protocol_sha256"] = digest(protocol)
    protocol_path = root / "protocol.json"
    protocol_path.write_text(json.dumps(protocol))
    holds = [schedule(g, datetime(2026, 11, 26, 1, tzinfo=UTC), week=12, home_score=None, away_score=None) for g in ids]
    store.replace_dataset("schedules", 2026, holds, path=path, synced_at=NOW - timedelta(seconds=30))
    plan = frozen.register(path=path, source=catalog / entry["run_id"], protocol_path=protocol_path, _clock=lambda: NOW)
    seed_current(path)
    return root, path, protocol_path, catalog / entry["run_id"], plan


@pytest.fixture
def pair(tmp_path, monkeypatch, base_pair):
    _root, baseline, protocol, source, plan = base_pair
    path = tmp_path / "nfl.db"
    with sqlite3.connect(baseline) as a, sqlite3.connect(path) as b:
        a.backup(b)
    monkeypatch.setenv("SCORPRED_DATA_ROOT", str(tmp_path))
    target = tmp_path / "cache/nflverse/phase2_2/protocol.json"
    target.parent.mkdir(parents=True)
    shutil.copyfile(protocol, target)
    return path, source, plan


def built(pair):
    path, _, _ = pair
    plan, models = frozen.load_models(path=path)
    target = next(g for g in inputs.observed_schedules(2026, NOW, plan["holdout_game_ids"], path=path) if g["week"] == 7)
    row = inputs.build_inputs(target, models["elo"], plan["holdout_game_ids"], path=path, cutoff=NOW)
    return row, plan, models


def test_original_artifacts_and_model_mapping_reproduce_without_replacement(pair):
    path, source, plan = pair
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob("*.json")}
    again = frozen.register(path=path, source=source, _clock=lambda: NOW + timedelta(days=10))
    assert again == plan
    assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob("*.json")}
    _, models = frozen.load_models(path=path)
    assert frozen.reproduce_registered_logistic(models["logistic"])["model_reproduced_exactly"]
    assert plan["source_bundle"] and len(plan["holdout_kickoffs_at_freeze"]) == 109
    assert models["logistic"]["mapping_validation"]["historical_rows_verified"] > 30
    assert models["logistic"]["feature_names"] == list(inputs.LOGISTIC_NAMES)
    assert not any(m["enabled"] or m["production_active"] for m in models.values())


def test_identical_inputs_and_independent_probability_replay(pair):
    row, plan, models = built(pair)
    a = forecast.probabilities(row, plan, models)
    assert a == forecast.probabilities(copy.deepcopy(row), plan, models)
    m = models["logistic"]["model"]
    vector = np.array([m["fill"][i] if row["features"][n] is None else row["features"][n] for i, n in enumerate(m["feature_names"])])
    logit = ((vector - m["center"]) / m["scale"]) @ m["coef"] + m["intercept"]
    c = models["logistic"]["calibration"]
    if c["method"] == "sigmoid":
        logit = c["coefficient"] * logit + c["intercept"]
    assert abs(a["logistic"] - 1 / (1 + np.exp(-logit))) < 1e-12
    forecast.commit(row, plan, models, path=pair[0], _clock=lambda: NOW)
    assert forecast.replay(path=pair[0]) == {"verified_forecasts": 2, "maximum_absolute_probability_error": 0.0, "holdout_outcomes_read": 0}


def test_atomic_pair_duplicate_sql_replacement_and_restart_retry(pair):
    path = pair[0]
    first = forecast.cycle(path=path, _clock=lambda: NOW)
    assert first["archived"] == 1
    second = forecast.cycle(path=path, _clock=lambda: NOW + timedelta(seconds=5))
    assert second["archived"] == 0
    with store.connection(path) as db:
        assert db.execute("SELECT COUNT(*) FROM nfl_forecasts").fetchone()[0] == 2
        for table in ("nfl_frozen_models", "nfl_forecast_studies", "nfl_forecast_inputs", "nfl_forecast_batches", "nfl_forecasts", "nfl_forecast_events"):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"DELETE FROM {table}")
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} LIMIT 1")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE nfl_forecasts SET payload='{}'")


def test_late_commit_rolls_back_all_records_and_marks_missed_once(pair):
    row, plan, models = built(pair)
    deadline = aware(row["scheduled_kickoff"]) - timedelta(minutes=60)
    with pytest.raises(ValueError, match="late_or_backdated"):
        forecast.commit(row, plan, models, path=pair[0], _clock=lambda: deadline + timedelta(seconds=1))
    result = forecast.cycle(path=pair[0], _clock=lambda: deadline + timedelta(seconds=1))
    assert result["missed"] == 1
    forecast.cycle(path=pair[0], _clock=lambda: deadline + timedelta(hours=3))
    with store.connection(pair[0]) as db:
        assert db.execute("SELECT COUNT(*) FROM nfl_forecasts").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM nfl_forecast_events WHERE kind='missed'").fetchone()[0] == 1


def test_expiration_between_generation_and_database_commit_rolls_back(pair):
    row, plan, models = built(pair)
    # Time cannot silently cross the 60-minute deadline while serializing.
    times = iter((NOW, aware(row["scheduled_kickoff"])))
    with pytest.raises(ValueError, match="deadline_elapsed"):
        forecast.commit(row, plan, models, path=pair[0], _clock=lambda: next(times))
    with store.connection(pair[0]) as db:
        assert db.execute("SELECT COUNT(*) FROM nfl_forecast_batches").fetchone()[0] == 0


def test_missing_data_and_unsupported_features_fail_closed(pair):
    row, plan, models = built(pair)
    assert row["missing_features"] == ["diff_rest_days"]  # Prescribed training-median imputation.
    missing = copy.deepcopy(row)
    missing["features"]["diff_win_rate"] = None
    with pytest.raises(ValueError, match="missing_required"):
        forecast.probabilities(missing, plan, models)
    row["features"]["injury"] = 1
    with pytest.raises(ValueError, match="missing_required"):
        forecast.probabilities(row, plan, models)


def test_invalid_home_away_mapping_and_stale_receipts_are_rejected(pair):
    _row, plan, models = built(pair)
    target = next(g for g in inputs.observed_schedules(2026, NOW, plan["holdout_game_ids"], path=pair[0]) if g["week"] == 7)
    with pytest.raises(ValueError, match="mapping_mismatch"):
        inputs.build_inputs(dict(target, home_team="NE", away_team="BUF"), models["elo"], plan["holdout_game_ids"], path=pair[0], cutoff=NOW)
    with pytest.raises(ValueError, match="stale_schedules"):
        inputs.build_inputs(target, models["elo"], plan["holdout_game_ids"], path=pair[0], cutoff=NOW + timedelta(minutes=10))


def test_dry_run_does_not_archive_and_forecast_lock_is_shared(pair):
    assert forecast.cycle(path=pair[0], dry_run=True, _clock=lambda: NOW)["results"][0]["status"] == "ready"
    with store.connection(pair[0]) as db:
        assert db.execute("SELECT COUNT(*) FROM nfl_forecasts").fetchone()[0] == 0
    with FileLock(str(pair[0].resolve()) + ".forecast.lock", timeout=0), pytest.raises(Timeout):
        forecast.cycle(path=pair[0], _clock=lambda: NOW)


def test_sealed_outcomes_are_redacted_and_cannot_affect_features_or_training(pair):
    row, plan, models = built(pair)
    held = schedule(plan["holdout_game_ids"][0], NOW - timedelta(days=20), week=12, home_score=999, away_score=0)
    original = [g for g in store.records("schedules", 2026, path=pair[0]) if g["game_id"] != held["game_id"]]
    store.replace_dataset("schedules", 2026, original + [held], path=pair[0], synced_at=NOW - timedelta(seconds=5))
    projected = inputs.observed_schedules(2026, NOW, plan["holdout_game_ids"], path=pair[0])
    assert next(g for g in projected if g["game_id"] == held["game_id"])["home_score"] is None
    target = next(g for g in projected if g["week"] == 7)
    after = inputs.build_inputs(target, models["elo"], plan["holdout_game_ids"], path=pair[0], cutoff=NOW)
    assert after["features"] == row["features"] and after["elo_features"] == row["elo_features"]
    with pytest.raises(ValueError, match="sealed"):
        reject_sealed_games([held["game_id"]])
    with pytest.raises(ValueError, match="sealed"):
        train_classifier([{"game_id": held["game_id"]}], {}, inputs.LOGISTIC_NAMES, "logistic", .1)


def test_utc_normalization_and_unknown_timestamps(pair):
    assert aware("2026-10-20T08:00:00-04:00") == NOW
    with pytest.raises(ValueError, match="timezone"):
        inputs.observed_schedules(2026, datetime(2026, 10, 20), [], path=pair[0])
    row, plan, models = built(pair)
    row["source_observed_max"] = (NOW + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="future_source"):
        forecast.probabilities(row, plan, models)


def test_artifact_and_feature_fingerprint_tampering_is_detected(pair, monkeypatch):
    row, plan, models = built(pair)
    altered = copy.deepcopy(models)
    altered["logistic"]["model"]["coef"][0] += 1
    with pytest.raises(ValueError, match="artifact_checksum"):
        forecast.probabilities(row, plan, altered)
    with pytest.raises(ValueError, match="checksum"):
        frozen.read_hashed({"payload": frozen.canonical(models["logistic"]), "sha256": "0" * 64})
    row["feature_names"] = list(reversed(row["feature_names"]))
    with pytest.raises(ValueError, match="provenance"):
        forecast.probabilities(row, plan, models)
    monkeypatch.setattr(frozen, "code_fingerprint", lambda: "modified")
    with pytest.raises(ValueError, match="feature code mismatch"):
        frozen.load_models(path=pair[0])


def test_fabricated_features_are_rejected_against_actual_source_observations(pair):
    row, plan, models = built(pair)
    row["features"]["diff_win_rate"] += .1
    with pytest.raises(ValueError, match="feature_fingerprint"):
        forecast.commit(row, plan, models, path=pair[0], _clock=lambda: NOW)


def test_replay_uses_original_receipts_despite_later_corrections(pair):
    forecast.cycle(path=pair[0], _clock=lambda: NOW)
    # A later complete pull must not replace the receipt or feature vintage.
    later = NOW + timedelta(seconds=20)
    store.replace_dataset("schedules", 2026, store.records("schedules", 2026, path=pair[0]), path=pair[0], synced_at=later,
                          collection_started_at=NOW - timedelta(seconds=1))
    assert forecast.replay(path=pair[0], verify_sources=True)["verified_forecasts"] == 2


def test_registration_refuses_source_corruption_and_late_new_studies(pair, tmp_path):
    empty = tmp_path / "empty.db"
    with pytest.raises(ValueError, match="deadline"):
        frozen.register(path=empty, source=pair[1], _clock=lambda: frozen.REGISTRATION_DEADLINE)
    # A checksum-valid registry cannot hide altered model coefficients from reproduction.
    copy_root = tmp_path / "copied"
    shutil.copytree(pair[1].parent, copy_root)
    source = copy_root / pair[1].name
    artifact_path = max(source.glob("fold*_logistic_baseline.json"), key=lambda p: int(p.stem.split("_")[0][4:]))
    model = json.loads(artifact_path.read_text())
    model["model"]["coef"][0] += .2
    artifact_path.write_text(json.dumps(model))
    catalog_path = copy_root / "registry.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["models"][source.name + "_" + artifact_path.stem]["sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    catalog_path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match="reproduced exactly"):
        frozen.register(path=empty, source=source, _clock=lambda: NOW)


@pytest.mark.parametrize("days", [-3, 3])
def test_schedule_change_appends_correction_and_preserves_original(pair, days):
    forecast.cycle(path=pair[0], _clock=lambda: NOW)
    with store.connection(pair[0]) as db:
        original = list(db.execute("SELECT payload FROM nfl_forecasts"))
        original = [r[0] for r in original]
    target = schedule("2026_07_NE_BUF", NOW + timedelta(days=days), week=7, home_score=None, away_score=None)
    store.replace_dataset("schedules", 2026, [target], path=pair[0], synced_at=NOW + timedelta(seconds=1))
    forecast.cycle(path=pair[0], _clock=lambda: NOW + timedelta(seconds=2))
    with store.connection(pair[0]) as db:
        assert [r[0] for r in db.execute("SELECT payload FROM nfl_forecasts")] == original
        assert db.execute("SELECT COUNT(*) FROM nfl_forecast_events WHERE kind='schedule_changed'").fetchone()[0] == 1


def test_later_commit_of_earlier_observation_cannot_change_saved_features(pair):
    forecast.cycle(path=pair[0], _clock=lambda: NOW)
    # Simulate a collection whose receipt time precedes the evidence cutoff but
    # whose transaction becomes visible only after the original forecast commit.
    plays = store.records("pbp", 2026, path=pair[0])
    plays[0] = {**plays[0], "yards_gained": 999}
    store.replace_dataset("pbp", 2026, plays, path=pair[0], synced_at=NOW - timedelta(seconds=5))
    assert forecast.replay(path=pair[0], verify_sources=True)["verified_forecasts"] == 2


def test_postponed_target_is_rejected_without_archiving(pair):
    _row, plan, models = built(pair)
    target = next(g for g in inputs.observed_schedules(2026, NOW, plan["holdout_game_ids"], path=pair[0]) if g["week"] == 7)
    with pytest.raises(ValueError, match="unsupported_target_game_state"):
        inputs.build_inputs(dict(target, status="postponed"), models["elo"], plan["holdout_game_ids"], path=pair[0], cutoff=NOW)


def test_schedule_only_rest_context_never_reads_sealed_results_and_keeps_latest_receipt(pair):
    _row, plan, models = built(pair)
    schedules = store.records("schedules", 2026, path=pair[0])
    schedules = [dict(g, home_score=None, away_score=None) if g["week"] == 6 else g for g in schedules]
    held = schedule(plan["holdout_game_ids"][0], NOW - timedelta(days=3), week=12, home_score=999, away_score=0)
    schedules = [g for g in schedules if g["game_id"] != held["game_id"]] + [held]
    updated = NOW - timedelta(seconds=5)
    store.replace_dataset("schedules", 2026, schedules, path=pair[0], synced_at=updated)
    target = next(g for g in inputs.observed_schedules(2026, NOW, plan["holdout_game_ids"], path=pair[0]) if g["week"] == 7)
    row = inputs.build_inputs(target, models["elo"], plan["holdout_game_ids"], path=pair[0], cutoff=NOW)
    assert row["features"]["diff_rest_days"] is None
    assert held["game_id"] in row["rest_schedule_context_ids"]
    assert held["game_id"] not in row["source_game_ids"] + row["elo_source_game_ids"]
    assert aware(row["source_observed_max"]) == updated


def test_endpoint_flags_authentication_and_holdout_lock(pair, monkeypatch):
    import user_auth
    from app import app
    monkeypatch.setattr(store, "database_path", lambda: pair[0])
    client = app.test_client()
    route = "/api/nfl/research/winner/forecasts/status"
    assert client.get(route).status_code == 404
    monkeypatch.setenv("NFL_ADVANCED_RESEARCH_ENABLED", "1")
    assert client.get(route).status_code == 404
    monkeypatch.setenv("NFL_WINNER_RESEARCH_ENABLED", "1")
    assert client.get(route).status_code == 401
    monkeypatch.setattr(user_auth, "current_user", lambda: {"id": 1})
    result = client.get(route)
    assert result.status_code == 200 and result.json["result"]["evaluation_locked"]
    assert "home_score" not in result.text and "actual_home_win" not in result.text
    assert client.get("/api/nfl/research/winner/forecasts/evaluation").status_code == 423
    holdout = pair[2]["holdout_game_ids"][0]
    assert client.get(f"/api/nfl/research/winner/{holdout}?model_id=x").status_code == 423
    assert client.get(f"/api/nfl/research/matchups/{holdout}").status_code == 423
    assert client.post(route, json={"enable": True}).status_code == 405
