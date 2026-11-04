"""
Scoreline and points predictor engine.

Ensemble approach:
- Soccer: Bivariate Poisson distribution (Dixon-Coles low-score correction) + ML win-prob blend
- NBA:    Normal distribution over projected totals + per-team score projections

Public API
----------
predict_soccer_scoreline(...) -> dict
predict_nba_scoreline(...)    -> dict
"""

from __future__ import annotations

import logging
import math
from typing import Any

from services.scoring_research import clean_form, finite

_log = logging.getLogger(__name__)


class InsufficientScoreData(ValueError):
    """A real scoring forecast cannot be constructed from the available data."""


def _research_completeness(projection):
    score = projection.get("feature_coverage", 1.0)
    return {"score": score, "tier": "high" if score >= .9 else "medium" if score >= .7 else "low",
            "factors": {"pregame_features": score == 1.0, "historical_evaluation": True}}

# ─── Poisson helpers ──────────────────────────────────────────────────────────

def _poisson_pmf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(k * math.log(lam) - lam - math.lgamma(k + 1))


def _normal_cdf(x: float, mean: float, sigma: float) -> float:
    """CDF of normal distribution P(X <= x) via erf."""
    z = (x - mean) / (sigma * math.sqrt(2))
    return 0.5 * (1.0 + math.erf(z))


# ─── Dixon-Coles low-score correction ─────────────────────────────────────────

def _dc_tau(i: int, j: int, lam_a: float, lam_b: float, rho: float) -> float:
    """Dixon-Coles (1997) correction for low-score cells."""
    if i == 0 and j == 0:
        return 1.0 - lam_a * lam_b * rho
    if i == 1 and j == 0:
        return 1.0 + lam_b * rho
    if i == 0 and j == 1:
        return 1.0 + lam_a * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


# ─── Soccer score matrix ──────────────────────────────────────────────────────

def _soccer_score_matrix(
    lam_a: float,
    lam_b: float,
    max_goals: int = 8,
    rho: float = -0.13,
) -> dict[tuple[int, int], float]:
    """
    Compute the bivariate Poisson probability matrix (Dixon-Coles corrected).
    Returns {(home_goals, away_goals): probability}, summing to ~1.
    """
    raw: dict[tuple[int, int], float] = {}
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = _poisson_pmf(i, lam_a) * _poisson_pmf(j, lam_b)
            tau = _dc_tau(i, j, lam_a, lam_b, rho)
            raw[(i, j)] = max(0.0, p * tau)
    total = sum(raw.values()) or 1.0
    return {k: v / total for k, v in raw.items()}


def _ou_from_matrix(
    matrix: dict[tuple[int, int], float],
    lines: list[float],
) -> list[dict[str, Any]]:
    results = []
    for line in lines:
        over = sum(v for (i, j), v in matrix.items() if (i + j) > line)
        under = sum(v for (i, j), v in matrix.items() if (i + j) < line)
        push = sum(v for (i, j), v in matrix.items() if (i + j) == line)
        results.append({
            "line": line,
            "over_prob": round(over * 100, 1),
            "under_prob": round(under * 100, 1),
            "push_prob": round(push * 100, 1),
            "lean": "OVER" if over > under else "UNDER",
        })
    return results


def _result_probs(matrix: dict[tuple[int, int], float]) -> dict[str, float]:
    hw = sum(v for (i, j), v in matrix.items() if i > j)
    d = sum(v for (i, j), v in matrix.items() if i == j)
    aw = sum(v for (i, j), v in matrix.items() if i < j)
    return {"home": round(hw * 100, 1), "draw": round(d * 100, 1), "away": round(aw * 100, 1)}


def _btts(matrix: dict[tuple[int, int], float]) -> dict[str, float]:
    yes = sum(v for (i, j), v in matrix.items() if i > 0 and j > 0)
    return {"yes": round(yes * 100, 1), "no": round((1.0 - yes) * 100, 1)}


def _top_scorelines(matrix: dict[tuple[int, int], float], n: int = 12) -> list[dict[str, Any]]:
    ranked = sorted(matrix.items(), key=lambda x: x[1], reverse=True)[:n]
    return [
        {
            "home": i,
            "away": j,
            "score": f"{i}-{j}",
            "prob": round(v * 100, 2),
            "result": "H" if i > j else ("D" if i == j else "A"),
        }
        for (i, j), v in ranked
    ]


# ─── Lambda calibration from form data ────────────────────────────────────────

def _compute_data_completeness(
    form_a: list, form_b: list,
    h2h: list | None,
    injuries_a: list | None, injuries_b: list | None,
    opp_str_a: float | None, opp_str_b: float | None,
    odds_total: float | None,
) -> dict:
    """
    Calculate data completeness score and tier.

    Returns:
        {
            "score": 0.0-1.0,
            "tier": "high" | "medium" | "low",
            "factors": { "form": bool, "h2h": bool, ... }
        }
    """
    factors = {
        "form": len(form_a or []) >= 3 and len(form_b or []) >= 3,
        "h2h": len(h2h or []) >= 2,
        "injuries": injuries_a is not None and injuries_b is not None,
        "standings": opp_str_a is not None and opp_str_b is not None,
        "market": odds_total is not None,
    }
    score = sum(factors.values()) / len(factors)

    if score >= 0.8:
        tier = "high"
    elif score >= 0.5:
        tier = "medium"
    else:
        tier = "low"

    return {"score": round(score, 2), "tier": tier, "factors": factors}


def _compute_market_comparison(
    model_ou_prob: float,
    market_ou_prob: float | None,
    threshold: float = 5.0,
) -> dict | None:
    """
    Compare model O/U probability to market-implied probability.

    Args:
        model_ou_prob: Model's probability for OVER (0-100)
        market_ou_prob: Market-implied probability (0-100), if available
        threshold: Edge threshold in percentage points for "significant"

    Returns:
        {
            "model_prob": float,
            "market_prob": float,
            "edge": float,  # model - market, in percentage points
            "signal": "value" | "caution" | "fair",
        }
        or None if market data unavailable
    """
    if market_ou_prob is None:
        return None

    edge = model_ou_prob - market_ou_prob

    if edge >= threshold:
        signal = "value"
    elif edge <= -threshold:
        signal = "caution"
    else:
        signal = "fair"

    return {
        "model_prob": round(model_ou_prob, 1),
        "market_prob": round(market_ou_prob, 1),
        "edge": round(edge, 1),
        "signal": signal,
    }


# ─── Lambda calibration from form data ────────────────────────────────────────

def _weighted_goals(form: list[dict], key: str) -> float:
    """Recency-weighted average goals from form records."""
    if not form:
        return 1.25
    decay = [0.35, 0.25, 0.18, 0.13, 0.09]
    wsum = 0.0
    wtotal = 0.0
    for i, m in enumerate(form[:5]):
        w = decay[i] if i < len(decay) else 0.05
        wsum += float(m.get(key) or 0) * w
        wtotal += w
    return max(0.2, wsum / wtotal) if wtotal > 0 else 1.25


def _calibrate_lambdas(
    form_a: list[dict],
    form_b: list[dict],
    h2h: list[dict] | None,
    injuries_a: list | None,
    injuries_b: list | None,
    is_home_a: bool,
    odds_total: float | None,
    opp_strength_a: float | None = None,
    opp_strength_b: float | None = None,
) -> tuple[float, float]:
    gf_a = _weighted_goals(form_a, "gf")
    ga_a = _weighted_goals(form_a, "ga")
    gf_b = _weighted_goals(form_b, "gf")
    ga_b = _weighted_goals(form_b, "ga")

    lam_a = (gf_a + ga_b) / 2.0
    lam_b = (gf_b + ga_a) / 2.0

    # H2H blend: up to 25% weight normally; 60% when both form lists are empty
    # (lower threshold to 1 match so any H2H data helps differentiate lambdas)
    if h2h and len(h2h) >= 1:
        no_form = not form_a and not form_b
        h2h_w = 0.60 if no_form else min(0.25, len(h2h) * 0.05)
        h2h_gf_a = sum(float(m.get("gf") or 0) for m in h2h[:5]) / max(len(h2h[:5]), 1)
        h2h_gf_b = sum(float(m.get("ga") or 0) for m in h2h[:5]) / max(len(h2h[:5]), 1)
        lam_a = lam_a * (1 - h2h_w) + h2h_gf_a * h2h_w
        lam_b = lam_b * (1 - h2h_w) + h2h_gf_b * h2h_w

    # Home advantage
    if is_home_a:
        lam_a *= 1.10
    else:
        lam_b *= 1.10

    # Opponent strength scaling (15% max adjustment based on 0-10 scale)
    # Strong opponent (8-10) → reduces your lambda by up to 12%
    # Weak opponent (0-2) → increases your lambda by up to 12%
    if opp_strength_a is not None:
        factor_a = 1.0 + (5.0 - opp_strength_a) * 0.024
        lam_a *= max(0.88, min(1.12, factor_a))
    if opp_strength_b is not None:
        factor_b = 1.0 + (5.0 - opp_strength_b) * 0.024
        lam_b *= max(0.88, min(1.12, factor_b))

    # Injury penalty (~2.5 pp per absent player, capped at 15%)
    if injuries_a:
        lam_a *= max(0.85, 1.0 - len(injuries_a) * 0.025)
    if injuries_b:
        lam_b *= max(0.85, 1.0 - len(injuries_b) * 0.025)

    # Odds-implied total anchoring (40% weight)
    if odds_total and odds_total > 0:
        poisson_total = lam_a + lam_b
        if poisson_total > 0:
            ratio = odds_total / poisson_total
            lam_a = lam_a * (0.6 + 0.4 * ratio)
            lam_b = lam_b * (0.6 + 0.4 * ratio)

    return max(0.2, lam_a), max(0.2, lam_b)


def _ml_blend_lambdas(
    lam_a: float,
    lam_b: float,
    ml_prob_a: float,
    ml_prob_b: float,
    ml_weight: float = 0.30,
) -> tuple[float, float]:
    """Nudge lambdas so Poisson win probs move toward ML win probs (30% blend)."""
    matrix = _soccer_score_matrix(lam_a, lam_b)
    probs = _result_probs(matrix)
    p_a = probs["home"] / 100.0
    p_b = probs["away"] / 100.0
    ml_a = max(0.05, min(0.90, ml_prob_a / 100.0 if ml_prob_a > 1 else float(ml_prob_a)))
    ml_b = max(0.05, min(0.90, ml_prob_b / 100.0 if ml_prob_b > 1 else float(ml_prob_b)))
    if p_a > 0 and p_b > 0:
        lam_a = lam_a * (ml_a / p_a) ** ml_weight
        lam_b = lam_b * (ml_b / p_b) ** ml_weight
    return max(0.2, lam_a), max(0.2, lam_b)


# ─── Soccer full prediction ────────────────────────────────────────────────────

def predict_soccer_scoreline(
    form_a: list[dict],
    form_b: list[dict],
    h2h: list[dict] | None = None,
    injuries_a: list | None = None,
    injuries_b: list | None = None,
    is_home_a: bool = True,
    ml_prob_a: float | None = None,
    ml_prob_b: float | None = None,
    odds_total: float | None = None,
    team_a_name: str = "Home",
    team_b_name: str = "Away",
    ou_lines: list[float] | None = None,
    opp_strength_a: float | None = None,
    opp_strength_b: float | None = None,
    research_projection: dict | None = None,
) -> dict[str, Any]:
    """
    Full soccer scoreline prediction: Poisson + ML ensemble.

    Returns dict with: top_scorelines, over_under lines, btts, win_probs,
    projected_score, score_ranges, primary_ou_line.
    """
    form_a, form_b = clean_form(form_a, "soccer"), clean_form(form_b, "soccer")
    if min(len(form_a), len(form_b)) < 3:
        raise InsufficientScoreData("Soccer scoring needs at least three completed games with scores per team.")
    if ou_lines is None:
        ou_lines = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]

    # Compute data completeness
    completeness = _compute_data_completeness(
        form_a, form_b, h2h, injuries_a, injuries_b,
        opp_strength_a, opp_strength_b, odds_total
    )

    lam_a, lam_b = _calibrate_lambdas(
        form_a, form_b, h2h, injuries_a, injuries_b, is_home_a, odds_total,
        opp_strength_a, opp_strength_b
    )

    if ml_prob_a is not None and ml_prob_b is not None:
        try:
            lam_a, lam_b = _ml_blend_lambdas(lam_a, lam_b, ml_prob_a, ml_prob_b)
        except (ValueError, TypeError, ArithmeticError):
            _log.warning("ML blend failed; using Poisson-only lambdas", exc_info=True)

    researched = bool(research_projection and research_projection.get("available"))
    rho = -.13
    if researched:
        lam_a = research_projection["projected_score"]["home"]
        lam_b = research_projection["projected_score"]["away"]
        rho = research_projection["rho"]
        completeness = _research_completeness(research_projection)
    matrix = _soccer_score_matrix(lam_a, lam_b, max_goals=max(12, math.ceil(max(lam_a, lam_b) + 8 * math.sqrt(max(lam_a, lam_b)))), rho=rho)
    ou = _ou_from_matrix(matrix, ou_lines)
    btts = _btts(matrix)
    top = _top_scorelines(matrix, n=12)
    win_probs = _result_probs(matrix)

    exp_home = sum(i * v for (i, j), v in matrix.items())
    exp_away = sum(j * v for (i, j), v in matrix.items())

    low = sum(v for (i, j), v in matrix.items() if (i + j) <= 1) * 100
    mid = sum(v for (i, j), v in matrix.items() if 2 <= (i + j) <= 3) * 100
    high = sum(v for (i, j), v in matrix.items() if (i + j) >= 4) * 100

    primary_ou = next(
        (o for o in ou if o["line"] == (odds_total or 2.5)),
        min(ou, key=lambda o: abs(o["line"] - (odds_total or 2.5))),
    )

    # Build full matrix as list for frontend heat-map (0-6 x 0-6)
    matrix_grid = [
        {
            "home": i,
            "away": j,
            "score": f"{i}-{j}",
            "prob": round(matrix.get((i, j), 0.0) * 100, 2),
        }
        for i in range(7)
        for j in range(7)
    ]

    # Compute market comparison if odds available
    market_comparison = None
    # A totals line alone is not a bookmaker probability. Do not fabricate an edge.

    return {
        "sport": "soccer",
        "team_a": team_a_name,
        "team_b": team_b_name,
        "lambda_a": round(lam_a, 3),
        "lambda_b": round(lam_b, 3),
        "projected_score": {
            "home": round(exp_home, 1),
            "away": round(exp_away, 1),
            "total": round(exp_home + exp_away, 1),
        },
        "most_likely_score": top[0] if top else {},
        "top_scorelines": top,
        "matrix_grid": matrix_grid,
        "win_probabilities": win_probs,
        "over_under": ou,
        "btts": btts,
        "score_ranges": {
            "low_0_1_goals": round(low, 1),
            "medium_2_3_goals": round(mid, 1),
            "high_4_plus_goals": round(high, 1),
        },
        "primary_ou_line": primary_ou,
        "model": research_projection["model"] if researched else "poisson_dc_descriptive",
        "research": research_projection,
        "notice": research_projection["notice"] if researched else "Descriptive score distribution; no validated scoring artifact is available for this request.",
        "data_inputs": {
            "form_a_games": len(form_a),
            "form_b_games": len(form_b),
            "h2h_games": len(h2h) if h2h else 0,
            "injuries_a": len(injuries_a) if injuries_a else 0,
            "injuries_b": len(injuries_b) if injuries_b else 0,
            "odds_anchored": odds_total is not None and not researched,
            "ml_blended": ml_prob_a is not None and not researched,
        },
        "data_completeness": completeness,
        "market_comparison": market_comparison,
    }


# ─── NBA points distribution ──────────────────────────────────────────────────

def _calibrate_nba_projections(
    stats_a: dict | None,
    stats_b: dict | None,
    form_a: list[dict] | None,
    form_b: list[dict] | None,
    injuries_a: list | None,
    injuries_b: list | None,
    is_home_a: bool,
    opp_strength_a: float | None = None,
    opp_strength_b: float | None = None,
) -> tuple[float, float]:
    """Calibrate projected team points from season stats + form + home advantage."""
    stats_a = stats_a or {}
    stats_b = stats_b or {}

    def scoring_stats(stats, form):
        ppg, conceded = finite(stats.get("ppg")), finite(stats.get("opp_ppg"))
        if ppg is not None and conceded is not None and min(ppg, conceded) > 0:
            return ppg, conceded
        if len(form or []) < 3:
            raise InsufficientScoreData("NBA scoring needs scoring averages or at least three completed games with scores per team.")
        rows = form[:6]
        return sum(row["our_pts"] for row in rows) / len(rows), sum(row["their_pts"] for row in rows) / len(rows)
    ppg_a, opp_ppg_a = scoring_stats(stats_a, form_a)
    ppg_b, opp_ppg_b = scoring_stats(stats_b, form_b)

    proj_a = ppg_a * 0.55 + opp_ppg_b * 0.45
    proj_b = ppg_b * 0.55 + opp_ppg_a * 0.45

    # Recent form blend (35% weight)
    if form_a:
        pts_list = [float(m.get("our_pts") or m.get("pts") or m.get("gf") or 0) for m in form_a[:5]]
        if any(p > 0 for p in pts_list):
            proj_a = proj_a * 0.65 + (sum(pts_list) / len(pts_list)) * 0.35
    if form_b:
        pts_list = [float(m.get("our_pts") or m.get("pts") or m.get("gf") or 0) for m in form_b[:5]]
        if any(p > 0 for p in pts_list):
            proj_b = proj_b * 0.65 + (sum(pts_list) / len(pts_list)) * 0.35

    # Home court advantage (~4 pt swing)
    if is_home_a:
        proj_a += 2.0
        proj_b -= 2.0
    else:
        proj_b += 2.0
        proj_a -= 2.0

    # Opponent strength scaling (stronger opponent → reduces your projection)
    if opp_strength_a is not None:
        factor_a = 1.0 + (5.0 - opp_strength_a) * 0.01
        proj_a *= max(0.955, min(1.045, factor_a))
    if opp_strength_b is not None:
        factor_b = 1.0 + (5.0 - opp_strength_b) * 0.01
        proj_b *= max(0.955, min(1.045, factor_b))

    # Injury penalty (~2 pts per key player, capped at 8)
    if injuries_a:
        proj_a -= min(8.0, len(injuries_a) * 2.0)
    if injuries_b:
        proj_b -= min(8.0, len(injuries_b) * 2.0)

    return max(85.0, proj_a), max(85.0, proj_b)


def _nba_ou_probs(
    proj_total: float,
    sigma: float,
    lines: list[float],
) -> list[dict[str, Any]]:
    results = []
    for line in lines:
        integer_line = float(line).is_integer()
        under_prob = _normal_cdf(line - .5 if integer_line else math.floor(line) + .5, proj_total, sigma)
        upper = _normal_cdf(line + .5, proj_total, sigma) if integer_line else under_prob
        over_prob = 1.0 - upper
        results.append({
            "line": line,
            "over_prob": round(over_prob * 100, 1),
            "under_prob": round(under_prob * 100, 1),
            "push_prob": round((upper - under_prob) * 100, 1),
            "lean": "OVER" if over_prob > under_prob else "UNDER",
        })
    return results


def _nba_score_distribution(
    proj_a: float,
    proj_b: float,
    sigma_a: float = 8.5,
    sigma_b: float = 8.5,
    n: int = 20,
) -> list[dict[str, Any]]:
    """Sample most-probable score pairs from independent normals."""
    scores: list[dict[str, Any]] = []
    r_a = range(max(0, math.floor(proj_a - 6 * sigma_a)), math.ceil(proj_a + 6 * sigma_a) + 1)
    r_b = range(max(0, math.floor(proj_b - 6 * sigma_b)), math.ceil(proj_b + 6 * sigma_b) + 1)
    for a in r_a:
        for b in r_b:
            if a == b:
                continue  # NBA final scores cannot be tied; condition on a winner.
            p_a = _normal_cdf(a + .5, proj_a, sigma_a) - _normal_cdf(a - .5, proj_a, sigma_a)
            p_b = _normal_cdf(b + .5, proj_b, sigma_b) - _normal_cdf(b - .5, proj_b, sigma_b)
            scores.append({
                "home": a, "away": b, "score": f"{a}-{b}",
                "prob": p_a * p_b,
                "result": "H" if a > b else ("A" if b > a else "D"),
            })
    scores.sort(key=lambda x: x["prob"], reverse=True)
    total = sum(s["prob"] for s in scores) or 1.0
    for s in scores:
        s["prob"] = round((s["prob"] / total) * 100, 2)
    return scores[:n]


def predict_nba_scoreline(
    stats_a: dict | None = None,
    stats_b: dict | None = None,
    form_a: list[dict] | None = None,
    form_b: list[dict] | None = None,
    injuries_a: list | None = None,
    injuries_b: list | None = None,
    is_home_a: bool = True,
    ml_prob_a: float | None = None,
    ml_prob_b: float | None = None,
    odds_total: float | None = None,
    team_a_name: str = "Home",
    team_b_name: str = "Away",
    ou_lines: list[float] | None = None,
    opp_strength_a: float | None = None,
    opp_strength_b: float | None = None,
    research_projection: dict | None = None,
) -> dict[str, Any]:
    """
    Full NBA scoreline / points distribution prediction.

    Returns dict with: projected_score, over_under lines, top_scorelines,
    win_probabilities, primary_ou_line, spread info.
    """
    form_a, form_b = clean_form(form_a, "nba"), clean_form(form_b, "nba")
    if ou_lines is None:
        ou_lines = [210.5, 215.5, 220.5, 225.5, 230.5, 235.5, 240.5]

    # Compute data completeness (NBA: h2h typically None, so pass empty list)
    completeness = _compute_data_completeness(
        form_a or [], form_b or [], None,
        injuries_a, injuries_b,
        opp_strength_a, opp_strength_b, odds_total
    )

    proj_a, proj_b = _calibrate_nba_projections(
        stats_a, stats_b, form_a or [], form_b or [],
        injuries_a, injuries_b, is_home_a,
        opp_strength_a, opp_strength_b
    )
    researched = bool(research_projection and research_projection.get("available"))
    if researched:
        proj_a = research_projection["projected_score"]["home"]
        proj_b = research_projection["projected_score"]["away"]
        completeness = _research_completeness(research_projection)
    proj_total = proj_a + proj_b

    # Odds anchoring (45% weight when available)
    if odds_total and odds_total > 0 and not researched:
        blended_total = proj_total * 0.55 + odds_total * 0.45
        ratio = blended_total / proj_total if proj_total > 0 else 1.0
        proj_a *= ratio
        proj_b *= ratio
        proj_total = proj_a + proj_b

    sigma_total = research_projection["sigma_total"] if researched else 12.5
    sigma_a = research_projection["sigma_home"] if researched else 8.5
    sigma_b = research_projection["sigma_away"] if researched else 8.5

    ou = _nba_ou_probs(proj_total, sigma_total, ou_lines)
    top = _nba_score_distribution(proj_a, proj_b, sigma_a, sigma_b, n=20)

    # Win probability
    if ml_prob_a is not None and ml_prob_b is not None and not researched:
        win_probs = {
            "home": round(float(ml_prob_a), 1),
            "away": round(float(ml_prob_b), 1),
        }
    else:
        margin = proj_a - proj_b
        p_a = _normal_cdf(0, -margin, math.hypot(sigma_a, sigma_b) if researched else 10.5)
        win_probs = {"home": round(p_a * 100, 1), "away": round((1 - p_a) * 100, 1)}

    primary_ou = next(
        (o for o in ou if o["line"] == (odds_total or 225.5)),
        min(ou, key=lambda o: abs(o["line"] - (odds_total or 225.5))),
    )

    proj_margin = round(proj_a - proj_b, 1)

    return {
        "sport": "nba",
        "team_a": team_a_name,
        "team_b": team_b_name,
        "projected_score": {
            "home": round(proj_a, 1),
            "away": round(proj_b, 1),
            "total": round(proj_total, 1),
        },
        "proj_margin": proj_margin,
        "spread_favorite": team_a_name if proj_margin > 0 else team_b_name if proj_margin < 0 else "Even",
        "most_likely_range": top[0] if top else {},
        "top_scorelines": top,
        "win_probabilities": win_probs,
        "over_under": ou,
        "primary_ou_line": primary_ou,
        "sigma_total": sigma_total,
        "model": research_projection["model"] if researched else "normal_descriptive",
        "research": research_projection,
        "notice": research_projection["notice"] if researched else "Descriptive score distribution; no validated scoring artifact is available for this request.",
        "data_inputs": {
            "form_a_games": len(form_a) if form_a else 0,
            "form_b_games": len(form_b) if form_b else 0,
            "injuries_a": len(injuries_a) if injuries_a else 0,
            "injuries_b": len(injuries_b) if injuries_b else 0,
            "odds_anchored": odds_total is not None and not researched,
            "ml_blended": ml_prob_a is not None and not researched,
        },
        "data_completeness": completeness,
    }
