import pathlib
import os
import datetime

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget, QSizePolicy, QLabel, QSpacerItem, QGraphicsOpacityEffect, QFileDialog, QDialog, QCheckBox, QRadioButton, QButtonGroup, QApplication
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QSize, QTimer, Signal

import subtitld
from subtitld.interface import utils
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import file_io
from subtitld.modules import utils as modules_utils


def load(self):
    self.titleBar_left_container = QWidget(self)
    self.titleBar_left_container.setObjectName('titleBar_left_container')
    self.titleBar_left_container.setLayout(QHBoxLayout())
    self.titleBar_left_container.layout().setContentsMargins(0, 0, 0, 1)
    self.titleBar_left_container.layout().setSpacing(0)
    self.titleBar_left_container.setSizePolicy(QSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.MinimumExpanding))
    self.titleBar_left_container.setAttribute(Qt.WA_StyledBackground, True)
    self.titleBar_left_container.opacity = QGraphicsOpacityEffect()
    self.titleBar_left_container.opacity.setOpacity(0)
    self.titleBar_left_container.setGraphicsEffect(self.titleBar_left_container.opacity)
    self.titleBar_left_container.animation = QPropertyAnimation(self.titleBar_left_container, b'pos')
    self.titleBar_left_container.animation.setEasingCurve(QEasingCurve.OutQuint)
    
    self.titleBar.layout().setAlignment(Qt.AlignTop)
    self.titleBar.setFixedHeight(37)
    self.titleBar.setObjectName('titleBar')
    self.titleBar.setAttribute(Qt.WA_StyledBackground, True)
        
    # Rearrange icons to top
    self.titleBar.layout().setAlignment(self.titleBar.minBtn, Qt.AlignTop)
    self.titleBar.layout().setAlignment(self.titleBar.maxBtn, Qt.AlignTop)
    self.titleBar.layout().setAlignment(self.titleBar.closeBtn, Qt.AlignTop)

    # Remove spacer
    for i in range(self.titleBar.layout().count()):
        item = self.titleBar.layout().itemAt(i)
        if isinstance(item, QSpacerItem):
            self.titleBar.layout().removeItem(item)

    # Add our bar
    self.titleBar.layout().insertWidget(0, self.titleBar_left_container)

    class titleBar_left_save_button(QPushButton):
        def update_state(widget):
            widget.setProperty(
                'class',
                'unsaved' if session.UNSAVED else 'saved'
            )
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            super().update()

    self.titleBar_left_save_button = titleBar_left_save_button(self)
    self.titleBar_left_save_button.setObjectName('titleBar_left_save_button')
    self.titleBar_left_save_button.setProperty('class', 'saved')
    self.titleBar_left_save_button.setIconSize(QSize(16, 16))
    self.titleBar_left_save_button.setFixedWidth(50)
    self.titleBar_left_save_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.titleBar_left_save_button.clicked.connect(lambda: toppanel_save_button_clicked(self))
    session._unsaved_change_callbacks.append(self.titleBar_left_save_button.update_state)
    self.titleBar_left_container.layout().addWidget(self.titleBar_left_save_button, alignment=Qt.AlignLeft | Qt.AlignVCenter)

    class titleBar_left_export_button(QPushButton):
        clicked = Signal()
        def __init__(widget, parent=None):
            super().__init__(parent)
            widget.setObjectName('titleBar_left_export_button')
            widget.setLayout(QHBoxLayout())
            widget.setAttribute(Qt.WA_LayoutOnEntireRect, False)
            widget.setAutoFillBackground(True)
            widget.layout().setContentsMargins(0, 0, 0, 0)
            widget.layout().setSpacing(0)
            # widget.setFixedHeight(30)
            widget.setSizePolicy(QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed))
            widget_icon = QLabel()
            widget_icon.setObjectName('titleBar_left_export_button_icon')
            widget_icon.setFixedSize(QSize(36, 36))
            widget.layout().addWidget(widget_icon)

        def sizeHint(widget):
            return widget.layout().sizeHint() if widget.layout() else super().sizeHint()

        def minimumSizeHint(widget):
            return widget.layout().minimumSize() if widget.layout() else super().minimumSizeHint()
        
        # def enterEvent(widget, event):
        #     widget.setProperty('class', 'hover')
        #     widget.style().unpolish(widget)
        #     widget.style().polish(widget)
        #     super().enterEvent(event)
        
        # def leaveEvent(widget, event):
        #     widget.setProperty('class', 'normal')
        #     widget.style().unpolish(widget)
        #     widget.style().polish(widget)
        #     super().leaveEvent(event)
        
        # def mousePressEvent(widget, event):
        #     if event.button() == Qt.LeftButton:
        #         widget.setProperty('class', 'pressed')
        #         widget.style().unpolish(widget)
        #         widget.style().polish(widget)
        #         widget.clicked.emit()

    self.titleBar_left_export_button = titleBar_left_export_button(self)
    self.titleBar_left_export_button.clicked.connect(lambda: toppanel_export_button_clicked(self))
    self.titleBar_left_container.layout().addWidget(self.titleBar_left_export_button, 0)

    self.titleBar_left_export_quick_button = QPushButton('SRT')
    self.titleBar_left_export_quick_button.setObjectName('titleBar_left_export_quick_button')
    self.titleBar_left_export_quick_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.titleBar_left_export_quick_button.clicked.connect(lambda: toppanel_export_quick_button_clicked(self))
    self.titleBar_left_export_quick_button.setFixedHeight(14)
    self.titleBar_left_export_quick_button.setVisible(False)
    self.titleBar_left_export_button.layout().addWidget(self.titleBar_left_export_quick_button, 1)

    def _refresh_quick_export_button():
        info = session.LAST_EXPORT
        if info and info.get('extension'):
            self.titleBar_left_export_quick_button.setText(info['extension'].upper())
            self.titleBar_left_export_quick_button.setToolTip(_('top_bar.quick_export').format(path=info['filepath']))
            self.titleBar_left_export_quick_button.setVisible(True)
        else:
            self.titleBar_left_export_quick_button.setVisible(False)
        self.titleBar_left_export_button.adjustSize()
        self.titleBar_left_export_button.updateGeometry()

    self._refresh_quick_export_button = _refresh_quick_export_button
    session._last_export_callbacks.append(_refresh_quick_export_button)
    _refresh_quick_export_button()

    self.titleBar_left_information_container = QWidget(self)
    self.titleBar_left_information_container.setObjectName('titleBar_left_information_container')
    self.titleBar_left_information_container.setSizePolicy(QSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.MinimumExpanding))
    self.titleBar_left_information_container.setLayout(QHBoxLayout())
    self.titleBar_left_information_container.layout().setContentsMargins(10, 0, 0, 0)
    self.titleBar_left_container.layout().addWidget(self.titleBar_left_information_container)

    self.titleBar_left_information_label = QLabel(self)
    self.titleBar_left_information_label.setObjectName('titleBar_left_information_label')
    self.titleBar_left_information_label.setAlignment(Qt.AlignCenter)
    self.titleBar_left_information_label.setSizePolicy(QSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.MinimumExpanding))
    self.titleBar_left_information_container.layout().addWidget(self.titleBar_left_information_label, 1)
    self.titleBar_left_information_label.setText('<b>filename.usf</b><br /><small>subtitle format</small>')

    self.tilteBar_subtitld_label = QLabel(self)
    self.tilteBar_subtitld_label.setObjectName('tilteBar_subtitld_label')
    self.tilteBar_subtitld_label.setAttribute(Qt.WA_StyledBackground, True)
    self.titleBar.layout().insertWidget(1, self.tilteBar_subtitld_label, alignment=Qt.AlignRight | Qt.AlignVCenter)
    self.tilteBar_subtitld_label.setText(f'<b>SUBTITLD</b>  v{subtitld.__version__}')


def update(self):
    filename = _('top_bar.untitled_file')
    filepath = pathlib.Path(session.VIDEO.get('filepath', '')).parent
    if session.SUBTITLE.get('filepath', ''):
        filename = pathlib.Path(session.SUBTITLE.get('filepath', '')).name
        filepath = pathlib.Path(session.SUBTITLE.get('filepath', '')).parent
            
    self.titleBar_left_information_label.setText(f'<small>{filepath}</small><br /><b>{filename}</b>')


def show(self):
    update(self)
    QTimer().singleShot(200, lambda: self.titleBar_left_container.opacity.setOpacity(1.0))
    utils.animate_element(self.titleBar_left_container.animation, duration=1000, effect='slide_from_left')


def translate(self):
    self.titleBar_left_save_button.setToolTip(_('top_bar.save'))
    self.titleBar_left_export_button.setToolTip(_('top_bar.export'))


def toppanel_save_button_clicked(self):
    """Save the project as USFX. Prompts for a path the first time (or when
    the current file isn't .usfx); subsequent clicks save in place."""
    usfx_filter = session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['USFX']['description'] + ' (*.usfx)'

    current_filepath = session.SUBTITLE.get('filepath') or ''
    needs_dialog = not current_filepath or not current_filepath.lower().endswith('.usfx')

    if needs_dialog:
        suggested_dir = os.path.dirname(current_filepath) if current_filepath else os.path.dirname(session.VIDEO.get('filepath', ''))
        base_source = current_filepath or session.VIDEO.get('filepath', '')
        suggested_filename = (os.path.splitext(os.path.basename(base_source))[0] or 'subtitle') + '.usfx'
        suggested = os.path.join(suggested_dir, suggested_filename)

        filedialog = QFileDialog.getSaveFileName(parent=self, caption='Save subtitle', dir=suggested, filter=usfx_filter)
        if not filedialog[0]:
            return
        filepath = filedialog[0]
        if not filepath.lower().endswith('.usfx'):
            filepath += '.usfx'
        session.SUBTITLE['filepath'] = filepath

    session.FORMAT['format'] = 'USFX'

    self.titleBar_left_save_button.setEnabled(False)
    session.set_unsaved(False)
    session.AUTOSAVE_BACKUP_DIRTY = False

    def _on_save_done(_path, success, _error):
        self.titleBar_left_save_button.setEnabled(True)
        if success:
            session.add_to_recent_files(session.SUBTITLE.get('filepath'), session.VIDEO.get('filepath', ''))
        else:
            session.set_unsaved(True)

    file_io.save_file_async(
        session.SUBTITLE['filepath'],
        'USFX',
        session.CONFIG['selected_language'],
        on_done=_on_save_done,
        parent=self,
    )


def toppanel_export_button_clicked(self):
    """Open the unified export dialog, then prompt for a save path filtered to
    the chosen format and run the appropriate exporter."""
    from subtitld.interface.export_dialog import ExportDialog
    from subtitld.modules import bounce as _bounce

    has_background = _bounce._background_audio_path() is not None
    has_vocals = _bounce._vocals_audio_path() is not None
    last = session.LAST_EXPORT or {}
    initial_category = last.get('category')

    dialog = ExportDialog(parent=self,
                          has_background=has_background,
                          has_vocals=has_vocals,
                          initial_category=initial_category)
    config = dialog.exec_and_get_values()
    if not config:
        return

    fmt = config.get('format')
    category = config.get('category')

    extensions = []
    description = fmt
    if category == 'subtitles' and fmt in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS:
        info = session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[fmt]
        extensions = info['extensions']
        description = info['description']
    elif category in ('audio', 'video') and fmt in session.LIST_OF_SUPPORTED_AUDIO_EXPORT_EXTENSIONS:
        info = session.LIST_OF_SUPPORTED_AUDIO_EXPORT_EXTENSIONS[fmt]
        extensions = info['extensions']
        description = info['description']
    elif category == 'documents' and fmt in session.LIST_OF_SUPPORTED_EXPORT_EXTENSIONS:
        info = session.LIST_OF_SUPPORTED_EXPORT_EXTENSIONS[fmt]
        extensions = info['extensions']
        description = info['description']

    if not extensions:
        return

    file_filter = description + ' (' + ' '.join(f'*.{e}' for e in extensions) + ')'

    suggested_dir = os.path.dirname(session.SUBTITLE.get('filepath', '') or session.VIDEO.get('filepath', ''))
    base = os.path.basename(session.SUBTITLE.get('filepath', '') or session.VIDEO.get('filepath', '')) or 'subtitle'
    suggested = os.path.join(suggested_dir, os.path.splitext(base)[0] + '.' + extensions[0])

    filedialog = QFileDialog.getSaveFileName(parent=self, caption=f'Export {fmt}', dir=suggested, filter=file_filter)
    if not filedialog[0]:
        return

    filepath = filedialog[0]
    if filepath.rsplit('.', 1)[-1].lower() not in extensions:
        filepath += f'.{extensions[0]}'
    selected_extension = filepath.rsplit('.', 1)[-1].lower()

    if category in ('audio', 'video'):
        audio_config = config.get('audio_config') or {'mode': 'mixdown', 'include_background': False}
        engine = getattr(self.preview_panel_player, '_audio_device', None)
        _bounce.bounce(filepath, fmt, audio_config['mode'],
                       audio_engine=engine,
                       include_background=audio_config.get('include_background', False))
        session.set_last_export({
            'filepath': filepath,
            'extension': selected_extension,
            'category': category,
            'format': fmt,
            'audio_format': fmt,
            'audio_config': audio_config,
        })
        return

    if category == 'subtitles' and 'options' in config:
        session.FORMAT['options'] = config['options']

    file_io.save_file(filepath, fmt, session.CONFIG['selected_language'])
    session.set_last_export({
        'filepath': filepath,
        'extension': selected_extension,
        'category': category,
        'format': fmt,
        'audio_format': None,
        'audio_config': None,
        'options': config.get('options'),
    })


def toppanel_export_quick_button_clicked(self):
    """Re-run the most recent export using its stored format/path. No dialogs."""
    info = session.LAST_EXPORT
    if not info or not info.get('filepath'):
        return

    filepath = info['filepath']
    category = info.get('category')

    if category in ('audio', 'video'):
        from subtitld.modules import bounce as _bounce
        audio_format = info.get('audio_format') or info.get('format')
        config = info.get('audio_config') or {'mode': 'mixdown', 'include_background': False}
        engine = getattr(self.preview_panel_player, '_audio_device', None)
        _bounce.bounce(filepath, audio_format, config['mode'],
                       audio_engine=engine,
                       include_background=config.get('include_background', False))
        return

    if info.get('options'):
        session.FORMAT['options'] = info['options']

    selected_format = info.get('format')
    if not selected_format:
        return
    file_io.save_file(filepath, selected_format, session.CONFIG['selected_language'])


class export_json_dialog(utils.SimpleDialog):
    def __init__(self, parent=None, title=''):
        super().__init__(parent, title)

        self.format_combobox = utils.LabeledComboBox()
        self.format_combobox.setLabel(_('export_json_dialog.format'))
        self.format_combobox.addItems(['Whisper', 'AD'])
        self.format_combobox.setCurrentText('Whisper')
        # self.format_combobox.activated.connect(lambda: self.format_combobox_activated())

        self.content.layout().addWidget(self.format_combobox)

    def exec_and_get_values(self):
        if self.exec() == QDialog.Accepted:
            return {
                'standard': self.format_combobox.currentText()
            }
        return None


class export_audio_dialog(utils.SimpleDialog):
    def __init__(self, parent=None, title='', is_mp4=False, has_background=False, has_vocals=False):
        super().__init__(parent, title)

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
            self.content.layout().addWidget(btn)

        if is_mp4:
            self.mode_clips.setEnabled(False)

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
        self.content.layout().addWidget(self.include_background_checkbox)

        # Hide the "include background" checkbox for the two pass-through modes
        # since mixing doesn't make sense when exporting a single track.
        def _refresh_checkbox_state():
            is_passthrough = self.mode_background_only.isChecked() or self.mode_vocals_only.isChecked()
            self.include_background_checkbox.setDisabled(is_passthrough or not has_background)
        for btn in (self.mode_mixdown, self.mode_stems, self.mode_clips,
                    self.mode_background_only, self.mode_vocals_only):
            btn.toggled.connect(_refresh_checkbox_state)

    def exec_and_get_values(self):
        if self.exec() == QDialog.Accepted:
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
                'mode': mode,
                'include_background': self.include_background_checkbox.isChecked() and self.include_background_checkbox.isEnabled(),
            }
        return None


class export_usf_dialog(utils.SimpleDialog):
    def __init__(self, parent=None, title=''):
        super().__init__(parent, title)

        self.embed_images_checkbox = QCheckBox(_('export_usf_dialog.embed_speaker_images'))
        self.content.layout().addWidget(self.embed_images_checkbox)

        self.embed_dubs_checkbox = QCheckBox(_('export_usf_dialog.embed_audio_clips'))
        self.content.layout().addWidget(self.embed_dubs_checkbox)

    def exec_and_get_values(self):
        if self.exec() == QDialog.Accepted:
            return {
                'embed_speaker_images': self.embed_images_checkbox.isChecked(),
                'embed_audio_clips': self.embed_dubs_checkbox.isChecked(),
            }
        return None