# Soccer historical lineage and statistic-contract preparation

The classifier source has **7,888 fixtures**, with date, home/away names, home/away goals and result. It has no league, season, source event ID, import batch or receipt fields. No original row was enriched or rewritten. [The per-fixture manifest](assets/rights_soccer/soccer_lineage.json) records candidate evidence separately from nullable original provenance.

## What local evidence can recover

| Classification | Fixtures | Meaning |
|---|---:|---|
| Unique exact fixture-metadata candidate | **895** | Same UTC date, both oriented team names and both final scores; one ESPN event ID and one explicit competition, with no detected contradictory cached identity/result version |
| Raw CSV fixture candidate only | **291** | Same date/names/scores in a validated local E0 raw source, but no independently retained source event ID |
| Unrecoverable from inspected fixture metadata | **6,702** | No qualifying exact match; this is not proof the event is absent upstream |
| Verified original per-row provider import lineage | **0** | Candidate agreement does not establish which original source/version was imported or when |

The 895 metadata candidates have explicit ESPN competitions: **323 English Premier League** (`700`, `eng.1`), **306 Spanish LALIGA** (`740`, `esp.1`) and **266 Bundesliga** (`720`, `ger.1`). Their event season metadata says **2025–26**. Those ESPN competition IDs are not ScorPred/API-Football IDs 39/140/78. This is a bounded candidate subset, not a recovered full training league denominator.

Matching uses current local ESPN response files plus 16 already local Git cache blobs from the importer commit. Explicit event/scoreboard competition metadata may be linked by the same source event ID. Team-name membership, calendar-based season guesses, accent stripping and fuzzy aliases do not assign competition. Repeated conflicting identities/results or multiple event IDs stay ambiguous. Missing IDs or competition leave an incomplete candidate, not an accepted mapping. Exact candidate fixtures are still subject to source-truth and identity review.

## Dataset-level and internal lineage

The current historical CSV is byte-identical to the file in commit **c26606d50f1aedca63ec5da897f05701529b887c**, whose recorded author time is April 19, 2026 22:39:33 -04:00. That commit adds `scripts/fetch_training_data.py`, which documents ESPN and converts normalized fixtures to exactly the six retained columns, dropping competition, season, source event/team IDs and receipts. This supports a **documented dataset-level ESPN producer lineage**, not proof of original request execution or a recovered per-row import batch. The source mentions five leagues and four season arguments; their presence does not assign individual rows or resolve start/ending-year semantics. [Git evidence](assets/rights_soccer/git_lineage_evidence.json) preserves file hashes and version references.

All **7,586 existing derived clean-feature rows** link uniquely back to a historical source row by exact date, oriented names and result. This recovers an internal file/row relationship. It does not establish original provider IDs, individual train/test membership or historical availability. The remaining 302 historical rows have no derived-row link in that saved feature file; they must not be assigned fabricated downstream identities. No feature builder or model was rerun.

The current cache's `_scorpred_source.observed_at`, where present, concerns a later response observation. Git author time is a repository timestamp. Neither is the classifier's original receive time. Filesystem mtime, match date and present-day source age cannot supply that time. The original match ID, batch and historical availability remain null/unverified for **all 7,888** rows; a candidate event ID stays in a separate namespace/evidence field. No old Class A pregame record was manufactured.

## Proposed lineage repair

Create an additive reviewed crosswalk sidecar keyed by original CSV hash + physical row locator. Give each field its own state: `verified_original`, `verified_internal_relationship`, `candidate_inferred`, `ambiguous`, or `unrecoverable`. Preserve the current CSV and artifact hashes.

Review the **895** uniquely nominated events first, retaining source IDs, explicit competition/season, matching method, observed version and candidate status. Review the **291** raw-only matches independently; their local E0 identity is evidence about those source rows, not proof the classifier came from that provider. Name aliases need evidence and validity periods before use. Leave the **6,702** unmatched rows unknown rather than propagating a club's current league backwards. No original timestamp or batch is filled without a genuine receipt/log.

Future permitted ingestion must capture provider URL/namespace, original event/team IDs, competition/season/stage, actual batch/receipt time, raw hash/row locator, source publication/revision times where supplied, parser/stat-definition versions and rights decision. Postgame inputs can support later reconstructed games only with a stated lag policy. This task creates no collector or retrospective pregame proof.

Acceptance: classify all 7,888 exactly once; 100% of claimed original fields have direct evidence; no name-only or ambiguous automatic assignment; no nonunique candidate acceptance; all 7,586 internal derived links accounted for; original receipt fields remain null where unknown; unchanged inputs/models; no new league-specific accuracy claim until the relevant identities and evaluation scope are established.

## Statistic-contract findings and exact repair scope

[The contract/issue manifest](assets/rights_soccer/soccer_stat_contracts.json) defines goals, shots, shots on target, corners, fouls, cards and xG, with **12 issues and 16 proposed acceptance cases**. [Synthetic probe evidence](assets/rights_soccer/statistic_probe_results.json) ties the findings to source hashes and demonstrates three residual defects without importing a provider module or changing production calculations.

| Category | Finding | Planned repair/test |
|---|---|---|
| Goals | Legacy player-row builder converts unknown goals/assists to zero. Importers accept FT/AET/PEN but omit explicit result-period scope | Preserve null versus genuine zero and require all composite components; store regulation/extra-time/shootout separately |
| Shots / on-target | ESPN overview defaults missing totals to zero; normalized fixture form has no ESPN team-stat payload. On-target aliases require a defined provider scope | Nullable nonnegative integer counts; compatible SOT ≤ total; zero retained; absent overview/fixture fields stay unavailable |
| Corners | Legacy `predictor.predict` returns constant **10.2** and derives `over95_corners` from goal totals | No corner output without valid corner observations and a validated corner method. Goal-only fixtures must produce unavailable corner statistics |
| Fouls | HF/AF exist in local raw data; form extraction does not supply committed/drawn fields | Keep committed versus drawn distinct; no unknown-to-zero conversion or undocumented alias |
| Cards | Overview missing values and opponent card averages can become hardcoded zero; subtype/team/player definitions are incomplete | Nullable subtype counts; explicit second-yellow/direct-red and bench/staff scope; no blind total or fabricated per-game denominator |
| xG | Historical importer labels floating `score.home/away` as xG; missing training xG becomes zero; legacy `xg_a/xg_b` are normalized score-lambda shares | Use only explicit, documented provider/model/period expected-goals fields; preserve missing and real zero; keep model attack shares separately labeled. No retraining here |
| Residual player aliases | `api_client._build_game_row` still maps blocks→clearances, all duels→aerial wins, total passes→completions, key passes→broader chances | Remove unsupported aliases from remaining legacy paths in a later repair; preserve the already implemented props-engine denylist |
| Parsing and scope | Generic numeric parsers permit inappropriate count values and strip percent signs; xG display aliases omit an explicit underscored provider form | Add metric-specific finite/range/integer/unit checks only after provider contract verification; cover missing/zero, negative, NaN/Infinity, fractions, percentages and period mismatch |

The primary current winner engine is separate from legacy `predictor.predict`; these findings do not prove the corner proxy feeds current winner predictions. Credentialed fixture-stat/xG coverage was not verified. API-Football documentation yielded no usable schema text, and Football-Data's notes URL returned HTTP 429; neither was retried. Definitions that lack authoritative evidence stay provisional. Counts/percentages, per-game/season/per-90 quantities and regulation/extra-time scopes must never share a canonical field implicitly.

The next implementation should add a reviewed lineage sidecar and repair the residual **null/unsupported statistic contracts** under versioned tests, while keeping the existing model and predictions unpromoted. The 319-row concat issue already has a clean Stage 1 preparation path; preserve it. NBA sports-data acquisition remains independently blocked on its rights/field-contract gate.
