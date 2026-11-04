"""Sealed-outcome boundary for application readers, before JSON decoding.

Raw ingestion is trusted storage maintenance, never a research read interface.
Administrator-owned SQLite files cannot be protected from arbitrary external
sqlite3 clients by application code; those accesses require offline review.
"""
import hashlib
import json
import re
import sqlite3

from runtime_paths import cache_dir

SCHEDULE_CONTEXT = ("game_id", "season", "week", "game_type", "gameday", "gametime",
                    "home_team", "away_team", "location", "espn", "stadium_id", "stadium")


class HoldoutAccessDenied(ValueError):
    pass


class SealedCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        self.connection.check_statement(sql)
        return super().execute(sql, parameters)

    def executemany(self, sql, parameters):
        self.connection.check_statement(sql)
        return super().executemany(sql, parameters)

    def executescript(self, sql):
        self.connection.check_statement(sql)
        return super().executescript(sql)


class SealedConnection(sqlite3.Connection):
    _holdout_protected = False

    def check_statement(self, sql):
        # SQLite's authorizer reports a CTE name like a view name. Disallow
        # CTEs on sealed handles so a CTE cannot impersonate a trusted view.
        # Current application queries use SELECT subqueries, not CTEs.
        if self._holdout_protected and re.search(r"\bWITH\b", sql, re.IGNORECASE):
            raise sqlite3.DatabaseError("CTEs are not supported on sealed NFL query handles")

    def cursor(self, factory=SealedCursor):
        if self._holdout_protected and factory is not SealedCursor:
            raise sqlite3.DatabaseError("Custom cursors are unavailable on sealed NFL handles")
        return super().cursor(factory)

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, parameters):
        return self.cursor().executemany(sql, parameters)

    def executescript(self, sql):
        return self.cursor().executescript(sql)

    def set_authorizer(self, callback):
        if self._holdout_protected:
            raise sqlite3.DatabaseError("Sealed NFL authorizer cannot be replaced")
        return super().set_authorizer(callback)

    def backup(self, *args, **kwargs):
        if self._holdout_protected:
            raise sqlite3.DatabaseError("Raw backup is restricted to offline preservation maintenance")
        return super().backup(*args, **kwargs)

    def serialize(self, *args, **kwargs):
        if self._holdout_protected:
            raise sqlite3.DatabaseError("Raw serialization is unavailable on sealed NFL handles")
        return super().serialize(*args, **kwargs)


def protocol():
    path = cache_dir("nflverse", "phase2_2") / "protocol.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    unsigned = {k: v for k, v in value.items() if k != "protocol_sha256"}
    actual = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if (actual != value.get("protocol_sha256") or value.get("holdout", {}).get("sealed") is not True
            or not value["holdout"].get("game_ids") or not value["holdout"].get("earliest_review_at")):
        raise HoldoutAccessDenied("Sealed protocol is invalid; outcome access denied")
    return value


def reject(game_ids):
    plan = protocol()
    if plan and set(game_ids) & set(plan["holdout"]["game_ids"]):
        raise HoldoutAccessDenied("Reserved NFL outcomes are sealed; explicit authorized review on or after March 1, 2027 is required")


def review_connection(*_args, **_kwargs):
    # Deliberately no flag/environment/date-only bypass. Stage 1 does not
    # implement an unsealer. A separately reviewed offline workflow is required.
    raise HoldoutAccessDenied("Review remains blocked, including after March 1, 2027, until an explicit authorized offline review workflow is implemented")


def protect(db):
    """Temporary views and authorizer hide results even from generic SQL helpers.

    Reads of main.records/main.record_versions bypassing these views are denied.
    Schedule context is whitelisted; all other reserved game statistics and
    unbounded 2026 season aggregates are excluded. No persisted row is changed.
    """
    plan = protocol()
    if plan is None:
        if db.execute("SELECT 1 FROM nfl_forecast_studies LIMIT 1").fetchone():
            raise HoldoutAccessDenied("Registered study requires its sealed protocol")
        return
    registered = db.execute("SELECT json_extract(payload,'$.holdout_protocol_sha256') FROM nfl_forecast_studies").fetchall()
    if any(r[0] != plan["protocol_sha256"] for r in registered):
        raise HoldoutAccessDenied("Registered holdout protocol mismatch")
    ids = plan["holdout"]["game_ids"]
    if any(not isinstance(g, str) or not g for g in ids):
        raise HoldoutAccessDenied("Invalid reserved identifiers")
    db.execute("CREATE TEMP TABLE _sealed_ids(game_id TEXT PRIMARY KEY)")
    db.executemany("INSERT INTO _sealed_ids VALUES (?)", [(g,) for g in ids])
    season = int(plan["holdout"].get("season", 2026))
    weeks = plan["holdout"].get("weeks") or [12]
    first_week = min(int(w) for w in weeks)
    context = "json_object(" + ",".join("'" + f + "',json_extract(payload,'$." + f + "')" for f in SCHEDULE_CONTEXT) + ")"
    # Unknown future week-level/season-level outcomes are conservatively masked.
    held = ("(COALESCE(json_extract(payload,'$.game_id'),json_extract(payload,'$.nflverse_game_id'),record_key) "
            "IN (SELECT game_id FROM _sealed_ids) OR (season=" + str(season)
            + " AND CAST(COALESCE(json_extract(payload,'$.week'),0) AS INTEGER)>=" + str(first_week) + "))")
    unbounded = "(season=" + str(season) + " AND substr(dataset,-7)='_season')"
    for table, columns in (("records", "dataset,season,record_key,observed_at,game_id,team"),
                           ("record_versions", "rowid AS rowid,dataset,season,record_key,observed_at")):
        db.execute("CREATE TEMP VIEW " + table + " AS SELECT " + columns
                   + ",CASE WHEN dataset='schedules' AND " + held + " THEN " + context + " ELSE payload END AS payload"
                   + " FROM main." + table + " WHERE NOT " + unbounded
                   + " AND (dataset='schedules' OR NOT " + held + ")")

    def authorize(action, table, _column, database, source):
        if table in {"records", "record_versions"} and database == "main":
            if action == sqlite3.SQLITE_READ:
                return sqlite3.SQLITE_OK if source in {"records", "record_versions"} else sqlite3.SQLITE_DENY
            if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
                return sqlite3.SQLITE_DENY
        if action in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_DROP_TEMP_VIEW,
                      sqlite3.SQLITE_DROP_TEMP_TABLE, sqlite3.SQLITE_CREATE_TEMP_VIEW,
                      sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_ALTER_TABLE,
                      sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_TRIGGER}:
            return sqlite3.SQLITE_DENY
        if action in {sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_TEMP_TRIGGER} and table in {"records", "record_versions"}:
            return sqlite3.SQLITE_DENY
        if table == "_sealed_ids" and action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    # TEMP-table population must not leave a transaction open when callers
    # begin their own atomic read/archive transaction.
    db.commit()
    db.set_authorizer(authorize)
    db._holdout_protected = True
