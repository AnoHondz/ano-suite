"""Unit tests for edge-based odds utilities."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import odds_utils


def test_decimal_to_raw_implied_converts_correctly():
    assert odds_utils.decimal_to_raw_implied(2.0) == 0.5


def test_decimal_to_raw_implied_rejects_invalid_values():
    try:
        odds_utils.decimal_to_raw_implied(1.0)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError for decimal odds <= 1.0")


def test_remove_vig_normalizes_to_one():
    fair = odds_utils.remove_vig({"home": 0.55, "draw": 0.26, "away": 0.30})
    assert abs(sum(fair.values()) - 1.0) < 1e-5


def test_compute_soccer_edge_returns_unavailable_when_missing_odds():
    edge = odds_utils.compute_soccer_edge(
        prob_a=0.44,
        prob_draw=0.24,
        prob_b=0.32,
        selected_team="A",
        home_odds=None,
        draw_odds=3.4,
        away_odds=2.8,
        base_play_type="LEAN",
    )
    assert edge["available"] is False
    assert edge["play_type"] == "LEAN"


def test_compute_soccer_edge_upgrades_avoid_to_lean_with_value_edge():
    edge = odds_utils.compute_soccer_edge(
        prob_a=0.47,
        prob_draw=0.22,
        prob_b=0.31,
        selected_team="A",
        home_odds=2.6,
        draw_odds=3.6,
        away_odds=2.9,
        base_play_type="AVOID",
    )
    assert edge["available"] is True
    assert edge["selected_edge"] > 0.03
    assert edge["play_type"] in {"LEAN", "BET"}
