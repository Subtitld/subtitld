"""Keyboard shortcuts panel — the command list and the capture area.

Layout (top to bottom): a single SHORTCUTS tab, a COMMAND / SHORTKEYS
column header, the command list, then the capture area for whichever
command is selected — its shortcut drawn as physical-looking keycaps, with
CHANGE and CLEAR under it.

Capture: CHANGE listens for the next combination. The window's shortcut
QActions are disabled while listening (they are window-level actions, so a
single-key binding — Space, Escape, 1-9 — would otherwise be swallowed
before the panel sees the key press) and re-enabled on EVERY exit path.
A captured combination is written to the config, put on the live QAction
through shortcuts.apply() so it works immediately, and saved.
"""

from PySide6.QtWidgets import (QApplication, QVBoxLayout, QWidget, QLabel, QScrollArea,
                               QHBoxLayout, QPushButton, QSizePolicy, QTabWidget,
                               QGraphicsOpacityEffect)
from PySide6.QtCore import Qt, QEasingCurve, QEvent, QObject, QPropertyAnimation, Signal
from PySide6.QtGui import QKeySequence

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import shortcuts

# A command with no shortcut. Kept as a one-item list of '' rather than an
# empty list: that is the shape the config has always held, and [0] is read
# in a few places.
_NO_KEYS = ['']

# The list well's height. The design caps it so the capture area below stays
# on screen; beyond this the list scrolls.
_LIST_MAX_HEIGHT = 420

# Labels up to this length get a square big keycap; longer ones (ESCAPE,
# SPACE, CTRL) get the wide one.
_SHORT_LABEL = 3

# The blink of the "Press a key combination…" prompt: opacity 1 → .35 → 1.
_BLINK_MS = 1200
_BLINK_DIM = 0.35

# Big keycap metrics. Qt reads QSS min-width/min-height as the CONTENT box,
# so the outer size is set here instead and the stylesheet only paints.
_BIG_KEY_HEIGHT = 62
_BIG_KEY_WIDTH = 62
_BIG_KEY_WIDE = 112
# The row is taller than a key by the travel, so a pressed key sinks into
# its own slack instead of pushing the hint and the buttons down.
_BIG_KEY_TRAVEL = 4
_BIG_KEY_ROW_HEIGHT = _BIG_KEY_HEIGHT + _BIG_KEY_TRAVEL

# Key names, in the design's spelling. Qt's own QKeySequence text says
# "Esc"/"Del"/"PgUp" and orders modifiers Meta+Ctrl+Alt+Shift; the shipped
# defaults ("Escape", "Ctrl+H") follow the design's, and QKeySequence parses
# both when the shortcut is registered.
_KEY_NAMES = {
    Qt.Key_Space: 'Space',
    Qt.Key_Up: 'Up',
    Qt.Key_Down: 'Down',
    Qt.Key_Left: 'Left',
    Qt.Key_Right: 'Right',
    Qt.Key_Escape: 'Escape',
}
_MODIFIERS = ((Qt.ControlModifier, 'Ctrl'), (Qt.AltModifier, 'Alt'),
              (Qt.ShiftModifier, 'Shift'), (Qt.MetaModifier, 'Meta'))


def _combination(event):
    """A key event as a shortcut string, e.g. 'Ctrl+Shift+S'.

    Built by hand rather than from QKeySequence so the modifier order and
    the key names match the design (and the shipped defaults).
    """
    parts = [name for flag, name in _MODIFIERS if event.modifiers() & flag]
    key = _KEY_NAMES.get(event.key())
    if key is None:
        key = QKeySequence(event.key()).toString(QKeySequence.PortableText)
    if not key:
        return ''
    parts.append(key.upper() if len(key) == 1 else key)
    combination = '+'.join(parts)
    # QKeySequence takes anything; an unusable key gives an empty string back.
    sequence = QKeySequence(combination)
    if sequence.count() != 1 or not sequence.toString():
        return ''
    return combination


def _key_labels(keys):
    """A shortcut string as the keycap labels to draw.

    Splits on '+', but not on a trailing one, so '+' and 'Ctrl++' still show
    a plus key rather than an empty cap.
    """
    if not keys:
        return []
    text = str(keys)
    if text.endswith('+'):
        head, plus = text[:-1], ['+']
    else:
        head, plus = text, []
    return [part for part in head.split('+') if part] + plus


class _Keycap(QLabel):
    """One key in a row of the list — the small cap."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setProperty('class', 'keycap')
        self.setAlignment(Qt.AlignCenter)


class _BigKeycap(QWidget):
    """One key in the capture area — the big cap.

    Its own widget rather than a QPushButton: the legend sits top-left, like
    the print on a real modifier key, and Qt cannot align a button's label
    vertically. Pressing it is a visual effect only — the key sinks by
    _BIG_KEY_TRAVEL and its side (the thick bottom border) shrinks to match.
    """

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setProperty('class', 'big_keycap')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedSize(_BIG_KEY_WIDE if len(text) > _SHORT_LABEL else _BIG_KEY_WIDTH,
                          _BIG_KEY_HEIGHT)
        layout = QVBoxLayout(self)
        # The padding plus the borders: Qt does not take QSS borders off a
        # layout's contents rect.
        layout.setContentsMargins(19, 9, 19, 12)
        layout.setSpacing(0)
        self.label = QLabel(text.upper())
        self.label.setProperty('class', 'big_keycap_label')
        layout.addWidget(self.label, 0, Qt.AlignTop | Qt.AlignLeft)
        layout.addStretch(1)

    def _set_pressed(self, pressed):
        if bool(self.property('pressed')) == bool(pressed):
            return
        self.setProperty('pressed', bool(pressed))
        _repolish(self)
        holder = self.parentWidget()
        if holder is not None and holder.layout() is not None:
            # Sink into the row's slack; nothing below moves.
            holder.layout().setContentsMargins(0, _BIG_KEY_TRAVEL if pressed else 0, 0, 0)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._set_pressed(True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._set_pressed(False)
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self._set_pressed(False)
        super().leaveEvent(event)


class _CaptureFilter(QObject):
    """Catches the next key press while the panel is listening.

    It sits on the application, not on a widget, so the combination is
    caught wherever focus happens to be — the list, a button, the panel.
    """

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host

    def eventFilter(self, obj, event):
        if not getattr(self._host, 'keyboard_panel_listening', False):
            return False
        if event.type() == QEvent.ShortcutOverride:
            # Take the key back from whatever has it registered — this is
            # what makes Escape (and Space, and Ctrl+Z) capturable.
            event.accept()
            return True
        if event.type() == QEvent.KeyPress:
            return keyboard_panel_capture_keys(self._host, event)
        return False


class _CommandRow(QWidget):
    """A command and its shortcut: one row of the list."""

    clicked = Signal(str)

    def __init__(self, command_id, description, keys, parent=None):
        super().__init__(parent)
        self.command_id = command_id
        self.setProperty('class', 'shortcut_row')
        self.setAttribute(Qt.WA_StyledBackground, True)
        # Qt only tracks hover for a plain widget when asked to.
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(0)

        self.name_label = QLabel(description)
        self.name_label.setProperty('class', 'shortcut_row_command')
        self.name_label.setWordWrap(True)
        self.name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addWidget(self.name_label, 1)

        self.keys_widget = QWidget()
        self.keys_widget.setFixedWidth(200)
        keys_row = QHBoxLayout(self.keys_widget)
        keys_row.setContentsMargins(0, 0, 0, 0)
        keys_row.setSpacing(4)
        row.addWidget(self.keys_widget, 0)

        self.set_keys(keys)

    def set_keys(self, keys):
        """Redraw the shortcut cell: a keycap per key, or NOT SET."""
        layout = self.keys_widget.layout()
        _clear(layout)
        labels = _key_labels(keys)
        if not labels:
            empty = QLabel(_('keyboard_panel.not_set').upper())
            empty.setProperty('class', 'shortcut_row_not_set')
            layout.addWidget(empty, 0, Qt.AlignVCenter)
        for index, label in enumerate(labels):
            if index:
                plus = QLabel('+')
                plus.setProperty('class', 'keycap_plus')
                layout.addWidget(plus, 0, Qt.AlignVCenter)
            # Centred, not stretched: a cap is its own 21px, not the row's.
            layout.addWidget(_Keycap(label), 0, Qt.AlignVCenter)
        layout.addStretch()

    def set_selected(self, selected):
        if bool(self.property('selected')) == bool(selected):
            return
        self.setProperty('selected', bool(selected))
        # A dynamic property only reaches the stylesheet after a repolish,
        # and the labels inside are styled by the row's state too.
        for widget in (self, self.name_label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.command_id)
        super().mousePressEvent(event)


def _clear(layout):
    """Empty a layout of its widgets.

    setParent(None) as well as deleteLater(): a widget merely taken out of a
    layout stays a child, keeps painting where it was, and still answers
    findChildren until the deletion is processed.
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def _current_keys(command_id):
    """Every key binding stored for a command (the config's, or the default)."""
    stored = session.CONFIG['shortcuts'].get(command_id) if session.CONFIG['shortcuts'] else None
    if stored is None:
        stored = shortcuts.default_shortcuts_dict.get(command_id, _NO_KEYS)
    if isinstance(stored, str):
        stored = [stored]
    return list(stored) or list(_NO_KEYS)


def _first_key(command_id):
    """The binding the panel shows and edits — a command can hold more."""
    keys = _current_keys(command_id)
    return keys[0] if keys else ''


def _conflicting_command(keys, except_command):
    """The other command already bound to `keys`, if any."""
    if not keys:
        return None
    for command_id in shortcuts.shortcuts_dict:
        if command_id == except_command:
            continue
        if any(key and key == keys for key in _current_keys(command_id)):
            return command_id
    return None


def load(self):
    tab_name = 'keyboard'

    left_panel_keyboard_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )
    # The tab strip runs to the panel's edges, like the other tabbed panels;
    # everything under it carries the design's own insets instead.
    left_panel_keyboard_panel.layout().setContentsMargins(0, 0, 0, 0)

    self.keyboard_panel_tabwidget = QTabWidget()
    self.keyboard_panel_tabwidget.setObjectName('keyboard_panel_tabwidget')
    left_panel_keyboard_panel.layout().addWidget(self.keyboard_panel_tabwidget)

    body = QWidget()
    body.setObjectName('keyboard_panel_body')
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(0, 0, 0, 0)
    body_layout.setSpacing(0)
    # Tab text comes from translate(), like every other tabbed panel.
    self.keyboard_panel_tabwidget.addTab(body, '')

    # Column header — the list's two columns, named.
    header = QWidget()
    header.setObjectName('keyboard_panel_header')
    header_row = QHBoxLayout(header)
    # 24px = the list's 12px margin + a row's 12px padding, so the two
    # column names sit over the text they name.
    header_row.setContentsMargins(24, 10, 24, 6)
    header_row.setSpacing(0)
    self.keyboard_panel_command_header = QLabel()
    self.keyboard_panel_command_header.setProperty('class', 'shortcut_column_label')
    header_row.addWidget(self.keyboard_panel_command_header, 1)
    self.keyboard_panel_keys_header = QLabel()
    self.keyboard_panel_keys_header.setProperty('class', 'shortcut_column_label')
    self.keyboard_panel_keys_header.setFixedWidth(200)
    header_row.addWidget(self.keyboard_panel_keys_header, 0)
    body_layout.addWidget(header)

    # The list well.
    self.keyboard_panel_list_scroll = QScrollArea()
    self.keyboard_panel_list_scroll.setObjectName('keyboard_panel_list_scroll')
    self.keyboard_panel_list_scroll.setWidgetResizable(True)
    self.keyboard_panel_list_scroll.setFrameShape(QScrollArea.NoFrame)
    self.keyboard_panel_list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    self.keyboard_panel_list_scroll.setMaximumHeight(_LIST_MAX_HEIGHT)
    body_layout.addWidget(self.keyboard_panel_list_scroll, 1)

    self.keyboard_panel_list = QWidget()
    self.keyboard_panel_list.setObjectName('keyboard_panel_list')
    list_layout = QVBoxLayout(self.keyboard_panel_list)
    list_layout.setContentsMargins(0, 0, 0, 0)
    list_layout.setSpacing(0)
    list_layout.addStretch()
    self.keyboard_panel_list_scroll.setWidget(self.keyboard_panel_list)

    # Capture area — the selected command's shortcut, big.
    self.keyboard_panel_capture = QWidget()
    self.keyboard_panel_capture.setObjectName('keyboard_panel_capture')
    self.keyboard_panel_capture.setAttribute(Qt.WA_StyledBackground, True)
    capture_layout = QVBoxLayout(self.keyboard_panel_capture)
    capture_layout.setContentsMargins(16, 20, 16, 18)
    capture_layout.setSpacing(14)
    body_layout.addWidget(self.keyboard_panel_capture)

    self.keyboard_panel_capture_title = QLabel()
    self.keyboard_panel_capture_title.setObjectName('keyboard_panel_capture_title')
    self.keyboard_panel_capture_title.setProperty('class', 'shortcut_column_label')
    self.keyboard_panel_capture_title.setAlignment(Qt.AlignCenter)
    capture_layout.addWidget(self.keyboard_panel_capture_title)

    self.keyboard_panel_keycaps = QWidget()
    # Tall enough to hold a pressed key's travel, with the keys hanging from
    # the top, so pressing one moves nothing below it.
    self.keyboard_panel_keycaps.setFixedHeight(_BIG_KEY_ROW_HEIGHT)
    keycaps_row = QHBoxLayout(self.keyboard_panel_keycaps)
    keycaps_row.setContentsMargins(0, 0, 0, 0)
    keycaps_row.setSpacing(10)
    capture_layout.addWidget(self.keyboard_panel_keycaps, 0, Qt.AlignCenter)

    # Stands in for the keycaps while listening; blinks on a timer, since
    # QSS has no animations.
    self.keyboard_panel_listening_label = QLabel()
    self.keyboard_panel_listening_label.setObjectName('keyboard_panel_listening_label')
    self.keyboard_panel_listening_label.setAlignment(Qt.AlignCenter)
    self.keyboard_panel_listening_label.setVisible(False)
    capture_layout.addWidget(self.keyboard_panel_listening_label)

    self.keyboard_panel_hint = QLabel()
    self.keyboard_panel_hint.setObjectName('keyboard_panel_hint')
    self.keyboard_panel_hint.setAlignment(Qt.AlignCenter)
    self.keyboard_panel_hint.setWordWrap(True)
    capture_layout.addWidget(self.keyboard_panel_hint)

    # Actions.
    actions = QWidget()
    actions_row = QHBoxLayout(actions)
    actions_row.setContentsMargins(0, 16, 0, 20)
    actions_row.setSpacing(8)
    actions_row.addStretch()
    self.keyboard_panel_change_button = QPushButton()
    self.keyboard_panel_change_button.setProperty('class', 'shortcut_action primary')
    self.keyboard_panel_change_button.clicked.connect(lambda: keyboard_panel_change_clicked(self))
    actions_row.addWidget(self.keyboard_panel_change_button)
    self.keyboard_panel_clear_button = QPushButton()
    self.keyboard_panel_clear_button.setProperty('class', 'shortcut_action')
    self.keyboard_panel_clear_button.clicked.connect(lambda: keyboard_panel_clear_clicked(self))
    actions_row.addWidget(self.keyboard_panel_clear_button)
    actions_row.addStretch()
    body_layout.addWidget(actions)

    self.keyboard_panel_rows = {}
    self.keyboard_panel_selected = None
    self.keyboard_panel_listening = False
    self.keyboard_panel_event_filter = _CaptureFilter(self, left_panel_keyboard_panel)
    # QSS has no animations, so the prompt's blink is a real one.
    opacity = QGraphicsOpacityEffect(self.keyboard_panel_listening_label)
    self.keyboard_panel_listening_label.setGraphicsEffect(opacity)
    self._keyboard_panel_blink = QPropertyAnimation(opacity, b'opacity', left_panel_keyboard_panel)
    self._keyboard_panel_blink.setDuration(_BLINK_MS)
    self._keyboard_panel_blink.setKeyValueAt(0.0, 1.0)
    self._keyboard_panel_blink.setKeyValueAt(0.5, _BLINK_DIM)
    self._keyboard_panel_blink.setKeyValueAt(1.0, 1.0)
    self._keyboard_panel_blink.setEasingCurve(QEasingCurve.InOutSine)
    self._keyboard_panel_blink.setLoopCount(-1)

    update(self)


def show(self):
    update(self)


def hide(self):
    # Leaving the panel must never strand the app with its shortcuts off.
    keyboard_panel_stop_listening(self)


def update(self):
    keyboard_panel_rebuild_list(self)


def keyboard_panel_rebuild_list(self):
    """(Re)build the command rows from the registry + config."""
    if not hasattr(self, 'keyboard_panel_list'):
        return
    layout = self.keyboard_panel_list.layout()
    _clear(layout)
    self.keyboard_panel_rows = {}

    for command_id, description in shortcuts.shortcuts_dict.items():
        row = _CommandRow(command_id, _(description), _first_key(command_id))
        keys = _current_keys(command_id)
        if len([key for key in keys if key]) > 1:
            # A command can hold more than one binding; the row shows the
            # first, so the rest live in the tooltip rather than nowhere.
            row.setToolTip(', '.join(key for key in keys if key))
        row.clicked.connect(lambda command=command_id: keyboard_panel_row_clicked(self, command))
        layout.addWidget(row)
        self.keyboard_panel_rows[command_id] = row
    layout.addStretch()

    if self.keyboard_panel_selected not in self.keyboard_panel_rows:
        self.keyboard_panel_selected = next(iter(self.keyboard_panel_rows), None)
    keyboard_panel_update_capture(self)
    row = self.keyboard_panel_rows.get(self.keyboard_panel_selected)
    if row is not None:
        # The list is taller than its well: show the selected command.
        self.keyboard_panel_list_scroll.ensureWidgetVisible(row)


def keyboard_panel_row_clicked(self, command_id):
    keyboard_panel_stop_listening(self)
    self.keyboard_panel_selected = command_id
    keyboard_panel_update_capture(self)


def keyboard_panel_update_capture(self):
    """Draw the selected command in the capture area."""
    selected = self.keyboard_panel_selected
    for command_id, row in self.keyboard_panel_rows.items():
        row.set_selected(command_id == selected)

    has_selection = selected is not None
    self.keyboard_panel_capture.setVisible(has_selection)
    self.keyboard_panel_change_button.setEnabled(has_selection)
    self.keyboard_panel_clear_button.setEnabled(has_selection)
    if not has_selection:
        self.keyboard_panel_capture_title.setText(_('keyboard_panel.select_a_command').upper())
        return

    # Qt ignores QSS text-transform on a label, so the caps are ours.
    self.keyboard_panel_capture_title.setText(_(shortcuts.shortcuts_dict.get(selected, '')).upper())
    keys = _first_key(selected)
    layout = self.keyboard_panel_keycaps.layout()
    _clear(layout)
    for index, label in enumerate(_key_labels(keys)):
        if index:
            plus = QLabel('+')
            plus.setProperty('class', 'big_keycap_plus')
            layout.addWidget(plus, 0, Qt.AlignVCenter)
        # Each key hangs in its own holder: pressing it changes the holder's
        # top margin, so the key sinks without moving its neighbours.
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(0)
        holder_layout.addWidget(_BigKeycap(label), 0, Qt.AlignTop)
        holder.setFixedHeight(_BIG_KEY_ROW_HEIGHT)
        layout.addWidget(holder, 0, Qt.AlignTop)
    self.keyboard_panel_keycaps.setVisible(not self.keyboard_panel_listening)
    if not self.keyboard_panel_listening:
        self.keyboard_panel_hint.setText('' if keys else _('keyboard_panel.no_shortcut'))


def keyboard_panel_change_clicked(self):
    if self.keyboard_panel_listening:
        keyboard_panel_stop_listening(self)
    else:
        keyboard_panel_start_listening(self)


def keyboard_panel_start_listening(self):
    if self.keyboard_panel_selected is None:
        return
    self.keyboard_panel_listening = True
    # Window-level QActions swallow single keys (Space, Escape, 1-9) before
    # they reach us, so they go off for as long as we listen.
    shortcuts.disable_actions(self)
    self.keyboard_panel_keycaps.setVisible(False)
    self.keyboard_panel_listening_label.setVisible(True)
    self.keyboard_panel_listening_label.setText(_('keyboard_panel.press_combination'))
    self.keyboard_panel_hint.setText(_('keyboard_panel.listening_hint'))
    self.keyboard_panel_change_button.setText(_('keyboard_panel.cancel'))
    # Keep the pair from re-centring when the label length changes.
    self.keyboard_panel_change_button.setMinimumWidth(self.keyboard_panel_change_button.width())
    self.keyboard_panel_change_button.setProperty('class', 'shortcut_action secondary')
    _repolish(self.keyboard_panel_change_button)
    self._keyboard_panel_blink.start()
    # On the application, so the key is caught wherever focus sits.
    QApplication.instance().installEventFilter(self.keyboard_panel_event_filter)


def keyboard_panel_stop_listening(self):
    if not getattr(self, 'keyboard_panel_listening', False):
        return
    self.keyboard_panel_listening = False
    QApplication.instance().removeEventFilter(self.keyboard_panel_event_filter)
    self._keyboard_panel_blink.stop()
    effect = self.keyboard_panel_listening_label.graphicsEffect()
    if effect is not None:
        # A half-faded prompt must not be what the user sees next time.
        effect.setOpacity(1.0)
    self.keyboard_panel_listening_label.setVisible(False)
    self.keyboard_panel_keycaps.setVisible(True)
    self.keyboard_panel_change_button.setText(_('keyboard_panel.change'))
    self.keyboard_panel_change_button.setProperty('class', 'shortcut_action primary')
    _repolish(self.keyboard_panel_change_button)
    # Whatever ended the capture, the app gets its shortcuts back.
    shortcuts.enable_actions(self)
    keyboard_panel_update_capture(self)


def keyboard_panel_capture_keys(self, event):
    """A key press while listening → the command's new shortcut.

    Returns True when the event was consumed.
    """
    key = event.key()
    if key in (Qt.Key_unknown, Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
        return True  # a modifier on its own is not a shortcut
    combination = _combination(event)
    if not combination:
        return True

    command_id = self.keyboard_panel_selected
    clash = _conflicting_command(combination, command_id)
    if clash is not None:
        # Warn rather than steal it: the other command would silently lose
        # its shortcut, and two commands on one key is ambiguous anyway.
        self.keyboard_panel_hint.setText(_('keyboard_panel.conflict').format(
            keys=combination, command=_(shortcuts.shortcuts_dict.get(clash, clash))))
        return True

    keyboard_panel_set_shortcut(self, command_id, [combination] + [
        key for key in _current_keys(command_id)[1:] if key])
    keyboard_panel_stop_listening(self)
    return True


def keyboard_panel_clear_clicked(self):
    if self.keyboard_panel_selected is None:
        return
    keyboard_panel_stop_listening(self)
    keyboard_panel_set_shortcut(self, self.keyboard_panel_selected, list(_NO_KEYS))
    self.keyboard_panel_hint.setText(_('keyboard_panel.no_shortcut'))


def keyboard_panel_set_shortcut(self, command_id, keys):
    """Store a command's bindings, put them on the live action and save."""
    session.CONFIG['shortcuts'][command_id] = keys
    # So the new shortcut works now rather than after the next start.
    shortcuts.apply(self, command_id, keys)
    try:
        session.CONFIG.save()
    except Exception:
        # An unwritable config must not cost the user the edit they just
        # made; it still applies for this session.
        pass
    row = self.keyboard_panel_rows.get(command_id)
    if row is not None:
        row.set_keys(keys[0] if keys else '')
        real = [key for key in keys if key]
        row.setToolTip(', '.join(real) if len(real) > 1 else '')
    keyboard_panel_update_capture(self)


def _repolish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def translate(self):
    # A QTabBar and a QPushButton honour QSS text-transform; a QLabel does
    # not, so every label's caps are applied here.
    self.keyboard_panel_tabwidget.setTabText(0, _('keyboard_panel.tab'))
    self.keyboard_panel_command_header.setText(_('keyboard_panel.command').upper())
    self.keyboard_panel_keys_header.setText(_('keyboard_panel.shortkeys').upper())
    self.keyboard_panel_change_button.setText(
        _('keyboard_panel.cancel') if self.keyboard_panel_listening else _('keyboard_panel.change'))
    self.keyboard_panel_clear_button.setText(_('keyboard_panel.clear'))
    keyboard_panel_rebuild_list(self)
