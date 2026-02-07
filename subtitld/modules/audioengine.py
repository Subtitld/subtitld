import sounddevice as sd
import soundfile as sf
import numpy as np


class AudioSource:
    def __init__(self, data, samplerate):
        self.data = data
        self.samplerate = samplerate


class Clip:
    def __init__(
        self,
        source: AudioSource,
        start_time: float,      # seconds on timeline
        start_offset: float = 0.0,  # seconds inside audio
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

    def read(self, playhead, frames, samplerate):
        channels = self.source.data.shape[1]
        out = np.zeros((frames, channels), dtype=np.float32)

        if not self.enabled:
            return out

        # timeline window
        t0 = playhead
        t1 = playhead + frames / samplerate

        clip_end = (
            self.start_time + self.duration
            if self.duration is not None
            else float("inf")
        )

        # no overlap
        if t1 <= self.start_time or t0 >= clip_end:
            return out

        # overlapping window
        clip_t0 = max(t0, self.start_time)
        clip_t1 = min(t1, clip_end)

        out_start = int((clip_t0 - t0) * samplerate)
        out_end = int((clip_t1 - t0) * samplerate)

        # source time (seconds)
        src_time = (
            (clip_t0 - self.start_time) + self.start_offset
        ) * self.speed

        # source positions (float indices)
        src_pos = src_time * samplerate
        src_idx = src_pos + np.arange(out_end - out_start) * self.speed

        # linear interpolation
        i0 = np.floor(src_idx).astype(np.int64)
        i1 = i0 + 1
        frac = src_idx - i0

        valid = (i0 >= 0) & (i1 < len(self.source.data))
        if not np.any(valid):
            return out

        s0 = self.source.data[i0[valid]]
        s1 = self.source.data[i1[valid]]

        out[out_start:out_start + np.count_nonzero(valid)] = (
            (1.0 - frac[valid, None]) * s0 +
            frac[valid, None] * s1
        ) * self.gain

        return out




class Track:
    def __init__(self):
        self.clips = []
        self.gain = 1.0
        self.enabled = True

    def add_clip(self, clip):
        self.clips.append(clip)

    def read(self, playhead, frames, samplerate):
        channels = self.clips[0].source.data.shape[1] if self.clips else 2
        out = np.zeros((frames, channels), dtype=np.float32)

        if not self.enabled:
            return out

        for clip in self.clips:
            out += clip.read(playhead, frames, samplerate)

        return out * self.gain



class SoundDeviceAudioEngine:
    def __init__(self, samplerate=48000, blocksize=2048):
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.tracks = []
        self.original_track = None
        self.playing = False
        self.playhead = 0.0
        self.speed = 1.0

        self.stream = sd.OutputStream(
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            channels=2,
            dtype='float32',
            callback=self._callback
        )

    def load_audio(self, filepath):
        data, sr = sf.read(filepath, always_2d=True, dtype='float32')
        if sr != self.samplerate:
            raise ValueError("Sample rate mismatch")
        return AudioSource(data, sr)

    def load_clip(self, source, start_time=0.0, start_offset=0.0, duration=None, gain=1.0, speed=1.0):
        clip = Clip(
            source,
            start_time,
            start_offset,
            duration,
            gain,
            speed
        )
        return clip
        
    def add_track(self, track):
        self.tracks.append(track)
        return track

    def generate_track(self):
        track = Track()
        return track

    def _callback(self, outdata, frames, time, status):
        if status:
            print(status)

        outdata.fill(0.0)

        for track in self.tracks:
            outdata += track.read(self.playhead, frames, self.samplerate)

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

    def set_gain(self, track, gain):
        track.gain = gain

    def set_speed(self, speed):
        self.speed = max(0.0, speed)