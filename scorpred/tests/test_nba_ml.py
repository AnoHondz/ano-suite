"""Tests for NBA ML pipeline: model loading and prediction flow."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import MagicMock, patch
import numpy as np

import nba_ml_service


# ── Model loading ──────────────────────────────────────────────────────────────

def test_model_not_found_returns_unavailable(tmp_path):
    """predict_match returns available=False when model file does not exist."""
    result = nba_ml_service.predict_match({}, model_path=tmp_path / "nonexistent.pkl")
    assert result["available"] is False
    assert "not found" in result.get("error", "").lower()


def test_load_model_returns_none_for_missing_file(tmp_path):
    """load_model returns None when path does not exist."""
    result = nba_ml_service.load_model(model_path=tmp_path / "nope.pkl")
    assert result is None


# ── Prediction flow ────────────────────────────────────────────────────────────

def _make_mock_bundle() -> dict:
    model = MagicMock()
    model.predict.return_value = np.array([1])          # HomeWin
    model.predict_proba.return_value = np.array([[0.35, 0.65]])
    return {
        "model":          model,
        "model_type":     "random_forest",
        "sport":          "nba",
        "feature_names":  nba_ml_service.NBA_FEATURE_COLUMNS,
        "class_labels":   nba_ml_service.NBA_CLASS_LABELS,
        "accuracy":       0.58,
        "calibrated":     True,
    }


def test_predict_match_returns_home_win(tmp_path):
    """predict_match returns HomeWin prediction when model says class 1."""
    bundle = _make_mock_bundle()

    with patch.object(nba_ml_service, "load_model", return_value=bundle):
        result = nba_ml_service.predict_match({
            "home_net_rating_last10": 5.0,
            "away_net_rating_last10": -2.0,
            "rest_days_home": 2.0,
            "rest_days_away": 1.0,
            "is_back_to_back_home": 0.0,
            "is_back_to_back_away": 1.0,
            "home_win_pct_last10": 0.7,
            "away_win_pct_last10": 0.4,
            "h2h_home_win_rate": 0.6,
            "pace_diff": 3.0,
        })

    assert result["available"] is True
    assert result["prediction"] == 1  # HomeWin
    assert abs(result["prob_a"] - 0.65) < 0.01   # home win probability
    assert abs(result["prob_b"] - 0.35) < 0.01   # away win probability
    assert result["confidence"] > 0.5


def test_predict_match_returns_away_win():
    """predict_match returns AwayWin when model says class 0."""
    bundle = _make_mock_bundle()
    bundle["model"].predict.return_value = np.array([0])
    bundle["model"].predict_proba.return_value = np.array([[0.72, 0.28]])

    with patch.object(nba_ml_service, "load_model", return_value=bundle):
        result = nba_ml_service.predict_match({"home_net_rating_last10": -8.0})

    assert result["available"] is True
    assert result["prediction"] == 0  # AwayWin
    assert result["prob_b"] > result["prob_a"]


def test_nba_ml_features_extracted_from_context():
    """_nba_ml_features returns non-default values when form data is present."""
    import scormastermind as sm

    form_a = [{"result": "W", "our_pts": 115, "their_pts": 105, "date": "2026-01-10"} for _ in range(5)]
    form_b = [{"result": "L", "our_pts": 100, "their_pts": 112, "date": "2026-01-09"} for _ in range(5)]
    h2h_a  = [{"result": "W", "our_pts": 110, "their_pts": 102} for _ in range(3)]

    features = sm._nba_ml_features({
        "form_a": form_a,
        "form_b": form_b,
        "h2h_form_a": h2h_a,
    })

    assert features["home_net_rating_last10"] == 10.0   # 115-105
    assert features["away_net_rating_last10"] == -12.0  # 100-112
    assert features["home_win_pct_last10"]    == 1.0    # all wins
    assert features["away_win_pct_last10"]    == 0.0    # all losses
    assert features["h2h_home_win_rate"]      == 1.0    # all h2h wins
    assert features["pace_diff"]              == 15.0   # 115 - 100
