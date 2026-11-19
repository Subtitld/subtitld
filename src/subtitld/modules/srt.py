"""SubRip (.srt): read the files people have, write ones every player reads.

Reading forgives, because SRT files come from everywhere:

- the encoding: a byte order mark (UTF-8, UTF-16, UTF-32) when there is
  one, else UTF-8, else the detected one (or the Windows code page that
  extends it: CP949 for EUC-KR, CP932 for Shift JIS ...), else
  Windows-1252; Windows-1250 (Czech, Slovak, Polish, Croatian ...) when
  the guess is Windows-1252 or none and the letters say otherwise (see
  _central_european);
- line ends of any kind;
- subtitles are found by their timing lines, not by blank lines, so a
  missing blank line, a blank line inside the text, missing numbers and
  leading or extra blank lines all read right;
- an end before its start, which lasts until the next subtitle;
- timings with a dot for the comma, one-digit hours, fewer or more than
  three decimals (",5" is half a second), a short arrow ("->"), and the
  coordinates some tools write after the end time.

The text keeps its formatting tags as written (modules.markup understands
them). Lines are trimmed, blank lines inside a subtitle go, and the
subtitles come sorted by start.

Writing gives plain SubRip: numbered from 1 in order of start, times as
"HH:MM:SS,mmm" to the nearest millisecond (hours past 99 if need be), no
empty subtitles and no blank line inside one (a blank line ends a subtitle
in every player), in UTF-8 with "\\n" line ends.
"""

import re

import chardet

from subtitld.modules import markup

_STAMP = r'(\d+):(\d{1,2}):(\d{1,2})(?:[,.](\d+))?'
_TIMING = re.compile(r'^\s*' + _STAMP + r'\s*(?:-{1,2}|–|—)\s*>\s*' + _STAMP)
_NUMBER = re.compile(r'^\s*(\d+)\s*$')
_BOMS = (
    (b'\xef\xbb\xbf', 'utf-8'),
    (b'\xff\xfe\x00\x00', 'utf-32-le'),     # before UTF-16's, which it starts with
    (b'\x00\x00\xfe\xff', 'utf-32-be'),
    (b'\xff\xfe', 'utf-16-le'),
    (b'\xfe\xff', 'utf-16-be'),
)
_DETECT_BYTES = 256 * 1024
# The encodings Windows wrote these files in extend the ones chardet names
# after their common characters: EUC-KR lacks syllables CP949 has, Shift
# JIS the symbols CP932 has. Decoded by the narrower one, those characters
# would fail the whole file.
_SUPERSETS = {'euc-kr': 'cp949', 'shift_jis': 'cp932', 'gb2312': 'gb18030', 'gbk': 'gb18030',
              'big5': 'big5hkscs', 'iso-8859-1': 'cp1252', 'windows-1252': 'cp1252'}
# Windows-1250 against Windows-1252, byte by byte (the two code pages'
# tables). Letters in 1250 that 1252 leaves undefined: Ť, Ź, ť.
_CE_ONLY = frozenset(b'\x8d\x8f\x9d')
# Letters in 1250 that are signs in 1252, which no Western word has after a
# letter: ł/³, ą/¹, ľ/¾, Ľ/¼, Ą/¥, ż/¿, ź/Ÿ.
_CE_SIGNS = frozenset(b'\xb3\xb9\xbe\xbc\xa5\xbf\x9f')
# Š, Ž, š, ž: the same in both, and Central European words' letters.
_CE_SHARED = frozenset(b'\x8a\x8e\x9a\x9e')
# Letters both have, different ones: č/è, ě/ì, ř/ø, ů/ù, ň/ò, ď/ï, ć/æ,
# đ/ð, ő/õ, ű/û, ĺ/å, ă/ã, ę/ê, ń/ñ, ŕ/à, in both cases.
_CE_LETTERS = frozenset(b'\xe8\xec\xf8\xf9\xf2\xef\xe6\xf0\xf5\xfb\xe5\xe3\xea\xf1\xe0'
                        b'\xc8\xcc\xd8\xd9\xd2\xcf\xc6\xd0\xd5\xdb\xc5\xc3\xca\xd1\xc0')


def decode(data):
    """The text of an SRT file's bytes, with "\\n" line ends."""
    text = None
    for bom, codec in _BOMS:
        if data.startswith(bom):
            text = data[len(bom):].decode(codec, 'replace')
            break
    if text is None:
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            encoding = _guess(data[:_DETECT_BYTES])
            try:
                text = data.decode(encoding) if encoding else None
            except (LookupError, UnicodeDecodeError):
                text = None
            if text is None:
                text = data.decode('cp1252', 'replace')
    return text.replace('\r\n', '\n').replace('\r', '\n')


def _guess(data):
    """The code page chardet takes legacy text in, or None. Short files of
    a few lines with letters past ASCII can leave it without a guess (a
    Czech AQTitle among many time lines, FFmpeg's sample): asked again
    of those lines alone, it has one."""
    encoding = chardet.detect(data).get('encoding')
    if not encoding:
        lettered = b'\n'.join(line for line in data.split(b'\n') if any(byte > 0x7f for byte in line))
        encoding = chardet.detect(lettered).get('encoding') if lettered else None
    encoding = _SUPERSETS.get((encoding or '').lower(), encoding)
    if encoding in (None, 'cp1252') and _central_european(data):
        return 'cp1250'
    return encoding


def _central_european(data):
    """Whether bytes that are not UTF-8 read as Windows-1250 rather than
    Windows-1252: a letter only 1250 has, a 1252 sign standing after a
    letter (Polish "się" is fine both ways, "ł" is "³" in 1252), or Š or
    Ž beside a letter the two read differently (Czech "ř" is "ø")."""
    if any(byte in _CE_ONLY for byte in data):
        return True
    for index in range(1, len(data)):
        if data[index] in _CE_SIGNS and (chr(data[index - 1]).isalpha() if data[index - 1] < 0x80
                                         else data[index - 1] >= 0xc0):
            return True
    return any(byte in _CE_SHARED for byte in data) and any(byte in _CE_LETTERS for byte in data)


def _seconds(hours, minutes, seconds, fraction):
    value = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    if fraction:
        value += int(fraction) / 10 ** len(fraction)
    return round(value, 3)


def parse(text):
    """The subtitles in SRT text: [{'start', 'end', 'text'}], by start."""
    lines = text.split('\n')
    timings = [(index, match) for index, line in enumerate(lines) if (match := _TIMING.match(line))]
    segments = []
    # The number written before a subtitle, when there is one: the next is
    # most likely one more.
    number = None
    if timings and timings[0][0] > 0:
        found = _NUMBER.match(lines[timings[0][0] - 1])
        number = int(found.group(1)) if found else None
    for position, (index, match) in enumerate(timings):
        last = position + 1 == len(timings)
        body = [line.strip() for line in lines[index + 1:timings[position + 1][0] if not last else len(lines)]]
        while body and not body[-1]:
            body.pop()
        next_number = None
        if not last and body:
            found = _NUMBER.match(body[-1])
            # The next subtitle's number, unless it is this one's text: it
            # stands after a blank line, or is the number that comes next.
            expected = number + 1 if number is not None else position + 2
            if found and (len(body) > 1 and not body[-2] or int(found.group(1)) == expected):
                next_number = int(found.group(1))
                body.pop()
        start = _seconds(*match.groups()[0:4])
        end = _seconds(*match.groups()[4:8])
        segments.append({
            'start': start,
            'end': end if end >= start else None,
            'text': '\n'.join(line for line in body if line),
        })
        number = next_number
    segments.sort(key=lambda segment: segment['start'])
    # An end before its start is a typo the subtitle should not vanish
    # for: it lasts until the next one, as FFmpeg shows it.
    for index, segment in enumerate(segments):
        if segment['end'] is None:
            following = segments[index + 1]['start'] if index + 1 < len(segments) else segment['start']
            segment['end'] = max(segment['start'], following)
    return segments


def read(path):
    """The subtitles in the SRT file at `path`."""
    with open(path, 'rb') as srt_file:
        return parse(decode(srt_file.read()))


def format_time(seconds):
    """SRT time for `seconds`: "HH:MM:SS,mmm", to the nearest millisecond."""
    total = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(total, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, millis = divmod(rest, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}'


def write(segments):
    """SRT text for `segments`."""
    blocks = []
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        text = str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
        lines = [line.strip() for line in text.split('\n')]
        lines = [line for line in lines if line]
        if not markup.plain('\n'.join(lines)).strip():
            continue
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', start)))
        blocks.append(f'{len(blocks) + 1}\n{format_time(start)} --> {format_time(end)}\n' + '\n'.join(lines))
    return '\n\n'.join(blocks) + '\n' if blocks else ''


def save(path, segments, encoding='utf-8', line_end='\n'):
    """Write `segments` to `path` as SRT: in UTF-8 unless an older player
    needs otherwise ('utf-8-sig' adds a byte order mark; a character the
    encoding lacks becomes "?"), with "\\n" or "\\r\\n" line ends."""
    with open(path, 'w', encoding=encoding, errors='replace', newline=line_end) as srt_file:
        srt_file.write(write(segments))
