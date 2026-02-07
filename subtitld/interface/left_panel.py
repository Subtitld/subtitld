from PySide6.QtWidgets import QVBoxLayout, QWidget, QHBoxLayout, QScrollArea, QStackedWidget, QPushButton, QSizePolicy, QGraphicsOpacityEffect
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QTimer, Qt
from PySide6.QtGui import QIcon

from subtitld.modules import session

from subtitld.interface import left_panel_subtitleslist
from subtitld.interface import left_panel_metadata
from subtitld.interface import left_panel_speakers
from subtitld.interface import left_panel_qualitycheck
from subtitld.interface import left_panel_global
from subtitld.interface import left_panel_interface
from subtitld.interface import left_panel_keyboard
from subtitld.interface import left_panel_transcription
from subtitld.interface import left_panel_translation
from subtitld.interface import left_panel_import
from subtitld.interface import left_panel_export
from subtitld.interface import left_panel_autosave
from subtitld.interface import utils
from subtitld.interface.translation import _


class navigation_button(QPushButton):
    def __init__(self, tab_name):
        super().__init__()
        self.tab_name = tab_name
        self.setProperty('class', 'navigation_button')
        self.setCheckable(True)
        self.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum))
        self.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS / f'left_panel_{tab_name}.svg')))
    
    def mousePressEvent(self, e):
        stacked_tabs = self.window().findChild(QStackedWidget, 'left_panel_stackedwidgets')
        if stacked_tabs:
            tab = stacked_tabs.findChild(QWidget, f'left_panel_{self.tab_name}')
            if tab:
                stacked_tabs.setCurrentWidget(tab)
        for button in self.window().findChildren(navigation_button):
            if button == self:
                continue
            button.setChecked(False)
        update(self.window())
        return super().mousePressEvent(e)


def load(self):
    self.left_panel_container = QWidget()
    self.left_panel_container.setLayout(QVBoxLayout())
    self.left_panel_container.layout().setContentsMargins(0, 0, 0, 0)

    self.left_panel = QWidget(self.left_panel_container)
    self.left_panel.setLayout(QHBoxLayout())
    self.left_panel.layout().setContentsMargins(0, self.titleBar.height() - 1, 0, 0)
    self.left_panel.layout().setSpacing(0)
    self.left_panel.opacity = QGraphicsOpacityEffect()
    self.left_panel.opacity.setOpacity(0)
    self.left_panel.setGraphicsEffect(self.left_panel.opacity)
    self.left_panel.animation = QPropertyAnimation(self.left_panel, b'pos')
    self.left_panel.animation.setEasingCurve(QEasingCurve.OutCubic)
    self.left_panel_container.layout().addWidget(self.left_panel)
    # self.left_panel.hide()

    self.main_horizontal_splitter.addWidget(self.left_panel_container)

    navigation_scroll = QScrollArea()
    navigation_scroll.setViewportMargins(0, 0, 0, 0)
    navigation_scroll.setWidgetResizable(True)
    navigation_scroll.setFixedWidth(50)
    navigation_scroll.setFrameShape(QScrollArea.NoFrame)
    navigation_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    navigation_scroll.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))

    self.left_panel_navigation = QWidget()
    self.left_panel_navigation.setObjectName('left_panel_navigation')
    self.left_panel_navigation.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.left_panel_navigation.setLayout(QVBoxLayout())
    self.left_panel_navigation.layout().setContentsMargins(0, 0, 0, 0)
    self.left_panel_navigation.layout().setSpacing(0)
    self.left_panel_navigation.layout().addStretch()
    navigation_scroll.setWidget(self.left_panel_navigation)

    self.left_panel.layout().addWidget(navigation_scroll)

    self.left_panel_stackedwidgets = QStackedWidget()
    self.left_panel_stackedwidgets.setObjectName('left_panel_stackedwidgets')
    self.left_panel.layout().addWidget(self.left_panel_stackedwidgets)

    left_panel_subtitleslist.load(self)    
    left_panel_metadata.load(self)
    left_panel_speakers.load(self)
    left_panel_qualitycheck.load(self)
    left_panel_global.load(self)
    left_panel_interface.load(self)
    left_panel_keyboard.load(self)
    left_panel_transcription.load(self)
    left_panel_translation.load(self)
    left_panel_import.load(self)
    left_panel_export.load(self)
    left_panel_autosave.load(self)

    self.left_panel_navigation.layout().itemAt(0).widget().click()

    
def show(self):
    QTimer().singleShot(200, lambda: self.left_panel.opacity.setOpacity(1.0))
    utils.animate_element(self.left_panel.animation, duration=1000, effect='slide_from_left')
    left_panel_subtitleslist.show(self)


def hide(self):
    utils.animate_element(self.left_panel.animation, duration=200, effect='slide_to_left')


def update(self):
    self.left_panel_stackedwidgets.currentWidget().update(self)


def add_panel(self, widget):
    button = navigation_button(widget.property('tab_name'))
    self.left_panel_navigation.layout().insertWidget(self.left_panel_navigation.layout().count() - 1, button)
    self.left_panel_stackedwidgets.addWidget(widget)


def translate(self):
    left_panel_subtitleslist.translate(self)
    left_panel_metadata.translate(self)
    left_panel_speakers.translate(self)
    left_panel_qualitycheck.translate(self)
    left_panel_global.translate(self)
    left_panel_interface.translate(self)
    left_panel_keyboard.translate(self)
    left_panel_transcription.translate(self)
    left_panel_translation.translate(self)
    left_panel_import.translate(self)
    left_panel_export.translate(self)
    left_panel_autosave.translate(self)