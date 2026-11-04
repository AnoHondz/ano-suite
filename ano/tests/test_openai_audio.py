from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ano.openai_audio import CloudRecognizer, OpenAIVoice


class FakeStream:
    """A microphone that delivers one second of silence, then lets the test press stop or cancel."""
    def __init__(self, after_open, **kwargs):
        self.callback = kwargs['callback']
        self.after_open = after_open

    def __enter__(self):
        for _ in range(10):
            self.callback(b'\0\0' * 1600, 1600, None, False)
        self.after_open()
        return self

    def __exit__(self, *args):
        pass


class CloudRecognizerTests(unittest.TestCase):
    def recognize(self, press):
        stop, cancel = Event(), Event()
        api = Mock()
        api.audio.transcriptions.create.return_value = SimpleNamespace(text=' Hey Ano ')
        sd = SimpleNamespace(RawInputStream=lambda **kwargs: FakeStream(lambda: press(stop, cancel), **kwargs))
        recognizer = CloudRecognizer('key', connect=lambda key, timeout: api)
        with patch.dict('sys.modules', sounddevice=sd):
            text = recognizer.recognize(stop, cancel, lambda: None)
        return text, api.audio.transcriptions.create

    def test_release_uploads_one_wav_and_returns_text(self):
        text, upload = self.recognize(lambda stop, cancel: stop.set())
        self.assertEqual(text, 'Hey Ano')
        name, data, kind = upload.call_args.kwargs['file']
        self.assertEqual((name, kind), ('speech.wav', 'audio/wav'))
        self.assertTrue(data.startswith(b'RIFF'))

    def test_cancelled_recording_is_never_uploaded(self):
        def cancel_it(stop, cancel):
            cancel.set()
            stop.set()
        text, upload = self.recognize(cancel_it)
        self.assertEqual(text, '')
        upload.assert_not_called()


class OpenAIVoiceTests(unittest.TestCase):
    def test_stop_silences_playback(self):
        written = []
        started = Event()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def iter_bytes(self, size):
                for _ in range(100):
                    started.set()
                    time.sleep(0.01)
                    yield b'\0' * size

        class Output:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def write(self, chunk):
                written.append(chunk)

        api = Mock()
        api.audio.speech.with_streaming_response.create.return_value = Response()
        voice = OpenAIVoice('key', 'nova', connect=lambda key, timeout: api)
        with patch.dict('sys.modules', sounddevice=SimpleNamespace(RawOutputStream=Output)):
            voice.speak('Hello there')
            self.assertTrue(voice.speaking())
            started.wait(2)
            voice.stop()
            count = len(written)
            time.sleep(0.1)
        self.assertFalse(voice.speaking())
        self.assertLessEqual(len(written), count + 1)
        self.assertEqual(api.audio.speech.with_streaming_response.create.call_args.kwargs['voice'], 'nova')
