"""Flask application for the ScorPred football and NBA predictor."""

from __future__ import annotations

import importlib
import hashlib
import copy
import os
import sys
import time
import threading

import re
import unicodedata
import click
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from inspect import unwrap
from typing import Any, Callable
from urllib.parse import urlparse
from sqlalchemy import text

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):
        return False
from pathlib import Path
from flask import Flask, jsonify, redirect, render_template as flask_render_template, request, send_from_directory, session, url_for, g
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix


import api_client as ac
import predictor as pred
import props_engine as pe
import scorpred_engine as se
import model_tracker as mt
import user_auth
import odds_fetcher
import result_updater as ru
import decision_ui as dui
from runtime_paths import (
    auth_db_path,
    auth_storage_diagnostics,
    data_root,
    ensure_runtime_dirs,
    ml_report_path,
    walk_forward_report_path,
)
from security import check_chat_rate_limit, configure_security, sanitize_error
from services import analysis_assistant as assistant_services
from services import bets_service
from services import cache_service
from services import calibration_service
from services import model_trust_service
from services import prediction_service
from services import validators
from services import decision_store
from services.data_freshness import from_records, worst
from services.prediction_lifecycle import calendar_date, freshness_state, is_pregame, parse_kickoff, sanitize_decision, unavailable_reason
from services.decision_engine import DecisionEngine
from services.match_brain import MatchBrain
from db_models import db, migrate_schema
from extensions import limiter
from decision_ui import build_decision_card, sort_cards_by_kickoff, top_opportunities, plan_summary

try:
    import nba_live_client as nc
    import nba_predictor as np_nba
except ImportError:  # pragma: no cover
    nc = None  # type: ignore[assignment]
    np_nba = None  # type: ignore[assignment]
from services import evidence as evidence_services
from services import tracking_bootstrap as bootstrap_services
from services import scoreline_engine as _scoreline_engine
from services.tracking_bootstrap import fixture_context_from_form
from services.alerter import EmailAlerter
from services.pipeline_monitor import PipelineMonitor
import services.scheduler as _sched_module
from league_config import (
    CURRENT_SEASON,
    DEFAULT_LEAGUE_ID,
    LEAGUE_BY_ID,
    SUPPORTED_LEAGUES,
    SUPPORTED_LEAGUE_IDS,
)
from nba_routes import nba_bp, nba_api_bp
from parlay_routes import parlay_bp
from nfl_routes import nfl_api_bp
from cachetools import TTLCache

try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None

load_dotenv()
ensure_runtime_dirs()

import logging as _logging
_logger = _logging.getLogger(__name__)
_RELEASE_TAG = "2026-04-20-d53ddbe"


class _LazyModuleProxy:
    """Lazy-load heavyweight modules to keep cold starts leaner."""

    def __init__(self, module_name: str):
        self._module_name = module_name
        self._module = None

    def _load(self):
        if self._module is None:
            self._module = importlib.import_module(self._module_name)
        return self._module

    def __getattr__(self, name: str):
        return getattr(self._load(), name)


sm = _LazyModuleProxy("scormastermind")
strategy_lab_services = _LazyModuleProxy("services.strategy_lab")
_EMPTY_METRICS = {
    "total_predictions": 0,
    "finalized_predictions": 0,
    "wins": 0,
    "losses": 0,
    "overall_accuracy": None,
    "by_confidence": {},
    "by_sport": {},
    "recent_predictions": [],
}

_FIXTURE_INDEX: dict[str, dict[str, Any]] = {}
_local_match_analysis_cache = TTLCache(maxsize=500, ttl=300)
_local_fixture_cache = TTLCache(maxsize=50, ttl=120)
_SOCCER_CARD_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="soccer-api")
_SOCCER_CARD_FLIGHTS = {}
_SOCCER_CARD_LOCK = threading.Lock()
_local_league_cache = TTLCache(maxsize=20, ttl=300)
_local_scoreline_cache: TTLCache = TTLCache(maxsize=200, ttl=300)
_API_CIRCUIT: dict[str, dict[str, Any]] = {}
_RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}
_DECISION_ENGINE = DecisionEngine()
_MATCH_BRAIN: MatchBrain | None = None


def _runtime_release_tag() -> str:
    commit = (os.environ.get("RENDER_GIT_COMMIT") or "").strip()
    branch = (os.environ.get("RENDER_GIT_BRANCH") or "").strip()
    if commit:
        short = commit[:7]
        return f"{branch}@{short}" if branch else short
    return _RELEASE_TAG

# â”€â”€ Production startup guard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_secret_key = os.environ.get("SECRET_KEY", "").strip()
_is_production = bool(os.environ.get("RENDER") or os.environ.get("FLASK_ENV") == "production")
if _is_production and not _secret_key:
    raise RuntimeError(
        "SECRET_KEY environment variable must be set in production. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
if _is_production and (len(_secret_key) < 32 or _secret_key in {"change-me-to-a-long-random-string", "dev-insecure-key-change-me"}):
    raise RuntimeError("Production SECRET_KEY must be a generated secret of at least 32 characters.")
if not _secret_key:
    _logger.warning("SECRET_KEY not set â€” using an insecure default. Set SECRET_KEY for production.")
    _secret_key = "dev-insecure-key-change-me"

# --- Persistent session config ---
app = Flask(__name__)
app.config["TESTING"] = os.getenv("SCORPRED_TESTING") == "1"
if _is_production:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
configure_security(app, _secret_key)
_database_url = (os.getenv("DATABASE_URL") or "").strip()
if _database_url.startswith("postgres://"):
    _database_url = _database_url.replace("postgres://", "postgresql://", 1)
if _database_url.startswith("postgresql://"):
    _database_url = _database_url.replace("postgresql://", "postgresql+psycopg://", 1)
if not _database_url:
    _database_url = f"sqlite:///{auth_db_path().as_posix()}"
app.config["SQLALCHEMY_DATABASE_URI"] = _database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
app.config["SESSION_COOKIE_NAME"] = "scorpred_session"
app.config["SESSION_COOKIE_SECURE"] = _is_production
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
db.init_app(app)
# --- Persistent session config ---
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
with app.app_context():
    db.create_all()
    migrate_schema(db.engine)


def render_template(template_name: str, **context):
    """Safe template renderer that avoids cascading template crashes."""
    try:
        return flask_render_template(template_name, **context)
    except Exception as exc:  # pragma: no cover - fallback path
        _logger.error("template_render_failed template=%s err=%s", template_name, exc, exc_info=True)
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"status": "error", "message": "template render failed", "code": 500}), 500
        return flask_render_template("error.html", **_page_context(msg="An internal error occurred. Please try again.")), 500

_auth_storage = auth_storage_diagnostics()
if _is_production and not _auth_storage["durable"]:
    raise RuntimeError("Production requires DATABASE_URL or a persistent disk for accounts and tracking.")
if _auth_storage["durable"]:
    _logger.info(
        "Auth storage ready at %s (%s).",
        _auth_storage["path"],
        _auth_storage["mode"],
    )
else:
    _logger.warning(
        "Auth storage is using an ephemeral path (%s). Accounts will not survive redeploys until "
        "DATABASE_URL or SCORPRED_PERSISTENT_ROOT points to durable storage.",
        _auth_storage["path"],
    )

try:
    with app.app_context():
        _db_ready = bool(db.session.execute(text("SELECT 1")).scalar())
except Exception:
    _logger.warning("Database connectivity check failed at startup", exc_info=True)
    _db_ready = False
_redis_ready = cache_service._get_redis_client() is not None
_redis_url = os.getenv("REDIS_URL", "").strip()
app.config["RATELIMIT_STORAGE_URI"] = _redis_url or "memory://"
limiter.init_app(app)
_logger.info(
    "startup_summary env=%s db=%s redis=%s api=%s",
    "production" if _is_production else "development",
    "connected" if _db_ready else "unavailable",
    "enabled" if _redis_ready else "local-fallback",
    "ready",
)

# ── React SPA integration ─────────────────────────────────────────────────────
_SPA_DIST = Path(__file__).parent / "frontend" / "dist"
_SPA_INDEX = _SPA_DIST / "index.html"

# Prefixes that must always reach Flask — never intercepted for the SPA.
_BACKEND_PREFIXES = (
    "/api/", "/static/", "/health", "/status", "/admin/",
    "/login", "/signup", "/logout",
)
_SPA_PAGE_PATHS = {"/", "/soccer", "/nba", "/nba/", "/nfl", "/nfl/", "/match-analysis", "/prediction", "/insights"}


@app.before_request
def _serve_spa_for_page_routes():
    """When the React build exists, serve index.html for all non-backend paths."""
    if not app.config.get("SERVE_SPA", True) or not _SPA_INDEX.exists() or request.method not in {"GET", "HEAD"}:
        return None  # no build yet — fall through to Jinja2 routes
    path = request.path
    if any(path.startswith(p) for p in _BACKEND_PREFIXES):
        return None
    # Static assets emitted by Vite (hashed filenames in /assets/)
    if path.startswith("/assets/"):
        filename = path[len("/assets/"):]
        return send_from_directory(_SPA_DIST / "assets", filename)
    if path in _SPA_PAGE_PATHS:
        return send_from_directory(_SPA_DIST, "index.html")
    return None

# â”€â”€ Blueprints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_CRITICAL_API_KEYS: set[str] = set()
_OPTIONAL_API_KEYS = {
    "API_FOOTBALL_KEY": "configured soccer provider is unavailable; public fallbacks may still work",
    "NBA_API_KEY": "legacy NBA standings are unavailable; public schedules and form remain usable",
    "ODDS_API_KEY": "betting odds and edge badges will be unavailable",
}
_FEATURE_NOTES = {
    "API_FOOTBALL_KEY": "soccer fixtures, standings, and match data will be unavailable",
}


def _validate_api_keys() -> None:
    missing_critical: list[str] = []
    present: list[str] = []

    for key in _CRITICAL_API_KEYS:
        # Football key: satisfied by either API_FOOTBALL_KEY or APISPORTS_KEY (direct api-sports.io)
        if key == "API_FOOTBALL_KEY":
            has_key = bool(os.getenv("API_FOOTBALL_KEY", "").strip() or os.getenv("APISPORTS_KEY", "").strip())
        else:
            has_key = bool(os.getenv(key, "").strip())
        if has_key:
            present.append(key)
        else:
            missing_critical.append(key)
            _logger.warning(
                "Missing API key %s -- %s",
                key,
                _FEATURE_NOTES.get(key, "related features will be degraded"),
            )

    for key, note in _OPTIONAL_API_KEYS.items():
        if os.getenv(key, "").strip():
            present.append(key)
        else:
            _logger.warning("Missing optional API key %s -- %s", key, note)

    if present:
        _logger.info("api_keys_present keys=%s", ",".join(sorted(present)))

    if missing_critical and _is_production:
        raise RuntimeError(
            "Critical API keys missing in production: "
            + ", ".join(sorted(missing_critical))
            + ". Set them in your environment before starting the server."
        )


_validate_api_keys()

app.register_blueprint(nba_bp)
app.register_blueprint(nba_api_bp)
app.register_blueprint(nfl_api_bp)
app.register_blueprint(user_auth.user_auth_bp)
app.register_blueprint(parlay_bp)


@app.before_request
def inject_user():
    g.current_user = user_auth.current_user()
    g._request_started_at = time.perf_counter()


@app.after_request
def _log_request_duration(response):
    if request.path.startswith(("/api/bets", "/api/session", "/my-bets", "/login", "/signup")):
        response.headers["Cache-Control"] = "no-store"
    started = getattr(g, "_request_started_at", None)
    if started is None:
        return response
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    _logger.info(
        "request_complete method=%s path=%s status=%s duration_ms=%s",
        request.method,
        request.path,
        response.status_code,
        duration_ms,
    )
    return response


@app.context_processor
def inject_auth_context():
    return {
        "current_user": user_auth.current_user(),
        "is_guest": user_auth.current_user() is None,
        "format_percent_decimal": format_percent_decimal,
        "format_confidence": format_confidence,
    }


def _is_api_request() -> bool:
    path = request.path or ""
    return path.startswith("/api/") or request.is_json or "application/json" in str(request.headers.get("Accept", "")).lower()


def _error_response(code: int, message: str):
    if _is_api_request():
        return jsonify({"status": "error", "message": message, "code": code}), code
    return render_template("error.html", **_page_context(msg=message)), code


@app.errorhandler(400)
def handle_400(exc):
    return _error_response(400, sanitize_error(exc) or "Bad request")


@app.errorhandler(Exception)
def handle_unhandled_exception(exc):
    if isinstance(exc, HTTPException):
        return _error_response(exc.code or 500, exc.description or exc.name)
    _logger.error("Unhandled exception in route %s: %s", request.path, exc, exc_info=True)
    return _error_response(500, "Something went wrong. Please try again or go back to the home page.")


LEAGUE = DEFAULT_LEAGUE_ID
SEASON = CURRENT_SEASON
LEAGUE_SESSION_KEY = "selected_league_id"


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _now_stamp() -> str:
    return assistant_services.now_stamp()


 # --- Restored missing backend helpers for prediction/result display ---
def _fixture_context_from_form():
    """
    Wrapper for fixture_context_from_form using Flask's request.form or request.args.
    """
    from flask import request
    # Prefer form data, fallback to args
    form_data = request.form if request.method == "POST" else request.args
    return fixture_context_from_form(form_data)
def _summarize_form_compare(form_compare: dict, actual_winner: str | None = None) -> str | None:
    """
    Summarize recent form comparison for both teams, optionally referencing the actual winner.
    """
    if not form_compare or not isinstance(form_compare, dict):
        return None
    segments = []
    for team, stats in (form_compare or {}).items():
        if not stats:
            continue
        wins = stats.get("wins")
        losses = stats.get("losses")
        avg_for = stats.get("avg_goals_for") or stats.get("avg_points_for")
        avg_against = stats.get("avg_goals_against") or stats.get("avg_points_against")
        seg = f"{team}: {wins}W-{losses}L"
        if avg_for is not None and avg_against is not None:
            seg += f" ({avg_for} for, {avg_against} against)"
        segments.append(seg)
    if not segments:
        return None
    joined = "; ".join(segments)
    if actual_winner and actual_winner != "Unknown":
        return f"Recent form: {joined}. {actual_winner} had the edge in recent results." if actual_winner in form_compare else f"Recent form: {joined}."
    return f"Recent form: {joined}."


def _reality_sentence(record: dict) -> str:
    """
    Generate a short sentence summarizing the actual result for a prediction record.
    """
    actual_winner = record.get("actual_winner") or "Unknown"
    final_score = record.get("final_score_display") or "Unknown"
    total_scored = record.get("total_scored")
    if total_scored is not None:
        unit = "goal" if str(record.get("sport") or "").lower() == "soccer" else "point"
        label = unit if total_scored == 1 else f"{unit}s"
        return f"{actual_winner}, {final_score}, {total_scored} {label}"
    return f"{actual_winner}, {final_score}"


def _prediction_sentence(record: dict) -> str:
    """
    Generate a short sentence summarizing the model's prediction for a record.
    """
    pick = _prediction_pick_display(record)
    prob = _predicted_outcome_probability(record)
    prob_text = _format_percent_value(prob)
    if pick and prob_text:
        return f"Model pick: {pick} ({prob_text})"
    if pick:
        return f"Model pick: {pick}"
    return "Model pick unavailable"


def _filter_useful_injury_context(injuries: dict) -> dict:
    """
    Filter injury context to only include teams with at least one notable injury.
    """
    if not injuries or not isinstance(injuries, dict):
        return {}
    return {team: row for team, row in injuries.items() if row and (row.get("count", 0) > 0 or row.get("notable"))}


def _extract_totals_leg(prediction: dict) -> dict | None:
    """
    Extract the totals leg (pick/line/market) from a prediction dict.
    """
    if not prediction or not isinstance(prediction, dict):
        return None
    # Try direct keys first
    pick = prediction.get("totals_pick")
    line = prediction.get("totals_line")
    market = prediction.get("totals_market")
    # If not present, try nested or legacy keys
    if not pick and "optional_picks" in prediction:
        for opt in prediction["optional_picks"]:
            m = str(opt.get("market") or "").lower()
            if "over/under" in m or "o/u" in m:
                pick = opt.get("lean") or opt.get("pick")
                # The rules engine names the line in the market ("Goals Over/Under 2.5"); never default it to 0.
                line = mt.parse_totals_line(opt.get("line") or opt.get("value"), opt.get("market"))
                market = opt.get("market")
                break
    result = {}
    if pick:
        result["pick"] = pick
    if line is not None:
        result["line"] = line
    if market:
        result["market"] = market
    return result if result else None


def _refresh_requested() -> bool:
    return bootstrap_services.refresh_requested(request.args)


def _set_data_refresh() -> bool:
    return bootstrap_services.set_data_refresh(ac, request.args)


@app.after_request
def _reset_force_refresh(response):
    return bootstrap_services.reset_force_refresh(ac, response)


def _football_data_source() -> str:
    return assistant_services.football_data_source(ac)


def _page_context(data_source: str | None = None, **kwargs) -> dict:
    if "alert_count" not in kwargs:
        kwargs["alert_count"] = 0
    return assistant_services.page_context(ac, data_source=data_source, **kwargs)


def _selection_error_redirect(endpoint: str, message: str):
    return redirect(url_for(endpoint, selection_error=message))


def _check_rate_limit(bucket: str, *, limit: int, window_seconds: int):
    now_ts = time.time()
    key = f"{bucket}:{request.remote_addr or 'unknown'}"
    history = _RATE_LIMIT_BUCKETS.get(key, [])
    history = [ts for ts in history if (now_ts - ts) < window_seconds]
    if len(history) >= limit:
        retry_after = max(1, int(window_seconds - (now_ts - history[0])))
        return retry_after
    history.append(now_ts)
    _RATE_LIMIT_BUCKETS[key] = history
    return None


def _clean_injuries(items: list[dict]) -> list[dict]:
    if isinstance(items, dict) and items.get("status") == "fail" and items.get("restricted"):
        return None  # Signal restricted
    return evidence_services.clean_injuries(items)



def _display_injuries(items: list[dict]) -> list[dict]:
    if isinstance(items, dict) and items.get("status") == "fail" and items.get("restricted"):
        return None
    return evidence_services.display_injuries(items)


def _fetch_team_squad(team_id: int, season: int = SEASON, league_id: int | None = None) -> list[dict]:
    selected_league_id = _coerce_league_id(league_id if league_id is not None else _active_league_id())
    cache_key = f"squad:{team_id}:{season}:{selected_league_id}"
    if not hasattr(g, "_api_squad_cache"):
        g._api_squad_cache = {}
    if cache_key in g._api_squad_cache:
        return g._api_squad_cache[cache_key]
    getter = getattr(ac, "get_players", None)
    if callable(getter):
        try:
            data = getter(team_id, season, selected_league_id)
        except TypeError:
            data = getter(team_id, season)
        if data:
            g._api_squad_cache[cache_key] = data
            return data
    data = ac.get_squad(team_id, season, selected_league_id)
    g._api_squad_cache[cache_key] = data
    return data


def _group_squad_by_position(squad: list[dict]) -> dict[str, list[dict]]:
    grouped = {"Goalkeeper": [], "Defender": [], "Midfielder": [], "Attacker": []}
    for raw in squad or []:
        player = raw.get("player") if isinstance(raw, dict) and isinstance(raw.get("player"), dict) else raw
        if not isinstance(player, dict):
            continue
        position = str(player.get("position") or raw.get("position") or "Attacker").strip().title()
        if position not in grouped:
            if position in {"Forward", "Striker", "Winger"}:
                position = "Attacker"
            elif position in {"Centre-Back", "Center Back", "Fullback", "Wing Back"}:
                position = "Defender"
            elif position in {"Keeper", "Goalie"}:
                position = "Goalkeeper"
            else:
                position = "Midfielder"
        grouped[position].append(
            {
                "id": player.get("id"),
                "name": player.get("name", ""),
                "photo": player.get("photo", ""),
                "number": player.get("number"),
                "position": position,
            }
        )
    return grouped


def _build_opp_strengths(standings: list) -> dict:
    try:
        return se.build_opp_strengths_from_standings(standings)
    except Exception:
        _logger.warning("Failed to build opponent strengths from standings", exc_info=True)
        return {}


def _football_supported_leagues() -> list[dict]:
    return bootstrap_services.football_supported_leagues(ac, LEAGUE_BY_ID)


def _critical_error(message: str, status_code: int = 503):
    return render_template("error.html", **_page_context(msg=message)), status_code


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_percent_decimal(value: float | None, *, plus: bool = True, empty: str = "N/A") -> str:
    if value is None:
        return empty
    try:
        number = float(value) * 100.0
    except (TypeError, ValueError):
        return empty
    sign = "+" if plus and number > 0 else ""
    return f"{sign}{number:.1f}%"


def format_confidence(value: float | int | None, *, empty: str = "N/A") -> str:
    if value is None:
        return empty
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return empty
    return f"{max(0, min(100, number))}%"


def _data_quality_label(value: Any) -> str:
    if isinstance(value, (int, float)):
        v = float(value)
        if v >= 75:
            return "Strong Data"
        if v >= 55:
            return "Partial Data"
        return "Limited Data"
    text = str(value or "").strip().lower()
    if not text or "limited" in text or "low" in text:
        return "Limited Data"
    if "strong" in text:
        return "Strong Data"
    if "moderate" in text or "partial" in text:
        return "Partial Data"
    return "Partial Data"


_TRACKING_REFRESH_LAST_RUN: datetime | None = None
_FIXTURE_AUTO_REFRESH_INTERVAL: int = int(os.environ.get("FIXTURE_REFRESH_INTERVAL_SECONDS", "900"))
_auto_refresh_timer: threading.Timer | None = None


def _schedule_auto_refresh(interval: int = _FIXTURE_AUTO_REFRESH_INTERVAL) -> None:
    """Schedule a background fixture refresh without blocking request threads."""
    global _auto_refresh_timer
    if _auto_refresh_timer is not None:
        _auto_refresh_timer.cancel()

    def _run():
        global _auto_refresh_timer
        try:
            if _MATCH_BRAIN is not None:
                league = _active_league_id()
                _MATCH_BRAIN.refresh_cycle(league, min_interval_seconds=0)
                app.logger.info("auto_refresh completed league=%s", league)
        except Exception:
            app.logger.debug("auto_refresh background task error", exc_info=True)
        finally:
            _schedule_auto_refresh(interval)

    _auto_refresh_timer = threading.Timer(interval, _run)
    _auto_refresh_timer.daemon = True
    _auto_refresh_timer.start()


def _prediction_confidence_pct(row: dict[str, Any]) -> int:
    explicit = row.get("confidence_pct")
    if explicit not in (None, ""):
        return int(round(_safe_float(explicit, 0)))
    probs = [row.get("prob_a"), row.get("prob_b"), row.get("prob_draw")]
    numeric = []
    for value in probs:
        if isinstance(value, (int, float)):
            parsed = float(value)
            numeric.append(parsed * 100 if 0 <= parsed <= 1 else parsed)
    if numeric:
        return int(round(max(0.0, min(100.0, max(numeric)))))
    tier = str(row.get("confidence") or "").lower()
    mapped = {"high": 72, "medium": 61, "low": 52}
    return mapped.get(tier, 55)


def _refresh_tracking_results_if_due(min_interval_seconds: int = 300) -> None:
    global _TRACKING_REFRESH_LAST_RUN
    now = datetime.now(timezone.utc)
    if _TRACKING_REFRESH_LAST_RUN and (now - _TRACKING_REFRESH_LAST_RUN).total_seconds() < min_interval_seconds:
        return
    if _MATCH_BRAIN is not None:
        try:
            _MATCH_BRAIN.refresh_cycle(_active_league_id(), min_interval_seconds=min_interval_seconds)
        except Exception:
            app.logger.debug("MatchBrain refresh_cycle skipped due to provider error.", exc_info=True)
    else:
        try:
            ru.update_pending_predictions()
        except Exception:
            app.logger.debug("Tracking auto-refresh skipped due to provider error.", exc_info=True)
    _TRACKING_REFRESH_LAST_RUN = now


def _normalize_probs(values: dict[str, Any] | None) -> dict[str, float]:
    raw = values if isinstance(values, dict) else {}
    a = max(_safe_float(raw.get("a"), 0.0), 0.0)
    draw = max(_safe_float(raw.get("draw"), 0.0), 0.0)
    b = max(_safe_float(raw.get("b"), 0.0), 0.0)
    total = a + draw + b
    if total <= 0:
        return {"a": 33.4, "draw": 33.3, "b": 33.3}
    scaled = {"a": (a / total) * 100.0, "draw": (draw / total) * 100.0, "b": (b / total) * 100.0}
    rounded = {k: round(v, 1) for k, v in scaled.items()}
    delta = round(100.0 - sum(rounded.values()), 1)
    if delta:
        top_key = max(rounded, key=lambda key: scaled[key])
        rounded[top_key] = round(rounded[top_key] + delta, 1)
    return rounded


setattr(sys.modules[__name__], "_normalise" + "_probs", _normalize_probs)


def _format_prediction_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Unknown"
    include_time = "T" in raw or (len(raw) > 10 and ":" in raw)
    for candidate in (raw, raw.replace("Z", "+00:00"), f"{raw[:10]}T00:00:00"):
        try:
            fmt = "%b %d, %Y %H:%M UTC" if include_time else "%b %d, %Y"
            return datetime.fromisoformat(candidate).strftime(fmt)
        except ValueError:
            continue
    return raw[:10]


def _normalize_team_name(name: str) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _natural_join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _predicted_outcome_probability(record: dict) -> float | None:
    predicted_code = str(record.get("predicted_winner") or "").lower()
    mapping = {"a": "prob_a", "b": "prob_b", "draw": "prob_draw"}
    key = mapping.get(predicted_code)
    if not key:
        return None
    raw = record.get(key)
    return _safe_float(raw) if raw is not None else None


def _team_names_match(left: str, right: str) -> bool:
    a = _normalize_team_name(left)
    b = _normalize_team_name(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _fixture_finished(fixture: dict) -> bool:
    status = ((fixture.get("fixture") or {}).get("status") or {}).get("short", "")
    return str(status).upper() in {"FT", "AET", "PEN"}


def _parse_stat_value(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _format_stat_number(value: float | None, *, percent: bool = False) -> str:
    if value is None:
        return "N/A"
    if percent:
        return f"{round(value, 1)}%"
    if float(value).is_integer():
        return str(int(value))
    return str(round(value, 2))


def _prediction_pick_display(record: dict) -> str:
    if record.get("predicted_winner_display"):
        return str(record.get("predicted_winner_display") or "Unknown")
    if record.get("predicted_pick_label"):
        return str(record.get("predicted_pick_label") or "Unknown")
    winner_code = str(record.get("predicted_winner", "")).strip().lower()

    if winner_code == "a":
        return record.get("team_a", "Team A")
    if winner_code == "b":
        return record.get("team_b", "Team B")
    if winner_code == "draw":
        final_score = record.get("final_score_display") or "Unknown"
        return f"Match drawn {final_score}" if final_score != "Unknown" else "Match drawn"

    # Fallback to actual winner if available
    actual_winner = record.get("actual_winner")
    final_score = record.get("final_score_display") or "Unknown"
    if actual_winner:
        return f"{actual_winner} won {final_score}" if final_score != "Unknown" else actual_winner
    return "Unknown"


def _prepare_model_component_sections(record: dict) -> list[dict]:
    factors = record.get("model_factors") if isinstance(record.get("model_factors"), dict) else {}
    sections: list[dict] = []

    for team_key, title in (("team_a", record.get("team_a") or "Team A"), ("team_b", record.get("team_b") or "Team B")):
        team_factors = factors.get(team_key)
        if not isinstance(team_factors, dict):
            continue
        rows = [
            {"label": key.replace("_", " ").title(), "value": round(value, 2) if isinstance(value, float) else value}
            for key, value in team_factors.items()
        ]
        if rows:
            sections.append({"title": f"{title} model components", "rows": rows})

    general_rows = [
        {"label": key.replace("_", " ").title(), "value": round(value, 2) if isinstance(value, float) else value}
        for key, value in factors.items()
        if not isinstance(value, dict)
    ]
    if general_rows:
        sections.insert(0, {"title": "Model factors", "rows": general_rows})
    return sections


def _actual_outcome_probability(record: dict) -> float | None:
    actual_code = str(record.get("actual_result") or "").lower()
    mapping = {"a": "prob_a", "b": "prob_b", "draw": "prob_draw"}
    key = mapping.get(actual_code)
    if not key:
        return None
    raw = record.get(key)
    return _safe_float(raw) if raw is not None else None


def _format_percent_value(value: float | None) -> str | None:
    if value is None:
        return None
    rounded = round(value, 1)
    return f"{int(rounded)}%" if float(rounded).is_integer() else f"{rounded}%"


def _pluralize(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def _sample_size_state(
    sample_size: int | None,
    *,
    unit: str,
    early_threshold: int,
    limited_threshold: int,
) -> dict[str, Any]:
    count = max(int(sample_size or 0), 0)
    unit_label = _pluralize(count, unit, "matches" if unit == "match" else None)

    if count <= 0:
        return {
            "count": 0,
            "status": "warming_up",
            "badge": "Collecting data",
            "sample_label": f"No graded {unit_label} yet",
        }
    if count < early_threshold:
        return {
            "count": count,
            "status": "early",
            "badge": "Early sample",
            "sample_label": f"{count} graded {unit_label} - early sample",
        }
    if count < limited_threshold:
        return {
            "count": count,
            "status": "limited",
            "badge": "Limited sample",
            "sample_label": f"{count} graded {unit_label} - limited sample",
        }
    return {
        "count": count,
        "status": "mature",
        "badge": "Established sample",
        "sample_label": f"{count} graded {unit_label}",
    }


def _build_live_metric_summary(accuracy: float | None, sample_size: int | None) -> dict[str, Any]:
    sample = _sample_size_state(sample_size, unit="game", early_threshold=8, limited_threshold=25)
    if accuracy is None or sample["count"] == 0:
        return {
            "title": "Live tracked accuracy",
            "value": "Collecting data",
            "badge": sample["badge"],
            "sample_label": sample["sample_label"],
            "summary": "Real match results are being graded, but the live sample is not large enough to anchor the product yet.",
            "tone": "muted",
        }

    summary = "Real graded picks from the live app."
    if sample["status"] == "early":
        summary = "Useful as an early health check, but too small to compare directly with the backtest headline."
    elif sample["status"] == "limited":
        summary = "Live tracking is building, but it still needs more graded games before it should outrank offline validation."

    return {
        "title": "Live tracked accuracy",
        "value": _format_percent_value(accuracy),
        "badge": sample["badge"],
        "sample_label": sample["sample_label"],
        "summary": summary,
        "tone": "positive" if accuracy >= 55 else "negative" if accuracy < 45 else "neutral",
    }


def _build_offline_metric_summary(
    accuracy: float | None,
    sample_size: int | None,
    *,
    title: str,
    summary: str,
) -> dict[str, Any]:
    sample = _sample_size_state(sample_size, unit="match", early_threshold=80, limited_threshold=250)
    if accuracy is None:
        return {
            "title": title,
            "value": "Awaiting report",
            "badge": "Refreshing",
            "sample_label": sample["sample_label"],
            "summary": summary,
            "tone": "muted",
        }

    return {
        "title": title,
        "value": _format_percent_value(accuracy),
        "badge": sample["badge"],
        "sample_label": sample["sample_label"],
        "summary": summary,
        "tone": "positive" if accuracy >= 55 else "negative" if accuracy < 45 else "neutral",
    }


def _build_walk_forward_metric_summary(walk_forward: dict[str, Any] | None) -> dict[str, Any]:
    payload = walk_forward or {}
    if not payload.get("available"):
        return {
            "title": "Primary performance metric",
            "value": "Backtest pending",
            "badge": "Refreshing",
            "sample_label": "Walk-forward report is not available yet",
            "summary": "This is the metric that should headline the product once the offline report is present.",
            "tone": "muted",
        }

    total_matches = payload.get("total_test_matches") or 0
    sample = _sample_size_state(total_matches, unit="match", early_threshold=250, limited_threshold=800)
    accuracy = payload.get("mean_combined_accuracy")
    return {
        "title": "Primary performance metric",
        "value": _format_percent_value(accuracy),
        "badge": "Most reliable",
        "sample_label": sample["sample_label"],
        "summary": "Walk-forward accuracy is the fairest headline metric because each fold predicts matches the model had not seen during training.",
        "tone": "positive" if isinstance(accuracy, (int, float)) and accuracy >= 55 else "negative" if isinstance(accuracy, (int, float)) and accuracy < 45 else "neutral",
    }


def _prediction_edge_state(prediction: dict[str, Any]) -> dict[str, str]:
    gap = _safe_float(prediction.get("score_gap"), 0.0)
    play_type = str(prediction.get("play_type") or "LEAN").upper()

    if gap < 0.35:
        return {
            "title": "Narrow matchup profile",
            "summary": "The side edge is playable but tight, so confidence depends more on venue, form, and lineup context.",
        }
    if play_type == "AVOID":
        return {
            "title": "Volatile matchup",
            "summary": "The matchup still has a side, but volatility is high enough to keep the public action conservative.",
        }
    if gap < 1.0:
        return {
            "title": "Playable lean",
            "summary": "One side rates slightly better, but the advantage is still modest.",
        }
    if gap < 1.8:
        return {
            "title": "Clear edge",
            "summary": "The pre-match profile shows meaningful separation between the two teams.",
        }
    return {
        "title": "Strong edge",
        "summary": "Multiple inputs are lining up in the same direction, creating a stronger-than-normal signal.",
    }


def _build_prediction_reason_tags(prediction: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    key_edges = prediction.get("key_edges") or []
    if key_edges:
        first_edge = key_edges[0]
        category = str(first_edge.get("category") or "").strip()
        if category:
            tags.append(f"{category} edge")

    gap = _safe_float(prediction.get("score_gap"), 0.0)
    if gap < 0.35:
        tags.append("Tight side edge")
    elif gap < 1.0:
        tags.append("Modest separation")
    else:
        tags.append("Clear separation")

    draw_prob = _safe_float((prediction.get("win_probabilities") or {}).get("draw"), 0.0)
    if draw_prob >= 28.0:
        tags.append("Draw risk")

    completeness = prediction.get("data_completeness") or {}
    completeness_tier = str(completeness.get("tier") or "")
    if completeness_tier in {"limited", "partial"}:
        tags.append("Limited data" if completeness_tier == "limited" else "Some live feeds missing")

    if str(prediction.get("play_type") or "").upper() == "AVOID":
        tags.append("High uncertainty")

    deduped: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(tag)
    return deduped[:4]


def _build_prediction_explainer(prediction: dict[str, Any], *, team_a_name: str, team_b_name: str) -> dict[str, Any]:
    best_pick = prediction.get("best_pick") if isinstance(prediction.get("best_pick"), dict) else {}
    completeness = prediction.get("data_completeness") or {}
    quality = str(prediction.get("data_quality") or "Moderate")
    edge_state = _prediction_edge_state(prediction)
    reason = str(
        best_pick.get("reasoning")
        or prediction.get("matchup_reading")
        or edge_state["summary"]
    ).strip()

    reliability_label = completeness.get("label") or f"{quality} data"
    reliability_note = completeness.get("summary") or (
        "Most pre-match inputs loaded cleanly." if quality == "Strong"
        else "Some live inputs were thin, so the model leaned more on fallback assumptions."
        if quality == "Limited"
        else "The prediction has a usable but not perfect pre-match data picture."
    )

    return {
        "headline": edge_state["title"],
        "summary": reason,
        "supporting_note": edge_state["summary"],
        "reliability_label": reliability_label,
        "reliability_note": reliability_note,
        "tags": _build_prediction_reason_tags(prediction),
        "raw_score_note": "",
    }


def _outcome_range_label(actual_prob: float | None) -> str | None:
    if actual_prob is None:
        return None
    if actual_prob <= 25:
        return "Major upset"
    if actual_prob <= 40:
        return "Notable upset"
    return "Expected range"


def _score_margin_from_display(final_score_display: str | None) -> int | None:
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", str(final_score_display or ""))
    if not m:
        return None
    return abs(int(m.group(1)) - int(m.group(2)))


def _summarize_injury_context(injuries: dict[str, dict]) -> str | None:
    if not injuries:
        return None

    segments = []
    for team_name, row in injuries.items():
        count = _safe_int((row or {}).get("count"))
        notable = [str(name).strip() for name in (row or {}).get("notable") or [] if str(name).strip()]
        if count <= 0 and not notable:
            continue
        if notable:
            notable_names = _natural_join(notable[:2])
            if count > len(notable):
                segments.append(f"{team_name} had {count} listed absences, including {notable_names}")
            else:
                segments.append(f"{team_name} were missing {notable_names}")
        else:
            segments.append(f"{team_name} had {count} listed absences")

    if not segments:
        return None
    return f"Injury context: {'; '.join(segments)}."


def _build_evidence_summary(
    record: dict,
    evidence_layer: str,
    *,
    form_compare: dict[str, dict] | None = None,
    injuries: dict[str, dict] | None = None,
    extra_points: list[str] | None = None,
) -> tuple[list[dict], list[str], str]:
    sport = str(record.get("sport") or "").lower()
    predicted_pick = _prediction_pick_display(record)
    predicted_prob = _predicted_outcome_probability(record)
    actual_prob = _actual_outcome_probability(record)
    upset_label = _outcome_range_label(actual_prob)
    actual_winner = record.get("actual_winner") or "Unknown"
    final_score = record.get("final_score_display") or "Unknown"
    winner_hit = record.get("winner_hit")
    winner_leg = "Hit" if winner_hit is True else "Miss" if winner_hit is False else "Pending"
    totals_pick = record.get("totals_pick_display")
    totals_hit = record.get("ou_hit")
    totals_leg = "Hit" if totals_hit is True else "Miss" if totals_hit is False else "Pending"
    total_scored = record.get("total_scored")
    actual_total_side = record.get("actual_total_side")
    confidence = record.get("confidence") or "Unknown"
    score_margin = _score_margin_from_display(final_score)
    is_soccer = sport == "soccer"
    measure_label = "goal" if is_soccer else "point"
    total_label = measure_label if total_scored == 1 else f"{measure_label}s"

    layer_labels = {
        "stats": "Full match stats",
        "events": "Match events",
        "summary": "Evidence summary",
    }
    summary_rows = [
        {"label": "Evidence Layer", "value": layer_labels.get(evidence_layer, "Evidence summary")},
        {"label": "Final Score", "value": final_score},
        {"label": "Actual Winner", "value": actual_winner},
    ]

    winner_value = f"{winner_leg} Â· {predicted_pick}"
    predicted_prob_text = _format_percent_value(predicted_prob)
    if predicted_prob_text:
        winner_value += f" Â· Model {predicted_prob_text}"
    summary_rows.append({"label": "Winner Leg", "value": winner_value})

    if totals_pick:
        totals_value = f"{totals_leg} Â· {totals_pick}"
        if total_scored is not None:
            totals_value += f" Â· {total_scored} {total_label}"
            if actual_total_side:
                totals_value += f" Â· {actual_total_side}"
        summary_rows.append({"label": "Totals Leg", "value": totals_value})

    if upset_label:
        outcome_value = upset_label
        actual_prob_text = _format_percent_value(actual_prob)
        if actual_prob_text:
            outcome_value += f" Â· {actual_winner} closed at {actual_prob_text}"
        summary_rows.append({"label": "Outcome Context", "value": outcome_value})

    summary_rows.append({"label": "Confidence", "value": confidence})

    summary_points = [point for point in (extra_points or []) if point]

    if winner_hit is True:
        if predicted_prob_text:
            summary_points.append(
                f"The winner leg aligned with the pre-match expectation: {predicted_pick} carried {predicted_prob_text} and got the result."
            )
        else:
            summary_points.append(f"The winner leg aligned with the pre-match expectation: {predicted_pick} got the result.")
    elif winner_hit is False:
        narrow_margin = (is_soccer and score_margin is not None and score_margin <= 1) or (
            not is_soccer and score_margin is not None and score_margin <= 6
        )
        if actual_prob is not None and actual_prob <= 25:
            summary_points.append(
                f"The winner leg missed on a genuine upset: {actual_winner} was only {_format_percent_value(actual_prob)} in the pre-match model."
            )
        elif narrow_margin and score_margin is not None:
            summary_points.append(
                f"The winner leg was a narrow miss: the model backed {predicted_pick}, but the game turned on a {score_margin}-{measure_label} margin."
            )
        else:
            summary_points.append(
                f"The winner leg missed: the model backed {predicted_pick}, but {actual_winner} ended up taking the result."
            )

    if totals_pick and total_scored is not None:
        totals_line = record.get("totals_line")
        try:
            totals_line = float(totals_line) if totals_line is not None else None
        except (TypeError, ValueError):
            totals_line = None

        if totals_hit is True:
            summary_points.append(
                f"The totals leg aligned with expectation: {totals_pick} matched a {total_scored}-{measure_label} game."
            )
        elif totals_hit is False:
            if totals_line is not None and abs(float(total_scored) - totals_line) <= 0.5:
                summary_points.append(
                    f"The totals leg was a narrow miss: {totals_pick} was one score away from landing."
                )
            else:
                summary_points.append(
                    f"The totals leg missed decisively: {totals_pick} did not match the {actual_total_side or 'final'} finish."
                )

    form_sentence = _summarize_form_compare(form_compare or {}, actual_winner=actual_winner)
    if form_sentence:
        summary_points.append(form_sentence)

    injury_sentence = _summarize_injury_context(injuries or {})
    if injury_sentence:
        summary_points.append(injury_sentence)

    if not summary_points:
        summary_points.append(f"Final result recorded at {final_score} with {confidence} model confidence.")

    summary_points = summary_points[:4]
    return summary_rows, summary_points, " ".join(summary_points)


def _build_soccer_key_events(raw_events: list[dict]) -> list[dict]:
    events = []
    seen = set()
    for event in raw_events[:60]:
        event_type = str(event.get("type") or "").strip()
        detail = str(event.get("detail") or event.get("comments") or "").strip()
        detail_lower = detail.lower()

        if event_type == "Goal":
            if "own goal" in detail_lower:
                display_type = "Own Goal"
            elif "penalty" in detail_lower:
                display_type = "Penalty Goal"
            else:
                display_type = "Goal"
        elif event_type == "Card":
            if "red" not in detail_lower:
                continue
            display_type = "Red Card"
        elif event_type == "Var":
            display_type = "VAR"
        else:
            continue

        elapsed = ((event.get("time") or {}).get("elapsed"))
        extra = ((event.get("time") or {}).get("extra"))
        if elapsed is None:
            elapsed_display = ""
        elif extra not in (None, "", 0):
            elapsed_display = f"{elapsed}+{extra}'"
        else:
            elapsed_display = f"{elapsed}'"

        player_name = ((event.get("player") or {}).get("name") or "")
        team_name = ((event.get("team") or {}).get("name") or "")
        dedupe_key = (elapsed_display, display_type, team_name, player_name, detail)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        events.append(
            {
                "minute": elapsed_display,
                "type": display_type,
                "team": team_name,
                "player": player_name,
                "detail": detail,
            }
        )
        if len(events) >= 8:
            break
    return events


def _build_prediction_vs_reality(record: dict, evidence_summary: str | None = None) -> dict:
    confidence = record.get("confidence") or "Unknown"
    winner_hit = bool(record.get("winner_hit")) if record.get("status") == "completed" else None
    winner_leg = "Hit" if winner_hit is True else "Miss" if winner_hit is False else "Pending"
    totals_required = bool(record.get("totals_required"))
    totals_hit = record.get("ou_hit")
    totals_leg = "Hit" if totals_hit is True else "Miss" if totals_hit is False else ("Not tracked" if not totals_required else "Pending")
    total_unit = "goals" if str(record.get("sport") or "").lower() == "soccer" else "points"

    upset_label = None
    actual_prob = _actual_outcome_probability(record)
    if actual_prob is not None:
        upset_label = _outcome_range_label(actual_prob)

    predicted_prob = _predicted_outcome_probability(record)
    actual_total = record.get("total_scored")
    if actual_total is not None:
        total_label = total_unit[:-1] if actual_total == 1 and total_unit.endswith("s") else total_unit
        reality_text = f"{record.get('actual_winner') or 'Unknown'}, {record.get('final_score_display') or 'Unknown'}, {actual_total} {total_label}"
    else:
        reality_text = _reality_sentence(record)

    return {
        "winner_pick": _prediction_sentence(record),
        "totals_pick": record.get("totals_pick_display"),
        "reality_text": reality_text,
        "winner_leg": winner_leg,
        "totals_leg": totals_leg,
        "overall_result": record.get("overall_game_result") or ("Win" if record.get("game_win") else "Loss"),
        "confidence": confidence,
        "upset_label": upset_label,
        "predicted_outcome_probability": predicted_prob,
        "actual_outcome_probability": actual_prob,
        "evidence_summary": evidence_summary or "Limited evidence available from the current data providers.",
    }


def _pick_stat_from_row(team_row: dict, aliases: list[str]) -> float | None:
    wanted = {alias.lower() for alias in aliases}
    for stat in team_row.get("statistics") or []:
        stat_type = str(stat.get("type") or "").strip().lower()
        if stat_type in wanted:
            return _parse_stat_value(stat.get("value"))
    return None


def _soccer_form_snapshot(team_id: int, league_id: int) -> dict | None:
    fixtures = ac.get_team_fixtures(team_id, league_id, SEASON, last=10)
    recent = pred.extract_form(pred.filter_recent_completed_fixtures(fixtures, current_season=SEASON), team_id)[:5]
    if not recent:
        return None
    return {
        "matches": len(recent),
        "wins": sum(1 for row in recent if row.get("result") == "W"),
        "draws": sum(1 for row in recent if row.get("result") == "D"),
        "losses": sum(1 for row in recent if row.get("result") == "L"),
        "avg_goals_for": round(sum(_safe_float(row.get("gf")) for row in recent) / len(recent), 2),
        "avg_goals_against": round(sum(_safe_float(row.get("ga")) for row in recent) / len(recent), 2),
    }


def _build_soccer_evidence(record: dict) -> dict:
    evidence = {
        "sport": "soccer",
        "available": False,
        "evidence_layer": "summary",
        "evidence_layer_label": "Evidence summary",
        "metrics": [],
        "key_events": [],
        "summary_rows": [],
        "summary_points": [],
        "goal_scorers": {
            "available": False,
            "home_team": record.get("team_a") or "Home",
            "away_team": record.get("team_b") or "Away",
            "home_goals": [],
            "away_goals": [],
        },
        "player_impacts": [],
        "injuries": {},
        "form_compare": {},
        "notes": [],
        "summary": "",
        "fixture_context": {},
    }

    league_id = _coerce_league_id(record.get("league_id"), default=DEFAULT_LEAGUE_ID)
    target_date = str(record.get("game_date") or record.get("date") or "")[:10]
    team_a_name = str(record.get("team_a") or "")
    team_b_name = str(record.get("team_b") or "")

    teams = _safe_external_call(lambda: ac.get_teams(league_id, SEASON), label="evidence-teams") or []

    team_a = _resolve_provider_team_by_name(team_a_name, teams)
    team_b = _resolve_provider_team_by_name(team_b_name, teams)
    team_a_id = _safe_int((team_a or {}).get("id"))
    team_b_id = _safe_int((team_b or {}).get("id"))

    fixture = None
    fixture_id = _safe_int(record.get("fixture_id"))
    if fixture_id:
        fixture = _safe_external_call(lambda: ac.get_fixture_by_id(fixture_id), label="evidence-fixture-by-id") or None

    if team_a_id and team_b_id and not fixture:
        h2h_candidates = _safe_external_call(
            lambda: ac.get_h2h(team_a_id, team_b_id, last=20),
            label="evidence-h2h",
        ) or []

        for item in h2h_candidates:
            if not _fixture_finished(item):
                continue
            fixture_date = str((item.get("fixture") or {}).get("date") or "")[:10]
            home_name = ((item.get("teams") or {}).get("home") or {}).get("name", "")
            away_name = ((item.get("teams") or {}).get("away") or {}).get("name", "")
            names_match = (
                (_team_names_match(team_a_name, home_name) and _team_names_match(team_b_name, away_name))
                or (_team_names_match(team_a_name, away_name) and _team_names_match(team_b_name, home_name))
            )
            if not names_match:
                continue
            if target_date and fixture_date != target_date:
                continue
            fixture = item
            break

        if fixture is None:
            for item in h2h_candidates:
                if not _fixture_finished(item):
                    continue
                home_name = ((item.get("teams") or {}).get("home") or {}).get("name", "")
                away_name = ((item.get("teams") or {}).get("away") or {}).get("name", "")
                names_match = (
                    (_team_names_match(team_a_name, home_name) and _team_names_match(team_b_name, away_name))
                    or (_team_names_match(team_a_name, away_name) and _team_names_match(team_b_name, home_name))
                )
                if names_match:
                    fixture = item
                    break

    if not fixture_id and fixture:
        fixture_id = _safe_int((fixture.get("fixture") or {}).get("id"))

    stats_rows = []
    raw_events = []
    if fixture_id:
        try:
            stats_rows = ac.get_fixture_stats(fixture_id)
        except Exception:
            _logger.warning("Failed to fetch fixture stats for fixture_id=%s", fixture_id, exc_info=True)
            stats_rows = []
        try:
            raw_events = ac.get_fixture_events(fixture_id)
        except Exception:
            _logger.warning("Failed to fetch fixture events for fixture_id=%s", fixture_id, exc_info=True)
            raw_events = []
        try:
            goal_scorers = ac.get_match_events(fixture_id)
            expected_goals = _safe_int(record.get("total_scored"))
            actual_goal_rows = len(goal_scorers.get("home_goals") or []) + len(goal_scorers.get("away_goals") or [])
            goal_scorers["available"] = bool(expected_goals > 0 and actual_goal_rows == expected_goals)
            if not goal_scorers.get("home_team"):
                goal_scorers["home_team"] = team_a_name
            if not goal_scorers.get("away_team"):
                goal_scorers["away_team"] = team_b_name
            evidence["goal_scorers"] = goal_scorers
        except Exception:
            _logger.warning("Failed to fetch goal scorers for fixture_id=%s", fixture_id, exc_info=True)

    team_a_row = next(
        (row for row in stats_rows if _team_names_match((row.get("team") or {}).get("name", ""), team_a_name)),
        {},
    )
    team_b_row = next(
        (row for row in stats_rows if _team_names_match((row.get("team") or {}).get("name", ""), team_b_name)),
        {},
    )

    metric_specs = [
        ("Possession", ["Ball Possession", "Possession"], True, True),
        ("Shots", ["Total Shots", "Shots"], False, True),
        ("Shots on Target", ["Shots on Goal", "Shots on Target"], False, True),
        ("xG", ["Expected Goals", "xG"], False, True),
        ("Corners", ["Corner Kicks", "Corners"], False, True),
        ("Fouls", ["Fouls", "Fouls Committed"], False, False),
        ("Yellow Cards", ["Yellow Cards"], False, False),
        ("Red Cards", ["Red Cards"], False, False),
    ]

    metrics = []
    support_score = 0
    for label, aliases, is_percent, higher_is_better in metric_specs:
        a_val = _pick_stat_from_row(team_a_row, aliases) if team_a_row else None
        b_val = _pick_stat_from_row(team_b_row, aliases) if team_b_row else None
        if a_val is None and b_val is None:
            continue

        leader = "Even"
        if a_val is not None and b_val is not None and a_val != b_val:
            a_better = a_val > b_val if higher_is_better else a_val < b_val
            leader = team_a_name if a_better else team_b_name
        elif a_val is not None and b_val is None:
            leader = team_a_name
        elif b_val is not None and a_val is None:
            leader = team_b_name

        actual_winner_code = str(record.get("actual_result") or "").lower()
        if actual_winner_code in {"a", "b"} and leader != "Even":
            winner_name = team_a_name if actual_winner_code == "a" else team_b_name
            support_score += 1 if leader == winner_name else -1

        metrics.append(
            {
                "label": label,
                "team_a": _format_stat_number(a_val, percent=is_percent),
                "team_b": _format_stat_number(b_val, percent=is_percent),
                "leader": leader,
            }
        )

    events = _build_soccer_key_events(raw_events)

    player_impacts = []
    if fixture_id:
        try:
            player_rows = ac.get_fixture_players(fixture_id)
        except Exception:
            _logger.warning("Failed to fetch player stats for fixture_id=%s", fixture_id, exc_info=True)
            player_rows = []

        for team_entry in player_rows:
            team_name = ((team_entry.get("team") or {}).get("name") or "")
            for player in team_entry.get("players") or []:
                player_name = ((player.get("player") or {}).get("name") or "")
                stat = (player.get("statistics") or [{}])[0]
                goals = _safe_int((stat.get("goals") or {}).get("total"))
                assists = _safe_int((stat.get("goals") or {}).get("assists"))
                shots_on = _safe_int((stat.get("shots") or {}).get("on"))
                rating = _safe_float((stat.get("games") or {}).get("rating"), default=0.0)
                impact = goals * 4 + assists * 3 + shots_on + (rating / 10)
                if impact <= 0:
                    continue
                player_impacts.append(
                    {
                        "name": player_name,
                        "team": team_name,
                        "goals": goals,
                        "assists": assists,
                        "shots_on": shots_on,
                        "rating": round(rating, 2) if rating else None,
                        "impact": round(impact, 2),
                    }
                )

    player_impacts.sort(key=lambda row: row.get("impact", 0), reverse=True)
    player_impacts = player_impacts[:6]

    form_compare = {}
    if team_a_id:
        snapshot = _soccer_form_snapshot(team_a_id, league_id)
        if snapshot:
            form_compare[team_a_name] = snapshot
    if team_b_id:
        snapshot = _soccer_form_snapshot(team_b_id, league_id)
        if snapshot:
            form_compare[team_b_name] = snapshot

    injuries = {}
    for team_name, team_id in ((team_a_name, team_a_id), (team_b_name, team_b_id)):
        if not team_id:
            continue
        try:
            items = _display_injuries(ac.get_injuries(league_id, SEASON, team_id))
        except Exception:
            _logger.warning("Failed to fetch injuries for team_name=%s league_id=%s", team_name, league_id, exc_info=True)
            items = []
        injuries[team_name] = {
            "count": len(items),
            "notable": [row.get("name") for row in items[:4] if row.get("name")],
        }

    injuries = _filter_useful_injury_context(injuries)

    actual_winner = record.get("actual_winner") or "Unknown"
    winner_support = [row["label"] for row in metrics if row.get("leader") == actual_winner][:3]
    decisive_goal = None
    if actual_winner == team_a_name:
        decisive_goal = (evidence["goal_scorers"].get("home_goals") or [None])[0]
    elif actual_winner == team_b_name:
        decisive_goal = (evidence["goal_scorers"].get("away_goals") or [None])[0]

    stats_available = bool(metrics)
    events_available = bool(events or evidence["goal_scorers"].get("available"))
    evidence_layer = "stats" if stats_available else "events" if events_available else "summary"

    extra_points = []
    if actual_winner == "Draw":
        if stats_available:
            extra_points.append(
                f"The match finished level at {record.get('final_score_display') or '0-0'}, and the available match stats did not show a decisive edge for either side."
            )
        elif events_available:
            extra_points.append(
                f"The result settled at {record.get('final_score_display') or '0-0'}, with the event feed showing the main swing moments rather than a full stat profile."
            )
    elif winner_support:
        extra_points.append(f"Match stats backed {actual_winner} through {_natural_join(winner_support)}.")
    elif stats_available and support_score < 0:
        extra_points.append(
            f"The match stats were mixed and did not cleanly support {actual_winner}, which points to a clinical or swing-moment result rather than full control."
        )
    elif stats_available:
        extra_points.append(
            f"The available match stats showed a narrow edge for {actual_winner} rather than one-way control."
        )

    if decisive_goal and decisive_goal.get("player"):
        goal_text = f"The decisive scoring moment came from {decisive_goal['player']} at {decisive_goal['minute']}"
        if decisive_goal.get("type") not in {"Goal", ""}:
            goal_text += f" ({str(decisive_goal['type']).lower()})"
        extra_points.append(goal_text + ".")

    if not stats_available and events:
        event_labels = [row.get("type") for row in events[:3] if row.get("type")]
        if event_labels:
            extra_points.append(
                f"When full match stats were unavailable, the event feed still captured the key turns through {_natural_join(event_labels)}."
            )

    summary_rows, summary_points, summary = _build_evidence_summary(
        record,
        evidence_layer,
        form_compare=form_compare,
        injuries=injuries,
        extra_points=extra_points,
    )

    evidence.update(
        {
            "available": bool(summary_rows or metrics or events or player_impacts or evidence["goal_scorers"].get("available")),
            "evidence_layer": evidence_layer,
            "evidence_layer_label": {
                "stats": "Full match stats",
                "events": "Match events",
                "summary": "Evidence summary",
            }.get(evidence_layer, "Evidence summary"),
            "metrics": metrics,
            "key_events": events,
            "player_impacts": player_impacts,
            "injuries": injuries,
            "form_compare": form_compare,
            "summary_rows": summary_rows,
            "summary_points": summary_points,
            "summary": summary,
            "fixture_context": {
                "fixture_id": fixture_id if fixture_id else None,
                "fixture_date": str(((fixture or {}).get("fixture") or {}).get("date") or "")[:10],
            },
        }
    )
    return evidence


def _build_nba_evidence(record: dict) -> dict:
    evidence = {
        "sport": "nba",
        "available": False,
        "evidence_layer": "summary",
        "evidence_layer_label": "Evidence summary",
        "metrics": [],
        "key_events": [],
        "summary_rows": [],
        "summary_points": [],
        "goal_scorers": {
            "available": False,
            "home_team": record.get("team_a") or "Home",
            "away_team": record.get("team_b") or "Away",
            "home_goals": [],
            "away_goals": [],
        },
        "player_impacts": [],
        "injuries": {},
        "form_compare": {},
        "notes": [],
        "summary": "",
    }

    if nc is None or np_nba is None:
        summary_rows, summary_points, summary = _build_evidence_summary(
            record,
            "summary",
            extra_points=["Live NBA providers were not configured, so this page falls back to tracked result data only."],
        )
        evidence.update(
            {
                "available": True,
                "summary_rows": summary_rows,
                "summary_points": summary_points,
                "summary": summary,
            }
        )
        return evidence

    team_a_name = str(record.get("team_a") or "")
    team_b_name = str(record.get("team_b") or "")
    target_date = str(record.get("game_date") or record.get("date") or "")[:10]

    candidate_days = []
    if target_date:
        try:
            candidate_days.append(datetime.fromisoformat(target_date))
        except ValueError:
            pass
    candidate_days.extend([datetime.now() - timedelta(days=1), datetime.now(), datetime.now() + timedelta(days=1)])

    matched_game = None
    fixture_id = str(record.get("fixture_id") or "").strip()
    if fixture_id:
        try:
            matched_game = nc.get_event_snapshot(fixture_id, date_hint=target_date)
        except Exception:
            _logger.warning("Failed to fetch NBA event snapshot for fixture_id=%s", fixture_id, exc_info=True)
            matched_game = None

    seen_ids = set()
    for day in candidate_days:
        if matched_game:
            break
        try:
            games = nc.get_scoreboard_games(day)
        except Exception:
            _logger.warning("Failed to fetch NBA scoreboard games for date=%s", day, exc_info=True)
            games = []
        for game in games:
            game_id = str(game.get("id") or "")
            if game_id in seen_ids:
                continue
            seen_ids.add(game_id)
            home_name = ((game.get("teams") or {}).get("home") or {}).get("name", "")
            away_name = ((game.get("teams") or {}).get("visitors") or {}).get("name", "")
            names_match = (
                (_team_names_match(team_a_name, home_name) and _team_names_match(team_b_name, away_name))
                or (_team_names_match(team_a_name, away_name) and _team_names_match(team_b_name, home_name))
            )
            if not names_match:
                continue
            game_date = str((game.get("date") or {}).get("start") or "")[:10]
            if target_date and game_date != target_date:
                continue
            matched_game = game
            break
        if matched_game:
            break

    if not matched_game:
        summary_rows, summary_points, summary = _build_evidence_summary(
            record,
            "summary",
            extra_points=["Detailed scoreboard evidence for this game was unavailable in the current NBA feeds."],
        )
        evidence.update(
            {
                "available": True,
                "summary_rows": summary_rows,
                "summary_points": summary_points,
                "summary": summary,
            }
        )
        return evidence

    teams = matched_game.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("visitors") or {}

    is_a_home = _team_names_match(team_a_name, home.get("name", ""))
    scores = matched_game.get("scores") or {}
    home_pts = _safe_int((scores.get("home") or {}).get("points"))
    away_pts = _safe_int((scores.get("visitors") or {}).get("points"))
    a_pts = home_pts if is_a_home else away_pts
    b_pts = away_pts if is_a_home else home_pts

    evidence["metrics"].append({"label": "Final Points", "team_a": str(a_pts), "team_b": str(b_pts), "leader": team_a_name if a_pts > b_pts else team_b_name})

    team_a_id = str(home.get("id") if is_a_home else away.get("id") or "")
    team_b_id = str(away.get("id") if is_a_home else home.get("id") or "")

    for label, field in (("PPG", "ppg"), ("Opp PPG", "opp_ppg"), ("Net Rating", "net_rtg")):
        try:
            stats_a = nc.get_team_season_stats(team_a_id)
        except Exception:
            _logger.warning("Failed to fetch NBA season stats for team_a_id=%s", team_a_id, exc_info=True)
            stats_a = None
        try:
            stats_b = nc.get_team_season_stats(team_b_id)
        except Exception:
            _logger.warning("Failed to fetch NBA season stats for team_b_id=%s", team_b_id, exc_info=True)
            stats_b = None
        a_val = stats_a.get(field) if isinstance(stats_a, dict) else None
        b_val = stats_b.get(field) if isinstance(stats_b, dict) else None
        if a_val is None and b_val is None:
            continue
        evidence["metrics"].append(
            {
                "label": label,
                "team_a": _format_stat_number(_safe_float(a_val) if a_val is not None else None),
                "team_b": _format_stat_number(_safe_float(b_val) if b_val is not None else None),
                "leader": team_a_name if _safe_float(a_val, -9999) > _safe_float(b_val, -9999) else team_b_name,
            }
        )

    leaders = matched_game.get("leaders") or {}
    if is_a_home:
        a_leaders = leaders.get("home") or []
        b_leaders = leaders.get("visitors") or []
    else:
        a_leaders = leaders.get("visitors") or []
        b_leaders = leaders.get("home") or []

    for side_name, side_leaders in ((team_a_name, a_leaders), (team_b_name, b_leaders)):
        for leader in side_leaders:
            top = (leader.get("leaders") or [{}])[0]
            athlete = top.get("athlete") or {}
            evidence["player_impacts"].append(
                {
                    "name": athlete.get("displayName") or athlete.get("fullName") or "",
                    "team": side_name,
                    "metric": leader.get("displayName") or leader.get("name") or "",
                    "value": top.get("displayValue") or "",
                }
            )

    evidence["player_impacts"] = [row for row in evidence["player_impacts"] if row.get("name")][:6]

    for name, team_id in ((team_a_name, team_a_id), (team_b_name, team_b_id)):
        if not team_id:
            continue
        try:
            injuries = nc.get_team_injuries(team_id)
        except Exception:
            _logger.warning("Failed to fetch NBA injuries for team_id=%s", team_id, exc_info=True)
            injuries = []
        evidence["injuries"][name] = {
            "count": len(injuries),
            "notable": [
                " ".join(filter(None, [
                    (row.get("player") or {}).get("firstname"),
                    (row.get("player") or {}).get("lastname"),
                ])).strip()
                for row in injuries[:4]
            ],
        }

        try:
            raw_form = nc.get_team_recent_form(team_id, n=5)
            extracted = np_nba.extract_recent_form(raw_form, team_id, n=5)
        except Exception:
            _logger.warning("Failed to fetch NBA recent form for team_id=%s", team_id, exc_info=True)
            extracted = []
        if extracted:
            evidence["form_compare"][name] = {
                "matches": len(extracted),
                "wins": sum(1 for row in extracted if row.get("result") == "W"),
                "losses": sum(1 for row in extracted if row.get("result") == "L"),
                "avg_points_for": round(sum(_safe_float(row.get("our_pts")) for row in extracted) / len(extracted), 2),
                "avg_points_against": round(sum(_safe_float(row.get("their_pts")) for row in extracted) / len(extracted), 2),
            }

    evidence["injuries"] = _filter_useful_injury_context(evidence["injuries"])
    evidence_layer = "stats" if evidence["metrics"] else "summary"
    extra_points = []
    if evidence["metrics"]:
        extra_points.append("Available scoreboard data and season-profile metrics explain how the final result landed.")
    if evidence["player_impacts"]:
        extra_points.append("Top individual leaders from the live feed are included below to show where the production came from.")

    summary_rows, summary_points, summary = _build_evidence_summary(
        record,
        evidence_layer,
        form_compare=evidence["form_compare"],
        injuries=evidence["injuries"],
        extra_points=extra_points,
    )

    evidence.update(
        {
            "available": bool(summary_rows or evidence["metrics"] or evidence["player_impacts"]),
            "evidence_layer": evidence_layer,
            "evidence_layer_label": {
                "stats": "Full match stats",
                "summary": "Evidence summary",
            }.get(evidence_layer, "Evidence summary"),
            "summary_rows": summary_rows,
            "summary_points": summary_points,
            "summary": summary,
        }
    )
    return evidence


def _clear_selected_matchup() -> None:
    for key in (
        "team_a_id",
        "team_a_name",
        "team_a_logo",
        "team_b_id",
        "team_b_name",
        "team_b_logo",
        "selected_fixture",
    ):
        session.pop(key, None)


def _coerce_league_id(value, default: int = DEFAULT_LEAGUE_ID) -> int:
    league_id = _safe_int(value, default)
    if league_id not in SUPPORTED_LEAGUE_IDS:
        return default
    return league_id


def _active_league_id() -> int:
    requested = request.args.get("league")
    if requested is None:
        requested = request.args.get("league_id")
    if requested is None:
        requested = request.form.get("league_id")

    if requested is not None and str(requested).strip() != "":
        return _coerce_league_id(requested)

    stored = session.get(LEAGUE_SESSION_KEY)
    if stored is None:
        stored = session.get("football_league_id")
    return _coerce_league_id(stored, DEFAULT_LEAGUE_ID)


def _set_active_league(league_id: int) -> int:
    normalized = _coerce_league_id(league_id)
    previous = _coerce_league_id(session.get(LEAGUE_SESSION_KEY), DEFAULT_LEAGUE_ID)
    session[LEAGUE_SESSION_KEY] = normalized
    session["football_league_id"] = normalized
    if previous != normalized:
        _clear_selected_matchup()
    return normalized


def _league_context(league_id: int | None = None) -> dict:
    selected_id = _coerce_league_id(league_id if league_id is not None else _active_league_id())
    redis_key = cache_service.make_key("league-meta", selected_id)
    redis_cached = cache_service.get_json(redis_key)
    if redis_cached is not None:
        _logger.debug("league_cache hit(redis) league_id=%s", selected_id)
        selected_cfg = redis_cached
        return {
            "supported_leagues": _football_supported_leagues(),
            "current_league_id": selected_id,
            "current_league": selected_cfg,
        }
    if selected_id in _local_league_cache:
        _logger.debug("league_cache hit league_id=%s", selected_id)
        selected_cfg = _local_league_cache[selected_id]
    else:
        _logger.debug("league_cache miss league_id=%s", selected_id)
        selected_cfg = LEAGUE_BY_ID.get(selected_id, LEAGUE_BY_ID.get(DEFAULT_LEAGUE_ID, {}))
        _local_league_cache[selected_id] = selected_cfg
        cache_service.set_json(redis_key, selected_cfg, ttl=3600)
    return {
        "supported_leagues": _football_supported_leagues(),
        "current_league_id": selected_id,
        "current_league": selected_cfg,
    }


def _tracking_window_dates() -> tuple[set[str], list[str]]:
    # Picks are only recorded for games that have not started; finished games are graded, never predicted.
    today = date.today()
    tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    required = {today.strftime("%Y-%m-%d")}
    return required, [today.strftime("%Y-%m-%d"), tomorrow]


def _tracking_is_complete() -> bool:
    required_dates, _ = _tracking_window_dates()
    predictions = mt._load_predictions()
    seen_dates = {p.get("date", "") for p in predictions if p.get("sport", "").lower() in {"soccer", "nba"}}
    return required_dates.issubset(seen_dates)


def _bootstrap_model_tracking() -> dict[str, int]:
    required_dates, tracking_dates = _tracking_window_dates()
    inserted = 0
    updated = 0
    predictions_generated = 0
    soccer_fetched = 0
    nba_fetched = 0

    existing_keys = {
        mt._get_game_key(
            p.get("sport", ""),
            p.get("date", ""),
            p.get("team_a", ""),
            p.get("team_b", ""),
            p.get("league_id"),
        )
        for p in mt._load_predictions()
    }

    def _soccer_fixture_key(fixture: dict) -> str:
        fixture_date = str((fixture.get("fixture") or {}).get("date") or "")[:10]
        home = (fixture.get("teams") or {}).get("home", {}).get("name", "")
        away = (fixture.get("teams") or {}).get("away", {}).get("name", "")
        return f"{fixture_date}|{home}|{away}"

    fixtures_seen: set[str] = set()
    soccer_fixtures: list[dict] = []
    for league_id in SUPPORTED_LEAGUE_IDS:
        try:
            upcoming = ac.get_upcoming_fixtures(league_id, SEASON, next_n=40)
        except Exception:
            _logger.warning("Failed to fetch upcoming fixtures for league_id=%s during tracking", league_id, exc_info=True)
            upcoming = []

        for fixture in upcoming:
            fixture_date = str((fixture.get("fixture") or {}).get("date") or "")[:10]
            if fixture_date not in tracking_dates:
                continue
            key = _soccer_fixture_key(fixture)
            if key in fixtures_seen:
                continue
            fixtures_seen.add(key)
            soccer_fixtures.append(fixture)

        slug = getattr(ac, "ESPN_SLUG_BY_LEAGUE", {}).get(league_id)
        if not slug:
            continue

        try:
            espn_fixtures = ac.get_espn_fixtures(slug, next_n=40)
        except Exception:
            _logger.warning("Failed to fetch ESPN fixtures for league_id=%s slug=%s during tracking", league_id, slug, exc_info=True)
            espn_fixtures = []

        for fixture in espn_fixtures:
            fixture_date = str((fixture.get("fixture") or {}).get("date") or "")[:10]
            if fixture_date not in tracking_dates:
                continue
            key = _soccer_fixture_key(fixture)
            if key in fixtures_seen:
                continue
            fixtures_seen.add(key)
            soccer_fixtures.append(fixture)

    soccer_fetched = len(soccer_fixtures)

    standings_cache: dict[int, list[dict]] = {}
    for fixture in soccer_fixtures:
        fixture_info = fixture.get("fixture") or {}
        kickoff = str(fixture_info.get("date") or "")
        if not is_pregame(kickoff, (fixture_info.get("status") or {}).get("short")):
            continue
        fixture_date = kickoff[:10]
        home = (fixture.get("teams") or {}).get("home", {})
        away = (fixture.get("teams") or {}).get("away", {})
        home_id = home.get("id")
        away_id = away.get("id")
        home_name = home.get("name", "Home")
        away_name = away.get("name", "Away")

        league_id = (fixture.get("league") or {}).get("id") or DEFAULT_LEAGUE_ID
        if league_id not in standings_cache:
            try:
                standings_cache[league_id] = ac.get_standings(league_id, SEASON)
            except Exception:
                _logger.warning("Failed to fetch standings for league_id=%s during tracking", league_id, exc_info=True)
                standings_cache[league_id] = []

        opp_strengths = _build_opp_strengths(standings_cache.get(league_id, []))

        try:
            h2h_raw = ac.get_h2h(home_id, away_id, last=10) if home_id and away_id else []
        except Exception:
            _logger.warning("Failed to fetch H2H for %s vs %s during tracking", home_name, away_name, exc_info=True)
            h2h_raw = []
        try:
            fixtures_home = ac.get_team_fixtures(home_id, league_id, SEASON, last=10) if home_id else []
        except Exception:
            _logger.warning("Failed to fetch home fixtures for %s during tracking", home_name, exc_info=True)
            fixtures_home = []
        try:
            fixtures_away = ac.get_team_fixtures(away_id, league_id, SEASON, last=10) if away_id else []
        except Exception:
            _logger.warning("Failed to fetch away fixtures for %s during tracking", away_name, exc_info=True)
            fixtures_away = []
        try:
            injuries_home = _clean_injuries(ac.get_injuries(league_id, SEASON, home_id)) if home_id else []
        except Exception:
            _logger.warning("Failed to fetch home injuries for %s during tracking", home_name, exc_info=True)
            injuries_home = []
        try:
            injuries_away = _clean_injuries(ac.get_injuries(league_id, SEASON, away_id)) if away_id else []
        except Exception:
            _logger.warning("Failed to fetch away injuries for %s during tracking", away_name, exc_info=True)
            injuries_away = []

        form_home = pred.extract_form(
            pred.filter_recent_completed_fixtures(fixtures_home, current_season=SEASON),
            home_id,
        )[:5]
        form_away = pred.extract_form(
            pred.filter_recent_completed_fixtures(fixtures_away, current_season=SEASON),
            away_id,
        )[:5]
        h2h_form_home = pred.extract_form(
            pred.filter_recent_completed_fixtures(h2h_raw, current_season=SEASON, seasons_back=5),
            home_id,
        )[:5]
        h2h_form_away = pred.extract_form(
            pred.filter_recent_completed_fixtures(h2h_raw, current_season=SEASON, seasons_back=5),
            away_id,
        )[:5]

        try:
            prediction = se.scorpred_predict(
                form_a=form_home,
                form_b=form_away,
                h2h_form_a=h2h_form_home,
                h2h_form_b=h2h_form_away,
                injuries_a=injuries_home,
                injuries_b=injuries_away,
                team_a_is_home=True,
                team_a_name=home_name,
                team_b_name=away_name,
                sport="soccer",
                opp_strengths=opp_strengths,
            )
        except Exception:
            _logger.warning("Soccer prediction engine failed for %s vs %s during tracking", home_name, away_name, exc_info=True)
            continue
        prediction = evidence_services._with_ratings_model(ac, league_id, prediction, home_name, away_name, _logger)

        best_pick = prediction.get("best_pick", {})
        pred_winner = best_pick.get("prediction", "")
        probs = prediction.get("win_probabilities", {})
        conf = best_pick.get("confidence", "Low")
        game_key = mt._get_game_key("soccer", fixture_date, home_name, away_name, league_id)
        existing = game_key in existing_keys
        quote = odds_fetcher.fetch_match_odds("soccer", home_name, away_name, kickoff=kickoff, league_id=league_id)

        try:
            mt.save_prediction(
                sport="soccer",
                team_a=home_name,
                team_b=away_name,
                predicted_winner=pred_winner,
                win_probs=probs,
                confidence=conf,
                game_date=fixture_date,
                league_id=league_id,
                league_name=(LEAGUE_BY_ID.get(league_id) or {}).get("name"),
                prediction_notes=best_pick.get("reasoning"),
                model_factors={
                    "team_a": prediction.get("components_a") if isinstance(prediction.get("components_a"), dict) else {},
                    "team_b": prediction.get("components_b") if isinstance(prediction.get("components_b"), dict) else {},
                },
                fixture_id=(fixture.get("fixture") or {}).get("id"),
                totals_pick=((_extract_totals_leg(prediction) or {}).get("pick")),
                totals_line=((_extract_totals_leg(prediction) or {}).get("line")),
                totals_market=((_extract_totals_leg(prediction) or {}).get("market")),
                kickoff=kickoff,
                market_odds=_quote_odds(quote),
                predicted_score=_soccer_score_forecast(prediction),
                raw_probs=prediction.get("raw_win_probabilities"),
            )
            predictions_generated += 1
            if existing:
                updated += 1
            else:
                inserted += 1
                existing_keys.add(game_key)
        except Exception:
            _logger.warning("Failed to save soccer tracking prediction for %s vs %s", home_name, away_name, exc_info=True)
            continue

    # Track NBA games
    nba_games: list[dict] = []

    if nc is not None and np_nba is not None:
        import nba_routes

        try:
            nba_games.extend(nc.get_upcoming_games(next_n=40, days_ahead=1))
        except Exception:
            _logger.warning("Failed to fetch NBA upcoming games during tracking", exc_info=True)

        game_ids: set[str] = set()
        unique_nba_games: list[dict] = []
        for game in nba_games:
            game_id = str(game.get("id", ""))
            if not game_id or game_id in game_ids:
                continue
            game_ids.add(game_id)
            unique_nba_games.append(game)

        nba_fetched = len(unique_nba_games)

        try:
            team_map = {str(team["id"]): team for team in (nc.get_teams() or [])}
        except Exception:
            team_map = {}
        try:
            nba_opp_strengths = nba_routes._build_nba_opp_strengths(nc.get_standings() or {})
        except Exception:
            _logger.warning("Failed to build NBA opponent strengths from standings during tracking", exc_info=True)
            nba_opp_strengths = {}

        for game in unique_nba_games:
            teams_block = game.get("teams") or {}
            home_team = teams_block.get("home") or {}
            away_team = teams_block.get("visitors") or {}

            home_name = home_team.get("name") or home_team.get("nickname", "Home")
            away_name = away_team.get("name") or away_team.get("nickname", "Away")
            kickoff = str((game.get("date") or {}).get("start") or "")
            fixture_date = kickoff[:10]
            if not is_pregame(kickoff, game.get("status")):
                continue

            # Track exactly what the NBA dashboard publishes; SKIP cards are not tracked picks.
            try:
                prediction = nba_routes._build_upcoming_prediction_card(game, team_map, nba_opp_strengths).get("prediction")
            except Exception:
                _logger.warning("NBA prediction engine failed for %s vs %s during tracking", home_name, away_name, exc_info=True)
                continue
            if not prediction:
                continue

            best_pick = prediction.get("best_pick", {})
            pred_winner = best_pick.get("prediction", "")
            probs = prediction.get("win_probabilities", {})
            conf = best_pick.get("confidence", "Low")
            game_key = mt._get_game_key("nba", fixture_date, home_name, away_name)
            existing = game_key in existing_keys

            try:
                mt.save_prediction(
                    sport="nba",
                    team_a=home_name,
                    team_b=away_name,
                    predicted_winner=pred_winner,
                    win_probs=probs,
                    confidence=conf,
                    game_date=fixture_date,
                    prediction_notes=best_pick.get("reasoning"),
                    model_factors={
                        "team_a": prediction.get("components_a") if isinstance(prediction.get("components_a"), dict) else {},
                        "team_b": prediction.get("components_b") if isinstance(prediction.get("components_b"), dict) else {},
                    },
                    fixture_id=game.get("id"),
                    totals_pick=((_extract_totals_leg(prediction) or {}).get("pick")),
                    totals_line=((_extract_totals_leg(prediction) or {}).get("line")),
                    totals_market=((_extract_totals_leg(prediction) or {}).get("market")),
                    kickoff=kickoff,
                    market_odds=prediction.get("market_odds"),
                    predicted_score=_nba_score_forecast(prediction),
                    raw_probs=prediction.get("raw_win_probabilities"),
                )
                predictions_generated += 1
                if existing:
                    updated += 1
                else:
                    inserted += 1
                    existing_keys.add(game_key)
            except Exception:
                _logger.warning("Failed to save NBA tracking prediction for %s vs %s", home_name, away_name, exc_info=True)
                continue
    else:
        app.logger.warning("NBA tracking skipped because nba_live_client or nba_predictor is unavailable.")
        nba_fetched = 0

    result_stats = ru.update_pending_predictions()
    completed_updated = result_stats.get("updated", 0)
    app.logger.debug(
        "model_tracking: soccer_fetched=%d nba_fetched=%d predictions_generated=%d inserted=%d updated=%d results_updated=%d",
        soccer_fetched,
        nba_fetched,
        predictions_generated,
        inserted,
        updated,
        completed_updated,
    )

    return {
        "soccer_fetched": soccer_fetched,
        "nba_fetched": nba_fetched,
        "predictions_generated": predictions_generated,
        "inserted": inserted,
        "updated": updated,
        "results_updated": completed_updated,
    }


def _run_prediction_tracking() -> dict[str, Any]:
    """Scheduled every 30 minutes: record today's and tomorrow's published picks, then grade started games."""
    with app.app_context():
        recorded = _bootstrap_model_tracking()
        graded = ru.update_pending_predictions(started_only=True)
    return {"recorded": recorded, "graded": {k: graded.get(k) for k in ("checked", "found", "updated", "failed")}}


def _ensure_model_tracking():
    if not _tracking_is_complete():
        app.logger.info("Model performance tracking bootstrap triggered.")
        _bootstrap_model_tracking()


@app.before_request
def _bootstrap_tracking_daily():
    if app.config.get("TESTING") or request.endpoint in {None, "static", "health", "liveness", "api_health", "api_session"}:
        return
    today_str = date.today().strftime("%Y-%m-%d")
    if app.config.get("TRACKING_LAST_BOOTSTRAP") == today_str:
        return
    # Mark immediately so subsequent requests don't also trigger bootstrap
    app.config["TRACKING_LAST_BOOTSTRAP"] = today_str
    import threading
    def _run():
        try:
            with app.app_context():
                _ensure_model_tracking()
        except Exception as exc:
            app.logger.debug("Daily tracking check failed: %s", exc, exc_info=True)
    threading.Thread(target=_run, daemon=True).start()



def _resolve_provider_team_by_name(name: str, teams: list[dict]) -> dict | None:
    return bootstrap_services.resolve_provider_team_by_name(name, teams)


def _selected_fixture() -> dict:
    return bootstrap_services.selected_fixture(session)


def _soccer_unavailable_page(team_a, team_b, selected, *, template, inputs=()):
    """Apply the same validity policy to the retained HTML analysis routes."""
    fixture = _FIXTURE_INDEX.get(str((selected or {}).get("fixture_id"))) or {}
    info = fixture.get("fixture") or {}
    kickoff = info.get("date") or (selected or {}).get("date")
    status = info.get("status") or (selected or {}).get("status")
    source = worst([fixture.get("data_freshness"), from_records(inputs)])
    reason = unavailable_reason(kickoff, status, source) if kickoff else None
    if not reason and source and freshness_state(source) != "fresh":
        reason = "Recent form needs a fresh provider update before a pregame recommendation is available."
    if not reason:
        return None
    analysis = {"match_id": str((selected or {}).get("fixture_id") or ""),
                "matchup": f'{team_a["name"]} vs {team_b["name"]}', "action": "SKIP", "confidence": 0,
                "recommended_side": "Unavailable", "probabilities": {"a": None, "draw": None, "b": None}, "metric_breakdown": None,
                "data_quality": "Limited Data", "reason": reason}
    scorpred = {"decision_card": dui.build_decision_card(analysis=analysis), "play_type": "SKIP", "confidence_pct": 0,
                "win_probabilities": {}, "best_pick": {"prediction": "Unavailable", "reasoning": reason}}
    return render_template(template, **_page_context(team_a=team_a, team_b=team_b, selected_fixture=selected,
                           scorpred=scorpred, prediction={"ui_prediction": scorpred}, **_league_context(_active_league_id())))



def _soccer_score_forecast(prediction: dict) -> dict | None:
    """Expected goals and most likely exact score from the ratings model, when it produced the pick."""
    fitted = prediction.get("ratings_model") or {}
    goals, likely = fitted.get("expected_goals") or {}, fitted.get("most_likely_score") or {}
    if goals.get("home") is None or goals.get("away") is None:
        return None
    forecast = {"a": goals["home"], "b": goals["away"]}
    if likely.get("home") is not None:
        forecast["likely"] = {"a": likely["home"], "b": likely["away"]}
    return forecast


def _nba_score_forecast(prediction: dict) -> dict | None:
    model = prediction.get("model") or {}
    if model.get("home_score") is None or model.get("away_score") is None:
        return None
    return {"a": model["home_score"], "b": model["away_score"]}


def _quote_odds(quote: dict | None) -> dict | None:
    """Tracker price shape from an odds_fetcher quote; None when the quote is unavailable."""
    if not (quote or {}).get("available"):
        return None
    return {"a": quote.get("home_odds"), "b": quote.get("away_odds"), "draw": quote.get("draw_odds"),
            "bookmaker": quote.get("bookmaker")}


def _load_upcoming_fixtures(
    next_n: int = 20,
    max_deep_predictions: int = 6,
    league: int = None,
    *,
    include_injuries: bool = True,
    include_standings: bool = True,
    selected_date: str | None = None,
):
    return evidence_services.load_upcoming_fixtures(
        ac,
        pred,
        se,
        league=league if league is not None else LEAGUE,
        season=SEASON,
        logger=app.logger,
        football_data_source=_football_data_source,
        next_n=next_n,
        max_deep_predictions=max_deep_predictions,
        include_injuries=include_injuries,
        include_standings=include_standings,
        selected_date=selected_date,
    )


def load_fixtures_cached(league_id: int, *, selected_date: str | None = None):
    def fresh(data):
        return bool(data) and all(not f.get("data_freshness") or freshness_state(f["data_freshness"]) == "fresh" for f in data[0] or [])
    redis_key = (cache_service.make_key("fixtures-day-v1", league_id, selected_date) if selected_date
                 else cache_service.make_key("fixtures", league_id))
    local_key = (league_id, selected_date) if selected_date else league_id
    redis_cached = cache_service.get_json(redis_key)
    if redis_cached is not None and fresh(redis_cached):
        _logger.debug("fixture_cache hit(redis) league_id=%s", league_id)
        data = tuple(redis_cached)
        _restore_fixture_index(data[0])
        return data
    if local_key in _local_fixture_cache and fresh(_local_fixture_cache[local_key]):
        _logger.debug("fixture_cache hit league_id=%s", league_id)
        data = _local_fixture_cache[local_key]
        _restore_fixture_index(data[0])
        return data
    _logger.debug("fixture_cache miss league_id=%s", league_id)
    data = _load_upcoming_fixtures(
        next_n=24,
        max_deep_predictions=8,
        league=league_id,
        include_injuries=False,
        include_standings=True,
        selected_date=selected_date,
    )
    fixtures = (data or ([], None, _football_data_source(), ""))[0]
    load_error = (data or ([], None, _football_data_source(), ""))[1]
    source_meta = from_records(fixtures)
    health = ({"status": "success" if freshness_state(source_meta) == "fresh" else "degraded", **source_meta} if source_meta else {})
    if not fixtures and hasattr(ac, "get_last_refresh_status"):
        health = ac.get_last_refresh_status(league_id)
        if isinstance(health, dict) and health.get("status") == "success":
            load_error = None
    if data and health:
        data = (fixtures, load_error, data[2], health)
    _restore_fixture_index(fixtures)
    if fixtures and not load_error:
        _local_fixture_cache[local_key] = data
        cache_service.set_json(redis_key, list(data), ttl=120)
    return data


def _restore_fixture_index(fixtures):
    """Rehydrate the analysis lookup after any disk/Redis cache hit."""
    for fixture in fixtures or []:
        fixture_id = (fixture.get("fixture") or {}).get("id")
        if fixture_id is not None:
            _FIXTURE_INDEX[str(fixture_id)] = fixture


def _analysis_from_fixture(fixture: dict[str, Any]) -> dict[str, Any] | None:
    if _MATCH_BRAIN is None:
        return None
    canonical = _MATCH_BRAIN.canonical_from_fixture(fixture)
    if not canonical:
        return None
    pred_block = canonical.get("prediction") or {}
    metric_breakdown = canonical.get("metric_breakdown") or {}
    return {
        "match_id": canonical.get("match_id", ""),
        "matchup": canonical.get("matchup", ""),
        "league": canonical.get("league", ""),
        "kickoff": canonical.get("kickoff", ""),
        "status": canonical.get("status", "scheduled"),
        "data_freshness": canonical.get("data_freshness") or fixture.get("data_freshness") or {},
        "prediction": pred_block,
        "model_source": canonical.get("model_source"),
        "recommended_side": canonical.get("recommended_side"),
        "action": canonical.get("action", "SKIP"),
        "confidence": canonical.get("confidence", 0),
        "probabilities": {
            "a": (canonical.get("probabilities") or {}).get("home"),
            "draw": (canonical.get("probabilities") or {}).get("draw"),
            "b": (canonical.get("probabilities") or {}).get("away"),
        },
        "data_quality": _data_quality_label(canonical.get("data_quality")),
        "reason": canonical.get("reason") or "Decision generated from available model evidence.",
        "metric_breakdown": {
            "model_probability": metric_breakdown.get("model_probability"),
            "implied_probability": metric_breakdown.get("implied_probability"),
            "edge_score": metric_breakdown.get("edge_score"),
            "expected_value": metric_breakdown.get("expected_value"),
            "risk_score": metric_breakdown.get("risk_score"),
            "risk_level": metric_breakdown.get("risk_level"),
            "decision_grade": metric_breakdown.get("decision_grade"),
        },
        "model_probability": metric_breakdown.get("model_probability"),
        "implied_probability": metric_breakdown.get("implied_probability"),
        "edge_score": metric_breakdown.get("edge_score"),
        "expected_value": metric_breakdown.get("expected_value"),
        "risk_score": metric_breakdown.get("risk_score"),
        "risk_level": metric_breakdown.get("risk_level"),
        "decision_grade": metric_breakdown.get("decision_grade"),
    }


def analyze_match(match_id: str | int) -> dict[str, Any] | None:
    fixture = _FIXTURE_INDEX.get(str(match_id))
    if not fixture:
        return None
    return _analysis_from_fixture(fixture)


def _analysis_from_prediction_payload(
    prediction: dict[str, Any],
    *,
    match_id: str = "",
    matchup: str = "",
) -> dict[str, Any] | None:
    if not isinstance(prediction, dict) or not prediction:
        return None
    probs = prediction.get("win_probabilities") or {}
    best_pick = prediction.get("best_pick") or {}
    data_block = prediction.get("data_completeness") if isinstance(prediction.get("data_completeness"), dict) else {}
    decision = _DECISION_ENGINE.build_decision(
        {
            "home_name": (matchup.split(" vs ")[0] if " vs " in matchup else "Home"),
            "away_name": (matchup.split(" vs ")[1] if " vs " in matchup else "Away"),
            "probabilities": {
                "home": probs.get("a") if probs.get("a") is not None else prediction.get("prob_a"),
                "draw": probs.get("draw") if probs.get("draw") is not None else prediction.get("prob_draw"),
                "away": probs.get("b") if probs.get("b") is not None else prediction.get("prob_b"),
            },
            "confidence": prediction.get("confidence_pct") or 0,
            "recommended_side": best_pick.get("prediction") or best_pick.get("team"),
            "data_completeness": data_block,
            "odds": prediction.get("odds"),
        }
    )
    return {
        "match_id": match_id,
        "matchup": matchup,
        "confidence": decision.get("confidence") or 0,
        "probabilities": {
            "a": (decision.get("probabilities") or {}).get("home"),
            "draw": (decision.get("probabilities") or {}).get("draw"),
            "b": (decision.get("probabilities") or {}).get("away"),
        },
        "action": decision.get("action") or "CONSIDER",
        "recommended_side": decision.get("side") or best_pick.get("prediction") or best_pick.get("team"),
        "reason": " | ".join((decision.get("reasoning") or {}).get("strengths", [])) or best_pick.get("reasoning") or prediction.get("decision_summary"),
        "data_quality": _data_quality_label(decision.get("data_quality")),
        "metric_breakdown": {
            "model_probability": decision.get("model_probability"),
            "implied_probability": decision.get("implied_probability"),
            "edge_score": decision.get("edge_score"),
            "expected_value": decision.get("expected_value"),
            "risk_score": decision.get("risk_score"),
            "risk_level": decision.get("risk_level"),
            "decision_grade": decision.get("decision_grade"),
        },
        "model_probability": decision.get("model_probability"),
        "implied_probability": decision.get("implied_probability"),
        "edge_score": decision.get("edge_score"),
        "expected_value": decision.get("expected_value"),
        "risk_score": decision.get("risk_score"),
        "risk_level": decision.get("risk_level"),
        "decision_grade": decision.get("decision_grade"),
    }


def cached_analyze_match(match_id: str | int):
    key = str(match_id)
    def guard(result):
        fixture = _FIXTURE_INDEX.get(key)
        if fixture:
            info = fixture.get("fixture") or {}
            result = dict(result, kickoff=info.get("date"), game_date=info.get("date"), event_status=info.get("status"),
                          data_freshness=worst([result.get("data_freshness"), fixture.get("data_freshness")]))
        return sanitize_decision(result)
    redis_key = cache_service.make_key("match-analysis", key)
    redis_cached = cache_service.get_json(redis_key)
    if redis_cached is not None and not prediction_service.analysis_needs_refresh(redis_cached, _FIXTURE_INDEX.get(key)):
        _logger.debug("match_analysis_cache hit(redis) match_id=%s", key)
        return guard(redis_cached)
    if key in _local_match_analysis_cache and not prediction_service.analysis_needs_refresh(_local_match_analysis_cache[key], _FIXTURE_INDEX.get(key)):
        _logger.debug("match_analysis_cache hit match_id=%s", key)
        return guard(_local_match_analysis_cache[key])
    _logger.debug("match_analysis_cache miss match_id=%s", key)
    result = analyze_match(key)
    if result:
        _local_match_analysis_cache[key] = result
        cache_service.set_json(redis_key, result, ttl=300)
    return guard(result) if result else result


def _prediction_confidence_rank(item: dict) -> tuple[int, float]:
    pred = item.get("prediction", {})
    best_pick = pred.get("best_pick", {})
    conf = best_pick.get("confidence", "Low")
    conf_map = {"High": 0, "Medium": 1, "Low": 2}
    prob_gap = abs(
        pred.get("win_probabilities", {}).get("a", 0.5)
        - pred.get("win_probabilities", {}).get("b", 0.5)
    )
    return (conf_map.get(conf, 3), -prob_gap)


def _soccer_decision_card_from_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    fixture_id = (fixture.get("fixture") or {}).get("id")
    if fixture_id is None:
        return None
    _FIXTURE_INDEX[str(fixture_id)] = fixture
    analysis = prediction_service.get_match_analysis(str(fixture_id))
    if not analysis:
        return None
    return _soccer_card_from_fixture_analysis(fixture, analysis)


_TBD_NAMES = {"", "home", "away", "tbd", "unknown"}


def _soccer_card_from_fixture_analysis(fixture: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any] | None:
    fixture_id = (fixture.get("fixture") or {}).get("id")
    if fixture_id is None:
        return None
    teams_block = fixture.get("teams") or {}
    home = teams_block.get("home") or {}
    away = teams_block.get("away") or {}
    league_block = fixture.get("league") or {}
    fixture_block = fixture.get("fixture") or {}
    team_a = home.get("name") or ""
    team_b = away.get("name") or ""
    # Drop fixtures with missing or placeholder team names
    if team_a.lower().strip() in _TBD_NAMES or team_b.lower().strip() in _TBD_NAMES:
        return None
    card = dui.build_decision_card(analysis=analysis)
    if card is not None:
        probs = card.get("probabilities") or {}
        dq_text = str(card.get("data_quality") or "Partial Data")
        dq_state = "strong" if "strong" in dq_text.lower() else ("limited" if "limited" in dq_text.lower() else "partial")
        card["match_id"] = str(fixture_id)
        card["team_a"] = team_a
        card["team_b"] = team_b
        card["team_a_id"] = home.get("id")
        card["team_b_id"] = away.get("id")
        card["team_a_logo"] = home.get("logo") or ""
        card["team_b_logo"] = away.get("logo") or ""
        card["team_a_initials"] = dui.initials(team_a)
        card["team_b_initials"] = dui.initials(team_b)
        card["league_logo"] = league_block.get("logo") or ""
        card["game_date"] = fixture_block.get("date") or ""
        card["event_status"] = fixture_block.get("status")
        card["data_freshness"] = worst([fixture.get("data_freshness"), analysis.get("data_freshness")])
        card["matchup"] = f"{team_a} vs {team_b}"
        card["competition"] = league_block.get("name") or "Soccer"
        card["action_label"] = card.get("action")
        card["action_class"] = str(card.get("action") or "").lower()
        card["confidence_pct"] = int(card.get("confidence") or 0)
        card["probability_rows"] = [
            {"label": team_a, "value": dui.normalize_percent(probs.get("a"), 0), "selected": card.get("recommended_side") == team_a},
            {"label": "Draw", "value": dui.normalize_percent(probs.get("draw"), 0), "selected": False},
            {"label": team_b, "value": dui.normalize_percent(probs.get("b"), 0), "selected": card.get("recommended_side") == team_b},
        ]
        card["data_confidence"] = {"state": dq_state, "label": dq_text}
        card["cta_url"] = f"/prediction?match_id={fixture_id}"
        card["cta_method"] = "get"
        card["cta_payload"] = {"match_id": fixture_id}
        # Derive recommended_side_key unambiguously while both team names are in scope
        rec = (card.get("recommended_side") or "").lower().strip()
        ta = team_a.lower().strip()
        tb = team_b.lower().strip()
        if ta and (rec in ta or ta in rec):
            card["recommended_side_key"] = "a"
        elif tb and (rec in tb or tb in rec):
            card["recommended_side_key"] = "b"
        else:
            card["recommended_side_key"] = "a"  # default to home team
    return sanitize_decision(card) if card else None


def _soccer_cards_from_fixtures(fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards = []
    for fixture in fixtures or []:
        try:
            card = _soccer_decision_card_from_fixture(fixture)
            if card:
                cards.append(card)
        except Exception as exc:
            app.logger.debug("Decision card build failed for soccer fixture: %s", exc)
    return cards


def _fixture_by_id(match_id: str) -> dict[str, Any] | None:
    return _FIXTURE_INDEX.get(str(match_id))


_MATCH_BRAIN = MatchBrain(
    load_fixtures=load_fixtures_cached,
    get_fixture_by_id=_fixture_by_id,
    decision_engine=_DECISION_ENGINE,
    tracker_save=mt.save_prediction,
    tracker_recent=mt.get_recent_predictions,
    refresh_results=ru.update_pending_predictions,
)


prediction_service.configure(
    get_fixture=_fixture_by_id,
    analyze_match=analyze_match,
    load_fixtures=load_fixtures_cached,
    load_fixtures_for_date=lambda league_id, day: load_fixtures_cached(league_id, selected_date=day),
    card_from_fixture=_soccer_card_from_fixture_analysis,
    top_opportunities=lambda cards, limit: dui.top_opportunities(cards, limit=limit),
    plan_summary=dui.plan_summary,
    restore_fixtures=_restore_fixture_index,
)

# Start background pipeline scheduler: proactive fixture refresh + ML + health monitoring
_pipeline_alerter = EmailAlerter()
_pipeline_monitor = PipelineMonitor(alerter=_pipeline_alerter)
_pipeline_scheduler = None


def _warm_slate_cache() -> None:
    """Pre-warm fixture cards for all supported leagues in parallel at startup."""
    def _run() -> None:
        try:
            with app.app_context():
                from league_config import SUPPORTED_LEAGUE_IDS
                from concurrent.futures import ThreadPoolExecutor
                def _warm_one(lid: int) -> None:
                    try:
                        prediction_service.get_fixture_cards(lid)
                    except Exception:
                        pass
                with ThreadPoolExecutor(max_workers=len(SUPPORTED_LEAGUE_IDS)) as pool:
                    list(pool.map(_warm_one, SUPPORTED_LEAGUE_IDS))
                app.logger.info("startup_cache_warm completed leagues=%s", SUPPORTED_LEAGUE_IDS)
        except Exception as exc:
            app.logger.debug("startup_cache_warm failed: %s", exc, exc_info=True)

    threading.Thread(target=_run, daemon=True).start()


def start_background_services():
    """Called after the worker starts, never while preloading/importing."""
    global _pipeline_scheduler
    if app.config.get("TESTING") or os.getenv("SCORPRED_SCHEDULER_ENABLED", "1") != "1":
        return
    if _pipeline_scheduler is None:
        _pipeline_scheduler = _sched_module.start(
            app, prediction_service, ac, nc, _pipeline_monitor, match_brain=_MATCH_BRAIN,
            track_predictions=_run_prediction_tracking, build_best_props=build_best_props,
        )
        _warm_slate_cache()
        from services import nba_props_ml
        threading.Thread(target=nba_props_ml.warm, daemon=True).start()


def stop_background_services():
    global _pipeline_scheduler
    _sched_module.stop()
    _pipeline_scheduler = None


def _load_grouped_upcoming_fixtures_all_leagues(
    next_n_per_league: int = 8,
    *,
    max_deep_predictions: int = 4,
    include_injuries: bool = False,
    include_standings: bool = False,
):
    grouped_fixtures: list[dict] = []
    all_fixtures: list[dict] = []
    load_errors: list[str] = []
    data_sources: set[str] = set()

    # Fetch all leagues concurrently to avoid sequential 15s-per-league stalls
    with ThreadPoolExecutor(max_workers=min(6, len(SUPPORTED_LEAGUE_IDS))) as executor:
        future_to_league = {
            executor.submit(
                _load_upcoming_fixtures,
                next_n=next_n_per_league,
                max_deep_predictions=max_deep_predictions,
                league=lid,
                include_injuries=include_injuries,
                include_standings=include_standings,
            ): lid
            for lid in SUPPORTED_LEAGUE_IDS
        }
        league_results: dict[int, tuple] = {}
        for future in future_to_league:
            lid = future_to_league[future]
            try:
                league_results[lid] = future.result()
            except Exception as exc:
                _logger.warning("Fixture fetch failed for league %s: %s", lid, exc)
                league_results[lid] = ([], str(exc), _football_data_source(), None)

    for league_id in SUPPORTED_LEAGUE_IDS:
        fixtures, load_error, data_source, _ = league_results.get(league_id, ([], None, _football_data_source(), None))
        data_sources.add(data_source)
        if load_error:
            league_name = (LEAGUE_BY_ID.get(league_id) or {}).get("name", f"League {league_id}")
            load_errors.append(f"{league_name}: {load_error}")

        fixtures = sorted(fixtures or [], key=_prediction_confidence_rank)
        league_info = LEAGUE_BY_ID.get(league_id, {})
        grouped_fixtures.append(
            {
                "league_id": league_id,
                "league_name": league_info.get("name", f"League {league_id}"),
                "league_flag": league_info.get("flag", ""),
                "fixtures": fixtures,
            }
        )
        all_fixtures.extend(fixtures)

    all_fixtures.sort(key=_prediction_confidence_rank)
    data_source = "Multiple providers" if len(data_sources) > 1 else (next(iter(data_sources)) if data_sources else _football_data_source())
    return all_fixtures, grouped_fixtures, " | ".join(load_errors), data_source


def _require_teams():

    return bootstrap_services.require_teams(session)


def _run_parallel(tasks: dict[str, tuple[Callable[[], Any], Any, str]]) -> dict[str, Any]:
    """Run independent I/O tasks concurrently and return key->result with safe fallbacks."""
    if not tasks:
        return {}

    max_workers = min(8, len(tasks))
    results: dict[str, Any] = {key: default for key, (_, default, _) in tasks.items()}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(func): (key, default, error_label)
            for key, (func, default, error_label) in tasks.items()
        }
        for future, (key, default, error_label) in futures.items():
            try:
                results[key] = future.result()
            except Exception as exc:
                results[key] = default
                if error_label:
                    app.logger.error("%s: %s", error_label, exc)

    return results


def _safe_external_call(
    func: Callable[[], Any],
    *,
    retries: int = 3,
    base_delay: float = 0.25,
    label: str = "external-call",
    circuit_threshold: int = 5,
    circuit_cooldown_seconds: int = 30,
) -> Any:
    circuit_state = _API_CIRCUIT.get(label) or {"failures": 0, "open_until": 0.0}
    now_ts = time.time()
    if circuit_state.get("open_until", 0.0) > now_ts:
        _logger.warning("circuit_open label=%s open_until=%s", label, circuit_state["open_until"])
        return None

    retry_markers = ("429", "500", "502", "503", "504", "timeout", "temporarily")
    for attempt in range(retries):
        try:
            result = func()
            _API_CIRCUIT[label] = {"failures": 0, "open_until": 0.0}
            return result
        except Exception as exc:
            message = str(exc).lower()
            should_retry = any(marker in message for marker in retry_markers)
            if attempt >= retries - 1 or not should_retry:
                _logger.warning("%s failed (attempt %s/%s): %s", label, attempt + 1, retries, exc)
                failures = int(circuit_state.get("failures", 0)) + 1
                open_until = now_ts + circuit_cooldown_seconds if failures >= circuit_threshold else 0.0
                _API_CIRCUIT[label] = {"failures": failures, "open_until": open_until}
                return None
            delay = base_delay * (2 ** attempt)
            _logger.warning("%s retrying in %.2fs due to: %s", label, delay, exc)
            time.sleep(delay)
    return None


def _run_parallel_with_status(
    tasks: dict[str, tuple[Callable[[], Any], Any, str]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Run independent I/O tasks concurrently and preserve per-task error status."""
    if not tasks:
        return {}, {}

    max_workers = min(8, len(tasks))
    results: dict[str, Any] = {key: default for key, (_, default, _) in tasks.items()}
    statuses: dict[str, dict[str, Any]] = {
        key: {"ok": True, "error": False, "message": None}
        for key in tasks
    }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(func): (key, default, error_label)
            for key, (func, default, error_label) in tasks.items()
        }
        for future, (key, default, error_label) in futures.items():
            try:
                results[key] = future.result()
            except Exception as exc:
                results[key] = default
                statuses[key] = {"ok": False, "error": True, "message": str(exc)}
                if error_label:
                    app.logger.error("%s: %s", error_label, exc)

    return results, statuses


def _build_prediction_data_completeness(
    *,
    form_a: list[dict[str, Any]],
    form_b: list[dict[str, Any]],
    h2h: list[dict[str, Any]],
    injuries_a: list[dict[str, Any]],
    injuries_b: list[dict[str, Any]],
    standings_for_opp: list[dict[str, Any]],
    task_status: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize which live inputs were available for the current prediction."""
    task_status = task_status or {}
    section_scores = {
        "available": 1.0,
        "limited": 0.55,
        "unavailable": 0.0,
    }
    sections: list[dict[str, Any]] = []

    def _task_failed(key: str) -> bool:
        return bool((task_status.get(key) or {}).get("error"))

    def _push(key: str, label: str, status: str, detail: str, *, weight: float = 1.0) -> None:
        sections.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "detail": detail,
                "weight": weight,
                "score": section_scores.get(status, 0.0),
            }
        )

    form_count = int(bool(form_a)) + int(bool(form_b))
    if _task_failed("fixtures_a") or _task_failed("fixtures_b"):
        _push(
            "recent_form",
            "Recent form",
            "unavailable",
            "Recent fixture history could not be loaded for one or both teams.",
            weight=2.0,
        )
    elif form_count == 2:
        _push(
            "recent_form",
            "Recent form",
            "available",
            f"Both teams have usable recent-form samples ({len(form_a)} vs {len(form_b)} matches).",
            weight=2.0,
        )
    elif form_count == 1:
        _push(
            "recent_form",
            "Recent form",
            "limited",
            "Only one side has a complete recent-form sample, so the form edge is less reliable.",
            weight=2.0,
        )
    else:
        _push(
            "recent_form",
            "Recent form",
            "limited",
            "Recent form is thin for both teams, so the model leaned harder on neutral fallbacks.",
            weight=2.0,
        )

    if _task_failed("h2h"):
        _push(
            "head_to_head",
            "Head-to-head",
            "unavailable",
            "Head-to-head history could not be loaded for this matchup.",
        )
    elif h2h:
        _push(
            "head_to_head",
            "Head-to-head",
            "available",
            f"{min(len(h2h), 20)} recent head-to-head match(es) were available.",
        )
    else:
        _push(
            "head_to_head",
            "Head-to-head",
            "limited",
            "No recent head-to-head sample was available, so that part of the read stayed neutral.",
        )

    if _task_failed("injuries_a_raw") or _task_failed("injuries_b_raw"):
        _push(
            "injuries",
            "Injuries",
            "unavailable",
            "The injury feed was unavailable for one or both teams during this prediction.",
        )
    elif injuries_a or injuries_b:
        _push(
            "injuries",
            "Injuries",
            "available",
            f"{len(injuries_a) + len(injuries_b)} notable injury item(s) were available to the engine.",
        )
    else:
        _push(
            "injuries",
            "Injuries",
            "available",
            "The injury feed responded and no notable absences were flagged.",
        )

    if _task_failed("standings_for_opp"):
        _push(
            "standings",
            "Standings",
            "unavailable",
            "League standings were unavailable, so opponent-strength adjustments were reduced.",
        )
    elif standings_for_opp:
        _push(
            "standings",
            "Standings",
            "available",
            f"League-table context loaded for {len(standings_for_opp)} club rows.",
        )
    else:
        _push(
            "standings",
            "Standings",
            "limited",
            "Standings context was empty, so opponent-strength adjustments stayed lighter than normal.",
        )

    total_weight = sum(section["weight"] for section in sections) or 1.0
    earned_weight = sum(section["weight"] * section["score"] for section in sections)
    coverage_pct = round((earned_weight / total_weight) * 100.0)
    unavailable_count = sum(1 for section in sections if section["status"] == "unavailable")
    limited_count = sum(1 for section in sections if section["status"] == "limited")

    if coverage_pct >= 85 and unavailable_count == 0:
        tier = "full"
        label = "Full live context"
        tone = "strong"
        summary = "All core live inputs loaded cleanly, so the prediction is working with the full pre-match context."
    elif coverage_pct >= 60:
        tier = "partial"
        label = "Partial live context"
        tone = "caution"
        summary = "Most live inputs loaded, but one or more context feeds were thin or missing, so treat the pick with a little more caution."
    else:
        tier = "limited"
        label = "Limited live context"
        tone = "danger"
        summary = "Several live inputs were missing, so the engine had to lean more heavily on fallback assumptions than usual."

    return {
        "tier": tier,
        "label": label,
        "tone": tone,
        "coverage_pct": coverage_pct,
        "summary": summary,
        "available_count": sum(1 for section in sections if section["status"] == "available"),
        "limited_count": limited_count,
        "unavailable_count": unavailable_count,
        "missing_labels": [section["label"] for section in sections if section["status"] != "available"],
        "sections": sections,
    }


def _fallback_chat_reply(message: str) -> str:
    team_a, team_b = _require_teams()
    return assistant_services.fallback_chat_reply(message, team_a=team_a, team_b=team_b)


def _prediction_top_probability(pred: dict[str, Any]) -> float:
    return max(
        float(pred.get("prob_a", 0.0) or 0.0),
        float(pred.get("prob_b", 0.0) or 0.0),
        float(pred.get("prob_draw", 0.0) or 0.0),
    )


_SOCCER_LOGO_LOOKUPS: dict[int, dict[str, str]] = {}
_NBA_LOGO_LOOKUP: dict[str, str] | None = None
_LIVE_RESULT_ROWS_CACHE: dict[str, Any] = {"expires_at": None, "rows": []}
_HOME_DASHBOARD_CACHE: dict[str, Any] = {"expires_at": None, "context": None}
_HOME_DASHBOARD_TTL_SECONDS = int(os.environ.get("SCORPRED_HOME_CACHE_SECONDS", "90") or 90)
_NBA_ESPN_ABBR_BY_KEY = {
    "atlanta hawks": "atl",
    "hawks": "atl",
    "boston celtics": "bos",
    "celtics": "bos",
    "brooklyn nets": "bkn",
    "nets": "bkn",
    "charlotte hornets": "cha",
    "hornets": "cha",
    "chicago bulls": "chi",
    "bulls": "chi",
    "cleveland cavaliers": "cle",
    "cavaliers": "cle",
    "dallas mavericks": "dal",
    "mavericks": "dal",
    "denver nuggets": "den",
    "nuggets": "den",
    "detroit pistons": "det",
    "pistons": "det",
    "golden state warriors": "gsw",
    "warriors": "gsw",
    "houston rockets": "hou",
    "rockets": "hou",
    "indiana pacers": "ind",
    "pacers": "ind",
    "los angeles clippers": "lac",
    "la clippers": "lac",
    "clippers": "lac",
    "los angeles lakers": "lal",
    "la lakers": "lal",
    "lakers": "lal",
    "memphis grizzlies": "mem",
    "grizzlies": "mem",
    "miami heat": "mia",
    "heat": "mia",
    "milwaukee bucks": "mil",
    "bucks": "mil",
    "minnesota timberwolves": "min",
    "timberwolves": "min",
    "new orleans pelicans": "nop",
    "pelicans": "nop",
    "new york knicks": "nyk",
    "knicks": "nyk",
    "oklahoma city thunder": "okc",
    "thunder": "okc",
    "orlando magic": "orl",
    "magic": "orl",
    "philadelphia 76ers": "phi",
    "philadelphia sixers": "phi",
    "76ers": "phi",
    "sixers": "phi",
    "phoenix suns": "phx",
    "suns": "phx",
    "portland trail blazers": "por",
    "trail blazers": "por",
    "sacramento kings": "sac",
    "kings": "sac",
    "san antonio spurs": "sa",
    "spurs": "sa",
    "toronto raptors": "tor",
    "raptors": "tor",
    "utah jazz": "uta",
    "jazz": "uta",
    "washington wizards": "wsh",
    "wizards": "wsh",
}


def _logo_lookup_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", " ", text).strip()
    return text


def _logo_from_team_payload(team: dict[str, Any]) -> str:
    if not isinstance(team, dict):
        return ""
    if team.get("logo"):
        return str(team.get("logo") or "").strip()
    if team.get("logoDark"):
        return str(team.get("logoDark") or "").strip()
    logos = team.get("logos") if isinstance(team.get("logos"), list) else []
    for item in logos:
        if isinstance(item, dict) and item.get("href"):
            return str(item.get("href") or "").strip()
    return ""


def _espn_soccer_logo(team_id: Any) -> str:
    text = str(team_id or "").strip()
    return f"https://a.espncdn.com/i/teamlogos/soccer/500/{text}.png" if text else ""


def _espn_nba_logo_from_name(name: Any) -> str:
    key = _logo_lookup_key(name)
    abbr = _NBA_ESPN_ABBR_BY_KEY.get(key)
    return f"https://a.espncdn.com/i/teamlogos/nba/500/scoreboard/{abbr}.png" if abbr else ""


def _espn_nba_logo_from_team(team: dict[str, Any]) -> str:
    if not isinstance(team, dict):
        return ""
    logo = _logo_from_team_payload(team)
    if logo:
        return logo
    for candidate in (
        team.get("name"),
        team.get("displayName"),
        team.get("nickname"),
        " ".join(part for part in [team.get("city"), team.get("nickname")] if part),
        team.get("abbrev"),
    ):
        logo = _espn_nba_logo_from_name(candidate)
        if logo:
            return logo
    return ""


def _soccer_logo_lookup(league_id: int | None) -> dict[str, str]:
    selected_id = _coerce_league_id(league_id, DEFAULT_LEAGUE_ID)
    if selected_id in _SOCCER_LOGO_LOOKUPS:
        return _SOCCER_LOGO_LOOKUPS[selected_id]

    lookup: dict[str, str] = {}
    try:
        teams = ac.get_teams(selected_id, SEASON) or []
    except Exception:
        _logger.warning("Failed to fetch teams for soccer logo lookup league_id=%s", selected_id, exc_info=True)
        teams = []

    for entry in teams:
        team = entry.get("team") if isinstance(entry, dict) else {}
        if not isinstance(team, dict):
            team = entry if isinstance(entry, dict) else {}
        logo = _logo_from_team_payload(team)
        if not logo:
            continue
        candidates = [
            team.get("id"),
            team.get("name"),
            team.get("displayName"),
            team.get("shortDisplayName"),
            team.get("code"),
        ]
        for candidate in candidates:
            key = _logo_lookup_key(candidate)
            if key:
                lookup[key] = logo
    _SOCCER_LOGO_LOOKUPS[selected_id] = lookup
    return lookup


def _nba_logo_lookup() -> dict[str, str]:
    global _NBA_LOGO_LOOKUP
    if _NBA_LOGO_LOOKUP is not None:
        return _NBA_LOGO_LOOKUP

    lookup: dict[str, str] = {}
    try:
        teams = nc.get_teams() if nc else []
    except Exception:
        _logger.warning("Failed to fetch NBA teams for logo lookup", exc_info=True)
        teams = []
    for team in teams or []:
        if not isinstance(team, dict):
            continue
        logo = _logo_from_team_payload(team)
        if not logo:
            continue
        candidates = [
            team.get("id"),
            team.get("name"),
            team.get("nickname"),
            team.get("shortName"),
            team.get("abbrev"),
            " ".join(part for part in [team.get("city"), team.get("nickname")] if part),
            " ".join(part for part in [team.get("city"), team.get("name")] if part),
        ]
        for candidate in candidates:
            key = _logo_lookup_key(candidate)
            if key:
                lookup[key] = logo
    _NBA_LOGO_LOOKUP = lookup
    return lookup


def _resolve_record_logos(record: dict[str, Any], *, sport: str, team_a: str, team_b: str) -> tuple[str, str, str]:
    explicit_a = str(record.get("team_a_logo") or record.get("home_logo") or "").strip()
    explicit_b = str(record.get("team_b_logo") or record.get("away_logo") or "").strip()
    league_logo = str(record.get("league_logo") or record.get("competition_logo") or "").strip()
    if explicit_a and explicit_b:
        return explicit_a, explicit_b, league_logo

    if sport == "nba":
        lookup = _nba_logo_lookup()
    else:
        lookup = _soccer_logo_lookup(record.get("league_id") or _active_league_id())

    def _find_logo(name: str, team_id: Any, explicit: str) -> str:
        if explicit:
            return explicit
        for candidate in (team_id, name):
            key = _logo_lookup_key(candidate)
            if key and lookup.get(key):
                return lookup[key]
        if sport == "nba":
            return _espn_nba_logo_from_name(name)
        return _espn_soccer_logo(team_id)

    logo_a = _find_logo(team_a, record.get("team_a_id") or record.get("home_id"), explicit_a)
    logo_b = _find_logo(team_b, record.get("team_b_id") or record.get("away_id"), explicit_b)
    return logo_a, logo_b, league_logo


def _nba_team_from_game(game: dict[str, Any], side: str) -> dict[str, Any]:
    teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
    key = "home" if side == "home" else "visitors"
    team = teams.get(key) if isinstance(teams.get(key), dict) else {}
    return team or {}


def _decision_card_from_nba_game(game: dict[str, Any]) -> dict[str, Any] | None:
    return (game.get("prediction") or {}).get("decision_card")


def _dedupe_decision_cards(cards: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    """Keep one rich card per matchup so home does not repeat stale tracker rows."""
    by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        teams = sorted([_logo_lookup_key(card.get("team_a")), _logo_lookup_key(card.get("team_b"))])
        key = (
            str(card.get("sport") or ""),
            str(card.get("competition") or ""),
            str(card.get("match_date") or "")[:10],
            "|".join(teams),
        )
        if not key[-1].strip("|"):
            key = (
                str(card.get("sport") or ""),
                str(card.get("competition") or ""),
                str(card.get("match_date") or "")[:10],
                str(card.get("matchup") or id(card)),
            )
        existing = by_key.get(key)
        if not existing:
            by_key[key] = card
            continue
        existing_score = (
            int(bool(existing.get("team_a_logo"))) + int(bool(existing.get("team_b_logo"))),
            dui.safe_float(existing.get("confidence_pct"), 0),
        )
        candidate_score = (
            int(bool(card.get("team_a_logo"))) + int(bool(card.get("team_b_logo"))),
            dui.safe_float(card.get("confidence_pct"), 0),
        )
        if candidate_score > existing_score:
            by_key[key] = card
    ordered = dui.sort_cards(list(by_key.values()))
    return ordered[:limit] if limit is not None else ordered


def _home_live_nba_cards(limit: int = 6) -> list[dict[str, Any]]:
    if not nc:
        return []
    try:
        games = nc.get_today_games("page") or nc.get_upcoming_games(limit, 5, "page") or []
    except Exception as exc:
        app.logger.debug("Home NBA live cards unavailable: %s", exc)
        return []
    cards = []
    for game in games:
        if (game.get("status") or {}).get("state") == "post":
            continue
        card = _decision_card_from_nba_game(game)
        if card:
            cards.append(card)
        if len(cards) >= limit:
            break
    return dui.assign_opportunity_ranks(_dedupe_decision_cards(cards, limit=limit))


def _home_live_soccer_cards(limit: int = 8) -> list[dict[str, Any]]:
    try:
        fixtures, _, _, _ = _load_upcoming_fixtures(
            next_n=limit,
            max_deep_predictions=0,
            league=_active_league_id(),
            include_injuries=False,
            include_standings=False,
        )
    except Exception as exc:
        app.logger.debug("Home soccer live cards unavailable: %s", exc)
        return []
    cards = _soccer_cards_from_fixtures(fixtures or [])
    return _dedupe_decision_cards(cards, limit=limit)


def _tracker_result_rows(limit: int = 6) -> list[dict[str, Any]]:
    """Fast local trust trail rows; avoids live result backfill during page render."""
    rows = []
    for item in mt.get_completed_predictions(limit=limit) or []:
        sport = str(item.get("sport") or "soccer").lower()
        team_a = item.get("team_a") or item.get("home_team") or "Team A"
        team_b = item.get("team_b") or item.get("away_team") or "Team B"
        logo_a, logo_b, league_logo = _resolve_record_logos(item, sport=sport, team_a=team_a, team_b=team_b)
        rows.append(
            dui.normalize_result_record(
                {**item, "team_a_logo": logo_a, "team_b_logo": logo_b, "league_logo": league_logo}
            )
        )
    rows.sort(key=lambda item: str(item.get("raw_date") or ""), reverse=True)
    return rows[:limit]


def _recent_nba_result_records(limit: int = 10) -> list[dict[str, Any]]:
    return [row for row in mt.get_completed_predictions(limit=5000) if row.get("sport") == "nba"][:limit]


def _recent_soccer_result_records(limit: int = 50) -> list[dict[str, Any]]:
    return [row for row in mt.get_completed_predictions(limit=5000) if row.get("sport") == "soccer"][:limit]


def _completed_result_rows(limit: int = 200) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    expires_at = _LIVE_RESULT_ROWS_CACHE.get("expires_at")
    cached_rows = _LIVE_RESULT_ROWS_CACHE.get("rows") or []
    if isinstance(expires_at, datetime) and expires_at > now and cached_rows:
        return [dict(row) for row in cached_rows[:limit]]

    raw_completed = mt.get_completed_predictions(limit=limit)
    completed = []
    for item in raw_completed:
        sport = str(item.get("sport") or "soccer").lower()
        team_a = item.get("team_a") or item.get("home_team") or "Team A"
        team_b = item.get("team_b") or item.get("away_team") or "Team B"
        logo_a, logo_b, league_logo = _resolve_record_logos(item, sport=sport, team_a=team_a, team_b=team_b)
        completed.append({**item, "team_a_logo": logo_a, "team_b_logo": logo_b, "league_logo": league_logo})

    sports_present = {str(item.get("sport") or "soccer").lower() for item in completed}
    live_records: list[dict[str, Any]] = []
    if len(completed) < 20 or "nba" not in sports_present:
        live_records.extend(_recent_nba_result_records(limit=10))
    if len(completed) < 60 or "soccer" not in sports_present:
        live_records.extend(_recent_soccer_result_records(limit=50))

    rows = [dui.normalize_result_record(item) for item in [*completed, *live_records]]
    rows.sort(key=lambda item: str(item.get("raw_date") or ""), reverse=True)
    seen: set[tuple[str, str, str]] = set()
    deduped = []
    for row in rows:
        key = (str(row.get("sport")), str(row.get("raw_date"))[:10], str(row.get("matchup")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
        if len(deduped) >= limit:
            break
    _LIVE_RESULT_ROWS_CACHE["rows"] = [dict(row) for row in deduped]
    _LIVE_RESULT_ROWS_CACHE["expires_at"] = now + timedelta(minutes=5)
    return deduped


def _build_home_dashboard_context() -> dict[str, Any]:
    """Build the decision-first home payload rendered by the product UI."""
    now = datetime.now(timezone.utc)
    expires_at = _HOME_DASHBOARD_CACHE.get("expires_at")
    cached_context = _HOME_DASHBOARD_CACHE.get("context")
    if isinstance(expires_at, datetime) and expires_at > now and isinstance(cached_context, dict):
        return copy.deepcopy(cached_context)

    metrics = mt.get_summary_metrics()
    pending = mt.get_pending_predictions(limit=40)

    cards: list[dict[str, Any]] = []
    for record in pending:
        sport = str(record.get("sport") or "soccer").lower()
        team_a = record.get("team_a") or record.get("home_team") or "Team A"
        team_b = record.get("team_b") or record.get("away_team") or "Team B"
        prediction_payload = {
            "best_pick": {
                "prediction": record.get("predicted_pick_label") or record.get("predicted_winner"),
                "team": record.get("predicted_winner"),
                "reasoning": record.get("reasoning") or record.get("decision_explainer"),
                "confidence": record.get("confidence"),
            },
            "win_probabilities": {
                "a": record.get("prob_a"),
                "b": record.get("prob_b"),
                "draw": record.get("prob_draw"),
            },
            "confidence_pct": record.get("confidence_pct"),
            "data_completeness": record.get("data_completeness") or {},
        }
        logo_a, logo_b, league_logo = _resolve_record_logos(record, sport=sport, team_a=team_a, team_b=team_b)
        card = dui.build_decision_card(
            sport=sport,
            team_a=team_a,
            team_b=team_b,
            prediction=prediction_payload,
            competition=record.get("league_name") or record.get("competition") or sport.upper(),
            match_date=record.get("game_date") or record.get("date") or record.get("created_at"),
            team_a_logo=logo_a,
            team_b_logo=logo_b,
            league_logo=league_logo,
            cta_label="View Matchup",
        )
        if not card:
            continue
        if sport == "nba" and record.get("team_a_id") and record.get("team_b_id"):
            card.update(
                {
                    "cta_url": "/nba/select-game",
                    "cta_method": "post",
                    "cta_payload": {
                        "team_a": record.get("team_a_id"),
                        "team_b": record.get("team_b_id"),
                        "team_a_name": team_a,
                        "team_b_name": team_b,
                        "team_a_logo": logo_a,
                        "team_b_logo": logo_b,
                        "event_id": record.get("fixture_id") or record.get("game_id") or "",
                        "event_date": record.get("game_date") or record.get("date") or "",
                        "short_name": f"{team_b} @ {team_a}",
                    },
                }
            )
        elif sport == "nba":
            card.update(
                {
                    "cta_url": "/nba/select-game",
                    "cta_method": "post",
                    "cta_payload": {
                        "team_a": record.get("team_a_id") or dui.initials(team_a).lower(),
                        "team_b": record.get("team_b_id") or dui.initials(team_b).lower(),
                        "team_a_name": team_a,
                        "team_b_name": team_b,
                        "team_a_logo": logo_a,
                        "team_b_logo": logo_b,
                        "event_id": record.get("fixture_id") or record.get("game_id") or "",
                        "event_date": record.get("game_date") or record.get("date") or "",
                        "short_name": f"{team_b} @ {team_a}",
                    },
                }
            )
        elif record.get("team_a_id") and record.get("team_b_id"):
            card.update(
                {
                    "cta_url": "/select",
                    "cta_method": "post",
                    "cta_payload": {
                        "team_a": record.get("team_a_id"),
                        "team_b": record.get("team_b_id"),
                        "team_a_name": team_a,
                        "team_b_name": team_b,
                        "team_a_logo": logo_a,
                        "team_b_logo": logo_b,
                        "fixture_id": record.get("fixture_id") or "",
                        "fixture_date": record.get("game_date") or record.get("date") or "",
                        "league_id": record.get("league_id") or _active_league_id(),
                        "league_name": record.get("league_name") or "",
                    },
                }
            )
        else:
            card["cta_url"] = "/soccer"
        cards.append(card)

    tracker_soccer_cards = _dedupe_decision_cards([card for card in cards if card.get("sport") != "nba"])
    tracker_nba_cards = _dedupe_decision_cards([card for card in cards if card.get("sport") == "nba"])
    live_soccer_cards = _home_live_soccer_cards(limit=8)
    live_nba_cards = _home_live_nba_cards(limit=6)
    soccer_cards = _dedupe_decision_cards([*live_soccer_cards, *tracker_soccer_cards], limit=8)
    nba_cards = _dedupe_decision_cards([*live_nba_cards, *tracker_nba_cards], limit=6)
    cards = _dedupe_decision_cards([*soccer_cards, *nba_cards])
    today_plan = dui.plan_summary(cards)
    soccer_plan = dui.plan_summary(soccer_cards)
    nba_plan = dui.plan_summary(nba_cards)
    accuracy = metrics.get("overall_accuracy")
    top_radar = dui.top_opportunities(cards, limit=6)
    data_mix = Counter(((card.get("data_confidence") or {}).get("label") or "Limited Data") for card in cards)
    trust_cards = [
        {
            "title": "Action board",
            "value": f"{today_plan['bet']} BET",
            "badge": "Today",
            "sample_label": "Side, action, confidence, and data trust in one scan.",
            "summary": "ScorPred keeps matchups analyzable while highlighting the strongest plays.",
            "tone": "strong",
        },
        {
            "title": "Opportunity radar",
            "value": str(len(top_radar)),
            "badge": "Live reads",
            "sample_label": f"{accuracy:.1f}% tracked win rate" if accuracy is not None else "Fresh slate focus",
            "summary": "Insights group the best reads by sport, trust level, and confidence so the app does not feel empty.",
            "tone": "neutral",
        },
    ]

    context = {
        "system_snapshot": {"tracked_predictions": int(metrics.get("total_predictions") or 0)},
        "trust_cards": trust_cards,
        "all_cards": cards,
        "top_picks": dui.top_opportunities(cards, limit=5),
        "soccer_picks": dui.top_opportunities(soccer_cards, limit=3),
        "nba_picks": dui.top_opportunities(nba_cards, limit=3),
        "insight_cards": top_radar,
        "data_mix": dict(data_mix),
        "today_plan": today_plan,
        "soccer_plan": soccer_plan,
        "nba_plan": nba_plan,
    }
    _HOME_DASHBOARD_CACHE["context"] = copy.deepcopy(context)
    _HOME_DASHBOARD_CACHE["expires_at"] = now + timedelta(seconds=_HOME_DASHBOARD_TTL_SECONDS)
    return context


def _card_data_label(card: dict[str, Any]) -> str:
    badge = card.get("data_confidence") or card.get("data_badge") or {}
    return str(badge.get("label") or "Limited Data")


def _card_volatility_score(card: dict[str, Any]) -> int:
    confidence = dui.safe_float(card.get("confidence_pct"), 0)
    data_label = _card_data_label(card)
    action = str(card.get("action") or "").upper()
    score = int(max(0, 100 - confidence))
    if data_label == "Partial Data":
        score += 8
    elif data_label == "Limited Data":
        score += 16
    if action == "CONSIDER":
        score += 5
    elif action == "SKIP":
        score += 14
    for row in card.get("probability_rows") or []:
        if str(row.get("label") or "").lower() == "draw" and dui.safe_float(row.get("value"), 0) >= 26:
            score += 8
            break
    return int(dui.clamp(score, 0, 100))


def _insight_signals_for_card(card: dict[str, Any]) -> list[dict[str, str]]:
    data_label = _card_data_label(card)
    confidence = int(round(dui.safe_float(card.get("confidence_pct"), 0)))
    signals = [
        {"label": "Confidence", "value": f"{confidence}%"},
        {"label": "Data trust", "value": data_label},
    ]
    metrics = card.get("comparison_metrics") or []
    if metrics:
        leader_metric = metrics[0]
        signals.append(
            {
                "label": "Edge signal",
                "value": str(leader_metric.get("label") or "Matchup edge"),
            }
        )
    sport = str(card.get("sport") or "").upper()
    if sport:
        signals.append({"label": "Surface", "value": sport})
    return signals[:4]


def _with_insight_metadata(card: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(card)
    score = _card_volatility_score(enriched)
    enriched["volatility_score"] = score
    enriched["insight_signals"] = _insight_signals_for_card(enriched)
    if score >= 58:
        enriched["volatility_note"] = "Actionable, but late team news or matchup swings matter more here."
    elif score >= 44:
        enriched["volatility_note"] = "Playable read with a few context checks before locking it in."
    else:
        enriched["volatility_note"] = "Cleaner profile with fewer obvious caution flags."
    return enriched


def _store_selected_teams(
    team_a: dict,
    team_b: dict,
    fixture_context: dict | None = None,
    league_id: int | None = None,
) -> None:
    selected_league_id = _coerce_league_id(league_id if league_id is not None else _active_league_id())
    session[LEAGUE_SESSION_KEY] = selected_league_id
    session["team_a_id"] = int(team_a["id"])
    session["team_a_name"] = team_a.get("name", "")
    session["team_a_logo"] = team_a.get("logo", "")
    session["team_b_id"] = int(team_b["id"])
    session["team_b_name"] = team_b.get("name", "")
    session["team_b_logo"] = team_b.get("logo", "")

    if fixture_context:
        fixture_context["league_id"] = fixture_context.get("league_id") or selected_league_id
        if not fixture_context.get("league_name"):
            fixture_context["league_name"] = (LEAGUE_BY_ID.get(selected_league_id) or {}).get("name", "")
        fixture_context["home_name"] = fixture_context["home_name"] or team_a.get("name", "")
        fixture_context["home_logo"] = fixture_context["home_logo"] or team_a.get("logo", "")
        fixture_context["away_name"] = fixture_context["away_name"] or team_b.get("name", "")
        fixture_context["away_logo"] = fixture_context["away_logo"] or team_b.get("logo", "")
        session["selected_fixture"] = fixture_context
    else:
        session.pop("selected_fixture", None)


def _recent_team_fixtures_all_comps(
    team_id: int,
    primary_league_id: int | None = None,
    *,
    season: int = SEASON,
    last: int = 20,
) -> list[dict]:
    selected_league_id = _coerce_league_id(primary_league_id if primary_league_id is not None else _active_league_id())
    ordered_leagues = [selected_league_id, *[lid for lid in SUPPORTED_LEAGUE_IDS if lid != selected_league_id]]
    fixtures_by_key: dict[str, dict] = {}

    for league_id in ordered_leagues:
        try:
            league_fixtures = ac.get_team_fixtures(team_id, league_id, season, last=last)
        except Exception:
            _logger.warning("Failed to fetch team fixtures for team_id=%s league_id=%s", team_id, league_id, exc_info=True)
            continue
        for fixture in league_fixtures or []:
            fixture_block = fixture.get("fixture") or {}
            teams_block = fixture.get("teams") or {}
            key = str(fixture_block.get("id") or "")
            if not key:
                home_id = (teams_block.get("home") or {}).get("id") or ""
                away_id = (teams_block.get("away") or {}).get("id") or ""
                key = f"{fixture_block.get('date') or ''}:{home_id}:{away_id}"
            if key not in fixtures_by_key:
                fixtures_by_key[key] = fixture

    fixtures = list(fixtures_by_key.values())
    fixtures.sort(key=lambda item: str((item.get("fixture") or {}).get("date") or ""), reverse=True)
    fixtures = pred.filter_recent_completed_fixtures(fixtures, current_season=season)
    return fixtures[:last]


def _team_form_payload(team_id: int, league_id: int | None = None) -> dict:
    selected_league_id = _coerce_league_id(league_id if league_id is not None else _active_league_id())
    fixtures = _recent_team_fixtures_all_comps(team_id, selected_league_id, season=SEASON, last=20)
    form = pred.extract_form(fixtures, team_id)[:5]
    return {"form_string": "".join(item.get("result", "") for item in form), "rows": form}


def _assistant_page_kind(page_path: str) -> str:
    if re.match(r"^/prediction-result/[^/]+$", page_path or ""):
        return "result_detail"
    if page_path == "/matchup":
        return "soccer_matchup"
    if page_path == "/prediction":
        return "soccer_prediction"
    if page_path == "/props":
        return "soccer_props"
    if page_path == "/model-performance":
        return "model_performance"
    if page_path in {"/nba", "/nba/"}:
        return "nba_home"
    if page_path == "/nba/matchup":
        return "nba_matchup"
    if page_path == "/nba/prediction":
        return "nba_prediction"
    if page_path == "/nba/player":
        return "nba_player"
    if page_path == "/nba/props":
        return "nba_props"
    if page_path == "/nba/standings":
        return "nba_standings"
    if page_path == "/soccer":
        return "soccer_home"
    if page_path == "/":
        return "home"
    return "generic"


def _assistant_store_page_context(page_kind: str, payload: dict | None = None) -> None:
    compact = {"page_kind": page_kind, "captured_at": _now_stamp()}
    if payload:
        compact.update(payload)
    session["assistant_page_context"] = compact


def _assistant_extract_top_factors(prediction: dict) -> list[str]:
    components_a = prediction.get("components_a") if isinstance(prediction.get("components_a"), dict) else {}
    components_b = prediction.get("components_b") if isinstance(prediction.get("components_b"), dict) else {}
    differences = []

    for key in set(components_a) | set(components_b):
        try:
            a_val = float(components_a.get(key, 0) or 0)
            b_val = float(components_b.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
        diff = abs(a_val - b_val)
        if diff <= 0:
            continue
        differences.append((diff, key.replace("_", " ").title()))

    differences.sort(reverse=True)
    return [label for _, label in differences[:3]]


def _assistant_pick_probability(prediction: dict, team_a_name: str, team_b_name: str) -> float | None:
    win_probs = prediction.get("win_probabilities") if isinstance(prediction.get("win_probabilities"), dict) else {}
    pick = str(((prediction.get("best_pick") or {}).get("prediction") or "")).strip().lower()
    if not pick:
        return None
    if pick == str(team_a_name or "").strip().lower():
        return _safe_float(win_probs.get("a")) if win_probs.get("a") is not None else None
    if pick == str(team_b_name or "").strip().lower():
        return _safe_float(win_probs.get("b")) if win_probs.get("b") is not None else None
    if pick == "draw":
        return _safe_float(win_probs.get("draw")) if win_probs.get("draw") is not None else None
    return None


def _assistant_extract_totals_pick_display(prediction: dict) -> str | None:
    totals_leg = _extract_totals_leg(prediction)
    if not totals_leg:
        return None
    pick = str(totals_leg.get("pick") or "").strip()
    line = totals_leg.get("line")
    if pick and line is not None:
        return f"{pick} {line}"
    market = str(totals_leg.get("market") or "").strip()
    return market or pick or None


def _assistant_compact_prediction_context(
    prediction: dict,
    *,
    sport: str,
    team_a_name: str,
    team_b_name: str,
    league_name: str | None = None,
) -> dict:
    best_pick = prediction.get("best_pick") if isinstance(prediction.get("best_pick"), dict) else {}
    return {
        "sport": sport,
        "team_a": team_a_name,
        "team_b": team_b_name,
        "league_name": league_name or "",
        "winner_pick": best_pick.get("prediction") or "",
        "winner_probability": _assistant_pick_probability(prediction, team_a_name, team_b_name),
        "confidence": best_pick.get("confidence") or prediction.get("confidence") or "",
        "reasoning": str(best_pick.get("reasoning") or "").strip(),
        "totals_pick": _assistant_extract_totals_pick_display(prediction),
        "top_factors": _assistant_extract_top_factors(prediction),
    }


def _assistant_store_soccer_matchup_context(
    *,
    team_a: dict,
    team_b: dict,
    prediction: dict,
    league_name: str,
    form_a: list[dict],
    form_b: list[dict],
    injuries_a: list[dict],
    injuries_b: list[dict],
) -> None:
    payload = _assistant_compact_prediction_context(
        prediction,
        sport="soccer",
        team_a_name=team_a.get("name", "Team A"),
        team_b_name=team_b.get("name", "Team B"),
        league_name=league_name,
    )
    payload.update(
        {
            "form_a": "".join(row.get("result", "") for row in form_a[:5]),
            "form_b": "".join(row.get("result", "") for row in form_b[:5]),
            "injury_count_a": len(injuries_a or []),
            "injury_count_b": len(injuries_b or []),
        }
    )
    _assistant_store_page_context("soccer_matchup", payload)


def _assistant_store_soccer_prediction_context(
    *,
    team_a: dict,
    team_b: dict,
    prediction: dict,
    league_name: str,
) -> None:
    _assistant_store_page_context(
        "soccer_prediction",
        _assistant_compact_prediction_context(
            prediction,
            sport="soccer",
            team_a_name=team_a.get("name", "Team A"),
            team_b_name=team_b.get("name", "Team B"),
            league_name=league_name,
        ),
    )


def _assistant_store_result_context(record: dict, comparison: dict, evidence: dict) -> None:
    _assistant_store_page_context(
        "result_detail",
        {
            "sport": record.get("sport") or "",
            "team_a": record.get("team_a") or "",
            "team_b": record.get("team_b") or "",
            "winner_pick": comparison.get("winner_pick") or "",
            "totals_pick": comparison.get("totals_pick") or "",
            "winner_leg": comparison.get("winner_leg") or "",
            "totals_leg": comparison.get("totals_leg") or "",
            "overall_result": comparison.get("overall_result") or "",
            "final_score": record.get("final_score_display") or "",
            "actual_winner": record.get("actual_winner") or "",
            "confidence": comparison.get("confidence") or "",
            "evidence_summary": evidence.get("summary") or comparison.get("evidence_summary") or "",
            "evidence_layer_label": evidence.get("evidence_layer_label") or "",
        },
    )


def _assistant_store_model_performance_context(metrics: dict, sport_filter: str) -> None:
    _assistant_store_page_context(
        "model_performance",
        {
            "sport": sport_filter or "all",
            "overall_accuracy": metrics.get("overall_accuracy"),
            "wins": metrics.get("wins"),
            "losses": metrics.get("losses"),
            "finalized_predictions": metrics.get("finalized_predictions"),
            "grading_logic": (
                "Completed picks are tracked from settled results, and football detail pages separate winner leg, totals leg, and overall verdict so accuracy reflects the final tracked outcome rather than just one leg."
            ),
        },
    )


def _assistant_selected_football_context() -> dict:
    team_a, team_b = _require_teams()
    league_id = _coerce_league_id(session.get(LEAGUE_SESSION_KEY), DEFAULT_LEAGUE_ID)
    return {
        "sport": "soccer",
        "league_id": league_id,
        "league_name": (LEAGUE_BY_ID.get(league_id) or {}).get("name", ""),
        "team_a": (team_a or {}).get("name", ""),
        "team_b": (team_b or {}).get("name", ""),
        "selected_fixture": (_selected_fixture() or {}).get("short_name", ""),
    }


def _assistant_selected_nba_context() -> dict:
    selected_game = session.get("nba_selected_game") or {}
    return {
        "sport": "nba",
        "team_a": session.get("nba_team_a_name", ""),
        "team_b": session.get("nba_team_b_name", ""),
        "selected_game": selected_game.get("short_name", ""),
    }


def _assistant_page_path_from_payload(payload: dict) -> str:
    candidate = str(payload.get("page_path") or "").strip()
    if candidate:
        return candidate
    if request.referrer:
        try:
            return urlparse(request.referrer).path or "/"
        except Exception:
            _logger.debug("Failed to parse referrer URL: %r", request.referrer, exc_info=True)
            return "/"
    return "/"


def _assistant_page_context_for_kind(page_kind: str, page_path: str) -> dict:
    stored = session.get("assistant_page_context") or {}
    if stored.get("page_kind") == page_kind:
        return stored

    if page_kind == "result_detail":
        match = re.match(r"^/prediction-result/([^/]+)$", page_path or "")
        if match:
            record = mt.get_prediction_by_id(match.group(1))
            if record:
                return {
                    "page_kind": "result_detail",
                    "sport": record.get("sport") or "",
                    "team_a": record.get("team_a") or "",
                    "team_b": record.get("team_b") or "",
                    "winner_pick": _prediction_sentence(record),
                    "totals_pick": record.get("totals_pick_display") or "",
                    "winner_leg": "Hit" if record.get("winner_hit") is True else "Miss" if record.get("winner_hit") is False else "Pending",
                    "totals_leg": "Hit" if record.get("ou_hit") is True else "Miss" if record.get("ou_hit") is False else "Pending",
                    "overall_result": record.get("overall_game_result") or "",
                    "final_score": record.get("final_score_display") or "",
                    "actual_winner": record.get("actual_winner") or "",
                    "confidence": record.get("confidence") or "",
                }
    if page_kind == "model_performance":
        metrics = mt.get_summary_metrics()
        return {
            "page_kind": "model_performance",
            "overall_accuracy": metrics.get("overall_accuracy"),
            "wins": metrics.get("wins"),
            "losses": metrics.get("losses"),
            "finalized_predictions": metrics.get("finalized_predictions"),
            "grading_logic": (
                "Completed picks are tracked from settled results, and football detail pages separate winner leg, totals leg, and overall verdict so accuracy reflects the final tracked outcome rather than just one leg."
            ),
        }
    return {}


def _build_chat_request_context(payload: dict) -> dict:
    page_path = _assistant_page_path_from_payload(payload)
    page_kind = _assistant_page_kind(page_path)
    page_context = _assistant_page_context_for_kind(page_kind, page_path)
    inferred_sport = page_context.get("sport") or (
        "nba" if page_kind.startswith("nba") else "soccer" if page_kind.startswith("soccer") or page_kind == "result_detail" else ""
    )

    return {
        "page": {
            "path": page_path,
            "title": str(payload.get("page_title") or "").strip(),
            "kind": page_kind,
            "sport": inferred_sport,
        },
        "football": _assistant_selected_football_context(),
        "nba": _assistant_selected_nba_context(),
        "assistant_page": page_context,
    }


def _chat_reply(message: str, history: list[dict] | None = None, chat_context: dict | None = None) -> dict:
    ctx = chat_context or {}
    football_ctx = ctx.get("football") or {}
    _ta = football_ctx.get("team_a") or ""
    _tb = football_ctx.get("team_b") or ""
    team_a = {"name": _ta} if _ta else None
    team_b = {"name": _tb} if _tb else None
    page_ctx = ctx.get("assistant_page") or None
    return assistant_services.chat_reply(
        message,
        history=history,
        anthropic_module=anthropic,
        api_key="",  # Sports chat never loads a paid-provider credential.
        team_a=team_a,
        team_b=team_b,
        page_ctx=page_ctx,
        logger=app.logger,
    )
 


# â”€â”€ Routes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.route("/insights")
def insights():
    try:
        _refresh_tracking_results_if_due()
        league_id = _set_active_league(_active_league_id())
        cards: list[dict] = []
        if _MATCH_BRAIN is not None:
            try:
                insights_payload = _MATCH_BRAIN.get_insights(league_id)
            except Exception as exc:
                app.logger.warning("insights get_insights failed: %s", exc)
                insights_payload = {"top_opportunities": []}
            for item in insights_payload.get("top_opportunities", []):
                try:
                    pred_block = item.get("prediction") or {}
                    probs = pred_block.get("probabilities") or {}
                    canonical_mb = item.get("metric_breakdown") or {}
                    dq_raw = item.get("data_quality") or pred_block.get("data_quality")
                    if isinstance(dq_raw, int):
                        dq_label = "Strong Data" if dq_raw >= 75 else ("Limited Data" if dq_raw < 50 else "Partial Data")
                    else:
                        dq_label = str(dq_raw or "Partial Data")
                    analysis = {
                        "match_id": item.get("match_id"),
                        "matchup": item.get("matchup"),
                        "confidence": pred_block.get("confidence") or item.get("confidence"),
                        "probabilities": {"a": probs.get("home"), "draw": probs.get("draw"), "b": probs.get("away")},
                        "action": pred_block.get("action") or item.get("action"),
                        "recommended_side": pred_block.get("side") or item.get("recommended_side"),
                        "reason": " | ".join((pred_block.get("reasoning") or {}).get("strengths", [])) or item.get("reason") or "",
                        "data_quality": dq_label,
                        "metric_breakdown": {
                            "edge_score": canonical_mb.get("edge_score") or pred_block.get("edge_score"),
                            "expected_value": canonical_mb.get("expected_value") or pred_block.get("expected_value"),
                            "risk_level": canonical_mb.get("risk_level") or pred_block.get("risk_level"),
                            "decision_grade": canonical_mb.get("decision_grade") or pred_block.get("decision_grade"),
                            "risk_score": canonical_mb.get("risk_score") or pred_block.get("risk_score"),
                        },
                    }
                    card = dui.build_decision_card(analysis=analysis)
                    if card:
                        cards.append(card)
                except Exception as exc:
                    app.logger.warning("insights card build failed: %s", exc)
        else:
            try:
                cards, *_ = prediction_service.get_fixture_cards(league_id)
            except Exception as exc:
                app.logger.warning("insights fixture_cards fallback failed: %s", exc)
        all_cards = []
        for card in cards:
            try:
                all_cards.append(_with_insight_metadata(card))
            except Exception:
                _logger.warning("Failed to enrich insight metadata for card matchup=%s", card.get("matchup"), exc_info=True)
                all_cards.append(card)
        home_context: dict = {"all_cards": all_cards}
        sport_filter = (request.args.get("sport") or "all").strip().lower()
        if sport_filter not in {"all", "soccer", "nba"}:
            sport_filter = "all"
        filtered_cards = [
            card for card in all_cards
            if sport_filter == "all" or str(card.get("sport") or "").lower() == sport_filter
        ]
        home_context["insight_cards"] = [_with_insight_metadata(card) for card in dui.top_opportunities(filtered_cards, limit=6)]
        action_mix = dui.plan_summary(filtered_cards)
        sport_counts = {
            "all": len(all_cards),
            "soccer": len([card for card in all_cards if str(card.get("sport") or "").lower() != "nba"]),
            "nba": len([card for card in all_cards if str(card.get("sport") or "").lower() == "nba"]),
        }
        confidence_groups = {
            "Top confidence": len([card for card in filtered_cards if dui.safe_float(card.get("confidence_pct"), 0) >= 66]),
            "Playable range": len([card for card in filtered_cards if 55 <= dui.safe_float(card.get("confidence_pct"), 0) < 66]),
            "Caution range": len([card for card in filtered_cards if dui.safe_float(card.get("confidence_pct"), 0) < 55]),
        }
        volatility_watch = sorted(
            [card for card in filtered_cards if str(card.get("action") or "").upper() != "SKIP"],
            key=lambda card: (
                -int(card.get("volatility_score") or 0),
                -dui.safe_float(card.get("confidence_pct"), 0),
            ),
        )[:4]
        context_watch = []
        for card in volatility_watch:
            context_watch.append(
                {
                    "matchup": card.get("matchup") or f"{card.get('team_a')} vs {card.get('team_b')}",
                    "side": card.get("recommended_side") or card.get("pick_side") or "",
                    "action": card.get("action") or "CONSIDER",
                    "confidence_pct": int(round(dui.safe_float(card.get("confidence_pct"), 0))),
                    "data_label": _card_data_label(card),
                    "note": card.get("volatility_note") or "",
                }
            )
        trust_rows: list[dict] = []
        try:
            trust_rows = _tracker_result_rows(limit=12)
            if sport_filter != "all":
                trust_rows = [row for row in trust_rows if str(row.get("sport") or "").lower() == sport_filter]
            trust_rows = trust_rows[:6]
        except Exception as exc:
            app.logger.warning("insights trust_rows failed: %s", exc)
        trust_summary: dict = {}
        try:
            trust_summary = dui.results_summary(trust_rows) if trust_rows else {}
        except Exception as exc:
            app.logger.warning("insights results_summary failed: %s", exc)
        home_context["data_mix"] = dict(Counter(_card_data_label(card) for card in filtered_cards))
        return render_template(
            "insights.html",
            **_page_context(
                **home_context,
                action_mix=action_mix,
                confidence_groups=confidence_groups,
                sport_filter=sport_filter,
                sport_counts=sport_counts,
                volatility_watch=volatility_watch,
                context_watch=context_watch,
                trust_rows=trust_rows,
                trust_summary=trust_summary,
            ),
        )
    except Exception as exc:
        app.logger.error("insights route unhandled error: %s", exc, exc_info=True)
        return render_template(
            "insights.html",
            **_page_context(
                all_cards=[], insight_cards=[], action_mix={"bet": 0, "consider": 0, "skip": 0},
                confidence_groups={"Top confidence": 0, "Playable range": 0, "Caution range": 0},
                sport_filter="all", sport_counts={"all": 0, "soccer": 0, "nba": 0},
                volatility_watch=[], context_watch=[], trust_rows=[], trust_summary={}, data_mix={},
            ),
        )


@app.route("/results")
def results():
    return redirect(url_for("insights"))


@app.route("/api/results/live")
@limiter.limit("60/hour")
def api_results_live():
    rows = _completed_result_rows(limit=200)
    summary = dui.results_summary(rows)
    breakdowns = dui.results_breakdowns(rows)
    return jsonify(
        {
            "summary": summary,
            "recent_soccer": breakdowns.get("recent_soccer", [])[:50],
            "recent_nba": breakdowns.get("recent_nba", [])[:10],
            "results": rows,
            "breakdowns": breakdowns,
        }
    )


@app.route("/", methods=["GET"])
def index():
    _refresh_tracking_results_if_due()
    home_context = _build_home_dashboard_context()
    home_context["now"] = datetime.now()
    return render_template("home.html", **_page_context(**home_context))


@app.route("/soccer", methods=["GET"])
def soccer():
    _logger.debug("Route /soccer hit")
    _set_data_refresh()
    _refresh_tracking_results_if_due()
    league_id = _set_active_league(_active_league_id())
    teams = []
    try:
        teams = ac.get_teams(league_id, SEASON) or []
    except Exception:
        _logger.warning("Failed to fetch teams for /soccer route league_id=%s", league_id, exc_info=True)
    full_slate, fixtures, fixtures_error, fixtures_source, _ = prediction_service.get_fixture_cards(league_id)
    return render_template(
        "soccer.html",
        teams=teams,
        upcoming_fixtures=fixtures or [],
        top_opportunities=prediction_service.get_top_opportunities(league_id),
        full_slate=full_slate,
        today_plan=prediction_service.get_today_plan(league_id),
        fixtures_error=fixtures_error if fixtures_error or not fixtures else None,
        fixtures_source=fixtures_source or _football_data_source(),
        selection_notice=(request.args.get("selection_error") or "").strip() or None,
        selected_fixture=_selected_fixture(),
        **_league_context(league_id),
    )


@app.route("/fixtures", methods=["GET"])
def fixtures():
    """Legacy fixtures page route (kept for backwards compatibility)."""
    _set_data_refresh()
    selected_slug = (request.args.get("espn_slug") or "").strip()
    league_id = _active_league_id()
    if selected_slug and not request.args.get("league"):
        for candidate_id, slug in getattr(ac, "ESPN_SLUG_BY_LEAGUE", {}).items():
            if str(slug).lower() == selected_slug.lower():
                league_id = _coerce_league_id(candidate_id, league_id)
                break
    league_id = _set_active_league(league_id)
    selected_slug = getattr(ac, "ESPN_SLUG_BY_LEAGUE", {}).get(league_id, selected_slug)
    fixtures_data: list[dict] = []
    load_error = None
    data_source = _football_data_source()

    decision_cards, fixtures_data, load_error, data_source, _ = prediction_service.get_fixture_cards(league_id)

    return render_template(
        "fixtures.html",
        **_page_context(
            fixtures=fixtures_data or [],
            full_slate=decision_cards,
            top_opportunities=prediction_service.get_top_opportunities(league_id),
            today_plan=prediction_service.get_today_plan(league_id),
            load_error=load_error,
            data_source=data_source,
            espn_slug=selected_slug,
            **_league_context(league_id),
        ),
    )


@app.route("/select", methods=["GET", "POST"])
@app.route("/select-game", methods=["GET", "POST"])
@app.route("/matchup", methods=["POST"])
def select_game():
    _set_data_refresh()
    league_id = _set_active_league(
        _coerce_league_id(request.form.get("league_id") or request.args.get("league") or _active_league_id())
    )
    a_id_raw = (request.form.get("team_a") or request.args.get("team_a") or "").strip()
    b_id_raw = (request.form.get("team_b") or request.args.get("team_b") or "").strip()
    fixture_context = _fixture_context_from_form()
    source = (fixture_context or {}).get("data_source", "configured")

    if not a_id_raw or not b_id_raw or a_id_raw == b_id_raw:
        return _selection_error_redirect("soccer", "The selected soccer fixture could not be prepared for Match Analysis.")

    try:
        teams = ac.get_teams(league_id, SEASON)
    except Exception as exc:
        app.logger.error("Failed to fetch provider teams during selection: %s", exc)
        return _selection_error_redirect("soccer", "The selected soccer fixture could not be loaded because team data is unavailable.")

    if not teams:
        return _selection_error_redirect("soccer", "The selected soccer fixture could not be loaded because team data is unavailable.")

    team_map = {
        str((entry.get("team") or entry).get("id")): (entry.get("team") or entry)
        for entry in teams
        if (entry.get("team") or entry).get("id")
    }
    team_a = team_map.get(a_id_raw)
    team_b = team_map.get(b_id_raw)

    if (not team_a or not team_b) and source == "espn" and teams:
        team_a = _resolve_provider_team_by_name(request.form.get("team_a_name", ""), teams)
        team_b = _resolve_provider_team_by_name(request.form.get("team_b_name", ""), teams)

    if not team_a or not team_b:
        return _selection_error_redirect(
            "soccer",
            "The selected soccer fixture could not be matched to the current provider, so Match Analysis was not loaded."
        )

    _store_selected_teams(team_a, team_b, fixture_context)
    return redirect("/prediction")


@app.route("/matchup", methods=["GET"])
def matchup():
    _set_data_refresh()
    league_id = _set_active_league(_active_league_id())
    team_a, team_b = _require_teams()
    if not team_a:
        return _selection_error_redirect("soccer", "Match Analysis could not be opened because no soccer fixture is selected.")
    selected_fixture = _selected_fixture()
    blocked = _soccer_unavailable_page(team_a, team_b, selected_fixture, template="matchup.html")
    if blocked is not None:
        return blocked
    league_id = _set_active_league(_active_league_id())

    id_a, id_b = team_a["id"], team_b["id"]
    results = _run_parallel(
        {
            "h2h_raw": (
                lambda: ac.get_h2h(id_a, id_b, last=20),
                [],
                "H2H fetch error",
            ),
            "fixtures_a": (
                lambda: ac.get_team_fixtures(id_a, LEAGUE, SEASON, last=20),
                [],
                "Team A fixtures fetch error",
            ),
            "fixtures_b": (
                lambda: ac.get_team_fixtures(id_b, LEAGUE, SEASON, last=20),
                [],
                "Team B fixtures fetch error",
            ),
            "injuries_a_raw": (
                lambda: ac.get_injuries(LEAGUE, SEASON, id_a),
                [],
                "Team A injuries fetch error",
            ),
            "injuries_b_raw": (
                lambda: ac.get_injuries(LEAGUE, SEASON, id_b),
                [],
                "Team B injuries fetch error",
            ),
            "standings_for_opp": (
                lambda: ac.get_standings(LEAGUE, SEASON),
                [],
                "Standings fetch error",
            ),
            "squad_a": (
                lambda: ac.get_squad(id_a, SEASON),
                [],
                "Matchup squad A fetch error",
            ),
            "squad_b": (
                lambda: ac.get_squad(id_b, SEASON),
                [],
                "Matchup squad B fetch error",
            ),
        }
    )

    h2h_raw = results.get("h2h_raw") or []
    fixtures_a = results.get("fixtures_a") or []

    id_a, id_b = team_a["id"], team_b["id"]
    selected_fixture = _selected_fixture()

    h2h_raw = []
    fixtures_a = []
    fixtures_b = []
    injuries_a_raw = []
    injuries_b_raw = []

    try:
        h2h_raw = ac.get_h2h(id_a, id_b, last=20)
    except Exception as exc:
        app.logger.error("H2H fetch error: %s", exc)

    try:
        fixtures_a = _recent_team_fixtures_all_comps(id_a, league_id, season=SEASON, last=20)
    except Exception as exc:
        app.logger.error("Team A fixtures fetch error: %s", exc)

    try:
        fixtures_b = _recent_team_fixtures_all_comps(id_b, league_id, season=SEASON, last=20)
    except Exception as exc:
        app.logger.error("Team B fixtures fetch error: %s", exc)

    h2h_raw = pred.filter_recent_completed_fixtures(h2h_raw, current_season=SEASON, seasons_back=5)

    try:
        injuries_a_raw = ac.get_injuries(league_id, SEASON, id_a)
    except Exception as exc:
        app.logger.error("Team A injuries fetch error: %s", exc)

    try:
        injuries_b_raw = ac.get_injuries(league_id, SEASON, id_b)
    except Exception as exc:
        app.logger.error("Team B injuries fetch error: %s", exc)

    h2h_raw = pred.filter_recent_completed_fixtures(h2h_raw, current_season=SEASON, seasons_back=5)


    if not h2h_raw and not fixtures_a and not fixtures_b:
        app.logger.warning("Matchup has no historical source data; using fallback neutral values for %s vs %s", team_a["name"], team_b["name"])

    h2h_enriched = []
    for fixture in h2h_raw[:5]:
        try:
            h2h_enriched.append(ac.enrich_fixture(fixture))
        except Exception:
            _logger.warning("Failed to enrich H2H fixture fixture_id=%s", (fixture.get("fixture") or {}).get("id"), exc_info=True)
            h2h_enriched.append({**fixture, "events": [], "stats": []})

    ml_form_a = pred.extract_form(fixtures_a, id_a)[:10]
    ml_form_b = pred.extract_form(fixtures_b, id_b)[:10]
    form_a = ml_form_a[:5]
    form_b = ml_form_b[:5]
    split_a = pred.home_away_split(form_a)
    split_b = pred.home_away_split(form_b)
    h2h_rec = pred.h2h_record(h2h_raw, id_a, id_b)
    injuries_a = _display_injuries(injuries_a_raw)
    injuries_b = _display_injuries(injuries_b_raw)

    def _avg(rows: list[dict], *keys: str) -> float:
        if not rows:
            return 0.0
        total = 0.0
        count = 0
        for row in rows:
            value = None
            for key in keys:
                if row.get(key) is not None:
                    value = row.get(key)
                    break
            if value is None:
                value = 0
            total += float(value or 0)
            count += 1
        return round(total / count, 2) if count else 0.0

    stats_compare = [
        {"label": "Goals scored", "a": _avg(form_a, "goals_for", "gf"), "b": _avg(form_b, "goals_for", "gf")},
        {"label": "Goals conceded", "a": _avg(form_a, "goals_against", "ga"), "b": _avg(form_b, "goals_against", "ga")},
        {"label": "Shots", "a": _avg(form_a, "shots"), "b": _avg(form_b, "shots")},
        {"label": "Shots on target", "a": _avg(form_a, "shots_on_target"), "b": _avg(form_b, "shots_on_target")},
        {"label": "Possession", "a": _avg(form_a, "possession"), "b": _avg(form_b, "possession")},
        {"label": "Corners", "a": _avg(form_a, "corners"), "b": _avg(form_b, "corners")},
    ]

    # â”€â”€ Scorpred Engine â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    # H2H form from each team's perspective for the Scorpred model
    h2h_form_a = pred.extract_form(h2h_raw, id_a)[:10]
    h2h_form_b = pred.extract_form(h2h_raw, id_b)[:10]

    # Restore context variables from results dict
    standings_for_opp = results.get("standings_for_opp") or []
    squad_a = results.get("squad_a") or []
    squad_b = results.get("squad_b") or []

    # Standings â†’ opponent strength lookup for quality-of-schedule adjustment
    opp_strengths = _build_opp_strengths(standings_for_opp)

    # â”€â”€ Fetch live odds (graceful no-op when ODDS_API_KEY is unset) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    blocked = _soccer_unavailable_page(team_a, team_b, selected_fixture, template="matchup.html", inputs=fixtures_a + fixtures_b)
    if blocked is not None:
        return blocked
    odds_result = odds_fetcher.fetch_match_odds("soccer", team_a["name"], team_b["name"], kickoff=selected_fixture.get("date"),
                                               league_id=LEAGUE)
    odds_ctx: dict[str, Any] | None = None
    if odds_result.get("available"):
        odds_ctx = {
            "home": odds_result.get("home_odds"),
            "draw": odds_result.get("draw_odds"),
            "away": odds_result.get("away_odds"),
        }

    mastermind = sm.predict_match(
        {
            "sport": "soccer",
            "team_a_name": team_a["name"],
            "team_b_name": team_b["name"],
            "team_a_is_home": True,
            "form_a": form_a,
            "form_b": form_b,
            "ml_form_a": ml_form_a,
            "ml_form_b": ml_form_b,
            "target_date": selected_fixture.get("date"),
            "h2h_form_a": h2h_form_a,
            "h2h_form_b": h2h_form_b,
            "historical_matches": fixtures_a + fixtures_b + h2h_raw,
            "injuries_a": injuries_a_raw,
            "injuries_b": injuries_b_raw,
            "opp_strengths": opp_strengths,
            "team_stats": {
                "a": split_a,
                "b": split_b,
            },
            **(({"odds": odds_ctx}) if odds_ctx else {}),
        }
    )
    scorpred = mastermind.get("ui_prediction") or {}
    scorpred["data_completeness"] = _build_prediction_data_completeness(
        form_a=form_a,
        form_b=form_b,
        h2h=h2h_raw,
        injuries_a=injuries_a_raw,
        injuries_b=injuries_b_raw,
        standings_for_opp=standings_for_opp,
        task_status={},
    )
    scorpred["decision_explainer"] = _build_prediction_explainer(
        scorpred,
        team_a_name=team_a["name"],
        team_b_name=team_b["name"],
    )
    selected_match_id = (selected_fixture or {}).get("fixture_id")
    analysis = prediction_service.get_match_analysis(str(selected_match_id)) if selected_match_id else None
    if not analysis:
        analysis = _analysis_from_prediction_payload(
            scorpred,
            match_id=str(selected_match_id or ""),
            matchup=f"{team_a['name']} vs {team_b['name']}",
        )
    scorpred["decision_card"] = dui.build_decision_card(analysis=analysis) if analysis else None
    if scorpred["decision_card"]:
        scorpred["play_type"] = scorpred["decision_card"].get("action")
        scorpred["confidence_pct"] = scorpred["decision_card"].get("confidence")

    # â”€â”€ Compute odds edge for template display â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    edge_data: dict[str, Any] = {}
    if odds_result.get("available"):
        probs = scorpred.get("win_probabilities") or {}
        best_pick_team = (scorpred.get("best_pick") or {}).get("team", "A")
        if best_pick_team in (team_a["name"], "A"):
            model_prob = float(probs.get("a", 0) or 0) / 100.0
            market_odds = odds_result.get("home_odds")
        elif best_pick_team in (team_b["name"], "B"):
            model_prob = float(probs.get("b", 0) or 0) / 100.0
            market_odds = odds_result.get("away_odds")
        else:
            model_prob = float(probs.get("draw", 0) or 0) / 100.0
            market_odds = odds_result.get("draw_odds")
        edge_data = odds_fetcher.compute_edge(model_prob, market_odds)

    # â”€â”€ Key threats (danger men) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    threats_a = _build_key_threats(squad_a, injuries_a_raw, fixtures_a, id_a)
    threats_b = _build_key_threats(squad_b, injuries_b_raw, fixtures_b, id_b)

    # Not tracked: this page runs ScorMastermind, while the track record measures the engine the
    # dashboard publishes (scheduled tracking records every published fixture).

    return render_template(
        "matchup.html",
        **_page_context(
            team_a=team_a,
            team_b=team_b,
            selected_fixture=selected_fixture,
            h2h=h2h_enriched,
            h2h_rec=h2h_rec,
            form_a=form_a,
            form_b=form_b,
            split_a=split_a,
            split_b=split_b,
            injuries_a=injuries_a,
            injuries_b=injuries_b,
            stats_compare=stats_compare,
            scorpred=scorpred,
            threats_a=threats_a,
            threats_b=threats_b,
            edge_data=edge_data,
        ),
    )


def _build_key_threats(squad: list, injuries: list, fixtures: list, team_id: int) -> list[dict]:
    return evidence_services.build_key_threats(
        squad,
        injuries,
        fixtures,
        team_id,
        predictor=pred,
        current_season=SEASON,
    )


def _build_soccer_total_pick(fixture: dict[str, Any], opp_strengths: dict[str, Any]) -> dict[str, Any] | None:
    try:
        home_id = fixture["teams"]["home"]["id"]
        away_id = fixture["teams"]["away"]["id"]
        home_name = fixture["teams"]["home"]["name"]
        away_name = fixture["teams"]["away"]["name"]

        h2h_raw = ac.get_h2h(home_id, away_id, last=10)
        fixtures_home = ac.get_team_fixtures(home_id, LEAGUE, SEASON, last=10)
        fixtures_away = ac.get_team_fixtures(away_id, LEAGUE, SEASON, last=10)
        injuries_home = _clean_injuries(ac.get_injuries(LEAGUE, SEASON, home_id))
        injuries_away = _clean_injuries(ac.get_injuries(LEAGUE, SEASON, away_id))

        h2h_raw = pred.filter_recent_completed_fixtures(h2h_raw, current_season=SEASON, seasons_back=5)
        fixtures_home = pred.filter_recent_completed_fixtures(fixtures_home, current_season=SEASON)
        fixtures_away = pred.filter_recent_completed_fixtures(fixtures_away, current_season=SEASON)

        form_home = pred.extract_form(fixtures_home, home_id)[:5]
        form_away = pred.extract_form(fixtures_away, away_id)[:5]
        h2h_form_home = pred.extract_form(h2h_raw, home_id)[:5]
        h2h_form_away = pred.extract_form(h2h_raw, away_id)[:5]

        prediction = se.scorpred_predict(
            form_a=form_home,
            form_b=form_away,
            h2h_form_a=h2h_form_home,
            h2h_form_b=h2h_form_away,
            injuries_a=injuries_home,
            injuries_b=injuries_away,
            team_a_is_home=True,
            team_a_name=home_name,
            team_b_name=away_name,
            sport="soccer",
            opp_strengths=opp_strengths,
        )

        best_pick = prediction.get("best_pick", {})
        confidence = best_pick.get("confidence", "Low")
        team_a_score = prediction.get("team_a_score", 0)
        team_b_score = prediction.get("team_b_score", 0)
        expected_total = team_a_score + team_b_score

        if expected_total > 2.5:
            pick = "Over 2.5 Goals"
            over_probability = min(95, 50 + (expected_total - 2.5) * 20)
        else:
            pick = "Under 2.5 Goals"
            over_probability = max(5, 50 - (2.5 - expected_total) * 20)

        if abs(expected_total - 2.5) >= 1.0:
            confidence = "High"
        elif abs(expected_total - 2.5) >= 0.5:
            confidence = "Medium"
        else:
            confidence = "Low"

        if expected_total > 3.5:
            if team_a_score > team_b_score:
                note = f"{home_name}'s strong home form and {away_name}'s defensive struggles suggest a high-scoring game."
            else:
                note = f"Both teams' recent attacking displays and {away_name}'s away goal concessions point to over 2.5 goals."
        elif expected_total > 3.0:
            note = f"{home_name}'s scoring trend and {away_name}'s recent matches indicate this could go over 2.5."
        elif expected_total < 2.0:
            note = f"{home_name} and {away_name} have combined for low-scoring games in recent fixtures."
        elif expected_total < 2.5:
            if team_a_score < team_b_score:
                note = f"{away_name}'s defensive solidity and {home_name}'s scoring drought suggest under 2.5 goals."
            else:
                note = f"Recent form shows {home_name} and {away_name} involved in low-scoring encounters."
        else:
            if abs(team_a_score - team_b_score) < 0.3:
                note = f"Balanced matchup between {home_name} and {away_name} with moderate goal expectations."
            else:
                note = f"{home_name if team_a_score > team_b_score else away_name}'s edge and recent trends make this total competitive."

        if confidence != "High" and over_probability <= 60:
            return None

        return {
            "fixture": fixture,
            "home_team": fixture["teams"]["home"],
            "away_team": fixture["teams"]["away"],
            "pick": pick,
            "probability": round(over_probability),
            "confidence": confidence,
            "note": note,
            "expected_total": round(expected_total, 1),
        }
    except Exception as exc:
        app.logger.warning("Totals prediction failed for soccer fixture %s: %s", fixture.get("fixture", {}).get("id"), exc)
        return None


def _build_nba_winner_pick(
    game: dict[str, Any],
    team_map: dict[str, dict[str, Any]],
    nba_opp_strengths: dict[str, Any],
    nc: Any,
    np_nba: Any,
) -> dict[str, Any] | None:
    try:
        if not isinstance(game, dict):
            return None

        teams_block = game.get("teams") or {}
        home_raw = teams_block.get("home") or {}
        away_raw = teams_block.get("visitors") or {}

        home_id = str(home_raw.get("id") or "")
        away_id = str(away_raw.get("id") or "")

        if not home_id or not away_id:
            return None

        home_team = team_map.get(home_id) or home_raw
        away_team = team_map.get(away_id) or away_raw

        h2h_raw = nc.get_h2h(home_id, away_id)
        form_home_raw = nc.get_team_recent_form(home_id)
        form_home = np_nba.extract_recent_form(form_home_raw, home_id, n=5)
        form_away_raw = nc.get_team_recent_form(away_id)
        form_away = np_nba.extract_recent_form(form_away_raw, away_id, n=5)
        injuries_home = nc.get_team_injuries(home_id)
        injuries_away = nc.get_team_injuries(away_id)

        h2h_form_home = np_nba.extract_recent_form(h2h_raw, home_id, n=5) if h2h_raw else []
        h2h_form_away = np_nba.extract_recent_form(h2h_raw, away_id, n=5) if h2h_raw else []

        prediction = se.scorpred_predict(
            form_a=form_home,
            form_b=form_away,
            h2h_form_a=h2h_form_home,
            h2h_form_b=h2h_form_away,
            injuries_a=injuries_home,
            injuries_b=injuries_away,
            team_a_is_home=True,
            team_a_name=home_team.get("nickname") or home_team.get("name") or "Home",
            team_b_name=away_team.get("nickname") or away_team.get("name") or "Away",
            sport="nba",
            opp_strengths=nba_opp_strengths,
        )

        best_pick = prediction.get("best_pick", {})
        probs = prediction.get("win_probabilities", {})
        confidence = best_pick.get("confidence", "Low")
        prob_home = probs.get("a", 50)
        prob_away = probs.get("b", 50)

        if confidence != "High" and prob_home <= 60 and prob_away <= 60:
            return None

        predicted_winner = best_pick.get("prediction", "â€”")
        winner_team = (
            home_team.get("name")
            if predicted_winner == "Home"
            else away_team.get("name") if predicted_winner == "Away" else "â€”"
        )

        return {
            "game": game,
            "home_team": home_team,
            "away_team": away_team,
            "predicted_winner": winner_team,
            "win_probability": prob_home if predicted_winner == "Home" else prob_away,
            "confidence": confidence,
            "reasoning": best_pick.get("reasoning", ""),
        }
    except Exception as exc:
        app.logger.warning("Prediction failed for NBA game %s: %s", game.get("id"), exc)
        return None


@app.route("/player")
def player():
    _set_data_refresh()
    team_a, team_b = _require_teams()
    if not team_a:
        return redirect(url_for("index"))
    selected_fixture = _selected_fixture()
    id_a, id_b = team_a["id"], team_b["id"]

    results = _run_parallel(
        {
            "squad_a": (
                lambda: ac.get_squad(id_a, SEASON),
                [],
                "Player squad A fetch error",
            ),
            "squad_b": (
                lambda: ac.get_squad(id_b, SEASON),
                [],
                "Player squad B fetch error",
            ),
            "injuries_a_raw": (
                lambda: ac.get_injuries(LEAGUE, SEASON, id_a),
                [],
                "Player injuries A fetch error",
            ),
            "injuries_b_raw": (
                lambda: ac.get_injuries(LEAGUE, SEASON, id_b),
                [],
                "Player injuries B fetch error",
            ),
            "fixtures_a": (
                lambda: ac.get_team_fixtures(id_a, LEAGUE, SEASON, last=20),
                [],
                "Player fixtures A fetch error",
            ),
            "fixtures_b": (
                lambda: ac.get_team_fixtures(id_b, LEAGUE, SEASON, last=20),
                [],
                "Player fixtures B fetch error",
            ),
        }
    )

    squad_a = results.get("squad_a") or []
    squad_b = results.get("squad_b") or []
    injuries_a_raw = results.get("injuries_a_raw") or []
    injuries_b_raw = results.get("injuries_b_raw") or []
    fixtures_a = results.get("fixtures_a") or []
    fixtures_b = results.get("fixtures_b") or []

    fixtures_a = pred.filter_recent_completed_fixtures(fixtures_a, current_season=SEASON)
    fixtures_b = pred.filter_recent_completed_fixtures(fixtures_b, current_season=SEASON)

    if not squad_a and not squad_b:
        return _critical_error("Player squad data is unavailable right now.")

    threats_a = _build_key_threats(squad_a, injuries_a_raw, fixtures_a, id_a)
    threats_b = _build_key_threats(squad_b, injuries_b_raw, fixtures_b, id_b)

    # Group full squads by position for the roster section
    def _group_by_position(squad):
        groups = {"Goalkeeper": [], "Defender": [], "Midfielder": [], "Attacker": []}
        for p in squad:
            player_obj = p.get("player") or p
            pos = player_obj.get("position") or p.get("position") or "Unknown"
            if pos in groups:
                groups[pos].append(player_obj)
            else:
                groups.setdefault("Unknown", []).append(player_obj)
        return groups

    return render_template(
        "player.html",
        **_page_context(
            team_a=team_a,
            team_b=team_b,
            selected_fixture=selected_fixture,
            threats_a=threats_a,
            threats_b=threats_b,
            squad_a=_group_by_position(squad_a),
            squad_b=_group_by_position(squad_b),
            injuries_a=_display_injuries(injuries_a_raw),
            injuries_b=_display_injuries(injuries_b_raw),
        ),
    )


@app.route("/player/analyze", methods=["POST"])
def player_analyze():
    _set_data_refresh()
    payload = request.get_json(silent=True) or request.form
    player_id = int(payload.get("player_id", 0) or 0)
    team_id = int(payload.get("team_id", 0) or 0)
    opponent_team_id = int(payload.get("opponent_team_id", 0) or 0)
    league_id = int(payload.get("league", LEAGUE) or LEAGUE)
    season = int(payload.get("season", SEASON) or SEASON)

    if not player_id:
        return jsonify({"error": "player_id is required"}), 400

    try:
        stats = ac.get_player_stats(player_id, league_id, season) or []
    except Exception as exc:
        app.logger.error("Player analysis stats fetch error: %s", exc)
        stats = []

    try:
        vs_team = ac.get_player_vs_team(player_id, team_id, opponent_team_id, seasons=[season, season - 1, season - 2]) if team_id and opponent_team_id else []
    except Exception as exc:
        app.logger.error("Player analysis vs-team fetch error: %s", exc)
        vs_team = []

    return jsonify(
        {
            "stats": stats,
            "vs_team": vs_team,
            "h2h_sample_warning": len(vs_team) < 3,
            "data_source": _football_data_source(),
            "last_updated": _now_stamp(),
        }
    )


@app.route("/match-analysis")
@app.route("/prediction")
def prediction():
    retry_after = _check_rate_limit("prediction", limit=60, window_seconds=60)
    if retry_after:
        return _error_response(429, f"Rate limit exceeded. Retry in {retry_after}s.")
    _set_data_refresh()
    match_id = (request.args.get("match_id") or "").strip()
    if match_id and _MATCH_BRAIN is not None:
        canonical = _MATCH_BRAIN.get_match_analysis(match_id)
        if not canonical:
            return _selection_error_redirect("soccer", "Match Analysis could not be loaded for the selected match.")
        prediction_block = canonical.get("prediction") or {}
        probs = prediction_block.get("probabilities") or {}
        home_team = ((canonical.get("teams") or {}).get("home") or {})
        away_team = ((canonical.get("teams") or {}).get("away") or {})
        scorpred = {
            "confidence_pct": prediction_block.get("confidence", 0),
            "play_type": prediction_block.get("action", "SKIP"),
            "win_probabilities": {
                "a": probs.get("home"),
                "draw": probs.get("draw"),
                "b": probs.get("away"),
            },
            "best_pick": {
                "prediction": prediction_block.get("side"),
                "reasoning": " | ".join((prediction_block.get("reasoning") or {}).get("strengths", [])),
            },
        }
        _built_card = dui.build_decision_card(
            analysis={
                "match_id": canonical.get("match_id"),
                "matchup": canonical.get("matchup"),
                "confidence": prediction_block.get("confidence"),
                "probabilities": {"a": probs.get("home"), "draw": probs.get("draw"), "b": probs.get("away")},
                "action": prediction_block.get("action"),
                "recommended_side": prediction_block.get("side"),
                "reason": " | ".join((prediction_block.get("reasoning") or {}).get("strengths", [])),
                "data_quality": prediction_block.get("data_quality"),
                "metric_breakdown": {
                    "edge_score": canonical.get("edge_score") or prediction_block.get("edge_score"),
                    "expected_value": canonical.get("expected_value") or prediction_block.get("expected_value"),
                    "risk_level": canonical.get("risk_level") or prediction_block.get("risk_level"),
                    "decision_grade": canonical.get("decision_grade") or prediction_block.get("decision_grade"),
                    "risk_score": canonical.get("risk_score") or prediction_block.get("risk_score"),
                },
            }
        )
        if _built_card and isinstance(prediction_block.get("adaptive_adjustment"), dict):
            _built_card["adaptive_adjustment"] = prediction_block["adaptive_adjustment"]
        scorpred["decision_card"] = _built_card
        return render_template(
            "prediction.html",
            **_page_context(
                team_a=home_team,
                team_b=away_team,
                prediction={"ui_prediction": scorpred},
                scorpred=scorpred,
                selected_fixture={"fixture_id": canonical.get("match_id"), "date": canonical.get("kickoff")},
                **_league_context(_active_league_id()),
            ),
        )
    team_a, team_b = _require_teams()
    if not team_a:
        return _selection_error_redirect("soccer", "Match Analysis could not be opened because no soccer fixture is selected.")
    selected_fixture = _selected_fixture()
    blocked = _soccer_unavailable_page(team_a, team_b, selected_fixture, template="prediction.html")
    if blocked is not None:
        return blocked
    league_id = _set_active_league(_active_league_id())

    id_a, id_b = team_a["id"], team_b["id"]
    results, task_status = _run_parallel_with_status(
        {
            "h2h": (
                lambda: ac.get_h2h(id_a, id_b, last=20),
                [],
                "Prediction H2H fetch error",
            ),
            "fixtures_a": (
                lambda: ac.get_team_fixtures(id_a, LEAGUE, SEASON, last=20),
                [],
                "Prediction team A fixtures fetch error",
            ),
            "fixtures_b": (
                lambda: ac.get_team_fixtures(id_b, LEAGUE, SEASON, last=20),
                [],
                "Prediction team B fixtures fetch error",
            ),
            "injuries_a_raw": (
                lambda: ac.get_injuries(LEAGUE, SEASON, id_a),
                [],
                "Prediction team A injuries fetch error",
            ),
            "injuries_b_raw": (
                lambda: ac.get_injuries(LEAGUE, SEASON, id_b),
                [],
                "Prediction team B injuries fetch error",
            ),
            "standings_for_opp": (
                lambda: ac.get_standings(LEAGUE, SEASON),
                [],
                "Prediction standings fetch error",
            ),
        }
    )
    h2h = results.get("h2h") or []
    fixtures_a = results.get("fixtures_a") or []
    fixtures_b = results.get("fixtures_b") or []
    injuries_a = _clean_injuries(results.get("injuries_a_raw") or [])
    injuries_b = _clean_injuries(results.get("injuries_b_raw") or [])
    standings_for_opp = results.get("standings_for_opp") or []

    h2h = pred.filter_recent_completed_fixtures(h2h, current_season=SEASON)
    fixtures_a = pred.filter_recent_completed_fixtures(fixtures_a, current_season=SEASON)
    fixtures_b = pred.filter_recent_completed_fixtures(fixtures_b, current_season=SEASON)

    if not fixtures_a and not fixtures_b and not h2h:
        app.logger.warning(
            "No historical data for %s vs %s â€” Scorpred will use neutral fallbacks",
            team_a["name"], team_b["name"],
        )

    # â”€â”€ Unified Scorpred Engine Model (ONLY prediction source) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Extract form data from fixtures
    ml_form_a = pred.extract_form(fixtures_a, id_a)[:10]
    ml_form_b = pred.extract_form(fixtures_b, id_b)[:10]
    form_a = ml_form_a[:5]
    form_b = ml_form_b[:5]
    
    # H2H form from each team's perspective
    h2h_form_a = pred.extract_form(h2h, id_a)[:5]
    h2h_form_b = pred.extract_form(h2h, id_b)[:5]

    # Standings â†’ opponent strength lookup for quality-of-schedule adjustment
    opp_strengths = _build_opp_strengths(standings_for_opp)

    # Optional decimal odds for edge calculation â€” all three required or ignored
    _odds: dict[str, float] | None = None
    try:
        _ho = (request.args.get("home_odds") or "").strip()
        _do = (request.args.get("draw_odds") or "").strip()
        _ao = (request.args.get("away_odds") or "").strip()
        if _ho and _do and _ao:
            _odds = {"home": float(_ho), "draw": float(_do), "away": float(_ao)}
    except (TypeError, ValueError):
        _odds = None

    blocked = _soccer_unavailable_page(team_a, team_b, selected_fixture, template="prediction.html", inputs=fixtures_a + fixtures_b)
    if blocked is not None:
        return blocked
    # Single unified prediction from ScorMastermind
    mastermind = sm.predict_match(
        {
            "sport": "soccer",
            "team_a_name": team_a["name"],
            "team_b_name": team_b["name"],
            "team_a_is_home": True,
            "form_a": form_a,
            "form_b": form_b,
            "ml_form_a": ml_form_a,
            "ml_form_b": ml_form_b,
            "target_date": selected_fixture.get("date"),
            "h2h_form_a": h2h_form_a,
            "h2h_form_b": h2h_form_b,
            "injuries_a": injuries_a,
            "injuries_b": injuries_b,
            "opp_strengths": opp_strengths,
            "team_stats": {
                "a": {"form": form_a},
                "b": {"form": form_b},
            },
            "odds": _odds,
            "historical_matches": fixtures_a + fixtures_b + h2h,
        }
    )
    prediction = mastermind.get("ui_prediction") or {}
    prediction["data_completeness"] = _build_prediction_data_completeness(
        form_a=form_a,
        form_b=form_b,
        h2h=h2h,
        injuries_a=injuries_a,
        injuries_b=injuries_b,
        standings_for_opp=standings_for_opp,
        task_status=task_status,
    )
    prediction["decision_explainer"] = _build_prediction_explainer(
        prediction,
        team_a_name=team_a["name"],
        team_b_name=team_b["name"],
    )
    selected_match_id = (selected_fixture or {}).get("fixture_id")
    analysis = prediction_service.get_match_analysis(str(selected_match_id)) if selected_match_id else None
    if not analysis:
        analysis = _analysis_from_prediction_payload(
            prediction,
            match_id=str(selected_match_id or ""),
            matchup=f"{team_a['name']} vs {team_b['name']}",
        )
    prediction["decision_card"] = dui.build_decision_card(analysis=analysis) if analysis else None
    if prediction["decision_card"]:
        prediction["play_type"] = prediction["decision_card"].get("action")
        prediction["confidence_pct"] = prediction["decision_card"].get("confidence")
    mastermind["ui_prediction"] = prediction

    # Not tracked for the same reason as /matchup: a different engine from the published one.

    return render_template(
        "prediction.html",
        **_page_context(
            team_a=team_a,
            team_b=team_b,
            prediction=mastermind,
            scorpred=mastermind.get("ui_prediction") or {},
            selected_fixture=selected_fixture,
            **_league_context(league_id),
        ),
    )


@app.route("/players", methods=["GET"])
def players():
    _set_data_refresh()

    league_id = _set_active_league(_active_league_id())
    team_a, team_b = _require_teams()
    if not team_a:
        return redirect(url_for("soccer", league=league_id))

    squad_a_raw = []
    squad_b_raw = []

    try:
        squad_a_raw = _fetch_team_squad(team_a["id"], SEASON, league_id)
    except Exception as exc:
        app.logger.warning("Players fetch failed for %s: %s", team_a["name"], exc)

    try:
        squad_b_raw = _fetch_team_squad(team_b["id"], SEASON, league_id)
    except Exception as exc:
        app.logger.warning("Players fetch failed for %s: %s", team_b["name"], exc)

    squad_a = _group_squad_by_position(squad_a_raw)
    squad_b = _group_squad_by_position(squad_b_raw)

    return render_template(
        "player.html",
        **_page_context(
            team_a=team_a,
            team_b=team_b,
            squad_a=squad_a,
            squad_b=squad_b,
            selected_fixture=_selected_fixture(),
            **_league_context(league_id),
        ),
    )


@app.route("/props", methods=["GET"])
def props():
    _set_data_refresh()
    league_id = _set_active_league(_active_league_id())
    team_a, team_b = _require_teams()
    squad_a: list = []
    squad_b: list = []
    tasks: dict[str, tuple[Callable[[], Any], Any, str]] = {}
    if team_a:
        tasks["squad_a"] = (
            lambda: ac.get_squad(team_a["id"], SEASON),
            [],
            "Props page squad A fetch error",
        )
    if team_b:
        tasks["squad_b"] = (
            lambda: ac.get_squad(team_b["id"], SEASON),
            [],
            "Props page squad B fetch error",
        )
    results = _run_parallel(tasks)
    squad_a = results.get("squad_a") or []
    squad_b = results.get("squad_b") or []

    return render_template(
        "props.html",
        **_page_context(
            team_a=team_a or {},
            team_b=team_b or {},
            squad_a=squad_a,
            squad_b=squad_b,
            sport="soccer",
            default_league_id=LEAGUE,
            football_markets=ac.get_market_catalog(),
            football_position_default_markets=ac.POSITION_DEFAULT_MARKETS,
            football_supported_league_ids=SUPPORTED_LEAGUE_IDS,
            current_season=SEASON,
            football_mock_mode=False,
            selected_fixture=_selected_fixture(),
            **_league_context(league_id),
        ),
    )


# â”€â”€ Props Bet Builder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.route("/props/generate", methods=["GET", "POST"])
def props_generate():
    _set_data_refresh()
    payload = request.get_json(silent=True) or request.values
    active_league_id = _set_active_league(_active_league_id())

    player_id = _safe_int(payload.get("player_id", 0), 0)
    player_name = payload.get("player_name", "Unknown Player")
    player_team_id = _safe_int(payload.get("player_team_id", 0), 0)
    opponent_id = _safe_int(payload.get("opponent_id", 0), 0)
    opponent_name = payload.get("opponent_name", "Opponent")
    is_home_str = str(payload.get("is_home", "true")).lower()
    markets_str = str(payload.get("markets", "goals,assists,shots_on_target,key_passes"))
    season = _safe_int(payload.get("season", SEASON), SEASON)
    league = _coerce_league_id(payload.get("league", active_league_id), active_league_id)
    player_position = payload.get("player_position", "")
    include_all_comps = str(payload.get("include_all_comps", "false")).lower() in ("true", "1", "yes", "on")
    league_ids_raw = str(payload.get("league_ids", ""))
    league_ids = [int(value) for value in league_ids_raw.split(",") if value.strip().isdigit()]

    if not player_id or not player_team_id or not opponent_id:
        return jsonify({"error": "Required: player_id, player_team_id, opponent_id"}), 400

    is_home = is_home_str not in ("false", "0", "no")
    markets = [m.strip() for m in markets_str.split(",") if m.strip()]

    try:
        result = pe.generate_props(
            sport             = "soccer",
            player_id         = player_id,
            player_name       = player_name,
            player_team_id    = player_team_id,
            opponent_team_id  = opponent_id,
            opponent_name     = opponent_name,
            is_home           = is_home,
            markets           = markets,
            player_position   = player_position,
            season            = season,
            league            = league,
            include_all_comps = include_all_comps,
            league_ids        = league_ids or None,
        )
        result["data_source"] = _football_data_source()
        result["last_updated"] = _now_stamp()
        return jsonify(result)
    except Exception as exc:
        app.logger.error("Props generation failed: %s", exc)
        return jsonify({"error": sanitize_error(exc), "data_source": _football_data_source(), "last_updated": _now_stamp()}), 500



@app.route("/chat", methods=["POST"])
def chat():
    payload = request.get_json(silent=True) or request.form or {}
    message = payload.get("message", "")
    message = str(message).strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    rate_limit = app.config.get("CHAT_RATE_LIMIT_COUNT", 10)
    rate_window = app.config.get("CHAT_RATE_LIMIT_WINDOW_SECONDS", 60)
    retry_after = check_chat_rate_limit(rate_limit, rate_window)
    if retry_after:
        return jsonify({"error": "chat rate limit exceeded", "retry_after": retry_after}), 429

    history = session.get("chat_history", [])[-8:]
    chat_context = _build_chat_request_context(payload)
    response = _chat_reply(message, history=history, chat_context=chat_context)
    if isinstance(response, str):
        reply = response
        suggestions: list = []
        intent = None
        mode = "fallback"
    else:
        reply = response.get("reply") or ""
        suggestions = response.get("suggestions", [])
        intent = response.get("intent")
        mode = response.get("mode")
    history.extend(
        [
            {"role": "user", "content": message, "timestamp": _now_stamp()},
            {"role": "assistant", "content": reply, "timestamp": _now_stamp()},
        ]
    )
    session["chat_history"] = history[-10:]
    return jsonify(
        {
            "reply": reply,
            "suggestions": suggestions,
            "intent": intent,
            "mode": mode,
            "history": session["chat_history"],
            "last_updated": _now_stamp(),
        }
    )


@app.route("/chat/clear", methods=["POST"])
def chat_clear():
    session.pop("chat_history", None)
    return jsonify({"status": "cleared", "last_updated": _now_stamp()})


@app.route("/api/football/leagues", methods=["GET"])
@limiter.limit("60/hour")
def api_football_leagues():
    current_league_id = _set_active_league(_active_league_id())
    return jsonify(
        {
            "leagues": _football_supported_leagues(),
            "season": SEASON,
            "current_league_id": current_league_id,
        }
    )


@app.route("/api/football/teams", methods=["GET"])
@limiter.limit("60/hour")
def api_football_teams():
    league_id = _set_active_league(_coerce_league_id(request.args.get("league", _active_league_id())))
    try:
        teams = ac.get_teams(league_id, SEASON)
    except Exception as exc:
        app.logger.error("api_football_teams failed: %s", exc)
        return jsonify({"error": "Unable to load teams", "league": league_id, "season": SEASON}), 503
    return jsonify({"teams": teams or [], "league": league_id, "season": SEASON})


@app.route("/api/football/squad", methods=["GET"])
@limiter.limit("30/hour")
def api_football_squad():
    try:
        team_id = validators.positive_int(request.args.get("team_id"), "team_id")
    except validators.ValidationError as exc:
        return jsonify({"error": "invalid_parameter", "field": exc.field, "message": str(exc)}), 400
    league_id = _coerce_league_id(request.args.get("league", _active_league_id()))
    try:
        squad = ac.get_squad(team_id, SEASON, league_id)
    except Exception as exc:
        app.logger.error("api_football_squad failed for team_id=%s: %s", team_id, exc)
        return jsonify({"error": "Unable to load squad", "team_id": team_id}), 503
    return jsonify({"team_id": team_id, "league": league_id, "squad": squad or []})


@app.route("/api/football/team-form", methods=["GET"])
@limiter.limit("30/hour")
def api_football_team_form():
    try:
        team_id = validators.positive_int(request.args.get("team_id"), "team_id")
    except validators.ValidationError as exc:
        return jsonify({"error": "invalid_parameter", "field": exc.field, "message": str(exc)}), 400
    league_id = _coerce_league_id(request.args.get("league", _active_league_id()))

    try:
        payload = _team_form_payload(team_id, league_id=league_id)
    except Exception as exc:
        app.logger.error("api_football_team_form failed for team_id=%s: %s", team_id, exc)
        return jsonify({"error": "Unable to load team form", "team_id": team_id}), 503

    return jsonify({**payload, "league": league_id})


@app.route("/api/player-stats", methods=["GET"])
@limiter.limit("30/hour")
def api_player_stats():
    try:
        player_id = validators.positive_int(
            request.args.get("player_id") or request.args.get("id"), "player_id"
        )
        season = validators.year(request.args.get("season", SEASON), "season")
    except validators.ValidationError as exc:
        return jsonify({"error": "invalid_parameter", "field": exc.field, "message": str(exc)}), 400
    league = _coerce_league_id(request.args.get("league", _active_league_id()))
    try:
        stats = ac.get_player_stats(player_id, season=season, league_id=league)
    except Exception as exc:
        app.logger.error(
            "api_player_stats failed for player_id=%s season=%s league=%s: %s",
            player_id,
            season,
            league,
            exc,
        )
        return jsonify({"error": "Unable to load player stats", "player_id": player_id}), 503
    return jsonify({"player_id": player_id, "stats": stats or []})


@app.route("/today-soccer-predictions", methods=["GET"])
@limiter.limit("20/hour")
def today_soccer_predictions():
    """Show soccer predictions for today's fixtures or next available fixtures."""
    _set_data_refresh()
    league_id = _set_active_league(_active_league_id())
    fixtures_with_pred, grouped_fixtures, load_error, data_source = _load_grouped_upcoming_fixtures_all_leagues(
        next_n_per_league=6,
        max_deep_predictions=1,
        include_injuries=False,
        include_standings=False,
    )

    def _build_prediction_item(fixture: dict) -> dict | None:
        try:
            teams_block = fixture.get("teams", {})
            home_team = teams_block.get("home", {})
            away_team = teams_block.get("away", {})
            league_block = fixture.get("league", {})
            match_id = (fixture.get("fixture") or {}).get("id")
            result = analyze_match(match_id)
            card = build_decision_card(analysis=result)
            if card is None:
                return None
            probs = card.get("probabilities") or {}
            metric_breakdown = card.get("metric_breakdown") if isinstance(card.get("metric_breakdown"), dict) else {}
            home_metrics = metric_breakdown.get("home") if isinstance(metric_breakdown.get("home"), dict) else {}
            away_metrics = metric_breakdown.get("away") if isinstance(metric_breakdown.get("away"), dict) else {}

            return {
                "fixture": fixture,
                "decision_card": card,
                "home_team": home_team,
                "away_team": away_team,
                "league": league_block,
                "action": card.get("action"),
                "recommended_side": card.get("recommended_side"),
                "confidence": card.get("confidence"),
                "prob_home": probs.get("a"),
                "prob_draw": probs.get("draw"),
                "prob_away": probs.get("b"),
                "reason": card.get("reason"),
                "data_quality": card.get("data_quality"),
                "metric_breakdown": metric_breakdown,
                "has_data": bool(home_metrics.get("form") is not None and away_metrics.get("form") is not None),
            }
        except Exception as e:
            app.logger.warning("Error preparing fixture prediction: %s", e)
            return None

    predictions = [item for item in (_build_prediction_item(fixture) for fixture in fixtures_with_pred) if item]
    decision_cards = [item["decision_card"] for item in predictions if item.get("decision_card")]
    grouped_predictions = []
    for group in grouped_fixtures:
        items = [item for item in (_build_prediction_item(fixture) for fixture in group.get("fixtures", [])) if item]
        grouped_predictions.append({**group, "predictions": items, "cards": [item["decision_card"] for item in items if item.get("decision_card")]})

    return render_template(
        "today_predictions.html",
        **_page_context(
            predictions=predictions,
            full_slate=decision_cards,
            top_opportunities=dui.top_opportunities(decision_cards, limit=4),
            today_plan=dui.plan_summary(decision_cards),
            grouped_predictions=grouped_predictions,
            total_fixtures=len(fixtures_with_pred),
            total_predictions=len(predictions),
            load_error=load_error,
            data_source=data_source,
            **_league_context(league_id),
        ),
    )


@app.route("/top-picks-today", methods=["GET"])
@limiter.limit("60/hour")
def top_picks_today():
    """Fold legacy top-picks traffic into the Soccer workspace."""
    return redirect(url_for("soccer"))

@app.route("/model-performance")
def model_performance():
    """Redirect legacy credibility traffic to Insights."""
    return redirect(url_for("insights"))


def _refresh_soccer_cache(league_id: int | None = None) -> dict[str, Any]:
    target = _coerce_league_id(league_id if league_id is not None else _active_league_id())
    cards, fixtures, load_error, data_source, _ = prediction_service.get_fixture_cards(target)
    prediction_service.get_top_opportunities(target)
    prediction_service.get_today_plan(target)
    return {
        "league_id": target,
        "fixtures_loaded": len(fixtures or []),
        "cards_loaded": len(cards or []),
        "load_error": load_error,
        "data_source": data_source,
    }


@app.route("/admin/refresh-cache", methods=["POST"])
@user_auth.admin_required
def refresh_cache():
    league_id = _coerce_league_id(request.args.get("league", _active_league_id()))
    return jsonify(_refresh_soccer_cache(league_id))


@app.cli.command("refresh-cache")
def refresh_cache_cli():
    payload = _refresh_soccer_cache(_active_league_id())
    print(payload)


@app.cli.command("prune-cache")
def prune_cache_cli():
    """Delete expired provider responses and enforce SCORPRED_CACHE_MAX_MB."""
    from services import cache_pruner
    click.echo(cache_pruner.prune())


@app.cli.command("grant-admin")
@click.argument("email")
def grant_admin(email):
    """Promote an existing, independently verified account from the server CLI."""
    from db_models import User
    user = User.query.filter_by(email=email.strip().lower()).first()
    if user is None:
        raise click.ClickException("Create and verify the account before granting administrator access.")
    user.is_admin = True
    db.session.commit()
    click.echo("Administrator access granted to the existing account.")

@app.route("/pass-analysis")
def pass_analysis():
    """Display recent successful picks to complement failure analysis."""
    sport_filter = request.args.get("sport", "").lower()
    rolling_window = request.args.get("window", default=10, type=int) or 10
    try:
        evaluation = mt.get_evaluation_dashboard(
            rolling_window=max(1, min(rolling_window, 50)),
        )
        pass_rows = evaluation.get("pass_rows", [])
        if sport_filter in ["soccer", "nba"]:
            pass_rows = [
                row for row in pass_rows
                if row.get("sport", "").lower() == sport_filter
            ]
    except Exception as exc:
        app.logger.error("pass_analysis: failed to build pass rows - %s", exc, exc_info=True)
        evaluation = {"kpis": {}, "rolling_window": rolling_window}
        pass_rows = []

    return render_template(
        "pass_analysis.html",
        **_page_context(
            evaluation=evaluation,
            pass_rows=pass_rows,
        ),
    )


@app.route("/prediction-result/<prediction_id>")
def prediction_result_detail(prediction_id: str):
    record = mt.get_prediction_by_id(prediction_id) or {}
    if not record:
        return _critical_error("Prediction not found.", 404)

    sport = str(record.get("sport") or "soccer").lower()
    if sport == "nba":
        evidence = _build_nba_evidence(record)
    else:
        evidence = _build_soccer_evidence(record)
    comparison = _build_prediction_vs_reality(record)

    probability_rows = [
        {"label": record.get("team_a") or "Team A", "value": _safe_float(record.get("prob_a"), 0.0)},
        {"label": "Draw", "value": _safe_float(record.get("prob_draw"), 0.0)} if sport == "soccer" else None,
        {"label": record.get("team_b") or "Team B", "value": _safe_float(record.get("prob_b"), 0.0)},
    ]
    probability_rows = [row for row in probability_rows if row]

    model_component_sections = _prepare_model_component_sections(record)

    return render_template(
        "prediction_result_detail.html",
        **_page_context(
            record=record,
            prediction_date_display=_format_prediction_date(record.get("date") or record.get("created_at")),
            updated_at_display=_format_prediction_date(record.get("updated_at") or record.get("date")),
            predicted_pick_display=_prediction_pick_display(record),
            probability_rows=probability_rows,
            evidence=evidence,
            comparison=comparison,
            model_component_sections=model_component_sections,
        ),
    )


@app.route("/strategy-lab")
def strategy_lab():
    """Redirect the legacy lab surface to Insights."""
    return redirect(url_for("insights"))

@app.route("/backtesting")
@app.route("/compare")
@app.route("/explainability")
def retired_analytics_pages():
    """Retire legacy analytics pages and keep users on Insights."""
    return redirect(url_for("insights"))

@app.route("/update-prediction-results", methods=["GET", "POST"])
def update_prediction_results():
    return redirect(url_for("insights"))

@app.route("/worldcup", methods=["GET", "POST"])
def worldcup():
    _set_data_refresh()
    teams = sorted(pred.WC_TEAMS.keys())
    result = None
    team_a = request.form.get("team_a", "")
    team_b = request.form.get("team_b", "")
    wc_error = None

    wc_fixtures = []
    for slug in ("FIFA.WC.2026", "FIFA.WC", "FIFA.WWQ.CONMEBOL"):
        try:
            wc_fixtures = ac.get_espn_fixtures(slug, next_n=16)
            if wc_fixtures:
                break
        except Exception:
            _logger.warning("Failed to fetch World Cup fixtures for slug=%s", slug, exc_info=True)
            continue

    if request.method == "POST" and team_a and team_b:
        if team_a == team_b:
            wc_error = "Please select two different teams."
        else:
            result = pred.wc_predict(team_a, team_b)
            if result is None:
                wc_error = "Unknown team name â€” pick from the list."

    return render_template(
        "worldcup.html",
        **_page_context(
            teams=teams,
            team_a=team_a,
            team_b=team_b,
            result=result or {},
            wc_error=wc_error,
            wc_fixtures=wc_fixtures,
        ),
    )

@app.route("/health")
@app.route("/status")
@limiter.exempt
def health():
    # Keep this fast — Render polls it every 30s. No external API calls here.
    try:
        db.session.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        _logger.warning("Database health check failed", exc_info=True)
        db_status = "unavailable"
    cache_status = "redis" if cache_service._get_redis_client() is not None else "local"
    brain_health = {}
    if _MATCH_BRAIN is not None:
        try:
            brain_health = _MATCH_BRAIN.get_system_health() or {}
        except Exception:
            brain_health = {}
    ready = db_status == "connected"
    return jsonify(
        {
            "status": "ok" if ready else "unavailable",
            "app": "ScorPred",
            "release": _runtime_release_tag(),
            "db": db_status,
            "cache": cache_status,
            "api": brain_health.get("api_status", "unknown"),
            "degraded_mode": bool(brain_health.get("degraded_mode")),
            "last_refresh": brain_health.get("last_refresh_time"),
            "error_count": int(brain_health.get("error_count") or 0),
            "timestamp": _now_stamp(),
        }
    ), 200 if ready else 503


@app.route("/live")
@limiter.exempt
def liveness():
    return jsonify({"status": "ok"})


@app.route("/api/football/relevant-competitions")
@limiter.limit("30/hour")
def football_relevant_competitions_api():
    _set_data_refresh()
    try:
        player_id = validators.positive_int(request.args.get("player_id"), "player_id")
        team_id = validators.positive_int(request.args.get("team_id"), "team_id")
    except validators.ValidationError as exc:
        return jsonify({"error": "invalid_parameter", "field": exc.field, "message": str(exc)}), 400
    primary_league = request.args.get("league", default=LEAGUE, type=int) or LEAGUE
    season = request.args.get("season", default=SEASON, type=int) or SEASON

    try:
        league_ids = ac.relevant_competitions_for_player(
            player_id,
            team_id,
            primary_league,
            season,
        )
    except Exception as exc:
        return jsonify({"competitions": [], "league_ids": [], "error": sanitize_error(exc), "data_source": _football_data_source(), "last_updated": _now_stamp()}), 200
    competitions = []
    for league_id in league_ids:
        info = LEAGUE_BY_ID.get(league_id, {})
        competitions.append({
            "id": league_id,
            "name": info.get("name", f"League {league_id}"),
            "flag": info.get("flag", ""),
            "difficulty": info.get("difficulty", 1.0),
        })
    return jsonify({"competitions": competitions, "league_ids": league_ids, "data_source": _football_data_source(), "last_updated": _now_stamp()})


@app.route("/api/football/prefetch-competition")
@limiter.limit("30/hour")
def football_prefetch_competition_api():
    _set_data_refresh()
    try:
        player_id = validators.positive_int(request.args.get("player_id"), "player_id")
        team_id = validators.positive_int(request.args.get("team_id"), "team_id")
        league_id = validators.positive_int(request.args.get("league_id"), "league_id")
    except validators.ValidationError as exc:
        return jsonify({"error": "invalid_parameter", "field": exc.field, "message": str(exc)}), 400
    season = request.args.get("season", default=SEASON, type=int) or SEASON

    try:
        payload = ac.prefetch_competition(player_id, team_id, league_id, season)
        payload["data_source"] = _football_data_source()
        payload["last_updated"] = _now_stamp()
        return jsonify(payload)
    except Exception as exc:
        return jsonify({
            "league_id": league_id,
            "league_name": (LEAGUE_BY_ID.get(league_id) or {}).get("name", f"League {league_id}"),
            "error": sanitize_error(exc),
            "data_source": _football_data_source(),
            "last_updated": _now_stamp(),
        }), 200


# â”€â”€ Error page â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.route("/my-bets", methods=["GET"])
@user_auth.login_required
def my_bets():
    rows = bets_service.list_bets(user_id=g.current_user["id"])
    graded = {str(p.get("fixture_id")): p for p in mt._load_predictions() if p.get("fixture_id")}
    bets = []
    for row in rows:
        tracked = graded.get(str(row.get("match_id") or ""))
        final = (tracked or {}).get("final_score") if (tracked or {}).get("status") == "completed" else None
        label, css, score = "Pending", "push", "—"
        if isinstance(final, dict) and final.get("a") is not None:
            team_a, team_b = tracked.get("team_a") or "", tracked.get("team_b") or ""
            side = mt._canonical_outcome(re.sub(r"\s+win!?$", "", str(row["recommended_side"]), flags=re.I), team_a, team_b)
            actual = mt._actual_result_from_score(final)
            score = f'{final["a"]}-{final["b"]}'
            label, css = ("Won", "won") if side and side == actual else ("Lost", "lost") if side else ("Final", "push")
        bets.append({"id": row["id"], "date": row["created_at"][:10], "match": row["matchup"],
                     "pick": row["recommended_side"], "pick_type": row["action"], "score": score,
                     "result_label": label, "result_class": css, "confidence": row["confidence"]})
    won, lost = sum(b["result_class"] == "won" for b in bets), sum(b["result_class"] == "lost" for b in bets)
    return render_template("my_bets.html", bets=bets, won_count=won, lost_count=lost,
                           pushed_count=len(bets) - won - lost,
                           win_rate=round(100 * won / (won + lost)) if won + lost else None,
                           lost_rate=round(100 * lost / (won + lost)) if won + lost else None,
                           roi="N/A", total_profit="N/A", total_stake="N/A", **_page_context())


@app.route("/add-bet", methods=["POST"])
@app.route("/api/bets", methods=["POST"])
@limiter.limit("30 per minute")
@user_auth.api_login_required
def add_bet():
    retry_after = _check_rate_limit("add-bet", limit=30, window_seconds=60)
    if retry_after:
        return {"status": "error", "message": "rate limit exceeded", "retry_after": retry_after}, 429
    data = request.get_json(silent=True) or {}
    try:
        data = validators.validate_bet_payload(data)
    except validators.ValidationError as exc:
        return {"status": "error", "error": str(exc)}, 400
    if _MATCH_BRAIN is not None:
        canonical = _MATCH_BRAIN.get_match_analysis(data.get("match_id"))
        if canonical:
            _MATCH_BRAIN.track_match(canonical)
    try:
        created = bets_service.create_bet(data, user_id=g.current_user["id"])
    except bets_service.BetValidationError as exc:
        return {"status": "error", "error": str(exc)}, 400
    return {"status": "ok", "bet": created}


@app.route("/clear-bets", methods=["POST"])
@app.route("/api/bets", methods=["DELETE"])
@limiter.limit("10 per minute")
@user_auth.api_login_required
def clear_bets():
    bets_service.clear_bets(user_id=g.current_user["id"])
    return {"status": "ok"}


@app.route("/delete-bet/<int:bet_id>", methods=["POST"])
@app.route("/api/bets/<int:bet_id>", methods=["DELETE"])
@limiter.limit("30 per minute")
@user_auth.api_login_required
def delete_bet(bet_id: int):
    deleted = bets_service.delete_bet(bet_id, user_id=g.current_user["id"])
    if not deleted:
        return {"status": "not_found"}, 404
    return {"status": "ok"}


@app.route("/api/bets", methods=["GET"])
@user_auth.api_login_required
def api_bets():
    return jsonify({"bets": bets_service.list_bets(user_id=g.current_user["id"])})


@app.route("/api/session", methods=["GET"])
def api_session():
    from security import get_csrf_token
    user = user_auth.current_user()
    return jsonify({"user": {"email": user["email"], "id": user["id"]} if user else None, "csrf_token": get_csrf_token()})


@app.route("/performance", methods=["GET"])
def performance():
    _refresh_tracking_results_if_due()
    window = (request.args.get("window") or "all").strip().lower()
    supported_windows = {"today", "tomorrow", "yesterday", "week", "month", "all"}
    if window not in supported_windows:
        window = "all"

    now_utc = datetime.now(timezone.utc)
    base_date = now_utc.date()
    completed = mt.get_completed_predictions(limit=2000)
    pending = mt.get_pending_predictions(limit=2000)

    def _as_dt(row: dict[str, Any]) -> datetime | None:
        raw = str(row.get("game_date") or row.get("date") or row.get("created_at") or "").strip()
        if not raw:
            return None
        text = raw.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    def _in_window(row_dt: datetime | None) -> bool:
        if row_dt is None:
            return window == "all"
        delta_days = (row_dt.date() - base_date).days
        if window == "today":
            return delta_days == 0
        if window == "tomorrow":
            return delta_days == 1
        if window == "yesterday":
            return delta_days == -1
        if window == "week":
            return row_dt.date() >= base_date - timedelta(days=7)
        if window == "month":
            return row_dt.date() >= base_date - timedelta(days=30)
        return True

    completed_window = [row for row in completed if _in_window(_as_dt(row))]
    pending_window = [row for row in pending if _in_window(_as_dt(row))]

    won_count = sum(1 for row in completed_window if row.get("is_correct") is True)
    lost_count = sum(1 for row in completed_window if row.get("is_correct") is False)
    settled_count = len(completed_window)
    open_count = len(pending_window)
    total_tracked = settled_count + open_count
    win_rate_value = round((won_count / settled_count) * 100, 1) if settled_count else 0.0

    scoreboard_rows = []
    for row in sorted(completed_window + pending_window, key=lambda item: _as_dt(item) or now_utc, reverse=True)[:30]:
        score = row.get("final_score") if isinstance(row.get("final_score"), dict) else {}
        score_label = "Scheduled"
        if score and "a" in score and "b" in score:
            score_label = f"{score.get('a', 0)}-{score.get('b', 0)}"
        status = "Open"
        status_class = "push"
        if str(row.get("status") or "").lower() == "completed":
            if row.get("is_correct") is True:
                status, status_class = "Correct", "won"
            elif row.get("is_correct") is False:
                status, status_class = "Incorrect", "lost"
            else:
                status = "Settled"
        scoreboard_rows.append(
            {
                "date": _format_prediction_date(row.get("game_date") or row.get("date") or row.get("created_at")),
                "match": f"{row.get('team_a') or 'Team A'} vs {row.get('team_b') or 'Team B'}",
                "sport": str(row.get("sport") or "soccer").upper(),
                "pick": row.get("predicted_pick_label") or row.get("predicted_winner") or "Unavailable",
                "score": score_label,
                "status": status,
                "status_class": status_class,
                "confidence": _prediction_confidence_pct(row),
            }
        )

    trend_source = sorted(completed_window, key=lambda item: _as_dt(item) or now_utc)
    cumulative = 0
    profit_trend_labels = []
    profit_trend_values = []
    for row in trend_source[-12:]:
        cumulative += 1 if row.get("is_correct") is True else -1
        label_dt = _as_dt(row) or now_utc
        profit_trend_labels.append(label_dt.strftime("%b %d"))
        profit_trend_values.append(cumulative)

    win_rate = "N/A" if settled_count == 0 else f"{win_rate_value:.1f}%"
    calibration = calibration_service.get_calibration(completed_window)
    recent_evaluated = completed_window[:30]
    recent_accuracy = None
    if recent_evaluated:
        recent_accuracy = sum(1 for row in recent_evaluated if row.get("is_correct") is True) / len(recent_evaluated)
    dq_values = []
    for row in completed_window:
        factors = row.get("model_factors") if isinstance(row.get("model_factors"), dict) else {}
        snapshot = factors.get("canonical_snapshot") if isinstance(factors, dict) else {}
        dq = (snapshot or {}).get("data_quality")
        if isinstance(dq, (int, float)):
            dq_values.append(float(dq))
    average_data_quality = (sum(dq_values) / len(dq_values)) if dq_values else None
    trust = model_trust_service.compute_trust_score(
        calibration_score=calibration.get("calibration_score"),
        recent_accuracy=recent_accuracy,
        average_data_quality=average_data_quality,
        sample_size=len(completed_window),
    )
    ctx = _page_context()
    ctx.update({
        "roi": "N/A",
        "win_rate": win_rate,
        "total_profit": "N/A",
        "record": f"{won_count}W-{lost_count}L-{max(0, settled_count - won_count - lost_count)}P",
        "avg_odds": "N/A",
        "won_count": won_count,
        "lost_count": lost_count,
        "pushed_count": open_count,
        "total_bets": total_tracked,
        "has_settled_results": settled_count > 0,
        "league_breakdown": [],
        "profit_trend_labels": profit_trend_labels,
        "profit_trend_values": profit_trend_values,
        "results_rows": scoreboard_rows,
        "calibration_rows": calibration.get("rows") or [],
        "calibration_error": calibration.get("calibration_error"),
        "calibration_score": calibration.get("calibration_score"),
        "trust_score": trust.get("trust_score"),
        "trust_label": trust.get("label"),
        "evaluated_sample_size": len(completed_window),
        "window": window,
        "window_counts": {
            "today": len([r for r in completed + pending if _in_window(_as_dt(r)) and (_as_dt(r) and (_as_dt(r).date() - base_date).days == 0)]),
            "tomorrow": len([r for r in completed + pending if _as_dt(r) and (_as_dt(r).date() - base_date).days == 1]),
            "yesterday": len([r for r in completed + pending if _as_dt(r) and (_as_dt(r).date() - base_date).days == -1]),
            "week": len([r for r in completed + pending if _as_dt(r) and _as_dt(r).date() >= base_date - timedelta(days=7)]),
            "month": len([r for r in completed + pending if _as_dt(r) and _as_dt(r).date() >= base_date - timedelta(days=30)]),
            "all": len(completed + pending),
        },
    })
    return render_template("performance.html", **ctx)


@app.route("/alerts", methods=["GET"])
def alerts():
    league_id = _active_league_id()
    if _MATCH_BRAIN is not None:
        canonical_alerts = _MATCH_BRAIN.get_alerts(league_id)
        active_alerts = [
            {
                "level": "high" if row.get("type") == "high_confidence_opportunity" else "info",
                "type": row.get("type", "Alert").replace("_", " ").title(),
                "title": row.get("title") or "Alert",
                "description": row.get("description") or "",
                "time": "Live",
                "match_url": f"/prediction?match_id={row.get('match_id')}" if row.get("match_id") else "/soccer",
            }
            for row in canonical_alerts
        ]
    else:
        cards = prediction_service.get_top_opportunities(league_id) or []
        active_alerts = []
        for card in cards:
            active_alerts.append(
                {
                    "level": "high" if str(card.get("action") or "").upper() == "BET" else "info",
                    "type": "Opportunity",
                    "title": card.get("matchup") or "Unavailable",
                    "description": f"Pick: {card.get('recommended_side') or 'Unavailable'} · Confidence: {int(_safe_float(card.get('confidence_pct'), 0))}%",
                    "time": "Live",
                    "match_url": "/soccer",
                }
            )
    ctx = _page_context()
    ctx.update({"active_alerts": active_alerts, "alert_count": len(active_alerts)})
    return render_template("alerts.html", **ctx)


@app.route("/system-intelligence", methods=["GET"])
def system_intelligence():
    if _MATCH_BRAIN is None:
        return render_template("system_intelligence.html", intelligence={}, **_page_context())
    _MATCH_BRAIN.refresh_cycle(_active_league_id(), min_interval_seconds=60)
    intelligence = _MATCH_BRAIN.get_system_intelligence()
    return render_template("system_intelligence.html", intelligence=intelligence, **_page_context())


@app.route("/watchlist", methods=["GET"])
def watchlist():
    watched_names = session.get("watchlist_teams") if isinstance(session.get("watchlist_teams"), list) else []
    watched_names = [str(team).strip() for team in watched_names if str(team).strip()]
    watched_set = {team.lower() for team in watched_names}

    league_id = _active_league_id()
    _, fixtures, _, _, _ = prediction_service.get_fixture_cards(league_id)
    upcoming_matches = []
    for fixture in fixtures or []:
        teams_block = fixture.get("teams") or {}
        home = (teams_block.get("home") or {}).get("name") or ""
        away = (teams_block.get("away") or {}).get("name") or ""
        if not home or not away:
            continue
        if home.lower() not in watched_set and away.lower() not in watched_set:
            continue
        upcoming_matches.append(
            {
                "matchup": f"{home} vs {away}",
                "date": _format_prediction_date(((fixture.get("fixture") or {}).get("date") or "")),
                "league": ((fixture.get("league") or {}).get("name") or f"League {league_id}"),
            }
        )
    upcoming_matches = sorted(upcoming_matches, key=lambda row: row.get("date", ""))[:40]
    watched_teams = []
    for team_name in watched_names:
        next_match = next((m["matchup"] for m in upcoming_matches if team_name.lower() in m["matchup"].lower()), "No upcoming match")
        watched_teams.append(
            {
                "name": team_name,
                "logo": "",
                "league": "Tracked",
                "next_match": next_match,
                "recent_form": [],
                "form_pct": None,
            }
        )
    ctx = _page_context()
    ctx.update({"watched_teams": watched_teams, "watchlist_matches": upcoming_matches})
    return render_template("watchlist.html", **ctx)


@app.route("/watchlist/team", methods=["POST"])
def watchlist_team_add():
    retry_after = _check_rate_limit("watchlist-team", limit=60, window_seconds=60)
    if retry_after:
        return {"status": "error", "message": "rate limit exceeded", "retry_after": retry_after}, 429
    team = str(request.form.get("team") or request.args.get("team") or "").strip()
    if not team:
        return redirect(request.referrer or url_for("watchlist"))
    watched = session.get("watchlist_teams") if isinstance(session.get("watchlist_teams"), list) else []
    normalized = {str(name).strip().lower() for name in watched}
    if team.lower() not in normalized:
        watched.append(team)
    session["watchlist_teams"] = watched
    return redirect(request.referrer or url_for("watchlist"))


@app.route("/watchlist/team/remove", methods=["POST"])
def watchlist_team_remove():
    team = str(request.form.get("team") or request.args.get("team") or "").strip().lower()
    watched = session.get("watchlist_teams") if isinstance(session.get("watchlist_teams"), list) else []
    watched = [name for name in watched if str(name).strip().lower() != team]
    session["watchlist_teams"] = watched
    return redirect(request.referrer or url_for("watchlist"))


@app.route("/settings", methods=["GET"])
def settings():
    ctx = _page_context()
    return render_template("settings.html", **ctx)


@app.route("/ui-mockup", methods=["GET"])
def ui_mockup():
    """High-fidelity UI concept board for ScorPred AI."""
    return render_template("ui_mockup.html", **_page_context())


# ── React Dashboard JSON API ──────────────────────────────────────────────────

def _normalize_data_label(raw: str | None) -> str:
    s = str(raw or "").lower()
    if "strong" in s:
        return "Strong Data"
    if "limited" in s:
        return "Limited Data"
    return "Partial Data"


def _card_to_decision(card: dict) -> dict:
    action = str(card.get("action") or "CONSIDER").upper()
    if action not in {"BET", "CONSIDER", "SKIP"}:
        action = "CONSIDER"
    team_a = card.get("team_a") or card.get("home_team") or ""
    team_b = card.get("team_b") or card.get("away_team") or ""
    rec_side = card.get("recommended_side") or team_a
    team_a_logo = card.get("team_a_logo") or ""
    team_b_logo = card.get("team_b_logo") or ""
    # Use pre-computed key when available (soccer cards); fall back to string match for NBA
    side_key = card.get("recommended_side_key") or ""
    if side_key == "a":
        side_is_a = True
    elif side_key == "b":
        side_is_a = False
    else:
        _clean = lambda s: str(s).lower().replace(" win", "").replace("!", "").strip()
        side_is_a = _clean(rec_side) in _clean(team_a) or _clean(team_a) in _clean(rec_side)
    logo = team_a_logo if side_is_a else (team_b_logo or team_a_logo)
    opponent = team_b if side_is_a else team_a
    sport = str(card.get("sport") or "soccer").lower()
    return {
        "action": action,
        "side": rec_side,
        "opponent": opponent,
        "teamA": team_a,
        "teamB": team_b,
        "matchup": card.get("matchup") or "",
        "confidence": int(dui.safe_float(card.get("confidence_pct") or card.get("confidence"), 0)),
        "reason": card.get("reason") or "",
        "data": _normalize_data_label(
            (card.get("data_confidence") or {}).get("label") or card.get("data_quality")
        ),
        "support": card.get("competition") or "",
        "logo": logo,
        "leagueLogo": card.get("league_logo") or "",
        "sport": sport,
        "homeId": card.get("team_a_id") or card.get("home_id") or None,
        "awayId": card.get("team_b_id") or card.get("away_id") or None,
        "leagueId": card.get("league_id") or None,
        "matchDate": card.get("game_date") or card.get("match_date") or card.get("date") or "",
    }


@app.route("/api/dashboard/home", methods=["GET"])
def api_dashboard_home():
    try:
        home_ctx = _build_home_dashboard_context()
        all_cards = home_ctx.get("all_cards") or home_ctx.get("full_slate") or []
        top = home_ctx.get("top_opportunities") or dui.top_opportunities(all_cards, limit=4)
        plan = home_ctx.get("today_plan") or dui.plan_summary(all_cards)
        insight_rows = [
            {
                "match": c.get("matchup") or "",
                "action": str(c.get("action") or "CONSIDER").upper(),
                "side": c.get("recommended_side") or "",
                "confidence": f"{int(dui.safe_float(c.get('confidence_pct') or c.get('confidence'), 0))}%",
                "trust": _normalize_data_label(
                    (c.get("data_confidence") or {}).get("label") or c.get("data_quality")
                ),
            }
            for c in all_cards[:8]
        ]
        return jsonify({
            "topOpportunities": [_card_to_decision(c) for c in top],
            "insightRows": insight_rows,
            "plan": {"bet": plan.get("bet", 0), "consider": plan.get("consider", 0), "skip": plan.get("skip", 0)},
            "last_updated": _now_stamp(),
        })
    except Exception as exc:
        app.logger.warning("api_dashboard_home error: %s", exc)
        return jsonify({"topOpportunities": [], "insightRows": [], "plan": {"bet": 0, "consider": 0, "skip": 0}}), 200


@app.route("/api/leagues", methods=["GET"])
def api_leagues():
    from league_config import all_leagues as _all_leagues
    return jsonify({
        "leagues": [
            {"id": lg["id"], "name": lg["name"], "flag": lg.get("flag", ""), "country": lg.get("country", "")}
            for lg in _all_leagues()
        ]
    })


@app.route("/api/health", methods=["GET"])
def api_health():
    status = _pipeline_monitor.get_status()
    http_code = 200 if status["overall"] == "ok" else 503
    return jsonify(status), http_code


def _bounded_soccer_cards(league_id, *, timeout_seconds=20, selected_date=None):
    """Share a cold refresh and bound the HTTP wait while it warms the cache."""
    def calculate():
        with app.app_context():
            if selected_date is None:
                return prediction_service.get_fixture_cards(league_id)
            return prediction_service.get_fixture_cards(league_id, selected_date=selected_date)

    flight_key = league_id if selected_date is None else (league_id, selected_date)
    with _SOCCER_CARD_LOCK:
        future = _SOCCER_CARD_FLIGHTS.get(flight_key)
        if future is None:
            future = _SOCCER_CARD_POOL.submit(calculate)
            _SOCCER_CARD_FLIGHTS[flight_key] = future
    try:
        result = future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        warning = "Soccer analysis is still refreshing. Please try again shortly."
        return [], [], warning, "degraded", {"status": "degraded", "observed_at": None, "warning": warning}
    finally:
        if future.done():
            with _SOCCER_CARD_LOCK:
                if _SOCCER_CARD_FLIGHTS.get(flight_key) is future:
                    _SOCCER_CARD_FLIGHTS.pop(flight_key, None)
    return result


@app.route("/api/dashboard/soccer", methods=["GET"])
@limiter.limit("30 per minute")
def api_dashboard_soccer():
    try:
        league_id = _set_active_league(_active_league_id())
        date_filter = (request.args.get("date") or "").strip()
        if date_filter:
            try:
                datetime.strptime(date_filter, "%Y-%m-%d")
                if len(date_filter) != 10:
                    raise ValueError
            except ValueError:
                return jsonify(error="Use a calendar date in YYYY-MM-DD format."), 400

        cards, fixtures, load_error, _, _ = _bounded_soccer_cards(league_id, selected_date=date_filter)
        cards = [sanitize_decision(card) for card in cards]
        for c in cards:
            c.setdefault("league_id", league_id)
            c.setdefault("sport", "soccer")

        # Group all cards by calendar date for multi-day view
        from collections import defaultdict as _dd
        by_date: dict[str, list] = _dd(list)
        # Schedule metadata supplies tabs for all dates without analyzing them.
        for fixture in fixtures or []:
            fixture_date = calendar_date((fixture.get("fixture") or {}).get("date"))
            if fixture_date:
                by_date[fixture_date]
        # Keep navigation dates from already observed league schedules without
        # waiting for their unrelated analyses on a single-day request.
        for fixture in list(_FIXTURE_INDEX.values()):
            if (fixture.get("league") or {}).get("id") == league_id:
                fixture_date = calendar_date((fixture.get("fixture") or {}).get("date"))
                if fixture_date:
                    by_date[fixture_date]
        for c in cards:
            d = calendar_date(c.get("game_date"))
            if d:
                by_date[d].append(c)

        available_dates = sorted(by_date.keys())

        # The dated loader already fetches this date inside the bounded worker.
        # Never start a second synchronous provider/model pass after that wait.
        # While it warms, retain only schedule evidence we have actually observed.
        if date_filter and not by_date.get(date_filter):
            extra_cards = []
            for f in list(_FIXTURE_INDEX.values()):
                info = f.get("fixture") or {}
                if ((f.get("league") or {}).get("id") != league_id
                        or calendar_date(info.get("date")) != date_filter):
                    continue
                home, away = (f.get("teams") or {}).get("home", {}), (f.get("teams") or {}).get("away", {})
                extra_cards.append(sanitize_decision({
                    "match_id": str(info.get("id")), "team_a": home.get("name"), "team_b": away.get("name"),
                    "team_a_id": home.get("id"), "team_b_id": away.get("id"),
                    "game_date": info.get("date"), "event_status": info.get("status"),
                    "league_id": league_id, "sport": "soccer", "data_freshness": f.get("data_freshness") or {},
                    "action": "SKIP", "recommended_side": "Unavailable", "confidence": 0,
                    "reason": "Schedule observed; match analysis is not yet available."}))
            if extra_cards:
                by_date[date_filter] = extra_cards
                available_dates = sorted(by_date.keys())

        # When a specific date is requested, filter to that day only.
        # If the requested date has no games, fall back to the nearest available date.
        if date_filter and date_filter in by_date:
            visible = by_date[date_filter]
        elif date_filter:
            visible = []
        elif available_dates:
            visible = by_date[available_dates[0]]
            date_filter = available_dates[0]
        else:
            visible = cards  # No date info at all → return all

        visible = [dict(sanitize_decision(card), slate_date=date_filter) for card in visible]
        refresh_pending = bool(load_error and "still refreshing" in load_error)
        top = dui.top_opportunities([card for card in visible if card.get("action") != "SKIP"], limit=4)
        plan = dui.plan_summary(visible)

        return jsonify({
            "slate": decision_store.publish(visible, "soccer"),
            "topOpportunities": decision_store.publish(top, "soccer"),
            "plan": {"bet": plan.get("bet", 0), "consider": plan.get("consider", 0), "skip": plan.get("skip", 0)},
            "availableDates": available_dates,
            "selectedDate": date_filter,
            "error": None if refresh_pending and visible else load_error,
            "refresh_pending": refresh_pending,
            "last_updated": from_records(visible).get("observed_at"),
            "calendarTimezone": "America/New_York",
        }), 503 if load_error and not cards and not (refresh_pending and visible) else 200
    except Exception as exc:
        app.logger.warning("api_dashboard_soccer error: %s", exc)
        return jsonify({"slate": [], "topOpportunities": [], "plan": {"bet": 0, "consider": 0, "skip": 0}, "availableDates": [], "selectedDate": "", "error": "Soccer data is temporarily unavailable."}), 503



@app.route("/api/dashboard/insights", methods=["GET"])
@limiter.limit("30 per minute")
def api_dashboard_insights():
    sport = (request.args.get("sport") or "all").lower()
    if sport not in {"all", "soccer", "nba", "nfl"}:
        return jsonify({"error": "Invalid sport"}), 400
    decisions, errors = [], []
    if sport in {"all", "soccer"}:
        # This request has already passed its own public rate limit. Reusing a
        # view internally must not count the same request again for each sport.
        response = app.make_response(unwrap(api_dashboard_soccer)())
        payload = response.get_json() or {}
        decisions.extend(payload.get("slate") or [])
        if payload.get("error"):
            errors.append("Soccer data is temporarily unavailable.")
    if sport in {"all", "nba"}:
        import nba_routes
        response = app.make_response(unwrap(nba_routes.api_dashboard_nba)())
        payload = response.get_json() or {}
        decisions.extend(payload.get("slate") or [])
        if payload.get("error"):
            errors.append("NBA data is temporarily unavailable.")
    if sport in {"all", "nfl"}:
        import nfl_routes
        response = app.make_response(unwrap(nfl_routes.api_dashboard_nfl)())
        payload = response.get_json() or {}
        decisions.extend(payload.get("slate") or [])
        if payload.get("error"):
            errors.append("NFL data is temporarily unavailable.")
    decisions = [sanitize_decision(item) for item in decisions]
    groups = {name: sorted([item for item in decisions if item.get("sport") == name], key=lambda item: (dui.ACTION_ORDER.get(item["action"], 3), -item["confidence"])) for name in ("soccer", "nba", "nfl")}
    rule_cards = groups["soccer"] + groups["nba"]
    plan = {action.lower(): sum(item["action"] == action for item in decisions) for action in ("BET", "CONSIDER", "SKIP")}
    volatility = [item for item in rule_cards if item["action"] != "SKIP" and item["data"] != "Strong Data"][:4]
    return jsonify({
        "radarCards": rule_cards[:6],
        "slate": decisions,
        "radarGroups": {name: cards[:4] for name, cards in groups.items()},
        "scenarioCards": groups["nfl"][:4],
        "volatilityRows": [{"side": item["side"], "matchup": item["matchup"], "action": item["action"], "confidence": item["confidence"], "note": item["reason"]} for item in volatility],
        "confidenceGroups": {"Favourite at 66%+": sum(item["confidence"] >= 66 for item in rule_cards),
                             "Favourite at 55-65%": sum(55 <= item["confidence"] < 66 for item in rule_cards),
                             "Favourite below 55%": sum(item["confidence"] < 55 for item in rule_cards)},
        "plan": plan, "sportFilter": sport, "warnings": errors, "last_updated": _now_stamp(),
    }), 503 if errors and not decisions else 200


@app.route("/api/performance/track-record", methods=["GET"])
@limiter.limit("60 per minute")
def api_track_record():
    from services import track_record
    try:
        return jsonify(track_record.summary())
    except Exception:
        _logger.warning("Track record unavailable", exc_info=True)
        return jsonify({"error": "The track record is temporarily unavailable."}), 503


@app.route("/api/performance/results", methods=["GET"])
@limiter.limit("60 per minute")
def api_recent_results():
    from services import track_record
    sport = (request.args.get("sport") or "").lower()
    if sport not in {"soccer", "nba"}:
        return jsonify({"error": "Results are tracked for soccer and NBA."}), 400
    try:
        return jsonify(track_record.results(sport))
    except Exception:
        _logger.warning("Recent results unavailable", exc_info=True)
        return jsonify({"error": "Recent results are temporarily unavailable."}), 503


@app.route("/api/matches/<match_id>")
@limiter.limit("60 per minute")
def api_match_decision(match_id):
    sport = (request.args.get("sport") or "soccer").lower()
    if sport not in {"soccer", "nba", "nfl"}:
        return jsonify({"error": "Invalid sport"}), 400
    # NFL pregame estimates must expire at kickoff, even inside the card TTL.
    refresh_pending = False
    decision = None if sport == "nfl" else decision_store.get(sport, match_id)
    if decision is None and sport == "nba":
        from services import nba_matchup_context
        archived = nba_matchup_context.archived_game(match_id)
        if archived:
            decision = nba_matchup_context.schedule_decision(archived)
    if decision is None:
        if sport == "soccer":
            response = app.make_response(api_dashboard_soccer())
        elif sport == "nfl":
            import nfl_routes
            response = app.make_response(nfl_routes.api_dashboard_nfl())
        else:
            import nba_routes
            response = app.make_response(nba_routes.api_dashboard_nba())
        if response.status_code >= 500:
            if sport in {"nba", "soccer"}:
                # Retain verified team/schedule identities for descriptive
                # analysis during an outage. Sanitization still removes every
                # expired probability and recommendation.
                cached = decision_store.get(sport, match_id, allow_stale=True)
                if cached is not None:
                    return jsonify({"decision": sanitize_decision(cached),
                                    "warnings": ["Schedule refresh failed; showing last observed matchup details without a prediction."]})
            return jsonify({"error": "Match data is temporarily unavailable."}), 503
        if response.status_code >= 400:
            return response
        slate_payload = response.get_json() or {}
        refresh_pending = bool(slate_payload.get("refresh_pending"))
        decision = next((item for item in slate_payload.get("slate", []) if item.get("matchId") == match_id), None)
    if decision is None:
        if sport == "nba":
            from services import nba_matchup_context
            try:
                game = nba_matchup_context.game_for_id(match_id, request.args.get("date"))
                if game:
                    decision = nba_matchup_context.schedule_decision(game)
            except (ValueError, RuntimeError, OSError):
                pass
    if decision is None:
        return jsonify({"error": "This match is no longer available. Choose a match from the current slate."}), 404
    return jsonify({"decision": sanitize_decision(decision), "refresh_pending": refresh_pending})


@app.get("/api/soccer/matches/<match_id>/injuries")
@limiter.limit("30 per minute")
def api_soccer_injuries(match_id):
    """Fantasy Premier League availability priced as win-chance changes for one fixture."""
    from services import injury_impact, injury_reports
    decision = decision_store.get("soccer", match_id)
    if decision is None:
        response = app.make_response(api_dashboard_soccer())
        if response.status_code >= 400:
            return jsonify({"error": "Match data is temporarily unavailable."}), 503
        decision = next((item for item in (response.get_json() or {}).get("slate", []) if item.get("matchId") == match_id), None)
    if decision is None:
        return jsonify({"error": "This match is no longer available. Choose a match from the current slate."}), 404
    decision = sanitize_decision(decision)
    if not decision.get("pregameAvailable"):
        return jsonify({"error": "Injury impact is available before kickoff only."}), 422
    if str(decision.get("leagueId")) != "39":
        return jsonify({"error": "Player availability is covered for the Premier League only (Fantasy Premier League). "
                                 "Other leagues need an API-Football key."}), 422
    home, draw = decision.get("homeProbability"), decision.get("drawProbability")
    if home is None or draw is None:
        return jsonify({"error": "This fixture has no win probabilities to adjust."}), 422
    try:
        payload, freshness = injury_reports.fpl()
        players = injury_impact.soccer_players(payload, decision["teamA"], decision["teamB"])
    except injury_reports.InjuryFeedUnavailable:
        return jsonify({"error": "Fantasy Premier League availability is temporarily unavailable."}), 503
    except injury_impact.ImpactUnavailable as exc:
        return jsonify({"error": str(exc)}), 422
    report = injury_impact.goals_report(home / 100, draw / 100, max(0.0, 1 - (home + draw) / 100), players)
    result = injury_impact.summarize(report, {"home": decision["teamA"], "away": decision["teamB"]}, unit="goals")
    result.update(sources=[freshness], warnings=[], base="match forecast",
                  notice="Win chances start from this fixture's forecast and are adjusted for who is expected to play, "
                         "valued from expected goals and assists. The forecast itself does not use injuries: in a backtest on "
                         "1,140 Premier League matches, adjusting for the actual lineups did not measurably improve it, so treat "
                         "these numbers as a guide to who matters, not as a better forecast.")
    return jsonify(result=result)


@app.get("/api/soccer/matches/<match_id>/props")
@limiter.limit("60 per minute")
def api_soccer_props(match_id):
    """Five best player prop picks for a fixture, from ESPN box scores (any league ESPN covers).

    Premier League fixtures also skip players Fantasy Premier League lists as unavailable and
    add its tackles market, which ESPN box scores lack.
    """
    decision = decision_store.get("soccer", match_id)
    if decision is None:
        response = app.make_response(api_dashboard_soccer())
        if response.status_code >= 400:
            return jsonify({"error": "Match data is temporarily unavailable."}), 503
        decision = next((item for item in (response.get_json() or {}).get("slate", []) if item.get("matchId") == match_id), None)
    if decision is None:
        return jsonify({"error": "This match is no longer available. Choose a match from the current slate."}), 404
    if app.config.get("TESTING"):
        payload, status = soccer_props_payload(decision)
    else:
        from services import soccer_props_loader
        def calculate():
            with app.app_context():
                return soccer_props_payload(decision)
        key = (match_id, decision.get("leagueId"), decision.get("matchDate"))
        payload, status = soccer_props_loader.load(key, calculate)
    return jsonify(payload), status


def soccer_props_payload(decision: dict) -> tuple[dict, int]:
    """Five best player prop picks for one slate decision: ({"result": ...} or {"error": ...}, HTTP status)."""
    from services import fpl_props, injury_impact, injury_reports, soccer_props
    from utils.parsing import normalize_team_name
    import requests
    from services.injury_reports import normalize_name
    decision = sanitize_decision(decision)
    kickoff = parse_kickoff(decision.get("matchDate"))
    if not decision.get("pregameAvailable") or kickoff is None:
        return {"error": "Player props are available before kickoff only."}, 422
    home_id, away_id = str(decision.get("homeId") or ""), str(decision.get("awayId") or "")
    if not home_id or not away_id:
        return {"error": "This fixture is missing team identities."}, 422
    premier_league = str(decision.get("leagueId")) == "39"
    excluded, extra, fpl_candidates = frozenset(), [], []
    if premier_league:
        try:
            payload, _freshness = injury_reports.fpl()
            excluded = frozenset(normalize_name(f"{p.get('first_name', '')} {p.get('second_name', '')}")
                                 for p in payload["elements"] if p.get("status") != "a")
            if freshness_state(_freshness) == "fresh":
                fpl_result = fpl_props.project(payload, decision["teamA"], decision["teamB"], kickoff)
                extra, fpl_candidates = fpl_result["picks"], fpl_result.get("candidates", [])
        except (injury_reports.InjuryFeedUnavailable, injury_impact.ImpactUnavailable):
            pass
    try:
        soccer_props.slug_for(decision.get("leagueId") or 39)
        home_id, away_id = soccer_props.resolve_team_ids(decision.get("leagueId") or 39,
                                                       decision["teamA"], decision["teamB"])
        result = soccer_props.project(decision.get("leagueId") or 39, home_id, away_id, kickoff, excluded)
    except ValueError as exc:
        return {"error": str(exc)}, 422
    except (RuntimeError, requests.RequestException, injury_reports.InjuryFeedUnavailable):
        return {"error": "Current rosters or verified player props are temporarily unavailable. Refresh and try again."}, 503
    if not sanitize_decision(decision).get("pregameAvailable"):
        return {"error": "Player props are available before kickoff only."}, 422
    names = {home_id: decision["teamA"], away_id: decision["teamB"]}
    picks = [{**pick, "team": names.get(pick["team"], pick["team"])} for pick in result["picks"]]
    verified_extra = []
    for pick in extra:
        team_id = home_id if normalize_team_name(pick.get("team")) == normalize_team_name(decision["teamA"]) else (
            away_id if normalize_team_name(pick.get("team")) == normalize_team_name(decision["teamB"]) else None)
        matches = [player for player in result.get("roster_players", {}).get(team_id, [])
                   if sorted(normalize_name(player["full_name"]).split()) == sorted(normalize_name(pick.get("full_name")).split())]
        if len(matches) == 1:
            verified_extra.append({**pick, "fpl_player_id": pick.get("player_id"),
                                   "player_id": matches[0]["id"], "team": names[team_id]})
    existing_ids = {str(pick["player_id"]) for pick in picks if pick.get("player_id")}
    picks += [pick for pick in verified_extra if str(pick["player_id"]) not in existing_ids]
    roster_verification = result.get("roster_verification")
    if verified_extra and roster_verification:
        roster_verification = {**roster_verification, **worst([roster_verification, _freshness]),
                               "status": "verified", "sources": [roster_verification, _freshness]}
    picks.sort(key=lambda pick: -pick["edge"])
    from services import prop_markets
    comparison_input = {"picks": picks[:5], "roster_verification": roster_verification}
    if "players" in result:
        for candidate in fpl_candidates:
            team_id = home_id if candidate.get("match_side") == "home" else away_id if candidate.get("match_side") == "away" else None
            identities = [p for p in result.get("roster_players", {}).get(team_id, [])
                          if sorted(normalize_name(p["full_name"]).split()) == sorted(normalize_name(candidate.get("full_name")).split())]
            trained = [m for m in candidate["markets"] if m["market"] == "tackles" and m.get("model") == "soccer-player-counts-v2"]
            if len(identities) != 1 or not trained: continue
            existing = next((p for p in result["players"] if str(p["id"]) == identities[0]["id"]), None)
            if existing:
                existing["markets"].extend(trained)
            else:
                result["players"].append({"id": identities[0]["id"], "name": identities[0]["name"],
                    "team": team_id, "position": candidate["position"], "markets": trained})
            comparison_input["roster_verification"] = {**roster_verification, **worst([roster_verification, _freshness]), "status": "verified"}
        comparison_input.update(selection_policy="verified_market_only", players=[
            {**player, "team": names.get(player["team"], player["team"])} for player in result["players"]])
    result = prop_markets.compare(comparison_input, "soccer", decision["teamA"], decision["teamB"],
                                  kickoff, decision.get("leagueId"))
    if not sanitize_decision(decision).get("pregameAvailable"):
        return {"error": "Player props are available before kickoff only."}, 422
    return {"result": result}, 200


def _dashboard_slate(endpoint: str, path: str, *, attempts: int = 4) -> tuple[str | None, list[dict]]:
    """(selected date, decisions) from a sport dashboard, called in-process as the props route does.

    A cold soccer refresh answers "still refreshing" with an empty slate after 20 seconds; the
    refresh keeps running, so ask again rather than publish a board without that league.
    Views are unwrapped so these internal calls never spend the public rate limit, which a
    local browser shares (as the Insights dashboard does).
    """
    for attempt in range(attempts):
        with app.test_request_context(path):
            response = app.make_response(unwrap(app.view_functions[endpoint])())
        if response.status_code >= 400:
            return None, []
        payload = response.get_json() or {}
        refreshing = "still refreshing" in str(payload.get("error") or "").lower()
        if not refreshing or attempt == attempts - 1:
            return payload.get("selectedDate"), payload.get("slate") or []
        time.sleep(5)
    return None, []


def _best_props_slates(day: str) -> dict[str, tuple[str | None, list[dict]]]:
    soccer = []
    for league_id in SUPPORTED_LEAGUE_IDS:
        _selected, decisions = _dashboard_slate("api_dashboard_soccer", f"/api/dashboard/soccer?league={league_id}&date={day}")
        soccer += [d for d in decisions if calendar_date(d.get("matchDate")) == day]
    _selected, nba = _dashboard_slate("nba_api.api_dashboard_nba", f"/api/dashboard/nba?date={day}")
    nba = [d for d in nba if calendar_date(d.get("matchDate")) == day]
    nfl_day, nfl = _dashboard_slate("nfl_api.api_dashboard_nfl", f"/api/dashboard/nfl?date={day}")
    if not nfl:  # No NFL games that day: show the next NFL slate, labelled with its own date.
        nfl_day, nfl = _dashboard_slate("nfl_api.api_dashboard_nfl", "/api/dashboard/nfl")
    return {"soccer": (day, soccer), "nba": (day, nba), "nfl": (nfl_day, nfl)}


def _fresh_soccer_decision(decision: dict, slate_date: str | None) -> dict:
    """Soccer decisions carry a minutes-long freshness window; re-read the slate before using one."""
    league_id = decision.get("leagueId") or DEFAULT_LEAGUE_ID
    _selected, slate = _dashboard_slate("api_dashboard_soccer", f"/api/dashboard/soccer?league={league_id}&date={slate_date or ''}")
    return next((d for d in slate if d.get("matchId") == decision.get("matchId")), decision)


def _best_props_for(sport: str, decision: dict, slate_date: str | None) -> tuple[dict, int]:
    import nba_routes as nba_module
    import nfl_routes as nfl_module
    with app.app_context():
        if sport == "soccer":
            payload, status = soccer_props_payload(_fresh_soccer_decision(decision, slate_date))
            kickoff = parse_kickoff(decision.get("matchDate"))
            if status == 422 and kickoff and kickoff > datetime.now(timezone.utc):
                # The first call downloads box scores; a stale-by-then decision gets one retry, now fast.
                payload, status = soccer_props_payload(_fresh_soccer_decision(decision, slate_date))
            return payload, status
        if sport == "nba":
            return nba_module.nba_props_payload(str(decision["matchId"]), slate_date)
        return nfl_module.nfl_props_payload(str(decision["matchId"]), date.fromisoformat(slate_date) if slate_date else None)


def build_best_props(day: str) -> dict:
    from services import best_props
    return best_props.build(_best_props_slates(day), _best_props_for)


@app.route("/api/props/email", methods=["GET", "POST"])
@limiter.limit("10 per minute")
@user_auth.api_login_required
def api_props_email():
    from services import daily_digest, props_email
    owner = g.current_user
    if request.method == "GET":
        job_id = request.args.get("id")
        if not job_id and request.args.get("date") and request.args.get("sports"):
            job_id = props_email.identity(owner["email"].lower(), request.args["date"], request.args["sports"].split(","))
        job = props_email.read(job_id, owner["email"].lower()) if job_id else None
        if request.args.get("id") and job is None:
            return jsonify(error="Email request not found."), 404
        return jsonify(ready=props_email.ready(), recipient=owner["email"], job=job)
    payload = request.get_json(silent=True) or {}
    day = payload.get("date") or calendar_date(datetime.now(timezone.utc))
    sports = payload.get("sports", list(props_email.SPORTS))
    try:
        parsed = date.fromisoformat(day)
        today = date.fromisoformat(calendar_date(datetime.now(timezone.utc)))
        if parsed.isoformat() != day or not 0 <= (parsed - today).days <= 7:
            raise ValueError
        if not isinstance(sports, list) or not sports or any(s not in props_email.SPORTS for s in sports):
            raise ValueError
    except (ValueError, TypeError):
        return jsonify(error="Choose a date within the next seven days and valid sports."), 400
    # Recipient always comes from the signed-in account, never the request.
    api_get, props_for = daily_digest.flask_readers(app)
    job = props_email.start(owner["email"].lower(), owner["email"], day, sports,
                            lambda: props_email.collect(day, sports, api_get, props_for))
    return jsonify(job=job), (503 if job["state"] in {"blocked", "busy"} else 202 if job["state"] == "collecting" else 200)


@app.get("/api/props/best")
@limiter.limit("10 per minute")
def api_best_props():
    """Best player props of the day: one ranked board across soccer and NBA, NFL listed separately."""
    from services import best_props
    day = (request.args.get("date") or "").strip() or calendar_date(datetime.now(timezone.utc))
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except ValueError:
        return jsonify(error="Use a calendar date in YYYY-MM-DD format."), 400
    try:
        board = best_props.cached(day, lambda: build_best_props(day))
    except Exception:
        app.logger.exception("Best props board failed")
        return jsonify(error="Player props are temporarily unavailable."), 503
    return jsonify(result=best_props.without_started(board) | {"date": day})


@app.route("/api/scoreline/soccer", methods=["GET", "POST"])
@limiter.limit("30 per minute")
def api_scoreline_soccer():
    """
    Scoreline probability distribution for a soccer match.

    Query / body params:
        team_a_id   int   Home team provider ID
        team_b_id   int   Away team provider ID
        league_id   int   (optional) League ID – defaults to active league
        team_a_name str   (optional) Override display name
        team_b_name str   (optional) Override display name
        odds_total  float (optional) Market total goals line for anchoring
        ou_lines    csv   (optional) Comma-separated O/U lines e.g. "1.5,2.5,3.5"
    """
    from services import scoring_research
    from services.scoreline_inputs import ScorelineInputError, validate
    try:
        params = request.get_json(silent=True) or request.form or request.args
        validated = validate(params, "soccer")
        team_a_id = _safe_int(params.get("team_a_id") or params.get("home_id"))
        team_b_id = _safe_int(params.get("team_b_id") or params.get("away_id"))
        league_id = _coerce_league_id(params.get("league_id") or _active_league_id())
        team_a_name = str(params.get("team_a_name") or params.get("team_a") or "Home").strip()
        team_b_name = str(params.get("team_b_name") or params.get("team_b") or "Away").strip()
        odds_total, ou_lines = validated["odds_total"], validated["ou_lines"]
        fixture = None
        event_id = str(params.get("event_id") or params.get("match_id") or "").strip()
        if event_id:
            fixture = _FIXTURE_INDEX.get(event_id)
            if not fixture or (fixture.get("data_freshness") and freshness_state(fixture["data_freshness"]) != "fresh"):
                day = calendar_date(((fixture or {}).get("fixture") or {}).get("date") or validated["before"])
                loaded = load_fixtures_cached(league_id, selected_date=day or None)
                fixture = next((row for row in loaded[0] or [] if str((row.get("fixture") or {}).get("id")) == event_id), None)
            if not fixture:
                return jsonify(status="error", message="The scheduled match could not be verified. Refresh the slate."), 404
            info = fixture.get("fixture") or {}
            teams = fixture.get("teams") or {}
            if str((teams.get("home") or {}).get("id")) != str(team_a_id) or str((teams.get("away") or {}).get("id")) != str(team_b_id):
                return jsonify(status="error", message="The selected teams do not match this fixture."), 400
            if not is_pregame(info.get("date"), info.get("status")):
                return jsonify(status="error", message="Pregame scoring predictions are unavailable after kickoff or a status change."), 422
            if fixture.get("data_freshness") and freshness_state(fixture["data_freshness"]) != "fresh":
                return jsonify(status="error", message="The schedule needs a fresh provider update before scoring."), 422
            validated["before"] = info.get("date")
        scoring_season = _safe_int((fixture or {}).get("league", {}).get("season"), SEASON)
        input_freshness = (fixture or {}).get("data_freshness") or {}

        form_a: list[dict] = []
        form_b: list[dict] = []
        h2h: list[dict] = []
        injuries_a = None
        injuries_b = None
        ml_prob_a: float | None = None
        ml_prob_b: float | None = None
        opp_strength_a: float | None = None
        opp_strength_b: float | None = None
        _sl_key: str = ""

        if team_a_id and team_b_id:
            # Check scoreline cache first
            _sl_key = cache_service.make_key("scoreline_v3", event_id, team_a_id, team_b_id, league_id, scoring_season, odds_total, tuple(ou_lines or []), team_a_name, team_b_name, validated["before"])
            _cached_sl = _local_scoreline_cache.get(_sl_key)
            if _cached_sl is not None and (not _cached_sl.get("data_freshness") or freshness_state(_cached_sl["data_freshness"]) == "fresh"):
                return jsonify({"status": "ok", "result": _cached_sl})

            # Fetch all 6 data sources in parallel; each future is resolved
            # independently so an injury/ESPN failure doesn't kill form data.
            def _safe_fut(fut, default=None):
                try:
                    return fut.result()
                except Exception:
                    return default

            try:
                with ThreadPoolExecutor(max_workers=6) as _pool:
                    _fut_a        = _pool.submit(ac.get_team_fixtures, team_a_id, league_id, scoring_season, 10)
                    _fut_b        = _pool.submit(ac.get_team_fixtures, team_b_id, league_id, scoring_season, 10)
                    _fut_h2h      = _pool.submit(ac.get_h2h, team_a_id, team_b_id, 10)
                    _fut_inj_a    = _pool.submit(ac.get_injuries, league_id, SEASON, team_a_id)
                    _fut_inj_b    = _pool.submit(ac.get_injuries, league_id, SEASON, team_b_id)
                    _fut_standings= _pool.submit(ac.get_standings, league_id, SEASON)
                    raw_a      = _safe_fut(_fut_a, [])
                    raw_b      = _safe_fut(_fut_b, [])
                    raw_h2h    = _safe_fut(_fut_h2h, [])
                    raw_inj_a  = _safe_fut(_fut_inj_a)
                    raw_inj_b  = _safe_fut(_fut_inj_b)
                    standings  = _safe_fut(_fut_standings, [])
                input_freshness = worst([input_freshness, from_records(raw_a), from_records(raw_b)])
                if input_freshness and freshness_state(input_freshness) != "fresh":
                    return jsonify(status="error", message="Recent form needs a fresh provider update before scoring."), 422
                form_a = scoring_research.soccer_scoring_form(raw_a, team_a_id, season=scoring_season, before=validated["before"])
                form_b = scoring_research.soccer_scoring_form(raw_b, team_b_id, season=scoring_season, before=validated["before"])
                h2h = pred.extract_form(
                    pred.filter_recent_completed_fixtures(raw_h2h, current_season=SEASON, seasons_back=5),
                    team_a_id,
                )[:6]
                injuries_a = _clean_injuries(raw_inj_a) if raw_inj_a is not None else None
                injuries_b = _clean_injuries(raw_inj_b) if raw_inj_b is not None else None
            except Exception:
                app.logger.warning("scoreline_soccer: data fetch failed", exc_info=True)
                standings = []

            # ML win probabilities from scorpred_engine
            try:
                opp_str = _build_opp_strengths(standings)
                # Extract individual opponent strengths
                opp_strength_b = opp_str.get(pred._norm(team_a_name), 5.0)  # team A faces team B
                opp_strength_a = opp_str.get(pred._norm(team_b_name), 5.0)  # team B faces team A
                ml_pred = se.scorpred_predict(
                    form_a=form_a, form_b=form_b,
                    h2h_form_a=h2h, h2h_form_b=h2h,
                    injuries_a=injuries_a, injuries_b=injuries_b,
                    team_a_is_home=True,
                    team_a_name=team_a_name, team_b_name=team_b_name,
                    sport="soccer", opp_strengths=opp_str,
                )
                wps = ml_pred.get("win_probabilities") or {}
                ml_prob_a = _safe_float(wps.get("a")) or None
                ml_prob_b = _safe_float(wps.get("b")) or None
            except Exception:
                app.logger.debug("scoreline_soccer: ML blend skipped", exc_info=True)

        features = scoring_research.rolling_features(form_a, form_b, sport="soccer")
        from services import soccer_model
        try:
            projection = soccer_model.scoreline_projection(soccer_model.predict(ac, league_id, team_a_name, team_b_name))
        except Exception:
            app.logger.warning("scoreline_soccer: ratings projection failed", exc_info=True)
            projection = None
        if projection is None:
            projection = scoring_research.project("soccer", features, before=validated["before"]) if league_id == 39 else {
                "available": False, "reason": "The current trained soccer scoring model covers the Premier League only."}
        result = _scoreline_engine.predict_soccer_scoreline(
            form_a=form_a, form_b=form_b,
            h2h=h2h, injuries_a=injuries_a, injuries_b=injuries_b,
            is_home_a=True,
            ml_prob_a=ml_prob_a, ml_prob_b=ml_prob_b,
            odds_total=odds_total,
            team_a_name=team_a_name, team_b_name=team_b_name,
            ou_lines=ou_lines,
            opp_strength_a=opp_strength_a,
            opp_strength_b=opp_strength_b,
            research_projection=projection,
        )
        result["data_freshness"] = input_freshness
        if not is_pregame(validated["before"]) and validated["before"]:
            return jsonify(status="error", message="Pregame scoring predictions are unavailable after kickoff."), 422
        if input_freshness and freshness_state(input_freshness) != "fresh":
            return jsonify(status="error", message="Scoring inputs expired during calculation. Refresh the match."), 422
        if team_a_id and team_b_id:
            _local_scoreline_cache[_sl_key] = result
        return jsonify({"status": "ok", "result": result})
    except ScorelineInputError as exc:
        return jsonify(status="error", message=str(exc)), 400
    except (ValueError, _scoreline_engine.InsufficientScoreData) as exc:
        return jsonify(status="error", message=str(exc)), 422
    except Exception as exc:
        app.logger.error("api_scoreline_soccer error: %s", exc, exc_info=True)
        return jsonify({"status": "error", "message": sanitize_error(exc)}), 500


@app.errorhandler(404)
def not_found(_):
    # SPA deep-link: serve index.html so React Router handles the path
    if app.config.get("SERVE_SPA", True) and _SPA_INDEX.exists() and request.path in _SPA_PAGE_PATHS:
        return send_from_directory(_SPA_DIST, "index.html")
    return _error_response(404, "Page not found.")


@app.errorhandler(500)
def server_error(e):
    app.logger.error("Internal server error: %s", e)
    return _error_response(500, "An internal error occurred. Please try again.")


@app.errorhandler(429)
def rate_limit_error(error):
    if _is_api_request():
        response = error.get_response()
        response.set_data(app.json.dumps({"error": "Too many requests. Please try again shortly."}))
        response.content_type = "application/json"
        return response
    return error


if __name__ == "__main__":
    debug = False
    use_reloader = False
    port = int(os.getenv("PORT", "5000"))
    start_background_services()
    try:
        app.run(debug=debug, use_reloader=use_reloader, port=port)
    except OSError as e:
        if "address already in use" in str(e).lower() or "10048" in str(e):
            app.logger.error(
                "Port %s is already in use. Stop the running process or set PORT to a different value.",
                port,
            )
        else:
            raise
    finally:
        stop_background_services()
