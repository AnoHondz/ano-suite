"""
parlay_engine.py — Parlay Mode: 5 daily picks + one parlay, priced off real sportsbooks.

How a slate is built
--------------------
1. Fetch the slate's games and odds from The Odds API (DraftKings, FanDuel,
   BetMGM, Caesars, ... — region "us") for moneyline, spreads and totals.
2. For every possible bet ("leg"), work out:
     * consensus_prob — the vig-free probability averaged across all books
     * model_prob     — ScorPred's own model probability (moneyline only, when
                         a model covers that game)
     * prob           — model and market blended (MODEL_WEIGHT), or the
                         market consensus alone when no model covers the game
     * best price     — the highest payout any book is offering
     * edge / EV      — prob vs. the best price's implied probability
3. Rank legs by  score = prob + EV_WEIGHT * EV , keep one leg per game, take the top 5.
4. Parlay: the combination of 2–4 of those picks whose combined odds pay at
   least PARLAY_MIN_ODDS (2.0 = double your stake) with the highest chance of
   hitting.

Schedules (US Eastern dates)
----------------------------
* NBA    — every day, that day's games.
* NFL    — Sundays, that Sunday's games.
* Soccer — Fridays: the weekend (Fri–Sun) across the top 5 leagues.
           Any other day with Champions League / Europa League games: those games.

Quota: the free Odds API plan has 500 credits/month. Checking whether games
exist uses the free /events endpoint; one odds call with 3 markets in 1 region
costs 3 credits. A normal month uses roughly 200–280 credits.

The engine itself is pure Python (no Flask) so it can be unit tested and run
from the command line:

    python parlay_engine.py nba            # build today's NBA slate
    python parlay_engine.py soccer --date 2026-11-06 --force
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# ── Tunables (env overrides) ─────────────────────────────────────────────────
PICKS_PER_SLATE = int(os.environ.get("PARLAY_PICKS", "5"))
PARLAY_MIN_ODDS = float(os.environ.get("PARLAY_MIN_ODDS", "2.0"))   # ≥ 2x payout
PARLAY_MIN_LEGS = 2
PARLAY_MAX_LEGS = int(os.environ.get("PARLAY_MAX_LEGS", "4"))
MODEL_WEIGHT = float(os.environ.get("PARLAY_MODEL_WEIGHT", "0.5"))  # model vs market blend
MIN_PROB = float(os.environ.get("PARLAY_MIN_PROB", "0.55"))
FALLBACK_MIN_PROB = 0.50
MIN_PRICE = 1.25        # skip ultra-heavy favourites (worse than about -400)
# A pick is "value" only at +4% expected value or better (services.value_policy). The card is still filled on thin
# slates (user, 2026-11-04), but those picks are tagged "best available" and never called value; the old -2% / -6%
# negative-value allowances are gone (audit, 2026-11-04).
EV_WEIGHT = 1.5
ODDS_REGIONS = os.environ.get("PARLAY_ODDS_REGIONS", "us")
MARKETS = "h2h,spreads,totals"
# Parlay Mode spends from the same monthly Odds API allowance as the morning email and props pages
# (services.prop_markets.MONTHLY_CREDIT_CAP). Its own share is capped so it never starves them.
PARLAY_MONTHLY_CREDITS = int(os.environ.get("PARLAY_MONTHLY_CREDITS", "150"))

SPORTS: dict[str, dict[str, Any]] = {
    "nba": {"label": "NBA", "schedule": "Every day"},
    "nfl": {"label": "NFL", "schedule": "Every game day (Thu / Sun / Mon)"},
    "soccer": {"label": "Soccer", "schedule": "Every day with top-league or European games"},
}

SPORT_KEYS: dict[str, str] = {
    "basketball_nba": "NBA",
    "americanfootball_nfl": "NFL",
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_uefa_champs_league": "Champions League",
    "soccer_uefa_europa_league": "Europa League",
}
SOCCER_WEEKEND_KEYS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
]
SOCCER_EURO_KEYS = ["soccer_uefa_champs_league", "soccer_uefa_europa_league"]

ModelProvider = Callable[[str, list[dict[str, Any]]], dict[str, dict[str, float]]]


# ── Odds math ────────────────────────────────────────────────────────────────

def american(decimal_odds: float) -> str:
    """Decimal → American odds string (2.5 → '+150', 1.5 → '-200')."""
    if decimal_odds is None or decimal_odds <= 1.0:
        return "—"
    if decimal_odds >= 2.0:
        return f"+{round((decimal_odds - 1) * 100)}"
    return f"-{round(100 / (decimal_odds - 1))}"


def combine_odds(prices: Iterable[float]) -> float:
    total = 1.0
    for p in prices:
        total *= p
    return total


# ── Slate windows ────────────────────────────────────────────────────────────

@dataclass
class SlateWindow:
    sport: str
    slate_date: date
    start: datetime          # UTC
    end: datetime            # UTC
    sport_keys: list[str]
    kind: str                # "daily" | "sunday" | "weekend" | "european"
    title: str


def _et_day_bounds(first: date, days: int = 1) -> tuple[datetime, datetime]:
    start = datetime(first.year, first.month, first.day, tzinfo=ET)
    end = start + timedelta(days=days)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def slate_window(sport: str, day: date) -> SlateWindow | None:
    """Return the window a slate covers on `day`, or None when no slate is due."""
    sport = sport.lower()
    if sport == "nba":
        s, e = _et_day_bounds(day)
        return SlateWindow("nba", day, s, e, ["basketball_nba"], "daily",
                           f"NBA · {day:%a %b} {day.day}")
    if sport == "nfl":
        # Every day: Sunday, Monday and Thursday games all get a slate; days without games spend nothing.
        s, e = _et_day_bounds(day)
        return SlateWindow("nfl", day, s, e, ["americanfootball_nfl"], "daily",
                           f"NFL · {day:%a %b} {day.day}")
    if sport == "soccer":
        if os.environ.get("PARLAY_ODDS_SOURCE", "espn").lower() == "odds_api":
            # The credit-using feed rations soccer: Fridays for the weekend, other days European games only.
            if day.weekday() == 4:
                s, e = _et_day_bounds(day, days=3)
                sun = day + timedelta(days=2)
                return SlateWindow("soccer", day, s, e, list(SOCCER_WEEKEND_KEYS), "weekend",
                                   f"Weekend soccer · {day:%b} {day.day}–{sun.day}")
            s, e = _et_day_bounds(day)
            return SlateWindow("soccer", day, s, e, list(SOCCER_EURO_KEYS), "european",
                               f"European night · {day:%a %b} {day.day}")
        s, e = _et_day_bounds(day)
        return SlateWindow("soccer", day, s, e, SOCCER_WEEKEND_KEYS + SOCCER_EURO_KEYS, "daily",
                           f"Soccer · {day:%a %b} {day.day}")
    raise ValueError(f"Unknown sport: {sport}")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Odds API client ──────────────────────────────────────────────────────────

class OddsApiError(RuntimeError):
    pass


def _reserve_shared_credits(cost: int, api_key: str) -> bool:
    from services.prop_markets import reserve_credits
    return reserve_credits(cost, credential=api_key, share="parlay", share_cap=PARLAY_MONTHLY_CREDITS)


class OddsClient:
    min_books = 2  # legs need prices from at least this many books

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None,
                 reserve: Callable[[int, str], bool] | None = None):
        self.api_key = api_key if api_key is not None else os.environ.get("ODDS_API_KEY", "")
        self.session = session or requests.Session()
        self.reserve = reserve or _reserve_shared_credits
        self.requests_remaining: str | None = None
        self.requests_used: str | None = None

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not self.api_key:
            raise OddsApiError("ODDS_API_KEY is not set")
        resp = self.session.get(f"{ODDS_API_BASE}{path}", params={"apiKey": self.api_key, **params}, timeout=15)
        self.requests_remaining = resp.headers.get("x-requests-remaining", self.requests_remaining)
        self.requests_used = resp.headers.get("x-requests-used", self.requests_used)
        try:
            from services.prop_markets import note_provider_usage
            note_provider_usage(resp.headers.get("x-requests-used"), credential=self.api_key)
        except Exception:
            pass  # usage sync is best effort; the reservation already counted this call
        if resp.status_code == 404:
            return []  # sport out of season / unknown key
        if resp.status_code == 401:
            raise OddsApiError("The Odds API rejected the key (401)")
        if resp.status_code == 429:
            raise OddsApiError("The Odds API quota or rate limit was hit (429)")
        resp.raise_for_status()
        return resp.json()

    def events(self, sport_key: str, start: datetime, end: datetime) -> list[dict]:
        """List games in a window. Free — does not use odds credits."""
        return self._get(f"/sports/{sport_key}/events",
                         {"commenceTimeFrom": _iso(start), "commenceTimeTo": _iso(end)}) or []

    def odds(self, sport_key: str, start: datetime, end: datetime) -> list[dict]:
        """Odds for every game in a window (costs 1 credit per market per region)."""
        cost = len(MARKETS.split(",")) * len(ODDS_REGIONS.split(","))
        if not self.api_key:
            raise OddsApiError("ODDS_API_KEY is not set")
        if not self.reserve(cost, self.api_key):
            raise OddsApiError("This month's odds-credit allowance for Parlay Mode is used up")
        return self._get(f"/sports/{sport_key}/odds", {
            "regions": ODDS_REGIONS,
            "markets": MARKETS,
            "oddsFormat": "decimal",
            "commenceTimeFrom": _iso(start),
            "commenceTimeTo": _iso(end),
        }) or []


# ── Free odds: ESPN's scoreboard (one book, usually DraftKings), no credits ──

ESPN_PATHS = {
    "basketball_nba": "basketball/nba", "americanfootball_nfl": "football/nfl",
    "soccer_epl": "soccer/eng.1", "soccer_spain_la_liga": "soccer/esp.1", "soccer_italy_serie_a": "soccer/ita.1",
    "soccer_germany_bundesliga": "soccer/ger.1", "soccer_france_ligue_one": "soccer/fra.1",
    "soccer_uefa_champs_league": "soccer/uefa.champions", "soccer_uefa_europa_league": "soccer/uefa.europa",
}


def american_to_decimal(value: Any) -> float | None:
    text = str(value).strip().upper() if value is not None else ""
    if text in ("EVEN", "EV", "PK"):
        return 2.0
    try:
        number = float(text.replace("+", ""))
    except ValueError:
        return None
    if number >= 100:
        return round(1 + number / 100, 4)
    if number <= -100:
        return round(1 + 100 / -number, 4)
    return None


def _close(block: dict | None) -> dict:
    block = block or {}
    return block.get("close") or block.get("open") or {}


def espn_event(event: dict) -> dict | None:
    """One ESPN scoreboard event in The Odds API's shape, with its single book's moneyline / spread / total."""
    competition = (event.get("competitions") or [{}])[0]
    sides = {c.get("homeAway"): (c.get("team") or {}).get("displayName") for c in competition.get("competitors") or []}
    home, away = sides.get("home"), sides.get("away")
    odds = (competition.get("odds") or [{}])[0]
    if not home or not away or not odds:
        return None
    book = (odds.get("provider") or {}).get("name") or "ESPN odds"
    markets = []
    moneyline = odds.get("moneyline") or {}
    prices = [(home, american_to_decimal(_close(moneyline.get("home")).get("odds"))),
              (away, american_to_decimal(_close(moneyline.get("away")).get("odds")))]
    if odds.get("drawOdds"):
        prices.append(("Draw", american_to_decimal((odds["drawOdds"] or {}).get("moneyLine"))))
    if all(price for _, price in prices):
        markets.append({"key": "h2h", "outcomes": [{"name": n, "price": p} for n, p in prices]})
    spread = odds.get("pointSpread") or {}
    rows = [(home, _close(spread.get("home"))), (away, _close(spread.get("away")))]
    try:
        outcomes = [{"name": n, "price": american_to_decimal(r.get("odds")), "point": float(r["line"])} for n, r in rows]
        if all(o["price"] for o in outcomes):
            markets.append({"key": "spreads", "outcomes": outcomes})
    except (KeyError, TypeError, ValueError):
        pass
    total = odds.get("total") or {}
    try:
        outcomes = [{"name": side.capitalize(), "price": american_to_decimal(_close(total.get(side)).get("odds")),
                     "point": float(str(_close(total.get(side))["line"]).lstrip("ouOU"))} for side in ("over", "under")]
        if all(o["price"] for o in outcomes):
            markets.append({"key": "totals", "outcomes": outcomes})
    except (KeyError, TypeError, ValueError):
        pass
    if not markets:
        return None
    return {"id": f"espn-{event.get('id')}", "home_team": home, "away_team": away, "commence_time": event.get("date"),
            "bookmakers": [{"title": book, "markets": markets}]}


class EspnOddsClient(OddsClient):
    """Game lines from ESPN's free scoreboard. One sportsbook, so each leg is that book's price."""
    min_books = 1

    def __init__(self, session: requests.Session | None = None):
        super().__init__(api_key="espn", session=session, reserve=lambda cost, key: True)

    def _scoreboard(self, sport_key: str, start: datetime, end: datetime) -> list[dict]:
        path = ESPN_PATHS.get(sport_key)
        if not path:
            return []
        day, last, events = start.astimezone(ET).date(), (end - timedelta(seconds=1)).astimezone(ET).date(), []
        while day <= last:
            resp = self.session.get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard",
                                    params={"dates": day.strftime("%Y%m%d")}, timeout=15)
            if resp.status_code != 200:
                raise ConnectionError(f"ESPN scoreboard returned {resp.status_code}")
            events += resp.json().get("events") or []
            day += timedelta(days=1)
        inside = []
        for event in events:
            try:
                when = datetime.fromisoformat(str(event.get("date")).replace("Z", "+00:00"))
            except ValueError:
                continue
            state = ((event.get("status") or {}).get("type") or {}).get("state")
            if start <= when < end and state == "pre":
                inside.append(event)
        return inside

    def events(self, sport_key: str, start: datetime, end: datetime) -> list[dict]:
        return [{"id": event.get("id")} for event in self._scoreboard(sport_key, start, end)]

    def odds(self, sport_key: str, start: datetime, end: datetime) -> list[dict]:
        return [shaped for event in self._scoreboard(sport_key, start, end) if (shaped := espn_event(event))]


def default_client() -> OddsClient:
    """Free ESPN odds unless PARLAY_ODDS_SOURCE=odds_api asks for the multi-book (credit-using) feed."""
    return OddsClient() if os.environ.get("PARLAY_ODDS_SOURCE", "espn").lower() == "odds_api" else EspnOddsClient()


# ── Team-name matching (for plugging in ScorPred model probabilities) ───────

_NAME_NOISE = {"fc", "afc", "cf", "sc", "ac", "the", "club", "de", "calcio", "football"}


def norm_team(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return " ".join(w for w in text.split() if w not in _NAME_NOISE)


def teams_match(a: str, b: str) -> bool:
    na, nb = norm_team(a), norm_team(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    wa, wb = set(na.split()), set(nb.split())
    return len(wa & wb) >= max(1, min(len(wa), len(wb)) - 1)


def match_model_rows(events: list[dict], rows: list[dict]) -> dict[str, dict[str, float]]:
    """Attach model rows {home, away, p_home, p_away, p_draw?} to odds events by team name.

    Probabilities may be 0–1 or 0–100. Returns {event_id: {"home","away","draw"}}.
    """
    out: dict[str, dict[str, float]] = {}

    def _p(v: Any) -> float | None:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f / 100.0 if f > 1.0 else f

    for ev in events:
        home, away = ev.get("home_team", ""), ev.get("away_team", "")
        for row in rows:
            ph, pa, pd = _p(row.get("p_home")), _p(row.get("p_away")), _p(row.get("p_draw"))
            if teams_match(home, row.get("home", "")) and teams_match(away, row.get("away", "")):
                probs = {"home": ph, "away": pa, "draw": pd}
            elif teams_match(home, row.get("away", "")) and teams_match(away, row.get("home", "")):
                probs = {"home": pa, "away": ph, "draw": pd}  # model listed the sides the other way round
            else:
                continue
            clean = {k: v for k, v in probs.items() if v is not None}
            if clean.get("home") is not None and clean.get("away") is not None:
                out[str(ev.get("id"))] = clean
            break
    return out


# ── Leg building ─────────────────────────────────────────────────────────────

def _fmt_point(p: float) -> str:
    return f"{p:+g}" if p else "PK"


def _selection_label(market: str, name: str, point: float | None, home: str, away: str) -> str:
    if market == "h2h":
        return "Draw" if name.lower() == "draw" else f"{name} to win"
    if market == "spreads":
        return f"{name} {_fmt_point(point or 0.0)}"
    if market == "totals":
        return f"{name} {point:g} ({away} @ {home})" if point is not None else name
    return name


def _bet_type(market: str) -> str:
    return {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}.get(market, market)


def build_legs(event: dict, sport_key: str, model_probs: dict[str, float] | None = None,
               min_books: int = 2) -> list[dict[str, Any]]:
    """Every bettable outcome of one game, priced at the best book with a vig-free consensus."""
    home, away = event.get("home_team", ""), event.get("away_team", "")
    acc: dict[tuple, dict[str, Any]] = {}

    for book in event.get("bookmakers", []) or []:
        book_name = book.get("title") or book.get("key") or "Book"
        for market in book.get("markets", []) or []:
            mkey = market.get("key")
            if mkey not in ("h2h", "spreads", "totals"):
                continue
            outcomes = [o for o in market.get("outcomes", []) or [] if float(o.get("price") or 0) > 1.0]
            if len(outcomes) < 2:
                continue
            # Group by line so an over/under (or spread pair) is de-vigged together.
            groups: dict[Any, list[dict]] = {}
            for o in outcomes:
                line = abs(float(o["point"])) if (mkey != "h2h" and o.get("point") is not None) else None
                groups.setdefault(line, []).append(o)
            for line_outcomes in groups.values():
                if len(line_outcomes) < 2:
                    continue
                overround = sum(1.0 / float(o["price"]) for o in line_outcomes)
                for o in line_outcomes:
                    price = float(o["price"])
                    point = float(o["point"]) if o.get("point") is not None else None
                    key = (mkey, o.get("name", ""), point)
                    slot = acc.setdefault(key, {"fair": [], "best": 0.0, "book": ""})
                    slot["fair"].append((1.0 / price) / overround)
                    if price > slot["best"]:
                        slot["best"], slot["book"] = price, book_name

    legs: list[dict[str, Any]] = []
    for (mkey, name, point), slot in acc.items():
        if len(slot["fair"]) < min_books:
            continue
        consensus = sum(slot["fair"]) / len(slot["fair"])
        model_p = None
        if mkey == "h2h" and model_probs:
            side = "draw" if name.lower() == "draw" else ("home" if name == home else ("away" if name == away else None))
            model_p = model_probs.get(side) if side else None
        prob = MODEL_WEIGHT * model_p + (1 - MODEL_WEIGHT) * consensus if model_p is not None else consensus
        # only half the estimated edge over the vig-free consensus counts until tracked results prove it (value_policy)
        from services.value_policy import EDGE_SHRINK
        prob = consensus + EDGE_SHRINK * (prob - consensus)
        price = slot["best"]
        legs.append({
            "event_id": str(event.get("id")),
            "sport_key": sport_key,
            "competition": SPORT_KEYS.get(sport_key, event.get("sport_title", "")),
            "home": home,
            "away": away,
            "matchup": f"{away} @ {home}" if not sport_key.startswith("soccer") else f"{home} vs {away}",
            "commence_time": event.get("commence_time"),
            "market": mkey,
            "bet_type": _bet_type(mkey),
            "outcome": name,
            "point": point,
            "selection": _selection_label(mkey, name, point, home, away),
            "price": round(price, 3),
            "american": american(price),
            "book": slot["book"],
            "books_count": len(slot["fair"]),
            "consensus_prob": round(consensus, 4),
            "model_prob": round(model_p, 4) if model_p is not None else None,
            "prob": round(prob, 4),
            "implied_prob": round(1.0 / price, 4),
            "edge": round(prob - 1.0 / price, 4),
            "ev": round(prob * price - 1.0, 4),
            "source": "model+market" if model_p is not None else "market",
        })
    return legs


def leg_score(leg: dict[str, Any]) -> float:
    return leg["prob"] + EV_WEIGHT * leg["ev"]


def select_picks(legs: list[dict[str, Any]], n: int = PICKS_PER_SLATE) -> list[dict[str, Any]]:
    """Top-n legs, at most one per game: value picks (+4% EV or better) first, then the best available sides by
    expected value to fill the card. Every pick is tagged "value" or "best available"."""
    from services.value_policy import VALUE_EDGE

    def _pick(pool: list[dict], taken: list[dict], key) -> list[dict]:
        used = {l["event_id"] for l in taken}
        chosen = list(taken)
        for leg in sorted(pool, key=key, reverse=True):
            if len(chosen) >= n:
                break
            if leg["event_id"] in used:
                continue
            chosen.append(leg)
            used.add(leg["event_id"])
        return chosen

    playable = [l for l in legs if l["price"] >= MIN_PRICE]
    picks = _pick([l for l in playable if l["prob"] >= MIN_PROB and l["ev"] >= VALUE_EDGE], [], leg_score)
    if len(picks) < n:  # thin slate: the strongest remaining sides by expected value, tagged as best available
        picks = _pick([l for l in playable if l["prob"] >= FALLBACK_MIN_PROB], picks, lambda l: (l["ev"], l["prob"]))
    picks.sort(key=leg_score, reverse=True)
    for i, p in enumerate(picks, 1):
        p["rank"] = i
        p["score"] = round(leg_score(p), 4)
        p["tier"] = "value" if p["ev"] >= VALUE_EDGE else "best available"
    return picks


def build_parlay(picks: list[dict[str, Any]], min_odds: float = PARLAY_MIN_ODDS) -> dict[str, Any] | None:
    """The 2–4 pick combination paying at least `min_odds` with the best expected value: from value picks when
    they can reach the odds ("+EV"), otherwise from every pick ("Best available"). Picks are from separate games."""
    from services.value_policy import TAG_BEST, TAG_VALUE, VALUE_EDGE
    best: tuple | None = None
    for pool, tag in (([p for p in picks if p["ev"] >= VALUE_EDGE], TAG_VALUE), (picks, TAG_BEST)):
        for size in range(PARLAY_MIN_LEGS, min(PARLAY_MAX_LEGS, len(pool)) + 1):
            for combo in itertools.combinations(pool, size):
                odds = combine_odds(l["price"] for l in combo)
                if odds < min_odds:
                    continue
                hit = combine_odds(l["prob"] for l in combo)
                key = (round(hit * odds, 6), hit, -size)
                if best is None or key > best[0]:
                    best = (key, combo, odds, hit)
        if best:
            break
    if best is None:
        return None
    _, combo, odds, hit = best
    return {
        "legs": [l["rank"] for l in combo],
        "selections": [f"{l['selection']} ({l['american']})" for l in combo],
        "decimal_odds": round(odds, 2),
        "american": american(odds),
        "hit_prob": round(hit, 4),
        "ev": round(hit * odds - 1.0, 4),
        "tag": tag,
        "payout_per_10": round(10 * odds, 2),
        "min_odds": min_odds,
    }


# ── Slate generation ─────────────────────────────────────────────────────────

def generate_slate(sport: str, day: date, client: OddsClient,
                   model_provider: ModelProvider | None = None) -> dict[str, Any]:
    window = slate_window(sport, day)
    base = {
        "sport": sport,
        "label": SPORTS[sport]["label"],
        "slate_date": day.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "picks": [],
        "parlay": None,
        "games": 0,
        "notes": [],
    }
    if window is None:
        return {**base, "status": "off_day", "title": f"No {SPORTS[sport]['label']} slate today",
                "notes": [f"{SPORTS[sport]['label']} picks are posted: {SPORTS[sport]['schedule']}."]}

    base.update({"title": window.title, "kind": window.kind,
                 "window": {"start": _iso(window.start), "end": _iso(window.end)}})

    # 1) Free check: which competitions actually have games in the window?
    active_keys = []
    failed = 0
    for key in window.sport_keys:
        try:
            if client.events(key, window.start, window.end):
                active_keys.append(key)
        except OddsApiError:
            raise
        except Exception as exc:  # network hiccup on one league shouldn't sink the slate
            failed += 1
            logger.warning("parlay: events check failed for %s: %s", key, exc)
    if not active_keys:
        if failed:  # couldn't tell — don't record "no games", let it retry
            return {**base, "status": "error", "notes": ["Couldn't reach the odds service."]}
        return {**base, "status": "no_games",
                "notes": ["No games found for this slate window."]}

    # 2) Paid call: odds for the active competitions only.
    events: list[tuple[str, dict]] = []
    odds_failed = 0
    for key in active_keys:
        try:
            for ev in client.odds(key, window.start, window.end):
                events.append((key, ev))
        except OddsApiError:
            raise
        except Exception as exc:
            odds_failed += 1
            logger.warning("parlay: odds fetch failed for %s: %s", key, exc)
    if odds_failed == len(active_keys):
        return {**base, "status": "error", "notes": ["Couldn't load odds."]}

    # 3) Optional ScorPred model probabilities.
    model_map: dict[str, dict[str, float]] = {}
    if model_provider is not None:
        try:
            model_map = model_provider(sport, [ev for _, ev in events]) or {}
        except Exception as exc:
            logger.warning("parlay: model provider failed for %s: %s", sport, exc)
            base["notes"].append("ScorPred model unavailable — picks use sportsbook consensus only.")

    legs: list[dict] = []
    for key, ev in events:
        legs.extend(build_legs(ev, key, model_map.get(str(ev.get("id"))), min_books=getattr(client, "min_books", 2)))

    picks = select_picks(legs)
    parlay = build_parlay(picks)
    model_games = sum(1 for _, ev in events if str(ev.get("id")) in model_map)

    if len(picks) < PICKS_PER_SLATE:
        base["notes"].append(f"Only {len(picks)} pick(s) met the bar on this slate.")
    if picks and parlay is None:
        base["notes"].append(f"No combination of picks reached {PARLAY_MIN_ODDS:g}x odds.")

    return {
        **base,
        "status": "ready" if picks else "no_picks",
        "games": len(events),
        "model_games": model_games,
        "competitions": [SPORT_KEYS.get(k, k) for k in active_keys],
        "picks": picks,
        "parlay": parlay,
        "quota_remaining": client.requests_remaining,
    }


# ── Storage ──────────────────────────────────────────────────────────────────

def _default_store_dir() -> Path:
    try:
        from runtime_paths import data_dir
        return data_dir() / "parlays"
    except Exception:
        return Path("data") / "parlays"


class SlateStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else _default_store_dir()

    def path(self, sport: str, day: date) -> Path:
        return self.root / sport / f"{day.isoformat()}.json"

    def load(self, sport: str, day: date) -> dict | None:
        p = self.path(sport, day)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def save(self, slate: dict) -> Path:
        p = self.path(slate["sport"], date.fromisoformat(slate["slate_date"]))
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(slate, indent=2), encoding="utf-8")
        tmp.replace(p)
        return p

    def latest(self, sport: str) -> dict | None:
        folder = self.root / sport
        if not folder.exists():
            return None
        for p in sorted(folder.glob("*.json"), reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("status") == "ready":
                return data
        return None

    def history(self, sport: str, limit: int = 10) -> list[dict]:
        folder = self.root / sport
        if not folder.exists():
            return []
        out = []
        for p in sorted(folder.glob("*.json"), reverse=True)[:limit]:
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
        return out

    def try_lock(self, sport: str, day: date, stale_after: int = 600) -> bool:
        """Cross-process lock so several gunicorn workers don't all spend quota."""
        lock = self.path(sport, day).with_suffix(".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            if lock.exists() and time.time() - lock.stat().st_mtime > stale_after:
                lock.unlink()
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            return False

    def unlock(self, sport: str, day: date) -> None:
        try:
            self.path(sport, day).with_suffix(".lock").unlink()
        except FileNotFoundError:
            pass


def today_et() -> date:
    return datetime.now(ET).date()


def ensure_slate(sport: str, day: date | None = None, *, client: OddsClient | None = None,
                 store: SlateStore | None = None, model_provider: ModelProvider | None = None,
                 force: bool = False) -> dict | None:
    """Return the saved slate for `day`, generating it once if it doesn't exist yet."""
    day = day or today_et()
    store = store or SlateStore()
    if not force:
        existing = store.load(sport, day)
        if existing is not None:
            return existing
    if slate_window(sport, day) is None:
        return generate_slate(sport, day, client or default_client(), None)  # off-day stub, not saved
    if not store.try_lock(sport, day):
        return store.load(sport, day)
    try:
        slate = generate_slate(sport, day, client or default_client(), model_provider)
        if slate.get("status") != "error":  # transient failures are retried next time
            store.save(slate)
        return slate
    finally:
        store.unlock(sport, day)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="Build a Parlay Mode slate")
    ap.add_argument("sport", choices=sorted(SPORTS))
    ap.add_argument("--date", help="YYYY-MM-DD (US Eastern); default today")
    ap.add_argument("--force", action="store_true", help="rebuild even if already saved")
    args = ap.parse_args()
    d = date.fromisoformat(args.date) if args.date else today_et()
    result = ensure_slate(args.sport, d, force=args.force)
    print(json.dumps(result, indent=2))
