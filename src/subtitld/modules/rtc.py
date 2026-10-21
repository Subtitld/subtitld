"""Direct links between collaborators: WebRTC data channels through libdatachannel's C API (ctypes)

Live co-editing sends media files from one collaborator to another directly (modules/collab_transfer.py).
libdatachannel does the work: ICE through routers (libjuice), encryption (DTLS), and reliable, ordered
delivery (SCTP). The link is set up by exchanging a description and candidates through subtitld.cc (the
`rtc` messages of modules/collab_sync.py).

The library is found, in order: SUBTITLDCC_LIBDATACHANNEL; next to Subtitld in a package; the system's; in a
checkout, the build from packaging/libdatachannel. available() says whether one was.

Its callbacks run on its own threads. Nothing here touches Qt or the project: callers hand on to the main
thread what has to go there.

SCTP sends acknowledgements at once instead of delaying them, which keeps a link fast when packets get lost
(the transport spike in LIVE_COLLABORATION_PLAN.md: 0.6 → 2.6 MB/s at 40 ms and 0.05% loss). The spike's
other settings, 8 MB buffers and a short minimum retransmission timeout, are off: either one stalled links
now and then once several shared the process. SUBTITLDCC_RTC_SCTP (buffers,rto,sack) chooses them, to
look into that.
"""

import ctypes
import ctypes.util
import logging
import os
import sys
import threading
import time

log = logging.getLogger(__name__)

# rtcState
NEW, CONNECTING, CONNECTED, DISCONNECTED, FAILED, CLOSED = range(6)
# rtcGatheringState
GATHERING_COMPLETE = 2
MAX_MESSAGE_SIZE = 256 * 1024


class _Configuration(ctypes.Structure):
    _fields_ = [('iceServers', ctypes.POINTER(ctypes.c_char_p)), ('iceServersCount', ctypes.c_int),
                ('proxyServer', ctypes.c_char_p), ('bindAddress', ctypes.c_char_p),
                ('certificateType', ctypes.c_int), ('iceTransportPolicy', ctypes.c_int),
                ('enableIceTcp', ctypes.c_bool), ('enableIceUdpMux', ctypes.c_bool),
                ('disableAutoNegotiation', ctypes.c_bool), ('forceMediaTransport', ctypes.c_bool),
                ('portRangeBegin', ctypes.c_uint16), ('portRangeEnd', ctypes.c_uint16),
                ('mtu', ctypes.c_int), ('maxMessageSize', ctypes.c_int)]


class _SctpSettings(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int) for name in (
        'recvBufferSize', 'sendBufferSize', 'maxChunksOnQueue', 'initialCongestionWindow', 'maxBurst',
        'congestionControlModule', 'delayedSackTimeMs', 'minRetransmitTimeoutMs', 'maxRetransmitTimeoutMs',
        'initialRetransmitTimeoutMs', 'maxRetransmitAttempts', 'heartbeatIntervalMs')]


_DESCRIPTION = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p)
_CANDIDATE = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p)
_STATE = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
_CHANNEL = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
_OPEN = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)
_MESSAGE = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)

_lib = None
_load_lock = threading.Lock()
_peers = {}      # pc id -> PeerConnection
_channels = {}   # dc id -> DataChannel
_registry_lock = threading.Lock()


def _names():
    if sys.platform.startswith('win'):
        return ['datachannel.dll', 'libdatachannel.dll']
    if sys.platform == 'darwin':
        return ['libdatachannel.dylib']
    return ['libdatachannel.so']


def _candidates():
    if os.environ.get('SUBTITLDCC_LIBDATACHANNEL'):
        yield os.environ['SUBTITLDCC_LIBDATACHANNEL']
    roots = []
    if getattr(sys, '_MEIPASS', None):
        roots.append(sys._MEIPASS)
    here = os.path.dirname(os.path.abspath(__file__))
    roots += [os.path.join(here, '..', 'lib'), os.path.dirname(sys.executable)]
    for root in roots:
        for name in _names():
            yield os.path.join(root, name)
    found = ctypes.util.find_library('datachannel')
    if found:
        yield found
    # A checkout: the build of packaging/libdatachannel.
    checkout = os.path.normpath(os.path.join(here, '..', '..', '..', 'packaging', 'libdatachannel', 'dist'))
    if os.path.isdir(checkout):
        for folder in sorted(os.listdir(checkout)):
            for name in _names():
                yield os.path.join(checkout, folder, name)


def library():
    """The loaded library, or None when there is none here."""
    global _lib
    with _load_lock:
        if _lib is not None:
            return _lib or None
        for path in _candidates():
            if not os.path.isfile(path) and os.sep in path:
                continue
            try:
                lib = ctypes.CDLL(path)
            except OSError as error:
                log.info('rtc: %s did not load: %s', path, error)
                continue
            _setup(lib)
            _lib = lib
            log.info('rtc: libdatachannel from %s', path)
            return lib
        _lib = False
        return None


def available():
    return library() is not None


def _setup(lib):
    lib.rtcSendMessage.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.rtcSetRemoteDescription.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p]
    lib.rtcAddRemoteCandidate.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p]
    lib.rtcCreateDataChannel.argtypes = [ctypes.c_int, ctypes.c_char_p]
    settings = _SctpSettings()
    tuned = os.environ.get('SUBTITLDCC_RTC_SCTP', 'sack').split(',')
    if 'buffers' in tuned:
        settings.recvBufferSize = settings.sendBufferSize = 8 * 1024 * 1024
    if 'rto' in tuned:
        settings.minRetransmitTimeoutMs = 100
        settings.initialRetransmitTimeoutMs = 200
    if 'sack' in tuned:
        settings.delayedSackTimeMs = -1
    lib.rtcSetSctpSettings(ctypes.byref(settings))
    if os.environ.get('SUBTITLDCC_RTC_LOG'):
        # libdatachannel's own log on stdout: 1 fatal … 6 verbose.
        lib.rtcInitLogger(int(os.environ['SUBTITLDCC_RTC_LOG']), None)
    for name, kind, function in _CALLBACKS:
        globals()[name] = kind(function)


# One callback of each kind for every connection and channel, dispatching by ID: a ctypes callback must
# live as long as the library may call it.
def _on_description(pc, sdp, kind, _ptr):
    peer = _peers.get(pc)
    if peer is not None and peer.on_description:
        peer.on_description(sdp.decode(), kind.decode())


def _on_candidate(pc, candidate, mid, _ptr):
    peer = _peers.get(pc)
    if peer is not None and peer.on_candidate:
        peer.on_candidate(candidate.decode(), mid.decode() if mid else '')


def _on_state(pc, state, _ptr):
    peer = _peers.get(pc)
    if peer is not None:
        peer.state = state
        if peer.on_state:
            peer.on_state(state)


def _on_channel(pc, dc, _ptr):
    peer = _peers.get(pc)
    if peer is not None:
        channel = DataChannel(dc, peer)
        if peer.on_channel:
            peer.on_channel(channel)


def _on_open(dc, _ptr):
    channel = _channels.get(dc)
    if channel is not None:
        channel.opened.set()
        if channel.on_open:
            channel.on_open()


def _on_closed(dc, _ptr):
    channel = _channels.get(dc)
    if channel is not None:
        channel.closed.set()
        if channel.on_closed:
            channel.on_closed()


def _on_message(dc, data, size, _ptr):
    channel = _channels.get(dc)
    if channel is None:
        return
    if size < 0:  # text, NUL-terminated
        channel.deliver(ctypes.string_at(data).decode('utf-8', 'replace'))
    else:
        channel.deliver(ctypes.string_at(data, size))


def _on_low(dc, _ptr):
    channel = _channels.get(dc)
    if channel is not None:
        channel.low.set()


_CALLBACKS = [('_cb_description', _DESCRIPTION, _on_description), ('_cb_candidate', _CANDIDATE, _on_candidate),
              ('_cb_state', _STATE, _on_state), ('_cb_channel', _CHANNEL, _on_channel),
              ('_cb_open', _OPEN, _on_open), ('_cb_closed', _OPEN, _on_closed),
              ('_cb_message', _MESSAGE, _on_message), ('_cb_low', _OPEN, _on_low)]


class PeerConnection:
    """One link with one collaborator. Set the callbacks, then create a channel (the offering side) or
    wait for on_channel (the answering side). Callbacks come on the library's threads."""

    def __init__(self, ice_servers=()):
        lib = library()
        if lib is None:
            raise RuntimeError('libdatachannel is not available')
        self.lib = lib
        self.on_description = self.on_candidate = self.on_state = self.on_channel = None
        self.state = NEW
        servers = [server.encode() for server in ice_servers]
        array = (ctypes.c_char_p * max(1, len(servers)))(*servers)
        config = _Configuration()
        config.iceServers = array
        config.iceServersCount = len(servers)
        config.maxMessageSize = MAX_MESSAGE_SIZE
        self.id = lib.rtcCreatePeerConnection(ctypes.byref(config))
        if self.id < 0:
            raise RuntimeError(f'rtcCreatePeerConnection failed ({self.id})')
        with _registry_lock:
            _peers[self.id] = self
        lib.rtcSetLocalDescriptionCallback(self.id, _cb_description)  # noqa: F821 (made by _setup)
        lib.rtcSetLocalCandidateCallback(self.id, _cb_candidate)  # noqa: F821
        lib.rtcSetStateChangeCallback(self.id, _cb_state)  # noqa: F821
        lib.rtcSetDataChannelCallback(self.id, _cb_channel)  # noqa: F821

    def set_remote_description(self, sdp, kind):
        return self.lib.rtcSetRemoteDescription(self.id, sdp.encode(), kind.encode())

    def add_remote_candidate(self, candidate, mid=''):
        return self.lib.rtcAddRemoteCandidate(self.id, candidate.encode(), mid.encode() if mid else None)

    def create_channel(self, label):
        dc = self.lib.rtcCreateDataChannel(self.id, label.encode())
        if dc < 0:
            raise RuntimeError(f'rtcCreateDataChannel failed ({dc})')
        return DataChannel(dc, self)

    def close(self):
        with _registry_lock:
            if _peers.pop(self.id, None) is None:
                return
            for dc in [dc for dc, channel in _channels.items() if channel.peer is self]:
                _channels.pop(dc, None)
        self.lib.rtcDeletePeerConnection(self.id)


class DataChannel:
    """A reliable, ordered channel. on_message gets bytes (or str for text); send() waits while too much is
    buffered (call it from a worker thread, not from a callback)."""

    HIGH_WATER = 16 * 1024 * 1024
    LOW_WATER = 4 * 1024 * 1024

    def __init__(self, dc, peer):
        self.id, self.peer, self.lib = dc, peer, peer.lib
        self.on_open = self.on_closed = None
        self._on_message = None
        self._early = []          # messages that came before on_message was set
        self._early_lock = threading.Lock()
        self.opened, self.closed, self.low = threading.Event(), threading.Event(), threading.Event()
        with _registry_lock:
            _channels[dc] = self
        self.lib.rtcSetOpenCallback(dc, _cb_open)  # noqa: F821
        self.lib.rtcSetClosedCallback(dc, _cb_closed)  # noqa: F821
        self.lib.rtcSetMessageCallback(dc, _cb_message)  # noqa: F821
        self.lib.rtcSetBufferedAmountLowThreshold(dc, self.LOW_WATER)
        self.lib.rtcSetBufferedAmountLowCallback(dc, _cb_low)  # noqa: F821

    @property
    def on_message(self):
        return self._on_message

    @on_message.setter
    def on_message(self, handler):
        """A channel the other side opened delivers as soon as it exists: what came before its handler was set
        goes to the handler then."""
        with self._early_lock:
            self._on_message = handler
            early, self._early = self._early, []
        for message in early:
            handler(message)

    def deliver(self, message):
        with self._early_lock:
            handler = self._on_message
            if handler is None:
                self._early.append(message)
                return
        handler(message)

    def buffered(self):
        return self.lib.rtcGetBufferedAmount(self.id)

    def send(self, data, timeout=60):
        """Send bytes (at most MAX_MESSAGE_SIZE), waiting while the buffer is full. False if it closed or stayed
        full for `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while self.buffered() > self.HIGH_WATER:
            if self.closed.is_set() or time.monotonic() > deadline:
                return False
            self.low.wait(0.05)
            self.low.clear()
        return self.lib.rtcSendMessage(self.id, data, len(data)) >= 0

    def send_text(self, text):
        return self.lib.rtcSendMessage(self.id, text.encode(), -1) >= 0
