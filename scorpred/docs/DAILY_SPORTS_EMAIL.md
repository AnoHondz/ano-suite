# Daily NBA, soccer and NFL email

The local opt-in digest uses the existing APScheduler owner and existing pregame APIs. It is separate from production prediction calculations and the monitor's alert emails. No model is trained or enabled, no deployment is performed, and no NFL evaluation or sealed-outcome endpoint is queried.

The requested schedule is 06:00 America/New_York daily to the recipient in the private runtime config, updated at the user's request on October 24, 2026. The 72-hour window covers early soccer kickoffs and NFL games on days without a same-day NFL slate. Each sport has its own ranked section with up to five available match/score forecasts and up to five player props. There is no cross-sport ranking of incompatible confidence measures.

The aim is at least five useful match/score selections overall when sources permit. A minimum count never overrides missing data, kickoff, source expiry, disabled models or model abstentions. Shortfalls are explained. A valid forecast with a SKIP betting action can appear as descriptive context with “No bet recommended”; SKIP is never upgraded to BET.

## What the email contains

- Real home/away teams, Eastern kickoff time, the app's existing side/action and explanation.
- Existing score-model outputs. NBA expected points, soccer expected goals and most-likely-score probability stay distinct; NFL recent-scoring means and scenario shares remain explicitly descriptive and experimental.
- Player props only after current-roster checks and the existing model guards. NBA/NFL additionally require a fresh injury report. Soccer explicitly states when injury availability is not independently verified. Missing or ambiguous names are omitted.
- Historical prop targets are labeled as such. If the existing configured market adapter supplies a fresh named sportsbook quote and supports a model estimate at that target, its actual target, price and update time appear. Historical-target percentages are never silently reused for another target.
- Source timestamps and missing-provider/coverage explanations. Exact scores and model percentages are not guaranteed outcomes or proven betting edges.

## Current operational limit

The chat Gmail connector used for the earlier one-off message does not give its credentials to this standalone app. No SMTP credential was configured during implementation. The daily job can be registered and previews generated, but unattended delivery remains **blocked_missing_smtp_credentials** until the user configures an authorized sender locally.

The app/backend and computer must be running at the scheduled time. This is a local schedule, not a cloud service. APScheduler follows Eastern daylight-saving changes, coalesces duplicate runs and permits a two-minute misfire window. It does not send old missed-day reports on a later startup.

## One-time local mail setup

Use the app's existing SMTP settings in the ignored `.env` file, with an authorized sender and credential:

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-sender@gmail.com
SMTP_PASS=your-app-password
```

Do not put credentials in command arguments, source control or chat. For Gmail, [Google's app-password instructions](https://support.google.com/accounts/answer/185833?hl=en) explain the 2-Step Verification requirement and account restrictions. This existing SMTP integration does not implement a separate Google OAuth login. TLS is required.

Configure the recipient and time, then safely reload the app:

```powershell
.\.venv\Scripts\python.exe scripts/daily_digest.py configure --recipient recipient@example.com --time 06:00
.\Start-ScorPred.ps1 -Restart
```

Inspect the actual running scheduler at `http://127.0.0.1:5001/api/local-runtime` under `daily_digest`. The `delivery` field reports credential readiness, not a successful authenticated send. A first successful SMTP submission is recorded only after the server accepts the message.

The same live status is available with `.\.venv\Scripts\python.exe scripts/daily_digest.py status`; if the local app cannot be reached, it falls back to configuration status with unknown scheduler registration.

Generate an unsent preview with the running local API:

```powershell
.\.venv\Scripts\python.exe scripts/daily_digest.py preview
```

The private preview and machine-readable report are in `persistent/notifications/daily-digest/latest-email.html` and `latest-report.json`. A manual `send` command uses the same delivery journal and requires configured mail credentials. Disable with `configure --recipient recipient@example.com --disabled`, followed by an app restart. Configuration, reports and delivery journals stay under the ignored persistent directory.

## Duplicate protection and failures

A process lock prevents concurrent generation/submission. The durable per-recipient, Eastern-date journal survives app restarts. A successful or uncertain SMTP submission cannot be automatically retried that day. An authentication/connect failure before submission is recorded separately and can be safely retried after fixing configuration. Secrets and credential-bearing exception messages are not logged or persisted.

Sources expiring during prop collection are refreshed once through their original APIs. Remaining expired forecasts are withdrawn. Kickoff/source/quote deadlines are checked again before SMTP submission; timestamps are never rewritten to make old data appear fresh. Failed, unsupported and stale sources cannot satisfy the selection target.

Slow schedule requests can also expire an earlier sport's short market window before score collection starts. The collector now renews each expired date and league partition once through its original dashboard API before selecting scores. Failed renewal or a withdrawn decision removes that partition; stale picks are never retained to meet a count.

## Verification

`tests/test_daily_digest.py` covers opt-in defaults, mail-readiness gating, recipient validation, separate sport measures, complete forecasts, honest shortfalls, SKIP preservation, roster filtering, source renewal/expiry, market targets, HTML escaping, preview behavior, durable deduplication, uncertain sends, process locking, scheduler registration and daylight-saving time. Existing backend regression tests remain required. Live previews exercise the existing NBA, soccer and NFL APIs without submitting email.

Implementation checks passed: 1,023 backend regression tests, followed by 35 focused digest/scheduler checks after the final safeguards. The live preview captured 12 match/score forecasts (NBA 2, soccer 5, NFL 5) and 12 props; missing selections were not padded. The running local scheduler registered its next run for 2026-10-24 13:00 Eastern. Actual unattended delivery was not tested or claimed because the mail credential is absent. The local launcher also allows up to three minutes for a cold backend startup, preserving singleton ownership.

On October 24, the user changed the daily time to 06:00 Eastern. After a managed restart the live scheduler reported its next run as October 25 at 06:00 EDT. Unattended sending remains blocked by the missing SMTP credential. A separately authorized one-off email was successfully sent through the connected Gmail tool on October 24 with five NBA matchup forecasts and five experimental historical-target props. Soccer APIs returned HTTP 503 and no qualified same-day NFL forecasts were available; neither section was padded. The connector's credentials remain separate from standalone SMTP delivery. Its success was recorded in the existing per-day delivery journal to prevent duplicate automatic submission.

October 24 verification: all 1,033 backend tests passed, including source renewal after a slow provider, failed renewal, withdrawn decisions and the 06:00 Eastern daylight-saving schedule. The live one-off refreshed NBA source data again immediately before Gmail submission and passed the same pregame/source-expiry checks. Actual scheduled SMTP delivery remains untested and blocked.
