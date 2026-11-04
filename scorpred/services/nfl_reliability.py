"""Past-only adaptive evaluation and a sealed prospective final holdout."""
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from services import nfl_analytics as analytics
from services import nflverse_store as store
from services.nfl_winner_features import aware, digest
from utils.atomic import atomic_write_json, file_transaction

STUDY_VERSION = "nfl-reliability-v1"


def sealed_protocol(path=None):
    from runtime_paths import cache_dir
    target = Path(path) if path else cache_dir("nflverse", "phase2_2") / "protocol.json"
    if not target.exists():
        return None
    value = json.loads(target.read_text(encoding="utf-8"))
    if (digest({k: v for k, v in value.items() if k != "protocol_sha256"}) != value.get("protocol_sha256")
            or value.get("holdout", {}).get("sealed") is not True):
        raise ValueError("Sealed NFL holdout protocol failed integrity checks")
    return value


def reject_sealed_games(game_ids):
    protocol = sealed_protocol()
    if protocol and set(game_ids) & set(protocol["holdout"]["game_ids"]):
        raise ValueError("Reserved NFL holdout outcomes remain sealed; no evaluation or fitting allowed")


def adaptive_folds(dataset, config, ordered):
    """Extend sparse development windows backward; never lower sample floors.

    Test outcomes do not choose boundaries. Each test block has >=min_test
    rows, including the final tail if necessary. Failed warmup boundaries move
    one group, not eight, so sparse postseason windows cannot discard months.
    """
    labels, accepted, excluded = dataset["labels"], [], []
    half_groups = (config.calibration_groups + 1) // 2
    half_rows = max(10, (config.min_calibration + 1) // 2)

    def take(before, cutoff, minimum_rows, minimum_groups):
        selected, groups = [], []
        for index in range(before - 1, -1, -1):
            part = ordered[index]
            if any(aware(labels[r["game_id"]]["usable_after"]) >= cutoff for r in part):
                continue
            groups.append(index)
            selected.extend(part)
            classes = {labels[r["game_id"]]["home_win"] for r in selected}
            if len(selected) >= minimum_rows and len(groups) >= minimum_groups and len(classes) == 2:
                return sorted(selected, key=lambda r: (r["prediction_cutoff"], r["game_id"])), min(groups)
        return None, None

    cursor = 0
    while cursor < len(ordered):
        end, count = cursor, 0
        while end < len(ordered) and count < config.min_test:
            count += len(ordered[end])
            end += 1
        if count < config.min_test:
            excluded.append({"boundary": cursor, "reason": "insufficient_final_test_rows", "games": count})
            break
        if sum(len(g) for g in ordered[end:]) < config.min_test:
            end = len(ordered)
        test = sorted([r for g in ordered[cursor:end] for r in g], key=lambda r: (r["prediction_cutoff"], r["game_id"]))
        parts, before, next_cutoff = {"test": test}, cursor, aware(test[0]["prediction_cutoff"])
        failure = None
        for name, size, groups in (("calibration_select", half_rows, half_groups),
                                   ("calibration_fit", half_rows, half_groups),
                                   ("tune", config.min_tune, config.tune_groups)):
            rows, boundary = take(before, next_cutoff, size, groups)
            if rows is None:
                failure = f"insufficient_available_{name}"
                break
            parts[name], before = rows, boundary
            next_cutoff = aware(rows[0]["prediction_cutoff"])
        if failure is None:
            train_groups = [g for g in ordered[:before] if all(aware(labels[r["game_id"]]["usable_after"]) < next_cutoff for r in g)]
            train = sorted([r for g in train_groups for r in g], key=lambda r: (r["prediction_cutoff"], r["game_id"]))
            if len(train) < config.min_train or len(train_groups) < config.train_groups or len({labels[r["game_id"]]["home_win"] for r in train}) < 2:
                failure = "insufficient_available_training"
            else:
                parts["train"] = train
        if failure:
            excluded.append({"boundary": cursor, "reason": failure, "game_ids": [r["game_id"] for r in ordered[cursor]]})
            cursor += 1
        else:
            accepted.append(parts)
            cursor = end
    return accepted, excluded


def register_protocol(path, dataset_fingerprint, *, database=None, now=None):
    """Register future games without reading or reporting their outcomes."""
    now = aware(now or datetime.now(UTC))
    path = Path(path)
    with file_transaction(path):
        if path.exists():
            protocol = json.loads(path.read_text())
            expected = digest({k: v for k, v in protocol.items() if k != "protocol_sha256"})
            if protocol["protocol_sha256"] != expected or protocol["development_dataset_fingerprint"] != dataset_fingerprint:
                raise ValueError("Existing prospective protocol does not match; never overwrite a registered holdout")
            return protocol
        games = store.records("schedules", 2026, path=database)
        selected = [g for g in games if g.get("game_type") == "REG" and 12 <= int(g["week"]) <= 18]
        if not selected or any(analytics.kickoff(g) is None or analytics.kickoff(g) <= now for g in selected):
            raise ValueError("Holdout must consist entirely of unplayed future games")
        if any(g.get("home_score") not in (None, "") or g.get("away_score") not in (None, "") for g in selected):
            raise ValueError("Holdout outcomes already present; cannot call this untouched")
        protocol = {"version": STUDY_VERSION, "registered_at": now.isoformat(),
            "development_dataset_fingerprint": dataset_fingerprint, "development_evidence_class": "B",
            "candidates": ["logistic_baseline", "nonlinear_forest"], "references": ["home_prior", "elo"],
            "primary_metrics": ["log_loss", "brier"], "secondary_metrics": ["accuracy", "roc_auc", "ece"],
            "backtest_design": "adaptive past windows; original minimum groups/samples; >=40 in each calibration half; test blocks >=30",
            "selection_basis": "coverage and predeclared sample requirements, never new test performance",
            "primary_comparison_count": 8, "bootstrap_samples": 5000,
            "holdout": {"season": 2026, "season_type": "REG", "weeks": [12, 13, 14, 15, 16, 17, 18],
                "game_ids": sorted(g["game_id"] for g in selected),
                "kickoffs_at_registration": {g["game_id"]: analytics.kickoff(g).isoformat() for g in selected},
                "minimum_prediction_lead_minutes": 60, "required_feature_evidence_class": "A",
                "earliest_review_at": "2027-03-01T00:00:00+00:00", "sealed": True,
                "forecast_requirement": "freeze candidate artifacts and feature mapping before first holdout cutoff; immutable forecasts saved before cutoff",
                "outcome_access": "no fitting, selection, calibration or evaluation in this study; explicit future review required"},
            "research_only": True, "enabled": False, "production_active": False}
        protocol["protocol_sha256"] = digest(protocol)
        atomic_write_json(path, protocol)
        return protocol


def coverage_audit(dataset, old_report, old_predictions, new_report, new_predictions):
    before = {p["game_id"] for p in old_predictions}
    after = {p["game_id"] for p in new_predictions}
    return {"eligible": len(dataset["rows"]), "original_evaluated": len(before), "expanded_evaluated": len(after),
        "newly_evaluated": len(after - before), "original_not_repeated": sorted(before - after),
        "original_skipped_folds": old_report["skipped_folds"],
        "expanded_skipped_boundaries": new_report["skipped_folds"],
        "remaining_by_season": dict(Counter(str(r["season"]) for r in dataset["rows"] if r["game_id"] not in after)),
        "historical_results_are_development_only": True}
