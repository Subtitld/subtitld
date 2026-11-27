"""The mark categories (modules.cue_kinds): a row each, its colour and its
name to change; the user's own can be removed, and more added.

The built-in four are named in the interface's language until renamed. A
change reaches the open project's marks at once.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from subtitld.interface import theme
from subtitld.interface.translation import _
from subtitld.modules import cue_kinds


class MarkCategoriesPanel(QWidget):
    """The categories, editable. `changed` when one is."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('mark_categories_panel')
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(6)
        self.title = QLabel()
        self.title.setProperty('class', 'section_title')
        self.layout().addWidget(self.title)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setProperty('class', 'hint')
        self.layout().addWidget(self.hint)
        self.rows = QWidget()
        self.rows.setLayout(QVBoxLayout())
        self.rows.layout().setContentsMargins(0, 0, 0, 0)
        self.rows.layout().setSpacing(4)
        self.layout().addWidget(self.rows)
        self.add_button = QPushButton()
        self.add_button.setObjectName('mark_categories_add_button')
        self.add_button.clicked.connect(self.add)
        self.layout().addWidget(self.add_button, 0, Qt.AlignLeft)
        self.build()

    def build(self):
        """A row for each category, as they are now."""
        while self.rows.layout().count():
            row = self.rows.layout().takeAt(0).widget()
            if row is not None:
                # Out of the window at once, not only when Qt gets to deleting it.
                row.setParent(None)
                row.deleteLater()
        self.title.setText(_('marks_panel.categories'))
        self.hint.setText(_('marks_panel.categories_hint'))
        self.add_button.setText(_('marks_panel.add_category'))
        for category_id, values in cue_kinds.categories().items():
            self.rows.layout().addWidget(self._row(category_id, values))

    def _row(self, category_id, values):
        row = QWidget()
        row.setLayout(QHBoxLayout())
        row.layout().setContentsMargins(0, 0, 0, 0)
        row.layout().setSpacing(6)
        swatch = QPushButton()
        swatch.setFixedSize(26, 26)
        swatch.setAccessibleName(_('units.color'))
        # The category's own colour, as the timeline draws it in either look.
        color = QColor(values['color'])
        swatch.setStyleSheet(f'QPushButton {{ background-color: rgba({color.red()}, {color.green()}, {color.blue()}, '
                             f'{color.alpha()}); border-radius: 3px; }}')
        swatch.clicked.connect(lambda: self.recolour(category_id))
        row.layout().addWidget(swatch)
        name = QLineEdit(cue_kinds.display_name(category_id, _))
        name.setAccessibleName(_('marks_panel.category_name'))
        name.editingFinished.connect(lambda: self.rename(category_id, name.text()))
        row.layout().addWidget(name, 1)
        if not values['builtin']:
            remove = QPushButton()
            remove.setIcon(theme.icon('chat_delete.svg'))
            remove.setFixedSize(26, 26)
            remove.setToolTip(_('marks_panel.remove_category'))
            remove.setAccessibleName(_('marks_panel.remove_category'))
            remove.clicked.connect(lambda: self.remove(category_id))
            row.layout().addWidget(remove)
        return row

    def recolour(self, category_id):
        color = QColorDialog.getColor(QColor(cue_kinds.category(category_id)['color']), self)
        if color.isValid():
            cue_kinds.set_category(category_id, color=color.name(QColor.HexArgb))
            self.build()
            self.changed.emit()

    def rename(self, category_id, text):
        text = text.strip()
        if not text or text == cue_kinds.display_name(category_id, _):
            return
        builtin = cue_kinds.BUILTIN_CATEGORIES.get(category_id)
        # A built-in one given its name in this language back is not renamed.
        cue_kinds.set_category(category_id, name=text if not builtin or text != _(f'marks.category.{category_id}')
                               else builtin['name'])
        self.changed.emit()

    def add(self):
        taken = len(cue_kinds.categories()) - len(cue_kinds.BUILTIN_CATEGORIES)
        palette = ('#ffb07cd8', '#ff5cc8c8', '#ffd87cb0', '#ff9cc85c', '#ffc8a05c', '#ff7c9cd8')
        cue_kinds.add_category(_('marks_panel.new_category'), palette[taken % len(palette)])
        self.build()
        self.changed.emit()

    def remove(self, category_id):
        cue_kinds.remove_category(category_id)
        self.build()
        self.changed.emit()
