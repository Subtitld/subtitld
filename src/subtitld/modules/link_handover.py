"""A Subtitld started with a subtitld:// link while another one runs hands the link over through a local
socket and quits, so the project opens in the window already there (modules/subtitldcc_links.py).

Every Subtitld listens, unless another one already does; a socket left by one that crashed is taken over.
"""

import hashlib
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from subtitld.modules.subtitldcc_links import is_link

WAIT_MS = 1000


def _server_name():
    """Per person: the socket of another person's Subtitld on this computer is not theirs."""
    who = os.environ.get('USER') or os.environ.get('USERNAME') or str(getattr(os, 'getuid', lambda: '')())
    return 'subtitld-links-' + hashlib.sha1(who.encode('utf-8', 'replace')).hexdigest()[:12]


def hand_over(links):
    """Give `links` to the Subtitld already running. True when it took them (this one can quit)."""
    socket = QLocalSocket()
    socket.connectToServer(_server_name())
    if not socket.waitForConnected(WAIT_MS):
        return False
    socket.write(''.join(f'{link}\n' for link in links).encode('utf-8'))
    sent = socket.waitForBytesWritten(WAIT_MS)
    socket.disconnectFromServer()
    if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        socket.waitForDisconnected(WAIT_MS)
    return sent


class Listener(QObject):
    """Links another Subtitld hands over (`received`, on the main thread)."""

    received = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._connection)
        self._buffers = {}

    def listen(self):
        """Take the links from now on, unless another Subtitld is taking them. True when listening."""
        name = _server_name()
        if self.server.listen(name):
            return True
        probe = QLocalSocket()
        probe.connectToServer(name)
        if probe.waitForConnected(300):
            probe.abort()
            return False
        # Left behind by a Subtitld that ended without closing it.
        QLocalServer.removeServer(name)
        return self.server.listen(name)

    def _connection(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self._buffers[socket] = b''
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._done(s))

    def _read(self, socket):
        self._buffers[socket] = self._buffers.get(socket, b'') + bytes(socket.readAll())

    def _done(self, socket):
        self._read(socket)
        data = self._buffers.pop(socket, b'')
        socket.deleteLater()
        for line in data.decode('utf-8', 'replace').splitlines():
            if is_link(line.strip()):
                self.received.emit(line.strip())

    def close(self):
        self.server.close()
