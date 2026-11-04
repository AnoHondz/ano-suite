"""Scheduled actions: type exact text into an app at a set time, with no model involved.

Only the user's own typed command creates one (LocalRoutines.reply sees only the user's message);
there is no model tool for it, so typing the command is the user's advance approval. At the due
time a fixed script brings the app to the front, types the text and presses Enter.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import re
import sqlite3

from .tools import ToolError

SCRIPT = Path(__file__).parent / 'assets' / 'type_into_app.ps1'
# Command name -> (display name, process name, Start-menu name pattern used to launch it if closed).
TARGETS = {
    'chatgpt': ('ChatGPT', 'ChatGPT', 'ChatGPT|Codex'),
    'notepad': ('Notepad', 'Notepad', '^Notepad$'),
    'vs code': ('VS Code', 'Code', 'Visual Studio Code'),
}
GRACE = timedelta(minutes=10)  # a late start (laptop asleep, Ano closed) still runs; older ones are reported missed
MAX_TEXT = 500

WHEN = (r'(?:(?:(today|tomorrow) )?at (\d{1,2})(?::(\d{2}))?\s*(am|pm)|in (\d{1,4}) (minutes?|hours?))')
SCHEDULE = re.compile(WHEN + r',? type ["“\'](.+?)["”\'] (?:in|into) (' + '|'.join(TARGETS) + r')[.!]?', re.I)


class ScheduledActions:
    def __init__(self, path, clock=lambda: datetime.now().astimezone()):
        self.path, self.clock = str(path), clock
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS scheduled_actions (id INTEGER PRIMARY KEY, due TEXT NOT NULL, '
                       'target TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL DEFAULT "")')
            # A run interrupted by a crash or shutdown is never replayed.
            db.execute("UPDATE scheduled_actions SET status='failed', result='Ano closed while it was running; it was not repeated.' "
                       "WHERE status='running'")
            db.execute('CREATE TABLE IF NOT EXISTS watches (name TEXT PRIMARY KEY, note TEXT NOT NULL DEFAULT "")')

    def watching(self, name):
        with self._db() as db:
            return db.execute('SELECT 1 FROM watches WHERE name=?', (name,)).fetchone() is not None

    def note(self, name, text):
        with self._db() as db:
            db.execute('UPDATE watches SET note=? WHERE name=?', (f'{self.clock():%I:%M %p} · {text}', name))

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def command(self, text):
        """Handle a scheduling command typed by the user. Returns the reply, or None if it is not one."""
        text = ' '.join(text.split())
        text = re.sub(r'^(?:(?:hey )?ano[,! ]+|please |(?:can|could|would) you(?: please)? )', '', text, flags=re.I)
        key = text.casefold().rstrip('.!?')
        if key in ('stop scheduler', 'stop the scheduler', 'stop ano scheduler'):
            with self._db() as db:
                cancelled = db.execute("UPDATE scheduled_actions SET status='cancelled', result='Stopped by the user.' WHERE status='pending'").rowcount
                watches = db.execute('DELETE FROM watches').rowcount
                running = db.execute("SELECT count(*) FROM scheduled_actions WHERE status='running'").fetchone()[0]
            return (f'Stopped Ano scheduler: cancelled {cancelled} waiting actions and disabled {watches} watches. '
                    f'{running} actions were already running; dispatched input cannot be recalled. '
                    'New schedules can still be created explicitly.')
        if key in ('scheduler status', 'show scheduler status', 'did you stop scheduler',
                   'did you stop the scheduler', 'did you stop the most recent scheduler'):
            with self._db() as db:
                pending = db.execute("SELECT count(*) FROM scheduled_actions WHERE status='pending'").fetchone()[0]
                running = db.execute("SELECT count(*) FROM scheduled_actions WHERE status='running'").fetchone()[0]
                watches = [row[0] for row in db.execute('SELECT name FROM watches ORDER BY name')]
            return (f'Current Ano scheduler state: {pending} waiting actions; {running} running actions. '
                    'Active watches: ' + (', '.join(watches) or 'none') + '. '
                    'This checks current state; it does not infer what an earlier model reply did.')
        if key in ('show scheduled actions', 'list scheduled actions', 'show scheduled tasks'):
            return self.listing()
        match = re.fullmatch(r'cancel scheduled (?:action|task) #?(\d+)', key)
        if match:
            return self.cancel(int(match[1]))
        if key in ('watch codex', 'watch codex overnight', 'keep codex going', 'keep codex running'):
            with self._db() as db:
                db.execute('INSERT OR IGNORE INTO watches(name) VALUES (?)', ('codex',))
            return ('Watching Codex. Every 10 minutes Ano reads the ChatGPT window; if the goal shows “Goal usage limited”, '
                    'Ano presses Resume goal 2 minutes after the reset time (hourly if no time is shown) and checks that it runs. '
                    'While watching, the laptop is kept awake; the screen can still turn off. Stop with: stop watching codex')
        if key in ('stop watching codex', 'stop codex watch'):
            with self._db() as db:
                db.execute('DELETE FROM watches WHERE name=?', ('codex',))
            return 'Stopped watching Codex. Normal sleep settings apply again.'
        if key in ('codex watch status', 'is codex being watched', 'codex status'):
            with self._db() as db:
                row = db.execute('SELECT note FROM watches WHERE name=?', ('codex',)).fetchone()
            if row is None:
                return 'Codex is not being watched. Start with: watch codex'
            return 'Watching Codex. Latest: ' + (row[0] or 'no check has finished yet.')
        match = SCHEDULE.fullmatch(text)
        if not match:
            if re.match(r'(?:(?:today|tomorrow) )?at \d|in \d+ (?:minute|hour)', key) and ' type ' in key:
                raise ToolError('Use: at 11:19 pm type "continue" in chatgpt. Apps: ' + ', '.join(TARGETS) + '. Nothing was scheduled.')
            return None
        day, hour, minute, meridiem, amount, unit, typed, target = match.groups()
        due = self._due(day, hour, minute, meridiem, amount, unit)
        if not typed.strip() or len(typed) > MAX_TEXT:
            raise ToolError(f'Type between 1 and {MAX_TEXT} characters. Nothing was scheduled.')
        target = target.casefold()
        with self._db() as db:
            identifier = db.execute("INSERT INTO scheduled_actions(due, target, text, status) VALUES (?,?,?,'pending')",
                                    (due.isoformat(timespec='seconds'), target, typed)).lastrowid
        name = TARGETS[target][0]
        return (f'Scheduled #{identifier}: {due:%a %d %b at %I:%M %p}, Ano brings {name} to the front, types '
                f'“{typed}” and presses Enter. It runs without asking, as long as Ano is running, not paused, '
                f'and the screen is unlocked. Cancel with: cancel scheduled action {identifier}')

    def _due(self, day, hour, minute, meridiem, amount, unit):
        now = self.clock()
        if amount:
            minutes = int(amount) * (60 if unit.lower().startswith('hour') else 1)
            if not 1 <= minutes <= 7 * 24 * 60:
                raise ToolError('Choose a time between one minute and one week from now. Nothing was scheduled.')
            return (now + timedelta(minutes=minutes)).replace(second=0, microsecond=0)
        hour, minute = int(hour), int(minute or 0)
        if not 1 <= hour <= 12 or not 0 <= minute <= 59:
            raise ToolError('Use a valid time from 1:00 to 12:59 am or pm. Nothing was scheduled.')
        hour = hour % 12 + (12 if meridiem.lower() == 'pm' else 0)
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day and day.lower() == 'tomorrow':
            due += timedelta(days=1)
        if due <= now:
            raise ToolError('That time has already passed today. Say “tomorrow at …” instead. Nothing was scheduled.')
        return due

    def pending(self):
        """Waiting typing actions for the UI: [{id, due, target, text}]."""
        with self._db() as db:
            rows = db.execute("SELECT id, due, target, text FROM scheduled_actions WHERE status='pending' ORDER BY due").fetchall()
        return [dict(id=i, due=due, app=TARGETS[target][0], text=text) for i, due, target, text in rows]

    def last_note(self, name):
        """The text of a watch's latest note, without its time stamp ('' if none)."""
        with self._db() as db:
            row = db.execute('SELECT note FROM watches WHERE name=?', (name,)).fetchone()
        return row[0].split(' · ', 1)[-1] if row and row[0] else ''

    def queue_now(self, target, text):
        """Type text into an app on the next tick (used by monitor rules the user typed). Returns the action id."""
        if target not in TARGETS or not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise ToolError('That typing action is not valid.')
        with self._db() as db:
            return db.execute("INSERT INTO scheduled_actions(due, target, text, status) VALUES (?,?,?,'pending')",
                              (self.clock().isoformat(timespec='seconds'), target, text)).lastrowid

    def listing(self):
        with self._db() as db:
            rows = db.execute("SELECT id, due, target, text FROM scheduled_actions WHERE status='pending' ORDER BY due").fetchall()
        if not rows:
            return 'No scheduled actions are waiting.'
        return 'Scheduled actions:\n' + '\n'.join(
            f'#{i} · {datetime.fromisoformat(due):%a %d %b %I:%M %p} · type “{text}” in {TARGETS[target][0]}'
            for i, due, target, text in rows)

    def cancel(self, identifier):
        with self._db() as db:
            changed = db.execute("UPDATE scheduled_actions SET status='cancelled' WHERE id=? AND status='pending'",
                                 (identifier,)).rowcount
        if not changed:
            raise ToolError(f'No waiting scheduled action #{identifier}. Say “show scheduled actions”.')
        return f'Cancelled scheduled action #{identifier}. Nothing will be typed.'

    def take_due(self):
        """Claim actions that are due now. Ones more than GRACE late are marked missed and returned as such."""
        now = self.clock()
        claimed = []
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT id, due, target, text FROM scheduled_actions WHERE status='pending' AND due<=? ORDER BY due",
                              (now.isoformat(timespec='seconds'),)).fetchall()
            for identifier, due, target, text in rows:
                missed = now - datetime.fromisoformat(due) > GRACE
                status = 'missed' if missed else 'running'
                db.execute('UPDATE scheduled_actions SET status=?, result=? WHERE id=?',
                           (status, 'Ano was not running at that time, so nothing was typed.' if missed else '', identifier))
                claimed.append(dict(id=identifier, due=due, target=target, text=text, missed=missed))
        return claimed

    def finish(self, identifier, ok, result):
        with self._db() as db:
            db.execute('UPDATE scheduled_actions SET status=?, result=? WHERE id=?',
                       ('done' if ok else 'failed', result[:500], identifier))

    @staticmethod
    def arguments(action):
        """powershell.exe arguments that run the fixed typing script for one claimed action."""
        name, process, start_name = TARGETS[action['target']]
        return ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT),
                '-Process', process, '-StartName', start_name, '-Text', action['text']]
