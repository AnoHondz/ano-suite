"""Challenge tests: hard, real tasks run through the live app, each checked against its actual outcome.

Type "run challenge tests" (or "run challenge tests word", "… apps", etc.). Ano opens a new chat, sends
each task exactly as a user would, waits for the reply, then checks what really happened: the file and
its contents, the slide count, the saved reminder, the tools that ran. When a check fails, Ano is told
exactly what is wrong and asked to find out why and finish the task (up to two more attempts). A
scorecard is posted at the end and saved as JSON.

Everything happens in a challenge folder or is cleaned up afterwards: no email is sent, nothing is
bought or submitted, reminders and scheduled typing created by a test are removed, and downloaded
programs are never opened. Tests that need a human (live voice) are reported as "needs you".
"""
from datetime import datetime, timedelta
from contextlib import closing
import json
from pathlib import Path
import random
import re
import sqlite3
import threading

from PySide6.QtCore import QObject, QTimer, Slot

AREAS = ('reasoning', 'apps', 'word', 'web', 'files', 'voice', 'projects', 'memory', 'images')
MAX_ATTEMPTS = 3          # the task plus two "find out why and fix it" follow-ups
TEST_TIMEOUT_MS = 10 * 60 * 1000


def _docs(ctx, pattern):
    # "~$Name.pptx" is Office's lock file while the real file is open, not a result.
    return sorted((path for path in ctx['workspace'].glob(pattern) if not path.name.startswith('~$')),
                  key=lambda path: path.stat().st_mtime)


def _docx_text(path):
    from docx import Document
    document = Document(str(path))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append('\t'.join(cell.text for cell in row.cells))
    return '\n'.join(parts)


def _tools_since(ctx, baseline):
    with closing(sqlite3.connect(str(ctx['action_log']))) as db, db:
        return [name for (name,) in db.execute('SELECT tool FROM actions WHERE id>? AND failed=0', (baseline,))]


def _number_in(text, value):
    digits = re.sub(r'(?<=\d)[,\s](?=\d{3}\b)', '', text)
    return value in digits


# ---------------------------------------------------------------- the tests
# Each check returns (ok, reason). reason is phrased for Ano: it is sent back as "Check failed: …".

def check_arrival(ctx, run):
    return ('17:50' in run['last'], 'the answer must be 17:50 (14:35 + 2 h 50 min + 25 min delay), given as just the time')


def check_locker(ctx, run):
    return (_number_in(run['last'], '4400'), 'the answer must be 4400 (the code I told you, 4417, minus 17)')


def check_notepad(ctx, run):
    path = ctx['challenge_dir'] / f'{run["tag"]}.txt'
    if not path.exists():
        return False, f'there is no file at {path}; it must be saved from Notepad itself at exactly that path'
    text = path.read_text(encoding='utf-8', errors='replace')
    return (f'Ano challenge {run["tag"]}' in text, f'{path.name} does not contain the exact text "Ano challenge {run["tag"]}"')


def check_calculator(ctx, run):
    used = _tools_since(ctx, run['baseline'])
    if not _number_in(run['last'], '7006652'):
        return False, 'the result must be 7,006,652 (1234 × 5678), read from the Calculator app'
    return ('desktop_input' in used, 'the Calculator app was not actually used: no desktop input was performed')


def check_lab_report(ctx, run):
    files = _docs(ctx, f'*{run["tag"]}*Lab*.docx')
    if not files:
        return False, f'no Word document named "{run["tag"]} Lab Report" exists in the workspace'
    text = _docx_text(files[-1])
    missing = [value for value in ('23.4', '23.1', '23.6', '23.37') if value not in text]
    return (not missing, f'{files[-1].name} is missing {missing} (the three volumes and their average, 23.37)')


def check_revision(ctx, run):
    files = _docs(ctx, f'*{run["tag"]}*.docx')
    revised = [path for path in files if 'Run' in _docx_text(path) and 'Trial' not in _docx_text(path)]
    originals = [path for path in files if 'Trial' in _docx_text(path)]
    if not revised:
        return False, 'no revised copy of the lab report has "Run" in place of every "Trial"'
    return (bool(originals), 'the original lab report with "Trial" was not preserved')


def check_wikipedia(ctx, run):
    if 'wikipedia.org' not in run['last']:
        return False, 'the reply must include the Wikipedia link you read'
    return (bool(re.search(r'\d[\d,. ]{4,}', run['last'])), 'the reply must give the population as a number')


def check_example_heading(ctx, run):
    used = _tools_since(ctx, run['baseline'])
    if 'Example Domain' not in run['last']:
        return False, 'the exact main heading on https://example.com is not in the reply'
    return (any(name in used for name in ('open_browser_tab', 'read_tab', 'inspect_browser_task')),
            'the page was not opened in the browser (no browser tool ran)')


def check_budget(ctx, run):
    files = _docs(ctx, f'*{run["tag"]}*Budget*.xlsx')
    if not files:
        return False, f'no spreadsheet named "{run["tag"]} Budget" exists'
    from openpyxl import load_workbook
    book = load_workbook(str(files[-1]))
    cells = [str(cell.value) for sheet in book.worksheets for row in sheet.iter_rows() for cell in row if cell.value is not None]
    joined = ' '.join(cells)
    missing = [value for value in ('Rent', '950', 'Food', '320', 'Transport', '85') if value not in joined]
    if missing:
        return False, f'{files[-1].name} is missing {missing}'
    if '1355' not in joined and not re.search(r'=SUM\(', joined, re.I):
        return False, 'there is no Total row adding the costs (1355, or a SUM formula)'
    charts = sum(len(sheet._charts) for sheet in book.worksheets)
    return (charts > 0, 'the spreadsheet has no chart')


def check_pitch(ctx, run):
    files = _docs(ctx, f'*{run["tag"]}*Pitch*.pptx')
    if not files:
        return False, f'no PowerPoint named "{run["tag"]} Pitch" exists'
    from zipfile import ZipFile
    slides = [name for name in ZipFile(files[-1]).namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', name)]
    return (len(slides) == 3, f'{files[-1].name} has {len(slides)} slides; it must have exactly 3 in total, the title slide included')


def check_checklist(ctx, run):
    files = _docs(ctx, f'*{run["tag"]}*Checklist*.pdf')
    if not files:
        return False, f'no PDF named "{run["tag"]} Checklist" exists'
    from pypdf import PdfReader
    text = '\n'.join(page.extract_text() or '' for page in PdfReader(str(files[-1])).pages)
    missing = [f'{n}.' for n in range(1, 6) if not re.search(rf'(?m)^\s*{n}[.)]', text)]
    return (not missing, f'the PDF does not have five numbered steps; missing {missing}')


def check_voice(ctx, run):
    ready = getattr(ctx['speech'], 'available', False) and getattr(ctx['voice'], 'available', False)
    return None, ('Voice is installed: hold the mic button and ask "what time is it", then listen for the reply.'
                  if ready else 'Voice is not set up on this PC (speech or offline recognition is missing).')


def check_scheduled(ctx, run):
    found = [a for a in ctx['scheduled'].pending() if run['tag'] in a['text']]
    if not found:
        return False, f'no scheduled typing action containing "{run["tag"]}" is waiting'
    return (found[0]['app'] == 'Notepad', 'the scheduled typing is not set for Notepad')


def check_monitor(ctx, run):
    rules = [r for r in ctx['monitors'].active() if r['condition'] == 'download_done' and r['id'] > run['monitor_baseline']]
    return (bool(rules), 'no new monitor for finished downloads was created')


def check_parking(ctx, run):
    text = run['last'].lower()
    return ('level 3' in text and 'bay c' in text, 'the answer must recall the saved note: level 3, bay C')


def check_reminder(ctx, run):
    with closing(sqlite3.connect(str(ctx['notes_db']))) as db, db:
        rows = db.execute('SELECT body, due_at FROM reminders WHERE done=0').fetchall()
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    match = [due for body, due in rows if run['tag'].lower() in body.lower()]
    if not match:
        return False, f'no reminder mentioning {run["tag"]} was saved'
    def local(due):  # reminders may be saved in UTC ("…T11:45:00+00:00"); compare in this PC's time
        try:
            when = datetime.fromisoformat(due)
        except ValueError:
            return due
        return (when.astimezone() if when.tzinfo else when).strftime('%Y-%m-%dT%H:%M')
    return (any(local(due).startswith(tomorrow + 'T07:45') for due in match),
            f'the reminder is not set for tomorrow at 07:45 local time (saved: {[local(due) for due in match]})')


def check_generated_image(ctx, run):
    folder = ctx['pictures']()
    fresh = [p for p in folder.glob('*.png') if p.stat().st_mtime >= run['started'].timestamp()] if folder.exists() else []
    return (bool(fresh), f'no new picture was saved in {folder}')


def check_image_code(ctx, run):
    return (run['secret'] in run['last'], f'the reply does not contain the 6-digit code written in the picture')


TESTS = [
    dict(id='reasoning-1', area='reasoning', title='Time arithmetic',
         steps=['A train leaves at 14:35. The trip takes 2 hours 50 minutes and it is delayed by 25 minutes. '
                'What time does it arrive? Answer with just the time in 24-hour format.'], check=check_arrival),
    dict(id='reasoning-2', area='reasoning', title='Remember within a conversation',
         steps=['My locker code for {tag} is 4417. Just remember it for this conversation.',
                'What is my {tag} locker code minus 17? Reply with just the number.'], check=check_locker),
    dict(id='apps-1', area='apps', title='Notepad: type and save through the app',
         steps=['Using the Notepad app itself (computer control), open Notepad, type exactly: Ano challenge {tag} '
                'and save it as {dir}\\{tag}.txt. Then confirm the file is saved.'], check=check_notepad, cleanup='windows'),
    dict(id='apps-2', area='apps', title='Calculator: compute in the app',
         steps=['Open the Calculator app and use it (computer control) to compute 1234 × 5678. '
                'Tell me the result exactly as the app shows it.'], check=check_calculator, cleanup='windows'),
    dict(id='word-1', area='word', title='Word report with a table and an average',
         steps=['Create a Word document called "{tag} Lab Report" with a title, a table with columns Trial and Volume (mL) '
                'holding 1 → 23.4, 2 → 23.1, 3 → 23.6, and a sentence giving the average volume to two decimals.'],
         check=check_lab_report),
    dict(id='word-2', area='word', title='Revise a Word document, keeping the original',
         steps=['In the Word document "{tag} Lab Report", change the word "Trial" to "Run" everywhere. '
                'Keep the original and save the revision as "{tag} Lab Report revised".'], check=check_revision),
    dict(id='web-1', area='web', title='Research with a source',
         steps=['Using the web, find the population of Harare on Wikipedia. Tell me the number and the exact Wikipedia link you read.'],
         check=check_wikipedia),
    dict(id='web-2', area='web', title='Browser: read a live page',
         steps=['Open https://example.com in my browser and tell me the exact main heading on that page.'],
         check=check_example_heading),
    dict(id='files-1', area='files', title='Spreadsheet with a total and a chart',
         steps=['Make a spreadsheet called "{tag} Budget" with columns Item and Cost: Rent 950, Food 320, Transport 85, '
                'then a Total row adding them up, and a column chart of the costs.'], check=check_budget),
    dict(id='files-2', area='files', title='Presentation with an exact slide count',
         steps=['Create a PowerPoint called "{tag} Pitch" about what Ano can do, with exactly 3 slides in total including the title slide.'],
         check=check_pitch),
    dict(id='files-3', area='files', title='PDF with numbered steps',
         steps=['Make a PDF called "{tag} Checklist" with a title and 5 numbered steps for backing up a laptop.'],
         check=check_checklist),
    dict(id='voice-1', area='voice', title='Live voice conversation', steps=[], check=check_voice),
    dict(id='projects-1', area='projects', title='Scheduled follow-up work',
         steps=['in 30 minutes type "{tag} still going" in notepad'], check=check_scheduled, cleanup='scheduled'),
    dict(id='projects-2', area='projects', title='Long-running watch',
         steps=['when my download finishes, tell me'], check=check_monitor, cleanup='monitor'),
    dict(id='memory-1', area='memory', title='Recall a saved note in a new chat',
         steps=['Take a note: {tag} parking spot is level 3, bay C.', '@new', 'Where did I park for {tag}? Check my notes.'],
         check=check_parking),
    dict(id='memory-2', area='memory', title='Natural-language reminder',
         steps=['Remind me tomorrow at 7:45 am to check the {tag} oven.'], check=check_reminder, cleanup='reminder'),
    dict(id='images-1', area='images', title='Generate an image',
         steps=['Generate an image of a red bicycle leaning on a white wall.'], check=check_generated_image),
    dict(id='images-2', area='images', title='Read text in a picture',
         steps=['Look at the picture {image} and tell me the 6-digit code written in it.'], check=check_image_code, setup='image'),
]


class ChallengeRunner(QObject):
    """Drives the tests through the real Controller, one message at a time, on the main thread."""

    def __init__(self, controller, context, report_dir):
        super().__init__()
        self.controller, self.ctx, self.report_dir = controller, context, Path(report_dir)
        self.lock = threading.Lock()
        self.requested = None      # set by command() on the reply worker thread; started on the main thread
        self.requested_local = False
        self.local = False
        self.stop_requested = False
        self.running = False
        self.waiting = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timed_out)
        controller.replyFinished.connect(self._reply_finished)

    # -------------------------------------------------- commands (called from LocalRoutines on a worker thread)
    def command(self, text, local=False):
        key = ' '.join(text.lower().split()).rstrip('.!?')
        if match := re.fullmatch(r'run (?:the |all )?challenge tests?(?: (?:for |on )?(?P<area>[a-z]+))?', key):
            area = match['area']
            if area and area not in AREAS:
                return f'Choose one of: {", ".join(AREAS)}, or say "run challenge tests" for all of them.'
            with self.lock:
                if self.running:
                    return 'A challenge run is already in progress. Say "stop challenge tests" to end it.'
                self.requested = area or 'all'
                self.requested_local = bool(local)
            tests = [t for t in TESTS if not area or t['area'] == area]
            return (f'Starting {len(tests)} challenge test(s) in a new chat. Each runs as a real task and is checked against '
                    'its actual result; failures are sent back to me to diagnose and fix (up to 2 retries). '
                    'Please leave Ano alone until the scorecard appears. ' +
                    ('Local-only run: every step and retry stays on the free brain.' if local else
                     'Paid brain: this uses OpenAI credits.'))
        if key in ('stop challenge tests', 'stop the challenge tests', 'cancel challenge tests'):
            with self.lock:
                self.stop_requested = True
                was = self.running or self.requested
                self.requested = None
            return 'Stopping the challenge run after the current step.' if was else 'No challenge run is in progress.'
        if key in ('challenge results', 'show challenge results', 'last challenge results'):
            reports = sorted(self.report_dir.glob('challenge-*.json'))
            if not reports:
                return 'No challenge run has finished yet. Say "run challenge tests".'
            return json.loads(reports[-1].read_text(encoding='utf-8'))['scorecard']
        return None

    def summary(self):
        """The newest result for every area, across all saved runs (runs are often one area at a time)."""
        latest = {}
        for path in sorted(self.report_dir.glob('challenge-*.json')):
            try:
                report = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            for area in {r['area'] for r in report.get('results', [])}:
                latest[area] = dict(area=area, when=report.get('started', ''), tag=report.get('tag', ''),
                                    results=[dict(title=r['title'], status=r['status'], attempts=r.get('attempts', 1),
                                                  why=(r.get('explanation') or r.get('check') or '')[:200])
                                             for r in report['results'] if r['area'] == area])
        rows = []
        for area in AREAS:
            if area in latest:
                row = latest[area]
                row['passed'] = sum(r['status'] in ('passed', 'fixed on retry') for r in row['results'])
                row['total'] = sum(r['status'] != 'needs you' for r in row['results'])
                row['needs_you'] = sum(r['status'] == 'needs you' for r in row['results'])
                rows.append(row)
            else:
                rows.append(dict(area=area, when='', tag='', results=[], passed=0, total=0, needs_you=0))
        return rows

    # -------------------------------------------------- the run
    @Slot(str, bool)
    def _reply_finished(self, reply, failed):
        if self.requested and not self.running:
            QTimer.singleShot(600, self._start)
            return
        if not self.running or not self.waiting:
            return
        self.waiting = False
        self.current['replies'].append(reply)
        QTimer.singleShot(400, self._advance)

    def _start(self):
        with self.lock:
            area, self.requested = self.requested, None
            if area is None:
                return
            self.running, self.stop_requested = True, False
            self.local = self.requested_local
        self.tag = 'AC' + datetime.now().strftime('%m%d%H%M')
        self.ctx['challenge_dir'].mkdir(parents=True, exist_ok=True)
        self.queue = [dict(t) for t in TESTS if area == 'all' or t['area'] == area]
        self.results, self.started = [], datetime.now()
        self.cost_before = self._spent()
        self.controller.newChat()
        self._next_test()

    def _next_test(self):
        if self.stop_requested or not self.queue:
            self._finish()
            return
        test = self.queue.pop(0)
        run = dict(test=test, tag=self.tag, started=datetime.now(), replies=[], attempt=1, step=0,
                   baseline=self._last_action(), secret='', last='',
                   monitor_baseline=max([rule['id'] for rule in self.ctx['monitors'].active()], default=0))
        self.current = run
        if test.get('cleanup') == 'windows':
            try:
                from .plugins.windows import open_windows
                run['windows_before'] = {row[0] for row in open_windows()}
            except Exception:
                run['windows_before'] = None  # unknown: then nothing is closed
        if test.get('setup') == 'image':
            run['secret'] = self._make_image(run)
        if not test['steps']:
            ok, reason = test['check'](self.ctx, run)
            self._record(run, None if ok is None else ok, reason)
            QTimer.singleShot(200, self._next_test)
            return
        self.timer.start(TEST_TIMEOUT_MS)
        self._send_step()

    def _fill(self, text, run):
        image = str(self.ctx['challenge_dir'] / f'{run["tag"]}-code.png')
        return text.replace('{tag}', run['tag']).replace('{dir}', str(self.ctx['challenge_dir'])).replace('{image}', image)

    def _send_step(self):
        run = self.current
        step = run['test']['steps'][run['step']]
        if step == '@new':
            self.controller.newChat()
            run['step'] += 1
            QTimer.singleShot(300, self._send_step)
            return
        self.waiting = True
        self._send(self._fill(step, run))

    def _send(self, text):
        self.controller.send(('local: ' if self.local else '') + text)

    def _advance(self):
        run = self.current
        run['last'] = run['replies'][-1] if run['replies'] else ''
        if self.stop_requested:
            self._record(run, False, 'stopped by you')
            self._finish()
            return
        if run['step'] + 1 < len(run['test']['steps']) and run['attempt'] == 1:
            run['step'] += 1
            self._send_step()
            return
        try:
            ok, reason = run['test']['check'](self.ctx, run)
        except Exception as error:
            ok, reason = False, f'the result could not be checked ({error})'
        if ok or run['attempt'] >= MAX_ATTEMPTS:
            self._record(run, ok, reason)
            QTimer.singleShot(500, self._next_test)
            return
        run['attempt'] += 1
        run['why'] = reason
        self.waiting = True
        self._send(f'Check failed for "{run["test"]["title"]}": {reason}. Find out why it went wrong by looking at '
                             'what you actually produced, fix it, and finish the task. Do not ask me questions; '
                             'if something truly blocks you, say exactly what and why.')

    def _timed_out(self):
        if not self.running or not self.waiting:
            return
        self.waiting = False
        self.controller.stopTask()
        self._record(self.current, False, 'it did not finish within 10 minutes')
        QTimer.singleShot(3000, self._next_test)

    def _record(self, run, ok, reason):
        self.timer.stop()
        self._cleanup(run)
        status = 'needs you' if ok is None else ('passed' if run['attempt'] == 1 else 'fixed on retry') if ok else 'failed'
        self.results.append(dict(id=run['test']['id'], area=run['test']['area'], title=run['test']['title'],
                                 status=status, attempts=run['attempt'], check=reason if not ok else '',
                                 explanation=(run['last'] or '')[:400] if status == 'failed' else '',
                                 seconds=round((datetime.now() - run['started']).total_seconds())))

    def _cleanup(self, run):
        kind = run['test'].get('cleanup')
        try:
            if kind == 'scheduled':
                for action in self.ctx['scheduled'].pending():
                    if run['tag'] in action['text']:
                        self.ctx['scheduled'].cancel(action['id'])
            elif kind == 'monitor':
                for rule in self.ctx['monitors'].active():
                    if rule['condition'] == 'download_done' and rule['id'] > run['monitor_baseline']:
                        self.ctx['monitors'].stop(rule['id'])
            elif kind == 'reminder':
                with closing(sqlite3.connect(str(self.ctx['notes_db']))) as db, db:
                    db.execute('DELETE FROM reminders WHERE done=0 AND lower(body) LIKE ?', (f'%{run["tag"].lower()}%',))
            elif kind == 'windows':
                # Close only windows this test opened (new since it started) that are its own: the saved
                # "AC….txt - Notepad", an empty "Untitled - Notepad", or Calculator. Unsaved ones ("*…") are left
                # alone: Windows would ask to save, and only the user should answer that.
                import ctypes
                from .plugins.windows import open_windows
                for hwnd, _pid, title, *_ in open_windows():
                    ours = run['tag'] in title or title in ('Untitled - Notepad', 'Calculator')
                    if hwnd not in run.get('windows_before', ()) and ours and not title.startswith('*'):
                        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        except Exception:
            pass

    def _finish(self):
        self.timer.stop()
        self.running = self.waiting = False
        counts = {s: sum(r['status'] == s for r in self.results) for s in ('passed', 'fixed on retry', 'failed', 'needs you')}
        automatic = len(self.results) - counts['needs you']
        cost = max(0.0, self._spent() - self.cost_before)
        marks = {'passed': 'PASS ', 'fixed on retry': 'FIXED', 'failed': 'FAIL ', 'needs you': 'YOU  '}
        lines = [f'Challenge run {self.tag} · {counts["passed"] + counts["fixed on retry"]}/{automatic} passed '
                 f'({counts["fixed on retry"]} fixed on retry) · {counts["failed"]} failed · {counts["needs you"]} need you · '
                 f'OpenAI cost about ${cost:.2f} · {round((datetime.now() - self.started).total_seconds() / 60)} min']
        for r in self.results:
            line = f'{marks[r["status"]]} {r["area"]} · {r["title"]}'
            if r['status'] == 'failed':
                line += f' — check: {r["check"]}'
            elif r['status'] == 'needs you':
                line += f' — {r["check"]}'
            elif r['status'] == 'fixed on retry':
                line += f' (attempt {r["attempts"]})'
            lines.append(line)
        failed = [r for r in self.results if r['status'] == 'failed']
        if failed:
            lines.append('')
            lines.append('Why the failures happened (Ano’s last reply):')
            lines += [f'· {r["title"]}: {" ".join(r["explanation"].split())[:220]}' for r in failed]
        scorecard = '\n'.join(lines)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        report = self.report_dir / f'challenge-{self.tag}.json'
        report.write_text(json.dumps(dict(tag=self.tag, started=self.started.isoformat(timespec='seconds'),
                                          cost_usd=round(cost, 4), results=self.results, scorecard=scorecard),
                                     indent=2, ensure_ascii=False), encoding='utf-8')
        self.controller.add_message('assistant', scorecard + f'\n\n· Report saved: {report}')
        changed = getattr(self.controller, 'challengesChanged', None)
        if changed is not None:
            changed.emit()

    # -------------------------------------------------- helpers
    def _spent(self):
        try:
            return float(self.ctx['ledger'].totals()[1])
        except Exception:
            return 0.0

    def _last_action(self):
        try:
            with closing(sqlite3.connect(str(self.ctx['action_log']))) as db, db:
                return db.execute('SELECT coalesce(max(id), 0) FROM actions').fetchone()[0]
        except sqlite3.Error:
            return 0

    def _make_image(self, run):
        """A test picture with a random code drawn in it, so the answer can only come from reading the image."""
        from PySide6.QtGui import QColor, QFont, QImage, QPainter
        secret = f'{random.randrange(100000, 999999)}'
        image = QImage(640, 280, QImage.Format_RGB32)
        image.fill(QColor('#f4f1e8'))
        painter = QPainter(image)
        painter.setPen(QColor('#1d2b3a'))
        font = QFont('Segoe UI', 26)
        painter.setFont(font)
        painter.drawText(40, 90, 'Ano challenge picture')
        font.setPointSize(54)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(40, 200, f'CODE {secret}')
        painter.end()
        image.save(str(self.ctx['challenge_dir'] / f'{run["tag"]}-code.png'))
        return secret
