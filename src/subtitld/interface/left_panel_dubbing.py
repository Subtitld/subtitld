import os
import secrets
import asyncio
import edge_tts

from PySide6.QtWidgets import QStackedWidget, QWidget, QVBoxLayout, QHBoxLayout, QSpinBox, QPushButton, QLabel, QLineEdit, QSizePolicy, QColorDialog, QComboBox, QCheckBox, QApplication, QRadioButton, QButtonGroup
from PySide6.QtGui import QImage, QPixmap, QPainter, QPainterPath, QColor
from PySide6.QtCore import QThread, QObject, Signal, Qt, QSize

from subtitld.interface import utils
from subtitld.interface import left_panel
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import subtitles


def _parse_timecode_input(text):
    """Parse 'HH:MM:SS.mmm' / 'MM:SS.mmm' / 'SS.mmm' into seconds, or None."""
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


class _EdgeTTSSignals(QObject):
    speech_ready = Signal(str, dict, str)   # (uid, subtitle, output_file)
    speech_error = Signal(str, dict, str)   # (uid, subtitle, message)
    voices_updated = Signal()


class _EdgeTTSSpeechThread(QThread):
    speech_ready = Signal(str, dict, str)
    speech_error = Signal(str, dict, str)

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
                size = os.path.getsize(output_file) if os.path.exists(output_file) else 0
                if size <= 0:
                    if os.path.exists(output_file):
                        try:
                            os.remove(output_file)
                        except OSError:
                            pass
                    self.speech_error.emit(subtitle['uid'], subtitle, 'edge-tts returned empty audio')
                    continue
                self.speech_ready.emit(subtitle['uid'], subtitle, output_file)
            except Exception as e:
                self.speech_error.emit(subtitle['uid'], subtitle, str(e))

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
    def stretch(subtitle, ratio):
        """Re-render `subtitle`'s dub with a speech rate derived from the visual
        stretch ratio. `ratio` is original_visual_width / new_visual_width —
        values < 1 mean the user made the clip longer (slower speech); > 1
        means shorter (faster). Locks the subtitle while regenerating; the
        new clip will land at index 0 via the normal speech_ready handler.

        Returns True when a regeneration was kicked off, False if the ratio
        was a no-op (rate didn't move)."""
        if ratio <= 0 or not subtitle:
            return False
        speaker_name = subtitle.get('speaker', 'A')
        speaker_dubbing = session.SPEAKERS.get(speaker_name, {}).get('dubbing', {})
        overrides = subtitle.setdefault('dubbing_options', {})
        current_rate = int(overrides.get('rate', speaker_dubbing.get('rate', 0)) or 0)
        # edge-tts rate is a percentage offset from 100% speed.
        current_speed_pct = 100 + current_rate
        new_rate = int(round(current_speed_pct * ratio - 100))
        new_rate = max(-100, min(100, new_rate))
        if new_rate == current_rate:
            return False
        overrides['rate'] = new_rate
        subtitle['locked'] = True
        EdgeTTSEngine.generate_speeches([{
            'uid': secrets.token_hex(4),
            'text': subtitle['text'],
            'speaker': speaker_name,
            'start': subtitle['start'],
            'end': subtitle['end'],
            'voice': overrides.get('voice') or speaker_dubbing.get('voice', ''),
            'rate': new_rate,
            'pitch': overrides.get('pitch', speaker_dubbing.get('pitch', 0)),
        }])
        return True

    @staticmethod
    def _on_speech_ready(uid, original_subtitle, file_path):
        size = os.path.getsize(file_path) if os.path.isfile(file_path) else 0
        if size <= 0:
            EdgeTTSEngine._on_speech_error(uid, original_subtitle, 'edge-tts produced an empty audio file')
            return
        for subtitle in session.SUBTITLE['segments']:
            if subtitle.get('start') == original_subtitle['start']:
                dubs = subtitle.setdefault('dubbing', [])
                inherited_start = dubs[0].get('start', subtitle['start']) if dubs else subtitle['start']
                dubs.insert(0, {
                    'engine': 'edge-tts',
                    'path': file_path,
                    'start': inherited_start,
                    'end': subtitle['end'],
                    'uid': uid,
                    'rate': original_subtitle.get('rate', 0),
                    'pitch': original_subtitle.get('pitch', 0),
                })
                subtitle['locked'] = False
                break
        for window in QApplication.topLevelWidgets():
            preview = getattr(window, 'preview_panel_player', None)
            timeline_widget = getattr(window, 'timeline_widget', None)
            if preview is None and timeline_widget is None:
                continue
            if preview is not None:
                preview._audio_device.sync_subtitle_dubs(session.SUBTITLE['segments'])
            if timeline_widget is not None:
                pending = getattr(timeline_widget, 'dub_stretching', None)
                if pending is not None and pending.get('subtitle', None) is not None:
                    for subtitle in session.SUBTITLE['segments']:
                        if subtitle.get('start') == original_subtitle['start'] and pending['subtitle'] is subtitle:
                            timeline_widget.dub_stretching = None
                            break
                timeline_widget.update()
            session.set_unsaved()
            break

    @staticmethod
    def _on_speech_error(uid, original_subtitle, message):
        """Unlock the source subtitle so the user can retry, and refresh the
        timeline so the locked-state visual goes away. Empty/failed clips
        never enter the project's dub list."""
        print('edge-tts error:', uid, message)
        target_start = original_subtitle.get('start') if isinstance(original_subtitle, dict) else None
        for subtitle in session.SUBTITLE.get('segments', []) or []:
            if target_start is not None and subtitle.get('start') == target_start:
                subtitle['locked'] = False
                break
        for window in QApplication.topLevelWidgets():
            timeline_widget = getattr(window, 'timeline_widget', None)
            if timeline_widget is not None:
                timeline_widget.update()

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

            scope_box = QWidget()
            scope_box.setLayout(QHBoxLayout())
            scope_box.layout().setContentsMargins(0, 0, 0, 0)
            scope_box.layout().setSpacing(8)
            widget.layout().addWidget(scope_box)

            widget.scope_label = QLabel()
            widget.scope_label.setProperty('class', 'widget_label')
            scope_box.layout().addWidget(widget.scope_label)

            widget.scope_group = QButtonGroup(widget)
            widget.scope_all = QRadioButton()
            widget.scope_selected = QRadioButton()
            widget.scope_range = QRadioButton()
            widget.scope_all.setChecked(True)
            for btn in (widget.scope_all, widget.scope_selected, widget.scope_range):
                widget.scope_group.addButton(btn)
                scope_box.layout().addWidget(btn)
            scope_box.layout().addStretch()

            widget.scope_range_box = QWidget()
            widget.scope_range_box.setLayout(QHBoxLayout())
            widget.scope_range_box.layout().setContentsMargins(0, 0, 0, 0)
            widget.scope_range_box.layout().setSpacing(6)
            widget.scope_range_box.setVisible(False)
            widget.layout().addWidget(widget.scope_range_box)

            widget.scope_range_from_label = QLabel()
            widget.scope_range_box.layout().addWidget(widget.scope_range_from_label)
            widget.scope_range_from = QLineEdit()
            widget.scope_range_from.setPlaceholderText('00:00:00.000')
            widget.scope_range_box.layout().addWidget(widget.scope_range_from, 1)

            widget.scope_range_to_label = QLabel()
            widget.scope_range_box.layout().addWidget(widget.scope_range_to_label)
            widget.scope_range_to = QLineEdit()
            widget.scope_range_to.setPlaceholderText('00:00:00.000')
            widget.scope_range_box.layout().addWidget(widget.scope_range_to, 1)

            widget.scope_range.toggled.connect(lambda checked: widget.scope_range_box.setVisible(checked))

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

        def _on_speech_error(widget, uid, _subtitle, message):
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

        def _scoped_segments_for_speaker(widget, speaker_name):
            segments = session.SUBTITLE.get('segments', []) or []
            speaker_segments = [s for s in segments if s.get('speaker', 'A') == speaker_name]
            if widget.scope_selected.isChecked():
                sel = session.SUBTITLE.get('selected')
                return [sel] if sel and sel.get('speaker', 'A') == speaker_name else []
            if widget.scope_range.isChecked():
                rng_from = _parse_timecode_input(widget.scope_range_from.text()) or 0.0
                raw_to = _parse_timecode_input(widget.scope_range_to.text())
                rng_to = float('inf') if raw_to is None else raw_to
                return [s for s in speaker_segments if s.get('end', 0) > rng_from and s.get('start', 0) < rng_to]
            return speaker_segments

        def generate_all_speeches_button_clicked(widget):
            speaker_name = widget.property('speaker')
            speaker_dubbing = session.SPEAKERS.get(speaker_name, {}).get('dubbing', {})
            scoped = widget._scoped_segments_for_speaker(speaker_name)
            speeches_to_generate = []
            for subtitle in scoped:
                overrides = subtitle.get('dubbing_options', {})
                subtitle['locked'] = True
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
            widget.scope_label.setText(_('panel_scope.label'))
            widget.scope_all.setText(_('panel_scope.all'))
            widget.scope_selected.setText(_('panel_scope.selected'))
            widget.scope_range.setText(_('panel_scope.range'))
            widget.scope_range_from_label.setText(_('panel_scope.from'))
            widget.scope_range_to_label.setText(_('panel_scope.to'))

    class dubbingPanel(QWidget):
        def __init__(widget, parent=None):
            super().__init__(parent=None)
            widget.parent = parent
            widget.setLayout(QVBoxLayout())
            widget.layout().setContentsMargins(0, 0, 0, 0)
            widget.layout().setSpacing(5)
            widget.setProperty('dubbing_engine', 'edge-tts')
            widget.setProperty('class', 'transparent_panel')

            widget.voice_combobox = utils.LabeledComboBox()
            widget.voice_combobox.addItems(session.CONFIG.get('dubbing', {}).get('edge-tts', {}).get('voices', {}).keys())
            widget.voice_combobox.activated.connect(lambda: widget.voice_combobox_changed())
            widget.layout().addWidget(widget.voice_combobox)

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
            widget.layout().addLayout(settings_line)

            widget.generate_speech_button = QPushButton()
            widget.generate_speech_button.clicked.connect(lambda: widget.generate_speech_button_clicked())
            widget.layout().addWidget(widget.generate_speech_button, 0, Qt.AlignRight)

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
            selected['locked'] = True
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
            window = widget.window()
            timeline_widget = getattr(window, 'timeline_widget', None)
            if timeline_widget is not None:
                timeline_widget.update()

        def update(widget):
            selected = session.SUBTITLE.get('selected')
            if not selected:
                return

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
            widget.voice_combobox.setLabel(_('subtitles_panel_widget_dubbing.voice'))
            widget.voice_rate_label.setText(_('subtitles_panel_widget_dubbing.rate'))
            widget.voice_pitch_label.setText(_('subtitles_panel_widget_dubbing.pitch'))
            widget.generate_speech_button.setText(_('subtitles_panel_widget_dubbing.generate_speech'))


EdgeTTSEngine.signals.speech_ready.connect(EdgeTTSEngine._on_speech_ready)
EdgeTTSEngine.signals.speech_error.connect(EdgeTTSEngine._on_speech_error)


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

    self.left_panel_dubbing_no_subtitle_label = QLabel()
    self.left_panel_dubbing_no_subtitle_label.setWordWrap(True)
    self.left_panel_dubbing_no_subtitle_label.setAlignment(Qt.AlignCenter)
    left_panel_dubbing_panel.layout().addWidget(self.left_panel_dubbing_no_subtitle_label)

    self.left_panel_dubbing_subtitle_settings = QWidget()
    self.left_panel_dubbing_subtitle_settings.setLayout(QVBoxLayout())
    self.left_panel_dubbing_subtitle_settings.layout().setContentsMargins(0, 0, 0, 0)
    self.left_panel_dubbing_subtitle_settings.layout().setSpacing(10)
    left_panel_dubbing_panel.layout().addWidget(self.left_panel_dubbing_subtitle_settings)

    self.left_panel_dubbing_engine_combobox = utils.LabeledComboBox()
    self.left_panel_dubbing_engine_combobox.setProperty('class', 'button')
    self.left_panel_dubbing_engine_combobox.activated.connect(lambda: left_panel_dubbing_engine_combobox_activated(self))
    self.left_panel_dubbing_subtitle_settings.layout().addWidget(self.left_panel_dubbing_engine_combobox)

    self.global_panel_dubbing_tabwidget = QStackedWidget()
    self.global_panel_dubbing_tabwidget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)

    global_panel_dubbing_edgetts_engine = EdgeTTSEngine
    self.global_panel_dubbing_tabwidget.addWidget(global_panel_dubbing_edgetts_engine.dubbingPanel())
    self.left_panel_speakers_list_of_available_dubbing_engine['edge-tts'] = global_panel_dubbing_edgetts_engine
    self.left_panel_dubbing_engine_combobox.addItem('edge-tts')

    self.left_panel_dubbing_subtitle_settings.layout().addWidget(self.global_panel_dubbing_tabwidget)
    left_panel_dubbing_panel.layout().addStretch()

    update(self)


def left_panel_dubbing_enable_checkbox_clicked(self):
    enabled = self.left_panel_dubbing_enable_checkbox.isChecked()
    session.CONFIG['dubbing']['enabled'] = enabled

    for window in QApplication.topLevelWidgets():
        preview = getattr(window, 'preview_panel_player', None)
        if preview is not None:
            engine = getattr(preview, '_audio_device', None)
            if engine is not None and hasattr(engine, 'speaker_tracks'):
                for track in engine.speaker_tracks.values():
                    track.enabled = enabled
        timeline_widget = getattr(window, 'timeline_widget', None)
        if timeline_widget is not None:
            timeline_widget.update()


def left_panel_dubbing_engine_combobox_activated(self):
    session.CONFIG['dubbing']['selected_engine'] = self.left_panel_dubbing_engine_combobox.currentText()
    global_panel_dubbing_tabwidget_update(self)


def update(self):
    enabled = session.CONFIG['dubbing'].get('enabled', False)
    self.left_panel_dubbing_enable_checkbox.setChecked(enabled)
    self.left_panel_dubbing_engine_combobox.setCurrentText(session.CONFIG['dubbing'].get('selected_engine', 'edge-tts'))

    has_selection = session.SUBTITLE.get('selected') is not None
    self.left_panel_dubbing_no_subtitle_label.setVisible(not has_selection)
    self.left_panel_dubbing_subtitle_settings.setVisible(has_selection)

    selected_engine = self.left_panel_dubbing_engine_combobox.currentText()
    for widget in self.global_panel_dubbing_tabwidget.findChildren(QWidget):
        if widget.property('dubbing_engine') == selected_engine and hasattr(widget, 'update_callback'):
            widget.update_callback()
            break

    preview = getattr(self, 'preview_panel_player', None)
    if preview is not None:
        engine = getattr(preview, '_audio_device', None)
        if engine is not None and hasattr(engine, 'speaker_tracks'):
            for track in engine.speaker_tracks.values():
                track.enabled = enabled


def translate(self):
    self.left_panel_dubbing_enable_checkbox.setText(_('subtitles_panel_widget_dubbing.enable_dubbing'))
    self.left_panel_dubbing_engine_combobox.setLabel(_('subtitles_panel_widget_dubbing.engine'))
    self.left_panel_dubbing_no_subtitle_label.setText(_('subtitles_panel_widget_dubbing.no_subtitle_selected'))
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
    