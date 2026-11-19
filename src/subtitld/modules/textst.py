"""Blu-ray text subtitles (TextST): the words of a Blu-ray's text subtitle
stream, read.

Besides its pictures (PGS, modules.pgs), a Blu-ray may carry subtitles as
text, drawn by the player in a font the disc gives: a TextST stream, in a
transport stream (.m2ts) of its own, stream type 0x92, which FFmpeg lists
as hdmv_text_subtitle but has no decoder for. The BD-ROM specification
that defines it is not public; libbluray's decoder (src/libbluray/decoders/
textst_decode.c, textst.h and graphics_processor.c) is the source this
module follows. A stream is segments, each a type (0x81 dialog style, 0x82
dialog presentation), a 16-bit length and its body:

- the dialog style segment, once, first: the player style flag, the region
  styles (each a region's place and size, its background, a text box in
  it, the flow, the alignments, the line spacing, the font, its style
  (bold 1, italic 2, outline 4), size and colours), the user styles, a
  palette (entries of id, Y, Cr, Cb, T) and then how many dialogs follow;
- a dialog presentation segment per subtitle: its start and end PTS (33
  bits, 90 kHz), a palette update when flagged, and one or two regions,
  each forced or not, its region style, and its text as escaped elements
  (0x1B, a type, a length): a string (1), a font (2), a font style with an
  outline (3), a size (4), a colour (5), a line break (0x0A) and a return
  to the region's style (0x0B).

`decode` gives subtitles: the strings with their line breaks, bold and
italic as markup where the region's style or an inline style makes them
so, the two regions of a dialog one under the other, a region placed in
the upper part of the frame starting {\\an8} (on a 1920x1080 plane: the
stream does not say the video's size), forced where a region is. The
strings are UTF-8, which the discs use; the character set of a stream is
named in the disc's clip information, not in the stream, so a stream in
another (Shift-JIS, Big5...) is read as UTF-8 would take it, with what
does not decode as such read as Latin-1.

A video's TextST track is read packet by packet through ffprobe
(video_subtitles.extract), each packet a segment. Writing one is not done:
there is no public source for a disc's clip information that names it, and
nothing to check a written stream against.
"""

PLANE = (1920, 1080)
DIALOG_STYLE = 0x81
DIALOG_PRESENTATION = 0x82
_STRING, _FONT_ID, _FONT_STYLE, _FONT_SIZE, _FONT_COLOR = 1, 2, 3, 4, 5
_NEWLINE, _RESET_STYLE = 0x0A, 0x0B


class TextSTError(ValueError):
    """Not a TextST stream Subtitld can read."""


class _Reader:
    def __init__(self, data):
        self.data, self.position = bytes(data), 0

    def take(self, count):
        if self.position + count > len(self.data):
            raise TextSTError('A TextST segment ends early')
        chunk = self.data[self.position:self.position + count]
        self.position += count
        return chunk

    def byte(self):
        return self.take(1)[0]

    def word(self):
        return int.from_bytes(self.take(2), 'big')

    def signed(self):
        value = self.word()
        return -(value & 0x7FFF) if value & 0x8000 else value

    def pts(self):
        return int.from_bytes(self.take(5), 'big') & 0x1FFFFFFFF


def segments(data):
    """[(type, body)] of the segments in `data`, back to back."""
    found = []
    position = 0
    while position + 3 <= len(data):
        kind = data[position]
        length = int.from_bytes(data[position + 1:position + 3], 'big')
        found.append((kind, bytes(data[position + 3:position + 3 + length])))
        position += 3 + length
    return found


def _palette(reader):
    entries = reader.word() // 5
    for _ in range(entries):
        reader.take(5)


def _style(value):
    return {'bold': bool(value & 1), 'italic': bool(value & 2)}


def parse_style(body):
    """{region style id: {'y', 'height', 'bold', 'italic'}} of a dialog
    style segment, and how many dialogs it says follow."""
    reader = _Reader(body)
    reader.take(2)                                     # player style flag, reserved
    regions, users = reader.byte(), reader.byte()
    styles = {}
    for _ in range(regions):
        identifier = reader.byte()
        _x, y, _width, height = (reader.word() for _ in range(4))
        reader.take(2)                                 # background colour, reserved
        reader.take(8)                                 # the text box
        reader.take(5)                                 # flow, alignments, line space, font
        style = _style(reader.byte())
        reader.take(4)                                 # size, colours, outline thickness
        styles[identifier] = dict(style, y=y, height=height)
    reader.take(15 * users)
    _palette(reader)
    count = reader.word() if reader.position + 2 <= len(reader.data) else 0
    return styles, count


def _text(raw):
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('latin-1')


def parse_dialog(body):
    """{'start', 'end' (PTS), 'regions': [{'forced', 'style', 'runs':
    [(text, bold, italic) or '\\n'], 'reset': [...]}]} of a dialog
    presentation segment; a run's bold and italic None where the region's
    style decides."""
    reader = _Reader(body)
    start, end = reader.pts(), reader.pts()
    if reader.byte() & 0x80:
        _palette(reader)
    regions = []
    for _ in range(min(2, reader.byte())):
        flags = reader.byte()
        style = reader.byte()
        length = reader.word()
        data = _Reader(reader.take(length))
        runs = []
        bold = italic = None
        while data.position < len(data.data):
            if data.byte() != 0x1B:
                continue                                # libbluray: a missing escape, skipped
            if data.position + 2 > len(data.data):
                break
            kind, size = data.byte(), data.byte()
            content = data.take(min(size, len(data.data) - data.position))
            if kind == _STRING:
                runs.append((_text(content), bold, italic))
            elif kind == _FONT_STYLE and content:
                inline = _style(content[0])
                bold, italic = inline['bold'], inline['italic']
            elif kind == _NEWLINE:
                runs.append('\n')
            elif kind == _RESET_STYLE:
                bold = italic = None
        regions.append({'forced': bool(flags & 0x40), 'continuous': bool(flags & 0x80), 'style': style, 'runs': runs})
    return {'start': start, 'end': end, 'regions': regions}


def _markup(runs, region_style):
    text = ''
    for run in runs:
        if run == '\n':
            text += '\n'
            continue
        words, bold, italic = run
        bold = region_style.get('bold', False) if bold is None else bold
        italic = region_style.get('italic', False) if italic is None else italic
        if italic:
            words = f'<i>{words}</i>'
        if bold:
            words = f'<b>{words}</b>'
        text += words
    return text.strip('\n')


def decode(packets, origin=0):
    """Subtitles [{'start', 'end', 'text'[, 'forced']}] of a TextST stream
    given as its packets (bytes each, one or more segments in each), their
    times less `origin` (90 kHz: the file's start)."""
    styles = {}
    found = []
    for packet in packets:
        for kind, body in segments(packet):
            try:
                if kind == DIALOG_STYLE:
                    styles = parse_style(body)[0]
                elif kind == DIALOG_PRESENTATION:
                    dialog = parse_dialog(body)
                else:
                    continue
            except TextSTError:
                continue
            if kind != DIALOG_PRESENTATION:
                continue
            texts, top, forced = [], False, False
            for region in dialog['regions']:
                style = styles.get(region['style'], {})
                text = _markup(region['runs'], style)
                if not text.strip():
                    continue
                texts.append(text)
                forced = forced or region['forced']
                if not top and 'y' in style:
                    top = style['y'] + style['height'] / 2 < PLANE[1] * 0.4
            if not texts:
                continue
            start = ((dialog['start'] - origin) % (1 << 33)) / 90000
            end = ((dialog['end'] - origin) % (1 << 33)) / 90000
            subtitle = {'start': round(start, 3), 'end': round(max(start, end), 3),
                        'text': ('{\\an8}' if top else '') + '\n'.join(texts)}
            if forced:
                subtitle['forced'] = True
            found.append(subtitle)
    found.sort(key=lambda subtitle: (subtitle['start'], subtitle['end']))
    return found


# Writing a stream, for the tests: what libbluray reads, laid out by hand.
def style_segment(regions, dialogs):
    """A dialog style segment: `regions` [(id, x, y, width, height, font
    style)], one user style none, an empty palette, `dialogs` following."""
    body = bytearray([0x80, 0x00, len(regions), 0])
    for identifier, x, y, width, height, font_style in regions:
        body += bytes([identifier]) + b''.join(v.to_bytes(2, 'big') for v in (x, y, width, height)) + b'\x00\x00'
        body += b''.join(v.to_bytes(2, 'big') for v in (0, 0, width, height))
        body += bytes([1, 2, 3, 0, 0, font_style, 32, 1, 0, 1])
    body += (0).to_bytes(2, 'big') + int(dialogs).to_bytes(2, 'big')
    return bytes([DIALOG_STYLE]) + len(body).to_bytes(2, 'big') + bytes(body)


def dialog_segment(start, end, regions):
    """A dialog presentation segment from `start` to `end` (seconds), of
    `regions` [(style id, forced, [elements])], each element a str (a
    string), '\\n', ('style', bold, italic) or 'reset'."""
    def stamp(seconds):
        return (round(seconds * 90000) & 0x1FFFFFFFF).to_bytes(5, 'big')

    body = bytearray(stamp(start) + stamp(end) + b'\x00' + bytes([len(regions)]))
    for style, forced, elements in regions:
        data = bytearray()
        for element in elements:
            if element == '\n':
                data += bytes([0x1B, _NEWLINE, 0])
            elif element == 'reset':
                data += bytes([0x1B, _RESET_STYLE, 0])
            elif isinstance(element, tuple):
                data += bytes([0x1B, _FONT_STYLE, 3, (1 if element[1] else 0) | (2 if element[2] else 0), 0, 0])
            else:
                raw = element.encode('utf-8')
                data += bytes([0x1B, _STRING, len(raw)]) + raw
        body += bytes([0x40 if forced else 0, style]) + len(data).to_bytes(2, 'big') + data
    return bytes([DIALOG_PRESENTATION]) + len(body).to_bytes(2, 'big') + bytes(body)
