from datetime import UTC, datetime, timedelta

import pytest

from services import espn_box_scores as box
from services import nba_props, prop_model, soccer_props


def soccer_summary():
    def athlete(aid, name, position, starter, subbed_in=False, shots=0, on_target=0):
        return {"athlete": {"id": aid, "displayName": name}, "position": {"abbreviation": position},
                "starter": starter, "subbedIn": subbed_in,
                "stats": [{"name": "totalShots", "value": shots}, {"name": "shotsOnTarget", "value": on_target}]}
    return {"header": {"competitions": [{"date": "2026-10-18T14:00Z", "status": {"type": {"completed": True}},
                                         "competitors": [{"team": {"id": "1"}, "homeAway": "home"},
                                                         {"team": {"id": "2"}, "homeAway": "away"}]}]},
            "keyEvents": [{"type": {"type": "substitution"}, "clock": {"value": 3600.0},
                           "participants": [{"athlete": {"id": "12"}}, {"athlete": {"id": "11"}}]}],
            "rosters": [{"team": {"id": "1"}, "roster": [athlete("11", "Starter Off", "F", True, shots=3, on_target=1),
                                                        athlete("12", "Sub On", "SUB", False, True, shots=1),
                                                        athlete("13", "Unused", "SUB", False)]},
                        {"team": {"id": "2"}, "roster": [athlete("21", "Full Match", "CM", True, shots=2)]}]}


def test_soccer_minutes_come_from_substitutions_and_unused_subs_are_dropped():
    teams = box._soccer_rows(soccer_summary())
    home = {row["name"]: row for row in teams["1"]["players"]}
    assert home["Starter Off"]["minutes"] == 60 and home["Sub On"]["minutes"] == 30
    assert "Unused" not in home
    assert home["Starter Off"]["shots"] == 3 and home["Starter Off"]["shots_on_target"] == 1
    assert teams["1"]["home"] and not teams["2"]["home"]


def test_nba_rows_skip_players_who_did_not_play():
    summary = {"header": {"competitions": [{"competitors": [{"team": {"id": "25"}, "homeAway": "home"}]}]},
               "boxscore": {"players": [{"team": {"id": "25"}, "statistics": [{
                   "keys": ["minutes", "points", "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "rebounds", "assists"],
                   "athletes": [{"athlete": {"id": "1", "displayName": "Played"}, "starter": True, "stats": ["34", "28", "4-9", "6", "7"]},
                                {"athlete": {"id": "2", "displayName": "Sat"}, "didNotPlay": True, "stats": []}]}]}]}}
    rows = box._nba_rows(summary)["25"]["players"]
    assert [(r["name"], r["minutes"], r["points"], r["threes"], r["assists"]) for r in rows] == [("Played", 34.0, 28.0, 4.0, 7.0)]
    assert rows[0]["blocks"] is None  # An omitted provider field is not a recorded zero.


def test_missing_soccer_statistics_cannot_become_high_confidence_unders():
    teams = box._soccer_rows(soccer_summary())
    assert teams["1"]["players"][0]["fouls"] is None
    rows = [{"minutes": 90, "shots": None} for _ in range(15)]
    assert prop_model.project_market(rows, ("shots",), .25, .02, 270, 1.0, 4) is None


NAMES = {"a1": "Ann Able", "a2": "Bea Bench", "b1": "Cal Cole"}


def game(index, home_points, away_points, minutes=34.0):
    date = (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index)).isoformat()
    def player(pid, team, points):
        return {"id": pid, "name": NAMES[pid], "position": "G", "starter": True, "minutes": minutes,
                "points": points, "rebounds": 5.0, "assists": 4.0, "threes": 2.0, "steals": 1.0, "blocks": 0.0, "turnovers": 2.0}
    return {"id": str(index), "date": date, "teams": {
        "A": {"home": True, "opponent": "B", "players": [player("a1", "A", home_points), player("a2", "A", 10.0)]},
        "B": {"home": False, "opponent": "A", "players": [player("b1", "B", away_points)]}}}


def test_game_props_project_regulars_skip_injured_players_and_pick_once_per_player():
    games = [game(i, 25.0 + (i % 3), 18.0) for i in range(12)]
    from services.injury_reports import normalize_name
    result = prop_model.game_props(nba_props.CONFIG, "A", "B", games, excluded=frozenset({normalize_name("Bea Bench")}))
    names = {p["name"] for p in result["players"]}
    assert names == {"Ann Able", "Cal Cole"}
    points = next(m for m in next(p for p in result["players"] if p["name"] == "Ann Able")["markets"] if m["market"] == "points")
    # 25-27 points (median 26); B allows more than the league here, so the projection sits above it.
    assert 26 < points["projection"] < 30 and points["line"] == 26.5
    assert points["ladder"]["start"] == 0 and points["ladder"]["over"] == sorted(points["ladder"]["over"], reverse=True)
    assert len({p["name"] for p in result["picks"]}) == len(result["picks"]) <= 5
    assert all(p["probability"] > max(p["record"], 0.5) for p in result["picks"])


def test_picks_respect_minimum_lines_and_allowed_sides():
    def market(key, side, line, probability, record):
        return {"market": key, "label": key, "side": side, "line": line, "probability": probability, "record": record}
    players = [{"name": "Scorer", "team": "A", "position": "F", "markets": [market("goals", "under", 0.5, 0.9, 0.6)]},
               {"name": "Shooter", "team": "A", "position": "F",
                "markets": [market("shots", "over", 1.5, 0.66, 0.4), market("fouls", "over", 0.5, 0.7, 0.6)]}]
    picks = prop_model.best_picks(players, soccer_props.CONFIG["pick_markets"], 1.0, sides=soccer_props.CONFIG["pick_sides"])
    # "No goal" is not a posted market; the shooter's shots edge (0.26) beats fouls (0.10).
    assert [(p["name"], p["market"]) for p in picks] == [("Shooter", "shots")]
    assert picks[0]["chance"] == 0.66


def test_soccer_positions_and_seasons():
    assert [soccer_props.position(p) for p in ("G", "CD-L", "LB", "DM", "AM", "CF", "LW")] == ["G", "D", "D", "M", "M", "F", "F"]
    assert soccer_props.season_of(datetime(2026, 10, 23, tzinfo=UTC)) == 2026
    assert soccer_props.season_of(datetime(2027, 3, 1, tzinfo=UTC)) == 2026
    with pytest.raises(ValueError):
        soccer_props.slug_for(999)


def test_nba_props_route_maps_team_names(monkeypatch):
    import nba_routes
    from app import app
    from services import injury_reports
    from services import nba_matchup_context as context
    tip = (datetime.now(UTC) + timedelta(hours=3)).isoformat()
    game_row = {"id": "g1", "date": {"start": tip}, "status": {"short": "NS"},
                "teams": {"home": {"id": "25", "name": "Oklahoma City Thunder", "abbrev": "OKC"},
                          "visitors": {"id": "7", "name": "Denver Nuggets", "abbrev": "DEN"}}}
    monkeypatch.setattr(context, "game_for_id", lambda game_id, date=None: game_row if game_id == "g1" else None)
    monkeypatch.setattr(nba_routes, "_nba_pregame_reason", lambda game: None)
    monkeypatch.setattr(injury_reports, "espn", lambda sport: ([{"name": "Hurt Guard", "team": "OKC"}, {"name": "Other", "team": "LAL"}], {}))
    seen = {}

    def project(home, away, season, tipoff, excluded, event_id=None):
        seen.update(home=home, away=away, excluded=excluded)
        return {"players": [], "picks": [{"name": "Star", "team": "25", "market": "points", "edge": .1}]}
    monkeypatch.setattr(nba_props, "project", project)
    client = app.test_client()
    response = client.get("/api/nba/matches/g1/props")
    assert response.status_code == 200
    assert response.json["result"]["picks"][0]["team"] == "Oklahoma City Thunder"
    assert seen["home"] == "25" and seen["excluded"] == frozenset({"hurt guard"})
    assert client.get("/api/nba/matches/missing/props").status_code == 404


def test_soccer_props_route_needs_an_espn_league(monkeypatch):
    from app import app
    from services import decision_store
    kickoff = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    decision = {"matchId": "m1", "sport": "soccer", "leagueId": 999, "teamA": "Home", "teamB": "Away", "homeId": 1, "awayId": 2,
                "side": "Home Win", "winProbability": 50.0, "drawProbability": 25.0, "matchDate": kickoff, "eventStatus": "NS",
                "dataFreshness": {"state": "fresh", "observed_at": datetime.now(UTC).isoformat(),
                                  "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}}
    monkeypatch.setattr(decision_store, "get", lambda sport, match_id, **_: decision)
    response = app.test_client().get("/api/soccer/matches/m1/props")
    assert response.status_code == 422 and "ESPN" in response.json["error"]
    decision["leagueId"] = 140
    monkeypatch.setattr(soccer_props, "resolve_team_ids", lambda *args: ("1", "2"))
    monkeypatch.setattr(soccer_props, "project", lambda *args, **kwargs: {"players": [], "picks": [
        {"name": "Striker", "team": "1", "market": "shots", "edge": .2}]})
    response = app.test_client().get("/api/soccer/matches/m1/props")
    assert response.status_code == 200 and response.json["result"]["picks"] == []
    assert "No verified sportsbook opportunity" in response.json["result"]["sportsbook"]["reason"]
    from services import injury_reports
    def unavailable(*args, **kwargs):
        raise injury_reports.InjuryFeedUnavailable("offline")
    monkeypatch.setattr(soccer_props, "project", unavailable)
    response = app.test_client().get("/api/soccer/matches/m1/props")
    assert response.status_code == 503 and "temporarily unavailable" in response.json["error"]


def test_soccer_props_resolves_provider_ids_by_exact_normalized_names(monkeypatch):
    import api_client
    monkeypatch.setattr(api_client, "_espn_league_teams", lambda league: [
        {"team": {"id": "364", "displayName": "Liverpool"}},
        {"team": {"id": "331", "displayName": "Brighton & Hove Albion"}},
    ])
    assert soccer_props.resolve_team_ids(39, "Liverpool FC", "Brighton & Hove Albion FC") == ("364", "331")
    with pytest.raises(ValueError, match="identity"):
        soccer_props.resolve_team_ids(39, "Liverpool FC", "Unknown")
