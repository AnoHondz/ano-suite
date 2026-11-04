"""Import nflverse into ScorPred's local SQLite store without starting Flask.

python -m scripts.sync_nflverse --seasons 2022,2023,2024,2025,2026
python -m scripts.sync_nflverse --seasons 2026 --datasets player_stats,rosters,depth_charts,pbp
"""
import argparse
import json
from datetime import UTC, datetime

from runtime_paths import cache_dir
from services import nflverse_store as store
from services.nfl_catalog import CATALOG, refresh_seconds

CORE_DATASETS = ("schedules", "teams", "team_stats")
FREE_DATASETS = CORE_DATASETS + (
    "player_stats", "rosters", "depth_charts", "snap_counts", "ngs_passing",
    "ngs_rushing", "ngs_receiving", "weekly_rosters", "players", "injuries",
)
ANALYTICS_DATASETS = FREE_DATASETS + ("pbp", "participation", "ftn_charting", "team_season", "player_season") + tuple(
    name for name in CATALOG if name.startswith("pfr_"))
SUPPORTED_DATASETS = frozenset(CATALOG)

PBP_COLUMNS = ["game_id", "play_id", "season", "week", "posteam", "defteam", "play_type", "no_play",
               "epa", "success", "yards_gained", "pass_attempt", "rush_attempt", "qb_kneel", "qb_spike",
               "qb_dropback", "sack", "qb_scramble", "complete_pass", "interception", "fumble_lost",
               "third_down_converted", "third_down_failed", "down", "yardline_100", "fixed_drive",
               "drive", "pass_touchdown", "rush_touchdown", "touchdown", "passer_player_id",
               "passing_yards", "qb_hit", "two_point_attempt", "play_type_nfl"]


def default_season(now=None):
    now = now or datetime.now(UTC)
    return now.year - 1 if now.month <= 2 else now.year


def sync(seasons, datasets=CORE_DATASETS, *, path=None, keep_going=False, refresh=False,
         force=False, missing_only=False, derive=False, now=None):
    supplied_now = now
    now = now or datetime.now(UTC)
    seasons = sorted(set(seasons))
    datasets = list(dict.fromkeys(datasets))
    unknown = set(datasets) - SUPPORTED_DATASETS
    if unknown or not datasets:
        raise ValueError(f"Unknown or empty dataset selection: {sorted(unknown)}")
    if not seasons or any(type(year) is not int or year < 1999 or year > now.year for year in seasons):
        raise ValueError("Use NFL seasons from 1999 through the current year")

    import nflreadpy as nfl
    import polars as pl
    from nflreadpy.config import update_config

    update_config(cache_mode="filesystem", cache_dir=cache_dir("nflverse", "downloads").resolve(),
                  cache_duration=300, timeout=25)
    reports = []
    entries = {(row["dataset"], row["season"]): row for row in store.metadata(path=path)}
    attempts = {(row["dataset"], row["season"]): row for row in store.refresh_status(path=path)}
    for dataset in datasets:
        spec = CATALOG[dataset]
        for season in ([0] if dataset in {"teams", "players"} else seasons):
            try:
                unsupported = season and season < spec.first_season
                # nflreadpy uses roster year here, including January/February.
                if dataset == "participation" and season >= now.year:
                    unsupported = True
                if unsupported:
                    report = {"dataset": dataset, "season": season, "status": "unsupported"}
                    store.record_refresh(dataset, season, "unsupported", now=now, path=path)
                    reports.append(report)
                    print(json.dumps(report), flush=True)
                    continue
                entry = entries.get((dataset, season))
                age = (now - datetime.fromisoformat(entry["synced_at"])).total_seconds() if entry else None
                interval = refresh_seconds(dataset, season, default_season(now))
                attempt = attempts.get((dataset, season))
                retry_age = (now - datetime.fromisoformat(attempt["attempted_at"])).total_seconds() if attempt else None
                backoff = (refresh and attempt and attempt["status"] in {"failed", "unavailable"}
                           and 0 <= retry_age < min(interval, 3600))
                if not force and ((missing_only and entry) or (refresh and age is not None and 0 <= age < interval)
                                  or backoff):
                    report = {"dataset": dataset, "season": season, "status": attempt["status"] if backoff else "cached"}
                    if backoff:
                        report.update(retry_backoff=True, error=attempt["error"])
                    reports.append(report)
                    print(json.dumps(report), flush=True)
                    continue
                # Match the loader cache TTL to the dataset instead of treating
                # a cached response as a newly downloaded source every 5 minutes.
                update_config(cache_duration=0 if force else interval)
                loader = getattr(nfl, spec.loader)
                frame = loader(**dict(spec.kwargs)) if spec.global_snapshot else loader(season, **dict(spec.kwargs))
                if dataset == "schedules":
                    required = {"game_id", "season", "game_type", "week", "gameday", "gametime", "home_team", "away_team", "espn"}
                    if not required.issubset(frame.columns):
                        raise ValueError("Unsupported nflverse schedule schema")
                    frame = frame.filter(pl.col("season") == season)
                if dataset == "pbp":
                    if not {"game_id", "play_id", "posteam", "epa", "success"}.issubset(frame.columns):
                        raise ValueError("Unsupported play-by-play schema")
                    frame = frame.select([column for column in PBP_COLUMNS if column in frame.columns])
                if dataset == "depth_charts":
                    # Historical assets contain exact duplicate source rows.
                    # Collapse only identical rows; conflicting identities fail.
                    frame = frame.unique(maintain_order=True)
                # Keep every historical depth snapshot, including pre-2025 weeks.
                observed = supplied_now or datetime.now(UTC)
                report = store.replace_dataset(dataset, season, frame.iter_rows(named=True), path=path, synced_at=observed,
                    collection_started_at=now, source_identifier=f"nflverse/nflreadpy:{spec.loader}",
                    receipt_metadata={"loader_cache_seconds": 0 if force else interval,
                                      "publication_timestamp_verified": False})
                store.record_refresh(dataset, season, "success", now=observed, path=path)
            except Exception as exc:
                code = getattr(getattr(exc, "response", None), "status_code", None)
                status = "unavailable" if code == 404 or isinstance(exc, store.EmptyDatasetError) else "failed"
                store.record_refresh(dataset, season, status, error=type(exc).__name__, now=supplied_now or datetime.now(UTC), path=path)
                if not keep_going:
                    raise
                # Keep successful feeds and preserve the failed feed's previous
                # rows/timestamp. A partial import still exits nonzero in main.
                report = {"dataset": dataset, "season": season, "status": status,
                          "error": type(exc).__name__}
            reports.append(report)
            print(json.dumps(report), flush=True)
    if derive:
        from services.nfl_analytics import refresh_analytics
        for season in seasons:
            reports.extend(refresh_analytics(season, path=path, now=supplied_now, force=force))
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", default=str(default_season()))
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--datasets", help="Comma-separated dataset names")
    selection.add_argument("--profile", choices=("core", "free", "analytics"), help="analytics includes PBP, PFR, FTN, participation and seasonal statistics")
    parser.add_argument("--keep-going", action="store_true", help="Continue independent feeds after failures; exit nonzero if any fail")
    parser.add_argument("--database")
    parser.add_argument("--refresh", action="store_true", help="Import missing seasons and refresh only datasets due under per-feed intervals")
    parser.add_argument("--force", action="store_true", help="Bypass freshness and loader caches for explicit correction imports")
    parser.add_argument("--missing-only", action="store_true", help="Skip datasets/seasons already stored")
    parser.add_argument("--derive", action="store_true", help="Recalculate changed team and quarterback game metrics offline")
    parser.add_argument("--status", action="store_true", help="Inspect stored coverage and latest refresh attempts without downloading")
    args = parser.parse_args()
    if args.status:
        print(json.dumps({"datasets": store.metadata(path=args.database), "refresh": store.refresh_status(path=args.database),
                          "collection": store.collection_status(path=args.database)}, indent=2))
        return
    try:
        seasons = sorted({int(value.strip()) for value in args.seasons.split(",")})
        datasets = [value.strip() for value in args.datasets.split(",")] if args.datasets is not None else (
            ANALYTICS_DATASETS if args.profile == "analytics" else FREE_DATASETS if args.profile == "free" else CORE_DATASETS)
        options = {name: True for name in ("refresh", "force", "missing_only", "derive") if getattr(args, name)}
        reports = sync(seasons, datasets, path=args.database, keep_going=args.keep_going, **options)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({"imported": reports, "database": str(args.database or store.database_path())}, indent=2))
    if any(report.get("status") in {"failed", "unavailable"} for report in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
