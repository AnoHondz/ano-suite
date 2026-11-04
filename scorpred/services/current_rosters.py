"""Current membership is verified independently of historical player statistics."""
from collections import Counter
from datetime import UTC, datetime

from services import injury_reports
from services.data_freshness import metadata
from services.prediction_lifecycle import freshness_state

FRESH_SECONDS = 600
PATHS = {"nba": "basketball/nba", "nfl": "football/nfl"}
BASE = "https://site.api.espn.com/apis/site/v2/sports"


class RosterUnavailable(RuntimeError):
    pass


def teams(sport, league=None, *, refresh=False):
    path = f"soccer/{league}" if sport == "soccer" else PATHS[sport]
    payload, fetched, stale = injury_reports._fetch(f"current-teams-{sport}-{league or sport}",
        f"{BASE}/{path}/teams?limit=100", fresh_seconds=FRESH_SECONDS, refresh=refresh)
    if stale or datetime.now(UTC).timestamp() - fetched > FRESH_SECONDS:
        raise RosterUnavailable("Current team directory needs a fresh provider observation.")
    try:
        return [entry["team"] for entry in payload["sports"][0]["leagues"][0]["teams"]]
    except (KeyError, IndexError, TypeError) as exc:
        raise RosterUnavailable("Current team directory could not be verified.") from exc


def current(sport, team_id, league=None, *, refresh=False):
    team_id = str(team_id)
    path = f"soccer/{league}" if sport == "soccer" else PATHS[sport]
    key = f"nba-props-roster-{team_id}" if sport == "nba" else f"current-roster-{sport}-{league or sport}-{team_id}"
    try:
        payload, fetched, stale = injury_reports._fetch(key, f"{BASE}/{path}/teams/{team_id}/roster",
            fresh_seconds=FRESH_SECONDS, refresh=refresh)
    except injury_reports.InjuryFeedUnavailable as exc:
        raise RosterUnavailable(f"Current {sport.upper()} roster is unavailable; historical membership cannot replace it.") from exc
    source = metadata(f"ESPN {sport.upper()} current roster", datetime.fromtimestamp(fetched, UTC),
                      FRESH_SECONDS, FRESH_SECONDS, degraded=stale)
    if (freshness_state(source) != "fresh" or not isinstance(payload, dict)
            or not isinstance(payload.get("team"), dict) or str(payload["team"].get("id")) != team_id
            or not isinstance(payload.get("athletes"), list)):
        raise RosterUnavailable("Current roster identity or freshness could not be verified.")
    now = datetime.now(UTC)
    expected_season = now.year + (now.month >= 7) if sport == "nba" else (
        now.year - (now.month < 7) if sport == "soccer" else now.year - (now.month <= 2))
    if str((payload.get("season") or {}).get("year")) != str(expected_season):
        raise RosterUnavailable("The roster provider returned a different season.")
    rows = payload["athletes"]
    if sport == "nfl":
        if any(not isinstance(group, dict) or not isinstance(group.get("items"), list) for group in rows):
            raise RosterUnavailable("NFL roster groups could not be verified.")
        rows = [athlete for group in rows for athlete in group["items"]]
    rows = [row for row in rows if isinstance(row, dict) and row.get("id") and row.get("displayName")]
    if not rows:
        raise RosterUnavailable("Current roster contains no verified athlete identities.")
    counts = Counter(str(row["id"]) for row in rows)
    eligible = {str(row["id"]): row for row in rows if counts[str(row["id"])] == 1
                and row.get("active") is not False and not row.get("injuries")
                and isinstance(row.get("status") or {}, dict)
                and (row.get("status") or {}).get("type") in {None, "active"}}
    verification = {**source, "status": "verified", "team_ids": [team_id],
                    "season": expected_season, "roster_count": len(rows),
                    "eligible_count": len(eligible), "method": "Exact ESPN athlete ID on current-season team roster"}
    return eligible, verification


def nfl_team_id(abbreviation):
    from services.nfl_catalog import canonical_team
    matches = [team["id"] for team in teams("nfl")
               if canonical_team(team.get("abbreviation")) == canonical_team(abbreviation)]
    if len(matches) != 1:
        raise RosterUnavailable("NFL team identity could not be resolved unambiguously.")
    return str(matches[0])
