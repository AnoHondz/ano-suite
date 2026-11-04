"""Regression checks for historical feature semantics, not forecast accuracy."""

from datetime import UTC, datetime, timedelta

import pytest

import ml_service
import scormastermind as mastermind
from scripts import train_scoring_models as training
from services import scoring_research as research
from services.soccer_feature_policy import (
    enrich_opponent_snapshots,
    opponent_average_ppg,
)
from train_model import _rolling_stats


def fixture(game):
    return {"fixture": {"id": game["game_id"], "date": game["kickoff"], "status": {"short": "FT"}},
            "league": {"season": game["season"]},
            "teams": {"home": {"id": 1, "name": "Home"}, "away": {"id": 2, "name": "Away"}},
            "goals": {"home": game["home_score"], "away": game["away_score"]}}


def game(index, when, *, season=2026, home_score=2, away_score=1):
    return {"game_id": str(index), "season": season, "kickoff": when.isoformat(), "date": when.date().isoformat(),
            "home_team": "1", "away_team": "2", "home_score": home_score, "away_score": away_score, "neutral": False}


def test_live_soccer_and_offline_training_use_identical_season_window_and_lag(monkeypatch):
    now = datetime(2026, 10, 17, 12, tzinfo=UTC)
    target = now - timedelta(days=3)
    history = [game(i, target - timedelta(days=50 - i * 5), home_score=i % 3, away_score=i % 2) for i in range(8)]
    # Neither a recently completed game nor a previous-season game may enter.
    history += [game("lagged", target - timedelta(hours=23), home_score=30),
                game("previous-season", target - timedelta(days=2), season=2025, home_score=40)]
    target_game = game("target", target, home_score=9, away_score=8)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(training, "datetime", Clock)
    row = next(row for row in training.rolling_rows(history + [target_game], "soccer") if row["game_id"] == "target")
    raw = [fixture(item) for item in history + [target_game]]
    live_home = research.soccer_scoring_form(raw, 1, season=2026, before=target.isoformat(), now=now)
    live_away = research.soccer_scoring_form(raw[::-1], "2", season="2026", before=target.isoformat(), now=now)
    live_features = research.rolling_features(live_home, live_away)
    assert len(live_home) == len(live_away) == 6
    assert {name: row[name] for name in research.ROLLING_FEATURES} == live_features
    assert {entry["fixture_id"] for entry in live_home} == {str(i) for i in range(2, 8)}
    # The target's final score is a label, never one of its own pregame inputs.
    raw[-1]["goals"] = {"home": 70, "away": 0}
    assert research.soccer_scoring_form(raw, 1, season=2026, before=target.isoformat(), now=now) == live_home


def test_soccer_insufficient_current_season_does_not_borrow_previous_season():
    now = datetime(2026, 10, 17, tzinfo=UTC)
    raw = [fixture(game(i, now - timedelta(days=i + 2), season=2026 if i < 2 else 2025)) for i in range(8)]
    selected = research.soccer_scoring_form(raw, 1, season=2026, now=now)
    assert len(selected) == 2
    with pytest.raises(ValueError, match="three completed"):
        research.rolling_features(selected, selected)


def test_future_target_uses_current_availability_and_rejects_ambiguous_metadata():
    now = datetime(2026, 10, 17, 12, tzinfo=UTC)
    rows = [{"kickoff": (now - timedelta(hours=hours)).isoformat(), "season": 2026, "gf": 0, "ga": 0}
            for hours in (2, 23, 24, 25, 48)]
    rows += [{"kickoff": "2026-10-01", "season": 2026, "gf": 1, "ga": 1},
             {"kickoff": "2026-10-01T00:00:00Z", "gf": 1, "ga": 1},
             {"kickoff": "2026-10-01T00:00:00Z", "season": 2025, "gf": 1, "ga": 1}]
    selected = research.select_prior_form(rows, season=2026, before=now + timedelta(days=4), now=now)
    assert [row["kickoff"] for row in selected] == [rows[3]["kickoff"], rows[4]["kickoff"]]
    assert research.select_prior_form(rows, season=None, now=now) == []
    with pytest.raises(ValueError, match="timezone"):
        research.select_prior_form(rows, season=2026, before="2026-10-17", now=now)


def test_live_opponent_feature_matches_training_own_history_aggregation():
    history = [{"opp_ppg": value, "gf": 0, "ga": 2, "pts": 0, "result": "L"} for value in (.2, .4, .6, 1.2, 2, 3)]
    expected = _rolling_stats(history)["opp_avg_ppg"]
    live = mastermind._ml_features({"form_a": list(reversed(history)),
                                   "form_b": [{"result": "W", "gf": 5, "ga": 0, "opp_ppg": 0}] * 5})
    assert live["home_opp_avg_ppg_5"] == expected == 1.44
    assert live["away_opp_avg_ppg_5"] == 0


@pytest.mark.parametrize("form", [[], [{"result": "W"}], [{"opp_ppg": 2}, {}],
                                 [{"opp_ppg": float("nan")}], [{"opp_ppg": 4}]])
def test_missing_opponent_snapshots_stay_missing_for_artifact_imputation(form):
    assert opponent_average_ppg(form) is None
    features = mastermind._ml_features({"form_a": form, "form_b": [{"result": "W"}] * 5})
    assert features["home_opp_avg_ppg_5"] is None
    assert features["away_opp_avg_ppg_5"] is None
    assert ml_service._features_dict_to_vector(features, ["home_opp_avg_ppg_5"], {"home_opp_avg_ppg_5": 1.7}) == [1.7]


def test_opponent_enrichment_uses_only_opponents_earlier_results():
    history = [
        {"date": "2026-10-01", "home_team": "Old Opponent", "away_team": "X", "goals_scored": 2, "goals_conceded": 0},
        {"date": "2026-10-03", "home_team": "X", "away_team": "Old Opponent", "goals_scored": 1, "goals_conceded": 1},
        {"date": "2026-10-07", "home_team": "Old Opponent", "away_team": "X", "goals_scored": 0, "goals_conceded": 2},
        {"date": "2026-10-10", "home_team": "Home", "away_team": "Old Opponent", "goals_scored": 0, "goals_conceded": 9},
        {"date": "2026-10-12", "home_team": "Old Opponent", "away_team": "X", "goals_scored": 9, "goals_conceded": 0},
    ]
    # Live forms retain exact kickoffs while historical CSVs have dates only.
    form = [{"opponent": "Old Opponent", "kickoff": "2026-10-10T15:00:00Z", "gf": 0, "ga": 9, "result": "L"}]
    result = enrich_opponent_snapshots(form, history, team="Home")
    assert result[0]["opp_ppg"] == 1.3333
    assert "opp_ppg" not in form[0]  # Caller-owned records are not changed.
    history[-2]["goals_conceded"] = 0
    history[-1]["goals_scored"] = 0
    assert enrich_opponent_snapshots(form, history, team="Home") == result
    assert enrich_opponent_snapshots(form, [], team="Home")[0]["opp_ppg"] is None
