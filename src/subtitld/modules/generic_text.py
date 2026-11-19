"""Subtitles in text no named format claims: the anonymous long tail.

Subtitle Edit keeps more than a hundred readers for text files nobody named
("UnknownSubtitle1" to "UnknownSubtitle107", TimeCodesOnly2, Csv to Csv5,
Tsv1 and Tsv2, the JsonType readers; src/libse/SubtitleFormats/*.cs), each
written for one file somebody sent in. Read side by side they are a handful
of shapes with different punctuation, and this module reads the shapes, not
the files. It is the last thing tried on a .txt (modules.file_io): every
named format is asked first, and plain text is what is left after it.

The shapes (`detect` says which, and how sure it is):

- **inline**: a line that starts with a time, or two, then the text
  ("00:00:01.000 00:00:03.500 Hello", "0:01 – 0:11 Hello",
  "[00:00:01] Hello", "{0:00:01}{0:00:03}Hello", "<tc><tc>"). Lines after
  it with no time are more of its text. As Unknown 1, 4, 7, 11, 48, 51,
  53, 57, 65, 80 (the end time at the line's end), 84, 85, 86, 89, 93,
  94, 97, 104 and 106 write them.
- **block**: times on a line of their own, or the start on one line and the
  end on the next, then the text under them until a blank line or the next
  time; a number alone on the line before is the subtitle's number
  ("[12]", "12.", "#12"). As Unknown 8, 12, 17, 18, 22, 23, 26, 29, 30,
  31, 32, 35, 38, 40, 50, 54, 56, 60, 61, 62, 63, 64, 66, 72, 73, 74, 77,
  107 and TimeCodesOnly2 write them, and an SRT or WebVTT saved as .txt,
  which go to their own readers.
- **frames**: "{start}{end}text" in frames, MicroDVD's lines in a .txt; read
  by modules.microdvd.
- **table**: delimited columns, comma, semicolon, tab or "|", with or
  without a header, quoted as CSV quotes; a start column, an end or a
  duration column, the text in one column (or two lines in two, Csv3's).
  Times as clocks in any of the notations below; plain numbers only under
  a header that names them, in milliseconds unless it says seconds or
  frames, as Csv, Tsv1 and Tsv2 write them. As Csv2 to Csv5, CsvNuendo,
  CsvExcel, Tsv1, Tsv2 and Adobe Audition's marker list (modules.csv_export)
  write them. Quotes are read as Subtitle Edit reads its own (a line break
  inside them, Csv2's quotes left undoubled, unquoted text with commas
  the rest of the line), and in a .csv "hh:mm:ss.ff" with no header is
  Csv5's frames after a dot. A .csv or .tsv file is read as this shape
  (modules.file_io), unless it is EDIUS's or Premiere's markers.
- **json**: an array of objects, at the top or under a key, each with a
  start (start, startTime, start_time, begin, from, in, startMs,
  tStartMs ...), an end or a duration (end, endTime, to, out, stop,
  duration ...) and the text (text, content, caption, subtitle, line,
  transcript ...). Numbers are seconds unless the key says milliseconds or
  every one is a whole number and the lengths run to hundreds; strings may
  be clocks. As the JsonType readers' files are. The JSON a named tool
  writes is modules.json_subtitles's; this is for the rest.

A time is a clock: "h:mm:ss" with a fraction after "." or "," (tenths,
hundredths or thousandths, as written), or frames after ":", ";" or "F"
(Subtitle Edit's DecodeTimeCodeFrames), or semicolons throughout, as
drop-frame timecode is written (Unknown 107); "hh.mm.ss.ff" (Unknown 50
and 86);
"m:ss" with or without a fraction (Unknown 1, 40, 89, 94, Audition); or
seconds, "12.5", only where a line or a table has nothing but seconds for
both times (Unknown 93). Frames are counted at the video's rate, else 25;
a frame field of 25 or more says the rate is higher, and 30 (or 60) is
taken.

A subtitle with no end runs to the start of the next, the last for `TAIL`
seconds, as legacy's VPlayer does.

What is refused rather than misread: anything with fewer than two
subtitles; text where most lines are not times or the text under them;
prose where a time starts a line here and there; times that go backwards
more than now and then; log files (level words such as INFO or ERROR, ISO
dates, chat nicknames in <angle brackets>, several lines stamped the same
second); a table whose time columns are neither clocks nor named; JSON
with no times in it.

Read only: these shapes are each some tool's private habit, and a file
written "in the generic shape" would be nobody's.
"""

import csv
import json
import re

from subtitld.modules import microdvd
from subtitld.modules import srt
from subtitld.modules import webvtt

TAIL = 3.0                       # seconds for a last subtitle that gives no end
DEFAULT_FPS = 25.0
THRESHOLD = 0.7                  # the confidence below which a shape is refused

INLINE = 'inline'
BLOCK = 'block'
FRAMES = 'frames'
TABLE = 'table'
JSON = 'json'
SRT = 'srt'
WEBVTT = 'webvtt'
SHAPES = (INLINE, BLOCK, FRAMES, TABLE, JSON, SRT, WEBVTT)

# One time, wherever it stands. The clock alternatives come first, so that
# "00:00:01:00" is one time and not two.
_TIME = re.compile(r'''
    (?:
      (?P<h>\d{1,3})(?P<c>[:;])(?P<m>[0-5]\d)(?P=c)(?P<s>[0-5]\d)(?:(?P<sep>[.,:;F])(?P<f>\d{1,3}))?
    | (?P<dh>\d\d)\.(?P<dm>[0-5]\d)\.(?P<ds>[0-5]\d)(?:\.(?P<df>\d\d))?
    | (?P<mm>\d{1,3}):(?P<ms>[0-5]\d)(?:(?P<msep>[.,])(?P<mf>\d{1,3}))?
    | (?P<sec>\d{1,6}\.\d{1,3})
    )
    (?![\w])(?!\.\d)(?!:\d)
''', re.X)
_OPEN = ' \t[{<('
_CLOSE = ' \t]}>)'
# What stands between two times on a line.
_BETWEEN = re.compile(r'[\s\]\}>)]*(?:-->|->|=>|=+>|\s-\s|[-–—~,|>=;]|\bto\b)?[\s\[\{<(]*')
# The subtitle's number before its times: "12 ", "12: ", "12) ", "#00012\t", "[12] ".
_INDEX = re.compile(r'\s*(?:#?\d{1,6}a?[.):]?|\[\d{1,6}\])\s+')
_NUMBER_LINE = re.compile(r'\s*(?:#?\d{1,6}[.):]?|\[\d{1,6}\])\s*$')
_LEAD = re.compile(r'^[\s,=:|]+')
# A file's own section marks around its subtitles (Unknown 17, 85, 107).
_MARKUP = re.compile(r'^\s*[\[<]/?(?:header|body|end|begin subtitles|end subtitles)[\]>]\s*$', re.I)

# Signs of a log or a chat, not of subtitles.
_LOG_WORD = re.compile(r'\b(?:TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|SEVERE|FATAL|CRITICAL)\b|^\s*(?:info|debug|warn(?:ing)?|error)\s*[:\]]', re.M)
_DATE = re.compile(r'\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b')
_NICK = re.compile(r'^\s*(?:<[^>\s]{1,32}>|\*\s+\S+\s)')

# Table headers: what each column is, by its name.
_START_NAMES = ('start', 'begin', 'in', 'from', 'tcin', 'timecodein', 'timein', 'starttime', 'recordin', 'sourcein', 'appearance')
_END_NAMES = ('end', 'out', 'to', 'stop', 'finish', 'tcout', 'timecodeout', 'timeout', 'endtime', 'recordout', 'sourceout')
_DURATION_NAMES = ('duration', 'dur', 'length')
_TEXT_NAMES = ('text', 'caption', 'subtitle', 'dialogue', 'dialog', 'content', 'transcript', 'line', 'sentence', 'name', 'translation')
_SPEAKER_NAMES = ('speaker', 'actor', 'character', 'role', 'voice')
_DELIMITERS = ('\t', ';', ',', '|')
# Adobe Audition's marker list (modules.csv_export), its header's columns.
_AUDITION = ['name', 'start', 'duration', 'timeformat', 'type', 'description']

# JSON keys, compared lower case without "_", "-" and spaces.
_JSON_START = ('start', 'starttime', 'startms', 'startmillis', 'startoffset', 'begin', 'from', 'in',
               'tstartms', 'timestart', 'starttimems', 'startseconds', 'startsec', 'offset')
_JSON_END = ('end', 'endtime', 'endms', 'endmillis', 'to', 'out', 'stop', 'timeend', 'endtimems',
             'endseconds', 'endsec')
_JSON_DURATION = ('duration', 'dur', 'durationms', 'ddurationms', 'length')
_JSON_TEXT = ('text', 'content', 'caption', 'subtitle', 'line', 'lines', 'transcript', 'sentence', 'value',
              'body', 'utterance', 'words', 'title', 'subtitlecontent', 'textcontent')
_JSON_SPEAKER = ('speaker', 'actor', 'character', 'voice', 'name')
_JSON_LISTS = ('subtitles', 'captions', 'segments', 'cues', 'items', 'events', 'results', 'data',
               'lines', 'paragraphs', 'body', 'entries', 'transcript', 'subs')


class Detection:
    """What `detect` found: the shape, how sure it is (0 to 1), and what it
    read on the way, so `parse` need not read it again."""

    def __init__(self, shape, confidence, segments=None, fps=None):
        self.shape = shape
        self.confidence = round(float(confidence), 3)
        self.segments = segments or []
        self.fps = fps

    def __repr__(self):
        return f'Detection({self.shape!r}, {self.confidence}, {len(self.segments)} subtitles)'


def _rounded(seconds):
    return round(max(0.0, float(seconds)), 3)


def _time(match, fps):
    """The seconds of a `_TIME` match, its frames at `fps`; and whether it
    was a plain number of seconds and whether it counted frames."""
    group = match.group
    fps = float(fps or DEFAULT_FPS)
    if group('h') is not None:
        value = int(group('h')) * 3600 + int(group('m')) * 60 + int(group('s'))
        fraction, sep = group('f'), group('sep')
        frames = None
        if fraction:
            if sep in '.,':
                value += int(fraction) / 10 ** len(fraction)
            elif len(fraction) == 3:
                value += int(fraction) / 1000
            else:
                frames = int(fraction)
                value += frames / fps
        return value, 'clock', frames
    if group('dh') is not None:
        value = int(group('dh')) * 3600 + int(group('dm')) * 60 + int(group('ds'))
        frames = int(group('df')) if group('df') else None
        return value + (frames / fps if frames is not None else 0), 'clock', frames
    if group('mm') is not None:
        value = int(group('mm')) * 60 + int(group('ms'))
        if group('mf'):
            value += int(group('mf')) / 10 ** len(group('mf'))
        return value, 'clock', None
    return float(group('sec')), 'seconds', None


def _signature(token):
    """A time's notation, digits aside: "00:00:01.00" is "00:00:00.00"."""
    return re.sub(r'\d', '0', token)


def _scan(line, fps):
    """The times a line begins with and the text after them, or None for a
    line that does not begin with a time: ([(seconds, kind, frames,
    notation)], text)."""
    position = len(line) - len(line.lstrip(_OPEN))
    if not _TIME.match(line, position):
        # "12 00:00:01:00 ...": the subtitle's number, then its times.
        index = _INDEX.match(line)
        if not index:
            return None
        position = index.end()
        position += len(line[position:]) - len(line[position:].lstrip(_OPEN))
    times = []
    first = None
    while True:
        match = _TIME.match(line, position)
        if not match:
            break
        first = first or match.group(0)
        times.append(_time(match, fps) + (_signature(match.group(0)),))
        position = match.end()
        between = _BETWEEN.match(line, position)
        if between and _TIME.match(line, between.end()):
            position = between.end()
            continue
        break
    if not times:
        return None
    text = line[position:].lstrip(_CLOSE)
    text = text.strip() if text.lstrip().startswith('-') else _LEAD.sub('', text).strip()
    # "[tc]text[tc]" and "tc<tab>text<tab>tc" (Unknown 80, 59): the end at
    # the line's end, written as the start is.
    if len(times) == 1 and text:
        tail = re.search(r'[\s\[{<(]*(' + _TIME.pattern + r')[\s\]}>)]*$', text, re.X)
        if tail and tail.start() > 0:
            end = _TIME.match(text, tail.start(1))
            if end and _signature(end.group(0)) == _signature(first):
                times.append(_time(end, fps) + (_signature(end.group(0)),))
                text = text[:tail.start()].rstrip(' \t,|')
    return times, text


def _ends(times):
    """Start and end of a subtitle from the times on its line(s): the first
    is the start; the end the first after it written as the start is and
    not before it ("1 tc 02:00 tc", Unknown 22, has the length between),
    else any later time not before it, else a later time read as the
    length, else None."""
    start, notation = times[0][0], times[0][3]
    later = times[1:]
    for value, _kind, _frames, written in later:
        if written == notation and value >= start:
            return start, value
    for value, _kind, _frames, _written in later:
        if value >= start:
            return start, value
    if later:
        return start, start + later[0][0]
    return start, None


def _fps_for(frame_fields, fps):
    """The rate frames are counted at: the video's, else 25, unless a frame
    field is past what that rate counts."""
    fps = float(fps or DEFAULT_FPS)
    highest = max(frame_fields, default=0)
    if highest >= fps:
        fps = 30.0 if highest < 30 else 60.0
    return fps


def _lines(text):
    return text.replace('\r\n', '\n').replace('\r', '\n').lstrip('\ufeff').split('\n')


def _looks_like_log(texts, starts, explicit_ends):
    """Whether what reads as subtitles is a log or a chat."""
    if not texts:
        return False
    count = len(texts)
    if sum(1 for text in texts if _LOG_WORD.search(text)) >= max(2, count * 0.3):
        return True
    if sum(1 for text in texts if _DATE.search(text)) >= max(2, count * 0.3):
        return True
    if sum(1 for text in texts if _NICK.match(text)) >= max(2, count * 0.3):
        return True
    # Several events stamped the same second is how logs go; subtitles
    # given only a start do not share one.
    if not explicit_ends and len(starts) > 2:
        same = sum(1 for a, b in zip(starts, starts[1:]) if abs(a - b) < 0.0005)
        if same >= max(2, (len(starts) - 1) * 0.2):
            return True
    return False


def _monotonic(starts):
    if len(starts) < 2:
        return 1.0
    return sum(1 for a, b in zip(starts, starts[1:]) if b >= a) / (len(starts) - 1)


def _finish(segments):
    """Ends for the subtitles that gave none, in time order."""
    segments.sort(key=lambda segment: segment['start'])
    for index, segment in enumerate(segments):
        if segment.get('end') is None:
            following = segments[index + 1]['start'] if index + 1 < len(segments) else None
            segment['end'] = following if following is not None and following > segment['start'] \
                else _rounded(segment['start'] + TAIL)
        segment['start'] = _rounded(segment['start'])
        segment['end'] = _rounded(max(segment['start'], segment['end']))
    return segments


# ------------------------------------------------------------- lines


def _read_lines(text, fps):
    """The inline and block shapes, one reading for both: a line that begins
    with a time starts a subtitle, its text after the times and on the lines
    under it, until a blank line or the next time. Returns a Detection or
    None."""
    lines = _lines(text)
    first_pass = [(_scan(line, DEFAULT_FPS) if line.strip() else None) for line in lines]
    frame_fields = [frames for scanned in first_pass if scanned for _v, _k, frames, _n in scanned[0] if frames is not None]
    fps = _fps_for(frame_fields, fps)
    cues = []                          # [times, text lines, explicit end?]
    current = None
    covered = header = 0
    time_lines = inline_lines = 0
    nonblank = 0
    for position, line in enumerate(lines):
        if not line.strip():
            # A blank line ends a subtitle's text; one between its times
            # and its text (Unknown 18) does not.
            if current is not None and current[1]:
                current = None
            continue
        nonblank += 1
        scanned = _scan(line, fps) if first_pass[position] else None
        if scanned:
            times, body = scanned
            seconds_only = all(time[1] == 'seconds' for time in times)
            if seconds_only and len(times) < 2:
                scanned = None            # "3.5 million ...": a number, not a time
            elif any(time[1] == 'seconds' for time in times) and not seconds_only:
                scanned = None
        if scanned:
            times, body = scanned
            time_lines += 1
            covered += 1
            # The end on the line under the start (Unknown 54, 64):
            # merged when the subtitle so far has one time and no text.
            if current is not None and not current[1] and len(current[0]) == 1 and not body:
                current[0].extend(times)
                continue
            # The end on a line of its own after the text (Unknown 7):
            # a lone time with a time line, or nothing, after it.
            if current is not None and current[1] and len(current[0]) == 1 and not body and len(times) == 1:
                following = next((other for other in lines[position + 1:] if other.strip()), '')
                if not following or _scan(following, fps):
                    current[0].extend(times)
                    current = None
                    continue
            current = [list(times), [body] if body else []]
            if body:
                inline_lines += 1
            cues.append(current)
            continue
        if _NUMBER_LINE.match(line):
            following = next((other for other in lines[position + 1:] if other.strip()), '')
            if _scan(following, fps):
                covered += 1
                continue
        if _MARKUP.match(line):
            covered += 1                # [HEADER], [/BODY], <end subtitles>
            continue
        if current is not None:
            current[1].append(line.strip())
            covered += 1
        elif not cues:
            header += 1                 # a title, a tool's banner
    if len(cues) < 2 or not nonblank:
        return None
    segments = []
    starts = []
    explicit = 0
    for times, body in cues:
        start, end = _ends(times)
        if end is not None:
            explicit += 1
        starts.append(start)
        segments.append({'start': start, 'end': end, 'text': '\n'.join(body)})
    texts = [segment['text'] for segment in segments if segment['text']]
    if len(texts) < max(2, len(segments) * 0.7):
        return None
    if _looks_like_log(texts, starts, explicit >= len(segments) * 0.5):
        return None
    # A few lines before the first subtitle are the file's own heading.
    counted = nonblank - min(header, 10)
    coverage = covered / counted
    timed = time_lines / counted
    monotonic = _monotonic(starts)
    average = sum(len(text) for text in texts) / len(texts)
    lines_each = sum(text.count('\n') + 1 for text in texts) / len(texts)
    if coverage < 0.75 or timed < 0.15 or monotonic < 0.8 or average > 250 or lines_each > 5:
        return None
    confidence = 0.5 * coverage + 0.3 * monotonic + 0.2 * (0.5 + 0.5 * explicit / len(segments))
    shape = INLINE if inline_lines * 2 >= len(cues) else BLOCK
    return Detection(shape, confidence, _finish(segments), fps)


# ------------------------------------------------------------- tables


def _key(name):
    """A header or key to compare: lower case, its "(unit)" and its
    spaces, dashes and underscores gone."""
    return re.sub(r'[\s_\-().#:]+', '', re.sub(r'\([^)]*\)', '', str(name).lower()))


def _named(name, names):
    """Whether a header names one of `names`: the name itself, or (for
    names of four letters or more) the name first ("Start time in
    milliseconds")."""
    key = _key(name)
    return any(key == candidate or (len(candidate) >= 4 and key.startswith(candidate)) for candidate in names)


def _clock_cell(cell, fps):
    """Seconds for a cell that is wholly a clock (not a plain number)."""
    match = _TIME.match(cell.strip())
    if not match or match.end() != len(cell.strip()) or match.group('sec') is not None:
        return None
    return _time(match, fps)


def _number(cell):
    try:
        return float(cell.strip().replace(',', '.')) if cell.strip() else None
    except ValueError:
        return None


def _unit(header):
    """What a numeric column's header says its numbers count: milliseconds
    unless it says seconds or frames (Csv, Tsv1, Tsv2)."""
    name = str(header).lower()
    if 'frame' in name:
        return 'frames'
    if re.search(r'milli|msec|\bms\b|\(ms\)', name):
        return 'ms'
    if re.search(r'\bsec|\(s\)|seconds', name):
        return 'seconds'
    return 'ms'


def _rows(text, delimiter):
    """The rows of a table, each a list of its cells as written (spaces
    kept). Quoted as CSV quotes, and as forgivingly as Subtitle Edit reads
    its own: two quotes in a quoted cell are one, a quote not followed by
    the delimiter or the line's end is part of the text (Csv2 writes the
    text's quotes as they are), and with commas or semicolons a line break
    inside quotes is the cell's (Csv and Csv2 write a subtitle's lines so).
    A quote that does not close is the text's own ('"Quoted", he said' in
    Audition's tab-separated markers, which quote nothing)."""
    text = '\n'.join(_lines(text))
    if delimiter in ',;':
        rows = _split_rows(text, delimiter, True)
        if rows is not None:
            return rows
    return _split_rows(text, delimiter, False)


def _split_rows(text, delimiter, spanning):
    """The rows `_rows` reads; None when spanning and a quote never closes."""
    if not spanning:
        text += '\n'                    # the last line ends as the others do
    rows, row, cell = [], [], []
    quoted = False
    opened = -1                         # where the quoted cell's quote is
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if quoted:
            if char == '"':
                if text[index + 1:index + 2] == '"':
                    cell.append('"')
                    index += 2
                    continue
                after = index + 1
                while after < length and text[after] in ' \t' and text[after] != delimiter:
                    after += 1
                if after >= length or text[after] in (delimiter, '\n'):
                    quoted = False
                    index = after
                    continue
                cell.append('"')
            elif char == '\n' and (not spanning or cell.count('\n') >= 3):
                if spanning:
                    return None         # more lines than a subtitle has
                # Not closed on its line: read the cell again, its quote
                # a character like the others.
                quoted, cell, index = False, ['"'], opened + 1
                continue
            else:
                cell.append(char)
            index += 1
            continue
        if char == '"' and not ''.join(cell).strip():
            quoted, cell, opened = True, [], index
        elif char == delimiter:
            row.append(''.join(cell))
            cell = []
        elif char == '\n':
            row.append(''.join(cell))
            cell = []
            if any(part.strip() for part in row):
                rows.append(row)
            row = []
        else:
            cell.append(char)
        index += 1
    if quoted:
        return None
    row.append(''.join(cell))
    if any(part.strip() for part in row):
        rows.append(row)
    return rows


def _read_table(text, fps, dot_frames=False):
    """The table shape: a Detection, or None. The width is the most cells
    four rows in five have; a row with more is one whose text holds the
    delimiter, unquoted (Csv2's "the text is the rest of the line")."""
    best = None
    for delimiter in _DELIMITERS:
        rows = _rows(text, delimiter)
        if len(rows) < 2:
            continue
        widths = [len(row) for row in rows]
        width = max(width for width in set(widths) if sum(1 for count in widths if count >= width) >= len(widths) * 0.8)
        if width < 2:
            continue
        steady = sum(1 for count in widths if count == width) / len(widths)
        found = _table_columns(rows, width, fps, delimiter, steady, dot_frames)
        if found and (best is None or found.confidence > best.confidence):
            best = found
    return best


def _table_columns(rows, width, fps, delimiter=',', steady=1.0, dot_frames=False):
    rows = [row + [''] * (width - len(row)) for row in rows if len(row) >= width - 1]
    header = None
    first = rows[0]
    if not any(_clock_cell(cell, fps) for cell in first) and any(re.search(r'[^\W\d_]', cell) for cell in first):
        header, rows = first, rows[1:]
    if len(rows) < 1 or (header is None and len(rows) < 2):
        return None
    frame_fields = []
    for row in rows:
        for cell in row:
            clock = _clock_cell(cell, DEFAULT_FPS)
            if clock and clock[2] is not None:
                frame_fields.append(clock[2])
    # Subtitle Edit's Csv5: no header, "hh:mm:ss.ff" with frames after the
    # dot, in a .csv (the same times elsewhere are hundredths).
    csv5 = dot_frames and header is None and all(
        all(re.fullmatch(r'\d\d:\d\d:\d\d\.\d\d', row[column].strip()) for row in rows) for column in (0, 1))
    if csv5:
        frame_fields = [int(row[column].strip()[-2:]) for row in rows for column in (0, 1)]
    fps = _fps_for(frame_fields, fps)
    names = header or [''] * width
    clocks, numbers = [], []
    for column in range(width):
        cells = [row[column] for row in rows if row[column].strip()]
        if not cells:
            continue
        if sum(1 for cell in cells if _clock_cell(cell, fps)) >= len(cells) * 0.9:
            clocks.append(column)
        elif header is not None and all(_number(cell) is not None for cell in cells):
            numbers.append(column)

    def pick(names_wanted, pool):
        return next((column for column in pool if _named(names[column], names_wanted)), None)

    start = pick(_START_NAMES, clocks + numbers)
    end = pick(_END_NAMES, [column for column in clocks + numbers if column != start])
    duration = pick(_DURATION_NAMES, [column for column in clocks + numbers if column not in (start, end)])
    # Headerless, or a header that names no times: the clocks in order.
    unnamed = [column for column in clocks if column not in (start, end, duration)]
    if start is None and unnamed:
        start = unnamed.pop(0)
    if end is None and duration is None and unnamed:
        end = unnamed.pop(0)
    # Every table Subtitle Edit reads gives both ends; a column of clocks
    # alone is as likely a timetable.
    if start is None or (end is None and duration is None):
        return None
    if start in numbers and header is None:
        return None
    used = {start, end, duration}
    speaker = next((column for column in range(width) if column not in used and header
                    and _named(names[column], _SPEAKER_NAMES)), None)
    if header and [_key(name) for name in header] == _AUDITION:
        speaker = 5                     # Subtitld's own export writes the speaker there
    used.add(speaker)
    text_columns = [column for column in range(width) if column not in used and header and _named(names[column], _TEXT_NAMES)]
    if not text_columns:
        candidates = [column for column in range(width) if column not in used and column not in clocks and column not in numbers]
        if not candidates:
            return None

        def letters(column):
            return sum(len(re.findall(r'[^\W\d_]', row[column])) for row in rows)
        best = max(candidates, key=letters)
        if not letters(best):
            return None
        text_columns = [best]
    # A row with more cells than the table: its text held the delimiter,
    # which is the text again when the text is the last column; any other
    # table is that wide throughout, or nearly.
    if text_columns == [width - 1]:
        rows = [row[:width - 1] + [delimiter.join(row[width - 1:])] for row in rows]
    elif steady < 0.8:
        return None

    def seconds(column, cell):
        if csv5 and column in (start, end):
            hours, minutes, rest = cell.strip().split(':')
            return int(hours) * 3600 + int(minutes) * 60 + int(rest[:2]) + int(rest[3:]) / fps
        if column in clocks:
            clock = _clock_cell(cell, fps)
            return clock[0] if clock else None
        number = _number(cell)
        if number is None:
            return None
        unit = _unit(names[column])
        return number / 1000 if unit == 'ms' else number / fps if unit == 'frames' else number

    segments, starts = [], []
    bad = 0
    for row in rows:
        begin = seconds(start, row[start])
        if begin is None:
            bad += 1
            continue
        finish = seconds(end, row[end]) if end is not None else None
        if finish is None and duration is not None:
            length = seconds(duration, row[duration])
            finish = begin + length if length is not None else None
        if finish is not None and finish < begin:
            bad += 1
            continue
        body = '\n'.join(part.replace('\\n', '\n').replace('///', '\n').strip()
                         for part in (row[column] for column in text_columns) if part.strip())
        segment = {'start': begin, 'end': finish, 'text': body}
        if speaker is not None and row[speaker].strip():
            segment['speaker'] = row[speaker].strip()
        segments.append(segment)
        starts.append(begin)
    if not segments or (header is None and len(segments) < 2) or bad > len(rows) * 0.1:
        return None
    if sum(1 for row in rows if any(_DATE.search(cell) for cell in row)) >= max(1, len(rows) * 0.3):
        return None                     # dated rows: a log or a calendar
    texts = [segment['text'] for segment in segments if segment['text']]
    if len(texts) < len(segments) * 0.7:
        return None
    if _looks_like_log(texts, starts, end is not None or duration is not None):
        return None
    monotonic = _monotonic(starts)
    if monotonic < 0.8:
        return None
    confidence = 0.6 + 0.2 * monotonic + (0.2 if header is not None else 0.1)
    return Detection(TABLE, confidence, _finish(segments), fps)


# ------------------------------------------------------------- JSON


def _json_seconds(value, key, fps, whole_ms):
    """Seconds for a JSON time value under `key`."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        k = _key(key)
        if re.search(r'ms$|milli', k) or whole_ms:
            return value / 1000
        return float(value)
    if isinstance(value, str):
        clock = _clock_cell(value, fps)
        if clock:
            return clock[0]
        number = _number(value.rstrip('s')) if re.fullmatch(r'\s*\d+(?:[.,]\d+)?s?\s*', value) else None
        if number is not None:
            return number / 1000 if re.search(r'ms$|milli', _key(key)) else number
    return None


def _json_field(item, names):
    for key in item:
        if _key(key) in names:
            return key
    return None


def _json_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        return '\n'.join(value)
    if isinstance(value, list) and all(isinstance(part, dict) for part in value):
        # Words with their own text ("words": [{"word": ...}]).
        parts = [part.get('text') or part.get('word') or part.get('w') or '' for part in value]
        if all(isinstance(part, str) for part in parts):
            return ' '.join(part.strip() for part in parts if part.strip())
    return None


def _json_lists(data, depth=0):
    """The arrays of objects in a JSON document, the top one first."""
    if isinstance(data, list) and data and sum(1 for item in data if isinstance(item, dict)) >= len(data) * 0.9:
        yield data
    if depth >= 3:
        return
    if isinstance(data, dict):
        keys = sorted(data, key=lambda key: 0 if _key(key) in _JSON_LISTS else 1)
        for key in keys:
            yield from _json_lists(data[key], depth + 1)
    elif isinstance(data, list):
        for item in data[:1]:
            if isinstance(item, (dict, list)):
                yield from _json_lists(item, depth + 1)


def from_json(data, fps=None):
    """The json shape for a parsed JSON document: a Detection, or None."""
    fps = float(fps or DEFAULT_FPS)
    best = None
    for items in _json_lists(data):
        dicts = [item for item in items if isinstance(item, dict)]
        sample = next((item for item in dicts if _json_field(item, _JSON_START) and _json_field(item, _JSON_TEXT)), None)
        if sample is None:
            continue
        start_key = _json_field(sample, _JSON_START)
        end_key = _json_field(sample, _JSON_END)
        length_key = _json_field(sample, _JSON_DURATION)
        text_key = _json_field(sample, _JSON_TEXT)
        speaker_key = _json_field(sample, tuple(name for name in _JSON_SPEAKER if name != _key(text_key)))
        # Whole numbers under a key that names no unit: milliseconds when
        # the subtitles would otherwise last minutes.
        values = [(item.get(start_key), item.get(end_key) if end_key else None,
                   item.get(length_key) if length_key else None) for item in dicts]
        numbers = [value for row in values for value in row if value is not None]
        whole_ms = False
        if numbers and all(isinstance(value, int) and not isinstance(value, bool) for value in numbers):
            lengths = sorted((row[1] - row[0]) if row[1] is not None and row[0] is not None else row[2]
                             for row in values if (row[1] is not None and row[0] is not None) or row[2] is not None)
            if lengths and lengths[len(lengths) // 2] >= 100:
                whole_ms = True
        segments, starts = [], []
        for item in dicts:
            text = _json_text(item.get(text_key))
            begin = _json_seconds(item.get(start_key), start_key, fps, whole_ms)
            if begin is None or text is None:
                continue
            finish = _json_seconds(item.get(end_key), end_key, fps, whole_ms) if end_key else None
            if finish is None and length_key:
                length = _json_seconds(item.get(length_key), length_key, fps, whole_ms)
                finish = begin + length if length is not None else None
            if finish is not None and finish < begin:
                continue
            segment = {'start': begin, 'end': finish, 'text': text.strip()}
            speaker = item.get(speaker_key) if speaker_key else None
            if isinstance(speaker, str) and speaker.strip():
                segment['speaker'] = speaker.strip()
            segments.append(segment)
            starts.append(begin)
        if not segments or len(segments) < len(dicts) * 0.5:
            continue
        monotonic = _monotonic(starts)
        if monotonic < 0.8:
            continue
        found = Detection(JSON, 0.6 + 0.2 * monotonic + 0.2 * len(segments) / len(dicts), _finish(segments), fps)
        if best is None or len(found.segments) > len(best.segments):
            best = found
    return best


# ------------------------------------------------------------- entry points


def _named_shape(text, fps):
    """SRT, WebVTT and MicroDVD saved as .txt: their own readers read them."""
    head = text.lstrip('\ufeff \t\n')
    if head.startswith('WEBVTT'):
        segments = webvtt.parse(text)
        return Detection(WEBVTT, 1.0, segments) if segments else None
    if re.search(r'^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}', text, re.M):
        segments = srt.parse(text)
        if len(segments) >= 1:
            return Detection(SRT, 0.95, segments)
    lines = [line for line in _lines(text) if line.strip()]
    framed = sum(1 for line in lines if re.match(r'\s*\{\d+\}\{\d*\}', line))
    if lines and framed >= 2 and framed >= len(lines) * 0.8:
        segments, declared = microdvd.parse(text, fps)
        if segments:
            return Detection(FRAMES, 0.9, segments, declared or fps or DEFAULT_FPS)
    return None


def detect(text, fps=None, extension=None):
    """Which shape `text` has, as a Detection with its confidence and the
    subtitles read, or None for text that is not subtitles in any of them
    (or not surely enough: below `THRESHOLD`). The file's `extension`
    ("csv") settles what only it can: Csv5's frames after a dot."""
    if not text or not text.strip():
        return None
    found = _named_shape(text, fps)
    if found:
        return found
    stripped = text.lstrip('\ufeff \t\n')
    if stripped[:1] in '[{':
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if data is not None:
            found = from_json(data, fps)
            return found if found and found.confidence >= THRESHOLD else None
    candidates = [shape for shape in (_read_lines(text, fps), _read_table(text, fps, extension == 'csv')) if shape]
    if not candidates:
        return None
    best = max(candidates, key=lambda shape: (shape.confidence, len(shape.segments)))
    # Rows that also read as lines read better as rows: the table
    # understands quotes and columns ("Hello, there" in Csv4).
    table = next((shape for shape in candidates if shape.shape == TABLE), None)
    if table and table.confidence >= THRESHOLD and len(table.segments) == len(best.segments):
        best = table
    return best if best.confidence >= THRESHOLD else None


def looks_like(head, fps=None):
    """Whether decoded text (a whole file, or its first part) is subtitles
    in one of these shapes."""
    if isinstance(head, (bytes, bytearray)):
        head = srt.decode(bytes(head))
    return detect(head, fps) is not None


def parse(text, fps=None):
    """The subtitles in `text` and the shape they were in:
    ([{'start', 'end', 'text'}], Detection). ValueError when none is."""
    found = detect(text, fps)
    if found is None:
        raise ValueError('No subtitle times found in this text')
    return [dict(segment) for segment in found.segments], found


def read(path, fps=None):
    """The subtitles in the file at `path`, and the Detection."""
    with open(path, 'rb') as text_file:
        return parse(srt.decode(text_file.read()), fps)
