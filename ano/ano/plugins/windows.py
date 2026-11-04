"""Other apps' windows: list, focus, minimise, maximise, restore, or politely close (the app may ask to save)."""
import ctypes
from ctypes import wintypes

from ..tools import ToolError

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
DWMWA_CLOAKED = 14
WM_CLOSE = 0x0010
SHOW = {'minimize': 6, 'maximize': 3, 'restore': 9}


def open_windows():
    """Return [(hwnd, pid, title, minimised)] for normal app windows, as Alt+Tab shows them."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        cloaked = wintypes.DWORD()
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        length = user32.GetWindowTextLengthW(hwnd)
        if cloaked.value or not length:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((int(hwnd), pid.value, title.value, bool(user32.IsIconic(hwnd))))
        return True

    user32.EnumWindows(visit, 0)
    return found


def process_name(pid):
    import psutil
    try:
        return psutil.Process(pid).name()
    except psutil.Error:
        return '?'


class Windows:
    def __init__(self, listing=open_windows, own_pid=None):
        import os
        self.listing = listing
        self.own_pid = own_pid if own_pid is not None else os.getpid()

    def definitions(self):
        return [
            {'name': 'list_windows',
             'description': 'List the open app windows (like Alt+Tab): window id, app, title, minimised.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {}}},
            {'name': 'window_action',
             'description': 'Act on a window by id from list_windows: focus, minimize, maximize, restore, or close. '
                            'Close asks the app politely, so it can still ask the user to save their work.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                 'window_id': {'type': 'integer'},
                 'action': {'type': 'string', 'enum': ['focus', 'minimize', 'maximize', 'restore', 'close']}},
                 'required': ['window_id', 'action']}},
        ]

    def run(self, name, arguments):
        return getattr(self, name)(**arguments)

    def _windows(self):
        return [window for window in self.listing() if window[1] != self.own_pid]

    def list_windows(self):
        lines = [f'window {hwnd} · {process_name(pid)} · {title}' + (' · minimised' if minimised else '')
                 for hwnd, pid, title, minimised in self._windows()]
        return '\n'.join(lines) or 'No app windows are open.', 'Looked at your open windows'

    def window_action(self, window_id, action):
        window = next((window for window in self._windows() if window[0] == int(window_id)), None)
        if window is None:
            raise ToolError('That window is no longer open. List the windows again.')
        hwnd, _, title, minimised = window
        user32 = ctypes.windll.user32
        if action == 'close':
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        elif action == 'focus':
            if minimised:
                user32.ShowWindow(hwnd, SHOW['restore'])
            user32.SetForegroundWindow(hwnd)
        elif action in SHOW:
            user32.ShowWindow(hwnd, SHOW[action])
        else:
            raise ToolError('Unknown window action.')
        past = {'focus': 'Switched to', 'minimize': 'Minimised', 'maximize': 'Maximised',
                'restore': 'Restored', 'close': 'Asked to close'}[action]
        return f'{past} “{title}”.', f'{past} “{title[:60]}”'
