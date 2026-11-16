"""Shared drawing helpers for Subtitld's painted widgets.

The look: no borders, depth from a vertical gradient (raised: lighter at the top, darker at the bottom; sunken: the
other way round, softer at the bottom), 3px corners on rectangles, 75° slants with 6px curves, and a 1px 45% black
contact line under raised buttons.
"""

import math

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath

SLANT = math.radians(75)
RUN15 = math.tan(math.radians(15))          # horizontal lean of a 75° side per pixel of height
CURVE = 6                                   # curve on a slanted corner
T6 = CURVE * math.tan(SLANT / 2)            # how far a 6px curve reaches along each side of a 75° turn
RADIUS = 3                                  # corner radius on rectangles
DEPTH = 6                                   # gradient: ±6% HSL lightness

CONTACT = QColor(0, 0, 0, 115)              # 1px line under a raised button
TOP_SHADOW = QColor(0, 0, 0, 46)            # 1px line at the top of a sunk (pressed/checked) zone
HOVER = QColor(255, 255, 255, 18)           # faint tint over a hovered zone

RAISED = '#4a5f70'                          # default button
SUNK = '#2e3e4c'                            # pressed/checked button
NEST = '#33475a'                            # inner key, a step darker than its parent
FIELD = '#2a3946'                           # input well
LIGHT = '#b8cee0'                           # icon/text light


def shade(color, pct):
    """Lighten (pct > 0) or darken (pct < 0) a colour in HSL lightness by pct percent of the range."""
    q = QColor(color)
    h, s, l, a = q.getHslF()
    q.setHslF(max(h, 0), s, max(0.0, min(1.0, l + pct / 100)), a)
    return q


def gradient(y0, y1, base, kind='raised', depth=DEPTH):
    """Vertical gradient over y0..y1: 'raised' (+depth → −depth) or 'sunken' (−depth → +depth/2)."""
    g = QLinearGradient(0, y0, 0, y1)
    if kind == 'raised':
        g.setColorAt(0, shade(base, depth))
        g.setColorAt(1, shade(base, -depth))
    else:
        g.setColorAt(0, shade(base, -depth))
        g.setColorAt(1, shade(base, depth / 2))
    return g


def bottom_colour(base, kind='raised', depth=DEPTH):
    """The colour a gradient ends on, for blending one zone into the next without an edge."""
    return shade(base, -depth if kind == 'raised' else depth / 2)


def rect_path(rect):
    path = QPainterPath()
    path.addRect(rect)
    return path


def rounded_path(rect, radius=RADIUS):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def unit(x, y):
    n = math.hypot(x, y)
    return x / n, y / n


def fillet(path, corner, d1, d2, t):
    """Line to `t` before `corner` along direction d1, then a curve leaving `t` along d2 (cubic circle arc)."""
    th = math.acos(max(-1.0, min(1.0, d1[0] * d2[0] + d1[1] * d2[1])))
    if th < 1e-6:
        path.lineTo(QPointF(*corner))
        return
    r = t / math.tan(th / 2)
    h = 4 / 3 * math.tan(th / 4) * r
    s = QPointF(corner[0] - d1[0] * t, corner[1] - d1[1] * t)
    e = QPointF(corner[0] + d2[0] * t, corner[1] + d2[1] * t)
    path.lineTo(s)
    path.cubicTo(QPointF(s.x() + d1[0] * h, s.y() + d1[1] * h), QPointF(e.x() - d2[0] * h, e.y() - d2[1] * h), e)


def hanging_rect(x, y, w, h, radius=RADIUS):
    """Hangs from a line above: square top corners, rounded bottom corners."""
    p = QPainterPath(QPointF(x, y))
    p.lineTo(x + w, y)
    p.lineTo(x + w, y + h - radius)
    p.quadTo(x + w, y + h, x + w - radius, y + h)
    p.lineTo(x + radius, y + h)
    p.quadTo(x, y + h, x, y + h - radius)
    p.closeSubpath()
    return p


def hanging_trap(x, y, w, h):
    """A notch hanging from the edge at y: top spans x..x+w, the sides lean in at 75° to the bottom, 6px curves at
    the bottom corners, and the top corners flare out into the edge (so it reads as cut into it)."""
    run = h * RUN15
    dl = unit(run, h)
    dr = unit(run, -h)
    p = QPainterPath(QPointF(x - T6, y))
    fillet(p, (x, y), (1, 0), dl, T6)
    fillet(p, (x + run, y + h), dl, (1, 0), T6)
    fillet(p, (x + w - run, y + h), (1, 0), dr, T6)
    fillet(p, (x + w, y), dr, (1, 0), T6)
    p.closeSubpath()
    return p


def standing_trap(x, y, w, h, bottom):
    """hanging_trap flipped vertically: stands on the line at `bottom`, narrowing upwards to bottom - h."""
    from PySide6.QtGui import QTransform
    path = hanging_trap(x, bottom - h, w, h)
    return QTransform(1, 0, 0, -1, 0, 2 * bottom - h).map(path)


def fill_raised(painter, path, rect, base=RAISED, contact=True):
    """A raised piece: gradient fill and, optionally, the 1px contact line along its bottom."""
    painter.fillPath(path, gradient(rect.top(), rect.bottom(), base))
    if contact:
        painter.fillPath(path.subtracted(path.translated(0, -1)), CONTACT)


def fill_sunk(painter, path, rect, base=SUNK, shadow=TOP_SHADOW):
    """A sunk zone (pressed/checked): sunken gradient and a 1px shadow along its top."""
    painter.fillPath(path, gradient(rect.top(), rect.bottom(), base, 'sunken'))
    painter.fillPath(path.intersected(rect_path(QRectF(rect.x() - 1, rect.top(), rect.width() + 2, 1))), shadow)


def render_svg_image(renderer, width, height, scale=4):
    """Rasterise an SVG renderer to a premultiplied image at `scale`×, for column stretching and masking."""
    img = QImage(int(width * scale), int(height * scale), QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    ip = QPainter(img)
    ip.setRenderHint(QPainter.Antialiasing)
    renderer.render(ip, QRectF(0, 0, img.width(), img.height()))
    ip.end()
    return img


def draw_stretched_icon(painter, renderer, x, y, w, h, extend_left=None, extend_right=None, fade_left=0, fade_right=0,
                        scale=4):
    """Draw an icon at x,y (w×h). Its left/right edge pixel column is stretched out to extend_left/extend_right so
    the subtitle blocks run on; fade_left/fade_right fade that end in from transparent over that many pixels."""
    img = render_svg_image(renderer, w, h, scale)
    x0 = extend_left if extend_left is not None else x
    x1 = extend_right if extend_right is not None else x + w
    lw = x1 - x0
    layer = QImage(max(1, int(lw * scale)), int(h * scale), QImage.Format_ARGB32_Premultiplied)
    layer.fill(0)
    lp = QPainter(layer)
    lp.setRenderHint(QPainter.SmoothPixmapTransform)
    lp.scale(scale, scale)
    lp.drawImage(QRectF(x - x0, 0, w, h), img)
    if extend_left is not None and x > extend_left:
        lp.drawImage(QRectF(0, 0, x - x0 + 0.25, h), img.copy(0, 0, 1, img.height()))
    if extend_right is not None and extend_right > x + w:
        lp.drawImage(QRectF(x + w - x0 - 0.25, 0, x1 - x - w + 0.25, h), img.copy(img.width() - 1, 0, 1, img.height()))
    if fade_left or fade_right:
        lp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        g = QLinearGradient(0, 0, lw, 0)
        clear, solid = QColor(0, 0, 0, 0), QColor(0, 0, 0, 255)
        g.setColorAt(0, clear if fade_left else solid)
        if fade_left:
            g.setColorAt(min(0.49, fade_left / lw), solid)
        if fade_right:
            g.setColorAt(max(0.51, 1 - fade_right / lw), solid)
        g.setColorAt(1, clear if fade_right else solid)
        lp.fillRect(QRectF(0, 0, lw, h), g)
    lp.end()
    painter.drawImage(QRectF(x0, y, lw, h), layer)
