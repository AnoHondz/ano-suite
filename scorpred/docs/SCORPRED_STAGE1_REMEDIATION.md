# ScorPred Stage 1 remediation

Date: October 21, 2026 (America/New_York). Technical receipt times below are UTC and are explicitly labeled.

Stage 1 source repairs and regression verification are implemented. **Operational sign-off is partial:** the identified NFL collector is running guarded source, but its latest completed cycle is degraded after a schedule ConnectionError; PBP collection succeeded in that cycle. The running NBA and soccer schedulers have successful receipts, while the latest app health reports stale ML predictions. Fresh free-provider adapter receipts are recorded separately from scheduler health. No deployment, real-model training, model replacement, or sealed-outcome evaluation occurred. Graphite & Sage source/layouts were preserved.

## Scope and evidence

Implementation follows the seven diagnostic reports and their issue register. New measurements are in [the Stage 1 evidence directory](../cache/diagnostics/stage1_2026-10-21/); original audit outputs were retained. Source repairs reject unavailable/malformed inputs and unsupported markets. Existing valid-input winner algorithms, model coefficients, calibration, policy weights and forecast identities were not changed.

The frozen-source repair is an explicit compatibility transition, not a regenerated model/study. [The safety revision](assets/nfl_stage1/safety_revision.json) binds the original study SHA, original source fingerprint, exactly five permitted source-file changes, and the hashes of two new safety modules. Startup does not generate or update this manifest. Unknown source drift still fails closed. The original bundled source and study remain intact for independent replay.

## NFL-001 — Holdout access boundary

**Root cause:** reserved-result exclusion was implemented on registered forecast/evaluation paths but not at the generic database-reader boundary. Generic snapshots could later consume held results. Legacy feature/training paths shared these unguarded readers.

**Files changed:** `services/nfl_holdout.py`, `services/nfl_safety_revision.py`, `services/nflverse_store.py`, `services/nfl_frozen_models.py`, `services/nfl_winner_features.py`, `services/nfl_winner_research.py`, `services/nfl_features.py`, `scripts/register_nfl_safety_revision.py`, and `docs/assets/nfl_stage1/` manifests.

**Repair:** ordinary store connections install temporary safe views before returning any handle. Reserved schedule payloads become a whitelist of fixture identity/context; scores, result, odds and other nonwhitelisted fields are never decoded by the reader. Reserved non-schedule statistics and unknown future week-level statistics are excluded. Unbounded 2026 `*_season` aggregates, including PFR aggregates, are also excluded. The original persisted records are unchanged. A SQLite authorizer denies direct `main.records` /`main.record_versions` reads and tested view/table/attach bypasses. Protected connections/cursors also reject CTE view-name impersonation, guard replacement, raw backup/serialization and schema manipulation; current application queries use SELECT subqueries rather than CTEs. Historical as-of and version readers use the same protected views. Generic full-feature builders reject reserved targets, snapshot collection skips them, and the legacy feature builder omits them. Frozen three-input forecasts retain their existing dedicated context projection.

**Access-path disposition:**

| Path | Disposition |
|---|---|
| `store.records`, `versions`, `records_as_of`, generic `connection` SQL | Guarded before JSON decoding, including qualified-main bypass denial |
| `FeatureBuilder` /`Vintages` /automatic full-feature snapshots | Guarded histories; reserved targets rejected/excluded |
| Historical reconstruction /Elo history /research evaluation | Protected store inputs plus existing reserved-ID fitting/evaluation checks |
| Legacy `nfl_model.train` /NFL scoring trainer | Protected legacy feature input and reserved-target exclusion; not executed on real data |
| Descriptive analytics /production NFL data readers | Protected store inputs; held results/statistics cannot escape through them |
| Registered frozen forecaster and research APIs | Existing SQL projections, disabled flags, authorization and locked evaluation retained; source-only compatibility explicitly verified |
| Audit/report utilities | Existing SQL-redacted projections or metadata-only reads; no held values exported |
| Collector ingestion, SQLite backup and aggregate preservation comparison | Trusted maintenance only; not an application/research query interface |
| Arbitrary external SQLite clients, copied raw archives, direct private ingestion handles | **Blocked from approved research workflow**, not an OS security boundary; application code cannot protect an administrator-owned file from arbitrary local programs |
| Final holdout review/unsealing | **Blocked**. No flag, HTTP method, date-only transition or environment switch unseals outcomes |

**Before/after:** the original reader returned full payloads. Synthetic held-game scores, result sentinels and advanced stats are now absent before decoding; attempts to bypass views raise SQLite access errors. The production protocol still reserves **109 games**, SHA `a4b906096d31dccbeceabdf4ffa1a275dccdd6a08206b2b636bac8026f202281`. The original study SHA remains `fd4526c5da021c30c28538dcdb67faadc18f581d7ac451a2d56f1f6589534cb7`. Both archived probabilities independently replay with **zero error**.

**Tests:** synthetic current/version/as-of reads, PBP exclusion, raw SQL/DDL/attach/export and CTE impersonation denial, missing-protocol fail-closed behavior, repeated ingestion, old frozen adversaries and independent export replay. All 36 new repair tests and the full suite pass.

**Remaining limits /status:** **Resolved for application paths; administrative/offline review paths explicitly blocked.** Stage 1 intentionally does not implement an unsealer, including after March 1, 2027. A separately authorized offline review on or after that date is required. Keep research `enabled=false` and `production_active=false`. No real held outcome was inspected or evaluated.

## OPS-001 — Collection and refresh reliability

**Root cause:** earlier NFL attempts recorded ConnectionError, with GitHub DNS-resolution failures in existing logs. A permanent provider outage or application parsing defect was not established. A separate collector timing defect added the entire four-minute wait after work, extending schedule gaps during slow snapshots. The original stall in the running app's refresh jobs has not been causally identified; NBA and soccer subsequently completed real successful refreshes. At the final check ML predictions had become stale. A new schedule ConnectionError also remains unresolved; a successful contemporaneous HEAD probe does not identify its cause or prove an import recovered.

**Files changed:** `scripts/nfl_collector.py`, `scripts/stage1_status.py`, `scripts/stage1_preservation.py`, `scripts/stage1_report.py`, safety boundary files above, and regression tests. The next collector deadline is now anchored to cycle start, so slow work does not add another full wait. Existing per-feed retry/backoff, transactional importer and singleton ownership were retained.

**Repair /recovery:** verified a genuine successful PBP receipt after the earlier failures; gracefully reloaded only the verified `ScorPred NFL Collector` task to load guarded source. The final reload has collector PID **18664**, launcher **17072**, source SHA `1aba32090a778eaadb6ce2f55f493473df17643db4953fbc10cabc584fb2cc24`, with IgnoreNew task policy. The snapshot and frozen forecast stages completed successfully. A second launch was actually refused by the database-owner lock. Research remains disabled; no unrelated process was stopped.

**Before/after evidence:**

| Measurement | Verified result |
|---|---|
| Earlier NFL PBP state | Failed ConnectionError and backoff; failed receipt retained |
| Latest inspected PBP success | **2026-10-21 18:11:30.898197 UTC**, receipt175,16,380 rows |
| Latest guarded collector schedule success | **2026-10-21 16:31:08.538231 UTC**, receipt173 |
| Latest schedule attempt | **18:11:22 UTC**, receipt174,ConnectionError; original successful receipt retained |
| First guarded snapshot /forecast cycle after final reload | Completed **15:06:44.081242 UTC**, no errors,28 snapshots,no forecasts due or missed |
| Latest completed guarded cycle | Completed **18:11:52.272518 UTC**, degraded due to schedule failure; PBP succeeded,28 snapshots,0 forecasts due/missed |
| Worker liveness | **18:13 UTC**, PID18664 and launcher17072 alive; task Running/IgnoreNew; collector waiting with current heartbeat |
| Public NFL schedule/PBP metadata | HTTP200; HEAD only, no NFL outcomes downloaded |
| Public NBA/soccer and NBA overview probes | HTTP200; availability does not prove complete stat/season coverage |
| One-shot existing NBA adapter refresh | 13:17:33 UTC,8 requested days,0 failed,0 stale,30 upcoming fixtures,0 prediction calls |
| Running app NBA scheduler recovery | Actual successful completion **15:02:27 UTC**, subsequent successful source receipt **16:17:00 UTC** |
| Free soccer source adapter refresh | All3 supported leagues returned fresh source receipts at **15:04:21–22 UTC**, no prediction calls; this is not scheduler health |
| Running app soccer scheduler recovery | Actual successful source receipt **16:24:17 UTC**; status ok at final check |
| Existing app `/api/health`,18:13 UTC | **503/degraded**; NBA/soccer status ok; ML predictions stale, last success **15:09:22 UTC**, beyond its3h monitor threshold |

The short source-fingerprint mismatch during the controlled collector transition was fail-closed, not a frozen-artifact alteration; the reloaded owner passed the explicit safety-revision checks. There is one owner plus its Windows launcher, not two writers. There is no claim that the running application's NBA scheduler recovered merely because direct provider calls or the one-shot adapter succeeded.

**Tests:** existing importer transaction/failed-source retention, empty/partial feed rejection, synthetic retry backoff and unchanged successful metadata, slow239-second cycle followed by another start within245seconds, collector lock/graceful-stop tests, actual duplicate-launch refusal, actual successful receipts and post-reload cycle.

**Remaining limits /status:** **Partially resolved.** Safe collection, retry behavior and NBA/soccer scheduled recovery are verified. The collector is alive, but its latest schedule import failed and its most recent successful schedule receipt is about102minutes old at the cited check. The running application's ML predictions are stale. Continuous cadence and sustained healthy service are not established by these point-in-time checks. The app was not restarted or redeployed because that would also activate unrelated pre-existing workspace changes and potentially alter loaded production behavior. Its loaded version/job ownership needs a controlled application recovery plan, including proof that the new reader contracts are loaded before the first reserved NFL kickoff. Auxiliary feeds still require separate ownership/coverage work (NFL-002); this task did not silently label them current.

## OPS-002 — Freshness and probability input contracts

**Root cause:** `unavailable_reason` only checked truthy metadata, accepting None/empty dictionaries. Canonical unavailable placeholders and a missing-vector path could supply apparent probabilities without valid inputs.

**Files changed:** `services/prediction_lifecycle.py`, `services/input_contracts.py`, `services/match_brain.py`; successful-provider test fixtures now use `tests/source_contracts.py`.

**Repair:** all recommendations require explicit valid fresh source metadata; missing, malformed, stale or postkickoff inputs fail closed. The soccer canonical boundary requires a finite, nonnegative, complete three-way vector with declared fraction/percentage normalization. Missing vectors become unavailable, without a neutral replacement probability or model confidence. Valid vectors retain the existing decision algorithm. Test fixtures claiming provider success now supply explicit synthetic receipts; rejection tests retain missing receipts.

**Before/after /tests:** absent metadata previously passed; it now produces SKIP and no displayed probability. Synthetic future/fresh inputs still work, while expired, incomplete and postkickoff contexts are rejected. Current-code Flask API/template regression tests and browser expiry tests pass.

**Remaining limits /status:** **Resolved in current code and isolated runtime tests.** Ordinary NBA/soccer caches still lack a durable historical receipt archive (VAL-001). No claim is made that an old running app process has loaded this source without a rollout.

## SOC-003 — Five/ten-match and target-date parity

**Root cause:** the original audited route truncated history before ten-match ML feature construction and used wall-clock rest. Outside-audit changes had already separated ML history and passed the target date, but route execution had not been verified.

**Files changed:** new route tests in `tests/test_stage1_repairs.py`; existing `app.py` and `scormastermind.py` corrections were preserved, not rewritten.

**Repair /before-after:** verified actual Flask execution of **both `/prediction` and `/matchup`**: five rows feed rules/UI, ten feed ML, and the selected fixture date is passed. With the synthetic split history, ten-match goals average is1 and the five-vs-ten trend is1; existing direct feature tests confirm target-date rest. The original audit's39/1,518 sensitivity changes are evidence of the old mismatch, not a new production accuracy result.

**Tests /status:** **Resolved at current-source runtime boundary.** Full existing feature-parity and ScorMastermind suites pass. Historical league lineage and current blended-model performance remain unverified; no new model/probability-performance claim is made.

## SOC-002 — Statistic meanings, units and missingness

**Root cause:** blocks were aliased as clearances, all duels as aerial wins, total passes as completed passes, and key passes as broader chances created. Missing composite components could become zero; a per-90 rate could fall back to a per-game rate.

**Files changed:** `props_engine.py`, repair tests.

**Repair:** retain market identities, but mark those unsupported definitions unavailable in both fixture and season mappings. The generator produces no estimate for these quantities or opponent-box touches. Supported counts require finite, nonnegative values; legitimate zero remains zero. Goal+assist and NBA PRA require all components. Per-90 rates require measured minutes and do not silently change units. Corrected sample-cache keys use a new namespace; original cache files were retained.

**Before/after /tests:** a source `tackles.blocks=7` no longer becomes seven clearances; all four ambiguous mappings return None. Missing/negative/nonfinite/boolean values are rejected, and measured zero/composite zero remains valid. Supported markets remain on the existing calculations. Provider player-stat entitlement/coverage is not inferred from code or a successful scoreboard response. Source context: [API-Football documentation](https://www.api-football.com/documentation-v3); no new subscription or credential was used.

**Remaining limits /status:** **Resolved mapping/missingness contracts; source coverage remains limited.** Broader market availability requires verified original definitions, not approximations or additional models.

## NBA-001 — Real-player coverage, season and overview freshness

**Root cause:** numeric-ID props called a legacy adapter that has no demonstrated real player logs; the route explicitly defaulted to2024. Overview cache fallback had no age ceiling, and descriptive averages were reused as recent/opponent inputs and unvalidated prop confidence.

**Files changed:** `nba_routes.py`, `props_engine.py`, repair tests.

**Repair:** integer identifier/type and season validation now returns400 for malformed inputs; default is current NBA ending year. Missing real log coverage yields an explicit unavailable coverage result and no prop cards. No mock replacement was added. The separate overview path retains its source observation time, rejects expired fallback and keeps missing stats null. Three-pointers made are no longer inferred from points or filled as zero. Season summaries no longer manufacture recent/opponent averages or prop confidence. Existing player/prop response keys remain present; unknown values are null/empty with reasons.

**Before/after /tests:** empty real-ID logs now visibly mean missing coverage, not a successful configured model. A public overview request returned200 with season-average names and one recent-statistics group; this proves endpoint availability, **not** season-aligned prop coverage or historical pregame evidence. Expired files remain untouched but are not served as fresh. Known average points remain descriptive while missing assists/3PM remain null. Flask props requests verify current season,400 on malformed identifiers,and preserved200 response shape for unavailable coverage.

**Remaining limits /status:** **Partially resolved.** Unsafe outputs/defaults and stale semantics are repaired; a verified real-player game-log adapter with complete ID/season/source contracts is still required before enabling those markets. NBA winner rules and model families were not redeveloped.

## SOC-001 — Legacy duplicated/mislabeled source

**Root cause:** `E0_2018.csv` actually contains319 rows from2025–26 and is byte-identical to the2025 file. The old preparation script concatenated files without season/identity checks and wrote at import time.

**Files changed:** `prepare_dataset.py`; new `data/processed/matches_stage1.csv` and `.sources.json`; regression test.

**Repair:** preparation now has an explicit main entry, validates required fields, dates/seasons, integer scores/results and fixture identities, quarantines wrong-season files in its manifest, and writes only a new version. It refuses existing output paths. The legitimate2019 COVID season extension is handled explicitly. No source download or model training occurred.

**Before/after /tests:** original legacy output2,918 rows/2,599 unique; new version **2,599 rows, zero duplicates**, excluding the319 mislabeled rows. Synthetic tests verify quarantine, unchanged originals and refusal to overwrite. All12 original audited soccer CSV hashes remain unchanged.

**Remaining limits /status:** **Resolved for the preparation path; original legacy files intentionally retained.** The old erroneous processed file is not silently replaced or promoted into any trained artifact. Recovery of genuine2018 data, per-league lineage (SOC-004) and versioned consumer migration belong to the next data-coverage phase.

## Preservation and regressions

The preservation CLI created an opaque SQLite backup, never decoded held outcomes, and compared original table columns against the live database using aggregate equality inside SQLite. Additions from legitimate collection are allowed; missing/modified original records are not. The local3.10GB snapshot is retained in the ignored evidence directory for verification/rollback; it is not a published artifact.

The [artifact preservation manifest](assets/nfl_stage1/preservation_artifacts.json) records exported frozen identities. The local [baseline hashes](../cache/diagnostics/stage1_2026-10-21/preservation_before.json), [aggregate preservation results](../cache/diagnostics/stage1_2026-10-21/preservation_after.json), and [evidence manifest](../cache/diagnostics/stage1_2026-10-21/evidence_manifest.json) bind the original observations, model files, study, forecasts and holdout metadata to this verification run.

| Preserved item | Evidence |
|---|---|
| Original observation versions | **1,531,675 checked; zero missing/changed**, including timestamps |
| Original frozen models/study/forecasts | 2models,1study,2forecasts; zero changed; independent probability error0 |
| Original inputs,batches,events,receipts | Aggregate full-column equality checks in `preservation_after.json` |
| Frozen exported artifacts /holdout metadata | `preservation_before.json` hashes and original study/protocol identities; no replacements |
| Original soccer CSVs | All12 match the original audit hashes |
| Current-source tests | **729 backend tests passed**, including36 new repair tests and NFL/NBA/soccer regressions |
| Frontend | TypeScript/Vite build passed,1,746 modules; **25 browser tests passed** |
| New repair modules | Ruff checks passed; unrelated existing lint debt was not swept into this repair |

The final backend evidence is `backend_tests.txt`; focused adversaries and test inventory are saved alongside it. Earlier failed runs exposed contract-less success fixtures and incomplete new synthetic route fixtures; these were corrected rather than reopening the missing-freshness compatibility path. Browser tests use mocked sports APIs; live-provider evidence is recorded separately. Test durations are not provider-uptime or prediction-quality measurements.

## Reproduction and recovery

Run from the repository in PowerShell. Never overwrite an existing preservation baseline or regenerate the safety revision merely to accept an unexplained source change.

```powershell
# Read-only receipt/provider/current-worker evidence; no NFL outcome downloads.
& .\.venv\Scripts\python.exe -m scripts.stage1_status

# Verify all original rows against the retained snapshot; aggregate output only.
& .\.venv\Scripts\python.exe -m scripts.stage1_preservation after
& .\.venv\Scripts\python.exe -m scripts.stage1_report
& .\.venv\Scripts\python.exe scripts/verify_nfl_forecast_exports.py --study-sha256 fd4526c5da021c30c28538dcdb67faadc18f581d7ac451a2d56f1f6589534cb7

# Initial baseline command, for a NEW remediation run only; refuses overwrite.
# python -m scripts.stage1_preservation before

& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m pytest tests/test_stage1_repairs.py -q
& .\.venv\Scripts\python.exe -m ruff check services/nfl_holdout.py services/nfl_safety_revision.py services/input_contracts.py scripts/register_nfl_safety_revision.py scripts/stage1_preservation.py scripts/stage1_status.py scripts/stage1_report.py prepare_dataset.py tests/test_stage1_repairs.py tests/source_contracts.py

$stageNode = Join-Path (Get-Location) '.tmp\review\node-v22.23.3-win-x64'
$env:Path = $stageNode + ';' + $env:Path
& "$stageNode\npm.cmd" --prefix frontend run build
$env:PLAYWRIGHT_CHANNEL = 'msedge'
& "$stageNode\npm.cmd" --prefix frontend run test:e2e

# Source preparation needs a NEW path; originals and existing derived files survive.
& .\.venv\Scripts\python.exe prepare_dataset.py --output data/processed/matches_stage1_review2.csv

# Read-only ownership/status; do not start an independent collection loop.
& .\scripts\manage_nfl_collector.ps1 -Action Status
```

If NFL collection fails again, inspect the actual attempted/successful receipts and `collector/status.json`; an old success or HTTP200 HEAD is not proof of a completed import. Existing failures back off for up to an hour, and successful data remains available with its original timestamps. Do not override DNS/proxy settings or use `--force` to make a report appear healthy. If a controlled source-only collector reload is needed, first verify task ownership and safety hashes; `manage_nfl_collector.ps1 -Action Reload` requests a graceful stop and reuses the same singleton task. It does not terminate unrelated processes.

## Outstanding work and next phase

Verify a successful schedule import after receipt174, then sustained collector cadence, and close the active application's stale ML prediction refresh and loaded-version sign-off under an explicit application recovery/rollout plan that accounts for unrelated existing workspace changes. NBA/soccer scheduler recovery is recorded, not inferred from a probe. Do not claim the old app process has loaded current contracts. Administrative raw-store access and final review remain outside the approved research workflow; no date-triggered automatic unseal exists.

Then proceed to **Stage2: source identity, permitted historical coverage and durable pregame provenance**. VAL-001 archives, SOC-004 league lineage, NFL-002 auxiliary ownership, and verified NBA player-log coverage remain priorities. Preserve the untouched NFL final109, existing models and research-disabled flags. No new model family or production-superiority claim is justified by these integrity repairs.
