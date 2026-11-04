# NFL data diagnosis

NFL has the strongest existing research controls, but no learned winner model has convincingly beaten Elo. Keep research disabled and preserve the 109-game sealed final holdout. The audit did not open or evaluate any reserved outcome.

## Source coverage and availability

| Season | Schedule games | Regular /postseason | PBP game IDs | Past scored games without PBP |
|---|---:|---|---:|---:|
| 2022 | 284 | 271 /13 | 284 | 0 |
| 2023 | 285 | 272 /13 | 285 | 0 |
| 2024 | 285 | 272 /13 | 285 | 0 |
| 2025 | 285 | 272 /13 | 285 | 0 |
| 2026 | 272 | 272 /0 | 93 | 0 at the initial snapshot |

All seasons contain 32 team identifiers and zero duplicate schedule IDs. No invalid scores or past missing results were found among allowed schedule projections. The 2022 271-game regular-season count is a source observation; assuming every year must have 272 would create a false missing-game diagnosis. At the initial October 20 snapshot, 2026 had 93 past scored games and 179 future games, including 109 reserved games. Future 0–0 placeholders do not count as results.

Current 2026 PBP: 16,380 plays, 186 missing EPA/success, 492 missing yards; no invalid tested binary/field-position values. Offensive/defensive EPA, success, passing/rushing efficiency, explosive-play, third-down and red-zone aggregations can be reconstructed from retained play columns. Their current existence does not establish historical publication time. The stored PBP projection omits CPOE and no_play, so comparisons requiring those columns need an explicit schema contract, not silent zero substitution. Prior-game defensive/opponent-strength features require both sides and a pregame cutoff.

Team stats cover 176 team-game rows /88 fixtures versus 93 initially completed PBP fixtures, leaving five not represented in that feed. Advanced team rows cover 80 fixtures; QB advanced rows total 187. Player stats have 6,131 rows, with six absent player IDs handled as unassigned identities rather than invented players. Snap counts have 7,463 rows with complete tested projected keys. Rosters have 3,043 entries and one missing GSIS ID; the broader player directory has 24,854 entries. The global team directory includes historical aliases, so its 36 rows are not evidence of 36 active clubs.

Injuries have 1,789 rows, 1,007 absent game-status designations; these may include practice reports. Source modification time is absent from the tested feed, so retrospective availability is unverified. Depth charts contain 668,543 temporal rows; 25,051 lack GSIS IDs (3.75%), but other IDs may support linkage. Their new `dt`, `team`, `pos_grp` and `pos_slot` schema must be interpreted instead of requiring obsolete weekly column names. Participation for 2026 is absent; the public asset HEAD returned 404.

The official [nflverse availability schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html) describes schedules updating frequently, overnight PBP and later corrections, daily rosters, and advanced/snap refresh cycles. It also explains that participation from 2023 onward is released after the postseason, and newer depth charts use timestamps. Therefore current participation absence is expected, while an old locally retained auxiliary feed is a separate collection question. Historical versions received in October are not observations from the historical kickoff date.

## Feature evidence

The canonical frozen-development dataset is `phase2_2/run_30fe98722d5901403574f017/dataset.json`, fingerprint `55774852dd69eb7d3c0945795febce780dfeec8b6c7588d6fe458214304d5f86`. It has 888 reconstructed games. An earlier Phase 2.1 draft has another fingerprint and must not silently substitute for it.

Of 100 expanded columns, 54 are always masked. Three rest/short-week/bye columns are missing on 87.95% of rows; the other 43 columns are present. Historical EPA, injury, QB/participation and other advanced availability cannot be inferred from current feed coverage. The retained small logistic model uses the existing compact feature definition; the frozen forecaster uses its registered three inputs and unchanged imputation/parameters. See the 280-entry inventory for every column and actual-use flag. Home advantage, rest, rolling form and strength-of-schedule calculations must use only prior games; no additional predictors were added.

## Actual winner validation

The existing chronological expansion evaluates **385 of 888 games**, across ten folds, two development seasons and 33 paired week blocks. Warmup, minimum training/season coverage and calibration periods are not test observations. Counting all 888 as evaluated would weaken the protocol. These results were read from preserved reports, not recomputed by fitting models.

| Method | Log loss | Binary Brier | Winner accuracy | ECE |
|---|---:|---:|---:|---:|
| Home prior | 0.692911 | 0.249856 | 54.03% | 0.039145 |
| Elo | **0.641521** | **0.225308** | 63.38% | 0.071037 |
| Small logistic | 0.644881 | 0.226703 | **64.42%** | 0.044323 |
| Random forest | 0.658181 | 0.232520 | 58.70% | 0.053789 |

Paired week-bootstrap 95% log-loss differences relative to Elo: logistic **[-0.024847, 0.032626]**; forest **[-0.012133, 0.044741]**. Both cross zero, as do the saved multiplicity-adjusted intervals. Logistic improves on the home-prior baseline but does not consistently improve on Elo. A higher point estimate of accuracy does not establish better probability forecasts. Seasonal/fold results vary; two seasons and 33 effective clusters cannot support broad consistency claims.

Established limits are restricted historical feature evidence, reconstructed source availability, and limited evaluation breadth. Overfitting, weak incremental signal and feature noise are possible explanations, not diagnosed causes. Data volume alone is not proof that a more complex model should win. A single pooled ECE is also not a prospective calibration guarantee.

## Frozen forecasting and holdout guard coverage

Two frozen model records, one study, one input batch and two genuine archived forecasts for one upcoming NE–CHI game were verified. The forecasts were archived October 20 at 16:47:51 UTC, about 55h27m before kickoff. Elo home probability is 0.520892; logistic is 0.508265. They have actual input receipts and checksum-bound inputs but no scored prospective outcome yet. They are not evidence of predictive success.

The holdout protocol SHA is `a4b906096d31dccbeceabdf4ffa1a275dccdd6a08206b2b636bac8026f202281`. It reserves 109 2026 regular-season games in weeks 12–18. The freeze deadline is November 26 at 00:00 UTC, before the first reserved kickoff at 01:00 UTC; explicit final review is no earlier than March 1, 2027. Model files, study and prior 83-file preservation baseline remain unchanged.

**Confirmed future risk:** the guarded registered forecaster is not the only NFL outcome reader. `services/nfl_winner_features.FeatureBuilder` loads schedule history without reserved-result exclusion, and `services/nfl_winner_research.prepare_snapshots` calls it for full-feature snapshots automatically in collector v2. `build_dataset` skips reserved targets but does not make every generic history reader safe. Legacy `services/nfl_features.build_features`, `nfl_model.train` and the NFL branch of `scripts/train_scoring_models` also lack equivalent seal guards. Once reserved games finish, these paths could decode or incorporate their results before the permitted review. No such breach is claimed now: the games are still future and this audit's SQL masked them.

The frozen study fingerprints existing source files, including collection/feature infrastructure. Patching pinned code without an explicit transition would invalidate v1 and cause fail-closed verification. The next implementation must design isolation/versioning first, preserve existing v1 artifacts and pair identities, then enforce common outcome-access guards at every applicable reader/entry point. Do not silently regenerate the study or retrain to make checks pass.

## Collector operational status

One Windows collector PID 7812 is running with singleton ownership, unchanged loaded writer SHA `73b6a17218275bee71f2148c6385a00ba8e00a60b493ee5f846eba6053e770d9`. Schedules have real attempted/received timestamps; latest inspected success was October 21 at 02:54:55 UTC. All scanned version observations have nonnull actual receive times. PBP is currently **failed/backing off with ConnectionError**. A later metadata HEAD returned 200; logs also show a GitHub name-resolution failure on a schedule attempt. Continuous collection reliability is therefore not established by one successful request. Auxiliary feeds remain largely from October 18, around 52 hours old by the final check, and the observed worker does not refresh all of them.

The forecaster's last check succeeded, with no due games and no missed forecasts. Existing archive triggers/checksums and database quick_check/foreign_key_check pass. Research remains disabled by default; production recent-score scenarios are unchanged. Exact next action: implement the reviewed holdout-safe reader/version boundary and restore monitored source refresh, then continue frozen prospective collection without evaluating the reserved outcomes.
