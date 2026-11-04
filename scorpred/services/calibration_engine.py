from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from services.canonical_trust import compute as _canonical_trust, MIN_SAMPLES as _MIN_TRUST_SAMPLES

_BUCKETS: list[tuple[int, int, str]] = [
    (50, 60, "50-60"),
    (60, 70, "60-70"),
    (70, 80, "70-80"),
    (80, 90, "80-90"),
    (90, 101, "90-100"),
]

_STRING_TIER_MAP = {"high": 72.0, "medium": 61.0, "low": 52.0}


def _to_numeric_confidence(row: dict[str, Any]) -> float:
    raw = row.get("confidence")
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _STRING_TIER_MAP.get(str(raw).strip().lower(), 0.0)


@dataclass(slots=True)
class CalibrationEngine:
    min_samples: int = 20

    def build_snapshot(self, decision: dict[str, Any]) -> dict[str, Any]:
        probs = decision.get("probabilities") or {}
        return {
            "match_id": str(decision.get("match_id") or ""),
            "predicted_side": decision.get("recommended_side") or decision.get("side"),
            "confidence": int(round(float(decision.get("confidence") or 50))),
            "probabilities": {
                "home": probs.get("home"),
                "draw": probs.get("draw"),
                "away": probs.get("away"),
            },
            "edge_score": decision.get("edge_score"),
            "expected_value": decision.get("expected_value"),
            "data_quality": decision.get("data_quality"),
            "timestamp": decision.get("tracked_at"),
        }

    def evaluate_completed(self, row: dict[str, Any]) -> dict[str, Any]:
        confidence = int(round(float(row.get("confidence") or 50)))
        return {
            "actual_result": row.get("actual_result"),
            "is_correct": row.get("is_correct"),
            "confidence_bucket": self._bucket_for(confidence),
        }

    def get_model_metrics(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        total_predictions = len(rows)
        completed = [r for r in rows if str(r.get("status") or "").lower() == "completed" and r.get("is_correct") is not None]
        completed_predictions = len(completed)
        wins = sum(1 for r in completed if r.get("is_correct") is True)
        win_rate = (wins / completed_predictions * 100.0) if completed_predictions else None

        buckets: dict[str, dict[str, float | int | None]] = {label: {"sample_size": 0, "predicted": None, "actual": None, "error": None} for *_rng, label in _BUCKETS}
        errors = []
        for low, high, label in _BUCKETS:
            members = [r for r in completed if low <= int(round(_to_numeric_confidence(r))) < high]
            if not members:
                continue
            predicted = sum(_to_numeric_confidence(r) for r in members) / len(members)
            actual = sum(1 for r in members if r.get("is_correct") is True) / len(members) * 100.0
            err = abs(predicted - actual)
            errors.append(err)
            buckets[label] = {
                "sample_size": len(members),
                "predicted": round(predicted, 2),
                "actual": round(actual, 2),
                "error": round(err, 2),
            }

        if completed_predictions < self.min_samples or not errors:
            calibration_score: float | str = "insufficient data"
            cal_for_trust: float | None = None
        else:
            mae = sum(errors) / len(errors)
            calibration_score = round(max(0.0, 100.0 - mae), 2)
            cal_for_trust = calibration_score

        recent_accuracy = (win_rate / 100.0) if win_rate is not None else None
        if completed_predictions >= self.min_samples:
            # Respect engine's own min_samples threshold over the global gate.
            effective_size = max(completed_predictions, _MIN_TRUST_SAMPLES)
            trust_result = _canonical_trust(
                calibration_score=cal_for_trust,
                recent_accuracy=recent_accuracy,
                sample_size=effective_size,
            )
            trust_score: float = trust_result["trust_score"] or 0.0
        else:
            # Below threshold — compute a penalized score without calibration.
            acc = max(0.0, min(1.0, float(recent_accuracy or 0.0)))
            maturity = min(1.0, completed_predictions / 50.0)
            trust_score = round(acc * 100.0 * 0.30 + maturity * 100.0 * 0.10, 1)

        return {
            "total_predictions": total_predictions,
            "completed_predictions": completed_predictions,
            "win_rate": round(win_rate, 2) if win_rate is not None else None,
            "calibration_score": calibration_score,
            "trust_score": trust_score,
            "bucket_breakdown": buckets,
        }

    def _bucket_for(self, confidence: int) -> str:
        for low, high, label in _BUCKETS:
            if low <= confidence < high:
                return label
        return "90-100"
