"""
football_data_client.py — football-data.org v4 wrapper for ScorPred.

Provider: api.football-data.org/v4  (free tier: 10 req/min, no daily cap)
Auth:     X-Auth-Token header
Signup:   https://www.football-data.org/client

Set FOOTBALL_DATA_KEY in .env to activate. Falls back to ESPN if key is absent.

Normalises all responses to the same dict structure used by api_client.py so
the rest of the codebase needs no changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re as _re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from runtime_paths import cache_dir
from services.data_freshness import SOURCE_KEY, stamp
from services.prediction_lifecycle import freshness_state

load_dotenv()

_logger = logging.getLogger("football_data_client")

# ── Config ────────────────────────────────────────────────────────────────────

FD_KEY: str = os.getenv("FOOTBALL_DATA_KEY", "").strip()
FD_BASE = "https://api.football-data.org/v4"
FD_TIMEOUT = float(os.getenv("EXTERNAL_API_TIMEOUT_SECONDS", "4"))
FD_RETRY_ATTEMPTS = 1
FD_RETRY_BACKOFF = 0.0

# True when a key is configured
FD_ENABLED: bool = bool(FD_KEY)

# ── Competition ID mapping: API-Football league ID → football-data.org ID ─────
# football-data.org free tier covers all listed competitions.
FD_COMP_MAP: dict[int, int] = {
    39:  2021,   # Premier League
    140: 2014,   # La Liga
    135: 2019,   # Serie A
    78:  2002,   # Bundesliga
    61:  2015,   # Ligue 1
    2:   2001,   # Champions League
    3:   2146,   # Europa League
    848: 2048,   # Conference League
    94:  2017,   # Primeira Liga (Portugal)
    88:  2003,   # Eredivisie (Netherlands)
    65:  2018,   # Brasileirão Série A
    71:  2013,   # Campeonato Brasileiro Série A (alt)
}

# Reverse map: fd comp ID → API-Football league ID (for normalisation)
_FD_COMP_REVERSE: dict[int, int] = {v: k for k, v in FD_COMP_MAP.items()}

# ── Cache ─────────────────────────────────────────────────────────────────────

_CACHE_DIR: Path = cache_dir("football") / "fd"
_CACHE_LOCK = threading.Lock()
_MEM_CACHE: dict[str, dict] = {}   # {"key": {"data": ..., "ts": float}}
_REFRESH_CONTEXT = threading.local()


def get_last_refresh_status() -> dict:
    return dict(getattr(_REFRESH_CONTEXT, "outcome", {"status": "failed", "observed_at": None, "warning": "No usable provider response"}))


def _served_payload(payload, observed_ts, ttl, *, degraded=False):
    if not isinstance(payload, dict) or not payload:
        _REFRESH_CONTEXT.outcome = {"status": "failed", "observed_at": None, "warning": "Provider returned no payload"}
        return {}
    original = payload.get(SOURCE_KEY)
    degraded = degraded or (isinstance(original, dict) and original.get("state") != "fresh")
    result = stamp(payload, "football_data", datetime.fromtimestamp(observed_ts, timezone.utc), ttl,
                   21600 if ttl >= 3600 else min(ttl * 3, 900), degraded=degraded)
    info = result[SOURCE_KEY]
    state = freshness_state(info)
    _REFRESH_CONTEXT.outcome = {"status": "success" if state == "fresh" else "degraded" if state == "degraded" else "failed",
                                "observed_at": info.get("observed_at"), "warning": info.get("warning")}
    if state == "unavailable":
        return {}
    for row in result.get("matches") or []:
        if isinstance(row, dict):
            row[SOURCE_KEY] = dict(info)
    return result

_TTL: dict[str, int] = {
    "competitions/%d/matches":    300,    # 5 min
    "competitions/%d/standings":  1800,   # 30 min
    "competitions/%d/teams":      3600,   # 1 hr
    "teams/%d/matches":           3600,   # 1 hr — form data rarely changes minute-to-minute
    "matches/%d/head2head":       3600,   # 1 hr
    "matches/%d":                 300,    # 5 min
    "default":                    600,
}


def _ttl_for(endpoint: str) -> int:
    ep = endpoint.strip("/")
    for pattern, secs in _TTL.items():
        if pattern == "default":
            continue
        if _re.fullmatch(pattern.replace("%d", r"\d+"), ep):
            return secs
    return _TTL["default"]


def _cache_key(endpoint: str, params: dict) -> str:
    raw = endpoint + ":" + json.dumps(params or {}, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{key}.json"


def _cache_valid(path: Path, ttl: int) -> bool:
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < ttl


def _load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        raise ValueError(f"Empty cache file: {path}")
    return json.loads(content)


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


# ── HTTP ──────────────────────────────────────────────────────────────────────

def fd_get(endpoint: str, params: dict | None = None) -> dict:
    """
    GET {FD_BASE}/{endpoint} and return the parsed JSON.
    Returns {} on any error. Caches results to disk + memory.
    """
    _REFRESH_CONTEXT.outcome = {"status": "failed", "observed_at": None, "warning": "Provider refresh did not succeed"}
    if not FD_ENABLED:
        return {}

    params = params or {}
    mem_key = _cache_key(endpoint, params)
    disk_path = _cache_path(mem_key)
    ttl = _ttl_for(endpoint)
    now = time.time()

    # 1. memory cache
    with _CACHE_LOCK:
        entry = _MEM_CACHE.get(mem_key)
        if entry and 0 <= (now - entry["ts"]) < ttl:
            return _served_payload(entry["data"], entry["ts"], ttl)
        stale = entry  # may be None

    # 2. disk cache
    if _cache_valid(disk_path, ttl):
        try:
            data = _load_json(disk_path)
            observed_ts = disk_path.stat().st_mtime
            data = _served_payload(data, observed_ts, ttl)
            with _CACHE_LOCK:
                _MEM_CACHE[mem_key] = {"data": data, "ts": observed_ts}
            return data
        except Exception:
            pass

    def fallback():
        if stale:
            data = _served_payload(stale["data"], stale["ts"], ttl, degraded=True)
            if data:
                return data
        if disk_path.exists():
            try:
                return _served_payload(_load_json(disk_path), disk_path.stat().st_mtime, ttl, degraded=True)
            except (OSError, ValueError):
                pass
        return {}

    # 3. live request
    url = f"{FD_BASE}/{endpoint.lstrip('/')}"
    headers = {"X-Auth-Token": FD_KEY, "Accept": "application/json"}

    # Circuit breaker: if the host has been unreachable recently, return stale
    # data immediately rather than burning rate-limit tokens on doomed attempts.
    if _circuit_is_open():
        _logger.debug("fd circuit open — skipping live request for %s", endpoint)
        return fallback()

    # Rate-limit guard: football-data.org free tier = 10 req/min
    # We respect this by keeping a simple per-process token bucket.
    _rate_limit_wait()

    result: dict = {}
    for attempt in range(FD_RETRY_ATTEMPTS):
        try:
            import requests as _req
            resp = _req.get(url, headers=headers, params=params, timeout=FD_TIMEOUT)
            _logger.debug("fd_get %s status=%s", url, resp.status_code)

            if resp.status_code == 429:
                _logger.warning("football-data.org rate-limited (429) for %s", endpoint)
                return fallback()

            if resp.status_code in (401, 403):
                _logger.error("football-data.org auth error %s — check FOOTBALL_DATA_KEY", resp.status_code)
                return {}

            if 400 <= resp.status_code < 500:
                # A missing team or restricted resource is about this request, not the host:
                # retrying cannot help, and it must not open the breaker for every other request.
                _logger.warning("football-data.org %s for %s", resp.status_code, endpoint)
                return fallback()

            if resp.status_code >= 500 and attempt < FD_RETRY_ATTEMPTS - 1:
                time.sleep(FD_RETRY_BACKOFF * (2 ** attempt))
                continue

            resp.raise_for_status()
            result = _served_payload(resp.json(), time.time(), ttl)
            _circuit_record_success()
            break
        except Exception as exc:
            _logger.warning("fd_get failed for %s (attempt %d): %s", endpoint, attempt + 1, exc)
            _circuit_record_failure()
            _rate_limit_revoke()
            if attempt < FD_RETRY_ATTEMPTS - 1:
                time.sleep(FD_RETRY_BACKOFF)
                continue
            return fallback()

    if result:
        _save_json(disk_path, result)
        with _CACHE_LOCK:
            _MEM_CACHE[mem_key] = {"data": result, "ts": now}

    return result


# ── Circuit breaker ───────────────────────────────────────────────────────────
# Opens after FD_CIRCUIT_THRESHOLD consecutive connection failures and stays open
# for FD_CIRCUIT_RESET_SECONDS. While open, fd_get returns stale cache or {}
# instantly — no rate-limit waits, no retries — so one bad network environment
# (e.g. Render blocking api.football-data.org) doesn't stall the whole app.

_CIRCUIT_LOCK = threading.Lock()
_CIRCUIT_FAILURES: list[float] = []  # timestamps of recent consecutive failures
FD_CIRCUIT_THRESHOLD = int(os.getenv("FD_CIRCUIT_THRESHOLD", "2"))
FD_CIRCUIT_RESET_SECONDS = int(os.getenv("FD_CIRCUIT_RESET_SECONDS", "3600"))
_CIRCUIT_OPEN_UNTIL: float = 0.0


def _circuit_is_open() -> bool:
    now = time.time()
    with _CIRCUIT_LOCK:
        global _CIRCUIT_OPEN_UNTIL
        if _CIRCUIT_OPEN_UNTIL and now < _CIRCUIT_OPEN_UNTIL:
            return True
        if _CIRCUIT_OPEN_UNTIL and now >= _CIRCUIT_OPEN_UNTIL:
            _CIRCUIT_OPEN_UNTIL = 0.0  # half-open: let one probe through
        return False


def _circuit_record_failure() -> None:
    now = time.time()
    with _CIRCUIT_LOCK:
        global _CIRCUIT_OPEN_UNTIL
        _CIRCUIT_FAILURES.append(now)
        # Keep only recent failures within the reset window
        cutoff = now - FD_CIRCUIT_RESET_SECONDS
        while _CIRCUIT_FAILURES and _CIRCUIT_FAILURES[0] < cutoff:
            _CIRCUIT_FAILURES.pop(0)
        if len(_CIRCUIT_FAILURES) >= FD_CIRCUIT_THRESHOLD and not _CIRCUIT_OPEN_UNTIL:
            _CIRCUIT_OPEN_UNTIL = now + FD_CIRCUIT_RESET_SECONDS
            _logger.warning(
                "fd circuit breaker OPEN after %d failures — skipping fd.org for %ds",
                len(_CIRCUIT_FAILURES),
                FD_CIRCUIT_RESET_SECONDS,
            )


def _circuit_record_success() -> None:
    with _CIRCUIT_LOCK:
        global _CIRCUIT_OPEN_UNTIL
        _CIRCUIT_FAILURES.clear()
        _CIRCUIT_OPEN_UNTIL = 0.0


# ── Simple token bucket (10 req / 60 s) ──────────────────────────────────────

_RATE_BUCKET_LOCK = threading.Lock()
_RATE_CALL_TIMES: list[float] = []
_RATE_LIMIT = 10   # max calls per window
_RATE_WINDOW = 60  # seconds


def _rate_limit_wait() -> None:
    """Serialising token-bucket: only one thread proceeds at a time so concurrent
    callers can never all burst through together and trip the 10 req/min limit."""
    _RATE_BUCKET_LOCK.acquire()
    while True:
        now = time.time()
        cutoff = now - _RATE_WINDOW
        while _RATE_CALL_TIMES and _RATE_CALL_TIMES[0] < cutoff:
            _RATE_CALL_TIMES.pop(0)
        if len(_RATE_CALL_TIMES) < _RATE_LIMIT:
            _RATE_CALL_TIMES.append(now)
            _RATE_BUCKET_LOCK.release()
            return
        wait = _RATE_WINDOW - (now - _RATE_CALL_TIMES[0]) + 0.05
        _logger.debug("Rate-limit: sleeping %.2fs before fd_get", wait)
        _RATE_BUCKET_LOCK.release()
        time.sleep(wait)
        _RATE_BUCKET_LOCK.acquire()


def _rate_limit_revoke() -> None:
    """Remove the most recent rate-limit token — called when a connection fails
    so that unreachable-host errors don't eat into the real request budget."""
    with _RATE_BUCKET_LOCK:
        if _RATE_CALL_TIMES:
            _RATE_CALL_TIMES.pop()


# ── Normalisers ───────────────────────────────────────────────────────────────

def _fd_status(fd_status: str) -> str:
    """Convert football-data.org match status to API-Football short code."""
    mapping = {
        "SCHEDULED":  "NS",
        "TIMED":      "NS",
        "IN_PLAY":    "LIVE",
        "PAUSED":     "HT",
        "FINISHED":   "FT",
        "SUSPENDED":  "SUSP",
        "POSTPONED":  "PST",
        "CANCELLED":  "CANC",
        "AWARDED":    "FT",
    }
    return mapping.get(fd_status, "UNK")


def _normalize_fd_match(match: dict, our_league_id: int) -> dict:
    """Normalise a football-data.org match dict to API-Football fixture format."""
    home = match.get("homeTeam") or {}
    away = match.get("awayTeam") or {}
    score = match.get("score") or {}
    ft = score.get("fullTime") or {}
    ht = score.get("halfTime") or {}
    fd_status = match.get("status", "SCHEDULED")

    from league_config import CURRENT_SEASON
    season_val = match.get("season", {}) or {}
    season_year = season_val.get("startYear") if isinstance(season_val, dict) else CURRENT_SEASON
    if not season_year and isinstance(season_val, dict):
        start_year = str(season_val.get("startDate") or "")[:4]
        season_year = int(start_year) if start_year.isdigit() else CURRENT_SEASON

    return {
        "fixture": {
            "id": match.get("id"),
            "date": match.get("utcDate", ""),
            "season": season_year or CURRENT_SEASON,
            "status": {
                "short": _fd_status(fd_status),
                "long": fd_status.replace("_", " ").title(),
            },
            "venue": {"name": match.get("venue", "")},
        },
        "league": {
            "id": our_league_id,
            "name": (match.get("competition") or {}).get("name", ""),
            "season": season_year or CURRENT_SEASON,
            "round": f"Matchday {match.get('matchday', '')}",
        },
        "teams": {
            "home": {
                "id": home.get("id"),
                "name": home.get("name") or home.get("shortName") or "",
                "logo": home.get("crest", ""),
            },
            "away": {
                "id": away.get("id"),
                "name": away.get("name") or away.get("shortName") or "",
                "logo": away.get("crest", ""),
            },
        },
        "goals": {
            "home": ft.get("home"),
            "away": ft.get("away"),
        },
        "score": {
            "halftime": {"home": ht.get("home"), "away": ht.get("away")},
        },
        "events": [],
        "stats": [],
        "source": "football_data",
        "data_freshness": dict(match.get(SOURCE_KEY) or {}),
    }


def _normalize_fd_standing_row(row: dict, our_league_id: int) -> dict:
    team = row.get("team") or {}
    return {
        "rank": row.get("position"),
        "team": {
            "id": team.get("id"),
            "name": team.get("name") or team.get("shortName") or "",
            "logo": team.get("crest", ""),
        },
        "points": row.get("points"),
        "goalsDiff": row.get("goalDifference"),
        "form": row.get("form", ""),
        "all": {
            "played": row.get("playedGames"),
            "win":    row.get("won"),
            "draw":   row.get("draw"),
            "lose":   row.get("lost"),
            "goals":  {
                "for":     row.get("goalsFor"),
                "against": row.get("goalsAgainst"),
            },
        },
        "home": {"played": 0, "win": 0, "draw": 0, "lose": 0, "goals": {"for": 0, "against": 0}},
        "away": {"played": 0, "win": 0, "draw": 0, "lose": 0, "goals": {"for": 0, "against": 0}},
    }


def _normalize_fd_team(fd_team: dict, our_league_id: int) -> dict:
    return {
        "team": {
            "id": fd_team.get("id"),
            "name": fd_team.get("name") or fd_team.get("shortName") or "",
            "logo": fd_team.get("crest", ""),
            "country": fd_team.get("area", {}).get("name", ""),
        },
        "venue": {
            "name": fd_team.get("venue", ""),
        },
    }


# ── Public API (mirrors api_client.py signatures) ─────────────────────────────

def get_upcoming_fixtures(
    league_id: int,
    season: int | None = None,
    next_n: int = 20,
) -> list[dict]:
    """
    Return up to next_n upcoming (not-started) fixtures for the given league.
    Results are normalised to API-Football fixture format.
    Returns [] if FOOTBALL_DATA_KEY is not set or the competition is unsupported.
    """
    _REFRESH_CONTEXT.outcome = {"status": "failed", "observed_at": None, "warning": "Competition or provider is unavailable"}
    fd_comp = FD_COMP_MAP.get(league_id)
    if not fd_comp or not FD_ENABLED:
        return []

    now_utc = datetime.now(timezone.utc)
    date_from = now_utc.strftime("%Y-%m-%d")
    date_to = (now_utc + timedelta(days=14)).strftime("%Y-%m-%d")

    data = fd_get(f"competitions/{fd_comp}/matches", {
        "dateFrom": date_from,
        "dateTo":   date_to,
        "status":   "SCHEDULED,TIMED",
    })
    matches = data.get("matches") or []
    result = [_normalize_fd_match(m, league_id) for m in matches]
    result.sort(key=lambda f: (f.get("fixture") or {}).get("date") or "")
    return result[:next_n]


def get_today_fixtures(league_id: int) -> list[dict]:
    """Return today's matches for the given league."""
    fd_comp = FD_COMP_MAP.get(league_id)
    if not fd_comp or not FD_ENABLED:
        return []

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = fd_get(f"competitions/{fd_comp}/matches", {"dateFrom": today, "dateTo": today})
    matches = data.get("matches") or []
    return [_normalize_fd_match(m, league_id) for m in matches]


def get_standings(league_id: int, season: int | None = None) -> list[dict]:
    """
    Return the total standings table for the given league.
    Normalised to API-Football standings row format.
    Returns [] if unsupported.
    """
    fd_comp = FD_COMP_MAP.get(league_id)
    if not fd_comp or not FD_ENABLED:
        return []

    params: dict = {}
    if season:
        params["season"] = season

    data = fd_get(f"competitions/{fd_comp}/standings", params)
    standings_blocks = data.get("standings") or []
    # football-data returns TOTAL / HOME / AWAY blocks; we want TOTAL
    total_block = next(
        (b for b in standings_blocks if (b.get("type") or "").upper() == "TOTAL"),
        standings_blocks[0] if standings_blocks else {},
    )
    table = total_block.get("table") or []
    return [_normalize_fd_standing_row(row, league_id) for row in table]


def get_teams(league_id: int, season: int | None = None) -> list[dict]:
    """
    Return all teams in the given competition/season.
    Normalised to API-Football teams format.
    """
    fd_comp = FD_COMP_MAP.get(league_id)
    if not fd_comp or not FD_ENABLED:
        return []

    params: dict = {}
    if season:
        params["season"] = season

    data = fd_get(f"competitions/{fd_comp}/teams", params)
    teams = data.get("teams") or []
    return [_normalize_fd_team(t, league_id) for t in teams]


def get_h2h(team_name_a: str, team_name_b: str, league_id: int, last: int = 10) -> list[dict]:
    """
    Approximate H2H by fetching recent finished matches for the competition
    and filtering to those involving both teams (matched by name substring).
    Returns normalised fixture list, newest first.

    NOTE: football-data.org H2H requires a match ID, not team IDs.
    This function scans recent results instead — accurate enough for analysis.
    """
    fd_comp = FD_COMP_MAP.get(league_id)
    if not fd_comp or not FD_ENABLED:
        return []

    now_utc = datetime.now(timezone.utc)
    # Free tier allows ~1 season; 420 days covers current + tail of previous
    date_from = (now_utc - timedelta(days=420)).strftime("%Y-%m-%d")
    date_to = now_utc.strftime("%Y-%m-%d")

    data = fd_get(f"competitions/{fd_comp}/matches", {
        "dateFrom": date_from,
        "dateTo":   date_to,
        "status":   "FINISHED",
    })
    matches = data.get("matches") or []

    name_a = team_name_a.lower()
    name_b = team_name_b.lower()

    fixtures = []
    for m in matches:
        home_name = (m.get("homeTeam") or {}).get("name", "").lower()
        away_name = (m.get("awayTeam") or {}).get("name", "").lower()
        names = {home_name, away_name}
        # Match if both team names appear (partial match ok for abbreviations)
        if any(name_a in n or n in name_a for n in names) and \
           any(name_b in n or n in name_b for n in names):
            fixtures.append(_normalize_fd_match(m, league_id))

    fixtures.sort(
        key=lambda f: (f.get("fixture") or {}).get("date") or "",
        reverse=True,
    )
    return fixtures[:last]


def get_team_recent_matches(team_id: int, last: int = 20) -> list[dict]:
    """
    Fetch completed matches for a football-data.org team ID.
    Returns normalised fixtures newest-first, suitable for form analysis.
    """
    if not FD_ENABLED:
        return []
    try:
        now_utc = datetime.now(timezone.utc)
        # Free tier: restrict to ~14 months (current + previous season overlap)
        date_from = (now_utc - timedelta(days=420)).strftime("%Y-%m-%d")
        date_to = now_utc.strftime("%Y-%m-%d")
        data = fd_get(f"teams/{team_id}/matches", {
            "dateFrom": date_from,
            "dateTo":   date_to,
            "status":   "FINISHED",
        })
        matches = data.get("matches") or []
        # Determine league_id from the first match's competition
        league_id_for_norm = None
        if matches:
            comp_id = (matches[0].get("competition") or {}).get("id")
            for our_id, fd_id in FD_COMP_MAP.items():
                if fd_id == comp_id:
                    league_id_for_norm = our_id
                    break
        fixtures = [_normalize_fd_match(m, league_id_for_norm or 39) for m in matches]
        fixtures.sort(key=lambda f: (f.get("fixture") or {}).get("date") or "", reverse=True)
        return fixtures[:last]
    except Exception:
        return []


def is_available() -> bool:
    """Return True if FOOTBALL_DATA_KEY is set and the client can be used."""
    return FD_ENABLED


def supported_league_ids() -> list[int]:
    """Return the API-Football league IDs supported by football-data.org free tier."""
    return list(FD_COMP_MAP.keys())
