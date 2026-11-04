"""Soccer source clocks survive cache restoration and have bounded fallback ages."""
import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

import api_client as ac
import football_data_client as fd
from services.data_freshness import SOURCE_KEY, metadata


def test_date_empty_schedule_records_success_only_from_both_observed_days(monkeypatch):
    monkeypatch.setattr(ac, '_FD_AVAILABLE', False)
    monkeypatch.setattr(ac, '_refresh_context', threading.local())
    monkeypatch.setattr(ac, 'api_get', lambda *a, **k: {})
    info = metadata('offline ESPN test', datetime.now(UTC), 120, 900)
    monkeypatch.setattr(ac, '_espn_get_json', lambda *a, **k: {'events':[], SOURCE_KEY:info})
    assert ac.get_fixtures_for_date(39,2026,'2026-10-28') == []
    assert ac.get_last_refresh_status(39)['status'] == 'success'
    observed = ac.get_last_refresh_status(39)['observed_at']
    assert observed == info['observed_at']
    monkeypatch.setattr(ac, '_espn_get_json', lambda *a, **k: {})
    assert ac.get_fixtures_for_date(39,2026,'2026-10-29') == []
    assert ac.get_last_refresh_status(39)['status'] == 'failed'


def test_football_data_batch_past_the_day_is_a_verified_empty_day(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(ac, '_refresh_context', threading.local())
    monkeypatch.setattr(ac, '_FD_AVAILABLE', True)
    status = {'status': 'success', 'observed_at': '2026-11-01T21:00:00+00:00', 'warning': None}
    batch = [{'fixture': {'id': 9, 'date': '2026-11-07T14:30:00+00:00'}}]
    monkeypatch.setattr(ac, '_fd', SimpleNamespace(get_upcoming_fixtures=lambda *a: batch, get_last_refresh_status=lambda: status))
    monkeypatch.setattr(ac, 'api_get', lambda *a, **k: pytest.fail('keyless API-Football must not be asked'))
    monkeypatch.setattr(ac, '_espn_get_json', lambda *a, **k: pytest.fail('ESPN must not be asked'))
    assert ac.get_fixtures_for_date(78, 2026, '2026-11-02') == []
    assert ac.get_last_refresh_status(78)['status'] == 'success'
    # A batch that ends before the day proves nothing, so the fallbacks still run.
    monkeypatch.setattr(ac, 'api_get', lambda *a, **k: {})
    monkeypatch.setattr(ac, '_espn_get_json', lambda *a, **k: {})
    assert ac.get_fixtures_for_date(78, 2026, '2026-11-20') == []
    assert ac.get_last_refresh_status(78)['status'] == 'failed'


def test_date_partial_fetch_is_not_successful_empty_schedule(monkeypatch):
    monkeypatch.setattr(ac, '_FD_AVAILABLE', False)
    monkeypatch.setattr(ac, '_refresh_context', threading.local())
    monkeypatch.setattr(ac, 'api_get', lambda *a, **k: {})
    info = metadata('offline ESPN test', datetime.now(UTC), 120, 900)
    payloads=iter([{'events':[], SOURCE_KEY:info}, {}])
    monkeypatch.setattr(ac, '_espn_get_json', lambda *a, **k: next(payloads))
    assert ac.get_fixtures_for_date(39,2026,'2026-10-28') == []
    assert ac.get_last_refresh_status(39)['status'] == 'degraded'


@pytest.fixture
def providers(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "CACHE_DIR", tmp_path / "api")
    monkeypatch.setattr(ac, "API_KEY", "test")
    monkeypatch.setattr(ac, "FORCE_REFRESH", False)
    monkeypatch.setattr(ac, "_API_FOOTBALL_SUSPENDED", False)
    monkeypatch.setattr(ac, "_USING_FREE_API", False)
    monkeypatch.setattr(ac, "_USING_DIRECT_APISPORTS", False)
    monkeypatch.setattr(ac, "_FD_AVAILABLE", False)
    monkeypatch.setattr(ac, "EXTERNAL_API_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(ac, "_refresh_context", threading.local())
    monkeypatch.setattr(ac, "_memory_cache", {})
    monkeypatch.setattr(ac, "_team_fixtures_cache", {})
    monkeypatch.setattr(ac, "_FORBIDDEN_ENDPOINTS", set())
    monkeypatch.setattr(ac, "_RATE_LIMITED_ENDPOINTS", {})
    monkeypatch.setattr(fd, "_CACHE_DIR", tmp_path / "fd")
    monkeypatch.setattr(fd, "_MEM_CACHE", {})
    monkeypatch.setattr(fd, "_REFRESH_CONTEXT", threading.local())
    monkeypatch.setattr(fd, "FD_ENABLED", True)
    monkeypatch.setattr(fd, "_circuit_is_open", lambda: True)


def _cached(path, payload, age):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    observed = time.time() - age
    os.utime(path, (observed, observed))
    return path.stat().st_mtime


def test_api_disk_restore_keeps_source_time(providers):
    params = {"league": 39, "next": 5}
    observed = _cached(ac._cache_path("fixtures", params), {"response": []}, 45)
    payload = ac.api_get("fixtures", params)
    assert payload[SOURCE_KEY]["state"] == "fresh"
    assert ac._memory_cache[ac._make_cache_key("fixtures", params)]["ts"] == observed
    assert datetime.fromisoformat(payload[SOURCE_KEY]["observed_at"]).timestamp() == pytest.approx(observed)


@pytest.mark.parametrize(("age", "available"), [(240, True), (2000, False)])
def test_api_disk_error_fallback_is_bounded_and_degraded(providers, age, available):
    params = {"league": 39, "next": 5}
    observed = _cached(ac._cache_path("fixtures", params), {"response": [{"fixture": {"id": 1}}]}, age)
    payload = ac.api_get("fixtures", params)
    if available:
        assert payload[SOURCE_KEY]["state"] == "degraded"
        assert payload["response"][0]["data_freshness"]["state"] == "degraded"
        assert ac._memory_cache[ac._make_cache_key("fixtures", params)]["ts"] == observed
    else:
        assert payload == {}
        assert ac.get_last_refresh_status()["status"] == "failed"


def test_football_data_disk_restore_does_not_restart_memory_ttl(providers):
    params = {"status": "SCHEDULED"}
    key = fd._cache_key("competitions/2021/matches", params)
    observed = _cached(fd._cache_path(key), {"matches": []}, 100)
    payload = fd.fd_get("competitions/2021/matches", params)
    assert payload[SOURCE_KEY]["state"] == "fresh"
    assert fd._MEM_CACHE[key]["ts"] == observed


@pytest.mark.parametrize(("age", "available"), [(600, True), (3600, False)])
def test_football_data_circuit_fallback_has_maximum_age(providers, age, available):
    endpoint = "competitions/2021/matches"
    _cached(fd._cache_path(fd._cache_key(endpoint, {})), {"matches": []}, age)
    payload = fd.fd_get(endpoint)
    assert bool(payload) is available
    if available:
        assert payload[SOURCE_KEY]["state"] == "degraded"
        assert fd.get_last_refresh_status()["status"] == "degraded"
    else:
        assert fd.get_last_refresh_status()["status"] == "failed"


@pytest.mark.parametrize(("age", "available"), [(300, True), (2000, False)])
def test_espn_failed_schedule_refresh_never_serves_unbounded_disk(providers, age, available):
    key = "scoreboard:39:20261017"
    _cached(ac._espn_cache_key("espn", (key,)), {"events": []}, age)
    if available:
        result = ac._espn_get_json("https://example.invalid/scoreboard", key, ttl_hours=0.5)
        assert result[SOURCE_KEY]["state"] == "degraded"
    else:
        with pytest.raises(RuntimeError, match="ESPN request failed"):
            ac._espn_get_json("https://example.invalid/scoreboard", key, ttl_hours=0.5)


def test_form_outer_cache_cannot_extend_expired_source(providers, monkeypatch):
    monkeypatch.setattr(fd, "FD_ENABLED", False)
    old = datetime.now(UTC) - timedelta(hours=2)
    fixture = {"fixture": {"id": 1}, "data_freshness": metadata("espn", old, 3600, 21600)}
    ac._team_fixtures_cache[(1, 39, 2026, 10)] = {"data": [fixture], "ts": time.time()}
    fallback = Mock(return_value=[])
    monkeypatch.setattr(ac, "api_get", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(ac, "_espn_schedule", lambda *_args: [])
    monkeypatch.setattr(ac, "_espn_scoreboard_team_fixtures", fallback)
    assert ac.get_team_fixtures(1, 39, 2026, 10) == []
    fallback.assert_called_once()


def test_empty_schedule_success_and_partial_failure_are_distinct(providers, monkeypatch):
    monkeypatch.setattr(ac, "api_get", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(ac, "_espn_get_json", lambda *_args, **_kwargs: ac._source_payload({"events": []}, "espn", time.time(), 120, 900))
    assert ac.get_upcoming_fixtures(39, 2026, 1) == []
    assert ac.get_last_refresh_status(39)["status"] == "success"
    original = ac.get_last_refresh_status(39)["observed_at"]
    ac._source_payload({}, "other", time.time(), 120, 900)
    assert ac.get_last_refresh_status(39)["observed_at"] == original

    def partial(_url, key, **_kwargs):
        if key.endswith(datetime.now(UTC).strftime("%Y%m%d")):
            return {}
        return ac._source_payload({"events": []}, "espn", time.time(), 120, 900)

    monkeypatch.setattr(ac, "_espn_get_json", partial)
    assert ac.get_upcoming_fixtures(39, 2026, 1) == []
    assert ac.get_last_refresh_status(39)["status"] == "degraded"


def test_calendar_query_fetches_both_utc_dates_and_filters_new_york(providers, monkeypatch):
    dates = []

    def fixtures(_endpoint, params, **_kwargs):
        dates.append(params["date"])
        rows = [{"fixture": {"id": 1, "date": "2026-10-17T23:00:00Z"}}] if params["date"] == "2026-10-17" else [
            {"fixture": {"id": 2, "date": "2026-10-18T03:00:00Z"}},
            {"fixture": {"id": 3, "date": "2026-10-18T05:00:00Z"}},
        ]
        return {"response": rows}

    monkeypatch.setattr(ac, "api_get", fixtures)
    result = ac.get_fixtures_for_date(39, 2026, "2026-10-17")
    assert dates == ["2026-10-17", "2026-10-18"]
    assert [row["fixture"]["id"] for row in result] == [1, 2]


def test_football_data_retains_actual_historical_season_and_cancelled_status(providers):
    match = {"id": 1, "status": "CANCELLED", "season": {"startDate": "2025-08-01"},
             "homeTeam": {"id": 1}, "awayTeam": {"id": 2}, SOURCE_KEY: {"state": "degraded", "observed_at": "2026-10-17T00:00:00Z"}}
    result = fd._normalize_fd_match(match, 39)
    assert result["league"]["season"] == 2025
    assert result["fixture"]["status"]["short"] == "CANC"
    assert result["data_freshness"]["state"] == "degraded"
    assert ac._espn_fixture_status("pre", "STATUS_POSTPONED")["short"] == "PST"
