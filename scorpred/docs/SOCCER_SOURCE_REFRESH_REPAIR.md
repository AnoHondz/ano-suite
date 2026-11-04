# Soccer source refresh repair — October 24, 2026

The soccer slate and Match Analysis now recover from expired source snapshots. Existing provider permissions, prediction methods, model coefficients, pregame checks and research restrictions are preserved. No datasets were imported, published model artifacts or fitting parameters changed, or services deployed by this repair. The app's existing runtime ratings calculation remains unchanged.

## Causes

- A request for one date waited for the full 24-fixture analysis batch. The public fallback could scan 29 UTC dates, and unrelated form/H2H work and cold ratings calculations exceeded the API's 20-second wait. The email received HTTP 503 rather than usable forecasts.
- Service and legacy analysis caches could retain an expired analysis after a genuine source refresh. Combining the old analysis with the new fixture correctly kept the old expiry, but the analysis was never regenerated.
- A fresh cached missing-prediction result could survive after an actual prediction arrived. The decision cache could also prevent Match Analysis from reaching the refreshed schedule.
- Open soccer pages withdrew expired predictions but did not request a new observation. Child props/injury requests did not consistently retain the league/date needed for a cold refresh.

## Changes

Single-day requests, including the default Soccer view, use the existing `get_fixtures_for_date` provider path and independent date/league cache keys. That path observes both UTC dates overlapping the Eastern calendar day. Background full-slate jobs retain their existing scope. Navigation dates can use already observed schedule metadata without waiting for unrelated analysis.

Expired analysis is recomputed only when the current scheduled fixture has a genuinely fresh source. A missing prediction can recover only when the fixture now contains valid three-way probabilities. Still-valid cached analysis retains its original observation and expiry times. Refresh failure, postponement and kickoff still withdraw estimates.

Expired soccer decision snapshots allow the normal Match Analysis/props/injury route to refresh its source. A failed provider can retain verified matchup identities as descriptive context, with expired recommendations removed. Known analysis warming returns an explicit `refresh_pending` state when verified schedule cards are available; actual provider failures remain errors.

Concurrent calls share one existing ratings calculation per league. Model parameters, fitting method, historical cutoffs and published artifacts are unchanged.

The existing NBA source-deadline hook is shared with soccer without changing NBA behavior. Visible soccer slates and match pages request a new observation at source expiry, throttle retries to 30 seconds, pause while hidden, and stop at kickoff. A known cold refresh recovers automatically. Props and injuries retain the selected league and Eastern match date.

## Live verification

The unmocked browser run used the running loopback app at `http://127.0.0.1:5173` on October 24, before 10:00 AM Eastern.

| League | Scheduled/status cards | Remaining pregame forecasts | Final warmed API time |
| --- | ---: | ---: | ---: |
| Premier League | 4 | 3 | 77 ms |
| Bundesliga | 6 | 1 | 57 ms |
| La Liga | 4 | 3 | 64 ms |

Seven started/status-ineligible games had no pregame forecast. Of the seven remaining forecasts, four retained CONSIDER and three retained SKIP. No action was upgraded to meet a count. Earlier provider-renewal reads took approximately 2.86–3.27 seconds; these single-machine observations are not a controlled performance benchmark.

The browser selected Chelsea–Tottenham from the live slate and verified its 57.5% existing model estimate, expected-goal forecast, five experimental player props, correct analysis link, and desktop/mobile layouts at 1440 and 390 pixels. No page errors or horizontal overflow occurred. These numbers are a dated verification snapshot, not current betting advice or verified sportsbook props.

NBA and NFL smoke checks returned HTTP 200 with eight NBA games on October 24 and twelve NFL games on October 25. The backend's source fingerprint matched the current code, with research disabled.

Private evidence is under `.tmp/soccer-refresh-repair/`: `verification.json`, `soccer-slate-desktop.png`, `soccer-analysis-1440.png`, and `soccer-analysis-390.png`. Reproduce the browser journey with `frontend/scripts/verify-soccer-refresh.mjs` using the existing Node/Playwright setup.

## Verification and limits

`tests/test_soccer_refresh_repair.py` covers date scoping, original source clocks, failed renewal, cached missing-prediction recovery, kickoff/status withdrawal, descriptive fallback, concurrent ratings calculation and warm-up status. Existing backend regressions include NBA, NFL, soccer and delivery safeguards. `frontend/e2e/soccer-refresh.spec.ts` checks automatic renewal in both soccer views, request throttling and withdrawal after a failed refresh; the existing frontend suite covers navigation and all three sports.

Final checks: **1,048 backend tests passed**, **72 frontend tests passed**, ESLint passed, and the TypeScript/Vite production build passed. An earlier concurrent run hit the existing 30-second tracking-subprocess timeout; the isolated retry and final complete backend run passed. An initial new browser test was corrected to assert the visible probability rather than a hidden card explanation; the final complete frontend suite passed. Whitespace checks passed.

A cold league calculation can still take longer than the first API wait. The app now exposes the verified schedule and retries while analysis finishes. Provider outages, unsupported inputs and started games can still make a forecast unavailable. Historical player-prop targets remain experimental when no live sportsbook feed is configured. Local automatic email delivery still depends on its separately configured sender credentials and running app.
