import ctypes
from ctypes import wintypes
import unittest
from unittest.mock import Mock, patch

from ano.windows_activity import SessionEvents


class SessionEventTests(unittest.TestCase):
    def test_lock_unlock_and_unrelated_messages(self):
        callback = Mock()
        api = Mock()
        with patch('ano.windows_activity.sys.platform', 'win32'), patch('ano.windows_activity.ctypes.WinDLL', return_value=api, create=True):
            listener = SessionEvents(123, callback)
            message = wintypes.MSG()
            message.hWnd = 123
            message.message = 0x02B1
            for reason, locked in ((7, True), (8, False), (4, True), (3, False)):
                message.wParam = reason
                self.assertEqual(listener.nativeEventFilter(None, ctypes.addressof(message)), (False, 0))
                callback.assert_called_with(locked)
            count = callback.call_count
            message.hWnd = 999
            listener.nativeEventFilter(None, ctypes.addressof(message))
            self.assertEqual(callback.call_count, count)
            listener.close()
            api.WTSUnRegisterSessionNotification.assert_called_once_with(123)
