# NBA source rights and release metadata

Reviewed October 22, 2026. **No intended-use permission has been established for any candidate. The 2023 pilot remains blocked.** Repository licenses were verified as declarations; their upstream rights chain was not. All existing registry flags remain disabled, and the prior registry/evidence files are preserved.

## Rights decisions

| Provider | Verified declaration | Current decision | Unresolved evidence |
|---|---|---|---|
| [sportsdataverse/sportsdataverse-data](https://github.com/sportsdataverse/sportsdataverse-data) | Root [MIT LICENSE](https://github.com/sportsdataverse/sportsdataverse-data/blob/main/LICENSE); [README](https://github.com/sportsdataverse/sportsdataverse-data/blob/main/README.md) claims CC-BY-4.0 data; [ESPN producer LICENSE.md](https://github.com/sportsdataverse/hoopR-nba-data/blob/main/LICENSE.md) explicitly covers data/code/docs under CC-BY-4.0 | **Unclear or unverified** | Which grant covers the particular release; authority to grant underlying ESPN-derived content for ScorPred's intended purpose |
| [sportsdataverse/hoopR-nba-stats-data](https://github.com/sportsdataverse/hoopR-nba-stats-data) | [LICENSE.md](https://github.com/sportsdataverse/hoopR-nba-stats-data/blob/main/LICENSE.md) explicitly CC-BY-4.0 for data/code/docs; root LICENSE is a stub | **Restricted** | Applicable upstream exception/authorization for the proposed statistics use; producer declaration alone does not establish it |
| [shufinskiy/nba_data](https://github.com/shufinskiy/nba_data) | [Apache-2.0 LICENSE](https://github.com/shufinskiy/nba_data/blob/main/LICENSE) | **Unclear or unverified** | Dataset-specific NBA/data.nba/pbpstats rights and the scope of the repository grant |

The classification is ScorPred's conservative access decision, not a conclusion that every factual statistic or every private analysis is protected or prohibited. No provider was contacted and no written approval, exception or downstream rights assurance was found. A release description saying a URL needs no authentication describes access, not authorization.

License response hashes, exact URLs, scope and observation times are in [the rights decision manifest](assets/rights_soccer/nba_rights_decisions.json) and the retained [repository](assets/github_data_research/repository_evidence.json)/[supplemental](assets/github_data_research/supplemental_evidence.json) evidence. Those declarations are not silently relabeled as data-use approval.

## Applicable primary terms

The [Disney terms](https://disneytermsofuse.com/english/) expressly include ESPN. Section 2 identifies Disney/licensor ownership; section 2A limits consumer use and addresses AI/ML uses. Sections **2B(viii)** and **2B(x)** condition business use and automated extraction/dataset building on **“express written permission.”** No such permission for the proposed release copies and reconciliation was verified. The page states May 24, 2024 as its update date; the reviewed page's actual clauses control over that date label. Whether a particular derivative statistical file falls within particular rights or exceptions remains unresolved; technical accessibility does not answer it.

[NBA terms section 9](https://www.nba.com/termsofuse) restricts gambling, products and comprehensive updated databases; no applicable exception was verified.

[CC-BY-4.0 legal code](https://creativecommons.org/licenses/by/4.0/legalcode.en), **1(g)** and **2(a)**, limits the grant to rights the licensor has authority to license. Sections **2(b)** and **5** retain other-rights and warranty limitations; section **3** imposes attribution/change-notice conditions. A CC data declaration therefore matters, but does not prove upstream authority. MIT/Apache grants likewise cannot be assumed to establish third-party rights beyond their actual scope.

The missing evidence is an authoritative, dataset/version-specific grant or reviewed applicable terms/exception covering the proposed access, copying, storage and historical reconciliation. Later training, automated updates, prediction display and redistribution need separate explicit scope. Keep notices, territory/retention conditions and any downstream restrictions with that decision. A registry flag, public URL or successful GET is insufficient.

## Verified release listings

Only public GitHub release descriptions, paginated **asset metadata listings**, README and source-code text were requested. No asset, sidecar, remote table or player statistic was fetched; no upstream NBA/ESPN endpoint or bearer-token service was called. The 12 bounded metadata/document requests include two 404 schema-document paths; there were no retries or redirect following.

| Release | Recognized season files | Ending seasons | Latest recognized season-file update, UTC |
|---|---:|---|---|
| [ESPN team boxes](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_nba_team_boxscores) | 102: CSV/Parquet/RDS × 34 | **1994–2027** | **2026-10-21 11:29:59** |
| [NBA Stats team boxes](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/nba_stats_team_boxscores) | 90: three formats × 30 | **1997–2026** | **2026-10-01 00:32:20** |
| [NBA Stats player boxes](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/nba_stats_player_boxscores) | 90: three formats × 30 | **1997–2026** | **2026-10-01 00:32:02** |

The complete listings contain 106/94/94 assets including four sidecars each. Pagination was explicitly checked; the embedded asset array was not assumed complete. Three formats are alternate representations, not extra games. Original asset IDs, names, sizes, update dates and published digests are in [the normalized manifest](assets/rights_soccer/nba_release_summary.json); exact responses and descriptions are in [the evidence snapshot](assets/rights_soccer/nba_release_evidence.json).

ESPN's description claims **25 seasons, 2002–2026, 79 assets, last written September 9**. Its actual listing has 34 season partitions through 2027 and October 21 updates. The producer README also has older counts. Preserve this inconsistency. The NBA Stats descriptions align with their 30-season file horizons, but still do not establish row completeness. Their descriptions also distinguish ending-year filenames from starting-year client arguments; any later pilot must use an explicit season convention. No documented network-access workaround was adopted.

## Schema and comparison with local coverage

The retained NBA Stats [player](https://github.com/sportsdataverse/hoopR-nba-stats-data/blob/main/docs/datasets/player_boxscores.md) and [team](https://github.com/sportsdataverse/hoopR-nba-stats-data/blob/main/docs/datasets/team_boxscores.md) schemas document `minutes` as **String**, with game/team/person identities and traditional count fields. A string type does not establish minute syntax, DNP rules, precision or actual historical fill rates.

The inspected ESPN README advertises team/player boxes, but the attempted equivalent dataset-schema Markdown paths returned 404. Its two [creation-stage source files](assets/rights_soccer/nba_schema_code_evidence.json) are thin wrappers around `nba_data_build`, without a minute field contract. **ESPN player-minute columns/units remain unverified in these inspected documents.** No missing contract was filled by downloading a sample asset.

All three newly inspected listings advertise 2023 files, potentially overlapping the **1,314** local ending-2023 games. Only **71** of those reference games have paired local cached box structures/minute presence. Across all 6,048 local results, the prior inventory found 282 paired boxes and 272 minute-presence passes. External file presence proves no additional game or repaired field; no candidate game IDs or box rows were inspected. Exact matches, additions and missing-game counts remain unknown.

The next NBA action is the rights evidence resolution and explicit ESPN field contract specified in [the conditional pilot plan](NBA_2023_PILOT_PLAN.md). Neither metadata verification nor a prepared plan authorizes sports-data acquisition.
