# Pregame validity and source freshness

ScorPred withdraws a pregame recommendation at kickoff, or earlier when the
provider reports a live, final, postponed or cancelled event. The check happens
before prediction, when a cached analysis or decision is returned, and in open
browser pages. Expired cards become SKIP with an unavailable reason; their side,
probabilities, confidence and market quote are cleared. Historical locked tracking
snapshots remain available for grading and audit.

Eligible engine decisions retain their original action. A stale input cannot
promote a pick or manufacture a probability.

## Source-age limits

Each provider payload carries its original observation time, fresh deadline and
maximum display deadline. Disk restores and fallback reads retain those times.
Reading old data never renews its age. One second of observation-clock jitter is
allowed because Windows clock APIs have different resolution; this does not
extend the expiry deadline.

| Input | Fresh for recommendations | Maximum cached fallback for display |
|---|---|---|
| Soccer API-Football/ESPN schedule | 2 minutes | 15 minutes |
| Soccer football-data.org schedule | 5 minutes | 15 minutes |
| Soccer recent form | Up to 1 hour, depending on source | 6 hours |
| NBA schedule/status | 1 minute | 5 minutes |
| NBA recent form | 1 hour | 6 hours |
| NFL nflverse schedule | 5 minutes | 24 hours |
| NFL ESPN schedule | 10 minutes | 24 hours |
| NFL historical scores/team statistics | 48 hours | No expired research scoring |
| Odds snapshot | 5 minutes | No expired fallback |
| Individual bookmaker quote | 15 minutes; also bounded by snapshot expiry | No expired quote |

Degraded schedule snapshots can display real matchups, but cannot produce a
pregame recommendation. Unavailable/expired form yields an unavailable forecast.
NFL team statistics require a fresh offline sync for research scoring. An archived
season remains an archive; a web read does not make its observations current.

Odds must match the participants and kickoff (within five minutes). Quotes from
the future, started events and old markets are rejected. Prices come from one
bookmaker. A quote's own deadline can hide it before the rest of the card expires.

## Features and confidence

Soccer research scoring shares its selector with offline training: only completed
same-season games, the six newest valid score pairs, a minimum of three per team,
and a strict 24-hour availability lag. Previous seasons cannot fill a thin current
season. The legacy Random Forest opponent-strength feature uses each team's own
historical opponents' pre-match points per game. Missing opponent histories remain
missing for artifact imputation; the current opponent is never substituted.

NFL recent-scoring percentages describe sampled scoring scenarios. They are not
calibrated win probabilities. Home and Insights group shortlists by sport; NFL
scenario shares are excluded from the rule-confidence buckets.
API cards expose these as `scenarioShare`/`tiedScenarioShare` and leave NFL
`winProbability`/`drawProbability` empty, with `confidenceMeasure: scenario_share`.
Research scoring
continues to carry retrospective evaluation and uncertainty disclosures and does
not change the match decision.

## Refresh outcomes and dates

Refresh jobs report success, degraded, failed or skipped explicitly, including the
source observation time. Cached fallback after a failed request remains degraded.
One successful league cannot hide another league's failure. A fresh empty schedule
is a successful observation. Health reports the first failure as degraded and
repeated failures as down.

Soccer dashboard requests wait at most 20 seconds for analysis. Concurrent requests
for a league share the pending refresh. If it is still running, the API returns
503 with a retryable "still refreshing" message while the bounded worker pool
finishes warming the cache; it does not report a successful source refresh.

All active dashboard date grouping, Today/Tomorrow labels, match links and kickoff
displays use America/New_York (ET), including daylight saving changes. Soccer day
queries include both UTC dates intersecting the requested Eastern calendar day.

These changes enforce validity and feature consistency. They do not establish
profitable betting performance or improve the published model's measured accuracy
by themselves; model evaluations remain retrospective.
