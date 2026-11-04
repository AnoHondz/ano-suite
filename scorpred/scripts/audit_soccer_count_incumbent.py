"""Frozen-model audit against the incumbent distribution on common held-out players."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, UTC
import json
import math
import os
from pathlib import Path
import sys
import numpy as np
from scipy.special import expit, logit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SCORPRED_PROPS_TRAINING"] = "1"
from services import prop_model, soccer_props, soccer_player_model as counts, fpl_props
from scripts.train_soccer_player_counts import THRESHOLDS


def main():
    artifact_path = ROOT / "data/models" / counts.ARTIFACT
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    report = json.loads((ROOT / "data/validation/soccer_player_counts_v2.json").read_text(encoding="utf-8"))
    calibration = json.loads((ROOT / "data/models/player_props_calibration_v1.json").read_text(encoding="utf-8"))
    counts.artifact = lambda: artifact
    results = defaultdict(list)
    for league in ("eng.1", "esp.1", "ger.1"):
        games = [json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "data/processed/soccer_props_v2/games/espn").glob("*.json")]
        games = sorted([g for g in games if g["league"] == league], key=lambda g: g["date"])
        by_team = defaultdict(list)
        tested = 0
        for index, game in enumerate(games):
            evaluated = False
            home, away = (tid for tid, side in sorted(game["teams"].items(), key=lambda item: not item[1]["home"]))
            day = game["date"][:10]
            cutoff = min(row["split_dates"]["test_from"] for key, row in report["markets"].items() if key != "tackles" and row.get("status") == "accepted")
            if day >= cutoff and index % 4 == 0:
                season = soccer_props.season_of(datetime.fromisoformat(game["date"].replace("Z", "+00:00")))
                prior = {g["id"]: g for tid in (home, away) for g in by_team[tid]
                         if g["date"][:10] < day and soccer_props.season_of(datetime.fromisoformat(g["date"].replace("Z", "+00:00"))) >= season - 1}
                if prior:
                    old = prop_model.game_props(soccer_props.CONFIG, home, away, list(prior.values()))
                    new = counts.apply_to_players(deepcopy(old["players"]), list(prior.values()), home, away,
                                                  datetime.fromisoformat(game["date"].replace("Z", "+00:00")))
                    actual = {p["id"]: (tid, p) for tid, team in game["teams"].items() for p in team["players"]}
                    original = {p["id"]: p for p in old["players"]}
                    for player in new:
                        if player["id"] not in actual or actual[player["id"]][0] != player["team"]: continue
                        previous = {m["market"]: m for m in original[player["id"]]["markets"]}
                        observed = actual[player["id"]][1]
                        for market in player["markets"]:
                            key = market["market"]
                            if market.get("model") != counts.VERSION or day < report["markets"][key]["split_dates"]["test_from"]: continue
                            if not counts.known(observed, key): continue
                            before = previous[key]
                            thresholds = THRESHOLDS[key]
                            p_old = np.array([before["ladder"]["over"][math.floor(line)] for line in thresholds])
                            layer = calibration["markets"].get("soccer/" + key)
                            if layer:
                                if day <= layer["training_end"]: continue
                                p_old = expit(layer["slope"] * logit(np.clip(p_old, 1e-5, 1 - 1e-5)) + layer["intercept"])
                            p_new = np.array([market["ladder"]["over"][math.floor(line)] for line in thresholds])
                            y = observed[key] > np.array(thresholds)
                            results[key].append((float(np.mean((p_old - y) ** 2)), float(np.mean((p_new - y) ** 2)),
                                abs(before["projection"] - observed[key]), abs(market["projection"] - observed[key])))
                    tested += 1
                    evaluated = True
            for tid in game["teams"]: by_team[tid].append(game)
            if evaluated and tested % 20 == 0:
                print(f"Incumbent {league}: {tested} held-out games", flush=True)
    output = {"policy": "Fixed every-fourth held-out ESPN fixture; common participating current-role players; original calibration kept",
              "selection": "Frozen count models; no tuning against this audit", "markets": {}}
    for key, rows in results.items():
        values = np.array(rows)
        before, after, old_mae, new_mae = map(float, values.mean(axis=0))
        enabled = len(rows) >= 100 and after <= before and new_mae <= old_mae * 1.02
        output["markets"][key] = {"n": len(rows), "incumbent_brier": before, "candidate_brier": after,
            "incumbent_mae": old_mae, "candidate_mae": new_mae, "enabled": enabled}
        artifact["markets"][key]["enabled"] = enabled
        artifact["markets"][key]["incumbent_audit"] = output["markets"][key]
        print(key, json.dumps(output["markets"][key]), flush=True)
    # Tackles have a separately documented FPL baseline in the training report;
    # live inference also requires complete FPL team context and eight role games.
    for key in artifact["markets"]:
        if key != "tackles" and key not in results: artifact["markets"][key]["enabled"] = False
    temporary = artifact_path.with_suffix(".new.json")
    temporary.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    temporary.replace(artifact_path)
    (ROOT / "data/validation/soccer_player_incumbent_v2.json").write_text(json.dumps(output, indent=2), encoding="utf-8")


if __name__ == "__main__": main()
