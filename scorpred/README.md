# ScorPred

ScorPred is a soccer, NBA and NFL analysis app with a React dashboard, Flask API,
weighted rule predictions, and a separately evaluated soccer machine-learning pipeline.
It is an experimental analysis tool. Profitable betting performance has not been established.

## What the app currently does

- Soccer and NBA slates with BET, CONSIDER, and SKIP decisions.
- NFL schedules and experimental recent-scoring scenarios, with CONSIDER/SKIP decisions.
  See [NFL coverage and limitations](docs/NFL.md).
  [nflverse import, feature store and research-model setup](docs/NFLVERSE.md).
  Optional market context and recommended feeds: [NFL data connections](docs/NFL_DATA_APIS.md).
- Shareable match-analysis URLs, league/date filters, and browser back/refresh support.
- Research-informed scoring forecasts for soccer, NBA and NFL, with score ranges,
  chronological test results and research references. See [scoring methods and setup](docs/SCORING_MODELS.md).
- Owner-scoped saved picks, authenticated mutations, CSRF protection, and rate limits.
- Prediction tracking with locked, atomic writes and grading of recorded predictions.
- Explicit provider errors, request timeouts, and retry controls.

Soccer, NBA and NFL dashboard APIs use public provider data where available.
NBA team identities and schedules share ESPN IDs; unavailable estimates remain
visible as SKIP with no invented probabilities. `NBA_API_KEY` is optional for the
legacy standings integration, not required for public NBA schedules or form.

Dashboard recommendations currently use the weighted rule engine. The calibrated
Random Forest is available through the ML inference layer and legacy analysis paths;
its offline evaluation is not an accuracy claim for every dashboard recommendation.
Predictions are unavailable when sufficient source data cannot be obtained. The app
does not create predictions from team-name hashes or reconstruct picks after results.

## Current offline model evaluation

The production artifact is a 60-feature Random Forest with sigmoid calibration.
Training, calibration, and evaluation use separate chronological windows; dates are
kept together, and preprocessing is fitted on the training window only.

The bundled historical dataset ends on **2026-04-19**. The evaluation generated on
2026-10-16 reports:

| Measurement | Value |
|---|---:|
| Training matches | 4,540 |
| Calibration matches | 1,528 |
| Test matches | 1,518 |
| Test accuracy | 52.04% |
| Training-majority baseline accuracy on test matches | 43.68% |
| Brier score, averaged across three classes | 0.1995 |
| Expected calibration error | 0.0216 |

See [the machine-readable evaluation](data/validation/production_model.json).
These are historical holdout results, not live returns or current-season validation.
The calibrated model selected no draws as its top class in this test window; draw
selection remains a limitation.
Older walk-forward reports describe experimental models and different feature schemas.

### Compared with bookmaker odds

`python scripts/evaluate_against_market.py` matches every test-window match to
football-data.co.uk closing and pre-weekend prices (top five leagues, 1,518 of 1,518
matched, no result disagreements) and writes
[market_evaluation.json](data/validation/market_evaluation.json):

| Measurement | Model | Bookmakers |
|---|---:|---:|
| Winners picked | 52.0% | 54.2% |
| Log loss (lower is better) | 1.003 | 0.973 |

The model's probabilities are worse than the market's in every league (log-loss gap
0.029, 95% interval 0.021–0.037). Flat stakes on every model pick at pre-weekend
prices returned −3.9% (95% interval −9.2% to +1.4%); bets where the model saw a 5%+
edge over the odds returned −17.3% (−25.1% to −8.9%). No betting edge is established.

The Insights **Track record** view shows these results with the verified record of
published rule-engine picks. Only picks first recorded before a known kickoff count.
Picks are frozen at kickoff and later records are reported as exclusions.

## Run locally

For the existing Windows installation, start both services with:

```powershell
.\Start-ScorPred.ps1
```

Open **http://127.0.0.1:5173**. Running the launcher again reuses both services.
Use `-Restart` to reload changed backend code or `-Stop` to stop the app.
The independent NFL collector is left running. Research models stay disabled.
See [local startup and verification](docs/LOCAL_APP_READINESS.md) for checks,
measurements, logs, and troubleshooting.

For a fresh environment, install the dependencies first:

Use Python 3.11 or 3.12 and Node 22. Create a virtual environment and install the
pinned model dependency version:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python scripts/model_artifacts.py
npm --prefix frontend ci
npm --prefix frontend run build
.\.venv\Scripts\python app.py
```

Open http://127.0.0.1:5000. On macOS/Linux, use `.venv/bin/python` instead of the
Windows interpreter path. Generate SECRET_KEY with Python's `secrets.token_hex(32)`.
Provider keys enable their associated integrations; public fallbacks can have gaps.

For frontend development, run Flask with PORT=5001, then `npm --prefix frontend run dev`.
Vite serves http://127.0.0.1:5173 and proxies API and authentication requests to Flask.

## Routes

| Route | Purpose |
|---|---|
| `/`, `/soccer`, `/nba`, `/nfl`, `/insights` | React dashboards |
| `/match-analysis?match_id=...&sport=soccer&league_id=39` | Reloadable match analysis |
| `/login`, `/signup` | Session authentication |
| `/my-bets` | Signed-in user's saved picks |
| `/api/session` | Current user and CSRF token |
| `/api/bets` | Authenticated GET/POST/DELETE for the current user's picks |
| `/api/bets/<id>` | Authenticated DELETE, scoped to the current user |
| `/api/matches/<id>` | Match decision; refreshes from the current slate when necessary |
| `/api/dashboard/soccer`, `/api/dashboard/nba`, `/api/dashboard/nfl`, `/api/dashboard/insights` | Dashboard data |
| `/api/nfl/matches/<id>/analysis?date=YYYY-MM-DD` | Event-bound NFL scoring scenarios |
| `/health` | Database readiness; returns 503 when unavailable |
| `/live` | Process liveness |
| `/api/health` | Background-pipeline freshness and failure status |

Mutation requests require `X-CSRF-Token` from `/api/session`. The read-only scoreline
calculations also accept GET. Former Strategy Lab/backtesting/comparison pages are
retired and redirect to Insights; they are not part of the current React navigation.

## Verify changes

See [pregame validity and freshness policies](docs/PREDICTION_LIFECYCLE.md) for
kickoff expiry, source-age limits, shared training/live features and ET calendars.

```text
python -m pytest tests -q
npm --prefix frontend run lint
npm --prefix frontend run build
```

For browser regressions, install Chromium with `npx playwright install chromium`
from `frontend`, then run `npm --prefix frontend run test:e2e`. The tests start Vite
and use controlled API responses. Set PLAYWRIGHT_CHANNEL=chrome to use installed Chrome.
Backend tests isolate their database and history, block live requests, and disable jobs.

## Train and publish models

```text
python production_model.py
python scripts/model_artifacts.py --create
python scripts/model_artifacts.py
```

Inference follows the artifact's declared feature order, including compatible older
schemas. It rejects invalid metadata rather than silently using another feature count.
The default serving artifact is the calibrated Random Forest; experimental ensembles
must be selected explicitly. scikit-learn is pinned to the artifact's training version.

The weekly model-release workflow trains and tests the production model, publishes an
immutable GitHub release, then commits a checksum-pinned manifest and its matching
evaluation. Builds verify or download that exact artifact. Publishing failure stops the
workflow; it does not update the release pointer. Updating workflow files locally does
not publish a release or deploy the app.

## Storage and deployment

The supplied Render Blueprint configures a paid single-instance service with a 1 GB
persistent disk. Runtime state lives under `/var/data/scorpred`; model releases live
with the application build so a persistent cache cannot pin an old model after deploy.
This configuration has been schema-validated but has not been deployed here.

Production requires a stable SECRET_KEY and durable auth storage. DATABASE_URL can
select PostgreSQL; the included deployment uses SQLite on the persistent disk.
Prediction history remains a single-disk JSON store with process/thread locking and
atomic replacement. Keep one Gunicorn worker and one service instance with this
configuration. Redis can share cache/rate-limit state, but does not make JSON history
safe for multiple service instances.

Provider response caches (`football`, `nba`, `nba_public`, `props`, `odds`, `service`)
are pruned every six hours, and on demand with `flask --app app prune-cache`. Files
older than `SCORPRED_CACHE_MAX_AGE_DAYS` (7) go first, then the oldest files until the
total fits under `SCORPRED_CACHE_MAX_MB` (300). Archives, the NFL feature store and
prediction history are never pruned.

Read [migration and operational notes](docs/HARDENING_MIGRATION.md) before deployment.
Older audit and implementation reports in `docs/` are historical snapshots, not the
current feature or deployment specification.
