from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from ano.action_log import ActionLog
from ano.tools import Toolbox


class ActionLogTests(unittest.TestCase):
    def test_records_outcome_without_arguments_or_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            log = ActionLog(Path(directory) / 'actions.db')
            tools = Toolbox(lambda: [], audit=log.record)
            tools.run('read_file', {'path': 'PRIVATE_DOCUMENT_NAME'})
            text, _ = log.run('recent_tool_activity', {})
            self.assertIn('read_file', text)
            self.assertIn('FAILED', text)
            self.assertNotIn('PRIVATE_DOCUMENT_NAME', text)
            log.record('bad identifier PRIVATE', False, -100)
            with closing(sqlite3.connect(log.path)) as db:
                row = db.execute('SELECT tool,milliseconds FROM actions ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual(row, ('invalid_tool_name', 0))

    def test_audit_failure_does_not_replay_action(self):
        plugin = Mock()
        plugin.definitions.return_value = [{'name': 'example'}]
        plugin.run.return_value = ('Done', 'note')
        audit = Mock(side_effect=OSError('Disk full'))
        result = Toolbox(lambda: [], plugins=[plugin], audit=audit).run('example', {})
        self.assertEqual(result, ('Done', 'note', False))
        plugin.run.assert_called_once()
        audit.assert_called_once()

    def test_paused_action_is_recorded_as_failed_without_running(self):
        audit = Mock()
        tools = Toolbox(lambda: [], allowed=lambda: False, audit=audit)
        self.assertTrue(tools.run('open_app', {'name': 'Word'})[2])
        self.assertEqual(audit.call_args.args[:2], ('open_app', True))
