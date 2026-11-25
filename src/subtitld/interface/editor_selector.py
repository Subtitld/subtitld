"""A choice in the subtitles panel's editor, beside the speaker's: a small
label over a list, in the editor's slanted look: a 75° left side rounded
into the top (the design's slants, 6 px curves), a faint ground and a faint
line along both. The speaker selector is drawn so too (`paint_slant`).

All of them are HEIGHT tall, their labels LABEL_HEIGHT, so they line up
side by side. The subtitle's theme, a mark's category and a title's theme
are each one.
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from subtitld.interface import theme

HEIGHT = 36                      # the speaker selector's, its picture's height
LABEL_HEIGHT = 14
SLANT = 75                       # degrees, as the design's slants
CURVE = 6.0                      # px, a slant's curve
# How far in the slant reaches at the bottom: what the contents keep clear of.
RUN = HEIGHT / math.tan(math.radians(SLANT))
INSET = int(math.ceil(RUN)) + 2


def slant_path(rect, joined=False):
    """The selector's shape in `rect`: its left side leaning 75° (in at the
    top), rounded into the top edge; square at the right and the bottom,
    where it meets the panel's edge. `joined`: one that goes on from the
    selector at its left, square there too, so the two are one strip."""
    left, top, right, bottom = rect.left(), rect.top(), rect.right() + 1, rect.bottom() + 1
    if joined:
        path = QPainterPath()
        path.addRect(QRectF(left, top, right - left, bottom - top))
        return path
    run = (bottom - top) / math.tan(math.radians(SLANT))
    # The curve between the slant and the top edge: CURVE along each.
    along = QPointF(-math.cos(math.radians(SLANT)), math.sin(math.radians(SLANT)))
    corner = QPointF(left + run, top)
    path = QPainterPath(QPointF(left, bottom))
    path.lineTo(corner + along * CURVE)
    path.quadTo(corner, corner + QPointF(CURVE, 0))
    path.lineTo(right, top)
    path.lineTo(right, bottom)
    path.closeSubpath()
    return path


def paint_slant(widget, joined=False):
    """Draw the slanted ground and edge on `widget` (from its paintEvent);
    `joined`, square on the left, going on from the one before it."""
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.Antialiasing)
    rect = QRectF(widget.rect())
    path = slant_path(rect, joined)
    painter.fillPath(path, theme.color(QColor('#08ffffff')))
    pen = QPen(theme.color(QColor('#14ffffff')), 1)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    if joined:
        # Its top, and where it meets the one before a line as its edge is,
        # from top to bottom.
        painter.drawLine(QPointF(rect.left(), rect.top() + 0.5), QPointF(rect.right() + 1, rect.top() + 0.5))
        painter.drawLine(QPointF(rect.left() + 0.5, rect.top()), QPointF(rect.left() + 0.5, rect.bottom() + 1))
    else:
        # The slant and the top, not the sides it shares with the panel.
        painter.setClipRect(rect.adjusted(0, 0, 0, -1))
        painter.drawPath(path.translated(0.5, 0.5))
    painter.end()


class EditorSelector(QWidget):
    """`label` over `combobox`; `row` takes what goes after the list."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty('class', 'editor_selector')
        self.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed))
        self.setFixedHeight(HEIGHT)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(INSET, 0, 0, 0)
        self.layout().setSpacing(0)
        self.label = QLabel()
        self.label.setProperty('class', 'properties_timing_labels')
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.label.setFixedHeight(LABEL_HEIGHT)
        self.layout().addWidget(self.label)
        self.row = QHBoxLayout()
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(0)
        self.combobox = QComboBox()
        self.combobox.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.row.addWidget(self.combobox, 1)
        self.layout().addLayout(self.row, 1)

    def paintEvent(self, event):
        paint_slant(self)

    @staticmethod
    def join(widget, joined):
        """Make `widget` (a selector) go on from the one at its left, or not:
        square there, and its contents nearer the edge."""
        widget.joined = joined
        margins = widget.layout().contentsMargins()
        widget.layout().setContentsMargins(4 if joined else INSET, margins.top(), margins.right(), margins.bottom())
        widget.update()

    def fit(self):
        """As wide as its longest item, padding and arrow too (a stylesheet's
        padding is not in the size Qt works out from the items)."""
        metrics = self.combobox.fontMetrics()
        widest = max([metrics.horizontalAdvance(self.combobox.itemText(i)) for i in range(self.combobox.count())] + [0])
        self.combobox.setMinimumWidth(widest + 40)

    def setLabel(self, text):
        self.label.setText(text)
        self.combobox.setAccessibleName(text)
