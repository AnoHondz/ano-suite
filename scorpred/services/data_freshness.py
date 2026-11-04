"""Source observation times are retained across cache and fallback layers."""
import copy
from datetime import UTC, datetime, timedelta

from services.prediction_lifecycle import freshness_state, parse_kickoff

SOURCE_KEY = "_scorpred_source"


def metadata(source, observed_at, fresh_seconds, max_seconds=None, *, degraded=False, now=None):
    now = now or datetime.now(UTC)
    observed = parse_kickoff(observed_at)
    if observed is None:
        return {"source": source, "state": "unavailable", "warning": "Source observation time is unavailable."}
    result = {"source": source, "state": "degraded" if degraded else "fresh", "observed_at": observed.isoformat(),
              "expires_at": (observed + timedelta(seconds=fresh_seconds)).isoformat(),
              "max_stale_at": (observed + timedelta(seconds=max_seconds or fresh_seconds)).isoformat()}
    result["state"] = freshness_state(result, now)
    if result["state"] != "fresh":
        result["warning"] = "Source refresh is delayed; pregame recommendations are unavailable until fresh data arrives."
    return result


def stamp(payload, source, observed_at, fresh_seconds, max_seconds=None, *, degraded=False):
    result = copy.deepcopy(payload)
    if isinstance(result, dict):
        original = result.get(SOURCE_KEY)
        observed_at = original.get("observed_at") if isinstance(original, dict) else observed_at
        result[SOURCE_KEY] = metadata(source, observed_at, fresh_seconds, max_seconds, degraded=degraded)
    return result


def worst(items, now=None):
    values = [dict(item) for item in items if isinstance(item, dict) and item]
    if not values:
        return {}
    rank = {"fresh": 0, "unknown": 1, "degraded": 2, "unavailable": 3}
    for value in values:
        value["state"] = freshness_state(value, now)
    result = dict(min(values, key=lambda value: (-rank.get(value["state"], 3), value.get("observed_at") or "")))
    for key in ("observed_at", "expires_at", "max_stale_at"):
        timestamps = [parse_kickoff(value.get(key)) for value in values]
        known = [value for value in timestamps if value is not None]
        if known:
            result[key] = min(known).isoformat()
    warnings = list(dict.fromkeys(value["warning"] for value in values if value.get("warning")))
    if warnings:
        result["warning"] = " ".join(warnings)
    result["state"] = freshness_state(result, now)
    return result


def from_records(records):
    """Combine source ages without substituting retrieval time for observation time."""
    return worst([row.get("data_freshness") or row.get(SOURCE_KEY) for row in records or [] if isinstance(row, dict)])
