from datetime import UTC, datetime, timedelta
from copy import deepcopy
import numpy as np
import pytest
from services import soccer_player_model as counts, prop_markets


def history():
    return [{"date": f"2026-01-{i:02d}T15:00:00Z", "minutes": 90, "starter": True, "shots": i % 4}
            for i in range(1, 16)]


def test_features_use_prior_dates_and_do_not_assume_missing_stats_are_zero():
    rows = history()
    initial = counts.inputs(rows, "shots", "M", [10], [12], True, "2026-02-01T15:00:00Z")
    contaminated = rows + [{"date": "2026-02-01T12:00:00Z", "minutes": 90, "shots": 999}]
    assert counts.inputs(contaminated, "shots", "M", [10], [12], True, "2026-02-01T15:00:00Z") == initial
    assert len(initial["x"]) == len(counts.FEATURES)
    assert counts.inputs(rows, "fouls", "M", [], [], True, "2026-02-01T15:00:00Z") is None


def test_expected_minutes_align_the_research_reference():
    rows = [{**r, "shots": 4} for r in history()] + [
        {"date": f"2026-01-{i:02d}T15:00:00Z", "minutes": 60, "starter": True, "shots": 2} for i in range(16, 21)]
    result = counts.inputs(rows, "shots", "M", [], [], True, "2026-02-01T15:00:00Z")
    assert result["minutes"] == pytest.approx(60)
    assert result["line"] == 2.5  # Not the old four-shot starter reference.


def test_count_distribution_is_monotone_and_over_under_are_complementary():
    values = counts.survival(2, .25, np.arange(10))
    assert all(a >= b for a, b in zip(values, values[1:]))
    assert all(0 <= p <= 1 for p in values)


def selection(monkeypatch, over=.7, status="available"):
    now = datetime.now(UTC)
    market = {"market": "shots", "label": "Shots", "model": counts.VERSION, "projection": 2,
              "games": 15, "line": 2.5, "side": "under", "probability": .7,
              "ladder": {"start": 0, "over": [.9, over, .3, .1]}}
    result = {"selection_policy": "verified_market_only", "picks": [{"name": "Unquoted historical under"}],
              "players": [{"id": "1", "name": "Current Player", "team": "Home", "position": "M", "markets": [market]}]}
    quote = {"player": prop_markets._name("Current Player"), "market": "player_shots", "line": 1.5,
             "over_decimal": 1.91, "under_decimal": 1.91, "bookmaker": "DraftKings", "bookmaker_key": "draftkings",
             "updated_at": now.isoformat(), "expires_at": (now + timedelta(minutes=5)).isoformat()}
    monkeypatch.setattr(prop_markets, "snapshot", lambda *a: {"status": status, "quotes": [quote] if status == "available" else []})
    return result, now + timedelta(days=1)


@pytest.mark.parametrize("over,side", [(.7, "over"), (.3, "under")])
def test_actual_quotes_can_select_either_side_regardless_of_historical_selection(monkeypatch, over, side):
    result, kickoff = selection(monkeypatch, over)
    original = deepcopy(result)
    response = prop_markets.compare(result, "soccer", "Home", "Away", kickoff, 39)
    assert response["picks"][0]["side"] == side
    assert response["picks"][0]["line"] == 1.5
    assert response["picks"][0]["line_source"] == "sportsbook"
    assert response["picks"][0]["expected_value"] > 0
    assert result == original


def test_absent_quotes_never_turn_historical_unders_into_ranked_picks(monkeypatch):
    result, kickoff = selection(monkeypatch, status="no_match")
    response = prop_markets.compare(result, "soccer", "Home", "Away", kickoff, 39)
    assert response["picks"] == [] and response["forecasts"]
    assert "No verified sportsbook opportunity" in response["sportsbook"]["reason"]


def test_even_prices_without_model_disagreement_produce_no_opportunity(monkeypatch):
    result, kickoff = selection(monkeypatch, over=.5)
    assert prop_markets.compare(result, "soccer", "Home", "Away", kickoff, 39)["picks"] == []


def test_verified_fixture_matching_handles_legal_club_suffixes_without_fuzzy_player_matching():
    kickoff = datetime.now(UTC) + timedelta(days=1)
    event = {"commence_time": kickoff.isoformat(), "home_team": "Liverpool", "away_team": "Brighton and Hove Albion"}
    assert prop_markets._same_game(event, "Liverpool FC", "Brighton & Hove Albion FC", kickoff)
    assert not prop_markets._same_game(event, "Brighton & Hove Albion FC", "Liverpool FC", kickoff)
    assert not prop_markets._same_game(event, "Everton FC", "Brighton & Hove Albion FC", kickoff)


def test_training_quarantines_unknown_ids_and_conflicting_duplicates():
    from scripts.train_soccer_player_counts import checked_game
    player = {"id": "1", "minutes": 90, "shots": 2}
    game = {"teams": {"A": {"players": [player, dict(player)]}, "B": {"players": [{"id": "2", "minutes": 90}]}}}
    cleaned, repairs = checked_game(game)
    assert repairs == 1 and len(cleaned["teams"]["A"]["players"]) == 1
    game["teams"]["A"]["players"][1]["shots"] = 3
    with pytest.raises(ValueError, match="Conflicting"):
        checked_game(game)
    game["teams"]["A"]["players"] = [{"id": "0", "minutes": 1}]
    with pytest.raises(ValueError, match="Unknown"):
        checked_game(game)


@pytest.mark.parametrize("enabled", [True, False])
def test_only_released_models_replace_forecasts_and_history_is_unchanged(monkeypatch, enabled):
    from tests.test_box_props import game
    games = [game(i, 20, 15, minutes=90) for i in range(12)]
    for row in games:
        for side in row["teams"].values():
            for player in side["players"]: player["shots"] = 2
    original = deepcopy(games)
    model = {"enabled": enabled, "mean": [0] * len(counts.FEATURES), "scale": [1] * len(counts.FEATURES),
             "coef": [0] * len(counts.FEATURES), "intercept": np.log(2), "alpha": .2}
    monkeypatch.setattr(counts, "artifact", lambda: {"markets": {"shots": model}})
    players = [{"id": "a1", "name": "Current", "team": "A", "position": "M", "markets": [
        {"market": "shots", "label": "Shots", "projection": 9}]}]
    result = counts.apply_to_players(players, games, "A", "B", datetime(2026, 3, 1, tzinfo=UTC))
    assert result[0]["markets"][0]["projection"] == (2 if enabled else 9)
    assert games == original
