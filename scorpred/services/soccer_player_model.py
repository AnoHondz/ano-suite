"""Soccer count models trained on strictly prior player and team observations."""
from functools import lru_cache
from datetime import datetime
import json
import math
import os
from statistics import mean, median, pstdev
import numpy as np
from scipy.stats import nbinom, poisson
from runtime_paths import model_dir

VERSION = "soccer-player-counts-v2"
ARTIFACT = "soccer_player_counts_v2.json"
MARKETS = ("shots", "shots_on_target", "goals", "assists", "fouls", "saves", "tackles")
FEATURES = ("log_expected_count", "log_rate90", "log_last5", "log_last15", "minutes5", "minutes_last",
            "minutes_spread", "starter_share", "log_games", "home", "rest", "log_opponent_allowed",
            "opponent_missing", "log_team_total", "goalkeeper", "defender", "midfielder", "forward")


def known(row, market):
    value = row.get(market)
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def inputs(history, market, position, opponent_totals, team_totals, home, kickoff):
    history = [r for r in history if r.get("date", "")[:10] < kickoff[:10]]
    if not history: return None
    recent = history[-5:]
    weights = np.power(.5, np.arange(len(recent) - 1, -1, -1) / 12)
    minutes = float(np.average([min(90, r["minutes"]) for r in recent], weights=weights))
    rows = [r for r in history[-40:] if known(r, market) and r.get("minutes", 0) > 0]
    role = [r for r in rows if r["minutes"] >= .7 * minutes]
    if minutes < 45 or len(role) < 8: return None
    weights = np.power(.5, np.arange(len(rows) - 1, -1, -1) / 12)
    rate90 = 90 * float(np.dot(weights, [r[market] for r in rows])) / float(np.dot(weights, [r["minutes"] for r in rows]))
    baseline = max(.01, rate90 * minutes / 90)
    values = [r[market] for r in role[-15:]]
    scaled = [r[market] * minutes / r["minutes"] for r in role[-15:]]
    line = math.floor(median(scaled)) + .5
    record = (sum(v > line for v in scaled) + 1) / (len(scaled) + 2)
    try:
        rest = (datetime.fromisoformat(kickoff.replace("Z", "+00:00")) -
                datetime.fromisoformat(history[-1]["date"].replace("Z", "+00:00"))).total_seconds() / 86400
    except (ValueError, KeyError): rest = 7
    mins = [min(90, r["minutes"]) for r in recent]
    x = [math.log(baseline), math.log1p(rate90), math.log1p(mean(r[market] for r in rows[-5:])),
         math.log1p(mean(values)), minutes / 90, mins[-1] / 90, pstdev(mins) / 90,
         mean(bool(r.get("starter")) for r in recent), math.log1p(len(rows)), float(home),
         max(0, min(21, rest)) / 14, math.log1p(mean(opponent_totals[-8:])) if opponent_totals else 0,
         float(not opponent_totals), math.log1p(mean(team_totals[-8:])) if team_totals else 0,
         *[float(position == p) for p in ("G", "D", "M", "F")]]
    return {"x": x, "baseline": baseline, "minutes": minutes, "line": line,
            "record_over": record, "games": len(values)}


@lru_cache(maxsize=3)
def _load(path, modified):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if value.get("version") == VERSION and value.get("features") == list(FEATURES) else {}
    except (OSError, ValueError): return {}


def artifact():
    if os.getenv("SCORPRED_TESTING") == "1" or os.getenv("SCORPRED_PROPS_TRAINING") == "1": return {}
    path = model_dir() / ARTIFACT
    return _load(path, path.stat().st_mtime_ns) if path.exists() else {}


def mean_prediction(x, model):
    features = (np.asarray(x) - model["mean"]) / model["scale"]
    return np.exp(np.clip(features @ np.asarray(model["coef"]) + model["intercept"], -9, 4))


def survival(mu, alpha, thresholds):
    return poisson.sf(thresholds, mu) if alpha < 1e-6 else nbinom.sf(thresholds, 1 / alpha, 1 / (1 + alpha * mu))


def reprice(market, data, fitted):
    mu = float(mean_prediction(data["x"], fitted))
    over = float(survival(mu, fitted["alpha"], math.floor(data["line"])))
    side = "over" if over >= .5 else "under"
    return {**market, "projection": round(mu, 2), "minutes": round(data["minutes"], 1),
        "line": data["line"], "side": side, "probability": round(over if side == "over" else 1 - over, 4),
        "record": round(data["record_over"] if side == "over" else 1 - data["record_over"], 4),
        "games": data["games"], "model": VERSION, "calibration": None,
        "ladder": {"start": 0, "over": [round(float(p), 5) for p in survival(mu, fitted["alpha"], np.arange(max(16, int(mu * 3) + 8)))]}}


def apply_to_players(players, games, home_id, away_id, kickoff):
    fitted = artifact()
    if not fitted.get("markets"): return players
    games = [g for g in games if g.get("date", "")[:10] < kickoff.date().isoformat()]
    from services.prop_model import player_histories
    histories = player_histories(games)
    totals = {}
    for game in sorted(games, key=lambda g: g["date"]):
        for team_id, team in game["teams"].items():
            for market in MARKETS:
                rows = team["players"]
                if rows and all(known(r, market) for r in rows):
                    totals.setdefault((str(team_id), market), []).append(sum(r[market] for r in rows))
    for player in players:
        team = str(player["team"])
        opponent = str(away_id) if team == str(home_id) else str(home_id)
        for index, market in enumerate(player["markets"]):
            key = market["market"]
            model = fitted["markets"].get(key)
            if not model or model.get("enabled") is False: continue
            data = inputs(histories.get(player["id"], []), key, player["position"],
                          totals.get((opponent, key), []), totals.get((team, key), []), team == str(home_id), kickoff.isoformat())
            if data is None: continue
            player["markets"][index] = reprice(market, data, model)
    return players
