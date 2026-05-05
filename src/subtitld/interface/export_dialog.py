"""Unified export dialog with four category tabs (Subtitles / Audio /
Video / Documents). Each tab exposes a format selector plus the
options that apply to that format. The dialog returns a single config
dict; the caller is responsible for the final file picker and dispatch."""

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
                               QPushButton, QButtonGroup, QRadioButton, QCheckBox,
                               QLabel, QSizePolicy, QDialog)
from PySide6.QtCore import Qt

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

        self.video_panel = _AudioPanel(has_background=has_background, has_vocals=has_vocals,
                                       formats=VIDEO_FORMATS)
        self._pages.addWidget(self.video_panel)

        self.documents_panel = _DocumentsPanel()
        self._pages.addWidget(self.documents_panel)

        for index, key in enumerate(self.CATEGORY_KEYS):
            self._category_buttons[key].clicked.connect(lambda _checked=False, i=index, k=key: self._select_category(i, k))

        start = initial_category if initial_category in self.CATEGORY_KEYS else 'subtitles'
        self._select_category(self.CATEGORY_KEYS.index(start), start)

        self.accept_button.setText(_('export_dialog.export_button'))

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
