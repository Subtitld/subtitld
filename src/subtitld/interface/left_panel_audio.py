"""Left-panel "Audio" tab.

A ``QTabWidget`` with two tabs:

* "Effects" — a small mixer. The tracks (the background music, the voice,
  each speaker's dubs) are listed with a live meter; the selected track's
  effect chain is shown below it as devices, top to bottom in signal order:
  an equalizer with its curve, compressors and gates with their transfer
  curves, all set with knobs or by dragging the graphs.
* "Recording" — the record-mode options (transcription engine, input
  device) and an input monitor. The record *action* lives on the player's
  record button; this tab is settings and monitoring.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget, QScrollArea, QPushButton, QDoubleSpinBox,
    QMenu, QFrame, QGraphicsOpacityEffect, QSizePolicy,
)
from PySide6.QtCore import QTimer, Qt, Signal, QSize
from PySide6.QtGui import QPainter, QColor, QPen, QPolygonF, QIcon, QPixmap
from PySide6.QtCore import QPointF

from subtitld.interface import left_panel
from subtitld.interface import utils
from subtitld.interface import audio_widgets as aw
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import recorder as recorder_mod
from subtitld.modules import audio_effects


def _fmt_clock(seconds):
    seconds = int(seconds or 0)
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


def _clear(layout):
    """Empty a layout. setParent(None) first: deleteLater alone leaves the
    widget a child (and drawn) until the event loop gets to it."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


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

    _build_recording_tab(self)
    _build_effects_tab(self)   # inserted first

    _populate_engines(self)
    _populate_devices(self)

    # Poll the record controller and the engine for the meters.
    self._audio_record_timer = QTimer(self)
    self._audio_record_timer.setInterval(33)
    self._audio_record_timer.timeout.connect(lambda: _tick(self))
    self._audio_record_timer.start()


# --------------------------------------------------------------------------- #
# Recording tab                                                                #
# --------------------------------------------------------------------------- #
def _build_recording_tab(self):
    rec_tab = QWidget()
    rec_v = QVBoxLayout(rec_tab)
    rec_v.setContentsMargins(10, 10, 10, 10)
    rec_v.setSpacing(8)

    self.audio_record_engine_block = utils.LabeledComboBox()
    self.audio_record_engine_combobox = self.audio_record_engine_block.combobox
    self.audio_record_engine_combobox.activated.connect(lambda: _engine_changed(self))
    rec_v.addWidget(self.audio_record_engine_block)

    self.audio_record_device_block = utils.LabeledComboBox()
    self.audio_record_device_combobox = self.audio_record_device_block.combobox
    self.audio_record_device_combobox.activated.connect(lambda: _device_changed(self))
    rec_v.addWidget(self.audio_record_device_block)

    self.audio_record_help = QLabel()
    self.audio_record_help.setWordWrap(True)
    self.audio_record_help.setProperty('class', 'description')
    rec_v.addWidget(self.audio_record_help)

    # Input monitor: what the microphone hears, and what the recorder does.
    monitor = QFrame(objectName='audio_record_monitor')
    monitor_v = QVBoxLayout(monitor)
    monitor_v.setContentsMargins(12, 10, 12, 10)
    monitor_v.setSpacing(8)
    top = QHBoxLayout()
    top.setSpacing(8)
    self.audio_record_monitor_label = QLabel(objectName='audio_record_monitor_label')
    top.addWidget(self.audio_record_monitor_label)
    top.addStretch(1)
    self.audio_record_elapsed = QLabel('00:00', objectName='audio_record_elapsed')
    top.addWidget(self.audio_record_elapsed)
    monitor_v.addLayout(top)
    self.audio_record_level = aw.LevelMeter(scale=True)
    self.audio_record_level.setObjectName('audio_record_level')
    monitor_v.addWidget(self.audio_record_level)
    status_line = QHBoxLayout()
    status_line.setSpacing(8)
    self.audio_record_status_light = QLabel(objectName='audio_record_status_light')
    self.audio_record_status_light.setFixedSize(8, 8)
    status_line.addWidget(self.audio_record_status_light, 0, Qt.AlignVCenter)
    self.audio_record_status = QLabel(objectName='audio_record_status')
    self.audio_record_status.setWordWrap(True)
    status_line.addWidget(self.audio_record_status, 1)
    monitor_v.addLayout(status_line)
    # Live interim transcription (streaming engines) — shown separately from
    # the subtitle cues, which only get committed (final) text.
    self.audio_record_interim = QLabel()
    self.audio_record_interim.setObjectName('audio_record_interim')
    self.audio_record_interim.setWordWrap(True)
    self.audio_record_interim.setVisible(False)
    monitor_v.addWidget(self.audio_record_interim)
    rec_v.addWidget(monitor)

    rec_v.addStretch()
    self.left_panel_audio_tabwidget.addTab(rec_tab, '')


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


# The one implementation lives in record_controls: the record button needs
# the same answer, and two copies of this filter would drift.
from subtitld.interface.record_controls import asr_providers as _asr_providers


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
    pos = combo.findData(saved) if saved else -1
    if pos >= 0:
        combo.setCurrentIndex(pos)
    combo.blockSignals(False)
    # A saved engine that is not listed is kept: its add-on may simply not be
    # installed (yet), and overwriting the choice here would lose it for good
    # the first time this tab is shown.
    if pos >= 0 or not saved:
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
# The status light: red while recording, amber for a problem, dim at rest.
_STATUS_LIGHTS = {'recording': 'live', 'transcribing': 'live',
                  'no-engine': 'problem', 'no-input': 'problem', 'error': 'problem'}


def _status_text(status):
    return _(_STATUS_KEYS.get(status, 'audio_panel.idle'))


def _set_status(self, status):
    self.audio_record_status.setText(_status_text(status))
    light = _STATUS_LIGHTS.get(status, 'idle')
    if self.audio_record_status_light.property('state') != light:
        self.audio_record_status_light.setProperty('state', light)
        self.audio_record_status_light.style().unpolish(self.audio_record_status_light)
        self.audio_record_status_light.style().polish(self.audio_record_status_light)


def _tick(self):
    if self.audio_record_level.isVisible():
        _tick_recording(self)
    if getattr(self, 'audio_fx_tab', None) is not None and self.audio_fx_tab.isVisible():
        _fx_tick_meters(self)


def _tick_recording(self):
    controller = getattr(self, 'record_controller', None)
    interim = getattr(controller, 'interim_text', '') if controller is not None else ''
    self.audio_record_interim.setText('… ' + interim if interim else '')
    self.audio_record_interim.setVisible(bool(interim))
    if controller is not None and controller.is_recording:
        self.audio_record_level.set_level(float(controller.level or 0.0))
        self.audio_record_elapsed.setText(_fmt_clock(controller.elapsed))
        _set_status(self, controller.status)
    else:
        self.audio_record_level.set_level(0.0)
        self.audio_record_elapsed.setText('00:00')
        # Keep a lingering warning (no engine / no input / error) visible after
        # Stop so the user can see why nothing happened; otherwise idle.
        status = controller.status if controller is not None else 'idle'
        _set_status(self, status if status in ('no-engine', 'no-input', 'error') else 'idle')


# --------------------------------------------------------------------------- #
# Effects tab: tracks and their chains                                         #
# --------------------------------------------------------------------------- #
_TRACK_COLORS = {'background': '#6f8fb0', 'voice': '#8fd0ea'}


def _effect_names():
    return {'eq': _('audio_panel.effect_eq'), 'compressor': _('audio_panel.effect_compressor'),
            'gate': _('audio_panel.effect_gate')}


def _build_effects_tab(self):
    tab = QWidget(objectName='audio_fx_tab')
    outer = QVBoxLayout(tab)
    outer.setContentsMargins(0, 0, 0, 0)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setProperty('class', 'transparent_panel')
    inner = QWidget(objectName='audio_fx_inner')
    inner.setProperty('class', 'transparent_panel')
    v = QVBoxLayout(inner)
    v.setContentsMargins(10, 10, 10, 10)
    v.setSpacing(6)

    self.audio_fx_tracks_label = QLabel()
    self.audio_fx_tracks_label.setProperty('class', 'widget_label')
    v.addWidget(self.audio_fx_tracks_label)
    tracks = QFrame(objectName='audio_fx_tracks')
    self.audio_fx_tracks_layout = QVBoxLayout(tracks)
    self.audio_fx_tracks_layout.setContentsMargins(0, 0, 0, 0)
    self.audio_fx_tracks_layout.setSpacing(1)
    v.addWidget(tracks)

    v.addSpacing(8)
    self.audio_fx_chain_label = QLabel()
    self.audio_fx_chain_label.setProperty('class', 'widget_label')
    v.addWidget(self.audio_fx_chain_label)
    self.audio_fx_chain_hint = QLabel()
    self.audio_fx_chain_hint.setProperty('class', 'description')
    self.audio_fx_chain_hint.setWordWrap(True)
    v.addWidget(self.audio_fx_chain_hint)
    chain = QWidget(objectName='audio_fx_chain')
    self.audio_fx_chain_layout = QVBoxLayout(chain)
    self.audio_fx_chain_layout.setContentsMargins(0, 4, 0, 0)
    self.audio_fx_chain_layout.setSpacing(0)
    v.addWidget(chain)
    v.addStretch(1)

    scroll.setWidget(inner)
    outer.addWidget(scroll)

    self.audio_fx_tab = tab
    self._audio_effects_model = []
    self._fx_track = ('background', None)
    self._fx_rows = {}          # track key -> _TrackRow
    self._fx_cards = []         # the shown _DeviceCard widgets
    self._fx_collapsed = set()  # ids of folded devices (view state only)
    self._fx_band = {}          # EQ id -> selected band
    self.left_panel_audio_tabwidget.insertTab(0, tab, '')


def _fx_engine(self):
    player = getattr(self, 'preview_panel_player', None)
    return getattr(player, '_audio_device', None) if player is not None else None


def _fx_playing(self):
    player = getattr(self, 'preview_panel_player', None)
    try:
        return player is not None and not player.is_paused()
    except Exception:
        return False


def _engine_track(engine, key):
    if engine is None:
        return None
    kind, speaker = key
    if kind == 'background':
        return getattr(engine, 'background_sound', None)
    if kind == 'voice':
        return getattr(engine, 'vocals_sound', None)
    return (getattr(engine, 'speaker_tracks', None) or {}).get(speaker)


def _fx_tracks(self):
    """(key, name, colour) of every track effects can go on: the background
    music, the voice, each speaker — and any speaker an effect still names
    after the speaker was removed, so its effects can still be seen."""
    tracks = [(('background', None), _('audio_panel.target_background'), _TRACK_COLORS['background']),
              (('voice', None), _('audio_panel.target_voice'), _TRACK_COLORS['voice'])]
    names = list(session.SPEAKERS.keys())
    for spec in self._audio_effects_model:
        target = spec.get('target') or {}
        if target.get('kind') == 'speaker' and target.get('speaker') is not None and target['speaker'] not in names:
            names.append(target['speaker'])
    for name in names:
        color = (session.SPEAKERS.get(name) or {}).get('color') or '#b8cee0'
        tracks.append((('speaker', name), _('audio_panel.target_speaker').format(name=name), color))
    return tracks


def _fx_chain(self, key=None):
    kind, speaker = key or self._fx_track
    return audio_effects.specs_for_target(self._audio_effects_model, kind, speaker)


def _effects_sync(self):
    """Persist the current model and push it into the audio engine."""
    audio_effects.save_effects(self._audio_effects_model)
    audio_effects.apply_to_engine(_fx_engine(self), self._audio_effects_model)


def _effects_reload(self):
    """Load the project's effects, rebuild the view, and apply to the engine."""
    self._audio_effects_model = audio_effects.load_effects()
    if self._fx_track not in [key for key, _name, _color in _fx_tracks(self)]:
        self._fx_track = ('background', None)
    _fx_rebuild(self)
    audio_effects.apply_to_engine(_fx_engine(self), self._audio_effects_model)


def _fx_rebuild(self):
    _fx_rebuild_tracks(self)
    _fx_rebuild_chain(self)


def _fx_select_track(self, key):
    if key != self._fx_track:
        self._fx_track = key
        _fx_rebuild(self)


def _fx_add(self, effect_type):
    kind, speaker = self._fx_track
    self._audio_effects_model.append(audio_effects.new_effect(effect_type, kind, speaker))
    _fx_rebuild(self)
    _effects_sync(self)


def _fx_remove(self, spec):
    self._audio_effects_model = [s for s in self._audio_effects_model if s is not spec]
    self._fx_collapsed.discard(spec.get('id'))
    _fx_rebuild(self)
    _effects_sync(self)


def _fx_move(self, spec, step):
    """Move a device up (-1) or down (+1) its track's chain."""
    model = self._audio_effects_model
    target = spec.get('target') or {}
    same = [i for i, s in enumerate(model)
            if (s.get('target') or {}).get('kind') == target.get('kind')
            and (target.get('kind') != 'speaker' or (s.get('target') or {}).get('speaker') == target.get('speaker'))]
    here = next(n for n, i in enumerate(same) if model[i] is spec)
    there = here + step
    if 0 <= there < len(same):
        a, b = same[here], same[there]
        model[a], model[b] = model[b], model[a]
        _fx_rebuild_chain(self)
        _effects_sync(self)


class _TrackRow(QFrame):
    """One track in the list: its colour, name, how many effects, its meter."""

    clicked = Signal()

    def __init__(self, name, color, count, selected, parent=None):
        super().__init__(parent)
        self.setObjectName('audio_fx_track_row')
        self.setProperty('selected', selected)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(34)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 10, 0)
        row.setSpacing(8)
        stripe = QFrame(objectName='audio_fx_track_stripe')
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(f'background: {color}; border: 0;')
        row.addWidget(stripe)
        self.name = QLabel(name, objectName='audio_fx_track_name')
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.name, 1)
        self.count = QLabel(str(count) if count else '', objectName='audio_fx_track_count')
        self.count.setAlignment(Qt.AlignCenter)
        self.count.setVisible(bool(count))
        row.addWidget(self.count)
        self.meter = aw.LevelMeter()
        self.meter.setFixedWidth(70)
        row.addWidget(self.meter, 0, Qt.AlignVCenter)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()


def _fx_rebuild_tracks(self):
    _clear(self.audio_fx_tracks_layout)
    self._fx_rows = {}
    for key, name, color in _fx_tracks(self):
        count = len(_fx_chain(self, key))
        row = _TrackRow(name, color, count, key == self._fx_track)
        row.setToolTip(_('audio_panel.track_effects_count').format(count=count) if count else '')
        row.clicked.connect(lambda key=key: _fx_select_track(self, key))
        self.audio_fx_tracks_layout.addWidget(row)
        self._fx_rows[key] = row


class _Connector(QWidget):
    """The line between two devices: signal flows down."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(16)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        x = self.width() / 2
        color = QColor(184, 206, 224, 70)
        painter.setPen(QPen(color, 1.5))
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height() - 5))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(x - 4, self.height() - 6), QPointF(x + 4, self.height() - 6),
                                       QPointF(x, self.height() - 1)]))
        painter.end()


def _fx_rebuild_chain(self):
    _clear(self.audio_fx_chain_layout)
    self._fx_cards = []
    names = {key: name for key, name, _color in _fx_tracks(self)}
    self.audio_fx_chain_label.setText(_('audio_panel.chain_title').format(track=names.get(self._fx_track, '')).upper())
    chain = _fx_chain(self)
    self.audio_fx_chain_hint.setText(_('audio_panel.chain_hint') if chain else _('audio_panel.chain_empty'))
    for index, spec in enumerate(chain):
        if index:
            self.audio_fx_chain_layout.addWidget(_Connector())
        card = _DeviceCard(self, spec, index, len(chain))
        self.audio_fx_chain_layout.addWidget(card)
        self._fx_cards.append(card)
    if chain:
        self.audio_fx_chain_layout.addWidget(_Connector())
    self.audio_fx_chain_layout.addWidget(_fx_add_slot(self))


def _fx_add_slot(self):
    button = QPushButton('+   ' + _('audio_panel.add_effect').upper(), objectName='audio_fx_add_slot')
    button.setCursor(Qt.PointingHandCursor)
    menu = QMenu(button)
    menu.setToolTipsVisible(True)
    for effect_type in audio_effects.EFFECT_TYPES:
        action = menu.addAction(_effect_names()[effect_type], lambda t=effect_type: _fx_add(self, t))
        action.setToolTip(_(f'audio_panel.effect_{effect_type}_hint'))
    button.setMenu(menu)
    return button


def _fx_icon(name):
    """A header icon, with a faint version for when its button is disabled
    (Qt's own greying is barely visible on these dark cards). Built from
    pixmaps: an SVG icon only uses an added pixmap of the exact size asked."""
    source = QIcon(str(session.PATH_SUBTITLD_GRAPHICS / f'audio_fx_{name}.svg'))
    icon = QIcon()
    for size in (12, 16, 24, 32):
        normal = source.pixmap(QSize(size, size))
        faint = QPixmap(normal.size())
        faint.fill(Qt.transparent)
        painter = QPainter(faint)
        painter.setOpacity(0.22)
        painter.drawPixmap(0, 0, normal)
        painter.end()
        icon.addPixmap(normal, QIcon.Normal)
        icon.addPixmap(faint, QIcon.Disabled)
    return icon


def _fmt_time(seconds):
    seconds = max(0.0, float(seconds))
    return f'{int(seconds // 60)}:{seconds % 60:04.1f}'


class _DeviceCard(QFrame):
    """One effect in the chain: a header (on/off, name, time range, order,
    fold, remove) over its graph and knobs."""

    def __init__(self, window, spec, index, count, parent=None):
        super().__init__(parent)
        self.window_ref = window
        self.spec = spec
        self.kind = spec.get('type')
        self.setObjectName('audio_fx_device')
        self.setProperty('effect', self.kind)
        params = spec.setdefault('params', audio_effects.default_params(self.kind))
        self.knobs = {}
        self.graph = None

        card = QVBoxLayout(self)
        card.setContentsMargins(10, 6, 8, 10)
        card.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(4)
        self.power = aw.PowerButton()
        self.power.setChecked(bool(spec.get('enabled', True)))
        self.power.setToolTip(_('audio_panel.effect_power'))
        self.power.toggled.connect(self._set_enabled)
        header.addWidget(self.power)
        title = QLabel(_effect_names().get(self.kind, self.kind).upper(), objectName='audio_fx_device_title')
        header.addWidget(title)
        header.addStretch(1)
        self.range_chip = QPushButton(objectName='audio_fx_range_chip')
        self.range_chip.setCheckable(True)
        self.range_chip.setCursor(Qt.PointingHandCursor)
        self.range_chip.setToolTip(_('audio_panel.effect_time_range'))
        self.range_chip.toggled.connect(lambda on: self.range_editor.setVisible(on))
        header.addWidget(self.range_chip)
        for icon, step, tip, enabled in (('up', -1, 'audio_panel.effect_move_up', index > 0),
                                         ('down', 1, 'audio_panel.effect_move_down', index < count - 1)):
            button = self._icon_button(icon, _(tip))
            button.setEnabled(enabled)
            button.clicked.connect(lambda _c=False, s=step: _fx_move(window, spec, s))
            header.addWidget(button)
        self.fold = self._icon_button('down', _('audio_panel.effect_fold'))
        self.fold.clicked.connect(self._toggle_fold)
        header.addWidget(self.fold)
        remove = self._icon_button('remove', _('audio_panel.effect_remove'))
        remove.setProperty('role', 'remove')
        remove.clicked.connect(lambda: _fx_remove(window, spec))
        header.addWidget(remove)
        card.addLayout(header)

        self.body = QWidget(objectName='audio_fx_device_body')
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        if self.kind == 'eq':
            self._build_eq(body, params)
        elif self.kind in ('compressor', 'gate'):
            self._build_dynamics(body, params)
        self.range_editor = self._build_range_editor()
        body.addWidget(self.range_editor)
        card.addWidget(self.body)

        self._opacity = QGraphicsOpacityEffect(self.body)
        self.body.setGraphicsEffect(self._opacity)
        self._show_enabled()
        self._show_range()
        self.range_editor.setVisible(False)
        folded = spec.get('id') in window._fx_collapsed
        self.body.setHidden(folded)
        self.fold.setIcon(_fx_icon('right' if folded else 'down'))

    @staticmethod
    def _icon_button(icon, tooltip):
        button = QPushButton(objectName='audio_fx_icon_button')
        button.setIcon(_fx_icon(icon))
        button.setIconSize(QSize(12, 12))
        button.setFixedSize(20, 20)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(tooltip)
        return button

    def _sync(self):
        _effects_sync(self.window_ref)

    # -- header ------------------------------------------------------------------

    def _set_enabled(self, on):
        self.spec['enabled'] = bool(on)
        self._show_enabled()
        self._sync()

    def _show_enabled(self):
        # Bypassed devices stay editable, only dimmed — as in a DAW. The
        # effect is off otherwise: rendering through it softens the text.
        enabled = self.spec.get('enabled', True)
        self._opacity.setOpacity(1.0 if enabled else 0.4)
        self._opacity.setEnabled(not enabled)

    def _toggle_fold(self):
        folded = not self.body.isHidden()
        self.body.setHidden(folded)
        self.fold.setIcon(_fx_icon('right' if folded else 'down'))
        (self.window_ref._fx_collapsed.add if folded else self.window_ref._fx_collapsed.discard)(self.spec.get('id'))

    # -- equalizer ---------------------------------------------------------------

    def _build_eq(self, body, params):
        bands = params.setdefault('bands', audio_effects.default_params('eq')['bands'])
        self.graph = aw.EqGraph(params)
        self.graph.selected = min(self.window_ref._fx_band.get(self.spec.get('id'), 1), len(bands) - 1)
        self.graph.bandChanged.connect(lambda _i: (self._show_band(), self._sync()))
        self.graph.bandSelected.connect(self._select_band)
        body.addWidget(self.graph)

        selector = QHBoxLayout()
        selector.setSpacing(0)
        self.band_buttons = []
        names = {'lowshelf': _('audio_panel.band_low'), 'peak': _('audio_panel.band_mid'),
                 'highshelf': _('audio_panel.band_high')}
        for index, band in enumerate(bands):
            button = QPushButton(f'{index + 1}  {names.get(band.get("type"), band.get("type", "")).upper()}',
                                 objectName='audio_fx_band_button')
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty('position', 'first' if index == 0 else 'last' if index == len(bands) - 1 else 'middle')
            button.setStyleSheet(f'QPushButton:checked {{ color: {aw.BAND_COLORS[index % len(aw.BAND_COLORS)]}; }}')
            button.clicked.connect(lambda _c=False, i=index: self.graph.select(i))
            selector.addWidget(button)
            self.band_buttons.append(button)
        body.addLayout(selector)

        color = aw.EFFECT_COLORS['eq']
        self.knobs['freq'] = aw.Knob(_('audio_panel.knob_freq'), 20, 20000, 1000, log=True,
                                     fmt=aw.format_hz, unit='Hz', color=color)
        self.knobs['gain'] = aw.Knob(_('audio_panel.knob_gain'), -24, 24, 0, default=0,
                                     fmt=aw.format_db, unit='dB', color=color)
        self.knobs['q'] = aw.Knob(_('audio_panel.knob_q'), 0.1, 10, 1.0, log=True,
                                  fmt=lambda v: f'{v:.2f}', color=color)
        for key, knob in self.knobs.items():
            knob.valueChanged.connect(lambda value, k=key: self._set_band_value(k, value))
        body.addLayout(self._knob_row(self.knobs.values()))
        self._show_band()

    def _select_band(self, index):
        self.window_ref._fx_band[self.spec.get('id')] = index
        self._show_band()

    def _show_band(self):
        bands = self.spec['params']['bands']
        index = self.graph.selected
        band = bands[index]
        color = aw.BAND_COLORS[index % len(aw.BAND_COLORS)]
        defaults = audio_effects.default_params('eq')['bands']
        for button_index, button in enumerate(self.band_buttons):
            button.setChecked(button_index == index)
        for key, fallback in (('freq', 1000.0), ('gain', 0.0), ('q', 1.0)):
            knob = self.knobs[key]
            knob.color = QColor(color)
            if index < len(defaults):
                knob.default = float(defaults[index].get(key, fallback))
            knob.setValue(float(band.get(key, fallback)))
            knob.update()

    def _set_band_value(self, key, value):
        band = self.spec['params']['bands'][self.graph.selected]
        band[key] = round(value, 3)
        self.graph.update()
        self._sync()

    # -- compressor, gate ------------------------------------------------------------

    def _build_dynamics(self, body, params):
        defaults = audio_effects.default_params(self.kind)
        for key, value in defaults.items():
            params.setdefault(key, value)
        self.graph = aw.DynamicsGraph(self.kind, params)
        self.graph.thresholdChanged.connect(lambda value: (self.knobs['threshold_db'].setValue(value), self._sync()))
        body.addWidget(self.graph)
        color = aw.EFFECT_COLORS[self.kind]
        floor = -80 if self.kind == 'gate' else -60
        specs = [('threshold_db', _('audio_panel.knob_threshold'), floor, 0, False, lambda v: f'{v:.0f}', 'dB')]
        if self.kind == 'compressor':
            specs.append(('ratio', _('audio_panel.knob_ratio'), 1, 20, True, lambda v: f'{v:.1f}', ':1'))
        specs += [('attack_ms', _('audio_panel.knob_attack'), 0.1, 200, True, aw.format_ms, 'ms'),
                  ('release_ms', _('audio_panel.knob_release'), 5, 2000, True, aw.format_ms, 'ms')]
        if self.kind == 'compressor':
            specs.append(('makeup_db', _('audio_panel.knob_makeup'), 0, 24, False, lambda v: f'{v:.1f}', 'dB'))
        for key, label, low, high, log, fmt, unit in specs:
            knob = aw.Knob(label, low, high, float(params.get(key, defaults[key])), default=defaults[key],
                           log=log, fmt=fmt, unit=unit, color=color)
            knob.valueChanged.connect(lambda value, k=key: self._set_param(k, value))
            self.knobs[key] = knob
        body.addLayout(self._knob_row(self.knobs.values()))

    def _set_param(self, key, value):
        self.spec['params'][key] = round(value, 3)
        self.graph.update()
        self._sync()

    @staticmethod
    def _knob_row(knobs):
        row = QHBoxLayout()
        row.setSpacing(0)
        row.addStretch(1)
        for knob in knobs:
            row.addWidget(knob)
        row.addStretch(1)
        return row

    def show_live(self, processor, playing):
        if isinstance(self.graph, aw.DynamicsGraph):
            if processor is None or not playing:
                self.graph.set_live(None, 0.0)
            else:
                self.graph.set_live(processor.last_input_db, processor.last_reduction_db)

    # -- time range ----------------------------------------------------------------

    def _build_range_editor(self):
        editor = QFrame(objectName='audio_fx_range_editor')
        column = QVBoxLayout(editor)
        column.setContentsMargins(8, 6, 8, 8)
        column.setSpacing(6)
        modes = QHBoxLayout()
        modes.setSpacing(0)
        self.range_all = QPushButton(_('audio_panel.range_whole').upper(), objectName='audio_fx_band_button')
        self.range_some = QPushButton(_('audio_panel.range_part').upper(), objectName='audio_fx_band_button')
        for position, button in (('first', self.range_all), ('last', self.range_some)):
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty('position', position)
            modes.addWidget(button)
        self.range_all.clicked.connect(lambda: self._set_range(None))
        self.range_some.clicked.connect(self._range_from_fields)
        column.addLayout(modes)
        fields = QHBoxLayout()
        fields.setSpacing(6)
        self.range_from = QDoubleSpinBox()
        self.range_to = QDoubleSpinBox()
        for spin in (self.range_from, self.range_to):
            spin.setRange(0, 999999)
            spin.setDecimals(2)
            spin.setSuffix(' s')
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self._range_from_fields)
        rng = self.spec.get('range')
        self.range_from.blockSignals(True)
        self.range_to.blockSignals(True)
        self.range_from.setValue(float(rng[0]) if rng else 0.0)
        self.range_to.setValue(float(rng[1]) if rng else 10.0)
        self.range_from.blockSignals(False)
        self.range_to.blockSignals(False)
        from_label = QLabel(_('audio_panel.effect_from'))
        from_label.setProperty('class', 'description')
        to_label = QLabel(_('audio_panel.effect_to'))
        to_label.setProperty('class', 'description')
        fields.addWidget(from_label)
        fields.addWidget(self.range_from, 1)
        fields.addWidget(to_label)
        fields.addWidget(self.range_to, 1)
        column.addLayout(fields)
        self.range_selected = QPushButton(_('audio_panel.range_selected'), objectName='audio_fx_range_selected')
        self.range_selected.setCursor(Qt.PointingHandCursor)
        self.range_selected.clicked.connect(self._range_from_selection)
        column.addWidget(self.range_selected)
        return editor

    def _range_from_fields(self, *_args):
        low, high = self.range_from.value(), self.range_to.value()
        if high <= low:
            high = low + 0.5
        self._set_range([low, high])

    def _range_from_selection(self):
        selected = session.SUBTITLE.get('selected')
        if not selected:
            return
        self._set_range([float(selected.get('start', 0.0)), float(selected.get('end', 0.0))])

    def _set_range(self, rng):
        self.spec['range'] = rng
        if rng:
            for spin, value in ((self.range_from, rng[0]), (self.range_to, rng[1])):
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        self._show_range()
        self._sync()

    def _show_range(self):
        rng = self.spec.get('range')
        self.range_chip.setText(f'{_fmt_time(rng[0])} – {_fmt_time(rng[1])}' if rng else _('audio_panel.range_chip_all').upper())
        self.range_chip.setProperty('limited', bool(rng))
        self.range_chip.style().unpolish(self.range_chip)
        self.range_chip.style().polish(self.range_chip)
        self.range_all.setChecked(not rng)
        self.range_some.setChecked(bool(rng))
        for widget in (self.range_from, self.range_to):
            widget.setEnabled(bool(rng))
        self.range_selected.setEnabled(bool(session.SUBTITLE.get('selected')))


def _fx_tick_meters(self):
    engine = _fx_engine(self)
    playing = _fx_playing(self)
    for key, row in self._fx_rows.items():
        track = _engine_track(engine, key)
        row.meter.set_level(float(getattr(track, 'last_peak', 0.0) or 0.0) if playing and track is not None else 0.0)
    if not self._fx_cards:
        return
    chain = getattr(_engine_track(engine, self._fx_track), 'effect_chain', None)
    for card in self._fx_cards:
        processor = chain.processor_for(card.spec.get('id')) if chain is not None else None
        card.show_live(processor, playing)


# --------------------------------------------------------------------------- #
# Panel hooks                                                                  #
# --------------------------------------------------------------------------- #
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
    self.audio_record_engine_block.setLabel(_('audio_panel.engine').upper())
    self.audio_record_device_block.setLabel(_('audio_panel.device').upper())
    self.audio_record_help.setText(_('audio_panel.help'))
    self.audio_record_monitor_label.setText(_('audio_panel.input_monitor').upper())
    self.audio_fx_tracks_label.setText(_('audio_panel.tracks'))
    _fx_rebuild(self)
