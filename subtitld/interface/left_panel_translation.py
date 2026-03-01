import os
from deep_translator import GoogleTranslator
import subprocess

from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel, QCheckBox, QStackedWidget, QHBoxLayout, QProgressBar, QPushButton
from PySide6.QtCore import Qt, QThread, Signal

from subtitld.interface import left_panel
from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import session

LANGUAGE_DESCRIPTIONS = session.LANGUAGE_DICT_LIST.keys()
INVERTED_LANGUAGES = {v: k for k, v in session.LANGUAGE_DICT_LIST.items()}


class GoogleTranslatorPanel(QWidget):
    translation_started = Signal()
    translation_progress = Signal(int)
    translation_finished = Signal()
    def __init__(widget, parent=None):
        super().__init__(parent=None)
        widget.parent = parent
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 0)
        widget.layout().setSpacing(10)
        widget.setProperty('translation_engine', 'GoogleTranslator')
        widget.setProperty('class', 'transparent_panel')

        widget.use_context = QCheckBox()
        widget.use_context.clicked.connect(lambda: widget.save_config())
        widget.use_context.setObjectName('global_panel_translation_google_translator_use_context')
        widget.layout().addWidget(widget.use_context)

        widget.layout().addStretch()

        class GoogleTranslatorThread(QThread):
            response = Signal(dict)
            response_error = Signal(str)
            progress = Signal(int)
            sentences_list = None
            
            def run(self):
                if self.sentences_list:
                    target_language = session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-US')
                    use_context = session.CONFIG['translation'].get('engine_options', {}). get('GoogleTranslator', {}).get('use_context', False)
                    try:
                        context_full = []

                        for index, segment in enumerate(self.sentences_list):
                            self.progress.emit(int((index / len(self.sentences_list)) * 100))
                            context = ''
                            
                            if use_context:
                                if context_full:
                                    index = 0
                                    while index < len(context_full) and (len(context + list(reversed(context_full))[index] + segment['text']) + 7 < 5000):
                                        context = list(reversed(context_full))[index] + ' ' + context
                                        index += 1

                                context_full.append(segment['text'])

                            translated_text = GoogleTranslator(source='auto', target=target_language[:2]).translate((f'{context}␟' if context else '') + segment['text'])

                            translated_text = translated_text.rsplit('␟')[-1].strip().replace('\u200b', '')

                            self.response.emit({
                                'original': segment['text'],
                                'translation': translated_text,
                                'language': target_language    
                            })

                    except Exception as e:
                        self.response_error.emit(str(e))
                
        def translate_thread_response(response):
            if isinstance(response, dict):
                for segment in session.SUBTITLE['segments']:
                    if segment['text'] == response['original']:
                        if 'translations' not in segment:
                            segment['translations'] = {}
                        segment['translations'][response['language']] = response['translation']
                widget.window().timeline_widget.update()
                session.set_unsaved()

        def translate_thread_error(response):
            error_dialog = utils.SimpleDialog(widget, title=_('translation_panel.error'), text=response)
            label = QLabel(response)
            error_dialog.content.layout().addWidget(label)
            error_dialog.reject_button.setVisible(False)
            error_dialog.exec()

        widget.translate_thread = GoogleTranslatorThread()
        widget.translate_thread.started.connect(lambda: widget.translation_started.emit())
        widget.translate_thread.progress.connect(lambda value: widget.translation_progress.emit(value))
        widget.translate_thread.finished.connect(lambda: widget.translation_finished.emit())
        widget.translate_thread.response.connect(translate_thread_response)
        widget.translate_thread.response_error.connect(lambda: translate_thread_error)

        widget.update_callback = widget.update
        widget.translate_process_callback = widget.translate_process
        widget.translate_callback = widget.translate

    def save_config(widget):
        if not 'engine_options' in session.CONFIG['translation']:
            session.CONFIG['translation']['engine_options'] = {}
        if not 'GoogleTranslator' in session.CONFIG['translation']['engine_options']:
            session.CONFIG['translation']['engine_options']['GoogleTranslator'] = {}
        session.CONFIG['translation']['engine_options']['GoogleTranslator']['use_context'] = widget.use_context.isChecked()

    def update(widget):
        widget.use_context.setChecked(session.CONFIG['translation'].get('engine_options', {}).get('GoogleTranslator', {}).get('use_context', False))
        
    def translate_process(widget):
        widget.translate_thread.sentences_list = session.SUBTITLE['segments']
        widget.translate_thread.start()

    def translate(widget):
        widget.use_context.setText(_('translation_panel.use_context'))


def load(self):
    tab_name = 'translation'
    
    left_panel_translation_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )

    self.global_panel_translation_show_translations_button = QCheckBox()
    self.global_panel_translation_show_translations_button.setObjectName('global_panel_translation_show_translations_button')
    self.global_panel_translation_show_translations_button.clicked.connect(lambda: global_panel_translation_show_translations_button_clicked(self))
    left_panel_translation_panel.layout().addWidget(self.global_panel_translation_show_translations_button)

    self.global_panel_translation_target_language_combobox = utils.LabeledComboBox()
    self.global_panel_translation_target_language_combobox.addItems(LANGUAGE_DESCRIPTIONS)
    self.global_panel_translation_target_language_combobox.activated.connect(lambda: global_panel_translation_target_language_combobox_activated(self))
    left_panel_translation_panel.layout().addWidget(self.global_panel_translation_target_language_combobox, 1)

    self.global_panel_translation_engine_combobox = utils.LabeledComboBox()
    self.global_panel_translation_engine_combobox.setProperty('class', 'button')
    self.global_panel_translation_engine_combobox.addItems(['GoogleTranslator'])
    self.global_panel_translation_engine_combobox.activated.connect(lambda: global_panel_translation_engine_combobox_activated(self))
    left_panel_translation_panel.layout().addWidget(self.global_panel_translation_engine_combobox)

    self.global_panel_translation_tabwidget = QStackedWidget()

    self.global_panel_translation_googletranslator_widget = GoogleTranslatorPanel()
    self.global_panel_translation_googletranslator_widget.translation_started.connect(lambda: global_panel_translation_start_translation_progress_start(self))
    self.global_panel_translation_googletranslator_widget.translation_progress.connect(lambda value: global_panel_translation_start_translation_progress_update(self, value))
    self.global_panel_translation_googletranslator_widget.translation_finished.connect(lambda: global_panel_translation_start_translation_progress_finish(self))
    self.global_panel_translation_tabwidget.addWidget(self.global_panel_translation_googletranslator_widget)

    left_panel_translation_panel.layout().addWidget(self.global_panel_translation_tabwidget, 1)

    bottom_line = QHBoxLayout()
    bottom_line.setContentsMargins(0, 0, 0, 0)
    bottom_line.setSpacing(0)
    left_panel_translation_panel.layout().addLayout(bottom_line)

    def global_panel_translation_start_translation_progress_start(self):
        self.global_panel_translation_start_translation_progress.setVisible(True)
        self.global_panel_translation_start_translation_progress.setValue(0)
        self.global_panel_translation_start_translation_progress.setMaximum(100)
        self.global_panel_translation_start_translation_button.setVisible(False)
        self.global_panel_translation_invert_translation_button.setVisible(False)
    
    def global_panel_translation_start_translation_progress_update(self, value):
        self.global_panel_translation_start_translation_progress.setValue(value)

    def global_panel_translation_start_translation_progress_finish(self):
        self.global_panel_translation_start_translation_progress.setVisible(False)
        self.global_panel_translation_start_translation_button.setVisible(True)
        self.global_panel_translation_invert_translation_button.setVisible(True)

    self.global_panel_translation_invert_translation_button = QPushButton()
    self.global_panel_translation_invert_translation_button.setProperty('class', 'secondary')
    self.global_panel_translation_invert_translation_button.clicked.connect(lambda: global_panel_translation_invert_translation_button_clicked(self))
    bottom_line.addWidget(self.global_panel_translation_invert_translation_button, 0, Qt.AlignLeft)

    self.global_panel_translation_start_translation_progress = QProgressBar()
    self.global_panel_translation_start_translation_progress.setVisible(False)
    self.global_panel_translation_start_translation_progress.setProperty('class', 'secondary')
    bottom_line.addWidget(self.global_panel_translation_start_translation_progress)

    self.global_panel_translation_start_translation_button = QPushButton()
    self.global_panel_translation_start_translation_button.setProperty('class', 'secondary')
    self.global_panel_translation_start_translation_button.clicked.connect(lambda: global_panel_translation_start_translation_button_clicked(self))
    bottom_line.addWidget(self.global_panel_translation_start_translation_button, 0, Qt.AlignRight)

    update(self)

    
def show(self):
    update(self)
    

def update(self):    
    selected_language_name = INVERTED_LANGUAGES[session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-US')]
    self.global_panel_translation_target_language_combobox.setCurrentText(selected_language_name)
    self.global_panel_translation_show_translations_button.setChecked(session.CONFIG['translation'].get('engine_options', {}).get('show_translations', False))

    global_panel_translation_tabwidget_update(self)


def global_panel_translation_tabwidget_update(self):
    for widget in self.global_panel_translation_tabwidget.findChildren(QWidget):
        if widget.property('translation_engine') == self.global_panel_translation_engine_combobox.currentText():
            self.global_panel_translation_tabwidget.setCurrentWidget(widget)
            widget.update_callback()
            break


def global_panel_translation_show_translations_button_clicked(self):
    if not 'engine_options' in session.CONFIG['translation']:
        session.CONFIG['translation']['engine_options'] = {}
    session.CONFIG['translation']['engine_options']['show_translations'] = self.global_panel_translation_show_translations_button.isChecked()
    self.timeline_widget.update()


def global_panel_translation_target_language_combobox_activated(self):
    if not 'engine_options' in session.CONFIG['translation']:
        session.CONFIG['translation']['engine_options'] = {}
    session.CONFIG['translation']['engine_options']['target_language'] = session.LANGUAGE_DICT_LIST[self.global_panel_translation_target_language_combobox.currentText()]
    self.timeline_widget.update()
    

def global_panel_translation_engine_combobox_activated(self):
    session.CONFIG['translation']['engine'] = self.global_panel_translation_engine_combobox.currentText()
    global_panel_translation_tabwidget_update(self)


def global_panel_translation_invert_translation_button_clicked(self):
    confirm_dialog = utils.SimpleDialog(self, title=_('translation_panel.invert_translation'))
    label = QLabel(_('translation_panel.invert_translation_text'))
    confirm_dialog.content.layout().addWidget(label)
    confirm_dialog.exec()
    confirm_translation = bool(confirm_dialog.result() == 1)
    if confirm_translation:
        target_language = session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-US')
        original_language = session.SUBTITLE.get('language', 'en-us')
        for segment in session.SUBTITLE['segments']:
            original_text = str(segment['text'])
            translated_text = str(segment['translations'][target_language])
            segment['text'] = translated_text
            if not 'translations' in segment:
                segment['translations'] = {}
            segment['translations'][original_language] = original_text
        
        session.SUBTITLE['language'] = target_language
        if not 'translations' in session.SUBTITLE:
            session.SUBTITLE['translations'] = {}
        session.CONFIG['translation']['engine_options']['target_language'] = original_language

        update(self)
        
        self.timeline_widget.update()


def global_panel_translation_start_translation_button_clicked(self):
    confirm_translation = False
    if session.SUBTITLE['segments']:
        confirm_dialog = utils.SimpleDialog(self, title=_('translation_panel.start_translation'))
        label = QLabel(_('translation_panel.start_translation_text'))
        confirm_dialog.content.layout().addWidget(label)
        confirm_dialog.exec()
        confirm_translation = bool(confirm_dialog.result() == 1)

    if confirm_translation:
        self.timeline_widget.update()
        for widget in self.global_panel_translation_tabwidget.findChildren(QWidget):
            if widget.property('translation_engine') == self.global_panel_translation_engine_combobox.currentText():
                widget.translate_process_callback()
                break


def hide(self):
    pass

    
def translate(self):    
    self.global_panel_translation_start_translation_button.setText(_('translation_panel.start_translation'))
    self.global_panel_translation_target_language_combobox.setLabel(_('translation_panel.target_language'))
    self.global_panel_translation_target_language_combobox.setToolTip(_('translation_panel.target_language'))
    self.global_panel_translation_engine_combobox.setLabel(_('translation_panel.engine'))
    self.global_panel_translation_show_translations_button.setText(_('translation_panel.show_translations'))
    self.global_panel_translation_show_translations_button.setToolTip(_('translation_panel.show_translations'))
    self.global_panel_translation_invert_translation_button.setText(_('translation_panel.invert_translation'))
    self.global_panel_translation_invert_translation_button.setToolTip(_('translation_panel.invert_translation'))
    for widget in self.global_panel_translation_tabwidget.findChildren(QWidget):
        if 'translate_callback' in dir(widget):
            widget.translate_callback()
