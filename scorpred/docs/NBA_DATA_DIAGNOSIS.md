# NBA data diagnosis

NBA has broad cached results and a separate validated scoring model, but the current winner recommendation system has no matching trustworthy historical or prospective accuracy estimate. Its primary output is weighted rule confidence. The optional fitted NBA winner classifier is absent.

## Historical and current coverage

The audit parsed 501 public response files: 335 summaries, 121 team schedules, 15 scoreboards, 29 rosters and one team directory. Older cached files are expected for historical responses; age alone is not proof that every current card is stale. No conflicting audited final scores, negative scores, self-matches or invalid UTC timestamps were found. Postponed events with `completed=false` are not final 0–0 ties.

| Ending year | Distinct live-cache events | Completed | Eligible monthly-archive games | Monthly-reference games absent from live cache |
|---|---:|---:|---:|---:|
| 2022 | Not represented | Not represented | 790, partial from January | 790 |
| 2023 | 1,174 | 1,173 | 1,314 | 141 |
| 2024 | 1,175 | 1,173 | 1,313 | 140 |
| 2025 | 1,178 | 1,173 | 1,315 | 142 |
| 2026 | 1,176 | 1,172 | 1,316 | 144 |
| 2027 | 1,265 | 67 preseason | Not in existing training archive | Unknown |

The 2027 response cache also has 1,198 scheduled regular-season events. Its 31 unique team IDs include a foreign preseason participant; this is not a claim that the NBA has 31 clubs. Monthly archives are the same ESPN source, not independent schedule truth. Lazy live caches need not include all archived playoff fixtures. Recent provider season-type 2 totals of 1,231 include Cup finals and require competition-aware filtering, not blanket duplicate removal. The 48 monthly files contain 6,541 raw events and 6,048 eligible games.

Three selected daily public NBA queries returned 18 events; all were cached and none disagreed on date/home/away identity. Full upcoming-season completeness remains unverified. The current source response cache contains no durable embedded receive metadata; filesystem timestamps cannot establish historical pregame availability.

## Category classification

| Category | Classification | Evidence and limitation |
|---|---|---|
| Historical results | Available but incomplete | Broad 2023–26 monthly coverage; 2022 partial; lazy live cache gaps |
| Upcoming schedule | Available but incomplete | 1,198 cached regular fixtures; three selected-date checks agree, whole universe not reconciled |
| Recent team form / home-away | Available but incomplete | Completed schedule and summary scores; prior-window calculations exist; full prospective snapshot coverage unverified |
| Offensive / defensive efficiency | Available but incomplete | Points-per-game proxies exist; possession-based efficiency not verified |
| Pace / possession counts | Not verified | No verified pace/off_rtg/def_rtg in actual derived team-stat return |
| Player season overview | Available but incomplete | Separate public overview adapter exists; no cached overview payloads found; historical receipt proof absent |
| Player logs feeding props | Unavailable in current legacy contract | Real numeric IDs return empty lists/None; mock-only path cannot satisfy numeric route |
| Starting lineups | Not verified | No dedicated verified pregame archive |
| Injuries / availability | Available but incomplete | 87 injury entries across 29 roster files, all tested injury dates present; game-specific prekickoff publication unknown |
| Minutes / rotations | Not verified | Not a demonstrated durable input to current winner method |
| Rest / back-to-back | Available but incomplete | Schedule-derived logic; current selected season is correct; immutable game-by-game inputs absent |
| Opponent strength | Available but incomplete | Standings/form proxy, not demonstrated historical point-in-time advanced efficiency |
| Roster changes / trades | Not verified | Rosters are snapshots, not an archived transaction/rotation history |
| Pregame source timestamps | Unavailable as durable evaluation proof | File cache age and ordinary tracking are insufficient |
| Source fallback / outage handling | Available but incomplete | Error/cache paths and health endpoint exist; latest refresh degraded; optional credentialed sources not tested |
| Winner recommendation performance | Not verified | No matching current-method chronological backtest or prospective scored archive |

NBA's official definition of [net rating](https://www.nba.com/stats/help/glossary?hidenav=true) is point differential per 100 possessions. Here the inspected `net_rtg` is a points-per-game difference, so its name does not establish that metric. Do not infer advanced feature availability from frontend or unused schema labels.

## Rule and player-data contracts

The primary eight-component NBA rule weights are recent form 39%, offense 14%, defense 14%, H2H 9%, venue 8%, rest 5%, injuries 4%, and opponent strength 7%. A logistic transformation of a weighted gap is not empirical probability calibration. The older five-component `nba_predictor` logic remains in the tree but should not be confused with the primary route's engine. Recommendation thresholds, disagreement/data penalties and trust grades are additional decision rules, not independent accuracy estimates.

Pure execution shows offense scores of 8.33 at 110 PPG and 10 at 115, 120 and 130 PPG. This compresses distinctions at high offense levels. Predictive harm is suspected, not measured. Empty injuries produce a perfect injury component of 10 versus 8.8 for one out point guard. Some upcoming-card contexts pass empty injury/H2H lists, so missing data can resemble healthy/neutral data; a verified league-wide health state is not established. The 505 cached roster player entries are not a proven complete active-player universe.

The props engine imports legacy `nba_client` player logs using numeric IDs. Those functions provide no real-ID logs in the tested contracts; their mock ID path does not pass the route's integer conversion. This is an unsupported data pathway, not evidence that fabricated mock player stats reached actual predictions. The props route also explicitly defaults to season 2024. A separate public player-overview path exists, but its stale-cache fallback has no age ceiling and wrappers label response update time without proving original source freshness. Historical player-prop performance is Unknown.

## Suspicions ruled out

An early source assignment `NBA_SEASON=2024` is overridden later. Both clients and a pure default-request probe use current ending year **2027**. This is not a general NBA stale-season defect; the props route's explicit 2024 default is a separate narrow problem. The current numeric score converter handles nested score dictionaries. Across 7,553 valid completed schedule references, **zero score pairs were lost**. Neither initial suspicion belongs in a confirmed bug list.

## Validation and freshness

The separate 11-feature points model reports 3,480 train /1,154 calibration /1,169 chronological test games, with test dates November 11, 2025–June 14, 2026. Test points MAE is **9.849864**, versus **10.487298** for the saved simple baseline; total-points MAE is 15.490664. Nominal 80% interval coverage is 80.33% home /77.50% away. These are retrospective score metrics, not validated winner probabilities. The old 1,000-event probe is not the source used by the newer monthly scoring trainer.

There are 35 ordinary NBA tracking rows, without enough source/model/kickoff provenance to establish pregame calibration or winner accuracy. At the later operational check, the last app NBA success was October 20 18:12:04 UTC, 524 minutes earlier; health was degraded. Public provider success earlier in the day does not negate this failure. Recovery/retry duration and loaded app version need independent operational validation.

The next NBA work should repair the real-ID player contract and stale semantics, preserve missingness, reconcile source identities/competition scope, and archive immutable current-method pregame predictions. Only then can chronological baseline comparisons and calibration establish whether the existing rules help. No new model family is justified by this audit.
