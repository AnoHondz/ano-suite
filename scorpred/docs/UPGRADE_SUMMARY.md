# ScorPred Upgrade Summary

This document summarizes a completed upgrade pass. It is a historical record,
not a statement that the current repository is production-ready.

## Upgrade Themes

- better soccer prediction clarity, including draw support
- richer result tracking and performance visibility
- cleaner matchup presentation
- stronger UI consistency

## What Still Remains

The repo still has follow-up work in progress, including:

- CI automation
- security hardening
- monolith reduction in `app.py`
- broader business-logic test coverage
- additional ML baseline work

## Practical Reading Guide

If you need the current truth, use:

- `README.md` for project status
- `pytest tests -q` for the latest passing baseline
- the source itself for the authoritative route/template contracts

Use this file as milestone context only.
