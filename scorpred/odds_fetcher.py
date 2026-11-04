"""
odds_fetcher.py — Fetch live odds from The Odds API and compute edge vs model.

Usage:
    from odds_fetcher import fetch_match_odds, compute_edge

The Odds API docs: https://the-odds-api.com/liveapi/guides/v4/
Requires environment variable: ODDS_API_KEY
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from datetime import UTC, datetime
from services.prediction_lifecycle import is_pregame, parse_kickoff

import requests

logger = logging.getLogger(__name__)

_ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
_CACHE_TTL = 300
_QUOTE_TTL = 900
_MATCH_ODDS_MONTHLY_CREDITS = int(os.environ.get("MATCH_ODDS_MONTHLY_CREDITS", "100"))
_CACHE_DIR = Path(os.environ.get("SCORPRED_DATA_ROOT", "")) / "cache" / "odds" if os.environ.get("SCORPRED_DATA_ROOT") else Path("cache") / "odds"

# Sport keys used by The Odds API
_SPORT_KEY_MAP = {
    "soccer": "soccer_epl",      # English Premier League
    "nba": "basketball_nba",
}
# Soccer feeds are per league; anything unlisted falls back to the default soccer key above.
_SOCCER_LEAGUE_KEYS = {
    39: "soccer_epl",
    78: "soccer_germany_bundesliga",
    140: "soccer_spain_la_liga",
}


def _cache_path(cache_key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in cache_key)
    return _CACHE_DIR / f"{safe}.json"


def _load_cached(cache_key: str) -> dict | None:
    path = _cache_path(cache_key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if 0 <= time.time() - data.get("_cached_at", 0) < _CACHE_TTL:
            return data.get("payload")
    except Exception:
        pass
    return None


def _save_cached(cache_key: str, payload: Any) -> None:
    try:
        _cache_path(cache_key).write_text(
            json.dumps({"_cached_at": time.time(), "payload": payload})
        )
    except Exception:
        pass


def fetch_match_odds(
    sport: str,
    team_a: str,
    team_b: str,
    *,
    markets: str = "h2h",
    regions: str = "uk,eu,us",
    kickoff: str | None = None,
    league_id: int | None = None,
) -> dict[str, Any]:
    """Fetch a fresh, complete bookmaker quote for a specific upcoming match.

    Returns:
        {
            "available": bool,
            "home_odds": float | None,    # decimal odds for team_a (home)
            "away_odds": float | None,    # decimal odds for team_b (away)
            "draw_odds": float | None,    # decimal odds for draw (soccer only)
            "bookmaker": str | None,      # all returned prices belong to this book
            "source": "live" | "cache" | "unavailable",
        }
    """
    if kickoff and not is_pregame(kickoff):
        return _unavailable("Pregame odds are unavailable after kickoff.")
    if not _ODDS_API_KEY:
        return _unavailable("No ODDS_API_KEY set")

    sport_key = _SPORT_KEY_MAP.get(sport.lower())
    if sport.lower() == "soccer" and league_id is not None:
        sport_key = _SOCCER_LEAGUE_KEYS.get(int(league_id), sport_key)
    if not sport_key:
        return _unavailable(f"Unsupported sport: {sport}")

    cache_key = f"{sport_key}_{team_a}_{team_b}_{kickoff}_{markets}_{regions}"
    cached = _load_cached(cache_key)
    if cached is not None and _current(cached, kickoff):
        cached["source"] = "cache"
        return cached

    games = _league_games(sport_key, markets, regions)
    if games is None:
        return _unavailable("The odds provider could not refresh prices.")

    result = _find_match(games, team_a, team_b, kickoff=kickoff)
    _save_cached(cache_key, result)
    result["source"] = "live"
    return result


_LEAGUE_GAMES: dict[tuple[str, str, str], tuple[float, list]] = {}
_LEAGUE_TTL = 120


def _league_games(sport_key: str, markets: str, regions: str) -> list | None:
    """One request prices a whole league, so matches looked up together share it (the API bills per request)."""
    key = (sport_key, markets, regions)
    cached = _LEAGUE_GAMES.get(key)
    if cached and 0 <= time.time() - cached[0] < _LEAGUE_TTL:
        return cached[1]
    # Tracking, closing lines and match pages called this every few minutes outside the monthly
    # cap (400 of the 500 free credits by 2026-11-02). It now spends from the shared allowance.
    from services import prop_markets
    cost = len(markets.split(",")) * len(regions.split(","))
    if not prop_markets.reserve_credits(cost, credential=_ODDS_API_KEY, share="match_odds",
                                        share_cap=_MATCH_ODDS_MONTHLY_CREDITS):
        logger.info("odds_fetcher: monthly odds-credit allowance reached; no request sent")
        return None
    try:
        url = f"{_ODDS_API_BASE}/sports/{sport_key}/odds"
        params = {
            "apiKey": _ODDS_API_KEY,
            "regions": regions,
            "markets": markets,
            "oddsFormat": "decimal",
        }
        resp = requests.get(url, params=params, timeout=6)
        prop_markets.note_provider_usage((getattr(resp, "headers", None) or {}).get("x-requests-used"),
                                         credential=_ODDS_API_KEY)
        resp.raise_for_status()
        games = resp.json()
    except Exception as exc:
        logger.warning("odds_fetcher: API call failed (%s)", type(exc).__name__)
        return None
    if not isinstance(games, list):
        return None
    _LEAGUE_GAMES[key] = (time.time(), games)
    return games


def _current(result, kickoff=None):
    now = datetime.now(UTC)
    event_time = parse_kickoff(result.get("commence_time"))
    updated = parse_kickoff(result.get("updated_at"))
    expected = parse_kickoff(kickoff) if kickoff else None
    return bool(result.get("available") and event_time and is_pregame(event_time, now=now)
                and updated and 0 <= (now - updated).total_seconds() <= _QUOTE_TTL
                and (not expected or abs((event_time - expected).total_seconds()) <= 300))


def _find_match(games: list[dict], team_a: str, team_b: str, *, kickoff=None) -> dict[str, Any]:
    """Match participants and kickoff; preserve a complete single-book quote."""
    a_norm = _norm(team_a)
    b_norm = _norm(team_b)

    for game in games:
        home = _norm(game.get("home_team", ""))
        away = _norm(game.get("away_team", ""))
        if not (_teams_match(a_norm, home) and _teams_match(b_norm, away)):
            continue
        event_time = parse_kickoff(game.get("commence_time"))
        expected = parse_kickoff(kickoff) if kickoff else None
        if not event_time or not is_pregame(event_time) or (expected and abs((event_time - expected).total_seconds()) > 300):
            continue

        for bm in game.get("bookmakers", []):
            bm_name = bm.get("title", "")
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                updated = market.get("last_update") or bm.get("last_update")
                quote_time = parse_kickoff(updated)
                if not quote_time or not 0 <= (datetime.now(UTC) - quote_time).total_seconds() <= _QUOTE_TTL:
                    continue
                best: dict[str, float] = {}
                for outcome in market.get("outcomes", []):
                    name = _norm(outcome.get("name", ""))
                    try:
                        price = float(outcome.get("price", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    if not 1.0 < price < float("inf"):
                        continue
                    if name == home and price > best.get("home", 0):
                        best["home"] = price
                    elif name == away and price > best.get("away", 0):
                        best["away"] = price
                    elif "draw" in name and price > best.get("draw", 0):
                        best["draw"] = price

                if best.get("home") and best.get("away") and ("draw" in best or not game.get("sport_key", "").startswith("soccer")):
                    return {"available": True, "home_odds": best.get("home"), "away_odds": best.get("away"),
                            "draw_odds": best.get("draw"), "bookmaker": bm_name, "updated_at": updated,
                            "commence_time": event_time.isoformat()}

    return _unavailable("Match not found in current odds feed")


def compute_edge(
    model_prob: float,
    market_odds: float | None,
) -> dict[str, Any]:
    """Compute expected value edge.

    Args:
        model_prob: Model's probability for the outcome (0.0–1.0 or 0–100).
        market_odds: Best available decimal odds from bookmaker.

    Returns:
        {
            "edge_pct": float,        # positive = positive EV
            "ev_per_unit": float,     # expected profit per 1-unit stake
            "implied_prob": float,    # market's implied probability
            "has_edge": bool,
            "label": str,             # "Value Bet" | "Fair" | "Avoid"
        }
    """
    if market_odds is None or market_odds <= 1.0:
        return {"edge_pct": None, "ev_per_unit": None, "implied_prob": None, "has_edge": False, "label": "No odds"}

    # Normalise model_prob to 0-1 range
    if model_prob > 1.0:
        model_prob = model_prob / 100.0
    model_prob = max(0.0, min(1.0, model_prob))

    implied_prob = 1.0 / market_odds
    edge_pct = round((model_prob - implied_prob) * 100, 1)
    ev_per_unit = round(model_prob * (market_odds - 1) - (1 - model_prob), 3)

    if edge_pct >= 3.0:
        label = "Value Bet"
        has_edge = True
    elif edge_pct >= -2.0:
        label = "Fair"
        has_edge = False
    else:
        label = "Avoid"
        has_edge = False

    return {
        "edge_pct": edge_pct,
        "ev_per_unit": ev_per_unit,
        "implied_prob": round(implied_prob * 100, 1),
        "has_edge": has_edge,
        "label": label,
    }


def _unavailable(reason: str = "") -> dict[str, Any]:
    return {
        "available": False,
        "home_odds": None,
        "away_odds": None,
        "draw_odds": None,
        "bookmaker": None,
        "source": "unavailable",
        "reason": reason,
    }


def _norm(s: str) -> str:
    return s.lower().strip()


def _teams_match(query: str, candidate: str) -> bool:
    """Fuzzy team name matching — handles 'Man City' vs 'Manchester City'."""
    if not query or not candidate:
        return False
    if query == candidate:
        return True
    # Substring match (one direction is enough for common abbreviations)
    q_words = set(query.split())
    c_words = set(candidate.split())
    overlap = q_words & c_words
    return len(overlap) >= max(1, len(q_words) - 1)
