"""Prepare a new shared state directory from two closed, verified local copies.

Never deletes or changes either source. Activation marker is written last.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ano.memory import Memory


def connect_readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def migrate(primary, secondary, destination):
    primary, secondary, destination = map(Path, (primary, secondary, destination))
    if destination.exists():
        raise FileExistsError('Choose a new destination; no state was overwritten.')
    # Complete preflight before creating destination or touching any source.
    for root in (primary, secondary):
        with closing(connect_readonly(root / 'memory.sqlite3')) as db:
            if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('A source database is not healthy. Recover a copy first.')
    destination.mkdir(parents=True)
    counts = {}
    with closing(connect_readonly(primary / 'memory.sqlite3')) as source:
        with closing(sqlite3.connect(destination / 'memory.sqlite3')) as target:
            source.backup(target)
    memory = Memory(destination / 'memory.sqlite3')
    try:
        with closing(connect_readonly(secondary / 'memory.sqlite3')) as source, memory.db:
            for identifier, title, timestamp in source.execute('SELECT id,title,updated_at FROM chats'):
                rows = source.execute('SELECT role,body,created_at FROM messages WHERE chat_id=? ORDER BY id', (identifier,)).fetchall()
                if not rows:
                    continue
                cursor = memory.db.execute('INSERT INTO chats(title,updated_at) VALUES (?,?)',
                                           ('Imported native: ' + title, timestamp))
                chat_id = cursor.lastrowid
                for role, body, at in rows:
                    memory.db.execute('INSERT INTO messages(role,body,created_at,chat_id) VALUES (?,?,?,?)',
                                      (role, body, at, chat_id))
                counts['secondary_messages_imported'] = counts.get('secondary_messages_imported', 0) + len(rows)
            for key, value in source.execute('SELECT key,value FROM settings'):
                memory.db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)', (key, value))
        # Preserve the complete secondary database, including any notes/activity,
        # as an independent archive. Those records are not silently merged by ID.
        archive = destination / 'migration-archives'
        archive.mkdir()
        for label, root in (('primary', primary), ('secondary', secondary)):
            for name in ('memory.sqlite3', 'routines.sqlite3', 'paid-usage.sqlite3', 'action-log.sqlite3'):
                if (root / name).is_file():
                    with closing(connect_readonly(root / name)) as source, closing(sqlite3.connect(archive / (label + '-' + name))) as target:
                        source.backup(target)
        # Secondary contains the latest scheduled-watch configuration; merge any
        # primary custom routine absent from that copy without changing conflicts.
        for name in ('routines.sqlite3', 'paid-usage.sqlite3', 'action-log.sqlite3'):
            selected = secondary if (secondary / name).is_file() else primary
            if (selected / name).is_file():
                with closing(connect_readonly(selected / name)) as source, closing(sqlite3.connect(destination / name)) as target:
                    source.backup(target)
        if (primary / 'routines.sqlite3').exists() and (destination / 'routines.sqlite3').exists():
            with closing(connect_readonly(primary / 'routines.sqlite3')) as source, closing(sqlite3.connect(destination / 'routines.sqlite3')) as target, target:
                for name, commands in source.execute('SELECT name,commands FROM routines'):
                    target.execute('INSERT OR IGNORE INTO routines(name,commands) VALUES (?,?)', (name, commands))
        # Keep the more conservative paid reservation ledger, never reset spend.
        ledgers = []
        for root in (primary, secondary):
            if (root / 'paid-usage.sqlite3').exists():
                with closing(connect_readonly(root / 'paid-usage.sqlite3')) as db:
                    ledgers.append((db.execute('SELECT coalesce(sum(usd),0) FROM charges').fetchone()[0], root))
        if ledgers:
            selected = max(ledgers, key=lambda row: row[0])[1]
            with closing(connect_readonly(selected / 'paid-usage.sqlite3')) as source, closing(sqlite3.connect(destination / 'paid-usage.sqlite3')) as target:
                source.backup(target)
        for name in ('ano_rules.md',):
            selected = primary if (primary / name).is_file() else secondary
            if (selected / name).is_file():
                shutil.copy2(selected / name, destination / name)
        for name in ('speech', 'local-images', 'browser-extension', 'scheduled'):
            selected = primary if (primary / name).is_dir() else secondary
            if (selected / name).is_dir():
                shutil.copytree(selected / name, destination / name)
        if memory.db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RuntimeError('Merged memory failed verification; no activation marker was written.')
        counts['total_messages'] = memory.db.execute('SELECT count(*) FROM messages').fetchone()[0]
        report = {'schema': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                  'primary_source': str(primary.resolve()), 'secondary_source': str(secondary.resolve()),
                  'sources_unchanged': True, 'secondary_other_records': 'Preserved in migration-archives, not merged by ID', **counts}
        (destination / 'migration-complete.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report
    finally:
        memory.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('primary', type=Path)
    parser.add_argument('secondary', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.primary, args.secondary, args.destination), indent=2))
