"""Public façade for model trust scoring.  Delegates to canonical_trust."""
from __future__ import annotations

from typing import Any
from services.canonical_trust import compute


def compute_trust_score(
    *,
    calibration_score: float | None,
    recent_accuracy: float | None,
    average_data_quality: float | None = None,
    sample_size: int,
) -> dict[str, Any]:
    return compute(
        calibration_score=calibration_score,
        recent_accuracy=recent_accuracy,
        average_data_quality=average_data_quality,
        sample_size=sample_size,
    )
