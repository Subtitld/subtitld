"""A look's SVG shapes (modules.timeline_svg) and frames
(modules.timeline_frames) as the timelines draw them.

The vertical timeline draws them as the timeline does, mirrored across
the diagonal (`mirrored`): what runs along the timeline's time runs down,
the timeline's top is the left. A shape drawn for the timeline is drawn
there that way, unchanged.
"""

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QTransform

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
            timeline_frames.paint(painter, found, swap(rect), hex_color, widget.devicePixelRatioF(), look_name, speaker)
    else:
        timeline_frames.paint(painter, found, rect, hex_color, widget.devicePixelRatioF(), look_name, speaker)
    return True
