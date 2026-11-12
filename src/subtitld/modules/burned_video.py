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

# The kinds of file, by the key the export and its dialog know them by.
CONTAINERS = {
    'BURN MP4': {'extension': 'mp4', 'alpha': False,
                 'video': ['-c:v', 'libx264', '-preset', 'medium', '-pix_fmt', 'yuv420p'],
                 'audio': ['-c:a', 'aac', '-b:a', '192k'], 'format': 'mp4'},
    'BURN MOV': {'extension': 'mov', 'alpha': False,
                 'video': ['-c:v', 'prores_ks', '-profile:v', '3', '-pix_fmt', 'yuv422p10le'],
                 'audio': ['-c:a', 'pcm_s16le'], 'format': 'mov'},
    'ALPHA MOV': {'extension': 'mov', 'alpha': True,
                  'video': ['-c:v', 'prores_ks', '-profile:v', '4444', '-pix_fmt', 'yuva444p10le'], 'format': 'mov'},
    'ALPHA WEBM': {'extension': 'webm', 'alpha': True,
                   'video': ['-c:v', 'libvpx-vp9', '-pix_fmt', 'yuva420p', '-b:v', '0', '-crf', '30', '-row-mt', '1'],
                   'format': 'webm'},
}
# H.264's constant rate factor for each quality: lower is better and larger.
QUALITIES = {'small': 26, 'balanced': 21, 'best': 16}
DEFAULT_QUALITY = 'balanced'
DEFAULT_SIZE = (1920, 1080)
DEFAULT_FPS = 25.0


def is_burned(container):
    return container in CONTAINERS


def is_alpha(container):
    return bool(CONTAINERS.get(container, {}).get('alpha'))


def concat_list(pictures, size, duration, folder):
    """Write in `folder` the frames of the clear picture track that shows
    `pictures` (subtitle_pictures.draw's, each placed on a frame `size`)
    for their times, and the concat list that holds each for its time:
    its path. Subtitles that overlap show one after the other."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    width, height = int(size[0]), int(size[1])
    blank = QImage(width, height, QImage.Format_ARGB32)
    blank.fill(Qt.transparent)
    blank_path = os.path.join(folder, 'blank.png')
    blank.save(blank_path, 'PNG')
    entries, at = [], 0.0
    for number, picture in enumerate(sorted(pictures, key=lambda p: p['start']), start=1):
        start, end = max(at, float(picture['start'])), min(float(duration), float(picture['end']))
        if end <= start:
            continue
        frame = QImage(width, height, QImage.Format_ARGB32)
        frame.fill(Qt.transparent)
        painter = QPainter(frame)
        painter.drawImage(int(picture['x']), int(picture['y']), QImage(picture['path']))
        painter.end()
        path = os.path.join(folder, f'frame{number:05d}.png')
        frame.save(path, 'PNG')
        if start > at:
            entries.append((blank_path, start - at))
        entries.append((path, end - start))
        at = end
    entries.append((blank_path, max(0.04, float(duration) - at)))
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
                style_id=None):
    """Write `output`: `video` with `segments` burned into its picture, or
    (an alpha container) the subtitles alone on a clear picture `size`
    (width, height) at `fps`, `duration` seconds long. Raises ValueError
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
        pictures = subtitle_pictures.draw(segments, size, os.path.join(folder, 'drawn'), style_id=style_id)
        listed = concat_list(pictures, size, duration, folder)
        overlay = ['-f', 'concat', '-safe', '0', '-i', listed]
        if holder['alpha']:
            command = overlay + ['-vf', f'fps={fps:.6f},format={_alpha_format(holder)}', '-t', f'{float(duration):.3f}']
        else:
            crf = QUALITIES.get(quality or DEFAULT_QUALITY, QUALITIES[DEFAULT_QUALITY])
            command = ['-i', str(video)] + overlay + [
                '-filter_complex', '[1:v]format=rgba[subtitles];[0:v:0][subtitles]overlay=eof_action=pass[out]',
                '-map', '[out]', '-map', '0:a?'] + holder['audio']
            if container == 'BURN MP4':
                command += ['-crf', str(crf)]
        _run(command + holder['video'] + ['-f', holder['format'], str(output)])
        if not os.path.isfile(output) or not os.path.getsize(output):
            raise RuntimeError('FFmpeg could not write the video')
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return output


def _alpha_format(holder):
    return holder['video'][holder['video'].index('-pix_fmt') + 1]


def _run(arguments):
    run = subprocess.run([str(session.FFMPEG_EXECUTABLE), '-hide_banner', '-loglevel', 'error', '-y', '-nostdin']
                         + arguments, capture_output=True, startupinfo=session.STARTUPINFO, check=False)
    if run.returncode != 0:
        said = (run.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
        raise RuntimeError(said[0] if said else 'FFmpeg could not write the video')
