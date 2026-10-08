"""Shared SCOPE selector — the collapsible chip + bar used to limit an
operation to the whole media, a typed time range, or the selected subtitle.

Built first for the transcription panel; translation and dubbing ("generate
all speeches") now mount the same widget instead of their own radio-button
rows, so all three read and behave identically. ScopeFooter, below, is the
footer they sit in — scope row, hairline, action row — so the three panels
end the same way too.

The widget keeps the ``transcription_scope_*`` objectNames the stylesheet
already targets — they are style hooks, not panel identity, so every host
inherits the same look without duplicating QSS.

State persists per host under ``session.CONFIG[<section>]`` (``scope``,
``scope_expanded``, ``scope_from``, ``scope_to``), so each panel remembers
its own scope independently.
"""

from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSizePolicy, QStackedWidget, QStyle, QStyleOption,
                               QVBoxLayout, QWidget)
from PySide6.QtCore import Qt, QEasingCurve, QPropertyAnimation, QSize, Signal
from PySide6.QtGui import QPainter

from subtitld.interface.translation import _
from subtitld.modules import session

SCOPE_ALL = 'all'
SCOPE_RANGE = 'range'
SCOPE_SELECTION = 'selection'

# Scope ids, in dropdown order.
SCOPE_IDS = (SCOPE_ALL, SCOPE_RANGE, SCOPE_SELECTION)

# Qt's QWIDGETSIZE_MAX — the "no maximum" sentinel restored once the bar is
# fully open, so later field changes can still grow it.
_QWIDGETSIZE_MAX = 16777215

# What the footer's own rows inset themselves by. The band, its hairline
# and its gradient run to the panel's edges, so the chip and the action
# buttons carry this instead of leaning on the host panel's padding.
_SIDE_PADDING = 10

# Shortest range the From/To pair may describe. An edit that would make the
# range empty or inverted pushes the OTHER end out by at least this much
# instead of being rejected — see _normalize_range().
_MIN_RANGE = 0.05


def format_tc(seconds):
    """Seconds → ``HH:MM:SS.mmm`` (the scope timecode form)."""
    try:
        seconds = max(0.0, float(seconds))
    except (TypeError, ValueError):
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02d}:{m:02d}:{s:02d}.{ms:03d}'


def parse_tc(text):
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


def selected_segment():
    """The selected subtitle, if it is still on the timeline and spans a
    usable range. A selection-scoped run acts on exactly this subtitle."""
    sel = session.SUBTITLE.get('selected')
    if not isinstance(sel, dict):
        return None
    if not any(seg is sel for seg in session.SUBTITLE.get('segments', []) or []):
        return None
    try:
        if float(sel.get('end', 0.0)) <= float(sel.get('start', 0.0)):
            return None
    except (TypeError, ValueError):
        return None
    return sel


class _ScopeValueLabel(QLabel):
    """A computed time in the scope bar: the duration, and From/To under
    scope "selection".

    Plain text in the inputs' type rather than a box that would read as a
    disabled field. It elides instead of forcing the bar wider, so a narrow
    panel squeezes the value and not the fields beside it — a label's own
    minimum width is its whole text, which would raise the left panel's
    minimum width by the three times together.
    """

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, event):
        painter = QPainter(self)
        option = QStyleOption()
        option.initFrom(self)
        # Whatever the stylesheet paints behind the text (nothing today).
        self.style().drawPrimitive(QStyle.PE_Widget, option, painter, self)
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, rect.width())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(rect, int(self.alignment()), text)


class ScopeSelector(QWidget):
    """Collapsible SCOPE chip + bar.

    ``changed`` fires whenever the effective scope changes — a different
    scope, an edited/captured range, or a refresh that flipped readiness.
    Hosts connect it to re-evaluate their action button (see is_ready()).
    """

    changed = Signal()

    def __init__(self, config_section, parent=None):
        super().__init__(parent)
        self._section = config_section
        self._suppress = False        # guards config writes during refresh()
        self._last_ready = None       # so changed only fires on a real flip
        self._build()
        # Render the stored state straight away rather than leaving a blank bar
        # until the host's first update() — and materialise the config section
        # so a host that reads it before any refresh finds it there.
        self.refresh()

    # ---- config -------------------------------------------------------

    def _cfg(self):
        cfg = session.CONFIG.setdefault(self._section, {})
        if not isinstance(cfg, dict):
            cfg = {}
            session.CONFIG[self._section] = cfg
        return cfg

    # ---- construction -------------------------------------------------

    def _build(self):
        def _labeled_field(field_widget, label_attr, *controls):
            v = QVBoxLayout(field_widget)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            lbl = QLabel()
            lbl.setProperty('class', 'widget_label')
            setattr(self, label_attr, lbl)
            v.addWidget(lbl)
            for control in controls:
                v.addWidget(control)

        def _value_label():
            value = _ScopeValueLabel()
            value.setProperty('class', 'transcription_scope_value')
            return value

        def _capture_row(line_edit, button_attr):
            """[input][⤓] — the capture button sits directly right of the
            input it stamps, so which field it fills is unambiguous."""
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            h.addWidget(line_edit, 1)
            button = QPushButton('⤓')
            button.setObjectName('scope_range_capture_button')
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda: self._capture_playhead(line_edit))
            setattr(self, button_attr, button)
            h.addWidget(button)
            return row

        self.setObjectName('transcription_scope_area')
        self.setAttribute(Qt.WA_StyledBackground, True)
        scope_v = QVBoxLayout(self)
        # No side padding — the expanded bar spans the host's whole content
        # width. Top breathing room keeps the chip off the content above.
        scope_v.setContentsMargins(0, 8, 0, 0)
        scope_v.setSpacing(0)

        # Collapse/expand chip, right-aligned with the action button below it.
        # The inset is the chip's own: the band behind it runs to the panel's
        # edges, so nothing here may rely on the host's padding.
        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(0, 0, _SIDE_PADDING, 0)
        chip_row.addStretch()
        self.chip = QPushButton()
        self.chip.setObjectName('transcription_scope_chip')
        self.chip.setCursor(Qt.PointingHandCursor)
        self.chip.clicked.connect(self._toggle)
        chip_row.addWidget(self.chip)
        scope_v.addLayout(chip_row)

        # The bar itself (collapsible). Two rows: what the scope IS on top,
        # the times it spans below.
        #
        # One row: SCOPE, FROM, DURATION, TO. The time inputs and the
        # DURATION label shrink (the label elides) rather than wrap, so a
        # narrow panel squeezes the values instead of splitting the bar.
        self.bar = QWidget()
        self.bar.setObjectName('transcription_scope_bar')
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(12, 8, 12, 10)
        bar.setSpacing(12)

        scope_field = QWidget()
        self.combobox = QComboBox()
        self.combobox.setObjectName('transcription_scope_combobox')
        # One item per SCOPE_IDS entry, in order. Labels are set in retranslate().
        self.combobox.addItems(['All', 'Range', 'Selection'])
        self.combobox.activated.connect(self._combobox_changed)
        _labeled_field(scope_field, 'scope_label', self.combobox)
        bar.addWidget(scope_field)

        self.from_field = QWidget()
        self.from_input = QLineEdit()
        self.from_input.setObjectName('transcription_scope_input')
        self.from_input.setPlaceholderText('00:00:00.000')
        self.from_input.editingFinished.connect(lambda: self._field_edited(SCOPE_RANGE + '_from'))
        self.from_row = _capture_row(self.from_input, 'from_capture_button')
        # Scope "selection" takes its times from the selected subtitle, so the
        # field shows this label instead of the editable input.
        self.from_value = _value_label()
        _labeled_field(self.from_field, 'from_label', self.from_row, self.from_value)
        bar.addWidget(self.from_field, 1)

        self.duration_field = QWidget()
        self.duration_value = _ScopeValueLabel()
        self.duration_value.setObjectName('transcription_scope_duration_value')
        self.duration_value.setProperty('class', 'transcription_scope_value')
        _labeled_field(self.duration_field, 'duration_label', self.duration_value)
        # No stretch: DURATION is a read-only summary, so the slack goes to
        # the two fields the user types in.
        bar.addWidget(self.duration_field, 0)

        self.to_field = QWidget()
        self.to_input = QLineEdit()
        self.to_input.setObjectName('transcription_scope_input')
        self.to_input.setPlaceholderText('00:00:00.000')
        self.to_input.editingFinished.connect(lambda: self._field_edited(SCOPE_RANGE + '_to'))
        self.to_row = _capture_row(self.to_input, 'to_capture_button')
        self.to_value = _value_label()
        _labeled_field(self.to_field, 'to_label', self.to_row, self.to_value)
        bar.addWidget(self.to_field, 1)

        # Why the action button is dead, in the space the times would use:
        # scope "selection" with nothing selected has no times to show.
        self.hint_label = QLabel()
        self.hint_label.setObjectName('scope_hint_label')
        self.hint_label.setWordWrap(True)
        # Seeded here, not left to retranslate(): a host that never runs its
        # translate() pass would otherwise show a BLANK hint beside a disabled
        # button, which explains nothing.
        self.hint_label.setText(_('panel_scope.selection_empty'))
        self.hint_label.setVisible(False)
        bar.addWidget(self.hint_label, 1)

        scope_v.addWidget(self.bar)


        # Vertical slide for expand/collapse: animate the bar's maxHeight.
        # Parented to the bar so it's cleaned up with it.
        self._bar_anim = QPropertyAnimation(self.bar, b'maximumHeight', self.bar)
        self._bar_anim.setDuration(170)
        self._bar_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._expanded_state = None  # None until first refresh → no anim on load

        # Once the open animation finishes, drop the maxHeight clamp so later
        # field changes (e.g. Range adds From/To) can still grow the bar. On a
        # collapse the state is False, so this is a no-op and the bar stays 0.
        def _release_clamp():
            if getattr(self, '_expanded_state', False):
                self.bar.setMaximumHeight(_QWIDGETSIZE_MAX)
        self._bar_anim.finished.connect(_release_clamp)

    def _set_expanded(self, expanded):
        """Show/hide the scope bar with a vertical slide.

        Animates only when the expanded state actually flips — a plain field
        refresh (scope change, edit) snaps instead of re-sliding, and the very
        first render snaps too (no animation on panel load)."""
        bar = self.bar
        bar.setVisible(True)  # always laid out; maxHeight drives visibility
        natural = bar.sizeHint().height()

        prev = self._expanded_state
        self._expanded_state = expanded

        anim = self._bar_anim
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

    # ---- range normalisation ------------------------------------------

    def _media_duration(self):
        try:
            return max(0.0, float(session.VIDEO.get('duration', 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _normalize_range(self, f, t, edited):
        """Resolve From/To into a valid, non-empty, in-bounds range.

        `edited` names the end the user just set ('from', 'to', or None) —
        that end is honoured and the OTHER one moves. Nothing is ever
        rejected with an error: an inverted or empty range self-corrects in
        the direction the user was not touching, which keeps typing and
        playhead-capture fluid (capture From past To, then capture To, and
        both land where you meant).

        The gap the moved end is pushed to is the range's previous duration
        when there was one, so nudging From along a 30s range slides the
        whole window instead of collapsing it to _MIN_RANGE.
        """
        total = self._media_duration()
        ceiling = total if total > 0 else None

        def _clamp(v):
            v = max(0.0, float(v))
            return min(v, ceiling) if ceiling is not None else v

        f, t = _clamp(f), _clamp(t)
        if t > f:
            return f, t

        # Empty or inverted. Keep the previous width if we had one.
        prev_f, prev_t = self._last_range
        gap = max(_MIN_RANGE, (prev_t - prev_f) if prev_t > prev_f else _MIN_RANGE)

        if edited == 'to':
            # The user set To → pull From back to make room.
            f = max(0.0, t - gap)
            if t <= f:                       # To sat at/below the floor
                t = _clamp(f + _MIN_RANGE)
                if t <= f:                   # degenerate media (duration ~0)
                    f = max(0.0, t - _MIN_RANGE)
        else:
            # The user set From (or we're normalising stored config) → push
            # To out. If that hits the end of the media, back From off instead
            # so the range stays inside the media rather than clamping to a
            # zero-width sliver at the very end.
            t = _clamp(f + gap)
            if t <= f:
                f = max(0.0, t - gap)
                if t <= f:
                    f = max(0.0, t - _MIN_RANGE)
        return f, t

    @property
    def _last_range(self):
        cfg = self._cfg()
        try:
            return (float(cfg.get('scope_from', 0.0) or 0.0),
                    float(cfg.get('scope_to', 0.0) or 0.0))
        except (TypeError, ValueError):
            return (0.0, 0.0)

    def _capture_playhead(self, line_edit):
        """Stamp the current playback position into `line_edit`, then
        normalise the pair so the capture can't leave an inverted range."""
        pos = 0.0
        try:
            pos = float(session.SUBTITLE.get('position', 0) or 0)
        except (TypeError, ValueError):
            pos = 0.0
        line_edit.setText(format_tc(pos))
        self._field_edited(SCOPE_RANGE + ('_from' if line_edit is self.from_input else '_to'))

    # ---- events -------------------------------------------------------

    def _toggle(self):
        cfg = self._cfg()
        cfg['scope_expanded'] = not bool(cfg.get('scope_expanded', False))
        self.refresh()

    def _combobox_changed(self):
        cfg = self._cfg()
        idx = self.combobox.currentIndex()
        if 0 <= idx < len(SCOPE_IDS):
            cfg['scope'] = SCOPE_IDS[idx]
        self.refresh()
        self.changed.emit()

    def _field_edited(self, which):
        if self._suppress:
            return
        cfg = self._cfg()
        f = parse_tc(self.from_input.text())
        t = parse_tc(self.to_input.text())
        # An unparseable field falls back to what was stored, so a typo in one
        # input can't silently zero the other.
        prev_f, prev_t = self._last_range
        f = prev_f if f is None else f
        t = prev_t if t is None else t
        edited = 'to' if which.endswith('_to') else 'from'
        f, t = self._normalize_range(f, t, edited)
        cfg['scope_from'], cfg['scope_to'] = f, t
        self.refresh()
        self.changed.emit()

    # ---- public API ---------------------------------------------------

    @property
    def scope(self):
        scope = self._cfg().get('scope', SCOPE_ALL)
        return scope if scope in SCOPE_IDS else SCOPE_ALL

    def current_range(self):
        """The [from, to] seconds this run should cover, or None for the
        whole media (scope 'all', or an unusable selection)."""
        scope = self.scope
        if scope == SCOPE_RANGE:
            f = parse_tc(self.from_input.text())
            t = parse_tc(self.to_input.text())
            if f is not None and t is not None and t > f:
                return (f, t)
            return None
        if scope == SCOPE_SELECTION:
            sel = selected_segment()
            if sel is not None:
                return (float(sel.get('start', 0.0)), float(sel.get('end', 0.0)))
            return None
        return None

    def selected_segment(self):
        """The subtitle a selection-scoped run targets, or None."""
        return selected_segment() if self.scope == SCOPE_SELECTION else None

    def is_ready(self):
        """False only when the scope cannot be satisfied right now — today
        that is scope "selection" with no usable subtitle selected. Hosts
        disable their action button on False."""
        if self.scope == SCOPE_SELECTION:
            return selected_segment() is not None
        return True

    def scoped_segments(self, segments=None):
        """The subset of `segments` this scope covers.

        Range uses OVERLAP (a subtitle straddling the boundary is included),
        which is what translation and dubbing have always done — a partly
        in-range line still needs translating."""
        if segments is None:
            segments = session.SUBTITLE.get('segments', []) or []
        scope = self.scope
        if scope == SCOPE_SELECTION:
            sel = selected_segment()
            if sel is None:
                return []
            # Honour the caller's subset: a dubbing speaker panel passes only
            # its own speaker's lines, and the selected subtitle may belong to
            # someone else — synthesizing it would put the wrong voice on it.
            return [sel] if any(s is sel for s in segments) else []
        if scope == SCOPE_RANGE:
            rng = self.current_range()
            if rng is None:
                return list(segments)
            f, t = rng
            return [s for s in segments
                    if float(s.get('end', 0) or 0) > f and float(s.get('start', 0) or 0) < t]
        return list(segments)

    def refresh(self):
        """Re-read config + timeline selection and update every widget."""
        cfg = self._cfg()
        scope = cfg.get('scope', SCOPE_ALL)
        if scope not in SCOPE_IDS:
            scope = SCOPE_ALL
            cfg['scope'] = scope
        expanded = bool(cfg.get('scope_expanded', False))
        total = self._media_duration()

        self._suppress = True
        try:
            self.combobox.blockSignals(True)
            self.combobox.setCurrentIndex(SCOPE_IDS.index(scope))
            self.combobox.blockSignals(False)

            # Chip: "<SCOPE>  <icon>" — ≡ (expand) when collapsed, − (collapse) when open.
            scope_name = self.combobox.currentText() or scope.upper()
            self.chip.setText(f'{scope_name.upper()}   {"−" if expanded else "≡"}')

            self._set_expanded(expanded)

            # Field visibility + values per scope. Scope "all" spans the whole
            # media, so it has no FROM/TO to show.
            show_range = scope in (SCOPE_RANGE, SCOPE_SELECTION)
            self.from_field.setVisible(show_range)
            self.to_field.setVisible(show_range)

            # Range is typed in (and capturable from the playhead); selection is
            # read off the selected subtitle, so its times show as plain labels.
            editable = scope == SCOPE_RANGE
            self.from_row.setVisible(editable)
            self.to_row.setVisible(editable)
            self.from_value.setVisible(not editable)
            self.to_value.setVisible(not editable)

            if scope == SCOPE_ALL:
                self.duration_value.setText(format_tc(total))
            elif scope == SCOPE_RANGE:
                f = float(cfg.get('scope_from', 0.0) or 0.0)
                raw_t = cfg.get('scope_to')
                t = float(raw_t) if raw_t not in (None, '') else total
                # Stored values can be stale (a shorter media was loaded since,
                # or an older build wrote an inverted pair), so they go through
                # the same normalisation a live edit does.
                f, t = self._normalize_range(f, t, None)
                cfg['scope_from'], cfg['scope_to'] = f, t
                if not self.from_input.hasFocus():
                    self.from_input.setText(format_tc(f))
                if not self.to_input.hasFocus():
                    self.to_input.setText(format_tc(t))
                self.duration_value.setText(format_tc(max(0.0, t - f)))
            else:  # selection — the selected subtitle's own times.
                sel = selected_segment()
                values = (self.from_value, self.duration_value, self.to_value)
                if sel is not None:
                    f = float(sel.get('start', 0.0))
                    t = float(sel.get('end', 0.0))
                    for value, text in zip(values, (f, max(0.0, t - f), t)):
                        value.setText(format_tc(text))
                else:
                    for value in values:
                        value.setText('—')

            # The hint stands in for the times it has none of. A collapsed
            # bar hides it, so the host also puts it on the dead button as a
            # tooltip (see is_ready / hint_text).
            nothing_selected = scope == SCOPE_SELECTION and selected_segment() is None
            self.hint_label.setVisible(nothing_selected)
            if nothing_selected:
                for field in (self.from_field, self.duration_field, self.to_field):
                    field.setVisible(False)
        finally:
            self._suppress = False

        # Let the host re-evaluate its action button when readiness flipped
        # (e.g. the user selected a subtitle while scope was "selection").
        ready = self.is_ready()
        if ready != self._last_ready:
            self._last_ready = ready
            self.changed.emit()

    def hint_text(self):
        """What stops this scope from running, or '' when nothing does.

        The hint is inside the bar, which the user can collapse, so a host
        also puts this on its disabled action button.
        """
        return '' if self.is_ready() else self.hint_label.text()

    def retranslate(self):
        self.scope_label.setText(_('panel_scope.label'))
        self.from_label.setText(_('panel_scope.from'))
        self.duration_label.setText(_('panel_scope.duration'))
        self.to_label.setText(_('panel_scope.to'))
        self.hint_label.setText(_('panel_scope.selection_empty'))
        for button in (self.from_capture_button, self.to_capture_button):
            button.setToolTip(_('panel_scope.capture_tooltip'))
        labels = [_('panel_scope.all'), _('panel_scope.range'), _('panel_scope.selection')]
        for i, text in enumerate(labels):
            self.combobox.setItemText(i, text)
        # The times beside it are labels, which never shrink, so a narrow
        # panel would squeeze the scope name away. Hold its widest label.
        self.combobox.setMinimumContentsLength(max(len(text) for text in labels))
        self.combobox.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.refresh()


class ScopeFooter(QWidget):
    """The panel footer the SCOPE selector sits in: the chip + bar, a
    hairline, and a row for the panel's own action buttons.

    Transcription, translation and dubbing all mount this, so their footers
    are the same thing rather than three near-copies: every row runs edge to
    edge inside the panel's 10px padding, and the hairline separates the
    scope row from the buttons under it.

    With ``section=None`` there is no scope: just the hairline and the
    action row — the same bottom line for a panel whose action has no time
    range (the speakers panel's Add speaker).
    """

    def __init__(self, section, parent=None):
        super().__init__(parent)
        self.setObjectName('scope_footer')
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        # Full-bleed: the hairline has to reach both edges, so any inset is
        # the panel's own padding, never the footer's.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.selector = ScopeSelector(section, parent=self) if section is not None else None
        if self.selector is not None:
            layout.addWidget(self.selector)

        self.divider = QWidget()
        self.divider.setObjectName('scope_footer_divider')
        self.divider.setAttribute(Qt.WA_StyledBackground, True)
        self.divider.setFixedHeight(1)
        layout.addWidget(self.divider)

        # The action row: two pages, switched rather than re-margined.
        # The buttons' page is inset, for the same reason as the chip — the
        # row's gradient reaches the panel's edges, its buttons do not,
        # including at the bottom, where the panel ends under this row. The
        # progress page is not inset at all: while a job runs, its bar fills
        # the whole row, edge to edge. A stack is as tall as its tallest
        # page, so the row keeps one height in both states. (Swapping pages
        # instead of editing margins also avoids a frame painted with the
        # bar inset: QProgressBar.setValue repaints before a posted
        # re-layout runs.)
        self.row = QStackedWidget()
        self.row.setObjectName('scope_footer_row')
        self.row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.actions_page = QWidget()
        actions_layout = QHBoxLayout(self.actions_page)
        actions_layout.setContentsMargins(_SIDE_PADDING, 0, _SIDE_PADDING, _SIDE_PADDING)
        actions_layout.setSpacing(0)
        self.row.addWidget(self.actions_page)

        self.progress_page = QWidget()
        progress_layout = QHBoxLayout(self.progress_page)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(0)
        self.row.addWidget(self.progress_page)

        self.row.setCurrentWidget(self.actions_page)
        layout.addWidget(self.row)

    @property
    def changed(self):
        """The selector's signal, so a host can wire the footer itself."""
        return self.selector.changed if self.selector is not None else None

    def add_action(self, widget, alignment=Qt.AlignRight, stretch=0, primary=False):
        """Put a button — or the progress bar that stands in for one — in the
        action row. Right-aligned by default, where every panel's primary
        action sits; pass ``alignment=None`` for something that should span
        the row instead (an alignment pins a widget to its size hint, which
        would cancel the stretch).

        ``primary=True`` gives the panel's main action the shared look (the
        light key the Start buttons wear), so the three footers end alike.
        """
        if primary:
            widget.setProperty('class', 'scope_action')
            # A collapsed bar hides the hint, so the button carries it while
            # it is disabled.
            self._primary = widget
            if self.selector is not None:
                self.selector.changed.connect(self._explain_primary)
                self._explain_primary()
        if alignment is None:
            self.actions_page.layout().addWidget(widget, stretch)
        else:
            self.actions_page.layout().addWidget(widget, stretch, alignment)
        return widget

    def add_stretch(self):
        """Push the actions added after this to the row's far end."""
        self.actions_page.layout().addStretch(1)

    def set_progress_bar(self, bar):
        """The bar shown in place of the buttons while a job runs. It fills
        the row edge to edge; ``show_progress`` switches to it."""
        bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.progress_page.layout().addWidget(bar)
        return bar

    def show_progress(self, running):
        """Swap the buttons for the progress bar (True) or back (False).

        The scope chip and bar go while a job runs: the scope is fixed once
        the job has started, so there is nothing to change there.
        """
        self.row.setCurrentWidget(self.progress_page if running else self.actions_page)
        if self.selector is not None:
            self.selector.setVisible(not running)

    def is_showing_progress(self):
        return self.row.currentWidget() is self.progress_page

    def _explain_primary(self):
        button = getattr(self, '_primary', None)
        if button is not None and self.selector is not None:
            button.setToolTip(self.selector.hint_text())

    def refresh(self):
        if self.selector is not None:
            self.selector.refresh()
        self._explain_primary()

    def retranslate(self):
        if self.selector is not None:
            self.selector.retranslate()
