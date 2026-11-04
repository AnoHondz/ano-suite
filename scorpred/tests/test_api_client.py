"""Tests for API client suppression and rate-limit recovery behavior."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import api_client


def test_fd_head_to_head_uses_completed_history_without_cross_provider_ids(monkeypatch):
    from types import SimpleNamespace
    def fixture(identifier, home, away, status="FT"):
        return {"fixture": {"id": identifier, "date": f"2026-10-{identifier:02d}T15:00Z", "status": {"short": status}},
                "teams": {"home": {"id": home}, "away": {"id": away}}}
    rows = [fixture(1, 64, 397), fixture(2, 397, 64), fixture(3, 64, 66), fixture(4, 64, 397, "NS")]
    monkeypatch.setattr(api_client, "_FD_AVAILABLE", True)
    monkeypatch.setattr(api_client, "get_teams", lambda league: [
        {"team": {"id": 64, "name": "Liverpool"}}, {"team": {"id": 397, "name": "Brighton"}}])
    monkeypatch.setattr(api_client, "_fd", SimpleNamespace(get_team_recent_matches=lambda team, last: rows))
    def unexpected(*args, **kwargs):
        raise AssertionError("Football-data IDs must not reach another provider")
    monkeypatch.setattr(api_client, "api_get", unexpected)
    monkeypatch.setattr(api_client, "_espn_schedule", unexpected)
    assert [f["fixture"]["id"] for f in api_client.get_h2h(64, 397, last=1)] == [2]
    monkeypatch.setattr(api_client, "_fd", SimpleNamespace(
        get_team_recent_matches=lambda team, last: [fixture(3, 64, 66)]))
    assert api_client.get_h2h(64, 397) == []


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.trust_env = False
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.response


def _reset_api_client_state(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(api_client, "API_KEY", "test-key")
    monkeypatch.setattr(api_client, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(api_client, "_USING_FREE_API", False)
    monkeypatch.setattr(api_client, "API_BASE", "https://api-football-v1.p.rapidapi.com/v3")
    api_client._memory_cache.clear()
    api_client._FORBIDDEN_ENDPOINTS.clear()
    api_client._RATE_LIMITED_ENDPOINTS.clear()


def test_api_get_skips_rate_limited_endpoint_during_cooldown(tmp_path, monkeypatch):
    _reset_api_client_state(tmp_path, monkeypatch)

    class _NoNetworkSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, *args, **kwargs):
            raise AssertionError("network call should be suppressed during cooldown")

    api_client._RATE_LIMITED_ENDPOINTS["injuries"] = time.time() + 60
    monkeypatch.setattr(api_client.requests, "Session", lambda: _NoNetworkSession())

    result = api_client.api_get("injuries", {"team": 10, "league": 39, "season": 2026})

    assert result["status"] == "fail"
    assert result["rate_limited"] is True


def test_api_get_sets_endpoint_cooldown_after_429(tmp_path, monkeypatch):
    _reset_api_client_state(tmp_path, monkeypatch)
    session = _FakeSession(_FakeResponse(429))
    monkeypatch.setattr(api_client.requests, "Session", lambda: session)

    before = time.time()
    result = api_client.api_get("injuries", {"team": 10, "league": 39, "season": 2026})

    assert result["status"] == "fail"
    assert result["rate_limited"] is True
    assert session.calls == 1
    assert api_client._RATE_LIMITED_ENDPOINTS["injuries"] >= before
