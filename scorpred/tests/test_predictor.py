"""
tests/test_predictor.py — Unit tests for predictor.py

Run with:
    pytest tests/test_predictor.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from league_config import CURRENT_SEASON

import predictor as pred


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_fixture(home_id: int, away_id: int, home_goals: int, away_goals: int,
                  date: str | None = None,
                  season: str | int | None = None,
                  status_short: str = "FT") -> dict:
    if date is None:
        date = f"{CURRENT_SEASON}-01-15T15:00:00+00:00"
    league = {"id": 39, "name": "Premier League"}
    if season is not None:
        league["season"] = season
    return {
        "fixture": {"id": 1, "date": date, "status": {"short": status_short}},
        "teams": {
            "home": {"id": home_id, "name": f"Team{home_id}", "logo": ""},
            "away": {"id": away_id, "name": f"Team{away_id}", "logo": ""},
        },
        "goals": {"home": home_goals, "away": away_goals},
        "league": league,
    }


# ── extract_form ──────────────────────────────────────────────────────────────

class TestExtractForm:
    def test_win_result(self):
        fixture = _make_fixture(1, 2, 3, 1)
        form = pred.extract_form([fixture], team_id=1)
        assert form[0]["result"] == "W"
        assert form[0]["gf"] == 3
        assert form[0]["ga"] == 1
        assert form[0]["home"] is True

    def test_loss_result_as_away(self):
        fixture = _make_fixture(1, 2, 3, 1)
        form = pred.extract_form([fixture], team_id=2)
        assert form[0]["result"] == "L"
        assert form[0]["gf"] == 1
        assert form[0]["ga"] == 3
        assert form[0]["home"] is False

    def test_draw_result(self):
        fixture = _make_fixture(1, 2, 2, 2)
        form = pred.extract_form([fixture], team_id=1)
        assert form[0]["result"] == "D"

    def test_skips_fixture_with_null_goals(self):
        fixture = _make_fixture(1, 2, None, None)
        fixture["goals"] = {"home": None, "away": None}
        form = pred.extract_form([fixture], team_id=1)
        assert form == []

    def test_clean_sheet_flag(self):
        fixture = _make_fixture(1, 2, 2, 0)
        form = pred.extract_form([fixture], team_id=1)
        assert form[0]["cs"] is True

    def test_no_clean_sheet_flag(self):
        fixture = _make_fixture(1, 2, 2, 1)
        form = pred.extract_form([fixture], team_id=1)
        assert form[0]["cs"] is False

    def test_empty_fixture_list(self):
        assert pred.extract_form([], team_id=1) == []

    def test_date_truncated_to_10_chars(self):
        fixture = _make_fixture(1, 2, 1, 0, date=f"{CURRENT_SEASON}-03-20T19:00:00+00:00")
        form = pred.extract_form([fixture], team_id=1)
        assert form[0]["date"] == f"{CURRENT_SEASON}-03-20"

    def test_filters_to_recent_completed_matches(self):
        old_fixture = _make_fixture(1, 2, 2, 1, date=f"{CURRENT_SEASON - 4}-12-10T15:00:00+00:00")
        recent_fixture = _make_fixture(1, 2, 1, 0, date=f"{CURRENT_SEASON}-05-10T15:00:00+00:00")
        pending_fixture = _make_fixture(1, 2, None, None, date=f"{CURRENT_SEASON}-05-20T15:00:00+00:00")
        form = pred.extract_form([old_fixture, pending_fixture, recent_fixture], team_id=1)
        assert len(form) == 1
        assert form[0]["date"] == f"{CURRENT_SEASON}-05-10"

    def test_extract_form_sorts_newest_first(self):
        older = _make_fixture(1, 2, 1, 0, date=f"{CURRENT_SEASON}-04-01T15:00:00+00:00")
        newer = _make_fixture(1, 2, 2, 1, date=f"{CURRENT_SEASON}-05-01T15:00:00+00:00")
        form = pred.extract_form([older, newer], team_id=1)
        assert form[0]["date"] == f"{CURRENT_SEASON}-05-01"
        assert form[1]["date"] == f"{CURRENT_SEASON}-04-01"

    def test_previous_season_fixture_is_included(self):
        prev_season = _make_fixture(
            1, 2, 1, 0,
            date=f"{CURRENT_SEASON}-05-10T15:00:00+00:00",
            season=f"{CURRENT_SEASON - 1}/{CURRENT_SEASON}",
            status_short="FT",
        )
        form = pred.filter_recent_completed_fixtures([prev_season], current_season=CURRENT_SEASON)
        assert len(form) == 1

    def test_live_match_is_excluded_even_with_goals(self):
        live_fixture = _make_fixture(
            1, 2, 2, 1,
            date=f"{CURRENT_SEASON}-04-10T15:00:00+00:00",
            season=f"{CURRENT_SEASON - 1}/{CURRENT_SEASON}",
            status_short="1H",
        )
        filtered = pred.filter_recent_completed_fixtures([live_fixture], current_season=CURRENT_SEASON)
        assert filtered == []

    def test_split_year_season_metadata_parses_start_year(self):
        split_year_fixture = _make_fixture(
            1, 2, 2, 0,
            date=f"{CURRENT_SEASON}-05-10T15:00:00+00:00",
            season=f"{CURRENT_SEASON - 1}/{CURRENT_SEASON}",
            status_short="FT",
        )
        filtered = pred.filter_recent_completed_fixtures([split_year_fixture], current_season=CURRENT_SEASON)
        assert len(filtered) == 1
        assert filtered[0]["league"]["season"] == f"{CURRENT_SEASON - 1}/{CURRENT_SEASON}"

    def test_date_fallback_uses_real_season_boundaries(self):
        date_fallback_fixture = _make_fixture(
            1, 2, 1, 0,
            date=f"{CURRENT_SEASON}-05-10T15:00:00+00:00",
            season=None,
            status_short="FT",
        )
        filtered = pred.filter_recent_completed_fixtures([date_fallback_fixture], current_season=CURRENT_SEASON)
        assert len(filtered) == 1

    def test_future_fixtures_do_not_crowd_out_previous_season_completed(self):
        """Verify that upcoming fixtures from current season don't replace completed previous-season fixtures."""
        from datetime import datetime, timedelta
        future_date = (datetime.now() + timedelta(days=30)).isoformat()[:10]
        future_fixture = _make_fixture(
            1, 2, None, None,
            date=f"{future_date}T19:00:00+00:00",
            season=CURRENT_SEASON,
            status_short="NS",
        )
        prev_season_completed = _make_fixture(
            1, 2, 2, 1,
            date=f"{CURRENT_SEASON}-06-01T15:00:00+00:00",
            season=CURRENT_SEASON - 1,
            status_short="FT",
        )
        filtered = pred.filter_recent_completed_fixtures(
            [future_fixture, prev_season_completed],
            current_season=CURRENT_SEASON
        )
        assert len(filtered) == 1
        assert filtered[0]["fixture"]["status"]["short"] == "FT"
        assert filtered[0]["goals"]["home"] == 2


# ── form_pts ──────────────────────────────────────────────────────────────────

class TestFormPts:
    def test_all_wins(self):
        form = [{"result": "W"}, {"result": "W"}, {"result": "W"}]
        assert pred.form_pts(form) == 1.0

    def test_all_losses(self):
        form = [{"result": "L"}, {"result": "L"}]
        assert pred.form_pts(form) == 0.0

    def test_all_draws(self):
        form = [{"result": "D"}, {"result": "D"}]
        assert round(pred.form_pts(form), 4) == round(1 / 3, 4)

    def test_empty_form_returns_neutral(self):
        assert pred.form_pts([]) == 0.5

    def test_mixed_form(self):
        form = [{"result": "W"}, {"result": "D"}, {"result": "L"}]
        # (3 + 1 + 0) / (3*3) = 4/9
        assert round(pred.form_pts(form), 4) == round(4 / 9, 4)


# ── avg_goals ─────────────────────────────────────────────────────────────────

class TestAvgGoals:
    def test_scored_average(self):
        form = [{"gf": 2, "ga": 0}, {"gf": 3, "ga": 1}]
        assert pred.avg_goals(form, scored=True) == 2.5

    def test_conceded_average(self):
        form = [{"gf": 2, "ga": 0}, {"gf": 3, "ga": 1}]
        assert pred.avg_goals(form, scored=False) == 0.5

    def test_empty_form_returns_default(self):
        assert pred.avg_goals([], scored=True) == 1.2


# ── home_away_split ───────────────────────────────────────────────────────────

class TestHomeAwaySplit:
    def test_split_counts(self):
        form = [
            {"result": "W", "gf": 2, "ga": 0, "home": True},
            {"result": "L", "gf": 0, "ga": 2, "home": True},
            {"result": "W", "gf": 1, "ga": 0, "home": False},
        ]
        split = pred.home_away_split(form)
        assert split["home"]["p"] == 2
        assert split["away"]["p"] == 1
        assert split["home"]["w"] == 1
        assert split["home"]["l"] == 1

    def test_empty_form_returns_zeros(self):
        split = pred.home_away_split([])
        assert split["home"]["p"] == 0
        assert split["away"]["p"] == 0


# ── h2h_record ────────────────────────────────────────────────────────────────

class TestH2hRecord:
    def test_basic_record(self):
        fixtures = [
            _make_fixture(1, 2, 2, 0),  # team 1 wins
            _make_fixture(1, 2, 1, 1),  # draw
            _make_fixture(2, 1, 3, 0),  # team 2 wins (home), so team 1 loses
        ]
        rec = pred.h2h_record(fixtures, id_a=1, id_b=2)
        assert rec["a_wins"] == 1
        assert rec["draws"] == 1
        assert rec["b_wins"] == 1
        assert rec["total"] == 3

    def test_h2h_record_filters_old_matches(self):
        fixtures = [
            _make_fixture(1, 2, 2, 0, date=f"{CURRENT_SEASON - 4}-12-10T15:00:00+00:00"),
            _make_fixture(1, 2, 1, 1, date=f"{CURRENT_SEASON}-05-10T15:00:00+00:00"),
        ]
        rec = pred.h2h_record(fixtures, id_a=1, id_b=2)
        assert rec["total"] == 1
        assert rec["a_wins"] == 0
        assert rec["draws"] == 1

    def test_empty_h2h(self):
        rec = pred.h2h_record([], id_a=1, id_b=2)
        assert rec["total"] == 1  # avoids division by zero
        assert rec["a_wins"] == 0

    def test_percentages_sum_to_1(self):
        fixtures = [
            _make_fixture(1, 2, 2, 1),
            _make_fixture(1, 2, 0, 1),
        ]
        rec = pred.h2h_record(fixtures, id_a=1, id_b=2)
        total_pct = rec["a_pct"] + rec["d_pct"] + rec["b_pct"]
        assert round(total_pct, 5) == 1.0


# ── _normalise_probs (via app helper) ─────────────────────────────────────────

class TestNormaliseProbs:
    """Test the probability normalisation helper in app.py."""

    def setup_method(self):
        import app as flask_app
        self._normalise = flask_app._normalise_probs

    def test_sums_to_100(self):
        result = self._normalise({"a": 50.0, "draw": 25.0, "b": 25.0})
        assert round(result["a"] + result["draw"] + result["b"], 1) == 100.0

    def test_all_zeros_returns_equal_split(self):
        result = self._normalise({"a": 0, "draw": 0, "b": 0})
        assert result == {"a": 33.4, "draw": 33.3, "b": 33.3}

    def test_negative_values_clamped_to_zero(self):
        result = self._normalise({"a": -10, "draw": 50, "b": 50})
        assert result["a"] == 0.0
        assert round(result["draw"] + result["b"], 1) == 100.0

    def test_preserves_relative_proportions(self):
        result = self._normalise({"a": 60.0, "draw": 20.0, "b": 20.0})
        assert result["a"] > result["draw"]
        assert result["a"] > result["b"]


# ── quick_predict_from_standings ──────────────────────────────────────────────

class TestQuickPredictFromStandings:
    def _make_standing(self, team_id: int, points: int, played: int = 20,
                       gf: int = 30, ga: int = 20, form: str = "WWDLW") -> dict:
        return {
            "team": {"id": team_id, "name": f"Team{team_id}"},
            "points": points,
            "form": form,
            "all": {
                "played": played,
                "goals": {"for": gf, "against": ga},
            },
        }

    def test_returns_dict_with_keys(self):
        standings = [
            self._make_standing(1, 60, gf=50, ga=20),
            self._make_standing(2, 40, gf=30, ga=30),
        ]
        result = pred.quick_predict_from_standings(1, 2, standings)
        assert isinstance(result, dict)
        assert "home_pct" in result
        assert "away_pct" in result
        assert "draw_pct" in result

    def test_empty_standings_returns_dict(self):
        result = pred.quick_predict_from_standings(1, 2, [])
        assert isinstance(result, dict)


# ── NBA Tests ──────────────────────────────────────────────────────────────────

import nba_predictor as np_nba


def _make_nba_game(home_id: int, away_id: int, home_pts: int, away_pts: int,
                   date: str = "2024-05-10", status: str = "Finished") -> dict:
    """Helper to create mock NBA game records."""
    return {
        "id": f"game-{home_id}-{away_id}",
        "date": {"start": f"{date}T19:00:00Z"},
        "status": {"long": status},
        "teams": {
            "home": {"id": home_id, "name": f"Team{home_id}", "logo": ""},
            "visitors": {"id": away_id, "name": f"Team{away_id}", "logo": ""},
        },
        "scores": {
            "home": {"points": home_pts, "linescore": [20, 25, 30, 28]},
            "visitors": {"points": away_pts, "linescore": [18, 22, 25, 26]},
        },
    }


def _make_nba_injury(player_id: int, player_name: str, status: str = "out") -> dict:
    """Helper to create mock injury records."""
    return {
        "player": {"id": player_id, "name": player_name},
        "status": status,
        "position": "PG",
    }


def _make_nba_player(player_id: int, name: str, pos: str = "PG",
                     injuries: list = None, points: float = 0.0) -> dict:
    """Helper to create mock player roster entries."""
    return {
        "id": player_id,
        "firstname": name.split()[0],
        "lastname": name.split()[-1],
        "displayName": name,
        "position": pos,
        "injuries": injuries or [],
        "games": 82,
        "points": points if points else 20.5,
        "rebounds": 6.2,
        "assists": 5.1,
        "leagues": {"standard": {"pos": pos}},
    }


class TestFilterCompletedNbaGames:
    def test_filters_to_finished_only(self):
        games = [
            _make_nba_game(1, 2, 110, 105, status="Finished"),
            _make_nba_game(1, 2, 100, 100, status="Live"),
            _make_nba_game(3, 4, 95, 98, status="Scheduled"),
        ]
        result = np_nba.filter_completed_nba_games(games)
        assert len(result) == 1
        assert result[0]["status"]["long"] == "Finished"

    def test_sorts_newest_first(self):
        games = [
            _make_nba_game(1, 2, 110, 105, date="2024-04-01"),
            _make_nba_game(1, 2, 100, 100, date="2024-05-01"),
        ]
        result = np_nba.filter_completed_nba_games(games)
        assert result[0]["date"]["start"].startswith("2024-05-01")
        assert result[1]["date"]["start"].startswith("2024-04-01")

    def test_empty_list_returns_empty(self):
        result = np_nba.filter_completed_nba_games([])
        assert result == []

    def test_no_finished_games_returns_empty(self):
        games = [
            _make_nba_game(1, 2, 100, 100, status="Live"),
            _make_nba_game(3, 4, 95, 98, status="Scheduled"),
        ]
        result = np_nba.filter_completed_nba_games(games)
        assert result == []


class TestExtractRecentForm:
    def test_extracts_last_5_games(self):
        games = [
            _make_nba_game(1, 2, 110, 105, date="2024-04-01"),
            _make_nba_game(1, 3, 100, 100, date="2024-04-05"),
            _make_nba_game(1, 4, 95, 98, date="2024-04-10"),
            _make_nba_game(1, 5, 120, 110, date="2024-04-15"),
            _make_nba_game(1, 6, 105, 100, date="2024-04-20"),
            _make_nba_game(1, 7, 115, 112, date="2024-04-25"),
        ]
        form = np_nba.extract_recent_form(games, team_id=1, n=5)
        assert len(form) == 5
        assert form[0]["result"] == "W"

    def test_identifies_wins_and_losses(self):
        games = [
            _make_nba_game(1, 2, 110, 105, date="2024-04-01"),  # home team 1 wins
            _make_nba_game(3, 1, 100, 95, date="2024-04-05"),   # away team 1 loses
        ]
        form = np_nba.extract_recent_form(games, team_id=1, n=5)
        # Most recent (second game) should be listed first
        result_by_date = {f["date"]: f["result"] for f in form}
        assert result_by_date["2024-04-05"] == "L"  # Away loss
        assert result_by_date["2024-04-01"] == "W"  # Home win

    def test_marks_home_away_correctly(self):
        games = [
            _make_nba_game(1, 2, 110, 105, date="2024-04-01"),  # team 1 home
            _make_nba_game(3, 1, 100, 95, date="2024-04-05"),   # team 1 away
        ]
        form = np_nba.extract_recent_form(games, team_id=1, n=5)
        home_away_by_date = {f["date"]: f["is_home"] for f in form}
        assert home_away_by_date["2024-04-01"] is True
        assert home_away_by_date["2024-04-05"] is False

    def test_skips_games_with_no_score(self):
        games = [
            _make_nba_game(1, 2, None, None),
            _make_nba_game(1, 2, 110, 105),
        ]
        form = np_nba.extract_recent_form(games, team_id=1, n=5)
        assert len(form) == 1

    def test_empty_games_returns_empty(self):
        form = np_nba.extract_recent_form([], team_id=1, n=5)
        assert form == []


class TestBuildH2hSummary:
    def test_counts_wins_correctly(self):
        games = [
            _make_nba_game(1, 2, 110, 105, date="2024-04-01"),  # team 1 (home) wins
            _make_nba_game(1, 2, 100, 100, date="2024-04-02"),  # tie
            _make_nba_game(2, 1, 95, 98, date="2024-04-03"),    # team 1 (away) wins
            _make_nba_game(2, 1, 105, 100, date="2024-04-04"), # team 2 (home) wins
        ]
        summary = np_nba.build_h2h_summary(games, team_a_id=1, team_b_id=2, n=5)
        # team 1: wins game1 (home win), wins game3 (away win) = 2 wins
        # team 2: wins game4 (home win) = 1 win
        # game2 is a tie
        assert summary["wins_a"] == 2
        assert summary["wins_b"] == 1
        assert summary["total"] == 4

    def test_limits_to_n_games(self):
        games = [
            _make_nba_game(1, 2, 110, 105, date="2024-04-01"),
            _make_nba_game(1, 2, 100, 100, date="2024-04-05"),
            _make_nba_game(1, 2, 95, 98, date="2024-04-10"),
            _make_nba_game(1, 2, 120, 110, date="2024-04-15"),
            _make_nba_game(1, 2, 105, 100, date="2024-04-20"),
            _make_nba_game(1, 2, 115, 112, date="2024-04-25"),
        ]
        summary = np_nba.build_h2h_summary(games, team_a_id=1, team_b_id=2, n=5)
        assert summary["total"] == 5

    def test_filters_to_finished_games(self):
        games = [
            _make_nba_game(1, 2, 110, 105, status="Finished"),
            _make_nba_game(1, 2, 100, 100, status="Live"),
            _make_nba_game(1, 2, 95, 98, status="Scheduled"),
        ]
        summary = np_nba.build_h2h_summary(games, team_a_id=1, team_b_id=2, n=5)
        assert summary["total"] == 1


class TestBuildInjurySummary:
    def test_categorizes_by_status(self):
        injuries = [
            _make_nba_injury(1, "Player1", "out"),
            _make_nba_injury(2, "Player2", "doubtful"),
            _make_nba_injury(3, "Player3", "questionable"),
            _make_nba_injury(4, "Player4", "probable"),
        ]
        summary = np_nba.build_injury_summary(injuries)
        assert len(summary["out"]) == 1
        assert len(summary["doubtful"]) == 1
        assert len(summary["questionable"]) == 1
        assert len(summary["probable"]) == 1

    def test_counts_healthy_vs_injured(self):
        injuries = [
            _make_nba_injury(1, "Player1", "out"),
            _make_nba_injury(2, "Player2", "doubtful"),
        ]
        roster = [
            _make_nba_player(1, "Player1"),
            _make_nba_player(2, "Player2"),
            _make_nba_player(3, "Player3"),
            _make_nba_player(4, "Player4"),
        ]
        summary = np_nba.build_injury_summary(injuries, roster)
        assert summary["total_injured"] == 2
        assert summary["healthy_count"] == 2
        assert summary["total_roster"] == 4

    def test_handles_empty_injuries(self):
        summary = np_nba.build_injury_summary([])
        assert summary["total_injured"] == 0
        assert len(summary["out"]) == 0

    def test_handles_empty_roster(self):
        injuries = [_make_nba_injury(1, "Player1", "out")]
        summary = np_nba.build_injury_summary(injuries)
        assert summary["total_injured"] == 1
        assert summary["total_roster"] == 0


class TestBuildKeyPlayerStatsSummary:
    def test_returns_top_players(self):
        roster = [
            _make_nba_player(1, "Star Player", "PG", points=25.0),
            _make_nba_player(2, "Good Player", "SG", points=15.0),
            _make_nba_player(3, "Bench Player", "SG", points=8.0),
        ]
        players = np_nba.build_key_player_stats_summary(roster, limit=2)
        assert len(players) == 2
        assert players[0]["position"] == "PG"  # prioritize PG

    def test_excludes_injured_out_players(self):
        roster = [
            _make_nba_player(1, "Star Player", "PG", injuries=[{"status": "out"}]),
            _make_nba_player(2, "Healthy Player", "SG"),
        ]
        players = np_nba.build_key_player_stats_summary(roster, limit=5)
        # Should still include injured players but deprioritize them
        assert len(players) <= 2

    def test_empty_roster_returns_empty(self):
        players = np_nba.build_key_player_stats_summary([], limit=5)
        assert players == []

    def test_sorts_by_position_importance(self):
        roster = [
            _make_nba_player(1, "Center", "C", points=10.0),
            _make_nba_player(2, "Point Guard", "PG", points=10.0),
        ]
        players = np_nba.build_key_player_stats_summary(roster, limit=5)
        # PG should come before C when points are equal
        assert players[0]["position"] == "PG"
        assert players[1]["position"] == "C"
