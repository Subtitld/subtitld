import hashlib
import os
import secrets

from PySide6.QtWidgets import QStackedWidget, QWidget, QVBoxLayout, QHBoxLayout, QSpinBox, QPushButton, QLabel, QLineEdit, QSizePolicy, QColorDialog, QComboBox, QCheckBox, QApplication, QRadioButton, QButtonGroup
from PySide6.QtGui import QImage, QPixmap, QPainter, QPainterPath, QColor
from PySide6.QtCore import QThread, QObject, Signal, Qt, QSize

from subtitld.interface import utils
from subtitld.interface import left_panel
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import subtitles
from subtitld.modules import addons
from subtitld.modules import clone_ref
from subtitld.modules.addons.provider import TASK_TTS_SYNTHESIZE
# Engine logic (signals, threads, generate_speeches, stretch, ...) lives in
# the addons module now. We import the legacy alias `EdgeTTSEngine` so the
# UI panel classes below — which are still hosted on this engine via
# `EdgeTTSEngine.dubbingPanel` / `.speaker_panel` — keep working unchanged.
from subtitld.modules.addons.builtin.edge_tts_provider import EdgeTTSEngine


def _engine_combobox_current_id(combobox) -> str:
    """Read the active engine id from the labeled combobox. Items now show
    a translated display name with the raw id stored as user data — the
    text is for humans, the id is what we feed back into config and lookup
    maps."""
    inner = combobox.combobox
    data = inner.currentData()
    if data:
        return data
    return inner.currentText()


def _project_tts_language() -> str:
    """Base language code for the project, sent to TTS providers.

    Subtitld stores subtitle language as IETF-ish tags (`en-us`, `pt-br`,
    `zh-cn`, ...). For TTS providers we ship the *base* code only —
    `en`, `pt`, `zh` — because:

      - All shipping TTS provider manifests advertise base codes
        (`en`, `pt`, ...) in their `languages` list, not regions.
      - Region distinctions (en-US vs en-GB, pt-BR vs pt-PT) only
        affect accent, which is selected via the *voice* in TTS, not
        the language param.
      - It matches what each wrapper already does internally as a
        fallback when it sees a regioned code.

    Returns '' if no language is set on the project — providers that
    require a language will surface a `bad_params` error in that case,
    which is preferable to silently picking a wrong default.
    """
    raw = (session.SUBTITLE.get('language') or '').strip().lower()
    if not raw:
        return ''
    return raw.split('-', 1)[0]


def _engine_combobox_set_id(combobox, engine_id) -> bool:
    inner = combobox.combobox
    for i in range(inner.count()):
        if inner.itemData(i) == engine_id:
            inner.setCurrentIndex(i)
            return True
    return False


def _addon_display_name(provider) -> str:
    """Return a human-readable label for an addon provider, with locale
    override. Looks up `addons.<id>.display_name` first; falls back to the
    provider's own `display_name` (manifest) and finally its raw `id`."""
    addon_id = getattr(provider, 'id', '') or ''
    if addon_id:
        key = f'addons.{addon_id}.display_name'
        translated = _(key)
        if translated and translated != key:
            return translated
    return getattr(provider, 'display_name', None) or addon_id


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


# NOTE: the engine logic that used to live here (`_EdgeTTSSignals`,
# `_EdgeTTSSpeechThread`, `_EdgeTTSVoicesThread`, the `EdgeTTSEngine` static
# class) was extracted to `subtitld.modules.addons.builtin.edge_tts_provider`.
# `EdgeTTSEngine` is still importable as a back-compat alias — see the
# `from ... import EdgeTTSEngine` at the top of this file. The two UI panel
# classes below stay here because they're tightly coupled to the dubbing
# panel widgetry; we attach them to the legacy alias at the bottom of this
# file so callers like `EdgeTTSEngine.dubbingPanel()` keep working.


def _stable_seed_for_speaker(speaker_name: str) -> int:
    """Derive a deterministic 32-bit seed from the speaker name.

    Used by clone-capable TTS add-ons (currently qwen3-tts; future XTTS /
    F5-TTS / Piper-equivalent will pick this up via the additive `seed`
    param contract) to lock the sampler's RNG so a given speaker has a
    stable "voice fingerprint" across runs of "generate all speeches".
    Without it, autoregressive samplers produce a different waveform on
    every call even when text/voice/ref_audio/ref_text are byte-identical
    — which the user perceives as the same speaker drifting in character
    between regenerations.

    Why speaker_name alone (and not, say, the project file path or the
    subtitle text):

      * Stable across regenerations of the same speech (regenerating one
        subtitle twice produces the same audio) — that's the bug fix.
      * Stable across "generate all" runs (same speaker, same character).
      * Stable across projects — "Narrator" sounds like "Narrator"
        whether they appear in project A or B; users who want variety
        rename the speaker, which is the natural lever.
      * Different speakers → different seeds → different fingerprints,
        even with the same reference audio (rare but possible when the
        user has not yet set per-speaker references).

    We use sha256+truncate rather than Python's built-in `hash()` because
    PYTHONHASHSEED randomises the latter per process. 32-bit truncation
    fits torch.manual_seed comfortably (torch internally masks to
    uint64).
    """
    digest = hashlib.sha256((speaker_name or '').encode('utf-8')).digest()
    return int.from_bytes(digest[:4], 'big')


def _voices_clone_first(voices):
    """Reorder a provider's voice list so clone voices float to the top.

    Clone-capable voices (e.g. `xtts-clone`, `qwen3-clone`, `f5-clone`,
    `kokoro-clone`) are the marquee feature for offline TTS — Subtitld
    auto-injects the speaker's source audio as the reference, so picking
    a clone is the most-likely-correct first choice on any provider that
    exposes one. We surface them at the top of every voice combobox to
    make that path discoverable without hiding the fixed-voice list
    underneath.
    Stable within each group: preserves the manifest's original order
    of clone voices and non-clone voices respectively, so users who
    rely on a known position keep finding it within its group."""
    voices = list(voices or [])
    clones = [v for v in voices if clone_ref.voice_requires_ref_audio(v)]
    fixed = [v for v in voices if not clone_ref.voice_requires_ref_audio(v)]
    return clones + fixed


# --- Shared "fit dub to subtitle duration" toggle --------------------------
# Available on every TTS speaker panel. When on, a freshly generated clip is
# time-stretched to the subtitle's duration right after synthesis (see
# `dub_fit.maybe_fit_dub`), with the un-stretched original kept in the dub
# list. Stored per speaker in `session.SPEAKERS[name]['dubbing']['fit_to_subtitle']`.
def _install_fit_checkbox(widget):
    """Create + wire the per-speaker auto-fit checkbox and add it to the
    panel's layout. The label is set in each panel's `translate()`."""
    checkbox = QCheckBox()

    def _changed(checked):
        name = widget.property('speaker')
        if name and name in session.SPEAKERS:
            session.SPEAKERS[name].setdefault('dubbing', {})['fit_to_subtitle'] = bool(checked)
            session.set_unsaved(True)

    checkbox.toggled.connect(_changed)
    widget.fit_to_subtitle_checkbox = checkbox
    widget.layout().addWidget(checkbox)


def _sync_fit_checkbox(widget):
    """Reflect the current speaker's stored auto-fit choice into the box.
    Called from each panel's `update()` (which runs right after the panel's
    speaker property is set)."""
    checkbox = getattr(widget, 'fit_to_subtitle_checkbox', None)
    if checkbox is None:
        return
    name = widget.property('speaker')
    current = bool((session.SPEAKERS.get(name, {}) or {}).get('dubbing', {}).get('fit_to_subtitle', False))
    checkbox.blockSignals(True)
    checkbox.setChecked(current)
    checkbox.blockSignals(False)


class _EdgeTTSSpeakerPanel(QWidget):
    def __init__(widget, parent=None):
        # Hide briefly while parentless so we don't briefly appear as a
        # top-level window; the QStackedWidget reparents us via addWidget.
        super().__init__(parent)
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
        widget.scope_range_from_capture = QPushButton('⤓')
        widget.scope_range_from_capture.setObjectName('scope_range_capture_button')
        widget.scope_range_from_capture.clicked.connect(
            lambda: widget._capture_playback_position(widget.scope_range_from)
        )
        widget.scope_range_box.layout().addWidget(widget.scope_range_from_capture)

        widget.scope_range_to_label = QLabel()
        widget.scope_range_box.layout().addWidget(widget.scope_range_to_label)
        widget.scope_range_to = QLineEdit()
        widget.scope_range_to.setPlaceholderText('00:00:00.000')
        widget.scope_range_box.layout().addWidget(widget.scope_range_to, 1)
        widget.scope_range_to_capture = QPushButton('⤓')
        widget.scope_range_to_capture.setObjectName('scope_range_capture_button')
        widget.scope_range_to_capture.clicked.connect(
            lambda: widget._capture_playback_position(widget.scope_range_to)
        )
        widget.scope_range_box.layout().addWidget(widget.scope_range_to_capture)

        widget.scope_range.toggled.connect(lambda checked: widget.scope_range_box.setVisible(checked))

        _install_fit_checkbox(widget)

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
            session.set_unsaved(True)

    def voice_rate_changed(widget):
        value = widget.voice_rate.value()
        speaker_name = widget.property('speaker')
        if speaker_name and speaker_name in session.SPEAKERS:
            session.SPEAKERS[speaker_name]['dubbing']['rate'] = value
            session.set_unsaved(True)

    def voice_pitch_changed(widget):
        value = widget.voice_pitch.value()
        speaker_name = widget.property('speaker')
        if speaker_name and speaker_name in session.SPEAKERS:
            session.SPEAKERS[speaker_name]['dubbing']['pitch'] = value
            session.set_unsaved(True)

    def _capture_playback_position(widget, line_edit):
        """Stamp the current playback position into `line_edit`. Uses
        the same ``timecode.Timecode('1000', ..., fractional=True)``
        spelling the playercontrols header shows, so the captured text
        round-trips through `_parse_timecode_input` exactly."""
        from subtitld.modules import timecode
        pos = float(session.SUBTITLE.get('position', 0) or 0)
        line_edit.setText(str(timecode.Timecode('1000', start_seconds=pos, fractional=True)))

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
        speaker_voice = speaker_dubbing.get('voice', '')
        speaker_rate = speaker_dubbing.get('rate', 0)
        speaker_pitch = speaker_dubbing.get('pitch', 0)
        speeches_to_generate = []
        for subtitle in scoped:
            overrides = subtitle.setdefault('dubbing_options', {})
            subtitle['locked'] = True
            voice_id = overrides.get('voice') or speaker_voice
            # Pin engine+voice (and rate/pitch fallbacks) on each targeted
            # subtitle BEFORE dispatching — so a mid-batch crash or per-item
            # failure doesn't lose the user's choice. See the matching
            # comment in _GenericTTSSpeakerPanel.generate_all_speeches_button_clicked.
            overrides['engine'] = 'edge-tts'
            overrides['voice'] = voice_id
            # The speaker batch applies the panel's CURRENT rate/pitch to every
            # targeted subtitle. Overwrite (not setdefault) so that changing the
            # rate and re-running actually re-applies it — a stale per-subtitle
            # override from an earlier run must not shadow the new value.
            overrides['rate'] = speaker_rate
            overrides['pitch'] = speaker_pitch
            speeches_to_generate.append({
                'uid': secrets.token_hex(4),
                'text': subtitle['text'],
                'speaker': speaker_name,
                'start': subtitle['start'],
                'end': subtitle['end'],
                'voice': voice_id,
                'rate': speaker_rate,
                'pitch': speaker_pitch,
            })
        if speeches_to_generate:
            session.set_unsaved(True)
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

        _sync_fit_checkbox(widget)

    def translate(widget):
        widget.voice_combobox.setLabel(_('subtitles_panel_widget_dubbing.voice'))
        widget.voice_rate_label.setText(_('subtitles_panel_widget_dubbing.rate'))
        widget.voice_pitch_label.setText(_('subtitles_panel_widget_dubbing.pitch'))
        widget.fit_to_subtitle_checkbox.setText(_('subtitles_panel_widget_dubbing.fit_to_subtitle'))
        widget.generate_all_speeches_button.setText(_('subtitles_panel_widget_dubbing.generate_all_speeches'))
        widget.scope_label.setText(_('panel_scope.label'))
        widget.scope_all.setText(_('panel_scope.all'))
        widget.scope_selected.setText(_('panel_scope.selected'))
        widget.scope_range.setText(_('panel_scope.range'))
        widget.scope_range_from_label.setText(_('panel_scope.from'))
        widget.scope_range_to_label.setText(_('panel_scope.to'))
        for btn in (widget.scope_range_from_capture, widget.scope_range_to_capture):
            btn.setToolTip(_('panel_scope.capture_tooltip'))

class _EdgeTTSDubbingPanel(QWidget):
    def __init__(widget, parent=None):
        super().__init__(parent)
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
        # Prefer the most-recent clip's recorded params — when a clip exists
        # the controls should reflect what *that* take used, not whatever
        # the speaker default happens to be right now.
        last_dub = (selected.get('dubbing') or [{}])[0]

        effective_voice = (
            last_dub.get('voice')
            or overrides.get('voice')
            or speaker_dubbing.get('voice', '')
        )
        if 'rate' in last_dub:
            effective_rate = last_dub.get('rate', 0)
        else:
            effective_rate = overrides.get('rate', speaker_dubbing.get('rate', 0))
        if 'pitch' in last_dub:
            effective_pitch = last_dub.get('pitch', 0)
        else:
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


# The provider connects its own speech_ready / speech_error to its host-side
# `_on_speech_ready` / `_on_speech_error` callbacks in its __init__, so we no
# longer need the connect calls that lived here. We DO still attach the two
# UI panel classes onto the legacy `EdgeTTSEngine` shim so callers like
# `EdgeTTSEngine.dubbingPanel()` and `EdgeTTSEngine.speaker_panel(...)` find
# them at the same names as before.
EdgeTTSEngine.dubbingPanel = _EdgeTTSDubbingPanel
EdgeTTSEngine.speaker_panel = _EdgeTTSSpeakerPanel


# ---------------------------------------------------------------------------
# Generic per-provider panels
# ---------------------------------------------------------------------------
# These render a minimal voice/rate/pitch UI for any TTS provider that isn't
# Edge TTS. They drive the provider's `generate_speeches([...])` directly.
# Add-ons with a richer per-engine UI (voice cloning reference audio for
# Coqui XTTS, etc.) can be enhanced later by reading `provider.config_schema`.
# ---------------------------------------------------------------------------
class _GenericTTSDubbingPanel(QWidget):
    """Per-subtitle dub controls for an arbitrary TTS provider."""

    def __init__(widget, provider, parent=None):
        super().__init__(parent)
        widget.parent = parent
        widget.provider = provider
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 0)
        widget.layout().setSpacing(5)
        widget.setProperty('dubbing_engine', provider.id)
        widget.setProperty('class', 'transparent_panel')

        widget.voice_combobox = utils.LabeledComboBox()
        widget._populate_voices()
        widget.voice_combobox.activated.connect(lambda: widget.voice_combobox_changed())
        widget.layout().addWidget(widget.voice_combobox)

        # Rate / pitch sliders are intentionally Edge-only. None of the
        # neural add-on engines (Kokoro, Coqui XTTS, Qwen3-TTS, F5-TTS,
        # Piper) expose a meaningful "rate %" parameter on the request —
        # Kokoro takes a `speed` float that mostly hurts quality, XTTS
        # ignores rate entirely, and the clone-capable engines preserve
        # the reference clip's prosody. Surfacing the sliders for these
        # engines was confusing in practice (users tweaked them, got no
        # audible change, blamed the add-on). The request envelope still
        # carries `rate=0`/`pitch=0` defaults from speaker_dubbing so the
        # wire protocol shape is unchanged.

        widget.generate_speech_button = QPushButton()
        widget.generate_speech_button.clicked.connect(lambda: widget.generate_speech_button_clicked())
        widget.layout().addWidget(widget.generate_speech_button, 0, Qt.AlignRight)

        try:
            provider.voices_updated.connect(widget._populate_voices)
        except Exception:
            pass

        widget.update_callback = widget.update
        widget.translate_callback = widget.translate

    def _populate_voices(widget):
        # Stash the currently-selected voice ID (not the label) so we can
        # restore it after the rebuild. Without this, projects that already
        # have a voice id in `dubbing_options` get reset on every refresh.
        current_id = widget.voice_combobox.combobox.currentData()
        widget.voice_combobox.clear()
        for v in _voices_clone_first(widget.provider.list_voices()):
            label = v.get('display_name') or v.get('id') or v.get('name') or str(v)
            voice_id = v.get('id') or label
            # CRITICAL: store voice ID as userData. We display labels but the
            # add-on protocol speaks IDs; previously we wrote the label into
            # `dubbing_options.voice` and the add-on then failed parsing it
            # (e.g. "Amy (US English, medium)" is not a `<locale>-<name>-<quality>`).
            widget.voice_combobox.combobox.addItem(label, voice_id)
        if current_id is not None:
            idx = widget.voice_combobox.combobox.findData(current_id)
            if idx >= 0:
                widget.voice_combobox.combobox.setCurrentIndex(idx)

    def voice_combobox_changed(widget):
        selected = session.SUBTITLE.get('selected')
        if selected is None:
            return
        # Persist the voice ID, not the display label.
        voice_id = widget.voice_combobox.combobox.currentData()
        if voice_id is None:
            return
        selected.setdefault('dubbing_options', {})['voice'] = voice_id
        session.set_unsaved()

    def generate_speech_button_clicked(widget):
        selected = session.SUBTITLE.get('selected')
        if not selected:
            return
        speaker_name = selected.get('speaker', 'A')
        speaker_dubbing = session.SPEAKERS.get(speaker_name, {}).get('dubbing', {})
        overrides = selected.get('dubbing_options', {})
        # Resolve voice ID with fallback to the combobox's current data — handles
        # the case where the user clicks generate without first triggering an
        # `activated` (combobox initialised but never re-selected).
        voice_id = (overrides.get('voice')
                    or speaker_dubbing.get('voice', '')
                    or widget.voice_combobox.combobox.currentData()
                    or '')
        # Cross-provider stale-id guard. `speaker_dubbing['voice']` and
        # `overrides['voice']` persist through engine combobox switches
        # (we deliberately don't clear them on switch — round-tripping
        # back to a previous engine should restore the previous voice
        # selection without re-typing). The downside is that a voice id
        # from a previous provider (e.g. `kokoro-pf-dora` after switching
        # to coqui-xtts) would otherwise reach the add-on and bounce
        # with `unsupported_voice`. The combobox is the source of truth
        # for "voice ids the current provider knows about" — it was just
        # repopulated by `voices_updated`. If our resolved id is missing
        # from `findData`, substitute the combobox's currentData and
        # write it back so the next request stays consistent.
        if voice_id and widget.voice_combobox.combobox.findData(voice_id) < 0:
            fallback_id = widget.voice_combobox.combobox.currentData() or ''
            if fallback_id:
                voice_id = fallback_id
                selected.setdefault('dubbing_options', {})['voice'] = voice_id
                if speaker_name in session.SPEAKERS:
                    session.SPEAKERS[speaker_name].setdefault('dubbing', {})['voice'] = voice_id
                session.set_unsaved(True)
        selected['locked'] = True
        request = {
            'uid': secrets.token_hex(4),
            'text': selected['text'],
            'speaker': speaker_name,
            'start': selected['start'],
            'end': selected['end'],
            'voice': voice_id,
            # Some providers (Coqui XTTS multilingual, Qwen3-TTS) need the
            # language to drive the model's text-frontend. Edge TTS
            # ignores it (voice already encodes language) but accepts the
            # field harmlessly. See `_project_tts_language` for stripping
            # rules.
            'language': _project_tts_language(),
            'rate': overrides.get('rate', speaker_dubbing.get('rate', 0)),
            'pitch': overrides.get('pitch', speaker_dubbing.get('pitch', 0)),
            # Stable per-speaker seed for autoregressive samplers. Addons
            # that don't know about `seed` ignore it (additive contract);
            # qwen3-tts wires it to `torch.manual_seed` before each
            # generation so the speaker's voice fingerprint stays
            # consistent across regenerations. See
            # `_stable_seed_for_speaker` for rationale.
            'seed': _stable_seed_for_speaker(speaker_name),
        }
        # If the selected voice declares it needs `voice_ref_audio`
        # (manifest `requires: ['voice_ref_audio']` or `clone: true`),
        # auto-attach the speaker's source audio as the reference. This
        # is the *only* sensible behavior for clone voices — there's no
        # manual reference-WAV upload UI in Subtitld today, and asking
        # the user to flip a separate "use original voice" toggle just
        # for the request to succeed was confusing in practice (failed
        # with `bad_params` if the toggle was off, e.g. qwen3-clone or
        # xtts-clone). The per-subtitle override matches what the
        # speaker-level batch generator does.
        if clone_ref.voice_id_requires_ref_audio(widget.provider, voice_id):
            ref_path, ref_text = clone_ref.extract_speaker_reference_with_text(speaker_name)
            if ref_path:
                request['voice_ref_audio'] = ref_path
                # Pair ref_text with ref_audio when available — clone-
                # capable engines fall back to "x-vector only" when
                # ref_text is missing, which leaks the model's training-
                # set accent into the output (e.g. English accent on
                # Portuguese clones). The subtitle text is exactly the
                # transcript for the audio span, so forwarding it costs
                # nothing and unlocks ICL mode in the addon.
                #
                # The `skip_ref_text` user opt-out (set per-addon in the
                # Configure dialog) suppresses this — for users who
                # prefer the addon's own auto-transcription path.
                if ref_text and not clone_ref.should_skip_ref_text(widget.provider.id):
                    request['voice_ref_text'] = ref_text
        widget.provider.generate_speeches([request])
        timeline_widget = getattr(widget.window(), 'timeline_widget', None)
        if timeline_widget is not None:
            timeline_widget.update()

    def update(widget):
        selected = session.SUBTITLE.get('selected')
        if not selected:
            return
        speaker_dubbing = session.SPEAKERS.get(selected.get('speaker', 'A'), {}).get('dubbing', {})
        overrides = selected.get('dubbing_options', {})
        # Prefer the most-recent clip's recorded params over the speaker
        # defaults / per-subtitle overrides. Reflects what the existing
        # take used so the controls match reality.
        last_dub = (selected.get('dubbing') or [{}])[0]

        target_id = (
            last_dub.get('voice')
            or overrides.get('voice')
            or speaker_dubbing.get('voice', '')
        )
        widget.voice_combobox.combobox.blockSignals(True)
        # Resolve the stored voice ID to a combobox row. Falls back to row 0 if
        # the saved voice no longer exists in this provider's list.
        idx = widget.voice_combobox.combobox.findData(target_id) if target_id else -1
        if idx >= 0:
            widget.voice_combobox.combobox.setCurrentIndex(idx)
        elif widget.voice_combobox.combobox.count() > 0:
            widget.voice_combobox.combobox.setCurrentIndex(0)
        widget.voice_combobox.combobox.blockSignals(False)

    def translate(widget):
        widget.voice_combobox.setLabel(_('subtitles_panel_widget_dubbing.voice'))
        widget.generate_speech_button.setText(_('subtitles_panel_widget_dubbing.generate_speech'))


class _GenericTTSSpeakerPanel(QWidget):
    """Per-speaker dubbing config for an arbitrary TTS provider — minimal
    voice picker + 'generate all speeches' button. Used when no engine-
    specific UI exists."""

    def __init__(widget, provider, parent=None):
        super().__init__(parent)
        widget.parent = parent
        widget.provider = provider
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(5, 5, 5, 5)
        widget.layout().setSpacing(5)
        widget.setProperty('dubbing_engine', provider.id)
        widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)
        widget.setProperty('class', 'transparent_panel')

        widget.voice_combobox = utils.LabeledComboBox()
        widget._populate_voices()
        widget.voice_combobox.activated.connect(lambda: widget.voice_combobox_changed())
        widget.layout().addWidget(widget.voice_combobox)

        # No manual voice-clone toggle: when the user picks a voice that
        # the manifest flags as `requires: ['voice_ref_audio']` (e.g.
        # `xtts-clone`, `qwen3-clone`, `f5-clone`), `generate_speeches`
        # automatically extracts a snippet of the speaker's source audio
        # via `clone_ref.extract_speaker_reference` and passes it as
        # `voice_ref_audio`. There's no other reasonable behavior for a
        # clone voice in Subtitld — and forcing the user to enable a
        # separate checkbox was confusing in practice (lines would fail
        # with `bad_params` if the toggle was off).

        # Optional "slow speech" toggle. Providers that have only a
        # binary slow/normal speed (gTTS) set
        # ``provider.supports_slow_speech = True``; this surfaces a
        # checkbox here and stores the choice as
        # ``speaker['dubbing']['rate'] = -1`` (slow) or ``0`` (normal).
        # Providers with continuous rate control (edge-tts) use their
        # own bespoke panel with a spinner instead and never touch this.
        widget.slow_speech_checkbox = None
        if getattr(provider, 'supports_slow_speech', False):
            widget.slow_speech_checkbox = QCheckBox()
            widget.slow_speech_checkbox.toggled.connect(
                lambda checked: widget._slow_speech_changed(checked)
            )
            widget.layout().addWidget(widget.slow_speech_checkbox)

        _install_fit_checkbox(widget)

        widget.generate_all_speeches_button = QPushButton()
        widget.generate_all_speeches_button.clicked.connect(lambda: widget.generate_all_speeches_button_clicked())
        widget.layout().addWidget(widget.generate_all_speeches_button, 0, Qt.AlignRight)

        try:
            provider.voices_updated.connect(widget._populate_voices)
        except Exception:
            pass

    def _populate_voices(widget):
        current_id = widget.voice_combobox.combobox.currentData()
        widget.voice_combobox.clear()
        for v in _voices_clone_first(widget.provider.list_voices()):
            label = v.get('display_name') or v.get('id') or v.get('name') or str(v)
            voice_id = v.get('id') or label
            widget.voice_combobox.combobox.addItem(label, voice_id)
        if current_id is not None:
            idx = widget.voice_combobox.combobox.findData(current_id)
            if idx >= 0:
                widget.voice_combobox.combobox.setCurrentIndex(idx)

    def voice_combobox_changed(widget):
        speaker_name = widget.property('speaker')
        voice_id = widget.voice_combobox.combobox.currentData()
        if speaker_name and speaker_name in session.SPEAKERS and voice_id is not None:
            session.SPEAKERS[speaker_name].setdefault('dubbing', {})['voice'] = voice_id
            session.set_unsaved(True)

    def _slow_speech_changed(widget, checked):
        """Persist the slow-speech toggle onto the speaker's dubbing
        config. We reuse the existing ``rate`` field (slow → ``-1``,
        normal → ``0``) so providers don't need a separate plumbing
        path; gTTS interprets ``rate < 0`` as ``slow=True`` in its
        synthesis call."""
        speaker_name = widget.property('speaker')
        if not (speaker_name and speaker_name in session.SPEAKERS):
            return
        session.SPEAKERS[speaker_name].setdefault('dubbing', {})['rate'] = (
            -1 if checked else 0
        )
        session.set_unsaved(True)

    def setProperty(widget, name, value):
        """Override so the checkbox can pick up the speaker's current
        slow setting when the panel is shown for a different speaker
        (e.g. via the speaker list). Qt's QStackedWidget reparents the
        same panel instance across speakers, so we sync on each
        ``setProperty('speaker', ...)``."""
        super().setProperty(name, value)
        if name == 'speaker':
            if widget.slow_speech_checkbox is not None:
                cur = bool(
                    session.SPEAKERS.get(value, {}).get('dubbing', {}).get('rate', 0) < 0
                )
                widget.slow_speech_checkbox.blockSignals(True)
                widget.slow_speech_checkbox.setChecked(cur)
                widget.slow_speech_checkbox.blockSignals(False)
            _sync_fit_checkbox(widget)

    def generate_all_speeches_button_clicked(widget):
        speaker_name = widget.property('speaker')
        speaker_dubbing = session.SPEAKERS.get(speaker_name, {}).get('dubbing', {})
        # Same fallback as the per-subtitle panel: if neither the per-subtitle
        # override nor the speaker default has been set yet, take whatever
        # the combobox is currently showing.
        speaker_voice = (speaker_dubbing.get('voice', '')
                         or widget.voice_combobox.combobox.currentData()
                         or '')
        # Cross-provider stale-id guard — see the matching block in
        # `_GenericTTSDubbingPanel.generate_speech_button_clicked` for
        # the rationale. The speaker-level default can be stale after an
        # engine switch; the combobox is freshly repopulated and is the
        # authoritative list of valid ids for the current provider.
        combobox_data = widget.voice_combobox.combobox.currentData() or ''
        if speaker_voice and widget.voice_combobox.combobox.findData(speaker_voice) < 0 and combobox_data:
            speaker_voice = combobox_data
            if speaker_name in session.SPEAKERS:
                session.SPEAKERS[speaker_name].setdefault('dubbing', {})['voice'] = speaker_voice
                session.set_unsaved(True)
        # Resolve the clone reference once for the whole batch — extraction
        # is cached on the speaker_slug, so doing it here vs. per-subtitle
        # is equivalent but skips the cache hash check on every iteration.
        # Per-subtitle voice overrides could in theory point at a non-clone
        # voice while the speaker default is a clone voice (or vice-versa),
        # so we recompute the requirement per entry below — the extraction
        # itself is still cached, no extra ffmpeg call.
        cached_clone_ref: list[tuple[str | None, str | None]] = []  # 0/1-element holder
        def _get_clone_ref():
            if not cached_clone_ref:
                cached_clone_ref.append(
                    clone_ref.extract_speaker_reference_with_text(speaker_name)
                )
            return cached_clone_ref[0]

        speeches_to_generate = []
        # Resolve rate/pitch once so we can write them onto each subtitle's
        # dubbing_options alongside engine+voice. Mirroring per-subtitle
        # generation: subtitle overrides win, then speaker default, then 0.
        speaker_rate = speaker_dubbing.get('rate', 0)
        speaker_pitch = speaker_dubbing.get('pitch', 0)
        for subtitle in session.SUBTITLE.get('segments', []) or []:
            if subtitle.get('speaker', 'A') != speaker_name:
                continue
            overrides = subtitle.setdefault('dubbing_options', {})
            subtitle['locked'] = True
            voice_id = overrides.get('voice') or speaker_voice
            # Per-subtitle override can be stale across an engine switch
            # (same reason as the speaker-level guard above). If the
            # override is unknown to the current provider, fall through
            # to the already-validated speaker_voice and persist the
            # correction so the per-subtitle row stops carrying a dead id.
            if (voice_id
                    and widget.voice_combobox.combobox.findData(voice_id) < 0
                    and speaker_voice):
                voice_id = speaker_voice
                if 'voice' in overrides:
                    overrides['voice'] = voice_id
                    session.set_unsaved(True)
            # Persist engine+voice on every targeted subtitle BEFORE
            # firing the batch. Two reasons: (1) if generation crashes
            # partway, the unprocessed subtitles still carry the choice
            # the user made — `regenerate_dub_for_selected` and the
            # subtitle-change auto-switch in `left_panel_dubbing.update`
            # both read this override, so the user can recover without
            # re-picking engine/voice in the panel; (2) it keeps the
            # subtitle's dubbing_options consistent with what the
            # speech_ready callback will write into `dubbing[0].engine`
            # once each item succeeds.
            overrides['engine'] = widget.provider.id
            overrides['voice'] = voice_id
            # The speaker batch applies the panel's CURRENT rate/pitch to every
            # targeted subtitle. Overwrite (not setdefault) so re-running after
            # changing the rate — or toggling gTTS "slow speech" back off (rate
            # 0) — actually re-applies it; a stale per-subtitle override from an
            # earlier run must not shadow the new value.
            overrides['rate'] = speaker_rate
            overrides['pitch'] = speaker_pitch
            session.set_unsaved(True)
            entry = {
                'uid': secrets.token_hex(4),
                'text': subtitle['text'],
                'speaker': speaker_name,
                'start': subtitle['start'],
                'end': subtitle['end'],
                'voice': voice_id,
                # Project-level language; same rationale as in
                # `generate_speech_button_clicked`. Cached above the loop
                # would shave a dict lookup but the readability of computing
                # it inline (next to the rest of the request shape) wins.
                'language': _project_tts_language(),
                'rate': speaker_rate,
                'pitch': speaker_pitch,
                # Stable per-speaker seed — see the single-shot path's
                # comment and `_stable_seed_for_speaker` for why. We
                # compute it inside the loop instead of hoisting because
                # the cost is trivial (one sha256 over a short string)
                # and inlining keeps the request-shape readable in one
                # place rather than scattering setup above the loop.
                'seed': _stable_seed_for_speaker(speaker_name),
            }
            # Auto-attach clone reference whenever the resolved voice
            # requires it. See `_GenericTTSDubbingPanel.generate_speech_button_clicked`
            # for the rationale — same policy applied across batch and
            # per-subtitle paths.
            if clone_ref.voice_id_requires_ref_audio(widget.provider, voice_id):
                ref_path, ref_text = _get_clone_ref()
                if ref_path:
                    entry['voice_ref_audio'] = ref_path
                    # Pair ref_text with ref_audio — see the same logic
                    # in `_GenericTTSDubbingPanel.generate_speech_button_clicked`
                    # for why this is mandatory for accent fidelity on
                    # non-English source material. Honour the same
                    # per-addon `skip_ref_text` opt-out the per-subtitle
                    # path uses, so the user's preference is consistent
                    # across single-click and batch generation.
                    if ref_text and not clone_ref.should_skip_ref_text(widget.provider.id):
                        entry['voice_ref_text'] = ref_text
            speeches_to_generate.append(entry)
        if speeches_to_generate:
            widget.provider.generate_speeches(speeches_to_generate)

    def update(widget):
        speaker_name = widget.property('speaker')
        dubbing = session.SPEAKERS.get(speaker_name, {}).get('dubbing') if speaker_name else None
        if not isinstance(dubbing, dict):
            dubbing = {}
        target_id = dubbing.get('voice', '')
        widget.voice_combobox.combobox.blockSignals(True)
        idx = widget.voice_combobox.combobox.findData(target_id) if target_id else -1
        if idx >= 0:
            widget.voice_combobox.combobox.setCurrentIndex(idx)
        elif widget.voice_combobox.combobox.count() > 0:
            widget.voice_combobox.combobox.setCurrentIndex(0)
        widget.voice_combobox.combobox.blockSignals(False)

        _sync_fit_checkbox(widget)

    def translate(widget):
        widget.voice_combobox.setLabel(_('subtitles_panel_widget_dubbing.voice'))
        widget.generate_all_speeches_button.setText(_('subtitles_panel_widget_dubbing.generate_all_speeches'))
        widget.fit_to_subtitle_checkbox.setText(_('subtitles_panel_widget_dubbing.fit_to_subtitle'))
        if widget.slow_speech_checkbox is not None:
            widget.slow_speech_checkbox.setText(
                _('subtitles_panel_widget_dubbing.slow_speech')
            )


class _ProviderEngineFacade:
    """Adapter that lets the speaker UI treat a `Provider` as an engine in
    the legacy `left_panel_speakers_list_of_available_dubbing_engine` map.
    Implements `dubbingPanel()` and `speaker_panel(...)` as factory methods.
    """

    def __init__(self, provider):
        self.provider = provider
        self.signals = provider  # provider already exposes speech_ready/error/voices_updated

    def dubbingPanel(self, *args, **kwargs):
        return _GenericTTSDubbingPanel(self.provider, *args, **kwargs)

    def speaker_panel(self, *args, **kwargs):
        return _GenericTTSSpeakerPanel(self.provider, *args, **kwargs)

    def generate_speeches(self, text_list):
        self.provider.generate_speeches(text_list)

    def stretch(self, subtitle, ratio):
        return self.provider.stretch(subtitle, ratio)


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
    self.left_panel_dubbing_no_subtitle_label.setObjectName('left_panel_dubbing_no_subtitle_label')
    self.left_panel_dubbing_no_subtitle_label.setWordWrap(True)
    self.left_panel_dubbing_no_subtitle_label.setAlignment(Qt.AlignCenter)
    left_panel_dubbing_panel.layout().addWidget(self.left_panel_dubbing_no_subtitle_label, 1)

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

    self.left_panel_dubbing_subtitle_settings.layout().addWidget(self.global_panel_dubbing_tabwidget)
    left_panel_dubbing_panel.layout().addStretch()

    # Populate dynamically from the add-on manager. Edge TTS keeps its
    # bespoke panel UI (richer, with rate/pitch/preview) — every other
    # registered TTS provider gets the generic schema-driven panel.
    _populate_engines(self)

    # Rebuild the engine combobox + stacked panels whenever the registry
    # changes — runtime install/uninstall via the add-ons panel needs to
    # show up in the dub UI without an app restart.
    manager = addons.get_manager()
    manager.providers_changed.connect(lambda: _populate_engines(self))

    update(self)


def _populate_engines(self):
    """(Re)build the engine combobox + stacked widget from the current
    set of registered TTS providers. Idempotent — safe to call on
    `providers_changed` as many times as the manager fires."""
    if not hasattr(self, 'left_panel_dubbing_engine_combobox'):
        return  # `load()` hasn't finished yet
    previous_selection = _engine_combobox_current_id(self.left_panel_dubbing_engine_combobox)

    # Block the combobox's `activated` signal so the rebuild doesn't
    # trip `left_panel_dubbing_engine_combobox_activated` and overwrite
    # `selected_engine` in CONFIG with whatever empty/transient value
    # the combobox momentarily has.
    self.left_panel_dubbing_engine_combobox.combobox.blockSignals(True)
    try:
        self.left_panel_dubbing_engine_combobox.clear()

        # Tear down whatever's in the stacked widget before re-adding,
        # otherwise we'd accumulate orphan panels each time.
        while self.global_panel_dubbing_tabwidget.count():
            widget = self.global_panel_dubbing_tabwidget.widget(0)
            self.global_panel_dubbing_tabwidget.removeWidget(widget)
            widget.deleteLater()

        # Refresh the speaker-side facade map too — stale entries here
        # would leak after an uninstall.
        self.left_panel_speakers_list_of_available_dubbing_engine.clear()

        manager = addons.get_manager()
        for provider in manager.providers_for_task(TASK_TTS_SYNTHESIZE):
            if provider.id == 'edge-tts':
                engine = EdgeTTSEngine
                self.global_panel_dubbing_tabwidget.addWidget(engine.dubbingPanel(self.global_panel_dubbing_tabwidget))
            else:
                engine = _ProviderEngineFacade(provider)
                self.global_panel_dubbing_tabwidget.addWidget(engine.dubbingPanel(self.global_panel_dubbing_tabwidget))
            self.left_panel_speakers_list_of_available_dubbing_engine[provider.id] = engine
            # Show the human-readable display name (with locale override)
            # but keep the raw provider id behind it as user data so config
            # / lookup keep working with stable ids.
            self.left_panel_dubbing_engine_combobox.combobox.addItem(_addon_display_name(provider), provider.id)

        # Try to restore selection: previous → CONFIG'd default →
        # whatever's first. Match by user data (provider id), not text.
        target = previous_selection or session.CONFIG.get('dubbing', {}).get('selected_engine', '')
        if target:
            _engine_combobox_set_id(self.left_panel_dubbing_engine_combobox, target)
    finally:
        self.left_panel_dubbing_engine_combobox.combobox.blockSignals(False)

    # Make sure the visible stacked panel matches the (possibly new)
    # current combobox text. Without this the stack stays on whatever
    # index was current before clear(), which after a rebuild may be a
    # different engine than the combobox label suggests.
    global_panel_dubbing_tabwidget_update(self)


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
    session.CONFIG['dubbing']['selected_engine'] = _engine_combobox_current_id(self.left_panel_dubbing_engine_combobox)
    global_panel_dubbing_tabwidget_update(self)


def update(self):
    enabled = session.CONFIG['dubbing'].get('enabled', False)
    self.left_panel_dubbing_enable_checkbox.setChecked(enabled)
    _engine_combobox_set_id(self.left_panel_dubbing_engine_combobox, session.CONFIG['dubbing'].get('selected_engine', 'edge-tts'))

    has_selection = session.SUBTITLE.get('selected') is not None
    self.left_panel_dubbing_no_subtitle_label.setVisible(not has_selection)
    self.left_panel_dubbing_subtitle_settings.setVisible(has_selection)

    # Auto-switch the engine combobox to the engine pinned on the selected
    # subtitle's `dubbing_options` (or recorded on its first dub). Without
    # this, after "Generate all speeches" partially failed, the user would
    # have to manually re-pick the same engine for every leftover subtitle
    # before "Generate speech" would target the right provider. The pinned
    # value is only honored if its engine is currently available — falls
    # through to the global default if the addon has been uninstalled.
    selected = session.SUBTITLE.get('selected') if has_selection else None
    if selected is not None:
        pinned_engine = (selected.get('dubbing_options') or {}).get('engine')
        if not pinned_engine:
            first_dub = (selected.get('dubbing') or [{}])[0] if selected.get('dubbing') else {}
            pinned_engine = first_dub.get('engine')
        if pinned_engine and self.left_panel_dubbing_engine_combobox.combobox.findData(pinned_engine) >= 0:
            _engine_combobox_set_id(self.left_panel_dubbing_engine_combobox, pinned_engine)
            session.CONFIG['dubbing']['selected_engine'] = pinned_engine

    selected_engine = _engine_combobox_current_id(self.left_panel_dubbing_engine_combobox)
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
    # Translate ALL engine panels — not just the current one. Otherwise
    # labels stay blank on engines that weren't selected at the moment of
    # the (single) startup `translate()` pass; switching to them later
    # would surface untranslated controls.
    for widget in self.global_panel_dubbing_tabwidget.findChildren(QWidget):
        if widget.property('dubbing_engine') and hasattr(widget, 'translate_callback'):
            widget.translate_callback()



def global_panel_dubbing_tabwidget_update(self):
    selected_id = _engine_combobox_current_id(self.left_panel_dubbing_engine_combobox)
    for widget in self.global_panel_dubbing_tabwidget.findChildren(QWidget):
        if widget.property('dubbing_engine') == selected_id:
            self.global_panel_dubbing_tabwidget.setCurrentWidget(widget)
            widget.update_callback()
            # Re-translate when switching engines — idempotent, ensures
            # labels are present even on engines that became visible after
            # the initial `translate()` pass.
            if hasattr(widget, 'translate_callback'):
                widget.translate_callback()
            break
    