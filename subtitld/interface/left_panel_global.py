from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel, QScrollArea, QCheckBox, QComboBox, QHBoxLayout, QPushButton, QDoubleSpinBox, QFileDialog, QListWidget, QListWidgetItem
from PySide6.QtCore import Qt, QMimeData
from PySide6.QtGui import QDragEnterEvent, QDropEvent

import json
import copy

from subtitld.interface import left_panel
from subtitld.interface import utils
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

    self.global_panel_general_save_as_line = QVBoxLayout()
    self.global_panel_general_save_as_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_general_save_as_line.setSpacing(5)

    self.global_subtitlesvideo_save_as_label = QLabel(parent=self.left_panel_global_panel_widget)
    self.global_subtitlesvideo_save_as_label.setProperty('class', 'widget_label')
    self.global_panel_general_save_as_line.addWidget(self.global_subtitlesvideo_save_as_label, 0, Qt.AlignLeft)

    list_of_subtitle_extensions = []
    for extformat in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS:
        list_of_subtitle_extensions.append(extformat + ' - ' + session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[extformat]['description'])
    self.global_subtitlesvideo_save_as_combobox = QComboBox(parent=self.left_panel_global_panel_widget)
    self.global_subtitlesvideo_save_as_combobox.setProperty('class', 'button')
    self.global_subtitlesvideo_save_as_combobox.addItems(list_of_subtitle_extensions)
    # self.global_subtitlesvideo_save_as_combobox.view().window().setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
    # self.global_subtitlesvideo_save_as_combobox.view().window().setAttribute(Qt.WA_TranslucentBackground)
    self.global_subtitlesvideo_save_as_combobox.activated.connect(lambda: global_subtitlesvideo_save_as_combobox_activated(self))
    self.global_panel_general_save_as_line.addWidget(self.global_subtitlesvideo_save_as_combobox, 0, Qt.AlignLeft)

    self.global_panel_general_save_copy = QCheckBox()
    self.global_panel_general_save_copy.stateChanged.connect(lambda: global_panel_general_save_copy_changed(self))
    self.global_panel_general_save_as_line.addWidget(self.global_panel_general_save_copy, 0, Qt.AlignLeft)

    self.left_panel_global_panel_widget.layout().addLayout(self.global_panel_general_save_as_line)

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

    self.left_panel_global_panel_widget.layout().addLayout(self.global_panel_general_minimum_duration_line)

    self.left_panel_global_subtitle_alignment = utils.LabeledComboBox()
    self.left_panel_global_subtitle_alignment.addItems(['Left', 'Center', 'Right'])
    self.left_panel_global_subtitle_alignment.activated.connect(lambda: left_panel_global_subtitle_alignment_activated(self))
    self.left_panel_global_panel_widget.layout().addWidget(self.left_panel_global_subtitle_alignment)


    self.left_panel_global_panel_widget.layout().addStretch()

    self.left_panel_global_panel_export_settings_button = QPushButton()
    self.left_panel_global_panel_export_settings_button.clicked.connect(lambda: left_panel_global_panel_export_settings_button_clicked(self))
    self.left_panel_global_panel_widget.layout().addWidget(self.left_panel_global_panel_export_settings_button, 0, Qt.AlignRight)

    update(self)

    
def show(self):
    update(self)


def update(self):
    for item in [self.global_subtitlesvideo_save_as_combobox.itemText(i) for i in range(self.global_subtitlesvideo_save_as_combobox.count())]:
        if session.CONFIG['default_values'] and item.startswith(session.CONFIG['default_values'].get('subtitle_format', 'USF')):
            self.global_subtitlesvideo_save_as_combobox.setCurrentText(item)
            break

    self.global_panel_general_save_copy.setChecked(session.CONFIG['default_values'].get('save_automatic_copy', False))

    self.global_panel_general_minimum_duration_spinbox.setValue(session.CONFIG['default_values'].get('minimum_subtitle_width', 1.0))

    self.left_panel_global_subtitle_alignment.setCurrentText(session.CONFIG['default_values'].get('subtitle_alignment', 'left').capitalize())


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
    self.global_subtitlesvideo_save_as_label.setText(_('global_panel.default_format_save'))
    self.global_panel_general_save_copy.setText(_('global_panel.save_copy'))
    self.global_panel_general_minimum_duration_label.setText(_('global_panel.minimum_duration'))
    self.global_panel_general_minimum_duration_seconds_label.setText(_('units.seconds'))
    self.left_panel_global_panel_export_settings_button.setText(_('global_panel.export_settings'))
    self.left_panel_global_subtitle_alignment.setLabel(_('global_panel.subtitle_alignment'))



    
