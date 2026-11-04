"""Local conversation storage with transactional full-text indexing."""
import sqlite3
import json
from pathlib import Path
import uuid
import os
import time


class Memory:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        try:
            if self.db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise sqlite3.DatabaseError('Ano memory failed its integrity check. Recover it before continuing.')
        except sqlite3.DatabaseError:
            self.db.close()
            raise
        # Python installations can ship SQLite older than the 3.51.3 WAL-reset
        # fix. Ano has concurrent notebook/activity connections, so use the
        # rollback journal rather than expose those runtimes to that race.
        # This trades some writer concurrency for a conservative durable mode.
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                body, content='messages', content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS messages_insert AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, body) VALUES (new.id, new.body);
            END;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY, app TEXT NOT NULL, title TEXT NOT NULL,
                started_at TEXT NOT NULL, ended_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS activity_started ON activity(started_at);
            CREATE VIRTUAL TABLE IF NOT EXISTS activity_fts USING fts5(
                app, title, content='activity', content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS activity_insert AFTER INSERT ON activity BEGIN
                INSERT INTO activity_fts(rowid, app, title) VALUES(new.id, new.app, new.title);
            END;
            CREATE TRIGGER IF NOT EXISTS activity_delete AFTER DELETE ON activity BEGIN
                INSERT INTO activity_fts(activity_fts, rowid, app, title)
                VALUES('delete', old.id, old.app, old.title);
            END;
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT 'New chat',
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE TRIGGER IF NOT EXISTS messages_delete AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, body) VALUES('delete', old.id, old.body);
            END;
        """)
        # Older databases have one conversation; it becomes the first chat.
        columns = [row[1] for row in self.db.execute('PRAGMA table_info(messages)')]
        with self.db:
            if 'chat_id' not in columns:
                self.db.execute('ALTER TABLE messages ADD COLUMN chat_id INTEGER NOT NULL DEFAULT 1')
            if 'in_reply_to' not in columns:
                self.db.execute('ALTER TABLE messages ADD COLUMN in_reply_to INTEGER')
            if not self.db.execute('SELECT 1 FROM chats').fetchone():
                title = 'Earlier chat' if self.db.execute('SELECT 1 FROM messages').fetchone() else 'New chat'
                self.db.execute('INSERT INTO chats(id, title) VALUES (1, ?)', (title,))

    def latest_chat(self) -> int:
        return self.db.execute('SELECT id FROM chats ORDER BY updated_at DESC, id DESC LIMIT 1').fetchone()[0]

    def chats(self) -> list[dict]:
        rows = self.db.execute('SELECT id, title, updated_at FROM chats ORDER BY updated_at DESC, id DESC')
        return [dict(zip(('id', 'title', 'updated_at'), row)) for row in rows]

    def new_chat(self) -> int:
        with self.db:
            return self.db.execute('INSERT INTO chats DEFAULT VALUES').lastrowid

    def delete_chat(self, chat: int):
        with self.db:
            self.db.execute('DELETE FROM messages WHERE chat_id=?', (chat,))
            self.db.execute('DELETE FROM chats WHERE id=?', (chat,))
            if not self.db.execute('SELECT 1 FROM chats').fetchone():
                self.db.execute('INSERT INTO chats DEFAULT VALUES')

    def add(self, role: str, body: str, chat: int | None = None, reply_to: int | None = None):
        chat = chat or self.latest_chat()
        with self.db:
            if reply_to is not None:
                request = self.db.execute('SELECT role,chat_id FROM messages WHERE id=?', (reply_to,)).fetchone()
                if role != 'assistant' or request != ('user', chat):
                    raise ValueError('A reply must reference a user request in the same chat.')
            message_id = self.db.execute("INSERT INTO messages(role, body, chat_id,in_reply_to) VALUES (?, ?, ?, ?)",
                                         (role, body, chat, reply_to)).lastrowid
            # A chat is named after its first question and sorts by its latest message.
            self.db.execute("UPDATE chats SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (chat,))
            if role == 'user':
                title = ' '.join(body.split())
                title = title[:40] + ('…' if len(title) > 40 else '')
                self.db.execute("UPDATE chats SET title=? WHERE id=? AND title='New chat'", (title, chat))
        return message_id

    def history_for_request(self, request_id, chat, limit=20):
        """Order completed turns for a queued request, excluding later commands.

        Replies can arrive after a later command was queued. Link them to their
        original user request rather than letting the model act on the wrong one.
        """
        if self.db.execute('SELECT role,chat_id FROM messages WHERE id=?', (request_id,)).fetchone() != ('user', chat):
            raise ValueError('Request is not a user message in this chat.')
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('History limit must be between 1 and 200.')
        rows = self.db.execute('''SELECT role,body,created_at,
            COALESCE(in_reply_to,id) AS turn,
            CASE WHEN role='user' THEN 0 ELSE 1 END AS role_order,id
            FROM messages WHERE chat_id=? AND (
                (role='user' AND id<=?) OR
                (role='assistant' AND in_reply_to<?) OR
                (role='assistant' AND in_reply_to IS NULL AND id<?
                 AND body NOT LIKE 'SMC research%'
                 AND body NOT LIKE 'AMD / Power of Three%'
                 AND body NOT LIKE 'ICT 2022 %'
                 AND body NOT LIKE 'Silver Bullet %'
                 AND body NOT LIKE 'Gap fill %'))
            ORDER BY turn DESC,role_order DESC,id DESC LIMIT ?''',
            (chat, request_id, request_id, request_id, limit)).fetchall()
        return [dict(zip(('role', 'body', 'created_at'), row[:3])) for row in reversed(rows)]

    def recent(self, limit: int = 100, chat: int | None = None) -> list[dict]:
        rows = self.db.execute(
            "SELECT role, body, created_at FROM (SELECT * FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (chat or self.latest_chat(), limit),
        )
        return [dict(zip(('role', 'body', 'created_at'), row)) for row in rows]

    def search(self, query: str) -> list[dict]:
        # Quote each token so arbitrary user input never becomes FTS syntax.
        tokens = query.split()
        if not tokens:
            return []
        expression = ' AND '.join('"' + token.replace('"', '""') + '"' for token in tokens)
        rows = self.db.execute("""
            SELECT m.role, m.body, m.created_at FROM messages_fts f
            JOIN messages m ON m.id=f.rowid WHERE messages_fts MATCH ?
            ORDER BY rank LIMIT 50
        """, (expression,))
        return [dict(zip(('role', 'body', 'created_at'), row)) for row in rows]

    def close(self):
        self.db.close()

    def backup_to(self, destination, max_seconds=5, clock=time.monotonic):
        """Create a new consistent, integrity-checked SQLite backup, never a raw copy."""
        destination = Path(destination)
        if destination.exists():
            raise FileExistsError('The backup already exists; it was not replaced.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name('.' + uuid.uuid4().hex + '.partial')
        target = None
        deadline = clock() + max_seconds
        def progress(_status, _remaining, _total):
            if clock() >= deadline:
                raise TimeoutError('Local backup exceeded its time limit; no snapshot was published.')
        try:
            target = sqlite3.connect(temporary)
            self.db.backup(target, pages=128, progress=progress, sleep=.05)
            if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise sqlite3.DatabaseError('The backup did not pass its integrity check.')
            target.close()
            target = None
            # Same-directory hard link publishes the verified bytes atomically,
            # refuses an existing name, and never exposes a half-written backup.
            os.link(temporary, destination)
            return destination
        finally:
            if target is not None:
                target.close()
            temporary.unlink(missing_ok=True)

    def setting(self, key, default):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (key, json.dumps(value)))

    def set_settings(self, values):
        """Commit a preference migration as one transaction, or leave it unapplied."""
        rows = [(key, json.dumps(value, allow_nan=False)) for key, value in values.items()]
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO settings VALUES (?, ?)', rows)

    def start_activity(self, app, title, timestamp):
        with self.db:
            cursor = self.db.execute(
                'INSERT INTO activity(app,title,started_at,ended_at) VALUES(?,?,?,?)',
                (app, title, timestamp, timestamp))
        return cursor.lastrowid

    def touch_activity(self, identifier, timestamp):
        with self.db:
            self.db.execute('UPDATE activity SET ended_at=? WHERE id=?', (timestamp, identifier))

    def activities(self, query='', limit=100, since=None, until=None):
        clauses, parameters = [], []
        join = ''
        if query.strip():
            join = 'JOIN activity_fts f ON f.rowid=a.id'
            clauses.append('activity_fts MATCH ?')
            parameters.append(' AND '.join('"' + word.replace('"', '""') + '"' for word in query.split()))
        if since:
            clauses.append('a.ended_at >= ?')
            parameters.append(since)
        if until:
            clauses.append('a.started_at < ?')
            parameters.append(until)
        where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
        rows = self.db.execute(f'''
            SELECT a.id,a.app,a.title,a.started_at,a.ended_at FROM activity a {join} {where}
            ORDER BY a.started_at DESC,a.id DESC LIMIT ?
        ''', [*parameters, limit])
        return [dict(zip(('id', 'app', 'title', 'started_at', 'ended_at'), row)) for row in rows]

    def delete_activity(self, identifier=None):
        with self.db:
            if identifier is None:
                self.db.execute('DELETE FROM activity')
            else:
                self.db.execute('DELETE FROM activity WHERE id=?', (identifier,))
