import os
import json

from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel, QHBoxLayout, QPushButton, QLineEdit, QSizePolicy, QStackedWidget, QProgressBar, QFileDialog, QComboBox
from PySide6.QtCore import Qt, QThread, Signal, QPropertyAnimation, QEasingCurve

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.interface import utils
from subtitld.modules import session
from subtitld.modules import file_io
from subtitld.modules import utils as modules_utils
from subtitld.modules import addons
from subtitld.modules.addons.provider import TASK_ASR_TRANSCRIBE
from subtitld.modules.session import LIST_OF_SUPPORTED_IMPORT_EXTENSIONS
from subtitld.modules.signals import SIGNALS as _SESSION_SIGNALS

_list_of_supported_import_extensions = []
for _exttype in LIST_OF_SUPPORTED_IMPORT_EXTENSIONS:
    for _ext in LIST_OF_SUPPORTED_IMPORT_EXTENSIONS[_exttype]['extensions']:
        _list_of_supported_import_extensions.append(_ext)


def _format_tc(seconds):
    """Seconds → ``HH:MM:SS.mmm`` (the transcription-scope timecode form)."""
    try:
        seconds = max(0.0, float(seconds))
    except (TypeError, ValueError):
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02d}:{m:02d}:{s:02d}.{ms:03d}'


def _parse_tc(text):
    """``HH:MM:SS.mmm`` / ``MM:SS.mmm`` / ``SS.mmm`` → seconds, or None."""
    if not text:
        return None
    text = str(text).strip()
    if not text:
        return None
    try:
        parts = text.split(':')
        seconds = float(parts[-1])
        if len(parts) >= 2:
            seconds += int(parts[-2]) * 60
        if len(parts) >= 3:
            seconds += int(parts[-3]) * 3600
        return max(0.0, seconds)
    except (TypeError, ValueError):
        return None


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

# Vosk used to be a built-in ASR provider here. It's now distributed as an
# external add-on (see https://github.com/Subtitld/addon-vosk) so the
# Subtitld binary stays lean — neither the `vosk` Python wheel nor the
# `~50 MB` to `~1.6 GB` model zips ship inside the app.
#
# AssemblyAI also used to be a built-in here (panel + thread that called
# the AssemblyAI REST API directly with the user's own key). Removed so
# fresh installs aren't pre-wired to a single paid cloud vendor.
#
# The current bundled offline ASR is whisper.cpp via the
# ``whispercpp_provider`` builtin (registered in ``__main__.py``). The
# model itself isn't shipped inside the binary — it's downloaded once on
# first use to ``PATH_SUBTITLD_DATA_MODELS/whispercpp/``. The picker
# entry below is auto-populated through ``_GenericASRPanel`` like any
# other registered ASR provider; no bespoke widget is needed because the
# provider's settings live entirely in the AddonsDialog config UI.

LANGUAGE_DESCRIPTIONS = session.LANGUAGE_DICT_LIST.keys()


class _GenericASRPanel(QWidget):
    """Generic transcription panel for an arbitrary `ASRProvider`.

    Renders just the engine name and pipes the provider's
    `partial` / `transcript_finished` / `error` / `progress` signals
    back to the host UI. Used for every ASR engine in the picker today —
    the built-in whisper.cpp provider, add-ons discovered at runtime,
    and the cloud-routed AssemblyAI builtin — because none of them need
    a bespoke widget here: model/threads/api-key settings all live in
    the AddonsDialog config UI rendered from ``provider.config_schema``.
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

        # Render the provider's `config_schema` inline so the user can
        # tweak options (e.g. which Whisper model to download) without
        # opening the AddonsDialog. Auto-saves on every change directly
        # into the same registry slot the provider reads from on
        # `transcribe()`, so no Apply button is needed. Providers that
        # don't declare a schema (or have only manifest-side schemas not
        # surfaced on the provider object) just don't get a strip — the
        # `for_provider` factory returns None.
        from subtitld.interface.addons_dialog import AddonConfigInlineWidget
        widget.options_widget = AddonConfigInlineWidget.for_provider(provider, parent=widget)
        if widget.options_widget is not None:
            widget.layout().addWidget(widget.options_widget)

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
        # Offset back onto the full timeline when a scope range was used
        # (the provider saw a slice starting at 0).
        offset = getattr(widget, '_scope_offset', 0.0)
        session.SUBTITLE.setdefault('segments', []).append({
            'start': float(segment.get('start', 0.0)) + offset,
            'end': float(segment.get('end', 0.0)) + offset,
            'text': segment.get('text', ''),
            'speaker': segment.get('speaker', 'A'),
        })
        speaker = segment.get('speaker', 'A')
        speaker_added = speaker not in session.SPEAKERS
        if speaker_added:
            session.SPEAKERS[speaker] = {'image': None}
        try:
            widget.window().timeline_widget.update()
        except Exception:
            pass
        # Notify the speakers panel to re-render when a NEW speaker
        # arrives mid-transcription — without this, the speakers list
        # stays empty until the user manually re-selects the panel.
        if speaker_added:
            _SESSION_SIGNALS.speakers_changed.emit()
        session.set_unsaved()

    def _on_finished(widget, segments):
        if isinstance(segments, list) and segments:
            offset = getattr(widget, '_scope_offset', 0.0)
            scope_range = getattr(widget, '_scope_range', None)
            if offset:
                # Shift slice-relative timestamps back onto the timeline.
                for seg in segments:
                    if isinstance(seg, dict):
                        seg['start'] = float(seg.get('start', 0.0)) + offset
                        seg['end'] = float(seg.get('end', 0.0)) + offset
            if scope_range is not None:
                # Scoped run: replace only the subtitles inside the range
                # (including any partials appended live during this run),
                # keep everything outside it, then splice the results in.
                f, t = scope_range
                existing = session.SUBTITLE.get('segments', []) or []
                merged = [s for s in existing
                          if not (f - 1e-6 <= float(s.get('start', 0.0)) < t)]
                merged.extend(segments)
                merged.sort(key=lambda s: float(s.get('start', 0.0)))
                session.SUBTITLE['segments'] = merged
            else:
                session.SUBTITLE['segments'] = list(segments)
            any_added = False
            for seg in session.SUBTITLE['segments']:
                speaker = seg.get('speaker', 'A')
                if speaker not in session.SPEAKERS:
                    session.SPEAKERS[speaker] = {'image': None}
                    any_added = True
            try:
                widget.window().timeline_widget.update()
            except Exception:
                pass
            if any_added:
                _SESSION_SIGNALS.speakers_changed.emit()
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
        # Scope range (from the collapsible selector). When set, transcribe
        # only that slice and offset the results back onto the timeline.
        scope_range = getattr(widget.window(), '_transcription_scope_range', None)
        widget._scope_offset = 0.0
        widget._scope_range = None
        if scope_range is not None:
            sliced = _extract_audio_slice(audio_file, scope_range[0], scope_range[1])
            if sliced:
                audio_file = sliced
                widget._scope_offset = float(scope_range[0])
                widget._scope_range = scope_range
        widget.transcript_started.emit()
        language = session.SUBTITLE.get('language', 'en-us')
        opts = session.CONFIG.get('transcription', {}).get('engine_options', {}).get(widget.provider.id, {})
        widget.provider.transcribe(audio_file, language, dict(opts) if isinstance(opts, dict) else {})

    def translate(widget):
        widget.info_label.setText(widget.provider.display_name)


class _ImportASRPanel(QWidget):
    """Panel for the built-in ``import`` engine — just an "Import" button.

    Selecting the "Import from file" engine shows this panel. The button
    opens a file dialog and appends the parsed subtitles to the timeline
    (same behavior as the old standalone import button). It carries the
    same ``transcript_*`` signals + ``*_callback`` attributes the host
    expects from an engine panel, so it slots into the stacked widget and
    the ``_populate_asr_addons`` wiring unchanged.
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

        widget.import_button = QPushButton()
        widget.import_button.setProperty('class', 'button')
        widget.import_button.clicked.connect(lambda: widget.transcript())
        widget.layout().addWidget(widget.import_button)
        widget.layout().addStretch()

        widget.update_callback = widget.update
        widget.transcript_callback = widget.transcript
        widget.translate_callback = widget.translate

    def update(widget):
        pass

    def transcript(widget):
        """Open the file dialog and append the imported subtitles. This is
        the actual "import" action — invoked by our own button."""
        _import_subtitles_from_file(widget.window())

    def translate(widget):
        widget.import_button.setText(_('import_panel.import'))


# Built-in transcription provider IDs whose panels are constructed once
# at load() time (because they own expensive state we can't afford to
# discard on `providers_changed`). Empty today: the bundled whisper.cpp
# provider — and every other registered ASR engine — uses
# ``_GenericASRPanel``, which carries no state worth preserving across
# rebuilds (model selection lives in the addons registry, not on the
# widget). This constant is the seam where a future ASR engine with a
# heavy hand-built panel (e.g. one that streams visualizations or holds
# a live socket) would register itself; both constants bump in lockstep
# with ``_BUILTIN_ASR_COUNT`` below.
_BUILTIN_ASR_IDS: set[str] = set()

# Number of built-in entries seeded at the head of the engine combobox /
# stacked widget. Add-ons live AFTER this index; the rebuild logic chops
# the tail past it. Must equal len(_BUILTIN_ASR_IDS).
_BUILTIN_ASR_COUNT = 0


# Scope ids, in dropdown order.
_SCOPE_IDS = ('all', 'range', 'selection')


# Qt's QWIDGETSIZE_MAX — the "no maximum" sentinel we restore once the bar
# is fully open, so later field changes can still grow it.
_QWIDGETSIZE_MAX = 16777215


def _scope_bar_set_expanded(self, expanded):
    """Show/hide the scope bar with a vertical slide.

    Animates only when the expanded state actually flips — a plain field
    refresh (scope change, edit) snaps instead of re-sliding, and the very
    first render snaps too (no animation on panel load)."""
    bar = self.transcription_scope_bar
    bar.setVisible(True)  # always laid out; maxHeight drives visibility
    natural = bar.sizeHint().height()

    prev = getattr(self, '_scope_expanded_state', None)
    self._scope_expanded_state = expanded

    anim = self._scope_bar_anim
    anim.stop()
    if prev is None or prev == expanded:
        # First render, or a refresh that didn't toggle the state: snap.
        bar.setMaximumHeight(_QWIDGETSIZE_MAX if expanded else 0)
        return

    # Toggle flipped → slide between 0 and the bar's natural height. A
    # persistent `finished` handler (wired at build time) releases the
    # maxHeight clamp once the bar is fully open.
    start = min(bar.maximumHeight(), natural)
    anim.setStartValue(start)
    anim.setEndValue(natural if expanded else 0)
    anim.start()


def _build_transcription_scope(self, container_layout):
    """Build the collapsible transcription-scope selector (chip + bar).

    Widgets are attached to `self`; `global_panel_import_scope_update`
    drives their visibility/values from the persisted config."""
    def _labeled_field(field_widget, label_attr, control):
        v = QVBoxLayout(field_widget)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        lbl = QLabel()
        lbl.setProperty('class', 'widget_label')
        setattr(self, label_attr, lbl)
        v.addWidget(lbl)
        v.addWidget(control)

    scope_area = QWidget()
    scope_area.setObjectName('transcription_scope_area')
    scope_area.setAttribute(Qt.WA_StyledBackground, True)
    self.transcription_scope_area = scope_area
    scope_v = QVBoxLayout(scope_area)
    # No side padding here — the expanded bar must span the full panel
    # width (edge to edge). The chip supplies its own right inset below.
    # Top breathing room keeps the chip off the content above; NO bottom
    # padding — the chip/bar sit directly on the line.
    scope_v.setContentsMargins(0, 8, 0, 0)
    scope_v.setSpacing(0)

    # Collapse/expand chip (right-aligned, inset 10px from the right edge
    # to line up with the Start button below it).
    chip_row = QHBoxLayout()
    chip_row.setContentsMargins(0, 0, 10, 0)
    chip_row.addStretch()
    self.transcription_scope_chip = QPushButton()
    self.transcription_scope_chip.setObjectName('transcription_scope_chip')
    self.transcription_scope_chip.setCursor(Qt.PointingHandCursor)
    self.transcription_scope_chip.clicked.connect(lambda: global_panel_import_scope_toggle(self))
    chip_row.addWidget(self.transcription_scope_chip)
    scope_v.addLayout(chip_row)

    # The bar itself (collapsible).
    self.transcription_scope_bar = QWidget()
    self.transcription_scope_bar.setObjectName('transcription_scope_bar')
    bar = QHBoxLayout(self.transcription_scope_bar)
    bar.setContentsMargins(12, 8, 12, 10)
    bar.setSpacing(14)

    scope_field = QWidget()
    self.transcription_scope_combobox = QComboBox()
    self.transcription_scope_combobox.setObjectName('transcription_scope_combobox')
    # One item per _SCOPE_IDS entry, in order. Labels are set in translate().
    self.transcription_scope_combobox.addItems(['All', 'Range', 'Selection'])
    self.transcription_scope_combobox.activated.connect(lambda: global_panel_import_scope_combobox_changed(self))
    _labeled_field(scope_field, 'transcription_scope_label', self.transcription_scope_combobox)
    bar.addWidget(scope_field)

    self.transcription_scope_from_field = QWidget()
    self.transcription_scope_from_input = QLineEdit()
    self.transcription_scope_from_input.setObjectName('transcription_scope_input')
    self.transcription_scope_from_input.setPlaceholderText('00:00:00.000')
    self.transcription_scope_from_input.editingFinished.connect(lambda: global_panel_import_scope_field_edited(self))
    _labeled_field(self.transcription_scope_from_field, 'transcription_scope_from_label', self.transcription_scope_from_input)
    bar.addWidget(self.transcription_scope_from_field, 1)

    self.transcription_scope_duration_field = QWidget()
    self.transcription_scope_duration_value = QLabel()
    self.transcription_scope_duration_value.setObjectName('transcription_scope_duration_value')
    _labeled_field(self.transcription_scope_duration_field, 'transcription_scope_duration_label', self.transcription_scope_duration_value)
    bar.addWidget(self.transcription_scope_duration_field, 1)

    self.transcription_scope_to_field = QWidget()
    self.transcription_scope_to_input = QLineEdit()
    self.transcription_scope_to_input.setObjectName('transcription_scope_input')
    self.transcription_scope_to_input.setPlaceholderText('00:00:00.000')
    self.transcription_scope_to_input.editingFinished.connect(lambda: global_panel_import_scope_field_edited(self))
    _labeled_field(self.transcription_scope_to_field, 'transcription_scope_to_label', self.transcription_scope_to_input)
    bar.addWidget(self.transcription_scope_to_field, 1)

    scope_v.addWidget(self.transcription_scope_bar)

    # Vertical slide for expand/collapse: animate the bar's maxHeight.
    # Parented to the bar so it's cleaned up with it.
    self._scope_bar_anim = QPropertyAnimation(
        self.transcription_scope_bar, b'maximumHeight', self.transcription_scope_bar)
    self._scope_bar_anim.setDuration(170)
    self._scope_bar_anim.setEasingCurve(QEasingCurve.OutCubic)
    self._scope_expanded_state = None  # None until first update → no anim on load

    # Once the open animation finishes, drop the maxHeight clamp so later
    # field changes (e.g. Range adds From/To) can still grow the bar. On a
    # collapse the state is False, so this is a no-op and the bar stays 0.
    def _release_scope_clamp():
        if getattr(self, '_scope_expanded_state', False):
            self.transcription_scope_bar.setMaximumHeight(_QWIDGETSIZE_MAX)
    self._scope_bar_anim.finished.connect(_release_scope_clamp)

    container_layout.addWidget(scope_area)


def _scope_selected_range():
    """(start, end) of the currently selected subtitle, or None."""
    sel = session.SUBTITLE.get('selected')
    if isinstance(sel, dict):
        try:
            return float(sel.get('start', 0.0)), float(sel.get('end', 0.0))
        except (TypeError, ValueError):
            return None
    return None


def _current_scope_range(self):
    """The [from, to] seconds the next transcription should cover, or
    None for the whole media (scope 'all', or an invalid/empty range)."""
    cfg = session.CONFIG.get('transcription', {})
    scope = cfg.get('scope', 'all')
    if scope == 'range':
        f = _parse_tc(self.transcription_scope_from_input.text())
        t = _parse_tc(self.transcription_scope_to_input.text())
        if f is not None and t is not None and t > f:
            return (f, t)
        return None
    if scope == 'selection':
        rng = _scope_selected_range()
        if rng and rng[1] > rng[0]:
            return rng
        return None
    return None


def global_panel_import_scope_toggle(self):
    cfg = session.CONFIG.setdefault('transcription', {})
    cfg['scope_expanded'] = not bool(cfg.get('scope_expanded', False))
    global_panel_import_scope_update(self)


def global_panel_import_scope_combobox_changed(self):
    cfg = session.CONFIG.setdefault('transcription', {})
    idx = self.transcription_scope_combobox.currentIndex()
    if 0 <= idx < len(_SCOPE_IDS):
        cfg['scope'] = _SCOPE_IDS[idx]
    global_panel_import_scope_update(self)


def global_panel_import_scope_field_edited(self):
    cfg = session.CONFIG.setdefault('transcription', {})
    f = _parse_tc(self.transcription_scope_from_input.text())
    t = _parse_tc(self.transcription_scope_to_input.text())
    if f is not None:
        cfg['scope_from'] = f
    if t is not None:
        cfg['scope_to'] = t
    global_panel_import_scope_update(self)


def global_panel_import_scope_update(self):
    """Refresh the scope selector — chip label/icon, bar visibility, and
    each field's value/visibility from the current scope + config."""
    if not hasattr(self, 'transcription_scope_combobox'):
        return
    cfg = session.CONFIG.setdefault('transcription', {})
    scope = cfg.get('scope', 'all')
    if scope not in _SCOPE_IDS:
        scope = 'all'
        cfg['scope'] = scope
    expanded = bool(cfg.get('scope_expanded', False))

    total = float(session.VIDEO.get('duration', 0.0) or 0.0)

    # Combobox selection (block signals to avoid re-entrancy).
    self.transcription_scope_combobox.blockSignals(True)
    self.transcription_scope_combobox.setCurrentIndex(_SCOPE_IDS.index(scope))
    self.transcription_scope_combobox.blockSignals(False)

    # Chip: "<SCOPE>  <icon>" — icon is ≡ (expand) when collapsed, − (collapse) when open.
    scope_name = self.transcription_scope_combobox.currentText() or scope.upper()
    self.transcription_scope_chip.setText(f'{scope_name.upper()}   {"−" if expanded else "≡"}')

    _scope_bar_set_expanded(self, expanded)

    # Field visibility + values per scope.
    show_range = scope in ('range', 'selection')
    self.transcription_scope_from_field.setVisible(show_range)
    self.transcription_scope_to_field.setVisible(show_range)
    self.transcription_scope_duration_field.setVisible(True)

    if scope == 'all':
        self.transcription_scope_duration_value.setText(_format_tc(total))
    elif scope == 'range':
        # Editable From/To from config; duration = To − From.
        self.transcription_scope_from_input.setReadOnly(False)
        self.transcription_scope_to_input.setReadOnly(False)
        f = float(cfg.get('scope_from', 0.0) or 0.0)
        t = float(cfg.get('scope_to', total) or total)
        if not self.transcription_scope_from_input.hasFocus():
            self.transcription_scope_from_input.setText(_format_tc(f))
        if not self.transcription_scope_to_input.hasFocus():
            self.transcription_scope_to_input.setText(_format_tc(t))
        self.transcription_scope_duration_value.setText(_format_tc(max(0.0, t - f)))
    else:  # selection — read From/To off the selected subtitle (read-only).
        self.transcription_scope_from_input.setReadOnly(True)
        self.transcription_scope_to_input.setReadOnly(True)
        rng = _scope_selected_range()
        if rng:
            f, t = rng
            self.transcription_scope_from_input.setText(_format_tc(f))
            self.transcription_scope_to_input.setText(_format_tc(t))
            self.transcription_scope_duration_value.setText(_format_tc(max(0.0, t - f)))
        else:
            self.transcription_scope_from_input.setText('—')
            self.transcription_scope_to_input.setText('—')
            self.transcription_scope_duration_value.setText('—')


def _extract_audio_slice(src, start, end):
    """Extract [start, end] seconds of `src` to a temp mono 16 kHz WAV.
    Returns the path, or None on failure. Used to limit transcription to a
    scope range; the caller offsets the returned timestamps by `start`."""
    import subprocess
    import tempfile
    if not src or end <= start:
        return None
    out = os.path.join(
        tempfile.gettempdir(),
        f'subtitld_transcribe_{int(start * 1000)}_{int(end * 1000)}.wav',
    )
    cmd = [
        session.FFMPEG_EXECUTABLE, '-y', '-loglevel', 'error',
        '-ss', f'{start:.3f}', '-i', src, '-t', f'{end - start:.3f}',
        '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', out,
    ]
    try:
        subprocess.run(
            cmd, check=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            startupinfo=session.STARTUPINFO,
        )
        return out if os.path.isfile(out) and os.path.getsize(out) > 0 else None
    except Exception:
        return None


def global_panel_import_start_transcription_progress_start(self):
    # Running mode: the progress bar takes over the whole bottom row — full
    # width AND the same height the Start button filled, with zero padding on
    # every side. The Start button and the ALL scope row are hidden.
    # Idempotent — safe to call from both the button click and the
    # transcript_started signal.
    progress = self.global_panel_import_start_transcription_progress
    button = self.global_panel_import_start_transcription_button
    progress.setMinimumHeight(max(button.height(), button.sizeHint().height(), 24))
    progress.setVisible(True)
    progress.setValue(0)
    progress.setMaximum(100)
    button.setVisible(False)
    if hasattr(self, 'transcription_scope_area'):
        self.transcription_scope_area.setVisible(False)
    if hasattr(self, 'transcription_footer_bottom_line'):
        self.transcription_footer_bottom_line.layout().setContentsMargins(0, 0, 0, 0)


def global_panel_import_start_transcription_progress_update(self, value):
    self.global_panel_import_start_transcription_progress.setValue(value)


def global_panel_import_start_transcription_progress_finish(self):
    # Restore the idle footer: hide the bar, drop the running-mode height,
    # bring back the Start button and the ALL scope row, and restore the
    # row's side/bottom padding.
    self.global_panel_import_start_transcription_progress.setVisible(False)
    self.global_panel_import_start_transcription_progress.setMinimumHeight(0)
    self.global_panel_import_start_transcription_button.setVisible(True)
    if hasattr(self, 'transcription_scope_area'):
        self.transcription_scope_area.setVisible(True)
    if hasattr(self, 'transcription_footer_bottom_line'):
        self.transcription_footer_bottom_line.layout().setContentsMargins(10, 0, 10, 10)


def _populate_asr_addons(self):
    """(Re)build only the add-on tail of the transcription engine combobox
    and stacked widget. Idempotent — safe to call after `providers_changed`.

    Built-in entries (currently none) live at indices 0.._BUILTIN_ASR_COUNT;
    add-ons live AFTER that, so we chop the tail and re-add. Using
    `removeItem` on the underlying QComboBox rather than `clear()`
    preserves built-in entries and their selection state.
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

        # "import" (file import) first, then the real ASR engines by id —
        # import is the most basic way to get subtitles in.
        providers = addons.get_manager().providers_for_task(TASK_ASR_TRANSCRIBE)
        providers.sort(key=lambda p: (p.id != 'import', p.id))
        for provider in providers:
            if provider.id in _BUILTIN_ASR_IDS:
                continue
            if provider.id == 'import':
                panel = _ImportASRPanel(provider)
            else:
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

    # NB: the old standalone "Import" button lived here. It is now the
    # built-in ``import`` engine (import_provider) — it shows up in the
    # engine picker below, and its panel (_ImportASRPanel) carries the
    # Import button. See _populate_asr_addons.

    self.global_panel_import_language_combobox = utils.LabeledComboBox()
    self.global_panel_import_language_combobox.addItems(LANGUAGE_DESCRIPTIONS)
    self.global_panel_import_language_combobox.activated.connect(lambda: global_panel_import_language_combobox_activated(self))
    left_panel_import_panel.layout().addWidget(self.global_panel_import_language_combobox, 1)

    self.global_panel_import_engine_combobox = utils.LabeledComboBox()
    self.global_panel_import_engine_combobox.setProperty('class', 'button')
    self.global_panel_import_engine_combobox.activated.connect(lambda: global_panel_import_engine_combobox_activated(self))
    left_panel_import_panel.layout().addWidget(self.global_panel_import_engine_combobox)

    self.global_panel_import_tabwidget = QStackedWidget()

    # No hand-coded ASR panel here today. Every ASR engine — including
    # the bundled whisper.cpp builtin registered in ``__main__.py`` —
    # arrives through the addons system below via ``_GenericASRPanel``.
    # See ``_BUILTIN_ASR_IDS`` above for the seam where a future ASR
    # engine with a hand-coded panel would slot in.

    # Add-on ASR providers discovered at runtime. When a built-in lands
    # back here, it is NOT churned on rebuild because its panel carries
    # state (API keys, model selection) we'd lose on every
    # `providers_changed` emission; only the add-on tail refreshes.
    _populate_asr_addons(self)

    # Wire add-on registry to the UI. A user installing/uninstalling an
    # ASR add-on at runtime triggers `providers_changed`; we rebuild the
    # add-on portion of the combobox + stacked widget without restarting
    # the app.
    addons.get_manager().providers_changed.connect(lambda: _populate_asr_addons(self))

    # Options area (per-engine ASR settings) — subtle background gradient
    # so it reads as the panel's "content", distinct from the footer below.
    self.global_panel_import_tabwidget.setObjectName('transcription_options_area')
    left_panel_import_panel.layout().addWidget(self.global_panel_import_tabwidget, 1)

    # --- Bottom "global" footer -------------------------------------------
    # The panel itself is padding-free (options span edge to edge); the
    # footer band supplies its own left/right/bottom padding so the scope
    # selector + Start button are inset while the content above is full-bleed.
    left_panel_import_panel.layout().setContentsMargins(0, 0, 0, 0)

    self.transcription_footer = QWidget()
    self.transcription_footer.setObjectName('transcription_footer')
    self.transcription_footer.setAttribute(Qt.WA_StyledBackground, True)
    footer_v = QVBoxLayout(self.transcription_footer)
    # Full-bleed: no side padding on the footer itself, so the divider
    # below spans edge to edge. The scope row and the button row each
    # supply their own inset padding instead.
    footer_v.setContentsMargins(0, 0, 0, 0)
    footer_v.setSpacing(0)

    # Collapsible scope selector (chip + bar) inside the footer.
    _build_transcription_scope(self, footer_v)

    # Edge-to-edge delimiter BETWEEN the scope row (above) and the Start
    # button (below). Its own 1px strip so it touches both panel edges.
    self.transcription_footer_divider = QWidget()
    self.transcription_footer_divider.setObjectName('transcription_footer_divider')
    self.transcription_footer_divider.setAttribute(Qt.WA_StyledBackground, True)
    self.transcription_footer_divider.setFixedHeight(1)
    footer_v.addWidget(self.transcription_footer_divider)


    bottom_line_w = QWidget()
    bottom_line_w.setObjectName('transcription_footer_bottom_line')
    # Kept so the running-mode toggle can zero this row's side padding for a
    # true edge-to-edge progress bar (see progress_start / progress_finish).
    self.transcription_footer_bottom_line = bottom_line_w
    bottom_line = QHBoxLayout(bottom_line_w)
    # Button keeps its side + bottom padding; NO top padding — it hugs the
    # divider directly above it (no gap between the line and the button).
    bottom_line.setContentsMargins(10, 0, 10, 10)
    bottom_line.setSpacing(0)
    footer_v.addWidget(bottom_line_w)

    # NB: the three `..._transcription_progress_*` helpers used to be
    # defined here as nested functions. That worked while no ASR
    # provider ever actually emitted `transcript_progress`, but the
    # nested-scope names are invisible to `_populate_asr_addons`, which
    # is module-level and only sees module-level names. When whisper.cpp
    # started emitting progress, `connect(... lambda: <nested name>)`
    # crashed with NameError. They now live at module scope.

    self.global_panel_import_start_transcription_progress = QProgressBar()
    self.global_panel_import_start_transcription_progress.setObjectName('transcription_progress_bar')
    self.global_panel_import_start_transcription_progress.setVisible(False)
    self.global_panel_import_start_transcription_progress.setProperty('class', 'secondary')
    self.global_panel_import_start_transcription_progress.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    # Stretch 1: once the Start button is hidden during a run, the bar expands
    # to fill the whole row (progress_start also zeroes the row's side padding).
    bottom_line.addWidget(self.global_panel_import_start_transcription_progress, 1)

    self.global_panel_import_start_transcription_button = QPushButton()
    self.global_panel_import_start_transcription_button.setObjectName('transcription_start_button')
    self.global_panel_import_start_transcription_button.clicked.connect(lambda: global_panel_import_start_transcription_button_clicked(self))
    bottom_line.addWidget(self.global_panel_import_start_transcription_button, 0, Qt.AlignRight)

    left_panel_import_panel.layout().addWidget(self.transcription_footer)

    update(self)

    
def show(self):
    update(self)


def update(self):
    if not session.SUBTITLE.get('language', False):
        session.SUBTITLE['language'] = session.CONFIG.get('transcription', {}).get('language', 'en-us')
    # `source_language` mirrors `language` at transcription time, then is
    # frozen — translation/invert flips `language` but never touches
    # `source_language`. clone_ref uses it to pull the audio-matching
    # transcript as `voice_ref_text`. See clone_ref._resolve_source_language.
    if not session.SUBTITLE.get('source_language'):
        session.SUBTITLE['source_language'] = session.SUBTITLE['language']
    selected_language_name = 'English (United States)'
    for language_name, language_code in session.LANGUAGE_DICT_LIST.items():
        if language_code == session.SUBTITLE['language']:
            selected_language_name = language_name
            break
    self.global_panel_import_language_combobox.setCurrentText(selected_language_name)

    # Restore the previously saved engine selection. There is no
    # default built-in any more — when the user's saved engine isn't in
    # the combobox (uninstalled add-on, or fresh install before any ASR
    # provider is installed), `setCurrentText` silently no-ops and the
    # combobox stays on its current item (or empty if nothing's there).
    self.global_panel_import_engine_combobox.setCurrentText(
        session.CONFIG.get('transcription', {}).get('engine', '')
    )

    global_panel_import_scope_update(self)
    global_panel_import_tabwidget_update(self)


def hide(self):
    pass


def global_panel_import_language_combobox_activated(self):
    chosen = session.LANGUAGE_DICT_LIST[self.global_panel_import_language_combobox.currentText()]
    session.SUBTITLE['language'] = chosen
    # The user is explicitly declaring what's spoken in the source media,
    # so source_language tracks language here — pre-translation, the two
    # are the same. Stamping unconditionally (vs. setdefault) so that
    # correcting a wrong auto-detected language also corrects
    # source_language; post-translation editors who want to redirect the
    # synthesis language without changing what's on the audio should use
    # the invert-translation flow instead.
    session.SUBTITLE['source_language'] = chosen
    if not isinstance(session.CONFIG.get('transcription'), dict):
        session.CONFIG['transcription'] = {}
    session.CONFIG['transcription']['language'] = chosen
    global_panel_import_tabwidget_update(self)


def global_panel_import_engine_combobox_activated(self):
    session.CONFIG['transcription']['engine'] = self.global_panel_import_engine_combobox.currentText()
    global_panel_import_tabwidget_update(self)


def global_panel_import_start_transcription_button_clicked(self):
    # Resolve the scope range once, here — the panel's transcript() reads
    # it to slice the audio and offset the returned timestamps.
    scope_range = _current_scope_range(self)
    self._transcription_scope_range = scope_range

    if scope_range is None:
        # Whole-media transcription REPLACES all subtitles → confirm when
        # some already exist, then clear.
        if session.SUBTITLE['segments']:
            confirm_dialog = utils.SimpleDialog(self, title=_('transcription_panel.start_transcription'))
            label = QLabel(_('transcription_panel.start_transcription_text'))
            confirm_dialog.content.layout().addWidget(label)
            confirm_dialog.exec()
            if confirm_dialog.result() != 1:
                return
        session.SUBTITLE['segments'] = []
        self.timeline_widget.update()
    # A scoped (range/selection) run MERGES: it only replaces subtitles
    # inside the range (handled in _on_finished), so no clear / confirm.

    for widget in self.global_panel_import_tabwidget.findChildren(QWidget):
        if widget.property('transcription_engine') == self.global_panel_import_engine_combobox.currentText():
            widget.transcript_callback()
            break
    # Switch to running mode right away so the Start button + ALL row vanish
    # the instant the user clicks — don't wait for the engine's (possibly
    # delayed) transcript_started signal, which re-runs this harmlessly.
    global_panel_import_start_transcription_progress_start(self)


def global_panel_import_tabwidget_update(self):
    current_engine = self.global_panel_import_engine_combobox.currentText()
    is_import = (current_engine == 'import')
    # The "import" engine drives its action from its own panel button, so
    # the whole bottom footer (scope selector + Start button) is hidden.
    if hasattr(self, 'transcription_footer'):
        self.transcription_footer.setVisible(not is_import)
    for widget in self.global_panel_import_tabwidget.findChildren(QWidget):
        if widget.property('transcription_engine') == current_engine:
            self.global_panel_import_tabwidget.setCurrentWidget(widget)
            widget.update_callback()
            break


def _import_subtitles_from_file(self):
    """Import subtitles from an existing file (SRT, DOCX, TXT, ...) and
    append them to the timeline. `self` is the main window. Driven by the
    built-in "Import from file" engine's panel button (_ImportASRPanel)."""
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
    self.global_panel_import_language_combobox.setLabel(_('transcription_panel.language'))
    self.global_panel_import_start_transcription_button.setText(_('transcription_panel.start_transcription'))
    self.global_panel_import_engine_combobox.setLabel(_('transcription_panel.engine'))
    if hasattr(self, 'transcription_scope_combobox'):
        self.transcription_scope_label.setText(_('transcription_panel.scope'))
        self.transcription_scope_from_label.setText(_('transcription_panel.scope_from'))
        self.transcription_scope_duration_label.setText(_('transcription_panel.scope_duration'))
        self.transcription_scope_to_label.setText(_('transcription_panel.scope_to'))
        labels = [_('transcription_panel.scope_all'), _('transcription_panel.scope_range'),
                  _('transcription_panel.scope_selection')]
        for i, text in enumerate(labels):
            self.transcription_scope_combobox.setItemText(i, text)
        global_panel_import_scope_update(self)
    for widget in self.global_panel_import_tabwidget.findChildren(QWidget):
        if 'translate_callback' in dir(widget):
            widget.translate_callback()

    
