"""Tests for the leakage-safe ML comparison helpers."""

from __future__ import annotations

from datetime import date, timedelta
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import ml_pipeline as mlp


def _sample_rows(count: int = 24) -> list[dict]:
    rows = []
    start = date(2024, 1, 1)
    for idx in range(count):
        form_gap = (idx % 6) - 2
        xg_gap = round(form_gap * 0.7 + (0.9 if idx % 2 == 0 else -0.4), 2)
        home_edge = 1 if idx % 3 != 1 else 0
        injuries_gap = -1 if idx % 5 == 0 else 0
        label = 1 if (form_gap + xg_gap + home_edge + injuries_gap) > 0.5 else 0
        rows.append(
            {
                "date": (start + timedelta(days=idx)).isoformat(),
                "form_gap": form_gap,
                "xg_gap": xg_gap,
                "home_edge": home_edge,
                "injuries_gap": injuries_gap,
                "label": label,
            }
        )
    return rows


def _sample_result_rows(count: int = 24) -> list[dict]:
    rows = []
    start = date(2024, 1, 1)
    for idx in range(count):
        home_goals = (idx * 3) % 4
        away_goals = (idx + 1) % 3
        goal_diff = home_goals - away_goals
        if goal_diff > 0:
            result = "HomeWin"
        elif goal_diff < 0:
            result = "AwayWin"
        else:
            result = "Draw"
        rows.append(
            {
                "date": (start + timedelta(days=idx)).isoformat(),
                "form": float((idx * 7) % 11),
                "goals_scored": home_goals,
                "goals_conceded": away_goals,
                "goal_diff": goal_diff,
                "result": result,
            }
        )
    return rows


def test_chronological_train_test_split_orders_rows_before_splitting():
    rows = [
        {"date": "2024-02-10", "label": 1},
        {"date": "2024-01-15", "label": 0},
        {"date": "2024-03-05", "label": 1},
        {"date": "2024-01-20", "label": 0},
    ]

    train_rows, test_rows = mlp.chronological_train_test_split(rows, test_ratio=0.25)

    assert [row["date"] for row in train_rows] == ["2024-01-15", "2024-01-20", "2024-02-10"]
    assert [row["date"] for row in test_rows] == ["2024-03-05"]
    assert max(row["date"] for row in train_rows) < min(row["date"] for row in test_rows)


def test_compare_binary_models_reports_both_baselines_and_workflow():
    report = mlp.compare_binary_models(
        _sample_rows(),
        feature_keys=["form_gap", "xg_gap", "home_edge", "injuries_gap"],
        test_ratio=0.25,
    )

    assert report["best_model"] in {"logistic_regression", "random_forest"}
    assert [entry["model"] for entry in report["ranking"]] == ["logistic_regression", "random_forest"] or [
        entry["model"] for entry in report["ranking"]
    ] == ["random_forest", "logistic_regression"]
    assert set(report["models"]) == {"logistic_regression", "random_forest"}
    assert report["workflow"]["chronological_split"] is True
    assert report["workflow"]["train_size"] == 18
    assert report["workflow"]["test_size"] == 6
    assert report["workflow"]["train_end"] < report["workflow"]["test_start"]
    assert report["models"]["logistic_regression"]["top_features"]
    assert report["models"]["random_forest"]["top_features"]


def test_compare_binary_models_rejects_single_class_train_or_test_windows():
    rows = _sample_rows(10)
    for idx, row in enumerate(rows):
        row["label"] = 1 if idx < 9 else 0

    with pytest.raises(ValueError, match="Both train and test windows need both outcome classes"):
        mlp.compare_binary_models(
            rows,
            feature_keys=["form_gap", "xg_gap", "home_edge", "injuries_gap"],
            test_ratio=0.2,
        )


def test_save_and_load_comparison_report_round_trip(tmp_path):
    report = mlp.compare_binary_models(
        _sample_rows(),
        feature_keys=["form_gap", "xg_gap", "home_edge", "injuries_gap"],
        test_ratio=0.25,
    )

    report_path = tmp_path / "model_comparison.json"
    saved_path = mlp.save_comparison_report(report, report_path)
    loaded = mlp.load_comparison_report(saved_path)

    assert saved_path == report_path
    assert loaded is not None
    assert loaded["best_model"] == report["best_model"]
    assert loaded["workflow"]["test_size"] == 6
    assert "generated_at" in loaded


def test_build_strategy_lab_summary_formats_ml_comparison_for_ui():
    report = {
        "best_model": "random_forest",
        "models": {
            "logistic_regression": {
                "accuracy": 0.482,
                "top_features": [
                    {"feature": "home_form_last_5", "weight": 0.81},
                    {"feature": "league_draw_rate", "weight": -0.52},
                ],
            },
            "random_forest": {
                "accuracy": 0.501,
                "top_features": [
                    {"feature": "home_form_last_5", "importance": 0.24},
                    {"feature": "away_goals_conceded_last_5", "importance": 0.19},
                    {"feature": "strength_gap", "importance": 0.16},
                    {"feature": "league_draw_rate", "importance": 0.11},
                    {"feature": "recent_goal_trend", "importance": 0.09},
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

    summary = mlp.build_strategy_lab_summary(report=report)

    assert summary["available"] is True
    assert summary["best_model_label"] == "Random Forest"
    assert summary["baseline_logistic_accuracy"] == 48.2
    assert summary["random_forest_accuracy"] == 50.1
    assert summary["accuracy_gap"] == 1.9
    assert summary["accuracy_gap_display"] == "+1.9 pts"
    assert summary["evaluation_matches"] == 943
    assert summary["top_signals"] == [
        "home_form_last_5 (importance 0.240)",
        "away_goals_conceded_last_5 (importance 0.190)",
        "strength_gap (importance 0.160)",
        "league_draw_rate (importance 0.110)",
        "recent_goal_trend (importance 0.090)",
    ]


def test_compare_binary_models_accepts_homewin_draw_awaywin_labels():
    report = mlp.compare_binary_models(
        _sample_result_rows(),
        feature_keys=["form", "goals_scored", "goals_conceded", "goal_diff"],
        label_key="result",
        date_key="date",
        test_ratio=0.25,
    )

    assert report["best_model"] in {"logistic_regression", "random_forest"}
    assert report["workflow"]["label_key"] == "result"
