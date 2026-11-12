"""Subtitles drawn as pictures, for a file that carries pictures (IMSC's Image Profile).

The preview's renderer (subtitle_renderer) draws each subtitle as the
preview shows it, in the chosen style, on a picture of the size asked for,
and gives back the subtitle cut out of that picture and where it was on it:
which is what a picture subtitle is. It runs in whatever thread asks, with
an event loop of its own while it waits for the helper.

Its layers are not kept: they are written out as they come, a few at a
time, so a long film does not hold all its pictures at once.
"""

import os
import re
import time

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QImage

from subtitld.interface import subtitle_renderer
from subtitld.modules import markup

BATCH = 8                    # subtitles sent to the helper at once
WAIT = 60.0                  # seconds one batch may take before it is given up on


def draw(segments, size, folder, stem='', wait=WAIT):
    """Draw `segments` on a picture `size` (width, height) pixels and write
    each as a PNG in `folder`: [{'start', 'end', 'path', 'x', 'y', 'width',
    'height', 'alt'[, 'forced']}], in the order of the subtitles, leaving
    out those with nothing to draw. `stem` begins each file's name; the
    files a drawing before left there by the same names are removed.

    Raises RuntimeError when this build cannot draw (no QtWebEngine) or the
    helper does not answer."""
    if not subtitle_renderer.available():
        raise RuntimeError('This build of Subtitld has no renderer to draw subtitles with')
    os.makedirs(folder, exist_ok=True)
    # The pictures of an export before, which a shorter one would leave
    # behind: its own names only, nothing else in the folder.
    for name in os.listdir(folder):
        if re.fullmatch(re.escape(stem) + r'\d{5}\.png', name):
            os.remove(os.path.join(folder, name))
    renderer = subtitle_renderer.SubtitleRenderer(disk_cache=None)
    renderer.size = (int(size[0]), int(size[1]))
    pictures = []
    try:
        style = renderer._style()
        drawable = [segment for segment in segments if markup.plain(str(segment.get('text') or '')).strip()]
        for first in range(0, len(drawable), BATCH):
            batch = drawable[first:first + BATCH]
            keys = [renderer._job(segment, float(segment['start']), *style)[0] for segment in batch]
            deadline = time.monotonic() + wait
            while not all(key in renderer.cache for key in keys):
                if time.monotonic() > deadline:
                    raise RuntimeError('The renderer did not draw the subtitles in time')
                for segment in batch:
                    renderer.layers([segment], float(segment['start']))
                loop = QEventLoop()
                QTimer.singleShot(20, loop.quit)
                loop.exec()
            for number, (segment, key) in enumerate(zip(batch, keys), start=first + 1):
                rect, image = renderer.cache.pop(key)
                if image is not None:
                    renderer.bytes -= image.sizeInBytes()
                if rect is None or image is None:
                    continue
                path = os.path.join(folder, f'{stem}{number:05d}.png')
                if not image.save(path, 'PNG'):
                    raise OSError(f'Could not write {path}')
                picture = {'start': float(segment['start']), 'end': float(segment['end']), 'path': path,
                           'x': int(round(rect.x() * renderer.size[0])), 'y': int(round(rect.y() * renderer.size[1])),
                           'width': image.width(), 'height': image.height(),
                           # The words it shows, which a player may speak or
                           # show in place of the picture.
                           'alt': markup.plain(markup.split_lead(str(segment.get('text') or ''))[1]).strip()}
                if segment.get('forced'):
                    picture['forced'] = True
                pictures.append(picture)
    finally:
        renderer.close()
    return pictures


def rgba(path):
    """The pixels of a PNG `draw` wrote, for the encoders of a disc's
    pictures (vobsub, pgs): four bytes a pixel, row after row, alpha not
    premultiplied."""
    image = QImage(path)
    if image.isNull():
        raise OSError(f'Could not read {path}')
    image = image.convertToFormat(QImage.Format_RGBA8888)
    width, height, line = image.width(), image.height(), image.bytesPerLine()
    data = bytes(image.constBits())[:line * height]
    if line == width * 4:
        return data
    return b''.join(data[row * line:row * line + width * 4] for row in range(height))
