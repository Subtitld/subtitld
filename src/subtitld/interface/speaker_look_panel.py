"""A speaker's own look, in their card in the speakers panel: their values
of the fields the project's subtitle style marks `per_speaker` (a colour,
say), over the project's (modules.subtitle_styles). Empty, and hidden by the
card, when the style has no such field.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from subtitld.interface.subtitle_style_panel import field_control, field_label, option_label
from subtitld.interface.translation import _
from subtitld.modules import subtitle_styles


def per_speaker_fields(style=None):
    style = style or subtitle_styles.get()
    return [f for f in style.fields if f.get('per_speaker')]


class SpeakerLookPanel(QWidget):
    """`speaker`'s values of the project style's per-speaker fields;
    `changed` after each."""

    changed = Signal()

    def __init__(self, speaker, parent=None):
        super().__init__(parent)
        self.speaker = speaker
        self.setObjectName('speaker_look_panel')
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(10, 6, 10, 8)
        self.layout().setSpacing(8)
        self.fields_widget = None
        self.reset_button = QPushButton()
        self.reset_button.setObjectName('speaker_look_reset_button')
        self.reset_button.clicked.connect(self.reset)
        self.build()

    def build(self):
        if self.fields_widget is not None:
            self.layout().removeWidget(self.fields_widget)
            self.fields_widget.setParent(None)
            self.fields_widget.deleteLater()
        self.fields_widget = QWidget()
        self.fields_widget.setLayout(QHBoxLayout())
        self.fields_widget.layout().setContentsMargins(0, 0, 0, 0)
        self.fields_widget.layout().setSpacing(20)
        style = subtitle_styles.get()
        values = subtitle_styles.values(style.id, self.speaker)
        for field in per_speaker_fields(style):
            column, _control = field_control(field, values[field['key']], field_label(style, field),
                                             lambda option, field=field: option_label(style, field, option),
                                             self._set, self)
            self.fields_widget.layout().addLayout(column)
        self.fields_widget.layout().addStretch()
        self.layout().insertWidget(0, self.fields_widget)
        self.layout().addWidget(self.reset_button, 0, Qt.AlignLeft)
        self.reset_button.setText(_('left_panel_speakers.look_reset'))
        self.reset_button.setEnabled(bool(subtitle_styles.speaker_values(style.id, self.speaker)))

    def _set(self, key, value):
        try:
            value = subtitle_styles.set_value(key, value, speaker=self.speaker)
        except (KeyError, ValueError):
            return None
        self.reset_button.setEnabled(True)
        self.changed.emit()
        return value

    def reset(self):
        """The project's values again, for this speaker."""
        style = subtitle_styles.get()
        for key in subtitle_styles.speaker_values(style.id, self.speaker):
            subtitle_styles.set_value(key, None, speaker=self.speaker)
        self.build()
        self.changed.emit()
