"""Vertical timeline tab: the timeline on its side, in the left panel.

Clicks select and seek, drags move a subtitle or its start or end (snapping
as the timeline does, one undo step per drag), the arrow keys step the
selected subtitle, or with Shift its start and with Ctrl its end (one undo
step a press), Ctrl+wheel and the buttons zoom,
and the view follows the playhead and the selection.

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
SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QStackedWidget, QPushButton
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QAction, QWheelEvent, QColor
import numpy as np
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import session, history
from subtitld.interface import left_panel_vtimeline as lpv

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


def near(value, places=3):
    return round(value, places)


print('the tab comes right after the plain-text one')
source = (SRC / 'subtitld' / 'interface' / 'left_panel.py').read_text()
order = [source.index(f'left_panel_{name}.load(self)') for name in ('plaintext', 'vtimeline', 'metadata')]
check('plain text, vertical timeline, metadata', order == sorted(order), True)


class Player:
    def __init__(self):
        self.seeks = []
        self.paused = True

    def seek(self, position):
        self.seeks.append(position)

    def is_paused(self):
        return self.paused

    def update(self):
        pass


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(400, 600)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.left_panel_navigation = QWidget()
        self.left_panel_navigation.setLayout(QVBoxLayout())
        self.left_panel_navigation.layout().addStretch()
        self.left_panel_stackedwidgets = QStackedWidget()
        self.layout().addWidget(self.left_panel_stackedwidgets)
        self.timeline_widget = types.SimpleNamespace(update=lambda: None, waveform_manager=None,
                                                     show_speaker_tracks=False, show_speaker_color=False,
                                                     dub_peaks={}, dub_onsets={}, background_onsets=None,
                                                     selected_onset=None, _request_dub_peaks=lambda path: None,
                                                     _request_dub_onsets=lambda path: None)
        self.preview_panel_player = Player()
        self.step_button = QPushButton()
        self.step_button.setCheckable(True)


def reset_document():
    session.SUBTITLE = {'segments': [
        {'start': 1.0, 'end': 3.0, 'text': 'One', 'speaker': 'A'},
        {'start': 5.0, 'end': 7.0, 'text': 'Two', 'speaker': 'A'},
        {'start': 7.5, 'end': 9.0, 'text': 'Three', 'speaker': 'A'},
        {'start': 12.0, 'end': 14.0, 'text': 'Locked', 'speaker': 'A', 'locked': True},
        {'start': 16.0, 'end': 18.0, 'text': 'Hidden', 'speaker': 'H'},
        {'start': 40.0, 'end': 42.0, 'text': 'Far', 'speaker': 'A'},
    ], 'selected': None, 'position': 0.0}
    history.history_clear()
    session.UNSAVED = False


session.CONFIG = {'timeline': {'snap': False}, 'default_values': {'minimum_subtitle_width': 0.5},
                  'vertical_timeline': {'zoom': 40.0}}
session.VIDEO = {'duration': 60.0, 'framerate': 25}
session.SPEAKERS = {'A': {'color': '#ff2e93'}, 'H': {'color': '#2e7fb8', 'hidden': True}}
reset_document()

host = Host()
lpv.load(host)
lpv.translate(host)
host.show()
QTest.qWait(100)
panel = host.vtimeline_panel
canvas = panel.canvas
scroll = panel.scroll
segments = session.SUBTITLE['segments']
one, two, three, locked, hidden, far = segments
player = host.preview_panel_player


def point(seconds, x=None):
    """A point on the canvas at `seconds`, in the subtitles lane."""
    lane = canvas.lanes()[2]
    return QPoint(int(x if x is not None else (lane[0] + lane[1]) / 2), int(round(canvas.y_at(seconds))))


def drag(start, end):
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(canvas, QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2))
    QTest.mouseMove(canvas, end)
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, end)


print('the canvas')
check('as tall as the video at 40 px a second', canvas.height(), int(lpv.TOP + 60 * 40 + lpv.BOTTOM))
check('as wide as the view', canvas.width(), scroll.viewport().width())

print('a click on a subtitle selects it')
QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, point(6.0))
check('selected', session.SUBTITLE['selected'] is two, True)
check('without seeking', player.seeks, [])
check('nor moving it', (two['start'], two['end'], len(history.ALL_HISTORY)), (5.0, 7.0, 0))

print('a click off the subtitles seeks, and drops the selection')
QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, point(4.0))
check('the player seeks there', [near(p, 2) for p in player.seeks], [4.0])
check('the position is there', near(session.SUBTITLE['position'], 2), 4.0)
check('nothing selected', session.SUBTITLE['selected'], None)
player.seeks.clear()
QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, point(10.0, x=10))
check('the ruler seeks too', [near(p, 2) for p in player.seeks], [10.0])

print('dragging a subtitle moves it, as one undo step')
drag(point(6.0), point(6.75))
check('moved by the drag', (near(two['start'], 2), near(two['end'], 2)), (5.75, 7.75))
check('one undo step', len(history.ALL_HISTORY), 1)
check('the document is unsaved', session.UNSAVED, True)
history.history_undo()
two = session.SUBTITLE['segments'][1]
check('undone in one go', (two['start'], two['end']), (5.0, 7.0))

print('dragging the edges moves the start or the end')
drag(point(5.0 + 0.05), point(4.5 + 0.05))
check('start up', (near(two['start'], 2), two['end']), (4.5, 7.0))
drag(point(7.0 - 0.05), point(6.0 - 0.05))
check('end up', (near(two['start'], 2), near(two['end'], 2)), (4.5, 6.0))
drag(point(6.0 - 0.05), point(4.0))
check('no shorter than the minimum length', near(two['end'], 2), near(two['start'] + 0.5, 2))
check('one undo step a drag', len(history.ALL_HISTORY), 3)

print('snapping, as the timeline snaps')
reset_document()
one, two, three, locked, hidden, far = session.SUBTITLE['segments']
session.CONFIG['timeline'] = {'snap': True, 'snap_limits': True, 'snap_moving': True, 'snap_value': 0.1}
drag(point(5.0 + 0.05), point(2.0))
check('a start stops at the previous end', near(two['start']), 3.001)
drag(point(two['end'] - 0.05), point(8.5))
check('an end stops at the next start', near(two['end']), 7.499)
before = len(history.ALL_HISTORY)
drag(point(1.5), point(1.5 + 3.0))
check('a move stops against the next subtitle', (near(one['start']), near(one['end'])), (1.0, 3.0))
check('so nothing changed, and no undo step', len(history.ALL_HISTORY), before)
session.CONFIG['timeline'] = {'snap': True, 'snap_grid': True, 'grid_type': 'seconds', 'snap_value': 0.1, 'snap_limits': False, 'snap_moving': False}
drag(point(41.0), point(41.0 + 2.04))
check('to the grid', (near(far['start'], 2), near(far['end'], 2)), (42.0, 44.0))
session.CONFIG['timeline'] = {'snap': False}

print('locked and hidden subtitles')
drag(point(13.0), point(13.0 + 1.0))
check('a locked one is selected, but stays put', (session.SUBTITLE['selected'] is locked, locked['start']), (True, 12.0))
QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, point(17.0))
check('a hidden speaker\'s is not there to click: it seeks', session.SUBTITLE['selected'], None)

print('zoom')
scroll.verticalScrollBar().setValue(0)
anchor_time = 6.0
anchor = point(anchor_time)
wheel = QWheelEvent(QPointF(anchor), QPointF(canvas.mapToGlobal(anchor)), QPoint(0, 0), QPoint(0, 120),
                    Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False)
QApplication.sendEvent(canvas, wheel)
check('Ctrl+wheel zooms in', near(canvas.pps, 2), near(40 * 1.12, 2))
check('the canvas grows with it', canvas.height(), int(lpv.TOP + 60 * canvas.pps + lpv.BOTTOM))
viewport_y = anchor.y() - 0          # the view was at the top
check('the time under the pointer stays under it',
      abs(canvas.time_at(scroll.verticalScrollBar().value() + viewport_y) - anchor_time) < 0.05, True)
panel.zoom_in_button.click()
check('the button zooms in a step', near(canvas.pps, 2), near(40 * 1.12 * lpv.ZOOM_STEP, 2))
check('kept for next time', near(session.CONFIG['vertical_timeline']['zoom'], 2), near(canvas.pps, 2))
for _ in range(40):
    panel.zoom_in_button.click()
check('up to the limit, where the button is off', (canvas.pps, panel.zoom_in_button.isEnabled()), (lpv.ZOOM_RANGE[1], False))
scroll.set_zoom(40.0)
check('and back', (canvas.pps, panel.zoom_in_button.isEnabled()), (40.0, True))

bar = scroll.verticalScrollBar()
bar.setValue(0)
wheel = QWheelEvent(QPointF(anchor), QPointF(canvas.mapToGlobal(anchor)), QPoint(0, 0), QPoint(0, -120),
                    Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
QApplication.sendEvent(canvas, wheel)
check('the wheel alone scrolls', (bar.value() > 0, canvas.pps), (True, 40.0))

print('the ruler fits its labels')
from PySide6.QtGui import QFont, QFontMetrics
metrics = QFontMetrics(QFont('Ubuntu Mono', 8))
usual = canvas.lanes()[0][1]
check('mm:ss fits', usual >= max(lpv.RULER_WIDTH, metrics.horizontalAdvance('01:00') + 10), True)
session.VIDEO['duration'] = 2 * 3600.0
wide = canvas.lanes()[0][1]
check('hh:mm:ss widens it to fit', (wide > usual, wide >= metrics.horizontalAdvance('02:00:00') + 10), (True, True))
session.VIDEO['duration'] = 60.0
check('and back', canvas.lanes()[0][1], usual)

print('the view follows')
scroll.verticalScrollBar().setValue(0)
session.SUBTITLE['position'] = 50.0
scroll.tick()
bar = scroll.verticalScrollBar()
y = canvas.y_at(50.0)
check('a playhead moved elsewhere comes into view', bar.value() <= y <= bar.value() + scroll.viewport().height(), True)
bar.setValue(0)
session.SUBTITLE['selected'] = far
lpv.update(host)
check('so does a subtitle selected elsewhere',
      bar.value() <= canvas.y_at(far['start']) and canvas.y_at(far['end']) <= bar.value() + scroll.viewport().height(), True)
player.paused = False
session.CONFIG['timeline'] = {'scrolling': 'follow'}
session.SUBTITLE['position'] = 20.0
scroll.tick()
check('playing, "follow" keeps the playhead in the middle',
      abs(bar.value() + scroll.viewport().height() / 2 - canvas.y_at(20.0)) <= 1, True)
player.paused = True

print('speaker tracks: a column each, as the timeline\'s rows')
reset_document()
one, two, three, locked, hidden, far = session.SUBTITLE['segments']
session.SPEAKERS['B'] = {'color': '#2ec4b6'}
two['speaker'] = 'B'
host.timeline_widget.show_speaker_tracks = True
columns = canvas.speaker_columns()
check('one per speaker', list(columns), ['A', 'H', 'B'])
check('side by side', canvas.block_rect(one, columns).right() < canvas.block_rect(two, columns).left(), True)
b_column = columns['B']
QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, point(6.0, x=(b_column[0] + b_column[1]) / 2))
check('a click in B\'s column finds B\'s subtitle', session.SUBTITLE['selected'] is two, True)
QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, point(6.0, x=columns['A'][0] + 5))
check('and in A\'s, nothing there: a seek', session.SUBTITLE['selected'], None)
host.timeline_widget.show_speaker_tracks = False

print('drawn with everything on')
host.timeline_widget.show_speaker_color = True
session.CONFIG['timeline'] = {'show_grid': True, 'grid_type': 'seconds'}
session.CONFIG['quality_check'] = {'enabled': True}
session.CONFIG['translation'] = {'engine_options': {'show_translations': True, 'target_language': 'pt-br'}}
one['translations'] = {'pt-br': 'Um'}
session.REPEAT_DURATION_BUFFER = [[2.0, 4.0]]
session.SUBTITLE['selected'] = one
canvas.grab()
check('without an error', canvas._last_paint_error, None)
session.REPEAT_DURATION_BUFFER = []

def pixel(seconds, x):
    """The colour drawn at a time and an x, as (r, g, b)."""
    image = canvas.grab().toImage()
    ratio = image.devicePixelRatio()
    color = QColor(image.pixel(int(x * ratio), int(round(canvas.y_at(seconds)) * ratio)))
    return color.red(), color.green(), color.blue()


def whiteness(rgb):
    return min(rgb)


session.CONFIG['timeline'] = {'snap': False}
session.CONFIG['quality_check'] = {}
session.CONFIG['translation'] = {}
host.timeline_widget.show_speaker_color = False
reset_document()
one, two, three, locked, hidden, far = session.SUBTITLE['segments']
scroll.verticalScrollBar().setValue(0)
middle = (canvas.lanes()[2][0] + canvas.lanes()[2][1]) / 2

print('the edges, as on the timeline')
rect = canvas.block_rect(two)
QTest.mouseMove(canvas, QPoint(int(middle), int(rect.top() + 4)))
check('near the top: the start', canvas.hover, (id(two), 'start'))
check('with the timeline\'s arrow, turned up', canvas.cursor().shape(), Qt.BitmapCursor)
lit = pixel(two['start'], middle)
QTest.mouseMove(canvas, QPoint(int(middle), int(rect.center().y())))
check('in the middle: the body', canvas.hover, (id(two), 'body'))
plain = pixel(two['start'], middle)
check('the hovered edge is drawn white', whiteness(lit) > whiteness(plain) + 40, True)
QTest.mouseMove(canvas, QPoint(int(middle), int(rect.bottom() - 4)))
check('near the bottom: the end', canvas.hover, (id(two), 'end'))
check('grabbing 20 px of the edge, as the timeline', lpv.EDGE, 20)

print('where two subtitles meet, a drag moves both (the tug of war)')
meeting = canvas.y_at(7.25)
two['end'] = 7.499
three['start'] = 7.5
QTest.mouseMove(canvas, QPoint(int(middle), int(canvas.y_at(7.4995))))
check('found', canvas.tug, (two, three))
before = len(history.ALL_HISTORY)
drag(QPoint(int(middle), int(canvas.y_at(7.4995)) - 1), QPoint(int(middle), int(canvas.y_at(7.4995 + 0.5)) - 1))
check('the end and the next start move together',
      (near(two['end'], 2), near(three['start'], 2)), (8.0, 8.0))
check('still touching', lpv._glued(two, three), True)
check('one undo step', len(history.ALL_HISTORY), before + 1)
drag(QPoint(int(middle), int(canvas.y_at(two['end'] + .0005)) + 2), QPoint(int(middle), int(canvas.y_at(6.0)) + 2))
check('and back up, from below the meeting', (near(two['end'], 1), near(three['start'], 1)), (6.0, 6.0))
check('the subtitle above keeps its minimum length', near(two['end'] - two['start'], 1) >= 0.5, True)
QTest.mouseMove(canvas, QPoint(int(middle), int(canvas.y_at(3.5))))
check('away from it, gone', canvas.tug, None)

print('the timeline\'s scrolling setting')
bar = scroll.verticalScrollBar()
player.paused = False
session.CONFIG['timeline'] = {'scrolling': 'page'}
bar.setValue(0)
session.SUBTITLE['position'] = 30.0
scroll.tick()
check('"page": past the bottom, the next page starts at the playhead',
      bar.value(), min(bar.maximum(), int(canvas.y_at(30.0))))
session.CONFIG['timeline'] = {'scrolling': 'none'}
bar.setValue(0)
session.SUBTITLE['position'] = 45.0
scroll.tick()
check('"none": the view stays, playing', bar.value(), 0)
player.paused = True
session.SUBTITLE['position'] = 50.0
scroll.tick()
check('and paused', bar.value(), 0)
session.CONFIG['timeline'] = {}

print('the timeline\'s options, when they change, are drawn')
first = scroll._signature_now()
session.CONFIG['timeline']['show_grid'] = True
check('the grid', scroll._signature_now() != first, True)
second = scroll._signature_now()
host.timeline_widget.show_speaker_color = True
check('speaker colours', scroll._signature_now() != second, True)
host.timeline_widget.show_speaker_color = False
session.CONFIG['timeline'] = {}

print('onset markers, as on the timeline: shown while paused')
host.timeline_widget.background_onsets = np.array([3.5, 20.0], dtype=np.float32)
plain = pixel(3.5, 4)
session.CONFIG['timeline'] = {'show_onset_markers': True}
marked = pixel(3.5, 4)
check('a line at each onset', marked != plain, True)
player.paused = False
check('none while playing', pixel(3.5, 4), plain)
player.paused = True
session.CONFIG['timeline'] = {}
host.timeline_widget.background_onsets = None

print('dub clips')
session.SPEAKERS['A'] = {'color': '#ff0000'}
clip_x = canvas.block_rect(two).right() - 6
wav = os.path.join(_xdg, 'dub.wav')
open(wav, 'wb').close()
two['start'], two['end'] = 5.0, 7.0
two['dubbing'] = [{'path': wav, 'start': 4.5, 'segments': [
    {'type': 'audio', 'path': wav, 'start': 0.0, 'end': 2.0, 'offset': 0.0}]}]
session.CONFIG['dubbing'] = {'enabled': False}
host.timeline_widget.dub_peaks[wav] = (np.full(200, -0.5), np.full(200, 0.5), 2.0)
off = pixel(6.0, clip_x)
plain_fill = off
session.CONFIG['dubbing'] = {'enabled': True}
on = pixel(6.0, clip_x)
check('drawn when dubbing is on', on != off, True)
check('in the speaker\'s colour', on[0] > on[1] + 60 and on[0] > on[2] + 60, True)
check('reaching past the subtitle, where the clip does', pixel(4.75, clip_x)[0] > 150, True)
check('the waveform inside, in white', whiteness(pixel(5.8, canvas.block_rect(two).right() - lpv.CLIP_WIDTH / 2)) > 200, True)
check('the text gives it room', canvas.block_rect(two).width() - lpv.VerticalTimeline.clip_width(canvas.block_rect(two)),
      canvas.block_rect(two).width() - lpv.CLIP_WIDTH)
two['dubbing'][0]['locked'] = True
two['dubbing'][0]['segments'][0]['rate'] = -20
canvas.grab()
check('locked and stretched, drawn without an error', canvas._last_paint_error, None)
del host.timeline_widget.dub_peaks[wav]
os.remove(wav)
row = [pixel(6.0, clip_x - dx) for dx in range(12)]
check('a clip still on its way: hatched', any(color != plain_fill for color in row), True)
check('without an error', canvas._last_paint_error, None)
session.CONFIG['dubbing'] = {'enabled': False}
two.pop('dubbing')

print('the bottom line')
session.SUBTITLE['position'] = 61.5
panel.update_labels()
check('the position', panel.position_label.text(), '00:01:01.500')
session.SUBTITLE['selected'] = one
panel.update_labels()
check('the selected subtitle\'s length', panel.selected_label.text().split()[-2], f'{one["end"] - one["start"]:.3f}')

print('the arrow keys step the selected subtitle, one undo step a press')
FRAME = 1 / 25
reset_document()
one, two, three, locked, hidden, far = session.SUBTITLE['segments']
host.activateWindow()
QTest.qWait(50)
QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, point(6.0))
check('a click selects, and gives the canvas the keys', (session.SUBTITLE['selected'] is two, canvas.hasFocus()),
      (True, True))


def arrow(key, press, repeat=False, modifier=Qt.NoModifier):
    QTest.simulateEvent(canvas, press, int(key), modifier, '', repeat, -1)


def hold_arrow(key, repeats=0, modifier=Qt.NoModifier):
    """Press, repeat as X11 does (a release and a press, both flagged as
    repeats), then let go."""
    arrow(key, True, modifier=modifier)
    for _ in range(repeats):
        arrow(key, False, repeat=True, modifier=modifier)
        arrow(key, True, repeat=True, modifier=modifier)
    arrow(key, False, modifier=modifier)


hold_arrow(Qt.Key_Down)
hold_arrow(Qt.Key_Down)
check('Down: a frame later a tap', (near(two['start']), near(two['end'])), (near(5.0 + 2 * FRAME), near(7.0 + 2 * FRAME)))
check('one undo step a tap', len(history.ALL_HISTORY), 2)
history.history_undo()
history.history_undo()
two = session.SUBTITLE['segments'][1]
session.UNSAVED = False
arrow(Qt.Key_Down, True)
for _ in range(10):
    arrow(Qt.Key_Down, False, repeat=True)
    arrow(Qt.Key_Down, True, repeat=True)
check('held: a frame a repeat', near(two['start']), near(5.0 + 11 * FRAME))
check('the rest are not told yet', session.UNSAVED, False)
arrow(Qt.Key_Down, False)
check('but once it is let go', session.UNSAVED, True)
check('one undo step for the press', len(history.ALL_HISTORY), 1)
history.history_undo()
two = session.SUBTITLE['segments'][1]
check('undone in one go', (two['start'], two['end']), (5.0, 7.0))

QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, point(2.0))
one = session.SUBTITLE['selected']
hold_arrow(Qt.Key_Up, 40)
check('Up: earlier, but not before the start of the video', (near(one['start']), near(one['end'])), (0.0, 2.0))
check('still one undo step', len(history.ALL_HISTORY), 1)
hold_arrow(Qt.Key_Up)
check('at the start, Up does nothing, and adds no step', (one['start'], len(history.ALL_HISTORY)), (0.0, 1))
host.step_button.setChecked(True)
session.CONFIG['timeline'] = {'step_unit': 'Seconds', 'step_value': 0.5}
hold_arrow(Qt.Key_Down)
check('with the step button on, the step set beside it', near(one['start']), 0.5)
host.step_button.setChecked(False)
session.CONFIG['timeline'] = {'snap': False}

print('with Shift the arrows step the start, with Ctrl the end')
SHIFT, CTRL = Qt.ShiftModifier, Qt.ControlModifier
reset_document()
one, two, three, locked, hidden, far = session.SUBTITLE['segments']
session.SUBTITLE['selected'] = two
hold_arrow(Qt.Key_Down, modifier=SHIFT)
hold_arrow(Qt.Key_Up, 4, modifier=CTRL)
check('Shift+Down: the start a frame later; Ctrl+Up held: the end five earlier',
      (near(two['start']), near(two['end'])), (near(5.0 + FRAME), near(7.0 - 5 * FRAME)))
check('one undo step a press', len(history.ALL_HISTORY), 2)
history.history_undo()
check('undoing the end', (near(session.SUBTITLE['selected']['start']), session.SUBTITLE['selected']['end']),
      (near(5.0 + FRAME), 7.0))
history.history_undo()
two = session.SUBTITLE['selected']
check('then the start', (two['start'], two['end']), (5.0, 7.0))
hold_arrow(Qt.Key_Up, 100, modifier=CTRL)
check('the end stops at the minimum length', (two['start'], near(two['end'])), (5.0, 5.5))
hold_arrow(Qt.Key_Up, modifier=CTRL)
check('and goes no further, adding no step', (near(two['end']), len(history.ALL_HISTORY)), (5.5, 1))
history.history_undo()
two = session.SUBTITLE['selected']
hold_arrow(Qt.Key_Down, 100, modifier=SHIFT)
check('so does the start', (near(two['start']), two['end']), (6.5, 7.0))
session.SUBTITLE['selected'] = one
hold_arrow(Qt.Key_Up, 40, modifier=SHIFT)
check('the start stops at the start of the video', (near(one['start']), one['end']), (0.0, 3.0))
session.SUBTITLE['selected'] = far
host.step_button.setChecked(True)
session.CONFIG['timeline'] = {'snap': False, 'step_unit': 'Seconds', 'step_value': 1.0}
hold_arrow(Qt.Key_Down, 30, modifier=CTRL)
check('and the end at its end', (far['start'], near(far['end'])), (40.0, 60.0))
host.step_button.setChecked(False)

print('and push a touching neighbour, with "move nearest" on')
reset_document()
one, two, three, locked, hidden, far = session.SUBTITLE['segments']
three['start'] = 7.001
session.CONFIG['timeline'] = {'snap': False, 'snap_move_nereast': True}
session.SUBTITLE['selected'] = two
hold_arrow(Qt.Key_Down, modifier=CTRL)
check('Ctrl+Down: the next start follows the end', (near(two['end']), near(three['start'])),
      (near(7.0 + FRAME), near(7.001 + FRAME)))
check('in one undo step', len(history.ALL_HISTORY), 1)
history.history_undo()
one, two, three, locked, hidden, far = session.SUBTITLE['segments']
check('undone together', (two['end'], three['start']), (7.0, 7.001))
session.SUBTITLE['selected'] = three
hold_arrow(Qt.Key_Up, 100, modifier=SHIFT)
check('Shift+Up held: the previous end gives way, down to its minimum length',
      (near(two['end']), near(three['start'])), (5.5, 5.501))
check('in one undo step', len(history.ALL_HISTORY), 1)
session.CONFIG['timeline'] = {'snap': False}
reset_document()

bar = scroll.verticalScrollBar()
QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, point(13.0))
locked = session.SUBTITLE['selected']
before = len(history.ALL_HISTORY)
bar.setValue(0)
hold_arrow(Qt.Key_Down)
check('a locked subtitle holds still', ((locked['start'], locked['end']), len(history.ALL_HISTORY)), ((12.0, 14.0), before))
check('and the view scrolls instead', bar.value() > 0, True)
session.SUBTITLE['selected'] = None
bar.setValue(0)
hold_arrow(Qt.Key_Down)
check('with nothing selected, the view scrolls, as before', bar.value() > 0, True)

shortcut_hits = []
down_action = QAction(host)
down_action.setShortcut('Down')
down_action.triggered.connect(lambda: shortcut_hits.append(1))
host.addAction(down_action)
hold_arrow(Qt.Key_Down)
check('nothing selected: a shortcut on Down keeps it', len(shortcut_hits), 1)
QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, point(8.0))
hold_arrow(Qt.Key_Down)
check('a subtitle selected: the arrow steps it instead', (len(shortcut_hits), near(session.SUBTITLE['selected']['start'])),
      (1, near(7.5 + FRAME)))
host.removeAction(down_action)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
