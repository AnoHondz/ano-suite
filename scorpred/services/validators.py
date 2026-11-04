from __future__ import annotations

from typing import Any
import math


class ValidationError(ValueError):
    """Raised when request payload validation fails."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


def validate_bet_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationError("payload must be an object")
    data = dict(payload)
    match_id = str(data.get("match_id") or "").strip()
    recommended_side = str(data.get("recommended_side") or "").strip()
    action = str(data.get("action") or "").strip().upper()

    if not match_id:
        raise ValidationError("match_id is required")
    if not recommended_side:
        raise ValidationError("recommended_side is required")
    if action not in {"BET", "CONSIDER", "SKIP"}:
        raise ValidationError("action must be BET, CONSIDER, or SKIP")

    confidence_raw = data.get("confidence")
    if confidence_raw in (None, ""):
        raise ValidationError("confidence is required")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError("confidence must be numeric") from exc
    if not math.isfinite(confidence) or confidence < 0 or confidence > 100:
        raise ValidationError("confidence must be between 0 and 100")

    data["match_id"] = match_id
    data["recommended_side"] = recommended_side
    data["action"] = action
    data["confidence"] = confidence
    return data


def positive_int(value: Any, field: str) -> int:
    """Coerce *value* to a positive integer or raise ValidationError."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be an integer", field)
    if v <= 0:
        raise ValidationError(f"{field} must be a positive integer", field)
    return v


def year(value: Any, field: str, *, min_year: int = 2000, max_year: int = 2030) -> int:
    """Coerce *value* to a plausible season year or raise ValidationError."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be an integer", field)
    if v < min_year or v > max_year:
        raise ValidationError(f"{field} must be between {min_year} and {max_year}", field)
    return v
