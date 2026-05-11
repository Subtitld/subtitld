"""Time-stretch a TTS-generated WAV via ffmpeg's `atempo` filter.

Local neural TTS engines (Piper, Coqui XTTS, etc.) synthesize at a fixed
speaking rate — there's no per-call `rate` knob like Edge TTS exposes. To
keep subtitld's "rate slider" UX consistent across providers we time-stretch
the engine's raw output here, host-side, after synthesis.

The raw WAV is never overwritten. Each rate produces a sibling cache file
named `<stem>__r<+|-><N>.wav`, so reopening a project with the same rate
is free, and the user can tweak the rate without re-running the engine.

Why atempo and not rubberband: we tested both and atempo (WSOLA) sounds
noticeably cleaner than rubberband on neural-TTS speech. The phase-vocoder
in rubberband interacts poorly with the HiFi-GAN-style vocoder used by
Piper / Coqui — the result is a metallic / phasey artifact on consonants
that persists even with the speech-tuned settings (`pitchq=consistency`,
`detector=soft`, `transients=smooth`, etc). atempo doesn't preserve formants,
so a +30 stretch lifts pitch ~5 semitones — but listeners consistently
prefer the slight chipmunk to the metallic ringing.

atempo's per-instance range is 0.5..2.0; we chain instances to extend the
range past those corners. subtitld's UI clamps rate to ±100 which gives
factor 0..2 — at the limits we still split for safety.

This module deliberately doesn't import any heavy audio libs — `subprocess`
+ `Path` only. The ffmpeg call itself is the only cost.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from subtitld.modules import session

log = logging.getLogger(__name__)


# rate is in subtitld's UI units: -100..100, where 0 is the engine's natural
# speed. We map to ffmpeg's `tempo` (a speed multiplier) as `1 + rate/100`,
# so +50 → 1.5× (faster, shorter), -25 → 0.75× (slower, longer).
#
# Below this threshold we treat the rate as a no-op and return the raw
# unchanged. Sub-1 % stretches aren't audibly different from the source and
# would just churn ffmpeg invocations during slider drags.
_RATE_NOOP_THRESHOLD = 1


def _rendered_path_for(raw_path: Path, rate_pct: int) -> Path:
    """Sibling-cache filename for `(raw, rate)`. Lives next to the raw so
    the cache shares its lifecycle (both wiped together when subtitld's
    user-cache dir is cleared).
    """
    sign = '+' if rate_pct >= 0 else '-'
    return raw_path.with_name(f'{raw_path.stem}__r{sign}{abs(int(rate_pct))}{raw_path.suffix}')


def _build_filter_chain(rate_pct: int) -> str:
    """Build a chained `atempo` filter for the given rate.

    atempo only accepts 0.5..2.0 in a single instance. We chain 2.0 / 0.5
    pre-stages for any factor outside that band, then append the residual.
    """
    factor = 1.0 + rate_pct / 100.0

    parts: list[str] = []
    remaining = factor
    while remaining > 2.0:
        parts.append('atempo=2.0')
        remaining /= 2.0
    while remaining < 0.5:
        parts.append('atempo=0.5')
        remaining /= 0.5
    parts.append(f'atempo={remaining:.4f}')
    return ','.join(parts)


def stretch_by_rate(raw_path: Path | str, rate_pct: int) -> Path:
    """Time-stretch `raw_path` by `rate_pct` (subtitld UI units, -100..100).

    Returns the path to play. `rate_pct == 0` (or near-zero) is a no-op —
    we return the raw path unchanged. Otherwise we render to a sibling
    `<stem>__r<sign><N>.wav` and return that. Re-renders are skipped if
    the destination already exists and is non-empty (cached).

    On ffmpeg failure we log and fall back to the raw — the user gets
    untouched audio rather than no audio.
    """
    raw_path = Path(raw_path)
    rate_pct = int(rate_pct or 0)

    if abs(rate_pct) < _RATE_NOOP_THRESHOLD:
        return raw_path
    if not raw_path.is_file():
        log.warning('audio_stretch: raw %s missing, returning as-is', raw_path)
        return raw_path

    rendered = _rendered_path_for(raw_path, rate_pct)
    # Cache hit: the user opened a project, or moved the rate slider back to
    # a value we've already rendered for this raw. Skip the ffmpeg round-trip.
    if rendered.is_file() and rendered.stat().st_size > 0:
        return rendered

    chain = _build_filter_chain(rate_pct)
    cmd = [
        session.FFMPEG_EXECUTABLE,
        '-hide_banner', '-loglevel', 'error',
        '-y', '-i', str(raw_path),
        '-af', chain,
        # Match subtitld's mixdown sample format. ffmpeg defaults to whatever
        # the source had, which is fine for piper's 22050 Hz mono — kept
        # explicit so future changes to addon output formats don't surprise us.
        '-ac', '1',
        str(rendered),
    ]
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
        log.warning('audio_stretch: ffmpeg %s failed for %s rate=%+d: %s',
                    chain, raw_path, rate_pct, stderr.strip())
        # Clean up partial output so a retry isn't tricked by the cache check.
        try:
            if rendered.exists() and rendered.stat().st_size == 0:
                rendered.unlink()
        except OSError:
            pass
        return raw_path

    log.info('audio_stretch: %s rate=%+d -> %s (%s)',
             raw_path.name, rate_pct, rendered.name, chain)
    return rendered
