"""
Mistake analysis module for ScorPred.

Analyses completed predictions to classify mistakes into categories,
compute category-level stats, and generate bounded policy adjustments
that ScorMastermind can apply at runtime.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_paths import mistake_report_path, policy_adjustments_path

logger = logging.getLogger(__name__)

# ── Mistake categories ──────────────────────────────────────────────────────

MISTAKE_CATEGORIES = [
    "draw_underestimated",
    "home_bias_overconfidence",
    "away_bias_underconfidence",
    "low_data_miss",
    "high_confidence_miss",
    "weak_edge_bet",
    "balanced_match_overcommit",
    "recency_bias",              # thin home-team history (form_a_length < 5)
    "popular_team_overrating",   # predicted high-ELO side but lost (elo_diff > 50)
]

# ── Bounded adjustment limits ───────────────────────────────────────────────
# Each adjustment is capped to these ranges to prevent runaway drift.

_ADJUSTMENT_BOUNDS: dict[str, tuple[float, float]] = {
    "confidence_penalty_pct": (-10.0, 0.0),
    "draw_boost_pct": (0.0, 8.0),
    "gap_penalty_pct": (-5.0, 0.0),
}

_MIN_SAMPLE_SIZE = 5  # Need at least this many mistakes in a category to propose adjustment


# ── Classification ──────────────────────────────────────────────────────────

def classify_mistake(pred: dict) -> list[str]:
    """Return a list of mistake category tags for a single wrong prediction.

    Expects a graded prediction dict from model_tracker with at minimum:
        is_correct, predicted_winner, actual_result_normalized,
        prob_a, prob_b, prob_draw, confidence
    """
    if pred.get("is_correct") is not False:
        return []

    tags: list[str] = []
    prob_a = pred.get("prob_a", 0) or 0
    prob_b = pred.get("prob_b", 0) or 0
    prob_draw = pred.get("prob_draw", 0) or 0
    predicted = (pred.get("predicted_winner_normalized") or pred.get("predicted_winner") or "").upper()
    actual = (pred.get("actual_result_normalized") or "").upper()
    confidence = (pred.get("confidence") or "").capitalize()
    sport = (pred.get("sport") or "").lower()

    top_prob = max(prob_a, prob_b, prob_draw)
    gap = top_prob - sorted([prob_a, prob_b, prob_draw])[-2] if top_prob > 0 else 0

    # draw_underestimated: predicted A or B but actual was draw
    if actual == "DRAW" and predicted in ("A", "B"):
        tags.append("draw_underestimated")

    # home_bias_overconfidence: predicted home (A) with high confidence, lost
    if predicted == "A" and confidence == "High" and actual != "A":
        tags.append("home_bias_overconfidence")

    # away_bias_underconfidence: actual was B (away win) but we predicted A or draw
    if actual == "B" and predicted != "B":
        tags.append("away_bias_underconfidence")

    # high_confidence_miss: any High-confidence prediction that was wrong
    if confidence == "High":
        tags.append("high_confidence_miss")

    # weak_edge_bet: gap between top two probs was < 5%
    if gap < 5:
        tags.append("weak_edge_bet")

    # balanced_match_overcommit: all three probs within 15% of each other
    probs = [prob_a, prob_b, prob_draw] if sport == "soccer" else [prob_a, prob_b]
    if probs and (max(probs) - min(probs)) < 15:
        tags.append("balanced_match_overcommit")

    # low_data_miss: confidence was Low (usually from limited data)
    if confidence == "Low":
        tags.append("low_data_miss")

    # recency_bias: home team had thin history (< 5 matches) at time of prediction
    form_a_length = pred.get("form_a_length")
    if form_a_length is not None and int(form_a_length) < 5:
        tags.append("recency_bias")

    # popular_team_overrating: predicted the higher-ELO team (elo_diff > 50) but lost
    elo_diff = pred.get("elo_diff")
    if elo_diff is not None:
        try:
            elo_diff_val = float(elo_diff)
        except (TypeError, ValueError):
            elo_diff_val = 0.0
        # elo_diff > 50 means home team had clear ELO advantage; predicted home but lost
        if elo_diff_val > 50 and predicted == "A":
            tags.append("popular_team_overrating")
        # elo_diff < -50 means away team had clear advantage; predicted away but lost
        elif elo_diff_val < -50 and predicted == "B":
            tags.append("popular_team_overrating")

    return tags


# ── Report generation ───────────────────────────────────────────────────────

def build_mistake_report(completed_predictions: list[dict]) -> dict:
    """Analyse completed predictions and build a structured mistake report.

    Returns a dict with:
        generated_at, total_analysed, total_correct, total_wrong,
        accuracy_pct, categories: {category: {count, rate, examples}}
    """
    wrong = [p for p in completed_predictions if p.get("is_correct") is False]
    correct = [p for p in completed_predictions if p.get("is_correct") is True]

    category_buckets: dict[str, list[dict]] = {cat: [] for cat in MISTAKE_CATEGORIES}

    for pred in wrong:
        tags = classify_mistake(pred)
        for tag in tags:
            if tag in category_buckets:
                category_buckets[tag].append({
                    "id": pred.get("id"),
                    "date": pred.get("date") or pred.get("game_date"),
                    "sport": pred.get("sport"),
                    "team_a": pred.get("team_a"),
                    "team_b": pred.get("team_b"),
                    "predicted": pred.get("predicted_winner"),
                    "actual": pred.get("actual_result_normalized") or pred.get("actual_result"),
                    "confidence": pred.get("confidence"),
                    "prob_a": pred.get("prob_a"),
                    "prob_b": pred.get("prob_b"),
                    "prob_draw": pred.get("prob_draw"),
                    "tags": tags,
                })

    total = len(correct) + len(wrong)
    categories_summary = {}
    for cat, examples in category_buckets.items():
        categories_summary[cat] = {
            "count": len(examples),
            "rate": round(len(examples) / total * 100, 1) if total else 0,
            "examples": examples[:5],  # Keep top 5 for readability
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_analysed": total,
        "total_correct": len(correct),
        "total_wrong": len(wrong),
        "accuracy_pct": round(len(correct) / total * 100, 1) if total else None,
        "categories": categories_summary,
    }


# ── Policy adjustment proposals ─────────────────────────────────────────────

def propose_adjustments(report: dict) -> dict:
    """Given a mistake report, propose bounded policy adjustments.

    Returns a dict with:
        generated_at, adjustments: {sport: {threshold_key: delta}},
        reasoning: [str]
    """
    categories = report.get("categories", {})
    total = report.get("total_analysed", 0)
    reasoning: list[str] = []
    adjustments: dict[str, dict[str, float]] = {"soccer": {}, "nba": {}}

    if total < _MIN_SAMPLE_SIZE:
        reasoning.append(f"Insufficient data ({total} predictions). No adjustments proposed.")
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "based_on_report": report.get("generated_at"),
            "adjustments": adjustments,
            "reasoning": reasoning,
        }

    # Rule 1: draw_underestimated → boost draw_min_top_prob_pct
    draw_under = categories.get("draw_underestimated", {})
    if draw_under.get("count", 0) >= _MIN_SAMPLE_SIZE:
        rate = draw_under["rate"]
        # Scale: 1% boost per 5% rate, capped
        boost = _clamp(rate / 5.0, *_ADJUSTMENT_BOUNDS["draw_boost_pct"])
        adjustments["soccer"]["draw_min_top_prob_pct"] = round(boost, 1)
        reasoning.append(
            f"Draw underestimated in {draw_under['count']} predictions ({rate}%). "
            f"Proposing draw threshold boost of +{boost:.1f}%."
        )

    # Rule 2: high_confidence_miss → penalise bet threshold
    high_miss = categories.get("high_confidence_miss", {})
    if high_miss.get("count", 0) >= _MIN_SAMPLE_SIZE:
        rate = high_miss["rate"]
        penalty = _clamp(-rate / 10.0, *_ADJUSTMENT_BOUNDS["confidence_penalty_pct"])
        for sport in ("soccer", "nba"):
            adjustments[sport]["bet_min_confidence_pct"] = round(-penalty, 1)  # positive = raise bar
        reasoning.append(
            f"High-confidence misses: {high_miss['count']} ({rate}%). "
            f"Proposing bet threshold raise of +{-penalty:.1f}%."
        )

    # Rule 3: weak_edge_bet → widen min gap
    weak_edge = categories.get("weak_edge_bet", {})
    if weak_edge.get("count", 0) >= _MIN_SAMPLE_SIZE:
        rate = weak_edge["rate"]
        widen = _clamp(rate / 10.0, 0.0, 5.0)
        for sport in ("soccer", "nba"):
            adjustments[sport]["min_top_two_gap_pct"] = round(widen, 1)
        reasoning.append(
            f"Weak-edge bets: {weak_edge['count']} ({rate}%). "
            f"Proposing gap threshold raise of +{widen:.1f}%."
        )

    # Rule 4: balanced_match_overcommit → raise lean threshold
    balanced = categories.get("balanced_match_overcommit", {})
    if balanced.get("count", 0) >= _MIN_SAMPLE_SIZE:
        rate = balanced["rate"]
        raise_lean = _clamp(rate / 8.0, 0.0, 5.0)
        for sport in ("soccer", "nba"):
            adjustments[sport]["lean_min_confidence_pct"] = round(raise_lean, 1)
        reasoning.append(
            f"Balanced-match overcommits: {balanced['count']} ({rate}%). "
            f"Proposing lean threshold raise of +{raise_lean:.1f}%."
        )

    # Rule 5: recency_bias → raise min_confidence_pct for soccer (thin home history)
    recency = categories.get("recency_bias", {})
    if recency.get("count", 0) >= _MIN_SAMPLE_SIZE:
        rate = recency["rate"]
        if rate > 20.0:
            adjustments["soccer"]["min_confidence_pct"] = 5.0
            reasoning.append(
                f"Recency bias detected in {recency['count']} predictions ({rate}%). "
                "Proposing +5% min_confidence_pct for soccer when home team history is thin."
            )

    # Rule 6: popular_team_overrating → penalise bet threshold for soccer
    popular = categories.get("popular_team_overrating", {})
    if popular.get("count", 0) >= _MIN_SAMPLE_SIZE:
        rate = popular["rate"]
        if rate > 25.0:
            raw = adjustments["soccer"].get("bet_min_confidence_pct", 0.0) - 3.0
            lo, hi = _ADJUSTMENT_BOUNDS.get("bet_min_confidence_pct", (-10, 0))
            adjustments["soccer"]["bet_min_confidence_pct"] = _clamp(raw, lo, hi)
            reasoning.append(
                f"Popular-team overrating in {popular['count']} predictions ({rate}%). "
                "Proposing -3% bet_min_confidence_pct for soccer high-ELO-favourite bets."
            )

    if not reasoning:
        reasoning.append("No category reached the minimum sample threshold for adjustments.")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "based_on_report": report.get("generated_at"),
        "adjustments": adjustments,
        "reasoning": reasoning,
    }


# ── Runtime adjustment loader ───────────────────────────────────────────────

def load_adjustments() -> dict[str, dict[str, float]]:
    """Load saved policy adjustments for runtime use.

    Returns {"soccer": {...}, "nba": {...}} or empty dicts on any failure.
    """
    path = policy_adjustments_path()
    if not path.exists():
        return {"soccer": {}, "nba": {}}
    try:
        data = json.loads(path.read_text())
        return data.get("adjustments", {"soccer": {}, "nba": {}})
    except Exception:
        logger.warning("Failed to load policy adjustments from %s", path)
        return {"soccer": {}, "nba": {}}


def apply_adjustments_to_thresholds(
    policy: dict[str, float], sport: str, adjustments: dict[str, dict[str, float]] | None = None,
) -> tuple[dict[str, float], list[str]]:
    """Apply learned adjustments to a sport policy dict, returning (adjusted_policy, notes).

    Adjustments are additive deltas bounded by _ADJUSTMENT_BOUNDS.
    Notes describe what was changed for explainability.
    """
    if adjustments is None:
        adjustments = load_adjustments()

    sport_adj = adjustments.get(sport, {})
    if not sport_adj:
        return dict(policy), []

    adjusted = dict(policy)
    notes: list[str] = []

    for key, delta in sport_adj.items():
        if key not in adjusted:
            continue
        old_val = adjusted[key]
        new_val = round(old_val + delta, 1)
        # Sanity: keep thresholds in 0-100 range
        new_val = max(0.0, min(100.0, new_val))
        if new_val != old_val:
            adjusted[key] = new_val
            notes.append(f"Adjusted {key}: {old_val} → {new_val} (learned +{delta:+.1f})")

    return adjusted, notes


# ── Persistence ─────────────────────────────────────────────────────────────

def save_report(report: dict) -> Path:
    path = mistake_report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str))
    logger.info("Mistake report saved to %s", path)
    return path


def save_adjustments(adjustments_doc: dict) -> Path:
    path = policy_adjustments_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(adjustments_doc, indent=2, default=str))
    logger.info("Policy adjustments saved to %s", path)
    return path


def load_report() -> dict | None:
    path = mistake_report_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
