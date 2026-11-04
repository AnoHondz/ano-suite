from copy import deepcopy
from services.soccer_model_picks import attach_context, select


def player(over=.75):
    return {"id": "p", "name": "Current Player", "team": "1", "position": "F", "markets": [{
        "market": "shots", "label": "Shots", "model": "soccer-player-counts-v2", "minutes": 80, "projection": 2.5,
        "games": 15, "side": "over", "probability": over, "ladder": {"start": 0, "over": [.9, over, min(.1, over / 2)]},
        "matchup_baseline": {"probability": .7, "ladder": {"start": 0, "over": [.8, .7, .2]}},
        "matchup_context": {"team_games": 8, "opponent_games": 8, "team_per_game": 12, "opponent_allowed_per_game": 15}}]}


def test_selects_fixed_target_with_both_models_and_reports_real_matchup_evidence():
    pick = select([player()])[0]
    assert (pick["side"], pick["line"], pick["chance"]) == ("over", 1.5, .7)
    assert pick["line_source"] == "model_target" and pick["sportsbook_quotes"] == []
    assert "opponent allows 15.00" in pick["rationale"]


def test_disagreement_small_samples_and_missing_context_withhold_picks():
    assert select([player(.3)]) == []
    for key, value in (("games", 7), ("minutes", 45), ("matchup_context", {})):
        p = player(); p["markets"][0][key] = value
        assert select([p]) == []


def test_under_is_allowed_when_both_models_support_it_and_no_player_is_duplicated():
    p = player(.25)
    p["markets"][0]["matchup_baseline"]["ladder"]["over"] = [.8, .3, .1]
    p["markets"].append(deepcopy(p["markets"][0]))
    picks = select([p])
    assert len(picks) == 1 and picks[0]["side"] == "under"


def test_defense_uses_opponents_output_against_the_defending_team_not_its_attack():
    p = player()
    games = [{"date": "2026-01-01", "teams": {
        "1": {"opponent": "2", "players": [{"shots": 14}]},
        "2": {"opponent": "1", "players": [{"shots": 3}]}}}]
    attach_context([p], games, {"markets": {"shots": (("shots",), .25)}}, "1", "2")
    context = p["markets"][0]["matchup_context"]
    assert context["opponent_allowed_per_game"] == 14
    assert context["team_per_game"] == 14


def test_missing_quotes_returns_explicit_model_picks_without_claiming_market_value(monkeypatch):
    from services import prop_markets
    monkeypatch.setattr(prop_markets, "snapshot", lambda *args: {"status": "budget_exhausted", "quotes": []})
    result = prop_markets.soccer_opportunities({"players": [player()]}, "Home", "Away", None, 39)
    assert result["sportsbook"]["status"] == "model_picks"
    assert result["sportsbook"]["matched_picks"] == 0
    assert len(result["picks"]) == 1
    assert "expected_value" not in result["picks"][0]
