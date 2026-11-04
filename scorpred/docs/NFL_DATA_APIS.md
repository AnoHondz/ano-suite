# NFL data connections

ScorPred uses **nflverse first** for historical/modeling data, with ESPN as the
schedule fallback. [nflverse setup and research pipeline](NFLVERSE.md) explains
imports and feature engineering. Python access uses the maintained `nflreadpy`;
`nfl_data_py` is deprecated by its maintainers.

The expanded [NFL analytics catalog and operations guide](NFL_ANALYTICS.md)
covers participation, FTN charting, PFR advanced weekly/season statistics,
season summaries, derived metrics and strict historical availability checks.

## Source roles

| Provider | Role in ScorPred |
|---|---|
| [nflverse / nflreadpy](https://nflreadpy.nflverse.com/api/load_functions/) | Primary schedules/history, weekly team/player stats, play-by-play, rosters and timestamped depth charts; importer/store/features implemented |
| nflverse extended free feeds | Snap counts, Next Gen passing/rushing/receiving, weekly roster history, global player IDs and injury research snapshots; `--profile free` implemented |
| ESPN public scoreboard | Explicit schedule/results fallback |
| [The Odds API v4](https://the-odds-api.com/liveapi/guides/v4/) | Optional current moneylines, spreads and totals; adapter implemented |
| [SportsDataIO NFL](https://sportsdata.io/developers/coverages/nfl) | Optional additional injury/depth-chart/weather/statistics coverage; not connected |
| [Sportradar NFL](https://developer.sportradar.com/football/docs/nfl-ig-rosters) | Alternative optional availability/statistics provider; not connected |
| [National Weather Service API](https://www.weather.gov/documentation/services-web-api) | Free US venue forecasts, no API key; suggested next connection, not implemented |

Injuries and confirmed starting lineups need a separate verified, timestamped
source. A roster or depth-chart row does not establish that a player is available.
The nflverse injury file loaded successfully on October 18, 2026, but the imported
schema lacks report timestamps; it is stored for research, not labeled as a live
pregame injury service. See [verified coverage and refresh command](NFLVERSE.md).
Confirm commercial feed coverage, historical access, display rights, freshness and
quotas with the provider. No paid subscriptions were activated by this change.

The NWS API is a useful next free connection for US stadium conditions. It requires
an identifying User-Agent and reasonable request rates. Resolve a stadium's
coordinates with `/points/{latitude},{longitude}`, then follow its hourly forecast
URL. Preserve forecast issue times; do not use recorded postgame weather as a
pregame training feature. International venues need another source, and indoor
or retractable-roof conditions need verified roof context. This API is suggested,
not currently used by ScorPred.

## Enable the implemented odds adapter

Add these **server-side** environment variables to `.env` or your host:

```dotenv
ODDS_API_KEY=<your actual The Odds API key>
NFL_ODDS_ENABLED=1
NFL_ODDS_REGION=us
```

Restart Flask. Open `/nfl`; Market odds should show available after a successful
provider response. A healthy feed can return zero matching games, so an available
connection does not guarantee a quote for every card. Missing, old or mismatched
quotes are omitted. The selected bookmaker, decimal moneylines, optional home
spread/total and quote timestamp appear in matchup analysis.

The request is `GET /v4/sports/americanfootball_nfl/odds`, with one region,
`markets=h2h,spreads,totals`, `oddsFormat=decimal`, and `dateFormat=iso`. The official
[v4 guide](https://the-odds-api.com/liveapi/guides/v4/) documents authentication and
usage credits; this request includes three market types. The adapter is opt-in,
shares a five-minute cache, and backs off for 45 seconds after provider errors.
It never logs request exceptions containing credential-bearing URLs or sends keys
to the browser. Supported regions are `us`, `us2`, `uk`, `eu`, and `au`.

Quotes must match both full team names and kickoff within five minutes. Prices
come from one bookmaker at a time, rather than combining unrelated books. Quotes
older than 15 minutes or more than one minute in the future are rejected. NFL
moneyline ties and settlement rules are not assumed to match the model's tied
scenarios. The UI provides market context, not a model-versus-market edge claim.

The scoring scenarios and DecisionEngine action are unchanged by market quotes.
NFL remains CONSIDER/SKIP, with no BET recommendations or validated profitability.
SportsDataIO and Sportradar are recommendations, not implemented connections.

## Before using richer data in recommendations

Ingest timestamped injuries/inactives and depth charts, venue weather, and
team/player efficiency histories using stable provider IDs. Keep a provider-ID
mapping rather than assuming ESPN IDs transfer. Backtests need records available
before kickoff: final scores or revised injury reports cannot be used as pregame
features. Validate a new feature schema, train chronologically, and test on a
separate time window. Compare calibration and against market prices before
changing action thresholds or publishing accuracy claims.
