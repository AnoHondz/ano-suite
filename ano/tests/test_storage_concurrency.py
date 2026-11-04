from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
from ano.memory import Memory
from ano.plugins.notes import Notebook


class StorageConcurrencyTests(unittest.TestCase):
    def test_concurrent_notes_and_chat_keep_integrity_and_all_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'memory.sqlite3'
            memory = Memory(path)
            notebook = Notebook(path)
            self.assertEqual(memory.db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')

            def add_notes(worker):
                for n in range(30):
                    notebook.add_note(f'Worker {worker} note {n}')

            with ThreadPoolExecutor(max_workers=3) as pool:
                jobs = [pool.submit(add_notes, worker) for worker in range(3)]
                for n in range(30):
                    memory.add('user', f'Message {n}')
                for job in jobs:
                    job.result()
            self.assertEqual(memory.db.execute('SELECT count(*) FROM notes').fetchone()[0], 90)
            self.assertEqual(memory.db.execute('SELECT count(*) FROM messages').fetchone()[0], 30)
            self.assertEqual(memory.db.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
            memory.close()

    def test_timed_out_backup_publishes_nothing_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = Memory(root / 'memory.sqlite3')
            memory.add('user', 'Keep source intact')
            times = iter((0, 10))
            destination = root / 'backups' / 'snapshot.sqlite3'
            with self.assertRaises(TimeoutError):
                memory.backup_to(destination, max_seconds=5, clock=lambda: next(times))
            self.assertFalse(destination.exists())
            self.assertFalse(list(destination.parent.glob('*.partial')))
            self.assertEqual(memory.recent()[0]['body'], 'Keep source intact')
            self.assertEqual(memory.db.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
            memory.close()

    def test_backup_is_verified_and_does_not_replace_prior_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = Memory(root / 'memory.sqlite3')
            memory.add('user', 'Preserve this record')
            backup = memory.backup_to(root / 'backups' / 'snapshot.sqlite3')
            memory.add('user', 'Newer record')
            with self.assertRaises(FileExistsError):
                memory.backup_to(backup)
            restored = Memory(backup)
            self.assertEqual(len(restored.recent()), 1)
            self.assertEqual(restored.recent()[0]['body'], 'Preserve this record')
            restored.close()
            self.assertFalse(list(backup.parent.glob('*.partial')))
            memory.close()
