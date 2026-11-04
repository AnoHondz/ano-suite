"""Record the last pre-kickoff price for tracked picks, so picks can be judged by closing line value.

Runs every few minutes. A pick is priced while its kickoff is inside ``WINDOW``; each run
overwrites the previous quote, so the stored line is the latest one seen before kickoff.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import model_tracker as mt
from services import nba_pregame_model as pregame

_log = logging.getLogger(__name__)

WINDOW = timedelta(minutes=25)


def _soccer_odds(pick: dict) -> dict | None:
    import odds_fetcher
    quote = odds_fetcher.fetch_match_odds("soccer", pick.get("team_a", ""), pick.get("team_b", ""),
                                          kickoff=pick.get("kickoff_at"), league_id=pick.get("league_id"))
    if not quote.get("available"):
        return None
    return {"a": quote.get("home_odds"), "b": quote.get("away_odds"), "draw": quote.get("draw_odds"),
            "bookmaker": quote.get("bookmaker")}


def _nba_odds_lookup(nba_live_client) -> Callable[[dict], dict | None]:
    games: dict[str, dict] | None = None

    def lookup(pick: dict) -> dict | None:
        nonlocal games
        if games is None:
            games = {str(game.get("id")): game for game in nba_live_client.get_upcoming_games(30, 1, "api") or []}
        market = pregame.market_from_odds((games.get(str(pick.get("fixture_id"))) or {}).get("odds"))
        if not market or not (market["home_decimal"] and market["away_decimal"]):
            return None
        return {"a": market["home_decimal"], "b": market["away_decimal"], "bookmaker": market["bookmaker"]}

    return lookup


def due(predictions: list[dict], now: datetime) -> list[dict]:
    rows = []
    for pick in predictions:
        kickoff = mt._aware_datetime(pick.get("kickoff_at"))
        if pick.get("status") != "completed" and kickoff is not None and now < kickoff <= now + WINDOW:
            rows.append(pick)
    return rows


def capture(nba_live_client=None, *, now: datetime | None = None,
            sources: dict[str, Callable[[dict], dict | None]] | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if sources is None:
        sources = {"soccer": _soccer_odds}
        if nba_live_client is not None:
            sources["nba"] = _nba_odds_lookup(nba_live_client)
    recorded = missing = 0
    for pick in due(mt._load_predictions(), now):
        source = sources.get(str(pick.get("sport", "")).lower())
        if source is None:
            continue
        try:
            odds = source(pick)
        except Exception:
            _log.warning("Closing price lookup failed for %s", pick.get("id"), exc_info=True)
            odds = None
        if odds and mt.record_closing_market(pick["id"], odds):
            recorded += 1
        else:
            missing += 1
    return {"recorded": recorded, "missing": missing}
