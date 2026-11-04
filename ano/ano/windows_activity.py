"""Read foreground app metadata and receive Windows session notifications."""
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import PureWindowsPath

from PySide6.QtCore import QAbstractNativeEventFilter


class WindowsActivity:
    def __init__(self):
        self.supported = sys.platform == 'win32'
        if not self.supported:
            return
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.user.OpenInputDesktop.restype = wintypes.HANDLE
        self.user.CloseDesktop.argtypes = [wintypes.HANDLE]
        self.user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    def unlocked(self):
        if not self.supported:
            return False
        desktop = self.user.OpenInputDesktop(0, False, 0x0001)
        if not desktop:
            return False
        try:
            name = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD()
            ok = self.user.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed))
            return bool(ok and name.value.casefold() == 'default')
        finally:
            self.user.CloseDesktop(desktop)

    def foreground(self):
        if not self.supported:
            return None
        window = self.user.GetForegroundWindow()
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if not window or not pid.value or pid.value == os.getpid():
            return None
        length = min(self.user.GetWindowTextLengthW(window), 1024)
        title = ctypes.create_unicode_buffer(length + 1)
        self.user.GetWindowTextW(window, title, len(title))
        if not title.value.strip():
            return None
        app = 'Unknown application'
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if handle:
            try:
                path = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(path))
                if self.kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    app = PureWindowsPath(path.value).name
            finally:
                self.kernel.CloseHandle(handle)
        # Avoid attributing a title to a different app if focus changed mid-read.
        if self.user.GetForegroundWindow() != window:
            return None
        return app, title.value.strip()


class SessionEvents(QAbstractNativeEventFilter):
    def __init__(self, hwnd, callback):
        super().__init__()
        self.hwnd = hwnd
        self.callback = callback
        self.registered = False
        if sys.platform == 'win32':
            self.wts = ctypes.WinDLL('wtsapi32', use_last_error=True)
            self.wts.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
            self.wts.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
            self.registered = bool(self.wts.WTSRegisterSessionNotification(hwnd, 0))

    def nativeEventFilter(self, event_type, message):
        if sys.platform == 'win32':
            msg = wintypes.MSG.from_address(int(message))
            if msg.hWnd == self.hwnd and msg.message == 0x02B1:
                if msg.wParam in (2, 4, 6, 7):
                    self.callback(True)
                elif msg.wParam in (1, 3, 5, 8):
                    self.callback(False)
        return False, 0

    def close(self):
        if self.registered:
            self.wts.WTSUnRegisterSessionNotification(self.hwnd)
            self.registered = False
