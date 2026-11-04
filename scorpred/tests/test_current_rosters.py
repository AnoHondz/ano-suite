from datetime import UTC, datetime, timedelta
import time
from copy import deepcopy
import pytest
from services import current_rosters as roster, injury_reports, soccer_props, nfl_props
from tests.test_box_props import game


def payload(team="A", ids=("a1",), sport="soccer"):
    now = datetime.now(UTC)
    season = now.year - (now.month < 7) if sport == "soccer" else now.year - (now.month <= 2)
    athletes = [{"id": pid, "displayName": pid, "status": {"type": "active"}} for pid in ids]
    return {"team": {"id": team}, "season": {"year": season},
            "athletes": [{"items": athletes}] if sport == "nfl" else athletes}


@pytest.mark.parametrize("failure", ["wrong_team", "old_season", "stale", "expired", "empty", "duplicate"])
def test_unverified_current_rosters_cannot_authorize_props(monkeypatch, failure):
    body = payload()
    if failure == "wrong_team": body["team"]["id"] = "B"
    if failure == "old_season": body["season"]["year"] -= 1
    if failure == "empty": body["athletes"] = []
    if failure == "duplicate": body["athletes"] *= 2
    monkeypatch.setattr(injury_reports, "_fetch", lambda *a, **kw:
        (body, time.time() - (700 if failure == "expired" else 0), failure == "stale"))
    if failure == "duplicate":
        assert roster.current("soccer", "A", "eng.1")[0] == {}
    else:
        with pytest.raises(roster.RosterUnavailable): roster.current("soccer", "A", "eng.1")


def test_departed_soccer_players_never_reappear_from_historical_box_scores(monkeypatch):
    games = [game(i, 25, 20, minutes=80) for i in range(12)]
    for row in games:
        for team in row["teams"].values():
            for player in team["players"]:
                player.update(position="M", shots=3, shots_on_target=1, goals=0, fouls=1)
    original = deepcopy(games)
    monkeypatch.setattr(soccer_props.box, "team_games", lambda *a, **kw: games)
    monkeypatch.setattr(injury_reports, "_fetch", lambda key, url, **kw:
        (payload("A", ("a2",)) if "/A/" in url else payload("B", ("b1",)), time.time(), False))
    result = soccer_props.project(39, "A", "B", datetime.now(UTC) + timedelta(days=1))
    assert {p["id"] for p in result["players"]} == {"a2", "b1"}
    assert all(p["player_id"] != "a1" for p in result["picks"])
    assert result["roster_verification"]["status"] == "verified"
    assert games == original


def test_nfl_current_ids_use_exact_gsis_crosswalk(monkeypatch):
    monkeypatch.setattr(roster, "nfl_team_id", lambda team: team)
    monkeypatch.setattr(roster, "current", lambda sport, team: (
        {team + "-espn": {"id": team + "-espn", "displayName": "Current"}},
        {"state": "fresh", "observed_at": datetime.now(UTC).isoformat(),
         "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat()}))
    monkeypatch.setattr(nfl_props.store, "records", lambda *args: [
        {"espn_id": "A-espn", "gsis_id": "a"}, {"espn_id": "B-espn", "gsis_id": "b"},
        {"espn_id": "old-espn", "gsis_id": "departed"}])
    eligible, source = nfl_props.current_eligibility("A", "B")
    assert set(eligible["A"]) == {"a"} and set(eligible["B"]) == {"b"}
    assert source["status"] == "verified"


def test_family_name_first_injury_feed_cannot_allow_an_unavailable_player():
    from services import nba_props, prop_model
    games = [game(i, 25, 20) for i in range(12)]
    for row in games:
        row["teams"]["A"]["players"][0]["name"] = "Kaoru Mitoma"
    result = prop_model.game_props(nba_props.CONFIG, "A", "B", games,
                                   excluded=frozenset({"Mitoma Kaoru"}))
    assert all(p["id"] != "a1" for p in result["players"])


def test_fpl_secondary_source_cannot_reintroduce_departed_soccer_player(monkeypatch):
    # Isolate roster joins from the independent complete-match coverage gate.
    monkeypatch.setattr('services.ano_data_quality.guard',lambda card,now=None:card)
    import app as application
    from services import fpl_props, prop_markets
    from services.data_freshness import metadata
    from services.injury_reports import normalize_name
    from utils.parsing import normalize_team_name
    now = datetime.now(UTC)
    source = metadata("Current roster", now, 600)
    decision = {"matchId": "current", "sport": "soccer", "leagueId": 39, "teamA": "Liverpool FC",
                "teamB": "Brighton & Hove Albion FC", "homeId": 64, "awayId": 397,
                "matchDate": (now + timedelta(days=1)).isoformat(), "eventStatus": "NS",
                "dataFreshness": source, "action": "SKIP"}
    monkeypatch.setattr(injury_reports, "fpl", lambda: ({"elements": []}, source))
    def pick(name, full_name, pid):
        return {"name": name, "full_name": full_name, "player_id": pid, "team": "Liverpool", "market": "tackles", "edge": .2}
    monkeypatch.setattr(fpl_props, "project", lambda *a: {"picks": [
        pick("Salah", "Mohamed Salah", "old-fpl-id"), pick("Current", "Current Player", "new-fpl-id")]})
    monkeypatch.setattr(soccer_props, "resolve_team_ids", lambda *a: ("364", "331"))
    monkeypatch.setattr(soccer_props, "project", lambda *a: {"picks": [], "roster_verification": source,
        "roster_players": {"364": [{"id": "current-espn-id", "name": "Current Player", "full_name": "Current Player"}]}})
    monkeypatch.setattr(prop_markets, "compare", lambda result, *a: result)
    body, status = application.soccer_props_payload(decision)
    assert status == 200
    assert [p["player_id"] for p in body["result"]["picks"]] == ["current-espn-id"]
    assert body["result"]["picks"][0]["fpl_player_id"] == "new-fpl-id"
