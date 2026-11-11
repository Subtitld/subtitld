"""DVB subtitles (ETSI EN 300 743): the pictures DVB broadcasts carry in an
MPEG transport stream, read for an OCR add-on, and written into one.

A DVB subtitle stream is a PES stream of private data (stream_type 6,
private_stream_1) that the PMT names with a subtitling descriptor (EN 300
468, 0x59): for each service in it, its language (ISO 639-2), its type
(0x10 to 0x14 for subtitles, 0x20 to 0x24 for subtitles for the hard of
hearing; the last of each for an HD display) and the pages it is on, a
composition page and an ancillary page whose segments (palettes and
pictures shared by several languages) it may use too. Each PES packet
starts with a data_identifier of 0x20 and a subtitle_stream_id of 0, then
holds segments, each a sync byte (0x0F), a type, its page and its length:

- 0x10, page composition: which regions the page shows and where, from
  the PES packet's presentation time (PTS) on, and for how long at most
  (page_time_out, in seconds); its page_state says whether it updates what
  is shown (normal case) or starts again (acquisition point, mode change:
  everything the decoder holds is let go);
- 0x11, region composition: a rectangle of pixels, its depth (2, 4 or 8
  bits a pixel), its palette and the objects in it, at their places;
- 0x12, CLUT definition: a palette's entries as Y, Cr, Cb and a
  transparency T (BT.601; Y 0 is no colour at all), each for the 2-, 4-
  or 8-bit regions it says, over a default palette for each depth;
- 0x13, object data: a picture coded as strings of 2-, 4- or 8-bit pixel
  codes run-length coded, a line at a time, its top field (even lines)
  and its bottom field (odd lines, or the top field again when it has
  none), with map tables for a 2- or 4-bit string in a deeper region;
- 0x14, display definition: the size of the display the regions are laid
  on (720x576 without one), and a window on it they are placed in;
- 0x80, end of display set: what came since the page composition is
  complete, and shown.

A subtitle is what a display set shows, the regions of its page drawn
into one picture, from its PTS until the next display set of the page or
its time-out, whichever comes first; a display set that shows nothing (no
regions, or regions with nothing in them) takes the subtitle away. Times
count from the earliest start of the file's streams, as FFmpeg counts
them. An object coded as characters rather than pixels gives its words,
which need no reading.

`read(path)` gives picture_subtitles.Picture for each subtitle of a
transport stream's DVB subtitle stream, with its language; `decode` reads
PES packets handed over by ffprobe, for a stream in another container.
The transport stream itself is read with the teletext module's demuxer.

Writing gives a transport stream with a DVB subtitle stream for each
language, each subtitle the picture `subtitle_pictures.draw` makes of it
(the preview's renderer), its colours brought down to a palette of 256
(or 16) entries, coded with 8-bit (or 4-bit) pixel code strings: in 16,
4-bit, where a display set would not fit in one PES packet otherwise
(FFmpeg reads none that takes more), and an object for each band of its
lines that fits in a segment. A display definition for the display it was
drawn for (1920x1080, or 720x576), a page in a mode change for each
subtitle (two at once, two regions), and a page with nothing on it when it
ends. `write_video` has FFmpeg copy those
streams into a transport stream of the video, its picture and sound
copied as well.
"""

import os
import subprocess
import tempfile

from subtitld.modules import teletext
from subtitld.modules.picture_subtitles import Picture

PAGE_COMPOSITION, REGION_COMPOSITION, CLUT_DEFINITION, OBJECT_DATA = 0x10, 0x11, 0x12, 0x13
DISPLAY_DEFINITION, END_OF_DISPLAY_SET = 0x14, 0x80
# The subtitling descriptor's types that are DVB subtitles: for any
# display, 4:3, 16:9, 2.21:1 and HD (and 3D), and the same for the hard of
# hearing.
NORMAL_TYPES = frozenset(range(0x10, 0x16))
HARD_OF_HEARING_TYPES = frozenset(range(0x20, 0x26))
SUBTITLE_TYPES = NORMAL_TYPES | HARD_OF_HEARING_TYPES
SD_DISPLAY = (720, 576)
HD_DISPLAY = (1920, 1080)
_PTS_WRAP = 1 << 33


# --- Palettes ------------------------------------------------------------------

def _default_cluts():
    """The default palettes of EN 300 743 (10.1 to 10.3), as (r, g, b, a)
    for the 2-, 4- and 8-bit regions: entry 0 is no colour at all."""
    two = [(0, 0, 0, 0), (255, 255, 255, 255), (0, 0, 0, 255), (127, 127, 127, 255)]
    four = [(0, 0, 0, 0)]
    for entry in range(1, 16):
        level = 255 if entry < 8 else 127
        four.append((level * (entry & 1), level * (entry >> 1 & 1), level * (entry >> 2 & 1), 255))
    eight = [(0, 0, 0, 0)]
    for entry in range(1, 256):
        bits = [entry >> shift & 1 for shift in range(8)]
        if entry < 8:
            eight.append((255 * bits[0], 255 * bits[1], 255 * bits[2], 63))
            continue
        low, high, alpha, base = {0x00: (85, 170, 255, 0), 0x08: (85, 170, 127, 0),
                                  0x80: (43, 85, 255, 127), 0x88: (43, 85, 255, 0)}[entry & 0x88]
        eight.append(tuple(base + low * bits[channel] + high * bits[channel + 4] for channel in range(3)) + (alpha,))
    return {2: two, 4: four, 8: eight}


DEFAULT_CLUTS = _default_cluts()


def ycrcb_to_rgba(y, cr, cb, t):
    """A CLUT entry as (r, g, b, a): BT.601, as FFmpeg shows it; Y 0 is no
    colour at all, whatever the others say."""
    if not y:
        return (0, 0, 0, 0)
    y, cb, cr = y - 16, cb - 128, cr - 128
    r, g, b = 1.164 * y + 1.596 * cr, 1.164 * y - 0.392 * cb - 0.813 * cr, 1.164 * y + 2.017 * cb
    clamp = lambda v: max(0, min(255, int(round(v))))  # noqa: E731
    return clamp(r), clamp(g), clamp(b), 255 - t


def rgba_to_ycrcb(r, g, b, a):
    """(Y, Cr, Cb, T) of a colour, BT.601 with Y from 16 (never 0, which is
    no colour); no opacity at all is entry 0's Y 0."""
    if not a:
        return (0, 128, 128, 255)
    clamp = lambda v, low, high: max(low, min(high, int(round(v))))  # noqa: E731
    y = 16 + (65.738 * r + 129.057 * g + 25.064 * b) / 256
    cb = 128 + (-37.945 * r - 74.494 * g + 112.439 * b) / 256
    cr = 128 + (112.439 * r - 94.154 * g - 18.285 * b) / 256
    return clamp(y, 16, 235), clamp(cr, 16, 240), clamp(cb, 16, 240), 255 - a


# --- Pixel code strings ------------------------------------------------------------

class _Bits:
    """Reads a run of bits, most significant first."""

    def __init__(self, data, start, end):
        self.data, self.bit, self.end = data, start * 8, end * 8

    def read(self, count):
        start = self.bit
        self.bit += count
        if self.bit > self.end:
            raise IndexError('past the end of the string')
        chunk = int.from_bytes(self.data[start >> 3:(start >> 3) + 3].ljust(3, b'\x00'), 'big')
        return (chunk >> (24 - (start & 7) - count)) & ((1 << count) - 1)

    def byte(self):
        """The byte after the string, its stuffing bits left out."""
        return (self.bit + 7) >> 3


def _string2(bits):
    """[(count, pixel code)] of a 2-bit/pixel code string."""
    runs = []
    while True:
        code = bits.read(2)
        if code:
            runs.append((1, code))
        elif bits.read(1):                          # switch_1: 3 to 10
            count = 3 + bits.read(3)
            runs.append((count, bits.read(2)))
        elif bits.read(1):                          # switch_2: one pixel of 0
            runs.append((1, 0))
        else:
            switch = bits.read(2)
            if switch == 0:
                return runs
            if switch == 1:
                runs.append((2, 0))
            elif switch == 2:
                count = 12 + bits.read(4)
                runs.append((count, bits.read(2)))
            else:
                count = 29 + bits.read(8)
                runs.append((count, bits.read(2)))


def _string4(bits):
    """[(count, pixel code)] of a 4-bit/pixel code string."""
    runs = []
    while True:
        code = bits.read(4)
        if code:
            runs.append((1, code))
        elif not bits.read(1):                      # switch_1 0: 3 to 9 of 0, or the end
            count = bits.read(3)
            if not count:
                return runs
            runs.append((count + 2, 0))
        elif not bits.read(1):                      # switch_2 0: 4 to 7
            count = 4 + bits.read(2)
            runs.append((count, bits.read(4)))
        else:
            switch = bits.read(2)
            if switch == 0:
                runs.append((1, 0))
            elif switch == 1:
                runs.append((2, 0))
            elif switch == 2:
                count = 9 + bits.read(4)
                runs.append((count, bits.read(4)))
            else:
                count = 25 + bits.read(8)
                runs.append((count, bits.read(4)))


def _string8(data, i, end):
    """([(count, pixel code)], the byte after it) of an 8-bit/pixel code
    string, which keeps to whole bytes."""
    runs = []
    while i < end:
        code = data[i]
        i += 1
        if code:
            runs.append((1, code))
            continue
        if i >= end:
            break
        flag = data[i]
        i += 1
        if not flag & 0x80:
            if not flag:
                return runs, i
            runs.append((flag, 0))
        elif i < end:
            runs.append((flag & 0x7F, data[i]))
            i += 1
    return runs, i


# The map tables a 2- or 4-bit string has in a deeper region, unless the
# object gives its own.
_MAP_2_TO_4 = (0x0, 0x7, 0x8, 0xF)
_MAP_2_TO_8 = (0x00, 0x77, 0x88, 0xFF)
_MAP_4_TO_8 = tuple(range(0x00, 0x100, 0x11))


def draw_field(block, region, x0, y0, field, non_modifying=False):
    """Draw a field's pixel-data sub-blocks into `region` ({'width',
    'height', 'depth', 'pixels'}), the object at (`x0`, `y0`) in it, from
    its line `field` (0 for the top field, 1 for the bottom) every other
    line."""
    width, height, depth, pixels = region['width'], region['height'], region['depth'], region['pixels']
    maps = {0x10: _MAP_2_TO_4 if depth == 4 else _MAP_2_TO_8 if depth == 8 else None,
            0x11: _MAP_4_TO_8 if depth == 8 else None}
    x, y = x0, y0 + field
    i, end = 0, len(block)
    while i < end:
        kind = block[i]
        i += 1
        if kind in (0x10, 0x11, 0x12):
            if kind == 0x12:
                runs, i = _string8(block, i, end)
            else:
                bits = _Bits(block, i, end)
                try:
                    runs = _string2(bits) if kind == 0x10 else _string4(bits)
                except IndexError:
                    return
                i = bits.byte()
            table = maps.get(kind)
            # A string deeper than its region keeps the bits the region has.
            mask = (1 << depth) - 1
            row = y * width
            for count, code in runs:
                if 0 <= y < height and x < width and not (non_modifying and code == 1):
                    colour = table[code] if table else code & mask
                    run = min(count, width - x)
                    pixels[row + x:row + x + run] = bytes((colour,)) * run
                x += count
        elif kind == 0x20 and i + 2 <= end:
            maps[0x10] = (block[i] >> 4, block[i] & 0x0F, block[i + 1] >> 4, block[i + 1] & 0x0F) \
                if depth == 4 else maps[0x10]
            i += 2
        elif kind == 0x21 and i + 4 <= end:
            maps[0x10] = tuple(block[i:i + 4]) if depth == 8 else maps[0x10]
            i += 4
        elif kind == 0x22 and i + 16 <= end:
            maps[0x11] = tuple(block[i:i + 16]) if depth == 8 else maps[0x11]
            i += 16
        elif kind == 0xF0:                          # end of the object's line
            x, y = x0, y + 2
        else:
            return


# --- Reading segments ------------------------------------------------------------

def segments_of(payload):
    """[(type, page id, data)] of a PES packet's data: the data_identifier
    and subtitle_stream_id (left out by some demuxers) and the segments up
    to the end marker (0xFF)."""
    found = []
    i = 2 if payload[:2] == b'\x20\x00' else 0
    while i + 6 <= len(payload) and payload[i] == 0x0F:
        kind = payload[i + 1]
        page = payload[i + 2] << 8 | payload[i + 3]
        length = payload[i + 4] << 8 | payload[i + 5]
        found.append((kind, page, payload[i + 6:i + 6 + length]))
        i += 6 + length
    return found


def first_page(packets):
    """The page of the first page composition in `packets`, for a stream
    whose descriptor names none; None for none."""
    for _pts, payload in packets:
        for kind, page, _data in segments_of(payload):
            if kind == PAGE_COMPOSITION:
                return page
    return None


class _Decoder:
    """What a decoder holds of a page: its display, regions, palettes and
    objects, and the subtitles shown so far."""

    def __init__(self):
        self.display = SD_DISPLAY
        self.window = (0, 0)
        self.shown = []
        self.pending = None                         # (seconds, time-out, [(region id, x, y)])
        self._reset()

    def _reset(self):
        self.regions = {}                           # id -> {'width', 'height', 'depth', 'clut', 'objects', ...}
        self.cluts = {}                             # id -> {2: [...], 4: [...], 8: [...]}
        self.texts = {}                             # object id -> its characters

    def feed(self, seconds, kind, data):
        if kind == PAGE_COMPOSITION and len(data) >= 2:
            self.end_display_set()
            state = (data[1] >> 2) & 0x03
            if state in (1, 2):                     # acquisition point, mode change: start again
                self._reset()
            placed = [(data[i], data[i + 2] << 8 | data[i + 3], data[i + 4] << 8 | data[i + 5])
                      for i in range(2, len(data) - 5, 6)]
            self.pending = (seconds, data[0], placed)
        elif kind == REGION_COMPOSITION and len(data) >= 10:
            self._region(data)
        elif kind == CLUT_DEFINITION and len(data) >= 2:
            self._clut(data)
        elif kind == OBJECT_DATA and len(data) >= 3:
            self._object(data)
        elif kind == DISPLAY_DEFINITION and len(data) >= 5:
            self.display = ((data[1] << 8 | data[2]) + 1, (data[3] << 8 | data[4]) + 1)
            self.window = (data[5] << 8 | data[6], data[9] << 8 | data[10]) if data[0] & 0x08 and len(data) >= 13 \
                else (0, 0)
        elif kind == END_OF_DISPLAY_SET:
            self.end_display_set()

    def _region(self, data):
        number, version, fill = data[0], data[1] >> 4, bool(data[1] & 0x08)
        width, height = data[2] << 8 | data[3], data[4] << 8 | data[5]
        depth = {1: 2, 2: 4, 3: 8}.get((data[6] >> 2) & 0x07, 8)
        fills = {8: data[8], 4: data[9] >> 4, 2: (data[9] >> 2) & 0x03}
        objects, i = [], 10
        while i + 6 <= len(data):
            kind = data[i + 2] >> 6
            objects.append((data[i] << 8 | data[i + 1], (data[i + 2] & 0x0F) << 8 | data[i + 3],
                            (data[i + 4] & 0x0F) << 8 | data[i + 5]))
            i += 8 if kind in (1, 2) else 6
        region = self.regions.get(number)
        if region is not None and region['version'] == version:
            return                                  # sent again, as it is
        if region is None or (region['width'], region['height'], region['depth']) != (width, height, depth):
            region = {'width': width, 'height': height, 'depth': depth,
                      'pixels': bytearray(bytes((fills[depth],)) * (width * height))}
            self.regions[number] = region
        elif fill:
            region['pixels'][:] = bytes((fills[depth],)) * (width * height)
        region.update(version=version, clut=data[7], objects=objects)

    def _clut(self, data):
        clut = self.cluts.setdefault(data[0], {depth: list(entries) for depth, entries in DEFAULT_CLUTS.items()})
        i = 2
        while i + 2 <= len(data):
            entry, flags = data[i], data[i + 1]
            if flags & 0x01:                        # full range: 8 bits each
                if i + 6 > len(data):
                    break
                y, cr, cb, t = data[i + 2:i + 6]
                i += 6
            else:
                if i + 4 > len(data):
                    break
                value = data[i + 2] << 8 | data[i + 3]
                y, cr, cb, t = (value >> 10) << 2, ((value >> 6) & 0x0F) << 4, ((value >> 2) & 0x0F) << 4, \
                    (value & 0x03) << 6
                i += 4
            colour = ycrcb_to_rgba(y, cr, cb, t)
            for flag, depth in ((0x80, 2), (0x40, 4), (0x20, 8)):
                if flags & flag and entry < len(clut[depth]):
                    clut[depth][entry] = colour

    def _object(self, data):
        number = data[0] << 8 | data[1]
        method, non_modifying = (data[2] >> 2) & 0x03, bool(data[2] & 0x02)
        if method == 1 and len(data) >= 4:          # a string of characters
            codes = [data[4 + 2 * k] << 8 | data[5 + 2 * k] for k in range(data[3]) if 5 + 2 * k < len(data)]
            self.texts[number] = ''.join(chr(code) for code in codes if code >= 0x20)
            return
        if method != 0 or len(data) < 7:
            return
        top_length, bottom_length = data[3] << 8 | data[4], data[5] << 8 | data[6]
        top = data[7:7 + top_length]
        # No bottom field: the top field's lines are the bottom's too.
        bottom = data[7 + top_length:7 + top_length + bottom_length] if bottom_length else top
        for region in self.regions.values():
            for found, x, y in region['objects']:
                if found == number:
                    draw_field(top, region, x, y, 0, non_modifying)
                    draw_field(bottom, region, x, y, 1, non_modifying)

    def end_display_set(self):
        """Show the display set begun: it ends the subtitle before it."""
        if self.pending is None:
            return
        seconds, timeout, placed = self.pending
        self.pending = None
        previous = self.shown[-1] if self.shown else None
        if previous is not None and previous.end is None:
            previous.end = seconds
        picture = self._compose(seconds, placed)
        if picture is None:
            return
        if timeout:
            picture.limit = seconds + timeout
        if previous is not None and previous.end == seconds and _same(previous, picture):
            previous.end = None                     # a page sent again, as it was: still shown
            previous.limit = getattr(picture, 'limit', None)
            return
        self.shown.append(picture)

    def _compose(self, seconds, placed):
        """The page's regions drawn into one picture, their palettes into
        one; None when nothing in them shows."""
        drawn, words = [], []
        for number, x, y in placed:
            region = self.regions.get(number)
            if region is None or not region['width'] or not region['height']:
                continue
            clut = self.cluts.get(region['clut']) or DEFAULT_CLUTS
            drawn.append((x + self.window[0], y + self.window[1], region, clut[region['depth']]))
            words += [self.texts[found] for found, _x, _y in region['objects'] if self.texts.get(found)]
        if not drawn:
            return None
        left = min(d[0] for d in drawn)
        top = min(d[1] for d in drawn)
        width = max(d[0] + d[2]['width'] for d in drawn) - left
        height = max(d[1] + d[2]['height'] for d in drawn) - top
        canvas = bytearray(width * height)
        colours, index_of = [(0, 0, 0, 0)], {(0, 0, 0, 0): 0}
        for x, y, region, palette in drawn:
            # Each region's codes as entries of the one palette.
            table = bytearray(256)
            for code in set(region['pixels']):
                colour = palette[code] if code < len(palette) else (0, 0, 0, 0)
                colour = colour if colour[3] else (0, 0, 0, 0)
                if colour not in index_of:
                    if len(colours) < 256:
                        index_of[colour] = len(colours)
                        colours.append(colour)
                    else:
                        index_of[colour] = min(range(len(colours)), key=lambda k: sum(
                            (a - b) ** 2 for a, b in zip(colours[k], colour)))
                table[code] = index_of[colour]
            pixels = bytes(region['pixels']).translate(bytes(table))
            w = region['width']
            for row in range(region['height']):
                start = (y - top + row) * width + (x - left)
                canvas[start:start + w] = pixels[row * w:(row + 1) * w]
        if len(colours) == 1 and not words:
            return None                             # regions with nothing in them
        picture = Picture(seconds, None, left, top, width, height, bytes(canvas), colours, False,
                          self.display[0], self.display[1], ' '.join(words))
        return picture

    def finish(self, default=5.0):
        self.end_display_set()
        for current, following in zip(self.shown, self.shown[1:] + [None]):
            if current.end is None:
                current.end = following.start if following else current.start + default
            limit = getattr(current, 'limit', None)
            if limit is not None:
                current.end = min(current.end, limit)
        return [picture for picture in self.shown if picture.end > picture.start]


def _same(one, other):
    return (one.x, one.y, one.width, one.height, one.indices, one.colours, one.text) == \
        (other.x, other.y, other.width, other.height, other.indices, other.colours, other.text)


def decode(packets, page=None, ancillary=None, origin=None):
    """The subtitles of composition page `page` (the first page any page
    composition names when None), with the segments of page `ancillary`, in
    `packets`, [(pts in 90 kHz ticks, PES packet data)]: [Picture], timed
    in seconds from `origin` (a PTS; the first packet's when None)."""
    if page is None:
        page = first_page(packets)
    pages = {page, ancillary if ancillary is not None else page}
    decoder = _Decoder()
    base, previous, wraps = None, None, 0
    for pts, payload in packets:
        if previous is not None and pts < previous - _PTS_WRAP // 2:
            wraps += 1
        previous = pts
        if base is None:
            base = origin if origin is not None else pts
        seconds = round((pts + wraps * _PTS_WRAP - base) / 90000.0, 3)
        for kind, segment_page, data in segments_of(payload):
            if segment_page in pages:
                decoder.feed(seconds, kind, data)
    return decoder.finish()


# --- Transport stream ------------------------------------------------------------

def _descriptor_entries(body):
    """The services of a subtitling descriptor's body: [{'language',
    'type', 'page', 'ancillary'}]."""
    return [{'language': body[start:start + 3].decode('latin-1', 'replace').strip('\x00 '),
             'type': body[start + 3], 'page': body[start + 4] << 8 | body[start + 5],
             'ancillary': body[start + 6] << 8 | body[start + 7]}
            for start in range(0, len(body) - 7, 8)]


def _pmt_streams(section):
    """{pid: [service]} for the elementary streams of a PMT section, the
    services a DVB subtitle stream's descriptor lists; None for others."""
    streams = {}
    if len(section) < 12 or section[0] != 0x02:
        return streams
    end = 3 + (((section[1] & 0x0F) << 8) | section[2]) - 4
    position = 12 + (((section[10] & 0x0F) << 8) | section[11])
    while position + 5 <= end:
        pid = ((section[position + 1] & 0x1F) << 8) | section[position + 2]
        length = ((section[position + 3] & 0x0F) << 8) | section[position + 4]
        info = section[position + 5:position + 5 + length]
        services, index = None, 0
        while index + 2 <= len(info):
            tag, size = info[index], info[index + 1]
            if tag == 0x59 and section[position] == 0x06:
                services = (services or []) + _descriptor_entries(info[index + 2:index + 2 + size])
            index += 2 + size
        streams[pid] = services
        position += 5 + length
    return streams


def _pmt_sections(stream, layout, limit=None, progress=None, stopped=None):
    """(pid, start, payload) of each packet, and each complete PMT's
    streams in between as (None, streams, None)."""
    pmt_pids, sections, read = set(), {}, 0
    for pid, start, payload in teletext._packets(stream, *layout, progress=progress, stopped=stopped):
        read += teletext.PACKET
        if limit is not None and read > limit:
            return
        if pid == 0 or pid in pmt_pids:
            if start:
                sections[pid] = bytearray(teletext._section_parts(payload, True))
            elif pid in sections:
                sections[pid] += payload
            section = sections.get(pid)
            if section and len(section) >= 3 and len(section) >= 3 + (((section[1] & 0x0F) << 8) | section[2]):
                if pid == 0 and section[0] == 0x00:
                    end = 3 + (((section[1] & 0x0F) << 8) | section[2]) - 4
                    for index in range(8, end - 3, 4):
                        if (section[index] << 8) | section[index + 1]:
                            pmt_pids.add(((section[index + 2] & 0x1F) << 8) | section[index + 3])
                elif section[0] == 0x02:
                    yield None, _pmt_streams(bytes(section)), None
                del sections[pid]
            continue
        yield pid, start, payload


def _layout(stream):
    layout = teletext._packet_layout(stream.read(204 * 6))
    stream.seek(0)
    return layout


def streams(path, limit=8 * 1024 * 1024):
    """The DVB subtitle streams of the transport stream at `path`, from the
    PMT in its first `limit` bytes: {pid: [{'language', 'type', 'page',
    'ancillary'}]}."""
    found = {}
    with open(path, 'rb') as stream:
        layout = _layout(stream)
        if layout is None:
            return {}
        for pid, listed, _payload in _pmt_sections(stream, layout, limit):
            if pid is None:
                found.update({key: services for key, services in listed.items() if services is not None})
                if found:
                    break
    return found


def scan(path, progress=None, stopped=None):
    """What the transport stream at `path` holds of DVB subtitles: ({pid:
    [service]}, {pid: [(pts, PES packet data)]}, origin), the services each
    stream's descriptor lists, the PES packets of each, and the earliest
    PTS of any of the file's streams (where FFmpeg counts its times from),
    in 90 kHz ticks."""
    listed, pending, packets, first = {}, {}, {}, {}

    def take(pid, pes):
        pts = teletext._pes_pts(pes)
        if pts is None:
            return
        first.setdefault(pid, pts)
        packets.setdefault(pid, []).append((pts, teletext._pes_payload(pes)))

    with open(path, 'rb') as stream:
        layout = _layout(stream)
        if layout is None:
            return {}, {}, None
        for pid, start, payload in _pmt_sections(stream, layout, progress=progress, stopped=stopped):
            if pid is None:
                for key, services in start.items():
                    listed.setdefault(key, services)
                continue
            if pid not in listed:
                continue
            if listed[pid] is None:
                # Another stream: its first PTS, for where times count from.
                if start and pid not in first:
                    pts = teletext._pes_pts(payload)
                    if pts is not None:
                        first[pid] = pts
                continue
            if start:
                if pending.get(pid):
                    take(pid, bytes(pending[pid]))
                pending[pid] = bytearray(payload)
            elif pid in pending:
                pending[pid] += payload
    for pid, pes in pending.items():
        if pes:
            take(pid, bytes(pes))
    origin = min(first.values()) if first else None
    return {pid: services for pid, services in listed.items() if services is not None}, packets, origin


def read(path, pid=None, page=None, progress=None, stopped=None):
    """(pictures, {'language', 'type', 'page', 'pid'}) of a DVB subtitle
    service in the transport stream at `path`: composition page `page` in
    the stream `pid`, or the first service any descriptor lists. Times
    count from the earliest start of the file's streams, as FFmpeg counts
    them. Raises ValueError for a file with no DVB subtitles."""
    listed, packets, origin = scan(path, progress=progress, stopped=stopped)
    page = int(page) if page not in (None, '') else None
    if pid is None or not packets.get(pid):
        candidates = [found for found, services in listed.items()
                      if page is None or any(service['page'] == page for service in services)]
        pid = next((found for found in candidates + list(packets) if packets.get(found)), None)
    if pid is None:
        raise ValueError('There are no DVB subtitles in this file')
    services = listed.get(pid) or []
    service = next((s for s in services if page is None or s['page'] == page), None) or \
        {'language': '', 'type': 0x10, 'page': page, 'ancillary': None}
    pictures = decode(packets[pid], service['page'], service['ancillary'], origin)
    return pictures, {'language': service['language'], 'type': service['type'], 'page': service['page'], 'pid': pid}


# --- Writing: colours ------------------------------------------------------------

def quantise(rgba, entries=256):
    """(indices, palette) of an RGBA picture (a numpy array, height x width
    x 4, not premultiplied) in a palette of `entries` colours, entry 0 no
    colour at all for what does not show: palette indices, row after row,
    and [(r, g, b, a)]. Colours beyond what the palette holds are brought
    together by median cut over the picture's own colours, weighted by how
    many pixels have each, so the antialiased edges of the letters keep as
    many steps as there is room for."""
    import numpy
    height, width = rgba.shape[:2]
    flat = rgba.reshape(-1, 4)
    shows = flat[:, 3] >= 8
    indices = numpy.zeros(width * height, numpy.uint8)
    if not shows.any():
        return indices.tobytes(), [(0, 0, 0, 0)]
    colours, inverse, counts = numpy.unique(flat[shows], axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.reshape(-1)
    room = entries - 1
    if len(colours) <= room:
        groups = numpy.arange(len(colours))
        palette = [tuple(int(v) for v in colour) for colour in colours]
    else:
        values = colours.astype(numpy.float64)
        boxes = [numpy.arange(len(colours))]
        while len(boxes) < room:
            spans = [(values[box].max(axis=0) - values[box].min(axis=0)).max() if len(box) > 1 else -1
                     for box in boxes]
            widest = int(numpy.argmax(spans))
            if spans[widest] <= 0:
                break
            box = boxes.pop(widest)
            channel = int(numpy.argmax(values[box].max(axis=0) - values[box].min(axis=0)))
            box = box[numpy.argsort(values[box, channel], kind='stable')]
            weight = numpy.cumsum(counts[box])
            cut = int(numpy.searchsorted(weight, weight[-1] / 2))
            cut = min(max(cut, 1), len(box) - 1)
            boxes += [box[:cut], box[cut:]]
        groups = numpy.zeros(len(colours), numpy.int64)
        palette = []
        for number, box in enumerate(boxes):
            groups[box] = number
            mean = (values[box] * counts[box, None]).sum(axis=0) / counts[box].sum()
            palette.append(tuple(int(round(v)) for v in mean))
    indices[shows] = (groups[inverse] + 1).astype(numpy.uint8)
    return indices.tobytes(), [(0, 0, 0, 0)] + palette


def png_rgba(path):
    """A PNG's pixels as a numpy array, height x width x 4, not
    premultiplied."""
    import numpy
    from PySide6.QtGui import QImage
    image = QImage(str(path))
    if image.isNull():
        raise OSError(f'Could not read {path}')
    image = image.convertToFormat(QImage.Format_RGBA8888)
    width, height = image.width(), image.height()
    rows = numpy.frombuffer(image.constBits(), numpy.uint8, count=image.sizeInBytes())
    return rows.reshape(height, image.bytesPerLine())[:, :width * 4].reshape(height, width, 4).copy()


# --- Writing: segments -----------------------------------------------------------

class _BitWriter:
    def __init__(self):
        self.value, self.count = 0, 0

    def put(self, value, bits):
        self.value = self.value << bits | (value & ((1 << bits) - 1))
        self.count += bits

    def bytes(self):
        """What was written, with stuffing bits (0) to a whole byte."""
        pad = -self.count % 8
        return (self.value << pad).to_bytes((self.count + pad) // 8, 'big')


def _runs(row):
    runs, start = [], 0
    for i in range(1, len(row) + 1):
        if i == len(row) or row[i] != row[start]:
            runs.append((i - start, row[start]))
            start = i
    return runs


def encode_string8(row):
    """A line of 8-bit pixel codes as an 8-bit/pixel code string, its end
    included."""
    out = bytearray()
    for count, code in _runs(row):
        while count:
            if code == 0:
                step = min(count, 127)
                out += bytes((0, step))
            elif count >= 3:
                step = min(count, 127)
                out += bytes((0, 0x80 | step, code))
            else:
                step = 1
                out.append(code)
            count -= step
    return bytes(out + b'\x00\x00')


def encode_string4(row):
    """A line of 4-bit pixel codes as a 4-bit/pixel code string, its end
    and its stuffing bits included."""
    bits = _BitWriter()
    for count, code in _runs(row):
        while count:
            if code == 0:
                if count >= 25:
                    step = min(count, 280)
                    bits.put(0, 4), bits.put(0b1111, 4), bits.put(step - 25, 8), bits.put(0, 4)
                elif count >= 10:
                    step = count
                    bits.put(0, 4), bits.put(0b1110, 4), bits.put(step - 9, 4), bits.put(0, 4)
                elif count >= 3:
                    step = count
                    bits.put(0, 4), bits.put(0, 1), bits.put(step - 2, 3)
                else:
                    step = count
                    bits.put(0, 4), bits.put(0b1100 | (step - 1), 4)
            elif count >= 25:
                step = min(count, 280)
                bits.put(0, 4), bits.put(0b1111, 4), bits.put(step - 25, 8), bits.put(code, 4)
            elif count >= 9:
                step = count
                bits.put(0, 4), bits.put(0b1110, 4), bits.put(step - 9, 4), bits.put(code, 4)
            elif count >= 4:
                step = min(count, 7)
                bits.put(0, 4), bits.put(0b10, 2), bits.put(step - 4, 2), bits.put(code, 4)
            else:
                step = 1
                bits.put(code, 4)
            count -= step
    bits.put(0, 8)                                  # 0000, switch_1 0, end of string
    return bits.bytes()


def _object_fields(indices, width, rows, depth):
    """The top and bottom fields of an object of `rows` (the picture's
    lines it holds), as pixel-data sub-blocks."""
    kind, encode = (0x12, encode_string8) if depth == 8 else (0x11, encode_string4)
    fields = []
    for parity in (0, 1):
        out = bytearray()
        for row in rows[parity::2]:
            out.append(kind)
            out += encode(indices[row * width:(row + 1) * width])
            out.append(0xF0)
        fields.append(bytes(out))
    return fields


def _segment(kind, page, data):
    return bytes((0x0F, kind, page >> 8, page & 0xFF, len(data) >> 8, len(data) & 0xFF)) + data


# The most an object's segment holds: what its length field can say, less
# what keeps it, with a PES header and its markers, in one PES packet.
_OBJECT_MOST = 0xFFFF - 32


def encode_picture(picture, depth=8):
    """A drawn subtitle ({'x', 'y', 'width', 'height', 'indices',
    'palette'}) as what a region needs: [(y in the region, the object's
    field lengths and fields)], an object for each band of lines, as many
    as keep each within a segment's length and a PES packet's."""
    width, height, indices = picture['width'], picture['height'], picture['indices']
    bands, pending = [], [(0, height)]
    while pending:
        top, bottom = pending.pop(0)
        upper, lower = _object_fields(indices, width, list(range(top, bottom)), depth)
        if len(upper) + len(lower) + 8 > _OBJECT_MOST and bottom - top > 1:
            middle = (top + bottom) // 2
            pending[:0] = [(top, middle), (middle, bottom)]
            continue
        body = len(upper).to_bytes(2, 'big') + len(lower).to_bytes(2, 'big') + upper + lower
        if (len(upper) + len(lower)) % 2:
            body += b'\x0f'                         # 8_stuff_bits, to a whole word
        bands.append((top, body))
    return bands


def display_set(page, pictures, display, version, timeout, depth=8):
    """The segments of a display set showing `pictures` (each encoded:
    'x', 'y', 'width', 'height', 'palette', 'bands', and its own 'depth'
    where it is not `depth`) on composition page `page`, in a mode change:
    [segment bytes]. None of them, a page with no regions, in the normal
    case, takes what is shown away."""
    version &= 0x0F
    out = [_segment(DISPLAY_DEFINITION, page, bytes((0x07,)) +
                    (display[0] - 1).to_bytes(2, 'big') + (display[1] - 1).to_bytes(2, 'big'))]
    state = 2 if pictures else 0
    placed = b''.join(bytes((number, 0xFF)) + picture['x'].to_bytes(2, 'big') + picture['y'].to_bytes(2, 'big')
                      for number, picture in enumerate(pictures))
    out.append(_segment(PAGE_COMPOSITION, page, bytes((min(255, max(0, timeout)), version << 4 | state << 2 | 0x03))
                        + placed))
    for number, picture in enumerate(pictures):
        level = 3 if picture.get('depth', depth) == 8 else 2
        flag = 0x20 if picture.get('depth', depth) == 8 else 0x40
        objects = b''.join(((number << 8) | band).to_bytes(2, 'big') + bytes((0x00, 0x00, 0xF0 | (top >> 8), top & 0xFF))
                           for band, (top, _body) in enumerate(picture['bands']))
        out.append(_segment(REGION_COMPOSITION, page, bytes((number, version << 4 | 0x08 | 0x07)) +
                            picture['width'].to_bytes(2, 'big') + picture['height'].to_bytes(2, 'big') +
                            bytes((level << 5 | level << 2 | 0x03, number, 0x00, 0x03)) + objects))
        entries = b''.join(bytes((entry, flag | 0x1F)) + bytes(rgba_to_ycrcb(*colour))
                           for entry, colour in enumerate(picture['palette']))
        out.append(_segment(CLUT_DEFINITION, page, bytes((number, version << 4 | 0x0F)) + entries))
    for number, picture in enumerate(pictures):
        for band, (_top, body) in enumerate(picture['bands']):
            object_id = (number << 8) | band
            # Coded as pixels (method 0), every colour drawn; a reserved bit.
            out.append(_segment(OBJECT_DATA, page, object_id.to_bytes(2, 'big') + bytes((version << 4 | 0x01,)) + body))
    out.append(_segment(END_OF_DISPLAY_SET, page, b''))
    return out


# The most a PES packet holds of a display set's segments.
_PES_ROOM = 0xFFFF - 3 - 5 - 3


def _pes(segments, pts):
    """The PES packets of a display set at `pts` (90 kHz ticks), each its
    data_identifier, subtitle_stream_id, segments and end marker: one, or
    as many as keep each within a PES packet's length (which FFmpeg's
    decoder does not follow: `fitted` keeps to one wherever it can)."""
    timestamp = bytes((0x21 | ((pts >> 29) & 0x0E), (pts >> 22) & 0xFF, 0x01 | ((pts >> 14) & 0xFE),
                       (pts >> 7) & 0xFF, 0x01 | ((pts << 1) & 0xFE)))
    packets, current = [], []
    for segment in segments:
        if current and sum(map(len, current)) + len(segment) > _PES_ROOM:
            packets.append(current)
            current = []
        current.append(segment)
    packets.append(current)
    out = []
    for group in packets:
        body = b'\x20\x00' + b''.join(group) + b'\xff'
        header = b'\x84\x80' + bytes((len(timestamp),)) + timestamp
        out.append(b'\x00\x00\x01\xbd' + (len(header) + len(body)).to_bytes(2, 'big') + header + body)
    return out


def fewer_colours(picture):
    """An encoded picture again in 16 colours, coded with 4-bit strings,
    which take far fewer bytes than 256 colours' antialiasing."""
    import numpy
    palette = numpy.array(picture['palette'], numpy.uint8)
    rgba = palette[numpy.frombuffer(picture['indices'], numpy.uint8)].reshape(picture['height'], picture['width'], 4)
    indices, palette = quantise(rgba, 16)
    reduced = dict(picture, indices=indices, palette=palette, depth=4)
    reduced['bands'] = encode_picture(reduced, 4)
    return reduced


def fitted(page, shown, display, version, timeout, depth=8):
    """The segments of a display set (as `display_set`), the pictures in
    it brought down to 16 colours (`fewer_colours`) when in 256 they would
    not fit in one PES packet."""
    segments = display_set(page, shown, display, version, timeout, depth)
    if sum(map(len, segments)) > _PES_ROOM and any(picture.get('depth', depth) == 8 for picture in shown):
        for number, picture in enumerate(shown):
            if picture.get('depth', depth) == 8:
                if 'reduced' not in picture:
                    picture['reduced'] = fewer_colours(picture)
                shown[number] = picture['reduced']
        segments = display_set(page, shown, display, version, timeout, depth)
    return segments


SUBTITLE_PID = 0x0102


def subtitling_type(display, hard_of_hearing=False):
    """The descriptor's type for subtitles for `display`: 0x10 (any
    aspect ratio), or 0x14 for an HD one; 0x20 and 0x24 for the hard of
    hearing."""
    kind = 0x14 if display[0] > SD_DISPLAY[0] or display[1] > SD_DISPLAY[1] else 0x10
    return kind + (0x10 if hard_of_hearing else 0)


def transport_stream(tracks, display=HD_DISPLAY, start=0.0, depth=8):
    """The bytes of a transport stream with a DVB subtitle stream for each
    of `tracks`: [{'language' (ISO 639-2, 'eng'), 'pictures' (each drawn
    subtitle's 'start', 'end', 'x', 'y', 'width', 'height', 'indices',
    'palette', from `quantise`), and 'hard_of_hearing'}], on `display`.
    Each changes what it shows when a subtitle starts or ends, those shown
    together in regions of their own, and shows nothing at `start` itself
    (seconds the times count from), so the stream's own start is the
    video's."""
    counters = {}
    stream_info = b''
    for number, track in enumerate(tracks):
        page = number + 1
        descriptor = track['language'][:3].encode('latin-1', 'replace').ljust(3, b' ') + bytes((
            subtitling_type(display, track.get('hard_of_hearing')),)) + page.to_bytes(2, 'big') + page.to_bytes(2, 'big')
        pid = SUBTITLE_PID + number
        stream_info += bytes((0x06, 0xE0 | (pid >> 8), pid & 0xFF, 0xF0, 2 + len(descriptor), 0x59,
                              len(descriptor))) + descriptor
    pat = b'\x00' + teletext._section(0x00, 1, bytes((0, 1, 0xE0 | (teletext.PMT_PID >> 8), teletext.PMT_PID & 0xFF)))
    pmt = b'\x00' + teletext._section(0x02, 1, bytes((0xE0 | (teletext.PCR_PID >> 8), teletext.PCR_PID & 0xFF,
                                                      0xF0, 0x00)) + stream_info)
    events = []
    for number, track in enumerate(tracks):
        encoded = [dict(picture, bands=encode_picture(picture, depth)) for picture in track.get('pictures') or []
                   if picture['width'] and picture['height']]
        times = sorted({0.0} | {round(float(p['start']), 3) for p in encoded} | {round(float(p['end']), 3) for p in encoded})
        for when in times:
            events.append((when, number, [p for p in encoded if float(p['start']) <= when < float(p['end'])]))
    events.sort(key=lambda event: (event[0], event[1]))
    versions, out = {}, []
    for when, number, shown in events:
        # Shown a second past the last of them to end, at most: a decoder
        # that misses the page taking them away does not show them for ever.
        timeout = int(max(float(p['end']) - when for p in shown) + 0.999) + 1 if shown else 0
        version = versions[number] = (versions.get(number, -1) + 1) & 0x0F
        segments = fitted(number + 1, shown, display, version, timeout, depth)
        pts = int(round((max(0.0, when) + start) * 90000)) % _PTS_WRAP
        out += teletext._ts_packets(0, pat, counters) + teletext._ts_packets(teletext.PMT_PID, pmt, counters)
        out.append(teletext._pcr_packet(pts))
        for pes in _pes(segments, pts):
            out += teletext._ts_packets(SUBTITLE_PID + number, pes, counters)
    return b''.join(out)


def draw_tracks(tracks, display=HD_DISPLAY, depth=8, draw=None, folder=None):
    """`tracks` ([{'segments', 'language' (Subtitld's code)}]) as
    `transport_stream` takes them: each subtitle drawn as the preview draws
    it, by `draw` (interface.subtitle_pictures.draw), on a picture of
    `display`'s size, and its colours brought into a palette of 256 (8-bit)
    or 16 (4-bit) entries."""
    from subtitld.modules import usf
    if draw is None:
        from subtitld.interface.subtitle_pictures import draw
    import shutil
    folder = folder or tempfile.mkdtemp(prefix='subtitld_dvbsub_')
    made = []
    try:
        for number, track in enumerate(tracks):
            pictures = []
            for drawn in draw(track.get('segments') or [], display, folder, stem=f'{number}_'):
                rgba = png_rgba(drawn['path'])
                indices, palette = quantise(rgba, 256 if depth == 8 else 16)
                x = max(0, min(int(drawn['x']), display[0] - drawn['width']))
                y = max(0, min(int(drawn['y']), display[1] - drawn['height']))
                pictures.append({'start': drawn['start'], 'end': drawn['end'], 'x': x, 'y': y,
                                 'width': min(drawn['width'], display[0]), 'height': min(drawn['height'], display[1]),
                                 'indices': indices, 'palette': palette, 'alt': drawn.get('alt', '')})
                if (pictures[-1]['width'], pictures[-1]['height']) != (drawn['width'], drawn['height']):
                    # Larger than the display: its top left part, which is all a decoder shows.
                    width, full = pictures[-1]['width'], drawn['width']
                    pictures[-1]['indices'] = b''.join(indices[row * full:row * full + width]
                                                       for row in range(pictures[-1]['height']))
            made.append({'language': usf.iso639_2(track.get('language')) if track.get('language') else 'und',
                         'hard_of_hearing': bool(track.get('hard_of_hearing')), 'pictures': pictures})
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return made


def display_for(width=None, height=None):
    """The display subtitles for a video `width` x `height` are drawn for:
    720x576 for a standard definition one, 1920x1080 for any other (or
    none), which the decoder scales to the picture."""
    if width and height and int(width) <= SD_DISPLAY[0] and int(height) <= SD_DISPLAY[1]:
        return SD_DISPLAY
    return HD_DISPLAY


def mux_command(video, subtitles, output, ffmpeg='ffmpeg'):
    """The FFmpeg command that writes `video` out as the transport stream
    `output`, its picture and sound copied, with the DVB subtitle streams
    of the transport stream `subtitles`."""
    return [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y', '-nostdin', '-i', str(video),
            '-i', str(subtitles), '-map', '0:v?', '-map', '0:a?', '-map', '1:s',
            '-c', 'copy', '-f', 'mpegts', str(output)]


def write_video(video, output, tracks, width=None, height=None, depth=8, ffmpeg='ffmpeg', startupinfo=None,
                draw=None):
    """Write `video` out as the transport stream `output` with a DVB
    subtitle stream for each of `tracks` ([{'segments', 'language'
    (Subtitld's code)}]), drawn for the display a video `width` x `height`
    has. Raises ValueError for no tracks and RuntimeError with what FFmpeg
    said when it cannot write, or when this build cannot draw."""
    if not tracks:
        raise ValueError('There are no subtitles to write into the video')
    display = display_for(width, height)
    drawn = draw_tracks(tracks, display, depth, draw)
    handle, path = tempfile.mkstemp(prefix='subtitld_dvbsub_', suffix='.ts')
    os.close(handle)
    try:
        with open(path, 'wb') as stream:
            stream.write(transport_stream(drawn, display, start=1.4, depth=depth))
        run = subprocess.run(mux_command(video, path, output, ffmpeg), capture_output=True, startupinfo=startupinfo,
                             check=False)
        if run.returncode != 0 or not os.path.isfile(output) or not os.path.getsize(output):
            said = (run.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
            raise RuntimeError(said[0] if said else 'FFmpeg could not write the video')
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return output
