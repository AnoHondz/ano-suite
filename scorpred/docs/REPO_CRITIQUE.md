# Repository Critique (April 25, 2026)

This critique focuses on maintainability, correctness, operational readiness, and developer experience.

## Executive Summary

ScorPred has broad feature coverage and a meaningful automated test footprint, but current reliability and maintainability risks are high. The most serious signals are:

1. **The app fails test collection due to an import mismatch** (`sort_cards_by_kickoff` imported from `decision_ui` but not defined there).
2. **Static analysis surfaces extensive quality debt** (108 Ruff violations, including undefined names in production modules).
3. **The main Flask entrypoint is a monolith** (`app.py` exceeds 6,500 lines), making code ownership, review, and safe refactoring difficult.
4. **Repository hygiene can be improved** (tracked `.env`, committed model artifacts, and mixed runtime/training concerns in one deploy surface).

## What is working well

- Clear top-level README with setup, training, and deployment notes.
- Strong testing intent: there are many targeted test files under `tests/`.
- Runtime path abstraction exists (`runtime_paths.py`), which is a good foundation for environment portability.

## Priority Findings

### 1) Correctness blocker: broken import in app startup

- `app.py` imports `sort_cards_by_kickoff` from `decision_ui`.
- `decision_ui.py` defines `build_decision_card`, `plan_summary`, and `top_opportunities`, but not `sort_cards_by_kickoff`.
- Result: importing `app.py` fails and tests that import app crash during collection.

**Impact**
- Blocks test execution for route-level tests.
- Suggests drift between modules and insufficient contract checks for public helpers.

**Recommendation**
- Either reintroduce `sort_cards_by_kickoff` in `decision_ui.py`, or remove/replace the import and call sites.
- Add a small contract test that imports the expected public helper names from `decision_ui`.

### 2) Quality debt: extensive lint violations (including undefined names)

Running Ruff reports **108 issues**, including:

- `F821` undefined names in `app.py` and `walk_forward_backtest.py`.
- Many unused imports/variables (`F401`, `F841`).
- Multiple import-order violations (`E402`) and noisy formatting issues.

**Impact**
- Undefined names represent potential runtime failures.
- High warning volume makes important issues harder to detect.

**Recommendation**
- Stabilize by fixing all `F821` first.
- Enforce Ruff in CI with a staged baseline approach:
  1. Gate on `F821`, `E9` classes immediately.
  2. Burn down remaining classes incrementally per directory.

### 3) Architecture risk: oversized monolithic modules

- `app.py` is ~6,546 lines.
- Large business modules (`nba_routes.py`, `props_engine.py`, `api_client.py`) are also very large.

**Impact**
- Slower onboarding and review cycles.
- Higher blast radius for changes.
- Harder to write focused tests.

**Recommendation**
- Split `app.py` by bounded contexts (auth, soccer routes, NBA routes, admin/system routes, API endpoints).
- Move shared formatting/util helpers into focused modules with explicit public APIs.
- Keep blueprint registration in `app.py`, but move endpoint implementation out.

### 4) Security/ops hygiene concerns

- A `.env` file is present in repo root.
- Model/data artifacts are tracked under `data/` (e.g., serialized models).

**Impact**
- Risk of secret leakage if `.env` contains non-placeholder values.
- Larger repo footprint and less reproducible builds if artifacts drift from code.

**Recommendation**
- Ensure `.env` is git-ignored and remove tracked secrets/artifacts that should be generated.
- Keep only deterministic fixture data in Git; publish heavier artifacts via releases/object storage when needed.

### 5) Dependency management and packaging maturity

- Dependencies are specified only in `requirements.txt` with broad ranges for several libraries.
- No lockfile for deterministic deploy/reproducible CI.

**Impact**
- Environment drift between local, CI, and production.

**Recommendation**
- Introduce lock-based dependency management (e.g., `pip-tools` compiled requirements or Poetry lock).
- Separate runtime vs dev/test dependencies.

## Suggested 30-day hardening plan

1. **Week 1: Stability baseline**
   - Fix import mismatch and all undefined-name errors.
   - Make `pytest tests/test_smoke_routes.py -q` green.
2. **Week 2: CI quality gates**
   - Add lint + smoke tests to CI pipeline.
   - Block merges on correctness-class lint failures.
3. **Week 3: App decomposition (phase 1)**
   - Extract one route family at a time into blueprints/modules.
4. **Week 4: Repo hygiene**
   - Remove tracked secrets/artifacts as needed, document artifact strategy, add lockfile.

## Quick scorecard (current)

- Correctness confidence: **Low**
- Maintainability: **Low–Medium**
- Operational readiness: **Medium**
- Test intent/coverage breadth: **Medium–High**
- Security hygiene: **Medium**

