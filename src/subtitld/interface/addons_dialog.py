"""Add-ons management UI — single unified list with filter pills.

v0.4 swaps the QListWidget+selection-driven action row for a
QScrollArea-of-cards. Each card hosts its own per-row buttons (Install /
Update / Disable-Enable / Reveal / Remove) so the user no longer has to
select-then-act, and the panel can word-wrap to whatever width the host
gives it without ever needing a horizontal scrollbar.

Embed via `AddonsPanel` — a `QWidget` placed inside the Global options
left-panel tab in `left_panel_global.py`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import addons
from subtitld.modules.addons import installer, languages as _languages, registry
from subtitld.modules.addons.provider import (
    TASK_ASR_TRANSCRIBE,
    TASK_TRANSLATE,
    TASK_TTS_SYNTHESIZE,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worker threads
# ---------------------------------------------------------------------------
class _CatalogFetchThread(QThread):
    """Background fetch so the UI stays responsive on slow networks."""

    finished_with = Signal(object, object)  # (catalog_dict_or_None, error_or_None)

    def __init__(self, force_refresh: bool = False):
        super().__init__()
        self._force_refresh = force_refresh

    def run(self):
        try:
            catalog = installer.fetch_catalog(force_refresh=self._force_refresh)
            self.finished_with.emit(catalog, None)
        except Exception as exc:  # pragma: no cover - network paths
            # Network/HTTP errors are expected (server down, 404, no
            # connectivity) — surface them as a one-line warning, not a
            # full traceback. The UI already converts the message into a
            # friendly empty-state in `_on_catalog`.
            log.warning('addons_dialog: catalog fetch failed: %s', _short_error(exc))
            self.finished_with.emit(None, _short_error(exc))


class _InstallThread(QThread):
    """Runs `installer.download_and_install` off the GUI thread."""

    progress = Signal(float, str)
    done = Signal(str, str)  # (addon_id, error_msg_or_empty)

    def __init__(self, catalog_entry: dict):
        super().__init__()
        self._entry = catalog_entry

    def run(self):
        try:
            addon_id = installer.download_and_install(
                self._entry,
                on_progress=lambda v, msg: self.progress.emit(float(v), msg),
            )
            self.done.emit(addon_id, '')
        except Exception as exc:  # pragma: no cover - network paths
            log.warning('addons_dialog: install failed: %s', _short_error(exc))
            self.done.emit(self._entry.get('id', ''), _short_error(exc))


def _short_error(exc: BaseException) -> str:
    """Stringify a network/HTTP error in a UI-friendly way. urllib's
    HTTPError carries the URL plus a status; we keep just `<status>:
    <reason>`. Other exceptions fall back to `str(exc)`."""
    from urllib.error import HTTPError, URLError
    if isinstance(exc, HTTPError):
        return f'HTTP {exc.code}: {exc.reason}'
    if isinstance(exc, URLError):
        # URLError wraps the underlying OSError with a `reason` attribute.
        reason = getattr(exc, 'reason', exc)
        return f'{type(reason).__name__}: {reason}' if not isinstance(reason, str) else reason
    return str(exc) or type(exc).__name__


# ---------------------------------------------------------------------------
# Row model
# ---------------------------------------------------------------------------
@dataclass
class _Row:
    """One unified entry shown in the list — either purely installed,
    purely available, both, or built-in. Carries enough info to decide which
    action buttons to expose without re-querying the registry.

    `is_builtin` is set for providers that ship inside the Subtitld binary
    (Edge TTS, AssemblyAI). They appear in the list so users can configure
    defaults / see capabilities, but the Install / Remove buttons are
    suppressed since their lifecycle isn't user-managed.
    """

    addon_id: str
    display_name: str
    summary: str
    license: str
    tasks: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    installed_manifest: dict | None = None
    catalog_entry: dict | None = None
    is_builtin: bool = False

    @property
    def is_installed(self) -> bool:
        # Built-ins count as "installed" for filter purposes — the user can
        # toggle them off / configure them just like installed add-ons.
        return self.installed_manifest is not None or self.is_builtin

    @property
    def is_available(self) -> bool:
        return self.catalog_entry is not None

    @property
    def installed_version(self) -> str | None:
        if self.installed_manifest:
            return self.installed_manifest.get('version')
        return None

    @property
    def latest_version(self) -> str | None:
        if self.catalog_entry:
            return self.catalog_entry.get('latest_version')
        return None

    @property
    def has_update(self) -> bool:
        if not (self.is_installed and self.is_available):
            return False
        installed = self.installed_version or ''
        latest = self.latest_version or ''
        return bool(installed) and bool(latest) and installed != latest


def _normalize_lang_list(raw: list | None) -> list[str]:
    """Run a raw manifest/catalog `languages` array through the BCP-47
    normalizer, dropping garbage. Order is preserved so the first declared
    language stays first when shown in tooltips."""
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for tag in raw:
        n = _languages.normalize(tag)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


# Map task IDs (the on-the-wire protocol strings) to translation keys for
# human-readable labels. Pill labels are kept terse for the filter row;
# this mapping deliberately uses a separate, more verbose set of strings
# (`task_name.*` vs `filter.task.*`) so the per-card meta line can read
# naturally — "Tasks: Text-to-speech" instead of "Tasks: TTS".
_TASK_TRANSLATION_KEYS: dict[str, str] = {
    'tts.synthesize': 'addons_dialog.task_name.tts.synthesize',
    'asr.transcribe': 'addons_dialog.task_name.asr.transcribe',
    'translate.text': 'addons_dialog.task_name.translate.text',
}


def _addon_localized(addon_id: str, field: str, fallback: str) -> str:
    """Look up `addons.<id>.<field>` in the locale file; fall back to the
    manifest-provided string when no translation is registered. Lets locales
    override addon display strings without forcing every manifest to ship
    its own translations."""
    if not addon_id:
        return fallback
    key = f'addons.{addon_id}.{field}'
    translated = _(key)
    if translated and translated != key:
        return translated
    return fallback


def _humanize_task(task_id: str) -> str:
    """Translate a single task id to its display label.

    Unknown task ids (third-party add-ons may declare new ones we don't
    know about) fall back to a prettified form: dots/underscores become
    spaces and the first letter is capitalised. That keeps the meta line
    readable without forcing the host to ship a translation for every
    future task type.
    """
    key = _TASK_TRANSLATION_KEYS.get(task_id)
    if key is not None:
        translated = _(key)
        # `i18n.t` returns the key itself when no translation exists.
        # In that (unexpected) case, prefer the prettified raw id over an
        # ugly dotted key in the UI.
        if translated and translated != key:
            return translated
    pretty = task_id.replace('.', ' ').replace('_', ' ').strip()
    return pretty[:1].upper() + pretty[1:] if pretty else task_id


def _humanize_tasks(tasks: list[str]) -> str:
    """Comma-join the human-readable label for each task id, dropping
    empties. Order preserved from the manifest."""
    return ', '.join(_humanize_task(t) for t in tasks if t)


def _languages_from_manifest(manifest: dict) -> list[str]:
    """Resolve an installed manifest's languages, with fallback to the
    `voices[].language` / `models[].language` arrays.

    Some authored manifests (e.g. early Piper builds) declare per-voice
    languages but forget the top-level `languages` array. Without this
    fallback, any specific language filter would hide them entirely —
    which is the bug the user hit when picking pt-br made Piper vanish.

    Top-level wins if present, so authors who deliberately scope down a
    subset of voice languages still control what's advertised."""
    top = _normalize_lang_list(manifest.get('languages'))
    if top:
        return top
    derived: list[str] = []
    for key in ('voices', 'models'):
        for entry in (manifest.get(key) or []):
            if isinstance(entry, dict):
                tag = entry.get('language')
                if tag:
                    derived.append(tag)
    return _normalize_lang_list(derived)


def _build_rows(installed_manifests: list[dict], catalog: dict | None,
                builtin_providers: list | None = None) -> list[_Row]:
    """Merge installed manifests, catalog entries, and built-in providers
    into a single id-keyed sequence.

    Order: built-ins first (alphabetised), then user-installed add-ons, then
    catalog-only ones. Built-ins are pinned to the top so the user always
    sees `Edge TTS` / `AssemblyAI` as a stable anchor regardless of how many
    add-ons they install.
    """
    by_id: dict[str, _Row] = {}

    # ---- Built-ins (synthesized from the running provider instances) ----
    for provider in builtin_providers or []:
        try:
            if not getattr(provider, 'is_builtin', False):
                continue
            pid = provider.id
        except Exception:
            continue
        by_id[pid] = _Row(
            addon_id=pid,
            display_name=getattr(provider, 'display_name', pid),
            # Built-ins don't carry a `summary` field; leave blank so the
            # card is compact. Tooltips on languages can fill the gap.
            summary='',
            license='built-in',
            tasks=list(getattr(provider, 'tasks', []) or []),
            languages=_normalize_lang_list(getattr(provider, 'languages', []) or []),
            is_builtin=True,
        )

    for manifest in installed_manifests:
        addon_id = manifest.get('id') or ''
        if not addon_id or addon_id in by_id:
            # Don't let an installed add-on with the same id as a built-in
            # silently shadow the built-in row. (Wouldn't normally happen,
            # but the registry permits replace-by-id, so be defensive.)
            continue
        by_id[addon_id] = _Row(
            addon_id=addon_id,
            display_name=manifest.get('display_name') or addon_id,
            summary=manifest.get('summary', ''),
            license=manifest.get('license', '?'),
            tasks=list(manifest.get('tasks') or []),
            languages=_languages_from_manifest(manifest),
            installed_manifest=manifest,
        )

    if catalog:
        for entry in catalog.get('addons') or []:
            addon_id = entry.get('id') or ''
            if not addon_id:
                continue
            existing = by_id.get(addon_id)
            if existing is None:
                by_id[addon_id] = _Row(
                    addon_id=addon_id,
                    display_name=entry.get('display_name') or addon_id,
                    summary=entry.get('summary', ''),
                    license=entry.get('license', '?'),
                    tasks=list(entry.get('tasks') or []),
                    languages=_normalize_lang_list(entry.get('languages')),
                    catalog_entry=entry,
                )
            else:
                existing.catalog_entry = entry
                # Catalog summary tends to be richer than manifest's; prefer it.
                if entry.get('summary'):
                    existing.summary = entry['summary']
                # Catalog languages override manifest ones if the manifest
                # didn't declare any (built-ins always have their own).
                if not existing.languages and not existing.is_builtin:
                    existing.languages = _normalize_lang_list(entry.get('languages'))

    builtin_rows = sorted(
        (r for r in by_id.values() if r.is_builtin),
        key=lambda r: r.display_name.lower(),
    )
    installed_rows = sorted(
        (r for r in by_id.values() if r.is_installed and not r.is_builtin),
        key=lambda r: r.display_name.lower(),
    )
    available_only = sorted(
        (r for r in by_id.values() if not r.is_installed),
        key=lambda r: r.display_name.lower(),
    )
    return builtin_rows + installed_rows + available_only


# ---------------------------------------------------------------------------
# Filter pill — checkable QPushButton with the QSS `filter_pill` class
# ---------------------------------------------------------------------------
def _make_pill(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setProperty('class', 'filter_pill')
    btn.setCheckable(True)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    btn.setFixedHeight(24)
    return btn


# ---------------------------------------------------------------------------
# Card widget — one rendered row, with its own action buttons inline
# ---------------------------------------------------------------------------
class _AddonCard(QFrame):
    """Renders one `_Row` as a self-contained card.

    Buttons emit signals carrying the `_Row` so the parent panel doesn't
    need to maintain a separate "currently-selected" reference. Buttons that
    don't apply to the row's state aren't created at all — keeps the
    layout tight on simple "available" rows.

    The card is rebuilt from scratch on every state change (install,
    enable/disable, etc.) instead of trying to mutate sub-widgets in
    place. Cards are cheap; rebuild keeps the state machine here trivial.
    """

    install_clicked = Signal(object)  # _Row
    update_clicked = Signal(object)
    toggle_clicked = Signal(object)
    reveal_clicked = Signal(object)
    remove_clicked = Signal(object)

    def __init__(
        self,
        row: _Row,
        install_target_id: str | None = None,
        parent=None,
    ):
        """`install_target_id` is whichever add-on the panel is currently
        installing (or `None` when nothing is in flight). The card uses it
        for two things:
          - if it's non-None, our own Install/Update button gets disabled
            (only one install at a time);
          - if it equals our row's id, we surface our inline `progress_bar`
            so the user sees download progress directly on the card they
            clicked rather than a faraway bar at the bottom of the panel.
        """
        super().__init__(parent)
        self.row = row
        self.setProperty('class', 'addon_card')
        self.setAttribute(Qt.WA_StyledBackground)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        any_install_in_progress = install_target_id is not None
        is_install_target = install_target_id == row.addon_id

        outer = QVBoxLayout()
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)
        self.setLayout(outer)

        # --- Top row: title (flex) + version + badge --------------------
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        title = QLabel(_addon_localized(row.addon_id, 'display_name', row.display_name))
        title.setProperty('class', 'addon_card_title')
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        top_row.addWidget(title, 1)

        # Built-ins don't carry a meaningful version — they ship as part of
        # the Subtitld binary, so showing "v1.0" or whatever the provider
        # happens to expose just adds noise next to the "built-in" badge.
        # Suppress the version chip entirely for them.
        if row.is_builtin:
            version_str = ''
        else:
            version_str = (
                f'v{row.installed_version}' if row.is_installed
                else (f'v{row.latest_version}' if row.latest_version else '')
            )
        if version_str:
            version_label = QLabel(version_str)
            version_label.setProperty('class', 'addon_card_version')
            top_row.addWidget(version_label, 0, Qt.AlignTop)

        badge_text = self._badge_text(row)
        if badge_text:
            badge = QLabel(badge_text)
            # Updated entries get a different colored badge so users can
            # spot them in a glance even with the "All" filter active.
            badge.setProperty(
                'class',
                'addon_card_badge_update' if row.has_update else 'addon_card_badge',
            )
            badge.setAttribute(Qt.WA_StyledBackground)
            top_row.addWidget(badge, 0, Qt.AlignTop)

        outer.addLayout(top_row)

        # --- Summary -----------------------------------------------------
        summary_text = _addon_localized(row.addon_id, 'summary', row.summary)
        if summary_text:
            summary = QLabel(summary_text)
            summary.setWordWrap(True)
            summary.setProperty('class', 'addon_card_summary')
            outer.addWidget(summary)

        # --- Meta line: license + tasks ---------------------------------
        meta_parts: list[str] = []
        if row.license and row.license not in ('', '?'):
            meta_parts.append(_('addons_dialog.details.license').format(license=row.license))
        if row.tasks:
            meta_parts.append(_('addons_dialog.details.tasks').format(
                tasks=_humanize_tasks(row.tasks)
            ))
        if meta_parts:
            meta = QLabel(' | '.join(meta_parts))
            meta.setWordWrap(True)
            meta.setProperty('class', 'addon_card_meta')
            outer.addWidget(meta)

        # --- Enabled/disabled state hint --------------------------------
        if row.is_installed:
            enabled = registry.is_enabled(row.addon_id)
            state_text = (
                _('addons_dialog.details.enabled') if enabled
                else _('addons_dialog.details.disabled')
            )
            state_label = QLabel(state_text)
            state_label.setProperty('class', 'addon_card_state')
            outer.addWidget(state_label)

        # --- Action button row ------------------------------------------
        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addStretch()

        # Available-only: just Install. Skipped for built-ins, which never
        # have a `catalog_entry` anyway but the explicit guard keeps the
        # intent obvious.
        if not row.is_installed and row.is_available and not row.is_builtin:
            install = QPushButton(_('addons_dialog.actions.install'))
            install.setEnabled(not any_install_in_progress)
            install.clicked.connect(lambda: self.install_clicked.emit(row))
            actions.addWidget(install)

        # Installed-with-update: Update + the standard installed actions
        if row.is_installed and row.has_update and not row.is_builtin:
            update = QPushButton(_('addons_dialog.actions.update'))
            update.setEnabled(not any_install_in_progress)
            update.clicked.connect(lambda: self.update_clicked.emit(row))
            actions.addWidget(update)

        if row.is_installed:
            enabled = registry.is_enabled(row.addon_id)
            toggle = QPushButton(
                _('addons_dialog.actions.disable') if enabled
                else _('addons_dialog.actions.enable')
            )
            toggle.clicked.connect(lambda: self.toggle_clicked.emit(row))
            actions.addWidget(toggle)

            # Reveal-in-folder only makes sense for filesystem-installed
            # add-ons. Built-ins live inside the binary — nothing to open.
            if not row.is_builtin:
                reveal = QPushButton(_('addons_dialog.actions.reveal'))
                reveal.clicked.connect(lambda: self.reveal_clicked.emit(row))
                actions.addWidget(reveal)

                remove = QPushButton(_('addons_dialog.actions.remove'))
                remove.setProperty('class', 'danger')
                remove.clicked.connect(lambda: self.remove_clicked.emit(row))
                actions.addWidget(remove)

        outer.addLayout(actions)

        # --- Inline progress bar ----------------------------------------
        # Only the card whose row is the current install target shows a
        # bar. The panel pushes percentage updates via `set_progress`;
        # `_render_list` rebuilds the cards on completion so the bar
        # disappears naturally rather than needing a "hide" call.
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName('addon_card_progress_bar')
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(1)
        self.progress_bar.setVisible(is_install_target)
        outer.addWidget(self.progress_bar)

    def set_progress(self, percent: int) -> None:
        """Called by the panel during the active install. Idempotent —
        flips visibility on so a card freshly created (no `install_target`
        flag) still surfaces the bar if its install starts after creation."""
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(max(0, min(100, percent)))

    @staticmethod
    def _badge_text(row: _Row) -> str:
        if row.is_builtin:
            return _('addons_dialog.badge.builtin')
        if row.has_update:
            return _('addons_dialog.badge.update')
        if row.is_installed:
            return _('addons_dialog.badge.installed')
        return _('addons_dialog.badge.available')


# ---------------------------------------------------------------------------
# Embeddable panel
# ---------------------------------------------------------------------------
class AddonsPanel(QWidget):
    """Single-list add-ons manager. Filter pills (install state + task type)
    on top, scrollable list of cards in the middle, install-from-file +
    refresh + progress at the bottom."""

    # Install-state filter values
    _STATE_ALL = 'all'
    _STATE_INSTALLED = 'installed'
    _STATE_AVAILABLE = 'available'
    _STATE_UPDATES = 'updates'

    # Task-type filter values (None means "any task")
    _TASK_ANY = None

    # Language filter sentinel: empty string means "any language" (the
    # combobox stores the BCP-47 tag in `userData`).
    _LANG_ANY = ''

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty('class', 'transparent_panel')
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().setSpacing(8)

        # --- State pills row (mutually exclusive) -------------------------
        self._state_filter = self._STATE_ALL
        self._task_filter = self._TASK_ANY
        self._language_filter = self._LANG_ANY

        # The initial setChecked() below fires `toggled`, which triggers
        # _render_list() — but the inner scroll layout doesn't exist yet at
        # that point. Block signals while seeding the default state, then
        # connect the handlers afterwards.
        state_row = QHBoxLayout()
        state_row.setSpacing(4)
        self._state_group = QButtonGroup(self)
        self._state_group.setExclusive(True)
        self._state_pills: dict[str, QPushButton] = {}
        for value in (self._STATE_ALL, self._STATE_INSTALLED, self._STATE_AVAILABLE, self._STATE_UPDATES):
            pill = _make_pill('')  # text set in retranslate()
            pill.blockSignals(True)
            self._state_group.addButton(pill)
            state_row.addWidget(pill)
            self._state_pills[value] = pill
        self._state_pills[self._STATE_ALL].setChecked(True)
        state_row.addStretch()
        self.layout().addLayout(state_row)

        # --- Task pills row (mutually exclusive) --------------------------
        task_row = QHBoxLayout()
        task_row.setSpacing(4)
        self._task_group = QButtonGroup(self)
        self._task_group.setExclusive(True)
        self._task_pills: dict[object, QPushButton] = {}  # value -> pill, key None for "any"
        for value in (self._TASK_ANY, TASK_TTS_SYNTHESIZE, TASK_ASR_TRANSCRIBE, TASK_TRANSLATE):
            pill = _make_pill('')
            pill.blockSignals(True)
            self._task_group.addButton(pill)
            task_row.addWidget(pill)
            self._task_pills[value] = pill
        self._task_pills[self._TASK_ANY].setChecked(True)
        task_row.addStretch()
        self.layout().addLayout(task_row)

        # --- Language filter combobox ------------------------------------
        # Populated from the union of all rows' declared languages, so the
        # dropdown only shows tags the user can actually filter by. The
        # default "Any language" entry uses an empty string as data so the
        # filter logic can early-out cheaply.
        lang_row = QHBoxLayout()
        lang_row.setSpacing(4)
        self._language_label = QLabel('')  # text set in retranslate()
        self._language_combo = QComboBox()
        # Match the surrounding state/task pills so the filter row reads as
        # one visual cluster. The QSS rule for QComboBox[class='filter_pill']
        # mirrors the QPushButton pill (rounded, same colors), with extra
        # right padding to leave room for the dropdown arrow.
        self._language_combo.setProperty('class', 'filter_pill')
        self._language_combo.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        # `userData` is the canonical lowercased tag we match against.
        # Wired up below after we have a stable initial state.
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)
        lang_row.addWidget(self._language_label)
        lang_row.addWidget(self._language_combo, 1)
        lang_row.addStretch()
        self.layout().addLayout(lang_row)

        # --- Scrollable card list + empty-state placeholder --------------
        # The list and the placeholder share the same slot in the layout
        # via a QStackedWidget. `_render_list` flips pages based on
        # whether we have rows to show / the catalog fetch failed.
        self._list_stack = QStackedWidget()

        # Page 0: scroll area containing a vertical column of cards.
        # `setHorizontalScrollBarPolicy(AlwaysOff)` is the explicit fix
        # for the cosmetic horizontal-scrollbar bug — long add-on names
        # now wrap inside the card title label rather than spilling sideways.
        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName('addons_panel_scroll')
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setFrameShape(QFrame.NoFrame)

        self._list_inner = QWidget()
        self._list_inner.setObjectName('addons_panel_scroll_inner')
        self._list_layout = QVBoxLayout(self._list_inner)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch(1)  # pushes cards to the top

        self._scroll_area.setWidget(self._list_inner)
        self._list_stack.addWidget(self._scroll_area)  # page 0

        # Page 1: empty-state label.
        self._empty_label = QLabel('')
        self._empty_label.setObjectName('addons_panel_empty')
        self._empty_label.setProperty('class', 'addons_empty_state')
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._list_stack.addWidget(self._empty_label)  # page 1

        self.layout().addWidget(self._list_stack, 1)

        # Overall fetch state, drives placeholder copy when the list is
        # empty: 'loading' (initial), 'ok' (catalog loaded), 'error' (last
        # fetch failed).
        self._fetch_state = 'loading'
        self._fetch_error_msg = ''

        # Now that the inner layout exists, hook up the pill `toggled`
        # handlers and re-enable signal delivery.
        for value, pill in self._state_pills.items():
            pill.toggled.connect(lambda checked, v=value: self._on_state_toggled(v, checked))
            pill.blockSignals(False)
        for value, pill in self._task_pills.items():
            pill.toggled.connect(lambda checked, v=value: self._on_task_toggled(v, checked))
            pill.blockSignals(False)

        # --- Bottom utility row -----------------------------------------
        # Per-row actions live inside the cards now; this row only carries
        # the panel-wide actions (Install from file…, Refresh).
        actions_row = QHBoxLayout()
        actions_row.setSpacing(6)
        actions_row.addStretch()

        self.install_from_file_button = QPushButton(_('addons_dialog.actions.from_file'))
        self.install_from_file_button.clicked.connect(self._install_from_file)

        self.refresh_button = QPushButton(_('addons_dialog.actions.refresh'))
        self.refresh_button.clicked.connect(lambda: self._refresh_catalog(force=True))

        actions_row.addWidget(self.install_from_file_button)
        actions_row.addWidget(self.refresh_button)
        self.layout().addLayout(actions_row)

        # --- Internal state ------------------------------------------
        # Progress feedback now lives inside the active card — see
        # `_AddonCard.progress_bar`. We track the install target here so
        # progress callbacks can find the right card (it gets rebuilt
        # every render, so we keep the *id* and resolve to a widget on
        # demand).
        self._catalog: dict | None = None
        self._rows: list[_Row] = []
        self._fetch_thread: _CatalogFetchThread | None = None
        self._install_thread: _InstallThread | None = None
        self._install_target_id: str | None = None
        self._cards_by_id: dict[str, _AddonCard] = {}

        self.retranslate()
        self._refresh_local_only()  # immediate render of installed items
        self._refresh_catalog(force=False)

    # -- Filter handlers ---------------------------------------------------
    def _on_state_toggled(self, value: str, checked: bool):
        if not checked:
            return
        self._state_filter = value
        self._render_list()

    def _on_task_toggled(self, value, checked: bool):
        if not checked:
            return
        self._task_filter = value
        self._render_list()

    def _on_language_changed(self, _index: int):
        # Combobox stores normalized BCP-47 tags as userData; '' means "any".
        data = self._language_combo.currentData()
        self._language_filter = data if isinstance(data, str) else self._LANG_ANY
        self._render_list()

    def _repopulate_language_combo(self):
        """Rebuild the language dropdown from the current row set.

        The combobox keeps its currently-selected tag if the new row set
        still includes it; otherwise it falls back to "Any language" so we
        don't strand the user on a filter that hides everything.

        We block signals during the rebuild because clearing + re-adding
        items fires `currentIndexChanged`, which would re-trigger
        `_render_list` mid-render.
        """
        previous = self._language_filter
        self._language_combo.blockSignals(True)
        try:
            self._language_combo.clear()
            self._language_combo.addItem(_('addons_dialog.filter.lang.any'), self._LANG_ANY)
            unique = _languages.collect_unique_tags([r.languages for r in self._rows])
            # Sort by the human-readable display name so the dropdown reads
            # alphabetically as the user sees it ("Arabic", "Chinese",
            # "English (United States)" …) rather than by raw tag, where
            # `de-de` would precede `en` lexicographically.
            for tag in sorted(unique, key=lambda t: _languages.display_name(t).lower()):
                self._language_combo.addItem(_languages.display_name(tag) or tag, tag)
            # Restore previous selection if still present.
            if previous and previous != self._LANG_ANY:
                idx = self._language_combo.findData(previous)
                if idx >= 0:
                    self._language_combo.setCurrentIndex(idx)
                else:
                    self._language_filter = self._LANG_ANY
                    self._language_combo.setCurrentIndex(0)
            else:
                self._language_combo.setCurrentIndex(0)
        finally:
            self._language_combo.blockSignals(False)

    # -- Refresh -----------------------------------------------------------
    def _builtin_providers(self) -> list:
        """Pull built-in providers off the live `AddonManager`. Wrapped in a
        try because tests may run this panel without a manager initialised."""
        try:
            return [
                p for p in addons.get_manager().all_providers()
                if getattr(p, 'is_builtin', False)
            ]
        except Exception:
            log.exception('addons_dialog: failed to enumerate built-in providers')
            return []

    def _rebuild_rows(self):
        """Rebuild `self._rows` from current installed manifests + cached
        catalog + live built-ins, then refresh the language combobox so
        newly-introduced tags are selectable. Caller is responsible for the
        subsequent `_render_list()`."""
        self._rows = _build_rows(
            installer.list_installed(),
            self._catalog,
            self._builtin_providers(),
        )
        self._repopulate_language_combo()

    def _refresh_local_only(self):
        """Render the list using just the installed manifests (no network).
        Called once at construction so the user sees something immediately
        even if the catalog fetch is slow."""
        self._rebuild_rows()
        self._render_list()

    def _refresh_catalog(self, force: bool):
        if self._fetch_thread is not None:
            return
        self._fetch_state = 'loading'
        self._fetch_error_msg = ''
        # Re-render so the empty-state label flips to "Loading…" if we're
        # currently showing the unreachable / no-results placeholder.
        self._render_list()
        thread = _CatalogFetchThread(force_refresh=force)
        thread.finished_with.connect(self._on_catalog)
        thread.finished.connect(lambda: setattr(self, '_fetch_thread', None))
        self._fetch_thread = thread
        thread.start()

    def _on_catalog(self, catalog, error):
        if error or not isinstance(catalog, dict):
            self._fetch_state = 'error'
            self._fetch_error_msg = error or 'unknown error'
            # Still re-render — installed items remain visible. When there
            # are none, the centered empty-state shows the reachability
            # error.
            self._rebuild_rows()
            self._render_list()
            return
        self._fetch_state = 'ok'
        self._fetch_error_msg = ''
        self._catalog = catalog
        self._rebuild_rows()
        self._render_list()

    # -- Render ------------------------------------------------------------
    def _filter_pass(self, row: _Row) -> bool:
        # State filter
        if self._state_filter == self._STATE_INSTALLED and not row.is_installed:
            return False
        if self._state_filter == self._STATE_AVAILABLE and row.is_installed:
            return False
        if self._state_filter == self._STATE_UPDATES and not row.has_update:
            return False
        # Task filter
        if self._task_filter is not None and self._task_filter not in row.tasks:
            return False
        # Language filter — RFC 4647 prefix-of matching; '' means "any" and
        # a row with no declared languages can't satisfy a specific filter.
        if self._language_filter:
            if not _languages.any_match(self._language_filter, row.languages):
                return False
        return True

    def _clear_cards(self):
        """Drop every layout item — widgets AND spacers — and re-add a
        single trailing stretch.

        Walking with `removeWidget` only drops the widget items; the
        trailing stretch from the previous render is a `QSpacerItem`
        (no widget) and survives. Without this, every rebuild appended
        another stretch via `addStretch(1)`, and the cards ended up
        sandwiched between accumulating stretches — visually pushed
        down/centered. `takeAt` removes any item type."""
        # Walk in reverse so removing items doesn't shift the indices we
        # still want to visit.
        for i in reversed(range(self._list_layout.count())):
            item = self._list_layout.takeAt(i)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            # Spacer items have no widget and no explicit destructor —
            # `takeAt` already removed them from the layout, that's enough.
        self._list_layout.addStretch(1)

    def _render_list(self):
        self._clear_cards()
        # Drop stale references — the cards we kept here were just
        # deleteLater()'d. Re-populated below.
        self._cards_by_id = {}

        rendered = 0
        # Insert above the trailing stretch (which is at index count()-1).
        for row in self._rows:
            if not self._filter_pass(row):
                continue
            card = _AddonCard(row, install_target_id=self._install_target_id)
            card.install_clicked.connect(self._install_row)
            card.update_clicked.connect(self._install_row)  # same code path
            card.toggle_clicked.connect(self._toggle_row)
            card.reveal_clicked.connect(self._reveal_row)
            card.remove_clicked.connect(self._remove_row)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)
            self._cards_by_id[row.addon_id] = card
            rendered += 1

        # Page 0 = list, Page 1 = empty-state placeholder. Pick the
        # message based on what's actually going on (loading / fetch
        # error / filtered-out / fully empty).
        if rendered:
            self._list_stack.setCurrentIndex(0)
        else:
            self._empty_label.setText(self._empty_state_text())
            self._list_stack.setCurrentIndex(1)

    def _empty_state_text(self) -> str:
        """Pick the right placeholder copy. Loading state takes priority,
        then fetch error, then "no rows under this filter" vs "nothing
        installed and no catalog entries"."""
        any_rows_total = bool(self._rows)
        any_filter_active = (
            self._state_filter != self._STATE_ALL
            or self._task_filter is not None
        )
        if self._fetch_state == 'loading' and not any_rows_total:
            return _('addons_dialog.empty.loading')
        if self._fetch_state == 'error' and not any_rows_total:
            return _('addons_dialog.empty.unreachable').format(
                error=self._fetch_error_msg or '—'
            )
        if any_rows_total and any_filter_active:
            return _('addons_dialog.empty.no_match')
        if any_rows_total:
            # Shouldn't really happen — rendered==0 with rows and no
            # filter means every row failed to render. Be safe.
            return _('addons_dialog.empty.no_match')
        # No rows at all and the fetch succeeded → catalog is genuinely
        # empty.
        return _('addons_dialog.empty.catalog_empty')

    # -- Per-card actions --------------------------------------------------
    def _install_row(self, row: _Row):
        if not row or not row.catalog_entry or self._install_thread is not None:
            return
        # Mark this row as the install target BEFORE re-rendering so the
        # newly-built card has its progress bar visible from frame zero.
        self._install_target_id = row.addon_id
        thread = _InstallThread(row.catalog_entry)
        thread.progress.connect(self._on_install_progress)
        thread.done.connect(self._on_install_done)
        thread.finished.connect(lambda: setattr(self, '_install_thread', None))
        self._install_thread = thread
        thread.start()
        # Re-render so all cards' Install/Update buttons disable while a
        # background install is in flight (prevents queuing two at once)
        # AND the target card surfaces its inline progress bar.
        self._render_list()

    def _on_install_progress(self, value: float, message: str):
        # `message` is intentionally unused — the inline progress bar is
        # the only visual feedback during installs now. Find the card
        # by the target id; if filters hid it (user clicked "Installed"
        # mid-install) the progress just goes nowhere — that's fine.
        if not self._install_target_id:
            return
        card = self._cards_by_id.get(self._install_target_id)
        if card is not None:
            card.set_progress(int(value * 100))

    def _on_install_done(self, addon_id: str, error: str):
        # Clear target before re-render so the rebuilt card shows no bar.
        self._install_target_id = None
        if error:
            err = utils.SimpleDialog(self, title=_('addons_dialog.install_error'))
            err.content.layout().addWidget(QLabel(
                _('addons_dialog.status.install_failed').format(addon=addon_id, error=error)
            ))
            err.reject_button.setVisible(False)
            err.exec()
            self._render_list()
            return
        try:
            addons.get_manager().discover()
        except Exception:
            log.exception('addons_dialog: discover after install failed')
        # Re-merge installed + cached catalog and re-render. The newly-
        # installed row swaps badge from "available" to "installed".
        self._rebuild_rows()
        self._render_list()

    def _toggle_row(self, row: _Row):
        if not row or not row.is_installed:
            return
        registry.set_enabled(row.addon_id, not registry.is_enabled(row.addon_id))
        # Card rebuild reflects the new toggle button label + state hint.
        self._render_list()

    def _remove_row(self, row: _Row):
        if not row or not row.is_installed:
            return
        confirm = utils.SimpleDialog(self, title=_('addons_dialog.confirm_remove'))
        confirm.content.layout().addWidget(QLabel(
            _('addons_dialog.confirm_remove_text').format(addon=row.addon_id)
        ))
        confirm.exec()
        if confirm.result() != 1:
            return
        # Take the provider down before nuking its files — otherwise a
        # running subprocess holds an fd onto the binary.
        try:
            addons.get_manager().unregister(row.addon_id)
        except Exception:
            log.exception('addons_dialog: unregister %s failed', row.addon_id)
        installer.uninstall(row.addon_id)
        self._rebuild_rows()
        self._render_list()

    def _reveal_row(self, row: _Row):
        if not row or not row.is_installed or not row.installed_manifest:
            return
        path = row.installed_manifest.get('_install_dir')
        if path and os.path.isdir(path):
            try:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            except Exception:
                log.exception('addons_dialog: reveal %s failed', path)

    def _install_from_file(self):
        zip_path, _filt = QFileDialog.getOpenFileName(
            self,
            _('addons_dialog.from_file_caption'),
            os.path.expanduser('~'),
            'ZIP archives (*.zip)',
        )
        if not zip_path:
            return
        try:
            addon_id = installer.install_from_zip(Path(zip_path))
            addons.get_manager().discover()
            confirm = utils.SimpleDialog(self, title=_('addons_dialog.installed_title'))
            confirm.content.layout().addWidget(QLabel(
                _('addons_dialog.installed_text').format(addon=addon_id)
            ))
            confirm.reject_button.setVisible(False)
            confirm.exec()
        except Exception as exc:
            err = utils.SimpleDialog(self, title=_('addons_dialog.install_error'))
            err.content.layout().addWidget(QLabel(str(exc)))
            err.reject_button.setVisible(False)
            err.exec()
        self._rebuild_rows()
        self._render_list()

    # -- Translate ---------------------------------------------------------
    def retranslate(self):
        self._state_pills[self._STATE_ALL].setText(_('addons_dialog.filter.state.all'))
        self._state_pills[self._STATE_INSTALLED].setText(_('addons_dialog.filter.state.installed'))
        self._state_pills[self._STATE_AVAILABLE].setText(_('addons_dialog.filter.state.available'))
        self._state_pills[self._STATE_UPDATES].setText(_('addons_dialog.filter.state.updates'))

        self._task_pills[self._TASK_ANY].setText(_('addons_dialog.filter.task.any'))
        self._task_pills[TASK_TTS_SYNTHESIZE].setText(_('addons_dialog.filter.task.tts'))
        self._task_pills[TASK_ASR_TRANSCRIBE].setText(_('addons_dialog.filter.task.asr'))
        self._task_pills[TASK_TRANSLATE].setText(_('addons_dialog.filter.task.translate'))

        # Language filter — only the "Any" entry needs translation; the rest
        # of the rows are raw BCP-47 tags. We rebuild the combobox so the
        # entry text picks up the current locale.
        self._language_label.setText(_('addons_dialog.filter.lang.label'))
        self._repopulate_language_combo()

        self.install_from_file_button.setText(_('addons_dialog.actions.from_file'))
        self.refresh_button.setText(_('addons_dialog.actions.refresh'))

        # Card buttons re-translate themselves on the next render.
        self._render_list()

    # -- Teardown ----------------------------------------------------------
    def shutdown(self):
        """Drain in-flight worker threads. Called from the host on app
        teardown so we don't leave a dangling QThread behind."""
        for attr in ('_fetch_thread', '_install_thread'):
            thread = getattr(self, attr, None)
            if thread is None:
                continue
            try:
                if thread.isRunning():
                    thread.wait(2000)
                    if thread.isRunning():
                        thread.terminate()
                        thread.wait(1000)
            except Exception:
                log.exception('addons_dialog: thread teardown for %s failed', attr)
