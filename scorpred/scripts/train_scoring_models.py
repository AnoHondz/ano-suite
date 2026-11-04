"""Train and evaluate experimental score models, separate from betting decisions.

python -m scripts.train_scoring_models --sports soccer,nba,nfl --sync-nba
"""
import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import numpy as np
import requests
from scipy.optimize import minimize_scalar
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor, Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.preprocessing import StandardScaler

from nfl_model import split_rows
from runtime_paths import cache_dir, repo_root
from services import scoring_research as research
from services.nfl_features import FEATURE_NAMES as NFL_FEATURES
from services.nfl_features import build_features as nfl_features
from services.nflverse_store import metadata
from utils.atomic import atomic_write_json


def sync_nba(start=2022, end=None):
    """Cache complete monthly public ESPN scoreboards; reject truncated responses."""
    now = datetime.now(UTC)
    end = end or now.year
    months = [(year, month) for year in range(start, end + 1) for month in range(1, 13)
              if datetime(year, month, 1, tzinfo=UTC) <= now and month not in (8, 9)]
    def fetch(item):
        year, month = item
        destination = cache_dir("score_models", "nba_raw", f"{year}{month:02}.json")
        if destination.exists() and (year, month) < (now.year, now.month):
            return
        response = requests.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
                                params={"dates": f"{year}{month:02}", "limit": 1000}, timeout=(4, 25))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("events"), list) or len(payload["events"]) >= 1000:
            raise ValueError("Invalid/truncated NBA scoreboard")
        atomic_write_json(destination, payload)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, months))


def soccer_games():
    games, audit, sources = {}, [], []
    for path in sorted((repo_root() / "data" / "raw").glob("E0_*.csv")):
        season = int(path.stem.split("_")[-1])
        with path.open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        prepared = []
        for row in rows:
            if not row.get("FTHG") or not row.get("FTAG"):
                continue
            date = datetime.strptime(row["Date"], "%d/%m/%Y").replace(tzinfo=UTC)
            # The 2019/20 season finished in July after the COVID suspension.
            season_end = datetime(season + 1, 8 if season == 2019 else 7, 1, tzinfo=UTC)
            if not datetime(season, 7, 1, tzinfo=UTC) <= date < season_end:
                audit.append({"file": path.name, "reason": "Date outside labeled season; entire file excluded."})
                prepared = []
                break
            prepared.append({"game_id": f'{date.date()}:{row["HomeTeam"]}:{row["AwayTeam"]}', "season": season,
                             "kickoff": date.isoformat(), "date": date.date().isoformat(), "home_team": row["HomeTeam"], "away_team": row["AwayTeam"],
                             "home_score": int(row["FTHG"]), "away_score": int(row["FTAG"]), "neutral": False})
        if prepared:
            sources.append({"source": str(path.relative_to(repo_root())), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            for game in prepared:
                if game["game_id"] in games and games[game["game_id"]] != game:
                    raise ValueError("Conflicting soccer game records")
                games[game["game_id"]] = game
    return list(games.values()), sources, audit


def nba_games():
    games, sources = {}, []
    for path in sorted(cache_dir("score_models", "nba_raw").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        sources.append({"source": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        for event in payload["events"]:
            if not event.get("status", {}).get("type", {}).get("completed") or event.get("season", {}).get("type") not in (2, 3):
                continue
            competition = event["competitions"][0]
            teams = {team["homeAway"]: team for team in competition["competitors"]}
            home, away = teams["home"], teams["away"]
            if any(not str(team.get("id", "")).isdigit() or not 1 <= int(team["id"]) <= 30 for team in (home, away)):
                continue  # Exclude All-Star and non-NBA exhibition teams.
            hs, aws = research.finite(home.get("score")), research.finite(away.get("score"))
            if hs is None or aws is None or min(hs, aws) <= 0:
                continue
            date = datetime.fromisoformat(event["date"])
            games[event["id"]] = {"game_id": event["id"], "season": event["season"]["year"], "kickoff": date.isoformat(),
                                  "date": date.date().isoformat(), "home_team": str(home["id"]), "away_team": str(away["id"]),
                                  "home_score": hs, "away_score": aws, "neutral": competition.get("neutralSite", False)}
    return list(games.values()), sources, []


def rolling_rows(games, sport):
    history, rows = defaultdict(list), []
    now = datetime.now(UTC)
    for game in sorted(games, key=lambda row: (row["kickoff"], row["game_id"])):
        kickoff = datetime.fromisoformat(game["kickoff"])
        if kickoff >= now:
            continue
        recent = {side: research.select_prior_form(history[(game["season"], game[f"{side}_team"])],
                                                   season=game["season"], before=game["kickoff"], now=now, sport=sport)
                  for side in ("home", "away")}
        if min(map(len, recent.values())) >= research.MIN_FORM_GAMES:
            features = research.rolling_features(recent["home"], recent["away"], is_home=not game["neutral"], sport=sport)
            rows.append({**game, **features, "target": 0 if game["home_score"] > game["away_score"] else 1 if game["home_score"] == game["away_score"] else 2})
        for side, other in (("home", "away"), ("away", "home")):
            history[(game["season"], game[f"{side}_team"])].append({"kickoff": game["kickoff"], "season": game["season"],
                                                               "gf": game[f"{side}_score"], "ga": game[f"{other}_score"]})
    return rows


def fit(sport, rows, sources, audit):
    names = NFL_FEATURES if sport == "nfl" else research.ROLLING_FEATURES
    training, calibration, test = split_rows(rows)
    def matrix(part):
        return np.array([[np.nan if row[name] is None else row[name] for name in names] for row in part], dtype=float)
    def scores(part):
        return np.array([[row["home_score"], row["away_score"]] for row in part], dtype=float)
    imputer = SimpleImputer(strategy="median", keep_empty_features=True).fit(matrix(training))
    scaler = StandardScaler().fit(imputer.transform(matrix(training)))
    x_train, x_cal, x_test = [scaler.transform(imputer.transform(matrix(part))) for part in (training, calibration, test)]
    y_train, y_cal, y_test = [scores(part) for part in (training, calibration, test)]
    targets, cal_predictions, test_predictions = [], [], []
    for index in range(2):
        estimator = PoissonRegressor(alpha=.1, max_iter=2000) if sport == "soccer" else Ridge(alpha=20)
        estimator.fit(x_train, y_train[:, index])
        targets.append({"coef": estimator.coef_.tolist(), "intercept": float(estimator.intercept_)})
        cal_predictions.append(estimator.predict(x_cal).clip(0))
        test_predictions.append(estimator.predict(x_test).clip(0))
    cal_pred, test_pred = np.array(cal_predictions).T, np.array(test_predictions).T
    residuals = y_cal - cal_pred
    # Finite-sample split-conformal quantile, separately for each team score.
    rank = min(len(calibration), math.ceil((len(calibration) + 1) * .8))
    radius = np.sort(np.abs(residuals), axis=0)[rank - 1]
    sigma = np.std(residuals, axis=0, ddof=1).clip(.1)
    rho = 0.0
    if sport == "soccer":
        # Fit only the low-score correlation on the calibration window.
        from services.scoreline_engine import _dc_tau
        def objective(value):
            taus = [_dc_tau(int(y[0]), int(y[1]), p[0], p[1], value) for y, p in zip(y_cal, cal_pred, strict=True)]
            return -sum(math.log(max(tau, 1e-12)) for tau in taus) if min(taus) > 0 else 1e20
        optimized = minimize_scalar(objective, bounds=(-.15, .15), method="bounded")
        if optimized.success:
            rho = float(optimized.x)
    generated = datetime.now(UTC).isoformat()
    model = {"version": 1, "sport": sport, "method": "regularized_poisson_dc_v2" if sport == "soccer" else f"{sport}_ridge_pregame_v2",
             "schema": research.schema(names), "feature_names": names, "generated_at": generated,
             "calibration_end": calibration[-1]["kickoff"], "link": "log" if sport == "soccer" else "identity",
             "fill": imputer.statistics_.tolist(), "center": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(), "targets": targets,
             "radius": radius.tolist(), "sigma": sigma.tolist(), "sigma_total": float(np.std(residuals.sum(axis=1), ddof=1)), "rho": rho}
    if sport == "soccer":
        model["form_policy"] = research.SOCCER_FORM_POLICY
    report = {"generated_at": generated, "sport": sport, "role": "experimental_scoring_only", "decision_engine_changed": False,
              "method": model["method"], "feature_names": names, "schema": model["schema"], "sources": sources, "excluded_sources": audit,
              "dataset_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
              "windows": {name: {"count": len(part), "start": part[0]["date"], "end": part[-1]["date"]} for name, part in zip(("train", "calibration", "test"), (training, calibration, test), strict=True)},
              "metrics": {"points_mae": float(mean_absolute_error(y_test, test_pred)),
                          "baseline_mae": float(mean_absolute_error(y_test, np.tile(y_train.mean(axis=0), (len(test), 1)))),
                          "total_mae": float(mean_absolute_error(y_test.sum(axis=1), test_pred.sum(axis=1))),
                          "interval_coverage": {side: float((np.abs(y_test[:, i] - test_pred[:, i]) <= radius[i]).mean()) for i, side in enumerate(("home", "away"))},
                          "nominal_interval_coverage": .8},
              "references": research.REFERENCES[sport] + [research.UNCERTAINTY_REFERENCE],
              "limitations": ["Retrospective data may contain later corrections; no prospective accuracy claim.",
                              "Time dependence and season shifts can invalidate exchangeability; interval coverage is empirical, not guaranteed.",
                              "No injury, starting-lineup, weather or market-price features.",
                              "Hyperparameters fixed in advance; test window is not used for tuning or refitting.",
                              "NBA model uses scores, not possession-level efficiency or player RAPM.",
                              "Soccer is an adaptation using rolling features, not a reproduction of the full Dixon-Coles team-strength MLE."]}
    if sport == "soccer":
        report["form_policy"] = research.SOCCER_FORM_POLICY
    destination, report_path = research.artifact_paths(sport)
    atomic_write_json(destination, model)
    report["artifact_sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
    atomic_write_json(report_path, report)
    print(json.dumps({"sport": sport, "windows": report["windows"], "metrics": report["metrics"], "excluded": audit}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sports", default="soccer,nba,nfl")
    parser.add_argument("--sync-nba", action="store_true")
    args = parser.parse_args()
    if args.sync_nba:
        sync_nba()
    for sport in args.sports.split(","):
        if sport == "nfl":
            fit(sport, nfl_features(), metadata(), [])
        elif sport in ("soccer", "nba"):
            games, sources, audit = soccer_games() if sport == "soccer" else nba_games()
            fit(sport, rolling_rows(games, sport), sources, audit)
        else:
            raise ValueError("Supported sports: soccer,nba,nfl")


if __name__ == "__main__":
    main()
