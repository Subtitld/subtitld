"""A long film's sound without running out of memory, and a 5.1 one played (issue #59).

- The waveform loader reads ffmpeg a piece at a time and hands over int16, its
  loudest sample at full scale: what the float32 decode, normalised and packed
  by WaveformManager.set_samples, gave before.
- The onset detector's smaller chunks and framing by views find the same onsets
  as one chunk with the frames indexed out.
- A sidecar with more than two channels (5.1, as earlier versions decoded it)
  reads as stereo, and the player's decode now writes mono or stereo.
- A track that fails to mix leaves the mixer running.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import os, sys, tempfile, subprocess, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))
from PySide6.QtWidgets import QApplication
import numpy as np
import soundfile as sf
app = QApplication([])
from subtitld.modules import session, audioengine
from subtitld.modules.onset_detection import detect_onsets
from subtitld.interface import timeline

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


work = Path(tempfile.mkdtemp())
rate = 48000
rng = np.random.default_rng(59)
# 60 s, more than one 4 MB piece: quiet noise with louder bursts, peaking well under full scale.
sound = (rng.standard_normal(rate * 60) * 0.02).astype(np.float32)
for start in range(rate, sound.size - rate, rate // 3):
    sound[start:start + 800] += np.hanning(800).astype(np.float32) * 0.3
stereo = np.stack((sound, sound), axis=1)
wav = work / 'sound.wav'
sf.write(wav, stereo, rate, subtype='FLOAT')

print('the waveform loader hands over int16, as set_samples packed the float32 decode')
loader = timeline.AudioLoaderThread()
samples, samplerate = loader._load_audio(str(wav), rate)
check('dtype', samples.dtype, np.dtype(np.int16))
check('length', samples.size, sound.size)
check('loudest at full scale', int(np.abs(samples).max()), 32767)
raw = np.frombuffer(subprocess.run([session.FFMPEG_EXECUTABLE, '-v', 'error', '-i', str(wav), '-ac', '1', '-ar', str(rate),
                                    '-f', 'f32le', '-'], capture_output=True, check=True).stdout, dtype=np.float32)
before = (np.clip(raw / np.max(np.abs(raw)), -1.0, 1.0) * 32767.0).astype(np.int16)
check('within one step of the float32 path', int(np.abs(samples.astype(np.int32) - before).max()) <= 1, True)
manager = timeline.WaveformManager()
manager._start_worker_if_missing = lambda key: None
manager.set_samples(samples)
check('set_samples keeps it as it is', manager.samples is samples, True)

print('onsets from smaller chunks match one chunk with the frames indexed out')
step = rate // timeline.ONSET_DETECTION_SAMPLERATE
low = samples[::step].astype(np.float32) / np.float32(32768.0)
hop, frame_size = 160, 640
n_frames = (low.size - frame_size) // hop + 1
frames = low[np.arange(frame_size)[None, :] + (np.arange(n_frames) * hop)[:, None]] * np.hanning(frame_size).astype(np.float32)
mag = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
novelty = np.zeros(n_frames, dtype=np.float32)
novelty[1:] = np.clip(np.diff(mag, axis=0), 0, None).sum(axis=1)
novelty /= novelty.max()
novelty = np.clip(novelty - np.convolve(novelty, np.ones(15, dtype=np.float32) / 15, mode='same'), 0, None)
found = detect_onsets(low, timeline.ONSET_DETECTION_SAMPLERATE)
check('some found', found.size > 50, True)
peaks = np.where((novelty[1:-1] >= novelty[:-2]) & (novelty[1:-1] > novelty[2:]) & (novelty[1:-1] >= 0.18))[0] + 1
check('each one a peak of the whole-signal novelty', bool(np.isin(np.round(found * 100).astype(int), peaks).all()), True)
thread = timeline.OnsetDetectionThread(samples, rate)
results = []
thread.finished.connect(results.append)
thread.run()
check('the thread takes the int16 waveform', bool(results and np.array_equal(results[0], found)), True)

print('a 5.1 sidecar reads as stereo')
six = work / 'six.flac'
sf.write(six, np.tile(sound[:rate * 5, None], (1, 6)) * 0.5, rate)
source = audioengine.AudioSource(str(six), rate)
block = source.read_frames(rate, 2048)
check('shape', block.shape, (2048, 2))
check('their mean', bool(np.allclose(block[:, 0], sound[rate:rate + 2048] * 0.5, atol=1e-4)), True)
clip = audioengine.Clip(source, 0.0)
check('a clip mixes it', clip.read(1.0, 1024, rate, audioengine.BufferPool()).shape, (1024, 2))
clip.shutdown()

print("the player's decode writes mono or stereo")
from subtitld.interface import playercontrols
for channels in (1, 2, 6):
    media = work / f'in{channels}.wav'
    sf.write(media, np.tile(sound[:rate * 2, None], (1, channels)), rate)
    out = work / f'out{channels}.flac'
    playercontrols._OriginalDecodeThread(str(media), str(out)).run()
    check(f'{channels} channels in', sf.info(str(out)).channels, min(channels, 2))

print('a track that fails to mix leaves the mixer running')


class Failing:
    def read(self, *args):
        raise ValueError('could not broadcast')


class Engine:
    blocksize, samplerate, speed = 512, rate, 1.0
    buffer_pool = audioengine.BufferPool()
    _ring = audioengine._PreMixedRing(rate)
    tracks = [Failing(), audioengine.Track()]


mixer = audioengine._MixerThread(Engine, 4096)
mixer.start()
mixer.resume(0.0)
deadline = time.monotonic() + 5
while Engine._ring.fill_level() < 4096 and time.monotonic() < deadline:
    time.sleep(0.01)
check('blocks mixed', Engine._ring.fill_level() >= 4096, True)
check('still running', mixer.is_alive(), True)
mixer.stop()

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
