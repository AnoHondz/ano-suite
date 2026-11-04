"""Completed-game player box scores from ESPN for NBA and soccer, cached permanently.

A finished game's box score never changes, so each is fetched once and kept as compact
rows: per player, minutes, starter flag and counted statistics. Soccer minutes come from
the match's substitution events (starters play 90 unless replaced).
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from runtime_paths import cache_dir
from services import injury_reports
from services.prediction_lifecycle import parse_kickoff

BASE = "https://site.api.espn.com/apis/site/v2/sports/{path}"
SCHEDULE_FRESH_SECONDS = 3600
NBA_FIELDS = {"points": "points", "rebounds": "rebounds", "assists": "assists", "steals": "steals", "blocks": "blocks",
              "turnovers": "turnovers"}
SOCCER_FIELDS = {"totalShots": "shots", "shotsOnTarget": "shots_on_target", "totalGoals": "goals",
                 "goalAssists": "assists", "foulsCommitted": "fouls", "saves": "saves", "offsides": "offsides"}
_MINUTE = re.compile(r"(\d+)")


def path(sport, league=None):
    return "basketball/nba" if sport == "nba" else f"soccer/{league}"


def team_events(sport, team_id, season, league=None):
    """Completed regular-season (NBA) or league (soccer) events for one team, oldest first."""
    url = BASE.format(path=f"{path(sport, league)}/teams/{team_id}/schedule") + f"?season={season}"
    payload, _fetched, _stale = injury_reports._fetch(f"schedule-{sport}-{league}-{team_id}-{season}", url,
                                                      fresh_seconds=SCHEDULE_FRESH_SECONDS)
    events = []
    for event in payload.get("events") or []:
        competition = (event.get("competitions") or [{}])[0]
        status = (competition.get("status") or event.get("status") or {}).get("type") or {}
        if sport == "nba" and (event.get("seasonType") or {}).get("type") not in (2, 3):
            continue
        if status.get("completed"):
            events.append({"id": str(event["id"]), "date": event.get("date")})
    return sorted(events, key=lambda e: e["date"] or "")


def _number(text):
    try:
        return float(str(text).split("-")[0].replace("+", ""))
    except (TypeError, ValueError):
        return None


def _nba_rows(summary):
    teams = {}
    competitors = (summary.get("header") or {}).get("competitions", [{}])[0].get("competitors") or []
    sides = {str(c["team"]["id"]): c.get("homeAway") == "home" for c in competitors if c.get("team")}
    for block in (summary.get("boxscore") or {}).get("players") or []:
        team_id = str(block["team"]["id"])
        stats = (block.get("statistics") or [{}])[0]
        keys = stats.get("keys") or []
        players = []
        for athlete in stats.get("athletes") or []:
            values = dict(zip(keys, athlete.get("stats") or []))
            minutes = _number(values.get("minutes"))
            if athlete.get("didNotPlay") or not minutes:
                continue
            row = {"id": str(athlete["athlete"]["id"]), "name": athlete["athlete"].get("displayName") or "",
                   "position": (athlete["athlete"].get("position") or {}).get("abbreviation") or "",
                   "starter": bool(athlete.get("starter")), "minutes": minutes}
            row.update({name: _number(values.get(key)) for key, name in NBA_FIELDS.items()})
            row["threes"] = _number(values.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
            for key,field in (('fieldGoalsMade-fieldGoalsAttempted','fga'),('threePointFieldGoalsMade-threePointFieldGoalsAttempted','fg3a'),('freeThrowsMade-freeThrowsAttempted','fta')):
                parts=str(values.get(key,'')).split('-')
                row[field]=_number(parts[1]) if len(parts)==2 else None
            players.append(row)
        teams[team_id] = {"home": sides.get(team_id, False), "players": players}
    return teams


def _soccer_rows(summary):
    replaced, entered = {}, {}
    for event in summary.get("keyEvents") or []:
        if (event.get("type") or {}).get("type") != "substitution":
            continue
        minute = min(90.0, ((event.get("clock") or {}).get("value") or 0) / 60)
        people = [p["athlete"]["id"] for p in event.get("participants") or [] if p.get("athlete")]
        if len(people) == 2:
            entered[str(people[0])], replaced[str(people[1])] = minute, minute
    competitors = (summary.get("header") or {}).get("competitions", [{}])[0].get("competitors") or []
    sides = {str(c["team"]["id"]): c.get("homeAway") == "home" for c in competitors if c.get("team")}
    teams = {}
    for roster in summary.get("rosters") or []:
        team_id = str(roster["team"]["id"])
        players = []
        for entry in roster.get("roster") or []:
            athlete_id = str(entry["athlete"]["id"])
            if entry.get("starter"):
                minutes = replaced.get(athlete_id, 90.0)
            elif entry.get("subbedIn") and athlete_id in entered:
                minutes = 90.0 - entered[athlete_id]
            else:
                continue
            values = {s["name"]: s.get("value") for s in entry.get("stats") or []}
            row = {"id": athlete_id, "name": entry["athlete"].get("displayName") or "",
                   "position": (entry.get("position") or {}).get("abbreviation") or "",
                   "starter": bool(entry.get("starter")), "minutes": max(minutes, 1.0)}
            row.update({name: _number(values.get(key)) for key, name in SOCCER_FIELDS.items()})
            players.append(row)
        teams[team_id] = {"home": sides.get(team_id, False), "players": players}
    return teams


def box_score(sport, event_id, league=None):
    """Compact rows for a completed game, or None when ESPN has no final box score."""
    target = cache_dir("box_scores") / f"{sport}-{league or 'nba'}-{event_id}.json"
    if target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    url = BASE.format(path=f"{path(sport, league)}/summary")
    for attempt in range(3):
        try:
            response = requests.get(url, params={"event": event_id}, timeout=(4, 20))
            response.raise_for_status()
            summary = response.json()
            break
        except (requests.RequestException, ValueError):
            if attempt == 2:
                return None
            time.sleep(1 + attempt)
    status = (summary.get("header") or {}).get("competitions", [{}])[0].get("status", {}).get("type", {})
    if not status.get("completed"):
        return None
    teams = _nba_rows(summary) if sport == "nba" else _soccer_rows(summary)
    if len(teams) != 2 or not all(t["players"] for t in teams.values()):
        return None
    ids = list(teams)
    for team_id, other in zip(ids, reversed(ids)):
        teams[team_id]["opponent"] = other
    date = (summary.get("header") or {}).get("competitions", [{}])[0].get("date")
    result = {"id": str(event_id), "date": date, "teams": teams}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result), encoding="utf-8")
    return result


def team_games(sport, team_id, seasons, league=None, before=None):
    """Box scores for a team's completed games in ``seasons`` before ``before``, oldest first."""
    cutoff = parse_kickoff(before) if before is not None else None
    events = [e for season in seasons for e in team_events(sport, team_id, season, league)
              if cutoff is None or ((start := parse_kickoff(e["date"])) is not None and start < cutoff)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        games = list(pool.map(lambda e: box_score(sport, e["id"], league), events))
    return [g for g in games if g and str(team_id) in g["teams"]]
