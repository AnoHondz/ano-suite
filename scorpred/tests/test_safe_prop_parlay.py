from datetime import UTC, datetime, timedelta

from services import safe_prop_parlay as spp

NOW = datetime(2026, 11, 2, 17, 0, tzinfo=UTC)


def _payload(outcomes, book="fanduel", market="player_points_alternate", age=30):
    return {"bookmakers": [{"key": book, "title": book.title(), "markets": [
        {"key": market, "last_update": (NOW - timedelta(seconds=age)).isoformat(), "outcomes": outcomes}]}]}


def _pick(name="Jalen Brunson", market="points"):
    # P(over k+0.5) for k = 15..25: the model's ladder; chance == probability means no shrink.
    over = [.97, .95, .92, .88, .83, .76, .68, .59, .5, .41, .33]
    return {"name": name, "team": "Knicks", "market": market, "side": "over", "line": 23.5, "probability": .6, "chance": .6,
            "ladder": {"start": 15, "over": over}}


def test_alt_quotes_keep_over_only_lines_and_drop_stale_or_whole_lines():
    outcomes = [{"name": "Over", "description": "Jalen Brunson", "point": 19.5, "price": 1.25},
                {"name": "Over", "description": "Jalen Brunson", "point": 20, "price": 1.3}]  # whole line can push
    quotes = spp.alt_quotes(_payload(outcomes), NOW.isoformat(), NOW)
    assert [(q["line"], q["side"], q["price"]) for q in quotes] == [(19.5, "over", 1.25)]
    assert spp.alt_quotes(_payload(outcomes, age=3600), NOW.isoformat(), NOW) == []


def test_safe_legs_need_high_chance_a_short_fair_price_and_keep_the_best_price():
    quotes = spp.alt_quotes(_payload([
        {"name": "Over", "description": "Jalen Brunson", "point": 17.5, "price": 1.08},   # too short a price
        {"name": "Over", "description": "Jalen Brunson", "point": 19.5, "price": 1.22},   # 83%: safe, fair
        {"name": "Over", "description": "Jalen Brunson", "point": 20.5, "price": 1.33},   # 76%: safe, fair, better price
        {"name": "Over", "description": "Jalen Brunson", "point": 22.5, "price": 1.55},   # 59%: not safe
    ]), NOW.isoformat(), NOW)
    legs = spp.safe_legs([_pick()], quotes, {"id": "g1", "matchup": "A @ B", "kickoff": "2026-11-03T00:00:00Z"})
    assert len(legs) == 1 and legs[0]["line"] == 20.5 and legs[0]["chance"] == .76
    assert legs[0]["selection"] == "Jalen Brunson Over 20.5 Points"
    unfair = spp.alt_quotes(_payload([{"name": "Over", "description": "Jalen Brunson", "point": 20.5, "price": 1.2}]), NOW.isoformat(), NOW)
    assert spp.safe_legs([_pick()], unfair, {"id": "g1", "matchup": "", "kickoff": ""}) == []  # 76% x 1.20 < 1


def test_parlay_stacks_one_safe_leg_per_game_until_it_pays_2x():
    def leg(game, chance, price):
        return {"game_id": game, "chance": chance, "price": price, "selection": f"{game}-{chance}"}
    legs = [leg("g1", .85, 1.25), leg("g1", .80, 1.35), leg("g2", .82, 1.30), leg("g3", .78, 1.33)]
    parlay = spp.best_parlay(legs)
    assert parlay["decimal_odds"] >= 2 and len({l["game_id"] for l in parlay["legs"]}) == len(parlay["legs"]) == 3
    assert abs(parlay["hit_prob"] - .85 * .82 * .78) < 1e-4
    assert spp.best_parlay([leg("g1", .9, 1.2), leg("g1", .9, 1.9)]) is None  # same game only
