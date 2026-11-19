"""KBP (.kbp): Karaoke Builder Studio's project, the words of a song laid
out in pages and timed syllable by syllable, from which it renders CD+G.

There is no published description of it. This follows kbputils (the
ItMightBeKaraoke project's Python library, kbp.py, which reads and writes
it and converts it to ASS in kbp2ass.py), the one open source reader.

A file is sections, each after a line of 29 dashes. "HEADERV2" holds the
template (the palette, the styles, margins, border colour) and then the
track information, a name in ten columns and its value: Status (1 when
the words are synced), Title, Artist, Audio, BuildFile, Intro, Outro,
Comments. Lines starting "'" are comments. Then a "PAGEV2" section a
page of the words, each line a header

    C/A/1234/1567/0/0/0

(its alignment, L, C or R; its style, a letter, A the first, lower case
for one that is not wiped; when it shows and goes, in hundredths of a
second; its offsets right and down; its rotation) and a line a syllable,

    Hel/           1240/1279/0

its text up to "/" ("{-}" stands for a "/" in it), when it is sung from
and to, in hundredths, and how it is wiped (0 the template's way). A
blank line ends a line. KBS ends a syllable a hundredth before the next
begins. An unsynced project has "LYRICSV2" and the words alone.

Reading gives a subtitle a line, from its header's start to its end; its
syllables as karaoke codes (modules.karaoke), a syllable ending a
hundredth before the next taken as reaching it; a style not wiped gives
the words alone. The first line of each page is marked as a page starts
('kar': {'paragraph': True}, as modules.kar marks a paragraph, so a page
is one when written as KAR too). Title and artist are the metadata, and
the header as it was ('kbp') to be written back.

Writing gives the header the file opened had (its title and artist those
of the metadata), else kbputils' default template; then the subtitles in
pages: a page where one was marked, else after a gap of `PAGE_GAP` or
more, or `PAGE_LINES` lines. A subtitle's lines are each a line, shown
for the whole subtitle, in the first style, centred; its syllables by its
karaoke codes, else a syllable a line sung for its share of the letters,
each ending a hundredth before the next. In UTF-8 with Windows line ends,
as kbputils writes it.
"""

import re

from subtitld.modules import karaoke, markup
from subtitld.modules.srt import decode

DIVIDER = '-' * 29
PAGE_GAP = 1.0
PAGE_LINES = 4

_HEADER = re.compile(r'^([LCR])/([A-Za-z])((?:/-?\d+){5})$')
_SYLLABLE = re.compile(r'^(.*?)/\s*(-?\d+)/(-?\d+)/(-?\d+)\s*$')

# kbputils' default template (KBPFile with no file), as it writes it.
TEMPLATE = '\n'.join((
    DIVIDER, 'KARAOKE BUILDER STUDIO', 'www.KaraokeBuilder.com', '', DIVIDER, 'HEADERV2', '',
    "'--- Template Information ---", '',
    "'Palette Colours (0-15)",
    '  055,FFF,000,E70,940,CFF,033,0DD,077,FCF,303,F3F,818,000,FFF,000', '',
    "'Styles (00-19)", "'  Number,Name", "'  Colour: Text,Outline,Text Wipe,Outline Wipe",
    "'  Font  : Name,Size,Style,Charset", "'  Other : Outline*4,Shadow*2,Wiping,Uppercase", '',
    '  Style00,Default,1,2,3,4', '    Arial,12,B,0', '    2,2,2,2,0,0,0,L', '',
    '  Style01,Male,5,6,7,8', '    Arial,12,B,0', '    2,2,2,2,0,0,0,L', '',
    '  Style02,Female,9,10,11,12', '    Arial,12,B,0', '    2,2,2,2,0,0,0,L', '',
    '  Style03,Other,4,8,12,14', '    Arial,12,B,0', '    2,2,2,2,0,0,0,L', '',
    '  StyleEnd', '',
    "'Margins : L,R,T,Line Spacing", '  2,2,7,12', '',
    "'Other: Border Colour,Detail Level", '  0,2', '',
    "'--- Track Information ---", ''))
TRACK_FIELDS = ('Status', 'Title', 'Artist', 'Audio', 'BuildFile', 'Intro', 'Outro', 'Comments')


def is_kbp(text):
    """Whether `text` is a Karaoke Builder Studio project."""
    lines = [line.strip() for line in str(text or '').split('\n')]
    return DIVIDER in lines and any(line in ('HEADERV2', 'PAGEV2', 'LYRICSV2') for line in lines)


def _sections(text):
    """[(name, [lines])] of the sections after each divider; the first,
    before any, named ''."""
    sections = [('', [])]
    lines = str(text or '').lstrip('﻿').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    after_divider = False
    for line in lines:
        if line.strip() == DIVIDER:
            after_divider = True
            continue
        if after_divider and line.strip() and not line.startswith("'"):
            sections.append((line.strip(), []))
            after_divider = False
            continue
        sections[-1][1].append(line)
    return sections


def _track_info(lines):
    """{field: value} of the track information lines."""
    info, field, started = {}, None, False
    for line in lines:
        if line.startswith("'--- Track Information"):
            started = True
            continue
        if not started or line.startswith("'"):
            continue
        if line.startswith(' ') and field:
            info[field] += '\n' + line.strip()
        elif line.strip():
            parts = line.split(None, 1)
            field = parts[0]
            info[field] = parts[1].strip() if len(parts) > 1 else ''
    return info


def _lines(page):
    """[(header fields, [(text, start, end)])] of a page's lines."""
    out, header, syllables = [], None, []
    for line in page + ['']:
        stripped = line.strip()
        if header is None:
            match = _HEADER.match(stripped)
            if match:
                numbers = [int(number) for number in match.group(3)[1:].split('/')]
                header = (match.group(1), match.group(2), numbers[0], numbers[1])
            continue
        if not stripped:
            out.append((header, syllables))
            header, syllables = None, []
            continue
        match = _SYLLABLE.match(line.rstrip())
        if match:
            syllables.append((match.group(1).replace('{-}', '/'), int(match.group(2)), int(match.group(3))))
    return out


def parse(text):
    """(subtitles, metadata) in a KBP project's text. Raises ValueError for
    one that is not, or whose words are not synced."""
    if not is_kbp(text):
        raise ValueError('Not a Karaoke Builder Studio project: it has no HEADERV2 or PAGEV2 section.')
    sections = _sections(text)
    header_lines = next((lines for name, lines in sections if name == 'HEADERV2'), [])
    info = _track_info(header_lines)
    pages = [lines for name, lines in sections if name == 'PAGEV2']
    if not pages and any(name == 'LYRICSV2' for name, _lines_ in sections):
        raise ValueError('This Karaoke Builder Studio project is not synced: its words have no times yet.')
    segments = []
    for page in pages:
        first = True
        for (_align, style, start, end), syllables in _lines(page):
            words = ''.join(text for text, _start, _end in syllables)
            if not words.strip():
                continue
            start, end = max(0, start) / 100, max(0, end) / 100
            if style.islower():
                body = words.strip()
            else:
                pieces = []
                for number, (piece, begin, finish) in enumerate(syllables):
                    following = syllables[number + 1][1] if number + 1 < len(syllables) else None
                    # A syllable ends a hundredth before the next: it reaches it.
                    if following is not None and following - finish == 1:
                        finish = following
                    begin = max(begin / 100, start)
                    pieces.append((begin, max(begin, finish / 100), piece))
                pieces[0] = (pieces[0][0], pieces[0][1], pieces[0][2].lstrip())
                pieces[-1] = (pieces[-1][0], pieces[-1][1], pieces[-1][2].rstrip())
                body = karaoke.text_from(start, [piece for piece in pieces if piece[2]])
            segment = {'start': round(start, 3), 'end': round(max(start, end), 3), 'text': body}
            if first:
                segment['kar'] = {'paragraph': True}
                first = False
            segments.append(segment)
    segments.sort(key=lambda segment: segment['start'])
    metadata = {key: info[field] for key, field in (('title', 'Title'), ('artist', 'Artist')) if info.get(field)}
    head = []
    for line in header_lines:
        if line.startswith("'--- Track Information"):
            break
        head.append(line)
    metadata['kbp'] = {'template': '\n'.join([DIVIDER, 'HEADERV2'] + head).rstrip('\n'), 'track': info}
    return segments, metadata


def read(path):
    """(subtitles, metadata) in the KBP project at `path`."""
    with open(path, 'rb') as kbp_file:
        return parse(decode(kbp_file.read()))


def _cs(seconds):
    return max(0, int(round(float(seconds) * 100)))


def _page_lines(segments):
    """[[(start, end, [(text, start, end)])]] pages of lines for the
    subtitles, times in seconds."""
    pages = []
    previous_end = None
    known = any(isinstance(segment.get('kar'), dict) for segment in segments)
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', start)))
        text = str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
        pieces = karaoke.timed(text, start)
        if not pieces:
            parts = [part for part in markup.plain(text).split('\n') if part.strip()]
            total = sum(len(part) for part in parts) or 1
            pieces, at = [], start
            for part in parts:
                after = at + (end - start) * len(part) / total
                pieces.append((at, after, part + '\n'))
                at = after
        lines = [[]]
        for begin, finish, piece in pieces:
            for number, part in enumerate(piece.split('\n')):
                if number:
                    lines.append([])
                if part:
                    lines[-1].append((part, begin, finish))
        lines = [line for line in lines if ''.join(part for part, _b, _f in line).strip()]
        if not lines:
            continue
        if known:
            new_page = bool((segment.get('kar') or {}).get('paragraph')) or not pages
        else:
            new_page = (not pages or start - previous_end >= PAGE_GAP or len(pages[-1]) + len(lines) > PAGE_LINES)
        if new_page:
            pages.append([])
        pages[-1].extend((start, end, line) for line in lines)
        previous_end = end
    return pages


def write(segments, metadata=None, audio=''):
    """`segments` as a KBP project's text (with "\\n" line ends), the
    title and artist of `metadata`, `audio` the song's file name when the
    project opened did not name one."""
    metadata = metadata or {}
    kept = metadata.get('kbp') if isinstance(metadata.get('kbp'), dict) else {}
    template = str(kept.get('template') or '').strip('\n')
    if template:
        head = [DIVIDER, 'KARAOKE BUILDER STUDIO', 'www.KaraokeBuilder.com', ''] + template.split('\n') + ['']
    else:
        head = TEMPLATE.split('\n')
    track = dict(kept.get('track') or {})
    track['Status'] = '1'
    for key, field in (('title', 'Title'), ('artist', 'Artist')):
        value = ' '.join(str(metadata.get(key) or '').split())
        if value:
            track[field] = value
    if audio and not track.get('Audio'):
        track['Audio'] = audio
    out = head + ["'--- Track Information ---", '']
    for field in list(TRACK_FIELDS) + [field for field in track if field not in TRACK_FIELDS]:
        value = str(track.get(field) or '')
        if field == 'Comments':
            out.append('')
        out.append(f'{field:<10}' + ('\n' + ' ' * 10).join(value.split('\n')))
    out.append('')
    for page in _page_lines(segments):
        out += [DIVIDER, 'PAGEV2']
        for start, end, syllables in page:
            out.append(f'C/A/{_cs(start)}/{_cs(end)}/0/0/0')
            for number, (text, begin, finish) in enumerate(syllables):
                following = syllables[number + 1][1] if number + 1 < len(syllables) else None
                finish_cs = _cs(finish)
                if following is not None and _cs(following) == finish_cs:
                    finish_cs -= 1                 # KBS's hundredth between syllables
                piece = text.lstrip() if number == 0 else text
                out.append(f"{piece.replace('/', '{-}') + '/':<15}{_cs(begin)}/{max(_cs(begin), finish_cs)}/0")
            out.append('')
    out += [DIVIDER, '']
    return '\n'.join(out) + '\n'


def save(path, segments, metadata=None, audio=''):
    """Write `segments` to `path` as a KBP project."""
    with open(path, 'w', encoding='utf-8', newline='\r\n') as kbp_file:
        kbp_file.write(write(segments, metadata, audio))
