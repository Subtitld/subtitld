from PySide6.QtWidgets import QSplitter
from PySide6.QtCore import Qt, QTimer

from subtitld.modules import file_io
from subtitld.modules import session

from subtitld.interface import left_panel
from subtitld.interface import preview_panel
from subtitld.interface import bottom_panel
from subtitld.interface import top_bar
from subtitld.interface.translation import _


def load(self):
    self.main_horizontal_splitter = QSplitter(Qt.Horizontal)
    self.main_horizontal_splitter.setHandleWidth(0)
    self.main_horizontal_splitter.splitterMoved.connect(lambda pos, index: main_horizontal_splitter_changed(self, pos, index))

    left_panel.load(self)
    
    preview_panel.load(self)
    
    self.main_horizontal_splitter.setSizes(session.CONFIG['interface_splitters'].get('main_horizontal', [25, 75]))

    self.main_vertical_splitter = QSplitter(Qt.Vertical)
    self.main_vertical_splitter.setObjectName('main_vertical_splitter')
    self.main_vertical_splitter.splitterMoved.connect(lambda pos, index: main_vertical_splitter_changed(self, pos, index))
    # Hide the native splitter handle — playercontrols renders its own
    # custom drag button at its top-right that drives the splitter sizes.
    self.main_vertical_splitter.setHandleWidth(0)

    self.main_vertical_splitter.addWidget(self.main_horizontal_splitter)
    
    bottom_panel.load(self)
    
    self.central_widget.layout().addWidget(self.main_vertical_splitter)

    self.main_vertical_splitter.setSizes(session.CONFIG['interface_splitters'].get('main_vertical', [70, 30]))

    if session.CONFIG.get('autosave', {}).get('backup_enabled', True):
        self.autosave_backup_timer.start()
        # The regular timer interval defaults to 5 min — too long for a fresh
        # project the user just started editing. Fire an early dirty-check
        # 30s after the production screen loads so the first backup lands
        # quickly (autosave_backup_timer_timeout itself bails when nothing
        # is dirty, so this is a no-op for read-only browsing).
        QTimer.singleShot(30000, lambda: file_io.autosave_backup_timer_timeout())

    if session.CONFIG.get('autosave', {}).get('original_enabled', True) and session.SUBTITLE.get('filepath', '').lower().endswith('.usfx'):
        self.autosave_original_timer.start()


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
    top_bar.translate(self)
    left_panel.translate(self)
    preview_panel.translate(self)
    bottom_panel.translate(self)
    