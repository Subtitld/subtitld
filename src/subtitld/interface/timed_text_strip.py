"""The pieces of a subtitle timed word by word, syllable by syllable or letter
by letter (modules.segment), under its text in the subtitles list.

Each piece is a chip, in the order they are sung, wrapping as the text does.
A click chooses one; in the chosen one, a click between two letters cuts it
there, its time shared by letters; a click in the gap between two joins them.
Under the chips, the chosen piece's start and end, in seconds from the
subtitle's start, and how it lights up. Each change is one undo step; its
words are edited in the text above it, as any subtitle's are.
"""

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from subtitld.interface.translation import _
from subtitld.modules import history, segment as segments

_PAD = 7            # inside a chip, either side of its letters
_GAP = 6            # between two chips, where a click joins them
_HEIGHT = 24        # a chip, as tall as the formatting bar's buttons
_RADIUS = 3

_CHIP = QColor('#304251')
_CHOSEN = QColor('#3e5363')
_TEXT = QColor('#b8cee0')
_TEXT_CHOSEN = QColor('#ffffff')

# How a piece lights up, as the menu lists it; '' the style's way.
EFFECTS = ('', *segments.EFFECTS)


def _shown(text):
    """A piece's letters as its chip shows them, with where each starts in
    its text: a line break as ⏎, its trailing spaces left out."""
    letters = [(index, '⏎' if character == '\n' else character) for index, character in segments.letters_of(text)]
    while letters and letters[-1][1].isspace():
        letters.pop()
    return letters


class _Chips(QWidget):
    """The pieces as chips, laid out in rows as wide as the strip."""

    chose = Signal(int)
    cut = Signal(int, int)        # piece, index in its text
    joined = Signal(int)          # piece, joined with the next

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('timed_text_chips')
        self.setMouseTracking(True)
        font = QFont('Montserrat')
        font.setPixelSize(12)
        self.setFont(font)
        self.pieces = []
        self.chosen = 0
        self.hover = None          # ('chip', n) | ('cut', n, index, x) | ('gap', n)
        self._chips = []           # [(rect, [(index, x)])] in this widget's coordinates

    def set_pieces(self, pieces, chosen):
        self.pieces, self.chosen = pieces, chosen
        self.hover = None
        self._lay_out(self.width())
        self.updateGeometry()
        self.update()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._lay_out(width, keep=False)

    def sizeHint(self):
        return QSize(self.width() or 200, self.heightForWidth(self.width() or 200))

    def resizeEvent(self, event):
        self._lay_out(event.size().width())
        super().resizeEvent(event)

    def _lay_out(self, width, keep=True):
        """Place the chips in rows `width` wide; gives the height they take."""
        metrics = QFontMetricsF(self.font())
        chips, x, y = [], 0.0, 0.0
        for piece in self.pieces:
            letters = _shown(piece['text'])
            edges, at = [], _PAD
            for index, character in letters:
                edges.append((index, at))
                at += metrics.horizontalAdvance(character)
            chip_width = max(at + _PAD, 2 * _PAD + 4)
            if x and x + chip_width > width:
                x, y = 0.0, y + _HEIGHT + _GAP
            chips.append((QRectF(x, y, chip_width, _HEIGHT), [(index, x + edge) for index, edge in edges]))
            x += chip_width + _GAP
        if keep:
            self._chips = chips
        return int(y + _HEIGHT) if chips else 0

    def _at(self, point):
        for number, (rect, edges) in enumerate(self._chips):
            if rect.contains(point):
                if number == self.chosen and len(edges) > 1:
                    # The edge between two letters nearest the pointer, not
                    # the one before the first.
                    index, x = min(edges[1:], key=lambda edge: abs(edge[1] - point.x()))
                    if abs(x - point.x()) <= 6:
                        return 'cut', number, index, x
                return 'chip', number
            gap = QRectF(rect.right(), rect.top(), _GAP, rect.height())
            if number + 1 < len(self._chips) and gap.contains(point):
                return 'gap', number
        return None

    def mouseMoveEvent(self, event):
        hover = self._at(event.position())
        if hover != self.hover:
            self.hover = hover
            kind = hover[0] if hover else None
            self.setCursor(Qt.PointingHandCursor if kind == 'chip' else Qt.SplitHCursor if kind == 'cut'
                           else Qt.PointingHandCursor if kind == 'gap' else Qt.ArrowCursor)
            self.setToolTip({'chip': _('timed_text.choose_tip'), 'cut': _('timed_text.cut_tip'),
                             'gap': _('timed_text.join_tip')}.get(kind, ''))
            self.update()

    def leaveEvent(self, event):
        self.hover = None
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        hover = self._at(event.position())
        if hover is None:
            return
        if hover[0] == 'cut':
            self.cut.emit(hover[1], hover[2])
        elif hover[0] == 'gap':
            self.joined.emit(hover[1])
        else:
            self.chose.emit(hover[1])

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self.font())
        for number, (rect, edges) in enumerate(self._chips):
            chosen = number == self.chosen
            painter.setPen(Qt.NoPen)
            painter.setBrush(_CHOSEN if chosen else _CHIP)
            painter.drawRoundedRect(rect, _RADIUS, _RADIUS)
            painter.setPen(_TEXT_CHOSEN if chosen else _TEXT)
            text = ''.join(character for _index, character in _shown(self.pieces[number]['text']))
            painter.drawText(rect.adjusted(_PAD, 0, -_PAD + 1, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
        if self.hover and self.hover[0] == 'cut':
            rect = self._chips[self.hover[1]][0]
            painter.setPen(QPen(_TEXT_CHOSEN, 1))
            painter.drawLine(int(self.hover[3]), int(rect.top() + 4), int(self.hover[3]), int(rect.bottom() - 4))
        elif self.hover and self.hover[0] == 'gap':
            rect = self._chips[self.hover[1]][0]
            painter.setPen(Qt.NoPen)
            painter.setBrush(_TEXT)
            painter.drawRoundedRect(QRectF(rect.right() + _GAP / 2 - 1, rect.top() + 4, 2, rect.height() - 8), 1, 1)
        painter.end()


class TimedTextStrip(QWidget):
    """The pieces of the subtitle `segment()` gives (a callable: the subtitle
    or None), shown while it is timed. `changed` says its pieces changed."""

    changed = Signal()

    def __init__(self, segment, parent=None):
        super().__init__(parent)
        self.setObjectName('timed_text_strip')
        self.setAttribute(Qt.WA_StyledBackground)
        self.segment = segment
        self.chosen = 0
        self._shown = None         # the id of the subtitle shown, its first piece chosen when another is
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 6)
        layout.setSpacing(6)

        self.chips = _Chips(self)
        self.chips.chose.connect(self._choose)
        self.chips.cut.connect(self._cut)
        self.chips.joined.connect(self._join)
        layout.addWidget(self.chips)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.start_label, self.end_label, self.effect_label = QLabel(), QLabel(), QLabel()
        self.start_spin, self.end_spin = QDoubleSpinBox(), QDoubleSpinBox()
        for name, label, spin in (('start', self.start_label, self.start_spin), ('end', self.end_label, self.end_spin)):
            spin.setObjectName(f'timed_text_{name}')
            spin.setDecimals(3)
            spin.setSingleStep(0.01)
            spin.setRange(0.0, 86400.0)
            spin.setSuffix(' s')
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(lambda value, n=name: self._time(n, value))
            label.setBuddy(spin)
            row.addWidget(label)
            row.addWidget(spin)
        row.addSpacing(6)
        self.effect_combo = QComboBox()
        self.effect_combo.setObjectName('timed_text_effect')
        for effect in EFFECTS:
            self.effect_combo.addItem('', effect)
        self.effect_combo.currentIndexChanged.connect(self._effect)
        self.effect_label.setBuddy(self.effect_combo)
        row.addWidget(self.effect_label)
        row.addWidget(self.effect_combo, 1)
        layout.addLayout(row)

        self.retranslate()
        self.refresh()

    def retranslate(self):
        self.start_label.setText(_('timed_text.start'))
        self.end_label.setText(_('timed_text.end'))
        self.start_spin.setToolTip(_('timed_text.start_tip'))
        self.end_spin.setToolTip(_('timed_text.end_tip'))
        self.effect_label.setText(_('timed_text.effect'))
        for number, effect in enumerate(EFFECTS):
            self.effect_combo.setItemText(number, _(f'timed_text.effect_{effect or "style"}'))

    def refresh(self):
        """Show the subtitle's pieces, if it is timed, and the chosen one's
        times and effect."""
        subtitle = self.segment()
        pieces = segments.timed_pieces(subtitle) if subtitle else []
        self.setVisible(bool(pieces))
        if not pieces:
            return
        if subtitle.get('id') != self._shown:
            self._shown, self.chosen = subtitle.get('id'), 0
        self.chosen = min(max(0, self.chosen), len(pieces) - 1)
        self.chips.set_pieces(pieces, self.chosen)
        piece = pieces[self.chosen]
        for spin, value in ((self.start_spin, piece['start']), (self.end_spin, piece['end'])):
            spin.blockSignals(True)
            spin.setValue(float(value))
            spin.blockSignals(False)
        effect = piece.get('effect') or ''
        self.effect_combo.blockSignals(True)
        self.effect_combo.setCurrentIndex(EFFECTS.index(effect) if effect in EFFECTS else 0)
        self.effect_combo.blockSignals(False)

    def choose(self, number):
        """Make piece `number` the chosen one."""
        self.chosen = number
        self.refresh()

    def _choose(self, number):
        self.choose(number)

    def _done(self):
        self.refresh()
        self.changed.emit()

    def _cut(self, number, at):
        subtitle = self.segment()
        if subtitle is None:
            return
        history.history_append()
        if segments.split_piece(subtitle, number, at):
            self.chosen = number + 1
            self._done()

    def _join(self, number):
        subtitle = self.segment()
        if subtitle is None:
            return
        history.history_append()
        if segments.join_pieces(subtitle, number):
            self.chosen = number
            self._done()

    def _time(self, which, value):
        subtitle = self.segment()
        if subtitle is None:
            return
        history.history_append()
        segments.time_piece(subtitle, self.chosen, **{which: value})
        self._done()

    def _effect(self, _index):
        subtitle = self.segment()
        if subtitle is None:
            return
        history.history_append()
        segments.set_effect(subtitle, self.chosen, self.effect_combo.currentData() or None)
        self._done()
