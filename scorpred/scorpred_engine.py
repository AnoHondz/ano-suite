"""
scorpred_engine.py — Scorpred Weighted Prediction Engine

Scoring model (all components scored 0-10, final score 0-10):
  Form Score                (39%)  Win=+2, Draw=+1, Loss=0 · recency weights · margin bonus
  Offensive Strength        (14%)  Avg goals/pts scored in last 5 + trend adjustment
  Defensive Strength        (14%)  Avg goals/pts conceded in last 5 (fewer = higher)
  Head-to-Head Score         (9%)  Last 5 meetings, recency-weighted · recent H2H > old H2H
  Home/Away Advantage        (8%)  Moderate venue boost — real form data, not hardcoded
  Match Context              (5%)  Rest and fatigue from last match spacing
  Squad Availability         (4%)  Injury impact — role-weighted positional impact
  Opponent Strength Adj      (7%)  Adjusts form value by quality of opponents faced

Recency weights applied to last 5 matches (most recent first):
  1st = 40%,  2nd = 25%,  3rd = 15%,  4th = 10%,  5th = 10%

Final Team Score =
  (Form × 0.39) + (Offense × 0.14) + (Defense × 0.14) + (H2H × 0.09)
  + (Home/Away × 0.08) + (Squad × 0.04) + (Opp Strength × 0.07) + (Match Context × 0.05)

Design rules:
  - H2H must NOT overpower current form
  - Venue is a moderate contextual boost, not a dominant factor
  - Opponent Strength separates inflated form from real form
  - Prefer current evidence over old narratives

Data Quality:
  - Strong: Recent form + H2H + opponent quality + injuries all available
  - Moderate: Some data missing but prediction still reliable
  - Limited: Minimal data — prediction based on limited information
"""

from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Any

import model_tracker as mt

logger = logging.getLogger(__name__)

_RECENCY_WEIGHTS = [0.40, 0.25, 0.15, 0.10, 0.10]


# ── Name normalisation (for opp-strength lookups) ─────────────────────────────

def _norm(name: str) -> str:
    """Lowercase, alphanumeric only — for fuzzy team-name matching."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _lookup_opp_strength(opponent_name: str, opp_strengths: dict[str, float]) -> float:
    """
    Find opponent strength score (0-10) from a pre-built lookup dict.
    Falls back to 5.0 (neutral) when no match found.
    """
    if not opp_strengths or not opponent_name:
        return 5.0
    key = _norm(opponent_name)
    if key in opp_strengths:
        return opp_strengths[key]
    # Partial match fallback
    for stored_key, val in opp_strengths.items():
        if key and stored_key and (key in stored_key or stored_key in key):
            return val
    return 5.0


# ── Component calculators ──────────────────────────────────────────────────────

def _form_score(form: list[dict], sport: str = "soccer") -> tuple[float, dict]:
    """
    Form score 0-10 using recency-weighted results.
    Win=+2, Draw=+1 (soccer only), Loss=0.
    Big-margin wins/losses get a +/- 0.5 adjustment.
    """
    if not form:
        return 5.0, {"details": [], "note": "No form data — neutral score applied"}

    weighted_sum = 0.0
    details = []

    for i, match in enumerate(form[:5]):
        weight = _RECENCY_WEIGHTS[i]
        result = match.get("result", "")

        if sport == "nba":
            margin = int(match.get("our_pts", 0) or 0) - int(match.get("their_pts", 0) or 0)
        else:
            margin = int(match.get("gf", 0) or 0) - int(match.get("ga", 0) or 0)

        if result == "W":
            pts = 2.0
        elif result == "D" and sport == "soccer":
            pts = 1.0
        else:
            pts = 0.0

        big_margin = 3 if sport == "soccer" else 15
        if margin >= big_margin:
            pts = min(pts + 0.5, 2.5)
        elif margin <= -big_margin:
            pts = max(pts - 0.5, -0.5)

        pts = max(-0.5, min(2.5, pts))
        weighted_sum += pts * weight
        details.append({
            "match_num": i + 1,
            "result": result,
            "pts": round(pts, 1),
            "weight": weight,
            "margin": margin,
        })

    total_weight = sum(_RECENCY_WEIGHTS[: min(5, len(form))])
    max_possible = 2.5 * total_weight
    score = (weighted_sum / max_possible * 10) if max_possible > 0 else 5.0
    return round(max(0.0, min(10.0, score)), 2), {
        "details": details,
        "weighted_sum": round(weighted_sum, 3),
    }


def _offense_score(form: list[dict], sport: str = "soccer") -> tuple[float, dict]:
    """
    Offensive strength 0-10.
    Soccer: avg goals scored (3.0 = max). NBA: avg pts scored (85-115 range).
    Trend check (last 2 vs previous 3) applies a ±1 pt adjustment.
    """
    if not form:
        return 5.0, {"avg": 0, "trend": "neutral"}

    scored_key = "our_pts" if sport == "nba" else "gf"
    values = [float(m.get(scored_key, 0) or 0) for m in form[:5]]

    if not values:
        return 5.0, {"avg": 0, "trend": "neutral"}

    avg = sum(values) / len(values)

    if sport == "nba":
        base = max(0.0, min(10.0, (avg - 85.0) / 3.0))
    else:
        base = max(0.0, min(10.0, (avg / 3.0) * 10))

    trend = "neutral"
    bonus = 0.0
    if len(values) >= 3:
        recent_avg = sum(values[:2]) / 2
        older_avg = sum(values[2:]) / len(values[2:])
        threshold = 0.5 if sport == "soccer" else 5.0
        if recent_avg > older_avg + threshold:
            trend = "increasing"
            bonus = 1.0
        elif recent_avg < older_avg - threshold:
            trend = "decreasing"
            bonus = -1.0

    final = round(max(0.0, min(10.0, base + bonus)), 2)
    return final, {"avg": round(avg, 2), "trend": trend, "base_score": round(base, 2)}


def _defense_score(form: list[dict], sport: str = "soccer") -> tuple[float, dict]:
    """
    Defensive strength 0-10. Fewer goals/pts conceded = higher score.
    Soccer: 0 conceded = 10, 3+ = 0. NBA: 85 allowed = 10, 115+ = 0.
    """
    if not form:
        return 5.0, {"avg": 0}

    conceded_key = "their_pts" if sport == "nba" else "ga"
    values = [float(m.get(conceded_key, 0) or 0) for m in form[:5]]

    if not values:
        return 5.0, {"avg": 0}

    avg = sum(values) / len(values)

    if sport == "nba":
        base = max(0.0, min(10.0, (115.0 - avg) / 3.0))
    else:
        base = max(0.0, min(10.0, (3.0 - avg) / 3.0 * 10))

    return round(base, 2), {"avg": round(avg, 2)}


def _h2h_score(h2h_form: list[dict], sport: str = "soccer") -> tuple[float, dict]:
    """
    H2H score 0-10, recency-weighted across last 5 meetings.
    Win=+2, Draw=+1 (soccer), Loss=0.
    Recent H2H matters more than older-season meetings.
    Note: this component carries 10% weight — it should not override current form.
    """
    if not h2h_form:
        return 5.0, {"details": [], "note": "No H2H data — neutral score applied"}

    weighted_sum = 0.0
    details = []

    for i, match in enumerate(h2h_form[:5]):
        weight = _RECENCY_WEIGHTS[i]
        result = match.get("result", "")

        if result == "W":
            pts = 2.0
        elif result == "D" and sport == "soccer":
            pts = 1.0
        else:
            pts = 0.0

        weighted_sum += pts * weight
        details.append({
            "match_num": i + 1,
            "result": result,
            "pts": round(pts, 1),
            "weight": weight,
        })

    total_weight = sum(_RECENCY_WEIGHTS[: min(5, len(h2h_form))])
    max_possible = 2.0 * total_weight
    score = (weighted_sum / max_possible * 10) if max_possible > 0 else 5.0
    return round(max(0.0, min(10.0, score)), 2), {
        "details": details,
        "weighted_sum": round(weighted_sum, 3),
    }


def _home_away_score(form: list[dict], is_home: bool, sport: str = "soccer") -> tuple[float, dict]:
    """
    Venue advantage 0-10.
    Base: home=6.0, away=4.5 — a moderate gap, not a dominant factor.
    Adjusted by actual venue-specific win rate from recent matches.
    """
    base = 6.0 if is_home else 4.5
    venue_key = "is_home" if sport == "nba" else "home"
    venue_form = [m for m in form[:5] if m.get(venue_key) == is_home]

    adjustment = 0.0
    win_rate = None
    if venue_form:
        wins = sum(1 for m in venue_form if m.get("result") == "W")
        win_rate = wins / len(venue_form)
        if is_home:
            if win_rate >= 0.6:
                adjustment = 1.5   # strong home form
            elif win_rate <= 0.3:
                adjustment = -1.0  # poor home form
        else:
            if win_rate >= 0.5:
                adjustment = 1.0   # decent away form
            elif win_rate <= 0.2:
                adjustment = -1.5  # poor away form

    score = round(max(0.0, min(10.0, base + adjustment)), 2)
    return score, {
        "venue": "Home" if is_home else "Away",
        "base": base,
        "adjusted": score,
        "venue_matches": len(venue_form),
        "win_rate": round(win_rate, 2) if win_rate is not None else None,
    }


def _match_context_score(form: list[dict], sport: str = "soccer") -> tuple[float, dict]:
    """
    Match context 0-10 based on days since last completed game.
    Short rest (<=3 days) is a slight fatigue penalty; long rest (>7 days) is a small recovery boost.
    """
    if not form:
        return 5.0, {"days_since_last": None, "category": "neutral", "note": "No recent match spacing data"}

    most_recent = None
    for match in form[:5]:
        date_str = str(match.get("date", ""))[:10]
        try:
            match_date = datetime.fromisoformat(date_str)
        except ValueError:
            continue
        if not most_recent or match_date > most_recent:
            most_recent = match_date

    if not most_recent:
        return 5.0, {"days_since_last": None, "category": "neutral", "note": "No valid dates"}

    # Ensure timezone-aware comparison (ESPN dates are naive — treat as UTC)
    aware_most_recent = most_recent.replace(tzinfo=timezone.utc) if most_recent.tzinfo is None else most_recent
    days_since_last = max(0, (datetime.now(timezone.utc) - aware_most_recent).days)
    if days_since_last <= 3:
        score = 4.2
        category = "short_rest"
    elif days_since_last <= 7:
        score = 5.0
        category = "normal_rest"
    elif days_since_last <= 10:
        score = 5.8
        category = "long_rest"
    else:
        score = 6.3
        category = "extended_rest"

    return round(max(0.0, min(10.0, score)), 2), {
        "days_since_last": days_since_last,
        "category": category,
        "note": f"{days_since_last} days since last match",
    }


def _squad_score(injuries: list, sport: str = "soccer") -> tuple[float, dict]:
    """
    Squad availability 0-10. Full squad = 10.
    Role-weighted absence impact: attackers hurt offense, defenders/keepers hurt defense,
    bench/reduced-status absences have a smaller effect.
    """
    if not injuries:
        return 10.0, {"total_injured": 0, "penalty": 0.0, "offense_penalty": 0.0, "defense_penalty": 0.0}

    offense_penalty = 0.0
    defense_penalty = 0.0
    total_penalty = 0.0

    for inj in injuries:
        status = str((inj.get("status") or "")).lower()
        if sport == "nba":
            pos = str((inj.get("player", {}).get("pos") or inj.get("position") or "")).upper()
        else:
            pos = str((inj.get("player", {}).get("position") or "")).title()

        if sport == "nba":
            if pos in {"PG", "SG", "SF", "PF", "G", "F"}:
                role_weight = 1.2
                offense_penalty += 1.2
            elif pos == "C":
                role_weight = 1.1
                defense_penalty += 1.1
            else:
                role_weight = 0.9
                offense_penalty += 0.9
        else:
            if pos in {"Attacker", "Forward", "Striker", "Winger", "Midfielder"}:
                role_weight = 1.2
                offense_penalty += 1.2
            elif pos in {"Defender", "Fullback", "Center Back", "Wing Back", "Goalkeeper"}:
                role_weight = 1.3 if pos == "Goalkeeper" else 1.1
                defense_penalty += role_weight
            else:
                role_weight = 0.9
                total_penalty += 0.9

        if status == "out":
            status_weight = 1.0
        elif status == "doubtful":
            status_weight = 0.55
        elif status == "questionable":
            status_weight = 0.35
        else:
            status_weight = 0.65

        bench_factor = 0.6 if str((inj.get("player", {}).get("role") or "")).lower() == "bench" else 1.0
        player_penalty = role_weight * status_weight * bench_factor
        total_penalty += player_penalty

    score = round(max(0.0, min(10.0, 10.0 - total_penalty)), 2)
    return score, {
        "total_injured": len(injuries),
        "penalty": round(total_penalty, 2),
        "offense_penalty": round(offense_penalty, 2),
        "defense_penalty": round(defense_penalty, 2),
    }


def _opp_strength_score(
    form: list[dict],
    opp_strengths: dict[str, float],
    sport: str = "soccer",
) -> tuple[float, dict]:
    """
    Opponent Strength Adjustment 0-10.

    Separates genuine form from inflated/deflated records:
      - Strong win vs strong opponent  → high score
      - Strong win vs weak opponent    → modest score
      - Loss vs strong opponent        → small credit (expected)
      - Loss vs weak opponent          → heavy penalty

    Uses recency weighting across last 5 matches.
    Defaults to neutral 5.0 when no opponent strength data is available.
    """
    if not form:
        return 5.0, {"avg_opp_strength": 5.0, "note": "No form data"}

    # If no lookup table provided, return neutral
    if not opp_strengths:
        return 5.0, {"avg_opp_strength": 5.0, "note": "No standings data — neutral applied"}

    weighted_sum = 0.0
    total_weight = 0.0
    details = []

    for i, match in enumerate(form[:5]):
        weight = _RECENCY_WEIGHTS[i]
        result = match.get("result", "")
        opp_name = match.get("opponent", "")
        opp_str = _lookup_opp_strength(opp_name, opp_strengths)  # 0-10

        # Opponent quality factor: 0.0 = weakest, 1.0 = strongest
        opp_factor = opp_str / 10.0

        # Points earned, adjusted by opponent quality
        if result == "W":
            # Win vs top opponent = 2.0, vs weakest = 1.0
            pts = 1.0 + opp_factor
        elif result == "D" and sport == "soccer":
            # Draw vs top opponent = 0.75, vs weakest = 0.25
            pts = 0.25 + opp_factor * 0.5
        else:
            # Loss vs strongest = 0.3 credit; vs weakest = 0 (penalised)
            pts = opp_factor * 0.3

        weighted_sum += pts * weight
        total_weight += weight
        details.append({
            "match_num": i + 1,
            "opponent": opp_name,
            "opp_strength": round(opp_str, 1),
            "result": result,
            "pts": round(pts, 2),
        })

    # Max possible: Win against strongest opponent every game = 2.0 × sum_weights
    max_possible = 2.0 * sum(_RECENCY_WEIGHTS[: min(5, len(form))])
    avg_opp_str = sum(d["opp_strength"] for d in details) / max(len(details), 1)

    score = (weighted_sum / max_possible * 10) if max_possible > 0 else 5.0
    return round(max(0.0, min(10.0, score)), 2), {
        "avg_opp_strength": round(avg_opp_str, 1),
        "details": details,
        "weighted_sum": round(weighted_sum, 3),
    }


def _logistic_probability(gap: float, steepness: float = 1.0) -> float:
    """
    Convert score gap to probability using logistic function.
    
    Larger gap → probability closer to 0 or 1 (more confident).
    Smaller gap → probability closer to 0.5 (less confident).
    
    Logistic: P = 1 / (1 + exp(-steepness * gap))
    """
    import math
    try:
        return 1.0 / (1.0 + math.exp(-steepness * gap))
    except (ValueError, OverflowError):
        return 0.5 if gap <= 0 else 0.95 if gap > 0 else 0.05


def _score_to_confidence_level(gap: float, sport: str = "soccer") -> str:
    """
    Determine confidence label based on score gap.
    
    Gap is absolute difference between scores (0-10 scale).
    Very small gap → Low confidence
    Medium gap → Medium confidence
    Large gap → High confidence
    """
    # Thresholds vary by sport and scale
    if sport == "nba":
        if gap >= 1.5:
            return "High"
        elif gap >= 0.5:
            return "Medium"
        else:
            return "Low"
    else:  # soccer
        if gap >= 2.0:
            return "High"
        elif gap >= 0.8:
            return "Medium"
        else:
            return "Low"


# ── Top Lean & Decision Status layer ─────────────────────────────────────────

def compute_top_lean(
    home_pct: float,
    draw_pct: float,
    away_pct: float,
    home_name: str,
    away_name: str,
) -> dict:
    """Determine the top lean from 1X2 probabilities (0-100 scale).

    Returns ``{"outcome": str, "label": str, "prob": float}``.

    Draw is only returned as the lean when:
    * ``draw_pct`` is the highest probability,
    * ``draw_pct >= 34``, **and**
    * ``abs(home_pct - away_pct) <= 6``.

    Otherwise the lean goes to the stronger side (Home / Away) or, when
    the home/away probs are within 2 pp of each other and neither exceeds
    draw, the outcome is ``"Toss-Up"``.
    """
    probs = {"home": home_pct, "draw": draw_pct, "away": away_pct}
    ranked = sorted(probs.items(), key=lambda x: x[1], reverse=True)
    top_key, top_val = ranked[0]

    # Draw gating: only allow draw as lean under strict conditions
    if top_key == "draw":
        if draw_pct >= 34 and abs(home_pct - away_pct) <= 6:
            return {"outcome": "Draw", "label": "Draw", "prob": round(draw_pct, 1)}
        # Fall through: pick the stronger side instead
        if home_pct >= away_pct:
            return {"outcome": "Home", "label": home_name, "prob": round(home_pct, 1)}
        return {"outcome": "Away", "label": away_name, "prob": round(away_pct, 1)}

    if top_key == "home":
        return {"outcome": "Home", "label": home_name, "prob": round(home_pct, 1)}
    return {"outcome": "Away", "label": away_name, "prob": round(away_pct, 1)}


def compute_decision_status(home_pct: float, draw_pct: float, away_pct: float) -> str:
    """Map 1X2 probabilities (0-100 scale) to a human-readable decision tag.

    Possible returns: ``"Strong Lean"`` | ``"Lean"`` | ``"Too Close"`` |
    ``"No Edge"``.
    """
    probs = sorted([home_pct, draw_pct, away_pct], reverse=True)
    top = probs[0]
    second = probs[1]
    gap = top - second

    if top < 42:
        return "No Edge"

    if gap < 4:
        return "Too Close"

    if top >= 55 and gap >= 8:
        return "Strong Lean"

    if gap >= 5:
        return "Lean"

    return "No Edge"


def _win_probabilities(score_a: float, score_b: float, sport: str = "soccer") -> dict[str, float]:
    """
    Convert two normalized scores (0-10) into a probability distribution.
    
    Uses logistic function to map score differences to win probabilities.
    Ensures all probabilities sum to 100%.
    """
    if score_a is None or score_b is None:
        return {"a": 33.3, "draw": 33.4, "b": 33.3} if sport == "soccer" else {"a": 50.0, "b": 50.0}

    # Calculate score gap (favorable for team A if positive)
    gap = score_a - score_b
    
    # Logistic scaling: larger gap = more extreme probability
    # Steepness=0.5 ensures reasonable probability diffusion
    steepness = 0.5
    prob_a_raw = _logistic_probability(gap, steepness)
    prob_b_raw = 1.0 - prob_a_raw
    
    if sport == "soccer":
        # For soccer, allocate a draw probability based on score gap
        # Close scores favor draws; distant scores favor decisive outcomes
        gap_abs = abs(gap)
        draw_pct = max(8.0, min(32.0, 26.0 - gap_abs * 3.5))
        
        # Remaining probability split between A and B
        remaining = 100.0 - draw_pct
        win_a = round(prob_a_raw * remaining, 1)
        win_b = round(prob_b_raw * remaining, 1)
        draw = round(100.0 - win_a - win_b, 1)
        
        # Normalize to ensure 100%
        total = win_a + win_b + draw
        if total != 100.0:
            diff = 100.0 - total
            win_a += diff
        
        return {"a": max(0.0, win_a), "draw": max(0.0, draw), "b": max(0.0, win_b)}
    else:
        # NBA / no-draw: pure logistic probability
        win_a = round(prob_a_raw * 100.0, 1)
        win_b = round(100.0 - win_a, 1)
        return {"a": win_a, "b": win_b}


def summarize_prediction_history() -> dict[str, Any]:
    """Bridge to model_tracker.get_summary_metrics(), returning legacy-shaped dict."""
    try:
        metrics = mt.get_summary_metrics(exclude_seeded=True)
    except Exception:
        return {"total_tracked": 0, "total_verified": 0, "accuracy": None, "confidence": {}}
    by_conf = metrics.get("by_confidence") or {}
    return {
        "total_tracked": metrics.get("total_predictions", 0),
        "total_verified": metrics.get("finalized_predictions", 0),
        "accuracy": round(metrics["overall_accuracy"], 1) if metrics.get("overall_accuracy") is not None else None,
        "confidence": {
            level: {
                "total": (by_conf.get(level) or {}).get("count", 0),
                "accuracy": (by_conf.get(level) or {}).get("accuracy"),
            }
            for level in ("High", "Medium", "Low")
        },
    }


# ── Key edges + optional picks ─────────────────────────────────────────────────

def _build_key_edges(
    comp_a: dict,
    comp_b: dict,
    name_a: str,
    name_b: str,
) -> list[dict]:
    """Return top 3 biggest scoring advantages between the two teams."""
    labels = {
        "form":           "Recent Form",
        "offense":        "Attacking Power",
        "defense":        "Defensive Strength",
        "h2h":            "Head-to-Head Record",
        "home_away":      "Venue Advantage",
        "squad":          "Squad Availability",
        "opp_strength":   "Opponent Quality Faced",
        "match_context":  "Match Context",
    }

    diffs = [
        (key, label, comp_a.get(key, 5.0) - comp_b.get(key, 5.0),
         comp_a.get(key, 5.0), comp_b.get(key, 5.0))
        for key, label in labels.items()
    ]
    diffs.sort(key=lambda x: abs(x[2]), reverse=True)

    edges = []
    for key, label, diff, a_val, b_val in diffs[:3]:
        if abs(diff) < 0.3:
            continue
        winner_name = name_a if diff > 0 else name_b
        edges.append({
            "team": "A" if diff > 0 else "B",
            "team_name": winner_name,
            "category": label,
            "margin": round(abs(diff), 1),
            "a_val": round(a_val, 1),
            "b_val": round(b_val, 1),
            "detail": f"{winner_name} leads in {label} ({a_val:.1f} vs {b_val:.1f})",
        })

    return edges[:3]


def _build_matchup_reading(
    comp_a: dict,
    comp_b: dict,
    name_a: str,
    name_b: str,
    score_a: float,
    score_b: float,
    sport: str,
) -> str:
    """
    Generate a concise analytical matchup reading that explains which side
    is actually stronger right now, whether form is real, and what to watch.
    """
    lines = []

    stronger = name_a if score_a > score_b else (name_b if score_b > score_a else None)
    if stronger:
        lines.append(f"{stronger} hold the clearest overall edge ({score_a:.1f} vs {score_b:.1f}).")

    # Form vs H2H conflict check
    form_leader = "A" if comp_a["form"] > comp_b["form"] + 0.5 else (
        "B" if comp_b["form"] > comp_a["form"] + 0.5 else None
    )
    h2h_leader = "A" if comp_a["h2h"] > comp_b["h2h"] + 0.5 else (
        "B" if comp_b["h2h"] > comp_a["h2h"] + 0.5 else None
    )
    if form_leader and h2h_leader and form_leader != h2h_leader:
        form_name = name_a if form_leader == "A" else name_b
        h2h_name = name_a if h2h_leader == "A" else name_b
        lines.append(
            f"Current form favours {form_name}, but H2H history leans {h2h_name} — "
            f"recent form carries 4× the weight of H2H in this model."
        )

    # Opponent quality flag
    opp_a = comp_a.get("opp_strength", 5.0)
    opp_b = comp_b.get("opp_strength", 5.0)
    if opp_a < 4.0:
        lines.append(f"{name_a}'s recent results came against weaker opposition — treat their form with caution.")
    if opp_b < 4.0:
        lines.append(f"{name_b}'s recent results came against weaker opposition — treat their form with caution.")
    if opp_a > 7.0:
        lines.append(f"{name_a} have been tested by quality opponents recently, making their form more credible.")
    if opp_b > 7.0:
        lines.append(f"{name_b} have been tested by quality opponents recently, making their form more credible.")

    # Match context / rest check
    context_a = comp_a.get("match_context", 5.0)
    context_b = comp_b.get("match_context", 5.0)
    if context_a < 4.5:
        lines.append(f"{name_a} may be fatigued from short rest, weakening their current edge.")
    if context_b < 4.5:
        lines.append(f"{name_b} may be fatigued from short rest, weakening their current edge.")
    if context_a > 5.5:
        lines.append(f"{name_a} have had extra recovery time, making their form more trustworthy.")
    if context_b > 5.5:
        lines.append(f"{name_b} have had extra recovery time, making their form more trustworthy.")

    # Attack/defense summary
    att_leader = name_a if comp_a["offense"] > comp_b["offense"] + 1.0 else (
        name_b if comp_b["offense"] > comp_a["offense"] + 1.0 else None
    )
    def_leader = name_a if comp_a["defense"] > comp_b["defense"] + 1.0 else (
        name_b if comp_b["defense"] > comp_a["defense"] + 1.0 else None
    )
    if att_leader:
        lines.append(f"{att_leader} carry the stronger attacking threat right now.")
    if def_leader:
        lines.append(f"{def_leader} are the tighter defensive unit.")

    return " ".join(lines) if lines else "Scores are close — this is a genuinely competitive matchup."


def _optional_picks(form_a: list[dict], form_b: list[dict], sport: str) -> list[dict]:
    """Generate optional picks only when trends clearly support them."""
    picks = []

    if sport == "nba":
        scored_key, conceded_key = "our_pts", "their_pts"
    else:
        scored_key, conceded_key = "gf", "ga"

    a_scored = [float(m.get(scored_key, 0) or 0) for m in form_a[:5]]
    b_scored = [float(m.get(scored_key, 0) or 0) for m in form_b[:5]]
    a_conced = [float(m.get(conceded_key, 0) or 0) for m in form_a[:5]]
    b_conced = [float(m.get(conceded_key, 0) or 0) for m in form_b[:5]]

    if not a_scored or not b_scored:
        return picks

    avg_a = sum(a_scored) / len(a_scored)
    avg_b = sum(b_scored) / len(b_scored)
    avg_total = avg_a + avg_b

    if sport == "nba":
        line = 220.0
        lean = "Over" if avg_total > line else "Under"
        picks.append({
            "market": f"Total Points O/U {line:.0f}",
            "lean": lean,
            "reasoning": f"Avg combined scoring: {avg_total:.1f} pts/game",
        })
    else:
        lean = "Over" if avg_total > 2.5 else "Under"
        picks.append({
            "market": "Goals Over/Under 2.5",
            "lean": lean,
            "reasoning": f"Avg combined goals: {avg_total:.2f}/game",
        })

        # BTTS — only include if conviction is clear
        a_score_rate = sum(1 for v in a_scored if v > 0) / len(a_scored)
        b_score_rate = sum(1 for v in b_scored if v > 0) / len(b_scored)
        a_conc_rate  = sum(1 for v in a_conced if v > 0) / max(len(a_conced), 1)
        b_conc_rate  = sum(1 for v in b_conced if v > 0) / max(len(b_conced), 1)
        btts_prob = (a_score_rate + b_conc_rate + b_score_rate + a_conc_rate) / 4
        picks.append({
            "market": "Both Teams to Score",
            "lean": "Yes" if btts_prob >= 0.5 else "No",
            "reasoning": f"BTTS probability ~{btts_prob * 100:.0f}% from recent scoring rates",
        })

    return picks


# ── Core scoring function ──────────────────────────────────────────────────────

def calculate_team_score(
    form: list[dict],
    h2h_form: list[dict],
    injuries: list,
    is_home: bool,
    opp_strengths: dict[str, float] | None = None,
    sport: str = "soccer",
) -> tuple[float, dict]:
    """
    Calculate the final Scorpred team score (0-10) and full component breakdown.

    Args:
        form          — last 5 completed matches from this team's perspective
        h2h_form      — last 5 H2H meetings from this team's perspective
        injuries      — current injury list for this team
        is_home       — True if this team is playing at home
        opp_strengths — dict of normalised_name → strength (0-10) from standings
        sport         — "soccer" or "nba"
    """
    f_score,  f_det   = _form_score(form, sport)
    o_score,  o_det   = _offense_score(form, sport)
    d_score,  d_det   = _defense_score(form, sport)
    h_score,  h_det   = _h2h_score(h2h_form, sport)
    ha_score, ha_det  = _home_away_score(form, is_home, sport)
    s_score,  s_det   = _squad_score(injuries, sport)
    os_score, os_det  = _opp_strength_score(form, opp_strengths or {}, sport)
    mc_score, mc_det  = _match_context_score(form, sport)

    final = round(
        f_score  * 0.39 +
        o_score  * 0.14 +
        d_score  * 0.14 +
        h_score  * 0.09 +
        ha_score * 0.08 +
        s_score  * 0.04 +
        os_score * 0.07 +
        mc_score * 0.05,
        2,
    )
    final = max(0.0, min(10.0, final))

    return final, {
        "form":           f_score,
        "offense":        o_score,
        "defense":        d_score,
        "h2h":            h_score,
        "home_away":      ha_score,
        "squad":          s_score,
        "opp_strength":   os_score,
        "match_context":  mc_score,
        "details": {
            "form":         f_det,
            "offense":      o_det,
            "defense":      d_det,
            "h2h":          h_det,
            "home_away":    ha_det,
            "squad":        s_det,
            "opp_strength": os_det,
            "match_context": mc_det,
        },
    }


# ── Main prediction entry point ────────────────────────────────────────────────

def _fallback_team_components() -> dict[str, Any]:
    """Return safe fallback component scores when calculations fail."""
    return {
        "form": 5.0,
        "offense": 5.0,
        "defense": 5.0,
        "h2h": 5.0,
        "home_away": 5.0,
        "squad": 5.0,
        "opp_strength": 5.0,
        "match_context": 5.0,
        "details": {
            "form": {"details": [], "note": "Fallback — form data unavailable"},
            "offense": {"avg": 0, "trend": "neutral", "note": "Fallback — offensive data unavailable"},
            "defense": {"avg": 0, "note": "Fallback — defensive data unavailable"},
            "h2h": {"details": [], "note": "Fallback — H2H data unavailable"},
            "home_away": {"venue": "Unknown", "base": 5.0, "adjusted": 5.0, "venue_matches": 0},
            "squad": {"total_injured": 0, "penalty": 0.0, "offense_penalty": 0.0, "defense_penalty": 0.0},
            "opp_strength": {"avg_opp_strength": 5.0, "note": "Fallback — opponent strength unavailable"},
            "match_context": {"days_since_last": None, "category": "neutral", "note": "Fallback — match context unavailable"},
        },
    }


def _assess_data_quality(
    form_a: list[dict],
    form_b: list[dict],
    h2h_form: list[dict],
    injuries_a: list,
    injuries_b: list,
    opp_strengths: dict[str, float] | None = None,
) -> str:
    """
    Assess data quality for the prediction.
    
    Returns: "Strong", "Moderate", or "Limited"
    """
    checks = {
        "form_a": len(form_a) >= 3,
        "form_b": len(form_b) >= 3,
        "h2h": len(h2h_form) >= 3,
        "injuries_a": len(injuries_a) > 0,
        "injuries_b": len(injuries_b) > 0,
        "opp_strength": bool(opp_strengths and len(opp_strengths) > 0),
    }
    
    passed = sum(1 for v in checks.values() if v)
    
    if passed >= 5:
        return "Strong"
    elif passed >= 3:
        return "Moderate"
    else:
        return "Limited"


def scorpred_predict(
    form_a: list[dict],
    form_b: list[dict],
    h2h_form_a: list[dict],
    h2h_form_b: list[dict],
    injuries_a: list,
    injuries_b: list,
    team_a_is_home: bool,
    team_a_name: str = "Team A",
    team_b_name: str = "Team B",
    sport: str = "soccer",
    opp_strengths: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Run the full Scorpred prediction model for a match.
    
    RELIABILITY GUARANTEE: This function ALWAYS returns a valid prediction dict.
    If any component fails, fallback neutral/conservative values are used.
    No None values are ever returned — every prediction is guaranteed.

    Args:
        form_a / form_b         — last 5 matches per team (team's perspective)
        h2h_form_a / h2h_form_b — last 5 H2H games per team (team's perspective)
        injuries_a / injuries_b — current injury lists
        team_a_is_home          — True if team A is the home side
        team_a_name / team_b_name — display names
        sport                   — "soccer" or "nba"
        opp_strengths           — dict of normalised_team_name → strength (0-10)
                                   built from league standings; if None, defaults
                                   to neutral 5.0 for opponent quality

    Returns a dict with:
        team_a_score / team_b_score  — final 0-10 scores (ALWAYS valid, never None)
        score_gap                    — absolute difference (ALWAYS ≥ 0)
        components_a / components_b  — per-component scores
        comparison                   — which team leads each component
        key_edges                    — top 2-3 biggest advantages
        matchup_reading              — analytical explanation
        best_pick                    — prediction, confidence, reasoning
        optional_picks               — over/under and BTTS suggestions
        debug_info                   — fallback usage and data quality notes
    """
    # Compute data quality label before running — based on what we have
    _has_form_a = len(form_a or []) >= 3
    _has_form_b = len(form_b or []) >= 3
    _has_h2h    = len(h2h_form_a or []) >= 2 or len(h2h_form_b or []) >= 2
    _has_opp    = bool(opp_strengths)
    _q_points   = sum([_has_form_a, _has_form_b, _has_h2h, _has_opp])
    if _q_points >= 4:
        _data_quality_label = "Strong"
    elif _q_points >= 2:
        _data_quality_label = "Moderate"
    else:
        _data_quality_label = "Limited"

    debug_info = {"fallbacks_used": [], "data_quality": _data_quality_label}
    
    try:
        score_a, comp_a = calculate_team_score(
            form_a, h2h_form_a, injuries_a, team_a_is_home, opp_strengths, sport
        )
    except Exception as exc:
        debug_info["fallbacks_used"].append(f"Team A score calculation failed: {exc}")
        debug_info["data_quality"] = "degraded"
        score_a = 5.0  # Neutral fallback
        comp_a = _fallback_team_components()
    
    try:
        score_b, comp_b = calculate_team_score(
            form_b, h2h_form_b, injuries_b, not team_a_is_home, opp_strengths, sport
        )
    except Exception as exc:
        debug_info["fallbacks_used"].append(f"Team B score calculation failed: {exc}")
        debug_info["data_quality"] = "degraded"
        score_b = 5.0  # Neutral fallback
        comp_b = _fallback_team_components()
    
    # Ensure scores are sempre valid (0-10 range)
    score_a = max(0.0, min(10.0, float(score_a or 5.0)))
    score_b = max(0.0, min(10.0, float(score_b or 5.0)))

    # If both teams land on exactly 5.0 (neutral), no meaningful signal exists.
    # Downgrade to Limited so the UI shows "no data" instead of fake 37/26/37.
    if score_a == 5.0 and score_b == 5.0:
        _data_quality_label = "Limited"
        debug_info["data_quality"] = "Limited"
        debug_info["fallbacks_used"].append("Both teams scored neutral 5.0 — no differentiating signal")

    def _edge(a: float, b: float) -> str:
        if a > b + 0.3:
            return "A"
        if b > a + 0.3:
            return "B"
        return "equal"

    try:
        a_trend = comp_a["details"]["offense"].get("trend", "neutral")
        b_trend = comp_b["details"]["offense"].get("trend", "neutral")
        if a_trend == "increasing" and b_trend != "increasing":
            trending_up = "A"
        elif b_trend == "increasing" and a_trend != "increasing":
            trending_up = "B"
        elif a_trend == "increasing" and b_trend == "increasing":
            trending_up = "both"
        else:
            trending_up = "neither"

        comparison = {
            "form":                _edge(comp_a["form"],         comp_b["form"]),
            "offense":             _edge(comp_a["offense"],      comp_b["offense"]),
            "defense":             _edge(comp_a["defense"],      comp_b["defense"]),
            "h2h":                 _edge(comp_a["h2h"],          comp_b["h2h"]),
            "home_away":           _edge(comp_a["home_away"],    comp_b["home_away"]),
            "squad":               _edge(comp_a["squad"],        comp_b["squad"]),
            "opp_strength":        _edge(comp_a["opp_strength"], comp_b["opp_strength"]),
            "trending_up_offense": trending_up,
        }
    except Exception as exc:
        debug_info["fallbacks_used"].append(f"Comparison calculation failed: {exc}")
        comparison = {"form": "equal", "offense": "equal", "defense": "equal", "h2h": "equal", 
                      "home_away": "equal", "squad": "equal", "opp_strength": "equal", "trending_up_offense": "neither"}
    
    # Best pick + confidence using unified confidence system
    gap = abs(score_a - score_b)
    confidence = _score_to_confidence_level(gap, sport)

    if score_a > score_b + 0.2:
        prediction = f"{team_a_name} Win"
        pick_team = "A"
    elif score_b > score_a + 0.2:
        prediction = f"{team_b_name} Win"
        pick_team = "B"
    else:
        if sport == "soccer":
            # Within the close zone, only call a draw when scores are nearly identical;
            # otherwise pick the team with the slight edge.
            if gap < 0.06:
                prediction = "Draw"
                pick_team = "draw"
            elif score_a > score_b:
                prediction = f"{team_a_name} Win"
                pick_team = "A"
            else:
                prediction = f"{team_b_name} Win"
                pick_team = "B"
        else:
            prediction = f"{team_a_name} Win" if score_a >= score_b else f"{team_b_name} Win"
            pick_team = "A" if score_a >= score_b else "B"

    # Initialize components with fallbacks
    comp_a_clean = _fallback_team_components()
    comp_b_clean = _fallback_team_components()
    key_edges = [{"detail": f"{team_a_name} vs {team_b_name}", "edge": gap}]
    matchup_reading = f"Matchup between {team_a_name} (score: {score_a}) and {team_b_name} (score: {score_b})."
    
    try:
        comp_keys = ("form", "offense", "defense", "h2h", "home_away", "squad", "opp_strength", "match_context")
        comp_a_clean = {k: comp_a[k] for k in comp_keys if k in comp_a}
        comp_b_clean = {k: comp_b[k] for k in comp_keys if k in comp_b}

        key_edges = _build_key_edges(comp_a_clean, comp_b_clean, team_a_name, team_b_name)
        matchup_reading = _build_matchup_reading(
            comp_a_clean, comp_b_clean, team_a_name, team_b_name, score_a, score_b, sport
        )
    except Exception as exc:
        debug_info["fallbacks_used"].append(f"Key edges/matchup reading failed: {exc}")
    
    try:
        top_edge_text = key_edges[0]["detail"] if key_edges else f"Scores close ({score_a} vs {score_b})"
        reasoning = f"{top_edge_text}. Gap: {gap:.1f}/10."
    except Exception:
        reasoning = f"{team_a_name} vs {team_b_name}. Gap: {gap:.1f}/10."
    
    try:
        win_probs = _win_probabilities(score_a, score_b, sport)
    except Exception as exc:
        debug_info["fallbacks_used"].append(f"Win probability calculation failed: {exc}")
        if sport == "soccer":
            win_probs = {"a": 33.3, "draw": 33.4, "b": 33.3}
        else:
            win_probs = {"a": 50.0, "b": 50.0}
    
    try:
        perf_summary = summarize_prediction_history()
    except Exception as exc:
        debug_info["fallbacks_used"].append(f"Performance summary failed: {exc}")
        perf_summary = {"total_tracked": 0, "total_verified": 0, "accuracy": None, "confidence": {}}
    
    try:
        optional = _optional_picks(form_a, form_b, sport)
    except Exception as exc:
        debug_info["fallbacks_used"].append(f"Optional picks failed: {exc}")
        optional = []

    # ── Decision layer: top lean + decision status ────────────────────
    _hp = round(win_probs.get("a", 0.0), 1)
    _dp = round(win_probs.get("draw", 0.0), 1)
    _ap = round(win_probs.get("b", 0.0), 1)
    if sport == "soccer":
        top_lean = compute_top_lean(_hp, _dp, _ap, team_a_name, team_b_name)
        decision_status = compute_decision_status(_hp, _dp, _ap)
    else:
        top_lean = {"outcome": "", "label": "", "prob": 0.0}
        decision_status = ""

    return {
        "team_a_score":   round(score_a, 2),
        "team_b_score":   round(score_b, 2),
        "score_gap":      round(gap, 2),
        "win_probabilities": win_probs,
        "prob_a":         _hp,
        "prob_b":         _ap,
        "prob_draw":      _dp,
        "home_pct":       _hp,
        "draw_pct":       _dp,
        "away_pct":       _ap,
        "confidence":     confidence,
        "winner_label":   prediction,
        "components_a":   comp_a_clean,
        "components_b":   comp_b_clean,
        "comparison":     comparison,
        "key_edges":      key_edges,
        "matchup_reading": matchup_reading,
        "best_pick": {
            "prediction": prediction,
            "team":       pick_team,
            "confidence": confidence,
            "reasoning":  reasoning,
        },
        "performance_summary": perf_summary,
        "optional_picks": optional,
        "debug_info": debug_info,
        "data_quality": _data_quality_label,
        "top_lean": top_lean,
        "decision_status": decision_status,
    }


# ── Standings → opponent-strength lookup builder ──────────────────────────────

def build_opp_strengths_from_standings(standings: list) -> dict[str, float]:
    """
    Convert a league standings list into a normalised_name → strength (0-10) dict.

    Strength blends rank position and available win/points-per-game context.
    This lets opponent quality reflect both league position and recent consistency.
    Returns empty dict when standings are unavailable.
    """
    if not standings:
        return {}
    total = len(standings)
    if total == 1:
        return {_norm((standings[0].get("team") or {}).get("name", "")): 5.0}

    lookup = {}
    ranks = []
    for s in standings:
        name = (s.get("team") or {}).get("name", "")
        rank = s.get("rank") or s.get("position") or 0
        if not name or not rank:
            continue
        ranks.append((name, int(rank), s))

    if not ranks:
        return {}

    for name, rank, s in ranks:
        # League position component: rank-based linear scale
        pos_score = (total - rank) / (total - 1) * 10 if total > 1 else 5.0

        # Standings data may include points, played, win/wins, and win percentage.
        # ESPN rows nest these under s["all"]; api-football puts them at the top level.
        _all = s.get("all") or {}
        points = s.get("points") or s.get("pts")
        played = s.get("played") or s.get("matches_played") or s.get("games") or _all.get("played")
        wins = s.get("win") or s.get("wins") or s.get("w") or _all.get("win")
        win_pct = None
        if wins is not None and played:
            try:
                win_pct = float(wins) / float(played)
            except Exception:
                win_pct = None
        elif isinstance(s.get("win_pct"), (int, float)):
            win_pct = float(s.get("win_pct"))
        elif points is not None and played:
            try:
                win_pct = float(points) / float(played) / 3.0
            except Exception:
                win_pct = None

        if win_pct is None:
            win_pct = 0.5
        win_pct = max(0.0, min(1.0, win_pct))

        quality_score = pos_score * 0.65 + win_pct * 10 * 0.35
        strength = round(max(0.0, min(10.0, quality_score)), 1)
        lookup[_norm(name)] = strength

    return lookup
