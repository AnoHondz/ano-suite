# NFL analytics operations

ScorPred extends `scripts/sync_nflverse.py`, `services/nflverse_store.py` and
`services/nfl_features.py`. The primary NFL pipeline, ESPN fallback, dashboard,
19-feature model schema, NBA and soccer integrations remain in place. No new
model is activated. `services/nfl_analytics.py` computes descriptive research
offline; web requests read compact game aggregates and weekly player records.

The separate [Phase 2 winner research engine](NFL_PHASE2_MODEL_RESEARCH.md)
adds reproducible cutoff audits and disabled probability-model infrastructure.
Its [current results](NFL_PHASE2_BACKTEST_RESULTS.md) report zero eligible
historical games; no winner model has been trained or activated on NFL data.

## Sources, signatures and supported seasons

Verified against installed **nflreadpy 0.1.5**, its source code, and the official
[Python loader API](https://nflreadpy.nflverse.com/api/load_functions/).
Coverage starts below describe loader support; files and individual fields may
be incomplete or unavailable. The CLI currently accepts NFL seasons from 1999
through the current calendar year. January/February default to the preceding NFL season.

All release paths below are relative to
`https://github.com/nflverse/nflverse-data/releases/download/` (normally Parquet).
The package owns download format/URL selection; ScorPred does not scrape PFR or
FTN sites or require their subscriptions. See the official
[availability schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html).

| CLI dataset | nflreadpy call | Coverage start / release path | Current-season refresh |
|---|---|---|---|
| `schedules` | `load_schedules(year)` | 1999; `schedules/games` | 5 minutes |
| `teams` | `load_teams()` | Global; `teams/teams_colors_logos` | 24 hours |
| `players` | `load_players()` | Global; `players/players` | 24 hours |
| `pbp` | `load_pbp(year)` | 1999; `pbp/play_by_play_YEAR` | 24 hours |
| `team_stats`, `player_stats` | `load_team_stats(year)`, `load_player_stats(year)` | 1999; `stats_team/stats_team_week_YEAR`, `stats_player/stats_player_week_YEAR` | 24 hours |
| `team_season`, `player_season` | Same loaders with `summary_level="reg+post"` | 1999; corresponding `*_regpost_YEAR` | 24 hours |
| `rosters` | `load_rosters(year)` | Loader: 1920; CLI: 1999; `rosters/roster_YEAR` | 24 hours |
| `weekly_rosters` | `load_rosters_weekly(year)` | 2002; `weekly_rosters/roster_weekly_YEAR` | 24 hours |
| `depth_charts` | `load_depth_charts(year)` | 2001; `depth_charts/depth_charts_YEAR` | 24 hours |
| `snap_counts` | `load_snap_counts(year)` | 2012; `snap_counts/snap_counts_YEAR` | 6 hours |
| `ngs_passing`, `ngs_rushing`, `ngs_receiving` | `load_nextgen_stats(year, stat_type=kind)` | 2016; `nextgen_stats/ngs_KIND`, filtered by year by the loader | 24 hours |
| `injuries` | `load_injuries(year)` | 2009; `injuries/injuries_YEAR` | 24 hours |
| `participation` | `load_participation(year)` | 2016; `pbp_participation/pbp_participation_YEAR`; historical only | 30 days |
| `ftn_charting` | `load_ftn_charting(year)` | 2022; `ftn_charting/ftn_charting_YEAR` | 6 hours |
| `pfr_{pass,rush,rec,def}_week` | `load_pfr_advstats(year, stat_type=kind, summary_level="week")` | 2018; `pfr_advstats/advstats_week_KIND_YEAR` | 6 hours |
| `pfr_{pass,rush,rec,def}_season` | Same loader with `summary_level="season"` | 2018; `pfr_advstats/advstats_season_KIND`, filtered by year by the loader | 6 hours |

Before this task, ScorPred already supported schedules/results, teams, weekly
team/player stats, reduced play-by-play, rosters, depth charts, snap counts,
three NGS feeds, weekly rosters, player identifiers and injuries. New selections
are participation, FTN charting, eight PFR feeds, and two season summary feeds.
Play-by-play now additionally retains the fields needed for QB, turnover,
third-down, red-zone and sack calculations. Optional absent fields stay absent.

The `core` and `free` profiles retain their selections. `analytics` selects all
26 source datasets, including large play-by-play and charting assets. Old season
archives use a 30-day refresh interval. All intervals are overridden individually
with `NFL_REFRESH_<DATASET>_SECONDS`, for example
`NFL_REFRESH_PBP_SECONDS=43200`. `--refresh` skips fresh imports, loads missing
seasons, and backs off failed/unavailable feeds for up to one hour. The original
explicit import behavior remains available without `--refresh`. Loader filesystem
caching uses the same per-feed interval; `--force` bypasses both cache checks.
Ordinary dashboard schedule refresh remains on its existing five-minute policy.

## Storage and migration

The existing SQLite database remains at
`SCORPRED_DATA_ROOT/cache/nflverse/nflverse.sqlite3` (repository root if unset).
It is separate from the application/auth SQLAlchemy database. No auth or bet
schema change is needed.

| Table | Purpose / relationship |
|---|---|
| `datasets` | `(dataset, season)` primary key; last successful receipt, retained row count, stable sorted content hash |
| `records` | `(dataset, season, record_key)` primary key; current payload, game/team lookup, observation timestamp |
| `dataset_details` | Coverage weeks/teams by dataset/season; maximum week is not a completeness assertion |
| `record_versions` | New: immutable changed payload observations keyed by dataset/season/key/time |
| `refresh_attempts` | New: attempts, success/failure/unavailable/unsupported status, exception class, source attribution |
| `schema_migrations` | New: applied migration version and time |

`migrations/nflverse/001_availability.sql` runs automatically once under an
immediate SQLite transaction. The Python migration converts old positional keys
to provider identities and seeds versions using the existing **receipt** times,
never the provider's report dates. It fails atomically on ambiguous identities.
Legacy payloads and metadata survive. Backup with SQLite's backup API before
upgrading; copying only a live WAL database file can omit committed changes.
Data migration version 2 normalizes integer/float play identifiers across PBP,
participation and FTN record/version keys; payloads and observation times remain
intact. Both steps are tracked and transactionally guarded by the store.

Ingestion validates a complete pull before publishing, rejects empty data and
conflicting duplicate identities, upserts changed/new rows transactionally, and
preserves omitted rows and prior versions. Exact duplicate depth-chart source
rows are removed before validation. Content hashes ignore row order; unchanged
records retain their observation time. Older observation timestamps and changed
payloads at the same timestamp are rejected. Refresh failures never rewrite
the last successful receipt or remove valid data. Retained row counts represent
the stored union, which can exceed a later truncated source file's row count.
Source withdrawals are not automatically interpreted as record deletions.

Native game IDs, GSIS IDs, PFR IDs and ESPN IDs remain distinct. Research compares
`LAR/LA`, `JAC/JAX`, and `WSH/WAS` as spelling aliases; relocation-era `OAK`,
`SD` and `STL` identities remain intact. Only explicit player IDs are crosswalked;
names are never used to infer GSIS IDs. Unlinked roster/PFR rows retain a local
source-qualified record identity with their actual provider-ID field still null.
Upstream player-stat rows with no player (team residual penalties, for example)
are stored in an explicit team-residual record namespace and excluded from
player research. Depth rows without weeks remain undated context.

Derived rows use `records` under `advanced_team_games` (game/team key) and
`advanced_qb_games` (game/team/GSIS key), linked to schedules by native game ID.
They include season/week, metric version, input fingerprint, and valid
numerators/denominators. Recalculation runs only when the input hashes, metric
version, or set of games eligible after the 24-hour lag changes. `--force
--derive` also forces recalculation. No second NFL store is created.

## Metric definitions

Unless specified otherwise, plays must have offense/defense IDs and `play_type`
`pass` or `run`. Nullified `no_play`, kneels, spikes and two-point attempts are
excluded. Modern PBP does not have a separate `no_play` column; `play_type`
excludes nullified plays, with an additional explicit flag check if supplied.
Kneel/spike exclusion flags must be known. Passing dropbacks include sacks and
scrambles when `qb_dropback` is set; rushing metrics use designed rushes outside
that dropback population. QB rows require an explicit passer GSIS ID, so plays
without that ID cannot contribute to a specific quarterback.

Each rate is `{value, numerator, denominator, eligible}`. Zero denominators
produce null values/numerators. Unknown fields do not become zero. `eligible`
shows the population before missing measurements are removed; observed
denominators expose partial coverage. Fractions use 0–1 units, including the
field named `completion_percentage`. Rolling rates pool numerators/denominators,
rather than averaging differently sized game rates.

| Metric | Numerator / denominator |
|---|---|
| EPA/play | Sum of known EPA / eligible plays with known EPA |
| Success rate | Known `success == 1` / plays with binary success flags; nflfastR defines success from positive EPA |
| Passing EPA/dropback | Sum of known dropback EPA / dropbacks with known EPA |
| Rushing EPA/play | Sum of known designed-rush EPA / designed rushes with known EPA |
| Explosive play rate | Dropbacks gaining at least 20 yards or designed rushes gaining at least 10 / those plays with known yards |
| Third-down conversion | Converted third downs / third downs where converted+failed equals one |
| Red-zone efficiency | Drives scoring an offensive TD after entering the opponent 20 / observed drives reaching yardline 1–20 with sufficient TD flag coverage; drive IDs required |
| Yards/play | Known yards gained / plays with known yards |
| Turnover play rate | Plays with interception or lost fumble / plays with both binary flags known; a play counts once |
| Team sack rate | Sacked dropbacks / dropbacks with known sack flags |
| Defensive efficiency | The corresponding opponent offensive EPA/success/passing/rushing/explosive/sack/pressure rates allowed |
| QB completion | Completions / pass attempts excluding sacks, with binary completion flags |
| QB adjusted yards/attempt | `(passing yards + 20*passing TDs - 45*interceptions)` / measured pass attempts; explicitly incomplete passes have zero passing yards |
| QB interception rate | Interceptions / attempts with known binary interception flags |
| QB sack rate | Sacks / (pass attempts + sacks), with complete sack-flag coverage |
| Pressure rate | Participation `was_pressure` true / measured dropbacks; when unavailable, explicitly linked PFR `times_pressured` / tracked attempts+sacks; impossible counts are rejected |
| QB EPA under pressure | Sum of EPA / measured pressured dropbacks; unavailable without explicit play pressure flags |

Pressure flags are historical participation data, not inferred from QB hits or
FTN blitz counts. PFR pressure fallback may cover a subset of QBs and uses a
different denominator; the output preserves its actual observed denominator.
Full-game source counts and PBP subsets can disagree; counts exceeding tracked
attempts+sacks are left unavailable. The red-zone measure is drive TD efficiency,
not points per red-zone trip. No missing PBP fields are manufactured.

Matchup research uses up to eight prior completed games in the same NFL season,
with a recent window of three. Trend is recent pooled EPA minus earlier pooled
EPA in that window, null without both periods. Defense is adjusted descriptively
by subtracting each opponent's pooled offensive EPA across its other available
games at the **target cutoff**, then averaging those residuals. This is not DVOA.
Strength of schedule is the mean of faced opponents' available score-based win
rates (ties count one-half), excluding that meeting. These baselines may include
games after a historical meeting, but always before the researched target.

Home-field context is a non-neutral venue flag. Rest days are kickoff-to-kickoff
elapsed days since the latest known completed game; `short_week` means fewer
than six days, and rest difference is home minus away. No unsupported numerical
home/short-week penalty is imposed. The EPA matchup gap is own offensive rate
minus opposing allowed rate; it is a descriptive comparison, not a probability.
Head-to-head scores are current-season prior-game context only. Player research
provides measured weekly passing/rushing/receiving totals in the recent game
window, excluding unlinked players and all season-aggregate/week-zero rows.

## Strict availability and feature gating

Research cutoffs must be timezone-aware, earlier than target kickoff, and no
later than now. A source version must have been observed by that cutoff. Prior
completed games must also be older than the conservative 24-hour completion lag.
Later stat corrections use later versions, never overwrite the earlier view.
Present-day roster, injury, depth, NGS summary and end-of-season aggregates are
not substituted into historical targets. Injuries remain contextual: there is
no verified injury/inactive publication-time archive, so availability adjustments
are explicitly unavailable. Depth-chart placement never confirms active status.

**An archive first imported today cannot prove historical pregame availability.**
Such rows are suitable for descriptive postgame analysis and future research,
but yield missing historical features before their first observation. The
original research feature builder now enforces the same vintage rule while
preserving its existing 19 feature names/schema. Historical training can therefore
have insufficient rows until enough prospective observations accumulate. There
is no silent retrospective approximation mode. Existing chronological train,
calibration and test splits remain in place; no random split or model retraining
was added. Previously generated artifacts/reports were not republished.

By default, `NFL_ADVANCED_RESEARCH_ENABLED=0`. Setting it to `1` exposes:

- `GET /api/nfl/research/coverage`: stored coverage and latest attempt status.
- `GET /api/nfl/research/matchups/<native_game_id>`: structured team/QB/player
  research. Optional `?as_of=<timezone-aware-ISO-time>` requests a strict vintage.

These routes return descriptive statistics and availability, with no winner
prediction, action or new score forecast. Existing dashboard/analysis responses
and UI remain intact. No live API request downloads PBP or participation files.

## Import, verify and recover

Run from the repository root using the environment with `requirements-nfl.txt`:

```powershell
.\.venv\Scripts\python -m pip install -r requirements-nfl.txt
# Backfill missing historical datasets; existing archives are cached.
.\.venv\Scripts\python -m scripts.sync_nflverse --seasons 2022,2023,2024,2025 --profile analytics --refresh --derive --keep-going
# Update the current NFL season (adjust the year in later seasons).
.\.venv\Scripts\python -m scripts.sync_nflverse --seasons 2026 --profile analytics --refresh --derive --keep-going
# Inspect without downloading or importing nflreadpy.
.\.venv\Scripts\python -m scripts.sync_nflverse --status
# Retry one feed immediately and force its cache and analytics to refresh.
.\.venv\Scripts\python -m scripts.sync_nflverse --seasons 2026 --datasets pbp --force --derive
```

`--database <path>` selects an alternate SQLite file; the download cache is shared
under the runtime data root. `--missing-only` leaves already imported seasons
alone. Schedule the refresh command externally on the machine with this store
(for example, hourly through Task Scheduler/cron); feed intervals govern actual
downloads. No external schedule or deployment is created automatically. Re-run
PBP/team/player imports after Wednesday-night corrections when needed. Full
timestamped depth history is large (hundreds of thousands of rows in 2026), so
allow disk/memory/time and keep these jobs out of web workers.

Reports distinguish `cached`, `unsupported`, `unavailable` and `failed`; successful
imports include row count/hash. With `--keep-going`, unrelated feeds complete.
Missing supported assets, empty seasonal responses or failures exit **1**;
known unsupported ranges are explicit skips. Retry backoff retains the failure
status/nonzero exit. Last valid rows remain usable and the separate failure
history remains inspectable. Diagnose the affected feed with `--status`, retry
that selection with `--force`, then rerun `--derive`. Do not delete the store to
recover. Without a usable nflverse schedule, the existing labeled ESPN fallback
continues to serve the application; ESPN does not manufacture missing analytics.

## Observed coverage and restrictions

Local imports were exercised for **2022, 2024 and 2026**. Existing schedules and
weekly team statistics for **2023 and 2025** were preserved. All catalog datasets
are integrated; current-year participation is intentionally unsupported and the
2026 PFR seasonal passing response was empty when checked. Weekly PFR passing
works. NFL source receipt time, latest week and actual rows are available in
`--status`; receipt alone does not guarantee upstream completeness. Seasonal
summary files may lag their weekly counterparts. NGS has upstream participation
thresholds, and week-zero summaries are not weekly games. Injury report schemas
vary: historical `date_modified` revisions are retained, while current rows may
lack report-issued times. Pre-2025 depth charts use `club_code`, week/game type;
2025 onward use `team` and appended `dt` snapshots.

Most nflverse data is [CC BY 4.0](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md).
FTN charting uses [CC BY-SA 4.0](https://nflreadr.nflverse.com/reference/load_ftn_charting.html).
[Participation](https://nflreadr.nflverse.com/reference/load_participation.html)
requires CC BY-SA attribution to FTN via nflverse for 2023 onward and NFL NGS via
nflverse for earlier years. Preserve dataset-specific attribution, notices and
share-alike obligations in redistribution/derived exports. The package's code
license does not replace dataset licenses. No paid API key or subscription is
required; optional odds providers elsewhere in the app remain unrelated.

See [verification results](NFL_EXPANSION_VERIFICATION.md) for exercised coverage,
regression checks, preservation checks and remaining limitations.
