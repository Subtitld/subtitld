"""Playhead: between two audio callbacks the cursor glides on, at the playback speed.

While playing, playercontrols redraws the cursor 30 times a second
(_repaint_playhead_strip), but the audio engine, the clock the cursor follows,
moves its playhead once a callback: every 85 ms, at 4096 samples a block and
48 kHz. Drawn as read, the cursor would stand still for a tick or two, then
jump. So _smoothed_position_sec anchors on the engine's playhead each time it
moves, and in between runs the cursor on by the wall clock times the playback
speed: a quarter second at most, should the engine stall, and not at all while
paused, when the wall clock runs on but the audio does not. With no sound
device the engine never moves, and the picture is the clock: the cursor
follows it, and the video-sync watchdog leaves it be.

This runs the real playercontrols.load on a stand-in window, with a stand-in
media player and audio engine, and fires the cursor timer by hand under a fake
time.perf_counter. Every position it checks is one the real code drew, and it
draws the same on any machine.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, time, types
from pathlib import Path
from unittest import mock
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QSplitter
from PySide6.QtCore import QObject, QRect, Signal, QPropertyAnimation
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import session
from subtitld.interface import playercontrols

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


def near(value, places=6):
    return round(value, places)


# A slot that raises, raises into sys.excepthook, not into the emit.
errors = []


def _note_error(kind, value, tb):
    errors.append(repr(value))
    sys.__excepthook__(kind, value, tb)


sys.excepthook = _note_error


class MediaPlayer(QObject):
    """QMediaPlayer, as far as the cursor asks it: playing or not, and where
    the picture is."""
    playbackStateChanged = Signal(object)

    def __init__(self):
        super().__init__()
        self.state = QMediaPlayer.StoppedState
        self.position_ms = 0

    def playbackState(self):
        return self.state

    def position(self):
        return self.position_ms

    def setPosition(self, ms):
        self.position_ms = ms


class Engine:
    playhead = 0.0
    stream = 'an output stream'     # None: no sound device
    playing = True

    @property
    def heard_position(self):
        return self.playhead


class Player(QObject):
    position_changed_signal = Signal()

    def __init__(self):
        super().__init__()
        self._media_player = MediaPlayer()
        # The audio engine, as far as the cursor asks it: its playhead, which
        # each callback moves on by a block, and the position heard, the
        # same here, with no output latency.
        self._audio_device = Engine()

    def seek(self, position):
        pass

    def is_paused(self):
        return self._media_player.state != QMediaPlayer.PlayingState


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(1000, 300)
        self.setLayout(QVBoxLayout())
        self.preview_panel_player = Player()
        self.main_vertical_splitter = QSplitter()
        self.bottom_panel = types.SimpleNamespace(animation=QPropertyAnimation())
        panel = types.SimpleNamespace(update=lambda window: None)
        self.left_panel_stackedwidgets = types.SimpleNamespace(currentWidget=lambda: panel)
        self.subtitles_panel_qlistwidget = types.SimpleNamespace(update_content=lambda: None)


DURATION = 600.0
ZOOM = 100              # px a second

session.CONFIG = {'timeline': {}, 'default_values': {'minimum_subtitle_width': 0.5},
                  'timeline_zoom': ZOOM, 'repeat_activated': False, 'playback_speed': 1.0}
session.VIDEO = {'duration': DURATION, 'scenes': []}
session.SPEAKERS = {}
session.SUBTITLE = {'segments': [], 'selected': None, 'position': 0.0}

host = Host()
playercontrols.load(host)
host.layout().addWidget(host.playercontrols_widget)
# Shown, so the timeline is laid out at the zoom: the strip checks need the
# cursor's x a second apart as on screen, not squeezed into a few px.
host.show()
QTest.qWait(100)
player = host.preview_panel_player
media = player._media_player
engine = player._audio_device
timer = host._timeline_repaint_timer
widget = host.timeline_widget
wpp = widget.width_proportion

TICK = timer.interval() / 1000      # the cursor timer's period
BLOCK = 4096 / 48000                # an audio callback's, at SoundDeviceAudioEngine's default blocksize
CAP = 0.25                          # the furthest the cursor runs on past the engine's playhead

# The strips the cursor timer asks the timeline to repaint.
strips = []
_update = widget.update


def _noting_update(*args):
    if args and isinstance(args[0], QRect):
        strips.append(QRect(args[0]))
    return _update(*args)


widget.update = _noting_update

# The fake perf_counter: positive, as the real one is, and only moving on.
clock = [1000.0]


def tick(after):
    """`after` s of wall clock go by, then the cursor timer fires. Returns
    where it draws the cursor.

    The timer never fires by itself: past setup, nothing here runs the event
    loop."""
    clock[0] += after
    with mock.patch.object(time, 'perf_counter', lambda: clock[0]):
        timer.timeout.emit()
    return session.SUBTITLE['position']


def covers(strip, *positions):
    """Whether `strip` takes in the cursor at each of `positions`."""
    return all(strip.left() <= p * wpp < strip.right() + 1 for p in positions)


def set_state(state):
    media.state = state
    media.playbackStateChanged.emit(state)


def play(position, speed, seconds):
    """Play from `position` at `speed` for `seconds` of wall clock: the engine
    moves its playhead on a block each callback, the cursor timer fires in
    between. Returns, for each tick, where the audio is and where the cursor
    is drawn."""
    engine.playhead = position
    session.CONFIG['playback_speed'] = speed
    strips.clear()
    set_state(QMediaPlayer.PlayingState)
    ticks = []
    wall = 0.0
    callbacks = 1
    for n in range(1, int(seconds / TICK) + 1):
        at = n * TICK
        while callbacks * BLOCK <= at:
            engine.playhead += BLOCK * speed
            callbacks += 1
        ticks.append((position + at * speed, tick(at - wall)))
        wall = at
    set_state(QMediaPlayer.PausedState)
    return ticks


print(f'the cursor timer fires every {TICK * 1000:.0f} ms, the audio engine every {BLOCK * 1000:.1f} ms')
check('the timeline is as wide as the video at the zoom', widget.width(), int(DURATION * ZOOM))
set_state(QMediaPlayer.PlayingState)
check('playing starts the cursor timer', timer.isActive(), True)
set_state(QMediaPlayer.PausedState)
check('pausing stops it', timer.isActive(), False)

for speed in (1.0, 0.5, 2.0):
    print(f'playing 2 s at {speed}x')
    ticks = play(3.0, speed, 2.0)
    drawn = [d for _, d in ticks]
    lags = [heard - d for heard, d in ticks]
    steps = [b - a for a, b in zip(drawn, drawn[1:])]
    print(f'    {len(ticks)} ticks, {sum(steps) / len(steps) * 1000:.1f} ms a step on average, '
          f'trailing the audio by {min(lags) * 1000:.1f} to {max(lags) * 1000:.1f} ms')
    check('  the cursor moves on every tick', all(step > 0 for step in steps), True)
    check('  never ahead of the audio', all(lag > -1e-9 for lag in lags), True)
    # Drawn as read, it would trail by up to a whole block.
    check('  trailing it by a tick at most', all(lag < TICK * speed + 1e-9 for lag in lags), True)
    check('  each tick asks for one strip repaint', len(strips), len(ticks))
    check('  over where the cursor was and where it is',
          all(covers(strip, *drawn[max(n - 1, 0):n + 1]) for n, strip in enumerate(strips)), True)

print('the audio engine stalls at 10 s for 5 s')
engine.playhead = 10.0
session.CONFIG['playback_speed'] = 1.0
set_state(QMediaPlayer.PlayingState)
drawn = [tick(TICK) for _ in range(int(5.0 / TICK))]
check('  the first tick draws the engine\'s playhead', near(drawn[0]), 10.0)
check('  the cursor runs on a quarter second, no further', near(max(drawn)), 10.0 + CAP)
check('  and waits there', near(drawn[-1]), 10.0 + CAP)
set_state(QMediaPlayer.PausedState)

print('a seek from 5 s to 50 s while playing')
engine.playhead = 5.0
set_state(QMediaPlayer.PlayingState)
check('  the first tick draws 5 s', near(tick(TICK)), 5.0)
check('  the next a tick on', near(tick(TICK)), near(5.0 + TICK))
engine.playhead = 50.0
strips.clear()
check('  the tick after the seek draws 50 s', near(tick(TICK)), 50.0)
# Or the cursor would be left drawn at 5 s.
check('  and repaints from where the cursor was to there',
      len(strips) == 1 and covers(strips[0], 5.0 + TICK, 50.0), True)
check('  the next a tick on from there', near(tick(TICK)), near(50.0 + TICK))
set_state(QMediaPlayer.PausedState)

print('a click at 42 s while paused')
engine.playhead = 42.0
player.position_changed_signal.emit()
check('  the seek\'s position change leaves the cursor timer stopped', timer.isActive(), False)
# Ticks that fire anyway, as they did when the timer ran on through a pause,
# 2.666 s of wall clock in all.
drawn = [tick(after) for after in (0.016, 0.050, 0.100, 0.500, 2.000)]
check('  the cursor stays at 42 s through every tick', [near(d) for d in drawn], [42.0] * 5)
set_state(QMediaPlayer.PlayingState)
check('  playing starts the cursor timer again', timer.isActive(), True)
# Not 42.25, where an anchor kept from before would put it: the wall clock
# that went by while paused was not played.
check('  the first tick draws 42 s', near(tick(TICK)), 42.0)
check('  the next a tick on', near(tick(TICK)), near(42.0 + TICK))
set_state(QMediaPlayer.PausedState)

print('the video-sync watchdog, with a sound device')
watchdog = host._av_sync_timer
engine.playhead, media.position_ms = 5.0, 20_000
set_state(QMediaPlayer.PlayingState)
watchdog.timeout.emit()
check('  a picture 15 s off is put back where the sound is', media.position_ms, 5000)
set_state(QMediaPlayer.PausedState)

print('no sound device: the picture is the clock')
# The engine never started, so its playhead stands where play() left it.
engine.stream, engine.playing, engine.playhead = None, False, 0.04
media.position_ms = 12_000
set_state(QMediaPlayer.PlayingState)
watchdog.timeout.emit()
check('  the watchdog leaves the picture where it is', media.position_ms, 12_000)
check('  the cursor follows the picture', near(tick(TICK)), 12.0)
media.position_ms = 12_500
check('  and moves on with it', near(tick(TICK)), 12.5)
set_state(QMediaPlayer.PausedState)
check('  paused, it stays where the picture stopped', near(tick(0.5)), 12.5)
engine.stream, engine.playing = 'an output stream', True

check('no slot raised', errors, [])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
