"""Built-in AssemblyAI ASR provider.

Wraps the legacy `AssemblyAIThread` (previously inlined inside
`left_panel_import.py:AssemblyAIPanel.__init__`) as a proper `ASRProvider`.
Requires an API key — read from
`session.CONFIG['transcription']['engine_options']['AssemblyAI']['api_key']`
exactly as the legacy panel did, so existing user configs round-trip.
"""

from __future__ import annotations

import logging
import os
import subprocess

from PySide6.QtCore import QObject, QThread, Signal

from subtitld.modules import session
from subtitld.modules.addons.provider import ASRProvider

log = logging.getLogger(__name__)


# AssemblyAI's Universal model documented supported locales as of 2026-05.
# Where AssemblyAI exposes only a primary language code (e.g. `en`, `pt`) we
# list both the bare tag *and* the common regional variants users will pick
# from the filter dropdown — the language matcher's prefix-of rule then
# accepts either side, so a user filtering "pt-br" matches the addon's "pt"
# entry, and a user filtering "pt" matches "pt-br". (See
# `subtitld.modules.addons.languages.tag_matches` for the rule.)
_ASSEMBLYAI_LANGUAGES = (
    'en', 'en-us', 'en-gb', 'en-au',
    'es', 'es-es', 'es-mx',
    'fr', 'fr-fr', 'fr-ca',
    'de', 'de-de',
    'it', 'it-it',
    'pt', 'pt-br', 'pt-pt',
    'nl', 'nl-nl',
    'hi', 'hi-in',
    'ja', 'ja-jp',
    'zh', 'zh-cn',
    'fi', 'ko', 'pl', 'ru', 'tr', 'uk', 'vi',
    'af', 'ar', 'az', 'be', 'bg', 'bn', 'bs', 'ca', 'cs', 'cy', 'da', 'el',
    'et', 'eu', 'fa', 'gl', 'he', 'hr', 'hu', 'hy', 'id', 'is', 'kk', 'kn',
    'lt', 'lv', 'mk', 'mr', 'ms', 'ne', 'no', 'pa', 'ro', 'sk', 'sl', 'sr',
    'sv', 'sw', 'ta', 'te', 'th', 'tl', 'ur',
)


class _AssemblyAIWorker(QThread):
    progress = Signal(int)
    completed = Signal(list)
    error = Signal(str)

    def __init__(self, audio_file: str, language: str):
        super().__init__()
        self.audio_file = audio_file
        self.language = language

    def run(self):
        try:
            import assemblyai as aai
        except ImportError as exc:
            self.error.emit(f'assemblyai not installed: {exc}')
            return

        api_key = (
            session.CONFIG.get('transcription', {})
            .get('engine_options', {})
            .get('AssemblyAI', {})
            .get('api_key')
        )
        if not api_key:
            self.error.emit('AssemblyAI: API key not configured')
            return
        if not self.audio_file:
            self.error.emit('AssemblyAI: audio_file is required')
            return

        # Re-encode to compact opus first — drastically smaller upload than
        # WAV. Path uses the legacy `vosk_transcribe_*` prefix that this
        # code inherited from the original AssemblyAIPanel implementation;
        # kept as-is so external scripts and users that look at
        # `$TMP/vosk_transcribe_*.opus` for debugging still find the file.
        temp_audio_file = os.path.join(
            session.PATH_TEMP, f'vosk_transcribe_{os.path.basename(self.audio_file)}.opus'
        )

        try:
            subprocess.Popen(
                [
                    session.FFMPEG_EXECUTABLE,
                    '-i', self.audio_file,
                    '-c:a', 'libopus',
                    '-b:a', '24k',
                    '-vbr', 'on',
                    '-compression_level', '10',
                    '-application', 'voip',
                    '-ac', '1',
                    '-ar', '48000',
                    temp_audio_file.replace('.wav', '.opus'),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=session.STARTUPINFO,
            ).wait()

            self.progress.emit(25)

            aai.settings.api_key = api_key
            cfg = aai.TranscriptionConfig(
                speaker_labels=True,
                language_code=self.language[:2] if self.language else 'en',
                punctuate=True,
            )

            transcriber = aai.Transcriber()
            transcript = transcriber.transcribe(f'{temp_audio_file}', config=cfg)

            segments = []
            for sentence in transcript.get_sentences():
                segments.append({
                    'start': float(sentence.start / 1000),
                    'end': float(sentence.end / 1000),
                    'text': sentence.text,
                    'speaker': sentence.speaker,
                })

            self.progress.emit(99)
            self.completed.emit(segments)
        except Exception as exc:
            log.exception('AssemblyAI: transcription failed')
            self.error.emit(str(exc))


class AssemblyAIProvider(ASRProvider):
    """AssemblyAI built-in provider (cloud, requires API key)."""

    PROVIDER_ID = 'assemblyai'

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._worker: _AssemblyAIWorker | None = None

    @property
    def id(self) -> str:  # noqa: A003
        return self.PROVIDER_ID

    @property
    def display_name(self) -> str:
        return 'AssemblyAI (cloud)'

    @property
    def is_builtin(self) -> bool:
        return True

    @property
    def languages(self) -> list[str]:
        return list(_ASSEMBLYAI_LANGUAGES)

    @property
    def config_schema(self) -> dict | None:
        # Single field — the API key. AddonsDialog renders this as a
        # password-masked text edit.
        return {
            'fields': [
                {
                    'key': 'api_key',
                    'type': 'password',
                    'label': 'AssemblyAI API key',
                    'storage': 'session.CONFIG.transcription.engine_options.AssemblyAI.api_key',
                },
            ],
        }

    def transcribe(self, audio_path: str, language: str, options: dict | None = None) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.error.emit('AssemblyAI: a transcription is already in progress')
            return

        worker = _AssemblyAIWorker(audio_path, language)
        self._worker = worker

        worker.started.connect(lambda: self.transcript_started.emit())
        worker.progress.connect(lambda v: self.progress.emit(v / 100.0, ''))
        worker.error.connect(self.error)
        worker.completed.connect(self.transcript_finished)
        worker.finished.connect(self._on_finished)

        worker.start()

    def cancel(self) -> None:
        # AssemblyAI's transcribe is a single sync HTTP roundtrip from the
        # client side; can't cancel mid-call. v0 no-op.
        log.info('assemblyai: cancel requested (no-op — sync HTTP)')

    def _on_finished(self) -> None:
        self._worker = None


_provider_instance: AssemblyAIProvider | None = None


def get_provider() -> AssemblyAIProvider:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = AssemblyAIProvider()
    return _provider_instance
