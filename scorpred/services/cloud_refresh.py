"""Small serving-data refresh; never imports the large NFL research archive."""
from datetime import UTC, datetime

DATASETS = ("schedules", "teams", "players", "player_stats", "injuries")


def refresh_nfl(*, now=None, syncer=None):
    from scripts.sync_nflverse import sync, default_season
    season = default_season(now or datetime.now(UTC))
    # Props use the current season and a previous-season player prior. No PBP,
    # research snapshots, training, or model activation is requested here.
    return (syncer or sync)([season - 1, season], DATASETS, refresh=True, keep_going=True)
