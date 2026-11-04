"""Best player props of the day across soccer, NBA and NFL.

Each sport's existing prop model picks up to five props per game. This board collects
every pregame game on the day's slate and ranks by edge: how far the model's chance
exceeds the player's own recent hit rate at that line, the information the model adds
beyond the obvious. Ranking by chance alone would fill the board with near-certainties
(a defender under 0.5 shots on target) that no bookmaker prices usefully. Picks must
still be at least ``MIN_CHANCE`` likely, and each player appears once.

NBA and soccer chances were backtested at these lines (each player's recent median) and
are compared in one overall list. NFL props are experimental and never backtested, so
they are listed separately and never mixed into the overall ranking. A line is a
sportsbook's only when ``line_source`` says so; otherwise it is the player's own median.
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from services.prediction_lifecycle import freshness_state

_log = logging.getLogger(__name__)

CACHE_SECONDS = 60 * 60  # matches the hourly warm-up; started games drop off on every read
EMPTY_CACHE_SECONDS = 60  # a board with no props (cold start, stale sources) is retried soon
PER_SPORT = 8
MIN_CHANCE = 0.6
OVERALL = 10
VALIDATION = {"soccer": "backtested", "nba": "backtested", "nfl": "experimental"}
# Premier League tackles come from the Fantasy Premier League model, checked on five rounds only.
SMALL_SAMPLE_MARKETS = {("soccer", "tackles")}

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def _kickoff(decision: dict) -> datetime | None:
    raw = str(decision.get("matchDate") or "")
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _edge(pick: dict) -> float:
    return pick.get("edge") or 0.0


def rank(picks: list[dict], limit: int) -> list[dict]:
    """Largest edge over the player's own record first, one pick per player, chance at least MIN_CHANCE."""
    best: dict[tuple, dict] = {}
    for pick in picks:
        if pick["chance"] < MIN_CHANCE:
            continue
        key = (pick["sport"], pick.get("name"), pick.get("team"))
        if key not in best or _edge(pick) > _edge(best[key]):
            best[key] = pick
    return sorted(best.values(), key=lambda p: (-_edge(p), -p["chance"]))[:limit]


def build(slates: dict[str, tuple[str | None, list[dict]]], props_for: Callable[[str, dict, str | None], tuple[dict, int]],
          *, now: datetime | None = None, workers: int = 2) -> dict[str, Any]:
    """``slates`` maps sport -> (slate date, decisions); ``props_for(sport, decision, date)`` returns (payload, status)."""
    now = now or datetime.now(timezone.utc)
    jobs = [(sport, decision, slate_date) for sport, (slate_date, decisions) in slates.items()
            for decision in decisions
            if decision.get("pregameAvailable") and (_kickoff(decision) or now) > now]

    def run(job):
        sport, decision, slate_date = job
        try:
            payload, status = props_for(sport, decision, slate_date)
        except Exception:
            _log.warning("best_props: %s %s failed", sport, decision.get("matchId"), exc_info=True)
            return job, None, "Props failed for this game."
        if status != 200:
            return job, None, payload.get("error") or f"Unavailable ({status})"
        return job, payload.get("result") or {}, None

    # Worker threads start with an empty context; carry the caller's (e.g. deferred odds pricing) into each job.
    contexts = [contextvars.copy_context() for _ in jobs]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(lambda context, job: context.run(run, job), contexts, jobs))

    picks: dict[str, list[dict]] = {sport: [] for sport in slates}
    games: dict[str, dict] = {sport: {"games": 0, "with_props": 0, "unavailable": []} for sport in slates}
    for (sport, decision, _slate_date), result, error in outcomes:
        games[sport]["games"] += 1
        if result is None:
            games[sport]["unavailable"].append({"matchup": decision.get("matchup"), "reason": error})
            continue
        games[sport]["with_props"] += 1
        for pick in result.get("picks") or []:
            if pick.get("chance") is None:
                continue
            picks[sport].append({
                "sport": sport, "match_id": decision.get("matchId"),
                "validation": "experimental model target" if pick.get("line_source") == "model_target" else "experimental sportsbook comparison" if pick.get("line_source") == "sportsbook" else "backtested, small sample" if (sport, pick.get("market")) in SMALL_SAMPLE_MARKETS
                else VALIDATION[sport],
                "matchup": decision.get("matchup"), "kickoff": decision.get("matchDate"),
                "league": decision.get("support") if sport == "soccer" else sport.upper(),
                **{key: pick.get(key) for key in ("name", "player_id", "team", "position", "label", "market", "side", "line",
                                                  "line_source", "projection", "chance", "record", "edge", "games",
                                                  "sportsbook_quotes")},
                **({"roster_verification": result["roster_verification"]} if result.get("roster_verification") else {}),
            })
    by_sport = {sport: rank(rows, PER_SPORT) for sport, rows in picks.items()}
    overall = rank([p for sport in ("soccer", "nba") for p in picks.get(sport, [])], OVERALL)
    return {
        "generated_at": now.isoformat(),
        "slates": {sport: {"date": slate_date, **games[sport]} for sport, (slate_date, _d) in slates.items()},
        "overall": overall,
        "by_sport": by_sport,
        "notes": [
            "Ranked by edge: the model's chance minus the player's own recent hit rate at that line. Chance is the probability the pick lands.",
            "Lines marked historical are the player's recent median, not a sportsbook line; a sportsbook line appears only when odds are configured.",
            "Soccer and NBA chances were backtested at historical lines and are ranked together; NFL props are experimental and listed separately.",
            "Props never establish a betting edge on their own; compare against the price you are offered.",
        ],
    }


def _lifetime(board: dict) -> int:
    has_props = any(slate.get("with_props") for slate in (board.get("slates") or {}).values())
    return CACHE_SECONDS if has_props else EMPTY_CACHE_SECONDS


def cached(key: str, make: Callable[[], dict], *, now: float | None = None) -> dict:
    """One build per key per CACHE_SECONDS; concurrent callers share it."""
    now = time.time() if now is None else now
    with _lock:
        entry = _cache.get(key)
        checked = datetime.fromtimestamp(now, timezone.utc)
        if (entry and now - entry[0] < _lifetime(entry[1]) and all(_roster_current(pick, checked)
                for rows in [entry[1].get("overall", []), *entry[1].get("by_sport", {}).values()] for pick in rows)):
            return entry[1]
        board = make()
        _cache[key] = (now, board)
        return board


def refresh(key: str, make: Callable[[], dict]) -> dict:
    """Rebuild now (the scheduler's warm-up); readers keep the previous board until it is replaced."""
    board = make()
    with _lock:
        _cache[key] = (time.time(), board)
    return board


def _roster_current(pick, now):
    verification = pick.get("roster_verification")
    return not verification or freshness_state(verification, now) == "fresh"


def without_started(board: dict, now: datetime | None = None) -> dict:
    """Drop picks whose game has started since the board was built."""
    now = now or datetime.now(timezone.utc)

    def live(pick):
        kickoff = _kickoff({"matchDate": pick.get("kickoff")})
        return (kickoff is None or kickoff > now) and _roster_current(pick, now)
    return {**board, "overall": [p for p in board["overall"] if live(p)],
            "by_sport": {sport: [p for p in rows if live(p)] for sport, rows in board["by_sport"].items()}}
