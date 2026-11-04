
import joblib
import numpy as np
import pytest

import app as module
import decision_ui as ui
import ml_service
from services import cache_service, prediction_service


def analysis(action="SKIP"):
    return {"match_id": "1", "matchup": "Home vs Away", "action": action,
            "confidence": 80, "probabilities": {"a": 70, "draw": 20, "b": 10},
            "recommended_side": "Home", "reason": "Risk guard", "data_quality": "Limited Data",
            "metric_breakdown": {"risk_level": "HIGH"}}


@pytest.mark.parametrize("action", ["BET", "CONSIDER", "SKIP"])
def test_card_and_ranking_preserve_engine_action(action):
    original = analysis(action)
    card = ui.build_decision_card(analysis=original)
    ui.top_opportunities([card])
    ui.plan_summary([card])
    assert card["action"] == action
    assert original == analysis(action)


class RecordingModel:
    n_features_in_ = 2
    classes_ = np.array([2, 0, 1])

    def predict_proba(self, matrix):
        assert matrix == [[3.0, 1.0]]
        return np.array([[0.2, 0.6, 0.2]])

    def predict(self, matrix):
        return np.array([0])


def test_inference_uses_artifact_feature_order_and_class_order(tmp_path):
    path = tmp_path / "model.pkl"
    joblib.dump({"model": RecordingModel(), "feature_names": ["away_ppg_5", "home_ppg_5"]}, path)
    result = ml_service.predict_match({"home_ppg_5": 1, "away_ppg_5": 3}, model_path=path)
    assert result["available"] is True
    assert result["probabilities"] == [0.6, 0.2, 0.2]


def test_inference_rejects_mismatched_artifact_metadata(tmp_path):
    path = tmp_path / "invalid.pkl"
    joblib.dump({"model": RecordingModel(), "feature_names": ["home_ppg_5"]}, path)
    result = ml_service.predict_match({}, model_path=path)
    assert result["available"] is False
    assert "schema" in result["error"].lower()


def test_cache_ttl_survives_memory_and_disk_restore(monkeypatch):
    monkeypatch.setattr(cache_service, "_get_redis_client", lambda: None)
    clock = [1000.0]
    monkeypatch.setattr(cache_service.time, "time", lambda: clock[0])
    cache_service.set_json("fixture", {"id": 1}, ttl=5)
    clock[0] += 4
    cache_service._local_cache.clear()
    assert cache_service.get_json("fixture") == {"id": 1}
    clock[0] += 2
    assert cache_service.get_json("fixture") is None
    cache_service._local_cache.clear()
    assert cache_service.get_json("fixture") is None


def test_restoring_short_ttl_does_not_extend_it(monkeypatch):
    monkeypatch.setattr(cache_service, "_get_redis_client", lambda: None)
    clock = [1000.0]
    monkeypatch.setattr(cache_service.time, "time", lambda: clock[0])
    cache_service.set_json("fixture", {"id": 1}, ttl=300)
    clock[0] += 61
    assert cache_service.get_json("fixture", ttl=60) is None


def test_cache_hit_rehydrates_analysis_lookup(monkeypatch):
    fixture = {"fixture": {"id": 42}, "teams": {"home": {"name": "Home"}, "away": {"name": "Away"}}}
    monkeypatch.setattr(cache_service, "get_json", lambda *a, **kw: [[fixture], None, "cached", ""])
    assert module.load_fixtures_cached(39)[0] == [fixture]
    assert module._FIXTURE_INDEX["42"] == fixture


def test_raw_fixture_cache_rehydrates_before_analysis(monkeypatch):
    fixture = {"fixture": {"id": 43}}
    restored = {}
    deps = dict(prediction_service._deps)
    monkeypatch.setattr(prediction_service, "_deps", deps)
    prediction_service.configure(
        restore_fixtures=lambda rows: restored.update({str(f["fixture"]["id"]): f for f in rows}),
        analyze_match=lambda key: analysis() if key in restored else None,
        card_from_fixture=lambda fixture, result: result,
    )
    monkeypatch.setattr(cache_service, "get_json", lambda key, **kw: [[fixture], None, "cached", ""] if key == cache_service.make_key("fixtures_raw", 39) else None)
    monkeypatch.setattr(cache_service, "set_json", lambda *args, **kwargs: None)
    cards, *_ = prediction_service.get_fixture_cards(39)
    assert len(cards) == 1
