"""Built-in Edge TTS provider.

This is the historical default TTS engine — Microsoft's online edge-tts
service. It was previously hardcoded inside `left_panel_dubbing.py` as a
static class `EdgeTTSEngine`. We've extracted the engine logic to a proper
`TTSProvider` (QObject + signals) so it sits next to the subprocess add-ons
in the registry.

For backwards compatibility we still expose `EdgeTTSEngine` as a
static-class facade that delegates to the singleton provider — code that
imports `from ...left_panel_dubbing import EdgeTTSEngine` keeps working
during the migration. The inner UI widget classes (`dubbingPanel`,
`speaker_panel`) remain attached by `left_panel_dubbing.py`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess

import edge_tts
from PySide6.QtCore import QObject, QThread, Signal

from subtitld.modules import session
from subtitld.modules.addons import languages as _languages
from subtitld.modules.addons.provider import TTSProvider


# Edge TTS speaks roughly 90 locales (see Microsoft's neural-voice docs).
# Keeping the list hardcoded — rather than deriving from `list_voices()` —
# means the AddonsPanel language filter works on first launch, before the
# voice list has been fetched. Voices add nothing to language coverage:
# every locale below has at least one voice. Maintenance is a once-per-year
# add when Microsoft introduces a new locale; the catalog rarely shrinks.
_EDGE_TTS_LANGUAGES = (
    'af-za', 'am-et', 'ar-ae', 'ar-bh', 'ar-dz', 'ar-eg', 'ar-iq',
    'ar-jo', 'ar-kw', 'ar-lb', 'ar-ly', 'ar-ma', 'ar-om', 'ar-qa',
    'ar-sa', 'ar-sy', 'ar-tn', 'ar-ye', 'az-az', 'bg-bg', 'bn-bd',
    'bn-in', 'bs-ba', 'ca-es', 'cs-cz', 'cy-gb', 'da-dk', 'de-at',
    'de-ch', 'de-de', 'el-gr', 'en-au', 'en-ca', 'en-gb', 'en-hk',
    'en-ie', 'en-in', 'en-ke', 'en-ng', 'en-nz', 'en-ph', 'en-sg',
    'en-tz', 'en-us', 'en-za', 'es-ar', 'es-bo', 'es-cl', 'es-co',
    'es-cr', 'es-cu', 'es-do', 'es-ec', 'es-es', 'es-gq', 'es-gt',
    'es-hn', 'es-mx', 'es-ni', 'es-pa', 'es-pe', 'es-pr', 'es-py',
    'es-sv', 'es-us', 'es-uy', 'es-ve', 'et-ee', 'fa-ir', 'fi-fi',
    'fil-ph', 'fr-be', 'fr-ca', 'fr-ch', 'fr-fr', 'ga-ie', 'gl-es',
    'gu-in', 'he-il', 'hi-in', 'hr-hr', 'hu-hu', 'hy-am', 'id-id',
    'is-is', 'it-it', 'ja-jp', 'jv-id', 'ka-ge', 'kk-kz', 'km-kh',
    'kn-in', 'ko-kr', 'lo-la', 'lt-lt', 'lv-lv', 'mk-mk', 'ml-in',
    'mn-mn', 'mr-in', 'ms-my', 'mt-mt', 'my-mm', 'nb-no', 'ne-np',
    'nl-be', 'nl-nl', 'or-in', 'pa-in', 'pl-pl', 'ps-af', 'pt-br',
    'pt-pt', 'ro-ro', 'ru-ru', 'si-lk', 'sk-sk', 'sl-si', 'so-so',
    'sq-al', 'sr-rs', 'su-id', 'sv-se', 'sw-ke', 'sw-tz', 'ta-in',
    'ta-lk', 'ta-my', 'ta-sg', 'te-in', 'th-th', 'tr-tr', 'uk-ua',
    'ur-in', 'ur-pk', 'uz-uz', 'vi-vn', 'wuu-cn', 'yue-cn', 'zh-cn',
    'zh-hk', 'zh-tw', 'zu-za',
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thread workers (verbatim from old left_panel_dubbing.py with names cleaned up)
# ---------------------------------------------------------------------------
class _EdgeTTSSpeechThread(QThread):
    speech_ready = Signal(str, dict, str)
    speech_error = Signal(str, dict, str)

    def __init__(self, text_list):
        super().__init__()
        self.text_list = text_list

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        cache_dir = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'dubbing')
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)

        for subtitle in self.text_list:
            output_file = os.path.join(cache_dir, f'{subtitle["uid"]}.wav')
            try:
                loop.run_until_complete(self._generate(subtitle, output_file))
                size = os.path.getsize(output_file) if os.path.exists(output_file) else 0
                if size <= 0:
                    if os.path.exists(output_file):
                        try:
                            os.remove(output_file)
                        except OSError:
                            pass
                    self.speech_error.emit(subtitle['uid'], subtitle, 'edge-tts returned empty audio')
                    continue
                self.speech_ready.emit(subtitle['uid'], subtitle, output_file)
            except Exception as e:
                self.speech_error.emit(subtitle['uid'], subtitle, str(e))

        loop.close()

    async def _generate(self, subtitle, final_file):
        speaker = session.SPEAKERS.get(subtitle['speaker'], {}).get('dubbing', {})
        voice = subtitle.get('voice') or speaker.get('voice', '')
        communicate = edge_tts.Communicate(
            text=subtitle['text'],
            voice=voice,
            rate=f'{subtitle.get("rate", 0):+d}%',
            pitch=f'{subtitle.get("pitch", 0):+d}Hz',
        )
        # edge-tts emits MP3 bytes regardless of the destination filename
        # extension. The audio engine reads dubs through libsndfile (via
        # `soundfile`), which can't decode MP3 — playback would be silent
        # for those clips. Save MP3 to a temp file first, then transcode
        # to a real PCM WAV at the requested filename.
        mp3_temp = final_file + '.mp3'
        await communicate.save(mp3_temp)
        if not os.path.isfile(mp3_temp) or os.path.getsize(mp3_temp) <= 0:
            return
        try:
            subprocess.run(
                [
                    session.FFMPEG_EXECUTABLE,
                    '-y', '-loglevel', 'error',
                    '-i', mp3_temp,
                    '-ac', '1',
                    '-ar', '24000',
                    '-c:a', 'pcm_s16le',
                    final_file,
                ],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                startupinfo=session.STARTUPINFO,
            )
        finally:
            try:
                os.remove(mp3_temp)
            except OSError:
                pass


class _EdgeTTSVoicesThread(QThread):
    voice_received = Signal(dict)

    def run(self):
        voices = asyncio.run(edge_tts.list_voices())
        for v in voices:
            self.voice_received.emit(v)


def _online() -> bool:
    """Cheap reachability probe — TCP-handshake to a Microsoft endpoint with
    a 2s timeout. We could call out to `requests`, but that pulls TLS and
    sometimes blocks for 30s+ behind captive portals."""
    try:
        with socket.create_connection(('speech.platform.bing.com', 443), timeout=2.0):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
class EdgeTTSProvider(TTSProvider):
    """Microsoft Edge TTS as a built-in provider.

    Voice list is cached in `session.CONFIG['dubbing']['edge-tts']['voices']`
    (preserved from the legacy code so on-disk configs round-trip).
    """

    PROVIDER_ID = 'edge-tts'

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._speech_threads: list[_EdgeTTSSpeechThread] = []
        self._voices_thread: _EdgeTTSVoicesThread | None = None
        self.speech_ready.connect(self._on_speech_ready)
        self.speech_error.connect(self._on_speech_error)

    # ---- Provider identity ------------------------------------------------
    @property
    def id(self) -> str:  # noqa: A003
        return self.PROVIDER_ID

    @property
    def display_name(self) -> str:
        return 'Edge TTS (online)'

    @property
    def is_builtin(self) -> bool:
        return True

    @property
    def languages(self) -> list[str]:
        return list(_EDGE_TTS_LANGUAGES)

    # ---- Voices -----------------------------------------------------------
    def list_voices(self) -> list[dict]:
        cfg = session.CONFIG.setdefault('dubbing', {}).setdefault('edge-tts', {}).setdefault('voices', {})
        return list(cfg.values())

    def refresh_voices(self) -> None:
        """Pull the voice list from edge-tts and emit `voices_updated` when
        done. Idempotent — does nothing if a fetch is already in flight."""
        if self._voices_thread is not None:
            return

        cfg = session.CONFIG.setdefault('dubbing', {}).setdefault('edge-tts', {}).setdefault('voices', {})

        def on_voice(voice: dict) -> None:
            cfg[voice['ShortName']] = voice

        def on_finished() -> None:
            self._voices_thread = None
            self.voices_updated.emit()

        thread = _EdgeTTSVoicesThread()
        thread.voice_received.connect(on_voice)
        thread.finished.connect(on_finished)
        self._voices_thread = thread
        thread.start()

    # ---- Synthesis --------------------------------------------------------
    def generate_speeches(self, text_list: list[dict]) -> None:
        # Online preflight — emit a single error per item if offline so the
        # UI can show a "you're offline" message instead of a 30s timeout.
        if not _online():
            for subtitle in text_list:
                self.speech_error.emit(
                    subtitle.get('uid', ''), subtitle,
                    'edge-tts: network unavailable (try again when you are online)'
                )
            return

        thread = _EdgeTTSSpeechThread(text_list)
        thread.speech_ready.connect(self.speech_ready)
        thread.speech_error.connect(self.speech_error)
        thread.finished.connect(lambda: self._speech_threads.remove(thread)
                                if thread in self._speech_threads else None)
        self._speech_threads.append(thread)
        thread.start()

    def stretch(self, subtitle: dict, ratio: float) -> bool:
        """Resize the active dub to the dragged width.

        Deterministic host-side ``atempo`` stretch (shared with the add-on
        provider) rather than a cloud re-synthesis. Edge's SSML ``rate`` maps
        non-linearly to output duration, so re-speaking at a computed rate
        rarely landed on the width the user dragged to — a plain time-stretch
        of the existing audio hits it exactly. The original synthesis is kept
        as the raw so repeated stretches never compound quality loss.
        """
        from subtitld.modules import dub_clip
        if not dub_clip.restretch_active_dub(subtitle, ratio):
            return False

        # Same post-process refresh the add-on provider uses: preview audio
        # device picks up the new file and the timeline redraws.
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

    # ---- Host-side post-process callbacks --------------------------------
    # These mirror the old `EdgeTTSEngine._on_speech_ready` / `_on_speech_error`
    # behaviour: they mutate `session.SUBTITLE['segments']` to attach the
    # generated dub at index 0 and refresh the timeline / preview audio.
    def _on_speech_ready(self, uid: str, original_subtitle: dict, file_path: str) -> None:
        # Imports deferred to avoid a hard dep on QApplication being up at
        # provider import time.
        from PySide6.QtWidgets import QApplication
        size = os.path.getsize(file_path) if os.path.isfile(file_path) else 0
        if size <= 0:
            self._on_speech_error(uid, original_subtitle, 'edge-tts produced an empty audio file')
            return
        for subtitle in session.SUBTITLE['segments']:
            if subtitle.get('start') == original_subtitle['start']:
                dubs = subtitle.setdefault('dubbing', [])
                inherited_start = dubs[0].get('start', subtitle['start']) if dubs else subtitle['start']
                dubs.insert(0, {
                    'engine': 'edge-tts',
                    'path': file_path,
                    'start': inherited_start,
                    'end': subtitle['end'],
                    'uid': uid,
                    'voice': original_subtitle.get('voice', ''),
                    'rate': original_subtitle.get('rate', 0),
                    'pitch': original_subtitle.get('pitch', 0),
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
                pending = getattr(timeline_widget, 'dub_stretching', None)
                if pending is not None and pending.get('subtitle', None) is not None:
                    for subtitle in session.SUBTITLE['segments']:
                        if subtitle.get('start') == original_subtitle['start'] and pending['subtitle'] is subtitle:
                            timeline_widget.dub_stretching = None
                            break
                timeline_widget.update()
            session.set_unsaved()
            break

    def _on_speech_error(self, uid: str, original_subtitle: dict, message: str) -> None:
        from PySide6.QtWidgets import QApplication
        log.error('edge-tts: uid=%s %s', uid, message)
        target_start = original_subtitle.get('start') if isinstance(original_subtitle, dict) else None
        for subtitle in session.SUBTITLE.get('segments', []) or []:
            if target_start is not None and subtitle.get('start') == target_start:
                subtitle['locked'] = False
                break
        for window in QApplication.topLevelWidgets():
            timeline_widget = getattr(window, 'timeline_widget', None)
            if timeline_widget is not None:
                timeline_widget.update()


# ---------------------------------------------------------------------------
# Singleton + back-compat shim
# ---------------------------------------------------------------------------
_provider_instance: EdgeTTSProvider | None = None


def get_provider() -> EdgeTTSProvider:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = EdgeTTSProvider()
    return _provider_instance


class _EdgeTTSEngineShim:
    """Static-class shim that mirrors the legacy `EdgeTTSEngine` surface so
    code that imports it directly (e.g. `timeline.py`,
    `left_panel_subtitleslist.py`) keeps working unchanged. Attribute access
    forwards to the singleton provider instance.

    Specifically supports:
        EdgeTTSEngine.signals.speech_ready / .speech_error / .voices_updated
        EdgeTTSEngine.generate_speeches(text_list)
        EdgeTTSEngine.get_voices_list()
        EdgeTTSEngine.stretch(subtitle, ratio)

    The inner UI widget classes (`dubbingPanel`, `speaker_panel`) are
    monkey-patched onto this shim by `left_panel_dubbing.py` after that
    module defines them — preserves the legacy `EdgeTTSEngine.dubbingPanel()`
    call shape.
    """

    @staticmethod
    def generate_speeches(text_list):
        get_provider().generate_speeches(text_list)

    @staticmethod
    def get_voices_list():
        get_provider().refresh_voices()

    @staticmethod
    def stretch(subtitle, ratio):
        return get_provider().stretch(subtitle, ratio)

    # `signals` was a `_EdgeTTSSignals` QObject in the legacy code — callers
    # do `EdgeTTSEngine.signals.speech_ready.connect(...)`. The provider
    # itself exposes those signals at the same names, so we just expose the
    # provider as `signals`.
    class _SignalsProxy:
        @property
        def speech_ready(self):
            return get_provider().speech_ready

        @property
        def speech_error(self):
            return get_provider().speech_error

        @property
        def voices_updated(self):
            return get_provider().voices_updated

    signals = _SignalsProxy()


EdgeTTSEngine = _EdgeTTSEngineShim
