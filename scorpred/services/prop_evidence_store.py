"""Append-only evidence for pregame reconstruction, independent of live caches."""
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3


def instant(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Evidence timestamps require an explicit timezone')
    return parsed.astimezone(UTC).isoformat()


class EvidenceStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS prop_evidence (
                    id INTEGER PRIMARY KEY, event_id TEXT NOT NULL,
                    kind TEXT NOT NULL, source TEXT NOT NULL, published_at TEXT NOT NULL,
                    received_at TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS prop_evidence_asof ON prop_evidence(event_id,received_at);
                CREATE TRIGGER IF NOT EXISTS prop_evidence_no_update BEFORE UPDATE ON prop_evidence
                    BEGIN SELECT RAISE(ABORT,'Evidence snapshots are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS prop_evidence_no_delete BEFORE DELETE ON prop_evidence
                    BEGIN SELECT RAISE(ABORT,'Evidence snapshots are immutable'); END;
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def append(self, event_id, kind, source, published_at, payload, *, received_at=None):
        if not all(isinstance(value, str) and value.strip() for value in (event_id, kind, source)):
            raise ValueError('An event identity, evidence kind and source are required')
        published = instant(published_at)
        received = instant(received_at or datetime.now(UTC))
        if published > received:
            raise ValueError('Evidence cannot be published after its receipt time')
        body = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
        with self.connection() as db:
            return db.execute('INSERT INTO prop_evidence(event_id,kind,source,published_at,received_at,payload,sha256) VALUES (?,?,?,?,?,?,?)',
                (event_id, kind, source, published, received, body, digest)).lastrowid

    def as_of(self, event_id, cutoff):
        cutoff = instant(cutoff)
        with self.connection() as db:
            rows = db.execute('SELECT * FROM prop_evidence WHERE event_id=? AND published_at<? AND received_at<? ORDER BY received_at,id',
                              (event_id, cutoff, cutoff)).fetchall()
        return [dict(row) | {'payload': json.loads(row['payload'])} for row in rows]
