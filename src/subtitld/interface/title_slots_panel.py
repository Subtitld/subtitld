"""A title's other parts, in the subtitles panel's editor under its text:
one field for each slot its theme has besides "main" (modules.title_themes),
those its settings show. Each slot is a bordered group, its label on top and
its field under it. A text slot is a line (a box, where the slot is
multiline), a list slot a box of one item a line, an image slot a small
preview (a placeholder while there is none) that is its button: hovered, it
offers to choose a picture, or, once the title has its own, to remove it. Where translations are shown, each
text and list slot has its translation under it, in the language the
translation goes to.

Labels come from the locale files when they have
`title_themes.<theme id>.<slot key>`, from the manifest otherwise.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from subtitld.interface.subtitle_style_panel import _text
from subtitld.interface import theme as look
from subtitld.interface.translation import _
from subtitld.modules import history, session, title_themes

THUMBNAIL = 40
BUTTON = 26          # a normal button's height


def slot_label(theme, slot):
    return _text(f'title_themes.{theme.id}.{slot["key"]}', slot.get('label') or slot['key'])


def translation_language():
    """The language translations are shown in, or None when they are not."""
    options = (session.CONFIG.get('translation') or {}).get('engine_options') or {}
    return options.get('target_language') if options.get('show_translations') else None


class TitleSlotsPanel(QWidget):
    """The selected title's slots (`title()` gives it); `changed` after an
    edit."""

    changed = Signal()

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName('title_slots_panel')
        self.title = title
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(14, 10, 14, 12)
        self.layout().setSpacing(6)
        self.rows = []
        self.editing = None          # the field whose edit has its undo step already
        self.shown_for = None

    def refresh(self, force=False):
        """The fields for the selected title's slots, with what it has in them."""
        title = self.title()
        slots = title_themes.shown_slots(title) if isinstance(title, dict) else []
        language = translation_language()
        shape = (id(title), title_themes.of(title).id if isinstance(title, dict) else None,
                 tuple(slot['key'] for slot in slots), language)
        # Shown whenever the title has slots: the editor hides the panel
        # for a cue of another kind, and the rows may still be this title's.
        self.setVisible(bool(slots))
        if shape == self.shown_for and not force:
            self._fill(title, slots, language)
            return
        self.shown_for = shape
        for row in self.rows:
            self.layout().removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        self.editing = None
        self.setVisible(bool(slots))
        if not slots:
            return
        theme = title_themes.of(title)
        for slot in slots:
            row = self._row(theme, slot, language)
            self.layout().addWidget(row)
            self.rows.append(row)
        self._fill(title, slots, language)

    # --- building ----------------------------------------------------------
    def _row(self, theme, slot, language):
        row = QFrame()
        row.setObjectName('title_slot_group')
        row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        row.slot = slot
        row.setLayout(QVBoxLayout())
        row.layout().setContentsMargins(6, 4, 6, 6)
        row.layout().setSpacing(2)
        label = QLabel(slot_label(theme, slot))
        label.setObjectName('title_slot_label')
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        row.layout().addWidget(label)
        if slot['type'] == 'image':
            row.layout().addLayout(self._image(row, slot, label.text()))
            return row
        row.field = self._text_field(slot, None, label.text())
        row.layout().addWidget(row.field)
        row.translated = None
        if language:
            note = QLabel(_('title_slots.translation').format(language=language))
            note.setObjectName('title_slot_label')
            note.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            row.layout().addWidget(note)
            row.translated = self._text_field(slot, language, f'{label.text()} ({language})')
            row.layout().addWidget(row.translated)
        return row

    def _text_field(self, slot, language, name):
        key = slot['key']
        if slot['type'] == 'list' or slot.get('multiline'):
            field = QPlainTextEdit()
            field.setObjectName('title_slot_box')
            field.setTabChangesFocus(True)
            field.setFixedHeight(64)
            if slot['type'] == 'list':
                field.setPlaceholderText(_('title_slots.one_a_line'))
            elif slot.get('placeholder'):
                field.setPlaceholderText(slot['placeholder'])
            field.textChanged.connect(lambda: self._edited(field, key, field.toPlainText(), language))
        else:
            field = QLineEdit()
            field.setObjectName('title_slot_line')
            if slot.get('placeholder'):
                field.setPlaceholderText(slot['placeholder'])
            if slot.get('max_length'):
                field.setMaxLength(int(slot['max_length']))
            field.textEdited.connect(lambda text: self._edited(field, key, text, language))
        field.setAccessibleName(name)
        return field

    def _image(self, row, slot, name):
        line = QHBoxLayout()
        line.setContentsMargins(0, 2, 0, 0)
        line.setSpacing(4)
        row.thumbnail = QLabel()
        row.thumbnail.setObjectName('title_slot_thumbnail')
        row.thumbnail.setFixedSize(THUMBNAIL, THUMBNAIL)
        row.thumbnail.setAlignment(Qt.AlignCenter)
        line.addWidget(row.thumbnail)
        # Over the preview, one at a time: their icons show on hover (or
        # focus), so the picture is seen whole the rest of the time.
        row.choose = self._preview_button(row.thumbnail, 'title_slot_choose', 'title_slots.choose_image', name)
        row.choose.clicked.connect(lambda: self._choose(row, slot['key']))
        row.remove = self._preview_button(row.thumbnail, 'title_slot_remove', 'title_slots.remove_image', name)
        row.remove.clicked.connect(lambda: self._set(slot['key'], None))
        row.problem = QLabel()
        row.problem.setObjectName('title_slot_problem')
        row.problem.setWordWrap(True)
        row.problem.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.problem.setVisible(False)
        line.addWidget(row.problem, 1)
        line.addStretch()
        return line

    def _preview_button(self, preview, object_name, text, name):
        button = QPushButton(preview)
        button.setObjectName(object_name)
        inset = (THUMBNAIL - BUTTON) // 2
        button.setGeometry(inset, inset, BUTTON, BUTTON)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(_(text))
        button.setAccessibleName(f'{name}: {_(text)}')
        return button

    # --- showing -----------------------------------------------------------
    def _fill(self, title, slots, language):
        for row in self.rows:
            key = row.slot['key']
            if row.slot['type'] == 'image':
                value = title_themes.slot_value(title, key)
                pixmap = QPixmap()
                if value and pixmap.loadFromData(_decoded(value)):
                    row.thumbnail.setPixmap(pixmap.scaled(THUMBNAIL, THUMBNAIL, Qt.KeepAspectRatio,
                                                          Qt.SmoothTransformation))
                else:
                    row.thumbnail.setPixmap(look.pixmap('title_slot_image_placeholder.svg', 16))
                own = (title.get('title_slots') or {}).get(key)
                row.choose.setVisible(not own)
                row.remove.setVisible(bool(own))
                continue
            for field, lang in ((row.field, None), (row.translated, language)):
                if field is None or field.hasFocus():
                    continue
                own = title.get('title_slots') or {} if not lang else \
                    (title.get('title_slot_translations') or {}).get(lang) or {}
                value = own.get(key)
                text = '\n'.join(value) if isinstance(value, list) else (value or '')
                field.blockSignals(True)
                if isinstance(field, QPlainTextEdit):
                    if field.toPlainText() != text:
                        field.setPlainText(text)
                elif field.text() != text:
                    field.setText(text)
                field.blockSignals(False)

    # --- changes -----------------------------------------------------------
    def _edited(self, field, key, value, language):
        title = self.title()
        if not isinstance(title, dict):
            return
        # One undo step for a field's run of typing, not one a key.
        if self.editing is not field:
            history.history_append()
            self.editing = field
        try:
            title_themes.set_slot(title, key, value, language)
        except (KeyError, ValueError):
            return
        session.set_unsaved(True)
        self.changed.emit()

    def _set(self, key, value):
        title = self.title()
        if not isinstance(title, dict):
            return
        history.history_append()
        self.editing = None
        title_themes.set_slot(title, key, value)
        session.set_unsaved(True)
        self.refresh()
        self.changed.emit()

    def _choose(self, row, key):
        kinds = ' '.join(f'*{suffix}' for suffix in sorted(title_themes.IMAGE_TYPES))
        path, _filter = QFileDialog.getOpenFileName(self.window(), _('title_slots.choose_image'), '',
                                                    f'{_("title_slots.pictures")} ({kinds})')
        if not path:
            return
        try:
            value = title_themes.image_data_url(path)
        except (OSError, ValueError) as error:
            row.problem.setText(_('title_slots.image_problem').format(reason=error))
            row.problem.setVisible(True)
            return
        row.problem.setVisible(False)
        self._set(key, value)


def _decoded(data_url):
    import base64
    try:
        return base64.b64decode(data_url.split(',', 1)[1])
    except (IndexError, ValueError):
        return b''
