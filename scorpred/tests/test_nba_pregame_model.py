"""NBA pregame ratings: venue-neutral margins, shrinkage, rest, leakage guards and pricing."""
from datetime import UTC, datetime, timedelta

import pytest

import app as app_module
import nba_routes
from services import nba_pregame_model as model
from services.data_freshness import metadata

TIP = datetime(2026, 1, 15, 0, 30, tzinfo=UTC)


def game(home, away, home_pts, away_pts, days_before, *, season_type=2, state="post", game_id=None, tip=TIP):
    start = tip - timedelta(days=days_before)
    return {"id": game_id or f"{home}-{away}-{days_before}", "season_type": season_type,
            "date": {"start": start.isoformat()}, "status": {"state": state, "completed": state == "post"},
            "teams": {"home": {"id": home}, "visitors": {"id": away}},
            "scores": {"home": {"points": home_pts}, "visitors": {"points": away_pts}}}


def schedule(team, margin, count, *, start_day=2, home_every=2, tip=TIP):
    rows = []
    for index in range(count):
        opponent = str(100 + index)
        diff = margin or (2 if index % 2 == 0 else -2)
        if index % home_every == 0:
            rows.append(game(team, opponent, 112 + diff, 112, start_day + index * 2, tip=tip))
        else:
            rows.append(game(opponent, team, 112, 112 + diff, start_day + index * 2, tip=tip))
    return rows


def test_home_court_is_removed_from_a_teams_own_margin():
    only_home_wins = [game("1", str(100 + i), 114, 112, 2 + i * 2) for i in range(30)]
    rating = model.team_rating(model.team_games(only_home_wins, "1", TIP))
    assert rating["raw_margin"] == 2
    assert abs(rating["rating"]) < 0.01


def test_small_samples_are_shrunk_toward_average():
    early = model.team_rating(model.team_games(schedule("1", 10, 3), "1", TIP))
    late = model.team_rating(model.team_games(schedule("1", 10, 40), "1", TIP))
    assert 0 < early["rating"] < 4
    assert 7.5 < late["rating"] < 10
    assert early["uncertainty"] > late["uncertainty"]


def test_blowouts_are_capped():
    rating = model.team_rating(model.team_games(schedule("1", 60, 40), "1", TIP))
    assert rating["rating"] <= model.MARGIN_CAP


def test_games_at_or_after_tipoff_preseason_and_unfinished_games_are_ignored():
    rows = [game("1", "2", 150, 100, 0), game("1", "3", 150, 100, -1), game("1", "4", 150, 100, 5, season_type=1),
            game("1", "5", 150, 100, 6, state="pre"), game("1", "6", 110, 100, 7), game("1", "6", 110, 100, 7)]
    assert [row["scored"] - row["allowed"] for row in model.team_games(rows, "1", TIP)] == [10]


def test_back_to_back_costs_the_tired_team():
    rested = model.project(schedule("1", 0, 20, start_day=2), schedule("2", 0, 20, start_day=2), "1", "2", TIP)
    tired = model.project(schedule("1", 0, 20, start_day=1), schedule("2", 0, 20, start_day=2), "1", "2", TIP)
    assert tired["home"]["back_to_back"] and not rested["home"]["back_to_back"]
    assert tired["margin"] == pytest.approx(rested["margin"] - model.BACK_TO_BACK)


def test_better_team_is_favored_and_scores_agree_with_margin():
    result = model.project(schedule("1", 8, 30), schedule("2", -4, 30), "1", "2", TIP)
    assert result["home_probability"] > 0.75
    assert result["home_score"] - result["away_score"] == pytest.approx(result["margin"])
    neutral = model.project(schedule("1", 8, 30), schedule("2", -4, 30), "1", "2", TIP, neutral=True)
    assert neutral["margin"] == pytest.approx(result["margin"] - model.HOME_COURT)


def test_espn_moneyline_is_devigged_and_legacy_spread_is_signed():
    current = model.market_from_odds({"provider": {"displayName": "DraftKings"}, "spread": -2.5, "overUnder": 234.5,
                                      "moneyline": {"home": {"close": {"odds": "-142"}}, "away": {"close": {"odds": "+120"}}}})
    assert current["source"] == "moneyline"
    assert current["home_probability"] == pytest.approx((1 / 1.704225) / (1 / 1.704225 + 1 / 2.2), abs=1e-4)
    assert current["total"] == 234.5
    legacy = model.market_from_odds({"spread": 6.5, "homeTeamOdds": {"favorite": False}, "awayTeamOdds": {"favorite": True}})
    assert legacy["source"] == "spread" and legacy["home_spread"] == 6.5 and legacy["home_probability"] < 0.5
    assert model.market_from_odds({}) is None and model.market_from_odds({"details": "OFF"}) is None


def test_no_price_is_never_a_bet_and_edges_need_samples():
    strong = model.project(schedule("1", 10, 30), schedule("2", -6, 30), "1", "2", TIP)
    assert model.decide(strong, None, "Home", "Away")["action"] == "CONSIDER"
    fair = {"bookmaker": "Book", "home_probability": strong["home_probability"]}
    assert model.decide(strong, fair, "Home", "Away")["action"] == "SKIP"
    soft = {"bookmaker": "Book", "home_probability": strong["home_probability"] - 0.08}
    picked = model.decide(strong, soft, "Home", "Away")
    assert picked["action"] == "BET" and picked["side_home"] and picked["edge"] == pytest.approx(0.08)
    early = model.project(schedule("1", 10, 5), schedule("2", -6, 5), "1", "2", TIP)
    assert model.decide(early, {"bookmaker": "Book", "home_probability": 0.2}, "Home", "Away")["action"] == "SKIP"


def test_underdog_value_is_the_recommended_side():
    close = model.project(schedule("1", 1, 30), schedule("2", 0, 30), "1", "2", TIP)
    market = {"bookmaker": "Book", "home_probability": close["home_probability"] + 0.06}
    decision = model.decide(close, market, "Home", "Away")
    assert decision["action"] == "BET" and not decision["side_home"]
    assert "Value on Away" in decision["reason"]


def test_dashboard_card_uses_season_ratings_and_fresh_market(monkeypatch):
    tip = datetime.now(UTC) + timedelta(hours=6)
    games = {"1": schedule("1", 9, 30, tip=tip), "2": schedule("2", -5, 30, tip=tip)}
    fresh = metadata("ESPN", datetime.now(UTC).isoformat(), 300, 900)
    monkeypatch.setattr(nba_routes.nc, "get_team_recent_form_context",
                        lambda team_id, *a, **kw: {"current_games": games[team_id], "data_freshness": fresh})
    monkeypatch.setattr(nba_routes.nc, "get_team_season_games", lambda *a, **kw: [])
    monkeypatch.setattr(nba_routes, "_nba_player_pools", lambda *a: (None, None))
    fixture = {"id": "g1", "date": {"start": tip.isoformat()}, "status": {"state": "pre", "short": "STATUS_SCHEDULED"},
               "data_freshness": fresh,
               "teams": {"home": {"id": "1", "name": "Boston Celtics"}, "visitors": {"id": "2", "name": "Miami Heat"}},
               "odds": {"provider": {"displayName": "DraftKings"}, "overUnder": 221.5,
                        "moneyline": {"home": {"close": {"odds": "-150"}}, "away": {"close": {"odds": "+130"}}}}}
    with app_module.app.app_context():
        prediction = nba_routes._build_upcoming_prediction_card(fixture, {}, {})["prediction"]
    card = prediction["decision_card"]
    assert card["action"] == "BET" and card["recommended_side"] == "Boston Celtics"
    assert card["market"]["bookmaker"] == "DraftKings"
    assert "DraftKings implies" in card["reason"] and "Fair line Boston Celtics -" in card["reason"]
    assert prediction["best_pick"]["prediction"] == "Boston Celtics Win"
    assert prediction["totals_line"] == 221.5
    assert card["probabilities"]["a"] + card["probabilities"]["b"] == pytest.approx(100)


def test_dashboard_card_waits_for_three_games(monkeypatch):
    tip = datetime.now(UTC) + timedelta(hours=6)
    fresh = metadata("ESPN", datetime.now(UTC).isoformat(), 300, 900)
    monkeypatch.setattr(nba_routes.nc, "get_team_recent_form_context", lambda *a, **kw: {"current_games": [], "data_freshness": fresh})
    monkeypatch.setattr(nba_routes.nc, "get_team_season_games", lambda *a, **kw: [])
    monkeypatch.setattr(nba_routes, "_nba_player_pools", lambda *a: (None, None))
    fixture = {"id": "g1", "date": {"start": tip.isoformat()}, "status": {"state": "pre"}, "data_freshness": fresh,
               "teams": {"home": {"id": "1", "name": "Boston Celtics"}, "visitors": {"id": "2", "name": "Miami Heat"}}}
    with app_module.app.app_context():
        result = nba_routes._build_upcoming_prediction_card(fixture, {}, {})
    assert result["prediction"] is None
    assert "3 completed games per team this season or last" in result["unavailable_reason"]


OPENER = datetime(2026, 10, 24, 0, 0, tzinfo=UTC)
LAST_SEASON = OPENER - timedelta(days=200)


def test_last_season_counts_at_a_discount_from_opening_night():
    strong = schedule("1", 8, 40, tip=LAST_SEASON)
    rows = model.team_games(strong, "1", OPENER)
    rating = model.team_rating(rows, season=model.season_of(OPENER))
    assert rating["games"] == 0 and rating["previous_games"] == 40 and rating["record"] == "0-0"
    decay = sum(0.5 ** (i / model.HALF_LIFE_GAMES) for i in range(40))
    assert rating["sample"] == pytest.approx(decay * model.PREVIOUS_SEASON_WEIGHT)
    assert 0 < rating["rating"] < model.team_rating(rows)["rating"]
    result = model.project(strong, schedule("2", -4, 40, tip=LAST_SEASON), "1", "2", OPENER)
    assert result["min_games"] == 0 and result["min_sample"] >= model.MIN_GAMES
    assert result["home_probability"] > 0.6


def test_two_seasons_ago_is_ignored():
    assert model.team_games(schedule("1", 8, 30, tip=OPENER - timedelta(days=560)), "1", OPENER) == []


def line(player_id, team, gp, minutes, pts, *, teams=None):
    return {"id": player_id, "name": f"P{player_id}", "team_ids": teams or [team], "gp": gp, "min": minutes, "pts": pts,
            "fgm": pts * 0.37, "fga": pts * 0.8, "ftm": pts * 0.15, "fta": pts * 0.19, "reb": 6, "ast": 5,
            "stl": 1, "blk": 0.5, "tov": 2.5, "pf": 2}


def lineup(status="", *, roster=True, star_team="1"):
    season = model.season_of(TIP)
    lines = {season: [line("star", star_team, 30, 35, 27), line("bench", "1", 30, 20, 8)], season - 1: []}
    players = ([{"id": "star", "name": "Star", "status": status}] if roster else []) + [{"id": "bench", "name": "Bench", "status": ""}]
    return model.player_pool("1", players, lines)


def test_injured_star_costs_their_value_and_a_healthy_regular_costs_nothing():
    home, away = schedule("1", 3, 30), schedule("2", 0, 30)
    base = model.project(home, away, "1", "2", TIP)
    healthy = model.project(home, away, "1", "2", TIP, home_players=lineup())
    out = model.project(home, away, "1", "2", TIP, home_players=lineup("Out"))
    questionable = model.project(home, away, "1", "2", TIP, home_players=lineup("Questionable"))
    star = next(row for row in out["availability"]["home"]["players"] if row["id"] == "star")
    assert 3 < star["value"] <= model.PLAYER_VALUE_CAP
    assert healthy["margin"] == pytest.approx(base["margin"])
    assert out["margin"] == pytest.approx(base["margin"] - star["value"])
    assert out["margin"] < questionable["margin"] < healthy["margin"]
    assert "without Star (Out, 27.0 ppg)" in model.decide(out, None, "Home", "Away")["reason"]


def test_roster_moves_are_priced_both_ways():
    home, away = schedule("1", 3, 30), schedule("2", 0, 30)
    base = model.project(home, away, "1", "2", TIP)
    arrived = model.project(home, away, "1", "2", TIP, home_players=lineup(star_team="9"))
    departed = model.project(home, away, "1", "2", TIP, home_players=lineup(roster=False))
    assert arrived["margin"] > base["margin"] + 3
    assert departed["margin"] < base["margin"] - 3
    assert departed["availability"]["home"]["players"][0]["status"] == "Left team"
    assert "adds Star" in model.decide(arrived, None, "Home", "Away")["reason"]
    assert "lost Pstar" in model.decide(departed, None, "Home", "Away")["reason"]


def test_traded_players_split_their_games_and_statuses_map_to_chances():
    pool = model.player_pool("1", [], {2026: [line("x", "1", 40, 30, 15, teams=["1", "5"])]})
    assert pool[0]["seasons"][2026]["team_share"] == 0.5 and not pool[0]["on_roster"]
    statuses = ("Out", "Doubtful", "Questionable", "Day-To-Day", "", None)
    assert [model.status_availability(status) for status in statuses] == [0, 0.25, 0.5, 0.6, 1, 1]


def test_head_to_head_is_reported_but_not_weighted():
    meetings = [game("1", "2", 120, 100, 9, game_id="m1"), game("2", "1", 99, 110, 19, game_id="m2")]
    home, away = schedule("1", 2, 30), schedule("2", 0, 30)
    plain = model.project(home + meetings, away + meetings, "1", "2", TIP)
    assert plain["head_to_head"]["record"] == "2-0" and plain["head_to_head"]["average_margin"] == 15.5
    assert "Home 2-0 vs Away" in model.decide(plain, None, "Home", "Away")["reason"]


def test_no_bet_before_enough_current_season_games():
    result = model.project(schedule("1", 10, 40, tip=LAST_SEASON), schedule("2", -6, 40, tip=LAST_SEASON), "1", "2", OPENER)
    soft = {"bookmaker": "Book", "home_probability": result["home_probability"] - 0.1}
    assert model.decide(result, soft, "Home", "Away")["action"] == "CONSIDER"


def test_dashboard_card_uses_last_season_and_rosters_on_opening_week(monkeypatch):
    tip = datetime.now(UTC) + timedelta(hours=6)
    past = tip - timedelta(days=200)
    fresh = metadata("ESPN", datetime.now(UTC).isoformat(), 300, 900)
    season = model.season_of(tip)
    previous = {"1": schedule("1", 9, 40, tip=past), "2": schedule("2", -5, 40, tip=past)}
    monkeypatch.setattr(nba_routes.nc, "get_team_recent_form_context", lambda *a, **kw: {"current_games": [], "data_freshness": fresh})
    monkeypatch.setattr(nba_routes.nc, "get_team_season_games", lambda team_id, *a, **kw: previous[team_id])
    monkeypatch.setattr(nba_routes.nc, "get_team_roster", lambda team_id, *a, **kw: [
        {"id": f"s{team_id}", "displayName": f"Star {team_id}", "injuries": [{"status": "Out"}] if team_id == "1" else []},
        {"id": f"b{team_id}", "displayName": f"Guard {team_id}", "injuries": []}])
    monkeypatch.setattr(nba_routes.nc, "get_league_player_stats", lambda year, *a, **kw: [
        line("s1", "1", 70, 35, 28), line("b1", "1", 70, 30, 18), line("s2", "2", 70, 34, 22)] if year == season - 1 else [])
    fixture = {"id": "g1", "date": {"start": tip.isoformat()}, "status": {"state": "pre", "short": "STATUS_SCHEDULED"},
               "data_freshness": fresh,
               "teams": {"home": {"id": "1", "name": "Boston Celtics"}, "visitors": {"id": "2", "name": "Miami Heat"}}}
    with app_module.app.app_context():
        prediction = nba_routes._build_upcoming_prediction_card(fixture, {}, {})["prediction"]
    view = prediction["model"]
    assert view["home"]["games"] == 0 and view["home"]["previous_games"] == 40
    assert view["availability"]["home"]["adjustment"] < -3
    reason = prediction["decision_card"]["reason"]
    assert "Boston Celtics without Star 1 (Out, 28.0 ppg)" in reason
    assert "Key matchup Guard 1 18.0/6.0/5.0 vs Star 2 22.0/6.0/5.0" in reason


def test_huge_early_season_edges_are_treated_as_missing_information():
    result = model.project(schedule("1", 10, 40, tip=LAST_SEASON), schedule("2", -6, 40, tip=LAST_SEASON), "1", "2", OPENER)
    far = {"bookmaker": "Book", "home_probability": result["home_probability"] - 0.2}
    decision = model.decide(result, far, "Home", "Away")
    assert decision["action"] == "SKIP" and "Too far from the market this early" in decision["reason"]
