"""Timeline: the tug of war stays on one line when speakers have tracks.

With speaker tracks shown, two subtitles a millisecond apart in time but on
two speakers' lines were taken for neighbours: hovering where one ended
showed the tug of war, and dragging it moved the other speaker's subtitle.
The drag's snapping also stopped at the subtitle next in the list, whatever
its line. Now the neighbours are the ones on the dragged subtitle's own
line; with every speaker on one line, nothing changes.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself, and sets scratch XDG folders before importing subtitld.
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
from subtitld.modules.segment import Subtitle
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


ZOOM = 50       # px a second: 20 s fit the 1000 px view

session.CONFIG = {'timeline': {'snap': True, 'snap_limits': True, 'show_speaker_tracks': True},
                  'default_values': {'minimum_subtitle_width': 0.5},
                  'timeline_zoom': ZOOM, 'repeat_activated': False, 'playback_speed': 1.0}
session.VIDEO = {'duration': 60.0, 'shot_changes': []}
session.SPEAKERS = {'A': {'color': '#ff2e93'}, 'B': {'color': '#2e93ff'}}


def load(segments):
    session.SUBTITLE = Subtitle({'segments': [dict(item) for item in segments], 'selected': None, 'position': 0.0})
    history.history_clear()
    session.UNSAVED = False


load([])
host = Host()
timeline.load(host)
host.layout().addWidget(host.timeline_scroll)
host.show()
QTest.qWait(100)
widget = host.timeline_widget


def x_at(seconds):
    return int(round(seconds * widget.width_proportion))


def y_of(speaker):
    """The middle of the speaker's line, or of the one line."""
    if not widget.show_speaker_tracks:
        return int(widget.subtitle_y + widget.subtitle_height / 2)
    track = list(session.SPEAKERS).index(speaker)
    height = widget.subtitle_height / len(session.SPEAKERS)
    return int(widget.subtitle_y + height * track + height / 2)


def hover(x, y):
    QTest.mouseMove(widget, QPoint(x, y))
    QTest.qWait(10)


def drag(start_x, end_x, y, steps=6):
    """Hover, press, move in `steps` moves and release, on one line."""
    hover(start_x, y)
    QTest.mousePress(widget, Qt.LeftButton, Qt.NoModifier, QPoint(start_x, y))
    for i in range(1, steps + 1):
        QTest.mouseMove(widget, QPoint(start_x + round((end_x - start_x) * i / steps), y))
    QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, QPoint(end_x, y))
    hover(end_x, widget.height() - 2)


def timing(text):
    item = next(item for item in session.SUBTITLE.segments if item['text'] == text)
    return (near(item['start']), near(item['end']))


widget.setMouseTracking(True)

# A's subtitle ends a millisecond before B's starts: next to each other in
# the list, on two lines.
ACROSS = [
    {'start': 10.0, 'end': 13.0, 'text': 'A one', 'speaker': 'A'},
    {'start': 13.001, 'end': 16.0, 'text': 'B one', 'speaker': 'B'},
    {'start': 18.0, 'end': 20.0, 'text': 'A two', 'speaker': 'A'},
]

print('speaker tracks: a subtitle on another line is not a neighbour')
widget.show_speaker_tracks = True
load(ACROSS)
hover(x_at(13.0) - 2, y_of('A'))
check('no tug of war where A ends', widget.show_tug_of_war, False)
drag(x_at(13.0) - 2, x_at(12.0) - 2, y_of('A'))
check("A's end moves", timing('A one'), (10.0, 12.0))
check("B's start stays", timing('B one'), (13.001, 16.0))

load(ACROSS)
session.CONFIG['timeline']['snap_move_nereast'] = True
drag(x_at(13.0) - 2, x_at(12.0) - 2, y_of('A'))
session.CONFIG['timeline']['snap_move_nereast'] = False
check('with "move nearest" on, B still stays', timing('B one'), (13.001, 16.0))

# A's two subtitles touch; B's overlaps them on its own line, and comes
# between them in the list.
ALONG = [
    {'start': 10.0, 'end': 13.0, 'text': 'A one', 'speaker': 'A'},
    {'start': 11.0, 'end': 16.0, 'text': 'B one', 'speaker': 'B'},
    {'start': 13.001, 'end': 15.0, 'text': 'A two', 'speaker': 'A'},
]

print('speaker tracks: the tug of war between two subtitles on one line')
load(ALONG)
hover(x_at(13.0) - 2, y_of('A'))
check('shows where they meet', near(widget.show_tug_of_war or 0, 2), 13.0)
drag(x_at(13.0) - 2, x_at(12.0) - 2, y_of('A'))
check('the first one ends earlier', timing('A one'), (10.0, 12.0))
check('the next on its line starts with it', timing('A two'), (12.001, 15.0))
check("the other speaker's is untouched", timing('B one'), (11.0, 16.0))
check('one undo step', len(history.ALL_HISTORY), 1)

print('speaker tracks: hovering a line picks the subtitle on it')
load(ALONG)
hover(x_at(12.0), y_of('B'))
check("B's subtitle, under A's in time", (widget.subtitle_under_the_cursor or {}).get('text'), 'B one')

print('one line: as before, neighbours in time are neighbours')
widget.show_speaker_tracks = False
load(ACROSS)
hover(x_at(13.0) - 2, y_of('A'))
check('the tug of war shows', near(widget.show_tug_of_war or 0, 2), 13.0)
drag(x_at(13.0) - 2, x_at(12.0) - 2, y_of('A'))
check("A's end moves", timing('A one'), (10.0, 12.0))
check("B's start follows", timing('B one'), (12.001, 16.0))

print()
print('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
