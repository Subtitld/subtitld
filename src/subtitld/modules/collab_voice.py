"""The editors' voice call (LIVE_COLLABORATION_PLAN.md, "Editors' chat")

Everyone in the call has a direct link with everyone else in it, a connection of its own for voice (a large
media transfer on the same one would hold voice back), carrying one unreliable, unordered channel of Opus
frames (modules/voice.py). Set-up goes through subtitld.cc like the media links', as `rtc` messages marked
`voice`. Of two people, the one whose connection ID sorts first offers.

Push-to-talk: the microphone is read all the time the call is on, but frames go out only while the person
talks (a key held, the button held, or latched on). What comes in from each collaborator goes through their
own jitter buffer, and the playback mixes them.

The audio devices are sounddevice streams, 48 kHz mono, 20 ms blocks; tests give their own `audio` (frames in,
frames out) instead. Callbacks from libdatachannel and from the audio devices come on their threads; nothing
here touches Qt.
"""

import logging
import threading
import time

from subtitld.modules import opus, rtc, voice

log = logging.getLogger(__name__)

SPEAKING_SECONDS = 0.3      # someone is speaking while their frames came within this long
MANY = 6                    # more people than this in a call: links may suffer (a note, no limit)
STUN = ('stun:stun.cloudflare.com:3478',)


class Link:
    """The voice link with one collaborator."""

    def __init__(self, call, peer, offering):
        self.call, self.peer = call, peer
        self.pc = rtc.PeerConnection(call.ice_servers())
        self.pc.on_description = lambda sdp, kind: call.signal(peer, {'voice': 1, 't': 'description', 'sdp': sdp,
                                                                      'type': kind})
        self.pc.on_candidate = lambda cand, mid: call.signal(peer, {'voice': 1, 't': 'candidate',
                                                                    'candidate': cand, 'mid': mid})
        self.pc.on_channel = self._channel
        self.channel = None
        self.early = []          # candidates that came before the description
        self.described = offering
        if offering:
            self._channel(self.pc.create_channel('voice', unreliable=True))

    def _channel(self, channel):
        self.channel = channel
        channel.on_message = lambda data: self.call.received(self.peer, data)

    def on_signal(self, payload):
        if payload.get('t') == 'description':
            self.pc.set_remote_description(payload.get('sdp', ''), payload.get('type', ''))
            self.described = True
            for candidate, mid in self.early:
                self.pc.add_remote_candidate(candidate, mid)
            self.early.clear()
        elif payload.get('t') == 'candidate':
            if self.described:
                self.pc.add_remote_candidate(payload.get('candidate', ''), payload.get('mid', ''))
            else:
                self.early.append((payload.get('candidate', ''), payload.get('mid', '')))

    def send(self, data):
        channel = self.channel
        if channel is not None and channel.opened.is_set() and not channel.closed.is_set():
            try:
                channel.send(data, timeout=0)
            except Exception:  # noqa: BLE001 (a link going away)
                pass

    def close(self):
        # Never from a libdatachannel callback: closing waits for its threads.
        threading.Thread(target=self.pc.close, daemon=True).start()


class Call:
    """This person's part in the project's voice call. `signal(peer, payload)` sends set-up through
    subtitld.cc; `relays()` gives subtitld.cc's TURN relay URLs (modules.collab_transfer.relay_urls)."""

    def __init__(self, signal, relays=lambda: (), audio=None):
        self.signal, self._relays = signal, relays
        self.audio = audio                 # None: sounddevice, opened by start()
        self.links = {}                    # peer -> Link
        self.buffers = {}                  # peer -> JitterBuffer
        self.heard = {}                    # peer -> when their last frame came
        self.lock = threading.Lock()
        self.encoder = None
        self.seq = 0
        self.held = False                  # the push-to-talk key or button is down
        self.latched = False               # talking without holding
        self.on = False
        self._streams = []

    def ice_servers(self):
        return STUN + tuple(self._relays())

    # --- Joining -----------------------------------------------------------------------------------------

    def start(self, input_device=None, output_device=None):
        """Join: the microphone and the speakers open (or the test's audio)."""
        if self.on:
            return
        self.encoder = opus.Encoder()
        self.on = True
        if self.audio is None:
            self._open_devices(input_device, output_device)

    def stop(self):
        """Leave: every link closes, the devices too."""
        self.on = False
        self.held = self.latched = False
        for stream in self._streams:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        self._streams = []
        with self.lock:
            links, self.links = list(self.links.values()), {}
            self.buffers.clear()
            self.heard.clear()
        for link in links:
            link.close()

    def _open_devices(self, input_device, output_device):
        import numpy as np
        import sounddevice as sd

        def captured(indata, frames, _time, _status):
            self.captured(indata[:, 0].astype(np.float32, copy=False))

        def playing(outdata, frames, _time, _status):
            outdata[:, 0] = self.playback(frames)

        inp = sd.InputStream(samplerate=opus.RATE, channels=1, dtype='float32', blocksize=opus.FRAME,
                             device=input_device, callback=captured, latency='low')
        out = sd.OutputStream(samplerate=opus.RATE, channels=1, dtype='float32', blocksize=opus.FRAME,
                              device=output_device, callback=playing, latency='low')
        inp.start()
        out.start()
        self._streams = [inp, out]

    def sync(self, me, others):
        """The collaborators in the call now (connection IDs): links with the new ones, none with those who
        left. Of two, the one whose ID sorts first offers."""
        if not self.on:
            return
        with self.lock:
            gone = [peer for peer in self.links if peer not in others]
            for peer in gone:
                self.links.pop(peer).close()
                self.buffers.pop(peer, None)
                self.heard.pop(peer, None)
            for peer in others:
                if peer not in self.links and me < peer:
                    self.links[peer] = Link(self, peer, offering=True)

    def on_rtc(self, peer, payload):
        """Set-up from a collaborator (an `rtc` message marked `voice`)."""
        if not self.on:
            return
        with self.lock:
            link = self.links.get(peer)
            if link is None or (payload.get('t') == 'description' and payload.get('type') == 'offer'):
                # Their offer starts a link (afresh, when they started over).
                if link is not None:
                    link.close()
                link = self.links[peer] = Link(self, peer, offering=False)
                self.buffers.pop(peer, None)
        link.on_signal(payload)

    # --- Sound -------------------------------------------------------------------------------------------

    @property
    def talking(self):
        return self.on and (self.held or self.latched)

    def captured(self, frame):
        """A 20 ms block from the microphone (audio thread): sent while this person talks."""
        if not self.talking or self.encoder is None:
            return
        data = voice.packet(self.seq, self.encoder.encode(frame))
        self.seq = (self.seq + 1) & 0xFFFF
        for link in list(self.links.values()):
            link.send(data)

    def received(self, peer, data):
        parsed = voice.unpack(data)
        if parsed is None:
            return
        with self.lock:
            buffer = self.buffers.get(peer)
            if buffer is None:
                buffer = self.buffers[peer] = voice.JitterBuffer()
            buffer.push(*parsed)
            self.heard[peer] = time.monotonic()

    def playback(self, frames=opus.FRAME):
        """The next block of everyone's voice, mixed (audio thread): a float32 array of `frames`."""
        import numpy as np

        mixed = np.zeros(frames, dtype=np.float32)
        with self.lock:
            for buffer in self.buffers.values():
                frame = buffer.pop()
                if frame:
                    count = min(frames, len(frame))
                    mixed[:count] += np.asarray(frame[:count], dtype=np.float32)
        np.clip(mixed, -1.0, 1.0, out=mixed)
        return mixed

    def speaking(self):
        """The collaborators heard in the last moment."""
        now = time.monotonic()
        with self.lock:
            return {peer for peer, when in self.heard.items() if now - when < SPEAKING_SECONDS}
