from PySide6.QtWidgets import QVBoxLayout, QWidget, QHBoxLayout, QScrollArea, QStackedWidget, QPushButton
from PySide6.QtCore import QPropertyAnimation, QEasingCurve

from subtitld.interface import utils
from subtitld.interface import left_panel
from subtitld.interface.translation import _


def load(self):
    tab_name = 'metadata'
    left_panel.add_button(self, tab_name)
    
    left_panel_metadata_panel = QWidget()
    left_panel_metadata_panel.setObjectName(f'left_panel_{tab_name}')
    left_panel_metadata_panel.setStyleSheet('background-color: red;')
    left_panel_metadata_panel.setLayout(QVBoxLayout())
    left_panel_metadata_panel.layout().setContentsMargins(0, 0, 0, 0)
    
    left_panel.add_panel(self, left_panel_metadata_panel)

    
def show(self):
    pass


def hide(self):
    pass
    
def translate(self):
    pass