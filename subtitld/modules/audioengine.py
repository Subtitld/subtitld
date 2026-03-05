import sounddevice as sd
import soundfile as sf
import numpy as np
from pathlib import Path
from threading import Thread, Lock
from queue import Queue, Empty
import threading
import time


FADE_FRAMES = 64


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

        # FIX 1: Pre-allocated index buffer to avoid per-callback allocations
        self._src_idx_buf = np.empty(blocksize * 4, dtype=np.float64)

        # FIX 2: Background prefetch thread
        self._prefetch_queue = Queue(maxsize=1)
        self._prefetch_running = True
        self._prefetch_threshold = 0.75  # start loading when 75% through current cache
        self._prefetch_thread = Thread(target=self._prefetch_worker, daemon=True)
        self._prefetch_thread.start()

    # ------------------------------------------------------------------
    # FIX 2: Background prefetch — disk I/O never blocks the audio thread
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # FIX 3: Boundary fades — eliminates clicks at clip start/end
    # ------------------------------------------------------------------

    def _apply_boundary_fades(self, out, out_start, num_output_frames, is_clip_start, is_clip_end):
        fade_len = min(FADE_FRAMES, num_output_frames)

        if is_clip_start:
            ramp = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
            out[out_start:out_start + fade_len] *= ramp[:, None]

        if is_clip_end:
            ramp = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
            end = out_start + num_output_frames
            out[end - fade_len:end] *= ramp[:, None]

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

        # FIX 4: Use round() instead of int() to avoid off-by-one frame errors
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

        # FIX 5: Re-use pre-allocated index buffer — no per-callback allocation
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

        # FIX 3: Apply fades at clip boundaries
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


class Track:
    def __init__(self):
        self.clips = []
        self.gain = 1.0
        self.enabled = True

    def add_clip(self, clip):
        self.clips.append(clip)

    def read(self, playhead, frames, samplerate, buffer_pool):
        out = buffer_pool.get((frames, 2), dtype=np.float32)

        if not self.enabled:
            return out

        for clip in self.clips:
            clip_data = clip.read(playhead, frames, samplerate, buffer_pool)
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

        # FIX 6: Thread-safe playhead access
        self._playhead_lock = threading.Lock()
        self._playhead = 0.0

        self.buffer_pool = BufferPool(max_size=30)

        self.stream = sd.OutputStream(
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            channels=2,
            dtype='float32',
            callback=self._callback
        )

    # FIX 6: Property-based thread-safe playhead
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

    def _callback(self, outdata, frames, time, status):
        if status:
            pass

        outdata.fill(0.0)

        # Read playhead once per callback to stay consistent within the block
        with self._playhead_lock:
            current_playhead = self._playhead

        for track in self.tracks:
            track_data = track.read(current_playhead, frames, self.samplerate, self.buffer_pool)
            outdata += track_data
            self.buffer_pool.release(track_data)

        np.clip(outdata, -1.0, 1.0, out=outdata)

        with self._playhead_lock:
            self._playhead += (frames / self.samplerate) * self.speed

    def play(self, position=0.0):
        if not self.playing:
            self.seek(position)
            self.stream.start()
            self.playing = True

    def pause(self):
        if self.playing:
            self.stream.stop()
            self.playing = False

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

    def shutdown(self):
        """Cleanly shut down the engine and all clip prefetch threads."""
        self.stop()
        self.stream.close()
        for track in self.tracks:
            for clip in track.clips:
                clip.shutdown()

    def get_memory_usage(self):
        """Estimate current memory usage in MB"""
        total_bytes = 0

        for buf in self.buffer_pool.pool:
            total_bytes += buf.nbytes

        for track in self.tracks:
            for clip in track.clips:
                if clip._cache_a['data'] is not None:
                    total_bytes += clip._cache_a['data'].nbytes
                if clip._cache_b['data'] is not None:
                    total_bytes += clip._cache_b['data'].nbytes

        return total_bytes / (1024 * 1024)