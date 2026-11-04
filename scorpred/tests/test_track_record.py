import math

import pytest

import app as module
import model_tracker as mt
from services import track_record


def record(sport, created, kickoff, correct, *, status="completed", season_type=None):
    row = {"sport": sport, "created_at": created, "kickoff_at": kickoff, "status": status, "is_correct": correct}
    if season_type is not None:
        row["season_type"] = season_type
    return row


@pytest.fixture
def records(tmp_path, monkeypatch):
    rows = [
        record("nba", "2026-10-20T11:00:00Z", "2026-10-20T23:00:00Z", True, season_type=2),
        record("nba", "2026-10-20T11:00:00Z", "2026-10-21T23:00:00Z", False, season_type=2),
        record("nba", "2026-10-22T00:30:00Z", "2026-10-21T23:00:00Z", True, season_type=2),
        record("nba", "2026-10-15T11:00:00Z", "2026-10-15T23:00:00Z", True, season_type=1),
        record("nba", "2026-10-22T11:00:00Z", "2026-10-23T23:00:00Z", None, status="pending", season_type=2),
        record("soccer", "2026-10-18T10:00:00Z", "2026-10-18T13:00:00Z", True),
        {"sport": "soccer", "created_at": "2026-10-18T10:00:00Z", "date": "2026-10-18", "status": "completed", "is_correct": True},
    ]
    monkeypatch.setattr(mt, "_load_predictions", lambda: rows)
    monkeypatch.setattr(track_record, "_MARKET_REPORT", tmp_path / "missing.json")
    return rows


def test_only_verified_regular_season_picks_are_counted(records):
    nba = track_record.summary()["sports"]["nba"]
    assert (nba["graded"], nba["correct"], nba["pending"]) == (2, 1, 1)
    assert nba["excluded"] == {"recorded_after_kickoff": 1, "no_kickoff_time": 0, "preseason": 1}
    assert nba["sample_status"] == "insufficient"
    soccer = track_record.summary()["sports"]["soccer"]
    assert (soccer["graded"], soccer["correct"]) == (1, 1)
    assert soccer["excluded"]["no_kickoff_time"] == 1


def test_results_list_graded_picks_newest_first_with_totals(records):
    records[0].update(totals_pick="Over", totals_result="Over", totals_line=220.5, total_scored=231)
    records[1].update(totals_pick="Under", totals_result="Push")
    nba = track_record.results("nba")
    assert [g["kickoff"] for g in nba["games"]] == ["2026-10-21T23:00:00Z", "2026-10-20T23:00:00Z"]
    assert (nba["winner"]["correct"], nba["winner"]["wrong"], nba["pending"]) == (1, 1, 1)
    assert (nba["totals"]["graded"], nba["totals"]["correct"]) == (1, 1)
    assert nba["games"][1]["totals"] == {"pick": "Over", "line": 220.5, "scored": 231, "correct": True}
    assert nba["games"][0]["totals"] is None
    response = module.app.test_client().get("/api/performance/results?sport=nfl")
    assert response.status_code == 400


def test_wilson_interval_is_wide_for_small_samples():
    low, high = track_record.wilson_interval(12, 14)
    assert 0.55 < low < 0.65 and 0.93 < high < 0.97
    assert track_record.wilson_interval(0, 0) is None


def test_endpoint_returns_summary_without_market_report(records):
    response = module.app.test_client().get("/api/performance/track-record")
    assert response.status_code == 200
    assert response.json["market_benchmark"] is None
    assert response.json["minimum_sample"] == track_record.MINIMUM_SAMPLE


def scored(outcome, probs, *, pick="A", market=None, closing=None, kickoff="2026-10-20T15:00:00Z"):
    row = {"sport": "soccer", "team_a": "Home", "team_b": "Away", "created_at": "2026-10-20T10:00:00Z",
           "kickoff_at": kickoff, "status": "completed", "actual_result": outcome, "predicted_winner": pick,
           "is_correct": pick == outcome, "prob_a": probs[0], "prob_draw": probs[1], "prob_b": probs[2]}
    if market:
        row["market_at_pick"] = {"fair": market}
    if closing:
        row["closing_market"] = {"fair": closing}
    return row


def test_probability_scores_market_comparison_and_closing_line_value(tmp_path, monkeypatch):
    rows = [
        scored("A", (50, 25, 25), market={"a": 0.4, "draw": 0.3, "b": 0.3}, closing={"a": 0.44, "draw": 0.28, "b": 0.28}),
        scored("B", (60, 20, 20), market={"a": 0.5, "draw": 0.2, "b": 0.3}, closing={"a": 0.45, "draw": 0.27, "b": 0.28},
               kickoff="2026-10-28T15:00:00Z"),
    ]
    monkeypatch.setattr(mt, "_load_predictions", lambda: rows)
    monkeypatch.setattr(track_record, "_MARKET_REPORT", tmp_path / "missing.json")
    soccer = track_record.summary()["sports"]["soccer"]
    expected = (-math.log(0.5) - math.log(0.2)) / 2
    assert soccer["probability"]["log_loss"] == pytest.approx(expected, abs=1e-4)
    assert soccer["probability"]["scored"] == 2
    assert [b["picks"] for b in soccer["probability"]["calibration"]] == [1, 1]
    market = soccer["market"]
    assert market["games"] == 2
    assert market["market_log_loss"] == pytest.approx((-math.log(0.4) - math.log(0.3)) / 2, abs=1e-4)
    assert market["model_minus_market"] > 0
    clv = soccer["closing_line_value"]
    assert clv["picks"] == 2
    assert clv["mean"] == pytest.approx((0.44 / 0.4 - 1 + 0.45 / 0.5 - 1) / 2, abs=1e-4)
    assert clv["positive_share"] == 0.5
    assert [w["week"] for w in soccer["weekly"]] == ["2026-W43", "2026-W44"]
    assert soccer["engines"] == {"unrecorded": 2}


def test_market_benchmark_reads_the_published_model_research():
    benchmark = track_record._market_benchmark()
    assert benchmark["test_window"] == {"start": "2021-22", "end": "2025-26"}
    assert benchmark["model_log_loss"] < benchmark["previous_engine_log_loss"]
    assert benchmark["log_loss_gap"] > 0


def test_recent_picks_and_score_accuracy(tmp_path, monkeypatch):
    rows = [scored("A", (50, 25, 25)), scored("B", (60, 20, 20), kickoff="2026-10-28T15:00:00Z")]
    rows[0].update(predicted_score={"a": 1.5, "b": 1.0, "likely": {"a": 1, "b": 0}}, final_score={"a": 1, "b": 0},
                   score_grade={"error_a": 0.5, "error_b": 1.0, "error_total": 1.5, "exact_hit": True})
    monkeypatch.setattr(mt, "_load_predictions", lambda: rows)
    monkeypatch.setattr(track_record, "_MARKET_REPORT", tmp_path / "missing.json")
    summary = track_record.summary()
    forecast = summary["sports"]["soccer"]["score_forecast"]
    assert forecast == {"graded": 1, "team_error": 0.75, "total_error": 1.5, "exact_score_hits": 1, "exact_score_rate": 1.0}
    recent = summary["recent"]
    assert [r["kickoff"] for r in recent] == ["2026-10-28T15:00:00Z", "2026-10-20T15:00:00Z"]
    assert recent[1]["final_score"] == {"a": 1, "b": 0} and recent[1]["result"] == "correct"
    assert recent[0]["result"] == "wrong" and recent[1]["pick_probability"] == 0.5
    assert set(summary["learning"]["sports"]) == {"soccer", "nba"}
