"""Synthetic observations exercise the engine, never assert NFL performance."""
import copy
import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

import nfl_client
from services import nfl_winner_evaluation as engine
from services import nfl_winner_features as features
from services import nfl_winner_research as registry
from services import nflverse_store as store
from services.nfl_winner_baselines import EloConfig, elo_probability
from tests.test_nfl_analytics import play

START = datetime(2024, 9, 1, 17, tzinfo=UTC)
END = datetime(2025, 2, 1, tzinfo=UTC)


def game(week, index=0, scores=False, **changes):
    when = START + timedelta(weeks=week - 1)
    local = when.astimezone(nfl_client.EASTERN)
    h, a = (("BUF", "NE"), ("KC", "NYJ"))[index]
    row = {"game_id": f"2024_{week:02}_{a}_{h}", "season": 2024, "week": week, "game_type": "REG",
           "gameday": local.date().isoformat(), "gametime": local.strftime("%H:%M"),
           "home_team": h, "away_team": a, "location": "Home", "home_score": None, "away_score": None}
    if scores:
        row.update(home_score=24 if (week + index) % 2 else 17, away_score=17 if (week + index) % 2 else 24)
    row.update(changes)
    return row


def seed_history(path, weeks=12, tie_week=6):
    schedules = [game(w, i) for w in range(1, weeks + 1) for i in range(2)]
    store.replace_dataset("schedules", 2024, schedules, path=path, synced_at=START - timedelta(days=30))
    for week in range(1, weeks + 1):
        stamp = START + timedelta(weeks=week - 1, hours=30)
        finished, plays = [], []
        for index in range(2):
            row = game(week, index, scores=True)
            if week == tie_week and index == 0:
                row.update(home_score=20, away_score=20)
            finished.append(row)
            for side, other in (("home", "away"), ("away", "home")):
                team = row[f"{side}_team"]
                plays.append(play(1 if side == "home" else 2, game_id=row["game_id"], season=2024, week=week,
                                  posteam=team, defteam=row[f"{other}_team"],
                                  passer_player_id="00-" + team, epa=float(week) if side == "home" else -float(week)))
        store.replace_dataset("schedules", 2024, finished, path=path, synced_at=stamp)
        store.replace_dataset("pbp", 2024, plays, path=path, synced_at=stamp)


def test_dataset_windows_labels_orientation_and_ties(tmp_path):
    path = tmp_path / "nfl.db"
    seed_history(path)
    data = features.build_dataset([2024], path=path, now=END)
    # First 3 weeks need a documented warmup; one later tied game is excluded.
    assert data["coverage"]["2024"]["eligible"] == 17
    assert data["coverage"]["2024"]["exclusions"]["tie_excluded_binary_conditional_on_no_tie"] == 1
    target = next(r for r in data["rows"] if r["game_id"] == game(7)["game_id"])
    assert target["home_team"] == "BUF" and target["away_team"] == "NE"
    assert target["team_features"]["home"]["w3_off_epa_per_play"] == 5
    assert target["team_features"]["home"]["w5_off_epa_per_play"] == 4
    assert target["team_features"]["home"]["w8_off_epa_per_play"] == 3.5
    assert target["team_features"]["home"]["wstd_off_epa_per_play"] == 3.5
    assert target["features"]["diff_w3_off_epa_per_play"] == 10
    assert data["labels"][target["game_id"]]["home_win"] == 1
    assert "home_score" not in target and "home_win" not in target["features"]
    assert target["game_id"] not in target["provenance"]["history_game_ids"]
    assert features.aware(target["provenance"]["source_observed_max"]) <= features.aware(target["prediction_cutoff"])
    assert target["quarterbacks"]["home"]["basis"] == "last_observed_game_primary_passer"
    assert target["quarterbacks"]["home"]["starter_confirmed"] is False


def test_post_cutoff_corrections_future_scores_rosters_and_qb_cannot_leak(tmp_path):
    path = tmp_path / "nfl.db"
    seed_history(path)
    builder = features.FeatureBuilder(2024, path=path)
    target = game(7, scores=True)
    original, _ = builder.build(target)
    late = END
    store.replace_dataset("pbp", 2024, [play(game_id=game(5)["game_id"], season=2024, week=5,
                                            epa=99999, passer_player_id="00-future")], path=path, synced_at=late)
    store.replace_dataset("rosters", 2024, [{"season": 2024, "team": "BUF", "gsis_id": "00-future"}], path=path, synced_at=late)
    changed, _ = features.FeatureBuilder(2024, path=path).build(target)
    assert original == changed
    target.update(home_score=999, away_score=1)
    assert features.FeatureBuilder(2024, path=path).build(target)[0]["features"] == original["features"]


def test_schedule_vintage_missing_seasons_and_reproducible_fingerprint(tmp_path):
    path = tmp_path / "nfl.db"
    store.replace_dataset("schedules", 2024, [game(7, scores=True)], path=path, synced_at=END)
    data = features.build_dataset([2023, 2024], path=path, now=END)
    assert data["coverage"]["2023"]["total"] == 0
    assert data["coverage"]["2024"]["exclusions"] == {"target_schedule_not_observed": 1}
    repeated = features.build_dataset([2023, 2024], path=path, now=END + timedelta(days=1))
    assert data["dataset_fingerprint"] == repeated["dataset_fingerprint"]
    assert not data["rows"]


def test_early_missing_qb_and_identifier_aliases_remain_honest(tmp_path):
    path = tmp_path / "nfl.db"
    seed_history(path, tie_week=None)
    target = game(9)
    target["home_team"] = "LAR"
    store.replace_dataset("schedules", 2024, [target], path=path, synced_at=END)
    # The correction is after the pregame cutoff and cannot relabel BUF history.
    row, reason = features.FeatureBuilder(2024, path=path).build(target)
    assert row is None and reason == "schedule_changed_after_cutoff"
    with pytest.raises(ValueError):
        features.FeatureBuilder(2024, path=path).build(game(7), as_of=START + timedelta(weeks=6))


def test_elo_is_sequential_regresses_seasons_and_handles_context_ties():
    cutoff = START + timedelta(weeks=5)
    target = game(6)
    initial = elo_probability(target, [], cutoff)
    past = game(1, scores=True, home_score=30, away_score=10)
    after = elo_probability(target, [past], cutoff)
    assert after["home_rating"] > initial["home_rating"]
    assert after["away_rating"] < initial["away_rating"]
    future = game(7, scores=True, home_score=100, away_score=0)
    assert elo_probability(target, [past, target, future], cutoff) == after
    assert elo_probability(target, [dict(past, _observed_at=END.isoformat())], cutoff)["history_games"] == 0
    assert elo_probability(target, [game(6, 1, scores=True)], cutoff)["history_games"] == 0
    next_year = dict(target, season=2025)
    regressed = elo_probability(next_year, [past], cutoff, EloConfig(season_retention=0))
    assert regressed["home_rating"] == 1500
    tied = elo_probability(target, [dict(past, home_score=20, away_score=20)], cutoff)
    assert tied["history_games"] == 1
    with pytest.raises(ValueError):
        EloConfig(season_retention=2)


def synthetic_dataset(groups=16, per_group=6):
    """Statistical test fixture, explicitly unrelated to actual NFL results."""
    random = np.random.default_rng(42)
    rows, labels = [], {}
    for index in range(groups * per_group):
        group = index // per_group
        cutoff = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(weeks=group)
        values = {name: float(random.normal()) for name in features.FEATURE_NAMES}
        values["home_field"] = 1.
        values["diff_rest_days"] = None if index % 7 == 0 else values["diff_rest_days"]
        identifier = f"synthetic-{index:04}"
        label = index % 2
        rows.append({"game_id": identifier, "season": 2024, "week": group + 1,
                     "prediction_cutoff": cutoff.isoformat(), "kickoff": (cutoff + timedelta(hours=1)).isoformat(),
                     "home_team": "BUF", "away_team": "NE", "features": values,
                     "feature_version": features.FEATURE_VERSION,
                     "provenance": {"source_observed_max": (cutoff - timedelta(days=1)).isoformat()},
                     "baselines": {"elo": {"home_probability": .55}}})
        labels[identifier] = {"home_win": label, "usable_after": (cutoff + timedelta(hours=26)).isoformat()}
    return {"synthetic": True, "rows": rows, "labels": labels, "schema": features.SCHEMA,
            "feature_version": features.FEATURE_VERSION, "feature_names": list(features.FEATURE_NAMES),
            "dataset_fingerprint": features.digest([rows, labels]), "config": {"min_games": 3, "lead_minutes": 60},
            "coverage": {"synthetic": {"eligible": len(rows), "total": len(rows), "exclusions": {}}}}


@pytest.fixture(scope="module")
def evaluated():
    dataset = synthetic_dataset()
    config = engine.EvaluationConfig(train_groups=4, tune_groups=2, calibration_groups=4, test_groups=2,
                                     min_train=24, min_tune=12, min_calibration=24, min_test=12, bootstrap_samples=5)
    report, predictions, artifacts = engine.evaluate(dataset, config, now=END)
    return dataset, config, report, predictions, artifacts


def test_grouped_expanding_folds_label_availability_and_no_shared_games(evaluated):
    _dataset, _config, report, predictions, _artifacts = evaluated
    assert report["status"] == "evaluated_research_only"
    assert len(report["folds"]) == 3
    previous_train = set()
    for fold in report["folds"]:
        windows = fold["windows"]
        ordered = [windows[k] for k in ("train", "tune", "calibration_fit", "calibration_select", "test")]
        for a, b in pairwise(ordered):
            assert a["end"] < b["start"]
            assert a["max_label_usable_after"] < b["start"]
            assert not set(a["game_ids"]) & set(b["game_ids"])
        assert previous_train <= set(windows["train"]["game_ids"])
        previous_train = set(windows["train"]["game_ids"])
    for row in predictions:
        assert row["kind"] == "backtest"
        assert row["training_cutoff"] < row["prediction_cutoff"]
        assert row["training_label_cutoff"] < row["prediction_cutoff"]
        assert row["prediction_generated_at"] == END.isoformat()
        assert row["home_win_probability"] + row["away_win_probability"] == 1
        assert 0 <= row["home_win_probability"] <= 1
    game_sets = {name: {p["game_id"] for p in predictions if p["model_version"] == name} for name in report["models"]}
    assert all(ids == next(iter(game_sets.values())) for ids in game_sets.values())


def test_future_observations_duplicates_single_classes_and_late_labels_rejected(evaluated):
    dataset, config, *_ = evaluated
    duplicate = copy.deepcopy(dataset)
    duplicate["rows"].append(duplicate["rows"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        engine.evaluate(duplicate, config)
    future = copy.deepcopy(dataset)
    future["rows"][0]["provenance"]["source_observed_max"] = END.isoformat()
    with pytest.raises(ValueError, match="availability"):
        engine.evaluate(future, config)
    late = copy.deepcopy(dataset)
    for label in late["labels"].values():
        label["usable_after"] = "2030-01-01T00:00:00+00:00"
    result = engine.evaluate(late, config)[0]
    assert not result["models"] and result["skipped_folds"]
    one_class = copy.deepcopy(dataset)
    for label in one_class["labels"].values():
        label["home_win"] = 1
    assert engine.evaluate(one_class, config)[0]["status"] == "insufficient_safe_history"


def test_metrics_probability_buckets_and_calibration_are_correct():
    m = engine.metrics([0, 1], [.25, .75])
    assert m["log_loss"] == pytest.approx(-np.log(.75))
    assert m["brier"] == .0625
    assert m["accuracy"] == 1 and m["roc_auc"] == 1
    assert sum(b["count"] for b in m["reliability"]) == 2
    assert engine.metrics([1], [1])["roc_auc"] is None
    for probabilities in ([np.nan], [1.1], [-.1]):
        with pytest.raises(ValueError):
            engine.metrics([1], probabilities)
    calibrator = engine.calibrate_fit(np.tile([.2, .8], 30), np.tile([0, 1], 30), "sigmoid")
    p = engine.calibrate_predict(calibrator, [.2, .8])
    assert 0 < p[0] < .2 and .8 < p[1] < 1
    with pytest.raises(ValueError, match="1000"):
        engine.calibrate_fit([.2, .8], [0, 1], "isotonic")
    iso = engine.calibrate_fit(np.tile([.2, .8], 500), np.tile([0, 1], 500), "isotonic")
    assert engine.calibrate_predict(iso, [.2, .8]).tolist() == [0, 1]


def test_training_preprocessing_determinism_and_json_inference(evaluated):
    dataset, config, report, _predictions, artifacts = evaluated
    key = min(artifacts)
    artifact = artifacts[key]
    ids = set(artifact["model"]["preprocessing_train_game_ids"])
    training = [r for r in dataset["rows"] if r["game_id"] in ids]
    names = artifact["model"]["feature_names"]
    expected = np.nanmedian(engine.matrix(training, names), axis=0)
    assert np.allclose(expected, artifact["model"]["fill"])
    again = engine.train_classifier(training, dataset["labels"], names, "logistic", .1, config.seed)
    repeated = engine.train_classifier(training, dataset["labels"], names, "logistic", .1, config.seed)
    assert again == repeated
    forest = engine.train_classifier(training, dataset["labels"], names, "forest", 3, config.seed)
    portable = json.loads(json.dumps(forest))
    assert np.allclose(engine.predict_portable(forest, dataset["rows"]), engine.predict_portable(portable, dataset["rows"]))
    # Independently refit sklearn's actual estimator and compare its probability
    # output with the portable interpreter, rather than only roundtripping JSON.
    from sklearn.ensemble import RandomForestClassifier
    train_x = engine.matrix(training, names)
    train_x = np.where(np.isnan(train_x), forest["fill"], train_x)
    train_x = (train_x - forest["center"]) / forest["scale"]
    independent = RandomForestClassifier(n_estimators=80, max_depth=3, min_samples_leaf=20,
                                         max_features=.7, random_state=config.seed, n_jobs=1).fit(
        train_x, [dataset["labels"][r["game_id"]]["home_win"] for r in training])
    test_x = engine.matrix(dataset["rows"], names)
    test_x = (np.where(np.isnan(test_x), forest["fill"], test_x) - forest["center"]) / forest["scale"]
    assert np.allclose(independent.predict_proba(test_x)[:, 1], engine.predict_portable(forest, dataset["rows"]))
    assert all(a["research_only"] and not a["enabled"] and not a["production_active"] for a in artifacts.values())
    assert report["production_comparison"]["available"] is False
    for fold in report["folds"]:
        for cal in fold["calibration"].values():
            assert cal["isotonic_available"] is False
            selected = cal["selected"]
            if selected != "none":
                assert cal["selection_metrics"][selected]["log_loss"] < cal["selection_metrics"]["none"]["log_loss"]


def test_registry_is_disabled_immutable_and_refuses_tampering(tmp_path, evaluated):
    dataset, _config, report, predictions, artifacts = evaluated
    entry = registry.save_run(dataset, report, predictions, artifacts, root=tmp_path)
    catalog = registry.registry(root=tmp_path)
    model_id = min(catalog["models"])
    model_entry, _artifact = registry.load_artifact(model_id, root=tmp_path)
    assert not model_entry["enabled"] and not model_entry["production_active"]
    assert registry.save_run(dataset, report, predictions, artifacts, root=tmp_path) == entry
    assert registry.evaluation(entry["run_id"], root=tmp_path)["status"] == report["status"]
    path = tmp_path / model_entry["artifact"]
    path.write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        registry.load_artifact(model_id, root=tmp_path)
    with pytest.raises(ValueError):
        registry.load_artifact("../model.pkl", root=tmp_path)
    with pytest.raises(ValueError):
        registry.safe_path(tmp_path, "../outside.json")
    bad = dict(report, enabled=True)
    with pytest.raises(ValueError, match="disabled"):
        registry.save_run(dataset, bad, predictions, artifacts, root=tmp_path)


def test_portable_artifacts_reject_cycles_nonfinite_and_schema_injection():
    model = {"kind": "forest", "feature_names": ["home_field"], "fill": [0], "center": [0], "scale": [1],
             "trees": [{"left": [0], "right": [0], "feature": [0], "threshold": [1], "p": [.5]}]}
    with pytest.raises(ValueError, match="Cyclic"):
        engine.predict_portable(model, [{"features": {"home_field": 1}}])
    model["feature_names"] = ["home_score"]
    with pytest.raises(ValueError, match="schema"):
        engine.predict_portable(model, [{"features": {"home_score": 1}}])


def test_no_history_creates_honest_report_and_no_model(tmp_path):
    from scripts.nfl_winner_research import run
    result = run([2024], path=tmp_path / "empty.db", output=tmp_path / "research")
    assert result["status"] == "insufficient_safe_history"
    assert result["eligible_games"] == result["evaluated_games"] == 0
    assert registry.registry(root=tmp_path / "research")["models"] == {}
    assert "N/A" in Path(result["summary"]).read_text()
    assert result["charts"]["available"] is False


def test_winner_routes_require_both_gates_and_historical_requests_are_separate(monkeypatch, tmp_path):
    from app import app
    monkeypatch.setattr(registry, "root_path", lambda: tmp_path)
    client = app.test_client()
    assert client.get("/api/nfl/research/winner/models").status_code == 404
    monkeypatch.setenv("NFL_ADVANCED_RESEARCH_ENABLED", "1")
    assert client.get("/api/nfl/research/winner/models").status_code == 404
    monkeypatch.setenv("NFL_WINNER_RESEARCH_ENABLED", "1")
    response = client.get("/api/nfl/research/winner/models")
    assert response.status_code == 200 and not response.json["registry"]["models"]
    assert client.get("/api/nfl/research/winner/g?model_id=x&as_of=2020-01-01").status_code == 400
    assert client.get("/api/nfl/research/winner/g").status_code == 400
    assert client.get("/api/nfl/research/winner/g?model_id=missing").status_code == 422


def test_prospective_inference_rejects_backdated_artifacts(tmp_path, evaluated):
    dataset, _config, report, predictions, artifacts = evaluated
    registry.save_run(dataset, report, predictions, artifacts, root=tmp_path / "research")
    model_id = next(iter(registry.registry(root=tmp_path / "research")["models"]))
    path = tmp_path / "nfl.db"
    store.replace_dataset("schedules", 2024, [game(10)], path=path, synced_at=START - timedelta(days=1))
    # Artifact was generated in Feb 2025, so it cannot be passed off as an
    # available live model during the 2024 source fixture.
    with pytest.raises(ValueError, match="unavailable"):
        registry.predict(game(10)["game_id"], model_id, path=path, root=tmp_path / "research", now=START)


def test_live_inference_uses_only_compact_offline_snapshots(tmp_path, evaluated, monkeypatch):
    dataset, _config, report, predictions, artifacts = evaluated
    root = tmp_path / "research"
    registry.save_run(dataset, report, predictions, artifacts, root=root)
    model_id = next(iter(registry.registry(root=root)["models"]))
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    target = game(7, season=2026, gameday="2026-10-20", gametime="13:00", game_id="future")
    path = tmp_path / "nfl.db"
    store.replace_dataset("schedules", 2026, [target], path=path, synced_at=now - timedelta(hours=1))
    store.replace_dataset("pbp", 2026, [play()], path=path, synced_at=now - timedelta(hours=1))
    row = copy.deepcopy(dataset["rows"][0])
    row.update(game_id="future", feature_version=features.FEATURE_VERSION, prediction_cutoff=now.isoformat(), quality={"fraction": .8})
    row["provenance"]["source_observed_max"] = (now - timedelta(hours=1)).isoformat()
    entries = {(r["dataset"], r["season"]): r for r in store.metadata(path=path)}
    name = features.digest(["future", 60, 3])
    payload = {"row": row, "feature_version": features.FEATURE_VERSION, "generated_at": now.isoformat(),
               "lead_minutes": 60, "min_games": 3,
               "source_fingerprints": {d: entries[(d, 2026)]["sha256"] for d in ("schedules", "pbp")}}
    from utils.atomic import atomic_write_json
    atomic_write_json(root / "snapshots" / (name + ".json"), payload)
    # Any raw PBP access in the web inference path is an architectural failure.
    monkeypatch.setattr(features.Vintages, "__init__", lambda *a, **k: pytest.fail("No raw features in request path"))
    result = registry.predict("future", model_id, root=root, path=path, now=now)
    assert result["available"] and result["kind"] == "live_research"
    assert result["home_win_probability"] + result["away_win_probability"] == 1
    assert result["model_enabled_in_production"] is False
    assert list((root / "live_research").glob("*.json"))
    stale = registry.predict("future", model_id, root=root, path=path, now=now + timedelta(hours=2))
    assert not stale["available"] and "snapshot" in stale["reason"]


def test_offline_snapshot_preparation_respects_cutoff_and_source_changes(tmp_path):
    path = tmp_path / "nfl.db"
    seed_history(path, weeks=8)
    now = START + timedelta(weeks=8, days=2)
    target = game(10)
    store.replace_dataset("schedules", 2024, [target], path=path, synced_at=now - timedelta(days=1))
    reports = registry.prepare_snapshots([2024], root=tmp_path / "research", path=path, now=now)
    assert next(r for r in reports if r["game_id"] == target["game_id"])["available"]
    entries = {(r["dataset"], r["season"]): r for r in store.metadata(path=path)}
    assert registry.load_snapshot(target["game_id"], 60, 3, entries, 2024, root=tmp_path / "research", now=now)
    entries[("pbp", 2024)]["sha256"] = "changed"
    assert registry.load_snapshot(target["game_id"], 60, 3, entries, 2024, root=tmp_path / "research", now=now) is None


def test_untracked_old_writer_is_rejected_and_repaired_without_backdating(tmp_path):
    path = tmp_path / "nfl.db"
    seed_history(path, weeks=8)
    stamp = START + timedelta(weeks=9)
    original = store.records("pbp", 2024, path=path)[0]
    revised = dict(original, epa=999)
    key = store.record_key("pbp", 2024, original)
    with store.connection(path) as db, db:
        db.execute("UPDATE records SET payload=?,observed_at=NULL WHERE dataset='pbp' AND season=2024 AND record_key=?",
                   (json.dumps(revised, sort_keys=True, separators=(",", ":")), key))
    assert not store.observation_integrity("pbp", 2024, path=path)["tracked"]
    assert features.FeatureBuilder(2024, path=path).build(game(7))[1] == "untracked_current_source_writer"
    store.replace_dataset("pbp", 2024, [revised], path=path, synced_at=stamp)
    assert store.observation_integrity("pbp", 2024, path=path)["tracked"]
    before = store.records_as_of("pbp", 2024, stamp - timedelta(days=1), path=path)
    assert next(r for r in before if store.record_key("pbp", 2024, r) == key)["epa"] == original["epa"]
    after = store.records_as_of("pbp", 2024, stamp, path=path)
    assert next(r for r in after if store.record_key("pbp", 2024, r) == key)["epa"] == 999
