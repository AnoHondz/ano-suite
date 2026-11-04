"""
api_client.py — API-Football (RapidAPI) wrapper with JSON caching.

Provider: api-football-v1.p.rapidapi.com
Cache:    cache/football/  (1-hour TTL by default)
Retry:    HTTP 429 → wait 12 s → retry once
On error: returns empty list or empty dict — never raises to caller

NOTE: In-memory cache (_memory_cache) is per-process only. Do not treat
it as shared state across gunicorn workers.
"""

import logging
import hashlib
import importlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from runtime_paths import cache_dir
from services.data_freshness import SOURCE_KEY, stamp
from services.prediction_lifecycle import calendar_date, freshness_state, parse_kickoff
from utils.parsing import safe_float as _sf, safe_int

from league_config import (
    COMP_DIFFICULTY,
    CURRENT_SEASON,
    DEFAULT_LEAGUE_ID,
    LEAGUE_BY_ID,
    SUPPORTED_LEAGUE_IDS,
    _current_football_season,
)

load_dotenv()


class _LazyModuleProxy:
    """Delay heavyweight imports until the first live provider call."""

    def __init__(self, module_name: str):
        self._module_name = module_name
        self._module = None

    def _load(self):
        if self._module is None:
            self._module = importlib.import_module(self._module_name)
        return self._module

    def __getattr__(self, name: str):
        return getattr(self._load(), name)


requests = _LazyModuleProxy("requests")

# ── football-data.org client (preferred free provider) ────────────────────────
try:
    import football_data_client as _fd  # type: ignore
    _FD_AVAILABLE = _fd.is_available()
except Exception:
    _fd = None  # type: ignore
    _FD_AVAILABLE = False

# ── Config ─────────────────────────────────────────────────────────────────────

API_KEY  = (os.getenv("API_FOOTBALL_KEY") or os.getenv("RAPIDAPI_KEY") or "").strip()
API_HOST = os.getenv("API_FOOTBALL_HOST", "api-football-v1.p.rapidapi.com").strip()
API_BASE = os.getenv("API_FOOTBALL_BASE_URL", "https://api-football-v1.p.rapidapi.com/v3").rstrip("/")
EXTERNAL_API_TIMEOUT_SECONDS = float(os.getenv("EXTERNAL_API_TIMEOUT_SECONDS", "8"))
EXTERNAL_API_RETRY_ATTEMPTS = 2
EXTERNAL_API_RETRY_BACKOFF_SECONDS = 2.0
FREE_PLAN_MAX_SEASON = int(os.getenv("API_FOOTBALL_FREE_MAX_SEASON", "2024"))

# Direct API-SPORTS key (api-sports.io account, bypasses RapidAPI entirely).
# If set, requests use x-apisports-key header instead of RapidAPI headers.
_APISPORTS_KEY = os.getenv("APISPORTS_KEY", "").strip()
_APISPORTS_KEY_BACKUP = os.getenv("APISPORTS_KEY_BACKUP", "").strip()
API_KEY_BACKUP = os.getenv("API_FOOTBALL_KEY_BACKUP", "").strip()

# Session-level flag: set to True when primary key daily quota is exhausted.
# Causes all subsequent requests to use the backup key until process restarts.
_PRIMARY_KEY_EXHAUSTED: bool = False

# Session-level flag: set to True when account is suspended so all future
# API-Football calls are skipped immediately without making a network request.
_API_FOOTBALL_SUSPENDED: bool = False

# Auto-detect direct mode: either APISPORTS_KEY is set, or host is api-sports.io
_USING_DIRECT_APISPORTS = bool(_APISPORTS_KEY) or "api-sports.io" in API_HOST

# True when configured to use the free-api-live-football-data provider, which has
# different endpoint paths from API-Football v3.
_USING_FREE_API = "free-api-live-football-data" in API_HOST

# Endpoint translation: API-Football v3 path → free-api-live-football-data path.
# Params are re-mapped per entry: the value is (free_path, param_remapper_fn | None).
# param_remapper_fn receives the original params dict and returns the translated dict.
_FREE_API_ENDPOINT_MAP: dict[str, tuple[str, Any]] = {
    "fixtures": (
        "football-get-all-matches",
        lambda p: (
            {"date": p["date"]} if "date" in p else
            {"matchId": p["id"]} if "id" in p else
            {"leagueId": p.get("league"), "season": p.get("season")}
        ),
    ),
    "fixtures/headtohead": (
        "football-get-h2h",
        lambda p: {"team1Id": p["h2h"].split("-")[0], "team2Id": p["h2h"].split("-")[1]} if "h2h" in p else p,
    ),
    "fixtures/statistics": (
        "football-get-match-statistics",
        lambda p: {"matchId": p.get("fixture")},
    ),
    "fixtures/events": (
        "football-get-match-events",
        lambda p: {"matchId": p.get("fixture")},
    ),
    "fixtures/players": (
        "football-get-match-players",
        lambda p: {"matchId": p.get("fixture")},
    ),
    "standings": (
        "football-get-standings",
        lambda p: {"leagueId": p.get("league"), "season": p.get("season")},
    ),
    "teams": (
        "football-get-teams-by-league-id",
        lambda p: {"leagueId": p.get("league"), "season": p.get("season")},
    ),
    "players": (
        "football-players-search",
        lambda p: {"searchrm": p.get("search", "")},
    ),
}

CACHE_DIR          = cache_dir("football")
CACHE_HOURS        = 1          # default 1-hour TTL
PROPS_CACHE_DIR    = cache_dir("props")
PLAYER_LOG_TTL_H   = 1
FORCE_REFRESH      = False
RAPIDAPI_OK        = True
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

ESPN_SLUG_BY_LEAGUE: dict[int, str] = {
    39: "eng.1",
    140: "esp.1",
    135: "ita.1",
    78: "ger.1",
    61: "fra.1",
    2: "uefa.champions",
    3: "uefa.europa",
    848: "uefa.europa.conf",
    45: "eng.fa",
    143: "esp.copa_del_rey",
    137: "ita.coppa_italia",
    81: "ger.dfb_pokal",
    66: "fra.coupe_de_france",
}

LEAGUES: dict[str, int] = {
    "premier_league": 39,
    "la_liga": 140,
    "serie_a": 135,
    "bundesliga": 78,
    "ligue_1": 61,
    "champions_league": 2,
    "europa_league": 3,
    "conference_league": 848,
    "fa_cup": 45,
    "copa_del_rey": 143,
    "coppa_italia": 137,
    "dfb_pokal": 81,
    "coupe_de_france": 66,
}

API_TEAM_ID_ALIASES: dict[int, int] = {
    33: 360,   # Manchester United
    50: 382,   # Manchester City
    66: 362,   # Aston Villa
}

API_PLAYER_ID_ALIASES: dict[int, dict[str, Any]] = {
    276: {"espn_id": 124091, "team_id": 360, "league_id": 39, "name": "Bruno Fernandes"},
}

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ESPN_V2_BASE = "https://site.api.espn.com/apis/v2/sports/soccer"
ESPN_WEB_BASE = "https://site.web.api.espn.com/apis/common/v3/sports/soccer"

# ── Logging ────────────────────────────────────────────────────────────────────
_logger = logging.getLogger("api_client")

# ── In-memory cache (per-process only — not shared across gunicorn workers) ───
import threading
_memory_cache: dict = {}
_cache_lock = threading.Lock()
_refresh_context = threading.local()


def get_last_refresh_status(league_id=None) -> dict:
    """Per-caller source outcome, including successfully fetched empty schedules."""
    if league_id is not None:
        return dict(getattr(_refresh_context, "schedule_outcomes", {}).get(league_id, {"status": "failed", "observed_at": None, "warning": "No schedule refresh was reported"}))
    return dict(getattr(_refresh_context, "outcome", {"status": "failed", "observed_at": None, "warning": "No provider refresh was reported"}))


def _remember_schedule(league_id):
    outcomes = getattr(_refresh_context, "schedule_outcomes", {})
    outcomes[league_id] = get_last_refresh_status()
    _refresh_context.schedule_outcomes = outcomes


def _source_payload(payload, source, observed_ts, fresh_seconds, max_seconds, *, degraded=False):
    if not isinstance(payload, dict) or not payload:
        _refresh_context.outcome = {"status": "failed", "observed_at": None, "warning": "Provider returned no usable payload"}
        return {}
    observed = datetime.fromtimestamp(observed_ts, timezone.utc)
    existing = payload.get(SOURCE_KEY)
    degraded = degraded or (isinstance(existing, dict) and existing.get("state") != "fresh")
    result = stamp(payload, source, observed, fresh_seconds, max_seconds, degraded=degraded)
    info = result[SOURCE_KEY]
    state = freshness_state(info)
    _refresh_context.outcome = {"status": "success" if state == "fresh" else "degraded" if state == "degraded" else "failed",
                                "observed_at": info.get("observed_at"), "warning": info.get("warning")}
    if state == "unavailable":
        return {}
    for key in ("response", "events", "matches"):
        for row in result.get(key) or []:
            if isinstance(row, dict):
                row[SOURCE_KEY] = dict(info)
                row["data_freshness"] = dict(info)
    return result

# In-process team fixture cache — keyed by (team_id, league_id, season, last)
# Avoids re-running the ESPN scoreboard scan on every scoreline request.
_team_fixtures_cache: dict = {}   # key → {"data": list, "ts": float}
_team_fixtures_lock = threading.Lock()
_TEAM_FIXTURES_TTL = 3600  # One hour; an outer cache cannot extend source freshness.

# ── TTL constants (seconds) — single source of truth ─────────────────────────
_ENDPOINT_TTLS: dict[str, int] = {
    "fixtures":          120,   # 2 min  — match schedules change infrequently
    "fixtures/headtohead": 3600, # 1 hr   — historical, rarely changes
    "teams":             3600,  # 1 hr
    "standings":         300,   # 5 min
    "players":           3600,  # 1 hr
    "injuries":          600,   # 10 min — injury status can change on matchday
    "predictions":       3600,  # 1 hr
    "scoreboard":        120,   # 2 min  — ESPN live scores
    "default":           300,   # 5 min  — fallback
}

def _get_cache_ttl(endpoint: str) -> int:
    for key in _ENDPOINT_TTLS:
        if endpoint.startswith(key):
            return _ENDPOINT_TTLS[key]
    return _ENDPOINT_TTLS["default"]

def _make_cache_key(endpoint: str, params: dict | None) -> str:
    raw = endpoint + ":" + json.dumps(params or {}, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()

# ── 403 session suppression — never retry a forbidden endpoint this process ──
# Cleared on process restart. Prevents hammering a key-gated endpoint.
_FORBIDDEN_ENDPOINTS: set[str] = set()
_RATE_LIMITED_ENDPOINTS: dict[str, float] = {}
_RATE_LIMIT_COOLDOWN_SECONDS = float(os.getenv("API_FOOTBALL_RATE_LIMIT_COOLDOWN_SECONDS", "900"))

# ── Token bucket — cap burst API calls per request context ───────────────────
_MAX_CALLS_PER_REQUEST = 40

def _request_call_count() -> int:
    try:
        from flask import g
        return getattr(g, "_api_call_count", 0)
    except Exception:
        return 0

def _increment_request_call_count() -> int:
    try:
        from flask import g
        count = getattr(g, "_api_call_count", 0) + 1
        g._api_call_count = count
        return count
    except Exception:
        return 0

# ── Cache helpers ──────────────────────────────────────────────────────────────

def _cache_path(endpoint: str, params: dict) -> Path:
    raw = endpoint + str(sorted((params or {}).items()))
    key = hashlib.md5(raw.encode()).hexdigest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.json"


def _cache_valid(path: Path, hours: int = CACHE_HOURS) -> bool:
    if not path.exists():
        return False
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return age < timedelta(hours=hours)


def _load(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        raise ValueError(f"Empty cache file: {path}")
    return json.loads(content)


def _save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


# ── Core HTTP ──────────────────────────────────────────────────────────────────

def _active_api_key() -> str:
    """Return the backup key when the primary daily quota is exhausted."""
    if _PRIMARY_KEY_EXHAUSTED:
        return _APISPORTS_KEY_BACKUP or API_KEY_BACKUP or _APISPORTS_KEY or API_KEY
    return _APISPORTS_KEY or API_KEY


def _headers(api_key=None) -> dict[str, str]:
    key = api_key or _active_api_key()
    if _USING_DIRECT_APISPORTS:
        # Direct api-sports.io access — uses a single header, no host header needed
        return {"x-apisports-key": key, "Accept": "application/json"}
    return {
        "X-RapidAPI-Key":  key,
        "X-RapidAPI-Host": API_HOST,
        "Accept":          "application/json",
    }


def api_get(endpoint: str, params: dict | None = None, *, cache_hours: int = CACHE_HOURS, force_refresh: bool = False) -> dict:
    """
    GET request to API-Football with robust error handling, in-memory caching, rate-limit handling, and stale fallback.
    Returns the raw API-Football response dict (containing a "response" list) on success, or {} on failure.
    """
    global RAPIDAPI_OK, _API_FOOTBALL_SUSPENDED
    params = params or {}
    path = _cache_path(endpoint, params)
    force_refresh = force_refresh or FORCE_REFRESH
    api_key = API_KEY or _APISPORTS_KEY
    cache_key = _make_cache_key(endpoint, params)
    cache_ttl = 3600 if endpoint == "fixtures" and params.get("team") else _get_cache_ttl(endpoint)
    max_stale = 21600 if cache_ttl >= 3600 else min(900, cache_ttl * 3)
    now = time.time()
    _refresh_context.outcome = {"status": "failed", "observed_at": None, "warning": "Provider request did not succeed"}

    def serve(data, observed_ts, *, degraded=False):
        return _source_payload(data, "api_football", observed_ts, cache_ttl, max_stale, degraded=degraded)
    # 1. In-memory cache check
    with _cache_lock:
        cache_entry = _memory_cache.get(cache_key)
        if cache_entry:
            age = now - cache_entry["ts"]
            if age < cache_ttl and not force_refresh:
                _logger.debug("In-memory cache hit for %s (age=%.1fs)", endpoint, age)
                return serve(cache_entry["data"], cache_entry["ts"])
            elif age < max_stale:
                _logger.debug("Stale in-memory cache available for %s (age=%.1fs)", endpoint, age)
                stale_entry = cache_entry
            else:
                stale_entry = None
        else:
            stale_entry = None

    if not force_refresh and path.exists() and 0 <= now - path.stat().st_mtime < cache_ttl:
        try:
            observed_ts = path.stat().st_mtime
            cached = serve(_load(path), observed_ts)
            if cached:
                with _cache_lock:
                    _memory_cache[cache_key] = {"data": cached, "ts": observed_ts}
                return cached
        except (OSError, ValueError):
            pass

    if not api_key:
        _logger.error("Missing API_FOOTBALL_KEY / RAPIDAPI_KEY / APISPORTS_KEY environment variable.")
        RAPIDAPI_OK = False
        return {}

    if _API_FOOTBALL_SUSPENDED:
        _logger.debug("Skipping API-Football call for %s (account suspended)", endpoint)
        if stale_entry:
            return serve(stale_entry["data"], stale_entry["ts"], degraded=True)
        return {}

    # 403 session suppression: if this endpoint was forbidden this process, skip the call
    endpoint_base = endpoint.split("?")[0].split("/")[0]
    if endpoint_base in _FORBIDDEN_ENDPOINTS:
        _logger.debug("Skipping forbidden endpoint %s (403 suppressed)", endpoint)
        if stale_entry:
            return serve(stale_entry["data"], stale_entry["ts"], degraded=True)
        return {}
    cooldown_until = _RATE_LIMITED_ENDPOINTS.get(endpoint_base)
    if cooldown_until:
        remaining = cooldown_until - time.time()
        if remaining > 0:
            _logger.debug("Skipping rate-limited endpoint %s (cooldown %.1fs)", endpoint, remaining)
            if stale_entry:
                return serve(stale_entry["data"], stale_entry["ts"], degraded=True)
            return {"status": "fail", "rate_limited": True}
        _RATE_LIMITED_ENDPOINTS.pop(endpoint_base, None)

    # Token bucket: cap burst API calls per request
    call_count = _increment_request_call_count()
    if call_count > _MAX_CALLS_PER_REQUEST:
        _logger.warning("Request call budget exceeded (%d) for endpoint %s", call_count, endpoint)
        if stale_entry:
            return serve(stale_entry["data"], stale_entry["ts"], degraded=True)
        return {}

    # Translate endpoint path and params when using the free football API provider
    translated_params = params
    if _USING_FREE_API:
        mapping = _FREE_API_ENDPOINT_MAP.get(endpoint)
        if mapping:
            free_path, param_fn = mapping
            translated_params = param_fn(params) if param_fn else params
            url = f"{API_BASE}/{free_path}"
        else:
            # Endpoint has no known free-API equivalent; skip and let ESPN handle it
            _logger.debug("No free-API mapping for endpoint '%s', skipping RapidAPI call", endpoint)
            return {}
    else:
        url = f"{API_BASE}/{endpoint.lstrip('/')}"

    # Pre-cap season and skip unsupported params for api-sports.io free plan.
    # This avoids wasting a live API call that is guaranteed to fail.
    if _USING_DIRECT_APISPORTS and not _USING_FREE_API:
        # Season: free plan covers up to FREE_PLAN_MAX_SEASON; cap silently.
        if isinstance(translated_params.get("season"), int) and translated_params["season"] > FREE_PLAN_MAX_SEASON:
            translated_params = dict(translated_params)
            translated_params["season"] = FREE_PLAN_MAX_SEASON
            _logger.debug("Pre-capped season to %s for endpoint %s", FREE_PLAN_MAX_SEASON, endpoint)
        # Params blocked on the free plan — skip the API call so ESPN handles it.
        _FREE_PLAN_BLOCKED_PARAMS: dict[str, set] = {
            "fixtures": {"next", "live"},
        }
        blocked = _FREE_PLAN_BLOCKED_PARAMS.get(endpoint, set())
        if blocked & set(translated_params.keys()):
            _logger.debug("Skipping blocked free-plan param for endpoint %s", endpoint)
            return {}

    # Per-request dedup cache — prevent identical calls within one page render
    try:
        from flask import g
        _request_cache = getattr(g, "_api_client_req_cache", None)
        if _request_cache is None:
            _request_cache = {}
            g._api_client_req_cache = _request_cache
    except Exception:
        _request_cache = None

    req_cache_key = f"{endpoint}:{json.dumps(params, sort_keys=True)}"
    if _request_cache is not None and req_cache_key in _request_cache:
        _logger.debug("Dedup cache hit for %s in this request", endpoint)
        return _request_cache[req_cache_key]

    result: dict = {}
    free_plan_season_retry_used = False
    for attempt in range(EXTERNAL_API_RETRY_ATTEMPTS):
        try:
            with requests.Session() as sess:
                sess.trust_env = False
                resp = sess.get(
                    url,
                    headers=_headers(api_key),
                    params=translated_params,
                    timeout=EXTERNAL_API_TIMEOUT_SECONDS,
                )
            _logger.debug("%s status=%s", url, resp.status_code)
            if resp.status_code == 429:
                _RATE_LIMITED_ENDPOINTS[endpoint_base] = time.time() + _RATE_LIMIT_COOLDOWN_SECONDS
                _logger.warning("Rate limit (429) for %s", endpoint)
                if stale_entry:
                    _logger.info("Serving stale cache for %s after 429", endpoint)
                    result = serve(stale_entry["data"], stale_entry["ts"], degraded=True)
                else:
                    result = {"status": "fail", "rate_limited": True}
                break
            if resp.status_code == 403:
                _FORBIDDEN_ENDPOINTS.add(endpoint_base)
                RAPIDAPI_OK = False
                _logger.warning("403 Forbidden for endpoint '%s' — suppressing for this session", endpoint)
                if stale_entry:
                    result = serve(stale_entry["data"], stale_entry["ts"], degraded=True)
                break
            if resp.status_code in RETRY_STATUS_CODES and attempt < EXTERNAL_API_RETRY_ATTEMPTS - 1:
                retry_after = resp.headers.get("Retry-After")
                sleep_seconds = float(retry_after) if retry_after else EXTERNAL_API_RETRY_BACKOFF_SECONDS
                _logger.warning("Retrying %s after %.1fs (status %s)", endpoint, sleep_seconds, resp.status_code)
                time.sleep(sleep_seconds)
                continue
            resp.raise_for_status()
            data = resp.json()
            if data.get("errors"):
                error_text = str(data.get("errors", "")).lower()
                # Account suspended — suppress all API-Football calls for this session
                if "suspended" in error_text:
                    _API_FOOTBALL_SUSPENDED = True
                    RAPIDAPI_OK = False
                    _logger.error(
                        "API-Football account suspended — skipping all API-Football calls for this session"
                    )
                    break
                # Daily quota exhausted — switch to backup key and retry immediately
                if "reached the request limit for the day" in error_text:
                    global _PRIMARY_KEY_EXHAUSTED
                    backup = _APISPORTS_KEY_BACKUP or API_KEY_BACKUP
                    if backup and not _PRIMARY_KEY_EXHAUSTED:
                        _PRIMARY_KEY_EXHAUSTED = True
                        api_key = backup
                        _logger.warning(
                            "Primary API key daily quota exhausted — switching to backup key for this session"
                        )
                        continue  # retry with backup key
                    _logger.error("API daily quota exhausted and no backup key available")
                    break
                if (
                    "free plans do not have access to this season" in error_text
                    and not free_plan_season_retry_used
                    and isinstance(translated_params, dict)
                    and translated_params.get("season") is not None
                ):
                    try:
                        requested = int(translated_params.get("season"))
                    except (TypeError, ValueError):
                        requested = FREE_PLAN_MAX_SEASON
                    if requested > FREE_PLAN_MAX_SEASON:
                        translated_params = dict(translated_params)
                        translated_params["season"] = FREE_PLAN_MAX_SEASON
                        free_plan_season_retry_used = True
                        _logger.warning(
                            "Retrying %s with free-plan season fallback: %s -> %s",
                            endpoint,
                            requested,
                            FREE_PLAN_MAX_SEASON,
                        )
                        continue
                _logger.warning("API-Football error for %s: %s", endpoint, data["errors"])
                break
            # Normalize free-API responses to API-Football v3 format {"response": [...]}
            if _USING_FREE_API and "response" not in data:
                payload = data.get("data") or data.get("result") or data.get("matches") or data.get("teams") or data.get("standings") or data
                data = {"response": payload if isinstance(payload, list) else ([payload] if isinstance(payload, dict) else [])}
            data = serve(data, time.time())
            _save(path, data)
            with _cache_lock:
                _memory_cache[cache_key] = {"data": data, "ts": now}
            RAPIDAPI_OK = True
            result = data
            break
        except (requests.RequestException, ValueError) as e:
            _logger.warning("Request failed for %s (attempt %d): %s", endpoint, attempt + 1, e)
            if attempt < EXTERNAL_API_RETRY_ATTEMPTS - 1:
                time.sleep(EXTERNAL_API_RETRY_BACKOFF_SECONDS)
                continue
            if stale_entry:
                _logger.info("Serving stale cache for %s after exception", endpoint)
                result = serve(stale_entry["data"], stale_entry["ts"], degraded=True)
            break

    # Fallback: try disk cache if no result
    if not result:
        if path.exists():
            try:
                observed_ts = path.stat().st_mtime
                cached = serve(_load(path), observed_ts, degraded=True)
                _logger.info("Serving disk cache for %s after API failure", endpoint)
                with _cache_lock:
                    if cached:
                        _memory_cache[cache_key] = {"data": cached, "ts": observed_ts}
                result = cached or {}
            except Exception as e:
                _logger.error("Disk cache load failed for %s: %s", endpoint, e)

    # Store in request-level cache if available
    if _request_cache is not None:
        _request_cache[req_cache_key] = result
    return result


# -- Runtime controls --------------------------------------------------------

def set_force_refresh(enabled: bool) -> None:
    global FORCE_REFRESH
    FORCE_REFRESH = bool(enabled)


def clear_cache() -> None:
    if CACHE_DIR.exists():
        for path in CACHE_DIR.glob("*.json"):
            try:
                path.unlink()
            except OSError:
                pass
    _FORBIDDEN_ENDPOINTS.clear()
    _RATE_LIMITED_ENDPOINTS.clear()


# -- ESPN fallback helpers ---------------------------------------------------

def _current_espn_season() -> int:
    now = datetime.now(timezone.utc)
    return now.year - 1 if now.month < 7 else now.year


def _requested_or_current_season(_season: int) -> int:
    return _current_espn_season()


def _espn_slug(league_id: int) -> str:
    return ESPN_SLUG_BY_LEAGUE.get(league_id, ESPN_SLUG_BY_LEAGUE[DEFAULT_LEAGUE_ID])


def _espn_cache_key(prefix: str, parts: tuple[Any, ...]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    raw = prefix + ":" + "|".join(str(part) for part in parts)
    return CACHE_DIR / f"{hashlib.md5(raw.encode('utf-8')).hexdigest()}.json"


def _espn_get_json(url: str, cache_key: str, ttl_hours: float = 1.0) -> dict:
    path = _espn_cache_key("espn", (cache_key,))
    fresh_seconds = 120 if "scoreboard" in cache_key and ttl_hours < 1 else min(3600, int(ttl_hours * 3600))
    max_seconds = 900 if fresh_seconds < 3600 else 21600
    _refresh_context.outcome = {"status": "failed", "observed_at": None, "warning": "ESPN refresh failed"}
    if not FORCE_REFRESH and _cache_valid(path, fresh_seconds / 3600):
        try:
            return _source_payload(_load(path), "espn", path.stat().st_mtime, fresh_seconds, max_seconds)
        except (OSError, ValueError):
            pass

    last_exc: Exception | None = None
    for attempt in range(EXTERNAL_API_RETRY_ATTEMPTS):
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    url,
                    timeout=EXTERNAL_API_TIMEOUT_SECONDS,
                    headers={"Accept": "application/json"},
                )

            if response.status_code in RETRY_STATUS_CODES and attempt < EXTERNAL_API_RETRY_ATTEMPTS - 1:
                time.sleep(EXTERNAL_API_RETRY_BACKOFF_SECONDS * (2 ** attempt))
                continue

            response.raise_for_status()
            payload = _source_payload(response.json(), "espn", time.time(), fresh_seconds, max_seconds)
            _save(path, payload)
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < EXTERNAL_API_RETRY_ATTEMPTS - 1:
                time.sleep(EXTERNAL_API_RETRY_BACKOFF_SECONDS * (2 ** attempt))
                continue

    if path.exists():
        try:
            payload = _source_payload(_load(path), "espn", path.stat().st_mtime, fresh_seconds, max_seconds, degraded=True)
            if payload:
                return payload
        except (OSError, ValueError):
            pass
    raise RuntimeError(f"ESPN request failed for cache key {cache_key}") from last_exc


def _espn_league_teams(league_id: int) -> list[dict]:
    slug = _espn_slug(league_id)
    payload = _espn_get_json(
        f"{ESPN_BASE}/{slug}/teams",
        f"teams:{league_id}:{slug}:{_requested_or_current_season(CURRENT_SEASON)}",
        ttl_hours=6,
    )
    sports = payload.get("sports") or []
    leagues = (sports[0].get("leagues") or []) if sports else []
    return (leagues[0].get("teams") or []) if leagues else []


def _espn_team_lookup(league_id: int) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for entry in _espn_league_teams(league_id):
        team = entry.get("team") or {}
        if team.get("id"):
            lookup[str(team["id"])] = team
    return lookup


def _resolve_team_id(team_id: int, league_id: int = DEFAULT_LEAGUE_ID) -> int:
    raw_id = _si(team_id)
    if not raw_id:
        return raw_id
    if raw_id in API_TEAM_ID_ALIASES:
        return API_TEAM_ID_ALIASES[raw_id]
    lookup = _espn_team_lookup(league_id)
    if str(raw_id) in lookup:
        return raw_id
    return raw_id


def _normalize_position(pos: str) -> str:
    pos = (pos or "").strip().lower()
    if pos.startswith("g"):
        return "Goalkeeper"
    if pos.startswith("d"):
        return "Defender"
    if pos.startswith("m"):
        return "Midfielder"
    if pos.startswith("f"):
        return "Attacker"
    return pos.title() if pos else ""


def _parse_record(summary: str) -> tuple[int, int, int]:
    if not summary:
        return 0, 0, 0
    parts = [part for part in summary.split("-") if part.strip()]
    if len(parts) != 3:
        return 0, 0, 0
    return _si(parts[0]), _si(parts[1]), _si(parts[2])


def _espn_stat_map(stats: list[dict]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for stat in stats or []:
        key = stat.get("name") or stat.get("abbreviation") or stat.get("label")
        if key:
            data[str(key)] = stat.get("value", stat.get("displayValue"))
    return data


def _team_logo(team: dict) -> str:
    logos = team.get("logos") or []
    if logos:
        return logos[0].get("href", "")
    return team.get("logo", "")


def _espn_score(score_val) -> int | None:
    """Extract integer score from an ESPN score field.

    ESPN returns scores in two shapes depending on the endpoint:
      - Simple scalar: "2" or 2
      - Rich object:   {"value": 2.0, "displayValue": "2", "$ref": "..."}
    """
    if score_val is None:
        return None
    if isinstance(score_val, dict):
        val = score_val.get("value") if score_val.get("value") is not None else score_val.get("displayValue")
        if val is None:
            return None
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return None
    try:
        return int(float(score_val))
    except (TypeError, ValueError):
        return None


def _espn_fixture_status(state: str, name: str = "") -> dict[str, str]:
    state = (state or "").lower()
    label = str(name or "").upper()
    for token, short in (("POSTPON", "PST"), ("CANCEL", "CANC"), ("SUSPEND", "SUSP"), ("ABANDON", "ABD"), ("DELAY", "TBD")):
        if token in label:
            return {"short": short, "long": label.replace("STATUS_", "").replace("_", " ").title()}
    if state == "post":
        return {"short": "FT", "long": "Finished"}
    if state == "in":
        return {"short": "LIVE", "long": "In Progress"}
    if state == "pre":
        return {"short": "NS", "long": "Scheduled"}
    return {"short": "UNK", "long": "Status unavailable"}


def _normalize_espn_fixture(event: dict, league_id: int) -> dict | None:
    competitions = event.get("competitions") or []
    if not competitions:
        return None

    competition = competitions[0]
    competitors = competition.get("competitors") or []
    home = next((entry for entry in competitors if entry.get("homeAway") == "home"), None)
    away = next((entry for entry in competitors if entry.get("homeAway") == "away"), None)
    if not home or not away:
        return None

    home_team = home.get("team") or {}
    away_team = away.get("team") or {}
    status_type = ((competition.get("status") or {}).get("type") or {})
    status = _espn_fixture_status(status_type.get("state"), status_type.get("name") or status_type.get("description"))
    venue = competition.get("venue") or event.get("venue") or {}

    return {
        "fixture": {
            "id": _si(event.get("id")),
            "date": event.get("date", ""),
            "season": _requested_or_current_season(CURRENT_SEASON),
            "status": status,
            "venue": {"name": venue.get("fullName", "")},
        },
        "league": {
            "id": league_id,
            "name": _league_name(league_id) or ((event.get("season") or {}).get("displayName")),
            "season": _requested_or_current_season(CURRENT_SEASON),
            "round": (((event.get("week") or {}).get("number")) and f"Round {(event.get('week') or {}).get('number')}") or "",
        },
        "teams": {
            "home": {
                "id": _si(home_team.get("id")),
                "name": home_team.get("displayName") or home_team.get("name") or "",
                "logo": home_team.get("logo") or _team_logo(home_team),
            },
            "away": {
                "id": _si(away_team.get("id")),
                "name": away_team.get("displayName") or away_team.get("name") or "",
                "logo": away_team.get("logo") or _team_logo(away_team),
            },
        },
        "goals": {
            "home": _espn_score(home.get("score")),
            "away": _espn_score(away.get("score")),
        },
        "score": {"halftime": {"home": None, "away": None}},
        "events": [],
        "stats": [],
        "source": "espn",
        "data_freshness": deepcopy(event.get("data_freshness") or event.get(SOURCE_KEY) or {}),
    }


def _espn_schedule(team_id: int, league_id: int, season: int = CURRENT_SEASON) -> list[dict]:
    slug = _espn_slug(league_id)
    resolved_team_id = _resolve_team_id(team_id, league_id)
    cache_key_str = f"schedule:{league_id}:{resolved_team_id}:{season}"
    payload = _espn_get_json(
        f"{ESPN_BASE}/{slug}/teams/{resolved_team_id}/schedule?season={season}",
        cache_key_str,
        ttl_hours=1,
    )
    events = payload.get("events") or []
    if not events:
        # Don't keep empty schedule responses cached — they are likely transient
        # errors (team not in league, season not yet populated, API hiccup).
        # Evicting lets the next request try again instead of serving stale empties.
        path = _espn_cache_key("espn", (cache_key_str,))
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
        return []
    fixtures = []
    for event in events:
        normalized = _normalize_espn_fixture(event, league_id)
        if normalized:
            fixtures.append(normalized)
    return fixtures


def _espn_roster(team_id: int, league_id: int) -> list[dict]:
    slug = _espn_slug(league_id)
    resolved_team_id = _resolve_team_id(team_id, league_id)
    payload = _espn_get_json(
        f"{ESPN_BASE}/{slug}/teams/{resolved_team_id}/roster",
        f"roster:{league_id}:{resolved_team_id}:{_requested_or_current_season(CURRENT_SEASON)}",
        ttl_hours=6,
    )
    return payload.get("athletes") or []


def _extract_roster_stat(athlete: dict, stat_name: str, default: float = 0.0) -> float:
    categories = ((((athlete.get("statistics") or {}).get("splits") or {}).get("categories")) or [])
    for category in categories:
        for stat in category.get("stats") or []:
            if stat.get("name") == stat_name:
                return _sf(stat.get("value"), default)
    return default


def _player_overview(player_id: int, league_id: int = DEFAULT_LEAGUE_ID) -> dict:
    preferred = _espn_slug(league_id)
    for slug in [preferred, *[value for value in ESPN_SLUG_BY_LEAGUE.values() if value != preferred]]:
        try:
            payload = _espn_get_json(
                f"{ESPN_WEB_BASE}/{slug}/athletes/{player_id}/overview",
                f"athlete:{player_id}:{slug}",
                ttl_hours=6,
            )
        except Exception:
            continue
        if payload.get("statistics"):
            return payload
    return {}


def _resolve_player_id(player_id: int) -> tuple[int, int | None]:
    alias = API_PLAYER_ID_ALIASES.get(_si(player_id))
    if alias:
        return _si(alias.get("espn_id")), _si(alias.get("team_id"), None)
    return _si(player_id), None


def _normalize_espn_squad_player(athlete: dict) -> dict:
    position = athlete.get("position") or {}
    photo = (athlete.get("headshot") or {}).get("href", "")
    player_id = _si(athlete.get("id"))
    name = athlete.get("displayName") or athlete.get("fullName") or ""
    normalized = {
        "id": player_id,
        "name": name,
        "firstname": athlete.get("firstName", ""),
        "lastname": athlete.get("lastName", ""),
        "photo": photo,
        "position": _normalize_position(position.get("abbreviation") or position.get("displayName") or ""),
        "number": athlete.get("jersey"),
        "player": {
            "id": player_id,
            "name": name,
            "photo": photo,
            "number": athlete.get("jersey"),
            "pos": position.get("abbreviation", ""),
        },
    }
    return normalized


def _overview_split(payload: dict, league_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    stats_root = payload.get("statistics") or {}
    names = stats_root.get("names") or []
    splits = stats_root.get("splits") or []
    wanted_slug = _espn_slug(league_id)
    split = next((entry for entry in splits if entry.get("leagueSlug") == wanted_slug), None)
    if split is None and splits:
        split = splits[0]
    if split is None:
        return {}, {}
    values = {name: split["stats"][idx] for idx, name in enumerate(names) if idx < len(split.get("stats") or [])}
    return values, split


def _normalize_espn_player_response(player_id: int, league_id: int) -> list[dict]:
    resolved_player_id, hinted_team_id = _resolve_player_id(player_id)
    payload = _player_overview(resolved_player_id, league_id)
    athlete = payload.get("athlete") or {}
    values, split = _overview_split(payload, league_id)
    team_id = _si(split.get("teamId"), hinted_team_id)
    team_name = split.get("displayName", "").replace(f"{_requested_or_current_season(CURRENT_SEASON)+1} ", "")
    player_name = athlete.get("displayName") or athlete.get("fullName") or API_PLAYER_ID_ALIASES.get(player_id, {}).get("name", "")

    response = {
        "player": {
            "id": resolved_player_id,
            "name": player_name,
            "photo": (athlete.get("headshot") or {}).get("href", ""),
        },
        "statistics": [{
            "team": {"id": team_id, "name": team_name},
            "league": {"id": league_id, "name": _league_name(league_id)},
            "games": {
                "position": _normalize_position(((athlete.get("position") or {}).get("abbreviation")) or ((athlete.get("position") or {}).get("displayName")) or ""),
                "rating": "0",
                "minutes": 0,
                "appearences": _sf(values.get("starts", 0)) or _sf(values.get("appearances", 0)),
            },
            "goals": {
                "total": _sf(values.get("totalGoals", 0)),
                "assists": _sf(values.get("goalAssists", 0)),
            },
            "shots": {
                "total": _sf(values.get("totalShots", 0)),
                "on": _sf(values.get("shotsOnTarget", 0)),
            },
            "passes": {"key": 0, "total": 0},
            "dribbles": {"success": 0},
            "duels": {"won": 0},
            "cards": {
                "yellow": _sf(values.get("yellowCards", 0)),
                "red": _sf(values.get("redCards", 0)),
            },
            "tackles": {"total": 0, "interceptions": 0, "blocks": 0},
        }],
    }
    return [response] if player_name else []


def _espn_top_players(league_id: int, stat_name: str) -> list[dict]:
    leaders: list[dict] = []
    for entry in get_teams(league_id, CURRENT_SEASON):
        team = entry.get("team") or {}
        team_id = _si(team.get("id"))
        if not team_id:
            continue
        for athlete in _espn_roster(team_id, league_id):
            value = _extract_roster_stat(athlete, stat_name, 0.0)
            if value <= 0:
                continue
            player = _normalize_espn_squad_player(athlete)
            leaders.append({
                "player": {
                    "id": player["id"],
                    "name": player["name"],
                    "photo": player["photo"],
                },
                "statistics": [{
                    "team": {"id": team_id, "name": team.get("name", "")},
                    "league": {"id": league_id, "name": _league_name(league_id)},
                    "goals": {
                        "total": _extract_roster_stat(athlete, "totalGoals", 0.0),
                        "assists": _extract_roster_stat(athlete, "goalAssists", 0.0),
                    },
                    "games": {"appearences": _extract_roster_stat(athlete, "appearances", 0.0)},
                }],
                "_sort": value,
            })
    leaders.sort(key=lambda item: item.get("_sort", 0), reverse=True)
    for item in leaders:
        item.pop("_sort", None)
    return leaders[:20]

# ── Helpers ────────────────────────────────────────────────────────────────────

def _si(v, d: int | None = 0) -> int | None:
    return safe_int(v, d)


def _league_name(league_id: int) -> str:
    return (LEAGUE_BY_ID.get(league_id) or {}).get("name", f"League {league_id}")


def _fixture_finished(f: dict) -> bool:
    status = (f.get("fixture") or {}).get("status") or {}
    short  = str(status.get("short") or "")
    return short in {"FT", "AET", "PEN"}


def _fixture_id(f: dict) -> int | None:
    v = (f.get("fixture") or {}).get("id")
    return _si(v) or None


def _fixture_date(f: dict) -> str:
    return str((f.get("fixture") or {}).get("date") or "")[:10]


def _opponent(fixture: dict, team_id: int) -> dict:
    teams = fixture.get("teams") or {}
    home  = teams.get("home") or {}
    away  = teams.get("away") or {}
    return away if str(home.get("id")) == str(team_id) else home


def _player_stats_entry(fp_data: list, player_id: int) -> dict | None:
    for team_entry in fp_data:
        for pe in team_entry.get("players") or []:
            p = pe.get("player") or {}
            if str(p.get("id")) == str(player_id):
                sl = pe.get("statistics") or [{}]
                return sl[0] if sl else {}
    return None


def _stat(stats: dict, section: str, key: str) -> float | None:
    raw = (stats.get(section) or {}).get(key)
    return _sf(raw) if raw is not None else None


# ── Domain methods ─────────────────────────────────────────────────────────────

def get_teams(league_id: int = DEFAULT_LEAGUE_ID, season: int = CURRENT_SEASON) -> list:
    # 1. football-data.org (preferred free provider — no daily quota)
    if _FD_AVAILABLE:
        try:
            result = _fd.get_teams(league_id, season)
            if result:
                return result
        except Exception:
            pass

    # 2. api-sports.io (legacy)
    try:
        data = api_get("teams", {"league": league_id, "season": season}, cache_hours=24)
        if data.get("response"):
            return data.get("response", [])
    except Exception:
        pass

    teams = []
    for entry in _espn_league_teams(league_id):
        team = entry.get("team") or {}
        teams.append({
            "team": {
                "id": _si(team.get("id")),
                "name": team.get("displayName") or team.get("name") or "",
                "logo": _team_logo(team),
                "country": (LEAGUE_BY_ID.get(league_id) or {}).get("country", ""),
            },
            "venue": {
                "name": (((team.get("venue") or {}).get("fullName")) if isinstance(team.get("venue"), dict) else "") or "",
            },
        })
    return teams


def get_h2h(id_a: int, id_b: int, last: int = 10) -> list:
    # 1. football-data.org: identify its team IDs and reuse completed history.
    if _FD_AVAILABLE:
        try:
            name_a = name_b = ""
            for lid in [DEFAULT_LEAGUE_ID, *SUPPORTED_LEAGUE_IDS]:
                if name_a and name_b:
                    break
                for t in (get_teams(lid) or []):
                    team = t.get("team") or {}
                    if str(team.get("id")) == str(id_a):
                        name_a = team.get("name", "")
                    if str(team.get("id")) == str(id_b):
                        name_b = team.get("name", "")
            if name_a and name_b:
                # The team IDs belong to football-data, not ESPN. Reuse the
                # completed team history instead of scanning every competition
                # and then passing these IDs to an unrelated provider.
                history = _fd.get_team_recent_matches(id_a, last=max(last, 100))
                if history:
                    pair = {str(id_a), str(id_b)}
                    result = [fixture for fixture in history
                              if _fixture_finished(fixture)
                              and {str((fixture.get("teams") or {}).get(side, {}).get("id"))
                                   for side in ("home", "away")} == pair]
                    result.sort(key=lambda f: str((f.get("fixture") or {}).get("date") or ""), reverse=True)
                    return result[:last]
                return []
        except Exception:
            pass

    # 2. api-sports.io (has a real h2h endpoint)
    try:
        data = api_get("fixtures/headtohead", {"h2h": f"{id_a}-{id_b}"}, cache_hours=6)
        resp = data.get("response", [])
        if resp:
            resp.sort(key=lambda f: str((f.get("fixture") or {}).get("date") or ""), reverse=True)
            return resp[:last]
    except Exception:
        pass

    team_a_id = _resolve_team_id(id_a)
    team_b_id = _resolve_team_id(id_b)
    fixtures = []
    for league_id in [DEFAULT_LEAGUE_ID, *SUPPORTED_LEAGUE_IDS]:
        try:
            for season_year in (CURRENT_SEASON, CURRENT_SEASON - 1):
                for fixture in _espn_schedule(team_a_id, league_id, season_year):
                    teams = fixture.get("teams") or {}
                    home_id = _si((teams.get("home") or {}).get("id"))
                    away_id = _si((teams.get("away") or {}).get("id"))
                    if {home_id, away_id} == {team_a_id, team_b_id} and _fixture_finished(fixture):
                        fixtures.append(fixture)
        except Exception:
            continue
        if fixtures:
            break
    fixtures.sort(key=lambda f: str((f.get("fixture") or {}).get("date") or ""), reverse=True)
    return fixtures[:last]


def get_fixture_stats(fixture_id: int) -> list:
    try:
        data = api_get("fixtures/statistics", {"fixture": fixture_id}, cache_hours=24)
        if data.get("response"):
            return data.get("response", [])
    except Exception:
        pass
    return []


def get_fixture_by_id(fixture_id: int) -> dict:
    try:
        data = api_get("fixtures", {"id": fixture_id}, cache_hours=24)
        response = data.get("response") or []
        return response[0] if response else {}
    except Exception:
        return {}


def get_fixture_events(fixture_id: int) -> list:
    try:
        data = api_get("fixtures/events", {"fixture": fixture_id}, cache_hours=24)
        if data.get("response"):
            return data.get("response", [])
    except Exception:
        pass
    return []


def get_match_events(fixture_id: int) -> dict[str, Any]:
    """Return goal scorers grouped by home/away side for a fixture.

    Filters only scoring events from the fixture events endpoint and deduplicates
    repeated provider rows. Own goals are credited to the benefiting team.
    """
    fixture = get_fixture_by_id(fixture_id)
    teams = fixture.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    home_name = home.get("name", "")
    away_name = away.get("name", "")

    result = {
        "home_team": home_name,
        "away_team": away_name,
        "home_goals": [],
        "away_goals": [],
    }

    seen: set[tuple[str, str, str, str]] = set()
    for event in get_fixture_events(fixture_id):
        event_type = str(event.get("type") or "").strip().lower()
        detail = str(event.get("detail") or "").strip()
        detail_lower = detail.lower()
        if event_type != "goal":
            continue

        goal_type = "Goal"
        if "pen" in detail_lower:
            goal_type = "Penalty"
        elif "own" in detail_lower:
            goal_type = "Own Goal"
        elif detail:
            goal_type = detail

        time_info = event.get("time") or {}
        elapsed = time_info.get("elapsed")
        extra = time_info.get("extra")
        if elapsed is None:
            minute = ""
        elif extra not in (None, "", 0):
            minute = f"{elapsed}+{extra}'"
        else:
            minute = f"{elapsed}'"

        team_name = ((event.get("team") or {}).get("name") or "").strip()
        player_name = ((event.get("player") or {}).get("name") or "").strip() or "Unknown"
        dedupe_key = (team_name, player_name, minute, goal_type)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        item = {
            "player": player_name,
            "minute": minute,
            "type": goal_type,
            "team": team_name,
        }

        if goal_type == "Own Goal":
            if team_name == home_name:
                result["away_goals"].append(item)
            elif team_name == away_name:
                result["home_goals"].append(item)
        elif team_name == home_name:
            result["home_goals"].append(item)
        elif team_name == away_name:
            result["away_goals"].append(item)

    return result


def get_fixture_player_stats(
    fixture_id: int,
    player_id: int | None = None,
    *,
    team_id: int | None = None,
) -> list | dict | None:
    params: dict = {"fixture": fixture_id}
    if team_id is not None:
        params["team"] = team_id
    try:
        resp = api_get("fixtures/players", params, cache_hours=24).get("response", [])
    except Exception:
        return [] if player_id is None else None
    if player_id is None:
        return resp
    return _player_stats_entry(resp, player_id)


def get_squad(
    team_id: int,
    season: int = CURRENT_SEASON,
    league_id: int = DEFAULT_LEAGUE_ID,
) -> list:
    try:
        data = api_get("players/squads", {"team": team_id}, cache_hours=6).get("response", [])
        if data:
            return data[0].get("players", [])
    except Exception:
        pass
    leagues_to_try = [league_id, *[lid for lid in SUPPORTED_LEAGUE_IDS if lid != league_id]]
    for possible_league in leagues_to_try:
        try:
            roster = [_normalize_espn_squad_player(player) for player in _espn_roster(team_id, possible_league)]
            if roster:
                return roster
        except Exception:
            continue
    return []


def get_player_stats(
    player_id: int,
    league_id: int = DEFAULT_LEAGUE_ID,
    season: int = CURRENT_SEASON,
) -> list:
    """Season aggregate stats for a player in a specific league."""
    try:
        resp = api_get(
            "players",
            {"id": player_id, "season": season, "league": league_id},
            cache_hours=6,
        ).get("response", [])
        filtered = []
        for item in resp:
            stats = [
                s for s in (item.get("statistics") or [])
                if (s.get("league") or {}).get("id") == league_id
            ]
            if stats:
                clone = deepcopy(item)
                clone["statistics"] = stats
                filtered.append(clone)
        if filtered:
            return filtered
    except Exception:
        pass
    return _normalize_espn_player_response(player_id, league_id)


def get_injuries(
    league_id: int = DEFAULT_LEAGUE_ID,
    season: int = CURRENT_SEASON,
    team_id: int | None = None,
) -> list:
    if team_id is None and league_id not in SUPPORTED_LEAGUE_IDS and season in SUPPORTED_LEAGUE_IDS:
        team_id, league_id, season = league_id, season, _current_football_season()
    try:
        params = {"league": league_id, "season": season}
        if team_id is not None:
            params["team"] = team_id
        data = api_get("injuries", params, cache_hours=4)
        if data.get("response"):
            return data.get("response", [])
    except Exception:
        pass

    teams = [team_id] if team_id else [entry.get("team", {}).get("id") for entry in get_teams(league_id, season)]
    injuries: list[dict] = []
    for raw_team_id in teams:
        if not raw_team_id:
            continue
        for athlete in _espn_roster(_si(raw_team_id), league_id):
            for injury in athlete.get("injuries") or []:
                injuries.append({
                    "player": {
                        "id": _si(athlete.get("id")),
                        "name": athlete.get("displayName") or athlete.get("fullName") or "",
                        "position": _normalize_position(((athlete.get("position") or {}).get("abbreviation")) or ((athlete.get("position") or {}).get("displayName")) or ""),
                        "reason": injury.get("detail") or injury.get("type") or injury.get("status") or "Unavailable",
                    }
                })
    if injuries:
        return injuries
    if team_id is not None:
        return [{
            "placeholder": True,
            "player": {
                "id": 0,
                "name": "No injuries reported",
                "position": "",
                "reason": "Fully fit squad",
            },
        }]
    return []


def _espn_scoreboard_team_fixtures(team_id: int, league_id: int, last: int = 10) -> list[dict]:
    """Scan the last 3 weeks of ESPN scoreboard results for matches involving team_id.

    Used as a last-resort fallback when the per-team schedule endpoint returns empty
    (e.g. cached-empty response on startup).  The scoreboard uses the same team IDs
    as the upcoming-fixtures endpoint so no ID translation is needed.
    """
    slug = _espn_slug(league_id)
    if not slug:
        return []
    now_utc = datetime.now(timezone.utc)
    day_offsets = list(range(-21, 1))  # last 3 weeks (soccer plays weekly, 3 weeks = ~3 matchdays)

    def _fetch_day(day_offset: int) -> dict:
        target = now_utc + timedelta(days=day_offset)
        date_str = target.strftime("%Y%m%d")
        try:
            return _espn_get_json(
                f"{ESPN_BASE}/{slug}/scoreboard?dates={date_str}",
                f"scoreboard:{league_id}:{date_str}",
                ttl_hours=6,
            )
        except Exception:
            return {}

    payloads: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        payloads = list(pool.map(_fetch_day, day_offsets))

    seen_ids: set[str] = set()
    fixtures: list[dict] = []
    for payload in payloads:
        for event in payload.get("events") or []:
            competitions = event.get("competitions") or []
            if not competitions:
                continue
            comp = competitions[0]
            competitor_ids = {str(c.get("id") or "") for c in (comp.get("competitors") or [])}
            if str(team_id) not in competitor_ids:
                continue
            fixture = _normalize_espn_fixture(event, league_id)
            if not fixture or not _fixture_finished(fixture):
                continue
            fid = str((fixture.get("fixture") or {}).get("id") or "")
            if fid and fid in seen_ids:
                continue
            if fid:
                seen_ids.add(fid)
            fixtures.append(fixture)

    fixtures.sort(key=lambda f: str((f.get("fixture") or {}).get("date") or ""), reverse=True)
    return fixtures[:last]


def get_team_fixtures(
    team_id: int,
    league_id: int = DEFAULT_LEAGUE_ID,
    season: int = CURRENT_SEASON,
    last: int = 10,
) -> list:
    # Check in-process cache first (avoids re-running the ESPN scoreboard scan)
    _cache_key_tf = (team_id, league_id, season, last)
    with _team_fixtures_lock:
        _entry = _team_fixtures_cache.get(_cache_key_tf)
        if _entry and (time.time() - _entry["ts"]) < _TEAM_FIXTURES_TTL:
            if all(not fixture.get("data_freshness") or freshness_state(fixture["data_freshness"]) == "fresh" for fixture in _entry["data"]):
                return deepcopy(_entry["data"])

    def _unique_fixtures(fixtures: list[dict]) -> list[dict]:
        seen = {}
        for fixture in fixtures:
            fixture_id = _fixture_id(fixture)
            if fixture_id:
                seen[fixture_id] = fixture
            else:
                home_id = (fixture.get("teams") or {}).get("home", {}).get("id") or ""
                away_id = (fixture.get("teams") or {}).get("away", {}).get("id") or ""
                key = f"{(fixture.get('fixture') or {}).get('date') or ''}:{home_id}:{away_id}"
                seen.setdefault(key, fixture)
        return list(seen.values())

    def _cache_and_return(result: list) -> list:
        observed = [parse_kickoff(fixture.get("data_freshness", {}).get("observed_at")) for fixture in result]
        observed = [value.timestamp() for value in observed if value is not None]
        with _team_fixtures_lock:
            _team_fixtures_cache[_cache_key_tf] = {"data": deepcopy(result), "ts": min(observed) if observed else time.time()}
        return result

    logger = logging.getLogger("api_client")
    fixtures = []

    # Try football-data.org first — it's the primary source for European soccer team IDs
    # and is fastest when the IDs originate from that provider (avoids slow API-Football
    # timeout on wrong-namespace IDs).
    try:
        import football_data_client as _fdc
        if _fdc.FD_ENABLED:
            fd_fixtures = _fdc.get_team_recent_matches(team_id, last=last)
            if fd_fixtures:
                logger.debug(f"[API_CLIENT] team_id={team_id} returning {len(fd_fixtures)} fixtures from football-data.org")
                return _cache_and_return(fd_fixtures)
    except Exception:
        pass

    try:
        logger.debug(f"[API_CLIENT] Fetching team fixtures: team_id={team_id}, league_id={league_id}, season={season}")
        for season_year in (season, season - 1):
            season_fixtures = api_get(
                "fixtures",
                {"team": team_id, "league": league_id, "season": season_year},
                cache_hours=2,
            ).get("response", [])
            logger.debug(f"[API_CLIENT] team_id={team_id} season={season_year} fetched {len(season_fixtures)} fixtures")
            if season_fixtures:
                fixtures.extend(season_fixtures)
        if fixtures:
            fixtures = _unique_fixtures(fixtures)
            fixtures = [f for f in fixtures if _fixture_finished(f)]
            fixtures.sort(
                key=lambda f: str((f.get("fixture") or {}).get("date") or ""),
                reverse=True,
            )
            logger.debug(f"[API_CLIENT] team_id={team_id} returning {len(fixtures[:last])} completed fixtures")
            return _cache_and_return(fixtures[:last])
    except Exception as exc:
        logger.warning(f"[API_CLIENT] get_team_fixtures failed for team_id={team_id}: {exc}")

    # ESPN fallback – try per-team schedule first, then scoreboard scan
    fixtures = []
    for season_year in (season, season - 1):
        try:
            fixtures.extend(
                [fixture for fixture in _espn_schedule(team_id, league_id, season_year) if _fixture_finished(fixture)]
            )
        except Exception:
            continue
    if fixtures:
        fixtures = _unique_fixtures(fixtures)
        fixtures.sort(key=lambda f: str((f.get("fixture") or {}).get("date") or ""), reverse=True)
        return _cache_and_return(fixtures[:last])

    # Scoreboard scan: fetch the last 3 weeks of completed league results and
    # pick out any match involving team_id.  Reliable because it uses the same
    # ESPN team IDs that come from the upcoming-fixtures scoreboard, and avoids
    # the per-team schedule endpoint that can return an empty cached response.
    return _cache_and_return(_espn_scoreboard_team_fixtures(team_id, league_id, last=last))


def get_standings(
    league_id: int = DEFAULT_LEAGUE_ID,
    season: int = CURRENT_SEASON,
) -> list:
    # 1. football-data.org (preferred free provider)
    if _FD_AVAILABLE:
        try:
            result = _fd.get_standings(league_id, season)
            if result:
                return result
        except Exception:
            pass

    # 2. api-sports.io (legacy)
    try:
        response = api_get("standings", {"league": league_id, "season": season}, cache_hours=4).get("response", [])
        if response:
            return (((response[0] or {}).get("league") or {}).get("standings") or [[]])[0]
    except Exception:
        pass

    try:
        payload = _espn_get_json(
            f"{ESPN_V2_BASE}/{_espn_slug(league_id)}/standings",
            f"standings:{league_id}:{_requested_or_current_season(season)}",
            ttl_hours=6,
        )
        entries = (((payload.get("children") or [{}])[0].get("standings") or {}).get("entries") or [])
    except Exception:
        return []

    rows = []
    for entry in entries:
        team = entry.get("team") or {}
        stats = _espn_stat_map(entry.get("stats") or [])
        wins, draws, losses = _parse_record(str(stats.get("overall", "")))
        # ESPN uses several key names; try each in order
        played = (_si(stats.get("gamesPlayed")) or _si(stats.get("GP"))
                  or _si(stats.get("played")) or (wins + draws + losses) or None)
        goals_for = (_si(stats.get("pointsFor")) or _si(stats.get("GF"))
                     or _si(stats.get("goals_for")))
        goals_against = (_si(stats.get("pointsAgainst")) or _si(stats.get("GA"))
                         or _si(stats.get("goals_against")))
        rank = (_si(entry.get("rank")) or _si(stats.get("rank"))
                or _si(stats.get("RK")) or _si(stats.get("rk")))
        rows.append({
            "rank": rank,
            "team": {
                "id": _si(team.get("id")),
                "name": team.get("displayName") or team.get("name") or "",
                "logo": _team_logo(team),
            },
            "points": _si(stats.get("points")),
            "goalsDiff": _si(stats.get("pointDifferential")),
            "form": "",
            "all": {
                "played": played,
                "win": wins,
                "draw": draws,
                "lose": losses,
                "goals": {"for": goals_for, "against": goals_against},
            },
            "home": {"played": 0, "win": 0, "draw": 0, "lose": 0, "goals": {"for": 0, "against": 0}},
            "away": {"played": 0, "win": 0, "draw": 0, "lose": 0, "goals": {"for": 0, "against": 0}},
        })
    return rows


def get_upcoming_fixtures(
    league_id: int = DEFAULT_LEAGUE_ID,
    season: int = CURRENT_SEASON,
    next_n: int = 20,
) -> list:
    logger = logging.getLogger("api_client")

    # 1. football-data.org (preferred free provider — no daily quota)
    if _FD_AVAILABLE:
        try:
            result = _fd.get_upcoming_fixtures(league_id, season, next_n)
            outcome = _fd.get_last_refresh_status()
            if result or outcome.get("status") == "success":
                _refresh_context.outcome = outcome
                _remember_schedule(league_id)
                logger.debug("[API_CLIENT] fd get_upcoming_fixtures returned %d fixtures", len(result))
                return result
        except Exception as exc:
            logger.warning("[API_CLIENT] fd get_upcoming_fixtures failed: %s", exc)

    # 2. api-sports.io (legacy)
    try:
        logger.debug(f"[API_CLIENT] Fetching upcoming fixtures: league_id={league_id}, season={season}, next_n={next_n}")
        payload = api_get(
            "fixtures",
            {"league": league_id, "season": season, "next": next_n},
            cache_hours=2,
        )
        response = payload.get("response", [])
        logger.debug(f"[API_CLIENT] get_upcoming_fixtures fetched {len(response)} fixtures")
        if response or "response" in payload and get_last_refresh_status().get("status") == "success":
            _remember_schedule(league_id)
            return response
    except Exception as exc:
        logger.warning(f"[API_CLIENT] get_upcoming_fixtures failed: {exc}")
    # ESPN fallback: scan ahead across the next few weeks (UTC) because many
    # leagues have multi-day gaps between matchdays. Fetch all days in parallel
    # to avoid the sequential-per-day hang.
    slug = _espn_slug(league_id)
    now_utc = datetime.now(timezone.utc)
    all_fixtures: list = []
    seen_ids: set[str] = set()
    max_days_ahead = max(7, min(28, next_n * 2))

    _FINISHED = {"FT", "AET", "PEN", "SUSP", "ABD", "WO"}
    _LIVE     = {"LIVE", "1H", "2H", "HT", "ET", "BT", "P", "INT"}

    def _fetch_day(day_offset: int):
        target = now_utc + timedelta(days=day_offset)
        date_str = target.strftime("%Y%m%d")
        try:
            return _espn_get_json(
                f"{ESPN_BASE}/{slug}/scoreboard?dates={date_str}",
                f"scoreboard:{league_id}:{date_str}",
                ttl_hours=0.5,
            )
        except Exception:
            return {}

    day_offsets = list(range(max_days_ahead + 1))
    payloads_by_offset: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(day_offsets))) as _pool:
        future_to_offset = {_pool.submit(_fetch_day, off): off for off in day_offsets}
        for future in as_completed(future_to_offset):
            payloads_by_offset[future_to_offset[future]] = future.result()

    for day_offset in day_offsets:
        payload = payloads_by_offset.get(day_offset) or {}
        for event in payload.get("events") or []:
            fixture = _normalize_espn_fixture(event, league_id)
            if not fixture:
                continue
            fid = str((fixture.get("fixture") or {}).get("id") or "")
            if fid and fid in seen_ids:
                continue
            if fid:
                seen_ids.add(fid)
            status_short = (fixture.get("fixture") or {}).get("status", {}).get("short", "NS")
            # Keep not-started and any unknown status; skip finished and live
            if status_short not in _FINISHED and status_short not in _LIVE:
                all_fixtures.append(fixture)

    infos = [payload.get(SOURCE_KEY) for payload in payloads_by_offset.values() if isinstance(payload, dict) and payload.get(SOURCE_KEY)]
    observed = [parse_kickoff(info.get("observed_at")) for info in infos]
    observed = [value for value in observed if value is not None]
    failed_days = sum(1 for payload in payloads_by_offset.values() if "events" not in payload)
    delayed = failed_days or any(freshness_state(info) != "fresh" for info in infos)
    _refresh_context.outcome = {
        "status": "degraded" if delayed and infos else "failed" if not infos else "success",
        "observed_at": min(observed).isoformat() if observed else None,
        "warning": "Some soccer schedule dates could not be refreshed" if delayed else None,
    }
    if delayed:
        for fixture in all_fixtures:
            fixture["data_freshness"] = dict(fixture.get("data_freshness") or {}, state="degraded", warning=_refresh_context.outcome["warning"])
    _remember_schedule(league_id)
    all_fixtures.sort(key=lambda f: str((f.get("fixture") or {}).get("date") or ""))
    return all_fixtures[:next_n]


def get_fixtures_for_date(
    league_id: int,
    season: int,
    date: str,
) -> list:
    """Fetch a New York calendar day, which spans parts of two UTC dates."""
    logger = logging.getLogger("api_client")
    try:
        requested = datetime.strptime(date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return []
    utc_dates = [requested.strftime("%Y-%m-%d"), (requested + timedelta(days=1)).strftime("%Y-%m-%d")]
    # Date reads must publish their own outcome, including a verified empty day.
    # Do not inherit a successful refresh from an earlier unrelated call.
    _refresh_context.outcome = {"status": "failed", "observed_at": None,
                                "warning": "No date-specific schedule refresh succeeded"}
    _remember_schedule(league_id)

    def remember(payloads, field):
        infos = [p.get(SOURCE_KEY) for p in payloads
                 if isinstance(p, dict) and isinstance(p.get(field), list) and p.get(SOURCE_KEY)]
        observed = [parse_kickoff(info.get("observed_at")) for info in infos]
        observed = [value for value in observed if value is not None]
        complete = len(infos) == len(utc_dates) and all(freshness_state(info) == "fresh" for info in infos)
        _refresh_context.outcome = {"status": "success" if complete else "degraded" if infos else "failed",
            "observed_at": min(observed).isoformat() if observed else None,
            "warning": None if complete else "Some schedule dates could not be refreshed"}
        _remember_schedule(league_id)

    def selected(fixtures):
        return sorted(
            {str((row.get("fixture") or {}).get("id")): row for row in fixtures
             if calendar_date((row.get("fixture") or {}).get("date")) == date}.values(),
            key=lambda row: str((row.get("fixture") or {}).get("date") or ""),
        )

    # 1. football-data.org: pull ahead batch and filter by date
    if _FD_AVAILABLE:
        try:
            result = _fd.get_upcoming_fixtures(league_id, season, 30)
            if result:
                filtered = selected(result)
                outcome = _fd.get_last_refresh_status()
                # A fresh batch that runs past the requested day proves the day has no matches;
                # falling through would only re-ask keyless API-Football and ESPN for the same answer.
                last_day = max((calendar_date((row.get("fixture") or {}).get("date")) or "" for row in result), default="")
                if filtered or (outcome.get("status") == "success" and last_day > date):
                    _refresh_context.outcome = outcome
                    _remember_schedule(league_id)
                    return filtered
        except Exception as exc:
            logger.debug("[API_CLIENT] fd get_fixtures_for_date filter failed: %s", exc)

    # 2. api-sports.io supports ?date=YYYY-MM-DD
    try:
        payloads = [api_get("fixtures", {"league": league_id, "season": season, "date": utc_day}) for utc_day in utc_dates]
        remember(payloads, "response")
        response = selected([row for payload in payloads for row in payload.get("response") or []])
        if response:
            logger.debug(
                "[API_CLIENT] get_fixtures_for_date api-sports returned %d for %s", len(response), date
            )
            if any("response" not in payload for payload in payloads):
                for row in response:
                    row["data_freshness"] = dict(row.get("data_freshness") or {}, state="degraded", warning="Some schedule dates could not be refreshed")
            return response
        if all("response" in payload for payload in payloads):
            return []
    except Exception as exc:
        logger.warning("[API_CLIENT] get_fixtures_for_date api-sports failed: %s", exc)

    # 3. ESPN fallback
    slug = _espn_slug(league_id)
    if slug:
        try:
            fixtures = []
            failed_days = 0
            espn_payloads = []
            for utc_day in utc_dates:
                espn_date = utc_day.replace("-", "")
                try:
                    payload = _espn_get_json(
                        f"{ESPN_BASE}/{slug}/scoreboard?dates={espn_date}",
                        f"scoreboard:{league_id}:{espn_date}", ttl_hours=120 / 3600,
                    )
                    espn_payloads.append(payload)
                    if not isinstance(payload.get("events"), list):
                        failed_days += 1
                    for event in payload.get("events") or []:
                        fixture = _normalize_espn_fixture(event, league_id)
                        if fixture:
                            fixtures.append(fixture)
                except (OSError, RuntimeError, ValueError):
                    failed_days += 1
            fixtures = selected(fixtures)
            remember(espn_payloads, "events")
            if failed_days:
                for fixture in fixtures:
                    fixture["data_freshness"] = dict(fixture.get("data_freshness") or {}, state="degraded", warning="Some schedule dates could not be refreshed")
            if fixtures:
                logger.debug(
                    "[API_CLIENT] get_fixtures_for_date ESPN returned %d for %s", len(fixtures), date
                )
                return fixtures
        except Exception as exc:
            logger.debug("[API_CLIENT] get_fixtures_for_date ESPN failed: %s", exc)

    return []


def get_espn_fixtures(espn_slug: str, next_n: int = 20) -> list:
    """Fetch upcoming fixtures from ESPN for a given sport slug (e.g. 'FIFA.WC.2026').

    Used by the World Cup page and other non-league ESPN endpoints that are not
    covered by the standard API-Football provider.

    Args:
        espn_slug: ESPN competition slug string.
        next_n: Maximum number of upcoming fixtures to return.

    Returns:
        List of normalised fixture dicts (same shape as get_upcoming_fixtures).
    """
    # ESPN base for soccer scoreboard uses league slug in path
    soccer_base = "https://site.api.espn.com/apis/site/v2/sports/soccer"
    url = f"{soccer_base}/{espn_slug}/scoreboard"
    cache_key = f"espn_slug:{espn_slug}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    try:
        payload = _espn_get_json(url, cache_key, ttl_hours=0.5)
    except Exception:
        return []

    # Use league_id=0 as a sentinel — _normalize_espn_fixture will still work for
    # the teams/goals/fixture fields we need.
    fixtures = []
    for event in payload.get("events") or []:
        fixture = _normalize_espn_fixture(event, 0)
        if not fixture:
            continue
        fixtures.append(fixture)

    fixtures.sort(key=lambda f: str((f.get("fixture") or {}).get("date") or ""))
    return fixtures[:next_n]


def get_top_scorers(league_id: int = DEFAULT_LEAGUE_ID, season: int = CURRENT_SEASON) -> list:
    try:
        response = api_get(
            "players/topscorers",
            {"league": league_id, "season": season},
            cache_hours=6,
        ).get("response", [])
        if response:
            return response
    except Exception:
        pass
    return _espn_top_players(league_id, "totalGoals")


def get_top_assisters(league_id: int = DEFAULT_LEAGUE_ID, season: int = CURRENT_SEASON) -> list:
    try:
        response = api_get(
            "players/topassists",
            {"league": league_id, "season": season},
            cache_hours=6,
        ).get("response", [])
        if response:
            return response
    except Exception:
        pass
    return _espn_top_players(league_id, "goalAssists")


def enrich_fixture(fixture: dict) -> dict:
    fid = fixture["fixture"]["id"]
    return {
        **fixture,
        "events": get_fixture_events(fid),
        "stats":  get_fixture_stats(fid),
    }


def get_fixture_players(fixture_id: int) -> list:
    result = get_fixture_player_stats(fixture_id)
    return result if isinstance(result, list) else []


def get_fixture_prediction(fixture_id: int) -> list:
    try:
        response = api_get("predictions", {"fixture": fixture_id}, cache_hours=6).get("response", [])
        if response:
            return response
    except Exception:
        pass
    return []


def get_live_fixtures(league_id: int | None = None) -> list:
    results = []
    league_ids = [league_id] if league_id else [DEFAULT_LEAGUE_ID]
    for lid in league_ids:
        try:
            payload = _espn_get_json(
                f"{ESPN_BASE}/{_espn_slug(lid)}/scoreboard",
                f"live:{lid}:{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H')}",
                ttl_hours=0.016,
            )
        except Exception:
            continue
        for event in payload.get("events") or []:
            fixture = _normalize_espn_fixture(event, lid)
            if fixture and ((fixture.get("fixture") or {}).get("status") or {}).get("short") == "LIVE":
                results.append(fixture)
    return results


def get_today_fixtures(league_id: int | None = None) -> list:
    results = []
    league_ids = [league_id] if league_id else [DEFAULT_LEAGUE_ID]
    for lid in league_ids:
        try:
            payload = _espn_get_json(
                f"{ESPN_BASE}/{_espn_slug(lid)}/scoreboard",
                f"today:{lid}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                ttl_hours=1,
            )
        except Exception:
            continue
        for event in payload.get("events") or []:
            fixture = _normalize_espn_fixture(event, lid)
            if fixture:
                results.append(fixture)
    results.sort(key=lambda item: str((item.get("fixture") or {}).get("date") or ""))
    return results


def parse_stat(stats: list, team_id: int, stat_type: str):
    for team_stats in stats:
        if team_stats["team"]["id"] == team_id:
            for s in team_stats["statistics"]:
                if s["type"] == stat_type:
                    return s["value"]
    return None


# ── Player game log (props engine feed) ────────────────────────────────────────

def _build_game_row(
    fixture: dict,
    fp_data: list,
    player_id: int,
    team_id: int,
    league_id: int,
) -> dict | None:
    stats = _player_stats_entry(fp_data, player_id)
    if not stats:
        return None
    opp    = _opponent(fixture, team_id)
    goals  = _stat(stats, "goals", "total") or 0.0
    assists = _stat(stats, "goals", "assists") or 0.0
    return {
        "fixture_id":       _fixture_id(fixture),
        "date":             _fixture_date(fixture),
        "league_id":        league_id,
        "league_name":      _league_name(league_id),
        "difficulty":       COMP_DIFFICULTY.get(league_id, 1.00),
        "home_id":          ((fixture.get("teams") or {}).get("home") or {}).get("id"),
        "away_id":          ((fixture.get("teams") or {}).get("away") or {}).get("id"),
        "home_name":        ((fixture.get("teams") or {}).get("home") or {}).get("name"),
        "away_name":        ((fixture.get("teams") or {}).get("away") or {}).get("name"),
        "opponent_id":      opp.get("id"),
        "opponent":         opp.get("name"),
        "goals":            goals,
        "assists":          assists,
        "goal_or_assist":   goals + assists,
        "shots":            _stat(stats, "shots", "total"),
        "shots_on_target":  _stat(stats, "shots", "on"),
        "key_passes":       _stat(stats, "passes", "key"),
        "chances_created":  _stat(stats, "passes", "key"),
        "dribbles":         _stat(stats, "dribbles", "success"),
        "dribbles_completed": _stat(stats, "dribbles", "success"),
        "passes_completed": _stat(stats, "passes", "total"),
        "tackles":          _stat(stats, "tackles", "total"),
        "interceptions":    _stat(stats, "tackles", "interceptions"),
        "clearances":       _stat(stats, "tackles", "blocks"),
        "aerial_duels_won": _stat(stats, "duels", "won"),
        "yellow_card":      _stat(stats, "cards", "yellow"),
        "yellow_cards":     _stat(stats, "cards", "yellow"),
        "minutes":          _stat(stats, "games", "minutes"),
        "minutes_played":   _stat(stats, "games", "minutes"),
        "rating":           _stat(stats, "games", "rating"),
        "touches_opp_box":  None,   # not in standard endpoint
        "xg":               _stat(stats, "expected", "goals"),
        "xa":               _stat(stats, "expected", "assists"),
    }


def get_player_game_log(
    player_id: int,
    team_id: int,
    league_id: int,
    season: int = CURRENT_SEASON,
) -> list:
    """
    Per-game stat rows for a player in a single competition.
    Results cached in cache/props/player_{id}_league_{league_id}_season_{season}.json
    """
    PROPS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = PROPS_CACHE_DIR / f"player_{player_id}_league_{league_id}_season_{season}.json"
    if _cache_valid(path, PLAYER_LOG_TTL_H):
        return _load(path)

    try:
        fixtures = api_get(
            "fixtures",
            {"team": team_id, "league": league_id, "season": season, "status": "FT"},
            cache_hours=2,
        ).get("response", [])
    except Exception:
        fixtures = []

    if not fixtures:
        _save(path, [])
        return []

    rows: list[dict] = []
    for fixture in sorted(
        fixtures,
        key=lambda f: str((f.get("fixture") or {}).get("date") or ""),
        reverse=True,
    ):
        fid = _fixture_id(fixture)
        if not fid:
            continue
        fp = get_fixture_player_stats(fid, team_id=team_id)
        if not isinstance(fp, list):
            continue
        row = _build_game_row(fixture, fp, player_id, team_id, league_id)
        if row:
            rows.append(row)

    _save(path, rows)
    return rows


def get_player_vs_team(
    player_id: int,
    team_id: int,
    opponent_team_id: int,
    league_ids: list[int] | None = None,
    season: int = CURRENT_SEASON,
) -> dict:
    """Historical records of player_id vs opponent across leagues and past seasons."""
    league_ids = league_ids or list(SUPPORTED_LEAGUE_IDS)
    seasons    = [season - i for i in range(4)]
    games: list[dict] = []

    for season in seasons:
        for lid in league_ids:
            try:
                fixtures = api_get(
                    "fixtures",
                    {"team": team_id, "league": lid, "season": season, "status": "FT"},
                    cache_hours=6,
                ).get("response", [])
            except Exception:
                continue
            for fixture in fixtures:
                if str(_opponent(fixture, team_id).get("id")) != str(opponent_team_id):
                    continue
                fid = _fixture_id(fixture)
                if not fid:
                    continue
                fp = get_fixture_player_stats(fid, team_id=team_id)
                if not isinstance(fp, list):
                    continue
                row = _build_game_row(fixture, fp, player_id, team_id, lid)
                if row:
                    row["season"] = season
                    games.append(row)
    if not games:
        return {
            "games_played": 0,
            "goals": 0.0,
            "assists": 0.0,
            "shots": 0.0,
            "shots_on_target": 0.0,
            "key_passes": 0.0,
            "dribbles": 0.0,
            "avg_rating": 0.0,
            "games": [],
        }

    def _sum(metric: str) -> float:
        return round(sum(_sf(g.get(metric)) for g in games), 2)

    avg_rating = (
        round(sum(_sf(g.get("rating")) for g in games) / len(games), 2)
        if games else 0.0
    )
    return {
        "games_played":    len(games),
        "goals":           _sum("goals"),
        "assists":         _sum("assists"),
        "shots":           _sum("shots"),
        "shots_on_target": _sum("shots_on_target"),
        "key_passes":      _sum("key_passes"),
        "dribbles":        _sum("dribbles"),
        "avg_rating":      avg_rating,
        "games":           sorted(games, key=lambda g: g["date"], reverse=True),
    }


def get_player_season_stats_all_comps(
    player_id: int,
    team_id: int,
    season: int = CURRENT_SEASON,
) -> list:
    """Season stats for a player across all supported competitions."""
    try:
        resp = api_get("players", {"id": player_id, "season": season}, cache_hours=6).get("response", [])
        if not resp:
            raise RuntimeError("No API-Football data")
    except Exception:
        overview = _player_overview(_resolve_player_id(player_id)[0], DEFAULT_LEAGUE_ID)
        stats_root = overview.get("statistics") or {}
        names = stats_root.get("names") or []
        splits = stats_root.get("splits") or []
        breakdown = []
        for split in splits:
            league_slug = split.get("leagueSlug", "")
            league_id_match = next((lid for lid, slug in ESPN_SLUG_BY_LEAGUE.items() if slug == league_slug), None)
            if league_id_match not in SUPPORTED_LEAGUE_IDS:
                continue
            values = {name: split["stats"][idx] for idx, name in enumerate(names) if idx < len(split.get("stats") or [])}
            goals = _sf(values.get("totalGoals", 0))
            assists = _sf(values.get("goalAssists", 0))
            shots = _sf(values.get("totalShots", 0))
            appearances = _sf(values.get("starts", 0))
            breakdown.append({
                "player_id": _resolve_player_id(player_id)[0],
                "player_name": (overview.get("athlete") or {}).get("displayName", ""),
                "team_id": _si(split.get("teamId")),
                "team_name": split.get("displayName", ""),
                "league_id": league_id_match,
                "league_name": _league_name(league_id_match),
                "difficulty": COMP_DIFFICULTY.get(league_id_match, 1.0),
                "appearances": appearances,
                "minutes": 0.0,
                "goals": goals,
                "assists": assists,
                "shots": shots,
                "key_passes": 0.0,
                "goal_or_assist": goals + assists,
                "goals_per_90": goals,
                "assists_per_90": assists,
                "shots_per_90": shots,
                "kp_per_90": 0.0,
                "raw": split,
            })
        breakdown.sort(key=lambda item: item["league_name"])
        return breakdown

    breakdown: list[dict] = []
    for item in resp:
        player = item.get("player") or {}
        for stat in item.get("statistics") or []:
            league  = stat.get("league") or {}
            team    = stat.get("team")   or {}
            lid     = league.get("id")
            if lid not in SUPPORTED_LEAGUE_IDS:
                continue
            if team_id and str(team.get("id")) != str(team_id):
                continue
            games       = stat.get("games") or {}
            minutes     = _sf(games.get("minutes"))
            appearances = _sf(games.get("appearences") or games.get("appearances"))
            goals       = _sf((stat.get("goals")  or {}).get("total"))
            assists     = _sf((stat.get("goals")  or {}).get("assists"))
            shots       = _sf((stat.get("shots")  or {}).get("total"))
            key_passes  = _sf((stat.get("passes") or {}).get("key"))
            breakdown.append({
                "player_id":       player.get("id"),
                "player_name":     player.get("name"),
                "team_id":         team.get("id"),
                "team_name":       team.get("name"),
                "league_id":       lid,
                "league_name":     _league_name(lid),
                "difficulty":      COMP_DIFFICULTY.get(lid, 1.00),
                "appearances":     appearances,
                "minutes":         minutes,
                "goals":           goals,
                "assists":         assists,
                "shots":           shots,
                "key_passes":      key_passes,
                "goal_or_assist":  goals + assists,
                "goals_per_90":    round(goals   / minutes * 90, 3) if minutes > 0 else 0.0,
                "assists_per_90":  round(assists / minutes * 90, 3) if minutes > 0 else 0.0,
                "shots_per_90":    round(shots   / minutes * 90, 3) if minutes > 0 else 0.0,
                "kp_per_90":       round(key_passes / minutes * 90, 3) if minutes > 0 else 0.0,
                "raw": stat,
            })
    breakdown.sort(key=lambda x: x["league_name"])
    return breakdown


def get_opponent_defensive_stats(
    team_id: int,
    league_id: int = DEFAULT_LEAGUE_ID,
    season: int = CURRENT_SEASON,
) -> dict:
    """Opponent defensive profile from team statistics endpoint."""
    empty = {
        "league_id": league_id, "league_name": _league_name(league_id),
        "games": 0, "goals_conceded_pg": 0.0,
        "shots_conceded_pg": 0.0, "clean_sheets": 0, "cards_given_pg": 0.0,
    }
    try:
        resp = api_get(
            "teams/statistics",
            {"team": team_id, "league": league_id, "season": season},
            cache_hours=6,
        ).get("response", {})
        if not resp:
            raise RuntimeError("No API-Football team stats")
    except Exception:
        fixtures = get_team_fixtures(team_id, league_id, season, last=30)
        conceded = []
        for fixture in fixtures:
            teams = fixture.get("teams") or {}
            goals = fixture.get("goals") or {}
            home_id = _si((teams.get("home") or {}).get("id"))
            away_id = _si((teams.get("away") or {}).get("id"))
            home_goals = goals.get("home")
            away_goals = goals.get("away")
            if home_id == _resolve_team_id(team_id, league_id) and away_goals is not None:
                conceded.append(_si(away_goals))
            elif away_id == _resolve_team_id(team_id, league_id) and home_goals is not None:
                conceded.append(_si(home_goals))
        if not conceded:
            return empty
        clean_sheets = sum(1 for value in conceded if value == 0)
        return {
            "league_id": league_id,
            "league_name": _league_name(league_id),
            "games": len(conceded),
            "goals_conceded_pg": round(sum(conceded) / len(conceded), 2),
            "shots_conceded_pg": 0.0,
            "clean_sheets": clean_sheets,
            "cards_given_pg": 0.0,
        }

    ga_avg      = _sf(((resp.get("goals") or {}).get("against") or {}).get("average", {}).get("total"))
    clean       = _si((resp.get("clean_sheet") or {}).get("total"))
    played      = _si(((resp.get("fixtures") or {}).get("played") or {}).get("total"))
    return {
        "league_id":        league_id,
        "league_name":      _league_name(league_id),
        "games":            played,
        "goals_conceded_pg": round(ga_avg, 2),
        "shots_conceded_pg": 0.0,
        "clean_sheets":     clean,
        "cards_given_pg":   0.0,
    }


# ── Position / market helpers used by app.py ──────────────────────────────────

POSITION_DEFAULT_MARKETS: dict[str, list[str]] = {
    "Attacker":   ["goals", "shots", "shots_on_target", "assists", "goal_or_assist", "key_passes", "dribbles"],
    "Midfielder": ["key_passes", "passes_completed", "assists", "goal_or_assist", "dribbles", "tackles", "interceptions"],
    "Defender":   ["tackles", "interceptions", "clearances", "aerial_duels_won", "passes_completed"],
    "Goalkeeper": ["minutes", "yellow_cards"],
    "":           ["goals", "assists", "shots_on_target", "key_passes"],
}

_POS_MAP: dict[str, str] = {
    "g": "Goalkeeper", "gk": "Goalkeeper", "goalkeeper": "Goalkeeper",
    "d": "Defender",   "cb": "Defender",   "lb": "Defender", "rb": "Defender",
    "defender": "Defender", "back": "Defender",
    "m": "Midfielder", "cm": "Midfielder", "dm": "Midfielder", "am": "Midfielder",
    "midfielder": "Midfielder", "mid": "Midfielder", "wing": "Midfielder",
    "f": "Attacker",   "fw": "Attacker",   "st": "Attacker", "cf": "Attacker",
    "lw": "Attacker",  "rw": "Attacker",
    "attacker": "Attacker", "forward": "Attacker", "striker": "Attacker",
}


def normalize_position_group(pos: str) -> str:
    key = (pos or "").strip().lower()
    return _POS_MAP.get(key, _POS_MAP.get(key[:2], ""))


def get_market_catalog() -> list[dict]:
    """Return all supported football prop markets for the UI."""
    from props_engine import SOCCER_MARKETS
    return [
        {
            "key":      k,
            "label":    v[0],
            "abbr":     v[1],
            "per_90":   v[2],
            "category": (
                "attacking"  if k in {"goals","assists","goal_or_assist","shots_total","shots_on_target","dribbles","touches_opp_box","anytime_goalscorer"} else
                "chance"     if k in {"key_passes","chances_created","xG","xA"} else
                "midfield"   if k in {"passes_completed"} else
                "defensive"  if k in {"tackles","interceptions","clearances","aerial_duels_won"} else
                "universal"
            ),
        }
        for k, v in SOCCER_MARKETS.items()
    ]


def relevant_competitions_for_player(
    player_id: int,
    team_id: int,
    primary_league: int,
    season: int = CURRENT_SEASON,
) -> list[int]:
    """
    Return competition IDs where this player has season data, plus the primary league.
    """
    comps = get_player_season_stats_all_comps(player_id, team_id, season)
    ids = [primary_league]
    for entry in comps:
        lid = entry.get("league_id")
        if lid and lid not in ids:
            ids.append(lid)
    return ids


def prefetch_competition(
    player_id: int,
    team_id: int,
    league_id: int,
    season: int = CURRENT_SEASON,
) -> dict:
    """
    Pre-warm the player game-log cache for one competition.
    Returns a summary dict with game count.
    """
    rows = get_player_game_log(player_id, team_id, league_id, season)
    return {
        "league_id":   league_id,
        "league_name": _league_name(league_id),
        "games_found": len(rows),
        "status":      "ok",
    }
