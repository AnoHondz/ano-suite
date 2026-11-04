# NFL coverage

ScorPred uses **nflverse as its primary NFL historical/modeling data source**.
Schedules and completed results are read from a local SQLite store. ESPN remains
an explicit fallback when nflverse has not been synchronized or is unavailable.
The API and data-coverage panel identify which source is actually serving a slate.

Open `/nfl` for the next scheduled regular-season/postseason date in a 14-day
window. Date selections and analysis links survive refresh; dates use US Eastern
time. ESPN event references are retained for existing match links, while nflverse
team abbreviations and native game IDs remain separate identifiers. Preseason,
Pro Bowl and fixtures without confirmed kickoff times are excluded.

## Active dashboard engine

The dashboard still uses experimental recent-scoring scenarios, now backed by
nflverse results when available. Each team needs three completed current-season
games; up to eight games per team are sampled. For each pair of historical games,
home points average the home team's scoring and the away team's conceding, and
vice versa. Mean scores give projected points. Shares of scenarios with home
leading, away leading or tied are descriptive, not calibrated win/final-tie
probabilities. No home advantage is invented at neutral sites.

DecisionEngine assigns CONSIDER/SKIP with Limited Data. Market quotes remain
separate context and cannot produce BET. Injuries, starting quarterbacks, forecast
weather, player availability and depth charts are not model inputs. NFL forecasts
are not automatically written to or settled in the legacy soccer/NBA ledger.

## Historical data and research model

See [nflverse setup](NFLVERSE.md) for imports, feature definitions and refresh
commands. The local store supports schedules, weekly team/player stats, rosters,
latest timestamped depth-chart snapshots, and a selected play-by-play schema.

`nfl_model.py` trains a separate research classifier and projected-points model
using rolling pregame features. It splits chronologically into training,
calibration and test windows and fits preprocessing only on training data.
Closing odds, actual game-day weather, current-game scores and unversioned player
availability are excluded from features. Historical statistics may still include
later corrections: this is not an archived game-day-data backtest.

The research model is **not activated in dashboard recommendations**. Its source
checksums, feature schema, metrics and limitations are recorded in
[data/validation/nflverse_model.json](../data/validation/nflverse_model.json).
Neither the soccer model's accuracy nor the NFL research results establish live
betting performance.

The scoring analysis panel additionally displays a separate experimental Ridge
point forecast from the 19 pregame features, with empirically evaluated score
ranges. This does not replace the recent-scoring shares or the dashboard decision.
See [scoring research, evaluation and setup](SCORING_MODELS.md).

## Availability

The app refreshes the small schedule file on demand after five minutes. An
explicitly labeled snapshot may be served for up to 24 hours during a provider
failure. Older/unseeded data falls back to ESPN; outages never invent fixtures.
Player/stat/play-by-play imports are separate jobs and do not block web requests.
Optional [odds setup and additional providers](NFL_DATA_APIS.md) remain available.

- `GET /api/dashboard/nfl?date=YYYY-MM-DD`: optional date; 400 for invalid dates,
  503 for unavailable schedules, 200 for legitimate empty schedules. Includes
  `source`, `warnings` and `dataSources`.
- `GET /api/matches/<id>?sport=nfl&date=YYYY-MM-DD`: canonical decision rebuilt
  against the current event before returning a pregame estimate.
- `GET /api/nfl/matches/<id>/analysis?date=YYYY-MM-DD`: event-bound scoring scenarios;
  unknown events return 404; unavailable estimates return 422.

NFL-specific endpoints allow 30 requests per minute per configured identity.
