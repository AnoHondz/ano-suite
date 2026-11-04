"""Verified track record for published rule-engine picks, plus the soccer market benchmark.

Only picks first recorded before a known kickoff are counted. Late or unverifiable
records are reported as exclusions rather than silently dropped.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from typing import Any

import model_tracker as mt
from runtime_paths import repo_root

MINIMUM_SAMPLE = 100
_EPSILON = 1e-6
_SIDES = ("a", "draw", "b")
_CALIBRATION_EDGES = (0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 1.01)
NBA_REGULAR_SEASON = 2
_MARKET_REPORT = repo_root() / "data" / "validation" / "soccer_model_research.json"


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> list[float] | None:
    if trials <= 0:
        return None
    phat = successes / trials
    denominator = 1 + z * z / trials
    centre = (phat + z * z / (2 * trials)) / denominator
    spread = z * math.sqrt(phat * (1 - phat) / trials + z * z / (4 * trials * trials)) / denominator
    return [round(max(0.0, centre - spread), 4), round(min(1.0, centre + spread), 4)]


def _outcome(row: dict) -> str | None:
    """Actual result as a probability key: a (home), b (away) or draw."""
    team_a, team_b = str(row.get("team_a") or ""), str(row.get("team_b") or "")
    code = mt._canonical_outcome(row.get("actual_result"), team_a, team_b) or mt._actual_result_from_score(row.get("final_score"))
    return {"A": "a", "B": "b", "draw": "draw"}.get(code)


def _probabilities(row: dict) -> dict[str, float] | None:
    values = {}
    for side in _SIDES:
        try:
            values[side] = max(0.0, float(row.get(f"prob_{side}") or 0))
        except (TypeError, ValueError):
            return None
    total = sum(values.values())
    return {side: value / total for side, value in values.items()} if total > 0 else None


def _pick_side(row: dict) -> str | None:
    code = mt._canonical_outcome(row.get("predicted_winner"), str(row.get("team_a") or ""), str(row.get("team_b") or ""))
    return {"A": "a", "B": "b", "draw": "draw"}.get(code)


def _log_loss(probabilities: dict[str, float], outcome: str) -> float:
    return -math.log(max(_EPSILON, probabilities.get(outcome, 0.0)))


def _brier(probabilities: dict[str, float], outcome: str, sides: tuple[str, ...]) -> float:
    """Mean squared error across the outcome classes (same convention as the market benchmark)."""
    return sum((probabilities.get(side, 0.0) - (side == outcome)) ** 2 for side in sides) / len(sides)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _probability_scores(rows: list[tuple[dict, dict, str]], sport: str) -> dict[str, Any]:
    """Log loss, Brier and top-pick calibration bins (with counts) over scored picks."""
    sides = _SIDES if sport == "soccer" else ("a", "b")
    bins: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for _row, probabilities, outcome in rows:
        top = max(sides, key=lambda side: probabilities.get(side, 0.0))
        top_probability = probabilities[top]
        index = next(i for i in range(len(_CALIBRATION_EDGES) - 1) if top_probability < _CALIBRATION_EDGES[i + 1])
        bins[index].append((top_probability, top == outcome))
    return {
        "scored": len(rows),
        "log_loss": _mean([_log_loss(p, o) for _r, p, o in rows]),
        "brier": _mean([_brier(p, o, sides) for _r, p, o in rows]),
        "calibration": [
            {"range": [_CALIBRATION_EDGES[i], min(1.0, _CALIBRATION_EDGES[i + 1])], "picks": len(bins[i]),
             "mean_probability": _mean([p for p, _hit in bins[i]]),
             "hit_rate": _mean([float(hit) for _p, hit in bins[i]])}
            for i in sorted(bins)
        ],
    }


def _market_comparison(rows: list[tuple[dict, dict, str]]) -> dict[str, Any]:
    """Model against the margin-free price seen when the pick was made, on the same games."""
    priced = [(p, (row.get("market_at_pick") or {}).get("fair") or {}, o) for row, p, o in rows]
    priced = [(p, market, o) for p, market, o in priced if market.get("a") and market.get("b")]
    model = _mean([_log_loss(p, o) for p, _m, o in priced])
    market = _mean([_log_loss(m, o) for _p, m, o in priced])
    return {"games": len(priced), "model_log_loss": model, "market_log_loss": market,
            "model_minus_market": None if model is None else round(model - market, 4)}


def _closing_line_value(verified: list[dict]) -> dict[str, Any]:
    """Change in the market's probability of the picked side between the pick and kickoff.

    Positive means the price moved toward the pick after it was made, the usual sign of a
    real edge. Graded and pending picks both count; the result is not needed.
    """
    values = []
    for row in verified:
        side = _pick_side(row)
        opening = ((row.get("market_at_pick") or {}).get("fair") or {}).get(side)
        closing = ((row.get("closing_market") or {}).get("fair") or {}).get(side)
        if side and opening and closing:
            values.append(closing / opening - 1)
    return {"picks": len(values), "mean": _mean(values),
            "positive_share": _mean([float(value > 0) for value in values])}


def _weekly(rows: list[tuple[dict, dict, str]]) -> list[dict[str, Any]]:
    weeks: dict[str, list[tuple[dict, str, dict]]] = defaultdict(list)
    for row, probabilities, outcome in rows:
        kickoff = mt._aware_datetime(row.get("kickoff_at"))
        if kickoff is None:
            continue
        year, week, _ = kickoff.isocalendar()
        weeks[f"{year}-W{week:02d}"].append((probabilities, outcome, row))
    return [{"week": week, "scored": len(items),
             "log_loss": _mean([_log_loss(p, o) for p, o, _r in items]),
             "accuracy": _mean([float(bool(r.get("is_correct"))) for _p, _o, r in items])}
            for week, items in sorted(weeks.items())]


def _score_forecast(verified: list[dict]) -> dict[str, Any]:
    """Predicted score against the final score: mean absolute error per team and on the total."""
    grades = [r["score_grade"] for r in verified if isinstance(r.get("score_grade"), dict)]
    exact = [g["exact_hit"] for g in grades if "exact_hit" in g]
    return {"graded": len(grades),
            "team_error": _mean([(g["error_a"] + g["error_b"]) / 2 for g in grades]),
            "total_error": _mean([g["error_total"] for g in grades]),
            "exact_score_hits": sum(exact), "exact_score_rate": _mean([float(hit) for hit in exact])}


RECENT_LIMIT = 30


def _learning() -> dict[str, Any]:
    from services import recalibration
    current = recalibration.state()
    return {"minimum_picks": recalibration.MIN_PICKS,
            "sports": {sport: {k: (current.get(sport) or {}).get(k) for k in
                               ("active", "picks", "engine_version", "reason", "holdout_gain", "holdout_gain_95ci",
                                "params", "checked_at")} for sport in recalibration.SPORTS}}


def _recent(records: list[dict]) -> list[dict[str, Any]]:
    """Latest verified pregame picks, predicted against what happened."""
    rows = [r for r in records if mt.verified_pregame(r) and str(r.get("sport", "")).lower() in ("soccer", "nba")]
    rows.sort(key=lambda r: r.get("kickoff_at") or "", reverse=True)
    out = []
    for r in rows[:RECENT_LIMIT]:
        side = _pick_side(r)
        probabilities = _probabilities(r) or {}
        final = r.get("final_score") if isinstance(r.get("final_score"), dict) else None
        out.append({
            "id": r.get("id"), "sport": r.get("sport"), "kickoff": r.get("kickoff_at"),
            "home": r.get("team_a"), "away": r.get("team_b"), "pick": r.get("predicted_pick_label") or r.get("predicted_winner"),
            "pick_probability": None if side is None else round(probabilities.get(side, 0.0), 3),
            "predicted_score": r.get("predicted_score"),
            "final_score": final and {"a": final.get("a"), "b": final.get("b")},
            "result": "pending" if r.get("status") != "completed" else "correct" if r.get("is_correct") else "wrong",
            "engine": (r.get("engine") or {}).get("version") or (r.get("engine") or {}).get("code_sha"),
        })
    return out


def _sport_summary(records: list[dict], sport: str) -> dict[str, Any]:
    rows = [r for r in records if str(r.get("sport", "")).lower() == sport]
    verified = [r for r in rows if mt.verified_pregame(r)]
    excluded_late = sum(1 for r in rows if r.get("kickoff_at") and not mt.verified_pregame(r))
    excluded_unverifiable = sum(1 for r in rows if not r.get("kickoff_at"))
    excluded_preseason = 0
    if sport == "nba":
        excluded_preseason = sum(1 for r in verified if r.get("season_type") not in (None, NBA_REGULAR_SEASON))
        verified = [r for r in verified if r.get("season_type") in (None, NBA_REGULAR_SEASON)]
    graded = [r for r in verified if r.get("status") == "completed" and r.get("is_correct") is not None]
    correct = sum(1 for r in graded if r.get("is_correct"))
    accuracy = round(correct / len(graded), 4) if graded else None
    scored = [(r, probabilities, outcome) for r in verified if r.get("status") == "completed"
              and (probabilities := _probabilities(r)) and (outcome := _outcome(r))]
    engines = Counter(((r.get("engine") or {}).get("code_sha") or "unrecorded") for r in verified)
    return {
        "graded": len(graded),
        "correct": correct,
        "accuracy": accuracy,
        "accuracy_95ci": wilson_interval(correct, len(graded)),
        "pending": sum(1 for r in verified if r.get("status") != "completed"),
        "excluded": {"recorded_after_kickoff": excluded_late, "no_kickoff_time": excluded_unverifiable,
                     "preseason": excluded_preseason},
        "sample_status": "sufficient" if len(graded) >= MINIMUM_SAMPLE else "insufficient",
        "first_kickoff": min((r["kickoff_at"] for r in graded), default=None),
        "last_kickoff": max((r["kickoff_at"] for r in graded), default=None),
        "probability": _probability_scores(scored, sport),
        "market": _market_comparison(scored),
        "closing_line_value": _closing_line_value(verified),
        "weekly": _weekly(scored),
        "score_forecast": _score_forecast(verified),
        "engines": dict(engines),
    }


def _market_benchmark() -> dict[str, Any] | None:
    """The published soccer model's walk-forward comparison with bookmaker prices."""
    try:
        report = json.loads(_MARKET_REPORT.read_text(encoding="utf-8"))
        seasons = report["data"]["evaluated_seasons"]
        model, market = report["overall"]["fitted"], report["overall"]["market_close"]
        gap = report["log_loss_gap"]["fitted"]["vs_market_close"]
        betting = report["betting_at_pre_weekend_prices"]["fitted"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    season = lambda code: f"20{code[:2]}-{code[2:]}"  # noqa: E731
    return {
        "generated_at": report["generated_at"],
        "test_window": {"start": season(seasons[0]), "end": season(seasons[-1])},
        "matches": model["matches"],
        "model_accuracy": model["accuracy"],
        "market_accuracy": market["accuracy"],
        "log_loss_gap": gap["mean"],
        "log_loss_gap_95ci": gap["95ci"],
        "model_pick_roi": betting["top_pick"]["roi"],
        "model_pick_roi_95ci": betting["top_pick"]["roi_95ci"],
        "value_bet_roi": betting["edge_over_5pct"]["roi"],
        "value_bet_roi_95ci": betting["edge_over_5pct"]["roi_95ci"],
        "value_bets": betting["edge_over_5pct"]["bets"],
        "model_predicted_draws": model["draw_picks"],
        "previous_engine_log_loss": report["overall"]["rules"]["log_loss"],
        "model_log_loss": model["log_loss"],
    }


RESULTS_LIMIT = 100


def _record(hits: list[bool]) -> dict[str, Any]:
    correct = sum(hits)
    return {"graded": len(hits), "correct": correct, "wrong": len(hits) - correct,
            "accuracy": round(correct / len(hits), 4) if hits else None,
            "accuracy_95ci": wilson_interval(correct, len(hits))}


def _totals_hit(row: dict) -> bool | None:
    pick, result = str(row.get("totals_pick") or "").lower(), str(row.get("totals_result") or "").lower()
    if pick not in ("over", "under") or result not in ("over", "under"):
        return None  # no total picked, or a push
    return pick == result


def results(sport: str, limit: int = RESULTS_LIMIT) -> dict[str, Any]:
    """Graded pregame picks for one sport, newest first, with winner and totals records.

    Same counting rules as the track record: only picks recorded before kickoff, and NBA
    preseason games are left out of the record.
    """
    records = mt._load_predictions()
    rows = [r for r in records if str(r.get("sport", "")).lower() == sport and mt.verified_pregame(r)]
    if sport == "nba":
        rows = [r for r in rows if r.get("season_type") in (None, NBA_REGULAR_SEASON)]
    graded = [r for r in rows if r.get("status") == "completed" and r.get("is_correct") is not None]
    graded.sort(key=lambda r: r.get("kickoff_at") or "", reverse=True)
    newest = mt._aware_datetime(graded[0].get("kickoff_at")) if graded else None
    games = []
    for r in graded:
        side = _pick_side(r)
        probabilities = _probabilities(r) or {}
        final = r.get("final_score") if isinstance(r.get("final_score"), dict) else None
        total_hit = _totals_hit(r)
        games.append({
            "id": r.get("id"), "kickoff": r.get("kickoff_at"), "league": r.get("league_name"),
            "home": r.get("team_a"), "away": r.get("team_b"),
            "pick": r.get("predicted_pick_label") or r.get("predicted_winner"),
            "pick_probability": None if side is None else round(probabilities.get(side, 0.0), 3),
            "final_score": final and {"a": final.get("a"), "b": final.get("b")},
            "correct": bool(r.get("is_correct")),
            "totals": None if total_hit is None else {"pick": r.get("totals_pick"), "line": r.get("totals_line"),
                                                      "scored": r.get("total_scored"), "correct": total_hit},
        })
    last_week = [r for r in graded if newest and (kickoff := mt._aware_datetime(r.get("kickoff_at")))
                 and (newest - kickoff).days < 7]
    return {
        "sport": sport,
        "minimum_sample": MINIMUM_SAMPLE,
        "winner": _record([bool(r.get("is_correct")) for r in graded]),
        "winner_last_7_days": _record([bool(r.get("is_correct")) for r in last_week]),
        "totals": _record([hit for r in graded if (hit := _totals_hit(r)) is not None]),
        "pending": sum(1 for r in rows if r.get("status") != "completed"),
        "games": games[:limit],
    }


def summary() -> dict[str, Any]:
    records = mt._load_predictions()
    return {
        "minimum_sample": MINIMUM_SAMPLE,
        "sports": {sport: _sport_summary(records, sport) for sport in ("soccer", "nba")},
        "market_benchmark": _market_benchmark(),
        "recent": _recent(records),
        "learning": _learning(),
        "notes": [
            "Counts only winner picks first recorded before the scheduled kickoff.",
            "Records from earlier rule versions are included when they meet that test.",
            "Intervals are 95% Wilson intervals; small samples cannot establish accuracy.",
            "Log loss and Brier score judge the full probabilities; lower is better.",
            "Closing line value compares the market's probability of the pick at kickoff with when it was made.",
        ],
    }
