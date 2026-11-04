import json
from unittest.mock import Mock

import pytest
import requests

from services import injury_impact as impact
from services import injury_reports as reports
from services import soccer_player_value as soccer


def player(value, availability, played_share=1.0, name="P"):
    return {"name": name, "position": "QB", "status": "Out" if availability == 0 else "Questionable",
            "injury": "", "updated": None, "sources": ["ESPN"], "value": value,
            "availability": availability, "played_share": played_share}


def test_an_absent_regular_lowers_the_win_chance_unless_the_form_already_lacks_them():
    healthy = impact.margin_report(0.0, 13.5, {"home": [], "away": []}, priced=False)["teams"]["home"]["win_now"]
    missing = impact.margin_report(0.0, 13.5, {"home": [player(5.0, 0.0)], "away": []}, priced=False)
    home = missing["teams"]["home"]
    assert healthy == pytest.approx(0.5)
    assert home["win_now"] < 0.40 and home["win_if_fit"] == pytest.approx(0.5)
    assert home["players"][0]["impact"] == pytest.approx(home["win_if_fit"] - home["win_now"])
    # Out all along: the recent form already reflects it, so tonight changes nothing.
    priced_in = impact.margin_report(0.0, 13.5, {"home": [player(5.0, 0.0, played_share=0.0)], "away": []}, priced=False)
    assert priced_in["teams"]["home"]["win_now"] == pytest.approx(0.5)
    assert priced_in["teams"]["home"]["win_if_fit"] > 0.6


def test_model_priced_margins_are_not_adjusted_twice_and_values_floor_at_replacement():
    report = impact.margin_report(-2.0, 12.0, {"home": [player(-1.0, 0.0), player(3.0, 0.5, name="Q")], "away": []}, priced=True)
    home = report["teams"]["home"]
    assert report["margin"] == -2.0
    impacts = {p["name"]: p["impact"] for p in home["players"]}
    assert impacts["P"] == pytest.approx(0.0) and impacts["Q"] > 0
    assert report["teams"]["away"]["win_now"] == pytest.approx(1 - home["win_now"])


def test_unvalued_positions_are_listed_without_an_impact():
    report = impact.margin_report(0.0, 13.5, {"home": [{**player(None, 0.0), "position": "LB"}], "away": []}, priced=False)
    assert report["teams"]["home"]["players"][0]["impact"] is None


def test_implied_rates_reproduce_the_forecast_and_absent_attackers_cost_goals():
    home_rate, away_rate = impact.implied_rates(0.48, 0.29)
    win, draw, loss = impact.poisson_outcomes(home_rate, away_rate)
    assert win == pytest.approx(0.48, abs=1e-4) and loss == pytest.approx(0.29, abs=1e-4)
    striker = {"name": "S", "position": "FWD", "status": "Injured", "injury": "", "updated": None, "sources": [],
               "availability": 0.0, "played_share": 1.0, "attack": 0.3, "defence": 0.0}
    report = impact.goals_report(0.48, 0.23, 0.29, {"home": [striker], "away": []})
    home = report["teams"]["home"]
    assert home["win_now"] < 0.48 < home["win_if_fit"] + 1e-6
    assert report["teams"]["away"]["win_now"] > 0.29


def test_nfl_status_play_rates():
    assert impact.nfl_availability("Out") == 0
    assert impact.nfl_availability("Injured Reserve") == 0
    assert impact.nfl_availability("Questionable") == pytest.approx(0.67)
    assert impact.nfl_availability("") == 1


def test_fpl_team_names_match_soccer_cards():
    teams = [{"id": 1, "name": "Man City"}, {"id": 2, "name": "Nott'm Forest"}, {"id": 3, "name": "Brighton"},
             {"id": 4, "name": "Bournemouth"}, {"id": 5, "name": "Man Utd"}]
    assert impact.fpl_team(teams, "Manchester City")["id"] == 1
    assert impact.fpl_team(teams, "Nottingham Forest")["id"] == 2
    assert impact.fpl_team(teams, "Brighton & Hove Albion")["id"] == 3
    assert impact.fpl_team(teams, "AFC Bournemouth")["id"] == 4
    assert impact.fpl_team(teams, "Manchester United")["id"] == 5
    assert impact.fpl_team(teams, "Real Madrid") is None


def fpl_player(pid, team, kind, minutes, status="a", chance=None, xg=0.0, xa=0.0, xgc=0.0, gc=0, starts=None):
    return {"id": pid, "web_name": f"p{pid}", "team": team, "element_type": kind, "minutes": minutes,
            "status": status, "chance_of_playing_next_round": chance, "news": "Knee injury" if status != "a" else "",
            "news_added": "2026-10-20T10:00:00Z", "expected_goals": xg, "expected_assists": xa,
            "expected_goals_conceded": xgc, "goals_conceded": gc, "starts": starts if starts is not None else minutes // 90}


def test_soccer_players_value_listed_absences_from_fpl():
    players = [fpl_player(i, 1 + i % 2, soccer.FORWARD, 600, xg=1.0 + i / 10) for i in range(12)]
    players += [fpl_player(100, 1, soccer.FORWARD, 630, status="i", chance=0, xg=6.0, xa=1.0),
                fpl_player(101, 1, soccer.GOALKEEPER, 630, xgc=9.0, gc=9), fpl_player(102, 2, soccer.GOALKEEPER, 630, xgc=9.0, gc=9),
                fpl_player(103, 2, soccer.DEFENDER, 0, status="u")]
    payload = {"elements": players, "teams": [{"id": 1, "name": "Arsenal"}, {"id": 2, "name": "Chelsea"}],
               "events": [{"finished": True}] * 7}
    sides = impact.soccer_players(payload, "Arsenal", "Chelsea")
    assert [p["name"] for p in sides["home"]] == ["p100"]
    assert sides["away"] == []  # left without playing: nothing to take out of the form
    star = sides["home"][0]
    assert star["availability"] == 0 and star["attack"] > 0.2 and star["played_share"] == pytest.approx(1.0)


def test_espn_feed_is_parsed_cached_and_served_stale_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "cache_dir", lambda *_: tmp_path)
    body = {"injuries": [{"injuries": [{"status": "Out", "date": "2026-10-22T10:00Z", "shortComment": "Out Sunday.",
                                         "details": {"type": "Ankle", "returnDate": "2026-10-30"},
                                         "athlete": {"displayName": "A. Player", "position": {"abbreviation": "WR"},
                                                     "team": {"abbreviation": "WSH"},
                                                     "links": [{"href": "https://www.espn.com/nfl/player/_/id/123/a-player"}]}}]}]}
    response = Mock(json=Mock(return_value=body), raise_for_status=Mock())
    get = Mock(return_value=response)
    monkeypatch.setattr(reports.requests, "get", get)
    rows, freshness = reports.espn("nfl")
    assert rows == [{"espn_id": "123", "name": "A. Player", "team": "WSH", "position": "WR", "status": "Out",
                     "injury": "Ankle", "return_date": "2026-10-30", "updated": "2026-10-22T10:00Z",
                     "comment": "Out Sunday.", "source": "ESPN"}]
    assert freshness["state"] == "fresh"
    reports.espn("nfl")
    assert get.call_count == 1  # served from cache
    cached = json.loads((tmp_path / "nfl.json").read_text())
    cached["fetched"] -= reports.FRESH_SECONDS + 1
    (tmp_path / "nfl.json").write_text(json.dumps(cached))
    monkeypatch.setattr(reports.requests, "get", Mock(side_effect=requests.Timeout()))
    rows, freshness = reports.espn("nfl")
    assert rows and freshness["state"] == "degraded"
    cached["fetched"] -= reports.MAX_STALE_SECONDS
    (tmp_path / "nfl.json").write_text(json.dumps(cached))
    with pytest.raises(reports.InjuryFeedUnavailable):
        reports.espn("nfl")


def test_scheduled_refresh_renews_fresh_feeds_and_reports_each_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "cache_dir", lambda *_: tmp_path)
    fpl_body = {"elements": [{"id": 1}], "teams": [{"id": 1}]}

    def get(url, **_kwargs):
        if "nfl" in url:
            raise requests.Timeout()
        return Mock(json=Mock(return_value=fpl_body if "fantasy" in url else {"injuries": []}), raise_for_status=Mock())

    get = Mock(side_effect=get)
    monkeypatch.setattr(reports.requests, "get", get)
    reports.fpl()
    reports.fpl(refresh=True)
    assert get.call_count == 2  # refresh bypasses the fresh cache
    outcomes = reports.refresh_all()
    assert [row["status"] for row in outcomes] == ["success", "failed", "success"]
    assert all(row["observed_at"] for row in (outcomes[0], outcomes[2]))
    assert "nfl" in outcomes[1]["error"]


def test_names_join_across_providers():
    assert reports.normalize_name("Deebo Samuel Sr.") == reports.normalize_name("deebo samuel")
    assert reports.normalize_name("Luka Dončić") == "luka doncic"


def soccer_decision(**changes):
    from datetime import UTC, datetime, timedelta
    decision = {"matchId": "m1", "sport": "soccer", "leagueId": 39, "teamA": "Arsenal", "teamB": "Chelsea",
                "homeProbability": 50.0, "drawProbability": 25.0, "winProbability": 50.0, "side": "Arsenal Win",
                "matchDate": (datetime.now(UTC) + timedelta(days=1)).isoformat(), "eventStatus": "NS",
                "dataFreshness": {"state": "fresh", "observed_at": datetime.now(UTC).isoformat(),
                                  "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}}
    decision.update(changes)
    return decision


def test_soccer_route_covers_the_premier_league_only(monkeypatch):
    from app import app
    from services import decision_store
    monkeypatch.setattr(decision_store, "get", lambda sport, match_id, **_: soccer_decision(leagueId=140))
    response = app.test_client().get("/api/soccer/matches/m1/injuries")
    assert response.status_code == 422 and "Premier League" in response.json["error"]


def test_soccer_route_prices_fpl_absences(monkeypatch):
    from app import app
    from services import decision_store
    monkeypatch.setattr(decision_store, "get", lambda sport, match_id, **_: soccer_decision())
    players = [fpl_player(i, 1 + i % 2, soccer.FORWARD, 600, xg=1.0) for i in range(12)]
    players.append(fpl_player(100, 1, soccer.FORWARD, 630, status="i", chance=0, xg=6.0))
    payload = {"elements": players, "teams": [{"id": 1, "name": "Arsenal"}, {"id": 2, "name": "Chelsea"}],
               "events": [{"finished": True}] * 7}
    monkeypatch.setattr(reports, "fpl", lambda: (payload, {"state": "fresh"}))
    response = app.test_client().get("/api/soccer/matches/m1/injuries")
    assert response.status_code == 200
    result = response.json["result"]
    home = result["teams"][0]
    assert home["team"] == "Arsenal" and home["win_now"] < 50 < home["win_if_fit"] + 0.1
    assert home["players"][0]["impact"] > 0 and result["unit"] == "goals"


def test_nfl_players_merge_espn_with_the_official_report_and_value_by_epa(tmp_path, monkeypatch):
    from services import nfl_props, nfl_service
    from services import nflverse_store as store
    season = 2026
    monkeypatch.setattr(store, "database_path", lambda: tmp_path / "nfl.db")
    rows = []
    for week in range(1, 5):
        game_id = f"{season}_{week:02d}_BB_AA"
        for team, qb_epa in (("AA", 12.0), ("BB", -4.0)):
            rows.append({"season": season, "season_type": "REG", "week": week, "game_id": game_id, "team": team,
                         "opponent_team": "BB" if team == "AA" else "AA", "player_id": f"{team}-qb",
                         "player_display_name": f"{team} Passer", "position": "QB", "attempts": 34, "sacks_suffered": 2,
                         "carries": 3, "passing_epa": qb_epa, "rushing_epa": 0.5})
            rows.append({**rows[-1], "player_id": f"{team}-qb2", "player_display_name": f"{team} Backup",
                         "attempts": 2, "sacks_suffered": 0, "carries": 0, "passing_epa": -2.0, "rushing_epa": 0.0})
    store.replace_dataset("player_stats", season, rows)
    store.replace_dataset("injuries", season, [{"season": season, "season_type": "REG", "game_type": "REG", "week": 5,
                                                "team": "AA", "gsis_id": "AA-qb", "full_name": "AA Passer",
                                                "position": "QB", "report_status": "Questionable",
                                                "report_primary_injury": "Knee"}])
    store.replace_dataset("weekly_rosters", season, [{"season": season, "week": 5, "team": "AA", "gsis_id": "AA-qb",
                                                      "espn_id": "900", "full_name": "AA Passer", "status": "ACT",
                                                      "game_type": "REG"}])
    nfl_props._week_inputs.cache_clear()
    monkeypatch.setattr(nfl_service, "_recent_games", lambda team, *_: [{"nflverse_id": f"{season}_{w:02d}_BB_AA"} for w in range(1, 5)])
    espn = [{"espn_id": "900", "name": "AA Passer", "team": "AA", "position": "QB", "status": "Out", "injury": "Knee",
             "updated": "2026-10-23T10:00Z", "source": "ESPN"},
            {"espn_id": "901", "name": "BB Linebacker", "team": "BB", "position": "LB", "status": "Doubtful", "injury": "Ankle",
             "updated": None, "source": "ESPN"}]
    game = {"season": season, "week": 5, "home_id": "AA", "away_id": "BB", "kickoff": "2026-10-25T17:00:00+00:00"}
    sides = impact.nfl_players(game, [], espn)
    starter = next(p for p in sides["home"] if p["name"] == "AA Passer")
    # ESPN's later Out overrides Thursday's official Questionable; both sources are credited.
    assert starter["status"] == "Out" and starter["availability"] == 0 and starter["sources"] == ["ESPN", "NFL report"]
    assert starter["value"] > 3 and starter["played_share"] == 1.0
    linebacker = next(p for p in sides["away"] if p["name"] == "BB Linebacker")
    assert linebacker["value"] is None and linebacker["availability"] == pytest.approx(0.02)
    report = impact.margin_report(0.0, impact.NFL_SIGMA, sides, priced=False)
    assert report["teams"]["home"]["win_now"] < 0.4


def test_soccer_tackle_picks_price_the_median_line_and_skip_unproven_markets():
    from services import fpl_props as soccer_props
    history = [{"round": r, "minutes": 90, "tackles": t, "expected_goals": 0.1, "expected_assists": 0.1,
                "goals_scored": 0, "assists": 0} for r, t in enumerate([1, 2, 1, 2, 1, 3], 1)]
    past = {"minutes": 2700, "tackles": 30, "expected_goals": 1.0, "expected_assists": 1.0}
    markets = soccer_props.player_markets(3, history, past, 1.3, 1.3, 1.1, 1.1)
    tackles = next(m for m in markets if m["market"] == "tackles")
    assert tackles["line"] == 1.5 and tackles["ladder"]["start"] == 0
    assert {m["market"] for m in markets} == {"goal", "assist", "tackles"}
    picks = soccer_props.best_picks([{"name": "M", "team": "AA", "position": "MID", "markets": markets}])
    assert all(p["market"] == "tackles" and p["probability"] > 0.5 for p in picks)
    keeper = soccer_props.player_markets(1, [{**row, "saves": 3} for row in history], None, 1.3, 1.3, 1.1, 1.1)
    assert [m["market"] for m in keeper] == ["saves"]
    assert soccer_props.best_picks([{"name": "K", "team": "AA", "position": "GK", "markets": keeper}]) == []
