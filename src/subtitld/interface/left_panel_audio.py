"""Left-panel "Audio" tab.

A ``QTabWidget`` (mirroring Global settings) whose first tab, "Recording",
holds the record-mode options: the input device and a live level / elapsed
readout. The record *action* lives on the player's record button — this tab is
just settings + monitoring.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QProgressBar, QTabWidget,
    QScrollArea, QPushButton, QCheckBox, QDoubleSpinBox, QMenu, QFrame,
)
from PySide6.QtCore import QTimer, Qt

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import recorder as recorder_mod
from subtitld.modules import audio_effects


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

    # --- Effects tab (inserted FIRST) ---
    _build_effects_tab(self)

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


# --------------------------------------------------------------------------- #
# Effects tab                                                                  #
# --------------------------------------------------------------------------- #
_EFFECT_TITLES = {'eq': 'Equalizer', 'compressor': 'Compressor', 'gate': 'Gate'}
_EQ_BAND_LABELS = {'lowshelf': 'Low', 'peak': 'Mid', 'highshelf': 'High'}


def _build_effects_tab(self):
    tab = QWidget()
    v = QVBoxLayout(tab)
    v.setContentsMargins(10, 10, 10, 10)
    v.setSpacing(8)

    self.audio_effects_add_button = QPushButton(_('audio_panel.add_effect'))
    self.audio_effects_add_button.setObjectName('audio_effects_add_button')
    menu = QMenu(self.audio_effects_add_button)
    menu.addAction(_EFFECT_TITLES['eq'], lambda: _effects_add(self, 'eq'))
    menu.addAction(_EFFECT_TITLES['compressor'], lambda: _effects_add(self, 'compressor'))
    menu.addAction(_EFFECT_TITLES['gate'], lambda: _effects_add(self, 'gate'))
    self.audio_effects_add_button.setMenu(menu)
    v.addWidget(self.audio_effects_add_button)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    inner = QWidget()
    self.audio_effects_list_layout = QVBoxLayout(inner)
    self.audio_effects_list_layout.setContentsMargins(0, 0, 0, 0)
    self.audio_effects_list_layout.setSpacing(8)
    scroll.setWidget(inner)
    v.addWidget(scroll, 1)

    self._audio_effects_model = []
    self.left_panel_audio_tabwidget.insertTab(0, tab, '')


def _fx_engine(self):
    player = getattr(self, 'preview_panel_player', None)
    return getattr(player, '_audio_device', None) if player is not None else None


def _effects_sync(self):
    """Persist the current model and push it into the audio engine."""
    audio_effects.save_effects(self._audio_effects_model)
    audio_effects.apply_to_engine(_fx_engine(self), self._audio_effects_model)


def _effects_reload(self):
    """Load the project's effects, rebuild the cards, and apply to the engine."""
    self._audio_effects_model = audio_effects.load_effects()
    _effects_rebuild(self)
    audio_effects.apply_to_engine(_fx_engine(self), self._audio_effects_model)


def _effects_add(self, effect_type):
    self._audio_effects_model.append(audio_effects.new_effect(effect_type, 'background'))
    _effects_rebuild(self)
    _effects_sync(self)


def _effects_remove(self, spec):
    self._audio_effects_model = [s for s in self._audio_effects_model if s is not spec]
    _effects_rebuild(self)
    _effects_sync(self)


def _effects_rebuild(self):
    layout = self.audio_effects_list_layout
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
    if not self._audio_effects_model:
        empty = QLabel(_('audio_panel.effects_empty'))
        empty.setWordWrap(True)
        empty.setProperty('class', 'description')
        layout.addWidget(empty)
    else:
        for spec in self._audio_effects_model:
            layout.addWidget(_build_effect_card(self, spec))
    layout.addStretch()


def _fx_target_options():
    opts = [(_('audio_panel.target_background'), {'kind': 'background', 'speaker': None}),
            (_('audio_panel.target_voice'), {'kind': 'voice', 'speaker': None})]
    for name in session.SPEAKERS.keys():
        opts.append((_('audio_panel.target_speaker').format(name=name),
                     {'kind': 'speaker', 'speaker': name}))
    return opts


def _fx_set(self, d, key, value):
    d[key] = value
    _effects_sync(self)


def _spin_row(label, value, lo, hi, step, decimals, suffix, cb):
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    lab = QLabel(label)
    lab.setProperty('class', 'description')
    h.addWidget(lab, 1)
    sp = QDoubleSpinBox()
    sp.setRange(lo, hi)
    sp.setSingleStep(step)
    sp.setDecimals(decimals)
    if suffix:
        sp.setSuffix(suffix)
    sp.setValue(float(value))
    sp.valueChanged.connect(cb)
    h.addWidget(sp)
    return row, sp


def _build_effect_card(self, spec):
    card = QFrame()
    card.setObjectName('audio_effect_card')
    card.setProperty('class', 'audio_effect_card')
    cv = QVBoxLayout(card)
    cv.setContentsMargins(10, 8, 10, 10)
    cv.setSpacing(6)

    # Header: enable | title | target | remove
    header = QHBoxLayout()
    header.setSpacing(6)
    enable = QCheckBox()
    enable.setChecked(bool(spec.get('enabled', True)))
    enable.toggled.connect(lambda on, s=spec: _fx_set(self, s, 'enabled', on))
    header.addWidget(enable)

    title = QLabel(_EFFECT_TITLES.get(spec.get('type'), spec.get('type', '')))
    title.setProperty('class', 'widget_label')
    header.addWidget(title, 1)

    target_combo = QComboBox()
    options = _fx_target_options()
    for text, tgt in options:
        target_combo.addItem(text, tgt)
    cur = spec.get('target') or {}
    for i, (_t, tgt) in enumerate(options):
        if tgt.get('kind') == cur.get('kind') and tgt.get('speaker') == cur.get('speaker'):
            target_combo.setCurrentIndex(i)
            break
    target_combo.activated.connect(
        lambda _i, s=spec, c=target_combo: _fx_set(self, s, 'target', dict(c.currentData())))
    header.addWidget(target_combo)

    remove = QPushButton('✕')
    remove.setObjectName('audio_effect_remove')
    remove.setFixedWidth(26)
    remove.clicked.connect(lambda _c, s=spec: _effects_remove(self, s))
    header.addWidget(remove)
    cv.addLayout(header)

    # Range: whole timeline vs [from, to] seconds
    rng = spec.get('range')
    range_check = QCheckBox(_('audio_panel.effect_time_range'))
    range_check.setChecked(rng is not None)
    range_row = QWidget()
    rh = QHBoxLayout(range_row)
    rh.setContentsMargins(0, 0, 0, 0)
    rh.setSpacing(6)
    from_spin = QDoubleSpinBox()
    from_spin.setRange(0, 999999)
    from_spin.setDecimals(2)
    from_spin.setSuffix(' s')
    to_spin = QDoubleSpinBox()
    to_spin.setRange(0, 999999)
    to_spin.setDecimals(2)
    to_spin.setSuffix(' s')
    if rng:
        from_spin.setValue(float(rng[0]))
        to_spin.setValue(float(rng[1]))
    else:
        to_spin.setValue(60.0)
    rh.addWidget(QLabel(_('audio_panel.effect_from')))
    rh.addWidget(from_spin, 1)
    rh.addWidget(QLabel(_('audio_panel.effect_to')))
    rh.addWidget(to_spin, 1)
    range_row.setEnabled(rng is not None)

    def _apply_range(*_a, s=spec, rc=range_check, fs=from_spin, ts=to_spin, rr=range_row):
        rr.setEnabled(rc.isChecked())
        if rc.isChecked():
            lo, hi = fs.value(), ts.value()
            if hi <= lo:
                hi = lo + 0.5
            s['range'] = [lo, hi]
        else:
            s['range'] = None
        _effects_sync(self)

    range_check.toggled.connect(_apply_range)
    from_spin.valueChanged.connect(_apply_range)
    to_spin.valueChanged.connect(_apply_range)
    cv.addWidget(range_check)
    cv.addWidget(range_row)

    # Params
    params = spec.setdefault('params', {})
    etype = spec.get('type')
    if etype == 'eq':
        for band in params.get('bands', []):
            lbl = _EQ_BAND_LABELS.get(band.get('type'), band.get('type', ''))
            g_row, _g = _spin_row(_('audio_panel.eq_band_gain').format(band=lbl),
                                  band.get('gain', 0.0), -24, 24, 0.5, 1, ' dB',
                                  lambda v, b=band: _fx_set(self, b, 'gain', v))
            f_row, _f = _spin_row(_('audio_panel.eq_band_freq').format(band=lbl),
                                  band.get('freq', 1000.0), 20, 20000, 10, 0, ' Hz',
                                  lambda v, b=band: _fx_set(self, b, 'freq', v))
            cv.addWidget(g_row)
            cv.addWidget(f_row)
    elif etype == 'compressor':
        cv.addWidget(_spin_row(_('audio_panel.comp_threshold'), params.get('threshold_db', -18), -60, 0, 1, 0, ' dB', lambda v: _fx_set(self, params, 'threshold_db', v))[0])
        cv.addWidget(_spin_row(_('audio_panel.comp_ratio'), params.get('ratio', 3), 1, 20, 0.5, 1, ':1', lambda v: _fx_set(self, params, 'ratio', v))[0])
        cv.addWidget(_spin_row(_('audio_panel.comp_attack'), params.get('attack_ms', 10), 0.1, 200, 1, 1, ' ms', lambda v: _fx_set(self, params, 'attack_ms', v))[0])
        cv.addWidget(_spin_row(_('audio_panel.comp_release'), params.get('release_ms', 120), 5, 2000, 5, 0, ' ms', lambda v: _fx_set(self, params, 'release_ms', v))[0])
        cv.addWidget(_spin_row(_('audio_panel.comp_makeup'), params.get('makeup_db', 0), 0, 24, 0.5, 1, ' dB', lambda v: _fx_set(self, params, 'makeup_db', v))[0])
    elif etype == 'gate':
        cv.addWidget(_spin_row(_('audio_panel.gate_threshold'), params.get('threshold_db', -45), -80, 0, 1, 0, ' dB', lambda v: _fx_set(self, params, 'threshold_db', v))[0])
        cv.addWidget(_spin_row(_('audio_panel.gate_attack'), params.get('attack_ms', 2), 0.1, 200, 1, 1, ' ms', lambda v: _fx_set(self, params, 'attack_ms', v))[0])
        cv.addWidget(_spin_row(_('audio_panel.gate_release'), params.get('release_ms', 120), 5, 2000, 5, 0, ' ms', lambda v: _fx_set(self, params, 'release_ms', v))[0])

    return card


def show(self):
    update(self)


def update(self):
    _populate_engines(self)
    _populate_devices(self)
    _effects_reload(self)


def hide(self):
    pass


def translate(self):
    self.left_panel_audio_tabwidget.setTabText(0, _('audio_panel.tab_effects'))
    self.left_panel_audio_tabwidget.setTabText(1, _('audio_panel.tab_recording'))
    self.audio_record_engine_label.setText(_('audio_panel.engine'))
    self.audio_record_device_label.setText(_('audio_panel.device'))
    self.audio_record_help.setText(_('audio_panel.help'))
    if hasattr(self, 'audio_effects_add_button'):
        self.audio_effects_add_button.setText(_('audio_panel.add_effect'))
