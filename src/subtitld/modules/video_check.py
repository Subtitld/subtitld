"""Play each video given for a few seconds with Qt, as the preview does, and
fail unless its picture comes through: a package's check of itself.

    subtitld --video-check VIDEO...

PySide6's own FFmpeg decodes AV1, the format of most YouTube downloads, only
with a GPU's help; the AppImage and the snap replace it with one built with
dav1d, and this is the proof that it took (smoke-appimage.sh). A frozen
Subtitld has no Python to run a script with, hence a command of its own.
"""

import sys

FRAMES = 10                     # a video passes with at least this many frames…
SECONDS = 3                     # …in its first seconds


def main(paths):
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

    app = QGuiApplication(sys.argv[:1])
    failed = []
    for path in paths:
        player, sink = QMediaPlayer(), QVideoSink()
        player.setVideoOutput(sink)
        frames = []
        sink.videoFrameChanged.connect(lambda frame, frames=frames: frames.append(frame.isValid()))
        player.setSource(QUrl.fromLocalFile(path))
        player.play()
        loop = QEventLoop()
        QTimer.singleShot(SECONDS * 1000, loop.quit)
        loop.exec()
        shown = sum(frames)
        print(f'{path}: {shown} frames shown, at {player.position()} of {player.duration()} ms', flush=True)
        if shown < FRAMES:
            failed.append(path)
        player.stop()
    for path in failed:
        print(f'::error::the picture of {path} does not come through', flush=True)
    return 1 if failed or not paths else 0
