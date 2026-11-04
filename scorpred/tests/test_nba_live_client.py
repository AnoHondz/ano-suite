"""Season-consistency tests for NBA live client form helpers."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import nba_live_client as nc


def test_team_directory_uses_public_provider_ids(monkeypatch):
    payload = {"sports": [{"leagues": [{"teams": [
        {"team": {"id": "19", "displayName": "Orlando Magic", "shortDisplayName": "Magic", "logos": [{"href": "https://example.com/orl.png"}]}},
        {"team": {"id": "14", "displayName": "Miami Heat", "shortDisplayName": "Heat"}},
    ]}]}]}
    monkeypatch.setattr(nc, "_espn_get", lambda *args, **kwargs: payload)
    monkeypatch.setattr(nc.legacy, "get_teams", lambda: pytest.fail("Public team IDs must not depend on the legacy API"))
    teams = nc.get_teams()
    assert {team["id"] for team in teams} == {"19", "14"}
    assert next(team for team in teams if team["id"] == "19")["logo"] == "https://example.com/orl.png"


@pytest.mark.parametrize("score,expected", [({"value": 113}, 113), ({"displayValue": "101"}, 101), ("98", 98), ({}, None), ("Infinity", None)])
def test_score_shapes_normalize_without_summary_refetch(score, expected):
    assert nc._as_int(score) == expected


def test_total_schedule_outage_is_not_reported_as_empty_schedule(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("Provider down")
    monkeypatch.setattr(nc, "get_scoreboard_games", unavailable)
    with pytest.raises(RuntimeError, match="schedule is temporarily unavailable"):
        nc.get_upcoming_games(12, 2, "page")


def test_standings_are_optional_without_legacy_key(monkeypatch):
    monkeypatch.setattr(nc.legacy, "NBA_API_KEY", "")
    monkeypatch.setattr(nc.legacy, "get_standings", lambda *args: pytest.fail("No authenticated standings call without key"))
    assert nc.get_standings() == {}


def _game(game_id: str, date_start: str) -> dict:
    return {
        "id": game_id,
        "date": {"start": date_start},
        "status": {"state": "post"},
        "teams": {
            "home": {"id": "1", "name": "Team A", "nickname": "A", "logo": ""},
            "visitors": {"id": "2", "name": "Team B", "nickname": "B", "logo": ""},
        },
        "scores": {
            "home": {"points": 110, "linescore": [25, 28, 29, 28]},
            "visitors": {"points": 101, "linescore": [22, 24, 26, 29]},
        },
        "records": {"home": [], "visitors": []},
    }


def test_get_team_recent_form_prefers_current_season_only(monkeypatch):
    def fake_completed(team_id: str, season: int):
        if season == 2026:
            return [_game("cur-1", "2026-04-01T19:00:00Z")]
        return [_game("old-1", "2025-03-01T19:00:00Z")]

    monkeypatch.setattr(nc, "_completed_team_games_for_season", fake_completed)

    rows = nc.get_team_recent_form("1", season=2026, n=5)

    assert len(rows) == 1
    assert rows[0]["id"] == "cur-1"


def test_recent_form_context_exposes_historical_when_current_missing(monkeypatch):
    def fake_completed(team_id: str, season: int):
        if season == 2026:
            return []
        if season == 2025:
            return [_game("old-1", "2025-03-01T19:00:00Z")]
        return []

    monkeypatch.setattr(nc, "_completed_team_games_for_season", fake_completed)

    context = nc.get_team_recent_form_context("1", season=2026, n=5, historical_lookback=2)

    assert context["current_games"] == []
    assert len(context["historical_games"]) == 1
    assert context["historical_season"] == 2025
    assert context["using_historical_context"] is True


def test_current_season_uses_espn_ending_year_label():
    from datetime import date
    assert nc.legacy.current_season(date(2026, 10, 22)) == 2027
    assert nc.legacy.current_season(date(2027, 4, 1)) == 2027
    assert nc.legacy.current_season(date(2027, 7, 31)) == 2027
    assert nc.legacy.current_season(date(2027, 8, 1)) == 2028


def test_cup_championship_is_not_a_standings_game():
    def raw(type_info):
        return {"id": "1", "date": "2025-12-17T01:30Z", "season": {"year": 2026, "type": 2},
                "competitions": [{"type": type_info, "competitors": [
                    {"homeAway": "home", "team": {"id": "18"}, "score": "124"},
                    {"homeAway": "away", "team": {"id": "24"}, "score": "113"}]}]}
    assert nc._normalize_event(raw({"id": "39", "abbreviation": "CC"}))["standings_game"] is False
    assert nc._normalize_event(raw({"id": "1", "abbreviation": "STD"}))["standings_game"] is True
    assert nc._normalize_event(raw({}))["standings_game"] is True
