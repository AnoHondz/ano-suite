"""Research snapshot: metadata-only NFL SQL and existing local audit evidence."""
import ast
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from scripts.research_github_sources import OUT, ROOT


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_files():
    files = set(ROOT.glob("*.py")) | set((ROOT / "services").glob("*.py"))
    files |= {p for p in (ROOT / "frontend/src").rglob("*") if p.is_file()}
    files |= {p for p in (ROOT / "data/models").glob("*") if p.is_file()}
    files |= set((ROOT / "docs/assets/nfl_phase2_3").glob("*.json"))
    files |= set((ROOT / "cache/nflverse/phase2_2").rglob("*.json"))
    files |= {ROOT / "scripts/nfl_collector.py", ROOT / "scripts/sync_nflverse.py"}
    return {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p) for p in sorted(files)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "existing_inventory.json"
    if target.exists():
        raise ValueError("Existing inventory snapshot retained")
    before = protected_files()
    database = (ROOT / "cache/nflverse/nflverse.sqlite3").resolve()
    # Only declared dataset metadata and aggregate receipts are queried. No
    # records/record_versions/outcomes/forecast payloads are opened or decoded.
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        metadata = [dict(r) for r in db.execute("SELECT dataset,season,row_count,synced_at,sha256 FROM datasets ORDER BY dataset,season")]
        receipts = [dict(r) for r in db.execute("SELECT dataset,season,MAX(collected_at) AS last_success FROM collection_receipts WHERE status='success' GROUP BY dataset,season")]
    audit = ROOT / "cache/diagnostics/2026-10-20_multisport"
    audit_names = ["nba_archive_gaps.json", "soccer_league_coverage.json", "nfl_data_audit.json", "cached_event_coverage.json", "soccer_csv_integrity.json"]
    tree = ast.parse((ROOT / "scripts/sync_nflverse.py").read_text(encoding="utf-8"))
    columns = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "PBP_COLUMNS" for t in node.targets))
    health = {"observed_at": datetime.now(UTC).isoformat()}
    try:
        try:
            response = urlopen("http://127.0.0.1:5001/api/health", timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            value = json.load(response)
            fields = ("status", "last_attempt", "last_success", "last_success_mins_ago", "consecutive_failures")
            health.update(http_status=response.code, overall=value.get("overall"),
                          pipelines={n: {k: r.get(k) for k in fields} for n, r in value.get("pipelines", {}).items()})
    except (URLError, OSError, ValueError) as exc:
        health["error_type"] = type(exc).__name__
    worker = json.loads((ROOT / "cache/nflverse/collector/status.json").read_text())
    fields = ("pid", "state", "heartbeat_at", "interval_seconds", "pbp_interval_seconds", "research_only", "enabled", "production_active")
    plan = json.loads((ROOT / "cache/nflverse/phase2_2/protocol.json").read_text())
    value = {"observed_at": datetime.now(UTC).isoformat(), "nfl_database_read_only": True,
             "nfl_sql_scope": "datasets metadata and aggregate collection_receipts only",
             "holdout_outcomes_read": 0, "holdout_games": len(plan["holdout"]["game_ids"]),
             "holdout_protocol_sha256": plan["protocol_sha256"], "nfl_dataset_metadata": metadata,
             "nfl_latest_success_receipts": receipts, "nfl_current_projection_columns": columns,
             "collector": {k: worker.get(k) for k in fields}, "application_health": health,
             "audit_snapshot_date": "2026-10-20; counts are historical measurements, not recomputed live",
             "audit_evidence": {name: {"path": str((audit / name).relative_to(ROOT)), "sha256": sha(audit / name)} for name in audit_names},
             "soccer_stage1_version": json.loads((ROOT / "data/processed/matches_stage1.sources.json").read_text()),
             "runtime_source_changes": "Stage1 plus local NBA machine-status correction; no Phase3 product source modifications"}
    target.write_text(json.dumps(value, indent=2), encoding="utf-8")
    (OUT / "preservation_before.json").write_text(json.dumps({"captured_at": datetime.now(UTC).isoformat(), "files": before}, indent=2), encoding="utf-8")
    print(json.dumps({"nfl_dataset_season_partitions": len(metadata), "preservation_files": len(before),
                      "app_health": health.get("overall"), "holdout_outcomes_read": 0}))


if __name__ == "__main__":
    main()
