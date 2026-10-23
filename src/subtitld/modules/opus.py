"""Opus, the voice codec of the editors' voice chat: libopus through ctypes

48 kHz mono, 20 ms frames (960 samples) of float audio. The encoder is set for speech, with in-band forward
error correction (a frame carries a rough copy of the one before, so one lost frame can be rebuilt from the
next) and discontinuous transmission (silence costs almost nothing). The decoder fills a lost frame from the
next one's correction data, or by concealment when there is none.

The library is found as libdatachannel is (modules/rtc.py): SUBTITLD_LIBOPUS; next to Subtitld in a package;
the system's. available() says whether one was.
"""

import ctypes
import ctypes.util
import logging
import os
import sys
import threading

log = logging.getLogger(__name__)

RATE = 48000
FRAME = 960            # 20 ms
MAX_PACKET = 1275      # the largest Opus packet for one frame
BITRATE = 32000

_APPLICATION_VOIP = 2048
_SET_BITRATE = 4002
_SET_COMPLEXITY = 4010
_SET_INBAND_FEC = 4012
_SET_PACKET_LOSS_PERC = 4014
_SET_DTX = 4016
_SET_SIGNAL = 4024
_SIGNAL_VOICE = 3001

_lib = None
_load_lock = threading.Lock()


def _names():
    if sys.platform.startswith('win'):
        return ['opus.dll', 'libopus-0.dll']
    if sys.platform == 'darwin':
        return ['libopus.0.dylib', 'libopus.dylib']
    return ['libopus.so.0', 'libopus.so']


def _candidates():
    if os.environ.get('SUBTITLD_LIBOPUS'):
        yield os.environ['SUBTITLD_LIBOPUS']
    roots = []
    if getattr(sys, '_MEIPASS', None):
        roots.append(sys._MEIPASS)
    here = os.path.dirname(os.path.abspath(__file__))
    roots += [os.path.join(here, '..', 'lib'), os.path.dirname(sys.executable)]
    for root in roots:
        for name in _names():
            yield os.path.join(root, name)
    found = ctypes.util.find_library('opus')
    if found:
        yield found
    yield from _names()  # by name, through the loader


def library():
    """The loaded libopus, or None when there is none here."""
    global _lib
    with _load_lock:
        if _lib is not None:
            return _lib or None
        for path in _candidates():
            if os.sep in path and not os.path.isfile(path):
                continue
            try:
                lib = ctypes.CDLL(path)
            except OSError:
                continue
            lib.opus_encoder_create.restype = ctypes.c_void_p
            lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
            lib.opus_encode_float.restype = ctypes.c_int32
            lib.opus_encode_float.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int,
                                              ctypes.c_char_p, ctypes.c_int32]
            lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
            lib.opus_decoder_create.restype = ctypes.c_void_p
            lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
            lib.opus_decode_float.restype = ctypes.c_int
            lib.opus_decode_float.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32,
                                              ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_int]
            lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
            lib.opus_get_version_string.restype = ctypes.c_char_p
            _lib = lib
            log.info('opus: %s from %s', lib.opus_get_version_string().decode(), path)
            return lib
        _lib = False
        return None


def available():
    return library() is not None


def _ctl(lib, encoder, request, value):
    # opus_encoder_ctl is variadic; its value goes as a C int.
    if lib.opus_encoder_ctl(ctypes.c_void_p(encoder), ctypes.c_int(request), ctypes.c_int(value)) != 0:
        raise RuntimeError(f'opus_encoder_ctl({request}) failed')


class Encoder:
    """Float frames of FRAME samples in, Opus packets out (b'' or a byte or two during silence, with DTX)."""

    def __init__(self, bitrate=BITRATE, expected_loss=10):
        self.lib = library()
        if self.lib is None:
            raise RuntimeError('libopus is not available')
        error = ctypes.c_int()
        self.state = self.lib.opus_encoder_create(RATE, 1, _APPLICATION_VOIP, ctypes.byref(error))
        if error.value != 0 or not self.state:
            raise RuntimeError(f'opus_encoder_create failed ({error.value})')
        for request, value in ((_SET_BITRATE, bitrate), (_SET_COMPLEXITY, 8), (_SET_SIGNAL, _SIGNAL_VOICE),
                               (_SET_INBAND_FEC, 1), (_SET_PACKET_LOSS_PERC, expected_loss), (_SET_DTX, 1)):
            _ctl(self.lib, self.state, request, value)
        self._out = ctypes.create_string_buffer(MAX_PACKET)

    def encode(self, frame):
        """`frame`: FRAME floats (a sequence or a numpy float32 array). Returns the packet."""
        pcm = (ctypes.c_float * FRAME)(*frame) if not hasattr(frame, 'ctypes') else frame.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        size = self.lib.opus_encode_float(self.state, pcm, FRAME, self._out, MAX_PACKET)
        if size < 0:
            raise RuntimeError(f'opus_encode_float failed ({size})')
        return self._out.raw[:size]

    def close(self):
        if self.state:
            self.lib.opus_encoder_destroy(self.state)
            self.state = None

    __del__ = close


class Decoder:
    """Opus packets in, float frames out; a lost one rebuilt from the next packet or concealed."""

    def __init__(self):
        self.lib = library()
        if self.lib is None:
            raise RuntimeError('libopus is not available')
        error = ctypes.c_int()
        self.state = self.lib.opus_decoder_create(RATE, 1, ctypes.byref(error))
        if error.value != 0 or not self.state:
            raise RuntimeError(f'opus_decoder_create failed ({error.value})')
        self._pcm = (ctypes.c_float * FRAME)()

    def decode(self, packet):
        """One frame from `packet`; None (lost): concealment."""
        return self._decode(packet, fec=False)

    def recover(self, next_packet):
        """The lost frame before `next_packet`, from that packet's correction data."""
        return self._decode(next_packet, fec=True)

    def _decode(self, packet, fec):
        data = packet if packet else None
        count = self.lib.opus_decode_float(self.state, data, len(packet) if packet else 0, self._pcm, FRAME, int(fec))
        if count < 0:
            raise RuntimeError(f'opus_decode_float failed ({count})')
        return list(self._pcm[:count])

    def close(self):
        if self.state:
            self.lib.opus_decoder_destroy(self.state)
            self.state = None

    __del__ = close
