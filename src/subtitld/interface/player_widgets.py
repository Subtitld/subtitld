"""Painted pieces of the player controls.

Each piece is one widget that paints itself (shapes.py); the parts you click are
real buttons (StripKey, a QAbstractButton) laid over it, so handlers, shortcuts,
tooltips, accessible names and checked states work as they do on any button.
The keys paint nothing themselves: the piece paints their hover, pressed and
checked looks, so a group reads as one object.
"""

import math
import os

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QFontMetricsF, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPainterPathStroker,
                           QPolygonF, QRegion, QTransform)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton, QAbstractSlider, QPushButton, QSizePolicy, QStyleOptionButton, QWidget

from subtitld.interface import shapes
from subtitld.modules import session

ICON_H = 20                           # the strips' subtitle icons are 20px tall
CONTACT = QColor(0, 0, 0, 64)         # the player pieces' contact line: softer than the 45% of other buttons
PIECE = '#2f404d'                     # the player pieces' raised colour (strips, gap, ADD, speed and repeat)
PIECE_NEST = '#273540'                # their nested keys, a step darker
WELL = ('#23303a', '#354856')         # their sunken fields (ADD's and the gap's duration), top to bottom
EDGE = QColor('#3a4e5f')              # the player panel's 1px top edge line (stylesheet: #playercontrols_widget_top_line)
BAND = QColor('#161e24')              # the window behind the clear 2px band above the player panel


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
    nested: True makes the first and last keys sub-keys of the ones between:
    each stands on the bottom edge to 3px under the top, flush with its end
    of the strip, a step darker, its inner side a 75° slant leaning in as it
    rises, centred on the old split; the keys between take the rest.
    """

    KEY_W = 32
    TOP, BOTTOM = 11, 11              # around the 20px icons: as tall as the other 42px pieces
    FADE = 7                          # the ends fade in over this many pixels

    def __init__(self, name, keys, picture=None, nested=False, parent=None):
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
        self.nested = nested
        self.zones = {}                    # key → its area, for the nested layout
        if nested:
            self._nest()

    def _nest(self):
        w, h = self.width(), self.height()
        nh = h - 3                                            # sub-keys hang to 3px above the bottom, as elsewhere
        run = nh * shapes.RUN15
        shape = self.shape()
        k = self.KEY_W
        # flush with the strip's ends (the notch runs on past them and is cut there), standing on the bottom edge to
        # 3px under the top: the hanging notch of the transport turned upside down, its inner side leaning in as it
        # rises, centred on the old split, curving into the bottom edge
        flip = QTransform(1, 0, 0, -1, 0, h)
        self.subzones = [flip.map(shapes.hanging_trap(-60, 0, 60 + k + run / 2, nh)).intersected(shape),
                         flip.map(shapes.hanging_trap(w - k - run / 2, 0, k + run / 2 + 60, nh)).intersected(shape)]
        self.sub_h = nh
        subs = self.subzones[0].united(self.subzones[1])
        inner = self.keys[1:-1]
        for i, key in enumerate(inner):
            x0 = 0 if i == 0 else (i + 1) * k
            x1 = w if i == len(inner) - 1 else (i + 2) * k
            self.zones[key] = shape.intersected(shapes.rect_path(QRectF(x0, 0, x1 - x0, h))).subtracted(subs)
        self.zones[self.keys[0]], self.zones[self.keys[-1]] = self.subzones
        for key, zone in self.zones.items():
            key.setGeometry(zone.boundingRect().toAlignedRect())
            key.setMask(_mask_of(zone, key))
        for key in (self.keys[0], self.keys[-1]):
            key.raise_()

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
            painter.fillPath(shape, shapes.gradient(0, rect.height(), PIECE))
        line = shape.subtracted(shape.translated(0, -1))
        subs = (self.keys[0], self.keys[-1]) if self.nested else ()
        for key in self.keys:
            if key in subs:
                continue
            zone = self.zones.get(key) or shape.intersected(shapes.rect_path(QRectF(key.geometry())))
            state = key.state()
            if state == 'sunk':
                shapes.fill_sunk(painter, zone, QRectF(key.geometry()))
                line = line.subtracted(shapes.rect_path(QRectF(key.geometry()).adjusted(0, 0, 0, 1)))
            elif state == 'hover':
                painter.fillPath(zone, shapes.HOVER)
        top = self.height() - self.sub_h if subs else 0
        for key in subs:                   # the sub-keys: a darker raised notch each, sinking when pressed
            zone = self.zones[key]
            if key.state() == 'sunk':
                painter.fillPath(zone, shapes.gradient(top, self.height(), shapes.SUNK, 'sunken'))
                painter.fillPath(zone.intersected(shapes.rect_path(QRectF(0, top, self.width(), 1))), QColor(0, 0, 0, 46))
            else:
                painter.fillPath(zone, shapes.gradient(top, self.height(), PIECE_NEST))
                if key.state() == 'hover':
                    painter.fillPath(zone, QColor(255, 255, 255, 14))
        painter.save()
        painter.setClipPath(shape)
        if self.picture:
            self.picture(painter, QRectF(0, self.TOP, rect.width(), ICON_H))
        else:
            self._paint_icons(painter)
        painter.restore()
        if self.isEnabled():
            painter.fillPath(line, CONTACT)

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
    remove gap on the right, and between them the duration field, a sunken
    26px field with 3px corners like ADD's. The icons' subtitle blocks run in
    to the field and out to the piece's ends, where they fade in. The field
    is the given spin box, laid over the painted field."""

    H = 42
    KEY_W = 40
    FIELD_W = 50                      # as ADD's duration field
    FIELD_H = 26
    ICON = 20

    def __init__(self, name, spinbox, parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.setAttribute(Qt.WA_Hover)
        self.setFixedSize(2 * self.KEY_W + self.FIELD_W, self.H)
        self.field_rect = QRectF(self.KEY_W, (self.H - self.FIELD_H) // 2, self.FIELD_W, self.FIELD_H)
        self.add_key = StripKey('gap_add_subtitle_button', self)
        self.add_key.setGeometry(0, 0, self.KEY_W, self.H)
        self.remove_key = StripKey('gap_remove_subtitle_button', self)
        self.remove_key.setGeometry(self.KEY_W + self.FIELD_W, 0, self.KEY_W, self.H)
        self.spinbox = spinbox
        spinbox.setParent(self)
        spinbox.setGeometry(self.field_rect.toRect())
        spinbox.raise_()
        self._icons = {'add': graphic('gap_add_icon.svg'), 'remove': graphic('gap_remove_icon.svg')}

    def shape(self):
        return shapes.hanging_rect(0, 0, self.width(), self.height())

    def field(self):
        return shapes.rounded_path(self.field_rect)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        shape, field = self.shape(), self.field()
        if not self.isEnabled():
            painter.fillPath(shape, QColor('#25323d'))
            painter.setOpacity(0.35)
        else:
            painter.fillPath(shape, shapes.gradient(0, rect.height(), PIECE))
        line = shape.subtracted(shape.translated(0, -1))
        for key in (self.add_key, self.remove_key):
            zone = shape.intersected(shapes.rect_path(QRectF(key.geometry())))
            state = key.state()
            if state == 'sunk':
                shapes.fill_sunk(painter, zone, QRectF(key.geometry()))
                line = line.subtracted(shapes.rect_path(QRectF(key.geometry()).adjusted(0, 0, 0, 1)))
            elif state == 'hover':
                painter.fillPath(zone, shapes.HOVER)
        # icons: inner blocks run in to the field, outer blocks out to the piece's ends, fading in
        painter.save()
        painter.setClipPath(shape.subtracted(field))
        iy = (self.H - self.ICON) / 2
        ax = self.KEY_W - 22
        shapes.draw_stretched_icon(painter, self._icons['add'], ax, iy, self.ICON, self.ICON, extend_left=0,
                                   extend_right=self.KEY_W + 2, fade_left=(ax + 3) / 2)
        rx = self.width() - self.KEY_W + 2
        shapes.draw_stretched_icon(painter, self._icons['remove'], rx, iy, self.ICON, self.ICON,
                                   extend_left=self.width() - self.KEY_W - 2, extend_right=self.width(),
                                   fade_right=(self.width() - rx - self.ICON + 3) / 2)
        painter.restore()
        # the field, sunken, as ADD's
        g = QLinearGradient(0, self.field_rect.top(), 0, self.field_rect.bottom())
        g.setColorAt(0, QColor(WELL[0]))
        g.setColorAt(1, QColor(WELL[1]))
        painter.fillPath(field, g)
        if self.isEnabled():
            painter.fillPath(line, CONTACT)

    def sizeHint(self):
        return self.size()


# --- music ↔ voice balance ---------------------------------------------------------------------------------------------

class BalanceSlider(QAbstractSlider):
    """Music ↔ voice balance (0 = music only, 100 = voice only, 50 = the
    original mix): one 42px piece hanging from the top line, split into a
    music zone and a voice zone whose widths are their shares, each with its
    share printed in it. The split is the handle — a 2px light line, solid
    for its top quarter, then fading out; the value snaps at the middle. The
    music and voice icons at the ends are keys (music_key / voice_key) that
    nudge the balance."""

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
        if self.isEnabled():
            painter.fillPath(shape.subtracted(shape.translated(0, -1)), CONTACT)

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
        # the duration field: its left side lies on the notch's right slant, so the two meet edge to edge; the
        # field is painted here (field_path) and the spin box over it is see-through
        fy = (self.H - 26) // 2
        slant_top = self.notch_x + notch_w                      # the notch's right side at the top edge
        self.field_left = (slant_top - fy * shapes.RUN15, slant_top - (fy + 26) * shapes.RUN15)
        field_right = self.field_left[0] + self.FIELD_W
        self.field_box = QRectF(self.field_left[1], fy, field_right - self.field_left[1], 26)
        self.spinbox.setGeometry(int(round(self.field_left[0])), fy, int(round(self.FIELD_W)), 26)
        self.setFixedWidth(int(field_right + 8))
        self.update()

    def field_path(self):
        (top_x, bottom_x), r = self.field_left, shapes.RADIUS
        y0, y1, x1 = self.field_box.top(), self.field_box.bottom(), self.field_box.right()
        p = QPainterPath(QPointF(top_x, y0))
        p.lineTo(x1 - r, y0)
        p.quadTo(x1, y0, x1, y0 + r)
        p.lineTo(x1, y1 - r)
        p.quadTo(x1, y1, x1 - r, y1)
        p.lineTo(bottom_x, y1)
        p.closeSubpath()
        return p

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
        """Its bottom-right corner is square: REMOVE continues the piece there, as one group."""
        w = self.width()
        return shapes.hanging_rect(0, 0, w, self.H).united(
            shapes.rect_path(QRectF(w - shapes.RADIUS - 1, 0, shapes.RADIUS + 1, self.H)))

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
            painter.fillPath(shape, shapes.gradient(0, self.H, PIECE))
            over_child = any(k.underMouse() for k in self.switches) or self.spinbox.underMouse()
            if self.underMouse() and not over_child:
                painter.fillPath(shape, shapes.HOVER)
        # icon and label
        self._icon.render(painter, QRectF(12, (self.H - self.ICON) / 2, self.ICON, self.ICON))
        painter.setFont(self._label_font())
        painter.setPen(self.palette().color(self.foregroundRole()))     # the stylesheet's color
        painter.drawText(QRectF(12 + self.ICON + 8, 0, self.notch_x - 40, self.H), Qt.AlignVCenter | Qt.AlignLeft,
                         self.text().strip().upper())
        # the switches: one raised group, then only the zones that differ are painted over
        painter.fillPath(notch, shapes.gradient(0, self.NOTCH_H, PIECE_NEST))
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
        # the duration field, sunken, against the notch's slant
        field = QLinearGradient(0, self.field_box.top(), 0, self.field_box.bottom())
        field.setColorAt(0, QColor(WELL[0]))
        field.setColorAt(1, QColor(WELL[1]))
        painter.fillPath(self.field_path(), field)
        if self.isEnabled() and not self.isDown():
            painter.fillPath(shape.subtracted(shape.translated(0, -1)), CONTACT)

    def sizeHint(self):
        return QSize(self.width(), self.H)

    def mouseMoveEvent(self, event):
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


# --- transport: play from the beginning · play/pause ▶|▶ · record ◉ mode ------------------------------------------

class SlantFrameButton(QPushButton):
    """Playback speed and repeat: a button drawn as a 75° slanted frame
    hanging from the panel's top edge, as tall as the other 42px pieces (no
    edge lines, square top corners, the 1px contact line along its bottom).
    Its inner slant runs under the transport's margin and lies on the
    transport's own slant, so the corner where they meet is square too; the
    outer bottom corner has the 6px curve. It holds its own controls in its
    layout, its icon in the room the stylesheet's paddings leave.
    lean='right' slants like \\ (the speed, left of the transport), 'left'
    like // (the repeat, right of it). Checked, the frame turns light, as the
    controls inside are showing."""

    H = 42
    SLANT_H = 46                      # the transport's slant below the frame's top (2px under the transport's)
    NORMAL = PIECE
    HOVERED = '#334654'
    CHECKED = '#748898'

    def __init__(self, lean, icon, parent=None):
        super().__init__(parent)
        self.lean = lean
        self.setAttribute(Qt.WA_Hover)
        self.setFixedHeight(self.H)
        self.setIcon(QIcon(os.path.join(session.PATH_SUBTITLD_GRAPHICS, icon)))

    def frame(self):
        w, h = self.width(), self.H
        run = h * shapes.RUN15
        inner = w - self.SLANT_H * shapes.RUN15          # the inner slant's top, on the transport's slant
        p = QPainterPath(QPointF(0, 0))
        p.lineTo(inner, 0)
        p.lineTo(inner + run, h)
        shapes.fillet(p, (run, h), (-1, 0), shapes.unit(-run, -h), shapes.T6)     # the outer bottom corner
        p.closeSubpath()
        if self.lean == 'left':
            p = QTransform(-1, 0, 0, 1, w, 0).map(p)
        return p

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        frame = self.frame()
        base = self.CHECKED if self.isChecked() else (self.HOVERED if self.underMouse() else self.NORMAL)
        shapes.fill_raised(painter, frame, QRectF(0, 0, self.width(), self.H), base, contact=False)
        painter.fillPath(frame.subtracted(frame.translated(0, -1)), CONTACT)
        if self.isDown():
            painter.fillPath(frame, QColor(0, 0, 0, 30))
        elif self.underMouse() and self.isChecked():
            painter.fillPath(frame, shapes.HOVER)
        if not self.icon().isNull():
            size = self.iconSize()
            pad = self.style().subElementRect(self.style().SubElement.SE_PushButtonContents, self._option(), self)
            cx = pad.center().x() + 0.5 if pad.isValid() else self.width() / 2
            mode = QIcon.Normal if self.isEnabled() else QIcon.Disabled
            pixmap = self.icon().pixmap(size, self.devicePixelRatioF(), mode)
            painter.drawPixmap(QPointF(cx - size.width() / 2, (self.height() - size.height()) / 2), pixmap)

    def _option(self):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        return option


def _tinted(renderer, rect, color, scale=4):
    """An icon in one colour (white on the red recording state)."""
    img = shapes.render_svg_image(renderer, rect.width(), rect.height(), scale)
    ip = QPainter(img)
    ip.setCompositionMode(QPainter.CompositionMode_SourceIn)
    ip.fillRect(img.rect(), QColor(color))
    ip.end()
    return img


def _mask_of(path, key):
    """A key's mouse area: the part of `path` (in the parent's coordinates) over the key."""
    local = path.translated(-key.x(), -key.y())
    return QRegion(local.toFillPolygon().toPolygon())


class Transport(QWidget):
    """The light centre notch of the player controls, one painted piece:

    - play from the beginning: a key hanging from the panel's top edge, flush
      with its left slant;
    - play/pause: the panel's left part, with the ▶|▶ group (play from the
      previous / next subtitle's start) hanging after its icon;
    - record: the panel's right part, its left edge continuing the ▶|▶
      group's slanted side, with the record mode key (transcript or audio,
      one key that switches) hanging flush with the right slant.

    Playing sinks the play part; recording turns the record part red. Where
    play and record meet under the group, a gradient blends one into the
    other. The keys are buttons laid over their areas (masked to them)."""

    H = 51
    PANEL_TOP, PANEL_BOTTOM = 0, 48      # flush with the top: no band of the dark line above the light panel
    LIGHT = '#bfd3e3'
    GROUP = '#93abbf'                 # the hanging keys' own colour
    GROUP_ON = '#7f92a3'
    PLAYING = '#5d778c'
    RECORDING = '#c8393f'
    MARGIN = 30                       # under the neighbouring lines, which overlap this piece
    ROOM = 64                         # room around the play and record icons, at mid-height
    KEY = 32
    TAB = 2                           # play and record rise this much above the keys and the side pieces
    INK = QColor('#304251')

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.setAttribute(Qt.WA_Hover)
        self.setMouseTracking(True)
        h = self.PANEL_BOTTOM - self.PANEL_TOP
        self.ny = self.PANEL_TOP                              # the keys' slants run from the top (cut to TAB below it)
        self.nh = h - 4                                       # ...to 4px above the panel's bottom: 42px shown, as the side pieces
        self.run = h * shapes.RUN15
        self.rg = self.nh * shapes.RUN15
        x = self.MARGIN
        self.x0 = x
        self.lg_gx = x + self.run / 2 - self.rg               # play from the beginning, flush with the left slant
        lg_right_mid = self.lg_gx + self.rg + self.KEY + self.rg / 2
        self.play_x = lg_right_mid + self.ROOM / 2
        self.pg_x = lg_right_mid + self.ROOM - self.rg / 2    # the ▶|▶ group
        self.pg_w = 48 + 2 * self.rg
        rec_left_mid = self.pg_x + self.pg_w - h / 2 * shapes.RUN15
        self.rec_dot_x = rec_left_mid + self.ROOM / 2
        self.rec_gx = rec_left_mid + self.ROOM - h / 2 * shapes.RUN15
        self.W = self.rec_gx + self.rg + self.KEY + self.run / 2 - x
        self.setFixedSize(int(round(self.W + 2 * self.MARGIN)), self.H)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.play_key = StripKey('playercontrols_playpause_button', self)
        self.play_key.setCheckable(True)
        self.record_key = StripKey('playercontrols_record_button', self)
        self.record_key.setCheckable(True)
        self.begin_key = StripKey('playercontrols_play_from_beginning_button', self)
        self.last_key = StripKey('playercontrols_play_from_last_start_button', self)
        self.next_key = StripKey('playercontrols_play_from_next_start_button', self)
        self.mode_key = StripKey('playercontrols_record_mode_button', self)
        self.mode = 'transcript'
        self.pulse = False
        r = graphic
        self._icons = {'play': r('play_icon.svg'), 'pause': r('pause_icon.svg'),
                       'last': r('play_from_last_start_icon.svg'), 'next': r('play_from_next_start_icon.svg'),
                       'transcript': r('record_transcript_icon.svg'), 'audio': r('record_wave_icon.svg')}
        self._areas()

    # -- geometry ------------------------------------------------------------------------------------------------------
    def panel(self, top=None):
        """The panel's outline from `top` (the widget's top by default): its slants curve into the top line."""
        x0, x1, y0, y1 = self.x0, self.x0 + self.W, self.PANEL_TOP, self.PANEL_BOTTOM
        if top is not None:                                   # the same slants, cut lower
            x0, x1, y0 = x0 + (top - y0) * shapes.RUN15, x1 - (top - y0) * shapes.RUN15, top
        run = (y1 - y0) * shapes.RUN15
        dl, dr = shapes.unit(run, y1 - y0), shapes.unit(run, -(y1 - y0))
        p = QPainterPath(QPointF(x0 - shapes.T6, y0))
        shapes.fillet(p, (x0, y0), (1, 0), dl, shapes.T6)
        shapes.fillet(p, (x0 + run, y1), dl, (1, 0), shapes.T6)
        shapes.fillet(p, (x1 - run, y1), (1, 0), dr, shapes.T6)
        shapes.fillet(p, (x1, y0), dr, (1, 0), shapes.T6)
        p.closeSubpath()
        return p

    def _notch(self, gx, w, flush_left=False, flush_right=False):
        lx = 60 if flush_left else 0
        rx = 60 if flush_right else 0
        return shapes.hanging_trap(gx - lx, self.ny, w + lx + rx, self.nh).intersected(self.panel().united(self.base))

    def _areas(self):
        y0, yb = self.PANEL_TOP, self.PANEL_BOTTOM
        # The outline: below the panel's 1px edge line its slants curve into that line (6px, as everywhere); only
        # the play and record tabs rise above it, to the top.
        self.base = self.panel(self.TAB - 1)
        self.begin_notch = self._notch(self.lg_gx, self.KEY + 2 * self.rg, flush_left=True)
        self.group_notch = self._notch(self.pg_x, self.pg_w)
        self.mode_notch = self._notch(self.rec_gx, self.KEY + 2 * self.rg, flush_right=True)
        notches = self.begin_notch.united(self.group_notch).united(self.mode_notch)
        tabs = self.panel().intersected(shapes.rect_path(QRectF(0, 0, self.width(), self.TAB))).subtracted(notches)
        self.outline = panel = self.base.united(tabs)
        xl_top = self.pg_x + self.pg_w
        self.edge_at = lambda yy: xl_top - (yy - self.ny) * shapes.RUN15     # the group's right slant, carried on
        rec = QPainterPath()
        rec.addPolygon(QPolygonF([QPointF(self.edge_at(y0 - 2), y0 - 2), QPointF(self.width() + 40, y0 - 2),
                                  QPointF(self.width() + 40, yb + 2), QPointF(self.edge_at(yb + 2), yb + 2)]))
        self.record_area = rec.subtracted(shapes.hanging_trap(self.pg_x, self.ny, self.pg_w, self.nh)).intersected(panel)
        self.play_area = panel.subtracted(self.record_area)
        # keys over their areas, masked to them
        group_edges = [self.pg_x - shapes.T6, self.pg_x + self.rg + 24, self.pg_x + self.pg_w + shapes.T6]
        self.last_zone = self.group_notch.intersected(shapes.rect_path(QRectF(group_edges[0], 0, group_edges[1] - group_edges[0], self.H)))
        self.next_zone = self.group_notch.intersected(shapes.rect_path(QRectF(group_edges[1], 0, group_edges[2] - group_edges[1], self.H)))
        # Above TAB only play and record show, as two tabs: the keys' slants carried up are their sides, and what
        # was the keys' curve into the top edge rounds the tabs' top corners.
        below = shapes.rect_path(QRectF(-100, self.TAB, self.width() + 200, self.H))
        self.visible = below.united(tabs)
        for key, path in ((self.play_key, self.play_area.subtracted(self.begin_notch).subtracted(self.group_notch)),
                          (self.record_key, self.record_area.subtracted(self.mode_notch)),
                          (self.begin_key, self.begin_notch.intersected(below)), (self.last_key, self.last_zone.intersected(below)),
                          (self.next_key, self.next_zone.intersected(below)), (self.mode_key, self.mode_notch.intersected(below))):
            box = path.boundingRect().toAlignedRect()
            key.setGeometry(box)
            key.setMask(_mask_of(path, key))
        for key in (self.begin_key, self.last_key, self.next_key, self.mode_key):
            key.raise_()
        # the margins are the neighbours': the playback speed and repeat frames run under them up to the slants,
        # so outside the panel (and its antialiased edge) this piece neither paints nor takes clicks
        stroker = QPainterPathStroker()
        stroker.setWidth(3)
        self.setMask(QRegion(panel.united(stroker.createStroke(panel)).toFillPolygon().toPolygon()))

    # -- state ---------------------------------------------------------------------------------------------------------
    def set_mode(self, mode):
        self.mode = mode
        self.update()

    def set_pulse(self, on):
        self.pulse = on
        self.update()

    # -- painting ------------------------------------------------------------------------------------------------------
    def _group(self, painter, notch, zones, bg, on, hover_alpha, all_sunk=False):
        """A hanging group of keys: one raised fill, then the zones that differ."""
        painter.fillPath(notch, shapes.gradient(self.ny, self.ny + self.nh, bg))
        painter.fillPath(notch.intersected(shapes.rect_path(QRectF(0, self.ny + self.nh - 1, self.width(), 1))),
                         QColor(0, 0, 0, 30))
        for zone, key in zones:
            state = key.state()
            if state == 'sunk' or all_sunk:
                painter.fillPath(zone, shapes.gradient(self.ny, self.ny + self.nh, on, 'sunken'))
                painter.fillPath(zone.intersected(shapes.rect_path(QRectF(0, self.TAB, self.width(), 1))),
                                 QColor(0, 0, 0, 45))
            if state == 'hover':
                painter.fillPath(zone, QColor(255, 255, 255, hover_alpha))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # the panel's top edge line, which runs along the side lines' second row, goes round this piece's outline
        stroker = QPainterPathStroker()
        stroker.setWidth(2)
        ring = stroker.createStroke(self.base).subtracted(self.outline)
        painter.fillPath(ring.intersected(shapes.rect_path(QRectF(0, 1, self.width(), self.H))), EDGE)
        # inside the outline, above the keys, the band is clear: cover the edge line running behind
        band = shapes.rect_path(QRectF(0, 0, self.width(), self.TAB))
        painter.fillPath(self.outline.intersected(band).subtracted(self.visible), BAND)
        painter.setClipPath(self.visible)
        y0, yb = self.PANEL_TOP, self.PANEL_BOTTOM
        h = yb - y0
        cy = y0 + h / 2
        panel = self.outline
        painter.fillPath(panel, shapes.gradient(y0, yb, self.LIGHT))
        playing = self.play_key.isChecked()
        recording = self.record_key.isChecked()
        enabled = self.isEnabled()
        for key, area in ((self.play_key, self.play_area), (self.record_key, self.record_area)):
            if key.state() == 'hover':
                painter.fillPath(area, QColor(255, 255, 255, 50))
        for on, area, base in ((playing, self.play_area, self.PLAYING), (recording, self.record_area, self.RECORDING)):
            if on:
                painter.fillPath(area, shapes.gradient(y0, yb, base, 'sunken'))
                painter.fillPath(area.intersected(shapes.rect_path(QRectF(0, y0, self.width(), 1))), QColor(0, 0, 0, 45))
        # under the ▶|▶ group, play blends into record where their colours differ
        play_c = shapes.bottom_colour(self.PLAYING, 'sunken') if playing else shapes.bottom_colour(self.LIGHT)
        rec_c = shapes.bottom_colour(self.RECORDING, 'sunken') if recording else shapes.bottom_colour(self.LIGHT)
        if play_c != rec_c:
            strip = self.play_area.intersected(shapes.rect_path(QRectF(self.pg_x - 20, self.ny + self.nh - 1, self.pg_w + 40,
                                                                       yb - (self.ny + self.nh - 1) + 2)))
            strip = strip.subtracted(shapes.hanging_trap(self.pg_x, self.ny, self.pg_w, self.nh))
            g = QLinearGradient(self.pg_x + self.rg, 0, self.edge_at(yb), 0)
            clear = QColor(rec_c)
            clear.setAlpha(0)
            g.setColorAt(0, clear)
            g.setColorAt(1, rec_c)
            painter.fillPath(strip, g)

        # play from the beginning: its own colour, a 1px start line, the ▶ on its own, the first subtitle clipped
        self._group(painter, self.begin_notch, [(self.begin_notch, self.begin_key)], self.GROUP, self.GROUP_ON, 60)
        iy = self.TAB + (self.nh - self.TAB - 24) / 2 + 1
        ix = self.lg_gx + self.rg + 4
        painter.save()
        painter.setClipPath(self.begin_notch)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.INK)
        painter.drawRect(QRectF(ix, iy, 1, 24))
        painter.drawPolygon(QPolygonF([QPointF(ix + 3, iy + 9), QPointF(ix + 13, iy + 14), QPointF(ix + 3, iy + 19)]))
        first = QPainterPath()
        first.addRoundedRect(QRectF(ix + 15, iy + 4, 60, 20), 2, 2)
        painter.fillPath(first, self.INK)
        painter.restore()

        # play / pause
        self._icons['pause' if playing else 'play'].render(painter, QRectF(self.play_x - 11, cy - 12, 22, 24))

        # ▶|▶: play from the previous / next subtitle's start; the neighbouring subtitles run out of the icons
        self._group(painter, self.group_notch, [(self.last_zone, self.last_key), (self.next_zone, self.next_key)],
                    self.GROUP, self.GROUP_ON, 60)
        gx = self.pg_x + self.rg
        self._icons['last'].render(painter, QRectF(gx, iy, 24, 24))
        self._icons['next'].render(painter, QRectF(gx + 24, iy, 24, 24))
        top, bottom = iy + 4, iy + 24
        before = QPainterPath()
        before.addRoundedRect(QRectF(self.pg_x - 30, top, gx - 1 - (self.pg_x - 30), bottom - top), 2, 2)
        after = shapes.rect_path(QRectF(gx + 48 - 3, top, self.rg + 23, bottom - top))
        for block in (before, after):
            painter.fillPath(block.intersected(self.group_notch), self.INK)

        # record: a muted red dot at rest, white while recording (dimmer on the pulse's off beat)
        painter.setPen(Qt.NoPen)
        if recording:
            dot = QColor('#ffffff') if not self.pulse else QColor(255, 255, 255, 150)
        else:
            dot = QColor('#7a4651')
        painter.setBrush(dot)
        painter.drawEllipse(QPointF(self.rec_dot_x, cy), 11, 11)
        # the mode key: one key, its icon pushed left, flush with the right slant; red while recording
        self._group(painter, self.mode_notch, [(self.mode_notch, self.mode_key)],
                    '#d4555b' if recording else self.GROUP, '#a62c32' if recording else self.GROUP_ON,
                    22 if recording else 60, all_sunk=recording)
        icon_rect = QRectF(self.rec_gx + self.rg, iy, 24, 24)
        renderer = self._icons['transcript' if self.mode == 'transcript' else 'audio']
        if recording:
            painter.drawImage(icon_rect, _tinted(renderer, icon_rect, '#ffffff'))
        else:
            renderer.render(painter, icon_rect)
        if not enabled:
            painter.fillRect(self.rect(), QColor(48, 66, 81, 140))

    def sizeHint(self):
        return self.size()
