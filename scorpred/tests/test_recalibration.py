import numpy as np
import pytest

import model_tracker as mt
from services import recalibration as rc


def _picks(n, sport, *, overconfident, seed=3):
    """Graded verified picks whose published probabilities are too extreme when ``overconfident``."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        true_home = rng.uniform(0.3, 0.7)
        published = min(0.97, max(0.03, 0.5 + (true_home - 0.5) * (3.0 if overconfident else 1.0)))
        home_won = rng.random() < true_home
        rows.append({"sport": sport, "status": "completed", "created_at": f"2026-01-01T00:00:00Z",
                     "kickoff_at": f"2026-01-{1 + i % 28:02d}T{10 + i % 10}:00:00Z",
                     "engine": {"version": "v-test"}, "team_a": "H", "team_b": "A",
                     "actual_result": "A" if home_won else "B",
                     "raw_probs": {"a": published * 100, "draw": 0, "b": (1 - published) * 100}})
    return rows


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "_path", lambda: tmp_path / "recalibration.json")
    monkeypatch.setattr(rc, "_state", None)
    monkeypatch.setattr(rc, "_engine_version", lambda sport: "v-test")


def test_too_few_picks_leaves_probabilities_unchanged(isolated, monkeypatch):
    monkeypatch.setattr(mt, "_load_predictions", lambda: _picks(50, "nba", overconfident=True))
    record = rc.learn("nba")
    assert not record["active"] and "Needs" in record["reason"]
    assert rc.adjust("nba", 0.8, 0.0, 0.2) == (0.8, 0.0, 0.2)


def test_overconfidence_is_learned_and_applied(isolated, monkeypatch):
    monkeypatch.setattr(mt, "_load_predictions", lambda: _picks(1500, "nba", overconfident=True))
    record = rc.learn("nba")
    assert record["active"] and record["params"]["temperature"] > 1.3
    home, _draw, away = rc.adjust("nba", 0.9, 0.0, 0.1)
    assert 0.5 < home < 0.9 and home + away == pytest.approx(1.0)
    assert rc.state()["nba"]["active"]


def test_well_calibrated_picks_do_not_switch_learning_on(isolated, monkeypatch):
    monkeypatch.setattr(mt, "_load_predictions", lambda: _picks(1500, "nba", overconfident=False))
    assert not rc.learn("nba")["active"]


def test_learning_is_ignored_after_an_engine_change(isolated, monkeypatch):
    monkeypatch.setattr(mt, "_load_predictions", lambda: _picks(1500, "nba", overconfident=True))
    assert rc.learn("nba")["active"]
    monkeypatch.setattr(rc, "_engine_version", lambda sport: "v-next")
    assert rc.active_params("nba") is None


def test_soccer_transform_keeps_a_distribution():
    p = rc.transform("soccer", np.array([0.5, 0.25, 0.25]), {"temperature": 1.5, "offset": 0.2})
    assert p.sum() == pytest.approx(1.0) and p[0] < 0.5 and p[1] > 0.25
