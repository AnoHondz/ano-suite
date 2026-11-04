# Reliability and ownership migration

This change is prepared locally. No Render service, database, GitHub release, or remote branch has been changed.

## Before deploying

1. Back up existing accounts and prediction history. The previous free/ephemeral configuration cannot recover state already lost on a restart.
2. Review `render.yaml`: it now selects a paid Starter service and a 1 GB disk at `/var/data`. Persistent disks support one service instance and prevent zero-downtime instance swaps. See [Render's disk documentation](https://render.com/docs/disks).
3. Copy any existing durable `auth.db` and `prediction_tracking.json` into the configured runtime locations before accepting traffic. Do not copy a live SQLite database with ordinary file-copy tools; use SQLite's backup API or stop writes first.
4. Set a generated, stable `SECRET_KEY` of at least 32 characters. Optional `DATABASE_URL` selects PostgreSQL using psycopg 3. `SCORPRED_PERSISTENT_ROOT` holds SQLite accounts; `SCORPRED_DATA_ROOT` holds tracking and caches.
5. If administration is needed, independently verify an existing account, then run `flask --app app grant-admin verified@example.com` on the server. Signup never grants administrator access, and claiming an email address does not confer a role.
6. Build, verify the model manifest, run tests, then deploy through the existing review process.

## Existing bets

Startup adds a nullable `bet.user_id` column to old databases without deleting rows.
Legacy shared rows remain unowned and invisible to normal user queries. They cannot
be safely attributed from the old schema, so this migration never assigns them to
the first person who signs in. Any reassignment requires an independently verified
owner and an explicit data migration.

New records always receive their owner from the authenticated session. Supplying
`user_id` in a request does not change ownership. Listing, clearing, and deleting are
owner-scoped. The duplicate-submission lease is owner-scoped and expires after 30 seconds.

## Background services and health

Importing `app` does not start jobs. Gunicorn's `post_worker_init` hook and the local
`python app.py` entry point explicitly start the scheduler; shutdown stops it. Keep
`workers=1` while scheduling inside the web worker. Set `SCORPRED_SCHEDULER_ENABLED=0`
for commands and tests that should not schedule work.

The prediction job receives its brain instance directly. Empty/failed refreshes do
not count as provider successes. `/health` checks database readiness without making
provider calls and returns 503 for a database failure. `/live` checks only process
liveness. `/api/health` reports pipeline freshness; a healthy database does not imply
that every upstream provider is healthy.

## History, caches, and backups

Tracking updates acquire a file lock across the whole read/modify/write transaction.
Files are flushed and atomically replaced. Corrupt or unreadable history raises an
error instead of becoming an empty list that can overwrite the original file.
This supports processes sharing the same disk, not independent service instances.

Cache entries carry their original creation and expiry times across memory, Redis,
and disk. Restoring an entry never renews its lifetime. The cache namespace changes
to v4, so older incompatible cache payloads are ignored. Cached fixtures rebuild
the analysis index before rendering cards.

Use database-consistent backups and retain copies outside the service. The disk
configuration provides persistence, not a complete disaster-recovery policy. Test
restoration before relying on production history.

## Model releases and evaluation limits

`production_model.py` builds a named 60-feature pipeline, fits preprocessing only on
the training window, freezes the fitted estimator for calibration, and evaluates a
later chronological holdout. The matching JSON report records dates, baseline,
accuracy, Brier score, calibration error, and class counts. It explicitly leaves
profitability unevaluated.

The weekly workflow publishes the binary first, then commits a manifest containing
its exact URL, SHA-256 checksum, schema, and scikit-learn version. Builds fail if the
requested artifact cannot be fetched or verified. A failed release does not silently
fall back to a different model. No release has been published during this local change.

The bundled data ends in April 2026. Validation does not establish current-season
accuracy or profitable betting. The calibrated model made no top-class draw picks
in this holdout. React dashboard decisions still identify their source as weighted
rules, so the standalone ML evaluation must not be presented as dashboard accuracy.

## Interface and API compatibility

Navigation uses URLs, including match ID, sport, league, and date where applicable.
Reloading an analysis page loads its decision from the API; if it has left the
available slate, the page explains that rather than recreating a prediction.

SPA interception applies only to known GET/HEAD page routes. API calls and legacy
POST mutations always reach backend authorization. Legacy bet mutation URLs remain
aliases for authenticated operations. `GET /my-bets` now shows personal saved picks,
not a shared performance ledger. Old hash-generated prediction/result fallbacks are
removed. An unavailable provider must not be interpreted as a winning or empty slate.
