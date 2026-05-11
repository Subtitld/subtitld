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
  subtitle the speaker has and clamp it to 12 s; the addon's own
  validation will surface an error if it's still too short.
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
import logging
import os
import re
import subprocess
from pathlib import Path

from subtitld.modules import session
from subtitld.modules import utils as modules_utils

log = logging.getLogger(__name__)

# Sweet spot for reference clips. Chosen by the most-restrictive add-on:
# F5-TTS asks for 6-15 s. We aim for 12 s upper bound to leave headroom
# for the addon's own validation; lower bound 3 s matches Qwen3-TTS's
# minimum, slightly below XTTS's 6 s — but XTTS will error gracefully if
# it's too short, which is preferable to refusing to extract.
_TARGET_MIN_SEC = 3.0
_TARGET_MAX_SEC = 12.0


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
) -> tuple[list[tuple[float, float]], list[str]] | None:
    """Pick a list of [start, end] spans of source audio for the speaker,
    paired with the subtitle text spoken in each span.

    Returns None if the speaker has no usable subtitles. Otherwise returns
    `(spans, texts)` where the two lists are the same length and in
    chronological order — `spans` is ready to feed an ffmpeg concat
    filter, `texts` is ready to be joined into a `ref_text` for ICL-mode
    voice cloning.

    Strategy:
      1. **One in-band subtitle exists** — return a single span for the
         longest subtitle in [_TARGET_MIN_SEC, _TARGET_MAX_SEC]. This is
         the common case and gives the cleanest reference.
      2. **All subtitles are too LONG** — return a single span clamped
         from the start of the longest one to _TARGET_MAX_SEC. The
         paired text is the *full* subtitle text even though we only
         use the first _TARGET_MAX_SEC of audio: clone-capable models
         (Qwen3-TTS, F5-TTS, XTTS) handle a slight ref_text/audio
         length mismatch better than they handle missing ref_text — and
         we don't have word-level timing to truncate the text more
         precisely. Worst case the model treats the trailing transcript
         words as anchoring information, which still helps language ID.
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
    segments = session.SUBTITLE.get('segments') or []
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
        text = (seg.get('text') or '').strip()
        candidates.append((idx, duration, start, end, text))

    if not candidates:
        return None

    in_band = [c for c in candidates if _TARGET_MIN_SEC <= c[1] <= _TARGET_MAX_SEC]
    if in_band:
        _idx, _dur, start, end, text = max(in_band, key=lambda c: c[1])
        return [(start, end)], [text]

    longest_dur = max(c[1] for c in candidates)
    if longest_dur > _TARGET_MAX_SEC:
        # Case 2 — at least one subtitle exceeds the upper bound. Pick
        # it and clamp from the start (we want the speaker's leading
        # delivery, not the tail that often drifts into silence).
        _idx, _dur, start, _end, text = max(candidates, key=lambda c: c[1])
        return [(start, start + _TARGET_MAX_SEC)], [text]

    # Case 3 — all subtitles are shorter than _TARGET_MIN_SEC. Walk
    # chronologically, accumulating until we cross the min threshold or
    # would exceed the max.
    candidates.sort(key=lambda c: c[0])  # by subtitle index
    selected_spans: list[tuple[float, float]] = []
    selected_texts: list[str] = []
    total = 0.0
    for _idx, dur, start, end, text in candidates:
        if total + dur > _TARGET_MAX_SEC:
            # The next chunk would overshoot. Trim it to what fits, but
            # only if the trimmed remainder is meaningful (>0.5 s, below
            # which ffmpeg's atrim produces tail garbage that hurts more
            # than it helps).
            remaining = _TARGET_MAX_SEC - total
            if remaining > 0.5:
                selected_spans.append((start, start + remaining))
                # Same trade-off as case 2 — keep the full text rather
                # than character-cropping mid-word.
                selected_texts.append(text)
                total += remaining
            break
        selected_spans.append((start, end))
        selected_texts.append(text)
        total += dur
        if total >= _TARGET_MIN_SEC:
            break

    if not selected_spans:
        return None
    return selected_spans, selected_texts


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
        collapsed. Empty string if no subtitle text is available, even
        when `wav_path` is set (e.g. silent placeholder subtitles). The
        caller decides whether an empty `ref_text` should be forwarded.

    Why pair the wav with the transcript: clone-capable models
    (Qwen3-TTS, F5-TTS, XTTS) all support an "ICL" mode that takes both
    `ref_audio` and `ref_text`. The qwen3 demo notes the wav-only mode
    has noticeably worse quality and tends to bleed the *training set*'s
    accent into the output (e.g. English accent on a Portuguese clone).
    Subtitld already has the transcription — it's literally the
    subtitle text on those spans — so we just pass it through.

    Cache invalidation: the WAV is regenerated if the source media has
    been modified after the cache file. We don't try to detect subtitle
    boundary edits — those are common during editing and would thrash
    the cache; a stale clip is rarely worse than re-extracting on every
    `generate all speeches` click. The text is rebuilt on every call
    (cheap) so it always reflects the *current* subtitle text even when
    the cached WAV is reused — addons re-run inference per call anyway,
    so a fresher text + slightly stale audio is a net win.
    """
    src = _audio_source_for_clone()
    if not src:
        log.info('clone_ref: no source media for speaker %s', speaker_name)
        return None, None

    pick = _pick_spans_for_speaker(speaker_name)
    if not pick:
        log.info('clone_ref: speaker %s has no usable subtitle segments', speaker_name)
        return None, None
    spans, texts = pick

    # Join with a single space and collapse runs — most subtitle text
    # is already clean, but the editor allows multi-line strings and
    # the model wants a flat one-line transcript. Empty entries (silent
    # placeholders) drop out of the join.
    joined_text = ' '.join(t for t in texts if t)
    joined_text = re.sub(r'\s+', ' ', joined_text).strip()
    ref_text = joined_text or None

    media_key = modules_utils.get_cache_key(src) or hashlib.sha1(
        src.encode('utf-8', errors='replace')
    ).hexdigest()[:12]
    speaker_slug = _slugify(speaker_name)
    out_path = _cache_dir() / f'{media_key}-{speaker_slug}.wav'

    if out_path.is_file() and out_path.stat().st_size > 0:
        try:
            if out_path.stat().st_mtime >= os.path.getmtime(src):
                return str(out_path), ref_text
        except OSError:
            pass  # fall through to re-extract

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
