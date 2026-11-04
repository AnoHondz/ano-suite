"""Train a stacking ensemble (Logistic Regression + Random Forest + XGBoost + LightGBM)
match outcome model on clean pre-match features only.

Target encoding:
- 0: Home Win
- 1: Draw
- 2: Away Win

Feature engineering removes all target leakage: every feature is a rolling
average computed ONLY from matches that occurred BEFORE the target match.
Team histories are updated AFTER feature extraction for each row.

Upgraded feature set (47 features):

    Overall rolling form – last 5 prior matches:
        home_avg_gf_5, home_avg_ga_5, home_avg_gd_5, home_ppg_5
        away_avg_gf_5, away_avg_ga_5, away_avg_gd_5, away_ppg_5

    Overall rolling form – last 10 prior matches:
        home_avg_gf_10, home_avg_ga_10, home_ppg_10
        away_avg_gf_10, away_avg_ga_10, away_ppg_10

    Trend delta (recent vs medium-term form):
        home_ppg_delta_5v10, home_gf_delta_5v10
        away_ppg_delta_5v10, away_gf_delta_5v10

    Venue-specific form (home team's prior home matches / away team's prior away):
        home_home_avg_gf_5, home_home_avg_ga_5, home_home_ppg_5
        away_away_avg_gf_5, away_away_avg_ga_5, away_away_ppg_5

    Scoring consistency (last 5):
        home_clean_sheet_rate_5, away_clean_sheet_rate_5
        home_scored_rate_5,      away_scored_rate_5

    Opponent-strength proxy (avg PPG of last 5 opponents at time of match):
        home_opp_avg_ppg_5, away_opp_avg_ppg_5

    Rest / fatigue:
        days_since_last_match_home, days_since_last_match_away

    Head-to-head (prior meetings only, strictly leakage-safe):
        h2h_home_points_avg, h2h_goal_diff_avg

    Derived comparison features (all leakage-safe combinations of pre-match data):
        ppg_diff_5, gf_diff_5, ga_diff_5, venue_ppg_diff_5
        attack_vs_defense_home, attack_vs_defense_away
        attack_balance_diff, scored_rate_diff_5, clean_sheet_diff_5
        rest_diff_days
"""

from __future__ import annotations

import argparse
import collections
import csv
from datetime import date
from pathlib import Path
from typing import Any

import json

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.ensemble import RandomForestClassifier, StackingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, confusion_matrix
from sklearn.utils.class_weight import compute_sample_weight

try:
    from xgboost import XGBClassifier
    _HAS_XGBOOST = True
except ImportError:
    _HAS_XGBOOST = False

try:
    from lightgbm import LGBMClassifier
    _HAS_LIGHTGBM = True
except ImportError:
    _HAS_LIGHTGBM = False

from runtime_paths import (
    clean_soccer_dataset_path,
    clean_soccer_model_path,
    data_dir,
    elo_state_path,
    ensemble_soccer_model_path,
    historical_dataset_path,
    league_model_path,
)
from utils.parsing import safe_float
from services.soccer_feature_policy import opponent_average_ppg


# ── Feature schema ────────────────────────────────────────────────────────────

SHORT_WINDOW = 5
LONG_WINDOW  = 10
H2H_WINDOW   = 5
MIN_HISTORY  = 3  # minimum prior matches per team before a row is usable

FEATURE_COLUMNS = [
    # Overall rolling form – last 5
    "home_avg_gf_5",
    "home_avg_ga_5",
    "home_avg_gd_5",
    "home_ppg_5",
    "away_avg_gf_5",
    "away_avg_ga_5",
    "away_avg_gd_5",
    "away_ppg_5",
    # Overall rolling form – last 10
    "home_avg_gf_10",
    "home_avg_ga_10",
    "home_ppg_10",
    "away_avg_gf_10",
    "away_avg_ga_10",
    "away_ppg_10",
    # Trend delta: short-term vs medium-term
    "home_ppg_delta_5v10",
    "home_gf_delta_5v10",
    "away_ppg_delta_5v10",
    "away_gf_delta_5v10",
    # Venue-specific form (home team at home / away team away) – last 5
    "home_home_avg_gf_5",
    "home_home_avg_ga_5",
    "home_home_ppg_5",
    "away_away_avg_gf_5",
    "away_away_avg_ga_5",
    "away_away_ppg_5",
    # Scoring consistency – last 5
    "home_clean_sheet_rate_5",
    "away_clean_sheet_rate_5",
    "home_scored_rate_5",
    "away_scored_rate_5",
    # Opponent-strength proxy – avg PPG of last 5 opponents faced
    "home_opp_avg_ppg_5",
    "away_opp_avg_ppg_5",
    # Rest / fatigue
    "days_since_last_match_home",
    "days_since_last_match_away",
    # Head-to-head (prior meetings only)
    "h2h_home_points_avg",
    "h2h_goal_diff_avg",
    # Derived comparison features
    "ppg_diff_5",
    "gf_diff_5",
    "ga_diff_5",
    "venue_ppg_diff_5",
    "attack_vs_defense_home",
    "attack_vs_defense_away",
    "attack_balance_diff",
    "scored_rate_diff_5",
    "clean_sheet_diff_5",
    "rest_diff_days",
    # ELO ratings (pre-match, leakage-safe)
    "home_elo",
    "away_elo",
    "elo_diff",
    # Market odds features (median-filled when absent)
    "home_implied_prob",
    "away_implied_prob",
    "draw_implied_prob",
    "odds_home_away_ratio",
    "market_confidence",
    # xG features (median-filled when absent)
    "home_xg_last5",
    "away_xg_last5",
    "home_xg_conceded_last5",
    "away_xg_conceded_last5",
    "xg_diff",
    # Referee features (median-filled for unknown referees)
    "referee_avg_cards",
    "referee_avg_goals",
    "referee_penalty_rate",
]

CLASS_LABELS = {0: "HomeWin", 1: "Draw", 2: "AwayWin"}

ELO_BASE = 1500.0   # starting ELO for every new team
ELO_K    = 20.0     # update factor per match
RECENCY_HALF_LIFE_DAYS = 365.0
RECENCY_MIN_WEIGHT = 0.45
RECENCY_MAX_WEIGHT = 2.35


# ── Target parsing ─────────────────────────────────────────────────────────────

def _target_from_row(row: dict[str, Any]) -> int | None:
    """Parse result column → 0 (HomeWin), 1 (Draw), 2 (AwayWin), or None."""
    raw = str(row.get("result") or row.get("target") or "").strip()
    lower = raw.lower()
    if lower in {"homewin", "home win", "h"} or raw == "0":
        return 0
    if lower in {"draw", "d", "x", "tie"} or raw == "1":
        return 1
    if lower in {"awaywin", "away win", "a"} or raw == "2":
        return 2
    return None


# ── Rolling feature engineering ───────────────────────────────────────────────

def _rolling_stats(history: list[dict[str, Any]], window: int = SHORT_WINDOW) -> dict[str, float]:
    """Compute rolling averages from the last `window` entries in history.

    Each history entry must have: gf (float), ga (float), pts (float).
    Optional field: opp_ppg (float) – opponent's PPG at time of match.
    """
    recent = history[-window:] if history else []
    if not recent:
        return {
            "avg_gf": 0.0, "avg_ga": 0.0, "avg_gd": 0.0, "ppg": 0.0,
            "clean_sheet_rate": 0.0, "scored_rate": 0.0, "opp_avg_ppg": None,
        }
    n = float(len(recent))
    avg_gf = sum(r["gf"] for r in recent) / n
    avg_ga = sum(r["ga"] for r in recent) / n
    ppg    = sum(r["pts"] for r in recent) / n
    clean_sheet_rate = sum(1.0 for r in recent if r["ga"] == 0.0) / n
    scored_rate      = sum(1.0 for r in recent if r["gf"] >= 1.0) / n
    opp_avg_ppg      = opponent_average_ppg(reversed(recent), window)
    return {
        "avg_gf":           round(avg_gf, 4),
        "avg_ga":           round(avg_ga, 4),
        "avg_gd":           round(avg_gf - avg_ga, 4),
        "ppg":              round(ppg, 4),
        "clean_sheet_rate": round(clean_sheet_rate, 4),
        "scored_rate":      round(scored_rate, 4),
        "opp_avg_ppg":      opp_avg_ppg,
    }


def _venue_rolling_stats(
    history: list[dict[str, Any]], is_home: bool, window: int = SHORT_WINDOW
) -> dict[str, float]:
    """Compute rolling stats filtered to home or away matches only."""
    venue_hist = [r for r in history if r.get("is_home") == is_home]
    return _rolling_stats(venue_hist, window)


def _parse_date(date_str: str) -> date | None:
    """Parse ISO-format date string (YYYY-MM-DD) to a date object."""
    try:
        return date.fromisoformat(str(date_str).strip())
    except ValueError:
        return None


def build_clean_features(
    historical_path: Path,
    league_id_filter: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Build a leakage-free pre-match feature dataset from historical_matches.csv.

    Each row's features are computed ONLY from matches before that match's date.
    Both teams' histories are updated AFTER feature extraction for that row.
    Rows where either team has fewer than MIN_HISTORY prior matches are dropped.

    When league_id_filter is set, only rows where row["league_id"] == str(league_id_filter)
    are included in the output feature set (histories still process all rows for warm-up).

    History entry fields stored per team:
        gf       (float) – goals scored
        ga       (float) – goals conceded
        pts      (float) – points earned (3/1/0)
        is_home  (bool)  – was this match at home?
        opp      (str)   – opponent team name
        opp_ppg  (float) – opponent's rolling PPG before this match
    """
    with historical_path.open("r", encoding="utf-8", newline="") as fh:
        raw_rows = list(csv.DictReader(fh))

    raw_rows.sort(key=lambda r: str(r.get("date", "")))

    team_history:     dict[str, list[dict[str, Any]]]        = collections.defaultdict(list)
    h2h_records:      dict[tuple[str, str], list[dict]]      = collections.defaultdict(list)
    last_match_dates: dict[str, date]                        = {}
    elo_ratings:      dict[str, float]                       = collections.defaultdict(lambda: ELO_BASE)
    referee_history:  dict[str, list[dict[str, Any]]]        = collections.defaultdict(list)
    processed:        list[dict[str, Any]]                   = []

    for row in raw_rows:
        home_team = str(row.get("home_team", "")).strip()
        away_team = str(row.get("away_team", "")).strip()
        if not home_team or not away_team:
            continue

        result = _target_from_row(row)
        if result is None:
            continue

        # Final scores – used ONLY to update histories after feature extraction
        home_gf      = safe_float(row.get("goals_scored"),  0.0)
        home_ga      = safe_float(row.get("goals_conceded"), 0.0)
        current_date = _parse_date(row.get("date", ""))

        home_hist = team_history[home_team]
        away_hist = team_history[away_team]

        # Capture ELO BEFORE this match (leakage-safe: pre-match strength estimate)
        h_elo = elo_ratings[home_team]
        a_elo = elo_ratings[away_team]

        # Pre-compute short-window stats for BOTH teams.
        # These are also used as the opp_ppg value stored in opponent history entries.
        home_s5 = _rolling_stats(home_hist, SHORT_WINDOW)
        away_s5 = _rolling_stats(away_hist, SHORT_WINDOW)

        # Odds features — read from CSV columns if present (else 0.0 sentinel for median fill)
        raw_odds_home = safe_float(row.get("odds_home"), 0.0)
        raw_odds_away = safe_float(row.get("odds_away"), 0.0)
        raw_odds_draw = safe_float(row.get("odds_draw"), 0.0)
        if raw_odds_home > 1.0 and raw_odds_away > 1.0 and raw_odds_draw > 1.0:
            _odds_margin = 1.0/raw_odds_home + 1.0/raw_odds_draw + 1.0/raw_odds_away
            _home_ip = (1.0/raw_odds_home) / _odds_margin
            _away_ip = (1.0/raw_odds_away) / _odds_margin
            _draw_ip = (1.0/raw_odds_draw) / _odds_margin
            _odds_ratio = round(_home_ip / max(_away_ip, 0.01), 4)
            _mkt_conf = max(_home_ip, _away_ip, _draw_ip)
        else:
            _home_ip = 0.0; _away_ip = 0.0; _draw_ip = 0.0
            _odds_ratio = 0.0; _mkt_conf = 0.0

        # xG features — rolling average of last 5 for each team (0.0 sentinel for median fill)
        _home_xg_hist = team_history[home_team]
        _away_xg_hist = team_history[away_team]

        def _xg_avg(hist: list[dict[str, Any]], key: str, window: int = SHORT_WINDOW) -> float:
            recent = hist[-window:]
            vals = [r[key] for r in recent if r.get(key, 0.0) > 0.0]
            return round(sum(vals) / len(vals), 4) if vals else 0.0

        _home_xg5 = _xg_avg(_home_xg_hist, "xg_for")
        _away_xg5 = _xg_avg(_away_xg_hist, "xg_for")
        _home_xgc5 = _xg_avg(_home_xg_hist, "xg_against")
        _away_xgc5 = _xg_avg(_away_xg_hist, "xg_against")
        _xg_diff = round(_home_xg5 - _away_xg5, 4)

        # Referee features — expanding window from referee history (no leakage)
        referee = str(row.get("referee", "")).strip()
        _ref_hist = referee_history[referee] if referee else []
        if _ref_hist:
            _ref_avg_cards = round(sum(r["total_cards"] for r in _ref_hist) / len(_ref_hist), 4)
            _ref_avg_goals = round(sum(r["total_goals"] for r in _ref_hist) / len(_ref_hist), 4)
            _ref_pen_rate  = round(sum(r["penalties"] for r in _ref_hist) / len(_ref_hist), 4)
        else:
            # 0.0 sentinel — will be median-filled after loop
            _ref_avg_cards = 0.0; _ref_avg_goals = 0.0; _ref_pen_rate = 0.0

        # League filter: only include in processed output when filter matches
        _row_league_id = str(row.get("league_id", "")).strip()
        _league_match = (
            league_id_filter is None
            or _row_league_id == str(league_id_filter)
        )

        # ── Feature extraction (only when both teams have enough prior data) ──
        if len(home_hist) >= MIN_HISTORY and len(away_hist) >= MIN_HISTORY and _league_match:
            home_s10    = _rolling_stats(home_hist, LONG_WINDOW)
            away_s10    = _rolling_stats(away_hist, LONG_WINDOW)
            home_venue  = _venue_rolling_stats(home_hist, is_home=True,  window=SHORT_WINDOW)
            away_venue  = _venue_rolling_stats(away_hist, is_home=False, window=SHORT_WINDOW)

            # H2H: prior pairings where THIS home team was home vs THIS away team
            prior_h2h   = h2h_records[(home_team, away_team)][-H2H_WINDOW:]
            h2h_pts     = sum(e["pts"] for e in prior_h2h) / len(prior_h2h) if prior_h2h else 1.0
            h2h_gd      = sum(e["gd"]  for e in prior_h2h) / len(prior_h2h) if prior_h2h else 0.0

            # Days since last match (capped at 60 to suppress gaps from data sparsity)
            def _days(team: str) -> float:
                if current_date and team in last_match_dates:
                    return min(float((current_date - last_match_dates[team]).days), 60.0)
                return 7.0

            processed.append({
                "date":       row.get("date", ""),
                "home_team":  home_team,
                "away_team":  away_team,
                # Overall last-5
                "home_avg_gf_5":         home_s5["avg_gf"],
                "home_avg_ga_5":         home_s5["avg_ga"],
                "home_avg_gd_5":         home_s5["avg_gd"],
                "home_ppg_5":            home_s5["ppg"],
                "away_avg_gf_5":         away_s5["avg_gf"],
                "away_avg_ga_5":         away_s5["avg_ga"],
                "away_avg_gd_5":         away_s5["avg_gd"],
                "away_ppg_5":            away_s5["ppg"],
                # Overall last-10
                "home_avg_gf_10":        home_s10["avg_gf"],
                "home_avg_ga_10":        home_s10["avg_ga"],
                "home_ppg_10":           home_s10["ppg"],
                "away_avg_gf_10":        away_s10["avg_gf"],
                "away_avg_ga_10":        away_s10["avg_ga"],
                "away_ppg_10":           away_s10["ppg"],
                # Trend delta
                "home_ppg_delta_5v10":   round(home_s5["ppg"]    - home_s10["ppg"],    4),
                "home_gf_delta_5v10":    round(home_s5["avg_gf"] - home_s10["avg_gf"], 4),
                "away_ppg_delta_5v10":   round(away_s5["ppg"]    - away_s10["ppg"],    4),
                "away_gf_delta_5v10":    round(away_s5["avg_gf"] - away_s10["avg_gf"], 4),
                # Venue-specific form
                "home_home_avg_gf_5":    home_venue["avg_gf"],
                "home_home_avg_ga_5":    home_venue["avg_ga"],
                "home_home_ppg_5":       home_venue["ppg"],
                "away_away_avg_gf_5":    away_venue["avg_gf"],
                "away_away_avg_ga_5":    away_venue["avg_ga"],
                "away_away_ppg_5":       away_venue["ppg"],
                # Scoring consistency
                "home_clean_sheet_rate_5": home_s5["clean_sheet_rate"],
                "away_clean_sheet_rate_5": away_s5["clean_sheet_rate"],
                "home_scored_rate_5":      home_s5["scored_rate"],
                "away_scored_rate_5":      away_s5["scored_rate"],
                # Opponent-strength proxy
                "home_opp_avg_ppg_5":    home_s5["opp_avg_ppg"],
                "away_opp_avg_ppg_5":    away_s5["opp_avg_ppg"],
                # Rest / fatigue
                "days_since_last_match_home": _days(home_team),
                "days_since_last_match_away": _days(away_team),
                # H2H
                "h2h_home_points_avg":   round(h2h_pts, 4),
                "h2h_goal_diff_avg":     round(h2h_gd,  4),
                # Derived comparison features
                "ppg_diff_5":            round(home_s5["ppg"] - away_s5["ppg"], 4),
                "gf_diff_5":             round(home_s5["avg_gf"] - away_s5["avg_gf"], 4),
                "ga_diff_5":             round(away_s5["avg_ga"] - home_s5["avg_ga"], 4),
                "venue_ppg_diff_5":      round(home_venue["ppg"] - away_venue["ppg"], 4),
                "attack_vs_defense_home": round(home_s5["avg_gf"] - away_s5["avg_ga"], 4),
                "attack_vs_defense_away": round(away_s5["avg_gf"] - home_s5["avg_ga"], 4),
                "attack_balance_diff":   round(
                    (home_s5["avg_gf"] - away_s5["avg_ga"]) - (away_s5["avg_gf"] - home_s5["avg_ga"]),
                    4,
                ),
                "scored_rate_diff_5":    round(home_s5["scored_rate"] - away_s5["scored_rate"], 4),
                "clean_sheet_diff_5":    round(home_s5["clean_sheet_rate"] - away_s5["clean_sheet_rate"], 4),
                "rest_diff_days":        round(_days(home_team) - _days(away_team), 4),
                # ELO ratings (pre-match)
                "home_elo":              round(h_elo, 2),
                "away_elo":              round(a_elo, 2),
                "elo_diff":              round(h_elo - a_elo, 2),
                # Market odds (0.0 sentinel when not present — median-filled after loop)
                "home_implied_prob":     round(_home_ip, 4),
                "away_implied_prob":     round(_away_ip, 4),
                "draw_implied_prob":     round(_draw_ip, 4),
                "odds_home_away_ratio":  _odds_ratio,
                "market_confidence":     round(_mkt_conf, 4),
                # xG features (0.0 sentinel when not present — median-filled after loop)
                "home_xg_last5":         _home_xg5,
                "away_xg_last5":         _away_xg5,
                "home_xg_conceded_last5": _home_xgc5,
                "away_xg_conceded_last5": _away_xgc5,
                "xg_diff":               _xg_diff,
                # Referee features (0.0 sentinel when unknown — median-filled after loop)
                "referee_avg_cards":     _ref_avg_cards,
                "referee_avg_goals":     _ref_avg_goals,
                "referee_penalty_rate":  _ref_pen_rate,
                # Target
                "result": CLASS_LABELS[result],
                "target": result,
            })

        # ── Update histories AFTER feature extraction (no leakage) ──────────
        home_pts = 3.0 if result == 0 else (1.0 if result == 1 else 0.0)
        away_pts = 3.0 if result == 2 else (1.0 if result == 1 else 0.0)

        # Read match xG from CSV (0.0 when absent)
        _match_home_xg = safe_float(row.get("home_xg"), 0.0)
        _match_away_xg = safe_float(row.get("away_xg"), 0.0)

        # opp_ppg stored = opponent's pre-match rolling PPG (computed above)
        team_history[home_team].append({
            "gf": home_gf, "ga": home_ga, "pts": home_pts,
            "is_home": True,  "opp": away_team, "opp_ppg": away_s5["ppg"],
            "xg_for": _match_home_xg, "xg_against": _match_away_xg,
        })
        team_history[away_team].append({
            "gf": home_ga, "ga": home_gf, "pts": away_pts,
            "is_home": False, "opp": home_team, "opp_ppg": home_s5["ppg"],
            "xg_for": _match_away_xg, "xg_against": _match_home_xg,
        })

        # Referee history update
        if referee:
            _total_cards = safe_float(row.get("home_yellow_cards"), 0.0) + \
                           safe_float(row.get("away_yellow_cards"), 0.0) + \
                           safe_float(row.get("home_red_cards"), 0.0) + \
                           safe_float(row.get("away_red_cards"), 0.0)
            _total_goals = home_gf + home_ga
            _penalties   = safe_float(row.get("penalties"), 0.0)
            referee_history[referee].append({
                "total_cards": _total_cards,
                "total_goals": _total_goals,
                "penalties": _penalties,
            })

        h2h_records[(home_team, away_team)].append(
            {"pts": home_pts, "gd": home_gf - home_ga}
        )
        h2h_records[(away_team, home_team)].append(
            {"pts": away_pts, "gd": home_ga - home_gf}
        )

        if current_date:
            last_match_dates[home_team] = current_date
            last_match_dates[away_team] = current_date

        # ── Update ELO AFTER feature extraction (no leakage) ────────────────
        home_score = 1.0 if result == 0 else (0.5 if result == 1 else 0.0)
        away_score = 1.0 - home_score
        h_expected = 1.0 / (1.0 + 10.0 ** ((a_elo - h_elo) / 400.0))
        a_expected = 1.0 - h_expected
        elo_ratings[home_team] = round(h_elo + ELO_K * (home_score - h_expected), 2)
        elo_ratings[away_team] = round(a_elo + ELO_K * (away_score - a_expected), 2)

    return processed, dict(elo_ratings)


# ── IO helpers ────────────────────────────────────────────────────────────────

def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("No rows available to write.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _row_to_features(row: dict[str, Any]) -> list[float]:
    return [safe_float(row.get(f), 0.0) for f in FEATURE_COLUMNS]


def build_logistic_estimator(random_state: int = 42) -> LogisticRegression:
    """Centralized LR settings used by training and walk-forward backtests."""
    return LogisticRegression(
        max_iter=3000,
        solver="lbfgs",
        C=0.85,
        tol=1e-3,
        random_state=random_state,
    )


def compute_recency_weights(
    rows: list[dict[str, Any]],
    *,
    half_life_days: float = RECENCY_HALF_LIFE_DAYS,
    min_weight: float = RECENCY_MIN_WEIGHT,
    max_weight: float = RECENCY_MAX_WEIGHT,
) -> np.ndarray:
    """Return normalized time-decay weights where newer rows matter more."""
    if not rows:
        return np.array([], dtype=float)

    parsed_dates = [_parse_date(row.get("date", "")) for row in rows]
    valid_dates = [value for value in parsed_dates if value is not None]
    if not valid_dates:
        return np.ones(len(rows), dtype=float)

    latest = max(valid_dates)
    safe_half_life = max(float(half_life_days), 1.0)
    raw_weights = []
    for parsed in parsed_dates:
        if parsed is None:
            raw_weights.append(1.0)
            continue
        age_days = max((latest - parsed).days, 0)
        raw_weights.append(0.5 ** (age_days / safe_half_life))

    weights = np.array(raw_weights, dtype=float)
    weights = weights / (float(np.mean(weights)) or 1.0)
    weights = np.clip(weights, float(min_weight), float(max_weight))
    weights = weights / (float(np.mean(weights)) or 1.0)
    return weights


def build_training_sample_weights(
    rows: list[dict[str, Any]],
    y: np.ndarray | list[int],
) -> np.ndarray:
    """Blend class balancing with a light recency tilt for newer matches."""
    y_array = np.asarray(y)
    if y_array.size == 0:
        return np.array([], dtype=float)

    class_weights = compute_sample_weight("balanced", y_array)
    recency_weights = compute_recency_weights(rows)
    combined = np.asarray(class_weights, dtype=float) * np.asarray(recency_weights, dtype=float)
    combined = combined / (float(np.mean(combined)) or 1.0)
    return combined


def sample_weighting_metadata() -> dict[str, float | str]:
    return {
        "type": "balanced_times_recency",
        "recency_half_life_days": RECENCY_HALF_LIFE_DAYS,
        "recency_min_weight": RECENCY_MIN_WEIGHT,
        "recency_max_weight": RECENCY_MAX_WEIGHT,
    }


# ── Training pipeline ─────────────────────────────────────────────────────────

def train_model(
    historical_path: Path | None = None,
    output_path: Path | None = None,
    processed_dataset_path: Path | None = None,
    random_state: int = 42,
    league_id: int | None = None,
) -> dict[str, Any]:
    """Build clean features, train 4 base models + stacking ensemble, save model.

    When league_id is set, only rows for that league are included in the training
    set. The model is saved to league_model_path(league_id) unless output_path
    overrides it. Returns {"skipped": True, "reason": ...} when sample size < 500.
    """
    historical = historical_path or historical_dataset_path()
    if not historical.exists():
        raise FileNotFoundError(
            f"Historical dataset not found: {historical}\n"
            "Provide data/historical_matches.csv to train the model."
        )

    print(f"Building clean pre-match features from: {historical}")
    rows, final_elo = build_clean_features(historical, league_id_filter=league_id)

    if league_id is not None:
        print(f"Per-league filter: league_id={league_id}  →  {len(rows)} rows after filtering")
        if len(rows) < 500:
            print(
                f"[warn] Insufficient data for league {league_id}: "
                f"{len(rows)} rows (need >= 500). Skipping."
            )
            return {"skipped": True, "reason": "insufficient data", "league_id": league_id, "rows": len(rows)}

    if len(rows) < 20:
        raise ValueError(
            f"Only {len(rows)} usable rows after feature engineering (need ≥ 20). "
            f"Check that historical_matches.csv has enough rows and team_history ≥ {MIN_HISTORY}."
        )

    # ── Save processed dataset ────────────────────────────────────────────────
    processed_path = processed_dataset_path or clean_soccer_dataset_path()
    _write_rows(processed_path, rows)
    print(f"Saved clean dataset ({len(rows)} rows) → {processed_path}")

    # ── Feature matrix and targets ────────────────────────────────────────────
    x = np.array([_row_to_features(r) for r in rows])
    y = np.array([int(r["target"]) for r in rows])
    n_train = len(rows) - max(1, int(len(rows) * 0.20)) * 2

    # Median-fill odds features: 0.0 sentinel means data was absent
    for _col_name in ("home_implied_prob", "away_implied_prob", "draw_implied_prob",
                      "odds_home_away_ratio", "market_confidence"):
        _col_idx = FEATURE_COLUMNS.index(_col_name)
        _col_vals = x[:, _col_idx]
        _present = _col_vals[:n_train][_col_vals[:n_train] > 0.0]
        if len(_present) > 0:
            _median = float(np.median(_present))
            x[_col_vals == 0.0, _col_idx] = _median

    # Median-fill xG features: 0.0 sentinel means data was absent
    for _col_name in ("home_xg_last5", "away_xg_last5",
                      "home_xg_conceded_last5", "away_xg_conceded_last5"):
        _col_idx = FEATURE_COLUMNS.index(_col_name)
        _col_vals = x[:, _col_idx]
        _present = _col_vals[:n_train][_col_vals[:n_train] > 0.0]
        if len(_present) > 0:
            _median = float(np.median(_present))
            x[_col_vals == 0.0, _col_idx] = _median
    # xg_diff can legitimately be negative; recompute from filled values
    _hi = FEATURE_COLUMNS.index("home_xg_last5")
    _ai = FEATURE_COLUMNS.index("away_xg_last5")
    _di = FEATURE_COLUMNS.index("xg_diff")
    x[:, _di] = np.round(x[:, _hi] - x[:, _ai], 4)

    # Median-fill referee features: 0.0 sentinel means referee was unknown
    for _col_name in ("referee_avg_cards", "referee_avg_goals", "referee_penalty_rate"):
        _col_idx = FEATURE_COLUMNS.index(_col_name)
        _col_vals = x[:, _col_idx]
        _present = _col_vals[:n_train][_col_vals[:n_train] > 0.0]
        if len(_present) > 0:
            _median = float(np.median(_present))
            x[_col_vals == 0.0, _col_idx] = _median

    # ── Class balance ─────────────────────────────────────────────────────────
    class_counts = collections.Counter(y.tolist())
    print("\nClass balance:")
    for cls, label in CLASS_LABELS.items():
        count = class_counts.get(cls, 0)
        pct = count / len(y) * 100.0
        print(f"  {label:10s} ({cls}): {count:4d}  ({pct:.1f}%)")

    # ── Chronological 60/20/20 split: train / calibration / test ─────────────
    n_test  = max(1, int(len(rows) * 0.20))
    n_cal   = max(1, int(len(rows) * 0.20))
    n_train = len(rows) - n_test - n_cal
    train_rows = rows[:n_train]
    cal_rows = rows[n_train:n_train+n_cal]
    test_rows = rows[n_train+n_cal:]
    x_train, y_train = x[:n_train],              y[:n_train]
    x_cal,   y_cal   = x[n_train:n_train+n_cal], y[n_train:n_train+n_cal]
    x_test,  y_test  = x[n_train+n_cal:],        y[n_train+n_cal:]
    print(
        f"\nChronological split: {len(x_train)} train / "
        f"{len(x_cal)} calibration / {len(x_test)} test"
    )

    # ── Define base estimators ────────────────────────────────────────────────
    estimators: list[tuple[str, Any]] = [
        ("lr", build_logistic_estimator(random_state)),
        ("rf", RandomForestClassifier(
            n_estimators=300,
            max_depth=8,
            min_samples_leaf=3,
            random_state=random_state,
        )),
    ]

    if _HAS_XGBOOST:
        estimators.append(("xgb", XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="mlogloss",
            random_state=random_state,
            verbosity=0,
            # sample_weight passed at fit() time below for class balancing
        )))
    else:
        print("  [warn] xgboost not installed – skipping XGBoost base model")

    if _HAS_LIGHTGBM:
        estimators.append(("lgbm", LGBMClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=random_state,
            verbose=-1,
        )))
    else:
        print("  [warn] lightgbm not installed – skipping LightGBM base model")

    print(f"\nBase models: {[name for name, _ in estimators]}")

    # ── Compute one balanced sample weighting scheme for every estimator ───────
    _sample_weight_train = build_training_sample_weights(train_rows, y_train)
    recency_train = compute_recency_weights(train_rows)
    print(
        "Sample weighting: "
        f"balanced x recency (half-life {RECENCY_HALF_LIFE_DAYS:.0f}d, "
        f"recent weight range {recency_train.min():.2f}-{recency_train.max():.2f})"
    )

    # ── Train individual base models and report accuracy ──────────────────────
    base_models: dict[str, Any] = {}
    for name, est in estimators:
        est.fit(x_train, y_train, sample_weight=_sample_weight_train)
        preds = est.predict(x_test)
        acc = float(accuracy_score(y_test, preds))
        base_models[name] = {"model": est, "accuracy": acc}
        print(f"  {name:6s} test accuracy: {acc * 100:.1f}%")

    # ── Build stacking ensemble with LR meta-learner ──────────────────────────
    # Re-create fresh estimators for StackingClassifier (it fits them internally)
    stack_estimators: list[tuple[str, Any]] = []
    for name, _ in estimators:
        if name == "lr":
            stack_estimators.append(("lr", build_logistic_estimator(random_state)))
        elif name == "rf":
            stack_estimators.append(("rf", RandomForestClassifier(
                n_estimators=300, max_depth=8, min_samples_leaf=3,
                random_state=random_state,
            )))
        elif name == "xgb" and _HAS_XGBOOST:
            stack_estimators.append(("xgb", XGBClassifier(
                n_estimators=200, max_depth=6, learning_rate=0.1,
                subsample=0.8, colsample_bytree=0.8,
                eval_metric="mlogloss",
                random_state=random_state, verbosity=0,
            )))
        elif name == "lgbm" and _HAS_LIGHTGBM:
            stack_estimators.append(("lgbm", LGBMClassifier(
                n_estimators=200, max_depth=6, learning_rate=0.1,
                subsample=0.8, colsample_bytree=0.8,
                random_state=random_state, verbose=-1,
            )))

    stacking_model = StackingClassifier(
        estimators=stack_estimators,
        final_estimator=build_logistic_estimator(random_state),
        cv=5,
        stack_method="predict_proba",
        passthrough=False,
    )

    print("\nTraining stacking ensemble ...")
    stacking_model.fit(x_train, y_train, sample_weight=_sample_weight_train)

    # ── Brier score before calibration ────────────────────────────────────────
    n_classes = len(CLASS_LABELS)
    proba_raw = stacking_model.predict_proba(x_test)
    brier_raw = float(
        sum(
            brier_score_loss(
                [1 if yi == c else 0 for yi in y_test],
                [p[c] for p in proba_raw],
            )
            for c in range(n_classes)
        )
        / n_classes
    )

    # ── Isotonic calibration on the held-out calibration split ────────────────
    calibrator = CalibratedClassifierCV(FrozenEstimator(stacking_model), method="isotonic")
    calibrator.fit(x_cal, y_cal)
    model = calibrator

    # ── Evaluate ──────────────────────────────────────────────────────────────
    predictions = model.predict(x_test)
    accuracy = float(accuracy_score(y_test, predictions))
    print(f"\nEnsemble test accuracy: {accuracy * 100:.1f}%  (random baseline ≈ 33%)")

    cm = confusion_matrix(y_test, predictions, labels=[0, 1, 2])
    print("\nConfusion matrix (rows=actual, cols=predicted):")
    print(f"  {'':10s}  " + "  ".join(f"{CLASS_LABELS[i]:10s}" for i in range(3)))
    for i, counts in enumerate(cm):
        print(f"  {CLASS_LABELS[i]:10s}  " + "  ".join(f"{v:10d}" for v in counts))

    # Brier score after calibration
    proba_test = model.predict_proba(x_test)
    brier = float(
        sum(
            brier_score_loss(
                [1 if yi == c else 0 for yi in y_test],
                [p[c] for p in proba_test],
            )
            for c in range(n_classes)
        )
        / n_classes
    )
    print(f"\nRaw Brier: {brier_raw:.3f} | Calibrated Brier: {brier:.3f}  (lower = better)")

    # ── Expected Calibration Error (ECE) ─────────────────────────────────────
    # Bucket predictions by max-class confidence into 10 equal-width bins.
    _proba_max = np.max(proba_test, axis=1)   # confidence = max class probability
    _ece_bins = 10
    _bin_edges = np.linspace(0.0, 1.0, _ece_bins + 1)
    _ece_buckets: list[dict] = []
    for _bi in range(_ece_bins):
        _lo, _hi = _bin_edges[_bi], _bin_edges[_bi + 1]
        _mask = (_proba_max >= _lo) & (_proba_max < _hi if _bi < _ece_bins - 1 else _proba_max <= _hi)
        _bin_size = int(_mask.sum())
        if _bin_size == 0:
            continue
        _bin_conf = float(np.mean(_proba_max[_mask]))
        _bin_preds = np.argmax(proba_test[_mask], axis=1)
        _bin_acc = float(np.mean(_bin_preds == y_test[_mask]))
        _ece_buckets.append({
            "range": f"{_lo * 100:.0f}-{_hi * 100:.0f}%",
            "count": _bin_size,
            "fraction": round(_bin_size / len(y_test), 4),
            "avg_confidence": round(_bin_conf, 4),
            "actual_accuracy": round(_bin_acc, 4),
        })
    _ece = float(sum(b["fraction"] * abs(b["avg_confidence"] - b["actual_accuracy"]) for b in _ece_buckets))
    print(f"\nExpected Calibration Error (ECE): {_ece * 100:.3f}%")
    print(f"  {'Bin':>8}  {'Count':>6}  {'Avg Conf':>9}  {'Actual Acc':>10}  {'Gap':>8}")
    for _b in _ece_buckets:
        print(
            f"  {_b['range']:>8}  {_b['count']:>6}  "
            f"{_b['avg_confidence'] * 100:>8.1f}%  "
            f"{_b['actual_accuracy'] * 100:>9.1f}%  "
            f"{(_b['avg_confidence'] - _b['actual_accuracy']) * 100:>+7.1f}%"
        )

    # ── Feature importances (from RF base model) ─────────────────────────────
    rf_model = base_models.get("rf", {}).get("model")
    if rf_model is not None and hasattr(rf_model, "feature_importances_"):
        print("\nFeature importances (from RF base model):")
        ranked = sorted(zip(FEATURE_COLUMNS, rf_model.feature_importances_), key=lambda t: -t[1])
        for feat, imp in ranked:
            print(f"  {feat:30s}: {imp:.4f}")

    print(f"\nFeatures used ({len(FEATURE_COLUMNS)}): {FEATURE_COLUMNS}")

    # ── Save ensemble model bundle ────────────────────────────────────────────
    if output_path is not None:
        save_path = output_path
    elif league_id is not None:
        save_path = league_model_path(league_id)
    else:
        save_path = ensemble_soccer_model_path()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "model": model,
        "model_type": "stacking_ensemble",
        "model_role": "production",
        "base_models": [name for name, _ in estimators],
        "base_accuracies": {name: info["accuracy"] for name, info in base_models.items()},
        "feature_names": FEATURE_COLUMNS,
        "class_labels": CLASS_LABELS,
        "accuracy": accuracy,
        "brier_score": brier,
        "brier_score_raw": brier_raw,
        "calibrated": True,
        "calibration_method": "isotonic",
        "ece": round(_ece, 6),
        "ece_buckets": _ece_buckets,
        "league_id": league_id,
        "rolling_window": SHORT_WINDOW,
        "sample_weighting": sample_weighting_metadata(),
        "dataset_path": str(processed_path),
        "train_date_range": [train_rows[0].get("date", ""), train_rows[-1].get("date", "")] if train_rows else [],
        "calibration_date_range": [cal_rows[0].get("date", ""), cal_rows[-1].get("date", "")] if cal_rows else [],
        "test_date_range": [test_rows[0].get("date", ""), test_rows[-1].get("date", "")] if test_rows else [],
    }
    joblib.dump(bundle, save_path)
    print(f"\nSaved ensemble model → {save_path}")

    # Also save standalone RF for backward-compatibility with older code paths
    # (only for the combined model, not per-league variants)
    if league_id is None:
        _save_rf_compat = True
    else:
        _save_rf_compat = False
    rf_compat_path = clean_soccer_model_path()
    rf_compat_path.parent.mkdir(parents=True, exist_ok=True)
    if _save_rf_compat:
        rf_base = base_models["rf"]["model"]
        rf_bundle = {
            "model": rf_base,
            "model_type": "random_forest",
            "model_role": "fallback",
            "feature_names": FEATURE_COLUMNS,
            "class_labels": CLASS_LABELS,
            "accuracy": base_models["rf"]["accuracy"],
            "rolling_window": SHORT_WINDOW,
            "sample_weighting": sample_weighting_metadata(),
            "dataset_path": str(processed_path),
        }
        joblib.dump(rf_bundle, rf_compat_path)
        print(f"Saved RF compat model → {rf_compat_path}")

    # ── Save ELO state for runtime lookup (combined model only) ───────────────
    if league_id is None:
        elo_path = elo_state_path()
        elo_path.parent.mkdir(parents=True, exist_ok=True)
        elo_path.write_text(json.dumps(final_elo, indent=2), encoding="utf-8")
        print(f"Saved ELO state ({len(final_elo)} teams) → {elo_path}")

    return {
        "dataset": str(processed_path),
        "model_path": str(save_path),
        "total_rows": len(rows),
        "train_size": len(x_train),
        "cal_size": len(x_cal),
        "test_size": len(x_test),
        "accuracy": accuracy,
        "brier_score": brier,
        "brier_score_raw": brier_raw,
        "ece": round(_ece, 6),
        "calibrated": True,
        "calibration_method": "isotonic",
        "league_id": league_id,
        "model_type": "stacking_ensemble",
        "sample_weighting": sample_weighting_metadata(),
        "base_models": [name for name, _ in estimators],
        "base_accuracies": {name: info["accuracy"] for name, info in base_models.items()},
        "feature_names": FEATURE_COLUMNS,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train ScorPred Soccer stacking ensemble on clean pre-match features."
    )
    parser.add_argument(
        "--historical",
        default=str(historical_dataset_path()),
        help="Path to historical_matches.csv.",
    )
    parser.add_argument(
        "--output",
        default=str(ensemble_soccer_model_path()),
        help="Where to save the ensemble model pickle.",
    )
    parser.add_argument("--random-state", type=int, default=42, help="Random seed.")
    parser.add_argument("--league-id", type=int, default=None, help="Train on one league only.")
    parser.add_argument("--all-leagues", action="store_true", help="Train per-league + combined.")
    return parser


def main() -> int:
    data_dir().mkdir(parents=True, exist_ok=True)
    args = build_parser().parse_args()

    if args.all_leagues:
        import league_config
        league_ids = league_config.SUPPORTED_LEAGUE_IDS
        print(f"\nTraining per-league models for league IDs: {league_ids}")
        for lid in league_ids:
            print(f"\n{'=' * 60}")
            print(f"  Per-league training: league_id={lid}")
            print(f"{'=' * 60}")
            r = train_model(
                historical_path=Path(args.historical),
                random_state=args.random_state,
                league_id=lid,
            )
            if r.get("skipped"):
                print(f"  Skipped: {r.get('reason')}")
            else:
                print(f"  Done. Test accuracy: {r['accuracy'] * 100:.1f}%")
        print(f"\n{'=' * 60}")
        print("  Combined model (all leagues)")
        print(f"{'=' * 60}")
        result = train_model(
            historical_path=Path(args.historical),
            output_path=Path(args.output),
            random_state=args.random_state,
        )
    elif args.league_id is not None:
        result = train_model(
            historical_path=Path(args.historical),
            random_state=args.random_state,
            league_id=args.league_id,
        )
    else:
        result = train_model(
            historical_path=Path(args.historical),
            output_path=Path(args.output),
            random_state=args.random_state,
        )

    if result.get("skipped"):
        print(f"\nSkipped: {result.get('reason')}")
        return 0
    print(f"\nDone. Test accuracy: {result['accuracy'] * 100:.1f}%")
    print(f"Clean dataset : {result['dataset']}")
    print(f"Saved model   : {result['model_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
