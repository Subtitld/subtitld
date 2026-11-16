"""Scene changes (cuts) in the video, for the timeline's "scenes" grid and
snapping.

FFmpeg's `scdet` filter does the work, on frames scaled down to 160 px wide:
the comparison is a whole-frame difference, so small frames find the same
cuts and the filter's cost goes away; decoding is what's left. `sc_pass=1`
hands only the cut frames on to `metadata`, which prints their times. About
30x real time on 1080p H.264, so it runs in the background and the grid fills
in when it's done.

The times are cached next to the waveform's, keyed to the video's path, size
and mtime, so a video is read once.
"""

import json
import os
import subprocess

from PySide6.QtCore import QThread, Signal

from subtitld.modules import session, utils

# scdet's own default: the score (0-100) a frame's difference from the last
# one must reach to count as a cut.
THRESHOLD = 10


def cache_path(filepath):
    """The JSON file the scene times of `filepath` are kept in, or None."""
    cache_key = utils.get_cache_key(filepath)
    if not cache_key:
        return None
    return os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform', f'{cache_key}_scenes.json')


def cached_scenes(filepath):
    """The scene times found earlier for `filepath`, or None."""
    path = cache_path(filepath)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path) as cache_file:
            return [float(time) for time in json.load(cache_file)]
    except (OSError, ValueError, TypeError):
        return None


def _save(filepath, scenes):
    path = cache_path(filepath)
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as cache_file:
            json.dump(scenes, cache_file)
    except OSError:
        pass


class SceneDetectionThread(QThread):
    """Reads `filepath` through scdet and emits [filepath, sorted times in
    seconds]. Emits nothing when stopped or when FFmpeg fails (an FFmpeg
    without scdet, older than 4.4, or a file it can't decode): the grid
    simply stays empty, as before."""
    response = Signal(list)

    def __init__(self, filepath, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self._process = None

    def stop(self):
        """Stop now: FFmpeg prints nothing between cuts, so the loop
        below would only see the request at the next one."""
        self.requestInterruption()
        process = self._process
        if process is not None and process.poll() is None:
            process.kill()

    def run(self):
        command = [str(session.FFMPEG_EXECUTABLE), '-hide_banner', '-loglevel', 'error', '-nostdin',
                   '-i', self.filepath, '-an', '-sn', '-dn',
                   '-vf', f'scale=160:-2,scdet=threshold={THRESHOLD}:sc_pass=1,metadata=print:key=lavfi.scd.time:file=-',
                   '-f', 'null', '-']
        try:
            process = self._process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                       text=True, startupinfo=session.STARTUPINFO)
        except OSError:
            return
        if self.isInterruptionRequested():  # stopped before there was a process to kill
            process.kill()
        scenes = []
        for line in process.stdout:
            if line.startswith('lavfi.scd.time='):
                try:
                    scenes.append(round(float(line.split('=', 1)[1]), 3))
                except ValueError:
                    pass
        if process.wait() != 0 or self.isInterruptionRequested():
            return
        scenes.sort()
        _save(self.filepath, scenes)
        self.response.emit([self.filepath, scenes])
