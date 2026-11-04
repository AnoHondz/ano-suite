# Soccer GitHub sources: results coverage versus event depth

Observed October 22,2026 UTC / October 21 Eastern. No match/event/lineup datasets were imported. A small competition-season index and repository file/commit metadata were inspected to establish scope. Current production predictions and the three supported leagues remain unchanged.

## Existing inventory and integrity prerequisites

ScorPred supports Premier League 39, Bundesliga 78 and LaLiga 140. The prior audit found 7,888 unique classifier fixtures dated 2021-08-08 through 2026-04-19, but all lacked league and original source-availability lineage. Its 47 varying inputs were mainly goals/results/form/venue/Elo/rest; five market, five xG and three referee columns were constant/unsupported. Their presence in a model signature is not measured data.

The audited cache had 1,348 events:1,260 completed and 88 scheduled. Prior 2025-start season caches had 374/380 EPL,300/306 Bundesliga and 374/380 LaLiga fixtures. That is 18 cached gaps against league capacities, not proof that the upstream sources omitted 18 games. Selected daily probes all agreed with cached identities, but were not universe reconciliation.

Stage 1 preserved the erroneous original `E0_2018.csv` and quarantined it by manifest: its 319 rows duplicated 2025/26. The new `matches_stage1.csv` has 2,599 unique records and zero duplicates; it was not promoted into a model. Genuine 2018/19 is still missing. Existing 2019–24 EPL seasons have 380 rows each;2025 raw data is partial at 319/380. Current input window, target-date and unsupported prop definitions were repaired and tested; new research must not treat those as unfixed source bugs. [Soccer audit](SOCCER_DATA_DIAGNOSIS.md), [Stage1 evidence](SCORPRED_STAGE1_REMEDIATION.md), [inventory](assets/github_data_research/existing_inventory.json).

## Results sources and actual maintenance

| Repository | Declared/manifest scope | Latest verified dataset-specific observation | Recommended use |
|---|---|---|---|
| [openfootball/football.json](https://github.com/openfootball/football.json) | Season directories 2010/11–2026/27; current folder includes `en.1`, `de.1`, `es.1` and five other league files | EPL2026/27 file last committed 2026-09-22; not a fresh October feed | CC0 historical fixture/result reconciliation and league/season labels; source rows still need completeness/identity validation |
| [openfootball/england](https://github.com/openfootball/england) | Historical/current English top-four-tier Football.TXT |2026/27 path last committed 2026-09-21; repository later pushed October 15 | Same-family upstream alternative; custom text parser and timezone checks required |
| [Fjelstul English Football](https://github.com/jfjelstul/englishfootball) |1888/89–2023/24; author reports 208,028 matches, teams, team appearances and end-season tables | Data-path commit 2024-05-26; not a current feed | Explicit CC-BY-SA4.0 historical lane, useful independent structure/team IDs for 2018 and promoted-club history |
| [footballcsv England](https://github.com/footballcsv/england) |1990 s–2020 s directories, latest 2020/21 folder |2020 s data-path last changed 2023-05-28 | Historical CSV convenience only; do not interpret recent README interest as an ongoing feed |
| [footballcsv Germany](https://github.com/footballcsv/deutschland) |1960 s–2020 s directories, latest 2020/21 folder |2020 s path last changed 2020-12-01 | Dated historical archive; cannot fill current Bundesliga gaps |
| [footballcsv Spain](https://github.com/footballcsv/espana) |2010 s and 2020/21 directories |2020 s path last changed 2020-12-01 | Dated historical archive; cannot fill current LaLiga gaps |

Folder ranges are manifest evidence, not independently recounted full seasons. The footballcsv READMEs expressly identify their data as 1:1 openfootball exports. They are not three independent sources of evidence and may contain partial final seasons. All those openfootball/footballcsv licences are CC0/public-domain dedications; Fjelstul requires attribution and adapted-work share-alike. Its `appearances` are **team-per-match**, not player minutes. Its final standings must never enter within-season pregame strength features. [Fjelstul licence/definition](https://github.com/jfjelstul/englishfootball#the-license), [openfootball update policy](https://github.com/openfootball/help).

Dates/names/results can strengthen chronological H/D/A reconstruction and league identity. They do not add possession, injuries, player events or xG. Keep 90-minute results distinct from extra time/shootouts and never create UTC kickoff times from date-only entries. Valid complete seasons should be left alone; import only an enumerated gap after conflict/identity checks.

## StatsBomb: selected event coverage, restricted use

The requested [statsbomb/open-data](https://github.com/statsbomb/open-data) redirects to [hudl/open-data](https://github.com/hudl/open-data). Its inspected `competitions.json` contains **80 competition-season entries**, not 80 complete leagues or seasons. The index's latest revision field is May 26,2026; it is revision metadata, not historical pregame publication proof. No 2026/27 entry for any supported league was found. [Exact80-row metadata export](assets/github_data_research/statsbomb_competition_seasons.csv).

| Supported league | Entries in inspected index | Critical limitation |
|---|---|---|
| Premier League |2003/04 and 2015/16 | No current season; an entry alone does not establish complete league-match coverage |
| Bundesliga |2015/16 and 2023/24 | Selected historical data, not a current Bundesliga feed |
| LaLiga | Selected seasons 2004/05–2020/21 plus 1973/74 | Uneven/selected match scope; do not assume every match or every club is represented |

Other entries include international tournaments, women's competitions and selected domestic competitions. They do not automatically expand the existing men's club-league prediction population. Events can supply provider-specific shot xG, passes, shots, cards/fouls, substitutions and lineups;360 exists only for selected matches. Event possession IDs are not a validated possession-time percentage. Actual lineups and events are postgame facts; they cannot be target-game pregame inputs.

The README highlights credit/logo requirements, but the actual **StatsBomb Public Data User Agreement** also restricts commercial exploitation and raw redistribution and requests user registration. Its text is a custom agreement, not an unrestricted CC-BY grant. Clause 3.1 contains a licensor-rights representation, but it does not remove usage restrictions. Any adaptation into public app outputs or training beyond the permitted scope needs a separate documented decision. Keep this a restricted isolated research candidate, not a production replacement. [Agreement PDF](https://github.com/hudl/open-data/blob/master/LICENSE.pdf), [retained licence hash/text](assets/github_data_research/technical_evidence.json).

No current complete free GitHub archive of injuries, suspensions or pregame announced squads for all three supported leagues was verified. A postgame lineup archive cannot fill that gap. Existing 31 roster files/932 players with zero cached injuries do not establish healthy squads.

## Additional candidates and rejected substitutions

[SkillCorner/opendata](https://github.com/SkillCorner/opendata) has recent dataset-path activity in September 2026, but its current sample is 10 Australian A-League 2024/25 games with tracking/events and aggregate context. It is useful format research, not new EPL/Bundesliga/LaLiga coverage. [Metrica samples](https://github.com/metrica-sports/sample-data) have three anonymized example games, last addition 2021; comprehensive reuse permission was not established in the inspected files. Neither supports a reliable target-population H/D/A evaluation.

The explicitly requested `andrewbaisden/football-data` returned 404. No season coverage, licence or maintenance was verified, and it is not silently replaced by the similarly named football-data.co.uk service or an unrelated author's repository. Existing football-data.co.uk acquisition restrictions from the original audit remain unresolved; a GitHub mirror would not automatically cure those terms.

## Ranked recommendation

1. **openfootball/football.json,71/100:** easiest bounded three-league historical reconciliation, but current folder is too stale for a live fallback claim.
2. **Fjelstul English Football,67:** strongest additional historical English identity/structure candidate, subject to share-alike compliance and source-specific team-lineage conventions. Old lower-tier games are not automatically useful modern top-tier examples.
3. **openfootball/england,66:** upstream same-family historical cross-check; use where it supplies a specifically measured gap rather than a duplicate JSON import.

footballcsv scores 55 as historical format mirrors. StatsBomb scores 58 technically but has a different licence/population scope; it is not promoted above a permission gate because it contains deeper columns. There is no unconditional current event/xG/injury replacement for the three leagues.

Reconstruct league/season identity first, then measure duplicate/conflict/unmatched counts. Only later propose a controlled experiment on the existing H/D/A method, with all classes, calibration-bin denominators, paired matchweek uncertainty and an untouched final period. More rows or xG columns alone do not validate accuracy.

## Gap matrix

[Full soccer CSV](assets/github_data_research/soccer_gaps.csv). Current optional stat coverage and historical rights are separate from technical field definitions.

| Dataset | Classification | Existing scope / proposed measurable change |
|---|---|---|
| Existing valid fixtures and result statistics | Already available and reliable | 7,888 unique classifier fixtures; existing 2019–24 EPL raw seasons complete in audit; Do not reimport known complete seasons; reliable fixture/result checks do not establish league lineage |
| Genuine 2018 EPL /missing result fixtures | Available from a verified GitHub source | Mislabeled 2018 file quarantined; newStage 1 derived 2599 rows; oldoriginals preserved; Reconcile genuine 2018 season;380 is league capacity, not a verified number of new rows; inspect 18 cached previous-league gaps and 61 missing 2025 rawEPL fixtures |
| League/season lineage for existing classifier data | Already available but incomplete | All 7,888 rows lack league/source-availability lineage; cannot infer from team names; Report mapping and ambiguity rates per league/season; never assign by nearest name alone |
| Fixtures /competition calendars | Already available but incomplete | Three supported current leagues, lazy incomplete cache; source receipts mostly ephemeral; Retain current provider unless a permitted source is demonstrably fresher; en.1 currentfilelastchangedSep 22 |
| Home-away goals/form /team strength /congestion /standings | Already available but incomplete | Existing rolling goals/results/Elo/rest;7 of 58 teamnames absent from savedElo; Resolve promoted-club history and complete schedules; compute standings causally, never import end-season table as pregame strength |
| xG /shots and shots on target | Available but licensing or provenance is uncertain | Optional current fields incomplete; xG/market/referee classifier columns unsupported or constant; 80 competition-seasonentries are an index, not 80 completeleagues; zero 2026/27 supported-league coverage proved |
| Passes /passing accuracy /possession | Available but licensing or provenance is uncertain | Optional current fields incomplete; xG/market/referee classifier columns unsupported or constant; 80 competition-seasonentries are an index, not 80 completeleagues; zero 2026/27 supported-league coverage proved |
| Corners /fouls /cards | Available but licensing or provenance is uncertain | Optional current fields incomplete; xG/market/referee classifier columns unsupported or constant; 80 competition-seasonentries are an index, not 80 completeleagues; zero 2026/27 supported-league coverage proved |
| Player events /lineups /substitutions | Available but licensing or provenance is uncertain | Optional current fields incomplete; xG/market/referee classifier columns unsupported or constant; 80 competition-seasonentries are an index, not 80 completeleagues; zero 2026/27 supported-league coverage proved |
| Injuries /suspensions /pregame announcements | Unavailable from verified free sources | 31 rosters/932 players and 0 cachedinjuries in audit; unknown is not healthy; Explicit unavailable status; future permitted source review before collection |
| Unsupported player-prop field definitions | Unavailable from verified free sources | Stage 1 blocks clearances<-blocks, aerialwins<-allduels, passescompleted<-totalpasses, chancescreated<-keypasses; Zero invented markets; validate exact source field meanings and minutes before any restoration |
| Current club-league tracking data | Available but licensing or provenance is uncertain | No matching tracked league histories; Zero verified EPL/Bundesliga/LaLiga tracking coverage; do not expand model scope |
| Durable pregame source/input/probability archive | Unavailable from verified free sources | 12 auditedsoccertrackingrows lack adequate bindings; Archive future receipts and complete H/D/A vectors, league/season and model/policy hashes |

## Complete candidate assessments

<!-- SOURCE_PROFILES -->

### [statsbomb/open-data](https://github.com/statsbomb/open-data) — conditional isolated noncommercial research only

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | hudl; soccer; data |
| Canonical location | https://github.com/hudl/open-data |
| Datasets | Selected events, shots/xG, passes, lineups, substitutions and 360 subsets |
| Seasons /competitions | 80 competition-season entries; EPL2003/04 and 2015/16; Bundesliga 2015/16 and 2023/24; selected LaLiga seasons; no supported-league 2026/27 entry |
| Latest verified dataset update | 2026-05-26T15:59:55.000+01:00; Latest inspected data-path commit; not a completeness or original-publication claim |
| Latest verified code maintenance | Not separately verified; Data-oriented repository; no separately verified code-maintenance date; repository activity is recorded separately |
| Repository push /archival status | 2026-09-07T16:27:17Z; archived=False (not dataset freshness) |
| Formats /access | JSON; selected 360 JSON; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | Custom StatsBomb Public Data User Agreement, updated 2023-09-08; noncommercial research scope, distribution/commercial restrictions, registration request and publication/logo attribution. Not CC-BY or unrestricted production data. |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Competition entry does not imply full-season matches. match_updated/available are revision metadata, not historical prekickoff receipts. Canonical repo redirects to hudl/open-data. |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | StatsBomb competition_id/season_id/match_id/team/player IDs need explicit mapping; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | high |
| Expected improvement | Research event semantics and provider-specific xG on selected historical games; does not fill current 3-league coverage |
| Score /bounded suitability | 58.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/statsbomb/open-data), [evidence2](https://raw.githubusercontent.com/hudl/open-data/master/README.md), [evidence3](https://github.com/hudl/open-data/commits/master/data/competitions.json), [evidence4](https://github.com/hudl/open-data/blob/master/LICENSE.pdf), [evidence5](https://github.com/hudl/open-data/blob/master/data/competitions.json). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [openfootball/football.json](https://github.com/openfootball/football.json) — verified suitable for bounded historical results/lineage reconciliation

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | openfootball; soccer; data |
| Canonical location | https://github.com/openfootball/football.json |
| Datasets | Multi-league fixtures/results in JSON |
| Seasons /competitions | Season directories 2010/11–2026/27; current folder has en.1,de.1,es.1 plus other leagues; exact per-file completeness unmeasured |
| Latest verified dataset update | 2026-09-22T09:33:49.000Z; Latest inspected data-path commit; not a completeness or original-publication claim |
| Latest verified code maintenance | Not separately verified; Data-oriented repository; no separately verified code-maintenance date; repository activity is recorded separately |
| Repository push /archival status | 2026-09-22T09:33:50Z; archived=False (not dataset freshness) |
| Formats /access | JSON; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | CC0/public-domain dedication explicitly covers schema, data and scripts |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Inspected 2026/27 EPL file last changed 2026-09-22, about 30 days old; volunteer updates have no SLA. Names/date/round lack original provider IDs and often timezones. |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | No shared canonical game/player IDs; verified team aliases plus competition/season/date/home/away keys; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | medium |
| Expected improvement | Recover genuinely labelled 2018/19 EPL, cross-check 18 audited missing cached fixtures and three-league season lineage; import only unmatched permitted games |
| Score /bounded suitability | 71.0/100; bounded data suitability=True; no new production activation |

Verification: [evidence1](https://api.github.com/repos/openfootball/football.json), [evidence2](https://raw.githubusercontent.com/openfootball/football.json/master/README.md), [evidence3](https://raw.githubusercontent.com/openfootball/football.json/master/LICENSE.md), [evidence4](https://github.com/openfootball/football.json/commits/master/2026-27/en.1.json). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [openfootball/england](https://github.com/openfootball/england) — verified suitable historical cross-check; prefer to stale mirrors

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | openfootball; soccer; data |
| Canonical location | https://github.com/openfootball/england |
| Datasets | English top-four-tier results/calendars in Football.TXT |
| Seasons /competitions | Historical seasons, maintained through 2026/27 folder; exact complete season list not enumerated |
| Latest verified dataset update | 2026-09-21T12:35:47.000+02:00; Latest inspected data-path commit; not a completeness or original-publication claim |
| Latest verified code maintenance | Not separately verified; Data-oriented repository; no separately verified code-maintenance date; repository activity is recorded separately |
| Repository push /archival status | 2026-10-15T14:42:32Z; archived=False (not dataset freshness) |
| Formats /access | Football.TXT; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | CC0/public-domain dedication |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | 2026/27 path last changed 2026-09-21 although repo pushedOct 15; custom text parsing and timezones require validation. Same family as football.json. |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | Team-name aliases, season-specific competition and original row locator; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | medium |
| Expected improvement | Recover EPL season identity and promoted-team history without replacing already complete 2019–24 results |
| Score /bounded suitability | 66.0/100; bounded data suitability=True; no new production activation |

Verification: [evidence1](https://api.github.com/repos/openfootball/england), [evidence2](https://raw.githubusercontent.com/openfootball/england/master/README.md), [evidence3](https://raw.githubusercontent.com/openfootball/england/master/LICENSE.md), [evidence4](https://github.com/openfootball/england/commits/master/2026-27). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [footballcsv/england](https://github.com/footballcsv/england) — verified suitable historical archive only

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | footballcsv; soccer; data |
| Canonical location | https://github.com/footballcsv/england |
| Datasets | English historical match-result CSV mirror |
| Seasons /competitions | Decade folders 1990 s–2020 s, latest folder 2020/21; EPL and lower divisions |
| Latest verified dataset update | 2023-05-28T11:53:10.000+02:00; Latest inspected data-path commit; not a completeness or original-publication claim |
| Latest verified code maintenance | Not separately verified; Data-oriented repository; no separately verified code-maintenance date; repository activity is recorded separately |
| Repository push /archival status | 2023-05-28T13:09:55Z; archived=False (not dataset freshness) |
| Formats /access | CSV; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | CC0-1.0 file and public-domain README; explicitly a 1:1 export of openfootball source |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Stops at 2020/21 folder in inspected tree; some seasons partial; not a current feed or independent confirmation of its own upstream |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | Names/dates; no guaranteed game ID or UTC kickoff; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | low |
| Expected improvement | Convenient historical format for a measured missing-season gap; zero assumed novel records over upstream openfootball |
| Score /bounded suitability | 55.0/100; bounded data suitability=True; no new production activation |

Verification: [evidence1](https://api.github.com/repos/footballcsv/england), [evidence2](https://raw.githubusercontent.com/footballcsv/england/master/README.md), [evidence3](https://raw.githubusercontent.com/footballcsv/england/master/LICENSE.md), [evidence4](https://github.com/footballcsv/england/commits/master/2020s). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [footballcsv/deutschland](https://github.com/footballcsv/deutschland) — verified suitable historical archive only

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | footballcsv; soccer; data |
| Canonical location | https://github.com/footballcsv/deutschland |
| Datasets | German historical match-result CSV mirror |
| Seasons /competitions | Decade folders 1960 s–2020 s, latest folder 2020/21; Bundesliga and lower divisions |
| Latest verified dataset update | 2020-12-01T06:05:32.000Z; Latest inspected data-path commit; not a completeness or original-publication claim |
| Latest verified code maintenance | Not separately verified; Data-oriented repository; no separately verified code-maintenance date; repository activity is recorded separately |
| Repository push /archival status | 2020-12-01T06:05:37Z; archived=False (not dataset freshness) |
| Formats /access | CSV; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | CC0-1.0 file and public-domain README; explicitly a 1:1 export of openfootball source |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Stops at 2020/21 folder in inspected tree; some seasons partial; not a current feed or independent confirmation of its own upstream |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | Names/dates; no guaranteed game ID or UTC kickoff; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | low |
| Expected improvement | Convenient historical format for a measured missing-season gap; zero assumed novel records over upstream openfootball |
| Score /bounded suitability | 55.0/100; bounded data suitability=True; no new production activation |

Verification: [evidence1](https://api.github.com/repos/footballcsv/deutschland), [evidence2](https://raw.githubusercontent.com/footballcsv/deutschland/master/README.md), [evidence3](https://raw.githubusercontent.com/footballcsv/deutschland/master/LICENSE.md), [evidence4](https://github.com/footballcsv/deutschland/commits/master/2020s). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [footballcsv/espana](https://github.com/footballcsv/espana) — verified suitable historical archive only

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | footballcsv; soccer; data |
| Canonical location | https://github.com/footballcsv/espana |
| Datasets | Spanish historical match-result CSV mirror |
| Seasons /competitions | 2010 s and 2020/21 folders; LaLiga and Segunda |
| Latest verified dataset update | 2020-12-01T06:05:41.000Z; Latest inspected data-path commit; not a completeness or original-publication claim |
| Latest verified code maintenance | Not separately verified; Data-oriented repository; no separately verified code-maintenance date; repository activity is recorded separately |
| Repository push /archival status | 2020-12-01T06:05:45Z; archived=False (not dataset freshness) |
| Formats /access | CSV; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | CC0-1.0 file and public-domain README; explicitly a 1:1 export of openfootball source |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Stops at 2020/21 folder in inspected tree; some seasons partial; not a current feed or independent confirmation of its own upstream |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | Names/dates; no guaranteed game ID or UTC kickoff; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | low |
| Expected improvement | Convenient historical format for a measured missing-season gap; zero assumed novel records over upstream openfootball |
| Score /bounded suitability | 55.0/100; bounded data suitability=True; no new production activation |

Verification: [evidence1](https://api.github.com/repos/footballcsv/espana), [evidence2](https://raw.githubusercontent.com/footballcsv/espana/master/README.md), [evidence3](https://raw.githubusercontent.com/footballcsv/espana/master/LICENSE.md), [evidence4](https://github.com/footballcsv/espana/commits/master/2020s). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [andrewbaisden/football-data](https://github.com/andrewbaisden/football-data) — requested URL unavailable; do not substitute another project silently

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | andrewbaisden; soccer; unresolved |
| Canonical location | https://github.com/andrewbaisden/football-data |
| Datasets | Unverified requested repository |
| Seasons /competitions | HTTP404 at requested URL |
| Latest verified dataset update | Not independently verified; No independent dataset update established; see source notes |
| Latest verified code maintenance | Not separately verified; Not verified at checked path |
| Repository push /archival status | Not independently verified; archived=None (not dataset freshness) |
| Formats /access | See dataset and access notes; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | Not verified |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | No accessible repository or licence verified |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | medium |
| Expected improvement | None established |
| Score /bounded suitability | 0.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/andrewbaisden/football-data). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [metrica-sports/sample-data](https://github.com/metrica-sports/sample-data) — reject for league coverage; rights review for optional examples

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | metrica-sports; soccer; sample |
| Canonical location | https://github.com/metrica-sports/sample-data |
| Datasets | Anonymized tracking and events for 3 sample games |
| Seasons /competitions | Two original CSV samples plus Game 3 added 2021-04-15; identities/competitions anonymized |
| Latest verified dataset update | Not independently verified; No independent dataset update established; see source notes |
| Latest verified code maintenance | Not separately verified; Data-oriented repository; no separately verified code-maintenance date; repository activity is recorded separately |
| Repository push /archival status | 2021-04-15T16:35:37Z; archived=False (not dataset freshness) |
| Formats /access | CSV; FIFA EPTS tracking/metadata; JSON events; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | README requests acknowledgement; no explicit comprehensive licence grant verified |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Not maintained as a current league dataset;3 examples cannot validate winner reliability |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | high |
| Expected improvement | No measurable gain for supported leagues or H/D/A validation |
| Score /bounded suitability | 21.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://api.github.com/repos/metrica-sports/sample-data), [evidence2](https://raw.githubusercontent.com/metrica-sports/sample-data/master/README.md). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [jfjelstul/englishfootball](https://github.com/jfjelstul/englishfootball) — verified suitable isolated historical research with share-alike compliance

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | jfjelstul; soccer; data |
| Canonical location | https://github.com/jfjelstul/englishfootball |
| Datasets | Seasons/teams/matches, team appearances, end-season standings |
| Seasons /competitions | 1888/89–2023/24 English top 4 tiers; author reports 208,028 matches; files not imported/recounted |
| Latest verified dataset update | 2024-05-26T15:23:04.000-05:00; Latest inspected path commit |
| Latest verified code maintenance | 2024-05-26T11:13:43.000-05:00; Latest inspected path commit |
| Repository push /archival status | 2024-05-26T20:48:45Z; archived=False (not dataset freshness) |
| Formats /access | CSV/SQLite/RData; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | CC-BY-SA4.0 explicitly covers database and R package; attribution and adapted-work share-alike required |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Not a current feed; old lower-tier populations/phoenix-team conventions require explicit policy; end-season standings leak if used within season |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | Author team_id across name changes; phoenix lineage not automatically adopted; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | medium |
| Expected improvement | Independent historical structure/IDs for missing 2018 EPL and promoted clubs; quantify exact overlap first |
| Score /bounded suitability | 67.0/100; bounded data suitability=True; no new production activation |

Verification: [evidence1](https://api.github.com/repos/jfjelstul/englishfootball), [evidence2](https://raw.githubusercontent.com/jfjelstul/englishfootball/master/README.md), [evidence3](https://github.com/jfjelstul/englishfootball/commits/master), [evidence4](https://github.com/jfjelstul/englishfootball/commits/master/data), [evidence5](https://github.com/jfjelstul/englishfootball/commits/master/R). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.


### [SkillCorner/opendata](https://github.com/SkillCorner/opendata) — reject for current ScorPred league gaps; optional isolated format sample

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | SkillCorner; soccer; sample |
| Canonical location | https://github.com/SkillCorner/opendata |
| Datasets | Broadcast tracking and derived events; season physical aggregates |
| Seasons /competitions | 10 Australian A-League 2024/25 sample games; not EPL/Bundesliga/LaLiga |
| Latest verified dataset update | 2026-09-14T12:48:41.000+01:00; Latest inspected path commit |
| Latest verified code maintenance | Not separately verified; Not verified at checked path |
| Repository push /archival status | Not independently verified; archived=None (not dataset freshness) |
| Formats /access | JSON tracking/events; CSV season aggregates; sample formats vary; GitHub release assets or commit-pinned raw files; inspect manifests before any later download |
| Licence /permitted use | MIT file and open-data README; preserve attribution; file-specific scope must be checked before reuse |
| Automation restrictions | GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory. |
| Rate limits | GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline 5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified. |
| Historical availability | File/asset existence and declared season scope only; per-game completeness not measured without ingestion |
| Pregame timestamp reliability | Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only. |
| Missing-data limitations | Recent repository activity does not turn 10 sample games into broad current league coverage; inferred tracking positions and definitions differ |
| Existing integration /compatibility | not integrated; Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly |
| IDs /overlap | provider IDs need explicit crosswalk; Not measured; reconciliation required before import, no novelty assumed |
| Integration complexity | high |
| Expected improvement | Zero verified supported-league coverage increase |
| Score /bounded suitability | 47.0/100; bounded data suitability=False; no new production activation |

Verification: [evidence1](https://github.com/SkillCorner/opendata), [evidence2](https://raw.githubusercontent.com/SkillCorner/opendata/master/README.md), [evidence3](https://raw.githubusercontent.com/SkillCorner/opendata/master/LICENSE), [evidence4](https://github.com/SkillCorner/opendata/commits/master), [evidence5](https://github.com/SkillCorner/opendata/commits/master/data), [evidence6](https://github.com/SkillCorner/opendata/commits/master/R). Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.
