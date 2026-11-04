# User table: PickleType → JSON migration

## What changed

`User.saved_picks` and `User.history` were stored as SQLAlchemy `PickleType`
(binary pickle blobs).  They have been changed to `db.JSON` (plain JSON
strings).  The schema type in SQLite is the same (`BLOB`/`TEXT` affinity), so
**no `ALTER TABLE` is needed**, but existing rows written by the old code
contain raw pickle bytes that `db.JSON` cannot read.  This script converts
those bytes to JSON in-place.

---

## When to run

Run this script **immediately after** deploying the `db_models.py` change.

Do not run it before deploying.  `PickleType` reads back data with
`pickle.loads()`.  If the migration converts a row to a JSON string while the
old code is still running, that old code will try `pickle.loads('[…]')` and
fail.  Deploy first, migrate second — the exposure window is a few seconds.

**Deployment order:**

```
1. Back up the database              (see Rollback below)
2. Deploy the updated db_models.py
3. Run this script immediately       (< 30 seconds on any real dataset)
```

---

## Commands

### Dry run first (strongly recommended)

```bash
python3 migrate_pickle_to_json.py --dry-run
```

This prints every row that *would* be changed without touching the database.
Review the output, then run for real:

```bash
python3 migrate_pickle_to_json.py
```

### Verbose output (shows every skipped row too)

```bash
python3 migrate_pickle_to_json.py --verbose
```

### Override the database path

```bash
python3 migrate_pickle_to_json.py --db-path /var/data/scorpred/auth.db
```

Useful on Render where the persistent disk is mounted at a known path.

### Exit codes

| Code | Meaning |
|------|---------|
| `0`  | All rows migrated cleanly (or nothing needed migrating). |
| `1`  | One or more rows had unreadable pickle data and were reset to `[]`. The rows were still written; exit 1 is a warning so deployment scripts can flag it. |

---

## Verification

After running the migration, spot-check with these queries using the SQLite CLI
(`sqlite3 <path-to-auth.db>`):

```sql
-- Every value should start with '[' or '{', never with a raw binary character.
SELECT id, email,
       SUBSTR(saved_picks, 1, 80) AS saved_picks_preview,
       SUBSTR(history,     1, 80) AS history_preview
FROM user
LIMIT 20;

-- Count rows that are still NULL (should be 0 after migration).
SELECT COUNT(*) AS null_saved_picks FROM user WHERE saved_picks IS NULL;
SELECT COUNT(*) AS null_history     FROM user WHERE history     IS NULL;

-- Count rows that look like pickle (first byte is \x80, the pickle protocol marker).
-- Should be 0 after a successful migration.
SELECT COUNT(*) AS pickle_saved_picks
FROM user
WHERE typeof(saved_picks) = 'blob'
  AND HEX(SUBSTR(saved_picks, 1, 1)) = '80';

SELECT COUNT(*) AS pickle_history
FROM user
WHERE typeof(history) = 'blob'
  AND HEX(SUBSTR(history, 1, 1)) = '80';
```

All four queries should return 0 after a clean migration.

You can also verify through the app — log in as any existing user and check
that **Saved Picks** and **History** load without a 500 error.

---

## Rollback

### Before the migration has run

The database is unchanged.  Revert `db_models.py` to restore `PickleType`
columns and redeploy.

### After the migration has run

1. Restore from the backup you made before deploying:

```bash
# Local
cp auth.db.bak auth.db

# Render persistent disk
cp /var/data/scorpred/auth.db.bak /var/data/scorpred/auth.db
```

2. Redeploy the previous version of `db_models.py`.

### Taking the backup

```bash
# Resolve the path from Python if you are not sure where it is:
python3 -c "from runtime_paths import auth_db_path; print(auth_db_path())"

# Then copy it:
cp /path/to/auth.db /path/to/auth.db.bak
```

On Render, use the **Shell** tab in the dashboard to run the copy command
before triggering a deploy.

---

## Idempotency

The script is safe to run multiple times.  It classifies each value before
deciding whether to write:

| Raw value | Action |
|-----------|--------|
| `NULL` or empty | Written as `[]` |
| Valid JSON string / bytes | Skipped (no write) |
| Pickle bytes | Deserialized and written as JSON |
| Unreadable bytes | Written as `[]`, exit code 1 |

Running it a second time after a successful migration will skip every row and
exit 0 without touching the database.
