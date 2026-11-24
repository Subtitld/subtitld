"""Vertical timeline: the tug of war stays in one column when speakers have
tracks, as on the timeline.

With speaker tracks shown, a subtitle and the next one in the list were
taken for neighbours whatever their speakers: where A's subtitle ended a
millisecond before B's started, the tug of war showed in A's column and
dragging it moved B's; snapping stopped at B's subtitle; and a nudge of B's
start pushed A's end. Two of A's subtitles touching with B's in between in
the list showed no tug of war at all. Now the neighbours are the ones in the
subtitle's own column; with every speaker on one line, nothing changes.

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
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QStackedWidget, QPushButton
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import session, history
from subtitld.modules.segment import Subtitle
from subtitld.interface import left_panel_vtimeline as lpv

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

    def update(self):
        pass


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(400, 900)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.left_panel_navigation = QWidget()
        self.left_panel_navigation.setLayout(QVBoxLayout())
        self.left_panel_navigation.layout().addStretch()
        self.left_panel_stackedwidgets = QStackedWidget()
        self.layout().addWidget(self.left_panel_stackedwidgets)
        self.timeline_widget = types.SimpleNamespace(update=lambda: None, waveform_manager=None,
                                                     show_speaker_tracks=True, show_speaker_color=False,
                                                     dub_peaks={}, dub_onsets={}, background_onsets=None,
                                                     selected_onset=None, _request_dub_peaks=lambda path: None,
                                                     _request_dub_onsets=lambda path: None)
        self.preview_panel_player = Player()
        self.step_button = QPushButton()
        self.step_button.setCheckable(True)


session.CONFIG = {'timeline': {'snap': True, 'snap_limits': True}, 'default_values': {'minimum_subtitle_width': 0.5},
                  'vertical_timeline': {'zoom': 40.0}}
session.VIDEO = {'duration': 60.0, 'framerate': 25}
session.SPEAKERS = {'A': {'color': '#ff2e93'}, 'B': {'color': '#2ec4b6'}}

# A's subtitle ends a millisecond before B's starts: next to each other in
# the list, in two columns.
ACROSS = [
    {'start': 1.0, 'end': 3.0, 'text': 'A one', 'speaker': 'A'},
    {'start': 3.001, 'end': 5.0, 'text': 'B one', 'speaker': 'B'},
    {'start': 6.0, 'end': 8.0, 'text': 'A two', 'speaker': 'A'},
]
# A's two subtitles touch; B's overlaps them in its own column, and comes
# between them in the list.
ALONG = [
    {'start': 10.0, 'end': 13.0, 'text': 'A three', 'speaker': 'A'},
    {'start': 11.0, 'end': 16.0, 'text': 'B two', 'speaker': 'B'},
    {'start': 13.001, 'end': 15.0, 'text': 'A four', 'speaker': 'A'},
]


def load(segments):
    session.SUBTITLE = Subtitle({'segments': [dict(item) for item in segments], 'selected': None, 'position': 0.0})
    history.history_clear()
    session.UNSAVED = False


load(ACROSS)
host = Host()
lpv.load(host)
lpv.translate(host)
host.show()
QTest.qWait(100)
canvas = host.vtimeline_panel.canvas


def find(text):
    return next(item for item in session.SUBTITLE.segments if item['text'] == text)


def timing(text):
    item = find(text)
    return (near(item['start']), near(item['end']))


def point(seconds, speaker=None):
    """A point at `seconds`, in the speaker's column or the one lane."""
    columns = canvas.speaker_columns()
    left, right = columns[speaker] if columns else canvas.lanes()[2]
    return QPoint(int((left + right) / 2), int(round(canvas.y_at(seconds))))


def drag(start, end):
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(canvas, QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2))
    QTest.mouseMove(canvas, end)
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, end)


print('speaker tracks: a subtitle in another column is not a neighbour')
load(ACROSS)
check('no tug of war where A ends', canvas.junction_at(point(3.0, 'A')), None)
session.CONFIG['timeline']['snap_move_nereast'] = True
drag(point(3.0, 'A') - QPoint(0, 2), point(2.5, 'A') - QPoint(0, 2))
check("A's end moves", timing('A one'), (1.0, 2.5))
check('with "move nearest" on, B stays', timing('B one'), (3.001, 5.0))
load(ACROSS)
session.SUBTITLE.selected = find('B one')
QTest.keyClick(canvas, Qt.Key_Up, Qt.ShiftModifier)
check("Shift+Up: B's start steps up", timing('B one'), (near(3.001 - .04), 5.0))
check("and A's end stays", timing('A one'), (1.0, 3.0))
session.CONFIG['timeline']['snap_move_nereast'] = False

print('speaker tracks: the tug of war between two subtitles in one column')
load(ALONG)
pair = canvas.junction_at(point(13.0, 'A'))
check('shows where they meet', [item['text'] for item in pair or ()], ['A three', 'A four'])
drag(point(13.0, 'A') - QPoint(0, 1), point(12.5, 'A') - QPoint(0, 1))
check('the first one ends earlier', timing('A three'), (10.0, 12.5))
check('the next in its column starts with it', timing('A four'), (12.501, 15.0))
check("the other speaker's is untouched", timing('B two'), (11.0, 16.0))
load(ALONG)
drag(point(13.0, 'A') - QPoint(0, 2), point(12.4, 'A') - QPoint(0, 2))
check("snapping doesn't stop at B's start", timing('A three'), (10.0, 12.4))

print('one line: as before, neighbours in time are neighbours')
host.timeline_widget.show_speaker_tracks = False
load(ACROSS)
pair = canvas.junction_at(point(3.0))
check('the tug of war shows', [item['text'] for item in pair or ()], ['A one', 'B one'])
drag(point(3.0) - QPoint(0, 1), point(2.5) - QPoint(0, 1))
check("A's end moves", timing('A one'), (1.0, 2.5))
check("B's start follows", timing('B one'), (2.501, 5.0))

print()
print('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
