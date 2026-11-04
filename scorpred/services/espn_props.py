"""Free DraftKings player props from ESPN's core odds feed (no Odds API credits).

ESPN lists each DraftKings prop as one item: main lines as two items at the same line (the over first,
then the under: 48 of 48 cross-checked pairs on 2026-11-02), and "Milestones" as over-only X+ alternate
lines. Main-line pairs are cross-checked against the X+ milestone at line + 0.5 where one exists: a pair
the milestone contradicts is flipped, one it cannot settle keeps the over-first order.

quotes() returns main-line pairs in prop_markets._quotes' shape, so the existing comparisons work
unchanged, and the alternate lines one side at a time for the parlay builders.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import requests

from services import cache_service
from services.prediction_lifecycle import parse_kickoff

CORE = "https://sports.core.api.espn.com/v2/sports/{sport}/leagues/{league}"
SITE = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
BOOK, BOOK_KEY, PROVIDER = "DraftKings", "draftkings", 100
TTL = 300          # a fetched board is current for 5 minutes, like the Odds API path
QUOTE_TTL = 900

LEAGUES = {"nba": ("basketball", "nba"), "nfl": ("football", "nfl")}
MAIN = {
    "nba": {"Total Points": "player_points", "Total Rebounds": "player_rebounds", "Total Assists": "player_assists",
            "Total 3-Point Field Goals": "player_threes", "Total Points, Rebounds, and Assists": "player_points_rebounds_assists"},
    "nfl": {"Total Passing Yards (incl. overtime)": "player_pass_yds", "Total Rushing Yards (incl. overtime)": "player_rush_yds",
            "Total Receiving Yards (incl. overtime)": "player_reception_yds", "Total Receptions (incl. overtime)": "player_receptions",
            "Total Rushing Plus Receiving Yards (incl. overtime)": "player_rush_reception_yds",
            "Total Passing Completions (incl. overtime)": "player_pass_completions",
            "Total Passing Attempts (incl. overtime)": "player_pass_attempts",
            "Total Rushing Attempts (incl. overtime)": "player_rush_attempts"},
    "soccer": {"Total Shots": "player_shots", "Total Shots on Target": "player_shots_on_target"},
}
MILESTONES = {
    "nba": {"Points Milestones": "player_points", "Rebounds Milestones": "player_rebounds", "Assists Milestones": "player_assists",
            "3-Point Field Goals Milestones": "player_threes", "Points + Assists + Rebounds Milestones": "player_points_rebounds_assists"},
    "nfl": {"Passing Yards Milestones": "player_pass_yds", "Rushing Yards Milestones": "player_rush_yds",
            "Receiving Yards Milestones": "player_reception_yds", "Receptions Milestones": "player_receptions",
            "Rushing + Receiving Yards Milestones": "player_rush_reception_yds", "Passing Completions Milestones": "player_pass_completions",
            "Passing Attempts Milestones": "player_pass_attempts", "Rushing Attempts Milestones": "player_rush_attempts"},
    "soccer": {"Shots Milestones": "player_shots", "Shots on Target Milestones": "player_shots_on_target",
               "Assists Milestones": "player_assists", "Fouls Committed Milestones": "player_fouls",
               "Goalkeeper Saves Milestones": "player_saves"},
}
# One price per player, no line: settles like over 0.5.
SINGLE = {"soccer": {"Anytime Goalscorer": "player_goals"}}


def _league(sport: str, league_id=None) -> tuple[str, str] | None:
    if sport == "soccer":
        from api_client import ESPN_SLUG_BY_LEAGUE
        slug = ESPN_SLUG_BY_LEAGUE.get(int(league_id or 0))
        return ("soccer", slug) if slug else None
    return LEAGUES.get(sport)


def _get(url, params=None):
    response = requests.get(url, params=params, timeout=(4, 20))
    response.raise_for_status()
    return response.json()


def event_id(sport: str, home: str, away: str, kickoff, league_id=None) -> str | None:
    """ESPN's id for this exact game: same oriented teams, start within 15 minutes."""
    from services.prop_markets import _same_game
    league = _league(sport, league_id)
    kickoff = parse_kickoff(kickoff)
    if not league or not kickoff:
        return None
    key = cache_service.make_key("espn-scoreboard-v1", *league, kickoff.strftime("%Y%m%d"))
    events = cache_service.get_json(key, ttl=TTL)
    if events is None:
        from zoneinfo import ZoneInfo
        day = kickoff.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")
        events = []
        for raw in _get(SITE.format(sport=league[0], league=league[1]), {"dates": day}).get("events") or []:
            sides = {c.get("homeAway"): (c.get("team") or {}).get("displayName")
                     for c in ((raw.get("competitions") or [{}])[0].get("competitors") or [])}
            events.append({"id": raw.get("id"), "home_team": sides.get("home"), "away_team": sides.get("away"),
                           "commence_time": raw.get("date")})
        cache_service.set_json(key, events, ttl=TTL)
    matches = [e for e in events if _same_game(e, home, away, kickoff, 900)]
    return str(matches[0]["id"]) if len(matches) == 1 else None


def _board(league: tuple[str, str], event: str) -> list[dict]:
    key = cache_service.make_key("espn-props-v1", *league, event)
    cached = cache_service.get_json(key, ttl=TTL)
    if cached is not None:
        return cached
    url = CORE.format(sport=league[0], league=league[1]) + f"/events/{event}/competitions/{event}/odds/{PROVIDER}/propBets"
    items, page = [], 1
    while page <= 20:
        body = _get(url, {"limit": 1000, "page": page})
        items += body.get("items") or []
        if page >= int(body.get("pageCount") or 1):
            break
        page += 1
    board = {"fetched_at": datetime.now(UTC).isoformat(), "items": items}
    cache_service.set_json(key, board, ttl=TTL)
    return board


def _names(refs: set[str]) -> dict[str, str]:
    """Athlete $ref -> display name; names are cached for a week."""
    names, missing = {}, []
    for ref in refs:
        cached = cache_service.get_json(cache_service.make_key("espn-athlete-v1", ref.split("?")[0]), ttl=7 * 86400)
        if cached:
            names[ref] = cached
        else:
            missing.append(ref)
    def load(ref):
        try:
            return ref, _get(ref.replace("http://", "https://")).get("displayName")
        except (requests.RequestException, ValueError):
            return ref, None
    with ThreadPoolExecutor(max_workers=8) as pool:
        for ref, name in pool.map(load, missing):
            if name:
                names[ref] = name
                cache_service.set_json(cache_service.make_key("espn-athlete-v1", ref.split("?")[0]), name, ttl=7 * 86400)
    return names


def _decimal(item: dict) -> float | None:
    """An item's price: NBA keeps it in 'odds', soccer (and game-level props) in 'current' -> 'over'."""
    odds = item.get("odds") or {}
    if not odds:
        over = (item.get("current") or {}).get("over") or {}
        try:
            value = float(over.get("decimal") or over.get("value"))
            return value if value > 1 else None
        except (TypeError, ValueError):
            return None
    try:
        value = float((odds.get("decimal") or {}).get("value"))
        return value if value > 1 else None
    except (TypeError, ValueError):
        from parlay_engine import american_to_decimal
        return american_to_decimal((odds.get("american") or {}).get("value"))


def _line(item: dict) -> float | None:
    try:
        return float(((item.get("current") or {}).get("target") or {}).get("value"))
    except (TypeError, ValueError):
        return None


def shape(sport: str, items: list[dict], names: dict[str, str], fetched_at: str, kickoff,
          unpriced: bool = False) -> tuple[list[dict], list[dict]]:
    """(main-line pairs in prop_markets._quotes' shape, over-only alternate lines).

    unpriced=True also keeps posted alternate lines that have no price yet (price None), for record-only
    picks; their ladders are taken as listed since there is no price order to check."""
    from services.prop_markets import _name
    fetched = parse_kickoff(fetched_at)
    kickoff = parse_kickoff(kickoff)
    expires = min(t for t in (kickoff, fetched + timedelta(seconds=TTL), fetched + timedelta(seconds=QUOTE_TTL)) if t).isoformat()
    main, milestones, single = MAIN.get(sport, {}), MILESTONES.get(sport, {}), SINGLE.get(sport, {})
    groups, alternates = {}, []
    for item in items:
        ref, kind = (item.get("athlete") or {}).get("$ref"), (item.get("type") or {}).get("name")
        if not ref or ref not in names:
            continue
        if kind in single:
            price = _decimal(item)
            if price or unpriced:
                alternates.append({"player": _name(names[ref]), "market": single[kind] + "_alternate", "line": 0.5,
                                   "side": "over", "price": price, "book": BOOK, "updated_at": fetched_at, "expires_at": expires,
                                   "_ref": ref, "_base": single[kind], "_single": True})
            continue
        line, price = _line(item), _decimal(item)
        if line is None or not (price or (unpriced and kind in milestones)):
            continue
        if kind in milestones:  # "10+" settles like over 9.5
            alternates.append({"player": _name(names[ref]), "market": milestones[kind] + "_alternate", "line": line - 0.5,
                               "side": "over", "price": price, "book": BOOK, "updated_at": fetched_at, "expires_at": expires,
                               "_ref": ref, "_base": milestones[kind]})
        elif kind in main:
            groups.setdefault((ref, main[kind], line), []).append(price)
    over_check = {(a["_ref"], a["_base"], a["line"]): a["price"] for a in alternates}
    pairs = []
    for (ref, market, line), prices in groups.items():
        if len(prices) != 2 or line % 1 != 0.5:
            continue
        over, under = prices
        check = over_check.get((ref, market, line))
        if check is not None and check == under and check != over:
            over, under = under, over  # the milestone says this pair is listed under-first
        pairs.append({"player": _name(names[ref]), "market": market, "line": line, "over_decimal": over, "under_decimal": under,
                      "bookmaker": BOOK, "bookmaker_key": BOOK_KEY, "updated_at": fetched_at, "fetched_at": fetched_at,
                      "expires_at": expires, "_ref": ref})
    # ESPN sometimes keeps a stale milestone (e.g. "4+ rebounds +153" for a player whose line is 9.5), so a
    # player's alternate ladder is used only when it proves itself: its rung at the main line carries exactly
    # the main line's over price, and every higher rung pays more than the one below it.
    anchors = {(p["_ref"], p["market"]): (p["line"], p["over_decimal"]) for p in pairs}
    ladders = {}
    for alternate in alternates:
        ladders.setdefault((alternate["_ref"], alternate["_base"]), []).append(alternate)
    # Where no main line exists (soccer has none), a ladder needs at least two rungs in order instead; a
    # one-price market such as anytime goalscorer has nothing to check against and is taken as listed.
    trusted = []
    for key, ladder in ladders.items():
        ladder.sort(key=lambda a: a["line"])
        anchor = anchors.get(key)
        if any(a["price"] is None for a in ladder):
            trusted += ladder if unpriced else []  # lines only, for record-only picks
            continue
        ordered = all(lo["price"] < hi["price"] for lo, hi in zip(ladder, ladder[1:]))
        if anchor is not None:
            ok = ordered and any(a["line"] == anchor[0] and a["price"] == anchor[1] for a in ladder)
        else:
            ok = all(a.get("_single") for a in ladder) or (ordered and len(ladder) >= 2)
        if ok:
            trusted += ladder
    for row in pairs + trusted:
        row.pop("_ref", None), row.pop("_base", None), row.pop("_single", None)
    return pairs, trusted


def quotes(sport: str, home: str, away: str, kickoff, league_id=None) -> dict:
    """DraftKings props for one game from ESPN: {status, quotes (pairs), alternates, event_id, fetched_at}."""
    league = _league(sport, league_id)
    if not league:
        return {"status": "unsupported", "quotes": [], "alternates": []}
    try:
        event = event_id(sport, home, away, kickoff, league_id)
        if not event:
            return {"status": "no_match", "quotes": [], "alternates": []}
        board = _board(league, event)
        names = _names({(i.get("athlete") or {}).get("$ref") for i in board["items"]} - {None})
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return {"status": "unavailable", "quotes": [], "alternates": []}
    pairs, alternates = shape(sport, board["items"], names, board["fetched_at"], kickoff)
    return {"status": "available" if pairs or alternates else "no_quotes", "quotes": pairs, "alternates": alternates,
            "event_id": f"espn-{event}", "fetched_at": board["fetched_at"]}
