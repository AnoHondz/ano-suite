"""Shared selector helpers for soccer backtests and runtime decisions."""

from __future__ import annotations

from typing import Any

SEGMENT_METADATA: dict[str, dict[str, str]] = {
    "draw_candidate": {
        "label": "Draw Candidate",
        "description": "Draw probability is close enough to side outcomes that draw handling matters.",
    },
    "model_disagreement": {
        "label": "Signal Disagreement",
        "description": "Rule and ML signals disagree on the likely winner.",
    },
    "close_match": {
        "label": "Close Match",
        "description": "Top outcomes are tightly clustered, so small modeling differences matter more.",
    },
    "strong_edge": {
        "label": "Strong Edge",
        "description": "One outcome has a clear lead over the alternatives.",
    },
    "agreement": {
        "label": "Signal Agreement",
        "description": "Rule and ML signals align on the same outcome.",
    },
}

SEGMENT_PRIORITY = [
    "draw_candidate",
    "model_disagreement",
    "close_match",
    "strong_edge",
    "agreement",
]

VALID_SOURCES = ("rule", "ml", "combined")


def _normalize_probs(probabilities: list[float] | tuple[float, ...] | None) -> list[float]:
    values = list(probabilities or [])
    if len(values) != 3:
        return [0.3333, 0.3333, 0.3334]
    total = sum(max(float(value), 0.0) for value in values) or 1.0
    return [max(float(value), 0.0) / total for value in values]


def _top_index(probabilities: list[float] | tuple[float, ...] | None) -> int:
    values = _normalize_probs(probabilities)
    return int(max(range(len(values)), key=lambda idx: values[idx]))


def normalize_source(source: Any) -> str | None:
    value = str(source or "").strip().lower()
    if value in VALID_SOURCES:
        return value
    return None


def source_label(source: Any) -> str:
    normalized = normalize_source(source)
    if normalized == "rule":
        return "Rule"
    if normalized == "ml":
        return "Ensemble ML"
    if normalized == "combined":
        return "Combined"
    return "Unknown"


def build_segment_flags(
    rule_probs: list[float] | tuple[float, ...] | None,
    ml_probs: list[float] | tuple[float, ...] | None,
    combined_probs: list[float] | tuple[float, ...] | None,
) -> dict[str, bool]:
    """Return boolean segment flags shared by offline and runtime selection."""
    rule_values = _normalize_probs(rule_probs)
    ml_values = _normalize_probs(ml_probs) if ml_probs else []
    combined_values = _normalize_probs(combined_probs)

    sorted_combined = sorted(combined_values, reverse=True)
    top_prob = sorted_combined[0]
    second_prob = sorted_combined[1] if len(sorted_combined) > 1 else top_prob
    gap = max(0.0, top_prob - second_prob)

    draw_peak = max(
        rule_values[1],
        ml_values[1] if ml_values else 0.0,
        combined_values[1],
    )

    rule_top = _top_index(rule_values)
    combined_top = _top_index(combined_values)
    ml_top = _top_index(ml_values) if ml_values else None

    draw_candidate = draw_peak >= 0.31 and combined_values[1] >= (top_prob - 0.04)
    model_disagreement = ml_top is not None and ml_top != rule_top
    close_match = gap <= 0.06 or top_prob <= 0.52
    strong_edge = top_prob >= 0.60 or gap >= 0.12
    agreement = ml_top is not None and ml_top == rule_top == combined_top

    return {
        "draw_candidate": draw_candidate,
        "model_disagreement": model_disagreement,
        "close_match": close_match,
        "strong_edge": strong_edge,
        "agreement": agreement,
    }


def matched_segments(flags: dict[str, bool] | None) -> list[str]:
    return [segment for segment in SEGMENT_PRIORITY if bool((flags or {}).get(segment))]


def choose_source(
    selector_profile: dict[str, Any] | None,
    *,
    flags: dict[str, bool] | None,
    available_sources: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Pick the runtime source using recent backtest defaults plus safe overrides."""
    available = {
        source
        for source in (available_sources or VALID_SOURCES)
        if normalize_source(source)
    }
    if not available:
        available = {"combined", "rule", "ml"}

    profile = selector_profile or {}
    default_source = normalize_source(profile.get("default_source"))
    if default_source not in available:
        for fallback in ("combined", "ml", "rule"):
            if fallback in available:
                default_source = fallback
                break
    if default_source is None:
        default_source = "combined"

    for override in profile.get("overrides") or []:
        segment = str(override.get("segment") or "").strip()
        preferred_source = normalize_source(override.get("preferred_source"))
        if not segment or preferred_source not in available:
            continue
        if bool((flags or {}).get(segment)):
            return {
                "source": preferred_source,
                "source_label": source_label(preferred_source),
                "segment": segment,
                "segment_label": SEGMENT_METADATA.get(segment, {}).get("label", segment.replace("_", " ").title()),
                "used_override": True,
                "reason": override.get("reason")
                or SEGMENT_METADATA.get(segment, {}).get("description")
                or "Recent backtest prefers this source for the current segment.",
            }

    return {
        "source": default_source,
        "source_label": source_label(default_source),
        "segment": None,
        "segment_label": None,
        "used_override": False,
        "reason": profile.get("summary") or "Using the recent backtest winner as the default signal.",
    }
