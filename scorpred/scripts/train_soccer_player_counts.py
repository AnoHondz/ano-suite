"""Train and freeze source-aware, chronological soccer player count models."""
from collections import defaultdict, deque
from datetime import UTC, datetime
import hashlib
from itertools import groupby
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")

import numpy as np
from scipy.stats import nbinom, poisson
from sklearn.linear_model import PoissonRegressor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SCORPRED_PROPS_TRAINING"] = "1"
from services import soccer_player_model as model

WORK = ROOT / ".tmp/soccer-training-v2"
DATA = ROOT / "data/processed/soccer_props_v2/games"
THRESHOLDS = {"shots": [.5, 1.5, 2.5, 3.5], "shots_on_target": [.5, 1.5, 2.5],
              "goals": [.5, 1.5], "assists": [.5, 1.5], "fouls": [.5, 1.5, 2.5],
              "saves": [1.5, 2.5, 3.5, 4.5], "tackles": [.5, 1.5, 2.5, 3.5]}


def checked_game(game):
    """Deduplicate identical source rows; quarantine conflicting or unknown identities."""
    seen, teams, repairs = set(), {}, 0
    for tid, team in game["teams"].items():
        players = {}
        for player in team["players"]:
            pid = str(player.get("id", ""))
            if pid in {"", "0", "None"} or not 0 < player.get("minutes", 0) <= 120:
                raise ValueError("Unknown athlete identity or invalid minutes")
            if pid in players:
                if players[pid] != player: raise ValueError("Conflicting duplicate athlete observation")
                repairs += 1
                continue
            if pid in seen: raise ValueError("Same athlete on both teams in one fixture")
            players[pid] = player
        seen.update(players)
        teams[tid] = {**team, "players": list(players.values())}
    return {**game, "teams": teams}, repairs


def dataset():
    games, hashes, quarantined, duplicate_rows = [], [], [], 0
    for path in DATA.glob("*/*.json"):
        game = json.loads(path.read_text(encoding="utf-8"))
        if game.get("date") and game["date"] < datetime.now(UTC).isoformat():
            try:
                game, repaired = checked_game(game)
                duplicate_rows += repaired
            except ValueError as exc:
                quarantined.append({"file": str(path.relative_to(DATA)), "reason": str(exc)})
                continue
            games.append(game)
            hashes.append((str(path.relative_to(DATA)), hashlib.sha256(path.read_bytes()).hexdigest()))
    games.sort(key=lambda g: g["date"])
    histories = defaultdict(lambda: deque(maxlen=40))
    totals = defaultdict(lambda: deque(maxlen=8))
    rows = defaultdict(list)
    by_source = defaultdict(int)
    for index, (day, daily) in enumerate(groupby(games, lambda g: g["date"][:10])):
        daily = list(daily)
        for game in daily:
            namespace = (game["source"], game["league"])
            for team_id, team in game["teams"].items():
                opponent = str(team["opponent"])
                for player in team["players"]:
                    if player.get("minutes", 0) <= 0: continue
                    history = list(histories[(*namespace, str(player["id"]))])
                    for market in model.MARKETS:
                        if not model.known(player, market): continue
                        if market == "saves" and player["position"] != "G": continue
                        if market != "saves" and player["position"] == "G": continue
                        part = model.inputs(history, market, player["position"],
                            list(totals[(*namespace, opponent, market)]), list(totals[(*namespace, str(team_id), market)]),
                            team["home"], game["date"])
                        if part:
                            rows[market].append((part["x"], player[market], part["baseline"], day, game["source"], game["league"]))
                            by_source[game["source"]] += 1
        # No outcome from the target date is available to another target on that date.
        for game in daily:
            namespace = (game["source"], game["league"])
            for tid, team in game["teams"].items():
                for player in team["players"]:
                    histories[(*namespace, str(player["id"]))].append({**player, "date": game["date"]})
                for market in model.MARKETS:
                    if team["players"] and all(model.known(p, market) for p in team["players"]):
                        totals[(*namespace, str(tid), market)].append(sum(p[market] for p in team["players"]))
        if index % 100 == 0: print(f"Feature dates: {index}; examples: {sum(map(len, rows.values()))}", flush=True)
    arrays = {}
    for market, items in rows.items():
        arrays[market + "_x"] = np.array([r[0] for r in items], dtype=np.float64)
        arrays[market + "_y"] = np.array([r[1] for r in items], dtype=np.float64)
        arrays[market + "_base"] = np.array([r[2] for r in items], dtype=np.float64)
        arrays[market + "_date"] = np.array([r[3] for r in items])
        arrays[market + "_source"] = np.array([r[4] for r in items])
        arrays[market + "_league"] = np.array([r[5] for r in items])
    np.savez_compressed(WORK / "count_features.npz", **arrays)
    manifest = {"games": len(games), "player_market_examples": sum(map(len, rows.values())), "examples_by_source": dict(by_source),
                "quarantined_games": quarantined, "identical_duplicate_rows_removed": duplicate_rows,
                "quality_policy": "Exact duplicate removal; unknown IDs and conflicting observations quarantined before fitting",
                "sha256": hashlib.sha256(json.dumps(sorted(hashes)).encode()).hexdigest(),
                "policy": "All observed games, no target-game subsampling; source/league/player IDs isolated; earlier dates only"}
    (WORK / "count_features.manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return arrays, manifest


def dispersion(y, mu, weights=None):
    weights = np.ones(len(y)) if weights is None else weights
    return float(np.clip(np.sum(weights * ((y - mu) ** 2 - mu)) / max(1, np.sum(weights * mu ** 2)), 0, 2))


def metrics(y, mu, alpha, thresholds):
    probabilities = model.survival(mu[:, None], alpha, np.floor(thresholds)[None, :])
    observed = y[:, None] > np.array(thresholds)[None, :]
    probability = np.clip(probabilities, 1e-6, 1 - 1e-6)
    likelihood = poisson.logpmf(y, mu) if alpha < 1e-6 else nbinom.logpmf(y, 1 / alpha, 1 / (1 + alpha * mu))
    output = {"n": len(y), "mae": float(np.mean(abs(y - mu))), "bias": float(np.mean(mu - y)),
              "count_nll": float(-np.mean(likelihood)), "brier": float(np.mean((probability - observed) ** 2)),
              "binary_log_loss": float(np.mean(-observed.astype(float) * np.log(probability) - (~observed).astype(float) * np.log1p(-probability))),
              "thresholds": {}}
    for i, line in enumerate(thresholds):
        output["thresholds"][str(line)] = {"predicted_over": float(np.mean(probability[:, i])),
            "actual_over": float(np.mean(observed[:, i])), "brier": float(np.mean((probability[:, i] - observed[:, i]) ** 2))}
    return output


def train(arrays, manifest):
    artifact, report = {}, {}
    for market in model.MARKETS:
        if market + "_x" not in arrays: continue
        x, y, baseline, dates, sources = [arrays[market + "_" + field] for field in ("x", "y", "base", "date", "source")]
        domain = "fpl" if market == "tackles" else "espn"
        days = sorted(set(dates[sources == domain]))
        if len(days) < 20:
            report[market] = {"status": "insufficient_primary_source"}; continue
        first, second = days[int(len(days) * .6)], days[int(len(days) * .8)]
        train_mask, valid, test = dates < first, (dates >= first) & (dates < second) & (sources == domain), (dates >= second) & (sources == domain)
        if min(train_mask.sum(), valid.sum(), test.sum()) < 150:
            report[market] = {"status": "insufficient_split_sample"}; continue
        mean, scale = x[train_mask].mean(axis=0), x[train_mask].std(axis=0)
        scale[scale < 1e-8] = 1
        standardized = (x - mean) / scale
        base_alpha = dispersion(y[train_mask & (sources == domain)], baseline[train_mask & (sources == domain)])
        baseline_valid = metrics(y[valid], baseline[valid], base_alpha, THRESHOLDS[market])
        baseline_test = metrics(y[test], baseline[test], base_alpha, THRESHOLDS[market])
        candidates = []
        for pool in ("primary_only", "multiple_sources"):
            mask = train_mask & (sources == domain) if pool == "primary_only" else train_mask
            # Never reinterpret fantasy assists as official assists or event duels as FPL tackles.
            if market == "assists": mask &= sources != "fpl"
            if market == "tackles": mask &= sources == "fpl"
            weights = np.array([1 if source == domain else .25 for source in sources[mask]])
            for penalty in (.03, .3, 3):
                fitted = PoissonRegressor(alpha=penalty, max_iter=300, tol=1e-6)
                fitted.fit(standardized[mask], y[mask], sample_weight=weights)
                fitted_alpha = dispersion(y[mask], fitted.predict(standardized[mask]), weights)
                score = metrics(y[valid], fitted.predict(standardized[valid]), fitted_alpha, THRESHOLDS[market])
                candidates.append((score["count_nll"] + score["binary_log_loss"], fitted, fitted_alpha,
                    {"pool": pool, "regularization": penalty, "training_n": int(mask.sum()), "validation": score}))
        _, fitted, fitted_alpha, selected = min(candidates, key=lambda row: row[0])
        selected = dict(selected)
        test_score = metrics(y[test], fitted.predict(standardized[test]), fitted_alpha, THRESHOLDS[market])
        selected.update(test=test_score, baseline_validation=baseline_valid, baseline_test=baseline_test,
                        split_dates={"train_before": first, "test_from": second, "test_end": max(dates[test])})
        # Freeze one validation-selected model, inspect its test once. Do not retune against test failures.
        passed = all(selected[name]["brier"] < selected[base]["brier"] and
                     selected[name]["count_nll"] < selected[base]["count_nll"] and
                     selected[name]["mae"] <= selected[base]["mae"] * 1.02
                     for name, base in (("validation", "baseline_validation"), ("test", "baseline_test")))
        selected["status"] = "accepted" if passed else "rejected"
        selected["candidates"] = [row[3] for row in candidates]
        report[market] = selected
        if passed:
            artifact[market] = {"mean": mean.tolist(), "scale": scale.tolist(), "coef": fitted.coef_.tolist(),
                "intercept": float(fitted.intercept_), "alpha": fitted_alpha,
                "training_end": first, "test": test_score, "pool": selected["pool"]}
        print(f"{market}: {selected['status']}; test Brier {baseline_test['brier']:.4f} -> {test_score['brier']:.4f}", flush=True)
    common = {"version": model.VERSION, "created_at": datetime.now(UTC).isoformat(), "features": list(model.FEATURES), "data": manifest,
        "policy": "Past-only features; six fixed source/regularization candidates; validation selection; one chronological test gate",
        "limitations": ["Count-stat validation is not sportsbook return validation", "Conditional on participation; DNPs excluded",
            "No timestamped historical odds archive", "Provider definitions differ; fantasy assists and tackle definitions are not pooled",
            "Old ESPN compact caches may contain legacy zeros; new parsing preserves missing values",
            "StatsBomb Open Data attribution required under publisher terms; Wyscout: Pappalardo et al. 2019, CC BY 4.0"]}
    for target, body in ((ROOT / "data/models" / model.ARTIFACT, {**common, "markets": artifact}),
                         (ROOT / "data/validation/soccer_player_counts_v2.json", {**common, "markets": report})):
        temporary = target.with_suffix(".new.json")
        temporary.write_text(json.dumps(body, indent=2), encoding="utf-8")
        temporary.replace(target)
    print(json.dumps({"accepted": list(artifact), "examples": manifest["player_market_examples"]}), flush=True)


if __name__ == "__main__":
    WORK.mkdir(parents=True, exist_ok=True)
    if "--fit-only" in sys.argv:
        train(np.load(WORK / "count_features.npz"), json.loads((WORK / "count_features.manifest.json").read_text()))
    else:
        train(*dataset())
