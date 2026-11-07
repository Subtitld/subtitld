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
"""

import os
import re

from subtitld.modules.picture_subtitles import Picture

SPU_TICK = 1024 / 90000          # a control sequence's date unit, in seconds
DEFAULT_DURATION = 5.0           # a subtitle that never says when it ends
DEFAULT_PALETTE = [(0, 0, 0), (255, 255, 255), (128, 128, 128), (32, 32, 32)] + [(128, 128, 128)] * 12

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
