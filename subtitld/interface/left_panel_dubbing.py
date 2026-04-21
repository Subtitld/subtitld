import os
import secrets
import asyncio
import edge_tts

from PySide6.QtWidgets import QStackedWidget, QWidget, QVBoxLayout, QHBoxLayout, QSpinBox, QPushButton, QLabel, QLineEdit, QSizePolicy, QColorDialog, QComboBox, QCheckBox, QApplication
from PySide6.QtGui import QImage, QPixmap, QPainter, QPainterPath, QColor
from PySide6.QtCore import QThread, QObject, Signal, Qt, QSize

from subtitld.interface import utils
from subtitld.interface import left_panel
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import subtitles


class _EdgeTTSSignals(QObject):
    speech_ready = Signal(str, dict, str)   # (uid, output_file)
    speech_error = Signal(str, str)   # (uid, message)
    voices_updated = Signal()


class _EdgeTTSSpeechThread(QThread):
    speech_ready = Signal(str, dict, str)
    speech_error = Signal(str, str)

    def __init__(self, text_list):
        super().__init__()
        self.text_list = text_list

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        cache_dir = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'dubbing')
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)

        for subtitle in self.text_list:
            output_file = os.path.join(cache_dir, f'{subtitle["uid"]}.wav')
            try:
                loop.run_until_complete(self._generate(subtitle, output_file))
                self.speech_ready.emit(subtitle['uid'], subtitle, output_file)
            except Exception as e:
                self.speech_error.emit(subtitle['uid'], str(e))

        loop.close()

    async def _generate(self, subtitle, final_file):
        speaker = session.SPEAKERS.get(subtitle['speaker'], {}).get('dubbing', {})
        voice = subtitle.get('voice') or speaker.get('voice', '')
        communicate = edge_tts.Communicate(
            text=subtitle['text'],
            voice=voice,
            rate=f'{subtitle.get("rate", 0):+d}%',
            pitch=f'{subtitle.get("pitch", 0):+d}Hz',
        )
        await communicate.save(final_file)


class _EdgeTTSVoicesThread(QThread):
    voice_received = Signal(dict)

    def run(self):
        voices = asyncio.run(edge_tts.list_voices())
        for v in voices:
            self.voice_received.emit(v)


class EdgeTTSEngine:
    signals = _EdgeTTSSignals()
    _speech_threads = []
    _voices_thread = None

    @staticmethod
    def get_voices_list():
        if EdgeTTSEngine._voices_thread is not None:
            return

        session.CONFIG['dubbing'].setdefault('edge-tts', {}).setdefault('voices', {})

        def on_voice(voice):
            session.CONFIG['dubbing']['edge-tts']['voices'][voice['ShortName']] = voice

        def on_finished():
            EdgeTTSEngine._voices_thread = None
            EdgeTTSEngine.signals.voices_updated.emit()

        thread = _EdgeTTSVoicesThread()
        thread.voice_received.connect(on_voice)
        thread.finished.connect(on_finished)
        EdgeTTSEngine._voices_thread = thread
        thread.start()

    @staticmethod
    def generate_speeches(text_list):
        thread = _EdgeTTSSpeechThread(text_list)
        thread.speech_ready.connect(EdgeTTSEngine.signals.speech_ready)
        thread.speech_error.connect(EdgeTTSEngine.signals.speech_error)
        thread.finished.connect(lambda: EdgeTTSEngine._speech_threads.remove(thread))
        EdgeTTSEngine._speech_threads.append(thread)
        thread.start()

    @staticmethod
    def _on_speech_ready(uid, original_subtitle, file_path):
        for subtitle in session.SUBTITLE['segments']:
            if subtitle.get('start') == original_subtitle['start']:
                subtitle.setdefault('dubbing', []).insert(0, {
                    'engine': 'edge-tts',
                    'path': file_path,
                    'start': subtitle['start'],
                    'end': subtitle['end'],
                    'uid': uid,
                    'rate': original_subtitle.get('rate', 0),
                    'pitch': original_subtitle.get('pitch', 0),
                })
                break
        for window in QApplication.topLevelWidgets():
            preview = getattr(window, 'preview_panel_player', None)
            timeline_widget = getattr(window, 'timeline_widget', None)
            if preview is None and timeline_widget is None:
                continue
            if preview is not None:
                preview._audio_device.sync_subtitle_dubs(session.SUBTITLE['segments'])
            if timeline_widget is not None:
                timeline_widget.update()
            session.set_unsaved()
            break

    class speaker_panel(QWidget):
        def __init__(widget, parent=None):
            super().__init__(parent=None)
            widget.parent = parent
            widget.setLayout(QVBoxLayout())
            widget.layout().setContentsMargins(5, 5, 5, 5)
            widget.layout().setSpacing(5)
            widget.setProperty('dubbing_engine', 'edge-tts')
            widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)
            widget.setProperty('class', 'transparent_panel')

            if not 'edge-tts' in session.CONFIG['dubbing']:
                session.CONFIG['dubbing']['edge-tts'] = {}

            if not 'voices' in session.CONFIG['dubbing']['edge-tts'] or not session.CONFIG['dubbing']['edge-tts']['voices']:
                EdgeTTSEngine.get_voices_list()

            widget.voice_combobox = utils.LabeledComboBox()
            widget.voice_combobox.addItems(session.CONFIG['dubbing']['edge-tts']['voices'].keys())
            widget.voice_combobox.activated.connect(lambda: widget.voice_combobox_changed())
            widget.layout().addWidget(widget.voice_combobox)

            settings_line = QHBoxLayout()
            widget.voice_rate_label = QLabel()
            settings_line.addWidget(widget.voice_rate_label)
            widget.voice_rate = QSpinBox()
            widget.voice_rate.setMinimum(-100)
            widget.voice_rate.setMaximum(100)
            widget.voice_rate.setValue(0)
            widget.voice_rate.valueChanged.connect(lambda: widget.voice_rate_changed())
            settings_line.addWidget(widget.voice_rate)
            settings_line.addSpacing(10)

            widget.voice_pitch_label = QLabel()
            settings_line.addWidget(widget.voice_pitch_label)
            widget.voice_pitch = QSpinBox()
            widget.voice_pitch.setMinimum(-100)
            widget.voice_pitch.setMaximum(100)
            widget.voice_pitch.setValue(0)
            widget.voice_pitch.valueChanged.connect(lambda: widget.voice_pitch_changed())
            settings_line.addWidget(widget.voice_pitch)
            settings_line.addStretch()

            widget.layout().addLayout(settings_line)

            widget.generate_all_speeches_button = QPushButton()
            widget.generate_all_speeches_button.clicked.connect(lambda: widget.generate_all_speeches_button_clicked())
            widget.layout().addWidget(widget.generate_all_speeches_button, 0, Qt.AlignRight)

            EdgeTTSEngine.signals.voices_updated.connect(widget._refresh_voices)
            EdgeTTSEngine.signals.speech_error.connect(widget._on_speech_error)

            widget.update()

        def _refresh_voices(widget):
            current = widget.voice_combobox.currentText()
            widget.voice_combobox.clear()
            widget.voice_combobox.addItems(session.CONFIG['dubbing']['edge-tts']['voices'].keys())
            if current:
                widget.voice_combobox.setCurrentText(current)

        def _on_speech_error(widget, uid, message):
            print('edge-tts error:', uid, message)

        def voice_combobox_changed(widget):
            value = widget.voice_combobox.currentText()
            speaker_name = widget.property('speaker')
            if speaker_name and speaker_name in session.SPEAKERS:
                session.SPEAKERS[speaker_name]['dubbing']['voice'] = value

        def voice_rate_changed(widget):
            value = widget.voice_rate.value()
            speaker_name = widget.property('speaker')
            if speaker_name and speaker_name in session.SPEAKERS:
                session.SPEAKERS[speaker_name]['dubbing']['rate'] = value

        def voice_pitch_changed(widget):
            value = widget.voice_pitch.value()
            speaker_name = widget.property('speaker')
            if speaker_name and speaker_name in session.SPEAKERS:
                session.SPEAKERS[speaker_name]['dubbing']['pitch'] = value

        def generate_all_speeches_button_clicked(widget):
            speaker_name = widget.property('speaker')
            speaker_dubbing = session.SPEAKERS.get(speaker_name, {}).get('dubbing', {})
            speeches_to_generate = []
            for subtitle in session.SUBTITLE['segments']:
                if subtitle.get('speaker', 'A') == speaker_name:
                    overrides = subtitle.get('dubbing_options', {})
                    speeches_to_generate.append({
                        'uid': secrets.token_hex(4),
                        'text': subtitle['text'],
                        'speaker': speaker_name,
                        'start': subtitle['start'],
                        'end': subtitle['end'],
                        'voice': overrides.get('voice') or speaker_dubbing.get('voice', ''),
                        'rate': overrides.get('rate', speaker_dubbing.get('rate', 0)),
                        'pitch': overrides.get('pitch', speaker_dubbing.get('pitch', 0)),
                    })
            if speeches_to_generate:
                EdgeTTSEngine.generate_speeches(speeches_to_generate)

        def update(widget):
            speaker_name = widget.property('speaker')
            dubbing = session.SPEAKERS.get(speaker_name, {}).get('dubbing') if speaker_name else None
            if not isinstance(dubbing, dict):
                dubbing = {}

            widget.voice_combobox.combobox.blockSignals(True)
            widget.voice_combobox.setCurrentText(dubbing.get('voice', ''))
            widget.voice_combobox.combobox.blockSignals(False)

            widget.voice_rate.blockSignals(True)
            widget.voice_rate.setValue(int(dubbing.get('rate', 0) or 0))
            widget.voice_rate.blockSignals(False)

            widget.voice_pitch.blockSignals(True)
            widget.voice_pitch.setValue(int(dubbing.get('pitch', 0) or 0))
            widget.voice_pitch.blockSignals(False)

        def translate(widget):
            widget.voice_combobox.setLabel(_('subtitles_panel_widget_dubbing.voice'))
            widget.voice_rate_label.setText(_('subtitles_panel_widget_dubbing.rate'))
            widget.voice_pitch_label.setText(_('subtitles_panel_widget_dubbing.pitch'))
            widget.generate_all_speeches_button.setText(_('subtitles_panel_widget_dubbing.generate_all_speeches'))

    class dubbingPanel(QWidget):
        def __init__(widget, parent=None):
            super().__init__(parent=None)
            widget.parent = parent
            widget.setLayout(QVBoxLayout())
            widget.layout().setContentsMargins(0, 0, 0, 0)
            widget.layout().setSpacing(10)
            widget.setProperty('dubbing_engine', 'edge-tts')
            widget.setProperty('class', 'transparent_panel')

            widget.no_subtitle_label = QLabel()
            widget.no_subtitle_label.setWordWrap(True)
            widget.no_subtitle_label.setAlignment(Qt.AlignCenter)
            widget.layout().addWidget(widget.no_subtitle_label)

            widget.settings_container = QWidget()
            widget.settings_container.setLayout(QVBoxLayout())
            widget.settings_container.layout().setContentsMargins(0, 0, 0, 0)
            widget.settings_container.layout().setSpacing(5)
            widget.layout().addWidget(widget.settings_container)

            widget.voice_combobox = utils.LabeledComboBox()
            widget.voice_combobox.addItems(session.CONFIG.get('dubbing', {}).get('edge-tts', {}).get('voices', {}).keys())
            widget.voice_combobox.activated.connect(lambda: widget.voice_combobox_changed())
            widget.settings_container.layout().addWidget(widget.voice_combobox)

            settings_line = QHBoxLayout()
            widget.voice_rate_label = QLabel()
            settings_line.addWidget(widget.voice_rate_label)
            widget.voice_rate = QSpinBox()
            widget.voice_rate.setMinimum(-100)
            widget.voice_rate.setMaximum(100)
            widget.voice_rate.valueChanged.connect(lambda: widget.voice_rate_changed())
            settings_line.addWidget(widget.voice_rate)
            settings_line.addSpacing(10)

            widget.voice_pitch_label = QLabel()
            settings_line.addWidget(widget.voice_pitch_label)
            widget.voice_pitch = QSpinBox()
            widget.voice_pitch.setMinimum(-100)
            widget.voice_pitch.setMaximum(100)
            widget.voice_pitch.valueChanged.connect(lambda: widget.voice_pitch_changed())
            settings_line.addWidget(widget.voice_pitch)
            settings_line.addStretch()
            widget.settings_container.layout().addLayout(settings_line)

            widget.generate_speech_button = QPushButton()
            widget.generate_speech_button.clicked.connect(lambda: widget.generate_speech_button_clicked())
            widget.settings_container.layout().addWidget(widget.generate_speech_button, 0, Qt.AlignRight)

            EdgeTTSEngine.signals.voices_updated.connect(widget._refresh_voices)

            widget.update_callback = widget.update
            widget.translate_callback = widget.translate

        def _refresh_voices(widget):
            current = widget.voice_combobox.currentText()
            widget.voice_combobox.clear()
            widget.voice_combobox.addItems(session.CONFIG.get('dubbing', {}).get('edge-tts', {}).get('voices', {}).keys())
            if current:
                widget.voice_combobox.setCurrentText(current)

        def voice_combobox_changed(widget):
            selected = session.SUBTITLE.get('selected')
            if selected is None:
                return
            selected.setdefault('dubbing_options', {})['voice'] = widget.voice_combobox.currentText()
            session.set_unsaved()

        def voice_rate_changed(widget):
            selected = session.SUBTITLE.get('selected')
            if selected is None:
                return
            selected.setdefault('dubbing_options', {})['rate'] = widget.voice_rate.value()
            session.set_unsaved()

        def voice_pitch_changed(widget):
            selected = session.SUBTITLE.get('selected')
            if selected is None:
                return
            selected.setdefault('dubbing_options', {})['pitch'] = widget.voice_pitch.value()
            session.set_unsaved()

        def generate_speech_button_clicked(widget):
            selected = session.SUBTITLE.get('selected')
            if not selected:
                return
            speaker_name = selected.get('speaker', 'A')
            speaker_dubbing = session.SPEAKERS.get(speaker_name, {}).get('dubbing', {})
            overrides = selected.get('dubbing_options', {})
            EdgeTTSEngine.generate_speeches([{
                'uid': secrets.token_hex(4),
                'text': selected['text'],
                'speaker': speaker_name,
                'start': selected['start'],
                'end': selected['end'],
                'voice': overrides.get('voice') or speaker_dubbing.get('voice', ''),
                'rate': overrides.get('rate', speaker_dubbing.get('rate', 0)),
                'pitch': overrides.get('pitch', speaker_dubbing.get('pitch', 0)),
            }])

        def update(widget):
            selected = session.SUBTITLE.get('selected')
            if not selected:
                widget.no_subtitle_label.setVisible(True)
                widget.settings_container.setVisible(False)
                return
            widget.no_subtitle_label.setVisible(False)
            widget.settings_container.setVisible(True)

            speaker_dubbing = session.SPEAKERS.get(selected.get('speaker', 'A'), {}).get('dubbing', {})
            overrides = selected.get('dubbing_options', {})

            effective_voice = overrides.get('voice') or speaker_dubbing.get('voice', '')
            effective_rate = overrides.get('rate', speaker_dubbing.get('rate', 0))
            effective_pitch = overrides.get('pitch', speaker_dubbing.get('pitch', 0))

            widget.voice_combobox.combobox.blockSignals(True)
            widget.voice_combobox.setCurrentText(effective_voice)
            widget.voice_combobox.combobox.blockSignals(False)

            widget.voice_rate.blockSignals(True)
            widget.voice_rate.setValue(int(effective_rate or 0))
            widget.voice_rate.blockSignals(False)

            widget.voice_pitch.blockSignals(True)
            widget.voice_pitch.setValue(int(effective_pitch or 0))
            widget.voice_pitch.blockSignals(False)

        def translate(widget):
            widget.no_subtitle_label.setText(_('subtitles_panel_widget_dubbing.no_subtitle_selected'))
            widget.voice_combobox.setLabel(_('subtitles_panel_widget_dubbing.voice'))
            widget.voice_rate_label.setText(_('subtitles_panel_widget_dubbing.rate'))
            widget.voice_pitch_label.setText(_('subtitles_panel_widget_dubbing.pitch'))
            widget.generate_speech_button.setText(_('subtitles_panel_widget_dubbing.generate_speech'))


EdgeTTSEngine.signals.speech_ready.connect(EdgeTTSEngine._on_speech_ready)


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
    self.left_panel_dubbing_engine_combobox.activated.connect(lambda: left_panel_dubbing_engine_combobox_activated(self))
    left_panel_dubbing_panel.layout().addWidget(self.left_panel_dubbing_engine_combobox)

    self.global_panel_dubbing_tabwidget = QStackedWidget()
    self.global_panel_dubbing_tabwidget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)

    global_panel_dubbing_edgetts_engine = EdgeTTSEngine
    self.global_panel_dubbing_tabwidget.addWidget(global_panel_dubbing_edgetts_engine.dubbingPanel())
    self.left_panel_speakers_list_of_available_dubbing_engine['edge-tts'] = global_panel_dubbing_edgetts_engine
    self.left_panel_dubbing_engine_combobox.addItem('edge-tts')

    left_panel_dubbing_panel.layout().addWidget(self.global_panel_dubbing_tabwidget)
    left_panel_dubbing_panel.layout().addStretch()

    update(self)


def left_panel_dubbing_enable_checkbox_clicked(self):
    session.CONFIG['dubbing']['enabled'] = self.left_panel_dubbing_enable_checkbox.isChecked()


def left_panel_dubbing_engine_combobox_activated(self):
    session.CONFIG['dubbing']['selected_engine'] = self.left_panel_dubbing_engine_combobox.currentText()
    global_panel_dubbing_tabwidget_update(self)


def update(self):
    self.left_panel_dubbing_enable_checkbox.setChecked(session.CONFIG['dubbing'].get('enabled', False))
    self.left_panel_dubbing_engine_combobox.setCurrentText(session.CONFIG['dubbing'].get('selected_engine', 'edge-tts'))

    selected_engine = self.left_panel_dubbing_engine_combobox.currentText()
    for widget in self.global_panel_dubbing_tabwidget.findChildren(QWidget):
        if widget.property('dubbing_engine') == selected_engine and hasattr(widget, 'update_callback'):
            widget.update_callback()
            break


def translate(self):
    self.left_panel_dubbing_enable_checkbox.setText(_('subtitles_panel_widget_dubbing.enable_dubbing'))
    self.left_panel_dubbing_engine_combobox.setLabel(_('subtitles_panel_widget_dubbing.engine'))
    for widget in self.global_panel_dubbing_tabwidget.findChildren(QWidget):
        if widget.property('dubbing_engine') == self.left_panel_dubbing_engine_combobox.currentText():
            widget.translate_callback()
            break



def global_panel_dubbing_tabwidget_update(self):
    for widget in self.global_panel_dubbing_tabwidget.findChildren(QWidget):
        if widget.property('dubbing_engine') == self.left_panel_dubbing_engine_combobox.currentText():
            self.global_panel_dubbing_tabwidget.setCurrentWidget(widget)
            widget.update_callback()
            break
    