"""The timeline's theme, picked from those there are (modules.timeline_themes).

"Custom" is listed too while the look was set from no theme: a look its
user made, kept from before themes. Built-in themes' names come from the
locale files (`timeline_themes.<id>.name`), others' from their file.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

from subtitld.interface.subtitle_style_panel import _text
from subtitld.interface.translation import _
from subtitld.modules import timeline_themes


def theme_label(theme):
    """A theme's name: from the locale files for one of Subtitld's, its own otherwise."""
    return _text(f'timeline_themes.{theme.id}.name', theme.name) if theme.builtin else theme.name


class TimelineThemePanel(QWidget):
    """The theme picker; `changed` after a theme is picked."""

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
        self.picker.activated.connect(self._picked)
        self.layout().addWidget(self.picker, 0, Qt.AlignLeft)
        self.build()

    def build(self):
        """The themes there are, the look's own picked, in the interface's language."""
        self.label.setText(_('timeline_style.theme'))
        self.picker.setAccessibleName(_('timeline_style.theme'))
        current = timeline_themes.current()
        self.picker.clear()
        if current is None:
            self.picker.addItem(_('timeline_style.custom_theme'), None)
        for theme in timeline_themes.available().values():
            self.picker.addItem(theme_label(theme), theme.id)
        self.picker.setCurrentIndex(max(0, self.picker.findData(current.id if current else None)))

    def _picked(self, index):
        theme_id = self.picker.itemData(index)
        if theme_id and (timeline_themes.current() is None or theme_id != timeline_themes.current().id):
            self.apply_theme(theme_id)

    def apply_theme(self, theme_id):
        timeline_themes.apply(theme_id)
        self.build()
        self.changed.emit()
