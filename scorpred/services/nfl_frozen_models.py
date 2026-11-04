"""Append-only research registration; never loads or replaces production models."""
import base64
import hashlib
import json
import platform
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from importlib.metadata import version as package_version
from pathlib import Path

import numpy as np
import sklearn

from runtime_paths import cache_dir
from services import nflverse_store as store
from services.nfl_pregame_features import (
    ELO_NAMES,
    LOGISTIC_NAMES,
    POLICY,
    SCHEDULE_FIELDS,
    VERSION,
    mapped_values,
)
from services.nfl_reliability import sealed_protocol
from services.nfl_winner_baselines import EloConfig
from services.nfl_winner_evaluation import (
    calibrate_fit,
    calibrate_predict,
    metrics,
    model_specs,
    predict_portable,
    train_classifier,
)
from services.nfl_winner_features import aware, digest
from services.nfl_winner_research import require_disabled

STUDY_ID = "nfl-phase2-3-elo-logistic-v1"
REGISTRATION_DEADLINE = datetime(2026, 11, 26, tzinfo=UTC)
SOURCE_RUN = "run_30fe98722d5901403574f017"
SOURCE_FILES = ("services/nfl_pregame_features.py", "services/nfl_frozen_models.py", "services/nfl_pregame_forecast.py",
                "services/nfl_winner_features.py", "services/nfl_historical_recovery.py", "services/nfl_winner_evaluation.py",
                "services/nfl_winner_baselines.py", "services/nfl_analytics.py", "services/nfl_catalog.py",
                "services/nflverse_provider.py", "services/nflverse_store.py", "services/nfl_reliability.py",
                "services/nfl_winner_research.py", "nfl_client.py", "utils/atomic.py",
                "scripts/nfl_pregame_forecast.py", "scripts/nfl_collector.py", "scripts/sync_nflverse.py",
                "migrations/nflverse/005_forecast_archive.sql")
FLAGS = {"research_only": True, "enabled": False, "production_active": False}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def code_fingerprint():
    root = Path(__file__).resolve().parents[1]
    return digest({name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES})


def source_bundle():
    root = Path(__file__).resolve().parents[1]
    return {name: {"sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
                   "bytes_base64": base64.b64encode((root / name).read_bytes()).decode("ascii")} for name in SOURCE_FILES}


def read_hashed(row):
    if row is None or hashlib.sha256(row["payload"].encode()).hexdigest() != row["sha256"]:
        raise ValueError("Frozen research artifact checksum mismatch")
    value = json.loads(row["payload"])
    require_disabled(value)
    return value


def study(*, path=None, protocol_path=None):
    with store.connection(path) as db:
        row = db.execute("SELECT * FROM nfl_forecast_studies WHERE study_id=?", (STUDY_ID,)).fetchone()
        if row is None:
            return None
        value = read_hashed(row)
        protocol = sealed_protocol(protocol_path)
        from services.nfl_safety_revision import compatible_sources
        if (not protocol or value["holdout_protocol_sha256"] != protocol["protocol_sha256"]
                or not compatible_sources(value, SOURCE_FILES, code_fingerprint())):
            raise ValueError("Frozen study protocol or feature code mismatch; automatic forecasting stopped")
        if (set(value["source_bundle"]) != set(SOURCE_FILES)
                or digest({name: entry["sha256"] for name, entry in value["source_bundle"].items()}) != value["feature_code_sha256"]
                or any(hashlib.sha256(base64.b64decode(entry["bytes_base64"], validate=True)).hexdigest() != entry["sha256"] for entry in value["source_bundle"].values())):
            raise ValueError("Frozen source bundle checksum mismatch")
        return value


def load_models(*, path=None, protocol_path=None):
    plan = study(path=path, protocol_path=protocol_path)
    if plan is None:
        raise ValueError("No frozen pregame study is registered")
    models = {}
    with store.connection(path) as db:
        for kind, entry in plan["models"].items():
            row = db.execute("SELECT * FROM nfl_frozen_models WHERE model_id=?", (entry["model_id"],)).fetchone()
            model = read_hashed(row)
            if (row["sha256"] != entry["artifact_sha256"] or model["feature_mapping_fingerprint"] != plan["feature_mapping_fingerprint"]
                    or model["feature_code_sha256"] != plan["feature_code_sha256"] or model["model_type"] != kind
                    or model["model_id"] != entry["model_id"] or model["libraries"] != libraries()):
                raise ValueError("Frozen model provenance mismatch")
            if model["feature_names"] != list(LOGISTIC_NAMES if kind == "logistic" else ELO_NAMES):
                raise ValueError("Frozen feature order mismatch")
            models[kind] = model
    if set(models) != {"elo", "logistic"}:
        raise ValueError("Both frozen models are required")
    return plan, models


def libraries():
    return {"sklearn": sklearn.__version__, "numpy": np.__version__, "python": platform.python_version(),
            "scipy": package_version("scipy"), "tzdata": package_version("tzdata")}


def reproduce_registered_logistic(model):
    samples = model["development_samples"]
    train = samples["train"]
    if [r["game_id"] for r in train] != model["training_game_ids"] or digest(train) != model["training_data_fingerprint"]:
        raise ValueError("Frozen training sample fingerprint mismatch")
    rows = [r for part in samples.values() for r in part]
    if len({r["game_id"] for r in rows}) != len(rows):
        raise ValueError("Frozen development windows overlap")
    dataset = {"rows": rows, "labels": {r["game_id"]: r["label"] for r in rows}, "supported_feature_names": LOGISTIC_NAMES}
    artifact = {"sklearn_version": model["libraries"]["sklearn"], "evidence_class": "B", "model": model["model"],
                "calibration": model["calibration"], "windows": {k: {"game_ids": [r["game_id"] for r in v]} for k, v in samples.items()}}
    reproduce_logistic(dataset, artifact)
    return {"base_training_games": len(train), "development_games": len(rows), "model_reproduced_exactly": True}


def reproduce_logistic(dataset, artifact):
    """Recreate tuning, preprocessing and calibration from earlier windows only."""
    if artifact["sklearn_version"] != sklearn.__version__ or artifact["evidence_class"] != "B":
        raise ValueError("Original artifact environment or evidence mismatch")
    rows = {r["game_id"]: r for r in dataset["rows"]}
    parts = {key: [rows[g] for g in artifact["windows"][key]["game_ids"]]
             for key in ("train", "tune", "calibration_fit", "calibration_select")}
    kind, names, parameters = model_specs(dataset["supported_feature_names"])["logistic_baseline"]
    if names != LOGISTIC_NAMES:
        raise ValueError("Unsupported original logistic mapping")
    candidates = []
    for parameter in parameters:
        model = train_classifier(parts["train"], dataset["labels"], names, kind, parameter)
        score = metrics([dataset["labels"][r["game_id"]]["home_win"] for r in parts["tune"]], predict_portable(model, parts["tune"]))
        candidates.append((score["log_loss"], parameter, model))
    model = min(candidates, key=lambda c: (c[0], c[1]))[2]
    calibration = {}
    scores = {}
    for method in ("none", "sigmoid"):
        calibration[method] = calibrate_fit(predict_portable(model, parts["calibration_fit"]),
            [dataset["labels"][r["game_id"]]["home_win"] for r in parts["calibration_fit"]], method)
        scores[method] = metrics([dataset["labels"][r["game_id"]]["home_win"] for r in parts["calibration_select"]],
            calibrate_predict(calibration[method], predict_portable(model, parts["calibration_select"])))
    improved = [m for m in scores if scores[m]["log_loss"] < scores["none"]["log_loss"] and scores[m]["brier"] <= scores["none"]["brier"]]
    selected = min(improved, key=lambda m: (scores[m]["log_loss"], m)) if improved else "none"
    if digest(model) != digest(artifact["model"]) or digest(calibration[selected]) != digest(artifact["calibration"]):
        raise ValueError("Original logistic artifact cannot be reproduced exactly; registration refused")
    return parts


def mapping_proof(dataset, *, path=None):
    from services import nfl_analytics as analytics
    from services.nfl_historical_recovery import ReconstructedBuilder
    builders, history, count = {}, [], 0
    for season in sorted({r["season"] for r in dataset["rows"]}):
        builder = ReconstructedBuilder(season, path=path, now=dataset["generated_at"])
        builders[season] = builder
        history.extend({k: v for k, v in g.items() if k in (*SCHEDULE_FIELDS, "home_score", "away_score", "_observed_at")}
                       for key, g in builder.games.items() if key not in builder.blocked)
    for row in dataset["rows"]:
        builder = builders[row["season"]]
        ids = row["provenance"]["source_game_dependencies"]
        eligible = [builder.games[g] for g in ids]
        histories = defaultdict_history(builder.cached_histories)
        played = [g for g in builder.games.values() if g["game_id"] != row["game_id"] and analytics.completed(g, aware(row["prediction_cutoff"]))]
        values, _ = mapped_values(builder.games[row["game_id"]], eligible, histories, played)
        if any((values[n] is None) != (row["features"][n] is None)
               or values[n] is not None and abs(values[n] - row["features"][n]) > 1e-12 for n in LOGISTIC_NAMES):
            raise ValueError("Historical and prospective feature mappings disagree")
        count += 1
    return {"historical_rows_verified": count, "maximum_error_tolerance": 1e-12}, sorted(history, key=lambda g: (g["season"], g["game_id"]))


def defaultdict_history(histories):
    from collections import defaultdict
    result = defaultdict(dict)
    for team, games in histories.items():
        for g in games:
            result[g["game_id"]][team] = g["off"]["yards_per_play"]["value"] is not None
    return result


def register(*, path=None, source=None, protocol_path=None, dry_run=False, _clock=None):
    clock = _clock or (lambda: datetime.now(UTC))
    now = aware(clock())
    existing = study(path=path, protocol_path=protocol_path)
    if existing:
        _, models = load_models(path=path, protocol_path=protocol_path)
        return {"study": existing, "models": models} if dry_run else existing
    protocol = sealed_protocol(protocol_path)
    if (not protocol or len(protocol["holdout"]["game_ids"]) != 109 or now >= REGISTRATION_DEADLINE
            or now >= min(aware(t) - timedelta(minutes=60) for t in protocol["holdout"]["kickoffs_at_registration"].values())):
        raise ValueError("Valid sealed 109-game holdout and registration before the deadline are required")
    source = Path(source or cache_dir("nflverse", "phase2_2") / SOURCE_RUN)
    from scripts.nfl_winner_research import load_replay_dataset
    dataset = load_replay_dataset(source / "dataset.json", "B")
    if (dataset["dataset_fingerprint"] != protocol["development_dataset_fingerprint"]
            or any(r["season"] > 2025 for r in dataset["rows"])
            or set(dataset["labels"]) & set(protocol["holdout"]["game_ids"])):
        raise ValueError("Frozen development population violates holdout protection")
    candidates = list(source.glob("fold*_logistic_baseline.json"))
    if not candidates:
        raise ValueError("No original logistic artifact to reproduce")
    chosen = max(candidates, key=lambda p: int(p.stem.split("_")[0][4:]))
    original = json.loads(chosen.read_text())
    catalog = json.loads((source.parent / "registry.json").read_text())
    original_id = source.name + "_" + chosen.stem
    checksum = hashlib.sha256(chosen.read_bytes()).hexdigest()
    if (catalog["models"][original_id]["sha256"] != checksum
            or original["dataset_fingerprint"] != dataset["dataset_fingerprint"]):
        raise ValueError("Original research artifact checksum mismatch")
    require_disabled(original)
    parts = reproduce_logistic(dataset, original)
    proof, history = mapping_proof(dataset, path=path)
    now = aware(clock())
    if now >= REGISTRATION_DEADLINE:
        raise ValueError("Registration deadline elapsed during validation")
    from services import nfl_analytics as analytics
    from services.nfl_pregame_features import observed_schedules
    scheduled = {g["game_id"]: g for g in observed_schedules(2026, now, protocol["holdout"]["game_ids"], path=path)}
    if any(g not in scheduled or analytics.kickoff(scheduled[g]) is None
           or analytics.kickoff(scheduled[g]) - timedelta(minutes=60) <= now for g in protocol["holdout"]["game_ids"]):
        raise ValueError("Actual observed holdout kickoffs are missing or already at their forecast deadline")
    feature_map = {"version": VERSION, "policy": POLICY, "logistic_names": LOGISTIC_NAMES, "elo_names": ELO_NAMES}
    common = {**FLAGS, "registered_at": now.isoformat(), "feature_version": VERSION,
              "feature_code_sha256": code_fingerprint(), "feature_mapping_fingerprint": digest(feature_map),
              "training_evidence_class": "B", "prospective_input_evidence_class": "A", "libraries": libraries(),
              "serialization": "canonical sorted JSON UTF-8 in immutable SQLite rows; SHA-256 over exact bytes",
              "source_dataset_fingerprint": dataset["dataset_fingerprint"], "source_run_id": source.name,
              "source_artifact_sha256": checksum, "mapping_validation": proof}
    train_ids = [r["game_id"] for r in parts["train"]]
    fit_data = [{"game_id": r["game_id"], "features": {n: r["features"][n] for n in LOGISTIC_NAMES},
                 "label": dataset["labels"][r["game_id"]]} for r in parts["train"]]
    development_samples = {p: [{"game_id": r["game_id"], "features": {n: r["features"][n] for n in LOGISTIC_NAMES},
                                "label": dataset["labels"][r["game_id"]]} for r in rows] for p, rows in parts.items()}
    logistic = {**common, "model_id": "nfl-frozen-logistic-v1", "model_version": "nfl-frozen-logistic-v1", "model_type": "logistic",
                "feature_names": list(LOGISTIC_NAMES), "training_game_ids": train_ids, "training_data_fingerprint": digest(fit_data),
                "training_cutoff": (aware(original["windows"]["tune"]["start"]) - timedelta(microseconds=1)).isoformat(),
                "evidence_cutoff": max(original["windows"][p]["max_label_usable_after"] for p in parts),
                "development_game_ids": {p: [r["game_id"] for r in rows] for p, rows in parts.items()},
                "development_samples": development_samples,
                "model": original["model"], "calibration": original["calibration"],
                "config": {"C": original["model"]["parameter"], "seed": 1729, "max_iter": 3000,
                           "imputation": "training_median_keep_empty_zero", "scaling": "training_StandardScaler",
                           "selection": "latest_chronological_fold_not_test_ranking", "original_artifact": chosen.name}}
    elo = {**common, "model_id": "nfl-frozen-elo-v1", "model_version": "nfl-frozen-elo-v1", "model_type": "elo",
           "feature_names": list(ELO_NAMES), "training_game_ids": [g["game_id"] for g in history],
           "training_data_fingerprint": digest(history), "training_cutoff": max(dataset["source_game_catalog"][g["game_id"]]["available_after_assumed"] for g in history),
           "evidence_cutoff": dataset["generated_at"], "historical_schedule": history, "config": asdict(EloConfig())}
    models = {"elo": elo, "logistic": logistic}
    if any(set(m["training_game_ids"]) & set(protocol["holdout"]["game_ids"]) for m in models.values()):
        raise ValueError("Holdout training is forbidden")
    plan = {**FLAGS, "study_id": STUDY_ID, "registered_at": now.isoformat(), "registration_deadline": REGISTRATION_DEADLINE.isoformat(),
            "feature_code_sha256": common["feature_code_sha256"], "feature_mapping_fingerprint": digest(feature_map),
            "source_bundle": source_bundle(),
            "holdout_kickoffs_verified_at": now.isoformat(),
            "holdout_kickoffs_at_freeze": {g: analytics.kickoff(scheduled[g]).isoformat() for g in protocol["holdout"]["game_ids"]},
            "holdout_protocol_sha256": protocol["protocol_sha256"], "holdout_game_ids": protocol["holdout"]["game_ids"],
            "evaluation_lock": {"sealed": True, "earliest_review_at": protocol["holdout"]["earliest_review_at"], "explicit_authorization_required": True},
            "forecast_window_hours": 72, "minimum_lead_minutes": 60, "policy": POLICY,
            "models": {k: {"model_id": m["model_id"], "artifact_sha256": digest(m)} for k, m in models.items()},
            "forecast_policy": "first eligible cycle between kickoff minus 72 hours and minus 60 minutes; atomic model pair; never replace",
            "holdout_policy": "no reserved outcome in fitting, online Elo history, calibration, evaluation or research responses",
            "evaluation_rules": "Phase 2.2 protocol unchanged; Phase 2.3 archives Elo and logistic only; no holdout evaluation implemented"}
    if dry_run:
        return {"study": plan, "models": models}
    if aware(clock()) >= REGISTRATION_DEADLINE:
        raise ValueError("Registration deadline elapsed during validation")
    with store.connection(path) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM nfl_forecast_studies WHERE study_id=?", (STUDY_ID,)).fetchone():
            raise ValueError("Concurrent registration; verify existing artifacts instead of replacing them")
        for m in models.values():
            db.execute("INSERT INTO nfl_frozen_models VALUES (?,?,?)", (m["model_id"], digest(m), canonical(m)))
        db.execute("INSERT INTO nfl_forecast_studies VALUES (?,?,?)", (STUDY_ID, digest(plan), canonical(plan)))
        if aware(clock()) >= min(REGISTRATION_DEADLINE, min(aware(t) - timedelta(minutes=60) for t in plan["holdout_kickoffs_at_freeze"].values())):
            raise ValueError("Registration deadline elapsed before commit; nothing registered")
    return plan
