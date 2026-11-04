"""Replay exported NFL research probabilities using only the Python standard library.

Supply the study checksum from the independently retained registration report.
This verifier never imports ScorPred services, reads results, or executes source
bundles. It does not recreate source features; use the research CLI --validate
against the original observation database for that additional check.
"""
import argparse
import base64
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def utc(value):
    stamp = datetime.fromisoformat(value)
    require(stamp.tzinfo is not None and stamp.utcoffset() == timedelta(0), "Non-UTC timestamp")
    return stamp


def sigmoid(value):
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1 + exp_value)


def verify(directory, expected_study_sha256):
    directory = Path(directory)
    raw = (directory / "study.json").read_bytes()
    require(sha(raw) == expected_study_sha256, "Study checksum mismatch")
    study = json.loads(raw)
    models = {}
    for kind, entry in study["models"].items():
        raw = (directory / (entry["model_id"] + ".json")).read_bytes()
        require(sha(raw) == entry["artifact_sha256"], "Model checksum mismatch")
        models[kind] = json.loads(raw)
    require(set(models) == {"elo", "logistic"}, "Unexpected registered model pair")
    for source in study["source_bundle"].values():
        require(sha(base64.b64decode(source["bytes_base64"], validate=True)) == source["sha256"], "Source bundle checksum mismatch")
    package = json.loads((directory / "forecasts.json").read_bytes())
    require(package["outcomes_included"] is False, "Unexpected outcome export")
    groups, maximum, checked = {}, 0.0, 0
    for forecast in package["forecasts"]:
        game_id = forecast["game_id"]
        # Native game identifiers must not escape the export directory.
        require(all(c.isalnum() or c == "_" for c in game_id), "Invalid game identifier")
        inputs = json.loads((directory / ("inputs_" + game_id + ".json")).read_bytes())
        require(sha(canonical(inputs)) == forecast["feature_fingerprint"], "Input checksum mismatch")
        require(not set(inputs["elo_source_game_ids"]) & set(study["holdout_game_ids"]), "Reserved result in inputs")
        for record in (study, forecast, *models.values()):
            require(record["research_only"] is True and record["enabled"] is False and record["production_active"] is False, "Research flags mismatch")
        require(forecast["study_id"] == study["study_id"], "Study identifier mismatch")
        for key in ("feature_code_sha256", "feature_mapping_fingerprint"):
            require(forecast[key] == study[key], "Feature provenance mismatch")
        require(inputs["evidence_class"] == forecast["evidence_class"] == "A", "Input evidence mismatch")
        require(forecast["training_evidence_class"] == "B", "Training evidence mismatch")
        for key in ("game_id", "home_team", "away_team", "scheduled_kickoff", "evidence_cutoff"):
            require(forecast[key] == inputs[key], "Input/forecast mapping mismatch")
        require(utc(study["registered_at"]) <= utc(forecast["evidence_cutoff"]), "Unregistered forecast")
        require(utc(inputs["source_observed_max"]) <= utc(forecast["evidence_cutoff"]) <= utc(forecast["forecast_created_at"]) <= utc(forecast["archived_at"]) <= utc(forecast["deadline"]), "Timestamp ordering mismatch")
        require(utc(forecast["deadline"]) == utc(forecast["scheduled_kickoff"]) - timedelta(minutes=60), "Deadline mismatch")
        kind = next(k for k, model in models.items() if model["model_id"] == forecast["model_id"])
        require(forecast["artifact_sha256"] == study["models"][kind]["artifact_sha256"], "Forecast artifact mismatch")
        if kind == "elo":
            ratings = inputs["elo_features"]
            delta = ratings["home_rating"] - ratings["away_rating"] + models[kind]["config"]["home_adjustment"] * ratings["home_field"]
            probability = sigmoid(math.log(10) * delta / 400)
        else:
            model = models[kind]["model"]
            require(model["feature_names"] == ["home_field", "diff_win_rate", "diff_rest_days"], "Unexpected feature order")
            values = [model["fill"][i] if inputs["features"][name] is None else inputs["features"][name] for i, name in enumerate(model["feature_names"])]
            z = model["intercept"] + sum((value - model["center"][i]) / model["scale"][i] * model["coef"][i] for i, value in enumerate(values))
            probability = sigmoid(z)
            calibration = models[kind]["calibration"]
            require(calibration["method"] in {"sigmoid", "none"}, "Unsupported calibration")
            if calibration["method"] == "sigmoid":
                clipped = min(max(probability, 1e-9), 1 - 1e-9)
                probability = sigmoid(calibration["coefficient"] * math.log(clipped / (1 - clipped)) + calibration["intercept"])
        home, away = forecast["home_win_probability"], forecast["away_win_probability"]
        require(all(math.isfinite(p) and 0 <= p <= 1 for p in (home, away)), "Invalid probability")
        require(abs(home + away - 1) <= 1e-12, "Probabilities do not complement")
        error = abs(probability - home)
        require(error <= 1e-12, "Independent probability mismatch")
        pair = groups.setdefault(game_id, {})
        require(kind not in pair, "Duplicate model forecast")
        pair[kind] = (forecast["feature_fingerprint"], forecast["forecast_created_at"], forecast["archived_at"])
        maximum, checked = max(maximum, error), checked + 1
    require(checked > 0, "No genuine forecasts to verify")
    for pair in groups.values():
        require(set(pair) == {"elo", "logistic"} and pair["elo"] == pair["logistic"], "Incomplete or mismatched model pair")
    return {"verified_forecasts": checked, "verified_games": len(groups), "maximum_probability_error": maximum,
            "study_sha256": expected_study_sha256, "holdout_outcomes_read": False, "production_active": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="docs/assets/nfl_phase2_3")
    parser.add_argument("--study-sha256", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.directory, args.study_sha256), indent=2))
    except (ValueError, KeyError, OSError, StopIteration, TypeError, OverflowError) as exc:
        parser.exit(1, f"Independent NFL export verification failed: {exc}\n")


if __name__ == "__main__":
    main()
