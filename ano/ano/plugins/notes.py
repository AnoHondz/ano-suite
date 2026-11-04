"""Local notes and reminders in Ano's SQLite file. Each call opens its own connection (tools run off-thread)."""
from datetime import datetime, timedelta, timezone
import sqlite3

from ..tools import ToolError

MAX_LIST = 50


class Notebook:
    def __init__(self, path, clock=lambda: datetime.now(timezone.utc)):
        self.path = path
        self.clock = clock
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY, body TEXT NOT NULL, due_at TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0);
            """)

    def definitions(self):
        text = {'type': 'string'}
        number = {'type': 'integer'}
        return [
            {'name': 'add_note', 'description': 'Save a note on this device.',
             'input_schema': {'type': 'object', 'additionalProperties': False,
                              'properties': {'text': text}, 'required': ['text']}},
            {'name': 'search_notes', 'description': 'Find saved notes containing all the words; empty lists the newest.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {'query': text}}},
            {'name': 'delete_note', 'description': 'Delete a note by id.',
             'input_schema': {'type': 'object', 'additionalProperties': False,
                              'properties': {'id': number}, 'required': ['id']}},
            {'name': 'add_reminder',
             'description': 'Remind the user at a local time with a Windows notification (while Ano is running; '
                            'missed reminders appear when Ano next starts). For "in N minutes/hours" give in_minutes; '
                            'for a clock time give when. "N hours before/after a time" means that clock time minus/plus '
                            'N hours, given as when (2 hours before 6 pm today -> 16:00 today), never in_minutes.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                 'text': text, 'when': {'type': 'string', 'description': 'Local YYYY-MM-DDTHH:MM'},
                 'in_minutes': {'type': 'integer', 'description': 'Minutes from now, e.g. 90 for "in an hour and a half"'}},
                 'required': ['text']}},
            {'name': 'list_reminders', 'description': 'List upcoming reminders.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {}}},
            {'name': 'cancel_reminder', 'description': 'Cancel a reminder by id.',
             'input_schema': {'type': 'object', 'additionalProperties': False,
                              'properties': {'id': number}, 'required': ['id']}},
        ]

    def run(self, name, arguments):
        return getattr(self, name)(**arguments)

    def add_note(self, text):
        if not text.strip():
            raise ToolError('The note is empty.')
        with self._db() as db:
            identifier = db.execute('INSERT INTO notes(body, created_at) VALUES (?, ?)',
                                    (text.strip(), self.clock().isoformat())).lastrowid
        return f'Saved note {identifier}.', 'Saved a note'

    def search_notes(self, query=''):
        words = query.casefold().split()
        with self._db() as db:
            rows = db.execute('SELECT id, body, created_at FROM notes ORDER BY id DESC').fetchall()
        rows = [row for row in rows if all(word in row[1].casefold() for word in words)][:MAX_LIST]
        lines = [f'note {row[0]} · {local(row[2]):%d %b %Y} · {row[1]}' for row in rows]
        return '\n'.join(lines) or 'No matching notes.', 'Searched your notes'

    def delete_note(self, id):
        with self._db() as db:
            deleted = db.execute('DELETE FROM notes WHERE id=?', (int(id),)).rowcount
        if not deleted:
            raise ToolError('No note with that id.')
        return f'Deleted note {id}.', 'Deleted a note'

    def add_reminder(self, text, when='', in_minutes=None):
        if not text.strip():
            raise ToolError('The reminder needs some text.')
        if in_minutes is not None:
            # Relative times are computed here: small local models get clock arithmetic wrong.
            if when or not 1 <= int(in_minutes) <= 7 * 24 * 60:
                raise ToolError('Give either when or in_minutes (1 minute to 7 days), not both.')
            due = self.clock() + timedelta(minutes=int(in_minutes))
        elif not when:
            raise ToolError('Say when: a local time like 2026-10-27T14:30, or in_minutes from now.')
        else:
            try:
                due = datetime.fromisoformat(when.strip()).astimezone(timezone.utc)
            except ValueError:
                raise ToolError('Use a local time like 2026-10-27T14:30.') from None
        if due <= self.clock():
            raise ToolError('That time has already passed.')
        with self._db() as db:
            identifier = db.execute('INSERT INTO reminders(body, due_at) VALUES (?, ?)',
                                    (text.strip(), due.isoformat())).lastrowid
        return f'Reminder {identifier} set for {local(due.isoformat()):%a %d %b %H:%M}.', \
            f'Reminder set for {local(due.isoformat()):%a %d %b %H:%M}'

    def list_reminders(self):
        with self._db() as db:
            rows = db.execute('SELECT id, body, due_at FROM reminders WHERE done=0 ORDER BY due_at LIMIT ?',
                              (MAX_LIST,)).fetchall()
        lines = [f'reminder {row[0]} · {local(row[2]):%a %d %b %H:%M} · {row[1]}' for row in rows]
        return '\n'.join(lines) or 'No upcoming reminders.', 'Checked your reminders'

    def cancel_reminder(self, id):
        with self._db() as db:
            cancelled = db.execute('DELETE FROM reminders WHERE id=? AND done=0', (int(id),)).rowcount
        if not cancelled:
            raise ToolError('No upcoming reminder with that id.')
        return f'Cancelled reminder {id}.', 'Cancelled a reminder'

    def take_due(self):
        """Return [(text, due local datetime)] that are due now, marking them done."""
        now = self.clock().isoformat()
        with self._db() as db:
            rows = db.execute('SELECT id, body, due_at FROM reminders WHERE done=0 AND due_at<=? ORDER BY due_at',
                              (now,)).fetchall()
            db.executemany('UPDATE reminders SET done=1 WHERE id=?', [(row[0],) for row in rows])
        return [(row[1], local(row[2])) for row in rows]

    def _db(self):
        return Connection(self.path)


class Connection:
    """sqlite3's own context manager commits but never closes."""
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10)

    def __enter__(self):
        return self.db

    def __exit__(self, kind, value, traceback):
        if kind is None:
            self.db.commit()
        else:
            self.db.rollback()
        self.db.close()


def local(stamp):
    return datetime.fromisoformat(stamp).astimezone()
