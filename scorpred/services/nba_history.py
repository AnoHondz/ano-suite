"""Full ESPN NBA box scores for the learned prop model, cached permanently.

Unlike services.espn_box_scores (compact rows for the rule model), each game keeps every
box-score column, the players listed out on ESPN's injury report, the players who did
not play, and the pregame spread and total when ESPN has them. Finished games never
change, so each event is fetched once. ``upcoming`` reads the same spread, total and
injury list for a game that has not started.
"""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from runtime_paths import cache_dir
from services import espn_box_scores as box
from services.prediction_lifecycle import parse_kickoff

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
SPLIT = {"fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
         "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("threes", "fg3a"),
         "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta")}
SINGLE = {"minutes": "minutes", "points": "points", "rebounds": "rebounds", "assists": "assists",
          "turnovers": "turnovers", "steals": "steals", "blocks": "blocks", "offensiveRebounds": "oreb",
          "defensiveRebounds": "dreb", "fouls": "fouls", "plusMinus": "plus_minus"}
_SESSION = requests.Session()


def target(event_id):
    return cache_dir("nba_history") / f"{event_id}.json"


def _get(url, params=None, attempts=4):
    for attempt in range(attempts):
        try:
            response = _SESSION.get(url, params=params, timeout=(5, 30))
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt == attempts - 1:
                return None
            time.sleep(2 ** attempt)


def _number(text):
    try:
        return float(str(text).replace("+", ""))
    except (TypeError, ValueError):
        return None


def _competitors(summary):
    competition = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    return competition, [c for c in competition.get("competitors") or [] if c.get("team")]


def _spread(summary, competitors):
    """Pregame point spread per team id (negative = favourite) and the total, when ESPN has them."""
    pick = next((p for p in summary.get("pickcenter") or [] if p.get("details") or p.get("overUnder")), None)
    if not pick:
        return None, None
    total = _number(pick.get("overUnder"))
    match = re.match(r"^\s*([A-Za-z]+)\s+(-?[\d.]+)\s*$", str(pick.get("details") or ""))
    if not match:
        return ({str(c["team"]["id"]): 0.0 for c in competitors} if str(pick.get("details")).upper() == "EVEN" else None), total
    favourite = match.group(1).upper()
    line = abs(float(match.group(2)))
    abbreviations = {str(c["team"].get("abbreviation") or "").upper(): str(c["team"]["id"]) for c in competitors}
    if favourite not in abbreviations:
        return None, total
    return {str(c["team"]["id"]): (-line if str(c["team"]["id"]) == abbreviations[favourite] else line)
            for c in competitors}, total


def _out(summary):
    injured = {}
    for team in summary.get("injuries") or []:
        injured[str((team.get("team") or {}).get("id"))] = [
            str(entry["athlete"]["id"]) for entry in team.get("injuries") or []
            if (entry.get("athlete") or {}).get("id") and str(entry.get("status") or "").lower() == "out"]
    return injured


def parse(summary, event_id, season):
    competition, competitors = _competitors(summary)
    if not ((competition.get("status") or {}).get("type") or {}).get("completed"):
        return None
    spreads, total = _spread(summary, competitors)
    injured = _out(summary)
    teams = {}
    for block in (summary.get("boxscore") or {}).get("players") or []:
        team_id = str(block["team"]["id"])
        stats = (block.get("statistics") or [{}])[0]
        keys = stats.get("keys") or []
        players, dnp = [], []
        for athlete in stats.get("athletes") or []:
            if not (athlete.get("athlete") or {}).get("id"):
                continue  # ESPN occasionally lists a placeholder with no player identity
            values = dict(zip(keys, athlete.get("stats") or []))
            player_id = str(athlete["athlete"]["id"])
            minutes = _number(values.get("minutes"))
            if athlete.get("didNotPlay") or not minutes:
                dnp.append(player_id)
                continue
            row = {"id": player_id, "name": athlete["athlete"].get("displayName") or "",
                   "position": (athlete["athlete"].get("position") or {}).get("abbreviation") or "",
                   "starter": bool(athlete.get("starter"))}
            row.update({name: _number(values.get(key)) for key, name in SINGLE.items()})
            for key, (made, attempted) in SPLIT.items():
                parts = str(values.get(key) or "").split("-")
                row[made], row[attempted] = (_number(parts[0]), _number(parts[1])) if len(parts) == 2 else (None, None)
            players.append(row)
        teams[team_id] = {"players": players, "dnp": dnp, "out": injured.get(team_id, [])}
    if len(teams) != 2 or not all(t["players"] for t in teams.values()):
        return None
    for competitor in competitors:
        team_id = str(competitor["team"]["id"])
        if team_id in teams:
            teams[team_id].update(home=competitor.get("homeAway") == "home", score=_number(competitor.get("score")),
                                  spread=(spreads or {}).get(team_id))
    ids = list(teams)
    for team_id, other in zip(ids, reversed(ids)):
        teams[team_id]["opponent"] = other
    season_type = ((summary.get("header") or {}).get("season") or {}).get("type")
    return {"id": str(event_id), "date": competition.get("date"), "season": season, "season_type": season_type,
            "total": total, "teams": teams}


def fetch(event_id, season):
    """'cached', 'fetched' or 'failed'; a completed game is stored once and never refetched."""
    path = target(event_id)
    if path.exists():
        return "cached"
    summary = _get(f"{BASE}/summary", {"event": event_id})
    try:
        game = parse(summary, event_id, season) if summary else None
    except (KeyError, TypeError, ValueError):
        game = None
    if game is None:
        return "failed"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(game), encoding="utf-8")
    return "fetched"


def load(event_id):
    path = target(event_id)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        return None


_LOADED: dict = {}  # file name -> game; finished games never change
_LOCK = threading.Lock()


def cached_games(seasons, before):
    """Every stored game in ``seasons`` before ``before`` (all teams), loading new files only once."""
    with _LOCK:
        for path in cache_dir("nba_history").glob("*.json"):
            if path.name not in _LOADED:
                _LOADED[path.name] = load(path.stem)
        games = list(_LOADED.values())
    cutoff = parse_kickoff(before)
    return [g for g in games if g and g.get("season") in seasons
            and (start := parse_kickoff(g["date"])) is not None and start < cutoff]


def team_games(team_id, seasons, before):
    """Full box scores of a team's completed games in ``seasons`` before ``before``, oldest first."""
    cutoff = parse_kickoff(before)
    events = [(e["id"], season) for season in seasons for e in box.team_events("nba", team_id, season)
              if (start := parse_kickoff(e["date"])) is not None and start < cutoff]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda item: fetch(*item), events))
    return [game for event_id, _season in events if (game := load(event_id))]


def upcoming(event_id):
    """Spread per team, total and players listed out for a game not yet played (empty when unavailable)."""
    summary = _get(f"{BASE}/summary", {"event": event_id}, attempts=2) or {}
    _competition, competitors = _competitors(summary)
    from datetime import UTC, datetime
    from services.data_freshness import metadata
    received=datetime.now(UTC)
    identity=str((summary.get('header') or {}).get('id') or _competition.get('id') or '')
    if identity != str(event_id):
        return {'spreads':{},'total':None,'out':{},'status':'unavailable',
                'notice':'Pregame summary identity could not be verified.'}
    spreads, total = _spread(summary, competitors) if competitors else (None, None)
    return {"spreads": spreads or {}, "total": total, "out": _out(summary),
            'status':'available' if len(spreads or {})==2 and total is not None else 'partial',
            'source':metadata('ESPN pregame summary',received,300),
            'lineup_status':'unverified','scope':'Reported spread, total and out list; not confirmed starting lineups or an injury-clearance guarantee.'}


def season_events(season):
    teams = _get(f"{BASE}/teams")["sports"][0]["leagues"][0]["teams"]
    events = {}
    for team in teams:
        schedule = _get(f"{BASE}/teams/{team['team']['id']}/schedule", {"season": season}) or {}
        for event in schedule.get("events") or []:
            status = (((event.get("competitions") or [{}])[0].get("status") or {}).get("type") or {})
            if (event.get("seasonType") or {}).get("type") in (2, 3) and status.get("completed"):
                events[str(event["id"])] = season
    return events
