from PySide6.QtWidgets import QSplitter
from PySide6.QtCore import Qt

from subtitld.modules import file_io
from subtitld.modules import session

from subtitld.interface import left_panel
from subtitld.interface import preview_panel
from subtitld.interface import bottom_panel
from subtitld.interface.translation import _


def load(self):
    self.main_horizontal_splitter = QSplitter(Qt.Horizontal)
    self.main_horizontal_splitter.splitterMoved.connect(lambda pos, index: main_horizontal_splitter_changed(self, pos, index))

    left_panel.load(self)
    
    preview_panel.load(self)
    
    self.main_horizontal_splitter.setSizes(session.CONFIG['interface_splitters'].get('main_horizontal', [25, 75]))

    self.main_vertical_splitter = QSplitter(Qt.Vertical)
    self.main_vertical_splitter.splitterMoved.connect(lambda pos, index: main_vertical_splitter_changed(self, pos, index))

    self.main_vertical_splitter.addWidget(self.main_horizontal_splitter)
    
    bottom_panel.load(self)
    
    self.central_widget.layout().addWidget(self.main_vertical_splitter)

    self.main_vertical_splitter.setSizes(session.CONFIG['interface_splitters'].get('main_vertical', [70, 30]))


def main_horizontal_splitter_changed(self, pos, index):
    session.CONFIG['interface_splitters']['main_horizontal'] = self.main_horizontal_splitter.sizes()


def main_vertical_splitter_changed(self, pos, index):
    session.CONFIG['interface_splitters']['main_vertical'] = self.main_vertical_splitter.sizes()


def show(self):
    self.central_widget.layout().setCurrentWidget(self.main_vertical_splitter)
    left_panel.show(self)
    preview_panel.show(self)
    bottom_panel.show(self)
    

def hide(self):
    left_panel.hide(self)
    preview_panel.hide(self)
    bottom_panel.hide(self)


def translate(self):
    left_panel.translate(self)
    preview_panel.translate(self)
    bottom_panel.translate(self)