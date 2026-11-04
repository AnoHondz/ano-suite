from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from threading import Event
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication
from ano.memory import Memory
from ano.speech import SapiSpeaker, SpeechService
from ano.voice import VoiceService, OfflineRecognizer, VoiceError


class FakeRecognizer:
    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def recognize(self, stop, cancel, listening):
        self.calls += 1
        listening()
        stop.wait(2)
        # Deliberately return stale text even on cancellation to test the UI guard.
        return 'what was I working on earlier'


class MicrophoneBackendTests(unittest.TestCase):
    def test_voice_turn_closes_on_recognizer_endpoint_without_button_release(self):
        stop, cancel = Event(), Event()
        recognizer = Mock()
        recognizer.AcceptWaveform.return_value = True
        recognizer.Result.return_value = '{"text":"synthetic complete utterance"}'
        recognizer.FinalResult.return_value = '{"text":""}'
        closed = []
        class Stream:
            def __init__(self, **kwargs):
                self.callback = kwargs['callback']
            def __enter__(self):
                self.callback(b'fixture audio', 100, None, False)
                return self
            def __exit__(self, *args):
                closed.append(True)
        sd = SimpleNamespace(query_devices=lambda **_: {'default_samplerate': 16000}, RawInputStream=Stream)
        vosk = SimpleNamespace(Model=Mock(), KaldiRecognizer=Mock(return_value=recognizer), SetLogLevel=Mock())
        backend = OfflineRecognizer('unused')
        backend._model = object()
        with patch.dict('sys.modules', sounddevice=sd, vosk=vosk):
            text = backend.recognize_turn(stop, cancel, lambda: None)
        self.assertEqual(text, 'synthetic complete utterance')
        self.assertFalse(stop.is_set())
        self.assertEqual(closed, [True])

    def test_release_closes_stream_and_transcribes_queued_audio(self):
        stop, cancel = Event(), Event()
        recognizer = Mock()
        recognizer.AcceptWaveform.return_value = False
        recognizer.FinalResult.return_value = '{"text":"example draft"}'
        closed = []
        class Stream:
            def __init__(self, **kwargs):
                self.callback = kwargs['callback']
            def __enter__(self):
                self.callback(b'pcm audio', 100, None, False)
                stop.set()
                return self
            def __exit__(self, *args):
                closed.append(True)
        sd = SimpleNamespace(query_devices=lambda **_: {'default_samplerate': 16000}, RawInputStream=Stream)
        vosk = SimpleNamespace(Model=Mock(), KaldiRecognizer=Mock(return_value=recognizer), SetLogLevel=Mock())
        backend = OfflineRecognizer('unused')
        backend._model = object()
        with patch.dict('sys.modules', sounddevice=sd, vosk=vosk):
            text = backend.recognize(stop, cancel, lambda: None)
        self.assertEqual(text, 'example draft')
        recognizer.AcceptWaveform.assert_called_once_with(b'pcm audio')
        self.assertEqual(closed, [True])

    def test_release_during_model_load_never_opens_microphone(self):
        stop, cancel = Event(), Event()
        sd = Mock()
        def load(path):
            stop.set()
            return object()
        vosk = SimpleNamespace(Model=load, KaldiRecognizer=Mock(), SetLogLevel=Mock())
        with patch.dict('sys.modules', sounddevice=sd, vosk=vosk):
            text = OfflineRecognizer('unused').recognize(stop, cancel, lambda: None)
        self.assertEqual(text, '')
        sd.RawInputStream.assert_not_called()


class VoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.allowed = True
        self.backend = FakeRecognizer()
        self.voice = VoiceService(self.backend, lambda: self.allowed)
        self.transcripts = []
        self.voice.transcript.connect(self.transcripts.append)

    def tearDown(self):
        self.voice.close()

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_press_release_produces_draft_only_after_release(self):
        self.voice.start()
        self.wait(lambda: self.voice.recording)
        self.assertEqual(self.transcripts, [])
        self.voice.start()
        self.assertEqual(self.backend.calls, 1)
        self.voice.stop()
        self.wait(lambda: not self.voice.busy)
        self.assertEqual(self.transcripts, ['what was I working on earlier'])

    def test_pause_lock_or_hide_cancels_even_if_backend_returns_text(self):
        self.voice.start()
        self.wait(lambda: self.voice.recording)
        self.voice.cancel()
        self.wait(lambda: not self.voice.busy)
        self.assertEqual(self.transcripts, [])
        self.assertIn('cancelled', self.voice.status)

    def test_blocked_state_prevents_microphone_and_discards_late_result(self):
        self.allowed = False
        self.voice.start()
        self.assertFalse(self.voice.busy)
        self.assertEqual(self.backend.calls, 0)
        self.allowed = True
        self.voice.start()
        self.wait(lambda: self.voice.recording)
        self.allowed = False
        self.voice.stop()
        self.wait(lambda: not self.voice.busy)
        self.assertEqual(self.transcripts, [])

    def test_unavailable_model_never_opens_microphone(self):
        backend = Mock()
        backend.available.return_value = False
        voice = VoiceService(backend, lambda: True)
        voice.start()
        backend.recognize.assert_not_called()
        self.assertIn('setup needed', voice.status)
        voice.close()

    def test_backend_error_is_safe_and_resets_busy(self):
        backend = Mock()
        backend.available.return_value = True
        backend.recognize.side_effect = RuntimeError('private device diagnostic')
        voice = VoiceService(backend, lambda: True)
        voice.start()
        self.wait(lambda: not voice.busy)
        self.assertNotIn('private', voice.status)
        self.assertIn('microphone', voice.status)
        voice.close()


class SpeechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.memory = Memory(Path(self.directory.name) / 'memory.sqlite3')
        self.backend = Mock()
        self.backend.available.return_value = True
        self.allowed = True
        self.speech = SpeechService(self.memory, self.backend, lambda: self.allowed)

    def tearDown(self):
        self.speech.close()
        self.memory.close()
        self.directory.cleanup()

    def test_off_by_default_opt_in_persists_and_output_is_bounded(self):
        self.speech.speak('Hello')
        self.backend.speak.assert_not_called()
        self.speech.setEnabled(True)
        self.speech.speak('x' * 5000)
        self.assertEqual(len(self.backend.speak.call_args.args[0]), 4000)
        self.assertTrue(self.memory.setting('spoken_replies', False))
        self.speech.setEnabled(False)
        self.assertFalse(self.speech.speaking)
        self.backend.stop.assert_called()

    def test_pause_or_lock_blocks_and_stops_speech(self):
        self.speech.setEnabled(True)
        self.allowed = False
        self.speech.speak('Do not speak')
        self.backend.speak.assert_not_called()
        self.allowed = True
        self.speech.speak('Hello')
        self.assertTrue(self.speech.speaking)
        self.allowed = False
        self.speech._poll()
        self.assertFalse(self.speech.speaking)

    def test_sapi_flags_force_plain_text_async_and_purge(self):
        backend = SapiSpeaker()
        backend.voice = Mock()
        backend.speak('<not executable markup>')
        backend.voice.Speak.assert_called_once_with('<not executable markup>', 19)
        backend.stop()
        backend.voice.Speak.assert_called_with('', 19)
