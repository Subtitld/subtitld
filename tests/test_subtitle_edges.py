"""Moving a subtitle's start or end, and the neighbour glued to it.

With "snap moving to nearest" on, a start 1 ms after the previous end drags
that end along. A nudge by an amount used to move the end the other way
(only an absolute time moved it right). And on the frames grid, a drag on
the timeline added the cursor's remainder within a frame to the edge on
every mouse move, so the edge crept right whatever the cursor did; now it
goes to the frame nearest the cursor, as the vertical timeline does. And an
absolute time of 0.0 read as "none given", so nothing moved to the very start.
Moving the last subtitle's end with it on looked for a next subtitle past the
end of the list, and raised IndexError; moving the first one's start looked
at index -1, the last subtitle.

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
from PySide6.QtWidgets import QApplication, QWidget, QPushButton
from PySide6.QtCore import Qt, QPointF, QEvent
from PySide6.QtGui import QMouseEvent
app = QApplication([])
from subtitld.modules import session, subtitles, history
session.set_unsaved = lambda *a, **k: None
from subtitld.interface import playercontrols, timeline
from subtitld.interface import left_panel_vtimeline as lpv

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


def near(value, places=3):
    return round(value, places)


FRAME = 1 / 25


def document(*timings):
    session.SUBTITLE = {'segments': [{'start': start, 'end': end, 'text': f'Cue {i}'} for i, (start, end) in enumerate(timings)],
                        'selected': None, 'position': 0.0}
    history.history_clear()
    return session.SUBTITLE['segments']


def glued(previous, following):
    return near(following['start'] - previous['end']) == .001


def raises(action):
    try:
        action()
    except Exception as error:
        return f'{type(error).__name__}: {error}'
    return None


print('nudging a start by an amount, with "snap moving to nearest"')
a, b = document((1.0, 3.0), (3.001, 5.0))
subtitles.move_start_subtitle(selected_subtitle=b, amount=.04, move_nereast=True, record=False)
check('later: the start moves', near(b['start']), 3.041)
check('and the previous end goes with it', near(a['end']), 3.04)
a, b = document((1.0, 3.0), (3.001, 5.0))
subtitles.move_start_subtitle(selected_subtitle=b, amount=-.04, move_nereast=True, record=False)
check('earlier: the start moves', near(b['start']), 2.961)
check('and the previous end goes with it', near(a['end']), 2.96)

print('the same as moving it to an absolute time')
a, b = document((1.0, 3.0), (3.001, 5.0))
subtitles.move_start_subtitle(selected_subtitle=b, absolute_time=3.041, move_nereast=True, record=False)
check('both ways land the same', (near(a['end']), near(b['start'])), (3.04, 3.041))

print('many nudges, both ways')
a, b = document((1.0, 3.0), (3.001, 5.0))
for amount in [FRAME] * 7 + [-FRAME] * 12 + [FRAME] * 2:
    subtitles.move_start_subtitle(selected_subtitle=b, amount=amount, move_nereast=True, record=False)
check('the start went 3 frames earlier', near(b['start']), near(3.001 - 3 * FRAME))
check('and the two are still 1 ms apart', glued(a, b), True)
check('the previous start stays put', a['start'], 1.0)

print('only a neighbour 1 ms away is dragged along')
a, b = document((1.0, 3.0), (3.5, 5.0))
subtitles.move_start_subtitle(selected_subtitle=b, amount=-.04, move_nereast=True, record=False)
check('a gap: the previous end stays', a['end'], 3.0)
a, b = document((1.0, 3.0), (3.001, 5.0))
subtitles.move_start_subtitle(selected_subtitle=b, amount=.04, move_nereast=False, record=False)
check('snapping to nearest off: the previous end stays', a['end'], 3.0)
check('and the start moves alone', near(b['start']), 3.041)
a, b = document((1.0, 3.0), (3.001, 5.0))
subtitles.move_start_subtitle(selected_subtitle=a, amount=.2, move_nereast=True, record=False)
check('the first subtitle has none to drag', (near(a['start']), a['end'], b['start'], b['end']), (1.2, 3.0, 3.001, 5.0))
# Sorted, and each ending after it starts, the last subtitle can never end
# 1 ms before the first starts; out of order, it can, so the lookup must not
# wrap round to it.
a, x, z = document((5.0, 6.0), (7.0, 8.0), (1.0, 4.999))
subtitles.move_start_subtitle(selected_subtitle=a, amount=.2, move_nereast=True, record=False)
check('nor does it reach round to the last in the list', (near(a['start']), z['end']), (5.2, 4.999))

print('the end, the mirror of it')
a, b = document((1.0, 3.0), (3.001, 5.0))
subtitles.move_end_subtitle(selected_subtitle=a, amount=.04, move_nereast=True, record=False)
check('later: the next start goes with it', (near(a['end']), near(b['start'])), (3.04, 3.041))
subtitles.move_end_subtitle(selected_subtitle=a, amount=-.08, move_nereast=True, record=False)
check('earlier: the next start goes with it', (near(a['end']), near(b['start'])), (2.96, 2.961))

print('the end of the last subtitle, with "snap moving to nearest"')
a, b = document((1.0, 3.0), (4.0, 6.0))
check('by an amount: no error', raises(lambda: subtitles.move_end_subtitle(selected_subtitle=b, amount=.04, move_nereast=True, record=False)), None)
check('it moves', near(b['end']), 6.04)
check('to an absolute time: no error', raises(lambda: subtitles.move_end_subtitle(selected_subtitle=b, absolute_time=7.0, move_nereast=True, record=False)), None)
check('it moves', b['end'], 7.0)
check('and the one before is left alone', (a['start'], a['end']), (1.0, 3.0))
(only,) = document((1.0, 3.0))
check('the only subtitle: no error', raises(lambda: subtitles.move_end_subtitle(selected_subtitle=only, amount=.5, move_nereast=True, record=False)), None)
check('it moves', only['end'], 3.5)

print('an absolute time of 0.0 is a time')
a, b = document((1.0, 3.0), (4.0, 6.0))
subtitles.move_start_subtitle(selected_subtitle=a, absolute_time=0.0, record=False)
check('a start goes to the very start', (a['start'], a['end']), (0.0, 3.0))
a, b = document((1.0, 3.0), (4.0, 6.0))
subtitles.move_subtitle(selected_subtitle=a, absolute_time=0.0, record=False)
check('a whole subtitle too, keeping its length', (a['start'], a['end']), (0.0, 2.0))
a, b = document((1.0, 3.0), (4.0, 6.0))
subtitles.move_end_subtitle(selected_subtitle=b, amount=-1.0, record=False)
check('and leaving it out still moves by the amount', (b['start'], b['end']), (4.0, 5.0))


class Player:
    def is_paused(self):
        return True


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(1000, 200)
        self.preview_panel_player = Player()
        self.timeline_widget = timeline.Timeline(self)
        self.timeline_widget.setGeometry(0, 0, 1000, 200)
        self.step_button = QPushButton()
        self.step_button.setCheckable(True)
        panel = types.SimpleNamespace(update=lambda window: None)
        self.left_panel_stackedwidgets = types.SimpleNamespace(currentWidget=lambda: panel)


session.CONFIG = {'timeline': {'snap': True, 'snap_limits': True, 'snap_value': .1, 'snap_grid': False, 'snap_move_nereast': True},
                  'default_values': {'minimum_subtitle_width': .5}, 'repeat_activated': False, 'shortcuts': {}}
session.VIDEO = {'duration': 10.0, 'framerate': 25, 'scenes': []}
host = Host()

print('the nudge keys, 1 and 7, with "snap moving to nearest" on')
a, b = document((1.0, 3.0), (3.001, 5.0))
session.SUBTITLE['selected'] = b
for _ in range(3):
    playercontrols.move_start_forward_subtitle_clicked(host)
check('7 three times: the start goes 3 frames later', near(b['start']), near(3.001 + 3 * FRAME))
check('with the previous end 1 ms behind it', glued(a, b), True)
for _ in range(5):
    playercontrols.move_start_back_subtitle_clicked(host)
check('1 five times: 2 frames before where it was', near(b['start']), near(3.001 - 2 * FRAME))
check('with the previous end still 1 ms behind it', glued(a, b), True)
session.CONFIG['timeline']['snap_move_nereast'] = False
playercontrols.move_start_back_subtitle_clicked(host)
check('with it off, 1 leaves the previous end alone', glued(a, b), False)
check('so the two overlap', b['start'] < a['end'], True)


tl = host.timeline_widget
tl.width_proportion = 100.0     # 100 px a second: x / 100 is the time under the cursor
tl.offset = 0.0


def drag(edge, *xs):
    tl.subtitle_start_is_clicked = edge == 'start'
    tl.subtitle_end_is_clicked = edge == 'end'
    tl.subtitle_is_clicked = edge == 'whole'
    tl.is_cursor_pressing = True
    positions = []
    selected = session.SUBTITLE['selected']
    try:
        for x in xs:
            tl.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(x, 5), QPointF(x, 5), Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
            positions.append(near(selected['start'] if edge != 'end' else selected['end']))
    finally:
        tl.subtitle_start_is_clicked = tl.subtitle_end_is_clicked = tl.subtitle_is_clicked = False
        tl.is_cursor_pressing = False
    return positions


session.CONFIG['timeline'].update({'snap_grid': True, 'grid_type': 'frames', 'snap_move_nereast': False})

print('dragging a start on the frames grid')
a, b = document((1.0, 2.0), (4.0, 8.0))
session.SUBTITLE['selected'] = b
check('it goes to the frame nearest the cursor, and stays while the cursor does',
      drag('start', 313, 313, 313), [3.12, 3.12, 3.12])
check('back to the right, then to the left', drag('start', 451, 449, 363), [4.52, 4.48, 3.64])

print('dragging an end on the frames grid')
a, b = document((1.0, 2.0), (4.0, 8.0))
session.SUBTITLE['selected'] = b
check('it goes to the frame nearest the cursor', drag('end', 613, 613, 687, 701), [6.12, 6.12, 6.88, 7.0])

print('dragging a whole subtitle on the frames grid')
a, b = document((1.0, 2.0), (4.0, 8.0))
session.SUBTITLE['selected'] = b
check('it goes to the frame nearest the cursor', drag('whole', 313, 313, 451), [3.12, 3.12, 4.52])
check('and keeps its length', near(b['end'] - b['start']), 4.0)

print('dragging a glued start on the frames grid, with "snap moving to nearest"')
session.CONFIG['timeline']['snap_move_nereast'] = True
a, b = document((1.0, 3.0), (3.001, 5.0))
session.SUBTITLE['selected'] = b
check('the start follows the cursor, on frames', drag('start', 361, 352, 337), [3.6, 3.52, 3.36])
check('and the previous end follows it, 1 ms behind', near(a['end']), 3.359)

print('dragging to the very start, with no limit to snap to')
session.CONFIG['timeline'].update({'snap_limits': False, 'snap_moving': False, 'snap_move_nereast': False})
a, b = document((1.0, 3.0), (4.0, 8.0))
session.SUBTITLE['selected'] = a
check('a start goes to frame 0', drag('start', 33, 1), [0.32, 0.0])
a, b = document((1.0, 3.0), (4.0, 8.0))
session.SUBTITLE['selected'] = a
check('a whole subtitle goes to frame 0', drag('whole', 33, 1), [0.32, 0.0])
check('keeping its length', near(a['end']), 2.0)
session.CONFIG['timeline']['grid_type'] = 'seconds'
a, b = document((1.0, 3.0), (4.0, 8.0))
session.SUBTITLE['selected'] = a
check('on the seconds grid, a start goes to second 0', drag('start', 33, 5), [0.33, 0.0])

print('dragging the last end, with "snap moving to nearest"')
session.CONFIG['timeline'].update({'snap_limits': True, 'grid_type': 'frames', 'snap_move_nereast': True})
a, b = document((1.0, 3.0), (4.0, 6.0))
session.SUBTITLE['selected'] = b
check('on the timeline: no error', raises(lambda: drag('end', 613, 687)), None)
check('the end follows the cursor', near(b['end']), 6.88)
a, b = document((1.0, 3.0), (4.0, 6.0))
check('on the vertical timeline, pushing: no error', raises(lambda: lpv._move('end', b, 7.0, record=False, push=True)), None)
check('the end moves', b['end'], 7.0)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
