"""A look's SVG shapes (modules.timeline_svg) and frames
(modules.timeline_frames) as the timelines draw them.

The vertical timeline draws them as the timeline does, mirrored across
the diagonal (`mirrored`): what runs along the timeline's time runs down,
the timeline's top is the left. A shape drawn for the timeline is drawn
there that way, unchanged.
"""

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF, QTransform

from subtitld.modules import session, timeline_frames, timeline_svg

# x and y swapped: the timeline's drawing as the vertical timeline's.
MIRROR = QTransform(0, 1, 1, 0, 0, 0)


def shape(widget, name, width, height, color):
    """A look's SVG shape as an image this size in `color`, made once, and
    how opaque to draw it; None without one that can be drawn."""
    if not name:
        return None
    image = timeline_svg.image(name, width, height, color.name(QColor.HexRgb), widget.devicePixelRatioF())
    if image is None:
        return None
    # Its currentColor took the colour's red, green and blue: its alpha is
    # the image's opacity. A shape in colours of its own is drawn as it is.
    return image, color.alphaF() if timeline_svg.takes_color(name) else 1.0


def natural_shape(widget, name, color, height=None):
    """A look's SVG shape at its own size (or this tall, its own width), and
    that width; (None, 0) without one."""
    width, natural_height = timeline_svg.natural_size(name) if name else (0.0, 0.0)
    if width <= 0:
        return None, 0.0
    return shape(widget, name, width, natural_height if height is None else height, color), width


def tile(painter, drawn, rect, width):
    """An SVG shape repeated along `rect`, each `width` wide, from the
    timeline's start (so it scrolls with it)."""
    image, opacity = drawn
    painter.save()
    painter.setOpacity(painter.opacity() * opacity)
    painter.drawTiledPixmap(rect, image, QPointF(rect.left() % width, 0))
    painter.restore()


def draw(painter, drawn, x, y):
    image, opacity = drawn
    painter.save()
    painter.setOpacity(painter.opacity() * opacity)
    painter.drawPixmap(QPointF(x, y), image)
    painter.restore()


class mirrored:
    """`with mirrored(painter, x, y):` the timeline's drawing from (x, y) on,
    as the vertical timeline's."""

    def __init__(self, painter, x=0.0, y=0.0):
        self.painter, self.x, self.y = painter, x, y

    def __enter__(self):
        self.painter.save()
        self.painter.translate(self.x, self.y)
        self.painter.setTransform(MIRROR, True)
        return self.painter

    def __exit__(self, *exc):
        self.painter.restore()


def swap(rect):
    """A rectangle of the vertical timeline as the timeline's, mirrored."""
    return QRectF(rect.top(), rect.left(), rect.height(), rect.width())


def frame(widget, painter, look_name, rect, color, speaker=None, vertical=False):
    """A frame drawn as a box in `rect`; False without one to draw."""
    found = timeline_frames.get(look_name)
    if found is None:
        return False
    hex_color = color.name(QColor.HexRgb)
    if vertical:
        with mirrored(painter):
            timeline_frames.paint(painter, found, swap(rect), hex_color, widget.devicePixelRatioF(), look_name, speaker,
                                  color.alphaF())
    else:
        timeline_frames.paint(painter, found, rect, hex_color, widget.devicePixelRatioF(), look_name, speaker,
                              color.alphaF())
    return True


def waveform_polygon(level, first, last, pixel_per_bucket, origin, center, amplitude, vertical=False):
    """The waveform's shape over buckets [first, last) of a level (mins, maxs,
    samples a bucket): a bucket's min and max at its middle, the time they
    stand for, `origin` px of the timeline before the image's start; at the
    audio's very start and end, its first and last reach the edge. Along x,
    the max up; `vertical`, along y, the max to the right (as the vertical
    timeline has always drawn it)."""
    all_mins, all_maxs, _per_bucket = level
    along = (first + 0.5 + np.arange(last - first)) * pixel_per_bucket - origin
    mins = np.asarray(all_mins[first:last], dtype=np.float64)
    maxs = np.asarray(all_maxs[first:last], dtype=np.float64)
    if first == 0:
        along, mins, maxs = np.r_[-origin, along], np.r_[mins[0], mins], np.r_[maxs[0], maxs]
    if last == len(all_mins):
        along = np.r_[along, len(all_mins) * pixel_per_bucket - origin]
        mins, maxs = np.r_[mins, mins[-1]], np.r_[maxs, maxs[-1]]
    sign = 1.0 if vertical else -1.0
    high, low = center + sign * maxs * amplitude, center + sign * mins * amplitude
    there, back = zip(along.tolist(), high.tolist()), zip(along[::-1].tolist(), low[::-1].tolist())
    if vertical:
        return QPolygonF([QPointF(a, t) for t, a in there] + [QPointF(a, t) for t, a in back])
    return QPolygonF([QPointF(t, a) for t, a in there] + [QPointF(t, a) for t, a in back])


def waveform_bottom_polygon(level, first, last, pixel_per_bucket, origin, base, amplitude, vertical=False):
    """The waveform as one-sided peaks standing on `base` (the bottom style):
    a bucket's larger of -min and max, up from it; `vertical`, the vertical
    timeline's mirror of that, leftwards from its right edge."""
    all_mins, all_maxs, _per_bucket = level
    along = (first + 0.5 + np.arange(last - first)) * pixel_per_bucket - origin
    peaks = np.maximum(-np.asarray(all_mins[first:last], dtype=np.float64),
                       np.asarray(all_maxs[first:last], dtype=np.float64))
    peaks = np.clip(peaks, 0.0, None)
    if first == 0:
        along, peaks = np.r_[-origin, along], np.r_[peaks[0], peaks]
    if last == len(all_mins):
        along, peaks = np.r_[along, len(all_mins) * pixel_per_bucket - origin], np.r_[peaks, peaks[-1]]
    high = base - peaks * amplitude
    points = list(zip(along.tolist(), high.tolist())) + [(along[-1], base), (along[0], base)]
    if vertical:
        return QPolygonF([QPointF(a, t) for t, a in points])
    return QPolygonF([QPointF(t, a) for t, a in points])


def waveform_setting():
    """How the waveform is drawn, a setting of the user's, not of the timeline's
    look: 'normal', 'bottom' or 'spectral', and how opaque (0 to 1)."""
    saved = session.CONFIG.get('waveform', {}) or {}
    style = saved.get('style', 'normal')
    if style not in WAVEFORM_STYLES:
        style = 'normal'
    try:
        opacity = min(100, max(0, int(saved.get('opacity', 100)))) / 100.0
    except (TypeError, ValueError):
        opacity = 1.0
    return style, opacity


WAVEFORM_STYLES = ('normal', 'bottom', 'spectral')

# The spectrogram: a frame of about 20 ms a column, its frequencies on the mel
# scale up to SPECTRAL_TOP_HZ, its loudness from SPECTRAL_FLOOR_DB to
# SPECTRAL_CEILING_DB of full scale; at most SPECTRAL_FRAMES frames a column
# when a column stands for more audio than a frame (the loudest of them).
SPECTRAL_TOP_HZ = 11000.0
SPECTRAL_FLOOR_DB = -80.0
SPECTRAL_CEILING_DB = -10.0
SPECTRAL_FRAMES = 4


def _spectral_colors():
    """256 premultiplied RGBA colours, quiet to loud: see-through dark violet,
    magenta, orange, a pale yellow, opaque."""
    stops = np.array([(0.0, 20, 8, 60, 0), (0.25, 90, 20, 120, 150), (0.5, 190, 40, 110, 210),
                      (0.75, 250, 130, 40, 240), (1.0, 255, 245, 170, 255)])
    at = np.linspace(0.0, 1.0, 256)
    colors = np.stack([np.interp(at, stops[:, 0], stops[:, c]) for c in range(1, 5)], axis=1)
    colors[:, :3] *= colors[:, 3:4] / 255.0
    return colors.round().astype(np.uint8)


SPECTRAL_COLORS = _spectral_colors()


def spectrogram_image(samples, rate, start, step, columns, rows, vertical=False):
    """The spectrogram of `samples` (int16 at `rate`) as an image `columns`
    wide and `rows` tall: column i the audio at `start` + (i + 0.5) × `step`
    seconds, the highest frequency at the top. `vertical`, its mirror: as
    many rows as columns, the highest frequency at the left."""
    size = 1 << max(8, int(round(math.log2(rate * 0.02))))
    frames = int(min(SPECTRAL_FRAMES, max(1, math.ceil(step * rate / size))))
    centers = (start + (np.arange(columns) + 0.5) * step) * rate
    # the frames of a column across its step, each `size` samples around its middle
    spread = (np.arange(frames) + 0.5) / frames - 0.5 if frames > 1 else np.zeros(1)
    middles = (centers[:, None] + spread[None, :] * step * rate).astype(np.int64)
    index = middles[:, :, None] + np.arange(size)[None, None, :] - size // 2
    inside = (index >= 0) & (index < len(samples))
    chunk = np.where(inside, samples[np.clip(index, 0, max(0, len(samples) - 1))], 0).astype(np.float32)
    window = np.hanning(size).astype(np.float32)
    magnitude = np.abs(np.fft.rfft(chunk * window, axis=2)).max(axis=1)
    decibels = 20.0 * np.log10(magnitude / (32768.0 * window.sum() / 2.0) + 1e-10)
    loudness = np.clip((decibels - SPECTRAL_FLOOR_DB) / (SPECTRAL_CEILING_DB - SPECTRAL_FLOOR_DB), 0.0, 1.0)
    # each row's frequency, on the mel scale, between the two bins about it
    top = min(SPECTRAL_TOP_HZ, rate / 2.0)
    mels = np.linspace(2595.0 * math.log10(1.0 + top / 700.0), 0.0, max(1, rows))
    bins = (700.0 * (10.0 ** (mels / 2595.0) - 1.0)) / (rate / size)
    low = np.clip(np.floor(bins).astype(np.int64), 0, loudness.shape[1] - 2)
    part = np.clip(bins - low, 0.0, 1.0)
    levels = loudness[:, low] * (1.0 - part) + loudness[:, low + 1] * part
    pixels = SPECTRAL_COLORS[(levels * 255.0).astype(np.int64)]
    if not vertical:
        pixels = pixels.transpose(1, 0, 2)
    pixels = np.ascontiguousarray(pixels)
    height, width = pixels.shape[:2]
    return QImage(pixels.tobytes(), width, height, width * 4, QImage.Format_RGBA8888_Premultiplied).copy()


def spectrogram_tile(width, height, scale, image, rect):
    """A spectrogram tile: `image` stretched over `rect`."""
    tile = QPixmap(int(math.ceil(width * scale)), int(math.ceil(height * scale)))
    tile.setDevicePixelRatio(scale)
    tile.fill(Qt.transparent)
    painter = QPainter(tile)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.drawImage(rect, image)
    painter.end()
    return tile


def waveform_tile(width, height, scale, polygon, look):
    """A waveform tile: `polygon` filled and outlined as the look says."""
    tile = QPixmap(int(math.ceil(width * scale)), int(math.ceil(height * scale)))
    tile.setDevicePixelRatio(scale)
    tile.fill(Qt.transparent)
    painter = QPainter(tile)
    painter.setRenderHint(QPainter.Antialiasing)
    border = look['waveform_border_width']
    painter.setPen(QPen(look.color('waveform_border_color'), border) if border > 0 else Qt.NoPen)
    painter.setBrush(look.color('waveform_fill_color'))
    painter.drawPolygon(polygon)
    painter.end()
    return tile


class TileCache:
    """Images kept by key up to a number of bytes, the oldest let go first."""

    def __init__(self, limit):
        from collections import OrderedDict
        self.limit, self.tiles, self.bytes = limit, OrderedDict(), 0

    def get(self, key):
        tile = self.tiles.get(key)
        if tile is not None:
            self.tiles.move_to_end(key)
        return tile

    def put(self, key, tile):
        self.tiles[key] = tile
        self.bytes += tile.width() * tile.height() * 4
        while self.bytes > self.limit and len(self.tiles) > 1:
            _key, old = self.tiles.popitem(last=False)
            self.bytes -= old.width() * old.height() * 4
        return tile

    def clear(self):
        self.tiles.clear()
        self.bytes = 0

    def __len__(self):
        return len(self.tiles)
