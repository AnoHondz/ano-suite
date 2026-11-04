"""
nba_predictor.py — NBA win probability model and player prop line generator.

Win probability weights:
  H2H record:              20%
  Recent form (last 10):   25%
  Home/away advantage:     20%
  Injury impact:           15%
  Off/Def rating diff:     20%

Prop line weights:
  Season averages:         40%
  Last 5 games average:    35%
  Vs opponent historical:  25%
"""

from __future__ import annotations
import math
from typing import Any


# ── Win probability model ──────────────────────────────────────────────────────

def predict_winner(
    team_a: dict,
    team_b: dict,
    h2h_games: list,
    form_a: list,
    form_b: list,
    injuries_a: list,
    injuries_b: list,
    stats_a: dict | None = None,
    stats_b: dict | None = None,
    team_a_is_home: bool = True,
) -> dict[str, Any]:
    """
    Compute win probabilities for team_a vs team_b.

    Returns a comprehensive prediction dict with all supporting numbers
    so the bettor can see the full calculation.
    """
    # ── 1. H2H record (20%) ───────────────────────────────────────────────────
    h2h_score = _h2h_score(h2h_games, team_a["id"], team_b["id"])
    h2h_a = h2h_score["a_pct"]
    h2h_b = h2h_score["b_pct"]

    # ── 2. Recent form (25%) ──────────────────────────────────────────────────
    form_score_a = _form_score(form_a, team_a["id"])
    form_score_b = _form_score(form_b, team_b["id"])
    form_total = form_score_a + form_score_b or 1
    form_a_pct = form_score_a / form_total
    form_b_pct = form_score_b / form_total

    # ── 3. Home/away advantage (20%) ─────────────────────────────────────────
    home_bonus_a = 0.58 if team_a_is_home else 0.42
    home_bonus_b = 1.0 - home_bonus_a

    # ── 4. Injury impact (15%) ────────────────────────────────────────────────
    inj_a, inj_b = _injury_impact(injuries_a), _injury_impact(injuries_b)
    inj_total = inj_a + inj_b or 1
    inj_a_pct = inj_a / inj_total
    inj_b_pct = inj_b / inj_total

    # ── 5. Offensive/defensive rating differential (20%) ─────────────────────
    rtg_a_pct, rtg_b_pct = _rating_score(stats_a, stats_b)

    # ── Weighted combination ──────────────────────────────────────────────────
    weights = {
        "h2h":   0.20,
        "form":  0.25,
        "home":  0.20,
        "inj":   0.15,
        "rtg":   0.20,
    }

    raw_a = (
        weights["h2h"]  * h2h_a +
        weights["form"] * form_a_pct +
        weights["home"] * home_bonus_a +
        weights["inj"]  * inj_a_pct +
        weights["rtg"]  * rtg_a_pct
    )
    raw_b = (
        weights["h2h"]  * h2h_b +
        weights["form"] * form_b_pct +
        weights["home"] * home_bonus_b +
        weights["inj"]  * inj_b_pct +
        weights["rtg"]  * rtg_b_pct
    )
    total = raw_a + raw_b or 1.0
    prob_a = round(100 * raw_a / total, 1)
    prob_b = round(100 * raw_b / total, 1)

    winner = "A" if prob_a >= prob_b else "B"
    margin = abs(prob_a - prob_b)
    if margin >= 15:
        confidence = "High"
    elif margin >= 7:
        confidence = "Medium"
    else:
        confidence = "Low"

    # ── Total points projection ───────────────────────────────────────────────
    ppg_a = stats_a.get("ppg", 110.0) if stats_a else 110.0
    opp_a = stats_a.get("opp_ppg", 110.0) if stats_a else 110.0
    ppg_b = stats_b.get("ppg", 110.0) if stats_b else 110.0
    opp_b = stats_b.get("opp_ppg", 110.0) if stats_b else 110.0

    # Projected total = blend of each team's scoring and opponent's allowed
    proj_a_pts = round((ppg_a * 0.6 + opp_b * 0.4), 1)
    proj_b_pts = round((ppg_b * 0.6 + opp_a * 0.4), 1)
    proj_total = round(proj_a_pts + proj_b_pts, 1)

    # Spread
    proj_margin = round(proj_a_pts - proj_b_pts, 1)

    # ── Component breakdown for UI transparency ───────────────────────────────
    components = {
        "h2h": {
            "weight": "20%",
            "team_a_pct": round(h2h_a * 100, 1),
            "team_b_pct": round(h2h_b * 100, 1),
            "record": h2h_score,
        },
        "form": {
            "weight": "25%",
            "team_a_pct": round(form_a_pct * 100, 1),
            "team_b_pct": round(form_b_pct * 100, 1),
            "team_a_score": round(form_score_a, 2),
            "team_b_score": round(form_score_b, 2),
        },
        "home_away": {
            "weight": "20%",
            "team_a_is_home": team_a_is_home,
            "team_a_pct": round(home_bonus_a * 100, 1),
            "team_b_pct": round(home_bonus_b * 100, 1),
        },
        "injuries": {
            "weight": "15%",
            "team_a_pct": round(inj_a_pct * 100, 1),
            "team_b_pct": round(inj_b_pct * 100, 1),
            "team_a_count": len(injuries_a),
            "team_b_count": len(injuries_b),
        },
        "ratings": {
            "weight": "20%",
            "team_a_pct": round(rtg_a_pct * 100, 1),
            "team_b_pct": round(rtg_b_pct * 100, 1),
            "team_a_net": stats_a.get("net_rtg", 0) if stats_a else 0,
            "team_b_net": stats_b.get("net_rtg", 0) if stats_b else 0,
        },
    }

    return {
        "prob_a": prob_a,
        "prob_b": prob_b,
        "winner": winner,
        "confidence": confidence,
        "proj_a_pts": proj_a_pts,
        "proj_b_pts": proj_b_pts,
        "proj_total": proj_total,
        "proj_margin": proj_margin,
        "over_under_line": _round_half(proj_total),
        "spread_line": _round_half(abs(proj_margin)),
        "spread_favoured": "A" if proj_margin > 0 else "B",
        "components": components,
    }


# ── Player prop line generator ─────────────────────────────────────────────────

def generate_prop_lines(
    season_avgs: dict | None,
    last5_avgs: dict | None,
    vs_opp_avgs: dict | None,
    vs_opp_limited: bool = False,
) -> list[dict]:
    """
    Generate prop lines for Points, Rebounds, Assists, 3PM, Steals, Blocks, PRA.

    Weights:
      season averages:   40%
      last 5 games avg:  35%
      vs opponent avg:   25%  (falls back to season avg if limited_sample)

    Returns a list of prop dicts with projection, lean, confidence, and breakdown.
    """
    if not season_avgs:
        return []

    markets = [
        ("points",   "Points",    "PTS"),
        ("rebounds", "Rebounds",  "REB"),
        ("assists",  "Assists",   "AST"),
        ("tpm",      "3-Pointers Made", "3PM"),
        ("steals",   "Steals",   "STL"),
        ("blocks",   "Blocks",   "BLK"),
        ("pra",      "PRA (Pts+Reb+Ast)", "PRA"),
    ]

    props = []
    for key, label, abbr in markets:
        sa  = _safe_f(season_avgs.get(key))
        l5  = _safe_f((last5_avgs or {}).get(key)) if last5_avgs else sa
        opp = _safe_f((vs_opp_avgs or {}).get(key)) if (vs_opp_avgs and not vs_opp_limited) else sa

        # Weighted projection
        if vs_opp_limited or not vs_opp_avgs:
            # Fall back: 55/45 season/last5
            proj = round(sa * 0.55 + l5 * 0.45, 1)
            opp_note = "⚠ Limited H2H sample — using season avg"
        else:
            proj = round(sa * 0.40 + l5 * 0.35 + opp * 0.25, 1)
            opp_note = None

        # Suggested betting line (round to nearest 0.5)
        line = _round_half(proj)

        # Lean
        diff = proj - line
        if diff > 0.25:
            lean = "OVER"
        elif diff < -0.25:
            lean = "UNDER"
        else:
            lean = "PUSH"

        # Confidence: based on consistency across the three sources
        spread = max(sa, l5, opp) - min(sa, l5, opp)
        if spread < proj * 0.15:
            conf = 85
        elif spread < proj * 0.30:
            conf = 72
        else:
            conf = 58

        props.append({
            "key":       key,
            "label":     label,
            "abbr":      abbr,
            "season_avg": sa,
            "last5_avg":  l5,
            "opp_avg":    opp,
            "projection": proj,
            "line":       line,
            "lean":       lean,
            "confidence": conf,
            "opp_note":   opp_note,
            "limited":    vs_opp_limited,
            "breakdown":  f"Season {sa} × 40% + Last5 {l5} × 35% + vsOpp {opp} × 25% = {proj}",
        })

    return props


# ── Best bets card ─────────────────────────────────────────────────────────────

def best_bets(prediction: dict, props_a: list, props_b: list,
              team_a_name: str, team_b_name: str) -> list[dict]:
    """
    Build a top-3 best bets slip from the win prediction + prop lines.
    Returns bets sorted by confidence descending with stake rating (1–5).
    """
    candidates = []

    # Match result
    winner_name = team_a_name if prediction["winner"] == "A" else team_b_name
    conf_map = {"High": 80, "Medium": 65, "Low": 50}
    candidates.append({
        "bet":        f"{winner_name} to Win",
        "market":     "Match Result",
        "line":       f"{prediction['prob_a'] if prediction['winner'] == 'A' else prediction['prob_b']}% probability",
        "confidence": conf_map[prediction["confidence"]],
        "reason":     f"Driven by: {_top_component(prediction['components'])}",
    })

    # Over/Under total
    ou_conf = 68
    candidates.append({
        "bet":        f"Total Points {'Over' if prediction['proj_total'] > prediction['over_under_line'] else 'Under'} {prediction['over_under_line']}",
        "market":     "Total Points O/U",
        "line":       f"Projected total: {prediction['proj_total']}",
        "confidence": ou_conf,
        "reason":     f"Combined team PPG projects to {prediction['proj_total']} pts",
    })

    # Best player prop
    all_props = [(p, "A") for p in (props_a or [])] + [(p, "B") for p in (props_b or [])]
    strong_props = [(p, t) for p, t in all_props if p["lean"] != "PUSH" and p["confidence"] >= 72]
    strong_props.sort(key=lambda x: x[0]["confidence"], reverse=True)
    if strong_props:
        best_p, side = strong_props[0]
        player_team = team_a_name if side == "A" else team_b_name
        candidates.append({
            "bet":        f"{best_p['label']} {best_p['lean']} {best_p['line']} ({player_team})",
            "market":     "Player Props",
            "line":       f"Projected: {best_p['projection']}",
            "confidence": best_p["confidence"],
            "reason":     best_p["breakdown"],
        })

    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    top3 = candidates[:3]

    for bet in top3:
        c = bet["confidence"]
        bet["stars"] = 5 if c >= 80 else 4 if c >= 72 else 3 if c >= 65 else 2 if c >= 55 else 1

    return top3


def build_market_recommendations(
    team_a: dict,
    team_b: dict,
    scorpred: dict,
    recent_form_a: list[dict],
    recent_form_b: list[dict],
    h2h_games: list,
    injuries_a: list,
    injuries_b: list,
    stats_a: dict | None = None,
    stats_b: dict | None = None,
    *,
    team_a_is_home: bool = True,
) -> dict[str, Any]:
    """Build explicit winner, spread, totals, and parlay outputs for the NBA prediction page.

    This is a display-oriented market layer built from the real team stats already available in the app.
    It is future-ready for full leg grading, but the spread leg is not yet settled by the tracking layer.
    """
    team_a_name = team_a.get("nickname") or team_a.get("name") or "Team A"
    team_b_name = team_b.get("nickname") or team_b.get("name") or "Team B"

    scorpred = scorpred or {}
    stats_a = stats_a or {}
    stats_b = stats_b or {}

    recent_a = _recent_team_summary(recent_form_a)
    recent_b = _recent_team_summary(recent_form_b)
    h2h_summary = _recent_h2h_summary(h2h_games)

    home_edge = 2.1 if team_a_is_home else -2.1
    injury_edge = (_injury_penalty_value(injuries_b) - _injury_penalty_value(injuries_a)) * 0.75
    net_edge = (_safe_f(stats_a.get("net_rtg")) - _safe_f(stats_b.get("net_rtg"))) * 0.55
    recent_edge = (recent_a["avg_margin"] - recent_b["avg_margin"]) * 0.35
    h2h_edge = h2h_summary["avg_margin"] * 0.12 if h2h_summary["games"] else 0.0
    probability_edge = (_safe_f(scorpred.get("prob_a")) - _safe_f(scorpred.get("prob_b"))) * 0.08

    expected_margin = home_edge + injury_edge + net_edge + recent_edge + h2h_edge + probability_edge
    if abs(expected_margin) < 0.5:
        expected_margin = 0.5 if _safe_f(scorpred.get("team_a_score")) >= _safe_f(scorpred.get("team_b_score")) else -0.5

    season_total_anchor = (
        (_safe_f(stats_a.get("ppg") if stats_a.get("ppg") is not None else 110.0) + _safe_f(stats_b.get("opp_ppg") if stats_b.get("opp_ppg") is not None else 110.0))
        + (_safe_f(stats_b.get("ppg") if stats_b.get("ppg") is not None else 110.0) + _safe_f(stats_a.get("opp_ppg") if stats_a.get("opp_ppg") is not None else 110.0))
    ) / 2.0
    recent_total = (
        recent_a["avg_scored"] + recent_b["avg_allowed"] + recent_b["avg_scored"] + recent_a["avg_allowed"]
    ) / 2.0
    h2h_total = h2h_summary["avg_total"] if h2h_summary["games"] else season_total_anchor

    scoring_environment = ((_safe_f(stats_a.get("ppg")) + _safe_f(stats_a.get("opp_ppg"))) / 2.0) + (
        (_safe_f(stats_b.get("ppg")) + _safe_f(stats_b.get("opp_ppg"))) / 2.0
    ) - 220.0
    total_injury_adjustment = -0.8 * (_injury_penalty_value(injuries_a) + _injury_penalty_value(injuries_b))

    expected_total = (
        season_total_anchor * 0.45
        + recent_total * 0.35
        + h2h_total * 0.20
        + scoring_environment * 0.20
        + total_injury_adjustment
    )
    total_line = _round_half(season_total_anchor)
    total_edge = expected_total - total_line

    winner_team = team_a_name if expected_margin >= 0 else team_b_name
    spread_side = team_a_name if expected_margin >= 0 else team_b_name
    spread_value = _round_half(abs(expected_margin))
    spread_label = f"{spread_side} {'-' if expected_margin >= 0 else '-'}{spread_value}"
    if expected_margin < 0:
        spread_label = f"{spread_side} -{spread_value}"

    total_lean = "Over" if total_edge >= 0 else "Under"
    total_pick = f"{total_lean} {total_line}"

    winner_confidence = scorpred.get("confidence") or _market_confidence_from_gap(abs(_safe_f(scorpred.get("prob_a")) - _safe_f(scorpred.get("prob_b"))), kind="winner")
    spread_confidence = _market_confidence_from_gap(abs(expected_margin), kind="spread")
    totals_confidence = _market_confidence_from_gap(abs(total_edge), kind="total")

    winner_reason = _winner_reason(team_a_name, team_b_name, scorpred, recent_a, recent_b, stats_a, stats_b, injuries_a, injuries_b)
    spread_reason = _spread_reason(team_a_name, team_b_name, expected_margin, net_edge, recent_edge, home_edge, injury_edge)
    totals_reason = _totals_reason(expected_total, total_line, recent_total, season_total_anchor, total_injury_adjustment, h2h_summary)

    winner_leg = {
        "leg_key": "winner",
        "leg_type": "Winner / Moneyline",
        "recommendation": winner_team,
        "confidence": winner_confidence,
        "strength": _market_strength_label(winner_confidence),
        "explanation": winner_reason,
        "tracking_status": "tracked",
    }
    spread_leg = {
        "leg_key": "spread",
        "leg_type": "Spread",
        "recommendation": spread_label,
        "confidence": spread_confidence,
        "strength": _market_strength_label(spread_confidence),
        "model_line": spread_value,
        "expected_margin": round(expected_margin, 1),
        "explanation": spread_reason,
        "tracking_status": "display_only",
    }
    totals_leg = {
        "leg_key": "total",
        "leg_type": "Total Points",
        "recommendation": total_pick,
        "confidence": totals_confidence,
        "strength": _market_strength_label(totals_confidence),
        "model_line": total_line,
        "expected_total": round(expected_total, 1),
        "explanation": totals_reason,
        "tracking_status": "future_ready",
    }

    parlay_legs = [winner_leg, spread_leg, totals_leg]
    alignment = _parlay_alignment_summary(winner_leg, spread_leg, totals_leg)

    return {
        "winner_leg": winner_leg,
        "spread_leg": spread_leg,
        "totals_leg": totals_leg,
        "parlay_checklist": parlay_legs,
        "alignment": alignment,
        "expected_margin": round(expected_margin, 1),
        "expected_total": round(expected_total, 1),
        "model_spread_label": spread_label,
        "model_total_pick": total_pick,
        "model_total_line": total_line,
        "display_note": "Spread and totals are model-derived suggestions built from ScorPred data inputs, not sportsbook lines.",
        "tracking_note": "Winner is already tracked. Spread is display-only for now, and totals are structured for future-expanded NBA leg grading.",
        "evidence_points": [
            f"Recent form margin: {team_a_name} {recent_a['avg_margin']:+.1f}, {team_b_name} {recent_b['avg_margin']:+.1f}.",
            f"Net rating edge: {team_a_name} {_safe_f(stats_a.get('net_rtg')):+.1f}, {team_b_name} {_safe_f(stats_b.get('net_rtg')):+.1f}.",
            f"Scoring environment baseline: {season_total_anchor:.1f} projected points before recent-form and injury adjustments.",
        ],
    }


# ── Internal helpers ───────────────────────────────────────────────────────────

def _h2h_score(games: list, id_a: int, id_b: int) -> dict:
    id_a = str(id_a)
    id_b = str(id_b)
    wins_a = wins_b = 0
    for g in filter_completed_nba_games(games):
        home_id = str(g.get("teams", {}).get("home", {}).get("id") or "")
        visit_id = str(g.get("teams", {}).get("visitors", {}).get("id") or "")
        if {home_id, visit_id} != {id_a, id_b}:
            continue
        scores   = g.get("scores", {})
        h_pts = _safe_f(scores.get("home", {}).get("points"))
        v_pts = _safe_f(scores.get("visitors", {}).get("points"))
        if h_pts == v_pts:
            continue
        if (home_id == id_a and h_pts > v_pts) or (visit_id == id_a and v_pts > h_pts):
            wins_a += 1
        else:
            wins_b += 1
    total = wins_a + wins_b or 1
    return {
        "wins_a": wins_a,
        "wins_b": wins_b,
        "total":  total,
        "a_pct":  wins_a / total,
        "b_pct":  wins_b / total,
    }


def _recent_team_summary(games: list[dict]) -> dict[str, float]:
    if not games:
        return {
            "avg_scored": 110.0,
            "avg_allowed": 110.0,
            "avg_margin": 0.0,
            "avg_total": 220.0,
            "win_rate": 0.5,
            "games": 0,
        }

    scored = [_safe_f(g.get("our_pts")) for g in games[:5]]
    allowed = [_safe_f(g.get("their_pts")) for g in games[:5]]
    margins = [s - a for s, a in zip(scored, allowed)]
    wins = sum(1 for g in games[:5] if g.get("result") == "W")
    count = max(len(scored), 1)
    return {
        "avg_scored": round(sum(scored) / count, 1),
        "avg_allowed": round(sum(allowed) / count, 1),
        "avg_margin": round(sum(margins) / count, 1),
        "avg_total": round(sum(s + a for s, a in zip(scored, allowed)) / count, 1),
        "win_rate": round(wins / count, 3),
        "games": count,
    }


def _recent_h2h_summary(games: list[dict]) -> dict[str, float]:
    rows = []
    for game in games[:5]:
        scores = game.get("scores") or {}
        a_pts = scores.get("home", {}).get("points")
        b_pts = scores.get("visitors", {}).get("points")
        if a_pts is None or b_pts is None:
            continue
        rows.append((float(a_pts), float(b_pts)))

    if not rows:
        return {"avg_total": 0.0, "avg_margin": 0.0, "games": 0}

    totals = [a + b for a, b in rows]
    margins = [a - b for a, b in rows]
    return {
        "avg_total": round(sum(totals) / len(totals), 1),
        "avg_margin": round(sum(margins) / len(margins), 1),
        "games": len(rows),
    }


def _injury_penalty_value(injuries: list) -> float:
    penalty = 0.0
    for inj in injuries or []:
        status = str(inj.get("status") or "").lower()
        if status == "out":
            penalty += 1.0
        elif status == "doubtful":
            penalty += 0.7
        elif status == "questionable":
            penalty += 0.35
        else:
            penalty += 0.2
    return penalty


def _market_confidence_from_gap(value: float, *, kind: str) -> str:
    if kind == "winner":
        if value >= 16:
            return "High"
        if value >= 8:
            return "Medium"
        return "Low"
    if kind == "spread":
        if value >= 8.0:
            return "High"
        if value >= 4.0:
            return "Medium"
        return "Low"
    if value >= 8.0:
        return "High"
    if value >= 4.0:
        return "Medium"
    return "Low"


def _market_strength_label(confidence: str) -> str:
    return {
        "High": "Strong",
        "Medium": "Playable",
        "Low": "Thin edge",
    }.get(confidence, "Thin edge")


def _winner_reason(
    team_a_name: str,
    team_b_name: str,
    scorpred: dict,
    recent_a: dict,
    recent_b: dict,
    stats_a: dict,
    stats_b: dict,
    injuries_a: list,
    injuries_b: list,
) -> str:
    favored = team_a_name if _safe_f(scorpred.get("prob_a")) >= _safe_f(scorpred.get("prob_b")) else team_b_name
    if favored == team_a_name:
        return (
            f"{team_a_name} hold the stronger current profile through form ({recent_a['avg_margin']:+.1f} recent margin), "
            f"net rating ({_safe_f(stats_a.get('net_rtg')):+.1f}), and home-court context."
        )
    return (
        f"{team_b_name} have the cleaner winning case through form ({recent_b['avg_margin']:+.1f} recent margin), "
        f"net rating ({_safe_f(stats_b.get('net_rtg')):+.1f}), and the injury balance."
    )


def _spread_reason(
    team_a_name: str,
    team_b_name: str,
    expected_margin: float,
    net_edge: float,
    recent_edge: float,
    home_edge: float,
    injury_edge: float,
) -> str:
    side = team_a_name if expected_margin >= 0 else team_b_name
    return (
        f"This spread leans {side} because the projected margin is {expected_margin:+.1f}, driven by net-rating edge ({net_edge:+.1f}), "
        f"recent-form margin ({recent_edge:+.1f}), home court ({home_edge:+.1f}), and injury swing ({injury_edge:+.1f})."
    )


def _totals_reason(
    expected_total: float,
    total_line: float,
    recent_total: float,
    season_total_anchor: float,
    injury_adjustment: float,
    h2h_summary: dict,
) -> str:
    h2h_note = f" H2H meetings have averaged {h2h_summary['avg_total']:.1f}." if h2h_summary.get("games") else ""
    return (
        f"The projected total is {expected_total:.1f} against a model line of {total_line:.1f}, based on a season scoring baseline of {season_total_anchor:.1f}, "
        f"recent-form pace of {recent_total:.1f}, and injury adjustment of {injury_adjustment:+.1f}.{h2h_note}"
    )


def _parlay_alignment_summary(winner_leg: dict, spread_leg: dict, totals_leg: dict) -> dict[str, str | list[str]]:
    winner_side = str(winner_leg.get("recommendation") or "")
    spread_pick = str(spread_leg.get("recommendation") or "")
    same_side = winner_side and spread_pick.startswith(winner_side)

    weak_legs = [
        leg["leg_type"]
        for leg in (winner_leg, spread_leg, totals_leg)
        if str(leg.get("confidence") or "") == "Low"
    ]

    if same_side and not weak_legs:
        overall = "Strong alignment"
        summary = "Moneyline and spread tell the same game script, and the totals edge is strong enough to support a full parlay view."
    elif same_side:
        overall = "Selective alignment"
        summary = "Moneyline and spread align, but one of the supporting legs is thinner and should be treated more selectively."
    else:
        overall = "Split card"
        summary = "The winner and spread do not create a clean same-side stack, so this looks better as selective single legs than a full same-story parlay."

    if weak_legs:
        summary += f" Weak legs right now: {', '.join(weak_legs)}."

    return {
        "overall": overall,
        "summary": summary,
        "weak_legs": weak_legs,
    }


def _form_score(games: list, team_id: int) -> float:
    """Win-weighted form score. Recent games count more (linear decay)."""
    filtered_games = filter_completed_nba_games(games)
    if not filtered_games:
        return 0.5
    total_w, total_pts = 0.0, 0.0
    team_id = str(team_id)
    for i, g in enumerate(reversed(filtered_games)):  # most recent = index 0
        weight = 1.0 + i * 0.05              # older games slightly less weight
        home_id = str(g.get("teams", {}).get("home", {}).get("id") or "")
        visit_id = str(g.get("teams", {}).get("visitors", {}).get("id") or "")
        if team_id not in {home_id, visit_id}:
            continue
        scores   = g.get("scores", {})
        our_key   = "home" if home_id == team_id else "visitors"
        their_key = "visitors" if our_key == "home" else "home"
        our_pts   = _safe_f(scores.get(our_key,   {}).get("points"))
        their_pts = _safe_f(scores.get(their_key, {}).get("points"))
        if our_pts + their_pts == 0:
            continue
        won = our_pts > their_pts
        total_w   += weight
        total_pts += weight * (1.0 if won else 0.0)
    return total_pts / total_w if total_w else 0.5


def _injury_impact(injuries: list) -> float:
    """
    Compute an inverse injury score (higher = healthier = better).
    Starters / key players penalise more.
    """
    base = 1.0
    for inj in injuries:
        status = (inj.get("status") or "").lower()
        pos    = (inj.get("player", {}).get("pos") or "").upper()
        # Higher penalty for starters; knock-out = full penalty
        if status in ("out", "doubtful"):
            penalty = 0.12 if pos in ("G", "F", "C") else 0.07
        elif status == "questionable":
            penalty = 0.05
        else:
            penalty = 0.02
        base = max(0.3, base - penalty)
    return base


def _rating_score(stats_a: dict | None, stats_b: dict | None) -> tuple[float, float]:
    """Return (a_pct, b_pct) based on net rating differential."""
    net_a = (stats_a or {}).get("net_rtg", 0.0)
    net_b = (stats_b or {}).get("net_rtg", 0.0)
    # Normalise into 0-1 range — difference of 10+ pts → very decisive
    diff = net_a - net_b
    pct_a = _sigmoid(diff / 10.0)
    pct_b = 1.0 - pct_a
    return pct_a, pct_b


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _safe_f(val) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def _nba_date_key(game: dict) -> str:
    return str(((game.get("date") or {}).get("start") or ""))


def _is_completed_nba_game(game: dict) -> bool:
    status = game.get("status") or {}
    state = str(status.get("state") or "").strip().lower()
    if state == "post":
        return True

    text = " ".join(
        str(status.get(key) or "").strip().lower()
        for key in ("long", "short", "detail", "description")
    )
    return "final" in text or "finished" in text


def _round_half(x: float) -> float:
    """Round to nearest 0.5."""
    return round(x * 2) / 2


def _top_component(components: dict) -> str:
    """Return the name of the component with the biggest A-B spread."""
    biggest, name = 0.0, "form"
    for k, v in components.items():
        spread = abs(v.get("team_a_pct", 50) - v.get("team_b_pct", 50))
        if spread > biggest:
            biggest = spread
            name = k.replace("_", " ").title()
    return name


# ── Form extraction helpers ────────────────────────────────────────────────────

def extract_form_for_display(games: list, team_id: int) -> list[dict]:
    """
    Convert raw API game records to a clean display format.
    Returns list sorted most-recent first.
    """
    results = []
    team_id = str(team_id)
    for g in filter_completed_nba_games(games):
        home_id = str(g.get("teams", {}).get("home", {}).get("id") or "")
        visit_id = str(g.get("teams", {}).get("visitors", {}).get("id") or "")
        if team_id not in {home_id, visit_id}:
            continue
        scores   = g.get("scores", {})
        is_home  = (home_id == team_id)
        our_key   = "home" if is_home else "visitors"
        their_key = "visitors" if is_home else "home"

        our_pts   = scores.get(our_key,   {}).get("points")
        their_pts = scores.get(their_key, {}).get("points")

        opp_info  = g.get("teams", {}).get("visitors" if is_home else "home", {})
        date_raw  = g.get("date", {}).get("start", "")[:10]

        if our_pts is None or their_pts is None:
            continue

        our_pts, their_pts = int(our_pts), int(their_pts)
        result = "W" if our_pts > their_pts else "L"

        # Quarter-by-quarter
        our_qs   = scores.get(our_key,   {}).get("linescore", [])
        their_qs = scores.get(their_key, {}).get("linescore", [])

        results.append({
            "date":       date_raw,
            "is_home":    is_home,
            "venue":      "Home" if is_home else "Away",
            "opponent":   opp_info.get("name", "Unknown"),
            "opp_logo":   opp_info.get("logo", ""),
            "our_pts":    our_pts,
            "their_pts":  their_pts,
            "result":     result,
            "score":      f"{our_pts}–{their_pts}",
            "our_qs":     our_qs,
            "their_qs":   their_qs,
        })

    return results


def h2h_display(games: list, team_a_id: int, team_b_id: int) -> list[dict]:
    """
    Format H2H games for display with quarter breakdowns and score details.
    """
    rows = []
    team_a_id = str(team_a_id)
    team_b_id = str(team_b_id)
    for g in filter_completed_nba_games(games):
        home_id = str(g.get("teams", {}).get("home", {}).get("id") or "")
        visit_id = str(g.get("teams", {}).get("visitors", {}).get("id") or "")
        if {home_id, visit_id} != {team_a_id, team_b_id}:
            continue
        home_t   = g.get("teams", {}).get("home",     {})
        visit_t  = g.get("teams", {}).get("visitors", {})
        scores   = g.get("scores", {})

        home_pts  = scores.get("home",      {}).get("points")
        visit_pts = scores.get("visitors",  {}).get("points")
        date_raw  = g.get("date", {}).get("start", "")[:10]
        venue     = f"{home_t.get('name', '')} (Home)"

        if home_pts is None or visit_pts is None:
            continue

        home_pts, visit_pts = int(home_pts), int(visit_pts)
        winner_id = home_id if home_pts > visit_pts else visit_id

        # Quarters
        home_qs  = scores.get("home",     {}).get("linescore", [])
        visit_qs = scores.get("visitors", {}).get("linescore", [])

        # From team A perspective
        a_key = "home" if home_id == team_a_id else "visitors"
        b_key = "visitors" if a_key == "home" else "home"
        a_pts = home_pts  if a_key == "home" else visit_pts
        b_pts = visit_pts if a_key == "home" else home_pts
        a_qs  = home_qs   if a_key == "home" else visit_qs
        b_qs  = visit_qs  if a_key == "home" else home_qs

        rows.append({
            "date":       date_raw,
            "venue":      venue,
            "a_pts":      a_pts,
            "b_pts":      b_pts,
            "a_qs":       a_qs,
            "b_qs":       b_qs,
            "winner_id":  winner_id,
            "game_id":    g.get("id"),
            "home_team":  home_t.get("name"),
            "home_logo":  home_t.get("logo", ""),
            "visit_team": visit_t.get("name"),
            "visit_logo": visit_t.get("logo", ""),
        })
    return rows


def compute_last5_averages(game_records: list) -> dict | None:
    """Compute averages from a list of per-game stat records (last 5)."""
    played = [r for r in game_records if _has_played_local(r)]
    if not played:
        return None
    fields = ["points", "rebounds", "assists", "steals", "blocks",
              "turnovers", "tpm", "tpa", "fgm", "fga"]
    totals = {f: 0.0 for f in fields}
    n = len(played)
    for r in played:
        s = r.get("statistics", [{}])[0] if r.get("statistics") else {}
        for f in fields:
            totals[f] += _safe_f(s.get(f, 0))
    avgs = {f: round(totals[f] / n, 1) for f in fields}
    avgs["pra"] = round(avgs["points"] + avgs["rebounds"] + avgs["assists"], 1)
    return avgs


def _has_played_local(rec: dict) -> bool:
    s = rec.get("statistics", [{}])[0] if rec.get("statistics") else {}
    raw = s.get("min", "0") or "0"
    return raw not in ("0", "0:00", "", None)


# ── NBA Recent Form and H2H Helpers (similar to soccer update) ─────────────────

def filter_completed_nba_games(games: list, limit: int | None = None) -> list:
    """
    Filter NBA games to completed (final/post) games only, sort newest first.
    Similar to soccer's filter_recent_completed_fixtures().
    """
    if not games:
        return []
    completed = [g for g in games if _is_completed_nba_game(g)]
    completed.sort(key=_nba_date_key, reverse=True)
    if limit is not None:
        return completed[:limit]
    return completed


def extract_recent_form(games: list, team_id: int, n: int = 5) -> list[dict]:
    """
    Extract the last N completed games for a team as form data.
    Returns list of dicts with result, opponent, score, home/away.
    Similar to soccer's extract_form() but for NBA.
    """
    completed = filter_completed_nba_games(games)
    form = []
    for g in completed:
        home_id = g.get("teams", {}).get("home", {}).get("id")
        away_id = g.get("teams", {}).get("visitors", {}).get("id")
        if str(team_id) not in {str(home_id), str(away_id)}:
            continue
        is_home = (str(home_id) == str(team_id))
        our_key = "home" if is_home else "visitors"
        their_key = "visitors" if is_home else "home"
        
        our_pts = g.get("scores", {}).get(our_key, {}).get("points")
        their_pts = g.get("scores", {}).get(their_key, {}).get("points")
        
        if our_pts is None or their_pts is None:
            continue
        
        our_pts, their_pts = int(our_pts), int(their_pts)
        opp_info = g.get("teams", {}).get(their_key, {})
        result = "W" if our_pts > their_pts else "L"
        
        form.append({
            "date": g.get("date", {}).get("start", "")[:10],
            "is_home": is_home,
            "opponent": opp_info.get("name", "—"),
            "opp_logo": opp_info.get("logo", ""),
            "our_pts": our_pts,
            "their_pts": their_pts,
            "result": result,
            "score": f"{our_pts}–{their_pts}",
            "venue": "Home" if is_home else "Away",
        })
        if len(form) >= n:
            break
    
    return form


def build_h2h_summary(h2h_games: list, team_a_id: int, team_b_id: int, n: int = 5) -> dict:
    """
    Build H2H summary from last N completed meetings.
    Returns wins count and recent games list.
    Only counts actual wins, not ties.
    """
    completed = filter_completed_nba_games(h2h_games)[:n]
    
    wins_a = 0
    wins_b = 0
    games_list = []
    
    for g in completed:
        home_id = g.get("teams", {}).get("home", {}).get("id")
        scores = g.get("scores", {})
        home_pts = scores.get("home", {}).get("points")
        visit_pts = scores.get("visitors", {}).get("points")
        
        if home_pts is None or visit_pts is None:
            continue
        
        home_pts, visit_pts = int(home_pts), int(visit_pts)
        
        # Only count actual wins, skip ties
        if home_pts == visit_pts:
            # Skip ties - don't count them as wins
            pass
        elif home_id == team_a_id:
            if home_pts > visit_pts:
                wins_a += 1
            else:
                wins_b += 1
        else:
            if visit_pts > home_pts:
                wins_a += 1
            else:
                wins_b += 1
        
        games_list.append({
            "date": g.get("date", {}).get("start", "")[:10],
            "home_team": g.get("teams", {}).get("home", {}).get("name", "—"),
            "away_team": g.get("teams", {}).get("visitors", {}).get("name", "—"),
            "home_pts": home_pts,
            "away_pts": visit_pts,
        })
    
    return {
        "wins_a": wins_a,
        "wins_b": wins_b,
        "total": len(games_list),
        "games": games_list,
    }


def build_injury_summary(injuries: list, roster: list = None) -> dict:
    """
    Build injury summary with status breakdown.
    Infer healthy players from roster if provided.
    
    Returns:
    {
        "out": [...player dicts...],
        "doubtful": [...],
        "questionable": [...],
        "probable": [...],
        "healthy_count": int,
        "total_roster": int,
    }
    """
    status_map = {
        "out": [],
        "doubtful": [],
        "questionable": [],
        "probable": [],
    }
    
    if not injuries:
        injuries = []
    
    for inj in injuries:
        status_str = (inj.get("status") or "").lower()
        player = inj.get("player", {})
        
        summary = {
            "id": player.get("id"),
            "name": player.get("name", "Unknown"),
            "position": inj.get("position", "—"),
            "status": status_str,
            "return_date": inj.get("return_date", ""),
        }
        
        if status_str in status_map:
            status_map[status_str].append(summary)
        elif status_str == "available" or status_str == "":
            # Skip; these are not injuries
            pass
        else:
            # Unknown status; default to questionable
            status_map["questionable"].append(summary)
    
    # Calculate healthy count if roster provided
    healthy_count = 0
    total_roster = len(roster) if roster else 0
    if roster:
        injured_ids = set()
        for cat_list in status_map.values():
            for inj in cat_list:
                if inj.get("id"):
                    injured_ids.add(inj["id"])
        healthy_count = total_roster - len(injured_ids)
    
    return {
        "out": status_map["out"],
        "doubtful": status_map["doubtful"],
        "questionable": status_map["questionable"],
        "probable": status_map["probable"],
        "total_injured": sum(len(v) for v in status_map.values()),
        "healthy_count": healthy_count,
        "total_roster": total_roster,
    }


def build_key_player_stats_summary(roster: list, limit: int = 5) -> list[dict]:
    """
    Extract key players from roster sorted by scoring potential.
    If season averages available, use those; otherwise infer from position.
    """
    if not roster:
        return []
    
    # Simple heuristic: prioritize by position and presence of stats
    position_order = {"PG": 1, "SG": 2, "SF": 3, "PF": 4, "C": 5}
    
    players_with_rank = []
    for p in roster:
        # Check if injured
        injuries = p.get("injuries", [])
        is_out = any((inj.get("status") or "").lower() == "out" for inj in injuries)
        
        # Get position
        pos = ""
        if p.get("leagues") and p["leagues"].get("standard"):
            pos = p["leagues"]["standard"].get("pos", "") or ""
        if not pos:
            pos = p.get("position", "G")
        
        # Get basic info; if full stats not available, use position estimation
        rank = position_order.get(pos, 99)
        
        player_summary = {
            "id": p.get("id"),
            "name": f"{p.get('firstname', '')} {p.get('lastname', '')}".strip() or p.get("displayName", "—"),
            "position": pos,
            "injured": is_out,
            "games": p.get("games", 0),
            "points": p.get("points", 0),
            "rebounds": p.get("rebounds", 0),
            "assists": p.get("assists", 0),
        }
        
        # Use (not_injured, -rank, -points) for sorting to prioritize healthy starters
        sort_key = (not is_out, -rank, -float(player_summary.get("points", 0)))
        players_with_rank.append((sort_key, player_summary))
    
    # Sort and take top N
    players_with_rank.sort(key=lambda x: x[0], reverse=True)
    return [p[1] for p in players_with_rank[:limit]]
