"""Ogg Kate (.ogg): Xiph's codec for subtitles and karaoke in an Ogg file,
on its own or beside a Theora video, as kateenc and ffmpeg2theora write it.

What is here follows libkate's own documentation of the bitstream
(doc/kate-format*.doc, bitstream 0.7) and, where the documentation leaves
a detail out, its encoder and decoder (src/kate_encode.c, kate_decode.c,
kate_fp.c); the Ogg pages follow RFC 3533.

A Kate stream is a run of packets in an Ogg logical stream, the first one
on a page of its own marked as the beginning of the stream. Each packet
starts with its type: 0x80 to 0x88 are headers, in that order, each with
"kate\\0\\0\\0" and a reserved byte after its type; 0x00 is a text event,
0x01 a keepalive, 0x7f the end of the stream (on the page marked as its
end). Fields are written with Ogg's bit packer, least significant bit
first, numbers of 32 and 64 bits little-endian; small numbers in a
variable length form (4 bits up to 14, else 15, a sign, 5 bits of length
and the number), floats as fixed point 16.16 with the bits all of a list
share left out; a "warp" is a field later versions added, its length in
bits first, so an older reader skips it.

- 0x80, ID header: the version, the number of headers, the text's
  encoding (0, UTF-8) and direction, the granule shift, the canvas size,
  the granule rate (a fraction: how many granules a second), the
  language (as kateenc's -l, "en", "pt_BR") and the category ("SUB",
  "K-SLM-LYRICS" ...);
- 0x81, the Vorbis comments; 0x82 the regions, 0x83 the styles, 0x84 the
  curves, 0x85 the motions, 0x86 the palettes, 0x87 the bitmaps, 0x88 the
  font ranges a text event can name by number;
- 0x00, a text event: its start, duration and backlink in granules, its
  text, an id, motions, and overrides of the language, region, style,
  markup type and more for this event alone.

A granule position is the start of the earliest event still on shifted
left by the granule shift, plus how far after it this one starts.

Reading gives a subtitle each text event, from its start for its
duration. Text in "simple" markup (Pango's, as the Tiger renderer draws
it) gives italic, bold, underline, strikethrough and a colour (<span
foreground>); its entities are decoded, other tags dropped. A region, the
event's own or one of the header's it names, and the halign and valign of
its style (or the region's) give one of the nine places; a motion that
moves the region (semantics region_position) places it where it starts.
The stream's language and category, the ID header's, come back with the
subtitles. A file with several Kate streams is read by its first one, or
by `serial`.

Writing gives a stream of bitstream 0.7 as libkate writes one: nine
regions and nine styles in the headers, one for each of the nine places,
each text event naming the region of its place, its text in simple
markup, the granule rate a thousand a second with a shift of 32, a page
for each event, the end packet at the last end.

Bitmaps (doc/kate-format-bitmap.doc, -rle-bitmap.doc, -palette.doc and
the headers 0x86 and 0x87; kate_decode.c and kate_rle.c where they leave
a detail out): an event may show a picture, its own or one of the
bitmaps header's, in the colours of a palette, its own, one of the
palettes header's, or the one the bitmap names. A bitmap is paletted (its
pixels packed, bitstream 0.1, or coded line by line in one of six kinds
of runs) or a PNG. `pictures` gives a picture_subtitles.Picture for each
event with one, where its region puts it on the canvas (the ID header's,
else the smallest usual frame holding them all), for an OCR add-on; the
stream's text events come with them as their words, as DCP's do
(picture_ocr). A stream of pictures opens as such (`has_bitmaps`), as a
DVD's subtitles do.

What is lost on the way: motions beyond where a region starts, a
region's size where a bitmap is drawn scaled to it, the bitmaps an event
lays in its text, fonts, styles' sizes and colours, karaoke; on writing,
the formatting simple markup has no tag for, rubies (their readings left
out), places beyond the nine, and pictures.
"""

import random
import struct

from subtitld.modules import markup

MAGIC = b'\x80kate\x00\x00\x00'
NUM_HEADERS = 9
VERSION = (0, 7)
# Granules of a thousand a second, the shift libkate takes by default.
GRANULE_RATE = (1000, 1)
GRANULE_SHIFT = 32
# Region metrics: pixels, percent and millionths of the canvas.
PIXEL, PERCENTAGE, MILLIONTHS = 0, 1, 2
MARKUP_NONE, MARKUP_SIMPLE = 0, 1
# Motion semantics: where a region is.
REGION_POSITION = 2


# --- Ogg ----------------------------------------------------------------------

def _ogg_table():
    table = []
    for byte in range(256):
        crc = byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else crc << 1
        table.append(crc & 0xFFFFFFFF)
    return table


_OGG_TABLE = _ogg_table()


def ogg_crc(data):
    """Ogg's page checksum (RFC 3533: CRC-32, polynomial 04C11DB7, no
    reflection, starting at 0)."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _OGG_TABLE[((crc >> 24) ^ byte) & 0xFF]
    return crc


def pages(data):
    """(header_type, granule, serial, sequence, [segment bytes]) for each
    Ogg page in `data`; bytes between pages (a damaged page) are skipped."""
    position = 0
    while True:
        position = data.find(b'OggS', position)
        if position < 0 or position + 27 > len(data):
            return
        if data[position + 4] != 0:
            position += 4
            continue
        header_type = data[position + 5]
        granule, serial, sequence = struct.unpack_from('<qII', data, position + 6)
        count = data[position + 26]
        lacing = data[position + 27:position + 27 + count]
        body = position + 27 + count
        end = body + sum(lacing)
        if len(lacing) < count or end > len(data):
            return
        segments, at = [], body
        for size in lacing:
            segments.append((data[at:at + size], size))
            at += size
        yield header_type, granule, serial, sequence, segments
        position = end


def packets(data):
    """{serial: [(packet, granule of the page it ends on)]} of an Ogg file,
    a packet continued over pages put back together."""
    out, pending = {}, {}
    for header_type, granule, serial, _sequence, segments in pages(data):
        current = pending.get(serial)
        if not header_type & 0x01:
            current = None                           # not a continuation: anything open is lost
        if current is None:
            current = bytearray()
        for piece, size in segments:
            current += piece
            if size < 255:
                out.setdefault(serial, []).append((bytes(current), granule))
                current = bytearray()
        pending[serial] = current if current else None
    return out


def page(serial, sequence, granule, body_packets, header_type=0):
    """The bytes of one Ogg page holding `body_packets` whole (each under
    255 x 255 bytes together): its lacing, its checksum."""
    lacing = bytearray()
    for packet in body_packets:
        size = len(packet)
        lacing += b'\xff' * (size // 255) + bytes((size % 255,))
    head = struct.pack('<4sBBqIIIB', b'OggS', 0, header_type, granule, serial, sequence, 0, len(lacing)) + bytes(lacing)
    whole = head + b''.join(body_packets)
    crc = ogg_crc(whole)
    return whole[:22] + struct.pack('<I', crc) + whole[26:]


# --- Bits ---------------------------------------------------------------------

class _Reader:
    """Ogg's bit unpacker: least significant bit first."""

    def __init__(self, data, position=0):
        self.data, self.bit = data, position * 8

    def read(self, count):
        value = 0
        for index in range(count):
            byte = self.bit >> 3
            if byte >= len(self.data):
                raise ValueError('Kate packet ends early')
            value |= ((self.data[byte] >> (self.bit & 7)) & 1) << index
            self.bit += 1
        return value

    def skip(self, count):
        self.bit += count
        if self.bit > len(self.data) * 8:
            raise ValueError('Kate packet ends early')

    def read32(self):
        value = self.read(32)
        return value - (1 << 32) if value & 0x80000000 else value

    def read64(self):
        value = self.read(64)
        return value - (1 << 64) if value & (1 << 63) else value

    def read32v(self):
        small = self.read(4)
        if small != 15:
            return small
        sign = self.read(1)
        value = self.read(self.read(5) + 1)
        return -value if sign else value

    def buffer(self, count):
        if not self.bit & 7:
            start = self.bit >> 3
            if start + count > len(self.data):
                raise ValueError('Kate packet ends early')
            self.bit += 8 * count
            return bytes(self.data[start:start + count])
        return bytes(self.read(8) for _ in range(count))

    def warp(self):
        """Skip the warps that end a structure, to the empty one."""
        while True:
            bits = self.read32v()
            if bits <= 0:
                if bits < 0:
                    raise ValueError('Kate packet has a bad warp')
                return
            self.skip(bits)

    def floats(self, count, streams=1):
        """`count` x `streams` floats (kate_fp_decode_kate_float)."""
        if count * streams == 0:
            return []
        if streams > 1 and self.read(1):
            count, streams = count * streams, 1
        values = [0.0] * (count * streams)
        for stream in range(streams):
            head, tail = self.read(4), self.read(4)
            bits = 32 - head - tail
            for index in range(count):
                sign = self.read(1) if head > 0 else 0
                value = self.read(bits) << tail
                if value & 0x80000000:
                    value -= 1 << 32
                values[index * streams + stream] = (-value if sign else value) / 65536.0
        return values


class _Writer:
    """Ogg's bit packer: least significant bit first."""

    def __init__(self):
        self.bits = []

    def write(self, value, count):
        for index in range(count):
            self.bits.append((value >> index) & 1)

    def write32v(self, value):
        if 0 <= value <= 14:
            self.write(value, 4)
            return
        self.write(15, 4)
        self.write(1 if value < 0 else 0, 1)
        value = abs(value)
        size = max(1, value.bit_length())
        self.write(size - 1, 5)
        self.write(value, size)

    def buffer(self, data):
        for byte in data:
            self.write(byte, 8)

    def warp(self, inner=None):
        """A warp holding `inner`'s bits (an empty one, which ends a
        structure, without)."""
        bits = inner.bits if inner is not None else []
        self.write32v(len(bits))
        self.bits.extend(bits)

    def floats(self, values):
        """`values` as one stream of 16.16 fixed point (kate_fp_encode)."""
        fixed = []
        for value in values:
            scaled = value * 65536.0
            fixed.append(int(scaled + 0.5) if int(value * 131072.0) & 1 else int(scaled))
        merged = 0
        for value in fixed:
            merged |= abs(value)
        head = 0
        while head < 15 and not (merged << head) & 0x80000000:
            head += 1
        tail = 0
        while tail < 15 and not (merged >> tail) & 1:
            tail += 1
        self.write(head, 4)
        self.write(tail, 4)
        bits = 32 - head - tail
        for value in fixed:
            if head > 0:
                self.write(1 if value < 0 else 0, 1)
                value = abs(value)
            self.write((value & 0xFFFFFFFF) >> tail, bits)

    def bytes(self):
        out = bytearray((len(self.bits) + 7) // 8)
        for index, bit in enumerate(self.bits):
            if bit:
                out[index >> 3] |= 1 << (index & 7)
        return bytes(out)


# --- Reading ------------------------------------------------------------------

def _region(reader, version):
    metric = reader.read(8)
    x, y, w, h, style = (reader.read32v() for _ in range(5))
    if version >= (0, 2):
        reader.read32v()
        reader.read(1)                               # clip
    if version >= (0, 6):
        reader.read32v()
        _metadata(reader)
    reader.warp()
    return {'metric': metric, 'x': x, 'y': y, 'w': w, 'h': h, 'style': style}


def _metadata(reader):
    if not reader.read(1):
        return
    for _ in range(reader.read32v()):
        reader.buffer(reader.read32v())
        reader.buffer(reader.read32v())
        reader.warp()
    reader.warp()


def _color(reader):
    return tuple(reader.read(8) for _ in range(4))


def _style(reader, version):
    values = reader.floats(8)
    style = {'halign': values[0], 'valign': values[1], 'text_color': _color(reader)}
    _color(reader)
    _color(reader)
    reader.read(16)                                  # font and margin metrics
    style.update(bold=reader.read(1), italic=reader.read(1), underline=reader.read(1), strike=reader.read(1))
    if version >= (0, 2):
        reader.read32v()
        reader.read(1)                               # justify
        reader.buffer(reader.read32v())              # font
    if version >= (0, 4):
        reader.read32v()
        reader.read32v()                             # wrap mode
    if version >= (0, 6):
        reader.read32v()
        _metadata(reader)
    reader.warp()
    return style


def _curve(reader):
    kind = reader.read(8)
    count = reader.read32v()
    reader.warp()
    return {'type': kind, 'points': reader.floats(count, 2)}


def _motion(reader, info):
    curves = []
    for _ in range(reader.read32v()):
        if reader.read(1):
            index = reader.read32v()
            curves.append(info['curves'][index] if 0 <= index < len(info['curves']) else None)
        else:
            curves.append(_curve(reader))
    reader.floats(len(curves))                       # durations
    x_mapping, y_mapping, semantics = reader.read(8), reader.read(8), reader.read(8)
    reader.read(1)                                   # periodic
    if info['version'] >= (0, 6):
        reader.read32v()
        _metadata(reader)
    reader.warp()
    return {'curves': curves, 'x_mapping': x_mapping, 'y_mapping': y_mapping, 'semantics': semantics}


def _palette(reader, version):
    """A palette: its colours, [(r, g, b, a)]."""
    colours = [_color(reader) for _ in range(reader.read(8) + 1)]
    if version >= (0, 6):
        reader.read32v()
        _metadata(reader)
    reader.warp()
    return colours


# Bitmaps: no larger than libkate takes one (kate_internal.h).
BITMAP_LIMIT = 4096
PALETTED, PNG = 0, 1
RLE = 1
# The six kinds of line in a bitmap coded in runs (kate_rle.c).
_EMPTY, _BASIC, _DELTA, _BASIC_STOP, _BASIC_STARTEND, _DELTA_STOP, _BASIC_ZERO = range(7)


def _rle_line(reader, kind, width, bits, zero, previous):
    """One line of a bitmap coded in runs (doc/kate-format-rle-bitmap.doc,
    the run lengths' sizes kate_rle.c's), a bytearray."""
    line = bytearray([zero]) * width
    if kind == _EMPTY:
        return line
    x = 0

    def fill(count, value):
        nonlocal x
        if count <= 0 or x + count > width:
            raise ValueError('Kate bitmap run goes past its line')
        line[x:x + count] = bytes([value]) * count
        x += count

    def copy(count):
        nonlocal x
        if count <= 0 or x + count > width:
            raise ValueError('Kate bitmap run goes past its line')
        if previous is not None:
            line[x:x + count] = previous[x:x + count]
        x += count

    if kind == _BASIC:
        while x < width:
            count = reader.read(4) + 1
            fill(count, reader.read(bits))
    elif kind == _DELTA:
        while x < width:
            if reader.read(1):
                copy(reader.read(6) + 1)
            else:
                count = reader.read(3) + 1
                fill(count, reader.read(bits))
    elif kind == _BASIC_STOP:
        start = reader.read(8)
        if start:
            fill(start, zero)
        while x < width:
            count = reader.read(6)
            if not count:
                break                                # the rest is the zero colour
            fill(count, reader.read(bits))
    elif kind == _BASIC_STARTEND:
        start, end = reader.read(9), reader.read(8)
        if start:
            fill(start, zero)
        if end > width - x:
            raise ValueError('Kate bitmap run goes past its line')
        stop = width - end
        while x < stop:
            count = reader.read(3) + 1
            if x + count > stop:
                raise ValueError('Kate bitmap run goes past its line')
            fill(count, reader.read(bits))
    elif kind == _DELTA_STOP:
        while x < width:
            if reader.read(1):
                copy(reader.read(5) + 1)
            else:
                count = reader.read(3)
                if not count:
                    break
                fill(count, reader.read(bits))
    elif kind == _BASIC_ZERO:
        while x < width:
            value = reader.read(bits)
            fill(reader.read(8 if value == zero else 3) + 1, value)
    else:
        raise ValueError('Kate bitmap line of an unknown kind')
    return line


def _rle(reader, width, height, bits):
    """A bitmap coded in runs: its palette indices, row after row."""
    zero = reader.read(bits)
    rows, previous = [], None
    for _ in range(height):
        previous = _rle_line(reader, reader.read(3), width, bits, zero, previous)
        rows.append(previous)
    return b''.join(rows)


def _bitmap(reader, version):
    """A bitmap: {'width', 'height', 'palette' (the index of the palette it
    names, -1 for none), 'indices' (a paletted one's pixels) or 'png'}."""
    width, height = reader.read32v(), reader.read32v()
    bpp = reader.read(8)
    if not (0 < width <= BITMAP_LIMIT and 0 < height <= BITMAP_LIMIT) or bpp > 8:
        raise ValueError('Kate bitmap of a size Subtitld does not read')
    bitmap = {'width': width, 'height': height, 'palette': -1, 'indices': None, 'png': None}
    if bpp == 0:
        kind = reader.read(8)
        if kind == PALETTED:
            if reader.read(8) != RLE:
                raise ValueError('Kate bitmap in an unknown encoding')
            bits = reader.read32v()
            bitmap['palette'] = reader.read32v()
            if not 0 < bits <= 8:
                raise ValueError('Kate bitmap of an unknown depth')
            bitmap['indices'] = _rle(reader, width, height, bits)
        elif kind == PNG:
            bitmap['png'] = reader.buffer(reader.read32() & 0xFFFFFFFF)
        else:
            raise ValueError('Kate bitmap of an unknown type')
    else:
        # Bitstream 0.1's: the pixels packed, `bpp` bits each.
        bitmap['palette'] = reader.read32v()
        bitmap['indices'] = bytes(reader.read(bpp) for _ in range(width * height))
    if version >= (0, 4):
        reader.read32v()
        reader.read32v()                             # the offsets to its logical centre
        reader.read32v()
    if version >= (0, 6):
        reader.read32v()
        _metadata(reader)
    reader.warp()
    return bitmap



def _headers(found):
    """The stream's ID header and what its other headers define."""
    info = {'version': (0, 0), 'curves': [], 'regions': [], 'styles': [], 'motions': [], 'language': '',
            'category': '', 'shift': GRANULE_SHIFT, 'rate': GRANULE_RATE, 'canvas': (0, 0), 'headers': 1,
            'comments': [], 'palettes': [], 'bitmaps': []}
    for packet in found:
        kind = packet[0]
        if not kind & 0x80 or packet[1:8] != MAGIC[1:]:
            continue
        reader = _Reader(packet, 9)
        try:
            if kind == 0x80:
                major, minor, info['headers'] = reader.read(8), reader.read(8), reader.read(8)
                info['version'] = (major, minor)
                info['encoding'], info['direction'] = reader.read(8), reader.read(8)
                reader.read(8)
                info['shift'] = reader.read(8)
                sizes = []
                for _ in range(2):
                    shift = reader.read(4)
                    base = reader.read(4) | (reader.read(8) << 4)
                    sizes.append(base << shift)
                info['canvas'] = tuple(sizes)
                reader.read32()
                info['rate'] = (reader.read32() or 1, reader.read32() or 1)
                info['language'] = reader.buffer(16).split(b'\x00')[0].decode('utf-8', 'replace')
                info['category'] = reader.buffer(16).split(b'\x00')[0].decode('utf-8', 'replace')
            elif kind == 0x81:
                reader.buffer(reader.read32() & 0xFFFFFFFF)
                for _ in range(reader.read32() & 0xFFFFFFFF):
                    info['comments'].append(reader.buffer(reader.read32() & 0xFFFFFFFF).decode('utf-8', 'replace'))
            elif kind == 0x82:
                info['regions'] = [_region(reader, info['version']) for _ in range(reader.read32v())]
            elif kind == 0x83:
                info['styles'] = [_style(reader, info['version']) for _ in range(reader.read32v())]
            elif kind == 0x84:
                info['curves'] = [_curve(reader) for _ in range(reader.read32v())]
            elif kind == 0x85:
                info['motions'] = [_motion(reader, info) for _ in range(reader.read32v())]
            elif kind == 0x86:
                info['palettes'] = [_palette(reader, info['version']) for _ in range(reader.read32v())]
            elif kind == 0x87:
                info['bitmaps'] = [_bitmap(reader, info['version']) for _ in range(reader.read32v())]
        except (ValueError, IndexError):
            continue                                 # a header Subtitld cannot read: what it defines is not used
    return info


def _event(packet, info):
    """A text event: {'start', 'end', 'text', 'markup', 'region', 'style',
    'motions', 'palette', 'bitmap'}; times in seconds."""
    reader = _Reader(packet, 1)
    start, duration = reader.read64(), reader.read64()
    reader.read64()                                  # backlink
    numerator, denominator = info['rate']
    text = reader.buffer(reader.read32() & 0xFFFFFFFF)
    event = {'start': start * denominator / numerator, 'end': (start + duration) * denominator / numerator,
             'text': text, 'markup': MARKUP_NONE, 'region': None, 'style': None, 'motions': [], 'encoding': 0,
             'palette': None, 'bitmap': None}
    version = info['version']
    try:
        if reader.read(1):
            reader.read32v()                         # id
        if reader.read(1):
            for _ in range(reader.read32v()):
                if reader.read(1):
                    index = reader.read32v()
                    if 0 <= index < len(info['motions']):
                        event['motions'].append(info['motions'][index])
                else:
                    event['motions'].append(_motion(reader, info))
        if reader.read(1):
            if reader.read(1):
                event['encoding'] = reader.read(8)
            if reader.read(1):
                reader.read(8)                       # directionality
            if reader.read(1):
                event['language'] = reader.buffer(reader.read32v()).decode('utf-8', 'replace')
            if reader.read(1):
                index = reader.read32v()
                if 0 <= index < len(info['regions']):
                    event['region'] = info['regions'][index]
            if reader.read(1):
                event['region'] = _region(reader, version)
            if reader.read(1):
                index = reader.read32v()
                if 0 <= index < len(info['styles']):
                    event['style'] = info['styles'][index]
            if reader.read(1):
                event['style'] = _style(reader, version)
            if reader.read(1):
                reader.read32v()                     # secondary style, by number
            if reader.read(1):
                _style(reader, version)              # secondary style
            if reader.read(1):
                reader.read32v()                     # font mapping
        if version >= (0, 2):
            reader.read32v()
            if reader.read(1):
                if reader.read(1):
                    index = reader.read32v()
                    if 0 <= index < len(info['palettes']):
                        event['palette'] = info['palettes'][index]
                if reader.read(1):
                    event['palette'] = _palette(reader, version)
                if reader.read(1):
                    index = reader.read32v()
                    if 0 <= index < len(info['bitmaps']):
                        event['bitmap'] = info['bitmaps'][index]
                if reader.read(1):
                    event['bitmap'] = _bitmap(reader, version)
                if reader.read(1):
                    event['markup'] = reader.read(8)
    except (ValueError, IndexError):
        pass                                         # what follows the text is past reading: the text stays
    if event['style'] is None and event['region'] is not None:
        index = event['region']['style']
        if 0 <= index < len(info['styles']):
            event['style'] = info['styles'][index]
    return event


def _fraction(value, metric, size):
    """A region's coordinate as a part of the canvas (0 to 1)."""
    if metric == PERCENTAGE:
        return value / 100.0
    if metric == MILLIONTHS:
        return value / 1000000.0
    return value / float(size) if size else None


def _keypad(event, info):
    """The place of an event: from its region (moved where a motion puts it
    first) and its style's alignment; None when it says nothing."""
    region, style = event['region'], event['style']
    column = row = None
    if style is not None:
        column = 0 if style['halign'] < -0.33 else 2 if style['halign'] > 0.33 else 1
        row = 2 if style['valign'] < -0.33 else 0 if style['valign'] > 0.33 else 1
    centre = None
    if region is not None:
        width, height = info['canvas']
        left, top = _fraction(region['x'], region['metric'], width), _fraction(region['y'], region['metric'], height)
        across, down = _fraction(region['w'], region['metric'], width), _fraction(region['h'], region['metric'], height)
        if None not in (left, top, across, down):
            centre = [left + across / 2, top + down / 2]
    for motion in event['motions']:
        curve = motion['curves'][0] if motion['curves'] else None
        if motion['semantics'] == REGION_POSITION and curve and len(curve['points']) >= 2 and \
                motion['x_mapping'] in (0, 1) and motion['y_mapping'] in (0, 1):
            x, y = curve['points'][0], curve['points'][1]
            width, height = info['canvas']
            if motion['x_mapping'] == 0:
                x = x / width if width else None
            if motion['y_mapping'] == 0:
                y = y / height if height else None
            if x is not None and y is not None and region is not None and centre is not None:
                centre = [x + (centre[0] - _fraction(region['x'], region['metric'], width)),
                          y + (centre[1] - _fraction(region['y'], region['metric'], height))]
            elif x is not None and y is not None:
                centre = [x, y]
    if centre is not None:
        if row is None or region is not None:
            row = 2 if centre[1] < 1 / 3 else 1 if centre[1] < 2 / 3 else 0
        if column is None:
            column = 0 if centre[0] < 1 / 3 else 1 if centre[0] < 2 / 3 else 2
    if row is None and column is None:
        return None
    return (1, 4, 7)[row if row is not None else 0] + (column if column is not None else 1)


# Pango's markup, as simple markup is drawn: the tags Subtitld keeps.
_SIMPLE_FLAGS = {'i': 'i', 'b': 'b', 'u': 'u', 's': 's'}


def from_simple(text):
    """Subtitld's markup for Kate's simple markup: <i>, <b>, <u>, <s> as they
    are, a <span foreground> as a <font color>, the entities decoded; other
    tags left out."""
    import html
    import re
    out, opened = [], []
    for token in re.split(r'(<[^<>]*>)', text):
        if not token.startswith('<'):
            out.append(token.replace('<', '').replace('>', ''))
            out[-1] = html.unescape(out[-1])
            continue
        name = re.match(r'<\s*(/?)\s*(\w+)', token)
        if not name:
            continue
        closing, tag = name.group(1), name.group(2).lower()
        if tag in _SIMPLE_FLAGS:
            out.append(f'<{closing}{tag}>')
        elif tag == 'span' and not closing:
            color = re.search(r'''(?:foreground|color|fgcolor)\s*=\s*["']([^"']+)["']''', token)
            value = markup.color_hex(color.group(1)) if color else ''
            opened.append(bool(value))
            if value:
                out.append(f'<font color="{value}">')
        elif tag == 'span' and closing:
            if opened and opened.pop():
                out.append('</font>')
    return ''.join(out)


def to_simple(text):
    """Kate's simple markup for a subtitle's text: italic, bold, underline
    and strikethrough as Pango's tags, a colour as a <span foreground>, the
    rest escaped; readings left out, the place code dropped."""
    import html
    out = []
    for run, style in markup.without_readings(markup.spans(text)):
        piece = html.escape(run, quote=False)
        if not piece:
            continue
        if style.color:
            piece = f'<span foreground="{style.color}">{piece}</span>'
        for flag, tag in (('strike', 's'), ('underline', 'u'), ('bold', 'b'), ('italic', 'i')):
            if getattr(style, flag):
                piece = f'<{tag}>{piece}</{tag}>'
        out.append(piece)
    return ''.join(out)


def is_kate(data):
    """Whether the bytes of an Ogg file (its start) hold a Kate stream."""
    data = bytes(data)
    return data[:4] == b'OggS' and MAGIC in data[:65536]


def streams(data):
    """[{'serial', 'language', 'category'}] of the Kate streams of an Ogg
    file's bytes, in the order they begin."""
    out = []
    for serial, found in packets(bytes(data)).items():
        if found and found[0][0].startswith(MAGIC):
            info = _headers([packet for packet, _granule in found[:NUM_HEADERS + 1]])
            out.append({'serial': serial, 'language': info['language'], 'category': info['category']})
    return out


def _events(data, serial=None):
    """(info, events) of the Kate stream `serial` (the first one when
    None) of an Ogg file's bytes. Raises ValueError for one with no Kate
    stream."""
    found = packets(bytes(data))
    serials = [key for key, value in found.items() if value and value[0][0].startswith(MAGIC)]
    if not serials:
        raise ValueError('There is no Kate stream in this file')
    serial = serial if serial in serials else serials[0]
    stream = [packet for packet, _granule in found[serial]]
    info = _headers([packet for packet in stream if packet[:1] and packet[0] & 0x80])
    events = []
    for packet in stream:
        if not packet or packet[0] != 0x00:
            continue
        try:
            events.append(_event(packet, info))
        except ValueError:
            continue
    return info, events


def _event_text(event, info):
    """An event's words in Subtitld's markup, its place code first; '' for
    none (or an encoding other than UTF-8, the only one defined)."""
    if event['encoding'] != 0:
        return ''
    text = event['text'].decode('utf-8', 'replace').replace('\r\n', '\n').rstrip('\x00')
    text = from_simple(text) if event['markup'] == MARKUP_SIMPLE else text
    if not markup.plain(text).strip():
        return ''
    keypad = _keypad(event, info)
    return (markup.with_alignment('', keypad) if keypad else '') + text


def parse(data, serial=None):
    """(subtitles, {'language', 'category'}) of the Kate stream `serial`
    (the first one when None) of an Ogg file's bytes. Raises ValueError for
    one with no Kate stream."""
    info, events = _events(data, serial)
    segments = []
    for event in events:
        text = _event_text(event, info)
        if text:
            segments.append({'start': round(event['start'], 3), 'end': round(max(event['start'], event['end']), 3),
                             'text': text})
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments, {'language': info['language'], 'category': info['category']}


def has_bitmaps(data, serial=None):
    """Whether a Kate stream of an Ogg file's bytes shows pictures."""
    try:
        return any(event['bitmap'] is not None for event in _events(data, serial)[1])
    except ValueError:
        return False


# The frames a canvas the ID header leaves unsaid may be, smallest first.
_FRAMES = ((720, 480), (720, 576), (1280, 720), (1920, 1080), (3840, 2160))
_GRAYS = [(level * 255 // 15, level * 255 // 15, level * 255 // 15, 255) for level in range(16)]


def _bitmap_picture(bitmap, palette):
    """(width, height, indices, colours) of a bitmap, or None."""
    if bitmap['png'] is not None:
        from subtitld.modules import picture_ocr
        return picture_ocr.png_picture(bitmap['png'])
    colours = list(palette or _GRAYS)
    return bitmap['width'], bitmap['height'], bitmap['indices'], colours + [(0, 0, 0, 0)] * (256 - len(colours))


def picture_list(data, serial=None):
    """(pictures, {'language', 'category'}) of the Kate stream `serial` of
    an Ogg file's bytes: a picture_subtitles.Picture for each event with a
    bitmap, in the colours of its palette (the event's, else the one the
    bitmap names, else grays), where its region puts it on the canvas;
    the other events' words as pictures of one clear pixel, at the top or
    the bottom as their place is."""
    from subtitld.modules.picture_subtitles import Picture
    info, events = _events(data, serial)
    width, height = info['canvas']
    placed = []
    for event in events:
        if event['bitmap'] is None:
            continue
        bitmap = event['bitmap']
        palette = event['palette']
        if palette is None and 0 <= bitmap['palette'] < len(info['palettes']):
            palette = info['palettes'][bitmap['palette']]
        decoded = _bitmap_picture(bitmap, palette)
        if decoded is None:
            continue
        region = event['region']
        if region is not None and region['metric'] == PIXEL:
            x, y = region['x'], region['y']
        elif region is not None and width and height:
            x = round(_fraction(region['x'], region['metric'], width) * width)
            y = round(_fraction(region['y'], region['metric'], height) * height)
        else:
            x = y = None                             # placed once the frame is known
        placed.append((event, decoded, x, y))
    if not (width and height):
        right = max([x + d[0] for _e, d, x, _y in placed if x is not None] + [0])
        bottom = max([y + d[1] for _e, d, _x, y in placed if y is not None] + [0])
        width, height = next((frame for frame in _FRAMES if frame[0] >= right and frame[1] >= bottom),
                             (right, bottom))
    found = []
    for event, (across, down, indices, colours), x, y in placed:
        if x is None:
            # No region: centred, low on the frame, as a subtitle is.
            x, y = max(0, (width - across) // 2), max(0, height - down - height // 20)
        found.append(Picture(event['start'], max(event['start'], event['end']), x, y, across, down, indices, colours,
                             False, width, height, ''))
    for event in events:
        if event['bitmap'] is not None:
            continue
        text = _event_text(event, info)
        if not text:
            continue
        lead, words = markup.split_lead(text)
        top = markup.alignment(lead) >= 7
        found.append(Picture(event['start'], max(event['start'], event['end']), 0, 0 if top else height - 1, 1, 1,
                             b'\x00', [(0, 0, 0, 0)], False, width, height, words))
    found.sort(key=lambda picture: (picture.start, picture.end))
    return found, {'language': info['language'], 'category': info['category']}


def pictures(path, serial=None):
    """(pictures, {'language', 'category'}) of the Kate file at `path` (picture_list)."""
    with open(path, 'rb') as kate_file:
        return picture_list(kate_file.read(), serial)


def read(path, serial=None):
    """(subtitles, {'language', 'category'}) of the Kate file at `path`."""
    with open(path, 'rb') as kate_file:
        return parse(kate_file.read(), serial)


# --- Writing ------------------------------------------------------------------

def _header(kind):
    writer = _Writer()
    writer.buffer(bytes((kind,)) + MAGIC[1:] + b'\x00')
    return writer


def _canvas(writer, size):
    shift, base = 0, int(size)
    while base >= 1 << 12:
        shift += 1
        base >>= 1
    writer.write(shift, 4)
    writer.write(base & 0x0F, 4)
    writer.write(base >> 4, 8)


def _id_header(language, category, width, height):
    writer = _header(0x80)
    for value in (VERSION[0], VERSION[1], NUM_HEADERS, 0, 0, 0, GRANULE_SHIFT):
        writer.write(value, 8)
    _canvas(writer, width)
    _canvas(writer, height)
    writer.write(0, 32)
    writer.write(GRANULE_RATE[0], 32)
    writer.write(GRANULE_RATE[1], 32)
    writer.buffer(language.encode('ascii', 'ignore')[:15].ljust(16, b'\x00'))
    writer.buffer(category.encode('ascii', 'ignore')[:15].ljust(16, b'\x00'))
    return writer.bytes()


def _comment_header(vendor='Subtitld'):
    writer = _header(0x81)
    writer.write(len(vendor), 32)
    writer.buffer(vendor.encode('utf-8'))
    writer.write(0, 32)
    return writer.bytes()


# Where each of the nine places puts its region, in percent of the canvas
# (x, y, width, height): a band across a tenth of it at the top, middle or
# bottom, inside the title safe area.
_ROWS = {0: 75, 1: 40, 2: 5}


def _region_bits(writer, keypad):
    row = (keypad - 1) // 3
    writer.write(PERCENTAGE, 8)
    for value in (5, _ROWS[row], 90, 20, keypad - 1):
        writer.write32v(value)
    clip = _Writer()
    clip.write(0, 1)
    writer.warp(clip)
    meta = _Writer()
    meta.write(0, 1)
    writer.warp(meta)
    writer.warp()


def _style_bits(writer, keypad):
    row, column = (keypad - 1) // 3, (keypad - 1) % 3
    # halign and valign from -1 (left, top) to 1 (right, bottom), the font
    # size and margins left to the renderer.
    writer.floats([column - 1.0, 1.0 - row, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    for color in ((255, 255, 255, 255), (0, 0, 0, 0), (255, 255, 255, 255)):
        for part in color:
            writer.write(part, 8)
    writer.write(PIXEL, 8)
    writer.write(PIXEL, 8)
    writer.write(0, 4)                               # bold, italics, underline, strike
    justify = _Writer()
    justify.write(0, 1)
    justify.write32v(0)
    writer.warp(justify)
    wrap = _Writer()
    wrap.write32v(0)
    writer.warp(wrap)
    meta = _Writer()
    meta.write(0, 1)
    writer.warp(meta)
    writer.warp()


def _list_header(kind, count=0, item=None):
    writer = _header(kind)
    writer.write32v(count)
    for index in range(count):
        item(writer, index + 1)
    if kind == 0x88:
        writer.write32v(0)                           # font mappings
    writer.warp()
    return writer.bytes()


def _text_packet(start, duration, backlink, text, keypad):
    writer = _Writer()
    writer.write(0x00, 8)
    for value in (start, duration, backlink):
        writer.write(value & 0xFFFFFFFFFFFFFFFF, 64)
    data = text.encode('utf-8')
    writer.write(len(data), 32)
    writer.buffer(data)
    writer.write(0, 1)                               # no id
    writer.write(0, 1)                               # no motions
    writer.write(1, 1)                               # overrides
    for flag in (0, 0, 0, 1):                        # encoding, direction, language, region index
        writer.write(flag, 1)
    writer.write32v(keypad - 1)
    for _ in range(6):                               # region, styles, secondary styles, font mapping
        writer.write(0, 1)
    extra = _Writer()                                # 0.2: palette, bitmap, markup type
    extra.write(1, 1)
    for _ in range(4):
        extra.write(0, 1)
    extra.write(1, 1)
    extra.write(MARKUP_SIMPLE, 8)
    writer.warp(extra)
    bitmaps = _Writer()                              # 0.4: no bitmaps
    bitmaps.write32v(0)
    writer.warp(bitmaps)
    meta = _Writer()                                 # 0.6: no metadata
    meta.write(0, 1)
    writer.warp(meta)
    writer.warp()
    return writer.bytes()


def language_tag(language):
    """The language as Kate writes it (RFC 3066, as kateenc's -l): 'pt_BR'
    for Subtitld's 'pt-br', 'en' for 'en'."""
    parts = str(language or '').replace('_', '-').split('-')
    if not parts or not parts[0]:
        return ''
    return parts[0].lower() + ('_' + parts[1].upper() if len(parts) > 1 and parts[1] else '')


def write(segments, language='', category='SUB', width=0, height=0, serial=None):
    """The bytes of an Ogg file with a Kate stream holding `segments`."""
    serial = serial if serial is not None else random.randint(1, 0x7FFFFFFF)
    numerator, denominator = GRANULE_RATE
    events = []
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        text = str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
        if not markup.plain(text).strip():
            continue
        start = max(0, int(round(float(segment.get('start', 0)) * numerator / denominator)))
        end = max(start, int(round(float(segment.get('end', 0)) * numerator / denominator)))
        events.append((start, end, to_simple(text), markup.alignment(text)))
    out = [page(serial, 0, 0, [_id_header(language_tag(language), category, width, height)], 0x02)]
    headers = [_comment_header(), _list_header(0x82, 9, _region_bits), _list_header(0x83, 9, _style_bits)] + \
        [_list_header(kind) for kind in (0x84, 0x85, 0x86, 0x87, 0x88)]
    out.append(page(serial, 1, 0, headers))
    sequence, active, last_end = 2, [], 0
    for start, end, text, keypad in events:
        # The earliest event still on when this one starts: the granule's base.
        active = [(begin, finish) for begin, finish in active if finish > start] + [(start, end)]
        earliest = min(begin for begin, _finish in active)
        granule = (earliest << GRANULE_SHIFT) | (start - earliest)
        out.append(page(serial, sequence, granule, [_text_packet(start, end - start, start - earliest, text, keypad)]))
        sequence += 1
        last_end = max(last_end, end)
    out.append(page(serial, sequence, last_end << GRANULE_SHIFT, [b'\x7f'], 0x04))
    return b''.join(out)


def save(path, segments, language='', category='SUB', width=0, height=0):
    """Write `segments` to `path` as an Ogg file with a Kate stream."""
    with open(path, 'wb') as kate_file:
        kate_file.write(write(segments, language, category, width, height))
