"""Painted pieces of the player controls.

Each piece is one widget that paints itself (shapes.py); the parts you click are
real buttons (StripKey, a QAbstractButton) laid over it, so handlers, shortcuts,
tooltips, accessible names and checked states work as they do on any button.
The keys paint nothing themselves: the piece paints their hover, pressed and
checked looks, so a group reads as one object.
"""

import os

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from subtitld.interface import shapes
from subtitld.modules import session

ICON_H = 20                           # the strips' subtitle icons are 20px tall


def graphic(name):
    return QSvgRenderer(os.path.join(session.PATH_SUBTITLD_GRAPHICS, name))


class StripKey(QAbstractButton):
    """A button that is part of a painted piece: it takes the clicks, its
    parent paints it."""

    def __init__(self, name, parent):
        super().__init__(parent)
        self.setObjectName(name)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover)
        self.pressed.connect(parent.update)
        self.released.connect(parent.update)
        self.toggled.connect(lambda _checked: parent.update())

    def paintEvent(self, event):
        pass

    def enterEvent(self, event):
        self.parent().update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.parent().update()
        super().leaveEvent(event)

    def changeEvent(self, event):
        self.parent().update()
        super().changeEvent(event)

    def state(self):
        if not self.isEnabled():
            return 'disabled'
        if self.isDown() or self.isChecked():
            return 'sunk'
        if self.underMouse():
            return 'hover'
        return 'normal'


class ActionStrip(QWidget):
    """Independent buttons grouped by what they do: one raised strip hanging
    from the line above (square top, 3px bottom corners), 32px keys with no
    gaps or splits. Their icons butt against each other so the subtitle
    blocks run from key to key; an icon narrower than its key is stretched at
    one edge, and the strip's two ends fade in. A hovered key takes a faint
    tint; a pressed or checked one sinks and loses the contact line under it.

    keys: (object name, icon file or None, stretch side 'left'/'right'/None).
    picture: optional callable(painter, rect) drawing the strip's icons in
    one go, for icons that cannot line up on their own.
    """

    KEY_W = 32
    TOP, BOTTOM = 3, 6                # around the 20px icons
    FADE = 7                          # the ends fade in over this many pixels

    def __init__(self, name, keys, picture=None, parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.setAttribute(Qt.WA_Hover)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(self.KEY_W * len(keys), self.TOP + ICON_H + self.BOTTOM)
        self.picture = picture
        self.specs = keys
        self.keys = []
        self._icons = {}
        for i, (key_name, icon, _side) in enumerate(keys):
            key = StripKey(key_name, self)
            key.setGeometry(i * self.KEY_W, 0, self.KEY_W, self.height())
            self.keys.append(key)
            if icon:
                self._icons[key_name] = graphic(icon)

    def key(self, name):
        return next(k for k in self.keys if k.objectName() == name)

    def shape(self):
        return shapes.hanging_rect(0, 0, self.width(), self.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        shape = self.shape()
        if not self.isEnabled():
            painter.fillPath(shape, QColor('#25323d'))
            painter.setOpacity(0.35)
        else:
            painter.fillPath(shape, shapes.gradient(0, rect.height(), '#3e5363'))
        line = shape.subtracted(shape.translated(0, -1))
        for key in self.keys:
            zone = shape.intersected(shapes.rect_path(QRectF(key.geometry())))
            state = key.state()
            if state == 'sunk':
                shapes.fill_sunk(painter, zone, QRectF(key.geometry()))
                line = line.subtracted(shapes.rect_path(QRectF(key.geometry()).adjusted(0, 0, 0, 1)))
            elif state == 'hover':
                painter.fillPath(zone, shapes.HOVER)
        painter.save()
        painter.setClipPath(shape)
        if self.picture:
            self.picture(painter, QRectF(0, self.TOP, rect.width(), ICON_H))
        else:
            self._paint_icons(painter)
        painter.restore()
        if self.isEnabled():
            painter.fillPath(line, shapes.CONTACT)

    def _paint_icons(self, painter):
        n = len(self.keys)
        for i, (key_name, _icon, side) in enumerate(self.specs):
            renderer = self._icons.get(key_name)
            if renderer is None:
                continue
            size = renderer.defaultSize()
            iw = size.width() * ICON_H / max(1, size.height())
            x0 = i * self.KEY_W
            x = x0 + self.KEY_W - iw if side == 'left' else x0 if side == 'right' else x0 + (self.KEY_W - iw) / 2
            shapes.draw_stretched_icon(painter, renderer, x, self.TOP, iw, ICON_H,
                                       extend_left=x0 if side == 'left' else None,
                                       extend_right=x0 + self.KEY_W if side == 'right' else None,
                                       fade_left=self.FADE if i == 0 else 0,
                                       fade_right=self.FADE if i == n - 1 else 0)

    def sizeHint(self):
        return QSize(self.width(), self.height())


# --- send text: one timeline drawn in code -------------------------------------------------------------------------

BLOCK = QColor('#b8cee0')
TEXT = QColor('#ffffff')
INK = QColor('#304251')


def _chevrons(painter, x, y, count, direction):
    """Open chevrons like the icons' « and »: 1px dark strokes, 5px tall, 2px apart."""
    pen = QPen(INK, 1)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    for k in range(count):
        cx = x + k * 2
        if direction == 'left':
            painter.drawPolyline([QPointF(cx + 1.5, y + 8.5), QPointF(cx, y + 11), QPointF(cx + 1.5, y + 13.5)])
        else:
            painter.drawPolyline([QPointF(cx, y + 8.5), QPointF(cx + 1.5, y + 11), QPointF(cx, y + 13.5)])
    painter.setPen(Qt.NoPen)


def send_text_picture(painter, rect):
    """The send-text strip as one timeline, so its blocks join across the keys: the previous subtitle, the current
    one (cut twice by the slices) and the next. Send to last (««) sits where the current subtitle starts, the two
    slice keys' cuts (« | and | ») inside it, send to next (»») where it ends. The ends fade in."""
    scale = 4
    w = rect.width()
    layer = QImage(int(w * scale), int(ICON_H * scale), QImage.Format_ARGB32_Premultiplied)
    layer.fill(0)
    lp = QPainter(layer)
    lp.setRenderHint(QPainter.Antialiasing)
    lp.scale(scale, scale)
    lp.setPen(Qt.NoPen)

    def block(x0, x1, color, y0, y1, radius, round_left=True, round_right=True):
        path = QPainterPath()
        path.addRoundedRect(QRectF(x0, y0, x1 - x0, y1 - y0), radius, radius)
        if not round_left:
            path = path.united(shapes.rect_path(QRectF(x0, y0, radius + 0.1, y1 - y0)))
        if not round_right:
            path = path.united(shapes.rect_path(QRectF(x1 - radius - 0.1, y0, radius + 0.1, y1 - y0)))
        lp.fillPath(path, color)

    quarter = w / 4
    b1, b2 = 14, w - 14                                # where the current subtitle starts and ends
    c1, c2 = quarter + 16, 3 * quarter - 16            # the two slice cuts
    for x0, x1, rl, rr in ((-3, b1 - 0.5, False, True), (b1 + 0.5, c1 - 2, True, True), (c1 + 2, c2 - 2, True, True),
                           (c2 + 2, b2 - 0.5, True, True), (b2 + 0.5, w + 3, True, False)):
        block(x0, x1, BLOCK, 2, 20, 2.5, rl, rr)
    for c in (c1, c2):
        lp.fillRect(QRectF(c - 0.5, 0, 1, 20), BLOCK)
    for x0, x1, rl, rr in ((-3, b1 - 3, False, True), (b1 + 3, c1 - 4, True, True), (c1 + 4, c2 - 4, True, True),
                           (c2 + 4, b2 - 3, True, True), (b2 + 3, w + 3, True, False)):
        block(x0, x1, TEXT, 7, 15, 4, rl, rr)
    _chevrons(lp, b1 + 5, 0, 2, 'left')
    _chevrons(lp, c1 - 8, 0, 1, 'left')
    _chevrons(lp, c2 + 6.5, 0, 1, 'right')
    _chevrons(lp, b2 - 8.5, 0, 2, 'right')
    lp.resetTransform()
    lp.scale(scale, scale)
    lp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    fade = ActionStrip.FADE
    g = QLinearGradient(0, 0, w, 0)
    g.setColorAt(0, QColor(0, 0, 0, 0))
    g.setColorAt(fade / w, QColor(0, 0, 0, 255))
    g.setColorAt(1 - fade / w, QColor(0, 0, 0, 255))
    g.setColorAt(1, QColor(0, 0, 0, 0))
    lp.fillRect(QRectF(0, 0, w, ICON_H), g)
    lp.end()
    painter.drawImage(QRectF(rect.x(), rect.y(), w, ICON_H), layer)
