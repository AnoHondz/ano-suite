"""
nba_ml_service.py — Runtime NBA ML inference helpers.

Mirrors ml_service.py for soccer but for 2-class NBA home/away prediction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib

from nba_train_model import NBA_CLASS_LABELS, NBA_FEATURE_COLUMNS
from runtime_paths import nba_model_path
from utils.parsing import safe_float

_MODEL_CACHE: dict[str, Any] = {"bundle": None, "path": None}


def _default_probabilities() -> list[float]:
    return [0.50, 0.50]


def _features_to_vector(features_dict: dict[str, Any]) -> list[float]:
    return [safe_float(features_dict.get(f), 0.0) for f in NBA_FEATURE_COLUMNS]


def model_exists(model_path: Path | None = None) -> bool:
    return (model_path or nba_model_path()).exists()


def load_model(model_path: Path | None = None, force_reload: bool = False) -> dict[str, Any] | None:
    path = model_path or nba_model_path()
    if not path.exists():
        return None

    if (
        not force_reload
        and _MODEL_CACHE.get("bundle") is not None
        and _MODEL_CACHE.get("path") == str(path)
    ):
        cached = _MODEL_CACHE["bundle"]
        return cached if isinstance(cached, dict) else None

    try:
        bundle = joblib.load(path)
    except Exception:
        return None

    if not isinstance(bundle, dict) or "model" not in bundle:
        return None

    _MODEL_CACHE["bundle"] = bundle
    _MODEL_CACHE["path"]   = str(path)
    return bundle


def predict_match(features_dict: dict[str, Any], model_path: Path | None = None) -> dict[str, Any]:
    """Predict NBA game outcome.

    Returns:
        available (bool)
        prediction (int): 1=HomeWin, 0=AwayWin
        probabilities (list[float]): [away_win_prob, home_win_prob]
        confidence (float): max probability
    """
    bundle = load_model(model_path=model_path)
    if not bundle:
        return {
            "available":     False,
            "prediction":    None,
            "probabilities": _default_probabilities(),
            "confidence":    0.5,
            "error":         "NBA model not found. Run nba_train_model.py first.",
        }

    model = bundle.get("model")
    if model is None:
        return {
            "available":     False,
            "prediction":    None,
            "probabilities": _default_probabilities(),
            "confidence":    0.5,
            "error":         "NBA model bundle invalid.",
        }

    vector = _features_to_vector(features_dict)
    try:
        proba_raw   = model.predict_proba([vector])[0]
        prediction  = int(model.predict([vector])[0])
    except Exception as exc:
        return {
            "available":     False,
            "prediction":    None,
            "probabilities": _default_probabilities(),
            "confidence":    0.5,
            "error":         str(exc),
        }

    probabilities = [round(float(p), 6) for p in proba_raw]
    confidence    = max(probabilities) if probabilities else 0.5

    return {
        "available":     True,
        "prediction":    prediction,
        "probabilities": probabilities,
        "confidence":    round(float(confidence), 6),
        "class_labels":  NBA_CLASS_LABELS,
        "model_type":    bundle.get("model_type", "random_forest"),
        # prob_a = home win prob (class 1), prob_b = away win prob (class 0)
        "prob_a":        probabilities[1] if len(probabilities) > 1 else 0.5,
        "prob_b":        probabilities[0] if probabilities else 0.5,
    }
