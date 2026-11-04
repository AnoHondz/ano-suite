"""Push-to-talk: audio exists only in memory, and the transcript is sent on release."""
import importlib.util
import json
from pathlib import Path
import queue
from threading import Event
import time

from PySide6.QtCore import QObject, Property, Signal, Slot, QRunnable, QThreadPool
from .voice_setup import model_ready


class VoiceError(Exception):
    pass


class OfflineRecognizer:
    def __init__(self, model_path):
        self.path = Path(model_path)
        self._model = None

    def available(self):
        return (model_ready(self.path) and importlib.util.find_spec('vosk') is not None
                and importlib.util.find_spec('sounddevice') is not None)

    def recognize_turn(self, stop, cancel, listening):
        return self.recognize(stop, cancel, listening, end_on_utterance=True)

    def recognize(self, stop, cancel, listening, end_on_utterance=False):
        import sounddevice as sd
        from vosk import Model, KaldiRecognizer, SetLogLevel
        SetLogLevel(-1)
        if self._model is None:
            self._model = Model(str(self.path))
        if stop.is_set() or cancel.is_set():
            return ''
        samplerate = int(sd.query_devices(kind='input')['default_samplerate'])
        recognizer = KaldiRecognizer(self._model, samplerate)
        chunks = queue.Queue(maxsize=32)
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

        parts = []
        utterance_done = Event()

        def consume(data):
            if recognizer.AcceptWaveform(data):
                text = json.loads(recognizer.Result()).get('text', '')
                if text:
                    parts.append(text)
                    if end_on_utterance:
                        utterance_done.set()

        # Opening the microphone is exclusive to this held-button worker.
        if not stop.is_set() and not cancel.is_set():
            with sd.RawInputStream(samplerate=samplerate, blocksize=max(1, samplerate // 10),
                                   dtype='int16', channels=1, callback=callback):
                listening()
                deadline = time.monotonic() + 60
                while not stop.is_set() and not cancel.is_set() and not utterance_done.is_set() and time.monotonic() < deadline:
                    if failed.is_set():
                        raise VoiceError('Microphone audio was interrupted. Please try again.')
                    try:
                        consume(chunks.get(timeout=.1))
                    except queue.Empty:
                        pass
        if cancel.is_set():
            return ''
        if failed.is_set():
            raise VoiceError('Microphone audio was interrupted. Please try again.')
        while not chunks.empty():
            consume(chunks.get_nowait())
        final = json.loads(recognizer.FinalResult()).get('text', '')
        if final:
            parts.append(final)
        return ' '.join(parts).strip()


class VoiceSignals(QObject):
    listening = Signal()
    completed = Signal(str, str)


class VoiceJob(QRunnable):
    def __init__(self, backend, turn=False):
        super().__init__()
        self.backend = backend
        self.turn = turn
        self.stop = Event()
        self.cancel = Event()
        self.signals = VoiceSignals()

    def run(self):
        try:
            recognize = self.backend.recognize_turn if self.turn else self.backend.recognize
            result = recognize(self.stop, self.cancel, self.signals.listening.emit)
            self.signals.completed.emit(result, '')
        except VoiceError as error:
            self.signals.completed.emit('', str(error))
        except Exception:
            self.signals.completed.emit('', 'Could not use the microphone. Check Windows microphone access and your default input device.')


class VoiceService(QObject):
    changed = Signal()
    transcript = Signal(str)
    starting = Signal()

    def __init__(self, backend, allowed):
        super().__init__()
        self.backend = backend
        self.allowed = allowed
        self._available = backend.available()
        self._job = None
        self._recording = False
        self._discard = False
        self._turn = False
        self.where = getattr(backend, 'where', 'on this device')
        self._status = f'Hold to talk · Speech is transcribed {self.where}' if self._available else 'Voice setup needed · Run python -m ano.voice_setup after installing .[voice]'
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)

    @Property(bool, constant=True)
    def available(self):
        return self._available

    @Property(bool, constant=True)
    def turnAvailable(self):
        return self._available and callable(getattr(self.backend, 'recognize_turn', None))

    @Property(bool, notify=changed)
    def turnMode(self):
        return self._turn

    @Property(bool, notify=changed)
    def busy(self):
        return self._job is not None

    @Property(bool, notify=changed)
    def recording(self):
        return self._recording

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Slot()
    def start(self):
        self._start(False)

    @Slot()
    def startTurn(self):
        if self.turnAvailable:
            self._start(True)

    def _start(self, turn):
        if not self._available or self.busy or not self.allowed():
            return
        self.starting.emit()
        self._discard = False
        self._turn = turn
        self._status = 'Preparing microphone… keep holding until Listening appears'
        self._job = VoiceJob(self.backend, turn=turn)
        self._job.signals.listening.connect(self._listening)
        self._job.signals.completed.connect(self._finished)
        self.changed.emit()
        self.pool.start(self._job)

    @Slot()
    def _listening(self):
        if self._job and not self._job.stop.is_set() and not self._discard:
            self._recording = True
            self._status = 'Listening · Pause after speaking to send (60-second limit)' if self._turn else 'Listening · Release to send (60-second limit)'
            self.changed.emit()

    @Slot()
    def stop(self):
        if self._job:
            self._job.stop.set()
            self._recording = False
            self._status = f'Transcribing {self.where}…'
            self.changed.emit()

    @Slot()
    def cancel(self):
        if self._job:
            self._discard = True
            self._job.cancel.set()
            self._job.stop.set()
            self._recording = False
            self._status = 'Voice cancelled · Audio discarded'
            self.changed.emit()

    @Slot(str, str)
    def _finished(self, text, error):
        discarded = self._discard or not self.allowed()
        self._job = None
        self._recording = False
        if discarded:
            self._status = 'Voice cancelled · Audio discarded'
        elif error:
            self._status = error
        elif text.strip():
            self._status = f'Transcribed {self.where} · Sent: “' + text.strip()[:80] + '”'
            self.transcript.emit(text.strip())
        else:
            self._status = 'No speech recognized · Hold until Listening appears and try again'
        self.changed.emit()

    def close(self):
        self.cancel()
        self.pool.waitForDone(5000)
