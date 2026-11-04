from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
import unittest

from ano.jobs import JobStore
from ano.work_ledger import WorkLedger
from ano.tools import Toolbox, ToolError
from ano.plugins.artifacts import Artifacts
from ano.plugins.workshop import Workshop


class WorkLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = JobStore(self.root / 'jobs.db')
        project = self.store.create_project('Synthetic receipt project')
        self.job = self.store.enqueue(project, 'Create fixture output')
        self.store.claim_next()
        self.ledger = WorkLedger(self.store, lambda: self.job)

    def test_real_file_receipt_is_durable_but_not_verified_task(self):
        plugin = Artifacts(Workshop(self.root / 'outputs', None))
        toolbox = Toolbox(lambda: [self.root], plugins=[plugin], lifecycle=self.ledger)
        result = toolbox.run('create_pdf', {'name': 'Fixture', 'title': 'Synthetic fixture', 'text': 'Exact fixture text.'})
        self.assertFalse(result[2])
        self.assertTrue((self.root / 'outputs/Fixture.pdf').is_file())
        receipts = self.store.steps(self.job)
        self.assertEqual([(s['tool'], s['state'], s['side_effect']) for s in receipts], [('create_pdf', 'returned', 1)])
        self.assertNotIn('Exact fixture text', str(receipts))
        self.assertEqual(self.store.get(self.job)['state'], 'running')

    def test_checkpoint_failure_prevents_tool_dispatch(self):
        ledger = Mock()
        ledger.before.side_effect = OSError('Disk full')
        toolbox = Toolbox(lambda: [self.root], lifecycle=ledger)
        toolbox._run = Mock()
        result = toolbox.run('open_file', {'path': 'ignored'})
        self.assertTrue(result[2])
        self.assertIn('No tool was dispatched', result[0])
        toolbox._run.assert_not_called()

    def test_failed_or_interrupted_unknown_write_is_uncertain_and_not_replayed(self):
        for name in ('create_pdf', 'read_unknown_future_plugin'):
            receipt = self.ledger.before(name)
            self.ledger.after(receipt, True)
        self.assertTrue(all(s['state'] == 'uncertain' for s in self.store.steps(self.job)))
        pending = self.ledger.before('open_file')
        self.store.recover_interrupted()
        self.assertEqual(self.store.get(self.job)['state'], 'paused')
        self.assertTrue(all(s['state'] == 'uncertain' for s in self.store.steps(self.job)))
        self.assertIsNone(self.store.claim_next())

    def test_failed_read_does_not_imply_unknown_external_write(self):
        receipt = self.ledger.before('read_file')
        self.ledger.after(receipt, True)
        self.assertEqual(self.store.steps(self.job)[0]['state'], 'failed')
        self.assertEqual(self.store.steps(self.job)[0]['side_effect'], 0)

    def test_receipt_save_failure_does_not_repeat_dispatched_tool(self):
        ledger = Mock()
        ledger.before.return_value = ('id', True)
        ledger.after.side_effect = OSError('Synthetic ledger failure')
        toolbox = Toolbox(lambda: [self.root], lifecycle=ledger)
        toolbox._run = Mock(return_value=('A receipt from the tool', 'Action dispatched', False))
        result = toolbox.run('open_file', {'path': 'fixture.pdf'})
        toolbox._run.assert_called_once()
        self.assertIn('do not repeat uncertain actions', result[0])
