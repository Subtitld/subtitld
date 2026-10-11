"""Built-in "Subtitles in the video" engine.

A Matroska or MP4 file often carries subtitles of its own: SRT and ASS
tracks, MP4's mov_text, WebM's WebVTT, a track a language. This engine
reads one of them into the timeline, as a transcription engine fills it,
so it sits in the same picker; the import panel gives it its own panel
(`_SubtitleTracksPanel`), with the choice of track, listed from what
opening the video already probed (session.VIDEO['subtitle_tracks']).

`transcribe` takes the video's path, not an audio file's, and options
{'track': the track's stream index, 'codec': its codec, 'duration': the
video's, for progress}. FFmpeg writes the track to a file of its own in
a thread (video_subtitles.extract): `progress` as it goes. The file is
then read as Subtitld opens such a file, on the main thread, since that
reader touches the session: `transcript_finished` with the subtitles, or
`error`. `cancel` stops FFmpeg, and nothing is heard of that run again.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, QThread, Signal, Slot

from subtitld.modules import file_io
from subtitld.modules import session
from subtitld.modules import video_subtitles
from subtitld.modules.addons.provider import ASRProvider


class _Extractor(QThread):
    extracted = Signal(str)
    failed = Signal(str)
    moved = Signal(float)

    def __init__(self, path, index, codec, duration):
        super().__init__()
        self.path, self.index, self.codec, self.duration = path, index, codec, duration
        self.process = None
        self.stopped = False

    def _started(self, process):
        self.process = process
        if self.stopped:
            process.kill()

    def run(self):
        try:
            target = video_subtitles.extract(
                self.path, self.index, self.codec, ffmpeg=session.FFMPEG_EXECUTABLE, folder=session.PATH_TEMP,
                duration=self.duration, startupinfo=session.STARTUPINFO, progress=self.moved.emit,
                started=self._started)
        except Exception as error:                   # FFmpeg missing, the video unreadable, or stopped
            if not self.stopped:
                self.failed.emit(str(error))
            return
        if self.stopped:
            os.remove(target)
            return
        self.extracted.emit(target)

    def stop(self):
        self.stopped = True
        if self.process is not None and self.process.poll() is None:
            self.process.kill()


def read(path):
    """The subtitles in a track's file, read as Subtitld opens such a file;
    the project's format and subtitld.cc origin, which that reader resets,
    are left as they were."""
    if not isinstance(session.FORMAT, dict):
        session.FORMAT = {}
    project_format, kept = session.FORMAT, dict(session.FORMAT)
    origin = session.SUBTITLE.get('origin')
    try:
        return file_io.process_subtitles_file(path)[0]
    finally:
        project_format.clear()
        project_format.update(kept)
        session.FORMAT = project_format
        if origin is not None:
            session.SUBTITLE['origin'] = origin


class SubtitlesProvider(ASRProvider):
    """One of the text subtitle tracks a video carries, as subtitles."""

    PROVIDER_ID = 'subtitles'

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._extractor = None

    @property
    def id(self) -> str:  # noqa: A003
        return self.PROVIDER_ID

    @property
    def display_name(self) -> str:
        return 'Subtitles in the video'

    @property
    def is_builtin(self) -> bool:
        return True

    @property
    def languages(self) -> list[str]:
        return []                                    # each track is in its own language

    @property
    def config_schema(self) -> dict | None:
        return None                                  # the panel offers the choice of track

    def transcribe(self, audio_path: str, language: str, options: dict | None = None) -> None:
        """Read the subtitle track `options['track']` of the video at `audio_path`."""
        self.cancel()
        options = options or {}
        extractor = _Extractor(audio_path, int(options.get('track', 0)), str(options.get('codec') or ''),
                               float(options.get('duration') or 0))
        extractor.moved.connect(lambda share: self.progress.emit(share, ''))
        extractor.extracted.connect(self._read)
        extractor.failed.connect(self.error.emit)
        self._extractor = extractor
        self.transcript_started.emit()
        extractor.start()

    @Slot(str)
    def _read(self, path):
        """The track's file, written: its subtitles on, unless the run was
        cancelled or another began since."""
        try:
            if self.sender() is not self._extractor:
                return
            try:
                segments = read(path)
            except Exception as error:               # a file the reader refuses
                self.error.emit(str(error))
                return
            self.transcript_finished.emit(segments)
        finally:
            if os.path.isfile(path):
                os.remove(path)

    def cancel(self) -> None:
        if self._extractor is not None:
            self._extractor.stop()
            self._extractor.wait(2000)
            self._extractor = None


_provider_instance: SubtitlesProvider | None = None


def get_provider() -> SubtitlesProvider:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = SubtitlesProvider()
    return _provider_instance
