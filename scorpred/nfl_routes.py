"""NFL dashboard and event-bound analysis API."""
import os
from datetime import UTC, date, datetime

from flask import Blueprint, current_app, jsonify, request

from extensions import limiter
from nfl_client import NFLProviderError
from services import nfl_service
from services.data_freshness import worst
from services.prediction_lifecycle import freshness_state, unavailable_reason

nfl_api_bp = Blueprint("nfl_api", __name__)


def _winner_research_enabled():
    return (os.getenv("NFL_ADVANCED_RESEARCH_ENABLED", "0") == "1"
            and os.getenv("NFL_WINNER_RESEARCH_ENABLED", "0") == "1")


@nfl_api_bp.route("/api/nfl/research/winner/forecasts/status", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@limiter.limit("10 per minute")
def api_nfl_pregame_status():
    if request.method not in {"GET", "HEAD"}:
        return jsonify(error="Research archive status is read-only."), 405
    if not _winner_research_enabled():
        return jsonify(error="NFL winner research is disabled."), 404
    from user_auth import current_user
    if not current_user():
        return jsonify(error="Authentication required for research archives."), 401
    from services.nfl_pregame_forecast import status
    try:
        return jsonify(research_only=True, model_enabled_in_production=False, result=status())
    except (ValueError, KeyError, OSError):
        return jsonify(error="Frozen research integrity checks failed; forecasting remains blocked."), 503


@nfl_api_bp.route("/api/nfl/research/winner/forecasts/evaluation", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@limiter.limit("10 per minute")
def api_nfl_pregame_sealed():
    if request.method not in {"GET", "HEAD"}:
        return jsonify(error="Research evaluation is locked and read-only."), 405
    if not _winner_research_enabled():
        return jsonify(error="NFL winner research is disabled."), 404
    from user_auth import current_user
    if not current_user():
        return jsonify(error="Authentication required for research archives."), 401
    return jsonify(error="Holdout outcomes remain sealed. Explicit review authorization is required on or after March 1, 2027.", evaluation_locked=True), 423


def _reserved_game(game_id):
    from services.nfl_reliability import sealed_protocol
    protocol = sealed_protocol()
    return protocol and game_id in protocol["holdout"]["game_ids"]


@nfl_api_bp.get("/api/nfl/research/winner/models")
@limiter.limit("10 per minute")
def api_nfl_winner_models():
    if not _winner_research_enabled():
        return jsonify(error="NFL winner research is disabled."), 404
    from services.nfl_winner_research import registry
    return jsonify(research_only=True, model_enabled_in_production=False, registry=registry())


@nfl_api_bp.get("/api/nfl/research/winner/evaluations/<run_id>")
@limiter.limit("10 per minute")
def api_nfl_winner_evaluation(run_id):
    if not _winner_research_enabled():
        return jsonify(error="NFL winner research is disabled."), 404
    from services.nfl_winner_research import evaluation
    try:
        return jsonify(research_only=True, result=evaluation(run_id))
    except (ValueError, KeyError, OSError):
        return jsonify(error="NFL research evaluation is unavailable."), 404


@nfl_api_bp.get("/api/nfl/research/winner/<game_id>")
@limiter.limit("5 per minute")
def api_nfl_winner_prediction(game_id):
    if not _winner_research_enabled():
        return jsonify(error="NFL winner research is disabled."), 404
    if _reserved_game(game_id):
        return jsonify(error="Reserved games use only the immutable offline archive; outcomes remain sealed."), 423
    from services.nfl_winner_research import predict
    model_id = request.args.get("model_id")
    if not model_id or request.args.get("as_of"):
        return jsonify(error="Select a registered model_id. Historical predictions are available only in backtest reports."), 400
    try:
        return jsonify(result=predict(game_id, model_id))
    except (ValueError, KeyError, OSError, TypeError):
        return jsonify(error="NFL research prediction is unavailable for this game, model or cutoff."), 422


@nfl_api_bp.get("/api/nfl/research/coverage")
@limiter.limit("10 per minute")
def api_nfl_research_coverage():
    if os.getenv("NFL_ADVANCED_RESEARCH_ENABLED", "0") != "1":
        return jsonify(error="Advanced NFL research is disabled."), 404
    from services import nflverse_store
    return jsonify(datasets=nflverse_store.metadata(), refresh=nflverse_store.refresh_status(),
                   collection=nflverse_store.collection_status(), research_only=True)


@nfl_api_bp.get("/api/nfl/research/matchups/<game_id>")
@limiter.limit("10 per minute")
def api_nfl_research_matchup(game_id):
    """Opt-in local analytics; no change to existing analysis or decisions."""
    if os.getenv("NFL_ADVANCED_RESEARCH_ENABLED", "0") != "1":
        return jsonify(error="Advanced NFL research is disabled."), 404
    if _reserved_game(game_id):
        return jsonify(error="Reserved holdout game research remains sealed."), 423
    from services.nfl_features import research_features
    try:
        raw = request.args.get("as_of")
        as_of = datetime.fromisoformat(raw) if raw else None
        return jsonify(result=research_features(game_id, as_of=as_of))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@nfl_api_bp.get("/api/nfl/matches/<match_id>/props")
@limiter.limit("30 per minute")
def api_nfl_props(match_id):
    """Pregame player prop projections for a real event on the selected slate."""
    try:
        selected = selected_date()
    except ValueError:
        return {"error": "Invalid date."}, 400
    payload, status = nfl_props_payload(match_id, selected)
    return jsonify(payload), status


def nfl_props_payload(match_id: str, selected) -> tuple[dict, int]:
    """({"result": ...} or {"error": ...}, HTTP status) for one slate game; shared with the best-props board."""
    try:
        payload, records = nfl_service.load_slate(selected)
        if not any(item["matchId"] == match_id for item in payload["slate"]):
            return {"error": "NFL match not found on this slate."}, 404
        game = next(game for game in records if game["id"] == match_id)
        if game.get("nflverse_id") and _reserved_game(game["nflverse_id"]):
            return {"error": "Reserved holdout game research remains sealed."}, 423
        from services import nfl_props
        now = datetime.now(UTC)
        source_freshness = nfl_service.prediction_freshness(game, records, now)
        if reason := unavailable_reason(game["kickoff"], game.get("status_name"), source_freshness, now):
            return {"error": reason, "dataFreshness": source_freshness}, 422
        try:
            result = nfl_props.project(game, records)
        except nfl_props.PropsUnavailable as exc:
            return {"error": str(exc)}, 422
        from services import prop_markets
        result = prop_markets.compare(result, "nfl", game.get("home_name"), game.get("away_name"), game["kickoff"])
        # Projections are pregame only; recheck in case kickoff passed meanwhile.
        if reason := unavailable_reason(game["kickoff"], game.get("status_name"), source_freshness, datetime.now(UTC)):
            return {"error": reason, "dataFreshness": source_freshness}, 422
        result["data_freshness"] = source_freshness
        return {"result": result}, 200
    except NFLProviderError:
        return {"error": "NFL data is temporarily unavailable."}, 503
    except Exception:
        current_app.logger.exception("NFL player props failed")
        return {"error": "NFL data is temporarily unavailable."}, 503


@nfl_api_bp.get("/api/nfl/matches/<match_id>/injuries")
@limiter.limit("30 per minute")
def api_nfl_injuries(match_id):
    """ESPN and official injury reports for a slate game, priced as win-chance changes."""
    try:
        selected = selected_date()
    except ValueError:
        return jsonify(error="Invalid date."), 400
    from services import injury_impact, injury_reports
    try:
        payload, records = nfl_service.load_slate(selected)
        if not any(item["matchId"] == match_id for item in payload["slate"]):
            return jsonify(error="NFL match not found on this slate."), 404
        game = next(game for game in records if game["id"] == match_id)
        if not game.get("nflverse_id"):
            return jsonify(error="Injury impact needs the local nflverse store; this game came from the ESPN fallback."), 422
        if _reserved_game(game["nflverse_id"]):
            return jsonify(error="Reserved holdout game research remains sealed."), 423
        now = datetime.now(UTC)
        analysis = nfl_service.analyze(game, records, now)
        if analysis is None:
            return jsonify(error="Injury impact needs an upcoming game with fresh data and three completed games per team."), 422
        warnings, sources = [], []
        try:
            espn_rows, espn_freshness = injury_reports.espn("nfl")
            sources.append(espn_freshness)
        except injury_reports.InjuryFeedUnavailable:
            espn_rows = []
            warnings.append("ESPN injuries are unavailable; showing the latest synced official report only.")
        players = injury_impact.nfl_players(game, records, espn_rows, now)
        # Recent-scoring margin plus home field (the intercept in scripts/backtest_nfl_injuries.py).
        base = analysis["projected_score"]["home"] - analysis["projected_score"]["away"] + (0.0 if game.get("neutral") else 1.5)
        report = injury_impact.margin_report(base, injury_impact.NFL_SIGMA, players, priced=False)
        result = injury_impact.summarize(report, {"home": game["home_name"], "away": game["away_name"]}, unit="points")
        if reason := unavailable_reason(game["kickoff"], game.get("status_name"), analysis.get("data_freshness"), datetime.now(UTC)):
            return jsonify(error=reason), 422
        result.update(sources=sources, warnings=warnings, base="recent scoring",
                      notice="Win chances come from recent scoring plus home field, adjusted for who is expected to play. "
                             "Player values use EPA (quarterbacks, running backs, receivers); linemen and defenders are listed but not valued.")
        return jsonify(result=result)
    except NFLProviderError:
        return jsonify(error="NFL data is temporarily unavailable."), 503
    except Exception:
        current_app.logger.exception("NFL injury impact failed")
        return jsonify(error="NFL data is temporarily unavailable."), 503


def selected_date():
    raw = request.args.get("date")
    if not raw:
        return None
    parsed = date.fromisoformat(raw)
    if parsed.isoformat() != raw or not datetime.now(UTC).year - 1 <= parsed.year <= datetime.now(UTC).year + 1:
        raise ValueError("Date outside supported range")
    return parsed


@nfl_api_bp.get("/api/dashboard/nfl")
@limiter.limit("30 per minute")
def api_dashboard_nfl():
    try:
        selected = selected_date()
    except ValueError:
        return jsonify(error="Use an ISO date (YYYY-MM-DD) within the previous, current or next year."), 400
    try:
        payload, _ = nfl_service.load_slate(selected)
        return jsonify(payload)
    except NFLProviderError:
        return jsonify(error="NFL data is temporarily unavailable."), 503
    except Exception:
        current_app.logger.exception("NFL dashboard failed")
        return jsonify(error="NFL data is temporarily unavailable."), 503


@nfl_api_bp.get("/api/nfl/matches/<match_id>/analysis")
@limiter.limit("30 per minute")
def api_nfl_analysis(match_id):
    try:
        selected = selected_date()
    except ValueError:
        return jsonify(error="Invalid date."), 400
    try:
        payload, records = nfl_service.load_slate(selected)
        # Bind the analysis to a real event on the selected slate, including
        # neutral-site context and kickoff, instead of arbitrary team IDs.
        decision = next((item for item in payload["slate"] if item["matchId"] == match_id), None)
        if decision is None:
            return jsonify(error="NFL match not found on this slate."), 404
        game = next(game for game in records if game["id"] == match_id)
        now = datetime.now(UTC)
        source_freshness = nfl_service.prediction_freshness(game, records, now)
        reason = unavailable_reason(game["kickoff"], game.get("status_name"), source_freshness, now)
        if reason:
            return jsonify(error=reason, dataFreshness=source_freshness), 422
        result = nfl_service.analyze(game, records, now)
        if result is None:
            return jsonify(error="NFL scoring analysis needs an upcoming game and at least three completed current-season games per team."), 422
        from services import scoring_research
        from services.nfl_features import build_features
        result["research"] = {"available": False, "reason": "Research scoring requires a synchronized nflverse feature store."}
        if game.get("data_source") == "nflverse":
            from services import nflverse_provider
            stats_freshness = nflverse_provider.dataset_freshness("team_stats", game["season"], now=now)
            result["research"] = {"available": False, "reason": "NFL team statistics need a fresh offline sync before research scoring is available.",
                                  "data_freshness": stats_freshness}
            if freshness_state(stats_freshness, now) == "fresh":
                feature = next((row for row in build_features(now=now) if row["game_id"] == game.get("nflverse_id")), None)
                if feature:
                    result["research"] = scoring_research.project("nfl", feature, before=game["kickoff"], now=now)
                    result["research"]["data_freshness"] = stats_freshness
        # A slow feature/model read must not return a pregame forecast after
        # kickoff. Recheck the event and the same observed source deadlines.
        finished = datetime.now(UTC)
        reason = unavailable_reason(game["kickoff"], game.get("status_name"), source_freshness, finished)
        if reason:
            return jsonify(error=reason, dataFreshness=source_freshness), 422
        if result.get("research", {}).get("available"):
            stats_freshness = result["research"].get("data_freshness")
            if freshness_state(stats_freshness, finished) != "fresh":
                result["research"] = {"available": False, "reason": "NFL team statistics expired during calculation. Refresh after the next sync."}
            else:
                result["data_freshness"] = worst([source_freshness, stats_freshness], now=finished)
        result["market"] = decision.get("market")
        return jsonify(result=result)
    except NFLProviderError:
        return jsonify(error="NFL data is temporarily unavailable."), 503
    except Exception:
        current_app.logger.exception("NFL scoring analysis failed")
        return jsonify(error="NFL data is temporarily unavailable."), 503
