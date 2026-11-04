"""Tests for Strategy Lab service helpers."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services import strategy_lab


def _window_payload(label: str, accuracy: float, start: str, end: str) -> dict:
    return {
        "label": label,
        "config": {
            "date_range": [start, end],
            "total_rows": 1200,
            "sample_weighting": {
                "type": "balanced_times_recency",
                "recency_half_life_days": 365.0,
                "recency_min_weight": 0.45,
                "recency_max_weight": 2.35,
            },
        },
        "aggregate": {
            "n_folds": 5,
            "total_test_matches": 240,
            "combined": {
                "mean_combined_accuracy": accuracy,
                "std_combined_accuracy": 0.012,
                "mean_rule_accuracy": accuracy - 0.02,
                "mean_ml_accuracy": accuracy - 0.01,
                "mean_avg_confidence_pct": 63.5,
                "by_predicted_outcome": {
                    "HomeWin": {"label": "HomeWin", "count": 110, "wins": 64, "losses": 46, "accuracy": 0.5818},
                    "Draw": {"label": "Draw", "count": 40, "wins": 19, "losses": 21, "accuracy": 0.4750},
                    "AwayWin": {"label": "AwayWin", "count": 90, "wins": 52, "losses": 38, "accuracy": 0.5778},
                },
                "by_confidence_bucket": {
                    "under_50": {"label": "Under 50%", "count": 20, "wins": 8, "losses": 12, "accuracy": 0.4000},
                    "50_59": {"label": "50-59%", "count": 100, "wins": 53, "losses": 47, "accuracy": 0.5300},
                    "60_69": {"label": "60-69%", "count": 80, "wins": 48, "losses": 32, "accuracy": 0.6000},
                    "70_plus": {"label": "70%+", "count": 40, "wins": 27, "losses": 13, "accuracy": 0.6750},
                },
            },
            "policy": {
                "aggregate_hit_rate_pct": 58.4,
                "aggregate_coverage_pct": 46.2,
                "total_placed": 111,
            },
            "base_models": {
                "stacking_ensemble": {"mean_accuracy": accuracy - 0.01},
                "lightgbm": {"mean_accuracy": accuracy - 0.03},
            },
            "trend": "stable",
            "trend_delta": 0.004,
        },
    }


def test_walk_forward_summary_supports_windowed_reports(tmp_path, monkeypatch):
    report_path = tmp_path / "walk_forward_report.json"
    report_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-04-19T00:00:00Z",
                "windows": {
                    "all_history": _window_payload("All History", 0.541, "2021-08-08", "2026-04-19"),
                    "last_3_years": _window_payload("Last 3 Years", 0.556, "2023-04-20", "2026-04-19"),
                },
                "selector": {
                    "default_source": "combined",
                    "default_accuracy": 0.556,
                    "segments": {
                        "overall": {"count": 240, "rule_accuracy": 0.53, "ml_accuracy": 0.548, "combined_accuracy": 0.556},
                        "model_disagreement": {"count": 90, "rule_accuracy": 0.46, "ml_accuracy": 0.57, "combined_accuracy": 0.51},
                    },
                    "overrides": [
                        {
                            "segment": "model_disagreement",
                            "preferred_source": "ml",
                            "sample_size": 90,
                            "preferred_accuracy": 0.57,
                            "default_accuracy": 0.51,
                            "gain_vs_default": 0.06,
                            "reason": "Rule and ML disagree on the likely winner.",
                        }
                    ],
                    "summary": "Default to Combined using recent backtests, with one segment override.",
                    "min_sample_size": 60,
                    "min_gain": 0.01,
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(strategy_lab, "walk_forward_report_path", lambda: report_path)

    summary = strategy_lab.walk_forward_summary()

    assert summary["available"] is True
    assert summary["windows"]["all_history"]["label"] == "All History"
    assert summary["windows"]["last_3_years"]["label"] == "Last 3 Years"
    assert summary["windows"]["last_3_years"]["mean_combined_accuracy"] == 55.6
    assert summary["windows"]["last_3_years"]["draw_accuracy_display"] == "47.5%"
    assert summary["windows"]["last_3_years"]["draw_sample_size"] == 40
    assert summary["windows"]["last_3_years"]["high_confidence_accuracy_display"] == "67.5%"
    assert summary["windows"]["last_3_years"]["high_confidence_sample_size"] == 40
    assert summary["windows"]["last_3_years"]["sample_weighting"]["type"] == "balanced_times_recency"
    assert summary["selector"]["available"] is True
    assert summary["selector"]["default_source_label"] == "Combined"
    assert summary["selector"]["override_rows"][0]["preferred_source_label"] == "Ensemble ML"


def test_build_strategy_lab_context_prefers_recent_weighted_strategy(monkeypatch):
    class TrackerStub:
        @staticmethod
        def get_summary_metrics():
            return {
                "total_predictions": 28,
                "finalized_predictions": 20,
                "wins": 11,
                "losses": 9,
                "overall_accuracy": 55.0,
                "by_confidence": {
                    "High": {"accuracy": 62.5, "count": 8, "wins": 5, "losses": 3},
                },
                "by_sport": {
                    "soccer": {"accuracy": 58.3, "count": 12, "wins": 7, "losses": 5},
                },
                "recent_predictions": [],
            }

        @staticmethod
        def get_completed_predictions(limit=6):
            return [{"winner_hit": True}] * limit

    class MlStub:
        DEFAULT_REPORT_PATH = "mock-report.json"

        @staticmethod
        def build_strategy_lab_summary():
            return {
                "available": True,
                "ensemble_accuracy": 54.6,
                "best_model_label": "Stacking Ensemble",
                "summary": "Stacking ensemble is currently the best saved ML model.",
            }

    monkeypatch.setattr(strategy_lab, "_ensure_ml_report_exists", lambda _ml_module: True)
    monkeypatch.setattr(
        strategy_lab,
        "_performance_comparison",
        lambda _metrics: {
            "available": True,
            "rule_accuracy": 51.2,
            "ml_accuracy": 54.6,
            "combined_accuracy": 55.0,
            "evaluation_matches": 240,
        },
    )
    monkeypatch.setattr(
        strategy_lab,
        "walk_forward_summary",
        lambda: {
            "available": True,
            "windows": {
                "all_history": {
                    "available": True,
                    "mean_combined_accuracy": 54.1,
                    "mean_ml_accuracy": 53.1,
                    "mean_rule_accuracy": 50.9,
                    "total_test_matches": 240,
                },
                "last_3_years": {
                    "available": True,
                    "mean_combined_accuracy": 55.6,
                    "mean_ml_accuracy": 54.3,
                    "mean_rule_accuracy": 50.4,
                    "total_test_matches": 180,
                },
            },
            "selector": {
                "available": True,
                "default_source": "combined",
                "default_source_label": "Combined",
                "override_rows": [],
            },
        },
    )

    context = strategy_lab.build_strategy_lab_context(
        tracker_module=TrackerStub(),
        ml_module=MlStub(),
    )

    recommendation = context["strategy_recommendation"]
    assert recommendation["source"] == "combined"
    assert recommendation["label"] == "Combined Signal"
    assert recommendation["action_label"] == "Use This Strategy"
    assert recommendation["window_bias_note"].startswith("Last 3 years")
    assert recommendation["trust_score"] >= 55.0
