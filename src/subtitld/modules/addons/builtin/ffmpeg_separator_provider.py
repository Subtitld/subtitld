"""Built-in audio-separator provider — fast ffmpeg mid/side trick.

This is the historical separator that used to live inline in
`playercontrols.MusicAudioExtractorThread`. We've extracted it into a
proper `AudioSeparatorProvider` so it sits next to the addon-backed
separators in the registry.

The technique: stereo mid/side decomposition.
  - vocals     := (L + R) / 2   (center channel — typically vocals)
  - background := L - R         (side channels — typically music)

Pros: zero-shot, runs anywhere ffmpeg runs, finishes in seconds on
multi-minute media.
Cons: any center-panned instrument (kick, bass, snare) bleeds into
vocals; any panned vocal (backing harmonies) leaks into background.
For high-quality separation, install the `audio-separator` add-on
(UVR/MDX/Demucs models).

Provider id: `ffmpeg-separator`.
"""

from __future__ import annotations

import logging
import os
import subprocess

from PySide6.QtCore import QObject, QThread, Signal

from subtitld.modules import session
from subtitld.modules.addons.provider import AudioSeparatorProvider
from subtitld.modules.utils import get_cache_key

log = logging.getLogger(__name__)


# Output sample rate (matches what `MusicAudioExtractorThread` produced
# historically). Fixed at 48 kHz so cached files round-trip to playback
# without resampling.
_OUTPUT_SAMPLE_RATE = 48000


class _FfmpegSeparationThread(QThread):
    """Background runner for a single ffmpeg invocation. Lives long enough
    to emit one terminal signal then ends."""

    progress = Signal(float, str)
    finished_ok = Signal(str, str, str)   # input, vocals, background
    finished_err = Signal(str, str)        # input, message

    def __init__(self, input_path: str, vocals_path: str, background_path: str,
                 original_path: str | None = None):
        super().__init__()
        self.input_path = input_path
        self.vocals_path = vocals_path
        self.background_path = background_path
        self.original_path = original_path

    def run(self) -> None:
        try:
            # 1) Cache an "original audio" decode at 48 kHz — used by
            #    `clone_ref` and the player as the fallback when the
            #    user hasn't asked for separation. Mirrors what
            #    `MusicAudioExtractorThread` did historically; some host
            #    code (e.g. `_audio_source_for_transcription`) relies on
            #    this file existing.
            if self.original_path and not os.path.exists(self.original_path):
                self.progress.emit(0.05, 'Decoding source audio...')
                cmd_orig = [
                    session.FFMPEG_EXECUTABLE,
                    '-hide_banner', '-loglevel', 'error',
                    '-i', self.input_path,
                    '-vn',
                    '-ar', str(_OUTPUT_SAMPLE_RATE), '-y',
                    self.original_path,
                ]
                rc = subprocess.run(
                    cmd_orig,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    startupinfo=session.STARTUPINFO,
                )
                if rc.returncode != 0:
                    self.finished_err.emit(
                        self.input_path,
                        f'ffmpeg decode failed: {rc.stderr.decode("utf-8", "replace")[:200]}',
                    )
                    return

            self.progress.emit(0.4, 'Separating vocals from background...')
            if not (os.path.exists(self.vocals_path)
                    and os.path.exists(self.background_path)):
                cmd = [
                    session.FFMPEG_EXECUTABLE,
                    '-hide_banner', '-loglevel', 'error',
                    '-i', self.input_path, '-y',
                    '-vn',
                    '-filter_complex',
                    (
                        '[0:a]asplit=2[a1][a2];'
                        '[a1]pan=mono|c0=0.5*c0+0.5*c1[vocals];'
                        '[a2]pan=mono|c0=c0-c1[background]'
                    ),
                    # vocals (center)
                    '-map', '[vocals]', '-ac', '1',
                    '-ar', str(_OUTPUT_SAMPLE_RATE),
                    self.vocals_path,
                    # background (sides)
                    '-map', '[background]', '-ac', '1',
                    '-ar', str(_OUTPUT_SAMPLE_RATE),
                    self.background_path,
                ]
                rc = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    startupinfo=session.STARTUPINFO,
                )
                if rc.returncode != 0:
                    self.finished_err.emit(
                        self.input_path,
                        f'ffmpeg separation failed: {rc.stderr.decode("utf-8", "replace")[:200]}',
                    )
                    return

            self.progress.emit(1.0, 'Done')
            self.finished_ok.emit(self.input_path, self.vocals_path, self.background_path)
        except Exception as exc:  # noqa: BLE001 — surface anything to the UI
            log.exception('ffmpeg-separator thread crashed')
            self.finished_err.emit(self.input_path, str(exc))


class FfmpegSeparatorProvider(AudioSeparatorProvider):
    """Fast mid/side ffmpeg-based separator. Always available (ffmpeg is
    a hard dependency of Subtitld)."""

    PROVIDER_ID = 'ffmpeg-separator'

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._threads: list[_FfmpegSeparationThread] = []

    @property
    def id(self) -> str:  # noqa: A003
        return self.PROVIDER_ID

    @property
    def display_name(self) -> str:
        # Surfaced in comboboxes and the addons panel. The "(fast)" hint
        # is intentional — quality is the trade-off vs. addon-backed
        # separators (UVR/MDX/Demucs).
        return 'ffmpeg (fast)'

    @property
    def is_builtin(self) -> bool:
        return True

    def separate(self, input_path: str, output_dir: str,
                 options: dict | None = None) -> None:
        # Honor the historical filename layout under
        # `PATH_SUBTITLD_DATA_AUDIOSEPARATION`: callers that pass
        # `output_dir = PATH_SUBTITLD_DATA_AUDIOSEPARATION` get the
        # legacy `<hash>_vocals.flac` / `<hash>_background.flac`
        # naming back. Other callers get the same scheme but rooted
        # at their chosen dir, which keeps the contract simple.
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            self.separation_error.emit(input_path, f'cannot create output dir: {exc}')
            return

        cache_key = get_cache_key(input_path) or 'unkeyed'
        vocals_path = os.path.join(output_dir, f'{cache_key}_vocals.flac')
        background_path = os.path.join(output_dir, f'{cache_key}_background.flac')
        original_path = os.path.join(output_dir, f'{cache_key}_original.flac')

        # Cache hit — emit immediately on the calling thread. Same
        # semantics as the historical code (which also short-circuited
        # when both files already existed on disk).
        if os.path.exists(vocals_path) and os.path.exists(background_path):
            self.separation_ready.emit(input_path, vocals_path, background_path)
            return

        thread = _FfmpegSeparationThread(
            input_path=input_path,
            vocals_path=vocals_path,
            background_path=background_path,
            original_path=original_path,
        )

        def _on_progress(value: float, message: str) -> None:
            self.progress.emit(value, message)

        def _on_ok(input_p: str, vocals_p: str, background_p: str) -> None:
            self.separation_ready.emit(input_p, vocals_p, background_p)
            if thread in self._threads:
                self._threads.remove(thread)

        def _on_err(input_p: str, message: str) -> None:
            self.separation_error.emit(input_p, message)
            if thread in self._threads:
                self._threads.remove(thread)

        thread.progress.connect(_on_progress)
        thread.finished_ok.connect(_on_ok)
        thread.finished_err.connect(_on_err)
        self._threads.append(thread)
        thread.start()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_provider_instance: FfmpegSeparatorProvider | None = None


def get_provider() -> FfmpegSeparatorProvider:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = FfmpegSeparatorProvider()
    return _provider_instance
