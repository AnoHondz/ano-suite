import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from services.prop_evidence_store import EvidenceStore


class EvidenceTests(unittest.TestCase):
    def test_late_received_data_cannot_leak_into_backtest(self):
        with TemporaryDirectory() as folder:
            store = EvidenceStore(Path(folder) / 'evidence.sqlite3')
            store.append('game1', 'odds', 'book A', '2026-10-29T15:00:00Z', {'line': 20.5}, received_at='2026-10-29T15:01:00Z')
            store.append('game1', 'injury', 'official', '2026-10-29T14:00:00Z', {'status': 'out'}, received_at='2026-10-29T16:01:00Z')
            store.append('game2', 'odds', 'book A', '2026-10-29T15:00:00Z', {'line': 10.5}, received_at='2026-10-29T15:01:00Z')
            rows = store.as_of('game1', '2026-10-29T16:00:00Z')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['payload']['line'], 20.5)
            with store.connection() as db:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute('UPDATE prop_evidence SET source=?', ('changed',))
            # A new process/store instance sees the same original snapshot.
            self.assertEqual(EvidenceStore(store.path).as_of('game1', '2026-10-29T16:00:00Z'), rows)

    def test_future_and_timezone_ambiguous_evidence_is_rejected(self):
        with TemporaryDirectory() as folder:
            store = EvidenceStore(Path(folder) / 'evidence.sqlite3')
            for published in ('2026-10-29T17:00:00Z', '2026-10-29T15:00:00'):
                with self.assertRaises(ValueError):
                    store.append('g', 'lineup', 'source', published, {}, received_at='2026-10-29T16:00:00Z')
