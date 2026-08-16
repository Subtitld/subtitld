"""Helpers for the dub-clip subclip list.

A dub take in `subtitle['dubbing']` represents *what gets played* for a
subtitle. The legacy model was a single rendered audio file pointed at by
`dub['path']`. The new model is a list of *independent subclips* the audio
engine plays as parallel placed clips (DAW-style):

    dub['segments'] = [
        {'type': 'audio', 'path': '...', 'start': 0.0, 'end': 1.0, 'offset': 0.0},
        {'type': 'audio', 'path': '...', 'start': 1.0, 'end': 2.9, 'offset': 1.5},
    ]

Field semantics for each subclip:
  - `start` / `end` are SOURCE-file offsets, in seconds. They define the
    region of the on-disk file that plays.
  - `offset` is the TIMELINE offset of this subclip's start, measured from
    `dub['start']`. So the subclip plays from `dub['start'] + offset` to
    `dub['start'] + offset + (end - start)`.

Subclips are independent — gaps and overlaps are both allowed (the audio
engine sums overlapping subclips, the absence of a subclip is silence).
Source files on disk are never modified, so undo is just data.

`dub['path']` is kept as the legacy fallback so old projects (and the
generic dub-peaks pipeline that draws the waveform on the timeline) keep
working. Code paths that need the per-subclip view call `normalize_segments`
to lazily synthesise a one-subclip list from the legacy path.

Realtime contract: file I/O only in `normalize_segments` /
`clip_total_duration` for the legacy fallback. Never call those from the
audio callback. The audio engine reads `dub['segments']` directly.
"""

from __future__ import annotations

import os
from typing import Iterable

import soundfile as sf


def effective_muted(dub: dict, index: int) -> bool:
    """Whether a dub clip is silent. A clip's default mute state is `index != 0`
    (so `[0]` — the default take — plays and alternates are silent), overridable
    with an explicit `'muted'` flag. See `audioengine._first_unmuted_dub`."""
    return bool(dub.get('muted', index != 0))


def solo_dub(subtitle: dict, dub: dict) -> None:
    """Make `dub` the only audible clip for this subtitle (mute every other
    take). Used when the user picks an alternate to listen to."""
    for d in subtitle.get('dubbing') or []:
        d['muted'] = (d is not dub)


def promote_dub(subtitle: dict, dub: dict) -> None:
    """Make `dub` the default: move it to index 0 and mark it the audible one
    (the others become muted alternates)."""
    dubs = subtitle.get('dubbing')
    if not dubs or dub not in dubs:
        return
    dubs.remove(dub)
    dubs.insert(0, dub)
    for i, d in enumerate(dubs):
        d['muted'] = (i != 0)


def _audio_subclip(path: str, source_start: float = 0.0,
                   source_end: float | None = None, offset: float = 0.0) -> dict:
    return {'type': 'audio', 'path': path,
            'start': source_start, 'end': source_end, 'offset': offset}


def _file_duration_seconds(path: str) -> float:
    """Read a sound file's total duration. 0.0 on any failure (missing file,
    bad header, etc.) so callers don't need to wrap every probe in a try."""
    if not path or not os.path.isfile(path):
        return 0.0
    try:
        with sf.SoundFile(path, 'r') as f:
            if f.samplerate <= 0:
                return 0.0
            return f.frames / f.samplerate
    except Exception:
        return 0.0


def _migrate_legacy_sequential(segments: list[dict]) -> None:
    """In-place migration of old-schema sequential segments (silence
    entries + audio entries without `offset`, played in order) into the
    new independent-subclip schema (audio-only with `offset`).

    Triggered when any segment is `type=silence` OR lacks the `offset`
    field. Silences collapse into the gap between adjacent subclips."""
    if not segments:
        return
    if not any(s.get('type', 'audio') == 'silence' or 'offset' not in s
               for s in segments):
        return
    cursor = 0.0
    new_list: list[dict] = []
    for seg in segments:
        seg_type = seg.get('type', 'audio')
        if seg_type == 'silence':
            cursor += max(0.0, float(seg.get('duration', 0.0) or 0.0))
            continue
        end = seg.get('end')
        if end is None:
            # Skip un-resolvable entries on migration — they were
            # placeholders in the old schema too.
            continue
        dur = max(0.0, float(end) - float(seg.get('start', 0.0)))
        new_seg = dict(seg)
        new_seg['offset'] = cursor
        new_seg.pop('duration', None)
        new_list.append(new_seg)
        cursor += dur
    segments[:] = new_list


def normalize_segments(dub: dict) -> list[dict]:
    """Ensure `dub` has a `segments` list of new-schema subclips.

    - Missing segments: synthesise one subclip from the legacy `dub['path']`.
      Probes the source file for duration so the subclip carries a concrete
      `end`. MAIN THREAD ONLY (file I/O).
    - Old-schema segments (silence entries, missing `offset`): migrate
      in place — silences collapse into gaps between offsets.

    Returns the list (mutates `dub`)."""
    segments = dub.get('segments')
    if segments is not None:
        _migrate_legacy_sequential(segments)
        return segments
    path = dub.get('path')
    if not path:
        dub['segments'] = []
        return dub['segments']
    end = _file_duration_seconds(path) or None
    dub['segments'] = [_audio_subclip(path, 0.0, end, 0.0)]
    return dub['segments']


def subclip_duration(seg: dict) -> float:
    """Playback duration of one subclip — `end - start` in source seconds.
    Independent of `offset`. Returns 0 for unresolved `end`."""
    end = seg.get('end')
    if end is None:
        return 0.0
    return max(0.0, float(end) - float(seg.get('start', 0.0)))


# Legacy alias — older call sites used `segment_duration`. Same semantic
# in the new schema (duration of the playback region in source seconds).
segment_duration = subclip_duration


def subclip_timeline_range(dub: dict, seg: dict) -> tuple[float, float]:
    """Return (timeline_start, timeline_end) for a subclip — its
    `offset` + `dub['start']` plus its duration."""
    base = float(dub.get('start', 0.0))
    offset = float(seg.get('offset', 0.0))
    dur = subclip_duration(seg)
    return (base + offset, base + offset + dur)


def clip_extent(dub: dict) -> tuple[float, float]:
    """Return the (min_timeline, max_timeline) bounding interval of the
    dub. The dub band's outer rect is drawn over this range. For empty
    dubs returns (dub_start, dub_start)."""
    base = float(dub.get('start', 0.0))
    segments = dub.get('segments')
    if not segments:
        end = _file_duration_seconds(dub.get('path', ''))
        return (base, base + end)
    lo = None
    hi = None
    for seg in segments:
        if seg.get('type', 'audio') != 'audio':
            continue
        end = seg.get('end')
        if end is None:
            continue
        offset = float(seg.get('offset', 0.0))
        dur = float(end) - float(seg.get('start', 0.0))
        t0 = base + offset
        t1 = t0 + dur
        if lo is None or t0 < lo:
            lo = t0
        if hi is None or t1 > hi:
            hi = t1
    if lo is None:
        return (base, base)
    return (lo, hi)


def clip_total_duration(dub: dict) -> float:
    """Width of the dub's extent on the timeline (max_end - min_start).
    Used for the dub band's outer rect."""
    lo, hi = clip_extent(dub)
    return max(0.0, hi - lo)


def iter_segment_ranges(dub: dict) -> Iterable[tuple[float, float, dict]]:
    """Yield `(timeline_start, timeline_end, segment)` for each audio
    subclip. Order matches the segments list (NOT sorted by timeline_start).

    For legacy dubs (no segments field), yields a single synthesised
    subclip — probes the source file for duration (MAIN THREAD ONLY)."""
    base = float(dub.get('start', 0.0))
    segments = dub.get('segments')
    if not segments:
        path = dub.get('path')
        if not path:
            return
        end = _file_duration_seconds(path)
        seg = _audio_subclip(path, 0.0, end, 0.0)
        yield (base, base + end, seg)
        return
    for seg in segments:
        if seg.get('type', 'audio') != 'audio':
            continue
        end = seg.get('end')
        if end is None:
            continue
        offset = float(seg.get('offset', 0.0))
        dur = float(end) - float(seg.get('start', 0.0))
        yield (base + offset, base + offset + dur, seg)


def collect_segment_paths(dub: dict) -> list[str]:
    """Unique audio paths referenced by `dub`'s subclips (or just
    `dub['path']` for legacy dubs). Order preserved so the bg loader
    queues the first subclip first."""
    seen: set[str] = set()
    out: list[str] = []
    segments = dub.get('segments')
    if not segments:
        path = dub.get('path')
        if path:
            out.append(path)
        return out
    for seg in segments:
        if seg.get('type', 'audio') != 'audio':
            continue
        path = seg.get('path')
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return out


# ---------------------------------------------------------------------------
# Editing helpers — pure data, no I/O.
# ---------------------------------------------------------------------------
def split_audio_segment(dub: dict, segment_index: int,
                        offset_within_segment: float) -> bool:
    """Split the subclip at `segment_index` into two independent halves
    at `offset_within_segment` seconds from its playback start. Together
    the halves cover the same timeline range and the same source range
    as the original — the user can then drag them apart.

    Returns True on success. False on bad index, non-audio subclip, or
    when the cut falls outside the subclip (a no-op split would create a
    zero-duration subclip)."""
    segments = normalize_segments(dub)
    if not (0 <= segment_index < len(segments)):
        return False
    seg = segments[segment_index]
    if seg.get('type', 'audio') != 'audio':
        return False
    source_start = float(seg.get('start', 0.0))
    end = seg.get('end')
    if end is None:
        return False
    source_end = float(end)
    seg_dur = source_end - source_start
    cut = max(0.0, float(offset_within_segment))
    if cut <= 0.0 or cut >= seg_dur:
        return False
    cut_source = source_start + cut
    seg_offset = float(seg.get('offset', 0.0))
    new_seg = dict(seg)
    seg['end'] = cut_source
    new_seg['start'] = cut_source
    new_seg['end'] = source_end
    new_seg['offset'] = seg_offset + cut
    segments.insert(segment_index + 1, new_seg)
    return True


def move_subclip(dub: dict, segment_index: int,
                 new_timeline_offset: float) -> bool:
    """Drop a subclip at a new `offset` (from `dub['start']`). Source
    range unchanged — this is the body-drag interaction.

    Negative offsets are allowed: the subclip can sit before `dub['start']`
    on the timeline (and therefore before the subtitle's own start). The
    audio engine doesn't enforce subtitle bounds — only the timeline
    window matters — so this just makes the dub physically extend past
    the subtitle rect when drawn."""
    segments = normalize_segments(dub)
    if not (0 <= segment_index < len(segments)):
        return False
    seg = segments[segment_index]
    if seg.get('type', 'audio') != 'audio':
        return False
    seg['offset'] = float(new_timeline_offset)
    return True


def trim_subclip_left(dub: dict, segment_index: int,
                      new_timeline_offset: float,
                      source_min: float = 0.0) -> bool:
    """Move the left edge of a subclip. `offset` and source `start`
    shift by the same delta so the audio under the new edge is the same
    sample that was under the previous edge — there's no glitch.

    Clamped so source `start` stays in [source_min, end). Returns True
    if the move was applied."""
    segments = normalize_segments(dub)
    if not (0 <= segment_index < len(segments)):
        return False
    seg = segments[segment_index]
    if seg.get('type', 'audio') != 'audio':
        return False
    end = seg.get('end')
    if end is None:
        return False
    old_offset = float(seg.get('offset', 0.0))
    old_source_start = float(seg.get('start', 0.0))
    delta = float(new_timeline_offset) - old_offset
    new_source_start = old_source_start + delta
    if new_source_start < source_min:
        # Clamp by hitting the source floor — also clamp the offset by
        # the same amount so the visible left edge moves in lock-step.
        clamp_delta = source_min - new_source_start
        new_source_start = source_min
        new_offset = old_offset + delta + clamp_delta
    else:
        new_offset = float(new_timeline_offset)
    if new_source_start >= float(end):
        return False
    # Negative offsets are intentional — the subclip can extend before
    # `dub['start']` (and thus before the subtitle's own start). Source
    # bounds (`source_min`, `end`) are the real constraints; the offset
    # is just visual placement.
    seg['start'] = new_source_start
    seg['offset'] = new_offset
    return True


def trim_subclip_right(dub: dict, segment_index: int,
                       new_timeline_end_offset: float,
                       source_max: float | None = None) -> bool:
    """Move the right edge of a subclip. Adjusts source `end` so the
    audio cuts off at the new edge. Clamped so source `end` is > `start`
    and <= source_max (when provided — typically the source-file
    duration)."""
    segments = normalize_segments(dub)
    if not (0 <= segment_index < len(segments)):
        return False
    seg = segments[segment_index]
    if seg.get('type', 'audio') != 'audio':
        return False
    seg_offset = float(seg.get('offset', 0.0))
    source_start = float(seg.get('start', 0.0))
    new_dur = float(new_timeline_end_offset) - seg_offset
    if new_dur <= 0:
        return False
    new_source_end = source_start + new_dur
    if source_max is not None and new_source_end > source_max:
        new_source_end = source_max
    if new_source_end <= source_start:
        return False
    seg['end'] = new_source_end
    return True


def remove_segment(dub: dict, segment_index: int) -> bool:
    """Drop the subclip at `segment_index`. No-op if index is out of range."""
    segments = normalize_segments(dub)
    if not (0 <= segment_index < len(segments)):
        return False
    segments.pop(segment_index)
    return True


def restretch_active_dub(subtitle: dict, ratio: float) -> bool:
    """Host-side time-stretch the active dub (``dubbing[0]``) by ``ratio`` via
    ffmpeg ``atempo`` — a *deterministic* stretch, so the clip lands on exactly
    the width the user dragged to. Mutates the dub in place (path / raw_path /
    rate and the single-subclip ``segments`` cache) and returns True if it
    changed, False on a no-op (no dub, bad ratio, missing raw, or the new rate
    equals the current one).

    Shared by the add-on and edge-tts providers' ``stretch()``. UI refresh is
    the caller's responsibility. The atempo path is why this is reliable where
    a cloud re-synth (edge's old behaviour) was not: edge's SSML ``rate`` maps
    non-linearly to output duration, so the result rarely matched the drag.
    """
    from pathlib import Path
    from subtitld.modules import session, audio_stretch

    if ratio <= 0 or not subtitle:
        return False
    dubs = subtitle.get('dubbing') or []
    if not dubs:
        return False
    current_dub = dubs[0]
    # Dubs from other engines / old projects may lack `raw_path`; treat `path`
    # as the raw so the first stretch lands a sibling cache and later stretches
    # always re-render from that same untouched source (no quality compounding).
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
    overrides['rate'] = new_rate

    # Keep a materialised single-subclip cache in sync with the new file so the
    # timeline's width (read from `seg['end']`) tracks the stretch. Only touch a
    # single-subclip dub; user-split multi-subclip dubs stretch via the
    # timeline's `_apply_subclip_stretch` and never reach here.
    segments = current_dub.get('segments')
    if segments and len(segments) == 1 and segments[0].get('type', 'audio') == 'audio':
        seg = segments[0]
        seg['path'] = rendered_path
        seg.setdefault('raw_path', str(raw_path))
        seg['rate'] = new_rate
        old_end = seg.get('end')
        if old_end is None:
            new_dur = _file_duration_seconds(rendered_path)
            if new_dur > 0:
                seg['start'] = 0.0
                seg['end'] = new_dur
                seg['offset'] = 0.0
        else:
            # Scale source-region coords by the rate-change factor so a prior
            # trim survives. `offset` is a TIMELINE position, NOT a source
            # coord — never scale it (see addon_provider.stretch history).
            current_factor = 1.0 + current_rate / 100.0
            new_factor = 1.0 + new_rate / 100.0
            scale = current_factor / max(new_factor, 1e-6)
            seg['start'] = float(seg.get('start', 0.0)) * scale
            seg['end'] = float(old_end) * scale
    return True
