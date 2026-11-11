"""Painted pieces of the player controls.

Each piece is one widget that paints itself (shapes.py); the parts you click are
real buttons (StripKey, a QAbstractButton) laid over it, so handlers, shortcuts,
tooltips, accessible names and checked states work as they do on any button.
The keys paint nothing themselves: the piece paints their hover, pressed and
checked looks, so a group reads as one object.
"""

import os

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton, QAbstractSlider, QSizePolicy, QWidget

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


# --- gap: add gap · duration · remove gap ----------------------------------------------------------------------------

class GapControl(QWidget):
    """One 42px raised piece hanging from the top line: add gap on the left,
    remove gap on the right, and between them the duration field hanging from
    the top edge as a sunken 75° notch. The icons' subtitle blocks run into
    the notch's slanted sides and out to the piece's ends, where they fade in.
    The field is the given spin box, laid over the notch."""

    H = 42
    KEY_W = 40
    FIELD_W = 36                      # the notch's flat bottom
    ICON = 20

    def __init__(self, name, spinbox, parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.setAttribute(Qt.WA_Hover)
        self.notch_h = self.H - 3
        self.run = self.notch_h * shapes.RUN15
        self.setFixedSize(int(round(2 * self.KEY_W + self.FIELD_W + 2 * self.run)), self.H)
        w = self.width()
        self.add_key = StripKey('gap_add_subtitle_button', self)
        self.add_key.setGeometry(0, 0, int(self.KEY_W + self.run), self.H)
        self.remove_key = StripKey('gap_remove_subtitle_button', self)
        self.remove_key.setGeometry(int(w - self.KEY_W - self.run), 0, int(self.KEY_W + self.run), self.H)
        self.spinbox = spinbox
        spinbox.setParent(self)
        # as wide as the notch at mid-height, where the text sits
        spinbox.setGeometry(int(self.KEY_W + self.run / 2), 0, int(self.FIELD_W + self.run), self.notch_h)
        spinbox.raise_()
        self._icons = {'add': graphic('gap_add_icon.svg'), 'remove': graphic('gap_remove_icon.svg')}

    def shape(self):
        return shapes.hanging_rect(0, 0, self.width(), self.height())

    def notch(self):
        return shapes.hanging_trap(self.KEY_W, 0, self.FIELD_W + 2 * self.run, self.notch_h).intersected(self.shape())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        shape, notch = self.shape(), self.notch()
        if not self.isEnabled():
            painter.fillPath(shape, QColor('#25323d'))
            painter.setOpacity(0.35)
        else:
            painter.fillPath(shape, shapes.gradient(0, rect.height(), '#3e5363'))
        line = shape.subtracted(shape.translated(0, -1))
        for key in (self.add_key, self.remove_key):
            zone = shape.intersected(shapes.rect_path(QRectF(key.geometry()))).subtracted(notch)
            state = key.state()
            if state == 'sunk':
                shapes.fill_sunk(painter, zone, QRectF(key.geometry()))
                line = line.subtracted(shapes.rect_path(QRectF(key.geometry()).adjusted(0, 0, 0, 1)))
            elif state == 'hover':
                painter.fillPath(zone, shapes.HOVER)
        # icons: inner blocks run under the notch (clipped by it), outer blocks out to the piece's ends, fading in
        painter.save()
        painter.setClipPath(shape.subtracted(notch))
        iy = (self.H - self.ICON) / 2
        ax = self.KEY_W - 22
        shapes.draw_stretched_icon(painter, self._icons['add'], ax, iy, self.ICON, self.ICON, extend_left=0,
                                   extend_right=self.KEY_W + self.run + 8, fade_left=(ax + 3) / 2)
        rx = self.width() - self.KEY_W + 2
        shapes.draw_stretched_icon(painter, self._icons['remove'], rx, iy, self.ICON, self.ICON,
                                   extend_left=self.width() - self.KEY_W - self.run - 8, extend_right=self.width(),
                                   fade_right=(self.width() - rx - self.ICON + 3) / 2)
        painter.restore()
        # the field: sunken, 1px shadow under the piece's top edge
        painter.fillPath(notch, shapes.gradient(0, self.notch_h, shapes.FIELD, 'sunken'))
        painter.fillPath(notch.intersected(shapes.rect_path(QRectF(0, 0, self.width(), 1))), QColor(0, 0, 0, 90))
        if self.isEnabled():
            painter.fillPath(line, shapes.CONTACT)

    def sizeHint(self):
        return self.size()


# --- music ↔ voice balance ---------------------------------------------------------------------------------------------

class BalanceSlider(QAbstractSlider):
    """Music ↔ voice balance (0 = music only, 100 = voice only, 50 = the
    original mix): one 42px piece hanging from the top line, split into a
    music zone and a voice zone whose widths are their shares, each with its
    share printed in it. The split is the handle — a 2px light line, solid
    for its top quarter, then fading out — and a small semicircle hanging from
    the top edge marks the middle, where the value snaps. The music and voice
    icons at the ends are keys (music_key / voice_key) that nudge the balance."""

    H = 42
    W = 132
    END = 26                          # kept at each end for the icons
    SNAP = 3
    MUSIC = '#5d778c'
    VOICE = '#93abbf'

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.setOrientation(Qt.Horizontal)
        self.setRange(0, 100)
        self.setValue(50)
        self.setFixedSize(self.W, self.H)
        self.setAttribute(Qt.WA_Hover)
        self.music_key = StripKey('music_voice_separation_music_icon', self)
        self.music_key.setGeometry(0, 0, self.END, self.H)
        self.voice_key = StripKey('music_voice_separation_voice_icon', self)
        self.voice_key.setGeometry(self.W - self.END, 0, self.END, self.H)
        self._icons = {'music': graphic('music_voice_separation_music_icon.svg'),
                       'voice': graphic('music_voice_separation_voice_icon.svg')}
        self.valueChanged.connect(self._snap)

    def _snap(self, value):
        if value != 50 and abs(value - 50) <= self.SNAP:
            self.blockSignals(True)
            self.setValue(50)
            self.blockSignals(False)

    def _split_x(self):
        return self.END + (1 - self.value() / 100) * (self.W - 2 * self.END)

    def _value_at(self, x):
        share = 1 - (x - self.END) / (self.W - 2 * self.END)
        return int(round(max(0.0, min(1.0, share)) * 100))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setSliderDown(True)
            self.setValue(self._value_at(event.position().x()))
            event.accept()

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self.setValue(self._value_at(event.position().x()))

    def mouseReleaseEvent(self, event):
        self.setSliderDown(False)

    def sliderChange(self, change):
        super().sliderChange(change)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        shape = shapes.hanging_rect(0, 0, self.W, self.H)
        if not self.isEnabled():
            painter.fillPath(shape, QColor('#25323d'))
            painter.setOpacity(0.35)
        sx = self._split_x()
        music = shape.intersected(shapes.rect_path(QRectF(0, 0, sx, self.H)))
        voice = shape.intersected(shapes.rect_path(QRectF(sx, 0, self.W - sx, self.H)))
        if self.isEnabled():
            painter.fillPath(music, shapes.gradient(0, self.H, self.MUSIC))
            painter.fillPath(voice, shapes.gradient(0, self.H, self.VOICE))
        for key in (self.music_key, self.voice_key):
            zone = shape.intersected(shapes.rect_path(QRectF(key.geometry())))
            if key.state() == 'hover':
                painter.fillPath(zone, shapes.HOVER)
            elif key.state() == 'sunk':
                painter.fillPath(zone, QColor(0, 0, 0, 40))
        cy = self.H / 2 - 1
        for which, cx in (('music', 14), ('voice', self.W - 14)):
            key = self.music_key if which == 'music' else self.voice_key
            painter.setOpacity(1.0 if key.isEnabled() and self.isEnabled() else 0.35)
            self._icons[which].render(painter, QRectF(cx - 8, cy - 8, 16, 16))
        painter.setOpacity(1.0 if self.isEnabled() else 0.35)
        # the shares, in their zones
        font = painter.font()
        font.setPixelSize(9)
        font.setBold(True)
        painter.setFont(font)
        value = self.value()
        painter.setPen(QColor(255, 255, 255, 220))
        if sx - self.END > 18:
            painter.drawText(QRectF(self.END - 2, 0, sx - self.END - 4, self.H), Qt.AlignCenter, str(100 - value))
        painter.setPen(QColor(26, 38, 50, 230))
        if self.W - self.END - sx > 18:
            painter.drawText(QRectF(sx + 6, 0, self.W - self.END - sx - 4, self.H), Qt.AlignCenter, str(value))
        # the handle: its left side touches the music zone; solid for the top quarter, then fading out
        g = QLinearGradient(0, 0, 0, self.H)
        g.setColorAt(0, QColor('#eef4f8'))
        g.setColorAt(0.25, QColor('#eef4f8'))
        g.setColorAt(1, QColor(238, 244, 248, 0))
        painter.save()
        painter.setClipPath(shape)
        painter.fillRect(QRectF(sx, 0, 2, self.H), g)
        painter.restore()
        # the middle, where it snaps: a small semicircle hanging from the top edge, over the handle's place at 50/50
        mid = self.END + 0.5 * (self.W - 2 * self.END) + 1
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawPie(QRectF(mid - 2, -2, 4, 4), 180 * 16, 180 * 16)
        if self.isEnabled():
            painter.fillPath(shape.subtracted(shape.translated(0, -1)), shapes.CONTACT)

    def sizeHint(self):
        return self.size()


# --- add subtitle: the button with its switches hanging in it -------------------------------------------------------

class AddSubtitleButton(QAbstractButton):
    """ADD: a 42px raised button hanging from the top line (its icon and
    label on the left), with its three switches hanging from its top edge as
    one 75° notch of raised keys whose icons butt into one picture — a
    switched-on key sinks — and the new subtitle's duration field after
    them. The switches are StripKeys (checkable), the field the given spin
    box; a click anywhere else is the button's own."""

    H = 42
    NOTCH_H = H - 3                   # the switches hang to 3px above the bottom
    ZONE = 20                         # each switch's icon zone; icons butt, no padding
    ICON = 20
    FIELD_W = 50
    STROKES = ((2.67, 4.0), (18.67, 20.0))   # the brackets' top and bottom strokes in a 20px icon

    def __init__(self, name, switches, spinbox, parent=None):
        """switches: (object name, icon, icon when checked or None)."""
        super().__init__(parent)
        self.setObjectName(name)
        self.setAttribute(Qt.WA_Hover)
        self.setMouseTracking(True)
        self.setFixedHeight(self.H)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.run = self.NOTCH_H * shapes.RUN15
        self._icon = graphic('add_subtitle_icon.svg')
        self.switches = []
        self._switch_icons = {}
        for key_name, icon, icon_checked in switches:
            key = StripKey(key_name, self)
            key.setCheckable(True)
            self.switches.append(key)
            self._switch_icons[key_name] = (graphic(icon), graphic(icon_checked) if icon_checked else None)
        self.spinbox = spinbox
        spinbox.setParent(self)
        self.pressed.connect(self.update)
        self.released.connect(self.update)
        self._layout()

    def _label_font(self):
        font = self.font()
        font.setPixelSize(10)
        font.setBold(True)
        return font

    def _layout(self):
        text_w = QFontMetricsF(self._label_font()).horizontalAdvance(self.text().strip().upper())
        self.notch_x = 12 + self.ICON + 8 + text_w + 12 + shapes.T6
        n = len(self.switches)
        notch_w = n * self.ZONE + 2 * self.run
        # zone edges: the outer zones take the notch's slanted sides (and their curves), the inner ones split the
        # icons where they meet
        edges = ([self.notch_x - shapes.T6] + [self.notch_x + self.run + i * self.ZONE for i in range(1, n)] +
                 [self.notch_x + notch_w + shapes.T6])
        for key, left, right in zip(self.switches, edges, edges[1:]):
            key.setGeometry(int(left), 0, int(round(right - left)), self.NOTCH_H)
        field_x = self.notch_x + notch_w + 10
        self.spinbox.setGeometry(int(field_x), (self.H - 26) // 2, self.FIELD_W, 26)
        self.setFixedWidth(int(field_x + self.FIELD_W + 8))
        self.update()

    def setText(self, text):
        super().setText(text)
        self._layout()

    def changeEvent(self, event):
        super().changeEvent(event)
        self.update()

    def notch(self):
        return shapes.hanging_trap(self.notch_x, 0, len(self.switches) * self.ZONE + 2 * self.run,
                                   self.NOTCH_H).intersected(self.shape())

    def shape(self):
        return shapes.hanging_rect(0, 0, self.width(), self.H)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        shape, notch = self.shape(), self.notch()
        if not self.isEnabled():
            painter.fillPath(shape, QColor('#25323d'))
            painter.setOpacity(0.35)
        elif self.isDown():
            shapes.fill_sunk(painter, shape, rect)
        else:
            painter.fillPath(shape, shapes.gradient(0, self.H, '#3e5363'))
            over_child = any(k.underMouse() for k in self.switches) or self.spinbox.underMouse()
            if self.underMouse() and not over_child:
                painter.fillPath(shape, shapes.HOVER)
        # icon and label
        self._icon.render(painter, QRectF(12, (self.H - self.ICON) / 2, self.ICON, self.ICON))
        painter.setFont(self._label_font())
        painter.setPen(QColor('#cddbe7'))
        painter.drawText(QRectF(12 + self.ICON + 8, 0, self.notch_x - 40, self.H), Qt.AlignVCenter | Qt.AlignLeft,
                         self.text().strip().upper())
        # the switches: one raised group, then only the zones that differ are painted over
        painter.fillPath(notch, shapes.gradient(0, self.NOTCH_H, shapes.NEST))
        painter.fillPath(notch.intersected(shapes.rect_path(QRectF(0, self.NOTCH_H - 1, self.width(), 1))),
                         QColor(0, 0, 0, 30))
        iy = (self.NOTCH_H - self.ICON) / 2 + 1
        for i, key in enumerate(self.switches):
            zone = notch.intersected(shapes.rect_path(QRectF(key.geometry()).adjusted(0, -1, 0, 1)))
            state = key.state()
            if state == 'sunk':
                painter.fillPath(zone, shapes.gradient(0, self.NOTCH_H, shapes.SUNK, 'sunken'))
                painter.fillPath(zone.intersected(shapes.rect_path(QRectF(0, 0, self.width(), 1))), QColor(0, 0, 0, 46))
            elif state == 'hover':
                painter.fillPath(zone, QColor(255, 255, 255, 14))
            normal, checked = self._switch_icons[key.objectName()]
            renderer = checked if key.isChecked() and checked is not None else normal
            ix = self.notch_x + self.run + i * self.ZONE
            painter.save()
            painter.setClipPath(notch)
            renderer.render(painter, QRectF(ix, iy, self.ICON, self.ICON))
            # the neighbouring subtitles' brackets run on into the slanted sides
            if i in (0, len(self.switches) - 1):
                for top, bottom in self.STROKES:
                    if i == 0:
                        ext = QRectF(self.notch_x - 20, iy + top, ix - self.notch_x + 20.5, bottom - top)
                    else:
                        ext = QRectF(ix + self.ICON - 0.5, iy + top, 40, bottom - top)
                    painter.fillRect(ext, QColor('#b8cee0'))
            painter.restore()
        if self.isEnabled() and not self.isDown():
            painter.fillPath(shape.subtracted(shape.translated(0, -1)), shapes.CONTACT)

    def sizeHint(self):
        return QSize(self.width(), self.H)

    def mouseMoveEvent(self, event):
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)
