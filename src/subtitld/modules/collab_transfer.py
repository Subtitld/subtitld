"""Media files from collaborator to collaborator (LIVE_COLLABORATION_PLAN.md: M14)

A file is asked for by hash from the collaborators who say they have it (`have`, modules/collab_sync.py),
over direct links (modules/rtc.py, set up through subtitld.cc's `rtc` messages). Its chunks are spread over
everyone who has it, a few at a time from each; each chunk is checked against its SHA-256 from the project's
media list before it is written, so a file can come from several people and resume after a break. When every
chunk is in, the whole file's hash is checked and it takes its place.

On the link, the asking side sends JSON text, {"op": "get", "hash", "chunks": [...]}, and the other side
answers with binary frames: HEADER (the file's hash, the chunk's index, the frame's offset in the chunk as
sent, that length, flags) and up to FRAME bytes. A chunk that zlib makes clearly smaller goes compressed.
{"op": "no", "hash"} means the other side won't send it.

A collaborator only sends what it announced, when the person turned sharing on for the project
(collab_state.STATE['share_media']); only project members can reach it (subtitld.cc passes `rtc` messages
within a project only).

Threads: libdatachannel calls back on its own threads; sending runs on a worker thread per link. What has to
reach the main thread (set-up messages to send, a finished file, news for the screen) goes through Bridge's
signals.
"""

import hashlib
import json
import logging
import os
import queue
import secrets
import shutil
import struct
import threading
import time
import zlib

from PySide6.QtCore import QObject, Signal

from subtitld.modules import rtc

log = logging.getLogger(__name__)

FRAME = 60 * 1024
HEADER = struct.Struct('>32sIIIB')
FLAG_ZLIB = 1
IN_FLIGHT_PER_LINK = 3
CHUNK_TIMEOUT = 60
# A collaborator who would not send a file is not asked for it again for this long.
REFUSAL_SECONDS = 60
# Links set up through a public STUN server, which only tells each side its own public address.
ICE_SERVERS = ('stun:stun.cloudflare.com:3478',)
COMPRESSIBLE = ('waveform', 'stem', 'dub', 'speaker_image')


class Bridge(QObject):
    """From libdatachannel's threads to the main one."""
    outgoing = Signal(str, object)   # (peer, payload) to send as an `rtc` message
    finished = Signal(str, str)      # (hash, path) a file is complete and checked
    failed = Signal(str, str)        # (hash, reason)
    news = Signal(str)               # (hash) progress or a change of state, for the screen


class Link:
    """One direct link with one collaborator: this side asks (offering) or answers."""

    def __init__(self, manager, peer, session, offering):
        self.manager, self.peer, self.session, self.offering = manager, peer, session, offering
        self.pc = rtc.PeerConnection(manager.ice_servers)
        self.pc.on_description = lambda sdp, kind: manager.signal(peer, {'s': session, 't': 'description', 'sdp': sdp, 'type': kind})
        self.pc.on_candidate = lambda cand, mid: manager.signal(peer, {'s': session, 't': 'candidate', 'candidate': cand, 'mid': mid})
        self.pc.on_state = self._state
        self.pc.on_channel = self._channel
        self.channel = None
        self.described = False   # the other side's description is applied: candidates can be
        self.early = []          # candidates that came before it
        self.open = threading.Event()
        self.alive = True
        self.requests = queue.Queue()
        self.control = queue.Queue()
        self.sender = threading.Thread(target=self._send_loop, name=f'collab-send-{peer}', daemon=True)
        self.sender.start()
        threading.Thread(target=self._control_loop, name=f'collab-control-{peer}', daemon=True).start()
        if offering:
            self._channel(self.pc.create_channel('media'))

    def _channel(self, channel):
        self.channel = channel
        channel.on_open = self._opened
        channel.on_message = self._message
        channel.on_closed = lambda: self.manager.link_lost(self)
        if channel.opened.is_set():
            self._opened()

    def _opened(self):
        log.debug('link %s %s: channel open', self.peer, self.session)
        self.open.set()
        self.manager.link_ready(self)

    def _state(self, state):
        log.debug('link %s %s: state %s', self.peer, self.session, state)
        if state in (rtc.FAILED, rtc.CLOSED, rtc.DISCONNECTED):
            self.manager.link_lost(self)

    def signal(self, payload):
        """A set-up message from the other side. Its description and candidates come from different threads
        there, in any order: a candidate before the description waits for it."""
        if payload.get('t') == 'description':
            self.pc.set_remote_description(payload.get('sdp') or '', payload.get('type') or '')
            self.described = True
            early, self.early = self.early, []
            for candidate, mid in early:
                self.pc.add_remote_candidate(candidate, mid)
        elif payload.get('t') == 'candidate' and payload.get('candidate'):
            if self.described:
                self.pc.add_remote_candidate(payload['candidate'], payload.get('mid') or '')
            else:
                self.early.append((payload['candidate'], payload.get('mid') or ''))

    def send_json(self, message):
        """Queued: libdatachannel must not be called from inside its own callbacks (a send from a message
        callback can stop the link for good), and requests are made there."""
        self.control.put(message)

    def _control_loop(self):
        while self.alive:
            try:
                message = self.control.get(timeout=1)
            except queue.Empty:
                continue
            if self.channel is not None and self.open.is_set():
                self.channel.send_text(json.dumps(message))

    def _message(self, data):
        if isinstance(data, str):
            try:
                message = json.loads(data)
            except ValueError:
                return
            if message.get('op') == 'get':
                log.debug('link %s %s: asked for %s', self.peer, self.session, message.get('chunks'))
                self.requests.put(message)
            elif message.get('op') == 'no':
                self.manager.refused(self, message.get('hash'))
            return
        if len(data) < HEADER.size:
            return
        file_hash, index, offset, total, flags = HEADER.unpack_from(data)
        self.manager.frame(self, file_hash.hex(), index, offset, total, flags, data[HEADER.size:])

    def _send_loop(self):
        """Answer requests, one chunk at a time, waiting while the link's buffer is full."""
        while self.alive:
            try:
                message = self.requests.get(timeout=1)
            except queue.Empty:
                continue
            file_hash = message.get('hash') or ''
            path = self.manager.shareable(file_hash)
            if path is None:
                self.send_json({'op': 'no', 'hash': file_hash})
                continue
            for index in message.get('chunks') or []:
                if not self.alive or not self._send_chunk(path, file_hash, int(index), message.get('chunk_size'),
                                                          message.get('kind')):
                    break

    def _send_chunk(self, path, file_hash, index, chunk_size, kind):
        size = int(chunk_size or 0)
        if size <= 0:
            return False
        try:
            with open(path, 'rb') as handle:
                handle.seek(index * size)
                payload = handle.read(size)
        except OSError:
            self.send_json({'op': 'no', 'hash': file_hash})
            return False
        flags = 0
        if kind in COMPRESSIBLE:
            packed = zlib.compress(payload, 1)
            if len(packed) < len(payload) * 0.9:
                payload, flags = packed, FLAG_ZLIB
        raw = bytes.fromhex(file_hash)
        for offset in range(0, len(payload), FRAME) or [0]:
            frame = HEADER.pack(raw, index, offset, len(payload), flags) + payload[offset:offset + FRAME]
            if not self.channel.send(frame):
                return False
        self.manager.sent(len(payload))
        log.debug('link %s %s: sent chunk %s', self.peer, self.session, index)
        return True

    def close(self):
        """Close the link. Not from libdatachannel's own callback (it can't delete a connection there):
        on a thread of its own."""
        self.alive = False
        threading.Thread(target=self.pc.close, daemon=True).start()


class Download:
    """One file coming in: where it goes, which chunks are in, which are on their way and from whom."""

    def __init__(self, media, destination):
        self.hash = media['hash']
        self.kind = media.get('kind', '')
        self.size = int(media['size'])
        self.chunk_size = int(media['chunk_size'])
        self.chunks = list(media['chunks'])
        self.destination = destination
        self.part = destination + '.part'
        self.progress_path = destination + '.part.json'
        self.done = set()
        self.in_flight = {}   # index -> (link, started)
        self.buffers = {}     # index -> bytearray
        self.links = set()
        self.lock = threading.Lock()
        self.received = 0
        self.sources = {}     # peer -> bytes they sent
        self.state = 'waiting'   # waiting, downloading, checking, done, failed
        self.error = ''
        self._load_progress()

    def _load_progress(self):
        try:
            with open(self.progress_path, encoding='utf-8') as handle:
                saved = json.load(handle)
        except (OSError, ValueError):
            return
        if saved.get('hash') == self.hash and os.path.isfile(self.part) and os.path.getsize(self.part) == self.size:
            self.done = {int(i) for i in saved.get('done') or [] if 0 <= int(i) < len(self.chunks)}

    def save_progress(self):
        try:
            with open(self.progress_path + '.tmp', 'w', encoding='utf-8') as handle:
                json.dump({'hash': self.hash, 'done': sorted(self.done)}, handle)
            os.replace(self.progress_path + '.tmp', self.progress_path)
        except OSError:
            pass

    def chunk_length(self, index):
        return min(self.chunk_size, self.size - index * self.chunk_size)

    def fraction(self):
        return len(self.done) / len(self.chunks) if self.chunks else 1.0

    def missing_bytes(self):
        return sum(self.chunk_length(i) for i in range(len(self.chunks)) if i not in self.done)

    def next_chunks(self, count):
        free = [i for i in range(len(self.chunks)) if i not in self.done and i not in self.in_flight]
        return free[:count]


class Manager(QObject):
    """A shared project's file transfers: links with collaborators, downloads, and answering their requests.

    `signal(peer, payload)` sends an `rtc` message (the engine's); `shareable(hash)` gives the local path of
    a file this app may send, or None; `holders(hash)` the peers who say they have one."""

    def __init__(self, send_rtc, shareable, holders, parent=None):
        super().__init__(parent)
        self.bridge = Bridge()
        self.bridge.outgoing.connect(send_rtc)
        self._shareable, self._holders = shareable, holders
        self.ice_servers = ICE_SERVERS
        self.links = {}        # session -> Link
        self.refusals = {}     # (peer, hash) -> when they refused
        self.early = {}        # session -> set-up messages that came before its offer
        self.downloads = {}    # hash -> Download
        self.lock = threading.RLock()
        self.bytes_sent = 0

    # --- Signalling ----------------------------------------------------------------------------------

    def signal(self, peer, payload):
        self.bridge.outgoing.emit(peer, payload)

    def on_rtc(self, peer, payload):
        """An `rtc` message from `peer` (main thread)."""
        session = payload.get('s')
        if not isinstance(session, str):
            return
        with self.lock:
            link = self.links.get(session)
            if link is None:
                if payload.get('t') != 'description' or payload.get('type') != 'offer':
                    # A candidate before the offer it belongs to: kept for it (a few, for a while).
                    waiting = self.early.setdefault(session, []) if len(self.early) < 64 else []
                    if len(waiting) < 32:
                        waiting.append(payload)
                    return
                try:
                    link = Link(self, peer, session, offering=False)
                except RuntimeError as error:
                    log.warning('collab transfer: no link: %s', error)
                    return
                self.links[session] = link
            waiting = self.early.pop(session, [])
        link.signal(payload)
        for early in waiting:
            link.signal(early)

    def shareable(self, file_hash):
        return self._shareable(file_hash)

    def sent(self, count):
        self.bytes_sent += count

    # --- Downloads -------------------------------------------------------------------------------------

    def download(self, media, destination):
        """Ask for file `media` (a media list entry with its `chunks`) to be written to `destination`.
        Raises OSError when there is not room for it."""
        with self.lock:
            existing = self.downloads.get(media['hash'])
            if existing is not None and existing.state in ('waiting', 'downloading', 'checking'):
                return existing
            download = Download(media, destination)
            folder = os.path.dirname(os.path.abspath(destination)) or '.'
            os.makedirs(folder, exist_ok=True)
            if shutil.disk_usage(folder).free < download.missing_bytes() + 64 * 1024 * 1024:
                raise OSError('not enough room')
            if not os.path.isfile(download.part) or os.path.getsize(download.part) != download.size:
                with open(download.part, 'wb') as handle:
                    handle.truncate(download.size)
                download.done.clear()
            self.downloads[download.hash] = download
        self.tick()
        return download

    def cancel(self, file_hash):
        with self.lock:
            download = self.downloads.pop(file_hash, None)
        if download is not None:
            download.state = 'failed'
            download.error = 'cancelled'
            self.bridge.news.emit(file_hash)

    def tick(self):
        """Every few seconds (main thread): links to holders where a download needs them, chunks that took
        too long given to someone else, and a finished one checked."""
        now = time.monotonic()
        for download in list(self.downloads.values()):
            if download.state not in ('waiting', 'downloading'):
                continue
            with download.lock:
                for index, (link, started) in list(download.in_flight.items()):
                    if now - started > CHUNK_TIMEOUT or not link.alive:
                        del download.in_flight[index]
                        download.buffers.pop(index, None)
            if len(download.done) == len(download.chunks):
                threading.Thread(target=self._complete, args=(download,), daemon=True).start()
                continue
            connected = {link.peer for link in download.links if link.alive}
            for peer in self._holders(download.hash):
                refused = self.refusals.get((peer, download.hash))
                if refused is not None and now - refused < REFUSAL_SECONDS:
                    continue
                if peer not in connected:
                    self._connect(download, peer)
            for link in list(download.links):
                if link.alive and link.open.is_set():
                    self._request(download, link)

    def _connect(self, download, peer):
        with self.lock:
            link = next((l for l in self.links.values() if l.peer == peer and l.offering and l.alive), None)
            if link is None:
                session = secrets.token_hex(8)
                try:
                    link = Link(self, peer, session, offering=True)
                except RuntimeError as error:
                    download.state, download.error = 'failed', str(error)
                    self.bridge.news.emit(download.hash)
                    return
                self.links[session] = link
                log.debug('connecting to %s (%s)', peer, session)
        download.links.add(link)

    def link_ready(self, link):
        """The link's channel is open: ask it for chunks of what it is in for."""
        for download in list(self.downloads.values()):
            if link in download.links:
                self._request(download, link)

    def link_lost(self, link):
        log.debug('link %s %s: lost', link.peer, link.session)
        if not link.alive:
            return
        link.alive = False
        with self.lock:
            self.links.pop(link.session, None)
        for download in list(self.downloads.values()):
            with download.lock:
                for index, (holder, _started) in list(download.in_flight.items()):
                    if holder is link:
                        del download.in_flight[index]
                        download.buffers.pop(index, None)
            download.links.discard(link)
        link.close()

    def refused(self, link, file_hash):
        self.refusals[(link.peer, file_hash)] = time.monotonic()
        download = self.downloads.get(file_hash)
        if download is None:
            return
        with download.lock:
            for index, (holder, _started) in list(download.in_flight.items()):
                if holder is link:
                    del download.in_flight[index]
            download.links.discard(link)

    def _request(self, download, link):
        with download.lock:
            mine = sum(1 for holder, _ in download.in_flight.values() if holder is link)
            wanted = download.next_chunks(IN_FLIGHT_PER_LINK - mine)
            now = time.monotonic()
            for index in wanted:
                download.in_flight[index] = (link, now)
            if wanted and download.state == 'waiting':
                download.state = 'downloading'
        if wanted:
            log.debug('asking %s for %s', link.peer, wanted)
            link.send_json({'op': 'get', 'hash': download.hash, 'chunks': wanted, 'chunk_size': download.chunk_size,
                            'kind': download.kind})

    def frame(self, link, file_hash, index, offset, total, flags, data):
        """A frame of a chunk (libdatachannel's thread)."""
        download = self.downloads.get(file_hash)
        if download is None or not 0 <= index < len(download.chunks):
            return
        with download.lock:
            if index in download.done:
                return
            buffer = download.buffers.setdefault(index, bytearray())
            if offset != len(buffer):
                log.debug('chunk %s of %s: frame at %s, expected %s (from %s)', index, file_hash[:8], offset,
                          len(buffer), link.peer)
                download.buffers.pop(index, None)  # out of step: ask for it again
                download.in_flight.pop(index, None)
                return
            buffer += data
            if len(buffer) < total:
                return
            payload = bytes(download.buffers.pop(index))
        if flags & FLAG_ZLIB:
            try:
                payload = zlib.decompress(payload)
            except zlib.error:
                payload = b''
        if hashlib.sha256(payload).hexdigest() != download.chunks[index] or len(payload) != download.chunk_length(index):
            log.warning('collab transfer: chunk %s of %s did not match; asking again', index, file_hash[:12])
            with download.lock:
                download.in_flight.pop(index, None)
            self._request(download, link)
            return
        with open(download.part, 'r+b') as handle:
            handle.seek(index * download.chunk_size)
            handle.write(payload)
        log.debug('chunk %s of %s in, from %s', index, file_hash[:8], link.peer)
        with download.lock:
            download.done.add(index)
            download.in_flight.pop(index, None)
            download.received += len(payload)
            download.sources[link.peer] = download.sources.get(link.peer, 0) + len(payload)
            count = len(download.done)
        if count % 8 == 0 or count == len(download.chunks):
            download.save_progress()
        self.bridge.news.emit(file_hash)
        if count == len(download.chunks):
            threading.Thread(target=self._complete, args=(download,), daemon=True).start()
        else:
            self._request(download, link)

    def _complete(self, download):
        """Every chunk is in: check the whole file, then it takes its place."""
        with download.lock:
            if download.state in ('checking', 'done'):
                return
            download.state = 'checking'
        self.bridge.news.emit(download.hash)
        whole = hashlib.sha256()
        with open(download.part, 'rb') as handle:
            while block := handle.read(8 * 1024 * 1024):
                whole.update(block)
        if whole.hexdigest() != download.hash:
            download.state, download.error = 'failed', 'checksum'
            download.done.clear()
            download.save_progress()
            self.bridge.failed.emit(download.hash, 'checksum')
            self.bridge.news.emit(download.hash)
            return
        os.replace(download.part, download.destination)
        try:
            os.remove(download.progress_path)
        except OSError:
            pass
        download.state = 'done'
        self.bridge.finished.emit(download.hash, download.destination)
        self.bridge.news.emit(download.hash)

    def close(self):
        with self.lock:
            links = list(self.links.values())
            self.links.clear()
        for link in links:
            link.close()
        for download in self.downloads.values():
            download.save_progress()
