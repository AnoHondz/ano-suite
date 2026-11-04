"""Read-only metadata/integrity checks; never select NFL outcome payloads."""
import argparse
from datetime import UTC, datetime

from scripts.audit_scorp import DEFAULT_OUTPUT, ROOT, fingerprint, load, readonly, write
from scripts.audit_scorp_providers import probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-integrity", action="store_true", help="Skip the expensive SQLite checks")
    args = parser.parse_args()
    result = {"observed_at": datetime.now(UTC).isoformat(), "read_only": True, "holdout_outcomes_read": 0}
    with readonly(ROOT / "cache/nflverse/nflverse.sqlite3") as db:
        db.execute("BEGIN")
        if not args.skip_integrity:
            result["quick_check"] = [list(r) for r in db.execute("PRAGMA quick_check(1)")]
            result["foreign_key_check"] = [list(r) for r in db.execute("PRAGMA foreign_key_check")]
        result["observation_timestamps"] = [dict(r) for r in db.execute(
            "SELECT dataset,season,COUNT(*) rows,SUM(observed_at IS NULL) null_observation,"
            "MIN(observed_at) first_observed_at,MAX(observed_at) last_observed_at "
            "FROM record_versions GROUP BY dataset,season ORDER BY dataset,season")]
        result["last_receipts"] = [dict(r) for r in db.execute(
            "SELECT id,dataset,season,attempted_at,collected_at,status,row_count rows "
            "FROM collection_receipts WHERE id IN (SELECT MAX(id) FROM collection_receipts GROUP BY dataset,season)")]
        result["forecast_table_counts"] = {name: db.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
            for name in ("nfl_frozen_models", "nfl_forecast_studies", "nfl_forecast_inputs", "nfl_forecast_batches", "nfl_forecasts", "nfl_forecast_events")}
        # Only unsealed game identifiers, never their statistics or scores.
        held = load(ROOT / "cache/nflverse/phase2_2/protocol.json")["holdout"]["game_ids"]
        if len(held) != 109:
            raise ValueError("Unexpected holdout; refuse audit")
        result["current_team_stats_game_ids"] = [r[0] for r in db.execute(
            "SELECT DISTINCT game_id FROM records WHERE dataset='team_stats' AND season=2026 "
            "AND game_id NOT IN (" + ",".join("?" for _ in held) + ") ORDER BY game_id", held)]
    result["collector"] = load(ROOT / "cache/nflverse/collector/status.json")
    log_path = ROOT / "cache/nflverse/collector/collector.log"
    # Boolean evidence only: never export arbitrary log content or account values.
    with log_path.open("rb") as log:
        log.seek(max(0, log_path.stat().st_size - 64_000))
        tail = log.read().decode("utf-8", errors="replace")
    result["collector_log_indicators"] = {"scope": "Last 64000 bytes, not a count or full incident timeline",
        "github_dns_failure_present": "github.com" in tail and "NameResolutionError" in tail,
        "connection_error_present": "ConnectionError" in tail}
    result["public_metadata_and_health"] = [probe(item) for item in (
        ("nfl_pbp_metadata", "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_2026.parquet", "HEAD"),
        ("local_pipeline_health", "http://127.0.0.1:5001/api/health", "GET"))]
    with readonly(ROOT / "persistent/auth.db") as accounts:
        result["account_store_metadata_only"] = {"schema": [dict(r) for r in accounts.execute(
            "SELECT name,type FROM sqlite_master WHERE type='table'")],
            "quick_check": [list(r) for r in accounts.execute("PRAGMA quick_check(1)")],
            "table_counts": {name: accounts.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0]
                             for name in ("user", "bet", "bet_submission")}}
    baselines = {"audit": DEFAULT_OUTPUT / "preservation_before.json",
                 "prior_nfl": ROOT / "cache/nflverse/phase2_3/preservation_before.json"}
    result["artifact_preservation"] = {}
    for label, path in baselines.items():
        files = load(path)["files"]
        result["artifact_preservation"][label] = {"files_checked": len(files), "changed": [
            name for name, expected in files.items() if not (ROOT / name).exists() or fingerprint(ROOT / name) != expected]}
    write(DEFAULT_OUTPUT, "integrity_recheck.json", result)
    print("Audit metadata checks complete; reserved outcomes not selected")


if __name__ == "__main__":
    main()
