"""Soccer refresh recovery using offline source observations, never production data."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import app as application
from services import cache_service, decision_store, evidence, prediction_service as ps, soccer_model
from services.data_freshness import metadata
from services.prediction_lifecycle import calendar_date, freshness_state


def source(age=0):
    return metadata("offline test provider", datetime.now(UTC) - timedelta(seconds=age), 120, 900)


def fixture(observation):
    return {"fixture": {"id": 987, "date": (datetime.now(UTC) + timedelta(hours=5)).isoformat(),
                        "status": {"short": "NS"}},
            "teams": {"home": {"id": 1, "name": "Home"}, "away": {"id": 2, "name": "Away"}},
            "league": {"id": 39}, "data_freshness": observation}


def analysis(observation, confidence=70):
    return {"match_id": "987", "matchup": "Home vs Away", "confidence": confidence, "action": "CONSIDER",
            "recommended_side": "Home", "probabilities": {"a": 70, "draw": 20, "b": 10},
            "reason": "Observed test inputs", "data_quality": "Partial Data", "metric_breakdown": None,
            "data_freshness": observation}


@pytest.mark.parametrize("reader", ["service", "legacy"])
def test_expired_analysis_regenerates_from_actual_refreshed_fixture(monkeypatch, reader):
    old, current = source(180), source()
    row = fixture(current)
    stale = analysis(old)
    cached_before = deepcopy(stale)
    cache_service.set_json(cache_service.make_key("match-analysis", "987"), stale, ttl=300)
    application._local_match_analysis_cache["987"] = stale
    application._FIXTURE_INDEX["987"] = row
    generate = Mock(return_value=analysis(current, confidence=74))
    monkeypatch.setitem(ps._deps, "analyze_match", generate)
    monkeypatch.setattr(application, "analyze_match", generate)
    actual = ps.get_match_analysis("987") if reader == "service" else application.cached_analyze_match("987")
    generate.assert_called_once_with("987")
    assert actual["confidence"] == 74 and actual["data_freshness"] == current
    assert freshness_state(actual["data_freshness"]) == "fresh"
    assert stale == cached_before  # No observation timestamp is rewritten.


@pytest.mark.parametrize("status", ["NS", "PST", "FT"])
def test_no_fresh_source_means_no_recovered_prediction(monkeypatch, status):
    old = source(180)
    row = fixture(old)
    row["fixture"]["status"]["short"] = status
    application._FIXTURE_INDEX["987"] = row
    cache_service.set_json(cache_service.make_key("match-analysis", "987"), analysis(old), ttl=300)
    generate = Mock(side_effect=AssertionError("Cannot regenerate from stale input"))
    monkeypatch.setitem(ps._deps, "analyze_match", generate)
    actual = ps.get_match_analysis("987")
    assert actual["action"] == "SKIP" and actual["recommended_side"] == "Unavailable"
    assert actual["data_freshness"]["observed_at"] == old["observed_at"]
    generate.assert_not_called()


def test_still_fresh_analysis_keeps_its_original_source_clock(monkeypatch):
    old, current = source(30), source()
    application._FIXTURE_INDEX["987"] = fixture(current)
    cache_service.set_json(cache_service.make_key("match-analysis", "987"), analysis(old), ttl=300)
    monkeypatch.setitem(ps._deps, "analyze_match", Mock(side_effect=AssertionError("Already fresh")))
    assert ps.get_match_analysis("987")["data_freshness"]["expires_at"] == old["expires_at"]


def test_missing_prediction_cache_recovers_when_real_fixture_prediction_arrives(monkeypatch):
    current = source()
    row = fixture(current)
    row["prediction"] = {"win_probabilities": {"a": 70, "draw": 20, "b": 10}}
    application._FIXTURE_INDEX["987"] = row
    unavailable = {**analysis(current), "probabilities": {}, "recommended_side": "Unavailable", "action": "SKIP"}
    cache_service.set_json(cache_service.make_key("match-analysis", "987"), unavailable, ttl=300)
    generate = Mock(return_value=analysis(current))
    monkeypatch.setitem(ps._deps, "analyze_match", generate)
    assert ps.get_match_analysis("987")["probabilities"] == row["prediction"]["win_probabilities"]
    generate.assert_called_once()


def test_soccer_decision_expiry_triggers_source_refresh_without_losing_descriptive_fallback():
    cache_service.set_json(cache_service.make_key("decision", "soccer", "987"),
        {"matchId": "987", "teamA": "Home", "teamB": "Away", "side": "Home", "action": "CONSIDER",
         "matchDate": fixture(source())["fixture"]["date"], "dataFreshness": source(180)}, ttl=300)
    assert decision_store.get("soccer", "987") is None
    withdrawn = decision_store.get("soccer", "987", allow_stale=True)
    assert withdrawn["teamA"] == "Home" and withdrawn["side"] == "Unavailable"
    assert withdrawn["winProbability"] is None


@pytest.mark.parametrize("selected", ["", "explicit"])
def test_single_day_dashboard_does_not_wait_for_multiweek_loader(monkeypatch, selected):
    row = fixture(source())
    day = calendar_date(row["fixture"]["date"])
    if not selected:
        row["fixture"]["date"] = datetime.now(UTC).isoformat()
        day = calendar_date(row["fixture"]["date"])
    daily = Mock(return_value=([row], None, "offline", ""))
    broad = Mock(side_effect=AssertionError("No multi-week scan for a dated dashboard"))
    monkeypatch.setitem(ps._deps, "load_fixtures_for_date", daily)
    monkeypatch.setitem(ps._deps, "load_fixtures", broad)
    monkeypatch.setitem(ps._deps, "card_from_fixture", lambda *args: {})
    monkeypatch.setitem(ps._deps, "analyze_match", lambda *args: None)
    ps.get_fixture_cards(39, selected_date=day if selected else "")
    daily.assert_called_once_with(39, day)
    broad.assert_not_called()


def test_dated_evidence_never_fetches_or_scores_other_days(monkeypatch):
    row = fixture(source())
    day = calendar_date(row["fixture"]["date"])
    other = deepcopy(row)
    other["fixture"].update(id=988, date=(datetime.now(UTC) + timedelta(days=2)).isoformat())
    api = SimpleNamespace(FORCE_REFRESH=True, get_fixtures_for_date=Mock(return_value=[row, other]),
                          get_upcoming_fixtures=Mock(side_effect=AssertionError("No broad scan")))
    engine = SimpleNamespace(scorpred_predict=Mock(return_value={"win_probabilities": {"a": 70, "draw": 20, "b": 10}}))
    monkeypatch.setattr(evidence, "_with_ratings_model", lambda api, league, prediction, *args: prediction)
    rows, _, _, _ = evidence.load_upcoming_fixtures(api, None, engine, league=39, season=2026,
        selected_date=day, include_injuries=False, include_standings=False, max_deep_predictions=0)
    assert [item["fixture"]["id"] for item in rows] == [987]
    api.get_fixtures_for_date.assert_called_once_with(39, 2026, day)
    api.get_upcoming_fixtures.assert_not_called()
    assert engine.scorpred_predict.call_count == 1


def test_concurrent_ratings_refresh_uses_one_existing_calculation(monkeypatch):
    monkeypatch.setattr(soccer_model, "_ratings", {})
    monkeypatch.setattr(soccer_model, "_build_locks", {})
    monkeypatch.setattr(soccer_model, "_artifact", {"ratings": {}})
    entered, release = threading.Event(), threading.Event()
    fetch = Mock(return_value=([], {}))
    built = {"built_at": time.time()}

    def calculate(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return built

    monkeypatch.setattr(soccer_model, "_fetch_results", fetch)
    monkeypatch.setattr(soccer_model, "build_ratings", calculate)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(soccer_model._league_ratings, None, 39) for _ in range(4)]
        assert entered.wait(3)
        release.set()
        assert all(job.result(timeout=3) is built for job in jobs)
    fetch.assert_called_once()


def test_warming_analysis_keeps_verified_schedule_and_explicit_retry_state(monkeypatch):
    row = fixture(source())
    row["teams"]["home"]["name"] = "Observed home club"
    row["teams"]["away"]["name"] = "Observed away club"
    day = calendar_date(row["fixture"]["date"])
    row['league'] = {'id':39}
    application._FIXTURE_INDEX['987'] = row
    monkeypatch.setattr(application, "_bounded_soccer_cards", lambda *args, **kw:
        ([], [], "Soccer analysis is still refreshing. Please try again shortly.", "degraded", {}))
    monkeypatch.setattr(application.ac, "get_fixtures_for_date", Mock(side_effect=AssertionError('No second provider pass')))
    card = {**analysis(row["data_freshness"]), "action": "SKIP", "recommended_side": "Unavailable",
            "confidence": 0, "probabilities": {}, "team_a": "Observed home club", "team_b": "Observed away club",
            "game_date": row["fixture"]["date"], "event_status": "NS", "league_id": 39}
    monkeypatch.setattr(application, "_soccer_decision_card_from_fixture", lambda *args: card)
    response = application.app.test_client().get(f"/api/dashboard/soccer?league_id=39&date={day}")
    assert response.status_code == 200 and response.json["error"] is None
    assert response.json["refresh_pending"] is True
    result = response.json["slate"][0]
    assert result["teamA"] == "Observed home club" and result["winProbability"] is None
    assert result["side"] == "Unavailable" and result["action"] == "SKIP"
