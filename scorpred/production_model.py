"""Small, versioned production pipeline with chronological calibration and holdout evaluation."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json

import joblib
import numpy as np
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, log_loss
from sklearn.pipeline import Pipeline

from runtime_paths import clean_soccer_model_path, historical_dataset_path, repo_root
from train_model import FEATURE_COLUMNS, build_clean_features, build_training_sample_weights
from utils.atomic import atomic_write_json

ZERO_IS_MISSING = [name for name in FEATURE_COLUMNS if name.startswith(("home_implied", "away_implied", "draw_implied", "home_xg", "away_xg", "referee_"))] + ["odds_home_away_ratio", "market_confidence"]


def chronological_splits(rows):
    n = len(rows)
    train_end, cal_end = int(n * 0.6), int(n * 0.8)
    while train_end > 0 and rows[train_end - 1]["date"] == rows[train_end]["date"]:
        train_end -= 1
    while cal_end > train_end and rows[cal_end - 1]["date"] == rows[cal_end]["date"]:
        cal_end -= 1
    if min(train_end, cal_end - train_end, n - cal_end) < 10:
        raise ValueError("Need at least 10 matches in each chronological window")
    return rows[:train_end], rows[train_end:cal_end], rows[cal_end:]


def feature_matrix(rows):
    matrix = np.array([[float(row.get(name) or 0.0) for name in FEATURE_COLUMNS] for row in rows])
    for name in ZERO_IS_MISSING:
        column = FEATURE_COLUMNS.index(name)
        matrix[matrix[:, column] == 0, column] = np.nan
    return matrix


def metrics(target, probabilities):
    predicted = probabilities.argmax(axis=1)
    confidence = probabilities.max(axis=1)
    correct = predicted == target
    buckets = []
    for low in np.arange(0, 1, 0.1):
        mask = (confidence >= low) & (confidence < low + 0.1 if low < 0.9 else confidence <= 1)
        if mask.any():
            buckets.append({"count": int(mask.sum()), "confidence": float(confidence[mask].mean()), "accuracy": float(correct[mask].mean())})
    return {"accuracy": round(float(correct.mean()), 6),
            "brier_score": round(float(np.square(probabilities - np.eye(3)[target]).mean()), 6),
            "log_loss": round(float(log_loss(target, probabilities, labels=[0, 1, 2])), 6),
            "ece": round(sum(b["count"] * abs(b["confidence"] - b["accuracy"]) for b in buckets) / len(target), 6),
            "confidence_buckets": buckets,
            "predicted_class_counts": {str(c): int((predicted == c).sum()) for c in range(3)}}


def train_production_model(historical=None, model_path=None, report_path=None, random_state=42):
    source = Path(historical or historical_dataset_path())
    rows, _ = build_clean_features(source)
    train, calibration, test = chronological_splits(rows)
    x_train, x_cal, x_test = map(feature_matrix, (train, calibration, test))
    y_train, y_cal, y_test = [np.array([int(row["target"]) for row in part]) for part in (train, calibration, test)]
    if any(set(y.tolist()) != {0, 1, 2} for y in (y_train, y_cal, y_test)):
        raise ValueError("All chronological windows must contain all three outcome classes")
    estimator = Pipeline([
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("classifier", RandomForestClassifier(n_estimators=300, max_depth=8, min_samples_leaf=5, n_jobs=1, random_state=random_state)),
    ])
    estimator.fit(x_train, y_train, classifier__sample_weight=build_training_sample_weights(train, y_train))
    calibrated = CalibratedClassifierCV(FrozenEstimator(estimator), method="sigmoid")
    calibrated.fit(x_cal, y_cal)
    probabilities = calibrated.predict_proba(x_test)
    evaluation = metrics(y_test, probabilities)
    majority = int(np.bincount(y_train, minlength=3).argmax())
    generated = datetime.now(timezone.utc).isoformat()
    schema = hashlib.sha256(json.dumps(FEATURE_COLUMNS).encode()).hexdigest()
    report = {"generated_at": generated, "method": "chronological_60_20_20_random_forest_sigmoid",
              "dataset_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "sklearn_version": sklearn.__version__,
              "feature_count": len(FEATURE_COLUMNS), "schema_version": schema,
              "total_rows": len(rows), "random_state": random_state,
              "windows": {name: {"count": len(part), "start": part[0]["date"], "end": part[-1]["date"]}
                          for name, part in (("train", train), ("calibration", calibration), ("test", test))},
              "metrics": evaluation,
              "majority_baseline_accuracy": round(float(accuracy_score(y_test, [majority] * len(test))), 6),
              "unadjusted_forest_metrics": metrics(y_test, estimator.predict_proba(x_test)),
              "profitability": {"evaluated": False, "reason": "Historical bookmaker prices and transaction costs have not been evaluated."}}
    bundle = {"model": calibrated, "model_type": "calibrated_random_forest", "model_role": "production",
              "feature_names": list(FEATURE_COLUMNS), "class_labels": {0:"HomeWin", 1:"Draw", 2:"AwayWin"},
              "feature_fill_values": dict(zip(FEATURE_COLUMNS, estimator.named_steps["imputer"].statistics_.tolist())),
              "zero_is_missing": ZERO_IS_MISSING, "schema_version": schema, "sklearn_version": sklearn.__version__,
              "trained_at": generated, "accuracy": evaluation["accuracy"], "brier_score": evaluation["brier_score"],
              "train_date_range": [train[0]["date"], train[-1]["date"]], "test_date_range": [test[0]["date"], test[-1]["date"]]}
    destination = Path(model_path or clean_soccer_model_path())
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    joblib.dump(bundle, temporary)
    temporary.replace(destination)
    report_destination = Path(report_path or repo_root() / "data" / "validation" / "production_model.json")
    atomic_write_json(report_destination, report)
    return report | {"model_path": str(destination), "report_path": str(report_destination)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--report-path", type=Path)
    parser.add_argument("--historical", type=Path)
    args = parser.parse_args()
    print(json.dumps(train_production_model(args.historical, args.model_path, args.report_path), indent=2))
