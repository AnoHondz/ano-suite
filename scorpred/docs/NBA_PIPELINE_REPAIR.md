# NBA pipeline repair — 2026-10-22

The NBA schedule, completed games, local historical navigation, and descriptive matchup comparisons work locally at <http://127.0.0.1:5173/nba>. Unsupported probabilities remain unavailable. No early-season probability fallback was enabled.

### Follow-up: make existing information visible

A subsequent user report of missing NBA information revealed a presentation problem: unavailable prediction panels appeared before the available historical comparisons, and the NBA overview did not expose those comparisons. Live API checks returned both scheduled fixtures and 82 prior-season observations for each team, with no provider errors.

The NBA overview now shows compact current/previous-season comparisons before its venue/prediction panel, with a direct full-comparison action. Match Analysis shows its full comparison before the prediction sections. Records, sample sizes, scoring averages, and the distinction between statistics and prediction availability are visible without treating abstention as an absence of history. Model behavior is unchanged.

Follow-up verification: 11 targeted NBA/readiness frontend tests passed; lint and production build passed. Real API browser verification passed for the overview at 1440 and 390 pixels, the four fixture cases, local-history navigation, and a controlled comparison-service outage. [Follow-up evidence](assets/immersive_ui/nba-visibility.json), [updated overview](assets/immersive_ui/nba-overview.png), [mobile overview](assets/immersive_ui/nba-overview-mobile.png). The full-suite and performance measurements below describe the preceding redesign verification; the follow-up changes are confined to frontend presentation.

## Root cause and compatibility

The schedule and ESPN team identifiers were valid. The existing weighted winner rules intentionally require at least two completed current-season games per team. The scoring model requires at least three eligible observations per team, uses same-season rolling features and a 24-hour availability lag, and rejects games after tipoff. The saved scoring model does not support silently replacing those inputs with previous-season averages. The NBA winner-classifier artifact `data/models/nba_random_forest.pkl` is absent.

The previous matchup interface mainly exposed those abstentions. It did not make the already-present historical scores useful when a prediction was unavailable. Historical and completed fixtures also lacked the necessary navigation path through the existing match API. The repair makes available observations useful without weakening prediction requirements.

## Implemented flow

1. Existing ESPN client → cached schedule and normalized ESPN event/team identifiers → existing winner/scoring gates → existing decision API and prediction displays.
2. The 48 existing monthly NBA archives → disposable indexed SQLite view of 6,048 completed games → versioned descriptive comparison API → independent NBA comparison panel.
3. Upcoming / Recent results / Local history → existing matchup selection and analysis route. Recent results defaults to yesterday in America/New_York; local history defaults to the latest archived date.

The new `services/nba_matchup_context.py` produces `nba-descriptive-context-v1`, with `role=descriptive_only` and `prediction_fallback=false`. It supplies observed W–L records, sample sizes, points scored/conceded per game, six recent results from each selected sample, and current/previous-season comparison tabs. Completed games show their actual final score separately from predictions.

The derived `cache/nba_context/history-v1.sqlite3` contains game/date/team/season indexes, source filenames, and original SHA-256 hashes. Build it explicitly with `.venv\Scripts\python.exe -m scripts.build_nba_context_index`; normal reads reuse it. An atomic rebuild rejects conflicting original observations and preserves the existing index on failure. It never imports the trainer or downloads historical records.

Current-season team schedules use the existing client and its freshness receipts. A shared four-worker pool coalesces repeated team-season requests; page requests wait at most 300 ms per team for that optional refresh. Historical comparisons can render while fresh current-season data is pending, and the panel retries pending refreshes. Source failures have explicit unavailable states and a retry control. The original prediction display remains independent of comparison-service failures.

The live event lookup now checks the explicitly requested date before adjacent dates. The local launcher includes the new service in its loaded-code fingerprint; the running backend was reloaded and its fingerprint verified against the workspace.

## Availability and provenance safeguards

- Existing winner/scoring thresholds, feature definitions, weights, and model artifacts are unchanged. Prior-season observations never enter the prediction feature path.
- Comparison games must be completed, have valid distinct NBA team identifiers and positive non-tied scores, and precede `min(now, selected tipoff) - 24 hours`. Preseason records and the target/future game are excluded.
- Records are counted as observed provider-type-2 games, not asserted to be official standings. Provider classifications can include additional games; for example, some archived samples contain 83 observations. Coverage is disclosed rather than silently relabeled.
- Historical archive receipt timestamps and pregame availability are unverified. The API returns `received_at=null` for those records; it does not convert file modification times into collection timestamps. Historical comparisons are retrospective descriptive evidence, not a reconstructed pregame forecast.
- Only existing local archives and the already-configured live client are used. All restricted/unverified GitHub NBA registry candidates remain disabled. No third-party dataset imports, training, NFL holdout access, frozen artifact modifications, or deployment occurred.

## Real games verified

| Case | ESPN game | Result |
| --- | --- | --- |
| Philadelphia 76ers–Cleveland Cavaliers, October 22, 2026 | 401909093 | Schedule and comparison panel load. Eligible current samples: PHI 1, CLE 0. Previous-season samples: 82 each. Prediction intentionally unavailable. |
| Oklahoma City Thunder–Denver Nuggets, October 22, 2026 | 401909094 | Schedule and comparisons load. Eligible current samples: OKC 1, DEN 0. Previous-season samples: 82 each. Prediction intentionally unavailable. |
| Orlando Magic–Atlanta Hawks, October 21, 2026 | 401909834 | Completed fixture, observed final score, and previous-season comparisons load. No post-tipoff pregame forecast. |
| San Antonio Spurs–New York Knicks, June 14, 2026 | 401859967 | Local history selection and analysis load from existing archives, including the recorded final score. |
| Unknown historical identifier | 999999999999 | Explicit 404; no fabricated statistics. |
| Controlled comparison-service outage | Browser-only 503 injection | Error/retry appear while the existing Game outlook remains visible. |

The prior-season observed records for PHI/CLE are 45–37 and 52–30, with 115.9/119.5 points scored and 116.1/115.4 conceded per game. These are actual archive-derived descriptions, not inputs to a new probability model. The current slate contains two scheduled games; neither has the required sample for a supported prediction. The verified October 21 results view contains 11 completed fixtures.

## Measured API responses

Loopback measurements on October 22 against the running application; these are individual observations, not service-level guarantees:

| Request | HTTP | Time |
| --- | --- | --- |
| NBA schedule, provider refresh / warm repeat | 200 | 1,611 / 238 ms |
| PHI–CLE comparisons, first / warm repeat | 200 | 194 / 78 ms |
| OKC–DEN comparisons | 200 | 161 ms |
| October 21 completed fixtures | 200 | 455 ms |
| Completed-game comparisons | 200 | 99 ms |
| Latest local archive fixtures | 200 | 35 ms |
| Historical-game comparisons | 200 | 92 ms |
| Existing PHI–CLE decision | 200 | 14 ms |
| PHI–CLE scoring estimate | 422 | 99 ms; intentional insufficient-sample response |

Full payloads and observation time: [NBA API evidence](assets/immersive_ui/nba-api.json). Real browser journey and outage verification: [NBA browser evidence](assets/immersive_ui/nba-browser.json).

## Verification and next step

All **832 backend tests** and **38 frontend tests** pass; frontend lint and the production build pass. The backend suite includes NBA/NFL/soccer regressions, missing-history handling, invalid identifiers, source failures, no-fallback/leakage protections, atomic index conflict handling, and unchanged original files. Browser checks use real APIs for the four fixtures above; only the outage scenario deliberately intercepts a response. The normal frontend suite uses controlled fixtures and Lite graphics; a separate real-WebGL run verifies the cinematic interface.

All 160 previously protected data/model/frozen/research files retain their original SHA-256 hashes. Six offline fixed-input soccer model/rule outputs exactly match the existing baseline. The local backend reports healthy with research disabled and current loaded code. Evidence: [verification](assets/immersive_ui/verification.json), [file preservation](assets/immersive_ui/preservation.json).

The next NBA step is to retain permitted, timestamped current-season observations and recheck the existing methods as their two-game/three-game requirements become satisfied. A previous-season probability fallback remains blocked until a separate chronological evaluation establishes feature compatibility, calibration, and acceptable error. The present repair authorizes neither that evaluation's training nor enabling an unsupported probability.
