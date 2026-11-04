# ScorPred review — October 16, 2026

ScorPred is a usable local experimental beta. Its current production release is
incomplete: recommendation expiry, data freshness, trained-model delivery and the
saved-picks workflow need attention before treating it as a dependable public app.

This review covers the current uncommitted workspace and the local app at
`http://127.0.0.1:5001`. It does not verify a deployed Render service. Application
source, accounts and saved records were not changed during the review.

## Priority findings

### 1. P1 — NBA recommendations remain actionable after tipoff

`nba_live_client.py:403` includes both pregame and live games as upcoming.
`nba_routes.py:610` builds pregame recommendations without a kickoff/status guard,
and `app.py:6450` accepts cached NBA decisions without checking kickoff expiry.

At 02:21 UTC on October 17 (10:21 p.m. Eastern on October 16), the dashboard
returned Mavericks–Hawks and Spurs–Kings with midnight-UTC kickoffs as CONSIDER.
The scoring endpoint correctly rejects those same past kickoffs. The product
therefore advertises a recommendation that cannot support its own analysis flow.

Expire pregame recommendations at kickoff across every sport. Keep live and
completed fixtures visible only with explicit status and suitable analysis.

### 2. P1 — Trained scoring models are not delivered or refreshed by deployment

`services/scoring_research.py:85` requires a JSON coefficient bundle from ignored
`cache/score_models` and a matching repository report. `render.yaml:7` verifies the
soccer classifier pickle and builds the frontend, but does not train or download
the new score bundles. Existing publishing workflows do not release them either.
Every bundle is rejected after 30 days (`services/scoring_research.py:118`).

The locally working upgrade will fall back to descriptive estimates or become
unavailable on a fresh host unless artifacts are manually copied/trained. This
manual requirement is documented, but it leaves the product release incomplete.

Publish versioned score-model/report pairs, restore them on startup, seed the
nflverse store, and run monitored import/training maintenance with rollback.

### 3. P2 — NBA stale-cache fallback can conceal indefinitely old data

`nba_live_client.py:150` serves any existing cache after upstream failure without
a maximum age or stale flag. `nba_routes.py:2543` then stamps the response with the
current time. Old schedules, game status and form can appear current, worsening
the kickoff-expiry problem.

Use bounded stale periods appropriate to each dataset. Return source observation
times, age and stale warnings; disable actionable forecasts beyond their bounds.

### 4. P2 — Cross-sport ranking compares incompatible quantities

`frontend/src/pages/HomePage.tsx:33` sorts NFL scenario shares alongside soccer/NBA
confidence. `app.py:6430` also ranks and groups these values together in Insights.

The live result placed a 100% Chiefs scenario share near the top of cross-sport
recommendations. This percentage comes from a small set of descriptive scoring
combinations, not a calibrated win probability. The NFL disclaimers help, but
do not make these measures comparable.

Separate model families in rankings, or establish a common prospectively
evaluated measure before comparing them. Preserve the underlying engine actions.

### 5. P2 — Saved picks is a dead end in the current React workflow

`frontend/src/components/DashboardTheme.tsx:123` links signed-in users to Saved
picks, but `DecisionCard.tsx:153` exposes only Analyze Match, and analysis has no
save control. There is no bets mutation in `frontend/src`. The authenticated save
API exists at `app.py:5905`; legacy templates have Track controls, but ordinary
sport routes serve React. The empty saved-picks page links back to Soccer to
track a match (`templates/my_bets.html:127`), where no such action exists.

Add an authenticated save action, CSRF handling, saved-state feedback and a link
back to the relevant match. This finding concerns the primary UI; saving through
the API or legacy surfaces remains technically possible.

### 6. P2 — ML monitoring can report success despite provider failure

`services/scheduler.py:69` records success when `MatchBrain.refresh_cycle` returns,
but `services/match_brain.py:325` catches fixture failures and returns cached or
empty data. Its refresh cycle still advances its refresh time.

An isolated failure probe produced `ml_predictions.status=ok` while the brain
reported degraded API status, three provider errors and no fresh data. Propagate
an explicit success/degraded/failure result into monitoring.

Locally, `/health` returned 200 while `/api/health` returned 503. They intentionally
measure different things. Unknown soccer/ML state immediately after startup is
also explained by first scheduled runs occurring 25/60 minutes later; that alone
does not establish a permanent scheduler failure.

### 7. P2 — Soccer dashboard lacks the protections applied to NFL

`app.py:6335` has no route rate limit; `extensions.py:5` sets no default limits.
The dashboard truncates date input rather than validating it, then arbitrary
uncached dates can trigger provider calls and distinct cache keys through
`api_client.py:1637`.

Apply strict ISO-date/range validation and a shared rate limit to expensive public
dashboard work. No quota-exhaustion attack was attempted during this review.

### 8. P2 — Calendar tabs mix UTC and local dates

NBA groups fixtures by their UTC date (`nba_routes.py:2483`), while cards display
browser-local kickoff dates (`DecisionCard.tsx:114`). Today/Tomorrow labels use
`toISOString()` (`DecisionCard.tsx:200`). At 10:21 p.m. Eastern on October 16,
October 17 is labeled Today, while some cards display October 16.

Choose an explicit calendar timezone per sport or user and use it consistently
for grouping, relative labels, navigation and kickoff display.

### 9. P2 — Legacy soccer ML feature semantics differ from training

`scormastermind.py:435` constructs opponent-PPG features from the current opposing
team's recent form. `train_model.py:469` constructs those same feature names from
the historical opponents each team actually faced. The schema names match but
the meaning differs, so the published classifier evaluation does not reproduce
this inference path.

Changing only those two holdout features to the live-path semantics changed 9 of
1,518 predicted outcomes, with a maximum probability movement of about 6.75
percentage points. This isolated change did not lower accuracy; it demonstrates
a contract mismatch, not proof of an accuracy decline. Share a feature builder
and add numerical train/inference parity checks.

This concerns legacy soccer classifier paths. Dashboard recommendations use the
rule engine, and the new scoring regressions have their own evaluation. The RF
dataset contained 7,888 unique match identities with no duplicate identities or
score/result conflicts; the mislabeled raw 2018 file is not evidence by itself
that the classifier dataset is contaminated.

### 10. P2 — Soccer scoring serves mixed-season inputs against same-season validation

`scripts/train_scoring_models.py:113` keys rolling histories by season and team.
Live scoring at `app.py:6540` instead calls the general fixture filter with its
default two-prior-season lookback (`predictor.py:60`). Early-season requests can
therefore fill the six-game window with last season's matches and claim a trained
forecast even without the three current-season games required during evaluation.

A synthetic input containing two current-season and four previous-season results
was accepted as six live scoring games and returned a trained forecast. Align
serving with training's season boundary, or separately evaluate and label a
mixed-season cold-start model.

## Additional product improvements

- Home waits for all three providers, even when a user selects a healthy sport
  (`HomePage.tsx:24`). Render available data independently and isolate failures.
- Partial Data analysis always says "Enough signal to act" even for SKIP
  (`MatchAnalysisPage.tsx:59`). Guidance should follow the actual action.
- Keep expected scores, scenario shares, model confidence and prediction ranges
  visually distinct. The scientific disclosures are useful, but advanced details
  could be expandable to make mobile analysis easier to scan.

## What works and what was verified

- Home, Soccer, NBA, NFL, Insights and Login loaded without JavaScript errors.
- These pages fit 320-pixel mobile and 1440-pixel desktop viewports without
  horizontal document overflow in the sampled flows.
- The three sport dashboards returned data. NFL used nflverse, with source and
  coverage information; trained scoring forecasts loaded for eligible fixtures.
- Authenticated bets ownership, CSRF and administrator checks appear correctly
  scoped; anonymous bets access is rejected. No authorization bypass was found
  in this review, which is not a comprehensive penetration test.
- 41 focused authorization/dashboard/scoring tests passed during this review.
  The preceding full run passed 509 backend tests and 10 browser regressions.
- `npm audit --omit=dev` reported zero production dependency advisories. This
  does not audit Python packages, development dependencies or application logic.
- The new scoring reports disclose modest improvements over simple baselines,
  retrospective data vintages and empirical interval coverage. Published methods
  and historical results do not establish live profitability.

## Suggested order

1. Enforce kickoff expiry, bounded data freshness and consistent dates.
2. Deliver and maintain the trained artifacts and nflverse data on the host.
3. Restore saving picks and isolate provider failures in the UI.
4. Correct feature/monitoring contracts, add public-endpoint protections, and
   separate incompatible confidence measures before expanding product claims.
