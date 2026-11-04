from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from train_model import build_training_sample_weights, compute_recency_weights


def test_compute_recency_weights_emphasize_newer_rows():
    rows = [
        {"date": "2022-04-20"},
        {"date": "2024-04-20"},
        {"date": "2026-04-20"},
    ]

    weights = compute_recency_weights(rows)

    assert len(weights) == 3
    assert weights[2] > weights[1] > weights[0]
    assert pytest.approx(float(weights.mean()), rel=1e-6) == 1.0


def test_build_training_sample_weights_blends_class_balance_and_recency():
    rows = [
        {"date": "2023-01-01"},
        {"date": "2024-01-01"},
        {"date": "2025-01-01"},
        {"date": "2026-01-01"},
    ]
    y = [0, 0, 0, 1]

    weights = build_training_sample_weights(rows, y)

    assert len(weights) == 4
    assert pytest.approx(float(weights.mean()), rel=1e-6) == 1.0
    assert weights[-1] == max(weights)
    assert weights[-1] > weights[-2]
