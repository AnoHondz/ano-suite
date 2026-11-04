import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ano.memory import Memory
from tools.recover_memory import recover


class RecoveryTests(unittest.TestCase):
    def test_preserves_records_and_rebuilds_search(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.sqlite3'
            destination = Path(directory) / 'recovered.sqlite3'
            memory = Memory(source)
            memory.add('user', 'Recovery searchable example')
            memory.set_setting('local_only', True)
            memory.db.execute('DELETE FROM chats')
            memory.db.commit()
            memory.close()
            report = recover(source, destination)
            recovered = Memory(destination)
            self.assertEqual(report['messages']['recovered'], 1)
            self.assertTrue(recovered.setting('local_only', False))
            self.assertEqual(len(recovered.search('searchable')), 1)
            self.assertEqual(len(recovered.recent()), 1)
            recovered.close()
            with closing(sqlite3.connect(source)) as original:
                self.assertEqual(original.execute('SELECT count(*) FROM chats').fetchone()[0], 0)
            with self.assertRaises(ValueError):
                recover(source, destination)
