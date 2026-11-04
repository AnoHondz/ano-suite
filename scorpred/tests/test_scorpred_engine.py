"""
Focused business-logic tests for the ScorPred prediction engine.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import scorpred_engine as se


def _fallback_components(**overrides):
    components = se._fallback_team_components()
    components.update(overrides)
    return components


def test_build_key_edges_orders_largest_advantages_and_filters_small_gaps():
    comp_a = {
        "form": 8.0,
        "offense": 6.4,
        "defense": 4.0,
        "h2h": 5.0,
        "home_away": 7.1,
        "squad": 5.1,
        "opp_strength": 4.9,
        "match_context": 6.2,
    }
    comp_b = {
        "form": 5.1,
        "offense": 6.2,
        "defense": 7.0,
        "h2h": 5.0,
        "home_away": 6.0,
        "squad": 5.0,
        "opp_strength": 5.1,
        "match_context": 6.1,
    }

    edges = se._build_key_edges(comp_a, comp_b, "Arsenal", "Chelsea")

    assert [edge["category"] for edge in edges] == [
        "Defensive Strength",
        "Recent Form",
        "Venue Advantage",
    ]
    assert all(edge["margin"] >= 0.3 for edge in edges)
    assert all(edge["category"] != "Attacking Power" for edge in edges)


def test_build_matchup_reading_explains_conflicts_and_caution_flags():
    comp_a = {
        "form": 8.0,
        "offense": 7.2,
        "defense": 4.1,
        "h2h": 3.0,
        "home_away": 6.5,
        "squad": 5.0,
        "opp_strength": 3.5,
        "match_context": 6.3,
    }
    comp_b = {
        "form": 5.6,
        "offense": 5.5,
        "defense": 7.3,
        "h2h": 8.1,
        "home_away": 5.0,
        "squad": 5.0,
        "opp_strength": 7.6,
        "match_context": 4.1,
    }

    reading = se._build_matchup_reading(comp_a, comp_b, "Arsenal", "Chelsea", 6.8, 6.1, "soccer")

    assert "Arsenal hold the clearest overall edge" in reading
    assert "Current form favours Arsenal, but H2H history leans Chelsea" in reading
    assert "Arsenal's recent results came against weaker opposition" in reading
    assert "Chelsea have been tested by quality opponents recently" in reading
    assert "Arsenal have had extra recovery time" in reading
    assert "Chelsea may be fatigued from short rest" in reading
    assert "Arsenal carry the stronger attacking threat right now" in reading
    assert "Chelsea are the tighter defensive unit" in reading


@pytest.mark.parametrize(
    ("form_a", "form_b", "h2h", "injuries_a", "injuries_b", "opp_strengths", "expected"),
    [
        ([{}, {}, {}], [{}, {}, {}], [{}, {}, {}], [{}], [{}], {"arsenal": 7.4}, "Strong"),
        ([{}, {}, {}], [{}, {}, {}], [], [], [], {"arsenal": 7.4}, "Moderate"),
        ([{}], [], [], [], [], {}, "Limited"),
    ],
)
def test_assess_data_quality_thresholds(
    form_a,
    form_b,
    h2h,
    injuries_a,
    injuries_b,
    opp_strengths,
    expected,
):
    assert se._assess_data_quality(form_a, form_b, h2h, injuries_a, injuries_b, opp_strengths) == expected


def test_scorpred_predict_returns_safe_fallback_when_team_score_fails(monkeypatch):
    calls = {"count": 0}

    def fake_calculate(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("form payload exploded")
        return 6.6, _fallback_components(
            form=7.4,
            offense=6.8,
            defense=6.2,
            h2h=5.8,
            home_away=5.9,
            squad=5.4,
            opp_strength=6.6,
            match_context=5.7,
        )

    monkeypatch.setattr(se, "calculate_team_score", fake_calculate)
    monkeypatch.setattr(
        se,
        "summarize_prediction_history",
        lambda: {"total_tracked": 4, "total_verified": 3, "accuracy": 66.7, "confidence": {}},
    )

    prediction = se.scorpred_predict(
        form_a=[{"result": "W", "gf": 2, "ga": 0}],
        form_b=[{"result": "W", "gf": 3, "ga": 1}],
        h2h_form_a=[],
        h2h_form_b=[],
        injuries_a=[],
        injuries_b=[],
        team_a_is_home=True,
        team_a_name="Arsenal",
        team_b_name="Chelsea",
        sport="soccer",
        opp_strengths={"arsenal": 7.1, "chelsea": 6.7},
    )

    assert prediction["team_a_score"] == 5.0
    assert prediction["team_b_score"] == 6.6
    assert prediction["winner_label"] == "Chelsea Win"
    assert prediction["components_a"]["form"] == 5.0
    assert prediction["data_quality"] == "Limited"
    assert prediction["debug_info"]["data_quality"] == "degraded"
    assert prediction["debug_info"]["fallbacks_used"]
    assert "Team A score calculation failed" in prediction["debug_info"]["fallbacks_used"][0]


def test_scorpred_predict_close_soccer_match_picks_edge_winner(monkeypatch):
    """Close-score soccer fixtures should now pick the team with the slight edge,
    not default to Draw every time (gap=0.2 → Arsenal has a narrow lead)."""
    results = iter(
        [
            (5.2, _fallback_components(form=5.2, offense=5.3, defense=5.1)),
            (5.0, _fallback_components(form=5.0, offense=5.1, defense=5.4)),
        ]
    )

    monkeypatch.setattr(se, "calculate_team_score", lambda *args, **kwargs: next(results))
    monkeypatch.setattr(
        se,
        "summarize_prediction_history",
        lambda: {"total_tracked": 0, "total_verified": 0, "accuracy": None, "confidence": {}},
    )
    monkeypatch.setattr(se, "_optional_picks", lambda *args, **kwargs: [])

    prediction = se.scorpred_predict(
        form_a=[{"result": "D", "gf": 1, "ga": 1}] * 3,
        form_b=[{"result": "D", "gf": 1, "ga": 1}] * 3,
        h2h_form_a=[{"result": "D"}] * 2,
        h2h_form_b=[{"result": "D"}] * 2,
        injuries_a=[],
        injuries_b=[],
        team_a_is_home=True,
        team_a_name="Arsenal",
        team_b_name="Chelsea",
        sport="soccer",
        opp_strengths={"arsenal": 6.5, "chelsea": 6.4},
    )

    # gap=0.2, which is >= 0.06 → engine picks the team with the slight edge
    assert prediction["winner_label"] == "Arsenal Win"
    assert prediction["best_pick"]["team"] == "A"
    assert prediction["confidence"] == "Low"
    assert prediction["score_gap"] == 0.2


# ── Top Lean & Decision Status ──────────────────────────────────────────────

class TestComputeTopLean:
    def test_home_lean_when_home_highest(self):
        lean = se.compute_top_lean(48.0, 22.0, 30.0, "Arsenal", "Chelsea")
        assert lean["outcome"] == "Home"
        assert lean["label"] == "Arsenal"
        assert lean["prob"] == 48.0

    def test_away_lean_when_away_highest(self):
        lean = se.compute_top_lean(30.0, 22.0, 48.0, "Arsenal", "Chelsea")
        assert lean["outcome"] == "Away"
        assert lean["label"] == "Chelsea"

    def test_draw_allowed_when_strict_conditions_met(self):
        # draw highest (35%), abs(home-away) = |30-32| = 2 <= 6, draw >= 34
        lean = se.compute_top_lean(30.0, 35.0, 32.0, "Arsenal", "Chelsea")
        assert lean["outcome"] == "Draw"

    def test_draw_rejected_when_below_34(self):
        # draw is highest at 33% but < 34 threshold
        lean = se.compute_top_lean(32.0, 33.0, 32.0, "Arsenal", "Chelsea")
        assert lean["outcome"] != "Draw"

    def test_draw_rejected_when_home_away_gap_too_large(self):
        # draw highest but abs(home-away) > 6
        lean = se.compute_top_lean(20.0, 36.0, 30.0, "Arsenal", "Chelsea")
        assert lean["outcome"] != "Draw"
        assert lean["outcome"] == "Away"


class TestComputeDecisionStatus:
    def test_strong_lean(self):
        assert se.compute_decision_status(58.0, 20.0, 22.0) == "Strong Lean"

    def test_lean(self):
        assert se.compute_decision_status(48.0, 22.0, 30.0) == "Lean"

    def test_no_edge_when_top_below_42(self):
        assert se.compute_decision_status(35.0, 33.0, 32.0) == "No Edge"

    def test_too_close_when_gap_under_4(self):
        assert se.compute_decision_status(44.0, 22.0, 42.0) == "Too Close"

    def test_no_edge_borderline(self):
        # top=43, gap=43-39=4 → not < 4 but top < 55 and gap < 5 → No Edge
        assert se.compute_decision_status(43.0, 18.0, 39.0) == "No Edge"


class TestDecisionLayerInPrediction:
    def test_scorpred_predict_includes_decision_fields(self, monkeypatch):
        results = iter([
            (6.0, _fallback_components(form=6.0)),
            (4.0, _fallback_components(form=4.0)),
        ])
        monkeypatch.setattr(se, "calculate_team_score", lambda *a, **kw: next(results))
        monkeypatch.setattr(
            se, "summarize_prediction_history",
            lambda: {"total_tracked": 0, "total_verified": 0, "accuracy": None, "confidence": {}},
        )
        monkeypatch.setattr(se, "_optional_picks", lambda *a, **kw: [])

        pred = se.scorpred_predict(
            [], [], [], [], [], [],
            team_a_is_home=True,
            team_a_name="Liverpool",
            team_b_name="Everton",
            sport="soccer",
        )
        assert "top_lean" in pred
        assert "decision_status" in pred
        assert pred["top_lean"]["label"] == "Liverpool"
        assert pred["top_lean"]["outcome"] == "Home"
        assert pred["decision_status"] in ("Strong Lean", "Lean", "Too Close", "No Edge")
