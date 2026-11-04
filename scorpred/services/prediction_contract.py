"""Prediction contract — schema gating for canonical analysis objects.

All analysis objects must pass validate_analysis_contract before being
written to any cache or rendered into any UI template.  Use
safe_validate() for non-raising validation that fails to unavailable state.
"""
from __future__ import annotations

from typing import Any
import logging


REQUIRED_ANALYSIS_FIELDS = (
    "match_id",
    "action",
    "recommended_side",
    "confidence",
    "probabilities",
    "reason",
    "data_quality",
    "metric_breakdown",
)

# Confidence 53 is a valid model output on its own; only flag it when paired
# with a known fake probability pattern.
_FORBIDDEN_CONFIDENCE_WITH_FAKE_PROBS: set = {53}
# Keep old name for any external imports — now always empty.
FORBIDDEN_CONFIDENCE_DEFAULTS: set = set()
FORBIDDEN_PROBABILITY_PATTERNS = [
    {"a": 38, "draw": 26, "b": 36},
    {"home": 38, "draw": 26, "away": 36},
]

_logger = logging.getLogger(__name__)


def validate_analysis_contract(analysis: dict[str, Any] | None) -> list[str]:
    """Return a list of contract-violation strings (empty = valid)."""
    errors: list[str] = []
    if not isinstance(analysis, dict):
        return ["analysis must be a dict"]

    for field in REQUIRED_ANALYSIS_FIELDS:
        if field not in analysis:
            errors.append(f"missing required field: {field}")

    confidence = analysis.get("confidence")
    if not isinstance(confidence, (int, float)):
        errors.append("confidence must be numeric")

    probs = analysis.get("probabilities")
    if not isinstance(probs, dict):
        errors.append("probabilities must be a dict")
    else:
        keys = set(probs.keys())
        if keys not in ({"a", "draw", "b"}, {"home", "draw", "away"}):
            errors.append("probabilities keys must be either {a,draw,b} or {home,draw,away}")
        if probs in FORBIDDEN_PROBABILITY_PATTERNS:
            errors.append("forbidden probability fallback pattern detected")
            if isinstance(confidence, (int, float)) and confidence in _FORBIDDEN_CONFIDENCE_WITH_FAKE_PROBS:
                errors.append(f"forbidden confidence+probability combination detected: conf={confidence}")

    metric_breakdown = analysis.get("metric_breakdown")
    if metric_breakdown not in (None, "Unavailable"):
        if _looks_fake_5050(metric_breakdown):
            errors.append("metric_breakdown appears to use forbidden fake 50/50 defaults")

    return errors


def validate_analysis(analysis: dict[str, Any] | None) -> dict[str, Any]:
    """Validate or raise ValueError — use at strict ingress boundaries."""
    errors = validate_analysis_contract(analysis)
    if errors:
        message = "; ".join(errors)
        _logger.error("invalid_analysis_contract: %s", message, extra={"errors": errors})
        raise ValueError(message)
    return analysis  # type: ignore[return-value]


def safe_validate(analysis: dict[str, Any] | None, *, context: str = "") -> dict[str, Any] | None:
    """Validate and return the analysis, or None if it fails contract.

    Use this at cache-write and render-boundary checkpoints to prevent
    invalid shapes reaching the UI without crashing the request.
    """
    if analysis is None:
        return None
    errors = validate_analysis_contract(analysis)
    if errors:
        _logger.warning(
            "contract_violation context=%s errors=%s",
            context or "unknown",
            "; ".join(errors),
        )
        return None
    return analysis


def _looks_fake_5050(metric_breakdown: Any) -> bool:
    if not isinstance(metric_breakdown, dict):
        return False
    values: list[tuple[float, float]] = []
    for metric in metric_breakdown.values():
        if not isinstance(metric, dict):
            continue
        home = metric.get("home") if "home" in metric else metric.get("a")
        away = metric.get("away") if "away" in metric else metric.get("b")
        if isinstance(home, (int, float)) and isinstance(away, (int, float)):
            values.append((float(home), float(away)))
    if not values:
        return False
    return all(home == 50.0 and away == 50.0 for home, away in values)
