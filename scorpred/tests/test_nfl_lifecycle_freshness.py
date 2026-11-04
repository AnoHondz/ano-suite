"""NFL predictions stop at kickoff and retain actual input observation dates."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
import requests

import nfl_client
import nfl_routes
from services import nfl_odds, nfl_service, nflverse_provider, nflverse_store
from services.data_freshness import SOURCE_KEY, metadata
from services.prediction_lifecycle import calendar_date, freshness_state

NOW = datetime.now(UTC)
SEASON = NOW.year - 1 if NOW.month <= 2 else NOW.year


@pytest.fixture(autouse=True)
def fresh_fixture_clock(monkeypatch):
    # Collection can precede execution by minutes in the full suite. Keep these
    # fresh-input fixtures fresh without changing any production age limits.
    current = datetime.now(UTC)
    monkeypatch.setitem(globals(), "NOW", current)
    monkeypatch.setitem(globals(), "SEASON", current.year - 1 if current.month <= 2 else current.year)


def game(identity="target", days=3, **changes):
    kickoff = NOW + timedelta(days=days)
    item = {"id": identity, "kickoff": kickoff.isoformat(), "date": calendar_date(kickoff),
            "season": SEASON, "season_type": 2, "week": 7, "standard": True, "state": "pre", "completed": False,
            "status": "Scheduled", "status_name": "STATUS_SCHEDULED", "neutral": False, "venue": "Test stadium",
            "home_id": "AA", "away_id": "BB", "home_name": "Home Team", "away_name": "Away Team",
            "home_logo": "", "away_logo": "", "home_score": None, "away_score": None,
            "schedule_freshness": metadata("Test schedule", NOW.isoformat(), 300, 86400, now=NOW)}
    item.update(changes)
    return item


def history():
    result = []
    for index in range(4):
        common = {"days": -7 * (index + 1), "state": "post", "completed": True, "status_name": "STATUS_FINAL",
                  "home_score": 28, "away_score": 14,
                  "form_freshness": metadata("Test completed scores", NOW.isoformat(), 172800, 172800, now=NOW)}
        result += [game(f"home-{index}", away_id="CC", **common), game(f"away-{index}", home_id="DD", **common)]
    return result


def test_live_slate_checks_receipts_after_download_but_keeps_explicit_as_of_clock(monkeypatch):
    records = history() + [game(days=1)]
    moments = iter([NOW - timedelta(seconds=10), NOW])
    class ReceiptClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(moments)
    monkeypatch.setenv("NFL_DATA_SOURCE", "nflverse")
    monkeypatch.setattr(nfl_service, "datetime", ReceiptClock)
    monkeypatch.setattr(nflverse_provider, "load_records", lambda *args: (
        records, {"source": "nflverse", "warning": None, "coverage": []}))
    monkeypatch.setattr(nfl_odds, "fetch_snapshot", lambda now: {"status": "unavailable"})
    monkeypatch.setattr(nfl_odds, "match_quote", lambda *args: None)
    payload, _ = nfl_service.load_slate(selected=(NOW + timedelta(days=1)).date())
    assert payload["dataFreshness"]["state"] == "fresh"
    payload, _ = nfl_service.load_slate(selected=(NOW + timedelta(days=1)).date(), now=NOW - timedelta(seconds=10))
    assert payload["dataFreshness"]["state"] == "unavailable"


@pytest.mark.parametrize("change", [{"kickoff": NOW.isoformat()}, {"kickoff": (NOW - timedelta(seconds=1)).isoformat()},
                                   {"state": "in"}, {"completed": True}, {"status_name": "STATUS_POSTPONED"}])
def test_kickoff_and_status_stop_nfl_predictions_before_engine_evaluation(monkeypatch, change):
    target = game(**change)
    engine = Mock(side_effect=AssertionError("No decision should be generated"))
    monkeypatch.setattr(nfl_service, "DecisionEngine", engine)
    assert nfl_service.analyze(target, history(), NOW) is None
    card = nfl_service.build_card(target, history(), NOW)
    assert card["action"] == "SKIP"
    assert card["probabilities"] == {}
    assert card["pregame_available"] is False
    assert card["event_status"]["name"] == target["status_name"]
    assert card["event_status"]["state"] == target["state"]
    engine.assert_not_called()


def test_delayed_schedule_is_visible_but_has_no_recommendation_or_market(monkeypatch):
    target = game(schedule_freshness=metadata("nflverse schedules", (NOW - timedelta(hours=2)).isoformat(), 300, 86400, now=NOW))
    records = history() + [target]
    monkeypatch.setenv("NFL_DATA_SOURCE", "nflverse")
    monkeypatch.setattr(nflverse_provider, "load_records", lambda *args: (records, {"source": "nflverse", "warning": "Refresh delayed.", "coverage": []}))
    quote = Mock(side_effect=AssertionError("No markets for unavailable predictions"))
    monkeypatch.setattr(nfl_odds, "match_quote", quote)
    payload, _ = nfl_service.load_slate(now=NOW)
    assert len(payload["slate"]) == 1
    assert payload["slate"][0]["action"] == "SKIP"
    assert payload["slate"][0]["winProbability"] is None
    assert payload["dataFreshness"]["state"] == "degraded"
    assert payload["last_updated"] == target["schedule_freshness"]["observed_at"]
    assert payload["dataSources"]["form"] == "needs_refresh"
    assert nfl_service.analyze(target, records, NOW) is None
    quote.assert_not_called()


def test_expired_historical_form_blocks_scores_instead_of_silently_dropping_inputs():
    target, records = game(), history()
    records[0]["form_freshness"] = metadata("Old completed scores", (NOW - timedelta(hours=49)).isoformat(), 172800, 172800, now=NOW)
    assert nfl_service.recent_form("AA", target, records, NOW) == []
    assert nfl_service.analyze(target, records, NOW) is None
    assert nfl_service.build_card(target, records, NOW)["action"] == "SKIP"


def test_recent_nfl_scores_obey_the_same_24_hour_availability_lag_as_research():
    target, records = game(), history()
    just_finished = game("recent", days=-.5, state="post", completed=True, status_name="STATUS_FINAL", home_score=90, away_score=0)
    assert nfl_service.recent_form("AA", target, records + [just_finished], NOW) == nfl_service.recent_form("AA", target, records, NOW)


@pytest.mark.parametrize("age,allowed", [(60, True), (86401, False), (-3600, False)])
def test_espn_fallback_preserves_source_time_and_has_a_bounded_stale_window(monkeypatch, age, allowed):
    observed = NOW - timedelta(seconds=age)
    cached = {"events": [game()], SOURCE_KEY: metadata("ESPN NFL schedules", observed.isoformat(), 10, 86400, now=NOW)}
    monkeypatch.setattr(nfl_client.cache_service, "get_json", lambda *args, **kwargs: deepcopy(cached))
    monkeypatch.setattr(nfl_client.requests, "get", Mock(side_effect=requests.Timeout()))
    if not allowed:
        with pytest.raises(nfl_client.NFLProviderError):
            nfl_client.calendar_events(SEASON)
    else:
        record = nfl_client.calendar_events(SEASON)[0]
        assert record["schedule_freshness"]["observed_at"] == observed.isoformat()
        assert record["schedule_freshness"]["state"] == "degraded"
        assert nfl_service.analyze(record, history(), NOW) is None


def seed_store(path, *, schedule_time=None, stats_time=None):
    schedule_time = NOW if schedule_time is None else schedule_time
    stats_time = NOW if stats_time is None else stats_time
    rows = []
    for item in history() + [game(identity="target")]:
        kickoff = datetime.fromisoformat(item["kickoff"]).astimezone(nfl_client.EASTERN)
        rows.append({"game_id": item["id"], "season": SEASON, "game_type": "REG", "week": 7,
                     "gameday": kickoff.date().isoformat(), "gametime": kickoff.strftime("%H:%M"),
                     "home_team": item["home_id"], "away_team": item["away_id"],
                     "home_score": item["home_score"], "away_score": item["away_score"],
                     "espn": "123" if item["id"] == "target" else None})
    teams = [{"team_abbr": code, "team_name": f"Team {code}"} for code in ("AA", "BB", "CC", "DD")]
    nflverse_store.replace_dataset("teams", 0, teams, path=path, synced_at=NOW)
    nflverse_store.replace_dataset("schedules", SEASON, rows, path=path, synced_at=schedule_time)
    nflverse_store.replace_dataset("team_stats", SEASON, [{"game_id": "home-0", "team": "AA", "season": SEASON}], path=path, synced_at=stats_time)
    return rows


def test_successful_nflverse_refresh_exposes_new_observation_not_old_metadata(tmp_path, monkeypatch):
    path = tmp_path / "nfl.sqlite3"
    rows = seed_store(path, schedule_time=NOW - timedelta(hours=2))
    monkeypatch.setattr(nflverse_store, "database_path", lambda: path)
    def refresh(seasons, *, now):
        nflverse_store.replace_dataset("schedules", SEASON, rows, path=path, synced_at=now)
    monkeypatch.setattr(nflverse_provider, "refresh_schedules", refresh)
    records, source = nflverse_provider.load_records(NOW.date(), (NOW + timedelta(days=14)).date(), NOW)
    assert source["data_freshness"]["observed_at"] == NOW.isoformat()
    assert source["data_freshness"]["state"] == "fresh"
    assert all(row["schedule_freshness"]["observed_at"] == NOW.isoformat() for row in records)


def test_outdated_team_stats_cannot_generate_research_scores(tmp_path, monkeypatch):
    from app import app
    from services import nfl_features

    path = tmp_path / "nfl.sqlite3"
    seed_store(path, stats_time=NOW - timedelta(hours=49))
    monkeypatch.setattr(nflverse_store, "database_path", lambda: path)
    monkeypatch.setenv("NFL_DATA_SOURCE", "nflverse")
    features = Mock(side_effect=AssertionError("Expired team stats cannot enter scoring inference"))
    monkeypatch.setattr(nfl_features, "build_features", features)
    response = app.test_client().get(f'/api/nfl/matches/123/analysis?date={game()["date"]}')
    assert response.status_code == 200
    assert response.json["result"]["research"]["available"] is False
    assert response.json["result"]["research"]["data_freshness"]["state"] == "unavailable"
    features.assert_not_called()


def test_nfl_analysis_rechecks_kickoff_after_slow_prediction(monkeypatch):
    from app import app

    target = game()
    monkeypatch.setattr(nfl_service, "load_slate", lambda selected: ({"slate": [{"matchId": "target"}]}, history() + [target]))
    monkeypatch.setattr(nfl_service, "analyze", lambda *args: {"projected_score": {"home": 20, "away": 17}})
    times = iter([NOW, datetime.fromisoformat(target["kickoff"])])
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(times)
    monkeypatch.setattr(nfl_routes, "datetime", Clock)
    response = app.test_client().get('/api/nfl/matches/target/analysis')
    assert response.status_code == 422
    assert "kickoff" in response.json["error"]


def odds_snapshot():
    target = game()
    return {"status": "available", "fetched_at": NOW.isoformat(), "events": [
        {"home_team": target["home_name"], "away_team": target["away_name"], "commence_time": target["kickoff"],
         "bookmakers": [{"title": "Test", "last_update": NOW.isoformat(),
                          "markets": [{"key": "h2h", "outcomes": [{"name": target["home_name"], "price": 1.9},
                                                                       {"name": target["away_name"], "price": 2.0}]}]}]}]}


@pytest.mark.parametrize("offset", [None, -301, 1])
def test_quote_requires_a_nonfuture_unexpired_snapshot(offset):
    snapshot = odds_snapshot()
    snapshot["fetched_at"] = (NOW + timedelta(seconds=offset)).isoformat() if offset is not None else None
    assert nfl_odds.match_quote(snapshot, game(), NOW) is None


def test_quote_rejects_future_bookmaker_updates_and_started_or_rescheduled_events():
    snapshot = odds_snapshot()
    snapshot["events"][0]["bookmakers"][0]["last_update"] = (NOW + timedelta(seconds=1)).isoformat()
    assert nfl_odds.match_quote(snapshot, game(), NOW) is None
    for target in (game(kickoff=NOW.isoformat()), game(state="in"), game(status_name="STATUS_POSTPONED")):
        assert nfl_odds.match_quote(odds_snapshot(), target, NOW) is None


def test_quote_expiry_keeps_original_snapshot_deadline():
    snapshot = odds_snapshot()
    snapshot["fetched_at"] = (NOW - timedelta(seconds=290)).isoformat()
    quote = nfl_odds.match_quote(snapshot, game(), NOW)
    assert quote is not None
    assert quote["data_freshness"]["expires_at"] == (NOW + timedelta(seconds=10)).isoformat()
    assert freshness_state(quote["data_freshness"], NOW + timedelta(seconds=10)) == "unavailable"


def test_calendar_groups_nfl_by_eastern_date_even_if_source_date_is_utc(monkeypatch):
    kickoff = (NOW + timedelta(days=3)).replace(hour=0, minute=15, second=0, microsecond=0)
    target = game(kickoff=kickoff.isoformat(), date=kickoff.date().isoformat())
    monkeypatch.setattr(nfl_client, "calendar_events", lambda year: [target])
    payload, _ = nfl_service.load_slate(now=NOW)
    assert payload["selectedDate"] == calendar_date(kickoff)
    assert payload["slate"][0]["slateDate"] == calendar_date(kickoff)
    assert payload["selectedDate"] != kickoff.date().isoformat()


def test_archived_stat_metadata_keeps_original_time_and_does_not_claim_current_freshness():
    observed = NOW - timedelta(days=500)
    result = nflverse_provider.dataset_freshness("team_stats", SEASON - 1, now=NOW,
                                               entries=[{"dataset": "team_stats", "season": SEASON - 1,
                                                         "row_count": 10, "synced_at": observed.isoformat()}])
    assert result["archived"] is True
    assert result["state"] == "unavailable"
    assert result["observed_at"] == observed.isoformat()
