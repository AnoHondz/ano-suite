from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest

import app as flask_app_module
import decision_ui as dui
import nba_routes
import security
from db_models import db
from db_models import User
import user_auth
from services import bets_service
from services import cache_service
from tests.source_contracts import fresh_source


def _sample_fixture(match_id: int = 9001) -> dict:
    return {
        "data_freshness": fresh_source(),
        "fixture": {"id": match_id, "date": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        "teams": {
            "home": {"id": 1, "name": "AFC Bournemouth", "logo": ""},
            "away": {"id": 2, "name": "Leeds", "logo": ""},
        },
        "league": {"id": 39, "name": "Premier League"},
    }


def _sample_analysis(match_id: int = 9001) -> dict:
    return {
        "match_id": str(match_id),
        "matchup": "AFC Bournemouth vs Leeds",
        "confidence": 72,
        "probabilities": {"a": 52, "draw": 24, "b": 24},
        "action": "BET",
        "recommended_side": "AFC Bournemouth",
        "reason": "Strong Data edge",
        "data_quality": "Strong Data",
        "metric_breakdown": None,
    }


@pytest.fixture
def client(monkeypatch):
    flask_app_module.app.config["TESTING"] = True
    flask_app_module.app.config["SECRET_KEY"] = "test-secret"
    flask_app_module.app.config["WTF_CSRF_ENABLED"] = False
    flask_app_module.app.config["TRACKING_LAST_BOOTSTRAP"] = date.today().strftime("%Y-%m-%d")
    flask_app_module.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    security.reset_chat_rate_limits()
    with flask_app_module.app.app_context():
        db.drop_all()
        db.create_all()

    fixture = _sample_fixture()
    monkeypatch.setattr(
        flask_app_module,
        "_build_home_dashboard_context",
        lambda: {
            "system_snapshot": {"tracked_predictions": 0},
            "trust_cards": [],
            "all_cards": [],
            "top_picks": [],
            "soccer_picks": [],
            "nba_picks": [],
            "insight_cards": [],
            "data_mix": {},
            "today_plan": {"bet": 0, "consider": 0, "skip": 0},
            "soccer_plan": {"bet": 0, "consider": 0, "skip": 0},
            "nba_plan": {"bet": 0, "consider": 0, "skip": 0},
        },
    )
    monkeypatch.setattr(flask_app_module.ac, "get_teams", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        flask_app_module,
        "load_fixtures_cached",
        lambda _league_id: ([fixture], None, "mock", ""),
    )
    monkeypatch.setattr(flask_app_module, "analyze_match", lambda _match_id: _sample_analysis())
    monkeypatch.setattr(
        flask_app_module,
        "_load_grouped_upcoming_fixtures_all_leagues",
        lambda **_kwargs: ([fixture], [{"fixtures": [fixture], "league_id": 39, "league_name": "Premier League", "league_flag": ""}], None, "mock"),
    )
    monkeypatch.setattr(flask_app_module, "_tracker_result_rows", lambda **_kwargs: [])
    cache_service._local_cache.clear()
    flask_app_module.prediction_service.configure(
        analyze_match=flask_app_module.analyze_match,
        load_fixtures=flask_app_module.load_fixtures_cached,
        card_from_fixture=flask_app_module._soccer_card_from_fixture_analysis,
        top_opportunities=lambda cards, limit: dui.top_opportunities(cards, limit=limit),
        plan_summary=dui.plan_summary,
    )
    monkeypatch.setattr(nba_routes, "_index_inner", lambda: "NBA Smoke")
    monkeypatch.setattr(nba_routes, "_prediction_inner", lambda: "NBA Prediction Smoke")

    with flask_app_module.app.test_client() as c:
        with flask_app_module.app.app_context():
            user_auth._save_user({"email": "smoke@example.test", "password_hash": user_auth._hash_password("example-password")})
        with c.session_transaction() as sess:
            sess[user_auth.USER_SESSION_KEY] = "smoke@example.test"
        yield c


@pytest.mark.parametrize(
    ("path", "expected_status", "needle"),
    [
        ("/", 200, "ScorPred"),
        ("/soccer", 200, "AFC Bournemouth"),
        ("/fixtures", 200, "AFC Bournemouth"),
        ("/today-soccer-predictions", 200, "AFC Bournemouth"),
        ("/insights", 200, "Insights"),
        ("/prediction", 200, ""),
        ("/matchup", 200, ""),
        ("/nba", 200, "NBA Smoke"),
        ("/nba/prediction", 200, "NBA Prediction Smoke"),
        ("/my-bets", 200, "My Bets"),
        ("/performance", 200, "Performance"),
        ("/alerts", 200, "Alerts"),
        ("/watchlist", 200, "Watchlist"),
        ("/settings", 200, "Settings"),
        ("/health", 200, ""),
    ],
)
def test_smoke_routes(client, path, expected_status, needle):
    response = client.get(path, follow_redirects=True)
    assert response.status_code == expected_status
    text = response.get_data(as_text=True)
    if needle:
        assert needle in text
    assert "Traceback" not in text
    assert "Internal Server Error" not in text
    assert "jinja2.exceptions" not in text


def test_system_intelligence_route_smoke(client):
    payload = {
        "model_metrics": {"trust_score": 68, "calibration_score": 65, "win_rate": 55},
        "calibration": {"buckets": []},
        "system_health": {
            "api_status": "ok",
            "last_refresh_time": None,
            "data_freshness": None,
            "error_count": 0,
            "degraded_mode": False,
        },
        "drift": {"drift_detected": False, "severity": "LOW", "reason": "stable performance", "short_window_metrics": {}, "long_window_metrics": {}},
        "decision_quality": {"bet_count": 0, "consider_count": 0, "skip_count": 0, "high_confidence_accuracy": None},
        "safeguards": {"fallback_data_used": False, "stale_data_served": False, "trust_downgraded": False},
    }
    with patch.object(flask_app_module, "_MATCH_BRAIN") as brain:
        brain.get_system_intelligence.return_value = payload
        brain.refresh_cycle.return_value = None
        response = client.get("/system-intelligence", follow_redirects=True)
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "System Intelligence" in text
    assert "Traceback" not in text
    assert "Internal Server Error" not in text


def test_health_route_returns_structured_status(client):
    with patch.object(flask_app_module, "_MATCH_BRAIN") as brain:
        brain.get_system_health.return_value = {
            "degraded_mode": False,
            "last_refresh_time": "2026-04-24T12:00:00Z",
            "error_count": 2,
        }
        response = client.get("/health")
    assert response.status_code == 200
    payload = response.get_json()
    for key in ("status", "app", "db", "cache", "api", "degraded_mode", "last_refresh", "error_count", "timestamp"):
        assert key in payload


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/soccer",
        "/fixtures",
        "/today-soccer-predictions",
        "/insights",
        "/prediction",
        "/nba",
        "/nba/prediction",
        "/my-bets",
        "/performance",
        "/alerts",
        "/watchlist",
        "/settings",
        "/system-intelligence",
    ],
)
def test_pages_do_not_render_fake_fallback_values(client, path):
    response = client.get(path, follow_redirects=True)
    assert response.status_code in (200, 302, 303)
    text = response.get_data(as_text=True)
    assert "53%" not in text
    assert "38 / 26 / 36" not in text
    assert "50/50" not in text


def test_pipeline_consistency_regression(client, monkeypatch):
    fixture = _sample_fixture(match_id=42)
    flask_app_module._FIXTURE_INDEX.clear()
    flask_app_module._local_fixture_cache.clear()
    flask_app_module._local_match_analysis_cache.clear()
    cache_service._local_cache.clear()

    monkeypatch.setattr(
        flask_app_module,
        "load_fixtures_cached",
        lambda _league_id: ([fixture], None, "mock", ""),
    )
    monkeypatch.setattr(
        flask_app_module,
        "analyze_match",
        lambda _match_id: {
            "match_id": "42",
            "matchup": "AFC Bournemouth vs Leeds",
            "confidence": 72,
            "probabilities": {"a": 52, "draw": 24, "b": 24},
            "action": "BET",
            "recommended_side": "AFC Bournemouth",
            "reason": "Strong Data",
            "data_quality": "Strong Data",
            "metric_breakdown": None,
        },
    )
    flask_app_module.prediction_service.configure(
        analyze_match=flask_app_module.analyze_match,
        load_fixtures=flask_app_module.load_fixtures_cached,
        card_from_fixture=flask_app_module._soccer_card_from_fixture_analysis,
        top_opportunities=lambda cards, limit: dui.top_opportunities(cards, limit=limit),
        plan_summary=dui.plan_summary,
    )

    response = client.get("/soccer")
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "AFC Bournemouth" in text
    assert "72%" in text
    assert "H 52 /" in text
    assert "D 24 /" in text
    assert "A 24" in text
    assert "Visual Read:</strong> Unavailable" in text
    assert "53%" not in text
    assert "38 / 26 / 36" not in text
    assert "50/50" not in text


def test_soccer_analysis_cache_guard(client, monkeypatch):
    fixture = _sample_fixture(match_id=7)
    calls = {"count": 0}

    flask_app_module._FIXTURE_INDEX.clear()
    flask_app_module._local_fixture_cache.clear()
    flask_app_module._local_match_analysis_cache.clear()
    cache_service._local_cache.clear()

    monkeypatch.setattr(
        flask_app_module,
        "load_fixtures_cached",
        lambda _league_id: ([fixture], None, "mock", ""),
    )

    def _counting_analyze(_match_id):
        calls["count"] += 1
        return _sample_analysis(match_id=7)

    monkeypatch.setattr(flask_app_module, "analyze_match", _counting_analyze)
    flask_app_module.prediction_service.configure(
        analyze_match=flask_app_module.analyze_match,
        load_fixtures=flask_app_module.load_fixtures_cached,
        card_from_fixture=flask_app_module._soccer_card_from_fixture_analysis,
        top_opportunities=lambda cards, limit: dui.top_opportunities(cards, limit=limit),
        plan_summary=dui.plan_summary,
    )

    first = client.get("/soccer")
    second = client.get("/soccer")
    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["count"] == 1


def test_build_decision_card_is_pure_mapping():
    analysis = {
        "matchup": "Burnley vs Man City",
        "confidence": 81,
        "probabilities": {"a": 10, "draw": 15, "b": 75},
        "action": "BET",
        "recommended_side": "Man City",
        "reason": "Model edge",
        "data_quality": "Strong Data",
        "metric_breakdown": None,
        "match_id": "991",
    }
    card = dui.build_decision_card(analysis=analysis)
    assert card["confidence"] == 81
    assert card["probabilities"] == {"a": 10, "draw": 15, "b": 75}
    assert card["action"] == "BET"


def test_add_to_bets_persists_to_database(client):
    response = client.post(
        "/add-bet",
        json={
            "match_id": "42",
            "matchup": "AFC Bournemouth vs Leeds",
            "recommended_side": "AFC Bournemouth",
            "action": "BET",
            "confidence": 72,
            "probabilities": {"a": 52, "draw": 24, "b": 24},
            "data_quality": "Strong Data",
        },
    )
    assert response.status_code == 200
    bets = bets_service.list_bets(user_id=client.get("/api/session").get_json()["user"]["id"])
    assert len(bets) == 1
    assert bets[0]["match_id"] == "42"


def test_add_bet_rejects_invalid_payload(client):
    response = client.post(
        "/add-bet",
        json={"matchup": "", "recommended_side": "", "action": "bad"},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "error"


def test_add_bet_blocks_duplicates_in_idempotency_window(client):
    payload = {
        "match_id": "dupe-1",
        "matchup": "AFC Bournemouth vs Leeds",
        "recommended_side": "AFC Bournemouth",
        "action": "BET",
        "confidence": 72,
        "probabilities": {"a": 52, "draw": 24, "b": 24},
        "data_quality": "Strong Data",
    }
    first = client.post("/add-bet", json=payload)
    second = client.post("/add-bet", json=payload)
    assert first.status_code == 200
    assert second.status_code == 400
    assert "duplicate" in (second.get_json().get("error") or "").lower()


def test_delete_bet_returns_404_for_missing_row(client):
    response = client.post("/delete-bet/999")
    assert response.status_code == 404
    payload = response.get_json()
    assert payload["status"] == "not_found"


def test_health_degrades_gracefully_when_api_unreachable(client, monkeypatch):
    # Readiness reports cached provider state; it must never call a live API.
    monkeypatch.setattr(flask_app_module.ac, "get_teams", lambda *_args, **_kwargs: pytest.fail("Health must not call provider"))
    monkeypatch.setattr(flask_app_module.MatchBrain, "get_system_health", lambda self: {"api_status": "degraded", "degraded_mode": True})
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] in {"ok", "degraded"}
    assert payload["api"] == "degraded"
    assert payload["degraded_mode"] is True


def test_cache_service_redis_path(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.store = {}

        def ping(self):
            return True

        def get(self, key):
            return self.store.get(key)

        def setex(self, key, _ttl, value):
            self.store[key] = value

        def delete(self, key):
            self.store.pop(key, None)

    fake = FakeRedis()
    monkeypatch.setattr(cache_service, "_redis_client", fake)
    monkeypatch.setattr(cache_service, "_get_redis_client", lambda: fake)
    key = cache_service.make_key("t", 1)
    cache_service.set_json(key, {"ok": True}, ttl=60)
    assert cache_service.get_json(key) == {"ok": True}


def test_cache_service_local_fallback(monkeypatch):
    monkeypatch.setattr(cache_service, "_redis_client", None)
    monkeypatch.setattr(cache_service, "_get_redis_client", lambda: None)
    key = cache_service.make_key("local", 9)
    cache_service.set_json(key, {"v": 1}, ttl=60)
    assert cache_service.get_json(key) == {"v": 1}
