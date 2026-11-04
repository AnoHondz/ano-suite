"""Opaque SQLite snapshot and aggregate equality checks, never outcome exports."""
import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cache/diagnostics/stage1_2026-10-21"


def sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("before", "after"))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    snapshot, original = OUT / "original_observations.sqlite3", ROOT / "cache/nflverse/nflverse.sqlite3"
    baseline = OUT / "preservation_before.json"
    if args.action == "before":
        if baseline.exists() or snapshot.exists():
            raise ValueError("Never overwrite preservation baseline")
        with sqlite3.connect(original.as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(snapshot) as dest:
            source.backup(dest)
        files = set((ROOT / "data/models").glob("*")) | set((ROOT / "docs/assets/nfl_phase2_3").glob("*.json"))
        files |= set((ROOT / "cache/nflverse/phase2_2").rglob("*.json"))
        old = json.loads((ROOT / "cache/nflverse/phase2_3/preservation_before.json").read_text())
        files |= {ROOT / n for n in old["files"] if not n.endswith(".py")}
        value = {"captured_at": datetime.now(UTC).isoformat(), "snapshot_sha256": sha(snapshot),
                 "files": {str(p.relative_to(ROOT)): sha(p) for p in sorted(files) if p.is_file()}}
        baseline.write_text(json.dumps(value, indent=2) + "\n")
    else:
        value = json.loads(baseline.read_text())
        result = {"verified_at": datetime.now(UTC).isoformat(), "snapshot_unchanged": sha(snapshot) == value["snapshot_sha256"],
                  "changed_artifacts": [n for n, h in value["files"].items() if sha(ROOT / n) != h],
                  "outcome_values_exported": 0, "comparison": "Aggregate equality inside SQLite; additions allowed, original rows immutable"}
        with sqlite3.connect(original.as_uri() + "?mode=ro", uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("ATTACH DATABASE ? AS baseline", (snapshot.as_uri() + "?mode=ro",))
            result["tables"] = {}
            for table, keys in (("record_versions", "a.dataset=b.dataset AND a.season=b.season AND a.record_key=b.record_key AND a.observed_at=b.observed_at"),
                                ("nfl_forecasts", "a.study_id=b.study_id AND a.game_id=b.game_id AND a.model_id=b.model_id"),
                                ("nfl_frozen_models", "a.model_id=b.model_id"),
                                ("nfl_forecast_studies", "a.study_id=b.study_id"),
                                ("nfl_forecast_inputs", "a.fingerprint=b.fingerprint"),
                                ("nfl_forecast_batches", "a.study_id=b.study_id AND a.game_id=b.game_id"),
                                ("nfl_forecast_events", "a.id=b.id"),
                                ("collection_receipts", "a.id=b.id"),
                                ("collection_games", "a.receipt_id=b.receipt_id AND a.game_id=b.game_id")):
                columns = [r[1] for r in db.execute("PRAGMA table_info('" + table + "')")]
                check = " OR ".join("a." + name + " IS NOT b." + name for name in columns)
                result["tables"][table] = {"original_rows": db.execute("SELECT COUNT(*) FROM baseline." + table).fetchone()[0],
                    "missing_or_changed": db.execute("SELECT COUNT(*) FROM baseline." + table + " a LEFT JOIN main." + table + " b ON " + keys + " WHERE b.rowid IS NULL OR " + check).fetchone()[0]}
        (OUT / "preservation_after.json").write_text(json.dumps(result, indent=2) + "\n")
        if result["changed_artifacts"] or not result["snapshot_unchanged"] or any(r["missing_or_changed"] for r in result["tables"].values()):
            raise ValueError("Preservation mismatch")
    print("Preservation", args.action, "complete; no outcomes decoded")


if __name__ == "__main__":
    main()
