"""Optional start at Windows sign-in, quietly in the tray, so reminders keep working.

Uses the current user's Run key only (no administrator rights, no scheduled task); turning it off
removes that one value.
"""
from pathlib import Path
import sys

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE = 'Ano'


def command(executable=None):
    """Windowless supervisor starts Ano hidden in the tray and recovers exits."""
    python = Path(executable or sys.executable)
    windowless = python.with_name('pythonw.exe')
    return f'"{windowless if windowless.exists() else python}" -m ano.supervisor --sign-in'


def _key(write=False):
    import winreg
    access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE if write else winreg.KEY_QUERY_VALUE
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)


def enabled(strict=False):
    import winreg
    try:
        with _key() as key:
            value, _ = winreg.QueryValueEx(key, VALUE)
    except FileNotFoundError:
        return False
    except OSError:
        if strict:raise
        return False
    return value == command()


def enable():
    import winreg
    with _key(write=True) as key:
        winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, command())


def disable():
    import winreg
    try:
        with _key(write=True) as key:
            winreg.DeleteValue(key, VALUE)
    except FileNotFoundError:
        pass
