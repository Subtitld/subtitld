"""Left-panel "Audio" tab.

A ``QTabWidget`` (mirroring Global settings) whose first tab, "Recording",
holds the record-mode options: the input device and a live level / elapsed
readout. The record *action* lives on the player's record button — this tab is
just settings + monitoring.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QProgressBar, QTabWidget,
)
from PySide6.QtCore import QTimer

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import recorder as recorder_mod


def _fmt_clock(seconds):
    seconds = int(seconds or 0)
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


def load(self):
    tab_name = 'audio'
    panel = left_panel.left_panel(
        parent=self, tab_name=tab_name,
        update_callback=update, translate_callback=translate,
    )
    panel.layout().setContentsMargins(0, 0, 0, 0)

    self.left_panel_audio_tabwidget = QTabWidget()
    self.left_panel_audio_tabwidget.setObjectName('left_panel_audio_tabwidget')
    panel.layout().addWidget(self.left_panel_audio_tabwidget)

    # --- Recording tab ---
    rec_tab = QWidget()
    rec_v = QVBoxLayout(rec_tab)
    rec_v.setContentsMargins(10, 10, 10, 10)
    rec_v.setSpacing(8)

    self.audio_record_engine_label = QLabel()
    self.audio_record_engine_label.setProperty('class', 'widget_label')
    rec_v.addWidget(self.audio_record_engine_label)

    self.audio_record_engine_combobox = QComboBox()
    self.audio_record_engine_combobox.activated.connect(lambda: _engine_changed(self))
    rec_v.addWidget(self.audio_record_engine_combobox)

    self.audio_record_device_label = QLabel()
    self.audio_record_device_label.setProperty('class', 'widget_label')
    rec_v.addWidget(self.audio_record_device_label)

    self.audio_record_device_combobox = QComboBox()
    self.audio_record_device_combobox.activated.connect(lambda: _device_changed(self))
    rec_v.addWidget(self.audio_record_device_combobox)

    self.audio_record_help = QLabel()
    self.audio_record_help.setWordWrap(True)
    self.audio_record_help.setProperty('class', 'description')
    rec_v.addWidget(self.audio_record_help)

    level_line = QHBoxLayout()
    self.audio_record_level = QProgressBar()
    self.audio_record_level.setObjectName('audio_record_level')
    self.audio_record_level.setRange(0, 100)
    self.audio_record_level.setValue(0)
    self.audio_record_level.setTextVisible(False)
    level_line.addWidget(self.audio_record_level, 1)
    self.audio_record_elapsed = QLabel('00:00')
    level_line.addWidget(self.audio_record_elapsed)
    rec_v.addLayout(level_line)

    self.audio_record_status = QLabel()
    self.audio_record_status.setProperty('class', 'description')
    self.audio_record_status.setWordWrap(True)
    rec_v.addWidget(self.audio_record_status)

    # Live interim transcription (streaming engines) — shown separately from
    # the subtitle cues, which only get committed (final) text.
    self.audio_record_interim = QLabel()
    self.audio_record_interim.setObjectName('audio_record_interim')
    self.audio_record_interim.setWordWrap(True)
    self.audio_record_interim.setVisible(False)
    rec_v.addWidget(self.audio_record_interim)

    rec_v.addStretch()
    self.left_panel_audio_tabwidget.addTab(rec_tab, '')

    _populate_engines(self)
    _populate_devices(self)

    # Poll the record controller for the level meter / elapsed clock.
    self._audio_record_timer = QTimer(self)
    self._audio_record_timer.setInterval(100)
    self._audio_record_timer.timeout.connect(lambda: _tick(self))
    self._audio_record_timer.start()


def _populate_devices(self):
    combo = self.audio_record_device_combobox
    combo.blockSignals(True)
    combo.clear()
    devices = recorder_mod.list_input_devices()
    if not devices:
        combo.addItem(_('audio_panel.no_input'), None)
        combo.setEnabled(False)
        combo.blockSignals(False)
        return
    combo.setEnabled(True)
    for index, name in devices:
        combo.addItem(name, index)
    saved = session.CONFIG.get('record', {}).get('device', None)
    default = recorder_mod.default_input_device()
    target = saved if saved is not None else default
    if target is not None:
        pos = combo.findData(target)
        if pos >= 0:
            combo.setCurrentIndex(pos)
    combo.blockSignals(False)
    session.CONFIG.setdefault('record', {})['device'] = combo.currentData()


def _asr_providers():
    """ASR engines that are registered *and* usable right now (a provider that
    still needs configuration — e.g. a cloud key — reports is_available False
    and is hidden until it's set up)."""
    try:
        from subtitld.modules import addons
        from subtitld.modules.addons.provider import TASK_ASR_TRANSCRIBE
        out = []
        for p in addons.get_manager().providers_for_task(TASK_ASR_TRANSCRIBE):
            if getattr(p, 'id', '') == 'import':
                continue
            try:
                if not p.is_available():
                    continue
            except Exception:
                pass
            out.append(p)
        return out
    except Exception:
        return []


def _populate_engines(self):
    combo = self.audio_record_engine_combobox
    combo.blockSignals(True)
    combo.clear()
    providers = _asr_providers()
    if not providers:
        combo.addItem(_('audio_panel.no_engine_available'), None)
        combo.setEnabled(False)
        combo.blockSignals(False)
        return
    combo.setEnabled(True)
    for p in providers:
        combo.addItem(getattr(p, 'display_name', None) or p.id, p.id)
    saved = session.CONFIG.get('record', {}).get('engine', None)
    if saved is not None:
        pos = combo.findData(saved)
        if pos >= 0:
            combo.setCurrentIndex(pos)
    combo.blockSignals(False)
    session.CONFIG.setdefault('record', {})['engine'] = combo.currentData()


def _engine_changed(self):
    session.CONFIG.setdefault('record', {})['engine'] = \
        self.audio_record_engine_combobox.currentData()


def _device_changed(self):
    session.CONFIG.setdefault('record', {})['device'] = \
        self.audio_record_device_combobox.currentData()


_STATUS_KEYS = {
    'recording': 'audio_panel.recording',
    'transcribing': 'audio_panel.transcribing',
    'no-engine': 'audio_panel.no_engine',
    'no-input': 'audio_panel.no_input_device',
    'error': 'audio_panel.error',
    'idle': 'audio_panel.idle',
}


def _status_text(status):
    return _(_STATUS_KEYS.get(status, 'audio_panel.idle'))


def _tick(self):
    controller = getattr(self, 'record_controller', None)
    interim = getattr(controller, 'interim_text', '') if controller is not None else ''
    self.audio_record_interim.setText('… ' + interim if interim else '')
    self.audio_record_interim.setVisible(bool(interim))
    if controller is not None and controller.is_recording:
        self.audio_record_level.setValue(int(min(1.0, controller.level * 3.0) * 100))
        self.audio_record_elapsed.setText(_fmt_clock(controller.elapsed))
        self.audio_record_status.setText(_status_text(controller.status))
    else:
        self.audio_record_level.setValue(0)
        self.audio_record_elapsed.setText('00:00')
        # Keep a lingering warning (no engine / no input / error) visible after
        # Stop so the user can see why nothing happened; otherwise idle.
        status = controller.status if controller is not None else 'idle'
        if status in ('no-engine', 'no-input', 'error'):
            self.audio_record_status.setText(_status_text(status))
        else:
            self.audio_record_status.setText(_('audio_panel.idle'))


def show(self):
    update(self)


def update(self):
    _populate_engines(self)
    _populate_devices(self)


def hide(self):
    pass


def translate(self):
    self.left_panel_audio_tabwidget.setTabText(0, _('audio_panel.tab_recording'))
    self.audio_record_engine_label.setText(_('audio_panel.engine'))
    self.audio_record_device_label.setText(_('audio_panel.device'))
    self.audio_record_help.setText(_('audio_panel.help'))
