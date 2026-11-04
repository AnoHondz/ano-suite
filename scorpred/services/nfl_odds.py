"""Optional NFL market context. Quotes never change the unvalidated NFL model."""
import hashlib
import math
import os
import re
from datetime import UTC, datetime

import requests

from services import cache_service
from services.data_freshness import SOURCE_KEY, metadata, stamp, worst
from services.prediction_lifecycle import freshness_state, is_pregame, parse_kickoff

URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
TTL = 300


def fetch_snapshot(now=None):
    now = now or datetime.now(UTC)
    key = os.getenv("ODDS_API_KEY", "").strip()
    if os.getenv("NFL_ODDS_ENABLED", "0") != "1" or not key or key.startswith("your_"):
        return {"status": "not_configured", "events": []}
    region = os.getenv("NFL_ODDS_REGION", "us").strip().lower()
    if region not in {"us", "us2", "uk", "eu", "au"}:
        return {"status": "invalid_region", "events": []}
    cache_key = cache_service.make_key("nfl-odds-v1", region, hashlib.sha256(key.encode()).hexdigest()[:16])
    cached = cache_service.get_json(cache_key, ttl=TTL)
    if cached is not None and freshness_state(snapshot_freshness(cached, now), now) == "fresh":
        return cached
    def unavailable(status):
        result = stamp({"status": status, "events": [], "fetched_at": now.isoformat()},
                       "The Odds API NFL snapshot", now.isoformat(), 45, 45)
        cache_service.set_json(cache_key, result, ttl=45)
        return result
    from services import prop_markets
    if not prop_markets.reserve_credits(3, credential=key, now=now):  # 3 markets, 1 region
        return unavailable("budget_exhausted")
    try:
        response = requests.get(URL, params={"apiKey": key, "regions": region,
                                "markets": "h2h,spreads,totals", "oddsFormat": "decimal", "dateFormat": "iso"},
                                timeout=(3, 8))
        prop_markets.note_provider_usage((getattr(response, "headers", None) or {}).get("x-requests-used"),
                                         credential=key, now=now)
        if response.status_code in {401, 403, 429}:
            return unavailable("rate_limited" if response.status_code == 429 else "authentication_failed")
        response.raise_for_status()
        events = response.json()
        if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
            raise TypeError("Invalid odds feed")
    except (requests.RequestException, ValueError, TypeError):
        # Never log the exception: requests exceptions may contain apiKey URLs.
        return unavailable("unavailable")
    result = stamp({"status": "available", "events": events, "fetched_at": now.isoformat()},
                   "The Odds API NFL snapshot", now.isoformat(), TTL, TTL)
    cache_service.set_json(cache_key, result, ttl=TTL)
    return result


def _name(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def snapshot_freshness(snapshot, now=None):
    now = now or datetime.now(UTC)
    fetched = parse_kickoff(snapshot.get("fetched_at")) if isinstance(snapshot, dict) else None
    if fetched is not None and fetched > now:
        return {"state": "unavailable", "warning": "The odds snapshot timestamp is in the future."}
    source = snapshot.get(SOURCE_KEY) if isinstance(snapshot, dict) else None
    observed = metadata("The Odds API NFL snapshot", snapshot.get("fetched_at") if isinstance(snapshot, dict) else None,
                        TTL, TTL, now=now)
    # Both dates must be valid if an envelope is present. Preserve the shorter
    # expiry of negative-cache entries and reject inconsistent/future dates.
    return worst([source, observed], now=now) if source else observed


def match_quote(snapshot, game, now=None):
    """Match exact participants AND kickoff; keep prices from a single bookmaker."""
    now = now or datetime.now(UTC)
    if (snapshot.get("status") != "available" or freshness_state(snapshot_freshness(snapshot, now), now) != "fresh"
            or game.get("completed") or game.get("state") not in (None, "pre")
            or not is_pregame(game.get("kickoff"), game.get("status_name"), now)):
        return None
    for event in snapshot["events"]:
        if (_name(event.get("home_team")) != _name(game["home_name"])
                or _name(event.get("away_team")) != _name(game["away_name"])):
            continue
        try:
            kickoff = parse_kickoff(event["commence_time"])
            target = parse_kickoff(game["kickoff"])
            if kickoff is None or target is None or kickoff <= now or abs((kickoff - target).total_seconds()) > 300:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        books = event.get("bookmakers") or []
        if not isinstance(books, list):
            continue
        for book in books:
            try:
                updated = parse_kickoff(book["last_update"])
                if updated is None or not 0 <= (now - updated).total_seconds() < 900:
                    continue
                markets = {item["key"]: item for item in book.get("markets") or []}
                # A bookmaker timestamp does not freshen older market quotes.
                if any(item.get("last_update") is not None and
                       ((market_time := parse_kickoff(item["last_update"])) is None
                        or not 0 <= (now - market_time).total_seconds() < 900)
                       for item in markets.values()):
                    continue
                outcomes = {_name(item["name"]): _number(item.get("price")) for item in markets.get("h2h", {}).get("outcomes", [])}
                home, away = outcomes.get(_name(game["home_name"])), outcomes.get(_name(game["away_name"]))
                if home is None or away is None or min(home, away) <= 1:
                    continue
                result = {"bookmaker": str(book["title"])[:100], "updated_at": updated.isoformat(),
                          "home_decimal": home, "away_decimal": away,
                          "data_freshness": worst([snapshot_freshness(snapshot, now),
                                                   metadata("NFL bookmaker quote", updated.isoformat(), 900, 900, now=now)], now=now)}
                for outcome in markets.get("spreads", {}).get("outcomes", []):
                    if _name(outcome.get("name")) == _name(game["home_name"]):
                        point = _number(outcome.get("point"))
                        if point is not None:
                            result["home_spread"] = point
                totals = [_number(item.get("point")) for item in markets.get("totals", {}).get("outcomes", [])]
                if totals and all(value is not None and value > 0 for value in totals) and len(set(totals)) == 1:
                    result["total"] = totals[0]
                return result
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
    return None
