"""Cheetah CAP (.cap): the binary caption files Cheetah International's
CAPtioning systems exchange.

Cheetah never published the format. What is here is reverse-engineered —
from Subtitle Edit's reader and writer (libse, CheetahCaption.cs and
CheetahCaptionOld.cs, GPL-3), which were themselves worked out from files
— and is marked below as known (files read and written agree on it) or as
a guess. Cheetah being a line-21 captioning system, its rows and columns
are CEA-608's: 15 rows, 32 columns.

A file is a 128-byte header, then one variable-length record a caption:

    header  EA 22, a version (1 to 3), 00, the number of captions (two
            bytes, low first), 09 A8 AF 4F (unknown, written as Subtitle
            Edit writes it), zeros to 128.
    record  its length (one byte, the length byte included), its kind,
            the start as hours, minutes, seconds and frames (a byte
            each), then
            - kind 0x6n (n lines): the end the same way, then nine bytes
              of style — 12 01 00 00 00 00, the justification (1 left,
              2 right, 3 centre), the row (1 top to 15 bottom) and the
              column of the text (0x10 the centre of 32);
            - kind 0x4n: no end, as the caption lasts until the next one
              starts (02 01 00 00 stands in its place), then five bytes of
              style: 00 00, the justification, the row and the column;
            then the text, a byte a character: 00 between lines (Cheetah
            writes four), D0 for italics to the end of the line, other
            bytes from C0 up codes not known, and a 00 to close.

The characters (known): ASCII, then the line-21 extras at 81 to 95 (♪ á é
í ó ú â ê î ô û à è Ñ ñ ç ¢ £ ¿ ½ ®), and Windows-1252 for the rest below
C0. Any other character is written as its letter without the accent, or
"?".

Guesses: the frame rate, which the header does not say — frames count
within a real second, at the video's rate or the one asked, 25 when there
is neither; the kind's low digit counted past two lines (Subtitle Edit
writes only 1 and 2); the rows and columns read as the nine places (rows
1 to 5 the top, 6 to 10 the middle; columns below 12 the left, above 20
the right) and written as Subtitle Edit writes them (rows 1, 8 and 15;
columns 2, 16 and 30). Text can start up to four bytes before its place
in some files; printable bytes there are read as text, as Subtitle Edit
reads them.

The older Cheetah format (EA 10) is read too: 128 bytes of header, then
178 bytes a caption, its start and end at 0 and 4 and two lines of 38
ASCII characters at 0x1A and 0x40. It is not written.

Writing gives a record with its end for every caption (the kind with an
end, so no caption depends on the next), in order of start; italics for
lines that are italic all through; the nine places; a caption's text cut
where the record's one-byte length would overflow.
"""

import unicodedata

from subtitld.modules import markup

HEADER_SIZE = 128
OLD_RECORD_SIZE = 178
DEFAULT_FPS = 25.0
_MAGIC, _MAGIC_OLD = b'\xea\x22', b'\xea\x10'
_HEADER_UNKNOWN = b'\x09\xa8\xaf\x4f'
_ITALIC = 0xD0
_LINE_BREAK = b'\x00\x00\x00\x00'
# What a record without an end has where the end would be.
_NO_END = (2, 1, 0, 0)
_MAX_TEXT = 255 - 20

# The line-21 extras, at 81 to 95.
_SPECIAL = {
    0x81: '♪', 0x82: 'á', 0x83: 'é', 0x84: 'í', 0x85: 'ó', 0x86: 'ú', 0x87: 'â', 0x88: 'ê', 0x89: 'î', 0x8A: 'ô',
    0x8B: 'û', 0x8C: 'à', 0x8D: 'è', 0x8E: 'Ñ', 0x8F: 'ñ', 0x90: 'ç', 0x91: '¢', 0x92: '£', 0x93: '¿', 0x94: '½',
    0x95: '®',
}
_TO_SPECIAL = {char: byte for byte, char in _SPECIAL.items()}
_SPELLED = {'ß': 'ss', 'Æ': 'AE', 'æ': 'ae', 'Ø': 'O', 'ø': 'o', 'Đ': 'D', 'đ': 'd', 'Þ': 'Th', 'þ': 'th',
            'Œ': 'OE', 'œ': 'oe', '…': '...', '‘': "'", '’': "'", '“': '"', '”': '"', '„': '"', '‚': ',',
            '–': '-', '—': '-'}

# Where a caption sits: the row and column written for each place.
_ROWS = {0: 15, 1: 8, 2: 1}            # bottom, middle, top
_COLUMNS = {0: 2, 1: 16, 2: 30}        # left, centre, right


def is_cap(data):
    """True when `data` begins as a Cheetah CAP file, new or old."""
    return len(data) >= HEADER_SIZE and (data[:2] == _MAGIC_OLD or (data[:2] == _MAGIC and data[2] <= 3))


def _seconds(hours, minutes, seconds, frames, fps):
    return round(hours * 3600 + minutes * 60 + seconds + frames / fps, 3)


def _decode(data):
    """Text with tags for a record's text bytes."""
    runs, italic = [], False
    for byte in data:
        if byte == 0:
            runs.append(('\n', markup.PLAIN))
            italic = False
        elif byte in _SPECIAL:
            runs.append((_SPECIAL[byte], markup.Style(italic=italic)))
        elif byte >= 0xC0 or byte < 0x20:
            if byte == _ITALIC:
                italic = True
        else:
            char = bytes([byte]).decode('cp1252', 'ignore')
            if char:
                runs.append((char, markup.Style(italic=italic)))
    return markup.tidy(runs)


def _place(justification, row, column):
    """The keypad number for a record's style bytes."""
    vertical = 2 if 1 <= row <= 5 else 1 if 6 <= row <= 10 else 0
    if justification == 1:
        horizontal = 0
    elif justification == 2:
        horizontal = 2
    else:
        horizontal = 0 if column < 12 else 2 if column > 20 else 1
    return (1, 4, 7)[vertical] + horizontal


def _parse_old(data, fps):
    """Captions in the older format (EA 10)."""
    segments = []
    for offset in range(HEADER_SIZE, len(data) - OLD_RECORD_SIZE + 1, OLD_RECORD_SIZE):
        record = data[offset:offset + OLD_RECORD_SIZE]
        lines = [record[at:at + 38].split(b'\x00')[0].decode('ascii', 'replace').strip() for at in (0x1A, 0x40)]
        text = '\n'.join(line for line in lines if line)
        if not text:
            continue
        start, end = _seconds(*record[0:4], fps), _seconds(*record[4:8], fps)
        segments.append({'start': start, 'end': end, 'text': text})
    # An end of zero, or before the start, lasts until the next caption.
    for index, segment in enumerate(segments):
        if segment['end'] <= segment['start']:
            following = segments[index + 1]['start'] if index + 1 < len(segments) else 0
            segment['end'] = following if following > segment['start'] else _reading_time(segment)
    return segments


def _reading_time(segment):
    """An end for a caption that does not say one: long enough to read it."""
    return round(segment['start'] + max(1.0, len(markup.plain(segment['text'])) / 15), 3)


def parse(data, fps=DEFAULT_FPS):
    """(subtitles, {'fps'}) in Cheetah CAP bytes, frames counted at `fps`.
    Raises ValueError for bytes that are not CAP."""
    fps = float(fps or DEFAULT_FPS)
    if not is_cap(data):
        raise ValueError('Not a Cheetah CAP file: it does not begin as one does')
    if data[:2] == _MAGIC_OLD:
        return _parse_old(data, fps), {'fps': fps}

    segments, ends_open = [], []
    offset = HEADER_SIZE
    while offset < len(data) - 15:
        length = data[offset]
        if length == 0:
            offset += 1
            continue
        record = data[offset:offset + length]
        offset += length
        if len(record) < 15:
            continue
        kind = record[1]
        start = _seconds(*record[2:6], fps)
        no_end = (kind & 0xF0) == 0x40 or (tuple(record[6:10]) == _NO_END and (kind & 0xF0) != 0x60)
        if no_end:
            justification, row, column = record[12:15]
            text_at = 15
            end = None
        else:
            justification, row, column = record[16:19] if len(record) >= 19 else (3, 15, 16)
            text_at = 19
            end = _seconds(*record[6:10], fps)
        # Some files start the text sooner than their style says: printable
        # bytes just before it are text.
        for _ in range(4):
            if text_at > 10 and record[text_at - 1] >= 0x20 and record[text_at - 1] != _ITALIC:
                text_at -= 1
            else:
                break
        body = _decode(record[text_at:])
        if not markup.plain(body).strip():
            continue
        keypad = _place(justification, row, column)
        segments.append({'start': start, 'end': end,
                         'text': (markup.with_alignment('', keypad) if keypad != 2 else '') + body})
        if end is None:
            ends_open.append(len(segments) - 1)
    # A caption without an end lasts until the next one starts.
    for index in ends_open:
        segment = segments[index]
        following = segments[index + 1]['start'] if index + 1 < len(segments) else 0
        segment['end'] = following if following > segment['start'] else _reading_time(segment)
    for segment in segments:
        segment['end'] = max(segment['start'], segment['end'])
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments, {'fps': fps}


def read(path, fps=DEFAULT_FPS):
    """(subtitles, its settings) in the CAP file at `path`, as parse()
    gives them."""
    with open(path, 'rb') as cap_file:
        return parse(cap_file.read(), fps)


# Writing.
def _encode_char(char):
    """CAP bytes for a character: its own, the letter without its accent,
    or "?"."""
    if char in _TO_SPECIAL:
        return bytes([_TO_SPECIAL[char]])
    try:
        encoded = char.encode('cp1252')
    except UnicodeEncodeError:
        encoded = b''
    if len(encoded) == 1 and 0x20 <= encoded[0] < 0xC0 and encoded[0] not in _SPECIAL:
        return encoded
    if char in _SPELLED:
        return _SPELLED[char].encode('ascii')
    base = ''.join(c for c in unicodedata.normalize('NFKD', char) if not unicodedata.combining(c))
    if base and base != char:
        return b''.join(_encode_char(c) for c in base)
    return b'?'


def _timecode(seconds, fps):
    """(hours, minutes, seconds, frames) for a time: the frames those of its
    second, at `fps`."""
    whole = int(max(0.0, seconds))
    frames = int((max(0.0, seconds) - whole) * fps + 0.5)
    if frames >= int(round(fps)):
        whole, frames = whole + 1, 0
    minutes, second = divmod(whole, 60)
    hours, minute = divmod(minutes, 60)
    return min(hours, 255), minute, second, frames


def _lines(text):
    """[(line text, italic all through)] for a caption's text."""
    lines = [[]]
    for run, style in markup.without_readings(markup.spans(text)):
        for index, part in enumerate(run.split('\n')):
            if index:
                lines.append([])
            lines[-1].extend((char, style.italic) for char in part)
    out = []
    for line in lines:
        letters = ''.join(char for char, _italic in line).strip()
        if letters:
            out.append((letters, all(italic for char, italic in line if char.strip())))
    return out


def write(segments, fps=DEFAULT_FPS):
    """Cheetah CAP bytes for `segments`, frames counted at `fps`."""
    fps = float(fps or DEFAULT_FPS)
    records = []
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        lead, rest = markup.split_lead(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
        lines = _lines(rest)
        if not lines:
            continue
        keypad = markup.alignment(lead)
        text = bytearray()
        for number, (line, italic) in enumerate(lines):
            if number:
                text += _LINE_BREAK
            if italic:
                text.append(_ITALIC)
            text += b''.join(_encode_char(char) for char in line)
        text = bytes(text[:_MAX_TEXT])
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', 0)))
        style = bytes([0x12, 0x01, 0, 0, 0, 0, 3, _ROWS[(keypad - 1) // 3], _COLUMNS[(keypad - 1) % 3]])
        body = bytes([0x60 + min(len(lines), 4)]) + bytes(_timecode(start, fps)) + bytes(_timecode(end, fps)) + style + text
        records.append(bytes([len(body) + 2]) + body + b'\x00')
    count = len(records)
    header = (_MAGIC + bytes([1, 0, count % 256, count // 256 % 256]) + _HEADER_UNKNOWN).ljust(HEADER_SIZE, b'\x00')
    return header + b''.join(records)


def save(path, segments, **options):
    """Write `segments` to `path` as Cheetah CAP; `options` as write() takes
    them."""
    with open(path, 'wb') as cap_file:
        cap_file.write(write(segments, **options))
