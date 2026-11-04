
from __future__ import annotations
import logging

# Global logger for this module
logger = logging.getLogger(__name__)
"""Evidence-building helpers for soccer matchup and prediction routes."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from services.data_freshness import from_records, worst
from services.prediction_lifecycle import calendar_date, freshness_state, unavailable_reason

_UPCOMING_FIXTURE_CACHE_TTL_SECONDS = 120
_TEAM_FORM_CACHE_TTL_SECONDS = 900
_H2H_CACHE_TTL_SECONDS = 900
_INJURY_CACHE_TTL_SECONDS = 900
_STANDINGS_CACHE_TTL_SECONDS = 1800

_UPCOMING_FIXTURE_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_TEAM_FORM_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_H2H_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_INJURY_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_STANDINGS_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}


def _cache_get(cache: dict[tuple[Any, ...], dict[str, Any]], key: tuple[Any, ...], now: datetime) -> Any | None:
    entry = cache.get(key)
    if not entry:
        return None
    if entry.get("expires_at") and entry["expires_at"] > now:
        value = deepcopy(entry.get("value"))
        def current(item):
            if isinstance(item, dict):
                meta = item.get("data_freshness") or item.get("_scorpred_source")
                return (not meta or freshness_state(meta, now) == "fresh") and all(current(v) for k, v in item.items() if k not in {"data_freshness", "_scorpred_source"})
            return all(current(v) for v in item) if isinstance(item, (list, tuple)) else True
        return value if current(value) else None
    return None


def _cache_get_stale(cache: dict[tuple[Any, ...], dict[str, Any]], key: tuple[Any, ...]) -> Any | None:
    entry = cache.get(key)
    if not entry:
        return None
    # Expired evidence must be re-observed by a provider before it can be scored.
    return None


def _cache_set(
    cache: dict[tuple[Any, ...], dict[str, Any]],
    key: tuple[Any, ...],
    value: Any,
    now: datetime,
    ttl_seconds: int,
) -> None:
    cache[key] = {
        "expires_at": now + timedelta(seconds=max(1, int(ttl_seconds))),
        "value": deepcopy(value),
    }


def _parse_fixture_dt(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_team(team: dict | None) -> dict:
    team = team or {}
    return {
        "id": int(team.get("id") or 0),
        "name": str(team.get("name") or "").strip() or "Unknown",
        "logo": str(team.get("logo") or ""),
    }


def _normalize_fixture_shape(fixture: dict, *, league: int, logger) -> dict | None:
    if not isinstance(fixture, dict):
        logger.warning("[EVIDENCE] Dropping malformed fixture entry: not a dict")
        return None

    fx = fixture.get("fixture") or {}
    teams = fixture.get("teams") or {}
    league_data = fixture.get("league") or {}

    home = _normalize_team(teams.get("home"))
    away = _normalize_team(teams.get("away"))
    if not home["id"] or not away["id"]:
        logger.warning("[EVIDENCE] Dropping fixture with invalid team IDs: home=%s away=%s", home.get("id"), away.get("id"))
        return None

    date_raw = str(fx.get("date") or "")
    parsed_date = _parse_fixture_dt(date_raw)
    if parsed_date is None:
        logger.warning("[EVIDENCE] Dropping fixture with invalid date: fixture_id=%s date=%s", fx.get("id"), date_raw)
        return None

    league_id = int(league_data.get("id") or league or 0)
    normalized = {
        **fixture,
        "fixture": {
            **fx,
            "id": int(fx.get("id") or 0),
            "date": parsed_date.isoformat(),
            "status": fx.get("status") or {"short": "NS", "long": "Not Started"},
            "venue": fx.get("venue") or {},
        },
        "teams": {"home": home, "away": away},
        "league": {
            **league_data,
            "id": league_id,
            "name": str(league_data.get("name") or f"League {league_id}").strip(),
            "season": league_data.get("season"),
            "round": league_data.get("round") or "",
        },
    }
    return normalized


def _fixture_in_window(fixture: dict, now: datetime, days: int | None) -> bool:
    if not days:
        return True
    fixture_dt = _parse_fixture_dt(str((fixture.get("fixture") or {}).get("date") or ""))
    if fixture_dt is None:
        return False
    return now <= fixture_dt <= now + timedelta(days=max(1, int(days)))


def _safe_call(call: Callable[[], Any], *, logger, label: str, default: Any) -> Any:
    logger.debug("[EVIDENCE] %s: start", label)
    try:
        value = call()
        count = len(value) if isinstance(value, list) else (len(value.keys()) if isinstance(value, dict) else None)
        if count is not None:
            logger.debug("[EVIDENCE] %s: success (count=%d)", label, count)
        else:
            logger.debug("[EVIDENCE] %s: success", label)
        return value
    except Exception as exc:  # pragma: no cover - defensive logging path
        logger.warning("[EVIDENCE] %s: failed (%s)", label, exc)
        return default


def _with_ratings_model(api_client, league: int, prediction: dict, home_name: str, away_name: str, logger) -> dict:
    """Publish the fitted ratings model's probabilities; the rules engine's stay only when ratings are unavailable."""
    from services import soccer_model
    try:
        fitted = soccer_model.predict(api_client, league, home_name, away_name)
    except Exception as exc:
        logger.warning("[EVIDENCE] ratings_model failed league=%s %s vs %s: %s", league, home_name, away_name, exc)
        fitted = None
    if fitted is None:
        logger.info("[EVIDENCE] ratings_model unavailable league=%s %s vs %s", league, home_name, away_name)
    return soccer_model.apply(prediction, fitted, home_name, away_name)


def _limited_prediction(engine, *, team_a_name: str, team_b_name: str, opp_strengths: dict, reason: str) -> dict:
    pred = engine.scorpred_predict(
        form_a=[],
        form_b=[],
        h2h_form_a=[],
        h2h_form_b=[],
        injuries_a=[],
        injuries_b=[],
        team_a_is_home=True,
        team_a_name=team_a_name,
        team_b_name=team_b_name,
        sport="soccer",
        opp_strengths=opp_strengths,
    )
    if isinstance(pred, dict):
        pred.setdefault("form_a", [])
        pred.setdefault("form_b", [])
        pred.setdefault("h2h_form_a", [])
        pred.setdefault("h2h_form_b", [])
        pred["data_quality"] = "Limited"
    return pred


def clean_injuries(items: list[dict]) -> list[dict]:
    cleaned = []
    for item in items or []:
        if item.get("placeholder"):
            continue
        player = item.get("player") or {}
        if player.get("name") == "No injuries reported":
            continue
        cleaned.append(item)
    return cleaned


def display_injuries(items: list[dict]) -> list[dict]:
    return clean_injuries(items)


def build_opp_strengths(engine, standings: list) -> dict:
    try:
        return engine.build_opp_strengths_from_standings(standings)
    except Exception:
        return {}


def _cached_team_form(
    api_client,
    predictor,
    *,
    team_id: int,
    league: int,
    season: int,
    now: datetime,
    logger,
) -> tuple[list[dict], list[dict]]:
    key = (league, season, team_id)
    cached = _cache_get(_TEAM_FORM_CACHE, key, now)
    if cached is not None:
        return cached.get("form", []), cached.get("fixtures", [])

    fixtures = _safe_call(
        lambda: api_client.get_team_fixtures(team_id, league, season, last=20),
        logger=logger,
        label=f"team_form_fetch(team_id={team_id}, league={league})",
        default=[],
    )
    fixtures = predictor.filter_recent_completed_fixtures(fixtures, current_season=season)
    form = predictor.extract_form(fixtures, team_id)[:5]

    if form:
        logger.info("[EVIDENCE] team_form_fetch ok team_id=%s rows=%d", team_id, len(form))
    else:
        logger.warning("[EVIDENCE] team_form_fetch empty team_id=%s (fixtures=%d)", team_id, len(fixtures))

    value = {"form": form, "fixtures": fixtures}
    _cache_set(_TEAM_FORM_CACHE, key, value, now, _TEAM_FORM_CACHE_TTL_SECONDS)
    return form, fixtures


def _cached_h2h(api_client, predictor, *, home_id: int, away_id: int, season: int, now: datetime, logger) -> tuple[list[dict], list[dict], list[dict]]:
    key = tuple(sorted((home_id, away_id)))
    cached = _cache_get(_H2H_CACHE, key, now)
    if cached is not None:
        return cached.get("raw", []), cached.get("home", []), cached.get("away", [])

    h2h_raw = _safe_call(
        lambda: api_client.get_h2h(home_id, away_id, last=12),
        logger=logger,
        label=f"h2h_fetch(home_id={home_id}, away_id={away_id})",
        default=[],
    )
    h2h_raw = predictor.filter_recent_completed_fixtures(h2h_raw, current_season=season, seasons_back=5)
    h2h_home = predictor.extract_form(h2h_raw, home_id)[:5]
    h2h_away = predictor.extract_form(h2h_raw, away_id)[:5]

    value = {"raw": h2h_raw, "home": h2h_home, "away": h2h_away}
    _cache_set(_H2H_CACHE, key, value, now, _H2H_CACHE_TTL_SECONDS)
    return h2h_raw, h2h_home, h2h_away


def _cached_injuries(api_client, *, team_id: int, league: int, season: int, now: datetime, logger) -> list[dict]:
    key = (league, season, team_id)
    cached = _cache_get(_INJURY_CACHE, key, now)
    if cached is not None:
        return cached

    injuries = _safe_call(
        lambda: api_client.get_injuries(league, season, team_id),
        logger=logger,
        label=f"injury_fetch(team_id={team_id}, league={league})",
        default=[],
    )
    injuries = clean_injuries(injuries)
    _cache_set(_INJURY_CACHE, key, injuries, now, _INJURY_CACHE_TTL_SECONDS)
    return injuries


def _cached_standings(api_client, *, league: int, season: int, now: datetime, logger) -> list[dict]:
    key = (league, season)
    cached = _cache_get(_STANDINGS_CACHE, key, now)
    if cached is not None:
        return cached

    standings = _safe_call(
        lambda: api_client.get_standings(league, season),
        logger=logger,
        label=f"standings_fetch(league={league}, season={season})",
        default=[],
    )
    _cache_set(_STANDINGS_CACHE, key, standings, now, _STANDINGS_CACHE_TTL_SECONDS)
    return standings


def load_upcoming_fixtures(
    api_client,
    predictor,
    engine,
    *,
    league: int | None = None,
    season: int | None = None,
    logger=logger,
    football_data_source: Callable[[], str] | None = None,
    next_n: int = 20,
    max_deep_predictions: int = 6,
    include_injuries: bool = True,
    include_standings: bool = True,
    competition: str | None = None,
    days: int | None = None,
    selected_date: str | None = None,
    **legacy_kwargs,
) -> tuple[list[dict], str | None, str, str]:
    """Load and score upcoming soccer fixtures with robust fallbacks and diagnostics.

    Backward-compatible keyword aliases supported:
    - ``league`` (canonical league id)
    - ``competition`` (canonical league-name filter string)
    - ``league_name`` (legacy alias for ``competition``)
    """
    if season is None:
        season = int(getattr(api_client, "CURRENT_SEASON", datetime.now(timezone.utc).year))

    legacy_league = legacy_kwargs.pop("league", None)
    if league is None and legacy_league is not None:
        league = legacy_league
    if league is None:
        league = int(getattr(api_client, "DEFAULT_LEAGUE_ID", 39))
    league = int(league)

    legacy_competition = legacy_kwargs.pop("league_name", None)
    if not competition and legacy_competition:
        competition = str(legacy_competition)

    if legacy_kwargs:
        logger.debug("[EVIDENCE] fixture_fetch ignoring legacy kwargs: %s", sorted(legacy_kwargs.keys()))
    now = datetime.now(timezone.utc)
    source = "configured"
    load_error: str | None = None
    espn_slug = ""

    source_hint = (football_data_source() if callable(football_data_source) else "configured") or "configured"
    force_refresh = bool(getattr(api_client, "FORCE_REFRESH", False))
    cache_key = (
        league,
        season,
        int(next_n),
        int(max_deep_predictions),
        int(bool(include_injuries)),
        int(bool(include_standings)),
        competition or "",
        int(days or 0),
        source_hint,
        selected_date,
    )

    if not force_refresh:
        cached = _cache_get(_UPCOMING_FIXTURE_CACHE, cache_key, now)
        if cached is not None:
            logger.info("[EVIDENCE] fixture_fetch cache_hit key=%s", cache_key)
            return cached

    logger.info(
        "[EVIDENCE] fixture_fetch start league=%s season=%s next_n=%s max_deep=%s competition=%s days=%s",
        league,
        season,
        next_n,
        max_deep_predictions,
        competition or "*",
        days or "*",
    )

    upcoming_raw: list[dict] = []

    # Layer 1: primary provider
    try:
        upcoming_raw = (api_client.get_fixtures_for_date(league, season, selected_date) if selected_date
                        else api_client.get_upcoming_fixtures(league, season, next_n=next_n))
        source = source_hint
        logger.info("[EVIDENCE] fixture_fetch primary_ok count=%d source=%s", len(upcoming_raw), source)
    except Exception as exc:
        load_error = "Live data temporarily unavailable. Showing fallback data."
        try:
            logger.exception("[EVIDENCE] fixture_fetch primary_failed league=%s season=%s error=%s", league, season, exc)
        except Exception:
            print(f"[LOG FAILSAFE] {exc}")

    # Layer 2: explicit fallback source when available
    if not upcoming_raw and not selected_date and hasattr(api_client, "get_espn_fixtures"):
        slug_map = getattr(api_client, "ESPN_SLUG_BY_LEAGUE", {}) or {}
        espn_slug = str(slug_map.get(league, ""))
        if espn_slug:
            try:
                upcoming_raw = api_client.get_espn_fixtures(espn_slug, next_n=next_n)
                source = "espn"
                logger.info("[EVIDENCE] fixture_fetch fallback_espn_ok slug=%s count=%d", espn_slug, len(upcoming_raw))
            except Exception as exc:
                try:
                    logger.exception("[EVIDENCE] fixture_fetch fallback_espn_failed slug=%s error=%s", espn_slug, exc)
                except Exception:
                    print(f"[LOG FAILSAFE] {exc}")
                load_error = (load_error + " | ") if load_error else ""
                load_error = f"{load_error}Fallback fixture source failed."

    # Layer 3: stale cache rescue
    if not upcoming_raw:
        stale = _cache_get_stale(_UPCOMING_FIXTURE_CACHE, cache_key)
        if stale:
            fixtures, _, cached_source, cached_slug = stale
            logger.warning(
                "[EVIDENCE] fixture_fetch stale_cache_rescue count=%d source=%s",
                len(fixtures or []),
                cached_source,
            )
            return stale

    if not upcoming_raw:
        try:
            logger.warning("[EVIDENCE] fixture_fetch empty after all sources league=%s season=%s", league, season)
        except Exception as exc:
            print(f"[LOG FAILSAFE] {exc}")
        result = ([], load_error or "No upcoming fixtures available.", source, espn_slug)
        _cache_set(_UPCOMING_FIXTURE_CACHE, cache_key, result, now, _UPCOMING_FIXTURE_CACHE_TTL_SECONDS)
        return result

    standings = []
    opp_strengths = {}
    if include_standings:
        standings = _cached_standings(api_client, league=league, season=season, now=now, logger=logger)
        opp_strengths = build_opp_strengths(engine, standings)

    normalized: list[dict] = []
    for fixture in upcoming_raw:
        item = _normalize_fixture_shape(fixture, league=league, logger=logger)
        if not item:
            continue
        if selected_date and calendar_date(item["fixture"]["date"]) != selected_date:
            continue
        if competition and competition.strip():
            league_name = str((item.get("league") or {}).get("name") or "").strip().lower()
            if competition.strip().lower() not in league_name:
                continue
        if not _fixture_in_window(item, now, days):
            continue
        normalized.append(item)

    normalized.sort(key=lambda row: str((row.get("fixture") or {}).get("date") or ""))
    fixtures_with_pred: list[dict] = []

    # Split fixtures into deep (needs full API fetch) and shallow (limited prediction)
    deep_fixtures = []
    shallow_fixtures = []
    for idx, fixture in enumerate(normalized[:next_n]):
        info = fixture.get("fixture") or {}
        blocked = unavailable_reason(info.get("date"), info.get("status"), fixture.get("data_freshness"))
        if blocked:
            fixture["prediction"] = {"action": "SKIP", "confidence_pct": 0, "best_pick": {}, "win_probabilities": {}, "reason": blocked}
            continue
        teams = fixture.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        home_id = int(home.get("id") or 0)
        away_id = int(away.get("id") or 0)
        if not home_id or not away_id or idx >= max_deep_predictions:
            shallow_fixtures.append((idx, fixture, home_id, away_id,
                                     str(home.get("name") or "Home"),
                                     str(away.get("name") or "Away"),
                                     "Missing team IDs" if not home_id or not away_id else "Deep prediction cap reached"))
        else:
            deep_fixtures.append((idx, fixture, home_id, away_id,
                                   str(home.get("name") or "Home"),
                                   str(away.get("name") or "Away")))

    # Prefetch all API data for deep fixtures in parallel
    def _fetch_fixture_data(home_id, away_id):
        """Fetch the requested evidence payloads for one fixture concurrently."""
        results = {}
        tasks = {
            "form_home": lambda: _cached_team_form(api_client, predictor, team_id=home_id, league=league, season=season, now=now, logger=logger),
            "form_away": lambda: _cached_team_form(api_client, predictor, team_id=away_id, league=league, season=season, now=now, logger=logger),
            "h2h":       lambda: _cached_h2h(api_client, predictor, home_id=home_id, away_id=away_id, season=season, now=now, logger=logger),
        }
        if include_injuries:
            tasks.update(
                {
                    "inj_home": lambda: _cached_injuries(
                        api_client, team_id=home_id, league=league, season=season, now=now, logger=logger
                    ),
                    "inj_away": lambda: _cached_injuries(
                        api_client, team_id=away_id, league=league, season=season, now=now, logger=logger
                    ),
                }
            )
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futs = {pool.submit(fn): key for key, fn in tasks.items()}
            for fut in as_completed(futs):
                key = futs[fut]
                try:
                    results[key] = fut.result()
                except Exception as exc:
                    logger.warning("[EVIDENCE] parallel_fetch %s failed: %s", key, exc)
                    results[key] = ([], []) if key.startswith("form") else ([], [], []) if key == "h2h" else []
        return results

    # Fetch all deep fixtures in parallel (one thread per fixture)
    prefetched: dict[int, dict] = {}
    if deep_fixtures:
        with ThreadPoolExecutor(max_workers=min(6, len(deep_fixtures))) as pool:
            fut_map = {
                pool.submit(_fetch_fixture_data, home_id, away_id): idx
                for idx, _, home_id, away_id, _, _ in deep_fixtures
            }
            for fut in as_completed(fut_map):
                idx = fut_map[fut]
                try:
                    prefetched[idx] = fut.result()
                except Exception as exc:
                    logger.warning("[EVIDENCE] fixture prefetch failed idx=%s: %s", idx, exc)
                    prefetched[idx] = {}

    # Build all predictions in original order
    all_items: dict[int, dict] = {idx: fixture for idx, fixture in enumerate(normalized[:next_n]) if "prediction" in fixture and unavailable_reason((fixture.get("fixture") or {}).get("date"), (fixture.get("fixture") or {}).get("status"), fixture.get("data_freshness"))}

    for idx, fixture, home_id, away_id, home_name, away_name, reason in shallow_fixtures:
        prediction = _limited_prediction(
            engine, team_a_name=home_name, team_b_name=away_name,
            opp_strengths=opp_strengths, reason=reason,
        )
        prediction = _with_ratings_model(api_client, league, prediction, home_name, away_name, logger)
        if reason == "Deep prediction cap reached":
            logger.info("[EVIDENCE] prediction_context degraded fixture_id=%s reason=deep_limit",
                        (fixture.get("fixture") or {}).get("id"))
        all_items[idx] = {**fixture, "prediction": prediction}

    for idx, fixture, home_id, away_id, home_name, away_name in deep_fixtures:
        data = prefetched.get(idx, {})
        try:
            form_home, fixtures_home = data.get("form_home") or ([], [])
            form_away, fixtures_away = data.get("form_away") or ([], [])
            fixture["data_freshness"] = worst([fixture.get("data_freshness"), from_records(fixtures_home), from_records(fixtures_away), from_records(standings)])
            if unavailable_reason((fixture.get("fixture") or {}).get("date"), (fixture.get("fixture") or {}).get("status"), fixture.get("data_freshness")):
                all_items[idx] = {**fixture, "prediction": {"action": "SKIP", "confidence_pct": 0, "best_pick": {}, "win_probabilities": {}}}
                continue
            h2h_result = data.get("h2h") or ([], [], [])
            _, h2h_home, h2h_away = h2h_result
            injuries_home = (data.get("inj_home") or []) if include_injuries else []
            injuries_away = (data.get("inj_away") or []) if include_injuries else []

            downgraded_reason = None
            if not form_home and not form_away:
                reason_bits = []
                reason_bits.append(f"home form empty (team_id={home_id}, fixtures={len(fixtures_home)})")
                reason_bits.append(f"away form empty (team_id={away_id}, fixtures={len(fixtures_away)})")
                downgraded_reason = "; ".join(reason_bits)
                logger.warning("[EVIDENCE] prediction_context downgraded fixture_id=%s reason=%s",
                               (fixture.get("fixture") or {}).get("id"), downgraded_reason)
            elif not form_home or not form_away:
                missing = "home" if not form_home else "away"
                logger.info("[EVIDENCE] prediction_context partial fixture_id=%s missing=%s_form",
                            (fixture.get("fixture") or {}).get("id"), missing)

            prediction = engine.scorpred_predict(
                form_a=form_home, form_b=form_away,
                h2h_form_a=h2h_home, h2h_form_b=h2h_away,
                injuries_a=injuries_home, injuries_b=injuries_away,
                team_a_is_home=True, team_a_name=home_name, team_b_name=away_name,
                sport="soccer", opp_strengths=opp_strengths,
            )
            if isinstance(prediction, dict):
                prediction["form_a"] = form_home
                prediction["form_b"] = form_away
                prediction["h2h_form_a"] = h2h_home
                prediction["h2h_form_b"] = h2h_away
                if downgraded_reason:
                    prediction["data_quality"] = "Limited"
                prediction = _with_ratings_model(api_client, league, prediction, home_name, away_name, logger)
        except Exception as exc:
            logger.warning("[EVIDENCE] prediction_context failed fixture_id=%s reason=%s",
                           (fixture.get("fixture") or {}).get("id"), exc)
            prediction = _limited_prediction(
                engine, team_a_name=home_name, team_b_name=away_name,
                opp_strengths=opp_strengths, reason=f"Prediction context failure: {exc}",
            )
        all_items[idx] = {**fixture, "prediction": prediction}

    fixtures_with_pred = [all_items[i] for i in sorted(all_items)]

    logger.info("[EVIDENCE] fixture_fetch end parsed=%d predicted=%d", len(normalized), len(fixtures_with_pred))
    result = (fixtures_with_pred, load_error, source, espn_slug)
    _cache_set(_UPCOMING_FIXTURE_CACHE, cache_key, result, now, _UPCOMING_FIXTURE_CACHE_TTL_SECONDS)
    return result


def build_key_threats(
    squad: list,
    injuries: list,
    fixtures: list,
    team_id: int,
    *,
    predictor,
    current_season: int,
) -> list[dict]:
    injured_ids = {
        (inj.get("player") or {}).get("id")
        for inj in injuries
        if (inj.get("player") or {}).get("id")
    }
    fixtures = predictor.filter_recent_completed_fixtures(fixtures, current_season=current_season)
    form = predictor.extract_form(fixtures, team_id)
    avg_gf = predictor.avg_goals(form, scored=True) if form else 1.2
    team_lambda = max(0.3, avg_gf)

    position_order = {"Attacker": 0, "Midfielder": 1, "Defender": 2, "Goalkeeper": 3}
    threat_labels = {
        "Attacker": "Goal Threat",
        "Midfielder": "Creative Threat",
        "Defender": "Set Piece Threat",
        "Goalkeeper": "Shot Stopper",
    }
    contribution_map = {
        "Attacker": "goals / shots on target",
        "Midfielder": "key passes / assists",
        "Defender": "aerial duels / clearances",
        "Goalkeeper": "saves / clean sheet",
    }

    candidates = []
    for player_entry in squad:
        player_obj = player_entry.get("player") or player_entry
        player_id = player_obj.get("id")
        if not player_id:
            continue
        position = player_obj.get("position") or player_entry.get("position") or "Unknown"
        is_injured = player_id in injured_ids
        pos_rank = position_order.get(position, 4)

        pos_boost = 1.4 if position == "Attacker" else 1.1 if position == "Midfielder" else 0.7
        health_penalty = 0.5 if is_injured else 1.0
        score = pos_boost * health_penalty * team_lambda

        candidates.append(
            {
                "id": player_id,
                "name": player_obj.get("name") or "",
                "photo": player_obj.get("photo", ""),
                "position": position,
                "pos_rank": pos_rank,
                "threat_label": threat_labels.get(position, "Key Player"),
                "contribution": contribution_map.get(position, "match impact"),
                "injured": is_injured,
                "score": score,
            }
        )

    candidates.sort(key=lambda item: (-item["score"], item["pos_rank"]))
    return candidates[:5]


def team_form_payload(
    api_client,
    predictor,
    *,
    team_id: int,
    league: int,
    season: int,
) -> dict:
    fixtures = api_client.get_team_fixtures(team_id, league, season, last=20)
    fixtures = predictor.filter_recent_completed_fixtures(fixtures, current_season=season)
    form = predictor.extract_form(fixtures, team_id)[:5]
    return {
        "form_string": "".join(item.get("result", "") for item in form),
        "rows": form,
    }
