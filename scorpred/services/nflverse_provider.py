"""Read nflverse schedules locally; refresh lightweight schedules independently.

Large play-by-play/stat imports run through the CLI, never in a web request.
ESPN IDs are stored as external event references, not treated as team IDs.
"""
import csv
import gzip
import io
import math
import threading
from datetime import UTC, datetime, timedelta

import requests

import nfl_client
from services import nflverse_store as store
from services.data_freshness import metadata, worst
from services.prediction_lifecycle import parse_kickoff

SCHEDULE_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv.gz"
_refresh_lock = threading.Lock()
SCHEDULE_FRESH_SECONDS = 300
SCHEDULE_MAX_SECONDS = 86400
FORM_FRESH_SECONDS = 172800
STATS_FRESH_SECONDS = 172800
CONTEXT_DATASETS = ("team_stats", "player_stats", "rosters", "depth_charts", "pbp",
                    "snap_counts", "ngs_passing", "ngs_rushing", "ngs_receiving",
                    "weekly_rosters", "players", "injuries")


class NFLVerseUnavailable(RuntimeError):
    pass


def refresh_schedules(seasons, *, now=None, path=None, collector_metadata=None):
    now = now or datetime.now(UTC)
    response = None
    try:
        response = requests.get(SCHEDULE_URL, timeout=(4, 15), stream=True)
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 8_000_000:
                raise ValueError("Oversized schedule response")
            chunks.append(chunk)
        with gzip.GzipFile(fileobj=io.BytesIO(b"".join(chunks))) as compressed:
            raw = compressed.read(30_000_001)
        if len(raw) > 30_000_000:
            raise ValueError("Oversized decompressed schedule")
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        required = {"game_id", "season", "game_type", "gameday", "gametime", "home_team", "away_team"}
        if not rows or not required.issubset(rows[0]):
            raise ValueError("Unsupported schedule schema")
        parts = {season: [row for row in rows if row["season"] == str(season)] for season in seasons}
        if any(not part for part in parts.values()):
            raise ValueError("Requested NFL season missing")
        for season, part in parts.items():
            # A request's evaluation clock is not the network receipt time.
            received = datetime.now(UTC)
            store.replace_dataset("schedules", season, part, path=path, synced_at=received, collection_started_at=now,
                                  source_identifier=SCHEDULE_URL,
                                  receipt_metadata={"http_last_modified": response.headers.get("Last-Modified"),
                                                    "http_etag": response.headers.get("ETag"),
                                                    "publication_timestamp_verified": False, **(collector_metadata or {})})
            store.record_refresh("schedules", season, "success", path=path, now=received)
        return received
    except Exception as exc:
        for season in seasons:
            store.record_refresh("schedules", season, "failed", path=path, error=type(exc).__name__, now=datetime.now(UTC))
        raise
    finally:
        if response is not None:
            response.close()


def load_records(anchor, end, now):
    season = anchor.year - 1 if anchor.month <= 2 else anchor.year
    seasons = {season}
    if end.year != anchor.year and end.month > 2:
        seasons.add(end.year)
    teams = {row["team_abbr"]: row for row in store.records("teams")}
    if not teams:
        raise NFLVerseUnavailable("nflverse has not been synchronized")
    meta = store.metadata()
    stamps = {row["season"]: stamp for row in meta if row["dataset"] == "schedules"
              and (stamp := parse_kickoff(row["synced_at"])) is not None}
    fresh = all(year in stamps and timedelta(0) <= now - stamps[year] < timedelta(minutes=5) for year in seasons)
    warning = None
    if not fresh:
        acquired = _refresh_lock.acquire(blocking=False)
        try:
            if not acquired:
                if not all(year in stamps and timedelta(0) <= now - stamps[year] < timedelta(hours=24) for year in seasons):
                    raise NFLVerseUnavailable("nflverse refresh is already in progress")
                warning = "nflverse schedules are refreshing; showing the last synchronized snapshot."
            else:
                try:
                    received = refresh_schedules(seasons, now=now)
                    if isinstance(received, datetime):
                        now = max(now, received)
                except (requests.RequestException, ValueError, OSError) as exc:
                    if not all(year in stamps and timedelta(0) <= now - stamps[year] < timedelta(hours=24) for year in seasons):
                        raise NFLVerseUnavailable("nflverse schedules are unavailable") from exc
                    warning = "nflverse schedule refresh is delayed; showing the last synchronized snapshot."
        finally:
            if acquired:
                _refresh_lock.release()
    # A successful refresh must expose its real new observation time. A failed
    # refresh keeps the original timestamp and remains visibly degraded.
    meta = store.metadata()
    stamps = {row["season"]: row["synced_at"] for row in meta if row["dataset"] == "schedules"}
    schedule_sources = {year: metadata("nflverse schedules", stamps.get(year), SCHEDULE_FRESH_SECONDS,
                                      SCHEDULE_MAX_SECONDS, degraded=bool(warning), now=now) for year in seasons}
    output = []
    for year in seasons:
        for row in store.records("schedules", year):
            normalized = normalize_game(row, teams, now)
            if normalized:
                normalized["schedule_freshness"] = schedule_sources[year]
                normalized["form_freshness"] = metadata("nflverse historical scores", stamps.get(year),
                                                         FORM_FRESH_SECONDS, FORM_FRESH_SECONDS, now=now)
                normalized["data_freshness"] = schedule_sources[year]
                output.append(normalized)
    if not output:
        raise NFLVerseUnavailable("No usable nflverse schedule records")
    coverage = {row["dataset"] for row in meta if row["season"] == season and row["row_count"] > 0}
    datasets = {name: dataset_freshness(name, 0 if name == "players" else season, now=now, entries=meta)
                for name in CONTEXT_DATASETS}
    return output, {"source": "nflverse", "warning": warning, "coverage": sorted(coverage),
                    "data_freshness": worst(schedule_sources.values(), now=now), "datasets": datasets}


def dataset_freshness(dataset, season, *, now=None, entries=None):
    """Current-season research stats must have been observed within 48 hours.

    Old season archives remain useful for offline training, but their timestamp
    is not rewritten or called current merely because a web request reads them.
    """
    now = now or datetime.now(UTC)
    entries = store.metadata() if entries is None else entries
    entry = next((row for row in entries if row["dataset"] == dataset and row["season"] == season and row["row_count"] > 0), None)
    result = metadata(f"nflverse {dataset}", entry["synced_at"] if entry else None,
                      STATS_FRESH_SECONDS, STATS_FRESH_SECONDS, now=now)
    result.update(season=season, rows=entry["row_count"] if entry else 0,
                  latest_week=(entry.get("coverage") or {}).get("latest_week") if entry else None)
    current_season = now.year - 1 if now.month <= 2 else now.year
    if 0 < season < current_season:
        result["archived"] = True
    return result


def number(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def normalize_game(row, teams, now):
    if row.get("game_type") not in {"REG", "WC", "DIV", "CON", "SB"}:
        return None
    try:
        # nflverse publishes gametime in US Eastern, including DST.
        kickoff = datetime.fromisoformat(f'{row["gameday"]}T{row["gametime"]}').replace(tzinfo=nfl_client.EASTERN).astimezone(UTC)
        home, away = teams[row["home_team"]], teams[row["away_team"]]
    except (ValueError, KeyError, TypeError):
        return None  # TBD kickoff or identity; never invent a date/team.
    hs, aws = number(row.get("home_score")), number(row.get("away_score"))
    completed = hs is not None and aws is not None and kickoff < now
    external_id = number(row.get("espn"))
    return {"id": str(int(external_id)) if external_id else f'nflverse:{row["game_id"]}',
            "nflverse_id": row["game_id"], "data_source": "nflverse",
            "kickoff": kickoff.isoformat(), "date": kickoff.astimezone(nfl_client.EASTERN).date().isoformat(),
            "season": int(row["season"]), "season_type": 2 if row["game_type"] == "REG" else 3,
            "week": int(row["week"]), "standard": True,
            "state": "post" if completed else ("pre" if kickoff > now else "in"), "completed": completed,
            "status_name": "STATUS_FINAL" if completed else ("STATUS_SCHEDULED" if kickoff > now else "STATUS_IN_PROGRESS"),
            "status": "Final" if completed else ("Scheduled" if kickoff > now else "Kickoff passed; live status unavailable"),
            "neutral": row.get("location") == "Neutral", "venue": row.get("stadium") or "Venue unavailable",
            "home_id": row["home_team"], "away_id": row["away_team"],
            "home_name": home["team_name"], "away_name": away["team_name"],
            "home_logo": home.get("team_logo_espn", ""), "away_logo": away.get("team_logo_espn", ""),
            "home_score": int(hs) if completed else None, "away_score": int(aws) if completed else None}
