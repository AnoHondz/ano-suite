import pytest

import app as module
import decision_ui
import nba_routes


def card(sport="soccer"):
    return {"match_id": "42", "sport": sport, "team_a": "Arsenal", "team_b": "Chelsea", "matchup": "Arsenal vs Chelsea",
            "action": "SKIP", "recommended_side": "Arsenal", "confidence": 80,
            "probabilities": {"a": 70, "draw": 20, "b": 10}, "data_quality": "Strong Data",
            "reason": "Risk guard", "game_date": "2026-10-17T16:30:00Z"}


def test_match_analysis_rebuilds_when_short_lived_snapshot_is_missing(monkeypatch):
    monkeypatch.setattr(module.prediction_service, "get_fixture_cards", lambda _, **kw: ([card()], [], None, "mock", ""))
    response = module.app.test_client().get("/api/matches/42?sport=soccer&league_id=39&date=2026-10-17")
    assert response.status_code == 200
    assert response.json["decision"]["matchId"] == "42"
    assert response.json["decision"]["action"] == "SKIP"


def test_nba_insights_uses_nba_cards_instead_of_filtering_soccer(monkeypatch):
    nba = decision_ui.card_to_decision(card("nba"))
    monkeypatch.setattr(nba_routes, "api_dashboard_nba", lambda: module.jsonify({"slate": [nba]}))
    monkeypatch.setattr(module.prediction_service, "get_fixture_cards", lambda _: pytest.fail("NBA filter must not load soccer"))
    response = module.app.test_client().get("/api/dashboard/insights?sport=nba")
    assert response.status_code == 200
    assert response.json["radarCards"][0]["sport"] == "nba"
    assert response.json["plan"]["skip"] == 1


def test_provider_failure_returns_explicit_unavailability(monkeypatch):
    monkeypatch.setattr(module.prediction_service, "get_fixture_cards", lambda _, **kw: ([], [], "Provider unavailable", "mock", ""))
    response = module.app.test_client().get("/api/dashboard/soccer")
    assert response.status_code == 503
    assert response.json["error"]


def test_schedule_without_prediction_never_generates_a_bet():
    game = {"id": "game", "teams": {"home": {"name": "Home"}, "visitors": {"name": "Away"}}}
    assert nba_routes._fallback_prediction_card_from_game(game)["prediction"] is None
    assert module._decision_card_from_nba_game(game) is None


def test_results_do_not_generate_predictions_for_completed_untracked_games():
    assert module._recent_nba_result_records() == []
    assert module._recent_soccer_result_records() == []


def test_nba_scoreline_with_team_ids_uses_the_distribution_service(monkeypatch):
    from datetime import UTC, datetime, timedelta
    start = datetime.now(UTC) - timedelta(days=3)
    games = [{"id": f"g{i}", "season_type": 2, "date": {"start": (start - timedelta(days=2 * i)).isoformat()},
              "status": {"state": "post", "completed": True},
              "teams": {"home": {"id": "1"}, "visitors": {"id": "2"}} if i % 2 else {"home": {"id": "2"}, "visitors": {"id": "1"}},
              "scores": {"home": {"points": 112 + i % 5}, "visitors": {"points": 108}}} for i in range(12)]
    monkeypatch.setattr(nba_routes.nc, "get_team_recent_form_context", lambda *a, **kw: {"current_games": games})
    monkeypatch.setattr(nba_routes.nc, "get_team_season_games", lambda *a, **kw: [])
    monkeypatch.setattr(nba_routes, "_nba_player_pools", lambda *a: (None, None))
    response = module.app.test_client().get("/api/scoreline/nba?team_a_id=1&team_b_id=2")
    assert response.status_code == 200
    result = response.json["result"]
    assert result["sport"] == "nba" and result["model"] == "nba-game-projection-v1"
    assert result["projected_score"]["home"] + result["projected_score"]["away"] == pytest.approx(result["projected_score"]["total"], abs=0.11)
    assert result["players"] == {"home": None, "away": None} and result["matchups"] is None


def test_spa_build_preserves_api_and_static_boundaries(monkeypatch, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>Application shell</html>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("/* bundle */", encoding="utf-8")
    monkeypatch.setattr(module, "_SPA_DIST", tmp_path)
    monkeypatch.setattr(module, "_SPA_INDEX", tmp_path / "index.html")
    monkeypatch.setitem(module.app.config, "SERVE_SPA", True)
    client = module.app.test_client()
    assert b"Application shell" in client.get("/match-analysis?match_id=42").data
    assert b"Application shell" in client.get("/nfl").data
    assert b"Application shell" in client.get("/nfl/").data
    assert b"Application shell" in client.get("/nfl?date=2026-10-18").data
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/session").is_json
    assert client.post("/api/bets", json={}).status_code == 401
    assert client.get("/unknown-page").status_code == 404


def test_nba_schedule_survives_missing_directory_and_thin_prediction_data(monkeypatch):
    import nba_live_client

    game = {"id": "nba-real", "date": {"start": "2026-10-18T23:00Z"}, "status": {"state": "pre"},
            "teams": {"home": {"id": "19", "name": "Orlando Magic"}, "visitors": {"id": "14", "name": "Miami Heat"}}}
    monkeypatch.setattr(nba_live_client, "get_upcoming_games", lambda *args: [game])
    monkeypatch.setattr(nba_live_client, "get_teams", list)
    monkeypatch.setattr(nba_live_client, "get_standings", dict)
    def unavailable_prediction(match, teams, strengths):
        assert teams["19"]["name"] == "Orlando Magic"
        return {"prediction": None, "unavailable_reason": "Insufficient current-season records."}
    monkeypatch.setattr(nba_routes, "_build_upcoming_prediction_card", unavailable_prediction)
    response = module.app.test_client().get("/api/dashboard/nba")
    assert response.status_code == 200
    row = response.json["slate"][0]
    assert row["matchId"] == "nba-real"
    assert row["sport"] == "nba"
    assert row["action"] == "SKIP"
    assert row["winProbability"] is None
    assert response.json["plan"]["skip"] == 1
    assert response.json["error"] is None


def test_soccer_insights_preserves_league_for_cold_match_links(monkeypatch):
    monkeypatch.setattr(module.prediction_service, "get_fixture_cards", lambda _, **kw: ([card()], [], None, "mock", ""))
    response = module.app.test_client().get("/api/dashboard/insights?sport=soccer&league_id=39")
    assert response.status_code == 200
    assert response.json["radarCards"][0]["leagueId"] == 39


def test_named_win_selection_keeps_home_probability_and_opponent():
    from datetime import UTC, datetime, timedelta
    from services.data_freshness import metadata
    now = datetime.now(UTC)
    raw = dict(card(), recommended_side="Arsenal Win!", team_a_logo="home-logo", team_b_logo="away-logo",
               game_date=(now+timedelta(hours=4)).isoformat(),event_status='pre',
               data_freshness=metadata('fixture',now.isoformat(),60,now=now))
    row = decision_ui.card_to_decision(raw)
    assert row["winProbability"] == 70
    assert row["opponent"] == "Chelsea"
    assert row["logo"] == "home-logo"
    assert row["action"] == "SKIP"
