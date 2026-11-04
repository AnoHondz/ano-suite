"""Recover readable Ano records into a new database; never modify the source.

Run with the project Python: tools/recover_memory.py SOURCE DESTINATION.
Quit Ano and copy its database plus -wal/-shm files before using this tool.
"""
import argparse
import sqlite3
from pathlib import Path

from ano.memory import Memory


def recover(source, destination):
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise ValueError('Destination already exists; choose a new file.')
    old = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    new = Memory(destination)
    report = {}
    try:
        # FTS tables are derived data. Rebuild them through the normal triggers.
        for table in ('settings', 'chats', 'messages', 'activity', 'notes',
                      'reminders', 'browser_tasks'):
            rows = []
            error = None
            schema = old.execute('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?',
                                 (table,)).fetchone()
            if not schema:
                continue
            try:
                cursor = old.execute(f'SELECT * FROM "{table}"')
                for row in cursor:
                    rows.append(row)
            except sqlite3.DatabaseError as exc:
                error = str(exc)
            skipped = 0
            with new.db:
                if table in ('notes', 'reminders', 'browser_tasks'):
                    new.db.execute(schema[0])
                new.db.execute(f'DELETE FROM "{table}"')
                if rows:
                    placeholders = ','.join('?' for _ in rows[0])
                    for row in rows:
                        try:
                            new.db.execute(f'INSERT INTO "{table}" VALUES ({placeholders})', row)
                        except sqlite3.IntegrityError:
                            skipped += 1
            report[table] = {'recovered': len(rows) - skipped, 'invalid_rows': skipped, 'error': error}
        with new.db:
            # Preserve conversation IDs even if the chat index was unreadable.
            new.db.execute('''INSERT OR IGNORE INTO chats(id,title,updated_at)
                SELECT chat_id, 'Recovered chat', MAX(created_at)
                FROM messages GROUP BY chat_id''')
            if not new.db.execute('SELECT 1 FROM chats').fetchone():
                new.db.execute('INSERT INTO chats DEFAULT VALUES')
        result = new.db.execute('PRAGMA integrity_check').fetchall()
        if result != [('ok',)]:
            raise RuntimeError(f'Recovered database failed integrity check: {result}')
        for table in ('messages_fts', 'activity_fts'):
            new.db.execute(f"INSERT INTO {table}({table}) VALUES ('integrity-check')")
        new.db.commit()
        new.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return report
    finally:
        old.close()
        new.close()


if __name__ == '__main__':
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(recover(args.source, args.destination), indent=2))
