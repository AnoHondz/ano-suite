import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

import app as module
import nba_predictor
import nba_routes
from scripts import train_scoring_models as training
from services import scoreline_engine as engine
from services import scoring_research as research
from services.scoreline_inputs import ScorelineInputError, validate


@pytest.mark.parametrize("params", [{}, {"team_a_id": 1, "team_b_id": 1}, {"team_a_id": "foo", "team_b_id": 2},
                                   {"team_a_id": 1, "team_b_id": 2, "odds_total": "nan"},
                                   {"team_a_id": 1, "team_b_id": 2, "ou_lines": "1.5,inf"},
                                   {"team_a_id": 1, "team_b_id": 2, "ou_lines": []},
                                   {"team_a_id": 1, "team_b_id": 2, "before": "2020-01-01T00:00:00Z"}])
def test_score_requests_reject_invalid_identities_and_nonfinite_totals(params):
    with pytest.raises(ScorelineInputError):
        validate(params, "soccer")


@pytest.mark.parametrize("sport", ["soccer", "nba"])
def test_missing_teams_are_a_clear_client_error(sport):
    response = module.app.test_client().get(f"/api/scoreline/{sport}")
    assert response.status_code == 400
    assert "different teams" in response.json["message"]


def test_missing_scoring_data_does_not_create_baseline_scores():
    with pytest.raises(engine.InsufficientScoreData):
        engine.predict_soccer_scoreline([], [])
    with pytest.raises(engine.InsufficientScoreData):
        engine.predict_nba_scoreline()


def test_live_feature_lag_matches_training_and_rejects_undated_games():
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    games = [{"id": str(hours), "date": {"start": (now - timedelta(hours=hours)).isoformat()}} for hours in (2, 23, 24, 25, 48)]
    games.append({"id": "missing", "date": {}})
    available = research.prior_games(games, "nba", now=now, before=(now + timedelta(days=3)).isoformat())
    assert [game["id"] for game in available] == ["25", "48"]


def test_legitimate_zero_goal_games_are_retained():
    result = engine.predict_soccer_scoreline([{"gf": 0, "ga": 0}] * 4, [{"gf": 0, "ga": 1}] * 4)
    assert result["data_inputs"]["form_a_games"] == 4
    assert sum(result["win_probabilities"].values()) == pytest.approx(100, abs=.15)
    assert result["projected_score"]["home"] < 1


def test_nba_exact_score_grid_does_not_skip_odd_scores_or_allow_final_ties():
    distribution = engine._nba_score_distribution(110, 110, n=20000)
    scores = {(item["home"], item["away"]): item["prob"] for item in distribution}
    assert (110, 111) in scores and (111, 110) in scores
    assert all(home != away for home, away in scores)
    assert max(scores.values()) < .5  # Prior two-point bins overstated exact probabilities.


def test_integer_nba_totals_include_push_probability():
    line = engine._nba_ou_probs(220, 15, [220])[0]
    assert line["push_prob"] > 0
    assert line["over_prob"] == line["under_prob"]
    assert line["over_prob"] + line["under_prob"] + line["push_prob"] == pytest.approx(100, abs=.15)


def test_nba_form_uses_team_identity_across_string_and_integer_ids():
    games = [{"id": "one", "date": {"start": "2026-10-10T00:00Z"}, "status": {"state": "post"},
              "teams": {"home": {"id": 1}, "visitors": {"id": 2}},
              "scores": {"home": {"points": 125}, "visitors": {"points": 99}}}]
    first = nba_predictor.extract_recent_form(games, "1")
    second = nba_predictor.extract_recent_form(games, 2)
    assert first[0]["our_pts"] == 125
    assert second[0]["our_pts"] == 99
    assert nba_predictor.extract_recent_form(games, 3) == []


def _nba_games(count, *, season_type=2):
    start = datetime.now(UTC) - timedelta(days=3)
    return [{"id": f"g{i}", "season_type": season_type, "date": {"start": (start - timedelta(days=2 * i)).isoformat()},
             "status": {"state": "post", "completed": True},
             "teams": {"home": {"id": "1"}, "visitors": {"id": "2"}},
             "scores": {"home": {"points": 110}, "visitors": {"points": 100}}} for i in range(count)]


def test_optional_nba_feed_failure_does_not_discard_scored_games(monkeypatch):
    def failed(*_):
        raise RuntimeError("Player feed down")
    monkeypatch.setattr(nba_routes.nc, "get_team_recent_form_context", lambda *a, **kw: {"current_games": _nba_games(10)})
    monkeypatch.setattr(nba_routes.nc, "get_team_season_games", lambda *a, **kw: [])
    monkeypatch.setattr(nba_routes, "_nba_player_pools", failed)
    response = module.app.test_client().get("/api/scoreline/nba?team_a_id=1&team_b_id=2")
    assert response.status_code == 200
    coverage = response.json["result"]["data_completeness"]["factors"]
    assert coverage["rosters"] is False and coverage["current_season"] is True


def test_nba_preseason_games_never_feed_the_scoreline(monkeypatch):
    monkeypatch.setattr(nba_routes.nc, "get_team_recent_form_context", lambda *a, **kw: {"current_games": _nba_games(6, season_type=1)})
    monkeypatch.setattr(nba_routes.nc, "get_team_season_games", lambda *a, **kw: [])
    monkeypatch.setattr(nba_routes, "_nba_player_pools", lambda *a: (None, None))
    response = module.app.test_client().get("/api/scoreline/nba?team_a_id=1&team_b_id=2")
    assert response.status_code == 422
    assert "this season or last" in response.json["message"]


def test_synthetic_training_inference_parity_integrity_and_time_boundaries(tmp_path, monkeypatch):
    model_path, report_path = tmp_path / "model.json", tmp_path / "report.json"
    monkeypatch.setattr(research, "artifact_paths", lambda _: (model_path, report_path))
    rows = []
    for index in range(300):
        kickoff = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=index)
        features = {name: (index % 11) / 10 for name in research.ROLLING_FEATURES}
        rows.append({**features, "target": index % 3, "date": kickoff.date().isoformat(), "kickoff": kickoff.isoformat(),
                     "home_score": 100 + index % 11, "away_score": 95 + index % 11})
    report = training.fit("nba", rows, [], [])
    assert report["windows"]["train"]["end"] < report["windows"]["calibration"]["start"] < report["windows"]["test"]["start"]
    model = json.loads(model_path.read_text())
    result = research.project("nba", rows[-1])
    assert result["available"] is True
    values = np.array([rows[-1][name] for name in model["feature_names"]])
    standardized = (values - np.array(model["center"])) / np.array(model["scale"])
    expected = model["targets"][0]["intercept"] + np.dot(model["targets"][0]["coef"], standardized)
    assert result["projected_score"]["home"] == pytest.approx(expected)
    assert research.project("nba", rows[-1], before="2019-01-01T00:00:00Z")["available"] is False
    assert research.project("nba", rows[-1], now=datetime.now(UTC) + timedelta(days=31))["available"] is False
    model_path.write_text(model_path.read_text() + " ")
    assert research.project("nba", rows[-1])["available"] is False


def test_researched_projection_is_not_modified_by_unsupported_injury_or_market_adjustments():
    forecast = {"available": True, "model": "test", "notice": "Experimental", "projected_score": {"home": 110, "away": 100},
                "sigma_home": 10, "sigma_away": 10, "sigma_total": 15}
    form = [{"our_pts": 100, "their_pts": 110}] * 4
    result = engine.predict_nba_scoreline(form_a=form, form_b=form, injuries_a=[{}] * 8, odds_total=300, research_projection=forecast)
    assert result["projected_score"] == {"home": 110, "away": 100, "total": 210}
    assert result["data_inputs"]["odds_anchored"] is False


def test_totals_line_does_not_fabricate_a_market_probability_or_edge():
    form = [{"gf": 1, "ga": 1}] * 4
    result = engine.predict_soccer_scoreline(form, form, odds_total=2.5)
    assert result["market_comparison"] is None
