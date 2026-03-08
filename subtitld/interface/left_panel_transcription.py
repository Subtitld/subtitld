import os
import json

from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel, QHBoxLayout, QPushButton, QLineEdit, QSizePolicy, QStackedWidget, QProgressBar
from PySide6.QtCore import Qt, QThread, Signal

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.interface import utils
from subtitld.modules import session

from vosk import Model, KaldiRecognizer
import requests
import zipfile
import wave
import subprocess
import re
import shutil
import assemblyai as aai

LANGUAGE_DESCRIPTIONS = session.LANGUAGE_DICT_LIST.keys()

VOSK_CONFIG = {
  "en": [
    {
      "name": "vosk-model-small-en-us-0.15",
      "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
      "size": "40M",
      "license": "Apache 2.0",
      "description": "Small English (US) model. Lightweight, low memory usage, suitable for mobile and embedded/offline applications."
    },
    {
      "name": "vosk-model-en-us-0.22",
      "url": "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip",
      "size": "1.8G",
      "license": "Apache 2.0",
      "description": "Full-size English (US) model. Higher accuracy, recommended for server or desktop environments."
    },
    {
      "name": "vosk-model-en-us-0.22-lgraph",
      "url": "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22-lgraph.zip",
      "size": "128M",
      "license": "Apache 2.0",
      "description": "English (US) model with compact language graph. Balanced size and accuracy."
    },
    {
      "name": "vosk-model-en-us-0.42-gigaspeech",
      "url": "https://alphacephei.com/vosk/models/vosk-model-en-us-0.42-gigaspeech.zip",
      "size": "2.3G",
      "license": "Apache 2.0",
      "description": "Large English model trained on GigaSpeech dataset. Improved accuracy for diverse speech."
    }
  ],
  "pt": [
    {
      "name": "vosk-model-small-pt-0.3",
      "url": "https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip",
      "size": "31M",
      "license": "Apache 2.0",
      "description": "Small Portuguese model. Lightweight and suitable for offline/mobile applications."
    },
    {
      "name": "vosk-model-pt-fb-v0.1.1-20220516_2113",
      "url": "https://alphacephei.com/vosk/models/vosk-model-pt-fb-v0.1.1-20220516_2113.zip",
      "size": "1.6G",
      "license": "GPLv3.0",
      "description": "Full-size Portuguese model (Facebook training). Higher accuracy, requires more memory."
    }
  ],
  "es": [
    {
      "name": "vosk-model-small-es-0.42",
      "url": "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip",
      "size": "39M",
      "license": "Apache 2.0",
      "description": "Small Spanish model. Lightweight, optimized for embedded and offline usage."
    },
    {
      "name": "vosk-model-es-0.42",
      "url": "https://alphacephei.com/vosk/models/vosk-model-es-0.42.zip",
      "size": "1.4G",
      "license": "Apache 2.0",
      "description": "Full-size Spanish model. Higher recognition accuracy for desktop/server usage."
    }
  ],
  "fr": [
    {
      "name": "vosk-model-small-fr-0.22",
      "url": "https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip",
      "size": "41M",
      "license": "Apache 2.0",
      "description": "Small French model. Lightweight and efficient for offline/mobile systems."
    },
    {
      "name": "vosk-model-fr-0.22",
      "url": "https://alphacephei.com/vosk/models/vosk-model-fr-0.22.zip",
      "size": "1.4G",
      "license": "Apache 2.0",
      "description": "Full-size French model. Improved accuracy, recommended for powerful systems."
    }
  ],
  "de": [
    {
      "name": "vosk-model-small-de-0.15",
      "url": "https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip",
      "size": "45M",
      "license": "Apache 2.0",
      "description": "Small German model. Suitable for low-resource and embedded applications."
    },
    {
      "name": "vosk-model-de-0.21",
      "url": "https://alphacephei.com/vosk/models/vosk-model-de-0.21.zip",
      "size": "1.9G",
      "license": "Apache 2.0",
      "description": "Full-size German model. Higher accuracy for server/desktop environments."
    }
  ],
  "it": [
    {
      "name": "vosk-model-small-it-0.22",
      "url": "https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip",
      "size": "48M",
      "license": "Apache 2.0",
      "description": "Small Italian model. Lightweight and optimized for offline usage."
    },
    {
      "name": "vosk-model-it-0.22",
      "url": "https://alphacephei.com/vosk/models/vosk-model-it-0.22.zip",
      "size": "1.2G",
      "license": "Apache 2.0",
      "description": "Full-size Italian model. Better accuracy for production/server applications."
    }
  ],
  "ru": [
    {
      "name": "vosk-model-small-ru-0.22",
      "url": "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip",
      "size": "45M",
      "license": "Apache 2.0",
      "description": "Small Russian model. Lightweight and suitable for offline systems."
    },
    {
      "name": "vosk-model-ru-0.42",
      "url": "https://alphacephei.com/vosk/models/vosk-model-ru-0.42.zip",
      "size": "1.8G",
      "license": "Apache 2.0",
      "description": "Full-size Russian model. Higher accuracy for complex speech recognition tasks."
    }
  ],
  "zh": [
    {
      "name": "vosk-model-small-cn-0.22",
      "url": "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip",
      "size": "42M",
      "license": "Apache 2.0",
      "description": "Small Chinese model. Compact and suitable for embedded/offline use."
    },
    {
      "name": "vosk-model-cn-0.22",
      "url": "https://alphacephei.com/vosk/models/vosk-model-cn-0.22.zip",
      "size": "1.3G",
      "license": "Apache 2.0",
      "description": "Full-size Chinese model. Higher recognition accuracy for server environments."
    }
  ],
  "ja": [
    {
      "name": "vosk-model-small-ja-0.22",
      "url": "https://alphacephei.com/vosk/models/vosk-model-small-ja-0.22.zip",
      "size": "48M",
      "license": "Apache 2.0",
      "description": "Small Japanese model. Lightweight for offline/mobile applications."
    },
    {
      "name": "vosk-model-ja-0.22",
      "url": "https://alphacephei.com/vosk/models/vosk-model-ja-0.22.zip",
      "size": "1Gb",
      "license": "Apache 2.0",
      "description": "Full-size Japanese model. Higher accuracy for desktop/server usage."
    }
  ]
}


class VoskPanel(QWidget):
    transcript_started = Signal()
    transcript_progress = Signal(int)
    transcript_finished = Signal()
    def __init__(widget, parent=None):
        super().__init__(parent=None)
        widget.parent = parent
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 0)
        widget.layout().setSpacing(10)
        widget.setProperty('transcription_engine', 'Vosk')
        widget.setProperty('class', 'transparent_panel')

        widget.selected_model = False

        widget.model_line = QWidget()
        widget.model_line.setLayout(QHBoxLayout())
        widget.model_line.layout().setContentsMargins(0, 0, 0, 0)
        widget.model_line.layout().setSpacing(5)
        widget.layout().addWidget(widget.model_line) 

        widget.model_combobox = utils.LabeledComboBox()
        widget.model_combobox.setObjectName('global_panel_transcription_vosk_transcription_model_combobox')
        widget.model_combobox.activated.connect(lambda: widget.model_combobox_activated())
        widget.model_line.layout().addWidget(widget.model_combobox, 1)

        widget.download_model_button = QPushButton()
        widget.download_model_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        widget.download_model_button.setObjectName('global_panel_transcription_vosk_transcription_download_model_button')
        widget.download_model_button.clicked.connect(lambda: widget.download_model_button_clicked())
        widget.model_combobox.bottom_line.addWidget(widget.download_model_button)

        widget.update_model_button = QPushButton()
        widget.update_model_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        widget.update_model_button.setObjectName('global_panel_transcription_vosk_transcription_update_model_button')
        widget.update_model_button.clicked.connect(lambda: widget.update_model_button_clicked())
        widget.model_combobox.bottom_line.addWidget(widget.update_model_button)

        widget.remove_model_button = QPushButton()
        widget.remove_model_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        widget.remove_model_button.setObjectName('global_panel_transcription_vosk_transcription_remove_model_button')
        widget.remove_model_button.setProperty('class', 'danger')
        widget.remove_model_button.clicked.connect(lambda: widget.remove_model_button_clicked())
        widget.model_combobox.bottom_line.addWidget(widget.remove_model_button)

        widget.no_model_available_label = QLabel()
        widget.layout().addWidget(widget.no_model_available_label)

        widget.details_line = QHBoxLayout()
        widget.details_line.setContentsMargins(0, 0, 0, 0)
        widget.details_line.setSpacing(10)

        widget.description_label = QLabel()
        widget.description_label.setWordWrap(True)
        widget.details_line.addWidget(widget.description_label, 1, Qt.AlignLeft | Qt.AlignTop)

        widget.license_label = utils.LabeledLabel()
        widget.details_line.addWidget(widget.license_label, 0, Qt.AlignTop)

        widget.size_label = utils.LabeledLabel()
        widget.details_line.addWidget(widget.size_label, 0, Qt.AlignTop)

        widget.layout().addLayout(widget.details_line)

        widget.layout().addStretch()

        class download_thread(QThread):
            response = Signal(float)
            url = None
            final_path = None
            def run(self):
                if self.url is not None and self.final_path is not None:
                    r = requests.get(self.url, stream=True)
                    total_size = int(r.headers.get('content-length', 0))
                    size_counter = 0
                    with open(self.final_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size = 1024 * 1024):
                            if chunk:
                                f.write(chunk)
                                size_counter += len(chunk)
                                self.response.emit(float(size_counter) / float(total_size))
                                                                  
                    with zipfile.ZipFile(self.final_path, 'r') as zip_ref:
                        zip_ref.extractall(session.PATH_SUBTITLD_DATA_MODELS)        
        
        def download_started():
            widget.model_combobox.setEnabled(False)
            widget.size_label.setLabel(_('transcription_panel.downloading'))

        def download_progress_changed(value):
            widget.size_label.setText(f'{widget.selected_model["size"]} ({int(value * 100)}%)')

        def download_finished():
            widget.size_label.setLabel(_('transcription_panel.size'))
            widget.model_combobox.setEnabled(True)
            widget.models_update()

        widget.download_thread = download_thread()
        widget.download_thread.response.connect(lambda value: download_progress_changed(value))
        widget.download_thread.started.connect(lambda: download_started())
        widget.download_thread.finished.connect(lambda: download_finished())

        class VoskThread(QThread):
            response = Signal(object)
            progress = Signal(int)
            model_path = None
            audio_file = None
            
            def run(self):
                if self.model_path and self.audio_file:
                    temp_audio_file = os.path.join(session.PATH_TEMP, f'vosk_transcribe_{os.path.basename(self.audio_file)}')

                    self.progress.emit(1)

                    subprocess.Popen(
                        [
                            session.FFMPEG_EXECUTABLE,
                            '-i', self.audio_file,
                            '-acodec', 'pcm_s16le',
                            '-ar', '16000',
                            '-ac', '1',
                            '-f', 'wav',
                            temp_audio_file
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, 
                        startupinfo=session.STARTUPINFO
                    ).wait()

                    self.progress.emit(8)

                    model = Model(self.model_path)

                    self.progress.emit(9)

                    wf = wave.open(temp_audio_file, "rb")

                    self.progress.emit(10)

                    rec = KaldiRecognizer(model, wf.getframerate())
                    rec.SetWords(True)

                    while True:
                        data = wf.readframes(4000)
                        if len(data) == 0:
                            break
                        if rec.AcceptWaveform(data):
                            result = rec.Result()
                            result = re.sub(r'(\d),(\d)', r'\1.\2', result)
                            result = json.loads(result)
                            self.progress.emit(10 + int((float(result['result'][0]['start'])/float(session.VIDEO.get('duration', 60.0))) * 90))
                            self.response.emit(result)

                    self.response.emit(rec.FinalResult())

        def translate_thread_response(response):
            if isinstance(response, dict) and all(k in response for k in ('result', 'text')):
                session.SUBTITLE['segments'].append({
                    'start': response['result'][0]['start'],
                    'end': response['result'][-1]['end'],
                    'text': response['text']
                })
                widget.window().timeline_widget.update()
                session.set_unsaved()

        widget.translate_thread = VoskThread()
        widget.translate_thread.response.connect(translate_thread_response)
        widget.translate_thread.started.connect(lambda: widget.transcript_started.emit())
        widget.translate_thread.progress.connect(lambda value: widget.transcript_progress.emit(value))        
        widget.translate_thread.finished.connect(lambda: widget.transcript_finished.emit())

        widget.update_callback = widget.update
        widget.transcript_callback = widget.transcript
        widget.translate_callback = widget.translate

    def update(widget):
        widget.model_line.setVisible(bool(session.SUBTITLE.get('language', 'en-us')[:2] in VOSK_CONFIG))
        widget.no_model_available_label.setVisible(not bool(session.SUBTITLE.get('language', 'en-us')[:2] in VOSK_CONFIG))
        widget.description_label.setVisible(bool(session.SUBTITLE.get('language', 'en-us')[:2] in VOSK_CONFIG))
        widget.license_label.setVisible(bool(session.SUBTITLE.get('language', 'en-us')[:2] in VOSK_CONFIG))
        widget.size_label.setVisible(bool(session.SUBTITLE.get('language', 'en-us')[:2] in VOSK_CONFIG))

        if widget.model_line.isVisible():
            list_of_available_models = [item['name'] for item in VOSK_CONFIG[session.SUBTITLE.get('language', 'en-us')[:2]]]
            widget.model_combobox.clear()
            widget.model_combobox.addItems(list_of_available_models)

            selected_model_from_config = session.CONFIG['transcription'].get('engine_options', {}).get('Vosk', {}).get('selected_model', False)
            if selected_model_from_config:
                widget.model_combobox.setCurrentText(selected_model_from_config)

        widget.models_update()

    def remove_model_button_clicked(widget):
        confirm_dialog = utils.SimpleDialog(widget, title=_('transcription_panel.remove_model_confirm'))
        label = QLabel(_('transcription_panel.remove_model_confirm_text'))
        confirm_dialog.content.layout().addWidget(label)
        confirm_dialog.exec()
        if confirm_dialog.result() == 1:
            widget.selected_model = {}
            for model in VOSK_CONFIG[session.SUBTITLE.get('language', 'en-us')[:2]]:
                if model['name'] == widget.model_combobox.currentText():
                    widget.selected_model = model
                    break

            model_path = os.path.join(session.PATH_SUBTITLD_DATA_MODELS, widget.selected_model['name'])

            if os.path.isdir(model_path):
                shutil.rmtree(model_path)

            widget.models_update()

    def models_update(widget):
        if widget.model_line.isVisible():
            widget.selected_model = {}
            for model in VOSK_CONFIG[session.SUBTITLE.get('language', 'en-us')[:2]]:
                if model['name'] == widget.model_combobox.currentText():
                    widget.selected_model = model
                    break

            widget.download_model_button.setVisible(not os.path.isdir(os.path.join(session.PATH_SUBTITLD_DATA_MODELS, widget.selected_model['name'])))
            widget.update_model_button.setVisible(os.path.isdir(os.path.join(session.PATH_SUBTITLD_DATA_MODELS, widget.selected_model['name'])))
            widget.remove_model_button.setVisible(os.path.isdir(os.path.join(session.PATH_SUBTITLD_DATA_MODELS, widget.selected_model['name'])))
            widget.description_label.setText(widget.selected_model['description'])
            widget.license_label.setText(widget.selected_model['license'])
            widget.size_label.setText(widget.selected_model['size'])

    def model_combobox_activated(widget):
        if not 'engine_options' in session.CONFIG['transcription']:
            session.CONFIG['transcription']['engine_options'] = {}
        if not 'Vosk' in session.CONFIG['transcription']['engine_options']:
            session.CONFIG['transcription']['engine_options']['Vosk'] = {}

        session.CONFIG['transcription']['engine_options']['Vosk']['selected_model'] = widget.model_combobox.currentText()

        widget.models_update()

    def download_model_button_clicked(widget):
        widget.selected_model = {}
        for model in VOSK_CONFIG[session.SUBTITLE.get('language', 'en-us')[:2]]:
            if model['name'] == widget.model_combobox.currentText():
                widget.selected_model = model
                break

        widget.download_thread.url = widget.selected_model['url']
        widget.download_thread.final_path = os.path.join(session.PATH_TEMP, widget.selected_model['name'])
        widget.download_thread.start()

    def update_model_button_clicked(widget):
        widget.remove_model_button_clicked()
        widget.download_model_button_clicked()

    def transcript(widget):
        if widget.selected_model:
            widget.translate_thread.model_path = os.path.join(session.PATH_SUBTITLD_DATA_MODELS, widget.selected_model['name'])
            widget.translate_thread.audio_file = session.VIDEO['music_voice_separation']['vocals']
            widget.translate_thread.start()

    def translate(widget):
        widget.model_combobox.setLabel(_('transcription_panel.model'))
        widget.license_label.setLabel(_('transcription_panel.license'))
        widget.size_label.setLabel(_('transcription_panel.size'))
        widget.no_model_available_label.setText(_('transcription_panel.no_model_available'))


class AssemblyAIPanel(QWidget):
    transcript_started = Signal()
    transcript_progress = Signal(int)
    transcript_finished = Signal()
    def __init__(widget, parent=None):
        super().__init__(parent=None)
        widget.parent = parent
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 0)
        widget.layout().setSpacing(10)
        widget.setProperty('transcription_engine', 'AssemblyAI')
        widget.setProperty('class', 'transparent_panel')

        widget.api_key = utils.LabeledLineEdit()
        widget.api_key.setObjectName('global_panel_transcription_assemblyai_transcription_api_key')
        widget.api_key.lineedit.setEchoMode(QLineEdit.Password)
        widget.api_key.editingFinished.connect(lambda value: widget.api_key_activated(value))
        widget.layout().addWidget(widget.api_key, 1)

        widget.layout().addStretch()

        class AssemblyAIThread(QThread):
            response = Signal(list)
            response_error = Signal(str)
            progress = Signal(int)
            audio_file = None
            
            def run(self):
                api_key = session.CONFIG['transcription'].get('engine_options', {}).get('AssemblyAI', {}).get('api_key', False)
                if api_key and self.audio_file:
                    try:
                        temp_audio_file = os.path.join(session.PATH_TEMP, f'vosk_transcribe_{os.path.basename(self.audio_file)}.opus')
                        subprocess.Popen(
                            [
                                session.FFMPEG_EXECUTABLE,
                                '-i', self.audio_file,
                                '-c:a', 'libopus',
                                '-b:a', '24k',
                                '-vbr', 'on',
                                '-compression_level', '10',
                                '-application', 'voip',
                                '-ac', '1',
                                '-ar', '48000',
                                temp_audio_file.replace('.wav', '.opus')
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            startupinfo=session.STARTUPINFO
                        ).wait()

                        self.progress.emit(25)
                
                        aai.settings.api_key = api_key
                        
                        assemblyai_config = aai.TranscriptionConfig(
                            speaker_labels=True,
                            language_code=session.SUBTITLE.get('language', 'en-us')[:2],
                            punctuate=True,
                        )

                        transcriber = aai.Transcriber()
                        transcript = transcriber.transcribe(
                            f"{temp_audio_file}",
                            config=assemblyai_config
                        )

                        segments = []

                        for sentence in transcript.get_sentences():
                            segments.append({
                                'start': float(sentence.start / 1000),
                                'end': float(sentence.end / 1000),
                                'text': sentence.text,
                                'speaker': sentence.speaker
                            })

                        self.response.emit(segments)

                        self.progress.emit(99)

                    except Exception as e:
                        self.response_error.emit(str(e))
                
        def translate_thread_response(response):
            if isinstance(response, list):
                session.SUBTITLE['segments'] = response
                widget.window().timeline_widget.update()
                session.set_unsaved()

        def translate_thread_error(response):
            error_dialog = utils.SimpleDialog(widget, title=_('transcription_panel.error'), text=response)
            label = QLabel(response)
            error_dialog.content.layout().addWidget(label)
            error_dialog.reject_button.setVisible(False)
            error_dialog.exec()

        widget.translate_thread = AssemblyAIThread()
        widget.translate_thread.response.connect(translate_thread_response)
        widget.translate_thread.response_error.connect(lambda: translate_thread_error)
        widget.translate_thread.started.connect(lambda: widget.transcript_started.emit())
        widget.translate_thread.progress.connect(lambda value: widget.transcript_progress.emit(value))
        widget.translate_thread.finished.connect(lambda: widget.transcript_finished.emit())

        widget.update_callback = widget.update
        widget.transcript_callback = widget.transcript
        widget.translate_callback = widget.translate

    def update(widget):
        widget.api_key.setText(session.CONFIG['transcription'].get('engine_options', {}).get('AssemblyAI', {}).get('api_key', ''))
    
    def api_key_activated(widget, value):
        if not 'engine_options' in session.CONFIG['transcription']:
            session.CONFIG['transcription']['engine_options'] = {}
        if not 'AssemblyAI' in session.CONFIG['transcription']['engine_options']:
            session.CONFIG['transcription']['engine_options']['AssemblyAI'] = {}

        session.CONFIG['transcription']['engine_options']['AssemblyAI']['api_key'] = value


    def transcript(widget):
        if session.CONFIG['transcription'].get('engine_options', {}).get('AssemblyAI', {}).get('api_key', ''):
            widget.translate_thread.audio_file = session.VIDEO['music_voice_separation']['vocals']
            widget.translate_thread.start()
        else:
            error_dialog = utils.SimpleDialog(widget, title=_('transcription_panel.error'))
            label = QLabel(_('transcription_panel.no_api_key_error'))
            error_dialog.content.layout().addWidget(label)
            error_dialog.exec()

    def translate(widget):
        widget.api_key.setLabel(_('transcription_panel.api_key'))
        widget.api_key.lineedit.setPlaceholderText(_('transcription_panel.api_key'))
        widget.api_key.lineedit.setToolTip(_('transcription_panel.api_key'))


def load(self):
    tab_name = 'transcription'
    
    left_panel_transcription_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )

    self.global_panel_transcription_language_combobox = utils.LabeledComboBox()
    self.global_panel_transcription_language_combobox.addItems(LANGUAGE_DESCRIPTIONS)
    self.global_panel_transcription_language_combobox.activated.connect(lambda: global_panel_transcription_language_combobox_activated(self))
    left_panel_transcription_panel.layout().addWidget(self.global_panel_transcription_language_combobox, 1)

    self.global_panel_transcription_engine_combobox = utils.LabeledComboBox()
    self.global_panel_transcription_engine_combobox.setProperty('class', 'button')
    self.global_panel_transcription_engine_combobox.addItems(['Vosk', 'AssemblyAI'])
    self.global_panel_transcription_engine_combobox.activated.connect(lambda: global_panel_transcription_engine_combobox_activated(self))
    left_panel_transcription_panel.layout().addWidget(self.global_panel_transcription_engine_combobox)

    self.global_panel_transcription_tabwidget = QStackedWidget()

    self.global_panel_transcription_vosk_transcription_widget = VoskPanel()
    self.global_panel_transcription_vosk_transcription_widget.transcript_started.connect(lambda: global_panel_transcription_start_transcription_progress_start(self))
    self.global_panel_transcription_vosk_transcription_widget.transcript_progress.connect(lambda value: global_panel_transcription_start_transcription_progress_update(self, value))
    self.global_panel_transcription_vosk_transcription_widget.transcript_finished.connect(lambda: global_panel_transcription_start_transcription_progress_finish(self))
    self.global_panel_transcription_tabwidget.addWidget(self.global_panel_transcription_vosk_transcription_widget)

    self.global_panel_transcription_assemblyai_transcription_widget = AssemblyAIPanel()
    self.global_panel_transcription_assemblyai_transcription_widget.transcript_started.connect(lambda: global_panel_transcription_start_transcription_progress_start(self))
    self.global_panel_transcription_assemblyai_transcription_widget.transcript_progress.connect(lambda value: global_panel_transcription_start_transcription_progress_update(self, value))
    self.global_panel_transcription_assemblyai_transcription_widget.transcript_finished.connect(lambda: global_panel_transcription_start_transcription_progress_finish(self))
    self.global_panel_transcription_tabwidget.addWidget(self.global_panel_transcription_assemblyai_transcription_widget)

    left_panel_transcription_panel.layout().addWidget(self.global_panel_transcription_tabwidget, 1)

    bottom_line = QHBoxLayout()
    bottom_line.setContentsMargins(0, 0, 0, 0)
    bottom_line.setSpacing(0)
    left_panel_transcription_panel.layout().addLayout(bottom_line)

    def global_panel_transcription_start_transcription_progress_start(self):
        self.global_panel_transcription_start_transcription_progress.setVisible(True)
        self.global_panel_transcription_start_transcription_progress.setValue(0)
        self.global_panel_transcription_start_transcription_progress.setMaximum(100)
        self.global_panel_transcription_start_transcription_button.setVisible(False)
    
    def global_panel_transcription_start_transcription_progress_update(self, value):
        self.global_panel_transcription_start_transcription_progress.setValue(value)

    def global_panel_transcription_start_transcription_progress_finish(self):
        self.global_panel_transcription_start_transcription_progress.setVisible(False)
        self.global_panel_transcription_start_transcription_button.setVisible(True)

    self.global_panel_transcription_start_transcription_progress = QProgressBar()
    self.global_panel_transcription_start_transcription_progress.setVisible(False)
    self.global_panel_transcription_start_transcription_progress.setProperty('class', 'secondary')
    bottom_line.addWidget(self.global_panel_transcription_start_transcription_progress)

    self.global_panel_transcription_start_transcription_button = QPushButton()
    self.global_panel_transcription_start_transcription_button.setProperty('class', 'secondary')
    self.global_panel_transcription_start_transcription_button.clicked.connect(lambda: global_panel_transcription_start_transcription_button_clicked(self))
    bottom_line.addWidget(self.global_panel_transcription_start_transcription_button, 0, Qt.AlignRight)

    update(self)

    
def show(self):
    update(self)


def update(self):
    if not session.SUBTITLE.get('language', False):
        session.SUBTITLE['language'] = 'en-us'
    selected_language_name = 'English (United States)'
    for language_name, language_code in session.LANGUAGE_DICT_LIST.items():
        if language_code == session.SUBTITLE['language']:
            selected_language_name = language_name
            break
    self.global_panel_transcription_language_combobox.setCurrentText(selected_language_name)

    self.global_panel_transcription_engine_combobox.setCurrentText(session.CONFIG['transcription'].get('engine', 'Vosk'))

    global_panel_transcription_tabwidget_update(self)
    

def hide(self):
    pass


def global_panel_transcription_language_combobox_activated(self):
    session.SUBTITLE['language'] = session.LANGUAGE_DICT_LIST[self.global_panel_transcription_language_combobox.currentText()]
    global_panel_transcription_tabwidget_update(self)


def global_panel_transcription_engine_combobox_activated(self):
    session.CONFIG['transcription']['engine'] = self.global_panel_transcription_engine_combobox.currentText()
    global_panel_transcription_tabwidget_update(self)


def global_panel_transcription_start_transcription_button_clicked(self):
    confirm_transcript = False
    if session.SUBTITLE['segments']:
        confirm_dialog = utils.SimpleDialog(self, title=_('transcription_panel.start_transcription'))
        label = QLabel(_('transcription_panel.start_transcription_text'))
        confirm_dialog.content.layout().addWidget(label)
        confirm_dialog.exec()
        confirm_transcript = bool(confirm_dialog.result() == 1)

    if confirm_transcript or not session.SUBTITLE['segments']:
        session.SUBTITLE['segments'] = []
        self.timeline_widget.update()
        for widget in self.global_panel_transcription_tabwidget.findChildren(QWidget):
            if widget.property('transcription_engine') == self.global_panel_transcription_engine_combobox.currentText():
                widget.transcript_callback()
                break
               

def global_panel_transcription_tabwidget_update(self):
    for widget in self.global_panel_transcription_tabwidget.findChildren(QWidget):
        if widget.property('transcription_engine') == self.global_panel_transcription_engine_combobox.currentText():
            self.global_panel_transcription_tabwidget.setCurrentWidget(widget)
            widget.update_callback()
            break
    

def translate(self):    
    self.global_panel_transcription_language_combobox.setLabel(_('transcription_panel.language'))
    self.global_panel_transcription_start_transcription_button.setText(_('transcription_panel.start_transcription'))
    self.global_panel_transcription_engine_combobox.setLabel(_('transcription_panel.engine'))
    for widget in self.global_panel_transcription_tabwidget.findChildren(QWidget):
        if 'translate_callback' in dir(widget):
            widget.translate_callback()

    

    
