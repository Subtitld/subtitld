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
(video_subtitles.extract), each packet a segment.

`save(path, segments, size)` writes one, as a disc holds it: a Blu-ray
transport stream (.m2ts, 192-byte packets) of the TextST stream alone, PID
0x1800 (libbluray's hdmv_pids.h), each segment a private_stream_2 PES of
its own (libbluray's m2ts_demux.c reads such a PES with no header; its
graphics processor a segment from each), the dialog style first and then
the dialogs in order, all of them ahead of playback, as a player loads the
whole stream before it starts (libbluray preloads it). The style segment
has a region style for each of the nine places a subtitle can ask for
({\\anN}: top, middle and bottom, left, centre and right; its id the
keypad number), on a plane the video's size (1920x1080 without one): a
region across 90 % of the width and four lines high, 5 % in from the
edge, its text aligned in it as the place says; the font the disc's first
(id 0), a twentieth of the height, white outlined black, on nothing.
Overlapping subtitles become a dialog for each stretch where the same ones
show, those in one place a region (two at most, as TextST holds). Bold,
italic and colour are inline font styles and colours, each run's set back
after it (0x0B); colours go in the palette (Y, Cr, Cb, T; BT.709 for HD,
as PGS's, modules.pgs). FFmpeg lists such a file's stream as
hdmv_text_subtitle and hands over its segments, which `decode` reads back.

What only the BD-ROM specification says is not written: a disc's clip
information (.clpi) names the stream's character set (UTF-8 here) and its
font files, so a stream needs one beside it in a disc's BDMV folder to
play (made by a disc authoring tool; tsMuxeR renders text to PGS and takes
no TextST in, and FFmpeg has no TextST encoder or bare TextST demuxer, so
the .m2ts is what both read); the limits it sets on regions, styles and
dialogs are not known, nor whether a player draws the outline (libbluray
does not). Continuous presentation and user styles are not written.
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


def _palette(reader, palette=None):
    """Read a palette: {entry: (r, g, b, a)} into `palette`, when given."""
    entries = reader.word() // 5
    for _ in range(entries):
        entry, y, cr, cb, alpha = reader.take(5)
        if palette is not None:
            from subtitld.modules import pgs
            palette[entry] = pgs._rgba(y, cr, cb, alpha, True)


def _style(value):
    return {'bold': bool(value & 1), 'italic': bool(value & 2)}


def parse_style(body, palette=None):
    """{region style id: {'y', 'height', 'bold', 'italic'}} of a dialog
    style segment, and how many dialogs it says follow; its palette into
    `palette` ({entry: (r, g, b, a)}), when given."""
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
    _palette(reader, palette)
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
    style decides, and a fourth item, the palette entry of its colour,
    where an inline colour gives it one."""
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
        bold = italic = colour = None
        while data.position < len(data.data):
            if data.byte() != 0x1B:
                continue                                # libbluray: a missing escape, skipped
            if data.position + 2 > len(data.data):
                break
            kind, size = data.byte(), data.byte()
            content = data.take(min(size, len(data.data) - data.position))
            if kind == _STRING:
                runs.append((_text(content), bold, italic) + (() if colour is None else (colour,)))
            elif kind == _FONT_STYLE and content:
                inline = _style(content[0])
                bold, italic = inline['bold'], inline['italic']
            elif kind == _FONT_COLOR and content:
                colour = content[0]
            elif kind == _NEWLINE:
                runs.append('\n')
            elif kind == _RESET_STYLE:
                bold = italic = colour = None
        regions.append({'forced': bool(flags & 0x40), 'continuous': bool(flags & 0x80), 'style': style, 'runs': runs})
    return {'start': start, 'end': end, 'regions': regions}


def _markup(runs, region_style, palette=None):
    text = ''
    for run in runs:
        if run == '\n':
            text += '\n'
            continue
        words, bold, italic = run[:3]
        bold = region_style.get('bold', False) if bold is None else bold
        italic = region_style.get('italic', False) if italic is None else italic
        colour = (palette or {}).get(run[3]) if len(run) > 3 else None
        if colour:
            words = f'<font color="#{colour[0]:02x}{colour[1]:02x}{colour[2]:02x}">{words}</font>'
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
    styles, palette = {}, {}
    found = []
    for packet in packets:
        for kind, body in segments(packet):
            try:
                if kind == DIALOG_STYLE:
                    palette = {}
                    styles = parse_style(body, palette)[0]
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
                text = _markup(region['runs'], style, palette)
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


# Writing --------------------------------------------------------------

PID = 0x1800                    # the TextST stream's, on a disc (libbluray's hdmv_pids.h)
PMT_PID = 0x0100
STREAM_TYPE = 0x92
TRANSPARENT, WHITE, BLACK = 0, 1, 2                 # the palette's first three entries
OUTLINE = 4                     # the font style's outline flag
_OUTLINE_MEDIUM = 2             # libbluray's textst.h: thin 1, medium 2, thick 3
# libbluray's textst.h: a keypad column left 1, centre 2, right 3; a row
# (by its first key) top 1, middle 2, bottom 3.
_HALIGN = {1: 1, 2: 2, 0: 3}
_VALIGN = {7: 1, 4: 2, 1: 3}


def _segment(kind, body):
    return bytes([kind]) + len(body).to_bytes(2, 'big') + bytes(body)


def font_size(size=PLANE):
    """The font's size on a plane of `size`: a twentieth of its height."""
    return max(8, min(255, round(size[1] / 20)))


def line_space(font):
    return min(255, round(font * 1.3))


def regions_for(size=PLANE):
    """{keypad: (x, y, width, height)}: a region for each of the nine places,
    across 90 % of the width, four lines high, 5 % in from the edges."""
    width, height = size
    region_height = 4 * line_space(font_size(size))
    margin = round(height * 0.05)
    x, region_width = round(width * 0.05), round(width * 0.9)
    tops = {7: margin, 4: (height - region_height) // 2, 1: height - margin - region_height}
    return {row + column: (x, tops[row], region_width, region_height) for row in (7, 4, 1) for column in (0, 1, 2)}


def dialog_style(size, colours, dialogs):
    """The dialog style segment: a region style per keypad place (its id the
    keypad number), no user styles, the palette (transparent, white, black
    and `colours` [(r, g, b)] from entry 3 on) and `dialogs` following."""
    from subtitld.modules import pgs
    hd = size[1] >= 720
    font = font_size(size)
    places = regions_for(size)
    body = bytearray([0x00, 0x00, len(places), 0])     # player style off, reserved; no user styles
    for keypad, (x, y, width, height) in sorted(places.items()):
        row = 7 if keypad >= 7 else 4 if keypad >= 4 else 1
        body += bytes([keypad]) + b''.join(v.to_bytes(2, 'big') for v in (x, y, width, height))
        body += bytes([TRANSPARENT, 0])                 # background, reserved
        body += b''.join(v.to_bytes(2, 'big') for v in (0, 0, width, height))      # the text box: all of it
        # Flow left to right, the alignments, the line space, font 0, its
        # style (outlined), size, colour, outline colour and thickness.
        body += bytes([1, _HALIGN[keypad % 3], _VALIGN[row], line_space(font), 0,
                       OUTLINE, font, WHITE, BLACK, _OUTLINE_MEDIUM])
    entries = [(TRANSPARENT, (16, 128, 128, 0)), (WHITE, pgs._ycbcr(255, 255, 255, 255, hd)),
               (BLACK, pgs._ycbcr(0, 0, 0, 255, hd))]
    entries += [(3 + number, pgs._ycbcr(r, g, b, 255, hd)) for number, (r, g, b) in enumerate(colours)]
    body += (5 * len(entries)).to_bytes(2, 'big') + b''.join(bytes([entry]) + bytes(value) for entry, value in entries)
    body += int(dialogs).to_bytes(2, 'big')
    return _segment(DIALOG_STYLE, body)


def _string_elements(words):
    """A string as escaped string elements, 255 bytes at most each, cut
    between characters."""
    out = bytearray()
    raw = words.encode('utf-8')
    while raw:
        cut = min(255, len(raw))
        while cut < len(raw) and raw[cut] & 0xC0 == 0x80:
            cut -= 1
        out += bytes([0x1B, _STRING, cut]) + raw[:cut]
        raw = raw[cut:]
    return bytes(out)


def region_data(text, colours):
    """A region's text (Subtitld's markup, without its place) as escaped
    elements: its strings and line breaks, a run in bold, italic or a
    colour styled inline and set back after it. `colours` is the list of
    the palette's colours from entry 3 on, added to as new ones come."""
    from subtitld.modules import markup
    out = bytearray()
    for number, line in enumerate(text.split('\n')):
        if number:
            out += bytes([0x1B, _NEWLINE, 0])
        for words, style in markup.spans(line):
            if not words:
                continue
            colour = markup.color_hex(style.color)
            entry = None
            if colour and colour.lower() != '#ffffff':
                rgb = tuple(int(colour[k:k + 2], 16) for k in (1, 3, 5))
                if rgb not in colours:
                    colours.append(rgb)
                entry = 3 + colours.index(rgb)
            if style.bold or style.italic:
                out += bytes([0x1B, _FONT_STYLE, 3, OUTLINE | (1 if style.bold else 0) | (2 if style.italic else 0),
                              BLACK, _OUTLINE_MEDIUM])
            if entry is not None:
                out += bytes([0x1B, _FONT_COLOR, 1, entry])
            out += _string_elements(words)
            if style.bold or style.italic or entry is not None:
                out += bytes([0x1B, _RESET_STYLE, 0])
    return bytes(out)


def dialog_presentation(start, end, regions):
    """A dialog presentation segment from `start` to `end` (90 kHz), of
    `regions` [(region style id, forced, escaped elements)], two at most."""
    body = bytearray((start & 0x1FFFFFFFF).to_bytes(5, 'big') + (end & 0x1FFFFFFFF).to_bytes(5, 'big'))
    body += bytes([0, len(regions)])                    # no palette update
    for style, forced, data in regions:
        body += bytes([0x40 if forced else 0, style]) + len(data).to_bytes(2, 'big') + data
    return _segment(DIALOG_PRESENTATION, body)


def stream(segments, size=PLANE):
    """The segments of a TextST stream of `segments` ([{'start', 'end',
    'text'[, 'forced']}]): the dialog style, then a dialog for each stretch
    of time the same subtitles show."""
    from subtitld.modules import markup
    cues = []
    for segment in segments:
        start, end = round(float(segment.get('start') or 0) * 90000), round(float(segment.get('end') or 0) * 90000)
        lead, words = markup.split_lead(str(segment.get('text') or ''))
        words = words.strip('\n')
        if end > start and markup.plain(words).strip():
            cues.append((start, end, markup.alignment(lead), words, bool(segment.get('forced'))))
    times = sorted({cue[0] for cue in cues} | {cue[1] for cue in cues})
    colours = []
    dialogs = []
    for begin, finish in zip(times, times[1:]):
        shown = [cue for cue in cues if cue[0] <= begin and cue[1] >= finish]
        if not shown:
            continue
        places = {}
        for cue in shown:
            places.setdefault(cue[2], []).append(cue)
        # Top to bottom, left to right; past two places, the rest go in the second.
        order = sorted(places, key=lambda keypad: (-((keypad - 1) // 3), (keypad - 1) % 3))
        groups = [list(places[keypad]) for keypad in order[:2]]
        for keypad in order[2:]:
            groups[-1] += places[keypad]
        regions = [(order[number], any(cue[4] for cue in group),
                    region_data('\n'.join(cue[3] for cue in group), colours)) for number, group in enumerate(groups)]
        dialogs.append(dialog_presentation(begin, finish, regions))
    return [dialog_style(size, colours, len(dialogs))] + dialogs


def _pes(segment):
    """A segment as a private_stream_2 PES packet: no header, its length the segment's."""
    return b'\x00\x00\x01\xbf' + len(segment).to_bytes(2, 'big') + segment


def transport_stream(segments, size=PLANE, language='eng'):
    """A Blu-ray transport stream (192-byte packets, each a 4-byte arrival
    time before it) of a TextST stream of `segments`: the PAT, a PMT with
    the HDMV registration and the stream (type 0x92, its language), and a
    PES for each of the stream's segments."""
    from subtitld.modules import teletext
    counters = {}
    pat = teletext._section(0x00, 1, bytes((0x00, 0x01, 0xE0 | PMT_PID >> 8, PMT_PID & 0xFF)))
    program = bytes((0x05, 4)) + b'HDMV'
    code = (language or 'und')[:3].encode('latin-1', 'replace').ljust(3, b' ')
    stream_info = bytes((0x0A, 4)) + code + b'\x00'
    body = bytes((0xFF, 0xFF, 0xF0 | len(program) >> 8, len(program) & 0xFF)) + program     # no PCR
    body += bytes((STREAM_TYPE, 0xE0 | PID >> 8, PID & 0xFF, 0xF0, len(stream_info))) + stream_info
    pmt = teletext._section(0x02, 1, body)
    packets = teletext._ts_packets(0, b'\x00' + pat, counters) + teletext._ts_packets(PMT_PID, b'\x00' + pmt, counters)
    for segment in stream(segments, size):
        packets += teletext._ts_packets(PID, _pes(segment), counters)
    # Arrival times at a nominal 48 Mbit/s: 192 bytes each 829 ticks of 27 MHz.
    return b''.join(((number * 829) & 0x3FFFFFFF).to_bytes(4, 'big') + packet for number, packet in enumerate(packets))


def save(path, segments, size=None, language='eng'):
    """Write `segments` at `path` as a Blu-ray TextST stream in its .m2ts,
    on a plane of `size` (1920x1080 when none). `language` is the ISO 639-2
    code its PMT names."""
    size = (int(size[0]), int(size[1])) if size and size[0] and size[1] else PLANE
    with open(path, 'wb') as handle:
        handle.write(transport_stream(segments, size, language))
    return path


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
