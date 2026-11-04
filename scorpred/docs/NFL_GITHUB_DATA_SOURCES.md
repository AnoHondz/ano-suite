# NFL GitHub sources: retain the existing backbone

Observed October 22, 2026 UTC / October 21 Eastern. Read with the [research method and scoring](SCORPRED_GITHUB_DATA_RESEARCH.md). No NFL outcome payloads were downloaded or queried; only local dataset/receipt metadata and upstream release metadata were inspected.

## What ScorPred already possesses

The live store has **90 dataset/season partitions across 28 dataset names**. Exact per-partition row counts, hashes and receive times are in [existing_inventory.json](assets/github_data_research/existing_inventory.json). Season support in a loader is not a claim that every season has been imported.

| Existing category | Local coverage | Measured limitation / immediate value |
|---|---|---|
| Schedules/results | 2022:284; 2023–25:285 each; 2026:272 rows | No need for another core schedule import. First four are closed historical scopes; future placeholders are not results |
| PBP | 2022–26; current 2026 metadata:16,380 rows | EPA/success already present. Current projection includes `no_play`; old stored-field audit is not a new verification. CPOE is not projected |
| Team/player game stats | 2022–26; 2026:176 team rows /6,131 player rows | Audit team feed covered 88 of 93 completed games; inspect the feed/derivation gap rather than add another source blindly |
| Derived team/QB games | 2022–26; 2026:160 team rows /187 QB rows | Derived from PBP; not an independent source. Their last derivation remains October 18 |
| Annual/weekly rosters, depth, injuries, snap counts | Mostly 2022,2024,2026 | Missing 2023/2025 local partitions; current observations mostly October 18, despite newer upstream releases |
| NGS passing/rushing/receiving | 2022,2024,2026 | Public weekly aggregates, not full player tracking. Newer combined release exists; historical availability still unproved |
| PFR passing/rushing/receiving/defense | Week and some season summaries; mostly 2022/24/26 | Specific missing partitions and current pass-season missingness; do not use final season aggregates within a season |
| FTN charting | 2022,2024,2026 | Existing data and loader; licence/definition gate before new adaptations |
| Participation | 2022 and 2024 | Historical 2023/2025 releases available; 2026 in-season release absent and not expected |
| Directories/crosswalks | 36 historical team entries;24,854 player entries | 36 is not the active-club count. Existing explicit IDs should resolve gaps; no heuristic name matching |

The code already supports offensive/defensive EPA, success, passing/rushing efficiency, explosive plays, third down, red-zone and QB aggregations. These must use previous eligible games, both sides where necessary and explicit play filters. Additional advanced matchup columns are not justified merely because a repository contains them. The historical research's 54 masked columns cannot be unmasked by discovering today's statistics.

Collection already has a single guarded owner, 240-second schedules, 21,600-second PBP and approximately 20-minute full-feature snapshots. Declared catalogue auxiliary intervals exist, but the observed collector does not own every auxiliary feed. A schedule/PBP success therefore does not make the roster/injury/NGS tables current. [Collector](../scripts/nfl_collector.py), [catalogue](../services/nfl_catalog.py), [diagnosis](NFL_DATA_DIAGNOSIS.md).

## Release freshness, code activity and historical availability

The current [nflverse organisation policy](https://github.com/nflverse#licensing) describes ordinary releases as CC-BY4.0 and FTN data as CC-BY-SA4.0. Preserve provider attribution, the exact licence version and release exceptions. Existing source-code licences are separate. Direct upstream scraping is not authorized by this research.

| Inspected release | Latest inspected asset update, UTC | Important distinction |
|---|---|---|
| [PBP](https://github.com/nflverse/nflverse-data/releases/tag/pbp) | 2026-10-21 14:35:34 | `play_by_play_2026.parquet` itself updated 14:35:32; this is file-version metadata, not an outcome read |
| [Rosters](https://github.com/nflverse/nflverse-data/releases/tag/rosters) | 2026-10-21 12:56:40 | Local 2026 rosters last received October 18 |
| [Depth](https://github.com/nflverse/nflverse-data/releases/tag/depth_charts) | 2026-10-21 12:53:37 | Newer schema uses timestamps rather than weekly snapshots from 2025 onward |
| [Injuries](https://github.com/nflverse/nflverse-data/releases/tag/injuries) | 2026-10-21 12:53:03 | Current asset existence does not prove an injury was published before an old kickoff |
| [NGS](https://github.com/nflverse/nflverse-data/releases/tag/nextgen_stats) | 2026-10-21 12:34:57 | Passing/rushing/receiving files contain multiple seasons; no full tracking stream established |
| [FTN charting](https://github.com/nflverse/nflverse-data/releases/tag/ftn_charting) | 2026-10-21 16:35:36 | Share-alike conditions and postgame availability require their own lane |
| [Participation](https://github.com/nflverse/nflverse-data/releases/tag/pbp_participation) | 2026-02-10 18:54:08 | Through 2025 assets; no 2026 file in the inspected manifest |

These long-lived release tags were created earlier. Their `published_at` values must not be substituted for latest data updates. [Machine-readable asset names, IDs, hashes where supplied and update dates](assets/github_data_research/release_manifests.json) retain the evidence without downloading the data.

The [official availability schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html) describes recurring PBP/stat corrections, daily roster/depth/injury updates, weekly NGS aggregates and four-daily FTN/PFR publishing opportunities. From 2023 onward participation arrives only after the postseason. Its absence in-season is expected. Timestamped depth changes can help future availability reconstruction, but `dt` alone is not ScorPred's receive receipt. Historical revisions remain ClassB unless original availability is independently proved.

## Ranked recommendation

1. **Keep `nflverse/nflverse-data` (88/100).** One existing data family; selective missing-year completion and refresh ownership offer measurable benefit. No new core import.
2. **Keep `nflverse/nflreadpy` (81/100) as existing tooling.** Already integrated; do not count it as another dataset or upgrade dependencies without a separate compatibility review.
3. **Use `nflverse/nflverse-rosters` (80/100) as a producer/schema reference.** Consume its outputs via the current host/loader, not an independent scraping stack. `nflverse-players` likewise supports explicit identity investigation; its README warns against installing the internal package.

There are **not three independently recommended new NFL datasets**. nflfastR supplies definitions and already-consumed derived fields. nfldata standalone file rights were not established in inspected licence paths; use the licensed release boundary. The actual nfelo project supplies a different prediction implementation, not a missing-statistics solution, and is rejected under this task's no-new-model constraint.

Do not expand the frozen feature definition, recalibrate models, regenerate the study hash or consume any of the 109 sealed outcomes. Both outcome and derived-statistic routes must retain current guards, including new provider IDs. Final review remains a separately authorized offline action no earlier than March 1,2027.

## Gap matrix

The classification **reliable** below concerns audited identity/results within their measured historical scope; it is not a claim of uninterrupted current collection. Gains are proposed acceptance tests. New usable record counts and cross-provider match rates remain unknown because no import occurred. [Full NFL CSV](assets/github_data_research/nfl_gaps.csv).

| Dataset | Classification | Existing scope / proposed measurable change |
|---|---|---|
| Historical schedules/results and core PBP | Already available and reliable | 2022–2026 stored;284/285/285/285/272 schedule rows, PBP all 5 seasons; Do not reimport valid core history; quantify schema/receipt gaps only |
| EPA/success, offensive/defensive team and QB aggregates | Already available but incomplete | PBP EPA/success and derived team/QB records exist;2026 team stats 88 vs 93 audited completed fixtures; Refresh/derive missing 5 feed games after source eligibility checks; report changed missingness, not assumed accuracy gain |
| PBP projection/schema completeness | Already available but incomplete | Current sync projection includes no_play; CPOE not projected. Audit stored projection predated this source check; Compare allowed historical schema keys against current source dictionaries before proposing a versioned extension |
| Rosters /weekly rosters | Already available but incomplete | Existing loaders and 2022/2024/2026 partitions; most auxiliary feeds lack 2023/2025 and retainOct 18 observations; Enumerate and reconcile missing 2023/2025 partitions; assign one periodic refresh owner; FTN requires share-alike decision |
| Depth charts | Already available but incomplete | Existing loaders and 2022/2024/2026 partitions; most auxiliary feeds lack 2023/2025 and retainOct 18 observations; Enumerate and reconcile missing 2023/2025 partitions; assign one periodic refresh owner; FTN requires share-alike decision |
| Injuries /availability | Already available but incomplete | Existing loaders and 2022/2024/2026 partitions; most auxiliary feeds lack 2023/2025 and retainOct 18 observations; Enumerate and reconcile missing 2023/2025 partitions; assign one periodic refresh owner; FTN requires share-alike decision |
| Snap counts | Already available but incomplete | Existing loaders and 2022/2024/2026 partitions; most auxiliary feeds lack 2023/2025 and retainOct 18 observations; Enumerate and reconcile missing 2023/2025 partitions; assign one periodic refresh owner; FTN requires share-alike decision |
| NGS passing/rushing/receiving | Already available but incomplete | Existing loaders and 2022/2024/2026 partitions; most auxiliary feeds lack 2023/2025 and retainOct 18 observations; Enumerate and reconcile missing 2023/2025 partitions; assign one periodic refresh owner; FTN requires share-alike decision |
| PFR advanced weeks | Already available but incomplete | Existing loaders and 2022/2024/2026 partitions; most auxiliary feeds lack 2023/2025 and retainOct 18 observations; Enumerate and reconcile missing 2023/2025 partitions; assign one periodic refresh owner; FTN requires share-alike decision |
| FTN charting | Already available but incomplete | Existing loaders and 2022/2024/2026 partitions; most auxiliary feeds lack 2023/2025 and retainOct 18 observations; Enumerate and reconcile missing 2023/2025 partitions; assign one periodic refresh owner; FTN requires share-alike decision |
| Historical participation 2023/2025 | Available from a verified GitHub source | Only 2022/2024 participation partitions present; Release manifest confirms files through 2025; quantify missing historical games after FTN licence review |
| In-season 2026 participation | Unavailable from verified free sources | Intentionally unavailable in current importer and verified release manifest; Zero current-season usable games established; do not scrape a substitute |
| Original historical pregame availability for advanced statistics | Unavailable from verified free sources | 54/100 historical columns masked; current receipts cannot backdate them; Collect genuine receipts prospectively; keep final 109 sealed and existing masks |
| Canonical game/player identity | Already available but incomplete | 24,854 player entries and existing explicit crosswalk; missing GSIS links observed; Measure crosswalk match rates and retain unresolved queue; no second directory import |

## Complete candidate assessments

Scores are engineering judgements with hard permission/availability gates. A producer/client is explicitly distinguished from a new data source. Unknown dates remain unknown rather than being copied from repository activity.

<!-- SOURCE_PROFILES -->

### [nflverse/nflverse-data](https://github.com/nflverse/nflverse-data) — retain existing integration; selectively repair gaps

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | nflverse; nfl; data |
| Canonical location | https://github.com/nflverse/nflverse-data |
| Datasets | Schedules/results, PBP, EPA/success, team/player stats, NGS, rosters, depth, injuries, snap counts, participation and FTN |
| Seasons /competitions | PBP assets 1999–2026; roster support 1920+; auxiliary eras differ; participation 2016–2025, no 2026 asset in inspected manifest |
| Latest verified dataset update | 2026-10-21T14:35:34Z; PBP release asset update; same data lineage, not latest played game |
| Latest verified code maintenance | 2025-10-12T21:42:29Z; path commit |
| Repository push /archival status | 2026-10-01T07:53:43Z; archived=False (not dataset freshness) |
| Formats /access | CSV/Parquet/RDS; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | CC-BY4.0 for ordinary data according to current nflverse policy; FTN charting and FTN-era participation CC-BY-SA4.0. Check each release exception and attribution. No blanket permission for independent upstream scraping. |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Mutable release assets, later corrections and postgame aggregates. Publication lag unknown for many historical fields. Current participation intentionally absent. |
| Existing integration /compatibility | already integrated:90 local dataset/season partitions; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | nflverse game_id, GSIS, PFR and ESPN namespaces; existing explicit player crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | low |
| Expected improvement | Fill missing 2023/2025 auxiliary partitions through existing loaders, refresh stale auxiliaries, review a versioned CPOE projection only if justified. No duplicate core import. |
| Score /bounded suitability | 88.0/100; bounded data suitability=True; no new production activation |

Verification: [evidence1](https://api.github.com/repos/nflverse/nflverse-data), [evidence2](https://raw.githubusercontent.com/nflverse/nflverse-data/main/README.md), [evidence3](https://raw.githubusercontent.com/nflverse/nflverse-data/main/LICENSE.md), [evidence4](https://api.github.com/repos/nflverse/nflverse-data/releases/tags/pbp), [evidence5](https://github.com/nflverse/nflverse-data/commit/da974bebfab76e48b9371681b83531343f26a767), [evidence6](https://github.com/nflverse#licensing), [evidence7](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [nflverse/nflreadpy](https://github.com/nflverse/nflreadpy) — retain existing client

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | nflverse; nfl; client |
| Canonical location | https://github.com/nflverse/nflreadpy |
| Datasets | Python loaders and cache/download policies for nflverse releases |
| Seasons /competitions | Loader-specific support; not an independent dataset |
| Latest verified dataset update | 2026-10-21T14:35:34Z; PBP release asset update; same data lineage, not latest played game |
| Latest verified code maintenance | 2026-09-09T19:52:39Z; path commit |
| Repository push /archival status | 2026-10-08T02:28:09Z; archived=False (not dataset freshness) |
| Formats /access | Python client; Polars DataFrames; release format varies by loader; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | MIT client; dataset licences remain source-specific |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | An installed loader does not establish historical availability or completeness |
| Existing integration /compatibility | already integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | low |
| Expected improvement | Already solves Python access; use existing pinned 0.1.5 rather than add another ingestion stack |
| Score /bounded suitability | 81.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/nflverse/nflreadpy), [evidence2](https://raw.githubusercontent.com/nflverse/nflreadpy/main/README.md), [evidence3](https://raw.githubusercontent.com/nflverse/nflreadpy/main/LICENSE.md), [evidence4](https://api.github.com/repos/nflverse/nflverse-data/releases/tags/pbp), [evidence5](https://github.com/nflverse/nflreadpy/commit/e7e87b2ef626b8672279db25432b1554ee13348a). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [nflverse/nflfastR](https://github.com/nflverse/nflfastR) — reference only; no new parser/model

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | nflverse; nfl; pipeline |
| Canonical location | https://github.com/nflverse/nflfastR |
| Datasets | PBP parsing, EPA/WPA and derived statistics |
| Seasons /competitions | NFL PBP era 1999+; outputs distributed by nflverse-data |
| Latest verified dataset update | 2026-10-21T14:35:34Z; PBP release asset update; same data lineage, not latest played game |
| Latest verified code maintenance | 2026-09-18T14:28:08Z; path commit |
| Repository push /archival status | 2026-10-14T20:15:54Z; archived=False (not dataset freshness) |
| Formats /access | R code/data frames; outputs delivered as CSV/Parquet/RDS releases; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | MIT code per DESCRIPTION; distributed data policy belongs to nflverse releases |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Running or refitting EPA models is outside this task; same underlying data |
| Existing integration /compatibility | existing outputs indirectly consumed; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | high |
| Expected improvement | Stat-definition reference and schema audit; existing PBP already supplies EPA/success |
| Score /bounded suitability | 77.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/nflverse/nflfastR), [evidence2](https://raw.githubusercontent.com/nflverse/nflfastR/master/README.md), [evidence3](https://raw.githubusercontent.com/nflverse/nflfastR/master/LICENSE), [evidence4](https://api.github.com/repos/nflverse/nflverse-data/releases/tags/pbp), [evidence5](https://github.com/nflverse/nflfastR/commit/ab663741652c598476844d2d24af0e2fcf6251f7), [evidence6](https://github.com/nflverse/nflfastR/blob/master/DESCRIPTION). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [nflverse/nflverse-rosters](https://github.com/nflverse/nflverse-rosters) — reference existing producer; repair refresh ownership

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | nflverse; nfl; pipeline |
| Canonical location | https://github.com/nflverse/nflverse-rosters |
| Datasets | Roster, depth-chart and injury generation |
| Seasons /competitions | Rosters 1920+, weekly 2002+, injuries 2009+, depth 2001+ loader support;2025+ depth timestamps |
| Latest verified dataset update | 2026-10-21T12:56:40Z; rosters release only; players release not independently checked |
| Latest verified code maintenance | 2025-08-03T12:50:29Z; path commit |
| Repository push /archival status | 2026-10-01T08:04:24Z; archived=False (not dataset freshness) |
| Formats /access | R producer code; CSV/Parquet/RDS release outputs; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | MIT code per DESCRIPTION; released ordinary data CC-BY4.0 subject to exceptions |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | No dedicated ScorPred auxiliary scheduler owner; historical injury publication unknown |
| Existing integration /compatibility | roster/depth/injury loaders and data already present; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | low |
| Expected improvement | Identify current temporal depth schema and refresh schedule; consume existing release host rather than scrape |
| Score /bounded suitability | 80.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/nflverse/nflverse-rosters), [evidence2](https://raw.githubusercontent.com/nflverse/nflverse-rosters/main/README.md), [evidence3](https://raw.githubusercontent.com/nflverse/nflverse-rosters/main/LICENSE), [evidence4](https://api.github.com/repos/nflverse/nflverse-data/releases/tags/rosters), [evidence5](https://github.com/nflverse/nflverse-rosters/commit/b26ffb387e00f97d34bba379ce9269e882409a5e), [evidence6](https://github.com/nflverse/nflverse-rosters/blob/main/DESCRIPTION). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [nflverse/nfldata](https://github.com/nflverse/nfldata) — do not add independent import

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | nflverse; nfl; reference |
| Canonical location | https://github.com/nflverse/nfldata |
| Datasets | NFL schedule/helper datasets and reference IDs |
| Seasons /competitions | Historical and current NFL files; exact standalone file ranges not inventoried |
| Latest verified dataset update | Not independently verified; No independent dataset update established; see source notes |
| Latest verified code maintenance | Not separately verified; Not verified at checked path |
| Repository push /archival status | 2026-10-22T01:45:14Z; archived=False (not dataset freshness) |
| Formats /access | See dataset and access notes; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | No standalone licence found in inspected README/LICENSE paths; org release policy supports nflverse-data, not an automatic relicence of every repository file |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Very recent repository activity is not an independently verified dataset update |
| Existing integration /compatibility | same lineage as existing schedule source; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | low |
| Expected improvement | No established extra coverage over existing schedules |
| Score /bounded suitability | 49.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/nflverse/nfldata), [evidence2](https://raw.githubusercontent.com/nflverse/nfldata/master/README.md). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [nflverse/nflverse-players](https://github.com/nflverse/nflverse-players) — reference only; do not install internal package

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | nflverse; nfl; pipeline |
| Canonical location | https://github.com/nflverse/nflverse-players |
| Datasets | GSIS/PFR/ESPN/OTC/PFF ID reconciliation and manual overrides |
| Seasons /competitions | Global historical/current player directory; exact per-source completeness not established |
| Latest verified dataset update | Not independently verified; rosters release only; players release not independently checked |
| Latest verified code maintenance | 2026-10-04T15:22:03Z; path commit |
| Repository push /archival status | 2026-10-04T15:22:06Z; archived=False (not dataset freshness) |
| Formats /access | R producer code; released player directory/crosswalk files; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | MIT code per DESCRIPTION; consume licensed players release through current loader |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Maintainer explicitly labels internal package; V2 renamed/removed variables; some links unresolved |
| Existing integration /compatibility | load_players and explicit crosswalk already present; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | low |
| Expected improvement | Investigate measured missing GSIS links using released explicit IDs;24,854 entries already local |
| Score /bounded suitability | 75.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/nflverse/nflverse-players), [evidence2](https://raw.githubusercontent.com/nflverse/nflverse-players/master/README.md), [evidence3](https://raw.githubusercontent.com/nflverse/nflverse-players/master/LICENSE), [evidence4](https://api.github.com/repos/nflverse/nflverse-data/releases/tags/rosters), [evidence5](https://github.com/nflverse/nflverse-players/commit/013ed4ebe161734a067074101af2f1cd6c42ff45), [evidence6](https://github.com/nflverse/nflverse-players/blob/master/DESCRIPTION). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [nfeloapp/nfelo](https://github.com/nfeloapp/nfelo) — unresolved URL; reject as supplied

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | nfeloapp; nfl; unresolved |
| Canonical location | https://github.com/nfeloapp/nfelo |
| Datasets | Unverified URL |
| Seasons /competitions | Not verified:HTTP404 |
| Latest verified dataset update | Not independently verified; No independent dataset update established; see source notes |
| Latest verified code maintenance | Not separately verified; Not verified at checked path |
| Repository push /archival status | Not independently verified; archived=None (not dataset freshness) |
| Formats /access | See dataset and access notes; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | Not verified |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | No repository verified |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | medium |
| Expected improvement | None |
| Score /bounded suitability | 0.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/nfeloapp/nfelo). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [greerreNFL/nfelo](https://github.com/greerreNFL/nfelo) — reject integration: new model and overlapping data

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | greerreNFL; nfl; model |
| Canonical location | https://github.com/greerreNFL/nfelo |
| Datasets | NFL power-rating and prediction implementation |
| Seasons /competitions | See project; no independent raw-data coverage established |
| Latest verified dataset update | Not independently verified; No independent dataset update established; see source notes |
| Latest verified code maintenance | 2026-10-20T12:40:46Z; path commit |
| Repository push /archival status | 2026-10-21T22:40:51Z; archived=False (not dataset freshness) |
| Formats /access | See dataset and access notes; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | No grant to use third-party data inferred from model repository |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | A different Elo implementation cannot repair provenance or establish incremental accuracy |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | high |
| Expected improvement | Methodology context only; no need to replace current Elo or frozen models |
| Score /bounded suitability | 35.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/greerreNFL/nfelo), [evidence2](https://raw.githubusercontent.com/greerreNFL/nfelo/main/README.md), [evidence3](https://github.com/greerreNFL/nfelo/commit/8675630b74a619142c1ef93590ee030e7ae72675). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.
