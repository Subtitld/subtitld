"""Waveform and dub-clip workers: a worker is let go only once its thread has finished.

Each worker emits its result as the last thing its run() does, and the slot
for that result runs on the main thread while the worker thread may still be
returning. Dropping the last reference to the QThread in that slot destroyed
it while its thread was running, and Qt aborted the whole process ("QThread:
Destroyed while thread '' is still running"), e.g. when a zoom asked the
waveform for a new detail level. The results now come on their own signals,
and the workers are released on QThread's own `finished`.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, time, types, wave, shutil
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent
import numpy as np
app = QApplication([])
from subtitld.modules import session
from subtitld.interface import timeline

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


def pump_until(done, seconds=30):
    # A tight loop keeps the main thread idle and ready, so each result slot
    # runs the moment it's posted: the timing that used to abort.
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        app.processEvents()
    return done()


print('the result signals leave QThread\'s own finished alone')
for cls in (timeline.WaveformWorker, timeline.DubPeaksWorker, timeline.DubOnsetsWorker):
    check(cls.__name__, 'finished' in vars(cls), False)

print('zooming asks for new detail levels in quick succession')
counts = {'created': 0, 'destroyed': 0}
_worker_init = timeline.WaveformWorker.__init__


def counting_init(self, *args, **kwargs):
    _worker_init(self, *args, **kwargs)
    counts['created'] += 1
    self.destroyed.connect(lambda *_: counts.__setitem__('destroyed', counts['destroyed'] + 1))


timeline.WaveformWorker.__init__ = counting_init
rng = np.random.default_rng(0)
samples = (rng.random(48000 * 60, dtype=np.float32) * 2 - 1)
zoom_steps = [128 << i for i in range(10)]  # samples per pixel, one level each
rounds, completed = 25, 0
for _ in range(rounds):
    manager = timeline.WaveformManager(samples=samples)
    for samples_per_pixel in zoom_steps:
        manager.get_level(samples_per_pixel)
    if pump_until(lambda: not manager.workers and all(key in manager.levels for key in zoom_steps)):
        completed += 1
check('every round built all its levels', completed, rounds)
check('no worker left behind', manager.workers, {})
mins, maxs, per_bucket = manager.levels[1024]
buckets = manager.samples[:len(manager.samples) // 1024 * 1024].reshape(-1, 1024)
check('a level holds the right minimums', np.array_equal(mins, buckets.min(axis=1) / np.float32(32768)), True)
check('and maximums', np.array_equal(maxs, buckets.max(axis=1) / np.float32(32768)), True)
check('and its bucket size', per_bucket, 1024)
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
check('every worker thread object deleted', counts['destroyed'], counts['created'])
timeline.WaveformWorker.__init__ = _worker_init

print('dub clips: peaks and onsets, from a file and from one ffmpeg can\'t read')
if not shutil.which(session.FFMPEG_EXECUTABLE):
    print('  SKIP: no ffmpeg on PATH')
else:
    host = types.SimpleNamespace(dub_peaks={}, dub_workers={}, dub_onsets={}, dub_onset_workers={},
                                 update=lambda: None)
    for name in ('_dub_cache_signature', '_dub_cache_path', '_dub_onsets_cache_path',
                 '_request_dub_peaks', '_on_dub_peaks_ready', '_request_dub_onsets', '_on_dub_onsets_ready'):
        setattr(host, name, types.MethodType(getattr(timeline.Timeline, name), host))

    rate = 24000
    clips = []
    for index in range(4):
        path = os.path.join(_xdg, f'dub{index}.wav')
        with wave.open(path, 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            tone = 0.3 * np.sin(2 * np.pi * (220 + 110 * index) * np.arange(rate) / rate)
            out.writeframes((tone * 32767).astype('<i2').tobytes())
        clips.append(path)
    for path in clips:
        host._request_dub_peaks(path)
        host._request_dub_onsets(path)
    pump_until(lambda: all(path in host.dub_peaks and path in host.dub_onsets for path in clips)
               and not host.dub_workers and not host.dub_onset_workers)
    check('peaks for every clip', sorted(host.dub_peaks) == sorted(clips), True)
    check('their length', {round(host.dub_peaks[path][2], 2) for path in clips}, {1.0})
    check('onsets for every clip', sorted(host.dub_onsets) == sorted(clips), True)
    check('no peaks worker left behind', host.dub_workers, {})
    check('no onsets worker left behind', host.dub_onset_workers, {})

    broken = os.path.join(_xdg, 'broken.wav')
    Path(broken).write_bytes(b'not audio at all')
    host._request_dub_peaks(broken)
    host._request_dub_onsets(broken)
    peaks_worker, onsets_worker = host.dub_workers[broken], host.dub_onset_workers[broken]
    pump_until(lambda: peaks_worker.isFinished() and onsets_worker.isFinished())
    for _ in range(20):
        app.processEvents()
    check('no peaks for it', broken in host.dub_peaks, False)
    check('no onsets for it', broken in host.dub_onsets, False)
    host._request_dub_peaks(broken)
    host._request_dub_onsets(broken)
    check('the paint asking again doesn\'t rerun ffmpeg on it',
          (host.dub_workers[broken] is peaks_worker, host.dub_onset_workers[broken] is onsets_worker), (True, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
