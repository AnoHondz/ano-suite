"""
nba_train_model.py — Train a calibrated Stacking Ensemble for NBA home/away win prediction.

Features per game (computed from rolling history, leakage-free):

    Performance – last 10 games:
        home_net_rating_last10, away_net_rating_last10
        home_off_rtg_last10, away_off_rtg_last10
        home_def_rtg_last10, away_def_rtg_last10
        home_win_pct_last10, away_win_pct_last10

    Recent form – last 5 games:
        home_win_pct_last5, away_win_pct_last5
        home_net_rating_last5, away_net_rating_last5

    Rest / fatigue:
        rest_days_home, rest_days_away
        is_back_to_back_home, is_back_to_back_away

    Head-to-head:
        h2h_home_win_rate

    ELO ratings (pre-match, leakage-safe):
        home_elo, away_elo, elo_diff

    Scoring consistency (std dev of points scored – lower = more reliable):
        home_scoring_consistency, away_scoring_consistency

    Derived comparison features:
        pace_diff, net_rating_diff, win_pct_diff_10

Target: 1 = home win, 0 = away win (2-class)

Chronological 60/20/20 split → calibrated StackingClassifier → saved to nba_model_path().
"""

from __future__ import annotations

import argparse
import collections
import time
from datetime import date
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier, StackingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss

try:
    from xgboost import XGBClassifier
except ImportError:
    XGBClassifier = None

try:
    from lightgbm import LGBMClassifier
except ImportError:
    LGBMClassifier = None

from runtime_paths import nba_model_path
from nba_client import NBA_SEASON, get_schedule, get_teams

NBA_FEATURE_COLUMNS = [
    # Performance – last 10
    "home_net_rating_last10",
    "away_net_rating_last10",
    "home_off_rtg_last10",
    "away_off_rtg_last10",
    "home_def_rtg_last10",
    "away_def_rtg_last10",
    "home_win_pct_last10",
    "away_win_pct_last10",
    # Recent form – last 5
    "home_win_pct_last5",
    "away_win_pct_last5",
    "home_net_rating_last5",
    "away_net_rating_last5",
    # Rest / fatigue
    "rest_days_home",
    "rest_days_away",
    "is_back_to_back_home",
    "is_back_to_back_away",
    # H2H
    "h2h_home_win_rate",
    # ELO
    "home_elo",
    "away_elo",
    "elo_diff",
    # Scoring consistency
    "home_scoring_consistency",
    "away_scoring_consistency",
    # Derived comparisons
    "pace_diff",
    "net_rating_diff",
    "win_pct_diff_10",
]

NBA_CLASS_LABELS = {0: "AwayWin", 1: "HomeWin"}
_MIN_HISTORY = 5

ELO_BASE = 1500.0
ELO_K    = 20.0


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except (ValueError, TypeError):
        return None


def _rolling(history: list[dict], window: int = 10) -> dict[str, float]:
    recent = history[-window:] if history else []
    if not recent:
        return {"net_rtg": 0.0, "win_pct": 0.5, "avg_pts": 110.0, "avg_pts_against": 110.0, "pts_std": 0.0}
    n = float(len(recent))
    net = sum(r["net"] for r in recent) / n
    win_pct = sum(1.0 for r in recent if r["won"]) / n
    avg_pts = sum(r["pts_for"] for r in recent) / n
    avg_pts_against = sum(r["pts_against"] for r in recent) / n
    pts_list = [r["pts_for"] for r in recent]
    pts_std = float(np.std(pts_list)) if len(pts_list) >= 2 else 0.0
    return {
        "net_rtg": round(net, 2),
        "win_pct": round(win_pct, 4),
        "avg_pts": round(avg_pts, 2),
        "avg_pts_against": round(avg_pts_against, 2),
        "pts_std": round(pts_std, 2),
    }


def _elo_expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def _elo_update(ra: float, rb: float, won: bool, margin: int = 0) -> float:
    expected = _elo_expected(ra, rb)
    actual = 1.0 if won else 0.0
    # Margin-of-victory multiplier (capped)
    mov = min(abs(margin), 30) / 30.0 * 0.5 + 1.0
    return ra + ELO_K * mov * (actual - expected)


def _collect_all_games(seasons: list[int]) -> list[dict]:
    """Fetch all finished games for all teams across given seasons.

    Returns deduplicated list sorted by date ascending.
    Falls back gracefully if API unavailable.
    """
    seen: dict[str | int, dict] = {}
    teams = []
    try:
        teams = get_teams()
    except Exception as e:
        print(f"  [warn] Could not fetch NBA teams: {e}")
        return []

    print(f"  Found {len(teams)} NBA teams")
    for team in teams:
        team_id = team.get("id")
        if not team_id:
            continue
        for season in seasons:
            try:
                games = get_schedule(int(team_id), season)
                for g in games:
                    gid = g.get("id")
                    if gid and gid not in seen:
                        status = str((g.get("status") or {}).get("long") or "").lower()
                        if "finish" in status or status == "final":
                            seen[gid] = g
            except Exception as e:
                print(f"    [warn] team={team_id} season={season}: {e}")
            time.sleep(0.05)

    games = list(seen.values())
    games.sort(key=lambda g: str((g.get("date") or {}).get("start") or ""))
    return games


def build_nba_features(seasons: list[int]) -> list[dict[str, Any]]:
    """Build leakage-free feature rows from NBA game history."""
    all_games = _collect_all_games(seasons)
    if not all_games:
        raise ValueError("No NBA game data fetched — check NBA_API_KEY or API availability.")

    print(f"  Processing {len(all_games)} unique finished games")

    team_history: dict[str | int, list[dict]] = collections.defaultdict(list)
    h2h_records:  dict[tuple, list[dict]]      = collections.defaultdict(list)
    last_dates:   dict[str | int, date]         = {}
    elo_ratings:  dict[str | int, float]        = collections.defaultdict(lambda: ELO_BASE)
    processed: list[dict[str, Any]]             = []

    for g in all_games:
        teams  = g.get("teams") or {}
        scores = g.get("scores") or {}

        home_id = (teams.get("home") or {}).get("id")
        away_id = (teams.get("visitors") or teams.get("away") or {}).get("id")
        if not home_id or not away_id:
            continue

        home_pts = (scores.get("home") or {}).get("points")
        away_pts = (scores.get("visitors") or scores.get("away") or {}).get("points")
        if home_pts is None or away_pts is None:
            continue

        try:
            home_pts = int(home_pts)
            away_pts = int(away_pts)
        except (TypeError, ValueError):
            continue

        raw_date = (g.get("date") or {}).get("start") or ""
        game_date = _parse_date(raw_date)
        home_won  = home_pts > away_pts

        h_hist = team_history[home_id]
        a_hist = team_history[away_id]

        if len(h_hist) >= _MIN_HISTORY and len(a_hist) >= _MIN_HISTORY:
            h_roll10 = _rolling(h_hist, 10)
            a_roll10 = _rolling(a_hist, 10)
            h_roll5  = _rolling(h_hist, 5)
            a_roll5  = _rolling(a_hist, 5)

            # Rest days (capped at 7)
            def _rest(team_id_: Any) -> float:
                if game_date and team_id_ in last_dates:
                    return min(float(max(0, (game_date - last_dates[team_id_]).days)), 7.0)
                return 3.0

            rest_home = _rest(home_id)
            rest_away = _rest(away_id)

            # H2H
            prior_h2h = h2h_records[(home_id, away_id)]
            h2h_win_rate = (
                sum(1 for r in prior_h2h if r["won"]) / len(prior_h2h)
                if prior_h2h else 0.5
            )

            # Pre-match ELO (read BEFORE update)
            h_elo = elo_ratings[home_id]
            a_elo = elo_ratings[away_id]

            processed.append({
                # Performance – last 10
                "home_net_rating_last10":  h_roll10["net_rtg"],
                "away_net_rating_last10":  a_roll10["net_rtg"],
                "home_off_rtg_last10":     h_roll10["avg_pts"],
                "away_off_rtg_last10":     a_roll10["avg_pts"],
                "home_def_rtg_last10":     h_roll10["avg_pts_against"],
                "away_def_rtg_last10":     a_roll10["avg_pts_against"],
                "home_win_pct_last10":     h_roll10["win_pct"],
                "away_win_pct_last10":     a_roll10["win_pct"],
                # Recent form – last 5
                "home_win_pct_last5":      h_roll5["win_pct"],
                "away_win_pct_last5":      a_roll5["win_pct"],
                "home_net_rating_last5":   h_roll5["net_rtg"],
                "away_net_rating_last5":   a_roll5["net_rtg"],
                # Rest / fatigue
                "rest_days_home":          rest_home,
                "rest_days_away":          rest_away,
                "is_back_to_back_home":    1.0 if rest_home <= 1 else 0.0,
                "is_back_to_back_away":    1.0 if rest_away <= 1 else 0.0,
                # H2H
                "h2h_home_win_rate":       round(h2h_win_rate, 4),
                # ELO
                "home_elo":                round(h_elo, 1),
                "away_elo":                round(a_elo, 1),
                "elo_diff":                round(h_elo - a_elo, 1),
                # Scoring consistency
                "home_scoring_consistency": h_roll10["pts_std"],
                "away_scoring_consistency": a_roll10["pts_std"],
                # Derived comparisons
                "pace_diff":               round(h_roll10["avg_pts"] - a_roll10["avg_pts"], 2),
                "net_rating_diff":         round(h_roll10["net_rtg"] - a_roll10["net_rtg"], 2),
                "win_pct_diff_10":         round(h_roll10["win_pct"] - a_roll10["win_pct"], 4),
                "target":                  1 if home_won else 0,
            })

        # Update histories AFTER feature extraction (leakage-free)
        h_net = home_pts - away_pts
        a_net = away_pts - home_pts
        team_history[home_id].append({"net": h_net, "won": home_won,   "pts_for": float(home_pts), "pts_against": float(away_pts)})
        team_history[away_id].append({"net": a_net, "won": not home_won, "pts_for": float(away_pts), "pts_against": float(home_pts)})
        h2h_records[(home_id, away_id)].append({"won": home_won})
        h2h_records[(away_id, home_id)].append({"won": not home_won})

        # Update ELO AFTER feature extraction
        margin = home_pts - away_pts
        elo_ratings[home_id] = _elo_update(h_elo, a_elo, home_won, margin)
        elo_ratings[away_id] = _elo_update(a_elo, h_elo, not home_won, -margin)

        if game_date:
            last_dates[home_id] = game_date
            last_dates[away_id] = game_date

    return processed


# ── Training ───────────────────────────────────────────────────────────────────

def train_nba_model(
    seasons: list[int] | None = None,
    output_path: Path | None = None,
    random_state: int = 42,
) -> dict[str, Any]:
    seasons = seasons or [NBA_SEASON - 1, NBA_SEASON]
    print(f"Building NBA features for seasons: {seasons}")

    rows = build_nba_features(seasons)
    if len(rows) < 20:
        raise ValueError(
            f"Only {len(rows)} usable NBA rows after feature engineering (need ≥ 20)."
        )

    X = np.array([[float(r.get(f, 0.0)) for f in NBA_FEATURE_COLUMNS] for r in rows])
    y = np.array([int(r["target"]) for r in rows])

    # Class balance
    counts = collections.Counter(y.tolist())
    total  = len(y)
    print(f"\nClass balance: HomeWin={counts[1]} ({counts[1]/total*100:.1f}%)  AwayWin={counts[0]} ({counts[0]/total*100:.1f}%)")

    # Chronological 60/20/20 split
    n_test = max(1, int(total * 0.20))
    n_cal  = max(1, int(total * 0.20))
    n_train = total - n_test - n_cal
    X_train, y_train = X[:n_train],              y[:n_train]
    X_cal,   y_cal   = X[n_train:n_train+n_cal], y[n_train:n_train+n_cal]
    X_test,  y_test  = X[n_train+n_cal:],        y[n_train+n_cal:]
    print(f"Split: {len(X_train)} train / {len(X_cal)} cal / {len(X_test)} test")

    # Build stacking ensemble (LR + RF + XGBoost + LightGBM)
    estimators = [
        ("lr",  LogisticRegression(max_iter=1000, solver="lbfgs", random_state=random_state)),
        ("rf",  RandomForestClassifier(n_estimators=300, max_depth=8, min_samples_leaf=3, random_state=random_state)),
    ]
    if XGBClassifier is not None:
        estimators.append(
            ("xgb", XGBClassifier(
                n_estimators=200, max_depth=5, learning_rate=0.08,
                subsample=0.8, colsample_bytree=0.8,
                eval_metric="logloss", random_state=random_state,
            ))
        )
    if LGBMClassifier is not None:
        estimators.append(
            ("lgbm", LGBMClassifier(
                n_estimators=200, max_depth=5, learning_rate=0.08,
                subsample=0.8, colsample_bytree=0.8,
                verbose=-1, random_state=random_state,
            ))
        )

    base_model_names = [name for name, _ in estimators]
    stacker = StackingClassifier(
        estimators=estimators,
        final_estimator=LogisticRegression(max_iter=500, solver="lbfgs", random_state=random_state),
        cv=5,
        passthrough=False,
    )
    stacker.fit(X_train, y_train)

    raw_brier = float(sum(
        brier_score_loss([1 if yi == c else 0 for yi in y_test], [p[c] for p in stacker.predict_proba(X_test)])
        for c in range(2)
    ) / 2)

    # Isotonic calibration
    cal_model = CalibratedClassifierCV(stacker, method="isotonic", cv="prefit")
    cal_model.fit(X_cal, y_cal)

    accuracy = float(accuracy_score(y_test, cal_model.predict(X_test)))
    cal_brier = float(sum(
        brier_score_loss([1 if yi == c else 0 for yi in y_test], [p[c] for p in cal_model.predict_proba(X_test)])
        for c in range(2)
    ) / 2)

    print(f"\nTest accuracy : {accuracy * 100:.1f}%  (random baseline ≈ 50%)")
    print(f"Raw Brier     : {raw_brier:.3f} | Calibrated Brier: {cal_brier:.3f}")
    print(f"Base models   : {base_model_names}")

    save_path = output_path or nba_model_path()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "model":              cal_model,
        "model_type":         "stacking_ensemble",
        "sport":              "nba",
        "feature_names":      NBA_FEATURE_COLUMNS,
        "class_labels":       NBA_CLASS_LABELS,
        "accuracy":           accuracy,
        "brier_score":        cal_brier,
        "brier_score_raw":    raw_brier,
        "calibrated":         True,
        "calibration_method": "isotonic",
        "base_models":        base_model_names,
        "seasons":            seasons,
    }
    joblib.dump(bundle, save_path)
    print(f"Saved NBA model → {save_path}")
    return bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train NBA home/away win classifier.")
    parser.add_argument("--seasons", nargs="+", type=int, default=None,
                        help="Season years to use (default: last 2 seasons)")
    parser.add_argument("--output", default=None, help="Output path for model pkl.")
    args = parser.parse_args()
    train_nba_model(
        seasons=args.seasons,
        output_path=Path(args.output) if args.output else None,
    )
