import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage, QColor

from ano.app import Controller
from ano.memory import Memory
from ano.screen import ScreenCapture, PreviewProvider, encode_image


class ScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.allowed = True
        self.preview = PreviewProvider()
        self.capture = ScreenCapture(lambda: self.allowed, self.preview)
        self.capture.window = Mock()
        self.capture.window.isVisible.return_value = False
        self.image = QImage(100, 60, QImage.Format_RGB32)
        self.image.fill(QColor('#9ae3c1'))

    def tearDown(self):
        self.capture.cancel()

    def take(self):
        screen = Mock()
        screen.grabWindow.return_value = SimpleNamespace(toImage=lambda: self.image)
        with patch('ano.screen.active_screen', return_value=screen):
            self.capture.begin()
            self.capture.timer.stop()
            self.capture._take()
        return screen

    def test_hide_before_capture_and_preview_exact_encoded_image(self):
        screen = self.take()
        self.capture.window.hide.assert_called_once()
        screen.grabWindow.assert_called_once_with(0)
        self.capture.window.showNormal.assert_called_once()
        self.assertTrue(self.capture.ready)
        self.assertFalse(self.capture.capturing)
        data = self.capture.consume()
        self.assertFalse(QImage.fromData(data).isNull())
        self.assertFalse(self.capture.ready)
        self.assertTrue(self.preview.image.isNull())

    def test_pause_blocks_begin_and_delayed_capture(self):
        self.allowed = False
        self.capture.begin()
        self.capture.window.hide.assert_not_called()
        self.allowed = True
        self.capture.begin()
        self.capture.timer.stop()
        self.allowed = False
        with patch('ano.screen.active_screen') as screen:
            self.capture._take()
            screen.assert_not_called()
        self.assertFalse(self.capture.ready)

    def test_panel_reopened_during_delay_blocks_capture(self):
        self.capture.window.isVisible.return_value = True
        with patch('ano.screen.active_screen') as screen:
            self.capture.begin()
            self.capture.timer.stop()
            self.capture._take()
            screen.assert_not_called()
        self.assertFalse(self.capture.ready)

    def test_cancel_pending_capture_and_preview(self):
        self.capture.begin()
        self.capture.cancel()
        with patch('ano.screen.active_screen') as screen:
            self.capture._take()
            screen.assert_not_called()
        self.take()
        self.capture.cancel()
        self.assertIsNone(self.capture.consume())
        self.assertTrue(self.preview.image.isNull())

    def test_approval_is_required_and_pause_invalidates_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = Memory(Path(directory) / 'memory.sqlite3')
            controller = Controller(memory, Mock())
            controller.screen_capture = self.capture
            self.capture.allowed = lambda: not controller.paused and not controller.busy
            self.take()
            controller.send('Cannot bypass preview')
            self.assertEqual(memory.recent(), [])
            controller.togglePause()
            self.assertFalse(self.capture.ready)
            controller.togglePause()
            controller.approveScreen('Stale approval')
            controller.provider.reply.assert_not_called()
            self.assertEqual(memory.recent(), [])
            memory.close()

    def test_image_dimensions_are_bounded(self):
        image = QImage(3000, 2400, QImage.Format_RGB32)
        image.fill(QColor('white'))
        result = QImage.fromData(encode_image(image))
        self.assertEqual((result.width(), result.height()), (2000, 1600))
