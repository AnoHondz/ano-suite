from __future__ import annotations

from services.decision_engine import DecisionEngine
from services.drift_engine import DriftEngine
from services.match_brain import MatchBrain


def _row(idx: int, *, correct: bool | str, confidence: int = 65) -> dict:
    day = f"{idx + 1:03d}"
    return {
        "status": "completed",
        "is_correct": correct,
        "date": f"2026-04-{day}",
        "model_factors": {"canonical_snapshot": {"confidence": confidence, "tracked_at": f"2026-04-{day}T12:00:00Z"}},
    }


def test_no_drift_when_performance_stable():
    rows = [_row(i, correct=(i % 3 != 0), confidence=62 + (i % 7)) for i in range(40)]
    status = DriftEngine().evaluate(rows)
    assert status["drift_detected"] is False
    assert status["short_window_metrics"]["win_rate"] >= 0.45


def test_drift_detected_when_recent_performance_drops():
    rows = []
    rows.extend(_row(i, correct=(i % 5 != 0), confidence=68) for i in range(30))  # strong long window
    rows.extend(_row(30 + i, correct=(i % 4 == 0), confidence=66) for i in range(20))  # weak recent
    status = DriftEngine().evaluate(rows)
    assert status["drift_detected"] is True
    assert status["severity"] in {"MEDIUM", "HIGH"}
    assert status["short_window_metrics"]["win_rate"] < status["long_window_metrics"]["win_rate"]


def test_calibration_spike_triggers_drift():
    rows = []
    rows.extend(_row(i, correct=(i % 10 != 0), confidence=60) for i in range(30))
    rows.extend(_row(30 + i, correct=(i % 5 == 0), confidence=92) for i in range(20))
    status = DriftEngine().evaluate(rows)
    assert status["drift_detected"] is True
    assert (status.get("calibration_change") or 0) > 0.08


def test_decision_engine_reduces_aggressive_actions_on_drift():
    engine = DecisionEngine()
    decision = engine.build_decision(
        {
            "home_name": "Arsenal",
            "away_name": "Chelsea",
            "probabilities": {"home": 69, "draw": 15, "away": 16},
            "confidence": 74,
            "odds": {"home": 2.3},
            "data_completeness": {"tier": "strong"},
            "drift_status": {
                "drift_detected": True,
                "severity": "HIGH",
                "short_window_metrics": {"win_rate": 0.35},
                "long_window_metrics": {"win_rate": 0.6},
                "calibration_change": 0.1,
            },
        }
    )
    assert decision["adaptive_adjustment"]["drift_detected"] is True
    assert decision["action"] in {"CONSIDER", "SKIP"}


def test_low_sample_size_does_not_trigger_drift_or_crash():
    rows = [_row(i, correct=(i % 2 == 0), confidence=65) for i in range(8)]
    status = DriftEngine().evaluate(rows)
    assert status["drift_detected"] is False
    assert "insufficient" in status["reason"]

    brain = MatchBrain(
        load_fixtures=lambda _league: ([], None, "configured", ""),
        get_fixture_by_id=lambda _mid: None,
        decision_engine=DecisionEngine(),
        tracker_recent=lambda _limit: rows,
    )
    payload = brain.get_system_intelligence()
    assert "drift" in payload
    assert payload["drift"]["drift_detected"] is False


def test_matchbrain_drift_status_is_stable_for_unchanged_rows():
    rows = []
    rows.extend(_row(i, correct=(i % 5 != 0), confidence=68) for i in range(30))
    rows.extend(_row(30 + i, correct=(i % 4 == 0), confidence=66) for i in range(20))
    brain = MatchBrain(
        load_fixtures=lambda _league: ([], None, "configured", ""),
        get_fixture_by_id=lambda _mid: None,
        decision_engine=DecisionEngine(),
        tracker_recent=lambda _limit: rows,
    )
    first = brain.get_drift_status()
    second = brain.get_drift_status()
    assert first == second
