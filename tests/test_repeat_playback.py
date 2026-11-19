"""Repeat playback: each stretch plays as many times as asked, then the next.

The repeat used to be checked in `timeline.update(self)`, which stopped
running while the video plays when the playhead got its own 30 Hz strip
repaint: the playhead ran past the stretch and never came back. The check
is `timeline.apply_repeat` now, run by that repaint. A "10 s x 3" stretch
plays three times (it played four), and a seek elsewhere starts the repeat
over from where it lands instead of being pulled back.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import session
from subtitld.modules.segment import Subtitle
from subtitld.interface import timeline

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


def _seek(pos, keep_repeat):
    """What the preview panel's seek does to the session."""
    session.SUBTITLE['position'] = float(pos)
    if not keep_repeat:
        session.REPEAT_DURATION_BUFFER = []


class Player:
    def set_position(self, pos, keep_repeat=False):
        _seek(pos, keep_repeat)


class Host:
    def __init__(self):
        self.preview_panel_player = Player()


def play(host, seconds, tick=1 / 30):
    """Play for `seconds` of wall clock, the repeat checked every tick as
    the playhead repaint does; the playhead's path, in tenths."""
    path = []
    for _ in range(round(seconds / tick)):
        session.SUBTITLE['position'] += tick
        timeline.apply_repeat(host)
        path.append(round(session.SUBTITLE['position'], 1))
    return path


def wraps(path):
    """Where the playhead went back to."""
    return [b for a, b in zip(path, path[1:]) if b < a]


session.CONFIG = {'repeat_activated': True, 'playback_repeat_duration': 2.0, 'playback_repeat_times': 3}
session.SUBTITLE = Subtitle({'position': 10.0})
session.REPEAT_DURATION_BUFFER = []
host = Host()

print('a 2 s stretch, three times, then the next')
path = play(host, 12.5)
check('goes back to each stretch\'s start twice', wraps(path), [10.0, 10.0, 12.0, 12.0])
check('then on into the third stretch', path[-1] > 14.0, True)
check('three plays left of the third stretch', len(session.REPEAT_DURATION_BUFFER), 3)
check('which starts where the second ended', [round(v, 1) for v in session.REPEAT_DURATION_BUFFER[0]], [14.0, 16.0])

print('a seek elsewhere starts the repeat from there')
session.SUBTITLE['position'] = 10.0
session.REPEAT_DURATION_BUFFER = []
play(host, 1.0)
_seek(40.0, keep_repeat=False)
path = play(host, 2.5)
check('not pulled back to 10 s', min(path) >= 40.0, True)
check('repeats the stretch from 40 s', wraps(path), [40.0])

print('once, then on')
session.CONFIG['playback_repeat_times'] = 1
session.SUBTITLE['position'] = 0.0
session.REPEAT_DURATION_BUFFER = []
check('never goes back', wraps(play(host, 7.0)), [])

print('off: nothing')
session.CONFIG['repeat_activated'] = False
session.SUBTITLE['position'] = 0.0
session.REPEAT_DURATION_BUFFER = []
check('no stretch', (wraps(play(host, 7.0)), session.REPEAT_DURATION_BUFFER), ([], []))

print('FAILED: ' + ', '.join(fails) if fails else 'all passed')
sys.exit(1 if fails else 0)
