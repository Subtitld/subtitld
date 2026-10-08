import os
import json

from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel, QHBoxLayout, QPushButton, QSizePolicy, QStackedWidget, QProgressBar, QFileDialog
from PySide6.QtCore import Qt, Signal

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
from subtitld.interface.scope_selector import ScopeFooter


# Inset of the per-engine options panel (engine name + inline config fields)
# shown under the TRANSCRIPTION ENGINE combobox. It matches the text padding
# inside the comboboxes above, so the engine's fields line up with their
# labels rather than with the panel's edge.
_OPTIONS_PANEL_PADDING = 10

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

# Vosk used to be a built-in ASR provider here. It's now distributed as an
# external add-on (see https://github.com/Subtitld/addon-vosk) so the
# Subtitld binary stays lean — neither the `vosk` Python wheel nor the
# `~50 MB` to `~1.6 GB` model zips ship inside the app.
#
# A paid cloud ASR vendor used to be wired in here too, first as a direct
# REST panel and later as a cloud-routed builtin. Both were removed so a
# stock Subtitld ships no third-party service integration and fresh
# installs aren't pre-wired to one vendor. Cloud ASR is an add-on now; the
# shared cloud plumbing those add-ons authenticate through stays in
# `addons/builtin/subtitld_cloud_shared.py`.
#
# No speech-to-text engine is bundled any more. Offline whisper.cpp is an
# add-on too (addon-whispercpp); it downloads its model on first use into
# ``PATH_SUBTITLD_DATA_MODELS/whispercpp/``, the folder the former
# built-in used, so earlier downloads are reused. Every engine's picker
# entry is auto-populated through ``_GenericASRPanel``; no bespoke widget
# is needed because settings live entirely in the AddonsDialog config UI.

LANGUAGE_DESCRIPTIONS = session.LANGUAGE_DICT_LIST.keys()


class _GenericASRPanel(QWidget):
    """Generic transcription panel for an arbitrary `ASRProvider`.

    Renders just the engine name and pipes the provider's
    `partial` / `transcript_finished` / `error` / `progress` signals
    back to the host UI. Used for every ASR engine in the picker today —
    add-ons discovered at runtime, offline and cloud-routed alike —
    because none of them need a bespoke
    widget here: model/threads/api-key settings all live in
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
        widget.layout().setContentsMargins(*(_OPTIONS_PANEL_PADDING,) * 4)
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
        target = getattr(widget, '_scope_target', None)
        if target is not None:
            # Selection run: the text belongs to the selected subtitle, so
            # fill it in as it arrives instead of adding cues of our own.
            if any(seg is target for seg in session.SUBTITLE.get('segments', []) or []):
                text = str(segment.get('text', '') or '').strip()
                if text:
                    widget._scope_target_parts.append(text)
                    target['text'] = ' '.join(widget._scope_target_parts)
                    try:
                        widget.window().timeline_widget.update()
                    except Exception:
                        pass
                    session.set_unsaved()
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
            target = getattr(widget, '_scope_target', None)
            if target is not None and not any(
                    seg is target for seg in session.SUBTITLE.get('segments', []) or []):
                target = None  # deleted mid-run; fall back to a plain scoped merge
            if target is not None and scope_range is not None:
                # Selection run: the whole transcript is this subtitle's text.
                # Its timing is left alone, and anything that landed inside it
                # during the run (live partials) goes.
                text = _joined_text(segments)
                if text:
                    target['text'] = text
                f, t = scope_range
                existing = session.SUBTITLE.get('segments', []) or []
                kept = [s for s in existing
                        if s is target or not (f - 1e-6 <= float(s.get('start', 0.0)) < t)]
                kept.sort(key=lambda s: float(s.get('start', 0.0)))
                session.SUBTITLE['segments'] = kept
            elif scope_range is not None:
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
        # Set for a "selection" run: the subtitle whose text this run writes.
        widget._scope_target = None
        widget._scope_target_parts = []
        if scope_range is not None:
            sliced = _extract_audio_slice(audio_file, scope_range[0], scope_range[1])
            if sliced:
                audio_file = sliced
                widget._scope_offset = float(scope_range[0])
                widget._scope_range = scope_range
                widget._scope_target = getattr(widget.window(), '_transcription_scope_target', None)
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
# discard on `providers_changed`). Empty today: every registered ASR
# engine uses ``_GenericASRPanel``, which carries no state worth preserving across
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



def _joined_text(segments):
    """Every returned segment's text as one line — what a selection-scoped
    run puts in the selected subtitle."""
    parts = [str(seg.get('text', '') or '').strip() for seg in segments if isinstance(seg, dict)]
    return ' '.join(part for part in parts if part)


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


# The minimum progress shown the instant a run starts. Starting the bar at a
# few percent (rather than a flat 0) reads as "it's working" immediately —
# some engines take a while before they emit their first real progress tick.
_TRANSCRIPTION_START_PERCENT = 5


def _reconcile_transcription_footer(self):
    """Single source of truth for the footer's idle-vs-running state, derived
    from ``self._transcription_running``. The bottom row is a two-page stack,
    so the Start button and the progress bar are mutually exclusive by
    construction — no stale state (an aborted run, a re-show mid-run, an
    engine switch) can leave both on screen. Safe to call any time the footer
    is (re)shown or updated; re-selecting the current page is a no-op."""
    footer = getattr(self, 'transcription_footer', None)
    if footer is None or not hasattr(self, 'global_panel_import_start_transcription_progress'):
        return  # footer not built yet
    running = bool(getattr(self, '_transcription_running', False))
    # One atomic content swap. QStackedLayout gives the incoming page the
    # row's full rect synchronously while showing it, so the progress bar's
    # geometry is final before anything can paint it. The row's height never
    # changes: its hint is the tallest page's, whichever one is current.
    footer.show_progress(running)
    if hasattr(self, 'transcription_scope'):
        self.transcription_scope.setVisible(not running)


def _reconcile_start_button(self):
    """The Start button is only live when the current scope can actually be
    satisfied — scope "selection" with nothing selected has nothing to
    transcribe, and the scope selector shows the hint that says so."""
    button = getattr(self, 'global_panel_import_start_transcription_button', None)
    scope = getattr(self, 'transcription_scope', None)
    if button is None or scope is None:
        return
    # Also dead while the Subtitld Cloud account is being checked, so a
    # second click cannot start a second check.
    button.setEnabled(scope.is_ready() and getattr(self, '_cloud_check', None) is None)


def global_panel_import_start_transcription_progress_start(self):
    # Enter running mode: the running page (edge-to-edge progress bar) replaces
    # the idle page and the scope row hides. Idempotent — safe to call from
    # both the button click and a (possibly delayed) transcript_started signal.
    progress = self.global_panel_import_start_transcription_progress
    if not getattr(self, '_transcription_running', False):
        # Seed the value BEFORE the page is shown. QProgressBar.setValue()
        # repaints synchronously when visible; done while hidden it only
        # records the value, so the first painted frame is the final one.
        # Only on the idle->running transition, so a delayed
        # transcript_started can't knock a bar that already advanced back.
        progress.setMaximum(100)
        progress.setValue(_TRANSCRIPTION_START_PERCENT)
    self._transcription_running = True
    _reconcile_transcription_footer(self)


def global_panel_import_start_transcription_progress_update(self, value):
    # Never let a run's bar fall back below the initial "it's working" floor,
    # so an engine that reports 0% for its first few ticks doesn't visually
    # reset to empty after we already showed 5%.
    self.global_panel_import_start_transcription_progress.setValue(
        max(_TRANSCRIPTION_START_PERCENT, int(value)))


def global_panel_import_start_transcription_progress_finish(self):
    # Leave running mode: the idle page (Start button) replaces the running
    # page and the scope row comes back.
    self._transcription_running = False
    _reconcile_transcription_footer(self)


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

    # The panel's 10px padding is carried by this content column instead of
    # the panel layout: the footer band below has to reach both panel edges,
    # and a padded panel layout would inset its hairline and gradient.
    left_panel_import_panel.layout().setContentsMargins(0, 10, 0, 0)
    content = QWidget()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(10, 0, 10, 0)
    content_layout.setSpacing(left_panel_import_panel.layout().spacing())
    left_panel_import_panel.layout().addWidget(content, 1)

    # NB: the old standalone "Import" button lived here. It is now the
    # built-in ``import`` engine (import_provider) — it shows up in the
    # engine picker below, and its panel (_ImportASRPanel) carries the
    # Import button. See _populate_asr_addons.

    self.global_panel_import_language_combobox = utils.LabeledComboBox()
    self.global_panel_import_language_combobox.addItems(LANGUAGE_DESCRIPTIONS)
    self.global_panel_import_language_combobox.activated.connect(lambda: global_panel_import_language_combobox_activated(self))
    content_layout.addWidget(self.global_panel_import_language_combobox, 1)

    self.global_panel_import_engine_combobox = utils.LabeledComboBox()
    self.global_panel_import_engine_combobox.setProperty('class', 'button')
    self.global_panel_import_engine_combobox.activated.connect(lambda: global_panel_import_engine_combobox_activated(self))
    content_layout.addWidget(self.global_panel_import_engine_combobox)

    self.global_panel_import_tabwidget = QStackedWidget()

    # No hand-coded ASR panel here today. Every ASR engine arrives through
    # the addons system below via ``_GenericASRPanel``.
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
    content_layout.addWidget(self.global_panel_import_tabwidget, 1)

    # --- Bottom footer ----------------------------------------------------
    # The shared scope footer: chip + bar, hairline, action row. It is added
    # to the PANEL, not to the padded content column, so its band spans the
    # panel edge to edge; its rows inset themselves instead. The translation
    # and dubbing panels mount the same footer.

    self.transcription_footer = ScopeFooter('transcription', parent=left_panel_import_panel)
    self.transcription_scope = self.transcription_footer.selector
    self.transcription_footer.changed.connect(lambda: _reconcile_start_button(self))
    self.transcription_footer_divider = self.transcription_footer.divider

    # NB: the three `..._transcription_progress_*` helpers used to be
    # defined here as nested functions. That worked while no ASR
    # provider ever actually emitted `transcript_progress`, but the
    # nested-scope names are invisible to `_populate_asr_addons`, which
    # is module-level and only sees module-level names. When an engine
    # started emitting progress, `connect(... lambda: <nested name>)`
    # crashed with NameError. They now live at module scope.
    self._transcription_running = False

    # The footer's action row has two pages: the Start button (inset) while
    # idle, the progress bar (edge to edge) while a job runs. See
    # ScopeFooter in scope_selector.py.
    self.global_panel_import_start_transcription_button = QPushButton()
    self.global_panel_import_start_transcription_button.setObjectName('transcription_start_button')
    self.global_panel_import_start_transcription_button.clicked.connect(lambda: global_panel_import_start_transcription_button_clicked(self))
    self.transcription_footer.add_action(self.global_panel_import_start_transcription_button, primary=True)

    self.global_panel_import_start_transcription_progress = QProgressBar()
    self.global_panel_import_start_transcription_progress.setObjectName('transcription_progress_bar')
    self.global_panel_import_start_transcription_progress.setProperty('class', 'secondary')
    self.global_panel_import_start_transcription_progress.setMaximum(100)
    self.transcription_footer.set_progress_bar(self.global_panel_import_start_transcription_progress)

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

    # The timeline calls left_panel.update() on every selection change, so
    # refreshing here is what keeps scope "selection" (its times, its hint,
    # and the Start button) in step with what's selected.
    self.transcription_scope.refresh()
    _reconcile_start_button(self)
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
    scope_range = self.transcription_scope.current_range()
    self._transcription_scope_range = scope_range

    # Scope "selection" transcribes the selected subtitle and writes the
    # text into it, so it needs one — and must never fall through to the
    # whole-media run below, which would replace every subtitle. The Start
    # button is disabled in that state, so this is the belt to its braces
    # (a selection can vanish between the last refresh and the click).
    scope = self.transcription_scope.scope
    self._transcription_scope_target = self.transcription_scope.selected_segment()
    if scope == 'selection' and (self._transcription_scope_target is None or scope_range is None):
        error_dialog = utils.SimpleDialog(self, title=_('transcription_panel.error'))
        error_dialog.content.layout().addWidget(QLabel(_('transcription_panel.scope_selection_empty')))
        error_dialog.reject_button.setVisible(False)
        error_dialog.exec()
        return

    # A cloud engine fails mid-job (after the audio is uploaded and the
    # subtitles cleared) when the key is missing or wrong or the balance
    # is empty. Check the account first; start only once it is good.
    engine_panel = _current_engine_panel(self)
    if getattr(getattr(engine_panel, 'provider', None), 'uses_subtitld_cloud', False):
        _check_cloud_account(self, lambda: _start_transcription(self, scope_range))
        return
    _start_transcription(self, scope_range)


def _current_engine_panel(self):
    """The options panel of the engine picked in the combobox, or None."""
    engine = self.global_panel_import_engine_combobox.currentText()
    for widget in self.global_panel_import_tabwidget.findChildren(QWidget):
        if widget.property('transcription_engine') == engine:
            return widget
    return None


def _check_cloud_account(self, proceed):
    """Check the Subtitld Cloud account in the background, then `proceed`
    — or explain what is wrong and start nothing.

    Only a definite answer blocks: no key, a rejected key, an empty
    balance, or no connection at all. A cloud build without the account
    endpoint, or a server error on it, lets the job run and speak for
    itself.
    """
    if getattr(self, '_cloud_check', None) is not None:
        return
    from subtitld.interface.cloud_dashboard import _CloudAccountWorker
    from subtitld.modules.addons.builtin import subtitld_cloud_shared as cloud

    button = self.global_panel_import_start_transcription_button
    worker = _CloudAccountWorker(self)
    self._cloud_check = worker
    button.setText(_('transcription_panel.cloud_checking'))
    _reconcile_start_button(self)

    def done(problem):
        self._cloud_check = None
        button.setText(_('transcription_panel.start_transcription'))
        _reconcile_start_button(self)
        messages = {
            'no_key': _('cloud_preflight.no_key').format(url=cloud.read_dashboard_url()),
            'bad_key': _('cloud_preflight.bad_key').format(url=cloud.read_dashboard_url()),
            'no_balance': _('cloud_preflight.no_balance').format(url=cloud.read_topup_url()),
            'network': _('cloud_preflight.network'),
        }
        if problem not in messages:
            proceed()
            return
        dialog = utils.SimpleDialog(self, title=_('transcription_panel.error'))
        dialog.content.layout().addWidget(QLabel(messages[problem]))
        dialog.reject_button.setVisible(False)
        dialog.exec()

    worker.loaded.connect(lambda account: done(cloud.account_problem(account)))
    worker.failed.connect(done)
    worker.finished.connect(worker.deleteLater)
    worker.start()


def _start_transcription(self, scope_range):
    """Clear what a whole-media run replaces, then hand the job to the
    selected engine."""
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

    engine_panel = _current_engine_panel(self)
    if engine_panel is not None:
        engine_panel.transcript_callback()
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
    # Whenever the footer (re)appears, reconcile the Start-button / progress-bar
    # state from the single running flag so the two can never both show.
    _reconcile_transcription_footer(self)
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
    if hasattr(self, 'transcription_scope'):
        self.transcription_scope.retranslate()
        _reconcile_start_button(self)
    for widget in self.global_panel_import_tabwidget.findChildren(QWidget):
        if 'translate_callback' in dir(widget):
            widget.translate_callback()

    
