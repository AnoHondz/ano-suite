"""Premium decision UI helpers for ScorPred.

The product surface should recommend a side for every normal matchup, then
explain how strong the play is and how trustworthy the data is. Internal
strength bands can shape sorting, while the public UI renders BET, CONSIDER,
or SKIP.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import logging
import re
from typing import Any

from services.prediction_contract import validate_analysis_contract

_logger = logging.getLogger(__name__)

TIER_ORDER = {"Best Bet": 0, "Strong Lean": 1, "Lean": 2, "Risky": 3, "No Pick": 4}
TIER_CLASS = {
    "Best Bet": "best-bet",
    "Strong Lean": "strong-lean",
    "Lean": "lean",
    "Risky": "risky",
    "No Pick": "no-pick",
}
DATA_ORDER = {"strong": 0, "partial": 1, "limited": 2}
ACTION_ORDER = {"BET": 0, "CONSIDER": 1, "SKIP": 2}
ACTION_CLASS = {"BET": "bet", "CONSIDER": "consider", "SKIP": "skip"}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_percent(value: Any, default: float = 0.0) -> float:
    number = safe_float(value, default)
    if 0 < number <= 1:
        number *= 100
    return round(clamp(number, 0, 100), 1)


def _probability_map(prediction: dict[str, Any] | None, *, sport: str = "soccer") -> dict[str, float]:
    prediction = prediction if isinstance(prediction, dict) else {}
    probs = prediction.get("win_probabilities")
    if not isinstance(probs, dict):
        probs = {
            "a": prediction.get("prob_a") or prediction.get("team_a_probability"),
            "b": prediction.get("prob_b") or prediction.get("team_b_probability"),
            "draw": prediction.get("prob_draw"),
        }
    a = normalize_percent(probs.get("a") or probs.get("team_a") or prediction.get("prob_a"), 0)
    b = normalize_percent(probs.get("b") or probs.get("team_b") or prediction.get("prob_b"), 0)
    draw = normalize_percent(probs.get("draw") or prediction.get("prob_draw"), 0) if sport == "soccer" else 0
    return {"a": a, "b": b, "draw": draw}


def _probabilities_signal_placeholder(probs: dict[str, float], *, sport: str) -> bool:
    a = probs.get("a", 0)
    b = probs.get("b", 0)
    draw = probs.get("draw", 0)
    if a == 0 and b == 0 and (sport != "soccer" or draw == 0):
        return True
    if abs(a - b) <= 1:
        if sport != "soccer":
            return True
        return draw <= 2 or abs(draw - a) <= 1
    return False


def _average_component_score(block: dict[str, Any]) -> float:
    values: list[float] = []
    for value in block.values():
        number = safe_float(value, -1)
        if number < 0:
            continue
        values.append(number * 10 if 0 < number <= 10 else number)
    return sum(values) / len(values) if values else 0.0


def _evidence_signal(prediction: dict[str, Any] | None) -> float:
    prediction = prediction if isinstance(prediction, dict) else {}
    comp_a = prediction.get("components_a") if isinstance(prediction.get("components_a"), dict) else {}
    comp_b = prediction.get("components_b") if isinstance(prediction.get("components_b"), dict) else {}
    a_score = _average_component_score(comp_a)
    b_score = _average_component_score(comp_b)
    if a_score or b_score:
        return clamp(abs(a_score - b_score) / 35, 0.18, 1)

    best_pick = prediction.get("best_pick") if isinstance(prediction.get("best_pick"), dict) else {}
    raw_conf = str(best_pick.get("confidence") or prediction.get("confidence") or "").strip().lower()
    if raw_conf == "high":
        return 0.85
    if raw_conf == "medium":
        return 0.55
    if raw_conf == "low":
        return 0.25
    return 0.42


def _stable_side_key(team_a: str, team_b: str, sport: str) -> str:
    seed = f"{sport}:{team_a}:{team_b}".encode("utf-8", errors="ignore")
    digest = hashlib.sha256(seed).hexdigest()
    return "a" if int(digest[:8], 16) % 2 == 0 else "b"


def _side_key_from_context(prediction: dict[str, Any], *, team_a: str, team_b: str, sport: str) -> str:
    comp_a = prediction.get("components_a") if isinstance(prediction.get("components_a"), dict) else {}
    comp_b = prediction.get("components_b") if isinstance(prediction.get("components_b"), dict) else {}
    a_score = _average_component_score(comp_a)
    b_score = _average_component_score(comp_b)
    if abs(a_score - b_score) >= 1:
        return "a" if a_score >= b_score else "b"
    return _stable_side_key(team_a, team_b, sport)


def probability_from_prediction(prediction: dict[str, Any] | None, *, sport: str = "soccer") -> float:
    if not isinstance(prediction, dict):
        return 0.0
    explicit = prediction.get("confidence_pct")
    if explicit not in (None, ""):
        return normalize_percent(explicit)
    probs = _probability_map(prediction, sport=sport)
    if _probabilities_signal_placeholder(probs, sport=sport):
        signal = _evidence_signal(prediction)
        best_pick = prediction.get("best_pick") if isinstance(prediction.get("best_pick"), dict) else {}
        raw_conf = str(best_pick.get("confidence") or prediction.get("confidence") or "").strip().lower()
        base = 55 + signal * 9
        if raw_conf == "high":
            base = max(base, 66)
        elif raw_conf == "medium":
            base = max(base, 60)
        elif raw_conf == "low":
            base = min(max(base, 55), 58)
        return round(clamp(base, 54, 74), 1)
    values = [probs["a"], probs["b"]]
    if sport == "soccer" and probs["draw"]:
        values.append(probs["draw"])
    return max(values) if values else 0.0


def data_badge(data: dict[str, Any] | None = None, *, has_prediction: bool = True) -> dict[str, str]:
    data = data or {}
    raw = str(data.get("tier") or data.get("state") or data.get("label") or "").strip().lower()

    if not has_prediction:
        state = "limited"
    elif raw in {"full", "strong", "strong data", "current-season data", "full live context"}:
        state = "strong"
    elif raw in {"partial", "mixed", "mixed data quality", "partial data", "partial live context"}:
        state = "partial"
    elif raw in {"limited", "limited data", "limited live context", "unavailable"}:
        state = "limited"
    elif data.get("all_data_available"):
        state = "strong"
    elif data.get("partial_data"):
        state = "partial"
    else:
        state = "partial" if has_prediction else "limited"

    labels = {
        "strong": "Strong Data",
        "partial": "Partial Data",
        "limited": "Limited Data",
    }
    return {"state": state, "label": labels[state]}


def _raw_pick(best_pick: dict[str, Any], prediction: dict[str, Any]) -> str:
    return str(
        best_pick.get("prediction")
        or best_pick.get("pick")
        or best_pick.get("winner_label")
        or prediction.get("winner_label")
        or ""
    ).strip()


def _clean_side_name(value: str) -> str:
    side = str(value or "").strip()
    lowered = side.lower()
    for suffix in (" to win", " win", " moneyline", " ml"):
        if lowered.endswith(suffix):
            side = side[: -len(suffix)].strip()
            lowered = side.lower()
            break
    return side


def _side_from_probabilities(probs: dict[str, float], *, team_a: str, team_b: str) -> tuple[str, str, bool]:
    if probs["a"] == 0 and probs["b"] == 0:
        return "No Pick", "", False
    side_key = "a" if probs["a"] >= probs["b"] else "b"
    draw_risk = bool(probs.get("draw", 0) >= max(probs["a"], probs["b"]))
    return (team_a if side_key == "a" else team_b), side_key, draw_risk


def pick_side(
    prediction: dict[str, Any] | None,
    *,
    team_a: str = "Team A",
    team_b: str = "Team B",
    sport: str = "soccer",
) -> tuple[str, bool, str, bool]:
    """Return side, no-pick flag, side key, and draw-risk flag."""

    if not isinstance(prediction, dict) or not prediction:
        return "No Pick", True, "", False

    probs = _probability_map(prediction, sport=sport)
    best_pick = prediction.get("best_pick") if isinstance(prediction.get("best_pick"), dict) else {}
    pick = _clean_side_name(_raw_pick(best_pick, prediction))
    tracking = str(best_pick.get("tracking_team") or best_pick.get("team") or prediction.get("predicted_winner") or "").strip()
    pick_l = pick.lower()
    tracking_l = tracking.lower()

    if tracking_l in {"a", "home", team_a.lower()}:
        return team_a, False, "a", False
    if tracking_l in {"b", "away", team_b.lower()}:
        return team_b, False, "b", False

    if tracking_l in {"draw", "avoid", "skip"} or pick_l in {"draw", "avoid", "skip", "no pick"}:
        side, side_key, draw_risk = _side_from_probabilities(probs, team_a=team_a, team_b=team_b)
        return side, side == "No Pick", side_key, True if sport == "soccer" else draw_risk

    if pick:
        if "draw" in pick_l:
            side, side_key, draw_risk = _side_from_probabilities(probs, team_a=team_a, team_b=team_b)
            return side, side == "No Pick", side_key, True if sport == "soccer" else draw_risk
        return pick, False, "a" if pick.lower() == team_a.lower() else ("b" if pick.lower() == team_b.lower() else ""), False

    if _probabilities_signal_placeholder(probs, sport=sport):
        side_key = _side_key_from_context(prediction, team_a=team_a, team_b=team_b, sport=sport)
        side = team_a if side_key == "a" else team_b
        return side, False, side_key, False

    side, side_key, draw_risk = _side_from_probabilities(probs, team_a=team_a, team_b=team_b)
    return side, side == "No Pick", side_key, draw_risk


def edge_gap(prediction: dict[str, Any] | None, *, sport: str = "soccer") -> float:
    probs = _probability_map(prediction, sport=sport)
    if _probabilities_signal_placeholder(probs, sport=sport):
        return round(4 + _evidence_signal(prediction) * 5, 1)
    return abs(probs["a"] - probs["b"])


def strength_for_prediction(
    prediction: dict[str, Any] | None,
    *,
    data_state: str,
    no_pick: bool,
    draw_risk: bool,
    sport: str,
) -> str:
    if no_pick or not isinstance(prediction, dict) or not prediction:
        return "No Pick"

    pct = probability_from_prediction(prediction, sport=sport)
    gap = edge_gap(prediction, sport=sport)
    best_pick = prediction.get("best_pick") if isinstance(prediction.get("best_pick"), dict) else {}
    raw_conf = str(best_pick.get("confidence") or prediction.get("confidence") or "").strip().lower()

    # Premium rebalance: encourage BET/CONSIDER, reduce SKIP, avoid 50/50
    score = pct + (gap * 1.1)
    if raw_conf == "high":
        score += 6
    elif raw_conf == "low":
        score -= 3
    if data_state == "strong":
        score += 6
    elif data_state == "limited":
        score -= 6
    if draw_risk:
        score -= 6

    # New thresholds: more Lean/Strong Lean, less Risky/No Pick
    if data_state == "limited":
        return "Lean" if score >= 52 and gap >= 4 else "Risky"
    if data_state == "partial":
        if score >= 65 and gap >= 7:
            return "Strong Lean"
        if score >= 54:
            return "Lean"
        return "Risky"
    if score >= 70 and gap >= 8:
        return "Best Bet"
    if score >= 62 and gap >= 5:
        return "Strong Lean"
    if score >= 54:
        return "Lean"
    return "Risky"


def display_confidence(raw_pct: float, tier: str) -> int:
    """Format the supplied score without promoting weak or missing evidence."""
    return int(round(clamp(safe_float(raw_pct, 0), 0, 100)))


def action_for_strength(*, tier: str, confidence_pct: float, data_state: str, no_pick: bool) -> str:
    """Translate internal strength bands into the public action system."""
    if no_pick or tier == "No Pick" or confidence_pct <= 0:
        return "SKIP"
    if data_state == "limited":
        return "CONSIDER" if confidence_pct >= 54 else "SKIP"
    if tier in {"Best Bet", "Strong Lean"} and confidence_pct >= 64:
        return "BET"
    if tier in {"Best Bet", "Strong Lean", "Lean"} or confidence_pct >= 54:
        return "CONSIDER"
    if tier == "Risky" and confidence_pct >= 53:
        return "CONSIDER"
    return "SKIP"


def _friendly_probability_rows(
    *,
    sport: str,
    team_a: str,
    team_b: str,
    probs: dict[str, float],
    selected_key: str,
    confidence_pct: float,
    draw_risk: bool,
) -> dict[str, float]:
    """Display only probabilities supplied by the prediction source."""
    return probs


def clean_reason(text: str | None) -> str:
    reason = str(text or "").strip()
    if not reason:
        return ""
    replacements = [
        (r"\bmodel[- ]?derived\b", "data-backed"),
        (r"\bmodel\b", "read"),
        (r"\bengine\b", "system"),
        (r"\bprediction\b", "pick"),
        (r"\bavoid\b", "risk control"),
        (r"score gap[:\s]*[0-9.\/]+", ""),
        (r"gap[:\s]*[0-9.\/]+", ""),
        (r"scores close\s*\([^)]*\)", "narrow matchup profile"),
        (r"internal rating[:\s].*", ""),
    ]
    for pattern, replacement in replacements:
        reason = re.sub(pattern, replacement, reason, flags=re.IGNORECASE)
    reason = re.sub(r"\s+", " ", reason).strip(" .")
    return reason[:1].upper() + reason[1:] if reason else ""


def decision_reason(*, tier: str, data_state: str, draw_risk: bool, sport: str, side: str) -> str:
    if tier in {"No Pick", "SKIP"}:
        return "No actionable edge due to missing or unreliable data."
    if draw_risk and sport == "soccer":
        return "Playable, but draw risk tempers the recommendation."
    if tier in {"Best Bet", "BET"}:
        return "Multiple strong factors support this side: form, matchup, and data quality."
    if tier in {"Strong Lean", "CONSIDER"}:
        return "Recent form and matchup context favor this side."
    if tier == "Lean":
        return "Playable edge with enough support to consider action."
    if data_state == "limited":
        return "Limited data, but the side still grades ahead on available evidence."
    return "Higher upside, but volatility remains."


def support_note_for(*, tier: str, data_state: str, draw_risk: bool, venue: str, side: str) -> str:
    if tier in {"No Pick", "SKIP"}:
        return "No actionable pick due to missing or unreliable data."
    if draw_risk:
        return "Draw risk tempers the recommendation, but the side remains playable."
    if data_state == "strong" and tier in {"Best Bet", "Strong Lean", "BET", "CONSIDER"}:
        return "Strong data and recent form support this pick."
    if data_state == "partial":
        return "Partial data supports the side, but monitor for lineup or context changes."
    if data_state == "limited":
        return "Limited data: use caution, but the side is still analyzable."
    if venue:
        return f"Venue context at {venue} supports the matchup read."
    return "Action and confidence together shape the play; review both."


def initials(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", str(value or ""))
    if not words:
        return "TM"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


def probability_rows(
    *,
    sport: str,
    team_a: str,
    team_b: str,
    prediction: dict[str, Any],
    selected_key: str,
    confidence_pct: float = 0,
    draw_risk: bool = False,
) -> list[dict[str, Any]]:
    probs = _probability_map(prediction, sport=sport)
    probs = _friendly_probability_rows(
        sport=sport,
        team_a=team_a,
        team_b=team_b,
        probs=probs,
        selected_key=selected_key,
        confidence_pct=confidence_pct,
        draw_risk=draw_risk,
    )
    rows = [
        {"label": team_a, "value": round(probs["a"], 1), "selected": selected_key == "a", "kind": "home"},
        {"label": team_b, "value": round(probs["b"], 1), "selected": selected_key == "b", "kind": "away"},
    ]
    if sport == "soccer":
        rows.insert(1, {"label": "Draw", "value": round(probs["draw"], 1), "selected": False, "kind": "draw"})
    return rows


def _metric_value(data: dict[str, Any], *keys: str) -> float:
    for key in keys:
        if key in data:
            value = safe_float(data.get(key), 0)
            return value * 10 if 0 < value <= 10 else value
    return 50


def comparison_metrics(prediction: dict[str, Any], *, team_a: str, team_b: str) -> list[dict[str, Any]]:
    comp_a = prediction.get("components_a") if isinstance(prediction.get("components_a"), dict) else {}
    comp_b = prediction.get("components_b") if isinstance(prediction.get("components_b"), dict) else {}
    if not comp_a and not comp_b:
        return []
    specs = [
        ("Recent form", ("form", "recent_form")),
        ("Attack", ("offense", "attack", "scoring")),
        ("Defense", ("defense", "defensive")),
        ("Venue/context", ("home_away", "venue", "match_context")),
    ]
    rows = []
    for label, keys in specs:
        a_value = round(clamp(_metric_value(comp_a, *keys), 0, 100), 1)
        b_value = round(clamp(_metric_value(comp_b, *keys), 0, 100), 1)
        rows.append(
            {
                "label": label,
                "team_a": team_a,
                "team_b": team_b,
                "a_value": a_value,
                "b_value": b_value,
                "leader": team_a if a_value >= b_value else team_b,
            }
        )
    return rows


def evidence_reason_from_metrics(
    metrics: list[dict[str, Any]],
    *,
    side: str,
    action_label: str,
    data_state: str,
    draw_risk: bool,
    sport: str,
) -> str:
    leaders = [str(item.get("label") or "").lower() for item in metrics if item.get("leader") == side]
    if draw_risk and sport == "soccer":
        return f"{side} grades ahead, but draw risk keeps the action measured."
    if {"recent form", "attack"} <= set(leaders):
        return f"Recent form and attacking profile point toward {side}."
    if "venue/context" in leaders and "defense" in leaders:
        return f"Venue context and defensive trend support {side}."
    if "attack" in leaders:
        return f"More stable attacking signals support {side}."
    if "recent form" in leaders:
        return f"Better recent form gives {side} the cleaner side."
    if data_state == "limited":
        return f"{side} still grades ahead, with caution because the data set is thinner."
    if action_label == "BET":
        return f"Multiple signals align behind {side}: matchup, form, and trust quality."
    if action_label == "CONSIDER":
        return f"{side} owns the better side of the matchup, with some volatility attached."
    return decision_reason(tier=action_label, data_state=data_state, draw_risk=draw_risk, sport=sport, side=side)


def why_win_points_for(
    metrics: list[dict[str, Any]],
    *,
    side: str,
    data_state: str,
    draw_risk: bool,
) -> list[str]:
    points: list[str] = []
    labels = [str(item.get("label") or "") for item in metrics if item.get("leader") == side]
    for label in labels[:3]:
        if label == "Recent form":
            points.append("Stronger recent form profile supports the side.")
        elif label == "Attack":
            points.append("Attack indicators give the pick more scoring upside.")
        elif label == "Defense":
            points.append("Defensive trend creates a cleaner matchup path.")
        elif label == "Venue/context":
            points.append("Venue and context markers tilt toward the recommended side.")
    if data_state == "strong":
        points.append("Data quality is strong enough to trust the read more heavily.")
    elif data_state == "partial":
        points.append("Partial data still gives enough support to keep the side actionable.")
    if not points:
        points = [
            "The side grades ahead on the available matchup profile.",
            "Confidence sits above the baseline for a playable read.",
            "The supporting context is stronger than a pure coin-flip setup.",
        ]
    return points[:5]


def why_lose_points_for(*, side: str, data_state: str, draw_risk: bool, sport: str) -> list[str]:
    points: list[str] = []
    if draw_risk and sport == "soccer":
        points.append("Draw risk can erase the edge even if the side plays well.")
    if data_state != "strong":
        points.append("Lineup or context changes matter more because the data picture is not complete.")
    points.append("Opponent volatility can swing the match if early momentum flips.")
    points.append("Finishing variance can turn a good read into a fragile result.")
    return points[:4]


def swing_factor_for(*, side: str, draw_risk: bool, sport: str) -> str:
    if draw_risk and sport == "soccer":
        return f"If {side} turns territory into early chances, the draw risk falls quickly."
    return f"If {side} controls the first major momentum spell, the recommendation strengthens."


def build_decision_card(
    *,
    analysis: dict[str, Any] | None = None,
    **_: Any,
) -> dict[str, Any]:
    team_a = str(_.get("team_a") or "").strip()
    team_b = str(_.get("team_b") or "").strip()
    if not analysis:
        prediction = _.get("prediction") if isinstance(_.get("prediction"), dict) else {}
        team_a = team_a or "Team A"
        team_b = team_b or "Team B"
        probs = prediction.get("win_probabilities") if isinstance(prediction.get("win_probabilities"), dict) else {}
        if not probs:
            probs = {
                "a": prediction.get("prob_a") or prediction.get("team_a_probability"),
                "draw": prediction.get("prob_draw"),
                "b": prediction.get("prob_b") or prediction.get("team_b_probability"),
            }
        best_pick = prediction.get("best_pick") if isinstance(prediction.get("best_pick"), dict) else {}
        conf = prediction.get("confidence_pct") or prediction.get("confidence") or 0
        if isinstance(conf, str):
            conf = 70 if conf.lower() == "high" else 60 if conf.lower() == "medium" else 52
        data_block = prediction.get("data_completeness") if isinstance(prediction.get("data_completeness"), dict) else {}
        tier = str(data_block.get("tier") or "").lower()
        analysis = {
            "matchup": f"{team_a} vs {team_b}",
            "confidence": conf,
            "probabilities": {"a": probs.get("a"), "draw": probs.get("draw"), "b": probs.get("b")},
            "action": prediction.get("action") or prediction.get("play_type") or "SKIP",
            "recommended_side": best_pick.get("prediction") or best_pick.get("team") or team_a,
            "reason": best_pick.get("reasoning") or prediction.get("decision_summary") or prediction.get("matchup_reading") or "",
            "data_quality": "Strong Data" if tier == "strong" else ("Partial Data" if tier in {"moderate", "partial"} else "Limited Data"),
            "metric_breakdown": prediction.get("metric_breakdown"),
            "match_id": _.get("match_id"),
        }
    contract_errors = validate_analysis_contract(analysis)
    if contract_errors:
        _logger.warning("Prediction contract validation failed: %s", "; ".join(contract_errors))
        return None

    matchup = str(analysis.get("matchup") or "").strip()
    if (not team_a or not team_b) and " vs " in matchup:
        left, right = matchup.split(" vs ", 1)
        team_a = team_a or left.strip() or "Team A"
        team_b = team_b or right.strip() or "Team B"
    team_a = team_a or "Team A"
    team_b = team_b or "Team B"

    confidence = analysis["confidence"]
    raw_action = str(analysis.get("action") or "CONSIDER").upper()
    action = "SKIP" if raw_action in {"AVOID", "NO PICK", "NOPICK"} else raw_action
    if action not in ACTION_ORDER:
        action = "SKIP"
    probabilities = analysis["probabilities"]
    sport = str(_.get("sport") or "soccer").lower()
    prob_a = normalize_percent(probabilities.get("a"), 0)
    prob_b = normalize_percent(probabilities.get("b"), 0)
    prob_draw = normalize_percent(probabilities.get("draw"), 0) if sport == "soccer" else 0
    recommended_side = str(analysis.get("recommended_side") or "").strip() or team_a
    recommended_side_l = recommended_side.lower()
    if " vs " in recommended_side_l or ("vs" in recommended_side_l and len(recommended_side) > 22):
        recommended_side = team_a if prob_a >= prob_b else team_b

    mb = analysis.get("metric_breakdown") or {}
    if not isinstance(mb, dict):
        mb = {}
    edge_score = mb.get("edge_score")
    expected_value = mb.get("expected_value")
    risk_level = str(mb.get("risk_level") or "").upper() or None
    decision_grade = mb.get("decision_grade")
    risk_score = mb.get("risk_score")

    card = {
        "matchup": matchup or f"{team_a} vs {team_b}",
        "team_a": team_a,
        "team_b": team_b,
        "team_a_logo": _.get("team_a_logo") or "",
        "team_b_logo": _.get("team_b_logo") or "",
        "team_a_initials": initials(team_a),
        "team_b_initials": initials(team_b),
        "sport": sport,
        "competition": _.get("competition") or "",
        "match_date": _.get("match_date") or "",
        "confidence": confidence,
        "probabilities": probabilities,
        "action": action,
        "recommended_side": recommended_side,
        "reason": analysis["reason"],
        "data_quality": analysis["data_quality"],
        "metric_breakdown": analysis.get("metric_breakdown"),
        "match_id": analysis.get("match_id"),
        "model_source": analysis.get("model_source"),
        "confidence_pct": int(safe_float(confidence, 0)),
        "action_label": action,
        "action_class": str(action).lower(),
        "edge_score": edge_score,
        "expected_value": expected_value,
        "risk_level": risk_level,
        "decision_grade": decision_grade,
        "risk_score": risk_score,
        "probability_rows": (
            [
                {"label": team_a, "value": prob_a, "selected": recommended_side == team_a},
                {"label": "Draw", "value": prob_draw, "selected": False},
                {"label": team_b, "value": prob_b, "selected": recommended_side == team_b},
            ]
            if sport == "soccer"
            else [
                {"label": team_a, "value": prob_a, "selected": recommended_side == team_a},
                {"label": team_b, "value": prob_b, "selected": recommended_side == team_b},
            ]
        ),
        "data_confidence": {
            "state": "strong" if "strong" in str(analysis["data_quality"]).lower() else ("limited" if "limited" in str(analysis["data_quality"]).lower() else "partial"),
            "label": str(analysis["data_quality"] or "Partial Data"),
        },
    }
    data_state = card["data_confidence"]["state"]
    draw_risk = prob_draw >= 26 if sport == "soccer" else False
    # If components_a/b missing, synthesise from metric_breakdown so comparison_metrics has data.
    enriched = dict(analysis)
    if not enriched.get("components_a") and not enriched.get("components_b"):
        mb = analysis.get("metric_breakdown") or {}
        if isinstance(mb, dict):
            comp_a, comp_b = {}, {}
            for key, val in mb.items():
                if isinstance(val, dict):
                    comp_a[key] = val.get("home", 0)
                    comp_b[key] = val.get("away", 0)
            if comp_a or comp_b:
                enriched["components_a"] = comp_a
                enriched["components_b"] = comp_b
    comp_metrics = comparison_metrics(enriched, team_a=team_a, team_b=team_b)
    card["comparison_metrics"] = comp_metrics
    card["why_win"] = why_win_points_for(comp_metrics, side=recommended_side, data_state=data_state, draw_risk=draw_risk)
    card["why_lose"] = why_lose_points_for(side=recommended_side, data_state=data_state, draw_risk=draw_risk, sport=sport)
    card["key_swing_factor"] = swing_factor_for(side=recommended_side, draw_risk=draw_risk, sport=sport)
    return card


def internal_confidence_tier(confidence_pct: float) -> str:
    if confidence_pct >= 62:
        return "high"
    if confidence_pct >= 52:
        return "medium"
    return "low"


def _playable_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [card for card in cards or [] if card.get("action") in {"BET", "CONSIDER"}]


def sort_cards_by_kickoff(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sort_cards(cards)


def sort_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        cards or [],
        key=lambda card: (
            ACTION_ORDER.get(str(card.get("action") or "SKIP"), 3),
            TIER_ORDER.get(str(card.get("strength_tier") or card.get("action") or "No Pick"), 5),
            DATA_ORDER.get(((card.get("data_confidence") or card.get("data_badge") or {}).get("state") or "limited"), 3),
            -safe_float(card.get("confidence_pct"), 0),
            str(card.get("match_date") or ""),
        ),
    )


def assign_opportunity_ranks(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    playable = sort_cards(_playable_cards(cards))
    for index, card in enumerate(playable, start=1):
        card["opportunity_rank"] = index
    return cards


def plan_summary(cards: list[dict[str, Any]]) -> dict[str, int]:
    assign_opportunity_ranks(cards or [])
    counts = Counter((card.get("action") or "SKIP") for card in cards or [])
    return {
        "bet": counts.get("BET", 0),
        "consider": counts.get("CONSIDER", 0),
        "skip": counts.get("SKIP", 0),
    }


def top_opportunities(cards: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    assign_opportunity_ranks(cards or [])
    playable = sort_cards(_playable_cards(cards))
    return (playable or sort_cards(cards or []))[:limit]


def format_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "TBD"
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:25], fmt).strftime("%b %d, %Y")
        except ValueError:
            continue
    return text[:10] if len(text) >= 10 else text


def result_status(record: dict[str, Any]) -> str:
    raw = str(record.get("overall_game_result") or record.get("result") or "").strip().lower()
    if raw in {"push", "void"} or record.get("is_push"):
        return "push"
    if record.get("is_correct") is True or record.get("winner_hit") is True or record.get("game_win") is True:
        return "correct"
    if record.get("is_correct") is False or record.get("winner_hit") is False or raw in {"loss", "incorrect"}:
        return "incorrect"
    return "push"


def _logo_from_record(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def normalize_result_record(record: dict[str, Any]) -> dict[str, Any]:
    sport = str(record.get("sport") or "soccer").lower()
    team_a = record.get("team_a") or record.get("home_team") or "Team A"
    team_b = record.get("team_b") or record.get("away_team") or "Team B"
    prediction = {
        "best_pick": {
            "prediction": record.get("predicted_pick_label") or record.get("predicted_winner_display") or record.get("predicted_winner"),
            "team": record.get("predicted_winner"),
            "reasoning": record.get("reasoning") or record.get("decision_explainer") or record.get("prediction_notes"),
            "confidence": record.get("confidence"),
        },
        "win_probabilities": {
            "a": record.get("prob_a"),
            "b": record.get("prob_b"),
            "draw": record.get("prob_draw"),
        },
        "confidence_pct": record.get("confidence_pct"),
        "data_completeness": record.get("data_completeness") or {},
    }
    card = build_decision_card(
        sport=sport,
        team_a=team_a,
        team_b=team_b,
        prediction=prediction,
        competition=record.get("league_name") or record.get("competition") or sport.upper(),
        match_date=record.get("game_date") or record.get("date") or record.get("created_at"),
        cta_url=f"/prediction-result/{record.get('id')}" if record.get("id") else "",
        cta_label="View Result",
        team_a_logo=_logo_from_record(record, "team_a_logo", "home_logo"),
        team_b_logo=_logo_from_record(record, "team_b_logo", "away_logo"),
        league_logo=_logo_from_record(record, "league_logo", "competition_logo"),
    ) or {}
    status = result_status(record)
    matchup_str = f"{team_a} vs {team_b}"
    return {
        "date": format_date(record.get("game_date") or record.get("date") or record.get("created_at")),
        "raw_date": str(record.get("game_date") or record.get("date") or record.get("created_at") or ""),
        "sport": sport,
        "competition": card.get("competition") or record.get("league_name") or sport.upper(),
        "matchup": card.get("matchup") or matchup_str,
        "team_a": card.get("team_a") or team_a,
        "team_b": card.get("team_b") or team_b,
        "team_a_logo": card.get("team_a_logo") or _logo_from_record(record, "team_a_logo", "home_logo"),
        "team_b_logo": card.get("team_b_logo") or _logo_from_record(record, "team_b_logo", "away_logo"),
        "team_a_initials": card.get("team_a_initials") or initials(team_a),
        "team_b_initials": card.get("team_b_initials") or initials(team_b),
        "league_logo": _logo_from_record(record, "league_logo", "competition_logo"),
        "final_score": record.get("final_score_display") or record.get("score") or "Pending",
        "action": card.get("action") or "CONSIDER",
        "action_label": card.get("action_label") or card.get("action") or "CONSIDER",
        "action_class": card.get("action_class") or "consider",
        "strength_tier": card.get("strength_tier") or card.get("action") or "CONSIDER",
        "strength_class": card.get("strength_class") or card.get("action_class") or "consider",
        "recommended_side": card.get("recommended_side") or team_a,
        "result": status,
        "result_label": status.title(),
        "confidence_pct": card.get("confidence_pct") or 0,
        "data_confidence": card.get("data_confidence") or {"state": "limited", "label": "Limited Data"},
        "summary_reason": card.get("summary_reason") or card.get("reason") or "",
        "detail_url": card.get("cta_url") or "",
    }


def _row_win_rate(rows: list[dict[str, Any]]) -> float:
    correct = sum(1 for row in rows if row["result"] == "correct")
    incorrect = sum(1 for row in rows if row["result"] == "incorrect")
    if correct + incorrect == 0:
        return 0.0
    return round((correct / (correct + incorrect)) * 100, 1)


def results_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row["result"] for row in rows)
    graded = counts["correct"] + counts["incorrect"] + counts["push"]
    recent = [row for row in rows if row["result"] in {"correct", "incorrect", "push"}]
    last_10 = recent[:10]
    streak_result = ""
    streak_count = 0
    for row in recent:
        status = row.get("result")
        if status == "push":
            continue
        if not streak_result:
            streak_result = status
        if status == streak_result:
            streak_count += 1
            continue
        break
    current_streak = f"{streak_count} {streak_result}" if streak_result and streak_count else "Awaiting result"
    by_sport = {}
    for sport in sorted({row.get("sport") or "soccer" for row in rows}):
        items = [row for row in rows if (row.get("sport") or "soccer") == sport]
        by_sport[sport] = {"count": len(items), "win_rate": _row_win_rate(items)}
    return {
        "total_graded": graded,
        "win_rate": _row_win_rate(rows),
        "recent_win_rate": _row_win_rate(last_10),
        "correct": counts["correct"],
        "incorrect": counts["incorrect"],
        "pushes": counts["push"],
        "recent_form": [row["result"] for row in last_10],
        "last_10_count": len(last_10),
        "current_streak": current_streak,
        "by_sport": by_sport,
    }


def results_breakdowns(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_action: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_comp[row["competition"]].append(row)
        by_action[row["action"]].append(row)

    competition_rows = [
        {"name": name, "count": len(items), "win_rate": _row_win_rate(items)}
        for name, items in by_comp.items()
    ]
    competition_rows.sort(key=lambda item: (item["win_rate"], item["count"]), reverse=True)

    action_rows = []
    for action in ("BET", "CONSIDER", "SKIP"):
        items = by_action.get(action, [])
        action_rows.append(
            {
                "name": action,
                "class": ACTION_CLASS[action],
                "count": len(items),
                "win_rate": _row_win_rate(items),
            }
        )

    return {
        "competitions": competition_rows,
        "actions": action_rows,
        "best_competition": competition_rows[0]["name"] if competition_rows else "Awaiting results",
        "recent_soccer": [row for row in rows if row.get("sport") == "soccer"][:50],
        "recent_nba": [row for row in rows if row.get("sport") == "nba"][:10],
    }


def normalize_data_label(raw: str | None) -> str:
    s = str(raw or "").lower()
    if "strong" in s:
        return "Strong Data"
    if "limited" in s:
        return "Limited Data"
    return "Partial Data"


def card_to_decision(card: dict) -> dict:
    action = str(card.get("action") or "CONSIDER").upper()
    if action not in {"BET", "CONSIDER", "SKIP"}:
        action = "SKIP"
    team_a = card.get("team_a") or ""
    team_b = card.get("team_b") or ""
    rec_side = card.get("recommended_side") or team_a or ""
    cta = card.get("cta_payload") or {}
    probabilities = card.get("probabilities") or {}
    def side_name(value):
        return str(value).lower().removesuffix("!").removesuffix(" win").strip()
    is_home = side_name(rec_side) == side_name(team_a)
    side_key = "draw" if side_name(rec_side) == "draw" else ("a" if is_home else "b")
    probability = probabilities.get(side_key)
    result = {
        "matchId": str(card.get("match_id") or cta.get("event_id") or ""),
        "winProbability": normalize_percent(probability, 0) if probability is not None and card.get("sport") != "nfl" else None,
        "drawProbability": normalize_percent(probabilities.get("draw"), 0) if probabilities.get("draw") is not None and card.get("sport") != "nfl" else None,
        # The full split, so a draw pick still says how the two win chances divide.
        "homeProbability": normalize_percent(probabilities.get("a"), 0) if probabilities.get("a") is not None and card.get("sport") != "nfl" else None,
        "scenarioShare": normalize_percent(probability, 0) if probability is not None and card.get("sport") == "nfl" else None,
        "tiedScenarioShare": normalize_percent(probabilities.get("draw"), 0) if probabilities.get("draw") is not None and card.get("sport") == "nfl" else None,
        "modelSource": card.get("model_source") or "weighted_rules",
        "action": action,
        "side": rec_side,
        "opponent": team_b if is_home else team_a,
        "teamA": team_a,
        "teamB": team_b,
        "matchup": card.get("matchup") or "",
        "matchDate": card.get("game_date") or card.get("match_date") or cta.get("event_date") or "",
        "slateDate": card.get("slate_date") or "",
        "eventStatus": card.get("event_status"),
        "dataFreshness": card.get("data_freshness") or {},
        "confidenceMeasure": "scenario_share" if card.get("sport") == "nfl" else
        "rule_confidence" if (card.get("model_source") or "weighted_rules") == "weighted_rules" else "model_probability",
        "confidence": int(safe_float(card.get("confidence_pct") or card.get("confidence"), 0)),
        "reason": card.get("reason") or "",
        "data": normalize_data_label(
            (card.get("data_confidence") or {}).get("label") or card.get("data_quality")
        ),
        "support": card.get("competition") or "",
        "logo": card.get("team_a_logo") if is_home else card.get("team_b_logo") or "",
        "leagueLogo": card.get("league_logo") or "",
        "teamALogo": card.get("team_a_logo") or "",
        "teamBLogo": card.get("team_b_logo") or "",
        "market": card.get("market") if isinstance(card.get("market"), dict) else None,
        "sport": card.get("sport") or "nba",
        "homeId": card.get("team_a_id") or cta.get("team_a") or None,
        "awayId": card.get("team_b_id") or cta.get("team_b") or None,
        "leagueId": card.get("league_id") or None,
    }

    from services.ano_data_quality import guard
    result["lineupEvidence"]=card.get("lineup_evidence") or {}
    result["injuryEvidence"]=card.get("injury_evidence") or {}
    return guard(result)
