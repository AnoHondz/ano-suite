"""Train and walk-forward test the learned NBA prop model against the rule model.

Data: full ESPN box scores from scripts/fetch_nba_history.py. Each test season is predicted
by a model trained only on earlier seasons. The rule model (services.prop_model, raw
probabilities, no published calibration) is replayed on the same games with exactly the
history the live endpoint sees, and both are scored at the rule model's own line for each
player and market. A market is promoted only when the learned model has lower log loss and
Brier score in every test season. The published model is then refit on all completed games.

Usage: python scripts/train_nba_props_ml.py [--test-seasons 2025 2026]
"""
import argparse
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.special import gammaln

ROOT = Path(__file__).resolve().parents[1]
os.environ["SCORPRED_PROPS_TRAINING"] = "1"  # raw rule-model probabilities; calibration was fit on these seasons
sys.path.insert(0, str(ROOT))
import lightgbm as lgb  # noqa: E402

from runtime_paths import cache_dir, model_dir  # noqa: E402
from services import nba_props, prop_model  # noqa: E402
from services import nba_props_ml as ml  # noqa: E402

PARAMS = {"objective": "poisson", "learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 100,
          "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
          "verbose": -1, "seed": 7, "num_threads": 4}
COMPACT = ("id", "name", "position", "starter", "minutes", "points", "rebounds", "assists", "threes", "steals",
           "blocks", "turnovers")


def load_games():
    games, digest = [], hashlib.sha256()
    for path in sorted(cache_dir("nba_history").glob("*.json")):
        game = json.loads(path.read_text(encoding="utf-8"))
        if game.get("date") and game.get("season"):
            games.append(game)
            digest.update(path.read_bytes())
    games.sort(key=lambda g: g["date"])
    return games, digest.hexdigest()


def build_rows(games):
    history, rows = ml.History(), []
    for index, game in enumerate(games):
        for team_id, team in game["teams"].items():
            for player in team["players"]:
                features = history.features(player["id"], team_id, game)
                if features is None:
                    continue
                targets = {market: sum(ml._num(player, f) for f in fields) for market, fields in ml.MARKETS.items()}
                rows.append({"season": game["season"], "date": game["date"], "game": game["id"], "team": team_id,
                             "player": player["id"], **targets, **{name: features.get(name) for name in ml.FEATURES}})
        history.update(game)
        if index % 1000 == 0:
            print(f"features: {index}/{len(games)} games, {len(rows)} rows", flush=True)
    frame = pd.DataFrame(rows)
    frame[ml.FEATURES] = frame[ml.FEATURES].astype(float)
    return frame


def nb_loglik(y, mu, alpha):
    r = 1 / alpha
    return np.sum(gammaln(y + r) - gammaln(r) - gammaln(y + 1) + r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu)))


def fit_alpha(y, mu):
    mu = np.maximum(mu, 0.02)
    result = minimize_scalar(lambda a: -nb_loglik(y, mu, a), bounds=(1e-4, 3.0), method="bounded")
    return float(result.x)


def train(frame, market, rounds=None):
    """Poisson GBM; early-stopped on the chronologically last 15% when ``rounds`` is None."""
    frame = frame.sort_values("date")
    if rounds is None:
        cut = int(len(frame) * 0.85)
        fit, valid = frame.iloc[:cut], frame.iloc[cut:]
        booster = lgb.train(PARAMS, lgb.Dataset(fit[ml.FEATURES], fit[market]), num_boost_round=4000,
                            valid_sets=[lgb.Dataset(valid[ml.FEATURES], valid[market])],
                            callbacks=[lgb.early_stopping(150, verbose=False)])
        alpha = fit_alpha(valid[market].to_numpy(), booster.predict(valid[ml.FEATURES], num_iteration=booster.best_iteration))
        return booster, booster.best_iteration, alpha
    booster = lgb.train(PARAMS, lgb.Dataset(frame[ml.FEATURES], frame[market]), num_boost_round=rounds)
    return booster, rounds, None


def compact(game):
    return {"id": game["id"], "date": game["date"],
            "teams": {tid: {"home": t["home"], "opponent": t["opponent"],
                            "players": [{k: p.get(k) for k in COMPACT} for p in t["players"]]}
                      for tid, t in game["teams"].items()}}


def rule_replay(games, season):
    """The rule model's markets for every player in every game of ``season``, as the live endpoint saw them."""
    pool = [compact(g) for g in games if g["season"] in (season - 1, season)]
    by_team = defaultdict(list)
    for game in pool:
        for team_id in game["teams"]:
            by_team[team_id].append(game)
    out = []
    targets = [g for g in pool if g["date"] >= min(x["date"] for x in games if x["season"] == season)]
    for index, game in enumerate(targets):
        home, away = sorted(game["teams"], key=lambda t: not game["teams"][t]["home"])
        prior = {g["id"]: g for t in (home, away) for g in by_team[t] if g["date"][:10] < game["date"][:10]}
        if not prior:
            continue
        result = prop_model.game_props(nba_props.CONFIG, home, away, list(prior.values()))
        actual = {row["id"]: row for team in game["teams"].values() for row in team["players"]}
        for player in result["players"]:
            row = actual.get(player["id"])
            if row is None:
                continue  # did not play: void
            for market in player["markets"]:
                over = market["probability"] if market["side"] == "over" else 1 - market["probability"]
                record = market["record"] if market["side"] == "over" else 1 - market["record"]
                observed = prop_model.value(row, nba_props.CONFIG["markets"][market["market"]][0])
                out.append({"game": game["id"], "player": player["id"], "team": player["team"], "name": player["name"],
                            "position": player["position"], "market": market["market"], "line": market["line"],
                            "rule_over": over, "record_over": record, "rule_projection": market["projection"],
                            "observed": observed, "y": float(observed > market["line"])})
        if index % 200 == 0:
            print(f"rule replay {season}: {index}/{len(targets)} games, {len(out)} markets", flush=True)
    return pd.DataFrame(out)


def scores(p, y):
    p = np.clip(p, 1e-5, 1 - 1e-5)
    return {"log_loss": float(np.mean(-y * np.log(p) - (1 - y) * np.log1p(-p))), "brier": float(np.mean((p - y) ** 2))}


def picks(frame, column, shrink=1.0):
    """Each game's five best picks (prop_model.best_picks) with ``column`` as the over probability."""
    graded = []
    for _game, rows in frame.groupby("game"):
        players = defaultdict(lambda: {"markets": []})
        for row in rows.itertuples():
            over = getattr(row, column)
            side = "over" if over >= row.record_over else "under"
            players[(row.player, row.team)].update(name=row.name, team=row.team, position=row.position, id=row.player)
            players[(row.player, row.team)]["markets"].append(
                {"market": row.market, "line": row.line, "side": side, "y": row.y,
                 "probability": over if side == "over" else 1 - over,
                 "record": row.record_over if side == "over" else 1 - row.record_over})
        for pick in prop_model.best_picks(list(players.values()), nba_props.CONFIG["pick_markets"], shrink):
            graded.append({"market": pick["market"], "probability": pick["probability"],
                           "hit": float((pick["y"] == 1.0) == (pick["side"] == "over"))})
    return pd.DataFrame(graded)


def evaluate(frame, games, season):
    train_rows, test_rows = frame[frame.season < season], frame[frame.season == season]
    replay = rule_replay(games, season)
    report, predictions = {}, {}
    for market in ml.MARKETS:
        booster, rounds, alpha = train(train_rows, market)
        mu = booster.predict(test_rows[ml.FEATURES], num_iteration=booster.best_iteration)
        predictions[market] = dict(zip(zip(test_rows.game, test_rows.player), mu))
        subset = replay[replay.market == market].copy()
        subset["mu"] = [predictions[market].get((g, p)) for g, p in zip(subset.game, subset.player)]
        subset = subset.dropna(subset=["mu"])
        subset["ml_over"] = [ml.over_probability(m, alpha, line) for m, line in zip(subset.mu, subset.line)]
        y = subset.y.to_numpy()
        report[market] = {
            "rounds": rounds, "alpha": alpha, "n": len(subset),
            "rule": scores(subset.rule_over.to_numpy(), y), "learned": scores(subset.ml_over.to_numpy(), y),
            "player_record": scores(subset.record_over.to_numpy(), y),
            "mean_abs_error": {"rule": float(np.mean(np.abs(subset.rule_projection - subset.observed))),
                               "learned": float(np.mean(np.abs(subset.mu - subset.observed)))},
            "calibration_learned": {"mean_over": float(subset.ml_over.mean()), "actual_over": float(y.mean())},
            "test_alpha": fit_alpha(subset.observed.to_numpy(), subset.mu.to_numpy()),
        }
        replay.loc[subset.index, "ml_over"] = subset.ml_over
        print(f"{season} {market}: n={len(subset)} rule={report[market]['rule']} learned={report[market]['learned']}", flush=True)
    replay = replay.dropna(subset=["ml_over"])
    pick_report = {}
    for column in ("rule_over", "ml_over"):
        graded = picks(replay, column)
        pick_report[column] = {"n": len(graded), "stated": float(graded.probability.mean()), "hit": float(graded.hit.mean()),
                               "by_market": {m: {"n": len(g), "hit": float(g.hit.mean())} for m, g in graded.groupby("market")}}
    return report, pick_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-seasons", type=int, nargs="+", default=[2025, 2026])
    args = parser.parse_args()
    games, data_hash = load_games()
    print(f"{len(games)} games, seasons {sorted({g['season'] for g in games})}", flush=True)
    frame = build_rows(games)
    folds = {season: evaluate(frame, games, season) for season in args.test_seasons}
    artifact = {"version": ml.VERSION, "created_at": datetime.now(UTC).isoformat(), "features": ml.FEATURES,
                "data_sha256": data_hash, "games": len(games), "rows": len(frame),
                "training_end": games[-1]["date"], "markets": {}}
    last = max(args.test_seasons)
    for market in ml.MARKETS:
        results = [folds[s][0][market] for s in args.test_seasons]
        wins = all(r["learned"]["log_loss"] < r["rule"]["log_loss"] and r["learned"]["brier"] < r["rule"]["brier"]
                   for r in results)
        rounds = int(np.mean([r["rounds"] for r in results]))
        booster, _, _ = train(frame, market, rounds)
        artifact["markets"][market] = {"promoted": wins, "rounds": rounds,
                                       "alpha": folds[last][0][market]["test_alpha"],
                                       "model": booster.model_to_string()}
    learned_picks = folds[last][1]["ml_over"]
    artifact["pick_shrink"] = round((learned_picks["hit"] - 0.5) / (learned_picks["stated"] - 0.5), 3)
    model_dir().mkdir(parents=True, exist_ok=True)
    (model_dir() / ml.ARTIFACT).write_text(json.dumps(artifact), encoding="utf-8")
    report = {key: value for key, value in artifact.items() if key != "markets"}
    report["promoted"] = [m for m, spec in artifact["markets"].items() if spec["promoted"]]
    report["folds"] = {str(s): {"markets": folds[s][0], "picks": folds[s][1]} for s in args.test_seasons}
    report["policy"] = ("Walk-forward by season; each test season predicted by a model trained on earlier seasons only. "
                        "Both models scored at the rule model's line (player's recent median). Promotion requires lower "
                        "log loss and Brier in every test season. Not compared with sportsbook lines.")
    (ROOT / "data/validation/nba_props_ml.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"promoted": report["promoted"], "pick_shrink": artifact["pick_shrink"]}), flush=True)


if __name__ == "__main__":
    main()
