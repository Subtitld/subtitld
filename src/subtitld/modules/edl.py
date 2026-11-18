"""CMX 3600 EDL (.edl): the edit decision list every editor reads and
writes, carrying subtitles as its events' comments, as Avid's locators or
as DaVinci Resolve's markers.

An EDL is lines of text: a TITLE: and an FCM: (DROP FRAME or NON-DROP
FRAME) at the top, then events, each a line

    001  AX       V     C        01:00:01:00 01:00:03:12 01:00:01:00 01:00:03:12

(its number, the reel, the track, the transition — C a cut, D a dissolve,
W a wipe, K a key, with its length in frames after D, W and K — and the
source in and out, the record in and out), followed by comment lines
beginning "*". What follows are the sources each part keeps to:

- OpenTimelineIO's CMX 3600 adapter (otio-cmx3600-adapter, cmx_3600.py)
  for the event line, the comments it knows ("* FROM CLIP NAME:", "* LOC:",
  "* COMMENT:") and Avid's locator lines, "* LOC: 01:00:01:14 RED     ANIM
  FIX NEEDED": a timecode, a colour and the comment, which OpenTimelineIO
  reads in the source timecode of the event it is under; Subtitld reads a
  locator inside the event's source range so, and one inside its record
  range (as Subtitld writes them, source and record the same) as it is;
- Subtitle Edit's Edl.cs for subtitles as events: the record in and out
  as the subtitle's times, the clip name as its text, black (BL) events
  left out;
- Subtitle Edit's DaVinciResolveMarkerEdl.cs for Resolve's markers: an
  event one frame long at the marker, then " |C:ResolveColorBlue |M:name
  |D:120", the colour, the name and the length in frames.

Reading takes what the file has: its locators or markers where it has
any (an EDL of cuts with a few notes on it is read as its notes), else
each event's clip name with any "* COMMENT:" lines after it as a
subtitle's lines. A locator is a point in time: it lasts until the next
locator or the end of its event, whichever comes first; a marker lasts
its |D: frames, or as a locator does when it is one frame long, but never
past POINT_SECONDS. Timecode with ";" or an FCM of DROP FRAME is
drop-frame (29.97 or 59.94, whatever the video's rate); otherwise frames
count at the video's rate, 25 without one. An EDL whose first time is an
hour or more in counts from 01:00:00:00, the start editors give a new
sequence: the hour is taken off, and given back on saving (as Avid
SubCap's, modules.avid_subcap). A locator's or marker's colour is kept on
the subtitle ('edl') and written back.

Writing gives one of STYLES:

- 'events': an event a subtitle, reel AX, the source times the record
  times, its first line as the clip name and the rest as "* COMMENT:"
  lines;
- 'locators': the same events, each with an Avid locator at its start in
  place of the clip name, the text on one line (a locator's comment has
  one), in the colour it was read with, else YELLOW;
- 'markers': Resolve's marker EDL, as Subtitle Edit writes it.

Event numbers have three digits, as CMX 3600 has them, until a list has
more than 999 events, which then count with more. Formatting is not
written: an EDL has none.
"""

import re
from fractions import Fraction

from subtitld.modules import markup
from subtitld.modules.avid_subcap import STARTS, drop_rate, frame_of, rate, timecode
from subtitld.modules.srt import decode

STYLES = ('events', 'locators', 'markers')
DEFAULT_FPS = 25.0
# The longest a point (a locator, a one-frame marker) is shown.
POINT_SECONDS = 3.0
COLORS = ('WHITE', 'RED', 'GREEN', 'BLUE', 'CYAN', 'MAGENTA', 'YELLOW', 'BLACK')

_TC = r'(\d{1,2})[:;.](\d{2})[:;.](\d{2})([:;.,])(\d{2})'
_EVENT = re.compile(r'^\s*(\d{1,6})\s+(\S+)\s+(\S+)\s+(C|D|W\d{1,3}|KB?|KO|K\s+B)\s+(?:(\d{1,4})\s+)?'
                    + _TC + r'\s+' + _TC + r'\s+' + _TC + r'\s+' + _TC + r'\s*$')
_LOCATOR = re.compile(r'^\*\s*LOC:?\s*' + _TC + r'(?:\s+(\w*))?(?:\s+(.*))?$', re.IGNORECASE)
_COMMENT = re.compile(r'^\*\s*(FROM CLIP NAME|COMMENT)\s*:\s*(.*)$', re.IGNORECASE)


def looks_like(head):
    """Whether `head` (text, or bytes decoded as SRT's are) is a CMX 3600
    EDL: an event line within its first lines, or its TITLE: and FCM:."""
    text = decode(head[:16384]) if isinstance(head, (bytes, bytearray)) else str(head or '')[:16384]
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')[:60]
    if any(_EVENT.match(line) for line in lines):
        return True
    upper = [line.strip().upper() for line in lines]
    return any(line.startswith('TITLE:') for line in upper) and any(line.startswith('FCM:') for line in upper)


def _frames(groups, nominal, drop):
    return frame_of(groups[0], groups[1], groups[2], groups[4], nominal, drop)


def parse(text, fps=None):
    """(subtitles, settings) in EDL text: [{'start', 'end', 'text'[,
    'edl']}] by start, and {'fps', 'drop', 'start', 'style', 'title'}: the
    rate read at, drop-frame or not, the timecode it counts from, which of
    STYLES it holds its subtitles as, and its TITLE."""
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    title = ''
    drop = False
    events = []
    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith('TITLE:'):
            title = stripped[6:].strip()
            continue
        if upper.startswith('FCM:'):
            drop = drop or ('DROP' in upper and 'NON' not in upper)
            continue
        match = _EVENT.match(line)
        if match:
            groups = match.groups()
            drop = drop or any(groups[index] == ';' for index in (8, 13, 18, 23))
            events.append({'reel': groups[1], 'times': [groups[5 + 5 * n:10 + 5 * n] for n in range(4)],
                           'name': None, 'comments': [], 'locators': [], 'marker': None})
            continue
        if not events or not stripped:
            continue
        event = events[-1]
        locator = _LOCATOR.match(stripped)
        if locator:
            groups = locator.groups()
            event['locators'].append((groups[0:5], (groups[5] or '').upper(), (groups[6] or '').strip()))
            continue
        if '|M:' in stripped:
            marker = stripped
            name_at = marker.index('|M:') + 3
            duration_at = marker.rfind('|D:')
            color = re.search(r'\|C:(\S+)', marker)
            name = marker[name_at:duration_at if duration_at > name_at else None].strip()
            try:
                length = int(marker[duration_at + 3:].strip()) if duration_at > name_at else 0
            except ValueError:
                length = 0
            event['marker'] = (name, length, color.group(1) if color else '')
            continue
        comment = _COMMENT.match(stripped)
        if comment:
            if comment.group(1).upper() == 'FROM CLIP NAME' and event['name'] is None:
                event['name'] = comment.group(2).strip()
            elif comment.group(1).upper() == 'COMMENT':
                event['comments'].append(comment.group(2).strip())

    fps = drop_rate(fps) if drop else float(fps or DEFAULT_FPS)
    nominal, frame_length = rate(fps)
    for event in events:
        event['frames'] = [_frames(groups, nominal, drop) for groups in event['times']]

    points = []                                 # (start, end limit, text, colour)
    style = 'events'
    if any(event['locators'] for event in events):
        style = 'locators'
        for event in events:
            source_in, source_out, record_in, record_out = event['frames']
            for groups, color, comment in event['locators']:
                at = _frames(groups, nominal, drop)
                if source_in <= at < source_out and not record_in <= at < record_out:
                    at = record_in + (at - source_in)
                points.append([at, record_out, comment, color])
    elif any(event['marker'] for event in events):
        style = 'markers'
        for event in events:
            if event['marker']:
                name, length, color = event['marker']
                record_in = event['frames'][2]
                points.append([record_in, record_in + length if length > 1 else None, name, color])
    if points:
        points.sort(key=lambda point: point[0])
        found = []
        limit = int(round(POINT_SECONDS / float(frame_length)))
        for index, (at, until, comment, color) in enumerate(points):
            following = next((point[0] for point in points[index + 1:] if point[0] > at), None)
            if style == 'markers' and until is not None:
                end = until
            else:
                candidates = [at + limit] + [value for value in (until, following) if value is not None and value > at]
                end = min(candidates)
            found.append((at, end, comment, color))
    else:
        found = []
        for event in events:
            if event['reel'].upper() == 'BL':
                continue
            body = [part for part in [event['name'] or ''] + event['comments'] if part]
            if body:
                found.append((event['frames'][2], event['frames'][3], '\n'.join(body), ''))

    hour = frame_of(1, 0, 0, 0, nominal, drop)
    origin = hour if found and min(entry[0] for entry in found) >= hour else 0
    segments = []
    for first, last, content, color in found:
        if not markup.plain(content).strip():
            continue
        start = round(float((first - origin) * frame_length), 3)
        segment = {'start': start, 'end': max(start, round(float((last - origin) * frame_length), 3)), 'text': content}
        if color:
            segment['edl'] = {'color': color}
        segments.append(segment)
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments, {'fps': fps, 'drop': drop, 'start': STARTS[1] if origin else STARTS[0], 'style': style,
                      'title': title}


def read(path, fps=None):
    """(subtitles, settings) in the EDL at `path`, as parse() gives them."""
    with open(path, 'rb') as edl_file:
        return parse(decode(edl_file.read()), fps)


def _frame(seconds, frame_length):
    return int(round(Fraction(float(seconds)).limit_denominator(1000000) / frame_length))


def write(segments, fps=DEFAULT_FPS, drop=True, start=STARTS[0], style='events', title='', line_end='\n'):
    """EDL text for `segments` in `style` (STYLES), frames at `fps`
    (drop-frame when `drop` and the rate is 29.97 or 59.94), counted from
    `start`, under TITLE `title`."""
    style = style if style in STYLES else 'events'
    nominal, frame_length = rate(fps)
    drop = bool(drop) and nominal in (30, 60) and frame_length.numerator == 1001
    origin = frame_of(1, 0, 0, 0, nominal, drop) if start == STARTS[1] else 0
    out = [f'TITLE: {title or "Subtitles"}', 'FCM: DROP FRAME' if drop else 'FCM: NON-DROP FRAME', '']
    ordered = sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0))))
    width = 3 if len(ordered) <= 999 else len(str(len(ordered)))
    number = 0
    for segment in ordered:
        text = markup.plain(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        if not lines:
            continue
        number += 1
        first = origin + _frame(segment.get('start', 0), frame_length)
        last = max(first + 1, origin + _frame(segment.get('end', 0), frame_length))
        color = str((segment.get('edl') or {}).get('color') or '')
        if style == 'markers':
            marker_in, marker_out = timecode(first, nominal, drop), timecode(first + 1, nominal, drop)
            out.append(f'{number:0{width}d}  001      V     C        {marker_in} {marker_out} {marker_in} {marker_out}  ')
            name = ' '.join(lines).replace('|', ' ')
            out.append(f' |C:{color if color.startswith("Resolve") else "ResolveColorBlue"} |M:{name} |D:{last - first}')
            out.append('')
            continue
        record_in, record_out = timecode(first, nominal, drop), timecode(last, nominal, drop)
        out.append(f'{number:0{width}d}  AX       V     C        {record_in} {record_out} {record_in} {record_out}')
        if style == 'locators':
            out.append(f'* LOC: {record_in} {(color if color in COLORS else "YELLOW"):7} {" ".join(lines)}')
        else:
            out.append(f'* FROM CLIP NAME: {lines[0]}')
            out.extend(f'* COMMENT: {line}' for line in lines[1:])
        out.append('')
    return line_end.join(out).rstrip('\n') + line_end


def save(path, segments, encoding='utf-8', **options):
    """Write `segments` to `path` as an EDL; `options` as write() takes them."""
    line_end = options.pop('line_end', '\r\n')
    with open(path, 'w', encoding=encoding, errors='replace', newline='') as edl_file:
        edl_file.write(write(segments, line_end=line_end, **options))
