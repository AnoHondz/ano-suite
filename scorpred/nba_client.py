"""
nba_client.py — API-NBA (RapidAPI) wrapper with JSON file caching.

Historical data: 6-hour TTL
Live / today data: 60-second TTL
Cache location: cache/nba/
"""

import os
import json
import hashlib
import time
import requests
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from dotenv import load_dotenv
from runtime_paths import cache_dir
from utils.parsing import safe_float as _safe_float, safe_int as _safe_int

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

NBA_CACHE_DIR = cache_dir("nba")
NBA_CACHE_HOURS = 6           # hours for historical data
NBA_LIVE_TTL_SECONDS = 60     # seconds for live/today data

NBA_API_KEY  = os.getenv("NBA_API_KEY", "").strip()
NBA_API_HOST = os.getenv("NBA_API_HOST", "nba-api-free-data.p.rapidapi.com").strip()
NBA_BASE_URL = os.getenv("NBA_API_BASE_URL", "https://nba-api-free-data.p.rapidapi.com").rstrip("/")
EXTERNAL_API_TIMEOUT_SECONDS = float(os.getenv("EXTERNAL_API_TIMEOUT_SECONDS", "8"))
EXTERNAL_API_RETRY_ATTEMPTS = max(1, int(os.getenv("EXTERNAL_API_RETRY_ATTEMPTS", "1")))
EXTERNAL_API_RETRY_BACKOFF_SECONDS = float(os.getenv("EXTERNAL_API_RETRY_BACKOFF_SECONDS", "0.5"))
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
# Endpoints that returned 429/403 this session — skip live calls, use stale cache only
_SUPPRESSED_ENDPOINTS: set[str] = set()

def current_season(today: date | None = None) -> int:
    """ESPN labels NBA seasons by their ending year (October 2026 -> 2027)."""
    today = today or datetime.now().date()
    return today.year + 1 if today.month >= 8 else today.year


NBA_SEASON = current_season()


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _cache_path(endpoint: str, params: dict) -> Path:
    raw = endpoint + str(sorted((params or {}).items()))
    key = hashlib.md5(raw.encode()).hexdigest()
    NBA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return NBA_CACHE_DIR / f"{key}.json"


def _cache_valid(path: Path, ttl_seconds: int) -> bool:
    if not path.exists():
        return False
    age_secs = (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)).total_seconds()
    return age_secs < ttl_seconds


def _save_cache(path: Path, data: dict) -> None:
    NBA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _load_cache(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        raise ValueError(f"Empty cache file: {path}")
    return json.loads(content)


# ── Core request ───────────────────────────────────────────────────────────────

def nba_get(endpoint: str, params: dict = None, ttl_seconds: int = None) -> dict:
    """
    GET request to API-NBA. Returns cached JSON when fresh.
    ttl_seconds defaults to 6 hours for historical, pass NBA_LIVE_TTL_SECONDS for live data.
    """
    params = params or {}
    if ttl_seconds is None:
        ttl_seconds = NBA_CACHE_HOURS * 3600

    path = _cache_path(endpoint, params)

    if _cache_valid(path, ttl_seconds):
        return _load_cache(path)

    if not NBA_API_KEY:
        raise RuntimeError("NBA_API_KEY not set in .env")

    # Skip endpoints that already failed this session; serve stale if available
    if endpoint in _SUPPRESSED_ENDPOINTS:
        if path.exists():
            return _load_cache(path)
        raise RuntimeError(f"NBA endpoint suppressed (rate-limited/forbidden): {endpoint}")

    headers = {
        "x-rapidapi-host": NBA_API_HOST,
        "x-rapidapi-key": NBA_API_KEY,
        "Content-Type": "application/json",
    }
    url = f"{NBA_BASE_URL}/{endpoint.lstrip('/')}"
    last_exc: Exception | None = None
    for attempt in range(EXTERNAL_API_RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=EXTERNAL_API_TIMEOUT_SECONDS)
            if resp.status_code in {429, 403}:
                _SUPPRESSED_ENDPOINTS.add(endpoint)
                if path.exists():
                    return _load_cache(path)
                raise RuntimeError(f"NBA API rate-limited/forbidden for {endpoint} ({resp.status_code})")
            if resp.status_code in RETRY_STATUS_CODES and attempt < EXTERNAL_API_RETRY_ATTEMPTS - 1:
                time.sleep(EXTERNAL_API_RETRY_BACKOFF_SECONDS * (2 ** attempt))
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < EXTERNAL_API_RETRY_ATTEMPTS - 1:
                time.sleep(EXTERNAL_API_RETRY_BACKOFF_SECONDS * (2 ** attempt))
                continue
    else:
        if path.exists():
            return _load_cache(path)
        raise RuntimeError(f"API-NBA request failed for {endpoint}") from last_exc

    errors = data.get("errors", [])
    if errors and errors != [] and errors != {}:
        raise RuntimeError(f"API-NBA error for {endpoint}: {errors}")

    _save_cache(path, data)
    return data


# ── Domain methods ─────────────────────────────────────────────────────────────

def get_teams() -> list:
    """All active NBA teams."""
    data = nba_get("teams", {"league": "standard"})
    teams = data.get("response", [])
    # Filter to only real NBA teams (exclude G-League / summer etc.)
    return [t for t in teams if t.get("nbaFranchise") and not t.get("allStar")]


def get_team_roster(team_id: int, season: int = NBA_SEASON) -> list:
    """Full squad (players) for a team in a given season."""
    data = nba_get("players", {"team": team_id, "season": season})
    return data.get("response", [])


def get_h2h(team_a_id: int, team_b_id: int, season: int = NBA_SEASON) -> list:
    """Last 10 head-to-head meetings between two teams, most recent first."""
    data = nba_get("games", {"h2h": f"{team_a_id}-{team_b_id}", "season": season})
    games = data.get("response", [])
    finished = [g for g in games if g.get("status", {}).get("long") == "Finished"]
    return sorted(finished, key=lambda g: g["date"]["start"], reverse=True)[:10]


def get_game_stats(game_id: int) -> list:
    """Box score + per-player stats for a single game."""
    data = nba_get("games/statistics", {"id": game_id})
    return data.get("response", [])


def get_player_stats(player_id: int, season: int = NBA_SEASON) -> list:
    """
    Per-game statistics for a player in a season.
    Returns list of game-by-game stat records (most recent last).
    """
    data = nba_get("players/statistics", {"id": player_id, "season": season})
    records = data.get("response", [])
    # Sort chronologically
    return sorted(records, key=lambda r: r.get("game", {}).get("id", 0))


def get_player_season_averages(player_id: int, season: int = NBA_SEASON) -> dict | None:
    """
    Compute season averages from per-game stats.
    Returns a single dict of averages, or None if no data.
    """
    games = get_player_stats(player_id, season)
    games = [g for g in games if _has_played(g)]
    if not games:
        return None

    n = len(games)
    fields = ["points", "rebounds", "assists", "steals", "blocks",
              "turnovers", "fgm", "fga", "ftm", "fta",
              "tpm", "tpa", "offReb", "defReb", "pFouls"]
    totals = {f: 0.0 for f in fields}
    min_total = 0.0

    for g in games:
        s = g.get("statistics", [{}])[0] if g.get("statistics") else {}
        for f in fields:
            totals[f] += _safe_float(s.get(f, 0))
        min_total += _parse_minutes(s.get("min", "0"))

    avgs = {f: round(totals[f] / n, 1) for f in fields}
    avgs["min"] = round(min_total / n, 1)
    avgs["games"] = n
    avgs["fgp"] = _pct(totals["fgm"], totals["fga"])
    avgs["ftp"] = _pct(totals["ftm"], totals["fta"])
    avgs["tpp"] = _pct(totals["tpm"], totals["tpa"])
    avgs["pra"] = round(avgs["points"] + avgs["rebounds"] + avgs["assists"], 1)
    return avgs


def get_player_last_n_games(player_id: int, season: int = NBA_SEASON, n: int = 5) -> list:
    """Return the last N game records for a player."""
    games = get_player_stats(player_id, season)
    played = [g for g in games if _has_played(g)]
    return played[-n:] if len(played) >= n else played


def get_injuries() -> list:
    """Current NBA injury report."""
    data = nba_get("injuries", {}, ttl_seconds=NBA_LIVE_TTL_SECONDS * 10)
    return data.get("response", [])


def get_team_injuries(team_id: int) -> list:
    """Current injuries for a specific team."""
    all_inj = get_injuries()
    return [i for i in all_inj if i.get("team", {}).get("id") == team_id]


def get_today_games() -> list:
    """Today's scheduled and live NBA games (60-second cache)."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        data = nba_get("games", {"date": today}, ttl_seconds=NBA_LIVE_TTL_SECONDS)
        return data.get("response", [])
    except Exception:
        return []


def get_scoreboard() -> dict:
    """
    Live scoreboard for today's games.
    Falls back to estimated data if endpoint unavailable.
    """
    games = get_today_games()
    if games:
        return {"games": games, "data_source": "live"}
    return {"games": [], "data_source": "estimated", "note": "No live games or endpoint unavailable"}


def get_schedule(team_id: int, season: int = NBA_SEASON) -> list:
    """
    Full season schedule for a team.
    Falls back gracefully if endpoint unavailable on current plan.
    """
    try:
        data = nba_get("games", {"team": team_id, "season": season},
                       ttl_seconds=NBA_CACHE_HOURS * 3600)
        games = data.get("response", [])
        if games:
            return sorted(games, key=lambda g: g.get("date", {}).get("start", ""))
        # Endpoint returned empty — not necessarily an error
        return []
    except Exception as exc:
        import logging
        logging.warning("NBA endpoint /games?team=%s not available on current plan: %s", team_id, exc)
        return []


def get_team_season_stats(team_id: int, season: int = NBA_SEASON) -> dict | None:
    """
    Derive season-level team stats from recent form games.
    Returns aggregated offensive/defensive ratings, pace, etc.
    """
    games = get_team_recent_form(team_id, season, n=20)
    if not games:
        return None

    pts_scored, pts_allowed, wins, losses = 0.0, 0.0, 0, 0
    home_w, home_l, away_w, away_l = 0, 0, 0, 0
    three_made, fga_total, tov_total, reb_total = 0.0, 0.0, 0.0, 0.0
    count = 0

    for g in games:
        home_team_id = g.get("teams", {}).get("home", {}).get("id")
        is_home = (home_team_id == team_id)
        scores = g.get("scores", {})
        our_key   = "home" if is_home else "visitors"
        their_key = "visitors" if is_home else "home"

        our_pts   = _safe_int(scores.get(our_key,   {}).get("points"))
        their_pts = _safe_int(scores.get(their_key, {}).get("points"))
        if our_pts is None or their_pts is None:
            continue

        count += 1
        pts_scored  += our_pts
        pts_allowed += their_pts
        won = our_pts > their_pts
        if won:
            wins += 1
            if is_home: home_w += 1
            else: away_w += 1
        else:
            losses += 1
            if is_home: home_l += 1
            else: away_l += 1

    if count == 0:
        return None

    return {
        "games": count,
        "wins": wins,
        "losses": losses,
        "ppg": round(pts_scored / count, 1),
        "opp_ppg": round(pts_allowed / count, 1),
        "net_rtg": round((pts_scored - pts_allowed) / count, 1),
        "home_record": f"{home_w}-{home_l}",
        "away_record": f"{away_w}-{away_l}",
    }


# ── Utility helpers ────────────────────────────────────────────────────────────

def _has_played(game_stat: dict) -> bool:
    """Return True if the player actually played (has points/min data)."""
    s = game_stat.get("statistics", [{}])[0] if game_stat.get("statistics") else {}
    raw_min = s.get("min", "0") or "0"
    return raw_min not in ("0", "0:00", "", None)


def _pct(made, attempted) -> str:
    if not attempted:
        return "0.0"
    return str(round(100.0 * made / attempted, 1))


def _parse_minutes(raw: str) -> float:
    """Convert '32:15' or '32' to float minutes."""
    if not raw:
        return 0.0
    parts = str(raw).split(":")
    try:
        mins = float(parts[0])
        secs = float(parts[1]) / 60 if len(parts) > 1 else 0.0
        return mins + secs
    except (ValueError, IndexError):
        return 0.0


def get_player_game_log(player_id: int, season: int = NBA_SEASON, n: int = 10) -> list:
    """Return last N game records enriched with opponent name + date."""
    all_stats = get_player_stats(player_id, season)
    played = [g for g in all_stats if _has_played(g)]
    recent = played[-n:]
    return list(reversed(recent))  # most recent first


def format_game_stat_line(stat_record: dict) -> dict:
    """Flatten a player statistics record into a simple display dict."""
    s = stat_record.get("statistics", [{}])[0] if stat_record.get("statistics") else {}
    game_info = stat_record.get("game", {})
    team_info = stat_record.get("team", {})
    return {
        "game_id":   game_info.get("id"),
        "date":      game_info.get("date", ""),
        "team_name": team_info.get("name", ""),
        "points":    _safe_int(s.get("points")) or 0,
        "rebounds":  _safe_int(s.get("totReb", s.get("rebounds"))) or 0,
        "assists":   _safe_int(s.get("assists")) or 0,
        "steals":    _safe_int(s.get("steals")) or 0,
        "blocks":    _safe_int(s.get("blocks")) or 0,
        "turnovers": _safe_int(s.get("turnovers")) or 0,
        "tpm":       _safe_int(s.get("tpm")) or 0,
        "fgm":       _safe_int(s.get("fgm")) or 0,
        "fga":       _safe_int(s.get("fga")) or 0,
        "fgp":       s.get("fgp", "0.0"),
        "min":       s.get("min", "0"),
        "pra":       (_safe_int(s.get("points")) or 0)
                     + (_safe_int(s.get("totReb", s.get("rebounds"))) or 0)
                     + (_safe_int(s.get("assists")) or 0),
    }


# ============================================================================
# Rewritten provider implementation for nba-api-free-data.p.rapidapi.com
# ============================================================================

import re
from typing import Any


DIVISION_ENDPOINTS: list[tuple[str, str, str]] = [
    ("atlantic", "east", "nba-atlantic-team-list"),
    ("central", "east", "nba-central-team-list"),
    ("southeast", "east", "nba-southeast-team-list"),
    ("northwest", "west", "nba-northwest-team-list"),
    ("pacific", "west", "nba-pacific-team-list"),
    ("southwest", "west", "nba-southwest-team-list"),
]

ROUTE_FEATURES: dict[str, list[str]] = {
    "index": ["teams", "scoreboard"],
    "matchup": ["team_stats", "h2h", "form", "injuries"],
    "player": ["roster", "player_stats"],
    "prediction": ["team_stats", "h2h", "form", "injuries"],
    "standings": ["standings"],
}

MOCK_FIRST_NAMES = [
    "Alex",
    "Jordan",
    "Marcus",
    "Jalen",
    "Tyler",
    "Devin",
    "Noah",
    "Miles",
    "Cameron",
    "Isaiah",
    "Malik",
    "Evan",
]

MOCK_LAST_NAMES = [
    "Carter",
    "Walker",
    "Hill",
    "Coleman",
    "Parker",
    "Hayes",
    "Brooks",
    "Bell",
    "Foster",
    "Graham",
    "Price",
    "Reed",
]

MOCK_PLAYER_SLOTS: list[dict[str, Any]] = [
    {"pos": "PG", "pts": 23.0, "reb": 4.3, "ast": 7.8, "stl": 1.5, "blk": 0.3, "tpm": 2.9, "min": 35},
    {"pos": "SG", "pts": 19.5, "reb": 4.8, "ast": 3.6, "stl": 1.1, "blk": 0.4, "tpm": 2.6, "min": 33},
    {"pos": "SF", "pts": 16.8, "reb": 6.2, "ast": 3.4, "stl": 1.0, "blk": 0.5, "tpm": 1.8, "min": 32},
    {"pos": "PF", "pts": 14.1, "reb": 7.7, "ast": 2.8, "stl": 0.9, "blk": 0.8, "tpm": 1.1, "min": 31},
    {"pos": "C", "pts": 12.6, "reb": 10.2, "ast": 2.1, "stl": 0.8, "blk": 1.6, "tpm": 0.3, "min": 30},
    {"pos": "G", "pts": 10.7, "reb": 3.1, "ast": 2.9, "stl": 0.7, "blk": 0.2, "tpm": 1.7, "min": 24},
    {"pos": "F", "pts": 9.4, "reb": 5.6, "ast": 2.1, "stl": 0.6, "blk": 0.5, "tpm": 1.0, "min": 23},
    {"pos": "C", "pts": 8.1, "reb": 6.8, "ast": 1.5, "stl": 0.5, "blk": 1.0, "tpm": 0.1, "min": 20},
    {"pos": "G", "pts": 7.2, "reb": 2.4, "ast": 2.6, "stl": 0.6, "blk": 0.1, "tpm": 1.3, "min": 19},
    {"pos": "F", "pts": 6.6, "reb": 4.5, "ast": 1.8, "stl": 0.5, "blk": 0.4, "tpm": 0.8, "min": 18},
]


class NbaProviderError(RuntimeError):
    """Base provider error."""


class MissingEndpointError(NbaProviderError):
    """Raised when the provider returns a route-does-not-exist message."""


class ProviderRequestError(NbaProviderError):
    """Raised when the provider returns an explicit error payload."""


def _infer_current_season_year(now: datetime | None = None) -> int:
    now = now or datetime.now()
    return now.year + 1 if now.month >= 8 else now.year


NBA_API_KEY = os.getenv("NBA_API_KEY", "").strip()
NBA_API_HOST = os.getenv("NBA_API_HOST", "nba-api-free-data.p.rapidapi.com").strip()
NBA_BASE_URL = os.getenv(
    "NBA_API_BASE_URL",
    "https://nba-api-free-data.p.rapidapi.com",
).rstrip("/")
NBA_SEASON = int(os.getenv("NBA_SEASON", str(_infer_current_season_year())))


def _season_label(season: int) -> str:
    return f"{season - 1}-{str(season)[-2:]}"


def _request_headers() -> dict[str, str]:
    return {
        "x-rapidapi-key": NBA_API_KEY,
        "x-rapidapi-host": NBA_API_HOST,
        "Content-Type": "application/json",
    }


def _feature_catalog() -> dict[str, dict[str, str]]:
    season_label = _season_label(NBA_SEASON)
    return {
        "teams": {
            "state": "live",
            "title": "Live team directory",
            "detail": "Using the six division endpoints exposed by this provider.",
        },
        "standings": {
            "state": "live",
            "title": "Live standings",
            "detail": f"The provider does not expose plain /standings, so the client uses live /nba-league-standings?year={NBA_SEASON} for the {season_label} season.",
        },
        "team_stats": {
            "state": "live",
            "title": "Season team stats",
            "detail": f"PPG, opponent PPG, net rating, home/away records, and last-10 form are derived from /nba-league-standings?year={NBA_SEASON}.",
        },
        "scoreboard": {
            "state": "mock",
            "title": "Mock daily slate",
            "detail": "The provider's /scoreboard and /schedule routes do not exist, so the app shows a clearly flagged synthetic slate.",
        },
        "h2h": {
            "state": "mock",
            "title": "Mock matchup history",
            "detail": "No working head-to-head or game-history endpoint is available, so H2H tables are deterministic mock data built from live team strength.",
        },
        "form": {
            "state": "mock",
            "title": "Mock recent form",
            "detail": "No working team schedule or game-log route is exposed on this provider, so recent form is simulated from live standings signals.",
        },
        "injuries": {
            "state": "fallback",
            "title": "No live injury feed",
            "detail": "This provider does not expose a usable injuries endpoint, so injury sections remain placeholder-only and are flagged in the UI.",
        },
        "roster": {
            "state": "mock",
            "title": "Mock rosters",
            "detail": "The provider's player endpoints return request failures, so roster dropdowns are populated with deterministic mock players per team.",
        },
        "player_stats": {
            "state": "mock",
            "title": "Mock player props data",
            "detail": "Season averages, recent logs, and vs-opponent splits are synthetic because /nba-player-info and /nba-player-gamelog do not return usable data.",
        },
    }


def get_route_support(route_name: str) -> dict[str, dict[str, str]]:
    catalog = _feature_catalog()
    return {
        key: catalog[key]
        for key in ROUTE_FEATURES.get(route_name, [])
        if key in catalog
    }


def _request_json(
    endpoint: str,
    params: dict[str, Any] | None = None,
    ttl_seconds: int | None = None,
) -> Any:
    params = params or {}
    ttl_seconds = ttl_seconds or NBA_CACHE_HOURS * 3600
    path = _cache_path(endpoint, params)

    if _cache_valid(path, ttl_seconds):
        return _load_cache(path)

    if not NBA_API_KEY:
        if path.exists():
            return _load_cache(path)
        raise RuntimeError("NBA_API_KEY is not set in .env")

    # Skip endpoints suppressed this session; serve stale if available
    if endpoint in _SUPPRESSED_ENDPOINTS:
        if path.exists():
            return _load_cache(path)
        raise RuntimeError(f"NBA endpoint suppressed (rate-limited/forbidden): {endpoint}")

    url = f"{NBA_BASE_URL}/{endpoint.lstrip('/')}"
    last_exc: Exception | None = None
    for attempt in range(EXTERNAL_API_RETRY_ATTEMPTS):
        try:
            response = requests.get(
                url,
                headers=_request_headers(),
                params=params,
                timeout=EXTERNAL_API_TIMEOUT_SECONDS,
            )
            if response.status_code in {429, 403}:
                _SUPPRESSED_ENDPOINTS.add(endpoint)
                if path.exists():
                    return _load_cache(path)
                raise RuntimeError(f"NBA provider rate-limited/forbidden for {endpoint} ({response.status_code})")
            if response.status_code in RETRY_STATUS_CODES and attempt < EXTERNAL_API_RETRY_ATTEMPTS - 1:
                time.sleep(EXTERNAL_API_RETRY_BACKOFF_SECONDS * (2 ** attempt))
                continue
            response.raise_for_status()
            payload = response.json()
            break
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < EXTERNAL_API_RETRY_ATTEMPTS - 1:
                time.sleep(EXTERNAL_API_RETRY_BACKOFF_SECONDS * (2 ** attempt))
                continue
    else:
        if path.exists():
            return _load_cache(path)
        raise RuntimeError(f"NBA provider request failed for {endpoint}") from last_exc

    if isinstance(payload, dict):
        message = str(payload.get("message", ""))
        if "does not exist" in message:
            raise MissingEndpointError(message)
        error = payload.get("error")
        if error:
            raise ProviderRequestError(str(error))

    _save_cache(path, payload)
    return payload


def nba_get(endpoint: str, params: dict | None = None, ttl_seconds: int | None = None) -> Any:
    return _request_json(endpoint, params=params, ttl_seconds=ttl_seconds)


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _split_record(raw: str | None) -> tuple[int, int]:
    match = re.match(r"^\s*(\d+)-(\d+)\s*$", raw or "")
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def _split_streak(raw: str | None) -> tuple[str, int]:
    match = re.match(r"^\s*([WL])(\d+)\s*$", raw or "")
    if not match:
        return "", 0
    return match.group(1), int(match.group(2))


def _stable_unit(*parts: Any) -> float:
    raw = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _stable_int(minimum: int, maximum: int, *parts: Any) -> int:
    if maximum <= minimum:
        return minimum
    span = maximum - minimum + 1
    return minimum + int(_stable_unit(*parts) * span) % span

# -- Standings fetch (internal) -------------------------------------------

def _fetch_standings_entries(season: int = NBA_SEASON) -> list:
    payload = _request_json("nba-league-standings", {"year": season})
    return payload.get("response", {}).get("standings", {}).get("entries", [])

def _entry_stats(entry: dict) -> dict:
    return {s["name"]: s.get("value", 0) for s in entry.get("stats", [])}

def _entry_display_stats(entry: dict) -> dict:
    return {s["name"]: s.get("displayValue", "") for s in entry.get("stats", [])}

def _team_from_entry(entry: dict, division: str = "", conference: str = "") -> dict:
    t = entry["team"]
    logos = t.get("logos", [])
    logo_url = logos[0]["href"] if logos else ""
    return {
        "id":         t["id"],
        "name":       t.get("displayName", t.get("name", "")),
        "nickname":   t.get("name", ""),
        "shortName":  t.get("shortDisplayName", ""),
        "abbrev":     t.get("abbreviation", ""),
        "city":       t.get("location", ""),
        "logo":       logo_url,
        "division":   division,
        "conference": conference,
    }

# -- Teams (live) -----------------------------------------------------------

def get_teams() -> list:
    teams: list = []
    seen: set = set()
    for division, conference, endpoint in DIVISION_ENDPOINTS:
        try:
            payload = _request_json(endpoint)
            for t in payload.get("response", {}).get("teamList", []):
                if t["id"] in seen:
                    continue
                seen.add(t["id"])
                teams.append({
                    "id":         t["id"],
                    "name":       t.get("name", ""),
                    "nickname":   t.get("shortName", ""),
                    "shortName":  t.get("shortName", ""),
                    "abbrev":     t.get("abbrev", ""),
                    "city":       t.get("name", "").replace(t.get("shortName", ""), "").strip(),
                    "logo":       t.get("logo", ""),
                    "logoDark":   t.get("logoDark", ""),
                    "division":   division,
                    "conference": conference,
                })
        except Exception:
            continue
    teams.sort(key=lambda t: t["name"])
    return teams


def _teams_by_id() -> dict:
    return {t["id"]: t for t in get_teams()}


# -- Standings (live) -------------------------------------------------------

def get_standings(season: int = NBA_SEASON) -> dict:
    entries     = _fetch_standings_entries(season)
    team_lookup = _teams_by_id()
    east, west  = [], []

    for entry in entries:
        tid   = entry["team"]["id"]
        st    = _entry_stats(entry)
        dst   = _entry_display_stats(entry)
        tinfo = team_lookup.get(tid, {})

        wins   = _parse_int(st.get("wins", 0))
        losses = _parse_int(st.get("losses", 0))
        total  = wins + losses or 1

        streak_dir, streak_n = _split_streak(dst.get("streak", ""))
        home_w, home_l = _split_record(dst.get("Home"))
        away_w, away_l = _split_record(dst.get("Road"))
        l10_w,  l10_l  = _split_record(dst.get("Last Ten Games"))

        row = {
            "team":           tinfo or _team_from_entry(entry),
            "conference":     tinfo.get("conference", ""),
            "division":       tinfo.get("division", ""),
            "wins":           wins,
            "losses":         losses,
            "win_pct":        round(wins / total, 3),
            "ppg":            round(_parse_float(st.get("avgPointsFor",     0)), 1),
            "opp_ppg":        round(_parse_float(st.get("avgPointsAgainst", 0)), 1),
            "net_rtg":        round(_parse_float(st.get("differential",     0)), 1),
            "seed":           _parse_int(st.get("playoffSeed", 0)),
            "games_behind":   dst.get("gamesBehind", "-"),
            "streak":         dst.get("streak", ""),
            "streak_dir":     streak_dir,
            "streak_n":       streak_n,
            "home_w":         home_w,  "home_l": home_l,
            "away_w":         away_w,  "away_l": away_l,
            "last10_w":       l10_w,   "last10_l": l10_l,
            "last10":         dst.get("Last Ten Games", ""),
            "home_record":    dst.get("Home", ""),
            "away_record":    dst.get("Road", ""),
            "points_for":     _parse_int(st.get("pointsFor", 0)),
            "points_against": _parse_int(st.get("pointsAgainst", 0)),
        }

        conf = tinfo.get("conference", "")
        if conf == "east":
            east.append(row)
        elif conf == "west":
            west.append(row)
        else:
            (east if len(east) <= len(west) else west).append(row)

    east.sort(key=lambda r: (-r["wins"], r["losses"]))
    west.sort(key=lambda r: (-r["wins"], r["losses"]))
    return {"east": east, "west": west}


# -- Team season stats (derived from live standings) -----------------------

def get_team_season_stats(team_id, season: int = NBA_SEASON):
    team_id = str(team_id)
    for entry in _fetch_standings_entries(season):
        if str(entry["team"]["id"]) != team_id:
            continue
        st  = _entry_stats(entry)
        dst = _entry_display_stats(entry)
        wins   = _parse_int(st.get("wins", 0))
        losses = _parse_int(st.get("losses", 0))
        total  = wins + losses or 1
        home_w, home_l = _split_record(dst.get("Home"))
        away_w, away_l = _split_record(dst.get("Road"))
        l10_w,  l10_l  = _split_record(dst.get("Last Ten Games"))
        return {
            "games":       total,
            "wins":        wins,
            "losses":      losses,
            "win_pct":     round(wins / total, 3),
            "ppg":         round(_parse_float(st.get("avgPointsFor",     0)), 1),
            "opp_ppg":     round(_parse_float(st.get("avgPointsAgainst", 0)), 1),
            "net_rtg":     round(_parse_float(st.get("differential",     0)), 1),
            "home_record": f"{home_w}-{home_l}",
            "away_record": f"{away_w}-{away_l}",
            "home_w": home_w, "home_l": home_l,
            "away_w": away_w, "away_l": away_l,
            "last10":      dst.get("Last Ten Games", ""),
            "last10_w": l10_w, "last10_l": l10_l,
            "streak":      dst.get("streak", ""),
        }
    return None


# -- H2H (mock, seeded from live win-pct) ----------------------------------

def get_h2h(team_a_id, team_b_id, season: int = NBA_SEASON) -> list:
    team_a_id = str(team_a_id)
    team_b_id = str(team_b_id)
    stats_a   = get_team_season_stats(team_a_id, season) or {}
    stats_b   = get_team_season_stats(team_b_id, season) or {}
    teams_map = _teams_by_id()
    ta = teams_map.get(team_a_id, {"id": team_a_id, "name": "Team A", "logo": ""})
    tb = teams_map.get(team_b_id, {"id": team_b_id, "name": "Team B", "logo": ""})

    win_pct_a = stats_a.get("win_pct", 0.5)
    base_pts  = int((stats_a.get("ppg", 110.0) + stats_b.get("ppg", 110.0)) / 2)

    games = []
    for i in range(10):
        month     = 4 - (i % 4)
        day       = 1 + (i * 7) % 28
        date      = f"{2025 - i // 4}-{month:02d}-{day:02d}"
        is_home_a = (i % 2 == 0)
        a_wins    = _stable_unit(team_a_id, team_b_id, i) < win_pct_a

        a_pts = base_pts + _stable_int(-8, 14, team_a_id, team_b_id, i, "a")
        b_pts = base_pts + _stable_int(-8, 14, team_a_id, team_b_id, i, "b")
        if a_wins and a_pts <= b_pts:
            a_pts, b_pts = b_pts + 2, a_pts
        elif not a_wins and b_pts <= a_pts:
            a_pts, b_pts = b_pts, a_pts + 2

        a_qs = [str(_stable_int(22, 34, team_a_id, i, q)) for q in range(4)]
        b_qs = [str(_stable_int(22, 34, team_b_id, i, q)) for q in range(4)]

        ht   = ta if is_home_a else tb
        vt   = tb if is_home_a else ta
        hpts = a_pts if is_home_a else b_pts
        vpts = b_pts if is_home_a else a_pts

        games.append({
            "id":      f"mock-{team_a_id}-{team_b_id}-{i}",
            "date":    {"start": date},
            "is_mock": True,
            "teams": {
                "home":     {"id": ht["id"],  "name": ht["name"],
                             "logo": ht.get("logo", "")},
                "visitors": {"id": vt["id"],  "name": vt["name"],
                             "logo": vt.get("logo", "")},
            },
            "scores": {
                "home":     {"points": hpts, "linescore": a_qs if is_home_a else b_qs},
                "visitors": {"points": vpts, "linescore": b_qs if is_home_a else a_qs},
            },
            "status": {"long": "Finished"},
        })
    return games


# -- Recent form (mock, seeded from live last-10) --------------------------

def get_team_recent_form(team_id, season: int = NBA_SEASON, n: int = 10) -> list:
    team_id   = str(team_id)
    stats     = get_team_season_stats(team_id, season) or {}
    teams_map = _teams_by_id()
    all_teams = sorted(teams_map.values(), key=lambda t: t["id"])
    own       = teams_map.get(team_id, {"id": team_id, "name": "Team", "logo": ""})
    opponents = [t for t in all_teams if t["id"] != team_id]

    l10_w  = stats.get("last10_w", 5)
    ppg    = stats.get("ppg",     110.0)
    opp_pg = stats.get("opp_ppg", 110.0)

    games = []
    for i in range(n):
        opp     = opponents[_stable_int(0, len(opponents) - 1, team_id, i, "opp")]
        month   = max(1, 3 - (i // 4))
        day     = max(1, 28 - (i * 3) % 26)
        date    = f"2025-{month:02d}-{day:02d}"
        is_home = (i % 2 == 0)
        won     = (i < l10_w)

        our_pts = max(80, round(ppg    + _stable_int(-10, 12, team_id, i, "us")))
        opp_pts = max(80, round(opp_pg + _stable_int(-10, 12, team_id, i, "them")))
        if won and our_pts <= opp_pts:
            our_pts, opp_pts = opp_pts + 3, our_pts
        elif not won and opp_pts <= our_pts:
            our_pts, opp_pts = opp_pts, our_pts + 3

        games.append({
            "id":      f"mock-form-{team_id}-{i}",
            "date":    {"start": date},
            "is_mock": True,
            "teams": {
                "home": {
                    "id":   team_id if is_home else opp["id"],
                    "name": own["name"] if is_home else opp["name"],
                    "logo": own.get("logo", "") if is_home else opp.get("logo", ""),
                },
                "visitors": {
                    "id":   opp["id"] if is_home else team_id,
                    "name": opp["name"] if is_home else own["name"],
                    "logo": opp.get("logo", "") if is_home else own.get("logo", ""),
                },
            },
            "scores": {
                "home":     {"points": our_pts if is_home else opp_pts, "linescore": []},
                "visitors": {"points": opp_pts if is_home else our_pts, "linescore": []},
            },
            "status": {"long": "Finished"},
        })
    return games


# -- Roster (mock, 10 players per team) ------------------------------------

def get_team_roster(team_id, season: int = NBA_SEASON) -> list:
    team_id = str(team_id)
    roster  = []
    for slot_i, slot in enumerate(MOCK_PLAYER_SLOTS):
        fi = _stable_int(0, len(MOCK_FIRST_NAMES) - 1, team_id, slot_i, "fn")
        li = _stable_int(0, len(MOCK_LAST_NAMES)  - 1, team_id, slot_i, "ln")
        roster.append({
            "id":        f"mock-{team_id}-{slot_i}",
            "firstname": MOCK_FIRST_NAMES[fi],
            "lastname":  MOCK_LAST_NAMES[li],
            "is_mock":   True,
            "leagues":   {"standard": {"pos": slot["pos"], "active": True}},
            "_slot":     slot_i,
        })
    return roster


# -- Player stats (mock) ---------------------------------------------------

def get_player_season_averages(player_id, season: int = NBA_SEASON):
    player_id = str(player_id)
    if not player_id.startswith("mock-"):
        return None
    try:
        slot_i = int(player_id.split("-")[-1])
    except (ValueError, IndexError):
        return None

    slot  = MOCK_PLAYER_SLOTS[slot_i % len(MOCK_PLAYER_SLOTS)]
    noise = (_stable_unit(player_id, "avg") - 0.5) * 3

    pts = round(max(0.0, slot["pts"] + noise), 1)
    reb = round(max(0.0, slot["reb"] + noise * 0.3), 1)
    ast = round(max(0.0, slot["ast"] + noise * 0.4), 1)
    tpm = round(max(0.0, slot["tpm"] + noise * 0.2), 1)
    stl = round(max(0.0, slot["stl"] + noise * 0.1), 1)
    blk = round(max(0.0, slot["blk"] + noise * 0.1), 1)
    tov = round(max(0.5, 2.5 - noise * 0.2), 1)
    fgp = round(min(62.0, max(35.0, 46.0 + noise)), 1)
    tpp = round(min(50.0, max(28.0, 36.0 + noise)), 1)
    ftp = round(min(95.0, max(60.0, 78.0 + noise)), 1)

    return {
        "is_mock":   True,
        "games":     _stable_int(55, 82, player_id, "gp"),
        "points":    pts,
        "rebounds":  reb,
        "assists":   ast,
        "tpm":       tpm,
        "steals":    stl,
        "blocks":    blk,
        "turnovers": tov,
        "min":       slot["min"],
        "fgp":       str(fgp),
        "tpp":       str(tpp),
        "ftp":       str(ftp),
        "pra":       round(pts + reb + ast, 1),
    }


def get_player_last_n_games(player_id, season: int = NBA_SEASON, n: int = 5) -> list:
    player_id = str(player_id)
    avgs = get_player_season_averages(player_id, season)
    if avgs is None:
        return []
    records = []
    for i in range(n):
        day    = max(1, 28 - i * 4)
        date   = f"2025-04-{day:02d}"
        gnoise = (_stable_unit(player_id, i, "g") - 0.5) * 6
        pts = max(0, round(avgs["points"]   + gnoise))
        reb = max(0, round(avgs["rebounds"] + gnoise * 0.3))
        ast = max(0, round(avgs["assists"]  + gnoise * 0.4))
        tpm = max(0, round(avgs["tpm"]      + gnoise * 0.2))
        stl = max(0, round(avgs["steals"]   + gnoise * 0.1))
        blk = max(0, round(avgs["blocks"]   + gnoise * 0.1))
        fgp_f = _parse_float(avgs["fgp"]) / 100 or 0.46
        fgm   = max(0, round(pts * 0.43))
        fga   = max(1, round(fgm / fgp_f))
        records.append({
            "is_mock": True,
            "game":    {"id": f"mock-g-{player_id}-{i}", "date": date},
            "statistics": [{
                "min": str(avgs["min"]),
                "points": pts, "totReb": reb, "assists": ast,
                "tpm": tpm, "steals": stl, "blocks": blk,
                "turnovers": max(0, round(avgs["turnovers"] + gnoise * 0.1)),
                "fgm": fgm, "fga": fga, "fgp": avgs["fgp"],
            }],
        })
    return records


def get_player_vs_team(player_id, team_id, player_team_id, seasons=None) -> dict:
    player_id = str(player_id)
    team_id   = str(team_id)
    avgs = get_player_season_averages(player_id)
    if avgs is None:
        return {"games": 0, "records": [], "averages": None, "limited_sample": True}

    factor  = 1.0 + (_stable_unit(player_id, team_id, "vs") - 0.5) * 0.30
    n_games = _stable_int(2, 8, player_id, team_id, "n")

    va = {
        "points":    round(avgs["points"]   * factor, 1),
        "rebounds":  round(avgs["rebounds"] * factor, 1),
        "assists":   round(avgs["assists"]  * factor, 1),
        "tpm":       round(avgs["tpm"]      * factor, 1),
        "steals":    round(avgs["steals"]   * factor, 1),
        "blocks":    round(avgs["blocks"]   * factor, 1),
        "turnovers": avgs["turnovers"],
        "fgp": avgs["fgp"], "tpp": avgs["tpp"],
    }
    va["pra"] = round(va["points"] + va["rebounds"] + va["assists"], 1)

    records = []
    for i in range(min(3, n_games)):
        gnoise = (_stable_unit(player_id, team_id, i, "vsg") - 0.5) * 5
        pts = max(0, round(va["points"]   + gnoise))
        reb = max(0, round(va["rebounds"] + gnoise * 0.3))
        ast = max(0, round(va["assists"]  + gnoise * 0.4))
        tpm = max(0, round(va["tpm"]      + gnoise * 0.2))
        fgp_f = _parse_float(avgs["fgp"]) / 100 or 0.46
        fgm = max(0, round(pts * 0.43))
        fga = max(1, round(fgm / fgp_f))
        records.append({
            "is_mock": True,
            "game": {"id": f"mock-vs-{player_id}-{team_id}-{i}",
                     "date": f"2025-0{i+1}-15"},
            "statistics": [{
                "min": str(avgs["min"]),
                "points": pts, "totReb": reb, "assists": ast, "tpm": tpm,
                "steals": max(0, round(va["steals"])),
                "blocks": max(0, round(va["blocks"])),
                "turnovers": max(0, round(va["turnovers"])),
                "fgm": fgm, "fga": fga, "fgp": avgs["fgp"],
            }],
        })

    return {
        "is_mock":        True,
        "games":          n_games,
        "records":        records,
        "averages":       va,
        "limited_sample": n_games < 3,
    }


# -- Not-available stubs ---------------------------------------------------

def get_today_games() -> list:
    return []


def get_injuries() -> list:
    return []


def get_team_injuries(team_id) -> list:
    return []


def get_game_stats(game_id) -> list:
    return []


# -- Convenience aliases ---------------------------------------------------

def get_player_stats(player_id, season: int = NBA_SEASON) -> list:
    return get_player_last_n_games(player_id, season=season, n=10)


def get_player_game_log(player_id, season: int = NBA_SEASON, n: int = 10) -> list:
    return get_player_last_n_games(player_id, season=season, n=n)


def compute_last5_averages_from_records(records: list) -> dict | None:
    if not records:
        return None
    fields = ["points", "totReb", "assists", "tpm", "steals", "blocks", "turnovers"]
    totals = {f: 0.0 for f in fields}
    n = 0
    for r in records:
        s = (r.get("statistics") or [{}])[0]
        for f in fields:
            totals[f] += _parse_float(s.get(f, 0))
        n += 1
    if n == 0:
        return None
    avgs = {f: round(totals[f] / n, 1) for f in fields}
    avgs["rebounds"] = avgs.pop("totReb")
    avgs["pra"] = round(avgs["points"] + avgs["rebounds"] + avgs["assists"], 1)
    return avgs
