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

from collections import deque

from PySide6.QtCore import QObject, Qt, QTimer

from subtitld.modules import session
from subtitld.modules.addons import languages as _languages
from subtitld.modules.addons import protocol
from subtitld.modules.addons import registry as _registry
from subtitld.modules.addons.process import AddonProcess
from subtitld.modules.addons.provider import (
    ASRProvider,
    AudioSeparatorProvider,
    TASK_ASR_TRANSCRIBE,
    TASK_ASR_STREAM,
    TASK_AUDIO_SEPARATE,
    TASK_TTS_SYNTHESIZE,
    TASK_TRANSLATE,
    TASK_VIDEO_LIPSYNC,
    TTSProvider,
    TranslationProvider,
    VideoLipsyncProvider,
)
from subtitld.modules import audio_stretch
from subtitld.modules.utils import get_cache_key

log = logging.getLogger(__name__)


def _dubbing_cache_dir() -> Path:
    """Where TTS providers drop generated WAVs. Matches what `EdgeTTSEngine`
    does today so existing playback code stays oblivious to the source."""
    cache_dir = Path(session.PATH_SUBTITLD_USER_CACHE) / 'dubbing'
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _build_addon_env(addon_id: str, options: dict | None) -> dict[str, str]:
    """Overlay per-add-on `Configure` values onto the host's environment so
    the subprocess can read them at startup.

    Convention (already adopted by the reference add-ons, e.g. qwen3-tts):
    addon_id `qwen3-tts` → prefix `QWEN3_TTS_`, key `device` → env var
    `QWEN3_TTS_DEVICE`. The addon entry point reads these with the manifest
    `config_schema` default as fallback, so when the user hasn't edited
    anything the manifest default still applies.

    Why env vars and not request params: most addons load their model once
    at startup (`device`, `model`, `total_steps`...), well before any
    per-request frame arrives. Passing those through env keeps the existing
    addon code shape (`os.environ.get(...)`) and avoids growing the
    protocol with a separate `configure` request.

    Always returns a complete env dict (started from `os.environ`) because
    `subprocess.Popen(env=...)` REPLACES the parent's environment rather
    than extending it — missing PATH/HOME/etc would break the addon's own
    subprocess spawns (ffmpeg, hugging-face hub network calls, etc.).
    """
    env = dict(os.environ)
    if not options:
        return env
    prefix = addon_id.replace('-', '_').upper() + '_'
    for key, value in options.items():
        if value is None or value == '':
            continue
        env_key = prefix + key.upper()
        if isinstance(value, bool):
            env[env_key] = '1' if value else '0'
        elif isinstance(value, (str, int, float)):
            env[env_key] = str(value)
        # Skip anything else: config_schema currently has no list/dict
        # field types, so this branch is unreachable in practice.
    return env


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
            # Snapshot the user's `Configure` values at spawn time. The
            # process keeps those env vars for its entire lifetime; if the
            # user edits options later, they take effect after the next
            # respawn (idle GC, crash, or explicit toggle). This matches
            # the "addons read options at startup only" note in
            # addons_dialog.py — we deliberately don't auto-restart.
            env = _build_addon_env(
                self._addon_id, _registry.options_for(self._addon_id)
            )
            self._process = AddonProcess(
                self._manifest, self._exe_path, env=env
            )
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

        # Serial dispatch queue.
        #
        # The previous implementation fired every request in `text_list`
        # to the addon's stdin in one tight loop. That meant:
        #   * N `AddonRequest` objects alive simultaneously (signal
        #     connections + refs to subtitle dicts), even though the
        #     addon process serializes inference internally and only
        #     works one at a time.
        #   * No back-pressure: a 500-line "generate all" floods stdin,
        #     and the user can't tell the engine to stop after the first
        #     few when something's clearly going wrong.
        #   * Memory grew with batch size for no synthesis-speed benefit.
        #
        # Match the edge-tts shape (sequential within a single worker):
        # `generate_speeches` enqueues, `_drain_next_tts` pops one and
        # sends it, and the result/error of THAT one triggers the next
        # pop. At most one request is in flight at any time. Multiple
        # `generate_speeches` calls just extend the same queue.
        self._tts_queue: deque[tuple[str, dict, str, dict]] = deque()
        self._tts_in_flight: bool = False

    def list_voices(self) -> list[dict]:
        # Most TTS add-ons declare their voices statically in the manifest;
        # nothing dynamic to fetch. Cloud-backed add-ons can override.
        return self._voices_cache

    def shutdown(self) -> None:
        # Drop any queued (not-yet-dispatched) requests. We do NOT emit
        # speech_error for them — at shutdown the slots downstream of
        # speech_error may already be torn down (QApplication going away,
        # session module unloading), and a stray emit could touch dead
        # Qt objects. Items still in the queue had `subtitle['locked']`
        # set by the caller; on next app start the project loads with
        # those flags re-zeroed via the regular load path, so the user
        # doesn't see a frozen-forever locked subtitle.
        self._tts_queue.clear()
        self._tts_in_flight = False
        super().shutdown()

    def generate_speeches(self, text_list: list[dict]) -> None:
        # Pre-compute the per-item request payload up-front so the queue
        # holds finished tuples and `_drain_next_tts` doesn't have to know
        # about the subtitle dict shape. We do NOT call `_ensure_process`
        # here — defer it to drain time so a transient process death
        # between batches doesn't fail-fast the whole queue (each item
        # gets its own _ensure_process attempt).
        cache_dir = _dubbing_cache_dir()
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

            self._tts_queue.append((uid, subtitle, output_path, params))

        # Kick the drain if nothing is currently in flight. If we ARE
        # in flight, the chained signal handler will pop the new items
        # on the next pass — calling _drain_next_tts here would be a
        # no-op anyway (the guard rejects it), but skip the dispatch
        # round-trip.
        if not self._tts_in_flight:
            self._drain_next_tts()

    def _drain_next_tts(self) -> None:
        """Pop one queued request and send it to the addon process. The
        request's result/error signals are wired to re-trigger this method
        when the addon is done with that one item — so at most one
        synthesis is in flight at any time.

        Called on the main thread only: either directly from
        `generate_speeches`, or indirectly via the queued result/error
        signals of the previous in-flight request (which Qt dispatches
        on the main thread). No locking needed.
        """
        if self._tts_in_flight:
            # Defensive: shouldn't be reachable today (the only callers
            # gate on this flag), but keeps the invariant explicit.
            return
        if not self._tts_queue:
            return

        uid, subtitle, output_path, params = self._tts_queue.popleft()

        # `_ensure_process` is the one operation that can fail per-item
        # (the addon binary might be missing / crashed / refusing to
        # restart). On failure, emit the error for this item and
        # immediately try the next — same fail-individually semantics
        # the edge-tts thread has for its per-item exceptions.
        try:
            proc = self._ensure_process()
        except Exception as exc:
            self.speech_error.emit(uid, subtitle, str(exc))
            # Tail call replaced with singleShot(0) to keep the stack
            # flat across long queues. Identical effect, no recursion
            # depth risk on a batch of N hundred.
            QTimer.singleShot(0, self._drain_next_tts)
            return

        req = proc.request(TASK_TTS_SYNTHESIZE, params)
        self._tts_in_flight = True
        self._wire_synthesis(req, uid, subtitle, output_path)

    def _wire_synthesis(self, req, uid: str, subtitle: dict, output_path: str) -> None:
        # The provider lives on the main thread (parented to QApplication),
        # so the signals will be queued across the reader-thread boundary
        # automatically by Qt.
        #
        # Both handlers MUST flip `_tts_in_flight` back to False and pump
        # the next item, regardless of which path fires. We do the
        # bookkeeping in a single `_advance` helper so the result and
        # error branches can't accidentally drift apart (e.g. an early
        # `return` on the empty-file guard used to skip the next-pump,
        # which would have wedged the queue forever).
        def _advance() -> None:
            self._tts_in_flight = False
            # singleShot(0) instead of a direct call: lets Qt finish
            # dispatching all queued slots connected to this same signal
            # (notably `_on_speech_ready`, which mutates session.SUBTITLE
            # and refreshes the timeline) BEFORE we fire the next
            # synthesis. Keeps the per-item visual feedback ordered.
            QTimer.singleShot(0, self._drain_next_tts)

        def on_result(_data: dict) -> None:
            size = os.path.getsize(output_path) if os.path.isfile(output_path) else 0
            if size <= 0:
                self.speech_error.emit(uid, subtitle, f'{self._addon_id} produced an empty audio file')
            else:
                self.speech_ready.emit(uid, subtitle, output_path)
            _advance()

        def on_error(code: str, message: str) -> None:
            self.speech_error.emit(uid, subtitle, f'[{code}] {message}')
            _advance()

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
                # Auto-fit to the subtitle duration if the speaker opted in;
                # keeps the un-stretched original in the dub list. Skipped when
                # the request opted out (a manual-stretch re-render).
                from subtitld.modules import dub_fit
                dub_fit.maybe_fit_dub(subtitle, original_subtitle.get('fit', True))
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

        Re-stretches the cached raw WAV with ffmpeg rather than re-running the
        engine. The raw is kept on disk forever (next to the rendered cache
        files), so the user can drag the rate slider back and forth without
        any quality loss or re-synthesis cost. The edge-tts provider now shares
        the same host-side path (see `dub_clip.restretch_active_dub`).

        Returns True if the dub was updated, False if nothing changed (no
        dub yet, ratio out of bounds, or new rate equal to current).
        """
        # Deterministic host-side atempo stretch (shared with edge-tts). It
        # mutates the active dub in place and syncs the single-subclip cache.
        from subtitld.modules import dub_clip
        if not dub_clip.restretch_active_dub(subtitle, ratio):
            return False

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
        self._stream_request = None
        self._stream_process = None

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

    # ---- live streaming (asr.stream) -----------------------------------
    def supports_streaming(self) -> bool:
        return TASK_ASR_STREAM in (self._manifest.get('tasks') or [])

    def stream_start(self, language: str, options: dict | None = None) -> None:
        """Open an `asr.stream` session. Audio is fed with `stream_feed`;
        segments come back via `stream_segment(seg, is_final)`; the committed
        list via `stream_finished`; failures via `stream_error`."""
        try:
            proc = self._ensure_process()
        except Exception as exc:
            self.stream_error.emit(str(exc))
            return
        self._stream_process = proc
        req = proc.request(TASK_ASR_STREAM, {
            'language': language,
            'options': options or {},
            'samplerate': 16000,
        }, timeout=None)
        self._stream_request = req

        def on_partial(data: dict) -> None:
            if not isinstance(data, dict):
                return
            final = bool(data.get('final', False))
            seg = {k: v for k, v in data.items() if k != 'final'}
            self.stream_segment.emit(seg, final)

        def on_result(data: dict) -> None:
            segments = data.get('segments') if isinstance(data, dict) else None
            self._stream_request = None
            self.stream_finished.emit(segments if isinstance(segments, list) else [])

        def on_error(code: str, message: str) -> None:
            self._stream_request = None
            self.stream_error.emit(f'[{code}] {message}')

        req.partial.connect(on_partial, Qt.QueuedConnection)
        req.result.connect(on_result, Qt.QueuedConnection)
        req.error.connect(on_error, Qt.QueuedConnection)

    def stream_feed(self, pcm_bytes: bytes) -> None:
        req = self._stream_request
        proc = self._stream_process
        if req is None or proc is None or not pcm_bytes:
            return
        import base64
        b64 = base64.b64encode(pcm_bytes).decode('ascii')
        proc.stream_send(req.id, protocol.make_stream_audio(req.id, b64))

    def stream_stop(self) -> None:
        req = self._stream_request
        proc = self._stream_process
        if req is None or proc is None:
            return
        proc.stream_send(req.id, protocol.make_stream_stop(req.id))


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
        # ------------------------------------------------------------
        # On-disk caching contract.
        #
        # The host (`playercontrols.MusicAudioExtractorThread`) names
        # its separation cache files `<hash>_vocals.flac` and
        # `<hash>_background.flac`, where the hash comes from
        # `utils.get_cache_key(input_path)`. The built-in
        # `ffmpeg-separator` writes those exact filenames, so on
        # project reload the host's three-file fast-path
        # (playercontrols.py:224-236) short-circuits the whole
        # pipeline.
        #
        # The subprocess wrapper, however, names its outputs after the
        # model (`<input>_(Vocals)_Kim_Inst.flac`, etc.). If we passed
        # those paths through verbatim, reload would never hit the
        # host fast-path and every project open would re-run the
        # model — wasting seconds-to-minutes per project on no work.
        #
        # Fix: adopt the same on-disk naming as the built-in. (a)
        # short-circuit when canonical files are already on disk; (b)
        # rename the subprocess outputs into place before emitting
        # `separation_ready`. The host fast-path then works
        # uniformly across all separator providers.
        # ------------------------------------------------------------
        cache_key = get_cache_key(input_path) or 'unkeyed'
        canonical_vocals = os.path.join(
            output_dir, f'{cache_key}_vocals.flac')
        canonical_background = os.path.join(
            output_dir, f'{cache_key}_background.flac')

        if (os.path.exists(canonical_vocals)
                and os.path.exists(canonical_background)):
            # Belt-and-braces: the host's three-file fast-path normally
            # catches this case before we're even called, but a partial
            # cache (missing `_original.flac` only) would route through
            # here. Emit synchronously without spinning up the
            # subprocess.
            self.separation_ready.emit(
                str(input_path), canonical_vocals, canonical_background)
            return

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

            # Normalize subprocess outputs to the host's canonical
            # filenames so reloads hit the fast-path. `os.replace`
            # is atomic on the same filesystem (which holds here —
            # both source and destination are in `output_dir`). If
            # the rename fails for any reason, fall back to emitting
            # the original paths so the current request still works.
            final_vocals = vocals
            final_background = background
            try:
                if vocals and os.path.exists(vocals):
                    os.replace(vocals, canonical_vocals)
                    final_vocals = canonical_vocals
                if background and os.path.exists(background):
                    os.replace(background, canonical_background)
                    final_background = canonical_background
            except OSError as exc:
                log.warning(
                    'failed to rename %s output into canonical cache slot '
                    '(%s); falling back to raw paths',
                    self._addon_id, exc,
                )

            self.separation_ready.emit(
                str(input_path), final_vocals, final_background)

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


class AddonVideoLipsyncProvider(_AddonProviderMixin, VideoLipsyncProvider):
    """Generative lip-sync backed by a subprocess add-on (Wav2Lip, MuseTalk).

    Protocol for ``video.lipsync``::

        request:  {video_path, audio_path, output_path, options}
        progress: {value, message}                 # 0..1
        result:   {output_path: <path>}            # exists + non-empty
    """

    def __init__(self, addon_id: str, manifest: dict, exe_path: str | Path,
                 parent: QObject | None = None):
        VideoLipsyncProvider.__init__(self, parent)
        _AddonProviderMixin.__init__(self, addon_id, manifest, exe_path)
        self._active_request = None

    def lipsync(self, request_id: str, video_path: str, audio_path: str,
                output_path: str, options: dict | None = None) -> None:
        try:
            proc = self._ensure_process()
        except Exception as exc:
            self.error.emit(request_id, str(exc))
            return

        params = {
            'video_path': str(video_path),
            'audio_path': str(audio_path),
            'output_path': str(output_path),
            'options': options or {},
        }
        req = proc.request(TASK_VIDEO_LIPSYNC, params)
        self._active_request = req

        def on_progress(value: float, message: str) -> None:
            self.progress.emit(request_id, float(value), str(message))

        def on_result(data: dict) -> None:
            self._active_request = None
            out = str(data.get('output_path', '') or '') if isinstance(data, dict) else ''
            if not out:
                self.error.emit(request_id, f'{self._addon_id} returned no output_path')
                return
            self.lipsync_ready.emit(request_id, out)

        def on_error(code: str, message: str) -> None:
            self._active_request = None
            self.error.emit(request_id, f'[{code}] {message}')

        req.progress.connect(on_progress, Qt.QueuedConnection)
        req.result.connect(on_result, Qt.QueuedConnection)
        req.error.connect(on_error, Qt.QueuedConnection)

    def cancel(self, request_id: str | None = None) -> None:
        if self._active_request is None or self._process is None:
            return
        self._process.cancel(self._active_request.id)
