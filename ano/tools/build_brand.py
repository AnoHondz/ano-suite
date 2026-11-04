"""Build Ano's icons from the real face, qml/AnoFace.qml, so the app, taskbar and chat always match.

Renders the idle face offscreen at each icon size (small sizes are drawn natively, not shrunk), then
writes ano/assets/ano-mark.png (chat avatar), ano/extension/icon.png and the multi-size ano-ai.ico.

    .venv\\Scripts\\python.exe tools\\build_brand.py
"""
import os
from pathlib import Path
import struct

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402
from PySide6.QtQuick import QQuickView  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'ano' / 'assets'
FACE = ROOT / 'ano' / 'qml' / 'AnoFace.qml'
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def render(size):
    """The idle, motionless face at exactly size x size pixels, on a transparent background."""
    view = QQuickView()
    view.setColor(Qt.transparent)
    view.setInitialProperties({'mood': 'idle', 'animated': False, 'interactive': False, 'soundEnabled': False})
    view.setResizeMode(QQuickView.SizeRootObjectToView)
    view.setSource(QUrl.fromLocalFile(str(FACE)))
    if view.status() != QQuickView.Ready:
        raise SystemExit('AnoFace.qml failed to load: ' + '; '.join(str(e) for e in view.errors()))
    view.resize(size, size)
    view.show()
    QGuiApplication.processEvents()
    image = view.grabWindow().convertToFormat(QImage.Format_ARGB32)
    view.close()
    if image.size().width() != size:
        image = image.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return image


def png_bytes(image):
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, 'PNG')
    return bytes(data)


def build():
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (keeps Qt alive)
    images = {size: render(size) for size in SIZES}
    images[256].save(str(ASSETS / 'ano-mark.png'))
    images[128].save(str(ROOT / 'ano' / 'extension' / 'icon.png'))
    encoded = [png_bytes(images[size]) for size in SIZES]
    offset = 6 + 16 * len(encoded)
    entries = []
    for size, data in zip(SIZES, encoded):
        entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    (ASSETS / 'ano-ai.ico').write_bytes(struct.pack('<HHH', 0, 1, len(encoded)) + b''.join(entries) + b''.join(encoded))
    print(f'Built ano-mark.png, extension icon.png and a {len(SIZES)}-size ano-ai.ico from AnoFace.qml.')


if __name__ == '__main__':
    build()
