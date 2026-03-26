import cv2
import mediapipe as mp
import numpy as np
from autohex import AutoHex

import asyncio
import edge_tts


from PySide6.QtWidgets import QStackedWidget, QWidget, QVBoxLayout, QScrollArea, QDialog, QPushButton, QLabel, QLineEdit, QSizePolicy, QColorDialog, QComboBox, QCheckBox
from PySide6.QtGui import QImage, QPixmap, QPainter, QPainterPath, QColor
from PySide6.QtCore import QThread, Signal, Qt, QSize

from subtitld.interface import utils
from subtitld.interface import left_panel
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import subtitles



class EdgeTTSPanel(QWidget):
    transcript_started = Signal()
    transcript_progress = Signal(int)
    transcript_finished = Signal()
    def __init__(widget, parent=None):
        super().__init__(parent=None)
        widget.parent = parent
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 0)
        widget.layout().setSpacing(10)
        widget.setProperty('dubbing_engine', 'edge-tts')
        widget.setProperty('class', 'transparent_panel')

        scrollable_label = QScrollArea()
        scrollable_label.setWidgetResizable(True)

        widget.voices_label = QLabel()
        widget.voices_label.setWordWrap(True)
        # widget.layout().addWidget(widget.voices_label)

        scrollable_label.setWidget(widget.voices_label)
        widget.layout().addWidget(scrollable_label)

        widget.update_voices_button = QPushButton()
        widget.update_voices_button.setObjectName('global_panel_transcription_assemblyai_widget.update_voices_button')
        widget.update_voices_button.clicked.connect(widget.update_voices_button_clicked)
        widget.layout().addWidget(widget.update_voices_button, 0, Qt.AlignRight)

        # widget.api_key = utils.LabeledLineEdit()
        # widget.api_key.setObjectName('global_panel_transcription_assemblyai_transcription_api_key')
        # widget.api_key.lineedit.setEchoMode(QLineEdit.Password)
        # widget.api_key.editingFinished.connect(lambda value: widget.api_key_activated(value))
        # widget.layout().addWidget(widget.api_key, 1)

        widget.layout().addStretch()

        class EdgeTTSVoicesThread(QThread):
            response = Signal(str)

            def run(self):
                print('started')
                voices = asyncio.run(edge_tts.list_voices())

                for v in voices:
                    text = f"{v['ShortName']} - {v['Gender']} - {v['Locale']}"
                    self.response.emit(text)
                    
        def voices_thread_response(response):
            widget.voices_label.setText(widget.voices_label.text() + "\n" + response)

        widget.voices_thread = EdgeTTSVoicesThread()
        widget.voices_thread.response.connect(voices_thread_response)
        # widget.translate_thread.response_error.connect(lambda: translate_thread_error)
        # widget.translate_thread.started.connect(lambda: widget.transcript_started.emit())
        # widget.translate_thread.progress.connect(lambda value: widget.transcript_progress.emit(value))
        # widget.translate_thread.finished.connect(lambda: widget.transcript_finished.emit())

        widget.update_callback = widget.update
        # widget.transcript_callback = widget.transcript
        widget.translate_callback = widget.translate

    def update(widget):
        widget.api_key.setText(session.CONFIG['transcription'].get('engine_options', {}).get('AssemblyAI', {}).get('api_key', ''))
    

    def update_voices_button_clicked(widget):
        print('clicked')
        widget.voices_label.setText('Updating voices...')
        widget.voices_thread.start()

    def translate(widget):
        widget.update_voices_button.setText("Update")



def load(self):
    tab_name = 'dubbing'
    
    left_panel_dubbing_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )

    self.left_panel_dubbing_enable_checkbox = QCheckBox()
    self.left_panel_dubbing_enable_checkbox.setObjectName('left_panel_dubbing_enable_checkbox')
    self.left_panel_dubbing_enable_checkbox.clicked.connect(lambda: left_panel_dubbing_enable_checkbox_clicked(self))
    left_panel_dubbing_panel.layout().addWidget(self.left_panel_dubbing_enable_checkbox)

    self.left_panel_dubbing_engine_combobox = utils.LabeledComboBox()
    self.left_panel_dubbing_engine_combobox.setProperty('class', 'button')
    self.left_panel_dubbing_engine_combobox.addItems(['edge-tts', 'AssemblyAI'])
    self.left_panel_dubbing_engine_combobox.activated.connect(lambda: left_panel_dubbing_engine_combobox_activated(self))
    left_panel_dubbing_panel.layout().addWidget(self.left_panel_dubbing_engine_combobox)

    self.global_panel_dubbing_tabwidget = QStackedWidget()

    self.global_panel_dubbing_edgetts_transcription_widget = EdgeTTSPanel()

    self.global_panel_dubbing_tabwidget.addWidget(self.global_panel_dubbing_edgetts_transcription_widget)

    left_panel_dubbing_panel.layout().addWidget(self.global_panel_dubbing_tabwidget)

    update(self)


def left_panel_dubbing_enable_checkbox_clicked(self):
    session.CONFIG['dubbing']['enabled'] = self.left_panel_dubbing_enable_checkbox.isChecked()


def left_panel_dubbing_engine_combobox_activated(self):
    session.CONFIG['dubbing']['selected_engine'] = self.left_panel_dubbing_engine_combobox.currentText()
    global_panel_dubbing_tabwidget_update(self)


def update(self):
    self.left_panel_dubbing_enable_checkbox.setChecked(session.CONFIG['dubbing'].get('enabled', False))
    self.left_panel_dubbing_engine_combobox.setCurrentText(session.CONFIG['dubbing'].get('selected_engine', 'edge-tts'))


def translate(self):
    self.left_panel_dubbing_enable_checkbox.setText(_('subtitles_panel_widget_dubbing.enable_dubbing'))
    self.left_panel_dubbing_engine_combobox.setLabel(_('subtitles_panel_widget_dubbing.engine'))


def global_panel_dubbing_tabwidget_update(self):
    for widget in self.global_panel_dubbing_tabwidget.findChildren(QWidget):
        if widget.property('dubbing_engine') == self.left_panel_dubbing_engine_combobox.currentText():
            self.global_panel_dubbing_tabwidget.setCurrentWidget(widget)
            widget.update_callback()
            break
    