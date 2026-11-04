"""Reproducible NFL research baseline. This script does not activate a live model."""
import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import log_loss, mean_absolute_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from runtime_paths import cache_dir, repo_root
from services.nfl_features import FEATURE_NAMES, SCHEMA, build_features
from services.nflverse_store import metadata
from utils.atomic import atomic_write_json


def split_rows(rows):
    rows = sorted([row for row in rows if row["target"] is not None], key=lambda row: row["kickoff"])
    if len(rows) < 250:
        raise ValueError("At least 50 completed games are needed in each chronological window")
    first, second = int(len(rows) * .6), int(len(rows) * .8)
    while first > 0 and rows[first - 1]["date"] == rows[first]["date"]:
        first -= 1
    while second > first and rows[second - 1]["date"] == rows[second]["date"]:
        second -= 1
    parts = rows[:first], rows[first:second], rows[second:]
    if min(map(len, parts)) < 50:
        raise ValueError("At least 50 completed games are needed in each chronological window")
    return parts


def matrix(rows):
    return np.array([[np.nan if row[name] is None else row[name] for name in FEATURE_NAMES] for row in rows], dtype=float)


def metrics(y, probabilities):
    confidence, predicted = probabilities.max(axis=1), probabilities.argmax(axis=1)
    ece = 0.0
    for low in np.arange(0, 1, .1):
        mask = (confidence >= low) & (confidence < low + .1 if low < .9 else confidence <= 1)
        if mask.any():
            ece += mask.mean() * abs((predicted[mask] == y[mask]).mean() - confidence[mask].mean())
    return {"accuracy": float((predicted == y).mean()), "log_loss": float(log_loss(y, probabilities, labels=[0, 1, 2])),
            "brier_mean_three_classes": float(np.square(probabilities - np.eye(3)[y]).mean()), "ece": float(ece),
            "predicted_class_counts": {str(i): int((predicted == i).sum()) for i in range(3)}}


def train(*, database=None, output=None, report_path=None, now=None):
    now = now or datetime.now(UTC)
    rows = build_features(path=database, now=now)
    train_rows, cal_rows, test_rows = split_rows(rows)
    x_train, x_cal, x_test = map(matrix, (train_rows, cal_rows, test_rows))
    y_train, y_cal, y_test = [np.array([row["target"] for row in part]) for part in (train_rows, cal_rows, test_rows)]
    if set(y_train) != {0, 1, 2}:
        raise ValueError("Training data must include home wins, ties and away wins")
    estimator = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(), LogisticRegression(C=.1, max_iter=3000, random_state=42))
    estimator.fit(x_train, y_train)
    model = CalibratedClassifierCV(FrozenEstimator(estimator), method="sigmoid")
    model.fit(x_cal, y_cal)
    predictions = model.predict_proba(x_test)
    if not np.isfinite(predictions).all() or not np.allclose(predictions.sum(axis=1), 1):
        raise ValueError("Invalid model probability output")
    score_model = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(), Ridge(alpha=20))
    score_model.fit(x_train, np.array([[row["home_score"], row["away_score"]] for row in train_rows]))
    target_scores = np.array([[row["home_score"], row["away_score"]] for row in test_rows])
    priors = np.bincount(y_train, minlength=3) / len(y_train)
    baseline_prob = np.tile(priors, (len(y_test), 1))
    evaluation, baseline = metrics(y_test, predictions), metrics(y_test, baseline_prob)
    sources = [row for row in metadata(path=database) if row["dataset"] in {"schedules", "team_stats"}]
    report = {"generated_at": now.isoformat(), "source": "nflverse", "role": "research", "live_use": False,
              "method": "chronological_60_20_20_logistic_sigmoid_and_ridge", "feature_names": FEATURE_NAMES,
              "schema": SCHEMA, "sklearn_version": sklearn.__version__, "sources": sources,
              "dataset_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
              "windows": {name: {"count": len(part), "start": part[0]["date"], "end": part[-1]["date"]}
                          for name, part in zip(("train", "calibration", "test"), (train_rows, cal_rows, test_rows), strict=True)},
              "metrics": evaluation, "training_class_prior_baseline": baseline,
              "feature_missing_fraction": {name: {"train": float(np.isnan(x_train[:, i]).mean()), "test": float(np.isnan(x_test[:, i]).mean())} for i, name in enumerate(FEATURE_NAMES)},
              "majority_baseline_accuracy": float((y_test == priors.argmax()).mean()),
              "projected_points_mae": float(mean_absolute_error(target_scores, score_model.predict(x_test))),
              "training_mean_points_baseline_mae": float(mean_absolute_error(target_scores, np.tile(np.array([[row["home_score"], row["away_score"]] for row in train_rows]).mean(axis=0), (len(test_rows), 1)))),
              "limitations": ["Retrospective corrected statistics, not archived game-day data vintages.",
                              "No injury, depth-chart, starting-quarterback, forecast-weather or bookmaker inputs.",
                              "Ties are rare; inspect predicted class counts before using draw estimates.",
                              "No live accuracy or profitability evaluation. Research artifact is not used by dashboard decisions."]}
    destination = Path(output or cache_dir("nflverse", "research_model.joblib"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    bundle = {"model": model, "score_model": score_model, "feature_names": FEATURE_NAMES, "schema": SCHEMA,
              "sklearn_version": sklearn.__version__, "role": "research", "live_use": False, "report": report}
    try:
        joblib.dump(bundle, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    report["artifact_sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
    atomic_write_json(report_path or repo_root() / "data" / "validation" / "nflverse_model.json", report)
    export = destination.parent / "pregame_features.csv"
    with export.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database")
    parser.add_argument("--output")
    parser.add_argument("--report")
    args = parser.parse_args()
    result = train(database=args.database, output=args.output, report_path=args.report)
    print(json.dumps({key: result[key] for key in ("windows", "metrics", "majority_baseline_accuracy", "projected_points_mae", "live_use")}, indent=2))
