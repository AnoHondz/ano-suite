"""Regression tests for ScorMastermind recommendation behavior."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import scormastermind as sm


def _context_with_rule_probs(a: float, draw: float, b: float, data_quality: str = "Strong") -> dict:
    return {
        "sport": "soccer",
        "team_a_name": "Chelsea",
        "team_b_name": "Manchester United",
        "team_a_is_home": True,
        "form_a": [{"result": "W"}] * 5,
        "form_b": [{"result": "W"}] * 5,
        "team_stats": {
            "a": {"ok": True},
            "b": {"ok": True},
        },
        "rule_prediction": {
            "win_probabilities": {"a": a, "draw": draw, "b": b},
            "data_quality": data_quality,
            "best_pick": {
                "prediction": "Chelsea Win",
                "team": "A",
                "confidence": "High",
                "reasoning": "Rule model edge.",
            },
        },
        "ml_outputs": {"prob_a": 0.50},
    }


def test_avoid_triggered_when_probabilities_are_too_close():
    context = _context_with_rule_probs(31.5, 35.0, 33.5, data_quality="Strong")

    result = sm.predict_match(context)
    ui = result["ui_prediction"]

    assert ui["recommended_play"] == "Avoid"
    assert ui["best_pick"]["prediction"] == "Avoid"
    assert ui["best_pick"]["confidence"] == "Low"
    assert ui["top_lean"]["prediction"] == "Manchester United Win"
    assert ui["top_lean"]["probability"] >= 33.0
    assert "No strong edge found" in ui["avoid_reasons"]


def test_strong_matchup_keeps_real_pick():
    context = _context_with_rule_probs(78.0, 10.0, 12.0, data_quality="Strong")
    context["ml_outputs"] = {"prob_a": 0.92}

    result = sm.predict_match(context)
    ui = result["ui_prediction"]

    assert ui["recommended_play"] != "Avoid"
    assert ui["best_pick"]["prediction"] == "Chelsea Win"
    assert ui["best_pick"]["tracking_team"] == "A"
    assert ui["win_probabilities"]["a"] >= 70.0


def test_supported_side_edge_beats_default_draw():
    context = _context_with_rule_probs(38.0, 34.0, 28.0, data_quality="Strong")
    context["form_a"] = [{"result": "W"}] * 4 + [{"result": "D"}]
    context["form_b"] = [{"result": "L"}] * 3 + [{"result": "D"}] * 2
    context["ml_outputs"] = {
        "prob_a": 0.61,
        "prob_draw": 0.16,
        "prob_b": 0.23,
    }

    result = sm.predict_match(context)
    ui = result["ui_prediction"]

    assert ui["best_pick"]["prediction"] == "Chelsea Win"
    assert ui["best_pick"]["team"] == "A"
    assert ui["recommended_play"] != "Avoid"
    assert ui["win_probabilities"]["a"] > ui["win_probabilities"]["draw"]


def test_draw_requires_clear_margin_over_side_outcomes():
    context = _context_with_rule_probs(36.0, 35.2, 28.8, data_quality="Strong")
    context["ml_outputs"] = {
        "prob_a": 0.53,
        "prob_draw": 0.24,
        "prob_b": 0.23,
    }

    result = sm.predict_match(context)
    ui = result["ui_prediction"]

    assert ui["best_pick"]["team"] in {"A", "B", "avoid"}
    assert ui["top_lean"]["prediction"] != "Draw"


def test_recent_selector_can_override_to_ml(monkeypatch):
    context = _context_with_rule_probs(62.0, 16.0, 22.0, data_quality="Strong")
    context["ml_outputs"] = {
        "prob_a": 0.20,
        "prob_draw": 0.18,
        "prob_b": 0.62,
    }

    monkeypatch.setattr(
        sm,
        "_load_selector_profile",
        lambda: {
            "default_source": "combined",
            "overrides": [
                {
                    "segment": "model_disagreement",
                    "preferred_source": "ml",
                    "reason": "Recent backtests prefer ML when rule and ML disagree.",
                }
            ],
        },
    )

    result = sm.predict_match(context)
    ui = result["ui_prediction"]

    assert ui["selector_source"] == "ml"
    assert ui["selector_used_override"] is True
    assert ui["best_pick"]["team"] == "B"
    assert ui["best_pick"]["prediction"] == "Manchester United Win"


def test_market_edge_can_rescue_pure_confidence_avoid():
    context = _context_with_rule_probs(44.0, 16.0, 40.0, data_quality="Strong")
    context["ml_outputs"] = {"prob_a": 0.51}
    context["odds"] = {
        "home": 2.60,
        "draw": 3.80,
        "away": 2.90,
    }

    result = sm.predict_match(context)
    ui = result["ui_prediction"]

    assert ui["edge_data"]["available"] is True
    assert ui["edge_data"]["selected_edge"] is not None
    assert ui["play_type"] in {"LEAN", "BET"}
    assert ui["recommended_play"] != "Avoid"
    assert ui["best_pick"]["prediction"] != "Avoid"


def test_policy_thresholds_can_force_avoid(monkeypatch):
    context = _context_with_rule_probs(78.0, 10.0, 12.0, data_quality="Strong")
    context["ml_outputs"] = {"prob_a": 0.92}

    monkeypatch.setattr(
        sm.pp,
        "sport_policy",
        lambda _sport: {
            "min_confidence_pct": 99.0,
            "min_top_two_gap_pct": 3.0,
            "lean_min_confidence_pct": 80.0,
            "bet_min_confidence_pct": 95.0,
            "draw_min_top_prob_pct": 37.0,
        },
    )

    result = sm.predict_match(context)
    ui = result["ui_prediction"]

    assert ui["recommended_play"] == "Avoid"
    assert "No strong edge found" in ui["avoid_reasons"]


# ── Prompt 4: _ml_features non-default values when context is populated ───────

def _form_rows(n: int = 5, result: str = "W", gf: float = 2.0, ga: float = 0.5) -> list[dict]:
    return [{"result": result, "gf": gf, "ga": ga, "date": "2026-01-01"} for _ in range(n)]


def test_ml_features_h2h_computed_from_context():
    """h2h_home_points_avg and h2h_goal_diff_avg must be non-default when h2h_form_a is non-empty."""
    h2h_form = _form_rows(3, result="W", gf=2.0, ga=1.0)
    features = sm._ml_features({
        "sport": "soccer",
        "form_a": _form_rows(),
        "form_b": _form_rows(),
        "h2h_form_a": h2h_form,
    })
    # 3 wins → avg pts = 3.0; avg gd = 2.0 - 1.0 = 1.0
    assert features["h2h_home_points_avg"] == 3.0, features["h2h_home_points_avg"]
    assert features["h2h_goal_diff_avg"]   == 1.0, features["h2h_goal_diff_avg"]


def test_ml_features_h2h_two_entry_mixed_results():
    """Minimum realistic h2h sample: 2 entries with mixed results produce non-default values."""
    # 1 Win (gf=3, ga=1) + 1 Loss (gf=0, ga=2)
    # avg pts = (3 + 0) / 2 = 1.5  (not the default 1.0)
    # avg gd  = ((3-1) + (0-2)) / 2 = (2 + -2) / 2 = 0.0  (same as default, but computed)
    h2h_form = [
        {"result": "W", "gf": 3.0, "ga": 1.0, "date": "2026-01-10"},
        {"result": "L", "gf": 0.0, "ga": 2.0, "date": "2025-11-05"},
    ]
    features = sm._ml_features({
        "sport": "soccer",
        "form_a": _form_rows(),
        "form_b": _form_rows(),
        "h2h_form_a": h2h_form,
    })
    # avg pts = 1.5 ≠ default 1.0 → confirms real data used, not constant
    assert features["h2h_home_points_avg"] == 1.5, features["h2h_home_points_avg"]
    # avg gd = 0.0, same numeric value as default, but verify it was computed
    assert features["h2h_goal_diff_avg"] == 0.0, features["h2h_goal_diff_avg"]


def test_ml_features_opp_ppg_computed_from_form():
    """Each feature uses that team's historical opponents' pre-match snapshots."""
    features = sm._ml_features({
        "sport": "soccer",
        "form_a": [{**row, "opp_ppg": 2.4} for row in _form_rows(5, result="L", gf=0.0, ga=2.0)],
        "form_b": [{**row, "opp_ppg": 0.8} for row in _form_rows(5, result="W", gf=2.0, ga=0.0)],
        "h2h_form_a": [],
    })
    assert features["home_opp_avg_ppg_5"] == 2.4
    assert features["away_opp_avg_ppg_5"] == 0.8


def test_ml_features_use_ten_match_history_and_target_fixture_date():
    home_history = [
        {"result": "W", "gf": 2.0, "ga": 0.0, "date": "2026-10-15"}
        for _ in range(5)
    ] + [
        {"result": "L", "gf": 0.0, "ga": 2.0, "date": "2026-10-01"}
        for _ in range(5)
    ]
    away_history = [
        {"result": "D", "gf": 1.0, "ga": 1.0, "date": "2026-10-18"}
        for _ in range(10)
    ]

    features = sm._ml_features({
        "sport": "soccer",
        "form_a": _form_rows(5, gf=1.0, ga=1.0),
        "form_b": _form_rows(5, gf=1.0, ga=1.0),
        "ml_form_a": home_history,
        "ml_form_b": away_history,
        "target_date": "2026-10-25T15:00:00Z",
    })

    assert features["home_avg_gf_5"] == 2.0
    assert features["home_avg_gf_10"] == 1.0
    assert features["home_gf_delta_5v10"] == 1.0
    assert features["home_ppg_delta_5v10"] == 1.5
    assert features["days_since_last_match_home"] == 10.0
    assert features["days_since_last_match_away"] == 7.0


def test_ml_features_defaults_when_context_is_empty():
    """When no form or h2h data is present, sensible neutral defaults are returned."""
    features = sm._ml_features({"sport": "soccer"})
    assert features["h2h_home_points_avg"] == 1.0
    assert features["h2h_goal_diff_avg"]   == 0.0
    assert features["home_opp_avg_ppg_5"] is None
    assert features["away_opp_avg_ppg_5"] is None
