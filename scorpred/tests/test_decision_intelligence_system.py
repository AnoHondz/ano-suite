from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app as flask_app_module
import model_tracker as mt
from services import calibration_service, model_trust_service
from services.calibration_engine import CalibrationEngine
from services.decision_engine import DecisionEngine
from services.feature_attribution_engine import FeatureAttributionEngine
from services.match_brain import MatchBrain
from tests.source_contracts import fresh_source


def _fixture(fid: int = 101, *, with_odds: bool = True):
    odds = {"home": 2.10, "draw": 3.2, "away": 3.6} if with_odds else {}
    return {
        "data_freshness": fresh_source(),
        "fixture": {"id": fid, "date": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(), "status": {"short": "NS"}},
        "teams": {
            "home": {"id": 1, "name": "Arsenal", "logo": ""},
            "away": {"id": 2, "name": "Chelsea", "logo": ""},
        },
        "league": {"name": "Premier League"},
        "prediction": {
            "win_probabilities": {"a": 52.0, "draw": 24.0, "b": 24.0},
            "best_pick": {"prediction": "Arsenal", "reasoning": "Stronger recent form"},
            "confidence_pct": 66,
            "data_completeness": {"tier": "strong"},
            "odds": odds,
        },
    }


def _client():
    flask_app_module.app.config["TESTING"] = True
    with flask_app_module.app.test_client() as client:
        return client


def test_decision_engine_contains_required_fields():
    decision = DecisionEngine().build_decision(
        {
            "home_name": "Arsenal",
            "away_name": "Chelsea",
            "probabilities": {"home": 55, "draw": 22, "away": 23},
            "confidence": 68,
            "data_completeness": {"tier": "strong"},
        }
    )
    assert "confidence" in decision
    assert "edge_score" in decision
    assert "risk_level" in decision
    assert "reasoning" in decision
    assert decision["reasoning"]["strengths"]
    assert decision["reasoning"]["risks"]
    assert decision["edge_score"] is None


def test_edge_score_is_decimal_and_formatter_is_percent():
    decision = DecisionEngine().build_decision(
        {
            "home_name": "Arsenal",
            "away_name": "Chelsea",
            "probabilities": {"home": 60, "draw": 20, "away": 20},
            "confidence": 60,
            "odds": {"home": 2.0},
            "data_completeness": {"tier": "strong"},
        }
    )
    assert decision["edge_score"] == 0.1
    assert flask_app_module.format_percent_decimal(decision["edge_score"]) == "+10.0%"


def test_missing_odds_does_not_fake_ev():
    decision = DecisionEngine().build_decision(
        {
            "home_name": "Arsenal",
            "away_name": "Chelsea",
            "probabilities": {"home": 56, "draw": 22, "away": 22},
            "confidence": 58,
            "data_completeness": {"tier": "strong"},
        }
    )
    assert decision["implied_probability"] is None
    assert decision["edge_score"] is None
    assert decision["expected_value"] is None


def test_analyze_match_uses_match_id_and_prediction_route_renders():
    canonical = {
        "match_id": "101",
        "matchup": "Arsenal vs Chelsea",
        "kickoff": "2026-04-24T15:00:00+00:00",
        "prediction": {
            "side": "Arsenal",
            "action": "BET",
            "confidence": 66,
            "probabilities": {"home": 52.0, "draw": 24.0, "away": 24.0},
            "edge_score": 0.05,
            "risk_level": "LOW",
            "expected_value": 0.05,
            "risk_score": 0.12,
            "model_probability": 0.66,
            "implied_probability": 0.61,
            "decision_grade": "A",
            "data_quality": 85,
            "reasoning": {"strengths": ["Stronger recent form"], "risks": ["Late squad news"]},
        },
        "teams": {"home": {"name": "Arsenal"}, "away": {"name": "Chelsea"}},
    }
    with patch.object(flask_app_module, "_MATCH_BRAIN") as brain:
        brain.get_match_analysis.return_value = canonical
        client = _client()
        rv = client.get("/prediction?match_id=101")
    assert rv.status_code == 200
    assert b"Arsenal" in rv.data
    assert b"66" in rv.data


def test_insights_route_no_redirect_loop():
    with patch.object(flask_app_module, "_MATCH_BRAIN") as brain:
        brain.get_insights.return_value = {"top_opportunities": [], "high_confidence": []}
        client = _client()
        rv = client.get("/insights", follow_redirects=False)
    assert rv.status_code == 200


def test_performance_ignores_open_matches_for_win_rate():
    completed = [{"status": "completed", "is_correct": True, "date": "2026-04-20"}]
    pending = [{"status": "pending", "is_correct": None, "date": "2026-04-20"}]
    with patch.object(mt, "get_completed_predictions", return_value=completed), patch.object(mt, "get_pending_predictions", return_value=pending):
        client = _client()
        rv = client.get("/performance")
    assert rv.status_code == 200
    assert b"100.0%" in rv.data


def test_tracking_deduplicates_and_updates_completion(tmp_path, monkeypatch):
    tracking_file = tmp_path / "tracking.json"
    monkeypatch.setattr(mt, "_TRACKING_FILE", str(tracking_file))
    pred_id_1 = mt.save_prediction(
        sport="soccer",
        team_a="Arsenal",
        team_b="Chelsea",
        predicted_winner="A",
        win_probs={"a": 52, "draw": 24, "b": 24},
        confidence="High",
        game_date="2026-04-24",
        fixture_id="101",
    )
    pred_id_2 = mt.save_prediction(
        sport="soccer",
        team_a="Arsenal",
        team_b="Chelsea",
        predicted_winner="A",
        win_probs={"a": 52, "draw": 24, "b": 24},
        confidence="High",
        game_date="2026-04-24",
        fixture_id="101",
    )
    assert pred_id_1 == pred_id_2
    assert len(mt.get_recent_predictions(10)) == 1

    updated = mt.update_prediction_result(pred_id_1, "A", {"a": 2, "b": 1}, fixture_id="101")
    assert updated is True
    completed = mt.get_completed_predictions(10)
    assert completed
    assert completed[0]["status"] == "completed"


def test_match_brain_returns_consistent_cached_analysis():
    fixture = _fixture()
    brain = MatchBrain(
        load_fixtures=lambda _league: ([fixture], None, "configured", ""),
        get_fixture_by_id=lambda _mid: fixture,
        decision_engine=DecisionEngine(),
    )
    first = brain.get_match_analysis("101")
    second = brain.get_match_analysis("101")
    assert first == second


def test_top_opportunities_sort_handles_none_ev():
    with_odds = _fixture(101, with_odds=True)
    without_odds = _fixture(102, with_odds=False)
    brain = MatchBrain(
        load_fixtures=lambda _league: ([with_odds, without_odds], None, "configured", ""),
        get_fixture_by_id=lambda _mid: with_odds,
        decision_engine=DecisionEngine(),
    )
    insights = brain.get_insights(39)
    assert len(insights["top_opportunities"]) == 2


def test_tracking_stores_canonical_snapshot(tmp_path, monkeypatch):
    tracking_file = tmp_path / "tracking.json"
    monkeypatch.setattr(mt, "_TRACKING_FILE", str(tracking_file))
    fixture = _fixture()
    brain = MatchBrain(
        load_fixtures=lambda _league: ([fixture], None, "configured", ""),
        get_fixture_by_id=lambda _mid: fixture,
        decision_engine=DecisionEngine(),
        tracker_save=mt.save_prediction,
    )
    canonical = brain.get_match_analysis("101")
    pred_id = brain.track_match(canonical)
    row = mt.get_prediction_by_id(pred_id)
    snapshot = (row.get("model_factors") or {}).get("canonical_snapshot")
    assert snapshot is not None
    assert snapshot["evaluation_status"] == "OPEN"
    assert "edge_score" in snapshot


def test_calibration_ignores_open_rows():
    rows = [
        {"status": "completed", "is_correct": True, "confidence": 80},
        {"status": "pending", "is_correct": None, "confidence": 90},
    ]
    result = calibration_service.get_calibration(rows)
    assert result["sample_size"] == 1


def test_model_trust_requires_minimum_sample():
    trust = model_trust_service.compute_trust_score(
        calibration_score=0.9,
        recent_accuracy=0.8,
        average_data_quality=80,
        sample_size=3,
    )
    assert trust["trust_score"] is None
    assert trust["label"] == "Insufficient Data"


def test_routes_do_not_recompute_action_outside_decision_engine():
    payload = {
        "win_probabilities": {"a": 55, "draw": 20, "b": 25},
        "best_pick": {"prediction": "Arsenal"},
        "confidence_pct": 80,
        "data_completeness": {"tier": "strong"},
    }
    with patch.object(flask_app_module.DecisionEngine, "build_decision", return_value={
        "side": "Arsenal",
        "action": "SKIP",
        "confidence": 80,
        "probabilities": {"home": 55, "draw": 20, "away": 25},
        "model_probability": 0.8,
        "implied_probability": None,
        "edge_score": None,
        "risk_level": "HIGH",
        "risk_score": 0.8,
        "expected_value": None,
        "decision_grade": "C",
        "data_quality": 80,
        "reasoning": {"strengths": ["x"], "risks": ["y"]},
    }):
        analysis = flask_app_module._analysis_from_prediction_payload(payload, match_id="1", matchup="Arsenal vs Chelsea")
    assert analysis["action"] == "SKIP"


def test_soccer_card_and_match_analysis_share_quant_fields():
    fixture = _fixture()
    with patch.object(flask_app_module, "_MATCH_BRAIN") as brain:
        canonical = {
            "data_freshness": fresh_source(),
            "match_id": "101",
            "matchup": "Arsenal vs Chelsea",
            "league": "Premier League",
            "kickoff": "2026-04-24T15:00:00+00:00",
            "status": "scheduled",
            "recommended_side": "Arsenal",
            "action": "BET",
            "confidence": 66,
            "probabilities": {"home": 52.0, "draw": 24.0, "away": 24.0},
            "data_quality": 85,
            "reason": "Stronger recent form",
            "metric_breakdown": {
                "model_probability": 0.66,
                "implied_probability": 0.52,
                "edge_score": 0.14,
                "expected_value": 0.14,
                "risk_score": 0.2,
                "risk_level": "MEDIUM",
                "decision_grade": "B",
            },
            "prediction": {
                "side": "Arsenal",
                "action": "BET",
                "confidence": 66,
                "probabilities": {"home": 52.0, "draw": 24.0, "away": 24.0},
                "model_probability": 0.66,
                "implied_probability": 0.52,
                "edge_score": 0.14,
                "expected_value": 0.14,
                "risk_score": 0.2,
                "risk_level": "MEDIUM",
                "decision_grade": "B",
                "data_quality": 85,
                "reasoning": {"strengths": ["Stronger recent form"], "risks": ["x"]},
            },
        }
        brain.canonical_from_fixture.return_value = canonical
        analysis = flask_app_module._analysis_from_fixture(fixture)
        card = flask_app_module._soccer_card_from_fixture_analysis(fixture, analysis)
    assert analysis["edge_score"] == 0.14
    assert analysis["expected_value"] == 0.14
    assert card["action"] == analysis["action"]


def test_api_timeout_uses_last_known_good_data():
    fixture = _fixture()
    calls = {"n": 0}

    def loader(_league):
        calls["n"] += 1
        if calls["n"] == 1:
            return [fixture], None, "configured", ""
        raise TimeoutError("provider timeout")

    brain = MatchBrain(
        load_fixtures=loader,
        get_fixture_by_id=lambda _mid: fixture,
        decision_engine=DecisionEngine(),
    )
    first = brain.safe_fetch_fixtures(39)
    second = brain.safe_fetch_fixtures(39)
    assert first
    assert second[0]["fixture"] == first[0]["fixture"]
    assert second[0]["data_freshness"]["state"] == "degraded"


def test_missing_probabilities_are_unavailable_without_invented_distribution():
    broken = _fixture()
    broken["prediction"]["win_probabilities"] = {}
    brain = MatchBrain(
        load_fixtures=lambda _league: ([broken], None, "configured", ""),
        get_fixture_by_id=lambda _mid: broken,
        decision_engine=DecisionEngine(),
    )
    analysis = brain.get_match_analysis("101")
    assert analysis["probabilities"] == {}
    assert analysis["confidence"] == 0
    assert analysis["action"] == "SKIP"


def test_duplicate_tracking_blocked(tmp_path, monkeypatch):
    tracking_file = tmp_path / "tracking.json"
    monkeypatch.setattr(mt, "_TRACKING_FILE", str(tracking_file))
    fixture = _fixture()
    brain = MatchBrain(
        load_fixtures=lambda _league: ([fixture], None, "configured", ""),
        get_fixture_by_id=lambda _mid: fixture,
        decision_engine=DecisionEngine(),
        tracker_save=mt.save_prediction,
        tracker_recent=mt.get_recent_predictions,
    )
    canonical = brain.get_match_analysis("101")
    first = brain.track_match(canonical)
    second = brain.track_match(canonical)
    assert first
    assert second == ""


def test_inconsistent_decision_output_keeps_first_valid_cache():
    fixture = _fixture()
    brain = MatchBrain(
        load_fixtures=lambda _league: ([fixture], None, "configured", ""),
        get_fixture_by_id=lambda _mid: fixture,
        decision_engine=DecisionEngine(),
    )
    first = brain.get_match_analysis("101")
    brain._analysis_cache["101"] = first
    brain._analysis_checksum["101"] = "fixed"
    with patch.object(MatchBrain, "_checksum", return_value="different"):
        second = brain.get_match_analysis("101")
    assert second == first


def test_invalid_match_id_returns_safe_unavailable_payload():
    brain = MatchBrain(
        load_fixtures=lambda _league: ([], None, "configured", ""),
        get_fixture_by_id=lambda _mid: None,
        decision_engine=DecisionEngine(),
    )
    payload = brain.get_match_analysis("")
    assert payload["status"] == "unavailable"
    assert payload["reason"] == "Data not ready"


def test_refresh_cycle_guard_prevents_duplicate_evaluation_calls():
    fixture = _fixture()
    calls = {"refresh": 0}

    def refresh():
        calls["refresh"] += 1

    brain = MatchBrain(
        load_fixtures=lambda _league: ([fixture], None, "configured", ""),
        get_fixture_by_id=lambda _mid: fixture,
        decision_engine=DecisionEngine(),
        tracker_recent=lambda _limit: [],
        refresh_results=refresh,
    )
    brain.refresh_cycle(39, min_interval_seconds=120)
    brain.refresh_cycle(39, min_interval_seconds=120)
    assert calls["refresh"] == 1


def test_calibration_engine_snapshot_and_completed_evaluation():
    engine = CalibrationEngine(min_samples=2)
    snapshot = engine.build_snapshot(
        {
            "match_id": "11",
            "recommended_side": "Arsenal",
            "confidence": 78,
            "probabilities": {"home": 0.5, "draw": 0.25, "away": 0.25},
            "edge_score": 0.12,
            "expected_value": 0.1,
            "data_quality": 80,
            "tracked_at": "2026-04-24T10:00:00Z",
        }
    )
    assert snapshot["match_id"] == "11"
    assert snapshot["predicted_side"] == "Arsenal"

    evaluated = engine.evaluate_completed({"confidence": 78, "actual_result": "H", "is_correct": True})
    assert evaluated["confidence_bucket"] == "70-80"


def test_calibration_engine_bucket_breakdown_and_trust_penalty():
    engine = CalibrationEngine(min_samples=2)
    rows = [
        {"status": "completed", "confidence": 75, "is_correct": True},
        {"status": "completed", "confidence": 75, "is_correct": False},
        {"status": "completed", "confidence": 55, "is_correct": False},
    ]
    metrics = engine.get_model_metrics(rows)
    assert metrics["bucket_breakdown"]["70-80"]["sample_size"] == 2
    assert isinstance(metrics["trust_score"], float)

    low_sample_metrics = CalibrationEngine(min_samples=20).get_model_metrics(rows)
    assert low_sample_metrics["calibration_score"] == "insufficient data"
    assert low_sample_metrics["trust_score"] < metrics["trust_score"]


def test_decision_engine_respects_trust_score_downgrade():
    decision = DecisionEngine().build_decision(
        {
            "home_name": "Arsenal",
            "away_name": "Chelsea",
            "probabilities": {"home": 70, "draw": 15, "away": 15},
            "confidence": 80,
            "odds": {"home": 1.9},
            "trust_score": 40,
            "data_completeness": {"tier": "strong"},
        }
    )
    assert decision["action"] in {"CONSIDER", "SKIP"}


def test_matchbrain_model_metrics_available():
    rows = [
        {"status": "completed", "confidence": 75, "is_correct": True},
        {"status": "completed", "confidence": 65, "is_correct": False},
        {"status": "pending", "confidence": 70, "is_correct": None},
    ]
    brain = MatchBrain(
        load_fixtures=lambda _league: ([], None, "configured", ""),
        get_fixture_by_id=lambda _mid: None,
        decision_engine=DecisionEngine(),
        tracker_recent=lambda _limit: rows,
    )
    metrics = brain.get_model_metrics()
    assert metrics["total_predictions"] == 3
    assert metrics["completed_predictions"] == 2
    assert "bucket_breakdown" in metrics


def test_system_intelligence_structure_and_health_failure_signal():
    rows = [
        {"status": "completed", "confidence": 75, "is_correct": True, "model_factors": {"canonical_snapshot": {"action": "BET", "confidence": 75}}},
        {"status": "completed", "confidence": 65, "is_correct": False, "model_factors": {"canonical_snapshot": {"action": "CONSIDER", "confidence": 65}}},
    ]
    brain = MatchBrain(
        load_fixtures=lambda _league: ([], None, "configured", ""),
        get_fixture_by_id=lambda _mid: None,
        decision_engine=DecisionEngine(),
        tracker_recent=lambda _limit: rows,
    )
    brain._api_status = "degraded"
    payload = brain.get_system_intelligence()
    assert "model_metrics" in payload
    assert "calibration" in payload
    assert "system_health" in payload
    assert "drift" in payload
    assert payload["system_health"]["degraded_mode"] is True
    assert "decision_quality" in payload


def test_system_intelligence_route_renders():
    canonical_payload = {
        "model_metrics": {"trust_score": 70, "calibration_score": 68, "win_rate": 55, "total_predictions": 10, "completed_predictions": 6},
        "calibration": {"buckets": [{"range": "70-80", "sample_size": 2, "predicted": 75, "actual": 60, "error": 15}]},
        "system_health": {"api_status": "ok", "last_refresh_time": None, "data_freshness": 10, "error_count": 0, "degraded_mode": False},
        "drift": {"drift_detected": False, "severity": "LOW", "reason": "stable performance", "short_window_metrics": {}, "long_window_metrics": {}},
        "decision_quality": {"bet_count": 1, "consider_count": 2, "skip_count": 3, "high_confidence_accuracy": 50},
        "safeguards": {"fallback_data_used": False, "stale_data_served": False, "trust_downgraded": False},
    }
    with patch.object(flask_app_module, "_MATCH_BRAIN") as brain:
        brain.get_system_intelligence.return_value = canonical_payload
        brain.refresh_cycle.return_value = None
        flask_app_module.app.config["TESTING"] = True
        with flask_app_module.app.test_client() as c:
            rv = c.get("/system-intelligence")
    assert rv.status_code == 200
    assert b"System Intelligence" in rv.data
    assert b"Calibration" in rv.data


def test_adaptive_thresholds_low_trust_reduce_bet_aggressiveness():
    rows = [{"status": "completed", "confidence": 60, "is_correct": False} for _ in range(25)]
    brain = MatchBrain(
        load_fixtures=lambda _league: ([], None, "configured", ""),
        get_fixture_by_id=lambda _mid: None,
        decision_engine=DecisionEngine(),
        tracker_recent=lambda _limit: rows,
    )
    metrics = {"trust_score": 40, "calibration_score": 50, "calibration_error": 20}
    thresholds = brain.get_adaptive_thresholds(model_metrics=metrics)
    assert thresholds["bet_min_confidence"] >= 70
    assert thresholds["max_risk_allowed"] == "LOW"


def test_adaptive_thresholds_high_trust_more_permissive():
    rows = [{"status": "completed", "confidence": 80, "is_correct": True} for _ in range(25)]
    brain = MatchBrain(
        load_fixtures=lambda _league: ([], None, "configured", ""),
        get_fixture_by_id=lambda _mid: None,
        decision_engine=DecisionEngine(),
        tracker_recent=lambda _limit: rows,
    )
    metrics = {"trust_score": 85, "calibration_score": 88, "calibration_error": 6}
    thresholds = brain.get_adaptive_thresholds(model_metrics=metrics)
    assert 60 <= thresholds["bet_min_confidence"] <= 64


def test_decision_engine_calibration_penalty_downgrades_action():
    decision = DecisionEngine().build_decision(
        {
            "home_name": "Arsenal",
            "away_name": "Chelsea",
            "probabilities": {"home": 68, "draw": 16, "away": 16},
            "confidence": 66,
            "odds": {"home": 2.0},
            "adaptive_thresholds": {"bet_min_confidence": 64, "consider_min_confidence": 54, "max_risk_allowed": "MEDIUM"},
            "calibration_error": 20,
            "trust_score": 60,
            "data_completeness": {"tier": "strong"},
        }
    )
    assert decision["adaptive_adjustment"]["calibration_penalty"] >= 2
    assert decision["action"] in {"CONSIDER", "SKIP"}


def test_adaptive_thresholds_clamped_within_bounds():
    rows = [{"status": "completed", "confidence": 99, "is_correct": False} for _ in range(40)]
    brain = MatchBrain(
        load_fixtures=lambda _league: ([], None, "configured", ""),
        get_fixture_by_id=lambda _mid: None,
        decision_engine=DecisionEngine(),
        tracker_recent=lambda _limit: rows,
    )
    thresholds = brain.get_adaptive_thresholds(model_metrics={"trust_score": 1, "calibration_score": 10, "calibration_error": 40})
    assert 60 <= thresholds["bet_min_confidence"] <= 85
    assert 50 <= thresholds["consider_min_confidence"] <= 75


def test_adaptive_thresholds_are_stable_between_calls():
    rows = [{"status": "completed", "confidence": 70, "is_correct": True} for _ in range(30)]
    brain = MatchBrain(
        load_fixtures=lambda _league: ([], None, "configured", ""),
        get_fixture_by_id=lambda _mid: None,
        decision_engine=DecisionEngine(),
        tracker_recent=lambda _limit: rows,
    )
    metrics = {"trust_score": 70, "calibration_score": 75, "calibration_error": 10}
    first = brain.get_adaptive_thresholds(model_metrics=metrics)
    second = brain.get_adaptive_thresholds(model_metrics=metrics)
    assert first == second


def test_feature_attribution_grouping_and_values():
    engine = FeatureAttributionEngine(min_samples=2)
    rows = [
        {"status": "completed", "is_correct": True, "model_factors": {"evaluation": {"features": {"form_score": 0.8, "attack_strength": 0.7}}}},
        {"status": "completed", "is_correct": True, "model_factors": {"evaluation": {"features": {"form_score": 0.75, "attack_strength": 0.65}}}},
        {"status": "completed", "is_correct": False, "model_factors": {"evaluation": {"features": {"form_score": 0.5, "attack_strength": 0.4}}}},
        {"status": "completed", "is_correct": False, "model_factors": {"evaluation": {"features": {"form_score": 0.55, "attack_strength": 0.45}}}},
    ]
    result = engine.summarize(rows)
    assert "form_score" in result["feature_impacts"]
    assert result["feature_impacts"]["form_score"]["signal"] == "positive"


def test_feature_attribution_low_sample_ignored():
    engine = FeatureAttributionEngine(min_samples=10)
    rows = [
        {"status": "completed", "is_correct": True, "model_factors": {"evaluation": {"features": {"form_score": 0.8}}}},
        {"status": "completed", "is_correct": False, "model_factors": {"evaluation": {"features": {"form_score": 0.4}}}},
    ]
    result = engine.summarize(rows)
    assert result["feature_impacts"] == {}


def test_feature_feedback_adjustment_within_bounds():
    decision = DecisionEngine().build_decision(
        {
            "home_name": "Arsenal",
            "away_name": "Chelsea",
            "probabilities": {"home": 60, "draw": 20, "away": 20},
            "confidence": 65,
            "feature_attribution": {
                "form_score": {"impact": 0.9, "sample_size": 50},
                "attack_strength": {"impact": 0.7, "sample_size": 40},
            },
            "data_completeness": {"tier": "strong"},
        }
    )
    assert 62 <= decision["confidence"] <= 68


def test_matchbrain_feature_attribution_exposed():
    rows = [
        {"status": "completed", "is_correct": True, "model_factors": {"evaluation": {"features": {"form_score": 0.8, "h2h_score": 0.7}}}},
        {"status": "completed", "is_correct": False, "model_factors": {"evaluation": {"features": {"form_score": 0.5, "h2h_score": 0.4}}}},
        {"status": "completed", "is_correct": True, "model_factors": {"evaluation": {"features": {"form_score": 0.75, "h2h_score": 0.65}}}},
        {"status": "completed", "is_correct": False, "model_factors": {"evaluation": {"features": {"form_score": 0.45, "h2h_score": 0.35}}}},
        {"status": "completed", "is_correct": True, "model_factors": {"evaluation": {"features": {"form_score": 0.72, "h2h_score": 0.62}}}},
        {"status": "completed", "is_correct": False, "model_factors": {"evaluation": {"features": {"form_score": 0.48, "h2h_score": 0.38}}}},
        {"status": "completed", "is_correct": True, "model_factors": {"evaluation": {"features": {"form_score": 0.79, "h2h_score": 0.69}}}},
        {"status": "completed", "is_correct": False, "model_factors": {"evaluation": {"features": {"form_score": 0.47, "h2h_score": 0.37}}}},
    ]
    brain = MatchBrain(
        load_fixtures=lambda _league: ([], None, "configured", ""),
        get_fixture_by_id=lambda _mid: None,
        decision_engine=DecisionEngine(),
        tracker_recent=lambda _limit: rows,
    )
    data = brain.get_feature_attribution()
    assert "top_positive_signals" in data
