from datetime import date, timedelta

import numpy as np
import pytest

from services import soccer_model as sm

PARAMS = {"half_life": 365.0, "ridge": 6.0, "elo_k": 15.0, "elo_home": 60.0}
LAYER = {"mean": [0.0] * 4, "scale": [1.0] * 4, "coef": [[1, 0, 0, 0], [0, 0, 0, 0], [-1, 0, 0, 0]],
         "intercept": [0.2, 0.0, 0.0]}


def _fixture(day, home, away, home_goals, away_goals, fixture_id):
    return {"fixture": {"id": fixture_id, "date": f"{day.isoformat()}T15:00Z", "status": {"short": "FT"}},
            "teams": {"home": {"name": home}, "away": {"name": away}},
            "goals": {"home": home_goals, "away": away_goals}}


class FakeClient:
    """Two seasons of a six-club league in which Strong beats everyone."""

    def __init__(self):
        clubs = ["Strong", "B", "C", "D", "E", "F"]
        self.results, start, n = [], date(2025, 8, 10), 0
        for round_ in range(30):
            for i, home in enumerate(clubs):
                away = clubs[(i + 1 + round_) % 6]
                if away == home:
                    continue
                goals = (3, 0) if home == "Strong" else (0, 2) if away == "Strong" else (1, 1)
                n += 1
                self.results.append(_fixture(start + timedelta(days=7 * round_), home, away, *goals, n))
        self.clubs = clubs

    def get_teams(self, league_id, season):
        return [{"team": {"id": club, "name": club}} for club in self.clubs]

    def _espn_schedule(self, team_id, league_id, season):
        return [f for f in self.results if team_id in (f["teams"]["home"]["name"], f["teams"]["away"]["name"])]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(sm, "_artifact", {"version": "test-v1", "ratings": PARAMS, "layer": LAYER})
    monkeypatch.setattr(sm, "_ratings", {})
    return FakeClient()


def test_combine_is_a_softmax_over_the_stored_layer():
    probabilities = sm.combine(np.array([0.5, 0, 0, 0]), LAYER)
    assert probabilities.sum() == pytest.approx(1.0)
    assert probabilities[0] > probabilities[2]


def test_ratings_favour_the_stronger_club_and_ignore_future_results(client):
    results, clubs = sm._fetch_results(client, 39, [2025])
    built = sm.build_ratings(results, clubs, date(2026, 1, 1), params=PARAMS)
    assert built["results"] < len(results)
    assert built["elo"].rating("Strong") > built["elo"].rating("B")
    lam, mu = built["dixon_coles"].rates("Strong", "B")
    assert lam > mu


def test_predict_and_apply_replace_rules_probabilities(client):
    fitted = sm.predict(client, 39, "Strong", "B")
    assert fitted["home"] > fitted["away"] and fitted["version"] == "test-v1"
    assert sm.predict(client, 39, "Strong", "Unknown FC") is None
    rules = {"win_probabilities": {"a": 30.0, "draw": 20.0, "b": 50.0}, "best_pick": {"prediction": "B Win", "team": "B"}}
    applied = sm.apply(rules, fitted, "Strong", "B")
    assert applied["best_pick"]["team"] == "A" and applied["best_pick"]["prediction"] == "Strong Win"
    assert applied["rules_win_probabilities"] == {"a": 30.0, "draw": 20.0, "b": 50.0}
    assert sum(applied["win_probabilities"].values()) == pytest.approx(100.0, abs=0.2)


def test_apply_leaves_the_prediction_alone_without_ratings():
    rules = {"win_probabilities": {"a": 30.0, "draw": 20.0, "b": 50.0}}
    assert sm.apply(dict(rules), None, "A", "B") == rules


def test_history_uses_espn_team_ids_instead_of_other_provider_ids(client, monkeypatch):
    from unittest.mock import Mock
    monkeypatch.setattr(client, "_espn_league_teams", lambda league: [
        {"team": {"id": "Strong", "name": "Strong"}}, {"team": {"id": "B", "name": "B"}}
    ], raising=False)
    monkeypatch.setattr(client, "get_teams", Mock(side_effect=AssertionError("Foreign provider IDs")))
    schedule = Mock(return_value=[])
    monkeypatch.setattr(client, "_espn_schedule", schedule)
    sm._fetch_results(client, 39, [2025])
    assert {call.args[0] for call in schedule.call_args_list} == {"Strong", "B"}


def test_provider_name_suffixes_resolve_to_actual_fitted_ratings(client):
    original = sm.predict(client, 39, "Strong", "B")
    resolved = sm.predict(client, 39, "Strong FC", "B AFC")
    assert resolved is not None
    assert resolved["elo"] == original["elo"]
    assert resolved["expected_goals"] == original["expected_goals"]


def test_roster_membership_without_fitted_history_cannot_publish_generic_forecast(client):
    sm.predict(client, 39, "Strong", "B")
    sm._ratings[39]["clubs"].add("Unobserved FC")
    assert sm.predict(client, 39, "Unobserved FC", "B") is None


def test_failed_rebuild_does_not_return_expired_ratings(client, monkeypatch):
    sm.predict(client, 39, "Strong", "B")
    sm._ratings[39]["built_at"] = 0
    monkeypatch.setattr(sm, "_fetch_results", lambda *args: ([], {}))
    assert sm.predict(client, 39, "Strong", "B") is None


def test_published_artifact_matches_the_research_report():
    import json
    from runtime_paths import model_dir, repo_root
    artifact = json.loads((model_dir() / sm.ARTIFACT_NAME).read_text())
    report = json.loads((repo_root() / "data" / "validation" / "soccer_model_research.json").read_text())
    assert artifact == report["published_artifact"]
    assert np.array(artifact["layer"]["coef"]).shape == (3, len(artifact["features"]))


def test_availability_adjustment_keeps_the_forecast_without_changes_and_moves_it_with_them():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import player_availability_backtest as backtest
    base = np.array([0.45, 0.27, 0.28])
    assert backtest.adjust(base, {"home": 0.0, "away": 0.0}, 1.0) is base
    weaker_home = backtest.adjust(base, {"home": -0.3, "away": 0.1}, 1.0)
    assert weaker_home.sum() == pytest.approx(1.0)
    assert weaker_home[0] < base[0] and weaker_home[2] > base[2]


def test_apply_overwrites_every_copy_of_the_rules_probabilities():
    fitted = {"home": 0.64, "draw": 0.21, "away": 0.15, "expected_goals": {"home": 1.9, "away": 0.8},
              "elo": {"home": 1550, "away": 1450}, "results_used": 640, "version": "soccer-ratings-v1"}
    rules = {"win_probabilities": {"a": 71.7, "draw": 14.5, "b": 13.8}, "prob_a": 71.7, "prob_draw": 14.5, "prob_b": 13.8,
             "home_pct": 71.7, "draw_pct": 14.5, "away_pct": 13.8, "confidence": "High", "best_pick": {}}
    applied = sm.apply(rules, fitted, "Stuttgart", "Gladbach")
    for keys in (("prob_a", "prob_draw", "prob_b"), ("home_pct", "draw_pct", "away_pct")):
        assert [applied[k] for k in keys] == [64.0, 21.0, 15.0]
    assert applied["confidence_pct"] == 64 and applied["top_lean"]["label"] == "Stuttgart"
    assert applied["model_source"] == "soccer-ratings-v1"


def test_scoreline_projection_matches_the_score_panel_contract(monkeypatch):
    monkeypatch.setattr(sm, "_artifact", {"version": "test-v1", "goals_validation": {
        "status": "walk_forward", "test_games": 10, "points_mae": 0.9, "baseline_mae": 1.0, "total_mae": 1.3,
        "interval_coverage": {"home": 0.93, "away": 0.94}, "test_end": "2026-05-24"}})
    fitted = {"version": "test-v1", "rho": -0.05, "expected_goals": {"home": 1.6, "away": 0.9}}
    projection = sm.scoreline_projection(fitted)
    assert projection["available"] and projection["projected_score"] == {"home": 1.6, "away": 0.9, "total": 2.5}
    assert projection["intervals"]["home"]["low"] <= 1.6 <= projection["intervals"]["home"]["high"]
    assert projection["references"][0]["url"].startswith("https://")
    assert sm.scoreline_projection(None) is None
