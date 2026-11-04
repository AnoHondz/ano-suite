"""Compare the frozen tackle model with the incumbent FPL Poisson forecast."""
from collections import defaultdict, deque
import json
from pathlib import Path
import sys
import numpy as np
from scipy.stats import poisson

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services import soccer_player_model as counts, fpl_props
from scripts.train_soccer_player_counts import checked_game

artifact_path = ROOT / "data/models" / counts.ARTIFACT
artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
training = json.loads((ROOT / "data/validation/soccer_player_counts_v2.json").read_text(encoding="utf-8"))
fitted = artifact["markets"].get("tackles")
if not fitted: raise SystemExit("No frozen tackle model passed the first gate")
cutoff = training["markets"]["tackles"]["split_dates"]["test_from"]
games = sorted([checked_game(json.loads(p.read_text(encoding="utf-8")))[0] for p in
    (ROOT / "data/processed/soccer_props_v2/games/fpl").glob("2025-26-*.json")], key=lambda g: g["date"])
history, totals, errors = defaultdict(lambda: deque(maxlen=40)), defaultdict(lambda: deque(maxlen=8)), []
for index, game in enumerate(games):
    for tid, team in game["teams"].items():
        for player in team["players"]:
            rows = list(history[player["id"]])
            if game["date"][:10] >= cutoff and index % 4 == 0 and player["position"] != "G" and fpl_props.expected_minutes(rows) >= 60:
                data = counts.inputs(rows, "tackles", player["position"], list(totals[team["opponent"]]),
                                     list(totals[tid]), team["home"], game["date"])
                if data and counts.known(player, "tackles"):
                    old_mu = max(.001, fpl_props.per_90(rows, None, "tackles") * fpl_props.expected_minutes(rows) / 90)
                    mu = float(counts.mean_prediction(data["x"], fitted))
                    thresholds = np.arange(4)
                    old, new = poisson.sf(thresholds, old_mu), counts.survival(mu, fitted["alpha"], thresholds)
                    y = player["tackles"] > thresholds + .5
                    errors.append((float(np.mean((old - y) ** 2)), float(np.mean((new - y) ** 2)),
                                   abs(old_mu - player["tackles"]), abs(mu - player["tackles"])))
    for tid, team in game["teams"].items():
        for player in team["players"]:
            history[player["id"]].append({**player, "date": game["date"]})
        if all(counts.known(p, "tackles") for p in team["players"]):
            totals[tid].append(sum(p["tackles"] for p in team["players"]))
values = np.array(errors)
old_brier, new_brier, old_mae, new_mae = map(float, values.mean(axis=0))
audit = {"n": len(errors), "incumbent_brier": old_brier, "candidate_brier": new_brier,
         "incumbent_mae": old_mae, "candidate_mae": new_mae,
         "enabled": bool(len(errors) >= 100 and new_brier <= old_brier and new_mae <= old_mae * 1.02),
         "policy": "Fixed every-fourth held-out FPL fixture; common participating regulars; no current roster leakage"}
fitted.update(enabled=audit["enabled"], incumbent_audit=audit)
temporary = artifact_path.with_suffix(".new.json")
temporary.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
temporary.replace(artifact_path)
report_path = ROOT / "data/validation/soccer_player_incumbent_v2.json"
report = json.loads(report_path.read_text(encoding="utf-8"))
report["markets"]["tackles"] = audit
report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(audit), flush=True)
