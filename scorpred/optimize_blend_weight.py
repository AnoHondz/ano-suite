"""
optimize_blend_weight.py — Find the best ML blend weight for soccer predictions.

Loads the clean feature dataset and trained model, evaluates every blend weight
in [0.0, 0.1, ..., 1.0] on the chronological test split (last 20%), and saves
the best weight to cache/ml/prediction_policy.json under "soccer_ml_blend_weight".

Usage:
    python optimize_blend_weight.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from ml_service import (
    _rule_prediction_from_features,
    _rule_probabilities,
    _row_to_features,
    load_model,
)
from runtime_paths import clean_soccer_dataset_path, prediction_policy_path
from train_model import CLASS_LABELS, FEATURE_COLUMNS, _target_from_row
from utils.parsing import safe_float

_BLEND_WEIGHTS = [round(w * 0.1, 1) for w in range(11)]   # 0.0, 0.1, …, 1.0


def _load_test_rows() -> tuple[list[list[float]], list[int]]:
    """Return (X_test, y_test) — the last 20% of the clean feature dataset."""
    path = clean_soccer_dataset_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Clean dataset not found: {path}\nRun train_model.py first."
        )
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    X: list[list[float]] = []
    y: list[int] = []
    for row in rows:
        target = _target_from_row(row)
        if target is None:
            continue
        X.append(_row_to_features(row))
        y.append(target)

    n_test = max(1, int(len(X) * 0.20))
    return X[-n_test:], y[-n_test:]


def _blended_prediction(
    rule_probs: list[float],
    ml_probs: list[float],
    w: float,
) -> int:
    """Return argmax of (w * rule + (1-w) * ml) per class."""
    combined = [w * rule_probs[i] + (1.0 - w) * ml_probs[i] for i in range(3)]
    return int(max(range(3), key=lambda i: combined[i]))


def optimize() -> float:
    X_test, y_test = _load_test_rows()
    n = len(X_test)
    if n < 10:
        raise ValueError(f"Only {n} test rows — need ≥ 10 to optimise.")

    bundle = load_model()
    if not bundle:
        raise RuntimeError("Trained model not found. Run train_model.py first.")
    model = bundle["model"]

    # Pre-compute rule and ML probabilities for every test row
    rule_probs_all = [_rule_probabilities(feat) for feat in X_test]
    ml_proba_all   = model.predict_proba(np.array(X_test))

    print(f"\nOptimising blend weight on {n} test rows\n")
    print(f"  {'ML weight (w)':>14s}  {'Rule weight':>12s}  {'Accuracy':>10s}")
    print(f"  {'-'*14}  {'-'*12}  {'-'*10}")

    best_weight  = 0.4
    best_accuracy = -1.0
    results: list[tuple[float, float]] = []

    for w in _BLEND_WEIGHTS:
        rule_w = round(1.0 - w, 1)
        hits = sum(
            1
            for i in range(n)
            if _blended_prediction(
                rule_probs_all[i],
                [float(p) for p in ml_proba_all[i]],
                w,
            ) == y_test[i]
        )
        acc = hits / n
        results.append((w, acc))
        marker = "  ←" if acc > best_accuracy else ""
        if acc > best_accuracy:
            best_accuracy = acc
            best_weight   = w
        print(f"  {w:>14.1f}  {rule_w:>12.1f}  {acc * 100:>9.1f}%{marker}")

    print(f"\nBest ML weight : {best_weight}  (accuracy {best_accuracy * 100:.1f}%)")

    # Save to prediction_policy.json
    policy_path = prediction_policy_path()
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy: dict = {}
    if policy_path.exists():
        try:
            with policy_path.open("r", encoding="utf-8") as fh:
                policy = json.load(fh)
        except Exception:
            policy = {}

    policy["soccer_ml_blend_weight"] = best_weight
    with policy_path.open("w", encoding="utf-8") as fh:
        json.dump(policy, fh, indent=2)
    print(f"Saved soccer_ml_blend_weight={best_weight} → {policy_path}")
    return best_weight


if __name__ == "__main__":
    optimize()
