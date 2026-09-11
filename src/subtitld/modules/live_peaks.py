"""Live audio peak buffer for the in-progress record take.

Deliberately contains **no Qt**. `LiveTakeBuffer.append` is installed as
`AudioRecorder.chunk_callback`, so it runs on the recorder's *writer thread*
(see `recorder.AudioRecorder`'s threading docstring); anything Qt-shaped here
would be touched off the GUI thread. The GUI side polls `read_since()` from a
timer instead, so:

  * the writer thread never blocks on the GUI thread — it takes a lock for a
    couple of numpy reductions and an append, and never waits on anything;
  * the GUI thread never blocks on audio — it copies only the buckets it has
    not drawn yet.

A queued signal per audio chunk was rejected: it would post an event 15-30x a
second and couple the repaint rate to the audio block rate. This project has
already paid for that mistake once — unthrottled timeline work starved the
mixer thread through the GIL and produced audible dropouts.

The reduction is O(len(block)) and independent of how long the take has run:
audio is bucketed by a fixed *duration*, and capacity grows by doubling.
"""

import threading

import numpy as np

# Seconds of audio per min/max bucket. Finer than the committed rendering
# (DubPeaksWorker reduces a whole file to ~400 buckets), so the live preview
# never looks coarser than the waveform that eventually replaces it.
BUCKET_SECONDS = 0.010


class LiveTakeBuffer:
    """Min/max peak buckets accumulated from a live recording.

    Written by the recorder's writer thread via :meth:`append`, read by the
    GUI thread via :meth:`read_since`. Only `_mins` / `_maxs` / `_n` / `_carry`
    are shared, and every access to them holds `_lock`.
    """

    def __init__(self, samplerate=16000, bucket_seconds=BUCKET_SECONDS, vad=None):
        self.samplerate = int(samplerate or 16000)
        self.bucket_seconds = float(bucket_seconds)
        self._bucket = max(1, int(round(self.samplerate * self.bucket_seconds)))
        self._lock = threading.Lock()
        self._mins = np.zeros(4096, dtype=np.float32)
        self._maxs = np.zeros(4096, dtype=np.float32)
        self._n = 0
        self._carry = np.zeros(0, dtype=np.float32)
        self._peak = 0.0
        # Optional VadSegmenter fed the SAME blocks, so the live preview's cue
        # boundaries are the ones the real segmenter will emit rather than an
        # approximation of them. Owned here so the streaming ASR path — which
        # has no segmenter of its own — gets previews too.
        self._vad = vad
        self._vad_state = (False, 0.0, 0.0, 0.12)

    # -- writer thread ------------------------------------------------------
    def append(self, mono):
        """Reduce one audio block into buckets. Runs on the WRITER thread.

        Must stay cheap and allocation-light: a typical block is 512-1024
        frames, i.e. 3-7 buckets, which is a few microseconds of numpy.
        """
        if mono is None:
            return
        x = np.asarray(mono, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return
        # The VAD keeps its own sub-frame remainder, so it gets each block
        # exactly once — feeding it the carry-concatenated array would replay
        # the carry samples and shift every boundary it reports.
        fresh = x
        with self._lock:
            if self._carry.size:
                x = np.concatenate((self._carry, x))
            nb = x.size // self._bucket
            if nb:
                block = x[:nb * self._bucket].reshape(nb, self._bucket)
                self._ensure(nb)
                self._mins[self._n:self._n + nb] = block.min(axis=1)
                self._maxs[self._n:self._n + nb] = block.max(axis=1)
                self._n += nb
                local = float(np.abs(block).max())
                if local > self._peak:
                    self._peak = local
            # Whatever did not fill a bucket rides along to the next block, so
            # bucket boundaries stay aligned to the take's own sample clock.
            self._carry = x[nb * self._bucket:].copy()
            if self._vad is not None:
                try:
                    self._vad.push(fresh)
                    self._vad_state = self._vad.state()
                except Exception:
                    pass

    def _ensure(self, extra):
        """Grow capacity by doubling — amortised O(1) per appended bucket."""
        need = self._n + extra
        cap = self._mins.size
        if need <= cap:
            return
        while cap < need:
            cap *= 2
        self._mins = np.resize(self._mins, cap)
        self._maxs = np.resize(self._maxs, cap)

    # -- GUI thread ---------------------------------------------------------
    def read_since(self, start):
        """Return `(start, mins, maxs)` for buckets from `start` onward.

        Copies only what the caller has not seen, so a long take does not make
        each tick more expensive.
        """
        with self._lock:
            n = self._n
            if start >= n:
                return start, None, None
            return (start,
                    self._mins[start:n].copy(),
                    self._maxs[start:n].copy())

    def vad_state(self):
        """`(in_speech, speech_start_s, last_speech_s, pad_s)` or None."""
        if self._vad is None:
            return None
        with self._lock:
            return self._vad_state

    @property
    def bucket_count(self):
        with self._lock:
            return self._n

    @property
    def peak(self):
        with self._lock:
            return self._peak

    def duration(self):
        """Seconds of audio reduced so far (excludes the sub-bucket carry)."""
        with self._lock:
            return self._n * self.bucket_seconds
