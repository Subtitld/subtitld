"""DivX subtitles (XSUB): pictures in an AVI's subtitle stream, read and
written.

DivX's players show subtitles drawn as pictures from a stream of their own
in the AVI (codec tags DXSB, and DXSA with opacities), which FFmpeg lists
as `xsub`. Each packet is one subtitle, as FFmpeg's xsubdec.c reads it and
xsubenc.c writes it:

    "[HH:MM:SS.mmm-HH:MM:SS.mmm]"   27 bytes, when it starts and ends, as
                                    times in the film, not offsets
    width, height, x, y, x2, y2     16-bit little-endian each
    first field's length            16-bit (xsubdec ignores it: files have
                                    bogus values)
    4 colours                       24-bit RGB, big-endian, each; DXSA
                                    then has an opacity byte for each.
                                    Colour 0 is the transparent one in
                                    DXSB, the others opaque
    the picture                     2 bits a pixel, run-length coded as a
                                    DVD subpicture's (a run of 1-3 in 4
                                    bits, 4-15 in 8, 16-63 in 12, 64-255
                                    in 16, 0 to the end of the row), each
                                    row starting on a byte; the even rows
                                    first, then the odd

`decode_packet` reads one into a picture_subtitles.Picture, for an OCR
add-on: a video's XSUB track is read packet by packet through ffprobe
(video_subtitles.extract_pictures), as a VobSub track is.

`write_video` writes a video out as an AVI with an XSUB stream for each
language. Each subtitle is drawn as the preview draws it, on the video's
frame (else DVD PAL's), made a DVD subpicture in four colours as VobSub's
export makes it (vobsub.save), and FFmpeg's xsub encoder turns those into
XSUB packets, the picture and sound copied. An AVI keeps no language
for a stream, only a name, so FFmpeg names an XSUB stream as DivX does,
"Subtitle - pt-xx;02", the language in ISO 639-1, and video_subtitles
reads the language back from it. An XSUB colour has no
opacity, so the soft edges a DVD subtitle keeps half seen come out
whole. FFmpeg's Matroska muxer does not take XSUB, so AVI is the one
container.
"""

import os
import re
import shutil
import struct
import subprocess
import tempfile

from subtitld.modules.picture_subtitles import Picture

HEADER = 27                       # the bracketed times
_TIMES = re.compile(rb'^\[(\d\d):(\d\d):(\d\d)\.(\d{3})-(\d\d):(\d\d):(\d\d)\.(\d{3})\]')
DEFAULT_SIZE = (720, 576)


class XSubError(ValueError):
    """Not an XSUB packet Subtitld can read."""


def _rows(data, offset, width, count):
    """`count` rows of 2-bit values from byte `offset`: ([bytearray], the
    byte after them)."""
    nibble = offset * 2
    limit = len(data) * 2

    def take():
        nonlocal nibble
        if nibble >= limit:
            raise IndexError
        value = data[nibble >> 1] >> 4 if nibble & 1 == 0 else data[nibble >> 1] & 15
        nibble += 1
        return value

    rows = []
    try:
        for _ in range(count):
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
                run, colour = value >> 2, value & 3
                if run == 0 or x + run > width:
                    run = width - x
                row[x:x + run] = bytes([colour]) * run
                x += run
            nibble += nibble & 1
            rows.append(row)
    except IndexError:
        pass
    while len(rows) < count:
        rows.append(bytearray(width))
    return rows, (nibble + 1) // 2


def decode_packet(data, has_alpha=False, screen=DEFAULT_SIZE):
    """The Picture of an XSUB packet, timed by the times it gives, on a
    frame of `screen`; `has_alpha` for DXSA's. Raises XSubError for a
    packet that is none."""
    data = bytes(data)
    found = _TIMES.match(data)
    if not found or len(data) < HEADER + 14 + 4 * (3 + has_alpha):
        raise XSubError('Not an XSUB packet: it does not begin with its times')
    numbers = [int(part) for part in found.groups()]
    start = numbers[0] * 3600 + numbers[1] * 60 + numbers[2] + numbers[3] / 1000
    end = numbers[4] * 3600 + numbers[5] * 60 + numbers[6] + numbers[7] / 1000
    width, height, x, y = struct.unpack('<HHHH', data[HEADER:HEADER + 8])
    if not width or not height or width > 4096 or height > 4096:
        raise XSubError(f'An XSUB picture of {width}x{height} pixels')
    position = HEADER + 14
    colours = []
    for number in range(4):
        r, g, b = data[position:position + 3]
        colours.append((r, g, b, 0 if number == 0 else 255))
        position += 3
    if has_alpha:
        colours = [(r, g, b, data[position + number]) for number, (r, g, b, _a) in enumerate(colours)]
        position += 4
    top, after = _rows(data, position, width, (height + 1) // 2)
    bottom, _end = _rows(data, after, width, height // 2)
    indices = bytearray()
    for row in range(height):
        indices += top[row // 2] if row % 2 == 0 else bottom[row // 2]
    return Picture(round(start, 3), round(max(start, end), 3), x, y, width, height, bytes(indices), colours,
                   False, int(screen[0]), int(screen[1]), '')


def pictures(packets, has_alpha=False, screen=DEFAULT_SIZE, offset=0.0):
    """The Pictures of a stream's packets (bytes each), in order of start,
    their times less `offset` (the file's start); packets that are none
    left out."""
    found = []
    for packet in packets:
        try:
            picture = decode_packet(packet, has_alpha, screen)
        except XSubError:
            continue
        picture.start, picture.end = round(picture.start - offset, 3), round(picture.end - offset, 3)
        found.append(picture)
    found.sort(key=lambda picture: (picture.start, picture.end))
    return found


# Writing ------------------------------------------------------------------

def frame_for(width=None, height=None):
    """The frame the pictures are drawn on: the video's, else DVD PAL's."""
    return (int(width), int(height)) if width and height else DEFAULT_SIZE


def mux_command(video, subtitles, output, ffmpeg='ffmpeg'):
    """The FFmpeg command that writes `video` out as the AVI `output`, its
    picture and sound copied, with an XSUB stream encoded from each VobSub
    of `subtitles` ([{'path' (its .idx), 'language' (Subtitld's code)}]),
    which FFmpeg's AVI muxer writes into the stream's name."""
    from subtitld.modules import usf
    command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y', '-nostdin', '-i', str(video)]
    for track in subtitles:
        command += ['-i', str(track['path'])]
    command += ['-map', '0:v?', '-map', '0:a?']
    for number in range(len(subtitles)):
        command += ['-map', f'{number + 1}:0']
    command += ['-c:v', 'copy', '-c:a', 'copy', '-c:s', 'xsub']
    for number, track in enumerate(subtitles):
        language = usf.iso639_2(track.get('language')) if track.get('language') else 'und'
        command += [f'-metadata:s:s:{number}', f'language={language}']
    return command + ['-f', 'avi', str(output)]


def write_video(video, output, tracks, width=None, height=None, ffmpeg='ffmpeg', startupinfo=None, draw=None):
    """Write `video` out as the AVI `output` with an XSUB stream for each of
    `tracks` ([{'segments', 'language' (Subtitld's code), 'title'}]),
    drawn on the video's frame (`width` x `height`). Raises ValueError for
    no tracks and RuntimeError with what FFmpeg said when it cannot write,
    or when this build cannot draw."""
    from subtitld.modules import vobsub
    if not tracks:
        raise ValueError('There are no subtitles to write into the video')
    if draw is None:
        from subtitld.interface.subtitle_pictures import draw
    from subtitld.interface.subtitle_pictures import rgba
    size = frame_for(width, height)
    folder = tempfile.mkdtemp(prefix='subtitld_xsub_')
    try:
        listed = []
        for number, track in enumerate(tracks):
            drawn = draw(track.get('segments') or [], size, os.path.join(folder, 'drawn'), stem=f'{number}_')
            path = os.path.join(folder, f'{number}.idx')
            vobsub.save(path, [dict(picture, rgba=rgba(picture['path'])) for picture in drawn], size,
                        language=track.get('language') or 'en')
            listed.append({'path': path, 'language': track.get('language') or '', 'title': track.get('title') or ''})
        run = subprocess.run(mux_command(video, listed, output, ffmpeg), capture_output=True, startupinfo=startupinfo,
                             check=False)
        if run.returncode != 0 or not os.path.isfile(output) or not os.path.getsize(output):
            said = (run.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
            raise RuntimeError(said[0] if said else 'FFmpeg could not write the video')
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return output
