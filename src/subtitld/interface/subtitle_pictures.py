"""Subtitles drawn as pictures, for a file that carries pictures (IMSC's
Image Profile) and for a video they are burned into (modules.burned_video).

The preview's renderer (subtitle_renderer) draws each subtitle as the
preview shows it, in the chosen style, on a picture of the size asked for,
and gives back the subtitle cut out of that picture and where it was on it:
which is what a picture subtitle is. It runs in whatever thread asks, with
an event loop of its own while it waits for the helper.

Its layers are not kept: they are written out as they come, a few at a
time, so a long film does not hold all its pictures at once.

A cue whose theme is animated (modules.themes: an intro and an outro) is
drawn once settled, after its intro: a picture for a file. For a video
(`fps` given), its intro and outro a picture a frame, the time between
them one picture: a theme holds still between its intro and its outro.
Unless the cue is timed word by word: then a picture from each word's
start, and a picture a frame while a word moves as it is sung (the
theme's timing "sung").
"""

import os
import re
import time

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QImage

from subtitld.interface import subtitle_renderer
from subtitld.modules import markup, themes
from subtitld.modules.segment import text_of, timed_pieces

BATCH = 8                    # subtitles sent to the helper at once
WAIT = 60.0                  # seconds one batch may take before it is given up on


def moments(segment, fps=None, style_id=None):
    """[(the moment drawn, from, until)] in seconds: the pictures a cue is,
    each drawn at its moment and shown from `from` to `until`. One, over
    the whole cue, unless its theme is animated: then the one settled
    after the intro, or (`fps`) a frame each through the intro and the
    outro and one for the time between. `style_id`: the style every
    subtitle is drawn in, as draw() takes it."""
    start, end = float(segment['start']), float(segment['end'])
    timing = themes.animation(segment, style_id)
    if not timing:
        return [(start, start, end)]
    intro, outro = timing
    settled = min(end, start + intro)
    if not fps:
        return [(settled, start, end)]
    leaving = max(settled, end - outro)
    out = []

    def frames(first, last):
        number = 0
        while True:
            at = first + number / fps
            if at >= last - 1e-6:
                return
            out.append((at, at, min(last, first + (number + 1) / fps)))
            number += 1
    frames(start, settled)
    middle(settled, leaving, segment, style_id, out, frames)
    frames(leaving, end)
    return out


def middle(first, last, segment, style_id, out, frames):
    """The pictures of an animated cue between its intro and its outro, into
    `out`: one, unless it is timed word by word: then one from each word's
    start, and `frames` while a word moves as it is sung."""
    if last <= first + 1e-6:
        return
    start = float(segment['start'])
    starts = sorted({start + piece['start'] for piece in timed_pieces(segment) or []
                     if piece['end'] > piece['start']})
    sung = themes.sung(segment, style_id) if starts else None
    moving = []
    for at in starts if sung else []:
        a, b = max(first, at), min(last, at + sung)
        if b <= a + 1e-6:
            continue
        if moving and a <= moving[-1][1] + 1e-6:
            moving[-1] = (moving[-1][0], max(moving[-1][1], b))
        else:
            moving.append((a, b))

    def hold(a, b):
        cuts = [a] + [at for at in starts if a + 1e-6 < at < b - 1e-6] + [b]
        for begin, until in zip(cuts, cuts[1:]):
            out.append((begin, begin, until))
    cursor = first
    for a, b in moving:
        if a > cursor + 1e-6:
            hold(cursor, a)
        frames(a, b)
        cursor = b
    if last > cursor + 1e-6:
        hold(cursor, last)


def draw(segments, size, folder, stem='', wait=WAIT, style_id=None, fps=None, span=None):
    """Draw `segments` on a picture `size` (width, height) pixels, in the
    style `style_id` for all of them (each its own, else the project's,
    when None), and write
    each as a PNG in `folder`: [{'start', 'end', 'path', 'x', 'y', 'width',
    'height', 'alt'[, 'forced']}], in the order of the subtitles, leaving
    out those with nothing to draw. An animated cue is a picture a frame of
    its intro and outro at `fps` (moments()); with `span` (from, until),
    only the pictures shown within it are drawn. `stem` begins each file's name; the
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
    renderer.style_id = style_id
    # A style asked for is every subtitle's, whatever theme it has of its own.
    renderer.one_style = style_id is not None
    renderer.fps = fps
    pictures = []
    try:
        style = renderer._style()
        drawable = [(segment, moment) for segment in segments if markup.plain(text_of(segment)).strip()
                    for moment in moments(segment, fps, style_id)
                    if span is None or (moment[2] > span[0] and moment[1] < span[1])]
        for first in range(0, len(drawable), BATCH):
            batch = drawable[first:first + BATCH]
            keys = [renderer._job(segment, at, *style)[0] for segment, (at, _from, _until) in batch]
            deadline = time.monotonic() + wait
            while not all(key in renderer.cache for key in keys):
                if time.monotonic() > deadline:
                    raise RuntimeError('The renderer did not draw the subtitles in time')
                for segment, (at, _from, _until) in batch:
                    renderer.layers([segment], at)
                loop = QEventLoop()
                QTimer.singleShot(20, loop.quit)
                loop.exec()
            # Two subtitles that show the same text the same way share a
            # key, and a layer: taken out of the cache once both are written.
            for number, ((segment, (_at, begin, until)), key) in enumerate(zip(batch, keys), start=first + 1):
                rect, image = renderer.cache[key]
                if rect is None or image is None:
                    continue
                path = os.path.join(folder, f'{stem}{number:05d}.png')
                if not image.save(path, 'PNG'):
                    raise OSError(f'Could not write {path}')
                picture = {'start': begin, 'end': until, 'path': path,
                           'x': int(round(rect.x() * renderer.size[0])), 'y': int(round(rect.y() * renderer.size[1])),
                           'width': image.width(), 'height': image.height(),
                           # The words it shows, which a player may speak or
                           # show in place of the picture.
                           'alt': markup.plain(markup.split_lead(text_of(segment))[1]).strip()}
                if segment.get('forced'):
                    picture['forced'] = True
                if segment.get('kind') == 'title':
                    # Drawn under a subtitle that shows with it (burned_video).
                    picture['title'] = True
                pictures.append(picture)
            for key in set(keys):
                image = renderer.cache.pop(key)[1]
                if image is not None:
                    renderer.bytes -= image.sizeInBytes()
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
