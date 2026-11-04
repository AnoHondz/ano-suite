from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import pytest

import nfl_client
from services import cache_service, nfl_service
from tests.source_contracts import fresh_source

NOW = datetime.now(UTC)


def game(event_id="next", days=3, **changes):
    kickoff = NOW + timedelta(days=days)
    item = {"id": event_id, "kickoff": kickoff.isoformat(), "data_freshness": fresh_source(NOW),
            "date": kickoff.astimezone(nfl_client.EASTERN).date().isoformat(),
            "season": NOW.year, "season_type": 2, "week": 6, "standard": True,
            "state": "pre", "completed": False, "status": "Scheduled", "status_name": "STATUS_SCHEDULED",
            "neutral": False, "venue": "Test stadium", "home_id": "1", "away_id": "2",
            "home_name": "Home Team", "away_name": "Away Team", "home_logo": "", "away_logo": "",
            "home_score": None, "away_score": None}
    item.update(changes)
    return item


def history(count=5):
    records = []
    for i in range(count):
        common = {"days": -7 * (i + 1), "state": "post", "completed": True,
                  "status": "Final", "status_name": "STATUS_FINAL"}
        records.extend([game(f"home-{i}", **common, away_id="3", home_score=30, away_score=10),
                        game(f"away-{i}", **common, home_id="4", home_score=30, away_score=10)])
    return records


def raw_event(**changes):
    event = {"id": "123", "date": "2026-10-16T00:15Z", "season": {"year": 2026, "type": 2}, "week": {"number": 6},
             "competitions": [{"type": {"id": "1"}, "neutralSite": True,
                               "status": {"type": {"state": "post", "completed": True, "name": "STATUS_FINAL", "description": "Final"}},
                               "competitors": [
                                   {"id": "2", "homeAway": "away", "score": "17", "team": {"displayName": "Away Team"}},
                                   {"id": "1", "homeAway": "home", "score": {"value": 24}, "team": {"displayName": "Home Team"}}]}]}
    event.update(changes)
    return event


def test_normalization_handles_reversed_sides_score_shapes_and_eastern_date():
    result = nfl_client.normalize_event(raw_event())
    assert result["home_id"] == "1"
    assert result["home_score"] == 24
    assert result["away_score"] == 17
    assert result["date"] == "2026-10-15"
    assert result["neutral"] is True


def test_kickoff_timestamp_and_eastern_slate_date_are_both_preserved():
    from services import decision_store

    target = nfl_client.normalize_event(raw_event())
    card = nfl_service.build_card(target, [], NOW)
    decision = decision_store.publish([card], "nfl")[0]
    assert decision["matchDate"] == "2026-10-16T00:15:00+00:00"
    assert decision["slateDate"] == "2026-10-15"


@pytest.mark.parametrize("change", [{"date": "bad"}, {"date": "2026-10-16T00:15"}, {"competitions": []}, {"season": None}])
def test_malformed_events_are_rejected(change):
    assert nfl_client.normalize_event(raw_event(**change)) is None


def test_calendar_cache_survives_memory_clear_without_refetch(monkeypatch):
    response = Mock()
    response.json.return_value = {"events": [raw_event()]}
    fetch = Mock(return_value=response)
    monkeypatch.setattr(nfl_client.requests, "get", fetch)
    first = nfl_client.calendar_events(2026)
    cache_service._local_cache.clear()
    assert nfl_client.calendar_events(2026) == first
    assert fetch.call_count == 1


@pytest.mark.parametrize("payload", [{"message": "failed"}, {"events": [None]}, {"events": [{}]}, []])
def test_provider_errors_are_not_cached(monkeypatch, payload):
    response = Mock()
    response.json.return_value = payload
    fetch = Mock(return_value=response)
    monkeypatch.setattr(nfl_client.requests, "get", fetch)
    for _ in range(2):
        with pytest.raises(nfl_client.NFLProviderError):
            nfl_client.calendar_events(2026)
    assert fetch.call_count == 2


def test_scoring_scenarios_use_observed_records_and_never_produce_bet():
    target = game()
    records = history()
    result = nfl_service.analyze(target, records, NOW)
    assert result["projected_score"] == {"home": 30, "away": 10}
    assert result["probabilities"] == {"home": 100, "draw": 0, "away": 0}
    assert result["samples"] == {"home": 5, "away": 5}
    assert result["validated"] is False
    card = nfl_service.build_card(target, records, NOW)
    assert card["action"] == "SKIP"  # descriptive scenarios never become a CONSIDER or BET
    assert card["recommended_side"] == "Home Team"
    assert card["data_confidence"]["label"] == "Limited Data"
    assert nfl_service.analyze(dict(target, neutral=True), records, NOW) == result


def test_tied_scenarios_are_preserved():
    records = history()
    for record in records:
        record.update(home_score=21, away_score=21)
    result = nfl_service.analyze(game(), records, NOW)
    assert result["probabilities"] == {"home": 0, "draw": 100, "away": 0}
    card = nfl_service.build_card(game(), records, NOW)
    assert card["recommended_side"] == "Draw"
    assert card["action"] != "BET"


def test_form_excludes_future_previous_season_preseason_void_and_allstar():
    target = game()
    valid = history()
    invalid = [dict(valid[0], id="old", season=NOW.year - 1),
               dict(valid[0], id="pre", season_type=1),
               dict(valid[0], id="allstar", standard=False),
               dict(valid[0], id="void", status_name="STATUS_CANCELED"),
               game("future", days=1, state="post", completed=True, status_name="STATUS_FINAL", home_score=99, away_score=0)]
    assert nfl_service.analyze(target, valid + invalid, NOW) == nfl_service.analyze(target, valid, NOW)
    assert len(nfl_service.recent_form("1", target, history(12), NOW)) == 8


def test_insufficient_data_and_started_games_have_no_invented_estimates():
    for target, records in [(game(), history(2)), (game(days=-1), history()),
                            (game(state="in"), history()), (game(status_name="STATUS_POSTPONED"), history())]:
        assert nfl_service.analyze(target, records, NOW) is None
        card = nfl_service.build_card(target, records, NOW)
        assert card["action"] == "SKIP"
        assert card["probabilities"] == {}
        assert card["confidence_pct"] == 0


def test_default_slate_finds_next_date_and_excludes_exhibitions(monkeypatch):
    target = game()
    records = history() + [target, game("pre", days=1, season_type=1), game("allstar", days=1, standard=False)]
    monkeypatch.setattr(nfl_client, "calendar_events", lambda year: records)
    payload, _ = nfl_service.load_slate(now=NOW)
    assert payload["selectedDate"] == target["date"]
    assert payload["slate"][0]["matchId"] == "next"
    assert payload["slate"][0]["sport"] == "nfl"
    assert len(payload["slate"]) == 1
    assert payload["plan"]["bet"] == 0
    empty, _ = nfl_service.load_slate(date.fromisoformat(target["date"]) + timedelta(days=1), now=NOW)
    assert empty["slate"] == []
    assert empty["error"] is None


def test_january_fetches_previous_calendar_year_for_same_season_form(monkeypatch):
    fetch = Mock(return_value=[])
    monkeypatch.setattr(nfl_client, "calendar_events", fetch)
    nfl_service.load_slate(date(2027, 1, 10), now=NOW)
    assert {call.args[0] for call in fetch.call_args_list} == {2026, 2027}


def test_dashboard_match_reload_analysis_and_insights(monkeypatch):
    from app import app
    target = game()
    monkeypatch.setattr(nfl_client, "calendar_events", lambda year: history() + [target])
    client = app.test_client()
    url = f'?date={target["date"]}'
    dashboard = client.get('/api/dashboard/nfl' + url)
    assert dashboard.status_code == 200
    original = dashboard.json["slate"][0]
    cache_service._local_cache.clear()  # match reconstruction works without warm cards
    match = client.get('/api/matches/next' + url + '&sport=nfl')
    assert match.status_code == 200
    assert match.json["decision"] == original
    result = client.get('/api/nfl/matches/next/analysis' + url)
    assert result.status_code == 200
    assert result.json["result"]["projected_score"]["home"] == 30
    assert client.get('/api/nfl/matches/unknown/analysis' + url).status_code == 404
    insights = client.get('/api/dashboard/insights?sport=nfl&date=' + target["date"])
    assert insights.status_code == 200
    # NFL scenario shares are not comparable calibrated win probabilities.
    assert not any(card.get("sport") == "nfl" for card in insights.json["radarCards"])


def test_failures_empty_schedules_dates_and_insufficient_analysis(monkeypatch):
    from app import app
    client = app.test_client()
    monkeypatch.setattr(nfl_client, "calendar_events", Mock(side_effect=nfl_client.NFLProviderError()))
    assert client.get('/api/dashboard/nfl').status_code == 503
    assert client.get('/api/matches/x?sport=nfl').status_code == 503
    assert client.get('/api/nfl/matches/x/analysis').status_code == 503
    for value in ['bad', '2026-13-01', '1990-01-01', '20261016']:
        assert client.get('/api/dashboard/nfl?date=' + value).status_code == 400
    monkeypatch.setattr(nfl_client, "calendar_events", lambda year: [])
    empty = client.get('/api/dashboard/nfl')
    assert empty.status_code == 200 and empty.json["slate"] == []
    target = game()
    monkeypatch.setattr(nfl_client, "calendar_events", lambda year: [target])
    assert client.get(f'/api/nfl/matches/next/analysis?date={target["date"]}').status_code == 422


def test_nfl_rate_limit_is_attached_to_route(monkeypatch):
    import app as module
    monkeypatch.setattr(module.limiter, "enabled", True)
    module.limiter.reset()
    monkeypatch.setattr(nfl_client, "calendar_events", lambda year: [])
    client = module.app.test_client()
    responses = [client.get('/api/dashboard/nfl') for _ in range(31)]
    assert all(response.status_code == 200 for response in responses[:30])
    assert responses[-1].status_code == 429
    module.limiter.reset()


def test_cached_match_is_rechecked_when_game_starts(monkeypatch):
    from app import app
    from services import decision_store

    target = game()
    card = nfl_service.build_card(target, history(), NOW)
    decision_store.publish([card], "nfl")
    started = dict(target, state="in", status="In Progress", status_name="STATUS_IN_PROGRESS")
    monkeypatch.setattr(nfl_client, "calendar_events", lambda year: history() + [started])
    response = app.test_client().get(f'/api/matches/next?sport=nfl&date={target["date"]}')
    assert response.status_code == 200
    assert response.json["decision"]["action"] == "SKIP"
    assert response.json["decision"]["winProbability"] is None


def test_market_context_preserves_engine_decision(monkeypatch):
    from services import nfl_odds

    target = game()
    baseline = nfl_service.build_card(target, history(), NOW)
    monkeypatch.setattr(nfl_client, "calendar_events", lambda year: history() + [target])
    monkeypatch.setattr(nfl_odds, "fetch_snapshot", lambda now: {"status": "available", "events": []})
    quote = {"bookmaker": "Test book", "home_decimal": 1.8, "away_decimal": 2.1, "updated_at": NOW.isoformat()}
    monkeypatch.setattr(nfl_odds, "match_quote", lambda *args: quote)
    payload, _ = nfl_service.load_slate(now=NOW)
    decision = payload["slate"][0]
    assert decision["market"] == quote
    assert decision["researchForecast"]["action"] == baseline["action"] != "BET"
    assert decision["action"] == "SKIP"  # A quote does not verify injuries or lineups.
    assert decision["pregameAvailable"] is True
    assert payload["dataSources"]["odds"] == "available"
