"""
league_config.py -- BallDontLie-supported football competitions for ScorPred.

Only competitions with BallDontLie football coverage are exposed in the props
experience. Difficulty weights are used during cross-competition aggregation:
  < 1.00 = tougher competition
  > 1.00 = softer competition
  1.00   = baseline domestic league
"""

from __future__ import annotations

from datetime import datetime as _dt

# Football seasons span two calendar years; API-Football uses the start year.
# August onwards → new season has started (e.g. Aug 2025 → season 2025).
# Before August → still in the season that started last year (e.g. Apr 2026 → season 2025).
def _current_football_season() -> int:
    now = _dt.now()
    return now.year if now.month >= 8 else now.year - 1

CURRENT_SEASON: int = _current_football_season()
DEFAULT_LEAGUE_KEY = "premier_league"

# key -> {id, name, country, flag, difficulty, type, bdl_slug, bdl_version}
SUPPORTED_LEAGUES: dict[str, dict] = {
    "premier_league": {
        "id": 39,
        "name": "Premier League",
        "country": "England",
        "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
        "difficulty": 1.00,
        "type": "league",
        "bdl_slug": "epl",
        "bdl_version": "v2",
    },
    "bundesliga": {
        "id": 78,
        "name": "Bundesliga",
        "country": "Germany",
        "flag": "🇩🇪",
        "difficulty": 1.00,
        "type": "league",
        "bdl_slug": "bundesliga",
        "bdl_version": "v1",
    },
    "la_liga": {
        "id": 140,
        "name": "La Liga",
        "country": "Spain",
        "flag": "🇪🇸",
        "difficulty": 1.00,
        "type": "league",
        "bdl_slug": "laliga",
        "bdl_version": "v1",
    },
}

# LEAGUE_BY_ID derives from SUPPORTED_LEAGUES which already has correct flags set above.
LEAGUE_BY_ID: dict[int, dict] = {cfg["id"]: cfg for cfg in SUPPORTED_LEAGUES.values()}
LEAGUE_KEY_BY_ID: dict[int, str] = {
    cfg["id"]: key for key, cfg in SUPPORTED_LEAGUES.items()
}
DEFAULT_LEAGUE_ID: int = SUPPORTED_LEAGUES[DEFAULT_LEAGUE_KEY]["id"]
SUPPORTED_LEAGUE_IDS: list[int] = [cfg["id"] for cfg in SUPPORTED_LEAGUES.values()]

# Convenience: difficulty by league id
COMP_DIFFICULTY: dict[int, float] = {cfg["id"]: cfg["difficulty"] for cfg in SUPPORTED_LEAGUES.values()}

# Domestic leagues grouped by country (for cross-comp filtering)
DOMESTIC_LEAGUE_IDS: set[int] = {
    cfg["id"] for cfg in SUPPORTED_LEAGUES.values() if cfg["type"] == "league"
}
EUROPEAN_COMP_IDS: set[int] = {
    cfg["id"] for cfg in SUPPORTED_LEAGUES.values() if cfg["type"] == "european"
}
CUP_IDS: set[int] = set()


def get_difficulty(league_id: int) -> float:
    """Return the competition difficulty weight for a league id (default 1.0)."""
    return COMP_DIFFICULTY.get(league_id, 1.00)


def get_league_info(league_id: int) -> dict | None:
    """Return the league config dict for a given id, or None if not found."""
    return LEAGUE_BY_ID.get(league_id)


def get_league_key(league_id: int) -> str | None:
    """Return the internal league key for a given competition id."""
    return LEAGUE_KEY_BY_ID.get(league_id)


def all_leagues() -> list[dict]:
    """Return all supported competitions in UI order."""
    return list(SUPPORTED_LEAGUES.values())


def leagues_for_country(country: str) -> list[dict]:
    """Return all leagues/cups for a given country name."""
    return [cfg for cfg in SUPPORTED_LEAGUES.values() if cfg["country"] == country]
