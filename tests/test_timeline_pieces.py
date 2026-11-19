"""The pieces of a subtitle timed word by word on the timeline
(interface.timeline_pieces): dragging the edge two pieces share moves both,
with Alt only the one dragged towards; an edge of its own moves alone; edges
snap to the playhead; a click on a chip takes the playhead to it, a double
click cuts it finer; tapping K while it plays times one piece after another.
Each change is one undo step.

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
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])
from subtitld.modules import history, session
from subtitld.modules.segment import Subtitle, timed_pieces
from subtitld.interface import timeline, timeline_pieces

fails = []


def check(name, got, want):
    ok = got == want
    print(('  ok   ' if ok else '  FAIL ') + name + ('' if ok else f'  (got {got!r}, want {want!r})'))
    if not ok:
        fails.append(name)


class Player:
    def __init__(self):
        self.at = None

    def set_position(self, position):
        self.at = position

    def is_paused(self):
        return True


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(1000, 200)
        self.preview_panel_player = Player()
        self.timeline_widget = timeline.Timeline(self)
        self.timeline_widget.setGeometry(0, 0, 1000, 200)
        panel = types.SimpleNamespace(update=lambda window: None)
        self.left_panel_stackedwidgets = types.SimpleNamespace(currentWidget=lambda: panel)


session.CONFIG = {'timeline': {'snap': True}, 'default_values': {}, 'repeat_activated': False, 'shortcuts': {}}
session.VIDEO = {'duration': 10.0, 'framerate': 25, 'shot_changes': []}
host = Host()
tl = host.timeline_widget
tl.width_proportion = 100.0          # 100 px a second
Y = 158                              # inside the strip


def document():
    session.SUBTITLE = Subtitle({'segments': [{'id': 's1', 'start': 1.0, 'end': 3.0, 'text': [
        {'text': 'Hel', 'start': 0.0, 'end': 0.5, 'level': 'word'},
        {'text': 'lo ', 'start': 0.5, 'end': 0.8, 'level': 'word'},
        {'text': 'world', 'start': 1.0, 'end': 1.6, 'level': 'word'}]}], 'selected': None, 'position': 9.0})
    session.SUBTITLE.selected = session.SUBTITLE.segments[0]
    history.history_clear()
    image = QImage(1000, 200, QImage.Format_ARGB32)
    painter = QPainter(image)
    timeline_pieces.paint(tl, painter, session.SUBTITLE.selected, QRectF(100, 120, 200, 60))
    painter.end()
    return session.SUBTITLE.selected


def spans():
    return [(p['start'], p['end']) for p in timed_pieces(session.SUBTITLE.selected)]


def mouse(kind, x, modifiers=Qt.NoModifier, buttons=Qt.LeftButton):
    point = QPointF(x, Y)
    button = Qt.LeftButton if kind != QEvent.MouseMove else Qt.NoButton
    event = QMouseEvent(kind, point, point, button, buttons, modifiers)
    {QEvent.MouseButtonPress: tl.mousePressEvent, QEvent.MouseMove: tl.mouseMoveEvent,
     QEvent.MouseButtonRelease: tl.mouseReleaseEvent, QEvent.MouseButtonDblClick: tl.mouseDoubleClickEvent}[kind](event)


def drag(x0, *xs, modifiers=Qt.NoModifier):
    mouse(QEvent.MouseButtonPress, x0, modifiers)
    for x in xs:
        mouse(QEvent.MouseMove, x, modifiers)
    mouse(QEvent.MouseButtonRelease, xs[-1], modifiers, Qt.NoButton)


print('where they are')
document()
check('kept where it painted them', tl._pieces_strip[0], 's1')
check('the edge two pieces share', timeline_pieces.hit(tl, QPointF(151, Y)), ('edge', 0, 'between'))
check('an edge of its own (after a gap)', timeline_pieces.hit(tl, QPointF(181, Y)), ('edge', 1, 'end'))
check('a chip', timeline_pieces.hit(tl, QPointF(220, Y)), ('piece', 2))
check('above the strip: none', timeline_pieces.hit(tl, QPointF(220, 125)), None)

print('dragging an edge')
document()
drag(150, 155, 162)
check('the edge two share moves both', spans()[:2], [(0.0, 0.62), (0.62, 0.8)])
check('kept between their other ends', (drag(162, 120, 90), spans()[:2])[1], [(0.0, 0.0), (0.0, 0.8)])
history.history_undo()
check('one undo step a drag', spans()[:2], [(0.0, 0.62), (0.62, 0.8)])
document()
drag(150, 165, 170, modifiers=Qt.AltModifier)
check('with Alt, dragged right: only the later one, a gap opens', spans()[:2], [(0.0, 0.5), (0.7, 0.8)])
document()
drag(150, 140, 130, modifiers=Qt.AltModifier)
check('with Alt, dragged left: only the earlier one', spans()[:2], [(0.0, 0.3), (0.5, 0.8)])
document()
drag(180, 190, 260)
check('an edge of its own moves alone, up to the next piece', spans()[1:], [(0.5, 1.0), (1.0, 1.6)])

print('snapping')
document()
session.SUBTITLE.position = 1.7
drag(150, 168, 173)
check('to the playhead, a few pixels off', spans()[0], (0.0, 0.7))
session.CONFIG['timeline']['snap'] = False
document()
session.SUBTITLE.position = 1.7
drag(150, 173)
check('not with snapping off', spans()[0], (0.0, 0.73))
session.CONFIG['timeline']['snap'] = True
document()
tl.background_onsets = [2.31]
drag(260, 240, 235)
check('to an onset heard', spans()[2], (1.0, 1.31))
tl.background_onsets = None

print('a click, a double click')
document()
mouse(QEvent.MouseButtonPress, 220)
mouse(QEvent.MouseButtonRelease, 220, buttons=Qt.NoButton)
check('a chip: the playhead to where it starts', (host.preview_panel_player.at, session.SUBTITLE.position), (2.0, 2.0))
mouse(QEvent.MouseButtonDblClick, 220)
check('double: cut a level finer (a word into letters, with no dictionary)',
      [p['text'] for p in timed_pieces(session.SUBTITLE.selected)], ['Hel', 'lo ', 'w', 'o', 'r', 'l', 'd'])

print('tapping')
document()
for at in (1.1, 1.4, 1.9, 2.5):
    session.SUBTITLE.position = at
    timeline_pieces.tap(host)
check('one piece after another from the first; the last tap ends the last',
      spans(), [(0.1, 0.4), (0.4, 0.9), (0.9, 1.5)])
session.SUBTITLE.position = 1.2
timeline_pieces.tap(host)
check('tapped again from the start: over from the first', spans()[0], (0.2, 0.4))
session.SUBTITLE.position = 5.0
check('not in a timed subtitle: nothing', timeline_pieces.tap(host), False)

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
