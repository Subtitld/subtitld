"""Generic provider implementations that wrap an `AddonProcess`.

The manager instantiates one of these per discovered add-on. They translate
the high-level provider API (`generate_speeches`, `transcribe`) into protocol
requests and emit the corresponding Qt signals when frames arrive from the
subprocess.

Each subclass is intentionally thin — the heavy lifting lives in
`AddonProcess`. We only do here the per-task semantic adaptation.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt

from subtitld.modules import session
from subtitld.modules.addons import languages as _languages
from subtitld.modules.addons import protocol
from subtitld.modules.addons.process import AddonProcess
from subtitld.modules.addons.provider import (
    ASRProvider,
    AudioSeparatorProvider,
    TASK_ASR_TRANSCRIBE,
    TASK_AUDIO_SEPARATE,
    TASK_TTS_SYNTHESIZE,
    TASK_TRANSLATE,
    TTSProvider,
    TranslationProvider,
)
from subtitld.modules import audio_stretch

log = logging.getLogger(__name__)


def _dubbing_cache_dir() -> Path:
    """Where TTS providers drop generated WAVs. Matches what `EdgeTTSEngine`
    does today so existing playback code stays oblivious to the source."""
    cache_dir = Path(session.PATH_SUBTITLD_USER_CACHE) / 'dubbing'
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


class _AddonProviderMixin:
    """Shared bits between the per-task provider classes."""

    def __init__(self, addon_id: str, manifest: dict, exe_path: str | Path):
        self._addon_id = addon_id
        self._manifest = manifest
        self._exe_path = str(exe_path)
        self._process: AddonProcess | None = None
        # Pre-compute the normalized language tag list once: manifests can
        # declare them in arbitrary case (`pt_BR`, `pt-BR`, `PT-br`), and
        # we want a single canonical form for filter matching. Empty list
        # if the manifest predates the schema field.
        raw_langs = manifest.get('languages') or []
        self._languages = [
            n for n in (_languages.normalize(t) for t in raw_langs) if n
        ]

    @property
    def id(self) -> str:  # noqa: A003
        return self._addon_id

    @property
    def display_name(self) -> str:
        return self._manifest.get('display_name', self._addon_id)

    @property
    def is_builtin(self) -> bool:
        return False

    @property
    def languages(self) -> list[str]:
        return list(self._languages)

    @property
    def config_schema(self) -> dict | None:
        return self._manifest.get('config_schema')

    def _ensure_process(self) -> AddonProcess:
        if self._process is None or not self._process.is_running():
            self._process = AddonProcess(self._manifest, self._exe_path)
            self._process.start()
        return self._process

    def shutdown(self) -> None:
        if self._process is not None:
            self._process.shutdown()
            self._process = None

    def health(self) -> dict:
        if self._process is None:
            return {'state': 'stopped'}
        if self._process.is_running():
            return {'state': 'running', 'pid': self._process._proc.pid if self._process._proc else None}
        return {'state': 'crashed'}


class AddonTTSProvider(_AddonProviderMixin, TTSProvider):
    """TTS implementation backed by a subprocess add-on."""

    def __init__(self, addon_id: str, manifest: dict, exe_path: str | Path,
                 parent: QObject | None = None):
        TTSProvider.__init__(self, parent)
        _AddonProviderMixin.__init__(self, addon_id, manifest, exe_path)
        self._voices_cache: list[dict] = list(manifest.get('voices', []) or [])

        # Mirror the EdgeTTSProvider pattern: connect our own outbound
        # signals to a post-process slot that integrates the generated WAV
        # into `session.SUBTITLE` and refreshes the timeline + audio device.
        # Without this the subtitle stays `locked=True` and the dub file
        # is on disk but never attached to the project.
        self.speech_ready.connect(self._on_speech_ready)
        self.speech_error.connect(self._on_speech_error)

    def list_voices(self) -> list[dict]:
        # Most TTS add-ons declare their voices statically in the manifest;
        # nothing dynamic to fetch. Cloud-backed add-ons can override.
        return self._voices_cache

    def generate_speeches(self, text_list: list[dict]) -> None:
        cache_dir = _dubbing_cache_dir()
        try:
            proc = self._ensure_process()
        except Exception as exc:
            for subtitle in text_list:
                self.speech_error.emit(subtitle.get('uid', ''), subtitle, str(exc))
            return

        for subtitle in text_list:
            uid = subtitle.get('uid') or secrets.token_hex(4)
            output_path = str(cache_dir / f'{uid}.wav')

            params = {
                'text': subtitle.get('text', ''),
                'voice': subtitle.get('voice', ''),
                'language': subtitle.get('language', ''),
                'rate': subtitle.get('rate', 0),
                'pitch': subtitle.get('pitch', 0),
                'output_path': output_path,
            }
            # Per-add-on extras — voice cloning reference audio for XTTS, etc.
            for key in ('voice_ref_audio', 'speaker_emb', 'emotion'):
                if key in subtitle:
                    params[key] = subtitle[key]

            req = proc.request(TASK_TTS_SYNTHESIZE, params)
            self._wire_synthesis(req, uid, subtitle, output_path)

    def _wire_synthesis(self, req, uid: str, subtitle: dict, output_path: str) -> None:
        # The provider lives on the main thread (parented to QApplication),
        # so the signals will be queued across the reader-thread boundary
        # automatically by Qt.
        def on_result(_data: dict) -> None:
            size = os.path.getsize(output_path) if os.path.isfile(output_path) else 0
            if size <= 0:
                self.speech_error.emit(uid, subtitle, f'{self._addon_id} produced an empty audio file')
                return
            self.speech_ready.emit(uid, subtitle, output_path)

        def on_error(code: str, message: str) -> None:
            self.speech_error.emit(uid, subtitle, f'[{code}] {message}')

        req.result.connect(on_result, Qt.QueuedConnection)
        req.error.connect(on_error, Qt.QueuedConnection)

    # ---- Host-side post-process callbacks --------------------------------
    # Mirror EdgeTTSProvider._on_speech_ready / _on_speech_error so the
    # generated WAV gets attached to `subtitle['dubbing']`, the subtitle is
    # unlocked, and the timeline/preview audio device pick up the new dub.
    # The only diff vs. the EdgeTTS version is the `engine` field on the
    # dub entry, which is the addon's id (e.g. `'piper-tts'`).
    def _on_speech_ready(self, uid: str, original_subtitle: dict, file_path: str) -> None:
        from PySide6.QtWidgets import QApplication
        size = os.path.getsize(file_path) if os.path.isfile(file_path) else 0
        if size <= 0:
            self._on_speech_error(uid, original_subtitle, f'{self._addon_id} produced an empty audio file')
            return
        # Local engines (Piper, XTTS, ...) ignore `rate` — they always produce
        # the engine's natural speaking speed. We honor subtitld's rate slider
        # by time-stretching the raw output via ffmpeg to a sibling cache file.
        # The raw is preserved (so the user can move the slider freely without
        # re-synthesizing) and the dub entry stores both paths.
        rate = int(original_subtitle.get('rate', 0) or 0)
        rendered_path = str(audio_stretch.stretch_by_rate(Path(file_path), rate))
        for subtitle in session.SUBTITLE.get('segments', []) or []:
            if subtitle.get('start') == original_subtitle.get('start'):
                dubs = subtitle.setdefault('dubbing', [])
                inherited_start = dubs[0].get('start', subtitle['start']) if dubs else subtitle['start']
                dubs.insert(0, {
                    'engine': self._addon_id,
                    'path': rendered_path,
                    # `raw_path` is the engine's untouched output. Future stretches
                    # always go back to this so quality doesn't degrade through
                    # repeated re-stretches.
                    'raw_path': file_path,
                    'start': inherited_start,
                    'end': subtitle['end'],
                    'uid': uid,
                    'voice': original_subtitle.get('voice', ''),
                    'rate': rate,
                    # Pitch shifting isn't wired for add-on TTS yet — keep it
                    # explicitly off so saved projects don't carry a stale value.
                    'pitch': 0,
                })
                subtitle['locked'] = False
                break
        for window in QApplication.topLevelWidgets():
            preview = getattr(window, 'preview_panel_player', None)
            timeline_widget = getattr(window, 'timeline_widget', None)
            if preview is None and timeline_widget is None:
                continue
            if preview is not None:
                preview._audio_device.sync_subtitle_dubs(session.SUBTITLE['segments'])
            if timeline_widget is not None:
                # Drain any pending dub-stretching state for this subtitle.
                pending = getattr(timeline_widget, 'dub_stretching', None)
                if pending is not None and pending.get('subtitle', None) is not None:
                    for subtitle in session.SUBTITLE['segments']:
                        if subtitle.get('start') == original_subtitle.get('start') and pending['subtitle'] is subtitle:
                            timeline_widget.dub_stretching = None
                            break
                timeline_widget.update()
            session.set_unsaved()
            break

    def _on_speech_error(self, uid: str, original_subtitle: dict, message: str) -> None:
        from PySide6.QtWidgets import QApplication
        log.error('%s: uid=%s %s', self._addon_id, uid, message)
        target_start = original_subtitle.get('start') if isinstance(original_subtitle, dict) else None
        for subtitle in session.SUBTITLE.get('segments', []) or []:
            if target_start is not None and subtitle.get('start') == target_start:
                subtitle['locked'] = False
                break
        for window in QApplication.topLevelWidgets():
            timeline_widget = getattr(window, 'timeline_widget', None)
            if timeline_widget is not None:
                timeline_widget.update()

    def stretch(self, subtitle: dict, ratio: float) -> bool:
        """Re-stretch the existing dub for `subtitle` by `ratio`.

        Unlike `EdgeTTSEngine.stretch` (which re-synthesizes via the cloud),
        local engines don't expose a per-call rate knob. We avoid re-running
        the engine entirely and instead re-stretch the cached raw WAV with
        ffmpeg. The raw is kept on disk forever (next to the rendered cache
        files), so the user can drag the rate slider back and forth without
        any quality loss or re-synthesis cost.

        Returns True if the dub was updated, False if nothing changed (no
        dub yet, ratio out of bounds, or new rate equal to current).
        """
        if ratio <= 0 or not subtitle:
            return False
        dubs = subtitle.get('dubbing') or []
        if not dubs:
            return False
        current_dub = dubs[0]
        # Old projects (or dubs from other engines) may not carry `raw_path`.
        # In that case we treat `path` as the raw — first re-stretch lands a
        # sibling cache file, and subsequent stretches reuse the same raw.
        raw_path = current_dub.get('raw_path') or current_dub.get('path')
        if not raw_path or not Path(raw_path).is_file():
            return False

        speaker_name = subtitle.get('speaker', 'A')
        speaker_dubbing = session.SPEAKERS.get(speaker_name, {}).get('dubbing', {})
        overrides = subtitle.setdefault('dubbing_options', {})
        current_rate = int(current_dub.get('rate', overrides.get('rate', speaker_dubbing.get('rate', 0))) or 0)
        current_speed_pct = 100 + current_rate
        new_rate = int(round(current_speed_pct * ratio - 100))
        new_rate = max(-100, min(100, new_rate))
        if new_rate == current_rate:
            return False

        rendered_path = str(audio_stretch.stretch_by_rate(Path(raw_path), new_rate))
        current_dub['path'] = rendered_path
        current_dub['raw_path'] = str(raw_path)
        current_dub['rate'] = new_rate
        current_dub['pitch'] = 0
        overrides['rate'] = new_rate

        # Mirror the post-process refresh from `_on_speech_ready` so the
        # preview audio device picks up the new file and the timeline redraws.
        from PySide6.QtWidgets import QApplication
        for window in QApplication.topLevelWidgets():
            preview = getattr(window, 'preview_panel_player', None)
            timeline_widget = getattr(window, 'timeline_widget', None)
            if preview is None and timeline_widget is None:
                continue
            if preview is not None:
                preview._audio_device.sync_subtitle_dubs(session.SUBTITLE['segments'])
            if timeline_widget is not None:
                timeline_widget.dub_stretching = None
                timeline_widget.update()
            session.set_unsaved()
            break
        return True


class AddonASRProvider(_AddonProviderMixin, ASRProvider):
    """ASR implementation backed by a subprocess add-on."""

    def __init__(self, addon_id: str, manifest: dict, exe_path: str | Path,
                 parent: QObject | None = None):
        ASRProvider.__init__(self, parent)
        _AddonProviderMixin.__init__(self, addon_id, manifest, exe_path)
        self._active_request = None
        self._partials_buffer: list[dict] = []

    def transcribe(self, audio_path: str, language: str, options: dict | None = None) -> None:
        try:
            proc = self._ensure_process()
        except Exception as exc:
            self.error.emit(str(exc))
            return

        self._partials_buffer = []
        params = {
            'audio_path': str(audio_path),
            'language': language,
            'options': options or {},
        }
        req = proc.request(TASK_ASR_TRANSCRIBE, params)
        self._active_request = req

        self.transcript_started.emit()

        def on_progress(value: float, message: str) -> None:
            self.progress.emit(value, message)

        def on_partial(data: dict) -> None:
            # Add-on may emit either {"segment": {...}} or the segment dict directly.
            seg = data.get('segment', data) if isinstance(data, dict) else {}
            if isinstance(seg, dict):
                self._partials_buffer.append(seg)
                self.partial.emit(seg)

        def on_result(data: dict) -> None:
            segments = data.get('segments') if isinstance(data, dict) else None
            if not isinstance(segments, list):
                segments = list(self._partials_buffer)
            self._active_request = None
            self.transcript_finished.emit(segments)

        def on_error(code: str, message: str) -> None:
            self._active_request = None
            self.error.emit(f'[{code}] {message}')

        req.progress.connect(on_progress, Qt.QueuedConnection)
        req.partial.connect(on_partial, Qt.QueuedConnection)
        req.result.connect(on_result, Qt.QueuedConnection)
        req.error.connect(on_error, Qt.QueuedConnection)

    def cancel(self) -> None:
        if self._active_request is None or self._process is None:
            return
        self._process.cancel(self._active_request.id)


class AddonAudioSeparatorProvider(_AddonProviderMixin, AudioSeparatorProvider):
    """Audio-separator implementation backed by a subprocess add-on.

    The add-on protocol for `audio.separate` is:

        request:
          {input_path, output_dir, options}
        progress:
          {value, message}            # 0..1, free-form message
        result:
          {vocals: <path>, background: <path>}
          # both files must exist and be non-empty by the time the
          # `result` frame lands. The host trusts the paths verbatim
          # and won't second-guess (no validation here).

    `options` is whatever the add-on declares in its manifest
    `config_schema` — for python-audio-separator that's typically a
    `model_filename` string (e.g. `UVR-MDX-NET-Inst_HQ_3.onnx`).
    """

    def __init__(self, addon_id: str, manifest: dict, exe_path: str | Path,
                 parent: QObject | None = None):
        AudioSeparatorProvider.__init__(self, parent)
        _AddonProviderMixin.__init__(self, addon_id, manifest, exe_path)
        self._active_request = None

    def separate(self, input_path: str, output_dir: str,
                 options: dict | None = None) -> None:
        try:
            proc = self._ensure_process()
        except Exception as exc:
            self.separation_error.emit(str(input_path), str(exc))
            return

        os.makedirs(output_dir, exist_ok=True)
        params = {
            'input_path': str(input_path),
            'output_dir': str(output_dir),
            'options': options or {},
        }
        req = proc.request(TASK_AUDIO_SEPARATE, params)
        self._active_request = req

        def on_progress(value: float, message: str) -> None:
            self.progress.emit(value, message)

        def on_result(data: dict) -> None:
            self._active_request = None
            vocals = ''
            background = ''
            if isinstance(data, dict):
                vocals = str(data.get('vocals', '') or '')
                background = str(data.get('background', '') or '')
            if not vocals:
                self.separation_error.emit(str(input_path),
                    f'{self._addon_id} returned no `vocals` path')
                return
            self.separation_ready.emit(str(input_path), vocals, background)

        def on_error(code: str, message: str) -> None:
            self._active_request = None
            self.separation_error.emit(str(input_path), f'[{code}] {message}')

        req.progress.connect(on_progress, Qt.QueuedConnection)
        req.result.connect(on_result, Qt.QueuedConnection)
        req.error.connect(on_error, Qt.QueuedConnection)

    def cancel(self) -> None:
        if self._active_request is None or self._process is None:
            return
        self._process.cancel(self._active_request.id)


class AddonTranslationProvider(_AddonProviderMixin, TranslationProvider):
    """Translation implementation backed by a subprocess add-on. v1+ feature."""

    def __init__(self, addon_id: str, manifest: dict, exe_path: str | Path,
                 parent: QObject | None = None):
        TranslationProvider.__init__(self, parent)
        _AddonProviderMixin.__init__(self, addon_id, manifest, exe_path)

    def translate(self, request_id: str, text: str, source: str, target: str,
                  options: dict | None = None) -> None:
        try:
            proc = self._ensure_process()
        except Exception as exc:
            self.error.emit(request_id, str(exc))
            return

        params = {
            'text': text,
            'source': source,
            'target': target,
            'options': options or {},
        }
        req = proc.request(TASK_TRANSLATE, params)

        def on_result(data: dict) -> None:
            self.translation_ready.emit(request_id, str(data.get('text', '')))

        def on_error(code: str, message: str) -> None:
            self.error.emit(request_id, f'[{code}] {message}')

        req.result.connect(on_result, Qt.QueuedConnection)
        req.error.connect(on_error, Qt.QueuedConnection)
