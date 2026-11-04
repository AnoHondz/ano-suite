"""Replay existing soccer weights and probe pure contracts; never fit or save a model."""
import argparse
import hashlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np

from scripts.audit_scorp import DEFAULT_OUTPUT, ROOT, load, write


def score(y, p):
    predicted = np.argmax(p, axis=1)
    clipped = np.clip(p[np.arange(len(y)), y], 1e-15, 1)
    brier_sum = np.square(p - np.eye(3)[y]).sum(axis=1)
    confidence, correct = np.max(p, axis=1), predicted == y
    buckets, ece = [], 0.0
    for i in range(10):
        mask = (confidence >= i / 10) & (confidence < (i + 1) / 10 if i < 9 else confidence <= 1)
        if mask.any():
            c, a, n = float(confidence[mask].mean()), float(correct[mask].mean()), int(mask.sum())
            buckets.append({"count": n, "confidence": c, "accuracy": a})
            ece += n / len(y) * abs(c - a)
    return {"games": len(y), "accuracy": float(correct.mean()), "log_loss": float(-np.log(clipped).mean()),
            "brier_mean_three_classes": float(brier_sum.mean() / 3), "brier_sum_three_classes": float(brier_sum.mean()),
            "top_label_ece_10_bins": ece, "predicted_class_counts": dict(Counter(map(int, predicted))),
            "actual_class_counts": dict(Counter(map(int, y))), "reliability_bins": buckets,
            "class_recall": {c: float((predicted[y == c] == c).mean()) if (y == c).any() else None for c in range(3)},
            "class_mean_probability": p.mean(axis=0).tolist(),
            "class_ece_10_bins": {c: sum(float(mask.mean()) * abs(float(p[mask, c].mean()) - float((y[mask] == c).mean()))
                                          for i in range(10) if (mask := ((p[:, c] >= i / 10) & (p[:, c] < (i + 1) / 10 if i < 9 else p[:, c] <= 1))).any()) for c in range(3)}}


def soccer(out):
    from production_model import chronological_splits, feature_matrix
    from train_model import FEATURE_COLUMNS, build_clean_features
    report = load(ROOT / "data/validation/production_model.json")
    source = ROOT / "data/historical_matches.csv"
    if hashlib.sha256(source.read_bytes()).hexdigest() != report["dataset_sha256"]:
        raise ValueError("Soccer source no longer matches registered report; replay refused")
    manifest = load(ROOT / "data/models/manifest.json")
    path = ROOT / "data/models/soccer_random_forest_clean.pkl"
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"][path.name]["sha256"]:
        raise ValueError("Soccer production artifact checksum mismatch")
    # This pure builder returns rows and Elo state without writing either.
    rows, _elo = build_clean_features(source)
    train, calibration, test = chronological_splits(rows)
    bundle = joblib.load(path)
    x_train, x_test = feature_matrix(train), feature_matrix(test)
    y_train = np.array([r["target"] for r in train])
    y = np.array([r["target"] for r in test])
    p = bundle["model"].predict_proba(x_test)
    require = np.isfinite(p).all() and np.allclose(p.sum(axis=1), 1)
    if not require:
        raise ValueError("Invalid fitted model probabilities")
    prior = np.bincount(y_train, minlength=3) / len(train)
    baseline = np.tile(prior, (len(test), 1))
    metrics, baseline_metrics = score(y, p), score(y, baseline)
    # A diagnostic perturbation of the existing input vector, not a fitted model:
    # The original audited /prediction snapshot supplied only five form rows,
    # collapsing these ten fields. Later outside-audit source edits address it.
    truncated = [dict(row) for row in test]
    changed_fields = []
    for side in ("home", "away"):
        for name in ("avg_gf", "avg_ga", "ppg"):
            name10, name5 = f"{side}_{name}_10", f"{side}_{name}_5"
            changed_fields.append(name10)
            for row in truncated:
                row[name10] = row[name5]
        for name in ("ppg_delta_5v10", "gf_delta_5v10"):
            key = f"{side}_{name}"
            changed_fields.append(key)
            for row in truncated:
                row[key] = 0.0
    truncated_p = bundle["model"].predict_proba(feature_matrix(truncated))
    truncation = {"fields_affected": changed_fields, "games": len(test),
                  "mean_absolute_class_probability_change": float(np.abs(p - truncated_p).mean()),
                  "maximum_absolute_class_probability_change": float(np.abs(p - truncated_p).max()),
                  "argmax_changed_games": int((p.argmax(axis=1) != truncated_p.argmax(axis=1)).sum()),
                  "metrics_after_isolated_perturbation": score(y, truncated_p),
                  "scope": "Only six long-window inputs and four trend inputs changed. Other route mismatches, if any, are not simulated. This is sensitivity on previously evaluated historical data, not runtime or prospective performance."}
    differences = {key: abs(metrics[key] - report["metrics"][key]) for key in ("accuracy", "log_loss", "brier_mean_three_classes") if key in report["metrics"]}
    differences["brier"] = abs(metrics["brier_mean_three_classes"] - report["metrics"]["brier_score"])
    if max(differences.values()) > 1e-6:
        raise ValueError("Current model replay differs from saved evaluation")
    by_season = {}
    seasons = [str(datetime.fromisoformat(r["date"]).year - (datetime.fromisoformat(r["date"]).month < 8)) for r in test]
    for season in sorted(set(seasons)):
        mask = np.array([s == season for s in seasons])
        by_season[season] = {"model": score(y[mask], p[mask]), "training_prior_baseline": score(y[mask], baseline[mask])}
    week_keys = [datetime.fromisoformat(r["date"]).strftime("%G-W%V") for r in test]
    weeks = sorted(set(week_keys))
    losses = -np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1))
    base_losses = -np.log(prior[y])
    counts = np.array([week_keys.count(w) for w in weeks])
    totals = np.array([float((losses - base_losses)[np.array([k == w for k in week_keys])].sum()) for w in weeks])
    rng = np.random.default_rng(1729)
    draws = rng.integers(0, len(weeks), (2000, len(weeks)))
    bootstrap = totals[draws].sum(axis=1) / counts[draws].sum(axis=1)
    missing = {name: {"rows": len(rows), "raw_missing": sum(r.get(name) is None for r in rows),
                      "zero": sum(r.get(name) == 0 for r in rows), "training_all_missing_after_policy": bool(np.isnan(x_train[:, i]).all()),
                      "unique_nonmissing_training_values": len(set(x_train[:, i][np.isfinite(x_train[:, i])].tolist()))} for i, name in enumerate(FEATURE_COLUMNS)}
    write(out, "soccer_model_replay.json", {"audited_at": datetime.now(UTC), "method": "existing fitted weights; no fit/calibration/training calls",
          "model_sha256": manifest["files"][path.name]["sha256"], "features": list(FEATURE_COLUMNS), "feature_coverage": missing,
          "source_sha256": report["dataset_sha256"], "windows": {k: {"games": len(v), "first": v[0]["date"], "last": v[-1]["date"]} for k, v in (("train", train), ("calibration", calibration), ("test", test))},
          "model": metrics, "training_prior": prior.tolist(), "training_prior_baseline": baseline_metrics, "by_season": by_season,
          "five_row_route_sensitivity": truncation,
          "saved_report_maximum_metric_error": max(differences.values()),
          "exploratory_paired_log_loss_difference": {"estimate": float((losses - base_losses).mean()), "week_clusters": len(weeks), "replicates": 2000,
              "interval_95": np.quantile(bootstrap, [.025, .975]).tolist(), "interpretation": "Post-hoc week-cluster bootstrap on already-used development data, not an untouched prospective confirmation."},
          "per_league_evaluation": "Unavailable: historical input contains no league identifiers. Team names alone do not establish complete league-season coverage.",
          "evidence": "Reconstructed prior-match chronology; immutable source availability receipts absent. Prediction quality of the runtime rule/ML blend is not established by this classifier replay."})
    print("soccer fitted classifier replay", metrics["games"], "games; no retraining", flush=True)


def contracts(out):
    import nba_client
    import nba_live_client as nba
    import scorpred_engine as rules
    from services.prediction_lifecycle import unavailable_reason
    now = datetime.now(UTC)
    expected = now.year + (now.month >= 8)
    sample = {"date": {"start": "2026-10-23T00:00:00Z"}}
    calls = []
    original = nba._latest_completed_team_games
    try:
        def record_call(team_id, **kwargs):
            calls.append({"team_id": team_id, "requested_season": kwargs.get("season")})
            return []
        nba._latest_completed_team_games = record_call
        nba.get_team_recent_form_context("2", historical_lookback=0)
        nba.get_team_recent_form_context("2", season=expected, historical_lookback=0)
    finally:
        nba._latest_completed_team_games = original
    offense = {str(pts): rules._offense_score([{"our_pts": pts, "their_pts": 110, "result": "W"}] * 5, "nba")[0] for pts in (100, 110, 115, 120, 130)}
    injuries = {"empty": rules._squad_score([], "nba"), "one_out": rules._squad_score([{"status": "Out", "position": "PG"}], "nba")}
    player_contracts = {"season_averages": nba.get_player_season_averages("1"), "last_games": nba.get_player_last_n_games("1"),
                        "versus_team": nba.get_player_vs_team("1", "2", "3"), "player_stats": nba.get_player_stats("1"), "game_log": nba.get_player_game_log("1")}
    manifest_path = out / "cache_manifest.json"
    normalization = {"completed_schedule_references": 0, "valid_raw_score_pairs": 0, "raw_pairs_lost_in_normalization": 0, "affected_unique_games": set()}
    if manifest_path.exists():
        for entry in load(manifest_path)["files"]:
            if entry.get("sport") != "nba" or entry.get("kind") != "schedule":
                continue
            for event in load(ROOT / entry["file"]).get("events", []):
                competition = (event.get("competitions") or [{}])[0]
                status = (competition.get("status") or event.get("status") or {}).get("type") or {}
                if not status.get("completed"):
                    continue
                normalization["completed_schedule_references"] += 1
                sides = competition.get("competitors") or []
                raw_scores = [r.get("score") for r in sides]
                raw_scores = [r.get("value", r.get("displayValue")) if isinstance(r, dict) else r for r in raw_scores]
                if len(raw_scores) != 2 or any(r is None for r in raw_scores):
                    continue
                normalization["valid_raw_score_pairs"] += 1
                converted = nba._normalize_event(event)
                if converted and any(converted["scores"][s]["points"] is None for s in ("home", "visitors")):
                    normalization["raw_pairs_lost_in_normalization"] += 1
                    normalization["affected_unique_games"].add(str(event["id"]))
    normalization["affected_unique_games"] = len(normalization["affected_unique_games"])
    normalization["interpretation"] = "Before optional summary enrichment; successful enrichment can recover scores, at the cost of extra requests. This is not a count of final incorrect predictions."
    # Demonstrate the metadata gate only, not a request to a prediction endpoint.
    gate = {"missing_freshness": unavailable_reason("2026-11-01T18:00:00Z", "NS", None, now),
            "explicit_unknown_freshness": unavailable_reason("2026-11-01T18:00:00Z", "NS", {"state": "unknown"}, now),
            "postkickoff": unavailable_reason("2026-01-01T18:00:00Z", "FT", None, now)}
    write(out, "contract_probes.json", {"audited_at": now, "network_requests": 0, "models_fitted": 0,
          "nba_legacy_season": nba_client.NBA_SEASON, "nba_live_default_season": nba.NBA_SEASON, "calendar_derived_current_ending_year": expected,
          "season_from_selected_game": nba._season_from_game_date(sample), "default_versus_explicit_calls": calls,
          "nba_rule_offense_scores": offense, "nba_injury_component": injuries, "player_stat_contracts": player_contracts,
          "nba_schedule_score_normalization": normalization,
          "pregame_gate_probes": gate,
          "limitations": "Synthetic probes establish function behavior only; they do not measure predictive accuracy or demonstrate that every route uses the affected function."})
    print("pure function contracts verified; no provider calls", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", choices=("soccer", "contracts", "all"), default="all")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.section in ("soccer", "all"):
        soccer(args.output)
    if args.section in ("contracts", "all"):
        contracts(args.output)


if __name__ == "__main__":
    main()
