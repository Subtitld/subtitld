"""A subtitle's text, edited as it looks.

The subtitle list's editor shows a subtitle's italic, bold, underline,
strikeout and colours, not tags, and turns what is typed and formatted
there back into the tags the text keeps (modules.markup). The override
codes a text starts with, such as its position ({\\an8}), are not text to
edit: the editor keeps them aside (`lead`) and puts them back.

The formatting commands act on the selection, or on the whole text when
nothing is selected, and toggle: italic on a text all italic takes it off.
"""

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap, QTextCharFormat,
                           QTextCursor, QTextFormat)
from PySide6.QtWidgets import QColorDialog, QGridLayout, QHBoxLayout, QMenu, QPushButton, QWidget, QWidgetAction

from subtitld.interface.translation import _
from subtitld.modules import history, markup

FLAGS = ('italic', 'bold', 'underline', 'strike')


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
    return fmt


def _style(fmt):
    color = ''
    if fmt.hasProperty(QTextFormat.ForegroundBrush) and fmt.foreground().style() != Qt.NoBrush:
        color = fmt.foreground().color().name()
    return markup.Style(fmt.fontItalic(), fmt.fontWeight() >= QFont.DemiBold, fmt.fontUnderline(),
                        fmt.fontStrikeOut(), color)


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
                                              before.color if before.color == after.color else ''))
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


class FormattingBar(QWidget):
    """The buttons that format the editor's text: italic, bold, underline,
    strikeout (also Ctrl+I, Ctrl+B, Ctrl+U), a colour, the position on the
    screen, and clearing it all. Each is one undo step. `changed` says the
    text changed without an edit in the editor (the position)."""

    changed = Signal()

    def __init__(self, textedit, parent=None):
        super().__init__(parent)
        self.setObjectName('subtitle_formatting_bar')
        self.setAttribute(Qt.WA_StyledBackground)
        self.textedit = textedit
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
        self.position_button.setMenu(self.position_menu)
        layout.addWidget(self.position_button)

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
        self.position_button.setIcon(_position_icon(keypad, '#ffffff' if keypad != 2 else '#b8cee0'))
        for key, cell in self.position_cells.items():
            cell.setChecked(key == keypad)

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
        self.refresh()
        self.changed.emit()

    def _clear(self):
        self._step()
        clear_formatting(self.textedit)
        if not self.textedit.textCursor().hasSelection() and alignment(self.textedit) != 2:
            set_alignment(self.textedit, 2)
            self.changed.emit()
        self.refresh()
