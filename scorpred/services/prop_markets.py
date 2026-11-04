"""Optional sportsbook comparisons, independent of prop models and pick rankings.

Uses the existing The Odds API credential, never sportsbook scraping. Only exact
participants, kickoff, full player names and documented full-game markets join.
No market quote is treated as validation of a model or a betting recommendation.
"""
import contextlib
import contextvars
import hashlib
import math
import os
import re
import sqlite3
import threading
import unicodedata
from collections import Counter
from datetime import UTC, datetime, timedelta

import requests

from services import cache_service
from services.prediction_lifecycle import is_pregame, parse_kickoff

BASE = "https://api.the-odds-api.com/v4/sports"
BOOKS = ("draftkings", "fanduel")
TTL = 300
QUOTE_TTL = 900
MONTHLY_CREDIT_CAP = 450  # Inside The Odds API free plan (500/month), set with the user 2026-11-02; never purchases quota.
_lock = threading.RLock()
# Background warm-ups set this so they never spend odds credits; only on-demand requests price props.
_prices_deferred = contextvars.ContextVar("prop_prices_deferred", default=False)


@contextlib.contextmanager
def prices_deferred():
    token = _prices_deferred.set(True)
    try:
        yield
    finally:
        _prices_deferred.reset(token)
SPORTS = {"nfl": "americanfootball_nfl", "nba": "basketball_nba"}
LEAGUES = {39: "soccer_epl", 61: "soccer_france_ligue_one", 78: "soccer_germany_bundesliga",
           135: "soccer_italy_serie_a", 140: "soccer_spain_la_liga", 253: "soccer_usa_mls"}
MARKETS = {
    "nfl": {"passing_yards": "player_pass_yds", "completions": "player_pass_completions",
            "passing_attempts": "player_pass_attempts", "passing_tds": "player_pass_tds",
            "interceptions": "player_pass_interceptions", "rushing_yards": "player_rush_yds",
            "carries": "player_rush_attempts", "receptions": "player_receptions",
            "receiving_yards": "player_reception_yds", "rush_rec_yards": "player_rush_reception_yds"},
    "nba": {"points": "player_points", "rebounds": "player_rebounds", "assists": "player_assists",
            "threes": "player_threes", "pra": "player_points_rebounds_assists"},
    "soccer": {"shots": "player_shots", "shots_on_target": "player_shots_on_target", "assists": "player_assists"},
}
MESSAGES = {
    "evidence_unavailable": "WAIT: sportsbook evidence could not be preserved for pregame research.",
    "not_configured": "Sportsbook comparison unavailable: no live player-prop odds feed is configured.",
    "disabled": "Sportsbook comparison is disabled.",
    "unsupported": "No supported sportsbook market is available for these props or this competition.",
    "unavailable": "Sportsbook prices could not be refreshed. Historical targets remain available.",
    "authentication_failed": "The sportsbook odds provider did not accept the configured credential.",
    "rate_limited": "The sportsbook odds provider's request limit was reached.",
    "budget_exhausted": "The local sportsbook comparison credit limit has been reached for this month.",
    "deferred": "Background refresh skips sportsbook lookups; prices load when props are requested.",
    "no_match": "No exact upcoming matchup was found in the sportsbook feed.",
    "schedule_conflict": "WAIT: the schedule and odds feed disagree on tipoff time for the same teams; no price was attached.",
    "no_quotes": "No fresh matching sportsbook quotes are available for these players and markets.",
    "started": "Pregame sportsbook comparisons are unavailable after kickoff.",
    "no_picks": "No supported player props are available to compare.",
}


def _name(value):
    # Full names only. No surname-only, substring, suffix-dropping or fuzzy joins.
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def reserve_credits(cost, *, credential=None, now=None, share=None, share_cap=None):
    """Reserve odds credits from the key's one monthly allowance (MONTHLY_CREDIT_CAP), shared by every
    ScorPred feature that calls The Odds API. A named share (e.g. Parlay Mode) can also have its own
    cap so it never starves the others. False, and nothing reserved, when either cap would be passed."""
    credential = os.getenv("ODDS_API_KEY", "").strip() if credential is None else credential
    if not credential:
        return False
    now = now or datetime.now(UTC)
    identity = hashlib.sha256(credential.encode()).hexdigest()[:16]
    month = now.strftime("%Y-%m")
    with _lock:
        budget_key = cache_service.make_key("prop-odds-budget-v1", identity, month)
        budget = cache_service.get_json(budget_key, ttl=32 * 86400) or {"reserved": 0}
        if budget["reserved"] + cost > MONTHLY_CREDIT_CAP:
            return False
        if share:
            share_key = cache_service.make_key("odds-budget-share-v1", identity, share, month)
            used = cache_service.get_json(share_key, ttl=32 * 86400) or {"reserved": 0}
            if share_cap is not None and used["reserved"] + cost > share_cap:
                return False
            cache_service.set_json(share_key, {"reserved": used["reserved"] + cost}, ttl=32 * 86400)
        cache_service.set_json(budget_key, {"reserved": budget["reserved"] + cost}, ttl=32 * 86400)
    return True


def note_provider_usage(used, *, credential=None, now=None):
    """Raise the local monthly count to the provider's own x-requests-used, so credits spent by any
    caller (or another copy of the app) still count against MONTHLY_CREDIT_CAP. Never lowers it."""
    used = _number(used)
    credential = os.getenv("ODDS_API_KEY", "").strip() if credential is None else credential
    if used is None or not credential:
        return
    now = now or datetime.now(UTC)
    identity = hashlib.sha256(credential.encode()).hexdigest()[:16]
    with _lock:
        budget_key = cache_service.make_key("prop-odds-budget-v1", identity, now.strftime("%Y-%m"))
        budget = cache_service.get_json(budget_key, ttl=32 * 86400) or {"reserved": 0}
        if used > budget["reserved"]:
            cache_service.set_json(budget_key, {"reserved": int(used)}, ttl=32 * 86400)


def credit_usage(*, credential=None, now=None, share=None):
    """Credits reserved this month: the shared total, or one named share's part of it."""
    credential = os.getenv("ODDS_API_KEY", "").strip() if credential is None else credential
    now = now or datetime.now(UTC)
    identity = hashlib.sha256(credential.encode()).hexdigest()[:16]
    key = (cache_service.make_key("odds-budget-share-v1", identity, share, now.strftime("%Y-%m")) if share
           else cache_service.make_key("prop-odds-budget-v1", identity, now.strftime("%Y-%m")))
    return (cache_service.get_json(key, ttl=32 * 86400) or {"reserved": 0})["reserved"]


def _fetch(sport_key, suffix, params, credential, now, cost=0, share=None, share_cap=None):
    """Single-flight cached reads; failures never log credential-bearing URLs.

    Reserve the maximum request cost before sending. Failed/empty requests also
    consume the local allowance conservatively. Provider-wide quotas still apply.
    """
    identity = hashlib.sha256(credential.encode()).hexdigest()[:16]
    key = cache_service.make_key("prop-odds-v1", identity, sport_key, suffix, sorted(params.items()))
    with _lock:
        cached = cache_service.get_json(key, ttl=TTL)
        if cached is not None:
            return cached
        if cost and _prices_deferred.get():
            return {"status": "deferred"}  # never cached: an on-demand request still pays and fetches
        if cost and not reserve_credits(cost, credential=credential, now=now, share=share, share_cap=share_cap):
            return {"status": "budget_exhausted"}
        try:
            response = requests.get(f"{BASE}/{sport_key}/{suffix}", params={**params, "apiKey": credential}, timeout=(3, 6))
            if response.status_code in {401, 403, 422, 429}:
                status = "rate_limited" if response.status_code == 429 else "authentication_failed" if response.status_code in {401, 403} else "unsupported"
                result = {"status": status}
            else:
                response.raise_for_status()
                result = {"status": "available", "payload": response.json(), "fetched_at": datetime.now(UTC).isoformat()}
            headers=getattr(response,'headers',{})
            result['quota']={field:_number(headers.get(header)) for field,header in
                             (('remaining','x-requests-remaining'),('used','x-requests-used'),('last_cost','x-requests-last'))}
            note_provider_usage(result['quota']['used'], credential=credential, now=now)
        except (requests.RequestException, ValueError, TypeError):
            result = {"status": "unavailable"}
        cache_service.set_json(key, result, ttl=TTL if result["status"] == "available" else 60)
        return result


def _same_teams(event, home, away):
    if not isinstance(event, dict):
        return False
    from utils.parsing import normalize_team_name
    # Legal club suffixes and '&' spelling differ across team providers. Player
    # names still use the strict full-name key; team matching is never fuzzy.
    # ESPN abbreviates the city ("LA Clippers") where the odds feed spells it out ("Los Angeles Clippers").
    team_key = lambda name: re.sub(r"^la ", "los angeles ", normalize_team_name(name)).replace(" ", "")
    return bool(team_key(home) and team_key(away) and team_key(home) != team_key(away)
                and team_key(event.get("home_team")) == team_key(home)
                and team_key(event.get("away_team")) == team_key(away))


def _same_game(event, home, away, kickoff, tolerance_seconds=300):
    start = parse_kickoff(event.get('commence_time')) if isinstance(event,dict) else None
    return bool(start and abs((start-kickoff).total_seconds())<=tolerance_seconds and _same_teams(event,home,away))


def _quotes(payload, market_keys, kickoff, fetched_at, now):
    quotes = []
    fetched = parse_kickoff(fetched_at)
    if not fetched or not 0 <= (now - fetched).total_seconds() < TTL:
        return quotes
    def items(value):
        return value if isinstance(value, list) else []
    for book in items(payload.get("bookmakers")):
        if not isinstance(book, dict) or book.get("key") not in BOOKS or not book.get("title"):
            continue
        for market in items(book.get("markets")):
            if not isinstance(market, dict) or not isinstance(market.get("key"), str) or market["key"] not in market_keys:
                continue
            updated = parse_kickoff(market.get("last_update"))
            if not updated or not 0 <= (now - updated).total_seconds() < QUOTE_TTL:
                continue
            pairs = {}
            for outcome in items(market.get("outcomes")):
                if not isinstance(outcome, dict) or not isinstance(outcome.get("name"), str) or outcome["name"] not in {"Over", "Under"}:
                    continue
                point, price = _number(outcome.get("point")), _number(outcome.get("price"))
                player = _name(outcome.get("description"))
                if not player or point is None or point < 0 or price is None or price <= 1:
                    continue
                pair = pairs.setdefault((player, point), {})
                side = outcome["name"].lower()
                # Conflicting duplicate outcomes are ambiguous, not a best-price opportunity.
                pair[side] = price if side not in pair else None
            for (player, point), pair in pairs.items():
                if not pair.get("over") or not pair.get("under"):
                    continue
                quotes.append({"player": player, "market": market["key"], "line": point,
                               "over_decimal": pair["over"], "under_decimal": pair["under"],
                               "bookmaker": str(book["title"])[:80], "bookmaker_key": book["key"],
                               "updated_at": updated.isoformat(), "fetched_at": fetched_at,
                               "expires_at": min(kickoff, fetched + timedelta(seconds=TTL),
                                                 updated + timedelta(seconds=QUOTE_TTL)).isoformat()})
    return quotes


def prop_source():
    """'espn' (free DraftKings props via ESPN, the default) or 'odds_api' (multi-book, uses credits)."""
    return "odds_api" if os.getenv("PROP_ODDS_SOURCE", "espn").strip().lower() == "odds_api" else "espn"


def _espn_snapshot(sport, sport_key, home, away, kickoff, market_keys, league_id=None):
    """The same snapshot contract, from ESPN's free DraftKings board: main-line pairs for the requested markets."""
    from services import espn_props
    feed = espn_props.quotes(sport, home, away, kickoff, league_id)
    if feed["status"] != "available":
        return {"status": feed["status"], "quotes": []}
    checked = datetime.now(UTC)
    quotes = [q for q in feed["quotes"] if q["market"] in market_keys and parse_kickoff(q["expires_at"]) > checked]
    if quotes:
        try:
            _preserve_quotes(sport_key, feed["event_id"], kickoff, quotes, fetched_at=feed.get("fetched_at"))
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return {"status": "evidence_unavailable", "quotes": [], "event_id": feed["event_id"]}
    return {"status": "available" if quotes else "no_quotes", "quotes": quotes, "event_id": feed["event_id"],
            "quota": None, "source": "ESPN (DraftKings)"}


def snapshot(sport, home, away, kickoff, market_keys, league_id=None):
    now = datetime.now(UTC)
    kickoff = parse_kickoff(kickoff)
    if not kickoff or not is_pregame(kickoff, now=now):
        return {"status": "started", "quotes": []}
    if os.getenv("PROP_ODDS_ENABLED", "1") != "1":
        return {"status": "disabled", "quotes": []}
    try:
        sport_key = LEAGUES.get(int(league_id or 0)) if sport == "soccer" else SPORTS.get(sport)
    except (ValueError, TypeError):
        sport_key = None
    if not sport_key or not market_keys:
        return {"status": "unsupported", "quotes": []}
    if prop_source() == "espn":
        feed = _espn_snapshot(sport, sport_key, home, away, kickoff, market_keys, league_id)
        # ESPN lists NFL prop lines without prices, so a sport ESPN cannot price falls back to The Odds API,
        # still inside the shared monthly credit cap.
        if feed["status"] == "available":
            return feed
    credential = os.getenv("ODDS_API_KEY", "").strip()
    if not credential or credential.startswith("your_"):
        return {"status": "not_configured", "quotes": []}
    events = _fetch(sport_key, "events", {}, credential, now)
    if events["status"] != "available":
        return {"status": events["status"], "quotes": []}
    games = events.get("payload")
    # NBA providers commonly distinguish scheduled start and actual tip time.
    # A bounded join still requires exact oriented teams and exactly one event.
    # Always use the EARLIER reported start for pregame eligibility and expiry.
    tolerance=900 if sport=='nba' else 300
    matches = [event for event in games if _same_game(event, home, away, kickoff,tolerance)] if isinstance(games, list) else []
    if not matches and isinstance(games,list):
        conflicts=[event for event in games if _same_teams(event,home,away)
                   and (start:=parse_kickoff(event.get('commence_time'))) is not None
                   and abs((start-kickoff).total_seconds())<=6*3600]
        if conflicts:
            return {'status':'schedule_conflict','quotes':[], 'schedule_conflict':{
                'schedule_tipoff':kickoff.isoformat(),'odds_tipoffs':[event['commence_time'] for event in conflicts],
                'feed_received_at':events.get('fetched_at')}}
    if len(matches) != 1 or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", str(matches[0].get("id") or "")):
        return {"status": "no_match", "quotes": []}
    event_id = matches[0]["id"]
    provider_start=parse_kickoff(matches[0]['commence_time'])
    effective_start=min(kickoff,provider_start)
    if not is_pregame(effective_start,now=datetime.now(UTC)):
        return {'status':'started','quotes':[]}
    result = _fetch(sport_key, f"events/{event_id}/odds",
                    {"bookmakers": ",".join(BOOKS), "markets": ",".join(sorted(market_keys)),
                     "oddsFormat": "decimal", "dateFormat": "iso"}, credential, now, cost=len(market_keys))
    payload = result.get("payload")
    checked_at = datetime.now(UTC)
    if result["status"] != "available":
        return {"status": result["status"], "quotes": [], 'quota':result.get('quota') or events.get('quota')}
    if (not is_pregame(effective_start, now=checked_at) or not _same_game(payload, home, away, kickoff,tolerance)
            or payload.get("id") != event_id):
        return {"status": "no_match", "quotes": []}
    quotes = _quotes(payload, market_keys, effective_start, result.get("fetched_at"), checked_at)
    for quote in quotes:
        quote['time_alignment']={'schedule_tipoff':kickoff.isoformat(),'provider_tipoff':provider_start.isoformat(),
            'pregame_cutoff':effective_start.isoformat(),'difference_seconds':abs((kickoff-provider_start).total_seconds()),
            'method':'Exact oriented teams, unique event, bounded NBA 15-minute / other-sport 5-minute tolerance. Earlier start controls expiry.'}
    try:
        _preserve_quotes(sport_key,event_id,effective_start,quotes,raw_payload=payload,fetched_at=result.get('fetched_at'))
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return {"status": "evidence_unavailable", "quotes": [], "event_id": event_id}
    return {"status": "available" if quotes else "no_quotes", "quotes": quotes, "event_id": event_id,'quota':result.get('quota')}


def _preserve_quotes(sport_key, event_id, kickoff, quotes, store=None, *, raw_payload=None,fetched_at=None):
    from services.prop_evidence_store import EvidenceStore
    from runtime_paths import persistent_root
    store = store or EvidenceStore(persistent_root() / 'prop-research' / 'evidence.sqlite3')
    if raw_payload is not None:
        store.append(f'{sport_key}:{event_id}','sportsbook_feed','The Odds API response receipt',
                     fetched_at,{'kickoff':kickoff.isoformat(),'payload':_json_evidence(raw_payload),
                                 'notice':'Receipt timestamp; individual market publication times remain in the payload. Incomplete pairs are preserved but cannot qualify.'},received_at=fetched_at)
    for quote in quotes:
        # A cached quote retains its actual original receipt time. Reading the
        # cache later must never make old evidence look newly collected.
        store.append(f'{sport_key}:{event_id}', 'sportsbook_quote', 'The Odds API',
                     quote['updated_at'], {'kickoff': kickoff.isoformat(), **quote},
                     received_at=quote['fetched_at'])


def _json_evidence(value):
    """Retain malformed provider numbers explicitly, never turn them into zero."""
    if isinstance(value,float) and not math.isfinite(value):return {'unusable_non_finite_number':str(value)}
    if isinstance(value,dict):return {key:_json_evidence(item) for key,item in value.items()}
    if isinstance(value,list):return [_json_evidence(item) for item in value]
    return value


def _model_at_line(pick, line):
    """Use the existing distribution and shrink, with no refit or extrapolation.

    Whole-number lines can push: omit comparisons until their settlement contract
    is supported. A truncated ladder cannot support a line outside its range.
    """
    if line % 1 != 0.5 or pick.get("side") not in {"over", "under"}:
        return None
    ladder = pick.get("ladder") or {}
    start, over = ladder.get("start"), ladder.get("over")
    if not isinstance(start, int) or isinstance(start, bool) or not isinstance(over, list):
        return None
    values = [_number(value) for value in over]
    if (not values or any(value is None or not 0 <= value <= 1 for value in values)
            or any(a < b for a, b in zip(values, values[1:]))):
        return None
    index = math.floor(line) - start
    if not 0 <= index < len(values):
        return None
    raw, shown = _number(pick.get("probability")), _number(pick.get("chance"))
    if raw is None or shown is None or not 0 <= raw <= 1 or not 0 <= shown <= 1:
        return None
    if raw == 0.5:
        if shown != 0.5:
            return None
        shrink = 1
    else:
        shrink = (shown - 0.5) / (raw - 0.5)
    if not 0 <= shrink <= 1.00001:
        return None
    probability = values[index] if pick["side"] == "over" else 1 - values[index]
    return round(0.5 + min(shrink, 1) * (probability - 0.5), 4)


def compare(result, sport, home, away, kickoff, league_id=None, *, all_lines=False, alternatives=False):
    """Add sportsbook evidence without mutating model output or changing rankings."""
    if sport == "soccer" and result.get("selection_policy") == "verified_market_only":
        return soccer_opportunities(result, home, away, kickoff, league_id)
    picks = result.get("picks") or []
    mapping = MARKETS.get(sport, {})
    market_keys = {mapping[pick["market"]] for pick in picks if pick.get("market") in mapping}
    if sport=='nba' and alternatives:
        market_keys.update(key+'_alternate' for key in list(market_keys) if key in ('player_points','player_rebounds','player_assists'))
    feed = snapshot(sport, home, away, kickoff, market_keys, league_id) if picks else {"status": "no_picks", "quotes": []}
    counts = Counter((_name(pick.get('name')),pick.get('market')) for pick in picks)
    identities={}
    for pick in picks:identities.setdefault(_name(pick.get('name')),set()).add(str(pick.get('player_id')))
    enriched, matched = [], 0
    for pick in picks:
        name, market = _name(pick.get("name")), mapping.get(pick.get("market"))
        reference = _number(pick.get("line"))
        matches = [quote for quote in feed["quotes"] if counts[(name,pick.get('market'))] == 1 and len(identities[name])==1 and quote["player"] == name
                   and quote["market"] in ({market,market+'_alternate'} if market and alternatives else {market}) and pick.get("side") in {"over", "under"} and reference is not None]
        comparisons = []
        for book in BOOKS:
            options = [quote for quote in matches if quote["bookmaker_key"] == book]
            if not options:
                continue
            for quote in (options if all_lines else [min(options, key=lambda item: (abs(item['line']-reference),item['line']))]):
                comparisons.append(_comparison(pick,quote,feed.get('event_id'),allow_push=sport=='nba'))
        matched += bool(comparisons)
        enriched.append({**pick, 'line_source':'historical', 'sportsbook_quotes':comparisons})
    status = feed['status'] if not feed['quotes'] or matched else 'no_quotes'
    return {**result,'picks':enriched,'sportsbook':{
        'status':status,'provider':'The Odds API','matched_picks':matched,'total_picks':len(picks),
        'reason':MESSAGES.get(status,f'{matched} of {len(picks)} props matched to fresh sportsbook lines.'),
        'validation':'Market comparisons are experimental; accuracy at sportsbook lines is not established.',
        'provider_quota':feed.get('quota'),'local_monthly_credit_cap':MONTHLY_CREDIT_CAP,
        'schedule_conflict':feed.get('schedule_conflict')}}


def _comparison(pick, quote, event_id, *, allow_push=False):
    selected = quote[f'{pick["side"]}_decimal']
    implied = 1 / selected
    total = 1 / quote["over_decimal"] + 1 / quote["under_decimal"]
    probability=_model_at_line(pick,quote['line'])
    push=0 if quote['line']%1==.5 else None
    if allow_push and quote['line']%1==0:
        below=_model_at_line(pick,quote['line']-.5)
        above=_model_at_line(pick,quote['line']+.5)
        if below is not None and above is not None:
            push=abs(below-above)
            probability=above if pick['side']=='over' else below
    return {**{key: value for key, value in quote.items() if key not in {"player", "over_decimal", "under_decimal"}},
                        "side": pick["side"], "decimal_odds": selected, "implied_probability": round(implied, 4),
                        "over_decimal": quote['over_decimal'], "under_decimal": quote['under_decimal'],
                        "provider_event_id":event_id, "provider_player":quote['player'],
                        "fair_probability": round(implied / total, 4) if quote["line"] % 1 == 0.5 else None,
                        "model_chance": probability,'model_push_probability':push,
                        "source": "The Odds API"}


def soccer_opportunities(result, home, away, kickoff, league_id):
    """Price both sides of actual quotes. Historical medians cannot create ranked picks."""
    players = result.get("players") or []
    mapping = MARKETS["soccer"]
    keys = {mapping[m["market"]] for player in players for m in player["markets"] if m["market"] in mapping}
    feed = snapshot("soccer", home, away, kickoff, keys, league_id) if keys else {"status": "no_picks", "quotes": []}
    names = Counter(_name(p["name"]) for p in players)
    now = datetime.now(UTC)
    choices, forecasts = [], []
    for player in players:
        for market in player["markets"]:
            forecasts.append({"name": player["name"], "player_id": player.get("id"), "team": player["team"],
                "market": market["market"], "label": market["label"], "projection": market["projection"],
                "minutes": market.get("minutes"), "games": market.get("games"),
                "model": market.get("model") or "historical baseline"})
            if market.get("model") != "soccer-player-counts-v2" or market.get("games", 0) < 8: continue
            if names[_name(player["name"])] != 1: continue
            matching = [q for q in feed["quotes"] if q["player"] == _name(player["name"])
                        and q["market"] == mapping.get(market["market"])]
            for quote in matching:
                expiration = parse_kickoff(quote.get("expires_at"))
                updated = parse_kickoff(quote.get("updated_at"))
                if not expiration or expiration <= now or not updated or updated > now: continue
                line = quote["line"]
                if line % 1 != .5: continue  # Whole-number settlement needs an explicit push model.
                over = _model_at_line({**market, "side": "over", "chance": market["probability"],
                    "probability": market["probability"]}, line)
                if over is None: continue
                for side, probability in (("over", over), ("under", 1 - over)):
                    odds = quote[f"{side}_decimal"]
                    ev = probability * odds - 1
                    implied = 1 / odds
                    total = 1 / quote["over_decimal"] + 1 / quote["under_decimal"]
                    fair = implied / total
                    if ev < .03 or probability - fair < .02: continue
                    comparison = {**{k: v for k, v in quote.items() if k not in {"player", "over_decimal", "under_decimal"}},
                        "side": side, "decimal_odds": odds, "model_chance": round(probability, 4),
                        "implied_probability": implied, "fair_probability": fair, "source": "The Odds API"}
                    choices.append({**market, "name": player["name"], "player_id": player.get("id"),
                        "team": player["team"], "position": player["position"], "side": side, "line": line,
                        "chance": round(probability, 4), "probability": round(probability, 4), "record": None,
                        "edge": probability - fair, "expected_value": ev, "line_source": "sportsbook",
                        "sportsbook_quotes": [comparison]})
    # One quoted opportunity per player; no quota for overs or unders.
    picks, seen = [], set()
    for choice in sorted(choices, key=lambda p: -p["expected_value"]):
        pid = str(choice["player_id"])
        if pid not in seen:
            picks.append(choice); seen.add(pid)
        if len(picks) == 5: break
    status = "available" if picks else feed["status"] if not feed["quotes"] else "no_verified_opportunity"
    reason = f"{len(picks)} experimental model comparisons at verified sportsbook prices." if picks else (
        "No verified sportsbook opportunity. " + MESSAGES.get(feed["status"], "No quoted side passed the model's research thresholds."))
    quoted_count = len(picks)
    if not picks:
        from services import soccer_model_picks
        picks = soccer_model_picks.select(players)
        if picks:
            status = "model_picks"
            reason = f"{len(picks)} model picks using player history and team matchup data. Targets are model selections; sportsbook prices are unavailable or did not qualify."
    return {**{k: v for k, v in result.items() if k not in {"players", "picks"}}, "picks": picks,
        "forecasts": forecasts, "sportsbook": {"status": status, "provider": "The Odds API", "reason": reason,
        "matched_picks": quoted_count, "total_picks": len(picks),
        "validation": "Experimental model comparisons; no profitable sportsbook performance established."}}
