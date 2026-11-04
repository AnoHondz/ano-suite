# NBA source refresh and match-analysis repair — October 22, 2026

The local NBA schedule and Match Analysis now refresh expired source snapshots instead of treating an expired presentation card as the final answer. Existing team statistics remain available independently of prediction availability. The installed NBA prediction formulas, parameters, minimums, and recommendation thresholds were not changed by this repair.

## Root causes

1. Canonical match cards had a 300-second presentation cache. NBA schedule/status observations expire after 60 seconds. A direct Match Analysis request could therefore return the cached card for several minutes after its source expired. Sanitization correctly withdrew the prediction, but no new source request was triggered.
2. Provider cache receipts were reconstructed from filesystem modification times. Copying/touching a file could change its apparent observation age. Newly fetched responses now retain an actual embedded receipt, and cache reads respect that receipt and its original deadlines.
3. The descriptive team-context cache and the player-average memory cache could outlive the underlying source deadline. Their own cache insertion times did not establish source freshness.
4. Duplicate page requests could independently fetch the same expired ESPN endpoint. The local backend's source fingerprint also omitted several NBA dependencies, allowing a loaded process to lag behind changes to those files.

## Implemented changes

- `services/decision_store.py`: an NBA card whose source is no longer fresh becomes a cache miss for normal match lookup. The existing route rebuilds from the schedule. Soccer/NFL lookup behavior is unchanged.
- `nba_live_client.py`: successful provider responses save their receipt with the payload. Disk reads retain the observation time, earliest original expiry/max-stale deadlines, and provenance basis. Touching a cache file cannot freshen an embedded old receipt. Existing legacy files remain readable within their previous bounds and are explicitly labeled `legacy_file_mtime`; no bulk deletion or timestamp fabrication was used.
- Identical concurrent provider requests share a result and receive independent copies. Page and background profiles remain separate so a short page request does not wait for the background worker's longer retry policy.
- Team-context snapshots refresh when their source expires, even within the outer 60-second cache. Player-average memory entries likewise stop at the earliest provider deadline and return independent copies; explicitly degraded player observations are rejected.
- Open NBA schedule, analysis and comparison views revalidate at the actual source deadline. Requests pause while the tab is hidden and resume on return; matchup renewal stops at tipoff. Repeated expired responses have a 30-second retry floor rather than a tight loop. The ordinary expiry guard still withdraws a prediction while fresh data is pending.
- During a provider outage, a still-cached known matchup can retain its real team identifiers and tipoff for descriptive analysis. Expired probabilities remain removed. The API and frontend display an outage notice. A cached schedule does not invent season type, venue neutrality, scores, or research eligibility.
- `scripts/run_local.py` now fingerprints the decision store, freshness/lifecycle helpers, and the installed NBA prediction/projection modules. The existing launcher safely restarted the owned loopback services; it did not create an additional collector.

Source freshness limits, the 24-hour comparison lag, post-tipoff restrictions, research flags, and missing-data abstentions remain in force. No probability is manufactured to fill an unavailable state. No training, new dataset import, migration, deployment, or frozen NFL artifact change was performed.

## Real future-match verification

The browser selected **Oklahoma City Thunder vs Denver Nuggets**, ESPN **401909094**, from the real NBA schedule. Its scheduled tipoff is **October 22 at 9:30 PM Eastern**. It clicked the existing Analyze Matchup action, reached the matching URL, and displayed both teams' historical statistics. The observed prior-season samples contain 82 games each: OKC 64–18, 119.0 points scored / 107.9 conceded per game; Denver 54–28, 122.1 / 116.9. These are labeled descriptive observations.

The current installed `nba-pregame-margin-v2` returned a 62.1% home win probability in the recorded verification, while retaining its own SKIP recommendation. This report verifies retrieval and display, not that model's calibration or betting value. The research blend remains disabled and separate.

The script allowed the real schedule receipt to expire without touching any cache files. The manual refresh path was verified first, followed by automatic renewal on an open page. The limiting source expiry advanced and the resulting decision had fresh inputs. The composite oldest-input observation timestamp remained unchanged because an hourly team input was still the oldest input; advancing it to the page-refresh time would have invented a receipt. Full timestamps and subsequent verification are in [browser evidence](assets/nba_refresh/browser.json).

Desktop and 390-pixel mobile checks found no page exceptions or horizontal overflow. Prior-season comparisons remained available. Philadelphia–Cleveland's pregame prediction remained unavailable after its 7:00 PM Eastern tipoff.

## Verification and evidence

Regression tests cover source-expired card refresh, failed-refresh abstention, retained matchup identities, invalid identifiers, actual receipt persistence, file-touch resistance, degraded sources, concurrent request reuse, team/player memory deadlines, post-tipoff withdrawal, and descriptive UI behavior. The full backend suite includes NFL/NBA/soccer regressions. The navigation test fixture was updated to represent disabled NFL research and unavailable optional props explicitly; no NFL prediction code was changed.

Final recorded runs: **914 backend tests passed** and **44 frontend tests passed**, including automatic renewal on an open NBA matchup. ESLint, TypeScript and the production build passed. The existing warning about the deferred 3D bundle size remains. All **160 protected data/model files** retain their baseline hashes, and both installed NBA prediction/projection modules match their hashes from the start of this repair.

Final counts and commands: [verification manifest](assets/nba_refresh/verification.json). Saved [API observations](assets/nba_refresh/api.json), [preservation checks](assets/nba_refresh/preservation.json), [desktop screenshot](assets/nba_refresh/nba-analysis-desktop.png), and [mobile screenshot](assets/nba_refresh/nba-analysis-mobile.png) provide the operational evidence.

The app runs at <http://127.0.0.1:5173>. Use `powershell -ExecutionPolicy Bypass -File .\Start-ScorPred.ps1 -Restart` for a controlled reload after future backend changes. Source outages can still withhold predictions; cached historical comparisons do not override that safeguard. Legacy source receipts remain explicitly distinguishable until a successful provider refresh replaces them.
