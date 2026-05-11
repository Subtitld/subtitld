from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel, QScrollArea, QCheckBox, QComboBox, QHBoxLayout, QPushButton, QDoubleSpinBox, QFileDialog, QListWidget, QListWidgetItem, QTabWidget
from PySide6.QtCore import Qt, QMimeData, QSize
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon

import os
import json
import copy

from subtitld.interface import left_panel
from subtitld.interface import utils
from subtitld.interface.addons_dialog import AddonsPanel
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules.config import Config


class DragDropWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.parent_window = None
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and url.toLocalFile().endswith('.json'):
                    event.acceptProposedAction()
                    return
        event.ignore()
    
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and url.toLocalFile().endswith('.json'):
                    event.acceptProposedAction()
                    return
        event.ignore()
    
    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and url.toLocalFile().endswith('.json'):
                    handle_json_drop(self.parent_window, url.toLocalFile())
                    event.acceptProposedAction()
                    return
        event.ignore()


def handle_json_drop(window, filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        error_dialog = utils.SimpleDialog(window, title=_('global_panel.import_error'))
        error_dialog.content.layout().addWidget(QLabel(_('global_panel.import_error_message')))
        error_dialog.reject_button.hide()
        error_dialog.exec()
        return

    valid_keys = Config.get_valid_keys()
    filtered_config_data = {k: v for k, v in config_data.items() if k in valid_keys}

    if not filtered_config_data:
        error_dialog = utils.SimpleDialog(window, title=_('global_panel.import_error'))
        error_dialog.content.layout().addWidget(QLabel(_('global_panel.import_no_valid_keys')))
        error_dialog.reject_button.hide()
        error_dialog.exec()
        return

    available_sections = [key for key in filtered_config_data.keys() if key != 'recent_files']

    if not available_sections:
        error_dialog = utils.SimpleDialog(window, title=_('global_panel.no_sections'))
        error_dialog.content.layout().addWidget(QLabel(_('global_panel.no_sections_message')))
        error_dialog.reject_button.hide()
        error_dialog.exec()
        return

    select_dialog = utils.SimpleDialog(window, title=_('global_panel.select_sections'))

    list_widget = QListWidget()
    list_widget.setMinimumHeight(400)
    list_widget.setProperty('class', 'section_list')
    for section in sorted(available_sections):
        item = QListWidgetItem(section)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        list_widget.addItem(item)

    select_dialog.content.layout().addWidget(list_widget)
    select_dialog.reject_button.setText(_('cancel'))
    select_dialog.accept_button.setText(_('import'))

    def import_sections():
        sections_to_import = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.checkState() == Qt.Checked:
                sections_to_import.append(item.text())

        if not sections_to_import:
            error_dialog = utils.SimpleDialog(window, title=_('global_panel.no_sections'))
            error_dialog.content.layout().addWidget(QLabel(_('global_panel.select_at_least_one')))
            error_dialog.reject_button.hide()
            error_dialog.exec()
            return

        for section_name in sections_to_import:
            section_data = filtered_config_data.get(section_name, {})

            if isinstance(section_data, dict):
                if section_name not in session.CONFIG:
                    session.CONFIG[section_name] = {}
                session.CONFIG[section_name].update(section_data)
            else:
                session.CONFIG[section_name] = section_data

        session.CONFIG.save()
        update(window)
        select_dialog.accept()

    select_dialog.accept_button.clicked.connect(import_sections)
    select_dialog.exec()


def load(self):
    tab_name = 'global'
    
    left_panel_global_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )
    # The shared `left_panel` class applies a 10px margin around its content;
    # the global config panel hosts a tab widget that should sit flush with
    # the panel edges, so override the margin here.
    left_panel_global_panel.layout().setContentsMargins(0, 0, 0, 0)

    left_panel_global_panel_scroll = QScrollArea()
    left_panel_global_panel_scroll.setObjectName('left_panel_global_panel_scroll')
    left_panel_global_panel_scroll.setWidgetResizable(True)
    left_panel_global_panel_scroll.setFrameShape(QScrollArea.NoFrame)
    left_panel_global_panel.layout().addWidget(left_panel_global_panel_scroll)

    self.left_panel_global_panel_widget = DragDropWidget()
    self.left_panel_global_panel_widget.setProperty('class', 'transparent_panel')
    self.left_panel_global_panel_widget.setObjectName('left_panel_global_panel_widget')
    self.left_panel_global_panel_widget.setLayout(QVBoxLayout())
    self.left_panel_global_panel_widget.layout().setContentsMargins(0, 0, 0, 0)
    self.left_panel_global_panel_widget.parent_window = self
    left_panel_global_panel_scroll.setWidget(self.left_panel_global_panel_widget)

    self.left_panel_global_tabs = QTabWidget()
    self.left_panel_global_tabs.setObjectName('left_panel_global_tabs')
    self.left_panel_global_panel_widget.layout().addWidget(self.left_panel_global_tabs)

    # --- Subtitles tab ---
    self.left_panel_global_tab_subtitles = QWidget()
    self.left_panel_global_tab_subtitles.setProperty('class', 'transparent_panel')
    self.left_panel_global_tab_subtitles.setLayout(QVBoxLayout())
    self.left_panel_global_tab_subtitles.layout().setContentsMargins(10, 10, 10, 10)
    self.left_panel_global_tab_subtitles.layout().setSpacing(10)
    self.left_panel_global_tabs.addTab(self.left_panel_global_tab_subtitles, '')

    self.global_panel_general_minimum_duration_line = QVBoxLayout()
    self.global_panel_general_minimum_duration_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_general_minimum_duration_line.setSpacing(2)

    self.global_panel_general_minimum_duration_label = QLabel()
    self.global_panel_general_minimum_duration_label.setProperty('class', 'widget_label')
    self.global_panel_general_minimum_duration_line.addWidget(self.global_panel_general_minimum_duration_label, 0, Qt.AlignLeft)

    self.global_panel_general_minimum_duration_line_2 = QHBoxLayout()
    self.global_panel_general_minimum_duration_line_2.setContentsMargins(0, 0, 0, 0)
    self.global_panel_general_minimum_duration_line_2.setSpacing(5)

    self.global_panel_general_minimum_duration_spinbox = QDoubleSpinBox()
    self.global_panel_general_minimum_duration_spinbox.setMinimum(.1)
    self.global_panel_general_minimum_duration_spinbox.setMaximum(999.999)
    self.global_panel_general_minimum_duration_spinbox.valueChanged.connect(lambda: global_panel_general_minimum_duration_spinbox_changed(self))
    self.global_panel_general_minimum_duration_line_2.addWidget(self.global_panel_general_minimum_duration_spinbox, 0, Qt.AlignLeft)

    self.global_panel_general_minimum_duration_seconds_label = QLabel()
    self.global_panel_general_minimum_duration_seconds_label.setProperty('class', 'units_label')
    self.global_panel_general_minimum_duration_line_2.addWidget(self.global_panel_general_minimum_duration_seconds_label, 0, Qt.AlignLeft)

    self.global_panel_general_minimum_duration_line_2.addStretch()

    self.global_panel_general_minimum_duration_line.addLayout(self.global_panel_general_minimum_duration_line_2)

    self.left_panel_global_tab_subtitles.layout().addLayout(self.global_panel_general_minimum_duration_line)

    self.left_panel_global_subtitle_alignment = utils.LabeledComboBox()
    self.left_panel_global_subtitle_alignment.addItems(['Left', 'Center', 'Right'])
    self.left_panel_global_subtitle_alignment.activated.connect(lambda: left_panel_global_subtitle_alignment_activated(self))
    self.left_panel_global_tab_subtitles.layout().addWidget(self.left_panel_global_subtitle_alignment)

    self.left_panel_global_tab_subtitles.layout().addStretch()

    # --- General tab ---
    self.left_panel_global_tab_general = QWidget()
    self.left_panel_global_tab_general.setProperty('class', 'transparent_panel')
    self.left_panel_global_tab_general.setLayout(QVBoxLayout())
    self.left_panel_global_tab_general.layout().setContentsMargins(10, 10, 10, 10)
    self.left_panel_global_tab_general.layout().setSpacing(10)
    self.left_panel_global_tabs.addTab(self.left_panel_global_tab_general, '')

    self.global_panel_general_save_as_line = QVBoxLayout()
    self.global_panel_general_save_as_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_general_save_as_line.setSpacing(5)

    self.global_subtitlesvideo_save_as_label = QLabel(parent=self.left_panel_global_tab_general)
    self.global_subtitlesvideo_save_as_label.setProperty('class', 'widget_label')
    self.global_panel_general_save_as_line.addWidget(self.global_subtitlesvideo_save_as_label, 0, Qt.AlignLeft)

    list_of_subtitle_extensions = []
    for extformat in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS:
        list_of_subtitle_extensions.append(extformat + ' - ' + session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[extformat]['description'])
    self.global_subtitlesvideo_save_as_combobox = QComboBox(parent=self.left_panel_global_tab_general)
    self.global_subtitlesvideo_save_as_combobox.setProperty('class', 'button')
    self.global_subtitlesvideo_save_as_combobox.addItems(list_of_subtitle_extensions)
    self.global_subtitlesvideo_save_as_combobox.activated.connect(lambda: global_subtitlesvideo_save_as_combobox_activated(self))
    self.global_panel_general_save_as_line.addWidget(self.global_subtitlesvideo_save_as_combobox, 0, Qt.AlignLeft)

    self.global_panel_general_save_copy = QCheckBox()
    self.global_panel_general_save_copy.stateChanged.connect(lambda: global_panel_general_save_copy_changed(self))
    self.global_panel_general_save_as_line.addWidget(self.global_panel_general_save_copy, 0, Qt.AlignLeft)

    self.left_panel_global_tab_general.layout().addLayout(self.global_panel_general_save_as_line)

    # Audio-separator engine picker. Lists every provider registered for
    # the `audio.separate` task — always at least the built-in
    # `ffmpeg-separator` (fast mid/side trick), plus any installed
    # add-on (e.g. python-audio-separator with UVR/MDX/Demucs models).
    # Persists to `CONFIG['addons']['defaults']['audio.separate']` via
    # the addon registry so the manager picks the same one on next
    # startup.
    self.global_panel_audio_separator_combobox = utils.LabeledComboBox()
    self.global_panel_audio_separator_combobox.activated.connect(
        lambda: global_panel_audio_separator_combobox_activated(self)
    )
    self.left_panel_global_tab_general.layout().addWidget(self.global_panel_audio_separator_combobox)

    # Listen for addon registration changes so installing/uninstalling
    # an audio-separator add-on at runtime refreshes the combobox.
    from subtitld.modules import addons as _addons_module
    _addons_module.get_manager().providers_changed.connect(
        lambda: global_panel_audio_separator_combobox_populate(self)
    )

    self.left_panel_global_tab_general.layout().addStretch()

    # --- USFX tab ---
    self.left_panel_global_tab_usfx = QWidget()
    self.left_panel_global_tab_usfx.setProperty('class', 'transparent_panel')
    self.left_panel_global_tab_usfx.setLayout(QVBoxLayout())
    self.left_panel_global_tab_usfx.layout().setContentsMargins(10, 10, 10, 10)
    self.left_panel_global_tab_usfx.layout().setSpacing(6)
    self.left_panel_global_tabs.addTab(self.left_panel_global_tab_usfx, '')

    self.left_panel_global_tab_usfx_intro = QLabel()
    self.left_panel_global_tab_usfx_intro.setProperty('class', 'widget_label')
    self.left_panel_global_tab_usfx_intro.setWordWrap(True)
    self.left_panel_global_tab_usfx.layout().addWidget(self.left_panel_global_tab_usfx_intro)

    self.usfx_include_speaker_images_checkbox = QCheckBox()
    self.usfx_include_speaker_images_checkbox.stateChanged.connect(lambda: usfx_option_changed(self, 'include_speaker_images', self.usfx_include_speaker_images_checkbox.isChecked()))
    self.left_panel_global_tab_usfx.layout().addWidget(self.usfx_include_speaker_images_checkbox, 0, Qt.AlignLeft)

    self.usfx_include_waveform_cache_checkbox = QCheckBox()
    self.usfx_include_waveform_cache_checkbox.stateChanged.connect(lambda: usfx_option_changed(self, 'include_waveform_cache', self.usfx_include_waveform_cache_checkbox.isChecked()))
    self.left_panel_global_tab_usfx.layout().addWidget(self.usfx_include_waveform_cache_checkbox, 0, Qt.AlignLeft)

    self.usfx_include_original_audio_checkbox = QCheckBox()
    self.usfx_include_original_audio_checkbox.stateChanged.connect(lambda: usfx_option_changed(self, 'include_original_audio', self.usfx_include_original_audio_checkbox.isChecked()))
    self.left_panel_global_tab_usfx.layout().addWidget(self.usfx_include_original_audio_checkbox, 0, Qt.AlignLeft)

    self.usfx_include_processed_audio_checkbox = QCheckBox()
    self.usfx_include_processed_audio_checkbox.stateChanged.connect(lambda: usfx_option_changed(self, 'include_processed_audio', self.usfx_include_processed_audio_checkbox.isChecked()))
    self.left_panel_global_tab_usfx.layout().addWidget(self.usfx_include_processed_audio_checkbox, 0, Qt.AlignLeft)

    self.usfx_include_original_video_checkbox = QCheckBox()
    self.usfx_include_original_video_checkbox.stateChanged.connect(lambda: usfx_option_changed(self, 'include_original_video', self.usfx_include_original_video_checkbox.isChecked()))
    self.left_panel_global_tab_usfx.layout().addWidget(self.usfx_include_original_video_checkbox, 0, Qt.AlignLeft)

    self.left_panel_global_tab_usfx.layout().addStretch()

    # --- Add-ons tab ---
    # Hosts the Browse / Installed sub-tabs for the local AI add-on system.
    # Embedded here (instead of a top-bar dialog) so the entry point lives
    # next to other app-wide preferences.
    self.left_panel_global_tab_addons = QWidget()
    self.left_panel_global_tab_addons.setProperty('class', 'transparent_panel')
    self.left_panel_global_tab_addons.setLayout(QVBoxLayout())
    self.left_panel_global_tab_addons.layout().setContentsMargins(0, 0, 0, 0)
    self.left_panel_global_tab_addons.layout().setSpacing(0)
    self.left_panel_global_tabs.addTab(self.left_panel_global_tab_addons, '')

    self.left_panel_global_addons_panel = AddonsPanel()
    self.left_panel_global_tab_addons.layout().addWidget(self.left_panel_global_addons_panel)

    # Export settings: icon-only button docked at the right end of the tab
    # bar via QTabWidget's corner-widget slot.
    self.left_panel_global_panel_export_settings_button = QPushButton()
    self.left_panel_global_panel_export_settings_button.setObjectName('left_panel_global_panel_export_settings_button')
    self.left_panel_global_panel_export_settings_button.setIcon(QIcon(os.path.join(session.PATH_SUBTITLD_GRAPHICS, 'left_panel_global_export_settings_icon.svg')))
    self.left_panel_global_panel_export_settings_button.setIconSize(QSize(14, 14))
    self.left_panel_global_panel_export_settings_button.setFlat(True)
    self.left_panel_global_panel_export_settings_button.setFixedSize(QSize(28, 28))
    self.left_panel_global_panel_export_settings_button.clicked.connect(lambda: left_panel_global_panel_export_settings_button_clicked(self))

    # Wrap in a top-aligned container so the corner widget hugs the top
    # edge of the tab bar instead of being vertically centered in the row.
    _export_corner = QWidget()
    _export_corner_layout = QVBoxLayout(_export_corner)
    _export_corner_layout.setContentsMargins(0, 0, 1, 0)
    _export_corner_layout.setSpacing(0)
    _export_corner_layout.addWidget(self.left_panel_global_panel_export_settings_button, 0, Qt.AlignTop | Qt.AlignRight)
    self.left_panel_global_tabs.setCornerWidget(_export_corner, Qt.TopRightCorner)

    update(self)

    
def show(self):
    update(self)


USFX_OPTION_DEFAULTS = {
    'include_speaker_images': True,
    'include_waveform_cache': False,
    'include_original_audio': False,
    'include_processed_audio': False,
    'include_original_video': False,
}


def _get_usfx_options():
    return {**USFX_OPTION_DEFAULTS, **(session.CONFIG.get('default_values', {}).get('usfx_options') or {})}


def usfx_option_changed(self, key, value):
    options = _get_usfx_options()
    options[key] = bool(value)
    session.CONFIG.setdefault('default_values', {})['usfx_options'] = options


def global_panel_audio_separator_combobox_populate(self):
    """Rebuild the audio-separator engine combobox from the live list of
    providers registered for the `audio.separate` task. Stores the raw
    provider id in user-data so config writes go through stable ids."""
    if not hasattr(self, 'global_panel_audio_separator_combobox'):
        return
    from subtitld.modules import addons
    from subtitld.modules.addons import registry
    from subtitld.modules.addons.provider import TASK_AUDIO_SEPARATE

    combobox = self.global_panel_audio_separator_combobox
    inner = combobox.combobox
    previous_data = inner.currentData()
    inner.blockSignals(True)
    try:
        combobox.clear()
        manager = addons.get_manager()
        providers = manager.providers_for_task(TASK_AUDIO_SEPARATE)
        # Built-in (`ffmpeg-separator`) first, then add-ons in id order.
        providers.sort(key=lambda p: (not p.is_builtin, p.id))
        for provider in providers:
            label = getattr(provider, 'display_name', None) or provider.id
            inner.addItem(label, provider.id)

        # Restore selection: previous → CONFIG'd default → first entry.
        configured = registry.default_for_task(TASK_AUDIO_SEPARATE)
        target = previous_data or configured or ''
        idx = inner.findData(target) if target else -1
        if idx >= 0:
            inner.setCurrentIndex(idx)
        elif inner.count() > 0:
            inner.setCurrentIndex(0)
    finally:
        inner.blockSignals(False)


def global_panel_audio_separator_combobox_activated(self):
    """Persist the user's selection to the addon registry so the new
    pick is what `MusicAudioExtractorThread` resolves on the next
    media load."""
    from subtitld.modules.addons import registry
    from subtitld.modules.addons.provider import TASK_AUDIO_SEPARATE
    inner = self.global_panel_audio_separator_combobox.combobox
    provider_id = inner.currentData()
    if provider_id:
        registry.set_default_for_task(TASK_AUDIO_SEPARATE, provider_id)


def update(self):
    global_panel_audio_separator_combobox_populate(self)

    for item in [self.global_subtitlesvideo_save_as_combobox.itemText(i) for i in range(self.global_subtitlesvideo_save_as_combobox.count())]:
        if session.CONFIG['default_values'] and item.startswith(session.CONFIG['default_values'].get('subtitle_format', 'USFX')):
            self.global_subtitlesvideo_save_as_combobox.setCurrentText(item)
            break

    self.global_panel_general_save_copy.setChecked(session.CONFIG['default_values'].get('save_automatic_copy', False))

    self.global_panel_general_minimum_duration_spinbox.setValue(session.CONFIG['default_values'].get('minimum_subtitle_width', 1.0))

    self.left_panel_global_subtitle_alignment.setCurrentText(session.CONFIG['default_values'].get('subtitle_alignment', 'left').capitalize())

    options = _get_usfx_options()
    for key, checkbox in (
        ('include_speaker_images', self.usfx_include_speaker_images_checkbox),
        ('include_waveform_cache', self.usfx_include_waveform_cache_checkbox),
        ('include_original_audio', self.usfx_include_original_audio_checkbox),
        ('include_processed_audio', self.usfx_include_processed_audio_checkbox),
        ('include_original_video', self.usfx_include_original_video_checkbox),
    ):
        checkbox.blockSignals(True)
        checkbox.setChecked(bool(options.get(key, False)))
        checkbox.blockSignals(False)


def left_panel_global_subtitle_alignment_activated(self):
    session.CONFIG['default_values']['subtitle_alignment'] = self.left_panel_global_subtitle_alignment.currentText().lower()
    self.timeline_widget.subtitle_alignment = {'left' : Qt.AlignLeft, 'center' : Qt.AlignCenter, 'right' : Qt.AlignRight}[session.CONFIG['default_values'].get('subtitle_alignment', 'left')]


def global_subtitlesvideo_save_as_combobox_activated(self):
    session.CONFIG['default_values']['subtitle_format'] = self.global_subtitlesvideo_save_as_combobox.currentText().split(' ', 1)[0]


def global_panel_general_save_copy_changed(self):
    session.CONFIG['default_values']['save_automatic_copy'] = self.global_panel_general_save_copy.isChecked()


def global_panel_general_minimum_duration_spinbox_changed(self):
    session.CONFIG['default_values']['minimum_subtitle_width'] = self.global_panel_general_minimum_duration_spinbox.value()


def left_panel_global_panel_export_settings_button_clicked(self):
    filedialog = QFileDialog.getSaveFileName(parent=self, caption='Save settings', filter='JSON (*.json)')
    config_dict = copy.deepcopy(session.CONFIG)
    if 'recent_files' in config_dict:
        del config_dict['recent_files']
    if filedialog[0] and filedialog[1]:
        with open(filedialog[0], mode='w', encoding='utf-8') as json_file:
            json.dump(config_dict, json_file, indent=4)


def hide(self):
    pass


def translate(self):
    self.left_panel_global_tabs.setTabText(0, _('global_panel.tab_subtitles'))
    self.left_panel_global_tabs.setTabText(1, _('global_panel.tab_general'))
    self.left_panel_global_tabs.setTabText(2, _('global_panel.tab_usfx'))
    self.left_panel_global_tabs.setTabText(3, _('global_panel.tab_addons'))
    if hasattr(self, 'left_panel_global_addons_panel'):
        self.left_panel_global_addons_panel.retranslate()
    self.left_panel_global_tab_usfx_intro.setText(_('global_panel.usfx_intro'))
    self.usfx_include_speaker_images_checkbox.setText(_('global_panel.usfx_include_speaker_images'))
    self.usfx_include_waveform_cache_checkbox.setText(_('global_panel.usfx_include_waveform_cache'))
    self.usfx_include_original_audio_checkbox.setText(_('global_panel.usfx_include_original_audio'))
    self.usfx_include_processed_audio_checkbox.setText(_('global_panel.usfx_include_processed_audio'))
    self.usfx_include_original_video_checkbox.setText(_('global_panel.usfx_include_original_video'))
    self.global_subtitlesvideo_save_as_label.setText(_('global_panel.default_format_save'))
    self.global_panel_general_save_copy.setText(_('global_panel.save_copy'))
    self.global_panel_general_minimum_duration_label.setText(_('global_panel.minimum_duration'))
    self.global_panel_general_minimum_duration_seconds_label.setText(_('units.seconds'))
    self.left_panel_global_panel_export_settings_button.setToolTip(_('global_panel.export_settings'))
    self.left_panel_global_subtitle_alignment.setLabel(_('global_panel.subtitle_alignment'))
    if hasattr(self, 'global_panel_audio_separator_combobox'):
        self.global_panel_audio_separator_combobox.setLabel(_('global_panel.audio_separator'))



    
