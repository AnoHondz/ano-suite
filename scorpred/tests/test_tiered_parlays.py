from services import tiered_parlays as tp


def test_hit_record_and_smoothing():
    assert tp.hit_record([10, 8, 12, 7], 8.5, "over") == (2, 4)
    assert tp.hit_record([10, 8, 12, 7], 8.5, "under") == (2, 4)
    assert tp.smoothed(10, 10) == 11 / 12  # 10 for 10 is not treated as certain
    assert tp.leg_chance(0.8, None) == 0.8 and tp.leg_chance(0.8, 0.6) == 0.7 and tp.leg_chance(None, None) is None


def test_a_perfect_small_record_is_pulled_toward_the_book_price():
    # 15/15 at a -556 line (book 85%, about 81% once its margin is removed): the blend lands between record and book.
    chance = tp.with_market(15, 15, 1.18)
    assert 0.86 < chance < 0.89
    assert abs(tp.with_market(0, 0, 2.0, other=2.0) - 0.5) < 1e-9  # no games: the book's fair chance


def test_the_book_prior_has_its_margin_removed():
    # -110 both ways: 52.4% each raw, 50% fair. The prior must be the fair 50%, not 52.4%.
    assert abs(tp.with_market(0, 0, 1.909, other=1.909) - 0.5) < 1e-9
    assert tp.with_market(0, 0, 1.909) < 1 / 1.909  # one side priced: a typical margin is still taken off


def test_minutes_that_swing_make_the_book_count_for_more():
    assert tp.minutes_weight([34, 35, 33, 34, 36]) < tp.minutes_weight([12, 34, 20, 38, 8])
    assert tp.minutes_weight([30]) == tp.MARKET_WEIGHT  # too few games to judge


def test_ladder_bets_are_separate_two_x_bets_from_different_games():
    legs = [{"player": f"p{i}", "game_id": f"g{i // 2}", "price": price, "chance": chance, "selection": f"leg {i}"}
            for i, (price, chance) in enumerate([(2.0, .56), (1.4, .78), (1.45, .76), (1.42, .75), (2.1, .52), (1.9, .55)])]
    bets = tp.ladder_bets(legs)
    assert bets and all(1.85 <= b["decimal_odds"] <= 2.3 for b in bets)
    games = [leg["game_id"] for b in bets for leg in b["legs"]]
    assert len(games) == len(set(games))                                     # no game used twice across the 3 bets


def test_soccer_game_legs_btts_goals_and_team_totals_are_backed_by_recent_scores():
    event = {"id": "9", "date": "2026-11-03T20:00Z", "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"displayName": "Barcelona"}}, {"homeAway": "away", "team": {"displayName": "Aston Villa"}}],
        "odds": [{"provider": {"name": "DraftKings"}, "total": {"over": {"close": {"line": "o2.5", "odds": "-150"}},
                                                               "under": {"close": {"line": "u2.5", "odds": "+120"}}}}]}]}
    price = lambda d: {"current": {"over": {"decimal": d}}}
    team = lambda line, d: {"type": {"name": "Team Total Goals"}, "team": {"$ref": "x/teams/83?lang=en"},
                            "current": {"over": {"decimal": d}, "target": {"value": line}}}
    items = [{"type": {"name": "Both Teams To Score"}, **price(1.6)}, {"type": {"name": "Both Teams To Score"}, **price(2.2)},
             team(1.5, 1.4), team(2.5, 2.3), team(1.5, 2.8), team(2.5, 1.6)]
    recent = {"83": [(3, 1)] * 12 + [(1, 0)] * 3, "362": [(1, 2)] * 10}
    home, away = {"id": "83", "displayName": "Barcelona"}, {"id": "362", "displayName": "Aston Villa"}
    legs = {l["selection"]: l for l in tp._game_legs(event, "uefa.champions", items, home, away, recent, "Villa @ Barca")}
    assert legs["Both teams to score: Yes"]["record"] == "20/20" and legs["Both teams to score: Yes"]["price"] == 1.6
    assert legs["Over 2.5 goals"]["record"] == "20/20" and legs["Under 2.5 goals"]["record"] == "0/20"
    # Barcelona's own 12/15 is the record; Villa's defence enters once, through the goal model (not as 10 more games)
    over = legs["Barcelona over 1.5 goals"]
    assert over["record"] == "12/15" and over["price"] == 1.4 and over["model"] is not None
    assert over["fair"] < 1 / 1.4 and over["evidence"] > 15
    assert "Barcelona under 2.5 goals" not in legs  # Barcelona's own record (3/15) does not back the under
    assert legs["Barcelona over 1.5 goals"]["why"][0] == ("Barcelona scored 2+ goals in 12 of their last 15 games, and "
                                                         "Aston Villa conceded 2+ goals in 10 of their last 10.")
    assert "Aston Villa over 1.5 goals" not in legs  # no team-total prices posted for Villa here
    # a strong attack against a tight defence: Villa now concede nothing, so Barcelona over 2.5 is no longer a lock
    tight = {**recent, "362": [(1, 0)] * 10}
    legs = {l["selection"]: l for l in tp._game_legs(event, "uefa.champions", items, home, away, tight, "Villa @ Barca")}
    assert "Barcelona over 2.5 goals" not in legs   # Barcelona's record backs it (12/15) but Villa's defence does not (0/10)


def _leg(player, game, chance, price):
    return {"player": player, "game_id": game, "chance": chance, "price": price, "selection": player}


def test_each_tier_pays_inside_its_band_from_value_legs_one_per_game():
    legs = [_leg("A", "g1", .85, 1.25), _leg("B", "g2", .84, 1.27), _leg("C", "g3", .80, 1.30), _leg("D", "g1", .62, 1.75),
            _leg("E", "g2", .58, 1.85), _leg("F", "g3", .55, 1.95), _leg("G", "g4", .52, 2.05), _leg("H", "g4", .70, 1.2)]
    tiers = tp.build_tiers(legs)
    for tier, (low, high) in tp.TIERS.items():
        parlay = tiers[tier]
        if tier == "five":
            assert parlay is None  # only four games on this board
            continue
        assert parlay and low <= parlay["decimal_odds"] <= high, tier
        assert len({leg["game_id"] for leg in parlay["legs"]}) == len(parlay["legs"])  # one leg per game
        assert parlay["tag"] == "+EV"
    assert tiers["low"]["decimal_odds"] < 2 and sorted(l["player"] for l in tiers["low"]["legs"]) == ["A", "B"]
    assert all(l["player"] != "H" for t in tiers.values() if t for l in t["legs"])  # -500 is below the price floor


def test_a_tier_is_still_filled_when_no_value_slip_exists_and_says_so():
    legs = [_leg("A", "g1", .70, 1.30), _leg("B", "g1", .80, 1.30), _leg("C", "g1", .80, 1.30), _leg("D", "g2", .5, 1.3)]
    tiers = tp.build_tiers(legs)  # B and C are value but share a game; the only second game's leg (D) is not value
    low = tiers["low"]
    assert low and low["tag"] == "Best available"
    assert len({l["game_id"] for l in low["legs"]}) == 2 and "D" in [l["player"] for l in low["legs"]]
    assert tiers["medium"] is None and tiers["fly"] is None  # no legs long enough for these bands


def test_slips_are_ranked_by_value_not_by_hit_chance():
    # Two near-certain short legs (more likely, poor value) against two fairly priced value legs in the same band.
    legs = [_leg("A", "g1", .80, 1.25), _leg("B", "g2", .80, 1.25), _leg("C", "g3", .62, 1.75), _leg("D", "g4", .50, 2.25)]
    medium = tp.build_tiers(legs)["medium"]
    assert sorted(l["player"] for l in medium["legs"]) == ["C", "D"] and medium["tag"] == "+EV"


def test_the_five_leg_parlay_takes_five_games():
    legs = [_leg(f"P{i}", f"g{i}", .62, 1.75) for i in range(6)]
    five = tp.build_tiers(legs)["five"]
    assert five and len(five["legs"]) == 5 and len({l["game_id"] for l in five["legs"]}) == 5


def test_goal_model_combines_attack_and_defence_once():
    strong_vs_leaky = tp.expected_goals([3] * 15, [3] * 15, True)
    strong_vs_tight = tp.expected_goals([3] * 15, [0] * 15, True)
    assert strong_vs_leaky > strong_vs_tight
    assert tp.expected_goals([2] * 5, [2] * 15, True) is None  # too few games
    assert abs(tp.goal_chance("btts", "yes", None, 1.5, 1.5) + tp.goal_chance("btts", "no", None, 1.5, 1.5) - 1) < 1e-9
    assert tp.goal_chance("total", "over", 2.5, 1.5, 1.5) > tp.goal_chance("total", "over", 2.5, 0.8, 0.8)
