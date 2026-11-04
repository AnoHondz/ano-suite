# ScorPred remediation roadmap

This is a proposed implementation plan. The audit implemented none of these production fixes. Existing data and research identities must be preserved; no deployment, new predictor family or sealed-outcome evaluation is authorized by this document. Estimates are engineering effort, not promised calendar dates; prospective validation also requires future games.

The [machine-readable register](../cache/diagnostics/2026-10-20_multisport/issue_register.json) contains each issue's sport, component, measurement, severity, prediction impact, established cause, confidence, fix, existing/free resource, complexity and acceptance test. P0 means a scheduled research-integrity deadline; P1 means correctness/provenance or currently degraded operations; P2 means bounded completeness/validation limitations. Suspected predictive harm is explicitly distinguished from a confirmed mechanism.

## Ranked issues

| Rank /ID | Status | Finding and measured scope | Priority |
|---|---|---|---|
| 1 NFL-001 | Confirmed future risk; no observed breach | Collector's generic full-feature history reader and two legacy training paths lack reserved-outcome guards; 109 future games protected only on registered paths | P0 before November 26 freeze /first reserved kickoff |
| 2 OPS-001 | Confirmed operational degradation | App health 503; last successful refresh ages 524–539 minutes; NFL PBP ConnectionError/backoff while schedules succeed | P1 |
| 3 VAL-001 | Confirmed evidence gap | 47 ordinary tracking rows do not bind kickoff/source/model; NBA current winner and soccer live-blend accuracy unknown | P1 |
| 4 SOC-001 | Confirmed data defect | 2018 raw file contains 319 duplicate 2025–26 fixtures; legacy processed table has 319 excess rows /2,918 | P1, scope excludes clean current classifier |
| 5 SOC-002 | Confirmed mapping problem | Clearances uses blocks; aerial wins uses all duel wins; completed passes lacks definition proof | P1 |
| 6 NBA-001 | Confirmed unsupported pathway | Real numeric-ID player logs empty; props route defaults to 2024; separate overview has uncapped stale fallback | P1 |
| 7 SOC-003 | Confirmed original defect; corrected on disk, validation pending | Original five-vs-ten-match mismatch changed 39/1,518 selections in sensitivity; current source adds ten-match and target-date contexts outside audit | P1 parity/version verification |
| 8 SOC-004 | Confirmed lineage gap | 7,888 training rows lack league/season/receipt lineage; per-league validation unavailable | P1 |
| 9 OPS-002 | Confirmed compatibility gap; live harm unproved | Missing freshness metadata passes future-game gate, while explicit unknown state rejects | P1 |
| 10 NFL-002 | Confirmed auxiliary coverage/freshness gap | Team stats cover 88 of93 initial completed fixtures; auxiliary observations largely October18 | P2 |

Additional issues: unsupported 13 soccer columns; 7/58 clubs missing Elo history; no draw argmax selections despite plausible pooled draw probabilities; misleading NBA possession-metric naming; missing injuries treated as empty/healthy; offense-rule saturation; validation profile/artifact lineage mismatch; and volatile monitor history. None of these establishes its own causal prediction-accuracy loss. NFL weak signal/overfitting remains a hypothesis, not a root-cause finding.

## Stage 1 — Critical data repairs

**Dependencies:** audit evidence, known running owners, frozen NFL protocol/source fingerprint, and current outside-audit source changes. **Effort:** approximately 5–8 engineer-days; separate NFL isolation design/review before implementation. **Benefit:** prevents avoidable corruption, premature holdout access, misleading statistic values and stale predictions.

First design a holdout-safe shared outcome-access boundary covering generic NFL feature/snapshot readers and legacy trainers. Inventory pinned files before editing. Choose an explicit isolation/version transition that preserves v1 study/model/input identities; do not overwrite the study fingerprint or retrain. Check real collection output without reading held results. Acceptance: synthetic reserved-result sentinels never reach a history aggregator or snapshot, every legacy training entry rejects the sealed interval, v1 exports independently replay unchanged, and collection has exactly one owner. Complete before the November freeze; do not rely on the registered forecaster alone.

Investigate observed network/refresh errors using timestamped attempts, DNS diagnostics and source status. Restore retry/recovery behavior without replacing retained data or stamping it fresh. Acceptance: an actual successful scheduled refresh after a recorded failure, correct original source age, explicit stale suppression and no duplicate job owners. A successful HEAD alone does not complete this work.

Quarantine the mislabeled soccer raw file in a new versioned manifest, validate season/date ranges and deduplicate derived inputs while preserving originals. Acceptance: a new derived version has zero fixture duplicates, all date/season contracts pass, and the clean classifier/scoring artifacts retain their original hashes until an explicitly approved training step.

Verify the outside-audit soccer changes through target-date/window parity at each route; bind the deployed/loaded version separately. Correct prop definitions, require null/unsupported for unknown quantities, repair real numeric-ID NBA data contracts and season defaults, and cap stale overview use. Acceptance: fixtures with known boxscores map exact definitions, unsupported markets are explicit, unknown injuries are distinguishable from healthy, and source expiry cannot be bypassed by a freshly generated response timestamp.

## Stage 2 — Data coverage improvements

**Dependencies:** Stage1 contracts and permitted-use review. **Effort:** 5–10 engineer-days. **Benefit:** known denominators, correct identities and useful current auxiliary coverage.

Reconcile fixture IDs, source competition/season, completed status and teams against versioned source manifests. Keep NBA preseason/Cup/playoff identity separate and preserve partial-season boundaries. Attach league/season and provider IDs to newly versioned soccer data only where independently evidenced; do not infer missing leagues from a name. Fill missing historical seasons/fixtures through permitted free sources after reconciling availability and rights.

Use existing nflverse schedules/PBP and source-specific permitted auxiliary feeds; give daily/periodic auxiliary refresh explicit ownership and receipts. Participation is not an available in-season 2026 input. Use the existing public NBA schedule/summary/roster/overview adapters where permitted; do not call undocumented possession/lineup proxies verified data. Use available football-data.org free entitlements with rate limits, or public-domain openfootball results as a coverage cross-check after checking season scope. New football-data.co.uk automated training acquisition requires resolving its published restrictions first.

Acceptance: per sport/league/season a declared fixture universe, reconciled missing/duplicate/conflict counts, explicit unmapped-team queue, source IDs and receipt age; all completed target games represented or an enumerated justified exception. Every auxiliary refresh has successful and failed receipts. No added model inputs merely because more columns are downloadable.

## Stage 3 — Feature and model reliability

**Dependencies:** Stages1–2 plus immutable input contracts. **Effort:** 5–8 engineer-days for implementation; prospective evidence accumulates over games. **Benefit:** valid comparisons of the existing methods.

Bind exact feature schema, preprocessing, artifact and decision-policy hashes. Make route features reproduce offline features from the same cutoff, window and target time, including absent values. Separate descriptive rule confidence, classifier probability and decision confidence in all contracts. Keep original optional columns/model signatures intact until a versioned training decision; zero-filled xG/referee fields are not measured inputs.

Evaluate the existing NBA rules and actual soccer blend against train-only priors and appropriate existing Elo/rule baselines on matched chronological data. Report proper losses, accuracy, classwise calibration, paired intervals and season/league consistency. Historical-only timestamps keep results ClassB. Freeze an independent future window before looking at results. Do not tune on the NFL final109.

Acceptance: same games for each paired comparison, train/calibration/test separation, untouched final-period manifest, no target/postkickoff statistics, calibration-bin counts and denominator-specific missingness, reproducible predictions without refitting frozen artifacts. No production superiority claim unless the predeclared primary endpoint and uncertainty support it.

## Stage 4 — Operational monitoring

**Dependencies:** Stage1 refresh contracts; can proceed alongside Stage2. **Effort:** 3–5 engineer-days. **Benefit:** visible failures and provable pregame collection.

Persist receipt/attempt/heartbeat history for NBA/soccer as well as NFL, with original receive/publication times distinct from response generation. Bind forecasts to source receipts and input/model/policy identities. Monitor source age, never-successful pipelines, failed retries, missed deadlines, owner identity and duplicate starts. Align health thresholds with card expiry and test restart recovery without altering live research.

Acceptance: simulated timeout, DNS failure, rate limit, partial import, restart and unknown freshness all leave retained records intact and emit correct alerts/stale state. A real pregame cycle proves received-before-kickoff and atomic forecast writes. Selected forecasts can be replayed offline from their immutable receipts. SMTP or other alert delivery is verified with an authorized test, not assumed from code.

## Stage 5 — Prediction quality improvements

**Dependencies:** valid data, feature parity, frozen evaluation and adequate prospective observations. **Effort:** 3–5 engineer-days per justified existing-method experiment, plus the required future observation window. **Benefit:** evidence-based changes rather than extra complexity.

Investigate only documented shortcomings of existing methods: rule saturation/missingness, draw decision behavior, calibration drift and existing model/policy comparisons. No additional family or unnecessary predictors are justified now. Accept changes only on matched primary proper-loss results with paired uncertainty and consistency evidence; retain simple baselines when superiority is unconvincing. Keep research disabled unless a separate release decision authorizes it. Deployment is outside this task.

The exact next implementation phase is **Stage1: holdout-safe outcome access and frozen-version isolation, refresh recovery, and corrected-input contract validation**. Begin with the NFL boundary design because its source fingerprint and November deadline constrain the rest; preserve all v1 artifacts and never open the final109 to design the repair.
