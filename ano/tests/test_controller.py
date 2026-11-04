import tempfile
import time
import unittest
from pathlib import Path
from threading import Event

from PySide6.QtCore import QCoreApplication
from ano.app import Controller
from ano.memory import Memory


class ControllerTests(unittest.TestCase):
    def test_failed_command_has_a_persistent_failure_reply_in_original_chat(self):
        controller = Controller(self.memory)
        original = controller.currentChat
        self.memory.add('user', 'Synthetic command', original)
        controller._reply_chat = original
        controller.newChat()
        controller._busy = True
        controller._receive('', 'Local model server failed; inspect earlier actions before retrying.')
        self.assertFalse(controller.busy)
        messages = self.memory.recent(chat=original)
        self.assertEqual(messages[-1]['role'], 'assistant')
        self.assertIn('without verified completion', messages[-1]['body'])
        self.assertIn('inspect earlier actions', messages[-1]['body'])
        self.assertEqual(self.memory.recent(chat=controller.currentChat), [])

    def test_explicit_continue_button_restores_control_and_requests_saved_progress(self):
        from unittest.mock import Mock
        from types import SimpleNamespace
        controller = Controller(self.memory)
        controller.browser_tasks = SimpleNamespace(task={'id':'saved-task','status':'blocked','resumable':True})
        controller.login_vault = Mock(hold=True, private=False)
        controller.desktop_control = Mock()
        controller.desktop_control.permitted.return_value = True
        controller.send = Mock()
        controller.continueWebsiteTask()
        controller.login_vault.done.assert_called_once()
        controller.desktop_control.startSession.assert_called_once_with('full')
        self.assertIn('saved-task', controller.send.call_args.args[0])
        self.assertIn('verify authentication', controller.send.call_args.args[0])

    def test_continue_button_cannot_resume_an_uncertain_submission(self):
        from unittest.mock import Mock
        from types import SimpleNamespace
        controller = Controller(self.memory)
        controller.browser_tasks = SimpleNamespace(task={'id':'saved-task','status':'blocked','pending_dispatch':True})
        controller.login_vault = Mock(hold=True, private=False)
        controller.desktop_control = Mock()
        self.assertFalse(controller.websiteTaskPending)
        controller.continueWebsiteTask()
        controller.login_vault.done.assert_not_called()
        controller.desktop_control.startSession.assert_not_called()
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.memory = Memory(Path(self.directory.name) / 'test.sqlite3')

    def tearDown(self):
        self.memory.close()
        self.directory.cleanup()

    def wait(self, controller):
        deadline = time.monotonic() + 3
        while controller.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertFalse(controller.busy)
        controller.pool.waitForDone()

    def test_async_reply_persists_and_duplicate_send_is_blocked(self):
        ready = Event()
        class Provider:
            def reply(self, history):
                ready.wait(2)
                return 'Hello from Claude'
        controller = Controller(self.memory, Provider())
        controller.send('Hello')
        self.assertTrue(controller.busy)
        controller.send('Duplicate')
        ready.set()
        self.wait(controller)
        self.assertEqual([m['role'] for m in self.memory.recent()], ['user', 'assistant'])

    def test_orb_clarification_cue_follows_explicit_reply_and_resets(self):
        controller = Controller(self.memory)
        controller._reply_chat = controller.currentChat
        controller._receive('Could you clarify which document you mean?', '')
        self.assertTrue(controller.needsClarification)
        controller.send('The notes document')
        self.assertFalse(controller.needsClarification)

    def test_orb_does_not_mark_normal_question_as_confusion(self):
        controller = Controller(self.memory)
        controller._reply_chat = controller.currentChat
        controller._receive('The export is ready. Would you like a copy?', '')
        self.assertFalse(controller.needsClarification)

    def test_orb_ignores_clarification_from_another_chat(self):
        controller = Controller(self.memory)
        controller._reply_chat = controller.currentChat
        self.memory.add('user', 'Old conversation', controller.currentChat)
        controller.newChat()
        controller._receive('Could you clarify what you mean?', '')
        self.assertFalse(controller.needsClarification)

    def test_chats_are_separate_and_replies_return_to_their_chat(self):
        ready = Event()
        seen = []
        class Provider:
            def reply(self, history):
                seen.append([m['body'] for m in history])
                ready.wait(2)
                return 'Answer for the first chat'
        controller = Controller(self.memory, Provider())
        first = controller.currentChat
        controller.send('Example first question')
        controller.newChat()
        second = controller.currentChat
        self.assertNotEqual(first, second)
        self.assertEqual(controller.messages, [])
        ready.set()
        self.wait(controller)
        self.assertEqual(controller.messages, [])
        self.assertEqual([m['body'] for m in self.memory.recent(chat=first)],
                         ['Example first question', 'Answer for the first chat'])
        self.assertEqual(seen, [['Example first question']])
        titles = {chat['id']: chat['title'] for chat in controller.chats}
        self.assertEqual(titles[first], 'Example first question')
        controller.newChat()
        self.assertEqual(controller.currentChat, second)  # an empty chat is reused
        controller.deleteChat(first)
        self.assertEqual([chat['id'] for chat in controller.chats], [second])

    def test_pause_discards_in_flight_reply_even_after_resume(self):
        ready = Event()
        class Provider:
            def reply(self, history):
                ready.wait(2)
                return 'Discard me'
        controller = Controller(self.memory, Provider())
        controller.send('Hello')
        controller.togglePause()
        controller.togglePause()
        ready.set()
        self.wait(controller)
        self.assertEqual(len(self.memory.recent()), 1)
        self.assertIn('discarded', controller.status)

    def test_approved_screenshot_reaches_worker_but_not_database(self):
        from unittest.mock import Mock
        calls = []
        class Provider:
            def reply(self, history, screenshot=None):
                calls.append(screenshot)
                return 'This is your example screen.'
        controller = Controller(self.memory, Provider())
        controller.screen_capture = Mock()
        controller.screen_capture.consume.return_value = b'private-image-bytes'
        controller.approveScreen('Explain this')
        self.wait(controller)
        self.assertEqual(calls, [b'private-image-bytes'])
        self.assertIn('[Approved screen snapshot]', self.memory.recent()[0]['body'])
        self.assertNotIn('private-image-bytes', str(self.memory.recent()))

    def test_recall_supplies_activity_only_for_recall_questions(self):
        from unittest.mock import Mock
        calls = []
        class Provider:
            def reply(self, history, activity_context=None):
                calls.append(activity_context)
                return 'A response'
        controller = Controller(self.memory, Provider())
        controller.activity = Mock()
        controller.activity.recall.return_value = ('{"records":[]}', 'No recorded activity.')
        controller.send('Hello')
        self.wait(controller)
        controller.activity.recall.assert_not_called()
        controller.recallActivity()
        self.wait(controller)
        self.assertEqual(calls, [None, '{"records":[]}'])

    def test_local_recall_and_pause_propagation(self):
        from unittest.mock import Mock
        controller = Controller(self.memory)
        controller.activity = Mock()
        controller.activity.recall.return_value = ('{}', 'You were editing Example project.')
        controller.recallActivity()
        self.assertIn('Example project', self.memory.recent()[-1]['body'])
        controller.togglePause()
        controller.activity.set_paused.assert_called_with(True)
        controller.recallActivity()
        self.assertEqual(len(self.memory.recent()), 2)

    def test_pause_stops_voice_and_speech_and_voice_blocks_send(self):
        from unittest.mock import Mock
        controller = Controller(self.memory)
        controller.voice = Mock()
        controller.voice.busy = True
        controller.speech = Mock()
        controller.send('Must not send during recording')
        self.assertEqual(self.memory.recent(), [])
        controller.togglePause()
        controller.voice.cancel.assert_called_once()
        controller.speech.stop.assert_called_once()

    def test_successful_reply_is_offered_to_optional_speech(self):
        from unittest.mock import Mock
        controller = Controller(self.memory)
        controller.speech = Mock()
        controller._receive('A spoken reply', '')
        controller.speech.speak.assert_called_once_with('A spoken reply')
        controller._discard = True
        controller._receive('Discarded reply', '')
        controller.speech.speak.assert_called_once()
