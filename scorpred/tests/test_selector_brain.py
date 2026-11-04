"""Tests for shared selector behavior."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import soccer_selector as selector


def test_build_segment_flags_marks_recently_sensitive_matchups():
    flags = selector.build_segment_flags(
        [0.36, 0.34, 0.30],
        [0.28, 0.38, 0.34],
        [0.32, 0.36, 0.32],
    )

    assert flags["draw_candidate"] is True
    assert flags["model_disagreement"] is True
    assert flags["close_match"] is True


def test_choose_source_prefers_override_then_default():
    profile = {
        "default_source": "combined",
        "summary": "Use the recent backtest winner by default.",
        "overrides": [
            {
                "segment": "model_disagreement",
                "preferred_source": "ml",
                "reason": "Recent backtests prefer ML when rule and ML disagree.",
            }
        ],
    }

    override_choice = selector.choose_source(
        profile,
        flags={"model_disagreement": True},
        available_sources=["rule", "ml", "combined"],
    )
    default_choice = selector.choose_source(
        profile,
        flags={"model_disagreement": False},
        available_sources=["rule", "ml", "combined"],
    )

    assert override_choice["source"] == "ml"
    assert override_choice["used_override"] is True
    assert default_choice["source"] == "combined"
    assert default_choice["used_override"] is False
