# Deployment Readiness Report

Date: 2026-04-15
Repository: scorpred
Scope: Production readiness validation, hardening, and smoke verification

## Executive Summary

Status: CONDITIONAL GO

The app is deployable with current code and dependency set, with one environment caveat:
- `gunicorn` cannot be executed on local Windows (expected) because it depends on Unix `fcntl`.
- This does not block Linux deployment targets (Render, Railway, Heroku-style Linux dynos, VM/container Linux hosts).

Core quality gates passed:
- Full test suite: 151/151 passing
- Python dependency consistency: no broken requirements
- WSGI import check: passed
- Key route smoke checks: all returned HTTP 200

## Hardening Changes Applied

1. Startup resilience for dotenv import
- File: app.py
- Change: made `python-dotenv` import optional with safe fallback `load_dotenv` no-op.
- Benefit: process no longer hard-crashes on startup if `dotenv` package is missing in a non-ideal environment.

2. Deployment package parity
- Action: installed `gunicorn` in active venv to match `requirements.txt` expectations.
- Note: local Windows still cannot run `gunicorn` runtime due platform limitations (`fcntl`), but Linux deployment will.

## Validation Results

### 1. Static/Editor Diagnostics
- Result: No errors found

### 2. Automated Tests
- Command: `c:/Dev/scorpred/.venv/Scripts/python.exe -m pytest -q`
- Result: 151 passed in ~65s

### 3. Dependency Graph Check
- Command: `c:/Dev/scorpred/.venv/Scripts/python.exe -m pip check`
- Result: No broken requirements found

### 4. WSGI Import Check
- Command: `c:/Dev/scorpred/.venv/Scripts/python.exe -c "import app; print('WSGI_IMPORT_OK')"`
- Result: `WSGI_IMPORT_OK`

### 5. HTTP Smoke Checks
- `GET /` -> 200
- `GET /soccer` -> 200
- `GET /nba/` -> 200
- `GET /model-performance` -> 200
- `GET /strategy-lab` -> 200
- `GET /nba/standings` -> 200

## Production Risk Register

### High
None identified from current test and smoke gates.

### Medium
1. NBA index latency remains high
- Observation: `/nba/` is still slow in local measurement windows (~35-38s in this environment).
- Cause profile: external provider response time and expensive route computation path.
- Impact: degraded UX/timeouts under load if infra timeout is short.
- Mitigation recommendation:
  - move NBA card prediction generation to async/background cache,
  - render page first, hydrate predictions separately,
  - tighten upstream call budgets per endpoint.

### Low
1. Ephemeral secret fallback
- If `SECRET_KEY` is unset, app generates process-local secret (secure but non-persistent).
- Impact: session invalidation on restart; not a crash.
- Mitigation: set stable `SECRET_KEY` in production env.

## Deployment Preconditions (Must Set)

Required environment variables:
- `SECRET_KEY` (strong random value)
- `PORT` (provided by platform)

Recommended:
- API provider keys and host/base vars used by soccer/NBA clients
- `FLASK_DEBUG=0`

## Deployment Command

Procfile currently defines:

`web: gunicorn app:app --workers 2 --threads 2 --timeout 60 --bind 0.0.0.0:$PORT`

This is suitable for Linux deployment targets.

## Final Go/No-Go

Go for Linux deployment with caveats:
- Set production env vars (`SECRET_KEY` at minimum).
- Accept that NBA page latency optimization is not fully complete yet.
- For stricter production SLOs, implement async/cached NBA prediction hydration before broad rollout.
