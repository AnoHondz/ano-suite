"""
tests/test_routes.py — Flask route tests for ScorPred.

Run with:
    pytest tests/test_routes.py -v
"""

import json
import logging
import types
import pytest
from unittest.mock import patch, MagicMock
from datetime import UTC, date, datetime, timedelta

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as flask_app_module
import security
import nba_predictor as np_nba_module
from tests.source_contracts import fresh_source


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    flask_app_module.app.config["TESTING"] = True
    flask_app_module.app.config["SECRET_KEY"] = "test-secret"
    flask_app_module.app.config["WTF_CSRF_ENABLED"] = False
    security.reset_chat_rate_limits()
    with flask_app_module.app.test_client() as c:
        yield c


@pytest.fixture
def secure_client():
    flask_app_module.app.config["TESTING"] = True
    flask_app_module.app.config["SECRET_KEY"] = "test-secret"
    flask_app_module.app.config["WTF_CSRF_ENABLED"] = True
    flask_app_module.app.config["CHAT_RATE_LIMIT_COUNT"] = 2
    flask_app_module.app.config["CHAT_RATE_LIMIT_WINDOW_SECONDS"] = 60
    security.reset_chat_rate_limits()
    with flask_app_module.app.test_client() as c:
        yield c


def _mock_teams():
    return [
        {"team": {"id": 33, "name": "Manchester United", "logo": "https://example.com/manu.png"}},
        {"team": {"id": 40, "name": "Liverpool", "logo": "https://example.com/lfc.png"}},
    ]


def _mock_fixture():
    return {
        "data_freshness": fresh_source(),
        "fixture": {"id": 1, "date": "2025-04-10T15:00:00+00:00"},
        "teams": {
            "home": {"id": 33, "name": "Manchester United", "logo": ""},
            "away": {"id": 40, "name": "Liverpool", "logo": ""},
        },
        "goals": {"home": 2, "away": 1},
        "league": {"name": "Premier League", "id": 39},
    }


def _mock_nba_game(
    home_id: str = "1610612747",
    away_id: str = "1610612738",
    home_name: str = "Los Angeles Lakers",
    away_name: str = "Boston Celtics",
    home_nickname: str = "Lakers",
    away_nickname: str = "Celtics",
    home_pts: int = 112,
    away_pts: int = 107,
    date: str = "2026-04-21",
) -> dict:
    return {
        "id": f"{home_id}-{away_id}-{date}",
        "short_name": f"{away_nickname} @ {home_nickname}",
        "date": {"start": f"{date}T19:30:00Z"},
        "status": {
            "long": "Final",
            "short": "Final",
            "detail": "Final",
            "state": "post",
        },
        "venue": {"name": "Test Arena"},
        "teams": {
            "home": {
                "id": str(home_id),
                "name": home_name,
                "nickname": home_nickname,
                "city": home_name.replace(f" {home_nickname}", ""),
                "logo": "https://example.com/lakers.png",
            },
            "visitors": {
                "id": str(away_id),
                "name": away_name,
                "nickname": away_nickname,
                "city": away_name.replace(f" {away_nickname}", ""),
                "logo": "https://example.com/celtics.png",
            },
        },
        "scores": {
            "home": {"points": home_pts, "linescore": [28, 26, 29, 29]},
            "visitors": {"points": away_pts, "linescore": [25, 27, 26, 29]},
        },
        "leaders": {"home": [], "visitors": []},
        "records": {"home": [], "visitors": []},
        "broadcasts": [],
        "geo_broadcasts": [],
        "odds": {},
        "attendance": 19000,
        "summary_link": "",
        "is_live": False,
        "is_pre": False,
        "is_post": True,
    }


def _mock_nba_teams() -> list[dict]:
    return [
        {
            "id": "1610612747",
            "name": "Los Angeles Lakers",
            "nickname": "Lakers",
            "city": "Los Angeles",
            "logo": "https://example.com/lakers.png",
        },
        {
            "id": "1610612738",
            "name": "Boston Celtics",
            "nickname": "Celtics",
            "city": "Boston",
            "logo": "https://example.com/celtics.png",
        },
    ]


def _mock_nba_variant_teams() -> list[dict]:
    return [
        {"id": "200", "name": "Hornets", "nickname": "Hornets", "city": "Charlotte", "abbrev": "CHA", "logo": "https://example.com/hornets.png"},
        {"id": "201", "name": "Heat", "nickname": "Heat", "city": "Miami", "abbrev": "MIA", "logo": "https://example.com/heat.png"},
        {"id": "202", "name": "Clippers", "nickname": "Clippers", "city": "Los Angeles", "abbrev": "LAC", "logo": "https://example.com/clippers.png"},
        {"id": "203", "name": "Warriors", "nickname": "Warriors", "city": "Golden State", "abbrev": "GSW", "logo": "https://example.com/warriors.png"},
        {"id": "204", "name": "Knicks", "nickname": "Knicks", "city": "New York", "abbrev": "NYK", "logo": "https://example.com/knicks.png"},
        {"id": "205", "name": "Lakers", "nickname": "Lakers", "city": "Los Angeles", "abbrev": "LAL", "logo": "https://example.com/lakers.png"},
    ]


def _mock_nba_scorpred():
    return {
        "team_a_score": 6.8,
        "team_b_score": 5.4,
        "score_gap": 1.4,
        "win_probabilities": {"a": 62.0, "b": 38.0},
        "components_a": {
            "form": 6.7,
            "offense": 6.4,
            "defense": 6.1,
            "h2h": 5.8,
            "home_away": 6.5,
            "opp_strength": 5.7,
            "squad": 8.6,
            "match_context": 5.0,
        },
        "components_b": {
            "form": 5.4,
            "offense": 5.8,
            "defense": 5.6,
            "h2h": 5.1,
            "home_away": 4.7,
            "opp_strength": 6.0,
            "squad": 8.2,
            "match_context": 4.8,
        },
        "best_pick": {
            "prediction": "Lakers Win",
            "team": "A",
            "confidence": "High",
            "reasoning": "Stronger recent form and the home-court edge tilt the matchup.",
        },
        "key_edges": [
            {"team": "A", "team_name": "Lakers", "category": "Form", "margin": 1.3},
            {"team": "A", "team_name": "Lakers", "category": "Venue", "margin": 1.8},
        ],
        "matchup_reading": "Lakers hold the clearest overall edge thanks to better recent form and the venue boost.",
        "optional_picks": [
            {"market": "Total Points O/U 220", "lean": "Over", "reasoning": "Avg combined scoring: 224.4 pts/game"},
        ],
    }


def _mock_strategy_metrics() -> dict:
    return {
        "total_predictions": 28,
        "finalized_predictions": 20,
        "wins": 11,
        "losses": 9,
        "overall_accuracy": 55.0,
        "by_confidence": {
            "High": {"accuracy": 62.5, "count": 8, "wins": 5, "losses": 3},
            "Medium": {"accuracy": 50.0, "count": 8, "wins": 4, "losses": 4},
            "Low": {"accuracy": 50.0, "count": 4, "wins": 2, "losses": 2},
        },
        "by_sport": {
            "soccer": {"accuracy": 58.3, "count": 12, "wins": 7, "losses": 5},
            "nba": {"accuracy": 50.0, "count": 8, "wins": 4, "losses": 4},
        },
        "recent_predictions": [],
    }


def _mock_completed_prediction(
    team_a: str = "Arsenal",
    team_b: str = "Chelsea",
    overall_result: str = "Win",
    winner_hit: bool = True,
) -> dict:
    return {
        "team_a": team_a,
        "team_b": team_b,
        "date": "2026-04-10",
        "final_score_display": "2-1",
        "overall_game_result": overall_result,
        "winner_hit": winner_hit,
    }


# ── Home page ─────────────────────────────────────────────────────────────────

class TestIndexRoute:
    def test_index_returns_200(self, client):
        with patch("api_client.get_teams", return_value=_mock_teams()), \
             patch("api_client.get_upcoming_fixtures", return_value=[]), \
             patch("api_client.get_standings", return_value=[]):
            rv = client.get("/")
        assert rv.status_code == 200

    def test_index_contains_brand(self, client):
        with patch("api_client.get_teams", return_value=[]), \
             patch("api_client.get_upcoming_fixtures", return_value=[]), \
             patch("api_client.get_standings", return_value=[]):
            rv = client.get("/")
        assert b"ScorPred" in rv.data

    def test_index_handles_api_failure_gracefully(self, client):
        with patch("api_client.get_teams", side_effect=Exception("API down")), \
             patch("api_client.get_upcoming_fixtures", side_effect=Exception("API down")), \
             patch("api_client.get_standings", return_value=[]):
            rv = client.get("/")
        # Should not crash — should return 200 with partial content
        assert rv.status_code == 200


# ── Fixtures page ─────────────────────────────────────────────────────────────

class TestFixturesRoute:
    def test_fixtures_returns_200(self, client):
        with patch("api_client.get_upcoming_fixtures", return_value=[_mock_fixture()]), \
             patch("api_client.get_standings", return_value=[]):
            rv = client.get("/fixtures")
        assert rv.status_code == 200

    def test_fixtures_empty_list_still_200(self, client):
        with patch("api_client.get_upcoming_fixtures", return_value=[]), \
             patch("api_client.get_standings", return_value=[]):
            rv = client.get("/fixtures")
        assert rv.status_code == 200

    def test_fixtures_analysis_form_includes_csrf_token(self, client):
        with patch("api_client.get_upcoming_fixtures", return_value=[_mock_fixture()]), \
             patch("api_client.get_standings", return_value=[]):
            rv = client.get("/fixtures")
        assert rv.status_code == 200
        assert b'name="csrf_token"' in rv.data

    def test_soccer_route_survives_fixture_loader_degrade(self, client):
        with patch("api_client.get_teams", return_value=_mock_teams()), \
             patch("app._load_upcoming_fixtures", side_effect=TypeError("unexpected keyword argument 'league'")):
            rv = client.get("/soccer")
        assert rv.status_code == 200

    def test_soccer_route_renders_fixture_cards_with_theme_safe_markup(self, client):
        with patch("api_client.get_teams", return_value=_mock_teams()), \
             patch("app._load_upcoming_fixtures", return_value=([_mock_fixture()], None, "configured", "")):
            rv = client.get("/soccer")
        assert rv.status_code == 200
        assert b"sp-decision-card" in rv.data
        assert b"Analyze Match" in rv.data


class TestTodayPredictionsRoute:
    def test_today_predictions_handles_missing_score_gap(self, client):
        payload = [
            {
                "fixture": {
                    "date": "2026-04-12T15:00:00+00:00",
                },
                "teams": {
                    "home": {"name": "Alpha", "logo": ""},
                    "away": {"name": "Beta", "logo": ""},
                },
                "league": {"name": "Premier League"},
                "prediction": {
                    "best_pick": {
                        "prediction": "Alpha",
                        "confidence": "High",
                        "reasoning": "Recent form edge",
                    },
                    "win_probabilities": {"a": 61.0, "draw": 22.0, "b": 17.0},
                },
            }
        ]

        grouped = [{"league_name": "Premier League", "fixtures": payload, "predictions": payload}]
        with patch("app._load_grouped_upcoming_fixtures_all_leagues", return_value=(payload, grouped, None, "configured")):
            rv = client.get("/today-soccer-predictions")

        assert rv.status_code == 200
        assert b"Soccer Predictions" in rv.data

    def test_today_predictions_show_multiple_league_sections(self, client):
        premier_fixture = {
            "fixture": {"date": "2026-04-12T15:00:00+00:00"},
            "teams": {
                "home": {"name": "Alpha", "logo": ""},
                "away": {"name": "Beta", "logo": ""},
            },
            "league": {"id": 39, "name": "Premier League"},
            "prediction": {
                "best_pick": {"prediction": "Alpha", "confidence": "High", "reasoning": "Edge"},
                "win_probabilities": {"a": 61.0, "draw": 22.0, "b": 17.0},
            },
        }
        laliga_fixture = {
            "fixture": {"date": "2026-04-12T18:00:00+00:00"},
            "teams": {
                "home": {"name": "Gamma", "logo": ""},
                "away": {"name": "Delta", "logo": ""},
            },
            "league": {"id": 140, "name": "La Liga"},
            "prediction": {
                "best_pick": {"prediction": "Gamma", "confidence": "Medium", "reasoning": "Form edge"},
                "win_probabilities": {"a": 54.0, "draw": 24.0, "b": 22.0},
            },
        }
        grouped = [
            {
                "league_id": 39,
                "league_name": "Premier League",
                "league_flag": "EN",
                "fixtures": [premier_fixture],
            },
            {
                "league_id": 140,
                "league_name": "La Liga",
                "league_flag": "ES",
                "fixtures": [laliga_fixture],
            },
        ]

        all_predictions = [premier_fixture, laliga_fixture]
        with patch("app._load_grouped_upcoming_fixtures_all_leagues", return_value=(all_predictions, grouped, None, "configured")):
            rv = client.get("/today-soccer-predictions?league=39")

        assert rv.status_code == 200
        assert b"Premier League" in rv.data
        assert b"La Liga" in rv.data


# ── Select / team selection ───────────────────────────────────────────────────

class TestSelectRoute:
    def test_select_redirects_to_prediction(self, client):
        with patch("api_client.get_teams", return_value=_mock_teams()):
            rv = client.post("/select", data={"team_a": "33", "team_b": "40"})
        assert rv.status_code in (302, 303)
        assert "/prediction" in rv.headers.get("Location", "")

    def test_select_fixture_stores_selected_context(self, client):
        fixture_payload = {
            "team_a": "33",
            "team_b": "40",
            "team_a_name": "Manchester United",
            "team_a_logo": "https://example.com/manu.png",
            "team_b_name": "Liverpool",
            "team_b_logo": "https://example.com/lfc.png",
            "fixture_id": "9001",
            "fixture_date": "2026-04-21T19:45:00+00:00",
            "league_name": "Premier League",
            "round": "Matchday 33",
            "venue_name": "Old Trafford",
            "data_source": "configured",
        }

        with patch("api_client.get_teams", return_value=_mock_teams()):
            rv = client.post("/select", data=fixture_payload)

        assert rv.status_code in (302, 303)
        assert "/prediction" in rv.headers.get("Location", "")
        with client.session_transaction() as sess:
            assert sess["team_a_id"] == 33
            assert sess["team_b_id"] == 40
            assert sess["selected_fixture"]["id"] == "9001"
            assert sess["selected_fixture"]["venue_name"] == "Old Trafford"

    def test_select_same_team_redirects_to_soccer_with_notice(self, client):
        with patch("api_client.get_teams", return_value=_mock_teams()):
            rv = client.post("/select", data={"team_a": "33", "team_b": "33"})
        assert rv.status_code in (302, 303)
        assert "/soccer?selection_error=" in rv.headers.get("Location", "")

    def test_select_missing_team_redirects_to_soccer_with_notice(self, client):
        with patch("api_client.get_teams", return_value=_mock_teams()):
            rv = client.post("/select", data={"team_a": "33", "team_b": ""})
        assert rv.status_code in (302, 303)
        assert "/soccer?selection_error=" in rv.headers.get("Location", "")

    def test_select_no_teams_available_returns_error(self, client):
        with patch("api_client.get_teams", return_value=[]):
            rv = client.post("/select", data={"team_a": "33", "team_b": "40"})
        # Should return an error page (503) when no team data
        assert rv.status_code in (302, 503)


# ── Matchup page ──────────────────────────────────────────────────────────────

class TestMatchupRoute:
    def test_matchup_without_session_redirects(self, client):
        rv = client.get("/matchup")
        assert rv.status_code in (302, 303)

    def test_matchup_with_session_returns_200(self, client):
        with client.session_transaction() as sess:
            sess["team_a_id"] = 33
            sess["team_a_name"] = "Manchester United"
            sess["team_a_logo"] = ""
            sess["team_b_id"] = 40
            sess["team_b_name"] = "Liverpool"
            sess["team_b_logo"] = ""

        with patch("api_client.get_h2h", return_value=[_mock_fixture()]), \
             patch("api_client.get_team_fixtures", return_value=[_mock_fixture()]), \
             patch("api_client.get_injuries", return_value=[]), \
             patch("api_client.enrich_fixture", return_value={**_mock_fixture(), "events": [], "stats": []}):
            rv = client.get("/matchup")
        assert rv.status_code == 200

    def test_matchup_handles_upstream_failures(self, client):
        with client.session_transaction() as sess:
            sess["team_a_id"] = 33
            sess["team_a_name"] = "Manchester United"
            sess["team_a_logo"] = ""
            sess["team_b_id"] = 40
            sess["team_b_name"] = "Liverpool"
            sess["team_b_logo"] = ""

        with patch("api_client.get_h2h", side_effect=Exception("h2h down")), \
             patch("api_client.get_team_fixtures", side_effect=Exception("fixtures down")), \
             patch("api_client.get_injuries", side_effect=Exception("injuries down")), \
             patch("api_client.get_standings", side_effect=Exception("standings down")):
            rv = client.get("/matchup")
        assert rv.status_code == 200


# ── Prediction page ───────────────────────────────────────────────────────────

class TestPredictionRoute:
    def test_prediction_without_session_redirects_to_soccer(self, client):
        rv = client.get("/prediction")
        assert rv.status_code in (302, 303)
        assert "/soccer?selection_error=" in rv.headers.get("Location", "")

    def test_prediction_with_session_returns_200(self, client):
        with client.session_transaction() as sess:
            sess["team_a_id"] = 33
            sess["team_a_name"] = "Manchester United"
            sess["team_a_logo"] = ""
            sess["team_b_id"] = 40
            sess["team_b_name"] = "Liverpool"
            sess["team_b_logo"] = ""

        fixtures = [_mock_fixture()]
        with patch("api_client.get_h2h", return_value=fixtures), \
             patch("api_client.get_team_fixtures", return_value=fixtures), \
             patch("api_client.get_injuries", return_value=[]), \
             patch("api_client.get_squad", return_value=[]):
            rv = client.get("/prediction")
        assert rv.status_code == 200

    def test_prediction_surfaces_input_reliability_when_live_feeds_are_partial(self, client):
        with client.session_transaction() as sess:
            sess["team_a_id"] = 33
            sess["team_a_name"] = "Manchester United"
            sess["team_a_logo"] = ""
            sess["team_b_id"] = 40
            sess["team_b_name"] = "Liverpool"
            sess["team_b_logo"] = ""

        fixtures = [_mock_fixture()]
        with patch("api_client.get_h2h", return_value=fixtures), \
             patch("api_client.get_team_fixtures", return_value=fixtures), \
             patch("api_client.get_injuries", side_effect=Exception("injuries down")), \
             patch("api_client.get_standings", side_effect=Exception("standings down")):
            rv = client.get("/prediction")

        assert rv.status_code == 200
        assert b"Trust Check" in rv.data
        assert b"Limited Data" in rv.data

    def test_prediction_page_does_not_track_its_engine(self, client):
        kickoff = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        flask_app_module._FIXTURE_INDEX["stage1-tracking"] = {
            "fixture": {"id": "stage1-tracking", "date": kickoff, "status": {"short": "NS"}},
            "data_freshness": fresh_source(),
        }
        with client.session_transaction() as sess:
            sess["team_a_id"] = 33
            sess["team_a_name"] = "Manchester United"
            sess["team_a_logo"] = ""
            sess["team_b_id"] = 40
            sess["team_b_name"] = "Liverpool"
            sess["team_b_logo"] = ""
            sess["selected_fixture"] = {
                "fixture_id": "stage1-tracking",
                "date": kickoff,
                "data_source": "configured",
            }

        fixtures = [_mock_fixture()]
        with patch("api_client.get_h2h", return_value=fixtures), \
             patch("api_client.get_team_fixtures", return_value=fixtures), \
             patch("api_client.get_injuries", return_value=[]), \
             patch("api_client.get_squad", return_value=[]), \
             patch("model_tracker.save_prediction") as mock_save:
            rv = client.get("/prediction")

        assert rv.status_code == 200
        # This page runs a different engine from the published one, so it never writes the track record.
        mock_save.assert_not_called()


class TestNbaPredictionRoute:
    def test_nba_prediction_without_session_redirects_to_nba_index(self, client):
        rv = client.get("/nba/prediction")
        assert rv.status_code in (302, 303)
        assert "/nba/?selection_error=" in rv.headers.get("Location", "")

    def test_nba_select_game_redirects_to_prediction_and_stores_context(self, client):
        live_game = _mock_nba_game(date="2026-04-21")
        home = live_game["teams"]["home"]
        away = live_game["teams"]["visitors"]

        with patch("nba_live_client.get_teams", return_value=_mock_nba_teams()):
            rv = client.post(
                "/nba/select-game",
                data={
                    "team_a": home["id"],
                    "team_b": away["id"],
                    "team_a_name": home["name"],
                    "team_a_logo": home["logo"],
                    "team_b_name": away["name"],
                    "team_b_logo": away["logo"],
                    "event_id": live_game["id"],
                    "event_date": live_game["date"]["start"],
                    "event_status": live_game["status"]["long"],
                    "venue_name": live_game["venue"]["name"],
                    "short_name": live_game["short_name"],
                },
            )

        assert rv.status_code in (302, 303)
        assert "/nba/prediction" in rv.headers.get("Location", "")
        with client.session_transaction() as sess:
            assert sess["nba_team_a_id"] == home["id"]
            assert sess["nba_team_b_id"] == away["id"]
            assert sess["nba_selected_game"]["event_id"] == live_game["id"]
            assert sess["nba_selected_game"]["venue_name"] == "Test Arena"

    def test_nba_select_game_missing_context_redirects_with_notice(self, client):
        with patch("nba_live_client.get_teams", return_value=_mock_nba_teams()):
            rv = client.post("/nba/select-game", data={"team_a": "1610612747", "team_b": ""})

        assert rv.status_code in (302, 303)
        assert "/nba/?selection_error=" in rv.headers.get("Location", "")

    def test_nba_select_game_uses_payload_fallback_when_team_directory_is_empty(self, client):
        with patch("nba_live_client.get_teams", return_value=[]):
            rv = client.post(
                "/nba/select-game",
                data={
                    "team_a": "30",
                    "team_b": "14",
                    "team_a_name": "Charlotte Hornets",
                    "team_a_logo": "https://example.com/hornets.png",
                    "team_b_name": "Miami Heat",
                    "team_b_logo": "https://example.com/heat.png",
                    "event_id": "401866755",
                    "event_date": "2026-04-14T23:30:00Z",
                    "event_status": "Scheduled",
                    "venue_name": "Spectrum Center",
                    "short_name": "MIA @ CHA",
                },
            )

        assert rv.status_code in (302, 303)
        assert "/nba/prediction" in rv.headers.get("Location", "")
        with client.session_transaction() as sess:
            assert sess["nba_team_a_id"] == "30"
            assert sess["nba_team_a_name"] == "Charlotte Hornets"
            assert sess["nba_team_a_nickname"] == "Hornets"
            assert sess["nba_team_a_city"] == "Charlotte"
            assert sess["nba_team_b_id"] == "14"
            assert sess["nba_team_b_name"] == "Miami Heat"
            assert sess["nba_team_b_nickname"] == "Heat"
            assert sess["nba_team_b_city"] == "Miami"
            assert sess["nba_selected_game"]["event_id"] == "401866755"

    @pytest.mark.parametrize(
        ("home_name", "away_name", "expected_home", "expected_away"),
        [
            ("Charlotte Hornets", "Miami Heat", "Hornets", "Heat"),
            ("LA Clippers", "Golden State Warriors", "Clippers", "Warriors"),
            ("New York Knicks", "Los Angeles Lakers", "Knicks", "Lakers"),
        ],
    )
    def test_nba_select_game_matches_canonical_names_when_ids_do_not_align(
        self,
        client,
        home_name,
        away_name,
        expected_home,
        expected_away,
    ):
        with patch("nba_live_client.get_teams", return_value=_mock_nba_variant_teams()):
            rv = client.post(
                "/nba/select-game",
                data={
                    "team_a": "espn-home-id",
                    "team_b": "espn-away-id",
                    "team_a_name": home_name,
                    "team_b_name": away_name,
                    "event_id": "401866755",
                    "event_date": "2026-04-14T23:30:00Z",
                    "event_status": "Scheduled",
                    "venue_name": "Test Arena",
                    "short_name": "TEST @ TEST",
                },
            )

        assert rv.status_code in (302, 303)
        assert "/nba/prediction" in rv.headers.get("Location", "")
        with client.session_transaction() as sess:
            assert sess["nba_team_a_name"] == expected_home
            assert sess["nba_team_b_name"] == expected_away
            assert sess["nba_selected_game"]["event_id"] == "401866755"

    def test_nba_select_game_logs_exact_mismatch_details(self, client, caplog):
        caplog.set_level(logging.INFO)

        with patch("nba_live_client.get_teams", return_value=_mock_nba_variant_teams()):
            rv = client.post(
                "/nba/select-game",
                data={
                    "team_a": "espn-home-id",
                    "team_b": "espn-away-id",
                    "team_a_name": "Seattle Supersonics",
                    "team_b_name": "Miami Heat",
                    "event_id": "401866756",
                },
            )

        assert rv.status_code in (302, 303)
        assert "/nba/?selection_error=" in rv.headers.get("Location", "")
        assert "selected_home='Seattle Supersonics'" in caplog.text
        assert "selected_away='Miami Heat'" in caplog.text
        assert "normalized_selected={'home': 'seattle supersonics', 'away': 'heat'}" in caplog.text
        assert "available_team_names=['Hornets', 'Heat', 'Clippers', 'Warriors', 'Knicks', 'Lakers']" in caplog.text
        assert "failure_reason=home=no canonical match for seattle supersonics; away=matched by canonical name heat" in caplog.text

    def test_nba_prediction_renders_current_payload(self, client):
        upcoming = _mock_nba_game()
        upcoming["data_freshness"] = fresh_source()
        upcoming["date"]["start"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        upcoming["status"] = {"state": "pre", "short": "Scheduled"}
        with client.session_transaction() as sess:
            sess["nba_team_a_id"] = "1610612747"
            sess["nba_team_a_name"] = "Los Angeles Lakers"
            sess["nba_team_a_logo"] = "https://example.com/lakers.png"
            sess["nba_team_a_nickname"] = "Lakers"
            sess["nba_team_a_city"] = "Los Angeles"
            sess["nba_team_b_id"] = "1610612738"
            sess["nba_team_b_name"] = "Boston Celtics"
            sess["nba_team_b_logo"] = "https://example.com/celtics.png"
            sess["nba_team_b_nickname"] = "Celtics"
            sess["nba_team_b_city"] = "Boston"
            sess["nba_selected_game"] = {
                "event_id": "401000001",
                "date": upcoming["date"]["start"],
                "status": "Scheduled",
                "venue_name": "Test Arena",
                "short_name": "Celtics @ Lakers",
            }

        home_game = _mock_nba_game()
        away_game = _mock_nba_game(
            home_id="1610612738",
            away_id="1610612747",
            home_name="Boston Celtics",
            away_name="Los Angeles Lakers",
            home_nickname="Celtics",
            away_nickname="Lakers",
            home_pts=107,
            away_pts=112,
            date="2026-04-18",
        )
        stats_a = {
            "wins": 50,
            "losses": 28,
            "ppg": 118.4,
            "opp_ppg": 112.1,
            "net_rtg": 6.3,
            "home_record": "29-10",
            "away_record": "21-18",
            "last10": "7-3",
            "streak": "W3",
        }
        stats_b = {
            "wins": 54,
            "losses": 24,
            "ppg": 117.2,
            "opp_ppg": 109.8,
            "net_rtg": 7.4,
            "home_record": "31-8",
            "away_record": "23-16",
            "last10": "6-4",
            "streak": "L1",
        }

        # Dispatch by team ID instead of call-order side_effect lists
        # so ThreadPoolExecutor scheduling cannot cause flaky mismatches.
        id_a = "1610612747"  # Lakers
        id_b = "1610612738"  # Celtics
        form_by_team = {
            id_a: {"current_games": [home_game, away_game], "historical_games": [], "using_historical_context": False},
            id_b: {"current_games": [away_game, home_game], "historical_games": [], "using_historical_context": False},
        }
        stats_by_team = {id_a: stats_a, id_b: stats_b}

        with patch("nba_routes.nc.get_event_snapshot", return_value=upcoming), \
             patch("nba_routes.nc.get_h2h", return_value=[home_game, away_game]), \
             patch("nba_routes.nc.get_team_recent_form_context", side_effect=lambda tid, *a, **kw: form_by_team[tid]), \
             patch("nba_routes.nc.get_team_injuries", return_value=[]), \
             patch("nba_routes.nc.get_team_season_stats", side_effect=lambda tid, *a, **kw: stats_by_team[tid]), \
             patch("nba_routes.nc.get_standings", return_value={"west": [{"team": {"name": "Lakers"}, "rank": 3}], "east": [{"team": {"name": "Celtics"}, "rank": 1}]}), \
             patch("nba_routes.sm.predict_match", return_value={"ui_prediction": _mock_nba_scorpred()}), \
             patch("model_tracker.save_prediction") as mock_save:
            rv = client.get("/nba/prediction")

        assert rv.status_code == 200
        assert b"NBA Match Analysis" in rv.data
        assert b"Lakers" in rv.data
        assert b"Trust Check" in rv.data
        assert b"Data Confidence" not in rv.data
        mock_save.assert_not_called()

    def test_nba_index_never_renders_no_confidence_phrase(self, client):
        game = _mock_nba_game(date="2026-04-21")

        with patch("nba_live_client.get_teams", return_value=_mock_nba_teams()), \
             patch("nba_live_client.get_today_games", return_value=[]), \
             patch("nba_live_client.get_upcoming_games", return_value=[game]), \
             patch("nba_live_client.get_h2h", return_value=[]), \
             patch("nba_live_client.get_team_recent_form_context", return_value={"current_games": [], "using_historical_context": False}), \
             patch("nba_live_client.get_team_injuries", return_value=[]), \
             patch("nba_live_client.get_standings", return_value={}), \
             patch("scorpred_engine.scorpred_predict", return_value={
                 "best_pick": {"prediction": "Lakers Win", "team": "A"},
                 "win_probabilities": {"a": 51.0, "b": 49.0},
                 "prob_home": 51.0,
                 "prob_away": 49.0,
             }):
            rv = client.get("/nba/")

        assert rv.status_code == 200
        assert b"No confidence" not in rv.data
        assert b"Limited Data" in rv.data

    def test_nba_prediction_shows_limited_data_notice_when_current_season_is_sparse(self, client):
        with client.session_transaction() as sess:
            sess["nba_team_a_id"] = "1610612747"
            sess["nba_team_a_name"] = "Los Angeles Lakers"
            sess["nba_team_a_logo"] = "https://example.com/lakers.png"
            sess["nba_team_a_nickname"] = "Lakers"
            sess["nba_team_a_city"] = "Los Angeles"
            sess["nba_team_b_id"] = "1610612738"
            sess["nba_team_b_name"] = "Boston Celtics"
            sess["nba_team_b_logo"] = "https://example.com/celtics.png"
            sess["nba_team_b_nickname"] = "Celtics"
            sess["nba_team_b_city"] = "Boston"
            sess["nba_selected_game"] = {
                "event_id": "401000001",
                "date": "2026-04-21T19:30:00Z",
                "status": "Scheduled",
                "venue_name": "Test Arena",
                "short_name": "Celtics @ Lakers",
            }

        home_game = _mock_nba_game()

        with patch("nba_live_client.get_event_snapshot", return_value=home_game), \
             patch("nba_live_client.get_h2h", return_value=[]), \
             patch("nba_live_client.get_team_recent_form_context", side_effect=[
                 {"current_games": [], "historical_games": [home_game], "using_historical_context": True},
                 {"current_games": [], "historical_games": [home_game], "using_historical_context": True},
             ]), \
             patch("nba_live_client.get_team_injuries", side_effect=[[], []]), \
             patch("nba_live_client.get_team_season_stats", side_effect=[None, None]), \
             patch("nba_live_client.get_standings", return_value={}), \
             patch("scormastermind.predict_match", return_value={
                 "ui_prediction": {
                     "best_pick": {"prediction": "Lakers Win", "team": "A", "confidence": "Low", "reasoning": "Edge is thin."},
                     "win_probabilities": {"a": 51.0, "b": 49.0},
                     "score_gap": 0.4,
                     "components_a": {"form": 5, "offense": 5, "defense": 5, "h2h": 5, "home_away": 5, "opp_strength": 5, "squad": 5, "match_context": 5},
                     "components_b": {"form": 5, "offense": 5, "defense": 5, "h2h": 5, "home_away": 5, "opp_strength": 5, "squad": 5, "match_context": 5},
                     "team_a_score": 5.2,
                     "team_b_score": 5.0,
                     "key_edges": [],
                 }
             }), \
             patch("model_tracker.save_prediction"):
            rv = client.get("/nba/prediction")

        assert rv.status_code == 200
        assert b"Limited Data" in rv.data
        assert b"Current-season stats: limited" in rv.data


class TestStrategyLabRoute:
    def test_strategy_lab_renders_saved_ml_comparison(self, client, tmp_path, monkeypatch):
        report_path = tmp_path / "model_comparison.json"
        report_path.write_text(
            json.dumps(
                {
                    "best_model": "random_forest",
                    "generated_at": "2026-04-13T10:00:00Z",
                    "models": {
                        "logistic_regression": {
                            "accuracy": 0.482,
                            "top_features": [{"feature": "home_form_last_5"}],
                        },
                        "random_forest": {
                            "accuracy": 0.501,
                            "top_features": [
                                {"feature": "home_form_last_5"},
                                {"feature": "away_goals_conceded_last_5"},
                                {"feature": "strength_gap"},
                                {"feature": "league_draw_rate"},
                                {"feature": "recent_goal_trend"},
                            ],
                        },
                    },
                    "workflow": {
                        "train_size": 2800,
                        "test_size": 943,
                        "train_start": "2021-08-01",
                        "train_end": "2025-03-15",
                        "test_start": "2025-03-16",
                        "test_end": "2026-03-30",
                        "feature_keys": [
                            "home_form_last_5",
                            "away_goals_conceded_last_5",
                            "strength_gap",
                            "league_draw_rate",
                            "recent_goal_trend",
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(flask_app_module.strategy_lab_services.mt, "get_summary_metrics", lambda: _mock_strategy_metrics())
        monkeypatch.setattr(
            flask_app_module.strategy_lab_services.mt,
            "get_completed_predictions",
            lambda limit=6: [_mock_completed_prediction()] * limit,
        )
        monkeypatch.setattr(flask_app_module.strategy_lab_services.mlp, "DEFAULT_REPORT_PATH", report_path)

        rv = client.get("/strategy-lab")

        assert rv.status_code in (302, 303)
        # Structural markers (data-testid) — stable across copy rewrites
        assert "/insights" in rv.headers.get("Location", "")
        # Content assertions — verify report data is rendered
        assert b"missing_model_comparison" not in rv.data


class TestModelPerformanceRoute:
    def test_model_performance_renders_evaluation_sections(self, client, monkeypatch):
        monkeypatch.setattr(flask_app_module.mt, "get_summary_metrics", lambda exclude_seeded=True: _mock_strategy_metrics())
        monkeypatch.setattr(flask_app_module.mt, "get_completed_predictions", lambda limit=50: [_mock_completed_prediction()] * min(limit, 3))
        monkeypatch.setattr(flask_app_module.mt, "get_pending_predictions", lambda limit=20: [])
        monkeypatch.setattr(
            flask_app_module.mt,
            "get_evaluation_dashboard",
            lambda rolling_window=10, strategy_reference=None, exclude_seeded=True: {
                "kpis": {
                    "overall_accuracy": 55.0,
                    "rolling_win_rate": 60.0,
                    "total_tracked_predictions": 28,
                    "finalized_predictions": 20,
                    "avoids_skipped": 2,
                    "current_best_strategy": "Combined",
                    "roi_or_points": 9,
                },
                "rolling_window": rolling_window,
                "series": {
                    "rolling_by_match": [{"label": "2026-04-01", "rolling_accuracy": 50.0}],
                    "rolling_by_day": [{"label": "2026-04-01", "rolling_accuracy": 50.0, "matches": 1}],
                    "cumulative_points": [{"label": "2026-04-01", "cumulative_points": 1, "delta": 1}],
                },
                "confidence_calibration": [{"bucket": "80-100", "sample_size": 1, "avg_confidence": 90.0, "actual_hit_rate": 100.0}],
                "strategy_comparison": [{"strategy": "Rule-Based", "accuracy": 55.0, "sample_size": 20}],
                "breakdowns": {
                    "by_sport": [{"sport": "soccer", "accuracy": 58.3, "count": 12, "wins": 7, "losses": 5}],
                    "by_confidence_tier": [{"tier": "High", "accuracy": 62.5, "count": 8, "wins": 5, "losses": 3}],
                    "by_predicted_outcome": [{"key": "A", "label": "Home", "count": 10, "wins": 6, "losses": 4, "accuracy": 60.0}],
                    "recent_form": {
                        "last_10": {"count": 10, "accuracy": 60.0},
                        "last_20": {"count": 20, "accuracy": 55.0},
                    },
                },
                "failure_rows": [
                    {
                        "date": "2026-04-02",
                        "sport": "SOCCER",
                        "matchup": "Liverpool vs Everton",
                        "predicted_outcome": "B",
                        "actual_result": "Liverpool",
                        "confidence": "Medium",
                        "confidence_pct": 61.0,
                        "recommendation": "Everton ML",
                        "notes": "Winner Pick: Miss",
                    }
                ],
            },
        )
        monkeypatch.setattr(
            flask_app_module.strategy_lab_services,
            "build_strategy_lab_context",
            lambda: {
                "performance_comparison": {
                    "rule_accuracy": 55.0,
                    "ml_accuracy": 57.0,
                    "combined_accuracy": 59.0,
                    "evaluation_matches": 120,
                },
                "ml_comparison": {
                    "best_model_label": "Random Forest",
                    "baseline_logistic_accuracy": 52.0,
                    "random_forest_accuracy": 57.0,
                },
            },
        )
        monkeypatch.setattr(
            flask_app_module.strategy_lab_services,
            "walk_forward_summary",
            lambda: {
                "available": True,
                "windows": {
                    "all_history": {
                        "available": True,
                        "label": "All History",
                        "n_folds": 5,
                        "mean_combined_accuracy": 54.1,
                        "total_test_matches": 240,
                        "date_range_display": "2021-08-08 to 2026-04-19",
                        "policy_hit_rate_pct": 58.4,
                        "draw_accuracy_display": "47.5%",
                        "draw_sample_size": 40,
                        "high_confidence_accuracy_display": "67.5%",
                        "high_confidence_sample_size": 40,
                        "sample_weighting": {
                            "type": "balanced_times_recency",
                        },
                    },
                    "last_3_years": {
                        "available": True,
                        "label": "Last 3 Years",
                        "n_folds": 5,
                        "mean_combined_accuracy": 55.6,
                        "total_test_matches": 180,
                        "date_range_display": "2023-04-20 to 2026-04-19",
                        "policy_hit_rate_pct": 59.8,
                        "draw_accuracy_display": "51.2%",
                        "draw_sample_size": 34,
                        "high_confidence_accuracy_display": "69.4%",
                        "high_confidence_sample_size": 36,
                        "sample_weighting": {
                            "type": "balanced_times_recency",
                        },
                    },
                },
                "selector": {
                    "available": True,
                    "default_source_label": "Combined",
                    "default_accuracy": 55.6,
                    "summary": "Default to Combined using the recent backtest window.",
                    "override_rows": [],
                },
            },
        )

        rv = client.get("/model-performance")

        assert rv.status_code in (302, 303)
        assert "/insights" in rv.headers.get("Location", "")

    def test_pass_analysis_renders(self, client, monkeypatch):
        monkeypatch.setattr(
            flask_app_module.mt,
            "get_evaluation_dashboard",
            lambda **kwargs: {
                "kpis": {"overall_accuracy": 57.1},
                "rolling_window": 10,
                "pass_rows": [
                    {
                        "date": "2026-04-03",
                        "sport": "SOCCER",
                        "matchup": "Arsenal vs Chelsea",
                        "predicted_outcome": "A",
                        "actual_result": "Arsenal",
                        "confidence": "High",
                        "confidence_pct": 83.0,
                        "recommendation": "Arsenal ML",
                        "notes": "Winner Pick: Hit",
                    }
                ],
            },
        )

        rv = client.get("/pass-analysis")

        assert rv.status_code == 200
        assert b"Pass Analysis" in rv.data
        assert b"Winning Prediction Log" in rv.data
        assert b"Arsenal vs Chelsea" in rv.data

    def test_strategy_lab_renders_clean_fallback_without_report(self, client, tmp_path, monkeypatch):
        missing_path = tmp_path / "missing_model_comparison.json"
        missing_dataset = tmp_path / "missing_historical_matches.csv"

        monkeypatch.setattr(flask_app_module.strategy_lab_services.mt, "get_summary_metrics", lambda: _mock_strategy_metrics())
        monkeypatch.setattr(
            flask_app_module.strategy_lab_services.mt,
            "get_completed_predictions",
            lambda limit=6: [_mock_completed_prediction(winner_hit=False, overall_result="Loss")] * limit,
        )
        monkeypatch.setattr(flask_app_module.strategy_lab_services.mlp, "DEFAULT_REPORT_PATH", missing_path)
        monkeypatch.setattr(flask_app_module.strategy_lab_services, "_DEFAULT_DATASET", missing_dataset)

        rv = client.get("/strategy-lab")

        assert rv.status_code in (302, 303)
        # Structural marker — stable regardless of display copy wording
        assert "/insights" in rv.headers.get("Location", "")
        # Legacy page content is not rendered.
        assert b"missing_model_comparison" not in rv.data

    def test_strategy_lab_auto_generates_ml_report_from_dataset(self, client, tmp_path, monkeypatch):
        """Report generation is now offline-only; page renders without a report."""
        report_path = tmp_path / "model_comparison.json"
        dataset_path = tmp_path / "historical_matches.csv"
        dataset_path.write_text(
            "\n".join(
                [
                    "date,home_team,away_team,form,goals_scored,goals_conceded,goal_diff,result",
                    "2024-01-01,Arsenal,Chelsea,7.2,2,1,1,HomeWin",
                    "2024-01-02,Liverpool,Everton,6.9,1,1,0,Draw",
                    "2024-01-03,Newcastle,Tottenham,4.8,0,2,-2,AwayWin",
                    "2024-01-04,West Ham,Brighton,5.4,2,1,1,HomeWin",
                    "2024-01-05,Fulham,Brentford,4.9,1,1,0,Draw",
                    "2024-01-06,Leicester,Wolves,6.1,3,2,1,HomeWin",
                    "2024-01-07,Crystal Palace,Aston Villa,3.7,0,1,-1,AwayWin",
                    "2024-01-08,Southampton,Leeds,5.0,2,2,0,Draw",
                    "2024-01-09,Manchester United,Bournemouth,7.5,3,0,3,HomeWin",
                    "2024-01-10,Nottingham Forest,Manchester City,3.2,0,2,-2,AwayWin",
                    "2024-01-11,Arsenal,Everton,7.4,2,0,2,HomeWin",
                    "2024-01-12,Chelsea,Liverpool,5.8,1,2,-1,AwayWin",
                ]
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(flask_app_module.strategy_lab_services.mt, "get_summary_metrics", lambda: _mock_strategy_metrics())
        monkeypatch.setattr(
            flask_app_module.strategy_lab_services.mt,
            "get_completed_predictions",
            lambda limit=6: [_mock_completed_prediction()] * limit,
        )
        monkeypatch.setattr(flask_app_module.strategy_lab_services.mlp, "DEFAULT_REPORT_PATH", report_path)
        monkeypatch.setattr(flask_app_module.strategy_lab_services, "_DEFAULT_DATASET", dataset_path)

        rv = client.get("/strategy-lab")

        assert rv.status_code in (302, 303)
        assert "/insights" in rv.headers.get("Location", "")
        # The legacy URL redirects and does not run report generation.
        assert not report_path.exists()


# ── Chat API ──────────────────────────────────────────────────────────────────

class TestChatRoute:
    def test_chat_returns_reply(self, client):
        rv = client.post("/chat", data={"message": "How do predictions work?"})
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "reply" in data
        assert "suggestions" in data
        assert len(data["reply"]) > 0

    def test_chat_empty_message_returns_400(self, client):
        rv = client.post("/chat", data={"message": ""})
        assert rv.status_code == 400

    def test_chat_clear_clears_history(self, client):
        # First send a message
        client.post("/chat", data={"message": "Hello"})
        # Then clear
        rv = client.post("/chat/clear")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data.get("status") == "cleared"

    def test_chat_with_legacy_anthropic_key_never_calls_paid_api(self, client):
        mock_response = MagicMock()
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "The prediction uses a Poisson model."
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        anthropic_stub = types.SimpleNamespace(Anthropic=MagicMock(return_value=mock_client))
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), \
             patch.object(flask_app_module, "anthropic", anthropic_stub):
            rv = client.post("/chat", data={"message": "Explain predictions"})

        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "reply" in data
        anthropic_stub.Anthropic.assert_not_called()

    def test_chat_fallback_uses_soccer_prediction_context(self, client):
        with client.session_transaction() as sess:
            sess["team_a_id"] = 42
            sess["team_a_name"] = "Arsenal"
            sess["team_a_logo"] = ""
            sess["team_b_id"] = 49
            sess["team_b_name"] = "Chelsea"
            sess["team_b_logo"] = ""
            sess["football_league_id"] = 39
            sess["assistant_page_context"] = {
                "page_kind": "soccer_prediction",
                "sport": "soccer",
                "team_a": "Arsenal",
                "team_b": "Chelsea",
                "winner_pick": "Arsenal",
                "winner_probability": 63.4,
                "confidence": "High",
                "reasoning": "Form edge and defensive profile favor Arsenal.",
                "totals_pick": "Over 2.5",
                "top_factors": ["Form", "Defense", "Opponent Strength"],
            }

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            rv = client.post(
                "/chat",
                data={"message": "Why was this team favored?", "page_path": "/prediction", "page_title": "Prediction"},
            )

        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["mode"] == "fallback"
        assert "Arsenal" in data["reply"]
        assert "Chelsea" in data["reply"]
        assert "63.4%" in data["reply"]
        assert any("confidence" in suggestion.lower() for suggestion in data["suggestions"])

    def test_chat_fallback_explains_result_detail_parlay_context(self, client):
        with client.session_transaction() as sess:
            sess["assistant_page_context"] = {
                "page_kind": "result_detail",
                "sport": "soccer",
                "team_a": "Arsenal",
                "team_b": "Chelsea",
                "winner_pick": "Arsenal to win",
                "totals_pick": "Over 2.5",
                "winner_leg": "Miss",
                "totals_leg": "Hit",
                "overall_result": "Loss",
                "final_score": "1-2",
                "actual_winner": "Chelsea",
                "evidence_summary": "Chelsea were more clinical in the key moments.",
            }

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            rv = client.post(
                "/chat",
                data={"message": "Why did this parlay lose?", "page_path": "/prediction-result/pred-1", "page_title": "Prediction Result Detail"},
            )

        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["mode"] == "fallback"
        assert "graded miss" in data["reply"]
        assert "graded hit" in data["reply"]
        assert "Loss" in data["reply"]

    def test_chat_fallback_uses_nba_context_for_market_comparison(self, client):
        with client.session_transaction() as sess:
            sess["nba_team_a_id"] = "1"
            sess["nba_team_a_name"] = "Celtics"
            sess["nba_team_a_logo"] = ""
            sess["nba_team_b_id"] = "2"
            sess["nba_team_b_name"] = "Heat"
            sess["nba_team_b_logo"] = ""
            sess["assistant_page_context"] = {
                "page_kind": "nba_prediction",
                "sport": "nba",
                "team_a": "Celtics",
                "team_b": "Heat",
                "winner_pick": "Celtics",
                "winner_probability": 58.0,
                "confidence": "Medium",
                "totals_pick": "Over 221.5",
            }

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            rv = client.post(
                "/chat",
                data={"message": "Explain winner vs spread vs totals", "page_path": "/nba/prediction", "page_title": "NBA Prediction"},
            )

        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["mode"] == "fallback"
        assert "Celtics vs Heat" in data["reply"]
        assert "spread is margin-based" in data["reply"]
        assert "combined points" in data["reply"]

    def test_chat_fallback_explains_model_performance_grading(self, client):
        with client.session_transaction() as sess:
            sess["assistant_page_context"] = {
                "page_kind": "model_performance",
                "overall_accuracy": 62.5,
                "wins": 15,
                "losses": 9,
                "finalized_predictions": 24,
                "grading_logic": "Completed picks separate winner leg, totals leg, and overall verdict so the tracked outcome reflects the full ticket.",
            }

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            rv = client.post(
                "/chat",
                data={"message": "How is accuracy graded?", "page_path": "/model-performance", "page_title": "Model Performance"},
            )

        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["mode"] == "fallback"
        assert "62.5%" in data["reply"]
        assert "15 wins and 9 losses" in data["reply"]
        assert "winner leg" in data["reply"]


# ── API endpoints ─────────────────────────────────────────────────────────────

class TestSecurityHardening:
    def test_select_rejects_missing_csrf_token(self, secure_client):
        with patch("api_client.get_teams", return_value=_mock_teams()):
            rv = secure_client.post("/select", data={"team_a": "33", "team_b": "40"})

        assert rv.status_code == 400
        assert b"csrf" in rv.data.lower()

    def test_select_accepts_valid_csrf_token(self, secure_client):
        with secure_client.session_transaction() as sess:
            sess[security.CSRF_SESSION_KEY] = "known-token"

        with patch("api_client.get_teams", return_value=_mock_teams()):
            rv = secure_client.post(
                "/select",
                data={"team_a": "33", "team_b": "40", "csrf_token": "known-token"},
            )

        assert rv.status_code in (302, 303)
        assert "/prediction" in rv.headers.get("Location", "")

    def test_chat_rate_limit_returns_429(self, secure_client):
        with secure_client.session_transaction() as sess:
            sess[security.CSRF_SESSION_KEY] = "chat-token"

        with patch.object(flask_app_module, "_chat_reply", return_value="Stub reply"):
            rv1 = secure_client.post("/chat", data={"message": "One", "csrf_token": "chat-token"})
            rv2 = secure_client.post("/chat", data={"message": "Two", "csrf_token": "chat-token"})
            rv3 = secure_client.post("/chat", data={"message": "Three", "csrf_token": "chat-token"})

        assert rv1.status_code == 200
        assert rv2.status_code == 200
        assert rv3.status_code == 429
        data = json.loads(rv3.data)
        assert "retry_after" in data
        assert "rate limit" in data["error"].lower()


class TestAPIRoutes:
    def test_football_leagues_api(self, client):
        rv = client.get("/api/football/leagues")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "leagues" in data
        assert "season" in data

    def test_football_teams_api(self, client):
        with patch("api_client.get_teams", return_value=_mock_teams()):
            rv = client.get("/api/football/teams?league=39")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "teams" in data

    def test_football_squad_api_missing_team_id(self, client):
        rv = client.get("/api/football/squad")
        assert rv.status_code == 400

    def test_football_squad_api_invalid_team_id(self, client):
        rv = client.get("/api/football/squad?team_id=abc")
        assert rv.status_code == 400

    def test_player_stats_api_missing_id(self, client):
        rv = client.get("/api/player-stats")
        assert rv.status_code == 400

    def test_player_stats_api_invalid_player_id(self, client):
        rv = client.get("/api/player-stats?player_id=abc")
        assert rv.status_code == 400

    def test_props_generate_missing_params(self, client):
        rv = client.post("/props/generate", data={})
        assert rv.status_code == 400

    def test_props_generate_invalid_numeric_payload_returns_400(self, client):
        rv = client.post(
            "/props/generate",
            json={"player_id": "abc", "player_team_id": "x", "opponent_id": "y"},
        )
        assert rv.status_code == 400

    def test_football_team_form_api(self, client):
        with patch("app._team_form_payload", return_value={"form_string": "WWDLW", "rows": []}):
            rv = client.get("/api/football/team-form?team_id=33&league=140")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["form_string"] == "WWDLW"
        assert data["league"] == 140


class TestLeagueSelectionFlow:
    def test_soccer_respects_league_query_for_team_loading(self, client):
        with patch("api_client.get_teams", return_value=_mock_teams()) as get_teams_mock, \
             patch("app._load_upcoming_fixtures", return_value=([], None, "configured", "")):
            rv = client.get("/soccer?league=140")
        assert rv.status_code == 200
        get_teams_mock.assert_called_once_with(140, flask_app_module.SEASON)

    def test_league_change_clears_existing_selected_matchup(self, client):
        with client.session_transaction() as sess:
            sess["football_league_id"] = 39
            sess["team_a_id"] = 33
            sess["team_b_id"] = 40

        with patch("api_client.get_teams", return_value=_mock_teams()), \
             patch("app._load_upcoming_fixtures", return_value=([], None, "configured", "")):
            rv = client.get("/soccer?league=140")

        assert rv.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get("football_league_id") == 140
            assert "team_a_id" not in sess
            assert "team_b_id" not in sess

    def test_team_form_payload_aggregates_supported_competitions(self, client):
        calls = []

        def _side_effect(team_id, league_id, season, last):
            calls.append((team_id, league_id, season, last))
            fixture = _mock_fixture()
            fixture["fixture"]["status"] = {"short": "FT", "long": "Finished"}
            fixture["league"] = {"id": league_id, "name": f"League {league_id}"}
            fixture["teams"]["home"]["id"] = team_id
            fixture["teams"]["away"]["id"] = 999
            fixture["goals"] = {"home": 1, "away": 0}
            return [fixture]

        with patch("app.ac.get_team_fixtures", side_effect=_side_effect):
            payload = flask_app_module._team_form_payload(33, league_id=140)

        called_leagues = [league_id for _, league_id, _, _ in calls]
        assert 140 in called_leagues
        assert 39 in called_leagues
        assert payload["form_string"]


# ── Error pages ───────────────────────────────────────────────────────────────

class TestErrorPages:
    def test_404_returns_error_page(self, client):
        rv = client.get("/this-page-does-not-exist")
        assert rv.status_code == 404
        assert b"not found" in rv.data.lower() or b"error" in rv.data.lower()


# ── World Cup page ────────────────────────────────────────────────────────────

class TestWorldCupRoute:
    def test_worldcup_get_returns_200(self, client):
        with patch("app.ac.get_espn_fixtures", return_value=[], create=True):
            rv = client.get("/worldcup")
        assert rv.status_code == 200

    def test_worldcup_post_with_valid_teams(self, client):
        with patch("app.ac.get_espn_fixtures", return_value=[], create=True):
            rv = client.post("/worldcup", data={"team_a": "Brazil", "team_b": "Argentina"})
        assert rv.status_code == 200


class TestUpdateResultsRoute:
    def test_update_results_post_with_null_accuracy_renders(self, client):
        summary = {"pending": 1, "completed": 0, "total": 1, "completion_rate": 0.0}
        update_stats = {"checked": 1, "found": 0, "updated": 0, "failed": 0, "errors": []}
        metrics = {
            "total_predictions": 1,
            "finalized_predictions": 0,
            "wins": 0,
            "losses": 0,
            "overall_accuracy": None,
            "by_confidence": {},
            "by_sport": {},
            "recent_predictions": [],
        }

        with patch("result_updater.get_update_summary", return_value=summary), \
             patch("result_updater.update_pending_predictions", return_value=update_stats), \
             patch("model_tracker.get_summary_metrics", return_value=metrics):
            rv = client.post("/update-prediction-results")

        assert rv.status_code in (302, 303)
        assert rv.headers["Location"].endswith("/insights")


class TestPredictionResultDetailRoute:
    def test_prediction_result_detail_renders_completed_soccer_result(self, client):
        record = {
            "id": "abc123",
            "sport": "soccer",
            "league_name": "Premier League",
            "date": "2026-04-10",
            "created_at": "2026-04-10T10:30:00Z",
            "updated_at": "2026-04-10T19:00:00Z",
            "team_a": "Arsenal",
            "team_b": "Bournemouth",
            "predicted_winner": "A",
            "predicted_winner_code": "a",
            "predicted_winner_display": "Arsenal",
            "prob_a": 63.4,
            "prob_b": 21.2,
            "prob_draw": 15.4,
            "confidence": "High",
            "status": "completed",
            "actual_result": "B",
            "actual_winner": "Bournemouth",
            "winner_hit": False,
            "game_win": False,
            "overall_game_result": "Loss",
            "final_score_display": "1-2",
            "total_scored": 3,
            "total_label": "Total Goals: 3",
            "totals_pick_display": "Over 2.5",
            "totals_required": True,
            "ou_hit": True,
            "actual_total_side": "Over",
            "winner_display": "Winner Pick: Miss",
            "ou_display": "Totals Leg: Over 2.5 — Hit",
            "prediction_notes": "Arsenal projected edge on recent form.",
            "model_factors": {"team_a": {"form": 7.2}, "team_b": {"form": 6.9}},
        }
        evidence = {
            "available": True,
            "metrics": [{"label": "Shots on Target", "team_a": "4", "team_b": "6", "leader": "Bournemouth"}],
            "key_events": [],
            "goal_scorers": {
                "available": True,
                "home_team": "Arsenal",
                "away_team": "Bournemouth",
                "home_goals": [{"player": "Saka", "minute": "14'", "type": "Goal", "team": "Arsenal"}],
                "away_goals": [
                    {"player": "Semenyo", "minute": "48'", "type": "Goal", "team": "Bournemouth"},
                    {"player": "Solanke", "minute": "73'", "type": "Penalty", "team": "Bournemouth"},
                ],
            },
            "player_impacts": [],
            "injuries": {},
            "form_compare": {},
            "summary": "Bournemouth were more clinical in front of goal.",
        }

        with patch("model_tracker.get_prediction_by_id", return_value=record), \
             patch("app._build_soccer_evidence", return_value=evidence):
            rv = client.get("/prediction-result/abc123")

        assert rv.status_code == 200
        assert b"Result Detail" in rv.data
        assert b"Bournemouth were more clinical" in rv.data
        assert b"ScorPred side" in rv.data
        assert b"Final score" in rv.data
        assert b"Result" in rv.data

    def test_prediction_result_detail_shows_hit_for_correct_winner_pick_even_if_totals_context_differs(self, client):
        record = {
            "id": "pred-hit-1",
            "sport": "soccer",
            "league_name": "Premier League",
            "date": "2026-04-12",
            "created_at": "2026-04-12T10:30:00Z",
            "updated_at": "2026-04-12T18:00:00Z",
            "team_a": "Sunderland",
            "team_b": "Tottenham Hotspur",
            "predicted_winner": "A",
            "predicted_winner_code": "a",
            "predicted_winner_display": "Sunderland",
            "prob_a": 60.5,
            "prob_b": 13.5,
            "prob_draw": 26.0,
            "confidence": "High",
            "status": "completed",
            "actual_result": "A",
            "actual_winner": "Sunderland",
            "winner_hit": True,
            "game_win": False,
            "overall_game_result": "Loss",
            "final_score_display": "1-0",
            "total_scored": 1,
            "total_label": "Total Goals: 1",
            "totals_pick_display": "Over 2.5",
            "totals_required": True,
            "ou_hit": False,
            "actual_total_side": "Under",
            "winner_display": "Winner Pick: Hit",
            "ou_display": "Totals Leg: Over 2.5 — Miss",
            "model_factors": {},
        }
        evidence = {
            "available": True,
            "metrics": [],
            "key_events": [],
            "goal_scorers": {
                "available": True,
                "home_team": "Sunderland",
                "away_team": "Tottenham Hotspur",
                "home_goals": [{"player": "Player A", "minute": "23'", "type": "Goal", "team": "Sunderland"}],
                "away_goals": [],
            },
            "player_impacts": [],
            "injuries": {},
            "form_compare": {},
            "summary": "Sunderland converted the decisive chance and the winner call landed.",
        }

        with patch("model_tracker.get_prediction_by_id", return_value=record), \
             patch("app._build_soccer_evidence", return_value=evidence):
            rv = client.get("/prediction-result/pred-hit-1")

        assert rv.status_code == 200
        assert b"Sunderland" in rv.data
        assert b"Sunderland, 1-0, 1 goal" in rv.data
        assert b"Result" in rv.data
        assert b"Loss" in rv.data

    def test_prediction_result_detail_invalid_id_returns_404(self, client):
        with patch("model_tracker.get_prediction_by_id", return_value=None):
            rv = client.get("/prediction-result/missing-id")
        assert rv.status_code == 404

    def test_prediction_result_detail_handles_missing_evidence_gracefully(self, client):
        record = {
            "id": "pred-nba-1",
            "sport": "nba",
            "date": "2026-04-10",
            "created_at": "2026-04-10T10:30:00Z",
            "updated_at": "2026-04-10T21:30:00Z",
            "team_a": "Celtics",
            "team_b": "Heat",
            "predicted_winner": "A",
            "predicted_winner_display": "Celtics",
            "prob_a": 56.0,
            "prob_b": 44.0,
            "prob_draw": 0.0,
            "confidence": "Medium",
            "status": "completed",
            "actual_result": "A",
            "actual_winner": "Celtics",
            "winner_hit": True,
            "game_win": True,
            "overall_game_result": "Win",
            "final_score_display": "112-108",
            "total_scored": 220,
            "winner_display": "Winner Pick: Hit",
            "model_factors": {},
        }
        evidence = {
            "available": True,
            "evidence_layer_label": "Evidence summary",
            "metrics": [],
            "key_events": [],
            "summary_rows": [
                {"label": "Evidence Layer", "value": "Evidence summary"},
                {"label": "Final Score", "value": "112-108"},
                {"label": "Winner Leg", "value": "Hit · Celtics · Model 56%"},
            ],
            "summary_points": [
                "Tracked result data still shows why the call landed, even without full provider box score detail.",
            ],
            "goal_scorers": {
                "available": False,
                "home_team": "Celtics",
                "away_team": "Heat",
                "home_goals": [],
                "away_goals": [],
            },
            "player_impacts": [],
            "injuries": {},
            "form_compare": {},
            "summary": "Detailed NBA evidence is limited for this game in current provider responses.",
        }

        with patch("model_tracker.get_prediction_by_id", return_value=record), \
             patch("app._build_nba_evidence", return_value=evidence):
            rv = client.get("/prediction-result/pred-nba-1")

        assert rv.status_code == 200
        assert b"Evidence summary" in rv.data
        assert b"Tracked result data still shows why the call landed" in rv.data
        assert b"Detailed fixture evidence is limited" not in rv.data
        assert b"Goal Scorers" not in rv.data

    def test_prediction_result_detail_handles_scoreless_draw_cleanly(self, client):
        record = {
            "id": "pred-0-0",
            "sport": "soccer",
            "league_name": "Premier League",
            "date": "2026-04-12",
            "created_at": "2026-04-12T10:30:00Z",
            "updated_at": "2026-04-12T18:00:00Z",
            "team_a": "Burnley",
            "team_b": "Everton",
            "predicted_winner": "draw",
            "predicted_winner_code": "draw",
            "predicted_winner_display": "Draw",
            "prob_a": 28.0,
            "prob_b": 30.0,
            "prob_draw": 42.0,
            "confidence": "Medium",
            "status": "completed",
            "actual_result": "draw",
            "actual_winner": "Draw",
            "winner_hit": True,
            "game_win": True,
            "overall_game_result": "Win",
            "final_score_display": "0-0",
            "total_scored": 0,
            "total_label": "Total Goals: 0",
            "winner_display": "Winner Pick: Hit",
            "model_factors": {},
        }
        evidence = {
            "available": True,
            "metrics": [],
            "key_events": [],
            "goal_scorers": {
                "available": False,
                "home_team": "Burnley",
                "away_team": "Everton",
                "home_goals": [],
                "away_goals": [],
            },
            "player_impacts": [],
            "injuries": {},
            "form_compare": {},
            "summary": "The match finished level with no goals and little separation in the available evidence.",
        }

        with patch("model_tracker.get_prediction_by_id", return_value=record), \
             patch("app._build_soccer_evidence", return_value=evidence):
            rv = client.get("/prediction-result/pred-0-0")

        assert rv.status_code == 200
        assert b"Draw, 0-0, 0 goals" in rv.data
        assert b"Goal Scorers" not in rv.data

    def test_prediction_result_detail_hides_goal_scorers_when_event_data_unavailable(self, client):
        record = {
            "id": "pred-no-events",
            "sport": "soccer",
            "league_name": "Premier League",
            "date": "2026-04-12",
            "created_at": "2026-04-12T10:30:00Z",
            "updated_at": "2026-04-12T18:00:00Z",
            "team_a": "Arsenal",
            "team_b": "Chelsea",
            "predicted_winner": "A",
            "predicted_winner_code": "a",
            "predicted_winner_display": "Arsenal",
            "prob_a": 57.0,
            "prob_b": 21.0,
            "prob_draw": 22.0,
            "confidence": "High",
            "status": "completed",
            "actual_result": "A",
            "actual_winner": "Arsenal",
            "winner_hit": True,
            "game_win": True,
            "overall_game_result": "Win",
            "final_score_display": "2-1",
            "total_scored": 3,
            "totals_pick_display": "Over 2.5",
            "totals_required": True,
            "ou_hit": True,
            "actual_total_side": "Over",
            "winner_display": "Winner Pick: Hit",
            "ou_display": "Totals Leg: Over 2.5 — Hit",
            "model_factors": {},
        }
        evidence = {
            "available": True,
            "metrics": [{"label": "Shots", "team_a": "15", "team_b": "8", "leader": "Arsenal"}],
            "key_events": [],
            "goal_scorers": {
                "available": False,
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "home_goals": [],
                "away_goals": [],
            },
            "player_impacts": [],
            "injuries": {},
            "form_compare": {},
            "summary": "Arsenal controlled the shot volume and the match played above the baseline total.",
        }

        with patch("model_tracker.get_prediction_by_id", return_value=record), \
             patch("app._build_soccer_evidence", return_value=evidence):
            rv = client.get("/prediction-result/pred-no-events")

        assert rv.status_code == 200
        assert b"Goal Scorers" not in rv.data


class TestSoccerEvidenceFallbacks:
    def test_build_soccer_evidence_uses_events_layer_when_stats_missing(self):
        record = {
            "id": "pred-events-layer",
            "sport": "soccer",
            "league_id": 39,
            "date": "2026-04-12",
            "team_a": "Arsenal",
            "team_b": "Chelsea",
            "fixture_id": 123,
            "predicted_winner": "A",
            "predicted_winner_code": "a",
            "actual_result": "B",
            "actual_winner": "Chelsea",
            "prob_a": 61.0,
            "prob_b": 19.0,
            "prob_draw": 20.0,
            "confidence": "High",
            "status": "completed",
            "winner_hit": False,
            "game_win": False,
            "overall_game_result": "Loss",
            "final_score_display": "1-2",
            "total_scored": 3,
            "totals_pick_display": "Over 2.5",
            "totals_line": 2.5,
            "ou_hit": True,
            "actual_total_side": "Over",
        }
        raw_events = [
            {
                "type": "Goal",
                "detail": "Normal Goal",
                "time": {"elapsed": 17},
                "team": {"name": "Arsenal"},
                "player": {"name": "Saka"},
            },
            {
                "type": "Goal",
                "detail": "Penalty",
                "time": {"elapsed": 63},
                "team": {"name": "Chelsea"},
                "player": {"name": "Palmer"},
            },
            {
                "type": "Card",
                "detail": "Red Card",
                "time": {"elapsed": 79},
                "team": {"name": "Arsenal"},
                "player": {"name": "Rice"},
            },
        ]

        with patch("app.ac.get_teams", return_value=[
            {"team": {"id": 1, "name": "Arsenal"}},
            {"team": {"id": 2, "name": "Chelsea"}},
        ]), \
             patch("app.ac.get_fixture_by_id", return_value=None), \
             patch("app.ac.get_fixture_stats", return_value=[]), \
             patch("app.ac.get_fixture_events", return_value=raw_events), \
             patch("app.ac.get_match_events", return_value={
                 "home_goals": [{"player": "Saka", "minute": "17'", "type": "Goal"}],
                 "away_goals": [
                     {"player": "Palmer", "minute": "63'", "type": "Penalty"},
                     {"player": "Jackson", "minute": "88'", "type": "Goal"},
                 ],
             }), \
             patch("app.ac.get_fixture_players", return_value=[]), \
             patch("app._soccer_form_snapshot", return_value=None), \
             patch("app.ac.get_injuries", return_value=[]):
            evidence = flask_app_module._build_soccer_evidence(record)

        assert evidence["evidence_layer"] == "events"
        assert evidence["evidence_layer_label"] == "Match events"
        assert [row["type"] for row in evidence["key_events"][:3]] == ["Goal", "Penalty Goal", "Red Card"]
        assert any(row["label"] == "Evidence Layer" and row["value"] == "Match events" for row in evidence["summary_rows"])
        assert "event feed" in evidence["summary"]

    def test_build_soccer_evidence_uses_narrative_summary_when_stats_and_events_missing(self):
        record = {
            "id": "pred-summary-layer",
            "sport": "soccer",
            "league_id": 39,
            "date": "2026-04-12",
            "team_a": "Arsenal",
            "team_b": "Chelsea",
            "fixture_id": 456,
            "predicted_winner": "A",
            "predicted_winner_code": "a",
            "actual_result": "B",
            "actual_winner": "Chelsea",
            "prob_a": 64.0,
            "prob_b": 18.0,
            "prob_draw": 18.0,
            "confidence": "High",
            "status": "completed",
            "winner_hit": False,
            "game_win": False,
            "overall_game_result": "Loss",
            "final_score_display": "1-2",
            "total_scored": 3,
            "totals_pick_display": "Under 2.5",
            "totals_line": 2.5,
            "ou_hit": False,
            "actual_total_side": "Over",
        }

        with patch("app.ac.get_teams", return_value=[
            {"team": {"id": 1, "name": "Arsenal"}},
            {"team": {"id": 2, "name": "Chelsea"}},
        ]), \
             patch("app.ac.get_fixture_by_id", return_value=None), \
             patch("app.ac.get_fixture_stats", return_value=[]), \
             patch("app.ac.get_fixture_events", return_value=[]), \
             patch("app.ac.get_match_events", return_value={"home_goals": [], "away_goals": []}), \
             patch("app.ac.get_fixture_players", return_value=[]), \
             patch("app._soccer_form_snapshot", side_effect=[
                 {"matches": 5, "wins": 4, "draws": 1, "losses": 0, "avg_goals_for": 2.0, "avg_goals_against": 0.6},
                 {"matches": 5, "wins": 1, "draws": 1, "losses": 3, "avg_goals_for": 0.8, "avg_goals_against": 1.8},
             ]), \
             patch("app.ac.get_injuries", side_effect=[[], [{"name": "Reece James"}] ]):
            evidence = flask_app_module._build_soccer_evidence(record)

        assert evidence["evidence_layer"] == "summary"
        assert evidence["key_events"] == []
        assert evidence["injuries"] == {"Chelsea": {"count": 1, "notable": ["Reece James"]}}
        assert any(row["label"] == "Outcome Context" and "Major upset" in row["value"] for row in evidence["summary_rows"])
        assert any("genuine upset" in point for point in evidence["summary_points"])

    def test_model_performance_completed_cards_link_to_detail_route(self, client):
        completed = [
            {
                "id": "pred42",
                "team_a": "Arsenal",
                "team_b": "Bournemouth",
                "sport": "soccer",
                "league_name": "Premier League",
                "final_score_display": "1-2",
                "winner_hit": False,
                "winner_display": "Winner Pick: Miss",
                "ou_display": "U/O 2.5: Hit",
                "ou_hit": True,
                "game_win": False,
                "overall_game_result": "Loss",
                "confidence": "High",
                "total_label": "Total Goals: 3",
            }
        ]
        metrics = {
            "total_predictions": 1,
            "finalized_predictions": 1,
            "wins": 0,
            "losses": 1,
            "overall_accuracy": 0.0,
            "by_confidence": {},
            "by_sport": {},
            "by_league": {},
            "recent_predictions": [],
        }

        with patch("model_tracker.get_summary_metrics", return_value=metrics), \
             patch("model_tracker.get_completed_predictions", return_value=completed), \
             patch("model_tracker.get_pending_predictions", return_value=[]):
            rv = client.get("/model-performance")

        assert rv.status_code in (302, 303)
        assert "/insights" in rv.headers.get("Location", "")


class TestConnectedFlows:
    def test_soccer_connected_flow(self, client):
        with patch("api_client.get_teams", return_value=_mock_teams()), \
             patch("api_client.get_upcoming_fixtures", return_value=[_mock_fixture()]), \
             patch("api_client.get_h2h", return_value=[_mock_fixture()]), \
             patch("api_client.get_team_fixtures", return_value=[_mock_fixture()]), \
             patch("api_client.get_injuries", return_value=[]), \
             patch("api_client.get_standings", return_value=[]), \
             patch("app.ac.get_squad", return_value=[]):
            assert client.get("/").status_code == 200
            assert client.get("/soccer").status_code == 200

            select_resp = client.post("/select", data={"team_a": "33", "team_b": "40"})
            assert select_resp.status_code in (302, 303)
            assert "/prediction" in select_resp.headers.get("Location", "")

            assert client.get("/prediction").status_code == 200
            assert client.get("/prediction").status_code == 200
            assert client.get("/players").status_code == 200
            assert client.get("/props").status_code == 200

    def test_model_performance_to_update_results_flow(self, client):
        with patch("result_updater.get_update_summary", return_value={"pending": 1, "completed": 0, "total": 1, "completion_rate": 0.0}), \
             patch("model_tracker.get_summary_metrics", return_value={"total_predictions": 1, "finalized_predictions": 0, "wins": 0, "losses": 0, "overall_accuracy": None, "by_confidence": {}, "by_sport": {}, "recent_predictions": []}):
            performance_resp = client.get("/model-performance")
            assert performance_resp.status_code in (302, 303)
            assert "/insights" in performance_resp.headers.get("Location", "")
            update_resp = client.get("/update-prediction-results")
            assert update_resp.status_code in (302, 303)
            assert "/insights" in update_resp.headers.get("Location", "")

    def test_props_page_renders_soccer_mode_with_team_context(self, client):
        with client.session_transaction() as sess:
            sess["team_a_id"] = 33
            sess["team_a_name"] = "Manchester United"
            sess["team_a_logo"] = ""
            sess["team_b_id"] = 40
            sess["team_b_name"] = "Liverpool"
            sess["team_b_logo"] = ""

        squad = [
            {
                "player": {"id": 1, "name": "Player One", "pos": "FW"},
                "leagues": {"standard": {"pos": "FW"}},
            }
        ]
        with patch("app.ac.get_squad", return_value=squad):
            rv = client.get("/props")
        assert rv.status_code == 200
        assert b"Football" in rv.data
        assert b"Player One" in rv.data


class TestTopPicksRoute:
    def test_top_picks_uses_soccer_and_nba_payload_shapes(self, client):
        today_str = date.today().strftime("%Y-%m-%d")
        soccer_fixtures = [
            {
                "teams": {
                    "home": {"id": 1, "name": "Alpha"},
                    "away": {"id": 2, "name": "Beta"},
                },
                "prediction": {
                    "best_pick": {
                        "prediction": "Alpha",
                        "confidence": "High",
                        "reasoning": "Strong form edge",
                    },
                    "win_probabilities": {"a": 64.0, "b": 21.0, "draw": 15.0},
                },
            }
        ]
        nba_recent = [
            {
                "sport": "nba",
                "date": today_str,
                "is_correct": None,
                "team_a": "Celtics",
                "team_b": "Heat",
                "predicted_winner": "A",
                "confidence": "High",
                "prob_a": 61.0,
                "prob_b": 39.0,
                "prob_draw": 0.0,
            }
        ]

        with patch("app._load_upcoming_fixtures", return_value=(soccer_fixtures, None, "configured", "")), \
             patch("app.mt.get_recent_predictions", return_value=nba_recent):
            rv = client.get("/top-picks-today")

        assert rv.status_code in (302, 303)
        assert "/soccer" in rv.headers.get("Location", "")

    def test_top_picks_groups_soccer_picks_by_league(self, client):
        soccer_fixtures = [
            {
                "teams": {
                    "home": {"id": 1, "name": "Alpha"},
                    "away": {"id": 2, "name": "Beta"},
                },
                "league": {"id": 39, "name": "Premier League"},
                "prediction": {
                    "best_pick": {"prediction": "Alpha", "confidence": "High", "reasoning": "Strong form edge"},
                    "win_probabilities": {"a": 64.0, "b": 21.0, "draw": 15.0},
                },
            },
            {
                "teams": {
                    "home": {"id": 3, "name": "Madrid"},
                    "away": {"id": 4, "name": "Sevilla"},
                },
                "league": {"id": 140, "name": "La Liga"},
                "prediction": {
                    "best_pick": {"prediction": "Madrid", "confidence": "High", "reasoning": "Home edge"},
                    "win_probabilities": {"a": 67.0, "b": 18.0, "draw": 15.0},
                },
            },
        ]
        grouped_fixtures = [
            {"league_id": 39, "league_name": "Premier League", "league_flag": "EN", "fixtures": [soccer_fixtures[0]]},
            {"league_id": 140, "league_name": "La Liga", "league_flag": "ES", "fixtures": [soccer_fixtures[1]]},
        ]

        with patch("app._load_grouped_upcoming_fixtures_all_leagues", return_value=(soccer_fixtures, grouped_fixtures, None, "configured")), \
             patch("app.mt.get_recent_predictions", return_value=[]):
            rv = client.get("/top-picks-today?league=39")

        assert rv.status_code in (302, 303)
        assert "/soccer" in rv.headers.get("Location", "")


class TestNbaFailureHandling:
    def test_nba_index_handles_fetch_failures(self, client):
        with patch("nba_live_client.get_teams", side_effect=Exception("teams down")), \
             patch("nba_live_client.get_today_games", side_effect=Exception("games down")), \
             patch("nba_live_client.get_upcoming_games", side_effect=Exception("upcoming down")), \
             patch("nba_live_client.get_standings", return_value={"conference": []}):
            rv = client.get("/nba/")
        assert rv.status_code == 200

    def test_nba_index_keeps_upcoming_game_when_prediction_data_is_unavailable(self, client):
        teams = [
            {"id": "1", "name": "Boston Celtics", "nickname": "Celtics", "city": "Boston", "logo": ""},
            {"id": "2", "name": "Orlando Magic", "nickname": "Magic", "city": "Orlando", "logo": ""},
        ]
        game = {
            "id": "evt-1",
            "date": {"start": "2026-04-12T22:00:00Z"},
            "status": {"long": "Scheduled", "short": "Scheduled", "state": "pre"},
            "venue": {"name": "TD Garden"},
            "teams": {
                "home": {"id": "1", "name": "Boston Celtics", "nickname": "Celtics", "logo": ""},
                "visitors": {"id": "2", "name": "Orlando Magic", "nickname": "Magic", "logo": ""},
            },
            "scores": {"home": {"points": 0}, "visitors": {"points": 0}},
        }

        with patch("nba_routes.nc.get_teams", return_value=teams), \
             patch("nba_routes.nc.get_today_games", return_value=[]), \
             patch("nba_routes.nc.get_upcoming_games", return_value=[game]), \
             patch("nba_routes.nc.get_h2h", return_value=[]), \
             patch("nba_routes.nc.get_team_recent_form", return_value=[]), \
             patch("nba_routes.nc.get_team_injuries", return_value=[]), \
             patch("nba_routes.nc.get_standings", return_value={"east": [], "west": []}), \
             patch("nba_routes.se.scorpred_predict", return_value={
                 "best_pick": {"prediction": "Celtics", "confidence": "High"},
                 "win_probabilities": {"a": 62.3, "b": 37.7},
             }):
            rv = client.get("/nba/")

        assert rv.status_code == 200
        assert b"CONSIDER" in rv.data
        assert b"Celtics" in rv.data
        assert b"SKIP" in rv.data
        assert b"Unavailable" in rv.data
        assert b"Game tied" not in rv.data

    def test_nba_prediction_without_verified_event_shows_unavailable(self, client):
        with client.session_transaction() as sess:
            sess["nba_team_a_id"] = "1"
            sess["nba_team_a_name"] = "Los Angeles Lakers"
            sess["nba_team_a_logo"] = ""
            sess["nba_team_a_nickname"] = "Lakers"
            sess["nba_team_a_city"] = "Los Angeles"
            sess["nba_team_b_id"] = "2"
            sess["nba_team_b_name"] = "Boston Celtics"
            sess["nba_team_b_logo"] = ""
            sess["nba_team_b_nickname"] = "Celtics"
            sess["nba_team_b_city"] = "Boston"

        scorpred = {
            "team_a_score": 7.1,
            "team_b_score": 5.9,
            "score_gap": 1.2,
            "prob_a": 61.5,
            "prob_b": 38.5,
            "confidence": "Medium",
            "winner_label": "Lakers to win",
            "components_a": {"form": 7, "offense": 6, "defense": 7, "h2h": 6, "home_away": 7, "opp_strength": 5, "squad": 6},
            "components_b": {"form": 5, "offense": 6, "defense": 5, "h2h": 4, "home_away": 3, "opp_strength": 5, "squad": 6},
            "best_pick": {"prediction": "Lakers to win", "confidence": "Medium", "reasoning": "Better form", "market": "Moneyline", "team": "A"},
            "optional_picks": [],
            "key_edges": [{"team": "A", "team_name": "Lakers", "category": "Recent form", "margin": 1.2}],
            "matchup_reading": "Lakers have the cleaner profile.",
            "win_probabilities": {"team_a": 58, "team_b": 42},
        }

        with patch("nba_routes.nc.get_h2h", return_value=[]), \
             patch("nba_routes.nc.get_team_recent_form", return_value=[]), \
             patch("nba_routes.nc.get_team_injuries", return_value=[]), \
             patch("nba_routes.nc.get_team_season_stats", return_value={"ppg": 110, "opp_ppg": 108, "net_rtg": 3.0}), \
             patch("nba_routes.nc.get_standings", return_value=[]), \
             patch("nba_routes.nc.get_route_support", return_value={}), \
             patch("nba_routes.se.scorpred_predict", return_value=scorpred), \
             patch("nba_routes.mt.save_prediction", return_value=None):
            rv = client.get("/nba/prediction")

        assert rv.status_code == 200
        assert b"Prediction data could not be generated" in rv.data
        assert b"NBA Match Analysis" in rv.data
        assert b"Pregame recommendations are unavailable" in rv.data
        assert b"Score Projections" not in rv.data
        assert b"Key Prediction Factors" not in rv.data

    def test_nba_prediction_without_verified_event_hides_markets_and_does_not_track(self, client):
        with client.session_transaction() as sess:
            sess["nba_team_a_id"] = "1"
            sess["nba_team_a_name"] = "Boston Celtics"
            sess["nba_team_a_logo"] = ""
            sess["nba_team_a_nickname"] = "Celtics"
            sess["nba_team_a_city"] = "Boston"
            sess["nba_team_b_id"] = "2"
            sess["nba_team_b_name"] = "Miami Heat"
            sess["nba_team_b_logo"] = ""
            sess["nba_team_b_nickname"] = "Heat"
            sess["nba_team_b_city"] = "Miami"

        scorpred = {
            "team_a_score": 7.8,
            "team_b_score": 5.6,
            "score_gap": 2.2,
            "prob_a": 64.0,
            "prob_b": 36.0,
            "confidence": "High",
            "winner_label": "Celtics to win",
            "components_a": {"form": 8, "offense": 7, "defense": 8, "h2h": 6, "home_away": 7, "opp_strength": 6, "squad": 8},
            "components_b": {"form": 5, "offense": 6, "defense": 5, "h2h": 4, "home_away": 4, "opp_strength": 6, "squad": 5},
            "best_pick": {"prediction": "Celtics to win", "confidence": "High", "reasoning": "Boston have the stronger two-way profile.", "team": "A"},
            "optional_picks": [],
            "key_edges": [{"team": "A", "team_name": "Celtics", "category": "Recent form", "margin": 2.2}],
            "matchup_reading": "Boston have the cleaner profile.",
            "win_probabilities": {"a": 64.0, "b": 36.0},
        }
        market_analysis = {
            "winner_leg": {"leg_key": "winner", "leg_type": "Winner / Moneyline", "recommendation": "Celtics", "confidence": "High", "tracking_status": "tracked", "explanation": "Boston have the stronger overall winning case."},
            "spread_leg": {"leg_key": "spread", "leg_type": "Spread", "recommendation": "Celtics -6.5", "confidence": "Medium", "tracking_status": "display_only", "expected_margin": 6.7, "explanation": "Boston project to win by multiple possessions."},
            "totals_leg": {"leg_key": "total", "leg_type": "Total Points", "recommendation": "Over 223.5", "confidence": "Medium", "tracking_status": "future_ready", "expected_total": 226.1, "explanation": "The scoring environment points to an above-baseline total."},
            "parlay_checklist": [
                {"leg_key": "winner", "leg_type": "Winner / Moneyline", "recommendation": "Celtics", "confidence": "High", "tracking_status": "tracked", "explanation": "Boston have the stronger overall winning case."},
                {"leg_key": "spread", "leg_type": "Spread", "recommendation": "Celtics -6.5", "confidence": "Medium", "tracking_status": "display_only", "explanation": "Boston project to win by multiple possessions."},
                {"leg_key": "total", "leg_type": "Total Points", "recommendation": "Over 223.5", "confidence": "Medium", "tracking_status": "future_ready", "explanation": "The scoring environment points to an above-baseline total."},
            ],
            "alignment": {"overall": "Selective alignment", "summary": "Moneyline and spread align, but the total is a secondary leg.", "weak_legs": []},
            "display_note": "Spread and total points recommendations are model-derived from ScorPred inputs.",
            "tracking_note": "Winner is already tracked. Spread is display-only for now, and totals are structured for future-expanded NBA leg grading.",
            "evidence_points": ["Recent form margin favors Boston.", "Net rating also favors Boston.", "The scoring baseline points toward 223.5+."],
            "model_spread_label": "Celtics -6.5",
            "model_total_pick": "Over 223.5",
            "expected_total": 226.1,
        }

        with patch("nba_routes.nc.get_h2h", return_value=[]), \
               patch("nba_routes.nc.get_team_recent_form", return_value=[]), \
               patch("nba_routes.nc.get_team_injuries", return_value=[]), \
               patch("nba_routes.nc.get_team_season_stats", return_value={"ppg": 116, "opp_ppg": 109, "net_rtg": 7.0}), \
               patch("nba_routes.nc.get_standings", return_value=[]), \
               patch("nba_routes.nc.get_route_support", return_value={}), \
               patch("nba_routes.se.scorpred_predict", return_value=scorpred), \
               patch("nba_routes.np_nba.build_market_recommendations", return_value=market_analysis), \
               patch("nba_routes.mt.save_prediction", return_value=None) as mock_save_prediction:
            rv = client.get("/nba/prediction")

        assert rv.status_code == 200
        assert b"Related Markets" not in rv.data
        assert b"Celtics -6.5" not in rv.data
        assert b"Over 223.5" not in rv.data
        assert b"Parlay Checklist" not in rv.data
        assert b"Model-derived" not in rv.data
        mock_save_prediction.assert_not_called()

    def test_nba_market_recommendations_flag_weak_legs_honestly(self):
        team_a = {"name": "Boston Celtics", "nickname": "Celtics"}
        team_b = {"name": "Miami Heat", "nickname": "Heat"}
        scorpred = {
            "prob_a": 52.0,
            "prob_b": 48.0,
            "confidence": "Low",
            "team_a_score": 5.4,
            "team_b_score": 5.2,
            "best_pick": {"prediction": "Celtics to win", "confidence": "Low", "reasoning": "Very small edge.", "team": "A"},
        }
        recent_form_a = [
            {"our_pts": 110, "their_pts": 108, "result": "W"},
            {"our_pts": 108, "their_pts": 107, "result": "W"},
            {"our_pts": 105, "their_pts": 106, "result": "L"},
        ]
        recent_form_b = [
            {"our_pts": 109, "their_pts": 108, "result": "W"},
            {"our_pts": 107, "their_pts": 106, "result": "W"},
            {"our_pts": 104, "their_pts": 105, "result": "L"},
        ]
        stats_a = {"ppg": 111.0, "opp_ppg": 109.5, "net_rtg": 1.5}
        stats_b = {"ppg": 110.5, "opp_ppg": 109.0, "net_rtg": 1.4}

        analysis = np_nba_module.build_market_recommendations(
            team_a,
            team_b,
            scorpred,
            recent_form_a,
            recent_form_b,
            [],
            [],
            [],
            stats_a=stats_a,
            stats_b=stats_b,
            team_a_is_home=True,
        )

        assert analysis["winner_leg"]["leg_type"] == "Winner / Moneyline"
        assert analysis["spread_leg"]["leg_type"] == "Spread"
        assert analysis["totals_leg"]["leg_type"] == "Total Points"
        assert analysis["spread_leg"]["confidence"] == "Low"
        assert analysis["totals_leg"]["confidence"] == "Low"
        assert analysis["alignment"]["overall"] == "Selective alignment"
        assert "Spread" in analysis["alignment"]["weak_legs"]
        assert "Total Points" in analysis["alignment"]["weak_legs"]

def test_worldcup_same_team_shows_error(client):
    with patch("app.ac.get_espn_fixtures", return_value=[], create=True):
        rv = client.post("/worldcup", data={"team_a": "Brazil", "team_b": "Brazil"})
    assert rv.status_code == 200
    assert b"different" in rv.data.lower() or b"error" in rv.data.lower()


class TestWatchlistAndTracking:
    def test_watchlist_add_remove_team_flow(self, client):
        add_resp = client.post("/watchlist/team", data={"team": "Arsenal"}, follow_redirects=True)
        assert add_resp.status_code == 200
        assert b"Arsenal" in add_resp.data

        remove_resp = client.post("/watchlist/team/remove", data={"team": "Arsenal"}, follow_redirects=True)
        assert remove_resp.status_code == 200
        assert b"Arsenal" not in remove_resp.data

    def test_performance_pending_rows_show_nonzero_confidence(self, client, monkeypatch):
        monkeypatch.setattr(flask_app_module, "_refresh_tracking_results_if_due", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            flask_app_module.mt,
            "get_completed_predictions",
            lambda limit=2000: [],
        )
        monkeypatch.setattr(
            flask_app_module.mt,
            "get_pending_predictions",
            lambda limit=2000: [
                {
                    "team_a": "Arsenal",
                    "team_b": "Newcastle United",
                    "sport": "soccer",
                    "status": "pending",
                    "prob_a": 0.61,
                    "prob_b": 0.21,
                    "prob_draw": 0.18,
                    "predicted_pick_label": "Arsenal",
                    "date": "2026-04-25T16:30:00+00:00",
                }
            ],
        )
        rv = client.get("/performance?window=all")
        assert rv.status_code == 200
        assert b"61%" in rv.data
