# GitHub data integration roadmap

Phase 3 research proposal, observed October 22,2026 UTC / October 21 Eastern. No stage is activated by this document. Preserve existing data, architecture, source fingerprints, models and archived forecasts. Keep research models disabled; no deployment or new model family is proposed.

## Order and gates

**A: verify existing repairs/operations → B: NBA metadata and rights → C: soccer identity/results → D: selective existing NFL feed completion → E: prove data improvements.** Rights review, source manifests and receipt design can run alongside StageA. If NBA permissions remain unresolved, continue permitted soccer/NFL work without substituting unauthorized collection.

Effort ranges below are rough engineering estimates after dependencies are available, not promises of elapsed calendar time. Prospective evidence requires future games and cannot be accelerated by retrospective timestamps.

## Stage A — Repair and stabilize what already exists

**Dependencies:** prior audit, Stage 1 source repairs, exact running-owner identity and frozen NFL compatibility manifest. **Estimate:**1–3 engineering days for remaining acceptance/operational work; network/provider failures may add elapsed time.

The holdout boundary, fresh-input rejection, unsupported prop mappings, soccer window/target-date parity, versioned preparation, NBA overview/defaults and ESPN status-code correction have already been implemented. Do not reapply a migration or regenerate a frozen identity as if those were still unstarted. Verify the current source/version and reuse synthetic regressions.

The latest Phase 3 health snapshot returned 503: ML refresh degraded with last success 344 minutes old, while NBA/soccer collection was ok. A previous healthy launch was a point observation. Diagnose the current failing stage from real receipts/errors without reading NFL sealed outcomes or stamping cached data fresh. No app restart or collection change was made in this research.

Acceptance:

- All ordinary NFL readers and future-provider paths remain guarded; synthetic held sentinels and unknown-ID/aggregate cases are denied. Preserve the 109-game protocol and frozen model/study/input/forecast identities.
- Exactly one app scheduler owner and one NFL writer; retain duplicate-start rejection, attempts, errors and actual receive times. Verify successful scheduled recovery after failure, then several declared intervals; one HEAD or one healthy response is insufficient.
- Align source-expiry suppression, refresh cadence and health status. Persist history across restarts rather than infer uptime from process memory.
- Keep original soccer files unchanged and the new 2,599-row version unpromoted. Preserve legitimate zero/null distinctions and unsupported NBA/soccer markets.
- No new source, model change or historical timestamp rewrite used to make an acceptance check pass.

## Stage B — Expand NBA historical evidence, after permission

**Priority:** highest new-data investigation. **Estimate:**1–2 days for metadata/rights/field contracts, then 3–5 days for a bounded research adapter after source eligibility is documented.

**B1, the exact next implementation task:** build a disabled NBA historical-provider registry and metadata-only reconciliation manifest. Compare existing monthly archive scope against the inspected SportsDataverse ESPN schedules/player/team-box release manifests. Record requested competition, ending-year convention, asset ID/URL/update/digest, code/data rights, source family and proposed field definitions. No sports assets or direct NBA/ESPN queries until the selected rights gate is resolved.

Concrete B1 deliverables:

1. A versioned research registry with `collection_enabled:false` and `serving_enabled:false`, purpose-specific permission state and evidence links.
2. A declared existing reference universe from the retained 6,048 eligible games, preserving partial 2022, playoff/Cup/play-in policies and original ESPN IDs.
3. A metadata comparison for ending years 2022–2026 first; older 2002–2021 scope is a subsequent measured expansion, not an automatic bulk download.
4. A reviewed schema for real player/team boxes, minutes, paired counts, nullable DNP/components and official-versus-estimated metrics. Preserve NBA Stats ID strings and plan an evidence-backed ESPN crosswalk.
5. An explicit stop result for unresolved permissions, stale assets, ambiguous season conventions or unavailable team/player detail. Do not replace a blocked feed with a similarly named scraper.

After B1 passes, a separately scoped, permitted asset pilot can use one completed historical season in an isolated research store. Select the season before examining prediction performance. Preserve raw bytes/receipts and reconcile to a declared source universe. Add no automatic production fallback and do not fabricate real-ID props from averages.

Acceptance for the later pilot: explain every missing/extra eligible game; zero silent conflicting/duplicate canonical events; at least 99% identity match is a proposed promotion target, with all remaining cases quarantined and enumerated, **not a measured present result**. Report fields with paired team coverage and valid player-minute denominators. If the target is missed, keep the source isolated. Historical source versions remain ClassB unless genuine availability evidence is supplied.

Do not add new winner predictors or fit models simply because advanced boxscore fields exist. Validate existing rules and their actual inputs only after the provenance and holdout requirements in StageE.

## Stage C — Repair and expand soccer identity/results

**Dependencies:** preserved Stage 1 files, verified licences, source competition/season manifests and alias policies. **Estimate:**3–5 engineering days for a bounded three-league reconciliation.

Start with the genuine 2018/19 EPL gap. Use openfootball results, or an isolated Fjelstul share-alike research version if that compliance decision is accepted. Enumerate actual files and fixtures before claiming 380 recovered games. Do not touch existing complete 2019–24 seasons merely to increase row count.

Reconcile the partial 2025 EPL raw file and the previous audit's six cached gaps per supported league; the 61 raw EPL deficit and 18 cache deficits can overlap and are not promised additions. Version the crosswalk into the 7,888-row classifier source only where event/competition/season linkage is independently evidenced. Do not infer leagues from names. Keep unmatched and multiple-match candidates visible.

Acceptance: exact season/source IDs for newly accepted records; zero conflicting duplicate fixture identities; source hashes unchanged; explicit extra-time/shootout/timezone semantics; mapping and missingness rates per league/season; clear before/after counts from the same eligible universe. Final standings cannot enter prior-date features. Retain current model artifacts and signatures; no retraining.

Selected StatsBomb events are a later restricted research lane only after its usage/registration/publication conditions are addressed. They do not provide current complete EPL/Bundesliga/LaLiga xG, injuries or lineups. No restored prop market without original field-definition and real-player coverage proof. Tracking samples from other leagues are not a route to expanding current prediction scope.

## Stage D — Improve NFL coverage through the existing pipeline

**Dependencies:** StageA guard/ownership acceptance, source licences and frozen-source compatibility. **Estimate:**2–4 engineering days for targeted auxiliary coverage/refresh work; not a rewrite.

Use existing nflreadpy/store contracts. First enumerate missing 2023/2025 auxiliary partitions and the 88-of 93 team-feed gap. Refresh existing 2026 auxiliaries through one owned job with per-feed receipts/backoff and actual source ages. Regenerate derived team/QB datasets only from permitted, eligible prior-game inputs, preserving original versions.

Do not import core schedules/PBP again merely because another producer repository exposes the same data. Do not install internal player/roster packages. Resolve explicit ID links through existing directories; retain unresolved IDs. A projected-field change, such as a later CPOE schema review, requires separate source/version approval and is not a reason to add a predictor.

FTN adaptations and post 2022 participation need an explicit share-alike decision.2026 participation has no verified in-season asset; do not repeatedly mark that expected absence as an ingestion defect or scrape another source to bypass it. Historical participation supplied after a postseason cannot enter predictions earlier in that same season.

Acceptance: newly usable allowed games/years reported per feed, missingness denominator and linkage rate, source update/receive lag, no duplicate writer, idempotent retries, old row versions unchanged, and synthetic sealed-ID/unmatched-provider/season-aggregate denial tests. Frozen source drift still fails closed. No actual final 109 outcomes inspected.

## Stage E — Validate data improvement before model experiments

**Dependencies:** versioned permitted pilot, immutable raw/receipts, canonical IDs, stat definitions and fixed eligibility/availability policy. **Estimate:**2–3 engineering days for a reproducible data-quality report, plus future collection time for ClassA validation.

Every integration must publish the following with the **same predeclared eligible denominator**:

| Measure | Required report / acceptance |
|---|---|
| New usable records | accepted minus records already represented under the canonical key; raw rows are not all usable |
| Games/seasons/competitions | before/after counts, partial-season boundaries and enumerated exclusions |
| Missing fields | per-field numerator/denominator and reason, including paired-team and complete-component coverage |
| Stale inputs | age/TTL distributions, reduction in expired observations and actual unsuccessful attempts |
| Identity match | mapped/unmapped/ambiguous counts, evidence/version and manual-review queue |
| Duplicates/conflicts | exact duplicates, conflicting versions, cross-source matches and quarantine; zero silent conflicts |
| Pregame quality | ClassA/B/U counts, all dependency receipts/cutoffs, lag assumptions and missing availability |
| Reproducibility | raw/source hashes, schema/parser/definition versions, deterministic normalization and replay result |
| Preservation | no modified original observations or frozen forecast/model/protocol identities; live legitimate append operations identified separately |
| Permission | dataset-specific scope, attribution/redistribution/adaptation conditions and unresolved gates |

Only then propose an experiment on the **existing** appropriate method: matched chronological train/calibration/test windows, train-only home/three-way priors and existing Elo/rule baselines, log loss/Brier/accuracy, classwise calibration with counts, paired week/matchweek uncertainty, and season/league consistency. Freeze the primary endpoint and genuinely untouched final period before outcomes are viewed. No exploratory use of the NFL final 109. No new family or production promotion is authorized by a successful coverage report.

## Stop conditions and risks

Stop a source before sports-data import when its purpose-specific rights remain uncertain, its season/competition namespace is ambiguous, its manifest is incomplete, or it would bypass NFL holdout controls. Keep metadata and a documented blocked result; do not manufacture a third candidate or download rows merely to finish a quota.

Historical additions can improve coverage without improving forecast quality. Revised results, selection-biased event samples, wrong competition universes, end-season aggregates and missing original timestamps remain material threats. The plan deliberately measures them before any model claim.

The recommended next stage is **B1 metadata/permission/canonical-schema work**, alongside completing the already observed StageA operational recovery. The outcome to review is a concrete disabled provider/manifest contract and resolved access scope, not a new model or deployed prediction change.
