"""Runtime ML inference and evaluation helpers for ScorPred."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

import joblib
from sklearn.metrics import accuracy_score

import logging

from runtime_paths import clean_soccer_dataset_path, clean_soccer_model_path, ensemble_soccer_model_path, league_model_path, trained_model_path

_log = logging.getLogger(__name__)
from train_model import CLASS_LABELS, FEATURE_COLUMNS, _target_from_row
from utils.parsing import safe_float
import prediction_policy as pp


_MODEL_CACHE: dict[str, Any] = {"bundle": None, "path": None, "mtime": 0.0}
_FEATURE_INDEX = {name: idx for idx, name in enumerate(FEATURE_COLUMNS)}


def _default_probabilities() -> list[float]:
    return [0.3333, 0.3333, 0.3334]


def _load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def _row_to_features(row: dict[str, Any]) -> list[float]:
    return [safe_float(row.get(feature), 0.0) for feature in FEATURE_COLUMNS]


def _features_dict_to_vector(
    features_dict: dict[str, Any],
    feature_names: list[str] | None = None,
    fill_values: dict[str, float] | None = None,
    zero_is_missing: list[str] | None = None,
) -> list[float]:
    """Use the artifact's feature order, including older supported schemas."""
    fills = fill_values or {}
    vector = []
    for feature in FEATURE_COLUMNS if feature_names is None else feature_names:
        value = safe_float(features_dict.get(feature), fills.get(feature, 0.0))
        if feature in (zero_is_missing or []) and value == 0:
            value = fills.get(feature, 0.0)
        vector.append(value if math.isfinite(value) else fills.get(feature, 0.0))
    return vector


def validate_model_bundle(bundle: Any) -> list[str]:
    if not isinstance(bundle, dict) or bundle.get("model") is None:
        raise ValueError("Model bundle must contain a fitted model.")
    names = bundle.get("feature_names")
    if not isinstance(names, (list, tuple)) or not names or any(
        not isinstance(name, str) or not name for name in names
    ) or len(set(names)) != len(names):
        raise ValueError("Model bundle must declare unique feature_names in training order.")
    count = getattr(bundle["model"], "n_features_in_", len(names))
    if count != len(names):
        raise ValueError(f"Model schema declares {len(names)} features; estimator expects {count}.")
    unknown = set(names) - set(FEATURE_COLUMNS)
    if unknown:
        raise ValueError(f"Unsupported model features: {', '.join(sorted(unknown))}")
    return list(names)


def _feature_value(features: list[float], name: str, default: float = 0.0) -> float:
    idx = _FEATURE_INDEX.get(name)
    if idx is None or idx >= len(features):
        return default
    return float(features[idx])


def _rule_prediction_from_features(features: list[float]) -> int:
    side_edge = (
        0.34 * _feature_value(features, "ppg_diff_5")
        + 0.15 * _feature_value(features, "gf_diff_5")
        + 0.15 * _feature_value(features, "ga_diff_5")
        + 0.18 * _feature_value(features, "attack_balance_diff")
        + 0.10 * _feature_value(features, "venue_ppg_diff_5")
        + 0.08 * _feature_value(features, "scored_rate_diff_5")
        + 0.06 * _feature_value(features, "clean_sheet_diff_5")
        + 0.015 * _feature_value(features, "rest_diff_days")
        + 0.0022 * _feature_value(features, "elo_diff")
    )
    draw_pressure = max(
        0.0,
        0.24
        - abs(side_edge) * 0.65
        - abs(_feature_value(features, "gf_diff_5")) * 0.10
        - abs(_feature_value(features, "attack_balance_diff")) * 0.12,
    )
    if draw_pressure >= 0.16:
        return 1
    if side_edge > 0:
        return 0
    return 2


def _rule_probabilities(features: list[float]) -> list[float]:
    pred = _rule_prediction_from_features(features)
    side_edge = abs(
        0.34 * _feature_value(features, "ppg_diff_5")
        + 0.15 * _feature_value(features, "gf_diff_5")
        + 0.15 * _feature_value(features, "ga_diff_5")
        + 0.18 * _feature_value(features, "attack_balance_diff")
        + 0.10 * _feature_value(features, "venue_ppg_diff_5")
        + 0.0022 * _feature_value(features, "elo_diff")
    )
    if pred == 1:
        draw_prob = min(0.44, max(0.31, 0.36 - side_edge * 0.10))
        side_prob = (1.0 - draw_prob) / 2.0
        return [round(side_prob, 6), round(draw_prob, 6), round(side_prob, 6)]
    if pred == 0:
        home_prob = min(0.74, max(0.49, 0.55 + side_edge * 0.18))
        draw_prob = min(0.24, max(0.10, 0.22 - side_edge * 0.08))
        away_prob = max(0.06, 1.0 - home_prob - draw_prob)
        return [round(home_prob, 6), round(draw_prob, 6), round(away_prob, 6)]
    away_prob = min(0.74, max(0.49, 0.55 + side_edge * 0.18))
    draw_prob = min(0.24, max(0.10, 0.22 - side_edge * 0.08))
    home_prob = max(0.06, 1.0 - away_prob - draw_prob)
    return [round(home_prob, 6), round(draw_prob, 6), round(away_prob, 6)]


def _combine_probabilities(rule_probs: list[float], ml_probs: list[float]) -> list[float]:
    """Blend rule and ML probabilities for offline evaluation.

    Weights are read from prediction_policy so that offline evaluation uses
    the same blend as the runtime decision layer in scormastermind.
    """
    ml_w = pp.soccer_ml_blend_weight()
    rule_w = 1.0 - ml_w
    combined = [rule_w * rule_probs[idx] + ml_w * ml_probs[idx] for idx in range(3)]
    if len(combined) == 3:
        draw_prob = combined[1]
        side_top = max(combined[0], combined[2])
        side_gap = abs(combined[0] - combined[2])

        # Draw transfer with suppression when draw is genuinely competitive —
        # mirrors the runtime draw_dominance / suppression logic.
        if draw_prob > 0.0:
            draw_dominance = max(-0.30, min(0.15, draw_prob - side_top))
            suppression = max(0.0, min(1.0, 1.0 - (draw_dominance + 0.04) * 8.0))
            raw_transfer = min(
                draw_prob * 0.22,
                max(0.0, max(0.0, side_gap - 0.05) * 0.55),
            )
            draw_transfer = raw_transfer * suppression
            if draw_transfer > 0.001:
                side_total = max(combined[0] + combined[2], 1e-9)
                combined[1] -= draw_transfer
                combined[0] += draw_transfer * (combined[0] / side_total)
                combined[2] += draw_transfer * (combined[2] / side_total)

    total = sum(combined) or 1.0
    return [round(prob / total, 6) for prob in combined]


def _confidence_label(confidence: float) -> str:
    # Thresholds aligned with scormastermind._confidence_label()
    if confidence >= 0.72:
        return "High"
    if confidence >= 0.51:
        return "Medium"
    return "Low"


def model_exists(model_path: Path | None = None) -> bool:
    return (model_path or clean_soccer_model_path()).exists()


def load_model(
    model_path: Path | None = None,
    force_reload: bool = False,
    league_id: int | None = None,
) -> dict[str, Any] | None:
    # The release manifest publishes one production artifact. Experimental
    # league/ensemble models must be selected explicitly by their caller.
    path = Path(model_path) if model_path else clean_soccer_model_path()
    if not path.exists():
        return None

    # Invalidate cache when the model file has been updated (e.g. after retraining)
    current_mtime = path.stat().st_mtime
    if (
        not force_reload
        and _MODEL_CACHE.get("bundle") is not None
        and _MODEL_CACHE.get("path") == str(path)
        and _MODEL_CACHE.get("mtime") == current_mtime
    ):
        cached = _MODEL_CACHE["bundle"]
        return cached if isinstance(cached, dict) else None

    try:
        bundle = joblib.load(path)
    except Exception:
        _log.exception("Unable to load model artifact %s", path.name)
        return None

    if not isinstance(bundle, dict) or "model" not in bundle:
        return None

    _MODEL_CACHE["bundle"] = bundle
    _MODEL_CACHE["path"] = str(path)
    _MODEL_CACHE["mtime"] = current_mtime
    return bundle


def _base_model_agreement(model: Any, vector: list[float]) -> dict[str, Any] | None:
    """Extract prediction agreement across base learners inside a StackingClassifier.

    Returns None when the model is not a stacking ensemble.
    """
    try:
        estimators = getattr(model, "estimators_", None)
        if not estimators:
            return None
        predictions = []
        for est in estimators:
            pred = int(est.predict([vector])[0])
            predictions.append(pred)
        from collections import Counter
        counts = Counter(predictions)
        most_common_pred, most_common_count = counts.most_common(1)[0]
        n = len(predictions)
        return {
            "n_base_models": n,
            "unanimous": most_common_count == n,
            "agreement_ratio": round(most_common_count / n, 4),
            "majority_class": int(most_common_pred),
        }
    except Exception:
        return None


def predict_match(
    features_dict: dict[str, Any],
    model_path: Path | None = None,
    league_id: int | None = None,
) -> dict[str, Any]:
    bundle = load_model(model_path=model_path, league_id=league_id)
    if not bundle:
        return {
            "available": False,
            "prediction": None,
            "probabilities": _default_probabilities(),
            "confidence": 0.0,
            "error": "Trained model not found. Run train_model.py first.",
        }

    model = bundle.get("model")
    if model is None:
        return {
            "available": False,
            "prediction": None,
            "probabilities": _default_probabilities(),
            "confidence": 0.0,
            "error": "Trained model bundle is invalid.",
        }

    try:
        feature_names = validate_model_bundle(bundle)
        vector = _features_dict_to_vector(features_dict, feature_names, bundle.get("feature_fill_values"), bundle.get("zero_is_missing"))
        probabilities_raw = model.predict_proba([vector])[0]
        prediction = int(model.predict([vector])[0])
        classes = list(getattr(model, "classes_", [0, 1, 2]))
        if set(classes) != {0, 1, 2}:
            raise ValueError("Soccer model must declare HomeWin, Draw, and AwayWin classes.")
        probabilities_raw = [probabilities_raw[classes.index(label)] for label in range(3)]
        if any(not math.isfinite(float(p)) or not 0 <= float(p) <= 1 for p in probabilities_raw):
            raise ValueError("Model returned invalid probabilities.")
    except Exception as exc:
        return {
            "available": False,
            "prediction": None,
            "probabilities": _default_probabilities(),
            "confidence": 0.0,
            "error": str(exc),
        }

    probabilities = [round(float(prob), 6) for prob in probabilities_raw]
    confidence = max(probabilities) if probabilities else 0.0
    model_type = bundle.get("model_type", "random_forest")

    # Expose base model agreement for stacking ensembles.
    base_agreement = _base_model_agreement(model, vector)

    return {
        "available": True,
        "prediction": prediction,
        "probabilities": probabilities,
        "confidence": round(float(confidence), 6),
        "class_labels": CLASS_LABELS,
        "model_type": model_type,
        "model_role": bundle.get("model_role", "fallback"),
        "base_models": bundle.get("base_models"),
        "base_agreement": base_agreement,
        "feature_count": len(feature_names),
        "schema_version": bundle.get("schema_version", "legacy-named-features"),
    }


def evaluate_model_comparison(
    dataset_path: Path | None = None,
    model_path: Path | None = None,
    test_size: float = 0.25,
    random_state: int = 42,
) -> dict[str, Any]:
    dataset = Path(dataset_path or clean_soccer_dataset_path())
    if not dataset.exists():
        return {
            "available": False,
            "message": f"Dataset not found at {dataset}",
            "rule_accuracy": None,
            "ml_accuracy": None,
            "combined_accuracy": None,
            "evaluation_matches": 0,
        }

    bundle = load_model(model_path=model_path)
    if not bundle:
        return {
            "available": False,
            "message": "Trained model not found. Run train_model.py first.",
            "rule_accuracy": None,
            "ml_accuracy": None,
            "combined_accuracy": None,
            "evaluation_matches": 0,
        }

    model = bundle.get("model")
    rows = _load_rows(dataset)
    x: list[list[float]] = []
    y: list[int] = []
    for row in rows:
        target = _target_from_row(row)
        if target is None:
            continue
        features = _row_to_features(row)
        x.append(features)
        y.append(target)

    if len(x) < 20:
        return {
            "available": False,
            "message": "Not enough rows to evaluate model comparison.",
            "rule_accuracy": None,
            "ml_accuracy": None,
            "combined_accuracy": None,
            "evaluation_matches": len(x),
        }

    # Chronological holdout to match train_model.py and avoid look-ahead leakage.
    n_test = max(1, int(len(x) * test_size))
    x_train, x_test = x[:-n_test], x[-n_test:]
    y_train, y_test = y[:-n_test], y[-n_test:]

    try:
        ml_predictions = model.predict(x_test)
        ml_probabilities = model.predict_proba(x_test)
    except Exception as exc:
        return {
            "available": False,
            "message": f"ML evaluation failed: {exc}",
            "rule_accuracy": None,
            "ml_accuracy": None,
            "combined_accuracy": None,
            "evaluation_matches": len(x_test),
        }

    rule_predictions = [_rule_prediction_from_features(features) for features in x_test]
    rule_probs = [_rule_probabilities(features) for features in x_test]

    combined_predictions: list[int] = []
    for idx, probs in enumerate(ml_probabilities):
        combined = _combine_probabilities(rule_probs[idx], [float(p) for p in probs])
        combined_predictions.append(int(max(range(3), key=lambda class_idx: combined[class_idx])))

    rule_accuracy = float(accuracy_score(y_test, rule_predictions)) * 100.0
    ml_accuracy = float(accuracy_score(y_test, ml_predictions)) * 100.0
    combined_accuracy = float(accuracy_score(y_test, combined_predictions)) * 100.0

    combined_probs: list[list[float]] = [
        _combine_probabilities(rule_probs[idx], [float(p) for p in probs])
        for idx, probs in enumerate(ml_probabilities)
    ]
    top_probs = [max(probs) for probs in combined_probs]
    gaps = [
        sorted(probs, reverse=True)[0] - sorted(probs, reverse=True)[1]
        for probs in combined_probs
    ]
    confidence_counts = {"High": 0, "Medium": 0, "Low": 0}
    for top_prob, gap in zip(top_probs, gaps):
        label = _confidence_label(min(0.95, max(0.05, top_prob * 0.82 + gap * 1.30)))
        confidence_counts[label] += 1
    draw_predictions = sum(1 for pred in combined_predictions if pred == 1)

    return {
        "available": True,
        "message": None,
        "rule_accuracy": round(rule_accuracy, 1),
        "ml_accuracy": round(ml_accuracy, 1),
        "combined_accuracy": round(combined_accuracy, 1),
        "evaluation_matches": len(y_test),
        "train_matches": len(y_train),
        "diagnostics": {
            "draw_rate_pct": round((draw_predictions / len(combined_predictions)) * 100.0, 1) if combined_predictions else 0.0,
            "avg_top_probability_pct": round((sum(top_probs) / len(top_probs)) * 100.0, 1) if top_probs else 0.0,
            "avg_top_two_gap_pct": round((sum(gaps) / len(gaps)) * 100.0, 1) if gaps else 0.0,
            "confidence_distribution": confidence_counts,
        },
    }
