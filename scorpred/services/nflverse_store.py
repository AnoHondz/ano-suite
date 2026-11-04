"""Local, transactional nflverse feature store; the web process only needs SQLite.

Provider IDs are kept distinct. Raw payloads may contain postgame/weather/closing
price fields; model features are built from an explicit whitelist elsewhere.
"""
import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from runtime_paths import cache_dir


class EmptyDatasetError(ValueError):
    """A published dataset contains no rows for the requested season."""


def source_team(row):
    return (row.get("team") or row.get("posteam") or row.get("team_abbr") or row.get("latest_team")
            or row.get("recent_team") or row.get("club_code") or row.get("tm") or row.get("possession_team"))


def record_key(dataset, season, row):
    """Stable provider identities, independent of source row order."""
    team = row.get("team") or row.get("team_abbr") or row.get("recent_team") or row.get("club_code") or row.get("tm")
    player = row.get("gsis_id") or row.get("player_id") or row.get("player_gsis_id")
    if not player and dataset in {"rosters", "weekly_rosters"} and row.get("full_name"):
        player = ["unlinked", row["full_name"], row.get("birth_date"), row.get("position")]
    if dataset == "players":
        parts = [row.get("gsis_id")]
        if season != 0 or not row.get("display_name"):
            raise ValueError("Player directory requires GSIS identity and a global snapshot")
    elif dataset == "teams":
        parts = [row.get("team_abbr")]
        if not row.get("team_name"):
            raise ValueError("Invalid nflverse team record")
    elif dataset == "schedules":
        parts = [row.get("game_id"), row.get("home_team"), row.get("away_team")]
    elif dataset in {"pbp", "participation", "ftn_charting"}:
        parts = [row.get("game_id") or row.get("nflverse_game_id"),
                 row.get("play_id") if dataset != "ftn_charting" else row.get("nflverse_play_id")]
        if parts[1] is None and dataset == "ftn_charting":
            parts[1] = row.get("ftn_play_id")
            if parts[1] is not None:
                parts[1] = "ftn:" + str(int(float(parts[1])))
        elif parts[1] is not None:
            value = float(parts[1])
            if not math.isfinite(value) or not value.is_integer():
                raise ValueError("Play identifiers must be finite integers")
            parts[1] = str(int(value))
    elif dataset in {"team_stats", "advanced_team_games"}:
        parts = [row.get("game_id"), team]
    elif dataset in {"player_stats", "advanced_qb_games"}:
        if dataset == "player_stats" and player is None and not row.get("player_display_name") and row.get("position") is None:
            # Upstream includes team-only residuals (e.g. unassigned penalties).
            # This is a record namespace, never a fabricated player identifier.
            player = ["unassigned_team_totals"]
        parts = [row.get("game_id"), team, player]
        if not row.get("game_id"):
            parts = [row.get("season_type"), row.get("week"), team, player]
    elif dataset in {"team_season", "player_season"}:
        parts = [team, row.get("season_type", "REG+POST")]
        if dataset == "player_season":
            parts.append(player or (["unassigned_team_totals"] if not row.get("player_display_name") and row.get("position") is None else None))
    elif dataset == "depth_charts":
        parts = [team, row.get("dt") or row.get("week") or "undated", player or row.get("espn_id") or row.get("elias_id"),
                 row.get("pos_grp_id") or row.get("formation") or "unknown",
                 row.get("pos_id") or row.get("depth_position") or row.get("position") or "unknown",
                 row.get("pos_slot", row.get("depth_team", "unknown"))]
        if not row.get("dt"):
            parts.append(row.get("game_type") or "unknown")
    elif dataset == "rosters":
        parts = [team, player]
    elif dataset == "snap_counts" or dataset.startswith("pfr_"):
        identity = row.get("pfr_player_id") or row.get("pfr_id")
        if dataset.startswith("pfr_") and not identity and row.get("player"):
            identity = ["unlinked", row["player"], row.get("pos")]
        parts = [team, identity]
        if not dataset.endswith("_season"):
            parts.append(row.get("game_id"))
    elif dataset in {"weekly_rosters", "injuries"} or dataset.startswith("ngs_"):
        parts = [row.get("season_type") or row.get("game_type"), row.get("week"), team, player]
        if dataset == "injuries" and row.get("date_modified"):
            parts.append(row["date_modified"])
    else:
        raise ValueError(f"Unsupported dataset: {dataset}")
    if any(part is None or part == "" for part in parts):
        raise ValueError(f"{dataset} requires stable provider identity")
    if season and dataset != "depth_charts" and int(row.get("season", -1)) != season:
        # Participation files identify season in the native game ID only.
        game = row.get("game_id") or row.get("nflverse_game_id") or ""
        if dataset != "participation" or not game.startswith(f"{season}_"):
            raise ValueError("Record requires matching season")
    if dataset in {"weekly_rosters", "injuries", "snap_counts"} or dataset.startswith("ngs_"):
        week = row.get("week")
        if type(week) is not int or not 0 <= week <= 30:
            raise ValueError("Context stats require a valid week")
    # Retain established identities for datasets already using natural keys.
    if dataset in {"teams", "players", "schedules"}:
        return str(parts[0])
    if dataset in {"team_stats", "pbp"}:
        return "|".join(map(str, parts))
    return json.dumps(parts, sort_keys=True, separators=(",", ":"))


def _migrate_availability(db):
    """Apply additive, versioned migrations, retaining all legacy observations."""
    db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    if db.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone():
        return
    with db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone():
            return
        if not db.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone():
            sql = (Path(__file__).resolve().parents[1] / "migrations/nflverse/001_availability.sql").read_text()
            for statement in sql.split(";"):
                if statement.strip():
                    db.execute(statement)
            # Previously positional records become natural keys. Fail atomically
            # on ambiguous identities rather than dropping existing records.
            for row in db.execute("SELECT r.*, d.synced_at FROM records r JOIN datasets d USING(dataset,season)").fetchall():
                key = record_key(row["dataset"], row["season"], json.loads(row["payload"]))
                db.execute("UPDATE records SET record_key=?, observed_at=? WHERE dataset=? AND season=? AND record_key=?",
                           (key, row["synced_at"], row["dataset"], row["season"], row["record_key"]))
                db.execute("INSERT INTO record_versions VALUES (?,?,?,?,?)",
                           (row["dataset"], row["season"], key, row["synced_at"], row["payload"]))
            db.execute("INSERT INTO schema_migrations VALUES (1,?)", (datetime.now(UTC).isoformat(),))
        # Data migration 2: integer and floating-point source play IDs identify
        # the same play. Change keys only, preserving payloads and observations.
        for table in ("records", "record_versions"):
            rows = db.execute(f"SELECT dataset,season,record_key,observed_at,payload FROM {table} "
                              "WHERE dataset IN ('pbp','participation','ftn_charting')").fetchall()
            for row in rows:
                key = record_key(row["dataset"], row["season"], json.loads(row["payload"]))
                if key != row["record_key"]:
                    db.execute(f"UPDATE {table} SET record_key=? WHERE dataset=? AND season=? AND record_key=? AND observed_at=?",
                               (key, row["dataset"], row["season"], row["record_key"], row["observed_at"]))
        db.execute("INSERT INTO schema_migrations VALUES (2,?)", (datetime.now(UTC).isoformat(),))


WRITER_VERSION = "nflverse-collection-v4"


def migrate(db):
    _migrate_availability(db)
    if db.execute("SELECT 1 FROM schema_migrations WHERE version=5").fetchone():
        return
    for version, filename in ((3, "003_collection_receipts.sql"), (4, "004_writer_guard.sql"), (5, "005_forecast_archive.sql")):
        with db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone():
                continue
            sql = (Path(__file__).resolve().parents[1] / "migrations/nflverse" / filename).read_text()
            pending = ""
            for line in sql.splitlines(keepends=True):
                pending += line
                if sqlite3.complete_statement(pending):
                    db.execute(pending)
                    pending = ""
            if pending.strip():
                raise ValueError("Incomplete collection migration")
            db.execute("INSERT INTO schema_migrations VALUES (?,?)", (version, datetime.now(UTC).isoformat()))


def enable_versioned_writes(*, path=None):
    """Local collector handoff: reject legacy deletes/untracked writes atomically."""
    with _connection(path, ingestion=True) as db, db:
        db.execute("INSERT INTO collection_control VALUES ('require_versioned_writes',1,?) "
                   "ON CONFLICT(key) DO UPDATE SET enabled=1,changed_at=excluded.changed_at", (datetime.now(UTC).isoformat(),))


def database_path():
    return cache_dir("nflverse") / "nflverse.sqlite3"


@contextmanager
def _connection(path=None, *, ingestion=False):
    target = Path(path or database_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    from services.nfl_holdout import SealedConnection
    db = sqlite3.connect(target, timeout=30, factory=SealedConnection)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS datasets (
                dataset TEXT NOT NULL, season INTEGER NOT NULL, synced_at TEXT NOT NULL,
                row_count INTEGER NOT NULL, sha256 TEXT NOT NULL,
                PRIMARY KEY(dataset, season));
            CREATE TABLE IF NOT EXISTS records (
                dataset TEXT NOT NULL, season INTEGER NOT NULL, record_key TEXT NOT NULL,
                observed_at TEXT, game_id TEXT, team TEXT, payload TEXT NOT NULL,
                PRIMARY KEY(dataset, season, record_key));
            CREATE TABLE IF NOT EXISTS dataset_details (
                dataset TEXT NOT NULL, season INTEGER NOT NULL, details TEXT NOT NULL,
                PRIMARY KEY(dataset, season));
            CREATE INDEX IF NOT EXISTS records_game ON records(dataset, game_id);
            CREATE INDEX IF NOT EXISTS records_team ON records(dataset, season, team);
        """)
        migrate(db)
        if not ingestion:
            from services.nfl_holdout import protect
            protect(db)
        yield db
    finally:
        db.close()


def connection(path=None):
    """Application/research connection with sealed results masked before decode."""
    return _connection(path)


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean(v) for v in value]
    return value


def replace_dataset(dataset, season, rows, *, path=None, synced_at=None, source_identifier=None,
                    source_publication_at=None, collection_started_at=None, receipt_metadata=None):
    """Validate then upsert atomically; omitted records and old versions survive.

    The legacy name is retained for callers. Refresh time is independent of each
    record's first observation; repeated identical pulls do not rewrite it.
    """
    clock = synced_at or datetime.now(UTC)
    if clock.tzinfo is None:
        raise ValueError("Refresh timestamp requires timezone")
    stamp = clock.astimezone(UTC).isoformat()
    started = collection_started_at or clock
    if started.tzinfo is None or started > clock:
        raise ValueError("Collection must start before its timezone-aware receipt")
    if source_publication_at is not None and (source_publication_at.tzinfo is None or source_publication_at > clock):
        raise ValueError("Source publication must be a known timestamp no later than receipt")
    encoded, seen = [], set()
    for original in rows:
        row = clean(original)
        if not isinstance(row, dict):
            raise TypeError("Expected a record object")
        key = record_key(dataset, season, row)
        if key in seen:
            raise ValueError("Duplicate provider record key")
        seen.add(key)
        payload = json.dumps(row, sort_keys=True, allow_nan=False, separators=(",", ":"))
        team = source_team(row)
        encoded.append((dataset, season, key, stamp, row.get("game_id") or row.get("nflverse_game_id"),
                        team, payload))
    if not encoded:
        raise EmptyDatasetError("Empty source response; previous dataset preserved")
    with _connection(path, ingestion=True) as db, db:
        previous = db.execute("SELECT synced_at FROM datasets WHERE dataset=? AND season=?", (dataset, season)).fetchone()
        if previous and previous[0] > stamp:
            raise ValueError("Refresh timestamp cannot precede the last successful refresh")
        for item in encoded:
            old = db.execute("SELECT payload,observed_at FROM records WHERE dataset=? AND season=? AND record_key=?", item[:3]).fetchone()
            if old is None or old[0] != item[6] or old[1] is None:
                if old and old[1] is not None and old[1] >= stamp:
                    raise ValueError("Changed records require a later observation timestamp")
                db.execute("INSERT INTO record_versions VALUES (?,?,?,?,?)",
                           (*item[:4], item[6]))
                db.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?) ON CONFLICT(dataset,season,record_key) DO UPDATE SET "
                           "observed_at=excluded.observed_at,game_id=excluded.game_id,team=excluded.team,payload=excluded.payload", item)
        # Fingerprint the retained union, sorted by stable key. Source row order
        # and unchanged downloads cannot invalidate derived metrics.
        digest = hashlib.sha256()
        retained = db.execute("SELECT payload FROM records WHERE dataset=? AND season=? ORDER BY record_key", (dataset, season)).fetchall()
        weeks, teams = set(), set()
        for item in retained:
            digest.update(item[0].encode())
            row = json.loads(item[0])
            team = source_team(row)
            if team:
                teams.add(team)
            if type(row.get("week")) is int and row["week"] > 0:
                weeks.add(row["week"])
        db.execute("INSERT OR REPLACE INTO datasets VALUES (?,?,?,?,?)",
                   (dataset, season, stamp, len(retained), digest.hexdigest()))
        details = {"weeks": sorted(weeks), "latest_week": max(weeks) if weeks else None,
                   "teams": sorted(teams)}
        db.execute("INSERT OR REPLACE INTO dataset_details VALUES (?,?,?)",
                   (dataset, season, json.dumps(details)))
        content = hashlib.sha256()
        game_hashes = {}
        for item in sorted(encoded, key=lambda item: item[2]):
            content.update(item[6].encode())
            if item[4]:
                game_hashes.setdefault(item[4], hashlib.sha256()).update(item[6].encode())
        receipt = db.execute("INSERT INTO collection_receipts(dataset,season,attempted_at,collected_at,ingested_at,"
            "source_identifier,source_publication_at,status,content_sha256,row_count,writer_version,error,metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (dataset, season, started.astimezone(UTC).isoformat(), stamp, stamp,
            source_identifier or f"nflverse:{dataset}", source_publication_at.astimezone(UTC).isoformat() if source_publication_at else None,
            "success", content.hexdigest(), len(encoded), WRITER_VERSION, None, json.dumps(receipt_metadata or {}, sort_keys=True)))
        receipt_id = receipt.lastrowid
        db.executemany("INSERT INTO collection_games VALUES (?,?,?)",
                       [(receipt_id, game_id, value.hexdigest()) for game_id, value in sorted(game_hashes.items())])
    return {"dataset": dataset, "season": season, "rows": len(retained), "sha256": digest.hexdigest(),
            "collection_receipt_id": receipt_id}


def records_as_of(dataset, season, before, *, path=None):
    """Latest *locally observed* version at cutoff, with no inferred backdating."""
    if before.tzinfo is None:
        raise ValueError("Availability cutoff requires timezone")
    if not Path(path or database_path()).exists():
        return []
    with connection(path) as db:
        rows = db.execute("SELECT payload, observed_at FROM (SELECT payload, observed_at, ROW_NUMBER() OVER "
                          "(PARTITION BY record_key ORDER BY observed_at DESC) AS rank FROM record_versions "
                          "WHERE dataset=? AND season=? AND observed_at<=?) WHERE rank=1",
                          (dataset, season, before.astimezone(UTC).isoformat()))
        return [{**json.loads(row[0]), "_observed_at": row[1]} for row in rows]


def observation_integrity(dataset, season, *, path=None):
    """Detect old/untracked writers; never infer when their payload was known."""
    if not Path(path or database_path()).exists():
        return {"tracked": False, "rows": 0, "untracked_rows": 0}
    with connection(path) as db:
        row = db.execute("SELECT COUNT(*),COALESCE(SUM(CASE WHEN r.observed_at IS NULL OR NOT EXISTS "
                         "(SELECT 1 FROM record_versions v WHERE v.dataset=r.dataset AND v.season=r.season "
                         "AND v.record_key=r.record_key AND v.observed_at=r.observed_at AND v.payload=r.payload) "
                         "THEN 1 ELSE 0 END),0) FROM records r WHERE r.dataset=? AND r.season=?", (dataset, season)).fetchone()
        return {"tracked": row[0] > 0 and row[1] == 0, "rows": row[0], "untracked_rows": row[1]}


def record_refresh(dataset, season, status, *, error=None, now=None, path=None):
    stamp = now or datetime.now(UTC)
    if stamp.tzinfo is None:
        raise ValueError("Refresh receipt requires timezone")
    stamp = stamp.astimezone(UTC).isoformat()
    with connection(path) as db, db:
        db.execute("INSERT INTO refresh_attempts(dataset,season,attempted_at,status,error) VALUES (?,?,?,?,?)",
                   (dataset, season, stamp, status, error))
        if status in {"failed", "unavailable"}:
            db.execute("INSERT INTO collection_receipts(dataset,season,attempted_at,source_identifier,status,writer_version,error,metadata) "
                       "VALUES (?,?,?,?,?,?,?,?)", (dataset, season, stamp, f"nflverse:{dataset}", status, WRITER_VERSION, error, "{}"))


def collection_status(*, path=None, now=None):
    """Latest actual attempts plus successful receipt ages, without raw data loads."""
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        raise ValueError("Collection status requires timezone")
    if not Path(path or database_path()).exists():
        return []
    with connection(path) as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM collection_receipts WHERE id IN "
            "(SELECT MAX(id) FROM collection_receipts GROUP BY dataset,season) ORDER BY dataset,season")]
        successes = {(r["dataset"], r["season"]): dict(r) for r in db.execute("SELECT * FROM collection_receipts WHERE id IN "
            "(SELECT MAX(id) FROM collection_receipts WHERE status='success' GROUP BY dataset,season)")}
        for row in rows:
            row["metadata"] = json.loads(row["metadata"])
            success = successes.get((row["dataset"], row["season"]))
            row["last_successful_collection_at"] = success["collected_at"] if success else None
            row["age_seconds"] = (clock - datetime.fromisoformat(success["collected_at"])).total_seconds() if success else None
            from services.nfl_catalog import CATALOG, refresh_seconds
            current_season = clock.year - 1 if clock.month <= 2 else clock.year
            maximum = refresh_seconds(row["dataset"], row["season"], current_season) if row["dataset"] in CATALOG else 86400
            row["maximum_age_seconds"] = maximum
            row["freshness"] = ("unavailable" if row["age_seconds"] is None else
                "fresh" if row["status"] == "success" and 0 <= row["age_seconds"] <= maximum else "degraded")
            row["associated_games"] = db.execute("SELECT COUNT(*) FROM collection_games WHERE receipt_id=?", (row["id"],)).fetchone()[0]
    return rows


def refresh_status(*, path=None):
    if not Path(path or database_path()).exists():
        return []
    with connection(path) as db:
        return [dict(row) for row in db.execute("SELECT * FROM refresh_attempts WHERE id IN "
                                               "(SELECT MAX(id) FROM refresh_attempts GROUP BY dataset,season)")]


def versions(dataset, *, season=None, path=None):
    """Ordered observations for efficient offline chronological feature builds."""
    if not Path(path or database_path()).exists():
        return []
    with connection(path) as db:
        sql = "SELECT observed_at,payload FROM record_versions WHERE dataset=?"
        params = [dataset]
        if season is not None:
            sql += " AND season=?"
            params.append(season)
        return [{"observed_at": row[0], "payload": json.loads(row[1])} for row in db.execute(
            sql + " ORDER BY observed_at,record_key", params)]


def records(dataset, season=None, *, path=None):
    if not Path(path or database_path()).exists():
        return []
    with connection(path) as db:
        sql, params = "SELECT payload FROM records WHERE dataset=?", [dataset]
        if season is not None:
            sql += " AND season=?"
            params.append(season)
        return [json.loads(row[0]) for row in db.execute(sql, params)]


def metadata(*, path=None):
    if not Path(path or database_path()).exists():
        return []
    with connection(path) as db:
        result = []
        for row in db.execute("SELECT d.*, c.details FROM datasets d LEFT JOIN dataset_details c "
                              "ON d.dataset=c.dataset AND d.season=c.season ORDER BY d.dataset,d.season"):
            entry = dict(row)
            entry["coverage"] = json.loads(entry.pop("details") or "{}")
            result.append(entry)
        return result
