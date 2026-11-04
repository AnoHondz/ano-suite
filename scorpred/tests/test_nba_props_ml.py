from datetime import UTC, datetime, timedelta

import pytest

from services import nba_props_ml as ml


def game(index, *, out=(), spread=-4.0, total=228.0):
    date = (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=2 * index)).isoformat()
    def player(pid, position, points, minutes):
        return {"id": pid, "name": pid, "position": position, "starter": True, "minutes": minutes, "points": points,
                "rebounds": 5.0, "assists": 4.0, "threes": 2.0, "fga": points / 1.2, "fg3a": 6.0, "fta": 3.0,
                "oreb": 1.0, "dreb": 4.0, "turnovers": 2.0}
    return {"id": str(index), "date": date, "season": 2026, "season_type": 2, "total": total,
            "teams": {"A": {"home": True, "opponent": "B", "spread": spread, "out": list(out),
                            "players": [player("star", "G", 30.0, 36.0), player("wing", "F", 12.0, 30.0)]},
                      "B": {"home": False, "opponent": "A", "spread": -spread, "out": [],
                            "players": [player("big", "C", 18.0, 32.0)]}}}


def test_features_use_only_earlier_games_and_count_teammates_out():
    history = ml.History()
    for index in range(4):
        history.update(game(index))
    upcoming = game(4, out=["star"])
    features = history.features("wing", "A", upcoming)
    assert features["points_ewm3"] == pytest.approx(12.0)
    assert features["games_season"] == 4 and features["days_rest"] == pytest.approx(2.0)
    assert features["out_minutes"] == pytest.approx(36.0) and features["out_count"] == 1
    assert features["team_total"] == pytest.approx(228 / 2 + 2) and features["home"] == 1.0
    assert set(features) == set(ml.FEATURES)
    assert history.features("newcomer", "A", upcoming) is None


def test_apply_without_promoted_model_leaves_rule_markets(monkeypatch):
    monkeypatch.setattr(ml, "promoted", lambda: {})
    result = {"players": [{"id": "wing", "team": "A", "markets": [{"market": "points", "line": 11.5, "side": "over",
                                                                     "probability": 0.6, "record": 0.55}]}]}
    assert ml.apply(result, "A", "B", 2026, datetime.now(UTC)) == result
    assert "model" not in result["players"][0]["markets"][0]


def test_over_probability_matches_negative_binomial_mean():
    assert ml.over_probability(20.0, 0.05, 19.5) == pytest.approx(float(ml.distribution(20.0, 0.05).sf(19)))
    assert 0.4 < ml.over_probability(20.0, 0.05, 19.5) < 0.6
