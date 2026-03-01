import os
import subprocess

from PySide6.QtWidgets import QVBoxLayout, QWidget, QScrollArea, QCheckBox, QPushButton, QComboBox, QGroupBox, QTabWidget, QGridLayout, QHBoxLayout, QLabel, QSpinBox, QLineEdit, QSizePolicy, QTableWidgetItem, QColorDialog
from PySide6.QtCore import Qt, QThread, Signal, QSize, QMargins
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPixmap, QFontDatabase, QBrush

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import file_io
from subtitld.modules import utils
from subtitld.modules import shortcuts
from subtitld.modules import subtitles


def load(self):
    tab_name = 'autosave'
    
    left_panel_autosave_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )

    left_panel_autosave_panel_scroll = QScrollArea()
    left_panel_autosave_panel_scroll.setObjectName('left_panel_autosave_panel_scroll')
    left_panel_autosave_panel_scroll.setWidgetResizable(True)
    left_panel_autosave_panel_scroll.setFrameShape(QScrollArea.NoFrame)
    left_panel_autosave_panel.layout().addWidget(left_panel_autosave_panel_scroll)

    left_panel_autosave_panel_widget = QWidget()
    left_panel_autosave_panel_widget.setProperty('class', 'transparent_panel')
    left_panel_autosave_panel_widget.setObjectName('left_panel_autosave_panel_widget')
    left_panel_autosave_panel_widget.setLayout(QVBoxLayout())
    left_panel_autosave_panel_widget.layout().setContentsMargins(0, 0, 0, 0)
    left_panel_autosave_panel_scroll.setWidget(left_panel_autosave_panel_widget)

    self.panel_autosave_backup_enabled_checkbox = QCheckBox()
    self.panel_autosave_backup_enabled_checkbox.clicked.connect(lambda: panel_autosave_backup_enabled_checkbox_clicked(self))
    left_panel_autosave_panel_widget.layout().addWidget(self.panel_autosave_backup_enabled_checkbox)

    self.panel_autosave_backup_interval_row = QWidget()
    self.panel_autosave_backup_interval_row.setLayout(QHBoxLayout())
    self.panel_autosave_backup_interval_row.layout().setContentsMargins(0, 0, 0, 0)
    self.panel_autosave_backup_interval_row.layout().setSpacing(5)
    left_panel_autosave_panel_widget.layout().addWidget(self.panel_autosave_backup_interval_row)

    self.panel_autosave_backup_interval_label = QLabel()
    self.panel_autosave_backup_interval_row.layout().addWidget(self.panel_autosave_backup_interval_label, 0, Qt.AlignLeft)

    self.panel_autosave_backup_interval_spinbox = QSpinBox()
    self.panel_autosave_backup_interval_spinbox.setMinimum(1)
    self.panel_autosave_backup_interval_spinbox.setMaximum(1000)
    self.panel_autosave_backup_interval_spinbox.valueChanged.connect(lambda: panel_autosave_backup_interval_spinbox_changed(self))
    self.panel_autosave_backup_interval_row.layout().addWidget(self.panel_autosave_backup_interval_spinbox, 0, Qt.AlignLeft)

    self.panel_autosave_backup_interval_seconds_label = QLabel()
    self.panel_autosave_backup_interval_seconds_label.setProperty('class', 'units_label')
    self.panel_autosave_backup_interval_row.layout().addWidget(self.panel_autosave_backup_interval_seconds_label, 0, Qt.AlignLeft)
    self.panel_autosave_backup_interval_row.layout().addStretch()

    left_panel_autosave_panel_widget.layout().addStretch()
    

    update(self)



def show(self):
    update(self)


def update(self):
    self.panel_autosave_backup_enabled_checkbox.setChecked(session.CONFIG.get('autosave', {}).get('backup_enabled', True))
    if session.CONFIG.get('autosave', {}).get('backup_enabled', True) and not self.autosave_timer.isActive():
        self.autosave_timer.start()
    else:
        self.autosave_timer.stop()
    
    self.panel_autosave_backup_interval_row.setEnabled(session.CONFIG.get('autosave', {}).get('backup_enabled', True))
    self.panel_autosave_backup_interval_spinbox.setValue(session.CONFIG.get('autosave', {}).get('interval', 300000) / (60 * 1000))
    
def hide(self):
    pass


def panel_autosave_backup_enabled_checkbox_clicked(self):
    session.CONFIG['autosave']['backup_enabled'] = self.panel_autosave_backup_enabled_checkbox.isChecked()
    update(self)

def panel_autosave_backup_interval_spinbox_changed(self):
    session.CONFIG['autosave']['interval'] = self.panel_autosave_backup_interval_spinbox.value() * 60 * 1000
    update(self)

    
def translate(self):    
    self.panel_autosave_backup_enabled_checkbox.setText(_('autosave_panel.autosave_backup_enable'))
    self.panel_autosave_backup_interval_label.setText(_('autosave_panel.autosave_backup_interval'))
    self.panel_autosave_backup_interval_seconds_label.setText(_('units.minutes'))



    
