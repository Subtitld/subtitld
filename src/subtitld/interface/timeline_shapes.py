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
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF, QTransform

from subtitld.modules import timeline_frames, timeline_svg

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
