# Automatic sportsbook comparisons

The prop UI no longer requires a user-entered line. When configured, `services/prop_markets.py` attaches fresh DraftKings and FanDuel quotes from The Odds API to the existing picks. The UI uses the selected book's actual target and price, alongside an experimental estimate from the existing distribution at that target. The original historical target, estimate and record remain available in the comparison details. Without a valid quote, rows explicitly identify historical targets rather than sportsbook offers.

## Source and access

Reviewed on 2026-10-23:

- [The Odds API plans](https://the-odds-api.com/): the published free plan lists 500 monthly credits and all betting markets, subject to bookmaker coverage. No subscription or purchase was made.
- [Official v4 documentation](https://the-odds-api.com/liveapi/guides/v4/): player props use event-specific odds. The event-id lookup costs no credits; odds costs depend on markets and bookmaker groups returned.
- [Official market identifiers](https://the-odds-api.com/sports-odds-data/betting-markets.html): only documented, full-game over/under markets are mapped. Soccer goalscorer contracts, fouls, saves and tackles are not silently equated to unsupported market types.
- [Provider terms](https://the-odds-api.com/terms-and-conditions.html), dated 2026-08-31: analytical applications, UI display and derived values are permitted; redistribution as a standalone raw-data service is prohibited. This integration exposes only comparisons attached to ScorPred picks, without a bulk/raw odds endpoint.

Local configuration inspection found no configured `ODDS_API_KEY`. Consequently, no live sportsbook quote was obtained or represented as verified during implementation. The market-present path is tested with controlled fixtures; the running app reports the absent feed honestly.

Set an authorized free-tier `ODDS_API_KEY` in the local `.env` or backend environment, then run `Start-ScorPred.ps1 -Restart`. Keep the credential private. `PROP_ODDS_ENABLED=0` disables the integration even when a key is present. No NFL betting-policy flag is enabled by this service.

## Matching and freshness

The lookup requires exact normalized home and away names, kickoff within five minutes, and the same event ID in the event-specific response. Player joins require full normalized names, without fuzzy or surname-only matching. Ambiguous duplicate local names are rejected. Only the exact mapped market is considered.

Each comparison requires both over and under prices from one bookmaker, player, market and target. Missing, conflicting, stale, future-dated and invalid prices are rejected. Books never complete each other's pairs. Each book keeps its own target and price. The first displayed book follows a fixed order, not the largest apparent advantage.

Quotes expire at the earliest of kickoff, five minutes after observation, or fifteen minutes after the market's published update. Expired quotes disappear on open pages and trigger one automatic refresh per expired snapshot. The existing parent prediction/source/kickoff safeguards remain in force. A sportsbook quote cannot make an unavailable model available.

Requests share a five-minute cache and an in-process lock. Failures are briefly cached; there are no retry loops. A persisted, conservative ceiling reserves at most 100 prop-odds credits per calendar month per credential. Reservation counts the maximum market cost even if a call fails or returns fewer markets. This is an additional local cap, not a replacement for the provider's account-wide quota. No keys or credential-bearing URLs are logged or returned to the browser.

## Statistical limits

Neither models nor pick rankings are retrained or changed. At supported half-point targets, the existing probability ladder and existing shrink factor produce the experimental model estimate. Unsupported settlement contracts, integer targets with possible pushes, missing distributions and truncated tails show no model estimate at the book's target. They never reuse the historical-target percentage for a different target.

The fair market percentage normalizes the same book's over/under implied probabilities at a half-point target to remove its displayed margin. It is market context, not verified truth. Differences do not establish a betting edge. Historical-median backtest results do not demonstrate accuracy at sportsbook targets.

The next validation step, after obtaining actual permitted quotes, is prospective timestamped comparison at the exact book/market/target/price. Evaluation must use outcomes observed later under explicit settlement rules, without changing the frozen NFL research artifacts or accessing sealed outcomes.

## Verification

`tests/test_prop_markets.py` covers credentials, caching, quota, event/player/market matching, independent books, malformed quotes, timestamps, price validity, unchanged model output and unsupported comparisons. Existing NFL/NBA/soccer prop-route tests retain their protections. `frontend/e2e/nfl-props.spec.ts` exercises all three sports, automatic targets, absent feeds, expiry renewal, mobile layouts and unavailable estimates.

Implementation verification: 998 backend-suite tests passed, followed by 33 focused market tests after the final malformed-schema guards; 69 frontend tests, lint and the production build passed. Actual local NBA, NFL and soccer matchups each returned five historical props with zero manual inputs and the correct missing-feed status. Nine desktop/mobile browser views passed without page errors. Evidence is in `.tmp/prop-market-comparison/verification.json`; no live bookmaker quote is claimed by that evidence.
