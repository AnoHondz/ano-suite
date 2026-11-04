# NBA prop roster repair — October 24, 2026

## Cause and scope

The Dallas–San Antonio matchup (`401909858`) displayed departed Dallas players. The prop model treated each athlete's latest appearance in its historical box-score sample as current team membership. This sample includes previous-season games and opponents; it cannot establish today's roster.

Current ESPN roster metadata for Dallas (team `6`) and San Antonio (`24`) confirmed that Anthony Davis, Ryan Nembhard and Marvin Bagley III were absent from Dallas's roster. Walter Clayton Jr. and Devin Vassell were present on their displayed teams. No player-name corrections were hardcoded.

Evidence endpoints:

- [Dallas current roster](https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/6/roster)
- [San Antonio current roster](https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/24/roster)

## Repair

`services/nba_props.py` verifies both current team rosters through the existing ESPN JSON cache before ranking players. It requires the requested team identity, unique athlete IDs and fresh source timestamps. Inactive, injured, ambiguous and malformed athlete entries cannot authorize a prop. An athlete listed on both rosters is excluded.

The shared prop model accepts an optional eligible-player-ID set. NBA calls supply it; existing historical backtests and other sports retain their previous behavior. A current roster cannot silently relabel an old-team statistical history as a new-team role. Eligible players use the current roster's display name, joined by exact ESPN athlete ID.

Prop picks retain `player_id` and a `roster_verification` observation with its original timestamp and expiry. Roster observations expire after ten minutes; failed refreshes cannot authorize props through stale fallback. The matchup endpoint returns an explanatory HTTP 503 when verification fails. Existing kickoff, schedule-freshness and sample-size safeguards remain in force.

The daily props board preserves this verification and rebuilds when it expires. Its response filter also withdraws expired roster rows. The managed backend fingerprint now includes the board module so its changes trigger the normal safe reload workflow.

Historical records, model parameters, probability calculations, sportsbook matching and ranking criteria are unchanged. The new tests compare the complete numerical output with the existing calculation when every player is roster-eligible.

## Validation

Automated coverage includes departed players, same-name/different-ID players, transfers without silently substituted history, duplicate roster IDs, cross-team ambiguity, inactive/injured athletes, malformed and absent rosters, provider outages, expired observations, unchanged eligible-player calculations and expiry of cached board rows.

Live API verification on October 24 checked eight scheduled NBA games and all 40 returned props against independently fetched current ESPN roster IDs, canonical names and team membership. Every check passed. Dallas–San Antonio returned five verified players; the three departed Dallas players were absent. Measured prop responses ranged from 1.3 to 9.6 seconds across the slate, including roster refreshes.

The actual Dallas–San Antonio browser journey passed at 1440px and 390px with the same current players, no page errors and no horizontal overflow. The managed backend was reloaded and its frontend proxy verified with research models disabled.

Validation: the full backend suite passed 1,062 tests; a subsequent focused run passed 62 tests, including the final additional board-expiry case. All nine shared prop UI tests passed across NBA, NFL and soccer. The complete frontend regression suite passed all 72 tests. Frontend lint and the production build passed.

Live results are recorded privately under `.tmp/nba-roster-repair/`, including the original response, post-repair responses, source roster snapshots, verification summaries and desktop/mobile screenshots.

## Limits

A roster is a bounded observation, not confirmation of an announced game lineup or guaranteed minutes. A recent transfer may remain without a prop until its existing historical-role safeguards are satisfied. Correct team membership does not establish a betting edge or make these experimental estimates validated sportsbook probabilities. Player-prop sportsbook prices remain unavailable until an authorized feed is configured.
