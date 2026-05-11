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


class SubtitleDubClip:
    """Wrapper clip that always plays subtitle['dubbing'][0] for a given subtitle dict.

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
        dubs = self.subtitle.get('dubbing')
        return dubs[0] if dubs else None

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
            # Evict stale entries: each clip's `read()` only ever consults
            # the current dub's path (`_current_dub()`), so older paths
            # left in `_loaded` from previous regenerations are dead
            # memory. A single 3-second mono int16 dub at 48 kHz is
            # 287 KB; across a 500-subtitle project with even modest
            # regeneration history this still leaks tens of MB. We keep
            # only the path we just loaded — the audio thread's
            # `.get(path)` lookup is atomic, so dropping siblings here
            # is callback-safe.
            stale = [p for p in self._loaded if p != path]
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
        path = dub.get('path')
        if not path:
            return None
        loaded = self._loaded.get(path)
        if loaded is None:
            # Not loaded yet — punt to the background thread. NEVER open
            # a SoundFile from here: a synchronous disk read on a cold
            # path can stall the audio callback past one block budget
            # (42.7 ms at 48 kHz / 2048) and the user hears a crackle in
            # what should be silence.
            self._request_async_preload(path)
            return None
        data, src_sr, total_frames = loaded
        start_time = float(dub.get('start', 0.0))
        clip_end = start_time + total_frames / src_sr
        t0 = playhead
        t1 = playhead + frames / samplerate
        if t1 <= start_time or t0 >= clip_end:
            return None
        out = buffer_pool.get((frames, 2), dtype=np.float32)
        clip_t0 = max(t0, start_time)
        clip_t1 = min(t1, clip_end)
        out_start = round((clip_t0 - t0) * samplerate)
        out_end = round((clip_t1 - t0) * samplerate)
        num_output_frames = out_end - out_start
        if num_output_frames <= 0:
            return out
        is_clip_start = clip_t0 <= start_time + 1.0 / samplerate
        is_clip_end = clip_t1 >= clip_end - 1.0 / samplerate

        # `data` is int16 mono at engine samplerate. Convert + scale to
        # float32 in [-1, 1] via one np.multiply, then broadcast to both
        # output channels. The scale factor folds the gain in so there's
        # exactly one multiply pass over the samples.
        scale = np.float32(self.gain) * _INT16_TO_FLOAT32

        # Fast path: source samplerate matches engine and no stretch in
        # flight. Just slice the int16 source, convert+scale once, write
        # the result into both output channels.
        if src_sr == samplerate and self.speed == 1.0:
            src_frame_start = int(round((clip_t0 - start_time) * src_sr))
            avail = total_frames - src_frame_start
            if avail <= 0:
                return out
            n = min(num_output_frames, avail)
            # One alloc: int16 → float32 with gain baked in. Cheap (n
            # ≤ blocksize = 2048 → 8 KB).
            mono_f32 = np.multiply(
                data[src_frame_start:src_frame_start + n], scale,
                dtype=np.float32, casting='unsafe',
            )
            out[out_start:out_start + n, 0] = mono_f32
            out[out_start:out_start + n, 1] = mono_f32
        else:
            # Slow path: speed != 1.0 (live stretch preview). Completed
            # stretches re-render the WAV file at the new rate so the
            # callback usually hits the fast path; this exists only so
            # the user-drag preview stays audible.
            ratio = (src_sr / samplerate) * self.speed
            src_frame_start = (clip_t0 - start_time) * src_sr
            src_idx = np.arange(num_output_frames, dtype=np.float64) * ratio + src_frame_start
            i0 = np.floor(src_idx).astype(np.int64)
            i1 = i0 + 1
            valid = (i0 >= 0) & (i1 < total_frames)
            valid_count = int(np.count_nonzero(valid))
            if valid_count == 0:
                return out
            i0v = i0[valid]
            i1v = i1[valid]
            fracv = (src_idx[valid] - i0v).astype(np.float32)
            # `data` is int16 mono → fancy-index produces int16 1-D
            # arrays. Lift to float32 for the interp arithmetic.
            s0 = data[i0v].astype(np.float32)
            s1 = data[i1v].astype(np.float32)
            mono_f32 = ((1.0 - fracv) * s0 + fracv * s1) * scale
            out[out_start:out_start + valid_count, 0] = mono_f32
            out[out_start:out_start + valid_count, 1] = mono_f32

        fade_len = min(FADE_FRAMES, num_output_frames)
        if is_clip_start:
            # Pre-built (FADE_FRAMES, 1) ramp shared by every clip in
            # the engine — broadcasts against the (n, 2) stereo slice
            # without any per-callback allocation.
            out[out_start:out_start + fade_len] *= _FADE_IN_RAMP[:fade_len]
        if is_clip_end:
            end = out_start + num_output_frames
            out[end - fade_len:end] *= _FADE_OUT_RAMP[:fade_len]
        return out

    def clear_cache(self):
        pass

    def shutdown(self):
        self._loaded.clear()


class Track:
    def __init__(self):
        self.clips = []
        self.gain = 1.0
        self.enabled = True
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

        out *= self.gain
        return out


class SoundDeviceAudioEngine:
    def __init__(self, samplerate=48000, blocksize=2048):
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

        # Per-speaker dub tracks. speaker_tracks[name] is the Track object.
        # subtitle_clips[id(subtitle)] is the SubtitleDubClip wrapping it.
        self.speaker_tracks = {}
        self.subtitle_clips = {}

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

        self.stream = sd.OutputStream(
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            channels=2,
            dtype='float32',
            callback=self._callback
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
        cb_t0 = time.perf_counter() if DEBUG_AUDIO else 0.0

        # Inter-callback gap = time the audio thread was *not* running. If
        # this exceeds the expected interval, the audio thread was starved
        # (GIL contention, GC, OS scheduler) — a fast callback alone can't
        # save us if it gets called late.
        gap_ms = 0.0
        if DEBUG_AUDIO and self._dbg_prev_callback_end > 0.0:
            gap_ms = (cb_t0 - self._dbg_prev_callback_end) * 1000.0

        outdata.fill(0.0)

        # Read playhead once per callback to stay consistent within the block
        with self._playhead_lock:
            current_playhead = self._playhead

        # Snapshot self.tracks: sync_subtitle_dubs (main thread) appends/
        # removes tracks during bulk dub generation, which would otherwise
        # mutate this list mid-iteration on the audio thread and crash.
        in_window_total = 0
        clips_total = 0
        for track in tuple(self.tracks):
            track_data = track.read(current_playhead, frames, self.samplerate, self.buffer_pool)
            outdata += track_data
            self.buffer_pool.release(track_data)
            in_window_total += getattr(track, 'last_in_window_clips', 0)
            clips_total += getattr(track, 'last_total_clips', 0)

        np.clip(outdata, -1.0, 1.0, out=outdata)

        with self._playhead_lock:
            self._playhead += (frames / self.samplerate) * self.speed

        if DEBUG_AUDIO:
            cb_end = time.perf_counter()
            cb_ms = (cb_end - cb_t0) * 1000.0
            flags = _decode_callback_status(status) if status else []
            # An inter-callback gap >1.5× the expected interval means the
            # audio thread was held off CPU (GIL/GC/scheduler) — that's the
            # actual cause of underruns when per-callback work is fast.
            starved = (
                self._dbg_prev_callback_end > 0.0
                and gap_ms > self._dbg_expected_interval_ms * 1.5
            )
            # Hand events off to the printer thread instead of writing to
            # stderr here. Inline stderr.write/flush from the audio thread
            # can block on the pipe and itself causes the underrun the
            # message is reporting — feedback loop.
            if flags or cb_ms > self._dbg_budget_ms * 0.8 or starved:
                event = (
                    'XRUN' if flags
                    else ('SLOW' if cb_ms > self._dbg_budget_ms * 0.8 else 'STARVED'),
                    cb_ms, gap_ms, in_window_total, clips_total,
                    current_playhead, tuple(flags),
                )
                try:
                    self._dbg_event_queue.put_nowait(event)
                except Exception:
                    pass  # queue full → drop, don't block the audio thread
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
                if in_window_total > self._dbg_max_in_window:
                    self._dbg_max_in_window = in_window_total
                self._dbg_last_total_clips = clips_total
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
                    kind, cb_ms, gap_ms, in_win, total_clips, ph, flags = \
                        self._dbg_event_queue.get_nowait()
                except Empty:
                    break
                drained += 1
                if kind == 'XRUN':
                    sys.stderr.write(
                        f'[audioengine] XRUN flags={",".join(flags)} '
                        f'cb_ms={cb_ms:.1f} gap_ms={gap_ms:.1f} '
                        f'budget={self._dbg_budget_ms:.1f} '
                        f'in_window={in_win}/{total_clips} '
                        f'playhead={ph:.2f}s\n'
                    )
                elif kind == 'SLOW':
                    sys.stderr.write(
                        f'[audioengine] SLOW cb_ms={cb_ms:.1f} gap_ms={gap_ms:.1f} '
                        f'budget={self._dbg_budget_ms:.1f} '
                        f'in_window={in_win}/{total_clips} '
                        f'playhead={ph:.2f}s\n'
                    )
                else:  # STARVED
                    sys.stderr.write(
                        f'[audioengine] STARVED gap_ms={gap_ms:.1f} '
                        f'expected={self._dbg_expected_interval_ms:.1f} '
                        f'cb_ms={cb_ms:.1f} '
                        f'in_window={in_win}/{total_clips} '
                        f'playhead={ph:.2f}s\n'
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
            # 30-100ms (longer than one audio block at 48kHz/2048), which the
            # OS sees as the callback never showing up. Refcounting still
            # frees everything immediately; we just defer cycle detection
            # until pause/stop, when a chunk on the audio thread is harmless.
            self._gc_was_enabled = gc.isenabled()
            gc.disable()
            self.seek(position)
            self.stream.start()
            self.playing = True

    def pause(self):
        if self.playing:
            self.stream.stop()
            self.playing = False
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
            self.speaker_tracks[speaker] = track
            self.tracks.append(track)
        return track

    def sync_subtitle_dubs(self, segments, default_speaker='A'):
        """Reconcile per-speaker dub tracks with the current subtitle list.

        Idempotent: call after loading a project, generating a dub, swapping
        the playlist, or reassigning a subtitle's speaker. Subtitles without
        a `dubbing` list are dropped from the engine."""
        seen = set()

        for sub in segments:
            if not sub.get('dubbing'):
                continue
            speaker = sub.get('speaker', default_speaker)
            track = self._ensure_speaker_track(speaker)
            sub_id = id(sub)
            seen.add(sub_id)

            clip = self.subtitle_clips.get(sub_id)
            if clip is None:
                clip = SubtitleDubClip(sub, self.samplerate)
                self.subtitle_clips[sub_id] = clip
                track.add_clip(clip)
            else:
                # Speaker may have changed — move the clip to the right track.
                for other in self.speaker_tracks.values():
                    if clip in other.clips and other is not track:
                        other.clips.remove(clip)
                        track.add_clip(clip)
                        break

            dub = sub['dubbing'][0]
            path = dub.get('path')
            if path:
                clip.preload(path)

        for sub_id in list(self.subtitle_clips):
            if sub_id in seen:
                continue
            clip = self.subtitle_clips.pop(sub_id)
            for track in self.speaker_tracks.values():
                if clip in track.clips:
                    track.clips.remove(clip)
                    break
            clip.shutdown()

    def shutdown(self):
        """Cleanly shut down the engine and all clip prefetch threads."""
        self.stop()
        self.stream.close()
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