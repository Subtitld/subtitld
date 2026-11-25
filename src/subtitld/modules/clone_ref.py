"""Extract a per-speaker voice-clone reference clip from the source media.

Used by clone-capable TTS add-ons (Coqui XTTS, Qwen3-TTS, F5-TTS) when the
user enables "Use original voice for cloning" in the speaker panel: instead
of asking for a separately-recorded reference WAV, we extract a snippet of
the speaker's own voice from the project's source audio and feed that to
the add-on as `voice_ref_audio`.

Strategy
========
- Source audio: same fallback chain as transcription — prefer separated
  vocals (cleanest), then the cached original FLAC, then the raw video.
- Span pick: among the speaker's subtitles, take the **longest single
  segment** whose duration is in [3, 12] seconds. We don't try to splice
  multiple subtitles together because the discontinuity (silence,
  overlap, scene cuts) tends to confuse the embedding extractor. 12 s
  is the safe middle ground: F5-TTS wants 6-15 s, XTTS wants 6+ s,
  Qwen3-TTS wants 3+ s.
- If no subtitle is in the [3, 12] s sweet spot, take the longest
  subtitle the speaker has and clamp it to 12 s, audio and text cut to
  the same words, by the subtitle's word times or else the words a
  speech recognition add-on hears in it (`when_ready`, `_fit_clipped`);
  the addon's own validation will surface an error if it's still too
  short.
- Output: mono 16 kHz WAV in
  `$SUBTITLD_USER_CACHE/clone-refs/<media_key>-<speaker_slug>.wav`.
  Re-extraction is skipped if the cache file already exists *and* the
  source media's mtime is older than the cache file. Otherwise we
  re-extract — the speaker's subtitle boundaries may have moved.

The output path is returned as a `str` (or `None` on failure). All
errors are logged at WARNING; we never raise — the caller falls back
to whatever `voice_ref_audio` the user set explicitly in the addon
config (or surfaces the addon's own error if neither is available).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from pathlib import Path

from subtitld.modules import cue_kinds
from subtitld.modules import session
from subtitld.modules import markup
from subtitld.modules import utils as modules_utils
from subtitld.modules.addons import registry as addons_registry
from subtitld.modules.segment import text_of, timed_by_words, timed_pieces

log = logging.getLogger(__name__)

# Sweet spot for reference clips. Chosen by the most-restrictive add-on:
# F5-TTS asks for 6-15 s. We aim for 12 s upper bound to leave headroom
# for the addon's own validation; lower bound 3 s matches Qwen3-TTS's
# minimum, slightly below XTTS's 6 s — but XTTS will error gracefully if
# it's too short, which is preferable to refusing to extract.
_TARGET_MIN_SEC = 3.0
_TARGET_MAX_SEC = 12.0


def _resolve_source_language() -> str | None:
    """Return the BCP-47 language tag of the *source audio* — the language
    actually spoken in the project's source media.

    This is distinct from `session.SUBTITLE.language`, which tracks the
    current *display* / synthesis language. When the user translates and
    inverts (`left_panel_translation.global_panel_translation_invert_translation_button_clicked`),
    `SUBTITLE.language` flips to the target language and `seg['text']`
    is overwritten with the translation — but the audio doesn't change.

    Resolution order:
      1. Explicit `SUBTITLE.source_language` — preferred, set at
         transcription time and preserved through invert.
      2. Fallback to `SUBTITLE.language` — correct for projects that
         have not been translated (the common case), and the only
         reasonable guess for legacy projects that predate the
         `source_language` field.

    Returns None when the project has no language at all (no
    transcription, no manifest), and when it has translations but does
    not say which language is spoken: a project saved before USF kept
    `source_language` may have been inverted, its subtitles a
    translation of what the audio says. In both cases the caller should
    skip `voice_ref_text` entirely rather than feed the model a
    wrong-language transcript.
    """
    explicit = (session.SUBTITLE.source_language or '').strip()
    if explicit:
        return explicit
    if has_translations():
        return None
    current = (session.SUBTITLE.language or '').strip()
    return current or None


def has_translations() -> bool:
    """Whether the project's subtitles carry translations. Then the language
    they are in may not be the one spoken (a translation inverted shows the
    translation), and only an explicit `source_language` says which is: it
    must not be taken from `SUBTITLE.language`."""
    return any(isinstance(seg, dict) and seg.get('translations')
               for seg in session.SUBTITLE.segments)


def _segment_text_for_source_language(seg: dict, source_lang: str | None) -> str:
    """Pick the subtitle text on `seg` that matches `source_lang`.

    The audio reference clip is in `source_lang`; the `ref_text` we pass
    to clone-capable addons (qwen3-clone, xtts-clone, f5-clone) must
    also be in `source_lang` or the model has no aligned transcript to
    anchor pronunciation against.

    Where the right text lives:
      * If `source_lang == SUBTITLE.language` → no invert has
        happened, `seg['text']` matches the audio. Use it.
      * Otherwise → invert happened, `seg['text']` is the *translated*
        string and the original (audio-matching) text was stashed in
        `seg['translations'][source_lang]` by the invert handler. Use
        the translations entry.
      * If neither is present (mismatched language, no translation
        recorded) → return '' so the caller skips this segment's text.
        Empty `ref_text` is preferable to wrong-language `ref_text`:
        empty trips the addon's x-vector-only fallback (lower clone
        quality), wrong-language actively poisons accent fidelity
        (audible English bleed on Portuguese, Spanish, Italian — see
        the qwen3 docs and the bug this function exists to fix).
    """
    current = (session.SUBTITLE.language or '').strip()
    if source_lang and current and source_lang == current:
        return markup.plain(text_of(seg)).strip()
    translations = seg.get('translations') if isinstance(seg.get('translations'), dict) else None
    if translations and source_lang:
        return markup.plain(translations.get(source_lang) or '').strip()
    # No safe text available — fall back to the empty string. Caller
    # joins texts and the empty strings drop out of the join.
    return ''


def _audio_source_for_clone() -> str | None:
    """Best available audio path for clone reference extraction.

    Mirrors `_audio_source_for_transcription` in left_panel_import.py —
    keeping it duplicated rather than reaching across modules so each
    feature's source-fallback policy can drift independently if needed.
    """
    separation = session.VIDEO.get('music_voice_separation') or {}
    vocals = separation.get('vocals')
    if vocals and os.path.isfile(vocals):
        return vocals

    video_path = session.VIDEO.get('filepath')
    if video_path:
        key = modules_utils.get_cache_key(video_path)
        if key:
            original_flac = os.path.join(
                session.PATH_SUBTITLD_DATA_AUDIOSEPARATION,
                key + '_original.flac',
            )
            if os.path.isfile(original_flac):
                return original_flac
        if os.path.isfile(video_path):
            return video_path
    return None


def _slugify(name: str) -> str:
    """Filesystem-safe slug for a speaker name. Empty/missing -> 'unknown'."""
    cleaned = re.sub(r'[^A-Za-z0-9_.-]+', '_', (name or '').strip())
    return cleaned or 'unknown'


def _cache_dir() -> Path:
    out = Path(session.PATH_SUBTITLD_USER_CACHE) / 'clone-refs'
    out.mkdir(parents=True, exist_ok=True)
    return out


def _pick_spans_for_speaker(
    speaker_name: str,
) -> tuple[list[tuple[float, float]], list[str], list[tuple[float, list] | None]] | None:
    """Pick a list of [start, end] spans of source audio for the speaker,
    paired with the subtitle text spoken in each span.

    Returns None if the speaker has no usable subtitles. Otherwise returns
    `(spans, texts, clipped)` where the three lists are the same length
    and in chronological order — `spans` is ready to feed an ffmpeg
    concat filter, `texts` is ready to be joined into a `ref_text` for
    ICL-mode voice cloning. `clipped` is None for a span that is its
    whole subtitle; for one cut short it is `(subtitle_duration,
    pieces)`, its text still the whole subtitle's, for `_fit_clipped`
    to cut down (`pieces` are the subtitle's timed pieces when the text
    is theirs, else []).

    Strategy:
      1. **One in-band subtitle exists** — return a single span for the
         longest subtitle in [_TARGET_MIN_SEC, _TARGET_MAX_SEC]. This is
         the common case and gives the cleanest reference.
      2. **All subtitles are too LONG** — return a single span clamped
         from the start of the longest one to _TARGET_MAX_SEC. Its text
         is still the whole subtitle's: the span is marked in `clipped`,
         and `_fit_clipped` cuts text and audio down to the same words
         before anything is extracted. Sending the whole text with the
         clipped audio made Reverb (which paces the take by the
         transcript and continues from it) speak a 25 s line in 3 s of
         babble when the subtitle ran 72 s.
      3. **All subtitles are too SHORT** — concatenate same-speaker
         subtitles in chronological order until the cumulative duration
         reaches _TARGET_MIN_SEC, capped at _TARGET_MAX_SEC. This case
         happens with terse dialogue / interjections where no single
         line is long enough on its own — the addons (XTTS wants 6+ s,
         F5-TTS wants 6-15 s, Qwen3-TTS wants 3+ s) would otherwise
         reject the request outright.

    Concat is chronological rather than longest-first because adjacent
    same-speaker utterances tend to have similar prosody/recording
    conditions; jumping around the timeline introduces louder/softer
    discontinuities the embedding extractor doesn't like.
    """
    segments = cue_kinds.subtitles(session.SUBTITLE.segments)
    # Resolve once, outside the loop — the source language is a
    # project-level property, identical for every segment.
    source_lang = _resolve_source_language()
    # (idx, dur, start, end, text)
    candidates: list[tuple[int, float, float, float, str]] = []
    for idx, seg in enumerate(segments):
        if seg.get('speaker', 'A') != speaker_name:
            continue
        try:
            start = float(seg['start'])
            end = float(seg['end'])
        except (KeyError, TypeError, ValueError):
            continue
        duration = end - start
        if duration <= 0:
            continue
        # Pick the segment's text in the SOURCE-AUDIO language, not the
        # current display language — see `_segment_text_for_source_language`
        # for why this matters when the user has translated + inverted.
        text = _segment_text_for_source_language(seg, source_lang)
        # Word times only fit the text they time, not a translation's.
        pieces = timed_pieces(seg) if text and text == markup.plain(text_of(seg)).strip() else []
        candidates.append((idx, duration, start, end, text, pieces))

    if not candidates:
        return None

    in_band = [c for c in candidates if _TARGET_MIN_SEC <= c[1] <= _TARGET_MAX_SEC]
    if in_band:
        _idx, _dur, start, end, text, _pieces = max(in_band, key=lambda c: c[1])
        return [(start, end)], [text], [None]

    longest_dur = max(c[1] for c in candidates)
    if longest_dur > _TARGET_MAX_SEC:
        # Case 2 — at least one subtitle exceeds the upper bound. Pick
        # it and clamp from the start (we want the speaker's leading
        # delivery, not the tail that often drifts into silence).
        _idx, dur, start, _end, text, pieces = max(candidates, key=lambda c: c[1])
        return [(start, start + _TARGET_MAX_SEC)], [text], [(dur, pieces)]

    # Case 3 — all subtitles are shorter than _TARGET_MIN_SEC. Walk
    # chronologically, accumulating until we cross the min threshold or
    # would exceed the max.
    candidates.sort(key=lambda c: c[0])  # by subtitle index
    selected_spans: list[tuple[float, float]] = []
    selected_texts: list[str] = []
    selected_clipped: list[tuple[float, list] | None] = []
    total = 0.0
    for _idx, dur, start, end, text, pieces in candidates:
        if total + dur > _TARGET_MAX_SEC:
            # The next chunk would overshoot. Trim it to what fits, but
            # only if the trimmed remainder is meaningful (>0.5 s, below
            # which ffmpeg's atrim produces tail garbage that hurts more
            # than it helps).
            remaining = _TARGET_MAX_SEC - total
            if remaining > 0.5:
                selected_spans.append((start, start + remaining))
                # Cut down to the same words by `_fit_clipped`, as in case 2.
                selected_texts.append(text)
                selected_clipped.append((dur, pieces))
                total += remaining
            break
        selected_spans.append((start, end))
        selected_texts.append(text)
        selected_clipped.append(None)
        total += dur
        if total >= _TARGET_MIN_SEC:
            break

    if not selected_spans:
        return None
    return selected_spans, selected_texts, selected_clipped


# The speech recognition add-on hears this much of the subtitle past a
# cut span's end: the words that end inside the span are then whole, and
# the one after them has a start.
_HEAR_PAST_SEC = 1.5
# A recognition that has not answered by then is given up on.
_HEAR_TIMEOUT_MS = 180_000

# Cut spans being heard now, by `_span_key`: the add-on, its timer, and
# the callbacks waiting for it.
_hearing: dict[str, dict] = {}
# Cut spans the add-on could not time this session (none installed for
# the language, an error): not asked again until Subtitld restarts.
_unheard: set[str] = set()


def _media_key(src: str) -> str:
    return modules_utils.get_cache_key(src) or hashlib.sha1(
        src.encode('utf-8', errors='replace')
    ).hexdigest()[:12]


def _span_key(src: str, start: float, end: float) -> str:
    return f'{_media_key(src)}@{start:.3f}-{end:.3f}'


def _heard_path() -> Path:
    return _cache_dir() / 'heard-words.json'


def _heard_words() -> dict:
    """The words the speech recognition add-on heard in cut spans, by
    `_span_key`: [{'text', 'start', 'end'}], seconds from the span's start."""
    try:
        heard = json.loads(_heard_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return heard if isinstance(heard, dict) else {}


def _keep_heard_words(key: str, words: list) -> None:
    heard = _heard_words()
    heard[key] = words
    try:
        _heard_path().write_text(json.dumps(heard), encoding='utf-8')
    except OSError:
        pass  # heard again next time


def _untimed_cut(src: str, pick) -> tuple[float, float, float] | None:
    """The span of `pick` that is cut short of a subtitle with no word
    times and no words heard in it yet, as (start, end, subtitle
    duration); None for none. Only the last span of a pick is ever cut."""
    spans, texts, clipped = pick
    (start, end), text, clip = spans[-1], texts[-1], clipped[-1]
    if clip is None or not text or clip[1] or _span_key(src, start, end) in _heard_words():
        return None
    return start, end, clip[0]


def when_ready(speaker_name: str, then) -> None:
    """Call `then()` once the speaker's reference can be built with its
    transcript: at once, unless its clip is cut short of a subtitle that
    has no word times. Then the speech recognition add-on hears the cut
    first (`_hear`), once per span (the words are kept on disk), and
    `then` runs when it answers or fails — a cut no add-on could time is
    sent without its text (see `_fit_clipped`)."""
    key = None
    try:
        src = _audio_source_for_clone()
        language = _resolve_source_language()
        pick = _pick_spans_for_speaker(speaker_name) if src and language else None
        cut = _untimed_cut(src, pick) if pick else None
        key = _span_key(src, cut[0], cut[1]) if cut else None
        if key is None or key in _unheard:
            then()
            return
        if key in _hearing:
            _hearing[key]['waiters'].append(then)
            return
        providers = _asr_providers(language)
        if not providers:
            log.info('clone_ref: no speech recognition add-on for %s to time the words of the cut clip', language)
            _unheard.add(key)
            then()
            return
        _hearing[key] = {'waiters': [then]}
        _hear(src, key, cut, language, providers)
    except Exception:
        log.warning('clone_ref: could not time the words of the cut clip', exc_info=True)
        if key is not None:
            _unheard.add(key)
        job = _hearing.pop(key, None) if key is not None else None
        for waiting in (job or {}).get('waiters') or [then]:
            waiting()


def _asr_providers(language: str) -> list:
    """The speech recognition add-ons that are usable now and hear
    `language`, to be tried in turn: the one picked for transcription
    first, then those on this computer, then remote ones."""
    from subtitld.modules import addons
    from subtitld.modules.addons.provider import SOURCE_ENGINES, TASK_ASR_TRANSCRIBE
    manager = addons.get_manager()
    wanted = language.lower().replace('_', '-').split('-')[0]

    def usable(provider) -> bool:
        if provider is None or provider.id in SOURCE_ENGINES or TASK_ASR_TRANSCRIBE not in provider.tasks:
            return False
        try:
            if not provider.is_available():
                return False
            spoken = [str(code).lower().replace('_', '-').split('-')[0] for code in provider.languages or []]
        except Exception:
            return False
        return not spoken or wanted in spoken

    picked = (session.CONFIG.get('transcription') or {}).get('engine')
    found = sorted(manager.providers_for_task(TASK_ASR_TRANSCRIBE),
                   key=lambda p: (p.id != picked, p.compute == 'remote', p.id))
    return [provider for provider in found if usable(provider)]


def _hear(src: str, key: str, cut: tuple[float, float, float], language: str, providers: list,
          wav: Path | None = None) -> None:
    """Have the first of `providers` (a copy of it: the shared one belongs
    to the Audio panel) transcribe the cut span and a little past it, the
    next if it fails; keep the words heard under `key`, and call the
    waiters."""
    from PySide6.QtCore import QTimer
    start, end, duration = cut
    if wav is None:
        length = min(duration, end - start + _HEAR_PAST_SEC)
        wav = _cache_dir() / f'{_media_key(src)}-hearing.wav'
        subprocess.run(_build_ffmpeg_cmd(src, [(start, start + length)], wav), startupinfo=session.STARTUPINFO,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=120)
    provider, rest = providers[0], providers[1:]
    asr = provider.clone()
    timer = QTimer()
    timer.setSingleShot(True)

    def finish(segments) -> None:
        job = _hearing.get(key)
        if job is None or job.get('asr') is not asr:
            return  # answered already, or timed out
        timer.stop()
        try:
            asr.shutdown()
        except Exception:
            pass
        words = _words_heard(segments) if segments is not None else []
        if not words and rest:
            _hear(src, key, cut, language, rest, wav)
            return
        del _hearing[key]
        if words:
            _keep_heard_words(key, words)
        else:
            _unheard.add(key)
        log.info('clone_ref: %s heard %d word(s) in the cut clip', provider.id, len(words))
        for then in job['waiters']:
            try:
                then()
            except Exception:
                log.warning('clone_ref: a dub waiting for the cut clip failed', exc_info=True)

    def failed(message: str) -> None:
        log.warning('clone_ref: %s could not hear the cut clip: %s', provider.id, message)
        finish(None)

    asr.transcript_finished.connect(finish)
    asr.error.connect(failed)
    timer.timeout.connect(lambda: failed('no answer'))
    _hearing[key].update(asr=asr, timer=timer)
    timer.start(_HEAR_TIMEOUT_MS)
    options = (session.CONFIG.get('transcription') or {}).get('engine_options', {}).get(provider.id, {})
    asr.transcribe(str(wav), language, dict(options) if isinstance(options, dict) else {})


def _words_heard(segments) -> list[dict]:
    """The words of an `asr.transcribe` answer, [{'text', 'start', 'end'}]:
    each segment's `words`, or the segment itself from an add-on that
    does not time words."""
    words = []
    for segment in segments if isinstance(segments, list) else []:
        if not isinstance(segment, dict):
            continue
        for piece in segment.get('words') or [segment]:
            try:
                text = str(piece.get('text') or piece.get('word') or '').strip()
                start, end = float(piece['start']), float(piece['end'])
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if text and end >= start:
                words.append({'text': text, 'start': start, 'end': end})
    return words


def _fit_clipped(src: str, spans: list[tuple[float, float]], texts: list[str],
                 clipped: list[tuple[float, list] | None]) -> tuple[list[tuple[float, float]], list[str]]:
    """Cut each clipped span and its text down to the same words (see
    `_fit_span`), timed by the subtitle's own pieces, else by the words
    the speech recognition add-on heard in it (`when_ready`).

    Without either the words in the span aren't known, and a guess does
    not do: Reverb, given 1 s more audio than text, skipped the line's
    first words; given words the audio stops short of, it spoke them
    first, and pauses matched to sentence ends were right on fewer than
    half of one project's subtitles. So such a span goes without text:
    alone, it is sent as it is; after others (a short subtitle's
    remainder), it is left out."""
    heard = _heard_words() if any(clipped) else {}
    fitted_spans: list[tuple[float, float]] = []
    fitted_texts: list[str] = []
    for (start, end), text, clip in zip(spans, texts, clipped):
        if clip is not None:
            _duration, pieces = clip
            words = heard.get(_span_key(src, start, end)) if text and not pieces else None
            if words:
                # The heard words found in the subtitle's text time its
                # pieces; the last found runs on to the text's end, so it
                # is never kept.
                pieces = (timed_by_words(text, words) or [])[:-1]
            if not pieces:
                if len(spans) == 1:
                    return [(start, end)], ['']
                continue
            length, text = _fit_span(end - start, pieces)
            if length <= 0.5:
                continue
            end = start + length
        fitted_spans.append((start, end))
        fitted_texts.append(text)
    if not fitted_spans:
        return spans[:1], ['']
    return fitted_spans, fitted_texts


def _fit_span(keep: float, pieces: list) -> tuple[float, str]:
    """The length of audio and the words spoken in it for a span that may
    last at most `keep` seconds, from the subtitle's timed `pieces`: the
    pieces that end in `keep`, the audio cut halfway to the next one."""
    timed = [p for p in pieces if isinstance(p, dict)]
    kept = [p for p in timed if float(p.get('end', 0.0)) <= keep]
    if not kept:
        return 0.0, ''
    last_end = float(kept[-1]['end'])
    after = timed[len(kept)] if len(kept) < len(timed) else None
    cut = (last_end + float(after.get('start', last_end))) / 2 if after else last_end + 0.1
    words = markup.plain(''.join(str(piece.get('text') or '') for piece in kept)).strip()
    return min(keep, max(last_end, cut)), words


def extract_speaker_reference_with_text(
    speaker_name: str,
) -> tuple[str | None, str | None]:
    """Build (or reuse) a clone-reference WAV for the given speaker, and
    return the matching subtitle transcript for the picked spans.

    Returns `(wav_path, ref_text)` where:
      * `wav_path` is the absolute path of a mono 16 kHz WAV, or None
        if the project doesn't have enough information yet (no source
        media, no subtitles for this speaker, ffmpeg failure).
      * `ref_text` is the concatenation of the subtitle texts spoken in
        the picked spans, separated by single spaces, with whitespace
        collapsed. None if no subtitle text in the audio's language is
        available, even when `wav_path` is set (e.g. silent placeholder
        subtitles, or a project with no language). The caller decides
        whether a missing `ref_text` should be forwarded.

    Why pair the wav with the transcript: clone-capable models
    (Qwen3-TTS, F5-TTS, XTTS) all support an "ICL" mode that takes both
    `ref_audio` and `ref_text`. The qwen3 demo notes the wav-only mode
    has noticeably worse quality and tends to bleed the *training set*'s
    accent into the output (e.g. English accent on a Portuguese clone).
    Subtitld already has the transcription — it's literally the
    subtitle text on those spans — so we just pass it through.

    A span cut short of its subtitle (`_pick_spans_for_speaker`'s
    `clipped`) gets its text and audio cut to the same words by
    `_fit_clipped` first: a transcript that runs past the audio makes a
    model pace and continue the take wrongly. Callers go through
    `when_ready` first, so the words of such a cut have been heard.

    Cache: the WAV is kept with a sidecar `.json` holding the pick it
    was made from and the text that goes with it. It is reused while the
    source media is older than it and the pick (spans and texts) is the
    same; otherwise it is extracted again — a few tenths of a second for
    a 12 s clip, and the clip and its transcript always agree. A clip
    with no sidecar (from before it was kept) is extracted again too.
    """
    src = _audio_source_for_clone()
    if not src:
        log.info('clone_ref: no source media for speaker %s', speaker_name)
        return None, None

    pick = _pick_spans_for_speaker(speaker_name)
    if not pick:
        log.info('clone_ref: speaker %s has no usable subtitle segments', speaker_name)
        return None, None
    spans, texts, clipped = pick

    media_key = _media_key(src)
    speaker_slug = _slugify(speaker_name)
    out_path = _cache_dir() / f'{media_key}-{speaker_slug}.wav'
    sidecar = out_path.with_suffix('.json')
    picked = {'spans': [list(span) for span in spans], 'texts': list(texts)}
    if any(clipped):
        # Words heard in the cut since the clip was made change its text.
        start, end = spans[-1]
        picked['heard'] = _heard_words().get(_span_key(src, start, end))

    if out_path.is_file() and out_path.stat().st_size > 0:
        try:
            cached = json.loads(sidecar.read_text(encoding='utf-8'))
            if out_path.stat().st_mtime >= os.path.getmtime(src) and cached.get('pick') == picked \
                    and 'ref_text' in cached:
                return str(out_path), cached.get('ref_text')
        except (OSError, ValueError, AttributeError):
            pass  # fall through to re-extract

    if any(clipped):
        spans, texts = _fit_clipped(src, spans, texts, clipped)

    # Join with a single space and collapse runs — most subtitle text
    # is already clean, but the editor allows multi-line strings and
    # the model wants a flat one-line transcript. Empty entries (silent
    # placeholders) drop out of the join.
    joined_text = ' '.join(t for t in texts if t)
    joined_text = re.sub(r'\s+', ' ', joined_text).strip()
    ref_text = joined_text or None

    cmd = _build_ffmpeg_cmd(src, spans, out_path)
    try:
        subprocess.run(
            cmd,
            startupinfo=session.STARTUPINFO,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        stderr = getattr(exc, 'stderr', b'') or b''
        if isinstance(stderr, bytes):
            stderr = stderr.decode('utf-8', errors='replace')
        span_desc = ', '.join(f'{s:.2f}-{e:.2f}s' for s, e in spans)
        log.warning('clone_ref: ffmpeg failed for %s spans=[%s]: %s',
                    speaker_name, span_desc, stderr.strip())
        try:
            if out_path.exists() and out_path.stat().st_size == 0:
                out_path.unlink()
        except OSError:
            pass
        return None, None

    try:
        sidecar.write_text(json.dumps({'pick': picked, 'ref_text': ref_text}), encoding='utf-8')
    except OSError:
        pass  # only the next call's reuse is lost
    total = sum(e - s for s, e in spans)
    log.info('clone_ref: extracted %s %d span(s) total=%.2fs ref_text_len=%d -> %s',
             speaker_name, len(spans), total, len(ref_text or ''), out_path.name)
    return str(out_path), ref_text


def extract_speaker_reference(speaker_name: str) -> str | None:
    """Backward-compatible shim: returns just the WAV path.

    Prefer `extract_speaker_reference_with_text` when the caller is
    forwarding the result to a clone-capable add-on — the paired
    `ref_text` materially improves output quality (see that function's
    docstring).
    """
    path, _text = extract_speaker_reference_with_text(speaker_name)
    return path


def _build_ffmpeg_cmd(src: str, spans: list[tuple[float, float]],
                      out_path: Path) -> list[str]:
    """Build the ffmpeg invocation for one or more spans.

    Single-span uses the fast `-ss/-i/-t` form (input-side seek, no
    re-decode of skipped audio). Multi-span uses an `atrim` per span
    plus a `concat` filter — ffmpeg has to decode the file once but the
    output is a single WAV with all spans glued back-to-back, no
    silence between them.
    """
    base = [
        session.FFMPEG_EXECUTABLE,
        '-hide_banner', '-loglevel', 'error', '-y',
    ]
    # Mono 16 kHz PCM is the lowest-common-denominator across the
    # clone-capable add-ons (XTTS resamples internally; F5-TTS reads
    # any rate but mono is required; Qwen3-TTS prefers 16 kHz).
    encode = ['-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le']

    if len(spans) == 1:
        start, end = spans[0]
        duration = max(0.5, end - start)
        return base + [
            '-ss', f'{start:.3f}',
            '-i', src,
            '-t', f'{duration:.3f}',
        ] + encode + [str(out_path)]

    # Multi-span: build a filter graph
    #   [0:a]atrim=start=A:end=B,asetpts=PTS-STARTPTS[a0];
    #   [0:a]atrim=start=C:end=D,asetpts=PTS-STARTPTS[a1];
    #   [a0][a1]concat=n=2:v=0:a=1[out]
    # asetpts is essential — without it, concat will respect the
    # original timestamps and insert silence between spans.
    parts = []
    labels = []
    for i, (start, end) in enumerate(spans):
        label = f'a{i}'
        parts.append(
            f'[0:a]atrim=start={start:.3f}:end={end:.3f},'
            f'asetpts=PTS-STARTPTS[{label}]'
        )
        labels.append(f'[{label}]')
    parts.append(f'{"".join(labels)}concat=n={len(spans)}:v=0:a=1[out]')
    filter_complex = ';'.join(parts)
    return base + [
        '-i', src,
        '-filter_complex', filter_complex,
        '-map', '[out]',
    ] + encode + [str(out_path)]


def should_skip_ref_text(provider_id: str | None) -> bool:
    """True if the user has opted to suppress `voice_ref_text` for this
    add-on.

    Driven by the per-add-on `skip_ref_text` boolean in
    `session.CONFIG['addons']['options'][<id>]`. The dubbing UI sets it via
    the Configure dialog on the add-on card (see `_AddonConfigDialog` in
    `addons_dialog.py`).

    Why this exists: even with the source-language fix (see
    `_resolve_source_language`), some users prefer the addon's auto-
    transcription path because: a) Whisper / the addon's own ASR may
    out-perform the user-edited subtitle text for short cloning windows;
    b) edited subtitles often paraphrase rather than transcribe verbatim,
    which breaks ICL alignment more than missing ref_text would; c)
    privacy-conscious users may not want their subtitle text shipped over
    the wire to a model that logs requests.

    Returns False if `provider_id` is missing or the registry can't be
    consulted (defensive — fail to *sending* ref_text, which is the legacy
    behaviour and gives better clone quality on the common case).
    """
    if not provider_id:
        return False
    try:
        opts = addons_registry.options_for(provider_id)
    except Exception:
        return False
    return bool(opts.get('skip_ref_text'))


def voice_requires_ref_audio(voice: dict | None) -> bool:
    """True if `voice` is a clone-style voice that needs `voice_ref_audio`.

    Detection signal: the `requires` array on the voice entry. We also
    accept a top-level `clone: true` flag for forward-compat (future
    add-ons may declare clone capability without the param being strictly
    required, e.g. if an addon-config-level default is acceptable).
    """
    if not isinstance(voice, dict):
        return False
    requires = voice.get('requires') or []
    if isinstance(requires, list) and 'voice_ref_audio' in requires:
        return True
    return bool(voice.get('clone'))


def provider_has_clone_voice(provider) -> bool:
    """True if the provider declares at least one clone-capable voice."""
    try:
        voices = provider.list_voices() or []
    except Exception:
        return False
    return any(voice_requires_ref_audio(v) for v in voices)


def voice_id_requires_ref_audio(provider, voice_id: str | None) -> bool:
    """True if `voice_id`, on this provider, requires `voice_ref_audio`.

    Drives the auto-injection logic in the dubbing UI: when the user
    picks a clone voice (e.g. `xtts-clone`, `qwen3-clone`, `f5-clone`)
    we always attach the speaker's source audio as the reference,
    bypassing any "use original voice" toggle. Returns False on lookup
    miss so we fail closed (no spurious injection on non-clone voices).
    """
    if not voice_id:
        return False
    try:
        voices = provider.list_voices() or []
    except Exception:
        return False
    for v in voices:
        if isinstance(v, dict) and v.get('id') == voice_id:
            return voice_requires_ref_audio(v)
    return False
