"""Actual-time, paired, immutable NFL research forecasts. No outcome evaluation."""
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from filelock import FileLock

from services import nfl_analytics as analytics
from services import nflverse_store as store
from services.nfl_catalog import canonical_team
from services.nfl_frozen_models import (
    FLAGS,
    STUDY_ID,
    canonical,
    load_models,
    read_hashed,
    study,
)
from services.nfl_pregame_features import (
    LOGISTIC_NAMES,
    VERSION,
    build_inputs,
    observed_schedules,
)
from services.nfl_winner_evaluation import calibrate_predict, predict_portable
from services.nfl_winner_features import aware, digest


def utc_now():
    return datetime.now(UTC)


def probabilities(inputs, plan, models):
    if set(models) != {"elo", "logistic"} or any(digest(m) != plan["models"][kind]["artifact_sha256"] for kind, m in models.items()):
        raise ValueError("model_artifact_checksum_mismatch")
    if (inputs["feature_version"] != VERSION or inputs["feature_names"] != list(LOGISTIC_NAMES)
            or inputs["evidence_class"] != "A" or inputs["training_evidence_class"] != "B"
            or inputs["policy"] != plan["policy"] or inputs["no_holdout_history"] is not True
            or set(inputs["source_game_ids"] + inputs["elo_source_game_ids"]) & set(plan["holdout_game_ids"])):
        raise ValueError("input_feature_provenance_mismatch")
    if aware(inputs["source_observed_max"]) > aware(inputs["evidence_cutoff"]):
        raise ValueError("future_source_observation")
    f = inputs["features"]
    if set(f) != set(LOGISTIC_NAMES) or f["home_field"] not in (0, 1) or f["diff_win_rate"] is None:
        raise ValueError("missing_required_feature")
    if not all(v is None or isinstance(v, (int, float)) and math.isfinite(v) for v in f.values()):
        raise ValueError("invalid_numeric_feature")
    elo = inputs["elo_features"]
    if elo["home_field"] != f["home_field"] or not all(math.isfinite(v) for v in elo.values()):
        raise ValueError("elo_feature_mapping_mismatch")
    model = models["logistic"]
    logistic = float(calibrate_predict(model["calibration"], predict_portable(model["model"], [{"features": f}]))[0])
    adjustment = models["elo"]["config"]["home_adjustment"] * elo["home_field"]
    baseline = 1 / (1 + 10 ** ((elo["away_rating"] - elo["home_rating"] - adjustment) / 400))
    result = {"elo": baseline, "logistic": logistic}
    if not all(math.isfinite(p) and 0 <= p <= 1 for p in result.values()):
        raise ValueError("invalid_probability")
    return result


def audit(kind, *, path=None, game_id=None, payload=None, event_key=None, now=None):
    stamp = aware(now or utc_now()).isoformat()
    payload = payload or {}
    key = event_key or digest([STUDY_ID, game_id, kind, stamp, payload])
    with store.connection(path) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM nfl_forecast_events WHERE event_key=?", (key,)).fetchone():
            db.execute("INSERT INTO nfl_forecast_events(event_key,created_at,study_id,game_id,kind,payload) VALUES (?,?,?,?,?,?)",
                       (key, stamp, STUDY_ID, game_id, kind, canonical(payload)))


def commit(inputs, plan, models, *, path=None, _clock=None):
    clock = _clock or utc_now
    generated = aware(clock())
    deadline = aware(inputs["scheduled_kickoff"]) - timedelta(minutes=plan["minimum_lead_minutes"])
    if not aware(plan["registered_at"]) <= aware(inputs["evidence_cutoff"]) <= generated <= deadline:
        raise ValueError("late_or_backdated_forecast")
    if any(not 0 <= (generated - aware(r["collected_at"])).total_seconds() <= r["maximum_age_seconds"] for r in inputs["freshness"].values()):
        raise ValueError("source_expired_during_forecast")
    values = probabilities(inputs, plan, models)
    fingerprint = digest(inputs)
    evidence_target = next((g for g in observed_schedules(inputs["season"], aware(inputs["evidence_cutoff"]), plan["holdout_game_ids"], path=path, version_ceiling=inputs["source_snapshot_boundary"]["versions"])
                            if g["game_id"] == inputs["game_id"]), None)
    if evidence_target is None or digest(build_inputs(evidence_target, models["elo"], plan["holdout_game_ids"], path=path, cutoff=inputs["evidence_cutoff"], source_ceiling=inputs["source_snapshot_boundary"])) != fingerprint:
        raise ValueError("feature_fingerprint_does_not_match_observed_sources")
    current = next((g for g in observed_schedules(inputs["season"], generated, plan["holdout_game_ids"], path=path) if g["game_id"] == inputs["game_id"]), None)
    if (current is None or analytics.kickoff(current).isoformat() != inputs["scheduled_kickoff"]
            or canonical_team(current["home_team"]) != inputs["home_team"] or canonical_team(current["away_team"]) != inputs["away_team"]):
        raise ValueError("schedule_changed_during_forecast")
    with store.connection(path) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM nfl_forecast_batches WHERE study_id=? AND game_id=?", (STUDY_ID, inputs["game_id"])).fetchone():
            raise ValueError("duplicate_forecast")
        if db.execute("SELECT 1 FROM nfl_forecast_events WHERE study_id=? AND game_id=? AND kind='missed'", (STUDY_ID, inputs["game_id"])).fetchone():
            raise ValueError("game_already_marked_missed")
        archived = aware(clock())
        if not generated <= archived <= deadline:
            raise ValueError("deadline_elapsed_before_archive")
        previous = db.execute("SELECT payload FROM nfl_forecast_inputs WHERE fingerprint=?", (fingerprint,)).fetchone()
        if previous and previous[0] != canonical(inputs):
            raise ValueError("feature_fingerprint_collision")
        if not previous:
            db.execute("INSERT INTO nfl_forecast_inputs VALUES (?,?)", (fingerprint, canonical(inputs)))
        db.execute("INSERT INTO nfl_forecast_batches VALUES (?,?,?,?)", (STUDY_ID, inputs["game_id"], archived.isoformat(), fingerprint))
        for kind, p in values.items():
            model = models[kind]
            forecast = {**FLAGS, "study_id": STUDY_ID, "game_id": inputs["game_id"], "home_team": inputs["home_team"],
                "away_team": inputs["away_team"], "scheduled_kickoff": inputs["scheduled_kickoff"],
                "forecast_created_at": generated.isoformat(), "archived_at": archived.isoformat(), "evidence_cutoff": inputs["evidence_cutoff"],
                "deadline": deadline.isoformat(), "model_id": model["model_id"], "model_version": model["model_version"],
                "artifact_sha256": plan["models"][kind]["artifact_sha256"], "feature_fingerprint": fingerprint,
                "feature_mapping_fingerprint": plan["feature_mapping_fingerprint"], "feature_code_sha256": plan["feature_code_sha256"],
                "home_win_probability": p, "away_win_probability": 1 - p, "forecast_status": "archived",
                "evidence_class": "A", "training_evidence_class": "B", "data_freshness": inputs["freshness"],
                "missing_features": inputs["missing_features"] if kind == "logistic" else [], "tie_policy": "conditional_on_no_tie"}
            db.execute("INSERT INTO nfl_forecasts VALUES (?,?,?,?,?)", (STUDY_ID, inputs["game_id"], model["model_id"], digest(forecast), canonical(forecast)))
        if aware(clock()) > deadline:
            raise ValueError("deadline_elapsed_before_commit")
    return {"game_id": inputs["game_id"], "status": "archived", "forecast_count": 2, "feature_fingerprint": fingerprint}


def upcoming(plan, now, *, path=None):
    now = aware(now)
    games = observed_schedules(2026, now, plan["holdout_game_ids"], path=path)
    with store.connection(path) as db:
        archived = {r[0] for r in db.execute("SELECT game_id FROM nfl_forecast_batches WHERE study_id=?", (STUDY_ID,))}
        missed = {r[0] for r in db.execute("SELECT DISTINCT game_id FROM nfl_forecast_events WHERE study_id=? AND kind='missed'", (STUDY_ID,))}
    rows = []
    for g in games:
        start = analytics.kickoff(g)
        if g["game_type"] not in {"REG", "WC", "DIV", "CON", "SB"} or start is None:
            continue
        deadline = start - timedelta(minutes=plan["minimum_lead_minutes"])
        if deadline < aware(plan["registered_at"]):
            continue  # No retrospective "misses" from before study registration.
        state = ("archived" if g["game_id"] in archived else "missed" if g["game_id"] in missed else
                 "deadline_missed" if now > deadline else "due" if start - timedelta(hours=plan["forecast_window_hours"]) <= now else "scheduled")
        rows.append({"game_id": g["game_id"], "home_team": canonical_team(g["home_team"]), "away_team": canonical_team(g["away_team"]),
                     "scheduled_kickoff": start.isoformat(), "deadline": deadline.isoformat(), "status": state,
                     "reserved_holdout": g["game_id"] in plan["holdout_game_ids"], "_target": g})
    return sorted(rows, key=lambda r: (r["scheduled_kickoff"], r["game_id"]))


def cycle(*, path=None, dry_run=False, protocol_path=None, _clock=None):
    clock = _clock or utc_now
    now = aware(clock())
    if study(path=path, protocol_path=protocol_path) is None:
        return {"status": "not_registered", "archived": 0}
    lock_path = str(Path(path or store.database_path()).resolve()) + ".forecast.lock"
    with FileLock(lock_path, timeout=0):
        plan, models = load_models(path=path, protocol_path=protocol_path)
        rows, results = upcoming(plan, now, path=path), []
        for row in rows:
            game_id = row["game_id"]
            if row["status"] == "deadline_missed":
                if not dry_run:
                    audit("missed", path=path, game_id=game_id, payload={"deadline": row["deadline"], "scheduled_kickoff": row["scheduled_kickoff"]},
                          event_key=digest([STUDY_ID, game_id, "missed"]), now=clock())
                results.append({"game_id": game_id, "status": "missed"})
            elif row["status"] == "due":
                try:
                    inputs = build_inputs(row["_target"], models["elo"], plan["holdout_game_ids"], path=path, cutoff=aware(clock()))
                    values = probabilities(inputs, plan, models)
                    result = {"game_id": game_id, "status": "ready", "probabilities": values} if dry_run else commit(inputs, plan, models, path=path, _clock=clock)
                    results.append(result)
                except (ValueError, KeyError, TypeError, OverflowError) as exc:
                    reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    if not dry_run:
                        audit("rejected", path=path, game_id=game_id, payload={"reason": reason}, now=clock())
                    results.append({"game_id": game_id, "status": "rejected", "reason": reason})
        # Detect schedule corrections without ever replacing a saved forecast.
        if not dry_run:
            with store.connection(path) as db:
                saved = {r["game_id"]: json.loads(r["payload"]) for r in db.execute("SELECT game_id,payload FROM nfl_forecasts WHERE study_id=?", (STUDY_ID,))}
            current_rows = [{"game_id": g["game_id"], "home_team": canonical_team(g["home_team"]), "away_team": canonical_team(g["away_team"]),
                             "scheduled_kickoff": analytics.kickoff(g).isoformat() if analytics.kickoff(g) else None}
                            for g in observed_schedules(2026, aware(clock()), plan["holdout_game_ids"], path=path)]
            for row in current_rows:
                original = saved.get(row["game_id"])
                if original and any(original[k] != row[k] for k in ("scheduled_kickoff", "home_team", "away_team")):
                    audit("schedule_changed", path=path, game_id=row["game_id"], payload={k: row[k] for k in ("scheduled_kickoff", "home_team", "away_team")},
                          event_key=digest([STUDY_ID, row["game_id"], "schedule_changed", row["scheduled_kickoff"], row["home_team"], row["away_team"]]), now=clock())
        result = {"status": "degraded" if any(r["status"] == "rejected" for r in results) else "success", "dry_run": dry_run,
                  "started_at": now.isoformat(), "completed_at": aware(clock()).isoformat(),
                  "eligible_upcoming": sum(r["status"] in {"due", "scheduled"} for r in rows),
                  "due_games": sum(r["status"] == "due" for r in rows), "archived": sum(r["status"] == "archived" for r in results),
                  "missed": sum(r["status"] == "missed" for r in results), "results": results}
        if not dry_run:
            audit("scheduler_cycle", path=path, payload=result, now=clock())
        return result


def replay(*, path=None, protocol_path=None, verify_sources=False):
    plan, models = load_models(path=path, protocol_path=protocol_path)
    if verify_sources:
        from services.nfl_frozen_models import reproduce_registered_logistic
        reproduce_registered_logistic(models["logistic"])
    checked, maximum = 0, 0.0
    with store.connection(path) as db:
        for batch in db.execute("SELECT * FROM nfl_forecast_batches WHERE study_id=?", (STUDY_ID,)):
            raw = db.execute("SELECT payload FROM nfl_forecast_inputs WHERE fingerprint=?", (batch["fingerprint"],)).fetchone()
            if not raw or digest(json.loads(raw[0])) != batch["fingerprint"]:
                raise ValueError("Archived feature fingerprint mismatch")
            inputs = json.loads(raw[0])
            if verify_sources:
                target = next((g for g in observed_schedules(inputs["season"], aware(inputs["evidence_cutoff"]), plan["holdout_game_ids"], path=path, version_ceiling=inputs["source_snapshot_boundary"]["versions"]) if g["game_id"] == inputs["game_id"]), None)
                if target is None or digest(build_inputs(target, models["elo"], plan["holdout_game_ids"], path=path, cutoff=inputs["evidence_cutoff"], source_ceiling=inputs["source_snapshot_boundary"])) != batch["fingerprint"]:
                    raise ValueError("Archived feature fingerprint does not match original observations")
            expected = probabilities(inputs, plan, models)
            saved = db.execute("SELECT * FROM nfl_forecasts WHERE study_id=? AND game_id=?", (STUDY_ID, batch["game_id"])).fetchall()
            if len(saved) != 2 or {r["model_id"] for r in saved} != {m["model_id"] for m in models.values()}:
                raise ValueError("Incomplete archived model pair")
            for r in saved:
                forecast = read_hashed(r)
                kind = next(k for k, m in models.items() if m["model_id"] == r["model_id"])
                if (forecast["artifact_sha256"] != plan["models"][kind]["artifact_sha256"]
                        or forecast["feature_fingerprint"] != batch["fingerprint"]
                        or forecast["feature_mapping_fingerprint"] != plan["feature_mapping_fingerprint"]
                        or forecast["feature_code_sha256"] != plan["feature_code_sha256"]
                        or forecast["home_team"] != inputs["home_team"] or forecast["away_team"] != inputs["away_team"]
                        or forecast["scheduled_kickoff"] != inputs["scheduled_kickoff"]
                        or not aware(inputs["source_observed_max"]) <= aware(forecast["evidence_cutoff"]) <= aware(forecast["forecast_created_at"]) <= aware(forecast["archived_at"]) <= aware(forecast["scheduled_kickoff"]) - timedelta(minutes=60)
                        or abs(forecast["home_win_probability"] + forecast["away_win_probability"] - 1) > 1e-12):
                    raise ValueError("Archived forecast provenance mismatch")
                error = abs(expected[kind] - forecast["home_win_probability"])
                if error > 1e-12:
                    raise ValueError("Saved prediction does not reproduce")
                maximum = max(maximum, error)
                checked += 1
    return {"verified_forecasts": checked, "maximum_absolute_probability_error": maximum, "holdout_outcomes_read": 0}


def records(*, path=None, game_id=None, protocol_path=None):
    load_models(path=path, protocol_path=protocol_path)
    with store.connection(path) as db:
        query, params = "SELECT * FROM nfl_forecasts WHERE study_id=?", [STUDY_ID]
        if game_id:
            query += " AND game_id=?"
            params.append(game_id)
        return [read_hashed(r) for r in db.execute(query + " ORDER BY game_id,model_id", params)]


def status(*, path=None, protocol_path=None, now=None):
    plan = study(path=path, protocol_path=protocol_path)
    if not plan:
        return {**FLAGS, "registered": False, "models": [], "upcoming": [], "evaluation_locked": True}
    plan, models = load_models(path=path, protocol_path=protocol_path)
    now = aware(now or utc_now())
    with store.connection(path) as db:
        archived = db.execute("SELECT COUNT(*) FROM nfl_forecasts WHERE study_id=?", (STUDY_ID,)).fetchone()[0]
        events = {r[0]: r[1] for r in db.execute("SELECT kind,COUNT(*) FROM nfl_forecast_events WHERE study_id=? GROUP BY kind", (STUDY_ID,))}
        latest = db.execute("SELECT created_at,payload FROM nfl_forecast_events WHERE study_id=? AND kind='scheduler_cycle' ORDER BY id DESC LIMIT 1", (STUDY_ID,)).fetchone()
        problems = [{"created_at": r[0], "game_id": r[1], "kind": r[2], "detail": json.loads(r[3])} for r in db.execute(
            "SELECT created_at,game_id,kind,payload FROM nfl_forecast_events WHERE study_id=? AND kind IN ('rejected','missed','schedule_changed') ORDER BY id DESC LIMIT 10", (STUDY_ID,))]
    collector_path = Path(path or store.database_path()).parent / "collector/status.json"
    collector = json.loads(collector_path.read_text()) if collector_path.exists() else {}
    heartbeat = collector.get("heartbeat_at")
    collector_status = {"state": collector.get("state", "unavailable"), "version": collector.get("version"),
                        "pid": collector.get("pid"), "heartbeat_at": heartbeat,
                        "heartbeat_fresh": bool(heartbeat and 0 <= (now - aware(heartbeat)).total_seconds() <= 300),
                        "forecast_scheduler_loaded": collector.get("version") == "nfl-prospective-collector-v2"}
    future = [r for r in upcoming(plan, now, path=path) if aware(r["scheduled_kickoff"]) > now]
    within_window = [r for r in future if aware(r["scheduled_kickoff"]) - timedelta(hours=plan["forecast_window_hours"]) <= now <= aware(r["deadline"])]
    return {**FLAGS, "registered": True, "study_id": STUDY_ID, "collector": collector_status,
            "scheduler_receipt_fresh": bool(latest and 0 <= (now - aware(latest[0])).total_seconds() <= 600),
            "models": [{k: m[k] for k in (
                "model_id", "model_version", "feature_names", "training_cutoff", "evidence_cutoff", "training_evidence_class",
                "prospective_input_evidence_class", "training_data_fingerprint", "feature_mapping_fingerprint", "feature_code_sha256")}
                | {"artifact_sha256": plan["models"][kind]["artifact_sha256"], "training_games": len(m["training_game_ids"])} for kind, m in models.items()],
            "upcoming": [{k: v for k, v in r.items() if k != "_target"} for r in future],
            "eligible_upcoming_games": len(within_window), "due_unarchived_games": sum(r["status"] == "due" for r in future),
            "upcoming_schedule_games": len(future),
            "archived_forecasts": archived, "archived_games": archived // 2, "missed_games": events.get("missed", 0),
            "rejected_attempts": events.get("rejected", 0), "data_quality_problems": problems,
            "last_scheduler_receipt": {"created_at": latest[0], "result": json.loads(latest[1])} if latest else None,
            "evidence_quality": "Class A observed inputs; Class B historical model training; not production validated",
            "evaluation_locked": True, "holdout_games": len(plan["holdout_game_ids"]), "holdout_outcomes_evaluated": 0,
            "earliest_review_at": plan["evaluation_lock"]["earliest_review_at"], "forecast_window_hours": plan["forecast_window_hours"],
            "feature_mapping_fingerprint": plan["feature_mapping_fingerprint"], "feature_code_sha256": plan["feature_code_sha256"]}
