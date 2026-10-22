"""Spike: play with a prerendered HTML overlay drawn in the video item's paint().

Hidden QWebEngineViews render overlay frames ahead of the playhead; the video
item draws the cached overlay for exactly the frame it is painting, so there is
no lead to tune. A frame that is not cached yet falls back to the live overlay
(the transparent view from spike.py, drawing --lead ms ahead). The source is
chosen when a frame arrives and applied by moving the live view in or out of
the video rect, which Qt shows in the same repaint as the frame.

    .venv/bin/python spikes/html_overlay/cached_player.py [video] [--views 4] [--render-size 960x540]
                                                          [--ahead 4] [--no-live] [--lead 80]
                                                          [--snap DIR] [--quit-after S]

Keys: space play/pause · ←/→ seek 1 s · ,/. step one frame · 1/2/3 rate 0.5/1/2
      e edit the cue under the playhead · c clear the cache · q quit
"""
import argparse
import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QRectF, QSizeF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QFont, QImage, QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsScene, QGraphicsView,
                               QVBoxLayout, QWidget)

from render_bench import read_code

HERE = Path(__file__).resolve().parent
FRAME_MS = 40.0
RAFS = 3


def merge_rects(rects):
    """Union overlapping rects, so no pixel is drawn twice (alpha would stack)."""
    rects = [QRect(*r) for r in rects]
    merged = True
    while merged:
        merged = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                if rects[i].intersects(rects[j]):
                    rects[i] = rects[i].united(rects.pop(j))
                    merged = True
                    break
            if merged:
                break
    return rects


class ConsolePage(QWebEnginePage):
    def __init__(self, on_message, parent):
        super().__init__(parent)
        self.on_message = on_message

    def javaScriptConsoleMessage(self, level, message, line, source):
        self.on_message(message)


class Renderer(QObject):
    """One hidden view rendering one frame at a time."""

    def __init__(self, cache, size):
        super().__init__()
        self.cache = cache
        self.w, self.h = size
        self.job = None          # (frame, generation)
        self.ready = False

        self.view = QWebEngineView()
        page = ConsolePage(self.on_console, self.view)
        self.view.setPage(page)
        page.setBackgroundColor(Qt.transparent)
        self.view.setAttribute(Qt.WA_DontShowOnScreen)
        self.view.setAttribute(Qt.WA_TranslucentBackground)
        self.view.resize(self.w, self.h)
        self.view.show()
        self.view.loadFinished.connect(self.on_loaded)
        self.view.load(QUrl.fromLocalFile(str(HERE / 'overlay.html')))

    def on_loaded(self, ok):
        # Let fonts and first layout settle; first captures were wrong in the bench.
        QTimer.singleShot(800, self.warm)

    def warm(self):
        self.ready = True
        self.cache.schedule()

    def js(self, code):
        self.view.page().runJavaScript(code)

    def render(self, frame, generation):
        self.job = (frame, generation)
        self.t0 = time.perf_counter()
        self.js(f'renderAt({frame * FRAME_MS}, {frame}, {RAFS})')

    def on_console(self, message):
        if not message.startswith('RENDERED ') or self.job is None:
            return
        _, frame_s, rects_s = message.split(' ', 2)
        frame, generation = self.job
        self.job = None
        img = self.view.grab().toImage()
        self.cache.on_rendered(self, frame, generation, img, json.loads(rects_s), time.perf_counter() - self.t0)


class FrameCache(QObject):
    def __init__(self, player_widget, args):
        super().__init__()
        self.pw = player_widget
        self.ahead = int(args.ahead * 1000 / FRAME_MS)
        self.behind = int(1.0 * 1000 / FRAME_MS)
        size = tuple(map(int, args.render_size.split('x')))
        self.scale = size[0] / 1920
        self.frames = {}          # frame -> [(QRectF normalised, QImage)]
        self.generation = {}      # cue index -> edit count
        self.renderers = [Renderer(self, size) for _ in range(args.views)]
        self.stats = {'rendered': 0, 'wrong': 0, 'stale': 0, 'hits': 0, 'live': 0, 'waits': 0, 'misses': 0,
                      'render_ms': []}

    @staticmethod
    def cue_of(frame):
        return int(frame * FRAME_MS // 2500)

    def gen_of(self, frame):
        return self.generation.get(self.cue_of(frame), 0)

    def schedule(self):
        head = self.pw.playhead_frame()
        # Evict what is far from the playhead.
        for f in [f for f in self.frames if f < head - self.behind or f > head + 2 * self.ahead]:
            del self.frames[f]
        in_flight = {r.job[0] for r in self.renderers if r.job}
        todo = (f for f in range(head, head + self.ahead) if f not in self.frames and f not in in_flight)
        for r in self.renderers:
            if r.ready and r.job is None:
                f = next(todo, None)
                if f is None:
                    break
                r.render(f, self.gen_of(f))

    def on_rendered(self, renderer, frame, generation, img, rects, seconds):
        if generation != self.gen_of(frame):
            self.stats['stale'] += 1
        elif read_code(img, self.scale) != frame:
            self.stats['wrong'] += 1           # captured too early: render again
        else:
            w, h = img.width(), img.height()
            crops = []
            for r in merge_rects(rects):
                crop = img.copy(r).convertToFormat(QImage.Format_ARGB32_Premultiplied)
                crops.append((QRectF(r.x() / w, r.y() / h, r.width() / w, r.height() / h), crop))
            self.frames[frame] = crops
            self.stats['rendered'] += 1
            self.stats['render_ms'].append(seconds * 1000)
            if frame == self.pw.on_screen_frame():
                self.pw.decide(frame)          # paused or seeking to a frame that just arrived
        self.schedule()

    def edit_cue(self, cue, text):
        self.generation[cue] = self.generation.get(cue, 0) + 1
        for r in self.renderers:
            r.js(f'editCue({cue}, {json.dumps(text)})')
        if self.pw.live:
            self.pw.live.page().runJavaScript(f'editCue({cue}, {json.dumps(text)})')
        start, end = int(cue * 2500 / FRAME_MS), int((cue + 1) * 2500 / FRAME_MS)
        for f in [f for f in self.frames if start <= f < end]:
            del self.frames[f]
        self.pw.decide(self.pw.shown_frame)
        self.schedule()

    def clear(self):
        self.frames.clear()
        self.pw.decide(self.pw.shown_frame)
        self.schedule()

    def ahead_count(self, head):
        n = 0
        while head + n in self.frames:
            n += 1
        return n


class VideoItem(QGraphicsVideoItem):
    def __init__(self, pw):
        super().__init__()
        self.pw = pw

    def video_rect(self):
        item = self.boundingRect()
        native = self.nativeSize()
        if native.width() <= 0 or native.height() <= 0:
            return item
        scale = min(item.width() / native.width(), item.height() / native.height())
        w, h = native.width() * scale, native.height() * scale
        return QRectF(item.x() + (item.width() - w) / 2, item.y() + (item.height() - h) / 2, w, h)

    def paint(self, painter, option, widget):
        super().paint(painter, option, widget)
        pw = self.pw
        frame = pw.current_video_frame()
        pw.shown_frame = frame
        rect = self.video_rect()

        # The item can paint a new frame before on_video_frame has run for it,
        # so decide() looks a frame ahead and this only checks which source is
        # in place: the live view covers the frame, or the cache draws it.
        crops = None if pw.live_visible else pw.cache.frames.get(frame)
        if crops is not None:
            painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform)
            for norm, img in crops:
                target = QRectF(rect.x() + norm.x() * rect.width(), rect.y() + norm.y() * rect.height(),
                                norm.width() * rect.width(), norm.height() * rect.height())
                painter.drawImage(target, img)
        source = ('CACHE' if crops is not None else 'LIVE' if pw.live_visible
                  else 'WAIT' if pw.live_ready else 'MISS')
        if frame != pw.last_counted:
            pw.last_counted = frame
            pw.cache.stats[{'CACHE': 'hits', 'LIVE': 'live', 'WAIT': 'waits', 'MISS': 'misses'}[source]] += 1
            if source == 'WAIT':
                print(f'wait: frame {frame} (live drew {pw.live_drawn})', flush=True)

        # Status, drawn by the host (not part of the overlay).
        head = pw.playhead_frame()
        s = pw.cache.stats
        status = (f'frame {frame}  {source:<5}  ahead {pw.cache.ahead_count(head)}  '
                  f'cache {s["hits"]}  live {s["live"]}  wait {s["waits"]}')
        painter.setFont(QFont('DejaVu Sans Mono', 11))
        box = QRectF(rect.right() - 560, rect.bottom() - 32, 552, 24)
        painter.fillRect(box, QColor(0, 0, 0, 170))
        painter.setPen({'CACHE': QColor('#7f7'), 'LIVE': QColor('#fd5'), 'WAIT': QColor('#f95'),
                        'MISS': QColor('#f55')}[source])
        painter.drawText(box.adjusted(8, 0, 0, 0), Qt.AlignVCenter, status)


class View(QGraphicsView):
    def __init__(self, scene, on_resize):
        super().__init__(scene)
        self.on_resize = on_resize

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.on_resize()


class PlayerWidget(QWidget):
    def __init__(self, video, args):
        super().__init__()
        self.args = args
        self.setWindowTitle('Cached HTML overlay spike')
        self.resize(1280, 760)
        self.shown_frame = -1
        self.last_counted = -1

        self.scene = QGraphicsScene(self)
        self.view = View(self.scene, self.fit_video)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setBackgroundBrush(Qt.black)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.video_item = VideoItem(self)
        self.scene.addItem(self.video_item)
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video_item)
        self.player.setSource(QUrl.fromLocalFile(str(video)))
        # The sink's videoFrame() is set from the decoder thread and can be a
        # frame ahead of what the item paints; the signal reaches the item and
        # us in order, so take the time from it.
        self.signal_frame = -1
        self.video_item.videoSink().videoFrameChanged.connect(self.on_video_frame)

        self.cache = FrameCache(self, args)

        # Live fallback: always rendering; parked outside the view when the
        # cache has the frame. Parking keeps Chromium drawing (the widget stays
        # visible), so it is current the moment it is moved back.
        self.decided_for = -1
        self.seek_target = None   # frame a seek is heading to, until the decoder gets there
        self.decoded = {'cache': 0, 'live': 0, 'blank': 0}
        self.live_drawn = -1
        self.live_visible = False
        self.live_ready = False
        self.live_rect = QRect()
        self.live = None
        if not args.no_live:
            self.live = QWebEngineView(self.view)
            self.live.setPage(ConsolePage(self.on_live_console, self.live))
            self.live.page().setBackgroundColor(Qt.transparent)
            self.live.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.live.setFocusPolicy(Qt.NoFocus)
            self.live.loadFinished.connect(self.on_live_loaded)
            url = QUrl.fromLocalFile(str(HERE / 'overlay.html'))
            url.setQuery(f'lead={args.lead}&report=1')
            self.live.load(url)
            self.player.playbackStateChanged.connect(lambda _s: self.push_live())

        keys = {
            'Space': self.toggle_play, 'Left': lambda: self.seek(-1000), 'Right': lambda: self.seek(1000),
            ',': lambda: self.step(-1), '.': lambda: self.step(1),
            '1': lambda: self.player.setPlaybackRate(0.5), '2': lambda: self.player.setPlaybackRate(1.0),
            '3': lambda: self.player.setPlaybackRate(2.0),
            'E': self.edit, 'C': self.cache.clear, 'Q': self.close,
        }
        for key, fn in keys.items():
            QShortcut(QKeySequence(key), self, activated=fn, context=Qt.ApplicationShortcut)

        self.edits = 0
        stats_timer = QTimer(self, interval=2000)
        stats_timer.timeout.connect(self.print_stats)
        stats_timer.start()
        self.last_rendered = 0

        if args.autoplay:
            self.wait_preroll()
        if args.snap:
            self.snap_dir = Path(args.snap)
            self.snap_dir.mkdir(parents=True, exist_ok=True)
            self.snap_n = 0
            t = QTimer(self, interval=int(args.snap_every * 1000))
            t.timeout.connect(self.snap)
            t.start()
        if args.seek_every:
            t = QTimer(self, interval=int(args.seek_every * 1000))
            t.timeout.connect(lambda: self.seek(args.seek_by))
            t.start()
        if args.edit_at:
            QTimer.singleShot(int(args.edit_at * 1000), self.edit)
        if args.quit_after:
            QTimer.singleShot(int(args.quit_after * 1000), self.close)

    # --- live fallback -----------------------------------------------------
    def on_live_loaded(self, ok):
        # As in spike.py: the view's internal quick widget only shows what is
        # under it with WA_AlwaysStackOnTop.
        for child in self.live.findChildren(QWidget):
            child.setAttribute(Qt.WA_AlwaysStackOnTop)
            child.setAttribute(Qt.WA_TranslucentBackground)
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.live_ready = True
        self.place_live()
        self.push_live()
        self.decide(self.shown_frame)

    def on_live_console(self, message):
        if message.startswith('DREW '):
            self.live_drawn = int(message[5:])
            # Waiting for the live view to catch up (after a seek or an edit)?
            f = self.on_screen_frame()
            if not self.live_visible and f not in self.cache.frames:
                self.decide(f)
            return
        if message.startswith('STATS'):
            print(f'[live] {message} {"shown" if self.live_visible else "parked"}', flush=True)

    def push_live(self, ms=None):
        if not self.live_ready:
            return
        if ms is None:
            ms = self.signal_frame * FRAME_MS if self.signal_frame >= 0 else float(self.player.position())
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self.live.page().runJavaScript(
            f'sync({ms:.3f}, {str(playing).lower()}, {self.player.playbackRate()}, {time.time() * 1000:.3f})')

    def place_live(self):
        if not self.live_ready:
            return
        self.live_rect = self.view.mapFromScene(self.video_item.video_rect()).boundingRect()
        if self.live_visible:
            self.live.setGeometry(self.live_rect)
        else:
            self.live.setGeometry(self.live_rect.translated(-20000, 0))
        self.live.raise_()

    def live_current(self, frame):
        """Has the live page drawn something near `frame`? Right after a seek it
        still shows the old position for a few frames; show nothing until then."""
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            lead = round(self.args.lead * self.player.playbackRate() / FRAME_MS)
            return frame - 3 <= self.live_drawn <= frame + lead + 3
        return self.live_drawn == frame

    def decide(self, frame):
        """Put the right overlay source in place for `frame` and, while playing,
        the frame after it: the item may paint that one before we hear of it."""
        self.decided_for = frame
        frames = [frame]
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            frames.append(frame + 1)
        want_live = self.live_ready and any(f not in self.cache.frames for f in frames) and self.live_current(frame)
        if want_live != self.live_visible:
            self.live_visible = want_live
            self.place_live()
        self.video_item.update()

    # --- time --------------------------------------------------------------
    def on_video_frame(self, frame):
        if frame.isValid() and frame.startTime() >= 0:
            self.signal_frame = round(frame.startTime() / 1000 / FRAME_MS)
            if self.seek_target is not None and abs(self.signal_frame - self.seek_target) <= 2:
                self.seek_target = None
        self.push_live()
        f = self.current_video_frame()
        self.decide(f)
        # Counted per decoded frame, so it holds even when the window is not
        # being repainted (covered, other workspace).
        src = 'cache' if not self.live_visible and f in self.cache.frames else 'live' if self.live_visible else 'blank'
        self.decoded[src] += 1
        self.cache.schedule()

    def current_video_frame(self):
        if self.signal_frame >= 0:
            return self.signal_frame
        return round(self.player.position() / FRAME_MS)

    def on_screen_frame(self):
        """The frame the overlay is for: the seek target while seeking."""
        return self.seek_target if self.seek_target is not None else self.shown_frame

    def playhead_frame(self):
        if self.seek_target is not None:
            return self.seek_target
        return max(self.shown_frame, 0) if self.shown_frame >= 0 else round(self.player.position() / FRAME_MS)

    def wait_preroll(self):
        # Start once the first second is cached, like an editor's preroll.
        if self.args.no_preroll or not self.cache.renderers or self.cache.ahead_count(0) >= 25:
            print('playing' if self.args.no_preroll else 'preroll done, playing', flush=True)
            self.player.play()
        else:
            QTimer.singleShot(50, self.wait_preroll)

    # --- transport ---------------------------------------------------------
    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek(self, delta):
        self.seek_to(max(0, self.player.position() + delta))

    def step(self, n):
        self.player.pause()
        self.seek_to(max(0, int((self.playhead_frame() + n) * FRAME_MS)))

    def seek_to(self, ms):
        # A seek jumps past the one-frame lookahead: decide for the target now,
        # and move the live page there while the decoder is still seeking.
        # Point the renderers there too: the first frames at the target are
        # often cached before the decoder delivers it.
        self.seek_target = round(ms / FRAME_MS)
        self.push_live(ms)
        self.decide(self.seek_target)
        self.player.setPosition(int(ms))
        self.cache.schedule()

    def edit(self):
        cue = self.cache.cue_of(self.playhead_frame())
        self.edits += 1
        text = f'Edited {self.edits}× while playing'
        print(f'edit cue {cue} at frame {self.playhead_frame()}', flush=True)
        self.cache.edit_cue(cue, text)

    # --- reporting ---------------------------------------------------------
    def print_stats(self):
        s = self.cache.stats
        new = s['rendered'] - self.last_rendered
        self.last_rendered = s['rendered']
        ms = sorted(s['render_ms'][-200:]) or [0]
        print(f'renders/s {new / 2:.1f}  ahead {self.cache.ahead_count(self.playhead_frame())}  '
              f'cache {s["hits"]} live {s["live"]} wait {s["waits"]} miss {s["misses"]}  wrong {s["wrong"]} stale {s["stale"]}  '
              f'render ms p50 {ms[len(ms) // 2]:.0f}  cached {len(self.cache.frames)}  '
              f'decoded: cache {self.decoded["cache"]} live {self.decoded["live"]} blank {self.decoded["blank"]}', flush=True)

    def snap(self):
        path = self.snap_dir / f'cached_{self.snap_n:03d}.png'
        self.grab().save(str(path))
        self.snap_n += 1

    def closeEvent(self, event):
        # The hidden render views count as open windows; quit explicitly.
        super().closeEvent(event)
        QApplication.quit()

    def fit_video(self):
        size = self.view.viewport().size()
        self.scene.setSceneRect(0, 0, size.width(), size.height())
        self.video_item.setSize(QSizeF(size))
        self.place_live()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('video', nargs='?', default=str(HERE / 'test_25fps.mp4'))
    parser.add_argument('--views', type=int, default=4)
    parser.add_argument('--render-size', default='960x540')
    parser.add_argument('--ahead', type=float, default=4.0, help='seconds to keep rendered ahead of the playhead')
    parser.add_argument('--autoplay', action='store_true')
    parser.add_argument('--no-preroll', action='store_true', help='start playing without waiting for the cache')
    parser.add_argument('--no-live', action='store_true', help='no live fallback: uncached frames draw nothing')
    parser.add_argument('--lead', type=float, default=80, help='ms the live overlay draws ahead while playing')
    parser.add_argument('--seek-every', type=float, help='seek by --seek-by ms every S seconds')
    parser.add_argument('--seek-by', type=int, default=3000, help='ms per automatic seek; negative goes back')
    parser.add_argument('--snap', metavar='DIR')
    parser.add_argument('--snap-every', type=float, default=1.3)
    parser.add_argument('--edit-at', type=float, help='edit the cue under the playhead after S seconds')
    parser.add_argument('--quit-after', type=float)
    args = parser.parse_args()
    app = QApplication(sys.argv)
    w = PlayerWidget(args.video, args)
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
