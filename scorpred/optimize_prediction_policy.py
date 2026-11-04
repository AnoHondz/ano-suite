"""Tune prediction-play thresholds from finalized historical outcomes.

Usage:
    c:/Dev/scorpred/.venv/Scripts/python.exe optimize_prediction_policy.py
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

import model_tracker as mt
import prediction_policy as policy_store


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _confidence_percent(pred: dict[str, Any]) -> float:
    raw = pred.get("confidence_pct")
    if isinstance(raw, (int, float)):
        val = float(raw)
        if 0.0 <= val <= 1.0:
            val *= 100.0
        return max(0.0, min(100.0, val))

    label = str(pred.get("confidence") or "").strip().lower()
    if label == "high":
        return 75.0
    if label == "medium":
        return 57.5
    if label == "low":
        return 42.5

    probs = [
        _safe_float(pred.get("prob_a"), 0.0),
        _safe_float(pred.get("prob_b"), 0.0),
        _safe_float(pred.get("prob_draw"), 0.0),
    ]
    top = max(probs)
    if top <= 1.0:
        top *= 100.0
    return max(0.0, min(100.0, top))


def _top_two_gap_pct(pred: dict[str, Any]) -> float:
    probs = [
        _safe_float(pred.get("prob_a"), 0.0),
        _safe_float(pred.get("prob_b"), 0.0),
        _safe_float(pred.get("prob_draw"), 0.0),
    ]
    if max(probs) <= 1.0:
        probs = [p * 100.0 for p in probs]
    probs.sort(reverse=True)
    if len(probs) < 2:
        return 0.0
    return max(0.0, probs[0] - probs[1])


def _is_avoid_pick(pred: dict[str, Any]) -> bool:
    value = str(pred.get("predicted_winner") or "").strip().lower()
    return value in {"avoid", "skip", "pass", "no bet"}


def _is_win(pred: dict[str, Any]) -> bool:
    if pred.get("winner_hit") is not None:
        return bool(pred.get("winner_hit"))
    return bool(pred.get("is_correct"))


def _evaluate_policy(predictions: list[dict[str, Any]], cfg: dict[str, float]) -> dict[str, float]:
    total = len(predictions)
    if total == 0:
        return {
            "score": 0.0,
            "coverage_pct": 0.0,
            "hit_rate_pct": 0.0,
            "unit_rate_pct": 0.0,
            "placed": 0,
            "wins": 0,
            "losses": 0,
            "avoided": 0,
        }

    placed = 0
    wins = 0
    losses = 0

    for pred in predictions:
        if _is_avoid_pick(pred):
            continue

        confidence = _confidence_percent(pred)
        gap = _top_two_gap_pct(pred)
        if confidence < cfg["min_confidence_pct"] or gap < cfg["min_top_two_gap_pct"]:
            continue

        placed += 1
        if _is_win(pred):
            wins += 1
        else:
            losses += 1

    avoided = total - placed
    coverage = (placed / total) * 100.0 if total else 0.0
    hit_rate = (wins / placed) * 100.0 if placed else 0.0
    unit_rate = ((wins - losses) / total) * 100.0 if total else 0.0

    # Balance quality and volume; heavily penalize tiny-sample overfitting.
    score = hit_rate * 0.72 + coverage * 0.18 + unit_rate * 0.10
    if placed < max(8, int(total * 0.12)):
        score -= 8.0

    return {
        "score": round(score, 3),
        "coverage_pct": round(coverage, 2),
        "hit_rate_pct": round(hit_rate, 2),
        "unit_rate_pct": round(unit_rate, 2),
        "placed": placed,
        "wins": wins,
        "losses": losses,
        "avoided": avoided,
    }


# Blend weight candidates swept separately from the threshold grid.
# The threshold optimizer cannot evaluate different blend weights because
# tracked-prediction confidences are already baked in at the blend weight
# that was active when each prediction was made.  Instead we select the
# blend weight from finalized performance: higher hit rate → trust the ML
# signal more; lower hit rate → reduce ML influence on the combined output.
_BLEND_WEIGHT_CANDIDATES: list[float] = [0.2, 0.3, 0.4, 0.5]


def _select_blend_weight(
    sport_predictions: list[dict[str, Any]],
    candidates: list[float] = _BLEND_WEIGHT_CANDIDATES,
    default: float = 0.4,
) -> float:
    """Pick the best blend weight from candidates using finalized hit-rate.

    Maps hit-rate bands → weight:
        ≥60%  → 0.5  (ML is clearly adding value, increase its share)
        ≥55%  → 0.4  (solid performance, keep current default)
        ≥50%  → 0.3  (marginal, pull back slightly on ML)
        <50%  → 0.2  (below par, minimise ML contribution)
    Falls back to *default* when the sample is too small to be reliable (<8).
    """
    non_avoid = [p for p in sport_predictions if not _is_avoid_pick(p)]
    if len(non_avoid) < 8:
        return default
    wins = sum(1 for p in non_avoid if _is_win(p))
    hit_rate = wins / len(non_avoid)

    if hit_rate >= 0.60:
        return max(candidates)   # 0.5
    if hit_rate >= 0.55:
        # prefer 0.4 if available
        return next((c for c in candidates if c == 0.4), candidates[-2])
    if hit_rate >= 0.50:
        return next((c for c in candidates if c == 0.3), candidates[1])
    return min(candidates)       # 0.2


def _candidate_grid() -> list[dict[str, float]]:
    grid: list[dict[str, float]] = []
    for min_conf in range(49, 68, 2):
        for min_gap in range(2, 13):
            for lean_min in range(49, 61, 2):
                for bet_min in range(64, 80, 2):
                    if lean_min >= bet_min:
                        continue
                    grid.append(
                        {
                            "min_confidence_pct": float(min_conf),
                            "min_top_two_gap_pct": float(min_gap),
                            "lean_min_confidence_pct": float(lean_min),
                            "bet_min_confidence_pct": float(bet_min),
                            "draw_min_top_prob_pct": 37.0,
                        }
                    )
    return grid


def _best_policy_for_sport(
    predictions: list[dict[str, Any]],
    sport: str,
    default_blend: float = 0.4,
) -> dict[str, Any]:
    scoped = [
        pred for pred in predictions
        if str(pred.get("sport") or "").strip().lower() == sport
    ]

    defaults = policy_store.default_policy()["sports"][sport]
    blend_weight = _select_blend_weight(scoped, default=default_blend)

    if len(scoped) < 16:
        return {
            "config": defaults,
            "blend_weight": blend_weight,
            "metrics": _evaluate_policy(scoped, defaults),
            "sample_size": len(scoped),
            "note": "Insufficient finalized sample for reliable tuning; defaults retained.",
        }

    best_cfg = defaults
    best_metrics = _evaluate_policy(scoped, defaults)
    best_score = best_metrics["score"]

    for candidate in _candidate_grid():
        metrics = _evaluate_policy(scoped, candidate)
        if metrics["score"] > best_score:
            best_score = metrics["score"]
            best_cfg = candidate
            best_metrics = metrics

    return {
        "config": best_cfg,
        "blend_weight": blend_weight,
        "metrics": best_metrics,
        "sample_size": len(scoped),
        "note": "Tuned from finalized tracked predictions.",
    }


def build_tuned_policy(predictions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    finalized = predictions if predictions is not None else mt.get_completed_predictions(limit=5000)

    soccer = _best_policy_for_sport(finalized, "soccer", default_blend=0.4)
    nba    = _best_policy_for_sport(finalized, "nba",    default_blend=0.3)

    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        # ── ML blend weights (swept from [0.2, 0.3, 0.4, 0.5] by _select_blend_weight) ──
        "soccer_ml_blend_weight": soccer["blend_weight"],
        "nba_ml_blend_weight":    nba["blend_weight"],
        "sports": {
            "soccer": soccer["config"],
            "nba": nba["config"],
        },
        "metadata": {
            "source": "backtest_optimizer",
            "sample_size": len(finalized),
            "sports": {
                "soccer": {
                    "sample_size": soccer["sample_size"],
                    "metrics": soccer["metrics"],
                    "note": soccer["note"],
                },
                "nba": {
                    "sample_size": nba["sample_size"],
                    "metrics": nba["metrics"],
                    "note": nba["note"],
                },
            },
        },
    }
    return payload


def main() -> int:
    payload = build_tuned_policy()
    policy_store.save_policy(payload)

    sport_meta = payload.get("metadata", {}).get("sports", {})
    soccer_meta = sport_meta.get("soccer", {})
    nba_meta = sport_meta.get("nba", {})

    print("Prediction policy tuned and saved.")
    print(json.dumps(payload.get("sports"), indent=2))
    print(
        "Backtest summary:",
        json.dumps(
            {
                "soccer": {
                    "sample_size": soccer_meta.get("sample_size"),
                    "metrics": soccer_meta.get("metrics"),
                },
                "nba": {
                    "sample_size": nba_meta.get("sample_size"),
                    "metrics": nba_meta.get("metrics"),
                },
            },
            indent=2,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
