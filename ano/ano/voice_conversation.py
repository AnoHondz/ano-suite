"""Opt-in local voice turns; microphone is closed during inference and playback.

This is half duplex, with a local interrupt control, not acoustic echo-cancelled
simultaneous speaking/listening. Starting a session never persists mic permission.
"""
from PySide6.QtCore import QObject, Signal, QTimer


class VoiceConversation(QObject):
    changed = Signal()

    def __init__(self, voice, speech, controller, allowed):
        super().__init__(controller)
        self.voice, self.speech, self.controller, self.allowed = voice, speech, controller, allowed
        self.active = False
        self.status = 'Voice conversation off'
        self.waiting = False
        self.interrupted = False
        voice.transcript.connect(self._transcript)
        voice.changed.connect(self._voice_changed)
        speech.changed.connect(self._speech_changed)
        controller.replyFinished.connect(self._reply_finished)

    @property
    def available(self):
        return self.voice.turnAvailable and self.speech.available and self.controller.connected

    def start(self):
        if self.active:
            return
        if not self.available or not self.allowed() or self.controller.busy or self.controller.queuedCount:
            self.controller.setStatus('Voice conversation needs local voice setup and an idle, unlocked Ano')
            return
        if getattr(self.controller.provider, 'mode', '') != 'local':
            self.controller.setStatus('Select the local brain before starting a local voice conversation')
            return
        self.active = True
        self.waiting = False
        self.interrupted = False
        self.speech.session_enabled = True
        self.speech.stop()
        self._listen()

    def stop(self, message='Voice conversation stopped'):
        if not self.active:
            return
        self.active = False
        self.waiting = False
        self.status = message
        self.speech.session_enabled = False
        self.voice.cancel()
        self.speech.stop()
        self.controller.setStatus(message)
        self.changed.emit()

    def interrupt(self):
        if not self.active:
            return
        if self.controller.busy:
            self.interrupted = True
            self.controller.stopTask()
            self.waiting = True
            self.status = 'Stopping task before listening again'
            self.changed.emit()
            return
        if self.voice.busy:
            self.voice.stop()
            return
        self.speech.stop()
        QTimer.singleShot(0, self._listen)

    def _listen(self):
        if not self.active:
            return
        if not self.allowed():
            self.stop('Voice conversation stopped by Pause, lock, privacy or screen capture')
            return
        if self.controller.busy or self.speech.speaking or self.voice.busy:
            return
        self.waiting = False
        self.status = 'Listening locally · Pause after speaking to send'
        self.voice.startTurn()
        if not self.voice.busy:
            self.stop('Microphone could not start')
        else:
            self.changed.emit()

    def _transcript(self, text):
        if not self.active or not self.allowed():
            return
        if text.casefold().strip(' .!?') in ('stop voice conversation', 'end voice conversation'):
            self.stop()
            return
        self.waiting = True
        self.status = 'Thinking locally · Microphone off'
        self.changed.emit()
        self.controller.send(text)
        if not self.controller.busy:
            self.stop('Voice command was not accepted; inspect Ano status')

    def _voice_changed(self):
        if self.active and not self.voice.busy and not self.waiting:
            # Empty speech, capture failure or cancellation must not reopen a mic loop.
            self.stop(self.voice.status)

    def _reply_finished(self, reply, failed):
        if not self.active:
            return
        if failed:
            if self.interrupted:
                self.interrupted = False
                QTimer.singleShot(0, self._listen)
            else:
                self.stop('Voice task stopped; inspect the saved task result before retrying')
        elif self.speech.speaking:
            self.status = 'Speaking locally · Microphone off · Interrupt to reply'
            self.changed.emit()
        elif self.speech.status:
            self.stop(self.speech.status)
        else:
            QTimer.singleShot(0, self._listen)

    def _speech_changed(self):
        if self.active and self.waiting and not self.speech.speaking and not self.controller.busy:
            if self.speech.status:
                self.stop(self.speech.status)
            else:
                QTimer.singleShot(0, self._listen)
