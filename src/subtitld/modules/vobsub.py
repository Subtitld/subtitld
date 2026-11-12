"""DVD subtitles, VobSub: an .idx index and a .sub of pictures.

The .sub is an MPEG-2 program stream of DVD subpicture units (SPUs), one
per subtitle, in private stream 1 (substream 0x20 + the track). The .idx is
text: the frame size, a palette of 16 colours, and per track its language
and a line per subtitle, its time and the .sub offset its first pack is at.

`read(idx_path)` (or the .sub's) gives the pictures of one track, as
picture_subtitles.Picture: time, place on the frame, its pixels as palette indices. Each SPU
picks four of the palette's colours and an opacity for each, and holds its
picture run-length coded, interlaced: even rows, then odd rows.

`decode_spu` and `parse_program_stream` are also used for the VobSub
tracks inside videos (video_subtitles), which FFmpeg hands over packet by
packet with the .idx text as the track's extradata.

`save(path, pictures, size)` writes one: the subtitles drawn as pictures
(interface.subtitle_pictures), one on screen at a time (disc_pictures),
each cut to the four colours an SPU has: transparent, and as the drawing
has them, mostly the fill, the outline and the soft edge between. The
colours of every SPU are gathered into the .idx's palette of sixteen, the
opacity of each kept in the SPU (in sixteen steps), and each SPU is
wrapped in the 2048-byte packs of a program stream, as a DVD holds them.
"""

import os
import re
import zlib

from subtitld.modules import disc_pictures
from subtitld.modules.picture_subtitles import Picture

SPU_TICK = 1024 / 90000          # a control sequence's date unit, in seconds
DEFAULT_DURATION = 5.0           # a subtitle that never says when it ends
DEFAULT_PALETTE = [(0, 0, 0), (255, 255, 255), (128, 128, 128), (32, 32, 32)] + [(128, 128, 128)] * 12
# DVD's two frame sizes. A VobSub kept beside a video file may be as big
# as the video instead.
SIZES = {'pal': (720, 576), 'ntsc': (720, 480)}
PACK = 2048                      # a program stream pack, a DVD sector
MAX_SPU = 0xFFFF                 # an SPU says its size in 16 bits

_TIMESTAMP = re.compile(r'timestamp:\s*(-?)(\d+):(\d+):(\d+)[:.,](\d+),\s*filepos:\s*([0-9a-fA-F]+)')
_DELAY = re.compile(r'delay:\s*(-?)(\d+):(\d+):(\d+)[:.,](\d+)')


class VobSubError(ValueError):
    """Not a VobSub Subtitld can read."""


def _seconds(sign, h, m, s, ms):
    value = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    return -value if sign == '-' else value


def parse_idx(text):
    """{'size': (w, h), 'palette': [(r, g, b)] * 16, 'langidx': int,
    'tracks': [{'index', 'language', 'entries': [(seconds, filepos)]}]}"""
    info = {'size': (720, 576), 'palette': list(DEFAULT_PALETTE), 'langidx': 0, 'tracks': [], 'origin': (0, 0)}
    track = None
    delay = 0.0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        key, _, value = line.partition(':')
        key = key.strip().lower()
        value = value.strip()
        if key == 'size':
            found = re.match(r'(\d+)\s*x\s*(\d+)', value)
            if found:
                info['size'] = (int(found.group(1)), int(found.group(2)))
        elif key == 'org':
            found = re.match(r'(-?\d+)\s*,\s*(-?\d+)', value)
            if found:
                info['origin'] = (int(found.group(1)), int(found.group(2)))
        elif key == 'palette':
            colours = []
            for item in value.split(','):
                item = item.strip()
                if re.fullmatch(r'[0-9a-fA-F]{6}', item):
                    colours.append((int(item[0:2], 16), int(item[2:4], 16), int(item[4:6], 16)))
            if colours:
                info['palette'] = (colours + DEFAULT_PALETTE)[:16]
        elif key == 'langidx':
            try:
                info['langidx'] = int(value)
            except ValueError:
                pass
        elif key == 'id':
            found = re.match(r'([A-Za-z-]*)\s*(?:,\s*index:\s*(\d+))?', value)
            language = found.group(1) if found else ''
            index = int(found.group(2)) if found and found.group(2) else len(info['tracks'])
            track = {'index': index, 'language': language, 'entries': []}
            info['tracks'].append(track)
            delay = 0.0
        elif key == 'delay':
            found = _DELAY.match(line)
            if found:
                delay += _seconds(*found.groups())
        elif key == 'timestamp':
            found = _TIMESTAMP.match(line)
            if found and track is not None:
                *time, filepos = found.groups()
                track['entries'].append((max(0.0, _seconds(*time) + delay), int(filepos, 16)))
    return info


def parse_program_stream(data):
    """The SPUs in an MPEG program stream: {track: [(pack offset, pts or None, spu bytes)]},
    the offset that of the pack the SPU's first packet is in."""
    units = {}
    building = {}                   # track -> [offset, pts, bytearray, size]
    i, end = 0, len(data)
    pack = 0
    while i + 4 <= end:
        if data[i:i + 3] != b'\x00\x00\x01':
            i += 1
            continue
        code = data[i + 3]
        if code == 0xBA:            # pack header
            pack = i
            if i + 14 <= end and data[i + 4] >> 6 == 1:
                i += 14 + (data[i + 13] & 7)            # MPEG-2
            else:
                i += 12                                 # MPEG-1
            continue
        if code == 0xB9:            # program end
            i += 4
            continue
        if i + 6 > end:
            break
        length = data[i + 4] << 8 | data[i + 5]
        body = data[i + 6:i + 6 + length]
        i += 6 + length
        if code != 0xBD or len(body) < 3:
            continue
        pts = None
        if body[0] >> 6 == 2:                           # MPEG-2 PES header
            header = 3 + body[2]
            if body[1] & 0x80 and len(body) >= 8:
                pts = ((body[3] >> 1) & 7) << 30 | body[4] << 22 | (body[5] >> 1) << 15 | body[6] << 7 | body[7] >> 1
        else:                                           # MPEG-1: stuffing, then the PTS
            header = 0
            while header < len(body) and body[header] == 0xFF:
                header += 1
            if header < len(body) and body[header] >> 6 == 1:
                header += 2
            if header < len(body) and body[header] >> 4 in (2, 3):
                b = body[header:header + 5]
                if len(b) == 5:
                    pts = ((b[0] >> 1) & 7) << 30 | b[1] << 22 | (b[2] >> 1) << 15 | b[3] << 7 | b[4] >> 1
                header += 5 if body[header] >> 4 == 2 else 10
            else:
                header += 1
        if header >= len(body):
            continue
        substream = body[header]
        if not 0x20 <= substream < 0x40:
            continue
        track = substream - 0x20
        payload = body[header + 1:]
        current = building.get(track)
        if pts is not None:
            current = None          # only a unit's first packet has a PTS: an unfinished one is lost
        if current is None:
            if len(payload) < 2:
                continue
            current = [pack, pts, bytearray(), payload[0] << 8 | payload[1]]
            building[track] = current
        current[2] += payload
        if len(current[2]) >= current[3]:
            units.setdefault(track, []).append((current[0], current[1], bytes(current[2][:current[3]])))
            del building[track]
    return units


def _rle_field(spu, offset, width, rows):
    """`rows` rows of a field, from `offset`: 2-bit colour indices, a bytearray per row."""
    nibble = offset * 2
    limit = len(spu) * 2

    def take():
        nonlocal nibble
        if nibble >= limit:
            raise IndexError
        byte = spu[nibble >> 1]
        value = byte >> 4 if nibble & 1 == 0 else byte & 0x0F
        nibble += 1
        return value

    out = []
    try:
        for _ in range(rows):
            row = bytearray(width)
            x = 0
            while x < width:
                value = take()
                if value < 0x4:
                    value = value << 4 | take()
                    if value < 0x10:
                        value = value << 4 | take()
                        if value < 0x40:
                            value = value << 4 | take()
                count, colour = value >> 2, value & 3
                if count == 0 or x + count > width:
                    count = width - x
                row[x:x + count] = bytes([colour]) * count
                x += count
            if nibble & 1:
                nibble += 1                             # rows start on a byte
            out.append(row)
    except IndexError:
        pass
    while len(out) < rows:
        out.append(bytearray(width))
    return out


def decode_spu(spu, palette):
    """One subpicture unit: (picture or None, start delay, duration or None).
    The picture's start and end are offsets from the unit's own time."""
    if len(spu) < 4:
        return None, 0.0, None
    control = spu[2] << 8 | spu[3]
    colours = [3, 2, 1, 0]
    alphas = [15, 15, 15, 0]
    box = None
    offsets = None
    start = 0.0
    stop = None
    forced = False
    seen = set()
    position = control
    while position + 4 <= len(spu) and position not in seen:
        seen.add(position)
        date = (spu[position] << 8 | spu[position + 1]) * SPU_TICK
        following = spu[position + 2] << 8 | spu[position + 3]
        i = position + 4
        while i < len(spu):
            command = spu[i]
            i += 1
            if command == 0xFF:
                break
            if command in (0x00, 0x01):
                start = date
                forced = forced or command == 0x00
            elif command == 0x02:
                stop = date
            elif command == 0x03 and i + 2 <= len(spu):
                colours = [spu[i] >> 4, spu[i] & 15, spu[i + 1] >> 4, spu[i + 1] & 15]
                i += 2
            elif command == 0x04 and i + 2 <= len(spu):
                alphas = [spu[i] >> 4, spu[i] & 15, spu[i + 1] >> 4, spu[i + 1] & 15]
                i += 2
            elif command == 0x05 and i + 6 <= len(spu):
                b = spu[i:i + 6]
                box = (b[0] << 4 | b[1] >> 4, (b[1] & 15) << 8 | b[2], b[3] << 4 | b[4] >> 4, (b[4] & 15) << 8 | b[5])
                i += 6
            elif command == 0x06 and i + 4 <= len(spu):
                offsets = (spu[i] << 8 | spu[i + 1], spu[i + 2] << 8 | spu[i + 3])
                i += 4
            elif command == 0x07 and i + 2 <= len(spu):  # colour/contrast change: skipped whole
                i += spu[i] << 8 | spu[i + 1]
            else:
                break
        if following == position:
            break
        position = following
    if box is None or offsets is None:
        return None, start, None if stop is None else stop - start
    x1, x2, y1, y2 = box
    width, height = x2 - x1 + 1, y2 - y1 + 1
    if width <= 0 or height <= 0 or width > 4096 or height > 4096:
        return None, start, None if stop is None else stop - start
    top = _rle_field(spu, offsets[0], width, (height + 1) // 2)
    bottom = _rle_field(spu, offsets[1], width, height // 2)
    # The four colours in the SPU's order: index 0 is the background, the
    # commands list them from 3 down to 0.
    colours4 = []
    for k in range(4):
        r, g, b = palette[colours[3 - k] % len(palette)]
        colours4.append((r, g, b, alphas[3 - k] * 17))
    indices = bytearray()
    for y in range(height):
        indices += top[y // 2] if y % 2 == 0 else bottom[y // 2]
    picture = Picture(0.0, 0.0, x1, y1, width, height, bytes(indices), colours4, forced=forced)
    return picture, start, None if stop is None else stop - start


def pictures(units, info, track_entries=None):
    """Pictures from one track's units [(offset, pts, spu)], timed by the
    .idx entries [(seconds, filepos)] when given, else by the PTS."""
    times = {filepos: seconds for seconds, filepos in (track_entries or [])}
    width, height = info['size']
    origin_x, origin_y = info.get('origin', (0, 0))
    found = []
    for offset, pts, spu in units:
        if times:
            if offset not in times:
                continue
            base = times[offset]
        elif pts is not None:
            base = pts / 90000
        else:
            continue
        picture, start, duration = decode_spu(spu, info['palette'])
        if picture is None:
            continue
        picture.start = base + start
        picture.end = base + start + duration if duration and duration > 0 else None
        picture.x += origin_x
        picture.y += origin_y
        picture.screen_width, picture.screen_height = width, height
        found.append(picture)
    found.sort(key=lambda p: p.start)
    for current, following in zip(found, found[1:] + [None]):
        if current.end is None:
            current.end = min(current.start + DEFAULT_DURATION, following.start) if following else \
                current.start + DEFAULT_DURATION
    return found


def paths(path):
    """(.idx, .sub) for either of a VobSub's two files."""
    stem, _ = os.path.splitext(path)
    idx = next((stem + ext for ext in ('.idx', '.IDX', '.Idx') if os.path.isfile(stem + ext)), stem + '.idx')
    sub = next((stem + ext for ext in ('.sub', '.SUB', '.Sub') if os.path.isfile(stem + ext)), stem + '.sub')
    return idx, sub


def read(path, track=None):
    """The pictures of one track of the VobSub at `path` (its .idx or its
    .sub), and the track: (pictures, {'index', 'language'}). `track` is
    the track's index; by default the one the .idx names (langidx), else
    the first. Without an .idx the .sub's first track is read with DVD's
    default palette and frame size, timed by its PTS."""
    idx_path, sub_path = paths(path)
    if not os.path.isfile(sub_path):
        raise VobSubError(f'{os.path.basename(sub_path)} is missing: a VobSub needs its .sub next to its .idx')
    with open(sub_path, 'rb') as handle:
        units = parse_program_stream(handle.read())
    if os.path.isfile(idx_path):
        with open(idx_path, 'rb') as handle:
            raw = handle.read()
        info = parse_idx(raw.decode('utf-8', 'replace'))
    else:
        info = parse_idx('')
    tracks = info['tracks'] or [{'index': number, 'language': '', 'entries': []} for number in sorted(units)]
    if not tracks:
        raise VobSubError('the VobSub holds no subtitles')
    wanted = info['langidx'] if track is None else track
    chosen = next((t for t in tracks if t['index'] == wanted), None) or \
        next((t for t in tracks if t['index'] in units), tracks[0])
    found = pictures(units.get(chosen['index'], []), info, chosen['entries'])
    if not found and chosen['entries']:
        # An .idx whose offsets miss the packs (edited by hand, or remuxed): the PTS then.
        found = pictures(units.get(chosen['index'], []), info)
    return found, {'index': chosen['index'], 'language': chosen['language'],
                   'tracks': [{'index': t['index'], 'language': t['language']} for t in tracks]}


# Writing ------------------------------------------------------------------

def frame_size(choice=None, width=0, height=0, fps=0):
    """The frame a VobSub's pictures are placed on: DVD's PAL or NTSC frame
    when `choice` names one ('pal', 'ntsc'), else the video's (`width` x
    `height`), else the DVD frame of the video's rate: NTSC's for 29.97 and
    the film rates a DVD pulls down to it, PAL's for 25 and 50 and when
    there is no rate to go by."""
    if choice in SIZES:
        return SIZES[choice]
    if width and height:
        return int(width), int(height)
    if fps and min(abs(float(fps) - rate) for rate in (25, 50)) > 0.5:
        return SIZES['ntsc']
    return SIZES['pal']


def _encode_field(rows):
    """The rows of one field (2-bit values, a numpy row each) run-length
    coded: a run in 4, 8, 12 or 16 bits as its length needs, 255 pixels at
    most, the last run of a row longer than that written as "to the end of
    the line", each row starting on a byte."""
    import numpy
    nibbles = []
    for row in rows:
        width = len(row)
        edges = (numpy.flatnonzero(row[1:] != row[:-1]) + 1).tolist()
        for start, end in zip([0] + edges, edges + [width]):
            colour = int(row[start])
            run = end - start
            if end == width and run > 255:
                nibbles += (0, 0, 0, colour)
                continue
            while run:
                count = min(run, 255)
                run -= count
                value = count << 2 | colour
                digits = 1 if count < 4 else 2 if count < 16 else 3 if count < 64 else 4
                nibbles += [(value >> (4 * k)) & 15 for k in range(digits - 1, -1, -1)]
        if len(nibbles) % 2:
            nibbles.append(0)
    return bytes(nibbles[i] << 4 | nibbles[i + 1] for i in range(0, len(nibbles), 2))


def encode_spu(indices, width, height, x, y, duration, colours, alphas, forced=False):
    """An SPU of `indices` (width x height values from 0 to 3) placed at x, y
    on the frame, shown from the unit's time for `duration` seconds.
    `colours` are the palette entries of the values 0 to 3 and `alphas`
    their opacities, 0 (clear) to 15. A forced one (shown even with
    subtitles off) starts with the command DVD players keep for those."""
    import numpy
    rows = numpy.frombuffer(indices, numpy.uint8).reshape(height, width)
    top, bottom = _encode_field(rows[0::2]), _encode_field(rows[1::2])
    top_offset = 4
    bottom_offset = top_offset + len(top)
    control = bottom_offset + len(bottom)
    if control + 30 > MAX_SPU:          # and the two control sequences, 30 bytes
        raise VobSubError(f'a subtitle of {width}x{height} pixels takes more than a DVD subpicture holds')
    x2, y2 = x + width - 1, y + height - 1
    # The first control sequence, at once: the colours and their opacities
    # (the values 3, 2, 1, 0 in that order), the area, where each field's
    # rows begin, and the start. The second, `duration` later, the stop.
    first = bytes([0x03, colours[3] << 4 | colours[2], colours[1] << 4 | colours[0],
                   0x04, alphas[3] << 4 | alphas[2], alphas[1] << 4 | alphas[0],
                   0x05, x >> 4, (x & 15) << 4 | x2 >> 8, x2 & 255, y >> 4, (y & 15) << 4 | y2 >> 8, y2 & 255,
                   0x06, top_offset >> 8, top_offset & 255, bottom_offset >> 8, bottom_offset & 255,
                   0x00 if forced else 0x01, 0xFF])
    second = control + 4 + len(first)
    stop = max(1, min(0xFFFF, round(duration / SPU_TICK)))
    sequences = bytes([0, 0, second >> 8 & 255, second & 255]) + first + \
        bytes([stop >> 8, stop & 255, second >> 8 & 255, second & 255, 0x02, 0xFF])
    size = control + len(sequences)
    return bytes([size >> 8, size & 255, control >> 8, control & 255]) + top + bottom + sequences


def _pack_header(scr):
    """An MPEG-2 pack header, its clock at `scr` (90 kHz), 10.08 Mbit/s."""
    return bytes([0, 0, 1, 0xBA,
                  0x44 | (scr >> 27 & 0x38) | (scr >> 28 & 3), scr >> 20 & 255,
                  (scr >> 12 & 0xF8) | 4 | (scr >> 13 & 3), scr >> 5 & 255, (scr << 3 & 0xF8) | 4, 1,
                  0x01, 0x89, 0xC3, 0xF8])


def packs(spu, seconds, track=0):
    """`spu` in the 2048-byte packs of a program stream: a PES packet of
    private stream 1 in each, substream 0x20 + `track`, the first carrying
    the PTS. The last pack is filled out with a padding packet, or, when
    the room left is too small for one, with stuffing in its PES header."""
    pts = max(0, int(round(seconds * 90000))) & 0x1FFFFFFFF
    stamp = bytes([0x21 | (pts >> 29 & 0x0E), pts >> 22 & 255, (pts >> 14 & 0xFE) | 1, pts >> 7 & 255,
                   (pts << 1 & 0xFE) | 1])
    out = bytearray()
    position = 0
    while position < len(spu):
        header = bytes([0x81, 0x80, 0x05]) + stamp if position == 0 else bytes([0x81, 0x00, 0x00])
        room = PACK - 14 - 6 - len(header) - 1
        chunk = spu[position:position + room]
        position += len(chunk)
        gap = room - len(chunk)
        if 0 < gap < 6:
            header = header[:2] + bytes([header[2] + gap]) + header[3:] + b'\xff' * gap
            gap = 0
        body = header + bytes([0x20 + track]) + chunk
        out += _pack_header(pts) + bytes([0, 0, 1, 0xBD, len(body) >> 8, len(body) & 255]) + body
        if gap:
            out += bytes([0, 0, 1, 0xBE, (gap - 6) >> 8, (gap - 6) & 255]) + b'\xff' * (gap - 6)
    return bytes(out)


def _idx_time(seconds):
    ms = max(0, int(round(seconds * 1000)))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}:{ms % 1000:03d}'


def _language_id(language):
    """The .idx's language id: ISO 639-1, as DVDs name theirs ('pt' for 'pt-br')."""
    code = str(language or 'en').replace('_', '-').split('-')[0].lower()
    return {'cmn': 'zh', 'yue': 'zh'}.get(code, code) if code.isalpha() else 'en'


def save(path, pictures, size, language='en'):
    """Write the VobSub of `pictures` (disc_pictures' dicts, in order of
    start) on a frame of `size`: the .idx at `path` (or beside it, when
    `path` is the .sub) and the .sub beside it. One track, in `language`."""
    import numpy
    stem = os.path.splitext(path)[0]
    width, height = int(size[0]), int(size[1])
    # Each subtitle cut to four colours first, as the palette is made of
    # all of them; its pixels kept compressed until then, a film's worth
    # being large.
    units = []
    for picture in disc_pictures.timeline(pictures):
        fitted = disc_pictures.fit(picture, (width, height))
        if fitted is None:
            continue
        x, y, w, h, rgba = fitted
        indices, colours = disc_pictures.reduce(rgba, 4)
        counts = numpy.bincount(numpy.frombuffer(indices, numpy.uint8), minlength=4)
        units.append({'start': picture['start'], 'duration': picture['end'] - picture['start'],
                      'x': x, 'y': y, 'width': w, 'height': h, 'indices': zlib.compress(indices, 1),
                      'colours': colours, 'counts': counts, 'forced': bool(picture.get('forced'))})
    # The palette: every SPU's colours, the more pixels and the more opaque
    # the more they weigh, as they are when there are sixteen or fewer,
    # else the sixteen k-means finds for them.
    weights = {}
    for unit in units:
        for (r, g, b, a), count in zip(unit['colours'][1:], unit['counts'][1:]):
            weights[(r, g, b)] = weights.get((r, g, b), 0) + int(count) * a
    found = [colour for colour, weight in weights.items() if weight > 0]
    if len(found) <= 16:
        palette = found
    else:
        centres, _labels = disc_pictures.cluster(found, [weights[colour] for colour in found], 16)
        palette = [tuple(int(round(v)) for v in centre) for centre in centres]
    palette = (palette + [colour for colour in DEFAULT_PALETTE if colour not in palette])[:16]
    palette += DEFAULT_PALETTE[len(palette):]
    table = numpy.array(palette, numpy.float64)

    def entry(colour):
        return int(numpy.argmin(((table - colour[:3]) ** 2).sum(1)))

    data = bytearray()
    entries = []
    for unit in units:
        colours = [0] + [entry(colour) for colour in unit['colours'][1:]]
        alphas = [0] + [int(round(colour[3] / 17)) for colour in unit['colours'][1:]]
        colours += [0] * (4 - len(colours))
        alphas += [0] * (4 - len(alphas))
        spu = encode_spu(zlib.decompress(unit['indices']), unit['width'], unit['height'], unit['x'], unit['y'], unit['duration'],
                         colours, alphas, unit['forced'])
        entries.append((unit['start'], len(data)))
        data += packs(spu, unit['start'])
    with open(stem + '.sub', 'wb') as handle:
        handle.write(bytes(data))
    lines = ['# VobSub index file, v7 (do not modify this line!)',
             '# Written by Subtitld',
             f'size: {width}x{height}', 'org: 0, 0', 'scale: 100%, 100%', 'alpha: 100%', 'smooth: OFF',
             'fadein/out: 0, 0', 'align: OFF at LEFT TOP', 'time offset: 0', 'forced subs: OFF',
             'palette: ' + ', '.join(f'{r:02x}{g:02x}{b:02x}' for r, g, b in palette),
             'custom colors: OFF, tridx: 0000, colors: 000000, 000000, 000000, 000000',
             '', 'langidx: 0', '', f'id: {_language_id(language)}, index: 0']
    lines += [f'timestamp: {_idx_time(start)}, filepos: {position:09x}' for start, position in entries]
    with open(stem + '.idx', 'w', encoding='utf-8', newline='\n') as handle:
        handle.write('\n'.join(lines) + '\n')
    return stem + '.idx', stem + '.sub'
