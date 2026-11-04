"""Tests for policy tuning based on tracked backtests."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import optimize_prediction_policy as opo
import prediction_policy as pp


def _pred(
    *,
    sport: str,
    confidence_pct: float,
    gap_pct: float,
    winner_hit: bool,
    predicted_winner: str = "A",
) -> dict:
    top = max(0.0, min(99.0, confidence_pct))
    second = max(0.0, top - gap_pct)
    third = max(0.0, 100.0 - top - second)
    return {
        "sport": sport,
        "predicted_winner": predicted_winner,
        "confidence_pct": confidence_pct,
        "prob_a": top,
        "prob_b": second,
        "prob_draw": third,
        "winner_hit": winner_hit,
    }


def test_build_tuned_policy_keeps_defaults_for_small_samples():
    payload = opo.build_tuned_policy(
        predictions=[
            _pred(sport="soccer", confidence_pct=62.0, gap_pct=6.0, winner_hit=True),
            _pred(sport="soccer", confidence_pct=55.0, gap_pct=4.0, winner_hit=False),
            _pred(sport="nba", confidence_pct=58.0, gap_pct=5.0, winner_hit=True),
        ]
    )

    defaults = pp.default_policy()
    assert payload["sports"]["soccer"] == defaults["sports"]["soccer"]
    assert payload["sports"]["nba"] == defaults["sports"]["nba"]


def test_build_tuned_policy_improves_or_matches_default_score():
    soccer_predictions = []
    for _ in range(18):
        soccer_predictions.append(_pred(sport="soccer", confidence_pct=72.0, gap_pct=8.0, winner_hit=True))
    for _ in range(14):
        soccer_predictions.append(_pred(sport="soccer", confidence_pct=51.0, gap_pct=2.5, winner_hit=False))
    for _ in range(8):
        soccer_predictions.append(_pred(sport="soccer", confidence_pct=58.0, gap_pct=4.5, winner_hit=False))

    payload = opo.build_tuned_policy(predictions=soccer_predictions)
    tuned_cfg = payload["sports"]["soccer"]
    default_cfg = pp.default_policy()["sports"]["soccer"]

    tuned_metrics = opo._evaluate_policy(soccer_predictions, tuned_cfg)
    default_metrics = opo._evaluate_policy(soccer_predictions, default_cfg)

    assert tuned_metrics["score"] >= default_metrics["score"]
    assert tuned_metrics["hit_rate_pct"] >= default_metrics["hit_rate_pct"]
