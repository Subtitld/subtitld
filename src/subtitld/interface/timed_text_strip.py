"""The pieces of a subtitle timed word by word, syllable by syllable or letter
by letter (modules.segment), in the subtitles list in place of its text.

Each piece is a field its words are typed in, in the order they are sung,
wrapping as the text does. A click goes into one, at the letter clicked,
and chooses it; Enter or a click away keeps what was typed, Escape leaves it
as it was, Tab goes on to the next (Shift+Tab, back). With Ctrl held, a
click between two letters of a piece cuts it there instead, its time shared
by letters; a click in the gap between two joins them. Under them, the
chosen piece's start and end, in seconds from the subtitle's start, and how
it lights up. Each change is one undo step.
"""

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from subtitld.interface.translation import _
from subtitld.modules import history, segment as segments

_PAD = 7            # inside a field, either side of its letters
_GAP = 6            # between two fields, where a click joins them
_HEIGHT = 24        # a field, as tall as the formatting bar's buttons
_RADIUS = 3

# The fields' own look (stylesheet.qss): sunken, darker at the top.
_FIELD = (QColor('#324350'), QColor('#445b6c'))
_FIELD_HOVER = (QColor('#3a4a58'), QColor('#4c6173'))
_TEXT = QColor('#b8cee0')
_TEXT_CHOSEN = QColor('#ffffff')

# How a piece lights up, as the menu lists it; '' the style's way.
EFFECTS = ('', *segments.EFFECTS)


def _shown(text):
    """A piece's letters as its field shows them, with where each starts in
    its text: a line break as ⏎, its trailing spaces left out."""
    letters = [(index, '⏎' if character == '\n' else character) for index, character in segments.letters_of(text)]
    while letters and letters[-1][1].isspace():
        letters.pop()
    return letters


class CtrlWatch(QObject):
    """While started, tells `changed(held)` each time Ctrl is pressed or let
    go, whichever widget has the keyboard."""

    def __init__(self, changed, parent=None):
        super().__init__(parent)
        self.changed = changed
        self.on = False

    def start(self):
        if not self.on:
            QApplication.instance().installEventFilter(self)
            self.on = True

    def stop(self):
        if self.on:
            QApplication.instance().removeEventFilter(self)
            self.on = False

    def eventFilter(self, watched, event):
        if (event.type() in (QEvent.KeyPress, QEvent.KeyRelease) and event.key() == Qt.Key_Control
                and not event.isAutoRepeat()):
            self.changed(event.type() == QEvent.KeyPress)
        return False


class _Editor(QLineEdit):
    """The field a piece is typed in, over where it is drawn. `finished`
    says how it was left: 'keep', 'cancel', 'next' or 'previous'."""

    finished = Signal(str)

    _KEYS = {Qt.Key_Return: 'keep', Qt.Key_Enter: 'keep', Qt.Key_Escape: 'cancel', Qt.Key_Tab: 'next',
             Qt.Key_Backtab: 'previous'}

    def event(self, event):
        # Taken before the window's shortcuts, and before Tab moves the focus.
        if event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress) and event.key() in self._KEYS:
            if event.type() == QEvent.KeyPress:
                how = self._KEYS[event.key()]
                self.finished.emit('previous' if how == 'next' and event.modifiers() & Qt.ShiftModifier else how)
            event.accept()
            return True
        return super().event(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if event.reason() != Qt.PopupFocusReason:
            self.finished.emit('keep')


class _Chips(QWidget):
    """The pieces as fields, laid out in rows as wide as the strip."""

    chose = Signal(int)
    cut = Signal(int, int)        # piece, index in its text
    joined = Signal(int)          # piece, joined with the next
    typed = Signal(int, str, str)  # piece, its words as typed, how it was left (_Editor.finished)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('timed_text_chips')
        self.setMouseTracking(True)
        # A click here takes the keyboard from a piece being typed in, which keeps what was typed.
        self.setFocusPolicy(Qt.ClickFocus)
        font = QFont('Montserrat')
        font.setPixelSize(12)
        self.setFont(font)
        self.pieces = []
        self.chosen = 0
        self.hover = None          # ('chip', n) | ('cut', n, index, x) | ('gap', n)
        self.editing = None        # the piece being typed in
        self._chips = []           # [(rect, [(index, x)])] in this widget's coordinates
        self.editor = _Editor(self)
        self.editor.setObjectName('timed_text_chip_editor')
        self.editor.setFont(font)
        self.editor.hide()
        self.editor.textEdited.connect(self._typing)
        self.editor.finished.connect(self.finish)
        self.ctrl = CtrlWatch(self._ctrl, self)

    def set_pieces(self, pieces, chosen):
        if self.editing is not None and pieces is not self.pieces:
            # Pieces changed under it (an undo): what was typed is of others.
            self.finish('cancel')
        self.pieces, self.chosen = pieces, chosen
        self.hover = None
        self._lay_out(self.width())
        self._place_editor()
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
        self._place_editor()
        super().resizeEvent(event)

    def _letters(self, number):
        if number == self.editing:
            return list(enumerate(self.editor.text()))
        return _shown(self.pieces[number]['text'])

    def _lay_out(self, width, keep=True):
        """Place the fields in rows `width` wide; gives the height they take."""
        metrics = QFontMetricsF(self.font())
        chips, x, y = [], 0.0, 0.0
        for number in range(len(self.pieces)):
            edges, at = [], _PAD
            for index, character in self._letters(number):
                edges.append((index, at))
                at += metrics.horizontalAdvance(character)
            # Room for the caret after the last letter, while it is typed in.
            chip_width = max(at + _PAD + (2 if number == self.editing else 0), 2 * _PAD + 4)
            if x and x + chip_width > width:
                x, y = 0.0, y + _HEIGHT + _GAP
            chips.append((QRectF(x, y, chip_width, _HEIGHT), [(index, x + edge) for index, edge in edges]))
            x += chip_width + _GAP
        if keep:
            self._chips = chips
        return int(y + _HEIGHT) if chips else 0

    def _at(self, point, ctrl=False):
        for number, (rect, edges) in enumerate(self._chips):
            if rect.contains(point):
                if not ctrl:
                    return 'chip', number
                if len(edges) > 1:
                    # The edge between two letters nearest the pointer, not
                    # the one before the first.
                    index, x = min(edges[1:], key=lambda edge: abs(edge[1] - point.x()))
                    return 'cut', number, index, x
                return None
            gap = QRectF(rect.right(), rect.top(), _GAP, rect.height())
            if number + 1 < len(self._chips) and gap.contains(point):
                return 'gap', number
        return None

    def _caret(self, number, x):
        """How many letters of piece `number` are left of `x`, to the nearest edge."""
        rect, edges = self._chips[number]
        places = [edge for _index, edge in edges] + [rect.right() - _PAD]
        return min(range(len(places)), key=lambda place: abs(places[place] - x))

    def _hover(self, point, ctrl):
        hover = self._at(point, ctrl)
        if hover != self.hover:
            self.hover = hover
            kind = hover[0] if hover else None
            self.setCursor(Qt.IBeamCursor if kind == 'chip' else Qt.SplitHCursor if kind == 'cut'
                           else Qt.PointingHandCursor if kind == 'gap' else Qt.ArrowCursor)
            self.setToolTip({'chip': _('timed_text.type_tip'), 'cut': _('timed_text.cut_tip'),
                             'gap': _('timed_text.join_tip')}.get(kind, ''))
            self.update()

    def _ctrl(self, held):
        if self.underMouse():
            self._hover(self.mapFromGlobal(QCursor.pos()).toPointF(), held)

    def mouseMoveEvent(self, event):
        self._hover(event.position(), bool(event.modifiers() & Qt.ControlModifier))

    def enterEvent(self, event):
        self.ctrl.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.ctrl.stop()
        self.hover = None
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        # A piece still typed in keeps its words first, which may move the others.
        self.finish('keep')
        hover = self._at(event.position(), bool(event.modifiers() & Qt.ControlModifier))
        if hover is None:
            return
        if hover[0] == 'cut':
            self.cut.emit(hover[1], hover[2])
        elif hover[0] == 'gap':
            self.joined.emit(hover[1])
        else:
            self.chose.emit(hover[1])
            self.edit(hover[1], self._caret(hover[1], event.position().x()))

    # --- typing ---------------------------------------------------------------
    def edit(self, number, caret=None):
        """Type in piece `number`: the caret `caret` letters in, or all of
        them selected."""
        if not 0 <= number < len(self.pieces):
            return
        self.editing = number
        self.editor.setText(''.join(character for _index, character in _shown(self.pieces[number]['text'])))
        self._lay_out(self.width())
        self._place_editor()
        self.editor.show()
        self.editor.setFocus(Qt.MouseFocusReason)
        if caret is None:
            self.editor.selectAll()
        else:
            self.editor.setCursorPosition(caret)
        self.update()

    def _place_editor(self):
        if self.editing is not None and self.editing < len(self._chips):
            self.editor.setGeometry(self._chips[self.editing][0].toAlignedRect())

    def _typing(self, _text):
        self._lay_out(self.width())
        self._place_editor()
        self.updateGeometry()
        self.update()

    def finish(self, how):
        """Stop typing in a piece: what was typed kept ('keep', 'next',
        'previous'), or left out ('cancel')."""
        number, self.editing = self.editing, None
        if number is None:
            return
        words = self.editor.text().replace('⏎', '\n')
        if self.editor.hasFocus():
            self.setFocus(Qt.OtherFocusReason)
        self.editor.hide()
        self._lay_out(self.width())
        self.updateGeometry()
        self.update()
        if how != 'cancel':
            self.typed.emit(number, words, how)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self.font())
        for number, (rect, edges) in enumerate(self._chips):
            if number == self.editing:
                continue
            chosen = number == self.chosen
            lit = chosen or (self.hover is not None and self.hover[1] == number and self.hover[0] != 'gap')
            shade = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            for stop, color in zip((0, 1), _FIELD_HOVER if lit else _FIELD):
                shade.setColorAt(stop, color)
            painter.setPen(Qt.NoPen)
            painter.setBrush(shade)
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
        self._subtitle = None      # the subtitle shown, which a piece typed in keeps its words in
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 6)
        layout.setSpacing(6)

        self.chips = _Chips(self)
        self.chips.chose.connect(self._choose)
        self.chips.cut.connect(self._cut)
        self.chips.joined.connect(self._join)
        self.chips.typed.connect(self._type)
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
        if self.chips.editing is not None and not (pieces and subtitle.get('id') == self._shown):
            # Another subtitle chosen while one of this one's pieces is typed
            # in: what was typed is kept, in this one.
            self.chips.finish('keep')
            return self.refresh()
        self._subtitle = subtitle
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

    def _type(self, number, words, how):
        """Piece `number` typed in: its words; then on to the next or the
        one before, if Tab or Shift+Tab left it."""
        subtitle = self._subtitle
        pieces = segments.timed_pieces(subtitle)
        if not 0 <= number < len(pieces):
            return
        became = 1
        if words != segments.piece_words(pieces[number]['text']):
            history.history_append()
            became = segments.set_piece_text(subtitle, number, words)
            self.chosen = number if became else max(0, number - 1)
            self._done()
        if how in ('next', 'previous') and self.segment() is subtitle:
            following = number + became if how == 'next' else number - 1
            if 0 <= following < len(segments.timed_pieces(subtitle)):
                self.choose(following)
                self.chips.edit(following)

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
