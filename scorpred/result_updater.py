"""
result_updater.py — Automatic game result fetcher and prediction updater.

Fetches completed game results from API and updates pending predictions
in the tracking file to mark them correct or incorrect.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import api_client as ac
import nba_live_client as nc
import model_tracker as mt
from league_config import CURRENT_SEASON
from utils.parsing import normalize_date, normalize_team_name as _shared_normalize_team_name


def _normalize_team_name(name: str) -> str:
    """Normalize team name for tolerant result matching."""
    return _shared_normalize_team_name(
        name,
        ignored_tokens={"fc", "cf", "sc", "afc", "club", "united", "city"},
    )


def _teams_match(team_a: str, team_b: str) -> bool:
    """Check if two team names match after normalization."""
    norm_a = _normalize_team_name(team_a)
    norm_b = _normalize_team_name(team_b)
    
    if not norm_a or not norm_b:
        return False
    
    # Exact match
    if norm_a == norm_b:
        return True
    
    # Partial match (one contains the other)
    if norm_a in norm_b or norm_b in norm_a:
        return True
    
    return False


def _parse_date(date_str: str) -> str:
    """Extract YYYY-MM-DD from various date formats."""
    return normalize_date(date_str) or ""


def _team_ids_match(
    team_a_id: str | int | None,
    team_b_id: str | int | None,
    home_id: str | int | None,
    away_id: str | int | None,
) -> bool:
    if team_a_id is None or team_b_id is None:
        return False
    return str(team_a_id) == str(home_id) and str(team_b_id) == str(away_id)


def fetch_soccer_result(
    team_a: str,
    team_b: str,
    date_str: str,
    league_id: int = 39,
    season: int = 2025,
    team_a_id: str | int | None = None,
    team_b_id: str | int | None = None,
) -> dict[str, Any] | None:
    """
    Fetch the actual result of a soccer match.
    
    Args:
        team_a: Team A name (from prediction)
        team_b: Team B name (from prediction)
        date_str: Date in YYYY-MM-DD format
        league_id: competition ID for the tracked prediction
        season: season year
    
    Returns:
        {
            "status": "FT|AET|PEN|...",
            "fixture_id": int,
            "score": {"a": int, "b": int},
            "winner": "A|B|D",
            "teams": {"a": str, "b": str},
            "found": True
        }
        or None if not found
    """
    target_date = _parse_date(date_str)
    fixtures = []

    # Attempt 1: ESPN league scoreboard (returns all events including completed)
    try:
        slug_map = getattr(ac, "ESPN_SLUG_BY_LEAGUE", {})
        espn_slug = slug_map.get(league_id, "eng.1")
        fixtures = ac.get_espn_fixtures(espn_slug, next_n=50)
    except Exception:
        fixtures = []

    # Attempt 2: API-Football recent fixtures for the league
    if not fixtures:
        try:
            fixtures = ac.get_fixtures_by_league(league_id, season, last=20)
        except Exception:
            fixtures = []
    
    # Attempt 3: Try yesterday's fixtures in case some finished late
    if not fixtures:
        try:
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            fixtures = ac.get_fixtures_by_date(yesterday, league_id, season)
        except Exception:
            pass
    
    for fixture in fixtures:
        try:
            fixture_date = _parse_date((fixture.get("fixture") or {}).get("date", ""))
            if fixture_date != target_date:
                continue
            
            # Check if game is finished
            status = ((fixture.get("fixture") or {}).get("status") or {}).get("short", "")
            if status not in ("FT", "AET", "PEN"):
                continue
            
            # Get team names and scores
            h_name = (fixture.get("teams") or {}).get("home", {}).get("name", "")
            a_name = (fixture.get("teams") or {}).get("away", {}).get("name", "")
            h_id = (fixture.get("teams") or {}).get("home", {}).get("id")
            a_id = (fixture.get("teams") or {}).get("away", {}).get("id")
            h_goals = fixture.get("goals", {}).get("home")
            a_goals = fixture.get("goals", {}).get("away")
            
            home_match = _team_ids_match(team_a_id, team_b_id, h_id, a_id) or (
                _teams_match(team_a, h_name) and _teams_match(team_b, a_name)
            )
            away_match = _team_ids_match(team_a_id, team_b_id, a_id, h_id) or (
                _teams_match(team_a, a_name) and _teams_match(team_b, h_name)
            )

            # Check if teams match (try both orderings)
            if home_match:
                # team_a is home
                if h_goals is None or a_goals is None:
                    continue
                
                if h_goals > a_goals:
                    winner = "A"
                elif a_goals > h_goals:
                    winner = "B"
                else:
                    winner = "draw"
                
                return {
                    "status": status,
                    "fixture_id": (fixture.get("fixture") or {}).get("id"),
                    "score": {"a": h_goals, "b": a_goals},
                    "winner": winner,
                    "teams": {"a": h_name, "b": a_name},
                    "found": True,
                }
            
            elif away_match:
                # team_a is away
                if h_goals is None or a_goals is None:
                    continue
                
                if a_goals > h_goals:
                    winner = "A"
                elif h_goals > a_goals:
                    winner = "B"
                else:
                    winner = "draw"
                
                return {
                    "status": status,
                    "fixture_id": (fixture.get("fixture") or {}).get("id"),
                    "score": {"a": a_goals, "b": h_goals},
                    "winner": winner,
                    "teams": {"a": a_name, "b": h_name},
                    "found": True,
                }
        except Exception:
            continue
    
    return None


def _candidate_nba_scoreboard_dates(date_str: str) -> list[datetime]:
    candidates: list[datetime] = []
    target_date = _parse_date(date_str)
    if target_date:
        try:
            base = datetime.fromisoformat(target_date)
        except ValueError:
            base = None
        if base is not None:
            candidates.extend([base - timedelta(days=1), base, base + timedelta(days=1)])

    today = datetime.now()
    candidates.extend([today - timedelta(days=1), today, today + timedelta(days=1)])

    unique: list[datetime] = []
    seen_dates: set[str] = set()
    for candidate in candidates:
        key = candidate.strftime("%Y-%m-%d")
        if key not in seen_dates:
            seen_dates.add(key)
            unique.append(candidate)
    return unique


def fetch_nba_result(
    team_a: str,
    team_b: str,
    date_str: str,
    team_a_id: str | int | None = None,
    team_b_id: str | int | None = None,
) -> dict[str, Any] | None:
    """
    Fetch the actual result of an NBA game.
    
    Args:
        team_a: Team A name (from prediction)
        team_b: Team B name (from prediction)
        date_str: Date in YYYY-MM-DD format
    
    Returns:
        {
            "status": "Final",
            "fixture_id": str,
            "score": {"a": int, "b": int},
            "winner": "A|B",
            "teams": {"a": str, "b": str},
            "found": True
        }
        or None if not found
    """
    all_games = []

    for candidate in _candidate_nba_scoreboard_dates(date_str):
        try:
            all_games.extend(nc.get_scoreboard_games(candidate))
        except Exception:
            continue
    
    for game in all_games:
        try:
            game_date = _parse_date((game.get("date") or {}).get("start", ""))
            target_date = _parse_date(date_str)
            if game_date != target_date:
                continue
            
            # Check if game is finished
            status = game.get("status") or {}
            status_state = str(status.get("state", "")).lower()
            status_long = str(status.get("long", "")).upper()
            if status_state != "post" and "FINAL" not in status_long:
                continue
            
            # Get team names
            teams = game.get("teams") or {}
            scores = game.get("scores") or {}
            home = teams.get("home") or {}
            away = teams.get("visitors") or {}
            home_name = home.get("name") or home.get("nickname", "")
            away_name = away.get("name") or away.get("nickname", "")
            home_id = home.get("id")
            away_id = away.get("id")
            
            home_score = scores.get("home", {}).get("points")
            away_score = scores.get("visitors", {}).get("points")
            if home_score is None or away_score is None:
                continue
            home_score = int(home_score)
            away_score = int(away_score)

            home_match = _team_ids_match(team_a_id, team_b_id, home_id, away_id) or (
                _teams_match(team_a, home_name) and _teams_match(team_b, away_name)
            )
            away_match = _team_ids_match(team_a_id, team_b_id, away_id, home_id) or (
                _teams_match(team_a, away_name) and _teams_match(team_b, home_name)
            )
            
            # Check if teams match (try both orderings)
            if home_match:
                # team_a is home
                if home_score > away_score:
                    winner = "A"
                else:
                    winner = "B"

                return {
                    "status": status_long or "FINAL",
                    "score": {"a": home_score, "b": away_score},
                    "winner": winner,
                    "teams": {"a": home_name, "b": away_name},
                    "found": True,
                    "fixture_id": str(game.get("id", "")),
                }

            elif away_match:
                # team_a is away
                if away_score > home_score:
                    winner = "A"
                else:
                    winner = "B"

                return {
                    "status": status_long or "FINAL",
                    "score": {"a": away_score, "b": home_score},
                    "winner": winner,
                    "teams": {"a": away_name, "b": home_name},
                    "found": True,
                    "fixture_id": str(game.get("id", "")),
                }
        except Exception:
            continue
    
    return None


# A game is worth checking for a result this long after kickoff.
RESULT_DELAY = timedelta(minutes=110)


def _started(pred: dict, now: datetime) -> bool:
    kickoff = mt._aware_datetime(pred.get("kickoff_at"))
    if kickoff is not None:
        return kickoff + RESULT_DELAY <= now
    day = str(pred.get("game_date") or pred.get("date") or "")[:10]
    return bool(day) and day < now.date().isoformat()


def update_pending_predictions(started_only: bool = False) -> dict[str, Any]:
    """
    Go through all pending predictions and try to fetch their actual results.
    
    Returns:
        {
            "checked": int,  # How many pending predictions were checked
            "found": int,    # How many actual results were found
            "updated": int,  # How many predictions were updated
            "failed": int,   # How many API calls failed
            "errors": [str], # Error messages
        }
    """
    predictions = mt._load_predictions()
    
    if not predictions:
        return {
            "checked": 0,
            "found": 0,
            "updated": 0,
            "failed": 0,
            "errors": [],
        }
    
    # Filter to pending predictions only (status != completed)
    pending = [p for p in predictions if p.get("status") != "completed"]
    if started_only:
        now = datetime.now(timezone.utc)
        pending = [p for p in pending if _started(p, now)]
    
    stats = {
        "checked": len(pending),
        "found": 0,
        "updated": 0,
        "failed": 0,
        "errors": [],
    }
    
    for pred in pending:
        sport = pred.get("sport", "").lower()
        team_a = pred.get("team_a", "")
        team_b = pred.get("team_b", "")
        date_str = pred.get("game_date") or pred.get("date", "")
        pred_id = pred.get("id", "")
        team_a_id = pred.get("team_a_id")
        team_b_id = pred.get("team_b_id")
        league_id = pred.get("league_id") or 39
        season = pred.get("season") or CURRENT_SEASON
        
        result = None

        if not date_str:
            stats["errors"].append(f"{pred_id} ({team_a} vs {team_b}): Missing stored game date")
            continue
        
        try:
            if sport == "soccer":
                result = fetch_soccer_result(
                    team_a,
                    team_b,
                    date_str,
                    league_id=int(league_id),
                    season=int(season),
                    team_a_id=team_a_id,
                    team_b_id=team_b_id,
                )
            elif sport == "nba":
                result = fetch_nba_result(
                    team_a,
                    team_b,
                    date_str,
                    team_a_id=team_a_id,
                    team_b_id=team_b_id,
                )
            else:
                stats["errors"].append(f"Unknown sport: {sport}")
                continue
        except Exception as e:
            stats["failed"] += 1
            stats["errors"].append(f"{pred_id} ({team_a} vs {team_b}): {str(e)[:60]}")
            continue
        
        if result and result.get("found"):
            stats["found"] += 1
            actual_winner = result.get("winner", "")
            final_score = result.get("score", None)
            fixture_id = result.get("fixture_id")
            
            # Update the prediction with result and final score
            if mt.update_prediction_result(pred_id, actual_winner, final_score, fixture_id=fixture_id):
                stats["updated"] += 1
        else:
            # Log why the result wasn't found
            stats["errors"].append(f"{pred_id} ({team_a} vs {team_b} on {date_str}): No result found - game may not be finished yet")
    
    return stats


def get_update_summary() -> dict[str, Any]:
    """
    Get summary of pending vs. completed predictions.
    
    Returns:
        {
            "pending": int,
            "completed": int,
            "total": int,
            "completion_rate": float,  # percent
        }
    """
    predictions = mt._load_predictions()
    
    total = len(predictions)
    completed = sum(1 for p in predictions if p.get("status") == "completed")
    pending = total - completed
    
    completion_rate = (completed / total * 100) if total > 0 else 0
    
    return {
        "pending": pending,
        "completed": completed,
        "total": total,
        "completion_rate": round(completion_rate, 1),
    }
