"""Live NBA props must use current team membership, never historical appearances."""
from datetime import UTC, datetime, timedelta
from copy import deepcopy
import time
from unittest.mock import Mock

import pytest

from services import best_props, injury_reports, nba_props, prop_model
from services.data_freshness import metadata
from tests.test_box_props import game


def athlete(player_id, name="Current Player", **changes):
    return {"id": player_id, "displayName": name, "status": {"type": "active"}, **changes}


def feed(team, players, age=0, stale=False):
    return {"team": {"id": team}, "athletes": players}, time.time() - age, stale


@pytest.mark.parametrize("kind", ["stale", "expired", "wrong_team", "empty", "malformed_team"])
def test_unverifiable_rosters_cannot_authorize_player_props(monkeypatch, kind):
    payload, fetched, stale = feed("A", [athlete("a1")], age=700 if kind == "expired" else 0, stale=kind == "stale")
    if kind == "wrong_team":
        payload["team"]["id"] = "B"
    if kind == "empty":
        payload["athletes"] = []
    if kind == "malformed_team":
        payload["team"] = []
    monkeypatch.setattr(injury_reports, "_fetch", lambda *a, **kw: (payload, fetched, stale))
    with pytest.raises(nba_props.RosterUnavailable):
        nba_props.current_roster("A")


def test_inactive_injured_and_ambiguous_athlete_ids_are_excluded(monkeypatch):
    rows = [athlete("ok"), athlete("inactive", status={"type": "inactive"}), athlete("disabled", active=False),
            athlete("hurt", injuries=[{"status": "Out"}]), athlete("malformed", status="unknown"),
            athlete("duplicate"), athlete("duplicate")]
    monkeypatch.setattr(injury_reports, "_fetch", lambda *a, **kw: feed("A", rows))
    eligible, observation = nba_props.current_roster("A")
    assert set(eligible) == {"ok"} and observation["state"] == "fresh"


def prepare(monkeypatch, rosters):
    games = [game(i, 25 + i % 3, 18) for i in range(12)]
    monkeypatch.setattr(nba_props.box, "team_games", lambda *a, **kw: games)
    monkeypatch.setattr(injury_reports, "_fetch", lambda _key, url, **kw:
                        feed(url.split("/teams/")[1].split("/")[0], rosters[url.split("/teams/")[1].split("/")[0]]))
    return games


def test_departed_players_are_removed_before_ranking_and_ids_survive_response(monkeypatch):
    games = prepare(monkeypatch, {"A": [athlete("a1", "Ann Able")], "B": [athlete("b1", "Cal Cole")]})
    before = deepcopy(games)
    result = nba_props.project("A", "B", 2027, datetime.now(UTC) + timedelta(hours=3))
    assert {p["id"] for p in result["players"]} == {"a1", "b1"}
    assert all(p["name"] != "Bea Bench" for p in result["picks"])
    assert all(p["player_id"] in {"a1", "b1"} for p in result["picks"])
    assert result["roster_verification"]["status"] == "verified"
    assert games == before  # Historical records remain untouched.


def test_same_name_with_different_id_never_authorizes_a_departed_player(monkeypatch):
    prepare(monkeypatch, {"A": [athlete("different-id", "Ann Able")], "B": [athlete("b1", "Cal Cole")]})
    result = nba_props.project("A", "B", 2027, datetime.now(UTC) + timedelta(hours=3))
    assert all(p["name"] != "Ann Able" for p in result["players"] + result["picks"])


def test_transfer_does_not_silently_relabel_old_team_history(monkeypatch):
    prepare(monkeypatch, {"A": [athlete("a2", "Bea Bench")], "B": [athlete("a1", "Ann Able"), athlete("b1", "Cal Cole")]})
    result = nba_props.project("A", "B", 2027, datetime.now(UTC) + timedelta(hours=3))
    assert all(p["id"] != "a1" for p in result["players"])


def test_a_player_listed_on_both_matchup_rosters_is_not_guessed(monkeypatch):
    prepare(monkeypatch, {"A": [athlete("a1", "Ann Able")], "B": [athlete("a1", "Ann Able"), athlete("b1", "Cal Cole")]})
    result = nba_props.project("A", "B", 2027, datetime.now(UTC) + timedelta(hours=3))
    assert {p["id"] for p in result["players"]} == {"b1"}


def test_existing_probabilities_and_ranking_are_unchanged_for_verified_players(monkeypatch):
    games = prepare(monkeypatch, {"A": [athlete("a1", "Ann Able"), athlete("a2", "Bea Bench")], "B": [athlete("b1", "Cal Cole")]})
    original = prop_model.game_props(nba_props.CONFIG, "A", "B", games)
    result = nba_props.project("A", "B", 2027, datetime.now(UTC) + timedelta(hours=3))
    # Descriptive source annotations may be added; every model value and ranking
    # must still match the unchanged baseline for verified players.
    def model_values(payload):
        payload=deepcopy(payload)
        for player in payload['players']:
            for market in player['markets']:market.pop('opportunity_evidence',None)
        for pick in payload['picks']:pick.pop('opportunity_evidence',None)
        return payload['players'],payload['picks']
    assert model_values(result)==model_values(original)
    assert all('opportunity_evidence' in market for player in result['players'] for market in player['markets'])


@pytest.mark.parametrize("overall_only", [False, True])
def test_best_props_board_rebuilds_at_roster_expiry_and_withdraws_stale_rows(monkeypatch, overall_only):
    now = datetime.now(UTC)
    verification = metadata("Verified roster", now, 10)
    pick = {"sport": "nba", "kickoff": (now + timedelta(hours=1)).isoformat(), "roster_verification": verification}
    board = {"overall": [pick], "by_sport": {"nba": [] if overall_only else [pick]}, "slates": {"nba": {"with_props": 1}}}
    monkeypatch.setattr(best_props, "_cache", {})
    make = Mock(return_value=board)
    best_props.cached("roster-day", make, now=now.timestamp())
    best_props.cached("roster-day", make, now=(now + timedelta(seconds=11)).timestamp())
    assert make.call_count == 2
    withdrawn = best_props.without_started(board, now + timedelta(seconds=11))
    assert not withdrawn["overall"] and not withdrawn["by_sport"]["nba"]


def test_roster_provider_outage_is_explicitly_unavailable(monkeypatch):
    def failed(*args, **kwargs):
        raise injury_reports.InjuryFeedUnavailable("Unavailable")
    monkeypatch.setattr(injury_reports, "_fetch", failed)
    with pytest.raises(nba_props.RosterUnavailable, match="could not be verified"):
        nba_props.current_roster("A")


def test_roster_failure_route_returns_no_historical_player_fallback(monkeypatch):
    import nba_routes
    from app import app
    from services import nba_matchup_context as context

    tip = (datetime.now(UTC) + timedelta(hours=3)).isoformat()
    game_row = {"date": {"start": tip}, "teams": {
        "home": {"id": "A", "name": "Home"}, "visitors": {"id": "B", "name": "Away"}}}
    monkeypatch.setattr(context, "game_for_id", lambda *args: game_row)
    monkeypatch.setattr(nba_routes, "_nba_pregame_reason", lambda game: None)
    monkeypatch.setattr(injury_reports, "espn", lambda *args: ([], {}))

    def failed(*args, **kwargs):
        raise nba_props.RosterUnavailable("Current NBA rosters could not be verified.")
    monkeypatch.setattr(nba_props, "project", failed)
    response = app.test_client().get("/api/nba/matches/roster-failure/props")
    assert response.status_code == 503 and "could not be verified" in response.json["error"]
    assert "result" not in response.json
