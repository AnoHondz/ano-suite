#!/usr/bin/env python3
"""
migrate_pickle_to_json.py

One-time migration: convert User.saved_picks and User.history from
SQLAlchemy PickleType binary blobs to plain JSON strings.

Why raw sqlite3 instead of the ORM:
  db.JSON tries to json.loads() every value it reads.  Rows written by
  PickleType contain raw pickle bytes — not JSON — so any ORM access would
  raise a decode error before we could inspect or fix the data.
  Going through sqlite3 directly lets us read the raw bytes, decide what
  they are, and write back clean JSON without touching the ORM at all.

Safe to run multiple times (idempotent).  Rows that are already valid JSON
are detected and skipped without being rewritten.
"""
from __future__ import annotations

import argparse
import json
import logging
import pickle
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Only the path helper is needed — importing `app` would trigger API-key
# validation, heavyweight module imports, and db.create_all() which are
# irrelevant for a direct sqlite3 migration.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_paths import auth_db_path  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# Columns to migrate, in the order we process them.
_COLUMNS = ("saved_picks", "history")

# Hardcoded so the f-string UPDATE is safe (never user-supplied).
_ALLOWED_COLUMNS = frozenset(_COLUMNS)


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class _ColumnStats:
    converted: int = 0   # pickle bytes  → JSON string
    skipped: int = 0     # already valid JSON, untouched
    defaulted: int = 0   # NULL / empty / unrecognised  → "[]"
    failed: int = 0      # pickle.loads() raised an exception → "[]"


@dataclass
class MigrationResult:
    total_rows: int = 0
    dry_run: bool = False
    columns: dict[str, _ColumnStats] = field(default_factory=dict)

    @property
    def total_failed(self) -> int:
        return sum(s.failed for s in self.columns.values())

    @property
    def total_updates(self) -> int:
        return sum(
            s.converted + s.defaulted + s.failed
            for s in self.columns.values()
        )


# ---------------------------------------------------------------------------
# Value classification
# ---------------------------------------------------------------------------

def _classify(raw: Any) -> tuple[str, str, Exception | None]:
    """
    Classify and convert one raw column value.

    Returns
    -------
    (json_string, action, exc_or_none)

    action is one of:
      "skip"     already valid JSON, no write needed
      "convert"  was pickle bytes, now JSON
      "default"  was NULL / empty / unrecognised, defaulted to "[]"
      "fail"     pickle.loads() raised an exception, defaulted to "[]"
    """
    # ── NULL / empty ──────────────────────────────────────────────────────────
    if raw is None or raw == b"" or raw == "":
        return "[]", "default", None

    # ── Already a text string ─────────────────────────────────────────────────
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped and stripped[0] in ("{", "["):
            try:
                json.loads(stripped)          # must be valid JSON, not just any string
                return stripped, "skip", None
            except json.JSONDecodeError:
                pass
        # Some other string we don't understand.
        return "[]", "default", None

    # ── Bytes ─────────────────────────────────────────────────────────────────
    if isinstance(raw, (bytes, bytearray)):
        # Edge case: JSON was stored as raw bytes (e.g. a previous partial
        # migration wrote b'[...]' instead of a proper TEXT value).
        try:
            decoded = raw.decode("utf-8")
            stripped = decoded.strip()
            if stripped and stripped[0] in ("{", "["):
                json.loads(stripped)
                return stripped, "skip", None
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

        # Normal path: try to unpickle.
        try:
            value = pickle.loads(raw)
        except Exception as exc:
            return "[]", "fail", exc

        if value is None:
            return "[]", "default", None

        if isinstance(value, (list, dict)):
            return json.dumps(value, separators=(",", ":")), "convert", None

        # Unexpected type (int, str, set, …).  Preserve what we can by
        # wrapping in a list; warn so the operator can review.
        log.warning(
            "Pickled value has unexpected type %s; wrapping in a list.",
            type(value).__name__,
        )
        try:
            wrapped = json.dumps([value], separators=(",", ":"))
            return wrapped, "convert", None
        except (TypeError, ValueError):
            return "[]", "default", None

    # Anything else (shouldn't happen in SQLite, but be defensive).
    return "[]", "default", None


# ---------------------------------------------------------------------------
# Main migration logic
# ---------------------------------------------------------------------------

def run_migration(db_path: Path, *, dry_run: bool = False) -> MigrationResult:
    """
    Open *db_path* with a raw sqlite3 connection, inspect every User row,
    and rewrite any non-JSON column values as JSON.

    All writes happen inside a single transaction that is committed only when
    every row has been processed without an unexpected error.  If an
    unhandled exception escapes, the connection closes without committing
    (SQLite rolls back automatically).
    """
    if not db_path.exists():
        log.error("Database file not found: %s", db_path)
        sys.exit(1)

    result = MigrationResult(dry_run=dry_run)
    for col in _COLUMNS:
        result.columns[col] = _ColumnStats()

    mode = "[DRY RUN] " if dry_run else ""
    log.info("%sOpening database: %s", mode, db_path)

    # detect_types=0  →  sqlite3 hands us raw Python bytes for BLOB columns
    # and str for TEXT columns, which is exactly what _classify() needs.
    conn = sqlite3.connect(str(db_path), detect_types=0)
    conn.row_factory = sqlite3.Row

    try:
        # Verify the table exists before we do anything else.
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "user" not in tables:
            log.error(
                'Table "user" not found in %s.  '
                "Have you run db.create_all() at least once?",
                db_path,
            )
            conn.close()
            sys.exit(1)

        rows = conn.execute(
            "SELECT id, email, saved_picks, history FROM user"
        ).fetchall()

        result.total_rows = len(rows)
        log.info("Inspecting %d user row(s)...", result.total_rows)

        # Collect updates so we can apply them all in one transaction or
        # bail before writing anything if dry_run is set.
        # List of (column_name, json_value, user_id).
        pending: list[tuple[str, str, int]] = []

        for row in rows:
            user_id: int = row["id"]
            email: str = row["email"] or f"<user {user_id}>"

            for col in _COLUMNS:
                assert col in _ALLOWED_COLUMNS  # guard against any future mutation
                stats = result.columns[col]
                raw = row[col]
                json_val, action, exc = _classify(raw)

                if action == "skip":
                    stats.skipped += 1
                    log.debug(
                        "  user %d (%s) %-13s already JSON — skipping",
                        user_id, email, col,
                    )

                elif action == "convert":
                    stats.converted += 1
                    item_count = len(json.loads(json_val))
                    log.info(
                        "  user %d (%s) %-13s pickle → JSON  (%d item(s))",
                        user_id, email, col, item_count,
                    )
                    pending.append((col, json_val, user_id))

                elif action == "default":
                    stats.defaulted += 1
                    log.info(
                        "  user %d (%s) %-13s NULL/empty/unrecognised → []",
                        user_id, email, col,
                    )
                    pending.append((col, "[]", user_id))

                elif action == "fail":
                    stats.failed += 1
                    log.warning(
                        "  user %d (%s) %-13s pickle.loads() failed (%s) — defaulting to []",
                        user_id, email, col, exc,
                    )
                    pending.append((col, "[]", user_id))

        if not pending:
            log.info("Nothing to update — every column is already valid JSON.")
            return result

        log.info("%d write(s) queued.", len(pending))

        if dry_run:
            log.info("[DRY RUN] No changes written.")
            return result

        # ── Commit in one transaction ─────────────────────────────────────────
        # `with conn:` is a context manager that commits on clean exit and rolls
        # back if an exception propagates out.
        with conn:
            for col, json_val, user_id in pending:
                conn.execute(
                    f"UPDATE user SET {col} = ? WHERE id = ?",
                    (json_val, user_id),
                )

        log.info("Transaction committed.  %d row-column(s) updated.", len(pending))

    finally:
        conn.close()

    return result


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def _print_summary(result: MigrationResult) -> None:
    prefix = "[DRY RUN] " if result.dry_run else ""
    width = 34

    print()
    print(f"{prefix}Migration summary")
    print("-" * (len(prefix) + 18))
    print(f"  {'Rows inspected':<{width}} {result.total_rows}")

    for col, stats in result.columns.items():
        print()
        print(f"  Column: {col}")
        print(f"    {'Converted  (pickle → JSON)':<{width}} {stats.converted}")
        print(f"    {'Skipped    (already JSON)':<{width}} {stats.skipped}")
        print(f"    {'Defaulted  (NULL/empty → [])':<{width}} {stats.defaulted}")
        print(f"    {'Failed     (unreadable → [])':<{width}} {stats.failed}")

    print()
    if result.total_failed:
        print(
            f"  WARNING: {result.total_failed} row-column(s) had unreadable pickle data "
            "and were reset to []."
        )
        print("  See MIGRATION.md — Verification for the SQL query to review affected users.")
    elif result.dry_run:
        print("  Dry run complete. Re-run without --dry-run to apply changes.")
    else:
        print("  All rows migrated successfully.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate User.saved_picks and User.history "
            "from PickleType blobs to JSON strings."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing anything.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to the SQLite database file.  "
            "Defaults to the value returned by runtime_paths.auth_db_path()."
        ),
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show DEBUG-level messages (prints every skipped row).",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    db_path = args.db_path if args.db_path else auth_db_path()
    result = run_migration(db_path, dry_run=args.dry_run)
    _print_summary(result)

    # Exit 1 if any rows had unreadable pickle data so CI / deployment scripts
    # can treat those as warnings.  The rows themselves were safely reset to [].
    sys.exit(1 if result.total_failed else 0)


if __name__ == "__main__":
    main()
