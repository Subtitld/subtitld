import os
from translate import Translator

from PySide6.QtWidgets import QVBoxLayout, QWidget, QScrollArea, QFileDialog, QPushButton
from PySide6.QtCore import Qt, QThread, Signal, QRect, QSize, QMargins
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import file_io
from subtitld.modules.session import LIST_OF_SUPPORTED_IMPORT_EXTENSIONS

list_of_supported_import_extensions = []
for exttype in LIST_OF_SUPPORTED_IMPORT_EXTENSIONS:
    for ext in LIST_OF_SUPPORTED_IMPORT_EXTENSIONS[exttype]['extensions']:
        list_of_supported_import_extensions.append(ext)


def load(self):
    tab_name = 'import'
    
    left_panel_import_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )
    
    left_panel_import_panel_scroll = QScrollArea()
    left_panel_import_panel_scroll.setObjectName('left_panel_import_panel_scroll')
    left_panel_import_panel_scroll.setWidgetResizable(True)
    left_panel_import_panel_scroll.setFrameShape(QScrollArea.NoFrame)
    left_panel_import_panel.layout().addWidget(left_panel_import_panel_scroll)

    self.left_panel_import_panel_widget = QWidget()
    self.left_panel_import_panel_widget.setProperty('class', 'transparent_panel')
    self.left_panel_import_panel_widget.setObjectName('left_panel_import_panel_widget')
    self.left_panel_import_panel_widget.setLayout(QVBoxLayout())
    self.left_panel_import_panel_widget.layout().setContentsMargins(0, 0, 0, 0)
    left_panel_import_panel_scroll.setWidget(self.left_panel_import_panel_widget)

    self.global_subtitlesvideo_import_button = QPushButton(parent=self.left_panel_import_panel_widget)
    self.global_subtitlesvideo_import_button.setProperty('class', 'button')
    # self.global_subtitlesvideo_import_button.setCheckable(True)
    self.global_subtitlesvideo_import_button.clicked.connect(lambda: global_subtitlesvideo_import_button_clicked(self))


    update(self)

def global_subtitlesvideo_import_button_clicked(self):
    """Function to import file"""
    # if self.global_subtitlesvideo_import_button.isChecked():
    #     self.global_subtitlesvideo_export_button.setGeometry(20, 200, self.global_panel_menu.width() - 40, 30)
    # else:
    #     self.global_subtitlesvideo_export_button.setGeometry(20, 120, self.global_panel_menu.width() - 40, 30)
    # self.global_subtitlesvideo_import_panel.setVisible(self.global_subtitlesvideo_import_button.isChecked())

    supported_import_files = 'Text files' + ' ({})'.format(" ".join([" * .{}".format(fo) for fo in list_of_supported_import_extensions]))
    file_to_open = QFileDialog.getOpenFileName(parent=self, caption='Select the file to import', dir=os.path.expanduser("~"), filter=supported_import_files)[0]
    if file_to_open:
        session.SUBTITLE['segments'] += file_io.import_file(filename=file_to_open)[0]
        session.SUBTITLE['segments'].sort()
        # update_widgets(self)
    
def show(self):
    update(self)

def update(self):
    pass

    
def hide(self):
    pass

    
def translate(self):    
    self.global_subtitlesvideo_import_button.setText(_('import_panel.import'))



    
