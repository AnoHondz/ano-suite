"""
props_engine.py — Deep statistical prop bet builder for ScorPred.

Applies a 6-layer model to every prop calculation:
  Layer 1 — Sample Collection
  Layer 2 — Core Averages
  Layer 3 — Consistency & Variance Analysis
  Layer 4 — Contextual Modifiers
  Layer 5 — Final Projection Formula
  Layer 6 — Confidence Score

Supports NBA props through the legacy nba_client compatibility layer and
soccer props through the main football api_client module.
All calculations happen server-side. Frontend receives a clean JSON payload.
Cache: cache/props/ (1-hour TTL for season data, 24-hour for historical/career)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import statistics
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from league_config import DEFAULT_LEAGUE_ID
from runtime_paths import cache_dir
from utils.parsing import safe_float as _sf

# ── Cache ──────────────────────────────────────────────────────────────────────

PROPS_CACHE_DIR = cache_dir("props")
PROPS_SEASON_TTL   = 3600          # 1 hour — current season game logs
PROPS_CAREER_TTL   = 86400         # 24 hours — historical / career vs opponent
PROPS_LIVE_TTL     = 60            # 60 seconds — live context


def _cache_path(key: str) -> Path:
    h = hashlib.md5(("verified-stat-contract-v2:" + key).encode()).hexdigest()
    PROPS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return PROPS_CACHE_DIR / f"{h}.json"


def _cache_valid(path: Path, ttl: int) -> bool:
    if not path.exists():
        return False
    import time
    return (time.time() - path.stat().st_mtime) < ttl


def _cache_load(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _cache_save(path: Path, data: Any) -> None:
    PROPS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


# ── Market configuration ───────────────────────────────────────────────────────

# NBA: (api_key, label, abbr)
NBA_MARKETS: dict[str, tuple[str, str]] = {
    "points":       ("Points",           "PTS"),
    "rebounds":     ("Rebounds",         "REB"),
    "assists":      ("Assists",          "AST"),
    "tpm":          ("3-Pointers Made",  "3PM"),
    "steals":       ("Steals",           "STL"),
    "blocks":       ("Blocks",           "BLK"),
    "turnovers":    ("Turnovers",        "TOV"),
    "pra":          ("PRA (Pts+Reb+Ast)","PRA"),
    "double_double":("Double-Double Prob","DD%"),
    "triple_double":("Triple-Double Prob","TD%"),
}

# Soccer: (label, abbr, per_90)
SOCCER_MARKETS: dict[str, tuple[str, str, bool]] = {
    # ── Attacking ──────────────────────────────────────────────────────────────
    "goals":              ("Goals",               "G",   True),
    "assists":            ("Assists",             "A",   True),
    "goal_or_assist":     ("Goal or Assist",      "G/A", True),
    "shots_total":        ("Shots Total",         "SH",  True),
    "shots_on_target":    ("Shots on Target",     "SOT", True),
    "key_passes":         ("Key Passes",          "KP",  True),
    "chances_created":    ("Chances Created",     "CC",  True),
    "dribbles":           ("Dribbles",            "DRB", True),
    "touches_opp_box":    ("Touches Opp. Box",    "TOB", True),
    "anytime_goalscorer": ("Anytime Goalscorer",  "ATG", False),
    # ── Midfield / defensive ───────────────────────────────────────────────────
    "passes_completed":   ("Passes Completed",    "PAS", False),
    "tackles":            ("Tackles",             "TKL", True),
    "interceptions":      ("Interceptions",       "INT", True),
    "clearances":         ("Clearances",          "CLR", True),
    "aerial_duels_won":   ("Aerial Duels Won",    "ADW", True),
    # ── Universal ──────────────────────────────────────────────────────────────
    "yellow_cards":       ("Yellow Cards",        "YC",  False),
    "minutes":            ("Minutes Played",      "MIN", False),
    "motm":               ("Man of Match %",      "MoM", False),
}

# API-Football fixture/players stat path tuples: (section, key) or None for computed fields
SOCCER_FIXTURE_STAT_PATH: dict[str, tuple[str, str] | None] = {
    "goals":              ("goals",    "total"),
    "assists":            ("goals",    "assists"),
    "goal_or_assist":     None,                        # computed: goals + assists
    "shots_total":        ("shots",    "total"),
    "shots_on_target":    ("shots",    "on"),
    "key_passes":         ("passes",   "key"),
    "chances_created":    None,                        # broader definition not verified
    "dribbles":           ("dribbles", "success"),
    "touches_opp_box":    None,                        # not in standard endpoint
    "anytime_goalscorer": ("goals",    "total"),       # same as goals (binary use)
    "passes_completed":   None,                        # total passes != verified completions
    "tackles":            ("tackles",  "total"),
    "interceptions":      ("tackles",  "interceptions"),
    "clearances":         None,                        # blocks are a different statistic
    "aerial_duels_won":   None,                        # total duels lack aerial separation
    "yellow_cards":       ("cards",    "yellow"),
    "minutes":            ("games",    "minutes"),
}

# Soccer season aggregate stat path (API-Football player stats response structure)
SOCCER_SEASON_STAT_PATH: dict[str, tuple[str, str] | None] = {
    "goals":              ("goals",    "total"),
    "assists":            ("goals",    "assists"),
    "goal_or_assist":     None,
    "shots_total":        ("shots",    "total"),
    "shots_on_target":    ("shots",    "on"),
    "key_passes":         ("passes",   "key"),
    "chances_created":    None,
    "dribbles":           ("dribbles", "success"),
    "touches_opp_box":    None,
    "anytime_goalscorer": ("goals",    "total"),
    "passes_completed":   None,
    "tackles":            ("tackles",  "total"),
    "interceptions":      ("tackles",  "interceptions"),
    "clearances":         None,
    "aerial_duels_won":   None,
    "yellow_cards":       ("cards",    "yellow"),
    "minutes":            ("games",    "minutes"),
}

# ── Position-aware default markets ────────────────────────────────────────────

POSITION_DEFAULT_MARKETS: dict[str, list[str]] = {
    "Attacker": [
        "goals", "shots_total", "shots_on_target", "assists",
        "goal_or_assist", "key_passes", "dribbles",
    ],
    "Midfielder": [
        "key_passes", "passes_completed", "assists", "goal_or_assist",
        "dribbles", "tackles", "interceptions",
    ],
    "Defender": [
        "tackles", "interceptions", "clearances", "aerial_duels_won",
        "passes_completed",
    ],
    "Goalkeeper": ["minutes", "yellow_cards"],
    "": ["goals", "assists", "shots_on_target", "key_passes"],
}


# ── Helper utilities ───────────────────────────────────────────────────────────

def _round_half(x: float) -> float:
    """Round to nearest 0.5."""
    return round(x * 2) / 2


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _pct(num: float, den: float) -> float:
    if den == 0:
        return 0.0
    return round(num / den * 100, 1)


def _weighted_avg(values_weights: list[tuple[float | None, float]]) -> float | None:
    """
    Weighted average excluding None values.
    Redistributes weight proportionally when data points are missing.
    """
    valid = [(v, w) for v, w in values_weights if v is not None]
    if not valid:
        return None
    total_w = sum(w for _, w in valid)
    if total_w == 0:
        return None
    return sum(v * w / total_w for v, w in valid)


def _std_dev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    try:
        return statistics.stdev(values)
    except Exception:
        return 0.0


def _parse_minutes(raw) -> float:
    """Parse '32:15' or '32' or 32 → float minutes."""
    if raw is None:
        return 0.0
    s = str(raw).strip()
    if ":" in s:
        parts = s.split(":")
        try:
            return float(parts[0]) + float(parts[1]) / 60
        except ValueError:
            return 0.0
    return _sf(s)


# ── NBA data collection ────────────────────────────────────────────────────────

def _nba_game_value(record: dict, market_key: str) -> float | None:
    """Extract a single stat value from an NBA per-game record."""
    if not record:
        return None
    stats = record.get("statistics", [{}])
    s = stats[0] if stats else {}
    if market_key in ("pra", "double_double", "triple_double"):
        pts = _stat_count(s.get("points"))
        reb = _stat_count(s.get("rebounds"))
        ast = _stat_count(s.get("assists"))
        if market_key == "pra":
            return pts + reb + ast if all(v is not None for v in (pts, reb, ast)) else None
        return None   # probability markets handled separately
    return _stat_count(s.get(market_key))


def _nba_has_played(record: dict) -> bool:
    s = (record.get("statistics") or [{}])[0]
    raw = s.get("min", "0") or "0"
    return raw not in ("0", "0:00", "", None)


def _nba_minutes(record: dict) -> float:
    s = (record.get("statistics") or [{}])[0]
    return _parse_minutes(s.get("min", "0"))


def _collect_nba_samples(
    player_id,
    player_team_id,
    opponent_team_id,
    season: int,
) -> dict:
    """
    Fetch all raw NBA data for the player.  Returns a dict with game-log lists
    and metadata, suitable for all downstream calculations.
    """
    import nba_client as nc

    cache_key = f"nba_samples_{player_id}_{player_team_id}_{opponent_team_id}_{season}"
    cpath = _cache_path(cache_key)
    if _cache_valid(cpath, PROPS_SEASON_TTL):
        return _cache_load(cpath)

    result: dict = {
        "sport":             "nba",
        "player_id":         player_id,
        "player_team_id":    player_team_id,
        "opponent_team_id":  opponent_team_id,
        "season":            season,
        "full_season_log":   [],
        "last5":             [],
        "last10":            [],
        "vs_opponent":       {"games": 0, "records": [], "averages": None, "limited_sample": True},
        "team_injuries":     [],
        "error":             None,
    }

    try:
        all_games = nc.get_player_stats(int(player_id), season)
        played = [g for g in all_games if _nba_has_played(g)]
        result["full_season_log"] = played
        result["last10"] = played[-10:] if len(played) >= 10 else played
        result["last5"]  = played[-5:]  if len(played) >= 5  else played
        logger.debug("NBA player %s season=%s games_played=%d", player_id, season, len(played))
    except Exception as e:
        logger.warning("NBA player stats fetch failed player=%s: %s", player_id, e)
        result["error"] = str(e)

    try:
        result["vs_opponent"] = nc.get_player_vs_team(
            int(player_id), int(opponent_team_id),
            int(player_team_id),
            seasons=[season - 3, season - 2, season - 1, season],
        )
    except Exception:
        pass

    try:
        result["team_injuries"] = nc.get_team_injuries(int(player_team_id))
    except Exception:
        pass

    _cache_save(cpath, result)
    return result


# ── Soccer data collection ─────────────────────────────────────────────────────

def _soccer_fixture_player_stats(player_stats_list: list, player_id) -> dict | None:
    """Return the raw statistics dict for a player from a fixture/players response."""
    if not isinstance(player_stats_list, list):
        return None
    for team_entry in player_stats_list:
        if not isinstance(team_entry, dict) or not isinstance(team_entry.get("players"), list):
            continue
        for player_entry in (team_entry.get("players") or []):
            if not isinstance(player_entry, dict):
                continue
            p = player_entry.get("player") or {}
            if not isinstance(p, dict):
                continue
            if str(p.get("id")) != str(player_id):
                continue
            stats_list = player_entry.get("statistics") or [{}]
            return stats_list[0] if isinstance(stats_list, list) and stats_list and isinstance(stats_list[0], dict) else None
    return None


def _stat_count(value):
    """Provider counts/minutes: a missing, malformed or negative value is unknown."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _soccer_fixture_player_value(player_stats_list: list, player_id, market_key: str) -> float | None:
    """Extract a specific stat for player_id from a fixture/players response."""
    stats = _soccer_fixture_player_stats(player_stats_list, player_id)
    if stats is None:
        return None

    # Computed: goal_or_assist = goals + assists
    if market_key == "goal_or_assist":
        goals_section = stats.get("goals")
        if not isinstance(goals_section, dict):
            return None
        goals = _stat_count(goals_section.get("total"))
        assists = _stat_count(goals_section.get("assists"))
        return goals + assists if goals is not None and assists is not None else None

    # touches_opp_box not available in standard endpoint
    if market_key == "touches_opp_box":
        return None

    path = SOCCER_FIXTURE_STAT_PATH.get(market_key)
    if not path:
        return None
    section, key = path
    section_data = stats.get(section) or {}
    if not isinstance(section_data, dict):
        return None
    raw = section_data.get(key)
    return _stat_count(raw)


def _collect_soccer_samples(
    player_id,
    player_team_id,
    opponent_team_id,
    season: int,
    league: int = DEFAULT_LEAGUE_ID,
) -> dict:
    """
    Fetch all raw soccer data for the player from API-Football.
    Builds per-game log by fetching fixture/players stats for recent matches.
    """
    import api_client as ac

    cache_key = f"soccer_samples_{player_id}_{player_team_id}_{opponent_team_id}_{season}_{league}"
    cpath = _cache_path(cache_key)
    if _cache_valid(cpath, PROPS_SEASON_TTL):
        return _cache_load(cpath)

    result: dict = {
        "sport":             "soccer",
        "player_id":         player_id,
        "player_team_id":    player_team_id,
        "opponent_team_id":  opponent_team_id,
        "season":            season,
        "league":            league,
        "season_stats":      None,          # aggregate from API-Football players endpoint
        "per_game_log":      [],            # list of per-fixture stat dicts
        "vs_opponent_log":   [],            # games only vs this opponent
        "team_injuries":     [],
        "error":             None,
    }

    # 1 — Season aggregate stats
    try:
        player_data = ac.get_player_stats(int(player_id), league, season)
        logger.debug("Soccer player %s season_stats entries=%d", player_id, len(player_data or []))
        if player_data:
            # API-Football returns list; find entry matching desired league
            entry = None
            for item in player_data:
                for stats_entry in (item.get("statistics") or []):
                    if stats_entry.get("league", {}).get("id") == league:
                        entry = stats_entry
                        break
                if entry:
                    break
            if not entry and player_data:
                # Fall back to first stats entry
                item = player_data[0]
                stats_list = item.get("statistics") or [{}]
                entry = stats_list[0] if stats_list else {}
            result["season_stats"] = entry
    except Exception as e:
        logger.warning("Soccer season stats fetch failed player=%s: %s", player_id, e)
        result["error"] = str(e)

    # 2 — Per-game log via fixture player stats
    try:
        fixtures = ac.get_team_fixtures(int(player_team_id), league, season, last=15)
        finished = [f for f in fixtures if _soccer_fixture_finished(f)]
        for fixture in finished[:15]:
            fid = _soccer_fixture_id(fixture)
            if not fid:
                continue
            fpath = _cache_path(f"soccer_fix_players_{fid}_{player_team_id}")
            if _cache_valid(fpath, PROPS_CAREER_TTL):
                fp_data = _cache_load(fpath)
                if not isinstance(fp_data, list):
                    fp_data = []
            else:
                try:
                    fp_data = ac.get_fixture_player_stats(fid, team_id=int(player_team_id))
                    if not isinstance(fp_data, list):
                        fp_data = []
                    _cache_save(fpath, fp_data)
                except Exception:
                    fp_data = []

            logger.debug("fixture %s fp_data entries=%d player_id=%s", fid, len(fp_data), player_id)
            game_row = _extract_soccer_game_row(fixture, fp_data, player_id)
            if game_row:
                result["per_game_log"].append(game_row)
                opp_id = str(_soccer_opponent_id(fixture, player_team_id))
                if opp_id == str(opponent_team_id):
                    result["vs_opponent_log"].append(game_row)
    except Exception:
        pass

    # 3 — Career vs opponent (older seasons)
    for past_season in [season - 2, season - 1]:
        try:
            old_fixtures = ac.get_team_fixtures(int(player_team_id), league, past_season, last=10)
            vs_old = [f for f in old_fixtures if
                      _soccer_fixture_finished(f) and
                      str(_soccer_opponent_id(f, player_team_id)) == str(opponent_team_id)]
            for fixture in vs_old[:5]:
                fid = _soccer_fixture_id(fixture)
                if not fid:
                    continue
                fpath = _cache_path(f"soccer_fix_players_{fid}_{player_team_id}")
                if _cache_valid(fpath, PROPS_CAREER_TTL):
                    fp_data = _cache_load(fpath)
                    if not isinstance(fp_data, list):
                        fp_data = []
                else:
                    try:
                        fp_data = ac.get_fixture_player_stats(fid, team_id=int(player_team_id))
                        if not isinstance(fp_data, list):
                            fp_data = []
                        _cache_save(fpath, fp_data)
                    except Exception:
                        fp_data = []
                game_row = _extract_soccer_game_row(fixture, fp_data, player_id)
                if game_row and game_row not in result["vs_opponent_log"]:
                    result["vs_opponent_log"].append(game_row)
        except Exception:
            pass

    # 4 — Team injuries
    try:
        result["team_injuries"] = ac.get_injuries(int(player_team_id), league, season)
    except Exception:
        pass

    # Sort per_game_log most recent first
    result["per_game_log"].sort(key=lambda r: r.get("date", ""), reverse=True)
    result["vs_opponent_log"].sort(key=lambda r: r.get("date", ""), reverse=True)

    _cache_save(cpath, result)
    return result


def _collect_soccer_samples_all_comps(
    player_id,
    player_team_id,
    opponent_team_id,
    season: int,
    league_ids: list[int] | None = None,
) -> dict:
    """
    Cross-competition variant of _collect_soccer_samples.
    Fetches fixture/player data from all specified leagues, tags each game
    with its competition, and builds per_competition breakdown.
    """
    import api_client as ac
    from league_config import (
        COMP_DIFFICULTY,
        DEFAULT_LEAGUE_ID,
        LEAGUE_BY_ID,
        SUPPORTED_LEAGUES,
    )

    if league_ids is None:
        league_ids = [cfg["id"] for cfg in SUPPORTED_LEAGUES.values()]

    cache_key = (
        f"soccer_allcomps_{player_id}_{player_team_id}_{opponent_team_id}"
        f"_{season}_{'_'.join(str(l) for l in sorted(league_ids))}"
    )
    cpath = _cache_path(cache_key)
    if _cache_valid(cpath, PROPS_SEASON_TTL):
        return _cache_load(cpath)

    all_game_log: list[dict] = []
    vs_opponent_log: list[dict] = []
    per_competition: dict[int, dict] = {}   # league_id → {name, games, game_log}
    season_stats_by_league: dict[int, dict] = {}

    for lid in league_ids:
        league_info = LEAGUE_BY_ID.get(lid) or {}
        league_name = league_info.get("name", f"League {lid}")
        difficulty  = COMP_DIFFICULTY.get(lid, 1.00)
        comp_log: list[dict] = []

        # Season aggregate stats for this league
        try:
            player_data = ac.get_player_stats(int(player_id), lid, season)
            if player_data:
                for item in player_data:
                    for stats_entry in (item.get("statistics") or []):
                        if (stats_entry.get("league") or {}).get("id") == lid:
                            season_stats_by_league[lid] = stats_entry
                            break
        except Exception:
            pass

        # Per-game log
        try:
            fixtures = ac.get_team_fixtures(int(player_team_id), lid, season, last=20)
            finished = [f for f in fixtures if _soccer_fixture_finished(f)]
            for fixture in finished[:20]:
                fid = _soccer_fixture_id(fixture)
                if not fid:
                    continue
                fpath = _cache_path(f"soccer_fix_players_{fid}_{player_team_id}")
                if _cache_valid(fpath, PROPS_CAREER_TTL):
                    fp_data = _cache_load(fpath)
                    if not isinstance(fp_data, list):
                        fp_data = []
                else:
                    try:
                        fp_data = ac.get_fixture_player_stats(fid, team_id=int(player_team_id))
                        if not isinstance(fp_data, list):
                            fp_data = []
                        _cache_save(fpath, fp_data)
                    except Exception:
                        fp_data = []

                game_row = _extract_soccer_game_row(fixture, fp_data, player_id)
                if game_row:
                    game_row["_league_id"]         = lid
                    game_row["_league_name"]       = league_name
                    game_row["_league_difficulty"] = difficulty
                    comp_log.append(game_row)
                    all_game_log.append(game_row)

                    if str(_soccer_opponent_id(fixture, player_team_id)) == str(opponent_team_id):
                        vs_opponent_log.append(game_row)
        except Exception:
            pass

        per_competition[lid] = {
            "league_id":   lid,
            "league_name": league_name,
            "difficulty":  difficulty,
            "games":       len(comp_log),
            "game_log":    comp_log,
        }

    # Sort most-recent first
    all_game_log.sort(key=lambda r: r.get("date", ""), reverse=True)
    vs_opponent_log.sort(key=lambda r: r.get("date", ""), reverse=True)

    # Team injuries (use primary league for this)
    team_injuries: list = []
    primary_league = league_ids[0] if league_ids else DEFAULT_LEAGUE_ID
    try:
        team_injuries = ac.get_injuries(int(player_team_id), primary_league, season)
    except Exception:
        pass

    # Prefer the season stats for the primary league, fall back to first found
    primary_season_stats = (
        season_stats_by_league.get(primary_league)
        or next(iter(season_stats_by_league.values()), None)
    )

    result = {
        "sport":              "soccer",
        "player_id":          player_id,
        "player_team_id":     player_team_id,
        "opponent_team_id":   opponent_team_id,
        "season":             season,
        "league":             primary_league,
        "all_comps":          True,
        "league_ids":         league_ids,
        "per_game_log":       all_game_log,
        "full_season_log":    all_game_log,
        "vs_opponent_log":    vs_opponent_log,
        "season_stats":       primary_season_stats,
        "season_stats_by_league": season_stats_by_league,
        "per_competition":    per_competition,
        "team_injuries":      team_injuries,
        "error":              None,
    }
    _cache_save(cpath, result)
    return result


def _soccer_fixture_finished(fixture: dict) -> bool:
    status = (fixture.get("fixture") or fixture).get("status", {})
    short = status.get("short") or status.get("elapsed") or ""
    long_ = status.get("long", "").lower()
    if isinstance(short, str) and short in ("FT", "AET", "PEN"):
        return True
    return "finished" in long_ or "full" in long_


def _soccer_fixture_id(fixture: dict) -> int | None:
    fid = (fixture.get("fixture") or fixture).get("id")
    if fid:
        return int(fid)
    return None


def _soccer_opponent_id(fixture: dict, team_id) -> int | None:
    """Return the opponent team id (the team that is NOT player_team_id)."""
    teams = fixture.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    hid = home.get("id")
    aid = away.get("id")
    if str(hid) == str(team_id):
        return aid
    return hid


def _extract_soccer_game_row(fixture: dict, fp_data: list, player_id) -> dict | None:
    """Build a per-game stats row from fixture + fixture/players data."""
    fid = _soccer_fixture_id(fixture) or 0
    finfo = fixture.get("fixture") or fixture
    date = str(finfo.get("date") or "")[:10]
    teams = fixture.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}

    row: dict = {
        "fixture_id": fid,
        "date": date,
        "home_id": home.get("id"),
        "away_id": away.get("id"),
        "home_name": home.get("name", ""),
        "away_name": away.get("name", ""),
    }

    # Extract all supported markets
    found_any = False
    for market_key in SOCCER_FIXTURE_STAT_PATH:
        val = _soccer_fixture_player_value(fp_data, player_id, market_key)
        row[market_key] = val
        if val is not None:
            found_any = True

    # Extract minutes played to calculate per-90 stats
    minutes_val = None
    for team_entry in fp_data:
        for player_entry in (team_entry.get("players") or []):
            p = player_entry.get("player") or {}
            if str(p.get("id")) != str(player_id):
                continue
            stats_list = player_entry.get("statistics") or [{}]
            stats = stats_list[0] if stats_list else {}
            minutes_val = _sf((stats.get("games") or {}).get("minutes")) or None
    row["minutes"] = minutes_val

    return row if found_any or minutes_val else None


# ── Layer 2 — Core Averages ────────────────────────────────────────────────────

def _extract_values(game_log: list, market_key: str, sport: str,
                    player_id=None, per_90: bool = False) -> list[float]:
    """
    Extract a list of per-game values for a market from a game log.
    Handles NBA and soccer formats.  Applies per-90 normalisation for soccer.
    """
    values = []
    for record in game_log:
        if sport == "nba":
            val = _nba_game_value(record, market_key)
            if val is None:
                continue
            values.append(val)
        else:  # soccer — record is already a flat dict built by _extract_soccer_game_row
            val = record.get(market_key)
            if val is None:
                continue
            if per_90:
                mins = _sf(record.get("minutes") or 0)
                if mins >= 10:  # only scale if reasonable playing time
                    val = val / mins * 90
                # if < 10 mins, skip the record for per-90 purposes
                else:
                    continue
            values.append(float(val))
    return values


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _weighted_rolling_avg(values: list[float]) -> float | None:
    """
    Weighted rolling average:
      last 3 games × 0.50
      games 4-7    × 0.30
      games 8-10   × 0.20
    values should be ordered most-recent first.
    """
    if not values:
        return None
    group1 = values[:3]
    group2 = values[3:7]
    group3 = values[7:10]

    parts: list[tuple[float | None, float]] = [
        (_avg(group1), 0.50),
        (_avg(group2), 0.30),
        (_avg(group3), 0.20),
    ]
    return round(_weighted_avg(parts) or 0, 2)


def _home_away_split_values(game_log: list, player_team_id, sport: str,
                             market_key: str, per_90: bool = False
                             ) -> tuple[list[float], list[float]]:
    """Return (home_values, away_values) from the full season log."""
    home_vals, away_vals = [], []
    for record in game_log:
        if sport == "nba":
            teams = record.get("game", {}).get("teams", {})
            home_id = str(teams.get("home", {}).get("id") or "")
            is_home = (home_id == str(player_team_id))
            val = _nba_game_value(record, market_key)
        else:
            is_home = str(record.get("home_id")) == str(player_team_id)
            val = record.get(market_key)
            if val is not None and per_90:
                mins = _sf(record.get("minutes") or 0)
                if mins >= 10:
                    val = float(val) / mins * 90
                else:
                    val = None
        if val is not None:
            if is_home:
                home_vals.append(float(val))
            else:
                away_vals.append(float(val))
    return home_vals, away_vals


def _vs_opponent_avg_from_log(vs_log: list, market_key: str, sport: str,
                               per_90: bool = False) -> tuple[float | None, int]:
    """Return (avg, game_count) from vs_opponent game log."""
    values = _extract_values(vs_log, market_key, sport, per_90=per_90)
    return (_avg(values), len(values)) if values else (None, 0)


def _build_core_averages(
    samples: dict,
    market_key: str,
    is_home: bool,
    sport: str,
    per_90: bool = False,
) -> dict:
    """
    Layer 2: Compute all core averages for a market from collected samples.
    Returns a dict of averages with sample sizes.
    """
    full_log   = samples.get("full_season_log") or samples.get("per_game_log") or []
    last5_log  = samples.get("last5") or (full_log[-5:] if len(full_log) >= 5 else full_log)
    last10_log = samples.get("last10") or (full_log[-10:] if len(full_log) >= 10 else full_log)
    vs_log     = samples.get("vs_opponent", {}).get("records") or samples.get("vs_opponent_log") or []

    ptid = samples.get("player_team_id")

    # For soccer, per_game_log is most-recent-first; reverse for "last N"
    if sport == "soccer":
        full_rev   = list(reversed(full_log))   # oldest first for slicing
        last10_log = full_log[:10]
        last5_log  = full_log[:5]
    else:
        full_rev = full_log

    season_vals  = _extract_values(full_log,   market_key, sport, per_90=per_90)
    last5_vals   = _extract_values(last5_log,  market_key, sport, per_90=per_90)
    last10_vals  = _extract_values(last10_log, market_key, sport, per_90=per_90)
    vs_opp_vals  = _extract_values(vs_log,     market_key, sport, per_90=per_90)

    home_vals, away_vals = _home_away_split_values(
        full_log, ptid, sport, market_key, per_90=per_90
    )

    # Most-recent-first list of last-10 values for rolling avg
    if sport == "nba":
        recent_vals = list(reversed(last10_vals))
    else:
        recent_vals = last10_vals  # already most-recent first

    # Handle soccer from season aggregate if per_game_log is sparse
    season_avg_from_api = None
    if sport == "soccer" and samples.get("season_stats") and not season_vals:
        season_avg_from_api = _soccer_season_avg(samples["season_stats"], market_key)

    season_avg   = _avg(season_vals) or season_avg_from_api
    last5_avg    = _avg(last5_vals)
    last10_avg   = _avg(last10_vals)
    vs_opp_avg, vs_opp_games = _vs_opponent_avg_from_log(vs_log, market_key, sport, per_90)
    home_avg     = _avg(home_vals)
    away_avg     = _avg(away_vals)
    rolling_avg  = _weighted_rolling_avg(recent_vals)

    active_ha_avg = home_avg if is_home else away_avg
    ha_label      = "Home average" if is_home else "Away average"

    return {
        "season_avg":        season_avg,
        "season_games":      len(season_vals),
        "last5_avg":         last5_avg,
        "last5_games":       len(last5_vals),
        "last10_avg":        last10_avg,
        "last10_games":      len(last10_vals),
        "vs_opponent_avg":   vs_opp_avg,
        "vs_opponent_games": vs_opp_games,
        "home_avg":          home_avg,
        "away_avg":          away_avg,
        "active_ha_avg":     active_ha_avg,
        "ha_label":          ha_label,
        "rolling_avg":       rolling_avg,
        "limited_sample":    vs_opp_games < 3,
    }


def _soccer_season_avg(season_stats: dict, market_key: str) -> float | None:
    """
    Extract per-game average from API-Football season aggregate stat entry.
    API-Football returns season totals; we normalise by appearances to get
    per-game rates, and by minutes/90 for per-90 markets.
    """
    if not isinstance(season_stats, dict) or not season_stats:
        return None

    # Computed market: goal_or_assist = season goals + season assists
    if market_key == "goal_or_assist":
        goals_avg   = _soccer_season_avg(season_stats, "goals")
        assists_avg = _soccer_season_avg(season_stats, "assists")
        if goals_avg is None or assists_avg is None:
            return None
        return round(goals_avg + assists_avg, 3)

    if market_key == "touches_opp_box":
        return None

    path = SOCCER_SEASON_STAT_PATH.get(market_key)
    if not path:
        return None
    section, key = path
    section_data = season_stats.get(section) or {}
    if not isinstance(section_data, dict):
        return None
    raw = section_data.get(key)
    if raw is None:
        return None

    total = _stat_count(raw)
    if total is None:
        return None

    # Normalise by games played (appearances) for per-game markets
    games_data    = season_stats.get("games") or {}
    appearances   = _sf(games_data.get("appearences") or games_data.get("appearances") or 0)
    minutes_total = _sf(games_data.get("minutes") or 0)

    minfo = SOCCER_MARKETS.get(market_key)
    per_90 = minfo[2] if minfo else False

    if market_key == "minutes":
        # Return average minutes per appearance directly
        return round(total / appearances, 1) if appearances > 0 else None

    if per_90:
        # Never silently replace a per-90 rate with a per-appearance rate.
        return round(total / minutes_total * 90, 3) if minutes_total > 0 else None

    # Per-game normalisation
    if appearances > 0:
        return round(total / appearances, 3)

    return round(total, 3)


# ── Layer 3 — Variance & Consistency ──────────────────────────────────────────

def _hit_rate(values: list[float], line: float) -> tuple[int, int]:
    """Returns (hit_count, total_games) where hit = value >= line."""
    if not values:
        return 0, 0
    hits = sum(1 for v in values if v >= line)
    return hits, len(values)


def _build_variance(
    samples: dict,
    market_key: str,
    line: float,
    sport: str,
    per_90: bool = False,
) -> dict:
    """Layer 3: Compute consistency and variance stats from last 10 games."""
    full_log   = samples.get("full_season_log") or samples.get("per_game_log") or []
    if sport == "soccer":
        last10_log = full_log[:10]   # most-recent first
    else:
        last10_log = (samples.get("last10") or full_log[-10:])

    vals = _extract_values(last10_log, market_key, sport, per_90=per_90)

    if not vals:
        return {
            "std_dev":            None,
            "std_dev_pct":        None,
            "hit_count":          0,
            "sample_games":       0,
            "hit_rate_pct":       None,
            "ceiling":            None,
            "floor":              None,
            "boom_rate_pct":      None,
            "bust_rate_pct":      None,
            "consistency_label":  "unknown",
        }

    mean = sum(vals) / len(vals)
    sd   = _std_dev(vals)
    sd_pct = (sd / mean * 100) if mean else 0

    hits, total = _hit_rate(vals, line)
    hit_pct = _pct(hits, total)

    ceiling = round(max(vals), 1)
    floor_  = round(min(vals), 1)

    boom_threshold = line * 1.5
    bust_threshold = line * 0.5
    boom = sum(1 for v in vals if v >= boom_threshold)
    bust = sum(1 for v in vals if v <= bust_threshold)
    boom_pct = _pct(boom, total)
    bust_pct = _pct(bust, total)

    if sd_pct < 20:
        consistency = "low variance"
    elif sd_pct < 40:
        consistency = "moderate"
    elif sd_pct < 60:
        consistency = "high variance"
    else:
        consistency = "very high variance"

    return {
        "std_dev":            round(sd, 2),
        "std_dev_pct":        round(sd_pct, 1),
        "hit_count":          hits,
        "sample_games":       total,
        "hit_rate_pct":       hit_pct,
        "ceiling":            ceiling,
        "floor":              floor_,
        "boom_rate_pct":      boom_pct,
        "bust_rate_pct":      bust_pct,
        "consistency_label":  consistency,
    }


# ── Layer 4 — Contextual Modifiers ────────────────────────────────────────────

def _opponent_modifier_nba(opponent_stats: dict | None, market_key: str,
                            season_avg: float) -> tuple[float, float, str]:
    """
    Returns (multiplier, raw_value, label).
    Compares opponent defensive stats against league average proxied from season_avg.
    """
    if not opponent_stats or not season_avg:
        return 1.0, 0.0, "No opponent data"

    # For points, use opponent PPG allowed; for others approximate by rating
    opp_ppg  = _sf(opponent_stats.get("opp_ppg", 0) or 0)
    net_rtg  = _sf(opponent_stats.get("net_rtg", 0) or 0)

    if market_key == "points":
        # League avg ~113; difference tells us how much opp allows vs average
        league_avg_pts = max(season_avg, 113.0)
        diff = opp_ppg - league_avg_pts
    else:
        # Use net rating as a general defensive proxy
        diff = -net_rtg   # positive net_rtg means good defence, so negative = more pts
        league_avg_pts = season_avg

    # Map diff to ±15% modifier
    multiplier = 1.0 + _clamp(diff / league_avg_pts, -0.15, 0.15)
    raw = round(diff * (season_avg / max(league_avg_pts, 1)), 2)

    if multiplier > 1.05:
        label = "🟢 weak defence"
    elif multiplier < 0.95:
        label = "🔴 tough defence"
    else:
        label = "⚪ average defence"

    return round(multiplier, 4), raw, label


def _opponent_modifier_soccer(season_stats: dict | None, market_key: str,
                               season_avg: float | None) -> tuple[float, float, str]:
    """Soccer opponent modifier using league-wide averages as proxy."""
    # Without real opponent defensive stats in our dataset we use a neutral modifier
    # (full implementation would pull opponent_team goals_conceded from standings/fixtures)
    return 1.0, 0.0, "Opponent data limited"


def _home_away_modifier(home_avg: float | None, away_avg: float | None,
                         is_home: bool, base_proj: float) -> tuple[float, float, str]:
    """Apply the difference between home and away avg to the projection."""
    if home_avg is None or away_avg is None:
        return 1.0, 0.0, "Insufficient H/A data"

    diff = home_avg - away_avg
    active = home_avg if is_home else away_avg
    if base_proj == 0:
        return 1.0, 0.0, "Home/Away neutral"

    multiplier = 1.0 + _clamp(diff if is_home else -diff, -0.10, 0.10) / base_proj * 0.5
    raw_adj    = round((active - base_proj) * 0.1, 2)
    if is_home and diff > 0:
        label = f"🏠 home boost (+{round(diff, 1)})"
    elif not is_home and diff > 0:
        label = f"✈️ away penalty (-{round(diff, 1)})"
    else:
        label = "Home/Away near neutral"

    return round(multiplier, 4), raw_adj, label


def _trend_modifier(last3_avg: float | None, season_avg: float | None) -> tuple[float, float, str]:
    """Hot streak (+10%) or cold streak (-10%) based on last 3 vs season avg."""
    if last3_avg is None or season_avg is None or season_avg == 0:
        return 1.0, 0.0, "Insufficient trend data"

    pct_diff = (last3_avg - season_avg) / season_avg

    if pct_diff > 0.20:
        multiplier, label = 1.10, "🔥 hot streak bonus"
    elif pct_diff < -0.20:
        multiplier, label = 0.90, "🥶 cold streak penalty"
    elif pct_diff > 0.05:
        multiplier, label = 1.03, "📈 slightly above average"
    elif pct_diff < -0.05:
        multiplier, label = 0.97, "📉 slightly below average"
    else:
        multiplier, label = 1.0, "➡️ on par with season avg"

    raw_adj = round(last3_avg - (season_avg or 0), 2)
    return round(multiplier, 4), raw_adj, label


def _minutes_modifier(game_log: list, sport: str, season_avg_min: float | None = None
                       ) -> tuple[float, float, str]:
    """
    Minutes trend: if last 3 games trending up vs season avg → +5%;
    if trending down → -5%.
    """
    if not game_log or len(game_log) < 3:
        return 1.0, 0.0, "Insufficient minutes data"

    if sport == "nba":
        last3 = [_nba_minutes(r) for r in game_log[-3:]]
        all_min = [_nba_minutes(r) for r in game_log]
    else:
        last3 = [_sf(r.get("minutes") or 0) for r in game_log[:3]]
        all_min = [_sf(r.get("minutes") or 0) for r in game_log]

    last3 = [m for m in last3 if m > 0]
    all_min = [m for m in all_min if m > 0]

    if not last3 or not all_min:
        return 1.0, 0.0, "No minutes data"

    last3_avg_min = sum(last3) / len(last3)
    s_avg_min = season_avg_min if season_avg_min else sum(all_min) / len(all_min)

    if s_avg_min == 0:
        return 1.0, 0.0, "No minutes data"

    diff_pct = (last3_avg_min - s_avg_min) / s_avg_min

    if diff_pct > 0.05:
        multiplier, label = 1.05, "↑ minutes trending up"
    elif diff_pct < -0.05:
        multiplier, label = 0.95, "↓ minutes trending down"
    else:
        multiplier, label = 1.0, "→ minutes stable"

    raw_adj = round(last3_avg_min - s_avg_min, 1)
    return round(multiplier, 4), raw_adj, label


def _injury_modifier(team_injuries: list, player_id) -> tuple[float, float, str]:
    """
    -8% if a key teammate feeder is injured (approximated: any key starter OUT/DOUBTFUL).
    """
    key_out = [
        inj for inj in team_injuries
        if (inj.get("status") or "").lower() in ("out", "doubtful")
        and str((inj.get("player") or {}).get("id") or "") != str(player_id)
    ]
    if key_out:
        n = min(len(key_out), 3)
        mult = round(1.0 - 0.08 * (n / 3), 4)
        label = f"🔴 {n} teammate(s) injured"
        return mult, round(-0.08 * (n / 3), 3), label
    return 1.0, 0.0, "✅ No key teammate injuries"


# ── Layer 5 — Final Projection ─────────────────────────────────────────────────

def _base_projection(
    season_avg:    float | None,
    last5_avg:     float | None,
    last10_avg:    float | None,
    vs_opp_avg:    float | None,
    active_ha_avg: float | None,
) -> float:
    """
    base = (season × 0.25) + (last5 × 0.30) + (last10 × 0.15) + (vsOpp × 0.20) + (H/A × 0.10)
    Missing values have their weight redistributed proportionally.
    """
    weights = [
        (season_avg,    0.25),
        (last5_avg,     0.30),
        (last10_avg,    0.15),
        (vs_opp_avg,    0.20),
        (active_ha_avg, 0.10),
    ]
    result = _weighted_avg(weights)
    return round(result or 0.0, 2)


def _apply_all_modifiers(base: float, mods: list[dict]) -> float:
    """Multiply all modifier values onto the base projection."""
    adj = base
    for m in mods:
        adj *= m.get("multiplier", 1.0)
    return round(adj, 2)


def _lean(adjusted: float, line: float) -> str:
    if line == 0:
        return "PUSH"
    ratio = adjusted / line
    if ratio > 1.08:
        return "OVER"
    if ratio < 0.92:
        return "UNDER"
    return "PUSH"


def _lean_margin_pct(adjusted: float, line: float) -> float:
    if line == 0:
        return 0.0
    return round((adjusted / line - 1) * 100, 1)


# ── Layer 6 — Confidence Score ─────────────────────────────────────────────────

def _confidence_score(
    vs_opp_games: int,
    variance: dict,
    lean: str,
    last5_avg: float | None,
    vs_opp_avg: float | None,
    modifiers: list[dict],
    season_avg: float | None,
) -> dict:
    """
    5 components × 20 pts max = 100 pts total.

    1) Sample size vs opponent
    2) Consistency (std deviation relative to mean)
    3) Hit rate (last 10)
    4) Trend alignment (last5 + vsOpp agree with lean)
    5) Contextual modifiers (how many point same direction)
    """
    # 1 — Sample size
    if vs_opp_games >= 10:
        sample_pts = 20
    elif vs_opp_games >= 5:
        sample_pts = 15
    elif vs_opp_games >= 2:
        sample_pts = 8
    else:
        sample_pts = 3

    # 2 — Consistency
    sd_pct = variance.get("std_dev_pct") or 100
    if sd_pct <= 20:
        consist_pts = 20
    elif sd_pct <= 40:
        consist_pts = int(20 - (sd_pct - 20) / 20 * 7.5)
    elif sd_pct <= 60:
        consist_pts = int(12.5 - (sd_pct - 40) / 20 * 7.5)
    else:
        consist_pts = 5

    # 3 — Hit rate
    hit_pct = variance.get("hit_rate_pct") or 0
    if hit_pct > 70:
        hit_pts = 20
    elif hit_pct >= 50:
        hit_pts = 15
    elif hit_pct >= 30:
        hit_pts = 8
    else:
        hit_pts = 3

    # 4 — Trend alignment
    if season_avg and season_avg > 0:
        last5_lean = (
            "OVER"  if last5_avg  and last5_avg  > season_avg else
            "UNDER" if last5_avg  and last5_avg  < season_avg else "PUSH"
        )
        opp_lean = (
            "OVER"  if vs_opp_avg and vs_opp_avg > season_avg else
            "UNDER" if vs_opp_avg and vs_opp_avg < season_avg else "PUSH"
        )
    else:
        last5_lean = opp_lean = "PUSH"

    agrees = sum([
        last5_lean == lean and lean != "PUSH",
        opp_lean  == lean and lean != "PUSH",
    ])
    trend_pts = 20 if agrees == 2 else (10 if agrees == 1 else 0)

    # 5 — Contextual modifiers alignment
    if not modifiers:
        ctx_pts = 10  # neutral
    else:
        # Determine the dominant direction of the modifiers
        boosts = sum(1 for m in modifiers if m.get("multiplier", 1.0) > 1.01)
        drags  = sum(1 for m in modifiers if m.get("multiplier", 1.0) < 0.99)
        total  = len(modifiers)
        same_dir = max(boosts, drags)
        ctx_pts = max(0, int(20 * same_dir / total))

    total_score = sample_pts + consist_pts + hit_pts + trend_pts + ctx_pts

    return {
        "score":             _clamp(total_score, 0, 100),
        "label":             _confidence_label(total_score),
        "components": {
            "sample_size":   sample_pts,
            "consistency":   consist_pts,
            "hit_rate":      hit_pts,
            "trend_alignment": trend_pts,
            "contextual":    ctx_pts,
        },
    }


def _confidence_label(score: int) -> str:
    if score >= 80:
        return "🔥 Elite pick"
    if score >= 65:
        return "✅ Strong pick"
    if score >= 50:
        return "📊 Moderate pick"
    if score >= 35:
        return "⚠️ Lean only"
    return "❌ Insufficient data"


# ── Sport-specific opponent data ───────────────────────────────────────────────

def _fetch_nba_opponent_stats(opponent_team_id, season: int) -> dict | None:
    try:
        import nba_client as nc
        return nc.get_team_season_stats(int(opponent_team_id), season)
    except Exception:
        return None


def _fetch_soccer_opponent_stats(opponent_team_id, league: int, season: int) -> dict | None:
    # Approximate using team fixtures (goals conceded per game)
    try:
        import api_client as ac
        fixtures = ac.get_team_fixtures(int(opponent_team_id), league, season, last=10)
        conceded = []
        for f in fixtures:
            teams = f.get("teams") or {}
            goals = f.get("goals") or {}
            h_id = (teams.get("home") or {}).get("id")
            is_home = str(h_id) == str(opponent_team_id)
            opp_goals = goals.get("away") if is_home else goals.get("home")
            if opp_goals is not None:
                conceded.append(_sf(opp_goals))
        if conceded:
            avg_conceded = sum(conceded) / len(conceded)
            return {"goals_conceded_pg": round(avg_conceded, 2)}
    except Exception:
        pass
    return None


# ── NBA probability markets ────────────────────────────────────────────────────

def _double_double_probability(full_log: list) -> dict:
    """% of last 20 games where player had 10+ in any two of pts/reb/ast."""
    last20 = full_log[-20:] if len(full_log) >= 20 else full_log
    if not last20:
        return {"probability": None, "sample": 0, "method": "insufficient data"}

    count = 0
    for record in last20:
        s = (record.get("statistics") or [{}])[0]
        cats = [
            _sf(s.get("points")),
            _sf(s.get("rebounds")),
            _sf(s.get("assists")),
        ]
        if sum(1 for c in cats if c >= 10) >= 2:
            count += 1

    prob = round(count / len(last20) * 100, 1)
    return {"probability": prob, "sample": len(last20), "count": count, "method": "last20_game_log"}


def _triple_double_probability(full_log: list) -> dict:
    """% of last 20 games where player had 10+ in three of pts/reb/ast."""
    last20 = full_log[-20:] if len(full_log) >= 20 else full_log
    if not last20:
        return {"probability": None, "sample": 0, "method": "insufficient data"}

    count = 0
    for record in last20:
        s = (record.get("statistics") or [{}])[0]
        cats = [
            _sf(s.get("points")),
            _sf(s.get("rebounds")),
            _sf(s.get("assists")),
        ]
        if all(c >= 10 for c in cats):
            count += 1

    prob = round(count / len(last20) * 100, 1)
    risk = "HIGH RISK BET" if prob < 15 else "viable"
    return {"probability": prob, "sample": len(last20), "count": count,
            "method": "last20_game_log", "risk_note": risk}


# ── PRA with correlation discount ─────────────────────────────────────────────

def _pra_projection(pts_proj: float | None, reb_proj: float | None,
                     ast_proj: float | None) -> float | None:
    available = [v for v in [pts_proj, reb_proj, ast_proj] if v is not None]
    if not available:
        return None
    total = sum(available)
    # -3% correlation discount
    return round(total * 0.97, 2)


# ── Soccer MOTM composite ──────────────────────────────────────────────────────

def _motm_probability(season_stats: dict | None, squad: list | None,
                       player_id) -> dict:
    """
    Composite score from goals, assists, key passes, dribbles, rating.
    Compare against squad to estimate MOTM probability.
    """
    if not season_stats:
        return {"probability": None, "note": "No data"}

    def _extract(s: dict) -> dict:
        return {
            "goals":      _sf((s.get("goals") or {}).get("total") or 0),
            "assists":    _sf((s.get("goals") or {}).get("assists") or 0),
            "key_passes": _sf((s.get("passes") or {}).get("key") or 0),
            "dribbles":   _sf((s.get("dribbles") or {}).get("success") or 0),
            "rating":     _sf((s.get("games") or {}).get("rating") or 6.5),
        }

    player_composite = _motm_composite(_extract(season_stats))
    # Without squad data we approximate: top 30% of games earns MOTM → ~30% baseline
    prob = round(min(player_composite * 8, 100), 1)
    return {"probability": prob, "composite_score": round(player_composite, 3),
            "note": "Estimated from season composite"}


def _motm_composite(stats: dict) -> float:
    return (
        stats.get("goals",      0) * 0.35 +
        stats.get("assists",    0) * 0.25 +
        stats.get("key_passes", 0) * 0.15 +
        stats.get("dribbles",   0) * 0.10 +
        (stats.get("rating",    6.5) - 6.0) * 0.15
    )


# ── Bet slip builder ────────────────────────────────────────────────────────────

def _build_bet_slip(prop_cards: list[dict]) -> dict:
    """
    Summarise the prop cards into a bet slip with parlay advisor.
    """
    picks = [
        {
            "player":     p.get("player_name", ""),
            "market":     p.get("market_label", ""),
            "lean":       p.get("projection", {}).get("lean", "PUSH"),
            "line":       p.get("projection", {}).get("suggested_line"),
            "confidence": p.get("confidence", {}).get("score", 0),
            "label":      p.get("confidence", {}).get("label", ""),
        }
        for p in prop_cards
        if p.get("projection", {}).get("lean") != "PUSH"
    ]
    picks.sort(key=lambda x: x["confidence"], reverse=True)

    if not picks:
        return {"picks": [], "slip_confidence": 0, "best_single": None,
                "parlay_risk": "N/A", "parlay_advice": "No confident picks found",
                "parlay_confidence": 0}

    # Average confidence
    slip_conf = round(sum(p["confidence"] for p in picks) / len(picks), 1)

    best = picks[0]
    best_single = f"{best['player']} — {best['market']} {best['lean']} {best['line']}"

    # Parlay calculation
    has_weak = any(p["confidence"] < 50 for p in picks)
    parlay_conf = 1.0
    for i, p in enumerate(picks):
        parlay_conf *= p["confidence"] / 100
        if i > 0:
            parlay_conf *= 0.95  # -5% correlation penalty per additional pick

    parlay_conf = round(parlay_conf * 100, 1)

    if has_weak:
        parlay_risk = "HIGH"
        parlay_advice = "Parlay not recommended — remove weak picks first"
    elif len(picks) > 4:
        parlay_risk = "MEDIUM"
        parlay_advice = f"Parlay viable but long — combined confidence {parlay_conf}%"
    else:
        parlay_risk = "LOW"
        parlay_advice = f"Parlay viable — combined confidence {parlay_conf}%"

    # Star rating
    stars = "⭐⭐⭐⭐⭐" if slip_conf >= 80 else \
            "⭐⭐⭐⭐"  if slip_conf >= 65 else \
            "⭐⭐⭐"    if slip_conf >= 50 else \
            "⭐⭐"      if slip_conf >= 35 else "⭐"

    return {
        "picks":             picks,
        "slip_confidence":   slip_conf,
        "stars":             stars,
        "best_single":       best_single,
        "parlay_risk":       parlay_risk,
        "parlay_advice":     parlay_advice,
        "parlay_confidence": parlay_conf,
    }


# ── Main prop card builder ─────────────────────────────────────────────────────

def _build_prop_card(
    sport:              str,
    market_key:         str,
    samples:            dict,
    is_home:            bool,
    opponent_stats:     dict | None,
    player_name:        str,
    opponent_name:      str,
    league:             int = DEFAULT_LEAGUE_ID,
) -> dict:
    """Build a single complete prop card for one market."""

    # Market meta
    if sport == "nba":
        minfo     = NBA_MARKETS.get(market_key, (market_key.title(), market_key.upper()))
        label     = minfo[0]
        abbr      = minfo[1]
        per_90    = False
    else:
        minfo     = SOCCER_MARKETS.get(market_key, (market_key.title(), market_key.upper(), False))
        label     = minfo[0]
        abbr      = minfo[1]
        per_90    = minfo[2]

    # ── Probability-only markets (DD, TD, MOTM) ────────────────────────────────
    if market_key == "double_double":
        prob = _double_double_probability(samples.get("full_season_log") or [])
        return {
            "market_key":    market_key,
            "market_label":  label,
            "abbr":          abbr,
            "player_name":   player_name,
            "opponent_name": opponent_name,
            "type":          "probability",
            "probability":   prob,
            "confidence":    {"score": 45 if prob.get("probability") else 10,
                              "label": "⚠️ Lean only" if prob.get("probability") else "❌ Insufficient data"},
        }

    if market_key == "triple_double":
        prob = _triple_double_probability(samples.get("full_season_log") or [])
        return {
            "market_key":    market_key,
            "market_label":  label,
            "abbr":          abbr,
            "player_name":   player_name,
            "opponent_name": opponent_name,
            "type":          "probability",
            "probability":   prob,
            "confidence":    {"score": 35 if prob.get("probability") else 10,
                              "label": "⚠️ Lean only" if prob.get("probability") else "❌ Insufficient data"},
        }

    if market_key == "motm":
        prob = _motm_probability(samples.get("season_stats"), None, samples.get("player_id"))
        return {
            "market_key":    market_key,
            "market_label":  label,
            "abbr":          abbr,
            "player_name":   player_name,
            "opponent_name": opponent_name,
            "type":          "probability",
            "probability":   prob,
            "confidence":    {"score": 40,
                              "label": "⚠️ Lean only"},
        }

    # ── Layer 2 — Core Averages ────────────────────────────────────────────────
    avgs = _build_core_averages(samples, market_key, is_home, sport, per_90)

    season_avg    = avgs["season_avg"]
    last5_avg     = avgs["last5_avg"]
    last10_avg    = avgs["last10_avg"]
    vs_opp_avg    = avgs["vs_opponent_avg"]
    active_ha_avg = avgs["active_ha_avg"]
    rolling_avg   = avgs["rolling_avg"]

    # ── PRA: sum sub-projections with correlation discount ─────────────────────
    if market_key == "pra":
        pts_avgs = _build_core_averages(samples, "points",   is_home, sport, False)
        reb_avgs = _build_core_averages(samples, "rebounds", is_home, sport, False)
        ast_avgs = _build_core_averages(samples, "assists",  is_home, sport, False)
        season_avg    = _pra_projection(pts_avgs["season_avg"],   reb_avgs["season_avg"],   ast_avgs["season_avg"])
        last5_avg     = _pra_projection(pts_avgs["last5_avg"],    reb_avgs["last5_avg"],    ast_avgs["last5_avg"])
        last10_avg    = _pra_projection(pts_avgs["last10_avg"],   reb_avgs["last10_avg"],   ast_avgs["last10_avg"])
        vs_opp_avg    = _pra_projection(pts_avgs["vs_opponent_avg"], reb_avgs["vs_opponent_avg"], ast_avgs["vs_opponent_avg"])
        active_ha_avg = _pra_projection(pts_avgs["active_ha_avg"], reb_avgs["active_ha_avg"], ast_avgs["active_ha_avg"])
        rolling_avg   = _pra_projection(pts_avgs["rolling_avg"],  reb_avgs["rolling_avg"],  ast_avgs["rolling_avg"])
        avgs = {**avgs, "season_avg": season_avg, "last5_avg": last5_avg,
                "last10_avg": last10_avg, "vs_opponent_avg": vs_opp_avg,
                "active_ha_avg": active_ha_avg, "rolling_avg": rolling_avg,
                "pra_note": "−3% correlation discount applied"}

    # ── Layer 5 base projection ────────────────────────────────────────────────
    base_proj = _base_projection(season_avg, last5_avg, last10_avg, vs_opp_avg, active_ha_avg)

    # If no data at all, return placeholder
    if base_proj == 0 and season_avg is None:
        logger.debug(
            "No data for player %s market=%s sport=%s — returning insufficient data card",
            samples.get("player_id"), market_key, sport,
        )
        return {
            "market_key":    market_key,
            "market_label":  label,
            "abbr":          abbr,
            "player_name":   player_name,
            "opponent_name": opponent_name,
            "type":          "standard",
            "error":         "Insufficient recent data",
            "layers":        avgs,
            "projection":    {
                "base":           None,
                "adjusted":       None,
                "suggested_line": None,
                "lean":           "N/A",
                "lean_margin_pct": None,
            },
            "variance":      {},
            "modifiers":     [],
            "confidence":    {"score": 0, "label": "❌ Insufficient data", "components": {}},
        }

    # Suggested line for variance calc
    raw_line = _round_half(base_proj)

    # ── Layer 3 — Variance ─────────────────────────────────────────────────────
    # For PRA: build a synthetic game log of pts+reb+ast values for variance analysis
    if market_key == "pra" and sport == "nba":
        full_log_for_var = samples.get("full_season_log") or []
        last10_for_var = full_log_for_var[-10:] if len(full_log_for_var) >= 10 else full_log_for_var
        pra_records = []
        for rec in last10_for_var:
            s = (rec.get("statistics") or [{}])[0]
            pra_val = _sf(s.get("points")) + _sf(s.get("rebounds")) + _sf(s.get("assists"))
            if pra_val > 0:
                pra_records.append(pra_val)
        variance = {
            "std_dev":           round(_std_dev(pra_records), 2) if pra_records else None,
            "std_dev_pct":       round(_std_dev(pra_records) / (sum(pra_records)/len(pra_records)) * 100, 1)
                                  if len(pra_records) >= 2 and sum(pra_records) > 0 else None,
            "hit_count":         sum(1 for v in pra_records if v >= raw_line),
            "sample_games":      len(pra_records),
            "hit_rate_pct":      _pct(sum(1 for v in pra_records if v >= raw_line), len(pra_records)),
            "ceiling":           round(max(pra_records), 1) if pra_records else None,
            "floor":             round(min(pra_records), 1) if pra_records else None,
            "boom_rate_pct":     _pct(sum(1 for v in pra_records if v >= raw_line * 1.5), len(pra_records)),
            "bust_rate_pct":     _pct(sum(1 for v in pra_records if v <= raw_line * 0.5), len(pra_records)),
            "consistency_label": "moderate",
        }
    else:
        variance = _build_variance(samples, market_key, raw_line, sport, per_90)

    # ── Layer 4 — Modifiers ────────────────────────────────────────────────────
    modifiers: list[dict] = []

    # 4a — Opponent modifier
    if sport == "nba":
        mult, raw_v, lbl = _opponent_modifier_nba(opponent_stats, market_key, season_avg or 0)
    else:
        mult, raw_v, lbl = _opponent_modifier_soccer(opponent_stats, market_key, season_avg)
    modifiers.append({"name": "Opponent defense", "multiplier": mult, "raw_value": raw_v, "label": lbl})

    # 4b — Home/Away modifier
    mult, raw_v, lbl = _home_away_modifier(avgs["home_avg"], avgs["away_avg"], is_home, base_proj)
    modifiers.append({"name": "Home/Away", "multiplier": mult, "raw_value": raw_v, "label": lbl})

    # 4c — Trend modifier (last 3 games vs season avg)
    full_log = samples.get("full_season_log") or samples.get("per_game_log") or []
    if sport == "soccer":
        last3_log = full_log[:3]  # most-recent-first
    else:
        last3_log = full_log[-3:] if len(full_log) >= 3 else full_log

    last3_vals = _extract_values(last3_log, market_key, sport, per_90=per_90)
    last3_avg  = _avg(last3_vals) if market_key != "pra" else None
    mult, raw_v, lbl = _trend_modifier(last3_avg, season_avg)
    modifiers.append({"name": "Recent trend", "multiplier": mult, "raw_value": raw_v, "label": lbl})

    # 4d — Minutes modifier
    mult, raw_v, lbl = _minutes_modifier(full_log, sport)
    modifiers.append({"name": "Minutes trend", "multiplier": mult, "raw_value": raw_v, "label": lbl})

    # 4e — Injury modifier
    injuries = samples.get("team_injuries") or []
    mult, raw_v, lbl = _injury_modifier(injuries, samples.get("player_id"))
    modifiers.append({"name": "Teammate injuries", "multiplier": mult, "raw_value": raw_v, "label": lbl})

    # ── Layer 5 — Adjusted projection ─────────────────────────────────────────
    adjusted_proj = _apply_all_modifiers(base_proj, modifiers)
    suggested_line = _round_half(adjusted_proj)
    lean = _lean(adjusted_proj, suggested_line)
    lean_margin_pct = _lean_margin_pct(adjusted_proj, suggested_line)

    # ── Layer 6 — Confidence ───────────────────────────────────────────────────
    confidence = _confidence_score(
        vs_opp_games  = avgs["vs_opponent_games"],
        variance      = variance,
        lean          = lean,
        last5_avg     = last5_avg,
        vs_opp_avg    = vs_opp_avg,
        modifiers     = modifiers,
        season_avg    = season_avg,
    )

    # ── Trend label for UI ─────────────────────────────────────────────────────
    if last5_avg and season_avg and season_avg > 0:
        l5_diff_pct = (last5_avg - season_avg) / season_avg
        trend_label = "📈 Hot" if l5_diff_pct > 0.10 else ("📉 Cold" if l5_diff_pct < -0.10 else "")
    else:
        trend_label = ""

    return {
        "market_key":    market_key,
        "market_label":  label,
        "abbr":          abbr,
        "player_name":   player_name,
        "opponent_name": opponent_name,
        "type":          "standard",
        "layers": {
            **avgs,
            "rolling_avg":    rolling_avg,
            "per_90":         per_90,
            "trend_label":    trend_label,
        },
        "variance":      variance,
        "modifiers":     modifiers,
        "projection": {
            "base":            base_proj,
            "adjusted":        adjusted_proj,
            "suggested_line":  suggested_line,
            "lean":            lean,
            "lean_margin_pct": lean_margin_pct,
        },
        "confidence": confidence,
    }


# ── Public entry point ─────────────────────────────────────────────────────────

def generate_props(
    sport:             str,
    player_id,
    player_name:       str,
    player_team_id,
    opponent_team_id,
    opponent_name:     str,
    is_home:           bool,
    markets:           list[str],
    player_position:   str = "",
    season:            int | None = None,
    league:            int = DEFAULT_LEAGUE_ID,
    include_all_comps: bool = False,
    league_ids:        list[int] | None = None,
) -> dict:
    """
    Main public API.  Returns a complete prop analysis JSON for the player vs opponent.

    Args:
        sport:            "nba" or "soccer"
        player_id:        API player id
        player_name:      Display name
        player_team_id:   Player's team id
        opponent_team_id: Opposing team id
        opponent_name:    Opposing team display name
        is_home:          True if player's team is the home side
        markets:          List of market keys e.g. ["points","rebounds","assists","pra"]
        player_position:  Position string (optional, used for soccer modifiers)
        season:           Season year (defaults to current NBA/soccer season)
        league:           Football league id (defaults to league_config.DEFAULT_LEAGUE_ID), ignored for NBA

    Returns:
        dict with keys: player, opponent, sport, is_home, props, bet_slip, errors
    """
    if season is None:
        if sport == "nba":
            from nba_live_client import NBA_SEASON
            season = NBA_SEASON
        else:
            from league_config import CURRENT_SEASON
            season = CURRENT_SEASON

    errors: list[str] = []

    # ── Collect samples ────────────────────────────────────────────────────────
    if sport == "nba":
        try:
            samples = _collect_nba_samples(player_id, player_team_id, opponent_team_id, season)
        except Exception as e:
            errors.append(f"NBA data collection failed: {e}")
            samples = {"sport": "nba", "full_season_log": [], "last5": [], "last10": [],
                       "vs_opponent": {"games": 0, "records": [], "averages": None, "limited_sample": True},
                       "team_injuries": [], "player_id": player_id, "player_team_id": player_team_id,
                       "opponent_team_id": opponent_team_id, "season": season}
    else:
        try:
            if include_all_comps:
                samples = _collect_soccer_samples_all_comps(
                    player_id, player_team_id, opponent_team_id, season,
                    league_ids=league_ids,
                )
            else:
                samples = _collect_soccer_samples(player_id, player_team_id, opponent_team_id, season, league)
        except Exception as e:
            errors.append(f"Soccer data collection failed: {e}")
            samples = {"sport": "soccer", "per_game_log": [], "vs_opponent_log": [],
                       "season_stats": None, "team_injuries": [],
                       "player_id": player_id, "player_team_id": player_team_id,
                       "opponent_team_id": opponent_team_id, "season": season, "league": league}

    if samples.get("error"):
        errors.append(samples["error"])

    # ── Fetch opponent defensive context ──────────────────────────────────────
    coverage_missing = sport == "nba" and not samples.get("full_season_log")
    if coverage_missing:
        errors.append("Verified real-player NBA game-log coverage is unavailable; no prop estimates generated.")
        opponent_stats = None
    elif sport == "nba":
        opponent_stats = _fetch_nba_opponent_stats(opponent_team_id, season)
    else:
        opponent_stats = _fetch_soccer_opponent_stats(opponent_team_id, league, season)

    # ── Build prop cards ───────────────────────────────────────────────────────
    prop_cards: list[dict] = []
    for market_key in markets:
        valid_set = set(NBA_MARKETS) if sport == "nba" else set(SOCCER_MARKETS)
        if market_key not in valid_set:
            errors.append(f"Unknown market '{market_key}' for sport '{sport}'")
            continue
        if coverage_missing:
            continue
        if sport == "soccer" and market_key in {"clearances", "aerial_duels_won", "passes_completed", "chances_created", "touches_opp_box"}:
            errors.append(f"Unsupported source definition for {market_key}; no estimate generated.")
            continue
        try:
            card = _build_prop_card(
                sport=sport,
                market_key=market_key,
                samples=samples,
                is_home=is_home,
                opponent_stats=opponent_stats,
                player_name=player_name,
                opponent_name=opponent_name,
                league=league,
            )
            prop_cards.append(card)
        except Exception as e:
            errors.append(f"Failed to build prop for {market_key}: {e}")

    # ── Bet slip ───────────────────────────────────────────────────────────────
    bet_slip = _build_bet_slip(prop_cards)

    return {
        "player": {
            "id":         player_id,
            "name":       player_name,
            "team_id":    player_team_id,
            "position":   player_position,
        },
        "opponent": {
            "id":   opponent_team_id,
            "name": opponent_name,
        },
        "sport":       sport,
        "season":      season,
        "is_home":     is_home,
        "props":       prop_cards,
        "bet_slip":    bet_slip,
        "data_notes": {
            "nba":    "Legacy NBA game-log adapter. Verified real-player coverage is required; no synthetic fallback.",
            "soccer": "Season aggregates from API-Football. Per-game stats fetched from fixture/players endpoint and cached.",
        }.get(sport, ""),
        "errors":      errors,
        "coverage": {"available": not coverage_missing, "source": "legacy NBA player game logs" if sport == "nba" else "API-Football fixture/player statistics",
                     "reason": "No verified real-player game logs" if coverage_missing else None},
    }


