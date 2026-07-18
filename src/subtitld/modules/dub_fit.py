"""Optionally fit a freshly-generated TTS dub to its subtitle's duration.

When a speaker has "fit dub to subtitle duration" enabled, the clip produced
by ANY TTS provider is time-stretched — once, right after generation — so it
plays in exactly the subtitle's on-screen duration:

  * generated audio LONGER than the subtitle  → sped up (stretched down)
  * generated audio SHORTER than the subtitle → slowed down (stretched up)

The stretched clip becomes the active dub (``subtitle['dubbing'][0]``) and the
un-stretched original is preserved right behind it (``dubbing[1]``) so nothing
is lost — the user keeps the original as an option in the dub list.

This runs on the main thread from each provider's ``_on_speech_ready`` (the
only ffmpeg cost is a single cached ``atempo`` render), so it stays a leaf
module: no Qt, no provider imports.
"""

from __future__ import annotations

from pathlib import Path

from subtitld.modules import session, audio_stretch, dub_clip


def speaker_fit_enabled(speaker_name: str | None) -> bool:
    """Whether the given speaker has auto-fit turned on."""
    dubbing = (session.SPEAKERS.get(speaker_name or 'A', {}) or {}).get('dubbing') or {}
    return bool(dubbing.get('fit_to_subtitle', False))


def maybe_fit_dub(subtitle: dict, allow: bool = True) -> bool:
    """If the subtitle's speaker has auto-fit enabled, replace ``dubbing[0]``
    with a copy time-stretched to the subtitle's duration and push the
    original down to ``dubbing[1]``.

    `allow` is a caller gate: a re-render that was itself triggered by a
    *manual* stretch passes ``allow=False`` so the auto-fit doesn't snap the
    hand-resized clip back to the subtitle width.

    The fit is encoded as the dub's ``rate`` (speed %, where rendered
    duration == raw_duration / (1 + rate/100)) rather than an opaque one-off
    render. That matters because:
      * the on-screen speed label reads the dub's ``rate`` — a naive fit that
        left ``rate == 0`` would show "1.000x" on a clip that is clearly
        stretched;
      * the manual-stretch machinery derives the next rate from the current
        one (``new_rate = (100 + rate) * drag_ratio - 100``) — so a wrong
        ``rate`` made a later manual stretch resize from the wrong baseline.

    Returns True if a fitted clip was inserted, False on any no-op.
    """
    if not allow:
        return False
    if not isinstance(subtitle, dict):
        return False
    if not speaker_fit_enabled(subtitle.get('speaker', 'A')):
        return False

    dubs = subtitle.get('dubbing') or []
    if not dubs:
        return False
    original = dubs[0]
    if not isinstance(original, dict) or original.get('fitted'):
        return False

    target = float(subtitle.get('end', 0.0) or 0.0) - float(subtitle.get('start', 0.0) or 0.0)
    if target <= 0:
        return False

    # Fit against the engine's untouched output — the dub's `rate` is defined
    # relative to that raw, and so is the stretch machinery that consumes it.
    raw_path = original.get('raw_path') or original.get('path')
    if not raw_path or not Path(raw_path).is_file():
        return False
    raw_dur = dub_clip._file_duration_seconds(raw_path)
    if raw_dur <= 0:
        return False

    # rendered_dur == raw_dur / (1 + rate/100) == target  ->  the rate that fits.
    fit_rate = int(round((raw_dur / target - 1.0) * 100))
    fit_rate = max(-100, min(100, fit_rate))
    if fit_rate == int(original.get('rate', 0) or 0):
        return False  # the freshly generated clip already fits at its own rate

    rendered = str(audio_stretch.stretch_by_rate(Path(raw_path), fit_rate))
    if rendered == str(raw_path):
        return False  # rate too small to matter — leave the original active

    # A fresh clip: its timeline width comes from the (now target-length) file,
    # so drop any inherited `segments` cache and let dub_clip re-probe.
    fitted = dict(original)
    fitted.pop('segments', None)
    fitted['path'] = rendered
    fitted['raw_path'] = str(raw_path)
    fitted['rate'] = fit_rate
    fitted['fitted'] = True
    fitted['fit_target'] = target
    fitted['uid'] = f"{original.get('uid', '')}_fit"

    dubs[0] = fitted
    dubs.insert(1, original)
    return True
