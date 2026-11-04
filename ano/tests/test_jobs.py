from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
import unittest
from ano.jobs import JobStore, JobStateError


class JobStoreTests(unittest.TestCase):
    def test_safe_resume_replays_only_reads_and_keeps_original_request(self):
        identifier = self.store.enqueue(self.project, 'Read public fixture', 3, 17)
        self.store.claim_next()
        self.store.start_step(identifier, 'read_file', side_effect=False)
        self.store.recover_interrupted()
        self.assertTrue(self.store.recovery_options(identifier)['can_resume'])
        self.store.resume_without_changes(identifier)
        resumed = self.store.claim_next()
        self.assertEqual(resumed['request_message_id'], 17)
        self.assertNotIn('requires_reinspection', resumed['checkpoint'])
        self.assertEqual(self.store.steps(identifier)[0]['state'], 'failed')
        self.assertEqual(resumed['attempts'], 2)

    def test_resume_blocks_every_prior_changing_dispatch_and_lost_context(self):
        for result in ('returned', 'failed', 'uncertain', 'verified'):
            identifier = self.store.enqueue(self.project, 'Possible write')
            self.store.claim_next()
            step = self.store.start_step(identifier, 'write_document', side_effect=True)
            self.store.finish_step(step, result)
            self.store.pause(identifier)
            self.assertFalse(self.store.recovery_options(identifier)['can_resume'])
            with self.assertRaises(JobStateError):
                self.store.resume_without_changes(identifier)
            self.assertEqual(self.store.get(identifier)['state'], 'paused')
        identifier = self.store.enqueue(self.project, 'Read screen', requires_context=True)
        self.store.recover_interrupted(include_queued=True)
        with self.assertRaisesRegex(JobStateError, 'fresh screen'):
            self.store.resume_without_changes(identifier)

    def test_unknown_dispatch_and_legacy_context_cannot_resume(self):
        identifier = self.store.enqueue(self.project, 'Unknown action')
        self.store.claim_next()
        self.store.start_step(identifier, 'unknown_action', side_effect=False)
        self.store.pause(identifier)
        with self.assertRaises(JobStateError):
            self.store.resume_without_changes(identifier)
        identifier = self.store.enqueue(self.project, 'Legacy context')
        active = self.store.claim_next()
        self.store.save_checkpoint(identifier, {}, active['revision'])
        self.store.pause(identifier)
        with self.assertRaisesRegex(JobStateError, 'predates'):
            self.store.resume_without_changes(identifier)

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'jobs.sqlite3'
        self.store = JobStore(self.path)
        self.project = self.store.create_project('Synthetic project')

    def test_concurrent_workers_claim_each_job_once_across_projects(self):
        other = self.store.create_project('Other project')
        ids = [self.store.enqueue(self.project if i % 2 else other, f'Task {i}') for i in range(12)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            claimed = list(pool.map(lambda _: self.store.claim_next()['id'], range(12)))
        self.assertEqual(set(claimed), set(ids))
        self.assertEqual(len(set(claimed)), 12)
        self.assertIsNone(self.store.claim_next())

    def test_interrupted_write_never_replays_and_prevents_false_success(self):
        identifier = self.store.enqueue(self.project, 'Write a fixture file', chat_id=3)
        self.store.claim_next()
        step = self.store.start_step(identifier, 'write_document', side_effect=True)
        restarted = JobStore(self.path)
        self.assertEqual(restarted.recover_interrupted(), 1)
        self.assertEqual(restarted.get(identifier)['state'], 'paused')
        self.assertIsNone(restarted.claim_next())
        restarted.resume(identifier)
        self.assertTrue(restarted.claim_next()['checkpoint']['requires_reinspection'])
        with self.assertRaises(JobStateError):
            restarted.finish(identifier, 'verified', {'passed': True, 'references': ['fixture read-back']})
        restarted.resolve_uncertain(step, 'verified', {'inspected': True, 'reference': 'Existing file read-back matched'})
        restarted.finish(identifier, 'verified', {'passed': True, 'references': ['Existing file read-back matched']})

    def test_competing_checkpoints_cannot_overwrite_one_another(self):
        identifier = self.store.enqueue(self.project, 'Checkpoint test')
        active = self.store.claim_next()
        def update(value):
            try:
                self.store.save_checkpoint(identifier, {'value': value}, active['revision'])
                return True
            except JobStateError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, (1, 2)))
        self.assertEqual(sum(results), 1)

    def test_cancellation_invalidates_stale_checkpoints_and_late_success(self):
        identifier = self.store.enqueue(self.project, 'Example job')
        active = self.store.claim_next()
        self.assertEqual(self.store.request_cancel(identifier), 'cancelling')
        with self.assertRaises(JobStateError):
            self.store.save_checkpoint(identifier, {'step': 1}, active['revision'])
        with self.assertRaises(JobStateError):
            self.store.finish(identifier, 'answered')
        self.store.finish(identifier, 'cancelled')
        self.assertEqual(self.store.get(identifier)['state'], 'cancelled')

    def test_queued_cancel_never_runs_and_delivered_reply_is_not_verified_action(self):
        cancelled = self.store.enqueue(self.project, 'Cancel this')
        self.assertEqual(self.store.request_cancel(cancelled), 'cancelled')
        identifier = self.store.enqueue(self.project, 'Answer a question')
        self.assertEqual(self.store.claim_next()['id'], identifier)
        self.store.finish(identifier, 'answered')
        self.assertEqual(self.store.get(identifier)['state'], 'answered')

    def test_verified_completion_requires_evidence_and_resolved_steps(self):
        identifier = self.store.enqueue(self.project, 'Save a fixture')
        self.store.claim_next()
        for evidence in (None, {}, {'passed': 'true', 'references': ['x']}, {'passed': True, 'references': []}):
            with self.assertRaises(ValueError):
                self.store.finish(identifier, 'verified', evidence)
        step = self.store.start_step(identifier, 'write_document', side_effect=True)
        self.store.finish_step(step, 'verified')
        self.store.finish(identifier, 'verified', {'passed': True, 'references': ['sha256 and read-back']})
        self.assertEqual(self.store.get(identifier)['state'], 'verified')
