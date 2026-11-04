"""Existing data becomes useful context without a new probability fallback."""
from datetime import UTC, datetime
import hashlib
import json

import pytest

import app
from services import nba_matchup_context as context

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def event(identifier, date="2026-01-01T00:00Z", season=2026, season_type=2, completed=True, scores=(110, 100)):
    return {"id": str(identifier), "date": date, "season": {"year": season, "type": season_type},
            "status": {"type": {"state": "post" if completed else "pre", "name": "STATUS_FINAL" if completed else "STATUS_SCHEDULED", "completed": completed}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "id": "20", "team": {"id": "20", "displayName": "Philadelphia 76ers"}, "score": scores[0]},
                {"homeAway": "away", "id": "5", "team": {"id": "5", "displayName": "Cleveland Cavaliers"}, "score": scores[1]},
            ]}]}


@pytest.fixture
def archive(tmp_path, monkeypatch):
    directory = tmp_path / 'raw'
    directory.mkdir()
    path = directory / '202601.json'
    path.write_text(json.dumps({"events": [event(101), event(102, "2026-02-01T00:00Z", scores=(95, 101)),
                                         event(103, season_type=1), event(104, completed=False), event(105, scores=(0, 0))]}))
    monkeypatch.setattr(context, 'ARCHIVE', directory)
    monkeypatch.setattr(context, 'INDEX', tmp_path / 'derived.sqlite3')
    monkeypatch.setattr(context, '_current_snapshot', lambda *args: {"games": [], "status": "unavailable", "source": {}})
    return path


def test_index_uses_only_eligible_existing_observations_and_preserves_originals(archive):
    before = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert context.build_index()['games'] == 2
    assert context.build_index()['games'] == 2
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == before
    assert context.archived_game('101')['context_source']['received_at'] is None


def test_conflicting_observations_fail_without_replacing_existing_index(archive):
    context.build_index()
    before = hashlib.sha256(context.INDEX.read_bytes()).hexdigest()
    (archive.parent / '202602.json').write_text(json.dumps({'events': [event(101, scores=(111, 100))]}))
    with pytest.raises(ValueError, match='Conflicting'):
        context.build_index()
    assert hashlib.sha256(context.INDEX.read_bytes()).hexdigest() == before


def test_early_season_has_real_previous_season_comparisons_and_no_probability(archive):
    target = context.live._normalize_event(event(999, '2026-10-23T23:00Z', season=2027, completed=False))
    result = context.context(target, now=NOW)
    home, away = result['teams']['home'], result['teams']['away']
    assert home['previous']['games'] == 2
    assert home['previous']['points_for'] == 102.5
    assert away['previous']['points_for'] == 100.5
    assert home['current']['games'] == 0
    assert home['current']['points_for'] is None
    assert result['role'] == 'descriptive_only'
    assert result['prediction_fallback'] is False
    assert result['prediction']['prior_season_probability_fallback'] == 'unsupported'
    assert home['historical_source']['historical_pregame_availability'] == 'unverified'


def test_target_result_and_future_records_cannot_enter_pregame_comparisons(archive):
    result = context.context(context.archived_game('102'), now=NOW)
    home = result['teams']['home']
    assert home['current']['games'] == 1
    assert home['current']['recent_results'][0]['game_id'] == '101'
    assert result['game']['result']['home']['points'] == 95


def test_live_context_excludes_preseason_future_target_and_unknown_scores(archive, monkeypatch):
    target = context.live._normalize_event(event(999, '2026-10-23T23:00Z', season=2027, completed=False))
    samples = [event(201, '2026-10-20T00:00Z', 2027), event(202, '2026-10-20T00:00Z', 2027, 1),
               event(203, '2026-10-23T00:00Z', 2027), event(204, '2026-10-21T23:00Z', 2027),
               event(205, '2026-10-20T00:00Z', 2027, scores=(None, None))]
    monkeypatch.setattr(context, '_current_snapshot', lambda *a: {'games': [context.live._normalize_event(e) for e in samples], 'status': 'available', 'source': {}})
    result = context.context(target, now=NOW)
    assert result['teams']['home']['current']['games'] == 1
    assert result['teams']['home']['current']['recent_results'][0]['game_id'] == '201'


def test_archived_game_link_works_without_current_predictions_or_network(archive, monkeypatch):
    monkeypatch.setattr(app, 'api_dashboard_nba', lambda: pytest.fail('No future schedule or predictions needed'), raising=False)
    response = app.app.test_client().get('/api/matches/101?sport=nba&date=2026-01-01')
    assert response.status_code == 200
    assert response.json['decision']['action'] == 'SKIP'
    assert response.json['decision']['winProbability'] is None


@pytest.mark.parametrize('path', ['/api/nba/games?view=unknown', '/api/nba/games?date=2026-13-01', '/api/nba/matches/bad/context'])
def test_invalid_requests_are_explicit(archive, path):
    assert app.app.test_client().get(path).status_code == 400


def test_missing_game_and_provider_failure_do_not_manufacture_stats(archive, monkeypatch):
    monkeypatch.setattr(context, 'game_for_id', lambda *args: None)
    assert app.app.test_client().get('/api/nba/matches/99/context').status_code == 404
    def unavailable(*args):
        raise RuntimeError('Provider down')
    monkeypatch.setattr(context, 'game_for_id', unavailable)
    response = app.app.test_client().get('/api/nba/matches/99/context')
    assert response.status_code == 503
    assert 'result' not in response.json


def test_archived_calendar_uses_eastern_day_and_no_reconstructed_forecast(archive):
    response = app.app.test_client().get('/api/nba/games?view=archive&date=2025-12-31')
    assert response.status_code == 200
    assert response.json['slate'][0]['matchId'] == '101'
    assert response.json['slate'][0]['winProbability'] is None


def test_absent_archive_is_an_explicit_empty_history(tmp_path, monkeypatch):
    monkeypatch.setattr(context, 'ARCHIVE', tmp_path)
    assert context.games_on_date() == ([], '')


def test_results_without_date_defaults_to_yesterday_not_latest_archive(archive, monkeypatch):
    from datetime import timedelta
    seen = []
    monkeypatch.setattr(context.live, 'get_scoreboard_games', lambda day, profile: seen.append(day) or [])
    response = app.app.test_client().get('/api/nba/games?view=results')
    yesterday = (datetime.now(context.CALENDAR_ZONE) - timedelta(days=1)).date().isoformat()
    assert response.status_code == 200
    assert response.json['selectedDate'] == yesterday
    assert seen[0].date().isoformat() == yesterday


def test_cup_championship_is_kept_in_archive_but_excluded_from_team_records(archive):
    cup_final = event(106, "2026-01-05T00:00Z", scores=(124, 113))
    cup_final["competitions"][0]["type"] = {"id": "39", "abbreviation": "CC"}
    archive.write_text(json.dumps({"events": [event(101), cup_final]}))
    assert context.build_index(force=True)["games"] == 2
    assert context.archived_game("106")["standings_game"] is False
    target = context.live._normalize_event(event(999, '2026-10-23T23:00Z', season=2027, completed=False))
    previous = context.context(target, now=NOW)["teams"]["home"]["previous"]
    assert previous["games"] == 1
    assert [row["game_id"] for row in previous["recent_results"]] == ["101"]
