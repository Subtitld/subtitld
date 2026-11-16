"""Timeline: the scenes grid. Subtitles snap to the video's cuts, which
scene_detection finds with FFmpeg's scdet and caches per video.

Nothing filled `session.VIDEO['scenes']` for years, so the grid did
nothing; and the drag appended the video's duration to that list on every
mouse move, and took the end handle's neighbouring cuts from the start side.

Standalone script, like the other suites here; puts this checkout's src/
on the path itself (the venv holds a non-editable install).
"""
import os, sys, tempfile, types
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import session, history, scene_detection
from subtitld.interface import timeline

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


def near(value, places=3):
    return round(value, places)


class Player:
    def seek(self, position):
        pass

    def is_paused(self):
        return True


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(1000, 200)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.preview_panel_player = Player()
        panel = types.SimpleNamespace(update=lambda window: None)
        self.left_panel_stackedwidgets = types.SimpleNamespace(currentWidget=lambda: panel)
        self.subtitles_panel_qlistwidget = types.SimpleNamespace(update_content=lambda: None)


ZOOM = 100      # px a second
LANE_Y = 80     # inside the subtitles lane (subtitle_y 55, 50 tall)


def reset_document():
    session.SUBTITLE = {'segments': [
        {'start': 1.0, 'end': 3.0, 'text': 'One', 'speaker': 'A'},
        {'start': 3.001, 'end': 4.5, 'text': 'Two', 'speaker': 'A'},
        {'start': 6.0, 'end': 8.0, 'text': 'Three', 'speaker': 'A'},
    ], 'selected': None, 'position': 0.0}
    history.history_clear()
    session.UNSAVED = False


session.CONFIG = {'timeline': {'snap': False}, 'default_values': {'minimum_subtitle_width': 0.5},
                  'timeline_zoom': ZOOM, 'repeat_activated': False, 'playback_speed': 1.0}
session.VIDEO = {'duration': 60.0, 'scenes': [5.5, 12.0]}
session.SPEAKERS = {'A': {'color': '#ff2e93'}}
reset_document()

host = Host()
timeline.load(host)
host.layout().addWidget(host.timeline_scroll)
host.show()
QTest.qWait(100)
widget = host.timeline_widget
check('the timeline is as wide as the video at the zoom', widget.width(), int(60 * ZOOM))


def x_at(seconds):
    return int(round(seconds * widget.width_proportion))


def drag(start_x, end_x, steps=6):
    """Press at start_x, move there in `steps` moves, release at the end."""
    QTest.mousePress(widget, Qt.LeftButton, Qt.NoModifier, QPoint(start_x, LANE_Y))
    for i in range(1, steps + 1):
        QTest.mouseMove(widget, QPoint(start_x + round((end_x - start_x) * i / steps), LANE_Y))
    QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, QPoint(end_x, LANE_Y))


def segment(index):
    return session.SUBTITLE['segments'][index]


def timing(index):
    return (near(segment(index)['start']), near(segment(index)['end']))



SNAP = {'snap': True, 'snap_grid': True, 'snap_limits': False, 'snap_moving': False,
        'snap_value': 0.1, 'show_grid': True, 'grid_type': 'scenes'}

print('dragging the start near a cut')
reset_document()
session.CONFIG['timeline'] = dict(SNAP)
drag(x_at(6.0) + 5, x_at(5.55) + 5)
check('snaps the start to it', timing(2), (5.5, 8.0))
check('the scenes list is left as it was', session.VIDEO['scenes'], [5.5, 12.0])

print('dragging the end near a cut')
reset_document()
segment(2)['end'] = 11.0
drag(x_at(11.0) - 5, x_at(11.95) - 5)
check('snaps the end to it', timing(2), (6.0, 12.0))
check('the scenes list is still as it was', session.VIDEO['scenes'], [5.5, 12.0])

print('moving a subtitle near a cut')
reset_document()
drag(x_at(7.0), x_at(7.0) + x_at(5.55) - x_at(6.0))
check('its start lands on the cut', timing(2), (5.5, 7.5))

print('away from cuts')
reset_document()
drag(x_at(7.0), x_at(9.0))
check('it moves freely', timing(2), (8.0, 10.0))

print('the video start and end are edges too')
reset_document()
session.VIDEO['scenes'] = []
drag(x_at(1.0) + 5, x_at(0.05) + 5)
check('the start snaps to 0', timing(0), (0.0, 3.0))
session.VIDEO['scenes'] = [5.5, 12.0]
session.CONFIG['timeline'] = {'snap': False}

print('telling cuts from motion')
quiet = [(n / 10, 0.2) for n in range(100)]
check('a lone spike in a still shot is a cut',
      scene_detection.cuts(sorted(quiet[:50] + [(5.05, 3.0)] + quiet[50:])), [5.05])
busy = [(n / 10, 2.0 + (n % 3)) for n in range(100)]
check('the same spike among moving frames is not',
      scene_detection.cuts(sorted(busy[:50] + [(5.05, 5.0)] + busy[50:])), [])
check('a score of 10 is a cut whatever is around it',
      scene_detection.cuts(sorted(busy[:50] + [(5.05, 10.0)] + busy[50:])), [5.05])
check('nothing under the floor is', scene_detection.cuts([(0.0, 0.0), (1.0, 2.4), (2.0, 0.0)]), [])

print('loading the scenes of a video')
video = os.path.join(_xdg, 'video.mkv')
open(video, 'wb').write(b'not really a video')
scene_detection._save(video, [1.25, 3.5])
session.VIDEO = {'duration': 60.0, 'scenes': [], 'filepath': video, 'width': 640}
widget.load_scenes()
check('a cached result is used at once', session.VIDEO['scenes'], [1.25, 3.5])
check('without a thread', widget.scene_thread, None)
widget._on_scenes_ready(['/elsewhere.mkv', [9.0]])
check("another video's result is ignored", session.VIDEO['scenes'], [1.25, 3.5])
session.VIDEO = {'duration': 60.0, 'scenes': [], 'filepath': video}
widget.load_scenes()
check('an audio-only file has none', (session.VIDEO['scenes'], widget.scene_thread), ([], None))

print('a file FFmpeg cannot read')
os.remove(scene_detection.cache_path(video))
session.VIDEO = {'duration': 60.0, 'scenes': [], 'filepath': video, 'width': 640}
widget.load_scenes()
widget.scene_thread.wait(10000)
QTest.qWait(50)
check('leaves the grid empty', session.VIDEO['scenes'], [])
check('and caches nothing', scene_detection.cached_scenes(video), None)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
