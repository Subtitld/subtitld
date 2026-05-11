"""Helpers for the dub-clip segment list.

A dub take in `subtitle['dubbing']` represents *what gets played* for a
subtitle. Historically each take was a single rendered audio file pointed at
by `dub['path']`. We now model a take as a *list of segments* the audio
engine plays in sequence:

    dub['segments'] = [
        {'type': 'audio', 'path': '...', 'start': 0.0, 'end': 1.7},
        {'type': 'silence', 'duration': 0.4},
        {'type': 'audio', 'path': '...', 'start': 1.7, 'end': 2.9},
    ]

This subsumes three operations from one schema:

  - **Split**     – one `audio` segment becomes two pointing at the same
    source with adjusted start/end offsets.
  - **Insert gap** – insert a `silence` segment between two segments.
  - **Trim range** – split, then remove the middle segment.

Source files on disk are never modified, so undo is just data.

`dub['path']` is kept as the legacy fallback so old projects (and the
generic dub-peaks pipeline that draws the waveform on the timeline) keep
working. Code paths that need the per-segment view call `normalize_segments`
to lazily synthesise a one-segment list from the legacy path.

Realtime contract: this module only does file I/O in `normalize_segments` /
`clip_total_duration` (the legacy fallback probes the source file once to
fill `end`). Both are MAIN-THREAD only — never call them from the audio
callback. The audio engine reads `dub['segments']` directly and bails to
its existing single-path code if missing.
"""

from __future__ import annotations

import os
from typing import Iterable

import soundfile as sf


def _audio_segment_default(path: str, end: float | None = None) -> dict:
    return {'type': 'audio', 'path': path, 'start': 0.0, 'end': end}


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


def normalize_segments(dub: dict) -> list[dict]:
    """Ensure `dub` has a `segments` list; synthesise one from the legacy
    `dub['path']` if missing. Probes the source file for total duration so
    the synthesised segment carries a concrete `end` (the audio engine and
    UI never have to re-probe). Returns the list (mutates `dub`).

    MAIN THREAD ONLY — opens the source file."""
    segments = dub.get('segments')
    if segments is not None:
        return segments
    path = dub.get('path')
    if not path:
        dub['segments'] = []
        return dub['segments']
    end = _file_duration_seconds(path) or None
    dub['segments'] = [_audio_segment_default(path, end)]
    return dub['segments']


def segment_duration(seg: dict) -> float:
    """Duration in seconds the segment occupies in the dub timeline.
    Audio segments must have a concrete `end` — a missing `end` returns 0
    (the file-probe responsibility lives in `normalize_segments`, not in
    the realtime audio loop)."""
    seg_type = seg.get('type', 'audio')
    if seg_type == 'silence':
        return max(0.0, float(seg.get('duration', 0.0) or 0.0))
    end = seg.get('end')
    if end is None:
        return 0.0
    return max(0.0, float(end) - float(seg.get('start', 0.0)))


def clip_total_duration(dub: dict) -> float:
    """Total playback duration of the dub, summing all segments. Falls
    back to a one-shot file probe of the legacy `dub['path']` when there
    are no segments. MAIN THREAD ONLY for the legacy path."""
    segments = dub.get('segments')
    if not segments:
        return _file_duration_seconds(dub.get('path', ''))
    return sum(segment_duration(seg) for seg in segments)


def iter_segment_ranges(dub: dict) -> Iterable[tuple[float, float, dict]]:
    """Yield `(start_time, end_time, segment)` tuples laid out sequentially
    starting at `dub['start']`. For legacy dubs with no segments field, yields
    a single synthesised audio segment (and probes the file for duration —
    MAIN THREAD ONLY in that path)."""
    base = float(dub.get('start', 0.0))
    cursor = base
    segments = dub.get('segments')
    if not segments:
        path = dub.get('path')
        if not path:
            return
        end = _file_duration_seconds(path)
        seg = _audio_segment_default(path, end)
        yield (cursor, cursor + end, seg)
        return
    for seg in segments:
        d = segment_duration(seg)
        yield (cursor, cursor + d, seg)
        cursor += d


# ---------------------------------------------------------------------------
# Editing helpers — pure data, no I/O
# ---------------------------------------------------------------------------
def split_audio_segment(dub: dict, segment_index: int, offset_within_segment: float) -> bool:
    """Split the audio segment at `segment_index` into two at
    `offset_within_segment` seconds from its playback start. Both halves
    point at the same source file; the cut moves `start` / `end` offsets.

    Returns True if a split happened. Returns False on bad index, non-audio
    segment, or when the cut falls outside the segment's range (a no-op
    split would create a zero-duration segment, which we don't want)."""
    segments = normalize_segments(dub)
    if not (0 <= segment_index < len(segments)):
        return False
    seg = segments[segment_index]
    if seg.get('type', 'audio') != 'audio':
        return False
    seg_start = float(seg.get('start', 0.0))
    end = seg.get('end')
    if end is None:
        return False
    seg_end = float(end)
    cut_offset = max(0.0, float(offset_within_segment))
    cut_point = seg_start + min(cut_offset, seg_end - seg_start)
    if cut_point <= seg_start or cut_point >= seg_end:
        return False
    new_seg = dict(seg)
    seg['end'] = cut_point
    new_seg['start'] = cut_point
    new_seg['end'] = seg_end
    segments.insert(segment_index + 1, new_seg)
    return True


def insert_silence_after(dub: dict, segment_index: int, duration: float) -> int:
    """Insert a silence segment after `segment_index`. Returns the new
    segment's index (or -1 on bad input). Pass `segment_index=-1` to
    insert at the very start of the dub."""
    segments = normalize_segments(dub)
    duration = max(0.0, float(duration))
    if duration <= 0.0 or segment_index < -1 or segment_index >= len(segments):
        return -1
    insert_at = segment_index + 1
    segments.insert(insert_at, {'type': 'silence', 'duration': duration})
    return insert_at


def remove_segment(dub: dict, segment_index: int) -> bool:
    """Drop the segment at `segment_index`. No-op if index is out of range."""
    segments = normalize_segments(dub)
    if not (0 <= segment_index < len(segments)):
        return False
    segments.pop(segment_index)
    return True


def move_boundary(dub: dict, boundary_index: int, new_timeline_position: float) -> bool:
    """Move the boundary between segments `boundary_index` and `boundary_index+1`
    to `new_timeline_position` (absolute seconds in the dub timeline). Lengthens
    the left segment and shortens the right one in lock-step.

    For audio segments the change is applied to the source-file offsets
    (`end` for the left, `start` for the right) so both halves keep
    pointing at the same on-disk file with the cut moving inside it.
    For silence segments the change applies to `duration`.

    Returns True if the move was applied. Returns False on bad index, or
    when the move would push a segment below zero duration."""
    segments = normalize_segments(dub)
    if not (0 <= boundary_index < len(segments) - 1):
        return False

    base = float(dub.get('start', 0.0))
    cursor = base
    boundary_pos = None
    for i, seg in enumerate(segments):
        cursor += segment_duration(seg)
        if i == boundary_index:
            boundary_pos = cursor
            break
    if boundary_pos is None:
        return False

    delta = float(new_timeline_position) - boundary_pos
    left = segments[boundary_index]
    right = segments[boundary_index + 1]
    left_dur = segment_duration(left)
    right_dur = segment_duration(right)
    new_left_dur = left_dur + delta
    new_right_dur = right_dur - delta
    if new_left_dur < 0 or new_right_dur < 0:
        return False

    if left.get('type') == 'silence':
        left['duration'] = new_left_dur
    else:
        left['end'] = float(left.get('start', 0.0)) + new_left_dur

    if right.get('type') == 'silence':
        right['duration'] = new_right_dur
    else:
        # Keep the right segment's source `end` fixed, move `start` to
        # absorb the delta — this is what "drag the cut point inside the
        # source" means for an audio-audio pair pointing at the same file.
        original_end = right.get('end')
        if original_end is None:
            return False
        right['start'] = float(original_end) - new_right_dur

    return True


def merge_adjacent_audio(dub: dict) -> int:
    """Collapse adjacent audio segments that point at the same source file
    AND are contiguous (segment N's end == segment N+1's start). Returns
    the number of merges performed. Useful after a no-op split is undone
    or after a sequence of edits leaves contiguous halves dangling."""
    segments = dub.get('segments')
    if not segments:
        return 0
    merged = 0
    i = 0
    while i < len(segments) - 1:
        a = segments[i]
        b = segments[i + 1]
        if (a.get('type') == 'audio' and b.get('type') == 'audio'
                and a.get('path') and a.get('path') == b.get('path')):
            a_end = a.get('end')
            b_start = float(b.get('start', 0.0))
            if a_end is not None and abs(float(a_end) - b_start) < 1e-9:
                a['end'] = b.get('end')
                segments.pop(i + 1)
                merged += 1
                continue
        i += 1
    return merged


def collect_segment_paths(dub: dict) -> list[str]:
    """Unique audio paths referenced by `dub`'s segments (or just `dub['path']`
    for legacy dubs). Used by the audio engine to know which files to preload
    via the background loader. Order is preserved so the first segment loads
    first."""
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
