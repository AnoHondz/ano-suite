# Email props on demand

Sign in, open the dashboard's **Best player props** section, choose All / NBA /
Soccer / NFL, and select **Email these props**. The selected date and sports are
sent to your signed-in email address. Collection runs in the background; the page
shows progress and mail-server acceptance. Return to the same selection to see
its saved status. A repeated click does not resend the same day's sport selection.

The app must stay running while collection finishes. A partial report names games
that could not load. Empty reports can be retried. If SMTP fails after submission
starts, delivery is marked uncertain and automatic resending is blocked: check
your mailbox before taking any further delivery action.

## One-time sender setup

The app cannot use the Gmail plugin attached to a chat. Its existing SMTP sender
requires `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER` and `SMTP_PASS` in the local `.env`.
For the existing Gmail SMTP sender, enter a Google app password as `SMTP_PASS`
locally. Do not put the password in chat or commit `.env`. Restart ScorPred after
changing the settings. The button shows a setup notice until credentials exist.
Credentials being present does not verify SMTP login; authentication failures are
shown as failed before submission. TLS is mandatory.

For a hidden local password prompt with an SMTP login check (no email is sent):

```powershell
.\.venv\Scripts\python.exe scripts\setup_email.py
```

## Command line

From the ScorPred folder, while its local server is running:

```powershell
.\.venv\Scripts\python.exe scripts\email_props.py --recipient you@example.com
.\.venv\Scripts\python.exe scripts\email_props.py --recipient you@example.com --sport nfl
```

The default date is today in America/New_York. Use `--date YYYY-MM-DD` to select
a different date. The CLI waits for collection and uses the same delivery journal
as the button. SMTP and provider failures never cause an automatic resend after
an ambiguous submission. Journals and prepared HTML are stored in the private
persistent notifications/props-email folder. No recurring schedule is enabled.
