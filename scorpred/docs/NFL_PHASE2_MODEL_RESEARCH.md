# NFL game-winner probability research

This is a backend-only, disabled research extension of the existing NFL pipeline.
It does not change dashboard decisions, existing scoring forecasts, model
activation flags, NBA/soccer logic or deployment configuration. It uses existing
free nflverse observations and installed scikit-learn 1.8 dependencies. No paid
API, new data pipeline or untrusted pickle loading is introduced.

**Current finding:** Phase 2.2 evaluates **385 identical Class B test games**
across ten chronological folds from the frozen 888-game reconstruction.
Small logistic improves on the home prior, but neither candidate convincingly
or consistently beats Elo. These are reconstructed development results, not
historical point-in-time observations. A future 109-game final holdout remains
sealed. Current-code prospective collection now runs as a guarded local singleton;
all research models remain disabled. See the complete
[Phase 2.2 reliability report](NFL_PHASE2_2_RELIABILITY.md). The earlier
[measured results](NFL_PHASE2_BACKTEST_RESULTS.md) and
[Phase 2.1 recovery report](NFL_PHASE2_1_HISTORICAL_RECOVERY.md) remain preserved.

## Architecture and Phase 1 verification

The store contained 1,376,653 records when inspected, with migrations 1 and 2
applied. It had the reported 1,298 advanced team-game rows for 2022/2024/2026.
SQLite quick checks passed, and all 79,762 payloads from the pre-expansion backup
were found in version history. Existing schedules and weekly team statistics for
2023/2025 were present; PBP/player stats were missing for those two seasons.
Those were imported using **the existing** `scripts.sync_nflverse` and its
`--derive` path, adding 139,044 records, including advanced 2023/2025 rows.
Phase 1 datasets and source versions are preserved.

A concurrent current-season schedule writer was found using null observation
timestamps without appending source versions, consistent with a process still
running pre-versioning code. Research now detects and refuses this condition.
The current 272-row snapshot was captured and reconciled at its real receipt
time, without inventing history. Intermediate unlogged snapshots cannot be
reconstructed. Phase 2.2 supersedes the earlier manual-reload requirement:
additive migration 4 now rejects unversioned writes before mutation, and the
current writer runs in a separately managed local collector using these same
import modules. It was gracefully reloaded without restarting or deploying the
multi-sport application. See the Phase 2.2 report for operational checks and
remaining network/uptime dependencies.

| Module | Role |
|---|---|
| `services/nflverse_store.py` | Existing SQLite/version abstraction; season-filtered deterministic observations, untracked-writer detection and honest receipt reconciliation |
| `services/nfl_winner_features.py` | Cutoff-aware source vintages, rolling features, labels stored separately, reproducible dataset and coverage audit |
| `services/nfl_winner_baselines.py` | Deterministic sequential Elo with fixed, configurable research settings |
| `services/nfl_winner_evaluation.py` | Grouped walk-forward folds, baselines, logistic/forest candidates, train-only preprocessing, calibration, metrics and paired intervals |
| `services/nfl_winner_research.py` | Disabled registry, checksum-verified JSON inference, compact snapshot preparation and prospective research output |
| `scripts/nfl_winner_research.py` | Local audit/evaluation/snapshot command, measured Markdown summary, optional scientific plots |
| `nfl_routes.py` | Three separate opt-in winner research routes; original responses stay intact |

No database migration or new SQL table was required for Phase 2. Existing source
and observation tables remain authoritative. Research artifacts live under
`SCORPRED_DATA_ROOT/cache/nflverse/phase2`, separate from production manifests:

- `registry.json`: run/model IDs, flags, artifact checksums, schema, fingerprint,
  model type, periods, calibration and evaluation metadata.
- `run_<id>/dataset.json`: reproducible predictors, separate labels, audit and
  source fingerprints. The ID includes feature/data/config/library versions.
- `run_<id>/evaluation.json`, `results.md`, `backtest_predictions.json`: actual
  evaluation outputs; when evidence is insufficient, metrics are unavailable.
- Per-fold model JSON files only when valid chronological fits exist.
- `snapshot_history/`: preserved prospective feature snapshots; `snapshots/`
  holds the latest compact cache for each game/configuration.
- `live_research/`: prospective research outputs, separate from backtests.

Completed run registrations are immutable. Every new model/run is strictly
`research_only=true`, `enabled=false`, `production_active=false`. There is no
promotion/activation endpoint or automatic artifact selection. Existing
production manifests are never edited by this workflow.

Phase 2.1 adds `services/nfl_historical_recovery.py` and additive migration 3:
append-only `collection_receipts` and `collection_games`, plus immutable-history
triggers. Existing record payloads and observation timestamps are retained.
The supplied Class B command writes a separate registry under
`cache/nflverse/phase2_1/class_b`; it contains 18 disabled fold artifacts.
Class B artifacts are rejected by prospective inference. Actual receipts,
including failed attempts, are inspectable through the existing importer
`--status` command and the gated research coverage endpoint.

## Evidence classes added in Phase 2.1

The time rules and full feature definitions below describe **Class A**. Its
receipt cutoff is unchanged. Class B is an explicit separate command, dataset
version, manifest, evaluation population, registry entry and prediction field.
Evaluations reject mixed evidence classes.

Class B assumes an unverified finish no later than kickoff +12 hours and waits
another **168 hours** for correction/publication. These are configurable bounds
with minimum values of 12/96 hours; they are never written into observation
timestamps or represented as actual publication/completion times. A reliable
reported game-end timestamp replaces the assumed finish when present. Known
later publication dates extend availability. Same-day sources are excluded.
Detected raw-event/result/schedule revisions and later appended plays exclude
the affected source game; unobserved archival revisions remain a limitation.

Only 46 of the original 100 feature names are supported in Class B. Unverified
EPA, EPA-based success, adjusted EPA and all QB inputs remain missing. Rest
inputs also remain missing when a more recent played game is excluded by the
publication delay, preventing an older statistics window from implying extra
physical rest. Models train only on the predeclared supported names; the small
logistic baseline reduces to home venue, recent form and available rest.
No prior-season team-stat carryover is used; sequential Elo alone carries its
documented regressed ratings across seasons using eligible earlier scores.

The availability manifest lists each game's dependencies, feature completeness,
exclusion reasons, assumptions and dataset fingerprint. A shared source-game
catalog retains completion bounds, assumed availability and actual observation
receipts. Class B label availability is also explicitly assumed, allowing
chronological fitting without pretending retrospective ingestion was pregame.

## Time and availability rules

Default prediction cutoff is **60 minutes before scheduled kickoff**, configurable
with `--lead-minutes`. All timestamps require timezones and are normalized to
UTC. Target schedule/team/venue must exist in a source version observed by the
cutoff. A later kickoff or team-identity correction excludes that historical
row; a venue flag uses its earlier known version, including for Elo.

The four times are distinct:

1. **Event time:** actual scheduled kickoff from the game record.
2. **Publication time:** unknown for these historical source versions, recorded
   as null. A release-file creation date cannot prove when its mutable contents
   became available.
3. **First observation:** the receipt of the exact source version in
   `record_versions`. Unchanged pulls retain that first version observation.
4. **Ingestion/materialization:** existing source ingestion and observation
   receipts coincide because Phase 1 did not record them separately. A newly
   calculated dataset/snapshot has its own real generation time; this never
   backdates its source observations.

Only prior games whose known score pair is complete and whose kickoff is at
least 24 hours before cutoff can contribute statistics. This completion lag is
an additional conservative policy, **not** an invented publication timestamp.
The source version must independently pass the observation cutoff. Phase 1
aggregates computed later are not treated as earlier observations: metrics are
reconstructed from eligible raw PBP vintages using existing metric functions.

Outcome labels are held outside predictors. They use the current completed
result; fitting eligibility additionally requires the outcome to have been
observed before that fitting stage. `usable_after` is the maximum of the real
first matching outcome observation and the 24-hour completion guard. Outcomes
not available at a fold boundary purge their entire game-week from that stage.
Cancelled/unfinished/TBD games remain excluded. NFL ties are explicitly excluded
from binary labels; they are half-point context in historical form/Elo only.

The two outputs are **conditional on no tie**: `P(away)=1-P(home)`. They are not
an unconditional three-way NFL forecast. No tie probability is fabricated.

## Features and missing-data policy

The schema/version is separate from the existing 19-feature NFL schema. Generate
home and away descriptive features plus model differentials (home minus away).
The classifier uses differences and venue, avoiding simultaneous duplicate
home/away/difference columns. Overlapping rolling windows still correlate;
regularization and ablation checks are necessary before any feature promotion.

For previous **3, 5, 8 and season-to-date** games, compute:

| Group | Features |
|---|---|
| Offense | EPA/play, passing EPA/dropback, designed-rush EPA/play, success rate, yards/play, explosive rate, third-down conversion, red-zone TD efficiency, turnover-play rate, sack rate allowed |
| Defense | Opponent EPA/play, passing/rushing EPA, success, explosives, third downs, red-zone TDs and sacks generated |
| QB | Passing EPA/dropback, completion fraction, interception rate, sack rate and standard deviation of per-game passing EPA |
| Context | Non-neutral home venue, rest-day difference, short week (<6 days), bye proxy (>=12 elapsed days), previous-three-game win rate, opponent win-rate schedule strength and opponent-adjusted allowed EPA |

Metric definitions and observed denominators reuse
[Phase 1 analytics](NFL_ANALYTICS.md#metric-definitions). Rolling rates pool
their observed numerators/denominators. Missing denominators stay missing; they
never silently become zero or a league-average statistic. Stability requires
two measured QB games. Team matching uses the existing spelling aliases;
relocation-era IDs are retained. Descriptive offense-versus-opposing-defense EPA
gaps are exported but are not duplicated in the linear classifier's columns.

QB selection uses the **last observed game's highest-dropback passer**, with
deterministic ID tie-breaking, as a disclosed historical fallback. It never
asserts the next starter. Observed season passing appearances are counted once
as a separate experience context feature, including prior observed appearances
on another team; this is not career experience. Future roster/depth entries
never select a historical quarterback. Missing passer IDs stay missing.

Minimum history is three same-season PBP games per team; no prior-season carryover
is invented. Early-season games are excluded explicitly. The requested windows
use all available prior games up to their length after that minimum. At least
some prior offensive EPA must be measured for each team. Remaining feature
missingness is reported and imputed from the **training fold only**; an entirely
missing training column uses sklearn's documented zero fill, not an invented
football statistic. Injuries, starting-lineup assumptions, travel coordinates,
weather, odds and future full-season summaries are excluded.

## Models, folds and calibration

Home prior is the training fold's home-win fraction. Elo starts at 1500, uses
55 home rating points (zero at neutral venues), K=20 and .75 annual retention.
All settings are configurable through `EloConfig`. Its ratings incorporate only
completed score versions known by the target cutoff; ties update as .5.
Elo may incorporate causally available earlier holdout results as an online
baseline. Candidate classifier parameters remain frozen throughout each test
block; same-week/simultaneous outcomes cannot enter model fitting.

The small logistic baseline uses venue, offensive/defensive eight-game EPA
differences, recent win-rate difference and rest difference. Expanded logistic
regression evaluates C=.1 and 1.0. The additional nonlinear candidate is the
existing sklearn Random Forest, 80 trees, depths 3/4, minimum leaf size 20,
max_features=.7, one worker and fixed seed 1729. Complexity is predeclared;
hyperparameters are chosen on tuning data only, never test results.

Default expanding windows use chronological **season/week groups**:

- At least 20 initial training groups, then 8 tuning groups.
- The next 8 calibration groups split into earlier fitting and later selection
  halves; neither is used for the base classifier fit.
- The next 8 groups are untouched test data. Advance 8 groups and refit on an
  expanded historical training window, with the same tuning/calibration lengths.
- Minimum samples: train 200, tune 50, total calibration 80 (at least 10 in each
  half), test 30; train/tune/calibration halves require both binary classes.
- All games in a season/week stay together. Overlapping adjacent group time
  ranges are rejected. Groups with labels unavailable by the next stage are
  purged. Early warmup and skipped-fold games are not assigned fake backtests.

Median imputation, scaling and estimator fitting use only base-training rows.
Raw probabilities, sigmoid/Platt calibration on log odds, and isotonic when all
folds have >=1000 fitting samples are compared. The stricter isotonic rule keeps
variants on identical evaluation games and avoids small-sample overfitting, as
recommended in [sklearn's calibration documentation](https://scikit-learn.org/stable/modules/calibration.html).
Calibration is retained only if it lowers log loss without worsening Brier on
the separate earlier selection period. The final test period never chooses a
calibrator, model complexity or activation flag; worsening final-test behavior
is reported, not hidden by retrospective reselection.

All eligible comparisons use the same test games. Reports contain log loss,
Brier, 50% accuracy, ROC-AUC only with both classes, ECE, reliability buckets,
confidence distribution, and breakdowns by season/week. Paired season/week block
bootstrap intervals compare each model with home prior and Elo (500 replicates,
at least five paired blocks). These are exploratory uncertainty measures, not
profitability evidence or automatic promotion criteria.

Ablations test basic strength, EPA, recent performance, QB and rest/home groups,
the full schema, and full-minus-QB/EPA/rest variants. Native tree impurity
importance is not treated as evidence of out-of-sample value. Historical production
comparison is unavailable without genuine archived probability observations;
independently defined favorite/underdog comparisons are likewise unavailable.

JSON artifacts store logistic coefficients or bounded forest node arrays,
training preprocessing, periods, seeds, versions and calibrator parameters.
Forest inference matches sklearn's float32 traversal. Loading verifies checksums,
paths, dimensions, finite values, tree bounds/cycles, schema and disabled flags.
No pickle/joblib deserialization occurs in this research workflow.

## Interface and reproduction

Use the existing environment and importer from the repository root:

```powershell
# Establish missing raw coverage using the existing source pipeline.
.\.venv\Scripts\python -m scripts.sync_nflverse --seasons 2023,2025 --datasets schedules,team_stats,player_stats,pbp --refresh --derive --keep-going
# Build the reproducible audit; fit/evaluate only if safe folds exist.
.\.venv\Scripts\python -m scripts.nfl_winner_research --seasons 2022,2023,2024,2025,2026 --lead-minutes 60
# Separate Class B reconstruction and evaluation; never merged with Class A.
.\.venv\Scripts\python -m scripts.nfl_winner_research --seasons 2022,2023,2024,2025,2026 --evidence-class B --publication-lag-hours 168 --completion-bound-hours 12 --output cache/nflverse/phase2_1/class_b
# Prepare compact, prospective features without fitting any model.
.\.venv\Scripts\python -m scripts.nfl_winner_research --seasons 2026 --snapshots-only
.\.venv\Scripts\python -m pytest tests/test_nfl_winner_research.py -q
```

The audit command exits **2** when infrastructure ran successfully but historical
evidence is insufficient. It still writes the coverage/dataset/evaluation files
and disabled run registration. `--database`, `--output`, `--min-games` and
`--lead-minutes` select explicit alternatives. `--prepare-upcoming` additionally
prepares snapshots after an audit; `--snapshots-only` is a separate maintenance
operation. No source download is triggered by these research commands.

Both `NFL_ADVANCED_RESEARCH_ENABLED=1` and `NFL_WINNER_RESEARCH_ENABLED=1` are
required for the separate backend research interface; defaults remain zero:

- `GET /api/nfl/research/winner/models`: registry and evaluation-run selection.
- `GET /api/nfl/research/winner/evaluations/<run_id>`: measured historical report.
- `GET /api/nfl/research/winner/<game_id>?model_id=<registered_id>`: prospective
  probability, home/away orientation, completeness, freshness and limitations.

No Class A model is currently registered, so no prospective candidate probability
can be served. Class B fold artifacts are retrospective only and cannot pass
the prospective loader. Merely enabling a route cannot train a model or enable production.
Historical `as_of` inference is rejected; regenerated backtests are separately
labeled and preserve their real generation timestamp. An artifact first generated
today cannot be presented as a live model available years ago.

Prospective inference accepts only real future games before the configured lead
cutoff, an already-generated registered artifact, and a fresh offline snapshot.
The request reads compact JSON and metadata, never full PBP or nflreadpy.
Snapshots default to one-hour expiry (`NFL_WINNER_SNAPSHOT_MAX_SECONDS`) and are
invalidated when schedule/PBP fingerprints change. Schedule/PBP receipts must
also be within 24/48 hours respectively. Model, feature and actual prediction
timestamps are returned separately. The existing ESPN fallback continues serving
the dashboard; it does not manufacture winner research inputs.

## Activation prerequisites

Accumulate prospective source observations or obtain a verifiable permitted
historical publication archive, then rerun this unchanged cutoff audit. Require
sufficient disjoint train/tune/calibration/test samples, stable feature coverage,
paired out-of-sample evidence against both simple priors, calibration that
generalizes, and an independent prospective review. Any future production
integration requires a separate explicit task. This implementation has no
automatic promotion path and makes no accuracy or financial-return claim.
