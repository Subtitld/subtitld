"""Live co-editing, in the subtitld.cc panel (interface/left_panel_subtitldcc.py).

Not shared yet: share the open project (as yourself or a group you publish as), or open one shared with you.
Shared: whether it is connected (changes made offline wait and go when it is back), who else is in it, who it
is shared with (the owner adds and removes people), and leaving it or, for the owner, stopping sharing it.
Either way the project stays in the editor as it is.

Questions are asked in place; calls to subtitld.cc run in the background (modules/collab_service.py).
"""

import html
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from subtitld.interface.translation import _
from subtitld.modules import collab_service, collab_state, session
from subtitld.modules import subtitldcc_service as service


def _text(object_name='subtitldcc_text'):
    label = QLabel()
    label.setObjectName(object_name)
    label.setWordWrap(True)
    return label


def _button(primary=False, danger=False, small=True):
    button = QPushButton()
    if primary or danger:
        button.setProperty('class', 'danger' if danger else 'primary')
    if small:
        button.setObjectName('subtitldcc_small_button')
    button.setCursor(Qt.PointingHandCursor)
    return button


def _row(*widgets, stretch=True):
    row = QWidget()
    row.setProperty('class', 'transparent_panel')
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    for widget in widgets:
        layout.addWidget(widget)
    if stretch:
        layout.addStretch()
    return row


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()


def default_title():
    """The project's file name, else its video's, without the extension."""
    for path in (session.SUBTITLE.get('filepath'), (session.VIDEO or {}).get('filepath')):
        if path:
            return os.path.splitext(os.path.basename(path))[0]
    return ''


class LiveCard(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setObjectName('subtitldcc_card')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        self._project = None      # GET /collab/projects/{doc_id} for the open shared project
        self._projects = None     # the shared projects list, once asked for
        self._busy = False
        self._pending_confirm = None

        self.title = _text('subtitldcc_card_title')
        layout.addWidget(self.title)
        self.status = _text()
        layout.addWidget(self.status)

        # Not shared: share it.
        self.share_box = QWidget()
        self.share_box.setProperty('class', 'transparent_panel')
        share = QVBoxLayout(self.share_box)
        share.setContentsMargins(0, 0, 0, 0)
        share.setSpacing(6)
        self.title_field = QLineEdit()
        self.title_field.textChanged.connect(lambda text: self.share_button.setEnabled(not self._busy and bool(text.strip())))
        self.owner_field = QComboBox()
        self.share_button = _button(primary=True)
        self.share_button.clicked.connect(self.share)
        share.addWidget(self.title_field)
        share.addWidget(_row(self.owner_field, self.share_button))
        self.list_button = _button()
        self.list_button.clicked.connect(self.load_projects)
        share.addWidget(_row(self.list_button))
        self.projects_box = QVBoxLayout()
        share.addLayout(self.projects_box)
        layout.addWidget(self.share_box)

        # Shared: who is here, who it's shared with.
        self.shared_box = QWidget()
        self.shared_box.setProperty('class', 'transparent_panel')
        shared = QVBoxLayout(self.shared_box)
        shared.setContentsMargins(0, 0, 0, 0)
        shared.setSpacing(6)
        self.here = _text()
        shared.addWidget(self.here)
        self.publishes_to = _text()
        shared.addWidget(self.publishes_to)
        self.conflicts = _text()
        shared.addWidget(self.conflicts)
        self.conflicts_box = QVBoxLayout()
        shared.addLayout(self.conflicts_box)
        self.members_title = _text('subtitldcc_kicker')
        shared.addWidget(self.members_title)
        self.members_box = QVBoxLayout()
        shared.addLayout(self.members_box)
        self.member_field = QLineEdit()
        self.add_button = _button()
        self.add_button.clicked.connect(self.add_member)
        self.member_field.returnPressed.connect(self.add_member)
        self.add_row = _row(self.member_field, self.add_button, stretch=False)
        shared.addWidget(self.add_row)
        self.sign_in_button = _button(primary=True)
        self.sign_in_button.clicked.connect(self.sign_in_again)
        self.stop_button = _button(danger=True)
        self.stop_button.clicked.connect(self.ask_stop)
        shared.addWidget(_row(self.sign_in_button, self.stop_button))
        layout.addWidget(self.shared_box)

        # A question asked in place.
        self.confirm = QWidget()
        self.confirm.setObjectName('subtitldcc_problem')
        self.confirm.setProperty('severity', 'warn')
        self.confirm.setAttribute(Qt.WA_StyledBackground, True)
        confirm = QVBoxLayout(self.confirm)
        confirm.setContentsMargins(10, 8, 10, 8)
        self.confirm_text = _text()
        confirm.addWidget(self.confirm_text)
        self.confirm_no = _button()
        self.confirm_no.clicked.connect(self.confirm.hide)
        self.confirm_yes = _button(primary=True)
        self.confirm_yes.clicked.connect(self._confirmed)
        confirm.addWidget(_row(self.confirm_no, self.confirm_yes))
        self.confirm.hide()
        layout.addWidget(self.confirm)

        # Collaborators' changes: the editor follows (at most every 150 ms while they stream in).
        self._editor_refresh = QTimer(self)
        self._editor_refresh.setSingleShot(True)
        self._editor_refresh.setInterval(150)
        self._editor_refresh.timeout.connect(self._refresh_editor)
        collab_service.listeners.append(self._news)
        self.retranslate()

    # --- What it shows -----------------------------------------------------------------------------------

    def _refresh_editor(self):
        from subtitld.interface import left_panel, left_panel_subtitleslist

        window = self.panel.window_
        if hasattr(window, 'timeline_widget'):
            window.timeline_widget.update_size()
            window.timeline_widget.update()
        if hasattr(window, 'left_panel_stackedwidgets'):
            left_panel.update(window)
        if hasattr(window, 'left_panel_subtitleslist_textedit'):
            left_panel_subtitleslist.refresh_lock(window)

    def _news(self, kind):
        window = self.panel.window_
        if kind in ('peers', 'locks') and hasattr(window, 'timeline_widget'):
            window.timeline_widget.update()
        if kind == 'locks' and hasattr(window, 'left_panel_subtitleslist_textedit'):
            from subtitld.interface import left_panel_subtitleslist
            left_panel_subtitleslist.refresh_lock(window)
        if kind == 'document' and not self._editor_refresh.isActive():
            self._editor_refresh.start()
        if kind in ('shared', 'failed'):
            self._project = None
            self._projects = None if kind == 'shared' else self._projects
        if kind in ('state', 'peers', 'shared', 'failed', 'conflicts') and not self.isHidden():
            self.refresh(load=kind == 'shared')

    def refresh(self, load=True):
        connected = service.is_connected()
        self.setVisible(connected)
        if not connected:
            return
        shared = collab_state.shared()
        self.share_box.setVisible(not shared)
        self.shared_box.setVisible(shared)
        if shared:
            self._render_shared()
            if load and self._project is None and not self._busy:
                self._busy = True
                collab_service.project(self._project_loaded, self._call_failed)
        else:
            self._render_share()

    def _render_share(self):
        failure = collab_service.failure()
        self.status.setText(_('collab.not_shared_any_more') if failure == 'not_found' else _('collab.share_text'))
        if not self.title_field.text():
            self.title_field.setText(default_title())
        owners = (service.account() or {}).get('publish_as') or [(service.account() or {}).get('handle') or '']
        current = [self.owner_field.itemData(i) for i in range(self.owner_field.count())]
        if current != owners:
            self.owner_field.clear()
            for handle in owners:
                self.owner_field.addItem(f'@{handle}', handle)
        self.owner_field.setVisible(len(owners) > 1)
        self.share_button.setEnabled(not self._busy and bool(self.title_field.text().strip()))
        self.list_button.setVisible(self._projects is None)

    def _render_shared(self):
        state = collab_service.state()
        waiting = len(collab_state.STATE.get('pending') or [])
        failure = collab_service.failure()
        if failure == 'missing_scope':
            self.status.setText(_('collab.sign_in_again_text'))
        elif state == 'live':
            self.status.setText(_('collab.live') if not waiting else _('collab.live_sending').format(count=waiting))
        elif state == 'joining':
            self.status.setText(_('collab.joining'))
        else:
            self.status.setText(_('collab.offline').format(count=waiting))
        self.sign_in_button.setVisible(failure == 'missing_scope')

        connection = collab_service.connection()
        engine = connection.engine if connection is not None else None
        others = sorted({f"@{(p.get('user') or {}).get('handle', '')}" for p in (engine.peers.values() if engine else [])})
        self.here.setText(_('collab.here').format(people=', '.join(others)) if others else _('collab.here_nobody'))
        found = collab_service.conflicts()
        self.conflicts.setVisible(bool(found))
        self.conflicts.setText(_('collab.conflicts').format(count=len(found)))
        _clear(self.conflicts_box)
        for conflict in found[:10]:
            self.conflicts_box.addWidget(self._conflict_row(conflict))

        project = self._project or {}
        manage = bool((project.get('permissions') or {}).get('can_manage'))
        _clear(self.members_box)
        if project:
            owner = _text()
            owner.setText(_('collab.owner').format(handle=(project.get('owner') or {}).get('handle', '')))
            self.members_box.addWidget(owner)
            for member in project.get('members') or []:
                label = _text()
                label.setText(f"@{member['handle']}")
                widgets = [label]
                if manage:
                    remove = _button()
                    remove.setText(_('collab.remove'))
                    remove.clicked.connect(lambda _c=False, handle=member['handle']: self.remove_member(handle))
                    widgets.append(remove)
                self.members_box.addWidget(_row(*widgets))
        self.members_title.setVisible(bool(project))
        subtitle = project.get('subtitle')
        self.publishes_to.setVisible(bool(subtitle))
        if subtitle:
            self.publishes_to.setText(_('collab.publishes_to').format(
                title=html.escape(subtitle.get('title') or ''), version=subtitle.get('version') or 0))
        self.add_row.setVisible(manage)
        self.stop_button.setText(_('collab.stop_sharing') if manage else _('collab.leave'))
        self.stop_button.setVisible(bool(project))

    def _conflict_row(self, conflict):
        box = QWidget()
        box.setObjectName('subtitldcc_problem')
        box.setProperty('severity', 'warn')
        box.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 6, 8, 6)
        text = _text()
        buttons = []
        if conflict.get('cue_id'):
            text.setText(_('collab.conflict_text').format(author=conflict.get('author') or '')
                         + f"<br><i>{html.escape(str(conflict.get('theirs') or ''))}</i>")
            mine, theirs = _button(), _button()
            mine.setText(_('collab.conflict_mine'))
            theirs.setText(_('collab.conflict_theirs'))
            mine.clicked.connect(lambda: collab_service.resolve(conflict, 'mine'))
            theirs.clicked.connect(lambda: collab_service.resolve(conflict, 'theirs'))
            buttons = [mine, theirs]
        else:
            text.setText(_('collab.refused_text'))
            ok = _button()
            ok.setText(_('collab.dismiss'))
            ok.clicked.connect(lambda: collab_service.resolve(conflict, 'mine'))
            buttons = [ok]
        layout.addWidget(text)
        layout.addWidget(_row(*buttons))
        return box

    # --- Not shared --------------------------------------------------------------------------------------

    def share(self):
        title = self.title_field.text().strip()
        if not title or self._busy:
            return
        self._busy = True
        self.share_button.setEnabled(False)
        owner = self.owner_field.currentData() or ''
        collab_service.share(title, owner, self._shared, self._call_failed)

    def _shared(self, project):
        self._busy = False
        self._project = project
        self.panel.show_message(_('collab.shared_now'))
        self.refresh(load=False)

    def load_projects(self):
        self.list_button.setEnabled(False)
        collab_service.projects(self._projects_loaded, self._call_failed)

    def _projects_loaded(self, page):
        self.list_button.setEnabled(True)
        self._projects = page.get('results') or []
        _clear(self.projects_box)
        if not self._projects:
            empty = _text()
            empty.setText(_('collab.no_projects'))
            self.projects_box.addWidget(empty)
        for project in self._projects:
            label = _text()
            label.setText(f"<b>{html.escape(project['title'])}</b><br>@{html.escape((project.get('owner') or {}).get('handle', ''))}")
            open_button = _button()
            open_button.setText(_('collab.open'))
            open_button.clicked.connect(lambda _c=False, p=project: self.ask_join(p))
            self.projects_box.addWidget(_row(label, open_button))
        self.refresh(load=False)

    def ask_join(self, project):
        key = 'collab.join_unsaved' if session.UNSAVED else 'collab.join_text'
        self._ask(_(key).format(title=project['title']), _('collab.open'), lambda: self._join(project))

    def _join(self, project):
        collab_service.join(self.panel.window_, project)
        self._project = None
        self.refresh()

    # --- Shared --------------------------------------------------------------------------------------------

    def _project_loaded(self, project):
        self._busy = False
        self._project = project
        if collab_service.follow_subtitle(project):
            self.panel.origin_changed()
        self.refresh(load=False)

    def published(self, share_id, version):
        """Published from the shared project (the subtitld.cc panel's Publish): tell subtitld.cc, which
        credits everyone who co-edited it."""
        def reported(project):
            self._project_loaded(project)
            others = [f'@{h}' for h in project.get('coeditors') or [] if h != (service.account() or {}).get('handle')]
            if others:
                self.panel.show_message(_('collab.published_with').format(people=', '.join(others)))

        collab_service.report_published(share_id, version, reported, self._call_failed)

    def add_member(self):
        handle = self.member_field.text().strip()
        if not handle:
            return
        self.add_button.setEnabled(False)

        def added(project):
            self.add_button.setEnabled(True)
            self.member_field.clear()
            self._project_loaded(project)

        collab_service.add_member(handle, added, self._call_failed)

    def remove_member(self, handle):
        def removed(_result):
            self._project = None
            self.refresh()

        self._ask(_('collab.remove_text').format(handle=handle), _('collab.remove'),
                  lambda: collab_service.remove_member(handle, removed, self._call_failed), danger=True)

    def ask_stop(self):
        manage = bool(((self._project or {}).get('permissions') or {}).get('can_manage'))
        key = 'collab.stop_sharing_text' if manage else 'collab.leave_text'
        label = _('collab.stop_sharing') if manage else _('collab.leave')
        self._ask(_(key), label, lambda: collab_service.stop_sharing(manage, self._stopped, self._call_failed),
                  danger=True)

    def _stopped(self, _result):
        self._project = None
        self.panel.show_message(_('collab.not_shared'))
        self.refresh()

    def sign_in_again(self):
        """The token predates co-editing (no "collab" scope): a new sign-in replaces it."""
        self.panel.sign_in(device=False)

    def signed_in(self):
        """A new sign-in: the open shared project tries again with it."""
        if collab_state.shared():
            collab_service.connect()
        self.refresh()

    # --- Questions and errors -------------------------------------------------------------------------------

    def _ask(self, text, action_label, action, danger=False):
        self.confirm_text.setText(text)
        self.confirm_yes.setText(action_label)
        self.confirm_yes.setProperty('class', 'danger' if danger else 'primary')
        self.confirm_yes.style().unpolish(self.confirm_yes)
        self.confirm_yes.style().polish(self.confirm_yes)
        self._pending_confirm = action
        self.confirm.show()

    def _confirmed(self):
        self.confirm.hide()
        action, self._pending_confirm = self._pending_confirm, None
        if action is not None:
            action()

    def _call_failed(self, error):
        self._busy = False
        self.add_button.setEnabled(True)
        self.list_button.setEnabled(True)
        self.panel._failed(error)
        self.refresh(load=False)

    def retranslate(self):
        self.title.setText(_('collab.title'))
        self.title_field.setPlaceholderText(_('collab.title_placeholder'))
        self.share_button.setText(_('collab.share'))
        self.share_button.setToolTip(_('collab.share_tip'))
        self.list_button.setText(_('collab.shared_with_you'))
        self.members_title.setText(_('collab.members'))
        self.member_field.setPlaceholderText(_('collab.member_placeholder'))
        self.add_button.setText(_('collab.add'))
        self.sign_in_button.setText(_('collab.sign_in_again'))
        self.confirm_no.setText(_('subtitldcc.cancel'))
        self.refresh(load=False)
