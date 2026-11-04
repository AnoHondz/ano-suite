# Backend Architecture Hardening Report

**Date:** 2025-01-XX  
**Scope:** Full backend correctness, consistency, and architecture integrity  
**Test suite:** 228/228 passing after all changes

---

## Summary of Changes

### Bugs Fixed (Code Changes)

| # | Issue | File(s) | Fix |
|---|-------|---------|-----|
| 1 | **NBA blend weight mismatch** — `_DEFAULT_NBA_ML_BLEND` was 0.40 but `_DEFAULT_POLICY["nba_ml_blend_weight"]` was 0.30. Runtime used the correct 0.30 from the policy dict, but the standalone constant disagreed. | `prediction_policy.py` | Aligned `_DEFAULT_NBA_ML_BLEND` to 0.30 to match the canonical policy dict. |
| 2 | **Strategy Lab offline/live metric mixing** — `_performance_comparison()` used `metrics.get("overall_accuracy")` (live tracker hit rate) as `rule_accuracy` in an offline comparison context, mixing two different evaluation populations. | `services/strategy_lab.py` | Now uses only `saved_rule_accuracy` from the offline report. Live tracker data is never mixed into offline figures. |
| 3 | **Strategy Lab apples-to-oranges insight** — `_ml_vs_rule_insights()` compared offline RF holdout accuracy against live tracked hit rate and framed one as "beating" the other. | `services/strategy_lab.py` | Rewritten to present both numbers side-by-side with an explicit note that they are different evaluation populations. |
| 4 | **NBA prediction tracking silently swallowed** — `except Exception: pass` at nba_routes.py L1504 meant a broken tracker would go completely unnoticed for NBA predictions. | `nba_routes.py` | Added `current_app.logger.warning("Prediction tracking failed (nba)", exc_info=True)`. |
| 5 | **Phantom mistake categories** — `late_form_overweight` and `historical_context_overtrust` were listed in `MISTAKE_CATEGORIES` but had zero classification logic. They always showed `count: 0` in reports and could never trigger adjustments. | `mistake_analysis.py` | Removed both phantom categories from the list. |
| 6 | **Rule 6 unbounded adjustment** — `propose_adjustments()` Rule 6 (popular_team_overrating) subtracted 3.0 from `bet_min_confidence_pct` without applying `_clamp()`, unlike all other rules. Combined with Rule 2, could produce out-of-bounds values. | `mistake_analysis.py` | Applied `_clamp()` with the existing `_ADJUSTMENT_BOUNDS` for `bet_min_confidence_pct`. |
| 7 | **Template error information leak** — Two template-rendering paths (`today_soccer_predictions`, `top_picks_today`) used `str(exc)` to set `load_error`, potentially exposing internal paths or stack details to users. JSON API responses were already sanitized. | `app.py` | Replaced `str(exc)` with `sanitize_error(exc)` at both locations. |
| 8 | **Missing ProxyFix** — The app trusts `X-Forwarded-For` for rate-limit keying but had no `ProxyFix` middleware, allowing header spoofing to bypass rate limits behind a reverse proxy. | `app.py` | Added `ProxyFix(app.wsgi_app, x_for=1, x_proto=1)` after Flask app creation. |

### Files Modified

| File | Changes |
|------|---------|
| `prediction_policy.py` | `_DEFAULT_NBA_ML_BLEND`: 0.40 → 0.30 |
| `nba_routes.py` | Silent `pass` → logged warning for NBA tracking failure |
| `mistake_analysis.py` | Removed 2 phantom categories; bounded Rule 6 adjustment with `_clamp()` |
| `services/strategy_lab.py` | `_performance_comparison()` uses only offline figures; `_ml_vs_rule_insights()` no longer frames offline vs live as a competition |
| `app.py` | Added `ProxyFix` import + middleware; 2× `str(exc)` → `sanitize_error(exc)` |

---

## Architecture Overview

### Prediction Pipeline

```
API Request
  │
  ├─ Soccer ─→ scormastermind.predict_match()
  │              ├─ scorpred_engine.scorpred_predict()  [rule-based scores]
  │              ├─ ml_service.predict()                [ML probabilities]
  │              ├─ Blend: ML × 0.40 + Rules × 0.60    [configurable]
  │              ├─ Draw transfer logic                  [draw_dominance → suppression → transfer]
  │              ├─ mistake_analysis.apply_adjustments() [learned deltas]
  │              └─ Policy gating (AVOID / LEAN / BET)
  │
  └─ NBA ────→ nba_predictor.predict()
                 ├─ ML model + historical stats
                 ├─ Blend: ML × 0.30 + Rules × 0.70    [configurable]
                 └─ Policy gating
```

### ML Model Stack (Soccer)

- **4 base models:** LogisticRegression, RandomForest, XGBoost, LightGBM
- **Meta-learner:** StackingClassifier(cv=5, final_estimator=LogisticRegression)
- **Calibration:** CalibratedClassifierCV(method='isotonic', cv='prefit')
- **47 features** defined in `train_model.FEATURE_COLUMNS`
- **Artifacts:** `data/models/soccer_ensemble_stack.pkl` (production), `soccer_random_forest_clean.pkl` (fallback)

### Tracking System

- Single JSON store: `cache/prediction_tracking.json`
- Dedup via `sport|date|sorted_teams` game key
- Single-pass grading via `update_prediction_result()` → `status="completed"`
- Surfaced to: Strategy Lab, Performance page, Mistake Analysis

### Automation (GitHub Actions)

| Schedule | Pipeline | Steps |
|----------|----------|-------|
| Daily 02:00 UTC | `daily_refresh.py` | Grade pending → Learn from mistakes → Optimize policy → Generate ML report |
| Weekly Sun 03:00 UTC | `weekly_retrain.py` | Prepare dataset → Train model → Walk-forward backtest → Generate report → Daily refresh |

---

## Known Limitations (Document-Only)

These are architectural constraints that are acceptable for the current deployment but should be understood:

### 1. In-Memory Rate Limiting (Per-Process)

Rate limits in `security.py` use an in-memory `defaultdict(deque)`. With Gunicorn's 2 workers, each process maintains separate counters. An attacker could theoretically double their effective rate limit by hitting different workers. Acceptable for a low-traffic prediction app; would need Redis-backed limiting at scale.

### 2. No In-Process Scheduler

The Procfile runs only the web process. All periodic tasks (result grading, retraining, report generation) rely on GitHub Actions. Result reconciliation only happens at 02:00 UTC daily or via manual POST to `/update-results`. For real-time result tracking, consider adding APScheduler or a Celery worker.

### 3. Venue Filter Degradation

`predictor.home_away_split()` filters form entries by `is_home` or `venue` fields. When these fields are absent from API data (common for some leagues), the filter returns all matches unfiltered — silently degrading the home/away split signal rather than failing.

### 4. CI Daily Refresh Data Dependency

The daily refresh in CI only produces output for steps 1–3 (grading, learning, policy optimization) if `cache/prediction_tracking.json` is committed to the repo. Since tracking data is gitignored by default, these steps are no-ops in CI unless manually committed.

### 5. Draw Transfer Logic Duplication

The draw-dominance/suppression/transfer algorithm exists in two places:
- **Runtime:** `scormastermind.py` lines ~542–562
- **Offline evaluation:** `ml_service.py :: _combine_probabilities()` lines ~112–126

Both implement identical logic. If one changes without the other, live predictions and backtests will diverge. Consider extracting into a shared utility function.

### 6. Dead Code Candidates

| Item | Location | Status |
|------|----------|--------|
| `retrain_model.py` | Root | 5-line wrapper calling `train_model.main()` — `weekly_retrain.py` imports `train_model` directly. Safe to remove. |
| `predictor.predict()` + Poisson helpers | `predictor.py` | Original prediction function, never called from production. Active utilities (`extract_form`, `filter_recent_completed_fixtures`, etc.) in the same file are still used. |
| `predictor.quick_predict_from_standings()` | `predictor.py` | Only called from tests. |

---

## Test Validation

```
228 passed, 15 warnings in 115.70s
```

All 228 tests pass after the hardening changes. The 15 warnings are sklearn feature-name warnings from LightGBM (cosmetic, not functional).
