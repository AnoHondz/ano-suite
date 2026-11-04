"""Optional spoken replies (Windows SAPI, or OpenAI voices via openai_audio); replies are plain text."""
import importlib.util
import sys

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer


class SapiSpeaker:
    def __init__(self):
        self.voice = None
        self._com_initialized = False

    def available(self):
        return sys.platform == 'win32' and importlib.util.find_spec('win32com') is not None

    def speak(self, text):
        if self.voice is None:
            import pythoncom
            from win32com.client import Dispatch
            pythoncom.CoInitialize()
            self._com_initialized = True
            try:
                self.voice = Dispatch('SAPI.SpVoice')
            except Exception:
                pythoncom.CoUninitialize()
                self._com_initialized = False
                raise
        self.voice.Speak(text, 1 | 2 | 16)  # async, purge old audio, force plain text

    def stop(self):
        if self.voice is not None:
            self.voice.Speak('', 1 | 2 | 16)

    def speaking(self):
        return bool(self.voice is not None and self.voice.Status.RunningState == 2)

    def close(self):
        try:
            self.stop()
        finally:
            self.voice = None
            if self._com_initialized:
                import pythoncom
                pythoncom.CoUninitialize()
                self._com_initialized = False


class SpeechService(QObject):
    changed = Signal()

    def __init__(self, memory, backend, allowed):
        super().__init__()
        self.memory = memory
        self.backend = backend
        self.allowed = allowed
        self._available = backend.available()
        self._enabled = memory.setting('spoken_replies', False)
        self.session_enabled = False
        self._speaking = False
        self._status = ''
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self._poll)

    @Property(bool, constant=True)
    def available(self):
        return self._available

    @Property(bool, notify=changed)
    def enabled(self):
        return self._enabled

    @Property(bool, notify=changed)
    def speaking(self):
        return self._speaking

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property('QVariantList', constant=True)
    def voices(self):
        return list(getattr(self.backend, 'voices', []))

    @Property(str, notify=changed)
    def voice(self):
        return getattr(self.backend, 'voice', '')

    @Slot(str)
    def setVoice(self, name):
        if name in self.voices:
            self.backend.voice = name
            self.memory.set_setting('openai_voice', name)
            self.changed.emit()

    @Slot(bool)
    def setEnabled(self, value):
        self._enabled = value
        self._status = ''
        self.memory.set_setting('spoken_replies', value)
        if not value:
            self.stop()
        self.changed.emit()

    @Slot(str)
    def speak(self, text):
        if not (self._enabled or self.session_enabled) or not self._available or not self.allowed() or not text.strip():
            return
        try:
            self.backend.speak(text[:4000])
            self._speaking = True
            self._status = ''
            self.timer.start()
            self.changed.emit()
        except Exception:
            self._status = 'Spoken reply unavailable. Check Windows voices and audio output.'
            self.stop()

    @Slot()
    def stop(self):
        try:
            self.backend.stop()
        except Exception:
            pass
        self.timer.stop()
        self._speaking = False
        self.changed.emit()

    def _poll(self):
        try:
            if not self.allowed() or not self.backend.speaking():
                # Backends that speak in the background report failures afterwards.
                self._status = getattr(self.backend, 'error', '') or self._status
                self.stop()
        except Exception:
            self.stop()

    def close(self):
        self.stop()
        try:
            self.backend.close()
        except Exception:
            pass
