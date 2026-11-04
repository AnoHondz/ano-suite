# Ano local command library

New verified workflows (no AI/API request):

- `check gpu` reads the PC's Windows-reported graphics adapter. AdapterRAM is not a verified dedicated-VRAM measurement. Live hardware questions such as `Which GPU do you have?` use the same read-only route.
- `create presentation Brief: {"title":"Supplied brief","slides":[{"title":"First slide","bullets":["Exact supplied point"]}]}` creates an editable PowerPoint from a bundled template and checks saved text.
- `remember fact NAME: VALUE`, `update fact NAME: VALUE`, `recall facts QUERY`, `show remembered facts`, and `forget fact NAME` manage explicit local facts with revisions and provenance. Name a preference `preference NAME` to make it available as local conversation context.
- `create pdf Summary: EXACT BODY TEXT` saves a new paginated PDF and checks extracted text.
- `create spreadsheet Sales: {"headers":["Month","Units"],"rows":[["Jan",20],["Feb",35]],"chart_column":2}` saves a styled XLSX with a numeric column chart. Cell values are read back. Optional `formulas`, such as `{"B4":"=SUM(B2:B3)"}` with a supplied blank total row, calculates supported formulas and checks their cached results. General functions and editing existing workbooks remain unfinished.
- While working, typed commands queue in their original chats. Menu → Tasks shows state and Cancel; Menu → Stop current task blocks further tool actions. Already dispatched actions cannot be recalled. Interrupted tasks pause after restart and are not automatically replayed.
- `make a word report about Ano` creates a native Word file from project facts and dispatches opening it. It verifies saved text; Office sign-in may still block Word itself.
- `write word document NAME: EXACT TEXT` saves a new `.docx` without overwriting an existing file.
- `read word document NAME` reads a workspace Word file as untrusted text.
- `revise word document Source: {"old":"Ravi","new":"Mira","output_name":"Revised","expected_count":1}` replaces exact inline body or table text without a model. It saves a separate `.docx` and a verified source backup. The match count must agree; existing outputs are preserved. Tracked changes, fields, hyperlinks and paragraph-spanning edits are unsupported. Replacement text inherits the first matched run's formatting.
- `draft word document NAME about TOPIC` uses the local writer and saves a verified `.docx`, labeled as an AI draft. This is not a no-model shortcut and does not verify factual accuracy.
- `show recent actions` lists tool names, outcomes and timings; payloads and document contents are not logged.
- `show local AI status` checks Ollama, loaded models and available RAM without generating an answer.
- `remind me tomorrow at 4:45 pm to send the invoice` uses a deterministic date/time parser. Explicit am/pm is required.
- `open word and then open calculator` preflights both commands before running. Unsupported steps do not partially execute through this direct route.

Common leading `opene`, `oppen` and `opn` typos are recognized. Literal document/note text is preserved.

Images: `generate image: PROMPT`, `generate portrait image: PROMPT`, or
`generate landscape image: PROMPT`. With the local SDXS model installed, generation
uses no API credits and saves to Pictures/Ano. Setup: run
`.\.venv\Scripts\python.exe -m ano.local_images install`, then restart Ano.
Short visual prompts work best; CPU generation may take minutes.

Local image understanding: the model tool `view_image_file` supplies actual pixels
from PNG/JPEG/WebP/BMP files in allowed folders, with metadata removed and a
bounded resolution. `read file` continues to extract OCR text. Visual interpretation
requires a configured vision model and is not verified just because a file was read.

Local commands: exact read/write/research commands work without an LLM. Original drafts use the local model. Use one command per message.

Interrupted tasks: open Menu → Tasks. A paused task offers Resume only when its
saved metadata confirms no screen/activity snapshot is required and every earlier
tool dispatch was read-only. Resumed replies stay in the original chat. Tasks
with changing or unknown actions remain held for destination inspection; completed
and cancelled tasks cannot resume. This does not yet resume arbitrary app workflows.

Apps: open calculator; open notepad; open word; open excel; open powerpoint; open outlook; open paint; open snipping tool; open spotify; open vs code; open visual studio code; open chrome; open brave; open edge

Websites: open canvas; open strayer; open github; open youtube; open google; open gmail; open google calendar; open google docs; open google sheets; open google drive; open wikipedia; open khan academy; open desmos; open overleaf; open outlook web

Folders: open my documents; open my downloads; open my desktop

Media: pause music; play music; play pause; next song; previous song; stop music; mute sound; toggle mute; volume up; volume down. Play/pause and mute are toggles; current playback is not read.

PC checks: check laptop health; check battery; check disk space; check memory; check network; check printers; check running apps; check windows version; check uptime; check time zone; check audio devices; check display devices; check storage devices; check print jobs; check network addresses; check recent updates

Windows: list windows; focus window 123; minimize window 123; maximize window 123; restore window 123.

Browser: list tabs; check browser connection. These require the existing extension.

Website work: show website tasks lists saved progress. Ask Ano to continue a saved website task after finishing login and enabling control.

Search: search web for TOPIC; search youtube for TOPIC; search github for TOPIC; find files named WORDS.

Read: read public page https://example.org; read file FULL_PATH; list folder FULL_PATH.

Documents: list documents; read document NAME; summarize document NAME; summarize file FULL_PATH.

Write exact text: write document NAME: YOUR TEXT; append document NAME: MORE TEXT; replace in document NAME: OLD => NEW. Only .md/.txt in AnoWorkspace; edits keep backups.

Research: research TOPIC. Reads up to three public sources and saves linked evidence notes, not a generated analysis.

Research writing: research and summarize TOPIC. Uses the local model for a short cited draft from fetched sources.

Original writing: draft document NAME about TOPIC. Uses the local LLM and saves a draft for your review.

Notes: take a note: TEXT; show my notes; find notes about WORDS.

Reminders: remind me in 25 minutes to TEXT; remind me at YYYY-MM-DDTHH:MM to TEXT; show reminders.

Math/time: calculate 24 times 17; calculate 15% of 80; what time is it; what is the date.

Workspaces: start study session; start coding session; start writing session.

Teach: teach routine NAME = open canvas -> open word. Save only; run with: run routine NAME.

Saved routines: budget worksheet, calendar review, capture workspace, coding, connection check, course notes, daily review, document workspace, email, focus timer, laptop check, math practice, presentations, project workspace, quick notes, research, spreadsheets, study, study resources, video learning, writing. Say “show routine NAME” to inspect before running.

Custom steps must be supported local commands. Up to eight steps; no shell commands, automatic submissions, or nested routines.


## Ano Sports desk and phone email

- `open sports desk` — native workspace with sport selection, report preview, email and phone controls.
- `email me picks` / `email me all sports picks` — supported NBA, soccer (Premier League, Bundesliga, La Liga), NFL reports and props.
- `email me nba picks` / `email me nba props` — targeted NBA report; soccer and nfl equivalents work too.
- `email me player props` — all supported sports' checked prop estimates.
- `enable email commands` / `disable email commands` / `email command status`.

Phone: reply to a new branded Ano email with ONE command on the FIRST line, preserving its [ANO:...] subject:
`picks`, `props`, `nba picks`, `nba props`, `soccer picks`, `soccer props`, `nfl picks`, `nfl props`, `status`, `help`, `stop email commands`.
Only new mail sent through your own you@example.com Gmail account is accepted. The reserved Gmail Sent label is checked from server metadata; a forged From address alone is not trusted. HTML-only replies, attachments, quoted history and arbitrary commands do not execute actions. No LLM calls, bets, purchases, arbitrary shell or forwarding. Unsupported commands return help.

The worker checks once a minute, one report at a time, with a 12-response rolling daily limit. A long report can delay later commands. The laptop must be signed in, awake and online; sign-in launch is configured in HKCU Run. Disabling email commands does not cancel the separately configured 6am daily digest. Ambiguous SMTP submissions are never automatically retried. Reports may omit unavailable data; prop checks cover up to four upcoming games per sport and eight unique player estimates per sport, not every possible player or league.

Email sender name and embedded image are Ano. Gmail's circular account photo is a separate account setting and is not changed by the report renderer. Current sportsbook offers are shown only when the source actually provides them; historical reference lines must not be presented as available bets.


### Email questions and paper-runner status

You can also reply with a short question on the first line. Common questions about CONSIDER and historical prop lines are answered deterministically. Other questions use `ano-local:latest` on localhost with no action tools, no web access and no paid fallback. Answers are labeled local-model explanations; factual quality is not established by transport tests. Long or HTML-only messages, attachments and quoted history are not action instructions. Read-only explanations cannot run laptop commands.

- Phone `nba matchups` / `compare nba matchups` requests NBA picks/props with available team comparisons.
- Phone `trading status` sends local ALPACA PAPER loop/ledger health. It does not verify broker exposure or compute P&L from acknowledgements.
- Native `email me paper trading health` sends the same diagnostics.

The paper execution loop writes `paper-execution.health.json` after each pass, including recovery readiness and error categories. A reader marks the heartbeat stale after 90 seconds. No trading strategy or order permissions were expanded.


### Threaded email follow-ups and broker snapshots

Reply to a newly generated Ano report (keep its subject and use Reply) with `props too`, `tomorrow instead`, or `same for nfl`. Ano resolves the sport/date from that particular verified outgoing message, not another thread's latest report. Without a verified parent report it asks you to name the sport/date. Explicit requests such as `nba props tomorrow` and `email me nba picks 2026-10-29` work in email. Other questions can include the previous exchange from the matching thread; explanations remain read-only/local with no paid fallback.

`broker status` in an authenticated email requests an on-demand Alpaca PAPER read through a fixed GET-only helper. It checks account availability, total account positions, bounded open orders and locally tracked unfinished entries. It never places/cancels/changes orders. No position is attributed to Ano solely because its symbol matches. Active bracket legs are checked against saved side, quantity and intended stop/target prices. Separate closing fills require their own saved receipt and exact filled quantity. Incomplete reads never mean flat or protected. `trading status` remains a local-loop/ledger check, not a broker query.

Delivery recovery uses a per-job OS-held lock to avoid interrupting live work. Interrupted preparing jobs are reported failed before submission; ambiguous submissions are checked for an existing self-Gmail inbox copy and never automatically resent. An absent inbox copy remains uncertain (it could have been archived); legacy jobs without verified lock ownership are left for inspection. Re-enabling the worker preserves its existing inbox cursor.

### Natural desktop sports emails

`email me scorpred`, `email me NBA picks tomorrow`, and `email me NBA props
2026-10-30` use the fixed premium report sender and its delivery receipts.
`email me` / `email me that` immediately after a sports request use that user
request's sport/date in the current chat. Without a clear sports context, Ano
asks which report; assistant-generated text and quoted requests are never used
as authority to email. Reports go to the configured self address, not an inferred
recipient. These commands do not call an LLM to rewrite or invent statistics.

`nba scorpred player` / `NBA player props` read the roster-verified player report
with labeled metrics, sample counts and available observation times. Match,
roster and player-source timestamps are separate; missing evidence remains
unavailable. Historical targets are not verified sportsbook offers.

If the general Gmail drafting tool is used for a self-addressed sports summary,
it still requires the Send click in **Ano's outbox**, not Gmail drafts. Its MIME
message includes premium HTML and the inline logo, along with plain text. This
path is explicitly labeled a reviewed summary rather than a fresh provider
report. Its replies are not monitored; use a new report's authenticated thread
for phone commands. Ordinary emails retain their existing formatting.

### Reliability and paper readiness

- `ano health`: shared ScorPred, paper-runner, email-command and research health.
- `trading bot readiness` / `get trading bot ready`: add a fresh GET-only paper
  account, position and order reconciliation. This never enables execution.
- `open reliability dashboard`: open the native health window. Its verification
  button reads the broker; it has no order or email controls.
- `run all trading models`: configure ICT, gap fill, SMC, Silver Bullet, AMD,
  opening range, Turtle Soup intraday and technical research for QQQ/DIA. Research runs on weekdays
  09:25–15:10 New York time and never expands execution permission.

The separately authorized `gap_fill_v1_2` paper strategy retains its rules.
Additional experimental paper adapters require explicit enablement as described
below; live trading is disabled. New gap-fill entries require all five closed opening
minute bars, the previous session's final minute, a current-week economic
calendar, verified empty account exposure and the existing risk gates. Sparse
IEX data is not proof of an unchanged consolidated price.

Broker evidence now verifies the saved entry identity/quantity and intended
stop/target prices. Partial entries must be confirmed final before a separate
close. Bracket cancellations are read back before closing the exact remaining
position. Ambiguous close dispatches are reserved durably and never replayed;
unresolved fills, replaced orders or mixed manual exposure require inspection.

Chat insights, the sports desk and native report emails use the same snapshot
adapter and local forecast ledger. All supported winner probabilities, including
SKIP, are recorded with immutable first-seen timestamps. NFL scenario shares
are not treated as win probabilities. Only the first eligible pre-event forecast
per event/model is scored against separately verified completed scoreboards.
Unknown providers, mismatched teams/times and missing final scores stay pending.
The baseline is uniform chance, not bookmaker odds or a profitability claim.
The scheduled ScorPred digest retains its own delivery and filtering gates.

Ano records real health observations every minute while running, with change
notifications for failures/recoveries. The seven-day observation period starts
with actual samples; elapsed time or unobserved periods never count as a pass.
Read `reliability/latest.json` and `reliability/monitor.health.json` under Ano's
state directory for diagnostics. A healthy loop is not proof of a profitable
strategy or authorization to submit an order.

An already enabled email-command service resumes when Ano opens, retaining its
authenticated mailbox cursor and receipt-based duplicate protection. Disabled
or uninitialized services stay off; `enable email commands` remains the opt-in.

`activate my trading bot`, `enable my paper trading bot` and
`start gap_fill_v1_2` now use the existing gap-fill paper executor directly,
without an LLM or a separate executable. This enables its existing rules;
it does not force an immediate order or bypass session/data/risk checks.
`what is gap_fill_v1_2` / `whats this gap filler` explain the strategy locally.
`best research model` / `let the best research model make a trade` read the
saved research results and report their limits without inventing a winner.
Performance-based best-model ranking is not implemented. Saved simulation
passes alone never grant another strategy order permission. Explicitly enabled
adapters use the shared first-valid-signal/risk checks described below.

Turtle Soup intraday v1 uses 20 consecutive closed one-minute RTH bars as its reference, requires the prior extreme to be at least four bars old, and confirms a sweep with a close back inside. It is an explicit intraday research adaptation, not the original 20-day strategy. Both-side sweeps, missing/stale bars and invalid prices mean WAIT. Stop/target values are research references, never order or fill receipts. Use `run turtle soup on qqq and dia` or `run all trading models`.

### Experimental multi-strategy paper execution

Explicit user authorization on 2026-10-29 permits the following named paper
adapters. `allow them to place trades`, `start all paper strategies`, or
`enable paper trading for all strategies` enables them together with the existing
gap-fill runner. Research launch commands preserve execution permissions.
`paper strategy status` shows actual permissions, daily entry count and recent
candidate decisions. `stop paper trading` disables all strategy entries, clears
queued plans and manages owned exposure to close. Permissions persist across
normal Ano restarts; uncertain submissions are never replayed.

| Adapter | Complete entry trigger | Exit cutoff (New York) |
|---|---|---|
| ICT 2022 | Fresh grade A/B confirmed sweep/MSS/FVG, still waiting for entry; existing liquidity target retained | 15:55 |
| Silver Bullet | Same fresh plan plus its named-liquidity/15-minute-bias/window filters | 11:00 or 15:00 window end |
| Turtle Soup intraday | Current closed 20-minute failed-breakout/reclaim signal | 15:55 |
| SMC retest v1 | Confirmed block/breaker retested later, current rejection candle, aligned completed daily/4h/1h bias | 15:55 |
| AMD-aligned ICT v1 | Fresh complete ICT plan with AMD alignment, within morning window | 15:55 |
| Opening range v1 | First current close beyond a complete contiguous 30-minute opening range; stop at range midpoint | 12:00 |
| EMA crossover v1 | Fresh 20/50 EMA crossover, RSI 50–70 long or 30–50 short, 1.5 ATR stop; at least 51 observed closed RTH bars | 15:55 |

These are explicit experimental execution variants, not canonical coverage or
validated profitability. Turtle Soup, SMC, opening-range and EMA targets use
two net risk units after the configured $0.02 per-share round-trip allowance
(not the scanner's raw 2R illustration). ICT/Silver Bullet/AMD retain their
original targets and skip plans that fail net reward/risk. Actual fills, fees and
slippage can differ from planning assumptions.

The single independent executor shares account exposure and a ledger across
all eight strategies: one position/pending entry at a time; at most three entry
attempts per day; a 0.25% planned per-entry risk budget; two fill-verified
consecutive-loss stop; and a 0.75% account daily-loss halt. Unknown prior closing
results block new entries. Buying power further caps quantities. A daily-loss
halt persists for that session and disables entries; re-enabling cannot bypass
the recorded same-day halt.

New adapters require fresh closed bars and a fresh non-crossed IEX quote,
bounded spread/price drift, exchange calendar, current-week news, and complete
empty-account checks including manual orders. They submit day limit brackets
only to Alpaca PAPER. Unfilled entries expire after at most two minutes; actual
exchange close and strategy windows can shorten that deadline. Cancellation
and protective exits use the same verified lifecycle as gap fill. Ano must
remain running for timed cancellation/flattening. No real-money endpoint or
direct model-callable order tool was added. The shared executor accepts the
first complete risk-qualified signal; it does not declare a model "best."


OANDA demo forex (practice endpoint only):
- `forex status` / `oanda status`: local runner, scan times and per-pair evidence.
- `start forex paper trading`: enable seven experimental session-relative variants on the configured four forex pairs.
- `stop forex trading`: disable entries and manage owned demo trades to close.
- `stop paper trading`: stops both the ETF and forex entries.
- `python -m ano.configure forex`: verify account and pairs; store credentials in Windows Credential Manager.

Forex scanners use explicit session-relative variants. EUR/USD and GBP/USD scan London 08:00-17:00 Europe/London as well as New York. USD/CAD retains New York; USD/JPY scans Tokyo 09:00-15:00 Asia/Tokyo. London follows UK daylight saving. There is no equity gap-fill strategy on forex.
OANDA has its own practice-account risk budget: one trade at a time, 0.25% planned risk per entry,
3 attempts per NY date, two consecutive losses, 0.75% NAV loss since that date's first observation,
and at most 3x notional. A new date creates a new persisted baseline; this is not a midnight account statement.
Management runs every 15 seconds independently of minute scans. Broker-held stop/target orders remain
active while Ano is offline; session/time exits and reporting require Ano running.
Entry alerts require broker GET verification. OANDA summaries arrive five minutes after each observed session: 17:05 London, 15:05 Tokyo and 16:05 New York. All windows share the same account-wide position, loss-streak and three-attempt NY-date limits. JPY uses its broker-reported 0.001 price tick, 0.01 pip and current JPY-to-USD risk conversion.
Unknown order/close outcomes block further entries and are not retried; inspect forex status and the demo account.


Keep-alive supervision:
- `supervisor status` / `keepalive status`: local observation, component checks and AC sleep-request state.
- Start with Windows uses the current user's Run key to start the windowless supervisor.
- It checks every 30 seconds, starts missing Ano/email workers, and starts ScorPred only after three refused-connection observations.
- Recovery is limited to three launch attempts/hour/component, at least 60 seconds apart. No live process is killed or replaced merely for stale health.
- Quit Ano suspends recovery until a manual open or the next Windows sign-in. Disabling Start with Windows also stops the supervisor on its next check.
- On AC power the supervisor requests system wakefulness; the display may turn off. Battery sleep, explicit sleep, shutdown, lid policy and internet outages are not overridden.
- Trading session windows, privacy/pause controls, broker reconciliation and all risk stops remain in force. Supervision submits no orders or emails.


Evidence and quiet summaries:
- `forex cancellations` / `why no forex trades`: durable, identity-verified OANDA cancellation transaction reasons; no entry retry.
- `trading performance` / `strategy performance`: fill-verified closes grouped by provider, strategy, instrument, session and cost basis. ETF net figures use a configured cost allowance; OANDA uses reported costs. Realized closed-trade drawdown is not intratrade drawdown. Missing chronology remains unknown. No automatic promotion or profitability claim.
- `scorpred accuracy`: first eligible pre-event observation per event/model, verified finals, uniform-baseline Brier and separate model/data-quality cohorts. Research-only WAIT forecasts are labelled separately. Unsettled outcomes remain unscored.
- A quiet automatic observer captures supported dashboard forecasts for today/tomorrow every five minutes and verifies finals separately. It does not request reports, send emails or place bets.
- Sports picks may show labelled provisional research leans when a fresh pregame model exists but prices, confirmed lineups or injury coverage are incomplete. Missing inputs remain visible; this does not establish a verified betting edge or promote SKIP to BET. Fresh complete-evidence SKIP forecasts may also appear as research picks with no bet recommended. Missing/stale model sources and started games remain WAIT. NFL scenario shares are not win probabilities. Props keep their separate verification requirements.
- Routine research, validation, cancellation and health-state messages stay out of trading pop-ups. Session summaries include actual results, unresolved problems, top skipped-check reasons and verified broker cancellations.
- Supervisor status requires both a fresh heartbeat and a matching live owner. Ano recovers a missing helper; a live stale owner is flagged rather than force-killed. Controlled exits leave an exit record and helper launch output is logged.
