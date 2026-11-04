"""Isolated research registry, safe JSON loading and prospective-only inference."""
import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from runtime_paths import cache_dir
from services import nfl_analytics as analytics
from services import nflverse_store as store
from services.nfl_winner_features import FEATURE_VERSION, SCHEMA, FeatureBuilder, aware
from utils.atomic import atomic_write_json, file_transaction

ID_PATTERN = re.compile(r"[a-zA-Z0-9_-]{1,160}\Z")
LIMITATIONS = ["Binary probabilities condition on no tie; no tie probability is estimated.",
               "Last observed primary passer is a fallback, not a confirmed starter.",
               "No injury, travel, weather or odds adjustment; no production picks.",
               "Historical receipt coverage limits evaluation; prospective validation remains necessary."]


def root_path():
    return cache_dir("nflverse", "phase2")


def safe_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Research path escapes the registry")
    return path


def registry(*, root=None):
    path = Path(root or root_path()) / "registry.json"
    return json.loads(path.read_text()) if path.exists() else {"version": 1, "models": {}, "runs": {}}


def require_disabled(value):
    if value.get("research_only") is not True or value.get("enabled") is not False or value.get("production_active") is not False:
        raise ValueError("Winner artifacts must remain disabled and research-only")


def save_run(dataset, report, predictions, artifacts, *, root=None):
    from services.nfl_reliability import reject_sealed_games
    reject_sealed_games([r["game_id"] for r in dataset["rows"]] + list(dataset["labels"]))
    root = Path(root or root_path())
    require_disabled(report)
    from services.nfl_winner_features import digest
    run_id = "run_" + digest([report["version"], dataset["dataset_fingerprint"], report["config"], report["sklearn_version"]])[:24]
    destination = root / run_id
    destination.mkdir(parents=True, exist_ok=True)
    # Immutable completed runs are reproduced by ID instead of rewritten with a
    # new generation timestamp. A rerun cannot edit earlier research evidence.
    with file_transaction(root / "registry.json"):
        catalog = registry(root=root)
        if run_id in catalog["runs"]:
            return catalog["runs"][run_id]
        atomic_write_json(destination / "dataset.json", dataset)
        if "availability_manifest" in dataset:
            atomic_write_json(destination / "availability_manifest.json", {
                "dataset_fingerprint": dataset["dataset_fingerprint"], "feature_version": dataset["feature_version"],
                "evidence_class": dataset["evidence_class"], "source_game_catalog": dataset["source_game_catalog"],
                "games": dataset["availability_manifest"]})
        atomic_write_json(destination / "backtest_predictions.json", {"kind": "backtest", "predictions": predictions})
        atomic_write_json(destination / "evaluation.json", report)
        for key, artifact in artifacts.items():
            require_disabled(artifact)
            model_id = run_id + "_" + key
            if not ID_PATTERN.fullmatch(model_id):
                raise ValueError("Invalid model identifier")
            relative = f"{run_id}/{key}.json"
            path = safe_path(root, relative)
            atomic_write_json(path, artifact)
            catalog["models"][model_id] = {
                "model_id": model_id, "model_version": artifact["version"], "model_type": artifact["model"]["kind"],
                "feature_version": artifact["feature_version"], "schema": artifact["schema"],
                "evidence_class": artifact.get("evidence_class", "A"),
                "historically_reconstructed": artifact.get("evidence_class", "A") == "B",
                "dataset_fingerprint": dataset["dataset_fingerprint"],
                "generated_at": artifact["generated_at"], "windows": artifact["windows"],
                "calibration_method": artifact["calibration"]["method"], "artifact": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "run_id": run_id,
                "evaluation": report["models"].get(artifact["model_name"] + ":selected"),
                "research_only": True, "enabled": False, "production_active": False}
        entry = {"run_id": run_id, "status": report["status"], "generated_at": report["generated_at"],
                 "evidence_class": dataset.get("evidence_class", "A"),
                 "dataset_fingerprint": dataset["dataset_fingerprint"], "eligible_games": len(dataset["rows"]),
                 "evaluated_games": report.get("evaluated_games", 0), "report": f"{run_id}/evaluation.json",
                 "dataset": f"{run_id}/dataset.json", "research_only": True, "enabled": False, "production_active": False}
        catalog["runs"][run_id] = entry
        atomic_write_json(root / "registry.json", catalog)
    return entry


def load_artifact(model_id, *, root=None):
    if not ID_PATTERN.fullmatch(model_id):
        raise ValueError("Invalid research model ID")
    root = Path(root or root_path())
    entry = registry(root=root)["models"].get(model_id)
    if entry is None:
        raise ValueError("Unknown research model ID")
    require_disabled(entry)
    if entry.get("evidence_class", "A") != "A":
        raise ValueError("Class B artifacts are retrospective reconstructions; prospective inference requires Class A")
    path = safe_path(root, entry["artifact"])
    if path.suffix != ".json" or path.stat().st_size > 16_000_000:
        raise ValueError("Invalid research artifact")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
        raise ValueError("Research artifact checksum mismatch")
    artifact = json.loads(payload)
    require_disabled(artifact)
    if artifact.get("feature_version") != FEATURE_VERSION or artifact.get("schema") != SCHEMA or artifact.get("dataset_fingerprint") != entry["dataset_fingerprint"]:
        raise ValueError("Incompatible research artifact")
    return entry, artifact


def evaluation(run_id, *, root=None):
    if not ID_PATTERN.fullmatch(run_id):
        raise ValueError("Invalid research run ID")
    root = Path(root or root_path())
    entry = registry(root=root)["runs"].get(run_id)
    if entry is None:
        raise ValueError("Unknown research run")
    require_disabled(entry)
    result = json.loads(safe_path(root, entry["report"]).read_text())
    require_disabled(result)
    from services.nfl_reliability import reject_sealed_games
    reject_sealed_games([g for f in result.get("folds", []) for w in f["windows"].values() for g in w["game_ids"]])
    return result


def prepare_snapshots(seasons, *, path=None, root=None, lead_minutes=60, min_games=3, now=None):
    """Offline compact snapshots keep PBP work out of the Flask request path."""
    now = aware(now or datetime.now(UTC))
    root = Path(root or root_path())
    reports = []
    from services.nfl_holdout import protocol
    reserved = set((protocol() or {}).get("holdout", {}).get("game_ids", []))
    source_meta = {(r["dataset"], r["season"]): r for r in store.metadata(path=path)}
    for season in seasons:
        builder = FeatureBuilder(season, path=path, lead_minutes=lead_minutes, min_games=min_games)
        games = [r for r in store.records("schedules", season, path=path) if r["game_id"] not in reserved
                 if analytics.kickoff(r) is not None and now <= analytics.kickoff(r) - timedelta(minutes=lead_minutes)
                 and analytics.kickoff(r) <= now + timedelta(days=14)]
        for game in games[:100]:
            row, reason = builder.build(game, as_of=now)
            if row is None:
                reports.append({"game_id": game["game_id"], "available": False, "reason": reason})
                continue
            from services.nfl_winner_features import digest
            name = digest([game["game_id"], lead_minutes, min_games])
            payload = {"row": row, "generated_at": now.isoformat(), "feature_version": FEATURE_VERSION,
                       "lead_minutes": lead_minutes, "min_games": min_games,
                       "source_fingerprints": {d: source_meta.get((d, season), {}).get("sha256") for d in ("schedules", "pbp")}}
            atomic_write_json(root / "snapshot_history" / (name + "_" + digest(payload) + ".json"), payload)
            atomic_write_json(root / "snapshots" / (name + ".json"), payload)
            reports.append({"game_id": game["game_id"], "available": True, "snapshot": name})
    return reports


def load_snapshot(game_id, lead, minimum, entries, season, *, root=None, now):
    from services.nfl_winner_features import digest
    name = digest([game_id, lead, minimum])
    path = Path(root or root_path()) / "snapshots" / (name + ".json")
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    maximum = int(os.getenv("NFL_WINNER_SNAPSHOT_MAX_SECONDS", "3600"))
    age = (now - aware(payload["generated_at"])).total_seconds()
    row = payload["row"]
    if (payload["feature_version"] != FEATURE_VERSION or row["feature_version"] != FEATURE_VERSION
            or row["game_id"] != game_id or payload["lead_minutes"] != lead or payload["min_games"] != minimum
            or not 0 <= age <= maximum or aware(row["prediction_cutoff"]) > now
            or aware(row["provenance"]["source_observed_max"]) > aware(row["prediction_cutoff"])):
        return None
    if any(payload["source_fingerprints"][d] != entries.get((d, season), {}).get("sha256") for d in ("schedules", "pbp")):
        return None
    return row


def predict(game_id, model_id, *, path=None, root=None, now=None):
    now = aware(now or datetime.now(UTC))
    entry, artifact = load_artifact(model_id, root=root)
    target = next((r for r in store.records("schedules", path=path) if r["game_id"] == game_id), None)
    if target is None or analytics.kickoff(target) is None:
        raise ValueError("Unknown NFL game or kickoff")
    details = evaluation(entry["run_id"], root=root)
    lead = details.get("prediction_lead_minutes", 60)
    cutoff = analytics.kickoff(target) - timedelta(minutes=lead)
    if now > cutoff:
        raise ValueError("Winner research closes at the configured pregame cutoff; use backtest reports for history")
    if aware(artifact["generated_at"]) > now or aware(artifact["windows"]["calibration_select"]["max_label_usable_after"]) >= now:
        raise ValueError("Model or calibration is unavailable at this prediction time")
    freshness = {}
    entries = {(r["dataset"], r["season"]): r for r in store.metadata(path=path)}
    for name, seconds in (("schedules", 86400), ("pbp", 172800)):
        meta = entries.get((name, int(target["season"])))
        age = (now - aware(meta["synced_at"])).total_seconds() if meta else None
        freshness[name] = {"last_successful_refresh": meta["synced_at"] if meta else None,
                           "fresh": age is not None and 0 <= age <= seconds, "maximum_age_seconds": seconds}
    if not all(r["fresh"] for r in freshness.values()):
        return {"available": False, "research_only": True, "model_enabled_in_production": False,
                "reason": "Research inputs require refresh", "data_freshness": freshness}
    integrity = {d: store.observation_integrity(d, int(target["season"]), path=path) for d in ("schedules", "pbp")}
    if not all(v["tracked"] for v in integrity.values()):
        return {"available": False, "research_only": True, "model_enabled_in_production": False,
                "reason": "Unversioned source changes detected; reload the versioned writer and import a new receipt",
                "observation_integrity": integrity}
    row = load_snapshot(game_id, lead, details.get("feature_config", {}).get("min_games", 3), entries,
                        int(target["season"]), root=root, now=now)
    if row is None:
        return {"available": False, "research_only": True, "model_enabled_in_production": False,
                "reason": "Prepare a fresh offline winner feature snapshot"}
    from services.nfl_winner_evaluation import calibrate_predict, predict_portable
    probability = float(calibrate_predict(artifact["calibration"], predict_portable(artifact["model"], [row]))[0])
    if not 0 <= probability <= 1:
        raise ValueError("Invalid model probability")
    result = {"available": True, "game_id": game_id, "research_only": True, "model_enabled_in_production": False,
              "model_version": model_id, "feature_version": FEATURE_VERSION, "kind": "live_research",
              "prediction_cutoff": now.isoformat(), "scheduled_prediction_cutoff": cutoff.isoformat(),
              "feature_as_of": row["prediction_cutoff"],
              "prediction_generated_at": now.isoformat(), "home_team": row["home_team"], "away_team": row["away_team"],
              "home_win_probability": probability, "away_win_probability": 1 - probability,
              "tie_policy": "conditional_on_no_tie", "data_freshness": freshness,
              "feature_completeness": row["quality"], "limitations": LIMITATIONS,
              "historical_evaluation": {"run_id": entry["run_id"], "metrics": entry["evaluation"]}}
    # Live research is persisted separately from historical evaluation predictions.
    from services.nfl_winner_features import digest
    destination = Path(root or root_path()) / "live_research" / (digest([model_id, game_id, now.isoformat()]) + ".json")
    atomic_write_json(destination, result)
    return result
