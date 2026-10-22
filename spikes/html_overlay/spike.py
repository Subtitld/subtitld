"""Spike: a transparent QWebEngineView over the player's QGraphicsVideoItem,
synced to the frames the video sink shows.

Throwaway code, not part of the app. Run from the repo root:

    .venv/bin/python spikes/html_overlay/spike.py [video] [--snap DIR] [--quit-after S]

Keys: space play/pause · ←/→ seek 1 s · ,/. step one frame (paused)
      1/2/3 rate 0.5/1/2 · m toggle frame/extrapolated clock
      [/] lead -/+10 ms · q quit
"""
import argparse
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QRectF, QSizeF, Qt, QTimer, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsScene, QGraphicsView,
                               QVBoxLayout, QWidget)

HERE = Path(__file__).resolve().parent
FRAME_MS = 40.0


class OverlayPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):
        print(f'[js] {message}', flush=True)


class Spike(QWidget):
    def __init__(self, video, args):
        super().__init__()
        self.args = args
        self.setWindowTitle('HTML overlay spike')
        self.resize(1280, 760)

        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setBackgroundBrush(Qt.black)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.video_item = QGraphicsVideoItem()
        self.scene.addItem(self.video_item)
        self.video_item.nativeSizeChanged.connect(lambda _s: self.place_overlay())

        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video_item)
        self.player.setSource(QUrl.fromLocalFile(str(video)))

        # The overlay: a child of the view, kept over the letterboxed video rect.
        self.overlay = QWebEngineView(self.view)
        self.overlay.setPage(OverlayPage(self.overlay))
        self.overlay.page().setBackgroundColor(Qt.transparent)
        self.overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.overlay.setFocusPolicy(Qt.NoFocus)
        self.overlay.loadFinished.connect(self.on_loaded)
        url = QUrl.fromLocalFile(str(HERE / 'overlay.html'))
        url.setQuery(f'lead={args.lead}')
        self.overlay.load(url)

        self.video_item.videoSink().videoFrameChanged.connect(self.on_frame)
        self.player.playbackStateChanged.connect(lambda _s: self.push(self.last_ms))
        self.last_ms = 0.0

        keys = {
            'Space': self.toggle_play, 'Left': lambda: self.seek(-1000), 'Right': lambda: self.seek(1000),
            ',': lambda: self.step(-1), '.': lambda: self.step(1),
            '1': lambda: self.player.setPlaybackRate(0.5), '2': lambda: self.player.setPlaybackRate(1.0),
            '3': lambda: self.player.setPlaybackRate(2.0),
            'M': lambda: self.overlay.page().runJavaScript('toggleMode()'),
            '[': lambda: self.overlay.page().runJavaScript('nudgeLead(-10)'),
            ']': lambda: self.overlay.page().runJavaScript('nudgeLead(10)'),
            'Q': self.close,
        }
        for key, fn in keys.items():
            QShortcut(QKeySequence(key), self, activated=fn, context=Qt.ApplicationShortcut)

        if args.snap:
            self.snap_dir = Path(args.snap)
            self.snap_dir.mkdir(parents=True, exist_ok=True)
            self.snap_n = 0
            timer = QTimer(self, interval=int(args.snap_every * 1000))
            timer.timeout.connect(self.snap)
            timer.start()
        if args.quit_after:
            QTimer.singleShot(int(args.quit_after * 1000), self.close)

    # --- overlay -----------------------------------------------------------
    def on_loaded(self, ok):
        print(f'overlay loaded: {ok}', flush=True)
        # QWebEngineView draws through an internal QQuickWidget. Only with
        # WA_AlwaysStackOnTop does a translucent QQuickWidget show the widgets
        # under it instead of a cleared background.
        if not self.args.no_stack_on_top:
            for child in self.overlay.findChildren(QWidget):
                child.setAttribute(Qt.WA_AlwaysStackOnTop)
                child.setAttribute(Qt.WA_TranslucentBackground)
                child.setAttribute(Qt.WA_TransparentForMouseEvents)
                print(f'  stack-on-top: {type(child).__name__}', flush=True)
        self.place_overlay()
        self.push(self.last_ms)
        if self.args.autoplay:
            self.player.play()

    def video_rect(self):
        item = self.video_item.boundingRect()
        native = self.video_item.nativeSize()
        if native.width() <= 0 or native.height() <= 0:
            return item
        scale = min(item.width() / native.width(), item.height() / native.height())
        w, h = native.width() * scale, native.height() * scale
        return QRectF(item.x() + (item.width() - w) / 2, item.y() + (item.height() - h) / 2, w, h)

    def place_overlay(self):
        size = self.view.viewport().size()
        self.scene.setSceneRect(0, 0, size.width(), size.height())
        self.video_item.setSize(QSizeF(size))
        rect = self.view.mapFromScene(self.video_rect()).boundingRect()
        self.overlay.setGeometry(rect)
        self.overlay.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.place_overlay()

    # --- sync --------------------------------------------------------------
    def on_frame(self, frame):
        start = frame.startTime()
        self.push(start / 1000.0 if start >= 0 else float(self.player.position()))

    def push(self, ms):
        self.last_ms = ms
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self.overlay.page().runJavaScript(
            f'window.sync && sync({ms:.3f}, {str(playing).lower()}, {self.player.playbackRate()}, {time.time() * 1000:.3f})')

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
        frame = round(self.last_ms / FRAME_MS) + n
        self.player.setPosition(max(0, int(frame * FRAME_MS)))

    def snap(self):
        path = self.snap_dir / f'snap_{self.snap_n:03d}.png'
        self.grab().save(str(path))
        self.snap_n += 1
        print(f'snap {path.name} at host {self.last_ms:.0f} ms', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('video', nargs='?', default=str(HERE / 'test_25fps.mp4'))
    parser.add_argument('--autoplay', action='store_true')
    parser.add_argument('--snap', metavar='DIR', help='save window grabs to DIR')
    parser.add_argument('--snap-every', type=float, default=1.7)
    parser.add_argument('--quit-after', type=float)
    parser.add_argument('--lead', type=float, default=0, help='ms the overlay draws ahead while playing')
    parser.add_argument('--no-stack-on-top', action='store_true',
                        help='skip WA_AlwaysStackOnTop, to see whether transparency works without it')
    args = parser.parse_args()

    print(f'platform: {os.environ.get("QT_QPA_PLATFORM") or "default"}, '
          f'session: {os.environ.get("XDG_SESSION_TYPE")}', flush=True)
    app = QApplication(sys.argv)
    w = Spike(args.video, args)
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
