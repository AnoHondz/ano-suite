"""Loopback-only local backend owner. No training or research activation."""
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".tmp/local-app"
SOURCES = ("app.py", "api_client.py", "services/props_email.py", "services/nfl_service.py", "services/prediction_service.py", "services/cache_service.py", "services/decision_store.py",
           "services/prop_market_value.py", "services/prop_evidence_store.py", "services/nba_prop_research.py", "services/nba_player_line.py",
           "services/prop_paper_results.py", "services/prop_results_refresh.py", "services/prop_closing_price.py",
           "services/nba_opportunity.py", "templates/nba_prop_research.html", "templates/props.html", "templates/base.html",
           "services/prop_analyst_research.py",
           "services/daft_uploads.py", "services/prop_video_content.py", "services/prop_parlay_research.py",
           "services/prop_validation.py", "services/prop_pipeline.py", "services/prop_manual_quote.py", "services/scheduler.py",
           "services/data_freshness.py", "services/prediction_lifecycle.py", "services/nba_matchup_context.py", "nba_routes.py", "nba_live_client.py",
           "services/nba_pregame_model.py", "services/nba_game_projection.py", "services/nba_early_season.py",
           "nfl_routes.py", "services/nfl_props.py", "services/soccer_props.py", "services/soccer_props_loader.py", "services/soccer_model_picks.py", "services/injury_impact.py",
           "services/injury_reports.py", "services/nfl_player_value.py", "services/soccer_player_value.py",
           "services/espn_box_scores.py", "services/prop_model.py", "services/nba_props.py", "services/fpl_props.py",
           "services/prop_markets.py", "services/prop_calibration.py", "services/soccer_player_model.py", "services/best_props.py", "services/current_rosters.py", "services/daily_digest.py", "services/ano_email.py", "services/ano_email_theme.py", "services/scheduler.py", "services/soccer_model.py", "decision_ui.py", "scripts/run_local.py")


def source_fingerprint():
    return hashlib.sha256(b"".join((ROOT / name).read_bytes() for name in SOURCES)).hexdigest()


def main():
    import msvcrt
    STATE.mkdir(parents=True, exist_ok=True)
    # OS releases this lock on exit; stale PID files can never create two owners.
    with (STATE / "backend.lock").open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit("ScorPred local backend is already owned by another process.")
        os.chdir(ROOT)
        sys.path.insert(0, str(ROOT))
        # NFL serving data (schedules, player stats, injuries) refreshes every 6 h, as in the cloud
        # deployment; without it props stall at the last manual nflverse sync.
        os.environ.update(PORT="5001", SCORPRED_TESTING="0", SCORPRED_SCHEDULER_ENABLED="1", SCORPRED_NFL_SYNC_ENABLED="1",
                          NFL_ADVANCED_RESEARCH_ENABLED="0", NFL_WINNER_RESEARCH_ENABLED="0", NBA_EARLY_SEASON_RESEARCH_ENABLED="0", ALERT_EMAIL="")
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        import app as application
        from flask import jsonify
        from werkzeug.serving import make_server

        identity = {"app": "ScorPred", "pid": os.getpid(), "root": str(ROOT),
                    "source_fingerprint": source_fingerprint(), "research_enabled": False}
        def local_runtime():
            from services import daily_digest
            scheduler=application._pipeline_scheduler
            paper_job=scheduler.get_job('nba_prop_paper_results') if scheduler is not None else None
            analyst_job=scheduler.get_job('nba_prop_analyst_uploads') if scheduler is not None else None
            jobs = sorted(({"id": job.id, "next_run": job.next_run_time.isoformat() if job.next_run_time else None}
                           for job in scheduler.get_jobs()), key=lambda j: j["id"]) if scheduler is not None else []
            return jsonify({**identity, "daily_digest": daily_digest.status(scheduler), "scheduled_jobs": jobs,
                'prop_research':{'path':'/nba/props/research','scope':'NBA points, rebounds and assists research; no wager execution',
                    'analyst_job_registered':analyst_job is not None,
                    'paper_results_job_registered':paper_job is not None,
                    'paper_results_next_run':paper_job.next_run_time.isoformat() if paper_job is not None else None}})
        application.app.add_url_rule("/api/local-runtime", "local_runtime", local_runtime)
        stop = STATE / "backend.stop"
        stop.unlink(missing_ok=True)
        server = make_server("127.0.0.1", 5001, application.app, threaded=True)
        (STATE / "backend.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
        application.start_background_services()
        # Invoke registered jobs once after startup. Their actual receipts, not
        # server startup, determine pipeline health; singleton limits still apply.
        if application._pipeline_scheduler is not None:
            for delay, name in enumerate(("soccer_fixtures", "nba_games", "ml_predictions"), 1):
                application._pipeline_scheduler.modify_job(name, next_run_time=datetime.now(UTC) + timedelta(seconds=delay))

        def wait_for_stop():
            while not stop.exists():
                time.sleep(.5)
            server.shutdown()

        threading.Thread(target=wait_for_stop, daemon=True).start()
        try:
            server.serve_forever()
        finally:
            application.stop_background_services()
            server.server_close()


if __name__ == "__main__":
    main()
