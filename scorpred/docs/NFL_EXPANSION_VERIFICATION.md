# NFL expansion verification

Work performed locally on October 18, 2026. No deployment, paid subscription,
model training or model activation was performed. Advanced research remains
disabled by default. Existing workspace changes were preserved.

## Existing integration and changes

The existing NFL importer already supported schedules/results, teams, weekly
team/player statistics, reduced PBP, rosters, depth charts, snap counts, NGS
passing/rushing/receiving, weekly rosters, player identifiers and injuries.
It used a shared SQLite store, on-demand schedule refresh and labeled ESPN
fallback. Existing research used the fixed 19-feature schema, chronological
train/calibration/test splits, and a separate experimental scoring workflow.

This expansion adds participation, FTN charting, PFR passing/rushing/receiving/
defense at both week and season levels, and combined regular/postseason team
and player summaries. It expands retained PBP fields, keeps full depth history,
and extends the same pipeline with incremental upserts, per-feed caching,
refresh failure tracking, observation versions and stored team/QB game metrics.

Schema migration 1 adds `record_versions`, `refresh_attempts` and migration
tracking, rekeys positional identities and preserves source receipt timestamps.
Data migration 2 normalizes play identifier representations. The existing
`records`, `datasets` and `dataset_details` tables remain; advanced team/QB rows
share this store. No application/auth database migration was needed.

Metrics cover offensive/allowed EPA, success, passing/rushing efficiency,
explosives, third downs, red-zone TD efficiency, yards, turnovers, sacks and
supported pressure data. QB research includes completion, EPA, adjusted yards,
interceptions, sacks, pressure splits and recent trends. Matchup bundles include
descriptive efficiency comparisons, rest/home context, recent form, schedule
strength, opponent-adjusted defense, prior head-to-head scores and measured
player totals. Definitions and denominators are in [the operations guide](NFL_ANALYTICS.md).

## Imported coverage

Full analytics selections were exercised for 2022 and 2024, plus current-season
2026. Historical schedules/team stats already present for 2023 and 2025 remain
available. Global team and player directories are stored once under season 0.

| Stored records | 2022 | 2024 | 2026 |
|---|---:|---:|---:|
| Play-by-play | 49,434 | 49,492 | 15,497 |
| Participation | 50,150 | 45,919 | Unsupported current season |
| FTN charting | 41,643 | 48,031 | 13,621 |
| Weekly player statistics | 18,831 | 18,983 | 6,131 |
| Full depth-chart history | 37,609 | 37,105 | 668,543 |
| Team season summaries | 32 | 32 | 32 |
| Player season summaries | 2,011 | 2,002 | 1,633 |
| Advanced team-game rows | 568 | 570 | 160 |
| Advanced quarterback-game rows | 693 | 706 | 187 |

Advanced team rows cover 284, 285 and 80 games respectively. The full local store
contains approximately **1.38 million records** and occupied **2.4 GiB** before
the final key normalization/WAL checkpoint. Current depth history is a large
part of that storage; imports run offline and the web process reads aggregates.
Downloaded caches and SQLite files remain ignored runtime artifacts.

The 2026 participation feed is unavailable during the season under the upstream
historical-only policy. The 2026 PFR seasonal passing response is empty; weekly
passing works, and the other current PFR season feeds imported successfully.
These statuses are recorded explicitly. Retrying the complete 2026 analytics
profile can exit 1 because of that missing supported seasonal asset; it does
not roll back successful independent feeds or overwrite the last valid snapshot.

All supported selections were exercised. Other season ranges are documented
loader support, not asserted to have been downloaded in this task. Current
files generally reach week 6; a maximum week never proves complete team coverage.
Injury adjustments remain unavailable because publication-time/inactive coverage
is insufficient. No historical pregame availability is inferred from today's
archive, depth chart, roster or source report date.

## Validation results

- Final full offline backend suite: **634 passed** in 81.07 seconds. This includes
  NFL API compatibility, ESPN fallback, NBA, soccer, freshness, chronological
  evaluation and all new storage/research fixtures.
- NFL/storage/research coverage includes numeric
  participation joins, timestamp-order safeguards, advanced metric arithmetic,
  missing fields/seasons, importer API signatures, natural-key idempotency,
  incremental updates, migrations, refresh backoff, opt-in routes, target-schedule
  vintages and leakage.
  Tests block live network requests; SDK signature tests use the installed
  optional `nflreadpy==0.1.5` without downloading anything.
- Frontend: `npm run build` passed (TypeScript and Vite). Node was available at
  `.tmp/review/node-v22.23.3-win-x64`; it was added to PATH for that command only.
- Live local NFL dashboard: **200**, source `nflverse`, 12 cards.
- Existing NFL matchup analysis: **200**.
- Gated advanced matchup/coverage endpoints: **200**. The sampled upcoming
  matchup returned five prior analytics games per team. An earlier historical
  cutoff returned `available=false`, as required for unobserved data.
- Independent source-PBP EPA arithmetic matched **all 1,298 team-game rows**
  across the imported seasons, with zero denominator/value mismatches.
- SQLite `integrity_check`: **ok**. The pre-change backup contains 79,762
  original payloads; **all 79,762** remain in observation history, including
  prior versions of corrected current rows. Legacy dataset counts did not shrink.

Backend tests and build commands:

```powershell
.\.venv\Scripts\python -m pytest tests -q
.\.venv\Scripts\python -m pytest tests/test_nfl_analytics.py tests/test_nflverse.py tests/test_nfl_free_data.py tests/test_nfl_lifecycle_freshness.py tests/test_scoring_research.py -q
cd frontend
npm run build
```

The local SQLite backup is
`cache/nflverse/nflverse.before-expansion.sqlite3`; machine-readable coverage
and preservation verification is at `cache/nflverse/expansion-verification.json`.
Use [documented refresh/status commands](NFL_ANALYTICS.md#import-verify-and-recover)
to inspect the current state rather than treating these counts as permanent.

## Remaining limitations

No verified live injury/inactive archive, automatic injury weighting or
historical game-day availability reconstruction was added. Pressure splits need
actual participation pressure flags or explicit PFR links; missing data stays
null. Season summaries never enter pregame rolling features. Historical training
can lack eligible rows under the stricter observation policy until prospective
snapshots accumulate. Existing model artifacts/reports were left in place.

Per-feed refresh commands are ready for an external maintenance scheduler on the
host owning the persistent store; no scheduler service or deployment was created.
Attribution and CC BY/CC BY-SA requirements apply to redistributed source data
and exports. All NFL analytics imports use free public nflverse assets and
require no paid API key. The existing ESPN fallback remains usable if nflverse
is unavailable; analytics coverage remains honestly unavailable where absent.
