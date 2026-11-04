"""Windows system-wide Ctrl+Alt+A shortcut, released on shutdown."""
import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter


class GlobalShortcut(QAbstractNativeEventFilter):
    ID = 0x414E

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.registered = False
        if sys.platform == 'win32':
            self.registered = bool(ctypes.windll.user32.RegisterHotKey(None, self.ID, 0x4003, ord('A')))

    def nativeEventFilter(self, event_type, message):
        if sys.platform == 'win32':
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == self.ID:
                self.callback()
                return True, 0
        return False, 0

    def close(self):
        if self.registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.ID)
            self.registered = False
