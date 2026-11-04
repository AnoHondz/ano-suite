"""Train monotone probability calibration on real, past-only multi-sport prop replays.

No current rosters enter historical fitting. Historical targets are not sportsbook
prices. Evaluation is conditional on participation; DNP outcomes are not fabricated.
"""
import argparse
from collections import defaultdict
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

ROOT = Path(__file__).resolve().parents[1]
os.environ["SCORPRED_PROPS_TRAINING"] = "1"
sys.path.insert(0, str(ROOT))
from services import prop_model, nba_props, soccer_props, nfl_props
from services import nflverse_store as store
from utils.atomic import atomic_write_json


def box_rows(sport, slug, output):
    prefix = f"soccer-{slug}-" if sport == "soccer" else "nba-nba-"
    config = soccer_props.CONFIG if sport == "soccer" else nba_props.CONFIG
    games, files = [], []
    now = datetime.now(UTC).isoformat()
    for path in sorted((ROOT / "cache/box_scores").glob(prefix + "*.json")):
        try:
            game = json.loads(path.read_text(encoding="utf-8"))
            if game.get("date") and game["date"] < now and len(game.get("teams", {})) == 2:
                games.append(game)
                files.append((path.name, hashlib.sha256(path.read_bytes()).hexdigest()))
        except (OSError, ValueError):
            continue
    games.sort(key=lambda g: g["date"])
    by_team = defaultdict(list)
    total_rows = 0
    for index, game in enumerate(games):
        team_ids = list(game["teams"])
        # Earlier calendar dates only: even overlapping kickoffs cannot leak results.
        day = game["date"][:10]
        prior = {g["id"]: g for team in team_ids for g in by_team[team]
                 if g["date"][:10] < day and int(day[:4]) - int(g["date"][:4]) <= 2}
        # Deterministic game subsample limits CPU cost; all earlier observed
        # games still supply history. The stride never depends on outcomes.
        if prior and index % 4 == 0:
            result = prop_model.game_props(config, *team_ids, list(prior.values()))
            actual = {row["id"]: (tid, row) for tid, team in game["teams"].items() for row in team["players"]}
            for player in result["players"]:
                played = actual.get(player["id"])
                if not played or played[0] != player["team"]:
                    continue
                for market in player["markets"]:
                    if market["market"] not in config["pick_markets"]:
                        continue
                    observed = prop_model.value(played[1], config["markets"][market["market"]][0])
                    over = market["probability"] if market["side"] == "over" else 1 - market["probability"]
                    record = market["record"] if market["side"] == "over" else 1 - market["record"]
                    output.write(json.dumps({"sport": sport, "league": slug, "date": day, "game": game["id"],
                        "player": player["id"], "market": market["market"], "line": market["line"],
                        "p": over, "record": record, "y": int(observed > market["line"])}) + "\n")
                    total_rows += 1
        for team in team_ids:
            by_team[team].append(game)
        if index % 100 == 0:
            print(f"{sport}/{slug}: {index}/{len(games)} games, {total_rows} examples", flush=True)
    return {"sport": sport, "league": slug, "games": len(games), "target_game_stride": 4, "examples": total_rows,
            "start": games[0]["date"] if games else None, "end": games[-1]["date"] if games else None,
            "sha256": hashlib.sha256(json.dumps(files).encode()).hexdigest()}


def nfl_rows(output, seasons=None):
    from services.nfl_reliability import reject_sealed_games
    counts = []
    if seasons is None:
        seasons = sorted({m['season'] for m in store.metadata() if m['dataset'] == 'player_stats'})
    today = datetime.now(UTC).date().isoformat()
    for season in seasons:
        rows, previous = nfl_props._rows(season), nfl_props._rows(season - 1)
        reject_sealed_games({r["game_id"] for r in rows + previous})
        schedules = {g["game_id"]: g for g in store.records("schedules", season)}
        prior_players = defaultdict(list)
        for row in sorted(previous, key=lambda r: r['week']):
            prior_players[row["player_id"]].append(row)
        total = 0
        for week in sorted({r["week"] for r in rows if r["week"] >= 4}):
            history = [r for r in rows if r["week"] < week]
            by_player = defaultdict(list)
            for row in sorted(history, key=lambda r: r["week"]):
                by_player[row["player_id"]].append(row)
            context = nfl_props.Context(history, history + previous)
            for row in rows:
                if row["week"] != week or row.get("position") not in nfl_props.POSITIONS:
                    continue
                schedule = schedules.get(row["game_id"]) or {}
                date = schedule.get("gameday")
                if not date or date >= today:
                    continue
                markets = nfl_props.player_markets(row["position"], by_player[row["player_id"]],
                    prior_players[row["player_id"]], row.get("opponent_team"), context, include_distribution=False)
                for market in markets:
                    pick = market.get("pick")
                    binary = market['market'] == 'anytime_td'
                    if not pick or (pick.get("line") is None and not binary):
                        continue
                    observed = nfl_props._value(row, nfl_props.MARKETS[market["market"]][1])
                    if observed is None:
                        continue
                    output.write(json.dumps({"sport": "nfl", "league": "nfl", "date": date,
                        "game": row["game_id"], "player": row["player_id"], "market": market["market"],
                        "line": .5 if binary else pick["line"], "p": pick["probability"] if binary or pick["side"] == "over" else 1 - pick["probability"],
                        "record": pick["record"] if binary or pick["side"] == "over" else 1 - pick["record"],
                        "y": int(observed >= 1) if binary else int(observed > pick["line"])}) + "\n")
                    total += 1
            print(f"NFL {season} week {week}: {total} examples", flush=True)
        counts.append({"sport": "nfl", "season": season, "examples": total,
                       "source_sha256": nfl_props._fingerprint(season),
                       "prior_sha256": nfl_props._fingerprint(season - 1)})
    return counts


def metrics(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    side = p >= 0.5
    return {"n": len(y), "brier": float(np.mean((p - y) ** 2)),
            "log_loss": float(np.mean(-y * np.log(p) - (1 - y) * np.log1p(-p))),
            "predicted_over": float(np.mean(p)), "actual_over": float(np.mean(y)),
            "sides": {name: {"n": int(mask.sum()),
                "predicted": float(np.mean(np.where(side[mask], p[mask], 1 - p[mask]))) if mask.any() else None,
                "hit": float(np.mean(y[mask] == side[mask])) if mask.any() else None}
                for name, mask in (("over", side), ("under", ~side))}}


def fit(dataset, manifest, sport_only=None):
    groups, sport_days = defaultdict(list), defaultdict(set)
    with dataset.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            groups[(row["sport"], row["market"])].append(row)
            sport_days[row["sport"]].add(row["date"])
    splits = {}
    for sport, dates in sport_days.items():
        dates = sorted(dates)
        splits[sport] = (dates[int(len(dates) * .6)], dates[int(len(dates) * .8)])
    artifact, reports = {}, {}
    for (sport, market), rows in groups.items():
        first, second = splits[sport]
        parts = [[r for r in rows if (r["date"] < first if split == 0 else
                  first <= r["date"] < second if split == 1 else r["date"] >= second)] for split in range(3)]
        if min(map(len, parts)) < 100 or len(parts[0]) < 500:
            reports[f"{sport}/{market}"] = {"status": "insufficient_sample", "split_counts": list(map(len, parts))}
            continue
        p, y = np.array([r["p"] for r in parts[0]]), np.array([r["y"] for r in parts[0]])
        x = logit(np.clip(p, 1e-5, 1 - 1e-5))
        def loss(params):
            z = params[0] * x + params[1]
            return np.mean(np.logaddexp(0, z) - y * z) + .0001 * np.sum((params - [1, 0]) ** 2)
        fitted = minimize(loss, [1.0, 0.0], bounds=[(.1, 3), (-2, 2)], method="L-BFGS-B")
        slope, intercept = map(float, fitted.x)
        report = {"coefficients": [slope, intercept], "splits": {}, "status": "rejected"}
        passes = bool(fitted.success)
        for name, part in zip(("train", "validation", "test"), parts):
            p, y = np.array([r["p"] for r in part]), np.array([r["y"] for r in part])
            adjusted = expit(slope * logit(np.clip(p, 1e-5, 1 - 1e-5)) + intercept)
            before, after = metrics(p, y), metrics(adjusted, y)
            report["splits"][name] = {"start": min(r["date"] for r in part), "end": max(r["date"] for r in part),
                "raw": before, "calibrated": after,
                "historical_baseline": metrics(np.array([r["record"] for r in part]), y)}
            if name != "train":
                passes = passes and after["brier"] < before["brier"] and after["log_loss"] <= before["log_loss"]
        if passes:
            report["status"] = "accepted"
            artifact[f"{sport}/{market}"] = {"slope": slope, "intercept": intercept,
                "training_end": report["splits"]["train"]["end"], "test": report["splits"]["test"]}
        reports[f"{sport}/{market}"] = report
        print(f"{sport}/{market}: {report['status']}", flush=True)
    common = {"version": "player-props-calibration-v1", "created_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(), "sources": manifest,
        "policy": "60/20/20 chronological date groups; coefficients fitted on train only; one fixed candidate; validation and test gate promotion",
        "limitations": ["Historical targets, not sportsbook lines", "Conditional on participation; DNPs excluded",
            "Base projection heuristics include constants previously tuned on 2024; evaluation is not independent of that model design",
            "Historical source publication vintages are unverified; replay chronology is by game week",
            "Historical lineups unavailable; current rosters never used to select historical players",
            "Source box-score caches may encode unavailable statistics as zero; raw-feed audit remains necessary"],
        "examples": sum(len(rows) for rows in groups.values())}
    model_path = ROOT / 'data/models/player_props_calibration_v1.json'
    report_path = ROOT / 'data/validation/player_props_training.json'
    if sport_only:
        # Keep other sports and their provenance intact during an NFL-only run.
        for path, updates in ((model_path, artifact), (report_path, reports)):
            prior = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            merged = {k: v for k, v in prior.get('markets', {}).items() if not k.startswith(sport_only + '/')}
            merged.update(updates)
            body = {**prior, 'version': common['version'], 'markets': merged,
                    'sport_runs': {**prior.get('sport_runs', {}), sport_only: common}}
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, body)
        report_path = ROOT / f'data/validation/{sport_only}_props_training.json'
        atomic_write_json(report_path, {**common, 'markets': reports})
    else:
        model_path.write_text(json.dumps({**common, "markets": artifact}, indent=2), encoding="utf-8")
        report_path.write_text(json.dumps({**common, "markets": reports}, indent=2), encoding="utf-8")
    print(json.dumps({"examples": common["examples"], "accepted": list(artifact), "report": str(report_path)}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fit-only", action="store_true")
    parser.add_argument('--sport', choices=('all', 'nfl'), default='all')
    args = parser.parse_args()
    dataset = ROOT / '.tmp/props-training' / ('nfl-examples.jsonl' if args.sport == 'nfl' else 'examples.jsonl')
    dataset.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = dataset.with_suffix(".manifest.json")
    if not args.fit_only:
        with dataset.open("w", encoding="utf-8") as output:
            manifest = [] if args.sport == 'nfl' else [box_rows(sport, slug, output) for sport, slug in (
                ("soccer", "eng.1"), ("soccer", "esp.1"), ("soccer", "ger.1"), ("nba", "nba"))]
            manifest.extend(nfl_rows(output))
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    fit(dataset, json.loads(manifest_path.read_text(encoding="utf-8")), 'nfl' if args.sport == 'nfl' else None)


if __name__ == "__main__":
    main()
