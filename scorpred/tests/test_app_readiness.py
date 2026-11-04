"""Presentation caches must never extend pregame eligibility or source freshness."""
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from services import cache_service, prediction_service
from services import decision_store
from services import prediction_lifecycle as lifecycle
from services.data_freshness import metadata


def configure_cards(monkeypatch):
    now = datetime.now(UTC)
    fixture = {"fixture": {"id": 901, "date": (now + timedelta(hours=2)).isoformat(), "status": "NS"},
               "teams": {"home": {"id": 1, "name": "Home"}, "away": {"id": 2, "name": "Away"}},
               "data_freshness": metadata("test", now.isoformat(), 30, 120)}
    analysis = {"data_freshness": fixture["data_freshness"]}
    build = Mock(return_value={"match_id": "901", "team_a": "Home", "team_b": "Away", "action": "CONSIDER",
                               "recommended_side": "Home", "confidence": 67, "probabilities": {"a": 60, "b": 40}})
    monkeypatch.setattr(prediction_service, "_deps", {
        "load_fixtures": lambda _: ([fixture], None, "test", ""),
        "analyze_match": lambda _: analysis, "card_from_fixture": build})
    return now, fixture, build


def test_compiled_cards_reuse_analysis_and_return_independent_copies(monkeypatch):
    _, _, build = configure_cards(monkeypatch)
    first = prediction_service.get_fixture_cards(39)[0]
    expected = first[0]["probabilities"].copy()
    first[0]["probabilities"]["a"] = 1
    second = prediction_service.get_fixture_cards(39)[0]
    assert second[0]["probabilities"] == expected
    assert build.call_count == 1


def test_compiled_cache_withdraws_prediction_at_source_expiry(monkeypatch):
    now, _, build = configure_cards(monkeypatch)
    assert prediction_service.get_fixture_cards(39)[0][0]["action"] == "CONSIDER"

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now + timedelta(seconds=30)

    monkeypatch.setattr(lifecycle, "datetime", Clock)
    expired = prediction_service.get_fixture_cards(39)[0][0]
    assert expired["action"] == "SKIP"
    assert expired["probabilities"] == {}
    assert build.call_count == 1


def test_new_schedule_status_invalidates_compiled_cache(monkeypatch):
    _, fixture, build = configure_cards(monkeypatch)
    prediction_service.get_fixture_cards(39)
    fixture["fixture"]["status"] = "PST"
    cache_service.delete(cache_service.make_key("fixtures_raw", 39))
    result = prediction_service.get_fixture_cards(39)[0][0]
    assert result["action"] == "SKIP"
    assert result["probabilities"] == {}
    assert build.call_count == 1


def test_expired_compiled_cache_rebuilds_without_retraining(monkeypatch):
    _, _, build = configure_cards(monkeypatch)
    prediction_service.get_fixture_cards(39)
    original_time = cache_service.time.time
    monkeypatch.setattr(cache_service.time, "time", lambda: original_time() + 61)
    prediction_service.get_fixture_cards(39)
    assert build.call_count == 2


def test_slate_calculates_only_selected_day_and_preserves_complete_schedule(monkeypatch):
    _, fixture, build = configure_cards(monkeypatch)
    import copy
    later = copy.deepcopy(fixture)
    later["fixture"]["id"] = 902
    later["fixture"]["date"] = (datetime.fromisoformat(fixture["fixture"]["date"]) + timedelta(days=4)).isoformat()
    prediction_service._deps["load_fixtures"] = lambda _: ([fixture, later], None, "test", "")
    cards, fixtures, *_ = prediction_service.get_fixture_cards(39, selected_date="")
    assert len(cards) == 1
    assert len(fixtures) == 2
    assert build.call_args.args[0]["fixture"]["id"] == 901
    build.reset_mock()
    prediction_service.get_fixture_cards(39, selected_date=lifecycle.calendar_date(later["fixture"]["date"]))
    assert build.call_count == 1
    assert build.call_args.args[0]["fixture"]["id"] == 902


def test_dashboard_uses_all_schedule_dates_without_calculating_all_games(monkeypatch):
    import app
    _, fixture, _ = configure_cards(monkeypatch)
    later = {"fixture": {"id": 902, "date": (datetime.now(UTC) + timedelta(days=4)).isoformat()}}
    day = lifecycle.calendar_date(fixture["fixture"]["date"])
    result = app.app.test_client().get('/api/dashboard/soccer')
    assert result.status_code == 200
    # Reuse raw schedule contract to add a tab with no model calculation.
    cache_service.delete(cache_service.make_key("fixtures_raw", 39))
    prediction_service._deps["load_fixtures"] = lambda _: ([fixture, later], None, "test", "")
    payload = app.app.test_client().get('/api/dashboard/soccer').json
    assert payload["selectedDate"] == day
    assert payload["availableDates"] == sorted([day, lifecycle.calendar_date(later["fixture"]["date"])])
    assert len(payload["slate"]) == 1


def test_publishing_unchanged_decision_does_not_rewrite_disk_or_extend_ttl(monkeypatch):
    now, fixture, _ = configure_cards(monkeypatch)
    card = {"match_id": "901", "team_a": "Home", "team_b": "Away", "game_date": fixture["fixture"]["date"],
            "event_status": "NS", "data_freshness": metadata("test", now.isoformat(), 300),
            "action": "CONSIDER", "recommended_side": "Home", "probabilities": {"a": 60, "b": 40}, "confidence": 67}
    write = Mock(wraps=cache_service.set_json)
    monkeypatch.setattr(cache_service, "set_json", write)
    first = decision_store.publish([card], "soccer")
    assert decision_store.publish([card], "soccer") == first
    assert write.call_count == 1
    decision_store.publish([dict(card, confidence=68)], "soccer")
    assert write.call_count == 2


def test_insights_counts_one_public_request_and_keeps_its_original_limit(monkeypatch):
    import app
    configure_cards(monkeypatch)
    monkeypatch.setattr(app.limiter, "enabled", True)
    app.limiter.reset()
    try:
        client = app.app.test_client()
        for _ in range(30):
            assert client.get('/api/dashboard/insights?sport=soccer').status_code == 200
        assert client.get('/api/dashboard/insights?sport=soccer').status_code == 429
    finally:
        app.limiter.reset()
