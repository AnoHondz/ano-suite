"""One Ano process per data directory; launching it again raises the existing panel."""
import hashlib
from pathlib import Path

from PySide6.QtCore import QObject, QLockFile, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    activated = Signal()

    def __init__(self, directory):
        super().__init__()
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.name = 'Ano-' + hashlib.sha256(str(directory.resolve()).casefold().encode()).hexdigest()[:24]
        self.lock = QLockFile(str(directory / 'ano.lock'))
        self.lock.setStaleLockTime(0)  # Dead-process lock detection remains enabled.
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._wake)

    def acquire(self):
        if not self.lock.tryLock(0):
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(500):
                socket.write(b'show')
                socket.waitForBytesWritten(500)
                socket.disconnectFromServer()
            return False
        if not self.server.listen(self.name):
            QLocalServer.removeServer(self.name)  # We own the lock, so an old endpoint is stale.
            self.server.listen(self.name)
        return True

    def _wake(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.close()
            socket.deleteLater()
            self.activated.emit()

    def close(self):
        self.server.close()
        self.lock.unlock()
