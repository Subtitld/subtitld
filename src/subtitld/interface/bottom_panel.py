from PySide6.QtWidgets import QVBoxLayout, QWidget, QGraphicsOpacityEffect
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QTimer

from subtitld.interface import utils
from subtitld.interface import playercontrols
from subtitld.interface.translation import _


def load(self):
    self.bottom_panel_container = QWidget()
    self.bottom_panel_container.setLayout(QVBoxLayout())
    self.bottom_panel_container.layout().setContentsMargins(0, 0, 0, 0)
    self.bottom_panel = QWidget(self.bottom_panel_container)
    self.bottom_panel.setLayout(QVBoxLayout())
    self.bottom_panel.layout().setContentsMargins(0, 0, 0, 0)
    self.bottom_panel.layout().setSpacing(0)
    self.bottom_panel.opacity = QGraphicsOpacityEffect()
    self.bottom_panel.opacity.setOpacity(0)
    self.bottom_panel.setGraphicsEffect(self.bottom_panel.opacity)
    self.bottom_panel_container.layout().addWidget(self.bottom_panel)
    self.bottom_panel.animation = QPropertyAnimation(self.bottom_panel, b'pos')
    self.bottom_panel.animation.setEasingCurve(QEasingCurve.OutCubic)

    self.main_vertical_splitter.addWidget(self.bottom_panel_container)

    playercontrols.load(self)

    self.bottom_panel.layout().addWidget(self.playercontrols_widget)
    
    
def show(self):
    # Opacity = 1 immediately. setUpdatesEnabled(False) in
    # productionscreen.show handles flash-hiding; a 0 → 1 opacity fade
    # would just leave the panel invisible through the OutCubic
    # easing's fast opening — see preview_panel.show().
    self.bottom_panel.opacity.setOpacity(1.0)
    utils.animate_element(self.bottom_panel.animation, duration=1000, effect='slide_from_bottom')
    self.timeline_widget.update()
    playercontrols.show(self)


def hide(self):
    utils.animate_element(self.bottom_panel.animation, duration=200, effect='slide_to_bottom')


def translate(self):
    playercontrols.translate(self)