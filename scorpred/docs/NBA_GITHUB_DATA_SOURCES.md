# NBA GitHub sources: promising archives, unresolved use gates

Observed October 22,2026 UTC / October 21 Eastern. No NBA historical assets were downloaded, no upstream NBA API requests were made for this research, and no source was enabled. Repository documents, endpoint code as text, and release metadata were inspected.

## What already exists and what actually needs expansion

ScorPred's prior audit found **6,048 eligible games** in 48 monthly ESPN responses:790 partial 2022, then 1,314/1,313/1,315/1,316 in ending years 2023–26. The 501-file live-response audit was a lazy cache with schedules, summaries, rosters and directory data; it was not another complete fixture database. These are October 20 snapshot counts. [Existing inventory and audit references](assets/github_data_research/existing_inventory.json), [NBA diagnosis](NBA_DATA_DIAGNOSIS.md).

Current adapters already supply schedules, results, recent scores/form, home-away context, rest and some rosters/injuries. They do not demonstrate a complete possession-based efficiency table, historical player-minute logs, pregame announced lineups or immutable injury/transaction availability. The real-ID props source remains unsupported; descriptive player season averages cannot manufacture those logs. The Stage 1 stale/default/null repairs and the later machine-status fix are preserved.

The current winner path is weighted rules, not a fitted NBA winner classifier. Its matching winner validation remains unknown. The separate scoring model's 1,169-game score test does not establish winner accuracy or justify adding new predictor families.

## Technical candidates and verified freshness

| Candidate family | Verified observation | Additional value and overlap |
|---|---|---|
| [SportsDataverse ESPN release store](https://github.com/sportsdataverse/sportsdataverse-data), produced by [hoopR-nba-data](https://github.com/sportsdataverse/hoopR-nba-data) | Inspected schedule/PBP/player-box assets include ending years 2002–2027. 2027 schedule Parquet updated 2026-10-21 11:30:57; PBP11:29:41; player boxes 11:30:16 UTC | Up to 20 earlier ending-year seasons relative to the local 2022–26 archive; per-game player/team detail. Net games unknown. Existing ESPN IDs ease joins, but this is the same upstream family as current caches |
| [NBA Stats producer](https://github.com/sportsdataverse/hoopR-nba-stats-data) | Documentation lists traditional player/team boxes 1997–2026,30 ending-year seasons; newest publication claimed October 1. Producer code was recently changed; the hub still describes this lane as dormant | Potential 25 earlier seasons; official NBA IDs, counts and minutes. Recent code is not proof the current-season pipeline runs. Verify actual asset/status before relying on it |
| [Shufinskiy archive](https://github.com/shufinskiy/nba_data) | README says 1996/97–2024/25; inspected file index also advertises 2025-start keys. Exact latest dataset content update not established | Older PBP/shots/possession variants. Per-type season horizons differ; neither README nor a filename proves complete 2025/26 coverage |
| [nba_api](https://github.com/swar/nba_api) | Current code exposes official NBA Stats/live endpoints and bundled static IDs; MIT client | Schema and potential client only. Not a hosted independently licensed historical dataset |
| [hoopR](https://github.com/sportsdataverse/hoopR) | Current R client/bulk loaders; code maintenance and actual release freshness recorded separately | Useful documentation for tags, not a reason to install an R collection stack in ScorPred |

The [asset manifest](assets/github_data_research/release_manifests.json) records actual update dates separately from tag creation. A current asset may have few early-season games; file availability is not season completeness. Some legacy hoopR functions still reference the archived [hoopR-data](https://github.com/sportsdataverse/hoopR-data); use the current release mapping, not an old URL copied from an example.

## Rights, automation and redistribution

**No NBA candidate receives an unconditional ScorPred production recommendation.** This is a decision about the presently verified evidence, not a claim that all statistical analysis is legally prohibited.

NBA terms restrict gambling, commercial reuse and comprehensive statistical databases; ScorPred needs affirmative permission before expanding this source. [NBA terms, §9](https://www.nba.com/termsofuse).

Disney's terms explicitly include ESPN and restrict automated extraction/data-set building and business-related use without permission. A public JSON endpoint's technical availability is not a documented collection entitlement. We did not perform new provider scraping. [Disney terms, §2B](https://disneytermsofuse.com/english/).

The [ESPN producer licence](https://github.com/sportsdataverse/hoopR-nba-data/blob/main/LICENSE.md) and [NBA Stats producer licence](https://github.com/sportsdataverse/hoopR-nba-stats-data/blob/main/LICENSE.md) expressly say CC-BY4.0 for repository contents. The release hub's README labels data CC-BY4.0, but its root LICENSE is MIT software text. Retain that discrepancy and resolve applicable dataset-specific rights, rather than selecting whichever label looks easiest. A repository licence cannot be assumed to establish every underlying right. The Apache 2.0 archive and MIT clients/parsers likewise do not grant upstream NBA data rights by themselves.

GitHub host limits apply to metadata/download operations. No published authorized numeric NBA/ESPN request quota was verified. nba_api documents throttling and cloud-network timeouts; wrapper retry examples are not permission. Do not use rotating proxies, spoofing or retries to evade access restrictions. Proposed collection and public redistribution both stay gated until an affirmative, purpose-specific permitted-use record exists.

## Statistic and identity feasibility

| Requested category | Feasible evidence source | Required qualification |
|---|---|---|
| Player/team boxes, minutes | ESPN hosted boxes or documented NBA Stats traditional boxes | Verify actual columns, game/player identity, minute format, DNP nulls and paired team totals |
| Offensive/defensive ratings, pace, eFG, TS, rebound/turnover measures | [AdvancedV3 expected fields](https://github.com/swar/nba_api/blob/master/src/nba_api/stats/endpoints/boxscoreadvancedv3.py); alternatively named estimates from permitted traditional counts | An endpoint schema proves a field contract exists, not its current availability, historical completeness or collection rights. Keep estimated/reported definitions separate |
| Possession-level PBP | ESPN/NBA PBP plus [pbpstats](https://github.com/dblackrun/pbpstats) reconstruction | Same event may be reordered/corrected; distinguish counted possessions from boxscore estimates and per 100 ratings from PPG |
| Actual lineups/stints | Postgame game rosters/PBP/substitutions | Actual starters are not pregame announcements; injuries and intended lineups still need original evidence |
| Rest, back-to-backs, venue and opponent strength | Existing schedules plus reconciled permitted history | No separate feed necessary; prior games only, Eastern calendar and competition scope preserved |
| Injury/availability and roster changes | Existing snapshots are incomplete; no complete verified GitHub pregame archive found | Unknown is not healthy; current roster cannot establish historical trades or availability |

Official NBA game IDs must stay 10-character strings with leading zeros; team IDs in the 161061xxxx namespace do not equal ESPN team IDs. Source season type and ending-year conversion need explicit tests. The NBA Stats producer documents an August 2026 switch to ending-year release names; never infer a start year from an old filename example. [Player-box schema and coverage table](https://github.com/sportsdataverse/hoopR-nba-stats-data/blob/main/docs/datasets/player_boxscores.md), [team-box schema](https://github.com/sportsdataverse/hoopR-nba-stats-data/blob/main/docs/datasets/team_boxscores.md).

The producer's game counts include a broader known competition universe than ScorPred's eligibility filter. Its 2026 table reports 1,322 built against 1,400 known; this does not mean 78 missing regular-season games. Separate preseason, regular season, Cup finals, play-in, playoffs, All-Star and international records before comparing denominators. Similarly, the existing 1,231 provider regular-type observation is not automatically a duplicate.

## Point-in-time dataset design

Historical reconstruction is feasible **conditionally as ClassB**: freeze a permitted source version, reconcile games/IDs, use only prior completed games, exclude target-game boxes and final season aggregates, apply an explicit publication-lag assumption, and retain missing availability as unknown. Source revision and lack of original receipt proof remain limitations.

It is not possible to create genuinely historical ClassA inputs by setting today's download time to an old game date. Nor can a new dataset validate ScorPred's old forecasts that were never timestamped/bound. For ClassA, archive future actual receipts, kickoff/status, exact current-rule inputs/policy/model identity and complete probability vector before tipoff. Announced lineup/injury claims need their own permitted source and timestamps.

Use broader boxes first to assess identity, missingness and reconstruction feasibility. No new predictor, model fitting or production promotion is justified in this phase. NBA-001 and VAL-001 acceptance should precede a winner-baseline comparison.

## Ranked conditional shortlist

1. **SportsDataverse ESPN release family** (producer 80/100, host 79): best identifier/coverage fit; host and producer count as one candidate family. First resolve release-specific rights.
2. **SportsDataverse NBA Stats family** (70): deeper documented traditional boxes and potential efficiency context, but source rights, release freshness and namespace reconciliation are open gates.
3. **Shufinskiy archive** (62): historical alternative with source-rights/horizon caveats; not a separate source of pregame injuries or current-season truth.

nba_api scores 72 as tooling, but it is not ranked as another hosted dataset. pbpstats' last verified code-path maintenance is April 2024 and it is a parser, not a free live availability feed. SkillCorner's maintained basketball sample is **Spanish ACB, not NBA**; it adds zero NBA games and is rejected for this task's coverage target. No third unconditional NBA source is invented.

## Gap matrix

All new source options with unresolved rights/provenance stay in that classification even when technical depth is attractive. [Full NBA CSV](assets/github_data_research/nba_gaps.csv).

| Dataset | Classification | Existing scope / proposed measurable change |
|---|---|---|
| Historical schedule/results universe | Available but licensing or provenance is uncertain | 6,048 eligible monthly ESPN games, ending 2022–2026;2022 partial; Reconcile 2002–2027 release manifests first;20 earlier ending-year seasons potentially available, net games unknown |
| Player box scores /minutes | Available but licensing or provenance is uncertain | Legacy real-ID props logs empty; public overviews descriptive only; Verify per-game real-player IDs, minutes and required count components; do not infer logs from averages |
| Team box scores /shooting /rebounds /turnovers | Available but licensing or provenance is uncertain | Score/form available, no complete possession-based boxscore table; Measure fraction of historical games with both teams'FGA/3 PM/FTA/OREB/DREB/TOV/minutes |
| Play-by-play | Available but licensing or provenance is uncertain | Not a durable NBA event store; Inspect 2002+ESPNrelease schema and same-game completeness; qualify provider event semantics |
| Possessions /pace /offensive and defensive ratings | Available but licensing or provenance is uncertain | PPG proxies are not verified possessions or efficiency; Possession definition and overtime-aware minute contract first; compare reconstructed vs published counts |
| eFG /TS /rebound and turnover percentages | Available but licensing or provenance is uncertain | Not demonstrated durable current inputs; Derive only named versioned formula families from verified prior-game denominators; never silently substitute official fields |
| Postgame actual starting lineups /on-court stints | Available but licensing or provenance is uncertain | No validated lineup archive; Verify actual lineup/minute coverage after permission; keep actual and announced lineups separate |
| Historical pregame announced lineups | Unavailable from verified free sources | No receipt-backed announcement ledger; Record permitted prospective announcements with actual receipt and confirmation status |
| Injury /availability and roster changes | Already available but incomplete | Audited 87 injury entries/29 roster payloads; not league-wide historical publication archive; Distinguish unknown from healthy; validate effective dates and source publication; enumerate coverage |
| Rest days /back-to-backs /home-away form | Already available but incomplete | Existing schedule-derived logic and season checks; Add only games demonstrably missing; prove timezone/cutoff parity on identical histories |
| Timestamped pregame probability/input archive | Unavailable from verified free sources | Ordinary tracking is insufficient;35 audited NBA rows lack required binding; Add immutable receipts and exact current-rule policy/input identity; compare existing methods only later |
| Broadcast tracking sample | Available but licensing or provenance is uncertain | No NBA tracking integration; Zero NBA games: sample is Spanish ACB; reject for requested NBA gap |

## Complete candidate assessments

<!-- SOURCE_PROFILES -->

### [sportsdataverse/hoopR](https://github.com/sportsdataverse/hoopR) — candidate documentation/client; no R installation needed

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | sportsdataverse; nba; client |
| Canonical location | https://github.com/sportsdataverse/hoopR |
| Datasets | R NBA/ESPN PBP, boxscore and bulk loaders; other providers out of scope |
| Seasons /competitions | NBA loaders document 2002+ ending years; no single shared earliest year for every field |
| Latest verified dataset update | 2026-10-21T11:30:26Z; ESPN player-box release asset update; same source family |
| Latest verified code maintenance | 2026-10-18T22:05:09Z; path commit |
| Repository push /archival status | 2026-10-21T14:39:39Z; archived=False (not dataset freshness) |
| Formats /access | R client/data frames; CSV/Parquet/RDS bulk releases and JSON APIs; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | MIT code; underlying data and provider rights separate |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Current upstream code does not prove source collection permission or same-day pipeline health |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | ESPN and NBA Stats identities differ; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | medium |
| Expected improvement | Identify release tags/columns; Python can consume permitted Parquet directly |
| Score /bounded suitability | 74.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/sportsdataverse/hoopR), [evidence2](https://raw.githubusercontent.com/sportsdataverse/hoopR/main/README.md), [evidence3](https://raw.githubusercontent.com/sportsdataverse/hoopR/main/LICENSE), [evidence4](https://api.github.com/repos/sportsdataverse/sportsdataverse-data/releases/tags/espn_nba_player_boxscores), [evidence5](https://github.com/sportsdataverse/hoopR/commit/aff3220d615dad1088a4984c955e9b84e4b39fa8), [evidence6](https://github.com/sportsdataverse/hoopR/blob/main/DESCRIPTION), [evidence7](https://github.com/sportsdataverse/hoopR/blob/main/R/load_nba.R). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [sportsdataverse/sportsdataverse-data](https://github.com/sportsdataverse/sportsdataverse-data) — priority conditional candidate; permission gate before sports-data import

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | sportsdataverse; nba; data |
| Canonical location | https://github.com/sportsdataverse/sportsdataverse-data |
| Datasets | Central ESPN NBA releases: schedules, PBP, team/player boxes, game rosters; separate NBA Stats tags |
| Seasons /competitions | Inspected ESPN release asset names 2002–2027 ending years; per-tag manifest is authoritative, no uniform completeness assumption |
| Latest verified dataset update | 2026-10-21T11:30:26Z; ESPN player-box release asset update; same source family |
| Latest verified code maintenance | 2026-06-03T02:51:35Z; path commit |
| Repository push /archival status | 2026-10-12T10:07:29Z; archived=False (not dataset freshness) |
| Formats /access | CSV/Parquet/RDS; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | README labels data CC-BY4.0 while root LICENSE is MIT software text; producer LICENSE.md says CC-BY4.0. Upstream rights/redistribution for ScorPred remain unverified. |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Mutable files; publication timestamps mostly postgame. Host/producer licence discrepancy and source permission remain gates. |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | ESPN game/player/team IDs align most closely with current adapters; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | medium |
| Expected improvement | Potential 20 earlier ending-year seasons versus 2022–26 local scoring archive; player/team boxes and historical ESPN IDs. Same ESPN lineage, not an independent schedule truth. |
| Score /bounded suitability | 79.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/sportsdataverse/sportsdataverse-data), [evidence2](https://raw.githubusercontent.com/sportsdataverse/sportsdataverse-data/main/README.md), [evidence3](https://raw.githubusercontent.com/sportsdataverse/sportsdataverse-data/main/LICENSE), [evidence4](https://api.github.com/repos/sportsdataverse/sportsdataverse-data/releases/tags/espn_nba_player_boxscores), [evidence5](https://github.com/sportsdataverse/sportsdataverse-data/commit/cbbe83221e1458631bffd595d4934bc3d54c6d8b). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [sportsdataverse/hoopR-data](https://github.com/sportsdataverse/hoopR-data) — reject as current feed; archive reference only

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | sportsdataverse; nba; archive |
| Canonical location | https://github.com/sportsdataverse/hoopR-data |
| Datasets | Legacy NBA game index and older PBP data |
| Seasons /competitions | Historical legacy archive; precise row scope not verified |
| Latest verified dataset update | Not independently verified; No independent dataset update established; see source notes |
| Latest verified code maintenance | 2021-05-18T12:05:40Z; path commit |
| Repository push /archival status | 2023-04-05T18:37:27Z; archived=True (not dataset freshness) |
| Formats /access | See dataset and access notes; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | No root licence verified in inspected files |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | GitHub archived=true; last push 2023-04-05. Some old loader links still point here. |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | medium |
| Expected improvement | No advantage over the maintained release host demonstrated |
| Score /bounded suitability | 33.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/sportsdataverse/hoopR-data), [evidence2](https://raw.githubusercontent.com/sportsdataverse/hoopR-data/main/README.md), [evidence3](https://github.com/sportsdataverse/hoopR-data/commit/df137330f844d31b61d27c49be059f6dbe78623b). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [sportsdataverse/hoopR-nba-data](https://github.com/sportsdataverse/hoopR-nba-data) — best producer to investigate behind hosted-release candidate

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | sportsdataverse; nba; pipeline |
| Canonical location | https://github.com/sportsdataverse/hoopR-nba-data |
| Datasets | Current ESPN NBA release producer: schedules, PBP, boxes, rosters, crosswalks, shots |
| Seasons /competitions | Declared 2002–present; release assets independently checked through 2027 ending year |
| Latest verified dataset update | 2026-10-21T11:30:26Z; ESPN player-box release asset update; same source family |
| Latest verified code maintenance | 2026-10-18T10:01:02Z; path commit |
| Repository push /archival status | 2026-10-21T15:31:22Z; archived=False (not dataset freshness) |
| Formats /access | Python/R producer code; CSV/Parquet/RDS release outputs; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | LICENSE.md expressly CC-BY4.0 for data/code/docs; root LICENSE stub and release-store MIT text do not settle upstream rights |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Do not count this producer and its release store as independent datasets; no original pregame injury/lineup ledger |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | ESPN IDs; producer publishes crosswalks needing audit; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | medium |
| Expected improvement | Historical boxscore/minute coverage with existing ESPN IDs; use releases rather than replicate scraping pipeline |
| Score /bounded suitability | 80.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/sportsdataverse/hoopR-nba-data), [evidence2](https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/README.md), [evidence3](https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/LICENSE), [evidence4](https://api.github.com/repos/sportsdataverse/sportsdataverse-data/releases/tags/espn_nba_player_boxscores), [evidence5](https://github.com/sportsdataverse/hoopR-nba-data/commit/4c0fe34867ec6b41a0950567343670c860ba1df7), [evidence6](https://github.com/sportsdataverse/hoopR-nba-data/blob/main/LICENSE.md). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [sportsdataverse/hoopR-nba-stats-data](https://github.com/sportsdataverse/hoopR-nba-stats-data) — conditional historical candidate; current refresh not established

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | sportsdataverse; nba; pipeline |
| Canonical location | https://github.com/sportsdataverse/hoopR-nba-stats-data |
| Datasets | NBA Stats boxes, schedules, possession/lineup and advanced-data processing |
| Seasons /competitions | Documented traditional player/team boxes 1997–2026 ending years,30 seasons; other releases vary |
| Latest verified dataset update | 2026-10-01; Producer documentation claims boxscore publication; independent release timestamp not fetched |
| Latest verified code maintenance | 2026-10-04T14:22:41Z; path commit |
| Repository push /archival status | 2026-10-21T14:18:50Z; archived=False (not dataset freshness) |
| Formats /access | Python/R producer code; CSV/Parquet/RDS release outputs; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | Producer LICENSE.md CC-BY4.0; NBA source terms need affirmative resolution for proposed use |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Release-hub dormant notice conflicts with recent code/docs publication. Documentation says 2026-10-01 boxes; do not assume live readiness. Some 2026 games absent in producer coverage table. |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | NBA game_id string preserves leading zeros;161061xxxx teams/person_id distinct from ESPN; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | high |
| Expected improvement | Potential 25 earlier seasons versus existing archive; official numeric IDs and shot/rebound/turnover counts for defined efficiency metrics |
| Score /bounded suitability | 70.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/sportsdataverse/hoopR-nba-stats-data), [evidence2](https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-stats-data/main/README.md), [evidence3](https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-stats-data/main/LICENSE), [evidence4](https://github.com/sportsdataverse/hoopR-nba-stats-data/blob/main/docs/datasets/player_boxscores.md), [evidence5](https://github.com/sportsdataverse/hoopR-nba-stats-data/commit/07d8b725099efedf0ec15c3947948dce09e49be2), [evidence6](https://github.com/sportsdataverse/hoopR-nba-stats-data/blob/main/LICENSE.md). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [swar/nba_api](https://github.com/swar/nba_api) — conditional schema/client reference; direct collection blocked pending permission

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | swar; nba; client |
| Canonical location | https://github.com/swar/nba_api |
| Datasets | NBA Stats and live API schema/client; player/team static IDs; AdvancedV3 and player logs |
| Seasons /competitions | Endpoint-specific history; no downloaded historical dataset and no universal support range |
| Latest verified dataset update | Not independently verified; No independent dataset update established; see source notes |
| Latest verified code maintenance | 2026-10-22T01:37:32Z; path commit |
| Repository push /archival status | 2026-10-22T01:37:36Z; archived=False (not dataset freshness) |
| Formats /access | Python client; JSON API responses; bundled reference records; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | MIT client only; no NBA data-collection/reuse grant |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | No published authorized numeric request quota verified; throttling/cloud timeouts documented. Do not use proxies to evade blocks. |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | Official NBA IDs; require sourced ESPN crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | high |
| Expected improvement | Explicit endpoint field contracts for minutes/ratings/pace/eFG/TS/rebounding/turnovers, and NBA IDs |
| Score /bounded suitability | 72.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/swar/nba_api), [evidence2](https://raw.githubusercontent.com/swar/nba_api/master/README.md), [evidence3](https://raw.githubusercontent.com/swar/nba_api/master/LICENSE), [evidence4](https://github.com/swar/nba_api/commit/ec190700dab27498b3f75ecac8f932e3a3ee1606), [evidence5](https://github.com/swar/nba_api/blob/master/src/nba_api/stats/endpoints/boxscoreadvancedv3.py). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [dblackrun/pbpstats](https://github.com/dblackrun/pbpstats) — defer: parser only, maintenance/schema risk

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | dblackrun; nba; parser |
| Canonical location | https://github.com/dblackrun/pbpstats |
| Datasets | Possession and on-court lineup reconstruction from PBP |
| Seasons /competitions | Provider-dependent history; no independently hosted complete archive |
| Latest verified dataset update | Not independently verified; No independent dataset update established; see source notes |
| Latest verified code maintenance | 2024-04-17T00:16:40Z; path commit |
| Repository push /archival status | 2024-07-03T04:27:32Z; archived=False (not dataset freshness) |
| Formats /access | Python parser; source-specific JSON PBP and parsed possessions; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | MIT parser; source data rights separate |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Last checked pbpstats-path code commit 2024; possession boundaries/substitution corrections differ from estimated possession formulas |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | high |
| Expected improvement | Potential consistency checks after permitted PBP acquisition, not a current data source |
| Score /bounded suitability | 46.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/dblackrun/pbpstats), [evidence2](https://raw.githubusercontent.com/dblackrun/pbpstats/main/README.md), [evidence3](https://raw.githubusercontent.com/dblackrun/pbpstats/main/LICENSE), [evidence4](https://github.com/dblackrun/pbpstats/commit/afeba78758b8c0fea6a28eec9d4df8152d882f61). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [shufinskiy/nba_data](https://github.com/shufinskiy/nba_data) — conditional archive; no unrestricted recommendation

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | shufinskiy; nba; data |
| Canonical location | https://github.com/shufinskiy/nba_data |
| Datasets | Bulk PBP, shots, matchups and pbpstats-derived possession files |
| Seasons /competitions | README1996/97–2024/25; manifest includes 2025-start keys; per-type ranges differ |
| Latest verified dataset update | Not independently verified; No independent dataset update established; see source notes |
| Latest verified code maintenance | Not separately verified; Not verified at checked path |
| Repository push /archival status | 2026-07-17T13:43:36Z; archived=False (not dataset freshness) |
| Formats /access | CSV in tar.xz archives; text file index; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | Apache 2.0 repository; underlying NBA/pbpstats collection and redistribution permission not established |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | README/manifest horizon mismatch; exact dataset last update and row completeness not established |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | high |
| Expected improvement | Historical events older than local NBA archive; no new source-independence from underlying NBA feeds |
| Score /bounded suitability | 62.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/shufinskiy/nba_data), [evidence2](https://raw.githubusercontent.com/shufinskiy/nba_data/main/README.md), [evidence3](https://raw.githubusercontent.com/shufinskiy/nba_data/main/LICENSE), [evidence4](https://github.com/shufinskiy/nba_data/blob/main/list_data.txt). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [SkillCorner/opendata-basketball](https://github.com/SkillCorner/opendata-basketball) — reject for NBA integration: wrong competition

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | SkillCorner; nba; sample |
| Canonical location | https://github.com/SkillCorner/opendata-basketball |
| Datasets | Tracking, dynamic events and offensive aggregates |
| Seasons /competitions | 10 Spanish ACB2025/26 sample games; aggregates 293/327 games, not NBA |
| Latest verified dataset update | 2026-09-10T16:21:19.000+02:00; Latest inspected path commit |
| Latest verified code maintenance | Not separately verified; Not verified at checked path |
| Repository push /archival status | Not independently verified; archived=None (not dataset freshness) |
| Formats /access | JSON; compressed JSONL tracking; CSV aggregates; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | MIT file and open-data README; preserve attribution and verify scope |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | A basketball dataset is not necessarily NBA; sample/full-aggregate scopes differ and possession definitions are proprietary-derived |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | high |
| Expected improvement | Zero additional NBA games |
| Score /bounded suitability | 44.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://github.com/SkillCorner/opendata-basketball), [evidence2](https://raw.githubusercontent.com/SkillCorner/opendata-basketball/main/README.md), [evidence3](https://raw.githubusercontent.com/SkillCorner/opendata-basketball/main/LICENSE), [evidence4](https://github.com/SkillCorner/opendata-basketball/commits/main), [evidence5](https://github.com/SkillCorner/opendata-basketball/commits/main/data), [evidence6](https://github.com/SkillCorner/opendata-basketball/commits/main/R). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.
