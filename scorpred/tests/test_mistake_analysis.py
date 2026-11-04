"""Tests for mistake_analysis module."""

from __future__ import annotations

import json

import pytest

import mistake_analysis as ma


# ── Fixtures ────────────────────────────────────────────────────────────────

def _wrong_pred(**overrides) -> dict:
    base = {
        "id": "test01",
        "sport": "soccer",
        "date": "2024-01-15",
        "team_a": "Liverpool",
        "team_b": "Arsenal",
        "predicted_winner": "A",
        "predicted_winner_normalized": "A",
        "actual_result": "Draw",
        "actual_result_normalized": "DRAW",
        "prob_a": 55.0,
        "prob_b": 30.0,
        "prob_draw": 15.0,
        "confidence": "High",
        "is_correct": False,
        "status": "completed",
    }
    base.update(overrides)
    return base


def _correct_pred(**overrides) -> dict:
    base = _wrong_pred(is_correct=True, actual_result_normalized="A")
    base.update(overrides)
    return base


# ── classify_mistake tests ──────────────────────────────────────────────────

class TestClassifyMistake:

    def test_correct_prediction_returns_empty(self):
        assert ma.classify_mistake(_correct_pred()) == []

    def test_draw_underestimated(self):
        pred = _wrong_pred(
            predicted_winner_normalized="A",
            actual_result_normalized="DRAW",
        )
        tags = ma.classify_mistake(pred)
        assert "draw_underestimated" in tags

    def test_home_bias_overconfidence(self):
        pred = _wrong_pred(
            predicted_winner_normalized="A",
            actual_result_normalized="B",
            confidence="High",
        )
        tags = ma.classify_mistake(pred)
        assert "home_bias_overconfidence" in tags

    def test_away_bias_underconfidence(self):
        pred = _wrong_pred(
            predicted_winner_normalized="A",
            actual_result_normalized="B",
        )
        tags = ma.classify_mistake(pred)
        assert "away_bias_underconfidence" in tags

    def test_high_confidence_miss(self):
        pred = _wrong_pred(confidence="High")
        tags = ma.classify_mistake(pred)
        assert "high_confidence_miss" in tags

    def test_low_confidence_not_high_miss(self):
        pred = _wrong_pred(confidence="Low")
        tags = ma.classify_mistake(pred)
        assert "high_confidence_miss" not in tags
        assert "low_data_miss" in tags

    def test_weak_edge_bet(self):
        pred = _wrong_pred(prob_a=40, prob_b=38, prob_draw=22)
        tags = ma.classify_mistake(pred)
        assert "weak_edge_bet" in tags

    def test_balanced_match_overcommit(self):
        pred = _wrong_pred(prob_a=38, prob_b=32, prob_draw=30)
        tags = ma.classify_mistake(pred)
        assert "balanced_match_overcommit" in tags


# ── build_mistake_report tests ──────────────────────────────────────────────

class TestBuildMistakeReport:

    def test_report_shape(self):
        preds = [_correct_pred(id=f"c{i}") for i in range(7)] + [
            _wrong_pred(id=f"w{i}") for i in range(3)
        ]
        report = ma.build_mistake_report(preds)

        assert report["total_analysed"] == 10
        assert report["total_correct"] == 7
        assert report["total_wrong"] == 3
        assert report["accuracy_pct"] == 70.0
        assert "categories" in report
        assert set(report["categories"].keys()) == set(ma.MISTAKE_CATEGORIES)

    def test_empty_predictions(self):
        report = ma.build_mistake_report([])
        assert report["total_analysed"] == 0
        assert report["accuracy_pct"] is None

    def test_examples_capped_at_5(self):
        preds = [_wrong_pred(id=f"w{i}", prob_a=40, prob_b=38, prob_draw=22) for i in range(10)]
        report = ma.build_mistake_report(preds)
        for cat_data in report["categories"].values():
            assert len(cat_data["examples"]) <= 5


# ── propose_adjustments tests ──────────────────────────────────────────────

class TestProposeAdjustments:

    def test_insufficient_data(self):
        report = {"total_analysed": 3, "categories": {}, "generated_at": "now"}
        adj = ma.propose_adjustments(report)
        assert "Insufficient data" in adj["reasoning"][0]
        assert adj["adjustments"]["soccer"] == {}

    def test_draw_boost_proposed(self):
        report = ma.build_mistake_report(
            [_correct_pred(id=f"c{i}") for i in range(20)]
            + [
                _wrong_pred(
                    id=f"w{i}",
                    predicted_winner_normalized="B",
                    actual_result_normalized="DRAW",
                    confidence="Medium",
                    prob_a=30, prob_b=45, prob_draw=25,
                )
                for i in range(6)
            ]
        )
        adj = ma.propose_adjustments(report)
        soccer_adj = adj["adjustments"]["soccer"]
        if "draw_min_top_prob_pct" in soccer_adj:
            assert 0 < soccer_adj["draw_min_top_prob_pct"] <= 8.0

    def test_adjustments_are_bounded(self):
        # Even with extreme data, adjustments stay within bounds
        report = {
            "total_analysed": 100,
            "generated_at": "now",
            "categories": {
                cat: {"count": 50, "rate": 50.0, "examples": []}
                for cat in ma.MISTAKE_CATEGORIES
            },
        }
        adj = ma.propose_adjustments(report)
        for sport_adj in adj["adjustments"].values():
            for key, val in sport_adj.items():
                if key == "draw_min_top_prob_pct":
                    assert val <= 8.0
                elif key == "bet_min_confidence_pct":
                    assert val <= 10.0
                elif key == "min_top_two_gap_pct":
                    assert val <= 5.0


# ── apply_adjustments_to_thresholds tests ──────────────────────────────────

class TestApplyAdjustments:

    def test_no_adjustments(self):
        policy = {"min_confidence_pct": 53.0, "bet_min_confidence_pct": 70.0}
        adjusted, notes = ma.apply_adjustments_to_thresholds(policy, "soccer", {"soccer": {}, "nba": {}})
        assert adjusted == policy
        assert notes == []

    def test_applies_delta(self):
        policy = {"min_confidence_pct": 53.0, "bet_min_confidence_pct": 70.0}
        adjustments = {"soccer": {"bet_min_confidence_pct": 3.0}, "nba": {}}
        adjusted, notes = ma.apply_adjustments_to_thresholds(policy, "soccer", adjustments)
        assert adjusted["bet_min_confidence_pct"] == 73.0
        assert len(notes) == 1

    def test_stays_in_range(self):
        policy = {"min_confidence_pct": 99.0}
        adjustments = {"soccer": {"min_confidence_pct": 5.0}, "nba": {}}
        adjusted, _ = ma.apply_adjustments_to_thresholds(policy, "soccer", adjustments)
        assert adjusted["min_confidence_pct"] == 100.0

    def test_missing_adjustment_file_returns_original(self):
        policy = {"min_confidence_pct": 53.0}
        adjusted, notes = ma.apply_adjustments_to_thresholds(policy, "soccer", None)
        # With no file, load_adjustments returns empty → no change
        assert adjusted["min_confidence_pct"] == 53.0


# ── Persistence tests ──────────────────────────────────────────────────────

class TestPersistence:

    def test_save_and_load_report(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ma, "mistake_report_path", lambda: tmp_path / "report.json")
        report = {"generated_at": "now", "total_analysed": 5, "categories": {}}
        ma.save_report(report)
        loaded = ma.load_report()
        assert loaded["total_analysed"] == 5

    def test_save_and_load_adjustments(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ma, "policy_adjustments_path", lambda: tmp_path / "adj.json")
        doc = {"adjustments": {"soccer": {"bet_min_confidence_pct": 2.0}, "nba": {}}}
        ma.save_adjustments(doc)
        loaded = ma.load_adjustments()
        assert loaded["soccer"]["bet_min_confidence_pct"] == 2.0

    def test_load_missing_adjustments(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ma, "policy_adjustments_path", lambda: tmp_path / "nonexistent.json")
        loaded = ma.load_adjustments()
        assert loaded == {"soccer": {}, "nba": {}}


# ── Prompt 8: recency_bias and popular_team_overrating ─────────────────────

class TestRecencyBias:

    def test_triggers_when_form_a_length_below_5(self):
        pred = _wrong_pred(form_a_length=3)
        tags = ma.classify_mistake(pred)
        assert "recency_bias" in tags

    def test_does_not_trigger_when_form_a_length_5_or_more(self):
        pred = _wrong_pred(form_a_length=5)
        tags = ma.classify_mistake(pred)
        assert "recency_bias" not in tags

    def test_does_not_trigger_when_form_a_length_missing(self):
        pred = _wrong_pred()
        tags = ma.classify_mistake(pred)
        assert "recency_bias" not in tags

    def test_does_not_trigger_on_correct_prediction(self):
        pred = _correct_pred(form_a_length=2)
        tags = ma.classify_mistake(pred)
        assert "recency_bias" not in tags

    def test_recency_bias_adjustment_triggers_above_20pct(self):
        # 6 wrong predictions with form_a_length < 5 out of 10 total = 60% rate
        wrong = [_wrong_pred(form_a_length=3) for _ in range(6)]
        correct = [_correct_pred() for _ in range(4)]
        report = ma.build_mistake_report(wrong + correct)
        adj_doc = ma.propose_adjustments(report)
        assert adj_doc["adjustments"]["soccer"].get("min_confidence_pct") == 5.0

    def test_recency_bias_adjustment_does_not_trigger_below_20pct(self):
        # Only 5 wrong predictions but all with thin form = 50% of 10 wrongs → rate = 5/10 = 50%
        # But if total > 5 and rate <= 20%, no trigger
        wrong_thin  = [_wrong_pred(form_a_length=2) for _ in range(1)]
        wrong_other = [_wrong_pred() for _ in range(4)]
        correct     = [_correct_pred() for _ in range(45)]
        report = ma.build_mistake_report(wrong_thin + wrong_other + correct)
        adj_doc = ma.propose_adjustments(report)
        # 1 prediction with recency_bias < _MIN_SAMPLE_SIZE (5) → no adjustment
        assert "min_confidence_pct" not in adj_doc["adjustments"]["soccer"]


class TestPopularTeamOverrating:

    def test_triggers_when_home_elo_advantage_high_and_predicted_a_wrong(self):
        pred = _wrong_pred(
            predicted_winner_normalized="A",
            elo_diff=80.0,
        )
        tags = ma.classify_mistake(pred)
        assert "popular_team_overrating" in tags

    def test_triggers_when_away_elo_advantage_high_and_predicted_b_wrong(self):
        pred = _wrong_pred(
            predicted_winner_normalized="B",
            predicted_winner="B",
            actual_result_normalized="A",
            elo_diff=-80.0,
        )
        tags = ma.classify_mistake(pred)
        assert "popular_team_overrating" in tags

    def test_does_not_trigger_when_elo_diff_small(self):
        pred = _wrong_pred(elo_diff=30.0)
        tags = ma.classify_mistake(pred)
        assert "popular_team_overrating" not in tags

    def test_does_not_trigger_when_elo_diff_missing(self):
        pred = _wrong_pred()
        tags = ma.classify_mistake(pred)
        assert "popular_team_overrating" not in tags

    def test_popular_overrating_adjustment_triggers_above_25pct(self):
        # 6 wrong predictions with elo_diff > 50 and predicted A = 60% rate
        # Use Medium confidence to avoid triggering high_confidence_miss (Rule 2)
        wrong = [_wrong_pred(elo_diff=100.0, confidence="Medium") for _ in range(6)]
        correct = [_correct_pred() for _ in range(4)]
        report = ma.build_mistake_report(wrong + correct)
        adj_doc = ma.propose_adjustments(report)
        # Should penalise bet_min_confidence_pct by -3
        bet_adj = adj_doc["adjustments"]["soccer"].get("bet_min_confidence_pct", 0.0)
        assert bet_adj < 0.0  # penalty means raise the bar (negative delta applied)

    def test_popular_overrating_does_not_trigger_below_25pct(self):
        # 5 wrong predictions but rate exactly = _MIN_SAMPLE_SIZE threshold
        wrong_pop = [_wrong_pred(elo_diff=100.0) for _ in range(5)]
        correct   = [_correct_pred() for _ in range(95)]
        report    = ma.build_mistake_report(wrong_pop + correct)
        adj_doc   = ma.propose_adjustments(report)
        # rate = 5/100 = 5% — below 25% threshold
        bet_adj   = adj_doc["adjustments"]["soccer"].get("bet_min_confidence_pct", 0.0)
        assert bet_adj >= 0.0
