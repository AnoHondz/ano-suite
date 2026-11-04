import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import model_tracker as mt
import result_updater as ru
import api_client as ac


def _mock_nba_game() -> dict:
    return {
        "id": "game-1",
        "date": {"start": "2026-04-01T19:00:00Z"},
        "status": {"long": "Final", "short": "Final", "state": "post"},
        "teams": {
            "home": {"name": "Boston Celtics", "nickname": "Celtics"},
            "visitors": {"name": "Miami Heat", "nickname": "Heat"},
        },
        "scores": {"home": {"points": 110}, "visitors": {"points": 101}},
    }


class TestTrackingMetrics:
    def test_summary_accuracy_uses_tracked_parlay_result(self, tmp_path):
        tracking_file = tmp_path / "prediction_tracking.json"
        with patch.object(mt, "_TRACKING_FILE", str(tracking_file)):
            pred_id = mt.save_prediction(
                sport="soccer",
                team_a="Team A",
                team_b="Team B",
                predicted_winner="A",
                win_probs={"a": 52.0, "b": 28.0, "draw": 20.0},
                confidence="High",
                game_date="2026-04-01",
                totals_pick="Under",
                totals_line=2.5,
            )
            mt.update_prediction_result(pred_id, "A", {"a": 1, "b": 1})

            metrics = mt.get_summary_metrics()

        assert metrics["finalized_predictions"] == 1
        assert metrics["wins"] == 1
        assert metrics["losses"] == 0
        assert metrics["overall_accuracy"] == 100.0

    def test_team_name_prediction_label_grades_hit_and_keeps_totals_secondary(self, tmp_path):
        tracking_file = tmp_path / "prediction_tracking.json"
        with patch.object(mt, "_TRACKING_FILE", str(tracking_file)):
            pred_id = mt.save_prediction(
                sport="soccer",
                team_a="Sunderland",
                team_b="Tottenham Hotspur",
                predicted_winner="Sunderland Win",
                win_probs={"a": 60.5, "b": 13.5, "draw": 26.0},
                confidence="High",
                game_date="2026-04-12",
                fixture_id=321,
                totals_pick="Over",
                totals_line=2.5,
            )
            mt.update_prediction_result(pred_id, "A", {"a": 1, "b": 0}, fixture_id=321)

            record = mt.get_prediction_by_id(pred_id)

        assert record is not None
        assert record["predicted_winner"] == "A"
        assert record["predicted_winner_display"] == "Sunderland"
        assert record["winner_hit"] is True
        assert record["game_win"] is False
        assert record["ou_hit"] is False
        assert record["overall_game_result"] == "Loss"
        assert record["fixture_id"] == "321"

    def test_parlay_outcome_covers_all_leg_combinations(self, tmp_path):
        tracking_file = tmp_path / "prediction_tracking.json"
        with patch.object(mt, "_TRACKING_FILE", str(tracking_file)):
            both_hit = mt.save_prediction(
                sport="soccer",
                team_a="Alpha",
                team_b="Beta",
                predicted_winner="A",
                win_probs={"a": 58, "b": 24, "draw": 18},
                confidence="High",
                game_date="2026-04-01",
                totals_pick="Over",
                totals_line=2.5,
            )
            winner_only = mt.save_prediction(
                sport="soccer",
                team_a="Gamma",
                team_b="Delta",
                predicted_winner="A",
                win_probs={"a": 57, "b": 23, "draw": 20},
                confidence="Medium",
                game_date="2026-04-01",
                totals_pick="Over",
                totals_line=2.5,
            )
            totals_only = mt.save_prediction(
                sport="soccer",
                team_a="Epsilon",
                team_b="Zeta",
                predicted_winner="A",
                win_probs={"a": 52, "b": 26, "draw": 22},
                confidence="Low",
                game_date="2026-04-01",
                totals_pick="Under",
                totals_line=2.5,
            )
            both_miss = mt.save_prediction(
                sport="soccer",
                team_a="Eta",
                team_b="Theta",
                predicted_winner="A",
                win_probs={"a": 55, "b": 20, "draw": 25},
                confidence="Low",
                game_date="2026-04-01",
                totals_pick="Over",
                totals_line=2.5,
            )

            mt.update_prediction_result(both_hit, "A", {"a": 2, "b": 1})
            mt.update_prediction_result(winner_only, "A", {"a": 1, "b": 0})
            mt.update_prediction_result(totals_only, "B", {"a": 0, "b": 1})
            mt.update_prediction_result(both_miss, "B", {"a": 0, "b": 1})

            hit_record = mt.get_prediction_by_id(both_hit)
            winner_only_record = mt.get_prediction_by_id(winner_only)
            totals_only_record = mt.get_prediction_by_id(totals_only)
            miss_record = mt.get_prediction_by_id(both_miss)

        assert hit_record["winner_hit"] is True and hit_record["ou_hit"] is True and hit_record["game_win"] is True
        assert winner_only_record["winner_hit"] is True and winner_only_record["ou_hit"] is False and winner_only_record["game_win"] is False
        assert totals_only_record["winner_hit"] is False and totals_only_record["ou_hit"] is True and totals_only_record["game_win"] is False
        assert miss_record["winner_hit"] is False and miss_record["ou_hit"] is False and miss_record["game_win"] is False

    def test_summary_metrics_zero_completed(self, tmp_path):
        tracking_file = tmp_path / "prediction_tracking.json"
        with patch.object(mt, "_TRACKING_FILE", str(tracking_file)):
            mt.save_prediction(
                sport="soccer",
                team_a="A",
                team_b="B",
                predicted_winner="A",
                win_probs={"a": 50.0, "b": 30.0, "draw": 20.0},
                confidence="Low",
                game_date="2026-04-01",
            )
            metrics = mt.get_summary_metrics()
        assert metrics["total_predictions"] == 1
        assert metrics["finalized_predictions"] == 0
        assert metrics["overall_accuracy"] is None

    def test_summary_metrics_mixed_outcomes_confidence_and_sport(self, tmp_path):
        tracking_file = tmp_path / "prediction_tracking.json"
        with patch.object(mt, "_TRACKING_FILE", str(tracking_file)):
            p1 = mt.save_prediction("soccer", "A", "B", "A", {"a": 55, "b": 25, "draw": 20}, "High", "2026-04-01", totals_pick="Over", totals_line=2.5)
            p2 = mt.save_prediction("soccer", "C", "D", "B", {"a": 30, "b": 50, "draw": 20}, "Low", "2026-04-01", totals_pick="Under", totals_line=2.5)
            p3 = mt.save_prediction("nba", "E", "F", "A", {"a": 52, "b": 48, "draw": 0}, "High", "2026-04-01")

            mt.update_prediction_result(p1, "A", {"a": 2, "b": 1})  # parlay win
            mt.update_prediction_result(p2, "A", {"a": 1, "b": 0})  # winner miss, totals hit => overall loss
            mt.update_prediction_result(p3, "A", {"a": 110, "b": 99})  # win

            metrics = mt.get_summary_metrics()

        assert metrics["finalized_predictions"] == 3
        assert metrics["wins"] == 2
        assert metrics["losses"] == 1
        assert metrics["overall_accuracy"] == 66.7
        assert metrics["by_confidence"]["High"]["wins"] == 2
        assert metrics["by_confidence"]["Low"]["losses"] == 1
        assert metrics["by_sport"]["soccer"]["wins"] == 1
        assert metrics["by_sport"]["soccer"]["losses"] == 1
        assert metrics["by_sport"]["nba"]["wins"] == 1

    def test_summary_metrics_include_soccer_by_league(self, tmp_path):
        tracking_file = tmp_path / "prediction_tracking.json"
        with patch.object(mt, "_TRACKING_FILE", str(tracking_file)):
            p1 = mt.save_prediction(
                "soccer", "Arsenal", "Chelsea", "A", {"a": 60, "b": 20, "draw": 20}, "High", "2026-04-01",
                league_id=39, league_name="Premier League", totals_pick="Over", totals_line=2.5
            )
            p2 = mt.save_prediction(
                "soccer", "Madrid", "Sevilla", "A", {"a": 58, "b": 22, "draw": 20}, "Medium", "2026-04-01",
                league_id=140, league_name="La Liga", totals_pick="Over", totals_line=2.5
            )

            mt.update_prediction_result(p1, "A", {"a": 2, "b": 1})
            mt.update_prediction_result(p2, "B", {"a": 0, "b": 1})
            metrics = mt.get_summary_metrics()

        assert metrics["by_league"]["Premier League"]["wins"] == 1
        assert metrics["by_league"]["Premier League"]["count"] == 1
        assert metrics["by_league"]["La Liga"]["losses"] == 1


class TestResultUpdater:
    def test_fetch_nba_result_reads_live_client_shape(self):
        with patch("result_updater.nc.get_scoreboard_games", return_value=[_mock_nba_game()]):
            result = ru.fetch_nba_result(
                team_a="Boston Celtics",
                team_b="Miami Heat",
                date_str="2026-04-01",
            )

        assert result is not None
        assert result["found"] is True
        assert result["winner"] == "A"
        assert result["score"] == {"a": 110, "b": 101}
        assert result["fixture_id"] == "game-1"

    def test_fetch_nba_result_ignores_non_final_games(self):
        game = _mock_nba_game()
        game["status"] = {"long": "Scheduled", "short": "7:00 PM", "state": "pre"}
        with patch("result_updater.nc.get_scoreboard_games", return_value=[game]):
            result = ru.fetch_nba_result("Boston Celtics", "Miami Heat", "2026-04-01")
        assert result is None

    def test_fetch_soccer_result_handles_draw(self):
        fixture = {
            "fixture": {"date": "2026-04-01T15:00:00+00:00", "status": {"short": "FT"}},
            "teams": {"home": {"name": "Alpha FC"}, "away": {"name": "Beta FC"}},
            "goals": {"home": 1, "away": 1},
        }
        with patch("result_updater.ac.get_espn_fixtures", return_value=[fixture]):
            result = ru.fetch_soccer_result("Alpha FC", "Beta FC", "2026-04-01")
        assert result is not None
        assert result["winner"] == "draw"

    def test_fetch_soccer_result_skips_non_final_or_missing_scores(self):
        fixtures = [
            {
                "fixture": {"date": "2026-04-01T15:00:00+00:00", "status": {"short": "NS"}},
                "teams": {"home": {"name": "Alpha"}, "away": {"name": "Beta"}},
                "goals": {"home": None, "away": None},
            },
            {
                "fixture": {"date": "2026-04-01T15:00:00+00:00", "status": {"short": "FT"}},
                "teams": {"home": {"name": "Alpha"}, "away": {"name": "Beta"}},
                "goals": {"home": None, "away": 1},
            },
        ]
        with patch("result_updater.ac.get_espn_fixtures", return_value=fixtures):
            result = ru.fetch_soccer_result("Alpha", "Beta", "2026-04-01")
        assert result is None

    def test_update_pending_predictions_idempotent_and_skips_completed(self, tmp_path):
        tracking_file = tmp_path / "prediction_tracking.json"
        with patch.object(mt, "_TRACKING_FILE", str(tracking_file)):
            pred_id = mt.save_prediction(
                sport="soccer",
                team_a="Alpha",
                team_b="Beta",
                predicted_winner="A",
                win_probs={"a": 55, "b": 25, "draw": 20},
                confidence="High",
                game_date="2026-04-01",
            )

            with patch("result_updater.fetch_soccer_result", return_value={"found": True, "winner": "A", "score": {"a": 2, "b": 1}}):
                first = ru.update_pending_predictions()
            assert first["checked"] == 1
            assert first["updated"] == 1

            with patch("result_updater.fetch_soccer_result", return_value={"found": True, "winner": "A", "score": {"a": 2, "b": 1}}):
                second = ru.update_pending_predictions()
            assert second["checked"] == 0
            assert second["updated"] == 0

            completed = [p for p in mt._load_predictions() if p.get("id") == pred_id][0]
            assert completed["status"] == "completed"
            assert completed["actual_result"] == "A"

    def test_update_pending_predictions_keeps_pending_when_no_result(self, tmp_path):
        tracking_file = tmp_path / "prediction_tracking.json"
        with patch.object(mt, "_TRACKING_FILE", str(tracking_file)):
            pred_id = mt.save_prediction(
                sport="nba",
                team_a="Boston Celtics",
                team_b="Miami Heat",
                predicted_winner="A",
                win_probs={"a": 52, "b": 48, "draw": 0},
                confidence="Medium",
                game_date="2026-04-01",
            )
            with patch("result_updater.fetch_nba_result", return_value=None):
                stats = ru.update_pending_predictions()
            assert stats["checked"] == 1
            assert stats["updated"] == 0
            row = [p for p in mt._load_predictions() if p.get("id") == pred_id][0]
            assert row["status"] == "pending"

    def test_update_pending_predictions_passes_saved_soccer_league(self, tmp_path):
        tracking_file = tmp_path / "prediction_tracking.json"
        with patch.object(mt, "_TRACKING_FILE", str(tracking_file)):
            mt.save_prediction(
                sport="soccer",
                team_a="Madrid",
                team_b="Sevilla",
                predicted_winner="A",
                win_probs={"a": 55, "b": 25, "draw": 20},
                confidence="High",
                game_date="2026-04-01",
                league_id=140,
                league_name="La Liga",
            )

            with patch("result_updater.fetch_soccer_result", return_value=None) as fetch_mock:
                ru.update_pending_predictions()

        assert fetch_mock.call_args.kwargs["league_id"] == 140


class TestMatchEventsHelper:
    def test_get_match_events_filters_goals_penalties_own_goals_and_dedupes(self):
        fixture_payload = {
            "response": [
                {
                    "teams": {
                        "home": {"name": "Sunderland"},
                        "away": {"name": "Tottenham Hotspur"},
                    }
                }
            ]
        }
        events = [
            {
                "type": "Goal",
                "detail": "Normal Goal",
                "team": {"name": "Sunderland"},
                "player": {"name": "Player A"},
                "time": {"elapsed": 23},
            },
            {
                "type": "Goal",
                "detail": "Normal Goal",
                "team": {"name": "Sunderland"},
                "player": {"name": "Player A"},
                "time": {"elapsed": 23},
            },
            {
                "type": "Goal",
                "detail": "Penalty",
                "team": {"name": "Tottenham Hotspur"},
                "player": {"name": "Player B"},
                "time": {"elapsed": 55},
            },
            {
                "type": "Goal",
                "detail": "Own Goal",
                "team": {"name": "Tottenham Hotspur"},
                "player": {"name": "Player C"},
                "time": {"elapsed": 67},
            },
            {
                "type": "Card",
                "detail": "Yellow Card",
                "team": {"name": "Sunderland"},
                "player": {"name": "Player D"},
                "time": {"elapsed": 71},
            },
        ]

        with patch("api_client.api_get", return_value=fixture_payload), \
             patch("api_client.get_fixture_events", return_value=events):
            result = ac.get_match_events(123)

        assert result["home_team"] == "Sunderland"
        assert result["away_team"] == "Tottenham Hotspur"
        assert len(result["home_goals"]) == 2
        assert result["home_goals"][0]["player"] == "Player A"
        assert result["home_goals"][1]["type"] == "Own Goal"
        assert len(result["away_goals"]) == 1
        assert result["away_goals"][0]["type"] == "Penalty"
