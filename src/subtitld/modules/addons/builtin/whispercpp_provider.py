"""Built-in offline ASR provider — whisper.cpp via pywhispercpp.

This is the fresh-install default for transcription: no cloud account,
no credentials, no PyTorch dep. Just whisper.cpp's GGML inference engine
running locally on CPU.

Models are NOT bundled with Subtitld. The first time the user runs a
transcription, the chosen model is downloaded once from the official
whisper.cpp model repository on Hugging Face and cached under
``$PATH_SUBTITLD_DATA_MODELS/whispercpp/ggml-<model>.bin``. Subsequent
runs read it from disk. This keeps the installer small (no 60+ MB
binary bloat for a feature the user may never use) at the cost of one
one-time download — same shape as the FFmpeg-separator addon's model
cache.

Provider id: ``whispercpp``.

Language handling
-----------------
The host passes language tags in BCP-47 shape (``en-us``, ``pt-br``).
Whisper itself only consumes the 2-letter ISO-639-1 part. We slice
``[:2]`` before forwarding. An empty string triggers Whisper's own
language auto-detection.

Cancellation
------------
whisper.cpp exposes a C-level abort callback; pywhispercpp didn't surface
it as of the version this code was written against. Cancellation here is
cooperative — we set a flag that's noticed between stages (encode →
download → transcribe), but the currently-running transcribe call has
to finish before the thread can exit. Good enough for v0; revisit if
users complain about long uncancellable jobs on the larger models.

Configuration storage
---------------------
The model size, thread-count and phrase-grouping settings live under
``session.CONFIG['addons']['options']['whispercpp']`` (via the registry
helpers) so the AddonsDialog's schema-driven config UI round-trips
correctly. We also merge any options dict the import panel passes in
(legacy ``session.CONFIG['transcription']['engine_options']``), with
the registry winning on conflict.

Phrase grouping
---------------
whisper.cpp segments at its own decoding cadence — usually a few-second
window or a detected pause — which slices mid-sentence often enough to
be annoying for subtitle work. By default we post-process the raw
segments into one-row-per-sentence, atomizing at sentence-final
punctuation (across Latin / CJK / Devanagari / Arabic scripts) and
re-merging up to ~84 chars / 7 s per row before forcing a break. Times
within a split are interpolated by character-length ratio: coarse, but
close enough for a manual-edit first pass. The user can switch the
behavior off in the addon config to get whisper's raw segmentation.
"""

from __future__ import annotations

import logging
import os
import subprocess
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, QThread, Signal

from subtitld.modules import session
from subtitld.modules.addons import registry
from subtitld.modules.addons.provider import ASRProvider

log = logging.getLogger(__name__)


# Whisper's 99 supported ISO-639-1 codes (from openai/whisper's
# tokenizer.py LANGUAGES dict). Listed bare; the addon language matcher
# does prefix-of resolution so regional variants like ``pt-br`` still
# match a provider that declares ``pt``.
_WHISPER_LANGUAGES = (
    'af', 'am', 'ar', 'as', 'az', 'ba', 'be', 'bg', 'bn', 'bo', 'br', 'bs',
    'ca', 'cs', 'cy', 'da', 'de', 'el', 'en', 'es', 'et', 'eu', 'fa', 'fi',
    'fo', 'fr', 'gl', 'gu', 'ha', 'haw', 'he', 'hi', 'hr', 'ht', 'hu', 'hy',
    'id', 'is', 'it', 'ja', 'jw', 'ka', 'kk', 'km', 'kn', 'ko', 'la', 'lb',
    'ln', 'lo', 'lt', 'lv', 'mg', 'mi', 'mk', 'ml', 'mn', 'mr', 'ms', 'mt',
    'my', 'ne', 'nl', 'nn', 'no', 'oc', 'pa', 'pl', 'ps', 'pt', 'ro', 'ru',
    'sa', 'sd', 'si', 'sk', 'sl', 'sn', 'so', 'sq', 'sr', 'su', 'sv', 'sw',
    'ta', 'te', 'tg', 'th', 'tk', 'tl', 'tr', 'tt', 'uk', 'ur', 'uz', 'vi',
    'yi', 'yo', 'zh',
)

# Whisper.cpp GGML models hosted by ggerganov on Hugging Face. `resolve/main`
# returns raw bytes (not a Git LFS pointer). Mirrors the layout used by the
# upstream ``models/download-ggml-model.sh`` script.
_MODEL_BASE_URL = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main'

# Models the picker exposes. Order is significant: the first entry is the
# default the picker lands on for a fresh install. ``base`` is multilingual
# (~142 MB) — a sensible balance of size/speed/accuracy across languages;
# ".en" variants are English-only and slightly faster.
_DEFAULT_MODEL = 'base'
_AVAILABLE_MODELS = (
    'tiny', 'tiny.en',
    'base', 'base.en',
    'small', 'small.en',
    'medium', 'medium.en',
    'large-v3',
)

# Download buffer size. 256 KiB keeps the UI progress bar moving smoothly
# without flooding the signal queue across the thread boundary.
_DOWNLOAD_CHUNK_BYTES = 256 * 1024


def _model_filename(model: str) -> str:
    return f'ggml-{model}.bin'


def _model_url(model: str) -> str:
    return f'{_MODEL_BASE_URL}/{_model_filename(model)}'


def _model_local_path(model: str) -> str:
    cache_root = session.PATH_SUBTITLD_DATA_MODELS / 'whispercpp'
    if not cache_root.exists():
        cache_root.mkdir(parents=True)
    return str(cache_root / _model_filename(model))


# ---------------------------------------------------------------------------
# Phrase regrouping.
# ---------------------------------------------------------------------------
#
# whisper.cpp emits segments at its own decoding cadence — typically a
# few-second window or a detected long pause — which means a single
# sentence often gets sliced across two adjacent segments (and, less
# commonly, several short sentences land in one). For subtitle work the
# user wants one row per phrase, so we post-process: atomize at
# sentence terminators (with time spans distributed by character-length
# ratio inside a split), then re-merge consecutive atoms until each row
# ends with a terminator or hits the readability cap. Speaker changes
# always force a break.
#
# The character-ratio interpolation is a coarse approximation — real
# speech doesn't have a uniform char/sec rate — but it's accurate enough
# for a manual-edit first pass and avoids the cost of asking whisper.cpp
# for word-level timestamps (which roughly doubles inference time).

# Sentence-ending punctuation across scripts. ASCII terminators,
# full-width CJK forms, Devanagari danda/double-danda, and the Arabic
# question mark. We intentionally OMIT the opening Spanish `¿`/`¡` since
# those don't end a phrase.
_PHRASE_TERMINATORS = frozenset('.?!。？！．।॥؟')

# Characters that may trail a terminator and still belong to the same
# sub-phrase — closing quotes, brackets, etc. So `"hello."` stays
# together when we split.
_PHRASE_TRAILING_CLOSERS = frozenset('"\')]}»›\u201d\u2019')

# Subtitle readability caps used as a fallback when whisper omits
# punctuation (common for low-quality audio or certain languages).
# Tuned to typical captioning guidelines: ~2 lines of ~42 chars at the
# canonical ~17 cps reading rate.
_PHRASE_MAX_CHARS = 84
_PHRASE_MAX_DURATION_SEC = 7.0


def _ends_with_terminator(text: str) -> bool:
    """True if ``text`` ends with sentence-final punctuation.

    Strips up to a handful of trailing closing-quotes/brackets first so
    `She said "hello."` still registers as a terminated sentence.
    """
    t = text.rstrip()
    for _ in range(4):
        if not t:
            return False
        if t[-1] in _PHRASE_TRAILING_CLOSERS:
            t = t[:-1].rstrip()
            continue
        break
    return bool(t) and t[-1] in _PHRASE_TERMINATORS


def _split_segment_on_terminators(seg: dict) -> list[dict]:
    """Split one raw whisper segment at internal sentence terminators.

    The original time span is distributed across sub-segments by
    character-length ratio. Each terminator (with any trailing closing
    quotes/brackets) closes the current sub-phrase; the remainder
    becomes the next.
    """
    text = (seg.get('text') or '').strip()
    if not text:
        return []
    start = float(seg.get('start', 0.0))
    end = float(seg.get('end', 0.0))
    speaker = seg.get('speaker', 'A')
    duration = max(0.0, end - start)
    total_chars = max(1, len(text))

    # Find every cut point — index just past a terminator (and any
    # trailing closing quotes/brackets).
    cut_points: list[int] = []
    i = 0
    while i < len(text):
        if text[i] in _PHRASE_TERMINATORS:
            j = i + 1
            while j < len(text) and text[j] in _PHRASE_TRAILING_CLOSERS:
                j += 1
            cut_points.append(j)
            i = j
        else:
            i += 1
    if not cut_points or cut_points[-1] < len(text):
        cut_points.append(len(text))

    subs: list[dict] = []
    prev = 0
    for cut in cut_points:
        chunk = text[prev:cut].strip()
        if chunk:
            subs.append({
                'start': start + duration * (prev / total_chars),
                'end': start + duration * (cut / total_chars),
                'text': chunk,
                'speaker': speaker,
            })
        prev = cut
    return subs


def _regroup_into_phrases(segments: list[dict]) -> list[dict]:
    """Rewrite raw whisper segments into phrase-aligned subtitle rows.

    Two passes: atomize each segment at internal sentence terminators,
    then re-merge consecutive atoms until each phrase either ends with
    a terminator or exceeds the readability caps. Speaker changes force
    a phrase boundary regardless.

    Returns a new list; does not mutate the input. Preserves order.
    """
    if not segments:
        return []

    atoms: list[dict] = []
    for seg in segments:
        atoms.extend(_split_segment_on_terminators(seg))
    if not atoms:
        return []

    phrases: list[dict] = []
    current: dict | None = None
    for atom in atoms:
        if current is None:
            current = dict(atom)
            continue
        # Never merge across speakers — even with no punctuation, a
        # diarization change is a hard phrase boundary.
        if atom['speaker'] != current['speaker']:
            phrases.append(current)
            current = dict(atom)
            continue
        # Terminator boundaries are sacred: if the current phrase
        # already ends with sentence-final punctuation, don't merge
        # the next atom into it. This is what produces one-row-per-
        # sentence — even short sentences stay split.
        if _ends_with_terminator(current['text']):
            phrases.append(current)
            current = dict(atom)
            continue

        # current is mid-sentence (no terminator) — try to absorb the
        # next atom to complete it.
        merged_text = current['text'] + ' ' + atom['text']
        merged_duration = atom['end'] - current['start']
        would_overflow = (
            len(merged_text) > _PHRASE_MAX_CHARS
            or merged_duration > _PHRASE_MAX_DURATION_SEC
        )
        if would_overflow:
            # No punctuation to lean on and we're past the readability
            # cap. Force-close current as-is — a 7-second row that ends
            # mid-thought still beats cramming the next chunk into a
            # runaway 30-second one.
            phrases.append(current)
            current = dict(atom)
            continue

        current['text'] = merged_text
        current['end'] = atom['end']
        # No early-close-on-terminator here: the top-of-loop check on
        # the NEXT iteration handles it. If this was the last atom we
        # fall through to the trailing append below.

    if current is not None:
        phrases.append(current)
    return phrases


# ---------------------------------------------------------------------------
# Worker thread: pre-encode → maybe download → transcribe.
# ---------------------------------------------------------------------------


class _WhisperWorker(QThread):
    progress = Signal(float, str)
    # Emitted for each segment whisper.cpp finalizes during decoding. The
    # panel layer turns these into timeline rows that show up live, so the
    # user sees text accumulate instead of staring at a frozen bar for
    # the full duration of a ``large-v3`` run.
    partial_segment = Signal(dict)
    transcript_finished = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        *,
        audio_path: str,
        language: str,
        model: str,
        threads: int,
        phrase_grouping: bool = True,
    ):
        super().__init__()
        self.audio_path = audio_path
        self.language = language
        self.model = model
        self.threads = threads
        self.phrase_grouping = phrase_grouping
        self._cancelled = False
        # Filled in by `_encode_to_wav`; used by the segment callback to
        # turn (segment end-time) into (progress fraction). Falls back to
        # an indeterminate-ish slow climb if we couldn't read it.
        self._audio_duration_sec: float = 0.0

    def cancel(self) -> None:
        # Cooperative — checked between stages. The in-flight transcribe
        # call inside pywhispercpp can't be interrupted from Python.
        self._cancelled = True

    def run(self) -> None:
        # 1) Pre-encode to 16 kHz mono PCM WAV — whisper.cpp's expected input.
        try:
            wav_path = self._encode_to_wav(self.audio_path)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b'').decode('utf-8', 'replace')[:300]
            self.error.emit(
                f'Whisper (offline): audio re-encode failed. {stderr}'
            )
            return
        except Exception as exc:  # noqa: BLE001 — surface anything to the UI
            self.error.emit(f'Whisper (offline): audio re-encode failed: {exc}')
            return
        if self._cancelled:
            self.error.emit('cancelled')
            return

        # 2) Make sure the model is on disk. Download once on first use.
        try:
            model_path = self._ensure_model(self.model)
        except urllib.error.HTTPError as exc:
            self.error.emit(
                f'Whisper (offline): model download failed (HTTP {exc.code}). '
                f'Check your internet connection and try again.'
            )
            return
        except urllib.error.URLError as exc:
            self.error.emit(
                f'Whisper (offline): model download failed ({exc.reason}). '
                f'Check your internet connection and try again.'
            )
            return
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f'Whisper (offline): model download failed: {exc}')
            return
        if self._cancelled:
            self.error.emit('cancelled')
            return

        # 3) Run transcription.
        try:
            segments = self._transcribe(wav_path, model_path)
        except RuntimeError as exc:
            # Used for our own preflight failures (e.g. pywhispercpp missing).
            self.error.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception('whispercpp: transcription crashed')
            self.error.emit(
                f'Whisper (offline): transcription failed: {exc}. '
                f'If the error persists, delete the model file at '
                f'{model_path} and try again.'
            )
            return
        if self._cancelled:
            self.error.emit('cancelled')
            return

        self.transcript_finished.emit(segments)

    # ---- stages --------------------------------------------------------
    def _encode_to_wav(self, source_path: str) -> str:
        self.progress.emit(0.02, 'Decoding audio...')
        out_path = os.path.join(
            session.PATH_TEMP,
            f'whispercpp_{os.path.basename(source_path)}.16k.wav',
        )
        # 16 kHz mono PCM s16 is whisper.cpp's native input format. Anything
        # else gets resampled inside the C++ layer — doing it here keeps the
        # native side dependency-free of libavformat.
        subprocess.run(
            [
                session.FFMPEG_EXECUTABLE,
                '-y', '-hide_banner', '-loglevel', 'error',
                '-i', source_path,
                '-vn',
                '-ac', '1',
                '-ar', '16000',
                '-c:a', 'pcm_s16le',
                out_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            startupinfo=session.STARTUPINFO,
        )

        # Read the encoded WAV's duration so the transcribe callback can
        # convert segment end-times into a real progress fraction. Import
        # locally so a missing soundfile dep doesn't take out the whole
        # provider — fallback is just a no-progress transcribe stage.
        try:
            import soundfile as sf  # type: ignore
            info = sf.info(out_path)
            self._audio_duration_sec = float(info.duration or 0.0)
        except Exception as exc:  # noqa: BLE001
            log.debug('whispercpp: could not read WAV duration (%s); '
                      'transcribe progress will be coarse', exc)
            self._audio_duration_sec = 0.0
        return out_path

    def _ensure_model(self, model: str) -> str:
        local = _model_local_path(model)
        if os.path.isfile(local) and os.path.getsize(local) > 0:
            return local

        # Download to a `.part` sibling first; rename only on full success.
        # An aborted download (user quit / network drop) doesn't then leave
        # a truncated file that future runs would happily try to load and
        # crash on.
        url = _model_url(model)
        part = local + '.part'
        self.progress.emit(0.05, f'Downloading {model} model (first use)...')
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'subtitld-whispercpp/1'},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            try:
                total = int(resp.headers.get('Content-Length') or 0)
            except (TypeError, ValueError):
                total = 0
            written = 0
            with open(part, 'wb') as out:
                while True:
                    if self._cancelled:
                        # Leave the .part — we don't try to resume yet, but
                        # also don't waste the bytes on the next failure.
                        return local
                    chunk = resp.read(_DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
                    if total > 0:
                        # Cap download progress at 0.9 so the transcribe
                        # stage has visible room to move the bar — download
                        # is the prelude, not the work itself.
                        ratio = min(0.9, 0.05 + 0.85 * (written / total))
                        self.progress.emit(
                            ratio,
                            f'Downloading {model} model... '
                            f'{written // (1024 * 1024)} / '
                            f'{(total // (1024 * 1024)) or "?"} MB',
                        )
        # Atomic rename so a partial file can never masquerade as a finished one.
        os.replace(part, local)
        return local

    def _transcribe(self, wav_path: str, model_path: str) -> list[dict]:
        # Defer the import: pywhispercpp loads a native lib at module
        # import time, which we don't want to pay at app startup. Also
        # gives us a clean error path if the wheel isn't installed.
        try:
            from pywhispercpp.model import Model
        except ImportError as exc:
            raise RuntimeError(
                'Whisper (offline): pywhispercpp is not installed. '
                'Install with `pip install pywhispercpp`.'
            ) from exc

        self.progress.emit(0.87, 'Loading model...')
        model_kwargs: dict = {}
        if self.threads and self.threads > 0:
            model_kwargs['n_threads'] = int(self.threads)
        # pywhispercpp's Model takes a model name OR an absolute path as the
        # first positional. We pass an explicit path to bypass its own
        # download / cache logic and keep the file under Subtitld's
        # PATH_SUBTITLD_DATA_MODELS dir for cache-management consistency.
        whisper_model = Model(model_path, **model_kwargs)

        self.progress.emit(0.90, 'Transcribing...')

        # ------------------------------------------------------------
        # Live segment streaming. pywhispercpp invokes
        # ``new_segment_callback`` per finalized segment, from inside
        # the C++ decode loop running on whichever native thread the
        # wheel set up. Emitting a Qt signal from there is safe — Qt's
        # cross-thread signal emission queues into the receiver's event
        # loop (`QueuedConnection` is the auto default between threads).
        #
        # We use the callback for two things:
        #   1. Emit `partial_segment` so the panel can drop text onto
        #      the timeline as it lands instead of waiting for the full
        #      transcript. On `large-v3` of an hour-long file this is
        #      the difference between "is it frozen?" and visible work.
        #   2. Advance the progress bar based on (segment end-time) /
        #      (audio duration). The 0.90 → 1.0 window is reserved for
        #      this stage; we cap at 0.999 so the bar visibly snaps to
        #      100% on the final `transcript_finished` signal instead
        #      of arriving there mid-transcribe.
        # ------------------------------------------------------------
        collected: list[dict] = []

        def _segment_to_dict(seg) -> dict | None:
            # pywhispercpp >= 1.2 exposes ``t0`` / ``t1`` in centiseconds.
            # Older builds gave ``.start`` / ``.end`` already in seconds.
            # Try both so a wheel-version bump doesn't silently break us.
            t0 = getattr(seg, 't0', None)
            t1 = getattr(seg, 't1', None)
            if t0 is None and t1 is None:
                start = float(getattr(seg, 'start', 0.0) or 0.0)
                end = float(getattr(seg, 'end', 0.0) or 0.0)
            else:
                start = float(t0 or 0) / 100.0
                end = float(t1 or 0) / 100.0
            text = (getattr(seg, 'text', '') or '').strip()
            if not text:
                return None
            return {
                'start': start,
                'end': end,
                'text': text,
                'speaker': 'A',
            }

        def _on_new_segment(seg) -> None:
            seg_dict = _segment_to_dict(seg)
            if seg_dict is None:
                return
            collected.append(seg_dict)
            # Stream to UI for live timeline updates.
            try:
                self.partial_segment.emit(seg_dict)
            except Exception:
                pass
            # Translate segment end-time into a progress fraction within
            # the reserved 0.90..0.999 window.
            duration = self._audio_duration_sec
            if duration > 0.0:
                fraction = min(1.0, max(0.0, seg_dict['end'] / duration))
                ratio = 0.90 + 0.099 * fraction
                pct = int(round(seg_dict['end']))
                total = int(round(duration))
                try:
                    self.progress.emit(
                        ratio,
                        f'Transcribing... {pct}/{total}s',
                    )
                except Exception:
                    pass

        def _should_abort() -> bool:
            # Returning True tells whisper.cpp to break out of the decode
            # loop at its next checkpoint — first real cancellation point
            # for an in-flight transcribe call.
            return self._cancelled

        # 2-letter ISO; empty → Whisper auto-detect.
        lang = (self.language[:2] if self.language else '').lower() or None
        transcribe_kwargs: dict = {
            'new_segment_callback': _on_new_segment,
            'abort_callback': _should_abort,
        }
        if lang:
            transcribe_kwargs['language'] = lang

        raw_segments = whisper_model.transcribe(wav_path, **transcribe_kwargs)

        # Prefer the callback-collected list: it's the order whisper.cpp
        # actually emitted them, which matches what the panel has
        # already streamed onto the timeline. Fall back to the return
        # value when the callback wasn't fired — e.g. short audio that
        # produced everything in one batch, or an older wheel that
        # didn't honor the callback.
        if collected:
            segments = collected
        else:
            segments = []
            for seg in raw_segments or []:
                seg_dict = _segment_to_dict(seg)
                if seg_dict is not None:
                    segments.append(seg_dict)

        # Re-merge whisper.cpp's timestamp-driven segments into phrase-
        # aligned subtitle rows. Live-streamed segments stay raw (the
        # user gets immediate decoding feedback); the wholesale replace
        # at `transcript_finished` swaps in the clean phrase-aligned
        # list. When `phrase_grouping` is off we keep whisper's native
        # segmentation untouched.
        if self.phrase_grouping:
            return _regroup_into_phrases(segments)
        return segments


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class WhispercppProvider(ASRProvider):
    """Offline transcription via whisper.cpp / pywhispercpp."""

    PROVIDER_ID = 'whispercpp'

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._worker: _WhisperWorker | None = None

    # ---- identity ------------------------------------------------------
    @property
    def id(self) -> str:  # noqa: A003
        return self.PROVIDER_ID

    @property
    def display_name(self) -> str:
        return 'Whisper (offline)'

    @property
    def is_builtin(self) -> bool:
        return True

    @property
    def languages(self) -> list[str]:
        return list(_WHISPER_LANGUAGES)

    @property
    def config_schema(self) -> dict | None:
        return {
            'fields': [
                {
                    'key': 'model',
                    'type': 'select',
                    'label': 'Model',
                    'help': (
                        'Larger models are more accurate but slower and bigger '
                        'to download. Multilingual variants ("base", "small", ...) '
                        'handle every language Whisper supports; ".en" variants '
                        'are English-only and slightly faster. The chosen model '
                        'is downloaded once on first use.'
                    ),
                    'options': list(_AVAILABLE_MODELS),
                    'default': _DEFAULT_MODEL,
                },
                {
                    'key': 'threads',
                    'type': 'int',
                    'label': 'CPU threads (0 = auto)',
                    'help': (
                        'Number of CPU threads for inference. Leave at 0 to let '
                        'whisper.cpp pick a sensible default for your machine.'
                    ),
                    'default': 0,
                },
                {
                    'key': 'phrase_grouping',
                    'type': 'bool',
                    'label': 'Group output by phrases',
                    'help': (
                        'Re-merge whisper.cpp\'s raw segments into one subtitle '
                        'row per sentence. Whisper segments at its own decoding '
                        'cadence (every few seconds or at a detected pause), '
                        'which often slices mid-sentence. Turn off to keep '
                        'whisper\'s native segmentation unchanged.'
                    ),
                    'default': True,
                },
            ],
        }

    # ---- transcribe ----------------------------------------------------
    def transcribe(self, audio_path: str, language: str, options: dict | None = None) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.error.emit('Whisper (offline): a transcription is already in progress')
            return

        # Two storage sources can carry this provider's settings:
        #   - the panel's ``options`` arg (read from the legacy
        #     ``session.CONFIG['transcription']['engine_options']`` slot)
        #   - ``registry.options_for(PROVIDER_ID)``, which is where the
        #     AddonsDialog's schema-driven config UI writes
        # The registry path is the authoritative one for new code, so it
        # wins on conflict; the panel options provide back-compat for
        # anything written through the old slot.
        cfg: dict = {}
        if isinstance(options, dict):
            cfg.update(options)
        cfg.update(registry.options_for(self.PROVIDER_ID))

        model = str(cfg.get('model') or _DEFAULT_MODEL)
        if model not in _AVAILABLE_MODELS:
            log.warning(
                'whispercpp: unknown model %r in config, falling back to %s',
                model, _DEFAULT_MODEL,
            )
            model = _DEFAULT_MODEL
        try:
            threads = int(cfg.get('threads') or 0)
        except (TypeError, ValueError):
            threads = 0

        # Truthy default — phrase-grouping is on out of the box because
        # whisper.cpp's raw segmentation is rarely sentence-aligned.
        # Users who want the raw segmentation can flip the toggle in
        # the addon config UI.
        phrase_grouping = bool(cfg.get('phrase_grouping', True))

        worker = _WhisperWorker(
            audio_path=audio_path,
            language=language,
            model=model,
            threads=threads,
            phrase_grouping=phrase_grouping,
        )
        self._worker = worker
        worker.progress.connect(lambda v, m: self.progress.emit(v, m))
        # Forward live segments so `_GenericASRPanel._on_partial` can
        # append them to the timeline as they're decoded. Auto-detected
        # cross-thread connection routes via the main thread's event
        # loop so the UI update is GUI-thread-safe even though the
        # callback fires from whisper.cpp's native worker.
        worker.partial_segment.connect(lambda seg: self.partial.emit(seg))
        worker.transcript_finished.connect(
            lambda segs: self.transcript_finished.emit(segs)
        )
        worker.error.connect(lambda msg: self.error.emit(msg))
        worker.finished.connect(self._on_worker_finished)
        worker.start()

    def cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            # Don't emit `cancelled` here — the worker's run() will emit
            # it on the next stage check, so we avoid a double-emit race.

    def _on_worker_finished(self) -> None:
        self._worker = None


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_provider_instance: WhispercppProvider | None = None


def get_provider() -> WhispercppProvider:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = WhispercppProvider()
    return _provider_instance
