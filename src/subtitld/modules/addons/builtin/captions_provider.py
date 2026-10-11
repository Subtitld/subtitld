"""Built-in "Closed captions in the video" engine.

A video recorded from television, or delivered for it, often carries its
captions: CEA-608 and CEA-708, in its frames or in a track of their own.
This engine reads them into the timeline, as a transcription engine fills
it, so it sits in the same picker; the import panel gives it its own
panel (`_CaptionsPanel`), with the choice of which captions to read.

`transcribe` takes the video's path, not an audio file's, and options
{'track': one of closed_captions.TRACKS}. It reads in a thread, through
ffprobe: `progress` as it goes, then `transcript_finished` with the
subtitles (none when the video carries no captions), or `error`.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from subtitld.modules import closed_captions
from subtitld.modules import session
from subtitld.modules.addons.provider import ASRProvider


class _Reader(QThread):
    read = Signal(list)
    failed = Signal(str)
    moved = Signal(float)

    def __init__(self, path, track):
        super().__init__()
        self.path, self.track = path, track
        self.process = None
        self.stopped = False

    def _started(self, process):
        self.process = process
        if self.stopped:
            process.kill()

    def run(self):
        try:
            segments, _track = closed_captions.from_video(
                self.path, self.track, ffprobe=session.FFPROBE_EXECUTABLE, startupinfo=session.STARTUPINFO,
                progress=self.moved.emit, started=self._started)
        except Exception as error:                   # ffprobe missing, or the video unreadable
            if not self.stopped:
                self.failed.emit(str(error))
            return
        if not self.stopped:
            self.read.emit(segments)

    def stop(self):
        self.stopped = True
        if self.process is not None and self.process.poll() is None:
            self.process.kill()


class CaptionsProvider(ASRProvider):
    """The closed captions a video carries, as subtitles."""

    PROVIDER_ID = 'captions'

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._reader = None

    @property
    def id(self) -> str:  # noqa: A003
        return self.PROVIDER_ID

    @property
    def display_name(self) -> str:
        return 'Closed captions in the video'

    @property
    def is_builtin(self) -> bool:
        return True

    @property
    def languages(self) -> list[str]:
        return []                                    # the captions are in whatever language they are

    @property
    def config_schema(self) -> dict | None:
        return None                                  # the panel offers the choice of captions

    def transcribe(self, audio_path: str, language: str, options: dict | None = None) -> None:
        """Read the captions of the video at `audio_path`."""
        self.cancel()
        track = (options or {}).get('track')
        reader = _Reader(audio_path, track if track in closed_captions.TRACKS else 'auto')
        reader.moved.connect(lambda share: self.progress.emit(share, ''))
        reader.read.connect(self.transcript_finished.emit)
        reader.failed.connect(self.error.emit)
        self._reader = reader
        self.transcript_started.emit()
        reader.start()

    def cancel(self) -> None:
        if self._reader is not None:
            self._reader.stop()
            self._reader.wait(2000)
            self._reader = None


_provider_instance: CaptionsProvider | None = None


def get_provider() -> CaptionsProvider:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = CaptionsProvider()
    return _provider_instance
