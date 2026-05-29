"""Allow only one running instance; raise the existing window on a second launch."""
from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_INSTANCE_KEY = "AERenderManager_SingleInstance_v1"


class SingleInstanceGuard:
    def __init__(self, key=_INSTANCE_KEY):
        self._key = key
        self._server = None
        self._on_raise = None

    def try_acquire(self):
        """
        Return True if this process is the primary instance.
        Return False if another instance is already running (and ping it).
        """
        probe = QLocalSocket()
        probe.connectToServer(self._key)
        if probe.waitForConnected(400):
            probe.write(b"raise")
            probe.waitForBytesWritten(500)
            probe.disconnectFromServer()
            return False

        QLocalServer.removeServer(self._key)
        self._server = QLocalServer()
        if not self._server.listen(self._key):
            QLocalServer.removeServer(self._key)
            if not self._server.listen(self._key):
                return True
        self._server.newConnection.connect(self._handle_connection)
        return True

    def set_raise_callback(self, callback):
        self._on_raise = callback

    def _handle_connection(self):
        conn = self._server.nextPendingConnection()
        if not conn:
            return
        conn.waitForReadyRead(300)
        conn.disconnectFromServer()
        if self._on_raise:
            QTimer.singleShot(0, self._on_raise)
