"""Walk-forward backtesting for time-sliced soccer model and policy evaluation.

Uses expanding train window + fixed-size test folds across the historical
timeline.  Evaluates multiple prediction layers per fold:

  A. Base model performance (LR, RF, XGBoost, LightGBM, stacking ensemble)
  B. Combined signal (ML + rule layer)
  C. Recommendation policy (BET / LEAN / AVOID)
  D. Optional flat-stake profitability simulation

All folds are strictly chronological — no look-ahead leakage.

Usage:
    python walk_forward_backtest.py
    python walk_forward_backtest.py --folds 6 --min-train-pct 0.40 --test-pct 0.10
"""

from __future__ import annotations

import argparse
import collections
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.ensemble import RandomForestClassifier, StackingClassifier
from sklearn.metrics import accuracy_score, brier_score_loss

from runtime_paths import historical_dataset_path, walk_forward_report_path
from train_model import (
    CLASS_LABELS,
    FEATURE_COLUMNS,
    _row_to_features,
    _target_from_row,
    build_logistic_estimator,
    build_training_sample_weights,
    build_clean_features,
    sample_weighting_metadata,
)
from ml_service import (
    _combine_probabilities,
    _rule_prediction_from_features,
    _rule_probabilities,
)
import prediction_policy as pp
import soccer_selector as selector
from utils.parsing import safe_float

try:
    from xgboost import XGBClassifier
    _HAS_XGBOOST = True
except ImportError:
    _HAS_XGBOOST = False

try:
    from lightgbm import LGBMClassifier
    _HAS_LIGHTGBM = True
except ImportError:
    _HAS_LIGHTGBM = False


DEFAULT_REPORT_PATH = walk_forward_report_path()
RECENT_WINDOW_YEARS = 3
SELECTOR_MIN_SAMPLE = 60
SELECTOR_MIN_GAIN = 0.01


def _parse_iso_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _recent_rows(rows: list[dict[str, Any]], *, years: int = RECENT_WINDOW_YEARS) -> tuple[list[dict[str, Any]], list[str]]:
    dated_rows = [(row, _parse_iso_date(row.get("date"))) for row in rows]
    dated_rows = [(row, when) for row, when in dated_rows if when is not None]
    if not dated_rows:
        return [], []

    latest = max(when for _row, when in dated_rows)
    cutoff = latest - timedelta(days=365 * years)
    recent = [row for row, when in dated_rows if when >= cutoff]
    return recent, [cutoff.date().isoformat(), latest.date().isoformat()]


def _resolve_report_path(output_path: Path | None = None) -> Path:
    return Path(output_path) if output_path else walk_forward_report_path()

# ── Fold generation ───────────────────────────────────────────────────────────


def generate_folds(
    n_rows: int,
    *,
    n_folds: int = 5,
    min_train_pct: float = 0.40,
    test_pct: float = 0.10,
    cal_pct: float = 0.05,
) -> list[dict[str, Any]]:
    """Generate expanding-window fold indices.

    Each fold has:
      train: [0, train_end)
      cal:   [train_end, cal_end)  — held-out calibration set
      test:  [cal_end, test_end)

    The train window expands with each fold.  Cal and test sizes are fixed
    fractions of the total dataset.
    """
    test_size = max(1, int(n_rows * test_pct))
    cal_size = max(1, int(n_rows * cal_pct))
    min_train = max(20, int(n_rows * min_train_pct))
    block = cal_size + test_size

    folds: list[dict[str, Any]] = []
    for i in range(n_folds):
        test_end = n_rows - (n_folds - 1 - i) * test_size
        cal_end = test_end - test_size
        train_end = cal_end - cal_size

        if train_end < min_train:
            continue
        if test_end > n_rows:
            continue

        folds.append({
            "fold": len(folds) + 1,
            "train_start": 0,
            "train_end": train_end,
            "cal_start": train_end,
            "cal_end": cal_end,
            "test_start": cal_end,
            "test_end": test_end,
            "train_size": train_end,
            "cal_size": cal_end - train_end,
            "test_size": test_end - cal_end,
        })

    return folds


# ── Model builders ────────────────────────────────────────────────────────────


def _build_base_estimators(random_state: int = 42) -> list[tuple[str, Any]]:
    """Create the same base estimator specs used in train_model.py."""
    estimators: list[tuple[str, Any]] = [
        ("lr", build_logistic_estimator(random_state)),
        ("rf", RandomForestClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=3,
            random_state=random_state,
        )),
    ]
    if _HAS_XGBOOST:
        estimators.append(("xgb", XGBClassifier(
            n_estimators=200, max_depth=6, learning_rate=0.1,
            subsample=0.8, colsample_bytree=0.8,
            use_label_encoder=False, eval_metric="mlogloss",
            random_state=random_state, verbosity=0,
        )))
    if _HAS_LIGHTGBM:
        estimators.append(("lgbm", LGBMClassifier(
            n_estimators=200, max_depth=6, learning_rate=0.1,
            subsample=0.8, colsample_bytree=0.8,
            random_state=random_state, verbose=-1,
        )))
    return estimators


def _build_stacking(estimators: list[tuple[str, Any]], random_state: int = 42) -> StackingClassifier:
    """Build a fresh stacking classifier matching the production architecture."""
    return StackingClassifier(
        estimators=estimators,
        final_estimator=build_logistic_estimator(random_state),
        cv=5,
        stack_method="predict_proba",
        passthrough=False,
    )


# ── Per-fold evaluation ──────────────────────────────────────────────────────


def _class_distribution(y: list[int]) -> dict[str, float]:
    counts = collections.Counter(y)
    n = max(len(y), 1)
    return {CLASS_LABELS.get(c, str(c)): round(counts.get(c, 0) / n * 100.0, 1) for c in sorted(CLASS_LABELS)}


def _brier_score(y_true: list[int], proba: np.ndarray) -> float:
    n_classes = len(CLASS_LABELS)
    return round(float(
        sum(
            brier_score_loss(
                [1 if yi == c else 0 for yi in y_true],
                [p[c] for p in proba],
            )
            for c in range(n_classes)
        ) / n_classes
    ), 4)


def _policy_metrics(
    y_true: list[int],
    predictions: list[int],
    probabilities: list[list[float]],
    policy: dict[str, float],
) -> dict[str, Any]:
    """Evaluate BET / LEAN / AVOID policy on fold predictions.

    Mirrors production ScorMastermind logic:
      confidence = top_prob * 0.82 + gap * 1.30  (simplified — no agreement bonus here)
      play_type assigned by policy thresholds
    """
    min_conf = policy.get("min_confidence_pct", 53.0)
    min_gap = policy.get("min_top_two_gap_pct", 3.0)
    lean_min = policy.get("lean_min_confidence_pct", 51.0)
    bet_min = policy.get("bet_min_confidence_pct", 70.0)
    draw_min_prob = policy.get("draw_min_top_prob_pct", 37.0)

    bets = {"placed": 0, "wins": 0, "losses": 0}
    leans = {"placed": 0, "wins": 0, "losses": 0}
    avoids = {"count": 0, "would_have_won": 0}
    by_class = {0: {"placed": 0, "wins": 0}, 1: {"placed": 0, "wins": 0}, 2: {"placed": 0, "wins": 0}}

    for i, (pred, probs) in enumerate(zip(predictions, probabilities)):
        actual = y_true[i]
        sorted_probs = sorted(probs, reverse=True)
        top_prob_pct = sorted_probs[0] * 100.0
        gap_pct = (sorted_probs[0] - sorted_probs[1]) * 100.0 if len(sorted_probs) > 1 else 0.0

        # Simplified confidence (mirrors core logic without agreement/edge bonuses)
        confidence_pct = min(95.0, max(5.0, top_prob_pct * 0.82 + gap_pct * 1.30))

        is_correct = (pred == actual)

        # Avoid triggers
        avoid = False
        if confidence_pct < min_conf:
            avoid = True
        if gap_pct < min_gap:
            avoid = True
        if pred == 1 and top_prob_pct < draw_min_prob:
            avoid = True

        if avoid:
            avoids["count"] += 1
            if is_correct:
                avoids["would_have_won"] += 1
            continue

        # Play type
        if confidence_pct >= bet_min:
            play_type = "BET"
        elif confidence_pct >= lean_min:
            play_type = "LEAN"
        else:
            play_type = "AVOID"

        if play_type == "AVOID":
            avoids["count"] += 1
            if is_correct:
                avoids["would_have_won"] += 1
            continue

        bucket = bets if play_type == "BET" else leans
        bucket["placed"] += 1
        if is_correct:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1

        by_class[pred]["placed"] += 1
        if is_correct:
            by_class[pred]["wins"] += 1

    total_placed = bets["placed"] + leans["placed"]
    total_wins = bets["wins"] + leans["wins"]
    total_losses = bets["losses"] + leans["losses"]

    return {
        "total_evaluated": len(y_true),
        "total_placed": total_placed,
        "total_avoided": avoids["count"],
        "coverage_pct": round(total_placed / max(len(y_true), 1) * 100.0, 1),
        "hit_rate_pct": round(total_wins / max(total_placed, 1) * 100.0, 1),
        "bets": bets,
        "leans": leans,
        "avoids": avoids,
        "by_class": {CLASS_LABELS.get(c, str(c)): v for c, v in by_class.items()},
    }


def _flat_stake_roi(
    y_true: list[int],
    predictions: list[int],
    probabilities: list[list[float]],
    policy: dict[str, float],
    stake: float = 1.0,
) -> dict[str, Any]:
    """Simple flat-stake profitability simulation.

    Uses a fixed fair-odds assumption: payout = 1/model_prob for a correct pick.
    This simulates ROI under the assumption that the model's probability IS the
    true probability, which is optimistic but consistent across folds.
    """
    min_conf = policy.get("min_confidence_pct", 53.0)
    min_gap = policy.get("min_top_two_gap_pct", 3.0)
    lean_min = policy.get("lean_min_confidence_pct", 51.0)
    draw_min_prob = policy.get("draw_min_top_prob_pct", 37.0)

    total_staked = 0.0
    total_returned = 0.0
    points = 0.0
    bets_placed = 0

    for i, (pred, probs) in enumerate(zip(predictions, probabilities)):
        sorted_probs = sorted(probs, reverse=True)
        top_prob_pct = sorted_probs[0] * 100.0
        gap_pct = (sorted_probs[0] - sorted_probs[1]) * 100.0 if len(sorted_probs) > 1 else 0.0
        confidence_pct = min(95.0, max(5.0, top_prob_pct * 0.82 + gap_pct * 1.30))

        if confidence_pct < lean_min or gap_pct < min_gap:
            continue
        if pred == 1 and top_prob_pct < draw_min_prob:
            continue

        actual = y_true[i]
        model_prob = max(probs[pred], 0.10)

        total_staked += stake
        bets_placed += 1
        if pred == actual:
            payout = stake / model_prob
            total_returned += payout
            points += 1.0
        else:
            points -= 1.0

    roi_pct = ((total_returned - total_staked) / max(total_staked, 1e-9)) * 100.0

    return {
        "bets_placed": bets_placed,
        "total_staked": round(total_staked, 2),
        "total_returned": round(total_returned, 2),
        "net_profit": round(total_returned - total_staked, 2),
        "roi_pct": round(roi_pct, 1),
        "flat_points": round(points, 1),
    }


def _selector_bucket_summary(bucket: dict[str, Any]) -> dict[str, Any]:
    count = int(bucket.get("count", 0) or 0)
    hits = bucket.get("hits") or {}
    return {
        "count": count,
        "rule_accuracy": round(float(hits.get("rule", 0)) / max(count, 1), 4) if count else None,
        "ml_accuracy": round(float(hits.get("ml", 0)) / max(count, 1), 4) if count else None,
        "combined_accuracy": round(float(hits.get("combined", 0)) / max(count, 1), 4) if count else None,
        "hits": {
            "rule": int(hits.get("rule", 0) or 0),
            "ml": int(hits.get("ml", 0) or 0),
            "combined": int(hits.get("combined", 0) or 0),
        },
    }


def _selector_metrics(
    y_true: list[int],
    rule_preds: list[int],
    ml_preds: list[int],
    combined_preds: list[int],
    rule_probs: list[list[float]],
    ml_probs: list[list[float]],
    combined_probs: list[list[float]],
) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {
        "overall": {"count": 0, "hits": {"rule": 0, "ml": 0, "combined": 0}},
    }
    for key in selector.SEGMENT_PRIORITY:
        buckets[key] = {"count": 0, "hits": {"rule": 0, "ml": 0, "combined": 0}}

    for idx, actual in enumerate(y_true):
        bucket_names = ["overall"]
        flags = selector.build_segment_flags(rule_probs[idx], ml_probs[idx], combined_probs[idx])
        bucket_names.extend(selector.matched_segments(flags))
        for key in bucket_names:
            bucket = buckets[key]
            bucket["count"] += 1
            if int(rule_preds[idx]) == actual:
                bucket["hits"]["rule"] += 1
            if int(ml_preds[idx]) == actual:
                bucket["hits"]["ml"] += 1
            if int(combined_preds[idx]) == actual:
                bucket["hits"]["combined"] += 1

    return {
        "segments": {
            key: _selector_bucket_summary(value)
            for key, value in buckets.items()
            if int(value.get("count", 0) or 0) > 0
        }
    }


CONFIDENCE_BUCKET_ORDER = ["under_50", "50_59", "60_69", "70_plus"]
CONFIDENCE_BUCKET_LABELS = {
    "under_50": "Under 50%",
    "50_59": "50-59%",
    "60_69": "60-69%",
    "70_plus": "70%+",
}


def _finalize_breakdown_rows(rows: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key, row in rows.items():
        count = int(row.get("count", 0) or 0)
        wins = int(row.get("wins", 0) or 0)
        losses = int(row.get("losses", 0) or 0)
        result[key] = {
            "label": row.get("label") or key,
            "count": count,
            "wins": wins,
            "losses": losses,
            "accuracy": round(wins / max(count, 1), 4) if count else None,
        }
    return result


def _predicted_outcome_breakdown(
    y_true: list[int],
    predictions: list[int],
) -> dict[str, dict[str, Any]]:
    rows = {
        label: {"label": label, "count": 0, "wins": 0, "losses": 0}
        for label in CLASS_LABELS.values()
    }
    for actual, pred in zip(y_true, predictions):
        label = CLASS_LABELS.get(int(pred), str(pred))
        bucket = rows.setdefault(label, {"label": label, "count": 0, "wins": 0, "losses": 0})
        bucket["count"] += 1
        if int(pred) == int(actual):
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
    return _finalize_breakdown_rows(rows)


def _confidence_bucket_key(top_prob_pct: float) -> str:
    if top_prob_pct < 50.0:
        return "under_50"
    if top_prob_pct < 60.0:
        return "50_59"
    if top_prob_pct < 70.0:
        return "60_69"
    return "70_plus"


def _confidence_bucket_breakdown(
    y_true: list[int],
    predictions: list[int],
    probabilities: list[list[float]],
) -> dict[str, dict[str, Any]]:
    rows = {
        key: {"label": label, "count": 0, "wins": 0, "losses": 0}
        for key, label in CONFIDENCE_BUCKET_LABELS.items()
    }
    for actual, pred, probs in zip(y_true, predictions, probabilities):
        top_prob_pct = max(float(value) for value in probs) * 100.0 if probs else 0.0
        key = _confidence_bucket_key(top_prob_pct)
        bucket = rows[key]
        bucket["count"] += 1
        if int(pred) == int(actual):
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
    return _finalize_breakdown_rows(rows)


def _aggregate_breakdown_rows(
    fold_results: list[dict[str, Any]],
    breakdown_key: str,
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for fold in fold_results:
        combined = fold.get("combined") or {}
        for key, metrics in ((combined.get(breakdown_key) or {}).items()):
            row = rows.setdefault(
                key,
                {
                    "label": metrics.get("label") or key,
                    "count": 0,
                    "wins": 0,
                    "losses": 0,
                },
            )
            row["count"] += int(metrics.get("count", 0) or 0)
            row["wins"] += int(metrics.get("wins", 0) or 0)
            row["losses"] += int(metrics.get("losses", 0) or 0)
    return _finalize_breakdown_rows(rows)


def evaluate_fold(
    rows: list[dict[str, Any]],
    fold: dict[str, Any],
    policy: dict[str, float],
    random_state: int = 42,
) -> dict[str, Any]:
    """Train models and evaluate all prediction layers on one fold."""
    train_rows = rows[fold["train_start"]:fold["train_end"]]
    cal_rows = rows[fold["cal_start"]:fold["cal_end"]]
    test_rows = rows[fold["test_start"]:fold["test_end"]]

    x_train = np.array([_row_to_features(r) for r in train_rows])
    y_train = np.array([int(r["target"]) for r in train_rows])
    x_cal = np.array([_row_to_features(r) for r in cal_rows])
    y_cal = np.array([int(r["target"]) for r in cal_rows])
    x_test = np.array([_row_to_features(r) for r in test_rows])
    y_test = [int(r["target"]) for r in test_rows]

    # Date range for this fold
    train_dates = [r.get("date", "") for r in train_rows if r.get("date")]
    test_dates = [r.get("date", "") for r in test_rows if r.get("date")]

    fold_meta = {
        **fold,
        "train_date_range": [train_dates[0], train_dates[-1]] if train_dates else [],
        "test_date_range": [test_dates[0], test_dates[-1]] if test_dates else [],
    }

    sample_weight_train = build_training_sample_weights(train_rows, y_train)

    # ── A. Base models ────────────────────────────────────────────────────────
    estimators = _build_base_estimators(random_state)
    base_results: dict[str, dict[str, Any]] = {}

    for name, est in estimators:
        est.fit(x_train, y_train, sample_weight=sample_weight_train)
        preds = est.predict(x_test).tolist()
        proba = est.predict_proba(x_test)
        acc = float(accuracy_score(y_test, preds))
        brier = _brier_score(y_test, proba)
        base_results[name] = {
            "accuracy": round(acc, 4),
            "brier_score": brier,
            "class_distribution": _class_distribution(preds),
            "draw_rate_pct": round(sum(1 for p in preds if p == 1) / max(len(preds), 1) * 100.0, 1),
        }

    # ── Stacking ensemble (calibrated) ────────────────────────────────────────
    stack_estimators = _build_base_estimators(random_state)
    stacking = _build_stacking(stack_estimators, random_state)
    stacking.fit(x_train, y_train, sample_weight=sample_weight_train)
    calibrated = CalibratedClassifierCV(FrozenEstimator(stacking), method="sigmoid")
    calibrated.fit(x_cal, y_cal)

    ensemble_preds = calibrated.predict(x_test).tolist()
    ensemble_proba = calibrated.predict_proba(x_test)
    ensemble_acc = float(accuracy_score(y_test, ensemble_preds))
    ensemble_brier = _brier_score(y_test, ensemble_proba)

    base_results["stacking_ensemble"] = {
        "accuracy": round(ensemble_acc, 4),
        "brier_score": ensemble_brier,
        "class_distribution": _class_distribution(ensemble_preds),
        "draw_rate_pct": round(sum(1 for p in ensemble_preds if p == 1) / max(len(ensemble_preds), 1) * 100.0, 1),
    }

    # ── B. Combined signal (ML + rule layer) ──────────────────────────────────
    x_test_list = x_test.tolist()
    rule_preds = [_rule_prediction_from_features(f) for f in x_test_list]
    rule_probs = [_rule_probabilities(f) for f in x_test_list]
    ensemble_proba_list = ensemble_proba.tolist()

    combined_probs: list[list[float]] = []
    combined_preds: list[int] = []
    for idx, ml_probs in enumerate(ensemble_proba_list):
        blended = _combine_probabilities(rule_probs[idx], ml_probs)
        combined_probs.append(blended)
        combined_preds.append(int(max(range(3), key=lambda c: blended[c])))

    combined_acc = float(accuracy_score(y_test, combined_preds))

    rule_acc = float(accuracy_score(y_test, rule_preds))

    combined_metrics = {
        "rule_accuracy": round(rule_acc, 4),
        "ml_accuracy": round(ensemble_acc, 4),
        "combined_accuracy": round(combined_acc, 4),
        "combined_draw_rate_pct": round(
            sum(1 for p in combined_preds if p == 1) / max(len(combined_preds), 1) * 100.0, 1
        ),
        "avg_confidence_pct": round(
            sum(max(p) for p in combined_probs) / max(len(combined_probs), 1) * 100.0, 1
        ),
        "by_predicted_outcome": _predicted_outcome_breakdown(y_test, combined_preds),
        "by_confidence_bucket": _confidence_bucket_breakdown(y_test, combined_preds, combined_probs),
    }

    # ── C. Recommendation policy ──────────────────────────────────────────────
    policy_result = _policy_metrics(y_test, combined_preds, combined_probs, policy)

    # ── D. Flat-stake ROI simulation ──────────────────────────────────────────
    roi_result = _flat_stake_roi(y_true=y_test, predictions=combined_preds,
                                 probabilities=combined_probs, policy=policy)
    selector_result = _selector_metrics(
        y_true=y_test,
        rule_preds=rule_preds,
        ml_preds=ensemble_preds,
        combined_preds=combined_preds,
        rule_probs=rule_probs,
        ml_probs=ensemble_proba_list,
        combined_probs=combined_probs,
    )

    return {
        "fold_meta": fold_meta,
        "base_models": base_results,
        "combined": combined_metrics,
        "policy": policy_result,
        "roi": roi_result,
        "selector": selector_result,
        "test_class_distribution": _class_distribution(y_test),
    }


# ── Aggregate metrics ─────────────────────────────────────────────────────────


def _aggregate_folds(fold_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate metrics across all folds."""
    if not fold_results:
        return {}

    n_folds = len(fold_results)

    # ── Base model aggregation ────────────────────────────────────────────────
    model_names = list(fold_results[0].get("base_models", {}).keys())
    model_agg: dict[str, dict[str, Any]] = {}
    for name in model_names:
        accs = [f["base_models"][name]["accuracy"] for f in fold_results if name in f.get("base_models", {})]
        briers = [f["base_models"][name]["brier_score"] for f in fold_results if name in f.get("base_models", {})]
        model_agg[name] = {
            "mean_accuracy": round(float(np.mean(accs)), 4) if accs else None,
            "std_accuracy": round(float(np.std(accs)), 4) if accs else None,
            "min_accuracy": round(float(np.min(accs)), 4) if accs else None,
            "max_accuracy": round(float(np.max(accs)), 4) if accs else None,
            "mean_brier": round(float(np.mean(briers)), 4) if briers else None,
            "folds_evaluated": len(accs),
        }

    # ── Combined aggregation ──────────────────────────────────────────────────
    combined_accs = [f["combined"]["combined_accuracy"] for f in fold_results]
    rule_accs = [f["combined"]["rule_accuracy"] for f in fold_results]
    ml_accs = [f["combined"]["ml_accuracy"] for f in fold_results]

    combined_agg = {
        "mean_combined_accuracy": round(float(np.mean(combined_accs)), 4),
        "std_combined_accuracy": round(float(np.std(combined_accs)), 4),
        "mean_rule_accuracy": round(float(np.mean(rule_accs)), 4),
        "mean_ml_accuracy": round(float(np.mean(ml_accs)), 4),
        "mean_avg_confidence_pct": round(
            float(np.mean([f["combined"]["avg_confidence_pct"] for f in fold_results])), 1
        ),
        "by_predicted_outcome": _aggregate_breakdown_rows(fold_results, "by_predicted_outcome"),
        "by_confidence_bucket": _aggregate_breakdown_rows(fold_results, "by_confidence_bucket"),
    }

    # ── Policy aggregation ────────────────────────────────────────────────────
    total_placed = sum(f["policy"]["total_placed"] for f in fold_results)
    total_wins = sum(f["policy"]["bets"]["wins"] + f["policy"]["leans"]["wins"] for f in fold_results)
    total_losses = sum(f["policy"]["bets"]["losses"] + f["policy"]["leans"]["losses"] for f in fold_results)
    total_avoided = sum(f["policy"]["total_avoided"] for f in fold_results)
    total_evaluated = sum(f["policy"]["total_evaluated"] for f in fold_results)

    hit_rates = [f["policy"]["hit_rate_pct"] for f in fold_results if f["policy"]["total_placed"] > 0]
    coverage_rates = [f["policy"]["coverage_pct"] for f in fold_results]

    policy_agg = {
        "total_placed": total_placed,
        "total_wins": total_wins,
        "total_losses": total_losses,
        "total_avoided": total_avoided,
        "total_evaluated": total_evaluated,
        "aggregate_hit_rate_pct": round(total_wins / max(total_placed, 1) * 100.0, 1),
        "aggregate_coverage_pct": round(total_placed / max(total_evaluated, 1) * 100.0, 1),
        "mean_hit_rate_pct": round(float(np.mean(hit_rates)), 1) if hit_rates else 0.0,
        "std_hit_rate_pct": round(float(np.std(hit_rates)), 1) if hit_rates else 0.0,
        "mean_coverage_pct": round(float(np.mean(coverage_rates)), 1),
    }

    # ── ROI aggregation ──────────────────────────────────────────────────────
    total_staked = sum(f["roi"]["total_staked"] for f in fold_results)
    total_returned = sum(f["roi"]["total_returned"] for f in fold_results)
    total_points = sum(f["roi"]["flat_points"] for f in fold_results)

    roi_agg = {
        "total_bets": sum(f["roi"]["bets_placed"] for f in fold_results),
        "total_staked": round(total_staked, 2),
        "total_returned": round(total_returned, 2),
        "net_profit": round(total_returned - total_staked, 2),
        "aggregate_roi_pct": round((total_returned - total_staked) / max(total_staked, 1e-9) * 100.0, 1),
        "total_flat_points": round(total_points, 1),
    }

    # ── Best / worst fold ─────────────────────────────────────────────────────
    best_fold_idx = int(np.argmax(combined_accs))
    worst_fold_idx = int(np.argmin(combined_accs))

    # ── Trend: is performance improving over time? ────────────────────────────
    if len(combined_accs) >= 3:
        first_half = combined_accs[:len(combined_accs) // 2]
        second_half = combined_accs[len(combined_accs) // 2:]
        trend_delta = float(np.mean(second_half)) - float(np.mean(first_half))
        if trend_delta > 0.02:
            trend = "improving"
        elif trend_delta < -0.02:
            trend = "declining"
        else:
            trend = "stable"
    else:
        trend_delta = 0.0
        trend = "insufficient_data"

    return {
        "n_folds": n_folds,
        "total_test_matches": sum(f["fold_meta"]["test_size"] for f in fold_results),
        "base_models": model_agg,
        "combined": combined_agg,
        "policy": policy_agg,
        "roi": roi_agg,
        "best_fold": {
            "fold": fold_results[best_fold_idx]["fold_meta"]["fold"],
            "combined_accuracy": combined_accs[best_fold_idx],
            "date_range": fold_results[best_fold_idx]["fold_meta"].get("test_date_range", []),
        },
        "worst_fold": {
            "fold": fold_results[worst_fold_idx]["fold_meta"]["fold"],
            "combined_accuracy": combined_accs[worst_fold_idx],
            "date_range": fold_results[worst_fold_idx]["fold_meta"].get("test_date_range", []),
        },
        "trend": trend,
        "trend_delta": round(trend_delta, 4),
    }


def _best_selector_source(metrics: dict[str, Any] | None) -> str:
    payload = metrics or {}
    candidates = []
    for source in ("combined", "ml", "rule"):
        accuracy = payload.get(f"{source}_accuracy")
        if isinstance(accuracy, (int, float)):
            candidates.append((float(accuracy), 1 if source == "combined" else 0, source))
    if not candidates:
        return "combined"
    candidates.sort(reverse=True)
    return candidates[0][2]


def _aggregate_selector(fold_results: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    for fold in fold_results:
        segments = ((fold.get("selector") or {}).get("segments") or {})
        for key, metrics in segments.items():
            bucket = buckets.setdefault(
                key,
                {"count": 0, "hits": {"rule": 0, "ml": 0, "combined": 0}},
            )
            bucket["count"] += int(metrics.get("count", 0) or 0)
            fold_hits = metrics.get("hits") or {}
            for source in ("rule", "ml", "combined"):
                bucket["hits"][source] += int(fold_hits.get(source, 0) or 0)

    summaries = {
        key: _selector_bucket_summary(bucket)
        for key, bucket in buckets.items()
        if int(bucket.get("count", 0) or 0) > 0
    }
    overall = summaries.get("overall") or {"count": 0}
    default_source = _best_selector_source(overall)

    overrides: list[dict[str, Any]] = []
    for key in selector.SEGMENT_PRIORITY:
        metrics = summaries.get(key)
        if not metrics:
            continue
        sample_size = int(metrics.get("count", 0) or 0)
        if sample_size < SELECTOR_MIN_SAMPLE:
            continue
        preferred_source = _best_selector_source(metrics)
        default_accuracy = metrics.get(f"{default_source}_accuracy")
        preferred_accuracy = metrics.get(f"{preferred_source}_accuracy")
        if (
            preferred_source == default_source
            or not isinstance(default_accuracy, (int, float))
            or not isinstance(preferred_accuracy, (int, float))
        ):
            continue
        gain = float(preferred_accuracy) - float(default_accuracy)
        if gain < SELECTOR_MIN_GAIN:
            continue
        overrides.append(
            {
                "segment": key,
                "preferred_source": preferred_source,
                "default_source": default_source,
                "sample_size": sample_size,
                "preferred_accuracy": round(float(preferred_accuracy), 4),
                "default_accuracy": round(float(default_accuracy), 4),
                "gain_vs_default": round(gain, 4),
                "reason": selector.SEGMENT_METADATA.get(key, {}).get("description"),
            }
        )

    overrides.sort(key=lambda row: (row["gain_vs_default"], row["sample_size"]), reverse=True)

    return {
        "default_source": default_source,
        "default_accuracy": overall.get(f"{default_source}_accuracy"),
        "segments": summaries,
        "overrides": overrides,
        "summary": (
            f"Default to {selector.source_label(default_source)} using recent backtests, "
            f"with {len(overrides)} segment override(s) when a different source is materially better."
        ),
        "min_sample_size": SELECTOR_MIN_SAMPLE,
        "min_gain": SELECTOR_MIN_GAIN,
    }


def _run_walk_forward_window(
    rows: list[dict[str, Any]],
    *,
    label: str,
    n_folds: int,
    min_train_pct: float,
    test_pct: float,
    cal_pct: float,
    random_state: int,
    policy: dict[str, float],
) -> dict[str, Any]:
    n_rows = len(rows)
    if n_rows < 40:
        return {
            "available": False,
            "label": label,
            "message": f"Need at least 40 rows for walk-forward, got {n_rows}.",
        }

    folds = generate_folds(
        n_rows,
        n_folds=n_folds,
        min_train_pct=min_train_pct,
        test_pct=test_pct,
        cal_pct=cal_pct,
    )
    if not folds:
        return {
            "available": False,
            "label": label,
            "message": (
                f"Could not generate valid folds with {n_rows} rows "
                f"(min_train_pct={min_train_pct}, test_pct={test_pct})."
            ),
        }

    print(f"Generated {len(folds)} walk-forward folds for {label}")

    fold_results: list[dict[str, Any]] = []
    for fold_def in folds:
        fold_num = fold_def["fold"]
        print(f"\n-- {label}: Fold {fold_num}/{len(folds)} --")
        print(f"  Train: rows 0-{fold_def['train_end']-1} ({fold_def['train_size']} rows)")
        print(f"  Cal:   rows {fold_def['cal_start']}-{fold_def['cal_end']-1} ({fold_def['cal_size']} rows)")
        print(f"  Test:  rows {fold_def['test_start']}-{fold_def['test_end']-1} ({fold_def['test_size']} rows)")

        result = evaluate_fold(rows, fold_def, policy, random_state)
        fold_results.append(result)

        ens = result["base_models"].get("stacking_ensemble", {})
        comb = result["combined"]
        pol = result["policy"]
        print(f"  Ensemble acc:  {ens.get('accuracy', 0)*100:.1f}%")
        print(f"  Combined acc:  {comb['combined_accuracy']*100:.1f}%")
        print(
            f"  Policy placed: {pol['total_placed']}/{pol['total_evaluated']} "
            f"({pol['coverage_pct']:.0f}% coverage, {pol['hit_rate_pct']:.0f}% hit rate)"
        )

    aggregate = _aggregate_folds(fold_results)
    all_dates = [r.get("date", "") for r in rows if r.get("date")]

    return {
        "available": True,
        "label": label,
        "config": {
            "n_folds": len(folds),
            "min_train_pct": min_train_pct,
            "test_pct": test_pct,
            "cal_pct": cal_pct,
            "random_state": random_state,
            "total_rows": n_rows,
            "date_range": [all_dates[0], all_dates[-1]] if all_dates else [],
            "feature_count": len(FEATURE_COLUMNS),
            "policy_used": policy,
            "sample_weighting": sample_weighting_metadata(),
        },
        "aggregate": aggregate,
        "selector": _aggregate_selector(fold_results),
        "folds": [
            {
                "fold_meta": fr["fold_meta"],
                "base_models": fr["base_models"],
                "combined": fr["combined"],
                "policy": fr["policy"],
                "roi": fr["roi"],
                "selector": fr.get("selector"),
                "test_class_distribution": fr["test_class_distribution"],
            }
            for fr in fold_results
        ],
    }


# ── Main entry point ──────────────────────────────────────────────────────────


def run_walk_forward(
    *,
    historical_path: Path | None = None,
    n_folds: int = 5,
    min_train_pct: float = 0.40,
    test_pct: float = 0.10,
    cal_pct: float = 0.05,
    output_path: Path | None = None,
    random_state: int = 42,
) -> dict[str, Any]:
    """Run a full walk-forward backtest and return the report dict."""
    hist_path = historical_path or historical_dataset_path()
    if not hist_path.exists():
        raise FileNotFoundError(f"Historical dataset not found: {hist_path}")

    print(f"Building clean pre-match features from: {hist_path}")
    rows, _elo = build_clean_features(hist_path)
    n_rows = len(rows)
    print(f"Dataset: {n_rows} usable rows")

    if n_rows < 40:
        raise ValueError(f"Need at least 40 rows for walk-forward, got {n_rows}")

    policy = pp.sport_policy("soccer")
    print(f"Using policy: {policy}")

    all_history = _run_walk_forward_window(
        rows,
        label="All History",
        n_folds=n_folds,
        min_train_pct=min_train_pct,
        test_pct=test_pct,
        cal_pct=cal_pct,
        random_state=random_state,
        policy=policy,
    )
    if not all_history.get("available"):
        raise ValueError(all_history.get("message") or "Failed to build all-history walk-forward report.")

    recent_rows, recent_date_range = _recent_rows(rows, years=RECENT_WINDOW_YEARS)
    recent_window = _run_walk_forward_window(
        recent_rows,
        label=f"Last {RECENT_WINDOW_YEARS} Years",
        n_folds=n_folds,
        min_train_pct=min_train_pct,
        test_pct=test_pct,
        cal_pct=cal_pct,
        random_state=random_state,
        policy=policy,
    )
    if recent_window.get("available"):
        recent_window.setdefault("config", {})
        if recent_date_range:
            recent_window["config"]["date_range"] = recent_date_range

    all_dates = [r.get("date", "") for r in rows if r.get("date")]
    selector_profile = recent_window.get("selector") if recent_window.get("available") else all_history.get("selector")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": {
            "n_folds": all_history.get("config", {}).get("n_folds", 0),
            "min_train_pct": min_train_pct,
            "test_pct": test_pct,
            "cal_pct": cal_pct,
            "random_state": random_state,
            "total_rows": n_rows,
            "date_range": [all_dates[0], all_dates[-1]] if all_dates else [],
            "feature_count": len(FEATURE_COLUMNS),
            "policy_used": policy,
            "sample_weighting": sample_weighting_metadata(),
        },
        "aggregate": all_history.get("aggregate", {}),
        "folds": all_history.get("folds", []),
        "windows": {
            "all_history": all_history,
            "last_3_years": recent_window,
        },
        "selector": selector_profile,
        "selector_window": "last_3_years" if recent_window.get("available") else "all_history",
    }

    save_path = _resolve_report_path(output_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved walk-forward report -> {save_path}")
    return report

    fold_results: list[dict[str, Any]] = []
    for fold_def in folds:
        fold_num = fold_def["fold"]
        print(f"\n── Fold {fold_num}/{len(folds)} ──")
        print(f"  Train: rows 0–{fold_def['train_end']-1} ({fold_def['train_size']} rows)")
        print(f"  Cal:   rows {fold_def['cal_start']}–{fold_def['cal_end']-1} ({fold_def['cal_size']} rows)")
        print(f"  Test:  rows {fold_def['test_start']}–{fold_def['test_end']-1} ({fold_def['test_size']} rows)")

        result = evaluate_fold(rows, fold_def, policy, random_state)
        fold_results.append(result)

        ens = result["base_models"].get("stacking_ensemble", {})
        comb = result["combined"]
        pol = result["policy"]
        print(f"  Ensemble acc:  {ens.get('accuracy', 0)*100:.1f}%")
        print(f"  Combined acc:  {comb['combined_accuracy']*100:.1f}%")
        print(f"  Policy placed: {pol['total_placed']}/{pol['total_evaluated']} "
              f"({pol['coverage_pct']:.0f}% coverage, {pol['hit_rate_pct']:.0f}% hit rate)")

    aggregate = _aggregate_folds(fold_results)

    # Date range of full dataset
    all_dates = [r.get("date", "") for r in rows if r.get("date")]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": {
            "n_folds": len(folds),
            "min_train_pct": min_train_pct,
            "test_pct": test_pct,
            "cal_pct": cal_pct,
            "random_state": random_state,
            "total_rows": n_rows,
            "date_range": [all_dates[0], all_dates[-1]] if all_dates else [],
            "feature_count": len(FEATURE_COLUMNS),
            "policy_used": policy,
        },
        "aggregate": aggregate,
        "folds": [
            {
                "fold_meta": fr["fold_meta"],
                "base_models": fr["base_models"],
                "combined": fr["combined"],
                "policy": fr["policy"],
                "roi": fr["roi"],
                "test_class_distribution": fr["test_class_distribution"],
            }
            for fr in fold_results
        ],
    }

    # ── Save report ───────────────────────────────────────────────────────────
    save_path = output_path or DEFAULT_REPORT_PATH
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved walk-forward report → {save_path}")

    return report


def load_walk_forward_report(path: Path | None = None) -> dict[str, Any] | None:
    """Load a saved walk-forward report if it exists."""
    target = _resolve_report_path(path)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run walk-forward backtest for soccer model and policy evaluation."
    )
    parser.add_argument("--historical", default=None, help="Path to historical_matches.csv.")
    parser.add_argument("--folds", type=int, default=5, help="Number of walk-forward folds.")
    parser.add_argument("--min-train-pct", type=float, default=0.40, help="Minimum training window as fraction of total rows.")
    parser.add_argument("--test-pct", type=float, default=0.10, help="Test fold size as fraction of total rows.")
    parser.add_argument("--cal-pct", type=float, default=0.05, help="Calibration fold size as fraction of total rows.")
    parser.add_argument("--output", default=None, help="Output path for the walk-forward report JSON.")
    parser.add_argument("--random-state", type=int, default=42, help="Random seed.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_walk_forward(
        historical_path=Path(args.historical) if args.historical else None,
        n_folds=args.folds,
        min_train_pct=args.min_train_pct,
        test_pct=args.test_pct,
        cal_pct=args.cal_pct,
        output_path=Path(args.output) if args.output else None,
        random_state=args.random_state,
    )

    agg = report.get("aggregate", {})
    comb = agg.get("combined", {})
    pol = agg.get("policy", {})
    models = agg.get("base_models", {})

    print("\n" + "=" * 70)
    print("WALK-FORWARD BACKTEST RESULTS")
    print("=" * 70)

    print(f"\nFolds: {agg.get('n_folds', 0)}  |  "
          f"Total test matches: {agg.get('total_test_matches', 0)}")
    print(f"Trend: {agg.get('trend', 'N/A')} (delta: {agg.get('trend_delta', 0):+.4f})")

    print("\n── Base Model Accuracy (mean ± std) ──")
    for name, m in models.items():
        mean = m.get("mean_accuracy", 0) or 0
        std = m.get("std_accuracy", 0) or 0
        print(f"  {name:20s}  {mean*100:.1f}% ± {std*100:.1f}%  "
              f"(range: {(m.get('min_accuracy',0) or 0)*100:.1f}–{(m.get('max_accuracy',0) or 0)*100:.1f}%)")

    print(f"\n── Combined Signal ──")
    print(f"  Rule accuracy:     {(comb.get('mean_rule_accuracy',0) or 0)*100:.1f}%")
    print(f"  ML accuracy:       {(comb.get('mean_ml_accuracy',0) or 0)*100:.1f}%")
    print(f"  Combined accuracy: {(comb.get('mean_combined_accuracy',0) or 0)*100:.1f}% "
          f"± {(comb.get('std_combined_accuracy',0) or 0)*100:.1f}%")

    print(f"\n── Policy Performance ──")
    print(f"  Placed:   {pol.get('total_placed', 0)} / {pol.get('total_evaluated', 0)}")
    print(f"  Coverage: {pol.get('aggregate_coverage_pct', 0):.1f}%")
    print(f"  Hit rate: {pol.get('aggregate_hit_rate_pct', 0):.1f}%")

    roi = agg.get("roi", {})
    print(f"\n── Flat-Stake ROI ──")
    print(f"  Points:     {roi.get('total_flat_points', 0):+.1f}")
    print(f"  ROI:        {roi.get('aggregate_roi_pct', 0):+.1f}%")

    best = agg.get("best_fold", {})
    worst = agg.get("worst_fold", {})
    print(f"\n── Fold Extremes ──")
    print(f"  Best:  Fold {best.get('fold', '?')} — {(best.get('combined_accuracy',0) or 0)*100:.1f}%"
          f"  {best.get('date_range', [])}")
    print(f"  Worst: Fold {worst.get('fold', '?')} — {(worst.get('combined_accuracy',0) or 0)*100:.1f}%"
          f"  {worst.get('date_range', [])}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
