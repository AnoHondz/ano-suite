"""OpenAI speech: accurate push-to-talk transcription and natural spoken replies.

Audio stays in memory. A recording is uploaded only after the mic button is released, never if it is cancelled.
"""
import importlib.util
import io
import queue
from threading import Event, Thread
import time
import wave

from .voice import VoiceError

TRANSCRIBE_MODEL = 'gpt-4o-transcribe'
SPEECH_MODEL = 'gpt-4o-mini-tts'
VOICES = ['coral', 'nova', 'shimmer', 'sage', 'ash', 'echo']
SPEECH_STYLE = 'Speak warmly and cheerfully, like a friendly anime companion. Keep a natural, clear pace.'
SAMPLE_RATE = 16000
SPEECH_RATE = 24000  # OpenAI 'pcm' output: 24 kHz, 16-bit, mono


def client(key, timeout):
    from openai import OpenAI
    return OpenAI(api_key=key, timeout=timeout, max_retries=0)


class CloudRecognizer:
    """Same interface as voice.OfflineRecognizer, so VoiceService can use either."""
    where = 'by OpenAI'

    def __init__(self, key, connect=client):
        self.key = key
        self.connect = connect

    def available(self):
        return bool(self.key) and importlib.util.find_spec('sounddevice') is not None

    def recognize(self, stop, cancel, listening):
        import sounddevice as sd
        chunks = queue.Queue(maxsize=700)  # about 70 seconds of 0.1-second blocks
        failed = Event()

        def callback(data, frames, timing, status):
            if stop.is_set() or cancel.is_set():
                return
            if status:
                failed.set()
                return
            try:
                chunks.put_nowait(bytes(data))
            except queue.Full:
                failed.set()

        if stop.is_set() or cancel.is_set():
            return ''
        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=SAMPLE_RATE // 10, dtype='int16',
                               channels=1, callback=callback):
            listening()
            deadline = time.monotonic() + 60
            while not stop.is_set() and not cancel.is_set() and time.monotonic() < deadline:
                if failed.is_set():
                    raise VoiceError('Microphone audio was interrupted. Please try again.')
                time.sleep(0.05)
        if cancel.is_set():
            return ''
        audio = b''.join(chunks.get_nowait() for _ in range(chunks.qsize()))
        if len(audio) < SAMPLE_RATE * 2 * 0.3:  # under 0.3 seconds: nothing worth sending
            return ''
        return self.transcribe(audio)

    def transcribe(self, audio):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(SAMPLE_RATE)
            file.writeframes(audio)
        try:
            result = self.connect(self.key, 30.0).audio.transcriptions.create(
                model=TRANSCRIBE_MODEL, file=('speech.wav', buffer.getvalue(), 'audio/wav'),
                prompt='The user is talking to their desktop assistant, which is called Ano.')
        except Exception:
            raise VoiceError('OpenAI could not transcribe that. Check the internet connection and try again.') from None
        return (result.text or '').strip()


class OpenAIVoice:
    """Same interface as speech.SapiSpeaker; streams speech so playback starts quickly."""
    voices = VOICES

    def __init__(self, key, voice='coral', connect=client):
        self.key = key
        self.voice = voice if voice in VOICES else VOICES[0]
        self.connect = connect
        self.error = ''
        self._generation = 0
        self._active = False

    def available(self):
        return bool(self.key) and importlib.util.find_spec('sounddevice') is not None

    def speak(self, text):
        self._generation += 1
        self._active = True
        self.error = ''
        Thread(target=self._play, args=(text, self._generation), daemon=True).start()

    def _play(self, text, generation):
        import sounddevice as sd
        try:
            with self.connect(self.key, 30.0).audio.speech.with_streaming_response.create(
                    model=SPEECH_MODEL, voice=self.voice, input=text, instructions=SPEECH_STYLE,
                    response_format='pcm') as response:
                with sd.RawOutputStream(samplerate=SPEECH_RATE, channels=1, dtype='int16') as output:
                    for chunk in response.iter_bytes(4800):
                        if generation != self._generation:
                            break
                        output.write(chunk)
        except Exception:
            if generation == self._generation:
                self.error = 'OpenAI voice is unavailable. Check the internet connection and audio output.'
        finally:
            if generation == self._generation:
                self._active = False

    def stop(self):
        # A newer generation makes the playing thread stop at its next chunk.
        self._generation += 1
        self._active = False

    def speaking(self):
        return self._active

    def close(self):
        self.stop()
