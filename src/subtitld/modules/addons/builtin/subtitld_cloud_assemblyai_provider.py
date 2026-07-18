"""Subtitld Cloud-routed AssemblyAI provider.

Looks like a plain "AssemblyAI" engine in the desktop's transcription
picker but routes all traffic through the user's Subtitld Cloud account
instead of asking them for a direct AssemblyAI API key. Pricing margin
is applied server-side; users see one bill (Subtitld Cloud) regardless
of which underlying provider they pick.

Pattern: one of these per upstream brand. ElevenLabs will get a
sibling ``subtitld_cloud_elevenlabs_provider.py``, Replicate a sibling
``subtitld_cloud_replicate_provider.py``, and so on — each filters
``/api/v1/catalog`` by its own ``?provider=`` value but reuses the
shared HTTP + auth helpers in ``subtitld_cloud_shared.py``.

Backward-compat rules this file follows (see the contract in
``subtitld_cloud_shared.py``'s module docstring):

  - Catalog rows are pass-through dicts. We don't validate them, so the
    cloud can add new ``metadata`` keys without breaking old desktops.
  - Job-status fields are read with ``.get(..., default)``. New optional
    response fields are ignored; missing ones don't crash.
  - The submit payload only includes fields the cloud has supported
    since v1 (``model``, ``audio_url``, ``language``). New optional
    fields for future features arrive in a desktop release, not a fix.
"""

from __future__ import annotations

import logging
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from subtitld.modules import session
from subtitld.modules.addons import registry
from subtitld.modules.addons.builtin import subtitld_cloud_shared as cloud
from subtitld.modules.addons.provider import ASRProvider

log = logging.getLogger(__name__)

# The cloud's provider id we filter the catalog by. Renaming this
# would break catalog lookups; it's part of the cloud's stable ID
# format ``subtitld-cloud:assemblyai/<native_id>``.
UPSTREAM_PROVIDER_ID = 'assemblyai'

# Languages this provider can transcribe — sourced from AssemblyAI's
# documented Universal-model locale support as of 2026-05. The list
# keeps the AddonsPanel language filter useful even before the user
# configures their API key (so the catalog refresh hasn't run yet).
#
# Where AssemblyAI exposes only a primary language code (e.g. `en`,
# `pt`) we list both the bare tag *and* the common regional variants
# users will pick from the filter dropdown — the language matcher's
# prefix-of rule then accepts either side. (See
# `subtitld.modules.addons.languages.tag_matches` for the rule.)
#
# When the cloud later exposes per-model language metadata via
# ``CatalogItem.languages``, ``refresh_catalog()`` populates the live
# list — this static fallback is only used before that fetch happens.
_STATIC_LANGUAGES = (
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

# Poll interval + total timeout for /api/v1/jobs/<id> polling.
_POLL_INTERVAL_MS = 2_000
_POLL_TIMEOUT_MS = 15 * 60 * 1_000  # 15 minutes

# Word-grouping thresholds for the deep fallback path (only fires when
# the cloud surfaces NEITHER sentences nor utterances — i.e. nothing
# but a flat ``words[]`` array). The legacy direct-AssemblyAI plugin
# delegated entirely to ``transcript.get_sentences()``; we mirror that
# by preferring server-side sentence segmentation when available and
# treating client-side word grouping as defensive runaway protection
# only — no punctuation detection (the user explicitly didn't want
# desktop-side punctuation-aware slicing; matches the old plugin's
# "trust the server" philosophy).
_WORD_GAP_BREAK_SEC = 0.6
# Hard runaway caps. Picked to swallow long complex sentences without
# forcing a mid-thought break: ~500 chars is ~3 lines of dense text,
# 30 s exceeds any natural single sentence by a wide margin.
_PHRASE_HARD_MAX_CHARS = 500
_PHRASE_HARD_MAX_DURATION_SEC = 30.0


# ---------------------------------------------------------------------------
# Response → segments translation
# ---------------------------------------------------------------------------
#
# The cloud's ``/api/v1/jobs/<id>`` completion payload relays from
# AssemblyAI. We prefer the richest server-side segmentation available
# and never re-segment client-side by punctuation (the legacy plugin's
# behavior — it used AssemblyAI's own ``get_sentences()`` and trusted
# it verbatim, which matched user expectation perfectly):
#
#   - Best: ``sentences`` present (from AssemblyAI's
#     ``/v2/transcript/<id>/sentences`` endpoint) → one cue per
#     sentence, decided server-side. The slice_by_phrase toggle picks
#     between "one cue per sentence" (on) and "one cue per speaker
#     turn" (off — merge consecutive same-speaker sentences).
#   - Next: ``utterances`` present (speaker turns) → one cue per turn.
#     The slice toggle is a no-op here — without server-side sentence
#     data we don't try to slice (no client-side punctuation
#     detection).
#   - Fallback: only ``words`` present → group by pause / runaway only
#     (no punctuation detection), to avoid one-cue-per-word.
#   - Last resort: just ``text`` (± ``audio_duration``) → a single
#     whole-audio cue so the user has SOMETHING to edit.
#
# Times are normalized to seconds in the output. AssemblyAI's native
# units are milliseconds, but the cloud may have already converted —
# we sniff via the ``audio_duration`` field rather than hard-code.


def _detect_ms_divisor(sample_value: float, audio_duration_sec: float) -> float:
    """Return 1000.0 if ``sample_value`` is likely milliseconds, else 1.0.

    Heuristic: if we know the audio duration AND a sample time value is
    more than 10× that duration, the value is in ms — there's no
    realistic interpretation where a single cue's end time exceeds the
    whole audio's duration in the same unit. Without a known duration,
    fall back to "values larger than 24 hours of seconds are ms" which
    correctly handles anything up to a 24-hour recording.
    """
    if audio_duration_sec > 0 and sample_value > audio_duration_sec * 10:
        return 1000.0
    if sample_value > 86_400:  # > 24 hours in seconds → must be ms
        return 1000.0
    return 1.0


def _segments_from_sentences(sentences: list, audio_duration_sec: float) -> list[dict]:
    """Translate AssemblyAI's server-side ``sentences[]`` directly.

    This is the preferred path — it mirrors the legacy direct-plugin's
    use of ``transcript.get_sentences()``. Each upstream sentence
    becomes one cue, with no client-side punctuation detection or
    re-grouping. Trusting the server's segmentation is the whole point
    of this path.
    """
    sample = max((s.get('end', 0) or 0 for s in sentences), default=0)
    divisor = _detect_ms_divisor(float(sample), audio_duration_sec)
    out: list[dict] = []
    for s in sentences:
        text = (s.get('text') or '').strip()
        if not text:
            continue
        out.append({
            'start': float(s.get('start', 0) or 0) / divisor,
            'end': float(s.get('end', 0) or 0) / divisor,
            'text': text,
            # AssemblyAI puts speaker on each sentence when diarization
            # is enabled; missing / null when it isn't.
            'speaker': str(s.get('speaker', '') or ''),
        })
    return out


def _merge_consecutive_speakers(segments: list[dict]) -> list[dict]:
    """Collapse consecutive segments with the same speaker into one cue
    each — used when the user toggles slice-by-phrase OFF and we want
    one cue per speaker TURN rather than per sentence.

    Empty-string speakers (no diarization) all match each other, so
    a transcript without speaker labels collapses to a single cue
    per contiguous-text run.
    """
    if not segments:
        return []
    out: list[dict] = []
    current = dict(segments[0])
    for seg in segments[1:]:
        if seg.get('speaker', '') == current.get('speaker', ''):
            current['end'] = seg.get('end', current['end'])
            current['text'] = f"{current['text']} {seg.get('text', '')}".strip()
        else:
            out.append(current)
            current = dict(seg)
    out.append(current)
    return out


def _segments_from_utterances(
    utterances: list,
    audio_duration_sec: float,
) -> list[dict]:
    """Translate the ``utterances`` array — one cue per speaker turn.

    No re-slicing happens here. When the user wants sentence-grained
    cues, the cloud must surface a ``sentences`` field (see
    ``_segments_from_sentences``); without it, we trust the upstream
    segmentation and emit utterances verbatim.
    """
    sample = max((u.get('end', 0) or 0 for u in utterances), default=0)
    divisor = _detect_ms_divisor(float(sample), audio_duration_sec)
    out: list[dict] = []
    for u in utterances:
        text = (u.get('text') or '').strip()
        if not text:
            continue
        # Speaker labels from AssemblyAI are single letters (A/B/C…)
        # which match the rest of the app's convention. Empty string
        # when not provided.
        out.append({
            'start': float(u.get('start', 0) or 0) / divisor,
            'end': float(u.get('end', 0) or 0) / divisor,
            'text': text,
            'speaker': str(u.get('speaker', '') or ''),
        })
    return out


def _segments_from_words(words: list, audio_duration_sec: float) -> list[dict]:
    """Deep fallback — fires only when the cloud surfaces NEITHER
    sentences nor utterances. Groups by pause and runaway caps only;
    NO punctuation detection (we don't try to be smarter than the
    server). One cue per word would be unusable, so this is the
    minimum-viable grouping.
    """
    if not words:
        return []
    sample = max((w.get('end', 0) or 0 for w in words), default=0)
    divisor = _detect_ms_divisor(float(sample), audio_duration_sec)

    out: list[dict] = []
    current: dict | None = None

    for w in words:
        text = (w.get('text') or '').strip()
        if not text:
            continue
        start = float(w.get('start', 0) or 0) / divisor
        end = float(w.get('end', 0) or 0) / divisor
        speaker = str(w.get('speaker', '') or '')

        if current is None:
            current = {'start': start, 'end': end, 'text': text, 'speaker': speaker}
            continue

        # Speaker change.
        if speaker and current['speaker'] and speaker != current['speaker']:
            out.append(current)
            current = {'start': start, 'end': end, 'text': text, 'speaker': speaker}
            continue

        # Long pause.
        gap = start - current['end']
        if gap >= _WORD_GAP_BREAK_SEC:
            out.append(current)
            current = {'start': start, 'end': end, 'text': text, 'speaker': speaker}
            continue

        # Runaway protection only — these caps sit well above natural
        # sentence lengths so they don't fire on normal speech.
        prospective_len = len(current['text']) + 1 + len(text)
        prospective_dur = end - current['start']
        if (
            prospective_len > _PHRASE_HARD_MAX_CHARS
            or prospective_dur > _PHRASE_HARD_MAX_DURATION_SEC
        ):
            out.append(current)
            current = {'start': start, 'end': end, 'text': text, 'speaker': speaker}
            continue

        # Otherwise append to the running group.
        current['text'] += ' ' + text
        current['end'] = end
        if not current['speaker'] and speaker:
            current['speaker'] = speaker

    if current is not None:
        out.append(current)
    return out


def _segments_from_job(
    data: dict,
    *,
    fallback_duration_sec: float = 0.0,
    slice_by_phrase: bool = False,
) -> list[dict]:
    """Top-level: pick the richest available shape, fall back gracefully.

    ``fallback_duration_sec`` is the locally-known audio duration (read
    from ``session.VIDEO['duration']`` at submit time). Used when the
    cloud's response omits ``audio_duration`` so the whole-audio
    fallback cue still lands with a real end time — without it, the
    cue would draw at zero width on the timeline and (worse) divide-
    by-zero any panel that reads cue durations.

    ``slice_by_phrase`` semantics:

      * With ``sentences`` available — True emits one cue per sentence
        (sentence-grained, the legacy plugin's behavior); False merges
        consecutive same-speaker sentences into one cue per speaker
        TURN (coarser grain).
      * With ``utterances`` only — toggle is a no-op (one cue per
        speaker turn either way). Without server-side sentence data we
        don't slice client-side: the desktop never does its own
        punctuation detection, matching the old direct plugin which
        delegated entirely to AssemblyAI's own segmentation.
      * With ``words`` only — toggle is a no-op (pause/runaway
        grouping is the only viable grouping without higher-level
        segmentation).

    Always returns at least one segment when ``data`` has any text at
    all (degrading to a whole-audio cue rather than empty so the user
    sees SOMETHING land in the editor) — but returns ``[]`` for a
    truly empty response so the import view can surface that cleanly.
    """
    audio_duration_sec = float(data.get('audio_duration') or 0.0)
    # If the duration field itself looks like ms, normalize.
    if audio_duration_sec > 86_400:
        audio_duration_sec = audio_duration_sec / 1000.0
    # If the cloud didn't surface a duration at all, use the local
    # fallback so the whole-audio cue still has non-zero length.
    if audio_duration_sec <= 0 and fallback_duration_sec > 0:
        audio_duration_sec = fallback_duration_sec

    # Best path — cloud surfaced AssemblyAI's sentence segmentation.
    sentences = data.get('sentences') or []
    if sentences:
        out = _segments_from_sentences(sentences, audio_duration_sec)
        if out:
            return out if slice_by_phrase else _merge_consecutive_speakers(out)

    utterances = data.get('utterances') or []
    if utterances:
        # No sentence data — emit one cue per speaker turn verbatim.
        # The slice_by_phrase toggle is a no-op (see docstring).
        out = _segments_from_utterances(utterances, audio_duration_sec)
        if out:
            return out

    words = data.get('words') or []
    if words:
        out = _segments_from_words(words, audio_duration_sec)
        if out:
            return out

    # Whole-audio fallback. End-time guarantees the cue draws on the
    # timeline — the speakers panel also reads from this, so a non-
    # zero duration is required to compute speaker percentages.
    text = (data.get('text') or '').strip()
    if not text:
        return []
    return [{
        'start': 0.0,
        'end': audio_duration_sec,
        'text': text,
        'speaker': '',
    }]


# ---------------------------------------------------------------------------
# Worker: opus encode + submit. Runs in a QThread so the UI stays responsive.
# ---------------------------------------------------------------------------


class _SubmitWorker(QThread):
    submitted = Signal(str)         # external job id (uuid)
    error = Signal(str)
    progress = Signal(int)          # 0..100

    def __init__(
        self,
        *,
        audio_path: str,
        language: str,
        model_public_id: str,
        base_url: str,
        api_key: str,
        speaker_labels: bool,
    ):
        super().__init__()
        self.audio_path = audio_path
        self.language = language
        self.model_public_id = model_public_id
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        # Mirrors AssemblyAI's native field name so the cloud relay can
        # pass it through verbatim — see `_asr_transcribe` POST body below.
        self.speaker_labels = speaker_labels

    def run(self) -> None:
        if not self.api_key:
            self.error.emit(
                'AssemblyAI (Subtitld Cloud): API key not configured. '
                f'Generate one at {self.base_url}/dashboard/keys/'
            )
            return
        if not self.audio_path:
            self.error.emit('AssemblyAI (Subtitld Cloud): audio_path is required')
            return

        opus_path = os.path.join(
            session.PATH_TEMP,
            f'subtitld_cloud_aai_{os.path.basename(self.audio_path)}.opus',
        )
        try:
            subprocess.Popen(
                [
                    session.FFMPEG_EXECUTABLE,
                    '-y',
                    '-i', self.audio_path,
                    '-c:a', 'libopus',
                    '-b:a', '24k',
                    '-vbr', 'on',
                    '-compression_level', '10',
                    '-application', 'voip',
                    '-ac', '1',
                    '-ar', '48000',
                    opus_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=session.STARTUPINFO,
            ).wait()
        except Exception as exc:
            self.error.emit(f'Audio re-encoding failed: {exc}')
            return

        self.progress.emit(25)

        # Upload step. The cloud's server-side upload proxy
        # (POST /api/v1/asr/upload) forwards the bytes to AssemblyAI's
        # upload endpoint using the cloud-side stored AssemblyAI key
        # and hands back a fetchable URL we pass into /asr/transcribe.
        # HTTPError gets its body read into the message so a 401/413
        # surfaces the cloud's actual reason instead of "HTTP Error 401".
        try:
            audio_url = self._upload(opus_path)
        except urllib.error.HTTPError as http_err:
            try:
                detail = http_err.read().decode('utf-8')[:500]
            except Exception:
                detail = ''
            self.error.emit(
                f'AssemblyAI (Subtitld Cloud): upload failed '
                f'({http_err.code}). {detail}'
            )
            return
        except Exception as exc:
            self.error.emit(f'AssemblyAI (Subtitld Cloud): upload failed: {exc}')
            return

        self.progress.emit(75)

        try:
            # Request body uses ONLY fields the cloud has supported since
            # v1. New optional fields land in future desktop releases —
            # never in this older code path.
            response = cloud.http_post_json(
                f'{self.base_url}/api/v1/asr/transcribe',
                {
                    'model': self.model_public_id,
                    'audio_url': audio_url,
                    'language': (self.language[:2] if self.language else ''),
                    # AssemblyAI native field; the cloud relays this
                    # through to the upstream. Older cloud builds that
                    # don't yet pass it through silently ignore the
                    # field — desktop stays forward-compatible.
                    'speaker_labels': bool(self.speaker_labels),
                },
                api_key=self.api_key,
            )
        except urllib.error.HTTPError as http_err:
            try:
                detail = http_err.read().decode('utf-8')[:500]
            except Exception:
                detail = ''
            self.error.emit(
                f'AssemblyAI (Subtitld Cloud): submit failed '
                f'({http_err.code}). {detail}'
            )
            return
        except Exception as exc:
            self.error.emit(f'AssemblyAI (Subtitld Cloud): submit failed: {exc}')
            return

        job_id = response.get('id') or ''
        if not job_id:
            self.error.emit(
                f'AssemblyAI (Subtitld Cloud): response missing job id: '
                f'{response!r}'
            )
            return

        self.submitted.emit(job_id)

    def _upload(self, opus_path: str) -> str:
        """POST the encoded audio to the cloud's upload proxy and return
        the fetchable URL it hands back.

        The cloud's ``POST /api/v1/asr/upload`` forwards to AssemblyAI's
        own upload endpoint using the cloud-side stored AssemblyAI key,
        so the desktop never needs to hold an upstream provider key —
        the whole point of the cloud-routed architecture. Response
        shape: ``{"audio_url": "https://..."}``. We pass that URL
        verbatim into the existing ``/api/v1/asr/transcribe`` call (its
        ``audio_url`` field already accepts arbitrary fetchable URLs),
        so this is the last "missing link" in the upstream→cloud→
        desktop chain.

        The opus file is read fully into memory before being POSTed.
        At 24 kbps mono that's ~10 MB per hour — well inside any
        sensible cap, and avoiding chunked-streaming machinery keeps
        the failure modes (network timeout, server 5xx) easy to reason
        about. See ``http_post_multipart`` for the wire format.
        """
        with open(opus_path, 'rb') as fh:
            audio_bytes = fh.read()

        response = cloud.http_post_multipart(
            f'{self.base_url}/api/v1/asr/upload',
            files={
                # Field name ``audio`` is the cloud API contract — DON'T
                # rename without coordinating with a cloud API bump.
                'audio': (
                    os.path.basename(opus_path),
                    audio_bytes,
                    # ffmpeg writes ``.opus`` files as Ogg-encapsulated
                    # Opus (RFC 7845). ``audio/ogg`` is the registered
                    # type; AssemblyAI's upload accepts it directly so
                    # the cloud proxy doesn't need to remux.
                    'audio/ogg',
                ),
            },
            api_key=self.api_key,
        )

        audio_url = response.get('audio_url') or ''
        if not audio_url:
            # Surface the unexpected response shape so a future cloud
            # field rename ("url"? "uri"?) shows up as a clear bug
            # instead of a silent submit-with-empty-url failure.
            raise RuntimeError(
                f'upload response missing audio_url: {response!r}'
            )
        return audio_url


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class SubtitldCloudAssemblyAIProvider(ASRProvider):
    """AssemblyAI ASR routed through Subtitld Cloud."""

    PROVIDER_ID = 'assemblyai'  # clean — claims back the name from the legacy

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._worker: _SubmitWorker | None = None
        self._job_id: str | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_once)
        self._elapsed_ms = 0
        # Captured at submit time from ``session.VIDEO['duration']`` —
        # used as a fallback when the cloud's job response doesn't
        # carry ``audio_duration`` (see `_segments_from_job`).
        self._audio_duration_fallback: float = 0.0
        # Captured at submit time from the user's diarization toggle.
        # When False, every returned cue is force-tagged with speaker
        # ``A`` after the transcript arrives — see ``_poll_once``.
        # We snapshot at submit (not poll) because the user could flip
        # the checkbox between submit and completion, and the polled
        # result must match what was actually requested.
        self._speaker_labels: bool = True
        # Same submit-time snapshot rule applies to phrase slicing:
        # toggling mid-job must not retroactively re-cut a result that
        # was already accepted as one-cue-per-utterance.
        self._slice_by_phrase: bool = True
        # Live catalog (filled by refresh_catalog). Empty when auth isn't set.
        self._models: list[dict] = []

    # ---- identity ------------------------------------------------------
    @property
    def id(self) -> str:  # noqa: A003
        return self.PROVIDER_ID

    @property
    def display_name(self) -> str:
        return 'AssemblyAI'

    @property
    def is_builtin(self) -> bool:
        return True

    def is_available(self) -> bool:
        # Cloud transcription only works once the user has set a Subtitld
        # Cloud key; without it, requests fail with "set your API key".
        return cloud.is_configured()

    @property
    def languages(self) -> list[str]:
        # If the cloud has populated the catalog with per-model language
        # metadata, derive the union from that. Otherwise fall back to
        # the static list shared with the legacy direct provider so the
        # language filter still works pre-auth.
        if self._models:
            collected: set[str] = set()
            for m in self._models:
                # Defensive: m['languages'] may be missing or wrong type
                # if the cloud later changes the shape.
                langs = m.get('languages')
                if isinstance(langs, list):
                    collected.update(str(x).lower() for x in langs if x)
            if collected:
                return sorted(collected)
        return list(_STATIC_LANGUAGES)

    @property
    def config_schema(self) -> dict | None:
        # Auth lives on the shared Subtitld Cloud config slot, so every
        # cloud-backed builtin (this one, ElevenLabs in the future, etc.)
        # reads from the same place. The schema declares it here so the
        # AddonsDialog renders a single field per builtin — the user
        # sees and edits the SAME api_key under each cloud-backed
        # provider's settings, which round-trips through one shared
        # session.CONFIG key. (If they update it under one, it updates
        # under all.)
        return {
            'fields': [
                # No ``base_url`` field — the cloud endpoint is fixed to
                # production for users and overridden only via the
                # ``SUBTITLD_CLOUD_BASE_URL`` env var for developers (see
                # ``subtitld_cloud_shared.read_base_url``). The API key is
                # set here OR in the Global → Subtitld Cloud tab; both
                # write the same shared config slot.
                {
                    'key': 'api_key',
                    'type': 'password',
                    'label': 'Subtitld Cloud API key',
                    'help': (
                        'AssemblyAI requests are billed through your Subtitld Cloud '
                        f'account. Generate an API key at {cloud.read_dashboard_url()}'
                    ),
                    'storage': 'session.CONFIG.transcription.engine_options.SubtitldCloud.api_key',
                },
                # Per-job option (no ``storage`` redirect — lives in the
                # per-addon bag at ``CONFIG['addons']['options']['assemblyai']``
                # because diarization is an AssemblyAI-specific knob, not
                # part of shared cloud auth). ``transcribe()`` reads it back
                # via ``registry.options_for(PROVIDER_ID)``.
                {
                    'key': 'speaker_labels',
                    'type': 'bool',
                    'label': 'Speaker diarization',
                    'help': (
                        'Detect distinct speakers and tag each subtitle with A, B, '
                        'C, ... When off, every subtitle is tagged as speaker A.'
                    ),
                    'default': True,
                },
                {
                    'key': 'slice_by_phrase',
                    'type': 'bool',
                    'label': 'Slice subtitles by phrase',
                    'help': (
                        'Re-cut each speaker turn into shorter, sentence-sized '
                        'cues using punctuation and pause detection. Turn off '
                        'to keep one subtitle per uninterrupted speaker turn.'
                    ),
                    'default': True,
                },
            ],
        }

    # ---- catalog -------------------------------------------------------
    def list_models(self) -> list[dict]:
        """Return cached catalog rows for this provider's ASR models.

        Rows are the verbatim shape returned by /api/v1/catalog (pass-
        through), so unknown future fields don't break the desktop.
        """
        return list(self._models)

    def refresh_catalog(self) -> None:
        """Fetch ``/api/v1/catalog?provider=assemblyai&task=asr.transcribe``.

        Silent on failure — the picker just shows the cached list (or
        empty if never loaded). The cloud server side enforces enabled-
        only filtering, so the desktop doesn't double-check.
        """
        self._models = cloud.fetch_catalog_filtered(
            provider=UPSTREAM_PROVIDER_ID,
            task='asr.transcribe',
        )

    # ---- transcribe ----------------------------------------------------
    def transcribe(self, audio_path: str, language: str, options: dict | None = None) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.error.emit('AssemblyAI (Subtitld Cloud): a transcription is already in progress')
            return

        # ``options['model']`` is the public id (e.g. 'subtitld-cloud:assemblyai/best').
        # Default to the first catalog entry when not specified — that's
        # whatever the cloud orders first for our provider (Best, currently).
        model_public_id = ''
        if options:
            model_public_id = options.get('model', '')
        if not model_public_id:
            if not self._models:
                self.refresh_catalog()
            if self._models:
                model_public_id = self._models[0].get('id', '')
        if not model_public_id:
            self.error.emit(
                'AssemblyAI (Subtitld Cloud): no models available. Check your API '
                'key and refresh, or visit the Subtitld Cloud dashboard.'
            )
            return

        self._job_id = None
        self._elapsed_ms = 0
        # Capture the local audio's duration as a fallback for
        # `_segments_from_job` when the cloud's response omits
        # ``audio_duration``. Without this, the whole-audio fallback
        # cue lands with end=0.0 and the user sees an empty timeline
        # (or, worse, a divide-by-zero in panels that read durations).
        # session.VIDEO is the host's source of truth — copy the value
        # at submit time so the worker thread doesn't reach into shared
        # state asynchronously.
        try:
            self._audio_duration_fallback = float(session.VIDEO.get('duration') or 0.0)
        except (AttributeError, TypeError, ValueError):
            self._audio_duration_fallback = 0.0

        # Resolve the diarization flag from both the legacy panel-options
        # slot and the new addon-registry slot — same merge pattern the
        # whispercpp provider uses for ``phrase_grouping`` so a setting
        # written through either path takes effect on the next submit.
        # Registry wins on conflict (authoritative for new code).
        cfg: dict = {}
        if isinstance(options, dict):
            cfg.update(options)
        cfg.update(registry.options_for(self.PROVIDER_ID))
        # Truthy default — diarization is on by default per AssemblyAI's
        # own recommendation. The user can opt out via the inline panel
        # toggle, in which case every cue gets stamped speaker ``A``
        # after the transcript arrives (see ``_poll_once``).
        self._speaker_labels = bool(cfg.get('speaker_labels', True))
        # Truthy default — uncut speaker turns are often 8–15 seconds
        # and don't fit a single subtitle row comfortably. Slicing
        # produces readable, sentence-sized cues out of the box; the
        # user can opt out to preserve upstream segmentation verbatim.
        self._slice_by_phrase = bool(cfg.get('slice_by_phrase', True))

        worker = _SubmitWorker(
            audio_path=audio_path,
            language=language,
            model_public_id=model_public_id,
            base_url=cloud.read_base_url(),
            api_key=cloud.read_api_key(),
            speaker_labels=self._speaker_labels,
        )
        self._worker = worker
        worker.started.connect(lambda: self.transcript_started.emit())
        worker.progress.connect(lambda v: self.progress.emit(v / 100.0, ''))
        worker.error.connect(self.error)
        worker.submitted.connect(self._on_submitted)
        worker.finished.connect(self._on_worker_finished)
        worker.start()

    def cancel(self) -> None:
        self._poll_timer.stop()
        if self._job_id:
            log.info('subtitld-cloud-aai: cancel requested for job %s', self._job_id)
            try:
                base = cloud.read_base_url().rstrip('/')
                req = urllib.request.Request(
                    f'{base}/api/v1/jobs/{self._job_id}/cancel', method='POST'
                )
                # Reuse the shared module's UA so we don't get 403'd by
                # the cloud's WAF (see subtitld_cloud_shared._USER_AGENT).
                req.add_header('User-Agent', cloud.USER_AGENT)
                api_key = cloud.read_api_key()
                if api_key:
                    req.add_header('Authorization', f'Bearer {api_key}')
                urllib.request.urlopen(req, timeout=5).read()
            except Exception:
                log.debug('subtitld-cloud-aai: cancel POST failed', exc_info=True)
        self.error.emit('cancelled')

    # ---- internal: post-submission flow --------------------------------
    def _on_submitted(self, job_id: str) -> None:
        self._job_id = job_id
        self._elapsed_ms = 0
        self._poll_timer.start()

    def _on_worker_finished(self) -> None:
        self._worker = None

    def _poll_once(self) -> None:
        if not self._job_id:
            self._poll_timer.stop()
            return

        self._elapsed_ms += _POLL_INTERVAL_MS
        if self._elapsed_ms > _POLL_TIMEOUT_MS:
            self._poll_timer.stop()
            self.error.emit(
                f'AssemblyAI (Subtitld Cloud): job {self._job_id} still pending '
                f'after {_POLL_TIMEOUT_MS // 60_000} minutes; giving up'
            )
            return

        try:
            data = cloud.http_get(
                f'{cloud.read_base_url().rstrip("/")}/api/v1/jobs/{self._job_id}',
                api_key=cloud.read_api_key(),
            )
        except Exception as exc:
            # Transient network errors are common; keep polling.
            log.debug('subtitld-cloud-aai: poll failed (will retry): %s', exc)
            return

        # All field access via .get() so unknown future statuses or
        # missing fields don't crash older desktops.
        status = data.get('status') or ''

        if status in ('queued', 'running'):
            return
        self._poll_timer.stop()

        if status == 'failed':
            msg = data.get('error_message') or data.get('error_code') or 'unknown error'
            self.error.emit(f'AssemblyAI (Subtitld Cloud): {msg}')
            return
        if status == 'cancelled':
            self.error.emit('cancelled')
            return
        if status != 'complete':
            # Future-unknown status — surface cleanly.
            self.error.emit(f'AssemblyAI (Subtitld Cloud): unexpected status {status!r}')
            return

        # Log the top-level keys at INFO so the cloud-side surface
        # stays observable as it evolves — without this, a future field
        # rename (e.g. ``utterances`` → ``segments``) reads on the
        # desktop as "subtitles aren't shown" with no clue where the
        # mismatch is. Keys only at INFO; the body lands at DEBUG only
        # (transcripts can carry sensitive content the user hasn't
        # opted into logging).
        response_keys = sorted(data.keys())
        log.info(
            'subtitld-cloud-aai: job %s complete, response keys: %s',
            self._job_id, response_keys,
        )
        log.debug('subtitld-cloud-aai: full job %s body: %r', self._job_id, data)
        segments = _segments_from_job(
            data,
            fallback_duration_sec=self._audio_duration_fallback,
            slice_by_phrase=self._slice_by_phrase,
        )
        if not segments:
            # Include the actual response keys in the user-visible
            # error so a screenshot of the dialog is enough to
            # diagnose a cloud-side surface change — no need to hunt
            # through stderr. When the cloud's shape stabilizes this
            # branch becomes unreachable and the suffix is harmless.
            self.error.emit(
                'AssemblyAI (Subtitld Cloud): transcript completed but '
                'no text was returned. Response keys: '
                f'{response_keys}. Check the Subtitld Cloud dashboard.'
            )
            return
        # Diarization opt-out: when the user disabled speaker labels at
        # submit time, every cue collapses onto speaker ``A``. We force
        # the field rather than trust the upstream — even with
        # ``speaker_labels=false`` AssemblyAI may still echo back stray
        # labels, and the user's clear intent here is "single speaker."
        # Doing this AFTER `_segments_from_job` (not inside it) keeps
        # the parser pure: same code path whether labels are on or off.
        if not self._speaker_labels:
            for seg in segments:
                seg['speaker'] = 'A'
        else:
            # Diarization ON but some cue came back without a speaker tag
            # (whole-audio fallback, or a sentence/word the upstream
            # didn't label). Empty-string speakers are an invalid
            # downstream contract: ``session.SPEAKERS['']`` gets created
            # with no ``dubbing`` block, and the dubbing path then hands
            # ``voice=''`` to edge-tts → ``Invalid voice ''``. Coerce
            # any missing label to ``A`` so the downstream pipeline
            # (speakers panel, dubbing engines) sees a well-formed cue.
            for seg in segments:
                if not seg.get('speaker'):
                    seg['speaker'] = 'A'
        self.transcript_finished.emit(segments)


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------

_provider_instance: SubtitldCloudAssemblyAIProvider | None = None


def get_provider() -> SubtitldCloudAssemblyAIProvider:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = SubtitldCloudAssemblyAIProvider()
    return _provider_instance
