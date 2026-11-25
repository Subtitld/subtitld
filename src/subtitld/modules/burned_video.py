"""The subtitles in the picture: burned into the video, or alone on a clear
one, drawn in a subtitle style as the preview draws them.

Each subtitle is drawn once (subtitle_pictures, the preview's renderer) at
its place on a frame the size of the video, and the drawings are put one
after the other on a clear picture track, each held for its subtitle's
time and nothing between them: an FFmpeg concat list of PNGs, kept in full
colour. That track is laid over the video and the picture encoded again
(burned in), or encoded on its own with its clear parts kept (alone).

A style that moves (its manifest's `animated`) is drawn once a subtitle,
still, for now."""

import os
import shutil
import subprocess
import tempfile

from subtitld.modules import session

# The picture codecs, by the key the export's options name them by: what
# FFmpeg writes them with, opaque and with their clear parts kept, and the
# constant rate factor of each quality, for those that take one (lower is
# better and larger).
CODECS = {
    'h264': {'args': ['-c:v', 'libx264', '-preset', 'medium'], 'pix_fmt': 'yuv420p',
             'crf': {'small': 26, 'balanced': 21, 'best': 16}},
    'hevc': {'args': ['-c:v', 'libx265', '-preset', 'medium'], 'pix_fmt': 'yuv420p',
             'crf': {'small': 30, 'balanced': 25, 'best': 20}},
    'vp9': {'args': ['-c:v', 'libvpx-vp9', '-b:v', '0', '-row-mt', '1'], 'pix_fmt': 'yuv420p',
            'alpha_pix_fmt': 'yuva420p', 'crf': {'small': 38, 'balanced': 31, 'best': 24}},
    'prores': {'args': ['-c:v', 'prores_ks', '-profile:v', '3'], 'pix_fmt': 'yuv422p10le'},
    'prores4444': {'args': ['-c:v', 'prores_ks', '-profile:v', '4444'], 'pix_fmt': 'yuv444p10le',
                   'alpha_pix_fmt': 'yuva444p10le'},
    'ffv1': {'args': ['-c:v', 'ffv1', '-level', '3'], 'pix_fmt': 'yuv420p', 'alpha_pix_fmt': 'yuva420p'},
    'png': {'args': ['-c:v', 'png'], 'pix_fmt': 'rgb24', 'alpha_pix_fmt': 'rgba'},
    'qtrle': {'args': ['-c:v', 'qtrle'], 'pix_fmt': 'rgb24', 'alpha_pix_fmt': 'argb'},
}
# The kinds of file, by the key the export and its dialog know them by: the
# codecs each takes (the first is the one chosen when none is), and, burned
# in, what the sound is written as.
CONTAINERS = {
    'BURN MP4': {'extension': 'mp4', 'format': 'mp4', 'alpha': False, 'codecs': ('h264', 'hevc'),
                 'audio': ['-c:a', 'aac', '-b:a', '192k']},
    'BURN MOV': {'extension': 'mov', 'format': 'mov', 'alpha': False, 'codecs': ('prores', 'h264'),
                 'audio': ['-c:a', 'pcm_s16le']},
    'BURN MKV': {'extension': 'mkv', 'format': 'matroska', 'alpha': False, 'codecs': ('h264', 'ffv1', 'vp9'),
                 'audio': ['-c:a', 'aac', '-b:a', '192k']},
    'BURN TS': {'extension': 'ts', 'format': 'mpegts', 'alpha': False, 'codecs': ('h264',),
                'audio': ['-c:a', 'aac', '-b:a', '192k']},
    'BURN WEBM': {'extension': 'webm', 'format': 'webm', 'alpha': False, 'codecs': ('vp9',),
                  'audio': ['-c:a', 'libopus', '-b:a', '160k']},
    'ALPHA MOV': {'extension': 'mov', 'format': 'mov', 'alpha': True, 'codecs': ('prores4444', 'png', 'qtrle')},
    'ALPHA MKV': {'extension': 'mkv', 'format': 'matroska', 'alpha': True,
                  'codecs': ('ffv1', 'vp9', 'prores4444', 'png')},
    'ALPHA WEBM': {'extension': 'webm', 'format': 'webm', 'alpha': True, 'codecs': ('vp9',)},
}
QUALITIES = ('small', 'balanced', 'best')
DEFAULT_QUALITY = 'balanced'
DEFAULT_SIZE = (1920, 1080)
DEFAULT_FPS = 25.0


def codec_of(container, codec=None):
    """`codec` if `container` takes it, else the container's first."""
    codecs = CONTAINERS[container]['codecs']
    return codec if codec in codecs else codecs[0]


def has_quality(codec):
    """Whether `codec` takes a quality: the lossless and fixed-rate ones do not."""
    return 'crf' in CODECS.get(codec, {})


def video_arguments(container, codec=None, quality=None):
    """What FFmpeg writes the picture of `container` with: `codec`, at
    `quality`, its clear parts kept when the container is an alpha one."""
    codec = codec_of(container, codec)
    spec = CODECS[codec]
    alpha = CONTAINERS[container]['alpha']
    arguments = spec['args'] + ['-pix_fmt', spec['alpha_pix_fmt'] if alpha else spec['pix_fmt']]
    if 'crf' in spec:
        arguments += ['-crf', str(spec['crf'].get(quality or DEFAULT_QUALITY, spec['crf'][DEFAULT_QUALITY]))]
    if codec == 'hevc' and container in ('BURN MP4', 'BURN MOV'):
        arguments += ['-tag:v', 'hvc1']                     # as Apple's players look for it
    return arguments


def is_burned(container):
    return container in CONTAINERS


def is_alpha(container):
    return bool(CONTAINERS.get(container, {}).get('alpha'))


def concat_list(pictures, size, duration, folder):
    """Write in `folder` the frames of the clear picture track that shows
    `pictures` (subtitle_pictures.draw's, each placed on a frame `size`)
    for their times, and the concat list that holds each for its time:
    its path. Subtitles that overlap show one after the other, and so do
    titles; a title and a subtitle (modules.cue_kinds: a picture with
    'title') share the frames they overlap in, the title under."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    width, height = int(size[0]), int(size[1])
    blank = QImage(width, height, QImage.Format_ARGB32)
    blank.fill(Qt.transparent)
    blank_path = os.path.join(folder, 'blank.png')
    blank.save(blank_path, 'PNG')
    shown = []
    for title in (True, False):
        at = 0.0
        for picture in sorted((p for p in pictures if bool(p.get('title')) == title), key=lambda p: p['start']):
            start, end = max(at, float(picture['start'])), min(float(duration), float(picture['end']))
            if end <= start:
                continue
            shown.append(dict(picture, start=start, end=end))
            at = end
    times = sorted({0.0, float(duration)} | {min(float(duration), max(0.0, float(picture[key])))
                                             for picture in shown for key in ('start', 'end')})
    entries, frames, images = [], {}, {}
    for begin, end in zip(times, times[1:]):
        if end <= begin:
            continue
        showing = tuple(number for number, picture in enumerate(shown)
                        if float(picture['start']) <= begin and float(picture['end']) >= end)
        if not showing:
            path = blank_path
        elif showing in frames:
            path = frames[showing]
        else:
            frame = QImage(width, height, QImage.Format_ARGB32)
            frame.fill(Qt.transparent)
            painter = QPainter(frame)
            for number in showing:
                picture = shown[number]
                if number not in images:
                    images[number] = QImage(picture['path'])
                painter.drawImage(int(picture['x']), int(picture['y']), images[number])
            painter.end()
            path = os.path.join(folder, f'frame{len(frames) + 1:05d}.png')
            # Lighter compression: a quarter faster to write a whole frame
            # (an animated theme is one a frame), a few KB more on disk.
            frame.save(path, 'PNG', 80)
            frames[showing] = path
        if entries and entries[-1][0] == path:
            entries[-1] = (path, entries[-1][1] + end - begin)
        else:
            entries.append((path, end - begin))
    if not entries or entries[-1][0] != blank_path:
        entries.append((blank_path, 0.04))
    lines = ['ffconcat version 1.0']
    for path, length in entries:
        lines += [f"file '{_quoted(path)}'", f'duration {length:.6f}']
    # The concat demuxer takes the last file's duration only when it is
    # named again after it.
    lines.append(f"file '{_quoted(entries[-1][0])}'")
    listed = os.path.join(folder, 'frames.ffconcat')
    with open(listed, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    return listed


def _quoted(path):
    return path.replace("'", "'\\''")


def write_video(output, container, segments, video=None, size=None, fps=None, duration=None, quality=None,
                style_id=None, codec=None):
    """Write `output`: `video` with `segments` burned into its picture, or
    (an alpha container) the subtitles alone on a clear picture `size`
    (width, height) at `fps`, `duration` seconds long; the picture in
    `codec` (CODECS), at `quality`, when the container takes it. Raises ValueError
    for a burn-in without a video, RuntimeError when the subtitles cannot
    be drawn or FFmpeg cannot write."""
    from subtitld.interface import subtitle_pictures
    holder = CONTAINERS[container]
    if not holder['alpha'] and not video:
        raise ValueError('No video loaded to burn the subtitles into')
    size = tuple(int(value) for value in (size or DEFAULT_SIZE))
    fps = float(fps or DEFAULT_FPS)
    if duration is None:
        duration = max([float(segment['end']) for segment in segments or []] + [1.0])
    folder = tempfile.mkdtemp(prefix='subtitld_burn_')
    try:
        # An animated theme a frame at a time, at the picture's rate.
        pictures = subtitle_pictures.draw(segments, size, os.path.join(folder, 'drawn'), style_id=style_id, fps=fps,
                                          span=(0.0, float(duration)))
        listed = concat_list(pictures, size, duration, folder)
        overlay = ['-f', 'concat', '-safe', '0', '-i', listed]
        if holder['alpha']:
            command = overlay + ['-vf', f'fps={fps:.6f}', '-t', f'{float(duration):.3f}']
        else:
            command = ['-i', str(video)] + overlay + [
                '-filter_complex', '[1:v]format=rgba[subtitles];[0:v:0][subtitles]overlay=eof_action=pass[out]',
                '-map', '[out]', '-map', '0:a?'] + holder['audio']
        _run(command + video_arguments(container, codec, quality) + ['-f', holder['format'], str(output)])
        if not os.path.isfile(output) or not os.path.getsize(output):
            raise RuntimeError('FFmpeg could not write the video')
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return output


def _run(arguments):
    run = subprocess.run([str(session.FFMPEG_EXECUTABLE), '-hide_banner', '-loglevel', 'error', '-y', '-nostdin']
                         + arguments, capture_output=True, startupinfo=session.STARTUPINFO, check=False)
    if run.returncode != 0:
        said = (run.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
        raise RuntimeError(said[0] if said else 'FFmpeg could not write the video')
