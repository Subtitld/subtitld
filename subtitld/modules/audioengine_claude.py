import sounddevice as sd
import soundfile as sf
import numpy as np
from pathlib import Path


class AudioSource:
    """Streams audio from disk instead of loading into RAM"""
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
    def __init__(self, max_size=10):
        self.pool = []
        self.max_size = max_size
        
    def get(self, shape, dtype=np.float32):
        for i, buf in enumerate(self.pool):
            if buf.shape == shape and buf.dtype == dtype:
                return self.pool.pop(i)
        return np.zeros(shape, dtype=dtype)
    
    def release(self, buf):
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
        speed: float = 1.0
    ):
        self.source = source
        self.start_time = start_time
        self.start_offset = start_offset
        self.duration = duration
        self.gain = gain
        self.speed = speed
        self.enabled = True
        
        # Cache for small repeated reads
        self._cache_start = -1
        self._cache_data = None
        self._cache_size = 8192  # frames to cache

    def read(self, playhead, frames, samplerate, buffer_pool):
        """Read audio with streaming from disk"""
        out = buffer_pool.get((frames, 2), dtype=np.float32)

        if not self.enabled:
            return out

        # Timeline window
        t0 = playhead
        t1 = playhead + frames / samplerate

        clip_end = (
            self.start_time + self.duration
            if self.duration is not None
            else self.start_time + (self.source.frames / self.source.samplerate)
        )

        # No overlap
        if t1 <= self.start_time or t0 >= clip_end:
            return out

        # Overlapping window
        clip_t0 = max(t0, self.start_time)
        clip_t1 = min(t1, clip_end)

        out_start = int((clip_t0 - t0) * samplerate)
        out_end = int((clip_t1 - t0) * samplerate)

        # Source time (seconds)
        src_time = ((clip_t0 - self.start_time) + self.start_offset) * self.speed
        src_frame_start = src_time * self.source.samplerate
        
        # Calculate how many source frames we need
        num_output_frames = out_end - out_start
        num_source_frames = int(num_output_frames * self.speed) + 2  # +2 for interpolation
        
        # Read from disk (with simple caching)
        src_frame_start_int = int(src_frame_start)
        
        if (self._cache_data is None or 
            src_frame_start_int < self._cache_start or 
            src_frame_start_int + num_source_frames > self._cache_start + len(self._cache_data)):
            # Cache miss - read from disk
            read_start = max(0, src_frame_start_int)
            read_frames = min(num_source_frames + self._cache_size, 
                            self.source.frames - read_start)
            
            if read_frames > 0:
                self._cache_data = self.source.read_frames(read_start, read_frames)
                self._cache_start = read_start
        
        if self._cache_data is None or len(self._cache_data) == 0:
            return out
        
        # Calculate positions within cache
        cache_offset = src_frame_start_int - self._cache_start
        src_idx = cache_offset + (src_frame_start - src_frame_start_int) + np.arange(num_output_frames) * self.speed
        
        # Linear interpolation
        i0 = np.floor(src_idx).astype(np.int64)
        i1 = i0 + 1
        frac = src_idx - i0

        valid = (i0 >= 0) & (i1 < len(self._cache_data))
        if not np.any(valid):
            return out

        i0_valid = i0[valid]
        i1_valid = i1[valid]
        frac_valid = frac[valid]
        
        s0 = self._cache_data[i0_valid]
        s1 = self._cache_data[i1_valid]

        interpolated = (1.0 - frac_valid[:, None]) * s0 + frac_valid[:, None] * s1
        out[out_start:out_start + np.count_nonzero(valid)] = interpolated * self.gain

        return out


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
        self.playhead = 0.0
        self.speed = 1.0
        
        # Memory optimization
        self.buffer_pool = BufferPool(max_size=20)

        self.stream = sd.OutputStream(
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            channels=2,
            dtype='float32',
            callback=self._callback
        )

    def load_audio(self, filepath):
        """Load audio source (streams from disk, not RAM)"""
        info = sf.info(filepath)
        if info.samplerate != self.samplerate:
            raise ValueError(f"Sample rate mismatch: file has {info.samplerate}, expected {self.samplerate}")
        return AudioSource(filepath, self.samplerate)

    def load_clip(self, source, start_time=0.0, start_offset=0.0, duration=None, gain=1.0, speed=1.0):
        return Clip(source, start_time, start_offset, duration, gain, speed)
        
    def add_track(self, track):
        self.tracks.append(track)
        return track

    def generate_track(self):
        return Track()

    def _callback(self, outdata, frames, time, status):
        if status:
            pass

        outdata.fill(0.0)
        
        for track in self.tracks:
            track_data = track.read(self.playhead, frames, self.samplerate, self.buffer_pool)
            outdata += track_data
            self.buffer_pool.release(track_data)

        np.clip(outdata, -1.0, 1.0, out=outdata)
        self.playhead += (frames / self.samplerate) * self.speed

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
        # Clear clip caches on seek
        for track in self.tracks:
            for clip in track.clips:
                clip._cache_start = -1
                clip._cache_data = None

    def set_gain(self, track, gain):
        track.gain = gain

    def set_speed(self, speed):
        self.speed = max(0.0, speed)
        
    def get_memory_usage(self):
        """Estimate current memory usage in MB"""
        total_bytes = 0
        
        # Buffer pool
        for buf in self.buffer_pool.pool:
            total_bytes += buf.nbytes
        
        # Clip caches
        for track in self.tracks:
            for clip in track.clips:
                if clip._cache_data is not None:
                    total_bytes += clip._cache_data.nbytes
        
        return total_bytes / (1024 * 1024)