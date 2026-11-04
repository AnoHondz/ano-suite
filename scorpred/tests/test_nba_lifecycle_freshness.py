"""NBA recommendations require future fixtures and genuinely fresh inputs."""
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

import app as module
import nba_live_client as nc
import nba_routes
from services.data_freshness import metadata
from services.prediction_lifecycle import is_pregame, sanitize_decision


@pytest.mark.parametrize("state,name,description,completed,eligible", [
    ("pre", "STATUS_SCHEDULED", "Scheduled", False, True),
    ("in", "STATUS_IN_PROGRESS", "In Progress", False, False),
    ("post", "STATUS_FINAL", "Final", True, False),
    ("pre", "STATUS_POSTPONED", "Postponed", False, False),
    ("pre", "STATUS_SCHEDULED", "Scheduled", True, False),
    ("pre", "STATUS_IN_PROGRESS", "In Progress", False, False),
])
def test_espn_display_time_is_not_a_machine_status(state, name, description, completed, eligible):
    now = datetime.now(UTC)
    kickoff = now + timedelta(hours=4)
    status = nc._event_status({"type": {"state": state, "name": name,
        "description": description, "completed": completed,
        "shortDetail": "10/21 - 7:00 PM EDT", "detail": "Wed, October 21st at 7:00 PM EDT"}})
    assert status["short"] == name
    assert status["short_detail"] == "10/21 - 7:00 PM EDT"
    assert is_pregame(kickoff, status, now) is eligible
    # Exercise the same serialized boundary consumed by the browser.
    card = {"matchId": "status-fixture", "matchDate": kickoff.isoformat(),
            "eventStatus": status, "dataFreshness": metadata("ESPN", now.isoformat(), 60, now=now),
            "action": "CONSIDER", "side": "Home", "confidence": 65}
    assert sanitize_decision(card, now)["pregameAvailable"] is eligible


@pytest.fixture
def nba_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(nc, "NBA_CACHE_DIR", tmp_path)
    monkeypatch.setattr(nc, "EXTERNAL_API_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(nc, "PAGE_RENDER_RETRY_ATTEMPTS", 1)
    nc._MEM_CACHE.clear()
    nc._SCOREBOARD_REFRESH.clear()
    monkeypatch.setattr(nc, "_SCHEDULE_REFRESH", {"status": "unknown", "observed_at": None})
    return tmp_path


def cached(endpoint, age, params=None, payload=None):
    path = nc._cache_path(f"espn:{endpoint}", params)
    path.write_text(json.dumps(payload or {"events": []}), encoding="utf-8")
    observed = datetime.now(UTC).timestamp() - age
    os.utime(path, (observed, observed))
    return observed


def fixture(kickoff=None, state="pre"):
    return {"id": "nba-fixture", "date": {"start": (kickoff or datetime.now(UTC) + timedelta(days=1)).isoformat()},
            "status": {"state": state}, "teams": {"home": {"id": "1", "name": "Boston Celtics"},
            "visitors": {"id": "2", "name": "Miami Heat"}}}


def test_scoreboard_stale_fallback_preserves_observation_and_never_rejuvenates(nba_cache):
    observed = cached("scoreboard", 120)
    first = nc._espn_get("scoreboard", ttl_seconds=60)
    second = nc._espn_get("scoreboard", ttl_seconds=60)
    assert first["_scorpred_source"]["state"] == "degraded"
    assert first["_scorpred_source"] == second["_scorpred_source"]
    assert datetime.fromisoformat(first["_scorpred_source"]["observed_at"]).timestamp() == pytest.approx(observed, abs=.001)


@pytest.mark.parametrize("endpoint,ttl,age", [("scoreboard", 60, 301), ("teams/1/schedule", 3600, 21601)])
def test_expired_provider_snapshot_is_unavailable(nba_cache, endpoint, ttl, age):
    cached(endpoint, age)
    with pytest.raises(RuntimeError, match="request failed"):
        nc._espn_get(endpoint, ttl_seconds=ttl)


def test_delayed_form_cannot_feed_prediction_even_inside_display_bound(nba_cache):
    cached("teams/1/schedule", 7200, {"season": 2027})
    with pytest.raises(RuntimeError, match="delayed team scores"):
        nc._completed_team_games_for_season("1", 2027)


@pytest.mark.parametrize("age,state", [(0, "pre"), (1, "pre"), (-3600, "in"), (-3600, "post")])
def test_started_or_nonpregame_fixture_never_loads_form(monkeypatch, age, state):
    game = fixture(datetime.now(UTC) - timedelta(seconds=age), state)
    monkeypatch.setattr(nba_routes.nc, "get_team_recent_form_context", lambda *a, **kw: pytest.fail("No form fetch for a started fixture"))
    monkeypatch.setattr(nba_routes.se, "scorpred_predict", lambda *a, **kw: pytest.fail("No prediction after kickoff"))
    result = nba_routes._build_upcoming_prediction_card(game, {}, {})
    assert result["prediction"] is None
    assert "Pregame" in result["unavailable_reason"]


def test_degraded_schedule_blocks_estimate_before_form_load(monkeypatch):
    game = fixture()
    game["data_freshness"] = metadata("ESPN", (datetime.now(UTC) - timedelta(minutes=2)).isoformat(), 60, 300, degraded=True)
    monkeypatch.setattr(nba_routes.nc, "get_team_recent_form_context", lambda *a, **kw: pytest.fail("Delayed schedule cannot generate a prediction"))
    assert nba_routes._build_upcoming_prediction_card(game, {}, {})["prediction"] is None


def test_fresh_empty_schedule_reports_success_and_source_observation(monkeypatch, nba_cache):
    def empty_day(target_date, request_profile):
        nc._SCOREBOARD_REFRESH[nc._ymd(target_date)] = metadata("ESPN", datetime.now(UTC).isoformat(), 60, 300)
        return []
    monkeypatch.setattr(nc, "get_scoreboard_games", empty_day)
    assert nc.get_upcoming_games(12, 2, "page") == []
    status = nc.get_schedule_refresh_status()
    assert status["status"] == "success"
    assert status["requested_days"] == 3
    assert status["observed_at"] is not None


def test_partial_schedule_failure_reports_degraded(monkeypatch, nba_cache):
    counter = []
    def partial(target_date, request_profile):
        counter.append(target_date)
        if len(counter) == 1:
            raise RuntimeError("Provider down")
        nc._SCOREBOARD_REFRESH[nc._ymd(target_date)] = metadata("ESPN", datetime.now(UTC).isoformat(), 60, 300)
        return []
    monkeypatch.setattr(nc, "get_scoreboard_games", partial)
    assert nc.get_upcoming_games(12, 2, "page") == []
    assert nc.get_schedule_refresh_status()["status"] == "degraded"
    assert nc.get_schedule_refresh_status()["failed_days"] == 1


def test_nba_dashboard_groups_after_midnight_utc_into_eastern_evening(monkeypatch):
    game = fixture(datetime(2099, 10, 18, 1, tzinfo=UTC), "in")
    monkeypatch.setattr(nc, "get_upcoming_games", lambda *a: [game])
    monkeypatch.setattr(nc, "get_teams", list)
    monkeypatch.setattr(nc, "get_standings", dict)
    response = module.app.test_client().get("/api/dashboard/nba?date=2099-10-17")
    assert response.status_code == 200
    assert response.json["availableDates"] == ["2099-10-17"]
    assert response.json["calendarTimezone"] == "America/New_York"
    assert response.json["slate"][0]["action"] == "SKIP"
    assert response.json["slate"][0]["side"] == "Unavailable"
    assert nba_routes._nba_calendar_parts(game["date"]["start"]) == ("2099-10-17", "21:00")


def test_nba_scoring_rejects_live_fixture_before_model_inputs(monkeypatch):
    game = fixture(state="in")
    monkeypatch.setattr(nba_routes.nc, "get_event_snapshot", lambda *a: game)
    monkeypatch.setattr(nba_routes.nc, "get_team_recent_form", lambda *a, **kw: pytest.fail("No pregame model input after start"))
    response = module.app.test_client().get("/api/scoreline/nba?team_a_id=1&team_b_id=2&event_id=nba-fixture")
    assert response.status_code == 400
    assert "Pregame" in response.json["message"]
