import os
from translate import Translator

from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel, QScrollArea, QHBoxLayout, QPushButton, QComboBox, QGroupBox, QTabWidget, QProgressBar, QMessageBox
from PySide6.QtCore import Qt, QThread, Signal, QRect, QSize, QMargins
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import file_io

LANGUAGE_DESCRIPTIONS = session.LANGUAGE_DICT_LIST.keys()


class global_panel_translation_thread(QThread):
    """Thread to generate burned video"""
    response = Signal(dict)
    subtitles_list = []
    language_from = 'en'
    language_to = 'en'

    def run(self):
        if self.subtitles_list and self.language_from and self.language_to:
            translator = Translator(from_lang=self.language_from, to_lang=self.language_to)

            for subtitle in self.subtitles_list:
                self.response.emit({i : translator.translate(subtitle['text'].replace('\n', ' ').replace('  ', ' '))})

            self.response.emit({'status' : 'end'})

def load(self):
    tab_name = 'translation'
    
    left_panel_translation_panel = QWidget()
    left_panel_translation_panel.setObjectName(f'left_panel_{tab_name}')
    left_panel_translation_panel.setProperty('tab_name', tab_name)
    left_panel_translation_panel.setLayout(QVBoxLayout())
    left_panel_translation_panel.layout().setContentsMargins(10, 10, 10, 10)

    left_panel_translation_panel_scroll = QScrollArea()
    left_panel_translation_panel_scroll.setObjectName('left_panel_translation_panel_scroll')
    left_panel_translation_panel_scroll.setWidgetResizable(True)
    left_panel_translation_panel_scroll.setFrameShape(QScrollArea.NoFrame)
    left_panel_translation_panel.layout().addWidget(left_panel_translation_panel_scroll)

    self.left_panel_translation_panel_widget = QWidget()
    self.left_panel_translation_panel_widget.setObjectName('left_panel_translation_panel_widget')
    self.left_panel_translation_panel_widget.setLayout(QVBoxLayout())
    self.left_panel_translation_panel_widget.layout().setContentsMargins(0, 0, 0, 0)
    left_panel_translation_panel_scroll.setWidget(self.left_panel_translation_panel_widget)

    self.global_subtitlesvideo_autosync_lang_from_combobox = QComboBox()
    self.global_subtitlesvideo_autosync_lang_from_combobox.setProperty('class', 'button')
    self.global_subtitlesvideo_autosync_lang_from_combobox.addItems(LANGUAGE_DESCRIPTIONS)
    self.left_panel_translation_panel_widget.layout().addWidget(self.global_subtitlesvideo_autosync_lang_from_combobox, 0, Qt.AlignTop)

    self.global_subtitlesvideo_autosync_lang_to_combobox = QComboBox()
    self.global_subtitlesvideo_autosync_lang_to_combobox.setProperty('class', 'button')
    self.global_subtitlesvideo_autosync_lang_to_combobox.addItems(LANGUAGE_DESCRIPTIONS)
    self.left_panel_translation_panel_widget.layout().addWidget(self.global_subtitlesvideo_autosync_lang_to_combobox, 0, Qt.AlignTop)

    self.global_subtitlesvideo_translate_button = QPushButton()
    self.global_subtitlesvideo_translate_button.setProperty('class', 'button')
    self.global_subtitlesvideo_translate_button.clicked.connect(lambda: global_subtitlesvideo_translate_button_clicked(self))
    self.left_panel_translation_panel_widget.layout().addWidget(self.global_subtitlesvideo_translate_button, 0, Qt.AlignTop)

    def global_panel_translation_thread_ended(response):
        if 'status' in response and response['status'] == 'end':
            # subtitles_panel.update_processing_status(self, show_widgets=False, value=0)
            self.global_subtitlesvideo_translate_button.setEnabled(True)
        else:
            for sub in response:
                # subtitles_panel.update_processing_status(self, show_widgets=True, value=int((sub / len(session.SUBTITLE['segments'])) * 100))
                session.SUBTITLE['segments'][sub]['text'] = response[sub]


    self.global_panel_translation_thread = global_panel_translation_thread(self)
    self.global_panel_translation_thread.response.connect(global_panel_translation_thread_ended)

    left_panel_translation_panel.update = update

    left_panel.add_panel(self, left_panel_translation_panel)

    update(self)

    
def show(self):
    update(self)

def update(self):
    pass

def global_subtitlesvideo_translate_button_clicked(self):
    """Function to translate subtitles"""
    run_command = False

    if bool(session.SUBTITLE['segments']):
        are_you_sure_message = QMessageBox(self)
        are_you_sure_message.setWindowTitle(_('alert.are_you_sure'))
        are_you_sure_message.setText(_('alert.overwrite_warning'))
        are_you_sure_message.addButton('Yes', QMessageBox.AcceptRole)
        are_you_sure_message.addButton('No', QMessageBox.RejectRole)
        ret = are_you_sure_message.exec()

        if ret == 0:
            run_command = True
    else:
        run_command = True


    if run_command:
        self.global_panel_translation_thread.subtitles_list = session.SUBTITLE['segments']
        self.global_panel_translation_thread.language_from = session.LANGUAGE_DICT_LIST[self.global_subtitlesvideo_autosync_lang_from_combobox.currentText()].split('-')[0]
        self.global_panel_translation_thread.language_to = session.LANGUAGE_DICT_LIST[self.global_subtitlesvideo_autosync_lang_to_combobox.currentText()].split('-')[0]
        self.global_panel_translation_thread.start()

        # subtitles_panel.update_processing_status(self, show_widgets=True, value=33)
        self.global_subtitlesvideo_translate_button.setEnabled(False)


    
def hide(self):
    pass

    
def translate(self):    
    self.global_subtitlesvideo_translate_button.setText(_('translation_panel.translate'))



    
