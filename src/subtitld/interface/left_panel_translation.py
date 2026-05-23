import os
from deep_translator import GoogleTranslator
import subprocess

from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel, QCheckBox, QStackedWidget, QHBoxLayout, QProgressBar, QPushButton, QRadioButton, QButtonGroup, QLineEdit
from PySide6.QtCore import Qt, QThread, Signal

from subtitld.interface import left_panel
from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import history
from subtitld.modules import utils as modules_utils

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
                    target_language = session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us')
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
        
    def translate_process(widget, segments_list=None):
        if segments_list is None:
            segments_list = session.SUBTITLE['segments']
        widget.translate_thread.sentences_list = segments_list
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

    self.translation_scope_box = QWidget()
    self.translation_scope_box.setLayout(QHBoxLayout())
    self.translation_scope_box.layout().setContentsMargins(0, 0, 0, 0)
    self.translation_scope_box.layout().setSpacing(8)
    left_panel_translation_panel.layout().addWidget(self.translation_scope_box)

    self.translation_scope_label = QLabel()
    self.translation_scope_label.setProperty('class', 'widget_label')
    self.translation_scope_box.layout().addWidget(self.translation_scope_label)

    self.translation_scope_group = QButtonGroup(self)
    self.translation_scope_all = QRadioButton()
    self.translation_scope_selected = QRadioButton()
    self.translation_scope_range = QRadioButton()
    self.translation_scope_all.setChecked(True)
    for btn in (self.translation_scope_all, self.translation_scope_selected, self.translation_scope_range):
        self.translation_scope_group.addButton(btn)
        self.translation_scope_box.layout().addWidget(btn)
    self.translation_scope_box.layout().addStretch()

    self.translation_scope_range_box = QWidget()
    self.translation_scope_range_box.setLayout(QHBoxLayout())
    self.translation_scope_range_box.layout().setContentsMargins(0, 0, 0, 0)
    self.translation_scope_range_box.layout().setSpacing(6)
    self.translation_scope_range_box.setVisible(False)
    left_panel_translation_panel.layout().addWidget(self.translation_scope_range_box)

    self.translation_scope_range_from_label = QLabel()
    self.translation_scope_range_box.layout().addWidget(self.translation_scope_range_from_label)
    self.translation_scope_range_from = QLineEdit()
    self.translation_scope_range_from.setPlaceholderText('00:00:00.000')
    self.translation_scope_range_box.layout().addWidget(self.translation_scope_range_from, 1)

    self.translation_scope_range_to_label = QLabel()
    self.translation_scope_range_box.layout().addWidget(self.translation_scope_range_to_label)
    self.translation_scope_range_to = QLineEdit()
    self.translation_scope_range_to.setPlaceholderText('00:00:00.000')
    self.translation_scope_range_box.layout().addWidget(self.translation_scope_range_to, 1)

    self.translation_scope_range.toggled.connect(lambda checked: self.translation_scope_range_box.setVisible(checked))

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
        update(self)

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
    selected_language_name = INVERTED_LANGUAGES[session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us')]
    self.global_panel_translation_target_language_combobox.setCurrentText(selected_language_name)
    self.global_panel_translation_show_translations_button.setChecked(session.CONFIG['translation'].get('engine_options', {}).get('show_translations', False))

    target_language = session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us')
    has_translations = any(
        isinstance(seg.get('translations'), dict) and seg['translations'].get(target_language)
        for seg in session.SUBTITLE.get('segments', []) or []
    )
    self.global_panel_translation_invert_translation_button.setVisible(has_translations)

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
        history.history_append()
        target_language = session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us')
        original_language = session.SUBTITLE.get('language', 'en-us')
        # Lock `source_language` to the pre-invert language *before* we
        # flip `SUBTITLE['language']`. Legacy projects (transcribed under
        # a Subtitld version that didn't write `source_language`) would
        # otherwise lose the audio-language reference forever once
        # `language` swaps to the translation target. clone_ref reads
        # this field to pick the audio-matching ref_text for clone-
        # capable TTS addons (qwen3-clone, xtts-clone, f5-clone).
        if not session.SUBTITLE.get('source_language'):
            session.SUBTITLE['source_language'] = original_language
        for segment in session.SUBTITLE['segments']:
            translations = segment.get('translations') if isinstance(segment.get('translations'), dict) else None
            if not translations or not translations.get(target_language):
                continue
            original_text = str(segment['text'])
            translated_text = str(translations[target_language])
            segment['text'] = translated_text
            segment.setdefault('translations', {})[original_language] = original_text

        session.SUBTITLE['language'] = target_language
        if not 'translations' in session.SUBTITLE:
            session.SUBTITLE['translations'] = {}
        session.CONFIG['translation']['engine_options']['target_language'] = original_language

        session.set_unsaved(True)
        update(self)

        self.timeline_widget.update()


def _parse_timecode_input(text):
    """Parse a 'HH:MM:SS.mmm' / 'MM:SS.mmm' / 'SS.mmm' input into seconds.
    Returns None if it can't be parsed."""
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        parts = text.split(':')
        seconds = float(parts[-1])
        if len(parts) >= 2:
            seconds += int(parts[-2]) * 60
        if len(parts) >= 3:
            seconds += int(parts[-3]) * 3600
        return seconds
    except (TypeError, ValueError):
        return None


def _scoped_segments(self):
    """Return the segment subset selected by the translation scope radios."""
    segments = session.SUBTITLE.get('segments', []) or []
    if self.translation_scope_selected.isChecked():
        sel = session.SUBTITLE.get('selected')
        return [sel] if sel else []
    if self.translation_scope_range.isChecked():
        rng_from = _parse_timecode_input(self.translation_scope_range_from.text())
        rng_to = _parse_timecode_input(self.translation_scope_range_to.text())
        if rng_from is None:
            rng_from = 0.0
        if rng_to is None:
            rng_to = float('inf')
        return [s for s in segments if s.get('end', 0) > rng_from and s.get('start', 0) < rng_to]
    return list(segments)


def global_panel_translation_start_translation_button_clicked(self):
    scoped = _scoped_segments(self)
    if not scoped:
        return

    confirm_translation = False
    if scoped:
        confirm_dialog = utils.SimpleDialog(self, title=_('translation_panel.start_translation'))
        label = QLabel(_('translation_panel.start_translation_text'))
        confirm_dialog.content.layout().addWidget(label)
        confirm_dialog.exec()
        confirm_translation = bool(confirm_dialog.result() == 1)

    if confirm_translation:
        if not self.global_panel_translation_show_translations_button.isChecked():
            self.global_panel_translation_show_translations_button.setChecked(True)
            global_panel_translation_show_translations_button_clicked(self)
        self.timeline_widget.update()
        for widget in self.global_panel_translation_tabwidget.findChildren(QWidget):
            if widget.property('translation_engine') == self.global_panel_translation_engine_combobox.currentText():
                widget.translate_process_callback(scoped)
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
    self.translation_scope_label.setText(_('panel_scope.label'))
    self.translation_scope_all.setText(_('panel_scope.all'))
    self.translation_scope_selected.setText(_('panel_scope.selected'))
    self.translation_scope_range.setText(_('panel_scope.range'))
    self.translation_scope_range_from_label.setText(_('panel_scope.from'))
    self.translation_scope_range_to_label.setText(_('panel_scope.to'))
    for widget in self.global_panel_translation_tabwidget.findChildren(QWidget):
        if 'translate_callback' in dir(widget):
            widget.translate_callback()
