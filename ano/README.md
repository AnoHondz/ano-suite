# Ano

Performance update: the default local `fast` profile sends tools relevant to the
latest task and exposes `find_tool` for additional capabilities. Ordinary
conversation can use the installed `ano-chat:latest` Qwen3 0.6B helper; actions,
web tasks, and screenshots stay with the existing `ano-local:latest` agent.
Both are loopback-only. Simple commands and image requests bypass chat inference.
Pause interrupts local model waits and discards late tool calls. Local requests
receive a bounded 3–15 minute per-call allowance based on prompt size and output
limit. Short answers return as soon as ready. Wait status updates every five seconds,
and Stop/Pause still interrupts the local wait. The fast profile keeps six recent messages plus the latest
long source through follow-ups; oversized requests explicitly ask for smaller
sections. Startup checks the database before writing. Use **Menu → Quit Ano**
for a clean shutdown before applying updates or repairing data.

Text-only tool planning can use the installed `ano-agent:latest` lightweight local
model (`local_agent_model` setting); screenshots and returned tool images switch
to the existing vision model without replaying the tool. Conversation and drafting
also have separate short prompts. Ordinary action turns have a bounded 10–30 minute
allowance; conversation/drafting use the single-call allowance. Explicit Free roam
removes the overall local turn deadline while retaining per-call and cancellation
bounds. Paid-route limits are unchanged. These allowances prevent premature cold
prompt cancellation; they do not make CPU inference faster or guarantee completion.

Startup commands run directly: **show startup apps**, **start Ano with Windows**,
**stop Ano starting with Windows**, and **open startup settings**. Registration
does not prove an app is enabled. Only Ano's own startup entry can be changed by
these tools; other apps remain a choice in Windows Startup Apps. Ano does not claim
that opening the settings page disabled them.

**Local by default; paid tasks require an explicit prefix each time.** Type
`paid: your hard task` for OpenAI (GPT-4.1 by default), or `paid usage` to read Ano's
local spending ledger. Normal messages never load the paid key or trigger paid fallback.
To make OpenAI the brain for every message instead, run
`python -m ano.configure brain openai` (or tick **Use OpenAI brain** in the menu);
then `local: your message` uses free local AI for one turn, and
`python -m ano.configure brain local` switches back. Save an
OpenAI key with `python -m ano.configure openai` (hidden input, Windows Credential
Manager). No Claude provider is enabled. Voice and image generation remain local.
Paid limits: 24 API calls and 10 minutes per task, 2,048 output tokens per call (including reasoning),
50¢ per-task estimate, $2 per UTC day and $20 total in Ano. The OpenAI brain sees every tool
on every request; built-in commands such as "open calculator" still run free first. Persistent reservations
are made before requests; uncertain network failures retain their reservation.
No SDK retries, automatic model upgrades, or paid background requests. These are
conservative estimates at published prices, not a provider-side account cap or
balance: other apps using the key are outside Ano's ledger. GPT-5 nano retires
December 11, 2026; Ano blocks it from that date without auto-upgrading. Configure
the loopback-only local model with `python -m ano.configure local`.

Task recovery: Claude, GPT and OpenAI-compatible providers now check unresolved
tool failures and explicit uncertain desktop-input receipts before ending a reply.
They can request up to two recovery continuations within the existing step budget.
Fresh desktop reads/views resolve the need to inspect; they do not by themselves
prove the user's whole task succeeded. Unresolved results are reported as unverified,
with instructions to inspect first and never blindly replay input or submissions.
Stop, Pause, locking, approval modes and existing execution limits still apply.
Recovery bookkeeping retains tool names only, not input values or screenshots.

A Windows desktop assistant foundation built with Python, PySide6/QML, and SQLite FTS5.

### Reliability update — October 26, 2026

Native `.docx` creation and text read-back now work without a model through the
Word document tools. Try `make a word report about Ano`, or
`write word document Notes: Your exact text`. New files never overwrite existing
documents. Opening is reported as a Windows dispatch, not proof that Word showed
the file; Office setup/sign-in can still require the user.

Word reading includes body paragraphs and table cells in document order, including
nested tables and merged-cell text once. It does not extract headers, footers,
text boxes or text inside images; use a separate review for those document parts.

For a local AI draft, use `draft word document NAME about TOPIC`. The output is
labeled as an AI draft; saving and paragraph read-back are verified, while factual
accuracy still needs review. For quick numeric questions, try `What is 24 times 17?`
or `What is 15% of 80?`; supported arithmetic uses the bounded calculator, without
model inference. Unsupported requests still follow the normal model route.

`show recent actions` reads a bounded local log of tool names, outcomes and
timings, excluding arguments and contents. `show local AI status` checks local
model inventory and RAM without inference. Explicit day/time reminders and
preflighted chains such as `open word and then open calculator` avoid model calls.

Memory uses rollback journaling with full synchronization, avoiding exposure to
the WAL-reset race in older SQLite builds. Startup creates one integrity-checked
local snapshot per day under the app data directory's `backups` folder. No prior
snapshot is replaced. Recovery can be partial; preserve damaged originals.

On this installation, migrated state lives in `Documents\AnoWorkspace\.ano-state`.
Both packaged development launches and normal desktop launches use that same
folder after a completed migration marker is present. This avoids split AppData
copies under Windows package virtualization. Original locations and both source
databases are preserved; imported chats have separate IDs. `ANO_DATA_DIR` remains
an explicit override, including for isolated tests. App setup, voice setup, image
assets and local runtime checks share the same resolver.

Benchmarks under `.runtime` distinguish synthetic model tool-selection probes,
isolated local file/note integration checks, and live UI observations. None proves
general autonomous reliability. No model weights were trained in this update.

## Version 0.1.3 — ordinary chat context correction

Ordinary chat and drafting no longer receive an unrelated current-clock prefix.
A live acceptance check showed the small model copying that clock instead of
using supplied fictional day/time facts. The same two-turn case returned the
correct fictional answers after removing the prefix. Action workflows retain
time context where scheduling and tool arguments need it. This targeted fix does
not establish general reasoning quality or ChatGPT Desktop feature parity.

## Version 0.1.2 — playlist tool selection and no-action recovery

Playlist requests now advertise browser-workflow and desktop inspection/control
tools, rather than only playback controls. If a local action request returns
plain text before any action tool runs, Ano makes one recovery attempt asking
for read-only discovery first. When configured, the larger local vision/agent
model handles that attempt. Existing deadlines, Pause, permissions and checks
still apply. No-action recovery never replays an action that already ran and
never uses a paid model. Ordinary chat, writing-only and paid profiles do not
use this recovery path.

A controlled local probe of the reported playlist request produced a valid
`list_windows` selection after recovery. No returned tool was executed in that
probe. It is evidence of a planning improvement, not a completed playlist or a
general model-quality guarantee.

## Version 0.1.1 — conversation routing correction

Capability questions such as `what are yr capabilities` show the actual local
command catalog. `stop scheduler` cancels Ano's waiting scheduled actions and
disables its watches, reporting exact counts; already dispatched input cannot be
recalled. `scheduler status` and `did you stop the most recent scheduler` read
current state instead of asking a model to guess what happened earlier. These
commands only affect Ano's own scheduler.

The installed `ano-agent` and `ano-chat` aliases are small Qwen 0.6B models,
not trained upgrades. Their speed does not establish reasoning quality. To use
the installed full local model and clear the small-model overrides, run:

```powershell
.\.venv\Scripts\python.exe -m ano.configure local ano-local:latest
```

Quit and restart Ano to apply model settings. This remains local and can be
slower. It does not guarantee complex task completion. Computer-control sessions
must be enabled again after restarting. Double-click `Launch Ano.cmd` in this
checkout to open the updated app with its existing environment and saved data.

## Run on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m ano
```

## Recovering a damaged local database

Quit Ano before recovery. First resolve the active data directory from this checkout:

```powershell
.\.venv\Scripts\python.exe -c "from ano.paths import data_directory; print(data_directory())"
```

Back up `memory.sqlite3` and any matching `-wal` and `-shm` files together from
that directory. On this migrated installation it is normally
`Documents\AnoWorkspace\.ano-state`; `%LOCALAPPDATA%\Ano\Ano` is the legacy
fallback. `ANO_DATA_DIR` overrides both. Keep the backup intact.
Recover from a separate working copy into a new, nonexistent destination:

```powershell
.\.venv\Scripts\python.exe tools/recover_memory.py PATH_TO_COPY\memory.sqlite3 PATH_TO_NEW\memory.sqlite3
```

The tool preserves readable settings, messages, activity, notes, reminders and
browser tasks, reconstructs missing chat records, rebuilds search indexes, and
checks database integrity. Its report lists unreadable tables and invalid rows;
recovery can be partial. It never replaces the source or an existing destination.
Only install the recovered database while Ano is stopped, retaining the original
database and sidecar files in the backup directory.

## Included in this milestone

- A compact chat panel docked in the bottom-right corner above the taskbar (380 px wide, up to 62% of the screen height; move or resize it freely). A top bar with Ano's live face and a **⋯** menu, chat bubbles, and a message box with **mic** and **send** buttons. It follows Windows light/dark mode.
- Local message storage and full-text history search (**⋯ → Search messages**).
- System tray: open, pause/resume, quit. Closing the panel hides it to the tray when available.
- Drag the chat panel by its top area (Ano icon, name, or the empty space beside them) to move it out of the way. Its position is remembered and restored on an available screen; toolbar buttons remain independently clickable.
- The chat is a clean, rounded, frameless panel with a subtle shadow. Its top-right collapse button leaves only Ano’s floating icon, with no cloud or background card: tap to reopen, or drag to reposition it. The icon defaults to the right edge of the screen and remembers its position. Light and dark colors follow Windows.
- Windows global shortcut **Ctrl+Alt+A** to show/hide. A shortcut conflict is reported in the tray tooltip.
- Pause (**⋯ → Pause**, or the tray) blocks message submission and shows a **Resume** bar.
- Enter sends to Claude when connected, or saves locally without a key; Shift+Enter inserts a newline.
- Select response text and press **Ctrl+C**, or use the **Copy response** icon beneath an Ano reply to copy the entire original response (including Markdown formatting).
- **⋯ → Explain my screen** hides Ano, captures the foreground application's monitor, then restores Ano with an approval preview.
- **⋯ → Activity** opens a searchable local timeline of foreground apps and window titles, with tracking and delete controls.
- The **mic** button (hold to talk) transcribes speech offline and sends it when you release. **⋯ → Speak replies** enables optional Windows speech output.
- **⋯ → Files Ano can read** lets Claude find, read, and open documents in folders you choose, and open websites in your browser.
- **PC troubleshooting**: Claude checks why the PC is slow or something fails, explains it, and offers fixes that run only after you click **Run fix**.
- **Plugins**: Gmail (send only after you review), Google Calendar, opening apps, notes and reminders, and media keys.
- **Web research**: Claude searches the web and reads pages when an answer needs current or specialised information, and lists its sources.
- **⋯ → Colour** offers blue, purple, and amber accents, saved on this device.

The interface uses neutral light/dark surfaces, Segoe UI typography, restrained accents, and compact rounded controls. Hearts and the pink background wash are removed. Ano keeps its existing app icon and a smaller animated state indicator (`qml/AnoFace.qml`); tapping to giggle is disabled in the chat interface. Conversation rows use quieter styling without repeated avatars or speech-bubble tails. Icons inside the window use Windows 11's Segoe Fluent Icons font, falling back to Segoe MDL2 Assets on Windows 10.

Launching Ano again raises the existing panel instead of starting a second process against the same data directory. After this update, quit any older running copies once before restarting so the new single-instance guard takes effect.

When a Claude key is configured, Send calls Anthropic's Messages API in a background thread. Up to 20 recent messages are sent as context. The latest user message and most recent long source table are preserved whole up to 64,000 characters each; other history uses a 24,000-character budget. Responses are stored locally. The default model is `claude-opus-5-5` at high effort; override it with `ANO_CLAUDE_MODEL`. Requests opt into Anthropic's server-side refusal fallback (`fallbacks: "default"`). Each call uses a 300-second timeout, no automatic retries, and a 16,000-token output limit; a normal request that uses tools makes up to 24 calls; a user-selected Free roam session continues until completion, Stop, or repeated failures. API usage is billed by Anthropic.

## Computer control permissions and saved logins

**Free roam** grants session-only access to readable files across local fixed/removable drives, terminal/IDE terminal input, Windows-key shortcuts, and automatic browser/desktop actions. Launch it from **⋯ → Computer control → Free roam (this session)**. It continues past normal execution cutoffs until finished, stopped, or blocked by repeated failures. AI API usage still applies. Stop, Esc, Pause, lock, closing Ano or switching modes revokes the expanded access; folder preferences are never overwritten. Credential use, email sending and protected system/security interfaces retain their existing controls.

Install the Windows control dependencies if they are not already installed:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[control]'
```

Restart Ano and open **⋯ → Computer control**. Choose **Approve each step**, **Full permission (this session)** or **Free roam (this session)**; each starts a fresh control session. Control starts off on every launch. Full permission shares app views and runs supported desktop/browser actions without per-step popups. The active mode appears above chat. Stop, Pause, lock, closing Ano, or changing modes invalidates old actions; full permission is never saved across sessions. Password-vault use, email sending, and system fixes retain their separate review flows. Ask for a task in an ordinary app, for example, “In Notepad, type Hello Ano.” Ano uses `list_windows`, `desktop_observe`, and `desktop_input`:

- A single restored app window is captured while Ano is hidden. In Approve each step mode, the **Share this app view?** preview must be approved before its image reaches Claude or GPT. Full permission shares automatically. Images remain in memory, not files or SQLite.
- In Approve each step mode, every mouse/keyboard input has its own **Run this action** review with the target, screenshot, purpose, and exact text or keys. Full permission runs these automatically. Available inputs are move, left/right single/double click, drag, scroll, Unicode typing (up to 2,000 characters), and named key combinations. Coordinates refer to the displayed screenshot; Ano maps them to the window's physical pixels.
- Each observation is single-use and expires after 60 seconds. Window identity, executable, title, bounds, and foreground focus are rechecked. The model must observe again to verify an action. Reviewed and Full permission sessions permit at most 50 inputs; Free roam removes this input cutoff and the normal 24-call AI cutoff. Each human approval expires after 90 seconds.
- **Stop control**, Escape, Ctrl+Alt+A, Pause, closing Ano, and Windows lock invalidate pending desktop actions. Input already delivered to an app cannot be recalled. Stop control affects desktop control; Pause also blocks the other tool plugins.
- Free roam permits terminals, IDE terminal panes and Windows-key shortcuts for user-requested work. Other modes use **Run fix** for reviewed commands. Credential-manager/security windows, elevated/protected targets and password fields remain excluded from generic input. Some apps do not expose enough accessibility information and will require manual interaction. Ano refuses input when Windows will not grant foreground focus, rather than sending it to another app.

All modes retain observation expiry, target checks, bounded individual payloads and Windows permission checks. Screen content and UI labels are untrusted data, not instructions. Automatic Claude-to-GPT fallback is skipped after a tool has run, to avoid silently repeating actions.

Full permission and Free roam return a fresh target-window image and new observation ID with each successful desktop input, saving a separate model round trip just to request the next view. Ano still verifies that image before continuing; if the app is still loading it can request another observation. If capture fails after input, the result explicitly reports that the action already happened and must not be replayed. Capture waits 150 ms when hiding a visible panel and uses no hide delay when already hidden. An already-focused target no longer incurs a fixed focus wait. Cloud reasoning and network latency still affect overall speed.

Open **⋯ → Passwords** to add or remove website and Windows-app logins. Passwords and metadata are stored in **Windows Credential Manager**, using bounded entries and a versioned, paged index. A named Windows mutex serializes vault writes across Ano instances. They are not written into Ano's SQLite database or chat. This uses your signed-in Windows account's protection; it is not an independent master-password system. Secrets necessarily exist briefly in local process/browser memory while filling.

Ask “Use my saved Example login in this tab.” The AI can list only login ids, labels, and target sites/apps. `request_saved_login` always asks for **Allow this login once**, showing the account and target without revealing the password. No model tool can save, retrieve, delete, or approve a password directly.

- **Website logins:** require HTTPS and the updated Ano browser extension. Matching uses the exact origin (scheme, hostname, port), not a similar name or parent domain. The extension pins the document id and rechecks origin and password-field type immediately before filling. Main-frame forms with one visible password field are supported; embedded/ambiguous/new-password forms are refused. Username and password are filled locally; the form is never submitted.
- **Windows-app logins:** save the actual app `.exe` path. Enter the username yourself and focus its password field before asking Ano to use that login. The executable path, process, UI Automation password property, and field runtime identity must match; only a writable value pattern is used. Unsupported or protected fields must be filled manually. Browser executables cannot be used to bypass website-origin checks.
- Opening the vault or starting a login request stops computer control, microphone input, speech, and activity tracking. Screen capture and model tools are blocked. Once credentials may have been filled, this **Private login** state remains until you finish logging in yourself and click **Done with login**. Computer control stays off until explicitly enabled again. Pending approvals are cancelled by Pause/lock; an already-issued fill cannot be recalled.

The extension files have been updated. To deploy the update to your configured extension folder and then reload it:

```powershell
.\.venv\Scripts\python.exe -m ano.configure browser
```

Open your browser's Extensions page and click **Reload** on Ano. If it is not installed yet, follow that command's Load unpacked instructions. Do not paste credentials into chat; enter them in the Passwords panel.

Validation: unit tests and the QML smoke test exercise approval/nonces, cancellation, origin/document binding, private-mode retention, vault entry paging, and safe provider image payloads using dummy data. A separate test passed against real Windows Credential Manager with 22 synthetic entries, then removed them. `node tests/check_extension_login.cjs` checks the extension against a synthetic DOM. The native input probe (`python tests/check_native_control.py`) was blocked by Windows foreground-focus protection when run from the background development runner; live mouse/keyboard and native password-field filling therefore still require an interactive test after launching Ano normally. No real account credentials were used in these checks.

## Service plugins

Plugins live in `ano/plugins/`. Each adds tools with `definitions()` and `run(name, arguments)`, and `app.py` registers it with the Toolbox. Pause and Windows lock block all of them.

| Plugin | Ask, for example | What it can do | Setup |
|---|---|---|---|
| Gmail (`mail.py`) | “Any unread email from Alex?”, “Reply saying Thursday works” | Search with Gmail syntax, read emails (never marks them read), prepare emails and replies | `python -m ano.configure gmail` |
| Google Calendar (`schedule.py`) | “What's on this week?”, “Add dentist Tuesday 2:30” | List events, including recurring ones; open a pre-filled new event | `python -m ano.configure calendar` to read; adding needs no setup |
| Apps (`apps.py`) | “Open Spotify” | Open any Start menu app by name, with no arguments | None |
| Notes and reminders (`notes.py`) | “Remind me at 5 to call Mum”, “Note the wifi code is…” | Local notes; reminders as Windows notifications plus a chat message, spoken if Speak replies is on | None |
| Media (`media.py`) | “Pause the music”, “Volume up a bit” | Play/pause, next, previous, stop, mute, volume steps | None |

**Email is never sent by Claude.** `send_email` only fills in the **Review email** window. You can edit the subject and text there, and only your **Send** click sends it, through Gmail SMTP; it then appears in Gmail's Sent folder. Cancel, Escape, Pause, or Windows lock discards it. The Gmail address and [app password](https://myaccount.google.com/apppasswords) (2-Step Verification required) are checked once and stored in Windows Credential Manager under `Ano/Gmail`. Revoking the app password in your Google account disconnects Ano. Email text is sent to Claude when it reads a message, and it is treated as untrusted data.

**Calendar** reading uses the calendar's *Secret address in iCal format* (Google Calendar → Settings → your calendar → Integrate calendar), stored under `Ano/GoogleCalendar` and refreshed at most every five minutes. Anyone with that address can see the calendar. New events open in Google Calendar in your browser and are saved only when you click Save.

**Reminders** only fire while Ano is running (it can stay in the tray). Reminders missed while Ano was closed appear at the next start, marked with their original time. Notes and reminders are stored in the same local SQLite file as messages.

## Windows and browser tabs

### Filling long forms

For a task like “Fill this ISP form from these notes,” Ano can now use `inspect_browser_form`, `fill_browser_form`, and `verify_browser_form`. Inspection returns editable fields with existing values, row descriptions, dropdown option labels/values, and limits. One batch can fill up to 100 observed text areas/inputs, select dropdown values, and set checkboxes/radios. The entire batch is prechecked before writing, then applied in groups of eight with cancellation checks between groups. Actual field values are compared with the requested values; a visible success phrase is not used as proof of data entry. These tools never submit the form. Consequential records remain drafts unless submission was explicitly requested separately.

Field references are bound to the observed document and URL. Existing user edits, changed options, stale observations, partial writes, and field validation errors are reported instead of silently treated as success. Form metadata and receipts are held in memory rather than saved in the browser-task ledger. Password/OTP/CAPTCHA pages use the existing vault or hand back to the user. The model is instructed to transcribe supplied facts and scores and ask about missing/conflicting dates, times or other facts.

The newest user message and the most recent long source message are preserved whole up to 64,000 characters each, including through follow-up date corrections. Other recent history retains a 24,000-character budget. Oversized new messages are rejected with a split-request instruction instead of silently losing the beginning of a table.

`browser_connection_status` diagnoses missing extension setup, a busy local port, and connected browsers. Ano needs its extension in Chrome, Edge or Brave; it does not inherit Codex’s in-app browser session or sign-in. Extension version 1.3 adds form support: run `.\.venv\Scripts\python.exe -m ano.configure browser`, reload Ano on the browser’s Extensions page, then restart one copy of Ano. Existing passwords or healthcare notes from this chat are not embedded in the app or its tests.

If the local port is temporarily occupied, Ano retries every two seconds and reconnects once it becomes available. Browser health must be checked afresh rather than inferred from earlier chat errors. An empty saved-login vault does not prevent work in a browser tab that is already signed in; Ano checks existing tabs before asking for credentials.

Validation uses a disposable 64-field ISP-shaped fixture with synthetic notes (`node tests/check_extension_forms.cjs`) and Python tests for reviews, document binding, expiry, cancellation, uncertain dispatch, long-message preservation and duplicate startup. No live clinical record is used for software testing.

Ano can run a **browser workflow** with a short plan and a local task ID. Choose a mode under **⋯ → Computer control** and use the updated extension. For example: “Use my open catalog tab to search for this song, and verify that its result appears.”

Outside Free roam, the workflow tools plan up to eight steps. Free roam removes that plan limit and the 24-action attempt cutoff. The tools inspect visible controls, perform one click or text fill, and read the page again to verify the planned success text. Approve each step mode shows the goal, plan, website, target and exact typed text for approval; Full permission performs the action automatically. Observations are single-use and expire; changed targets or documents are refused. Cancel, Esc, Pause, private login, and Windows lock stop further actions. A request already sent to the browser cannot be recalled.

`start_browser_task`, `inspect_browser_task`, `browser_task_action`, `verify_browser_task`, `finish_browser_task`, and `browser_task_status` provide the execution loop. Plans, verified text excerpts and outcomes live in the `browser_tasks` table of local `memory.sqlite3`; typed field values and full page bodies are not logged there. Interrupted tasks become blocked on restart, with no automatic action replay. Ask Ano for a task's status using its ID. Only steps with newly appearing, planned page text count as verified. The model must still choose criteria that represent your actual request.

This first version supports main-page web controls and ordinary text fields. Password, OTP and CAPTCHA pages hand back to you or the existing approved vault flow. It does not yet automate account signup, fetch verification codes for signup, verify downloaded files, judge generated music, or save reusable workflows. Completion verifies the planned visible text only. A finished song still requires a working music service and verification of its actual audio result.

After updating, run `.\.venv\Scripts\python.exe -m ano.configure browser`, reload Ano on the browser's Extensions page, and restart the desktop app. Disposable checks: `python -m unittest tests.test_browser_tasks tests.test_browser` and `node tests/check_extension_workflow.cjs`. These test a simulated search workflow; they do not sign into a real website or spend credits.

**Windows** (`plugins/windows.py`, no setup): Claude can list open app windows (as Alt+Tab shows them) and focus, minimise, maximise, restore, or close one. Closing sends the normal close request, so apps still ask to save unsaved work. Ano cannot click or type inside other apps.

**Browser tabs** (`plugins/browser.py` plus the extension in `ano/extension/`) work in Brave, Chrome, and Edge:

1. Run `python -m ano.configure browser`. It creates a private token (stored under `Ano/BrowserExtension` in Credential Manager) and copies the extension to `%LOCALAPPDATA%\Ano\Ano\browser-extension`.
2. In `brave://extensions` or `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and choose that folder. Do this once per browser.
3. Restart Ano.

Claude can then list tabs (title, site, active, playing audio, pinned, and when each was last used), close tabs (reopen with Ctrl+Shift+T), switch to, mute, or read a tab's page text. These actions run without a confirmation step, and each reply lists what was done.

The extension connects only to `ws://127.0.0.1:47613` on this PC. Ano accepts only connections with a `chrome-extension://` origin, which web pages cannot fake, that also present the token. Listing tabs sends their titles and sites to Claude, and reading a tab sends its text; page text is treated as untrusted. To remove access, remove the extension from the browser.

## PC troubleshooting

Ask things like “why is my computer slow?”, “why can't I connect to Wi-Fi?”, or “why won't this install?”. Claude investigates, explains the cause in plain language, and offers a fix (`ano/plugins/system.py`):

- **system_overview** (runs freely) reports CPU, memory, the busiest programs, disk space, uptime, battery, Windows version, and whether a restart is pending. It uses `psutil`.
- **inspect_system** (runs freely) runs a Windows PowerShell command for deeper checks: startup apps, services, event-log errors, network, drivers, updates, Defender, disk health. Before running, PowerShell's own parser checks that the command is **read-only**:
  - every command must be on an allowlist of `Get-*` and formatting cmdlets;
  - aliases, `&` / `.` invocation, file redirection, method calls (except simple string and number ones), and casts to types that could create files are refused.
  
  It runs without administrator rights, with a 45-second limit.
- **propose_fix** (needs your click) opens a **Fix this?** window with Claude's explanation and the exact command. It runs only when you click **Run fix**; fixes marked as needing administrator rights also show the Windows UAC prompt. The output is then sent back to Claude, which checks whether the fix worked. Cancel, Escape, Pause, or Windows lock discards a pending fix. Fixes run for at most 30 minutes.

Claude is told to prefer the smallest safe fix, say how to undo it, and never delete personal files or disable security features, but review each command before running it. Commands sent to Claude and their output (process names, error logs, and similar system details) are part of the conversation sent to Anthropic.

## OpenAI features

Run `python -m ano.configure openai` to add an OpenAI key; it is stored under `Ano/OpenAI` in Credential Manager. With a key, Ano adds:

- **Voice recognition** (`openai_audio.CloudRecognizer`, `gpt-4o-transcribe`). The recording stays in memory and is uploaded once, when you release the mic button. A cancelled recording, or one under 0.3 seconds, is never uploaded. Without a key, the offline Vosk recogniser is used.
- **Natural spoken replies** (`openai_audio.OpenAIVoice`, `gpt-4o-mini-tts`). Playback is streamed, so it starts quickly. Choose a voice under **⋯ → Voice**. Turn it on with **⋯ → Speak replies**. Reply text is sent to OpenAI to be spoken.
- **Image generation** (`plugins/images.py`, `gpt-image-2`). Ask for a picture; it is saved in `Pictures\Ano` and opened.
- **A second brain** (`gpt.py`, `gpt-5.5`), with the same tools and instructions as Claude. **⋯ → Brain** chooses **Auto** (Claude answers, and GPT takes over if Claude fails, saying so in the reply), **Claude Opus 5.5**, or **GPT-5.5**. GPT does not have the web search tools. If Claude failed partway through a task, GPT may repeat actions Claude already took.

OpenAI usage is billed to your OpenAI account.

## Web research

Claude has Anthropic's server-side `web_search` and `web_fetch` tools, at most five searches and five page reads per request. Claude decides when to research; simple questions are answered directly. Searches run on Anthropic's servers, not on this PC, and the search terms are built from your conversation. Replies end with the cited sources and a line per search or page read. Web searches are billed per search on top of token usage. Web page text is treated as untrusted data, and pages open in your browser only when you ask.

## Files and websites

Ask in chat or by voice, for example “open YouTube”, “find my CV”, or “summarise the budget PDF in Downloads”. Claude chooses a tool and Ano runs it locally:

- **find_files / list_folder** search names in the allowed folders (up to 25 matches, at most four seconds of searching).
- **read_file** reads plain text and code files, Word `.docx`, and PDF text (via QtPdf), up to 30,000 characters, and sends that text to Claude. Scanned PDFs have no text to read.
- **open_file** opens a document, picture, audio, video, or folder in its usual Windows app. Programs, scripts, and shortcuts are refused.
- **open_website** opens `http`/`https` addresses in the default browser.

Actions run without a confirmation step, and each reply lists what was done (for example “· Opened https://www.youtube.com”). Paths are resolved, following links and `..`, and must stay inside an allowed folder. **Files** chooses the folders; the defaults are Documents, Desktop, and Downloads. Pause or Windows lock blocks every tool. File contents are treated as untrusted data, but a document crafted to mislead Claude could still lead it to open a website; keep sensitive folders off the list.

Without a key, Ano stays in local preview mode; screen previews can still be captured and cancelled, but cannot be sent. Local activity tracking, offline voice dictation, and a basic local recall summary work without Claude. Pause blocks capture and new sends, clears an unapproved screenshot, stops activity tracking, cancels microphone input and speech output, and discards a pending reply; it cannot recall a request already sent to Anthropic.

## Push-to-talk and spoken replies

Install the optional voice dependencies and small English model once:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[voice]'
.\.venv\Scripts\python.exe -m ano.voice_setup
```

Restart Ano. Hold the **mic** button, wait for it to turn red and the box to say **Listening…**, say your request, then release. The first press loads the model and can take a moment. The recognized text is added after anything already typed and sent immediately, so spoken commands such as “open YouTube” run hands-free. It does not take a screenshot. For a spoken screen question, dictate the question first and then choose **Explain my screen** to review an image.

Vosk performs recognition locally using the default Windows input device. No raw audio is written to files, SQLite, or a cloud service. The microphone is opened only during a held-button request, for at most 60 seconds. Pause, lock, loss of window focus, hiding/closing the panel, or cancellation discards the recording and any late transcript. Releasing while the model is still loading prevents the microphone from opening. A Windows microphone permission or device problem is shown in the voice status line.

The installer downloads [Vosk's small US English model](https://alphacephei.com/vosk/models) over HTTPS into the app data directory under `speech/`. `ANO_VOSK_MODEL` can select another model directory. The model download is about 40 MB; recognition accuracy depends on accent, noise, and microphone quality. The app does not listen for a wake phrase yet.

**Speak replies** is off by default and its setting is saved locally. When enabled, new assistant replies use Windows' default SAPI voice and audio output, up to 4,000 characters per reply. The **stop** button in the top bar interrupts speech. Pause, Windows lock, turning the option off, or beginning push-to-talk also stops it. If Windows speech output fails, the answer remains available in chat. Reply text is treated as plain text, never as speech markup or a filename.

To check this on your Surface: switch between everyday apps and compare the Activity timeline with **What was I working on earlier?**, then dictate that question using the **mic** button. Enable **Speak replies** to test your speakers, and verify Pause stops both microphone input and playback. This manual check is separate from automated tests.

## Background awareness

Open **⋯ → Activity** to view the timeline. The **On/Off** switch controls tracking independently from Pause and is remembered across restarts. Tracking starts enabled for this requested feature; it runs while Ano is in the tray, only while the application is running.

- Checks foreground app process name and window title every two seconds. It ignores Ano's own windows and untitled windows. It does not read document bodies, browser page contents, keystrokes, or clipboard data, and does not take background screenshots.
- Consecutive observations of the same app/title form one interval. End times are checkpointed every ten seconds and when the interval ends. Times are estimates, not proof of attention or work completed. Polls can miss very quick switches.
- Pause, tracking Off, Windows lock/logoff/disconnect, or an inaccessible input desktop stops recording. Native session notifications handle lock/unlock, and the desktop is checked before and after reading metadata. Unknown desktop state blocks tracking. Gaps over ten seconds, such as sleep, are excluded from the old interval.
- Search the latest matching 100 entries across the saved history. Dates display in Windows' local timezone; stored timestamps are UTC.
- **Delete** removes an entry. **Delete all** asks for confirmation and removes the activity timeline. A deleted active item is suppressed until a different app/title is observed. Deletion does not remove previous chat summaries or data already sent to Claude.

Ask **“What was I working on earlier?”** in chat, or click **What was I doing?** in the timeline. Related recall questions about today and yesterday are supported. Ano attaches up to 40 recent intervals (last 24 hours by default, or the requested local day), with titles limited to 240 characters, to that Claude request. Raw activity entries are not attached to ordinary chat requests. Previous recall answers remain in chat history and can be included in later chat context. Without a key, Ano returns a short local list instead. App names and titles alone cannot establish what happened inside an app.

Activity is stored in the same local SQLite database as messages, with no separate server or background Windows service. There is no automatic retention limit yet; use the delete controls to manage history.

## Explain your screen

1. Optionally type a question in the message box, then choose **⋯ → Explain my screen**.
2. Ano hides for 300 ms so Windows can remove its panel, then captures the monitor containing the foreground app. If that monitor cannot be determined, it uses Ano's monitor. Other monitors are excluded.
3. Review the image and edit your question. The default is “Ano, what am I looking at?”
4. Choose **Send to Claude** to share this image plus recent chat, or **Cancel** / Escape to discard it. Nothing is sent before approval.
5. Claude's answer appears in chat. A text marker records that you approved a snapshot.

Screenshots stay in memory and are not saved to files or SQLite. The preview shows the same JPEG that will be sent, resized to fit within 2000 × 2000 pixels. Images are attached only to the approved request, not later conversation requests. Cancel, pause, Windows lock, or closing the panel clears the pending image. If capture fails, Ano restores the panel and shows an error. Protected windows may appear blank and require another source of context.

Capture and approval are both blocked while paused. Pausing during the hide delay cancels capture; pausing after approval cannot retract the API request. This is a one-shot screenshot feature, not continuous screen monitoring or UI Automation.

## Connect or replace the Claude key

```powershell
.\.venv\Scripts\python.exe -m ano.configure
```

The hidden prompt stores the key in Windows Credential Manager under `Ano/Anthropic`. Restart Ano afterward. Keys are never written to source files or the conversation database. `ANTHROPIC_API_KEY`, when set, takes precedence over Credential Manager.

Messages live under Qt's per-user local application data directory (`Ano/Ano/memory.sqlite3`). Set `ANO_DATA_DIR` to override that location. SQLite data is ordinary, unencrypted local storage. Only the most recent 100 messages are shown by default; search covers the full history.

## Next milestones

1. UI Automation for richer accessible screen context.
2. Task summaries and richer memory management.
3. Opt-in “Hey Ano” activation after push-to-talk is validated on the device.
4. Browser extension for reading and acting inside web pages.

No web server, hosted database, or Sites project is required. A Windows executable and `pyside6-deploy` configuration are future packaging work.

## Run without paid AI credits

Local inference uses your laptop instead of a metered model API. It still uses RAM,
electricity, and download bandwidth. It does not guarantee the quality or speed of
a larger hosted model. Free hosted API tiers have quotas; they are not unlimited.

Install [Ollama for Windows](https://ollama.com/download/windows), then start with
the approximately 2.5 GB [Qwen3 4B text/tool model](https://ollama.com/library/qwen3:4b):

```powershell
ollama pull qwen3:4b
.\.venv\Scripts\python.exe -m ano.local_runtime
.\.venv\Scripts\python.exe -m ano.configure local
```

Restart Ano after configuration. The diagnostic checks the local server and model
capabilities without generating an answer. Configuration requires an installed
tool-capable model. Local-only mode restricts the model endpoint to loopback and
disables the built-in paid Claude/OpenAI chat, image, and voice providers. Existing
credentials remain stored. Environment variables override saved settings; remove
conflicting `ANO_LOCAL_*` overrides before configuring. Local replies default to
a 2,048-token output limit, configurable through `ANO_LOCAL_OUTPUT_TOKENS`.

Qwen3 4B is text-only: it cannot inspect screenshots. Native desktop tools remain
available, but screenshot reasoning needs a compatible vision-capable model, and
local models must be tested for reliable tool use. On a 16 GB Surface Laptop 2
with integrated graphics, CPU inference may be slow. Public web research requires
internet access even with a local model. Other cloud-only features do not gain
offline equivalents through this setup.

The status line identifies local-only mode. Remote compatible endpoints are
labeled Remote, rather than advertised as free. Economy mode can still use paid
models, but will not retry through a paid model after local tool execution.
Local-only mode blocks that built-in paid fallback entirely. This setting does
not make third-party services or plugins free.

To intentionally restore paid providers, run `python -m ano.configure cloud`
with the project virtual environment and restart. Charges can then apply.

## Web-connected computer brain

Ano's compatible-model brain (`ano/brain.py`) shares one tool loop across web
research, the connected browser, local files, and native Windows control. It is
used for GPT and local/compatible models. Claude also receives the public web
tools through the shared toolbox. Restart Ano to load these changes.

- `search_public_web` returns up to five public source links and snippets using
  Bing's RSS results. It needs no search API key, but availability is not
  guaranteed; blocked searches return an error and suggest browser tools.
- `read_public_page` extracts text from public HTTPS pages, with pagination and
  a source URL. It does not run JavaScript, share login cookies, or submit forms.
  Signed-in and dynamic websites use the existing browser connection instead.
- Computer control uses the existing observe, input, and verification tools.
  Enable it in Ano's Computer control menu. Existing review modes, Pause, lock,
  saved-login review, and stop controls remain enforced.

The public reader rejects private/local addresses, pins connections to a checked
public IP, validates TLS certificates, rechecks redirects, and limits response
sizes. Page text and search results are labeled untrusted. The brain is instructed
to read sources before citing them and verify actions before claiming completion;
these instructions cannot guarantee a model's behavior.

Try: “Search for the official Python documentation, read its installation page,
and tell me the steps with source links.” Then enable Computer control and try:
“Open Calculator, calculate 24 times 17, and verify the displayed answer.”

The code provides orchestration and tools, not newly trained model weights. A
configured tool-capable model is still required. Screenshot reasoning additionally
requires vision support. Local inference and public web access do not require paid
model credits; selecting a paid provider still incurs that provider's charges.

## Taught local actions and routines

Open the menu and choose **Local commands (no AI credits)**, or say **show commands**.
The local router recognizes 65 named commands, plus parameterized searches, notes,
reminders, calculations, file reads, and window actions. Commands run in Ano's worker
thread without calling an LLM. Unmatched requests go to the configured model; with
local-only mode enabled that model runs on this PC. Screenshots go to the model,
rather than triggering a command from the text of a screen question.

There are 21 starter recipes: study, course notes, research, coding, writing,
spreadsheets, presentations, email, quick notes, math practice, video learning,
budget worksheet, study resources, project workspace, laptop check, connection
check, focus timer, calendar review, capture workspace, document workspace, and
daily review. Inspect one with **show routine study**, then run it with
**run routine study**. Installing recipes does not execute them.

Teach a new sequence explicitly in chat:

```text
teach routine my class = open canvas -> open word -> remind me in 45 minutes to take a break
show routine my class
run routine my class
```

Teaching saves supported commands; it does not execute the sequence, record
background input, train model weights, or accept arbitrary shell scripts. Recipes
are stored in `routines.sqlite3` in Ano's local data directory. Each run reparses
the stored commands, so relative reminders use the run time. Existing names cannot
be overwritten; choose a new name to preserve an existing routine. Nested recipes
are not supported. Up to eight actions can be taught per recipe.

Commands use whole-message matching. For example, **open calculator** runs locally,
but **do not open calculator** or **open calculator and explain algebra** is handed
to the model. Routine failures stop subsequent steps and never fall back to a model.
Pause, Windows lock, private-login state and the existing tool checks still apply.
Open-app and open-website results report dispatch, not proof that an app loaded or
a website task was completed. These recipes do not automatically submit coursework,
send messages, fill passwords, or bypass the existing computer-control modes.

Canvas opens the verified Strayer Canvas entry point, which may ask you to sign in.
Folders remain limited by Ano's existing allowed-folder settings. Installed app
names and browser-extension availability still determine which recipes can run.
Play/pause and mute commands toggle their state; they do not inspect playback.
Reminders require Ano to stay running for notifications, or are shown on next start.

This Surface is configured to use the downloaded Qwen3-VL 2B Instruct model through
the `ano-local:latest` Ollama profile, with a 16,384-token context. A synthetic image
read took about 72 seconds during setup; that is a single check, not an everyday
performance guarantee. The local commands avoid that model latency.

See [the local command reference](LOCAL_COMMANDS.md) for exact examples.

## Read, write, and research

These commands are available in chat and can be saved as routine steps:

```text
write document Class notes: Today I studied variables and loops.
read document Class notes
append document Class notes: Review functions tomorrow.
replace in document Class notes: Review functions => Practice functions
summarize document Class notes
list documents
research effective study habits
research and summarize effective study habits
draft document Study plan about a simple weekly study schedule
```

New `.md` or `.txt` files are saved under **Documents/AnoWorkspace**. New-file
creation never overwrites an existing document. Append and unique exact-text
replacement save the previous bytes under `.history/`, write the change, and
read it back. No arbitrary filesystem writes or executable files are supported.
The workspace must remain inside Ano's allowed folders. Exact writing preserves
paragraphs and spacing inside the supplied text.

`read file FULL_PATH` retains existing support for text/code, Word `.docx`, and
text-based PDFs in allowed folders. `summarize file FULL_PATH` extracts key
passages from the first reader window (up to 8,000 characters); `summarize document
NAME` extracts passages from the workspace document. These are labeled extracts,
not an AI-written summary. Scanned documents may have no readable text.

`research TOPIC` searches the public web, attempts to read at most five candidates,
and saves evidence from up to three distinct hosts. Each successful source has its
actual final URL and an excerpt capped at 24 words. Unreadable sources are listed.
The report explicitly requires review of relevance, authority, dates and conflicts;
it does not claim independent fact-checking or generated synthesis. Search and
page failures never turn into invented references or a paid model fallback.

`research and summarize TOPIC` adds a short local-model synthesis to the fetched
evidence. The synthesis is labeled AI-generated and uses numbered references to
the sources actually read. Missing, nonexistent or URL-inventing references are
rejected. This checks reference structure, not whether every generated claim is
supported; facts and interpretations still need human review.

`draft document NAME about TOPIC` makes an original prose draft using only the
configured loopback local model. It saves the result with an AI-draft label and
reports that an LLM was used. It does not claim to browse or invent citations.
Topic length is limited to 2,000 characters; review generated facts and wording.
The exact read/write commands and evidence research do not invoke an LLM. If the
main chat uses a remote provider, the installed `ano-local:latest` Ollama profile
is still used for these local drafting commands; their text is not routed to that
remote chat provider.

## Website tasks, sign-in, and continuing to a result

Extension **1.4** adds task-tab creation and direct navigation. Update the installed
extension files with `python -m ano.configure browser`, then reload Ano once in
Chrome/Edge/Brave's Extensions page. Restart Ano after application changes. The
connection banner reports a mismatched extension version. This does not install
a second extension or change the saved connection token.

Give a concrete website goal, for example: “Open my Canvas course, inspect Week 3,
and report each assignment's submission status with evidence. Do not submit work.”
The agent should open or select a connected tab, plan observable success criteria,
inspect page elements, perform an action, and verify the result. The task tracker
retains verified progress; a model reply alone cannot mark its task complete.
Both model clients request continuation when a tracked task is still running,
with two bounded nudges if the model tries to end early. Normal tool/action limits
and the user's session mode still apply; this is not an infinite retry loop.

Saved website credentials stay in Windows Credential Manager. The existing
`request_saved_login` action fills only. The separate `sign_in_saved_website`
action displays a **Sign in once** review before loading the password and can
submit a same-origin HTTPS POST form with exactly one password and one submit
control. Credentials never go to the model. GET forms, ambiguous submit controls,
password-change forms, CAPTCHA/OTP pages, and unsupported SSO forms are handed to
the user. Submission does not prove authentication. An uncertain sign-in is never
automatically retried.

Private mode remains active after a login. Finish any MFA/identity step yourself.
If a resumable task exists, the **Continue task · full control** button explicitly
clears private mode, enables full control for this session, and asks the agent to
resume saved progress and inspect the page. **Done with login** remains available
without enabling control. You can also enable your preferred control mode and ask
Ano to continue. `show website tasks` lists saved IDs and progress.

`resume_browser_task` discards stale observations and preserves verified steps.
It cannot resume completed/denied tasks or uncertain in-flight actions. The agent
must inspect the current page before acting. Verification still depends on the
planned visible success text; downloads, account state, and external results need
appropriate independent evidence. Screenshots and page content cannot authorize
unrelated actions. Some sites and models will still require human help.

## Reliable native text entry

`desktop_observe` now includes accessible `editable_elements` when an app exposes
them, including editable combo boxes such as Spotify search. Each ID is bound to
that observation and an internal UI Automation runtime ID. No existing field
values or password fields are included in this list.

For a search box or editor, use `desktop_input` with `action: set_text`, the
observed `element_id`, and the requested text. This sets the specific field and
reads its exact value back. An already-matching value is verified without entering
it twice. Stale, password, read-only and foreign-window fields are rejected. The
existing review/session mode and stop checks apply.

Ordinary `type` now requires a confirmed readable editable field in normal modes.
Its receipt separates text dispatch from `text_verified`. Ignored keystrokes are
not described as successful text entry. Explicit Free roam retains raw input for
terminals/unsupported fields, but cannot claim verified text when no readback is
available. A failed follow-up screenshot preserves any text-verification receipt.

These checks verify field entry, not an entire task. A Spotify playlist still needs
the correct playlist name, intended artist/track versions, each track addition,
and final contents checked. Models can still misinterpret a screen or choose the
wrong action. App interfaces, unavailable accessibility patterns, stopped sessions
and external failures can require a human handoff; no universal completion
guarantee is possible.

## Verify native results instead of replaying clicks

`desktop_read(window_id, query="")` reads accessible labels and roles from the
selected app, with ancestor labels to distinguish a playlist table from search
suggestions or recommendations. It does not click, type, read textbox values,
or create new action IDs. An optional query filters label text. Password fields
are omitted; output is capped and explicitly marked partial when truncated.
Missing content in a partial or virtualized tree is not proof of absence.

`desktop_observe` also supplies supported `actionable_elements`. For buttons and
menu items, `desktop_input` action `invoke` targets a fresh observed control by
runtime ID, label and role instead of guessing pixel coordinates. Changed,
offscreen or disabled controls are rejected. An invocation is still only dispatch:
the resulting state needs a fresh read or screenshot. Uncertain invocations are
not automatically retried. Read-only verification remains available after the
normal input cap, without permitting more input.

For example, after an uncertain Add to playlist click, inspect the existing
destination playlist's own title and track rows before considering another add.
A highlighted menu option proves neither success nor failure. Ano is instructed
to make this read-only check itself when the tools permit it, rather than asking
the user to inspect a result that it can read. These tools do not guarantee that
all apps expose useful accessibility information or that a model interprets it
correctly. Existing control modes, pause/lock checks and reviews remain active.

## Free local image generation

Ano can generate images on this PC with **SDXS-512-DreamShaper** and the Windows
CPU build of stable-diffusion.cpp. This uses existing open model weights, not a
new model trained for Ano. No API key or paid image service is needed. Prompts
remain local during generation, and the generated PNG is saved in Pictures/Ano.
Local-only mode keeps this feature available while paid providers remain off.

Install the pinned runtime and weights once (about 900 MB, internet required):

```powershell
.\.venv\Scripts\python.exe -m ano.local_images install
.\.venv\Scripts\python.exe -m ano.local_images status
```

Downloads are verified against SHA-256 checksums before use. Sources, revisions,
and the declared model license are recorded in SOURCES.json under
`%LOCALAPPDATA%/Ano/Ano/local-images`. Model terms still apply; see the
[SDXS model card](https://huggingface.co/IDKiro/sdxs-512-dreamshaper), the
[converted weights](https://huggingface.co/akleine/sdxs-512), and the
[CPU runtime](https://github.com/leejet/stable-diffusion.cpp).

Restart Ano after installation, then enter one command per message:

```text
generate image: a red fox sitting in a sunlit forest, storybook illustration
generate portrait image: a lighthouse above stormy waves
generate landscape image: a cozy cabin beside a lake at sunset
```

These commands bypass the chat LLM. Natural-language image requests can also use
the `generate_image` tool, including from the compact local profile. Only the local
image provider is registered; generation errors do not fall back to paid services.
With no image files installed, the tool reports the setup command instead of
claiming an image was generated.

This small one-step model favors speed over fine detail, readable lettering, and
complex prompt accuracy. Square images are 512x512; portrait images are 384x512;
landscape images are 512x384. CPU generation can take minutes. Ano uses two CPU
threads, stops the worker on Pause/lock/private login, and times out after ten
minutes. Its temporary runtime logs are removed after each job. The generated PNG
may contain the runtime's prompt/seed metadata; review it before sharing.

## Model reliability checks

Local and compatible-model task loops keep the newest screenshot and three recent
tool results in full. Older images become stale-view notices, and older results
retain bounded excerpts with their original call IDs. User instructions and source
messages are not shortened by this cleanup. It reduces growing context during a
task; it does not guarantee that every request fits a model's context window.

Both provider loops retain receipts when shortening old results and tell the model
to inspect current state rather than replay an earlier action. A timeout or server
error after tool execution warns that actions may already have taken effect.
Empty compatible-model responses and requests for unavailable tools are reported
explicitly instead of falling through to a generic failure or success claim.
The compact local profile preserves nested schema constraints while shortening
descriptions, and rejects tool calls outside its advertised tool list before
dispatch. Permission checks still apply to every available tool.

Run a synthetic tool-use check against the installed local model:

```powershell
.\.venv\Scripts\python.exe -m tools.check_local_brain
.\.venv\Scripts\python.exe -m tools.check_local_brain --profile compact
```

The check exposes only a diagnostic tool, generates a fresh receipt code, and
requires the model to call it once and report its actual result. It uses the
loopback Ollama server with no personal files, desktop actions, browser sessions,
or paid provider. This is a basic tool-use check, not a benchmark of all assistant
skills or evidence of model-weight training. Restart Ano to load application-code
changes.

## Verify

### Persistent commands and local artifacts

With the task ledger enabled, typed commands wait in a local queue while Ano is
busy. Use Menu → Tasks to inspect/cancel them, or Menu → Stop current task.
Each reply stays linked to its originating command and chat. Model workers run
one at a time on this laptop. `answered` means a reply was saved, not that the
requested external outcome was independently verified. Interrupted work is
paused at startup; it is never automatically replayed. Automatic recovery and
general parallel tool execution are still unfinished.

Exact supplied-data commands bypass the language model:

```text
create pdf Summary: Exact body text to place in the PDF.
create spreadsheet Sales: {"headers":["Month","Units"],"rows":[["Jan",20],["Feb",35]],"chart_column":2}
```

These create new files in AnoWorkspace and never overwrite existing files. PDF
text and spreadsheet values are read back. Spreadsheet strings are literal unless
cells are explicitly listed in the `formulas` object. Supported arithmetic and
SUM/AVERAGE/MIN/MAX/COUNT/ABS/ROUND formulas are calculated locally and their numeric
XLSX caches are checked, so initial results display without a separate recalculation.
References must remain within supplied data rows on the single sheet. External
references, cycles and unsupported syntax are refused before writing. General
Excel functions, imported workbook recalculation, arbitrary spreadsheet editing,
and PowerPoint editing remain unfinished. Editable supplied-content PowerPoint creation is
available with `create presentation NAME: {"title":"TITLE","slides":[{"title":"SLIDE TITLE","bullets":["POINT"]}]}`.
It uses a bundled template with a cover plus alternating content layouts, accepts
1–20 content slides with 1–4 short points each, and reads all texts back before
saving. General layouts, charts, imported templates and deck revisions still need
work. A read-back receipt does not certify visual layout.

Example with explicit calculated cells:

```text
create spreadsheet Totals: {"headers":["Item","Amount"],"rows":[["A",10],["B",20],["Total",null]],"formulas":{"B4":"=SUM(B2:B3)"},"chart_column":2}
```
The local tools require the allowed Documents folder and stop on Pause/lock.

Advanced local roles use installed model names in `local_agent_model`,
`local_chat_model`, `local_writer_model`, and `local_vision_model`. Invalid or
missing overrides fall back with a startup warning. Selecting a model is not
training, and downloading a model does not select it or establish its quality.

Explicit local memory commands work without a model:

```text
remember fact preference response length: Brief answers, please.
update fact preference response length: Detailed answers for technical topics.
recall facts response length
show remembered facts
forget fact preference response length
```

Facts retain their source command and originating chat/message identifiers when
provided by the task queue. Changes keep revision history; forgetting hides a
fact from retrieval without deleting its audit history. Models can search facts
but cannot write them. Relevant facts and explicitly named `preference ...` facts
may enter a downloaded local model's context. They never enter the paid or remote
model context automatically and do not authorize resuming old actions. Retrieval
currently uses bounded word matching, not semantic search.

Menu → Start local voice conversation enables an in-memory voice session without
changing the saved spoken-replies preference. Speak and pause to send a turn;
the mic closes during inference and playback, then reopens for the next turn.
The interrupt control can stop playback or the active task before a new turn.
Pause, lock, private login, hiding Ano, capture failure, and Stop close the session.
This is half duplex: simultaneous talk-over and acoustic echo cancellation remain
unfinished. Automated voice tests simulate capture/playback; real device audio
quality is not established by those tests.

Active jobs now save tool-dispatch receipts before dispatch. If the receipt cannot
be written, dispatch is blocked. A successful return is labeled `returned`, not
verified completion. A failed potentially changing tool is `uncertain`; interrupted
steps require destination inspection, and failures never trigger automatic replay.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tests/smoke_ui.py
```

The automated tests use fake clocks, app metadata, desktop/lock states, synthetic images, and simulated microphone/speech backends. They verify activity intervals, pause/lock/sleep gaps, deletion, recall, conditional Claude context, microphone lifetime, cancellation, send-on-release dictation, file tools confined to allowed folders, the Claude tool loop, speech opt-in, and QML controls without API calls, desktop screenshots, real microphone recording, or speaker output. Native Windows session registration and metadata APIs are checked separately during development. The installed speech model is also validated with Windows-generated speech written to a temporary WAV file and recognized offline; that check does not use the microphone or speakers. Actual device microphone quality and the everyday activity check still need a user test.

Framework references: [Qt for Python](https://doc.qt.io/qtforpython-6/index.html), [Qt system tray](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QSystemTrayIcon.html), [SQLite FTS5](https://www.sqlite.org/fts5.html), [Claude Messages API](https://platform.claude.com/docs/en/build-with-claude/working-with-messages).

### Trading analysis and email updates (29 October 2026)

`run the trading model` / `run all trading models` monitors QQQ and DIA for ICT,
SMC v1, Silver Bullet, AMD / Power of Three, gap fills, a 30-minute opening range,
and technical context (EMA20/50, RSI14, ATR14, VWAP where volume is available).
`trading model status` shows each strategy's latest timestamped observations or
missing/stale-data reason. `stop the trading model` stops research watches;
paper execution retains its separate existing controls. `run smc`, `run silver
bullet`, `run amd`, `run ict`, and `start gap fill model` start individual watches.
`smc qqq` / `smc dia` fetch an immediate SMC snapshot. Monitoring is on weekdays
09:25–15:10 New York time; fresh source bars are required. Settings and latest
snapshots survive app restarts. The suite does not enable paper or live orders.

SMC v1 uses confirmed two-sided swings for BOS/CHOCH and liquidity sweeps.
An order block is the last opposite candle within ten contiguous bars before
a displaced swing break (body at least 1.5 times the preceding twenty-body mean).
Its zone is the candle's wick range. A close through the far edge invalidates it
and confirms an opposite breaker. Retests require a later closed candle; a close
through the breaker's far edge invalidates it. Premium/discount uses the latest
confirmed swing high/low midpoint. Daily/4h/1h directional context compares the
last two completed source-bar closes; missing or outdated input is unavailable,
not a directional vote. These are explicit Ano research definitions, without a
claim of canonical SMC coverage or proven predictive edge. Bar outcomes are
simulations, not broker fills. Higher-timeframe inputs use the documented
[Alpaca historical bars API](https://docs.alpaca.markets/us/reference/stockbars).

Both the Ano sports reply path and local ScorPred scheduled digest now use
`ano-premium-v2`: a mobile-friendly green/navy and gold layout, inline logo,
readable report sections and original evidence/availability labels. The standalone
ScorPred theme copy must remain byte-identical to `ano/email_theme.py`; a regression
test checks this when the sibling installation is available. Preview fixture:
`previews/premium-email.html` (illustrative layout only). Old delivered emails are
not changed; updated styling applies to newly generated reports.

Ano now has a shared reliability window: say `open reliability dashboard`,
`ano health`, or `trading bot readiness`. Readiness verifies the paper account
with GET-only requests and never enables orders. The paper runner separately
requires complete signal bars, verified account exposure, current-week news,
saved bracket-price/quantity evidence and the existing risk rules. An uncertain
closing request is never automatically replayed. Gap-fill keeps its existing
paper rules. The user can explicitly enable the seven additional experimental
paper adapters with `allow them to place trades`; all share one account owner
and the same daily risk limits. Unknown strategies and live trading stay disabled.

The local forecast ledger captures pre-event snapshots from chat, the sports
desk and native sports emails, including SKIP forecasts. It scores the first
eligible observation per event/model only after a matching completed scoreboard
is verified. Refreshes are not independent trials, unsupported results stay
pending, and there is no accuracy/profitability claim before verified outcomes.
The seven-day health log records actual observations while Ano runs, with
failure/recovery notices. See `LOCAL_COMMANDS.md` for scope and commands.

### Native forex revision — 30 October 2026

Forex SMC and ICT now use `fx-native-v2`, defaulting to **shadow mode**. Legacy forex
SMC/ICT entries are retired. On the user's explicit request, the existing shared
ForexRunner can run the revision automatically on OANDA practice only. The dated
session selection and every account/risk gate still apply. A practice opt-in is
bound to the current source fingerprint; source changes disable new native entries
until explicitly re-enabled. Checklist scores and replay results never enable it.
Existing ETF strategy rules
are separate. Observe `forex-native-v2.health.json` and the immutable candle and
decision store `forex-native-v2.sqlite3` in Ano's shared state directory.

The native scanner uses named London, New York and Tokyo civil-time windows,
confirmed pivot breaks on completed H4/H1 candles with daily context, and known
untouched opposing liquidity. SMC rejects a nearby target below 2R or an opposing
block before its target. ICT emits a midpoint **LIMIT/GTD** contract expiring
after 30 minutes, the session cutoff or the next news exclusion boundary, whichever
is earlier. The shared manager verifies pending identity/price/protection/expiry,
cancels on invalidation, risk, news or permission stops, and reconciles uncertain
submissions or cancellations with GET-only requests. It never retries an ambiguous
submission or adopts manual trades/orders.

Market orders use a bounded adverse allowance (up to 0.5 pip and 10% of stop
distance, rounded to the instrument tick), included in worst-fill sizing.
Monetary limits remain 0.25% planned risk per entry, 0.75% daily loss halt, two
consecutive losses, one owned exposure and maximum 3x notional. The NY risk day
allows three fills/reservations; submissions have separate caps of three per
session and nine per risk day, with a 60-second cooldown. An unknown outcome
reserves capacity and is never retried. Only verified cancellations release a
fill reservation; they still consume submission capacity.

GET-only historical capture: `python tools/forex_native_review.py --pair EUR_USD
--session london --output NEW_INPUT.json`. Prefix replay: `python -m
ano.forex_replay NEW_INPUT.json --output REVIEW.json --start UTC_START --cutoff
UTC_HOLDOUT_CUTOFF`. Supply ISO timestamps including `Z` or a UTC offset. Replay
uses bid/ask prices, explicit slippage, stop-first ambiguity, expiring limits,
separate strategy/pair/session results and a chronological holdout. Missing news
blocks simulated entries. Downloaded history and revised calendars are explicitly
retrospective; they cannot prove contemporaneous availability. No profitable edge
or calibrated probability has been established. Automatic practice operation is an
unvalidated experiment, separate from the evidence needed to establish a validated
strategy. Native and legacy versions have separate performance labels.
