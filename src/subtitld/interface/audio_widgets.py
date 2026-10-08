"""Small audio widgets for the Audio panel, drawn like a compact DAW:

* ``Knob`` — a rotary control: drag up/down (Shift for fine), wheel, arrow
  keys; double-click returns it to its default.
* ``LevelMeter`` — a segmented peak meter with a falling peak-hold.
* ``EqGraph`` — the equalizer's frequency response, its bands as handles
  to drag (frequency across, gain up and down; wheel for the width).
* ``DynamicsGraph`` — a compressor's or gate's transfer curve, with its
  threshold to drag, the live input on the curve and a gain-reduction bar.
* ``PowerButton`` — an effect's on/off switch.

All of them paint themselves; the panel's stylesheet does not reach inside.
"""

import math
import time

import numpy as np

from PySide6.QtWidgets import QWidget, QAbstractButton, QSizePolicy
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QSize
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QFont, QLinearGradient, QFontMetrics

from subtitld.modules import audio_effects

TEXT = QColor('#b8cee0')
TEXT_DIM = QColor(184, 206, 224, 110)
GRID = QColor(255, 255, 255, 14)
GRID_STRONG = QColor(255, 255, 255, 30)
PANEL = QColor('#121a20')

GREEN = QColor('#5fc48a')
YELLOW = QColor('#e7c35a')
RED = QColor('#ff6b6b')

# Colours by effect, used for the card stripe, curves and knob arcs.
EFFECT_COLORS = {'eq': '#8fd0ea', 'compressor': '#e7b85a', 'gate': '#c9b3f0'}
# EQ band handles, low to high.
BAND_COLORS = ('#e7b85a', '#8fd0ea', '#c9b3f0')


def _small_font(widget, size=8, bold=True):
    font = QFont(widget.font())
    font.setPixelSize(size)
    font.setBold(bold)
    return font


def format_hz(value):
    return f'{value / 1000:.1f}k' if value >= 1000 else f'{value:.0f}'


def format_db(value):
    return f'{value:+.1f}' if abs(value) >= 0.05 else '0.0'


def format_ms(value):
    return f'{value:.1f}' if value < 10 else f'{value:.0f}'


# ---------------------------------------------------------------------------
# Knob
# ---------------------------------------------------------------------------

class Knob(QWidget):
    """A rotary control over [minimum, maximum], linear or logarithmic.

    `fmt` turns the value into the text under the dial; `unit` follows it,
    smaller. A range that crosses zero (a gain) is drawn from the middle.
    """

    valueChanged = Signal(float)

    _SWEEP = 270.0          # degrees from minimum to maximum
    _DRAG_PIXELS = 160.0    # a full sweep, dragging

    def __init__(self, label, minimum, maximum, value, default=None, log=False, fmt=None, unit='',
                 color='#8fd0ea', parent=None):
        super().__init__(parent)
        self.label = label
        self.minimum, self.maximum = float(minimum), float(maximum)
        self.log = bool(log) and self.minimum > 0
        self.default = float(value if default is None else default)
        self.fmt = fmt or (lambda v: f'{v:.1f}')
        self.unit = unit
        self.color = QColor(color)
        self._value = self._clamp(float(value))
        self._drag = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.SizeVerCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setToolTip(label)

    def sizeHint(self):
        return QSize(58, 76)

    def minimumSizeHint(self):
        return self.sizeHint()

    # -- value -----------------------------------------------------------------

    def _clamp(self, value):
        return max(self.minimum, min(self.maximum, value))

    def to_unit(self, value):
        if self.log:
            return math.log(value / self.minimum) / math.log(self.maximum / self.minimum)
        return (value - self.minimum) / (self.maximum - self.minimum)

    def from_unit(self, unit):
        unit = max(0.0, min(1.0, unit))
        if self.log:
            return self.minimum * (self.maximum / self.minimum) ** unit
        return self.minimum + unit * (self.maximum - self.minimum)

    def value(self):
        return self._value

    def setValue(self, value, emit=False):
        value = self._clamp(float(value))
        if abs(value - self._value) < 1e-9:
            return
        self._value = value
        self.update()
        if emit:
            self.valueChanged.emit(value)

    def _nudge(self, units):
        self.setValue(self.from_unit(self.to_unit(self._value) + units), emit=True)

    # -- input -----------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = (event.position().y(), self.to_unit(self._value))
            self.setFocus(Qt.MouseFocusReason)

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return
        y, start = self._drag
        scale = 0.1 if event.modifiers() & Qt.ShiftModifier else 1.0
        self.setValue(self.from_unit(start + (y - event.position().y()) / self._DRAG_PIXELS * scale), emit=True)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def mouseDoubleClickEvent(self, event):
        self.setValue(self.default, emit=True)

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        self._nudge(steps * (0.004 if event.modifiers() & Qt.ShiftModifier else 0.02))
        event.accept()

    def keyPressEvent(self, event):
        steps = {Qt.Key_Up: 0.01, Qt.Key_Right: 0.01, Qt.Key_Down: -0.01, Qt.Key_Left: -0.01,
                 Qt.Key_PageUp: 0.1, Qt.Key_PageDown: -0.1}
        if event.key() in steps:
            self._nudge(steps[event.key()])
        elif event.key() in (Qt.Key_Home, Qt.Key_End):
            self.setValue(self.minimum if event.key() == Qt.Key_Home else self.maximum, emit=True)
        else:
            super().keyPressEvent(event)

    # -- paint -----------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = self.width()

        painter.setFont(_small_font(self, 8))
        painter.setPen(TEXT_DIM if not self.hasFocus() else TEXT)
        painter.drawText(QRectF(0, 0, width, 12), Qt.AlignCenter, self.label.upper())

        size = 38
        dial = QRectF((width - size) / 2, 15, size, size)
        start_angle = 225.0                       # Qt: degrees, counter-clockwise from 3 o'clock
        unit = self.to_unit(self._value)

        track_pen = QPen(QColor(255, 255, 255, 26), 3.5, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(dial, int(start_angle * 16), int(-self._SWEEP * 16))

        origin = 0.0
        if not self.log and self.minimum < 0 < self.maximum:
            origin = self.to_unit(0.0)            # bipolar: grow from the middle
        arc_pen = QPen(self.color if self.isEnabled() else TEXT_DIM, 3.5, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(arc_pen)
        span = (unit - origin) * self._SWEEP
        if abs(span) > 0.5:
            painter.drawArc(dial, int((start_angle - origin * self._SWEEP) * 16), int(-span * 16))

        body = dial.adjusted(6, 6, -6, -6)
        gradient = QLinearGradient(body.topLeft(), body.bottomLeft())
        gradient.setColorAt(0, QColor('#3e5363'))
        gradient.setColorAt(1, QColor('#26333e'))
        painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
        painter.setBrush(gradient)
        painter.drawEllipse(body)

        angle = math.radians(start_angle - unit * self._SWEEP)
        center = body.center()
        radius = body.width() / 2 - 2.5
        tip = QPointF(center.x() + math.cos(angle) * radius, center.y() - math.sin(angle) * radius)
        inner = QPointF(center.x() + math.cos(angle) * radius * 0.35, center.y() - math.sin(angle) * radius * 0.35)
        painter.setPen(QPen(QColor('#e8f1f8'), 2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(inner, tip)

        painter.setFont(_small_font(self, 10, bold=False))
        painter.setPen(TEXT)
        text = self.fmt(self._value)
        value_rect = QRectF(0, 56, width, 14)
        if self.unit:
            metrics = QFontMetrics(painter.font())
            unit_font = _small_font(self, 8, bold=False)
            unit_width = QFontMetrics(unit_font).horizontalAdvance(' ' + self.unit)
            text_width = metrics.horizontalAdvance(text)
            left = (width - text_width - unit_width) / 2
            painter.drawText(QRectF(left, 56, text_width + 1, 14), Qt.AlignLeft | Qt.AlignVCenter, text)
            painter.setFont(unit_font)
            painter.setPen(TEXT_DIM)
            painter.drawText(QRectF(left + text_width, 56, unit_width + 1, 14), Qt.AlignLeft | Qt.AlignVCenter, ' ' + self.unit)
        else:
            painter.drawText(value_rect, Qt.AlignCenter, text)
        painter.end()


# ---------------------------------------------------------------------------
# Level meter
# ---------------------------------------------------------------------------

class LevelMeter(QWidget):
    """A horizontal peak meter, -60 to 0 dBFS, in lit segments: green, then
    yellow from -18 dB, red from -6 dB. Rises at once, falls at 24 dB/s; a
    hold line keeps the peak for a moment. With `scale`, the dB marks are
    written underneath."""

    FLOOR = -60.0

    def __init__(self, scale=False, parent=None):
        super().__init__(parent)
        self.scale = scale
        self._display = self.FLOOR
        self._hold = self.FLOOR
        self._hold_at = 0.0
        self._last = time.monotonic()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(22 if scale else 6)

    def level_db(self):
        return self._display

    def set_level(self, peak):
        """`peak`: the latest linear peak (0..1)."""
        now = time.monotonic()
        elapsed = min(0.5, now - self._last)
        self._last = now
        target = 20.0 * math.log10(peak) if peak and peak > 1e-6 else self.FLOOR
        target = max(self.FLOOR, min(0.0, target))
        if target >= self._display:
            self._display = target
        else:
            self._display = max(target, self._display - 24.0 * elapsed)
        if self._display >= self._hold or now - self._hold_at > 1.2:
            if self._display >= self._hold:
                self._hold, self._hold_at = self._display, now
            else:
                self._hold = max(self._display, self._hold - 12.0 * elapsed)
        self.update()

    def _x(self, db, width):
        return (db - self.FLOOR) / -self.FLOOR * width

    def paintEvent(self, event):
        painter = QPainter(self)
        width = self.width()
        bar_height = 8 if self.scale else self.height()
        painter.fillRect(QRectF(0, 0, width, bar_height), PANEL)
        segment, gap = 3, 1
        x = 0
        lit_to = self._x(self._display, width)
        while x < width:
            db = self.FLOOR + (x + segment / 2) / width * -self.FLOOR
            color = RED if db > -6 else YELLOW if db > -18 else GREEN
            if x + segment > lit_to:
                color = QColor(color)
                color.setAlpha(34)
            painter.fillRect(QRectF(x, 0, min(segment, width - x), bar_height), color)
            x += segment + gap
        if self._hold > self.FLOOR + 0.5:
            hx = self._x(self._hold, width)
            painter.fillRect(QRectF(max(0, hx - 1.5), 0, 2, bar_height), QColor('#e8f1f8'))
        if self.scale:
            painter.setFont(_small_font(self, 8, bold=False))
            painter.setPen(TEXT_DIM)
            for mark in (-60, -40, -30, -20, -12, -6, -3, 0):
                mx = self._x(mark, width)
                painter.fillRect(QRectF(mx - 0.5, bar_height + 1, 1, 3), TEXT_DIM)
                label = str(mark)
                align = Qt.AlignHCenter
                rect = QRectF(mx - 15, bar_height + 3, 30, 11)
                if mark == -60:
                    rect, align = QRectF(0, bar_height + 3, 30, 11), Qt.AlignLeft
                elif mark == 0:
                    rect, align = QRectF(width - 30, bar_height + 3, 30, 11), Qt.AlignRight
                painter.drawText(rect, align | Qt.AlignTop, label)
        painter.end()


# ---------------------------------------------------------------------------
# Graph base
# ---------------------------------------------------------------------------

class _Graph(QWidget):
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(118)
        self.setMouseTracking(True)

    def sizeHint(self):
        return QSize(280, 118)

    def plot_rect(self):
        return QRectF(26, 8, max(10, self.width() - 34), self.height() - 24)

    def _background(self, painter):
        painter.fillRect(self.rect(), PANEL)
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
        painter.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))


# ---------------------------------------------------------------------------
# EQ graph
# ---------------------------------------------------------------------------

class EqGraph(_Graph):
    """The response of `params['bands']` (the live model dicts), 20 Hz to
    20 kHz, ±24 dB. Each band is a handle: drag it across for the frequency
    and up or down for the gain; the wheel over it widens or narrows it;
    a double-click flattens it."""

    bandChanged = Signal(int)
    bandSelected = Signal(int)

    F_MIN, F_MAX, DB = 20.0, 20000.0, 24.0

    def __init__(self, params, color=EFFECT_COLORS['eq'], parent=None):
        super().__init__(color, parent)
        self.params = params
        self.selected = 1
        self._dragging = None
        self._hover = None

    def bands(self):
        return self.params.get('bands', [])

    def _fx(self, freq, rect):
        unit = math.log10(max(self.F_MIN, freq) / self.F_MIN) / math.log10(self.F_MAX / self.F_MIN)
        return rect.left() + unit * rect.width()

    def _freq_at(self, x, rect):
        unit = max(0.0, min(1.0, (x - rect.left()) / rect.width()))
        return self.F_MIN * (self.F_MAX / self.F_MIN) ** unit

    def _fy(self, db, rect):
        return rect.center().y() - db / self.DB * rect.height() / 2

    def _db_at(self, y, rect):
        return max(-self.DB, min(self.DB, (rect.center().y() - y) / (rect.height() / 2) * self.DB))

    def _handle(self, index, rect):
        band = self.bands()[index]
        return QPointF(self._fx(float(band.get('freq', 1000)), rect), self._fy(float(band.get('gain', 0)), rect))

    def _band_at(self, pos):
        rect = self.plot_rect()
        best, best_distance = None, 14.0
        for index in range(len(self.bands())):
            point = self._handle(index, rect)
            distance = math.hypot(point.x() - pos.x(), point.y() - pos.y())
            if distance < best_distance:
                best, best_distance = index, distance
        return best

    def select(self, index):
        if index is not None and index != self.selected:
            self.selected = index
            self.update()
            self.bandSelected.emit(index)

    def mousePressEvent(self, event):
        index = self._band_at(event.position())
        if index is not None:
            self.select(index)
            self._dragging = index

    def mouseMoveEvent(self, event):
        rect = self.plot_rect()
        if self._dragging is None:
            hover = self._band_at(event.position())
            if hover != self._hover:
                self._hover = hover
                self.setCursor(Qt.SizeAllCursor if hover is not None else Qt.ArrowCursor)
                self.update()
            return
        band = self.bands()[self._dragging]
        band['freq'] = round(self._freq_at(event.position().x(), rect), 1)
        band['gain'] = round(self._db_at(event.position().y(), rect) * 2) / 2   # 0.5 dB steps
        self.update()
        self.bandChanged.emit(self._dragging)

    def mouseReleaseEvent(self, event):
        self._dragging = None

    def mouseDoubleClickEvent(self, event):
        index = self._band_at(event.position())
        if index is not None:
            self.bands()[index]['gain'] = 0.0
            self.update()
            self.bandChanged.emit(index)

    def wheelEvent(self, event):
        index = self._band_at(event.position())
        if index is None:
            index = self.selected
        if index is None or index >= len(self.bands()):
            return
        band = self.bands()[index]
        steps = event.angleDelta().y() / 120.0
        band['q'] = round(max(0.1, min(10.0, float(band.get('q', 1.0)) * (1.12 ** steps))), 3)
        self.select(index)
        self.update()
        self.bandChanged.emit(index)
        event.accept()

    def leaveEvent(self, event):
        self._hover = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self._background(painter)
        rect = self.plot_rect()
        painter.setFont(_small_font(self, 8, bold=False))

        for freq in (50, 100, 200, 500, 1000, 2000, 5000, 10000):
            x = self._fx(freq, rect)
            painter.setPen(QPen(GRID_STRONG if freq in (100, 1000, 10000) else GRID, 1))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            if freq in (100, 1000, 10000):
                painter.setPen(TEXT_DIM)
                painter.drawText(QRectF(x - 20, rect.bottom() + 2, 40, 12), Qt.AlignCenter, format_hz(freq))
        for db in (-12, 0, 12):
            y = self._fy(db, rect)
            painter.setPen(QPen(GRID_STRONG if db == 0 else GRID, 1, Qt.SolidLine if db == 0 else Qt.DashLine))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(TEXT_DIM)
            painter.drawText(QRectF(0, y - 6, rect.left() - 4, 12), Qt.AlignRight | Qt.AlignVCenter, f'{db:+d}' if db else '0')

        xs = np.linspace(rect.left(), rect.right(), max(60, int(rect.width() / 2)))
        freqs = np.array([self._freq_at(x, rect) for x in xs])
        bands = self.bands()

        # Each band's own shape, faint, under the combined curve.
        for index, band in enumerate(bands):
            response = audio_effects.eq_response({'bands': [band]}, freqs)
            path = QPainterPath()
            for i, (x, db) in enumerate(zip(xs, response)):
                point = QPointF(x, self._fy(max(-self.DB, min(self.DB, db)), rect))
                path.moveTo(point) if i == 0 else path.lineTo(point)
            color = QColor(BAND_COLORS[index % len(BAND_COLORS)])
            color.setAlpha(110 if index == self.selected else 50)
            painter.setPen(QPen(color, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

        response = audio_effects.eq_response(self.params, freqs)
        curve = QPainterPath()
        for i, (x, db) in enumerate(zip(xs, response)):
            point = QPointF(x, self._fy(max(-self.DB, min(self.DB, db)), rect))
            curve.moveTo(point) if i == 0 else curve.lineTo(point)
        fill = QPainterPath(curve)
        zero = self._fy(0, rect)
        fill.lineTo(QPointF(xs[-1], zero))
        fill.lineTo(QPointF(xs[0], zero))
        fill.closeSubpath()
        glow = QColor(self.color)
        glow.setAlpha(40)
        painter.fillPath(fill, glow)
        painter.setPen(QPen(self.color if self.isEnabled() else TEXT_DIM, 2))
        painter.drawPath(curve)

        for index, band in enumerate(bands):
            point = self._handle(index, rect)
            color = QColor(BAND_COLORS[index % len(BAND_COLORS)])
            radius = 6.5 if index in (self.selected, self._hover) else 5
            painter.setPen(QPen(QColor('#e8f1f8') if index == self.selected else QColor(0, 0, 0, 120), 1.5))
            painter.setBrush(color)
            painter.drawEllipse(point, radius, radius)
            painter.setPen(QColor('#121a20'))
            painter.setFont(_small_font(self, 7))
            painter.drawText(QRectF(point.x() - 6, point.y() - 6, 12, 12), Qt.AlignCenter, str(index + 1))

        if self.selected is not None and self.selected < len(bands):
            band = bands[self.selected]
            text = f'{format_hz(float(band.get("freq", 1000)))} Hz   {format_db(float(band.get("gain", 0)))} dB   Q {float(band.get("q", 1)):.2f}'
            painter.setFont(_small_font(self, 8, bold=False))
            painter.setPen(TEXT)
            painter.drawText(QRectF(rect.left() + 4, rect.top() + 1, rect.width() - 8, 12), Qt.AlignRight | Qt.AlignTop, text)
        painter.end()


# ---------------------------------------------------------------------------
# Dynamics graph
# ---------------------------------------------------------------------------

class DynamicsGraph(_Graph):
    """A compressor's or a gate's transfer curve: input level across, output
    up. The dashed line is the threshold — drag it. A dot rides the curve at
    the live input level, and the bar on the right shows the gain reduction."""

    thresholdChanged = Signal(float)

    def __init__(self, kind, params, color=None, parent=None):
        super().__init__(color or EFFECT_COLORS[kind], parent)
        self.kind = kind
        self.params = params
        self.floor = -80.0 if kind == 'gate' else -60.0
        self.input_db = None
        self.reduction_db = 0.0
        self._dragging = False

    def plot_rect(self):
        rect = super().plot_rect()
        return rect.adjusted(0, 0, -14, 0)      # room for the reduction bar

    def _x(self, db, rect):
        return rect.left() + (db - self.floor) / -self.floor * rect.width()

    def _y(self, db, rect):
        return rect.bottom() - (max(self.floor, min(0.0, db)) - self.floor) / -self.floor * rect.height()

    def _db_at_x(self, x, rect):
        return max(self.floor, min(0.0, self.floor + (x - rect.left()) / rect.width() * -self.floor))

    def output_db(self, input_db):
        if self.kind == 'gate':
            return audio_effects.gate_output_db(self.params, input_db, floor_db=self.floor)
        return audio_effects.compressor_output_db(self.params, input_db)

    def set_live(self, input_db, reduction_db):
        """The processor's last input level and gain reduction (dB), or
        None for the input when nothing plays."""
        self.input_db = input_db
        self.reduction_db = min(0.0, reduction_db or 0.0)
        self.update()

    def _near_threshold(self, x):
        rect = self.plot_rect()
        return abs(x - self._x(float(self.params.get('threshold_db', -18)), rect)) < 8

    def mousePressEvent(self, event):
        if self.plot_rect().contains(event.position()) or self._near_threshold(event.position().x()):
            self._dragging = True
            self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event):
        if not self._dragging:
            self.setCursor(Qt.SizeHorCursor if self._near_threshold(event.position().x()) else Qt.ArrowCursor)
            return
        value = round(self._db_at_x(event.position().x(), self.plot_rect()))
        if value != self.params.get('threshold_db'):
            self.params['threshold_db'] = float(value)
            self.update()
            self.thresholdChanged.emit(float(value))

    def mouseReleaseEvent(self, event):
        self._dragging = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self._background(painter)
        rect = self.plot_rect()
        painter.setFont(_small_font(self, 8, bold=False))

        step = 20 if self.kind == 'gate' else 12
        db = 0.0
        while db >= self.floor:
            x, y = self._x(db, rect), self._y(db, rect)
            painter.setPen(QPen(GRID, 1))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(TEXT_DIM)
            painter.drawText(QRectF(0, y - 6, rect.left() - 4, 12), Qt.AlignRight | Qt.AlignVCenter, f'{db:.0f}')
            if db > self.floor:
                painter.drawText(QRectF(x - 16, rect.bottom() + 2, 32, 12), Qt.AlignCenter, f'{db:.0f}')
            db -= step

        painter.setPen(QPen(GRID_STRONG, 1, Qt.DashLine))
        painter.drawLine(QPointF(rect.left(), rect.bottom()), QPointF(rect.right(), rect.top()))

        xs = np.linspace(self.floor, 0.0, 160)
        ys = self.output_db(xs)
        curve = QPainterPath()
        for i, (x_db, y_db) in enumerate(zip(xs, ys)):
            point = QPointF(self._x(x_db, rect), self._y(y_db, rect))
            curve.moveTo(point) if i == 0 else curve.lineTo(point)
        painter.setPen(QPen(self.color if self.isEnabled() else TEXT_DIM, 2))
        painter.drawPath(curve)

        threshold = float(self.params.get('threshold_db', -18))
        tx = self._x(threshold, rect)
        painter.setPen(QPen(QColor('#e8f1f8'), 1, Qt.DashLine))
        painter.drawLine(QPointF(tx, rect.top()), QPointF(tx, rect.bottom()))
        painter.setPen(TEXT)
        label_rect = QRectF(tx + 3, rect.top() + 1, 60, 12)
        if label_rect.right() > rect.right():
            label_rect = QRectF(tx - 63, rect.top() + 1, 60, 12)
            painter.drawText(label_rect, Qt.AlignRight | Qt.AlignTop, f'{threshold:.0f} dB')
        else:
            painter.drawText(label_rect, Qt.AlignLeft | Qt.AlignTop, f'{threshold:.0f} dB')

        if self.input_db is not None and self.input_db > self.floor:
            out = float(self.output_db(np.array([self.input_db]))[0])
            point = QPointF(self._x(self.input_db, rect), self._y(out, rect))
            painter.setPen(Qt.NoPen)
            glow = QColor(self.color)
            glow.setAlpha(70)
            painter.setBrush(glow)
            painter.drawEllipse(point, 7, 7)
            painter.setBrush(QColor('#e8f1f8'))
            painter.drawEllipse(point, 3.5, 3.5)

        bar = QRectF(rect.right() + 6, rect.top(), 6, rect.height())
        painter.fillRect(bar, QColor(255, 255, 255, 12))
        depth = min(1.0, -self.reduction_db / 24.0)
        if depth > 0:
            painter.fillRect(QRectF(bar.left(), bar.top(), bar.width(), bar.height() * depth), self.color)
        painter.end()


# ---------------------------------------------------------------------------
# Power button
# ---------------------------------------------------------------------------

class PowerButton(QAbstractButton):
    """An effect's on/off switch: a power symbol, lit when on."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(20, 20)

    def sizeHint(self):
        return QSize(20, 20)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        color = GREEN if on else QColor(184, 206, 224, 90)
        if self.underMouse():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 18))
            painter.drawEllipse(QRectF(1, 1, 18, 18))
        if on:
            glow = QColor(GREEN)
            glow.setAlpha(45)
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(QRectF(2, 2, 16, 16))
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(QRectF(5, 5.5, 10, 10), int(120 * 16), int(300 * 16))
        painter.drawLine(QPointF(10, 4), QPointF(10, 10))
        painter.end()

    def enterEvent(self, event):
        self.update()

    def leaveEvent(self, event):
        self.update()
