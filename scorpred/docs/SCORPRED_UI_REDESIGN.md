# ScorPred: Graphite & Sage frontend redesign

Implemented and verified locally on October 20, 2026. No deployment was performed.

## Scope and implementation plan

The implementation followed the existing React/Vite frontend and Flask API contracts:

1. Inspect shared components, routes, freshness handling, research gates, and saved evaluations.
2. Introduce reusable Graphite & Sage tokens and update the existing layout and matchup cards.
3. Redesign Home, NFL match analysis, and Insights; apply the shared system to NBA and soccer.
4. Run the production build, frontend lint, existing backend/browser regressions, additional UI contract checks, and real-API screenshot verification.

The attachment supplied a written brief, without a separate reference image. The agreed Graphite & Sage screen concepts guided the layout. The Sites skill's local-only workflow was used with the existing checkout; no Site registration or publishing occurred.

Existing uncommitted work was present before this task. The modified-file inventory below lists this task's edits, rather than attributing the entire working-tree diff to the redesign.

## Design system and behavior

`frontend/src/index.css` defines reusable tokens for background `#101416`, card surface `#191F22`, dividers `#303A3E`, primary text `#EEF1EE`, secondary text `#A5B0AE`, and sage accent `#B8CA9B`. Inter is the interface font and IBM Plex Mono is used for analytical values and timestamps. Existing legacy font utility names resolve to Inter for compatibility.

Flat surfaces, fine borders, consistent spacing, readable text, and restrained status badges replace gradients, glowing rings, and decorative hero sections. Transitions are limited to interaction feedback. Reduced-motion preferences, keyboard focus, skip navigation, accessible labels, and table alternatives to calibration graphics are supported.

- **Home:** full returned slates grouped by sport, sport filters, URL-backed date selection, previous/next day controls, recommendation counts, explicit per-provider loading/error states, and confirmed fixtures including supported SKIP decisions. Incomplete loading/error responses do not masquerade as complete numeric slate counts. Different default sport dates are labeled.
- **Matchup cards:** shared team identities, optional logos with initials fallback, competition, decision, accurately labeled measure, concise reason, source warning when provided, kickoff, and analysis action. Expired pregame output displays Unavailable rather than an invented zero-percent estimate.
- **NFL analysis:** matchup masthead; current game outlook; descriptive home-ahead/tied/away-ahead shares; recent-scoring mean points; source-supported comparison; separate market context; separate trained scoring forecast; separate gated winner-research area.
- **Insights:** existing current-slate analysis remains available, alongside a Historical performance view for the saved NFL Phase 2.1 study. Switching to soccer or NBA retains the applicable current-slate experience.
- **NBA and soccer:** existing top-opportunity/full-slate analysis, league/date controls, score forecasts, and sport-specific modules retain their behavior within the shared visual system.
- **Navigation:** Home, Soccer, NBA, NFL, Match Analysis, and Insights remain accessible. Account and Settings link to the existing Flask routes. The Vite proxy also forwards Settings and Saved picks during development.

## Routes and API dependencies

| Area | Routes / read dependencies |
|---|---|
| Shared layout | Existing React navigation; `/api/session`; `/login`; `/my-bets`; `/settings` |
| Home | `/`; existing `/api/dashboard/soccer`, `/api/dashboard/nba`, `/api/dashboard/nfl`; optional existing `date` query |
| Sport dashboards | `/soccer`, `/nba`, `/nfl`; corresponding existing dashboard APIs; soccer `/api/leagues` |
| Match Analysis | Existing `/match-analysis?match_id=...&sport=...&date=...`; `/api/matches/<id>` |
| NFL scoring | Existing `/api/nfl/matches/<id>/analysis?date=...` |
| Soccer/NBA scoring | Existing scoreline APIs and existing ScorelinePredictor; not rewritten |
| Insights current slate | Existing `/api/dashboard/insights?sport=...` |
| Winner research | Existing gated `/api/nfl/research/winner/models` and user-selected `/api/nfl/research/winner/evaluations/<run_id>`; no game-forecast requests |
| Historical study | Read-only metric snapshot exported from the saved Phase 2.1 evaluation artifact |

Backend routes, database schemas, model algorithms, eligibility rules, evaluation logic, environment files, and feature flags were not changed by this task. No model was activated.

## Historical evidence and research isolation

The historical view reads `frontend/src/data/nfl-phase21-evaluation.json`, generated from:

`cache/nflverse/phase2_1/class_b/run_d51f0ec86b0dde870b81a606/evaluation.json`

`frontend/scripts/export-nfl-evaluation.py` copies only the five requested models' saved metrics and calibration bins, evaluation identity, evidence class, sample sizes, test cutoff range, and relevant paired-comparison information. It records the source SHA-256. It does not export game-level predictions, model artifacts, or training data.

The exported metrics and bins were checked for exact equality with the source report, and the source checksum was verified. Values are not duplicated as hardcoded component constants. To regenerate from the same immutable artifact, run from the repository root:

```powershell
.\.venv\Scripts\python.exe frontend/scripts/export-nfl-evaluation.py
```

The study has 139 chronologically evaluated games out of 888 eligible games and uses Class B reconstructed evidence. Its table, three comparison charts, and selectable calibration plot all use saved observations. Empty calibration bins are omitted rather than synthesized. Limitations explain reconstruction uncertainty, unavailable historical features, excluded ties, and the absence of an established statistically significant advantage. The forest-versus-prior confidence interval includes zero.

The separate match-analysis research panel checks the existing server-gated registry. Only disabled, research-only registry runs can be selected, and returned reports must retain their research-only/disabled flags to render. It never calls a game-level winner forecast endpoint or enables a model. The static historical study is explicitly a saved report, independent of the live research access state.

## Verification

| Check | Result |
|---|---|
| Production build | Passed: TypeScript and Vite production build |
| Frontend lint | Passed: ESLint |
| Backend regressions | 672 passed |
| Frontend browser regressions | 23 passed using installed Google Chrome |
| Responsive browser checks | 320, 390, 768, and 1440 px; no document horizontal overflow or page errors |
| Prediction lifecycle | Existing kickoff, source-expiry, market/context, and response-expiry regression checks passed |
| Data-state checks | Loading, timeout, missing/malformed data, provider failure, retry, and partial-provider availability passed |
| Historical values | All five models' metrics and calibration bins exactly match the saved report and SHA-256 |
| Research isolation | Disabled research remains unavailable; no game-level research forecast is requested; historical view makes no research-prediction calls |
| Real APIs | NFL, NBA, soccer, Insights, NFL match detail, and NFL scoring returned HTTP 200 during screenshot capture |
| Live research gate | Registry returned HTTP 404; unavailable state rendered without affecting current analysis |

Commands used:

```powershell
npm --prefix frontend run build
npm --prefix frontend run lint
.\.venv\Scripts\python.exe -m pytest tests -q
$env:PLAYWRIGHT_CHANNEL = 'chrome'
npm --prefix frontend run test:e2e -- --workers=2
```

This shell did not initially expose Node/npm on PATH. Verification used the existing Node installation at `C:\Users\User\AppData\Local\tradescor-tools\node-v24.20.0-win-x64`. The Playwright-managed Chromium revision expected by the installed package was absent, so browser tests used the already-installed Google Chrome through the existing channel setting. No dependency installation or lockfile change was needed.

Soccer initially returned HTTP 503 while refreshing and subsequently returned HTTP 200 during capture. Error handling was observed without manufacturing replacement fixtures or results.

## Screenshots

All screenshots below show the implemented production build against the running local Flask APIs or the verified saved evaluation, with no browser-mocked data. The capture records response status codes and browser errors in [capture-audit.json](assets/graphite-sage/capture-audit.json).

| Screen | Desktop | Mobile |
|---|---|---|
| Home | [Desktop](assets/graphite-sage/home-desktop.png) | [Mobile](assets/graphite-sage/home-mobile.png) |
| NFL dashboard | [Desktop](assets/graphite-sage/nfl-desktop.png) | [Mobile](assets/graphite-sage/nfl-mobile.png) |
| NFL game analysis | [Desktop](assets/graphite-sage/nfl-analysis-desktop.png) | [Mobile](assets/graphite-sage/nfl-analysis-mobile.png) |
| NBA dashboard | [Desktop](assets/graphite-sage/nba-desktop.png) | [Mobile](assets/graphite-sage/nba-mobile.png) |
| Soccer dashboard | [Desktop](assets/graphite-sage/soccer-desktop.png) | [Mobile](assets/graphite-sage/soccer-mobile.png) |
| Insights: current slate | [Desktop](assets/graphite-sage/insights-slate-desktop.png) | Covered by responsive browser checks |
| Insights: historical study | [Desktop](assets/graphite-sage/insights-history-desktop.png) | [Mobile](assets/graphite-sage/insights-history-mobile.png) |

To refresh screenshots from the frontend directory with Flask running and the current production build available:

```powershell
node scripts/capture-ui.mjs
```

The capture script does not restart Flask, activate research, or deploy anything.

## Files edited or added by this task

- `frontend/src/index.css`
- `frontend/src/components/DashboardTheme.tsx`
- `frontend/src/components/DecisionCard.tsx`
- `frontend/src/components/NFLAnalysis.tsx`
- `frontend/src/components/HistoricalPerformance.tsx` (new)
- `frontend/src/components/NFLWinnerResearch.tsx` (new)
- `frontend/src/pages/HomePage.tsx`
- `frontend/src/pages/MatchAnalysisPage.tsx`
- `frontend/src/pages/InsightsPage.tsx`
- `frontend/src/pages/NFLPage.tsx`
- `frontend/src/pages/NBAPage.tsx`
- `frontend/src/pages/SoccerPage.tsx`
- `frontend/src/data/nfl-phase21-evaluation.json` (new)
- `frontend/scripts/export-nfl-evaluation.py` (new)
- `frontend/scripts/capture-ui.mjs` (new)
- `frontend/vite.config.ts`
- `frontend/e2e/graphite-sage.spec.ts` (new)
- `frontend/e2e/navigation.spec.ts`
- `frontend/e2e/lifecycle.spec.ts`
- `frontend/e2e/scoring.spec.ts`
- `docs/SCORPRED_UI_REDESIGN.md` (this report)
- `docs/assets/graphite-sage/` (13 screenshots and capture audit)

Production-build output remains under the existing ignored `frontend/dist` directory. Browser-test output remains under the existing ignored test-results directory.

## Remaining limitations

- NFL team comparison intentionally contains only metrics supplied by the existing current-analysis endpoint: game sample counts and scenario mean points. No passing, rushing, offensive-efficiency, or defensive-efficiency values were invented.
- The historical snapshot is pinned to the saved Phase 2.1 report. It is not a live performance feed and requires an explicit export/build to change.
- The server's live winner-research gate remains closed. No prospective research prediction was run or validated against live data.
- Account, saved-pick management, and Settings continue to use their existing Flask pages and authentication behavior; this task did not rebuild those screens.
- Fonts use Google Fonts with local system fallbacks. External providers and logo hosts can fail independently; the UI retains the corresponding unavailable/initials states.
- Browser automation and screenshots use Google Chrome. Other browser engines were not separately tested.
