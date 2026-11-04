"""Unified prediction orchestration for soccer and NBA routes.

ScorMastermind blends:
- Rule-based engine output (scorpred_engine)
- Optional ML signal (report-driven or provided model output)
- Lightweight heuristics (form + context)

Primary API:
    predict_match(context) -> dict
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import ml_service
import nba_ml_service
import odds_utils
import prediction_policy as pp
import soccer_selector as selector
import api_client as ac
import predictor as pred
from runtime_paths import elo_state_path, ml_report_path, walk_forward_report_path
import scorpred_engine as se
from services.soccer_feature_policy import enrich_opponent_snapshots, opponent_average_ppg

_ML_REPORT_PATH = ml_report_path()
_WALK_FORWARD_REPORT_PATH = walk_forward_report_path()


# ── Blend weights (shared via prediction_policy) ──────────────────────────────
# Both scormastermind (runtime) and ml_service (offline eval) now read from
# the same source — prediction_policy.soccer_ml_blend_weight() / nba variant.
# prediction_policy.json is read from disk when the file mtime changes, so
# updates written by daily_refresh are picked up on the next prediction.

# ── ELO state cache (loaded once from training output) ───────────────────────────
class _EloCache:
    ratings: dict[str, float] | None = None
    _mtime: float = 0.0


class _SelectorCache:
    profile: dict[str, Any] | None = None
    _mtime: float = 0.0


def _load_soccer_elo() -> dict[str, float]:
    """Return ELO ratings dict saved by train_model.py. Falls back to empty dict.

    Re-reads the file if its modification time has changed (e.g. after retraining)
    so a running server picks up fresh ratings without a restart.
    """
    try:
        path = elo_state_path()
        if path.exists():
            mtime = path.stat().st_mtime
            if _EloCache.ratings is not None and mtime == _EloCache._mtime:
                return _EloCache.ratings
            _EloCache.ratings = json.loads(path.read_text(encoding="utf-8"))
            _EloCache._mtime = mtime
            return _EloCache.ratings
    except Exception:
        pass
    if _EloCache.ratings is None:
        _EloCache.ratings = {}
    return _EloCache.ratings


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.72:
        return "High"
    if confidence >= 0.51:
        return "Medium"
    return "Low"


def _is_weak_data_quality(data_quality: str) -> bool:
    return str(data_quality or "").strip().lower() in {"limited", "weak", "poor"}


def _load_ml_report() -> dict[str, Any] | None:
    if not _ML_REPORT_PATH.exists():
        return None
    try:
        return json.loads(_ML_REPORT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_selector_profile() -> dict[str, Any] | None:
    try:
        path = _WALK_FORWARD_REPORT_PATH
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        if _SelectorCache.profile is not None and mtime == _SelectorCache._mtime:
            return _SelectorCache.profile
        report = json.loads(path.read_text(encoding="utf-8"))
        windows = report.get("windows") or {}
        profile = (
            report.get("selector")
            or (windows.get("last_3_years") or {}).get("selector")
            or (windows.get("all_history") or {}).get("selector")
            or {}
        )
        if isinstance(profile, dict) and profile:
            _SelectorCache.profile = profile
            _SelectorCache._mtime = mtime
            return profile
    except Exception:
        pass
    return None


def _results_to_points(rows: list[dict], sport: str) -> float:
    if not rows:
        return 0.5
    pts = 0.0
    for row in rows[:5]:
        result = str(row.get("result", "")).upper()
        if result == "W":
            pts += 1.0
        elif result == "D" and sport == "soccer":
            pts += 0.45
    return pts / max(1.0, min(5.0, float(len(rows[:5]))))


def _average_metric(rows: list[dict[str, Any]], key: str, default: float = 0.0) -> float:
    if not rows:
        return default
    values = [_safe_float(row.get(key), default) for row in rows]
    return sum(values) / len(values)


_ELO_DEFAULT = 1500.0


def _build_elo_features(context: dict[str, Any]) -> dict[str, float]:
    """Look up pre-match ELO for both teams from the saved training state.

    Keys checked in context: ``team_a_name`` / ``team_b_name`` (exact match
    against the team names used in historical_matches.csv).  Falls back to
    the neutral 1500.0 baseline for unknown teams or a missing state file.
    """
    elo = _load_soccer_elo()
    team_a = str(context.get("team_a_name") or context.get("home_team") or "").strip()
    team_b = str(context.get("team_b_name") or context.get("away_team") or "").strip()
    home_elo = float(elo.get(team_a, _ELO_DEFAULT))
    away_elo = float(elo.get(team_b, _ELO_DEFAULT))
    return {
        "home_elo":  round(home_elo, 2),
        "away_elo":  round(away_elo, 2),
        "elo_diff":  round(home_elo - away_elo, 2),
    }


def _nba_ml_features(context: dict[str, Any]) -> dict[str, float]:
    """Build NBA ML feature dict matching NBA_FEATURE_COLUMNS in nba_train_model.py."""
    form_a    = context.get("form_a") or []
    form_b    = context.get("form_b") or []
    h2h_form_a = context.get("h2h_form_a") or []

    def _net_rtg(rows: list[dict], window: int = 10) -> float:
        recent = rows[:window]
        if not recent:
            return 0.0
        vals = [
            _safe_float(r.get("our_pts"), 110.0) - _safe_float(r.get("their_pts"), 110.0)
            for r in recent
        ]
        return round(sum(vals) / len(vals), 2)

    def _win_pct(rows: list[dict], window: int = 10) -> float:
        recent = rows[:window]
        if not recent:
            return 0.5
        return round(sum(1.0 for r in recent if str(r.get("result", "")).upper() == "W") / len(recent), 4)

    def _avg_pts(rows: list[dict], window: int = 10) -> float:
        recent = rows[:window]
        if not recent:
            return 110.0
        return round(sum(_safe_float(r.get("our_pts"), 110.0) for r in recent) / len(recent), 2)

    def _avg_pts_against(rows: list[dict], window: int = 10) -> float:
        recent = rows[:window]
        if not recent:
            return 110.0
        return round(sum(_safe_float(r.get("their_pts"), 110.0) for r in recent) / len(recent), 2)

    def _scoring_consistency(rows: list[dict], window: int = 10) -> float:
        recent = rows[:window]
        if len(recent) < 2:
            return 0.0
        pts = [_safe_float(r.get("our_pts"), 110.0) for r in recent]
        mean = sum(pts) / len(pts)
        return round((sum((p - mean) ** 2 for p in pts) / len(pts)) ** 0.5, 2)

    def _rest_days(rows: list[dict]) -> float:
        if not rows:
            return 3.0
        try:
            from datetime import date as _date
            ds = rows[0].get("date") or ""
            if ds:
                last  = _date.fromisoformat(str(ds).strip()[:10])
                return min(float(max(0, (_date.today() - last).days)), 7.0)
        except Exception:
            pass
        return 3.0

    rest_home = _rest_days(form_a)
    rest_away = _rest_days(form_b)

    h2h_win_rate = 0.5
    if h2h_form_a:
        wins = sum(1 for r in h2h_form_a if str(r.get("result", "")).upper() == "W")
        h2h_win_rate = round(wins / len(h2h_form_a), 4)

    # ELO: use context-level ELO if available, else neutral
    h_elo = _safe_float(context.get("home_elo"), 1500.0)
    a_elo = _safe_float(context.get("away_elo"), 1500.0)

    h_net10 = _net_rtg(form_a, 10)
    a_net10 = _net_rtg(form_b, 10)
    h_wpct10 = _win_pct(form_a, 10)
    a_wpct10 = _win_pct(form_b, 10)
    h_off10 = _avg_pts(form_a, 10)
    a_off10 = _avg_pts(form_b, 10)

    return {
        # Performance – last 10
        "home_net_rating_last10": h_net10,
        "away_net_rating_last10": a_net10,
        "home_off_rtg_last10":    h_off10,
        "away_off_rtg_last10":    a_off10,
        "home_def_rtg_last10":    _avg_pts_against(form_a, 10),
        "away_def_rtg_last10":    _avg_pts_against(form_b, 10),
        "home_win_pct_last10":    h_wpct10,
        "away_win_pct_last10":    a_wpct10,
        # Recent form – last 5
        "home_win_pct_last5":     _win_pct(form_a, 5),
        "away_win_pct_last5":     _win_pct(form_b, 5),
        "home_net_rating_last5":  _net_rtg(form_a, 5),
        "away_net_rating_last5":  _net_rtg(form_b, 5),
        # Rest / fatigue
        "rest_days_home":         rest_home,
        "rest_days_away":         rest_away,
        "is_back_to_back_home":   1.0 if rest_home <= 1 else 0.0,
        "is_back_to_back_away":   1.0 if rest_away <= 1 else 0.0,
        # H2H
        "h2h_home_win_rate":      h2h_win_rate,
        # ELO
        "home_elo":               round(h_elo, 1),
        "away_elo":               round(a_elo, 1),
        "elo_diff":               round(h_elo - a_elo, 1),
        # Scoring consistency
        "home_scoring_consistency": _scoring_consistency(form_a, 10),
        "away_scoring_consistency": _scoring_consistency(form_b, 10),
        # Derived comparisons
        "pace_diff":              round(h_off10 - a_off10, 2),
        "net_rating_diff":        round(h_net10 - a_net10, 2),
        "win_pct_diff_10":        round(h_wpct10 - a_wpct10, 4),
    }


def _ml_features(context: dict[str, Any]) -> dict[str, float | None]:
    """Build pre-match feature dict matching FEATURE_COLUMNS in train_model.py.

    Computes features from the live form context where data is available.
    Missing historical opponent snapshots remain missing for the artifact's
    training-time imputation; current-opponent or own-form proxies are invalid.
    """
    form_a    = context.get("ml_form_a") or context.get("form_a") or []
    form_b    = context.get("ml_form_b") or context.get("form_b") or []
    h2h_form_a = context.get("h2h_form_a") or [] # H2H results from home team's perspective
    sport     = str(context.get("sport") or "soccer").lower()
    target_date = context.get("target_date") or context.get("kickoff") or context.get("match_date")

    def _avg(rows: list[dict], key: str, window: int = 5, default: float = 0.0) -> float:
        vals = [_safe_float(row.get(key), default) for row in rows[:window]
                if row.get(key) is not None]
        return sum(vals) / len(vals) if vals else default

    def _ppg(rows: list[dict], window: int = 5) -> float:
        """Points per game – Win=3, Draw=1 (soccer only), Loss=0."""
        recent = rows[:window]
        if not recent:
            return 0.0
        pts = 0.0
        for row in recent:
            r = str(row.get("result", "")).upper()
            if r == "W":
                pts += 3.0
            elif r == "D" and sport == "soccer":
                pts += 1.0
        return pts / float(len(recent))

    def _clean_sheet_rate(rows: list[dict], window: int = 5) -> float:
        recent = rows[:window]
        if not recent:
            return 0.0
        return sum(1.0 for r in recent if _safe_float(r.get("ga"), 1.0) == 0.0) / len(recent)

    def _scored_rate(rows: list[dict], window: int = 5) -> float:
        recent = rows[:window]
        if not recent:
            return 0.0
        return sum(1.0 for r in recent if _safe_float(r.get("gf"), 0.0) >= 1.0) / len(recent)

    def _venue_rows(rows: list[dict], is_home: bool, window: int = 5) -> list[dict]:
        """Filter form entries by venue using 'is_home' or 'venue' key if available.
        Falls back to all entries when no venue metadata is present."""
        filtered = []
        for r in rows:
            r_home  = r.get("is_home")
            r_venue = str(r.get("venue", "")).upper()
            if r_home is not None:
                if bool(r_home) == is_home:
                    filtered.append(r)
            elif r_venue in ("H", "A"):
                if (r_venue == "H") == is_home:
                    filtered.append(r)
        return filtered[:window] if filtered else rows[:window]

    def _days_since_last(rows: list[dict], target: Any) -> float:
        """Measure rest to the target fixture date, matching the training feature."""
        if not rows:
            return 7.0
        try:
            from datetime import date as _date
            date_str = rows[0].get("date") or rows[0].get("match_date")
            if date_str:
                last  = _date.fromisoformat(str(date_str).strip())
                target_day = _date.fromisoformat(str(target).strip()[:10]) if target else _date.today()
                return min(float(max(0, (target_day - last).days)), 60.0)
        except (TypeError, ValueError):
            return 7.0
        return 7.0

    # ── Overall last-5 ────────────────────────────────────────────────────────
    h_gf5  = round(_avg(form_a, "gf"),    4)
    h_ga5  = round(_avg(form_a, "ga"),    4)
    a_gf5  = round(_avg(form_b, "gf"),    4)
    a_ga5  = round(_avg(form_b, "ga"),    4)
    h_ppg5 = round(_ppg(form_a, 5),       4)
    a_ppg5 = round(_ppg(form_b, 5),       4)

    # ── Overall last-10 ───────────────────────────────────────────────────────
    h_gf10  = round(_avg(form_a, "gf", 10), 4)
    h_ga10  = round(_avg(form_a, "ga", 10), 4)
    a_gf10  = round(_avg(form_b, "gf", 10), 4)
    a_ga10  = round(_avg(form_b, "ga", 10), 4)
    h_ppg10 = round(_ppg(form_a, 10),        4)
    a_ppg10 = round(_ppg(form_b, 10),        4)

    # ── Venue-specific rows ───────────────────────────────────────────────────
    h_home_rows = _venue_rows(form_a, is_home=True)
    a_away_rows = _venue_rows(form_b, is_home=False)

    h_home_ppg5 = round(_ppg(h_home_rows, 5), 4)
    a_away_ppg5 = round(_ppg(a_away_rows, 5), 4)
    h_scored_rate5 = round(_scored_rate(form_a, 5), 4)
    a_scored_rate5 = round(_scored_rate(form_b, 5), 4)
    h_clean_sheet_rate5 = round(_clean_sheet_rate(form_a, 5), 4)
    a_clean_sheet_rate5 = round(_clean_sheet_rate(form_b, 5), 4)
    days_home = _days_since_last(form_a, target_date)
    days_away = _days_since_last(form_b, target_date)

    attack_vs_defense_home = round(h_gf5 - a_ga5, 4)
    attack_vs_defense_away = round(a_gf5 - h_ga5, 4)

    # ── H2H features (from h2h_form_a — home team's perspective) ─────────────
    h2h_pts_avg: float = 1.0
    h2h_gd_avg: float  = 0.0
    if h2h_form_a:
        pts_list: list[float] = []
        gd_list: list[float]  = []
        for r in h2h_form_a[:5]:
            res = str(r.get("result", "")).upper()
            gf  = _safe_float(r.get("gf"), 0.0)
            ga  = _safe_float(r.get("ga"), 0.0)
            pts_list.append(3.0 if res == "W" else (1.0 if res == "D" else 0.0))
            gd_list.append(gf - ga)
        if pts_list:
            h2h_pts_avg = round(sum(pts_list) / len(pts_list), 4)
            h2h_gd_avg  = round(sum(gd_list)  / len(gd_list),  4)

    # ── Opponent-strength proxy ────────────────────────────────────────────────
    # Each side's feature uses that side's five historical meetings. Optional
    # full-history enrichment computes opponents' PPG before those meetings.
    history = context.get("historical_matches") or []
    opponent_form_a = enrich_opponent_snapshots(form_a[:5], history, team=context.get("team_a_id") or context.get("team_a_name"))
    opponent_form_b = enrich_opponent_snapshots(form_b[:5], history, team=context.get("team_b_id") or context.get("team_b_name"))
    home_opp_avg_ppg_5 = opponent_average_ppg(opponent_form_a)
    away_opp_avg_ppg_5 = opponent_average_ppg(opponent_form_b)

    return {
        # Overall last-5
        "home_avg_gf_5":         h_gf5,
        "home_avg_ga_5":         h_ga5,
        "home_avg_gd_5":         round(h_gf5 - h_ga5, 4),
        "home_ppg_5":            h_ppg5,
        "away_avg_gf_5":         a_gf5,
        "away_avg_ga_5":         a_ga5,
        "away_avg_gd_5":         round(a_gf5 - a_ga5, 4),
        "away_ppg_5":            a_ppg5,
        # Overall last-10
        "home_avg_gf_10":        h_gf10,
        "home_avg_ga_10":        h_ga10,
        "home_ppg_10":           h_ppg10,
        "away_avg_gf_10":        a_gf10,
        "away_avg_ga_10":        a_ga10,
        "away_ppg_10":           a_ppg10,
        # Trend delta
        "home_ppg_delta_5v10":   round(h_ppg5 - h_ppg10, 4),
        "home_gf_delta_5v10":    round(h_gf5  - h_gf10,  4),
        "away_ppg_delta_5v10":   round(a_ppg5 - a_ppg10, 4),
        "away_gf_delta_5v10":    round(a_gf5  - a_gf10,  4),
        # Venue-specific form
        "home_home_avg_gf_5":    round(_avg(h_home_rows, "gf"),    4),
        "home_home_avg_ga_5":    round(_avg(h_home_rows, "ga"),    4),
        "home_home_ppg_5":       h_home_ppg5,
        "away_away_avg_gf_5":    round(_avg(a_away_rows, "gf"),    4),
        "away_away_avg_ga_5":    round(_avg(a_away_rows, "ga"),    4),
        "away_away_ppg_5":       a_away_ppg5,
        # Scoring consistency
        "home_clean_sheet_rate_5": h_clean_sheet_rate5,
        "away_clean_sheet_rate_5": a_clean_sheet_rate5,
        "home_scored_rate_5":      h_scored_rate5,
        "away_scored_rate_5":      a_scored_rate5,
        # Past opponents' pre-match PPG, with identical training/live semantics.
        "home_opp_avg_ppg_5":    home_opp_avg_ppg_5,
        "away_opp_avg_ppg_5":    away_opp_avg_ppg_5,
        # Rest / fatigue
        "days_since_last_match_home": days_home,
        "days_since_last_match_away": days_away,
        # H2H (computed from h2h_form_a when available)
        "h2h_home_points_avg":   h2h_pts_avg,
        "h2h_goal_diff_avg":     h2h_gd_avg,
        # Derived comparison features
        "ppg_diff_5":            round(h_ppg5 - a_ppg5, 4),
        "gf_diff_5":             round(h_gf5 - a_gf5, 4),
        "ga_diff_5":             round(a_ga5 - h_ga5, 4),
        "venue_ppg_diff_5":      round(h_home_ppg5 - a_away_ppg5, 4),
        "attack_vs_defense_home": attack_vs_defense_home,
        "attack_vs_defense_away": attack_vs_defense_away,
        "attack_balance_diff":   round(attack_vs_defense_home - attack_vs_defense_away, 4),
        "scored_rate_diff_5":    round(h_scored_rate5 - a_scored_rate5, 4),
        "clean_sheet_diff_5":    round(h_clean_sheet_rate5 - a_clean_sheet_rate5, 4),
        "rest_diff_days":        round(days_home - days_away, 4),
        # ELO ratings (looked up from training state; default to 1500 if unknown)
        **_build_elo_features(context),
    }


def _normalize_probabilities(prob_a: float, prob_draw: float, prob_b: float) -> tuple[float, float, float]:
    total = max(prob_a + prob_draw + prob_b, 1e-9)
    return prob_a / total, prob_draw / total, prob_b / total


def _feature_edge_summary(features: dict[str, float]) -> dict[str, float]:
    ppg_diff = _safe_float(features.get("ppg_diff_5"), 0.0)
    gf_diff = _safe_float(features.get("gf_diff_5"), 0.0)
    ga_diff = _safe_float(features.get("ga_diff_5"), 0.0)
    venue_diff = _safe_float(features.get("venue_ppg_diff_5"), 0.0)
    attack_balance = _safe_float(features.get("attack_balance_diff"), 0.0)
    scored_rate_diff = _safe_float(features.get("scored_rate_diff_5"), 0.0)
    clean_sheet_diff = _safe_float(features.get("clean_sheet_diff_5"), 0.0)
    rest_diff_days = _safe_float(features.get("rest_diff_days"), 0.0)
    elo_diff = _safe_float(features.get("elo_diff"), 0.0)

    side_edge = (
        0.34 * ppg_diff
        + 0.15 * gf_diff
        + 0.15 * ga_diff
        + 0.18 * attack_balance
        + 0.10 * venue_diff
        + 0.08 * scored_rate_diff
        + 0.06 * clean_sheet_diff
        + 0.015 * rest_diff_days
        + 0.0022 * elo_diff
    )

    return {
        "ppg_diff": round(ppg_diff, 4),
        "gf_diff": round(gf_diff, 4),
        "ga_diff": round(ga_diff, 4),
        "venue_diff": round(venue_diff, 4),
        "attack_balance": round(attack_balance, 4),
        "elo_diff": round(elo_diff, 2),
        "side_edge": round(side_edge, 4),
    }


def _top_outcomes(prob_a: float, prob_b: float, prob_draw: float = 0.0) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = [
        {"team": "A", "prob": prob_a},
        {"team": "B", "prob": prob_b},
    ]
    if prob_draw > 0.0:
        outcomes.append({"team": "draw", "prob": prob_draw})
    outcomes.sort(key=lambda item: _safe_float(item.get("prob"), 0.0), reverse=True)
    return outcomes


def _blend_soccer_probabilities(
    rule_prob_a_total: float,
    rule_prob_draw: float,
    rule_prob_b_total: float,
    ml: dict[str, Any],
    edge_summary: dict[str, float],
    data_quality: str,
) -> tuple[float, float, float]:
    weak_quality = _is_weak_data_quality(data_quality)
    base_ml_weight = pp.soccer_ml_blend_weight()
    ml_weight = base_ml_weight if ml.get("available") and not weak_quality else max(0.0, base_ml_weight - 0.06) if ml.get("available") else 0.0
    rule_weight = 1.0 - ml_weight if ml.get("available") else 1.0

    prob_a = rule_prob_a_total
    prob_draw = rule_prob_draw
    prob_b = rule_prob_b_total

    if ml.get("available") and ml.get("prob_draw") is not None:
        ml_prob_a = _clamp(_safe_float(ml.get("prob_a"), 0.3333), 0.01, 0.99)
        ml_prob_draw = _clamp(_safe_float(ml.get("prob_draw"), 0.3333), 0.0, 0.98)
        ml_prob_b = _clamp(_safe_float(ml.get("prob_b"), 0.3334), 0.01, 0.99)

        prob_a = rule_weight * rule_prob_a_total + ml_weight * ml_prob_a
        prob_draw = rule_weight * rule_prob_draw + ml_weight * ml_prob_draw
        prob_b = rule_weight * rule_prob_b_total + ml_weight * ml_prob_b
    elif ml.get("available"):
        rule_non_draw = max(0.01, rule_prob_a_total + rule_prob_b_total)
        rule_non_draw_a = _clamp(rule_prob_a_total / rule_non_draw, 0.01, 0.99)
        ml_non_draw_a = _clamp(_safe_float(ml.get("prob_a"), 0.5), 0.01, 0.99)
        combined_non_draw_a = _clamp(rule_weight * rule_non_draw_a + ml_weight * ml_non_draw_a, 0.01, 0.99)
        prob_draw = rule_prob_draw
        prob_a = combined_non_draw_a * (1.0 - prob_draw)
        prob_b = (1.0 - combined_non_draw_a) * (1.0 - prob_draw)

    prob_a, prob_draw, prob_b = _normalize_probabilities(prob_a, prob_draw, prob_b)

    side_gap = abs(prob_a - prob_b)
    side_max = max(prob_a, prob_b)
    edge_strength = abs(_safe_float(edge_summary.get("side_edge"), 0.0))

    # Draw transfer: move some draw probability to sides when the data
    # clearly favours a side outcome.  Suppress transfer when the draw is
    # genuinely competitive so we don't contradict a valid draw selection.
    if prob_draw > 0.0:
        # How dominant is the draw vs the best side?  When draw is close to
        # or above the top side the transfer should be near-zero.
        draw_dominance = _clamp(prob_draw - side_max, -0.30, 0.15)
        # Scale factor: 1.0 when draw is clearly below sides, ~0 when draw
        # is competitive, 0 when draw leads.
        suppression = _clamp(1.0 - (draw_dominance + 0.04) * 8.0, 0.0, 1.0)

        raw_transfer = min(
            prob_draw * 0.22,
            max(0.0, (edge_strength - 0.25) * 0.09 + max(0.0, side_gap - 0.05) * 0.55),
        )
        draw_transfer = raw_transfer * suppression
        if draw_transfer > 0.001:
            side_total = max(prob_a + prob_b, 1e-9)
            prob_draw -= draw_transfer
            prob_a += draw_transfer * (prob_a / side_total)
            prob_b += draw_transfer * (prob_b / side_total)

    return _normalize_probabilities(prob_a, prob_draw, prob_b)


def _soccer_ml_probabilities(ml: dict[str, Any]) -> list[float] | None:
    if not ml.get("available") or ml.get("prob_draw") is None:
        return None
    return [
        _clamp(_safe_float(ml.get("prob_a"), 0.3333), 0.01, 0.99),
        _clamp(_safe_float(ml.get("prob_draw"), 0.3333), 0.0, 0.98),
        _clamp(_safe_float(ml.get("prob_b"), 0.3334), 0.01, 0.99),
    ]


def _select_soccer_probability_source(
    *,
    rule_probs: list[float],
    ml: dict[str, Any],
    edge_summary: dict[str, float],
    data_quality: str,
) -> tuple[tuple[float, float, float], dict[str, Any]]:
    combined = _blend_soccer_probabilities(
        rule_probs[0],
        rule_probs[1],
        rule_probs[2],
        ml,
        edge_summary,
        data_quality,
    )
    ml_probs = _soccer_ml_probabilities(ml)
    profile = _load_selector_profile()
    flags = selector.build_segment_flags(rule_probs, ml_probs, list(combined))
    selection = selector.choose_source(
        profile,
        flags=flags,
        available_sources=("rule", "ml", "combined") if ml_probs else ("rule", "combined"),
    )

    if selection["source"] == "ml" and ml_probs:
        chosen = tuple(ml_probs)
    elif selection["source"] == "rule":
        chosen = tuple(rule_probs)
    else:
        chosen = combined
        selection["source"] = "combined"
        selection["source_label"] = selector.source_label("combined")

    return _normalize_probabilities(*chosen), {
        "source": selection.get("source"),
        "source_label": selection.get("source_label"),
        "segment": selection.get("segment"),
        "segment_label": selection.get("segment_label"),
        "used_override": bool(selection.get("used_override")),
        "reason": selection.get("reason"),
        "profile_available": bool(profile),
    }


def _select_soccer_outcome(prob_a: float, prob_draw: float, prob_b: float, edge_summary: dict[str, float]) -> str:
    side_gap = abs(prob_a - prob_b)
    side_max = max(prob_a, prob_b)
    edge_strength = abs(_safe_float(edge_summary.get("side_edge"), 0.0))

    draw_viable = (
        prob_draw >= 0.36
        and side_gap <= 0.06
        and edge_strength <= 0.36
        and prob_draw >= side_max + 0.012
    )
    draw_balanced = (
        prob_draw >= 0.39
        and side_gap <= 0.04
        and edge_strength <= 0.24
        and prob_draw >= side_max + 0.005
    )

    if draw_viable or draw_balanced:
        return "draw"
    return "A" if prob_a >= prob_b else "B"


def _confidence_score(
    prob_a: float,
    prob_draw: float,
    prob_b: float,
    *,
    selected_team: str,
    ml: dict[str, Any],
    rule_prob_a_for_compare: float,
    rule_prob_draw: float,
    has_forms: bool,
    has_stats: bool,
    data_quality: str,
    edge_summary: dict[str, float],
) -> tuple[float, float, float, bool]:
    outcomes = _top_outcomes(prob_a, prob_b, prob_draw)
    top_prob = _safe_float(outcomes[0].get("prob"), 0.0)
    second_prob = _safe_float(outcomes[1].get("prob"), 0.0) if len(outcomes) > 1 else top_prob
    gap = max(0.0, top_prob - second_prob)

    ml_prob_draw = _safe_float(ml.get("prob_draw"), -1.0)
    ml_top_team = ""
    if ml.get("available"):
        if ml_prob_draw >= 0.0:
            ml_candidates = {
                "A": _safe_float(ml.get("prob_a"), 0.0),
                "draw": ml_prob_draw,
                "B": _safe_float(ml.get("prob_b"), 0.0),
            }
            ml_top_team = max(ml_candidates, key=ml_candidates.get)
        else:
            ml_top_team = "A" if _safe_float(ml.get("prob_a"), 0.5) >= 0.5 else "B"

    rule_top_team = "draw" if rule_prob_draw >= max(rule_prob_a_for_compare * (1.0 - rule_prob_draw), 1.0 - rule_prob_a_for_compare) and rule_prob_draw > 0.0 else "A" if rule_prob_a_for_compare >= 0.5 else "B"

    agreement_bonus = 0.0
    if ml_top_team and ml_top_team == selected_team:
        agreement_bonus += 0.05
    if rule_top_team == selected_team:
        agreement_bonus += 0.03
    strong_disagreement = bool(ml_top_team) and ml_top_team != rule_top_team and ml_top_team != selected_team and rule_top_team != selected_team
    if strong_disagreement:
        agreement_bonus -= 0.04

    # Base model agreement: boost confidence when all base learners in the
    # stacking ensemble agree, penalise slightly when they disagree.
    base_agreement = ml.get("base_agreement") or {}
    agreement_ratio = _safe_float(base_agreement.get("agreement_ratio"), 0.0)
    if agreement_ratio > 0.0:
        if base_agreement.get("unanimous"):
            agreement_bonus += 0.03
        elif agreement_ratio < 0.5:
            agreement_bonus -= 0.02

    edge_strength = abs(_safe_float(edge_summary.get("side_edge"), 0.0))
    edge_bonus = min(0.08, edge_strength * 0.08) if selected_team != "draw" else min(0.06, max(0.0, 0.35 - edge_strength) * 0.14)
    cluster_penalty = max(0.0, 0.08 - gap) * 0.90

    data_penalty = 0.0
    if not has_forms:
        data_penalty += 0.06
    if not has_stats:
        data_penalty += 0.05
    if _is_weak_data_quality(data_quality):
        data_penalty += 0.05
    if selected_team == "draw" and gap < 0.03:
        data_penalty += 0.02

    confidence = _clamp(top_prob * 0.82 + gap * 1.30 + agreement_bonus + edge_bonus - cluster_penalty - data_penalty, 0.05, 0.95)
    return confidence, top_prob * 100.0, gap * 100.0, strong_disagreement


def _prediction_text(team_key: str, team_a_name: str, team_b_name: str) -> str:
    if team_key == "A":
        return f"{team_a_name} Win"
    if team_key == "B":
        return f"{team_b_name} Win"
    return "Draw"


def _ml_signal(context: dict[str, Any]) -> dict[str, Any]:
    ml_outputs = context.get("ml_outputs") or {}
    report = context.get("ml_report") or _load_ml_report() or {}

    if {"prob_a", "prob_draw", "prob_b"}.issubset(ml_outputs.keys()):
        prob_a = _clamp(_safe_float(ml_outputs.get("prob_a"), 0.3333), 0.01, 0.99)
        prob_draw = _clamp(_safe_float(ml_outputs.get("prob_draw"), 0.3333), 0.0, 0.98)
        prob_b = _clamp(_safe_float(ml_outputs.get("prob_b"), 0.3334), 0.01, 0.99)
        total = max(prob_a + prob_draw + prob_b, 1e-9)
        probabilities = [prob_a / total, prob_draw / total, prob_b / total]
        return {
            "available": True,
            "prob_a": probabilities[0],
            "prob_draw": probabilities[1],
            "prob_b": probabilities[2],
            "confidence": max(probabilities),
            "source": "provided_ml_output",
            "top_features": ml_outputs.get("top_features") or [],
        }

    for key in ("prob_a", "home_win_prob", "winner_prob"):
        if key in ml_outputs:
            prob = _clamp(_safe_float(ml_outputs.get(key), 0.5), 0.01, 0.99)
            return {
                "available": True,
                "prob_a": prob,
                "prob_draw": None,
                "prob_b": 1.0 - prob,
                "confidence": max(prob, 1.0 - prob),
                "source": "provided_ml_output",
                "top_features": ml_outputs.get("top_features") or [],
            }

    _features_for_inference = _ml_features(context)

    # Inject live odds as features when available
    try:
        import odds_fetcher as _of
        _home_team_name = str(context.get("team_a_name") or context.get("team_a") or "")
        _away_team_name = str(context.get("team_b_name") or context.get("team_b") or "")
        _live_odds = _of.fetch_match_odds("soccer", _home_team_name, _away_team_name,
                                         league_id=int(context.get("league_id") or 0) or None)
        if _live_odds.get("available"):
            _h = float(_live_odds.get("home_odds") or 0)
            _a = float(_live_odds.get("away_odds") or 0)
            _d = float(_live_odds.get("draw_odds") or 0)
            if _h > 1.0 and _a > 1.0 and _d > 1.0:
                _m = 1.0/_h + 1.0/_d + 1.0/_a
                _features_for_inference["home_implied_prob"] = round((1.0/_h)/_m, 4)
                _features_for_inference["away_implied_prob"] = round((1.0/_a)/_m, 4)
                _features_for_inference["draw_implied_prob"] = round((1.0/_d)/_m, 4)
                _features_for_inference["odds_home_away_ratio"] = round(
                    _features_for_inference["home_implied_prob"] / max(_features_for_inference["away_implied_prob"], 0.01),
                    4,
                )
                _features_for_inference["market_confidence"] = max(
                    _features_for_inference["home_implied_prob"],
                    _features_for_inference["away_implied_prob"],
                    _features_for_inference["draw_implied_prob"],
                )
    except Exception:
        pass  # odds unavailable — ml_service will use training median fallback

    _league_id = int(context.get("league_id") or 0) or None
    inference = ml_service.predict_match(_features_for_inference, league_id=_league_id)
    missing_opponent_features = [name for name in ("home_opp_avg_ppg_5", "away_opp_avg_ppg_5")
                                 if _features_for_inference.get(name) is None]
    if inference.get("available"):
        probs = inference.get("probabilities") or [0.3333, 0.3333, 0.3334]
        model_type = inference.get("model_type", "random_forest")
        source = f"{model_type}_model"
        return {
            "available": True,
            "prob_a": _clamp(_safe_float(probs[0], 0.3333), 0.01, 0.99),
            "prob_draw": _clamp(_safe_float(probs[1], 0.3333), 0.0, 0.98),
            "prob_b": _clamp(_safe_float(probs[2], 0.3334), 0.01, 0.99),
            "confidence": _clamp(_safe_float(inference.get("confidence"), 0.0), 0.0, 1.0),
            "prediction": inference.get("prediction"),
            "source": source,
            "model_role": inference.get("model_role"),
            "base_models": inference.get("base_models"),
            "base_agreement": inference.get("base_agreement"),
            "missing_opponent_features": missing_opponent_features,
            "top_features": ml_outputs.get("top_features") or [],
        }

    return {
        "available": False,
        "prob_a": None,
        "prob_draw": None,
        "prob_b": None,
        "source": "missing_ml_data",
        "error": inference.get("error", "Model inference unavailable"),
        "top_features": [],
    }


def _build_rule_prediction(context: dict[str, Any]) -> dict[str, Any]:
    return se.scorpred_predict(
        form_a=context.get("form_a") or [],
        form_b=context.get("form_b") or [],
        h2h_form_a=context.get("h2h_form_a") or [],
        h2h_form_b=context.get("h2h_form_b") or [],
        injuries_a=context.get("injuries_a") or [],
        injuries_b=context.get("injuries_b") or [],
        team_a_is_home=bool(context.get("team_a_is_home", True)),
        team_a_name=context.get("team_a_name") or "Team A",
        team_b_name=context.get("team_b_name") or "Team B",
        sport=str(context.get("sport") or "soccer"),
        opp_strengths=context.get("opp_strengths") or {},
    )


def _ui_prediction(
    context: dict[str, Any],
    rule_prediction: dict[str, Any],
    prob_a: float,
    prob_b: float,
    prob_draw: float,
    confidence_float: float,
    selected_team: str | None = None,
) -> dict[str, Any]:
    sport = str(context.get("sport") or "soccer").lower()
    team_a_name = context.get("team_a_name") or "Team A"
    team_b_name = context.get("team_b_name") or "Team B"
    confidence_label = _confidence_label(confidence_float)

    ui = dict(rule_prediction or {})

    if sport == "soccer":
        win_probs = {
            "a": round(prob_a * 100.0, 1),
            "draw": round(prob_draw * 100.0, 1),
            "b": round(prob_b * 100.0, 1),
        }
    else:
        win_probs = {
            "a": round(prob_a * 100.0, 1),
            "b": round(prob_b * 100.0, 1),
        }

    ui["win_probabilities"] = win_probs
    ui["prob_a"] = win_probs.get("a", 0.0)
    ui["prob_b"] = win_probs.get("b", 0.0)
    ui["prob_draw"] = win_probs.get("draw", 0.0)
    ui["home_pct"] = win_probs.get("a", 0.0)
    ui["away_pct"] = win_probs.get("b", 0.0)
    ui["draw_pct"] = win_probs.get("draw", 0.0)
    ui["confidence"] = confidence_label

    winner_team = selected_team or ("draw" if sport == "soccer" and win_probs.get("draw", 0.0) >= max(win_probs.get("a", 0.0), win_probs.get("b", 0.0)) else "A" if win_probs.get("a", 0.0) >= win_probs.get("b", 0.0) else "B")
    winner_text = _prediction_text(winner_team, team_a_name, team_b_name)

    ui["winner_label"] = winner_text
    ui["best_pick"] = {
        "prediction": winner_text,
        "team": winner_team,
        "confidence": confidence_label,
        "reasoning": f"ScorMastermind blended ML, rule model, and heuristic context for this edge.",
    }
    return ui




def analyze_match(match_id: int | str) -> dict[str, Any] | None:
    """Build a canonical soccer analysis payload for one fixture id."""
    try:
        fixture = ac.get_fixture_by_id(int(match_id))
    except Exception:
        return None
    if not isinstance(fixture, dict):
        return None

    teams = fixture.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    home_id = int(home.get("id") or 0)
    away_id = int(away.get("id") or 0)
    home_name = str(home.get("name") or "Home")
    away_name = str(away.get("name") or "Away")
    league_id = int(((fixture.get("league") or {}).get("id")) or 0)
    season = int(((fixture.get("league") or {}).get("season")) or 0)
    if not home_id or not away_id:
        return None

    try:
        h2h_raw = ac.get_h2h(home_id, away_id, last=20)
    except Exception:
        h2h_raw = []
    try:
        fixtures_home = ac.get_team_fixtures(home_id, league_id, season, last=20)
    except Exception:
        fixtures_home = []
    try:
        fixtures_away = ac.get_team_fixtures(away_id, league_id, season, last=20)
    except Exception:
        fixtures_away = []
    try:
        injuries_home = ac.get_injuries(league_id, season, home_id)
    except Exception:
        injuries_home = []
    try:
        injuries_away = ac.get_injuries(league_id, season, away_id)
    except Exception:
        injuries_away = []
    try:
        standings = ac.get_standings(league_id, season)
    except Exception:
        standings = []

    h2h_raw = pred.filter_recent_completed_fixtures(h2h_raw, current_season=season or None, seasons_back=5)
    form_home = pred.extract_form(fixtures_home, home_id)[:5]
    form_away = pred.extract_form(fixtures_away, away_id)[:5]
    h2h_home = pred.extract_form(h2h_raw, home_id)[:5]
    h2h_away = pred.extract_form(h2h_raw, away_id)[:5]
    opp_strengths = se.build_opp_strengths_from_standings(standings) if standings else {}

    result = predict_match(
        {
            "sport": "soccer",
            "team_a_name": home_name,
            "team_b_name": away_name,
            "team_a_is_home": True,
            "league_id": league_id,
            "form_a": form_home,
            "form_b": form_away,
            "h2h_form_a": h2h_home,
            "h2h_form_b": h2h_away,
            "injuries_a": injuries_home,
            "injuries_b": injuries_away,
            "opp_strengths": opp_strengths,
            "team_stats": {"a": {"form": form_home}, "b": {"form": form_away}},
        }
    )
    payload = result.get("ui_prediction") if isinstance(result.get("ui_prediction"), dict) else {}
    probabilities = payload.get("win_probabilities") if isinstance(payload.get("win_probabilities"), dict) else {}
    best_pick = payload.get("best_pick") if isinstance(payload.get("best_pick"), dict) else {}

    if any(probabilities.get(key) is None for key in ("a", "draw", "b")):
        return None

    raw_action = str(payload.get("play_type") or payload.get("recommended_play") or "").strip().upper()
    action = "PASS" if raw_action in {"AVOID", ""} else raw_action
    recommended_side = best_pick.get("prediction")
    if str(recommended_side).strip().lower() == "avoid":
        recommended_side = ((payload.get("top_lean") or {}).get("prediction") or recommended_side)
    confidence = payload.get("confidence_pct")
    reason = best_pick.get("reasoning")
    data_quality = payload.get("data_quality")
    if any(value is None for value in (recommended_side, confidence, reason, data_quality)):
        return None

    metric_breakdown = {
        "home": {"form": round(_results_to_points(form_home, "soccer"), 3)},
        "away": {"form": round(_results_to_points(form_away, "soccer"), 3)},
    }

    return {
        "action": action,
        "recommended_side": recommended_side,
        "confidence": confidence,
        "probabilities": probabilities,
        "reason": reason,
        "data_quality": data_quality,
        "metric_breakdown": metric_breakdown,
    }
def predict_match(context: dict[str, Any]) -> dict[str, Any]:
    """Return a unified prediction for soccer or NBA.

    Required context keys (flexible):
    - form_a, form_b, h2h_form_a, h2h_form_b
    - injuries_a, injuries_b
    - team_a_name, team_b_name
    - sport, team_a_is_home
    Optional:
    - ml_outputs, ml_report, team_stats, rule_prediction
    """
    sport = str(context.get("sport") or "soccer").lower()
    team_a_name = context.get("team_a_name") or "Team A"
    team_b_name = context.get("team_b_name") or "Team B"
    policy = pp.sport_policy(sport)

    # ── Apply learned adjustments from mistake analysis ─────────────────────
    _learned_notes: list[str] = []
    try:
        import mistake_analysis as _ma
        policy, _learned_notes = _ma.apply_adjustments_to_thresholds(policy, sport)
    except Exception:
        pass

    min_confidence_pct = _safe_float(policy.get("min_confidence_pct"), 53.0)
    min_top_two_gap_pct = _safe_float(policy.get("min_top_two_gap_pct"), 3.0)
    lean_min_confidence_pct = _safe_float(policy.get("lean_min_confidence_pct"), 51.0)
    bet_min_confidence_pct = _safe_float(policy.get("bet_min_confidence_pct"), 70.0)
    draw_min_top_prob_pct = _safe_float(policy.get("draw_min_top_prob_pct"), 37.0)

    rule_prediction = context.get("rule_prediction") or _build_rule_prediction(context)
    rule_probs = rule_prediction.get("win_probabilities") or {}

    rule_prob_a_total = _clamp(_safe_float(rule_probs.get("a"), 50.0) / 100.0, 0.01, 0.99)
    rule_prob_b_total = _clamp(_safe_float(rule_probs.get("b"), 50.0) / 100.0, 0.01, 0.99)
    rule_prob_draw = _clamp(_safe_float(rule_probs.get("draw"), 0.0) / 100.0, 0.0, 0.6) if sport == "soccer" else 0.0

    ml = _ml_signal(context)
    features = _ml_features(context) if sport == "soccer" else {}
    edge_summary = _feature_edge_summary(features) if sport == "soccer" else {"side_edge": 0.0}
    data_quality = str(rule_prediction.get("data_quality") or "Moderate")
    weak_data_quality = _is_weak_data_quality(data_quality)

    if sport == "soccer":
        rule_non_draw = max(0.01, rule_prob_a_total + rule_prob_b_total)
        rule_prob_a_for_compare = _clamp(rule_prob_a_total / rule_non_draw, 0.01, 0.99)
        selector_choice = {}
        (prob_a, prob_draw, prob_b), selector_choice = _select_soccer_probability_source(
            rule_probs=[rule_prob_a_total, rule_prob_draw, rule_prob_b_total],
            ml=ml,
            edge_summary=edge_summary,
            data_quality=data_quality,
        )
        selected_team = _select_soccer_outcome(prob_a, prob_draw, prob_b, edge_summary)
    else:
        rule_prob_a = _clamp(rule_prob_a_total, 0.01, 0.99)
        rule_prob_a_for_compare = rule_prob_a
        # Try NBA ML signal
        _nba_ml_result = nba_ml_service.predict_match(_nba_ml_features(context))
        if _nba_ml_result.get("available"):
            nba_ml_w = pp.nba_ml_blend_weight()
            ml_prob_a = _clamp(_safe_float(_nba_ml_result.get("prob_a"), 0.5), 0.01, 0.99)
            prob_a = _clamp((1.0 - nba_ml_w) * rule_prob_a + nba_ml_w * ml_prob_a, 0.01, 0.99)
        elif ml.get("available"):
            _nbw = pp.nba_ml_blend_weight()
            ml_prob_a = _clamp(_safe_float(ml.get("prob_a"), 0.5), 0.01, 0.99)
            prob_a = _clamp((1.0 - _nbw) * rule_prob_a + _nbw * ml_prob_a, 0.01, 0.99)
        else:
            prob_a = rule_prob_a
        prob_b = 1.0 - prob_a
        prob_draw = 0.0
        selected_team = "A" if prob_a >= prob_b else "B"
        selector_choice = {
            "source": "combined" if ml.get("available") else "rule",
            "source_label": "Combined" if ml.get("available") else "Rule",
            "segment": None,
            "segment_label": None,
            "used_override": False,
            "reason": "NBA still uses the existing production flow.",
            "profile_available": False,
        }

    has_forms = bool(context.get("form_a") and context.get("form_b"))
    stats = context.get("team_stats") or {}
    has_stats = bool((stats.get("a") or stats.get("team_a")) and (stats.get("b") or stats.get("team_b")))

    confidence, top_prob_pct, top_two_gap_pct, strong_disagreement_from_confidence = _confidence_score(
        prob_a,
        prob_draw,
        prob_b,
        selected_team=selected_team,
        ml=ml,
        rule_prob_a_for_compare=rule_prob_a_for_compare,
        rule_prob_draw=rule_prob_draw,
        has_forms=has_forms,
        has_stats=has_stats,
        data_quality=data_quality,
        edge_summary=edge_summary,
    )
    ml_confidence_pct = _safe_float(ml.get("confidence"), 0.0) * 100.0 if ml.get("available") else 0.0
    confidence_pct = confidence * 100.0
    winner = _prediction_text(selected_team, team_a_name, team_b_name)
    winner_prob = prob_draw if selected_team == "draw" else prob_a if selected_team == "A" else prob_b

    explanation = {
        "ml_signal": {
            "available": bool(ml.get("available")),
            "source": ml.get("source", "missing_ml_data"),
            "prob_a": round(_safe_float(ml.get("prob_a"), 0.5), 4),
            "prob_draw": round(_safe_float(ml.get("prob_draw"), 0.0), 4) if ml.get("prob_draw") is not None else None,
            "missing_opponent_features": ml.get("missing_opponent_features") or [],
        },
        "rule_signal": {
            "winner": (rule_prediction.get("best_pick") or {}).get("prediction", ""),
            "probabilities": rule_probs,
            "confidence": (rule_prediction.get("best_pick") or {}).get("confidence", ""),
        },
        "selector": selector_choice,
        "edge_summary": edge_summary,
        "top_features": ml.get("top_features") or [],
    }

    ui_prediction = _ui_prediction(
        context,
        rule_prediction,
        prob_a=prob_a,
        prob_b=prob_b,
        prob_draw=prob_draw,
        confidence_float=confidence,
        selected_team=selected_team,
    )

    outcomes: list[dict[str, Any]] = [
        {"team": "A", "prediction": f"{team_a_name} Win", "prob": prob_a},
        {"team": "B", "prediction": f"{team_b_name} Win", "prob": prob_b},
    ]
    if sport == "soccer":
        outcomes.append({"team": "draw", "prediction": "Draw", "prob": prob_draw})
    outcomes.sort(key=lambda item: _safe_float(item.get("prob"), 0.0), reverse=True)

    top_outcome = next((item for item in outcomes if item.get("team") == selected_team), outcomes[0])
    second_outcome = outcomes[1] if len(outcomes) > 1 else outcomes[0]
    if second_outcome.get("team") == selected_team and len(outcomes) > 2:
        second_outcome = outcomes[2]

    ml_prob_a = _safe_float(ml.get("prob_a"), 0.5)
    ml_prob_draw = _safe_float(ml.get("prob_draw"), -1.0)
    if ml_prob_draw >= max(_safe_float(ml.get("prob_a"), 0.0), _safe_float(ml.get("prob_b"), 0.0), 0.0):
        ml_side = "draw"
    else:
        ml_side = "A" if ml_prob_a >= 0.56 else "B" if ml_prob_a <= 0.44 else ""
    rule_side = _select_soccer_outcome(rule_prob_a_total, rule_prob_draw, rule_prob_b_total, edge_summary) if sport == "soccer" else ("A" if rule_prob_a_for_compare >= 0.56 else "B" if rule_prob_a_for_compare <= 0.44 else "")
    strong_signal_disagreement = bool(ml.get("available")) and bool(ml_side) and bool(rule_side) and ml_side != rule_side and strong_disagreement_from_confidence

    avoid_reasons: list[str] = []
    if confidence_pct < min_confidence_pct:
        avoid_reasons.append("No strong edge found")
    if top_two_gap_pct < min_top_two_gap_pct:
        avoid_reasons.append("Outcome probabilities are too close")
    if weak_data_quality:
        avoid_reasons.append("High uncertainty matchup")
    if strong_signal_disagreement:
        avoid_reasons.append("ML and rule signals disagree strongly")
    if sport == "soccer" and selected_team == "draw" and top_prob_pct < draw_min_top_prob_pct:
        avoid_reasons.append("Draw signal is not strong enough")

    avoid_triggered = bool(avoid_reasons)

    if confidence_pct < lean_min_confidence_pct:
        play_type = "AVOID"
    elif confidence_pct < bet_min_confidence_pct:
        play_type = "LEAN"
    else:
        play_type = "BET"
    top_lean = {
        "team": top_outcome.get("team"),
        "prediction": top_outcome.get("prediction"),
        "probability": round(winner_prob * 100.0, 1),
        "display": f"Top lean: {top_outcome.get('prediction')} ({winner_prob * 100.0:.1f}%)",
    }

    best_pick = ui_prediction.get("best_pick") or {}
    best_pick["model_source"] = selector_choice.get("source")
    best_pick["model_source_label"] = selector_choice.get("source_label")
    if avoid_triggered:
        reason = avoid_reasons[0]
        best_pick["prediction"] = "Avoid"
        best_pick["team"] = "avoid"
        best_pick["tracking_team"] = selected_team
        best_pick["confidence"] = "Low"
        best_pick["reasoning"] = reason
        ui_prediction["confidence"] = "Low"
        ui_prediction["recommended_play"] = "Avoid"
        ui_prediction["avoid_reasons"] = avoid_reasons
        ui_prediction["top_lean"] = top_lean
        ui_prediction["risk_label"] = "Elevated"
        ui_prediction["play_type"] = "AVOID"
    else:
        best_pick["tracking_team"] = best_pick.get("team") or selected_team
        if selector_choice.get("used_override") and selector_choice.get("reason"):
            existing_reason = str(best_pick.get("reasoning") or "").strip()
            selector_reason = selector_choice.get("reason")
            best_pick["reasoning"] = (
                f"{existing_reason} {selector_reason}".strip()
                if existing_reason
                else selector_reason
            )
        ui_prediction["recommended_play"] = best_pick.get("prediction") or top_outcome.get("prediction")
        ui_prediction["avoid_reasons"] = []
        ui_prediction["top_lean"] = top_lean
        ui_prediction["play_type"] = play_type

    ui_prediction["confidence_pct"] = round(confidence_pct, 1)
    ui_prediction["ml_confidence_pct"] = round(ml_confidence_pct, 1)
    ui_prediction["selector_source"] = selector_choice.get("source")
    ui_prediction["selector_source_label"] = selector_choice.get("source_label")
    ui_prediction["selector_segment"] = selector_choice.get("segment")
    ui_prediction["selector_segment_label"] = selector_choice.get("segment_label")
    ui_prediction["selector_reason"] = selector_choice.get("reason")
    ui_prediction["selector_used_override"] = bool(selector_choice.get("used_override"))
    if _learned_notes:
        ui_prediction["learned_adjustment_notes"] = _learned_notes

    ui_prediction["best_pick"] = best_pick

    # ── Edge calculation (when odds provided) ─────────────────────────────────
    # Accepts context["odds"] = {"home": float, "draw": float, "away": float}
    # (decimal odds).  Computes edge vs vig-adjusted implied probs and stores
    # a market-adjusted play_type inside edge_data.
    #
    # Design rule: edge_data.play_type is an ADDITIONAL signal shown in the UI
    # alongside the model's recommendation.  It only overrides ui_prediction
    # play_type when the sole AVOID reason was low confidence (no data-quality
    # or disagreement flags) — in that case edge can legitimately rescue a LEAN.
    odds_ctx = context.get("odds")
    edge_data: dict[str, Any] = {}
    if odds_ctx and sport == "soccer":
        edge_data = odds_utils.compute_soccer_edge(
            prob_a=prob_a,
            prob_draw=prob_draw,
            prob_b=prob_b,
            selected_team=selected_team,
            home_odds=odds_ctx.get("home"),
            draw_odds=odds_ctx.get("draw"),
            away_odds=odds_ctx.get("away"),
            base_play_type=ui_prediction.get("play_type", play_type),
        )
        if edge_data.get("available"):
            model_play = ui_prediction.get("play_type", play_type)
            edge_play = edge_data["play_type"]
            # Only let edge upgrade a pure-confidence AVOID (no data/disagreement)
            edge_rescuable_reasons = {
                "No strong edge found",
                "Outcome probabilities are too close",
            }
            pure_confidence_avoid = (
                model_play == "AVOID"
                and bool(avoid_reasons)
                and set(avoid_reasons).issubset(edge_rescuable_reasons)
            )
            if edge_play != model_play and (not avoid_triggered or pure_confidence_avoid):
                ui_prediction["play_type"] = edge_play
                if pure_confidence_avoid and edge_play in {"LEAN", "BET"}:
                    best_pick["prediction"] = top_outcome.get("prediction")
                    best_pick["team"] = selected_team
                    best_pick["confidence"] = ui_prediction.get("confidence", best_pick.get("confidence", "Medium"))
                    ui_prediction["recommended_play"] = top_outcome.get("prediction")
                    ui_prediction["avoid_reasons"] = []
                    ui_prediction["top_lean"] = top_lean
                    ui_prediction["risk_label"] = "Moderate" if edge_play == "LEAN" else "Controlled"
                sel_edge = edge_data.get("selected_edge", 0.0) or 0.0
                sel_label = edge_data.get("selected_edge_label", "")
                edge_note = f" Market edge: {sel_label} ({sel_edge:+.1%})."
                existing = best_pick.get("reasoning") or ""
                best_pick["reasoning"] = (existing + edge_note).strip()
                ui_prediction["best_pick"] = best_pick

    ui_prediction["edge_data"] = edge_data

    # ── ML source metadata for template display ───────────────────────────────
    _source_raw = ml.get("source", "rules_only")
    _source_labels = {
        "stacking_ensemble_model": "Stacking Ensemble",
        "random_forest_model": "Random Forest (fallback)",
        "rules_only": "Rules Only",
    }
    ui_prediction["ml_source"] = _source_raw
    ui_prediction["ml_source_label"] = _source_labels.get(_source_raw, _source_raw)
    ui_prediction["decision_layer"] = "Combined Signal" if ml.get("available") else "Rules Only"
    ui_prediction["model_role"] = ml.get("model_role", "")
    ui_prediction["base_models"] = ml.get("base_models") or []
    ui_prediction["base_agreement"] = ml.get("base_agreement")

    return {
        "winner": winner,
        "probability": round(winner_prob, 4),
        "confidence": round(confidence, 4),
        "explanation": explanation,
        "ui_prediction": ui_prediction,
        "rule_prediction": rule_prediction,
        "model_probability": round(winner_prob, 6),   # top class probability for calibration tracking
    }
