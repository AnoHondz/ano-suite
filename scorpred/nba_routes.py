"""
nba_routes.py -- Flask Blueprint for the NBA prediction module.

Registered in app.py as:
    from nba_routes import nba_bp
    app.register_blueprint(nba_bp)

All routes live under /nba prefix.
Session keys use the nba_ prefix to avoid collisions with the football section.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import decision_ui as dui
import model_tracker as mt
import scorpred_engine as se
from extensions import limiter
from runtime_paths import cache_dir
from security import sanitize_error
from services import decision_store
from services import nba_pregame_model as pregame
from services import recalibration
from services.data_freshness import SOURCE_KEY, from_records, metadata, worst
from services.decision_engine import DecisionEngine
from services.prediction_lifecycle import (
    CALENDAR_ZONE,
    calendar_date,
    freshness_state,
    parse_kickoff,
    sanitize_decision,
    unavailable_reason,
)

nba_bp = Blueprint(
    "nba",
    __name__,
    url_prefix="/nba",
    template_folder="templates",
)

nba_api_bp = Blueprint("nba_api", __name__)


class _LazyModuleProxy:
    """Lazy-load heavyweight NBA dependencies on first real use."""

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
nc = _LazyModuleProxy("nba_live_client")
np_nba = _LazyModuleProxy("nba_predictor")
sm = _LazyModuleProxy("scormastermind")


# -- Helpers -----------------------------------------------------------------

_NBA_TEAM_ALIASES = {
    "atlanta hawks": "hawks",
    "hawks": "hawks",
    "atl": "hawks",
    "boston celtics": "celtics",
    "celtics": "celtics",
    "bos": "celtics",
    "brooklyn nets": "nets",
    "nets": "nets",
    "bkn": "nets",
    "charlotte hornets": "hornets",
    "hornets": "hornets",
    "cha": "hornets",
    "chicago bulls": "bulls",
    "bulls": "bulls",
    "chi": "bulls",
    "cleveland cavaliers": "cavaliers",
    "cavaliers": "cavaliers",
    "cavs": "cavaliers",
    "cle": "cavaliers",
    "dallas mavericks": "mavericks",
    "mavericks": "mavericks",
    "mavs": "mavericks",
    "dal": "mavericks",
    "denver nuggets": "nuggets",
    "nuggets": "nuggets",
    "den": "nuggets",
    "detroit pistons": "pistons",
    "pistons": "pistons",
    "det": "pistons",
    "golden state warriors": "warriors",
    "warriors": "warriors",
    "gs": "warriors",
    "gsw": "warriors",
    "houston rockets": "rockets",
    "rockets": "rockets",
    "hou": "rockets",
    "indiana pacers": "pacers",
    "pacers": "pacers",
    "ind": "pacers",
    "los angeles clippers": "clippers",
    "la clippers": "clippers",
    "clippers": "clippers",
    "lac": "clippers",
    "los angeles lakers": "lakers",
    "la lakers": "lakers",
    "lakers": "lakers",
    "lal": "lakers",
    "memphis grizzlies": "grizzlies",
    "grizzlies": "grizzlies",
    "mem": "grizzlies",
    "miami heat": "heat",
    "heat": "heat",
    "mia": "heat",
    "milwaukee bucks": "bucks",
    "bucks": "bucks",
    "mil": "bucks",
    "minnesota timberwolves": "timberwolves",
    "timberwolves": "timberwolves",
    "wolves": "timberwolves",
    "min": "timberwolves",
    "new orleans pelicans": "pelicans",
    "pelicans": "pelicans",
    "nop": "pelicans",
    "no": "pelicans",
    "new york knicks": "knicks",
    "knicks": "knicks",
    "ny": "knicks",
    "nyk": "knicks",
    "oklahoma city thunder": "thunder",
    "thunder": "thunder",
    "okc": "thunder",
    "orlando magic": "magic",
    "magic": "magic",
    "orl": "magic",
    "philadelphia 76ers": "76ers",
    "philadelphia sixers": "76ers",
    "76ers": "76ers",
    "sixers": "76ers",
    "phi": "76ers",
    "phoenix suns": "suns",
    "suns": "suns",
    "phx": "suns",
    "portland trail blazers": "trail blazers",
    "portland trailblazers": "trail blazers",
    "trail blazers": "trail blazers",
    "blazers": "trail blazers",
    "por": "trail blazers",
    "sacramento kings": "kings",
    "kings": "kings",
    "sac": "kings",
    "san antonio spurs": "spurs",
    "spurs": "spurs",
    "sa": "spurs",
    "sas": "spurs",
    "toronto raptors": "raptors",
    "raptors": "raptors",
    "tor": "raptors",
    "utah jazz": "jazz",
    "jazz": "jazz",
    "uta": "jazz",
    "washington wizards": "wizards",
    "wizards": "wizards",
    "was": "wizards",
}

_NBA_TEAM_METADATA = {
    "hawks": {"name": "Atlanta Hawks", "city": "Atlanta", "nickname": "Hawks"},
    "celtics": {"name": "Boston Celtics", "city": "Boston", "nickname": "Celtics"},
    "nets": {"name": "Brooklyn Nets", "city": "Brooklyn", "nickname": "Nets"},
    "hornets": {"name": "Charlotte Hornets", "city": "Charlotte", "nickname": "Hornets"},
    "bulls": {"name": "Chicago Bulls", "city": "Chicago", "nickname": "Bulls"},
    "cavaliers": {"name": "Cleveland Cavaliers", "city": "Cleveland", "nickname": "Cavaliers"},
    "mavericks": {"name": "Dallas Mavericks", "city": "Dallas", "nickname": "Mavericks"},
    "nuggets": {"name": "Denver Nuggets", "city": "Denver", "nickname": "Nuggets"},
    "pistons": {"name": "Detroit Pistons", "city": "Detroit", "nickname": "Pistons"},
    "warriors": {"name": "Golden State Warriors", "city": "Golden State", "nickname": "Warriors"},
    "rockets": {"name": "Houston Rockets", "city": "Houston", "nickname": "Rockets"},
    "pacers": {"name": "Indiana Pacers", "city": "Indiana", "nickname": "Pacers"},
    "clippers": {"name": "Los Angeles Clippers", "city": "Los Angeles", "nickname": "Clippers"},
    "lakers": {"name": "Los Angeles Lakers", "city": "Los Angeles", "nickname": "Lakers"},
    "grizzlies": {"name": "Memphis Grizzlies", "city": "Memphis", "nickname": "Grizzlies"},
    "heat": {"name": "Miami Heat", "city": "Miami", "nickname": "Heat"},
    "bucks": {"name": "Milwaukee Bucks", "city": "Milwaukee", "nickname": "Bucks"},
    "timberwolves": {"name": "Minnesota Timberwolves", "city": "Minnesota", "nickname": "Timberwolves"},
    "pelicans": {"name": "New Orleans Pelicans", "city": "New Orleans", "nickname": "Pelicans"},
    "knicks": {"name": "New York Knicks", "city": "New York", "nickname": "Knicks"},
    "thunder": {"name": "Oklahoma City Thunder", "city": "Oklahoma City", "nickname": "Thunder"},
    "magic": {"name": "Orlando Magic", "city": "Orlando", "nickname": "Magic"},
    "76ers": {"name": "Philadelphia 76ers", "city": "Philadelphia", "nickname": "76ers"},
    "suns": {"name": "Phoenix Suns", "city": "Phoenix", "nickname": "Suns"},
    "trail blazers": {"name": "Portland Trail Blazers", "city": "Portland", "nickname": "Trail Blazers"},
    "kings": {"name": "Sacramento Kings", "city": "Sacramento", "nickname": "Kings"},
    "spurs": {"name": "San Antonio Spurs", "city": "San Antonio", "nickname": "Spurs"},
    "raptors": {"name": "Toronto Raptors", "city": "Toronto", "nickname": "Raptors"},
    "jazz": {"name": "Utah Jazz", "city": "Utah", "nickname": "Jazz"},
    "wizards": {"name": "Washington Wizards", "city": "Washington", "nickname": "Wizards"},
}

_NBA_ESPN_ABBR_BY_CANONICAL = {
    "hawks": "atl",
    "celtics": "bos",
    "nets": "bkn",
    "hornets": "cha",
    "bulls": "chi",
    "cavaliers": "cle",
    "mavericks": "dal",
    "nuggets": "den",
    "pistons": "det",
    "warriors": "gsw",
    "rockets": "hou",
    "pacers": "ind",
    "clippers": "lac",
    "lakers": "lal",
    "grizzlies": "mem",
    "heat": "mia",
    "bucks": "mil",
    "timberwolves": "min",
    "pelicans": "nop",
    "knicks": "nyk",
    "thunder": "okc",
    "magic": "orl",
    "76ers": "phi",
    "suns": "phx",
    "trail blazers": "por",
    "kings": "sac",
    "spurs": "sa",
    "raptors": "tor",
    "jazz": "uta",
    "wizards": "wsh",
}


def _normalize_nba_name(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[.\-_/]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _canonical_nba_name(value: str) -> str:
    normalized = _normalize_nba_name(value)
    if not normalized:
        return ""
    return _NBA_TEAM_ALIASES.get(normalized, normalized)


def _team_name_candidates(team: dict) -> list[str]:
    values = [
        team.get("name", ""),
        team.get("nickname", ""),
        team.get("shortName", ""),
        team.get("abbrev", ""),
        " ".join(part for part in [team.get("city", ""), team.get("nickname", "")] if part),
        " ".join(part for part in [team.get("city", ""), team.get("name", "")] if part),
    ]
    return [value for value in values if str(value or "").strip()]


def _match_reason(selected_names: list[str], team_index: dict[str, list[dict]]) -> str:
    if not selected_names:
        return "no selected team names provided"
    canonical_selected = [_canonical_nba_name(name) for name in selected_names if _canonical_nba_name(name)]
    if not canonical_selected:
        return "selected team names normalized to empty canonical values"
    missing = [name for name in canonical_selected if name not in team_index]
    if missing:
        return f"no canonical match for {', '.join(missing)}"
    return "canonical match was ambiguous or unavailable"


def _log_team_match_debug(stage: str, selected_home: str, selected_away: str, teams: list[dict], reason: str = "") -> None:
    available_names = [team.get("name", "") for team in teams]
    normalized_selected = {
        "home": _canonical_nba_name(selected_home),
        "away": _canonical_nba_name(selected_away),
    }
    normalized_teams = [
        {
            "id": str(team.get("id", "")),
            "name": team.get("name", ""),
            "canonical": sorted({_canonical_nba_name(candidate) for candidate in _team_name_candidates(team) if _canonical_nba_name(candidate)}),
        }
        for team in teams
    ]
    current_app.logger.info(
        "nba select_game trace stage=%s selected_home=%r selected_away=%r normalized_selected=%s available_team_names=%s normalized_team_names=%s failure_reason=%s",
        stage,
        selected_home,
        selected_away,
        normalized_selected,
        available_names,
        normalized_teams,
        reason or "resolved",
    )


def _selection_mismatch_notice(selected_home: str, selected_away: str, teams: list[dict], reason: str) -> str:
    return "The selected NBA game could not be matched to the current team list. Please refresh and try again."


def _resolve_nba_team_from_selection(raw_id: str, raw_name: str, teams: list[dict]) -> tuple[dict | None, str]:
    team_by_id = {str(team.get("id", "")).strip(): team for team in teams if str(team.get("id", "")).strip()}
    if raw_id and raw_id in team_by_id:
        return team_by_id[raw_id], "matched by provider id"

    selected_canonical = _canonical_nba_name(raw_name)
    if not selected_canonical:
        return None, "selected team name normalized to empty canonical value"

    canonical_index: dict[str, list[dict]] = {}
    for team in teams:
        canonical_names = {
            _canonical_nba_name(candidate)
            for candidate in _team_name_candidates(team)
            if _canonical_nba_name(candidate)
        }
        for canonical_name in canonical_names:
            canonical_index.setdefault(canonical_name, []).append(team)

    matches = canonical_index.get(selected_canonical, [])
    if len(matches) == 1:
        return matches[0], f"matched by canonical name {selected_canonical}"
    if len(matches) > 1:
        return matches[0], f"matched by canonical name {selected_canonical} with duplicates"
    return None, _match_reason([raw_name], canonical_index)


def _fallback_nba_team_from_selection(raw_id: str, raw_name: str, raw_logo: str) -> tuple[dict | None, str]:
    canonical = _canonical_nba_name(raw_name)
    if not raw_id or not canonical:
        return None, "payload fallback unavailable"

    metadata = _NBA_TEAM_METADATA.get(canonical, {})
    if not metadata:
        return None, f"payload fallback unavailable for canonical name {canonical}"

    display_name = metadata.get("name") or raw_name.strip() or canonical.title()
    nickname = metadata.get("nickname") or display_name.split()[-1]
    city = metadata.get("city") or display_name.removesuffix(f" {nickname}").strip()
    fallback_logo = raw_logo
    abbr = _NBA_ESPN_ABBR_BY_CANONICAL.get(canonical)
    if not fallback_logo and abbr:
        fallback_logo = f"https://a.espncdn.com/i/teamlogos/nba/500/scoreboard/{abbr}.png"
    return {
        "id": str(raw_id).strip(),
        "name": display_name,
        "nickname": nickname,
        "shortName": nickname,
        "city": city,
        "logo": fallback_logo or "",
        "abbrev": "",
    }, f"matched by selection payload canonical name {canonical}"

def _require_nba_teams():
    """Return (team_a, team_b) dicts from session, or (None, None) if not set."""
    if "nba_team_a_id" not in session:
        return None, None
    return (
        {
            "id":       session["nba_team_a_id"],
            "name":     session["nba_team_a_name"],
            "logo":     session["nba_team_a_logo"],
            "nickname": session.get("nba_team_a_nickname", ""),
            "city":     session.get("nba_team_a_city", ""),
        },
        {
            "id":       session["nba_team_b_id"],
            "name":     session["nba_team_b_name"],
            "logo":     session["nba_team_b_logo"],
            "nickname": session.get("nba_team_b_nickname", ""),
            "city":     session.get("nba_team_b_city", ""),
        },
    )


def _selected_nba_game():
    # Patch: Robust NBA matchup routing - prefer session, fallback to query args if present
    selected = session.get("nba_selected_game")
    if not selected:
        # Try to build from query args if present
        event_id = request.args.get("event_id")
        if event_id:
            selected = {
                "event_id": event_id,
                "date": request.args.get("event_date", "").strip(),
                "status": request.args.get("event_status", "").strip(),
                "venue_name": request.args.get("venue_name", "").strip(),
                "short_name": request.args.get("short_name", "").strip(),
                "home_name": request.args.get("team_a_name", "").strip(),
                "home_logo": request.args.get("team_a_logo", "").strip(),
                "away_name": request.args.get("team_b_name", "").strip(),
                "away_logo": request.args.get("team_b_logo", "").strip(),
                "sport": "nba",
                "league_name": "NBA",
            }
            session["nba_selected_game"] = selected
    return selected


def _selection_error_redirect(message: str):
    return redirect(url_for("nba.index", selection_error=message))


def _store_nba_teams(team_a: dict, team_b: dict) -> None:
    session["nba_team_a_id"] = str(team_a.get("id", ""))
    session["nba_team_a_name"] = team_a.get("name", "")
    session["nba_team_a_logo"] = team_a.get("logo", "")
    session["nba_team_a_nickname"] = team_a.get("nickname", "")
    session["nba_team_a_city"] = team_a.get("city", "")

    session["nba_team_b_id"] = str(team_b.get("id", ""))
    session["nba_team_b_name"] = team_b.get("name", "")
    session["nba_team_b_logo"] = team_b.get("logo", "")
    session["nba_team_b_nickname"] = team_b.get("nickname", "")
    session["nba_team_b_city"] = team_b.get("city", "")


def _store_selected_game_from_payload(payload) -> None:
    event_id = payload.get("event_id", "").strip()
    if not event_id:
        session.pop("nba_selected_game", None)
        return

    session["nba_selected_game"] = {
        "event_id": event_id,
        "date": payload.get("event_date", "").strip(),
        "status": payload.get("event_status", "").strip(),
        "venue_name": payload.get("venue_name", "").strip(),
        "short_name": payload.get("short_name", "").strip(),
        "home_name": payload.get("team_a_name", "").strip(),
        "home_logo": payload.get("team_a_logo", "").strip(),
        "away_name": payload.get("team_b_name", "").strip(),
        "away_logo": payload.get("team_b_logo", "").strip(),
        "sport": "nba",
        "league_name": "NBA",
    }


def _store_assistant_page_context(page_kind: str, payload: dict | None = None) -> None:
    compact = {"page_kind": page_kind, "captured_at": _now_stamp()}
    if payload:
        compact.update(payload)
    session["assistant_page_context"] = compact


def _assistant_pick_probability(prediction: dict, team_a_name: str, team_b_name: str) -> float | None:
    win_probs = prediction.get("win_probabilities") if isinstance(prediction.get("win_probabilities"), dict) else {}
    pick = str(((prediction.get("best_pick") or {}).get("prediction") or "")).strip().lower()
    if pick == str(team_a_name or "").strip().lower():
        value = win_probs.get("a")
    elif pick == str(team_b_name or "").strip().lower():
        value = win_probs.get("b")
    else:
        value = None
    try:
        return round(float(value), 1) if value is not None else None
    except (TypeError, ValueError):
        return None


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


def _assistant_totals_pick_display(prediction: dict) -> str | None:
    totals_leg = prediction.get("totals_leg") or {}
    if not totals_leg and prediction.get("totals_pick"):
        totals_leg = {"pick": prediction["totals_pick"], "line": prediction.get("totals_line"), "market": prediction.get("totals_market")}
    if not totals_leg:
        totals_leg = next((item for item in prediction.get("optional_picks") or [] if "over/under" in str(item.get("market") or "").lower() or "o/u" in str(item.get("market") or "").lower()), {})
    if not totals_leg:
        return None
    pick = str(totals_leg.get("pick") or "").strip()
    line = totals_leg.get("line")
    if pick and line is not None:
        return f"{pick} {line}"
    return str(totals_leg.get("market") or pick or "").strip() or None


def _assistant_prediction_context(prediction: dict, team_a_name: str, team_b_name: str, market_analysis: dict | None = None) -> dict:
    best_pick = prediction.get("best_pick") if isinstance(prediction.get("best_pick"), dict) else {}
    return {
        "sport": "nba",
        "team_a": team_a_name,
        "team_b": team_b_name,
        "winner_pick": best_pick.get("prediction") or "",
        "winner_probability": _assistant_pick_probability(prediction, team_a_name, team_b_name),
        "confidence": best_pick.get("confidence") or prediction.get("confidence") or "",
        "reasoning": str(best_pick.get("reasoning") or "").strip(),
        "totals_pick": ((market_analysis or {}).get("totals_leg") or {}).get("recommendation") or _assistant_totals_pick_display(prediction),
        "spread_pick": ((market_analysis or {}).get("spread_leg") or {}).get("recommendation") or "",
        "top_factors": _assistant_extract_top_factors(prediction),
    }


def _log_err(msg: str, exc: Exception = None) -> None:
    current_app.logger.error("%s%s", msg, f": {exc}" if exc else "")
    if exc:
        current_app.logger.debug(traceback.format_exc())


class _route_timer:
    """Context manager that logs route execution time."""

    def __init__(self, route_name: str):
        self.route_name = route_name
        self.start: float = 0

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *_exc):
        elapsed_ms = (time.perf_counter() - self.start) * 1000
        current_app.logger.info(
            "NBA %-25s rendered in %7.0f ms", self.route_name, elapsed_ms,
        )
        return False


def _support(route_name: str) -> dict:
    """Return route_support dict for the given route name."""
    return nc.get_route_support(route_name)


def _now_stamp() -> str:
    return datetime.now(CALENDAR_ZONE).isoformat()


def _page_context(**kwargs) -> dict:
    context = {
        "data_source": "ESPN public NBA feeds + live standings source",
        "last_updated": _now_stamp(),
    }
    context.update(kwargs)
    return context


def _game_side(game: dict, side: str) -> dict:
    teams = game.get("teams") or {}
    if side == "away":
        return teams.get("visitors") or teams.get("away") or {}
    return teams.get("home") or {}


def _team_logo(team: dict) -> str:
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
    for candidate in _team_name_candidates(team):
        canonical = _canonical_nba_name(candidate)
        abbr = _NBA_ESPN_ABBR_BY_CANONICAL.get(canonical)
        if abbr:
            return f"https://a.espncdn.com/i/teamlogos/nba/500/scoreboard/{abbr}.png"
    return ""


def _fallback_prediction_card_from_game(game: dict, team_map: dict[str, dict] | None = None) -> dict:
    return {**game, "prediction": None, "prediction_error": "Prediction unavailable"}


def _nba_pregame_reason(game: dict) -> str | None:
    raw_date = game.get("date")
    kickoff = raw_date.get("start") if isinstance(raw_date, dict) else raw_date
    return unavailable_reason(kickoff, game.get("status"), game.get("data_freshness"))


def _nba_calendar_parts(kickoff):
    parsed = parse_kickoff(kickoff)
    return (calendar_date(parsed), parsed.astimezone(CALENDAR_ZONE).strftime("%H:%M")) if parsed else ("", "")


def _nba_form_reason(*contexts):
    if any(context.get("data_freshness") and freshness_state(context["data_freshness"]) != "fresh" for context in contexts):
        return "Current NBA team form needs a fresh provider update before a pregame estimate is available."
    return None


def _nba_guard_prediction(prediction, game, *contexts):
    if _nba_pregame_reason(game) or _nba_form_reason(*contexts):
        return None
    if prediction and prediction.get("decision_card"):
        raw_date = game.get("date")
        prediction["decision_card"].update(
            game_date=raw_date.get("start") if isinstance(raw_date, dict) else raw_date,
            event_status=game.get("status"),
            data_freshness=worst([game.get("data_freshness"), *[context.get("data_freshness") for context in contexts]]))
        prediction["decision_card"] = sanitize_decision(prediction["decision_card"])
    return prediction


def _build_nba_opp_strengths(nba_standings: dict | list | None) -> dict:
    flat = []
    if isinstance(nba_standings, dict):
        for conf_teams in nba_standings.values():
            flat.extend(conf_teams)
    elif nba_standings:
        flat = list(nba_standings)

    ranked = []
    for index, entry in enumerate(flat):
        team_info = entry.get("team") or entry
        name = team_info.get("name") or team_info.get("nickname", "")
        conference = entry.get("conference")
        conference_rank = conference.get("rank") if isinstance(conference, dict) else None
        rank = entry.get("rank") or entry.get("seed") or conference_rank or (index + 1)
        if name:
            ranked.append({"team": {"name": name}, "rank": rank})

    return se.build_opp_strengths_from_standings(ranked) if ranked else {}


def _nba_projection(game: dict, home_id: str, away_id: str, home_name: str, away_name: str) -> tuple[dict | None, list, str | None]:
    """Fetch both seasons, rosters and player averages, then project; (None, contexts, reason) when it cannot."""
    contexts, previous = [{}, {}], [[], []]
    season = game.get("season") or nc._season_from_game_date(game) or nc.NBA_SEASON
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(nc.get_team_recent_form_context, team_id, season, 100, 0, "page") for team_id in (home_id, away_id)]
        past = [executor.submit(nc.get_team_season_games, team_id, season - 1, "page") for team_id in (home_id, away_id)]
        pools = executor.submit(_nba_player_pools, home_id, away_id, season)
        for index, future in enumerate(futures):
            try:
                contexts[index] = future.result() or {}
            except Exception:
                current_app.logger.warning("NBA season games unavailable for team %s", (home_id, away_id)[index], exc_info=True)
        for index, future in enumerate(past):
            try:
                previous[index] = future.result() or []
            except Exception:
                current_app.logger.warning("NBA previous-season games unavailable for team %s", (home_id, away_id)[index], exc_info=True)
        try:
            home_pool, away_pool = pools.result()
        except Exception:
            current_app.logger.warning("NBA rosters or player averages unavailable for %s vs %s", home_id, away_id, exc_info=True)
            home_pool = away_pool = None
    if any("current_games" not in context for context in contexts):
        return None, contexts, "Current-season team results are unavailable right now."
    if reason := _nba_form_reason(*contexts):
        return None, contexts, reason
    projection = pregame.project(contexts[0]["current_games"] + previous[0], contexts[1]["current_games"] + previous[1],
                                 home_id, away_id, (game.get("date") or {}).get("start"), neutral=game.get("neutral_site") is True,
                                 home_players=home_pool, away_players=away_pool)
    if projection["min_sample"] < pregame.MIN_GAMES:
        counts = (f"{home_name} {projection['home']['games']}+{projection['home']['previous_games']}, "
                  f"{away_name} {projection['away']['games']}+{projection['away']['previous_games']}")
        return None, contexts, f"Ratings need {pregame.MIN_GAMES} completed games per team this season or last (current+previous: {counts})."
    return projection, contexts, None


def _build_upcoming_prediction_card(game: dict, team_map: dict[str, dict], nba_opp_strengths: dict) -> dict:
    if reason := _nba_pregame_reason(game):
        return {**game, "prediction": None, "unavailable_reason": reason}
    home_id = str(_game_side(game, "home").get("id") or "")
    away_id = str(_game_side(game, "away").get("id") or "")
    home_team = team_map.get(home_id) or _game_side(game, "home")
    away_team = team_map.get(away_id) or _game_side(game, "away")

    if not home_team.get("name") or not away_team.get("name"):
        return {**game, "prediction": None}

    home_name, away_name = home_team["name"], away_team["name"]
    projection, contexts, reason = _nba_projection(game, home_id, away_id, home_name, away_name)
    if reason:
        return {**game, "prediction": None, "unavailable_reason": reason}
    kickoff = (game.get("date") or {}).get("start")
    home_pool = projection["availability"]["home"]
    raw_home = projection["home_probability"]
    projection["home_probability"] = recalibration.adjust("nba", raw_home, 0.0, 1 - raw_home)[0]

    market_card, market = _nba_market(game)
    decision = pregame.decide(projection, market, home_name, away_name)
    p_home = projection["home_probability"]
    favorite_home = p_home >= 0.5
    favorite_probability = max(p_home, 1 - p_home)
    side_name = home_name if decision["side_home"] else away_name
    sample = projection["min_sample"]
    data_quality = "Strong Data" if sample >= 20 and home_pool is not None else "Partial Data" if sample >= 8 else "Limited Data"
    card = dui.build_decision_card(
        analysis={
            "match_id": str(game.get("id") or ""),
            "matchup": f"{home_name} vs {away_name}",
            "action": decision["action"],
            "recommended_side": side_name,
            "confidence": round(decision["side_probability"] * 100),
            "probabilities": {"a": round(p_home * 100, 1), "draw": 0, "b": round((1 - p_home) * 100, 1)},
            "reason": decision["reason"],
            "data_quality": data_quality,
            "metric_breakdown": None,
        },
        sport="nba", team_a=home_name, team_b=away_name, competition="NBA",
        team_a_logo=home_team.get("logo") or "", team_b_logo=away_team.get("logo") or "",
    )
    card.update(
        team_a_id=home_id, team_b_id=away_id, model_source=pregame.VERSION, market=market_card,
        edge_score=None if decision["edge"] is None else round(decision["edge"], 4),
        game_date=kickoff, event_status=game.get("status"),
        data_freshness=worst([game.get("data_freshness"), *[context.get("data_freshness") for context in contexts]]),
        cta_payload={"team_a": home_id, "team_b": away_id, "team_a_name": home_name, "team_b_name": away_name,
                     "team_a_logo": home_team.get("logo") or "", "team_b_logo": away_team.get("logo") or "",
                     "event_id": game.get("id") or "", "event_date": kickoff or "",
                     "event_status": ((game.get("status") or {}).get("long") or ""),
                     "venue_name": ((game.get("venue") or {}).get("name") or ""), "short_name": game.get("short_name") or ""},
    )
    favorite = home_name if favorite_home else away_name
    prediction = {
        "model": projection | {"home": _rating_summary(projection["home"]), "away": _rating_summary(projection["away"]),
                               "availability": {side: _availability_summary(projection["availability"][side])
                                                for side in ("home", "away")}},
        "win_probabilities": card["probabilities"],
        "confidence": "High" if favorite_probability >= 0.7 else "Medium" if favorite_probability >= 0.6 else "Low",
        "best_pick": {"prediction": f"{favorite} Win", "team": "A" if favorite_home else "B",
                      "reasoning": decision["reason"]},
        "components_a": _rating_summary(projection["home"]),
        "components_b": _rating_summary(projection["away"]),
        "play_type": decision["action"],
        "decision_card": sanitize_decision(card),
        "raw_win_probabilities": {"a": round(raw_home * 100, 1), "draw": 0, "b": round((1 - raw_home) * 100, 1)},
        "market_odds": None if not market or not (market["home_decimal"] and market["away_decimal"]) else
        {"a": market["home_decimal"], "b": market["away_decimal"], "bookmaker": market["bookmaker"]},
    }
    prediction["best_pick"]["confidence"] = prediction["confidence"]
    if market and market.get("total") is not None:
        prediction.update(totals_pick="Over" if projection["total"] > market["total"] else "Under",
                          totals_line=market["total"], totals_market="Total Points")
    if reason := _nba_pregame_reason(game):
        return {**game, "prediction": None, "unavailable_reason": reason}
    return {**game, "prediction": prediction}


def _rating_summary(team: dict) -> dict:
    return {"games": team["games"], "previous_games": team["previous_games"], "sample": round(team["sample"], 1),
            "record": team["record"], "rating": round(team["rating"], 2),
            "raw_margin": None if team["raw_margin"] is None else round(team["raw_margin"], 1),
            "back_to_back": team.get("back_to_back", False)}


def _availability_summary(lineup: dict | None) -> dict | None:
    if lineup is None:
        return None
    players = [{"id": row["id"], "name": row["name"], "status": row["status"], "on_roster": row["on_roster"],
                "availability": row["availability"], "minutes": round(row["minutes"], 1), "pts": round(row["pts"], 1),
                "reb": round(row["reb"], 1), "ast": round(row["ast"], 1), "value": round(row["value"], 2),
                "played_share": round(row["played_share"], 2), "delta": round(row["delta"], 2)}
               for row in lineup["players"] if row["on_roster"] or abs(row["delta"]) >= 0.25]
    return {"adjustment": round(lineup["adjustment"], 2), "players": players[:12]}


def _nba_player_pools(home_id: str, away_id: str, season: int) -> tuple[list | None, list | None]:
    """Rosters with injury status plus this season's and last season's player averages for both teams."""
    with ThreadPoolExecutor(max_workers=4) as executor:
        rosters = [executor.submit(nc.get_team_roster, team_id, season, "page") for team_id in (home_id, away_id)]
        lines = {year: executor.submit(nc.get_league_player_stats, year, "page") for year in (season, season - 1)}
        lines = {year: future.result() for year, future in lines.items()}
        pools = []
        for team_id, future in zip((home_id, away_id), rosters):
            roster = [{"id": player.get("id"), "name": player.get("displayName") or
                       f"{player.get('firstname', '')} {player.get('lastname', '')}".strip(),
                       "position": (player.get("leagues") or {}).get("standard", {}).get("pos") or "",
                       "status": ((player.get("injuries") or [{}])[0] or {}).get("status") or ""}
                      for player in future.result() or []]
            pools.append(pregame.player_pool(team_id, roster, lines) if roster else None)
    return pools[0], pools[1]


def _nba_market(game: dict) -> tuple[dict | None, dict | None]:
    """Return (display market, model market) only when the schedule snapshot carrying the odds is recent."""
    market = pregame.market_from_odds(game.get("odds"))
    freshness = game.get("data_freshness") or {}
    observed = parse_kickoff(freshness.get("observed_at"))
    if not market or observed is None or freshness_state(freshness) != "fresh" \
            or not 0 <= (datetime.now(UTC) - observed).total_seconds() < 900:
        return None, None
    display = {"bookmaker": market["bookmaker"], "updated_at": freshness["observed_at"], "data_freshness": freshness,
               "home_decimal": market["home_decimal"], "away_decimal": market["away_decimal"],
               "home_spread": market["home_spread"], "total": market["total"]}
    return display, market


def _confidence_display_label(conf_key: str) -> str:
    key = str(conf_key or "").strip()
    if key == "High":
        return "High Confidence"
    if key == "Medium":
        return "Moderate Confidence"
    if key == "Low":
        return "Low Confidence"
    return "Limited Data"


def _downgrade_confidence_key(conf_key: str) -> str:
    if conf_key == "High":
        return "Medium"
    if conf_key == "Medium":
        return "Low"
    if conf_key == "Low":
        return "Limited Data"
    return "Limited Data"


def _apply_nba_confidence_profile(
    prediction: dict,
    *,
    form_a_games: int,
    form_b_games: int,
    stats_a_available: bool,
    stats_b_available: bool,
    used_historical_context: bool,
    data_limited: bool,
) -> dict:
    if not isinstance(prediction, dict):
        return prediction

    best_pick = prediction.get("best_pick") or {}
    win_probs = prediction.get("win_probabilities") or {}
    prob_a = float(win_probs.get("a", 50.0) or 50.0)
    prob_b = float(win_probs.get("b", 50.0) or 50.0)
    prob_gap = abs(prob_a - prob_b)

    form_quality = min(form_a_games, form_b_games)
    stats_ok = bool(stats_a_available and stats_b_available)

    if data_limited or form_quality < 2 or not stats_ok:
        conf_key = "Limited Data"
    elif prob_gap >= 14 and form_quality >= 5:
        conf_key = "High"
    elif prob_gap >= 7 and form_quality >= 3:
        conf_key = "Medium"
    else:
        conf_key = "Low"

    if used_historical_context and conf_key in {"High", "Medium", "Low"}:
        conf_key = _downgrade_confidence_key(conf_key)

    best_pick["confidence"] = conf_key
    best_pick["confidence_label"] = _confidence_display_label(conf_key)
    prediction["best_pick"] = best_pick
    prediction["confidence_label"] = best_pick["confidence_label"]
    return prediction


def _nba_data_completeness(
    *,
    form_a_games: int,
    form_b_games: int,
    stats_a_available: bool,
    stats_b_available: bool,
    used_historical_context: bool,
    data_limited: bool,
) -> dict:
    form_quality = min(form_a_games, form_b_games)
    stats_ok = bool(stats_a_available and stats_b_available)

    if data_limited or form_quality < 2 or not stats_ok:
        return {
            "tier": "limited",
            "label": "Limited data",
            "summary": "Current-season coverage is thin here, so treat this matchup as a lower-trust read.",
        }
    if used_historical_context or form_quality < 4:
        return {
            "tier": "partial",
            "label": "Mixed data quality",
            "summary": "Mostly current-season context is available, with some historical support filling the gaps.",
        }
    return {
        "tier": "full",
        "label": "Current-season data",
        "summary": "Form, team stats, and matchup context are all coming from current-season inputs.",
    }


def _nba_prediction_edge_state(prediction: dict) -> dict[str, str]:
    win_probs = prediction.get("win_probabilities") or {}
    prob_a = float(win_probs.get("a", 50.0) or 50.0)
    prob_b = float(win_probs.get("b", 50.0) or 50.0)
    gap = abs(prob_a - prob_b)

    if gap < 4:
        return {
            "title": "Narrow matchup profile",
            "summary": "The side edge is playable but tight, so confidence depends more on form, rest, and rotation context.",
        }
    if gap < 8:
        return {
            "title": "Playable lean",
            "summary": "One side grades better, but the separation is still modest.",
        }
    if gap < 14:
        return {
            "title": "Clear edge",
            "summary": "The pre-game profile shows meaningful separation between the two teams.",
        }
    return {
        "title": "Strong edge",
        "summary": "Several inputs are lining up in the same direction, creating a stronger-than-usual signal.",
    }


def _nba_reason_tags(prediction: dict, data_completeness: dict) -> list[str]:
    tags: list[str] = []
    key_edges = prediction.get("key_edges") or []
    if key_edges:
        category = str((key_edges[0] or {}).get("category") or "").strip()
        if category:
            tags.append(f"{category} edge")

    win_probs = prediction.get("win_probabilities") or {}
    prob_a = float(win_probs.get("a", 50.0) or 50.0)
    prob_b = float(win_probs.get("b", 50.0) or 50.0)
    prob_gap = abs(prob_a - prob_b)
    if prob_gap < 4:
        tags.append("Close matchup")
    elif prob_gap >= 14:
        tags.append("Strong separation")

    tier = str(data_completeness.get("tier") or "")
    if tier == "limited":
        tags.append("Limited data")
    elif tier == "partial":
        tags.append("Mixed data")

    return tags[:4]


def _build_nba_prediction_explainer(
    prediction: dict,
    *,
    data_completeness: dict,
    team_a_name: str,
    team_b_name: str,
) -> dict:
    best_pick = prediction.get("best_pick") if isinstance(prediction.get("best_pick"), dict) else {}
    edge_state = _nba_prediction_edge_state(prediction)
    summary = str(
        best_pick.get("reasoning")
        or prediction.get("matchup_reading")
        or edge_state["summary"]
    ).strip()

    return {
        "headline": edge_state["title"],
        "summary": summary,
        "supporting_note": edge_state["summary"],
        "reliability_label": data_completeness.get("label") or "Match context",
        "reliability_note": data_completeness.get("summary") or "The prediction is using the available pre-game context.",
        "tags": _nba_reason_tags(prediction, data_completeness),
        "raw_score_note": "",
    }


def _apply_nba_product_presentation(
    prediction: dict,
    *,
    data_completeness: dict,
    team_a_name: str,
    team_b_name: str,
    team_a_logo: str = "",
    team_b_logo: str = "",
    form_strip: list[Any] | None = None,
) -> dict:
    if not isinstance(prediction, dict):
        return prediction

    prediction["data_completeness"] = data_completeness
    probs = prediction.get("win_probabilities") or {}
    best = prediction.get("best_pick") or {}
    decision = DecisionEngine().build_decision({
        "home_name": team_a_name, "away_name": team_b_name,
        "probabilities": {"home": probs.get("a"), "draw": 0, "away": probs.get("b")},
        "recommended_side": best.get("prediction"),
        "confidence": prediction.get("confidence_pct") or prediction.get("confidence"),
        "data_quality": {"full": 85, "partial": 65, "limited": 45}.get(data_completeness.get("tier"), 45),
        "odds": prediction.get("odds") or {},
    })
    existing_action = prediction.get("action") or prediction.get("play_type")
    if existing_action:
        decision["action"] = existing_action if existing_action in {"BET", "CONSIDER", "SKIP"} else "SKIP"
    analysis = {"match_id": prediction.get("match_id"), "matchup": f"{team_a_name} vs {team_b_name}",
                "action": decision["action"], "confidence": decision["confidence"],
                "recommended_side": decision["side"], "reason": best.get("reasoning") or prediction.get("matchup_reading") or "Weighted rule prediction",
                "probabilities": {"a": probs.get("a"), "draw": 0, "b": probs.get("b")},
                "data_quality": "Strong Data" if decision["data_quality"] >= 75 else ("Partial Data" if decision["data_quality"] >= 55 else "Limited Data"),
                "metric_breakdown": {key: decision[key] for key in ("edge_score", "expected_value", "risk_level", "model_probability")}}
    decision_card = dui.build_decision_card(
        analysis=analysis,
        sport="nba",
        team_a=team_a_name,
        team_b=team_b_name,
        prediction=prediction,
        competition="NBA",
        team_a_logo=team_a_logo,
        team_b_logo=team_b_logo,
        form_strip=form_strip or [],
        cta_url="/nba/prediction",
        cta_label="View Matchup",
    )
    action = decision_card.get("action") or "CONSIDER"
    if action == "BET":
        risk_label = "Controlled"
    elif action == "CONSIDER":
        risk_label = "Balanced"
    else:
        risk_label = "Elevated"
    prediction["play_type"] = action
    prediction["confidence_pct"] = decision_card["confidence_pct"]
    prediction["risk_label"] = risk_label
    prediction["data_completeness"] = data_completeness
    prediction["decision_card"] = decision_card
    prediction["decision_explainer"] = _build_nba_prediction_explainer(
        prediction,
        data_completeness=data_completeness,
        team_a_name=team_a_name,
        team_b_name=team_b_name,
    )
    return prediction


def _refresh_requested() -> bool:
    return str(request.args.get("refresh", "")).strip().lower() in {"1", "true", "yes", "on"}


def _clear_nba_cache() -> None:
    for folder in (cache_dir("nba"), cache_dir("nba_public")):
        if not folder.exists():
            continue
        for path in folder.glob("*.json"):
            try:
                path.unlink()
            except OSError:
                continue


def _apply_refresh() -> None:
    if _refresh_requested():
        _clear_nba_cache()


_ESPN_NBA_OVERVIEW_CACHE = cache_dir("nba")
_ESPN_NBA_OVERVIEW_TTL = 1800       # 30 minutes
_ESPN_TIMEOUT    = float(os.getenv("EXTERNAL_API_TIMEOUT_SECONDS", "20"))
_ESPN_RETRIES    = max(1, int(os.getenv("EXTERNAL_API_RETRY_ATTEMPTS", "3")))
_ESPN_BACKOFF    = float(os.getenv("EXTERNAL_API_RETRY_BACKOFF_SECONDS", "1.2"))
_ESPN_RETRY_CODES = {429, 500, 502, 503, 504}


def _espn_player_overview(player_id: str) -> dict:
    """Fetch ESPN player overview with retry, cache, and stale-cache fallback."""
    url = (
        f"https://site.web.api.espn.com/apis/common/v3/sports"
        f"/basketball/nba/athletes/{player_id}/overview"
    )
    cache_key = hashlib.md5(f"nba_espn_overview:{player_id}".encode()).hexdigest()
    path = _ESPN_NBA_OVERVIEW_CACHE / f"espn_ov_{cache_key}.json"

    if path.exists():
        age = (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)).total_seconds()
        if 0 <= age < _ESPN_NBA_OVERVIEW_TTL:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            data[SOURCE_KEY] = metadata("ESPN player overview", datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(), _ESPN_NBA_OVERVIEW_TTL)
            return data

    last_exc: Exception | None = None
    for attempt in range(_ESPN_RETRIES):
        try:
            with requests.Session() as sess:
                sess.trust_env = False
                resp = sess.get(
                    url,
                    timeout=_ESPN_TIMEOUT,
                    headers={"Accept": "application/json"},
                )
            if resp.status_code in _ESPN_RETRY_CODES and attempt < _ESPN_RETRIES - 1:
                time.sleep(_ESPN_BACKOFF * (2 ** attempt))
                continue
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise TypeError("Unsupported ESPN player overview schema")
            data[SOURCE_KEY] = metadata("ESPN player overview", datetime.now(UTC).isoformat(), _ESPN_NBA_OVERVIEW_TTL)
            _ESPN_NBA_OVERVIEW_CACHE.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            return data
        except (requests.RequestException, ValueError, TypeError) as exc:
            last_exc = exc
            if attempt < _ESPN_RETRIES - 1:
                time.sleep(_ESPN_BACKOFF * (2 ** attempt))

    # Retain expired files for diagnosis, but never serve them as fresh data.
    raise RuntimeError(
        f"ESPN NBA player overview unavailable for player_id={player_id}"
    ) from last_exc


def _season_split_map(overview: dict) -> dict:
    statistics = overview.get("statistics") or {}
    names = statistics.get("names") or []
    splits = statistics.get("splits") or []
    regular = next((split for split in splits if split.get("displayName") == "Regular Season"), None)
    stats = (regular or {}).get("stats") or []
    return {name: stats[idx] for idx, name in enumerate(names) if idx < len(stats)}


def _overview_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _recent_game_log(overview: dict) -> list[dict]:
    game_log = overview.get("gameLog") or {}
    stat_groups = game_log.get("statistics") or []
    if not stat_groups:
        return []
    totals = stat_groups[0]
    names = totals.get("names") or []
    events_by_id = game_log.get("events") or {}
    rows = []
    for event_stats in (totals.get("events") or [])[:5]:
        event_id = str(event_stats.get("eventId") or "")
        values = event_stats.get("stats") or []
        value_map = {name: values[idx] for idx, name in enumerate(names) if idx < len(values)}
        event = events_by_id.get(event_id) or {}
        rows.append(
            {
                "event_id": event_id,
                "date": (event.get("gameDate") or "")[:10],
                "opponent": event.get("opponent", ""),
                "result": event.get("gameResult", ""),
                "minutes": value_map.get("minutes"),
                "min": value_map.get("minutes"),
                "points": _overview_number(value_map.get("points")),
                "rebounds": _overview_number(value_map.get("totalRebounds")),
                "assists": _overview_number(value_map.get("assists")),
                "blocks": _overview_number(value_map.get("blocks")),
                "steals": _overview_number(value_map.get("steals")),
                "turnovers": _overview_number(value_map.get("turnovers")),
                "threePointPct": _overview_number(value_map.get("threePointPct")),
                "fieldGoalPct": _overview_number(value_map.get("fieldGoalPct")),
                "tpm": _overview_number(value_map.get("threePointFieldGoalsMade")),
                "fgm": "-",
                "fga": "-",
                "fgp": _overview_number(value_map.get("fieldGoalPct")),
                "pra": round(
                    float(value_map.get("points", 0) or 0)
                    + float(value_map.get("totalRebounds", 0) or 0)
                    + float(value_map.get("assists", 0) or 0),
                    1,
                ) if all(_overview_number(value_map.get(k)) is not None for k in ("points", "totalRebounds", "assists")) else None,
            }
        )
    return rows


def _build_nba_props_from_avgs(season_avgs: dict, limited_data: bool) -> list[dict]:
    # A season overview is descriptive: it supplies neither verified opponent
    # samples nor calibrated prop confidence. Do not fabricate prop estimates.
    return []


def _build_nba_key_threats(roster: list, featured: list) -> list[dict]:
    """Return up to 5 key threat players for a team.

    If live featured leaders exist (from a selected game) we use those first,
    then fill remaining slots from the roster ordered by position importance.
    """
    position_meta = {
        "PG": {"label": "Playmaker", "contribution": "points + assists", "rank": 1},
        "SG": {"label": "Scorer",    "contribution": "points + 3-pointers", "rank": 2},
        "SF": {"label": "Wing",      "contribution": "points + rebounds",  "rank": 3},
        "PF": {"label": "Power Forward", "contribution": "rebounds + mid-range", "rank": 4},
        "C":  {"label": "Rim Anchor", "contribution": "rebounds + blocks", "rank": 5},
        "G":  {"label": "Guard",     "contribution": "points + assists",   "rank": 2},
        "F":  {"label": "Forward",   "contribution": "points + rebounds",  "rank": 3},
    }

    threats = []
    featured_ids = set()

    # First: promoted featured leaders (live stat leaders for this game)
    for fp in featured[:3]:
        pid = str(fp.get("id") or fp.get("playerId") or "")
        if not pid:
            continue
        featured_ids.add(pid)
        pos = fp.get("position") or fp.get("abbreviation") or "G"
        meta = position_meta.get(pos, position_meta["G"])
        threats.append({
            "id": pid,
            "name": fp.get("name") or fp.get("displayName") or "—",
            "photo": fp.get("headshot") or fp.get("photo") or "",
            "position": pos,
            "threat_label": meta["label"],
            "contribution": meta["contribution"],
            "injured": False,
            "live_stat": f"{fp.get('value', '')} {fp.get('metric', '')}".strip(),
            "is_featured": True,
        })

    # Then: fill remaining slots from roster by position rank
    for p in roster:
        if len(threats) >= 5:
            break
        pid = str(p.get("id") or "")
        if pid in featured_ids or not pid:
            continue
        injuries = p.get("injuries") or []
        is_injured = bool(injuries) and (injuries[0].get("status") or "").lower() == "out"
        if is_injured:
            continue  # skip confirmed-out players

        pos_raw = ""
        if p.get("leagues") and p["leagues"].get("standard"):
            pos_raw = p["leagues"]["standard"].get("pos") or ""
        if not pos_raw:
            pos_raw = p.get("position") or "G"

        meta = position_meta.get(pos_raw, position_meta["G"])
        threats.append({
            "id": pid,
            "name": f"{p.get('firstname','')} {p.get('lastname','')}".strip() or p.get("displayName","—"),
            "photo": p.get("photo") or "",
            "position": pos_raw,
            "threat_label": meta["label"],
            "contribution": meta["contribution"],
            "injured": bool(injuries),
            "live_stat": "",
            "is_featured": False,
        })

    return threats[:5]


def _build_player_analysis(player_id: str, player_name: str = "") -> dict:
    overview = _espn_player_overview(str(player_id))
    season_avgs = _season_split_map(overview)
    last5_log = _recent_game_log(overview)
    limited_data = len(last5_log) < 5
    return {
        "player": {"id": str(player_id), "name": player_name},
        "season_avgs": {key: _overview_number(season_avgs.get(source)) for key, source in {
            "games": "gamesPlayed", "min": "avgMinutes", "points": "avgPoints", "rebounds": "avgRebounds",
            "assists": "avgAssists", "blocks": "avgBlocks", "steals": "avgSteals", "turnovers": "avgTurnovers",
            "fgp": "fieldGoalPct", "tpp": "threePointPct", "ftp": "freeThrowPct", "tpm": "avgThreePointFieldGoalsMade"}.items()},
        "last5_log": last5_log,
        "last5_limited": limited_data,
        "vs_opp_records": [],
        "vs_opp_games": 0,
        "vs_opp_avgs": None,
        "vs_opp_limited": True,
        "limited_data_warning": limited_data,
        "props": _build_nba_props_from_avgs(season_avgs, limited_data),
        "data_source": "ESPN player overview",
        "last_updated": (overview.get(SOURCE_KEY) or {}).get("observed_at"),
        "data_freshness": overview.get(SOURCE_KEY),
    }


# -- Routes ------------------------------------------------------------------

@nba_bp.route("", methods=["GET"])
@nba_bp.route("/", methods=["GET"])
def index():
    with _route_timer("index"):
      return _index_inner()


def _index_inner():
    _apply_refresh()
    load_error = None
    teams = []
    today_games = []
    upcoming_games = []
    upcoming_games_with_predictions = []

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            fut_teams = pool.submit(nc.get_teams)
            fut_today = pool.submit(nc.get_today_games, "page")
            fut_upcoming = pool.submit(nc.get_upcoming_games, 12, 5, "page")
            fut_standings = pool.submit(nc.get_standings)

        teams = fut_teams.result()
    except Exception as e:
        load_error = str(e)
        _log_err("NBA teams fetch failed", e)

    try:
        today_games = fut_today.result()
    except Exception as e:
        _log_err("NBA today games fetch failed", e)

    try:
        upcoming_games = fut_upcoming.result()
    except Exception as e:
        _log_err("NBA upcoming games fetch failed", e)

    # ── Generate Scorpred predictions for each upcoming game ──────────────────
    team_map = {str(t["id"]): t for t in teams} if teams else {}
    nba_opp_strengths = {}
    try:
        nba_opp_strengths = _build_nba_opp_strengths(fut_standings.result())
    except Exception as e:
        _log_err("NBA standings fetch failed for index predictions", e)

    slate_games = upcoming_games or [
        game for game in today_games or []
        if (game.get("status") or {}).get("state") in {"pre", "in"}
    ]

    if slate_games:
        indexed_games = list(enumerate(slate_games))
        ordered_results: list[dict | None] = [None] * len(indexed_games)
        max_workers = min(6, len(indexed_games)) or 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_build_upcoming_prediction_card, game, team_map, nba_opp_strengths): index
                for index, game in indexed_games
            }
            for future in as_completed(future_map):
                index = future_map[future]
                game = indexed_games[index][1]
                try:
                    ordered_results[index] = future.result()
                except Exception as e:
                    _log_err(f"Prediction for NBA game {game.get('id')}", e)
                    ordered_results[index] = _fallback_prediction_card_from_game(game, team_map)

                if not ((ordered_results[index] or {}).get("prediction") or {}).get("decision_card"):
                    ordered_results[index] = _fallback_prediction_card_from_game(game, team_map)

        upcoming_games_with_predictions = [result for result in ordered_results if result is not None]

    decision_cards = [
        (game.get("prediction") or {}).get("decision_card") or _nba_schedule_decision(game)
        for game in upcoming_games_with_predictions
    ]
    dui.assign_opportunity_ranks(decision_cards)

    return render_template(
        "nba/index.html",
        **_page_context(
            teams=teams,
            today_games=today_games,
            upcoming_games=upcoming_games_with_predictions,
            full_slate=decision_cards,
            top_opportunities=dui.top_opportunities(decision_cards, limit=4),
            today_plan=dui.plan_summary(decision_cards),
            selection_notice=(request.args.get("selection_error") or "").strip() or None,
            selected_game=_selected_nba_game() or {},
            load_error=load_error,
            route_support=_support("index"),
        ),
    )


@nba_bp.route("/matchup", methods=["POST"])
@nba_bp.route("/select", methods=["POST"])
def select():
    _apply_refresh()
    a_id = request.form.get("team_a", "").strip()
    b_id = request.form.get("team_b", "").strip()

    if not a_id or not b_id or a_id == b_id:
        return _selection_error_redirect("The selected NBA matchup could not be prepared for Match Analysis.")

    try:
        teams = nc.get_teams()
    except Exception:
        return _selection_error_redirect("The selected NBA matchup could not be loaded because team data is unavailable.")

    # IDs from the new provider are strings like "1", "2" etc.
    team_map = {str(t["id"]): t for t in teams}
    if a_id not in team_map or b_id not in team_map:
        return _selection_error_redirect("The selected NBA matchup could not be matched to the current team list.")

    ta, tb = team_map[a_id], team_map[b_id]
    _store_nba_teams(ta, tb)
    session.pop("nba_selected_game", None)

    return redirect(url_for("nba.prediction"))


@nba_bp.route("/select-game", methods=["GET", "POST"])
def select_game():
    _apply_refresh()
    # Support both GET (card links) and POST (form submissions)
    a_id = (request.form.get("team_a") or request.args.get("team_a") or "").strip()
    b_id = (request.form.get("team_b") or request.args.get("team_b") or "").strip()
    a_name = (request.form.get("team_a_name") or request.args.get("team_a_name") or "").strip()
    b_name = (request.form.get("team_b_name") or request.args.get("team_b_name") or "").strip()
    a_logo = (request.form.get("team_a_logo") or request.args.get("team_a_logo") or "").strip()
    b_logo = (request.form.get("team_b_logo") or request.args.get("team_b_logo") or "").strip()

    if not a_id or not b_id or a_id == b_id:
        return _selection_error_redirect("The selected NBA game could not be prepared for Match Analysis.")

    try:
        teams = nc.get_teams()
    except Exception:
        return _selection_error_redirect("The selected NBA game could not be loaded because team data is unavailable.")

    team_a, reason_a = _resolve_nba_team_from_selection(a_id, a_name, teams)
    team_b, reason_b = _resolve_nba_team_from_selection(b_id, b_name, teams)

    if not team_a:
        team_a, fallback_reason_a = _fallback_nba_team_from_selection(a_id, a_name, a_logo)
        if team_a:
            reason_a = fallback_reason_a
    if not team_b:
        team_b, fallback_reason_b = _fallback_nba_team_from_selection(b_id, b_name, b_logo)
        if team_b:
            reason_b = fallback_reason_b

    if not team_a or not team_b:
        failure_reason = f"home={reason_a}; away={reason_b}"
        _log_team_match_debug(
            "failed",
            a_name,
            b_name,
            teams,
            reason=failure_reason,
        )
        return _selection_error_redirect(_selection_mismatch_notice(a_name, b_name, teams, failure_reason))

    _log_team_match_debug(
        "resolved",
        a_name,
        b_name,
        teams,
        reason=f"home={reason_a}; away={reason_b}",
    )

    _store_nba_teams(team_a, team_b)
    if request.method == "POST":
        _store_selected_game_from_payload(request.form)
    else:
        _store_selected_game_from_payload(request.args)
    return redirect(url_for("nba.prediction"))


@nba_bp.route("/matchup")
def matchup():
    with _route_timer("matchup"):
      return _matchup_inner()


def _matchup_inner():
    _apply_refresh()
    team_a, team_b = _require_nba_teams()
    if not team_a:
        return _selection_error_redirect("Match Analysis could not be opened because no NBA game is selected.")

    id_a, id_b = str(team_a["id"]), str(team_b["id"])
    error = None
    selected_game = _selected_nba_game()
    game_snapshot = None

    h2h_rows = []
    h2h_summary = {}
    form_a = []
    form_b = []
    recent_form_a = []
    recent_form_b = []
    split_a = {}
    split_b = {}
    injuries_a = []
    injuries_b = []
    injury_summary_a = {}
    injury_summary_b = {}
    key_players_a = []
    key_players_b = []
    stats_a = None
    stats_b = None
    roster_a = []
    roster_b = []
    raw_h2h = []
    form_context_a = {"current_season": nc.NBA_SEASON, "current_games": [], "historical_games": [], "using_historical_context": False}
    form_context_b = {"current_season": nc.NBA_SEASON, "current_games": [], "historical_games": [], "using_historical_context": False}

    # ── Parallel data fetching ────────────────────────────────────────────────
    _fetch_tasks: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=8) as _pool:
        if selected_game and selected_game.get("event_id"):
            _fetch_tasks["snapshot"] = _pool.submit(
                nc.get_event_snapshot, selected_game["event_id"], selected_game.get("date"), "page",
            )
        _fetch_tasks["h2h"] = _pool.submit(nc.get_h2h, id_a, id_b, nc.NBA_SEASON, "page")
        _fetch_tasks["form_a"] = _pool.submit(nc.get_team_recent_form_context, id_a, nc.NBA_SEASON, 10, 2, "page")
        _fetch_tasks["form_b"] = _pool.submit(nc.get_team_recent_form_context, id_b, nc.NBA_SEASON, 10, 2, "page")
        _fetch_tasks["stats_a"] = _pool.submit(nc.get_team_season_stats, id_a)
        _fetch_tasks["stats_b"] = _pool.submit(nc.get_team_season_stats, id_b)
        _fetch_tasks["roster_a"] = _pool.submit(nc.get_team_roster, id_a, nc.NBA_SEASON, "page")
        _fetch_tasks["roster_b"] = _pool.submit(nc.get_team_roster, id_b, nc.NBA_SEASON, "page")
        _fetch_tasks["standings"] = _pool.submit(nc.get_standings)

    if "snapshot" in _fetch_tasks:
        try:
            game_snapshot = _fetch_tasks["snapshot"].result()
        except Exception as e:
            _log_err("Selected game snapshot", e)

    try:
        raw_h2h = _fetch_tasks["h2h"].result()
        h2h_rows = np_nba.h2h_display(raw_h2h, id_a, id_b)
        h2h_summary = np_nba.build_h2h_summary(raw_h2h, id_a, id_b, n=5)
    except Exception as e:
        _log_err("H2H fetch", e)

    try:
        form_context_a = _fetch_tasks["form_a"].result()
        recent_a = form_context_a.get("current_games") or []
        form_a = np_nba.extract_form_for_display(recent_a, id_a)
        recent_form_a = np_nba.extract_recent_form(recent_a, id_a, n=5)
    except Exception as e:
        _log_err("Form A fetch", e)

    try:
        form_context_b = _fetch_tasks["form_b"].result()
        recent_b = form_context_b.get("current_games") or []
        form_b = np_nba.extract_form_for_display(recent_b, id_b)
        recent_form_b = np_nba.extract_recent_form(recent_b, id_b, n=5)
    except Exception as e:
        _log_err("Form B fetch", e)

    try:
        stats_a = _fetch_tasks["stats_a"].result()
    except Exception as e:
        _log_err("Stats A", e)

    try:
        stats_b = _fetch_tasks["stats_b"].result()
    except Exception as e:
        _log_err("Stats B", e)

    try:
        roster_a = _fetch_tasks["roster_a"].result()
        injuries_a = nc.get_injuries_from_roster(roster_a)
        key_players_a = np_nba.build_key_player_stats_summary(roster_a, limit=5)
        injury_summary_a = np_nba.build_injury_summary(injuries_a, roster_a)
    except Exception as e:
        _log_err("Roster A", e)

    try:
        roster_b = _fetch_tasks["roster_b"].result()
        injuries_b = nc.get_injuries_from_roster(roster_b)
        key_players_b = np_nba.build_key_player_stats_summary(roster_b, limit=5)
        injury_summary_b = np_nba.build_injury_summary(injuries_b, roster_b)
    except Exception as e:
        _log_err("Roster B", e)

    def _splits(form_list):
        home = [g for g in form_list if g["is_home"]]
        away = [g for g in form_list if not g["is_home"]]

        def _agg(lst):
            if not lst:
                return {"w": 0, "l": 0, "ppg": 0, "opp_ppg": 0}
            w = sum(1 for g in lst if g["result"] == "W")
            return {
                "w": w, "l": len(lst) - w,
                "ppg":     round(sum(g["our_pts"]   for g in lst) / len(lst), 1),
                "opp_ppg": round(sum(g["their_pts"] for g in lst) / len(lst), 1),
            }

        return {"home": _agg(home), "away": _agg(away)}

    split_a = _splits(form_a)
    split_b = _splits(form_b)

    # ── Scorpred Engine ────────────────────────────────────────────────────────
    scorpred = None
    try:
        h2h_form_a = np_nba.extract_recent_form(raw_h2h, id_a, n=10)
        h2h_form_b = np_nba.extract_recent_form(raw_h2h, id_b, n=10)

        # Build opponent-strength lookup from NBA standings (pre-fetched in parallel)
        nba_opp_strengths = {}
        try:
            nba_opp_strengths = _build_nba_opp_strengths(_fetch_tasks["standings"].result())
        except Exception:
            pass

        reason = (_nba_pregame_reason(game_snapshot or selected_game or {})
                  or _nba_form_reason(form_context_a, form_context_b))
        if reason or min(len(recent_form_a), len(recent_form_b)) < 2:
            error = reason or "At least two completed current-season games per team are needed."
            raise ValueError(error)
        mastermind = sm.predict_match(
            {
                "sport": "nba",
                "team_a_name": team_a.get("nickname") or team_a["name"],
                "team_b_name": team_b.get("nickname") or team_b["name"],
                "team_a_is_home": True,
                "form_a": recent_form_a,
                "form_b": recent_form_b,
                "h2h_form_a": h2h_form_a,
                "h2h_form_b": h2h_form_b,
                "injuries_a": injuries_a,
                "injuries_b": injuries_b,
                "opp_strengths": nba_opp_strengths,
                "team_stats": {
                    "a": stats_a or {},
                    "b": stats_b or {},
                },
            }
        )
        scorpred = mastermind.get("ui_prediction") or {}
        scorpred = _apply_nba_confidence_profile(
            scorpred,
            form_a_games=len(recent_form_a),
            form_b_games=len(recent_form_b),
            stats_a_available=bool(stats_a),
            stats_b_available=bool(stats_b),
            used_historical_context=bool(form_context_a.get("using_historical_context") or form_context_b.get("using_historical_context")),
            data_limited=(len(recent_form_a) < 2 or len(recent_form_b) < 2),
        )
        data_completeness = _nba_data_completeness(
            form_a_games=len(recent_form_a),
            form_b_games=len(recent_form_b),
            stats_a_available=bool(stats_a),
            stats_b_available=bool(stats_b),
            used_historical_context=bool(form_context_a.get("using_historical_context") or form_context_b.get("using_historical_context")),
            data_limited=(len(recent_form_a) < 2 or len(recent_form_b) < 2),
        )
        scorpred = _apply_nba_product_presentation(
            scorpred,
            data_completeness=data_completeness,
            team_a_name=team_a.get("nickname") or team_a["name"],
            team_b_name=team_b.get("nickname") or team_b["name"],
            team_a_logo=team_a.get("logo") or "",
            team_b_logo=team_b.get("logo") or "",
            form_strip=recent_form_a,
        )
    except Exception as e:
        _log_err("Scorpred NBA engine", e)

    scorpred = _nba_guard_prediction(scorpred, game_snapshot or selected_game or {}, form_context_a, form_context_b)
    season_context = {
        "current_season": nc.NBA_SEASON,
        "form_a_current": len(form_context_a.get("current_games") or []),
        "form_b_current": len(form_context_b.get("current_games") or []),
        "form_a_historical": len(form_context_a.get("historical_games") or []),
        "form_b_historical": len(form_context_b.get("historical_games") or []),
        "has_historical_context": bool(form_context_a.get("historical_games") or form_context_b.get("historical_games")),
    }

    return render_template(
        "nba/matchup.html",
        **_page_context(
            team_a=team_a,
            team_b=team_b,
            selected_game=selected_game or {},
            game_snapshot=game_snapshot or {},
            h2h_rows=h2h_rows,
            h2h_summary=h2h_summary,
            form_a=form_a,
            form_b=form_b,
            recent_form_a=recent_form_a,
            recent_form_b=recent_form_b,
            split_a=split_a,
            split_b=split_b,
            stats_a=stats_a or {},
            stats_b=stats_b or {},
            injuries_a=injuries_a,
            injuries_b=injuries_b,
            injury_summary_a=injury_summary_a,
            injury_summary_b=injury_summary_b,
            key_players_a=key_players_a,
            key_players_b=key_players_b,
            scorpred=scorpred,
            season_context=season_context,
            error=error,
            route_support=_support("matchup"),
        ),
    )


@nba_bp.route("/player")
def player():
    _apply_refresh()
    team_a, team_b = _require_nba_teams()
    if not team_a:
        return redirect(url_for("nba.index"))

    id_a, id_b = str(team_a["id"]), str(team_b["id"])
    error = None
    roster_a = []
    roster_b = []
    selected_game = _selected_nba_game()
    game_snapshot = None
    featured_players = {"home": [], "visitors": []}

    if selected_game and selected_game.get("event_id"):
        try:
            game_snapshot = nc.get_event_snapshot(
                selected_game["event_id"], selected_game.get("date")
            )
        except Exception as e:
            _log_err("Selected game snapshot for player page", e)

    try:
        roster_a = nc.get_team_roster(id_a)
        roster_a.sort(key=lambda p: p.get("lastname", ""))
    except Exception as e:
        _log_err("Roster A", e)
        error = str(e)

    try:
        roster_b = nc.get_team_roster(id_b)
        roster_b.sort(key=lambda p: p.get("lastname", ""))
    except Exception as e:
        _log_err("Roster B", e)

    if selected_game and selected_game.get("event_id"):
        try:
            featured_players = nc.get_featured_players_for_event(
                selected_game["event_id"], selected_game.get("date")
            )
        except Exception as e:
            _log_err("Featured players", e)

    threats_a = _build_nba_key_threats(roster_a, featured_players.get("home", []))
    threats_b = _build_nba_key_threats(roster_b, featured_players.get("visitors", []))

    return render_template(
        "nba/player.html",
        **_page_context(
            team_a=team_a,
            team_b=team_b,
            roster_a=roster_a,
            roster_b=roster_b,
            threats_a=threats_a,
            threats_b=threats_b,
            selected_game=selected_game or {},
            game_snapshot=game_snapshot or {},
            featured_players=featured_players,
            error=error,
            route_support=_support("player"),
        ),
    )


@nba_bp.route("/player/analyze", methods=["POST"])
@nba_bp.route("/api/player-analysis")
def player_analysis_api():
    _apply_refresh()
    payload = request.get_json(silent=True) or request.values
    player_id = str(payload.get("player_id", "")).strip()
    player_name = str(payload.get("player_name", "")).strip()
    if not player_id:
        return jsonify({"error": "player_id is required"}), 400

    try:
        analysis = _build_player_analysis(player_id, player_name=player_name)
        return jsonify(analysis)
    except Exception as exc:
        _log_err("NBA player analysis", exc)
        return jsonify(
            {
                "error": str(exc),
                "season_avgs": {},
                "last5_log": [],
                "vs_opp_records": [],
                "props": [],
                "limited_data_warning": True,
                "data_source": "ESPN player overview",
                "last_updated": _now_stamp(),
            }
        ), 200


@nba_bp.route("/prediction")
def prediction():
    with _route_timer("prediction"):
      return _prediction_inner()


def _prediction_inner():
    _apply_refresh()
    team_a, team_b = _require_nba_teams()
    if not team_a:
        return _selection_error_redirect("Match Analysis could not be opened because no NBA game is selected.")

    id_a, id_b = str(team_a["id"]), str(team_b["id"])
    error = None
    h2h_games = []
    h2h_games_filtered = []
    h2h_rows = []
    h2h_summary = {}
    form_a_raw = []
    form_b_raw = []
    form_a_filtered = []
    form_b_filtered = []
    injuries_a = []
    injuries_b = []
    stats_a = None
    stats_b = None
    selected_game = _selected_nba_game()
    game_snapshot = None
    form_context_a = {"current_season": nc.NBA_SEASON, "current_games": [], "historical_games": [], "using_historical_context": False}
    form_context_b = {"current_season": nc.NBA_SEASON, "current_games": [], "historical_games": [], "using_historical_context": False}

    # ── Parallel data fetching ────────────────────────────────────────────────
    _fetch_tasks: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=9) as _pool:
        if selected_game and selected_game.get("event_id"):
            _fetch_tasks["snapshot"] = _pool.submit(
                nc.get_event_snapshot, selected_game["event_id"], selected_game.get("date"), "page",
            )
        _fetch_tasks["h2h"] = _pool.submit(nc.get_h2h, id_a, id_b, nc.NBA_SEASON, "page")
        _fetch_tasks["form_a"] = _pool.submit(nc.get_team_recent_form_context, id_a, nc.NBA_SEASON, 10, 2, "page")
        _fetch_tasks["form_b"] = _pool.submit(nc.get_team_recent_form_context, id_b, nc.NBA_SEASON, 10, 2, "page")
        _fetch_tasks["inj_a"] = _pool.submit(nc.get_team_injuries, id_a, nc.NBA_SEASON, "page")
        _fetch_tasks["inj_b"] = _pool.submit(nc.get_team_injuries, id_b, nc.NBA_SEASON, "page")
        _fetch_tasks["stats_a"] = _pool.submit(nc.get_team_season_stats, id_a)
        _fetch_tasks["stats_b"] = _pool.submit(nc.get_team_season_stats, id_b)
        _fetch_tasks["standings"] = _pool.submit(nc.get_standings)

    if "snapshot" in _fetch_tasks:
        try:
            game_snapshot = _fetch_tasks["snapshot"].result()
        except Exception as e:
            _log_err("Selected game snapshot for prediction", e)

    try:
        h2h_games = _fetch_tasks["h2h"].result()
        h2h_games_filtered = np_nba.filter_completed_nba_games(h2h_games)
        h2h_rows = np_nba.h2h_display(h2h_games_filtered, id_a, id_b)
        h2h_summary = np_nba.build_h2h_summary(h2h_games_filtered, id_a, id_b, n=5)
    except Exception as e:
        _log_err("H2H for prediction", e)

    try:
        form_context_a = _fetch_tasks["form_a"].result()
        form_a_raw = form_context_a.get("current_games") or []
        form_a_filtered = np_nba.filter_completed_nba_games(form_a_raw)
    except Exception as e:
        _log_err("Form A for prediction", e)

    try:
        form_context_b = _fetch_tasks["form_b"].result()
        form_b_raw = form_context_b.get("current_games") or []
        form_b_filtered = np_nba.filter_completed_nba_games(form_b_raw)
    except Exception as e:
        _log_err("Form B for prediction", e)

    try:
        injuries_a = _fetch_tasks["inj_a"].result()
    except Exception as e:
        _log_err("Injuries A for prediction", e)

    try:
        injuries_b = _fetch_tasks["inj_b"].result()
    except Exception as e:
        _log_err("Injuries B for prediction", e)

    try:
        stats_a = _fetch_tasks["stats_a"].result()
    except Exception as e:
        _log_err("Stats A for prediction", e)

    try:
        stats_b = _fetch_tasks["stats_b"].result()
    except Exception as e:
        _log_err("Stats B for prediction", e)

    # NOTE: Removed legacy nba_predictor.predict_winner() - using ONLY Scorpred Engine now
    # This ensures a single source of truth for all predictions

    form_a_display = np_nba.extract_form_for_display(form_a_filtered, id_a)
    form_b_display = np_nba.extract_form_for_display(form_b_filtered, id_b)
    h2h_rows = np_nba.h2h_display(h2h_games_filtered, id_a, id_b)

    # ── Scorpred Engine ────────────────────────────────────────────────────────
    scorpred = None
    market_analysis = None
    try:
        nba_form_a = np_nba.extract_recent_form(form_a_filtered, id_a, n=5)
        nba_form_b = np_nba.extract_recent_form(form_b_filtered, id_b, n=5)
        h2h_form_a = np_nba.extract_recent_form(h2h_games_filtered, id_a, n=5)
        h2h_form_b = np_nba.extract_recent_form(h2h_games_filtered, id_b, n=5)

        # Build opponent-strength lookup from NBA standings (pre-fetched in parallel)
        nba_opp_strengths = {}
        try:
            nba_opp_strengths = _build_nba_opp_strengths(_fetch_tasks["standings"].result())
        except Exception:
            pass

        reason = (_nba_pregame_reason(game_snapshot or selected_game or {})
                  or _nba_form_reason(form_context_a, form_context_b))
        if reason or min(len(nba_form_a), len(nba_form_b)) < 2:
            error = reason or "At least two completed current-season games per team are needed."
            raise ValueError(error)
        mastermind = sm.predict_match(
            {
                "sport": "nba",
                "team_a_name": team_a.get("nickname") or team_a["name"],
                "team_b_name": team_b.get("nickname") or team_b["name"],
                "team_a_is_home": True,
                "form_a": nba_form_a,
                "form_b": nba_form_b,
                "h2h_form_a": h2h_form_a,
                "h2h_form_b": h2h_form_b,
                "injuries_a": injuries_a,
                "injuries_b": injuries_b,
                "opp_strengths": nba_opp_strengths,
                "team_stats": {
                    "a": stats_a or {},
                    "b": stats_b or {},
                },
            }
        )
        scorpred = mastermind.get("ui_prediction") or {}
        scorpred = _apply_nba_confidence_profile(
            scorpred,
            form_a_games=len(nba_form_a),
            form_b_games=len(nba_form_b),
            stats_a_available=bool(stats_a),
            stats_b_available=bool(stats_b),
            used_historical_context=bool(form_context_a.get("using_historical_context") or form_context_b.get("using_historical_context")),
            data_limited=(len(nba_form_a) < 2 or len(nba_form_b) < 2),
        )
        market_analysis = np_nba.build_market_recommendations(
            team_a,
            team_b,
            scorpred,
            nba_form_a,
            nba_form_b,
            h2h_games_filtered,
            injuries_a,
            injuries_b,
            stats_a=stats_a,
            stats_b=stats_b,
            team_a_is_home=True,
        )
    except Exception as e:
        _log_err("Scorpred NBA engine", e)

    has_historical_context = bool(form_context_a.get("historical_games") or form_context_b.get("historical_games"))
    limited_current_season = len(form_a_filtered) < 2 or len(form_b_filtered) < 2

    data_notes = [
        "Upcoming/live game context is from ESPN's public scoreboard and summary feeds.",
        "Primary analysis uses current-season completed form and current-season team snapshot data.",
        "Head-to-head is shown as historical context and is not treated as current-season form.",
    ]
    if limited_current_season:
        data_notes.append("Current-season data is limited for this matchup.")
    if has_historical_context:
        data_notes.append("Historical context is shown where current-season coverage is incomplete.")

    season_context = {
        "current_season": nc.NBA_SEASON,
        "form_a_current": len(form_context_a.get("current_games") or []),
        "form_b_current": len(form_context_b.get("current_games") or []),
        "form_a_historical": len(form_context_a.get("historical_games") or []),
        "form_b_historical": len(form_context_b.get("historical_games") or []),
        "has_historical_context": has_historical_context,
        "limited_current_season": limited_current_season,
        "stats_current_available": bool(stats_a and stats_b),
    }

    if scorpred:
        data_completeness = _nba_data_completeness(
            form_a_games=season_context["form_a_current"],
            form_b_games=season_context["form_b_current"],
            stats_a_available=bool(stats_a),
            stats_b_available=bool(stats_b),
            used_historical_context=has_historical_context,
            data_limited=limited_current_season,
        )
        scorpred = _apply_nba_product_presentation(
            scorpred,
            data_completeness=data_completeness,
            team_a_name=team_a.get("nickname") or team_a["name"],
            team_b_name=team_b.get("nickname") or team_b["name"],
            team_a_logo=team_a.get("logo") or "",
            team_b_logo=team_b.get("logo") or "",
            form_strip=nba_form_a,
        )

    scorpred = _nba_guard_prediction(scorpred, game_snapshot or selected_game or {}, form_context_a, form_context_b)
    if scorpred is None:
        market_analysis = None
    # Not tracked: this page runs ScorMastermind; the track record measures the pregame margin
    # model the dashboard publishes, which scheduled tracking records for every game.

    _store_assistant_page_context(
        "nba_prediction",
        _assistant_prediction_context(
            scorpred or {},
            team_a.get("nickname") or team_a["name"],
            team_b.get("nickname") or team_b["name"],
            market_analysis=market_analysis,
        ),
    )

    return render_template(
        "nba/prediction.html",
        **_page_context(
            team_a=team_a,
            team_b=team_b,
            selected_game=selected_game or {},
            game_snapshot=game_snapshot or {},
            injuries_a=injuries_a,
            injuries_b=injuries_b,
            stats_a=stats_a or {},
            stats_b=stats_b or {},
            form_a=form_a_display,
            form_b=form_b_display,
            h2h_rows=h2h_rows,
            h2h_summary=h2h_summary,
            prediction=scorpred,
            scorpred=scorpred,    # template guards on {% if scorpred %} — expose it directly
            data_notes=data_notes,
            show_data_notice=(limited_current_season or has_historical_context),
            season_context=season_context,
            error=error,
            market_analysis=market_analysis or {},
            route_support=_support("prediction"),
        ),
    )


def _build_today_prediction_card(
    game: dict, team_map: dict[str, dict], nba_opp_strengths: dict,
) -> dict | None:
    """Build a single prediction card for the today-predictions dashboard.

    Runs all per-game fetches (H2H, form, injuries) in parallel.
    Designed to be called from a ThreadPoolExecutor so multiple games
    are processed concurrently.
    """
    if _nba_pregame_reason(game):
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

    game_date_start = (game.get("date") or {}).get("start") or ""
    game_date, game_time = _nba_calendar_parts(game_date_start)

    # ── Parallel per-game data fetches ─────────────────────────────────────
    h2h_raw: list = []
    form_home: list = []
    form_away: list = []
    injuries_home: list = []
    injuries_away: list = []
    form_home_context: dict = {"using_historical_context": False}
    form_away_context: dict = {"using_historical_context": False}

    with ThreadPoolExecutor(max_workers=5) as pool:
        fut_h2h = pool.submit(nc.get_h2h, home_id, away_id)
        fut_form_h = pool.submit(nc.get_team_recent_form_context, home_id, nc.NBA_SEASON, 10)
        fut_form_a = pool.submit(nc.get_team_recent_form_context, away_id, nc.NBA_SEASON, 10)
        fut_inj_h = pool.submit(nc.get_team_injuries, home_id)
        fut_inj_a = pool.submit(nc.get_team_injuries, away_id)

    try:
        h2h_raw = fut_h2h.result()
    except Exception:
        pass
    try:
        form_home_context = fut_form_h.result()
        form_home_raw = form_home_context.get("current_games") or []
        form_home = np_nba.extract_recent_form(form_home_raw, home_id, n=5)
    except Exception:
        pass
    try:
        form_away_context = fut_form_a.result()
        form_away_raw = form_away_context.get("current_games") or []
        form_away = np_nba.extract_recent_form(form_away_raw, away_id, n=5)
    except Exception:
        pass
    try:
        injuries_home = fut_inj_h.result()
    except Exception:
        pass
    try:
        injuries_away = fut_inj_a.result()
    except Exception:
        pass

    h2h_form_home = np_nba.extract_recent_form(h2h_raw, home_id, n=5) if h2h_raw else []
    h2h_form_away = np_nba.extract_recent_form(h2h_raw, away_id, n=5) if h2h_raw else []

    if _nba_form_reason(form_home_context, form_away_context) or min(len(form_home), len(form_away)) < 2 or _nba_pregame_reason(game):
        return None

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
    prediction = _apply_nba_confidence_profile(
        prediction,
        form_a_games=len(form_home),
        form_b_games=len(form_away),
        stats_a_available=True,
        stats_b_available=True,
        used_historical_context=bool(
            form_home_context.get("using_historical_context")
            or form_away_context.get("using_historical_context")
        ),
        data_limited=(len(form_home) < 2 or len(form_away) < 2),
    )
    data_completeness = _nba_data_completeness(
        form_a_games=len(form_home),
        form_b_games=len(form_away),
        stats_a_available=True,
        stats_b_available=True,
        used_historical_context=bool(
            form_home_context.get("using_historical_context")
            or form_away_context.get("using_historical_context")
        ),
        data_limited=(len(form_home) < 2 or len(form_away) < 2),
    )
    prediction = _apply_nba_product_presentation(
        prediction,
        data_completeness=data_completeness,
        team_a_name=home_team.get("nickname") or home_team.get("name") or "Home",
        team_b_name=away_team.get("nickname") or away_team.get("name") or "Away",
        team_a_logo=home_team.get("logo") or "",
        team_b_logo=away_team.get("logo") or "",
        form_strip=form_home,
    )
    if prediction.get("decision_card"):
        prediction["decision_card"].update(
            {
                "cta_url": "/nba/select-game",
                "cta_method": "post",
                "cta_label": "Analyze Match",
                "cta_payload": {
                    "team_a": home_id,
                    "team_b": away_id,
                    "team_a_name": home_team.get("name") or home_team.get("nickname") or "Home",
                    "team_b_name": away_team.get("name") or away_team.get("nickname") or "Away",
                    "team_a_logo": home_team.get("logo") or "",
                    "team_b_logo": away_team.get("logo") or "",
                    "event_id": game.get("id") or "",
                    "event_date": game_date_start,
                    "event_status": ((game.get("status") or {}).get("long") or ""),
                    "venue_name": ((game.get("venue") or {}).get("name") or ""),
                    "short_name": game.get("short_name") or "",
                },
            }
        )

    if _nba_pregame_reason(game) or _nba_form_reason(form_home_context, form_away_context):
        return None
    if prediction.get("decision_card"):
        prediction["decision_card"].update(game_date=game_date_start, event_status=game.get("status"),
                                           data_freshness=worst([game.get("data_freshness"), form_home_context.get("data_freshness"), form_away_context.get("data_freshness")]))
        prediction["decision_card"] = sanitize_decision(prediction["decision_card"])
    best_pick = prediction.get("best_pick", {})
    probs = prediction.get("win_probabilities", {})

    return {
        "game": game,
        "game_date": game_date,
        "game_time": game_time,
        "home_team": home_team,
        "away_team": away_team,
        "prediction": prediction,
        "decision_card": prediction.get("decision_card"),
        "predicted_winner": best_pick.get("prediction", "—"),
        "confidence": best_pick.get("confidence", "Low"),
        "prob_home": probs.get("a", 50),
        "prob_away": probs.get("b", 50),
        "reasoning": best_pick.get("reasoning", ""),
    }


def _today_prediction_from_fallback_game(game: dict, team_map: dict[str, dict]) -> dict | None:
    if _nba_pregame_reason(game):
        return None
    fallback = _fallback_prediction_card_from_game(game, team_map)
    prediction = fallback.get("prediction") or {}
    card = prediction.get("decision_card")
    if not card:
        return None
    home_raw = _game_side(game, "home")
    away_raw = _game_side(game, "away")
    home_team = team_map.get(str(home_raw.get("id") or "")) or home_raw
    away_team = team_map.get(str(away_raw.get("id") or "")) or away_raw
    game_date_start = (game.get("date") or {}).get("start") or ""
    probs = prediction.get("win_probabilities") or {}
    return {
        "game": game,
        "game_date": _nba_calendar_parts(game_date_start)[0],
        "game_time": _nba_calendar_parts(game_date_start)[1],
        "home_team": home_team,
        "away_team": away_team,
        "prediction": prediction,
        "decision_card": card,
        "predicted_winner": (prediction.get("best_pick") or {}).get("prediction", ""),
        "confidence": (prediction.get("best_pick") or {}).get("confidence", "Medium"),
        "prob_home": probs.get("a", 50),
        "prob_away": probs.get("b", 50),
        "reasoning": (prediction.get("best_pick") or {}).get("reasoning", ""),
    }


@nba_bp.route("/today-predictions")
def today_predictions():
    with _route_timer("today_predictions"):
      return _today_predictions_inner()


def _today_predictions_inner():
    """
    Show NBA predictions for today's games (or next available games if none today).
    """
    _apply_refresh()
    load_error = None
    today_games = []
    team_map = {}

    # Build team_map from the teams directory (used as a name/logo supplement)
    try:
        teams = nc.get_teams()
        team_map = {str(t["id"]): t for t in teams}
    except Exception as e:
        _log_err("Teams fetch for today predictions", e)

    # Fetch today's NBA scoreboard
    now_utc = datetime.now(UTC)
    current_app.logger.info(
        "today_predictions: UTC=%s — fetching today's scoreboard",
        now_utc.strftime("%Y-%m-%d %H:%M"),
    )
    try:
        today_games = nc.get_today_games()
        current_app.logger.info("today_predictions: today_games=%d", len(today_games))
    except Exception as e:
        _log_err("Today games fetch", e)
        load_error = str(e)

    # If no games today, fall back to the next 36 hours (days_ahead=2 scans today+2)
    if not today_games:
        current_app.logger.info(
            "today_predictions: no games on today's scoreboard — scanning next 2 days for upcoming"
        )
        try:
            today_games = nc.get_upcoming_games(next_n=12, days_ahead=2)
            current_app.logger.info(
                "today_predictions: upcoming fallback found %d games", len(today_games)
            )
        except Exception as e:
            _log_err("Upcoming games fallback", e)

    # Build opponent-strength lookup once (shared across all games)
    nba_opp_strengths = {}
    try:
        nba_standings = nc.get_standings()
        flat: list = []
        if isinstance(nba_standings, dict):
            for conf_teams in nba_standings.values():
                flat.extend(conf_teams)
        else:
            flat = list(nba_standings)
        ranked = []
        for i, entry in enumerate(flat):
            team_info = entry.get("team") or entry
            name = team_info.get("name") or team_info.get("nickname", "")
            rank = entry.get("rank") or entry.get("conference", {}).get("rank") or (i + 1)
            if name:
                ranked.append({"team": {"name": name}, "rank": rank})
        nba_opp_strengths = se.build_opp_strengths_from_standings(ranked)
    except Exception:
        pass

    # Build predictions for each game — parallelized across games
    predictions_for_games = []
    if today_games:
        max_workers = min(6, len(today_games)) or 1
        indexed_games = list(enumerate(today_games))
        ordered_results: list[dict | None] = [None] * len(indexed_games)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_build_today_prediction_card, game, team_map, nba_opp_strengths): idx
                for idx, game in indexed_games
            }
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    result = future.result()
                    if not result or not result.get("decision_card"):
                        result = _today_prediction_from_fallback_game(indexed_games[idx][1], team_map)
                    ordered_results[idx] = result
                except Exception as e:
                    _log_err(f"Prediction for game {indexed_games[idx][1].get('id')}", e)
                    ordered_results[idx] = _today_prediction_from_fallback_game(indexed_games[idx][1], team_map)

        predictions_for_games = [r for r in ordered_results if r is not None]

    current_app.logger.info(
        "today_predictions: built %d predictions from %d games",
        len(predictions_for_games), len(today_games),
    )

    # Sort by strength tier, then displayed confidence.
    predictions_for_games.sort(
        key=lambda x: (
            dui.TIER_ORDER.get(((x.get("decision_card") or {}).get("strength_tier") or "No Pick"), 5),
            -float(((x.get("decision_card") or {}).get("confidence_pct") or 0)),
        )
    )

    # ── Yesterday section: completed predictions from tracker ─────────────────
    from datetime import timedelta
    today = datetime.now(CALENDAR_ZONE).date()
    yesterday_str = (today - timedelta(days=1)).isoformat()
    today_str = today.isoformat()
    try:
        recent = mt.get_recent_predictions(limit=50)
        yesterday_results = [
            p for p in recent
            if p.get("sport") == "nba"
            and p.get("date", "") in (yesterday_str, today_str)
            and p.get("is_correct") is not None
        ]
    except Exception:
        yesterday_results = []

    decision_cards = [item.get("decision_card") for item in predictions_for_games if item.get("decision_card")]
    dui.assign_opportunity_ranks(decision_cards)

    return render_template(
        "nba/today_predictions.html",
        **_page_context(
            predictions=predictions_for_games,
            full_slate=decision_cards,
            top_opportunities=dui.top_opportunities(decision_cards, limit=4),
            today_plan=dui.plan_summary(decision_cards),
            total_games=len(today_games),
            total_predictions=len(predictions_for_games),
            load_error=load_error,
            yesterday_results=yesterday_results,
            route_support=_support("today_predictions"),
        ),
    )


@nba_bp.route("/props/generate", methods=["GET", "POST"])
def nba_props_generate():
    """
    NBA player prop generation endpoint.
    Query params:
      player_id       int   — API-NBA player id
      player_name     str   — display name
      player_team_id  int   — player's team id
      opponent_id     int   — opponent team id
      opponent_name   str   — opponent display name
      is_home         bool  — "true" or "false"
      markets         str   — comma-separated e.g. "points,rebounds,assists,pra"
      season          int   — optional, current NBA ending year
    """
    import props_engine as pe

    _apply_refresh()
    payload = request.get_json(silent=True) or request.values
    if not hasattr(payload, "get"):
        return jsonify(error="Expected an object of player and team identifiers."), 400
    try:
        if any(isinstance(payload.get(k), (bool, float, list, dict)) for k in ("player_id", "player_team_id", "opponent_id", "season")):
            raise ValueError("Identifiers must not be coerced from another type")
        player_id = int(payload.get("player_id", 0) or 0)
        player_team_id = int(payload.get("player_team_id", 0) or 0)
        opponent_id = int(payload.get("opponent_id", 0) or 0)
        season = int(payload.get("season", nc.NBA_SEASON) or nc.NBA_SEASON)
    except (TypeError, ValueError):
        return jsonify(error="Player, team, opponent and season must be integer identifiers."), 400
    player_name = payload.get("player_name", "Unknown Player")
    opponent_name = payload.get("opponent_name", "Opponent")
    is_home_str = str(payload.get("is_home", "true")).lower()
    markets_str = str(payload.get("markets", "points,rebounds,assists,pra"))
    position = payload.get("position", "")

    if min(player_id, player_team_id, opponent_id) <= 0 or not 1999 <= season <= nc.NBA_SEASON:
        return jsonify({"error": "Required: player_id, player_team_id, opponent_id"}), 400

    is_home = is_home_str not in ("false", "0", "no")
    markets = [m.strip() for m in markets_str.split(",") if m.strip()]

    try:
        result = pe.generate_props(
            sport             = "nba",
            player_id         = player_id,
            player_name       = player_name,
            player_team_id    = player_team_id,
            opponent_team_id  = opponent_id,
            opponent_name     = opponent_name,
            is_home           = is_home,
            markets           = markets,
            player_position   = position,
            season            = season,
        )
        result["data_source"] = "Legacy NBA player game-log adapter; verified coverage required"
        result["last_updated"] = None  # No source receipt supplied by the legacy log adapter.
        return jsonify(result)
    except Exception as e:
        current_app.logger.error("NBA props generation failed: %s", e)
        return jsonify({"error": str(e), "data_source": "ESPN live roster + configured NBA prop model", "last_updated": _now_stamp()}), 500


@nba_bp.route("/props")
def nba_props_page():
    """Render the NBA props bet builder page."""
    _apply_refresh()
    team_a, team_b = _require_nba_teams()
    roster_a, roster_b = [], []
    if team_a:
        try:
            roster_a = nc.get_team_roster(str(team_a["id"]))
        except Exception:
            pass
    if team_b:
        try:
            roster_b = nc.get_team_roster(str(team_b["id"]))
        except Exception:
            pass
    from flask import render_template
    return render_template(
        "props.html",
        **_page_context(
            team_a=team_a or {},
            team_b=team_b or {},
            squad_a=roster_a,
            squad_b=roster_b,
            sport="nba",
        ),
    )


@nba_bp.get('/props/research')
def nba_prop_research_page():
    return render_template('nba_prop_research.html')


@nba_api_bp.get('/api/nba/props/research/games')
@limiter.limit('30 per minute')
def nba_prop_research_games():
    raw=request.args.get('date') or datetime.now(CALENDAR_ZONE).date().isoformat()
    try:
        if len(raw)!=10:raise ValueError()
        day=datetime.strptime(raw,'%Y-%m-%d').replace(tzinfo=CALENDAR_ZONE)
        if day.strftime('%Y-%m-%d')!=raw or abs((day.date()-datetime.now(CALENDAR_ZONE).date()).days)>14:raise ValueError()
    except ValueError:
        return jsonify(error='Choose a date within 14 days in YYYY-MM-DD format.'),400
    try:
        games=nc.get_scoreboard_games(day,'page')
        slate=[]
        for game in games:
            reason=_nba_pregame_reason(game)
            home,away=_game_side(game,'home'),_game_side(game,'away')
            slate.append({'matchId':str(game['id']),'teamA':home.get('name'),'teamB':away.get('name'),
                          'pregameAvailable':reason is None,'reason':reason})
        return jsonify(slate=slate,selectedDate=raw)
    except Exception:
        return jsonify(error='Current NBA schedule could not be verified; research remains WAIT.'),503


@nba_api_bp.get('/api/nba/props/player')
@limiter.limit('10 per minute')
def nba_requested_player_line():
    """Read-only exact-line analysis; never consumes sportsbook-feed credits."""
    from concurrent.futures import ThreadPoolExecutor
    from services import nba_props,injury_reports
    from services.nba_player_line import analyze
    from services.prediction_lifecycle import parse_kickoff
    from datetime import timezone
    try:
        raw=request.args.get('date') or datetime.now(CALENDAR_ZONE).date().isoformat()
        day=datetime.strptime(raw,'%Y-%m-%d').replace(tzinfo=CALENDAR_ZONE)
        if day.strftime('%Y-%m-%d')!=raw or abs((day.date()-datetime.now(CALENDAR_ZONE).date()).days)>14:raise ValueError('Date must be within fourteen days')
        name=request.args.get('player','').strip()
        market=request.args.get('market','points');side=request.args.get('side','over');line=float(request.args.get('line','nan'))
        if not 2<=len(name)<=80 or market not in ('points','assists','rebounds','pa') or side not in ('over','under') or not math.isfinite(line) or not 0<=line<=200 or line%1 not in (0,.5):
            raise ValueError('Supply player, supported market, over/under and a whole or half line from zero to 200')
        from services.nba_player_line import player_snapshot,project_snapshot
        from services.prop_evidence_store import EvidenceStore
        from runtime_paths import persistent_root
        store=EvidenceStore(persistent_root()/'prop-research'/'evidence.sqlite3')
        now=datetime.now(timezone.utc);saved=player_snapshot(store,name,raw,now)
        if not saved:return jsonify(result={'status':'WAIT','forecast':None,'reason':'No unique saved pregame player snapshot is available yet.'})
        teams=list(saved['inputs']['eligible_rosters'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            current=list(pool.map(nba_props.current_roster,teams))
        rosters={tid:r[0] for tid,r in zip(teams,current)};sources=[r[1] for r in current]
        tid,pid=saved['team_id'],saved['player_id']
        if pid not in rosters.get(tid,{}):return jsonify(result={'status':'WAIT','forecast':None,'reason':'Player is not eligible on the refreshed current roster.'})
        start=parse_kickoff(saved['inputs']['kickoff']);injury_source=None;excluded=frozenset()
        try:
            reports,injury_source=injury_reports.espn('nba')
            excluded=frozenset(injury_reports.normalize_name(r['name']) for r in reports)
        except injury_reports.InjuryFeedUnavailable:pass
        result=project_snapshot(saved,rosters,sources,excluded)
        response=analyze(result,pid,market,line,side,start,injury_source=injury_source)
        from services.prop_evidence_store import EvidenceStore
        from services.prop_pipeline import preserve_inputs
        from runtime_paths import persistent_root
        store=EvidenceStore(persistent_root()/'prop-research'/'evidence.sqlite3')
        inputs=result.pop('_research_inputs',None)
        if inputs:response['input_snapshot_id']=preserve_inputs(store,str(saved['game_id']),inputs)
        response['snapshot_id']=store.append('nba:'+str(saved['game_id']),'requested_player_line','Exact user-supplied line; unvalidated baseline',now,response)
        return jsonify(result=response)
    except (ValueError,TypeError) as exc:return jsonify(error=str(exc)),400
    except Exception:
        current_app.logger.exception('Requested player line unavailable')
        return jsonify(error='Player analysis is unavailable; no estimate invented.'),503


@nba_api_bp.get('/api/nba/props/brief')
@limiter.limit('30 per minute')
def nba_prop_brief():
    from services.nba_player_line import saved_brief
    from services.prop_evidence_store import EvidenceStore
    from runtime_paths import persistent_root
    raw=request.args.get('date') or datetime.now(CALENDAR_ZONE).date().isoformat()
    try:
        if datetime.strptime(raw,'%Y-%m-%d').date().isoformat()!=raw:raise ValueError()
    except ValueError:return jsonify(error='Use YYYY-MM-DD'),400
    return jsonify(result=saved_brief(EvidenceStore(persistent_root()/'prop-research'/'evidence.sqlite3'),raw))


@nba_api_bp.get('/api/nba/props/research/results')
@limiter.limit('30 per minute')
def nba_prop_research_results():
    from runtime_paths import persistent_root
    from services.prop_evidence_store import EvidenceStore
    from services.prop_paper_results import stored_summary
    return jsonify(result=stored_summary(EvidenceStore(persistent_root() / 'prop-research' / 'evidence.sqlite3')))


@nba_api_bp.get('/api/nba/props/research/readiness')
@limiter.limit('30 per minute')
def nba_prop_pipeline_readiness():
    from runtime_paths import persistent_root
    from services.prop_evidence_store import EvidenceStore
    from services.prop_pipeline import readiness
    return jsonify(result=readiness(EvidenceStore(persistent_root()/'prop-research'/'evidence.sqlite3')))


@nba_api_bp.post('/api/nba/props/research/bet365-line')
@limiter.limit('12 per minute')
def nba_prop_manual_bet365_line():
    from services.prop_evidence_store import EvidenceStore
    from services.prop_manual_quote import compare_saved
    from runtime_paths import persistent_root
    options=request.get_json(silent=True)
    if not isinstance(options,dict):return jsonify(error='Enter a bet365 line and both American prices.'),400
    try:
        return jsonify(result=compare_saved(EvidenceStore(persistent_root()/'prop-research'/'evidence.sqlite3'),options))
    except (ValueError,TypeError,KeyError,LookupError) as exc:
        return jsonify(error=str(exc)),400


@nba_api_bp.route('/api/nba/props/research/snapshots/<int:snapshot_id>/explanation',methods=['GET','POST'])
@limiter.limit('6 per minute')
def nba_prop_snapshot_explanation(snapshot_id):
    from runtime_paths import persistent_root
    from services.prop_evidence_store import EvidenceStore
    from services.prop_pipeline import load_report,explain
    options=request.get_json(silent=True) or {} if request.method=='POST' else {}
    mode=options.get('mode','deterministic') if isinstance(options,dict) else None
    if mode not in ('deterministic','local_ai'):
        return jsonify(error='Choose deterministic or local_ai. Paid providers are not enabled by this endpoint.'),400
    try:
        store=EvidenceStore(persistent_root()/'prop-research'/'evidence.sqlite3')
        return jsonify(result=explain(load_report(store,snapshot_id),use_local_ai=mode=='local_ai'))
    except LookupError:
        return jsonify(error='Saved research snapshot not found.'),404
    except ValueError:
        return jsonify(error='The local explanation configuration is invalid.'),503


@nba_api_bp.get('/api/nba/props/research/parlays')
@limiter.limit('30 per minute')
def nba_prop_research_parlays():
    import json
    from datetime import UTC
    from runtime_paths import persistent_root
    from services.prop_evidence_store import EvidenceStore
    from services.prop_parlay_research import build
    try:
        count=int(request.args.get('legs','2'));target=float(request.args.get('target','2'))
        store=EvidenceStore(persistent_root() / 'prop-research' / 'evidence.sqlite3')
        with store.connection() as db:
            records=db.execute("SELECT id,payload FROM prop_evidence WHERE kind='research_decision' ORDER BY received_at,id").fetchall()
        latest={}
        for record in records:
            report=json.loads(record['payload']);report['_source_snapshot_id']=record['id'];latest[report['game_id']]=report
        legs=[];now=datetime.now(UTC)
        for report in latest.values():
            kickoff=parse_kickoff(report.get('kickoff'))
            if not kickoff or kickoff<=now:continue
            for row in report.get('rows',[]):
                quote=row.get('quote') or {};expires=parse_kickoff(quote.get('expires_at'))
                if not quote.get('market','').endswith('_alternate'):continue
                if not expires or expires<=now:continue
                legs.append({**row,'game_id':report['game_id'],'bookmaker_key':quote.get('bookmaker_key'),
                    'selection_id':f"{report['game_id']}:{row.get('player_id')}:{row.get('market')}:{quote.get('side')}:{quote.get('line')}",
                    'calibration_validated':row.get('calibration_validated') is True})
        output=build(legs,leg_count=count,target_decimal=target)
        output.update(combination_price_source='not_configured',joint_model_status='not_validated',
                      recorded_at=now.isoformat(),input_snapshot_ids=[r['_source_snapshot_id'] for r in latest.values() if (start:=parse_kickoff(r.get('kickoff'))) and start>now])
        output['snapshot_id']=store.append('nba:parlay-research','parlay_research','small-parlay-research-v1',now,output)
        output['paper_recorded']=True
        return jsonify(result=output)
    except (ValueError,TypeError):
        return jsonify(error='Choose 2 or 3 legs and finite decimal target odds greater than 1.'),400


@nba_bp.route("/standings")
def standings():
    _apply_refresh()
    data  = {"east": [], "west": []}
    error = None
    try:
        data = nc.get_standings()
    except Exception as e:
        error = str(e)
        _log_err("Standings fetch", e)
    return render_template(
        "nba/standings.html",
        **_page_context(
            standings=data,
            error=error,
            route_support=_support("standings"),
        ),
    )


def _nba_schedule_decision(game, reason=None):
    """Display a real fixture as SKIP when no supported pregame estimate exists."""
    home, away = _game_side(game, "home"), _game_side(game, "away")
    return {"match_id": str(game.get("id") or ""), "sport": "nba", "competition": "NBA",
            "team_a": home.get("name"), "team_b": away.get("name"),
            "team_a_id": home.get("id"), "team_b_id": away.get("id"),
            "team_a_logo": home.get("logo"), "team_b_logo": away.get("logo"),
            "matchup": f'{home.get("name")} vs {away.get("name")}',
            "game_date": (game.get("date") or {}).get("start", ""),
            "event_status": game.get("status"), "data_freshness": game.get("data_freshness"),
            "recommended_side": "Unavailable", "action": "SKIP", "confidence_pct": 0, "confidence": 0,
            "probabilities": {}, "model_source": "unavailable",
            "data_confidence": {"label": "Limited Data"}, "data_quality": "Limited Data",
            "reason": reason or "Schedule available; a supported pregame estimate is currently unavailable."}


@nba_api_bp.route("/api/dashboard/nba", methods=["GET"])
@limiter.limit("60 per minute")
def api_dashboard_nba():
    try:
        date_filter = (request.args.get("date") or "").strip()[:10]  # YYYY-MM-DD
        cards: list[dict] = []
        available_dates: list[str] = []
        load_error = None
        schedule_status = {}
        try:
            import nba_live_client as _nc
            # Fetch 7 days ahead to power the multi-day tabs
            all_games = _nc.get_upcoming_games(30, 7, "page") or []
            schedule_status = _nc.get_schedule_refresh_status()
            if not all_games:
                today = _nc.get_today_games("page") or []
                all_games = [g for g in today if (g.get("status") or {}).get("state") in {"pre", "in"}]

            # Collect available dates from all fetched games
            from collections import defaultdict as _dd
            games_by_date: dict[str, list] = _dd(list)
            for g in all_games:
                d = calendar_date((g.get("date") or {}).get("start"))
                if d:
                    games_by_date[d].append(g)
            available_dates = sorted(games_by_date.keys())

            # Filter to requested date or show all (first available day if none)
            if date_filter and date_filter in games_by_date:
                slate = games_by_date[date_filter]
            elif date_filter:
                slate = []
            else:
                # No date specified: show the nearest day that has games
                slate = games_by_date.get(available_dates[0], []) if available_dates else all_games

            try:
                teams = _nc.get_teams() or []
            except Exception:
                teams = []
            team_map = {str(t["id"]): t for t in teams}
            for game in all_games:
                for side in ("home", "away"):
                    team = _game_side(game, side)
                    if team.get("id") and team.get("name"):
                        team_map.setdefault(str(team["id"]), team)
            try:
                standings = _nc.get_standings() or {}
            except Exception:
                standings = {}
            nba_opp_strengths = _build_nba_opp_strengths(standings)
            ordered: list[dict | None] = [None] * len(slate)
            with ThreadPoolExecutor(max_workers=min(8, len(slate) or 1)) as ex:
                fmap = {ex.submit(_build_upcoming_prediction_card, g, team_map, nba_opp_strengths): i for i, g in enumerate(slate)}
                for fut in as_completed(fmap):
                    idx = fmap[fut]
                    try:
                        result = fut.result()
                    except Exception:
                        current_app.logger.warning("NBA prediction card build failed for game index=%d", idx, exc_info=True)
                        result = {}
                    ordered[idx] = ((result.get("prediction") or {}).get("decision_card")
                                    or _nba_schedule_decision(slate[idx], result.get("unavailable_reason")))
            cards = [sanitize_decision(card) for card in ordered if card]
            cards = [
                c for c in cards
                if (c.get("team_a") or "").strip().lower() not in ("", "tbd", "home")
                and (c.get("team_b") or "").strip().lower() not in ("", "tbd", "away")
            ]
        except Exception as exc:
            load_error = sanitize_error(exc)
            current_app.logger.warning("api_dashboard_nba data fetch error: %s", exc)
        dui.assign_opportunity_ranks(cards)
        top = dui.top_opportunities(cards, limit=4)
        plan = dui.plan_summary(cards)
        return jsonify({
            "slate": decision_store.publish(cards, "nba"),
            "topOpportunities": decision_store.publish(top, "nba"),
            "plan": {"bet": plan.get("bet", 0), "consider": plan.get("consider", 0), "skip": plan.get("skip", 0)},
            "availableDates": available_dates,
            "selectedDate": date_filter or (available_dates[0] if available_dates else ""),
            "error": load_error,
            "warnings": [schedule_status["warning"]] if schedule_status.get("warning") else [],
            "refresh_status": schedule_status.get("status", "unknown"),
            "calendarTimezone": "America/New_York",
            "last_updated": schedule_status.get("observed_at"),
        }), 503 if load_error and not cards else 200
    except Exception as exc:
        current_app.logger.warning("api_dashboard_nba error: %s", exc)
        return jsonify({"slate": [], "topOpportunities": [], "plan": {"bet": 0, "consider": 0, "skip": 0}, "availableDates": [], "error": "NBA data is temporarily unavailable."}), 503


@nba_api_bp.get("/api/nba/matches/<game_id>/context")
@limiter.limit("60 per minute")
def api_nba_matchup_context(game_id):
    from services import nba_matchup_context as context_service
    try:
        game = context_service.game_for_id(game_id, request.args.get("date"))
        if not game:
            return jsonify(error="NBA game not found in the available schedule or local archive."), 404
        return jsonify(result=context_service.context(game))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        current_app.logger.exception("NBA descriptive context unavailable")
        return jsonify(error="NBA comparisons are temporarily unavailable. Schedule and prediction availability remain separate."), 503


@nba_api_bp.get("/api/nba/matches/<game_id>/props")
@limiter.limit("20 per minute")
def api_nba_props(game_id):
    """Five best player prop picks for an upcoming game, from ESPN box scores."""
    payload, status = nba_props_payload(game_id, request.args.get("date"))
    return jsonify(payload), status


def nba_props_payload(game_id: str, date_hint: str | None) -> tuple[dict, int]:
    """({"result": ...} or {"error": ...}, HTTP status) for one game; shared with the best-props board."""
    from services import injury_reports, nba_props
    from services import nba_matchup_context as context_service
    from services.injury_reports import normalize_name
    try:
        game = context_service.game_for_id(game_id, date_hint)
        if not game:
            return {"error": "NBA game not found in the available schedule or local archive."}, 404
        if reason := _nba_pregame_reason(game):
            return {"error": reason}, 422
        home, away = _game_side(game, "home"), _game_side(game, "away")
        home_id, away_id = str(home.get("id") or ""), str(away.get("id") or "")
        tipoff = parse_kickoff((game.get("date") or {}).get("start"))
        if not home_id or not away_id or tipoff is None:
            return {"error": "This game is missing team or tip-off details."}, 422
        names = {home_id: home.get("name") or "Home", away_id: away.get("name") or "Away"}
        teams = {(home.get("abbrev") or "").upper(), (away.get("abbrev") or "").upper()} - {""}
        _freshness = None
        try:
            reports, _freshness = injury_reports.espn("nba")
            # Anyone listed for either team; without abbreviations, anyone listed in the league.
            excluded = frozenset(normalize_name(r["name"]) for r in reports if not teams or r["team"] in teams)
        except injury_reports.InjuryFeedUnavailable:
            excluded = frozenset()
        result = nba_props.project(home_id, away_id, pregame.season_of(tipoff), tipoff, excluded, event_id=game_id)
        game_environment=result.get('game_environment') or {'status':'unavailable','notice':'Pregame game-environment evidence was not collected by the active model.'}
        from services import prop_markets
        picks = [{**pick, 'team_id':pick['team'], "team": names.get(pick["team"], pick["team"])} for pick in result["picks"]]
        from services.nba_prop_research import points_candidates
        from services import nba_props_ml
        research_inputs=result.pop('_research_inputs',None)
        research_points=points_candidates(result,markets=('points','rebounds','assists'),rule_shrink=nba_props.CONFIG['shrink'],
            learned_version=nba_props_ml.VERSION,learned_shrink=nba_props_ml.pick_shrink())
        legacy_keys={(str(p.get('player_id')),p.get('market')) for p in picks}
        combined=picks+[{**p,'team':names.get(p['team'],p['team'])} for p in research_points
                        if (str(p.get('player_id')),p.get('market')) not in legacy_keys]
        for pick in combined:
            if pick.get('matchup_context'):
                pick['matchup_context']={**pick['matchup_context'], 'opponent_name':names.get(pick['matchup_context'].get('opponent_id'),'Unknown')}
        result = prop_markets.compare({"picks": combined, "roster_verification": result.get("roster_verification")},
                                     "nba", names[home_id], names[away_id], tipoff,all_lines=True,alternatives=True)
        priced_research=[p for p in result['picks'] if p.get('market') in ('points','rebounds','assists')]
        result['picks']=result['picks'][:len(picks)]
        result['sportsbook']['total_picks']=len(picks)
        result['sportsbook']['matched_picks']=sum(bool(p.get('sportsbook_quotes')) for p in result['picks'])
        if result['sportsbook'].get('status')=='available':
            result['sportsbook']['reason']=f"{result['sportsbook']['matched_picks']} of {len(picks)} legacy picks matched to fresh sportsbook lines; full points coverage is in research."
        import sqlite3
        from services.nba_prop_research import build as build_prop_research
        from services.prop_evidence_store import EvidenceStore
        from runtime_paths import persistent_root
        research = build_prop_research({**result,'picks':priced_research,'game_environment':game_environment}, game_id=game_id, kickoff=tipoff, injury_source=_freshness)
        try:
            from services.prop_analyst_research import approved_sources,for_game,video_candidates
            evidence_store=EvidenceStore(persistent_root() / 'prop-research' / 'evidence.sqlite3')
            from services.prop_validation import for_models
            keys={((p.get('model') or 'minutes-rate-count baseline')+(':'+p['market'] if p['market']!='points' else '')+suffix)
                  for p in priced_research for suffix in ('',':alternate',':integer',':alternate:integer')}
            validations=for_models(evidence_store,keys)
            research=build_prop_research({**result,'picks':priced_research,'game_environment':game_environment,
                'validation_by_model':validations},game_id=game_id,kickoff=tipoff,injury_source=_freshness,markets=('points','rebounds','assists'))
            from services.prop_pipeline import preserve_inputs
            research['input_snapshot_id']=preserve_inputs(evidence_store,game_id,{
                **(research_inputs or {}),'game_environment':game_environment,'injury_source':_freshness,
                'price_candidates':priced_research})
            sources=approved_sources()
            research['external_research']=for_game(evidence_store,game_id,research['recorded_at'],sources)
            research['external_research']['video_review']=video_candidates(evidence_store,[row['player'] for row in research['rows'] if row.get('player')],research['recorded_at'],sources)
            research['external_research']['approved_source_names']=[source.get('name',identifier) for identifier,source in sources.items() if source.get('permitted') is True]
            snapshot_id = evidence_store.append(
                f'nba:{game_id}', 'research_decision', research['version'], research['recorded_at'], research)
            research['paper_recorded'] = True
            research['snapshot_id'] = snapshot_id
        except (OSError, ValueError, TypeError, sqlite3.Error):
            research['paper_recorded'] = False
            research['status'] = 'WAIT'
            for row in research['rows']:
                row['decision'] = 'WAIT'
                row['reasons'].append('Paper evidence storage failed')
        result['research'] = research
        if reason := _nba_pregame_reason(game):
            return {"error": reason}, 422
        return {"result": result}, 200
    except nba_props.RosterUnavailable as exc:
        return {"error": str(exc)}, 503
    except ValueError as exc:
        return {"error": str(exc)}, 400
    except Exception:
        current_app.logger.exception("NBA player props failed")
        return {"error": "NBA player props are temporarily unavailable."}, 503


@nba_api_bp.get("/api/nba/matches/<game_id>/injuries")
@limiter.limit("30 per minute")
def api_nba_injuries(game_id):
    """ESPN injury statuses priced through the pregame model's lineup adjustment."""
    from services import injury_impact, injury_reports
    from services import nba_matchup_context as context_service
    try:
        game = context_service.game_for_id(game_id, request.args.get("date"))
        if not game:
            return jsonify(error="NBA game not found in the available schedule or local archive."), 404
        if reason := _nba_pregame_reason(game):
            return jsonify(error=reason), 422
        home, away = _game_side(game, "home"), _game_side(game, "away")
        home_id, away_id = str(home.get("id") or ""), str(away.get("id") or "")
        names = {"home": home.get("name") or "Home", "away": away.get("name") or "Away"}
        projection, _contexts, reason = _nba_projection(game, home_id, away_id, names["home"], names["away"])
        if reason:
            return jsonify(error=reason), 422
        warnings, sources = [], []
        try:
            espn_rows, freshness = injury_reports.espn("nba")
            sources.append(freshness)
        except injury_reports.InjuryFeedUnavailable:
            espn_rows = []
            warnings.append("The ESPN league injury feed is unavailable; statuses come from team rosters only.")
        try:
            players = injury_impact.nba_players(projection, espn_rows)
        except injury_impact.ImpactUnavailable as exc:
            return jsonify(error=str(exc)), 422
        report = injury_impact.margin_report(projection["margin"], projection["sigma"], players, priced=True)
        result = injury_impact.summarize(report, names, unit="points")
        if reason := _nba_pregame_reason(game):
            return jsonify(error=reason), 422
        result.update(sources=sources, warnings=warnings, base="pregame model",
                      notice="Win chances are the pregame model's, which already accounts for these statuses. "
                             "Player values come from box-score averages this season and last.")
        return jsonify(result=result)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        current_app.logger.exception("NBA injury impact failed")
        return jsonify(error="NBA injury data is temporarily unavailable."), 503


@nba_api_bp.get("/api/nba/research/early-season")
@limiter.limit("60 per minute")
def api_nba_early_season_report():
    from services import nba_early_season
    return jsonify(nba_early_season.load_report())


@nba_api_bp.get("/api/nba/research/early-season/matches/<game_id>")
@limiter.limit("30 per minute")
def api_nba_early_season_preview(game_id):
    from services import nba_early_season, nba_matchup_context
    if not nba_early_season.enabled():
        return jsonify(error="NBA early-season research previews are disabled. Production predictions are unchanged."), 404
    try:
        game = nba_matchup_context.game_for_id(game_id, request.args.get("date"))
        if not game:
            return jsonify(error="NBA game not found."), 404
        result = nba_early_season.forecast(nba_matchup_context.context(game), nba_early_season.load_artifact())
        return jsonify(result=result)
    except (ValueError, KeyError, OSError, TypeError):
        return jsonify(error="Early-season research inputs or calibration are unavailable, stale, or incompatible."), 422
    except Exception:
        current_app.logger.exception("NBA early-season research unavailable")
        return jsonify(error="NBA early-season research is temporarily unavailable."), 503


@nba_api_bp.get("/api/nba/games")
@limiter.limit("60 per minute")
def api_nba_observed_games():
    from services import nba_matchup_context as context_service
    try:
        view = request.args.get("view", "archive")
        if view not in {"archive", "results"}:
            return jsonify(error="Choose archive or results."), 400
        raw = request.args.get("date") or ""
        if view == "results" and not raw:
            raw = (datetime.now(CALENDAR_ZONE) - timedelta(days=1)).date().isoformat()
        if raw and (len(raw) != 10 or datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d") != raw):
            raise ValueError("Use a calendar date in YYYY-MM-DD format.")
        games, selected = context_service.games_on_date(raw or None)
        if view == "results" and raw and not games:
            day = datetime.fromisoformat(raw).replace(tzinfo=CALENDAR_ZONE)
            if abs((day.date() - datetime.now(CALENDAR_ZONE).date()).days) <= 14:
                games = [game for game in nc.get_scoreboard_games(day, "page") if game.get("status", {}).get("completed")]
                selected = raw
        return jsonify(slate=[context_service.schedule_decision(game) for game in games], topOpportunities=[],
                       selectedDate=selected or raw, availableDates=[selected or raw] if selected or raw else [],
                       plan={"bet": 0, "consider": 0, "skip": len(games)},
                       notice="Completed observations only. Archived statistics are descriptive and are never reconstructed pregame predictions.",
                       last_updated=None, history_role="descriptive_only")
    except (ValueError, TypeError):
        return jsonify(error="Use a valid calendar date in YYYY-MM-DD format."), 400
    except Exception:
        current_app.logger.exception("NBA observed games unavailable")
        return jsonify(error="NBA observed games are temporarily unavailable."), 503


@nba_api_bp.route("/api/scoreline/nba", methods=["GET", "POST"])
@limiter.limit("30 per minute")
def api_scoreline_nba():
    """
    Scoreline / points distribution for an NBA game.

    Query / body params:
        team_a_id   str   Home team provider ID
        team_b_id   str   Away team provider ID
        team_a_name str   (optional) Override display name
        team_b_name str   (optional) Override display name
        odds_total  float (optional) Market total points; over/under lines centre on it
        event_id    str   (optional) ESPN event; verifies the fixture and supplies its odds

    Scores, quarters, over/unders and player box lines come from nba_game_projection, built on
    the same pregame projection as the decision card.
    """
    from services import nba_game_projection as game_projection
    from services import scoreline_engine as scoreline_service
    from services.scoreline_inputs import ScorelineInputError, validate
    try:
        params = request.get_json(silent=True) or request.form or request.args
        validated = validate(params, "nba")
        event_id = str(params.get("event_id") or params.get("match_id") or "").strip()
        game_snapshot = None
        if event_id:
            try:
                game_snapshot = nc.get_event_snapshot(event_id, validated["before"], "page")
            except (RuntimeError, ValueError, OSError):
                raise ScorelineInputError("The NBA game status could not be refreshed. Retry when schedule data is available.") from None
            if not game_snapshot:
                raise ScorelineInputError("The NBA game status could not be verified. Refresh the schedule and retry.")
            if reason := _nba_pregame_reason(game_snapshot):
                raise ScorelineInputError(reason)
            actual_ids = [str(_game_side(game_snapshot, side).get("id") or "") for side in ("home", "away")]
            if actual_ids != [validated["team_a_id"], validated["team_b_id"]]:
                raise ScorelineInputError("The NBA teams do not match the selected fixture.")
            validated["before"] = (game_snapshot.get("date") or {}).get("start")
        team_a_id = str(params.get("team_a_id") or params.get("home_id") or "").strip()
        team_b_id = str(params.get("team_b_id") or params.get("away_id") or "").strip()
        team_a_name = str(params.get("team_a_name") or params.get("team_a") or "Home").strip()
        team_b_name = str(params.get("team_b_name") or params.get("team_b") or "Away").strip()
        odds_total = validated["odds_total"]
        if not team_a_id or not team_b_id or team_a_id == team_b_id:
            raise ScorelineInputError("Choose a match with two valid teams.")
        game = game_snapshot or {"date": {"start": validated["before"] or datetime.now(UTC).isoformat()},
                                 "teams": {"home": {"id": team_a_id}, "visitors": {"id": team_b_id}}}
        projection, contexts, reason = _nba_projection(game, team_a_id, team_b_id, team_a_name, team_b_name)
        if reason:
            raise scoreline_service.InsufficientScoreData(reason)
        _, market = _nba_market(game_snapshot) if game_snapshot else (None, None)
        if odds_total:
            market = {**(market or {}), "total": odds_total}
        result = game_projection.build(projection, market, team_a_name, team_b_name)
        input_freshness = worst([(game_snapshot or {}).get("data_freshness"), *[context.get("data_freshness") for context in contexts]])
        if game_snapshot and (reason := _nba_pregame_reason(game_snapshot)):
            raise ScorelineInputError(reason)
        if validated["before"] and not parse_kickoff(validated["before"]) > datetime.now(UTC):
            raise ScorelineInputError("Pregame scoring predictions are unavailable after kickoff.")
        result["data_freshness"] = input_freshness
        return jsonify({"status": "ok", "result": result})
    except ScorelineInputError as exc:
        return jsonify(status="error", message=str(exc)), 400
    except scoreline_service.InsufficientScoreData as exc:
        return jsonify(status="error", message=str(exc)), 422
    except Exception as exc:
        current_app.logger.error("api_scoreline_nba error: %s", exc, exc_info=True)
        return jsonify({"status": "error", "message": sanitize_error(exc)}), 500
