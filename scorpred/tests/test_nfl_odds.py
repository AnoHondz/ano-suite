from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
import requests

from services import cache_service, nfl_odds

NOW = datetime.now(UTC)
GAME = {"home_name": "Kansas City Chiefs", "away_name": "Buffalo Bills", "kickoff": (NOW + timedelta(days=2)).isoformat()}


@pytest.fixture(autouse=True)
def fresh_fixture_clock(monkeypatch):
    current = datetime.now(UTC)
    monkeypatch.setitem(globals(), "NOW", current)
    monkeypatch.setitem(globals(), "GAME", {**GAME, "kickoff": (current + timedelta(days=2)).isoformat()})


def snapshot():
    return {"status": "available", "fetched_at": NOW.isoformat(), "events": [{"home_team": GAME["home_name"], "away_team": GAME["away_name"],
        "commence_time": GAME["kickoff"], "bookmakers": [{"title": "Test book", "last_update": NOW.isoformat(), "markets": [
            {"key": "h2h", "outcomes": [{"name": GAME["home_name"], "price": 1.8}, {"name": GAME["away_name"], "price": 2.1}]},
            {"key": "spreads", "outcomes": [{"name": GAME["home_name"], "point": -3.5}]},
            {"key": "totals", "outcomes": [{"name": "Over", "point": 44.5}, {"name": "Under", "point": 44.5}]}]}]}]}


def enabled(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "secret-test-key")
    monkeypatch.setenv("NFL_ODDS_ENABLED", "1")
    monkeypatch.setenv("NFL_ODDS_REGION", "us")


def test_opt_in_requires_key_and_does_not_make_requests(monkeypatch):
    monkeypatch.delenv("NFL_ODDS_ENABLED", raising=False)
    fetch = Mock()
    monkeypatch.setattr(nfl_odds.requests, "get", fetch)
    assert nfl_odds.fetch_snapshot()["status"] == "not_configured"
    fetch.assert_not_called()


def test_success_is_cached_across_cold_memory_and_key_is_not_in_payload(monkeypatch):
    enabled(monkeypatch)
    response = Mock(status_code=200)
    response.json.return_value = snapshot()["events"]
    fetch = Mock(return_value=response)
    monkeypatch.setattr(nfl_odds.requests, "get", fetch)
    result = nfl_odds.fetch_snapshot(NOW)
    cache_service._local_cache.clear()
    assert nfl_odds.fetch_snapshot(NOW) == result
    assert fetch.call_count == 1
    assert 'secret-test-key' not in str(result)
    assert fetch.call_args.kwargs['params']['markets'] == 'h2h,spreads,totals'


@pytest.mark.parametrize("status,expected", [(401, "authentication_failed"), (403, "authentication_failed"), (429, "rate_limited")])
def test_provider_errors_back_off_and_do_not_leak_keys(monkeypatch, caplog, status, expected):
    enabled(monkeypatch)
    fetch = Mock(return_value=Mock(status_code=status))
    monkeypatch.setattr(nfl_odds.requests, "get", fetch)
    assert nfl_odds.fetch_snapshot()["status"] == expected
    assert nfl_odds.fetch_snapshot()["status"] == expected
    assert fetch.call_count == 1
    assert "secret-test-key" not in caplog.text


def test_network_exceptions_never_log_urls_or_secrets(monkeypatch, caplog):
    enabled(monkeypatch)
    monkeypatch.setattr(nfl_odds.requests, "get", Mock(side_effect=requests.Timeout("https://api.test/?apiKey=secret-test-key")))
    assert nfl_odds.fetch_snapshot()["status"] == "unavailable"
    assert "secret-test-key" not in caplog.text


def test_quote_requires_matching_teams_kickoff_freshness_and_same_book():
    data = snapshot()
    quote = nfl_odds.match_quote(data, GAME, NOW)
    assert {key: value for key, value in quote.items() if key != "data_freshness"} == {
        "bookmaker": "Test book", "updated_at": NOW.isoformat(), "home_decimal": 1.8,
        "away_decimal": 2.1, "home_spread": -3.5, "total": 44.5}
    assert quote["data_freshness"]["observed_at"] == NOW.isoformat()
    for broken in [dict(GAME, kickoff=(NOW + timedelta(days=9)).isoformat()), dict(GAME, away_name="Other Team")]:
        assert nfl_odds.match_quote(data, broken, NOW) is None
    assert nfl_odds.match_quote(data, GAME, NOW + timedelta(minutes=16)) is None
    assert nfl_odds.match_quote(data, GAME, NOW - timedelta(minutes=5)) is None
    partial = deepcopy(data)
    book = partial["events"][0]["bookmakers"][0]
    other = deepcopy(book)
    book["markets"][0]["outcomes"].pop()
    other["markets"][0]["outcomes"].pop(0)
    partial["events"][0]["bookmakers"].append(other)
    assert nfl_odds.match_quote(partial, GAME, NOW) is None


@pytest.mark.parametrize("price", [0, 1, -2, "NaN", "Infinity", None])
def test_invalid_prices_are_not_published(price):
    data = snapshot()
    data["events"][0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = price
    assert nfl_odds.match_quote(data, GAME, NOW) is None


