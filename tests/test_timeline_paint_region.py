"""Timeline: repainting the playhead strip walks only what it has to.

While playing, playercontrols repaints a strip about 100 px wide around the
cursor 30 times a second (_repaint_playhead_strip). All of paintEvent's Python
holds the GIL the audio callback needs, so Timeline._paint_body narrows its
loops to the dirty rect: the time labels, the waveform's buckets and the onset
markers are walked for the strip alone. The subtitles are walked for the whole
viewport, though, not the strip: a dub band can reach past its subtitle's start
and end, and a band crossing the strip from a subtitle outside it would be left
half painted.

This drives the real Timeline offscreen, 800 subtitles over 1600 s of video,
and records what its painter draws, once for the viewport and once for a strip.
It counts what is drawn, not how long drawing takes, so it passes or fails the
same way on any machine.

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
import numpy as np
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PySide6.QtCore import QRect, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import session, utils
from subtitld.interface import timeline

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


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


# What the timeline's painter draws while `drawn` is a list, rather than None.
drawn = None


class RecordingPainter(QPainter):
    """The timeline's QPainter, noting what it draws on the timeline."""

    def __init__(self, *args):
        super().__init__(*args)
        self.recording = bool(args) and isinstance(args[0], timeline.Timeline)

    def drawRoundedRect(self, *args):
        if self.recording and drawn is not None:
            drawn.append(('rect', QRectF(args[0])))
        return super().drawRoundedRect(*args)

    def drawText(self, *args):
        if self.recording and drawn is not None:
            drawn.append(('text', next(a for a in args if isinstance(a, str))))
        return super().drawText(*args)

    def drawPath(self, path):
        if self.recording and drawn is not None:
            drawn.append(('path', path.boundingRect(), self.brush().color().name(QColor.HexArgb)))
        return super().drawPath(path)

    def drawPixmap(self, *args):
        if self.recording and drawn is not None:
            drawn.append(('pixmap', QPointF(args[0]).x()))
        return super().drawPixmap(*args)


timeline.QPainter = RecordingPainter

DURATION = 1600.0
ZOOM = 100              # px a second
SAMPLERATE = 48000
BUCKET = 512            # samples a waveform bucket, as get_level picks it for 480 samples a pixel
WAVEFORM_FILL = '#ff123456'
# These put the edges of the viewport and of the strip well clear of every
# subtitle's start and end, every second's tick and every onset, so what each
# edge takes in does not hang on how the code rounds.
SCROLL = 80175          # 801.75 s, between subtitles 400 and 401
CURSOR = 806.25         # inside the viewport, between subtitles 402 and 403

session.CONFIG = {'timeline': {'show_onset_markers': True, 'waveform_fill_color': WAVEFORM_FILL},
                  'default_values': {'minimum_subtitle_width': 0.5},
                  'timeline_zoom': ZOOM, 'repeat_activated': False, 'playback_speed': 1.0}
session.VIDEO = {'duration': DURATION, 'scenes': [], 'samplerate': SAMPLERATE}
session.SPEAKERS = {'A': {'color': '#ff2e93'}, 'B': {'color': '#2e93ff'}}
# One subtitle every 2 s, 1.5 s long: subtitle i spans [2i, 2i + 1.5].
session.SUBTITLE = {'segments': [{'start': i * 2.0, 'end': i * 2.0 + 1.5, 'text': f'sub {i}', 'speaker': 'AB'[i % 2]}
                                 for i in range(int(DURATION / 2))],
                    'selected': None, 'position': CURSOR}

host = Host()
timeline.load(host)
host.layout().addWidget(host.timeline_scroll)
host.show()
QTest.qWait(100)
scroll = host.timeline_scroll
widget = host.timeline_widget
wpp = widget.width_proportion

# A waveform already built at the bucket size this zoom asks for, so painting
# draws it rather than starting a worker to build one. Only the level is drawn;
# the samples have to be there, no more.
buckets = int(DURATION * SAMPLERATE / BUCKET) + 1
widget.waveform_manager.samples = np.zeros(1, np.int16)
widget.waveform_manager.levels[BUCKET] = (np.full(buckets, -0.5, np.float32),
                                          np.full(buckets, 0.5, np.float32), BUCKET)
bucket_px = BUCKET / SAMPLERATE * wpp
# An onset every quarter second, an eighth of a second off the beat.
onsets = np.arange(0.125, DURATION, 0.25).astype(np.float32)
widget.background_onsets = onsets
onset_glow_px = widget._onset_marker_pixmap().width()   # a marker reaches this far right of its onset

scroll.horizontalScrollBar().setValue(SCROLL)
QTest.qWait(50)
check('the timeline is as wide as the video at the zoom', widget.width(), int(DURATION * ZOOM))
check('scrolled to 801.75 s', scroll.horizontalScrollBar().value(), SCROLL)

# The [left, right) px of each paint event's rect: its height is the viewport's.
event_spans = []
_paint_body = widget._paint_body


def _noting_paint_body(painter, event):
    event_spans.append((event.rect().left(), event.rect().right() + 1))
    _paint_body(painter, event)


widget._paint_body = _noting_paint_body


def paint(rect=None):
    """Repaint the timeline, or just `rect` of it; return what was drawn."""
    global drawn
    QTest.qWait(10)         # pending updates, out of the way of this one
    event_spans.clear()
    drawn = []
    if rect is None:
        widget.repaint()
    else:
        widget.repaint(rect)
    got, drawn = drawn, None
    check('  painted without an error', getattr(widget, '_last_paint_error', None), None)
    return got


def subtitles_drawn(got):
    """Indexes of the subtitles drawn, known by their rectangles."""
    rects = {(round(s['start'] * wpp), round((s['end'] - s['start']) * wpp)): i
             for i, s in enumerate(session.SUBTITLE['segments'])}
    found = []
    for kind, *rest in got:
        if kind == 'rect':
            i = rects.get((round(rest[0].x()), round(rest[0].width())))
            if i is not None:
                found.append(i)
    return sorted(found)


def subtitles_within(left, right):
    """Indexes of the subtitles that reach into [left, right) px."""
    return [i for i, s in enumerate(session.SUBTITLE['segments'])
            if s['start'] * wpp < right and s['end'] * wpp > left]


labels = {utils.get_timeline_time_str(sec): sec for sec in range(int(DURATION))}


def seconds_labelled(got):
    return sorted(labels[rest[0]] for kind, *rest in got if kind == 'text' and rest[0] in labels)


def waveforms(got):
    return [rest[0] for kind, *rest in got if kind == 'path' and rest[1] == WAVEFORM_FILL]


def onset_markers(got):
    return sum(1 for kind, *rest in got if kind == 'pixmap')


def onsets_within(left, right):
    """Onsets whose marker, line and glow, reaches into [left, right) px."""
    xs = onsets.astype(np.float64) * wpp
    return int(np.count_nonzero((xs < right) & (xs + onset_glow_px > left)))


def check_labels(got, left, right):
    """Each second whose tick is in [left, right) labelled, and none more
    than a second before or after it."""
    secs = seconds_labelled(got)
    inside = [sec for sec in range(int(DURATION)) if left <= sec * wpp < right]
    check('  labels every second it shows', set(inside) <= set(secs), True)
    check('  and none a second away from it', all(left - wpp <= sec * wpp < right + wpp for sec in secs), True)
    return secs


print('a repaint of the whole viewport')
view_left = SCROLL
view_right = SCROLL + scroll.viewport().width()
in_view = subtitles_within(view_left, view_right)
check('the viewport shows subtitles 401 to 405', in_view, [401, 402, 403, 404, 405])
got = paint()
check('  the event is the viewport', event_spans, [(view_left, view_right)])
check('  draws the subtitles in it, of the 800', subtitles_drawn(got), in_view)
secs = check_labels(got, view_left, view_right)
print(f'    labelled {len(secs)} seconds' + (f', {secs[0]} to {secs[-1]}' if secs else ''))
waves = waveforms(got)
check('  draws one waveform', len(waves), 1)
check('  across the viewport, give or take a bucket', len(waves) == 1
      and waves[0].left() <= view_left + bucket_px and waves[0].right() >= view_right - bucket_px, True)
check('  marks the onsets that reach into it', onset_markers(got), onsets_within(view_left, view_right))

print('a repaint of the playhead strip')
# The strip _repaint_playhead_strip asks for on play's first frame: 96 px
# left of the cursor for the speed badge, 8 px right of it.
cursor_x = int(CURSOR * wpp)
strip = QRect(cursor_x - 96, 0, 96 + 8, widget.height())
strip_left, strip_right = strip.left(), strip.right() + 1
check('the strip reaches into subtitles 402 and 403 alone', subtitles_within(strip_left, strip_right), [402, 403])
check('and the markers of 5 onsets', onsets_within(strip_left, strip_right), 5)
got = paint(strip)
check('  the event is the strip', event_spans, [(strip_left, strip_right)])
# Not 402 and 403 alone: a dub band can reach past its subtitle's start and
# end (see the comment above visible_start_sec in _paint_body).
check('  still draws every subtitle in the viewport, and no more', subtitles_drawn(got), in_view)
secs = check_labels(got, strip_left, strip_right)
check('  labels 805 and 806 alone', secs, [805, 806])
waves = waveforms(got)
check('  draws one waveform', len(waves), 1)
check('  across the strip alone, give or take a bucket', len(waves) == 1
      and strip_left - 2 * bucket_px <= waves[0].left() <= strip_left + bucket_px
      and strip_right - bucket_px <= waves[0].right() <= strip_right + 2 * bucket_px, True)
check('  marks only the onsets that reach into the strip', onset_markers(got), 5)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
