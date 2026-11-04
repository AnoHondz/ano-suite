from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class FeatureAttributionEngine:
    min_samples: int = 8

    def summarize(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        completed = [r for r in rows if str(r.get("status") or "").lower() == "completed" and r.get("is_correct") is not None]
        by_feature: dict[str, dict[str, list[float]]] = {}

        for row in completed:
            snapshot = ((row.get("model_factors") or {}).get("evaluation") or {})
            features = snapshot.get("features") or {}
            if not isinstance(features, dict):
                continue
            bucket = "correct" if row.get("is_correct") is True else "wrong"
            for name, value in features.items():
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                store = by_feature.setdefault(name, {"correct": [], "wrong": []})
                store[bucket].append(numeric)

        impacts: dict[str, dict[str, Any]] = {}
        for feature, groups in by_feature.items():
            correct_vals = groups["correct"]
            wrong_vals = groups["wrong"]
            sample_size = len(correct_vals) + len(wrong_vals)
            if sample_size < self.min_samples or not correct_vals or not wrong_vals:
                continue
            correct_avg = sum(correct_vals) / len(correct_vals)
            wrong_avg = sum(wrong_vals) / len(wrong_vals)
            diff = correct_avg - wrong_avg
            impacts[feature] = {
                "correct_avg": round(correct_avg, 4),
                "wrong_avg": round(wrong_avg, 4),
                "impact": round(diff, 4),
                "signal": "positive" if diff > 0 else "negative" if diff < 0 else "neutral",
                "sample_size": sample_size,
            }

        ordered = sorted(impacts.items(), key=lambda kv: abs(float(kv[1].get("impact") or 0.0)), reverse=True)
        top_positive = [{"feature": k, **v} for k, v in ordered if (v.get("impact") or 0) > 0][:5]
        top_negative = [{"feature": k, **v} for k, v in ordered if (v.get("impact") or 0) < 0][:5]
        neutral = [{"feature": k, **v} for k, v in ordered if (v.get("impact") or 0) == 0][:5]

        return {
            "feature_impacts": impacts,
            "top_positive_signals": top_positive,
            "top_negative_signals": top_negative,
            "neutral_signals": neutral,
        }
