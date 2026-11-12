"""Blu-ray subtitles, PGS (Presentation Graphic Stream): a .sup of pictures.

A .sup is a run of segments, each `PG`, a 90 kHz PTS and DTS, a type and
its length. A display set, ended by an END segment, holds a presentation
composition (PCS: the objects shown from its PTS on, and where), windows,
palettes (PDS: up to 256 YCbCr colours with opacity) and objects (ODS: a
run-length coded picture, in fragments when large). A composition without
objects clears the screen; each composition holds until the next.

`read(path)` gives picture_subtitles.Picture per composition, the objects
it shows drawn into one picture (two objects, top and bottom lines, make
one picture with the space between). `parse(data)` reads bytes, for the
PGS tracks inside videos, which FFmpeg writes out as a .sup.

`save(path, pictures, size)` writes one: the subtitles drawn as pictures
(interface.subtitle_pictures), one on screen at a time (disc_pictures).
Each is an epoch of its own, which a player can start reading at: a
composition of one object in one window as big as it, forced where the
subtitle is, a palette of up to 255 of the picture's colours and
transparent, and the object, split over segments when it is larger than
one holds. A composition with no object clears it at its end, unless the
next subtitle starts there and takes its place.
"""

from subtitld.modules.picture_subtitles import Picture

PCS, WDS, PDS, ODS, END = 0x16, 0x17, 0x14, 0x15, 0x80
DEFAULT_DURATION = 5.0


class PGSError(ValueError):
    """Not a PGS Subtitld can read."""


def _rgba(y, cr, cb, alpha, hd):
    """A PDS entry as (r, g, b, a): BT.709 for HD, BT.601 below."""
    y, cb, cr = y - 16, cb - 128, cr - 128
    if hd:
        r, g, b = 1.164 * y + 1.793 * cr, 1.164 * y - 0.213 * cb - 0.533 * cr, 1.164 * y + 2.112 * cb
    else:
        r, g, b = 1.164 * y + 1.596 * cr, 1.164 * y - 0.392 * cb - 0.813 * cr, 1.164 * y + 2.017 * cb
    clamp = lambda v: max(0, min(255, int(round(v))))  # noqa: E731
    return clamp(r), clamp(g), clamp(b), alpha


def decode_rle(data, width, height):
    """An object's run-length coded picture: palette indices, width x height bytes."""
    out = bytearray(width * height)
    x = y = 0
    i, end = 0, len(data)
    while i < end and y < height:
        byte = data[i]
        i += 1
        if byte:
            if x < width:
                out[y * width + x] = byte
            x += 1
            continue
        if i >= end:
            break
        flags = data[i]
        i += 1
        if flags == 0:                  # end of the line
            x, y = 0, y + 1
            continue
        count = flags & 0x3F
        if flags & 0x40:
            if i >= end:
                break
            count = count << 8 | data[i]
            i += 1
        colour = 0
        if flags & 0x80:
            if i >= end:
                break
            colour = data[i]
            i += 1
        if colour and x < width:
            run = min(count, width - x)
            start = y * width + x
            out[start:start + run] = bytes([colour]) * run
        x += count
    return bytes(out)


def segments(data):
    """(pts seconds, type, payload) for each segment."""
    i, end = 0, len(data)
    while i + 13 <= end:
        if data[i:i + 2] != b'PG':
            j = data.find(b'PG', i + 1)
            if j < 0:
                return
            i = j
            continue
        pts = int.from_bytes(data[i + 2:i + 6], 'big') / 90000
        kind = data[i + 10]
        size = data[i + 11] << 8 | data[i + 12]
        yield pts, kind, data[i + 13:i + 13 + size]
        i += 13 + size


def parse(data):
    """The pictures in a PGS stream, in time order."""
    palettes = {}
    objects = {}                        # id -> [width, height, bytearray]
    pending = None                      # the display set's composition, drawn at END
    shown = []
    screen = (1920, 1080)
    for pts, kind, payload in segments(data):
        if kind == PCS and len(payload) >= 11:
            width, height = payload[0] << 8 | payload[1], payload[2] << 8 | payload[3]
            screen = (width or screen[0], height or screen[1])
            state = payload[7]
            if state & 0x80:            # epoch start: everything before is gone
                objects.clear()
                palettes.clear()
            count = payload[10]
            placed = []
            i = 11
            for _ in range(count):
                if i + 8 > len(payload):
                    break
                object_id = payload[i] << 8 | payload[i + 1]
                cropped = payload[i + 3] & 0x80
                # Forced: shown even where a viewer asked for no subtitles,
                # for the sign or the line of another language a film
                # translates on screen.
                forced = bool(payload[i + 3] & 0x40)
                x, y = payload[i + 4] << 8 | payload[i + 5], payload[i + 6] << 8 | payload[i + 7]
                crop = None
                i += 8
                if cropped and i + 8 <= len(payload):
                    crop = tuple(payload[i + k] << 8 | payload[i + k + 1] for k in (0, 2, 4, 6))
                    i += 8
                placed.append((object_id, x, y, crop, forced))
            pending = (pts, payload[9], placed)
        elif kind == PDS and len(payload) >= 2:
            palette = palettes.setdefault(payload[0], {})
            hd = screen[1] >= 720
            for k in range(2, len(payload) - 4, 5):
                entry, y, cr, cb, alpha = payload[k:k + 5]
                palette[entry] = _rgba(y, cr, cb, alpha, hd)
        elif kind == ODS and len(payload) >= 4:
            object_id = payload[0] << 8 | payload[1]
            sequence = payload[3]
            if sequence & 0x80 and len(payload) >= 11:
                width, height = payload[7] << 8 | payload[8], payload[9] << 8 | payload[10]
                objects[object_id] = [width, height, bytearray(payload[11:])]
            elif object_id in objects:
                objects[object_id][2] += payload[4:]
        elif kind == END and pending is not None:
            when, palette_id, placed = pending
            pending = None
            if shown and shown[-1].end is None:
                shown[-1].end = when
            picture = _compose(placed, objects, palettes.get(palette_id, {}), screen, when)
            if picture is not None:
                shown.append(picture)
    for current, following in zip(shown, shown[1:] + [None]):
        if current.end is None:
            current.end = following.start if following else current.start + DEFAULT_DURATION
    return [p for p in shown if p.end > p.start]


def _compose(placed, objects, palette, screen, when):
    drawn = []
    forced = any(item[4] for item in placed)
    for object_id, x, y, crop, _forced in placed:
        if object_id not in objects:
            continue
        width, height, data = objects[object_id]
        if not width or not height:
            continue
        indices = decode_rle(bytes(data), width, height)
        if crop:
            cx, cy, cw, ch = crop
            cw, ch = min(cw, width - cx), min(ch, height - cy)
            if cw <= 0 or ch <= 0:
                continue
            indices = b''.join(indices[(cy + r) * width + cx:(cy + r) * width + cx + cw] for r in range(ch))
            width, height = cw, ch
        drawn.append((x, y, width, height, indices))
    if not drawn:
        return None
    left = min(d[0] for d in drawn)
    top = min(d[1] for d in drawn)
    right = max(d[0] + d[2] for d in drawn)
    bottom = max(d[1] + d[3] for d in drawn)
    width, height = right - left, bottom - top
    canvas = bytearray(width * height)
    for x, y, w, h, indices in drawn:
        for row in range(h):
            start = (y - top + row) * width + (x - left)
            canvas[start:start + w] = indices[row * w:(row + 1) * w]
    colours = [palette.get(v, (0, 0, 0, 0)) for v in range(256)]
    picture = Picture(when, None, left, top, width, height, bytes(canvas), colours, forced)
    picture.screen_width, picture.screen_height = screen
    return picture


def read(path):
    with open(path, 'rb') as handle:
        data = handle.read()
    if data[:2] != b'PG':
        raise PGSError(f'{path} is not a PGS (.sup) file')
    return parse(data)


# Writing ------------------------------------------------------------------

# The frame rates a composition names, by their code.
FRAME_RATES = {23.976: 0x10, 24.0: 0x20, 25.0: 0x30, 29.97: 0x40, 50.0: 0x60, 59.94: 0x70}
MAX_SEGMENT = 0xFFFF            # a segment says its length in 16 bits
FORCED = 0x40                   # a composition object's flag for a forced one
EPOCH_START = 0x80


def rate_code(fps):
    """The composition's code for `fps`, the nearest Blu-ray has (23.976 when
    there is none to go by)."""
    if not fps:
        return FRAME_RATES[23.976]
    return FRAME_RATES[min(FRAME_RATES, key=lambda rate: abs(rate - float(fps)))]


def _ycbcr(r, g, b, alpha, hd):
    """(Y, Cr, Cb, alpha) for a colour, in video range: BT.709 for HD, BT.601
    below, as _rgba reads them back."""
    kr, kb = (0.2126, 0.0722) if hd else (0.299, 0.114)
    luma = kr * r + (1 - kr - kb) * g + kb * b
    clamp = lambda v, low, high: max(low, min(high, int(round(v))))  # noqa: E731
    return (clamp(16 + luma * 219 / 255, 16, 235), clamp(128 + (r - luma) * 112 / 255 / (1 - kr), 16, 240),
            clamp(128 + (b - luma) * 112 / 255 / (1 - kb), 16, 240), alpha)


def encode_rle(indices, width, height):
    """An object's picture run-length coded, as decode_rle reads it: a byte
    for one or two pixels of a colour other than 0, a run of 0 in two or
    three bytes, of another colour in three or four, and 00 00 at each
    line's end."""
    import numpy
    rows = numpy.frombuffer(indices, numpy.uint8).reshape(height, width)
    out = bytearray()
    for row in rows:
        edges = (numpy.flatnonzero(row[1:] != row[:-1]) + 1).tolist()
        for start, end in zip([0] + edges, edges + [width]):
            colour = int(row[start])
            run = end - start
            while run:
                count = min(run, 0x3FFF)
                run -= count
                if colour and count <= 2:
                    out += bytes([colour]) * count
                elif colour == 0:
                    out += bytes([0, count]) if count < 64 else bytes([0, 0x40 | count >> 8, count & 255])
                elif count < 64:
                    out += bytes([0, 0x80 | count, colour])
                else:
                    out += bytes([0, 0xC0 | count >> 8, count & 255, colour])
        out += b'\x00\x00'
    return bytes(out)


def _segment(kind, payload, pts, dts=0):
    return b'PG' + (pts & 0xFFFFFFFF).to_bytes(4, 'big') + (dts & 0xFFFFFFFF).to_bytes(4, 'big') + \
        bytes([kind]) + len(payload).to_bytes(2, 'big') + payload


def _composition(size, rate, number, state, objects=()):
    """A PCS: the frame, its rate, the composition's number and state, and
    the objects shown [(x, y, forced)], object and window 0 the first."""
    payload = int(size[0]).to_bytes(2, 'big') + int(size[1]).to_bytes(2, 'big') + bytes([rate]) + \
        (number & 0xFFFF).to_bytes(2, 'big') + bytes([state, 0, 0, len(objects)])
    for index, (x, y, forced) in enumerate(objects):
        payload += index.to_bytes(2, 'big') + bytes([index, FORCED if forced else 0]) + \
            int(x).to_bytes(2, 'big') + int(y).to_bytes(2, 'big')
    return payload


def _window(x, y, width, height):
    return bytes([1, 0]) + b''.join(int(v).to_bytes(2, 'big') for v in (x, y, width, height))


def display_sets(x, y, width, height, indices, colours, start, end, size, rate, number, forced=False):
    """The bytes of one subtitle: an epoch's display set that shows it at
    `start` and one that clears it at `end` (None: none, the next subtitle
    taking its place). Its composition numbers are `number` and the next.

    The times before the PTS are a decoder's, by the HDMV model as
    authoring tools reckon it: the plane cleared at 9 ticks of 90 kHz a 3200
    pixels, the object decoded at 9 a 1600, the DTS that long before."""
    hd = size[1] >= 720
    begin = int(round(start * 90000))
    clear = (9 * size[0] * size[1] + 3199) // 3200
    decode = (9 * width * height + 1599) // 1600
    window_time = (9 * width * height + 3199) // 3200
    dts = max(0, begin - clear - decode - window_time)
    decoded = min(begin, dts + clear + decode)
    out = _segment(PCS, _composition(size, rate, number, EPOCH_START, [(x, y, forced)]), begin, dts)
    out += _segment(WDS, _window(x, y, width, height), dts)
    out += _segment(PDS, bytes([0, 0]) + b''.join(bytes([entry]) + bytes(_ycbcr(*colour, hd))
                                               for entry, colour in enumerate(colours)), dts)
    # The object in fragments a segment can hold: the first says how long
    # the whole is, and how big the picture.
    data = encode_rle(indices, width, height)
    head = (len(data) + 4).to_bytes(3, 'big') + width.to_bytes(2, 'big') + height.to_bytes(2, 'big')
    pieces = [data[:MAX_SEGMENT - 11]]
    rest = data[MAX_SEGMENT - 11:]
    while rest:
        pieces.append(rest[:MAX_SEGMENT - 4])
        rest = rest[MAX_SEGMENT - 4:]
    for piece_number, piece in enumerate(pieces):
        flags = (0x80 if piece_number == 0 else 0) | (0x40 if piece_number == len(pieces) - 1 else 0)
        payload = bytes([0, 0, 0, flags]) + (head if piece_number == 0 else b'') + piece
        out += _segment(ODS, payload, decoded, dts)
    out += _segment(END, b'', decoded, decoded)
    if end is not None:
        finish = max(begin + 1, int(round(end * 90000)))
        dts = max(begin, finish - window_time)
        out += _segment(PCS, _composition(size, rate, number + 1, 0), finish, dts)
        out += _segment(WDS, _window(x, y, width, height), dts)
        out += _segment(END, b'', dts, dts)
    return out


def save(path, pictures, size, fps=None):
    """Write the .sup of `pictures` (disc_pictures' dicts, in order of
    start) on a frame of `size` at the rate nearest `fps`: each subtitle an
    epoch of its own, in up to 255 colours and transparent, cleared at its
    end unless the next one starts there."""
    from subtitld.modules import disc_pictures
    size = (int(size[0]), int(size[1]))
    rate = rate_code(fps)
    number = 0
    previous = None

    def written(unit, following):
        end = None if following is not None and unit['end'] >= following else unit['end']
        return display_sets(*unit['fitted'][:4], unit['indices'], unit['colours'], unit['start'], end,
                            size, rate, number, forced=unit['forced'])

    with open(path, 'wb') as handle:
        # Each written once the next is known: whether it is cleared, or
        # the next takes its place at once.
        for picture in disc_pictures.timeline(pictures):
            fitted = disc_pictures.fit(picture, size)
            if fitted is None:
                continue
            if previous is not None:
                handle.write(written(previous, picture['start']))
                number += 2
            indices, colours = disc_pictures.reduce(fitted[4], 256)
            previous = {'fitted': fitted, 'indices': indices, 'colours': colours, 'start': picture['start'],
                        'end': picture['end'], 'forced': bool(picture.get('forced'))}
        if previous is not None:
            handle.write(written(previous, None))
