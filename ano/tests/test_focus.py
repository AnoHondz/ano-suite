import unittest
from unittest.mock import Mock

from ano.desktop_native import WindowsDesktop
from ano.tools import ToolError


class FakeUser:
    """Foreground stays put until the n-th focus method runs."""

    def __init__(self, works_on):
        self.front, self.works_on, self.calls = 1, works_on, []

    def GetForegroundWindow(self):
        return self.front


def desktop(works_on):
    backend = object.__new__(WindowsDesktop)
    backend.user = FakeUser(works_on)
    target = dict(hwnd=42)
    backend.describe = lambda hwnd: target
    def method(name):
        def run(hwnd):
            backend.user.calls.append(name)
            if name == works_on:
                backend.user.front = hwnd
        return run
    for name in ('_force_foreground', '_nudge_then_force', '_focus_by_automation', '_restore_cycle'):
        setattr(backend, name, method(name))
    backend._front_app = lambda: 'Code.exe'
    return backend, target


class FocusTests(unittest.TestCase):
    def test_stronger_methods_are_tried_until_one_works(self):
        backend, target = desktop('_focus_by_automation')
        backend.focus(target)
        self.assertEqual(backend.user.calls, ['_force_foreground', '_nudge_then_force', '_focus_by_automation'])

    def test_already_in_front_needs_nothing(self):
        backend, target = desktop(None)
        backend.user.front = 42
        backend.focus(target)
        self.assertEqual(backend.user.calls, [])

    def test_failure_names_the_window_that_stayed_in_front(self):
        backend, target = desktop(None)
        with self.assertRaises(ToolError) as caught:
            backend.focus(target)
        self.assertIn('Code.exe stayed in front', str(caught.exception))
        self.assertEqual(len(backend.user.calls), 4)


if __name__ == '__main__':
    unittest.main()
