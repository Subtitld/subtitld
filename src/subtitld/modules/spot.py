"""Spot (.spt) and its Unicode sibling (.sptx): the binary subtitle files
of the Spot subtitling software, read only.

Neither format is published. What is here follows Subtitle Edit's
readers (libse, Spt.cs and Sptx.cs, GPL-3 as Subtitld is), worked out
from files. Subtitle Edit writes neither (its Spt writer is a stub that
puts no subtitle in the file), so neither does Subtitld.

.spt: the first byte says where the first subtitle starts; then a record
a subtitle, of two lines at most:

    0   the time in, eight ASCII digits HHMMSSFF
    8   the time out, the same
    20  01 when the whole subtitle is italic
    36  the length of the first line, 40 that of the second (a byte each)
    52  the first line's text, then the second's, a byte a character in the
        system's code page (Windows-1252 taken here)

.sptx is the same with the text in UTF-16LE: records are found by their
sixteen ASCII digits, the italic flag at 20, the text's length in bytes at
36 (or at 42 when the first is 0, or over 200 where the second is not) and
the text at 50.

In the text, "<" to ">" is italic. The frames count within the second at a
rate the files do not say: the video's, else 25.
"""

from subtitld.modules import markup

DEFAULT_FPS = 25.0


def is_spt(data):
    """True when `data` begins as a .spt does: a first byte that points past
    the header, then 00."""
    return len(data) > 100 and data[0] > 10 and data[1] == 0


def _seconds(digits, fps):
    """Seconds for an HHMMSSFF time, or None for one that is not."""
    try:
        text = digits.decode('ascii')
        hours, minutes, seconds, frames = int(text[0:2]), int(text[2:4]), int(text[4:6]), int(text[6:8])
    except (UnicodeDecodeError, ValueError):
        return None
    return round(hours * 3600 + minutes * 60 + seconds + min(frames / fps, 0.999), 3)


def _runs(text, italic_all):
    """[(text, Style)] for a subtitle's text: "<" to ">" italic."""
    runs, italic = [], False
    for char in text.replace('\r\n', '\n').replace('\r', '\n'):
        if char == '<':
            italic = True
            runs.append((' ', markup.PLAIN))
        elif char == '>':
            italic = False
            runs.append((' ', markup.PLAIN))
        else:
            runs.append((char, markup.Style(italic=italic or italic_all)))
    return runs


def _segment(start, end, text, italic_all):
    body = markup.tidy(_runs(text, italic_all))
    if start is None or end is None or not markup.plain(body).strip():
        return None
    return {'start': start, 'end': max(start, end), 'text': body}


def parse(data, fps=DEFAULT_FPS):
    """(subtitles, {'fps'}) in .spt bytes, frames counted at `fps`. Raises
    ValueError for bytes that are not Spot's."""
    fps = float(fps or DEFAULT_FPS)
    if not is_spt(data):
        raise ValueError('Not a Spot file: it does not begin as one does')
    segments, at = [], data[0]
    while at + 40 < len(data):
        first, second = data[at + 36], data[at + 40]
        if not first and not second:
            at += 52
            continue
        text = data[at + 52:at + 52 + first].decode('cp1252', 'replace')
        if second:
            text += '\n' + data[at + 52 + first:at + 52 + first + second].decode('cp1252', 'replace')
        found = _segment(_seconds(data[at:at + 8], fps), _seconds(data[at + 8:at + 16], fps), text, data[at + 20] == 1)
        if found:
            segments.append(found)
        at += 52 + first + second
    return segments, {'fps': fps}


def parse_sptx(data, fps=DEFAULT_FPS):
    """(subtitles, {'fps'}) in .sptx bytes, frames counted at `fps`. Raises
    ValueError for bytes with no subtitle in them."""
    fps = float(fps or DEFAULT_FPS)
    segments, at = [], data[0] if data else 0
    while at + 50 <= len(data):
        if not all(0x30 <= byte <= 0x39 for byte in data[at:at + 16]):
            at += 1
            continue
        length = data[at + 36]
        if length == 0 or (length > 200 and data[at + 42] < 200):
            length = data[at + 42]
        if at + 50 + length >= len(data):
            break
        text = data[at + 50:at + 50 + length].decode('utf-16-le', 'replace') if length else ''
        found = _segment(_seconds(data[at:at + 8], fps), _seconds(data[at + 8:at + 16], fps), text, data[at + 20] == 1)
        if found:
            segments.append(found)
        at += 16
    if not segments:
        raise ValueError('Not a Spot file: no subtitle found in it')
    return segments, {'fps': fps}


def read(path, fps=DEFAULT_FPS):
    """(subtitles, its settings) in the .spt or .sptx file at `path`."""
    with open(path, 'rb') as spot_file:
        data = spot_file.read()
    return parse_sptx(data, fps) if path.lower().endswith('.sptx') else parse(data, fps)
