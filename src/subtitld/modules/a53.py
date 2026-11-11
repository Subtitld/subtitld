"""Closed captions in a video's own frames, as ATSC broadcasts carry them
(A/53 Part 4) and every player that decodes the video finds them: each
picture carries the caption data that goes with it, CEA-608's pair for
each field and CEA-708's packets, as the user data of its picture.

- H.264 and HEVC carry it in an SEI NAL unit before the picture's slices:
  registered user data (ITU-T T.35, payload type 4) of the United States
  (B5h), ATSC's provider code (0031h), then 'GA94';
- MPEG-2 carries it as the picture's user data (start code 000001B2h),
  from 'GA94'.

What follows 'GA94' is the same in all three: user_data_type_code 3,
the count of three-byte entries, then the entries (cc_data), as the mcc
module writes them for a frame (`mcc.caption_frames`).

FFmpeg reads these captions from any video it decodes, but has no way to
write them from a caption file: its encoders pass on those a decoded
frame already has. So the picture is copied as it is and the captions
put into it here. FFmpeg copies the video stream into a transport stream,
which puts each picture in a PES packet of its own with its presentation
time; each of those is rewritten with the caption data of the frame its
time falls on (padding for a frame with none, so every picture has some,
as broadcast has it), any the video carried before taken out; and FFmpeg
copies the rewritten stream into the file asked for, with the sound of
the video, at the time the picture started at.

`write_video` does all of it; `caption_unit`, `with_captions` and
`rewrite` are its parts, which the tests read.
"""

import json
import os
import subprocess
import tempfile

from subtitld.modules import teletext

# The transport stream's stream types of the video codecs taken.
STREAM_TYPES = {0x01: 'mpeg2video', 0x02: 'mpeg2video', 0x1B: 'h264', 0x24: 'hevc'}
CODECS = frozenset(STREAM_TYPES.values())
# The kinds of file written, by the export's format.
CONTAINERS = {'MP4 A53': {'extension': 'mp4', 'format': 'mp4'}, 'TS A53': {'extension': 'ts', 'format': 'mpegts'}}
_PADDING = (0xFA, 0x00, 0x00)
_NO_CAPTIONS = ((0xFC, 0x80, 0x80), (0xFD, 0x80, 0x80))
_WRAP = 1 << 33


def user_data(triplets):
    """A/53's caption user data from 'GA94': type 3, the count of entries
    (process_cc_data_flag set), the em_data byte, the entries, the marker."""
    triplets = list(triplets)[:31]
    return b'GA94\x03' + bytes((0xC0 | len(triplets), 0xFF)) + b''.join(bytes(t) for t in triplets) + b'\xff'


def _escaped(data):
    """A NAL unit's payload with emulation prevention: a 03h after two
    zeros that a byte of 3 or less follows."""
    out, zeros = bytearray(), 0
    for byte in data:
        if zeros >= 2 and byte <= 0x03:
            out.append(0x03)
            zeros = 0
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


def caption_unit(codec, triplets):
    """The bytes that carry a frame's caption data `triplets` in a video of
    `codec` ('h264', 'hevc', 'mpeg2video'), its start code first."""
    data = user_data(triplets)
    if codec == 'mpeg2video':
        return b'\x00\x00\x01\xb2' + data
    message = b'\xb5\x00\x31' + data
    payload = bytes([4]) + b'\xff' * (len(message) // 255) + bytes([len(message) % 255]) + message + b'\x80'
    header = bytes((39 << 1, 0x01)) if codec == 'hevc' else b'\x06'       # HEVC's prefix SEI; H.264's SEI
    return b'\x00\x00\x00\x01' + header + _escaped(payload)


def _units(data):
    """[(begin, body, end)] of each unit after a start code in `data`: where
    its start code begins (a 4-byte one's leading zero too), where the unit
    itself does, and where the next begins."""
    starts, index = [], data.find(b'\x00\x00\x01')
    while index >= 0:
        starts.append((index - 1 if index and data[index - 1] == 0 else index, index + 3))
        index = data.find(b'\x00\x00\x01', index + 3)
    return [(begin, body, starts[number + 1][0] if number + 1 < len(starts) else len(data))
            for number, (begin, body) in enumerate(starts)]


def _is_picture(codec, unit):
    """Whether a unit is (the first of) the picture's own data: a slice."""
    if not unit:
        return False
    if codec == 'h264':
        return 1 <= unit[0] & 0x1F <= 5
    if codec == 'hevc':
        return (unit[0] >> 1) & 0x3F < 32
    return 0x01 <= unit[0] <= 0xAF


def _is_captions(codec, unit):
    """Whether a unit is caption user data: an SEI whose message is A/53's
    registered user data, or MPEG-2 user data from 'GA94'."""
    if codec == 'mpeg2video':
        return unit[:5] == b'\xb2GA94'
    if codec == 'h264' and unit[:1] == b'\x06':
        message = unit[1:]
    elif codec == 'hevc' and len(unit) > 2 and (unit[0] >> 1) & 0x3F == 39:
        message = unit[2:]
    else:
        return False
    return message[:1] == b'\x04' and b'\xb5\x00\x31GA94' in message[:12]


def with_captions(codec, access_unit, triplets):
    """A picture's data (an access unit, its units after start codes) with
    its caption data `triplets` put before its first slice, and any it had
    taken out. A unit with no slice is left as it is."""
    units = _units(access_unit)
    out, placed = bytearray(access_unit[:units[0][0]] if units else access_unit), False
    for begin, body, end in units:
        unit = access_unit[body:end]
        if _is_captions(codec, unit):
            continue
        if not placed and _is_picture(codec, unit):
            out += caption_unit(codec, triplets)
            placed = True
        out += access_unit[begin:end]
    return bytes(out) if placed else access_unit


def _clock_packet(packet, counter):
    """A transport stream packet of `packet`'s clock (PCR) alone, no payload,
    its continuity counter the last one sent."""
    return bytes((0x47, packet[1] & 0x1F, packet[2], 0x20 | (counter & 0x0F), 183, 0x10)) + packet[6:12] + \
        b'\xff' * 176


def video_stream(path):
    """(PID, codec, the earliest PTS) of the first video stream of the
    transport stream at `path` that A/53 captions go in; None for none."""
    pmt_pids, sections, pid, codec, earliest = set(), {}, None, None, None
    with open(path, 'rb') as stream:
        for found, start, payload in teletext._packets(stream):
            if pid is None and (found == 0 or found in pmt_pids):
                if start:
                    sections[found] = bytearray(teletext._section_parts(payload, True))
                elif found in sections:
                    sections[found] += payload
                section = sections.get(found)
                if not section or len(section) < 3 or len(section) < 3 + (((section[1] & 0x0F) << 8) | section[2]):
                    continue
                if found == 0 and section[0] == 0x00:
                    end = 3 + (((section[1] & 0x0F) << 8) | section[2]) - 4
                    for index in range(8, end - 3, 4):
                        if (section[index] << 8) | section[index + 1]:
                            pmt_pids.add(((section[index + 2] & 0x1F) << 8) | section[index + 3])
                elif section[0] == 0x02:
                    for stream_pid, info in teletext._pmt_streams(bytes(section)).items():
                        if info['type'] in STREAM_TYPES:
                            pid, codec = stream_pid, STREAM_TYPES[info['type']]
                            break
                del sections[found]
            elif found == pid and start:
                pts = teletext._pes_pts(payload)
                if pts is not None and (earliest is None or (pts - earliest) % _WRAP > _WRAP // 2):
                    earliest = pts
    return (pid, codec, earliest) if pid is not None else None


def rewrite(source, target, pid, transform):
    """Copy the transport stream `source` to `target`, each PES packet of
    `pid` (a picture) with its payload `transform(pts, payload)`, sent
    again in packets of its own where it ended; the clock its packets
    carried is sent where it was, in packets of the clock alone."""
    counters = {}
    with open(source, 'rb') as stream, open(target, 'wb') as out:
        layout = teletext._packet_layout(stream.read(204 * 6))
        if layout is None:
            raise ValueError('Not a transport stream')
        size, offset = layout
        stream.seek(offset)
        pending = None

        def flush():
            pes = bytes(pending)
            if pes[:3] != b'\x00\x00\x01' or len(pes) < 9:
                return
            begin = 9 + pes[8]
            payload = transform(teletext._pes_pts(pes), pes[begin:])
            length = begin - 6 + len(payload)
            length = length if pes[4:6] != b'\x00\x00' and length <= 0xFFFF else 0
            out.write(b''.join(teletext._ts_packets(pid, pes[:4] + length.to_bytes(2, 'big') + pes[6:begin] + payload,
                                                    counters)))

        while True:
            chunk = stream.read(size * 4096)
            if len(chunk) < 188:
                break
            for start in range(0, len(chunk) - 187, size):
                packet = chunk[start:start + 188]
                if packet[0] != 0x47 or ((packet[1] & 0x1F) << 8 | packet[2]) != pid:
                    out.write(packet)
                    continue
                control = (packet[3] >> 4) & 0x03
                counters.setdefault(pid, packet[3] & 0x0F)
                if control & 0x02 and packet[4] and packet[5] & 0x10:
                    out.write(_clock_packet(packet, counters[pid] - 1))
                if not control & 0x01:
                    continue
                payload = packet[4 + (packet[4] + 1 if control & 0x02 else 0):]
                if packet[1] & 0x40:
                    if pending is not None:
                        flush()
                    pending = bytearray(payload)
                elif pending is not None:
                    pending += payload
        if pending is not None:
            flush()


def probe(path, ffprobe='ffprobe', startupinfo=None):
    """(the video's codec, its frame rate, when its picture starts after
    the file does, in seconds) of the video at `path`."""
    result = subprocess.run([str(ffprobe), '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams',
                             str(path)], capture_output=True, startupinfo=startupinfo, check=False)
    metadata = json.loads(result.stdout or b'{}')
    start = float(metadata.get('format', {}).get('start_time') or 0)
    for stream in metadata.get('streams', []):
        if stream.get('codec_type') == 'video' and stream.get('codec_name') not in ('png', 'mjpeg'):
            rate = 0.0
            try:
                numerator, denominator = (float(part) for part in str(stream.get('avg_frame_rate')).split('/'))
                rate = numerator / denominator if denominator else 0.0
            except ValueError:
                pass
            return str(stream.get('codec_name') or ''), rate, float(stream.get('start_time') or start) - start
    return '', 0.0, 0.0


def _run(command, startupinfo=None):
    run = subprocess.run(command, capture_output=True, startupinfo=startupinfo, check=False)
    if run.returncode != 0:
        said = (run.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
        raise RuntimeError(said[0] if said else 'FFmpeg could not write the video')


def write_video(video, output, frames, count, fps, container='MP4 A53', ffmpeg='ffmpeg', ffprobe='ffprobe',
                startupinfo=None):
    """Write `video` out as `output` with the caption data `frames` ({frame:
    [(marker, byte, byte)]} at `fps` frames a second, `count` entries a
    frame, as mcc.caption_frames gives them) in its pictures, its picture
    and sound copied. Raises ValueError for a video whose codec takes no
    A/53 captions, and RuntimeError with what FFmpeg said when it cannot
    write the file."""
    codec, _rate, offset = probe(video, ffprobe, startupinfo)
    if codec not in CODECS:
        raise ValueError(f'Captions go in the frames of H.264, HEVC or MPEG-2 video, not {codec or "this video"}')
    holder = CONTAINERS.get(container, CONTAINERS['MP4 A53'])
    folder = tempfile.mkdtemp(prefix='subtitld_a53_')
    copied, rewritten = os.path.join(folder, 'video.ts'), os.path.join(folder, 'captioned.ts')
    try:
        _run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y', '-nostdin', '-i', str(video), '-map', '0:v:0',
              '-c', 'copy', '-f', 'mpegts', copied], startupinfo)
        found = video_stream(copied)
        if found is None or found[2] is None:
            raise RuntimeError('FFmpeg wrote no video to put the captions in')
        pid, codec, earliest = found

        def transform(pts, payload):
            # The frame this picture is shown on, counted from the file's start.
            seconds = ((pts - earliest) % _WRAP) / 90000.0 + offset if pts is not None else 0.0
            triplets = list(frames.get(int(round(seconds * fps)), _NO_CAPTIONS))
            return with_captions(codec, payload, triplets + [_PADDING] * (count - len(triplets)))

        rewrite(copied, rewritten, pid, transform)
        _run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y', '-nostdin', '-itsoffset', f'{offset:.6f}',
              '-i', rewritten, '-i', str(video), '-map', '0:v:0', '-map', '1:a?', '-c', 'copy',
              '-f', holder['format'], str(output)], startupinfo)
        if not os.path.isfile(output) or not os.path.getsize(output):
            raise RuntimeError('FFmpeg could not write the video')
    finally:
        for path in (copied, rewritten):
            try:
                os.unlink(path)
            except OSError:
                pass
        try:
            os.rmdir(folder)
        except OSError:
            pass
    return output
