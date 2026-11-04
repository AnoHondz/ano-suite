"""NBA source deadlines must trigger refreshes without reviving stale predictions."""
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
import os
import threading
from unittest.mock import Mock

import pytest
import requests

import app
import nba_live_client as live
import nba_routes
from services import decision_store, nba_matchup_context as context, prediction_lifecycle as lifecycle
from services.data_freshness import metadata


def card(now, source):
    return {"match_id": "refresh-game", "sport": "nba", "team_a": "Home", "team_b": "Away", "team_a_id": "20", "team_b_id": "5",
            "game_date": (now + timedelta(hours=4)).isoformat(), "event_status": "pre", "data_freshness": source,
            "action": "SKIP", "recommended_side": "Unavailable", "probabilities": {}, "reason": "Insufficient observations.", "confidence": 0}


def test_expired_nba_card_rebuilds_from_schedule_instead_of_reusing_five_minute_cache(monkeypatch):
    current = [datetime.now(UTC)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return current[0]
    monkeypatch.setattr(lifecycle, 'datetime', Clock)
    original = card(current[0], metadata('ESPN', current[0].isoformat(), 60, 300, now=current[0]))
    decision_store.publish([original], 'nba')
    assert decision_store.get('nba', 'refresh-game') is not None
    current[0] += timedelta(seconds=61)
    assert decision_store.get('nba', 'refresh-game') is None
    replacement = card(current[0], metadata('ESPN', current[0].isoformat(), 60, 300, now=current[0]))
    rebuild = Mock(side_effect=lambda: {'slate': decision_store.publish([replacement], 'nba')})
    monkeypatch.setattr(nba_routes, 'api_dashboard_nba', rebuild)
    response = app.app.test_client().get('/api/matches/refresh-game?sport=nba')
    assert response.status_code == 200
    assert response.json['decision']['dataFreshness']['observed_at'] == current[0].isoformat()
    assert response.json['decision']['researchForecast']['reason'] == 'Insufficient observations.'
    assert response.json['decision']['reason'].startswith('WAIT:')
    assert response.json['decision']['pregameAvailable'] is True
    assert response.json['decision']['recommendationEligible'] is False
    assert response.json['decision']['winProbability'] is None
    rebuild.assert_called_once()


def test_failed_refresh_keeps_known_matchup_but_never_reinstates_expired_probability(monkeypatch):
    now = datetime.now(UTC)
    original = card(now, metadata('ESPN', (now-timedelta(minutes=2)).isoformat(), 60, 300))
    original.update(action='CONSIDER', recommended_side='Home', probabilities={'a': 65, 'b': 35})
    decision_store.publish([original], 'nba')
    monkeypatch.setattr(nba_routes, 'api_dashboard_nba', lambda: ({'error': 'Provider unavailable'}, 503))
    response = app.app.test_client().get('/api/matches/refresh-game?sport=nba')
    assert response.status_code == 200
    assert response.json['decision']['action'] == 'SKIP'
    assert response.json['decision']['winProbability'] is None
    assert response.json['decision']['teamA'] == 'Home'
    assert response.json['decision']['dataFreshness']['observed_at'] == original['data_freshness']['observed_at']
    assert 'refresh failed' in response.json['warnings'][0]


def test_provider_outage_keeps_cached_identities_and_local_history_without_fabricating_scope(monkeypatch, tmp_path):
    now = datetime.now(UTC)
    original = card(now, metadata('ESPN', (now-timedelta(minutes=2)).isoformat(), 60, 300))
    original['event_status'] = {'state': 'pre', 'completed': False}
    decision_store.publish([original], 'nba')
    monkeypatch.setattr(context, 'ARCHIVE', tmp_path/'absent')
    monkeypatch.setattr(context.live, 'get_scoreboard_games', Mock(side_effect=RuntimeError('Provider down')))
    monkeypatch.setattr(context, '_current_snapshot', lambda *a: {'games': [], 'status': 'unavailable', 'source': {}})
    response = app.app.test_client().get('/api/nba/matches/refresh-game/context')
    # Public identifiers are numeric. Use the same saved record under a real-ID shape.
    assert response.status_code == 400
    original['match_id'] = '123'
    decision_store.publish([original], 'nba')
    response = app.app.test_client().get('/api/nba/matches/123/context')
    assert response.status_code == 200
    result = response.json['result']
    assert result['game']['schedule_source'] == 'cached_schedule'
    assert result['game']['season_type'] is None
    assert result['game']['neutral_site'] is None
    assert result['game']['result'] is None
    assert result['prediction_fallback'] is False
    assert result['teams']['home']['previous']['points_for'] is None


@pytest.fixture
def provider_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(live, 'NBA_CACHE_DIR', tmp_path)
    monkeypatch.setattr(live, 'PAGE_RENDER_RETRY_ATTEMPTS', 1)
    monkeypatch.setattr(live, 'EXTERNAL_API_RETRY_ATTEMPTS', 1)
    return tmp_path


def test_new_provider_receipt_survives_disk_cache_reads_without_using_file_mtime(provider_cache, monkeypatch):
    get = Mock(return_value=Mock(status_code=200, json=lambda: {'events': []}))
    monkeypatch.setattr(requests.Session, 'get', get)
    received = live._espn_get('scoreboard', ttl_seconds=60, request_profile='page')
    path = live._cache_path('espn:scoreboard', {})
    saved = json.loads(path.read_text())
    assert saved['_scorpred_source']['observed_at'] == received['_scorpred_source']['observed_at']
    assert saved['_scorpred_source']['receipt_basis'] == 'provider_response'
    os.utime(path, (0, 0))
    reused = live._espn_get('scoreboard', ttl_seconds=60, request_profile='page')
    assert reused['_scorpred_source'] == received['_scorpred_source']
    assert get.call_count == 1


def test_touching_an_expired_cache_cannot_make_it_fresh(provider_cache):
    observed = datetime.now(UTC)-timedelta(minutes=2)
    source = metadata('ESPN', observed.isoformat(), 60, 300)
    path = live._cache_path('espn:scoreboard', {})
    path.write_text(json.dumps({'events': [], '_scorpred_source': source}))
    os.utime(path, None)
    response = live._espn_get('scoreboard', ttl_seconds=60, request_profile='page')
    assert response['_scorpred_source']['state'] == 'degraded'
    assert response['_scorpred_source']['observed_at'] == observed.isoformat()


def test_explicit_degraded_receipt_cannot_become_fresh_without_provider_response(provider_cache):
    source = metadata('ESPN', datetime.now(UTC).isoformat(), 60, 300, degraded=True)
    path = live._cache_path('espn:scoreboard', {})
    path.write_text(json.dumps({'events': [], '_scorpred_source': source}))
    response = live._espn_get('scoreboard', ttl_seconds=60, request_profile='page')
    assert response['_scorpred_source']['state'] == 'degraded'


def test_concurrent_provider_requests_share_one_fetch_and_return_independent_data(provider_cache, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def fetch(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return Mock(status_code=200, json=lambda: {'events': [{'id': 'real-provider-event'}]})
    get = Mock(side_effect=fetch)
    monkeypatch.setattr(requests.Session, 'get', get)
    with ThreadPoolExecutor(max_workers=4) as pool:
        owner = pool.submit(live._espn_get, 'scoreboard', ttl_seconds=60, request_profile='page')
        assert entered.wait(5)
        others = [pool.submit(live._espn_get, 'scoreboard', ttl_seconds=60, request_profile='page') for _ in range(3)]
        release.set()
        rows = [future.result() for future in [owner, *others]]
    assert get.call_count == 1
    rows[0]['events'].clear()
    assert all(row['events'] for row in rows[1:])


def test_expired_team_snapshot_refreshes_before_its_outer_sixty_second_cache(monkeypatch):
    source = metadata('ESPN', (datetime.now(UTC)-timedelta(seconds=61)).isoformat(), 60, 300)
    old = Future(); old.set_result({'games': [], 'source': source, 'status': 'available'})
    monkeypatch.setattr(context, '_forms', {('20', 2027): (context.time.monotonic(), old)})
    new_source = metadata('ESPN', datetime.now(UTC).isoformat(), 3600, 21600)
    refresh = Mock(return_value={'games': [], 'source': new_source, 'status': 'available'})
    monkeypatch.setattr(context, '_current', refresh)
    result = context._current_snapshot('20', 2027)
    assert result['source']['observed_at'] == new_source['observed_at']
    assert result['status'] == 'available'
    refresh.assert_called_once()


def test_player_memory_cache_uses_provider_deadline_and_independent_copies(monkeypatch):
    clock = [datetime.now(UTC).timestamp()]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return datetime.fromtimestamp(clock[0], UTC)
    monkeypatch.setattr(lifecycle, 'datetime', Clock)
    monkeypatch.setattr(live.time, 'time', lambda: clock[0])
    monkeypatch.setattr(live, '_MEM_CACHE', {})
    def received(*args, **kwargs):
        return {'athletes': [{'athlete': {'id': 'player-1', 'displayName': 'Observed player'}}],
                '_scorpred_source': metadata('ESPN', datetime.fromtimestamp(clock[0], UTC).isoformat(), 2, 10, now=Clock.now())}
    fetch = Mock(side_effect=received)
    monkeypatch.setattr(live, '_espn_get', fetch)
    first = live.get_league_player_stats(2027)
    first.clear()
    assert live.get_league_player_stats(2027)[0]['id'] == 'player-1'
    assert fetch.call_count == 1
    clock[0] += 3
    live.get_league_player_stats(2027)
    assert fetch.call_count == 2


def test_degraded_player_averages_are_not_promoted_to_fresh_memory(monkeypatch):
    monkeypatch.setattr(live, '_MEM_CACHE', {})
    source = metadata('ESPN', datetime.now(UTC).isoformat(), 60, 300, degraded=True)
    monkeypatch.setattr(live, '_espn_get', lambda *a, **kw: {'athletes': [], '_scorpred_source': source})
    with pytest.raises(RuntimeError, match='fresh provider observation'):
        live.get_league_player_stats(2027)
    assert not live._MEM_CACHE
