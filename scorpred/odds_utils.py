"""
Edge-based betting utilities for ScorPred.

Core formula:
    edge = model_probability − fair_implied_probability

fair_implied_probability removes the bookmaker's vig (overround) so the
comparison is always against a "no-margin" line.  A positive edge means
the model assigns more probability to an outcome than the market does —
indicating potential value.  A negative edge means the market is pricing
the outcome more tightly than the model warrants.
"""

from __future__ import annotations

from typing import Any


# ── Core math ─────────────────────────────────────────────────────────────────

def decimal_to_raw_implied(decimal_odds: float) -> float:
    """Convert decimal odds to raw (vig-included) implied probability.

    Args:
        decimal_odds: e.g. 2.10 for evens-ish favourite.

    Returns:
        Raw implied probability before vig removal (0 < p < 1).

    Raises:
        ValueError: when decimal_odds ≤ 1.0 (would imply impossible outcome).
    """
    if decimal_odds <= 1.0:
        raise ValueError(f"Decimal odds must be > 1.0, got {decimal_odds!r}")
    return 1.0 / decimal_odds


def remove_vig(raw_probs: dict[str, float]) -> dict[str, float]:
    """Normalise raw implied probabilities to sum to 1.0 (removes bookmaker margin).

    Divides each raw implied prob by the sum of all raw implied probs
    (the overround).  The result is the "fair" market-implied probability
    for each outcome.

    Args:
        raw_probs: mapping of outcome label → raw implied probability.

    Returns:
        Same keys, vig-adjusted probabilities summing to 1.0.
    """
    total = sum(raw_probs.values())
    if total <= 0.0:
        raise ValueError(f"Sum of implied probs must be > 0, got {total}")
    return {k: round(v / total, 6) for k, v in raw_probs.items()}


def compute_edge(model_prob: float, fair_implied_prob: float) -> float:
    """Return edge = model_prob − fair_implied_prob, rounded to 4 d.p."""
    return round(float(model_prob) - float(fair_implied_prob), 4)


# ── Human-readable labels ─────────────────────────────────────────────────────

def edge_label(edge: float) -> str:
    """Human-readable classification of an edge value."""
    if edge >= 0.08:
        return "Strong Value"
    if edge >= 0.04:
        return "Value"
    if edge >= 0.01:
        return "Slight Edge"
    if edge >= -0.02:
        return "Fair"
    if edge >= -0.06:
        return "Slight Fade"
    return "Fade"


# ── Play-type override ────────────────────────────────────────────────────────

def odds_play_type(edge: float, model_prob: float, base_play_type: str) -> str:
    """Override the base play type using edge data.

    Promotion path:  AVOID → LEAN  (modest positive edge)
                     LEAN  → BET   (strong positive edge + model prob)
    Demotion path:   BET   → LEAN  (negative edge)
                     LEAN  → AVOID (negative edge)
    No meaningful edge: keep base_play_type unchanged.

    Args:
        edge:            selected-outcome edge value (model - fair implied).
        model_prob:      model probability for the selected outcome (0–1).
        base_play_type:  current BET / LEAN / AVOID before edge adjustment.

    Returns:
        Adjusted play type string.
    """
    if edge >= 0.06 and model_prob >= 0.48:
        return "BET"
    if edge >= 0.03 and model_prob >= 0.42:
        # Upgrade AVOID → LEAN; leave LEAN / BET unchanged
        return "LEAN" if base_play_type == "AVOID" else base_play_type
    if edge <= -0.04:
        if base_play_type == "BET":
            return "LEAN"
        if base_play_type == "LEAN":
            return "AVOID"
    return base_play_type


# ── Soccer 3-way entry point ──────────────────────────────────────────────────

def compute_soccer_edge(
    *,
    prob_a: float,
    prob_draw: float,
    prob_b: float,
    selected_team: str,
    home_odds: float | None,
    draw_odds: float | None,
    away_odds: float | None,
    base_play_type: str = "LEAN",
) -> dict[str, Any]:
    """Compute edge for all three soccer outcomes and return a structured result.

    Args:
        prob_a / prob_draw / prob_b  — model probabilities (0–1 scale).
        selected_team                — "A", "B", or "draw".
        home_odds / draw_odds / away_odds — decimal odds; all three required.
        base_play_type               — current BET / LEAN / AVOID pre-adjustment.

    Returns a dict with keys:
        available           — False when odds are missing or invalid.
        home_edge           — edge for home-win outcome.
        draw_edge           — edge for draw outcome.
        away_edge           — edge for away-win outcome.
        home_edge_label     — human label for home edge.
        draw_edge_label     — human label for draw edge.
        away_edge_label     — human label for away edge.
        selected_edge       — edge for the predicted outcome.
        selected_edge_label — human label for selected edge.
        fair_home / fair_draw / fair_away — vig-adjusted implied probs.
        vig_pct             — estimated bookmaker margin as a percentage.
        play_type           — edge-adjusted play type.
    """
    _empty: dict[str, Any] = {
        "available": False,
        "home_edge": None,
        "draw_edge": None,
        "away_edge": None,
        "home_edge_label": None,
        "draw_edge_label": None,
        "away_edge_label": None,
        "selected_edge": None,
        "selected_edge_label": None,
        "fair_home": None,
        "fair_draw": None,
        "fair_away": None,
        "vig_pct": None,
        "play_type": base_play_type,
    }

    if not all([home_odds, draw_odds, away_odds]):
        return _empty

    try:
        raw_home = decimal_to_raw_implied(float(home_odds))
        raw_draw = decimal_to_raw_implied(float(draw_odds))
        raw_away = decimal_to_raw_implied(float(away_odds))
    except (TypeError, ValueError):
        return _empty

    vig_pct = round((raw_home + raw_draw + raw_away - 1.0) * 100.0, 2)
    fair = remove_vig({"home": raw_home, "draw": raw_draw, "away": raw_away})

    home_edge = compute_edge(prob_a, fair["home"])
    draw_edge = compute_edge(prob_draw, fair["draw"])
    away_edge = compute_edge(prob_b, fair["away"])

    sel_edge = {"A": home_edge, "draw": draw_edge, "B": away_edge}.get(selected_team, home_edge)
    sel_model_prob = {"A": prob_a, "draw": prob_draw, "B": prob_b}.get(selected_team, prob_a)
    adjusted_play_type = odds_play_type(sel_edge, sel_model_prob, base_play_type)

    return {
        "available": True,
        "home_edge": home_edge,
        "draw_edge": draw_edge,
        "away_edge": away_edge,
        "home_edge_label": edge_label(home_edge),
        "draw_edge_label": edge_label(draw_edge),
        "away_edge_label": edge_label(away_edge),
        "selected_edge": sel_edge,
        "selected_edge_label": edge_label(sel_edge),
        "fair_home": fair["home"],
        "fair_draw": fair["draw"],
        "fair_away": fair["away"],
        "vig_pct": vig_pct,
        "play_type": adjusted_play_type,
    }
