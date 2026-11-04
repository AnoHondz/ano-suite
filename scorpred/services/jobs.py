from __future__ import annotations

from typing import Any


def precompute_predictions(*, league_id: int | None = None) -> dict[str, Any]:
    """Async-ready stub for future Celery/RQ prediction precompute tasks."""
    return {"queued": False, "job": "precompute_predictions", "league_id": league_id}


def warm_prediction_cache(*, league_id: int | None = None) -> dict[str, Any]:
    """Async-ready stub for future Celery/RQ cache warming tasks."""
    return {"queued": False, "job": "warm_prediction_cache", "league_id": league_id}
