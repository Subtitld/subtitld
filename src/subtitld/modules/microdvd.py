"""MicroDVD (.sub): read the files of the DivX years and the players that
still show them, write what MPlayer, VLC and Subtitle Edit read.

A subtitle is a line, "{start}{end}text", its times in frames of the
video it was made for, "|" between its lines, and codes in braces for its
style: {y:i} italic, {y:b} bold, {y:u} underline, {y:s} struck,
{c:$BBGGRR} a colour; lower case for the rest of the line, upper case
({Y:i}) for the whole subtitle. A first subtitle "{1}{1}23.976" says the
frame rate.

Reading forgives:

- the bytes are decoded as SRT's are (files from the DivX years are often
  in their language's Windows code page, Windows-1250 in Central Europe);
- the frame rate a "{1}{1}25" or "{0}{0}25" line declares, else the
  video's, else 25; the declaring line is no subtitle;
- an empty end ("{100}{}") lasts until the next subtitle;
- a line that begins "/" is italic, as MPL2 writes it;
- italic, bold, underline, struck and colour become formatting tags,
  lower-case codes to the end of their line, upper-case ones to the end of
  the subtitle; fonts, sizes, positions and charsets go;
- the subtitles come sorted by start.

Writing gives "{1}{1}" and the frame rate first, then a line a subtitle,
in order, times in frames at that rate, lines joined by "|". A style the
whole subtitle has is an upper-case code at its start, one a whole line
has a lower-case code at the line's. Players read codes only there, so
formatting on part of a line is written plain; and there is no place on
the screen to give.

A .sub can also be SubViewer 2 (text, read as SBV) or VobSub (pictures of
subtitles, an MPEG stream): kind() tells them apart.
"""

import re

from subtitld.modules import markup
from subtitld.modules.srt import decode

LAST_DURATION = 4.0                      # seconds for a last subtitle with no end
_LINE = re.compile(r'^\s*\{(\d+)\}\{(\d*)\}(.*)$')
_CODE = re.compile(r'\{([a-zA-Z]):([^{}]*)\}')
_SUBVIEWER = re.compile(r'^\s*\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}\s*,\s*\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}\s*$', re.M)
_ANY_LINE = re.compile(r'^\s*\{\d+\}\{\d*\}', re.M)
_FLAGS = {'i': 'italic', 'b': 'bold', 'u': 'underline', 's': 'strike'}


def kind(data):
    """What a .sub file holds: 'vobsub' (pictures, an MPEG program stream),
    'subviewer' (SubViewer 2's text) or 'microdvd'."""
    if data[:4] == b'\x00\x00\x01\xba':
        return 'vobsub'
    head = decode(data[:4096])
    if '[INFORMATION]' in head.upper() or (_SUBVIEWER.search(head) and not _ANY_LINE.search(head)):
        return 'subviewer'
    return 'microdvd'


def _rate(text):
    """The frame rate a subtitle's text declares, or None."""
    try:
        rate = float(text.strip().replace(',', '.'))
    except ValueError:
        return None
    return rate if 1 < rate < 200 else None


def _color(bgr):
    digits = bgr.strip().lstrip('$&H').rstrip('&')[-6:].rjust(6, '0')
    if not re.fullmatch(r'[0-9a-fA-F]{6}', digits):
        return ''
    color = '#' + (digits[4:6] + digits[2:4] + digits[0:2]).lower()
    return '' if color == '#ffffff' else color


def _styled(style, letter, value):
    """`style` changed by a code ({y:i}, {c:$0000FF} ...)."""
    letter = letter.lower()
    if letter == 'y':
        for flag in re.findall(r'[ibus]', value.lower()):
            style = style._replace(**{_FLAGS[flag]: True})
    elif letter == 'c':
        style = style._replace(color=_color(value))
    return style


def _text(body):
    """Text with tags for a subtitle's MicroDVD text."""
    whole = markup.PLAIN
    runs = []
    for index, line in enumerate(body.split('|')):
        if index:
            runs.append(('\n', whole))             # in the whole subtitle's style, which goes on
        style = whole
        line = line.strip()
        if line.startswith('/') and not line.startswith('//'):
            style, line = style._replace(italic=True), line[1:].lstrip()
        position = 0
        for code in _CODE.finditer(line):
            runs.append((line[position:code.start()], style))
            position = code.end()
            letter, value = code.group(1), code.group(2)
            style = _styled(style, letter, value)
            if letter.isupper():
                whole = _styled(whole, letter, value)
        runs.append((line[position:], style))
    return markup.tidy(runs)


def parse(text, fps=None):
    """(subtitles, declared frame rate or None) in MicroDVD text, its frames
    counted at the rate it declares, else `fps`, else 25."""
    entries = []
    declared = None
    for line in text.split('\n'):
        match = _LINE.match(line)
        if not match:
            continue
        start, end, body = int(match.group(1)), match.group(2), match.group(3)
        if declared is None and not entries and start <= 1 and (end or '0') in ('0', '1') and _rate(body):
            declared = _rate(body)
            continue
        entries.append((start, int(end) if end else None, body))
    rate = declared or (float(fps) if fps else 25.0)
    entries.sort(key=lambda entry: entry[0])
    segments = []
    for index, (start, end, body) in enumerate(entries):
        if end is None:
            end = entries[index + 1][0] if index + 1 < len(entries) else start + round(LAST_DURATION * rate)
        body_text = _text(body)
        if not markup.plain(body_text).strip():
            continue
        segments.append({'start': round(start / rate, 3), 'end': round(max(start, end) / rate, 3), 'text': body_text})
    return segments, declared


def read(path, fps=None):
    """(subtitles, declared frame rate or None) in the MicroDVD file at
    `path`, as parse() gives them."""
    with open(path, 'rb') as sub_file:
        return parse(decode(sub_file.read()), fps)


def _bgr(color):
    return '$' + color[5:7] + color[3:5] + color[1:3]


def _codes(style, upper):
    letter = 'Y' if upper else 'y'
    codes = [f'{{{letter}:{flag}}}' for flag, field in _FLAGS.items() if getattr(style, field)]
    if style.color:
        codes.append(f'{{{"C" if upper else "c"}:{_bgr(style.color).upper()}}}')
    return ''.join(codes)


def _encode(text):
    """MicroDVD text for a subtitle's text: its lines joined by "|", its
    style as codes where MicroDVD can say it."""
    _lead, rest = markup.split_lead(text)
    lines = [[]]
    for run, style in markup.spans(rest):
        for index, part in enumerate(run.replace('|', '¦').split('\n')):
            if index:
                lines.append([])
            lines[-1].extend((char, style) for char in part)
    lines = [line for line in (_trimmed(line) for line in lines) if line]
    if not lines:
        return ''
    shown = [cell for line in lines for cell in line if cell[0].strip()]
    # What every visible character has is the whole subtitle's.
    whole = markup.Style(*(all(getattr(cell[1], field) for cell in shown) for field in ('italic', 'bold', 'underline', 'strike')),
                         shown[0][1].color if all(cell[1].color == shown[0][1].color for cell in shown) else '')
    out = []
    for line in lines:
        # What every visible character of the line has, beyond the whole
        # subtitle's, is a lower-case code at its start: players read codes
        # there, not within a line.
        visible = [cell for cell in line if cell[0].strip()]
        own = markup.Style(*(all(getattr(cell[1], field) for cell in visible) and not getattr(whole, field)
                             for field in ('italic', 'bold', 'underline', 'strike')),
                           visible[0][1].color if all(cell[1].color == visible[0][1].color for cell in visible)
                           and visible[0][1].color != whole.color else '')
        out.append(_codes(own, upper=False) + ''.join(char for char, _style in line))
    return _codes(whole, upper=True) + '|'.join(out)


def _trimmed(line):
    while line and not line[0][0].strip():
        line = line[1:]
    while line and not line[-1][0].strip():
        line = line[:-1]
    return line


def write(segments, fps):
    """MicroDVD text for `segments`, its times in frames at `fps`."""
    fps = float(fps)
    lines = [f'{{1}}{{1}}{fps:g}']
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        body = _encode(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
        if not body:
            continue
        start = round(float(segment.get('start', 0)) * fps)
        end = max(start, round(float(segment.get('end', 0)) * fps))
        lines.append(f'{{{start}}}{{{end}}}{body}')
    return '\n'.join(lines) + '\n'


def save(path, segments, fps, encoding='utf-8', line_end='\n'):
    """Write `segments` to `path` as MicroDVD: in UTF-8 unless an older
    player needs otherwise (a character the encoding lacks becomes "?"),
    with "\\n" or "\\r\\n" line ends."""
    with open(path, 'w', encoding=encoding, errors='replace', newline=line_end) as sub_file:
        sub_file.write(write(segments, fps))
