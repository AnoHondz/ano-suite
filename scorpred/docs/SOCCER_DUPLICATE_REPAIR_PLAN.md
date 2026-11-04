# Soccer duplicate-fixture repair preparation

The read-only inventory confirms **2,918 legacy processed rows, 2,599 unique fixtures and 319 excess rows in 319 two-row groups**. Each group has identical retained result/statistic observations and differs only in `Season`: `E0_2018.csv` versus `E0_2025.csv`. There are **zero score/statistic-conflict groups**, but all 319 have conflicting season/source labels. No records were deleted, merged or rewritten.

[The complete issue manifest](assets/rights_soccer/soccer_duplicates.json) enumerates **every group**: date, oriented teams, explicit source competition, season candidate, both processed CSV line numbers, exact original raw-row locators, differing fields, observation fingerprints and downstream candidate references. CSV line numbers include the header. A synthetic or local row locator is not an upstream match identifier.

## Source and identity evidence

`data/raw/E0_2018.csv` and `E0_2025.csv` are byte-identical, SHA-256 **4cb7f75e1780ba7cb55664b750de3ef19d8d9733fea38b96722eb88f7487ac05**. Every group maps exactly to both files under the retained observation columns. Raw `Div=E0` establishes the local source competition code; filename/date validation and the existing Stage 1 acceptance manifest support **2025–26**, not 2018–19. The original provider match ID, source download URL and original receipt time are absent. Football-Data format/source-family attribution is documented locally; it does not recover an original HTTP receipt or settle future acquisition rights.

For example, Liverpool–Bournemouth on August 15, 2025 is at processed lines **2 and 2,601**, raw line **2** in each aliased source, and Stage 1 line **2,282**. Its observations are identical; its source identifiers disagree. The other 318 groups are explicitly listed, rather than assumed to behave the same from a sample.

Future canonical identity should include competition, validated season, date, oriented source team identity and phase/provider match ID where available. The present exact-date/name key diagnoses this particular concat defect. It is not a universal merge rule for cup matches, rescheduling or cross-provider identity.

## Which pipelines reference the observations

| Path | Verified relationship and limitation |
|---|---|
| Legacy `data/processed/matches.csv` | Holds both copies; 319 groups/638 member rows. No current direct production classifier consumer was found in the inspected Python/YAML paths |
| `prepare_dataset.py` | Already validates raw dates and writes the separate `matches_stage1.csv`; it rejects/quarantines the mislabeled file by manifest. Do not reapply this repair or overwrite the existing output |
| Existing Stage 1 version | 2,599 rows, zero duplicate excess; **all 319 groups** have exactly one linked fixture here; it remains unpromoted |
| `scripts/train_scoring_models.py:soccer_games` | Reads validated raw E0 files, excludes the mislabeled 2018 file and accepts 2025 once. The saved soccer scoring report references the accepted raw source hashes. This does not prove every fixture was a training target after warm-up/window filters |
| Primary classifier / production trainer / chronological backtest | Read `data/historical_matches.csv`, with 7,888 distinct fixtures, not the legacy concat. Only **74** duplicate-group identities exactly match classifier date/names/scores; these are shared-fixture candidates, not duplicate classifier rows or proof of importer origin |
| Derived classifier analysis | Reads the separate 7,586-row clean-feature dataset; group-level exact identity links are recorded. Alternate provider spellings are not automatically merged |
| Weekly preparation wrapper | Its documentation still says legacy concat, while it calls the current versioned preparer. An existing default Stage 1 output causes the preparer's intentional no-overwrite error. This is an orchestration/documentation issue to plan separately; no job was run or changed |

## Proposed additive remediation

1. Keep both original raw files and the legacy concat immutable. Preserve all source/row fingerprints and the already accepted Stage 1 version. Record the mislabeled file's alias/quarantine relationship; do not rename it into a fabricated 2018/19 archive.
2. Review any downstream consumers before choosing a new research selector. If another prepared version is required, generate a new named version and provenance sidecar, using the existing accepted-source/date/result validation. No automatic promotion or classifier retraining follows.
3. Maintain one canonical research fixture per valid source key with all raw locators. Conflicting observations go to an issue queue. Identical scores alone are insufficient to merge unrelated provider records.
4. Record true 2018/19 as a missing season and 2025/26 as partial; obtain any later additional results only from a separately permitted source. No mirror bypasses unresolved acquisition terms.

Acceptance: same **2,599** canonical fixtures as the retained Stage 1 version, zero duplicate canonical keys, zero silent conflicting payloads, exactly 319 source-alias exceptions enumerated, complete original locator coverage, unchanged original hashes and unchanged model/forecast artifacts. Fixture order and feature chronology must remain deterministic. A version-selection change requires its own validation and authorization.

Prepared tests cover exact duplicates, season-label conflicts, conflicting score observations, different-date fixtures, absent source metadata, deterministic exports and no overwrites. The existing Stage 1 tests continue to check quarantine and preservation. [The verification report](assets/rights_soccer/verification.json) records the actual regression results. This task implements diagnosis and plans only.
