"""Nudge keys: one undo step a key press, however long the key is held.

A nudge shortcut repeats while its key is held, and every repeat used to
push its own snapshot, so holding a key filled the undo stack. Now a press
is one step, a tap is one step each, and a click on a nudge button is one
step each.

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
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import session, history, shortcuts
from subtitld.interface import playercontrols

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


def near(value, places=3):
    return round(value, places)


class Player:
    def is_paused(self):
        return True


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(400, 200)
        self.setLayout(QVBoxLayout())
        self.preview_panel_player = Player()
        self.timeline_widget = QWidget()
        self.timeline_widget.setFocusPolicy(Qt.StrongFocus)
        self.layout().addWidget(self.timeline_widget)
        self.step_button = QPushButton()
        self.step_button.setCheckable(True)
        panel = types.SimpleNamespace(update=lambda window: None)
        self.left_panel_stackedwidgets = types.SimpleNamespace(currentWidget=lambda: panel)
        # Wired as playercontrols wires its buttons.
        self.move_forward_subtitle = QPushButton('>')
        self.move_forward_subtitle.clicked.connect(lambda: playercontrols.move_forward_subtitle_clicked(self))
        self.layout().addWidget(self.move_forward_subtitle)


FRAME = 1 / 25


def reset_document():
    session.SUBTITLE = {'segments': [
        {'start': 2.0, 'end': 4.0, 'text': 'One', 'speaker': 'A'},
        {'start': 6.0, 'end': 8.0, 'text': 'Two', 'speaker': 'A'},
    ], 'selected': None, 'position': 0.0}
    session.SUBTITLE['selected'] = session.SUBTITLE['segments'][1]
    history.history_clear()


session.CONFIG = {'timeline': {}, 'repeat_activated': False, 'shortcuts': {}}
session.VIDEO = {'duration': 60.0, 'framerate': 25}
reset_document()

host = Host()
shortcuts.load(host, session.CONFIG['shortcuts'])
host.show()
QTest.qWaitForWindowExposed(host)
host.activateWindow()
host.timeline_widget.setFocus()
QTest.qWait(50)
check('the window is active, so its shortcuts work', QApplication.activeWindow() is host, True)


def key(text, press, repeat=False):
    QTest.simulateEvent(QApplication.focusWidget(), press, ord(text), Qt.NoModifier, text, repeat, -1)


def tap(text):
    key(text, True)
    key(text, False)


def hold(text, repeats):
    """Press, repeat as X11 does (a release and a press, both flagged as
    repeats), then let go."""
    key(text, True)
    for _ in range(repeats):
        key(text, False, repeat=True)
        key(text, True, repeat=True)
    key(text, False)


def timing():
    selected = session.SUBTITLE['selected']
    return (near(selected['start']), near(selected['end']))


print('tapping a nudge key')
tap('6')
tap('6')
tap('6')
check('moves the subtitle a frame a tap', timing(), (near(6.0 + 3 * FRAME), near(8.0 + 3 * FRAME)))
check('one undo step a tap', len(history.ALL_HISTORY), 3)
check('letting go ends the press, and takes its filter off', host._nudge_hold._pressed, False)
history.history_undo()
check('one undo takes one tap back', timing(), (near(6.0 + 2 * FRAME), near(8.0 + 2 * FRAME)))

print('holding a nudge key')
reset_document()
history.history_append()        # an edit made before the press
earlier = history.top_snapshot()
hold('6', 10)
check('moves the subtitle a frame a repeat', timing(), (near(6.0 + 11 * FRAME), near(8.0 + 11 * FRAME)))
check('the history grows by exactly 1', len(history.ALL_HISTORY), 2)
history.history_undo()
check('one undo puts it back', timing(), (6.0, 8.0))
check('leaving the edit before it', history.top_snapshot() is earlier, True)

print('a press longer than the undo stack')
reset_document()
history.history_append()
earlier = history.top_snapshot()
hold('4', history.MAX_HISTORY + 20)
check('still one undo step', len(history.ALL_HISTORY), 2)
history.history_undo()
check('undone in one go', timing(), (6.0, 8.0))
check('with the edit before it still there', history.top_snapshot() is earlier, True)

print('the start and the end')
reset_document()
hold('7', 5)
hold('3', 5)
check('the start went 6 frames later, the end 6 earlier',
      timing(), (near(6.0 + 6 * FRAME), near(8.0 - 6 * FRAME)))
check('one undo step a press', len(history.ALL_HISTORY), 2)
history.history_undo()
check('undoing the end', timing(), (near(6.0 + 6 * FRAME), 8.0))
history.history_undo()
check('then the start', timing(), (6.0, 8.0))

print('a second key pressed while one is held')
reset_document()
key('9', True)
for _ in range(3):
    key('9', False, repeat=True)
    key('9', True, repeat=True)
key('1', True)                  # pressed over the held 9: a press of its own
for _ in range(3):
    key('1', False, repeat=True)
    key('1', True, repeat=True)
key('1', False)
key('9', False)
check('is a step of its own', len(history.ALL_HISTORY), 2)

print('clicking a nudge button')
reset_document()
for _ in range(3):
    QTest.mouseClick(host.move_forward_subtitle, Qt.LeftButton)
check('one undo step a click', len(history.ALL_HISTORY), 3)
tap('6')
check('and a key after it is another', len(history.ALL_HISTORY), 4)
QTest.mouseClick(host.move_forward_subtitle, Qt.LeftButton)
check('and a click after a key, another', len(history.ALL_HISTORY), 5)
check('the press stays on, for now', host._nudge_hold._pressed, True)
QTest.qWait(playercontrols._NudgeHold.LAPSE_MS + 200)
check('then lapses, taking its filter off', host._nudge_hold._pressed, False)

print('an edit made during a press')
reset_document()
key('6', True)
history.history_append()        # something else pushed a step mid-press
key('6', False, repeat=True)
key('6', True, repeat=True)
key('6', False)
check('the repeat after it records its own step', len(history.ALL_HISTORY), 3)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
