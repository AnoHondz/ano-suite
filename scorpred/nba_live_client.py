"""
Live NBA data client built around ESPN's public JSON endpoints.

This module keeps the existing standings/team-stats helpers from nba_client,
but replaces mock scoreboards, schedules, rosters, and injuries with live data.
"""

from __future__ import annotations

import hashlib
import copy
from concurrent.futures import Future
import json
import logging
import os
import time
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

import nba_client as legacy
from runtime_paths import cache_dir
from services.data_freshness import metadata, worst
from services.prediction_lifecycle import freshness_state, parse_kickoff
from utils.atomic import atomic_write_json

logger = logging.getLogger(__name__)

NBA_SEASON = legacy.NBA_SEASON
NBA_ESPN_BASE_URL = os.getenv(
    "NBA_ESPN_BASE_URL",
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba",
).rstrip("/")
NBA_ESPN_STATS_BASE_URL = os.getenv(
    "NBA_ESPN_STATS_BASE_URL",
    "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba",
).rstrip("/")
EXTERNAL_API_TIMEOUT_SECONDS = float(os.getenv("EXTERNAL_API_TIMEOUT_SECONDS", "20"))
EXTERNAL_API_RETRY_ATTEMPTS = max(1, int(os.getenv("EXTERNAL_API_RETRY_ATTEMPTS", "3")))
EXTERNAL_API_RETRY_BACKOFF_SECONDS = float(os.getenv("EXTERNAL_API_RETRY_BACKOFF_SECONDS", "1.2"))
PAGE_RENDER_TIMEOUT_SECONDS = float(os.getenv("EXTERNAL_API_PAGE_TIMEOUT_SECONDS", "3"))
PAGE_RENDER_RETRY_ATTEMPTS = max(1, int(os.getenv("EXTERNAL_API_PAGE_RETRY_ATTEMPTS", "1")))
PAGE_RENDER_RETRY_BACKOFF_SECONDS = float(os.getenv("EXTERNAL_API_PAGE_RETRY_BACKOFF_SECONDS", "0.0"))

NBA_CACHE_DIR = cache_dir("nba_public")
NBA_LIVE_TTL_SECONDS = 60
NBA_SCHEDULE_TTL_SECONDS = 60 * 60
NBA_STATUS_MAX_AGE_SECONDS = 5 * 60
NBA_FORM_MAX_AGE_SECONDS = 6 * 60 * 60
NBA_PLAYER_STATS_TTL_SECONDS = 6 * 60 * 60
NBA_PAST_PLAYER_STATS_TTL_SECONDS = 7 * 24 * 60 * 60
CALENDAR_TZ = ZoneInfo("America/New_York")
_CUP_CHAMPIONSHIP_TYPE_ID = "39"
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

# Memory entries store the earliest source deadline, never a new receipt time.
_MEM_CACHE: dict[str, tuple[float, Any]] = {}
_SCHEDULE_MEM: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_SCOREBOARD_REFRESH: dict[str, dict[str, Any]] = {}
_SCHEDULE_REFRESH: dict[str, Any] = {"status": "unknown", "observed_at": None}
_FETCH_LOCK = threading.Lock()
_FETCHES: dict[tuple, Future] = {}
_WAITERS: dict[tuple, int] = {}


def _source_metadata(observed_at: float, ttl_seconds: int, max_age: int, *, stale=False) -> dict:
    return metadata("ESPN", datetime.fromtimestamp(observed_at, UTC).isoformat(), ttl_seconds, max_age, degraded=stale)


def _with_source(payload: Any, metadata: dict) -> Any:
    return {**payload, "_scorpred_source": metadata} if isinstance(payload, dict) else payload


def get_schedule_refresh_status() -> dict:
    """Outcome of the last full schedule scan, including fresh empty calendars."""
    return dict(_SCHEDULE_REFRESH)

ROUTE_FEATURES: dict[str, list[str]] = {
    "index": ["teams", "scoreboard"],
    "matchup": ["team_stats", "h2h", "form", "injuries"],
    "player": ["roster", "player_stats"],
    "prediction": ["team_stats", "h2h", "form", "injuries"],
    "standings": ["standings"],
}


_CACHE_DIR_READY = False


def _cache_path(endpoint: str, params: dict[str, Any] | None = None) -> Path:
    global _CACHE_DIR_READY
    raw = f"{endpoint}:{sorted((params or {}).items())}"
    key = hashlib.md5(raw.encode("utf-8")).hexdigest()
    if not _CACHE_DIR_READY:
        NBA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_DIR_READY = True
    return NBA_CACHE_DIR / f"{key}.json"


def _cache_valid(path: Path, ttl_seconds: int) -> bool:
    if not path.exists():
        return False
    age = datetime.now(UTC) - datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return 0 <= age.total_seconds() < ttl_seconds


def _load_cache(path: Path) -> Any:
    with open(path, encoding="utf-8") as file:
        content = file.read().strip()
    if not content:
        raise ValueError(f"Empty cache file: {path}")
    return json.loads(content)


def _save_cache(path: Path, payload: Any) -> None:
    NBA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)


def _request_settings(request_profile: str = "default") -> tuple[float, int, float]:
    if request_profile == "page":
        return (
            PAGE_RENDER_TIMEOUT_SECONDS,
            PAGE_RENDER_RETRY_ATTEMPTS,
            PAGE_RENDER_RETRY_BACKOFF_SECONDS,
        )
    return (
        EXTERNAL_API_TIMEOUT_SECONDS,
        EXTERNAL_API_RETRY_ATTEMPTS,
        EXTERNAL_API_RETRY_BACKOFF_SECONDS,
    )


def _espn_get(
    endpoint: str,
    params: dict[str, Any] | None = None,
    ttl_seconds: int = NBA_SCHEDULE_TTL_SECONDS,
    request_profile: str = "default",
    base_url: str | None = None,
) -> Any:
    # Coalesce the same page request without making short page fetches wait for
    # the longer retry policy of a background refresh.
    key = (str(_cache_path(f"espn:{endpoint}", params or {})), ttl_seconds, request_profile, base_url)
    with _FETCH_LOCK:
        flight = _FETCHES.get(key)
        owner = flight is None
        if owner:
            flight = Future()
            _FETCHES[key] = flight
        else:
            _WAITERS[key] = _WAITERS.get(key, 0) + 1
    if not owner:
        return copy.deepcopy(flight.result())
    try:
        result = _fetch_espn(endpoint, params, ttl_seconds, request_profile, base_url)
    except BaseException as exc:
        with _FETCH_LOCK:
            _FETCHES.pop(key, None)
            _WAITERS.pop(key, None)
        flight.set_exception(exc)
        raise
    with _FETCH_LOCK:
        _FETCHES.pop(key, None)
        waiters = _WAITERS.pop(key, 0)
    # Waiters copy from a private snapshot; with none waiting (the usual case) nothing is copied.
    flight.set_result(copy.deepcopy(result) if waiters else None)
    return result


def _cached_snapshot(path, ttl_seconds, max_age, *, stale=False):
    payload = _load_cache(path)
    if not isinstance(payload, dict):
        raise ValueError("NBA cache does not contain a provider response")
    original = payload.get("_scorpred_source")
    if isinstance(original, dict):
        observed = original.get("observed_at")
        source = metadata("ESPN", observed, ttl_seconds, max_age,
                          degraded=stale or freshness_state(original) != "fresh")
        for field in ("expires_at", "max_stale_at"):
            previous, current = parse_kickoff(original.get(field)), parse_kickoff(source.get(field))
            if previous and current:
                source[field] = min(previous, current).isoformat()
        source["state"] = freshness_state(source)
        source["receipt_basis"] = original.get("receipt_basis", "embedded_observation")
    else:
        # Older cache files have no durable receipt. Retain the existing bounded
        # legacy fallback, and replace it only after a real provider response.
        source = _source_metadata(path.stat().st_mtime, ttl_seconds, max_age, stale=stale)
        source["receipt_basis"] = "legacy_file_mtime"
    return _with_source(payload, source)


def _fetch_espn(endpoint, params, ttl_seconds, request_profile, base_url):
    params = params or {}
    path = _cache_path(f"espn:{endpoint}", params)
    max_age = (NBA_STATUS_MAX_AGE_SECONDS if endpoint in {"scoreboard", "summary"}
               else max(ttl_seconds, NBA_FORM_MAX_AGE_SECONDS) if endpoint.endswith("/schedule") else ttl_seconds)

    if path.exists():
        try:
            cached = _cached_snapshot(path, ttl_seconds, max_age)
            if cached["_scorpred_source"]["state"] == "fresh":
                logger.debug("ESPN cache HIT  %s", endpoint)
                return cached
        except (ValueError, OSError):
            logger.warning("Ignoring invalid NBA cache for %s", endpoint)

    logger.debug("ESPN cache MISS %s", endpoint)
    url = f"{base_url or NBA_ESPN_BASE_URL}/{endpoint.lstrip('/')}"
    timeout_seconds, retry_attempts, retry_backoff_seconds = _request_settings(request_profile)
    last_exc: Exception | None = None
    for attempt in range(retry_attempts):
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json"},
                    timeout=timeout_seconds,
                )

            if response.status_code in RETRY_STATUS_CODES and attempt < retry_attempts - 1:
                time.sleep(retry_backoff_seconds * (2 ** attempt))
                continue

            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("error") or payload.get("errors"):
                raise ValueError("NBA provider returned an invalid response")
            source = _source_metadata(datetime.now(UTC).timestamp(), ttl_seconds, max_age)
            source["receipt_basis"] = "provider_response"
            observed = _with_source(payload, source)
            try:
                _save_cache(path, observed)
            except OSError:
                logger.warning("NBA cache write failed for %s; using the received response", endpoint)
            return observed
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < retry_attempts - 1:
                time.sleep(retry_backoff_seconds * (2 ** attempt))
                continue

    if path.exists():
        try:
            cached = _cached_snapshot(path, ttl_seconds, max_age, stale=True)
            if cached["_scorpred_source"]["state"] != "unavailable":
                logger.warning("ESPN STALE fallback for %s", endpoint)
                return cached
        except (OSError, ValueError):
            logger.warning("Ignoring invalid NBA fallback cache for %s", endpoint)
    raise RuntimeError(f"NBA ESPN request failed for {endpoint}") from last_exc


def _feature_catalog() -> dict[str, dict[str, str]]:
    return {
        "teams": {
            "state": "live",
            "title": "Live team directory",
            "detail": "Team identities come from the current provider-backed NBA team directory.",
        },
        "standings": {
            "state": "live",
            "title": "Live standings",
            "detail": "Standings and team-level season stats are live from the configured standings feed.",
        },
        "team_stats": {
            "state": "live",
            "title": "Live team stats",
            "detail": "Season records, PPG, opponent PPG, and net rating are live.",
        },
        "scoreboard": {
            "state": "live",
            "title": "Live scoreboard",
            "detail": "Upcoming, scheduled, and in-progress NBA games are pulled from ESPN's public scoreboard feed.",
        },
        "h2h": {
            "state": "live",
            "title": "Live matchup history",
            "detail": "Head-to-head history is built from real completed games on the public team schedule feed.",
        },
        "form": {
            "state": "live",
            "title": "Live recent form",
            "detail": "Recent form is built from real completed games on each team's public schedule feed.",
        },
        "injuries": {
            "state": "live",
            "title": "Live injuries",
            "detail": "Injury statuses come from the public team roster feed for the selected teams.",
        },
        "roster": {
            "state": "live",
            "title": "Live rosters",
            "detail": "Rosters come from ESPN's public team roster endpoint.",
        },
        "player_stats": {
            "state": "fallback",
            "title": "Roster-first player view",
            "detail": "The app now shows live rosters and featured leaders for the selected game, but full player game-log props are still limited by public endpoint coverage.",
        },
    }


def get_route_support(route_name: str) -> dict[str, dict[str, str]]:
    catalog = _feature_catalog()
    return {
        key: catalog[key]
        for key in ROUTE_FEATURES.get(route_name, [])
        if key in catalog
    }


def get_teams() -> list[dict]:
    # Schedule IDs come from ESPN; use the same provider for team identities.
    payload = _espn_get("teams", ttl_seconds=86400, request_profile="page")
    teams = {}
    for sport in payload.get("sports") or []:
        for league in sport.get("leagues") or []:
            for entry in league.get("teams") or []:
                team = _team_from_competitor(entry)
                if team["id"] and team["name"] != "Team":
                    teams[team["id"]] = team
    if not teams:
        raise RuntimeError("NBA team directory is unavailable")
    return sorted(teams.values(), key=lambda team: team["name"])


def get_standings(season: int | None = None) -> dict:
    if not legacy.NBA_API_KEY:
        return {}  # Standings are optional; public schedules/form remain usable.
    season = season or legacy.current_season()
    result = legacy.get_standings(season)
    # The legacy provider may silently return arbitrarily old snapshots.
    path = legacy._cache_path("nba-league-standings", {"year": season})
    if not _cache_valid(path, NBA_FORM_MAX_AGE_SECONDS):
        raise RuntimeError("NBA standings freshness cannot be verified")
    return result


def get_team_season_stats(team_id, season: int | None = None):
    games = _latest_completed_team_games(team_id, season=season or legacy.current_season(), enrich_scores=True)
    result = _derive_team_stats_from_games(team_id, games)
    if result:
        result["data_freshness"] = worst([game.get("data_freshness") for game in games])
    return result


def _as_int(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("value", value.get("displayValue"))
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _team_from_competitor(competitor: dict[str, Any]) -> dict[str, Any]:
    team = competitor.get("team") or {}
    return {
        "id": str(team.get("id") or competitor.get("id") or ""),
        "name": team.get("displayName") or team.get("name") or "Team",
        "nickname": team.get("shortDisplayName") or team.get("name") or "Team",
        "city": team.get("location", ""),
        "abbrev": team.get("abbreviation", ""),
        "logo": team.get("logo")
        or ((team.get("logos") or [{}])[0].get("href"))
        or "",
    }


def _event_state(status: dict[str, Any]) -> str:
    return ((status or {}).get("type") or {}).get("state", "")


def _event_status(status: dict[str, Any]) -> dict[str, Any]:
    type_info = (status or {}).get("type") or {}
    return {
        "long": type_info.get("description") or status.get("type") or "Scheduled",
        # ESPN shortDetail is display text (often a scheduled date/time), not
        # a machine status. Eligibility consumers require the actual status code.
        "short": type_info.get("name") or type_info.get("shortDetail") or status.get("displayClock") or "",
        "short_detail": type_info.get("shortDetail") or status.get("displayClock") or "",
        "completed": type_info.get("completed", False),
        "state": type_info.get("state", ""),
        "detail": type_info.get("detail") or type_info.get("shortDetail") or "",
    }


def _normalize_competition(event: dict[str, Any], competition: dict[str, Any]) -> dict[str, Any] | None:
    competitors = competition.get("competitors") or []
    home = next((entry for entry in competitors if entry.get("homeAway") == "home"), None)
    away = next((entry for entry in competitors if entry.get("homeAway") == "away"), None)
    if not home or not away:
        return None

    status = competition.get("status") or event.get("status") or {}
    broadcasts = competition.get("broadcasts") or event.get("broadcasts") or []
    geo_broadcasts = competition.get("geoBroadcasts") or event.get("geoBroadcasts") or []
    venue = competition.get("venue") or {}
    competition_type = competition.get("type") or {}

    return {
        "id": str(event.get("id") or competition.get("id") or ""),
        # ESPN files the NBA Cup final as a regular-season game, but it is excluded from standings.
        "standings_game": not (str(competition_type.get("abbreviation") or "").upper() == "CC"
                               or str(competition_type.get("id") or "") == _CUP_CHAMPIONSHIP_TYPE_ID),
        "short_name": event.get("shortName", ""),
        "season_type": _as_int((event.get("seasonType") or {}).get("type") or (event.get("season") or {}).get("type")),
        "season": _as_int((event.get("season") or {}).get("year")),
        "date": {"start": event.get("date") or competition.get("date") or ""},
        "status": _event_status(status),
        "venue": {"name": venue.get("fullName", "")},
        "teams": {
            "home": _team_from_competitor(home),
            "visitors": _team_from_competitor(away),
        },
        "scores": {
            "home": {
                "points": _as_int(home.get("score")),
                "linescore": [
                    line.get("displayValue") or str(line.get("value"))
                    for line in (home.get("linescores") or [])
                ],
            },
            "visitors": {
                "points": _as_int(away.get("score")),
                "linescore": [
                    line.get("displayValue") or str(line.get("value"))
                    for line in (away.get("linescores") or [])
                ],
            },
        },
        "leaders": {
            "home": home.get("leaders") or [],
            "visitors": away.get("leaders") or [],
        },
        "records": {
            "home": home.get("record") or [],
            "visitors": away.get("record") or [],
        },
        "broadcasts": broadcasts,
        "geo_broadcasts": geo_broadcasts,
        "odds": (competition.get("odds") or [{}])[0],
        "attendance": competition.get("attendance"),
        "neutral_site": competition.get("neutralSite"),
        "summary_link": next(
            (link.get("href") for link in (event.get("links") or []) if "summary" in (link.get("rel") or [])),
            "",
        ),
        "is_live": _event_state(status) == "in",
        "is_pre": _event_state(status) == "pre",
        "is_post": _event_state(status) == "post",
    }


def _normalize_event(event: dict[str, Any]) -> dict[str, Any] | None:
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    return _normalize_competition(event, competitions[0])


def _ymd(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(CALENDAR_TZ)
    return value.strftime("%Y%m%d")


def get_scoreboard_games(target_date: datetime | None = None, request_profile: str = "default") -> list[dict]:
    target_date = target_date or datetime.now(CALENDAR_TZ)
    payload = _espn_get(
        "scoreboard",
        params={"dates": _ymd(target_date)},
        ttl_seconds=NBA_LIVE_TTL_SECONDS,
        request_profile=request_profile,
    )
    source = payload.get("_scorpred_source") or {}
    _SCOREBOARD_REFRESH[_ymd(target_date)] = source
    games = []
    for event in payload.get("events") or []:
        if normalized := _normalize_event(event):
            normalized["data_freshness"] = dict(source)
            games.append(normalized)
    games.sort(key=lambda game: game["date"]["start"])
    return games


def get_today_games(request_profile: str = "default") -> list[dict]:
    return get_scoreboard_games(request_profile=request_profile)


def get_upcoming_games(next_n: int = 12, days_ahead: int = 5, request_profile: str = "default") -> list[dict]:
    global _SCHEDULE_REFRESH
    now = datetime.now(CALENDAR_TZ)
    dates = [now + timedelta(days=d) for d in range(days_ahead + 1)]

    # Fetch all dates in parallel instead of sequentially
    from concurrent.futures import ThreadPoolExecutor, as_completed
    day_results: dict[int, list[dict]] = {}
    failed_days = 0
    failed_indices: set[int] = set()
    with ThreadPoolExecutor(max_workers=min(len(dates), 6)) as pool:
        futs = {pool.submit(get_scoreboard_games, d, request_profile): i for i, d in enumerate(dates)}
        for fut in as_completed(futs):
            try:
                day_results[futs[fut]] = fut.result()
            except Exception:
                day_results[futs[fut]] = []
                failed_days += 1
                failed_indices.add(futs[fut])

    sources = [_SCOREBOARD_REFRESH.get(_ymd(d), {}) for i, d in enumerate(dates) if i not in failed_indices]
    stale_days = sum(source.get("state") == "degraded" for source in sources)
    observations = [source["observed_at"] for source in sources if source.get("observed_at")]
    _SCHEDULE_REFRESH = {
        "status": "failed" if failed_days == len(dates) else "degraded" if failed_days or stale_days else "success",
        "observed_at": min(observations) if observations else None,
        "requested_days": len(dates), "failed_days": failed_days, "stale_days": stale_days,
        "warning": "NBA schedule refresh is incomplete or delayed; affected games have no pregame recommendation." if failed_days or stale_days else None,
    }
    if failed_days == len(dates):
        raise RuntimeError("NBA schedule is temporarily unavailable")

    seen: set[str] = set()
    upcoming: list[dict] = []
    for i in range(len(dates)):
        for game in day_results.get(i, []):
            if game["id"] in seen:
                continue
            if game.get("status", {}).get("state") not in {"pre", "in"}:
                continue
            seen.add(game["id"])
            upcoming.append(game)
            if len(upcoming) >= next_n:
                return upcoming

    return upcoming


def _candidate_dates_from_hint(date_hint: str | None) -> list[datetime]:
    candidates = []
    if date_hint:
        text = str(date_hint).replace("Z", "+00:00")
        try:
            base = datetime.fromisoformat(text)
        except ValueError:
            try:
                base = datetime.fromisoformat(str(date_hint)[:10])
            except ValueError:
                base = None
        if base is not None:
            if base.tzinfo is not None:
                base = base.astimezone(CALENDAR_TZ)
            candidates.extend([base, base - timedelta(days=1), base + timedelta(days=1)])

    today = datetime.now(CALENDAR_TZ)
    candidates.extend([today - timedelta(days=1), today, today + timedelta(days=1)])
    unique: list[datetime] = []
    seen_dates: set[str] = set()
    for candidate in candidates:
        key = candidate.strftime("%Y-%m-%d")
        if key not in seen_dates:
            seen_dates.add(key)
            unique.append(candidate)
    return unique


def get_event_snapshot(event_id: str, date_hint: str | None = None, request_profile: str = "default") -> dict | None:
    event_id = str(event_id)
    for candidate in _candidate_dates_from_hint(date_hint):
        for game in get_scoreboard_games(candidate, request_profile=request_profile):
            if game["id"] == event_id:
                return game
    return None


def get_game_summary(event_id: str, request_profile: str = "default") -> dict:
    return _espn_get(
        "summary",
        params={"event": str(event_id)},
        ttl_seconds=NBA_LIVE_TTL_SECONDS,
        request_profile=request_profile,
    )


def _team_schedule(team_id: str, season: int | None = None, request_profile: str = "default", *, require_fresh: bool = False) -> list[dict]:
    params: dict = {}
    if season is not None:
        params["season"] = season
    payload = _espn_get(
        f"teams/{team_id}/schedule",
        params=params if params else None,
        ttl_seconds=NBA_SCHEDULE_TTL_SECONDS,
        request_profile=request_profile,
    )
    source = payload.get("_scorpred_source") or {}
    if require_fresh and source.get("state") == "degraded":
        raise RuntimeError("NBA form refresh failed; delayed team scores cannot support a pregame prediction")
    events = []
    for event in payload.get("events") or []:
        if normalized := _normalize_event(event):
            normalized["data_freshness"] = dict(source)
            events.append(normalized)
    return events


def _completed_team_games_for_season(team_id: str, season: int, request_profile: str = "default") -> list[dict]:
    finished = [game for game in _team_schedule(team_id, season=season, request_profile=request_profile, require_fresh=True)
                if game["status"]["state"] == "post" and game.get("standings_game", True)]
    finished.sort(key=lambda game: game["date"]["start"], reverse=True)
    return finished


def _extract_summary_scores(summary: dict[str, Any], event_id: str) -> dict[str, Any] | None:
    competitions = ((summary.get("header") or {}).get("competitions") or [])
    if not competitions:
        return None
    competitors = competitions[0].get("competitors") or []
    home = next((entry for entry in competitors if entry.get("homeAway") == "home"), None)
    away = next((entry for entry in competitors if entry.get("homeAway") == "away"), None)
    if not home or not away:
        return None

    return {
        "scores": {
            "home": {
                "points": _as_int(home.get("score")),
                "linescore": [line.get("displayValue") or str(line.get("value")) for line in (home.get("linescores") or [])],
            },
            "visitors": {
                "points": _as_int(away.get("score")),
                "linescore": [line.get("displayValue") or str(line.get("value")) for line in (away.get("linescores") or [])],
            },
        },
        "records": {
            "home": home.get("record") or [],
            "visitors": away.get("record") or [],
        },
        "event_id": str(event_id),
    }


def _enrich_game_scores(game: dict[str, Any], request_profile: str = "default") -> dict[str, Any]:
    if game["scores"]["home"].get("points") is not None and game["scores"]["visitors"].get("points") is not None:
        return game

    event_id = str(game.get("id") or "")
    if not event_id:
        return game

    try:
        summary = get_game_summary(event_id, request_profile=request_profile)
    except Exception:
        return game
    source = summary.get("_scorpred_source") or {}
    if source and freshness_state(source) != "fresh":
        return game

    enriched = _extract_summary_scores(summary, event_id)
    if not enriched:
        return game

    game["scores"] = enriched["scores"]
    game["records"] = enriched["records"]
    game["data_freshness"] = worst([game.get("data_freshness"), source])
    return game


def _latest_completed_team_games(
    team_id: str,
    season: int = NBA_SEASON,
    lookback_seasons: int = 0,
    enrich_scores: bool = False,
    request_profile: str = "default",
) -> list[dict]:
    team_id = str(team_id)
    for candidate_season in range(season, season - lookback_seasons - 1, -1):
        try:
            games = _completed_team_games_for_season(team_id, candidate_season, request_profile=request_profile)
        except TypeError:
            games = _completed_team_games_for_season(team_id, candidate_season)
        if games:
            if enrich_scores:
                return [_enrich_game_scores(dict(game), request_profile=request_profile) for game in games]
            return games
    return []


def _season_from_game_date(game: dict[str, Any]) -> int | None:
    date_text = ((game.get("date") or {}).get("start") or "")[:10]
    if not date_text:
        return None
    try:
        dt = datetime.fromisoformat(date_text)
    except ValueError:
        return None
    # ESPN identifies seasons by their ending year (October 2026 -> 2027).
    return dt.year + 1 if dt.month >= 8 else dt.year


def get_team_recent_form_context(team_id, season: int = NBA_SEASON, n: int = 10, historical_lookback: int = 2, request_profile: str = "default") -> dict[str, Any]:
    """Return current-season recent form plus optional clearly-separated historical context."""
    team_id = str(team_id)
    current = _latest_completed_team_games(
        team_id,
        season=season,
        lookback_seasons=0,
        enrich_scores=True,
        request_profile=request_profile,
    )[:n]
    historical: list[dict[str, Any]] = []
    historical_season: int | None = None

    if not current:
        for candidate in range(season - 1, season - historical_lookback - 1, -1):
            try:
                rows = _completed_team_games_for_season(team_id, candidate, request_profile=request_profile)
            except TypeError:
                rows = _completed_team_games_for_season(team_id, candidate)
            if rows:
                historical = [_enrich_game_scores(dict(game), request_profile=request_profile) for game in rows[:n]]
                historical_season = candidate
                break

    return {
        "current_games": current,
        "historical_games": historical,
        "current_season": season,
        "historical_season": historical_season,
        "current_complete": len(current) >= min(5, n),
        "using_historical_context": bool(historical),
        "data_freshness": worst([game.get("data_freshness") for game in current]),
    }


def _points_for_team(game: dict[str, Any], team_id: str) -> tuple[int | None, int | None, bool | None]:
    team_id = str(team_id)
    home = game["teams"]["home"]
    away = game["teams"]["visitors"]
    home_points = _as_int(game["scores"]["home"].get("points"))
    away_points = _as_int(game["scores"]["visitors"].get("points"))

    if str(home.get("id") or "") == team_id:
        return home_points, away_points, True
    if str(away.get("id") or "") == team_id:
        return away_points, home_points, False
    return None, None, None


def _record_string(wins: int, losses: int) -> str:
    return f"{wins}-{losses}"


def _parse_record_value(value: str) -> tuple[int, int]:
    text = str(value or "").strip()
    if "-" not in text:
        return 0, 0
    left, right = text.split("-", 1)
    return _as_int(left) or 0, _as_int(right) or 0


def _team_record_map(game: dict[str, Any], team_id: str) -> dict[str, str]:
    team_id = str(team_id)
    side = "home" if str(game["teams"]["home"].get("id") or "") == team_id else "visitors"
    entries = game.get("records", {}).get(side) or []
    records: dict[str, str] = {}
    for entry in entries:
        record_type = str(entry.get("type") or "").strip().lower()
        summary = entry.get("summary") or entry.get("displayValue") or ""
        if record_type:
            records[record_type] = summary
    return records


def _derive_team_stats_from_games(team_id: str, games: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not games:
        return None

    team_id = str(team_id)
    totals = {
        "wins": 0,
        "losses": 0,
        "points_for": 0,
        "points_against": 0,
        "home_w": 0,
        "home_l": 0,
        "away_w": 0,
        "away_l": 0,
    }
    recent_results: list[str] = []
    scored_games = 0

    for game in games:
        our_points, their_points, is_home = _points_for_team(game, team_id)
        if our_points is None or their_points is None or is_home is None:
            continue

        scored_games += 1
        won = our_points > their_points
        recent_results.append("W" if won else "L")
        totals["wins" if won else "losses"] += 1
        totals["points_for"] += our_points
        totals["points_against"] += their_points
        if is_home:
            totals["home_w" if won else "home_l"] += 1
        else:
            totals["away_w" if won else "away_l"] += 1

    latest_records = _team_record_map(games[0], team_id)
    total_record = latest_records.get("total", "")
    home_record = latest_records.get("home", "")
    away_record = latest_records.get("road", latest_records.get("away", ""))
    record_wins, record_losses = _parse_record_value(total_record)

    total_games = totals["wins"] + totals["losses"]
    if record_wins or record_losses:
        totals["wins"] = record_wins
        totals["losses"] = record_losses
        total_games = record_wins + record_losses
    if total_games == 0:
        return None

    last10_results = recent_results[:10]
    last10_wins = sum(1 for result in last10_results if result == "W")
    last10_losses = len(last10_results) - last10_wins

    streak_value = ""
    if recent_results:
        streak_result = recent_results[0]
        streak_count = 0
        for result in recent_results:
            if result != streak_result:
                break
            streak_count += 1
        streak_value = f"{streak_result}{streak_count}"

    return {
        "games": total_games,
        "wins": totals["wins"],
        "losses": totals["losses"],
        "win_pct": round(totals["wins"] / total_games, 3),
        "ppg": round(totals["points_for"] / max(scored_games, 1), 1),
        "opp_ppg": round(totals["points_against"] / max(scored_games, 1), 1),
        "net_rtg": round((totals["points_for"] - totals["points_against"]) / max(scored_games, 1), 1),
        "home_record": home_record or _record_string(totals["home_w"], totals["home_l"]),
        "away_record": away_record or _record_string(totals["away_w"], totals["away_l"]),
        "home_w": totals["home_w"],
        "home_l": totals["home_l"],
        "away_w": totals["away_w"],
        "away_l": totals["away_l"],
        "last10": _record_string(last10_wins, last10_losses),
        "last10_w": last10_wins,
        "last10_l": last10_losses,
        "streak": streak_value,
    }


def get_team_recent_form(team_id, season: int = NBA_SEASON, n: int = 10, request_profile: str = "default") -> list[dict]:
    team_id = str(team_id)
    context = get_team_recent_form_context(team_id, season=season, n=n, historical_lookback=0, request_profile=request_profile)
    return context.get("current_games") or []


def get_h2h(team_a_id, team_b_id, season: int = NBA_SEASON, request_profile: str = "default") -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor

    team_a_id = str(team_a_id)
    team_b_id = str(team_b_id)
    seen_ids: set[str] = set()
    games: list[dict] = []
    target_set = {team_a_id, team_b_id}

    def _scan_season(yr: int) -> list[dict]:
        found: list[dict] = []
        try:
            for game in _team_schedule(team_a_id, season=yr, request_profile=request_profile, require_fresh=True):
                if game["status"]["state"] != "post":
                    continue
                home_id = game["teams"]["home"]["id"]
                away_id = game["teams"]["visitors"]["id"]
                if {home_id, away_id} != target_set:
                    continue
                found.append(game)
        except Exception:
            pass
        return found

    # Fetch 5 seasons in parallel
    seasons = list(range(season - 4, season + 1))
    with ThreadPoolExecutor(max_workers=len(seasons)) as pool:
        season_results = list(pool.map(_scan_season, seasons))

    for season_games in season_results:
        for game in season_games:
            gid = game["id"]
            if gid not in seen_ids:
                seen_ids.add(gid)
                games.append(_enrich_game_scores(dict(game), request_profile=request_profile))

    games.sort(key=lambda game: game["date"]["start"], reverse=True)
    return games[:10]


def _normalize_roster_player(player: dict[str, Any]) -> dict[str, Any]:
    injuries = player.get("injuries") or []
    position = player.get("position") or {}
    status = player.get("status") or {}
    return {
        "id": str(player.get("id") or ""),
        "firstname": player.get("firstName", ""),
        "lastname": player.get("lastName", ""),
        "displayName": player.get("displayName") or player.get("fullName") or "",
        "photo": (player.get("headshot") or {}).get("href", ""),
        "jersey": player.get("jersey", ""),
        "age": player.get("age"),
        "position": position.get("displayName") or position.get("name") or "",
        "leagues": {
            "standard": {
                "pos": position.get("abbreviation", ""),
                "active": status.get("type") == "active" or status.get("name") == "Active",
            }
        },
        "injuries": injuries,
        "status": status.get("name", ""),
        "_raw": player,
    }


def get_team_roster(team_id, season: int = NBA_SEASON, request_profile: str = "default") -> list[dict]:
    payload = _espn_get(
        f"teams/{team_id}/roster",
        ttl_seconds=NBA_SCHEDULE_TTL_SECONDS,
        request_profile=request_profile,
    )
    roster = [_normalize_roster_player(player) for player in (payload.get("athletes") or [])]
    roster.sort(key=lambda player: (player["lastname"], player["firstname"]))
    return roster


def get_injuries_from_roster(roster: list[dict]) -> list[dict]:
    injuries = []
    for player in roster:
        for injury in player.get("injuries") or []:
            injuries.append(
                {
                    "player": {
                        "id": player["id"],
                        "firstname": player["firstname"],
                        "lastname": player["lastname"],
                        "pos": player["leagues"]["standard"]["pos"],
                    },
                    "status": injury.get("status", player.get("status", "")),
                    "description": injury.get("detail") or injury.get("type") or injury.get("status") or "",
                    "date": injury.get("date", ""),
                }
            )
    return injuries


def get_team_injuries(team_id, season: int = NBA_SEASON, request_profile: str = "default") -> list[dict]:
    roster = get_team_roster(team_id, season=season, request_profile=request_profile)
    return get_injuries_from_roster(roster)


_PLAYER_STAT_FIELDS = {
    "general": {"gamesPlayed": "gp", "avgMinutes": "min", "avgRebounds": "reb", "avgFouls": "pf"},
    "offensive": {"avgPoints": "pts", "avgFieldGoalsMade": "fgm", "avgFieldGoalsAttempted": "fga",
                  "avgFreeThrowsMade": "ftm", "avgFreeThrowsAttempted": "fta", "avgAssists": "ast",
                  "avgThreePointFieldGoalsMade": "fg3m",
                  "avgTurnovers": "tov"},
    "defensive": {"avgSteals": "stl", "avgBlocks": "blk"},
}


def get_league_player_stats(season: int, request_profile: str = "default") -> list[dict]:
    """Every player's regular-season per-game averages for ``season`` (ESPN ending year).

    ESPN combines a traded player's season into one row; ``team_ids`` lists every team they appeared for.
    """
    ttl = NBA_PLAYER_STATS_TTL_SECONDS if season >= NBA_SEASON else NBA_PAST_PLAYER_STATS_TTL_SECONDS
    key = f"player_stats:{season}"
    cached = _MEM_CACHE.get(key)
    if cached and time.time() < cached[0]:
        return [dict(line) for line in cached[1]]  # rows are flat; callers never mutate team_ids
    lines: list[dict] = []
    deadline = time.time() + min(ttl, 10 * 60)
    page, pages = 1, 1
    while page <= min(pages, 5):
        payload = _espn_get("statistics/byathlete", params={"season": season, "seasontype": 2, "limit": 1000, "page": page},
                            ttl_seconds=ttl, request_profile=request_profile, base_url=NBA_ESPN_STATS_BASE_URL)
        source = payload.get("_scorpred_source") or {}
        if source:
            if freshness_state(source) != "fresh":
                raise RuntimeError("NBA player averages need a fresh provider observation")
            expires = parse_kickoff(source.get("expires_at"))
            if expires:
                deadline = min(deadline, expires.timestamp())
        pages = _as_int((payload.get("pagination") or {}).get("pages")) or 1
        names = {category.get("name"): category.get("names") or [] for category in payload.get("categories") or []}
        for row in payload.get("athletes") or []:
            athlete = row.get("athlete") or {}
            teams = [str(team.get("id")) for team in athlete.get("teams") or [] if team.get("id")]
            line = {"id": str(athlete.get("id") or ""), "name": athlete.get("displayName") or "",
                    "position": (athlete.get("position") or {}).get("abbreviation") or "",
                    "team_ids": teams or [str(athlete.get("teamId") or "")]}
            for category in row.get("categories") or []:
                fields = _PLAYER_STAT_FIELDS.get(category.get("name"), {})
                for name, value in zip(names.get(category.get("name"), []), category.get("values") or category.get("totals") or []):
                    if name in fields:
                        try:
                            line[fields[name]] = float(value)
                        except (TypeError, ValueError):
                            pass
            if line["id"]:
                lines.append(line)
        page += 1
    _MEM_CACHE[key] = (deadline, [dict(line) for line in lines])
    return lines


def get_team_season_games(team_id, season: int, request_profile: str = "default") -> list[dict]:
    """Completed standings games for a finished season, newest first; old results may come from a stale cache."""
    finished = [game for game in _team_schedule(str(team_id), season=season, request_profile=request_profile)
                if game["status"]["state"] == "post" and game.get("standings_game", True)]
    finished.sort(key=lambda game: game["date"]["start"], reverse=True)
    return finished


def get_injuries() -> list:
    return []


def get_game_stats(game_id) -> list:
    summary = get_game_summary(str(game_id))
    return (summary.get("boxscore") or {}).get("teams", [])


def _extract_featured_leaders(side: list[dict]) -> list[dict]:
    featured = []
    for leader in side:
        athlete = ((leader.get("leaders") or [{}])[0] or {}).get("athlete") or {}
        if not athlete:
            continue
        featured.append(
            {
                "metric": leader.get("displayName") or leader.get("name") or "",
                "abbreviation": leader.get("abbreviation") or "",
                "value": (leader.get("leaders") or [{}])[0].get("displayValue", ""),
                "name": athlete.get("displayName") or athlete.get("fullName") or "",
                "headshot": athlete.get("headshot", ""),
                "position": (athlete.get("position") or {}).get("abbreviation", ""),
                "team_id": str((athlete.get("team") or {}).get("id") or ""),
            }
        )
    return featured


def get_featured_players_for_event(event_id: str, date_hint: str | None = None) -> dict[str, list[dict]]:
    snapshot = get_event_snapshot(event_id, date_hint=date_hint)
    if not snapshot:
        return {"home": [], "visitors": []}
    return {
        "home": _extract_featured_leaders(snapshot.get("leaders", {}).get("home", [])),
        "visitors": _extract_featured_leaders(snapshot.get("leaders", {}).get("visitors", [])),
    }


def get_player_season_averages(player_id, season: int = NBA_SEASON):
    return None


def get_player_last_n_games(player_id, season: int = NBA_SEASON, n: int = 5) -> list:
    return []


def get_player_vs_team(player_id, team_id, player_team_id, seasons=None) -> dict:
    return {"games": 0, "records": [], "averages": None, "limited_sample": True}


def get_player_stats(player_id, season: int = NBA_SEASON) -> list:
    return []


def get_player_game_log(player_id, season: int = NBA_SEASON, n: int = 10) -> list:
    return []


def compute_last5_averages_from_records(records: list) -> dict | None:
    return None


def format_game_stat_line(stat_record: dict) -> dict:
    return stat_record
