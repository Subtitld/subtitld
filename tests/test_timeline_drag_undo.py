"""Timeline: dragging a subtitle, or its start or end, is one undo step.

Every mouse move used to push its own snapshot, so one long drag filled the
whole undo stack (MAX_HISTORY): what came before it could no longer be
undone, and undoing the drag took dozens of Ctrl+Z.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
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
from subtitld.modules import session, history
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
session.VIDEO = {'duration': 60.0, 'scenes': []}
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


print('a click without a move')
drag(x_at(7.0), x_at(7.0), steps=0)
check('selects the subtitle', session.SUBTITLE['selected'] is segment(2), True)
check('and leaves no undo step', len(history.ALL_HISTORY), 0)

print('dragging a subtitle')
history.history_append()        # an edit made before the drag
earlier = history.top_snapshot()
drag(x_at(7.0), x_at(9.0))
check('moves it', timing(2), (8.0, 10.0))
check('the history grows by exactly 1', len(history.ALL_HISTORY), 2)
check('the edit before it is still there', history.ALL_HISTORY[0] is earlier, True)
check('the document is unsaved', session.UNSAVED, True)
history.history_undo()
check('one undo puts it back', timing(2), (6.0, 8.0))
check('leaving the edit before it', history.top_snapshot() is earlier, True)

print('dragging the start and the end')
reset_document()
drag(x_at(6.0) + 5, x_at(5.0) + 5)
check('the start moves', timing(2), (5.0, 8.0))
check('one undo step', len(history.ALL_HISTORY), 1)
drag(x_at(8.0) - 5, x_at(9.5) - 5)
check('the end moves', timing(2), (5.0, 9.5))
check('one undo step each', len(history.ALL_HISTORY), 2)
history.history_undo()
check('undoing the end', timing(2), (5.0, 8.0))
history.history_undo()
check('then the start', timing(2), (6.0, 8.0))
check('back to an empty stack', len(history.ALL_HISTORY), 0)

print('a drag longer than the undo stack')
reset_document()
history.history_append()
earlier = history.top_snapshot()
moves = history.MAX_HISTORY + 20
drag(x_at(7.0), x_at(7.0) + moves, steps=moves)
check('moves it a pixel at a time', timing(2), (near(6.0 + moves / ZOOM), near(8.0 + moves / ZOOM)))
check('still one undo step', len(history.ALL_HISTORY), 2)
history.history_undo()
check('undone in one go', timing(2), (6.0, 8.0))
check('with the edit before it still there', history.top_snapshot() is earlier, True)

print('a drag held by a snap')
reset_document()
session.CONFIG['timeline'] = {'snap': True, 'snap_limits': True, 'snap_moving': True, 'snap_value': 0.1}
QTest.mousePress(widget, Qt.LeftButton, Qt.NoModifier, QPoint(x_at(3.8), LANE_Y))
for x in (x_at(3.8) - 2, x_at(3.8) - 4, x_at(3.8) - 6):
    QTest.mouseMove(widget, QPoint(x, LANE_Y))
check('Two stays against One', timing(1), (3.001, 4.5))
check('so no undo step yet', len(history.ALL_HISTORY), 0)
for x in (x_at(3.8) + 20, x_at(3.8) + 40):
    QTest.mouseMove(widget, QPoint(x, LANE_Y))
QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, QPoint(x_at(3.8) + 40, LANE_Y))
check('dragged clear of it, it moves', timing(1), (3.401, 4.9))
check('as one undo step', len(history.ALL_HISTORY), 1)
history.history_undo()
check('undone back against One', timing(1), (3.001, 4.5))
session.CONFIG['timeline'] = {'snap': False}

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
