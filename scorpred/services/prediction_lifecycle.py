"""Pregame eligibility, source-age checks and one calendar for every sport."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

CALENDAR_TIMEZONE = "America/New_York"
CALENDAR_ZONE = ZoneInfo(CALENDAR_TIMEZONE)


def parse_kickoff(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def calendar_date(value):
    parsed = parse_kickoff(value)
    return parsed.astimezone(CALENDAR_ZONE).date().isoformat() if parsed else ""


def is_pregame(kickoff, status=None, now=None):
    parsed = parse_kickoff(kickoff)
    if parsed is None or parsed <= (now or datetime.now(UTC)):
        return False
    if isinstance(status, dict):
        if status.get("completed"):
            return False
        fields = [status.get(key) for key in ("state", "short", "name", "long") if status.get(key)]
        return all(is_pregame(parsed, value, now) for value in fields)
    if status is None or status == "":
        return True  # Serialized legacy cards still have a mandatory future kickoff.
    return str(status).strip().upper() in {"PRE", "NS", "SCHEDULED", "STATUS_SCHEDULED", "NOT STARTED", "TIMED"}


def freshness_state(metadata, now=None):
    if not isinstance(metadata, dict) or not metadata:
        return "unknown"
    now = now or datetime.now(UTC)
    observed = parse_kickoff(metadata.get("observed_at"))
    expires = parse_kickoff(metadata.get("expires_at"))
    maximum = parse_kickoff(metadata.get("max_stale_at")) or expires
    # Windows wall-clock APIs can differ by sub-millisecond resolution. Permit
    # one second of observation-clock jitter, never enough to extend a TTL.
    if observed is None or (observed - now).total_seconds() > 1 or maximum is None or maximum <= now:
        return "unavailable"
    if metadata.get("state") != "fresh" or expires is None or expires <= now:
        return "degraded"
    return "fresh"


def unavailable_reason(kickoff, status=None, metadata=None, now=None):
    if not is_pregame(kickoff, status, now):
        return "Pregame recommendations are unavailable after kickoff or when the game is not scheduled."
    if freshness_state(metadata, now) != "fresh":
        return "Pregame recommendations are unavailable because their source data needs a fresh update."
    return None


def sanitize_decision(card, now=None):
    """Downgrade expired recommendations without changing a valid engine action."""
    result = dict(card)
    now = now or datetime.now(UTC)
    market = result.get("market")
    if isinstance(market, dict):
        updated = parse_kickoff(market.get("updated_at"))
        if not updated or not 0 <= (now - updated).total_seconds() < 900 or (market.get("data_freshness") and freshness_state(market["data_freshness"], now) != "fresh"):
            result["market"] = None
    serialized = "matchId" in result or "teamA" in result
    kickoff = result.get("matchDate") if serialized else result.get("game_date") or result.get("match_date") or result.get("kickoff")
    status = result.get("eventStatus") if serialized else result.get("event_status") or result.get("status")
    metadata = result.get("dataFreshness") if serialized else result.get("data_freshness")
    reason = unavailable_reason(kickoff, status, metadata, now)
    result["pregameAvailable" if serialized else "pregame_available"] = reason is None
    if not reason:
        if serialized:
            from services.ano_data_quality import guard
            return guard(result,now)
        return result
    result.update(action="SKIP", reason=reason, market=None)
    if serialized:
        result.update(side="Unavailable", confidence=0, winProbability=None, drawProbability=None, scenarioShare=None,
                      tiedScenarioShare=None, data="Limited Data", recommendationEligible=False, researchPick=False)
    else:
        result.update(recommended_side="Unavailable", confidence=0, confidence_pct=0, probabilities={},
                      data_quality="Limited Data", data_confidence={"label": "Limited Data"})
        if isinstance(result.get("prediction"), dict):
            result["prediction"] = {"action": "SKIP", "confidence_pct": 0, "best_pick": {}, "win_probabilities": {}, "reason": reason}
    return result
