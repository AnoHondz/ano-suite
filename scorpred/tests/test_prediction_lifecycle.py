"""Public recommendations expire independently of their cache's remaining TTL."""
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta

import pytest

import app as app_module
import decision_ui
import nba_routes
import nfl_routes
import odds_fetcher
from services import decision_store
from services import prediction_lifecycle as lifecycle
from services.data_freshness import metadata, worst
from services.decision_engine import DecisionEngine
from services.match_brain import MatchBrain


def future_card(**changes):
    now=datetime.now(UTC)
    evidence={"state":"fresh","observed_at":now.isoformat(),"expires_at":(now+timedelta(hours=1)).isoformat()}
    return {"match_id": "expiry-test", "sport": "soccer", "team_a": "Arsenal", "team_b": "Chelsea",
            "data_freshness": metadata("synthetic provider", datetime.now(UTC).isoformat(), 3600),
            "matchup": "Arsenal vs Chelsea", "game_date": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            "event_status": "NS", "action": "BET", "recommended_side": "Arsenal", "confidence": 72,
            "probabilities": {"a": 72, "draw": 16, "b": 12}, "data_quality": "Strong Data",
            "lineup_evidence":{**evidence,"status":"confirmed"},"injury_evidence":{**evidence,"coverage_complete":True},
            "market":{"home_decimal":2.0,"away_decimal":2.0,"updated_at":now.isoformat()}, **changes}


def test_cached_decision_expires_exactly_at_kickoff(monkeypatch):
    card = future_card()
    kickoff = datetime.fromisoformat(card["game_date"])
    decision_store.publish([card], "soccer")
    assert decision_store.get("soccer", card["match_id"])["action"] == "BET"

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return kickoff

    monkeypatch.setattr(lifecycle, "datetime", Clock)
    expired = decision_store.get("soccer", card["match_id"])
    assert expired["action"] == "SKIP"
    assert expired["winProbability"] is None
    assert expired["side"] == "Unavailable"
    assert "kickoff" in expired["reason"]


@pytest.mark.parametrize("status", ["LIVE", "HT", "FT", "PST", "CANC", {"state": "pre", "completed": True}])
def test_changed_status_with_future_time_is_not_pregame(status):
    card = future_card(event_status=status)
    assert lifecycle.sanitize_decision(card)["action"] == "SKIP"


def test_source_expiry_and_market_expiry_have_independent_deadlines():
    now = datetime.now(UTC)
    source = metadata("schedule", now.isoformat(), 120, 900, now=now)
    card = future_card(data_freshness=source,
                       market={"bookmaker": "Book", "updated_at": (now - timedelta(minutes=16)).isoformat()})
    clean = lifecycle.sanitize_decision(card, now)
    assert clean["action"] == "BET"
    assert clean["market"] is None
    expired = lifecycle.sanitize_decision(card, now + timedelta(seconds=120))
    assert expired["action"] == "SKIP"
    assert "fresh" in expired["reason"]


def test_combined_freshness_uses_earliest_deadline():
    now = datetime.now(UTC)
    form = metadata("form", (now - timedelta(minutes=30)).isoformat(), 3600, 21600, now=now)
    schedule = metadata("schedule", now.isoformat(), 60, 300, now=now)
    combined = worst([form, schedule], now=now)
    assert combined["expires_at"] == schedule["expires_at"]
    assert lifecycle.freshness_state(combined, now + timedelta(seconds=60)) == "degraded"


def test_insights_keep_nfl_scenarios_out_of_rule_confidence(monkeypatch):
    soccer = future_card(confidence=67)
    nba = decision_ui.card_to_decision(future_card(sport="nba", match_id="nba-test", confidence=69))
    nfl = decision_ui.card_to_decision(future_card(sport="nfl", match_id="nfl-test", confidence=100, probabilities={"a": 100, "draw": 0, "b": 0}))
    monkeypatch.setattr(app_module.prediction_service, "get_fixture_cards", lambda _, **kw: ([soccer], [], None, "mock", ""))
    monkeypatch.setattr(nba_routes, "api_dashboard_nba", lambda: app_module.jsonify(slate=[nba]))
    monkeypatch.setattr(nfl_routes, "api_dashboard_nfl", lambda: app_module.jsonify(slate=[nfl]))
    payload = app_module.app.test_client().get("/api/dashboard/insights").json
    assert {card["sport"] for card in payload["radarCards"]} == {"soccer", "nba"}
    assert payload["radarGroups"]["nfl"][0]["confidence"] == 100
    assert payload["radarGroups"]["nfl"][0]["scenarioShare"] == 100
    assert payload["radarGroups"]["nfl"][0]["winProbability"] is None
    assert payload["confidenceGroups"]["Favourite at 66%+"] == 2


def test_soccer_calendar_and_links_use_eastern_day(monkeypatch):
    card = future_card(game_date="2026-10-19T02:00:00Z", action="SKIP")
    monkeypatch.setattr(app_module.prediction_service, "get_fixture_cards", lambda _, **kw: ([card], [], None, "mock", ""))
    payload = app_module.app.test_client().get("/api/dashboard/soccer?date=2026-10-18").json
    assert payload["availableDates"] == ["2026-10-18"]
    assert payload["slate"][0]["slateDate"] == "2026-10-18"
    assert lifecycle.calendar_date("2026-11-02T04:30:00Z") == "2026-11-01"  # After DST ends.


def quote_game(now, **changes):
    return {"home_team": "Arsenal", "away_team": "Chelsea", "sport_key": "soccer_epl",
            "commence_time": (now + timedelta(hours=1)).isoformat(),
            "bookmakers": [{"title": "Current Book", "last_update": now.isoformat(), "markets": [
                {"key": "h2h", "outcomes": [{"name": "Arsenal", "price": 2.1}, {"name": "Chelsea", "price": 3.4}, {"name": "Draw", "price": 3.2}]}]}], **changes}


def test_odds_reject_old_future_and_wrong_fixture_quotes():
    now = datetime.now(UTC)
    game = quote_game(now)
    assert odds_fetcher._find_match([game], "Arsenal", "Chelsea")["available"]
    for age in (timedelta(minutes=16), timedelta(minutes=-2)):
        game["bookmakers"][0]["last_update"] = (now - age).isoformat()
        assert not odds_fetcher._find_match([game], "Arsenal", "Chelsea")["available"]
    game = quote_game(now)
    assert not odds_fetcher._find_match([game], "Arsenal", "Chelsea", kickoff=(now + timedelta(days=1)).isoformat())["available"]
    game["commence_time"] = (now - timedelta(seconds=1)).isoformat()
    assert not odds_fetcher._find_match([game], "Arsenal", "Chelsea")["available"]


def test_soccer_score_api_rejects_status_change_before_cached_forecast(monkeypatch):
    kickoff = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    app_module._FIXTURE_INDEX["88"] = {"fixture": {"id": 88, "date": kickoff, "status": {"short": "PST"}},
                                    "teams": {"home": {"id": 1}, "away": {"id": 2}}}
    monkeypatch.setattr(app_module.ac, "get_team_fixtures", lambda *_: pytest.fail("A postponed match must not fetch model inputs"))
    response = app_module.app.test_client().get("/api/scoreline/soccer", query_string={
        "team_a_id": 1, "team_b_id": 2, "event_id": 88, "before": kickoff})
    assert response.status_code == 422
    assert "status change" in response.json["message"]


@pytest.mark.parametrize("route", ["/prediction", "/matchup"])
def test_legacy_soccer_pages_do_not_generate_after_kickoff(monkeypatch, route):
    monkeypatch.setattr(app_module.sm, "predict_match", lambda *_: pytest.fail("No model evaluation after kickoff"))
    monkeypatch.setattr(app_module.ac, "get_h2h", lambda *_args, **_kwargs: pytest.fail("No input fetch after kickoff"))
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session.update(team_a_id=1, team_a_name="Arsenal", team_a_logo="", team_b_id=2, team_b_name="Chelsea", team_b_logo="",
                       selected_fixture={"fixture_id": 99, "date": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()})
    response = client.get(route)
    assert response.status_code == 200
    assert b"Unavailable" in response.data
    assert b"after kickoff" in response.data


def test_cold_soccer_refresh_has_a_bounded_wait_and_shares_pending_work(monkeypatch):
    pending = Future()
    calls = []

    class Pool:
        def submit(self, calculate):
            calls.append(calculate)
            return pending

    monkeypatch.setattr(app_module, "_SOCCER_CARD_POOL", Pool())
    monkeypatch.setattr(app_module, "_SOCCER_CARD_FLIGHTS", {})
    first = app_module._bounded_soccer_cards(39, timeout_seconds=0)
    second = app_module._bounded_soccer_cards(39, timeout_seconds=0)
    assert len(calls) == 1
    assert first == second
    assert first[0] == []
    assert "still refreshing" in first[2]
    assert first[4]["status"] == "degraded"
    completed = ([future_card()], [], None, "test", "")
    pending.set_result(completed)
    assert app_module._bounded_soccer_cards(39, timeout_seconds=0) == completed
    assert app_module._SOCCER_CARD_FLIGHTS == {}


@pytest.mark.parametrize("expired", [False, True])
def test_brain_refresh_does_not_renew_an_old_prediction_timestamp(monkeypatch, expired):
    now = datetime.now(UTC)
    old_source = metadata("original inputs", (now - timedelta(seconds=200 if expired else 90)).isoformat(), 120, 900)
    current_source = metadata("refreshed inputs", now.isoformat(), 120, 900)
    cached = future_card(data_freshness=old_source)
    fixture = {"fixture": {"id": "expiry-test", "date": cached["game_date"], "status": {"short": "NS"}},
               "data_freshness": current_source}
    brain = MatchBrain(load_fixtures=lambda _: ([], None, "test", ""), get_fixture_by_id=lambda _: fixture,
                       decision_engine=DecisionEngine())
    brain._analysis_cache["expiry-test"] = cached
    calls = []

    def regenerate(self, row):
        calls.append(row)
        return {**cached, "confidence": 77, "data_freshness": current_source}

    monkeypatch.setattr(MatchBrain, "canonical_from_fixture", regenerate)
    result = brain.get_match_analysis("expiry-test")
    assert len(calls) == int(expired)
    assert result["confidence"] == (77 if expired else 72)
    assert result["data_freshness"]["observed_at"] == (current_source if expired else old_source)["observed_at"]
    assert result["data_freshness"]["expires_at"] == (current_source if expired else old_source)["expires_at"]


def test_legacy_analysis_cache_rechecks_the_current_fixture_status(monkeypatch):
    cached = future_card()
    app_module._local_match_analysis_cache["expiry-test"] = cached
    app_module._FIXTURE_INDEX["expiry-test"] = {"fixture": {"id": "expiry-test", "date": cached["game_date"], "status": {"short": "PST"}}}
    monkeypatch.setattr(app_module, "analyze_match", lambda *_: pytest.fail("The cached result should only be withdrawn"))
    assert app_module.cached_analyze_match("expiry-test")["action"] == "SKIP"
