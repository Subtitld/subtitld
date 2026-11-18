"""TSB4 (.sub): the binary subtitle files of SubtitleNext (formerly
Titlebee), read only.

The format is not published. What is here follows Subtitle Edit's reader
(libse, TSB4.cs, GPL-3 as Subtitld is), which was worked out from files.
Subtitle Edit does not write it, and nothing says what the chunks this
reader skips would have to hold, so neither does Subtitld.

A file begins "TSB4" and is a run of chunks, each a four-letter name and
its size:

    CPAG    size 4: the Windows code page of the text (two bytes, low
            first; 1252 when there is none)
    TITL    a subtitle: its size, then straight after it
    TIME    its first frame and the frame after its last, at 16 and 20 from
            TITL (two bytes, low first, and a third, the high one)
    LINE    within TITL: the text's length at 4 and the text at 8

Sizes and frame numbers are little-endian; some files pad what would be a
zero high byte with a space (20) instead, and a reader takes a 20 there
for zero until a TITL whose size's second byte is not one, as Subtitle
Edit does. In the text, 7F turns italics on and off (to the end of the
line at most) and 00 is a space.

The frames count from the start of the video at a rate the file does not
say: the video's, else 25.
"""

from subtitld.modules import markup

DEFAULT_FPS = 25.0
_MAGIC = b'TSB4'
_ITALIC = '\x7f'


def is_tsb4(data):
    """True when `data` begins as a TSB4 file."""
    return len(data) >= 100 and data[:4] == _MAGIC


def looks_like(head):
    """True when `head`, the first bytes of a .sub, are TSB4's."""
    return bytes(head[:4]) == _MAGIC


def _runs(text):
    """[(text, Style)] for a subtitle's text: 7F toggles italics, a line
    ends them."""
    runs, italic = [], False
    for char in text.replace('\r\n', '\n').replace('\r', '\n'):
        if char == _ITALIC:
            italic = not italic
        elif char == '\n':
            runs.append(('\n', markup.PLAIN))
            italic = False
        else:
            runs.append((' ' if char == '\x00' else char, markup.Style(italic=italic)))
    return runs


def _codec(code_page):
    try:
        name = f'cp{int(code_page)}'
        ''.encode(name)
        return name
    except (LookupError, ValueError):
        return 'cp1252'


def parse(data, fps=DEFAULT_FPS):
    """(subtitles, {'fps', 'code_page'}) in TSB4 bytes, frames counted at
    `fps`. Raises ValueError for bytes that are not TSB4."""
    fps = float(fps or DEFAULT_FPS)
    if not is_tsb4(data):
        raise ValueError('Not a TSB4 file: it does not begin as one does')
    code_page, spaces = 0, True
    segments = []
    at = 0
    while at < len(data) - 20:
        if data[at:at + 8] == b'CPAG\x04\x00\x00\x00':
            code_page = data[at + 8] | data[at + 9] << 8
            at += 12
        if at + 24 <= len(data) and data[at:at + 4] == b'TITL' and data[at + 8:at + 12] == b'TIME':
            if data[at + 5] != 0x20:
                spaces = False
            size = data[at + 4] + (0 if spaces else data[at + 5] << 8)
            first = data[at + 16] | data[at + 17] << 8
            if data[at + 18] != 0x20 or not spaces:
                first += data[at + 18] << 16
            last = data[at + 20] | data[at + 21] << 8
            if data[at + 22] != 0x20 or not spaces:
                last += data[at + 22] << 16
            line = data.find(b'LINE', at, at + size + 8)
            text = ''
            if 0 <= line and line + 8 <= len(data):
                length = data[line + 4] + (0 if data[line + 5] == 0x20 and spaces else data[line + 5] << 8)
                text = data[line + 8:line + 8 + length].decode(_codec(code_page or 1252), 'replace')
            body = markup.tidy(_runs(text))
            if markup.plain(body).strip():
                start = round(first / fps, 3)
                segments.append({'start': start, 'end': max(start, round(last / fps, 3)), 'text': body})
            at += size + 5
        at += 1
    return segments, {'fps': fps, 'code_page': code_page or 1252}


def read(path, fps=DEFAULT_FPS):
    """(subtitles, its settings) in the TSB4 file at `path`, as parse()
    gives them."""
    with open(path, 'rb') as sub_file:
        return parse(sub_file.read(), fps)
