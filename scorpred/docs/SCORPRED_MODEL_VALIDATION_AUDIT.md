# ScorPred model validation audit

This audit reuses fitted artifacts and preserved research outputs. It does not train or recalibrate models, inspect sealed NFL outcomes, or change production predictions. A model's label, output type and validation scope must agree before an accuracy claim is accepted.

## Method and claim inventory

| Method | Actual output | Source and preprocessing | Matching evaluation / verdict |
|---|---|---|---|
| NFL production recent-score engine | Descriptive scenario shares | Same-season prior scoring pairs, minimum sample and availability lag | Not a calibrated win probability; cannot justify a betting accuracy claim |
| NFL Elo /small logistic /forest research | Binary home-win probabilities | Canonical 888 reconstructed rows; chronological fit/cal/test policy; restricted availability and imputation | 385 evaluated games; no convincing incremental log-loss improvement over Elo |
| Frozen NFL Elo /logistic v1 | Registered binary probabilities | Three bound pregame inputs, frozen parameters and versioned source receipts | Two forecasts /one upcoming game, no scored prospective outcome |
| Legacy NFL winner artifact | Binary classifier probabilities | Older retrospective feature pipeline | 196-game report, 61.22% accuracy /0.678742 log loss; different window and provenance, not comparable to 385-game result |
| Soccer calibrated classifier | Home/draw/away probabilities | 60 columns /47 varying; previous-result chronology and Elo; fitted imputation/calibration | 1,518 existing test games replayed; baseline improvement on reconstructed data |
| Soccer rules +ScorMastermind | Blended probabilities, transformed confidence, decision categories | Rule features, classifier, policy weights, agreement/data penalties; old profile/report can influence selection | Current blend accuracy, log loss and calibration **Unknown** |
| NBA primary rules +ScorMastermind | Rule confidence, implied transformed scores and decisions | Eight weighted components from points/form/venue/rest/etc; optional ML artifact absent | Current winner accuracy and calibration **Unknown** |
| Legacy NBA predictor | Five-component heuristic | Older rule path; not the primary route's winner engine | No matching current production claim justified |
| Soccer /NBA /NFL scoring artifacts | Expected goals/points, margin/total, interval | Existing 11 /11 /19 feature schemas; separate score training and reports | Retrospective MAE/interval evidence, not win-probability calibration |
| Soccer and NBA props | Sample/threshold player-stat estimates | Separate statistic mappings and player-log sources | Semantic/availability contracts unresolved; prospective performance **Unknown** |
| MatchBrain /DecisionEngine /policy /mistake adjustments | Grades, edge/trust, BET/LEAN/SKIP | Derived model/rule outputs plus thresholds and source-quality rules | Not independently calibrated probabilities; current decision-strategy performance **Unknown** |

The complete [feature CSV](../cache/diagnostics/2026-10-20_multisport/feature_inventory.csv) traces 280 model/engine entries to source, required fields, calculation, use and evidence. The optional NBA winner schema's 25 fields do not establish a fitted model or actual live metric availability. Rule scores, source coverage, predicted probability, confidence and final selection are not interchangeable.

## NFL probability evidence

| Method | Games | Log loss | Brier | Accuracy | ECE |
|---|---:|---:|---:|---:|---:|
| Home prior | 385 | 0.692911 | 0.249856 | 54.03% | 0.039145 |
| Elo | 385 | 0.641521 | 0.225308 | 63.38% | 0.071037 |
| Logistic | 385 | 0.644881 | 0.226703 | 64.42% | 0.044323 |
| Forest | 385 | 0.658181 | 0.232520 | 58.70% | 0.053789 |

Paired week-block log-loss differences use the same games, accounting for shared weekly variation. Logistic–Elo [-0.024847,0.032626] and forest–Elo [-0.012133,0.044741] both include zero; adjusted intervals also do. Neither learned model consistently improves probability quality. Folds cover two seasons /33 week blocks, not 385 fully independent replications. More tested rows cannot be manufactured by moving warmup/calibration rows into evaluation. Fifty-four advanced columns are correctly masked where historical availability is unproved.

No cause of the forest/logistic result is identified solely from this comparison. Sample power, restricted signals, feature noise and model complexity remain separable hypotheses. The sealed 109-game holdout is still untouched and may only be used under its original protocol. A generic reader/legacy training guard gap must be addressed before those games can contaminate unrelated snapshots; the frozen v1 source fingerprint constrains how that fix is versioned.

## Soccer replay and uncertainty

Artifact SHA `be294838aad6273456d754ae59679a8e8a75e313f3de9ab42fc07d217dedd88b`; source SHA `b713fa97bc8e3c189dfb6c0196f334386256fbb81fa31d366b0c00384121e07b`. No fit calls were made. Exact chronological windows and predictions reproduce saved classifier metrics within 3.93e-7.

| Predictor | Games | Log loss | Mean-class Brier | Accuracy | Top-label ECE |
|---|---:|---:|---:|---:|---:|
| Existing calibrated classifier | 1,518 | 1.002599 | 0.199535 | 52.04% | 0.021563 |
| Fixed training class prior | 1,518 | 1.072552 | 0.216319 | 43.68% | 0.005972 |

The train-prior argmax is home. Its lower ECE does not make its discriminatory power or log loss better. The multiclass Brier convention here averages squared error across three classes; summing gives classifier Brier 0.598604. Binary NFL and mean-three-class soccer Brier are not directly comparable.

An exploratory 2,000-replicate paired week bootstrap over 33 clusters gives log-loss difference -0.069953, interval [-0.088420,-0.051082]. This post-hoc interval on a previously evaluated development period is not an untouched final test, a comparison against Elo, or validation of the live blend. No historical league IDs exist, so league-specific performance is Unknown. The test includes 29 games in start-year 2024 and 1,489 in 2025; stable performance across many seasons is not established.

The classifier never selects draw by argmax, but its mean draw probability and pooled classwise calibration are reasonable on this window. Report zero draw recall alongside probability scores; do not replace this with a blanket claim that draw probability is invalid. Evaluate draw decisions and classwise calibration prospectively before changing thresholds.

Original source-context mismatch sensitivity affected 39 selections and probabilities by up to 12.88 points, but had mixed aggregate score effects. Source changes outside the audit now appear to fix the ten-match/target-date context. Matching route-level parity and a loaded-runtime identity check are required. Neither original nor corrected runtime blend accuracy is established by the standalone classifier replay.

An older April walk-forward report covers 3,790 test rows and 47 features; it belongs to a different artifact/schema than the October 60-column classifier. It cannot validate the current selector, confidence policy or blended predictions merely because the file is loaded as a profile.

## Existing scoring evidence

| Sport | Test games | Points/goals MAE | Saved baseline MAE | Total MAE | Nominal 80% interval coverage home /away |
|---|---:|---:|---:|---:|---|
| Soccer | 480 | 0.900194 | 0.941877 | 1.256643 | 83.33% /87.92% |
| NBA | 1,169 | 9.849864 | 10.487298 | 15.490664 | 80.33% /77.50% |
| NFL legacy | 196 | 7.466682 | 7.820087 | 10.880424 | 87.76% /82.14% |

These are existing report metrics, not retrained or independent prospective results. Soccer scoring uses previously retained EPL files; this is not all-league scoring validation, and new source acquisition must resolve the current terms. Historical licensing compliance was not established by this audit. Exact report values are included in the machine-readable summary. Score models have separate source and competition filters. Marginal interval coverage does not establish joint exact-score probability or winner calibration. NFL legacy scoring also needs the sealed-access guard review before any future training invocation.

## Leakage controls and unmeasurable claims

Verified current controls include chronological soccer feature construction and splits, registered NFL source cutoffs, archive checksums and immutable pairs, and UI withdrawal at kickoff/expiry. Retrospectively revised source data still makes historical results Class B. NFL holdout SQL redaction happened before Python decoding in this audit. No reserved outcome was inspected.

NBA/soccer tracking has 47 rows, with no adequate kickoff/model/source binding. This cannot establish a trustworthy prospective denominator, log loss, calibration or ROI. Unknown winner accuracy must remain Unknown. Source-missingness defaults, player-data gaps and rule saturation have observed mechanisms; their impact on current live predictive accuracy has not been causally measured.

The next valid experiment freezes the corrected existing input contract, artifact hashes and policy, then archives timestamped source receipts and complete pregame probability vectors. Evaluate fixed chronological or genuinely prospective windows against train-only priors and the existing appropriate Elo/rule baselines. Use paired game/week or matchweek uncertainty, season/league breakdowns, calibration bins with counts and a separately untouched final period. Do not reuse the sealed NFL holdout for exploratory design choices.
