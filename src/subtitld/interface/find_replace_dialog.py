"""Find & Replace dialog for Subtitld subtitle cues.

Direction B from the 2026 enhanced-components handoff: a compact classic
modal. Built on the shared ``utils.SimpleDialog`` grammar (slate title
bar, translucent body, custom footer) so the visual language matches
every other dialog in the app (Export, Addons, speaker editors, …)
without re-inventing chrome.

Layout (top → bottom):

* Title bar (handled by SimpleDialog) — "FIND & REPLACE", uppercase.
* Body
    - "FIND" label + Find input with three inline 22×22 toggles
      (``Aa`` match-case / ``ab`` whole-word / ``.*`` regex).
    - "REPLACE WITH" label + Replace input (no toggles).
* Footer — match count on the left, three buttons on the right
  (Find next · Replace · Replace all).

The default SimpleDialog footer ships an OK/Cancel pair, which doesn't
fit a 3-button right cluster with a status counter on the left. Rather
than fork the base class, we reach in via ``self.accept_button.parent()``
(the same handle ``ExportDialog`` uses for its processing-state swap),
clear the layout, and rebuild the footer in place. This keeps the
QSS-targeted ``#dialog_bottom`` styling intact (top border, slate fill,
corner radii) so the dialog still reads as part of the family.

State lives on the dialog instance:

* ``_match_case`` (default True), ``_whole_word`` (default False),
  ``_regex`` (default False).
* ``_matches`` — list of ``(segment_dict, char_start, char_end)``,
  recomputed lazily on any Find-text edit or toggle flip.
* ``_active_index`` — which match Find-next will jump to.

Mutation is direct-on-dict assignment of ``segment['text']`` (the
established codebase convention — see
``left_panel_subtitleslist_textedit_changed``), followed by
``session.set_unsaved(True)`` and a host-side timeline + list refresh.

The host window is captured at construction time as ``self._host`` so
Find-next / Replace / Replace-all can re-render the cue list and
timeline through it the same way any other in-app text edit would.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules.shortcuts import shortcut


# ---------------------------------------------------------------------------
# Compiled-pattern builder
# ---------------------------------------------------------------------------


def _build_pattern(needle: str, match_case: bool, whole_word: bool, regex: bool):
    """Translate the find text + toggles into a compiled regex.

    Returns ``None`` for empty needles or for malformed user-supplied
    regex (in regex mode) — callers treat ``None`` as "zero matches"
    and disable the action buttons.
    """
    if not needle:
        return None
    flags = 0 if match_case else re.IGNORECASE
    if regex:
        # User-supplied regex — surface a malformed expression as
        # "no matches" rather than crashing the dialog.
        try:
            pattern = re.compile(needle, flags)
        except re.error:
            return None
    else:
        pattern = re.escape(needle)
        if whole_word:
            # ``\b`` is Unicode-aware under default re flags — good enough
            # for the subtitle text we're dealing with.
            pattern = r'\b' + pattern + r'\b'
        pattern = re.compile(pattern, flags)
    return pattern


# ---------------------------------------------------------------------------
# Inline 22×22 toggle button used inside the Find field.
# ---------------------------------------------------------------------------


class _ToggleButton(QPushButton):
    """Square checkable button — `Aa` / `ab` / `.*` style.

    Styled via the ``find_toggle`` QSS class so the on/off/hover states
    live in the stylesheet rather than per-instance palette code.
    """

    def __init__(self, label: str, *, underline: bool = False, tooltip: str = '', parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(22, 22)
        self.setProperty('class', 'find_toggle')
        if underline:
            # The "whole word" toggle's `ab` glyph is conventionally
            # underlined to distinguish it from the case toggle. Plain
            # rich text — the QSS sets weight/size at the button level.
            self.setText(f'<u>{label}</u>')
        else:
            self.setText(label)
        if tooltip:
            self.setToolTip(tooltip)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)


# ---------------------------------------------------------------------------
# Search field shell — dark input box + optional right-aligned toggles.
# ---------------------------------------------------------------------------


class _SearchField(QFrame):
    """Composite input: a QLineEdit framed by a dark slate shell, with
    an optional toggle group right-aligned inside the same row.

    Visually matches the handoff spec (height 30px, 2px radius, inset
    1px outline) — that styling is applied via the ``find_replace_field``
    object name in the QSS.
    """

    def __init__(self, placeholder: str = '', *, with_toggles: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName('find_replace_field')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout()
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(4)
        self.setLayout(layout)

        self.lineedit = QLineEdit()
        self.lineedit.setObjectName('find_replace_lineedit')
        self.lineedit.setPlaceholderText(placeholder)
        # The line edit borrows the parent's slate fill so the frame
        # outline reads as the input's outline (no double border).
        self.lineedit.setFrame(False)
        layout.addWidget(self.lineedit, 1)

        if with_toggles:
            self.toggle_row = QWidget()
            self.toggle_row.setLayout(QHBoxLayout())
            self.toggle_row.layout().setContentsMargins(0, 0, 0, 0)
            self.toggle_row.layout().setSpacing(2)

            self.match_case_toggle = _ToggleButton(
                'Aa',
                tooltip=_('find_replace_dialog.match_case'),
            )
            self.match_case_toggle.setChecked(True)  # default ON per handoff

            self.whole_word_toggle = _ToggleButton(
                'ab',
                underline=True,
                tooltip=_('find_replace_dialog.whole_word'),
            )

            self.regex_toggle = _ToggleButton(
                '.*',
                tooltip=_('find_replace_dialog.regex'),
            )

            for tg in (self.match_case_toggle, self.whole_word_toggle, self.regex_toggle):
                self.toggle_row.layout().addWidget(tg)
            layout.addWidget(self.toggle_row, 0, Qt.AlignRight | Qt.AlignVCenter)
        else:
            self.match_case_toggle = None
            self.whole_word_toggle = None
            self.regex_toggle = None


# ---------------------------------------------------------------------------
# The dialog itself.
# ---------------------------------------------------------------------------


class FindReplaceDialog(utils.SimpleDialog):
    """Find & Replace modal — see module docstring for layout / state."""

    def __init__(self, host):
        # The host window is what owns ``session.SUBTITLE`` + the
        # timeline + the cue list. We re-render through it after every
        # mutation so the user sees changes land on the same widgets
        # they'd see from any other edit.
        super().__init__(parent=host, title=_('find_replace_dialog.title'))
        self._host = host
        self.setObjectName('find_replace_dialog')
        # 440px width per the handoff; height auto-sizes to content.
        self.setMinimumWidth(440)
        self.setMaximumWidth(560)

        # Toggle state — defaults match the handoff (case ON, others OFF).
        self._matches: list[tuple[dict, int, int]] = []
        self._active_index: int = 0

        # ------------------------------------------------------------------
        # Body
        # ------------------------------------------------------------------
        content_layout = self.content.layout()
        content_layout.setSpacing(0)
        content_layout.setContentsMargins(16, 14, 16, 14)

        find_label = QLabel(_('find_replace_dialog.find_label'))
        find_label.setObjectName('find_replace_dialog_label')
        find_label.setProperty('class', 'find_replace_section_label')
        content_layout.addWidget(find_label)
        content_layout.addSpacing(5)

        self._find_field = _SearchField(
            placeholder=_('find_replace_dialog.find_placeholder'),
            with_toggles=True,
        )
        content_layout.addWidget(self._find_field)

        content_layout.addSpacing(12)

        replace_label = QLabel(_('find_replace_dialog.replace_label'))
        replace_label.setObjectName('find_replace_dialog_label')
        replace_label.setProperty('class', 'find_replace_section_label')
        content_layout.addWidget(replace_label)
        content_layout.addSpacing(5)

        self._replace_field = _SearchField(
            placeholder=_('find_replace_dialog.replace_placeholder'),
            with_toggles=False,
        )
        content_layout.addWidget(self._replace_field)

        # ------------------------------------------------------------------
        # Footer — rebuild the SimpleDialog bottom row into:
        #   [status text]  <stretch>  [find next] [replace] [replace all]
        #
        # SimpleDialog exposes the footer as ``self.bottom_line``. This used
        # to walk ``self.accept_button.parent()``, which broke silently once
        # the default button moved into the bottom-right tab — the parent is
        # then the tab, not the footer. Clearing the layout in place keeps
        # the QSS ``#dialog_bottom`` styling.
        # ------------------------------------------------------------------
        bottom_widget = self.bottom_line
        bottom_layout = bottom_widget.layout()
        while bottom_layout.count():
            item = bottom_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

        bottom_layout.setContentsMargins(14, 0, 14, 0)
        bottom_layout.setSpacing(6)

        self._status_label = QLabel()
        self._status_label.setObjectName('find_replace_dialog_status')
        self._status_label.setProperty('class', 'find_replace_status')
        self._status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bottom_layout.addWidget(self._status_label, 1, Qt.AlignLeft | Qt.AlignVCenter)

        self._find_next_button = QPushButton(_('find_replace_dialog.find_next'))
        self._find_next_button.setProperty('class', 'find_replace_button')
        bottom_layout.addWidget(self._find_next_button, 0, Qt.AlignRight | Qt.AlignVCenter)

        self._replace_button = QPushButton(_('subtitles_panel.replace'))
        self._replace_button.setProperty('class', 'find_replace_button')
        bottom_layout.addWidget(self._replace_button, 0, Qt.AlignRight | Qt.AlignVCenter)

        self._replace_all_button = QPushButton(_('subtitles_panel.replace_all'))
        self._replace_all_button.setProperty('class', 'find_replace_button_primary')
        bottom_layout.addWidget(self._replace_all_button, 0, Qt.AlignRight | Qt.AlignVCenter)

        # ------------------------------------------------------------------
        # Wiring
        # ------------------------------------------------------------------
        self._find_field.lineedit.textChanged.connect(self._recompute_matches)
        self._replace_field.lineedit.textChanged.connect(self._update_buttons_state)

        self._find_field.match_case_toggle.toggled.connect(self._on_toggle_changed)
        self._find_field.whole_word_toggle.toggled.connect(self._on_toggle_changed)
        self._find_field.regex_toggle.toggled.connect(self._on_toggle_changed)

        # Enter inside the Find field steps through matches without the
        # user having to reach for the footer button.
        self._find_field.lineedit.returnPressed.connect(self._on_find_next)
        # Enter inside the Replace field replaces-and-advances — the
        # standard convention for replace dialogs (Sublime, VS Code, …).
        self._replace_field.lineedit.returnPressed.connect(self._on_replace)

        self._find_next_button.clicked.connect(self._on_find_next)
        self._replace_button.clicked.connect(self._on_replace)
        self._replace_all_button.clicked.connect(self._on_replace_all)

        # Esc / close X dismiss — already wired by SimpleDialog's
        # close_button to ``self.reject()``. Nothing to add.

        # Initial state.
        self._recompute_matches()

    # ----------------------------------------------------------------------
    # Search state
    # ----------------------------------------------------------------------
    def _on_toggle_changed(self, _checked: bool) -> None:
        self._recompute_matches()

    def _recompute_matches(self, *_args) -> None:
        needle = self._find_field.lineedit.text()
        pattern = _build_pattern(
            needle,
            match_case=self._find_field.match_case_toggle.isChecked(),
            whole_word=self._find_field.whole_word_toggle.isChecked(),
            regex=self._find_field.regex_toggle.isChecked(),
        )
        matches: list[tuple[dict, int, int]] = []
        if pattern is not None:
            for segment in session.SUBTITLE.get('segments', []) or []:
                text = segment.get('text') or ''
                for m in pattern.finditer(text):
                    # ``re`` can yield zero-width matches (e.g. user-typed
                    # ``\b``); skip them — they'd cause an infinite Find-
                    # next loop and aren't meaningful for Replace.
                    if m.end() == m.start():
                        continue
                    matches.append((segment, m.start(), m.end()))
        self._matches = matches
        # Clamp active index — count may have shrunk since last edit.
        if self._matches:
            self._active_index %= len(self._matches)
        else:
            self._active_index = 0
        self._update_status()
        self._update_buttons_state()

    def _update_status(self) -> None:
        count = len(self._matches)
        if count == 0:
            self._status_label.setText(_('find_replace_dialog.no_matches'))
        elif count == 1:
            self._status_label.setText(_('find_replace_dialog.match_count_one'))
        else:
            self._status_label.setText(
                _('find_replace_dialog.match_count_many').format(count=count)
            )

    def _update_buttons_state(self, *_args) -> None:
        has_find_text = bool(self._find_field.lineedit.text())
        has_matches = bool(self._matches)
        self._find_next_button.setEnabled(has_matches)
        # Replace / Replace-all are valid even if the replacement string
        # is empty (the user might intentionally want to *delete* every
        # occurrence). Only gate on whether there's something TO replace.
        self._replace_button.setEnabled(has_matches)
        self._replace_all_button.setEnabled(has_matches)
        # Visual opacity for disabled state is handled by the QSS rule
        # on ``find_replace_button:disabled``.

    # ----------------------------------------------------------------------
    # Actions
    # ----------------------------------------------------------------------
    def _on_find_next(self) -> None:
        """Advance selection to the next match (wrap at end)."""
        if not self._matches:
            return
        segment, _start, _end = self._matches[self._active_index]
        self._select_segment(segment)
        self._active_index = (self._active_index + 1) % len(self._matches)

    def _on_replace(self) -> None:
        """Replace the currently-active match, then advance."""
        if not self._matches:
            return
        segment, start, end = self._matches[self._active_index]
        replacement = self._replace_field.lineedit.text()
        old_text = segment.get('text') or ''
        segment['text'] = old_text[:start] + replacement + old_text[end:]
        session.set_unsaved(True)
        # Re-derive matches: offsets shifted, count may have changed.
        # Try to keep the user roughly where they were — same segment
        # if it still has matches, otherwise the next-along match.
        previous_index = self._active_index
        self._recompute_matches()
        if self._matches:
            self._active_index = previous_index % len(self._matches)
        self._select_segment(segment)
        self._refresh_host()

    def _on_replace_all(self) -> None:
        """Replace every match across every segment, atomically."""
        if not self._matches:
            return
        needle = self._find_field.lineedit.text()
        pattern = _build_pattern(
            needle,
            match_case=self._find_field.match_case_toggle.isChecked(),
            whole_word=self._find_field.whole_word_toggle.isChecked(),
            regex=self._find_field.regex_toggle.isChecked(),
        )
        if pattern is None:
            return
        replacement = self._replace_field.lineedit.text()
        # Mutate each segment in place — the cue list and timeline read
        # from these dicts, so the next host refresh shows the new text.
        affected_segments = set()
        for segment in session.SUBTITLE.get('segments', []) or []:
            text = segment.get('text') or ''
            new_text, n = pattern.subn(replacement, text)
            if n > 0:
                segment['text'] = new_text
                affected_segments.add(id(segment))
        if affected_segments:
            session.set_unsaved(True)
        self._recompute_matches()  # → "0 matches" (typically)
        self._refresh_host()

    # ----------------------------------------------------------------------
    # Host integration
    # ----------------------------------------------------------------------
    def _select_segment(self, segment: dict) -> None:
        """Make ``segment`` the active cue in the host's editor AND
        physically navigate the UI to it.

        Concretely:

        * Stash the dict in ``session.SUBTITLE['selected']`` (what the
          cue list's own click handler does first).
        * Seek the video preview to the cue's start. ``set_position`` is
          unconditional — we deliberately don't gate on ``is_paused()``
          the way the click-handler path does, because Find-next is an
          *explicit* navigation request, unlike an incidental row click
          during playback.
        * Run the canonical ``left_panel_subtitleslist.update(host)``
          flow. That refreshes the cue list, syncs the text editor,
          updates the speaker selector, and repaints the timeline
          against the new playhead.
        * Explicitly scroll the matched row into view. The list's
          ``update_content`` only scrolls when the playhead drifts
          across cue boundaries during playback — for a find-jump to
          an off-screen row we need ``EnsureVisible`` ourselves.
        """
        session.SUBTITLE['selected'] = segment
        self._refresh_host(seek_to=segment)

    def _refresh_host(self, seek_to: dict | None = None) -> None:
        """Refresh the host UI after a session-state mutation.

        ``seek_to`` is the segment Find-next or Replace just navigated
        to — when provided, the preview player seeks to its start and
        the cue list scrolls it into view. ``Replace-all`` passes
        ``None`` (no specific target row to jump to).
        """
        host = self._host
        if host is None:
            return

        # 1) Seek the video preview. The timeline reads from the
        #    player's current position, so seeking here is what makes
        #    the timeline cursor visibly jump to the matched cue.
        if seek_to is not None:
            try:
                host.preview_panel_player.set_position(float(seek_to.get('start', 0.0)))
            except (AttributeError, RuntimeError, TypeError):
                pass

        # 2) Run the canonical post-selection flow — same code path the
        #    cue list's own row-click handler uses, so the text editor,
        #    speaker selector, properties row, and list highlight all
        #    update in lockstep. Wrapped in try/except so a partially-
        #    mounted host (e.g. dialog opened before the production
        #    screen is ready) can't take out the mutation we already
        #    applied to ``session.SUBTITLE``.
        try:
            from subtitld.interface import left_panel_subtitleslist
            left_panel_subtitleslist.update(host)
        except (AttributeError, RuntimeError, ImportError):
            pass

        # 3) Scroll the matched row into view. The list's own
        #    update_content() sets the current index (so the row gets
        #    highlighted) but only auto-scrolls when the playhead drifts
        #    across segment boundaries during playback — not when the
        #    selection moves under it. For an off-screen find-jump
        #    that's exactly the case we need to handle.
        if seek_to is not None:
            try:
                qlist = host.subtitles_panel_qlistwidget
                qlist.scrollTo(
                    qlist.model.get_index(seek_to),
                    QAbstractItemView.EnsureVisible,
                )
            except (AttributeError, RuntimeError, ValueError):
                pass

        # 4) Timeline repaint. ``update()`` here is the Qt repaint —
        #    the playhead has already moved via set_position above; this
        #    just re-renders the new position.
        try:
            host.timeline_widget.update()
        except (AttributeError, RuntimeError):
            pass

    # ----------------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        # On (re)open, refresh against the current cue list — segments
        # may have been added/edited/removed since the last invocation.
        self._recompute_matches()
        # Focus the Find input and pre-select its text so a re-open with
        # a stale search term lets the user just start typing.
        self._find_field.lineedit.setFocus(Qt.OtherFocusReason)
        self._find_field.lineedit.selectAll()


# ---------------------------------------------------------------------------
# Singleton accessor + trigger
# ---------------------------------------------------------------------------


def _get_or_create_dialog(host) -> FindReplaceDialog:
    """Cache one dialog instance per host window.

    Re-using the instance keeps the user's last Find text + toggle
    state between opens — the expected convention for find/replace
    affordances across mature editors.
    """
    dialog = getattr(host, '_find_replace_dialog', None)
    if dialog is None:
        dialog = FindReplaceDialog(host)
        host._find_replace_dialog = dialog
    return dialog


def show_find_replace_dialog(host) -> None:
    """Open (or raise) the Find & Replace dialog for ``host``.

    Non-modal: the user can keep clicking around the cue list while the
    dialog is open. ``raise_()`` + ``activateWindow()`` brings it back
    to the front if it was already visible but obscured.
    """
    dialog = _get_or_create_dialog(host)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


@shortcut(
    'show_find_replace_dialog',
    'Open the Find & Replace dialog',
    ['Ctrl+H', 'Ctrl+F'],
)
def _shortcut_show_find_replace_dialog(host):
    show_find_replace_dialog(host)
