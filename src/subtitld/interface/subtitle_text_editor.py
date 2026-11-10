"""A subtitle's text, edited as it looks.

The subtitle list's editor shows a subtitle's italic, bold, underline,
strikeout and colours, not tags, and turns what is typed and formatted
there back into the tags the text keeps (modules.markup). The override
codes a text starts with, such as its position ({\\an8}), are not text to
edit: the editor keeps them aside (`lead`) and puts them back.

The formatting commands act on the selection, or on the whole text when
nothing is selected, and toggle: italic on a text all italic takes it off.
"""

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap, QTextCharFormat,
                           QTextCursor, QTextFormat)
from PySide6.QtWidgets import (QColorDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QWidget,
                               QWidgetAction)

from subtitld.interface.translation import _
from subtitld.modules import history, look as subtitld_look, markup, position, session

FLAGS = ('italic', 'bold', 'underline', 'strike')
# A reading and what it reads have no look of their own to go by, so the
# format carries which of the two a run is; without it an edit would leave
# the reading loose in the text it reads.
_RUBY_ROLE = QTextFormat.UserProperty + 1


def _char_format(style):
    fmt = QTextCharFormat()
    fmt.setFontItalic(style.italic)
    if style.bold:
        # Otherwise the editor's own weight, not Normal's lighter one.
        fmt.setFontWeight(QFont.Bold)
    fmt.setFontUnderline(style.underline)
    fmt.setFontStrikeOut(style.strike)
    if style.color:
        fmt.setForeground(QColor(style.color))
    if style.ruby:
        fmt.setProperty(_RUBY_ROLE, style.ruby)
        if style.ruby == 'text':
            # Raised and smaller, where ruby draws it above.
            fmt.setVerticalAlignment(QTextCharFormat.AlignSuperScript)
    return fmt


def _style(fmt):
    color = ''
    if fmt.hasProperty(QTextFormat.ForegroundBrush) and fmt.foreground().style() != Qt.NoBrush:
        color = fmt.foreground().color().name()
    role = fmt.property(_RUBY_ROLE)
    return markup.Style(fmt.fontItalic(), fmt.fontWeight() >= QFont.DemiBold, fmt.fontUnderline(),
                        fmt.fontStrikeOut(), color, role if role in markup.RUBY_ROLES else '')


def set_text(textedit, text):
    """Show `text` in `textedit`, formatted, keeping its leading codes aside."""
    lead, rest = markup.split_lead(text)
    textedit.subtitle_lead = lead
    textedit.clear()
    cursor = QTextCursor(textedit.document())
    for run, style in markup.spans(rest):
        cursor.insertText(run, _char_format(style))
    if not rest:
        # Typing in an empty editor starts plain.
        textedit.setCurrentCharFormat(_char_format(markup.PLAIN))


def runs(textedit, start=0, end=None):
    """[(text, Style)] for the editor's text between two positions."""
    document = textedit.document()
    end = document.characterCount() - 1 if end is None else end
    found = []
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            first, last = fragment.position(), fragment.position() + fragment.length()
            low, high = max(first, start), min(last, end)
            if low < high:
                found.append((fragment.text()[low - first:high - first], _style(fragment.charFormat())))
            iterator += 1
        separator = block.position() + block.length() - 1
        if block.next().isValid() and start <= separator < end:
            found.append(('\n', None))
        block = block.next()
    # A line break takes the styles the text on both sides of it shares, so
    # a tag spans the lines it covers and ends on the line it ends on.
    for index, (run, style) in enumerate(found):
        if style is None:
            before = next((s for r, s in reversed(found[:index]) if s is not None), markup.PLAIN)
            after = next((s for r, s in found[index + 1:] if s is not None), markup.PLAIN)
            found[index] = (run, markup.Style(*(a and b for a, b in zip(before[:4], after[:4])),
                                              before.color if before.color == after.color else '',
                                              before.ruby if before.ruby == after.ruby else ''))
    return found


def text(textedit):
    """The editor's text as the subtitle keeps it: codes, then tags."""
    return getattr(textedit, 'subtitle_lead', '') + markup.from_spans(runs(textedit))


def halves(textedit, position):
    """(before, after) the editor's text cut at `position`, each well formed,
    the position code carried to both."""
    before = getattr(textedit, 'subtitle_lead', '') + markup.from_spans(runs(textedit, 0, position))
    after = markup.from_spans(runs(textedit, position))
    return markup.rebalance(before, after)


def _range(textedit):
    cursor = textedit.textCursor()
    if cursor.hasSelection():
        return cursor.selectionStart(), cursor.selectionEnd()
    return 0, textedit.document().characterCount() - 1


def _restyle(textedit, change):
    """Give the selection (or the whole text) the styles `change(style)`
    returns, as one step of the editor's undo."""
    start, end = _range(textedit)
    if start >= end:
        # Nothing to format: what is typed next takes the style.
        textedit.setCurrentCharFormat(_char_format(change(_style(textedit.currentCharFormat()))))
        return
    restyled = [(run, change(style)) for run, style in runs(textedit, start, end)]
    cursor = textedit.textCursor()
    selected = cursor.hasSelection()
    cursor.beginEditBlock()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.KeepAnchor)
    cursor.removeSelectedText()
    for run, style in restyled:
        cursor.insertText(run, _char_format(style))
    cursor.endEditBlock()
    if selected:
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        textedit.setTextCursor(cursor)


def is_on(textedit, flag):
    """Whether the selection (or the whole text) is all `flag`."""
    start, end = _range(textedit)
    if start >= end:
        return getattr(_style(textedit.currentCharFormat()), flag)
    found = [style for run, style in runs(textedit, start, end) if run.strip()]
    return bool(found) and all(getattr(style, flag) for style in found)


def toggle(textedit, flag):
    """Turn `flag` ('italic', 'bold', 'underline', 'strike') on for the
    selection, or off when it is all on already."""
    value = not is_on(textedit, flag)
    _restyle(textedit, lambda style: style._replace(**{flag: value}))


def set_color(textedit, color):
    """Colour the selection: '#rrggbb', or '' for the default."""
    _restyle(textedit, lambda style: style._replace(color=color or ''))


def clear_formatting(textedit):
    """Take every style off the selection."""
    _restyle(textedit, lambda style: markup.PLAIN)


def alignment(textedit):
    """Where the text is placed, as a keypad number (2: bottom centre)."""
    return markup.alignment(getattr(textedit, 'subtitle_lead', ''))


def set_alignment(textedit, keypad):
    textedit.subtitle_lead = markup.with_alignment(getattr(textedit, 'subtitle_lead', ''), keypad)


# Colours that read over video, offered first; any other is a dialog away.
SWATCHES = ('#ffffff', '#ffff00', '#00ffff', '#00ff00', '#ff00ff', '#ff0000', '#ffa500', '#8ab4ff')
_KEYPAD_ROWS = ((7, 8, 9), (4, 5, 6), (1, 2, 3))
_POSITION_KEYS = {7: 'top_left', 8: 'top', 9: 'top_right', 4: 'left', 5: 'middle', 6: 'right',
                  1: 'bottom_left', 2: 'bottom', 3: 'bottom_right'}


def _pixmap(size):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    return pixmap


def _position_icon(keypad, color='#b8cee0', size=16):
    """The screen as nine places, the one for `keypad` lit."""
    pixmap = _pixmap(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(color), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(0.5, 1.5, size - 1, size - 3, 2, 2)
    cell_w, cell_h = (size - 3) / 3, (size - 5) / 3
    for row, keys in enumerate(_KEYPAD_ROWS):
        for column, key in enumerate(keys):
            cx = 1.5 + cell_w * (column + 0.5)
            cy = 2.5 + cell_h * (row + 0.5)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(color))
            if key == keypad:
                painter.drawRoundedRect(cx - cell_w / 2 + 0.5, cy - cell_h / 2 + 0.5, cell_w - 1, cell_h - 1, 1, 1)
            else:
                painter.drawEllipse(cx - 0.8, cy - 0.8, 1.6, 1.6)
    painter.end()
    return QIcon(pixmap)


def _swatch_icon(color, size=14):
    pixmap = _pixmap(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor('#80000000'), 1))
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
    painter.end()
    return QIcon(pixmap)


def _color_icon(color, text_color='#b8cee0', size=16):
    """An "A" over a bar in the colour (as word processors show it); the
    bar is outlined when the text keeps the default colour."""
    pixmap = _pixmap(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    font = QFont('Montserrat', 1)
    font.setPixelSize(int(size * 0.72))
    font.setWeight(QFont.Bold)
    painter.setFont(font)
    painter.setPen(QColor(text_color))
    painter.drawText(0, -1, size, size - 3, Qt.AlignCenter, 'A')
    if color:
        painter.setPen(QPen(QColor('#80000000'), 0.5))
        painter.setBrush(QColor(color))
    else:
        painter.setPen(QPen(QColor(text_color), 1))
        painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(1.5, size - 4, size - 3, 3, 1, 1)
    painter.end()
    return QIcon(pixmap)


def _clear_icon(color='#b8cee0', size=16):
    """An "A" struck through: formatting gone."""
    pixmap = _pixmap(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    font = QFont('Montserrat', 1)
    font.setPixelSize(int(size * 0.8))
    font.setWeight(QFont.Bold)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(0, 0, size, size, Qt.AlignCenter, 'A')
    painter.setPen(QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(2, size - 2, size - 2, 2)
    painter.end()
    return QIcon(pixmap)


def _subtitle_icon(color='#b8cee0', size=16):
    """Three dots: what the subtitle is besides its text."""
    pixmap = _pixmap(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    radius = size / 9
    for column in (0.25, 0.5, 0.75):
        painter.drawEllipse(QRectF(size * column - radius, size / 2 - radius, 2 * radius, 2 * radius))
    painter.end()
    return QIcon(pixmap)


class FormattingBar(QWidget):
    """The buttons that format the editor's text: italic, bold, underline,
    strikeout (also Ctrl+I, Ctrl+B, Ctrl+U), a colour, the position on the
    screen, and clearing it all. Each is one undo step. `changed` says the
    text changed without an edit in the editor (the position).

    `segment`, when given, says which subtitle the editor holds (a callable,
    the subtitle or None): the position menu then also sets its margins and
    rotation (modules.position), which the subtitle keeps beside its text,
    and a menu of its own says when the subtitle is shown — forced, for a
    viewer with subtitles off; hidden, for none — and hands a subtitle
    that brought a look of its own from its file back to the chosen
    style."""

    changed = Signal()

    def __init__(self, textedit, parent=None, segment=None):
        super().__init__(parent)
        self.setObjectName('subtitle_formatting_bar')
        self.setAttribute(Qt.WA_StyledBackground)
        self.textedit = textedit
        self.segment = segment or (lambda: None)
        self._place_step = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 4)
        layout.setSpacing(2)

        self.flag_buttons = {}
        for flag in FLAGS:
            button = QPushButton(_(f'formatting.{flag}_letter'))
            button.setObjectName(f'subtitle_formatting_{flag}')
            button.setProperty('class', 'subtitle_formatting_button')
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda _checked=False, f=flag: self._toggle(f))
            layout.addWidget(button)
            self.flag_buttons[flag] = button

        self.color_button = QPushButton()
        self.color_button.setObjectName('subtitle_formatting_color')
        self.color_button.setProperty('class', 'subtitle_formatting_button')
        self.color_button.setCursor(Qt.PointingHandCursor)
        self.color_button.setFocusPolicy(Qt.NoFocus)
        self.color_button.setIconSize(QSize(16, 16))
        self.color_menu = QMenu(self.color_button)
        self.color_menu.setObjectName('subtitle_formatting_menu')
        swatches = QWidget()
        grid = QGridLayout(swatches)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(4)
        for index, color in enumerate(SWATCHES):
            swatch = QPushButton()
            swatch.setProperty('class', 'subtitle_formatting_swatch')
            swatch.setIcon(_swatch_icon(color, 18))
            swatch.setIconSize(QSize(18, 18))
            swatch.setToolTip(color)
            swatch.setCursor(Qt.PointingHandCursor)
            swatch.clicked.connect(lambda _checked=False, c=color: self._pick_color(c))
            grid.addWidget(swatch, index // 4, index % 4)
        action = QWidgetAction(self.color_menu)
        action.setDefaultWidget(swatches)
        self.color_menu.addAction(action)
        self.color_menu.addSeparator()
        self.color_other_action = self.color_menu.addAction(_('formatting.color_other'), self._other_color)
        self.color_default_action = self.color_menu.addAction(_('formatting.color_default'), lambda: self._pick_color(''))
        self.color_button.setMenu(self.color_menu)
        layout.addWidget(self.color_button)

        self.position_button = QPushButton()
        self.position_button.setObjectName('subtitle_formatting_position')
        self.position_button.setProperty('class', 'subtitle_formatting_button')
        self.position_button.setCursor(Qt.PointingHandCursor)
        self.position_button.setFocusPolicy(Qt.NoFocus)
        self.position_button.setIconSize(QSize(16, 16))
        self.position_menu = QMenu(self.position_button)
        self.position_menu.setObjectName('subtitle_formatting_menu')
        cells = QWidget()
        grid = QGridLayout(cells)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(4)
        self.position_cells = {}
        for row, keys in enumerate(_KEYPAD_ROWS):
            for column, key in enumerate(keys):
                cell = QPushButton()
                cell.setProperty('class', 'subtitle_formatting_swatch')
                cell.setCheckable(True)
                cell.setIcon(_position_icon(key, '#dfe8f0', 22))
                cell.setIconSize(QSize(22, 22))
                cell.setCursor(Qt.PointingHandCursor)
                cell.clicked.connect(lambda _checked=False, k=key: self._pick_position(k))
                grid.addWidget(cell, row, column)
                self.position_cells[key] = cell
        action = QWidgetAction(self.position_menu)
        action.setDefaultWidget(cells)
        self.position_menu.addAction(action)
        # Finer than the nine places: margins from the edge it is aligned
        # to, in percent of the picture, and a turn (also by dragging the
        # subtitle on the video).
        fine = QWidget()
        form = QGridLayout(fine)
        form.setContentsMargins(8, 2, 8, 6)
        form.setHorizontalSpacing(6)
        form.setVerticalSpacing(4)
        self.place_labels, self.place_spins = {}, {}
        for row, (key, suffix) in enumerate((('horizontal', ' %'), ('vertical', ' %'), ('rotation', ' °'))):
            label = QLabel()
            spin = QDoubleSpinBox()
            spin.setObjectName(f'subtitle_formatting_place_{key}')
            spin.setDecimals(1)
            spin.setSingleStep(0.5 if suffix == ' %' else 1)
            spin.setSuffix(suffix)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(lambda _value=0, k=key: self._set_place(k))
            label.setBuddy(spin)
            form.addWidget(label, row, 0)
            form.addWidget(spin, row, 1)
            self.place_labels[key], self.place_spins[key] = label, spin
        self.place_spins['rotation'].setRange(-180, 180)
        self.place_action = QWidgetAction(self.position_menu)
        self.place_action.setDefaultWidget(fine)
        self.position_menu.addSeparator()
        self.position_menu.addAction(self.place_action)
        self.place_reset_action = self.position_menu.addAction(_('formatting.position_reset'), self._reset_place)
        self.position_menu.aboutToShow.connect(self._show_place)
        self.position_button.setMenu(self.position_menu)
        layout.addWidget(self.position_button)

        # What the subtitle is besides its text, kept beside it as its
        # position is: when it is shown, and the look its file gave it.
        self.subtitle_button = QPushButton()
        self.subtitle_button.setObjectName('subtitle_formatting_subtitle')
        self.subtitle_button.setProperty('class', 'subtitle_formatting_button')
        self.subtitle_button.setCursor(Qt.PointingHandCursor)
        self.subtitle_button.setFocusPolicy(Qt.NoFocus)
        self.subtitle_button.setIconSize(QSize(16, 16))
        self.subtitle_menu = QMenu(self.subtitle_button)
        self.subtitle_menu.setObjectName('subtitle_formatting_menu')
        self.forced_action = self.subtitle_menu.addAction(_('formatting.forced'))
        self.forced_action.setCheckable(True)
        self.forced_action.toggled.connect(lambda on: self._set_shown('forced', on))
        self.hidden_action = self.subtitle_menu.addAction(_('formatting.hidden'))
        self.hidden_action.setCheckable(True)
        self.hidden_action.toggled.connect(lambda on: self._set_shown('hidden', on))
        self.subtitle_menu.addSeparator()
        self.look_action = self.subtitle_menu.addAction(_('formatting.look_clear'), self._clear_look)
        self.subtitle_menu.aboutToShow.connect(self._show_subtitle)
        self.subtitle_button.setMenu(self.subtitle_menu)
        layout.addWidget(self.subtitle_button)

        self.clear_button = QPushButton()
        self.clear_button.setIcon(_clear_icon())
        self.clear_button.setIconSize(QSize(16, 16))
        self.clear_button.setObjectName('subtitle_formatting_clear')
        self.clear_button.setProperty('class', 'subtitle_formatting_button')
        self.clear_button.setCursor(Qt.PointingHandCursor)
        self.clear_button.setFocusPolicy(Qt.NoFocus)
        self.clear_button.clicked.connect(self._clear)
        layout.addWidget(self.clear_button)
        layout.addStretch()

        # Ctrl+I, Ctrl+B, Ctrl+U, while the editor has the keyboard: taken
        # before any shortcut of the window's could take them.
        textedit.installEventFilter(self)

        textedit.cursorPositionChanged.connect(self.refresh)
        textedit.selectionChanged.connect(self.refresh)
        self.retranslate()
        self.refresh()

    _KEYS = ((QKeySequence.Italic, 'italic'), (QKeySequence.Bold, 'bold'), (QKeySequence.Underline, 'underline'))

    def eventFilter(self, watched, event):
        if watched is self.textedit and event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress):
            for keys, flag in self._KEYS:
                if event.matches(keys):
                    if event.type() == QEvent.KeyPress:
                        self._toggle(flag)
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

    def retranslate(self):
        for flag, button in self.flag_buttons.items():
            button.setText(_(f'formatting.{flag}_letter'))
            button.setToolTip(_(f'formatting.{flag}'))
        self.color_button.setToolTip(_('formatting.color'))
        self.color_other_action.setText(_('formatting.color_other'))
        self.color_default_action.setText(_('formatting.color_default'))
        self.position_button.setToolTip(_('formatting.position'))
        for key, cell in self.position_cells.items():
            cell.setToolTip(_(f'formatting.position_{_POSITION_KEYS[key]}'))
        for key, label in self.place_labels.items():
            label.setText(_(f'formatting.position_{key}'))
            self.place_spins[key].setToolTip(_(f'formatting.position_{key}_tip'))
        self.place_reset_action.setText(_('formatting.position_reset'))
        self.subtitle_button.setToolTip(_('formatting.subtitle'))
        self.forced_action.setText(_('formatting.forced'))
        self.hidden_action.setText(_('formatting.hidden'))
        self.look_action.setText(_('formatting.look_clear'))
        self.clear_button.setToolTip(_('formatting.clear'))

    def refresh(self):
        """Show the selection's formatting (or the whole text's)."""
        for flag, button in self.flag_buttons.items():
            button.setChecked(is_on(self.textedit, flag))
        start, end = _range(self.textedit)
        colors = {style.color for run, style in runs(self.textedit, start, end) if run.strip()} if start < end else set()
        color = colors.pop() if len(colors) == 1 else ''
        self.color_button.setIcon(_color_icon(color))
        keypad = alignment(self.textedit)
        placed = keypad != 2 or position.of(self.segment()).any()
        self.position_button.setIcon(_position_icon(keypad, '#ffffff' if placed else '#b8cee0'))
        for key, cell in self.position_cells.items():
            cell.setChecked(key == keypad)
        # Lit, as the position is when the subtitle is placed, while the
        # subtitle is anything but ordinary: forced, hidden, or in a look
        # of its own.
        segment = self.segment()
        self.subtitle_button.setVisible(segment is not None)
        unusual = segment is not None and bool(segment.get('forced') or segment.get('hidden')
                                               or subtitld_look.of(segment))
        self.subtitle_button.setIcon(_subtitle_icon('#ffffff' if unusual else '#b8cee0'))

    def _step(self):
        """One undo step for what follows."""
        history.history_append()

    def _toggle(self, flag):
        self._step()
        toggle(self.textedit, flag)
        self.refresh()

    def _pick_color(self, color):
        self.color_menu.close()
        self._step()
        set_color(self.textedit, color)
        self.refresh()

    def _other_color(self):
        start, end = _range(self.textedit)
        colors = [style.color for run, style in runs(self.textedit, start, end) if style.color]
        chosen = QColorDialog.getColor(QColor(colors[0] if colors else '#ffffff'), self, _('formatting.color'))
        if chosen.isValid():
            self._pick_color(chosen.name())

    def _pick_position(self, keypad):
        self.position_menu.close()
        self._step()
        set_alignment(self.textedit, keypad)
        # One of the nine places, as it is: margins kept would be measured
        # from another edge now. A turn stays.
        segment = self.segment()
        if segment is not None:
            position.store(segment, position.of(segment)._replace(h='', v=''))
        self.refresh()
        self.changed.emit()

    # --- margins and rotation ----------------------------------------------
    def _safe(self):
        player = session.CONFIG.get('videoplayer', {})
        return player.get('safe_margin_title_x', 10) / 100, player.get('safe_margin_title_y', 10) / 100

    def _show_place(self):
        """The menu opens: its numbers are the subtitle's, each change from
        here on in one undo step."""
        segment = self.segment()
        self.place_action.setVisible(segment is not None)
        self.place_reset_action.setVisible(segment is not None)
        self._place_step = None
        if segment is None:
            return
        keypad, place = alignment(self.textedit), position.of(segment)
        across, down = position.margins(place, keypad, self._safe())
        values = {'horizontal': (across, keypad in (2, 5, 8)), 'vertical': (down, keypad in (4, 5, 6)),
                  'rotation': (place.rotate[2], None)}
        for key, (value, centred) in values.items():
            spin = self.place_spins[key]
            spin.blockSignals(True)
            if centred is not None:
                spin.setRange(-50, 50) if centred else spin.setRange(0, 100)
            spin.setValue(value)
            spin.blockSignals(False)
        self.place_reset_action.setEnabled(place.any())

    def _place_undo_step(self):
        if self._place_step is None or self._place_step is not history.top_snapshot():
            self._step()
            self._place_step = history.top_snapshot()

    def _set_place(self, key):
        segment = self.segment()
        if segment is None:
            return
        self._place_undo_step()
        place = position.of(segment)
        value = self.place_spins[key].value()
        if key == 'horizontal':
            place = place._replace(h=position.percent(value))
        elif key == 'vertical':
            place = place._replace(v=position.percent(value))
        else:
            place = place._replace(rotate=place.rotate[:2] + (value,))
        position.store(segment, place)
        self.place_reset_action.setEnabled(place.any())
        self.refresh()
        session.set_unsaved(True)

    def _reset_place(self):
        segment = self.segment()
        if segment is None:
            return
        self._place_undo_step()
        position.store(segment, position.of(segment)._replace(h='', v='', rotate=(0.0, 0.0, 0.0)))
        self.refresh()
        session.set_unsaved(True)

    # --- when it is shown, and its own look -----------------------------------
    def _show_subtitle(self):
        """The menu opens: it shows what the subtitle is now."""
        segment = self.segment()
        for key, action in (('forced', self.forced_action), ('hidden', self.hidden_action)):
            action.blockSignals(True)
            action.setChecked(bool(segment and segment.get(key)))
            action.blockSignals(False)
        self.look_action.setEnabled(bool(segment and subtitld_look.of(segment)))

    def _set_shown(self, key, on):
        """Forced (shown with subtitles off) or hidden (shown to nobody),
        on or off: kept on the subtitle only while it is on."""
        segment = self.segment()
        if segment is None or bool(segment.get(key)) == on:
            return
        self._step()
        if on:
            segment[key] = True
        else:
            segment.pop(key, None)
        self.refresh()
        session.set_unsaved(True)

    def _clear_look(self):
        """Draw the subtitle in the chosen style again, not in the look its
        file brought."""
        segment = self.segment()
        if segment is None or not subtitld_look.of(segment):
            return
        self._step()
        subtitld_look.store(segment, {})
        self.refresh()
        session.set_unsaved(True)

    def _clear(self):
        self._step()
        clear_formatting(self.textedit)
        if not self.textedit.textCursor().hasSelection():
            segment = self.segment()
            if segment is not None and position.of(segment).any():
                position.store(segment, position.of(segment)._replace(h='', v='', rotate=(0.0, 0.0, 0.0)))
                session.set_unsaved(True)
            # Formatting gone, all of it: the look a file gave it as well.
            if segment is not None and subtitld_look.of(segment):
                subtitld_look.store(segment, {})
                session.set_unsaved(True)
            if alignment(self.textedit) != 2:
                set_alignment(self.textedit, 2)
                self.changed.emit()
        self.refresh()
