"""Main-thread, single-use human approvals for worker-thread operations."""
import threading
import uuid

from PySide6.QtCore import QObject, Property, Signal, Slot
from .tools import ToolError


class Reviews(QObject):
    changed = Signal()
    _show = Signal(str, object)
    _clear = Signal(str)

    def __init__(self, allowed, timeout=90):
        super().__init__()
        self.allowed = allowed
        self.timeout = timeout
        self._lock = threading.Lock()
        self._waiting = {}
        self._review = {}
        self._show.connect(self._present)
        self._clear.connect(self._dismiss)

    @Property('QVariantMap', notify=changed)
    def review(self):
        return self._review

    @Property(bool, notify=changed)
    def ready(self):
        return bool(self._review)

    def request(self, **details):
        if not self.allowed():
            raise ToolError('Ano is paused or locked. Nothing was approved.')
        identifier = uuid.uuid4().hex
        waiting = [threading.Event(), False]
        with self._lock:
            if self._waiting:
                raise ToolError('Another action is awaiting review. Try again afterward.')
            self._waiting[identifier] = waiting
        self._show.emit(identifier, details)
        try:
            if not waiting[0].wait(self.timeout) or not waiting[1] or not self.allowed():
                raise ToolError('The user cancelled, stopped, or did not approve this action.')
        finally:
            with self._lock:
                self._waiting.pop(identifier, None)
            self._clear.emit(identifier)

    @Slot(str, object)
    def _present(self, identifier, details):
        with self._lock:
            waiting = self._waiting.get(identifier)
            if not waiting or waiting[0].is_set() or not self.allowed():
                return
        self._review = dict(details, id=identifier)
        self.changed.emit()

    @Slot(str)
    def approve(self, identifier):
        with self._lock:
            waiting = self._waiting.get(identifier)
            if waiting and self._review.get('id') == identifier and self.allowed():
                waiting[1] = True
                waiting[0].set()
        self._dismiss(identifier)

    @Slot()
    def cancel(self):
        with self._lock:
            for waiting in self._waiting.values():
                waiting[1] = False
                waiting[0].set()
        self._review = {}
        self.changed.emit()

    @Slot(str)
    def _dismiss(self, identifier):
        if self._review.get('id') == identifier:
            self._review = {}
            self.changed.emit()
