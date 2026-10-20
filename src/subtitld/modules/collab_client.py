"""The open shared project's connection to subtitld.cc (modules.collab_sync over a QWebSocket)

While a shared project is open, it stays connected: it reconnects on its own (waiting longer each time, up to
a minute), pings to notice a dead link, and catches up when it is back. Changes made here are sent as the
journal records them, also while offline (modules.collab_state queues them); collaborators' changes are
applied to the project as they come.

It stops for good when the server says the person may not: signed out (4401), a token without the "collab"
scope (4403: sign in again), or not a member any more (4404). `failed` says which.
"""

import json
import logging

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

from subtitld.modules import collab_state, cue_ids, docops, history, journal, session
from subtitld.modules.collab_sync import Engine, Live

log = logging.getLogger(__name__)

PING_SECONDS = 25
RESYNC_SECONDS = 3
BACKOFF_SECONDS = (1, 2, 5, 10, 30, 60)
# Close codes after which trying again cannot help.
FINAL_CODES = {4400: 'user_agent', 4401: 'sign_in_required', 4403: 'missing_scope', 4404: 'not_found'}


class SessionLive(Live):
    """The open project (modules.session)."""

    def flush(self):
        session.capture_changes()

    def snapshot(self):
        return docops.snapshot(session.SUBTITLE.get('segments') or [], session.SPEAKERS, session.SUBTITLE)

    def apply(self, ops):
        document = {'segments': session.SUBTITLE.setdefault('segments', []), 'speakers': session.SPEAKERS,
                    'meta': session.SUBTITLE}
        # Not undoable here and not sent back: they are the server's. Undo here no longer reverts what they
        # replaced: the person's earlier change to it is gone.
        history.forget(ops)
        with collab_state.remote(), history.amending(None):
            for op in ops:
                try:
                    docops.apply(document, [op])
                except docops.OpError as error:
                    log.warning('collab: a change did not fit the project: %s', error)
                    continue
                if op.get('op') == 'cue.insert':
                    segment = cue_ids.find(document['segments'], op['cue'].get('id'))
                    if segment is not None:
                        segment.update(journal.runtime('cue', segment['id']))
            selected = session.SUBTITLE.get('selected')
            if isinstance(selected, dict) and not any(s is selected for s in document['segments']):
                session.SUBTITLE['selected'] = False
            session.set_unsaved()


def websocket_url(server: str, doc: str) -> str:
    base = (server or '').rstrip('/')
    base = base.replace('https://', 'wss://', 1).replace('http://', 'ws://', 1)
    return f'{base}/ws/collab/{doc}'


class Connection(QObject):
    """One shared project's connection. `changed(kind)` passes on the engine's news ('state', 'document',
    'peers', 'locks', 'conflicts')."""

    changed = Signal(str)
    failed = Signal(str)

    def __init__(self, url: str, token: str, user_agent: str, live: Live | None = None, parent=None):
        super().__init__(parent)
        self.url, self.token, self.user_agent = url, token, user_agent
        self.engine = Engine(self._send, live or SessionLive())
        self.engine.listeners.append(self._engine_news)
        self.socket = QWebSocket('', parent=self)
        self.socket.connected.connect(self._connected)
        self.socket.disconnected.connect(self._disconnected)
        self.socket.textMessageReceived.connect(self._received)
        self._stopped = True
        self._attempt = 0
        self._retry = self._timer(self.start_socket)
        self._ping = self._timer(lambda: self._send({'type': 'ping'}), repeat=True)
        self._ping.setInterval(PING_SECONDS * 1000)
        self._gap = self._timer(self.engine.resync)
        self._gap.setInterval(RESYNC_SECONDS * 1000)
        journal.add_listener(self._journal_changes)

    def _timer(self, slot, repeat=False):
        timer = QTimer(self)
        timer.setSingleShot(not repeat)
        timer.timeout.connect(slot)
        return timer

    # --- Control -----------------------------------------------------------------------------------

    def start(self):
        self._stopped = False
        self._attempt = 0
        self.start_socket()

    def start_socket(self):
        if self._stopped or self.socket.state() != QAbstractSocket.SocketState.UnconnectedState:
            return
        request = QNetworkRequest(QUrl(self.url))
        request.setRawHeader(b'Authorization', f'Bearer {self.token}'.encode())
        request.setRawHeader(b'User-Agent', self.user_agent.encode())
        self.socket.open(request)

    def stop(self):
        """Disconnect for good (the project was closed). What is queued stays queued."""
        self._stopped = True
        self._retry.stop()
        self._ping.stop()
        self._gap.stop()
        journal.remove_listener(self._journal_changes)
        self.socket.close()

    @property
    def state(self):
        return self.engine.state

    # --- Socket ------------------------------------------------------------------------------------

    def _send(self, message):
        if self.socket.state() == QAbstractSocket.SocketState.ConnectedState:
            self.socket.sendTextMessage(json.dumps(message, ensure_ascii=False))

    def _connected(self):
        self._attempt = 0
        self._ping.start()
        self.engine.connected()

    def _disconnected(self):
        self._ping.stop()
        self._gap.stop()
        code = self.socket.closeCode().value
        self.engine.disconnected()
        if code in FINAL_CODES:
            self._stopped = True
            self.failed.emit(FINAL_CODES[code])
            return
        if not self._stopped:
            delay = BACKOFF_SECONDS[min(self._attempt, len(BACKOFF_SECONDS) - 1)]
            self._attempt += 1
            self._retry.start(delay * 1000)

    def _received(self, text):
        try:
            message = json.loads(text)
        except ValueError:
            log.warning('collab: a message that does not read')
            return
        self.engine.on_message(message)
        if self.engine.needs_resync and not self._gap.isActive():
            self._gap.start()

    # --- The project -------------------------------------------------------------------------------

    def _journal_changes(self, _ops, _undo):
        self.engine.local_changed()

    def _engine_news(self, kind, *args):
        self.changed.emit(kind)
