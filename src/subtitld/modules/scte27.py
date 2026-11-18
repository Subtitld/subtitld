"""SCTE 27 subtitles: the bitmap subtitles of North American cable, in an
MPEG transport stream, read for an OCR add-on and written into one.

SCTE 27 itself is sold by SCTE and was not at hand. What is here follows
the decoder VLC has for it (modules/codec/scte27.c) and VLC's transport
stream demuxer (modules/demux/mpeg/ts_psi.c and ts_scte.c), which say the
same of every field, and FFmpeg for nothing: FFmpeg neither demuxes nor
decodes it.

A subtitle stream is stream_type 0x82 in the PMT. It carries private
sections, not PES packets: table_id 0xC6, a section length of 12 bits,
then a byte whose bit 6 says a message comes in segments and whose low
six bits are the protocol version (0); with segments, a table extension
(the message's id), the last segment's number and this one's (12 bits
each); then the message, or the segment of it, and a CRC-32. A
subtitle_message is:

    ISO_639_language_code   24
    pre_clear_display        1   clear what is shown first
    immediate                1   show it as it arrives, not at its time
    reserved                 1
    display_standard         5   0 720x480 at 29.97, 1 720x576 at 25,
                                 2 1280x720 at 59.94, 3 1920x1080 at 59.94
    display_in_PTS          32   when to show it, the PTS's low 32 bits
    subtitle_type            4   1: a simple bitmap
    reserved                 1
    display_duration        11   for how many frames of the standard
    block_length            16   the simple bitmap's bytes

and a simple bitmap: 5 reserved bits, background_style (1, framed), an
outline_style (2: none, outline, drop shadow, reserved), the character
colour (Y 5 bits, opaque 1, Cr 5, Cb 5), the bitmap's top left and bottom
right corners (12 bits each, across then down); for a framed one the
frame's corners and its colour; for an outline its thickness (after 4
reserved bits) and colour; for a shadow how far right and down (4 bits
each) and its colour; for the reserved style 24 bits; then the bitmap's
compressed length (16) and the bitmap, one bit a pixel run-length coded
line after line:

    1 RRR FFFFF   RRR pixels on (0 for 8), then FFFFF off (0 for 32)
    01 FFFFFF     FFFFFF pixels off (0 for 64)
    001 RRRR      RRRR pixels on (0 for 16)
    00001         the rest of the line off

Reading gives picture_subtitles.Picture for each message: the frame
filled with its colour (none when not framed), the outline drawn as a
disc around each pixel on, or the shadow as the bitmap moved, then the
characters on top, as VLC draws them, on the display of the message's
standard. It is shown from its display_in_PTS (or, immediate, from the
last time the file gave before it) for its duration, or until the next
message that clears the display. Colours are BT.601 (modules.dvbsub), not
opaque meaning half seen. Times count from the earliest start of the
file's streams, as FFmpeg counts them for the others.

Writing draws each subtitle as the preview draws it
(subtitle_pictures.draw) and keeps one colour of it: the pixels near its
brightest common colour are the characters, coded in runs, and what else
shows beside them is an outline of the thickness and colour it has, or,
when it fills most of the picture, the frame behind the text. A message
for each subtitle is sent half a second before it shows, clearing what
was shown, for its duration (a message again every 2047 frames, the most
one says, for a longer one), on 1920x1080 (720x480 for a standard
definition video). `write_video` puts the stream into a transport stream
of the video, its picture and sound copied by FFmpeg, the sections laid
in by their time and the PMT given the stream, since FFmpeg drops a
stream it does not know.
"""

import os
import subprocess
import tempfile

from subtitld.modules import dvbsub, teletext
from subtitld.modules.picture_subtitles import Picture

STREAM_TYPE = 0x82
TABLE_ID = 0xC6
SUBTITLE_PID = 0x0140
_PTS_WRAP = 1 << 33
# The display standards: (width, height, frame rate).
STANDARDS = {0: (720, 480, 30000 / 1001), 1: (720, 576, 25.0), 2: (1280, 720, 60000 / 1001),
             3: (1920, 1080, 60000 / 1001)}
_LONGEST = 2047                                     # frames a message can say it lasts
_LEAD = 0.5                                         # seconds a message is sent before it shows
_SECTION_ROOM = 4093 - 1 - 4                        # a section's message bytes, unsegmented
_SEGMENT_ROOM = 4093 - 1 - 5 - 4


# --- Bits ----------------------------------------------------------------------

class _Bits:
    """Bits, most significant first."""

    def __init__(self, data):
        self.data, self.bit = data, 0

    def read(self, count):
        value = 0
        for _ in range(count):
            byte = self.bit >> 3
            if byte >= len(self.data):
                raise EOFError
            value = (value << 1) | ((self.data[byte] >> (7 - (self.bit & 7))) & 1)
            self.bit += 1
        return value


class _BitWriter:
    def __init__(self):
        self.bits = []

    def write(self, value, count):
        self.bits.extend((value >> shift) & 1 for shift in range(count - 1, -1, -1))

    def bytes(self):
        out = bytearray((len(self.bits) + 7) // 8)
        for index, bit in enumerate(self.bits):
            if bit:
                out[index >> 3] |= 0x80 >> (index & 7)
        return bytes(out)


def _read_color(bits):
    y, opaque, cr, cb = bits.read(5) << 3, bits.read(1), bits.read(5) << 3, bits.read(5) << 3
    return dvbsub.ycrcb_to_rgba(y, cr, cb, 0 if opaque else 127)


def _write_color(writer, rgba):
    y, cr, cb, t = dvbsub.rgba_to_ycrcb(*rgba)
    writer.write(y >> 3 if rgba[3] else 0, 5)
    writer.write(1 if t < 64 else 0, 1)
    writer.write(cr >> 3, 5)
    writer.write(cb >> 3, 5)


# --- Reading -------------------------------------------------------------------

def decode_bitmap(data, width, height):
    """The pixels of a compressed bitmap: bytes, 1 on and 0 off, row after
    row (VLC's reading: a run passes from one line to the next)."""
    size = width * height
    pixels = bytearray(size)
    bits = _Bits(data)
    position = 0
    try:
        while position < size:
            on = off = 0
            if bits.read(1):
                on, off = bits.read(3) or 8, bits.read(5) or 32
            elif bits.read(1):
                off = bits.read(6) or 64
            elif bits.read(1):
                on = bits.read(4) or 16
            elif bits.read(2) == 1:
                position = min((position // width + 1) * width, size)
                continue
            end = min(size, position + on)
            pixels[position:end] = b'\x01' * (end - position)
            position = min(size, end + off)
    except EOFError:
        pass
    return bytes(pixels)


def simple_bitmap(data):
    """A simple bitmap as a Picture's place and pixels: (x, y, width,
    height, indices, colours), the colours [frame, character, outline,
    shadow]; None for one whose corners make no picture."""
    bits = _Bits(data)
    bits.read(5)
    framed, outline_style = bits.read(1), bits.read(2)
    character = _read_color(bits)
    top_h, top_v, bottom_h, bottom_v = (bits.read(12) for _ in range(4))
    if top_h >= bottom_h or top_v >= bottom_v:
        return None
    frame = (top_h, top_v, bottom_h, bottom_v)
    frame_color = (0, 0, 0, 0)
    if framed:
        frame = tuple(bits.read(12) for _ in range(4))
        frame_color = _read_color(bits)
        if frame[0] > top_h or frame[1] > top_v or frame[2] < bottom_h or frame[3] < bottom_v:
            return None
    thickness = right = down = 0
    outline_color = shadow_color = (0, 0, 0, 0)
    if outline_style == 1:
        bits.read(4)
        thickness = bits.read(4)
        outline_color = _read_color(bits)
    elif outline_style == 2:
        right, down = bits.read(4), bits.read(4)
        shadow_color = _read_color(bits)
    elif outline_style == 3:
        bits.read(24)
    length = bits.read(16)
    start = bits.bit // 8 + (1 if bits.bit % 8 else 0)
    width, height = bottom_h - top_h, bottom_v - top_v
    on = decode_bitmap(data[start:start + length] if length else data[start:], width, height)
    # Room for the outline or the shadow around the frame, as VLC draws it.
    margin_h = margin_v = thickness if outline_style == 1 else 0
    if outline_style == 2:
        margin_h, margin_v = right, down
    left, top = frame[0] - margin_h, frame[1] - margin_v
    across, down_size = frame[2] + margin_h - left, frame[3] + margin_v - top
    picture = bytearray(across * down_size)
    shift_x, shift_y = top_h - left, top_v - top

    def set_pixel(x, y, value):
        if 0 <= x < across and 0 <= y < down_size:
            picture[y * across + x] = value

    if outline_style == 1:
        disc = [(dx, dy) for dy in range(thickness + 1) for dx in range(thickness + 1)
                if (dx or dy) and dx * dx + dy * dy <= thickness * thickness]
        for y in range(height):
            for x in range(width):
                if on[y * width + x]:
                    for dx, dy in disc:
                        for sx, sy in ((dx, dy), (-dx, dy), (dx, -dy), (-dx, -dy)):
                            set_pixel(x + shift_x + sx, y + shift_y + sy, 2)
    elif outline_style == 2:
        for y in range(height):
            for x in range(width):
                if on[y * width + x]:
                    set_pixel(x + shift_x + right, y + shift_y + down, 3)
    for y in range(height):
        for x in range(width):
            if on[y * width + x]:
                set_pixel(x + shift_x, y + shift_y, 1)
    return left, top, across, down_size, bytes(picture), [frame_color, character, outline_color, shadow_color]


def message(data):
    """A subtitle_message's fields: {'language', 'pre_clear', 'immediate',
    'standard', 'pts', 'type', 'duration', 'block'}; None when too short."""
    if len(data) < 12:
        return None
    length = data[10] << 8 | data[11]
    return {'language': bytes(data[0:3]).decode('latin-1', 'replace').strip('\x00 '),
            'pre_clear': bool(data[3] & 0x80), 'immediate': bool(data[3] & 0x40), 'standard': data[3] & 0x1F,
            'pts': int.from_bytes(data[4:8], 'big'), 'type': data[8] >> 4,
            'duration': (data[8] & 0x07) << 8 | data[9], 'block': bytes(data[12:12 + length])}


class _Messages:
    """Sections of a stream put back together into messages."""

    def __init__(self):
        self.segments, self.id = [], None

    def section(self, section):
        """The message a whole section completes, or None."""
        if len(section) < 8 or section[0] != TABLE_ID or section[3] & 0x3F:
            return None
        length = (section[1] & 0x0F) << 8 | section[2]
        body = section[3:3 + length]
        if teletext._crc32(section[:3 + length]) != 0:
            return None                              # damaged
        body = body[:-4]
        if not body[0] & 0x40:
            return bytes(body[1:])
        if len(body) < 6:
            return None
        ident = body[1] << 8 | body[2]
        last, number = body[3] << 4 | body[4] >> 4, (body[4] & 0x0F) << 8 | body[5]
        if number == 0:
            self.segments, self.id = [], ident
        elif ident != self.id or len(self.segments) != number:
            self.segments, self.id = [], None
            return None
        self.segments.append(bytes(body[6:]))
        if number == last:
            whole, self.segments = b''.join(self.segments), []
            return whole
        return None


def _sections(payload, start, pending):
    """The sections a packet's payload completes, `pending` (a bytearray)
    holding what is carried on to the next."""
    found = []
    if start:
        pointer = payload[0]
        pending += payload[1:1 + pointer]
        if pending:
            found.append(bytes(pending))
        pending.clear()
        payload = payload[1 + pointer:]
    pending += payload
    while len(pending) >= 3 and pending[0] != 0xFF:
        size = 3 + ((pending[1] & 0x0F) << 8 | pending[2])
        if len(pending) < size:
            break
        found.append(bytes(pending[:size]))
        del pending[:size]
    if pending[:1] == b'\xff':
        pending.clear()
    return [section for section in found if len(section) >= 3 and len(section) >= 3 + ((section[1] & 0x0F) << 8 | section[2])]


def _pmt_streams(section):
    """{pid: language or '' for an SCTE 27 stream, None for another} of a
    PMT section; the language from an ISO 639 descriptor."""
    streams = {}
    if len(section) < 12 or section[0] != 0x02:
        return streams
    end = 3 + (((section[1] & 0x0F) << 8) | section[2]) - 4
    position = 12 + (((section[10] & 0x0F) << 8) | section[11])
    while position + 5 <= end:
        kind = section[position]
        pid = ((section[position + 1] & 0x1F) << 8) | section[position + 2]
        length = ((section[position + 3] & 0x0F) << 8) | section[position + 4]
        info = section[position + 5:position + 5 + length]
        language, index = '', 0
        while index + 2 <= len(info):
            if info[index] == 0x0A and info[index + 1] >= 3:
                language = bytes(info[index + 2:index + 5]).decode('latin-1', 'replace').strip('\x00 ')
            index += 2 + info[index + 1]
        streams[pid] = language if kind == STREAM_TYPE else None
        position += 5 + length
    return streams


def scan(path, progress=None, stopped=None, limit=None):
    """({pid: language}, {pid: [(pts or None, message, pts of the last
    time before it)]}, origin): the SCTE 27 streams of the transport
    stream at `path`, their messages, and the earliest PTS of its other
    streams (where FFmpeg counts their times from)."""
    listed, pmt_pids, tables, pending, found, first = {}, set(), {}, {}, {}, {}
    assemblers, last_time = {}, None
    with open(path, 'rb') as stream:
        layout = teletext._packet_layout(stream.read(204 * 6))
        if layout is None:
            return {}, {}, None
        stream.seek(0)
        read = 0
        for pid, start, payload in teletext._packets(stream, *layout, progress=progress, stopped=stopped):
            read += teletext.PACKET
            if limit is not None and read > limit:
                break
            if pid == 0 or pid in pmt_pids:
                if start:
                    tables[pid] = bytearray(teletext._section_parts(payload, True))
                elif pid in tables:
                    tables[pid] += payload
                section = tables.get(pid)
                if section and len(section) >= 3 and len(section) >= 3 + (((section[1] & 0x0F) << 8) | section[2]):
                    if pid == 0 and section[0] == 0x00:
                        end = 3 + (((section[1] & 0x0F) << 8) | section[2]) - 4
                        for index in range(8, end - 3, 4):
                            if (section[index] << 8) | section[index + 1]:
                                pmt_pids.add(((section[index + 2] & 0x1F) << 8) | section[index + 3])
                    elif section[0] == 0x02:
                        for key, value in _pmt_streams(bytes(section)).items():
                            listed.setdefault(key, value)
                    del tables[pid]
                continue
            if pid not in listed:
                continue
            if listed[pid] is None:
                if start:
                    pts = teletext._pes_pts(payload)
                    if pts is not None:
                        first.setdefault(pid, pts)
                        last_time = pts
                continue
            for section in _sections(payload, start, pending.setdefault(pid, bytearray())):
                whole = assemblers.setdefault(pid, _Messages()).section(section)
                if whole is not None:
                    found.setdefault(pid, []).append((whole, last_time))
    origin = min(first.values()) if first else None
    return {pid: language for pid, language in listed.items() if language is not None}, found, origin


def streams(path, limit=8 * 1024 * 1024):
    """The SCTE 27 streams of the transport stream at `path`, from the PMT
    in its first `limit` bytes: {pid: language}."""
    return scan(path, limit=limit)[0]


def decode(messages, origin=None):
    """[Picture] of `messages`, [(message bytes, PTS of the last time the
    file gave before it)], timed in seconds from `origin`."""
    shown = []
    base = origin
    for data, before in messages:
        fields = message(data)
        if fields is None or fields['type'] != 1:
            continue
        width, height, rate = STANDARDS.get(fields['standard'], STANDARDS[3])
        if fields['immediate'] or before is not None and not fields['pts']:
            pts = before if before is not None else 0
        else:
            # The 33rd bit, from the time the file was at.
            pts = fields['pts']
            if before is not None:
                pts |= before & (1 << 32)
                if pts < before - (1 << 31):
                    pts += 1 << 32
        if base is None:
            base = pts
        start = round(((pts - base) % _PTS_WRAP) / 90000.0, 3)
        drawn = simple_bitmap(fields['block'])
        if fields['pre_clear']:
            for picture in shown:
                if picture.end > start:
                    picture.end = start
        if drawn is None or not (any(drawn[4]) or drawn[5][0][3]):
            continue                                 # nothing that shows: it clears the display alone
        x, y, across, down, indices, colours = drawn
        end = round(start + fields['duration'] / rate, 3)
        # A message sent again as it was, right after the one before, is one subtitle.
        if shown and shown[-1].indices == indices and (shown[-1].x, shown[-1].y) == (x, y) and \
                abs(shown[-1].end - start) < 0.05:
            shown[-1].end = end
            continue
        shown.append(Picture(start, end, x, y, across, down, indices, colours, screen_width=width, screen_height=height))
    return [picture for picture in shown if picture.end > picture.start]


def read(path, pid=None, progress=None, stopped=None):
    """(pictures, {'language', 'pid'}) of an SCTE 27 stream of the
    transport stream at `path`: `pid`, or the first. Raises ValueError for
    a file with none."""
    listed, found, origin = scan(path, progress=progress, stopped=stopped)
    if pid is None or not found.get(pid):
        pid = next((key for key in list(listed) + list(found) if found.get(key)), None)
    if pid is None:
        raise ValueError('There are no SCTE 27 subtitles in this file')
    messages = found[pid]
    language = listed.get(pid) or next((m['language'] for m in (message(data) for data, _t in messages) if m), '')
    return decode(messages, origin), {'language': language, 'pid': pid}


# --- Writing -------------------------------------------------------------------

def encode_bitmap(pixels, width, height):
    """A bitmap's pixels (1 on, 0 off, row after row) coded in runs, a
    line at a time, the off pixels a line ends with as its end."""
    writer = _BitWriter()

    def off_runs(count):
        while count > 0:
            piece = min(count, 64)
            writer.write(0b01, 2)
            writer.write(piece % 64, 6)
            count -= piece

    def on_runs(count):
        while count > 0:
            piece = min(count, 16)
            writer.write(0b001, 3)
            writer.write(piece % 16, 4)
            count -= piece

    for row in range(height):
        line = pixels[row * width:(row + 1) * width]
        runs, at = [], 0
        while at < width:
            end = at
            while end < width and line[end] == line[at]:
                end += 1
            runs.append((line[at], end - at))
            at = end
        index = 0
        while index < len(runs):
            value, count = runs[index]
            last = index == len(runs) - 1
            if not value:
                if last:
                    writer.write(0b00001, 5)
                else:
                    off_runs(count)
                index += 1
                continue
            if last:
                on_runs(count)                       # the line ends on: no end code
                index += 1
                continue
            if count > 8:
                on_runs(count - 8)
                count = 8
            off = runs[index + 1][1]
            if index + 1 == len(runs) - 1:
                on_runs(count)
                writer.write(0b00001, 5)
            else:
                writer.write(1, 1)
                writer.write(count % 8, 3)
                first = min(off, 32)
                writer.write(first % 32, 5)
                off_runs(off - first)
            index += 2
    return writer.bytes()


def bitmap_block(x, y, pixels, width, height, character, outline=None, thickness=0, frame=None):
    """A simple bitmap: the characters' pixels at (x, y), their colour
    (r, g, b, a), an outline of `thickness` in `outline`, or a frame
    (left, top, right, bottom, colour) behind them."""
    writer = _BitWriter()
    writer.write(0, 5)
    writer.write(1 if frame else 0, 1)
    writer.write(1 if outline and thickness else 0, 2)
    _write_color(writer, character)
    for value in (x, y, x + width, y + height):
        writer.write(value, 12)
    if frame:
        for value in frame[:4]:
            writer.write(value, 12)
        _write_color(writer, frame[4])
    if outline and thickness:
        writer.write(0, 4)
        writer.write(min(15, thickness), 4)
        _write_color(writer, outline)
    coded = encode_bitmap(pixels, width, height)
    writer.write(len(coded), 16)
    return writer.bytes() + coded


def message_bytes(language, standard, pts, duration, block, pre_clear=True):
    return (language.encode('latin-1', 'replace')[:3].ljust(3, b' ')
            + bytes(((0x80 if pre_clear else 0) | (standard & 0x1F),)) + (pts & 0xFFFFFFFF).to_bytes(4, 'big')
            + bytes((0x10 | ((duration >> 8) & 0x07), duration & 0xFF)) + len(block).to_bytes(2, 'big') + block)


def sections_of(data, ident=0):
    """The sections of a message: one, or segments of it."""
    def section(body):
        length = len(body) + 4
        head = bytes((TABLE_ID, 0x30 | (length >> 8), length & 0xFF)) + body
        return head + teletext._crc32(head).to_bytes(4, 'big')
    if len(data) <= _SECTION_ROOM:
        return [section(b'\x00' + data)]
    pieces = [data[at:at + _SEGMENT_ROOM] for at in range(0, len(data), _SEGMENT_ROOM)]
    last = len(pieces) - 1
    return [section(bytes((0x40,)) + ident.to_bytes(2, 'big') + bytes((last >> 4, ((last & 0x0F) << 4) | (number >> 8),
                                                                       number & 0xFF)) + piece)
            for number, piece in enumerate(pieces)]


def one_colour(rgba):
    """(pixels on, the characters' colour, outline colour or None, its
    thickness, frame or None) of a drawn subtitle (numpy RGBA): the
    characters the pixels near its brightest common colour, the other
    pixels that show its outline, or, when they fill most of the picture,
    its frame."""
    import numpy
    flat = rgba.reshape(-1, 4).astype(numpy.int32)
    shows = flat[:, 3] >= 96
    height, width = rgba.shape[:2]
    if not shows.any():
        return bytes(width * height), (255, 255, 255, 255), None, 0, None
    luma = flat[:, 0] * 299 + flat[:, 1] * 587 + flat[:, 2] * 114
    bright = shows & (luma >= numpy.percentile(luma[shows], 75))
    fill = numpy.median(flat[bright][:, :3], axis=0)
    near = shows & (numpy.abs(flat[:, :3] - fill).sum(axis=1) <= 120)
    pixels = near.astype(numpy.uint8)
    rest = shows & ~near
    character = tuple(int(v) for v in fill) + (255,)
    if not rest.any():
        return pixels.tobytes(), character, None, 0, None
    colour = tuple(int(v) for v in numpy.median(flat[rest][:, :3], axis=0)) + (255 if flat[rest][:, 3].mean() > 192 else 128,)
    if rest.sum() > 0.5 * width * height:
        return pixels.tobytes(), character, None, 0, colour
    # The outline's thickness: how far its pixels reach from the characters.
    grid_rest, grid_on = rest.reshape(height, width), near.reshape(height, width)
    reach, current = 0, grid_on.copy()
    while reach < 15 and (grid_rest & ~current).any():
        grown = current.copy()
        grown[1:, :] |= current[:-1, :]
        grown[:-1, :] |= current[1:, :]
        grown[:, 1:] |= current[:, :-1]
        grown[:, :-1] |= current[:, 1:]
        if (grown == current).all():
            break
        current = grown
        reach += 1
        if not (grid_rest & ~current).any():
            break
    return pixels.tobytes(), character, colour, max(1, reach), None


def draw_tracks(tracks, display=(1920, 1080), draw=None, folder=None):
    """`tracks` ([{'segments', 'language'}]) drawn as the preview draws
    them, each subtitle in one colour: [{'language' (ISO 639-2), 'blocks':
    [{'start', 'end', 'block'}]}]."""
    from subtitld.modules import usf
    if draw is None:
        from subtitld.interface.subtitle_pictures import draw
    import shutil
    folder = folder or tempfile.mkdtemp(prefix='subtitld_scte27_')
    made = []
    try:
        for number, track in enumerate(tracks):
            blocks = []
            for drawn in draw(track.get('segments') or [], display, folder, stem=f'{number}_'):
                rgba = dvbsub.png_rgba(drawn['path'])
                width, height = min(drawn['width'], display[0] - 1), min(drawn['height'], display[1] - 1)
                rgba = rgba[:height, :width]
                x = max(0, min(int(drawn['x']), display[0] - 1 - width))
                y = max(0, min(int(drawn['y']), display[1] - 1 - height))
                pixels, character, outline, thickness, frame = one_colour(rgba)
                boxed = (x, y, x + width, y + height, frame) if frame else None
                blocks.append({'start': drawn['start'], 'end': drawn['end'],
                               'block': bitmap_block(x, y, pixels, width, height, character, outline, thickness, boxed)})
            made.append({'language': usf.iso639_2(track.get('language')) if track.get('language') else 'und',
                         'blocks': blocks})
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return made


def standard_for(width=None, height=None):
    """The display standard subtitles for a video are drawn for: 0
    (720x480) for a standard definition one, 3 (1920x1080) for any other."""
    if width and height and int(width) <= 720 and int(height) <= 576:
        return 0
    return 3


def _messages(track, standard, start):
    """[(send time, sections)] of a track's blocks."""
    rate = STANDARDS[standard][2]
    out = []
    for ident, block in enumerate(track['blocks']):
        begin, end = float(block['start']), float(block['end'])
        while end - begin > 0.001:
            frames = min(_LONGEST, max(1, int(round((end - begin) * rate))))
            pts = int(round((begin + start) * 90000)) % _PTS_WRAP
            data = message_bytes(track['language'], standard, pts, frames, block['block'])
            out.append((begin + start - _LEAD, sections_of(data, ident & 0xFFFF)))
            begin += frames / rate
    return out


def transport_stream(tracks, standard=3, start=0.0):
    """The bytes of a transport stream with an SCTE 27 stream for each of
    `tracks` (as draw_tracks gives them), each message sent `_LEAD`
    seconds before it shows. Times are seconds from `start`."""
    counters = {}
    stream_info = b''
    for number, track in enumerate(tracks):
        pid = SUBTITLE_PID + number
        descriptor = bytes((0x0A, 4)) + track['language'][:3].encode('latin-1', 'replace').ljust(3, b' ') + b'\x00'
        stream_info += bytes((STREAM_TYPE, 0xE0 | (pid >> 8), pid & 0xFF, 0xF0, len(descriptor))) + descriptor
    pat = b'\x00' + teletext._section(0x00, 1, bytes((0, 1, 0xE0 | (teletext.PMT_PID >> 8), teletext.PMT_PID & 0xFF)))
    pmt = b'\x00' + teletext._section(0x02, 1, bytes((0xE0 | (teletext.PCR_PID >> 8), teletext.PCR_PID & 0xFF,
                                                      0xF0, 0x00)) + stream_info)
    # Each stream starts by clearing the display at `start`, a bitmap of a
    # pixel off, so its own start is the video's.
    blank = bitmap_block(0, 0, b'\x00', 1, 1, (255, 255, 255, 255))
    events = sorted([(start, number, sections_of(message_bytes(track['language'], standard,
                                                               int(round(start * 90000)), 1, blank)))
                     for number, track in enumerate(tracks)]
                    + [(when, number, sections) for number, track in enumerate(tracks)
                       for when, sections in _messages(track, standard, start)])
    out = []
    for when, number, sections in events:
        pts = int(round(max(0.0, when) * 90000)) % _PTS_WRAP
        out += teletext._ts_packets(0, pat, counters) + teletext._ts_packets(teletext.PMT_PID, pmt, counters)
        out.append(teletext._pcr_packet(pts))
        for section in sections:
            out += teletext._ts_packets(SUBTITLE_PID + number, b'\x00' + section, counters)
    return b''.join(out)


def _pcr(packet):
    """The PCR base a packet's adaptation field carries, or None."""
    if packet[3] & 0x20 and packet[4] >= 7 and packet[5] & 0x10:
        return (packet[6] << 25) | (packet[7] << 17) | (packet[8] << 9) | (packet[9] << 1) | (packet[10] >> 7)
    return None


def _with_streams(section, info):
    """A PMT section with the elementary streams `info` added, its CRC new."""
    length = ((section[1] & 0x0F) << 8) | section[2]
    body = section[3:3 + length - 4] + info
    total = len(body) + 4
    head = bytes((section[0], (section[1] & 0xF0) | (total >> 8), total & 0xFF)) + body
    return head + teletext._crc32(head).to_bytes(4, 'big')


def interleave(source, target, tracks, standard=3, start=None):
    """Copy the transport stream `source` to `target` with an SCTE 27
    stream for each of `tracks`: the PMT given them, each message's
    sections laid in before the first packet whose clock is past its send
    time. Times count from the earliest PTS of `source` (or `start`)."""
    with open(source, 'rb') as stream:
        data = stream.read()
    layout = teletext._packet_layout(data[:204 * 6])
    if layout is None:
        raise ValueError('Not a transport stream')
    size, offset = layout
    packets = [data[at:at + 188] for at in range(offset, len(data) - 187, size) if data[at] == 0x47]
    if start is None:
        times = [teletext._pes_pts(packet[4 + (packet[4] + 1 if packet[3] & 0x20 else 0):])
                 for packet in packets if packet[1] & 0x40]
        times = [pts for pts in times if pts is not None]
        start = (min(times) / 90000.0) if times else 0.0
    info = b''
    for number, track in enumerate(tracks):
        pid = SUBTITLE_PID + number
        descriptor = bytes((0x0A, 4)) + track['language'][:3].encode('latin-1', 'replace').ljust(3, b' ') + b'\x00'
        info += bytes((STREAM_TYPE, 0xE0 | (pid >> 8), pid & 0xFF, 0xF0, len(descriptor))) + descriptor
    events = sorted((when, number, sections) for number, track in enumerate(tracks)
                    for when, sections in _messages(track, standard, start))
    counters, pmt_pids, out = {}, set(), []
    pending = list(events)
    for packet in packets:
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        clock = _pcr(packet)
        if clock is not None:
            while pending and pending[0][0] * 90000 <= clock:
                _when, number, sections = pending.pop(0)
                for section in sections:
                    out += teletext._ts_packets(SUBTITLE_PID + number, b'\x00' + section, counters)
        if pid == 0 and packet[1] & 0x40:
            payload = packet[4 + (packet[4] + 1 if packet[3] & 0x20 else 0):]
            section = teletext._section_parts(payload, True)
            if section[:1] == b'\x00':
                end = 3 + (((section[1] & 0x0F) << 8) | section[2]) - 4
                for index in range(8, end - 3, 4):
                    if (section[index] << 8) | section[index + 1]:
                        pmt_pids.add(((section[index + 2] & 0x1F) << 8) | section[index + 3])
        if pid in pmt_pids and packet[1] & 0x40:
            payload = packet[4 + (packet[4] + 1 if packet[3] & 0x20 else 0):]
            section = teletext._section_parts(payload, True)
            if section[:1] == b'\x02' and 3 + (((section[1] & 0x0F) << 8) | section[2]) <= len(section):
                counters[pid] = packet[3] & 0x0F
                out += teletext._ts_packets(pid, b'\x00' + _with_streams(section, info), counters)
                continue
        out.append(packet)
    for _when, number, sections in pending:
        for section in sections:
            out += teletext._ts_packets(SUBTITLE_PID + number, b'\x00' + section, counters)
    with open(target, 'wb') as stream:
        stream.write(b''.join(out))


def write_video(video, output, tracks, width=None, height=None, ffmpeg='ffmpeg', startupinfo=None, draw=None):
    """Write `video` out as the transport stream `output` with an SCTE 27
    stream for each of `tracks` ([{'segments', 'language'}]), drawn for the
    display a video `width` x `height` has. Raises ValueError for no
    tracks and RuntimeError with what FFmpeg said when it cannot write."""
    if not tracks:
        raise ValueError('There are no subtitles to write into the video')
    standard = standard_for(width, height)
    drawn = draw_tracks(tracks, STANDARDS[standard][:2], draw)
    handle, copied = tempfile.mkstemp(prefix='subtitld_scte27_', suffix='.ts')
    os.close(handle)
    try:
        run = subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y', '-nostdin', '-i', str(video),
                              '-map', '0:v?', '-map', '0:a?', '-c', 'copy', '-f', 'mpegts', copied],
                             capture_output=True, startupinfo=startupinfo, check=False)
        if run.returncode != 0 or not os.path.getsize(copied):
            said = (run.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
            raise RuntimeError(said[0] if said else 'FFmpeg could not write the video')
        interleave(copied, output, drawn, standard)
    finally:
        try:
            os.unlink(copied)
        except OSError:
            pass
    return output
