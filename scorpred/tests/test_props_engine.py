"""
Focused business-logic tests for the prop generation engine.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import props_engine as pe


def _nba_record(points, rebounds=0, assists=0, minutes="32:00", home_id=1, away_id=2):
    return {
        "statistics": [
            {
                "points": points,
                "rebounds": rebounds,
                "assists": assists,
                "min": minutes,
            }
        ],
        "game": {
            "teams": {
                "home": {"id": home_id},
                "visitors": {"id": away_id},
            }
        },
    }


def test_base_projection_redistributes_missing_weights():
    projection = pe._base_projection(
        season_avg=20.0,
        last5_avg=None,
        last10_avg=18.0,
        vs_opp_avg=None,
        active_ha_avg=22.0,
    )

    assert projection == 19.8


def test_build_variance_reports_consistency_and_hit_rates():
    samples = {
        "full_season_log": [
            _nba_record(10),
            _nba_record(30),
            _nba_record(5),
            _nba_record(35),
            _nba_record(40),
        ]
    }

    variance = pe._build_variance(samples, "points", 20.0, "nba")

    assert variance["sample_games"] == 5
    assert variance["hit_count"] == 3
    assert variance["ceiling"] == 40.0
    assert variance["floor"] == 5.0
    assert variance["consistency_label"] == "very high variance"


def test_build_prop_card_returns_placeholder_when_no_recent_data():
    card = pe._build_prop_card(
        sport="nba",
        market_key="points",
        samples={"player_id": 23, "full_season_log": [], "last5": [], "last10": []},
        is_home=True,
        opponent_stats=None,
        player_name="LeBron James",
        opponent_name="Boston Celtics",
    )

    assert card["error"] == "Insufficient recent data"
    assert card["projection"]["lean"] == "N/A"
    assert card["confidence"]["score"] == 0


def test_build_bet_slip_sorts_best_pick_and_flags_weak_legs():
    slip = pe._build_bet_slip(
        [
            {
                "player_name": "LeBron James",
                "market_label": "Points",
                "projection": {"lean": "OVER", "suggested_line": 27.5},
                "confidence": {"score": 82, "label": "Strong"},
            },
            {
                "player_name": "Jayson Tatum",
                "market_label": "Rebounds",
                "projection": {"lean": "UNDER", "suggested_line": 8.5},
                "confidence": {"score": 46, "label": "Lean"},
            },
            {
                "player_name": "Anthony Davis",
                "market_label": "Blocks",
                "projection": {"lean": "PUSH", "suggested_line": 2.5},
                "confidence": {"score": 65, "label": "Good"},
            },
        ]
    )

    assert [pick["player"] for pick in slip["picks"]] == ["LeBron James", "Jayson Tatum"]
    assert slip["best_single"].startswith("LeBron James")
    assert slip["parlay_risk"] == "HIGH"
    assert slip["slip_confidence"] == 64.0


def test_build_prop_card_probability_market_uses_game_log():
    card = pe._build_prop_card(
        sport="nba",
        market_key="double_double",
        samples={
            "full_season_log": [
                _nba_record(24, rebounds=12, assists=4),
                _nba_record(18, rebounds=8, assists=7),
                _nba_record(12, rebounds=11, assists=10),
            ]
        },
        is_home=True,
        opponent_stats=None,
        player_name="Nikola Jokic",
        opponent_name="Lakers",
    )

    assert card["type"] == "probability"
    assert card["probability"]["probability"] == 66.7
    assert card["confidence"]["score"] == 45


def test_generate_props_keeps_partial_results_and_reports_bad_markets(monkeypatch):
    samples = {
        "sport": "nba",
        "player_id": 23,
        "player_team_id": 1,
        "opponent_team_id": 2,
        "season": 2026,
        "full_season_log": [_nba_record(28, rebounds=8, assists=7)],
        "last5": [_nba_record(28, rebounds=8, assists=7)],
        "last10": [_nba_record(28, rebounds=8, assists=7)],
        "vs_opponent": {"games": 1, "records": [_nba_record(28, rebounds=8, assists=7)]},
        "team_injuries": [],
    }

    monkeypatch.setattr(pe, "_collect_nba_samples", lambda *args, **kwargs: samples)
    monkeypatch.setattr(pe, "_fetch_nba_opponent_stats", lambda *args, **kwargs: {"opp_ppg": 113, "net_rtg": 0})

    def fake_build_prop_card(**kwargs):
        if kwargs["market_key"] == "points":
            return {
                "player_name": kwargs["player_name"],
                "market_label": "Points",
                "projection": {"lean": "OVER", "suggested_line": 27.5},
                "confidence": {"score": 78, "label": "Strong"},
            }
        raise RuntimeError("downstream calc exploded")

    monkeypatch.setattr(pe, "_build_prop_card", fake_build_prop_card)

    payload = pe.generate_props(
        sport="nba",
        player_id=23,
        player_name="LeBron James",
        player_team_id=1,
        opponent_team_id=2,
        opponent_name="Boston Celtics",
        is_home=True,
        markets=["points", "bogus_market", "rebounds"],
        season=2026,
    )

    assert [card["market_label"] for card in payload["props"]] == ["Points"]
    assert payload["bet_slip"]["best_single"].startswith("LeBron James")
    assert any("Unknown market 'bogus_market'" in error for error in payload["errors"])
    assert any("Failed to build prop for rebounds: downstream calc exploded" == error for error in payload["errors"])
