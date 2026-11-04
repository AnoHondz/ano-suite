"""Safe player-prop parlay: stack high-chance legs at real sportsbook alternate lines until it pays 2x.

A safe leg is a points / rebounds / assists line the model gives at least MIN_CHANCE, priced by
DraftKings or FanDuel between about -650 and -155, where the model's chance is at least the price's
implied chance. The parlay takes one leg per game (books price same-game legs together, for less) and
picks the combination most likely to hit that still pays PARLAY_MIN_ODDS.

Alternate lines are over-only, so they are read one side at a time rather than through
prop_markets._quotes, which needs both sides. They come from DraftKings' X+ lines on ESPN
(services.espn_props), so building costs no odds credits. alt_quotes() still reads The Odds API's shape.
"""
from __future__ import annotations

import itertools
import math
import os
from datetime import UTC, datetime, timedelta

import parlay_engine as pe
from services import prop_markets
from services.prediction_lifecycle import is_pregame, parse_kickoff

ALT_MARKETS = {"points": "player_points_alternate", "rebounds": "player_rebounds_alternate",
               "assists": "player_assists_alternate"}
LABELS = {"points": "Points", "rebounds": "Rebounds", "assists": "Assists"}
MIN_CHANCE = 0.72
MIN_PRICE, MAX_PRICE = 1.15, 1.65   # about -650 to -155
MAX_LEGS = 5
LEGS_PER_GAME = 6                   # best few per game, to keep the search small
MAX_GAMES = int(os.environ.get("SAFE_PARLAY_GAMES", "15"))  # ESPN's alternate lines are free: price the whole slate


def alt_quotes(payload: dict, fetched_at: str | None, now: datetime) -> list[dict]:
    """One-sided alternate-line prices from fresh DraftKings / FanDuel markets."""
    fetched = parse_kickoff(fetched_at)
    if not fetched or not 0 <= (now - fetched).total_seconds() < prop_markets.TTL:
        return []
    quotes = []
    for book in payload.get("bookmakers") or []:
        if not isinstance(book, dict) or book.get("key") not in prop_markets.BOOKS:
            continue
        for market in book.get("markets") or []:
            if not isinstance(market, dict) or market.get("key") not in ALT_MARKETS.values():
                continue
            updated = parse_kickoff(market.get("last_update"))
            if not updated or not 0 <= (now - updated).total_seconds() < prop_markets.QUOTE_TTL:
                continue
            for outcome in market.get("outcomes") or []:
                if not isinstance(outcome, dict) or outcome.get("name") not in ("Over", "Under"):
                    continue
                line, price = prop_markets._number(outcome.get("point")), prop_markets._number(outcome.get("price"))
                player = prop_markets._name(outcome.get("description"))
                if player and line is not None and line % 1 == 0.5 and price and price > 1:
                    quotes.append({"player": player, "market": market["key"], "line": line, "side": outcome["name"].lower(),
                                   "price": price, "book": str(book.get("title") or book["key"])[:80],
                                   "updated_at": updated.isoformat(),
                                   "expires_at": (min(fetched + timedelta(seconds=prop_markets.TTL),
                                                      updated + timedelta(seconds=prop_markets.QUOTE_TTL))).isoformat()})
    return quotes


def safe_legs(picks: list[dict], quotes: list[dict], game: dict) -> list[dict]:
    """Every safe leg for one game: model chance at the book's own line, at a short but fair price."""
    legs = {}
    for pick in picks:
        market = ALT_MARKETS.get(pick.get("market"))
        name = prop_markets._name(pick.get("name"))
        if not market or not name:
            continue
        for quote in quotes:
            if quote["player"] != name or quote["market"] != market:
                continue
            chance = prop_markets._model_at_line({**pick, "side": quote["side"]}, quote["line"])
            price = quote["price"]
            if chance is None or chance < MIN_CHANCE or not MIN_PRICE <= price <= MAX_PRICE or chance * price < 1:
                continue
            key = (name, pick["market"], quote["side"], quote["line"])
            if key not in legs or price > legs[key]["price"]:  # best price for the same bet across books
                legs[key] = {"game_id": game["id"], "matchup": game["matchup"], "kickoff": game["kickoff"],
                             "player": pick["name"], "team": pick.get("team"), "market": pick["market"],
                             "selection": f'{pick["name"]} {quote["side"].capitalize()} {quote["line"]:g} {LABELS[pick["market"]]}',
                             "side": quote["side"], "line": quote["line"], "price": price, "american": pe.american(price),
                             "book": quote["book"], "chance": chance, "ev": round(chance * price - 1, 4),
                             "expires_at": quote["expires_at"]}
    # One leg per player and stat: the line with the best price that still qualifies.
    best = {}
    for leg in legs.values():
        key = (leg["player"], leg["market"])
        if key not in best or leg["price"] > best[key]["price"]:
            best[key] = leg
    return sorted(best.values(), key=lambda leg: (-leg["chance"], -leg["price"]))[:LEGS_PER_GAME]


def best_parlay(legs: list[dict], *, min_odds: float = pe.PARLAY_MIN_ODDS, max_legs: int = MAX_LEGS) -> dict | None:
    """One leg per game; the most likely combination that pays at least min_odds (fewer legs on a tie)."""
    by_game = {}
    for leg in legs:
        by_game.setdefault(leg["game_id"], []).append(leg)
    games = list(by_game)
    best = None
    for size in range(2, min(max_legs, len(games)) + 1):
        for chosen in itertools.combinations(games, size):
            for combo in itertools.product(*(by_game[g] for g in chosen)):
                odds = math.prod(leg["price"] for leg in combo)
                if odds < min_odds:
                    continue
                hit = math.prod(leg["chance"] for leg in combo)
                if best is None or (hit, -size, odds) > (best["hit_prob"], -len(best["legs"]), best["decimal_odds"]):
                    best = {"legs": list(combo), "decimal_odds": round(odds, 2), "american": pe.american(odds),
                            "hit_prob": round(hit, 4), "ev": round(hit * odds - 1, 4), "payout_per_10": round(10 * odds, 2)}
    return best


def build_today(app, *, now: datetime | None = None, max_games: int = MAX_GAMES) -> dict:
    """Price tonight's NBA alternate lines and build the safe parlay. Spends about 3 credits a game."""
    from services import daily_digest
    now = now or datetime.now(UTC)
    day = pe.today_et().isoformat()
    api_get, props_for = daily_digest.flask_readers(app)
    slate, code = api_get("/api/dashboard/nba", {"date": day})
    cards = sorted((card for card in (slate.get("slate") or []) if code == 200
                    and is_pregame(card.get("matchDate"), card.get("eventStatus"), now=now)),
                   key=lambda card: card["matchDate"])
    from services import espn_props
    notes, legs, games = [], [], 0
    for card in cards:
        if games >= max_games:
            break
        home, away = card["teamA"], card["teamB"]
        # DraftKings' alternate (X+) lines from ESPN: free, so every game can be priced.
        feed = espn_props.quotes("nba", home, away, card["matchDate"])
        quotes = [q for q in feed["alternates"] if q["market"] in ALT_MARKETS.values()]
        if not quotes:
            notes.append(f"{away} @ {home}: alternate lines unavailable ({feed['status']}).")
            continue
        with prop_markets.prices_deferred():  # model projections only
            payload, status = props_for("nba", card, day)
        picks = (payload.get("result") or {}).get("picks") or [] if status == 200 else []
        if not picks:
            notes.append(f"{away} @ {home}: no model projections ({status}).")
            continue
        games += 1
        game = {"id": feed["event_id"], "matchup": f"{away} @ {home}", "kickoff": card["matchDate"]}
        legs.extend(safe_legs(picks, quotes, game))
    parlay = best_parlay(legs)
    if games and not parlay:
        notes.append(f"No mix of safe legs (at least {MIN_CHANCE:.0%} each) from different games reached {pe.PARLAY_MIN_ODDS:g}x.")
    return {"status": "ready" if parlay else "no_parlay", "generated_at": datetime.now(UTC).isoformat(),
            "games_priced": games, "legs": legs, "parlay": parlay, "notes": notes}
