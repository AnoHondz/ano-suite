# Soccer statistic contracts v1

Implemented `soccer-stats-v1` in [a separate research module](../services/soccer_stat_contracts.py). All **12 documented issues** have corrected or explicitly unavailable behavior in this version; all **16 reviewed acceptance cases** are executable regressions. Existing production calculations, predictor modules, feature builders, model weights and selectors remain unchanged. Activating different statistic semantics in those paths requires a separately approved compatibility change; no activation or deployment is included here.

## Contract and transformation record

Normalization retains original source values, a named source/definition version, period, aggregation, unit, transformations, issues and explicit prediction-availability state. Research exports keep corrected values with immutable source file/row/hash references; the original CSVs retain the exact source values. No source field is overwritten. Zero is accepted only when a supplied observation validates as zero; absent, malformed or unsupported fields remain null.

Count fields require finite nonnegative integers and reject booleans, fractions and percent strings. Minutes require a declared decimal-minute unit. Match and descriptive season aggregates are labeled separately. Unsupported period/unit/definition combinations do not become match counts. Missing numerator/denominator leaves a rate unavailable; a genuine zero numerator with a positive denominator yields zero. Complete-window research means remain null when any required input is unknown. No model imputation or training definition is changed in production.

| Issue | v1 behavior |
|---|---|
| SOC-STAT-001: unknown goals/assists → zero | Nullable counts; `goal_or_assist` requires both known components; genuine zero preserved |
| 002: overview defaults/unsupported synthetic fields | Explicit competition split and descriptive season scope; missing totals/minutes/passes/duels remain null, without starts→appearances substitution |
| 003: absent fixture/form statistics | Explicit raw-column or provider-table mapping; absent tables/fields produce null, with quality denominators; no new acquisition |
| 004: corner constant and goal-based proxy | Corners require an actual compatible observation; corner probability remains unavailable because no validated corner forecasting method is supplied |
| 005: score type treated as xG | Final scores are never an xG source; only explicit expected-goals fields with a verified source/model/period definition qualify |
| 006: missing training xG treated as zero | Nullable complete-window helper for research; no imputation, feature rebuild or model fitting in production |
| 007: score-lambda share labeled xG | Retained as explicitly labeled model attack share/fraction, marked unvalidated for prediction; never mapped to observed xG |
| 008: unsupported opponent rates set to zero | Rates require known counts and valid denominators; unsupported card/shot rates remain null |
| 009: blocks/duels/total passes/key passes mislabeled | No proxy aliases; clearances/aerial wins/pass completions/broader chances need dedicated verified fields; existing props denylist unchanged |
| 010: regulation/extra-time/shootout mixing | Period scores retained separately; regulation result derives only from explicit regulation score, never shootout winner |
| 011: generic numeric/unit coercion | Finite, nonnegative, integral count checks; incompatible percentages and other units rejected/quarantined |
| 012: expected-goals display alias gap | `Expected Goals`, `xG` and `expected_goals` supported in the declared research table adapter, still subject to source/model-definition gating |

Fouls committed and drawn are distinct fields. Yellow, second-yellow red, direct red and source red counts remain separate; no blind card total is produced. Incompatible provider definitions, player/team scope or period conventions remain unresolved instead of being silently harmonized. A source declaring red-card counts does not establish its subtype conventions.

## Actual local research coverage

[Corrected observations](assets/soccer_foundation/v1/corrected_statistics.json) cover **5,198 team sides across 2,599 canonical E0 fixtures**. Their source definition covers the explicit retained count columns only; it is not a new upstream permission or a verified advanced-statistic grant.

| Field | Known / 5,198 sides |
|---|---:|
| Full-time source goals | 5,198 |
| Shots / shots on target | 5,197 each |
| Corners | 5,198 |
| Fouls committed | 5,198 |
| Source yellow / red counts | 5,198 each |
| Assists, drawn fouls, card subtypes, minutes, xG | 0 verified observations supplied by these retained columns |
| Corner forecasting probabilities | 0; unsupported |

One source observation fails the same-scope SOT ≤ shots rule: **West Ham away at Newcastle, August 15, 2021**, retained Stage 1 CSV line **770**, has away shots **8** and away SOT **9**. Both corrected research fields are null and the inconsistency is recorded. The original values remain intact. This is one quarantined statistic pair, not a deleted fixture or a repaired source score.

Historical pregame availability remains unverified. Target-game final boxes are denied as prediction dependencies. A supplied cutoff qualifies a prior-game statistic only with timezone-aware strict prior end/receipt times and explicitly prior-game match scope. Season totals never qualify through this rule. The exported historical observations remain unavailable for prediction; receipt times are not manufactured from present files or the audit timestamp.

## Acceptance cases and access boundary

The reviewed case names are read directly from [the original specification](assets/rights_soccer/soccer_stat_contracts.json), preventing a reduced substitute test list:

1. Unknown goals/composite remain null.
2. Genuine zero goals/assists remain zero.
3. Missing shots/cards remain null.
4. SOT greater than shots quarantines the pair.
5. Goal counts cannot supply corners/probability.
6. Observed zero corners remain zero.
7. Committed/drawn fouls remain distinct.
8. Card subtypes remain distinct without blind addition.
9. Floating final score cannot supply xG.
10. Explicit zero xG survives a verified model definition.
11. Unverified xG definition stays unavailable.
12. Negative/nonfinite/fractional/boolean counts are rejected.
13. Percentages cannot become corner counts.
14. Shootout winner cannot replace regulation draw.
15. Unsupported legacy proxy aliases remain null.
16. Target-game final boxes fail a pregame cutoff.

Additional tests cover competition-specific overview selection, missing source evidence, incompatible units/versions, original/candidate lineage separation, duplicate membership/representatives, conflicting observations, immutable/idempotent exports, default-disabled research reads, copied return values, artifact integrity and unchanged production imports. [Verification](assets/soccer_foundation/verification/verification.json) records final backend/frontend/build/lint results and preservation evidence.

The corrected modules have no automatic production integration. There is no approval prompt or deployment action in this implementation. What remains to authorize later is a specific production input-contract migration after original/candidate evidence review and fixed-input feature/forecast compatibility analysis. Do not retrain or claim improved prediction accuracy from these data-quality changes.
