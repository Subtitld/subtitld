"""The selected title's theme (modules.title_themes), in the subtitles
panel's editor where a subtitle has its speaker, and its settings, in a
dialog the button beside it opens.

The theme is picked from those there are, and becomes the one new titles
get. In the dialog each of the theme's fields gets the control its type
asks for (subtitle_style_panel.field_control); a bool other fields are
`visible_if` is a group that can be switched off, those fields inside it.
A change is the title's own: what it sets apart from the theme's
defaults; Reset takes them all back to the theme's.

Labels come from the locale files when they have
`title_themes.<theme id>.<key>` (`.name` for the theme's own name,
`.<key>.<value>` for an option), from the manifest otherwise.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QGroupBox, QPushButton, QScrollArea, QVBoxLayout, QWidget

from subtitld.interface import theme, utils
from subtitld.interface.editor_selector import EditorSelector
from subtitld.interface.subtitle_style_panel import _text, field_control
from subtitld.interface.translation import _
from subtitld.modules import history, title_themes

COLUMNS = 2


def theme_name(theme):
    return _text(f'title_themes.{theme.id}.name', theme.name)


def field_label(theme, field):
    return _text(f'title_themes.{theme.id}.{field["key"]}', field.get('label') or field['key'])


def option_label(theme, field, option):
    return _text(f'title_themes.{theme.id}.{field["key"]}.{option["value"]}', option.get('label') or str(option['value']))


class TitleSettingsDialog(utils.SimpleDialog):
    """A title's settings, as its theme offers them; `changed` after each.
    Reset takes them back to the theme's own."""

    changed = Signal()

    def __init__(self, title, parent=None):
        theme = title_themes.of(title)
        super().__init__(parent, title=_('theme_store.options_title').format(name=theme_name(theme)))
        self.setObjectName('title_settings_dialog')
        self.title = title
        self.fields_widget = None
        self.reject_button.setText(_('timeline_style.reset_options'))
        self.reject_button.clicked.disconnect()
        self.reject_button.clicked.connect(self.reset)
        self.accept_button.setText(_('theme_store.close'))
        # The fields scroll, the dialog no taller than most of the screen.
        self.scroll = QScrollArea()
        self.scroll.setObjectName('title_settings_scroll')
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # The dialog's own ground behind the fields, not the scroll area's.
        self.scroll.setStyleSheet('QScrollArea#title_settings_scroll { background: transparent; }')
        self.scroll.viewport().setAutoFillBackground(False)
        self.content.layout().addWidget(self.scroll)
        self.build()

    def build(self):
        if self.fields_widget is not None:
            self.fields_widget.setParent(None)
            self.fields_widget.deleteLater()
        self.fields_widget = QWidget()
        self.fields_widget.setProperty('class', 'transparent_panel')
        self.fields_widget.setAutoFillBackground(False)
        self.fields_widget.setLayout(QVBoxLayout())
        self.fields_widget.layout().setContentsMargins(0, 6, 0, 6)
        self.fields_widget.layout().setSpacing(10)
        theme = title_themes.of(self.title)
        values = title_themes.values(self.title)
        switches = {f.get('visible_if') for f in theme.fields if f.get('visible_if')}
        loose = [f for f in theme.fields if f['key'] not in switches and not f.get('visible_if')]
        if loose:
            self.fields_widget.layout().addWidget(self._group(theme, None, loose, values))
        for field in theme.fields:
            if field['key'] in switches:
                inside = [f for f in theme.fields if f.get('visible_if') == field['key']]
                self.fields_widget.layout().addWidget(self._group(theme, field, inside, values))
        self.scroll.setWidget(self.fields_widget)
        if self.isVisible():
            self.fit()

    def fit(self):
        """As wide as the fields, as tall as they are up to half the screen.
        Measured once they have the stylesheet's fonts and padding (shown):
        before, they measure narrower than they draw, and the right of them
        is cut off."""
        self.fields_widget.ensurePolished()
        self.fields_widget.layout().activate()
        hint = self.fields_widget.sizeHint()
        frame = self.scroll.frameWidth() * 2
        self.scroll.setMinimumWidth(hint.width() + self.scroll.verticalScrollBar().sizeHint().width() + frame)
        screen = self.screen().availableGeometry().height() if self.screen() else 900
        self.scroll.setMinimumHeight(min(hint.height() + frame, int(screen * 0.5)))
        self.layout().activate()
        self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        self.fit()

    def _group(self, theme, switch, fields, values):
        group = QGroupBox(field_label(theme, switch) if switch else '')
        # The stylesheet's padding is the group's only inset; with no title
        # it needs no room on top for one.
        group.setProperty('untitled', switch is None)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        group.setLayout(grid)
        if switch is not None:
            group.setCheckable(True)
            group.setChecked(values[switch['key']])
            group.toggled.connect(lambda on, key=switch['key']: self._set(key, on))
        for number, field in enumerate(fields):
            column, _control = field_control(field, values[field['key']], field_label(theme, field),
                                             lambda option, field=field: option_label(theme, field, option),
                                             self._set, self)
            grid.addLayout(column, number // COLUMNS, number % COLUMNS, Qt.AlignLeft | Qt.AlignTop)
        grid.setColumnStretch(COLUMNS, 1)
        return group

    def _set(self, key, value):
        history.history_append()
        try:
            value = title_themes.set_value(self.title, key, value)
        except (KeyError, ValueError):
            return None
        self.changed.emit()
        return value

    def reset(self):
        if self.title.get('title_settings'):
            history.history_append()
            self.title['title_settings'] = {}
            self.build()
            self.changed.emit()


class TitlePanel(EditorSelector):
    """A title's theme, picked from a list beside the speaker's place, and
    the button to its settings (`settings_button`, placed by whoever places
    the panel). `title` gives the title shown (the selected
    one); `changed` after a change to it."""

    changed = Signal()

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName('title_panel')
        self.title = title
        self.combobox.activated.connect(self._picked)
        # Its settings, behind an icon (the theme cards' options).
        # Not inside the selector: its place sets it where it goes (beside it,
        # a button as the formatting's are).
        self.settings_button = QPushButton()
        self.settings_button.setObjectName('title_panel_settings_button')
        self.settings_button.setProperty('class', 'subtitle_formatting_button')
        self.settings_button.setIcon(theme.icon('theme_card_options.svg'))
        self.settings_button.clicked.connect(self.open_settings)
        self.dialog = None

    def refresh(self, force=False):
        """Show the selected title's theme."""
        title = self.title()
        if not isinstance(title, dict):
            return
        current = title_themes.of(title)
        self.setLabel(_('title_panel.theme'))
        self.settings_button.setToolTip(_('title_panel.settings'))
        self.settings_button.setAccessibleName(_('title_panel.settings'))
        self.settings_button.setEnabled(bool(current.fields))
        self.combobox.blockSignals(True)
        self.combobox.clear()
        for available in title_themes.available().values():
            self.combobox.addItem(theme_name(available), available.id)
        self.combobox.setCurrentIndex(self.combobox.findData(current.id))
        self.combobox.blockSignals(False)
        self.fit()

    def open_settings(self):
        title = self.title()
        if not isinstance(title, dict):
            return
        self.dialog = TitleSettingsDialog(title, self.window())
        self.dialog.changed.connect(self.changed.emit)
        self.dialog.exec()

    def _picked(self, index):
        title = self.title()
        theme_id = self.combobox.itemData(index)
        if not isinstance(title, dict) or not theme_id or theme_id == title_themes.of(title).id:
            return
        history.history_append()
        title['title_theme'] = theme_id
        # The theme picked is the one the next title gets.
        title_themes.set_current(theme_id)
        self.refresh()
        self.changed.emit()
