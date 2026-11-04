# NBA historical reconciliation: metadata only

The offline scan reproduces the audited **6,048 unique eligible games** from **6,541 raw events in 48 existing monthly files**. It excludes 483 incomplete/other-season-type observations and 10 non-NBA-team events. There are zero duplicate eligible observations or conflicting identities/results. No existing record was changed.

Evidence: [reconciliation JSON](assets/nba_registry/reconciliation.json), [local game identities](assets/nba_registry/local_game_index.json), [season CSV](assets/nba_registry/season_coverage.csv), [input file hashes](assets/nba_registry/input_manifest.json), [candidate asset metadata](assets/nba_registry/candidate_manifests.json). These exports contain identities and structural coverage, not downloaded player statistics or model inputs.

## Existing reference and local field gaps

The same eligibility as `scripts/train_scoring_models.py:nba_games` is reproduced without importing or invoking the trainer: completed provider season types 2/3, NBA team IDs 1–30 and positive finite final scores. Existing Cup/play-in/provider classification is preserved. A type-2 total of 1,231 is not automatically a duplicate. This reference is an ESPN archive, not an independent complete NBA fixture universe.

| Ending season | Eligible games | Provider type 2 / 3 | Locally cached paired team/player boxes | Games passing player-minute presence check |
|---|---:|---:|---:|---:|
| 2022 | 790 | 703 / 87 | 0 | 0 |
| 2023 | 1,314 | 1,230 / 84 | 71 | 71 |
| 2024 | 1,313 | 1,231 / 82 | 71 | 69 |
| 2025 | 1,315 | 1,231 / 84 | 72 | 68 |
| 2026 | 1,316 | 1,231 / 85 | 68 | 64 |
| **Total** | **6,048** | **5,626 / 422** | **282** | **272** |

The 2022 reference starts January 1, 2022 UTC. October–December 2021 is outside the retained monthly archive, so that season is partial. The count of absent games cannot be verified from file-level candidate manifests. Earlier seasons are outside this archive; 2023–2026 completeness also lacks an independently reconciled competition-specific denominator. No exact upstream game deficit is claimed.

We inspected **502 existing NBA public-response files**. Only 282 summary IDs match eligible historical reference games, with box structures for both expected ESPN team IDs. Thus **5,766 reference games lack paired box structures in this local response cache**. This is a measured local-cache gap, not a claim those games lack boxes upstream or are missing from the 6,048 results.

Minute presence passes for 272 games: both teams have player rows, and every row not marked DNP has a valid nonnegative numeric or minute:second value. Genuine zero is accepted; DNP is not converted to zero. Ten rows in ten other games have unknown minutes despite `didNotPlay:false`; eight have explicit player IDs and two lack IDs. These provider flags do not prove actual participation. [The JSON inventory](assets/nba_registry/reconciliation.json) enumerates the rows. **5,776 games lack paired usable minute presence in this inspected cache**, including the 5,766 without paired boxes. A missing-player count across all historical games is **unknown** because complete historical rosters were not established.

These are structural presence checks. Paired totals, complete rosters, DNP semantics, overtime totals, statistic definitions and advanced metrics have not been validated. Cached fragments are not a validated player-log contract for props.

The earlier audit's 1,357 monthly-reference events absent from its lazy live event cache (790/141/140/142/144 by season) are retained separately as a dated comparison. They concern all cached event references, unlike this narrower box-summary check. Neither number represents missing historical results.

## Candidate coverage and potential overlap

All dates below refer to retained metadata observed October 22, 2026 UTC. A file existing is not proof of any particular row, complete season or available pregame field.

| Candidate dataset | Recognized files | Ending seasons represented by file names | Evidence level |
|---|---:|---|---|
| SportsDataverse ESPN schedules | 102: three formats × 34 seasons | 1994–2027 | Release asset metadata |
| ESPN player boxes | 102: three formats × 34 seasons | 1994–2027 | Release asset metadata |
| ESPN PBP | 78: three formats × 26 seasons | 2002–2027 | Release asset metadata |
| ESPN team boxes / game rosters | Unknown | Unknown | Manifests absent |
| NBA Stats traditional player/team boxes | Unknown independently | 1997–2026 claimed | Producer documentation only |
| Other registered NBA Stats datasets | Unknown | Unknown | Manifests absent |
| Shufinskiy `cdnnba` | 12 | 2021–2026 | Text file index; regular/playoff separate |
| `datanba` | 18 | 2017–2025 | Text file index |
| `matchups` | 18 | 2018–2026 | Text file index |
| `nbastats` | 58 | 1997–2025 | Text file index |
| `nbastatsv3` | 60 | 1997–2026 | Text file index |
| `pbpstats` | 50 | 2001–2025 | Text file index |
| `shotdetail` | 60 | 1997–2026 | Text file index |

ESPN's three inspected datasets potentially overlap all five local seasons. Schedules/player-box files advertise 28 earlier ending seasons, 1994–2021; PBP advertises 20, 2002–2021. These are possible coverage opportunities, not new-game counts. The 2027 files likewise do not establish completed eligible games or current-season completeness.

NBA Stats' documented traditional boxes potentially overlap 2022–2026 and describe 25 earlier seasons. Their published built/known totals include a different competition universe; subtracting them from ScorPred's counts would produce unsupported deficits. The hub also hosts the NBA Stats producer's outputs: this is not another independent provider copy to count twice.

Shufinskiy start-year keys are converted using the README's explicit 1996 → 1996/97 examples. Regular/playoff files and seven variants may contain the same games. WNBA entries are excluded. Some 2025-start keys imply file-level 2025/26 coverage while other variants stop earlier. The latest actual content update remains unknown.

**No candidate game-level matches, missing IDs, exact overlaps or net additions are verified.** Machine-readable values remain `null`, not zero or inferred full-season counts. ESPN namespace compatibility is promising; official NBA IDs and other variants still need explicit crosswalk evidence. No joining by player/team names or by numeric resemblance occurred.

## Pregame provenance and blocked work

None of the 6,048 reference games has verified original pregame receipt proof in the inspected sources. Current file dates, release updates and final-game dates cannot supply it. Missing injury/announced-lineup history stays unknown. No new predictions, calibration results or accuracy improvements are claimed; no model was trained or enabled.

All sources remain blocked for sports-asset use by [the permission gates](NBA_SOURCE_PERMISSION_MATRIX.md). The exact next step is to resolve rights for the ESPN family and obtain the missing team-box release manifest as metadata. After an affirmative permitted-use record and separate pilot scope, predeclare **ending season 2023**, an entirely retained local season, for one isolated schedule/box identity-and-field reconciliation. Explain every extra/missing competition before proposing any model experiment. No pilot download is authorized by this completed B1 work.

Verification and preservation results are recorded in [verification.json](assets/nba_registry/verification.json). NFL outcome stores were not accessed for this task; existing production logic and frozen artifacts remain unchanged.
