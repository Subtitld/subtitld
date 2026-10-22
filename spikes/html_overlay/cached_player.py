"""Spike: play with a prerendered HTML overlay drawn in the video item's paint().

Hidden QWebEngineViews render overlay frames ahead of the playhead; the video
item draws the cached overlay for exactly the frame it is painting, so there is
no lead to tune. A frame that is not cached yet draws nothing and counts as a
miss (no live fallback in this test).

    .venv/bin/python spikes/html_overlay/cached_player.py [video] [--views 4] [--render-size 960x540]
                                                          [--ahead 4] [--snap DIR] [--quit-after S]

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
        self.stats = {'rendered': 0, 'wrong': 0, 'stale': 0, 'hits': 0, 'misses': 0, 'render_ms': []}

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
            if frame == self.pw.shown_frame:
                self.pw.video_item.update()    # paused on a frame that just arrived
        self.schedule()

    def edit_cue(self, cue, text):
        self.generation[cue] = self.generation.get(cue, 0) + 1
        for r in self.renderers:
            r.js(f'editCue({cue}, {json.dumps(text)})')
        start, end = int(cue * 2500 / FRAME_MS), int((cue + 1) * 2500 / FRAME_MS)
        for f in [f for f in self.frames if start <= f < end]:
            del self.frames[f]
        self.pw.video_item.update()
        self.schedule()

    def clear(self):
        self.frames.clear()
        self.pw.video_item.update()
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

        crops = pw.cache.frames.get(frame)
        if crops is not None:
            painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform)
            for norm, img in crops:
                target = QRectF(rect.x() + norm.x() * rect.width(), rect.y() + norm.y() * rect.height(),
                                norm.width() * rect.width(), norm.height() * rect.height())
                painter.drawImage(target, img)
        if frame != pw.last_counted:
            pw.last_counted = frame
            pw.cache.stats['hits' if crops is not None else 'misses'] += 1

        # Status, drawn by the host (not part of the overlay).
        head = pw.playhead_frame()
        status = (f'frame {frame}  {"HIT" if crops is not None else "MISS"}  '
                  f'cached ahead {pw.cache.ahead_count(head)}  '
                  f'hits {pw.cache.stats["hits"]}  misses {pw.cache.stats["misses"]}')
        painter.setFont(QFont('DejaVu Sans Mono', 11))
        box = QRectF(rect.right() - 560, rect.bottom() - 32, 552, 24)
        painter.fillRect(box, QColor(0, 0, 0, 170))
        painter.setPen(QColor('#7f7') if crops is not None else QColor('#f55'))
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
        if args.edit_at:
            QTimer.singleShot(int(args.edit_at * 1000), self.edit)
        if args.quit_after:
            QTimer.singleShot(int(args.quit_after * 1000), self.close)

    # --- time --------------------------------------------------------------
    def on_video_frame(self, frame):
        if frame.isValid() and frame.startTime() >= 0:
            self.signal_frame = round(frame.startTime() / 1000 / FRAME_MS)
        self.cache.schedule()

    def current_video_frame(self):
        if self.signal_frame >= 0:
            return self.signal_frame
        return round(self.player.position() / FRAME_MS)

    def playhead_frame(self):
        return max(self.shown_frame, 0) if self.shown_frame >= 0 else round(self.player.position() / FRAME_MS)

    def wait_preroll(self):
        # Start once the first second is cached, like an editor's preroll.
        if not self.cache.renderers or self.cache.ahead_count(0) >= 25:
            print('preroll done, playing', flush=True)
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
        self.player.setPosition(max(0, self.player.position() + delta))

    def step(self, n):
        self.player.pause()
        self.player.setPosition(max(0, int((self.playhead_frame() + n) * FRAME_MS)))

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
              f'hits {s["hits"]} misses {s["misses"]}  wrong {s["wrong"]} stale {s["stale"]}  '
              f'render ms p50 {ms[len(ms) // 2]:.0f}  cached {len(self.cache.frames)}', flush=True)

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('video', nargs='?', default=str(HERE / 'test_25fps.mp4'))
    parser.add_argument('--views', type=int, default=4)
    parser.add_argument('--render-size', default='960x540')
    parser.add_argument('--ahead', type=float, default=4.0, help='seconds to keep rendered ahead of the playhead')
    parser.add_argument('--autoplay', action='store_true')
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
