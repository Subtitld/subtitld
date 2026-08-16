import sounddevice as sd
import soundfile as sf
import numpy as np
import gc
import os
import sys
from pathlib import Path
from threading import Thread, Lock
from queue import Queue, Empty
import threading
import time


FADE_FRAMES = 64

# Prebuilt fade ramps — applied at every clip boundary. Shared by every
# clip in the engine so the audio callback never allocates a ramp via
# np.linspace, which used to be a per-clip-boundary allocation. Reshape
# to (N, 1) once at module load so the per-callback `out[...] *= ramp`
# can broadcast against a (N, 2) stereo slice without any further
# allocation.
_FADE_IN_RAMP = np.linspace(0.0, 1.0, FADE_FRAMES, dtype=np.float32).reshape(-1, 1)
_FADE_OUT_RAMP = np.linspace(1.0, 0.0, FADE_FRAMES, dtype=np.float32).reshape(-1, 1)
# 1-D variants used by SubtitleDubClip — its independent-subclip path
# applies fades to the mono float32 source before `+=`-mixing into the
# stereo buffer, so overlapping subclips don't have their fade ramp
# attenuate each other's samples.
_FADE_IN_RAMP_1D = _FADE_IN_RAMP.reshape(-1)
_FADE_OUT_RAMP_1D = _FADE_OUT_RAMP.reshape(-1)

# Reciprocal of int16 full-scale. The dub cache stores int16 mono — the
# audio callback converts to float32 in [-1, 1] by multiplying by this
# scalar (rolled into `gain` so one np.multiply does both). 32768 keeps
# +1.0 exactly representable; the int16 minimum (-32768) maps to a hair
# past -1.0 which is harmless since the engine clips the mix anyway.
_INT16_TO_FLOAT32 = np.float32(1.0 / 32768.0)

# Toggle realtime audio-callback debug logging. Off by default — the
# diagnostic itself (per-callback timing, the 100Hz watchdog thread, the GC
# callback, and stderr writes from the audio thread) is a noticeable
# real-time hazard on hosts already close to underrun. Set
# SUBTITLD_AUDIO_DEBUG=1 only when actively chasing a chunkiness bug.
DEBUG_AUDIO = os.environ.get('SUBTITLD_AUDIO_DEBUG', '0') != '0'


def _decode_callback_status(status):
    """Sounddevice CallbackFlags → human-readable list. Empty list = clean."""
    if not status:
        return []
    flags = []
    for name in ('input_underflow', 'input_overflow', 'output_underflow',
                 'output_overflow', 'priming_output'):
        if getattr(status, name, False):
            flags.append(name)
    return flags or [str(status)]


class AudioSource:
    """Streams audio from disk with background thread"""
    def __init__(self, filepath, samplerate):
        self.filepath = Path(filepath)
        self.samplerate = samplerate
        self._info = sf.info(filepath)
        self.channels = self._info.channels
        self.frames = self._info.frames

    def read_frames(self, start_frame, num_frames):
        """Read a chunk of audio from disk"""
        with sf.SoundFile(self.filepath, 'r') as f:
            f.seek(int(start_frame))
            data = f.read(int(num_frames), dtype='float32', always_2d=True)
            # Ensure stereo output
            if data.shape[1] == 1:
                data = np.tile(data, (1, 2))
            return data


class BufferPool:
    """Reuse buffers to reduce memory allocations"""
    def __init__(self, max_size=20):
        self.pool = []
        self.max_size = max_size
        self.lock = Lock()

    def get(self, shape, dtype=np.float32):
        with self.lock:
            for i, buf in enumerate(self.pool):
                if buf.shape == shape and buf.dtype == dtype:
                    return self.pool.pop(i)
        return np.zeros(shape, dtype=dtype)

    def release(self, buf):
        with self.lock:
            if len(self.pool) < self.max_size:
                buf.fill(0.0)
                self.pool.append(buf)


class Clip:
    def __init__(
        self,
        source: AudioSource,
        start_time: float,
        start_offset: float = 0.0,
        duration: float | None = None,
        gain: float = 1.0,
        speed: float = 1.0,
        blocksize: int = 2048,
    ):
        self.source = source
        self.start_time = start_time
        self.start_offset = start_offset
        self.duration = duration
        self.gain = gain
        self.speed = speed
        self.enabled = True

        # Double buffering for smooth playback
        self._cache_a = {'start': -1, 'data': None}
        self._cache_b = {'start': -1, 'data': None}
        self._active_cache = self._cache_a
        self._cache_size = 131072  # ~2.7 seconds at 48kHz
        self._cache_lock = Lock()

        # Pre-allocated index buffer reused every callback for the
        # linear-resample math — saves one (blocksize * speed)-sized
        # np.arange allocation per callback. `* 4` covers up to 4x
        # playback speed before the slice spills.
        self._src_idx_buf = np.empty(blocksize * 4, dtype=np.float64)

        # Background prefetch: disk I/O for the next cache chunk runs on
        # this thread so the audio callback never blocks waiting for
        # bytes off the platter.
        self._prefetch_queue = Queue(maxsize=1)
        self._prefetch_running = True
        self._prefetch_threshold = 0.75  # start loading when 75% through current cache
        self._prefetch_thread = Thread(target=self._prefetch_worker, daemon=True)
        self._prefetch_thread.start()

    def _prefetch_worker(self):
        while self._prefetch_running:
            try:
                read_start, read_frames, target_cache = self._prefetch_queue.get(timeout=1.0)
                if read_frames <= 0:
                    continue
                data = self.source.read_frames(read_start, read_frames)
                with self._cache_lock:
                    target_cache['data'] = data
                    target_cache['start'] = read_start
                    self._active_cache = target_cache
            except Empty:
                continue

    def _get_cache_for_position(self, frame_pos, needed_frames=2048):
        """Return a cache that covers frame_pos, triggering async prefetch when nearing the end."""
        with self._cache_lock:
            frames_needed = int(needed_frames * max(1.0, self.speed)) + 100

            for cache in (self._cache_a, self._cache_b):
                if cache['data'] is None:
                    continue
                cache_end = cache['start'] + len(cache['data'])
                if frame_pos >= cache['start'] and frame_pos + frames_needed <= cache_end:
                    # Trigger prefetch when we've consumed more than threshold of this cache
                    progress = (frame_pos - cache['start']) / max(1, len(cache['data']))
                    if progress > self._prefetch_threshold:
                        other = self._cache_b if cache is self._cache_a else self._cache_a
                        next_start = int(cache_end)
                        # Only enqueue if not already loading that position
                        if other.get('start') != next_start and self._prefetch_queue.empty():
                            next_frames = min(self._cache_size, self.source.frames - next_start)
                            if next_frames > 0:
                                try:
                                    self._prefetch_queue.put_nowait((next_start, next_frames, other))
                                except Exception:
                                    pass
                    return cache

            # Synchronous fallback — only happens on first load or after a seek
            target_cache = self._cache_a if self._active_cache is self._cache_b else self._cache_b
            read_start = max(0, int(frame_pos))
            read_frames = min(self._cache_size, self.source.frames - read_start)

            if read_frames > 0:
                target_cache['data'] = self.source.read_frames(read_start, read_frames)
                target_cache['start'] = read_start
                self._active_cache = target_cache

            return target_cache

    def _apply_boundary_fades(self, out, out_start, num_output_frames, is_clip_start, is_clip_end):
        """Multiply a fade-in ramp into the first FADE_FRAMES of the
        clip and a fade-out ramp into the last FADE_FRAMES. Eliminates
        the click that would otherwise happen at any clip boundary."""
        # Reuse the module-level ramps — same as SubtitleDubClip. The
        # previous np.linspace-per-callback allocated 256 bytes × 2 ramps
        # × however-many in-window clips, which is malloc pressure on
        # the audio thread for no audible benefit.
        fade_len = min(FADE_FRAMES, num_output_frames)
        if is_clip_start:
            out[out_start:out_start + fade_len] *= _FADE_IN_RAMP[:fade_len]
        if is_clip_end:
            end = out_start + num_output_frames
            out[end - fade_len:end] *= _FADE_OUT_RAMP[:fade_len]

    def read(self, playhead, frames, samplerate, buffer_pool):
        """Read audio with double-buffered streaming and background prefetch."""
        out = buffer_pool.get((frames, 2), dtype=np.float32)

        if not self.enabled:
            return out

        t0 = playhead
        t1 = playhead + frames / samplerate

        clip_end = (
            self.start_time + self.duration
            if self.duration is not None
            else self.start_time + (self.source.frames / self.source.samplerate)
        )

        if t1 <= self.start_time or t0 >= clip_end:
            return out

        clip_t0 = max(t0, self.start_time)
        clip_t1 = min(t1, clip_end)

        # round() rather than int() so an unlucky float rounding doesn't
        # drop one output frame at the clip boundary (audible click).
        out_start = round((clip_t0 - t0) * samplerate)
        out_end = round((clip_t1 - t0) * samplerate)
        num_output_frames = out_end - out_start

        if num_output_frames <= 0:
            return out

        # Detect clip boundaries for fade application
        is_clip_start = clip_t0 <= self.start_time + 1.0 / samplerate
        is_clip_end = clip_t1 >= clip_end - 1.0 / samplerate

        src_time = ((clip_t0 - self.start_time) + self.start_offset) * self.speed
        src_frame_start = src_time * self.source.samplerate

        cache = self._get_cache_for_position(int(src_frame_start), num_output_frames)

        if cache['data'] is None or len(cache['data']) == 0:
            return out

        cache_offset = src_frame_start - cache['start']

        # Reuse pre-allocated index buffer to avoid one np.arange-sized
        # alloc per callback.
        src_idx = self._src_idx_buf[:num_output_frames]
        np.multiply(np.arange(num_output_frames), self.speed, out=src_idx)
        src_idx += cache_offset

        i0 = np.floor(src_idx).astype(np.int64)
        i1 = i0 + 1
        frac = src_idx - i0

        valid = (i0 >= 0) & (i1 < len(cache['data']))
        if not np.any(valid):
            return out

        i0_valid = i0[valid]
        i1_valid = i1[valid]
        frac_valid = frac[valid].astype(np.float32)

        s0 = cache['data'][i0_valid]
        s1 = cache['data'][i1_valid]

        interpolated = (1.0 - frac_valid[:, None]) * s0 + frac_valid[:, None] * s1
        out[out_start:out_start + np.count_nonzero(valid)] = interpolated * self.gain

        self._apply_boundary_fades(out, out_start, num_output_frames, is_clip_start, is_clip_end)

        return out

    def clear_cache(self):
        """Clear caches on seek."""
        with self._cache_lock:
            self._cache_a = {'start': -1, 'data': None}
            self._cache_b = {'start': -1, 'data': None}

    def shutdown(self):
        """Stop the prefetch thread cleanly."""
        self._prefetch_running = False
        self._prefetch_thread.join(timeout=2.0)


def _first_unmuted_dub(dubs):
    """The dub clip that should be heard: the first un-muted entry. A clip's
    default mute state is `index != 0`, so with no explicit flags `[0]` plays
    and the alternates are silent — the "clip playlist" model. Soloing an
    alternate (mute the rest, un-mute it) makes it play; promoting one to `[0]`
    makes it the new default."""
    if not dubs:
        return None
    for i, d in enumerate(dubs):
        if not d.get('muted', i != 0):
            return d
    return dubs[0]


class SubtitleDubClip:
    """Wrapper clip that plays a subtitle's active dub (the first un-muted entry
    of `subtitle['dubbing']`; `[0]` by default) for a given subtitle dict.

    Reads `path` and `start` live each callback so timeline drags / playlist swaps
    take effect immediately without engine re-sync. Loaded WAV samples are kept
    in a small per-instance dict keyed by file path.

    Storage format (since the int16-mono refactor):
        _loaded[path] = (data: int16 ndarray shape (N,), engine_samplerate, N)

    int16 instead of float32 halves the bytes; mono instead of stereo halves
    them again — 4× shrink overall. TTS sources are universally mono (Edge
    TTS, Piper, Coqui XTTS all emit 24 kHz mono), so the historical
    `np.repeat(data, 2, axis=1)` was pure waste. The audio callback converts
    + broadcasts to stereo float32 in one vectorized pass, costing a single
    (n,) ndarray allocation per in-window clip per callback — still well
    under the 42.7 ms block budget."""

    # Background loader, lazily created the first time a clip needs to
    # pull a WAV off disk. Shared across all clips — one thread is enough
    # because the queue serializes loads anyway, and we never want
    # competing reads thrashing the same disk.
    _bg_loader_thread = None
    _bg_loader_queue: Queue = Queue()
    _bg_loader_lock = threading.Lock()

    def __init__(self, subtitle, samplerate):
        self.subtitle = subtitle
        self.samplerate = samplerate
        self.gain = 1.0
        self.speed = 1.0
        self.enabled = True
        # path -> (int16 mono ndarray of shape (N,), engine_samplerate, N).
        # See class docstring for the rationale behind int16 mono storage.
        self._loaded = {}
        # Paths we've already asked the background loader to fetch — keeps
        # us from re-queueing the same path on every audio callback. We
        # don't store the actual data here; that goes into `_loaded` once
        # the loader thread is done.
        self._load_pending: set[str] = set()

    def _current_dub(self):
        return _first_unmuted_dub(self.subtitle.get('dubbing'))

    def preload(self, path):
        """Synchronous load — only safe to call from the MAIN thread (e.g.
        from `sync_subtitle_dubs`) or the background loader. NEVER from
        the audio callback: a cold-cache SoundFile open can stall for tens
        of milliseconds, which is guaranteed underrun territory at
        blocksize=2048.

        Resamples to engine samplerate, downmixes to mono, and packs as
        int16 — all up-front so the audio callback only has to multiply
        by a scaling constant and broadcast across two output channels.
        Storage is 1/4 the size of the original (stereo, float32, engine
        SR) representation; the in-callback cost is one (n,) float32
        allocation per in-window clip, which fits comfortably under the
        block budget."""
        if path in self._loaded:
            return
        try:
            with sf.SoundFile(path, 'r') as f:
                data = f.read(dtype='float32', always_2d=True)
                src_sr = f.samplerate
            # Downmix to mono: TTS sources are mono in practice (Edge,
            # Piper, Coqui all emit single-channel) so this is a no-op
            # in the common case. For genuinely stereo inputs, average
            # the channels — that's what every L+R-to-mono downmix
            # does.
            if data.shape[1] == 1:
                mono = data[:, 0]
            else:
                mono = data.mean(axis=1, dtype=np.float32)
            # Resample to engine rate. Linear interp at load is what the
            # callback's slow path used to do per-frame — amortizing it
            # here removes 12 numpy allocations from each in-window
            # callback. Same audible result.
            if src_sr != self.samplerate:
                src_frames = len(mono)
                ratio = self.samplerate / src_sr
                dst_frames = int(round(src_frames * ratio))
                if dst_frames <= 0:
                    return
                src_idx = np.linspace(0.0, src_frames - 1, dst_frames, dtype=np.float64)
                xp = np.arange(src_frames, dtype=np.float64)
                mono = np.interp(src_idx, xp, mono).astype(np.float32)
            # Pack to int16. Clip before cast to keep the high-bit
            # interpretation predictable — TTS output is normally well
            # below full scale but we never want a wraparound on an
            # over-the-top sample.
            np.clip(mono, -1.0, 1.0, out=mono)
            packed = (mono * 32767.0).astype(np.int16)
            self._loaded[path] = (packed, self.samplerate, len(packed))
            # Evict stale entries: `read()` consults the current dub's
            # segment paths (or the legacy single path); older paths
            # left in `_loaded` from previous regenerations are dead
            # memory. A single 3-second mono int16 dub at 48 kHz is
            # 287 KB; across a 500-subtitle project with even modest
            # regeneration history this still leaks tens of MB.
            #
            # Keep:
            #   - the path we just loaded
            #   - every audio-segment path in the current dub (multi-path
            #     dubs created by split-with-reference or future
            #     ASR-replace flows would otherwise self-evict their
            #     segments as each new file finishes loading)
            #
            # The audio thread's `.get(path)` lookup is atomic, so
            # dropping siblings here is callback-safe.
            from subtitld.modules import dub_clip
            keep = {path}
            dub = self._current_dub()
            if dub is not None:
                for p in dub_clip.collect_segment_paths(dub):
                    if p:
                        keep.add(p)
            stale = [p for p in self._loaded if p not in keep]
            for p in stale:
                self._loaded.pop(p, None)
        except Exception:
            pass

    @classmethod
    def _ensure_bg_loader(cls):
        """Spin up the shared background-loader thread on first use."""
        with cls._bg_loader_lock:
            if cls._bg_loader_thread is not None and cls._bg_loader_thread.is_alive():
                return
            cls._bg_loader_thread = threading.Thread(
                target=cls._bg_loader_run, name='subtitle-dub-loader', daemon=True,
            )
            cls._bg_loader_thread.start()

    @classmethod
    def _bg_loader_run(cls):
        """Drain (clip, path) pairs forever. The clip stuffs the result
        into its own `_loaded` dict — no shared structure, so no lock
        needed for the write itself."""
        while True:
            try:
                clip, path = cls._bg_loader_queue.get()
            except Exception:
                continue
            if clip is None:
                continue
            try:
                clip.preload(path)
            except Exception:
                pass
            finally:
                # Drop the pending marker either way — if the load failed
                # the audio thread will just keep producing silence for
                # this clip until something retriggers preload (e.g. the
                # path changes, or sync_subtitle_dubs runs again).
                clip._load_pending.discard(path)

    def _request_async_preload(self, path):
        """Audio-thread-safe: enqueue a background load and mark the path
        as pending so subsequent callbacks don't enqueue it again."""
        if path in self._load_pending:
            return
        self._load_pending.add(path)
        SubtitleDubClip._ensure_bg_loader()
        try:
            SubtitleDubClip._bg_loader_queue.put_nowait((self, path))
        except Exception:
            # Queue is unbounded so this shouldn't happen, but if anything
            # fails just drop the pending marker so a future callback can
            # retry. Better silent dub than blocked audio thread.
            self._load_pending.discard(path)

    def read(self, playhead, frames, samplerate, buffer_pool):
        # Fast outer-bound skip — with hundreds of dubs in a project only
        # a handful are inside the audio window. Returning None lets
        # Track.read skip the per-clip alloc + add + zero-fill, which
        # otherwise dominates the audio callback (the buffer pool tops
        # out at ~30 buffers, so each off-window clip falls through to a
        # fresh np.zeros allocation).
        if not self.enabled:
            return None
        dub = self._current_dub()
        if not dub:
            return None

        base = float(dub.get('start', 0.0))

        # Build the iteration list. Legacy dubs (no `segments` key)
        # collapse to one virtual subclip built from `dub['path']` and
        # the loaded frame count — NO file I/O on the audio thread.
        # `segments == []` means "explicitly empty take" → silence.
        segments_field = dub.get('segments')
        if segments_field is None:
            path = dub.get('path')
            if not path:
                return None
            loaded = self._loaded.get(path)
            if loaded is None:
                self._request_async_preload(path)
                return None
            _, src_sr, total_frames = loaded
            seg_dur = total_frames / src_sr if src_sr > 0 else 0.0
            if seg_dur <= 0.0:
                return None
            segments_iter = (
                {'type': 'audio', 'path': path,
                 'start': 0.0, 'end': seg_dur, 'offset': 0.0},
            )
        elif not segments_field:
            return None
        else:
            segments_iter = segments_field

        t0 = playhead
        t1 = playhead + frames / samplerate

        # Fast outer-bound skip — bail before doing any per-subclip work
        # if the whole dub is out of the audio window. Subclips are
        # independent so we need min(timeline_start), max(timeline_end).
        extent_lo = None
        extent_hi = None
        for seg in segments_iter:
            if seg.get('type', 'audio') != 'audio':
                continue
            end = seg.get('end')
            if end is None:
                continue
            seg_offset = float(seg.get('offset', 0.0))
            seg_t0 = base + seg_offset
            seg_t1 = seg_t0 + max(0.0, float(end) - float(seg.get('start', 0.0)))
            if extent_lo is None or seg_t0 < extent_lo:
                extent_lo = seg_t0
            if extent_hi is None or seg_t1 > extent_hi:
                extent_hi = seg_t1
        if extent_lo is None or t1 <= extent_lo or t0 >= extent_hi:
            return None

        # `data` int16 mono → float32 in [-1, 1] via one np.multiply per
        # subclip slice; gain folds in so we do exactly one multiply.
        scale = np.float32(self.gain) * _INT16_TO_FLOAT32

        out = None  # allocate lazily on first rendered slice

        for i, seg in enumerate(segments_iter):
            if seg.get('type', 'audio') != 'audio':
                continue
            end = seg.get('end')
            if end is None:
                continue
            seg_source_start = float(seg.get('start', 0.0))
            seg_dur = float(end) - seg_source_start
            if seg_dur <= 0:
                continue
            seg_offset = float(seg.get('offset', 0.0))
            seg_t0 = base + seg_offset
            seg_t1 = seg_t0 + seg_dur
            if seg_t1 <= t0 or seg_t0 >= t1:
                continue

            path = seg.get('path')
            if not path:
                continue
            loaded = self._loaded.get(path)
            if loaded is None:
                self._request_async_preload(path)
                continue
            data, src_sr, total_frames = loaded

            clip_t0 = max(t0, seg_t0)
            clip_t1 = min(t1, seg_t1)
            out_start = round((clip_t0 - t0) * samplerate)
            out_end = round((clip_t1 - t0) * samplerate)
            n_out = out_end - out_start
            if n_out <= 0:
                continue

            if out is None:
                out = buffer_pool.get((frames, 2), dtype=np.float32)

            # Fast path: source samplerate matches engine and no stretch
            # in flight. Slice int16 source → mono float32 with gain
            # folded in.
            if src_sr == samplerate and self.speed == 1.0:
                src_frame_start = int(round(
                    (clip_t0 - seg_t0 + seg_source_start) * src_sr
                ))
                avail = total_frames - src_frame_start
                if avail <= 0:
                    continue
                n = min(n_out, avail)
                mono_f32 = np.multiply(
                    data[src_frame_start:src_frame_start + n], scale,
                    dtype=np.float32, casting='unsafe',
                )
                rendered_n = n
            else:
                # Slow path: speed != 1.0 (live stretch preview).
                ratio = (src_sr / samplerate) * self.speed
                src_frame_start = (clip_t0 - seg_t0 + seg_source_start) * src_sr
                src_idx = np.arange(n_out, dtype=np.float64) * ratio + src_frame_start
                i0 = np.floor(src_idx).astype(np.int64)
                i1 = i0 + 1
                valid = (i0 >= 0) & (i1 < total_frames)
                valid_count = int(np.count_nonzero(valid))
                if valid_count == 0:
                    continue
                i0v = i0[valid]
                i1v = i1[valid]
                fracv = (src_idx[valid] - i0v).astype(np.float32)
                s0 = data[i0v].astype(np.float32)
                s1 = data[i1v].astype(np.float32)
                mono_f32 = ((1.0 - fracv) * s0 + fracv * s1) * scale
                rendered_n = valid_count

            # Fades — applied to `mono_f32` BEFORE the `+=` mix so
            # overlapping subclips don't attenuate each other's samples.
            # Skip the fade at any edge that's contiguous with another
            # subclip in this dub (same path, same source offset adjacent
            # in source, same timeline offset adjacent in timeline) so
            # a fresh split with both halves still aligned plays without
            # an audible dip at the seam. After the user moves or trims
            # either half, contiguity breaks and the fade applies.
            seg_starts_at_window = clip_t0 <= seg_t0 + 1.0 / samplerate
            seg_ends_at_window = clip_t1 >= seg_t1 - 1.0 / samplerate
            fade_in_needed = seg_starts_at_window
            fade_out_needed = seg_ends_at_window
            if (fade_in_needed or fade_out_needed) and len(segments_iter) > 1:
                # O(N) scan against the rest of the subclip list. With
                # ~tens of subclips per dub max this is cheap; we skip
                # the whole check when there's only one subclip anyway.
                for j, other in enumerate(segments_iter):
                    if j == i:
                        continue
                    if other.get('type', 'audio') != 'audio':
                        continue
                    if other.get('path') != path:
                        continue
                    other_end = other.get('end')
                    if other_end is None:
                        continue
                    other_start = float(other.get('start', 0.0))
                    other_offset = float(other.get('offset', 0.0))
                    other_dur = float(other_end) - other_start
                    # Other ends where this one starts (in both source
                    # and timeline) → no fade-in for this subclip.
                    if (fade_in_needed
                            and abs(float(other_end) - seg_source_start) < 1e-6
                            and abs(other_offset + other_dur - seg_offset) < 1e-6):
                        fade_in_needed = False
                    # Other starts where this one ends → no fade-out.
                    if (fade_out_needed
                            and abs(other_start - float(end)) < 1e-6
                            and abs(other_offset - (seg_offset + seg_dur)) < 1e-6):
                        fade_out_needed = False
            fade_len = min(FADE_FRAMES, rendered_n)
            if fade_len > 0:
                if fade_in_needed:
                    mono_f32[:fade_len] *= _FADE_IN_RAMP_1D[:fade_len]
                if fade_out_needed:
                    mono_f32[rendered_n - fade_len:rendered_n] *= _FADE_OUT_RAMP_1D[:fade_len]

            # `+=` because subclips can overlap on the timeline — both
            # via intentional user placement and as a transient state
            # while dragging.
            out[out_start:out_start + rendered_n, 0] += mono_f32
            out[out_start:out_start + rendered_n, 1] += mono_f32

        return out

    def clear_cache(self):
        pass

    def shutdown(self):
        self._loaded.clear()


class _PreMixedRing:
    """Circular numpy buffer of pre-mixed float32 stereo frames.

    Producer (``_MixerThread``) writes mixed blocks via ``push``;
    consumer (the sounddevice audio callback) reads them via
    ``pop_into``. The buffer's job is to decouple the callback from
    the actual mixing work — the callback only does a memcpy out of
    this buffer, so its GIL hold drops from "however long mixing took"
    (1–30 ms, occasionally spiking under heavy projects) to "however
    long it takes to copy ~4096 frames" (~50 µs).

    That decoupling is what makes playback survive main-thread events
    like timeline zoom, subtitle selection, paint storms — the
    producer thread CAN stall under those events, but the consumer
    keeps draining smoothly until the ring drains entirely. The ring
    is sized for ~500 ms of headroom; the producer targets ~200 ms of
    pre-fill so it has 300 ms of "I can stall this long" margin.

    Underrun semantics: ``pop_into`` zeroes the unread tail when the
    ring is short, so a stalled producer manifests as silence (audible
    as a short gap) rather than a freeze or crash. This is the exact
    same audible result as the old callback missing its deadline, but
    it takes a ~300 ms producer stall to reproduce vs. a ~85 ms
    callback stall before.
    """

    def __init__(self, capacity_frames, channels=2):
        self._buf = np.zeros((capacity_frames, channels), dtype=np.float32)
        self._capacity = int(capacity_frames)
        self._read_pos = 0
        self._write_pos = 0
        self._fill = 0  # frames available to read
        self._lock = threading.Lock()

    def fill_level(self):
        with self._lock:
            return self._fill

    def free_space(self):
        with self._lock:
            return self._capacity - self._fill

    def capacity(self):
        return self._capacity

    def push(self, block):
        """Copy as much of ``block`` (shape (n, 2)) into the ring as
        fits. Returns the number of frames actually written; the
        producer can sleep and retry the rest on the next iteration."""
        n_in = len(block)
        with self._lock:
            free = self._capacity - self._fill
            if free <= 0:
                return 0
            n = n_in if n_in < free else free
            wp = self._write_pos
            cap = self._capacity
            end = wp + n
            if end <= cap:
                self._buf[wp:end] = block[:n]
            else:
                first = cap - wp
                self._buf[wp:cap] = block[:first]
                self._buf[0:end - cap] = block[first:n]
            self._write_pos = end % cap
            self._fill += n
            return n

    def pop_into(self, out, frames):
        """Copy ``frames`` frames into ``out`` (shape (frames, 2)).
        Zeroes the tail if the ring has fewer than ``frames`` available.
        Returns the number of frames that actually came from the ring
        (the rest is silence)."""
        with self._lock:
            n = self._fill if self._fill < frames else frames
            if n == 0:
                out.fill(0.0)
                return 0
            rp = self._read_pos
            cap = self._capacity
            end = rp + n
            if end <= cap:
                out[:n] = self._buf[rp:end]
            else:
                first = cap - rp
                out[:first] = self._buf[rp:cap]
                out[first:n] = self._buf[0:end - cap]
            if n < frames:
                out[n:].fill(0.0)
            self._read_pos = end % cap
            self._fill -= n
            return n

    def clear(self):
        """Drop all buffered audio. Called on seek so the listener
        hears the new position immediately instead of ~200 ms of
        stale pre-roll mixed from before the seek."""
        with self._lock:
            self._read_pos = 0
            self._write_pos = 0
            self._fill = 0


class _MixerThread(threading.Thread):
    """Background mixer that fills the engine's pre-mix ring buffer.

    Lifecycle:
      * Created and started once in ``SoundDeviceAudioEngine.__init__``
        (lives for the whole engine lifetime).
      * Starts paused; ``resume(position)`` is called from
        ``engine.play()`` and ``pause()`` from ``engine.pause()``.
      * ``reseek(position)`` re-anchors mid-playback without changing
        paused state.

    The producer holds its own playhead (``_producer_playhead``) that
    runs ahead of the engine's consumer playhead by the ring buffer's
    current fill level. External callers (UI, seek) interact with the
    consumer playhead (``engine._playhead``); the producer playhead is
    an internal detail.

    Why a plain ``threading.Thread`` instead of ``QThread``: this
    thread does pure Python + numpy work and doesn't need Qt's event
    loop. Daemon=True so it doesn't block process exit.
    """

    def __init__(self, engine, target_fill_frames):
        super().__init__(name='subtitld-audio-mixer', daemon=True)
        self._engine = engine
        self._target_fill = int(target_fill_frames)
        self._running = True
        self._paused = True
        self._cond = threading.Condition()
        self._producer_playhead = 0.0
        # Bumped every time resume()/reseek() re-anchors the producer.
        # The run loop reads the generation alongside the playhead; if
        # it doesn't match when we go to push the mixed block, the mix
        # is stale (mixed at the OLD playhead) and we discard it. Without
        # this, a seek mid-mix would push ~85 ms of wrong-position audio
        # into the freshly-cleared ring.
        self._gen = 0

    def stop(self):
        with self._cond:
            self._running = False
            self._cond.notify_all()

    def pause(self):
        with self._cond:
            self._paused = True
            self._cond.notify_all()

    def resume(self, position):
        """Resume mixing from ``position`` (seconds). Clears the ring
        so the next audio the listener hears is mixed from
        ``position``, not stale pre-roll from before pause."""
        with self._cond:
            self._engine._ring.clear()
            self._producer_playhead = float(position)
            self._paused = False
            self._gen += 1
            self._cond.notify_all()

    def reseek(self, position):
        """Mid-playback seek: drop the ring's pre-roll and re-anchor
        the producer. Paused state unchanged."""
        with self._cond:
            self._engine._ring.clear()
            self._producer_playhead = float(position)
            self._gen += 1
            self._cond.notify_all()

    def run(self):
        engine = self._engine
        block_size = engine.blocksize
        sr = engine.samplerate
        # Scratch buffer reused across iterations — the audio callback
        # no longer touches the buffer pool, so we own this exclusively.
        scratch = np.zeros((block_size, 2), dtype=np.float32)

        while True:
            # Wait until we should mix: not paused, ring has room, and
            # we're still running. Bounded timeout is defensive.
            with self._cond:
                while True:
                    if not self._running:
                        return
                    if self._paused:
                        self._cond.wait(timeout=0.05)
                        continue
                    if engine._ring.fill_level() >= self._target_fill:
                        self._cond.wait(timeout=0.005)
                        continue
                    ph = self._producer_playhead
                    gen = self._gen
                    break

            # Mix one block at `ph`. Snapshot `tracks` so a concurrent
            # `sync_subtitle_dubs` append on the main thread can't tear
            # the iteration — same pattern the old callback used.
            scratch.fill(0.0)
            for track in tuple(engine.tracks):
                td = track.read(ph, block_size, sr, engine.buffer_pool)
                scratch += td
                engine.buffer_pool.release(td)
            np.clip(scratch, -1.0, 1.0, out=scratch)

            # Re-acquire the lock for the push + playhead advance. If
            # the generation changed during the mix, a seek happened
            # and our mix is for a stale playhead — discard it.
            with self._cond:
                if gen != self._gen:
                    continue
                pushed = engine._ring.push(scratch)
                if pushed > 0:
                    self._producer_playhead = ph + (pushed / sr) * engine.speed


class Track:
    def __init__(self):
        self.clips = []
        self.gain = 1.0
        self.enabled = True
        # Optional audio_effects.EffectChain applied to this track's mixed
        # block before the gain (channel-strip order: EQ/dynamics → fader).
        # Swapped atomically from the main thread; the audio thread snapshots
        # the reference, so a mid-block rebuild can't tear it apart.
        self.effect_chain = None
        # Updated each Track.read() — read by the engine's debug printer.
        self.last_in_window_clips = 0
        self.last_total_clips = 0

    def add_clip(self, clip):
        self.clips.append(clip)

    def read(self, playhead, frames, samplerate, buffer_pool):
        out = buffer_pool.get((frames, 2), dtype=np.float32)
        # DEBUG: how many clips actually rendered audio this callback.
        self.last_in_window_clips = 0
        self.last_total_clips = 0

        if not self.enabled:
            return out

        # Snapshot the clip list so the audio callback can't be torn apart
        # by `Track.add_clip` / `track.clips.remove(...)` running on the main
        # thread (e.g. via SoundDeviceAudioEngine.sync_subtitle_dubs while
        # bulk dub generation is delivering new clips).
        clips_snapshot = tuple(self.clips)
        self.last_total_clips = len(clips_snapshot)
        for clip in clips_snapshot:
            clip_data = clip.read(playhead, frames, samplerate, buffer_pool)
            if clip_data is None:
                # Out-of-window clip — saves a per-clip add + zero-fill
                # at hundreds of off-window clips per audio callback.
                continue
            self.last_in_window_clips += 1
            out += clip_data
            buffer_pool.release(clip_data)

        chain = self.effect_chain   # atomic snapshot (main thread may swap it)
        if chain is not None:
            try:
                chain.process(out, playhead, samplerate)
            except Exception:
                # A DSP glitch must never take down the audio thread — drop
                # the effect for this block rather than propagate.
                pass

        out *= self.gain
        return out


class SoundDeviceAudioEngine:
    def __init__(self, samplerate=48000, blocksize=4096):
        # ARCHITECTURE
        # ============
        # Producer/consumer split. The sounddevice audio callback no
        # longer mixes — a separate ``_MixerThread`` (started below) does
        # all per-block mixing into ``self._ring`` (a circular numpy
        # buffer). The callback collapses to a ``memcpy`` from the ring,
        # cutting its GIL hold from ~5-30 ms to ~50 µs.
        #
        # Why this matters: the audio callback runs Python under the
        # GIL. Any main-thread work that holds the GIL longer than the
        # callback budget starves it and audio cuts. With mixing on the
        # callback path that budget was ~85 ms (blocksize=4096 at 48 kHz),
        # which the main thread routinely blew past during zoom,
        # subtitle selection, etc. With the producer split, the only
        # thing the callback HAS to do under the GIL is a numpy slice
        # copy; the ring buffer provides ~300 ms of slack against
        # producer-side stalls (mixer thread can also be GIL-stalled,
        # but its work being deferred is harmless as long as the ring
        # isn't empty).
        #
        # blocksize=4096 at 48 kHz → ~85 ms per audio block. Larger
        # blocks reduce per-callback overhead and let the mixer batch
        # work, at the cost of ~85 ms extra latency on seek/play.
        # Imperceptible in practice; play/pause feel instant.
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.tracks = []
        self.playing = False
        self.speed = 1.0

        # Playhead is read by the audio thread on every callback and
        # written by the main thread on seek/play — guard with a lock
        # so a torn read can't make the callback render the wrong block.
        self._playhead_lock = threading.Lock()
        self._playhead = 0.0

        self.buffer_pool = BufferPool(max_size=30)

        # Pre-mix ring buffer + mixer thread. See architecture comment
        # at the top of __init__ for the rationale.
        #
        # Capacity = ~500 ms (at 48 kHz, ~24000 frames). That's the
        # absolute headroom; in steady state the producer maintains
        # ~200 ms of pre-fill (``_target_fill``), so the producer can
        # stall up to ~200 ms before the consumer starts hearing
        # silence. Going larger costs negligible memory (500 ms * 48 kHz
        # * 2 ch * 4 B = 192 KB) but increases seek latency proportionally
        # (the ring has to be drained or cleared before new audio plays).
        # 500 ms is the right balance: long enough to absorb any single
        # main-thread paint storm, short enough that a seek + immediate
        # resume feels instant.
        self._ring = _PreMixedRing(
            capacity_frames=int(self.samplerate * 0.5),
            channels=2,
        )
        self._mixer = _MixerThread(
            engine=self,
            target_fill_frames=int(self.samplerate * 0.2),
        )
        self._mixer.start()

        # Per-speaker dub tracks. speaker_tracks[name] is the Track object.
        # subtitle_clips[id(subtitle)] is the SubtitleDubClip wrapping it.
        self.speaker_tracks = {}
        self.subtitle_clips = {}

        # Multiplier applied to every dub track's gain, driven by the
        # music/voice-separation slider in `playercontrols`. The rule:
        # dub gain follows the *background* gain. When the user pulls the
        # slider toward "voice only" they're trying to hear the original
        # vocals isolated — the dubs should fade out with the music. When
        # the slider sits at neutral or leans toward "music only" (the
        # normal dubbing scenario) the dubs play at full alongside the
        # preserved music. New speaker tracks pick this up on creation in
        # `_ensure_speaker_track` so a slider position set before any
        # dubs were generated still wins when dubs come in later.
        self.dub_separation_gain = 1.0

        # Audio-effects model (list of effect specs, see modules/audio_effects).
        # Chains are (re)assigned to the background / vocals / per-speaker tracks
        # by `apply_effects`, and re-applied when a new speaker track appears.
        self.audio_effects = []

        # ---- DEBUG INSTRUMENTATION ----------------------------------
        # Aggregates filled by _callback (audio thread) and dumped by the
        # printer (main thread) once per second so the realtime callback
        # never blocks on stderr.
        self._dbg_lock = threading.Lock()
        self._dbg_callbacks = 0
        self._dbg_xruns = 0
        self._dbg_last_status_flags = []
        self._dbg_max_callback_ms = 0.0
        self._dbg_sum_callback_ms = 0.0
        self._dbg_over_budget = 0  # callbacks that ran >80% of the budget
        self._dbg_max_in_window = 0
        self._dbg_last_total_clips = 0
        self._dbg_budget_ms = (self.blocksize / self.samplerate) * 1000.0
        self._dbg_running = DEBUG_AUDIO
        # Tracks the wall-clock at end of previous callback so we can spot
        # gaps where the audio thread didn't get scheduled at all (GIL
        # starvation, GC pause, OS preemption).
        self._dbg_prev_callback_end = 0.0
        self._dbg_max_gap_ms = 0.0
        self._dbg_gap_starvations = 0
        self._dbg_expected_interval_ms = self._dbg_budget_ms
        # GC monitor — a stop-the-world gen-2 collection is the classic
        # cause of a multi-frame audio dropout in long-running Python apps.
        self._dbg_gc_collections = [0, 0, 0]
        self._dbg_gc_during_playback = 0
        # Bounded queue the audio callback puts XRUN/SLOW/STARVED events
        # onto. The printer thread drains it. put_nowait drops events when
        # full — by design: the callback must never block on a queue.
        self._dbg_event_queue = Queue(maxsize=64)
        if DEBUG_AUDIO:
            sys.stderr.write(
                f'[audioengine] DEBUG ON  sr={self.samplerate} '
                f'blocksize={self.blocksize} budget={self._dbg_budget_ms:.1f}ms\n'
            )
            sys.stderr.flush()

            def _gc_cb(phase, info):
                if phase != 'stop':
                    return
                gen = info.get('generation', 0)
                with self._dbg_lock:
                    if 0 <= gen < 3:
                        self._dbg_gc_collections[gen] += 1
                    if self.playing:
                        self._dbg_gc_during_playback += 1
                        # Don't write to stderr here — gc.callbacks can fire
                        # on any thread, including the audio callback if it
                        # ever allocates. Stats are surfaced by the printer.

            self._dbg_gc_cb = _gc_cb
            gc.callbacks.append(_gc_cb)

            self._dbg_thread = threading.Thread(
                target=self._dbg_printer_loop, daemon=True, name='audioengine-debug'
            )
            self._dbg_thread.start()

            # Watchdog: a plain Python thread that sleeps 10ms and notes any
            # gap > 30ms between wakes. If this thread also experiences large
            # gaps during playback, the GIL is being held by something else
            # (and the audio callback's gaps are explained too). If the
            # watchdog stays smooth while audio still chunks, the problem is
            # below Python (PortAudio/PipeWire/OS scheduling) and we should
            # stop optimizing Python.
            self._dbg_watchdog_max_gap_ms = 0.0
            self._dbg_watchdog_long_gaps = 0
            self._dbg_watchdog_ticks = 0
            self._dbg_watchdog_thread = threading.Thread(
                target=self._dbg_watchdog_loop, daemon=True, name='audioengine-watchdog'
            )
            self._dbg_watchdog_thread.start()

        # ``latency='high'`` asks PortAudio for the largest device-side
        # buffer it deems "high latency" (typically 50-200 ms on Linux
        # ALSA / PipeWire). That buffer is what protects us from
        # underruns when the audio thread is starved by GIL contention
        # or a slow paint event on the main thread — a single 60 Hz
        # repaint that overshoots by 30 ms can't kill playback if the
        # device has 100+ ms of slack queued ahead of it. Without this,
        # PortAudio picks the device default — which on PipeWire can
        # land as low as 10 ms, leaving zero headroom.
        #
        # ``prime_output_buffers_using_stream_callback=True`` runs the
        # callback to FILL the buffer before ``stream.start()`` returns,
        # so the very first audio block already exists when the device
        # asks for it (instead of being filled during the first
        # callback, which is the most jitter-prone one).
        self.stream = sd.OutputStream(
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            channels=2,
            dtype='float32',
            latency='high',
            prime_output_buffers_using_stream_callback=True,
            callback=self._callback,
        )

    @property
    def playhead(self):
        with self._playhead_lock:
            return self._playhead

    @playhead.setter
    def playhead(self, value):
        with self._playhead_lock:
            self._playhead = value

    def load_audio(self, filepath):
        """Load audio source (streams from disk, not RAM)"""
        info = sf.info(filepath)
        if info.samplerate != self.samplerate:
            raise ValueError(f"Sample rate mismatch: file has {info.samplerate}, expected {self.samplerate}")
        return AudioSource(filepath, self.samplerate)

    def load_clip(self, source, start_time=0.0, start_offset=0.0, duration=None, gain=1.0, speed=1.0):
        return Clip(source, start_time, start_offset, duration, gain, speed, blocksize=self.blocksize)

    def add_track(self, track):
        self.tracks.append(track)
        return track

    def generate_track(self):
        return Track()

    def _callback(self, outdata, frames, _time_info, status):
        # Producer/consumer model: the heavy mixing happens in
        # ``_MixerThread`` and lands in ``self._ring``. All this
        # callback does is copy ``frames`` frames out of the ring into
        # ``outdata`` and advance the consumer playhead.
        #
        # That makes the GIL hold per callback ~50 µs (a numpy slice
        # copy), down from 1-30 ms (mixing). Even a main-thread paint
        # storm that holds the GIL for 200+ ms can't underrun the
        # device anymore: the callback wakes, copies, releases, and the
        # consumer keeps draining whatever the mixer thread had time to
        # produce before the storm started.
        cb_t0 = time.perf_counter() if DEBUG_AUDIO else 0.0
        gap_ms = 0.0
        if DEBUG_AUDIO and self._dbg_prev_callback_end > 0.0:
            gap_ms = (cb_t0 - self._dbg_prev_callback_end) * 1000.0

        # Pop pre-mixed audio. ``pop_into`` zeroes the tail if the ring
        # is short, so a producer stall manifests as silence (not a
        # crash) and the consumer keeps draining smoothly afterwards.
        frames_from_ring = self._ring.pop_into(outdata, frames)

        with self._playhead_lock:
            self._playhead += (frames / self.samplerate) * self.speed

        if DEBUG_AUDIO:
            cb_end = time.perf_counter()
            cb_ms = (cb_end - cb_t0) * 1000.0
            flags = _decode_callback_status(status) if status else []
            # A short read from the ring means the producer fell behind
            # — surface that explicitly so the diagnostic distinguishes
            # "callback was slow" (now impossible in steady state) from
            # "mixer thread couldn't keep up."
            underrun = frames_from_ring < frames
            starved = (
                self._dbg_prev_callback_end > 0.0
                and gap_ms > self._dbg_expected_interval_ms * 1.5
            )
            if flags or underrun or starved:
                tag = ('XRUN' if flags
                       else ('UNDERFILL' if underrun else 'STARVED'))
                event = (tag, cb_ms, gap_ms,
                         frames_from_ring, frames,
                         self._ring.fill_level(),
                         tuple(flags))
                try:
                    self._dbg_event_queue.put_nowait(event)
                except Exception:
                    pass
            with self._dbg_lock:
                self._dbg_callbacks += 1
                if flags:
                    self._dbg_xruns += 1
                    self._dbg_last_status_flags = flags
                if cb_ms > self._dbg_budget_ms * 0.8:
                    self._dbg_over_budget += 1
                if cb_ms > self._dbg_max_callback_ms:
                    self._dbg_max_callback_ms = cb_ms
                self._dbg_sum_callback_ms += cb_ms
                if gap_ms > self._dbg_max_gap_ms:
                    self._dbg_max_gap_ms = gap_ms
                if starved:
                    self._dbg_gap_starvations += 1
            self._dbg_prev_callback_end = cb_end

    def _dbg_watchdog_loop(self):
        """Pure-Python loop that sleeps 10ms and measures gaps. Used to
        distinguish GIL contention (everyone gets gaps) from audio-stack
        latency (only the callback gets gaps).

        When a long gap is observed AND playback is happening, capture all
        other threads' stack frames — whichever thread held the GIL during
        the gap is the one we want to find. We snapshot the stacks here on
        the watchdog thread (which got the GIL the moment the holder
        released it), then aggregate the most-seen top frame for the
        per-second printer to dump."""
        self._dbg_top_frames = {}  # 'file:line:func' -> count
        prev = time.perf_counter()
        my_tid = threading.get_ident()
        while self._dbg_running:
            time.sleep(0.010)
            now = time.perf_counter()
            gap_ms = (now - prev) * 1000.0
            prev = now
            stacks_to_record = None
            if gap_ms > 30.0 and self.playing:
                # Capture frames immediately while the just-released GIL
                # holder's call site is still likely on top of its stack.
                frames = sys._current_frames()
                stacks_to_record = []
                for tid, frame in frames.items():
                    if tid == my_tid:
                        continue
                    # Top of stack: most recent line being executed.
                    stacks_to_record.append((tid, frame.f_code.co_filename,
                                             frame.f_lineno, frame.f_code.co_name))
            with self._dbg_lock:
                self._dbg_watchdog_ticks += 1
                if gap_ms > self._dbg_watchdog_max_gap_ms:
                    self._dbg_watchdog_max_gap_ms = gap_ms
                if gap_ms > 30.0:
                    self._dbg_watchdog_long_gaps += 1
                if stacks_to_record:
                    for _tid, fname, lineno, func in stacks_to_record:
                        # Drop the absolute path prefix to keep the line short.
                        short = fname.split('/subtitld/')[-1] if '/subtitld/' in fname else fname.rsplit('/', 1)[-1]
                        key = f'{short}:{lineno}:{func}'
                        self._dbg_top_frames[key] = self._dbg_top_frames.get(key, 0) + 1

    def _dbg_printer_loop(self):
        """Background thread — prints aggregated audio-callback stats every
        second. Runs off the realtime audio thread so stderr I/O can't stall
        the callback."""
        while self._dbg_running:
            time.sleep(1.0)
            with self._dbg_lock:
                cb = self._dbg_callbacks
                xruns = self._dbg_xruns
                over = self._dbg_over_budget
                mx = self._dbg_max_callback_ms
                avg = (self._dbg_sum_callback_ms / cb) if cb else 0.0
                last_flags = self._dbg_last_status_flags
                max_in = self._dbg_max_in_window
                total = self._dbg_last_total_clips
                max_gap = self._dbg_max_gap_ms
                starvations = self._dbg_gap_starvations
                gc_g0, gc_g1, gc_g2 = self._dbg_gc_collections
                gc_play = self._dbg_gc_during_playback
                wd_max = self._dbg_watchdog_max_gap_ms
                wd_long = self._dbg_watchdog_long_gaps
                wd_ticks = self._dbg_watchdog_ticks
                top_frames = self._dbg_top_frames
                self._dbg_watchdog_max_gap_ms = 0.0
                self._dbg_watchdog_long_gaps = 0
                self._dbg_watchdog_ticks = 0
                self._dbg_top_frames = {}
                self._dbg_callbacks = 0
                self._dbg_xruns = 0
                self._dbg_over_budget = 0
                self._dbg_max_callback_ms = 0.0
                self._dbg_sum_callback_ms = 0.0
                self._dbg_max_in_window = 0
                self._dbg_last_status_flags = []
                self._dbg_max_gap_ms = 0.0
                self._dbg_gap_starvations = 0
                self._dbg_gc_collections = [0, 0, 0]
                self._dbg_gc_during_playback = 0
            # Drain per-callback events queued by the audio thread. These
            # are the SLOW/STARVED/XRUN messages that used to be written
            # inline from _callback (a real-time hazard). Cap the drain so
            # a burst can't keep us in this loop for too long.
            drained = 0
            while drained < 32:
                try:
                    kind, cb_ms, gap_ms, ring_n, want_n, ring_fill, flags = \
                        self._dbg_event_queue.get_nowait()
                except Empty:
                    break
                drained += 1
                if kind == 'XRUN':
                    sys.stderr.write(
                        f'[audioengine] XRUN flags={",".join(flags)} '
                        f'cb_ms={cb_ms:.1f} gap_ms={gap_ms:.1f} '
                        f'ring={ring_n}/{want_n} fill={ring_fill}\n'
                    )
                elif kind == 'UNDERFILL':
                    # Producer (mixer thread) couldn't keep up — ring
                    # had ``ring_n`` frames ready when the callback
                    # wanted ``want_n``. Distinct from STARVED (which
                    # is about the audio thread being descheduled).
                    sys.stderr.write(
                        f'[audioengine] UNDERFILL ring={ring_n}/{want_n} '
                        f'cb_ms={cb_ms:.1f} gap_ms={gap_ms:.1f}\n'
                    )
                else:  # STARVED
                    sys.stderr.write(
                        f'[audioengine] STARVED gap_ms={gap_ms:.1f} '
                        f'expected={self._dbg_expected_interval_ms:.1f} '
                        f'cb_ms={cb_ms:.1f} ring_fill={ring_fill}\n'
                    )
            if cb == 0 and not self.playing:
                if drained:
                    sys.stderr.flush()
                continue
            sys.stderr.write(
                f'[audioengine] 1s: callbacks={cb} xruns={xruns} '
                f'over_budget={over} starved={starvations} '
                f'avg_ms={avg:.2f} max_ms={mx:.2f} max_gap_ms={max_gap:.1f} '
                f'gc=g0:{gc_g0}/g1:{gc_g1}/g2:{gc_g2} gc_during_play={gc_play} '
                f'wd=ticks={wd_ticks}/exp~100 long_gaps={wd_long} max={wd_max:.1f}ms '
                f'max_in_window={max_in}/{total} clips '
                f'playing={self.playing}'
                + (f' last_flags={",".join(last_flags)}' if last_flags else '')
                + '\n'
            )
            if top_frames:
                # Top 5 frames seen at the moment of long-gap events.
                ranked = sorted(top_frames.items(), key=lambda kv: kv[1], reverse=True)[:5]
                sys.stderr.write(
                    '[audioengine] 1s top GIL holders during gaps: '
                    + ' | '.join(f'{loc}={n}' for loc, n in ranked)
                    + '\n'
                )
            sys.stderr.flush()

    def play(self, position=0.0):
        if not self.playing:
            # Cyclic GC is the dominant cause of audio underruns in projects
            # with hundreds of dubs: each scan of the object graph blocks for
            # 30-100ms, longer than one audio block. Refcounting still
            # frees everything immediately; we just defer cycle detection
            # until pause/stop, when a chunk on the audio thread is harmless.
            # (Less critical now that the audio callback is just a memcpy,
            # but the mixer thread is still subject to GC pauses — so keep.)
            self._gc_was_enabled = gc.isenabled()
            gc.disable()
            self.playhead = position
            for track in self.tracks:
                for clip in track.clips:
                    clip.clear_cache()
            # Resume the mixer thread; it clears the ring and starts
            # producing from `position`. Then pre-fill briefly so the
            # first audio callback already has data — without this,
            # play() returns immediately and the device buffer takes
            # one full block to fill, manifesting as a "muted first
            # block" click on play.
            self._mixer.resume(position)
            deadline = time.monotonic() + 0.1
            target = self.blocksize * 2
            while (time.monotonic() < deadline
                    and self._ring.fill_level() < target):
                time.sleep(0.002)
            self.stream.start()
            self.playing = True

    def pause(self):
        if self.playing:
            self.stream.stop()
            self.playing = False
            # Stop the mixer so it doesn't keep mixing audio nobody
            # will hear. The ring still holds whatever was pre-mixed;
            # next play() clears it on resume.
            self._mixer.pause()
            if getattr(self, '_gc_was_enabled', True):
                gc.enable()
            # Catch up on any cycles that accumulated while playback was
            # suppressing collection.
            gc.collect()

    def stop(self):
        self.pause()
        self.seek(0.0)

    def seek(self, seconds):
        self.playhead = seconds
        # Re-anchor the mixer to the new position. If we're playing,
        # this also clears the ring so the listener doesn't hear ~200 ms
        # of stale pre-roll from the old position before the new audio
        # arrives. If we're paused, the next play() will re-resume from
        # the new playhead anyway.
        self._mixer.reseek(seconds)
        for track in self.tracks:
            for clip in track.clips:
                clip.clear_cache()

    def set_gain(self, track, gain):
        track.gain = gain

    def set_speed(self, speed):
        self.speed = max(0.0, speed)

    def _ensure_speaker_track(self, speaker):
        track = self.speaker_tracks.get(speaker)
        if track is None:
            track = Track()
            # Honor whatever the separation slider asked for at the
            # time this dub was generated — see dub_separation_gain.
            track.gain = self.dub_separation_gain
            self.speaker_tracks[speaker] = track
            self.tracks.append(track)
            # New speaker → apply any effects targeting it.
            self._apply_effects_to_track(track, 'speaker', speaker)
        return track

    # ---- audio effects --------------------------------------------------
    def set_audio_effects(self, specs):
        """Replace the effects model and (re)assign chains to all tracks."""
        self.audio_effects = list(specs or [])
        self.apply_effects()

    def apply_effects(self):
        """(Re)assign effect chains to the background / vocals / speaker tracks
        from the current model. Safe to call whenever the model or the set of
        tracks changes."""
        self._apply_effects_to_track(getattr(self, 'background_sound', None), 'background')
        self._apply_effects_to_track(getattr(self, 'vocals_sound', None), 'voice')
        for name, track in self.speaker_tracks.items():
            self._apply_effects_to_track(track, 'speaker', name)

    def _apply_effects_to_track(self, track, kind, speaker=None):
        if track is None:
            return
        from subtitld.modules import audio_effects
        specs = audio_effects.specs_for_target(self.audio_effects, kind, speaker)
        if not specs:
            track.effect_chain = None
            return
        chain = track.effect_chain
        if chain is None:
            track.effect_chain = audio_effects.EffectChain(specs, self.samplerate)
        else:
            chain.rebuild(specs)   # keeps filter/envelope state when unchanged

    def set_dub_separation_gain(self, gain):
        """Apply `gain` to every existing dub track and remember it for
        any tracks created later. Called by the music/voice-separation
        slider so dubs fade out alongside the background music when the
        user wants to hear the isolated original vocals."""
        gain = max(0.0, min(1.0, float(gain)))
        self.dub_separation_gain = gain
        for track in self.speaker_tracks.values():
            track.gain = gain

    def sync_subtitle_dubs(self, segments, default_speaker='A'):
        """Reconcile per-speaker dub tracks with the current subtitle list.

        Idempotent: call after loading a project, generating a dub, swapping
        the playlist, or reassigning a subtitle's speaker. Subtitles without
        a `dubbing` list are dropped from the engine.

        Identity matching: the per-clip cache is keyed by ``id(subtitle)``
        for O(1) lookup, but ``id(sub)`` changes whenever ``history.py``
        deep-copies the segments list (Ctrl+Z / Ctrl+Shift+Z). Without
        a fallback, every undo destroys every SubtitleDubClip and the
        new clips have empty ``_loaded`` caches — manifesting as silent
        playback until the background loader catches up (visible as
        "clips are on the timeline but inaudible after undo"). So we
        ALSO build a dub-identity lookup (the dub's ``uid``, or its
        ``path`` for legacy dubs) and rebind the existing clip onto the
        freshly-restored subtitle dict when the id() lookup misses but
        the dub identity matches. The clip's pre-loaded audio data
        survives unchanged."""
        # Pre-pass: build dub-identity → (old_sub_id, clip) lookup, so
        # we can transplant clips whose subtitle dict identity changed
        # (typical after undo/redo) without losing their audio cache.
        existing_by_dub_key = {}
        for old_sub_id, clip in self.subtitle_clips.items():
            old_dub = clip._current_dub()
            if old_dub is None:
                continue
            key = old_dub.get('uid') or old_dub.get('path')
            if key:
                existing_by_dub_key[key] = (old_sub_id, clip)

        seen = set()

        for sub in segments:
            if not sub.get('dubbing'):
                continue
            speaker = sub.get('speaker', default_speaker)
            track = self._ensure_speaker_track(speaker)
            sub_id = id(sub)
            seen.add(sub_id)
            dub = _first_unmuted_dub(sub['dubbing'])

            # Resolution order: dub-identity match (rewires across
            # undo/redo) → id-match (steady state) → new clip.
            clip = None
            dub_key = dub.get('uid') or dub.get('path')
            if dub_key and dub_key in existing_by_dub_key:
                old_sub_id, candidate = existing_by_dub_key.pop(dub_key)
                if old_sub_id != sub_id:
                    # Re-key the clip in the subtitle_clips map and
                    # rebind its subtitle reference so `_current_dub()`
                    # reads from the live (new) dict. Direct attribute
                    # assignment is GIL-atomic; the mixer thread sees
                    # either the old or the new ref, never a torn state.
                    self.subtitle_clips.pop(old_sub_id, None)
                    self.subtitle_clips[sub_id] = candidate
                    candidate.subtitle = sub
                clip = candidate

            if clip is None:
                clip = self.subtitle_clips.get(sub_id)

            if clip is None:
                clip = SubtitleDubClip(sub, self.samplerate)
                self.subtitle_clips[sub_id] = clip
                track.add_clip(clip)
            else:
                # Ensure the clip lives on the correct speaker track.
                # Speaker may have changed (undo across a speaker
                # reassign) OR we just rewired from another clip; in
                # both cases the right answer is "move to the speaker
                # this subtitle is currently tagged with."
                if clip not in track.clips:
                    for other in self.speaker_tracks.values():
                        if other is not track and clip in other.clips:
                            other.clips.remove(clip)
                    track.add_clip(clip)
            # Warm every audio path the dub references through the
            # background loader queue. Segments may reference multiple
            # files after a clone-ref split or ASR-driven trim; the helper
            # falls back to `dub['path']` for legacy single-file dubs.
            #
            # Why async, not `clip.preload()`: on a 300-subtitle project
            # this loop fires 300+ `sf.SoundFile.read()` + resample + int16
            # pack operations. Synchronously on the main thread that's
            # seconds of frozen UI right after the user opens a recent
            # file — the dominant cost left after Phase 1 zip extract was
            # shrunk. The background loader is already running, already
            # designed for exactly this pattern (used on audio-callback
            # cache miss), and the audio engine tolerates a not-yet-loaded
            # dub by producing silence + re-enqueueing through `read()`.
            #
            # Race note: if a USFX dub file hasn't landed on disk yet
            # (Phase 2 extractor still streaming), the bg loader's
            # `sf.SoundFile` open raises FileNotFoundError → caught by
            # `preload()`'s catch-all → `_load_pending` is dropped in the
            # loader's `finally`. When `on_usfx_member_ready` fires for
            # that same path it re-enqueues and the load succeeds.
            from subtitld.modules import dub_clip
            for path in dub_clip.collect_segment_paths(dub):
                clip._request_async_preload(path)

        for sub_id in list(self.subtitle_clips):
            if sub_id in seen:
                continue
            clip = self.subtitle_clips.pop(sub_id)
            for track in self.speaker_tracks.values():
                if clip in track.clips:
                    track.clips.remove(clip)
                    break
            clip.shutdown()

    def on_usfx_member_ready(self, arcname, target_path):
        """Slot for `signals.SIGNALS.usfx_member_ready`.

        Called when the USFX Phase 2 extractor has finished writing a
        deferred zip member. For dub WAVs, find the matching subtitle
        clip and enqueue a preload through the existing background
        loader, so the audio data is cached before the playhead reaches
        the clip. Other arcnames (waveform.npy, FLAC stems) are no-ops
        here — they belong to the timeline / audio-separation paths.

        Safe to call from any thread because:
          * `subtitle_clips.values()` reads a dict that's mutated only
            from the main thread; the wrapper that connects this slot
            (in `preview_panel`) routes through a main-thread QObject so
            Qt's auto-promote puts us on the main thread already.
          * `_request_async_preload` enqueues onto a Queue (thread-safe
            by construction) and never blocks.
        """
        if not arcname.startswith('assets/dubs/'):
            return
        for clip in self.subtitle_clips.values():
            dub = clip._current_dub()
            if dub is None:
                continue
            # Match by full resolved path — the USFX parse stored the
            # extract_dir-joined arcname in `dub['path']`, and the
            # extractor writes to that same path. Identity match is
            # cheap; legacy multi-segment dubs would also match if any
            # segment shares the path.
            if dub.get('path') == target_path:
                clip._request_async_preload(target_path)

    def shutdown(self):
        """Cleanly shut down the engine and all clip prefetch threads."""
        self.stop()
        self.stream.close()
        # Stop the mixer thread before clips' shutdown — clips clear
        # cached source data, so a still-running mixer would race the
        # teardown.
        self._mixer.stop()
        self._mixer.join(timeout=1.0)
        for track in self.tracks:
            for clip in track.clips:
                clip.shutdown()

    def render_buffer(self, start=0.0, end=None, samplerate=None, tracks=None, blocksize=4096):
        """Render the mix offline into a numpy (frames, 2) array.

        start / end in seconds. If end is None, uses the video duration or the
        latest clip endpoint. If `tracks` is provided, only those Track objects
        are mixed (for rendering stems); otherwise all tracks mix together."""
        sr = int(samplerate or self.samplerate)
        mix_tracks = list(tracks) if tracks is not None else list(self.tracks)

        if end is None:
            latest = 0.0
            for track in mix_tracks:
                for clip in track.clips:
                    if isinstance(clip, SubtitleDubClip):
                        dub = clip._current_dub()
                        if dub:
                            try:
                                path = dub.get('path')
                                if path and path in clip._loaded:
                                    _, src_sr, total = clip._loaded[path]
                                    latest = max(latest, float(dub.get('start', 0.0)) + total / src_sr)
                            except Exception:
                                pass
            end = max(end or 0.0, latest)

        if end <= start:
            return np.zeros((0, 2), dtype=np.float32)

        total_frames = int(round((end - start) * sr))
        out = np.zeros((total_frames, 2), dtype=np.float32)

        written = 0
        playhead = float(start)
        while written < total_frames:
            frames = min(blocksize, total_frames - written)
            block = np.zeros((frames, 2), dtype=np.float32)
            for track in mix_tracks:
                td = track.read(playhead, frames, sr, self.buffer_pool)
                block += td
                self.buffer_pool.release(td)
            out[written:written + frames] = block
            written += frames
            playhead = start + written / sr

        np.clip(out, -1.0, 1.0, out=out)
        return out

    def get_memory_usage(self):
        """Estimate current resident audio memory in MB. Covers:
          * the BufferPool (small, ~half a MB cap)
          * Clip disk-streaming caches (~2 MB per Clip)
          * SubtitleDubClip preloaded dub data (the big one on dub-heavy
            projects — int16 mono at engine SR, ~96 KB/s of dub speech)

        Used for diagnostics, not for any sizing decision. Cheap enough
        to call from a UI status line."""
        total_bytes = 0

        for buf in self.buffer_pool.pool:
            total_bytes += buf.nbytes

        for track in self.tracks:
            for clip in track.clips:
                # Streaming-audio Clips have double-buffered caches.
                cache_a = getattr(clip, '_cache_a', None)
                cache_b = getattr(clip, '_cache_b', None)
                if cache_a is not None and cache_a.get('data') is not None:
                    total_bytes += cache_a['data'].nbytes
                if cache_b is not None and cache_b.get('data') is not None:
                    total_bytes += cache_b['data'].nbytes
                # SubtitleDubClips hold one preloaded dub each.
                loaded = getattr(clip, '_loaded', None)
                if loaded:
                    for entry in loaded.values():
                        data = entry[0] if entry else None
                        if data is not None:
                            total_bytes += data.nbytes

        return total_bytes / (1024 * 1024)