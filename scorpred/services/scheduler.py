"""Background scheduler for proactive data refresh.

Replaces the crude threading.Timer loop previously used in app.py.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

from services.pipeline_monitor import combine_refresh_outcomes, fixture_refresh_outcome

_log = logging.getLogger("scheduler")
_scheduler: BackgroundScheduler | None = None


def _record_refresh(monitor, pipeline: str, outcomes: list[dict]) -> None:
    outcome = combine_refresh_outcomes(outcomes)
    if outcome["status"] == "success":
        monitor.record_success(pipeline, observed_at=outcome["observed_at"])
        _log.info("%s refresh succeeded observed_at=%s", pipeline, outcome["observed_at"])
    else:
        error = RuntimeError(outcome["error"] or "Refresh did not complete successfully")
        monitor.record_failure(pipeline, error)
        _log.warning("%s refresh %s: %s", pipeline, outcome["status"], error)


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(
            executors={"default": ThreadPoolExecutor(4)},
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 120,
            },
        )
    return _scheduler


def start(app, prediction_service, ac, nba_live_client, monitor, *, match_brain, track_predictions=None,
          build_best_props=None) -> BackgroundScheduler:
    """Register all refresh jobs and start the scheduler."""
    from league_config import SUPPORTED_LEAGUE_IDS

    sched = get_scheduler()
    if sched.running:
        return sched

    @sched.scheduled_job("interval", minutes=25, id="soccer_fixtures")
    def refresh_soccer():
        with app.app_context():
            outcomes = []
            for league_id in SUPPORTED_LEAGUE_IDS:
                try:
                    _cards, fixtures, error, source, marker = prediction_service.get_fixture_cards(league_id)
                    outcome = fixture_refresh_outcome(fixtures, error, source, marker)
                    if outcome.get("error"):
                        outcome["error"] = f"League {league_id}: {outcome['error']}"
                    outcomes.append(outcome)
                except Exception as exc:
                    outcomes.append({"status": "failed", "observed_at": None, "error": f"League {league_id}: {exc}"})
            _record_refresh(monitor, "soccer_fixtures", outcomes)

    @sched.scheduled_job("interval", minutes=25, start_date="2000-01-01 00:02:00", id="nba_games")
    def refresh_nba():
        with app.app_context():
            try:
                if nba_live_client is None:
                    raise RuntimeError("NBA provider is unavailable")
                nba_live_client.get_upcoming_games(30, 7, "api")
                # [] can mean a valid off-season schedule. The source outcome decides health.
                outcome = nba_live_client.get_schedule_refresh_status()
                _record_refresh(monitor, "nba_games", [outcome])
            except Exception as exc:
                _log.warning("nba_games refresh failed: %s", exc)
                monitor.record_failure("nba_games", exc)

    @sched.scheduled_job("interval", minutes=60, id="ml_predictions")
    def refresh_predictions():
        with app.app_context():
            outcomes = []
            for league_id in SUPPORTED_LEAGUE_IDS:
                try:
                    if match_brain is None:
                        raise RuntimeError("Prediction brain is unavailable")
                    outcome = match_brain.refresh_cycle(league_id, min_interval_seconds=0)
                    if isinstance(outcome, dict):
                        outcome = dict(outcome)
                        if outcome.get("error"):
                            outcome["error"] = f"League {league_id}: {outcome['error']}"
                    outcomes.append(outcome)
                except Exception as exc:
                    outcomes.append({"status": "failed", "observed_at": None, "error": f"League {league_id}: {exc}"})
            _record_refresh(monitor, "ml_predictions", outcomes)

    @sched.scheduled_job("interval", minutes=10, id="staleness_check")
    def check_staleness():
        with app.app_context():
            monitor.check_staleness()

    @sched.scheduled_job('cron', hour='8-12', timezone='America/New_York', id='nba_prop_analyst_uploads')
    def refresh_prop_analyst_uploads():
        with app.app_context():
            try:
                from services.daft_uploads import collect
                collect()
            except Exception as exc:
                _log.warning('NBA analyst collection unavailable (%s)', type(exc).__name__)

    @sched.scheduled_job('interval', minutes=60, id='nba_prop_paper_results')
    def refresh_prop_paper_results():
        with app.app_context():
            try:
                from runtime_paths import persistent_root
                from services.prop_evidence_store import EvidenceStore
                from services.prop_results_refresh import refresh
                refresh(EvidenceStore(persistent_root() / 'prop-research' / 'evidence.sqlite3'))
            except Exception:
                _log.exception('NBA prop paper result refresh failed')

    # Free league-wide feeds (ESPN NBA/NFL, Fantasy Premier League): three requests per run, no API quota.
    # Runs at startup, then just inside the feeds' 10-minute freshness window so requests read a warm cache.
    @sched.scheduled_job("interval", minutes=9, next_run_time=datetime.now(timezone.utc), id="injury_reports")
    def refresh_injuries():
        from services import injury_reports
        try:
            _record_refresh(monitor, "injury_reports", injury_reports.refresh_all())
        except Exception as exc:
            _log.warning("injury_reports refresh failed: %s", exc)
            monitor.record_failure("injury_reports", exc)

    # Record every published pregame pick (and revise it until kickoff), then grade finished games.
    @sched.scheduled_job("interval", minutes=30, start_date="2000-01-01 00:04:00", id="prediction_tracking")
    def run_prediction_tracking():
        if track_predictions is None:
            return
        try:
            outcome = track_predictions()
            _log.info("prediction_tracking %s", outcome)
        except Exception as exc:
            _log.warning("prediction_tracking failed: %s", exc)

    # Keep today's best-props board warm so the page never waits on a full rebuild. Hourly and on two
    # threads: the build is CPU-heavy Python and must not starve page requests (Insights timed out at 15 min/6 threads).
    @sched.scheduled_job("interval", minutes=60, next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2), id="best_props")
    def warm_best_props():
        if build_best_props is None:
            return
        from services import best_props, prop_markets
        from services.prediction_lifecycle import calendar_date
        day = calendar_date(datetime.now(timezone.utc))
        try:
            # Hourly pricing of every game spent the monthly odds allowance in hours; price on demand only.
            with prop_markets.prices_deferred():
                best_props.refresh(day, lambda: build_best_props(day))
        except Exception as exc:
            _log.warning("best_props failed: %s", exc)

    # Learn from graded picks once a day; a correction is applied only if it beats held-out picks.
    @sched.scheduled_job("interval", hours=24, start_date="2000-01-01 02:30:00", id="recalibration")
    def learn_recalibration():
        from services import recalibration
        try:
            recalibration.learn_all()
        except Exception as exc:
            _log.warning("recalibration failed: %s", exc)

    # Closing prices for tracked picks; only games starting within the next 25 minutes are priced.
    @sched.scheduled_job("interval", minutes=10, start_date="2000-01-01 00:03:00", id="closing_lines")
    def capture_closing_lines():
        from services import closing_lines
        try:
            outcome = closing_lines.capture(nba_live_client)
            if outcome["recorded"] or outcome["missing"]:
                _log.info("closing_lines recorded=%s missing=%s", outcome["recorded"], outcome["missing"])
        except Exception as exc:
            _log.warning("closing_lines failed: %s", exc)

    @sched.scheduled_job("interval", hours=6, start_date="2000-01-01 00:05:00", id="cache_prune")
    def prune_cache():
        from services import cache_pruner
        try:
            cache_pruner.prune()
        except Exception as exc:
            _log.warning("cache_prune failed: %s", exc)

    # Explicit local opt-in; no sender credentials means no unattended email.
    from services import daily_digest
    try:
        digest_config = daily_digest.settings()
        if digest_config["enabled"]:
            hour, minute = map(int, digest_config["time"].split(":"))
            def send_daily_digest():
                try:
                    result = daily_digest.run(app)
                    _log.info("daily_sports_digest state=%s", result["state"])
                except Exception as exc:
                    _log.warning("daily_sports_digest failed (%s)", type(exc).__name__)
            sched.add_job(send_daily_digest, "cron", hour=hour, minute=minute,
                          timezone=digest_config["timezone"], id="daily_sports_digest",
                          replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=120)
            # NBA player props are often posted after the morning email: noon, with a 3 PM retry, once a day.
            def send_player_parlays():
                try:
                    from services.parlay_email import send_player_parlays as send
                    _log.info("nba_player_parlays %s", send(app)["state"])
                except Exception as exc:
                    _log.warning("nba_player_parlays failed (%s)", type(exc).__name__)
            sched.add_job(send_player_parlays, "cron", hour="12,15", minute=0, timezone=digest_config["timezone"],
                          id="nba_player_parlays", replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=600)
    except (OSError, ValueError, TypeError):
        _log.warning("Daily sports digest configuration is invalid; email remains disabled.")

    if os.getenv("SCORPRED_NFL_SYNC_ENABLED", "0") == "1":
        @sched.scheduled_job("interval", hours=6, id="nfl_serving_data",
                             next_run_time=datetime.now(timezone.utc) + timedelta(seconds=2))
        def refresh_nfl_serving_data():
            from services.cloud_refresh import refresh_nfl
            try:
                outcomes = refresh_nfl()
                failed = [row for row in outcomes if row.get("status") in {"failed", "unavailable"}]
                _log.info("nfl_serving_data refresh datasets=%s failed=%s", len(outcomes), len(failed))
            except Exception as exc:
                _log.warning("nfl_serving_data refresh failed (%s)", type(exc).__name__)

    # Parlay Mode: one sportsbook-priced slate per sport per day, from the shared odds-credit allowance.
    try:
        from parlay_routes import register_parlay_jobs
        register_parlay_jobs(sched, app)
    except Exception as exc:
        _log.warning("parlay jobs not registered: %s", exc)

    sched.start()
    _log.info("APScheduler started with jobs: soccer_fixtures, nba_games, ml_predictions, injury_reports, staleness_check, prediction_tracking, best_props, recalibration, closing_lines, cache_prune, parlay_nba/nfl/soccer")
    return sched


def stop():
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
