from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QCoreApplication
from ano.app import Controller
from ano.memory import Memory
from ano.voice import VoiceService
from ano.speech import SpeechService
from ano.voice_conversation import VoiceConversation


class TurnRecognizer:
    def __init__(self):
        self.calls = 0
    def available(self):
        return True
    def recognize_turn(self, stop, cancel, listening):
        self.calls += 1
        listening()
        if self.calls == 1:
            return 'Synthetic voice question'
        cancel.wait(3)
        return 'stale cancelled text'


class Speaker:
    def __init__(self):
        self.outputs = []
        self.playing = False
        self.error = ''
    def available(self):
        return True
    def speak(self, text):
        self.outputs.append(text)
        self.playing = True
    def speaking(self):
        return self.playing
    def stop(self):
        self.playing = False


class VoiceConversationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.memory = Memory(Path(self.tmp.name) / 'memory.db')
        self.controller = Controller(self.memory, SimpleNamespace(mode='local', reply=lambda *args, **kwargs: 'Synthetic voice answer'))
        self.recognizer, self.speaker = TurnRecognizer(), Speaker()
        self.voice = VoiceService(self.recognizer, lambda: not self.controller.busy and not self.controller.paused)
        self.speech = SpeechService(self.memory, self.speaker, lambda: not self.voice.busy and not self.controller.paused)
        self.controller.voice = self.voice
        self.controller.speech = self.speech
        self.voice.starting.connect(self.speech.stop)
        self.session = VoiceConversation(self.voice, self.speech, self.controller, lambda: not self.controller.paused)
        self.controller.voice_conversation = self.session

    def tearDown(self):
        self.session.stop()
        self.voice.close()
        self.speech.close()
        self.controller.pool.waitForDone()
        self.app.processEvents()
        self.memory.close()
        self.tmp.cleanup()

    def wait_for(self, predicate):
        end = time.monotonic() + 3
        while time.monotonic() < end and not predicate():
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_hands_free_turn_waits_for_playback_before_listening_again(self):
        self.session.start()
        self.wait_for(lambda: self.speech.speaking)
        self.assertEqual(self.speaker.outputs, ['Synthetic voice answer'])
        self.assertEqual(self.recognizer.calls, 1)
        self.assertFalse(self.voice.busy)
        self.assertFalse(self.speech.enabled)  # session did not persist speech opt-in
        self.speaker.playing = False
        self.speech._poll()
        self.wait_for(lambda: self.recognizer.calls == 2)
        self.session.stop()
        self.wait_for(lambda: not self.voice.busy)
        self.assertNotIn('stale cancelled text', str(self.memory.recent()))

    def test_pause_cancels_session_and_prevents_microphone_restart(self):
        self.session.start()
        self.wait_for(lambda: self.speech.speaking)
        self.controller.togglePause()
        self.assertFalse(self.session.active)
        self.assertFalse(self.speech.session_enabled)
        self.assertFalse(self.speech.speaking)
        self.assertFalse(self.voice.busy)

    def test_start_does_not_enable_paid_or_unavailable_voice(self):
        self.controller.provider.mode = 'openai'
        self.session.start()
        self.assertFalse(self.session.active)
        self.assertEqual(self.recognizer.calls, 0)
        self.assertIn('local brain', self.controller.status)
