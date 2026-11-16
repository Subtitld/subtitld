"""The waveform caches on disk: a video's samples with their zoom levels, and a dub's peaks.

They are numpy arrays in a zip (np.savez), read with pickling off. A waveform cache can come from elsewhere
(inside a .usfx, from a collaborator in live co-editing), and a pickled one runs any code it carries when
it is loaded. A file not in this form, as the pickled caches of earlier versions are, reads as no cache:
the waveform is worked out again from the audio and saved in this form.

The files keep their .npy names, which a .usfx and the media exchanged in co-editing refer to.
"""

import os
import zipfile

import numpy as np


def _write(path, arrays):
    """Through a file object, as np.savez would add .npz to a name, and into place in one step so a reader
    (or a collaborator fetching it) never sees half of it."""
    temporary = f'{path}.part'
    try:
        with open(temporary, 'wb') as handle:
            np.savez(handle, **arrays)
        os.replace(temporary, path)
    except BaseException:
        # A full disk, say: not a half-written copy left taking the room it lacked.
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def _read(path):
    """The arrays in the file by name, or None when it isn't a cache of this form."""
    try:
        data = np.load(path, allow_pickle=False)
    except (OSError, ValueError, EOFError, zipfile.BadZipFile):
        return None
    if not isinstance(data, np.lib.npyio.NpzFile):
        return None
    try:
        with data:
            arrays = {name: data[name] for name in data.files}
    except (OSError, ValueError, EOFError, KeyError, zipfile.BadZipFile):
        return None
    if any(not isinstance(a, np.ndarray) or a.dtype.kind not in 'iuf' for a in arrays.values()):
        return None
    return arrays


def save_waveform(path, samples, levels):
    """`samples` (int16) and `levels`: {samples per bucket: (mins, maxs, samples per bucket)}."""
    arrays = {'samples': np.asarray(samples)}
    keys = sorted(levels)
    arrays['level_keys'] = np.asarray(keys, dtype=np.int64)
    arrays['level_buckets'] = np.asarray([levels[k][2] for k in keys], dtype=np.int64)
    for i, key in enumerate(keys):
        arrays[f'level_{i}_mins'] = np.asarray(levels[key][0], dtype=np.float32)
        arrays[f'level_{i}_maxs'] = np.asarray(levels[key][1], dtype=np.float32)
    _write(path, arrays)


def load_waveform(path):
    """(samples, levels) as save_waveform takes them, or None."""
    arrays = _read(path)
    if arrays is None or 'samples' not in arrays or arrays['samples'].ndim != 1:
        return None
    keys = arrays.get('level_keys', np.zeros(0, dtype=np.int64))
    buckets = arrays.get('level_buckets', np.zeros(0, dtype=np.int64))
    if keys.ndim != 1 or buckets.shape != keys.shape:
        return None
    levels = {}
    for i, (key, bucket) in enumerate(zip(keys.tolist(), buckets.tolist())):
        mins, maxs = arrays.get(f'level_{i}_mins'), arrays.get(f'level_{i}_maxs')
        if mins is None or maxs is None or mins.ndim != 1 or mins.shape != maxs.shape or int(bucket) < 1:
            return None
        levels[int(key)] = (mins, maxs, int(bucket))
    return arrays['samples'], levels


def save_peaks(path, mins, maxs, duration):
    _write(path, {'mins': np.asarray(mins, dtype=np.float32), 'maxs': np.asarray(maxs, dtype=np.float32),
                  'duration': np.asarray(float(duration), dtype=np.float64)})


def load_peaks(path):
    """(mins, maxs, duration in seconds) as save_peaks takes them, or None."""
    arrays = _read(path)
    if arrays is None or not {'mins', 'maxs', 'duration'} <= arrays.keys():
        return None
    mins, maxs, duration = arrays['mins'], arrays['maxs'], arrays['duration']
    if mins.ndim != 1 or mins.shape != maxs.shape or duration.shape != ():
        return None
    return mins, maxs, float(duration)
