"""Live (as-you-speak) transcription for Record mode.

Streaming ASR is hard and the bundled engines (whisper.cpp, cloud) are
batch-oriented, so we get "live" the pragmatic, robust way:

    recorded blocks ─▶ VadSegmenter ─▶ per-utterance WAV ─▶ ASRProvider.transcribe
                       (energy VAD)                          └▶ subtitle (offset to
                                                                the utterance start)

The moment a spoken phrase is followed by a short silence, that phrase is cut
out and transcribed on its own; the resulting text is placed on the timeline at
the phrase's real position. This yields subtitle-sized cues with sensible timing
and bounded latency, and reuses the existing ``ASRProvider`` contract unchanged.

Two pieces, both independently testable:

* :class:`VadSegmenter` — pure DSP/state-machine, no Qt, no I/O. Feed it float32
  mono blocks; it returns finalized ``(start_sample, end_sample)`` utterances.
* :class:`LiveTranscriber` — a QObject that buffers audio, drives the segmenter,
  writes utterance WAVs, and serialises them through an ASR provider, emitting a
  ``subtitle_ready`` signal per finalized cue. The audio side runs on the
  recorder's writer thread; the ASR side is marshalled to the main thread via a
  queued signal, so there's no shared mutable state across threads.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import numpy as np

from PySide6.QtCore import QObject, Signal, Qt

import soundfile as sf


class VadSegmenter:
    """Energy-based voice-activity segmenter.

    A run of speech frames (RMS ≥ ``threshold``) that lasts at least
    ``min_speech`` seconds and is then followed by ``min_silence`` seconds of
    quiet is emitted as one utterance. Runs longer than ``max_utterance`` are
    force-cut so latency and cue length stay bounded. Emitted ranges are padded
    slightly on each side (``pad``) and never overlap.

    All positions are absolute sample indices into the stream fed so far.
    """

    def __init__(self, samplerate=16000, threshold=0.012, min_silence=0.6,
                 min_speech=0.25, max_utterance=15.0, pad=0.12, frame=0.02):
        self.samplerate = int(samplerate)
        self.threshold = float(threshold)
        self._frame = max(1, int(frame * self.samplerate))
        self._min_sil = int(min_silence * self.samplerate)
        self._min_speech = int(min_speech * self.samplerate)
        self._max_utt = int(max_utterance * self.samplerate)
        self._pad = int(pad * self.samplerate)

        self._buf = np.empty(0, dtype=np.float32)  # sub-frame remainder
        self._pos = 0                              # samples consumed into frames
        self._in_speech = False
        self._speech_start = 0
        self._speech_samples = 0
        self._silence_run = 0
        self._last_speech_pos = 0
        self._last_emitted_end = 0

    def push(self, mono) -> list:
        """Feed a block; return a list of finalized ``(start, end)`` utterances
        (usually empty, occasionally one, rarely more on a force-cut)."""
        mono = np.asarray(mono, dtype=np.float32)
        if self._buf.size:
            mono = np.concatenate([self._buf, mono])
        finalized = []
        fs = self._frame
        n = len(mono)
        i = 0
        while i + fs <= n:
            frame = mono[i:i + fs]
            i += fs
            self._pos += fs
            rms = float(np.sqrt(np.mean(frame * frame))) if fs else 0.0
            if rms >= self.threshold:
                if not self._in_speech:
                    self._in_speech = True
                    self._speech_start = self._pos - fs
                    self._speech_samples = 0
                    self._silence_run = 0
                self._speech_samples += fs
                self._last_speech_pos = self._pos
                self._silence_run = 0
                if self._pos - self._speech_start >= self._max_utt:
                    u = self._finalize()
                    if u:
                        finalized.append(u)
                    # The phrase is still going — start a fresh utterance from
                    # here so a monologue splits into cue-sized pieces.
                    self._in_speech = True
                    self._speech_start = self._pos
                    self._speech_samples = 0
                    self._silence_run = 0
                    self._last_speech_pos = self._pos
            elif self._in_speech:
                self._silence_run += fs
                if self._silence_run >= self._min_sil:
                    u = self._finalize()
                    if u:
                        finalized.append(u)
        self._buf = mono[i:].copy()
        return finalized

    def flush(self) -> list:
        """Finalize any in-progress speech at end of recording."""
        # Fold the remainder in as a final (partial) frame so trailing speech
        # isn't lost.
        if self._buf.size:
            self._pos += self._buf.size
            rms = float(np.sqrt(np.mean(self._buf * self._buf)))
            if rms >= self.threshold and self._in_speech:
                self._speech_samples += self._buf.size
                self._last_speech_pos = self._pos
            self._buf = np.empty(0, dtype=np.float32)
        u = self._finalize()
        return [u] if u else []

    def _finalize(self):
        if not self._in_speech:
            return None
        speech_dur = self._speech_samples
        start = self._speech_start
        end = self._last_speech_pos
        self._in_speech = False
        self._silence_run = 0
        self._speech_samples = 0
        if speech_dur < self._min_speech:
            return None  # too short → likely a click/noise, drop it
        start = max(self._last_emitted_end, start - self._pad)
        end = end + self._pad
        if end <= start:
            return None
        self._last_emitted_end = end
        return (int(start), int(end))


class LiveTranscriber(QObject):
    """Turn a live recording into subtitles as it's spoken.

    ``push(mono)`` is called from the recorder's writer thread. Completed
    utterances are handed to the ASR provider one at a time (serialised) and the
    text comes back as ``subtitle_ready`` cues, timed to the utterance's real
    position in the recording.
    """

    # A finalized utterance WAV is ready (emitted from the audio thread, so the
    # ASR dispatch below always runs on the main thread via a queued connection).
    _utterance_ready = Signal(str, float)
    subtitle_ready = Signal(dict)   # {'start','end','text','speaker'}
    status_changed = Signal(str)

    def __init__(self, provider, language='en-us', options=None,
                 samplerate=16000, speaker='A', base_offset=0.0, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._language = language or 'en-us'
        self._options = dict(options) if isinstance(options, dict) else {}
        self._sr = int(samplerate)
        self._speaker = speaker or 'A'
        # Timeline seconds the recording started at (the playhead when Play was
        # pressed) — added to every utterance so live cues land in sync.
        self._base_offset = float(base_offset or 0.0)

        self._seg = VadSegmenter(samplerate=self._sr)
        self._buf = np.empty(0, dtype=np.float32)
        self._buf_base = 0  # absolute sample index of _buf[0]
        self._tmpdir = tempfile.mkdtemp(prefix='subtitld_live_')

        self._pending = []        # list of (wav_path, offset_seconds)
        self._busy = False
        self._current_offset = 0.0
        self._active = True
        self._finishing = False   # recorder stopped; drain what's queued then done
        self._connected = False

        self._utterance_ready.connect(self._on_utterance_ready, Qt.QueuedConnection)
        self._connect_provider()

    # -- audio thread ------------------------------------------------------
    def push(self, mono):
        """Feed recorded audio (writer thread). Buffers, segments, and on each
        completed utterance writes a WAV + signals the main thread."""
        if not self._active:
            return
        mono = np.asarray(mono, dtype=np.float32)
        self._buf = np.concatenate([self._buf, mono]) if self._buf.size else mono.copy()
        for (start, end) in self._seg.push(mono):
            self._cut_and_signal(start, end)

    def finish(self):
        """Call after the recorder stops: flush any trailing speech, then let
        the already-queued utterances drain — ``status_changed('done')`` fires
        once the last one has been transcribed.

        NOTE: we deliberately do NOT check for done here. Utterances finalized
        on the audio thread reach the ASR side via a *queued* signal, so at the
        moment ``finish`` runs ``_pending`` can still be empty even though work
        is in flight — declaring done here would tear the transcriber down and
        drop those subtitles. Done is decided only after a real utterance is
        processed (``_maybe_done`` from the transcript/error slots), with the
        caller's timeout as the backstop for a silent recording."""
        if not self._active:
            return
        self._finishing = True
        for (start, end) in self._seg.flush():
            self._cut_and_signal(start, end)

    def _maybe_done(self):
        if self._finishing and not self._busy and not self._pending:
            self.status_changed.emit('done')

    def _cut_and_signal(self, start_sample, end_sample):
        lo = start_sample - self._buf_base
        hi = end_sample - self._buf_base
        lo = max(0, lo)
        hi = min(len(self._buf), hi)
        if hi <= lo:
            return
        samples = self._buf[lo:hi].copy()
        # Drop everything up to this utterance's end — earlier audio is never
        # needed again (utterances are ordered and non-overlapping).
        if hi < len(self._buf):
            self._buf = self._buf[hi:].copy()
        else:
            self._buf = np.empty(0, dtype=np.float32)
        self._buf_base = end_sample
        wav = os.path.join(self._tmpdir, f'utt_{start_sample}.wav')
        try:
            sf.write(wav, samples, self._sr, subtype='PCM_16')
        except Exception:
            return
        self._utterance_ready.emit(wav, start_sample / float(self._sr))

    # -- main thread -------------------------------------------------------
    def _on_utterance_ready(self, wav_path, offset):
        self._pending.append((wav_path, offset))
        self._dispatch_next()

    def _dispatch_next(self):
        if self._busy or not self._pending or not self._active:
            return
        wav, offset = self._pending.pop(0)
        self._busy = True
        self._current_offset = float(offset)
        try:
            self.status_changed.emit('transcribing')
            self._provider.transcribe(wav, self._language, dict(self._options))
        except Exception:
            self._busy = False
            self._dispatch_next()

    def _on_transcript_finished(self, segments):
        offset = self._current_offset + self._base_offset
        self._busy = False
        if isinstance(segments, list):
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                text = str(seg.get('text', '')).strip()
                if not text:
                    continue
                start = float(seg.get('start', 0.0)) + offset
                end = float(seg.get('end', 0.0)) + offset
                if end <= start:
                    end = start + 0.5
                self.subtitle_ready.emit({
                    'start': start, 'end': end, 'text': text,
                    'speaker': self._speaker,
                })
        self._dispatch_next()
        self._maybe_done()

    def _on_error(self, message):
        # Skip the failed utterance and keep going.
        self._busy = False
        self._dispatch_next()
        self._maybe_done()

    # -- provider wiring / teardown ---------------------------------------
    def _connect_provider(self):
        try:
            self._provider.transcript_finished.connect(self._on_transcript_finished, Qt.QueuedConnection)
            self._provider.error.connect(self._on_error, Qt.QueuedConnection)
            self._connected = True
        except Exception:
            self._connected = False

    def stop(self):
        """Detach from the provider and mark inactive. Pending utterances that
        were already dispatched are allowed to complete; nothing new starts."""
        self._active = False
        if self._connected:
            try:
                self._provider.transcript_finished.disconnect(self._on_transcript_finished)
                self._provider.error.disconnect(self._on_error)
            except Exception:
                pass
            self._connected = False

    def cleanup(self):
        self.stop()
        try:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        except Exception:
            pass
