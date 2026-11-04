"""Provider failures never become healthy refreshes merely by serving cached rows."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services import scheduler
from services.decision_engine import DecisionEngine
from services.match_brain import MatchBrain
from services.pipeline_monitor import PipelineMonitor
from tests.source_contracts import fresh_source


def _fixture():
    return {
        "data_freshness": fresh_source(),
        "fixture": {"id": 42, "date": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(), "status": {"short": "NS"}},
        "teams": {"home": {"id": 1, "name": "Home"}, "away": {"id": 2, "name": "Away"}},
        "prediction": {"win_probabilities": {"a": 60, "draw": 20, "b": 20}, "confidence_pct": 70,
                       "best_pick": {"prediction": "Home"}, "data_completeness": {"tier": "strong"}},
    }


def _brain(loader, **kwargs):
    return MatchBrain(load_fixtures=loader, get_fixture_by_id=lambda _key: None,
                      decision_engine=DecisionEngine(), **kwargs)


def _start_scheduler(monkeypatch, brain, *, prediction_service=None, nba=None):
    monkeypatch.setattr(scheduler.BackgroundScheduler, "start", lambda self: None)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monitor = PipelineMonitor()
    sched = scheduler.start(SimpleNamespace(app_context=nullcontext), prediction_service, None, nba, monitor,
                            match_brain=brain)
    return sched, monitor


def test_first_refresh_failure_is_degraded_and_does_not_advance_success():
    monitor = PipelineMonitor()
    observed = datetime.now(timezone.utc) - timedelta(minutes=40)
    monitor.record_success("soccer_fixtures", observed_at=observed)
    monitor.record_failure("soccer_fixtures", TimeoutError("provider timeout"))
    state = monitor.get_status()["pipelines"]["soccer_fixtures"]
    assert state["status"] == "degraded"
    assert state["last_success"] == observed.isoformat()
    assert state["last_success_mins_ago"] >= 40
    for _ in range(2):
        monitor.record_failure("soccer_fixtures", TimeoutError("provider timeout"))
    assert monitor.get_status()["pipelines"]["soccer_fixtures"]["status"] == "down"


def test_empty_fresh_schedule_replaces_previous_games():
    loader = Mock(side_effect=[([_fixture()], None, "api", ""), ([], None, "api", "")])
    brain = _brain(loader)
    assert brain.safe_fetch_fixtures(39)
    assert brain.safe_fetch_fixtures(39) == []
    assert brain._fixture_refresh_outcomes[39]["status"] == "success"
    assert brain._last_successful_fixtures[39] == []


def test_cached_fallback_preserves_observation_and_last_successful_cycle():
    loader = Mock(side_effect=[([_fixture()], None, "api", ""), TimeoutError("offline")])
    brain = _brain(loader)
    first = brain.refresh_cycle(39, min_interval_seconds=0)
    observed = brain._fixture_observed_at[39]
    last_success = brain._last_refresh_at
    second = brain.refresh_cycle(39, min_interval_seconds=0)
    assert first["status"] == "success"
    assert second["status"] == "degraded"
    assert second["observed_at"] == observed.isoformat()
    assert brain._last_refresh_at == last_success
    assert brain.get_system_health()["api_status"] == "degraded"


def test_expired_fallback_is_unavailable_and_healthy_league_does_not_hide_failure():
    fixture = _fixture()
    loader = Mock(side_effect=[([fixture], None, "api", ""), TimeoutError("offline"), ([], None, "api", "")])
    brain = _brain(loader)
    brain.safe_fetch_fixtures(39)
    brain._fixture_observed_at[39] = datetime.now(timezone.utc) - timedelta(hours=3)
    assert brain.safe_fetch_fixtures(39) == []
    assert brain._fixture_refresh_outcomes[39]["status"] == "failed"
    assert brain.safe_fetch_fixtures(140) == []
    assert brain.get_system_health()["api_status"] == "degraded"
    assert brain.get_system_health()["data_freshness"] is None


@pytest.mark.parametrize("failure", [TimeoutError("result provider offline"), {"checked": 2, "failed": 1, "errors": ["offline"]}])
def test_result_refresh_failure_is_not_success_even_with_fresh_fixtures(failure):
    refresh = Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure)
    brain = _brain(lambda _league: ([], None, "api", ""), refresh_results=refresh)
    outcome = brain.refresh_cycle(39, min_interval_seconds=0)
    assert outcome["status"] != "success"
    assert brain._last_refresh_at is None
    assert brain.get_system_health()["result_refresh"]["status"] == "failed"


def test_scheduler_all_failed_leagues_cannot_report_success(monkeypatch):
    brain = _brain(Mock(side_effect=TimeoutError("all providers offline")))
    sched, monitor = _start_scheduler(monkeypatch, brain)
    try:
        sched.get_job("ml_predictions").func()
        state = monitor.get_status()["pipelines"]["ml_predictions"]
        assert state["status"] == "degraded"
        assert state["last_success"] is None
        assert len(brain._fixture_refresh_outcomes) > 1
    finally:
        scheduler.stop()


def test_scheduler_partial_failure_cannot_be_hidden_by_later_success(monkeypatch):
    def loader(league):
        if league == 39:
            raise TimeoutError("first league offline")
        return [], None, "api", ""
    sched, monitor = _start_scheduler(monkeypatch, _brain(loader))
    try:
        sched.get_job("ml_predictions").func()
        state = monitor.get_status()["pipelines"]["ml_predictions"]
        assert state["status"] == "degraded"
        assert state["last_success"] is None
        assert "39" in state["error"]
    finally:
        scheduler.stop()


def test_scheduler_empty_soccer_and_nba_schedules_can_be_healthy(monkeypatch):
    observed = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    metadata = {"status": "success", "observed_at": observed}
    soccer = SimpleNamespace(get_fixture_cards=lambda _league: ([], [], None, "api", metadata))
    nba = SimpleNamespace(get_upcoming_games=lambda *_args: [], get_schedule_refresh_status=lambda: metadata)
    sched, monitor = _start_scheduler(monkeypatch, None, prediction_service=soccer, nba=nba)
    try:
        sched.get_job("soccer_fixtures").func()
        sched.get_job("nba_games").func()
        for pipeline in ("soccer_fixtures", "nba_games"):
            state = monitor.get_status()["pipelines"][pipeline]
            assert state["status"] == "ok"
            assert state["last_success"] == observed
            assert state["last_success_mins_ago"] >= 15
    finally:
        scheduler.stop()


def test_scheduler_injury_refresh_cannot_hide_a_failed_feed(monkeypatch):
    from services import injury_reports
    observed = datetime.now(timezone.utc).isoformat()
    outcomes = [{"status": "success", "observed_at": observed, "error": None}]
    monkeypatch.setattr(injury_reports, "refresh_all", lambda: list(outcomes))
    sched, monitor = _start_scheduler(monkeypatch, None)
    try:
        job = sched.get_job("injury_reports")
        job.func()
        assert monitor.get_status()["pipelines"]["injury_reports"]["status"] == "ok"
        outcomes.append({"status": "failed", "observed_at": None, "error": "nfl injury feed is unavailable"})
        job.func()
        state = monitor.get_status()["pipelines"]["injury_reports"]
        assert state["status"] == "degraded"
        assert "nfl" in state["error"]
    finally:
        scheduler.stop()


def test_started_fixture_does_not_generate_a_new_pregame_decision():
    fixture = _fixture()
    fixture["fixture"]["date"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    engine = Mock()
    brain = MatchBrain(load_fixtures=lambda _league: ([], None, "api", ""),
                       get_fixture_by_id=lambda _key: fixture, decision_engine=engine)
    canonical = brain.get_match_analysis(42)
    engine.build_decision.assert_not_called()
    assert canonical["action"] == canonical["prediction"]["action"] == "SKIP"
    assert canonical["probabilities"] == canonical["prediction"]["probabilities"] == {}
    assert canonical["confidence"] == canonical["prediction"]["confidence"] == 0


def test_cached_decision_loses_recommendation_at_kickoff(monkeypatch):
    import services.prediction_lifecycle as lifecycle
    fixture = _fixture()
    brain = MatchBrain(load_fixtures=lambda _league: ([], None, "api", ""),
                       get_fixture_by_id=lambda _key: fixture, decision_engine=DecisionEngine())
    first = brain.get_match_analysis(42)
    assert first["action"] == "CONSIDER"
    kickoff = datetime.fromisoformat(fixture["fixture"]["date"])

    class AtKickoff(datetime):
        @classmethod
        def now(cls, tz=None):
            return kickoff.astimezone(tz) if tz else kickoff.replace(tzinfo=None)

    monkeypatch.setattr(lifecycle, "datetime", AtKickoff)
    expired = brain.get_match_analysis(42)
    assert expired["action"] == expired["prediction"]["action"] == "SKIP"
    assert not expired["pregame_available"]
    assert first["action"] == "CONSIDER"  # Locked earlier snapshots are not rewritten.


def test_match_brain_uses_new_york_calendar_near_utc_midnight():
    fixture = _fixture()
    fixture["fixture"]["date"] = "2026-10-17T03:00:00+00:00"
    now = datetime(2026, 10, 16, 23, tzinfo=timezone.utc)
    assert _brain(lambda _league: ([], None, "api", "")).get_date_bucket(fixture, now) == "today"
