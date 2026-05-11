import os
import json

from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel, QHBoxLayout, QPushButton, QLineEdit, QSizePolicy, QStackedWidget, QProgressBar, QFileDialog
from PySide6.QtCore import Qt, QThread, Signal

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.interface import utils
from subtitld.modules import session
from subtitld.modules import file_io
from subtitld.modules import utils as modules_utils
from subtitld.modules import addons
from subtitld.modules.addons.provider import TASK_ASR_TRANSCRIBE
from subtitld.modules.session import LIST_OF_SUPPORTED_IMPORT_EXTENSIONS

_list_of_supported_import_extensions = []
for _exttype in LIST_OF_SUPPORTED_IMPORT_EXTENSIONS:
    for _ext in LIST_OF_SUPPORTED_IMPORT_EXTENSIONS[_exttype]['extensions']:
        _list_of_supported_import_extensions.append(_ext)


def _audio_source_for_transcription():
    """Best available audio path for transcription.

    Prefers the separated vocals (cleanest input) → falls back to the cached
    full-original FLAC → falls back to the raw video file. Useful when the
    separation thread hasn't finished yet."""
    separation = session.VIDEO.get('music_voice_separation') or {}
    vocals = separation.get('vocals')
    if vocals and os.path.isfile(vocals):
        return vocals

    video_path = session.VIDEO.get('filepath')
    if video_path:
        key = modules_utils.get_cache_key(video_path)
        if key:
            original_flac = os.path.join(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION, key + '_original.flac')
            if os.path.isfile(original_flac):
                return original_flac
        if os.path.isfile(video_path):
            return video_path

    return None

import subprocess
import assemblyai as aai

# Vosk used to be a built-in ASR provider here. It's now distributed as an
# external add-on (see https://github.com/Subtitld/addon-vosk) so the
# Subtitld binary stays lean — neither the `vosk` Python wheel nor the
# `~50 MB` to `~1.6 GB` model zips ship inside the app. Users who want
# offline transcription install the add-on via the AddonsPanel; it then
# appears in the engine combobox alongside AssemblyAI through the
# generic `_GenericASRPanel` path below.

LANGUAGE_DESCRIPTIONS = session.LANGUAGE_DICT_LIST.keys()


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
        widget.api_key.setObjectName('global_panel_import_assemblyai_transcription_api_key')
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
                for segment in session.SUBTITLE['segments']:
                    if not segment.get('speaker', 'A') in session.SPEAKERS:
                        session.SPEAKERS[segment.get('speaker', 'A')] = {
                            'image': None
                        }
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
            audio_file = _audio_source_for_transcription()
            if not audio_file:
                return
            widget.translate_thread.audio_file = audio_file
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


class _GenericASRPanel(QWidget):
    """Generic transcription panel for an arbitrary `ASRProvider`.

    Used for ASR add-ons discovered at runtime — handles `transcribe()`
    invocation and routes the provider's `partial`/`transcript_finished`/
    `error` signals back to the host UI. The built-in AssemblyAI keeps
    its bespoke panel (API-key field) above; this is the fallback when
    there's no engine-specific UI to render — including for the Vosk
    add-on, whose generic schema-driven UI is configured via the
    AddonsPanel rather than an embedded panel here.
    """

    transcript_started = Signal()
    transcript_progress = Signal(int)
    transcript_finished = Signal()

    def __init__(widget, provider, parent=None):
        super().__init__(parent=None)
        widget.parent = parent
        widget.provider = provider
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 0)
        widget.layout().setSpacing(10)
        widget.setProperty('transcription_engine', provider.id)
        widget.setProperty('class', 'transparent_panel')

        widget.info_label = QLabel()
        widget.info_label.setWordWrap(True)
        widget.info_label.setText(provider.display_name)
        widget.layout().addWidget(widget.info_label)

        widget.layout().addStretch()

        # Wire provider signals through to the host's progress UI.
        try:
            provider.transcript_started.connect(lambda: widget.transcript_started.emit())
        except Exception:
            pass
        try:
            provider.progress.connect(lambda v, _msg='': widget.transcript_progress.emit(int(v * 100)))
        except Exception:
            pass
        try:
            provider.transcript_finished.connect(lambda segments: widget._on_finished(segments))
        except Exception:
            pass
        try:
            provider.partial.connect(lambda seg: widget._on_partial(seg))
        except Exception:
            pass
        try:
            provider.error.connect(lambda msg: widget._on_error(msg))
        except Exception:
            pass

        widget.update_callback = widget.update
        widget.transcript_callback = widget.transcript
        widget.translate_callback = widget.translate

    def _on_partial(widget, segment):
        # Append directly so live updates show on the timeline.
        if not isinstance(segment, dict):
            return
        session.SUBTITLE.setdefault('segments', []).append({
            'start': float(segment.get('start', 0.0)),
            'end': float(segment.get('end', 0.0)),
            'text': segment.get('text', ''),
            'speaker': segment.get('speaker', 'A'),
        })
        speaker = segment.get('speaker', 'A')
        if speaker not in session.SPEAKERS:
            session.SPEAKERS[speaker] = {'image': None}
        try:
            widget.window().timeline_widget.update()
        except Exception:
            pass
        session.set_unsaved()

    def _on_finished(widget, segments):
        if isinstance(segments, list) and segments:
            session.SUBTITLE['segments'] = list(segments)
            for seg in segments:
                speaker = seg.get('speaker', 'A')
                if speaker not in session.SPEAKERS:
                    session.SPEAKERS[speaker] = {'image': None}
            try:
                widget.window().timeline_widget.update()
            except Exception:
                pass
            session.set_unsaved()
        widget.transcript_finished.emit()

    def _on_error(widget, message):
        try:
            error_dialog = utils.SimpleDialog(widget, title=_('transcription_panel.error'))
            label = QLabel(str(message))
            error_dialog.content.layout().addWidget(label)
            error_dialog.reject_button.setVisible(False)
            error_dialog.exec()
        except Exception:
            pass
        widget.transcript_finished.emit()

    def update(widget):
        pass

    def transcript(widget):
        audio_file = _audio_source_for_transcription()
        if not audio_file:
            return
        widget.transcript_started.emit()
        language = session.SUBTITLE.get('language', 'en-us')
        opts = session.CONFIG.get('transcription', {}).get('engine_options', {}).get(widget.provider.id, {})
        widget.provider.transcribe(audio_file, language, dict(opts) if isinstance(opts, dict) else {})

    def translate(widget):
        widget.info_label.setText(widget.provider.display_name)


# Built-in transcription provider IDs. Their panels are constructed once at
# load() time because they own expensive state (AssemblyAI API-key field) we
# can't afford to discard every time a user installs/removes an unrelated
# add-on. Vosk used to live here too — it's now an add-on; see
# `_populate_asr_addons` below.
_BUILTIN_ASR_IDS = {'assemblyai'}

# Index of the first add-on slot in the engine combobox / stacked widget.
# Equals the number of built-in entries seeded in `load()` (currently just
# AssemblyAI). Bumping this in lockstep with `_BUILTIN_ASR_IDS` keeps the
# rebuild logic correct without per-id arithmetic.
_BUILTIN_ASR_COUNT = 1


def _populate_asr_addons(self):
    """(Re)build only the add-on tail of the transcription engine combobox
    and stacked widget. Idempotent — safe to call after `providers_changed`.

    The combobox always starts with the built-in entry ('AssemblyAI');
    add-ons live AFTER `_BUILTIN_ASR_COUNT`, so we chop the tail and
    re-add. Using `removeItem` on the underlying QComboBox rather than
    `clear()` preserves the built-in entry and its selection state.
    """
    if not hasattr(self, 'global_panel_import_engine_combobox'):
        return  # `load()` hasn't finished yet

    combobox = self.global_panel_import_engine_combobox.combobox
    stack = self.global_panel_import_tabwidget

    previous_selection = combobox.currentText()
    combobox.blockSignals(True)
    try:
        # Drop combobox entries past the built-ins.
        while combobox.count() > _BUILTIN_ASR_COUNT:
            combobox.removeItem(combobox.count() - 1)
        # Drop add-on widgets in the stacked widget — also past the built-ins.
        while stack.count() > _BUILTIN_ASR_COUNT:
            widget = stack.widget(stack.count() - 1)
            stack.removeWidget(widget)
            widget.deleteLater()

        for provider in addons.get_manager().providers_for_task(TASK_ASR_TRANSCRIBE):
            if provider.id in _BUILTIN_ASR_IDS:
                continue
            panel = _GenericASRPanel(provider)
            panel.transcript_started.connect(lambda: global_panel_import_start_transcription_progress_start(self))
            panel.transcript_progress.connect(lambda value: global_panel_import_start_transcription_progress_update(self, value))
            panel.transcript_finished.connect(lambda: global_panel_import_start_transcription_progress_finish(self))
            stack.addWidget(panel)
            combobox.addItem(provider.id)

        if previous_selection:
            combobox.setCurrentText(previous_selection)
    finally:
        combobox.blockSignals(False)

    global_panel_import_tabwidget_update(self)


def load(self):
    tab_name = 'import'

    left_panel_import_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )

    self.global_subtitlesvideo_import_button = QPushButton()
    self.global_subtitlesvideo_import_button.setProperty('class', 'button')
    self.global_subtitlesvideo_import_button.clicked.connect(lambda: global_subtitlesvideo_import_button_clicked(self))
    left_panel_import_panel.layout().addWidget(self.global_subtitlesvideo_import_button)

    self.global_panel_import_language_combobox = utils.LabeledComboBox()
    self.global_panel_import_language_combobox.addItems(LANGUAGE_DESCRIPTIONS)
    self.global_panel_import_language_combobox.activated.connect(lambda: global_panel_import_language_combobox_activated(self))
    left_panel_import_panel.layout().addWidget(self.global_panel_import_language_combobox, 1)

    self.global_panel_import_engine_combobox = utils.LabeledComboBox()
    self.global_panel_import_engine_combobox.setProperty('class', 'button')
    self.global_panel_import_engine_combobox.activated.connect(lambda: global_panel_import_engine_combobox_activated(self))
    left_panel_import_panel.layout().addWidget(self.global_panel_import_engine_combobox)

    self.global_panel_import_tabwidget = QStackedWidget()

    # Built-in panel: AssemblyAI (rich UI with API-key field). Its
    # `transcription_engine` property uses the legacy 'AssemblyAI' label so
    # config files round-trip unchanged. (Vosk used to live here too —
    # it's now distributed as an add-on.)
    self.global_panel_import_assemblyai_transcription_widget = AssemblyAIPanel()
    self.global_panel_import_assemblyai_transcription_widget.transcript_started.connect(lambda: global_panel_import_start_transcription_progress_start(self))
    self.global_panel_import_assemblyai_transcription_widget.transcript_progress.connect(lambda value: global_panel_import_start_transcription_progress_update(self, value))
    self.global_panel_import_assemblyai_transcription_widget.transcript_finished.connect(lambda: global_panel_import_start_transcription_progress_finish(self))
    self.global_panel_import_tabwidget.addWidget(self.global_panel_import_assemblyai_transcription_widget)
    self.global_panel_import_engine_combobox.addItem('AssemblyAI')

    # Add-on ASR providers discovered at runtime. The AssemblyAI built-in is
    # NOT churned on rebuild because its panel carries the user's API key —
    # we'd lose it on every `providers_changed` emission. We only refresh
    # the add-on tail.
    _populate_asr_addons(self)

    # Wire add-on registry to the UI. A user installing/uninstalling an
    # ASR add-on at runtime triggers `providers_changed`; we rebuild the
    # add-on portion of the combobox + stacked widget without restarting
    # the app.
    addons.get_manager().providers_changed.connect(lambda: _populate_asr_addons(self))

    left_panel_import_panel.layout().addWidget(self.global_panel_import_tabwidget, 1)

    bottom_line = QHBoxLayout()
    bottom_line.setContentsMargins(0, 0, 0, 0)
    bottom_line.setSpacing(0)
    left_panel_import_panel.layout().addLayout(bottom_line)

    def global_panel_import_start_transcription_progress_start(self):
        self.global_panel_import_start_transcription_progress.setVisible(True)
        self.global_panel_import_start_transcription_progress.setValue(0)
        self.global_panel_import_start_transcription_progress.setMaximum(100)
        self.global_panel_import_start_transcription_button.setVisible(False)
    
    def global_panel_import_start_transcription_progress_update(self, value):
        self.global_panel_import_start_transcription_progress.setValue(value)

    def global_panel_import_start_transcription_progress_finish(self):
        self.global_panel_import_start_transcription_progress.setVisible(False)
        self.global_panel_import_start_transcription_button.setVisible(True)

    self.global_panel_import_start_transcription_progress = QProgressBar()
    self.global_panel_import_start_transcription_progress.setVisible(False)
    self.global_panel_import_start_transcription_progress.setProperty('class', 'secondary')
    bottom_line.addWidget(self.global_panel_import_start_transcription_progress)

    self.global_panel_import_start_transcription_button = QPushButton()
    self.global_panel_import_start_transcription_button.setProperty('class', 'secondary')
    self.global_panel_import_start_transcription_button.clicked.connect(lambda: global_panel_import_start_transcription_button_clicked(self))
    bottom_line.addWidget(self.global_panel_import_start_transcription_button, 0, Qt.AlignRight)

    update(self)

    
def show(self):
    update(self)


def update(self):
    if not session.SUBTITLE.get('language', False):
        session.SUBTITLE['language'] = session.CONFIG.get('transcription', {}).get('language', 'en-us')
    selected_language_name = 'English (United States)'
    for language_name, language_code in session.LANGUAGE_DICT_LIST.items():
        if language_code == session.SUBTITLE['language']:
            selected_language_name = language_name
            break
    self.global_panel_import_language_combobox.setCurrentText(selected_language_name)

    # Default ASR engine: AssemblyAI (the only built-in left after Vosk
    # was extracted). Existing user configs that store `'Vosk'` here will
    # silently fall through to AssemblyAI when the saved engine isn't in
    # the combobox — `setCurrentText` is a no-op for unknown text.
    self.global_panel_import_engine_combobox.setCurrentText(session.CONFIG['transcription'].get('engine', 'AssemblyAI'))

    global_panel_import_tabwidget_update(self)
    

def hide(self):
    pass


def global_panel_import_language_combobox_activated(self):
    chosen = session.LANGUAGE_DICT_LIST[self.global_panel_import_language_combobox.currentText()]
    session.SUBTITLE['language'] = chosen
    if not isinstance(session.CONFIG.get('transcription'), dict):
        session.CONFIG['transcription'] = {}
    session.CONFIG['transcription']['language'] = chosen
    global_panel_import_tabwidget_update(self)


def global_panel_import_engine_combobox_activated(self):
    session.CONFIG['transcription']['engine'] = self.global_panel_import_engine_combobox.currentText()
    global_panel_import_tabwidget_update(self)


def global_panel_import_start_transcription_button_clicked(self):
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
        for widget in self.global_panel_import_tabwidget.findChildren(QWidget):
            if widget.property('transcription_engine') == self.global_panel_import_engine_combobox.currentText():
                widget.transcript_callback()
                break
               

def global_panel_import_tabwidget_update(self):
    for widget in self.global_panel_import_tabwidget.findChildren(QWidget):
        if widget.property('transcription_engine') == self.global_panel_import_engine_combobox.currentText():
            self.global_panel_import_tabwidget.setCurrentWidget(widget)
            widget.update_callback()
            break


def global_subtitlesvideo_import_button_clicked(self):
    """Import subtitles from an existing file (SRT, DOCX, TXT, ...)."""
    supported_import_files = 'Text files' + ' ({})'.format(' '.join('*.{}'.format(fo) for fo in _list_of_supported_import_extensions))
    file_to_open = QFileDialog.getOpenFileName(parent=self, caption='Select the file to import', dir=os.path.expanduser('~'), filter=supported_import_files)[0]
    if file_to_open:
        session.SUBTITLE['segments'] += file_io.import_file(filename=file_to_open)[0]
        session.SUBTITLE['segments'].sort(key=lambda s: s.get('start', 0))
        timeline_widget = getattr(self, 'timeline_widget', None)
        if timeline_widget is not None:
            timeline_widget.update()
        from subtitld.interface import left_panel as _lp
        _lp.update(self)
        session.set_unsaved()


def translate(self):
    self.global_subtitlesvideo_import_button.setText(_('import_panel.import'))
    self.global_panel_import_language_combobox.setLabel(_('transcription_panel.language'))
    self.global_panel_import_start_transcription_button.setText(_('transcription_panel.start_transcription'))
    self.global_panel_import_engine_combobox.setLabel(_('transcription_panel.engine'))
    for widget in self.global_panel_import_tabwidget.findChildren(QWidget):
        if 'translate_callback' in dir(widget):
            widget.translate_callback()

    
