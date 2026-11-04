"""Summarize usable data, provider provenance, quality repairs and exclusions."""
from collections import defaultdict
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.train_soccer_player_counts import checked_game

sources = defaultdict(lambda: {"games": 0, "player_games": 0, "leagues": set(), "dates": []})
quarantined, duplicates = [], 0
for path in (ROOT / "data/processed/soccer_props_v2/games").glob("*/*.json"):
    try:
        game, repaired = checked_game(json.loads(path.read_text(encoding="utf-8")))
        duplicates += repaired
    except ValueError as exc:
        quarantined.append({"file": str(path.relative_to(ROOT)), "reason": str(exc)})
        continue
    row = sources[game["source"]]
    row["games"] += 1
    row["player_games"] += sum(len(t["players"]) for t in game["teams"].values())
    row["leagues"].add(game["league"])
    row["dates"].append(game["date"])
for row in sources.values():
    row["leagues"] = sorted(row["leagues"])
    row["start"], row["end"] = min(row["dates"]), max(row.pop("dates"))
attempts = []
for name in ("soccer_props_sources_v2.json", "soccer_props_supplement_v2.json", "soccer_props_statsbomb_v2.json"):
    path = ROOT / "data/validation" / name
    if path.exists(): attempts.append({"file": name, "details": json.loads(path.read_text(encoding="utf-8"))})
report = {"created_at": datetime.now(UTC).isoformat(), "providers": dict(sources),
    "usable_games": sum(r["games"] for r in sources.values()), "quarantined": quarantined,
    "identical_duplicate_rows_removed": duplicates,
    "training_data": json.loads((ROOT / ".tmp/soccer-training-v2/count_features.manifest.json").read_text(encoding="utf-8")),
    "contracts": ["No provider player IDs merged by names", "FPL fantasy assists not pooled with official assists",
        "Tackles trained only on comparable FPL labels", "FPL overlapping ESPN fixtures contribute tackles only",
        "No xP, post-match aggregates or future appearances used as pregame features",
        "Sparse champion-only StatsBomb Bundesliga sample excluded", "No current roster used for historical player selection"],
    "collection_attempts": attempts}
target = ROOT / "data/validation/soccer_props_data_audit_v2.json"
target.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({"usable_games": report["usable_games"], "providers": dict(sources), "report": str(target)}))
