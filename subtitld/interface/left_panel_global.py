from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel, QScrollArea, QCheckBox, QComboBox, QHBoxLayout, QSpinBox, QDoubleSpinBox, QSlider
from PySide6.QtCore import Qt

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.modules import session


def load(self):
    tab_name = 'global'
    
    left_panel_global_panel = QWidget()
    left_panel_global_panel.setObjectName(f'left_panel_{tab_name}')
    left_panel_global_panel.setProperty('tab_name', tab_name)
    left_panel_global_panel.setLayout(QVBoxLayout())
    left_panel_global_panel.layout().setContentsMargins(10, 10, 10, 10)

    left_panel_global_panel_scroll = QScrollArea()
    left_panel_global_panel_scroll.setObjectName('left_panel_global_panel_scroll')
    left_panel_global_panel_scroll.setWidgetResizable(True)
    left_panel_global_panel_scroll.setFrameShape(QScrollArea.NoFrame)
    left_panel_global_panel.layout().addWidget(left_panel_global_panel_scroll)

    self.left_panel_global_panel_widget = QWidget()
    self.left_panel_global_panel_widget.setObjectName('left_panel_global_panel_widget')
    self.left_panel_global_panel_widget.setLayout(QVBoxLayout())
    self.left_panel_global_panel_widget.layout().setContentsMargins(0, 0, 0, 0)
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

    self.left_panel_global_panel_widget.layout().addStretch()
    
    left_panel.add_panel(self, left_panel_global_panel)

    left_panel_global_panel.update = update

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


def global_subtitlesvideo_save_as_combobox_activated(self):
    session.CONFIG['default_values']['subtitle_format'] = self.global_subtitlesvideo_save_as_combobox.currentText().split(' ', 1)[0]


def global_panel_general_save_copy_changed(self):
    session.CONFIG['default_values']['save_automatic_copy'] = self.global_panel_general_save_copy.isChecked()


def global_panel_general_minimum_duration_spinbox_changed(self):
    session.CONFIG['default_values']['minimum_subtitle_width'] = self.global_panel_general_minimum_duration_spinbox.value()

    
def hide(self):
    pass

    
def translate(self):    
    self.global_subtitlesvideo_save_as_label.setText(_('global_panel.default_format_save'))
    self.global_panel_general_save_copy.setText(_('global_panel.save_copy'))
    self.global_panel_general_minimum_duration_label.setText(_('global_panel.minimum_duration'))
    self.global_panel_general_minimum_duration_seconds_label.setText(_('units.seconds'))



    
