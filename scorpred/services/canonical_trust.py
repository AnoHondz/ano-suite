"""Canonical trust-score calculator — single source of truth.

Formula  : 40% calibration quality + 30% recent accuracy + 20% data quality + 10% sample maturity
Min gate : MIN_SAMPLES evaluated matches required before reporting a score
Labels   : Elite (≥85) · Strong (≥70) · Developing (≥55) · Low (<55)
"""
from __future__ import annotations

from typing import Any

MIN_SAMPLES: int = 10

_LABEL_THRESHOLDS = (
    (85.0, "Elite Trust"),
    (70.0, "Strong Trust"),
    (55.0, "Developing Trust"),
    (0.0, "Low Trust"),
)


def compute(
    *,
    calibration_score: float | None,
    recent_accuracy: float | None,
    average_data_quality: float | None = None,
    sample_size: int,
) -> dict[str, Any]:
    """Return canonical trust dict.

    When sample_size < MIN_SAMPLES the score is None and label is
    "Insufficient Data" — callers must handle this case gracefully.
    """
    if sample_size < MIN_SAMPLES:
        return {
            "trust_score": None,
            "label": "Insufficient Data",
            "sample_size": sample_size,
        }

    cal = max(0.0, min(100.0, float(calibration_score or 0.0)))
    acc = max(0.0, min(1.0, float(recent_accuracy or 0.0)))
    dq = max(0.0, min(100.0, float(average_data_quality or 0.0)))
    sample_maturity = min(1.0, sample_size / 50.0)

    raw = (
        cal * 0.40
        + acc * 100.0 * 0.30
        + dq * 0.20
        + sample_maturity * 100.0 * 0.10
    )
    score = round(max(0.0, min(100.0, raw)), 1)
    label = _label(score)
    return {
        "trust_score": score,
        "label": label,
        "sample_size": sample_size,
    }


def _label(score: float) -> str:
    for threshold, label in _LABEL_THRESHOLDS:
        if score >= threshold:
            return label
    return "Low Trust"
