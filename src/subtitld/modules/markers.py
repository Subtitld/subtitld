"""Editors' marker lists as subtitles: EDIUS's marker list (.csv, and the
XML of its format version 4) and Adobe Premiere Pro's markers (.csv).

Both are what an editor exports of its timeline's markers, a comment and
where it is, and imports back; Subtitle Edit reads and writes them, and
its classes are the only description of them there is to follow:

- EDIUS (EdiusMarkerList2Frames.cs, EdiusMarkerList3Frames.cs and their
  Ms twins, Edius4Frames.cs, Edius4Ms.cs): a CSV under "# EDIUS Marker
  list", "# Format Version 2" (or 3) and a "#" header line, each row its
  number, in version 3 an anchor ("ON"), the position and the duration as
  timecode, and the comment; the fields quoted, a quote inside one
  written \\", a comment running over lines inside its quotes. Times are
  HH:MM:SS:FF, frames, or with three digits after the seconds,
  milliseconds. Version 4 is XML in the namespace
  http://www.grassvalley.com/ns/edius/markerListInfo: <edius:marker>s
  holding <edius:no>, <edius:anchor>, <edius:position>,
  <edius:duration>, <edius:comment> and <edius:color> (0xAARRGGBB);
- Premiere (AdobePremiereMarkersCsv.cs): the Markers panel's "Export
  Markers as CSV", tab-separated despite its name, under the header
  "Marker Name, Description, In, Out, Duration, Marker Type" (tabs
  between); times HH:MM:SS:FF, or HH;MM;SS;FF drop-frame. The
  description is the text, the name where there is none.

A marker with no length (a zero duration, an Out equal to its In) is a
point: it lasts until the next marker, at most POINT_SECONDS. Frames count
at the video's rate, 25 without one (29.97 or 59.94 for Premiere's
drop-frame). The format version, the time units and an EDIUS marker's
colour are kept to be written back.

Writing gives EDIUS's version 3 CSV in frames (or the version and units
the file was opened with; version 4 when the file is .xml); Premiere's columns with the
text as both the name and the description, each marker a Comment, on one
line (the format has no line breaks inside a field). Both with CRLF, the
CSVs in UTF-8 with a byte order mark (which of the encodings the editors
write is not in Subtitle Edit's code: it detects the encoding when it
reads). Formatting is not written.
"""

import datetime
import re
import xml.etree.ElementTree as ET
from fractions import Fraction
from xml.sax.saxutils import escape

from subtitld.modules import markup
from subtitld.modules.avid_subcap import drop_rate, frame_of, rate, timecode
from subtitld.modules.srt import decode
from subtitld.modules.segment import text_of

DEFAULT_FPS = 25.0
POINT_SECONDS = 3.0
EDIUS_NAMESPACE = 'http://www.grassvalley.com/ns/edius/markerListInfo'
PREMIERE_HEADER = 'Marker Name\tDescription\tIn\tOut\tDuration'
KINDS = ('edius', 'premiere')

_TIME = re.compile(r'^\s*(\d{1,2})[:;.](\d{2})[:;.](\d{2})([:;.,])(\d{2,3})\s*$')


def kind(head):
    """'edius', 'premiere' or None for `head` (text, or bytes decoded as
    SRT's are)."""
    text = decode(head[:16384]) if isinstance(head, (bytes, bytearray)) else str(head or '')[:16384]
    if '# EDIUS Marker list' in text or '<edius:markerLists' in text or EDIUS_NAMESPACE in text:
        return 'edius'
    first = next((line for line in text.replace('\r', '\n').split('\n') if line.strip()), '')
    if first.lstrip('\ufeff').startswith(PREMIERE_HEADER):
        return 'premiere'
    return None


def looks_like(head):
    """Whether `head` is a marker list this module reads."""
    return kind(head) is not None


def _seconds(value, nominal, frame_length, drop=False):
    """Seconds of a time: frames after the seconds, or milliseconds where
    there are three digits; None for what is not a time."""
    match = _TIME.match(value or '')
    if not match:
        return None, False
    hours, minutes, seconds, separator, last = match.groups()
    if len(last) == 3:
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(last) / 1000, True
    count = frame_of(hours, minutes, seconds, last, nominal, drop or separator == ';')
    return float(count * frame_length), False


def _csv_fields(lines):
    """EDIUS's CSV rows: quoted fields, \\" for a quote, a quoted field
    running on over lines."""
    rows, fields, field, quoted, started = [], [], [], False, False
    for line in lines:
        if not quoted and (not line.strip() or line.lstrip().startswith('#')):
            continue
        at = 0
        while at < len(line):
            char = line[at]
            if quoted and line.startswith('\\"', at):
                field.append('"')
                at += 2
                continue
            if char == '"':
                quoted = not quoted
                started = True
            elif char == ',' and not quoted:
                fields.append(''.join(field))
                field, started = [], False
            else:
                field.append(char)
            at += 1
        if quoted:
            field.append('\n')
            continue
        if field or started or fields:
            fields.append(''.join(field))
        rows.append(fields)
        fields, field, started = [], [], False
    return rows


def _points(found):
    """[(start, end or None, text, extra)] with each point (no end) given
    its end: the next one's start, at most POINT_SECONDS on."""
    found.sort(key=lambda entry: entry[0])
    segments = []
    for index, (start, end, text, extra) in enumerate(found):
        if end is None or end <= start:
            following = next((entry[0] for entry in found[index + 1:] if entry[0] > start), None)
            end = min(start + POINT_SECONDS, following) if following is not None else start + POINT_SECONDS
        if not markup.plain(text).strip():
            continue
        segment = {'start': round(start, 3), 'end': round(end, 3), 'text': text}
        if extra:
            segment['markers'] = extra
        segments.append(segment)
    return segments


def parse(text, fps=None):
    """(subtitles, settings) in a marker list's text: settings
    {'kind', 'fps', 'version' (EDIUS's), 'units' ('frames' or 'ms'),
    'drop'}."""
    found_kind = kind(text)
    if found_kind is None:
        raise ValueError('Not a marker list: neither EDIUS\'s nor Premiere\'s header is there')
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    drop = found_kind == 'premiere' and any(';' in (line.split('\t') + ['', '', ''])[2] for line in lines[1:])
    fps = drop_rate(fps) if drop else float(fps or DEFAULT_FPS)
    nominal, frame_length = rate(fps)
    found, units, version = [], 'frames', None
    if found_kind == 'premiere':
        for line in lines[1:]:
            parts = line.split('\t')
            if len(parts) < 4:
                continue
            start, _ms = _seconds(parts[2], nominal, frame_length, drop)
            if start is None:
                continue
            end, _ms = _seconds(parts[3], nominal, frame_length, drop)
            body = parts[1].strip() or parts[0].strip()
            found.append((start, end, body, {}))
    elif '<edius:markerLists' in text or text.lstrip('\ufeff \n').startswith('<'):
        version = 4
        try:
            root = ET.fromstring(text.lstrip('\ufeff').encode('utf-8'))
        except ET.ParseError as error:
            line, column = error.position
            raise ValueError(f'Line {line}, column {column + 1}: {error}') from error
        for marker in root.iter(f'{{{EDIUS_NAMESPACE}}}marker'):
            def child(name):
                element = marker.find(f'{{{EDIUS_NAMESPACE}}}{name}')
                return element.text or '' if element is not None else ''
            start, in_ms = _seconds(child('position'), nominal, frame_length)
            length, _ms = _seconds(child('duration'), nominal, frame_length)
            if start is None:
                continue
            units = 'ms' if in_ms else units
            color = child('color').strip()
            found.append((start, start + (length or 0), child('comment'),
                          {'color': color} if color and color.lower() != '0xffffffff' else {}))
    else:
        version_found = re.search(r'#\s*Format Version\s*(\d+)', text)
        version = int(version_found.group(1)) if version_found else 2
        for row in _csv_fields(lines):
            fields = [field.strip() if index < len(row) - 1 else field for index, field in enumerate(row)]
            if version >= 3 and len(fields) >= 5:
                position, duration, comment = fields[2], fields[3], fields[4]
            elif len(fields) >= 4:
                position, duration, comment = fields[1], fields[2], fields[3]
            else:
                continue
            start, in_ms = _seconds(position, nominal, frame_length)
            length, _ms = _seconds(duration, nominal, frame_length)
            if start is None:
                continue
            units = 'ms' if in_ms else units
            found.append((start, start + (length or 0), comment.strip('\n'), {}))
    return _points(found), {'kind': found_kind, 'fps': fps, 'version': version, 'units': units, 'drop': drop}


def read(path, fps=None):
    """(subtitles, settings) in the marker list at `path`, as parse() gives them."""
    with open(path, 'rb') as marker_file:
        return parse(decode(marker_file.read()), fps)


def _frame(seconds, frame_length):
    return int(round(Fraction(float(seconds)).limit_denominator(1000000) / frame_length))


def _ms_time(seconds, separator):
    milliseconds = int(round(float(seconds) * 1000))
    hours, rest = divmod(milliseconds, 3600000)
    minutes, rest = divmod(rest, 60000)
    whole, rest = divmod(rest, 1000)
    return f'{hours:02d}:{minutes:02d}:{whole:02d}{separator}{rest:03d}'


def _texts(segments):
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        text = markup.plain(text_of(segment).replace('\r\n', '\n').replace('\r', '\n')).strip()
        if text:
            yield segment, text


def write_edius(segments, fps=DEFAULT_FPS, version=3, units='frames', created=None):
    """An EDIUS marker list for `segments`: version 2 or 3 CSV, or 4's
    XML, times in frames at `fps` or in milliseconds (`units`)."""
    nominal, frame_length = rate(fps)
    # As .NET's "ddd MMM dd HH:mm:ss yyy" writes it, in English whatever
    # the locale.
    moment = created or datetime.datetime.now()
    created = (f'{("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[moment.weekday()]} '
               f'{("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[moment.month - 1]} '
               f'{moment:%d %H:%M:%S %Y}')

    def times(segment):
        start, end = float(segment.get('start', 0)), max(float(segment.get('start', 0)), float(segment.get('end', 0)))
        if units == 'ms':
            separator = '.' if version == 4 else ':'
            return _ms_time(start, separator), _ms_time(end - start, separator)
        first = _frame(start, frame_length)
        return timecode(first, nominal), timecode(max(0, _frame(end, frame_length) - first), nominal)
    if version == 4:
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="no" ?>',
               f'<edius:markerInfo xmlns:edius="{EDIUS_NAMESPACE}">', '\t<edius:formatVersion>4</edius:formatVersion>',
               f'\t<edius:CreateDate>{created}</edius:CreateDate>', '\t<edius:markerLists>']
        for number, (segment, text) in enumerate(_texts(segments), 1):
            position, duration = times(segment)
            color = str((segment.get('markers') or {}).get('color') or '0xffffffff')
            out += ['\t\t<edius:marker>', f'\t\t\t<edius:no>{number}</edius:no>', '\t\t\t<edius:anchor>1</edius:anchor>',
                    f'\t\t\t<edius:position>{position}</edius:position>', f'\t\t\t<edius:duration>{duration}</edius:duration>',
                    f'\t\t\t<edius:comment>{escape(text)}</edius:comment>', f'\t\t\t<edius:color>{escape(color)}</edius:color>',
                    '\t\t</edius:marker>']
        out += ['\t</edius:markerLists>', '</edius:markerInfo>']
        return '\r\n'.join(out) + '\r\n'
    version = 2 if version == 2 else 3
    out = ['# EDIUS Marker list', f'# Format Version {version}', f'# Created Date : {created}', '#',
           '# No, Anchor, Position, Duration, Comment' if version == 3 else '# No, Position, Duration, Comment']
    for number, (segment, text) in enumerate(_texts(segments), 1):
        position, duration = times(segment)
        comment = text.replace('"', '\\"').replace('\n', '\r\n')
        anchor = '"ON",' if version == 3 else ''
        out.append(f'{number},{anchor}"{position}","{duration}","{comment}"')
    return '\r\n'.join(out) + '\r\n'


def write_premiere(segments, fps=DEFAULT_FPS, drop=False):
    """Premiere's marker CSV for `segments`, times in frames at `fps`,
    drop-frame (HH;MM;SS;FF) when `drop` and the rate has it."""
    nominal, frame_length = rate(fps)
    drop = bool(drop) and nominal in (30, 60) and frame_length.numerator == 1001
    out = [PREMIERE_HEADER + '\tMarker Type']

    def label(count):
        written = timecode(count, nominal, drop)
        return written.replace(':', ';') if drop else written
    for segment, text in _texts(segments):
        first = _frame(segment.get('start', 0), frame_length)
        last = max(first, _frame(segment.get('end', 0), frame_length))
        line = ' '.join(part.strip() for part in text.split('\n') if part.strip()).replace('\t', ' ')
        out.append(f'{line}\t{line}\t{label(first)}\t{label(last)}\t{label(last - first)}\tComment')
    return '\r\n'.join(out) + '\r\n'


def save(path, segments, kind='edius', **options):
    """Write `segments` to `path` as a marker list of `kind` (KINDS);
    `options` as write_edius() or write_premiere() takes them."""
    if kind == 'premiere':
        text = write_premiere(segments, **options)
    else:
        text = write_edius(segments, **options)
    # A CSV with a byte order mark, so that Windows tools take it as UTF-8;
    # the XML says its encoding itself.
    encoding = 'utf-8' if text.startswith('<?xml') else 'utf-8-sig'
    with open(path, 'w', encoding=encoding, newline='') as marker_file:
        marker_file.write(text)
