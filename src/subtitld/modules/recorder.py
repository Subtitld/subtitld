"""Microphone recording for Record mode.

Captures the chosen (or default) input device to a **16 kHz mono WAV** — the
exact format Subtitld's ASR providers expect — so a recording can be
transcribed live (per-utterance, see :mod:`subtitld.modules.live_transcribe`)
and/or become the project's audio.

Threading model (so the audio callback never blocks on disk or ASR):

    sounddevice callback  ──put──▶  Queue  ──get──▶  writer thread
                                                       ├─ SoundFile.write()
                                                       ├─ RMS level meter
                                                       └─ chunk_callback(...)  (live transcription)

The callback only copies + enqueues. All the real work is in
``_process_block`` on the writer thread — which is also what the tests drive
directly with synthetic frames, so the whole pipeline is verifiable without any
hardware. ``sounddevice`` is imported defensively: on a box with no working
audio backend the module still imports (``sd is None``) and recording simply
reports that no input is available.
"""

from __future__ import annotations

import os
import queue
import threading

import numpy as np

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - depends on the host audio stack
    sd = None

import soundfile as sf


SAMPLE_RATE = 16000
CHANNELS = 1
# Sentinel pushed on the queue to end the writer thread cleanly.
_STOP = object()


def list_input_devices() -> list[tuple[int, str]]:
    """`[(index, name), ...]` for every device with input channels. Empty when
    sounddevice is unavailable or query fails."""
    if sd is None:
        return []
    devices = []
    try:
        for index, dev in enumerate(sd.query_devices()):
            if dev.get('max_input_channels', 0) > 0:
                devices.append((index, dev.get('name', f'Input {index}')))
    except Exception:
        return []
    return devices


def default_input_device() -> int | None:
    """Index of the default input device, or None."""
    if sd is None:
        return None
    try:
        dev = sd.default.device
        index = dev[0] if isinstance(dev, (list, tuple)) else dev
        return int(index) if index is not None and int(index) >= 0 else None
    except Exception:
        return None


def input_available() -> bool:
    return bool(list_input_devices())


class AudioRecorder:
    """Record the microphone to a 16 kHz mono WAV.

    Parameters
    ----------
    output_path : str
        Destination WAV (PCM_16). Parent dirs are created.
    device : int | None
        Input device index (see :func:`list_input_devices`); None = default.
    chunk_callback : callable | None
        Called on the writer thread with each captured block as a float32 mono
        ``np.ndarray`` — used by live transcription and can be None for a plain
        recording.
    """

    def __init__(self, output_path, device=None, chunk_callback=None,
                 samplerate=SAMPLE_RATE):
        self.output_path = str(output_path)
        self.device = device
        self.chunk_callback = chunk_callback
        self.samplerate = int(samplerate)

        self._sf = None
        self._stream = None
        self._queue: queue.Queue = queue.Queue()
        self._writer = None
        self._lock = threading.Lock()

        self._frames_written = 0
        self._level = 0.0
        self._recording = False
        self._paused = False
        self._error = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        """Open the WAV, spin up the writer thread, then start capture.

        Raises RuntimeError if the input device can't be opened (no mic /
        backend). The WAV + writer are torn down again so ``start`` can be
        retried cleanly.
        """
        if self._recording:
            return
        parent = os.path.dirname(self.output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._sf = sf.SoundFile(self.output_path, mode='w', samplerate=self.samplerate,
                                channels=CHANNELS, subtype='PCM_16')
        self._frames_written = 0
        self._level = 0.0
        self._error = None
        self._recording = True
        self._paused = False
        self._writer = threading.Thread(target=self._writer_loop, name='recorder-writer',
                                        daemon=True)
        self._writer.start()

        if sd is not None:
            try:
                self._stream = sd.InputStream(
                    samplerate=self.samplerate, channels=CHANNELS, dtype='float32',
                    device=self.device, callback=self._sd_callback,
                )
                self._stream.start()
            except Exception as exc:
                # Roll back so the recorder is left in a clean, retriable state.
                self._recording = False
                self._queue.put(_STOP)
                if self._writer is not None:
                    self._writer.join(timeout=2)
                    self._writer = None
                self._close_file()
                try:
                    if os.path.exists(self.output_path):
                        os.unlink(self.output_path)
                except OSError:
                    pass
                raise RuntimeError(f'could not open input device: {exc}') from exc

    def _sd_callback(self, indata, frames, time_info, status):  # pragma: no cover - hw
        # Keep this minimal: copy the mono column and hand off. Never do disk /
        # ASR work here — it runs in the real-time audio thread.
        if self._paused:
            return  # dropped at the source so paused audio never reaches disk
        try:
            mono = (indata[:, 0] if indata.ndim > 1 else indata).astype(np.float32, copy=True)
        except Exception:
            return
        self._queue.put(mono)

    def _writer_loop(self):
        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            self._process_block(item)

    def _process_block(self, mono: np.ndarray):
        """Write one block to disk, update the level meter, and fan out to the
        chunk callback. Runs on the writer thread; the unit tests call it
        directly with synthetic frames."""
        if self._paused or len(mono) == 0:
            return
        with self._lock:
            if self._sf is not None:
                self._sf.write(mono)
            self._frames_written += len(mono)
            self._level = float(np.sqrt(np.mean(np.square(mono))))
        if self.chunk_callback is not None:
            try:
                self.chunk_callback(mono)
            except Exception:
                pass

    def feed(self, mono):
        """Inject a block as if it came from the mic — the path used by tests
        and any non-sounddevice capture source. Dropped while paused (checked
        synchronously here, so pause is deterministic)."""
        if self._paused:
            return
        self._queue.put(np.asarray(mono, dtype=np.float32))

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def stop(self) -> str:
        """Stop capture, flush the queue, close the WAV. Returns the output
        path. Safe to call more than once."""
        if not self._recording and self._sf is None:
            return self.output_path
        self._recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        # Drain everything already queued, then stop the writer.
        self._queue.put(_STOP)
        if self._writer is not None:
            self._writer.join(timeout=5)
            self._writer = None
        self._close_file()
        return self.output_path

    def _close_file(self):
        with self._lock:
            if self._sf is not None:
                try:
                    self._sf.close()
                except Exception:
                    pass
                self._sf = None

    # -- state -------------------------------------------------------------
    @property
    def level(self) -> float:
        """RMS amplitude 0..1 of the most recent block (for a level meter)."""
        return self._level

    @property
    def elapsed(self) -> float:
        """Seconds captured so far."""
        return self._frames_written / float(self.samplerate)

    @property
    def frames_written(self) -> int:
        return self._frames_written

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_paused(self) -> bool:
        return self._paused
