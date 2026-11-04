# ScorPred Verification Checklist

This checklist captures an implementation milestone from an earlier upgrade
pass. It is not a substitute for the current README, test suite, or a real
production-readiness review.

## Historical Items Verified In That Pass

- soccer predictions supported draw outcomes
- prediction tracking and dashboard routes existed
- the matchup flow was consolidated
- data-quality messaging was added
- UI consistency work was completed

## Important Clarification

The unified Scorpred flow is the primary prediction path, but the repository
still contains some legacy compatibility code and ongoing cleanup work.

## Current Usage

Use this file as a milestone checklist only. For the current state of the app:

- read `README.md`
- run `pytest tests -q`
- inspect the live route and template code paths directly

## Current Bottom Line

This milestone was completed, but the project is still under active hardening
and should not yet be described as production-ready.
