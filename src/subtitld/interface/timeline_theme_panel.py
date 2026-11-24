"""The timeline's theme, picked from those there are (modules.timeline_themes),
and the few options the theme lets its user change, under it, with a
button to set them back to the theme's own.

Browse themes… opens the themes on subtitld.cc, to install (interface/timeline_theme_browser.py).

"Custom" is listed too while the look was set from no theme: a look its
user made, kept from before themes. Built-in themes' names and their
options' labels come from the locale files (`timeline_themes.<id>.name`,
`timeline_themes.<id>.options.<key>`), others' from their file.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from subtitld.interface.subtitle_style_panel import _text, field_control
from subtitld.interface.translation import _
from subtitld.modules import theme_store, timeline_themes

COLUMNS = 2


def theme_label(theme):
    """A theme's name: from the locale files for one of Subtitld's, its own otherwise; one installed from
    subtitld.cc says so, as its name may be any (even a built-in one's)."""
    if theme.builtin:
        return _text(f'timeline_themes.{theme.id}.name', theme.name)
    return f'{theme.name} · subtitld.cc' if theme.id.startswith(theme_store.PREFIX) else theme.name


def option_label(theme, option):
    """An option's label: from the locale files for one of Subtitld's themes, its own otherwise."""
    own = option.get('label') or option['key'].replace('_', ' ').capitalize()
    return _text(f'timeline_themes.{theme.id}.options.{option["key"]}', own) if theme.builtin else own


def choice_label(theme, option, choice):
    own = choice.get('label') or str(choice['value'])
    key = f'timeline_themes.{theme.id}.options.{option["key"]}.{choice["value"]}'
    return _text(key, own) if theme.builtin else own


class TimelineThemePanel(QWidget):
    """The theme picker and its theme's options; `changed` after either."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(2)
        self.label = QLabel()
        self.label.setProperty('class', 'widget_label')
        self.layout().addWidget(self.label, 0, Qt.AlignLeft)
        self.picker = QComboBox()
        self.picker.setObjectName('timeline_theme_picker')
        # As wide as its longest name: themes come and go (installed from subtitld.cc, removed).
        self.picker.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.picker.activated.connect(self._picked)
        self.browse_button = QPushButton()
        self.browse_button.setObjectName('timeline_theme_browse_button')
        self.browse_button.clicked.connect(self.browse)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(10)
        line.addWidget(self.picker)
        line.addWidget(self.browse_button)
        line.addStretch()
        self.layout().addLayout(line)
        self.options_widget = None
        self.controls = {}          # option key -> its control
        self.reset_button = None
        self.build()

    def build(self):
        """The themes there are, the look's own picked, and its options, in
        the interface's language."""
        self.label.setText(_('timeline_style.theme'))
        self.picker.setAccessibleName(_('timeline_style.theme'))
        self.browse_button.setText(_('theme_store.browse'))
        self.browse_button.setToolTip(_('theme_store.browse_tip'))
        current = timeline_themes.current()
        self.picker.clear()
        if current is None:
            self.picker.addItem(_('timeline_style.custom_theme'), None)
        for theme in timeline_themes.available().values():
            self.picker.addItem(theme_label(theme), theme.id)
        self.picker.setCurrentIndex(max(0, self.picker.findData(current.id if current else None)))
        self._build_options(current)

    def _build_options(self, theme):
        if self.options_widget is not None:
            self.layout().removeWidget(self.options_widget)
            self.options_widget.setParent(None)
            self.options_widget.deleteLater()
        self.options_widget = QWidget()
        self.options_widget.setObjectName('timeline_theme_options')
        column = QVBoxLayout(self.options_widget)
        column.setContentsMargins(0, 20, 0, 0)
        column.setSpacing(10)
        self.layout().addWidget(self.options_widget)
        self.controls.clear()
        self.reset_button = None
        if theme is None or not theme.options:
            self.options_widget.hide()
            return
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(10)
        values = timeline_themes.option_values(theme)
        for index, option in enumerate(theme.options):
            cell_layout, control = field_control(option, values[option['key']], option_label(theme, option),
                                                 lambda choice, option=option: choice_label(theme, option, choice),
                                                 self._set, self)
            cell = QWidget()
            cell.setLayout(cell_layout)
            grid.addWidget(cell, index // COLUMNS, index % COLUMNS, Qt.AlignLeft | Qt.AlignTop)
            self.controls[option['key']] = control
        grid.setColumnStretch(COLUMNS, 1)
        column.addLayout(grid)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        self.reset_button = QPushButton(_('timeline_style.reset_options'))
        self.reset_button.setObjectName('timeline_theme_reset_button')
        self.reset_button.clicked.connect(self.reset_options)
        buttons.addWidget(self.reset_button, 0, Qt.AlignLeft)
        buttons.addStretch()
        column.addLayout(buttons)

    def _set(self, key, value):
        try:
            value = timeline_themes.set_option(key, value)
        except (KeyError, ValueError):
            return None
        self.changed.emit()
        return value

    def _picked(self, index):
        theme_id = self.picker.itemData(index)
        if theme_id and (timeline_themes.current() is None or theme_id != timeline_themes.current().id):
            self.apply_theme(theme_id)

    def apply_theme(self, theme_id):
        timeline_themes.apply(theme_id)
        self.build()
        self.changed.emit()

    def browse(self):
        """Themes from subtitld.cc (interface/timeline_theme_browser.py)."""
        from subtitld.interface import timeline_theme_browser

        timeline_theme_browser.browse(self.window())

    def reset_options(self):
        timeline_themes.reset_options()
        self.build()
        self.changed.emit()
