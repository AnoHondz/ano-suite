from __future__ import annotations

import logging
import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from services import cache_service
from services.data_freshness import worst
from services.input_contracts import three_way_probabilities
from services.prediction_lifecycle import calendar_date, freshness_state, sanitize_decision, unavailable_reason

_logger = logging.getLogger(__name__)

_FIXTURE_CARD_TTL = 120
_MATCH_ANALYSIS_TTL = 120
_TOP_OPP_TTL = 60
_COMPILED_CARD_TTL = 60
_TODAY_PLAN_TTL = 60

_deps: dict[str, Callable[..., Any]] = {}


def configure(**deps: Callable[..., Any]) -> None:
    _deps.update(deps)


def analysis_needs_refresh(result, fixture):
    """Recompute an expired analysis only from a genuinely refreshed pregame fixture."""
    if not fixture:
        return False
    info = fixture.get("fixture") or {}
    missing_recovered = (not three_way_probabilities(result.get("probabilities"))
                         and three_way_probabilities((fixture.get("prediction") or {}).get("win_probabilities")))
    return ((freshness_state(result.get("data_freshness")) != "fresh" or missing_recovered)
            and not unavailable_reason(info.get("date"), info.get("status"), fixture.get("data_freshness")))


def _guard_analysis(result, match_id):
    lookup = _deps.get("get_fixture")
    fixture = lookup(str(match_id)) if lookup else None
    if fixture:
        info = fixture.get("fixture") or {}
        result = dict(result, kickoff=info.get("date"), event_status=info.get("status"),
                      data_freshness=worst([result.get("data_freshness"), fixture.get("data_freshness")]))
    return sanitize_decision(result)


def get_match_analysis(match_id: str | int):
    key = cache_service.make_key("match-analysis", match_id)
    cached = cache_service.get_json(key, ttl=_MATCH_ANALYSIS_TTL)
    lookup = _deps.get("get_fixture")
    fixture = lookup(str(match_id)) if lookup else None
    if cached is not None and not analysis_needs_refresh(cached, fixture):
        return _guard_analysis(cached, match_id)
    analyze_fn = _deps.get("analyze_match")
    if not analyze_fn:
        return None
    result = analyze_fn(match_id)
    if result is not None:
        cache_service.set_json(key, result, ttl=_MATCH_ANALYSIS_TTL)
    return _guard_analysis(result, match_id) if result is not None else None


def _sources_fresh(payload) -> bool:
    try:
        fixtures = payload[0] or []
    except (TypeError, IndexError, KeyError):
        return False
    return all(not f.get("data_freshness") or freshness_state(f["data_freshness"]) == "fresh" for f in fixtures)


def get_fixture_cards(league_id: int, *, selected_date: str | None = None):
    # Use a separate namespace from load_fixtures_cached to avoid cache shape collisions.
    load_date = (selected_date or calendar_date(datetime.now(UTC))) if selected_date is not None else None
    dated_loader = _deps.get("load_fixtures_for_date") if load_date else None
    raw_key = (cache_service.make_key("fixtures_raw_day_v1", league_id, load_date) if dated_loader
               else cache_service.make_key("fixtures_raw", league_id))
    payload = cache_service.get_json(raw_key, ttl=_FIXTURE_CARD_TTL)
    if payload is not None and not _sources_fresh(payload):
        # The cache's TTL counts from when it was written, but fixture freshness counts from when the
        # provider was observed; a payload that has expired would publish every card as unavailable.
        payload = None
    if payload is None:
        load_fn = _deps.get("load_fixtures")
        if not load_fn:
            return [], None, "Unavailable", "", ""
        try:
            payload = dated_loader(league_id, load_date) if dated_loader else load_fn(league_id)
        except Exception:
            _logger.warning("load_fixtures failed for league_id=%s", league_id, exc_info=True)
            payload = ([], "Fixture loader unavailable", "degraded", "")
        cache_service.set_json(raw_key, payload, ttl=_FIXTURE_CARD_TTL)
    try:
        fixtures, load_error, source, marker = payload
    except (TypeError, ValueError):
        fixtures, load_error, source, marker = [], "Cache shape error", "", ""

    build_fn = _deps.get("card_from_fixture")
    if not build_fn:
        return [], fixtures, load_error, source, marker

    valid = [f for f in (fixtures or []) if (f.get("fixture") or {}).get("id") is not None]
    restore = _deps.get("restore_fixtures")
    if restore:
        restore(valid)

    # A dashboard needs one day's analysis, not every match in the loader's
    # multi-week schedule. Background jobs keep their existing full-slate mode.
    if selected_date is not None:
        dates = sorted({calendar_date((fixture.get("fixture") or {}).get("date")) for fixture in valid} - {""})
        visible_date = selected_date or (dates[0] if dates else "")
        valid = [fixture for fixture in valid if calendar_date((fixture.get("fixture") or {}).get("date")) == visible_date]

    # Include all source metadata/status fields: a new observation invalidates
    # this short presentation cache, without changing prediction calculations.
    fingerprint = hashlib.sha256(json.dumps(valid, sort_keys=True, default=str).encode()).hexdigest()
    compiled_key = cache_service.make_key("compiled-fixture-cards-v1", league_id, selected_date, fingerprint)
    cached_cards = cache_service.get_json(compiled_key, ttl=_COMPILED_CARD_TTL) if not load_error else None
    if cached_cards is not None:
        # Withdraw expired recommendations even inside the presentation TTL.
        return [sanitize_decision(card) for card in cached_cards], fixtures, load_error, source, marker

    def _analyze_and_build(fixture: dict) -> dict | None:
        fixture_id = (fixture.get("fixture") or {}).get("id")
        info = fixture.get("fixture") or {}
        metadata = fixture.get("data_freshness") or {}
        if unavailable_reason(info.get("date"), info.get("status"), metadata) or load_error:
            home, away = (fixture.get("teams") or {}).get("home", {}), (fixture.get("teams") or {}).get("away", {})
            card = {"match_id": str(fixture_id), "team_a": home.get("name"), "team_b": away.get("name"),
                    "team_a_id": home.get("id"), "team_b_id": away.get("id"), "team_a_logo": home.get("logo"), "team_b_logo": away.get("logo"),
                    "matchup": f'{home.get("name")} vs {away.get("name")}', "game_date": info.get("date"),
                    "league_id": league_id, "competition": (fixture.get("league") or {}).get("name"), "event_status": info.get("status"),
                    "cta_url": f"/prediction?match_id={fixture_id}", "cta_method": "get", "cta_payload": {"match_id": fixture_id},
                    "data_freshness": metadata or ({"state": "degraded"} if load_error else {}), "action": "SKIP"}
            return sanitize_decision(card)
        try:
            analysis = get_match_analysis(str(fixture_id))
        except Exception:
            _logger.warning("get_match_analysis failed for fixture_id=%s", fixture_id, exc_info=True)
            analysis = None
        if not analysis:
            return None
        try:
            card = build_fn(fixture, analysis)
            if card:
                card = dict(card, game_date=info.get("date"), event_status=info.get("status"),
                            data_freshness=worst([metadata, analysis.get("data_freshness")]))
                return sanitize_decision(card)
            return None
        except Exception:
            _logger.warning("card_from_fixture failed for fixture_id=%s", fixture_id, exc_info=True)
            return None

    cards: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for card in pool.map(_analyze_and_build, valid):
            if card:
                cards.append(card)

    if not load_error and len(cards) == len(valid):
        cache_service.set_json(compiled_key, cards, ttl=_COMPILED_CARD_TTL)
    return cards, fixtures, load_error, source, marker


def get_top_opportunities(league_id: int):
    top_key = cache_service.make_key("top-opportunities", league_id)
    cached = cache_service.get_json(top_key, ttl=_TOP_OPP_TTL)
    if cached is not None:
        return [clean for card in cached if (clean := sanitize_decision(card)).get("action") != "SKIP"]
    cards, *_ = get_fixture_cards(league_id)
    top_fn = _deps.get("top_opportunities")
    result = top_fn(cards, 4) if top_fn else cards[:4]
    cache_service.set_json(top_key, result, ttl=_TOP_OPP_TTL)
    return result


def get_today_plan(league_id: int):
    # Counts are derived from guarded cards; a cached count could survive kickoff.
    cards, *_ = get_fixture_cards(league_id)
    plan_fn = _deps.get("plan_summary")
    result = plan_fn(cards) if plan_fn else {"bet": 0, "consider": 0, "skip": 0}
    return result
