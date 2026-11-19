"""Ayato (.aya): the binary subtitle files of the Japanese subtitling tool
Ayato, read and written.

Its maker never published the format. What is here follows Subtitle
Edit's reader and writer (libse, Ayato.cs, GPL-3 as Subtitld is), which
were worked out from files:

    header  2713 bytes: "\\x05" "02.03" "\\x05" "Ayato" 02 at the start, the
            number of subtitles (two bytes, low first) at 682 and again at
            2050, the font name ("Microsoft Sans Serif") at 2088, and other
            bytes whose meaning is not known, written as Subtitle Edit
            writes them.
    record  its number (two bytes, low first, from 1), 00 00, the first
            frame (three bytes, low first), 00, the frame after the last
            (the same), 00, 17 00 02 00 (not known), the length of what
            follows the padding (one byte), 55 zeros, then 07 and the text
            in UTF-8, its lines parted by 1F 07.

The frames count from the start of the video at a rate the file does not
say: the video's, else 25. A record's length is one byte, so a subtitle's
text is cut at 254 bytes. A reader takes any run of up to three control
bytes followed by 07 for a line break, as Subtitle Edit does; the first
record must be number 1, or the file is not taken for Ayato.

Formatting, places and colours are not in the format and are left out.
"""

from subtitld.modules import markup

DEFAULT_FPS = 25.0
HEADER_SIZE = 2713
_RECORD_TEXT = 72
_LINE_BREAK = b'\x1f\x07'

# The header as Subtitle Edit writes it: offset, bytes; zeros elsewhere.
_HEADER_BYTES = (
    (0, b'\x0502.03\x05Ayato\x02'), (77, b'\x01'), (81, b'\x01'), (608, b'\x3b\x32'),
    (613, b'\x58\x02\xe8\x03\xe8\x03\xe8\x03\xe8\x03'), (639, b'\x02'), (686, b'\x09\x04'), (751, b'\x08'),
    (760, b'\x01\x0a'), (830, b'\x58\x48'), (2048, b'\x99\x02'), (2069, b'\x17'), (2071, b'\x17'),
    (2073, b'\x02'), (2075, b'\x27'), (2077, b'\x0c'), (2079, b'\x04'), (2082, b'\x01'), (2085, b'\x01'),
    (2088, b'Microsoft Sans Serif'), (2120, b'\x1f'), (2123, b'\x01\x02'), (2128, b'\xff'), (2136, b'\x02'),
    (2176, b'\x01'), (2193, b'\x02\x02'), (2197, b'\x0c\x14\x0c\x01\xe8\x03\xe8\x03\xe8\x03\xe8\x03'),
    (2225, b'\x13\x08\xdf\x07\x13\x08\xdf\x07'),
)


def is_ayato(data):
    """True when `data` is long enough for an Ayato file and its first
    record, after the header, is number 1."""
    return len(data) >= HEADER_SIZE + 73 and data[HEADER_SIZE] == 1


def _text(data):
    """A record's text bytes (after the length) as text with "\\n" between
    lines."""
    offset = 0
    for lead in range(3):
        if lead < len(data) and data[lead] == 7:
            offset = lead + 1
            break
    if offset < len(data) and data[offset] < 32:
        offset += 1
    lines, line, at = [], bytearray(), offset
    while at < len(data):
        # Up to three control bytes and a 07 part two lines.
        run = 0
        while run < 3 and at + run < len(data) and data[at + run] < 32 and data[at + run] != 7:
            run += 1
        if at + run < len(data) and data[at + run] == 7:
            lines.append(line.decode('utf-8', 'replace'))
            line = bytearray()
            at += run + 1
            if at < len(data) and data[at] < 32:
                at += 1
            continue
        line.append(data[at])
        at += 1
    lines.append(line.decode('utf-8', 'replace'))
    return '\n'.join(part.strip() for part in lines if part.strip())


def parse(data, fps=DEFAULT_FPS):
    """(subtitles, {'fps'}) in Ayato bytes, frames counted at `fps`. Raises
    ValueError for bytes that are not Ayato."""
    fps = float(fps or DEFAULT_FPS)
    if not is_ayato(data):
        raise ValueError('Not an Ayato file: it does not begin as one does')
    segments, at = [], HEADER_SIZE
    while at + _RECORD_TEXT < len(data):
        length = data[at + 16]
        if length and at + _RECORD_TEXT + length <= len(data):
            text = _text(data[at + _RECORD_TEXT:at + _RECORD_TEXT + length])
            if text.strip():
                first = int.from_bytes(data[at + 4:at + 7], 'little')
                last = int.from_bytes(data[at + 8:at + 11], 'little')
                start = round(first / fps, 3)
                segments.append({'start': start, 'end': max(start, round(last / fps, 3)), 'text': text})
        at += _RECORD_TEXT + length
    return segments, {'fps': fps}


def read(path, fps=DEFAULT_FPS):
    """(subtitles, its settings) in the Ayato file at `path`, as parse()
    gives them."""
    with open(path, 'rb') as aya_file:
        return parse(aya_file.read(), fps)


def _header(count):
    header = bytearray(HEADER_SIZE)
    for offset, value in _HEADER_BYTES:
        header[offset:offset + len(value)] = value
    for offset in (682, 2050):
        header[offset:offset + 2] = (count & 0xFFFF).to_bytes(2, 'little')
    return bytes(header)


def write(segments, fps=DEFAULT_FPS):
    """Ayato bytes for `segments`, frames counted at `fps`."""
    fps = float(fps or DEFAULT_FPS)
    records = []
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        _lead, rest = markup.split_lead(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
        lines = [line.strip() for line in markup.plain(rest).split('\n') if line.strip()]
        if not lines:
            continue
        text = _LINE_BREAK.join(line.encode('utf-8') for line in lines)
        if len(text) > 254:
            text = text[:254].decode('utf-8', 'ignore').encode('utf-8')
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', 0)))
        number = len(records) + 1
        records.append((number & 0xFFFF).to_bytes(2, 'little') + b'\x00\x00'
                       + min(int(round(start * fps)), 0xFFFFFF).to_bytes(3, 'little') + b'\x00'
                       + min(int(round(end * fps)), 0xFFFFFF).to_bytes(3, 'little') + b'\x00'
                       + b'\x17\x00\x02\x00' + bytes([len(text) + 1]) + bytes(55) + b'\x07' + text)
    return _header(len(records)) + b''.join(records)


def save(path, segments, **options):
    """Write `segments` to `path` as Ayato; `options` as write() takes
    them."""
    with open(path, 'wb') as aya_file:
        aya_file.write(write(segments, **options))
