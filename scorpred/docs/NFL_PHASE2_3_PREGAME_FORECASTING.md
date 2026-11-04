# NFL Phase 2.3: frozen models and genuine pregame forecasting

This phase builds an isolated, append-only research forecast archive. It does
not change production predictions, enable a model, expose holdout outcomes, or
deploy the application. The existing local collector performs scheduled archive
work; the multi-sport web application is not restarted.

The Phase 2.2 result remains unchanged: 385 reconstructed development games did
not establish consistent superiority over Elo. Phase 2.3 does not evaluate the
109-game holdout or claim validated prospective performance.

## Verified operational result

At **October 20, 2026, 16:48:13 UTC**, one game was inside the fixed 72-hour
forecast window, with 179 upcoming games on the observed season schedule.
**Two genuine forecasts for one game** were archived atomically by the scheduled
collector: `2026_07_NE_CHI`, New England at Chicago. Its observed kickoff is
October 23 at 00:15 UTC; its deadline is October 22 at 23:15 UTC.

| Frozen model | Chicago home probability | New England away probability |
|---|---:|---:|
| Elo v1 | 0.520891869903406 | 0.479108130096594 |
| Small logistic v1 | 0.508264927348489 | 0.491735072651511 |

Both share evidence cutoff **October 20 at 16:47:37.838961 UTC**, creation time
**16:47:46.334593 UTC**, and archive time **16:47:51.909603 UTC**. That is
55 hours, 27 minutes before kickoff. Their identical input fingerprint is
`8b43e93f9d06c2bfdf2c99d8f3c944f949c3d5785c8dd0e8c43277aabb4a017f`.
The schedule receipt is 69 (16:47:37.094283 UTC); the PBP receipt is 42
(11:35:08.848004 UTC). Both met the registered age limits. Logistic's rest
difference is missing because newer schedule context is not yet publication
eligible; its original training-median imputation was used and recorded.

The existing Windows task was gracefully reloaded as collector v2, **PID 7812**,
with loaded collector SHA-256
`73b6a17218275bee71f2148c6385a00ba8e00a60b493ee5f846eba6053e770d9`.
The first cycle refreshed all 28 existing feature snapshots but correctly
rejected the forecast because its schedule receipt expired during that work.
The next scheduled cycle refreshed the schedule and archived the pair, finishing
successfully at **16:47:52.098644 UTC**, with no collection errors. Totals are
**one audited rejected attempt, zero missed games**. A subsequent manual execute
at 16:48:36 UTC archived zero additional games and preserved the original pair.
One game remains within the collection window, already archived; zero are due
and unarchived, and 178 later games remain queued beyond that window.

Full source-based validation reproduced the classifier fit and both original
feature/probability records with **maximum absolute probability error 0.0**.
The separate standard-library verifier also reproduced both probabilities with
error 0.0, and rejected four deliberate corruptions made only in temporary
copies: altered study, model, input and forecast probability. The original
artifacts were not changed by these negative checks.

The initial preservation scan confirmed **83 existing artifact files** and
**1,530,347 original observations** unchanged. The streamed original-history
SHA-256 remains
`a83d4d8dd0b5310dc88548c31c96e06393adc8f01b8fd9bcf4c353a675cdc94f`.
The 109-game holdout remains sealed, with **zero holdout outcomes evaluated**.
No production model was activated, no application process was reloaded, and
no deployment occurred.

Supporting exports: [operational verification](assets/nfl_phase2_3/verification.json),
[preservation proof](assets/nfl_phase2_3/preservation.json),
[independent replay and corruption checks](assets/nfl_phase2_3/independent_replay.json),
[unchanged archive after retry](assets/nfl_phase2_3/retry_verification.json),
[pregame forecasts](assets/nfl_phase2_3/forecasts.json), and
[observed inputs](assets/nfl_phase2_3/inputs_2026_07_NE_CHI.json).
These are a timestamped verification snapshot; the local database continues
collecting later receipts and audit events.

## Frozen model pair

The study ID is `nfl-phase2-3-elo-logistic-v1`. Its two model versions are
`nfl-frozen-elo-v1` and `nfl-frozen-logistic-v1`. Both retain
`research_only=true`, `enabled=false`, `production_active=false`. Local research
collection is independent of production activation and of the disabled web
research feature flags.

Elo keeps the existing sequential algorithm: initial rating 1500, home adjustment
55 points, K=20, annual retention 0.75, and half-point updates for historical
ties. Its frozen seed contains 1,139 eligible 2022–2025 schedule results. The
latest reconstructed availability bound is February 16, 2026 at 11:30 UTC;
the seed was observed before registration, not before those historical seasons.
Its artifact evidence cutoff is the frozen source materialization at
**October 19, 2026, 14:07:10.715523 UTC**. This actual observation/materialization
time is distinct from the February historical availability bound.
Genuinely observed, sufficiently delayed 2026 results can update its input
ratings under this fixed algorithm. **Every reserved holdout game is excluded
from that history, even after its result becomes known.** The seed and settings
are never rewritten.

Logistic freezes the **last chronological Phase 2.2 fold**, not the fold with
the best test score. It has 604 base-training games, with its training cutoff at
December 25, 2024, 16:59:59.999999 UTC. The last base-training prediction cutoff
was December 17, 2024, 00:30 UTC. Its disjoint development windows contain 61
tuning, 56 calibration-fitting and 60 calibration-selection examples. Their
latest outcome availability bound is December 23, 2025, 13:15 UTC. No 2026
result enters classifier fitting, parameter selection or calibration.

The existing C=0.1 logistic regression, seed 1729, 3,000-iteration limit,
training-median imputation and training StandardScaler are retained exactly.
The original fold's sigmoid calibrator is retained: coefficient
1.256824552279779 and intercept -0.3004211534353791. Its selection used the
earlier development windows. We do not switch to raw probabilities based on
later test scores.

Registration independently repeats the original tuning grid (C=0.1/1.0), model
fit, preprocessing and raw/sigmoid calibration selection. The serialized model
and calibrator must match the original artifact **exactly**, otherwise
registration fails. The historical development dataset fingerprint remains
`55774852dd69eb7d3c0945795febce780dfeec8b6c7588d6fe458214304d5f86`.
No original research artifact is modified.

Artifacts record identifiers, training/development game IDs, training-sample
and source-dataset fingerprints, configuration, cutoff times, evidence classes,
feature order, feature/code fingerprints, exact software versions and SHA-256.
The logistic artifact includes its approved training and development samples
so reproduction does not require a mutable external training cache. The study
also stores the exact source bytes and per-file checksums in a frozen source
bundle. Source is archived as data and is never dynamically executed.

Registration committed on **October 20, 2026 at 12:38:20.079135 UTC**, before
the November 26 deadline. The exact canonical artifacts are exported alongside
this report; their SHA-256 checksums are:

| Artifact | SHA-256 |
|---|---|
| [Elo v1](assets/nfl_phase2_3/nfl-frozen-elo-v1.json) | `1d3498489943452fa1000850e8bdbbb326220070e54e7d4e86a55b6145a22e8b` |
| [Logistic v1](assets/nfl_phase2_3/nfl-frozen-logistic-v1.json) | `98b751981b21e6862f6880304b5e0747a38b5cf8c337d8d4088b99e93783524c` |
| [Study and frozen source bundle](assets/nfl_phase2_3/study.json) | `fd4526c5da021c30c28538dcdb67faadc18f581d7ac451a2d56f1f6589534cb7` |

The Elo training-data fingerprint is
`b3364bd10e49cff21883477c9185f51980a5c9c57867670e09c9058e4bfc0ceb`.
The logistic training-data fingerprint is
`d78a40b7d265ae7b715c3953c9ed7a1271df778b463549b64fb546687b3fcb5f`.
Both share feature-mapping fingerprint
`9aa5d080d5887f9ff0ebcb3e9d3227ec07eaad24a77b0d175d846e58922d2aa2`
and frozen feature/source-code SHA-256
`785fd83db69be4b7ad978d74797b9b5ac481b0d648831720504bc87dae5ea72b`.
These identify the actual registered artifacts, rather than an earlier dry run.

## Matched feature mapping and evidence classes

The logistic feature order is fixed:

1. `home_field`: 1 at the nominal home venue, 0 at a neutral venue.
2. `diff_win_rate`: home minus away mean result in the last three eligible
   same-season PBP games; a historical tie contributes one half.
3. `diff_rest_days`: home minus away elapsed days since the latest eligible
   game. It is missing when a more recent played game is excluded by the delay.

This is the existing small model's supported subset. The two unsupported EPA
inputs remain excluded; no injuries, starter assertions, quarterback metrics,
odds, weather, full-season aggregates or additional predictors are added.
Missing rest receives the original training median. Missing core win-rate or
venue inputs, insufficient history, or absent measured event statistics reject
the forecast rather than inventing inputs.

Eligibility keeps the same completion bound plus correction/publication delay:
12 + 168 hours. A known later publication time extends it. Each team needs at
least three same-season PBP games and measured event yards. Native game IDs,
canonical team aliases and home/away orientation must agree. Elo's saved input
order is home field, home rating and away rating; its probability formula is
unchanged.

Reserved scores never enter statistical history. A prior reserved kickoff can
still identify uncertain physical rest without reading its result; a more recent
potentially played schedule masks rest instead of implying a long break since
an older statistics window. Schedule-only provenance is recorded separately.
Omitting all reserved results means statistical context can age during weeks
12–18; this conservative, frozen restriction must be disclosed in future review.

The new three-input assembly was independently compared with **all 888 frozen
Class B historical rows**, with tolerance 1e-12 and matching missingness. The
full collector's 100-feature Class A snapshots are not silently substituted.

New forecasts explicitly distinguish **Class A observed input evidence** from
**Class B historical model-training evidence**. A current, actually observed
correction can be used only if its immutable source version existed by that
forecast's evidence cutoff. Unknown publication times stay unknown; an embargo
is a conservative selection rule, not a fabricated publication timestamp. The
older inference loader still rejects Class B artifacts. This separate matched
research workflow does not weaken that loader or promote a model.

## Scheduling and deadline enforcement

The installed `ScorPred NFL Collector` Windows task remains the single collection
worker. Collector v2 refreshes schedules every four minutes, PBP every six hours
and the existing full feature snapshots every twenty minutes. After these stages,
it attempts the registered frozen study. Forecast failures are reported without
stopping independent collection stages or changing previous forecasts.

The frozen collection rule is **the first eligible cycle between 72 hours and
60 minutes before each game's independently observed kickoff**. It is not a
claim that every game starts at the global registration deadline. Every target
uses its own latest observed native schedule and time-zone-aware Eastern-to-UTC
conversion. Games beyond the window remain scheduled, not falsely successful.

The evidence cutoff is the real UTC time of the source snapshot. Creation and
archive times are sampled from the actual clock after preparation. Their order
must be source observation <= evidence cutoff <= creation <= archive <= kickoff
minus 60 minutes. There is no CLI `as_of` override. A clock moving backward,
expired source, changed schedule, or deadline crossing rejects the transaction.

The engine requires an associated target schedule receipt no older than five
minutes and a successful PBP receipt no older than six hours. Future receipts
are excluded, including a pull that began earlier but finished after the cutoff.
Only earlier eligible games' PBP is selected before decoding; SQL projections
remove all reserved scores and all future/same-day scores. Source observations
and original receipt times remain immutable.

Each input archive also pins the highest committed source-version row ID and
collection-receipt ID visible to its snapshot. Rebuilds use those same boundaries.
A collection observed earlier but committed later therefore cannot change an
already saved input or break its historical replay. Postponed, suspended,
cancelled or in-progress targets are rejected.

Before committing, the engine rebuilds the inputs from their recorded source
vintages and compares the entire feature fingerprint. Both probabilities must
be finite, in [0,1], and complementary. Predictions are binary conditional on no
tie; an unconditional NFL tie probability is not fabricated.

One database-path lock prevents duplicate collectors. A second database-path
lock serializes manual and scheduled forecast runs. Unique study/game/model
keys and an atomic transaction commit **both models for the same game or neither**.
Retries before deadline are safe. Restarting scans durable state; it neither
replaces existing records nor creates duplicate forecasts.

An unarchived game whose deadline passed after study registration receives one
permanent missed event. Games before registration are not retrospectively
labeled as misses. A missed game is never backfilled. Later schedule/identity
changes append audit events against the original forecast; they do not create a
replacement. Such events must be considered in a future validity review.

## Archive schema and integrity

Additive migration 5 extends the existing NFL SQLite database:

| Table | Purpose |
|---|---|
| `nfl_frozen_models` | Canonical JSON artifacts with unique model IDs and SHA-256 |
| `nfl_forecast_studies` | Immutable model pair, feature policy, source bundle, deadlines and holdout seal |
| `nfl_forecast_inputs` | Complete model inputs, missingness, receipts and source fingerprints keyed by content hash |
| `nfl_forecast_batches` | Unique study/game pair and actual archive timestamp |
| `nfl_forecasts` | Two immutable per-game probability records and their checksums |
| `nfl_forecast_events` | Append-only rejections, misses, schedule corrections, operator notes and scheduler receipts |

Database triggers reject UPDATE, DELETE and duplicate INSERT/REPLACE. These
protections cover SQL replacement behavior as well as ordinary application
upserts. Existing migrations, source records, version history, collection
receipts and observation timestamps are preserved.

Each forecast records native ID, canonical teams, observed kickoff, creation
time, archive time, evidence cutoff, model ID/version, feature/code fingerprints,
both probabilities, data freshness, missing inputs, evidence classes, status
and artifact checksum. Immutable inputs permit independent probability replay;
full validation also reproduces the logistic fit and rebuilds original features
from the source observation history. A replay never generates a new forecast.

Artifact SHA-256 is over the **exact UTF-8 canonical JSON bytes stored in the
database** (sorted keys, compact separators, no NaN). Pretty-printing an artifact
changes its file hash; exported canonical artifacts must retain those bytes.
Working code, library, artifact, source bundle or protocol mismatches fail closed.
No automatic training, replacement or model selection follows such a failure.

## Holdout and access controls

The original Phase 2.2 protocol is unchanged, hash
`a4b906096d31dccbeceabdf4ffa1a275dccdd6a08206b2b636bac8026f202281`.
The same 109 games remain sealed. Registration must finish before **November 26,
2026, 00:00 UTC**, and every reserved game's actual observed kickoff must still
be future and before its own forecast deadline at registration. Actual observed
kickoffs are recorded again at freeze; the scheduler checks them independently.

Reserved IDs are rejected by model fitting and chronological evaluation, excluded
from newly built historical research datasets and from online Elo context, and
blocked in the old per-game research routes. The forecast archive records only
pregame probabilities and input provenance. Its status route never reads or
returns actual winners, scores, outcome labels or holdout evaluation metrics.

The new Insights **Pregame research** view uses the existing Graphite & Sage
styles and the label **Experimental NFL Winner Research — Not Used for Production
Recommendations**. It shows model versions/checksums, queued games, archive
counts, misses, rejection events, evidence quality, collector freshness and the
evaluation lock. It contains no generation, activation or evaluation controls.

Both existing environment gates remain off by default:
`NFL_ADVANCED_RESEARCH_ENABLED` and `NFL_WINNER_RESEARCH_ENABLED`. The new
read-only status endpoint additionally requires an authenticated account:
`GET /api/nfl/research/winner/forecasts/status`. Mutations are rejected.
`/api/nfl/research/winner/forecasts/evaluation` remains locked with HTTP 423.
Reaching March 1, 2027 does not automatically unlock anything: an explicitly
authorized future review is still required. No holdout evaluation is implemented
or performed in this phase. Phase 2.3 archives Elo/logistic only; it does not
retroactively change Phase 2.2's comparison family or fabricate forest forecasts.

The new web code is built and tested but the running multi-sport web process is
left unchanged. Its existing feature flags are not enabled. A later controlled
application reload would be needed to serve the new gated API from that process.
The independently managed collector can archive research predictions now.

## Operator commands and recovery

Run from the repository root, using the existing environment:

```powershell
.venv/Scripts/python.exe -m scripts.nfl_pregame_forecast --registration-dry-run
.venv/Scripts/python.exe -m scripts.nfl_pregame_forecast --register
.venv/Scripts/python.exe -m scripts.nfl_pregame_forecast --status
.venv/Scripts/python.exe -m scripts.nfl_pregame_forecast --dry-run
.venv/Scripts/python.exe -m scripts.nfl_pregame_forecast --execute
.venv/Scripts/python.exe -m scripts.nfl_pregame_forecast --forecasts
.venv/Scripts/python.exe -m scripts.nfl_pregame_forecast --forecasts --game-id <native-id>
.venv/Scripts/python.exe -m scripts.nfl_pregame_forecast --replay
.venv/Scripts/python.exe -m scripts.nfl_pregame_forecast --validate
.venv/Scripts/python.exe scripts/verify_nfl_forecast_exports.py --study-sha256 fd4526c5da021c30c28538dcdb67faadc18f581d7ac451a2d56f1f6589534cb7
.venv/Scripts/python.exe -m scripts.nfl_pregame_forecast --audit-note "Reason for operator investigation" --game-id <native-id>
& scripts/manage_nfl_collector.ps1 -Action Status
& scripts/manage_nfl_collector.ps1 -Action Reload
```

Registration is one-time and refuses replacement. Repeating it verifies and
returns the existing study. Dry runs write no model or forecast registration;
ordinary database schema initialization can still occur. Execute uses the same
lock and durable uniqueness rules as the scheduler. Exit 2 signals a degraded
forecast cycle or unregistered study, not successful archived predictions.

The standalone export verifier uses only Python's standard library, independently
calculates Elo and calibrated logistic probabilities, and imports no ScorPred
services. It checks the expected study checksum supplied from this report,
model/source/input hashes, team mappings, paired records and timestamp order.
It reads no holdout outcomes and executes no bundled source. Its input is the
canonical export directory; the separate research CLI `--validate` additionally
rebuilds features from the original observation database and reproduces the
approved logistic fit.
The [verifier source](../scripts/verify_nfl_forecast_exports.py) is retained
alongside its checksum in the independent replay packet.

For a collection interruption, inspect `cache/nflverse/collector/status.json`,
`cycles.jsonl`, `collector.log`, the forecast events and receipt ages. Fix the
underlying network/source issue; then reload only the collector using its manager.
The manager waits for a graceful stop and verifies the task identity. Never
launch a second worker or edit timestamps to recover a missed deadline.

For a checksum/code mismatch, stop forecast work by leaving the mismatch intact
and investigate; collection can continue while its forecasting stage reports
degraded. Restore the exact audited source/library environment or use an isolated
matching checkout to replay. The frozen source bundle supplies the original
bytes; it must not be automatically executed. Do not rewrite the registered
artifact or unseal outcomes to resolve an operational error. Corrections belong
in separate audit notes.

Preserve the SQLite database using SQLite's online backup facility rather than
copying an open WAL database's main file alone. Keep historical datasets and the
exported canonical artifacts with that backup. This local task requires the PC
awake, user logged in and a working network. It is not an always-on hosted service
and this phase performs no deployment.

## Validation and remaining limitations

The final backend run passed **692 tests in 283.57 seconds**, including the
existing NFL, NBA and soccer regression suites. The frontend production build
passed TypeScript and Vite compilation (1,746 modules, 18.10 seconds). Both
targeted browser checks passed (22.7 seconds): disabled access stays restricted,
and authorized access displays provenance and the sealed evaluation state while
issuing only read-only research status requests. Ruff and whitespace checks
passed for the changed Python files.

Coverage includes exact artifact reproduction, independent probability
calculation, mapping and fingerprint mismatches, missing required inputs versus
approved median imputation, UTC and future timestamp rejection, late archive
rollback, atomic paired archives, SQL INSERT/REPLACE protection, restart/retry
deduplication, source-history replay after late commits, postponed targets,
schedule corrections, sealed schedule-only rest context, authorization and
disabled production flags. Two pre-existing fixture clocks were refreshed per
test so lengthy suite execution cannot expire their five-minute freshness;
production freshness limits were not changed.

The prospective accuracy of these frozen models remains unknown. Source
collection interruptions, schedule corrections, the fixed 72-hour window and
Class B training assumptions must remain visible when interpreting any future
comparison. Do not infer production readiness from successful collection or
exact numerical replay. The prior 385-game development comparison did not
establish a convincing improvement over Elo; it is unchanged by this phase.

The next operational step is to keep the local collector running with the PC
awake and user logged in, monitor source freshness and rejection/missed events,
and preserve paired forecasts without retraining. The first reserved game's
currently observed kickoff is November 26, 2026 at 01:00 UTC, so its 72-hour
collection window opens November 23 at 01:00 UTC. Subsequent schedule changes
must be checked against each game's actual kickoff. After the season, request
explicit authorization for the registered paired evaluation on or after
March 1, 2027; do not inspect holdout outcomes or tune before that review.
