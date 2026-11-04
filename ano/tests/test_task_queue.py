import tempfile
import time
import unittest
from pathlib import Path
from threading import Event

from PySide6.QtCore import QCoreApplication
from ano.app import Controller
from ano.jobs import JobStore
from ano.memory import Memory


class QueueTests(unittest.TestCase):
    def test_task_progress_property_exposes_backend_snapshot(self):
        from types import SimpleNamespace
        controller = Controller(self.memory, None, self.jobs)
        self.assertEqual(controller.taskProgress, {})
        snapshot = dict(goal='Public fixture', step=1, total=2, instruction='Inspect result', state='running')
        controller.browser_tasks = SimpleNamespace(progress_snapshot=lambda:dict(snapshot))
        self.assertEqual(controller.taskProgress, snapshot)
        self.assertGreaterEqual(controller.metaObject().indexOfProperty('taskProgress'), 0)

    def test_partial_text_is_not_saved_and_stays_attached_to_original_chat(self):
        ready = Event()
        class Provider:
            supports_partial = True
            def reply(self, history, partial):
                partial('Unfinished preview')
                ready.wait(2)
                return 'Final unchanged answer'
        controller = Controller(self.memory, Provider(), self.jobs)
        seen = []
        controller.partialReply.connect(lambda chat, text: seen.append((chat, text)))
        origin = controller.currentChat
        controller.send('Synthetic streaming question')
        controller.newChat()
        ready.set()
        self.drain(controller)
        self.assertIn((origin, 'Unfinished preview'), seen)
        self.assertIn((origin, ''), seen)
        self.assertNotIn('Unfinished preview', str(self.memory.recent(chat=origin)))
        self.assertEqual(self.memory.recent(chat=origin)[-1]['body'], 'Final unchanged answer')

    def test_partial_text_rejected_after_cancel_or_for_another_request(self):
        controller = Controller(self.memory, None, self.jobs)
        seen = []
        controller.partialReply.connect(lambda chat, text: seen.append(text))
        controller._busy = True
        controller._reply_chat, controller._reply_request = 1, 7
        controller._receive_partial('stale', 1, 6)
        controller._active_cancelled = True
        controller._receive_partial('late', 1, 7)
        self.assertEqual(seen, [])
        controller.togglePause()
        self.assertEqual(seen, [''])
    def test_resumed_unstarted_task_returns_to_original_chat(self):
        origin = self.memory.latest_chat()
        request = self.memory.add('user', 'Read synthetic fixture', origin)
        identifier = self.jobs.enqueue(self.jobs.project_for_chat(origin), 'Read synthetic fixture', origin, request)
        self.jobs.recover_interrupted(include_queued=True)
        class Provider:
            def reply(self, history):
                self.history = history
                return 'Recovered fixture answer'
        provider = Provider()
        controller = Controller(self.memory, provider, self.jobs)
        controller.newChat()
        other = controller.currentChat
        controller.resumeTask(identifier)
        self.drain(controller)
        self.assertEqual(self.jobs.get(identifier)['state'], 'answered')
        self.assertEqual(self.memory.recent(chat=origin)[-1]['body'], 'Recovered fixture answer')
        self.assertEqual(self.memory.recent(chat=other), [])
        self.assertEqual(provider.history[-1]['source_message_id'], request)

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.memory = Memory(Path(self.tmp.name) / 'memory.db')
        self.jobs = JobStore(Path(self.tmp.name) / 'jobs.db')

    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    def drain(self, controller):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not controller.busy and not controller.queuedCount:
                break
            time.sleep(.005)
        self.assertFalse(controller.busy)
        self.assertEqual(controller.queuedCount, 0)
        controller.pool.waitForDone()

    def test_queued_commands_have_correct_history_and_are_not_lost(self):
        ready = Event()
        histories = []
        class Provider:
            def reply(self, history):
                histories.append([x['body'] for x in history])
                ready.wait(2)
                return 'Reply to ' + history[-1]['body']
        controller = Controller(self.memory, Provider(), self.jobs)
        controller.send('First')
        controller.send('Second')
        controller.send('Second')  # accidental duplicate while in flight
        controller.send('Third')
        self.assertEqual(controller.queuedCount, 2)
        ready.set()
        self.drain(controller)
        self.assertEqual(histories, [['First'], ['First', 'Reply to First', 'Second'],
                                    ['First', 'Reply to First', 'Second', 'Reply to Second', 'Third']])
        self.assertEqual([j['state'] for j in self.jobs.list_jobs()], ['answered'] * 3)

    def test_stop_blocks_late_actions_and_queue_remains_separate(self):
        ready = Event()
        permitted = []
        class Provider:
            def reply(self, history):
                ready.wait(2)
                permitted.append(controller.work_allowed)
                return 'Late model claim'
        controller = Controller(self.memory, Provider(), self.jobs)
        controller.send('Synthetic write')
        controller.stopTask()
        self.assertFalse(controller.work_allowed)
        ready.set()
        self.drain(controller)
        self.assertEqual(permitted, [False])
        self.assertEqual(self.jobs.list_jobs()[0]['state'], 'cancelled')
        self.assertNotIn('Late model claim', str(self.memory.recent()))

    def test_cross_chat_reply_and_cancelled_queue(self):
        ready = Event()
        class Provider:
            def reply(self, history):
                ready.wait(2)
                return 'Origin reply'
        controller = Controller(self.memory, Provider(), self.jobs)
        origin = controller.currentChat
        controller.send('Original command')
        controller.newChat()
        controller.send('Queued in other chat')
        queued = self.jobs.list_jobs()[-1]
        controller.cancelTask(queued['id'])
        ready.set()
        self.drain(controller)
        self.assertEqual(self.memory.recent(chat=origin)[-1]['body'], 'Origin reply')
        self.assertEqual(len(self.memory.recent(chat=controller.currentChat)), 1)
        self.assertEqual(self.jobs.get(queued['id'])['state'], 'cancelled')

    def test_recovered_queue_is_paused_without_execution(self):
        project = self.jobs.project_for_chat(self.memory.latest_chat())
        request = self.memory.add('user', 'Do not repeat a dispatched write')
        identifier = self.jobs.enqueue(project, 'Do not repeat a dispatched write', self.memory.latest_chat(), request)
        restarted = JobStore(self.jobs.path)
        self.assertEqual(restarted.recover_interrupted(include_queued=True), 1)
        self.assertEqual(restarted.get(identifier)['state'], 'paused')
        self.assertIsNone(restarted.claim_next())


if __name__ == '__main__':
    unittest.main()
