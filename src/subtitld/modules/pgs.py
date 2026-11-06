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
                cropped = payload[i + 3] & 0x80          # 0x40 is "forced"
                x, y = payload[i + 4] << 8 | payload[i + 5], payload[i + 6] << 8 | payload[i + 7]
                crop = None
                i += 8
                if cropped and i + 8 <= len(payload):
                    crop = tuple(payload[i + k] << 8 | payload[i + k + 1] for k in (0, 2, 4, 6))
                    i += 8
                placed.append((object_id, x, y, crop))
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
    for object_id, x, y, crop in placed:
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
    picture = Picture(when, None, left, top, width, height, bytes(canvas), colours)
    picture.screen_width, picture.screen_height = screen
    return picture


def read(path):
    with open(path, 'rb') as handle:
        data = handle.read()
    if data[:2] != b'PG':
        raise PGSError(f'{path} is not a PGS (.sup) file')
    return parse(data)
