"""Singleton local NFL collector using existing refresh/import/snapshot modules.

No Flask startup, prediction training, activation, publication or deployment.
"""
import argparse
import hashlib
import json
import logging
import os
import signal
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from filelock import FileLock, Timeout

from scripts.sync_nflverse import default_season, sync
from services import nflverse_store as store
from services.nfl_winner_research import prepare_snapshots
from services.nflverse_provider import refresh_schedules
from utils.atomic import atomic_write_json

COLLECTOR_VERSION = "nfl-prospective-collector-v2"
PBP_INTERVAL_SECONDS = 21600
log = logging.getLogger(__name__)


def code_hash():
    root = Path(__file__).resolve().parents[1]
    files = ("scripts/nfl_collector.py", "scripts/sync_nflverse.py", "services/nflverse_provider.py",
             "services/nflverse_store.py", "services/nfl_winner_features.py", "services/nfl_winner_research.py",
             "services/nfl_pregame_features.py", "services/nfl_frozen_models.py", "services/nfl_pregame_forecast.py")
    return hashlib.sha256(b"".join((root / name).read_bytes() for name in files)).hexdigest()


def collect_cycle(database, state_dir, *, snapshots_due=True, collector_identity=None):
    now, season = datetime.now(UTC), default_season()
    identity = collector_identity or {"collector_version": COLLECTOR_VERSION, "collector_pid": os.getpid(), "writer_code_sha256": code_hash()}
    errors, result = [], {"started_at": now.isoformat(), **identity}
    try:
        result["schedule_received_at"] = refresh_schedules([season], path=database, collector_metadata=identity).isoformat()
    except Exception as exc:
        log.exception("NFL schedule collection failed")
        errors.append({"stage": "schedules", "error": type(exc).__name__})
    meta = next((r for r in store.metadata(path=database) if r["dataset"] == "pbp" and r["season"] == season), None)
    if meta is None or now - datetime.fromisoformat(meta["synced_at"]) >= timedelta(seconds=PBP_INTERVAL_SECONDS):
        try:
            # Collector-local refresh policy; do not alter the app environment.
            os.environ["NFL_REFRESH_PBP_SECONDS"] = str(PBP_INTERVAL_SECONDS)
            result["pbp"] = sync([season], ["pbp"], path=database, refresh=True, keep_going=True)
            errors.extend({"stage": "pbp", "error": r.get("error", r["status"])} for r in result["pbp"] if r.get("status") in {"failed", "unavailable"})
        except Exception as exc:
            log.exception("NFL PBP collection failed")
            errors.append({"stage": "pbp", "error": type(exc).__name__})
    if snapshots_due:
        try:
            snapshots = prepare_snapshots([season], path=database)
            result["snapshot_count"] = sum(r["available"] for r in snapshots)
            result["snapshot_exclusions"] = [r for r in snapshots if not r["available"]]
        except Exception as exc:
            log.exception("NFL feature collection failed")
            errors.append({"stage": "snapshots", "error": type(exc).__name__})
    try:
        from services.nfl_pregame_forecast import cycle
        result["forecasts"] = cycle(path=database)
        if result["forecasts"]["status"] == "degraded":
            errors.append({"stage": "forecasts", "error": "pregame_inputs_rejected"})
    except Exception as exc:
        log.exception("NFL pregame research forecasting failed; existing archives preserved")
        errors.append({"stage": "forecasts", "error": type(exc).__name__})
    result.update(completed_at=datetime.now(UTC).isoformat(), errors=errors, status="degraded" if errors else "success")
    with (Path(state_dir) / "cycles.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, sort_keys=True) + "\n")
    return result


def run(database, state_dir, *, interval_seconds=240, once=False):
    if not 30 <= interval_seconds <= 240:
        raise ValueError("Use a 30–240 second schedule interval, below the five-minute source freshness limit")
    database, state_dir = Path(database).resolve(), Path(state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    database.parent.mkdir(parents=True, exist_ok=True)
    stop, stop_file = threading.Event(), state_dir / "stop.request"
    with FileLock(str(database) + ".collector.lock", timeout=0):
        stop_file.unlink(missing_ok=True)
        store.enable_versioned_writes(path=database)
        started = datetime.now(UTC).isoformat()
        cycle_count, last_snapshot = 0, None
        state = {"version": COLLECTOR_VERSION, "pid": os.getpid(), "database": str(database),
                 "started_at": started, "code_sha256": code_hash(), "interval_seconds": interval_seconds,
                 "pbp_interval_seconds": PBP_INTERVAL_SECONDS,
                 "research_only": True, "enabled": False, "production_active": False}
        handlers = {sig: signal.signal(sig, lambda *_args: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
        identity = {"collector_version": COLLECTOR_VERSION, "collector_pid": os.getpid(), "writer_code_sha256": state["code_sha256"]}
        try:
            while not stop.is_set() and not stop_file.exists():
                cycle_started = datetime.now(UTC)
                atomic_write_json(state_dir / "status.json", {**state, "state": "collecting", "heartbeat_at": datetime.now(UTC).isoformat(), "cycles": cycle_count})
                due = last_snapshot is None or datetime.now(UTC) - last_snapshot >= timedelta(minutes=20)
                result = collect_cycle(database, state_dir, snapshots_due=due, collector_identity=identity)
                cycle_count += 1
                if due and "snapshot_count" in result:
                    last_snapshot = datetime.now(UTC)
                state["last_cycle"] = result
                atomic_write_json(state_dir / "status.json", {**state, "state": "waiting", "heartbeat_at": datetime.now(UTC).isoformat(), "cycles": cycle_count})
                if once:
                    break
                deadline = cycle_started + timedelta(seconds=interval_seconds)
                while datetime.now(UTC) < deadline and not stop_file.exists() and not stop.wait(5):
                    atomic_write_json(state_dir / "status.json", {**state, "state": "waiting", "heartbeat_at": datetime.now(UTC).isoformat(), "cycles": cycle_count})
        finally:
            atomic_write_json(state_dir / "status.json", {**state, "state": "stopped", "heartbeat_at": datetime.now(UTC).isoformat(), "cycles": cycle_count})
            for sig, handler in handlers.items():
                signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(store.database_path()))
    parser.add_argument("--state-dir", default=str(store.database_path().parent / "collector"))
    parser.add_argument("--interval-seconds", type=int, default=240)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--stop", action="store_true", help="Request graceful stop after the active collection finishes")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    if args.stop:
        (state_dir / "stop.request").write_text(datetime.now(UTC).isoformat())
        return
    if args.status:
        print((state_dir / "status.json").read_text() if (state_dir / "status.json").exists() else '{"state":"not_started"}')
        return
    logging.basicConfig(filename=state_dir / "collector.log", level=logging.INFO)
    try:
        run(args.database, state_dir, interval_seconds=args.interval_seconds, once=args.once)
    except Timeout:
        raise SystemExit("An NFL collector already owns this database") from None


if __name__ == "__main__":
    main()
