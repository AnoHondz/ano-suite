"""
Regression tests for NBA predictor helpers against live-client game shapes.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import nba_predictor as np_nba


def _make_game(
    home_id: str,
    away_id: str,
    home_pts,
    away_pts,
    *,
    date: str = "2026-04-01",
    long_status: str = "Final",
    state: str = "post",
) -> dict:
    return {
        "id": f"{home_id}-{away_id}-{date}",
        "date": {"start": f"{date}T19:00:00Z"},
        "status": {
            "long": long_status,
            "short": long_status,
            "detail": long_status,
            "state": state,
        },
        "teams": {
            "home": {"id": str(home_id), "name": f"Team{home_id}", "logo": ""},
            "visitors": {"id": str(away_id), "name": f"Team{away_id}", "logo": ""},
        },
        "scores": {
            "home": {"points": home_pts, "linescore": [25, 25, 25, 25]},
            "visitors": {"points": away_pts, "linescore": [24, 24, 24, 24]},
        },
    }


class TestNbaPredictorRegressions:
    def test_filter_completed_nba_games_accepts_final_post_shape(self):
        games = [
            _make_game("1", "2", 110, 101, long_status="Final", state="post"),
            _make_game("1", "2", None, None, long_status="Scheduled", state="pre"),
        ]

        result = np_nba.filter_completed_nba_games(games)

        assert len(result) == 1
        assert result[0]["status"]["long"] == "Final"

    def test_filter_completed_nba_games_sorts_and_limits(self):
        games = [
            _make_game("1", "2", 101, 99, date="2026-04-01"),
            _make_game("1", "2", 102, 98, date="2026-04-03"),
            _make_game("1", "2", 103, 97, date="2026-04-02"),
        ]

        result = np_nba.filter_completed_nba_games(games, limit=2)

        assert [game["date"]["start"][:10] for game in result] == ["2026-04-03", "2026-04-02"]

    def test_extract_recent_form_uses_final_post_games(self):
        games = [
            _make_game("1", "2", 110, 101, date="2026-04-01"),
            _make_game("3", "1", 108, 99, date="2026-04-03"),
        ]

        result = np_nba.extract_recent_form(games, team_id="1", n=5)

        assert len(result) == 2
        assert result[0]["date"] == "2026-04-03"
        assert result[0]["result"] == "L"

    def test_build_h2h_summary_counts_final_post_games(self):
        games = [
            _make_game("1", "2", 110, 101, date="2026-04-01"),
            _make_game("2", "1", 99, 105, date="2026-04-02"),
            _make_game("2", "1", 106, 100, date="2026-04-03"),
        ]

        result = np_nba.build_h2h_summary(games, team_a_id="1", team_b_id="2", n=5)

        assert result["wins_a"] == 2
        assert result["wins_b"] == 1
        assert result["total"] == 3

    def test_extract_form_for_display_filters_non_completed_games(self):
        games = [
            _make_game("1", "2", 110, 101, long_status="Final", state="post"),
            _make_game("1", "3", None, None, long_status="Scheduled", state="pre"),
        ]

        result = np_nba.extract_form_for_display(games, team_id="1")

        assert len(result) == 1
        assert result[0]["opponent"] == "Team2"
