"""The editors' voice chat: packets and the jitter buffer (LIVE_COLLABORATION_PLAN.md, "Editors' chat")

Voice goes as Opus frames (modules/opus.py) over an unreliable, unordered data channel (modules/rtc.py): a
frame that is lost is not sent again, it would come too late. Each packet is a version byte, a 16-bit
sequence number and one 20 ms Opus frame. The receiving side keeps a few frames in hand (JitterBuffer), plays
one every 20 ms, rebuilds a lost frame from the next one's correction data, conceals a gap it can't rebuild,
and goes quiet when the other person stops talking (push-to-talk: no packets at all).

Qt-free and free of audio devices: the capture and the playback feed and drain these.
"""

import struct

from subtitld.modules import opus

VERSION = 1
HEADER = struct.Struct('>BH')
TARGET = 3            # frames kept in hand before playing (60 ms)
MAX_HELD = 15         # beyond this the buffer drops the oldest (300 ms): a link that caught up all at once
CONCEAL = 3           # missing frames concealed in a row before going quiet (the talker stopped)


def packet(seq, payload):
    return HEADER.pack(VERSION, seq & 0xFFFF) + payload


def unpack(data):
    """(seq, Opus payload), or None when it isn't a voice packet."""
    if len(data) < HEADER.size:
        return None
    version, seq = HEADER.unpack_from(data)
    if version != VERSION:
        return None
    return seq, bytes(data[HEADER.size:])


def _after(a, b):
    """Whether sequence number `a` comes after `b` (16 bits, wrapping)."""
    return 0 < ((a - b) & 0xFFFF) < 0x8000


class JitterBuffer:
    """One talker's frames, in order, at the pace they are played. push() as packets come (any thread that
    holds no other lock of ours: the buffer has none, so callers serialize); pop() every 20 ms from the
    playback: a frame of floats, or None when this talker is quiet."""

    def __init__(self, decoder=None):
        self.decoder = decoder or opus.Decoder()
        self.held = {}          # seq -> payload
        self.next = None        # the seq to play next, while playing
        self.missing = 0        # frames missing in a row
        self.stats = {'played': 0, 'recovered': 0, 'concealed': 0, 'late': 0, 'dropped': 0}

    def push(self, seq, payload):
        if self.next is not None and not _after(seq, self.next) and seq != self.next:
            self.stats['late'] += 1
            return
        self.held[seq] = payload
        while len(self.held) > MAX_HELD:
            oldest = self._oldest()
            del self.held[oldest]
            self.stats['dropped'] += 1
            if self.next == oldest:
                self.next = (oldest + 1) & 0xFFFF

    def pop(self):
        if self.next is None:
            if len(self.held) < TARGET:
                return None
            self.next = self._oldest()
            self.missing = 0
        seq = self.next
        self.next = (seq + 1) & 0xFFFF
        if seq in self.held:
            self.missing = 0
            self.stats['played'] += 1
            return self.decoder.decode(self.held.pop(seq))
        following = self.held.get(self.next)
        if following is not None:
            self.missing = 0
            self.stats['recovered'] += 1
            return self.decoder.recover(following)
        self.missing += 1
        if self.missing > CONCEAL or not self.held and self.missing > 1:
            # The talker stopped (or the link did): quiet, and start again with the next packets.
            self.next = None
            self.held.clear()
            return None
        self.stats['concealed'] += 1
        return self.decoder.decode(None)

    def _oldest(self):
        """The earliest held sequence number (16 bits wrap: compared around one of them)."""
        reference = next(iter(self.held))
        return min(self.held, key=lambda seq: (seq - reference + 0x8000) & 0xFFFF)
