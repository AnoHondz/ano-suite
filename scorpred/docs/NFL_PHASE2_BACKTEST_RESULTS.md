# NFL Phase 2 backtest results

**Current result:** [Phase 2.2](NFL_PHASE2_2_RELIABILITY.md) expands the frozen
888-game Class B development dataset to **385 evaluated games in ten folds**.
Small logistic records log loss 0.644881, Brier 0.226703 and 64.42% accuracy;
forest records 0.658181, 0.232520 and 58.70%. Elo has the best probability scores
(0.641521 and 0.225308). Neither candidate establishes consistent superiority
over Elo; the 109-game prospective final holdout remains untouched. No model
was promoted or activated. Class B does not establish historical point-in-time
availability. The dated Phase 2.1 results and original Class A audit below are
preserved; their different test populations must not be pooled.

## Phase 2.1 Class B measured results

| Model | Games | Log loss | Brier | Accuracy | ECE |
|---|---:|---:|---:|---:|---:|
| Home prior | 139 | 0.684146 | 0.245510 | 56.83% | 0.014375 |
| Sequential Elo | 139 | 0.685779 | 0.245627 | 55.40% | 0.063585 |
| Small logistic | 139 | 0.678255 | 0.242256 | 61.15% | 0.081834 |
| Expanded logistic | 139 | 0.708323 | 0.250808 | 58.27% | 0.122180 |
| Random forest | 139 | 0.649553 | 0.230126 | 57.55% | 0.071863 |

All rows compare identical unseen games across two accepted folds. Test coverage
is 3 games from the 2023 season, 105 from 2024 and 31 from 2025. Three further
folds failed unchanged sample thresholds. Forest log-loss improvement over the
home prior has a paired week-block bootstrap 95% interval of
**[-0.089456, +0.031166]**; versus Elo it is **[-0.066317, +0.000972]**.
Both include zero. Meaningful superiority is **not established**. The larger
logistic candidate is worse than the simple baseline on pooled primary metrics.
No test-ranked model replaces the predeclared simple research starting point.

Raw/sigmoid calibration was tested on these same test games, with fitting and
selection restricted to earlier, disjoint windows. Both folds retained raw
probabilities for the three main classifiers. Isotonic was unavailable: only
44/41 fitting observations per fold, below the predeclared 1,000 threshold.
Calibration generalization remains inconclusive. A sigmoid variant of the full
logistic happens to look better on test log loss, but it is not retrospectively
selected using those test outcomes.

The immutable run is `run_d51f0ec86b0dde870b81a606`, fingerprint
`55774852dd69eb7d3c0945795febce780dfeec8b6c7588d6fe458214304d5f86`.
Its separate Class B directory contains the manifest, raw/selected metrics,
probability-bucket CSV, reliability/confidence SVG/PNG, calibration-comparison
SVG/PNG and 18 disabled fold artifacts. See the
[complete recovery report](NFL_PHASE2_1_HISTORICAL_RECOVERY.md) for coverage,
publication assumptions, ablations, exact paths, writer verification and commands.

Latest validation: **663 backend tests passed**, frontend build passed, Ruff and
diff checks passed. Migration 3 adds collection receipts and immutable-history
guards; a real schedule refresh verified the current writer and preserved all
1,530,347 existing observation records by hash. The older app was not restarted;
the recovery report provides the operator reload procedure.

## Original Phase 2 Class A coverage audit

The default 60-minute pregame cutoff and 24-hour prior-completion guard produced:

| Season | Stored schedule games | Eligible training/evaluation rows | Schedule not observed by cutoff | Ties excluded | Not confirmed complete |
|---|---:|---:|---:|---:|---:|
| 2022 | 284 | 0 | 282 | 2 | 0 |
| 2023 | 285 | 0 | 285 | 0 | 0 |
| 2024 | 285 | 0 | 285 | 0 | 0 |
| 2025 | 285 | 0 | 284 | 1 | 0 |
| 2026 | 272 | 0 | 79 | 1 | 192 |
| Total | 1,411 | 0 | 1,215 | 4 | 192 |

The columns are mutually exclusive primary exclusion reasons, not cumulative
coverage funnels. The dataset JSON includes a game-level audit. Early-season
minimum-history checks are implemented, but these real files fail the earlier
schedule-vintage guard first. The 192 current-season rows include scheduled,
in-progress or otherwise insufficiently confirmed outcomes; they are not
silently labeled losses or forced into training.

Historical schedules for 2022–2025 were first observed locally on
**October 16, 2026**. Current-season schedule observations begin on
**October 18, 2026**. Their archive season is not their publication/receipt time.
No verified earlier publication archive exists in this store. A conservative
lag applied to an old game's kickoff cannot repair this lack of provenance.

Source coverage was expanded through the existing importer during this task:

| Season | PBP rows | Player-week rows | Advanced team-game rows |
|---|---:|---:|---:|
| 2022 | 49,434 | 18,831 | 568 |
| 2023 | 49,665 | 18,643 | 570 |
| 2024 | 49,492 | 18,983 | 570 |
| 2025 | 48,771 | 19,422 | 570 |
| 2026 | 15,497 | 6,131 | 160 |

2023/2025 PBP, player statistics and advanced rows were missing and have now
been imported. All 82 dataset partitions present at the beginning of Phase 2
were retained; 81 fingerprints were unchanged. An existing current-season
schedule writer refreshed the remaining partition without version tracking.
The current 272-row snapshot was captured/reconciled at its real observation
time; earlier unlogged intermediate versions cannot be reconstructed. Research
now blocks untracked writes. Reloading that existing process with the current
writer is required for reliable future collection; no restart was performed.
The additions use existing tables. Added source coverage improves
descriptive completeness but does not establish pregame availability years ago.

The store now contains **1,515,697 current records**, an increase of 139,044.
The latest saved audit is `run_3ce4a13d863e716a710c0d5a`, with dataset fingerprint
`7b02dc7abe4b011dd9ab5457595c5c4d23d24d9e0e251009f2401b52b2775c15`.
Its actual generation timestamp is `2026-10-18T23:33:57.354465+00:00`.
There are **100 versioned predictor columns**. Twenty-seven prospective feature
snapshots were prepared successfully, but these are not predictions or evaluated
games. Snapshot expiry and source changes require new offline preparation.
At the final integrity check, all 272 current-season schedule rows and all
15,497 current-season PBP rows matched their recorded observation versions.

## Original Class A baseline and calibration results

| Model | Log loss | Brier | Accuracy | ECE | Unseen NFL games |
|---|---|---|---|---|---:|
| Training-only home prior | N/A | N/A | N/A | N/A | 0 |
| Sequential Elo | N/A | N/A | N/A | N/A | 0 |
| Small logistic baseline | N/A | N/A | N/A | N/A | 0 |
| Expanded logistic candidate | N/A | N/A | N/A | N/A | 0 |
| Bounded random forest candidate | N/A | N/A | N/A | N/A | 0 |
| Archived production probabilities | Unavailable | Unavailable | Unavailable | Unavailable | 0 |

For Class A, no calibration, ablation, paired-improvement interval, feature-value conclusion,
favorite/underdog comparison or reliability chart is statistically available for
NFL games. Isotonic fitting also requires >=1000 calibration observations per
evaluated fold. The code will generate real evaluation tables, buckets and
optional matplotlib SVG charts when valid folds exist; it does not draw a
misleading reliability chart from zero observations.

**Recommended model now:** none. Start future comparisons with the home prior,
Elo and small regularized logistic baseline. Complexity has not earned a role.
Whether either candidate improves on baselines is **unknown**, not a measured
failure or a success.

## Leakage and integrity checks

Offline fixtures exercise source versions available before cutoff, target-game
exclusion, future QB/roster/stat corrections, schedule/venue revisions, numeric
play IDs, early-season missingness, tie handling and correct home orientation.
They also exercise sequential Elo, completed-only updates, season regression,
unavailable observations, same-week fold isolation, delayed outcome purging,
train-only imputation/scaling, fixed-seed fitting, four disjoint fitting/selection
stages and strictly later test windows.

Portable JSON probabilities are compared with independently fitted sklearn
forest predictions, not just serialization roundtrips. Tests check probability
bounds/sums, analytical log loss/Brier, Platt/isotonic behavior, calibration
selection on earlier data, immutable disabled registry entries, checksum/path
rejections and bounded/cycle-safe tree interpretation. Prospective fixtures
verify no raw PBP loads in web requests, source-fingerprint invalidation,
snapshot expiry, and rejection of models generated after a claimed prediction.

No new SQL schema change was needed in the original Phase 2 implementation.
Phase 2.1 adds migration 3, as described above. The existing versioned store remains
authoritative and the separate research registry has no model activations.
Live research predictions and regenerated backtests have separate storage and
explicit generation timestamps. NBA/soccer logic and existing NFL picks are
unchanged; no source/model artifact was deleted to rebuild anything.

## Original Phase 2 validation and changed files

- Full backend regression suite: **651 passed in 93.39 seconds**, including
  existing NFL refresh/routes, NBA, soccer and migration coverage.
- New research tests: **17 tests**, using isolated synthetic fixtures without
  live internet access. Synthetic classifiers/calibrators are not NFL results.
- Frontend production build: **passed** (TypeScript and Vite; 1,743 modules).
- Ruff checks for the six new Python modules/tests: **passed**.
- Real local route checks: default gate returned 404; both research gates
  enabled returned the disabled registry and insufficient-history report with
  HTTP 200; an unknown model returned 422. No production activation occurred.
- SQL changes: **no new table, migration or schema change**. Existing tables
  received the source backfill and the honestly timed schedule reconciliation.

New files are `services/nfl_winner_features.py`,
`services/nfl_winner_baselines.py`, `services/nfl_winner_evaluation.py`,
`services/nfl_winner_research.py`, `scripts/nfl_winner_research.py`,
`tests/test_nfl_winner_research.py`, and the two Phase 2 documentation files.
Existing files extended for this task are `services/nflverse_store.py`,
`nfl_routes.py`, `.env.example`, and `docs/NFL_ANALYTICS.md`.
Research datasets, audit runs, registry and snapshots are local cache artifacts;
they are separate from the production model manifest.

## Reproduce and inspect

```powershell
.\.venv\Scripts\python -m scripts.nfl_winner_research --seasons 2022,2023,2024,2025,2026 --lead-minutes 60
.\.venv\Scripts\python -m scripts.nfl_winner_research --seasons 2026 --snapshots-only
.\.venv\Scripts\python -m pytest tests -q
```

The audit exits 2 to distinguish insufficient evidence from a successful model
evaluation; it writes actual coverage and exclusion reasons. Inspect
`cache/nflverse/phase2/registry.json` for the immutable run ID and corresponding
`dataset.json`, `evaluation.json`, `results.md` and empty backtest prediction list.
No Class A model artifact is registered. Standalone prospective snapshots can be
prepared now without pretending that an evaluated winner model exists.

See [architecture and methodology](NFL_PHASE2_MODEL_RESEARCH.md) for feature
definitions, exact fold thresholds, registry paths, API gates and the evidence
required before any separate activation proposal. All sources remain free;
no paid dependency or deployment is introduced.
