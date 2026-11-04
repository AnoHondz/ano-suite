"""Bounded local action diagnostics: names, result flags and timings, never payloads."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3


class ActionLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY, at TEXT NOT NULL, tool TEXT NOT NULL,
                failed INTEGER NOT NULL, milliseconds INTEGER NOT NULL)''')

    def record(self, tool, failed, milliseconds):
        # Unknown strings may contain user data; only persist identifier-shaped names.
        tool = tool if re.fullmatch(r'[a-z][a-z0-9_]{0,63}', str(tool)) else 'invalid_tool_name'
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute('INSERT INTO actions(at,tool,failed,milliseconds) VALUES (?,?,?,?)',
                       (datetime.now(timezone.utc).isoformat(timespec='seconds'), tool,
                        int(bool(failed)), max(0, min(int(milliseconds), 86_400_000))))
            db.execute('DELETE FROM actions WHERE id NOT IN (SELECT id FROM actions ORDER BY id DESC LIMIT 2000)')

    def definitions(self):
        return [{'name': 'recent_tool_activity', 'description':
                 'Read recent action names, success/failure flags and duration. No arguments or document contents are logged. Does not prove UI completion.',
                 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]

    def run(self, name, arguments):
        if name != 'recent_tool_activity' or arguments:
            raise ValueError('Invalid diagnostics request.')
        with closing(sqlite3.connect(self.path, timeout=2)) as db:
            rows = db.execute('SELECT at,tool,failed,milliseconds FROM actions ORDER BY id DESC LIMIT 12').fetchall()
        if not rows:
            return 'No tool actions recorded yet. A model response alone does not mean an action ran.', ''
        lines = ['Recent local tool results (not proof of app display or task completion):']
        lines += [f'{at} | {tool} | {"FAILED" if failed else "returned successfully"} | {ms} ms'
                  for at, tool, failed, ms in rows]
        return '\n'.join(lines), 'Read local action diagnostics'
