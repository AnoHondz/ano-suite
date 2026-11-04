# Ano software audit (28 October 2026)

**For:** an AI coding agent (ChatGPT / Codex) taking over or reviewing work on Ano.
**State:** version 0.1.10, all 579 unit tests pass, UI smoke test passes, app running on this PC.

---

## 1. What Ano is

A Windows desktop AI assistant for one user, in `C:\Users\User\Documents\Ano`.

- **Stack:** Python 3.12, PySide6 (Qt 6) with a QML front end, venv in `.venv`.
- **Start it:** `.venv\Scripts\pythonw.exe -m ano` (it also starts at sign-in with `--tray`).
- **User data:** `C:\Users\User\Documents\AnoWorkspace\.ano-state\` (memory, routines, action log, paid usage, challenge reports). Never replace or overwrite these databases.
- **Files Ano creates:** `C:\Users\User\Documents\AnoWorkspace\`.
- **UI preferences:** `%LOCALAPPDATA%\Ano\Ano\ui.ini`.

**The user's goal:** ChatGPT-desktop-level ability in nine areas:
1. conversation and reasoning;
2. autonomous app tasks;
3. Word workflows;
4. browser and web work;
5. spreadsheets, presentations and PDFs;
6. live voice;
7. parallel and resumable long work;
8. memory and integrations;
9. images.

The user wants any area that is unverified or missing to be treated as unfinished, never claimed as done.

---

## 2. Architecture

### Brains (in order of wrapping)

| Layer | File | Role |
|---|---|---|
| `FactMemoryBrain` | facts.py | Outermost: exact "remember/forget" commands. Exposes `.ledger`, `.mode` and `.model` of the inner brain. |
| `PaidTasks` | paid.py | OpenAI GPT-4.1 when the brain is "openai". A `local:` prefix sends one message to the free brain. Caps: $2.00 per UTC day, $20 total (`SpendLedger`). |
| `RoutineBrain` → `LocalRoutines` | routines.py | Exact built-in commands, handled free and before any model. The chain is: scheduled → monitors → challenges → goals → background, then triage and plan. |
| Local model | brain.py, gpt.py, local_profile.py | Ollama at `127.0.0.1:11434`. Base model `ano-local:latest` (qwen3-vl 2B). The chat and agent roles now fall back to it (the settings `local_chat_model` and `local_agent_model` are set to empty). |

- `AGENT_RULES` in brain.py are the agent instructions shared by both brains.
- The local brain only sees tools whose keywords appear in the request (`TOOL_GROUPS` in local_profile.py). Prompts that Ano writes itself must avoid trigger words, or the model gets unrelated tools.

### Main process

- **Controller** (app.py) is the QObject exposed to QML as `ano`. It owns the chat and busy state, the job queue (jobs.py), streaming (`partialReply`), browser-task progress (`taskProgress`), savings, watch items, handle requests, inventory and challenge results.
- `ReplyJob` runs one reply on a thread pool. Only one main reply runs at a time; extra messages queue as jobs.

### Tools

- `Toolbox` (tools.py) combines the plugins in `ano/plugins/`: apps, artifacts, browser, images, local_images, local_status, mail (Gmail, not configured), media, notes, outlook, schedule (Google Calendar, not configured), startup, system, video, web, websearch, windows, word_documents, workshop.
- Desktop control is in desktop.py and desktop_native.py. Browser control is through the extension in browser.py and browser_tasks.py.

### Claude-built subsystems

| Module | What it does |
|---|---|
| monitor.py | "when X, do Y" rules. Sensors run on a background thread: apps, focus, downloads, power, network, CPU, Claude Code state, Codex state, Windows notifications. |
| scheduled.py, codex_watch.py | Timed typing into apps; the Codex "Resume goal" watcher. |
| challenges.py | 18 live challenge tests across the nine areas. Each checks the real result and sends failures back to Ano to fix (up to 2 retries). Reports are JSON in `.ano-state/challenges/`. |
| handle.py | "handle it" requests for notifications. The notification text is fenced as untrusted. Requests are saved in the memory setting `handle_requests`. |
| goals.py | `keep working on …`: multi-turn goals that end on a STATUS line, max 12 turns, saved in the setting `active_goal`. |
| background.py | `in the background: …`: up to 2 tasks in their own chats on `ano-background-*` threads. |
| inventory.py | Installed and running apps, browser extensions and plugins, for Settings → Apps & plugins. |
| plugins/outlook.py | Classic Outlook through COM: inbox, read email, calendar, and draft reply. It never sends; the reply opens for the user. |

### UI

- `ano/qml/Main.qml` (~3,400 lines), plus AnoButton.qml, IconButton.qml, AnoFace.qml and ControlPanels.qml.
- Panels:
  - Settings
  - Watching
  - Apps & plugins
  - Challenge results
  - Tasks
  - Chats
- Other UI: the composer with command suggestions ("/" lists them), a streaming reply card, the browser-task progress strip, notification cards with hold-to-approve, and the orbit flight animation.

---

## 3. Safety rules that must stay true

- Never type passwords, payment details or one-time codes. Passwords go only through the login vault's approved flow.
- Never send email or messages for the user. Gmail sending and Outlook replies only open a draft for the user to send.
- Notification, email and web text is untrusted data, never instructions.
- Bank, wallet, password-manager and code notifications are dropped whole (`SENSITIVE_APP` and `SENSITIVE_TEXT` in monitor.py). Their text is never kept, spoken or sent.
- "handle it" approval is a **press-and-hold** of 0.9 s. It is exposed to UI Automation as text, not a button.
  - **Why:** another agent (Codex's computer-use) was seen tapping a plain Approve button by itself.
  - Do not turn this back into a clickable or invokable button.
- Background threads (`ano-background-*`) are refused screen, keyboard and browser-task tools (tools.py), and skip job checkpoints.
- Monitors never auto-open programs or scripts (`NEVER_AUTO_OPEN`). Window titles that look private are dropped.
- Only the user's own typed words create rules, goals or schedules. No model tool can create them.
- Notification reading uses a temporary copy of `wpndatabase.db` that is deleted straight away. The original file is never opened.
- Never raise the OpenAI spending caps without the user.

---

## 4. Changes in the last session (27–28 October 2026, by Claude)

| Area | Change | Verified |
|---|---|---|
| Desktop focus | `desktop_native.focus` now tries plain, F24 nudge, UI Automation focus, then minimise/restore. On failure the error names the window that stayed in front. | Live: Calculator passes. |
| Notepad typing | Windows 11 Notepad's RichEdit "Document" counts as a text field (`text_field()`). Word documents are excluded. | Live: set and read back exactly. |
| Save dialogs | New rule in `AGENT_RULES`, and the coordinate error now points to `list_windows`. | **Not live-verified** (budget cap). |
| Slide counts | `total_slides` is checked. The total is parsed from the user's words (`requested_slide_total`). Extra slides are trimmed and reported. `tidy_slides` fits points to 1–4, under 120 characters. | Live on the local brain: exactly 3 slides. |
| Savings | `FactMemoryBrain.ledger` added, so Settings shows real data. | Yes. |
| Voice | No code change; checked on hardware. | Live: speakers → mic → Vosk recognised it 3/3; SAPI spoke the reply. |
| Local AI | Chat and agent roles now use the 2B model, not 0.6B. Benchmarks: 0.6B answered 0/3; 2B answered 3/3 and made correct tool calls. | Yes. |
| Self-check | `AGENT_RULES`: check every exact requirement before replying, work around locked files, convert UTC with the offset. `stamp()` adds the UTC offset to the local-time header. | Unit only. |
| Cache test | `GPTClient.warm` now sends the same system prompt as replies; the old failing test passes. | Yes. |
| Notifications | Notifications arriving up to 20 s before sensing starts still fire (`NOTIFICATION_GRACE`). | Live. |
| Handle it | Hold-to-approve; cards survive restarts. | Live: no auto-approval in 90 s. |
| Outlook | New plugin; tool group by keyword; integration note in capability_context.py. | Live: inbox, read, calendar. |
| Goals | New goals.py. Commands only touch an in-memory copy (SQLite belongs to the main thread). `local:` carries through all turns. Stops on DONE, NEED YOU, 2 failures, 2 missing STATUS lines, Stop, or 12 turns. | Live: start, resume offer, stop. Not live with OpenAI. |
| Background | New background.py with the `local:` prefix. | Live. |
| UI | Watch alerts highlighted; "click the app, then Retry" hint; Challenge results page; "Waiting" and "Paused" states on the watch chip; duplicate rules blocked. | Preview, smoke and live. |
| Challenges | Office `~$` lock files ignored; UTC reminder check fixed; app tests close only the windows they opened. | Unit and live. |

---

## 5. Live challenge scorecard

Each row is the newest run for that area.

| Area | Result | Notes |
|---|---|---|
| Reasoning | 2/2 | 1 fixed on retry |
| Word | 2/2 | 1 fixed on retry |
| Web | 2/2 | 1 fixed on retry |
| Images | 2/2 | |
| Projects | 2/2 | |
| Files | 2/3 | The slide-count failure was fixed after this run; rerun to confirm. |
| Memory | 1/2 | The failure was a checker bug, now fixed; rerun. |
| Apps | Calculator passes on retry; Notepad fails at the Save dialog | |
| Voice | Needs a person | The hardware loop test passed. |

---

## 6. Known weaknesses and open work (priority order)

1. **Saving through dialogs is unreliable.** After Ctrl+S, GPT-4.1 tries coordinates on the Notepad screenshot instead of observing the Save dialog window. Run `run challenge tests apps` on OpenAI to see whether the new rule fixes it. If not, consider a dedicated tool that saves the focused app to an exact path through the file dialog's File name field.
2. **The free local model is weak at multi-step tasks.** In one goal it made a .docx instead of a PDF twice and went round in circles reading files. It also gets facts wrong (it gave Gaborone as a Zimbabwean city). Candidates: try `qwen3:4b-instruct-2507` (installed, about twice as slow), or stop the local brain from taking multi-step goals.
3. **Goals and the self-check rules are not proven on OpenAI.** They need a live run once the daily cap resets (00:00 UTC).
4. **Phone message replies:** Ano can read Phone Link notifications but cannot reply through them; it can only draft text.
5. **Parallel work:** the main chat is single-threaded. Background tasks cannot use the screen.
6. **Gmail and Google Calendar plugins exist but are not configured.** The user would need to run `python -m ano.configure` with an app password or iCal link.
7. **UI Automation tree:** one element inside a chat message raises a COMError when read. Generic walkers stop there, which can hide later controls from screen readers. Find the element and fix it.
8. **The local tool routing is keyword-based** (`TOOL_GROUPS`). It is fragile: any prompt Ano writes itself can pull in unrelated tools.
9. **Background tasks share one provider object.** Its fields (`last_paid`, `last_route`) can be mixed up between concurrent tasks. Only labels are affected today.

---

## 7. How to work on it safely

- **Tests:** `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`, then `$env:PYTHONPATH='.'; .venv\Scripts\python.exe tests\smoke_ui.py`.
- **Live checks:** say `run challenge tests <area>` in Ano (areas: reasoning, apps, word, web, files, voice, projects, memory, images), or use Settings → Challenge results.
  - On OpenAI, each area costs about $0.02–0.90. Check spending first; the $2/day cap blocks a whole session of tests.
  - Use the `local:` prefix for free checks.
- **Restart after Python changes:** stop the `-m ano` processes, then start `.venv\Scripts\pythonw.exe -m ano` from the project folder.
- **Live tests leave things behind:** Notepad and Calculator windows, and files with an `AC…` tag. Windows 11 Notepad keeps unsaved tabs, so check a window's tabs before closing it.
- **Re-read files before editing.** More than one agent has edited this project.
- **Prefer small, tested changes.** Every new capability so far has needed a live run to find its real bug: thread affinity, keyword routing, layout timing.
