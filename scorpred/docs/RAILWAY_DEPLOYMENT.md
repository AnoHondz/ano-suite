# Railway cloud deployment

Target: a single always-running Railway Pro service, one replica, with a private
persistent volume at `/var/data`. Pro is required for the existing Gmail SMTP
sender. Pricing is a $20 monthly minimum commitment with excess resource usage
billed separately, not a fixed $20 total. Keep service sleeping/serverless off.

`railway.json` selects the multi-stage Docker build. The Python runtime serves
the compiled React frontend. Gunicorn uses the existing single-worker config so
there is one scheduler. `/health` is the deployment readiness check.

## Required service variables

Set these as Railway variables, never in Git or the Docker image:

- `SECRET_KEY`: a strong stable secret (reuse the current one only through a
  secure transfer if preserving accounts/sessions is intended).
- `FOOTBALL_DATA_KEY`, `ODDS_API_KEY`, `NBA_API_KEY`: existing configured provider
  credentials as required. Unconfigured optional providers remain unavailable.
- `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER`, `SMTP_PASS`.
- `DAILY_DIGEST_ENABLED=false` for staging and migration verification.
- After verifying the cloud report: `DAILY_DIGEST_RECIPIENT=you@example.com`,
  `DAILY_DIGEST_TIME=06:00`, and `DAILY_DIGEST_ENABLED=true`.

The existing digest scheduler uses `America/New_York`, including daylight-saving
changes. It is not a UTC-only Railway cron. Keep local delivery enabled until the
cloud is verified, then disable the local daily digest before enabling the cloud
schedule to prevent duplicate emails.

The image configures `/var/data/scorpred` as the data root and
`/var/data/scorpred/persistent` as persistent storage. Attach the volume before
using the app: accounts, imported data, caches, and delivery journals depend on it.
The volume must fit actual data and future growth. Monitor usage and memory; no
fixed monthly total or minimum memory requirement has been established.

## NFL serving data

The image includes the existing nflreadpy importer. With
`SCORPRED_NFL_SYNC_ENABLED=1`, the scheduler synchronizes schedules, teams, the
player-ID directory, weekly player statistics, and injuries after startup and
every six hours. It requests the current NFL season and the previous season for
player priors. It does not copy/import the 6.5 GB local play-by-play and research
archive, train any model, or activate research predictors. The first NFL props
request can remain unavailable until the initial import completes. Upstream
availability and licensing limits still apply.

## Deployment and cutover checks

1. Connect Railway and choose the intended project/environment; approve any plan
   or paid resource creation before it is performed.
2. Publish the reviewed source without `.env`, `.runtime`, `persistent`, `cache`,
   transfer bundles, or local account databases. GitHub is currently public;
   private account/data migration must use a separate secure channel.
3. Create one service and persistent volume; provide secrets as variables with
   daily delivery disabled. Build and inspect logs, memory, storage, and health.
4. Check the soccer/NBA/NFL APIs and the first small NFL sync. Verify models and
   props without copying sealed research outcomes or changing freshness limits.
5. If retaining existing logins and saved picks, transfer a consistent auth DB
   snapshot and delivery journal privately while writes are stopped. Do not
   overwrite an existing cloud database. Otherwise create a new account.
6. Verify SMTP login and a deliberate cloud test email. Then switch the daily
   sender from local to cloud and confirm the next 6 AM Eastern run.

ChatGPT scheduling is optional after cloud deployment. The app's own scheduler
can deliver without depending on a ChatGPT session or the laptop being awake.

## Verification performed locally

The Railway configuration passed the official Railway JSON schema. The existing
model manifest checksum was verified. Docker is not installed on this laptop, so
the Linux image has not been built or smoke-tested locally; a Railway build and
live checks remain required before claiming successful deployment.
