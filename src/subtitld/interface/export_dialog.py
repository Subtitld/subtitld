"""Unified export dialog with four category tabs (Subtitles / Audio /
Video / Documents). Each tab exposes a format selector plus the
options that apply to that format. The dialog returns a single config
dict; the caller is responsible for the final file picker and dispatch."""

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
                               QPushButton, QButtonGroup, QRadioButton, QCheckBox,
                               QLabel, QSizePolicy, QDialog, QTabWidget, QGridLayout,
                               QGroupBox, QSpinBox, QComboBox, QLineEdit, QProgressBar)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase

from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import session


SUBTITLE_FORMATS = ['SRT', 'VTT', 'ASS', 'DFXP', 'TTML', 'SAMI', 'SCC', 'SBV', 'SUB', 'XML', 'USF', 'JSON']
AUDIO_FORMATS = ['WAV', 'FLAC', 'MP3']
VIDEO_FORMATS = ['MP4']
DOCUMENT_FORMATS = ['TXT', 'KDENLIVE']


class _SubtitlesPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)

        self.format_combo = utils.LabeledComboBox()
        self.format_combo.setLabel(_('export_dialog.format'))
        self.format_combo.addItems(SUBTITLE_FORMATS)
        self.format_combo.activated.connect(self._refresh_options)
        self.layout().addWidget(self.format_combo)

        self.options_stack = QStackedWidget()
        self.layout().addWidget(self.options_stack)

        usf_panel = QWidget()
        usf_panel.setLayout(QVBoxLayout())
        usf_panel.layout().setContentsMargins(0, 8, 0, 0)
        self.usf_embed_images = QCheckBox(_('export_usf_dialog.embed_speaker_images'))
        usf_panel.layout().addWidget(self.usf_embed_images)
        self.usf_embed_dubs = QCheckBox(_('export_usf_dialog.embed_audio_clips'))
        usf_panel.layout().addWidget(self.usf_embed_dubs)
        usf_panel.layout().addStretch()
        self.options_stack.addWidget(usf_panel)

        json_panel = QWidget()
        json_panel.setLayout(QVBoxLayout())
        json_panel.layout().setContentsMargins(0, 8, 0, 0)
        self.json_standard = utils.LabeledComboBox()
        self.json_standard.setLabel(_('export_json_dialog.format'))
        self.json_standard.addItems(['Whisper', 'AD'])
        self.json_standard.setCurrentText('Whisper')
        json_panel.layout().addWidget(self.json_standard)
        json_panel.layout().addStretch()
        self.options_stack.addWidget(json_panel)

        self._empty_panel = QWidget()
        self._empty_panel.setLayout(QVBoxLayout())
        self._empty_panel.layout().addStretch()
        self.options_stack.addWidget(self._empty_panel)

        self.layout().addStretch()
        self._refresh_options()

    def _refresh_options(self):
        fmt = self.format_combo.currentText()
        if fmt == 'USF':
            self.options_stack.setCurrentIndex(0)
        elif fmt == 'JSON':
            self.options_stack.setCurrentIndex(1)
        else:
            self.options_stack.setCurrentIndex(2)

    def get_config(self):
        fmt = self.format_combo.currentText()
        config = {'category': 'subtitles', 'format': fmt}
        if fmt == 'USF':
            config['options'] = {
                'embed_speaker_images': self.usf_embed_images.isChecked(),
                'embed_audio_clips': self.usf_embed_dubs.isChecked(),
            }
        elif fmt == 'JSON':
            config['options'] = {'standard': self.json_standard.currentText()}
        return config


class _AudioPanel(QWidget):
    def __init__(self, parent=None, has_background=False, has_vocals=False, formats=None):
        super().__init__(parent)
        self._has_background = has_background
        self._has_vocals = has_vocals
        self._formats = formats or AUDIO_FORMATS
        self._allow_clips = 'MP4' not in self._formats

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)

        self.format_combo = utils.LabeledComboBox()
        self.format_combo.setLabel(_('export_dialog.format'))
        self.format_combo.addItems(self._formats)
        self.layout().addWidget(self.format_combo)

        mode_label = QLabel(_('export_dialog.output_mode'))
        mode_label.setProperty('class', 'widget_label')
        self.layout().addWidget(mode_label)

        self.mode_group = QButtonGroup(self)
        self.mode_mixdown = QRadioButton(_('export_audio_dialog.mode_mixdown'))
        self.mode_stems = QRadioButton(_('export_audio_dialog.mode_stems'))
        self.mode_clips = QRadioButton(_('export_audio_dialog.mode_clips'))
        self.mode_background_only = QRadioButton(_('export_audio_dialog.mode_background_only'))
        self.mode_vocals_only = QRadioButton(_('export_audio_dialog.mode_vocals_only'))
        self.mode_mixdown.setChecked(True)
        for btn in (self.mode_mixdown, self.mode_stems, self.mode_clips,
                    self.mode_background_only, self.mode_vocals_only):
            self.mode_group.addButton(btn)
            self.layout().addWidget(btn)

        self.mode_clips.setEnabled(self._allow_clips)
        self.mode_background_only.setEnabled(has_background)
        if not has_background:
            self.mode_background_only.setToolTip(_('export_audio_dialog.background_unavailable'))
        self.mode_vocals_only.setEnabled(has_vocals)
        if not has_vocals:
            self.mode_vocals_only.setToolTip(_('export_audio_dialog.vocals_unavailable'))

        self.include_background_checkbox = QCheckBox(_('export_audio_dialog.include_background'))
        self.include_background_checkbox.setEnabled(has_background)
        if not has_background:
            self.include_background_checkbox.setToolTip(_('export_audio_dialog.background_unavailable'))
        self.layout().addWidget(self.include_background_checkbox)

        for btn in (self.mode_mixdown, self.mode_stems, self.mode_clips,
                    self.mode_background_only, self.mode_vocals_only):
            btn.toggled.connect(self._refresh_checkbox_state)

        self.layout().addStretch()

    def _refresh_checkbox_state(self):
        is_passthrough = self.mode_background_only.isChecked() or self.mode_vocals_only.isChecked()
        self.include_background_checkbox.setDisabled(is_passthrough or not self._has_background)

    def get_config(self):
        mode = 'mixdown'
        if self.mode_stems.isChecked():
            mode = 'stems'
        elif self.mode_clips.isChecked():
            mode = 'clips'
        elif self.mode_background_only.isChecked():
            mode = 'background_only'
        elif self.mode_vocals_only.isChecked():
            mode = 'vocals_only'
        return {
            'format': self.format_combo.currentText(),
            'audio_config': {
                'mode': mode,
                'include_background': (
                    self.include_background_checkbox.isChecked()
                    and self.include_background_checkbox.isEnabled()
                ),
            },
        }


class _DocumentsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)

        self.format_combo = utils.LabeledComboBox()
        self.format_combo.setLabel(_('export_dialog.format'))
        self.format_combo.addItems(DOCUMENT_FORMATS)
        self.layout().addWidget(self.format_combo)

        note = QLabel(_('export_dialog.documents_placeholder'))
        note.setWordWrap(True)
        self.layout().addWidget(note)

        self.layout().addStretch()

    def get_config(self):
        return {
            'category': 'documents',
            'format': self.format_combo.currentText(),
        }


class _VideoPanel(QWidget):
    """Video export panel.

    Lifted verbatim (in structure) from ``left_panel_export.load`` —
    the FFmpeg sub-tab and the standalone "generate transparent video"
    button. Signals are wired to local placeholder slots; the user will
    re-implement actual behaviour later.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().setSpacing(20)

        self.video_tabwidget = QTabWidget()

        # FFmpeg sub-tab ---------------------------------------------------
        self.ffmpeg_panel = QWidget()
        self.ffmpeg_panel.setLayout(QGridLayout())
        self.ffmpeg_panel.layout().setContentsMargins(10, 10, 10, 10)
        self.ffmpeg_panel.layout().setSpacing(20)

        self.ffmpeg_left_panel = QWidget()
        self.ffmpeg_left_panel.setLayout(QVBoxLayout())
        self.ffmpeg_left_panel.layout().setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_left_panel.layout().setSpacing(20)

        # Font group ------------------------------------------------------
        self.ffmpeg_font_group = QGroupBox('Font')
        self.ffmpeg_font_group.setLayout(QHBoxLayout())
        self.ffmpeg_font_group.layout().setContentsMargins(10, 10, 10, 10)
        self.ffmpeg_font_group.layout().setSpacing(20)

        self.ffmpeg_fontsize_line = QVBoxLayout()
        self.ffmpeg_fontsize_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_fontsize_line.setSpacing(2)

        self.ffmpeg_fontsize_label = QLabel()
        self.ffmpeg_fontsize_label.setProperty('class', 'widget_label')
        self.ffmpeg_fontsize_line.addWidget(self.ffmpeg_fontsize_label, 0, Qt.AlignLeft)

        self.ffmpeg_fontsize_line_2 = QHBoxLayout()
        self.ffmpeg_fontsize_line_2.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_fontsize_line_2.setSpacing(5)

        self.ffmpeg_fontsize_spinbox = QSpinBox()
        self.ffmpeg_fontsize_spinbox.setMinimum(1)
        self.ffmpeg_fontsize_spinbox.setMaximum(999)
        self.ffmpeg_fontsize_spinbox.valueChanged.connect(self._fontsize_changed)
        self.ffmpeg_fontsize_line_2.addWidget(self.ffmpeg_fontsize_spinbox, 0, Qt.AlignLeft)

        self.ffmpeg_fontsize_seconds_label = QLabel()
        self.ffmpeg_fontsize_seconds_label.setProperty('class', 'units_label')
        self.ffmpeg_fontsize_line_2.addWidget(self.ffmpeg_fontsize_seconds_label, 0, Qt.AlignLeft)

        self.ffmpeg_fontsize_line.addLayout(self.ffmpeg_fontsize_line_2)
        self.ffmpeg_font_group.layout().addLayout(self.ffmpeg_fontsize_line)

        self.ffmpeg_fontfamily_line = QVBoxLayout()
        self.ffmpeg_fontfamily_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_fontfamily_line.setSpacing(2)

        self.ffmpeg_fontfamily_label = QLabel()
        self.ffmpeg_fontfamily_label.setProperty('class', 'widget_label')
        self.ffmpeg_fontfamily_line.addWidget(self.ffmpeg_fontfamily_label, 0, Qt.AlignLeft)

        self.ffmpeg_fontfamily_line_2 = QHBoxLayout()
        self.ffmpeg_fontfamily_line_2.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_fontfamily_line_2.setSpacing(5)

        fonts = QFontDatabase().families()
        self.ffmpeg_fontfamily_combobox = QComboBox()
        self.ffmpeg_fontfamily_combobox.addItems(fonts)
        self.ffmpeg_fontfamily_combobox.activated.connect(self._fontfamily_changed)
        self.ffmpeg_fontfamily_line_2.addWidget(self.ffmpeg_fontfamily_combobox, 0, Qt.AlignLeft)

        self.ffmpeg_fontfamily_line.addLayout(self.ffmpeg_fontfamily_line_2)
        self.ffmpeg_font_group.layout().addLayout(self.ffmpeg_fontfamily_line)

        self.ffmpeg_color_vbox = QVBoxLayout()
        self.ffmpeg_color_vbox.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_color_vbox.setSpacing(2)

        self.ffmpeg_color_label = QLabel()
        self.ffmpeg_color_label.setProperty('class', 'widget_label')
        self.ffmpeg_color_vbox.addWidget(self.ffmpeg_color_label, 0, Qt.AlignLeft)

        self.ffmpeg_color_button = QPushButton()
        self.ffmpeg_color_button.setProperty('class', 'color_pick_button')
        self.ffmpeg_color_button.setFixedWidth(80)
        self.ffmpeg_color_button.clicked.connect(self._color_button_clicked)
        self.ffmpeg_color_vbox.addWidget(self.ffmpeg_color_button, 0, Qt.AlignLeft)

        self.ffmpeg_font_group.layout().addLayout(self.ffmpeg_color_vbox)
        self.ffmpeg_font_group.layout().addStretch()
        self.ffmpeg_left_panel.layout().addWidget(self.ffmpeg_font_group)

        # Outline ---------------------------------------------------------
        self.ffmpeg_outline_group = QGroupBox()
        self.ffmpeg_outline_group.setCheckable(True)
        self.ffmpeg_outline_group.setLayout(QHBoxLayout())
        self.ffmpeg_outline_group.toggled.connect(self._outline_group_toggled)
        self.ffmpeg_outline_group.layout().setContentsMargins(10, 10, 10, 10)
        self.ffmpeg_outline_group.layout().setSpacing(20)

        self.ffmpeg_outline_vbox = QVBoxLayout()
        self.ffmpeg_outline_vbox.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_outline_vbox.setSpacing(2)

        self.ffmpeg_outline_label = QLabel()
        self.ffmpeg_outline_label.setProperty('class', 'widget_label')
        self.ffmpeg_outline_vbox.addWidget(self.ffmpeg_outline_label, 0, Qt.AlignLeft)

        self.ffmpeg_outline_line = QHBoxLayout()
        self.ffmpeg_outline_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_outline_line.setSpacing(5)

        self.ffmpeg_outline_value = QSpinBox()
        self.ffmpeg_outline_value.setMinimum(0)
        self.ffmpeg_outline_value.setMaximum(99999)
        self.ffmpeg_outline_value.valueChanged.connect(self._outline_value_changed)
        self.ffmpeg_outline_line.addWidget(self.ffmpeg_outline_value, 0, Qt.AlignLeft)

        self.ffmpeg_outline_value_pixels_label = QLabel()
        self.ffmpeg_outline_value_pixels_label.setProperty('class', 'units_label')
        self.ffmpeg_outline_line.addWidget(self.ffmpeg_outline_value_pixels_label, 0, Qt.AlignLeft)

        self.ffmpeg_outline_vbox.addLayout(self.ffmpeg_outline_line)
        self.ffmpeg_outline_group.layout().addLayout(self.ffmpeg_outline_vbox)
        self.ffmpeg_outline_group.layout().addStretch()
        self.ffmpeg_left_panel.layout().addWidget(self.ffmpeg_outline_group)

        # Shadow ----------------------------------------------------------
        self.ffmpeg_shadow_group = QGroupBox()
        self.ffmpeg_shadow_group.setCheckable(True)
        self.ffmpeg_shadow_group.setLayout(QHBoxLayout())
        self.ffmpeg_shadow_group.toggled.connect(self._shadow_group_toggled)
        self.ffmpeg_shadow_group.layout().setContentsMargins(10, 10, 10, 10)
        self.ffmpeg_shadow_group.layout().setSpacing(20)

        self.ffmpeg_shadow_vbox = QVBoxLayout()
        self.ffmpeg_shadow_vbox.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_shadow_vbox.setSpacing(2)

        self.ffmpeg_shadow_label = QLabel()
        self.ffmpeg_shadow_label.setProperty('class', 'widget_label')
        self.ffmpeg_shadow_vbox.addWidget(self.ffmpeg_shadow_label, 0, Qt.AlignLeft)

        self.ffmpeg_shadow_line = QHBoxLayout()
        self.ffmpeg_shadow_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_shadow_line.setSpacing(5)

        self.ffmpeg_shadow_distance = QSpinBox()
        self.ffmpeg_shadow_distance.setMinimum(-99999)
        self.ffmpeg_shadow_distance.setMaximum(99999)
        self.ffmpeg_shadow_distance.valueChanged.connect(self._shadow_distance_changed)
        self.ffmpeg_shadow_line.addWidget(self.ffmpeg_shadow_distance, 0, Qt.AlignLeft)

        self.ffmpeg_shadow_distance_pixels_label = QLabel()
        self.ffmpeg_shadow_distance_pixels_label.setProperty('class', 'units_label')
        self.ffmpeg_shadow_line.addWidget(self.ffmpeg_shadow_distance_pixels_label, 0, Qt.AlignLeft)

        self.ffmpeg_shadow_vbox.addLayout(self.ffmpeg_shadow_line)
        self.ffmpeg_shadow_group.layout().addLayout(self.ffmpeg_shadow_vbox)
        self.ffmpeg_shadow_group.layout().addStretch()
        self.ffmpeg_left_panel.layout().addWidget(self.ffmpeg_shadow_group)

        # Margins ---------------------------------------------------------
        self.ffmpeg_margins_group = QGroupBox()
        self.ffmpeg_margins_group.setLayout(QGridLayout())
        self.ffmpeg_margins_group.layout().setContentsMargins(10, 10, 10, 10)
        self.ffmpeg_margins_group.layout().setSpacing(20)

        # Left
        self.ffmpeg_margins_left_vbox = QVBoxLayout()
        self.ffmpeg_margins_left_vbox.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_margins_left_vbox.setSpacing(2)

        self.ffmpeg_margins_left_label = QLabel()
        self.ffmpeg_margins_left_label.setProperty('class', 'widget_label')
        self.ffmpeg_margins_left_vbox.addWidget(self.ffmpeg_margins_left_label, 0, Qt.AlignLeft)

        self.ffmpeg_margins_left_line = QHBoxLayout()
        self.ffmpeg_margins_left_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_margins_left_line.setSpacing(5)

        self.ffmpeg_margins_left_distance = QSpinBox()
        self.ffmpeg_margins_left_distance.setMinimum(-99999)
        self.ffmpeg_margins_left_distance.setMaximum(99999)
        self.ffmpeg_margins_left_distance.valueChanged.connect(self._margins_left_changed)
        self.ffmpeg_margins_left_line.addWidget(self.ffmpeg_margins_left_distance, 0, Qt.AlignLeft)

        self.ffmpeg_margins_left_distance_pixels_label = QLabel()
        self.ffmpeg_margins_left_distance_pixels_label.setProperty('class', 'units_label')
        self.ffmpeg_margins_left_line.addWidget(self.ffmpeg_margins_left_distance_pixels_label, 0, Qt.AlignLeft)
        self.ffmpeg_margins_left_line.addStretch()

        self.ffmpeg_margins_left_vbox.addLayout(self.ffmpeg_margins_left_line)
        self.ffmpeg_margins_group.layout().addLayout(self.ffmpeg_margins_left_vbox, 1, 1, 1, 1)

        # Bottom
        self.ffmpeg_margins_bottom_vbox = QVBoxLayout()
        self.ffmpeg_margins_bottom_vbox.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_margins_bottom_vbox.setSpacing(2)

        self.ffmpeg_margins_bottom_label = QLabel()
        self.ffmpeg_margins_bottom_label.setProperty('class', 'widget_label')
        self.ffmpeg_margins_bottom_vbox.addWidget(self.ffmpeg_margins_bottom_label, 0, Qt.AlignLeft)

        self.ffmpeg_margins_bottom_line = QHBoxLayout()
        self.ffmpeg_margins_bottom_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_margins_bottom_line.setSpacing(5)

        self.ffmpeg_margins_bottom_distance = QSpinBox()
        self.ffmpeg_margins_bottom_distance.setMinimum(-99999)
        self.ffmpeg_margins_bottom_distance.setMaximum(99999)
        self.ffmpeg_margins_bottom_distance.valueChanged.connect(self._margins_bottom_changed)
        self.ffmpeg_margins_bottom_line.addWidget(self.ffmpeg_margins_bottom_distance, 0, Qt.AlignLeft)

        self.ffmpeg_margins_bottom_distance_pixels_label = QLabel()
        self.ffmpeg_margins_bottom_distance_pixels_label.setProperty('class', 'units_label')
        self.ffmpeg_margins_bottom_line.addWidget(self.ffmpeg_margins_bottom_distance_pixels_label, 0, Qt.AlignLeft)
        self.ffmpeg_margins_bottom_line.addStretch()

        self.ffmpeg_margins_bottom_vbox.addLayout(self.ffmpeg_margins_bottom_line)
        self.ffmpeg_margins_group.layout().addLayout(self.ffmpeg_margins_bottom_vbox, 2, 2, 1, 1)

        # Right
        self.ffmpeg_margins_right_vbox = QVBoxLayout()
        self.ffmpeg_margins_right_vbox.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_margins_right_vbox.setSpacing(2)

        self.ffmpeg_margins_right_label = QLabel()
        self.ffmpeg_margins_right_label.setProperty('class', 'widget_label')
        self.ffmpeg_margins_right_vbox.addWidget(self.ffmpeg_margins_right_label, 0, Qt.AlignLeft)

        self.ffmpeg_margins_right_line = QHBoxLayout()
        self.ffmpeg_margins_right_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_margins_right_line.setSpacing(5)

        self.ffmpeg_margins_right_distance = QSpinBox()
        self.ffmpeg_margins_right_distance.setMinimum(-99999)
        self.ffmpeg_margins_right_distance.setMaximum(99999)
        self.ffmpeg_margins_right_distance.valueChanged.connect(self._margins_right_changed)
        self.ffmpeg_margins_right_line.addWidget(self.ffmpeg_margins_right_distance, 0, Qt.AlignLeft)

        self.ffmpeg_margins_right_distance_pixels_label = QLabel()
        self.ffmpeg_margins_right_distance_pixels_label.setProperty('class', 'units_label')
        self.ffmpeg_margins_right_line.addWidget(self.ffmpeg_margins_right_distance_pixels_label, 0, Qt.AlignLeft)
        self.ffmpeg_margins_right_line.addStretch()

        self.ffmpeg_margins_right_vbox.addLayout(self.ffmpeg_margins_right_line)
        self.ffmpeg_margins_group.layout().addLayout(self.ffmpeg_margins_right_vbox, 1, 3, 1, 1)

        self.ffmpeg_left_panel.layout().addWidget(self.ffmpeg_margins_group)

        # Final command + export button ----------------------------------
        self.ffmpeg_final_line = QHBoxLayout()
        self.ffmpeg_final_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_final_line.setSpacing(20)

        self.ffmpeg_command_line = QVBoxLayout()
        self.ffmpeg_command_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_command_line.setSpacing(5)

        self.ffmpeg_command_label = QLabel()
        self.ffmpeg_command_label.setProperty('class', 'widget_label')
        self.ffmpeg_command_line.addWidget(self.ffmpeg_command_label, 0, Qt.AlignLeft)

        self.ffmpeg_command_qlineedit = QLineEdit()
        self.ffmpeg_command_qlineedit.textEdited.connect(self._command_text_edited)
        self.ffmpeg_command_line.addWidget(self.ffmpeg_command_qlineedit)

        self.ffmpeg_final_line.addLayout(self.ffmpeg_command_line)

        self.ffmpeg_export_button = QPushButton()
        self.ffmpeg_export_button.setProperty('class', 'button_dark')
        self.ffmpeg_export_button.clicked.connect(self._export_button_clicked)
        self.ffmpeg_export_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
        self.ffmpeg_final_line.addWidget(self.ffmpeg_export_button)

        self.ffmpeg_left_panel.layout().addLayout(self.ffmpeg_final_line)
        self.ffmpeg_left_panel.layout().addStretch()

        self.ffmpeg_panel.layout().addWidget(self.ffmpeg_left_panel, 1, 1, 1, 2)

        # Preview ---------------------------------------------------------
        self.ffmpeg_preview_panel = QWidget()
        self.ffmpeg_preview_panel.setLayout(QVBoxLayout())
        self.ffmpeg_preview_panel.layout().setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_preview_panel.layout().setSpacing(20)

        self.ffmpeg_preview_label = QLabel()
        self.ffmpeg_preview_label.setProperty('class', 'widget_label')
        self.ffmpeg_preview_panel.layout().addWidget(self.ffmpeg_preview_label, 0)

        self.ffmpeg_preview_image = QLabel()
        self.ffmpeg_preview_image.setProperty('class', 'widget_label')
        self.ffmpeg_preview_image.setAlignment(Qt.AlignTop)
        self.ffmpeg_preview_panel.layout().addWidget(self.ffmpeg_preview_image, 1)

        self.ffmpeg_preview_panel.layout().addStretch()

        self.ffmpeg_panel.layout().addWidget(self.ffmpeg_preview_panel, 1, 3, 1, 1)

        self.video_tabwidget.addTab(self.ffmpeg_panel, 'FFMPEG')

        self.layout().addWidget(self.video_tabwidget, 0)

        # Standalone "generate transparent video" button -----------------
        self.generate_transparent_video_button = QPushButton()
        self.generate_transparent_video_button.setProperty('class', 'button_dark')
        self.generate_transparent_video_button.clicked.connect(self._generate_transparent_video_clicked)
        self.generate_transparent_video_button.setVisible(False)
        self.layout().addWidget(self.generate_transparent_video_button)

    # ------------------------------------------------------------------
    # Placeholder slots — user will rewire actual behaviour later.
    # ------------------------------------------------------------------
    def _fontsize_changed(self):
        pass

    def _fontfamily_changed(self):
        pass

    def _color_button_clicked(self):
        pass

    def _outline_group_toggled(self):
        pass

    def _outline_value_changed(self):
        pass

    def _shadow_group_toggled(self):
        pass

    def _shadow_distance_changed(self):
        pass

    def _margins_left_changed(self):
        pass

    def _margins_bottom_changed(self):
        pass

    def _margins_right_changed(self):
        pass

    def _command_text_edited(self):
        pass

    def _export_button_clicked(self):
        pass

    def _generate_transparent_video_clicked(self):
        pass

    def _update_preview(self):
        # Placeholder; user will reconnect to a worker thread later.
        pass

    def showEvent(self, event):
        self._update_preview()
        return super().showEvent(event)

    def get_config(self):
        return {'category': 'video'}


class ExportDialog(utils.SimpleDialog):
    """Universal export dialog. Returns a config dict via
    `exec_and_get_values()` or None when cancelled."""

    CATEGORY_KEYS = ['subtitles', 'audio', 'video', 'documents']

    def __init__(self, parent=None, has_background=False, has_vocals=False, initial_category=None):
        super().__init__(parent, _('export_dialog.title'))
        self.setMinimumSize(640, 380)

        self.content.layout().setContentsMargins(0, 0, 0, 0)

        body = QWidget()
        body.setLayout(QHBoxLayout())
        body.layout().setContentsMargins(0, 0, 0, 0)
        body.layout().setSpacing(0)
        self.content.layout().addWidget(body)

        sidebar = QWidget()
        sidebar.setObjectName('export_dialog_sidebar')
        sidebar.setLayout(QVBoxLayout())
        sidebar.layout().setContentsMargins(0, 10, 0, 10)
        sidebar.layout().setSpacing(0)
        sidebar.setFixedWidth(140)
        body.layout().addWidget(sidebar)

        self._category_buttons = {}
        self._category_group = QButtonGroup(self)
        self._category_group.setExclusive(True)
        for key in self.CATEGORY_KEYS:
            btn = QPushButton(_(f'export_dialog.category_{key}'))
            btn.setObjectName('export_dialog_category_button')
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            sidebar.layout().addWidget(btn)
            self._category_group.addButton(btn)
            self._category_buttons[key] = btn
        sidebar.layout().addStretch()

        right = QWidget()
        right.setObjectName('export_dialog_pages')
        right.setLayout(QVBoxLayout())
        right.layout().setContentsMargins(20, 16, 20, 16)
        body.layout().addWidget(right, 1)

        self._pages = QStackedWidget()
        right.layout().addWidget(self._pages)

        self.subtitles_panel = _SubtitlesPanel()
        self._pages.addWidget(self.subtitles_panel)

        self.audio_panel = _AudioPanel(has_background=has_background, has_vocals=has_vocals)
        self._pages.addWidget(self.audio_panel)

        self.video_panel = _VideoPanel()
        self._pages.addWidget(self.video_panel)

        self.documents_panel = _DocumentsPanel()
        self._pages.addWidget(self.documents_panel)

        for index, key in enumerate(self.CATEGORY_KEYS):
            self._category_buttons[key].clicked.connect(lambda _checked=False, i=index, k=key: self._select_category(i, k))

        start = initial_category if initial_category in self.CATEGORY_KEYS else 'subtitles'
        self._select_category(self.CATEGORY_KEYS.index(start), start)

        self.accept_button.setText(_('export_dialog.export_button'))

        # Remember the form-side widgets so `enter_processing_state` can
        # hide them in one shot, and re-show them if the caller ever
        # wants to revert (currently unused but cheap to support).
        self._form_body = body
        # `self.bottom_line`, not `accept_button.parent()` — the default
        # button now lives inside the footer's right-hand tab, so the parent
        # is the tab rather than the footer we mean to hide.
        self._form_buttons_parent = self.bottom_line
        self._processing_widget = None
        self._processing_label = None

    # ------------------------------------------------------------------
    # Processing-state UI
    #
    # The host (top_bar.toppanel_export_button_clicked) kicks the actual
    # export off on a background thread. While that thread runs we swap
    # the form for a small indeterminate-progress widget, leave the
    # dialog visible and application-modal, and wait for the host to
    # call `finish_processing()` from the thread's done-callback.
    #
    # This keeps the user oriented (the same dialog they clicked Export
    # in is now showing "Exporting...") instead of a frozen window. The
    # placeholder spinner is a vanilla `QProgressBar` in indeterminate
    # mode; when the user ships their custom animation later, drop it
    # in by replacing `_build_processing_widget` — the public API
    # (`enter_processing_state` / `finish_processing`) doesn't change.
    # ------------------------------------------------------------------
    def _build_processing_widget(self) -> QWidget:
        widget = QWidget()
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(40, 40, 40, 40)
        widget.layout().setSpacing(16)

        widget.layout().addStretch()

        label = QLabel('')
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setProperty('class', 'widget_label')
        widget.layout().addWidget(label, 0, Qt.AlignCenter)
        self._processing_label = label

        # min=0, max=0 puts the bar into Qt's indeterminate "barber pole"
        # mode — animated by Qt itself on the GUI thread. The export work
        # runs on a worker QThread so the GUI event loop stays free to
        # tick the animation.
        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setTextVisible(False)
        progress.setFixedHeight(6)
        progress.setMinimumWidth(280)
        widget.layout().addWidget(progress, 0, Qt.AlignCenter)

        widget.layout().addStretch()
        return widget

    def enter_processing_state(self, message: str) -> None:
        """Switch the dialog into a busy / processing display: hide the
        category form + the OK/Cancel buttons, show a centered status
        label + indeterminate progress bar. Idempotent — calling twice
        just updates the message. Application-modal so the user can't
        start a second export from the main window while this one runs.
        """
        if self._processing_widget is None:
            self._processing_widget = self._build_processing_widget()
            # Append into the same content layout as the body so the
            # processing widget inherits the dialog's content margins.
            self.content.layout().addWidget(self._processing_widget)

        self._processing_label.setText(message or '')
        self._processing_widget.setVisible(True)

        self._form_body.setVisible(False)
        if self._form_buttons_parent is not None:
            self._form_buttons_parent.setVisible(False)

        # The dialog was hidden by `accept()` when the user confirmed the
        # form. Re-show it (non-blocking) and mark it application-modal
        # so the rest of the app stops responding to clicks while the
        # export runs. We don't call `exec()` here because the caller
        # has already spawned the background thread and needs to keep
        # executing past this point.
        self.setWindowModality(Qt.ApplicationModal)
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

    def finish_processing(self) -> None:
        """Tear down the processing state and close the dialog. Safe to
        call from any thread — Qt routes the close through the event
        loop via `QDialog.accept`."""
        # Use done() with a sentinel return code so any future caller
        # that wants to know whether the dialog closed via the
        # processing path can distinguish it from form Accept. For now
        # the return code isn't read.
        self.accept()

    def _select_category(self, index, key):
        self._pages.setCurrentIndex(index)
        self._category_buttons[key].setChecked(True)
        self.title_line.label.setText(f'<b>{_("export_dialog.title").upper()}</b>  <span style="opacity:0.6">{_(f"export_dialog.category_{key}")}</span>')

    def _current_category(self):
        for index, key in enumerate(self.CATEGORY_KEYS):
            if self._pages.currentIndex() == index:
                return key
        return 'subtitles'

    def exec_and_get_values(self):
        if self.exec() != QDialog.Accepted:
            return None
        category = self._current_category()
        if category == 'subtitles':
            return self.subtitles_panel.get_config()
        if category == 'audio':
            cfg = self.audio_panel.get_config()
            cfg['category'] = 'audio'
            return cfg
        if category == 'video':
            cfg = self.video_panel.get_config()
            cfg['category'] = 'video'
            return cfg
        return self.documents_panel.get_config()
