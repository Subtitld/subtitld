"""Built-in Google TTS (gTTS) provider.

Sibling of ``edge_tts_provider`` — a free, no-API-key, online TTS that
hits Google Translate's undocumented TTS endpoint. Lower quality than
edge-tts but useful as:

  * a reliability fallback when Microsoft changes the edge-tts endpoint
    (it's happened twice in the wild; gTTS and edge-tts hit different
    services so they fail independently),
  * a way to reach a handful of African and Asian languages that
    edge-tts doesn't cover.

Key differences vs. edge-tts:

  * No per-language *voices* in the gTTS-native sense — just language
    × accent TLD pairs (Google routes en+com.au through an Australian
    voice). We expose those pairs as separate entries in the voice
    combobox.
  * Only two speeds: normal and ``slow=True``. Mapped to the existing
    ``rate`` field as ``rate < 0`` → slow, ``rate >= 0`` → normal. The
    ``_GenericTTSSpeakerPanel`` honors ``supports_slow_speech`` and
    surfaces a checkbox automatically.
  * No pitch control.

The synthesis pipeline mirrors edge-tts exactly: gTTS writes MP3 →
ffmpeg transcodes to mono 24 kHz PCM WAV → that path becomes
``subtitle['dubbing'][0]['path']``. The audio engine and the rest of
the dubbing pipeline see the result indistinguishably from an edge-tts
dub.
"""

from __future__ import annotations

import logging
import os
import secrets
import socket
import subprocess

from gtts import gTTS
from PySide6.QtCore import QObject, QThread, Signal

from subtitld.modules import session
from subtitld.modules.addons.provider import TTSProvider


log = logging.getLogger(__name__)


# Static voice catalog: (language_code, tld, human-readable label).
# These are the language/accent combinations gTTS routes through
# distinct Google voices. Languages without accent variants get a single
# entry with the default TLD. Curated rather than fetched because gTTS
# exposes no listing API — Google's TTS endpoint is "best effort" and
# the supported set rarely changes; an annual sync is the most we'd
# ever need.
_GTTS_VOICES: tuple[tuple[str, str, str], ...] = (
    # English — most variants
    ('en', 'com',    'English (US)'),
    ('en', 'co.uk',  'English (UK)'),
    ('en', 'com.au', 'English (Australia)'),
    ('en', 'co.in',  'English (India)'),
    ('en', 'ca',     'English (Canada)'),
    ('en', 'co.za',  'English (South Africa)'),
    ('en', 'ie',     'English (Ireland)'),
    # Spanish
    ('es', 'com',    'Spanish (US)'),
    ('es', 'es',     'Spanish (Spain)'),
    ('es', 'com.mx', 'Spanish (Mexico)'),
    # Portuguese
    ('pt', 'com.br', 'Portuguese (Brazil)'),
    ('pt', 'pt',     'Portuguese (Portugal)'),
    # French
    ('fr', 'fr',     'French (France)'),
    ('fr', 'ca',     'French (Canada)'),
    # Single-locale languages — one voice each, default TLD.
    ('de', 'com', 'German'),
    ('it', 'com', 'Italian'),
    ('nl', 'com', 'Dutch'),
    ('pl', 'com', 'Polish'),
    ('ru', 'com', 'Russian'),
    ('tr', 'com', 'Turkish'),
    ('uk', 'com', 'Ukrainian'),
    ('cs', 'com', 'Czech'),
    ('sk', 'com', 'Slovak'),
    ('hu', 'com', 'Hungarian'),
    ('ro', 'com', 'Romanian'),
    ('da', 'com', 'Danish'),
    ('sv', 'com', 'Swedish'),
    ('no', 'com', 'Norwegian'),
    ('fi', 'com', 'Finnish'),
    ('el', 'com', 'Greek'),
    ('bg', 'com', 'Bulgarian'),
    ('hr', 'com', 'Croatian'),
    ('sr', 'com', 'Serbian'),
    ('sl', 'com', 'Slovenian'),
    ('et', 'com', 'Estonian'),
    ('lv', 'com', 'Latvian'),
    ('lt', 'com', 'Lithuanian'),
    ('ca', 'com', 'Catalan'),
    ('eu', 'com', 'Basque'),
    ('gl', 'com', 'Galician'),
    ('is', 'com', 'Icelandic'),
    ('cy', 'com', 'Welsh'),
    ('ga', 'com', 'Irish'),
    ('sq', 'com', 'Albanian'),
    ('mk', 'com', 'Macedonian'),
    ('bs', 'com', 'Bosnian'),
    ('hi', 'com', 'Hindi'),
    ('bn', 'com', 'Bengali'),
    ('ta', 'com', 'Tamil'),
    ('te', 'com', 'Telugu'),
    ('kn', 'com', 'Kannada'),
    ('ml', 'com', 'Malayalam'),
    ('mr', 'com', 'Marathi'),
    ('gu', 'com', 'Gujarati'),
    ('pa', 'com', 'Punjabi'),
    ('ur', 'com', 'Urdu'),
    ('ne', 'com', 'Nepali'),
    ('si', 'com', 'Sinhala'),
    ('ja', 'com', 'Japanese'),
    ('ko', 'com', 'Korean'),
    ('zh-CN', 'com', 'Chinese (Mandarin)'),
    ('zh-TW', 'com', 'Chinese (Taiwan)'),
    ('vi', 'com', 'Vietnamese'),
    ('th', 'com', 'Thai'),
    ('id', 'com', 'Indonesian'),
    ('ms', 'com', 'Malay'),
    ('jw', 'com', 'Javanese'),
    ('su', 'com', 'Sundanese'),
    ('km', 'com', 'Khmer'),
    ('my', 'com', 'Burmese'),
    ('ar', 'com', 'Arabic'),
    ('he', 'com', 'Hebrew'),
    ('fa', 'com', 'Persian'),
    ('hy', 'com', 'Armenian'),
    ('af', 'com', 'Afrikaans'),
    ('sw', 'com', 'Swahili'),
    ('zu', 'com', 'Zulu'),
    ('lat', 'com', 'Latin'),
    ('eo', 'com', 'Esperanto'),
)


def _voice_id(lang: str, tld: str) -> str:
    """Stable id for the (lang, tld) pair. Pipe-separated because both
    lang codes (``zh-CN``) and TLDs (``com.au``) can contain dots/dashes
    but neither contains a pipe."""
    return f'{lang}|{tld}'


def _parse_voice_id(voice_id: str) -> tuple[str, str]:
    """Inverse of _voice_id. Returns (lang, tld) or ('en', 'com') if the
    id is malformed (shouldn't happen, but guards against project files
    that hand-edit dubbing config)."""
    if '|' in voice_id:
        lang, tld = voice_id.split('|', 1)
        return lang, tld
    return 'en', 'com'


# ---------------------------------------------------------------------------
# Thread workers
# ---------------------------------------------------------------------------
class _GTTSSpeechThread(QThread):
    speech_ready = Signal(str, dict, str)
    speech_error = Signal(str, dict, str)

    def __init__(self, text_list):
        super().__init__()
        self.text_list = text_list

    def run(self):
        cache_dir = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'dubbing')
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)

        for subtitle in self.text_list:
            output_file = os.path.join(cache_dir, f'{subtitle["uid"]}.wav')
            try:
                self._generate(subtitle, output_file)
                size = os.path.getsize(output_file) if os.path.exists(output_file) else 0
                if size <= 0:
                    if os.path.exists(output_file):
                        try:
                            os.remove(output_file)
                        except OSError:
                            pass
                    self.speech_error.emit(
                        subtitle['uid'], subtitle, 'gTTS returned empty audio'
                    )
                    continue
                self.speech_ready.emit(subtitle['uid'], subtitle, output_file)
            except Exception as e:
                self.speech_error.emit(subtitle['uid'], subtitle, str(e))

    def _generate(self, subtitle, final_file):
        speaker = session.SPEAKERS.get(subtitle['speaker'], {}).get('dubbing', {})
        voice = subtitle.get('voice') or speaker.get('voice', '') or 'en|com'
        lang, tld = _parse_voice_id(voice)
        # Negative rate flags slow speech; gTTS has no continuous rate.
        slow = int(subtitle.get('rate', 0) or 0) < 0
        tts = gTTS(text=subtitle['text'], lang=lang, tld=tld, slow=slow)
        # gTTS emits MP3 only — same problem as edge-tts. Save MP3 to a
        # temp file then transcode to PCM WAV; libsndfile can't decode
        # MP3, so playback would be silent without this step.
        mp3_temp = final_file + '.mp3'
        tts.save(mp3_temp)
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


def _online() -> bool:
    """Cheap reachability probe — TCP-handshake to translate.google.com
    with a 2s timeout. Matches edge-tts's preflight pattern."""
    try:
        with socket.create_connection(('translate.google.com', 443), timeout=2.0):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
class GTTSProvider(TTSProvider):
    """Google TTS (gTTS) as a built-in provider.

    Voice list is static (gTTS exposes no listing API; the supported
    set rarely changes). Slow speech is the only synthesis knob —
    surfaced as a checkbox via ``supports_slow_speech`` on the
    generic speaker panel. No pitch.
    """

    PROVIDER_ID = 'gtts'
    # Tells _GenericTTSSpeakerPanel to add a slow-speech checkbox that
    # stores its state in speaker.dubbing.rate (-1 for slow, 0 for
    # normal). The synthesis path reads the same field, so no extra
    # plumbing is needed.
    supports_slow_speech = True

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._speech_threads: list[_GTTSSpeechThread] = []
        self.speech_ready.connect(self._on_speech_ready)
        self.speech_error.connect(self._on_speech_error)

    # ---- Provider identity -----------------------------------------------
    @property
    def id(self) -> str:  # noqa: A003
        return self.PROVIDER_ID

    @property
    def display_name(self) -> str:
        return 'Google TTS (gTTS)'

    @property
    def is_builtin(self) -> bool:
        return True

    @property
    def languages(self) -> list[str]:
        # De-duplicate the language column from _GTTS_VOICES.
        return sorted({lang for lang, _tld, _label in _GTTS_VOICES})

    # ---- Voices ----------------------------------------------------------
    def list_voices(self) -> list[dict]:
        return [
            {
                'id': _voice_id(lang, tld),
                'display_name': label,
                'lang': lang,
                'tld': tld,
            }
            for lang, tld, label in _GTTS_VOICES
        ]

    def refresh_voices(self) -> None:
        """No-op — voice list is static. Emit so the UI re-syncs in
        case the signal is bound to a redraw."""
        self.voices_updated.emit()

    # ---- Synthesis -------------------------------------------------------
    def generate_speeches(self, text_list: list[dict]) -> None:
        if not _online():
            for subtitle in text_list:
                self.speech_error.emit(
                    subtitle.get('uid', ''), subtitle,
                    'gTTS: network unavailable (try again when you are online)'
                )
            return

        thread = _GTTSSpeechThread(text_list)
        thread.speech_ready.connect(self.speech_ready)
        thread.speech_error.connect(self.speech_error)
        thread.finished.connect(lambda: self._speech_threads.remove(thread)
                                if thread in self._speech_threads else None)
        self._speech_threads.append(thread)
        thread.start()

    def stretch(self, subtitle: dict, ratio: float) -> bool:
        # gTTS has only two speeds. We can't honor an arbitrary ratio,
        # so refuse the request and let the host fall back to its
        # post-render time-stretch path. Returning False is the
        # documented "I can't natively stretch — please stretch the
        # rendered audio instead" signal.
        return False

    # ---- Host-side post-process callbacks --------------------------------
    # Identical shape to EdgeTTSProvider — attach the WAV at
    # subtitle['dubbing'][0] and refresh timeline + preview audio.
    def _on_speech_ready(self, uid: str, original_subtitle: dict, file_path: str) -> None:
        from PySide6.QtWidgets import QApplication
        size = os.path.getsize(file_path) if os.path.isfile(file_path) else 0
        if size <= 0:
            self._on_speech_error(uid, original_subtitle, 'gTTS produced an empty audio file')
            return
        for subtitle in session.SUBTITLE['segments']:
            if subtitle.get('start') == original_subtitle['start']:
                dubs = subtitle.setdefault('dubbing', [])
                inherited_start = dubs[0].get('start', subtitle['start']) if dubs else subtitle['start']
                dubs.insert(0, {
                    'engine': 'gtts',
                    'path': file_path,
                    'start': inherited_start,
                    'end': subtitle['end'],
                    'uid': uid,
                    'voice': original_subtitle.get('voice', ''),
                    'rate': original_subtitle.get('rate', 0),
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
                timeline_widget.update()
            session.set_unsaved()
            break

    def _on_speech_error(self, uid: str, original_subtitle: dict, message: str) -> None:
        from PySide6.QtWidgets import QApplication
        log.error('gtts: uid=%s %s', uid, message)
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
# Singleton
# ---------------------------------------------------------------------------
_provider_instance: GTTSProvider | None = None


def get_provider() -> GTTSProvider:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = GTTSProvider()
    return _provider_instance
