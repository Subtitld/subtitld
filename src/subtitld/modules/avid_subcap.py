"""Avid SubCap (.txt): the subtitle file Avid Media Composer's SubCap effect
imports and exports, which other tools call Avid DS or Avid Caption.

    @ This file written with the Avid Caption plugin, version 1

    <begin subtitles>
    01:00:03:15 01:00:08:05
    The first line
    and the second

    01:00:10:00 01:00:12:00
    The next subtitle
    <end subtitles>

A subtitle is its start and end in timecode, "HH:MM:SS:FF", frames after
the seconds, on a line of their own, then its lines of text and a blank
line. A line that begins "@" is a comment. The file holds nothing else:
no frame rate, no formatting, no place on the screen. SubCap sets those in
the effect, for every subtitle at once.

Reading forgives:

- the bytes are decoded as SRT's are;
- the header and the <begin subtitles> and <end subtitles> lines missing,
  in capitals or not; comments anywhere;
- text on the line of the times, after them, as some tools write it;
- timecode with ";" (or "." or ",") before the frames is drop-frame, which
  only 29.97 and 59.94 count in: the file is read at that rate whatever
  the video's is. With ":" the frames are counted at the video's rate,
  25 where there is none;
- a file whose first subtitle starts an hour or more in counts from
  01:00:00:00, the start Media Composer gives a new sequence: the hour is
  taken off, so the subtitles meet the video, and given back on saving;
- formatting tags written into the text stay as Subtitld's (SubCap would
  show them as text, so writing leaves them out);
- the subtitles come sorted by start.

Writing gives SubCap's own layout: its header, the subtitles between
<begin subtitles> and <end subtitles>, times in frames of the export's
rate (the video's), drop-frame at 29.97 and 59.94 unless asked otherwise,
counted from 00:00:00:00 or 01:00:00:00, and the plain text of each
subtitle with no blank line inside it.
"""

import re
from fractions import Fraction

from subtitld.modules import markup
from subtitld.modules.srt import decode

HEADER = '@ This file written with the Avid Caption plugin, version 1'
BEGIN = '<begin subtitles>'
END = '<end subtitles>'
DEFAULT_FPS = 25.0
# Where the sequence's timecode starts: Subtitld's 0, or an hour in.
STARTS = ('00:00:00:00', '01:00:00:00')

_TIMECODE = r'(\d{1,2})[:;.](\d{2})[:;.](\d{2})([:;.,])(\d{2})'
_TIMES = re.compile(r'^\s*' + _TIMECODE + r'\s+' + _TIMECODE + r'(?:\s+(.*))?$')

# Frame rates as video gives them: the frames a second of timecode counts,
# and how long a frame really lasts.
_RATES = {
    23.976: (24, Fraction(1001, 24000)),
    24.0: (24, Fraction(1, 24)),
    25.0: (25, Fraction(1, 25)),
    29.97: (30, Fraction(1001, 30000)),
    30.0: (30, Fraction(1, 30)),
    50.0: (50, Fraction(1, 50)),
    59.94: (60, Fraction(1001, 60000)),
    60.0: (60, Fraction(1, 60)),
}


def is_subcap(data):
    """Whether `data` (bytes or text) is an Avid SubCap file: one that opens
    its subtitles with <begin subtitles>, or carries the plugin's header."""
    text = decode(data[:8192]) if isinstance(data, (bytes, bytearray)) else str(data or '')[:8192]
    lowered = text.lower()
    return BEGIN in lowered or 'written with the avid caption plugin' in lowered


def rate(fps):
    """(frames a timecode second counts, Fraction of a second a frame lasts)
    for `fps`: the nearest of the rates video uses, 25 for none."""
    fps = float(fps or DEFAULT_FPS)
    nearest = min(_RATES, key=lambda known: abs(known - fps))
    if abs(nearest - fps) < 0.05:
        return _RATES[nearest]
    return round(fps), Fraction(1) / Fraction(fps).limit_denominator(1001)


def drop_rate(fps):
    """The drop-frame rate for a video at `fps`: 59.94 near 60, else 29.97."""
    return 59.94 if float(fps or 0) > 45 else 29.97


def frame_of(hours, minutes, seconds, frames, nominal, drop=False):
    """The frame a timecode names, from 0. Drop-frame skips the first
    numbers of every minute but each tenth, two at 29.97 and four at 59.94,
    so its count must put them back."""
    count = (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * nominal + int(frames)
    if drop:
        dropped = nominal // 15
        all_minutes = int(hours) * 60 + int(minutes)
        count -= dropped * (all_minutes - all_minutes // 10)
    return count


def timecode(frame, nominal, drop=False):
    """The timecode of a frame: HH:MM:SS:FF, or HH:MM:SS;FF drop-frame."""
    frame = max(0, int(frame))
    if drop:
        dropped = nominal // 15
        per_minute = nominal * 60 - dropped
        per_ten = per_minute * 10 + dropped
        tens, rest = divmod(frame, per_ten)
        frame += dropped * 9 * tens + (dropped * ((rest - dropped) // per_minute) if rest >= dropped else 0)
    hours, rest = divmod(frame, nominal * 3600)
    minutes, rest = divmod(rest, nominal * 60)
    seconds, frames = divmod(rest, nominal)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}{";" if drop else ":"}{frames:02d}'


def parse(text, fps=None):
    """(subtitles, settings) in SubCap text: [{'start', 'end', 'text'}] by
    start, and {'fps', 'drop', 'start'}, the rate the times were read at,
    whether they were drop-frame and the timecode the file counts from."""
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    found = []                                  # (start groups, end groups, [lines])
    for line in lines:
        stripped = line.strip()
        if stripped.lower() == END:
            break
        if stripped.lower() == BEGIN or stripped.startswith('@'):
            continue
        match = _TIMES.match(line)
        if match:
            groups = match.groups()
            found.append((groups[0:5], groups[5:10], [groups[10].strip()] if groups[10] and groups[10].strip() else []))
        elif stripped and found:
            found[-1][2].append(stripped)

    drop = any(start[3] != ':' or end[3] != ':' for start, end, _lines in found)
    fps = drop_rate(fps) if drop else float(fps or DEFAULT_FPS)
    nominal, frame_length = rate(fps)
    framed = [(frame_of(*start[0:3], start[4], nominal, drop), frame_of(*end[0:3], end[4], nominal, drop), body)
              for start, end, body in found]
    # Media Composer starts a sequence at 01:00:00:00, and a file made from
    # one carries that hour in every time.
    hour = frame_of(1, 0, 0, 0, nominal, drop)
    origin = hour if framed and min(first for first, _end, _body in framed) >= hour else 0
    segments = []
    for first, last, body in framed:
        content = '\n'.join(body)
        if not markup.plain(content).strip():
            continue
        start = round(float((first - origin) * frame_length), 3)
        end = max(start, round(float((last - origin) * frame_length), 3))
        segments.append({'start': start, 'end': end, 'text': content})
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments, {'fps': fps, 'drop': drop, 'start': STARTS[1] if origin else STARTS[0]}


def read(path, fps=None):
    """(subtitles, settings) in the SubCap file at `path`, as parse() gives them."""
    with open(path, 'rb') as subcap_file:
        return parse(decode(subcap_file.read()), fps)


def write(segments, fps=DEFAULT_FPS, drop=True, start=STARTS[0], line_end='\n'):
    """SubCap text for `segments`, in frames at `fps` (drop-frame when `drop`
    and the rate is 29.97 or 59.94), counted from `start`."""
    nominal, frame_length = rate(fps)
    # Only 29.97 and 59.94 have a drop-frame timecode.
    drop = bool(drop) and nominal in (30, 60) and frame_length.numerator == 1001
    origin = frame_of(1, 0, 0, 0, nominal, drop) if start == STARTS[1] else 0
    out = [HEADER, '', BEGIN]
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        text = markup.plain(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        if not lines:
            continue
        first = int(round(Fraction(float(segment.get('start', 0))).limit_denominator(1000000) / frame_length))
        last = int(round(Fraction(float(segment.get('end', 0))).limit_denominator(1000000) / frame_length))
        last = max(first + 1, last)
        out.append(f'{timecode(origin + first, nominal, drop)} {timecode(origin + last, nominal, drop)}')
        out.extend(lines)
        out.append('')
    out.append(END)
    return line_end.join(out) + line_end


def save(path, segments, fps=DEFAULT_FPS, drop=True, start=STARTS[0], encoding='utf-8', line_end='\n'):
    """Write `segments` to `path` as Avid SubCap."""
    with open(path, 'w', encoding=encoding, errors='replace', newline='') as subcap_file:
        subcap_file.write(write(segments, fps, drop, start, line_end))
