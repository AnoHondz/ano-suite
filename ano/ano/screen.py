"""One-shot, in-memory screenshots with a separate approval step."""
import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QPoint, QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuick import QQuickImageProvider


def active_screen(fallback):
    """Use the foreground app's monitor after Ano has been hidden."""
    if sys.platform == 'win32':
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        rect = wintypes.RECT()
        if user32.GetWindowRect(user32.GetForegroundWindow(), ctypes.byref(rect)):
            screen = QGuiApplication.screenAt(QPoint((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2))
            if screen:
                return screen
    return fallback or QGuiApplication.primaryScreen()


def encode_image(image):
    if image.isNull():
        raise ValueError('Screen capture is unavailable on this display.')
    image = image.scaled(2000, 2000, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    image.setDevicePixelRatio(1)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, 'JPEG', 90):
        raise ValueError('Could not prepare the screenshot.')
    buffer.close()
    return bytes(data)


class PreviewProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self.image = QImage()

    def requestImage(self, identifier, size, requested_size):
        image = self.image.copy()
        size.setWidth(image.width())
        size.setHeight(image.height())
        return image


class ScreenCapture(QObject):
    changed = Signal()
    error = Signal(str)

    def __init__(self, allowed, preview_provider):
        super().__init__()
        self.allowed = allowed
        self.preview_provider = preview_provider
        self.window = None
        self._capturing = False
        self._bytes = None
        self._revision = 0
        self._fallback = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._take)

    @Property(bool, notify=changed)
    def capturing(self):
        return self._capturing

    @Property(bool, notify=changed)
    def ready(self):
        return self._bytes is not None

    @Property(str, notify=changed)
    def previewUrl(self):
        return f'image://capture/{self._revision}' if self.ready else ''

    @Slot()
    def begin(self):
        if not self.allowed() or self._capturing or self.ready or self.window is None:
            return
        self._fallback = self.window.screen()
        self._capturing = True
        self.changed.emit()
        self.window.hide()
        # Allow Windows' compositor to remove Ano before reading display pixels.
        self.timer.start(300)

    def _restore(self):
        if self.window:
            self.window.showNormal()
            self.window.raise_()
            self.window.requestActivate()

    def _take(self):
        if not self._capturing:
            return
        try:
            if not self.allowed():
                return
            if self.window.isVisible():
                raise ValueError('Ano must be hidden before capturing. Please try again.')
            screen = active_screen(self._fallback)
            if screen is None:
                raise ValueError('No display is available.')
            self._bytes = encode_image(screen.grabWindow(0).toImage())
            self.preview_provider.image = QImage.fromData(self._bytes)
            self._revision += 1
        except Exception:
            self._clear()
            self.error.emit('Could not capture the display. Please try again.')
        finally:
            self._capturing = False
            self._restore()
            self.changed.emit()

    def _clear(self):
        self._bytes = None
        self.preview_provider.image = QImage()

    @Slot()
    def cancel(self):
        was_capturing = self._capturing
        self.timer.stop()
        self._capturing = False
        self._clear()
        if was_capturing:
            self._restore()
        self.changed.emit()

    def consume(self):
        if not self.allowed() or not self.ready:
            return None
        data = self._bytes
        self._clear()
        self.changed.emit()
        return data
