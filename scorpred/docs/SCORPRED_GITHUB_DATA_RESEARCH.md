# ScorPred Phase 3: GitHub sports-data research

Research observed **October 22, 2026 UTC / October 21 Eastern**. This is research and architecture planning. No sports records were imported, prediction logic changed, models fitted, migrations executed, or services restarted. The NFL final 109 outcomes were not queried. Repository documentation, licences, file/season manifests and a competition index were inspected; these are not imported match datasets.

## Decision

Repair coverage and provenance before adding predictors. Keep nflverse as the NFL data backbone. Investigate SportsDataverse's hosted NBA releases behind an explicit rights gate, starting with schedule and boxscore manifests. Repair soccer identity/season lineage with permitted results sources before considering selected event data.

**25 accessible repositories researched; 27 candidate URLs checked; 7 repositories suitable for explicitly bounded historical data work.** Two references returned 404: the requested `andrewbaisden/football-data` and an initial `nfeloapp/nfelo` lookup. The actual NFL project `greerreNFL/nfelo` was subsequently researched and rejected as an additional model implementation. Counts are reproducible in [sources.json](assets/github_data_research/sources.json).

The 7 are **nflverse/nflverse-data**, **openfootball/football.json**, **openfootball/england**, the **footballcsv England/Germany/Spain** historical mirrors, and **jfjelstul/englishfootball** under its share-alike conditions. These represent **three publication families**, not seven independent ground truths. Five belong to the same openfootball/footballcsv family. Only the NFL release host is already integrated. Suitable here means an identified historical use under documented conditions; it does not mean current freshness, complete rows, legal warranty, model approval or unrestricted production use. All proposed new production integrations remain disabled.

## Existing system, with dated denominators

The seven original diagnostics were read before external research. They describe the October 20 audit, not necessarily today's loaded source. Stage 1 and the later local launch corrected holdout boundaries, missing freshness, unsupported prop mappings, five/ten-match parity, the soccer preparation path, NBA overview semantics and the ESPN machine-status/display-text mix-up. See [Stage1](SCORPRED_STAGE1_REMEDIATION.md). Remaining gaps in the original reports are not all still unfixed bugs.

| Sport | Already possessed | Missing/incomplete | Avoid redundant acquisition |
|---|---|---|---|
| NFL | Current metadata:90 dataset/season partitions; schedules, PBP and player/team stats 2022–26; EPA/success and derived team/QB records; directories; multiple roster, injury, depth, NGS, PFR, snap and FTN feeds | Many auxiliaries have 2022/24/26 but no 2023/25 partitions; observations mostlyOct 18; team feed 88 vs 93 completed games in audit;2026 participation intentionally absent; advanced historical availability unproved | Do not reimport existing core seasons or add another library for identical releases |
| NBA | Audited 6,048 eligible ESPN monthly games ending 2022–26,2022 partial; public scoreboards, schedules, summaries, rosters, form/rest logic and descriptive player overview; separate scoring artifact | No complete durable player/team-box or possession ledger; real-ID prop logs unsupported; point-in-time injuries/announced lineups absent; current winner rules lack matching validation | Broader ESPN release results overlap existing archive and are not independent schedule confirmation |
| Soccer | Audited 7,888 unique classifier fixtures,130 teams,2021-08-08–2026-04-19; current 3-league adapters; goals/results/form/Elo/rest; Stage 1 version has 2,599 unique legacy results | Classifier rows lack league/source lineage; genuine 2018 EPL missing;2025 rawEPL319/380; audited cached previous-season fixtures six below capacity in each league; xG/market/referee inputs not demonstrated; injuries/props incomplete | Valid complete 2019–24 EPL seasons and existing 3-league results should be reconciled, not blindly duplicated |

The [local inventory](assets/github_data_research/existing_inventory.json) was captured read-only using only NFL `datasets` metadata and aggregate receipt dates. It also records the actual current PBP projection, collector configuration, prior audit hash references and Stage 1 preparation manifest. NFL row counts are metadata counts, **not permission to expose held results**. NBA/soccer numeric counts above remain explicitly the prior audit's denominators. No new full coverage scan or season recount was performed.

Current source lists `no_play` in the PBP projection; the earlier audit found it absent from inspected stored payloads. The current values were not re-audited. CPOE is not in the current projection. Do not silently present the old missingness estimate as a current source-schema defect.

At the new local health observation **2026-10-22 01:56 UTC**, the app returned 503/degraded: NBA and soccer refresh were ok, while ML refresh was degraded and its last success was 344 minutes old. The NFL collector separately remained active with its existing 240-second schedule and 21,600-second PBP intervals. The previous launch's healthy check was a point measurement, not an uptime guarantee. This research did not modify or restart these processes; StageA operational acceptance remains necessary.

## Best candidates by sport

| Sport | Ranked practical choice | Decision |
|---|---|---|
| NFL | 1.nflverse-data ordinary licensed releases; keep existing nflreadpy; consult roster/player producers for definitions | Only one recommended data family. Extend existing loaders/ownership selectively; do not invent three independent sources |
| NBA | 1.SportsDataverse ESPN release family;2.NBA Stats producer family;3.Shufinskiy's bulk archive | All conditional. First is closest to current ESPN identities; second has deeper documented historical boxes; third offers older events but uncertain upstream rights and horizon. nba_api is a schema/client reference, not an independently licensed data archive |
| Soccer | 1.openfootball/football.json;2.Fjelstul English Football;3.openfootball/england | Historical reconciliation only. Fjelstul requires share-alike compliance; JSON/current text feeds are volunteer-maintained and stale for live fallback. footballcsv is a dated mirror. StatsBomb is a separate restricted research candidate, not a current 3-league replacement |

Full source-by-source details: [NFL](NFL_GITHUB_DATA_SOURCES.md), [NBA](NBA_GITHUB_DATA_SOURCES.md), [soccer](SOCCER_GITHUB_DATA_SOURCES.md). Each includes the 18 requested assessment categories and its gap matrix. Every source is also represented in [sources.csv](assets/github_data_research/sources.csv); 39 dataset gaps are in [gaps.csv](assets/github_data_research/gaps.csv). The [prior 280-entry feature inventory](../cache/diagnostics/2026-10-20_multisport/feature_inventory.csv) provides the measured field-level source, calculation, missingness and use evidence; its audit date remains explicit.

## Scoring and permission gates

Scores use 0–5 per dimension:0 absent/unverified,1 poor,2 limited,3 useful/qualified,4 strong and 5 excellent within the declared scope. Weights: coverage 15,reliability 15,freshness 10,statistical depth 10,provenance 15,licensing suitability 15,integration ease 5,and value to verified gaps 15. Total is `sum(weight*score/5)`, out of 100. Ease 5 means low integration complexity. These are engineering judgements, not estimated accuracy or statistical tests. Missing evidence is penalized. Dataset age, code activity and repository push time remain separate fields.

A technical score never overrides a failed permission, competition, timestamp or holdout gate. The ESPN producer scores 80, but that is **not** an unconditional recommendation. A sample from Spanish ACB cannot fill NBA gaps;10A-League tracking matches cannot fill EPL/Bundesliga/LaLiga gaps. Clients and producer pipelines are not counted again as independent datasets.

GitHub REST generally allows 60 unauthenticated requests/hour/IP and a 5000/hour authenticated baseline, with additional secondary limits. Raw/release downloads have no universal numeric quota verified here. Stop on 403/429, honorRetry-After, cache metadata and do not evade blocks. Repository restrictions and upstream terms are separate. [GitHub limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).

Current nflverse policy describes ordinary releases asCC-BY4.0 and FTN data asCC-BY-SA4.0, with per-dataset exceptions. Keep attribution and rights attached to each dataset version. This differs from older blanket descriptions; the retained organisation policy and release evidence are the relevant current sources. [nflverse licensing](https://github.com/nflverse#licensing).

NBA and ESPN-derived candidates retain a permission gate; MIT client code and a third-party repository licence are insufficient to establish ScorPred's proposed data rights. StatsBomb's actual PDF agreement, not just its README attribution sentence, governs its restricted research lane. Openfootball's CC0 dedication supports historical results reuse; its own support page promises no refresh schedule. [NBA assessment](NBA_GITHUB_DATA_SOURCES.md), [StatsBomb agreement](https://github.com/hudl/open-data/blob/master/LICENSE.pdf), [openfootball support](https://github.com/openfootball/help).

## What the evidence can and cannot establish

Release `published_at` often dates creation of a reusable release tag years ago; it is not the latest dataset update. Per-asset `updated_at`, asset identity/digest and the inspected path's commit are recorded separately in [release manifests](assets/github_data_research/release_manifests.json). Even a fresh asset timestamp does not prove the date of its newest game or historical pregame publication. A new commit can be documentation only. README claims and independent metadata checks are labelled separately, including contradictory dormant/active NBA status information.

No row-level net-new counts, cross-provider conflict rate or identity-match percentages were measured: no datasets were imported. Claimed gains are specific, testable scope hypotheses. More rows do not imply better predictions. Neither the NFL385-game results, the soccer classifier replay nor the NBA score-model MAE is new evidence for a proposed source or winner model.

The [architecture](SCORPRED_DATA_INTEGRATION_ARCHITECTURE.md) adds a provider registry, immutable raw versions/receipts, identity crosswalks, conflict quarantine and availability-aware views around the existing adapters. The [roadmap](SCORPRED_GITHUB_INTEGRATION_ROADMAP.md) gives measurable StageA–E acceptance criteria.

**Exact next implementation task:** create a disabled NBA historical-provider registry plus a metadata-only season/asset reconciliation manifest against the existing 6,048-game archive. Record permitted-use decisions, ending-year/competition mappings, source lineage, release IDs/digests and proposed boxscore field contracts. Do not fetch sports assets until that source's rights gate is resolved. In parallel close the observed ML refresh failure under existing operational controls. Models and the NFL final 109 stay untouched.

## Reproduction and preservation

Evidence is retained under [assets/github_data_research](assets/github_data_research/). `repository_evidence.json`, `supplemental_evidence.json`, `technical_evidence.json` and `additional_candidates_evidence.json` retain bounded responses, URLs, observation times, response hashes and failures. The StatsBomb licence PDF/text was inspected locally; only a small PDF-reading tool was installed into ignored `.tmp`, not application dependencies.

The research helpers refuse to overwrite initial network/local snapshots. To repeat collection, use a new output run rather than silently replacing evidence. The deterministic builders regenerate reviewed CSV/JSON exports from those snapshots:

```powershell
& .\.venv\Scripts\python.exe -m scripts.build_github_research_exports
& .\.venv\Scripts\python.exe -m scripts.build_github_gap_exports
& .\.venv\Scripts\python.exe -m scripts.render_github_source_profiles
& .\.venv\Scripts\python.exe -m scripts.verify_github_research
```

Research validation checks all required fields, score arithmetic, classifications, repository counts, cross-file references, stored response hashes and the 207-file preservation baseline. Existing backend 735 tests/browser 25/build results belong to the preceding local-launch task; they are not rerun model or uptime evidence for this research. No production code changed here, so broad app tests are not rerun merely for documentation generation.
