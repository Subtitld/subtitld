"""waveform_cache: the waveform and dub-peak caches are saved and read without pickle. A waveform cache can
come from a .usfx or from a collaborator in live co-editing; a pickled one would run the code it carries
when the timeline loaded it. Such a file, or a cache saved by an earlier version, now reads as no cache.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os
import sys
import tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import utils, waveform_cache
from subtitld.interface import timeline

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


root = Path(tempfile.mkdtemp())
marker = root / 'ran'


class Payload:
    """What a hostile cache carries: unpickling it makes `marker`."""

    def __reduce__(self):
        return (os.mkdir, (str(marker),))


print('The waveform, saved and read back')
samples = (np.sin(np.arange(48000) / 50) * 30000).astype(np.int16)
levels = {512: (np.linspace(-1, 0, 93, dtype=np.float32), np.linspace(0, 1, 93, dtype=np.float32), 512),
          4096: (np.zeros(11, dtype=np.float32), np.ones(11, dtype=np.float32), 4096)}
path = root / 'key_waveform.npy'
waveform_cache.save_waveform(str(path), samples, levels)
check('kept under its .npy name (a .usfx and co-editing look for it)', sorted(p.name for p in root.iterdir()),
      ['key_waveform.npy'])
got = waveform_cache.load_waveform(str(path))
check('the samples', got is not None and np.array_equal(got[0], samples) and got[0].dtype == np.int16, True)
check('the levels', sorted(got[1]), [512, 4096])
check('a level', [np.array_equal(got[1][512][0], levels[512][0]), np.array_equal(got[1][512][1], levels[512][1]),
                  got[1][512][2]], [True, True, 512])
waveform_cache.save_waveform(str(path), samples, {})
check('no levels yet', waveform_cache.load_waveform(str(path))[1], {})

broken = root / 'full' / 'disk_waveform.npy'
broken.parent.mkdir()
real_savez = np.savez


def full_disk(handle, **arrays):
    handle.write(b'half')
    raise OSError(28, 'No space left on device')


np.savez = full_disk
try:
    waveform_cache.save_waveform(str(broken), samples, levels)
    check('a full disk: the save fails', False, True)
except OSError:
    check('a full disk: the save fails', True, True)
np.savez = real_savez
check('… leaving no half-written file', sorted(p.name for p in broken.parent.iterdir()), [])

print("A dub's peaks")
peaks = root / 'dub_peaks.npy'
waveform_cache.save_peaks(str(peaks), [-0.5, -0.25], [0.5, 0.25], 1.5)
got = waveform_cache.load_peaks(str(peaks))
check('read back', [got[0].tolist(), got[1].tolist(), got[2]], [[-0.5, -0.25], [0.5, 0.25], 1.5])

print('Files that are not caches of this form')
hostile = root / 'hostile_waveform.npy'
np.save(hostile, np.array({'samples': samples, 'levels': {}, 'x': Payload()}, dtype=object), allow_pickle=True)
check('pickled, as an earlier version saved it (here carrying code): no cache',
      waveform_cache.load_waveform(str(hostile)), None)
check('… and its code never ran', marker.exists(), False)
np.save(hostile, np.array([Payload()], dtype=object), allow_pickle=True)
check('a dub cache the same way: no cache', waveform_cache.load_peaks(str(hostile)), None)
with open(hostile, 'wb') as handle:
    np.savez(handle, samples=samples, level_keys=np.array([Payload()], dtype=object))
check('an object array inside the zip: no cache', waveform_cache.load_waveform(str(hostile)), None)
check('… and still no code ran', marker.exists(), False)
np.save(hostile, samples)
check('a bare array: no cache', waveform_cache.load_waveform(str(hostile)), None)
hostile.write_bytes(b'PK\x03\x04 not really a zip')
check('a broken zip: no cache', waveform_cache.load_waveform(str(hostile)), None)
hostile.write_bytes(b'')
check('empty: no cache', waveform_cache.load_peaks(str(hostile)), None)
with open(hostile, 'wb') as handle:
    np.savez(handle, samples=samples, level_keys=np.array([512]), level_buckets=np.array([512]))
check('a level without its arrays: no cache', waveform_cache.load_waveform(str(hostile)), None)
with open(hostile, 'wb') as handle:
    np.savez(handle, samples=np.array(['text']))
check('text instead of numbers: no cache', waveform_cache.load_waveform(str(hostile)), None)
check('missing: no cache', waveform_cache.load_peaks(str(root / 'nothing.npy')), None)

print("The timeline's waveform")
video = root / 'video.mkv'
video.write_bytes(b'not a video, only something to key the cache on')
manager = timeline.WaveformManager(filepath=str(video))
cache = Path(manager._cache_path())
np.save(cache, np.array({'samples': samples, 'levels': {}, 'x': Payload()}, dtype=object), allow_pickle=True)
check('a pickled cache (from a .usfx or a collaborator): not used', manager._load_from_cache(), False)
check('… and its code never ran', marker.exists(), False)
manager.samples, manager.levels = samples, levels
manager._save_to_cache()
fresh = timeline.WaveformManager(filepath=str(video))
check('saved again in the new form, it loads', fresh._load_from_cache(), True)
check('… with its samples and levels', [np.array_equal(fresh.samples, samples), sorted(fresh.levels)], [True, [512, 4096]])
check('the cache key is the one the extractor and co-editing use',
      cache.name, f'{utils.get_cache_key(str(video))}_waveform.npy')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
