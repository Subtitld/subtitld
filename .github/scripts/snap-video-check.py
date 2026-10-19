"""Play each video given for a few seconds with Qt, as the preview does, and
fail unless its picture comes through: run inside the snap (smoke-snap.sh).
PySide6's own FFmpeg decodes AV1 only with a GPU's help; the snap replaces it
with one built with dav1d, and this is the proof that it took.

    QT_QPA_PLATFORM=offscreen python3 snap-video-check.py VIDEO...
"""
import sys

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

app = QGuiApplication(sys.argv[:1])
failed = []
for path in sys.argv[1:]:
    player, sink = QMediaPlayer(), QVideoSink()
    player.setVideoOutput(sink)
    frames = []
    sink.videoFrameChanged.connect(lambda frame, frames=frames: frames.append(frame.isValid()))
    player.setSource(QUrl.fromLocalFile(path))
    player.play()
    loop = QEventLoop()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    shown = sum(frames)
    print(f'{path}: {shown} frames shown, at {player.position()} of {player.duration()} ms')
    if shown < 10:
        failed.append(path)
    player.stop()
for path in failed:
    print(f'::error::the picture of {path} does not come through')
sys.exit(1 if failed else 0)
