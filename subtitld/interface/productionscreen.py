import os
import sys
from datetime import datetime

from PySide6.QtWidgets import QSplitter, QLabel, QGraphicsOpacityEffect, QListWidget, QListWidgetItem, QVBoxLayout, QWidget, QHBoxLayout, QSizePolicy
from PySide6.QtCore import QPropertyAnimation, Qt, QSize, QEasingCurve, QTimer

from subtitld.interface import global_panel, player, playercontrols, timeline, subtitles_panel
from subtitld.modules import file_io, session
from subtitld.interface.translation import _


def load(self):
    self.main_horizontal_splitter = QSplitter(Qt.Horizontal)

    global_panel.load(self)

    subtitles_panel.load(self)

    self.main_horizontal_splitter.addWidget(self.subtitles_panel_widget)

    player.load(self)

    self.main_horizontal_splitter.addWidget(self.player_widget)

    self.main_vertical_splitter = QSplitter(Qt.Vertical)

    self.main_vertical_splitter.addWidget(self.main_horizontal_splitter)

    playercontrols.load(self)

    self.main_vertical_splitter.addWidget(self.playercontrols_widget)

    
def show(self):
    self.main_widget.setCurrentIndex(1)
    subtitles_panel.show(self)
    playercontrols.show(self)
    global_panel.hide_global_panel(self)
    # timeline.update_timeline(self)
    

    # timeline.update_timeline(self)
    # self.startscreen.hide(self)
    # playercontrols.show(self)
    # self.subtitles_panel.show(self)

    # playercontrols.show(self)
    # self.subtitles_panel.show(self)
    # self.global_panel.hide_global_panel(self)
    # player_qrect = [self.player_widget.x(), self.player_widget.y(), self.player_widget.width(), self.player_widget.height()] 
    # original_qrect = [self.start_screen_thumbnail_background.x(), self.start_screen_thumbnail_background.y(), self.start_screen_thumbnail_background.width(), self.start_screen_thumbnail_background.height()]
    # self.generate_effect(self.start_screen_thumbnail_background_transparency_animation, 'opacity', 200, 1.0, 0.0)
    # self.generate_effect(self.player_border_animation, 'geometry', 700, original_qrect, player_qrect)
    # self.generate_effect(self.player_border_transparency_animation, 'opacity', 700, 0.5, 1.0)



def hide(self):
    """Function to hide starting panel"""
    self.generate_effect(self.start_screen_animation_out, 'geometry', 200, [self.start_screen.x(), self.start_screen.y(), self.start_screen.width(), self.start_screen.height()], [self.start_screen.x(), int(self.start_screen.height()), self.start_screen.width(), self.start_screen.height()])
    self.generate_effect(self.start_screen_transparency_animation, 'opacity', 200, 1.0, 0.5)
