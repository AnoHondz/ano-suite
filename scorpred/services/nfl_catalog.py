"""Documented nflreadpy 0.1.5 dataset selection and refresh policy.

Start years describe loader support, not guaranteed upstream completeness.
See docs/NFL_ANALYTICS.md for release URLs, limitations and licensing.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    loader: str
    first_season: int = 1999
    interval: int = 86400
    global_snapshot: bool = False
    kwargs: tuple = ()


CATALOG = {
    "schedules": Dataset("load_schedules", interval=300),
    "teams": Dataset("load_teams", global_snapshot=True),
    "players": Dataset("load_players", global_snapshot=True),
    "team_stats": Dataset("load_team_stats"),
    "player_stats": Dataset("load_player_stats"),
    "team_season": Dataset("load_team_stats", kwargs=(("summary_level", "reg+post"),)),
    "player_season": Dataset("load_player_stats", kwargs=(("summary_level", "reg+post"),)),
    "pbp": Dataset("load_pbp"),
    "rosters": Dataset("load_rosters", 1920),
    "weekly_rosters": Dataset("load_rosters_weekly", 2002),
    "depth_charts": Dataset("load_depth_charts", 2001),
    "snap_counts": Dataset("load_snap_counts", 2012, 21600),
    "injuries": Dataset("load_injuries", 2009),
    "participation": Dataset("load_participation", 2016, 2592000),
    "ftn_charting": Dataset("load_ftn_charting", 2022, 21600),
}
for kind in ("passing", "rushing", "receiving"):
    CATALOG[f"ngs_{kind}"] = Dataset("load_nextgen_stats", 2016, kwargs=(("stat_type", kind),))
for kind in ("pass", "rush", "rec", "def"):
    for level in ("week", "season"):
        CATALOG[f"pfr_{kind}_{level}"] = Dataset(
            "load_pfr_advstats", 2018, 21600,
            kwargs=(("stat_type", kind), ("summary_level", level)))


def refresh_seconds(dataset, season, current_season):
    """Per-feed overrides; completed archives default to a 30-day cache."""
    spec = CATALOG[dataset]
    default = 2592000 if 0 < season < current_season else spec.interval
    value = int(os.getenv(f"NFL_REFRESH_{dataset.upper()}_SECONDS", str(default)))
    if value < 0:
        raise ValueError("Refresh intervals must be nonnegative")
    return value


def canonical_team(value):
    # Source spelling aliases only; relocation-era IDs remain distinct.
    return {"LAR": "LA", "JAC": "JAX", "WSH": "WAS"}.get(value, value)


def player_ids(rows):
    """Crosswalk only explicit IDs. Never match player names heuristically."""
    output = {}
    for row in rows:
        if row.get("gsis_id"):
            for field in ("gsis_id", "pfr_id", "pfr_player_id", "espn_id"):
                if row.get(field):
                    output[(field, str(row[field]))] = row["gsis_id"]
    return output
