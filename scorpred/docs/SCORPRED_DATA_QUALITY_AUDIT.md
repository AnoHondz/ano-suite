# ScorPred data-quality audit

This report uses the October 20, 2026 data snapshot and the October 21 02:56 UTC operational recheck. See [system scope and reproducible commands](SCORPRED_SYSTEM_DIAGNOSTIC.md). Counts are observed records, not assumed complete provider universes. Model accuracy is separate from data quality.

## Measured coverage

| Dataset | Observed scope | Completeness / integrity evidence | Important limit |
|---|---|---|---|
| NFL schedules | 2022: 284; 2023–25: 285 each; 2026: 272 | 32 teams/season; no duplicate IDs or invalid nonsealed scores; 93 past scored 2026 games at initial snapshot | 109 reserved outcomes redacted in SQL; future placeholder scores are not completed games |
| NFL PBP | 284/285/285/285/93 game IDs for 2022–26 | All initially completed nonsealed schedules represented | Current-version availability does not prove historical pregame availability |
| NBA monthly archive | 6,048 eligible games, 2022–26 ending years | Deduplicated existing monthly source; complete 2023–26 regular schedules plus playoffs by provider classification | 2022 starts January; archive and live cache use the same provider |
| NBA response cache | 501 files; 2023–27 ending years | No audited final events missing scores, invalid UTC dates, negative scores, self-matches or conflicting records | Lazy response cache is not a complete fixture database |
| Soccer production training CSV | 7,888 fixtures, 130 teams, 2021-08-08–2026-04-19 | No duplicated fixtures or inconsistent result labels | All rows lack league, season and source-availability lineage |
| Soccer result cache | 1,348 distinct events across three leagues | 1,048 completed from 2025-start seasons, 212 completed +88 scheduled from 2026-start seasons; no conflicting final scores | Not a complete season archive |
| Soccer legacy processed CSV | 2,918 rows /2,599 unique fixtures | 319 duplicate excess rows, 10.93% | Separate from the clean 7,888-row classifier dataset |

NBA eligible archive total is 790+1,314+1,313+1,315+1,316 = **6,048**. Total raw monthly events are 6,541; preseason, nonstandard teams, and incomplete games are excluded by the existing training eligibility policy. Provider season-type 2 includes Cup finals in recent seasons, so 1,231 is not by itself a duplicate error.

The comparison to 380 EPL/LaLiga and 306 Bundesliga double round-robin capacity leaves six cached fixtures unrepresented for each previous season. This is a cache gap, not independent proof that the upstream provider omitted six fixtures. Three selected daily public probes returned 50 NBA/soccer events, all already in the audited cache, with zero date/home/away disagreements. This small check cannot establish season completeness or provider uptime.

## Integrity and missingness

`soccer_csv_integrity.json` covers 12 CSVs. `E0_2018.csv` is byte-identical to `E0_2025.csv`: all 319 rows are from 2025–26. The legacy concatenator propagates this duplicate season. The current scoring trainer explicitly excludes the mislabeled source; the clean classifier dataset has zero duplicate fixtures. `data/matches.csv` has 300 anonymous feature-only samples; fixture identity and fixture duplicate counts are Unknown.

NFL 2026 stored PBP has 16,380 rows: EPA and success each missing on 186 (1.14%), yards gained on 492 (3.00%). Passer ID absence on nonpassing plays is not a player-coverage failure. CPOE and no_play are not retained in this PBP projection; their 100% missingness does not establish upstream corruption. Six player-stat IDs and one roster GSIS ID are absent, with namespace/fallback handling; investigate linkage rather than fabricate identities. Injury designation is absent on 1,007/1,789 rows (56.29%), which can include practice-only reports. Publication time is not verified. New depth-chart column aliases and timestamp rows must not be confused with a broken old weekly schema.

Soccer's 60-column classifier has 13 constant/unsupported optional inputs: five market, five xG and three referee fields. The data supplies no varying training signal for them. NBA rule outputs use observed points-per-game summaries rather than verified possession-based efficiency; labeled advanced fields in the optional, absent winner-model schema are not evidence that those inputs exist.

Rosters: NBA has 29 cached roster payloads, 505 player entries and 87 injury entries; soccer has 31 rosters, 932 player entries and zero injury entries. Denominators are cached payloads, not a verified league-wide active roster universe. Zero cached injuries is not proof that every player is available. NBA's separate public player-overview adapter exists, but the legacy real-ID player-log contract feeding props returns empty data.

## Pregame evidence and leakage

NFL historical model development remains reconstructed Class B. Fifty-four of the expanded 100 research features are always masked, and three rest/bye/short-week fields are missing on 87.95% of 888 rows. Current EPA coverage cannot fill those historical inputs without availability proof. Frozen Elo/logistic forecasts have actual receipts and bound inputs; their training data remains Class B.

The registered frozen forecast path and sealed evaluation endpoint are guarded. However the generic 100-feature snapshot builder, invoked automatically by the collector, reads schedule history without the same reserved-outcome exclusion. Legacy NFL feature/training entry points also lack that guard. This is a confirmed **future access/contamination risk**, not evidence that the 109 future outcomes have been accessed. Remediation must respect the frozen study's source fingerprint; see [NFL diagnosis](NFL_DATA_DIAGNOSIS.md).

NBA/soccer response files and ordinary tracking do not demonstrate immutable pregame availability. The tracking export has 47 rows (35 NBA, 12 soccer; 28 completed, 19 pending), no duplicate tracking IDs, but no kickoff or model-version evidence sufficient for a prospective probability evaluation. No retrospective tracking win rate is represented as trustworthy accuracy. Source filesystem mtime is not an immutable receive receipt.

## Freshness and provider errors

Earlier public checks succeeded: all 12 bounded ESPN daily requests returned 200; NFL schedules/PBP/injuries metadata returned 200, while 2026 participation returned 404. The later PBP HEAD still returned 200 while the collector was retrying a ConnectionError. App health changed from 200/ok to 503/degraded with refresh success ages 524–539 minutes. This establishes operational degradation, not its complete cause or duration. No services were restarted to conceal it.

The first audit's unsupported ESPN date-range queries returned 400, and an incorrect local port returned 404. These are retained in `initial_probe_limitations.json` as diagnostic mistakes, not product outages. Corrected daily requests and port 5001 were used for conclusions. Credentialed provider availability is Not verified: audit-process keys were absent, and the running application's environment was not exposed.

## Comparison rubric

Scores measure audit evidence and controls on an ordinal 1–4 scale: **1** = measured material deficiency; **2** = useful but incomplete or reconstructed; **3** = broad coverage with explicit bounded gaps and working controls; **4** = independently reconciled completeness or durable prospective proof across relevant periods. **Unknown** = no adequate matching evaluation/denominator, not zero. These are not percentages or performance estimates and must not be averaged into overall accuracy.

| Dimension | NFL | NBA | Soccer |
|---|---:|---:|---:|
| Historical coverage | 3: five schedule/PBP seasons | 3: 2023–26 plus partial 2022 | 2: 7,888 unlabelled fixtures; raw season error |
| Current-season coverage | 3: 272 schedules /93 past PBP games | 2: preseason +scheduled cache; source-universe incomplete | 2: 212 completed /88 upcoming in three leagues |
| Data completeness | 2: auxiliary gaps, no in-season participation | 2: player/lineup and possession gaps | 1: lineage absent; xG/referee unsupported |
| Data freshness | 2: schedules current, PBP retry, old auxiliaries | 1: last app refresh 524 minutes | 1: last app refresh 539 minutes |
| Data accuracy | 3: schema/DB checks pass; aliases explained | 2: results pass; metric semantics incomplete | 1: raw season corruption and prop mappings |
| Pregame evidence | 2: actual archive, only one future fixture | 1: no equivalent durable receipt archive | 1: no equivalent durable receipt archive |
| Feature availability | 2: 54/100 masked historically | 2: rule inputs partly available; optional ML absent | 2: 47 varying of 60 classifier columns |
| Winner-model validation | 3: 385 chronological games, paired intervals | **Unknown**: current winner method unvalidated | 2: classifier replay; runtime blend Unknown |
| Winner calibration evidence | 2: historical bins/ECE, no prospective labels | **Unknown** | 2: pooled classifier only, no league validation |
| Operational reliability | 2: singleton/receipts good; failures +guard gap | 1: verified degraded refresh | 1: verified degraded refresh |

Machine-readable scores with supporting evidence are in `comparison_matrix.json`; findings are in `issue_register.json`. No sport currently qualifies for level 4. Some NBA/soccer source files changed after the original snapshot; their corrected disk behavior does not establish loaded runtime behavior or prospective reliability.
