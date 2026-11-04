# Local ScorPred app readiness — 2026-10-22

## Start the existing Windows installation

From `C:\Users\User\Documents\scorpred`:

```powershell
.\Start-ScorPred.ps1
```

Open **http://127.0.0.1:5173**. The frontend proxies Flask on `127.0.0.1:5001`.
Both listeners bind only to loopback. This is local development, with no deployment.
The existing Python environment and Node installation were sufficient; no additional
packages or historical dataset assets were downloaded. Existing live-provider
refreshes continued through the existing app pipeline.

Running the command again reuses the same servers. Use:

```powershell
.\Start-ScorPred.ps1 -Restart
.\Start-ScorPred.ps1 -Stop
```

The launcher checks dependencies, server identity, loaded backend source hashes,
database readiness, the frontend, and its actual API proxy. A launch mutex, an OS
backend lock, and strict ports prevent duplicate app servers. An unrelated owner
of either port produces an error and is left alone. Restart uses graceful backend
shutdown; a server still finishing work prevents a second backend from starting.
Logs are in `.tmp/local-app/`. The separate NFL collector is left running.
`NFL_ADVANCED_RESEARCH_ENABLED` and `NFL_WINNER_RESEARCH_ENABLED` are forced off.
The launcher also disables outbound email alerts for local use.

For a new environment, follow the dependency setup in the README. The launcher
finds Node on PATH or the existing local `.tmp/review/node-*-win-x64` installation.
It reports actionable missing-dependency errors. It does not install models or
train anything. If Windows blocks unsigned scripts, invoke this local file with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-ScorPred.ps1
```

## Changes

- Primary navigation is Dashboard, NFL, NBA, Soccer, Insights. Match analysis
  remains available through selected games and shareable URLs.
- Sport pages show the complete selected day's schedule first. Every published
  date is reachable, including dates beyond the former three-tab cutoff and
  selected dates reached through links.
- Refresh reloads active views from the server without a full page reload.
  Loading, source observation times, partial provider warnings, and unavailable
  estimates remain visible. Provider timestamps are never replaced by refresh time.
- NBA's insufficient-sample scoring response is an availability notice rather
  than a broken-feature error with an ineffective retry button. Unexpected
  failures retain actionable errors. Available outcome probabilities are displayed
  separately from rule confidence; NFL scenarios retain experimental labels.
- Independent dashboard requests still run concurrently. Shared requests avoid
  duplicate StrictMode/navigation fetches. A bounded 100-entry browser cache lasts
  at most 15 seconds, shortens to source expiration, excludes session responses,
  and clears on refresh. Errors are not cached; shared requests retain a timeout.
- Soccer computes cards for the selected day instead of its entire multi-week
  fixture batch. All schedule dates remain available. Background jobs retain
  their full-schedule behavior. Insights uses the same current day per sport;
  its initial response contains four current-slate games rather than 27 games
  spread across the longer Soccer schedule.
- Compiled Soccer cards have a 60-second cache keyed by actual fixture/source
  metadata. Every read still checks kickoff and source expiry. Identical decision
  publication avoids rewriting files or renewing the existing decision TTL.
- Sport and analysis pages load on demand. Initial JavaScript including its static
  imports fell from 291.07 KB to 222.41 KB; the entry chunk is 191.33 KB. This moves
  secondary UI into separate chunks rather than removing its features.
- Insights' internal calls no longer charge the public request multiple times.
  Its existing public limit remains 30 requests per minute; provider limits and
  authentication are unchanged.
- Registered background refresh jobs run once after startup. Health is populated
  from their real outcomes and source times, not a fabricated startup success.

## Measured performance

Measurements use the actual running app and existing provider caches, without
mocked data. They are observations, not a controlled provider-speed benchmark.
The baseline is the first captured parallel API batch. See the machine-readable
[performance evidence](assets/app_readiness/performance.json) for all rounds.

| API | First baseline | After changes, initial sample | After changes, median of three samples |
|---|---:|---:|---:|
| NFL dashboard | 125 ms | 139 ms | 263 ms |
| NBA dashboard | 1,934 ms | 2,592 ms | 255 ms |
| Soccer dashboard | 10,897 ms | 10,910 ms | 36 ms |
| Insights, all sports | 11,066 ms | 11,048 ms | 330 ms |

The later API samples benefit from warm caches. A material improvement in cold
provider latency was not established; this report makes no contrary claim.
Cold providers can still delay a complete dashboard: a subsequent real desktop
journey took 10.98 seconds to settle all sports, while other sports and navigation
remained usable. The mobile journey with warm sources settled in 483 ms. A cold
Soccer scoring calculation took 4.14 seconds; the following warm mobile selection
took 152 ms. Do not interpret cached medians as cold-start guarantees.

The real desktop return to Dashboard dropped from 910 ms to 170 ms, and mobile
from 906 ms to 71 ms. Other route timings, individual response statuses, and
viewport checks are recorded in the verification evidence. Browser measurements
include rendering; API measurements do not. This distinction is retained.

## Verification

The final automated results and exact commands are in
[verification.json](assets/app_readiness/verification.json). The backend suite
includes NFL, NBA, Soccer, authorization, database failure, cache/source expiry,
prediction lifecycle, frozen research safety, and offline soccer foundation checks.
New tests cover selected-day calculation, complete date metadata, compiled cache
expiry and mutation isolation, unchanged decision writes, and the original request
limit. Frontend tests cover request deduplication, cache expiration, refresh,
later dates, unavailable scoring, provider warnings, invalid IDs, and the existing
desktop/mobile presentation and prediction-expiry contracts.

Results: **819 backend tests passed**, **31 frontend tests passed**, frontend
ESLint and the TypeScript/Vite build passed, focused Python lint passed. A final
lint check also found an old duplicate fetch block after an unconditional return;
that unreachable block was removed without changing the executable function body.
The affected cache, dashboard, and lifecycle tests were rerun after that cleanup.

Unmocked Edge browser journeys at 1440×1000 and 390×844 completed:
Dashboard → NFL → selected game/analysis → Dashboard → NBA → selected game →
Soccer → selected fixture/scoring → Insights → refresh control → browser reload.
All 22 steps completed without a page exception, error alert, or horizontal
overflow. NBA showed the expected insufficient-sample notice. NFL scoring and
Soccer scoring used real existing inputs. The companion screenshots and request
logs are under `.tmp/app_ready/browser-complete/`.

For repeatable local checks with Node on PATH:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --tb=short
npm --prefix frontend run lint
npm --prefix frontend run build
$env:PLAYWRIGHT_CHANNEL = 'msedge'
npm --prefix frontend run test:e2e
Push-Location frontend
npm run verify:local
Pop-Location
.\.venv\Scripts\python.exe -m scripts.profile_local --output .tmp/api-timings.json
```

The automated frontend suite uses controlled test fixtures. The separate
`verify:local` journey uses the real backend and never mocks API responses.
Repeated manual profiling is subject to the unchanged local API rate limits.

## Preserved boundaries and remaining limitations

160 protected original-data, model, frozen-artifact, and disabled soccer research
files retained their exact hashes. Six fixed-input production outputs, from the
existing saved classifier and weighted rule engine, were unchanged. No training,
third-party dataset imports, restricted NBA provider activation, source-rights
research, historical repair, frozen artifact changes, or deployment occurred.
Sealed NFL outcome payloads were not parsed. The soccer sidecar and corrected
research contracts remain disabled and outside production serving/training.

The selected NBA games lack enough completed current-season games for supported
winner and scoring estimates. Their schedule and context work; probabilities are
left unavailable. Free provider coverage, network latency, missing optional API
credentials, and delayed source observations still affect individual matches.
Expired recommendations are withdrawn, and refreshing the view can return a
still-delayed source without inventing a new observation time. NFL estimates
remain descriptive, unvalidated scoring scenarios. This is a working local app,
not a production deployment or a claim of predictive reliability.
