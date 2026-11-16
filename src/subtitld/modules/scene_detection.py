"""Scene changes (cuts) in the video, for the timeline's "scenes" grid and
snapping.

FFmpeg's `scdet` filter scores every frame's change from the one before
(0-100), on frames scaled down to 160 px wide: the comparison is a
whole-frame difference, so small frames score the same and the filter's
cost goes away; decoding is what's left. About 30x real time on 1080p
H.264, so it runs in the background and the grid fills in when it's done.

scdet's own cut-off (10) misses most cuts in screen recordings: one grey
editor window cut to another scores 3 to 8. Lowered to that for everything,
a film's motion, flashes and fading titles score the same and would count.
What tells a cut apart is that it is a single spike: the frames around it
change little, while in motion they all do. So a frame is a cut when it
scores 10 or more, or at least 2.5 and three times any other frame's
score within half a second of it.

The times are cached next to the waveform's, keyed to the video's path, size
and mtime, so a video is read once.
"""

import bisect
import json
import os
import subprocess

from PySide6.QtCore import QThread, Signal

from subtitld.modules import session, utils

# A cut whatever the frames around it do (scdet's own default threshold).
SURE_SCORE = 10
# Below it, a cut when at least FLOOR and RATIO times every other score
# within WINDOW seconds.
FLOOR = 2.5
RATIO = 3
WINDOW = 0.5
# Part of the cache file's name: results found with other rules are found again.
RULES_VERSION = 2


def cache_path(filepath):
    """The JSON file the scene times of `filepath` are kept in, or None."""
    cache_key = utils.get_cache_key(filepath)
    if not cache_key:
        return None
    return os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform', f'{cache_key}_scenes{RULES_VERSION}.json')


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


def cuts(scores):
    """The times in `scores`, (time, score) pairs in time order, that are
    cuts by the rule above."""
    times = [time for time, _score in scores]
    found = []
    first = 0
    for index, (time, score) in enumerate(scores):
        if score < FLOOR:
            continue
        if score < SURE_SCORE:
            while times[first] < time - WINDOW:
                first += 1
            last = bisect.bisect_right(times, time + WINDOW, lo=index)
            around = max((scores[other][1] for other in range(first, last) if other != index), default=0)
            if score < RATIO * around:
                continue
        found.append(round(time, 3))
    return found


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
        """Stop now: kill FFmpeg rather than wait for the loop below to
        read its next line, which a slow decode can hold up."""
        self.requestInterruption()
        process = self._process
        if process is not None and process.poll() is None:
            process.kill()

    def run(self):
        command = [str(session.FFMPEG_EXECUTABLE), '-hide_banner', '-loglevel', 'error', '-nostdin',
                   '-i', self.filepath, '-an', '-sn', '-dn',
                   '-vf', 'scale=160:-2,scdet=threshold=0:sc_pass=0,metadata=print:key=lavfi.scd.score:file=-',
                   '-f', 'null', '-']
        try:
            process = self._process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                       text=True, startupinfo=session.STARTUPINFO)
        except OSError:
            return
        if self.isInterruptionRequested():  # stopped before there was a process to kill
            process.kill()
        # Each scored frame prints `frame:N  pts:P  pts_time:T`, then its
        # `lavfi.scd.score=S`.
        scores = []
        time = None
        for line in process.stdout:
            try:
                if line.startswith('frame:'):
                    time = float(line.rsplit('pts_time:', 1)[1])
                elif line.startswith('lavfi.scd.score=') and time is not None:
                    scores.append((time, float(line.split('=', 1)[1])))
            except (IndexError, ValueError):
                pass
        if process.wait() != 0 or self.isInterruptionRequested():
            return
        scores.sort()
        scenes = cuts(scores)
        _save(self.filepath, scenes)
        self.response.emit([self.filepath, scenes])
