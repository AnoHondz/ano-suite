from pathlib import Path
import hashlib
import os
import tempfile
from unittest import TestCase
from unittest.mock import patch

from ano.memory import Memory
from ano.paths import data_directory
from tools.migrate_state import migrate


class StatePathsTests(TestCase):
    def test_adopts_shared_state_only_after_completed_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / 'shared'
            with patch('ano.paths.shared_state_directory', return_value=shared), patch.dict(os.environ, {'ANO_DATA_DIR': '', 'LOCALAPPDATA': str(root)}):
                self.assertEqual(data_directory(), root / 'Ano' / 'Ano')
                shared.mkdir()
                (shared / 'memory.sqlite3').touch()
                self.assertEqual(data_directory(), root / 'Ano' / 'Ano')
                (shared / 'migration-complete.json').write_text('{}')
                self.assertEqual(data_directory(), shared)
                with patch.dict(os.environ, {'ANO_DATA_DIR': str(root / 'test-override')}):
                    self.assertEqual(data_directory(), root / 'test-override')

    def test_preserves_both_sources_and_separates_colliding_chat_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary, secondary, target = root / 'primary', root / 'secondary', root / 'shared'
            for source, text in ((primary, 'Earlier history'), (secondary, 'Recent native history')):
                memory = Memory(source / 'memory.sqlite3')
                memory.add('user', text)
                memory.close()
            before = {p: hashlib.sha256((p / 'memory.sqlite3').read_bytes()).hexdigest()
                      for p in (primary, secondary)}
            result = migrate(primary, secondary, target)
            self.assertEqual(result['total_messages'], 2)
            merged = Memory(target / 'memory.sqlite3')
            self.assertEqual(merged.db.execute('SELECT count(DISTINCT chat_id) FROM messages').fetchone()[0], 2)
            self.assertEqual(merged.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            merged.close()
            self.assertTrue((target / 'migration-complete.json').is_file())
            self.assertTrue((target / 'migration-archives' / 'secondary-memory.sqlite3').is_file())
            for p, digest in before.items():
                self.assertEqual(hashlib.sha256((p / 'memory.sqlite3').read_bytes()).hexdigest(), digest)
            with self.assertRaises(FileExistsError):
                migrate(primary, secondary, target)
