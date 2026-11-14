"""LRC (.lrc): the lyrics files music players scroll in time with a song,
and the enhanced LRC karaoke players light word by word.

An LRC is a line of lyrics after the moment it is sung, "[01:12.40]", in
minutes, seconds and hundredths (some write thousandths, a colon before
them, or none at all). A line sung more than once may carry all its
moments, "[00:21.10][01:12.40]Chorus". A line is shown until the next one,
and a moment with no words after it clears the screen, so that is where
a line ends; the last line, with nothing after it, gets `TAIL`.

At the top, ID tags say what the song is: [ti:] its title, [ar:] the
artist, [al:] the album, [au:] who wrote it, [by:] who made the LRC,
[length:] how long the song is, and [offset:], milliseconds to take from
every moment (a positive one shows the lines sooner). [re:] and [ve:] name
the program that wrote the file, and [#:] is a comment.

Enhanced LRC (also A2) adds the moment each word is sung, "<01:12.40>",
inside the line: "[01:12.40]<01:12.40>Hel<01:12.90>lo <01:13.20>world
<01:13.80>". A word lasts until the next stamp; a stamp with nothing after
it ends the last word.

Walaoke (Walasoft's player) marks who sings a duet: "M:", "F:" or "D:"
after a line's moment, for a man, a woman or both, in a colour of each;
a line without one is sung by whoever sang the line before (Wikipedia's
"LRC (file format)" is the only description there is of it).

Players and editors write the moments three ways, and Subtitle Edit has a
reader for each (Lrc, Lrc3DigitsMs, LrcNoEndTime): hundredths, with a
moment alone where a line ends before the next begins; thousandths
("[01:12.400]"); and no ending moments at all, each line shown until the
next. Reading takes any of them, and `dialect` says which a file is in.

Reading gives a subtitle a line and moment, the offset applied, the text
as written; word stamps become karaoke codes (modules.karaoke), each word
as long as its stamps say. The ID tags are the subtitles' metadata
('title', 'artist', 'album', 'songwriter', 'author', 'length'); [re:],
[ve:] and comments are not kept. A Walaoke part is the subtitle's
speaker, 'Male', 'Female' or 'Duet' (`PARTS`), from its line on.

Writing gives the metadata's tags, then a line a subtitle at its start,
in hundredths (or thousandths), with no offset; a subtitle's lines joined
by a space, as LRC has no line breaks and no formatting; word stamps where
its text has karaoke codes, the last stamp its last word's end. Where a
subtitle ends before the next begins, or is the last, a stamp with no
words says so, unless the dialect has no ending moments. Asked for
Walaoke's parts, a line whose speaker is not the line before's starts
with the part: 'Male', 'Female' and 'Duet' (or M, F, D, man, woman, both)
are theirs, and other names take M, F, then D in the order they first
speak. Walaoke cannot go back to no part: a subtitle without a speaker
after one with keeps the part before.
"""

import re

from subtitld.modules import karaoke, markup
from subtitld.modules.srt import decode

# The last line, with no line after it to end it, lasts this long.
TAIL = 3.0

_STAMP = re.compile(r'\[\s*(\d+)\s*:\s*(\d{1,2})(?:\s*[.:]\s*(\d{1,3}))?\s*\]')
_WORD = re.compile(r'<\s*(\d+)\s*:\s*(\d{1,2})(?:\s*[.:]\s*(\d{1,3}))?\s*>')
_TAG = re.compile(r'^\s*\[\s*([A-Za-z#]+)\s*:(.*)\]\s*$')
_OFFSET = re.compile(r'^\s*([+-]?\d+)')

# Walaoke's parts, as the speakers they are read as.
PARTS = {'M': 'Male', 'F': 'Female', 'D': 'Duet'}
_PART = re.compile(r'^([MFD]):\s?')
_PART_NAMES = {'m': 'M', 'male': 'M', 'man': 'M', 'f': 'F', 'female': 'F', 'woman': 'F', 'd': 'D', 'duet': 'D',
               'both': 'D'}
# How the moments are written: Subtitle Edit's three LRC formats.
DIALECTS = ('hundredths', 'milliseconds', 'no_end')

# ID tags as the metadata's keys, and back, in the order they are written.
TAGS = {'ti': 'title', 'ar': 'artist', 'al': 'album', 'au': 'songwriter', 'by': 'author', 'length': 'length'}


def _seconds(minutes, seconds, fraction):
    value = int(minutes) * 60 + int(seconds)
    if fraction:
        value += int(fraction) / 10 ** len(fraction)
    return value


def _stamp(seconds, brackets='[]', milliseconds=False):
    """"[mm:ss.xx]", or "<mm:ss.xx>" for a word; "[mm:ss.xxx]" in
    thousandths."""
    scale = 1000 if milliseconds else 100
    units = max(0, round(float(seconds) * scale))
    minutes, units = divmod(units, 60 * scale)
    fraction = f'{units % scale:03d}' if milliseconds else f'{units % scale:02d}'
    return f'{brackets[0]}{minutes:02d}:{units // scale:02d}.{fraction}{brackets[1]}'


def is_lrc(text):
    """Whether `text` has a line that starts with an LRC moment."""
    return any(_STAMP.match(line.strip()) for line in str(text or '').split('\n'))


def dialect(text):
    """Which of `DIALECTS` the LRC `text` writes its moments in:
    'milliseconds' when they have three digits, 'no_end' when no moment
    stands alone to end a line (and there are two lines or more), else
    'hundredths'."""
    alone, lines = 0, 0
    thousandths = False
    for line in str(text or '').split('\n'):
        rest = line.strip()
        found = False
        while True:
            stamp = _STAMP.match(rest)
            if not stamp:
                break
            found = True
            thousandths = thousandths or len(stamp.group(3) or '') == 3
            rest = rest[stamp.end():]
        if found:
            lines += 1
            alone += not rest.strip()
    if thousandths:
        return 'milliseconds'
    if lines >= 2 and not alone:
        return 'no_end'
    return 'hundredths'


def _words(body, shift):
    """(text, [(start, end or None, word)]) for a line's text: the words
    between its word stamps, each moved by `shift`, or the text and [] when
    it has none. A word's end is the next stamp, None for the last one when
    nothing ends it."""
    stamps = list(_WORD.finditer(body))
    if not stamps:
        return body.strip(), []
    words = []
    lead = body[:stamps[0].start()]
    for index, stamp in enumerate(stamps):
        start = round(_seconds(*stamp.groups()) - shift, 3)
        piece = body[stamp.end():stamps[index + 1].start() if index + 1 < len(stamps) else len(body)]
        if index == 0:
            piece = lead.lstrip() + piece.lstrip()
        elif piece[:1].isspace() and words:
            # A2 puts spaces either side of a stamp: the space belongs to
            # the word before it.
            if not words[-1][2][-1:].isspace():
                words[-1] = words[-1][:2] + (words[-1][2] + ' ',)
            piece = piece.lstrip()
        if index + 1 == len(stamps):
            piece = piece.rstrip()
        if words and words[-1][1] is None:
            words[-1] = (words[-1][0], start, words[-1][2])
        if piece:
            words.append((start, None, piece))
    if words:
        words[-1] = (words[-1][0], words[-1][1], words[-1][2].rstrip())
    return ''.join(word for _start, _end, word in words), words


def parse(text):
    """(subtitles, metadata) in LRC text: [{'start', 'end', 'text'}] by
    start, and what its ID tags say."""
    lines = str(text or '').lstrip('﻿').split('\n')
    metadata = {}
    offset = 0.0
    for line in lines:
        tag = _TAG.match(line)
        if not tag or _STAMP.match(line.strip()):
            continue
        name, value = tag.group(1).lower(), tag.group(2).strip()
        if name == 'offset':
            number = _OFFSET.match(value)
            offset = int(number.group(1)) / 1000 if number else 0.0
        elif name in TAGS and value:
            metadata[TAGS[name]] = value

    entries = []
    part = ''
    for order, line in enumerate(lines):
        rest = line.strip()
        moments = []
        while True:
            stamp = _STAMP.match(rest)
            if not stamp:
                break
            moments.append(_seconds(*stamp.groups()))
            rest = rest[stamp.end():]
        # Walaoke's part, which lasts until another is given.
        named = _PART.match(rest.lstrip())
        if moments and named:
            part = PARTS[named.group(1)]
            rest = rest.lstrip()[named.end():]
        for moment in moments:
            # A line sung again moves its word stamps with it.
            shift = moments[0] - moment + offset
            body, words = _words(rest, shift)
            entries.append((round(max(0.0, moment - offset), 3), order, body, words, part))
    entries.sort(key=lambda entry: (entry[0], entry[1]))

    segments = []
    for index, (start, _order, body, words, part) in enumerate(entries):
        if not body.strip():
            continue
        if index + 1 < len(entries):
            end = entries[index + 1][0]
        elif words and words[-1][1] is not None:
            end = words[-1][1]
        else:
            end = round(start + TAIL, 3)
        end = max(end, start)
        if words:
            # The last word, when nothing ends it, lasts until the line does.
            pieces = [(max(word_start, start), max(word_end if word_end is not None else end, word_start), word)
                      for word_start, word_end, word in words]
            body = karaoke.text_from(start, pieces)
        segment = {'start': start, 'end': round(end, 3), 'text': body}
        if part:
            segment['speaker'] = part
        segments.append(segment)
    return segments, metadata


def read(path):
    """(subtitles, metadata) in the LRC file at `path`."""
    with open(path, 'rb') as lrc_file:
        text = decode(lrc_file.read())
    if not is_lrc(text):
        raise ValueError('Not an LRC file: no line starts with a time such as [00:12.00].')
    return parse(text)


def _line_text(text):
    """A subtitle's text as one LRC line."""
    return ' '.join(line.strip() for line in markup.plain(text).split('\n') if line.strip())


def _parts(segments):
    """{speaker: Walaoke part} for the speakers of `segments`."""
    parts, others = {}, iter('MFD')
    for segment in segments:
        name = str(segment.get('speaker') or '').strip()
        if not name or name in parts:
            continue
        parts[name] = _PART_NAMES.get(name.lower()) or next(others, 'D')
    return parts


def write(segments, metadata=None, walaoke=False, dialect='hundredths'):
    """`segments` as LRC text, with the ID tags `metadata` has; the
    speakers as Walaoke's parts when `walaoke`; the moments as `dialect`
    (one of `DIALECTS`) has them."""
    milliseconds = dialect == 'milliseconds'
    scale = 1000 if milliseconds else 100
    out = []
    for tag, key in TAGS.items():
        value = ' '.join(str((metadata or {}).get(key) or '').split())
        if value:
            out.append(f'[{tag}:{value}]')
    ordered = sorted(segments, key=lambda segment: (float(segment.get('start', 0)), float(segment.get('end', 0))))
    parts = _parts(ordered) if walaoke else {}
    part = ''
    for index, segment in enumerate(ordered):
        start, end = float(segment.get('start', 0)), float(segment.get('end', 0))
        text = str(segment.get('text') or '')
        words = karaoke.timed(text, start)
        if words:
            spoken = [(word_start, word_end, ' '.join(word.split('\n'))) for word_start, word_end, word in words
                      if word.strip()]
            body = ''.join(_stamp(word_start, '<>', milliseconds) + word for word_start, _end, word in spoken)
            body = (body + _stamp(spoken[-1][1], '<>', milliseconds)) if spoken else ''
        else:
            body = _line_text(text)
        if not body:
            continue
        speaker = parts.get(str(segment.get('speaker') or '').strip(), '')
        if speaker and speaker != part:
            body = f'{speaker}: {body}'
            part = speaker
        out.append(_stamp(start, '[]', milliseconds) + body)
        following = float(ordered[index + 1].get('start', 0)) if index + 1 < len(ordered) else None
        if dialect != 'no_end' and (following is None or round(end * scale) < round(following * scale)):
            out.append(_stamp(end, '[]', milliseconds))
    return '\n'.join(out) + '\n'


def save(path, segments, metadata=None, encoding='utf-8', line_end='\n', walaoke=False, dialect='hundredths'):
    """Write `segments` to `path` as LRC; `walaoke` and `dialect` as
    write() takes them."""
    with open(path, 'w', encoding=encoding, newline=line_end) as lrc_file:
        lrc_file.write(write(segments, metadata, walaoke=walaoke, dialect=dialect))
