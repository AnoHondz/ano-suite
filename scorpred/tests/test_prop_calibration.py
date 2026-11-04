import pytest
from services import prop_calibration as calibration, prop_model


def market():
    return {"market": "shots", "line": 1.5, "side": "over", "probability": .8,
            "record": .6, "ladder": {"start": 0, "over": [.95, .8, .4, .1]}}


def test_rejected_or_missing_calibration_preserves_existing_distribution(monkeypatch):
    row = market()
    monkeypatch.setattr(calibration, "parameters", lambda *args: None)
    assert calibration.apply(row, "soccer") is row


def test_accepted_calibration_is_monotone_and_reprices_both_sides(monkeypatch):
    row = market()
    monkeypatch.setattr(calibration, "parameters", lambda *args: {"slope": .7, "intercept": -.5})
    result = calibration.apply(row, "soccer")
    ladder = result["ladder"]["over"]
    assert all(0 < p < 1 for p in ladder)
    assert all(a >= b for a, b in zip(ladder, ladder[1:]))
    over = ladder[1]
    assert result["probability"] == pytest.approx(over if result["side"] == "over" else 1 - over, abs=.0001)
    assert row["ladder"]["over"] == [.95, .8, .4, .1]


def test_selected_picks_keep_their_conservative_selection_adjustment():
    row = {**market(), "calibration": "player-props-calibration-v1"}
    picks = prop_model.best_picks([{"name": "Player", "team": "A", "position": "M", "markets": [row]}],
                                  {"shots": .5}, .66)
    # Fitting all market probabilities does not separately validate the most
    # extreme five selections, so keep the established selection adjustment.
    assert picks[0]["chance"] == .698


def test_fpl_missing_previous_season_stat_is_not_treated_as_zero():
    from services import fpl_props
    history = [{"minutes": 90, "tackles": 3}, {"minutes": 90, "tackles": 3}]
    past = {"minutes": 3000}  # This source never reported tackles.
    assert fpl_props.per_90(history, past, "tackles") == 3


def test_four_game_tackle_estimates_are_not_ranked_as_strong_picks():
    from services import fpl_props
    row = {"market": "tackles", "line": 2.5, "side": "under", "probability": .9,
           "record": .5, "games": 4}
    player = {"name": "Current Player", "team": "A", "position": "MID", "markets": [row]}
    assert fpl_props.best_picks([player]) == []
    row["games"] = 8
    assert len(fpl_props.best_picks([player])) == 1
