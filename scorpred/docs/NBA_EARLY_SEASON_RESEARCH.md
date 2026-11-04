# NBA early-season reliability study — 2026-10-22

The fixed historical/current-season blend produced a promising aggregate result on **189 early-season games**, but **does not justify removing the former production minimums**. On the 71 games below the former two-game winner-rule minimum, the improvement over a previous-season-only estimate was uncertain. Below the three-game scoring minimum, the blend was slightly worse on probability scoring rules. No production model was promoted by this study.

## Scope and frozen method

The protocol was frozen before evaluation at **2026-10-22T19:28:47 UTC** in [nba_early_season_protocol_v1.json](../data/validation/nba_early_season_protocol_v1.json). Its SHA-256 is recorded in the artifact manifest. Parameters were not selected by examining the evaluation results.

`nba-early-season-v1` reuses the existing `_calibrate_nba_projections` scoring formula, with explicit new input semantics rather than pretending that previous-season games satisfy the saved Ridge feature contract:

- Use observed previous-season regular-game scoring/conceding averages, requiring at least 20 observations when a historical contribution is needed. This is a research coverage requirement, not proof of reliability.
- Average up to six eligible current-season results. Current weight is `min(current sample / 6, 1)`; previous-season weight is the remainder. There are no invented or duplicated game rows.
- Use the existing 55% offense / 45% opposing defense formula, existing four-point home-court swing and score floor. Verified neutral venues get no home swing.
- Omit injuries, odds, lineups and opponent standings rather than fabricate their historical availability. Do not double-count the current form through the helper's additional form adjustment.
- Calibrate the existing normal-margin approximation using only earlier seasons' early-season residuals. Earlier residual quantiles provide separate 80% target team-score ranges. Regression coefficients are not fitted or retrained.
- Reject missing venue-neutrality metadata, insufficient history, stale sources/artifacts, future receipts, post-tipoff requests, incompatible artifacts, or calibration after the feature cutoff. The same 24-hour lag remains in force.

This is a separate research method. The installed Ridge model, its coefficients, and its same-season feature schema were not modified by this work. It would be invalid to feed this blend into that saved model silently.

## Data, chronology, and holdout

The evaluator reads only existing monthly archives from January 2022 through July 2025. It filters 4,395 completed regular-game observations for valid NBA identities and scores; no sports dataset was downloaded. It does not open ending-2026/2027 archive months. Season-ending 2023 supplies **96 development/calibration early-season games**. The chronological evaluation covers ending-2024 (**94 games**) and ending-2025 (**95 games**). Each evaluation season uses only earlier seasons for its probability/range calibration. Current-season features use only games before target tipoff minus 24 hours, with the target result inserted into history only after constructing its estimate.

The 189 observations are the games where either team has fewer than six eligible current-season results. This is not an evaluation of all historical NBA games, player projections, the saved Ridge model, or an exact reconstruction of the primary weighted winner engine. The latter lacks archived point-in-time standings and other inputs; its former two-game/three-game gates are compared as availability requirements only.

Ending-2026 is excluded from this experiment, but was already used in earlier ScorPred scoring research and comparisons. It must **not** be called a globally untouched holdout. Genuinely prospective validation requires predictions frozen before future tipoffs. Historical archive receipt times remain unverified, so chronological filtering does not establish historical publication-time availability or freedom from later source corrections.

Chronological future evaluation and separate probability calibration follow the principles described in [scikit-learn cross-validation guidance](https://scikit-learn.org/stable/modules/cross_validation.html) and [probability calibration guidance](https://scikit-learn.org/1.8/modules/calibration.html). These sources do not validate this specific ScorPred method.

## Results

Lower log loss, Brier score, and score MAE are better. Accuracy is winner classification on the same matched games, not a betting return or a confidence rating.

| Method | Games | Log loss | Brier | Winner accuracy | Points MAE per team |
| --- | ---: | ---: | ---: | ---: | ---: |
| Gradual season blend | 189 | 0.64255 | 0.22584 | 62.43% | 9.140 |
| Previous-season only | 189 | 0.66219 | 0.23491 | 58.20% | 9.332 |
| Home-prior baseline | 189 | 0.69293 | 0.24989 | 52.91% | — |

| Ending season | Blend log loss | Previous-season-only log loss | Home-prior log loss | Blend accuracy |
| --- | ---: | ---: | ---: | ---: |
| 2024 | 0.66614 | 0.67217 | 0.68212 | 59.57% |
| 2025 | 0.61920 | 0.65232 | 0.70364 | 65.26% |

The aggregate improvement has the same direction in both seasons. Estimated five-bin calibration error is 0.0471 for the blend, 0.0369 for previous-season only, and 0.0337 for the home prior. These small-sample summaries do not establish prospective calibration; the home prior's simpler forecast can have lower binned error while having worse log loss and discrimination. The blend's separate team-score range coverage was 79.37% home and 80.95% away against the 80% target. Simultaneous joint coverage is not asserted.

### Games affected by the former minimums

| Matched subgroup | Games | Blend log loss | Previous-season-only log loss | Blend / prior-only accuracy |
| --- | ---: | ---: | ---: | ---: |
| Either team below two eligible current games | 71 | 0.64504 | 0.65138 | 57.75% / 57.75% |
| Either team below three eligible current games | 101 | 0.65943 | 0.65410 | 58.42% / 58.42% |

The overall result includes 88 games with three to five current observations. It cannot be used as proof that the currently blocked zero/one/two-game cases improve. The exact two-observation subgroup contained only 30 games and its blend log loss was worse than prior-only. These subgroup diagnostics are reported transparently and were not used to tune a replacement method.

## Paired uncertainty

The fixed protocol uses 2,000 paired bootstrap resamples of seven-day calendar blocks, seed 20261022. Deltas are blend minus baseline; negative loss differences favor the blend.

| Comparison | Log-loss difference | Nominal 95% interval |
| --- | ---: | --- |
| All 189 vs previous-season only | −0.01965 | [−0.05367, −0.00408] |
| All 189 vs home prior | −0.05039 | [−0.10632, −0.02113] |
| Below-two subgroup vs previous-season only | −0.00633 | [−0.01366, +0.02351] |
| Below-three subgroup vs previous-season only | +0.00533 | [−0.01067, +0.03076] |

Only **six weekly blocks** support the aggregate interval, and four support each threshold subgroup. Long-range team dependence, the two-season sample, source vintages, and subgroup uncertainty limit the evidence. These nominal intervals are not convincing production certification. Accuracy improvement over prior-only is also uncertain: +4.23 percentage points, interval [−1.05, +10.94]. The report separately records favorable aggregate paired loss intervals and `statistically_convincing=false`.

## Implementation and operation

- Research engine: `services/nba_early_season.py`.
- Offline evaluator: `python -m scripts.evaluate_nba_early_season`.
- Metadata/results API: `/api/nba/research/early-season`; it reports actual study metrics without enabling a predictor.
- Optional forecast API: `/api/nba/research/early-season/matches/<id>?date=YYYY-MM-DD`. It returns 404 by default. `NBA_EARLY_SEASON_RESEARCH_ENABLED=0`; the local launcher explicitly keeps it disabled. No production decision path calls this research engine.
- NBA comparisons now include an expandable, clearly labeled research-study section. It does not request forecasts while previews are disabled. Optional previews, when explicitly enabled outside the default launcher, remain isolated and labeled experimental.
- Research artifacts, row-level forecasts, calibration, protocol/source hashes, and an integrity manifest live under `cache/nba_early_season/v1/`. Artifact loading checks both its manifest and the research/scoring-helper source fingerprint. Existing model artifacts are preserved.
- Explicit prospective capture: `python -m scripts.snapshot_nba_early_season --game-id <id> --date YYYY-MM-DD`. This command is a manual research action, does not activate the web predictor, preserves existing snapshots, and rejects post-tipoff collection. No background writer was started.

One immutable **Thunder–Nuggets (401909094)** research forecast and its exact context were captured at **2026-10-22T23:33:10.975268 UTC**, before its scheduled 01:30 UTC tipoff. Its original snapshot stays in the prospective directory with no selected-game outcome. A repeat preserved the file. Philadelphia–Cleveland was skipped after its 23:00 UTC tipoff; no retrospective prediction was recorded for it. This single future record is a starting point, not an evaluated prospective sample.

## Verification and concurrent-work boundary

The full backend run passed **847 tests** before the final snapshot immutability test was added; a subsequent focused run passed **16 early-season tests**, including that new case. The frontend run passed **42 tests** with two workers. Lint and production build passed; the existing deferred-3D chunk warning remains. The first frontend run had three timing failures during concurrent edits; its successful rerun is retained separately. All 160 protected data/model files retain their prior hashes.

The user confirmed another session is editing ScorPred. That session has independently changed NBA production prediction files, the NBA overview, and tests while this study ran. This study does not validate or promote those separate production changes. Its comparisons refer to the former minimums observed when the protocol was frozen. The study remains accessible within NBA Match Analysis and via its metadata API; the concurrently revised overview no longer embeds the comparison panel. Final app-wide regression and a single controlled backend reload should be owned by the session completing those production changes; a running backend fingerprint was observed behind the concurrently changing workspace.

Real-browser verification of the research section passed in Match Analysis at desktop and 390-pixel mobile widths, with no page exceptions or preview requests while research is disabled. [Desktop screenshot](assets/nba_early_season/nba-study-desktop.png), [mobile screenshot](assets/nba_early_season/nba-study-mobile.png).

Evidence and raw results: [report](assets/nba_early_season/report.json), [protocol](assets/nba_early_season/protocol.json), [verification](assets/nba_early_season/verification.json), [paired row-level results](assets/nba_early_season/predictions.json), [browser verification](assets/nba_early_season/browser.json).

## Exact next step

Keep this method fixed and disabled in production. Capture the remaining eligible upcoming early-season games before tipoff, with immutable inputs, source receipts, method/protocol hashes, and no outcome in the forecast record. Predeclare the prospective evaluation cohort before examining its results, then score that cohort with the same baselines and paired loss/calibration checks. Do not lower the current-season requirements based on the aggregate 189-game result, and do not use the currently reserved outcomes to tune this version.
