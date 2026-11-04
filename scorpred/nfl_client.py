"""Public ESPN NFL records. Cache only valid responses, with bounded requests."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import requests

from services import cache_service
from services.data_freshness import SOURCE_KEY, metadata, stamp
from services.prediction_lifecycle import freshness_state

EASTERN = ZoneInfo("America/New_York")
BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
SCHEDULE_FRESH_SECONDS = 600
SCHEDULE_MAX_SECONDS = 86400
FORM_FRESH_SECONDS = 172800


class NFLProviderError(RuntimeError):
    pass


def calendar_events(year: int) -> list[dict]:
    # ESPN's dates=YYYY selects a calendar year, not an NFL season. Date-range
    # queries are not supported consistently by this feed.
    key = cache_service.make_key("nfl", "calendar-v3-observed", year)
    cached = cache_service.get_json(key, ttl=SCHEDULE_MAX_SECONDS)
    now = datetime.now(UTC)

    def records(payload, *, degraded=False):
        source = payload.get(SOURCE_KEY) or {}
        observed = source.get("observed_at")
        schedule = metadata("ESPN NFL schedules", observed, SCHEDULE_FRESH_SECONDS,
                            SCHEDULE_MAX_SECONDS, degraded=degraded, now=now)
        form = metadata("ESPN NFL historical scores", observed, FORM_FRESH_SECONDS,
                        FORM_FRESH_SECONDS, now=now)
        return [{**event, "data_source": "espn", "schedule_freshness": schedule,
                 "form_freshness": form, "data_freshness": schedule} for event in payload["events"]]

    valid_cache = isinstance(cached, dict) and isinstance(cached.get("events"), list)
    cached_state = freshness_state(cached.get(SOURCE_KEY), now) if valid_cache else "unavailable"
    if cached_state == "fresh":
        return records(cached)
    try:
        response = requests.get(BASE_URL, params={"dates": str(year), "limit": 1000}, timeout=(4, 15))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise TypeError("Missing NFL events")
        events = [normalize_event(event) for event in payload["events"]]
        if any(event is None for event in events):
            raise ValueError("Malformed NFL event")
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError) as exc:
        if cached_state == "degraded":
            return records(cached, degraded=True)
        raise NFLProviderError("NFL data is temporarily unavailable.") from exc
    payload = stamp({"events": events}, "ESPN NFL schedules", now.isoformat(),
                    SCHEDULE_FRESH_SECONDS, SCHEDULE_MAX_SECONDS)
    cache_service.set_json(key, payload, ttl=SCHEDULE_MAX_SECONDS)
    return records(payload)


def normalize_event(event: dict) -> dict | None:
    try:
        competition = event["competitions"][0]
        sides = {item["homeAway"]: item for item in competition["competitors"]}
        home, away = sides["home"], sides["away"]
        kickoff = datetime.fromisoformat(event["date"])
        status = (competition.get("status") or event["status"])["type"]
        season = event["season"]
        if not kickoff.tzinfo or not event["id"] or home["id"] == away["id"]:
            return None
        result = {
            "id": str(event["id"]), "kickoff": kickoff.isoformat(),
            "date": kickoff.astimezone(EASTERN).date().isoformat(),
            "season": int(season["year"]), "season_type": int(season["type"]),
            "week": int((event.get("week") or {}).get("number", 0)),
            "standard": str((competition.get("type") or {}).get("id")) == "1",
            "state": status["state"], "completed": bool(status.get("completed")),
            "status": status.get("description", "Unknown"),
            "status_name": status.get("name", ""),
            "neutral": bool(competition.get("neutralSite")),
            "venue": (competition.get("venue") or {}).get("fullName", "Venue unavailable"),
        }
        for side, competitor in (("home", home), ("away", away)):
            team = competitor["team"]
            result[f"{side}_id"] = str(competitor["id"])
            result[f"{side}_name"] = team["displayName"]
            result[f"{side}_logo"] = team.get("logo", "")
            if not team["displayName"]:
                return None
            score = competitor.get("score")
            if isinstance(score, dict):
                score = score.get("value")
            result[f"{side}_score"] = int(score) if result["completed"] else None
            if result["completed"] and result[f"{side}_score"] < 0:
                return None
        return result
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None
