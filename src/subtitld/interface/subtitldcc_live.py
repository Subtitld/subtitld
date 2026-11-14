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
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

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


def _size(count):
    for unit, factor in (('GB', 1024 ** 3), ('MB', 1024 ** 2), ('KB', 1024)):
        if count >= factor:
            return f'{count / factor:.1f} {unit}'
    return f'{count} B'


def use_video(window, path):
    """Open `path` as the project's video here, in place of the one open (a received original)."""
    from subtitld.interface import left_panel, left_panel_subtitldcc, top_bar
    from subtitld.modules import file_io

    window.preview_panel_player.loadfile(path)
    session.VIDEO = file_io.process_video_file(path)
    if hasattr(window, 'timeline_widget'):
        window.timeline_widget.update_size()
        window.timeline_widget.load_waveform()
        window.timeline_widget.update()
    session.set_unsaved(True)  # the project points at this video now
    left_panel.update(window)
    top_bar.update(window)
    left_panel_subtitldcc.video_opened(window)
    collab_service.list_media()


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
        self._link = None         # a project a subtitld:// link named, waiting for a sign-in
        self._busy = False
        self._pending_confirm = None
        self._shown_here = None   # who was here when the card last showed it (presence alone changes nothing)

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
        # Its page on subtitld.cc, where collaborators open it in Subtitld (subtitldcc_open.py).
        self.copy_link_button = _button()
        self.copy_link_button.clicked.connect(self.copy_link)
        shared.addWidget(_row(self.copy_link_button))
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
        # Media: the project's files, sent between collaborators (modules/collab_transfer.py).
        self.media_title = _text('subtitldcc_kicker')
        shared.addWidget(self.media_title)
        self.share_media = QCheckBox()
        self.share_media.toggled.connect(collab_service.set_sharing_media)
        shared.addWidget(self.share_media)
        self.media_note = _text()
        shared.addWidget(self.media_note)
        self.media_box = QVBoxLayout()
        shared.addLayout(self.media_box)
        self.dubs = _text()
        shared.addWidget(self.dubs)
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
        from subtitld.interface import left_panel, left_panel_dubbing, left_panel_subtitleslist

        left_panel_dubbing.show_project_dubs()  # the project's dubs, as they arrive, seen and heard here too

        window = self.panel.window_
        start_screen = getattr(window, 'start_screen', None)
        if start_screen is not None and window.central_widget.layout().currentWidget() is start_screen:
            return  # a project opened from a link, waiting for its video (subtitldcc_open.py)
        if hasattr(window, 'timeline_widget'):
            window.timeline_widget.update_size()
            window.timeline_widget.update()
        if hasattr(window, 'left_panel_stackedwidgets'):
            left_panel.update(window)
        if hasattr(window, 'left_panel_subtitleslist_textedit'):
            left_panel_subtitleslist.refresh_lock(window)
        player = getattr(window, 'preview_panel_player', None)
        if player is not None and hasattr(player, '_audio_device'):
            # Dubs that changed, or whose files just came, play as they are now.
            player._audio_device.sync_subtitle_dubs(session.SUBTITLE.get('segments') or [])
        if not self.isHidden():
            self.refresh(load=False)

    def _news(self, kind):
        window = self.panel.window_
        if kind in ('peers', 'locks') and hasattr(window, 'timeline_widget'):
            window.timeline_widget.update()
        if kind == 'locks' and hasattr(window, 'left_panel_subtitleslist_textedit'):
            from subtitld.interface import left_panel_subtitleslist
            left_panel_subtitleslist.refresh_lock(window)
        if kind in ('document', 'dubs') and not self._editor_refresh.isActive():
            self._editor_refresh.start()
        if kind == 'stems':
            self._take_stems()
        if kind in ('shared', 'failed'):
            self._project = None
            self._projects = None if kind == 'shared' else self._projects
        if kind == 'peers':
            # Presence comes several times a second while someone plays the video; the card shows who is here.
            here = self._here()
            if here == self._shown_here:
                return
            self._shown_here = here
        if kind in ('state', 'peers', 'shared', 'failed', 'conflicts', 'media') and not self.isHidden():
            self.refresh(load=kind == 'shared')

    def _take_stems(self):
        """Separated audio that came from a collaborator: once the original, the vocals and the background are
        all here, the player takes them (the music/voice slider comes on), as when the video is opened again."""
        from subtitld.modules import collab_media

        window = self.panel.window_
        video = (session.VIDEO or {}).get('filepath')
        extractor = getattr(window, 'music_voice_separation_thread', None)
        if not video or extractor is None:
            return
        paths = [collab_media.destination('stem', f'{role}.flac') for role in collab_media.STEM_ROLES]
        if all(path and os.path.isfile(path) for path in paths):
            extractor.filename = video
            extractor.start()

    def _here(self):
        connection = collab_service.connection()
        engine = connection.engine if connection is not None else None
        return sorted((p.get('user') or {}).get('handle', '') for p in (engine.peers.values() if engine else []))

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
        self.copy_link_button.setVisible(bool(project.get('url')))
        self.add_row.setVisible(manage)
        self.stop_button.setText(_('collab.stop_sharing') if manage else _('collab.leave'))
        self.stop_button.setVisible(bool(project))
        self._render_media()

    # --- Media -------------------------------------------------------------------------------------------

    def _render_media(self):
        rows = collab_service.media_status()
        available = collab_service.transfers_available()
        self.share_media.blockSignals(True)
        self.share_media.setChecked(collab_service.sharing_media())
        self.share_media.blockSignals(False)
        self.share_media.setEnabled(available)
        self.media_note.setVisible(not available)
        self.media_note.setText(_('collab.media_no_links'))
        self.media_title.setVisible(bool(rows) or available)
        _clear(self.media_box)
        matches = collab_service.video_matches_project()
        current = (session.VIDEO or {}).get('filepath')
        for row in rows:
            label = _text()
            label.setText(f"{html.escape(row['name'])} · {_size(row['size'])} · {self._media_state(row)}")
            widgets = [label]
            if row['state'] == 'available' and available:
                get = _button()
                get.setText(_('collab.media_get'))
                derived = row['kind'] in ('waveform', 'stem')
                get.setEnabled(not derived or matches)
                if derived and not matches:
                    get.setToolTip(_('collab.media_needs_video'))
                get.clicked.connect(lambda _c=False, r=row: self.get_media(r))
                widgets.append(get)
            elif row['state'] in ('downloading', 'checking'):
                cancel = _button()
                cancel.setText(_('subtitldcc.cancel'))
                cancel.clicked.connect(lambda _c=False, h=row['hash']: collab_service.cancel_download(h))
                widgets.append(cancel)
            elif row['state'] == 'here' and row['kind'] == 'video' and row['path'] and row['path'] != current:
                use = _button()
                use.setText(_('collab.media_use_video'))
                use.clicked.connect(lambda _c=False, path=row['path']: use_video(self.panel.window_, path))
                widgets.append(use)
            self.media_box.addWidget(_row(*widgets))
        dubs = collab_service.dubs_status()
        self.dubs.setVisible(bool(dubs['missing']))
        if dubs['missing']:
            key = 'collab.dubs_coming' if dubs['coming'] else 'collab.dubs_waiting'
            self.dubs.setText(_(key).format(count=dubs['missing']))

    def _media_state(self, row):
        state = row['state']
        if state == 'downloading':
            return _('collab.media_downloading').format(percent=int(row['fraction'] * 100))
        if state == 'available':
            return _('collab.media_available').format(count=row['holders'])
        return _(f'collab.media_{state}')

    def get_media(self, row):
        """Get a file from the collaborators who have it: an original into a folder the person picks, the
        waveform and separated audio where Subtitld keeps them for the video open here."""
        from subtitld.modules import collab_media
        if row['kind'] == 'video':
            start = os.path.dirname(session.SUBTITLE.get('filepath') or (session.VIDEO or {}).get('filepath') or '') or os.path.expanduser('~')
            folder = QFileDialog.getExistingDirectory(self, _('collab.media_where'), start)
            if not folder:
                return
            destination = collab_media.free_path(folder, row['name'])
        else:
            destination = collab_media.destination(row['kind'], row['name'])
            if destination is None:
                return
        collab_service.download_media(row['hash'], destination, self._media_failed)

    def _media_failed(self, error):
        if isinstance(error, OSError) and str(error) == 'not enough room':
            self.panel.show_message(_('collab.media_no_room'), error=True)
            return
        self._call_failed(error)

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

    def open_link(self, doc_id):
        """A subtitld:// link to shared project `doc_id`, followed with a project open in the editor: asked
        here first, like opening one from the list."""
        if collab_state.shared() and collab_state.STATE.get('doc') == doc_id:
            self.panel.show_message(_('open_link.already_open'))
            return
        if not service.is_connected():
            self._link = doc_id  # once connected (signed_in)
            self.panel.show_message(_('open_link.connect_first'))
            return
        self._link = None

        def failed(error):
            if service.error_code(error) == 'not_found':
                self.panel.show_message(_('open_link.not_found'), error=True)
            else:
                self._call_failed(error)

        collab_service.project(self.ask_join, failed, doc_id=doc_id)

    def copy_link(self):
        url = (self._project or {}).get('url')
        if url:
            QApplication.clipboard().setText(url)
            self.panel.show_message(_('collab.link_copied'))

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
        """A new sign-in: the open shared project tries again with it, and a link waiting for it goes on."""
        if collab_state.shared():
            collab_service.connect()
        self.refresh()
        if self._link:
            self.open_link(self._link)

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
        self.copy_link_button.setText(_('collab.copy_link'))
        self.copy_link_button.setToolTip(_('collab.copy_link_tip'))
        self.members_title.setText(_('collab.members'))
        self.member_field.setPlaceholderText(_('collab.member_placeholder'))
        self.add_button.setText(_('collab.add'))
        self.sign_in_button.setText(_('collab.sign_in_again'))
        self.media_title.setText(_('collab.media'))
        self.share_media.setText(_('collab.media_share'))
        self.share_media.setToolTip(_('collab.media_share_tip'))
        self.confirm_no.setText(_('subtitldcc.cancel'))
        self.refresh(load=False)


class _Faces(QWidget):
    """Who is in the project: a small circle each, with an initial, overlapping; ringed green while speaking."""
    SIZE = 20
    STEP = 14

    def __init__(self):
        super().__init__()
        self._people = []                               # [(initial, speaking)]
        self.setFixedHeight(self.SIZE)

    def set_people(self, people):
        people = people[:6]
        if people != self._people:
            self._people = people
            self.setFixedWidth(self.SIZE + self.STEP * (len(people) - 1) if people else 0)
            self.update()

    def paintEvent(self, event):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QFont, QPainter, QPen
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont('Montserrat')
        font.setPixelSize(9)
        font.setWeight(QFont.Bold)
        painter.setFont(font)
        for index, (initial, speaking) in reversed(list(enumerate(self._people))):
            rect = QRectF(index * self.STEP + 1, 1, self.SIZE - 2, self.SIZE - 2)
            painter.setPen(QPen(QColor('#55d43f') if speaking else QColor('#1c252c'), 1.6))
            painter.setBrush(QColor('#4a6376'))
            painter.drawEllipse(rect)
            painter.setPen(QColor('#e3edf5'))
            painter.drawText(rect, Qt.AlignCenter, initial)


class LiveBar(QWidget):
    """The panel's footer for live co-editing, always in view under the cards, like a call's bar: whether
    the open project is shared and connected, its title and who is in it; under that, the microphone, the
    call, the chat (with what is unread) and leaving. Not shared, it offers to share it (the light tab in its
    corner) and the projects shared with you. Hidden while no one is signed in to subtitld.cc. The cards keep
    the details (who it is shared with, the media, the chat itself); the bar's questions are asked there."""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setObjectName('live_bar')
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)   # as tall as it needs
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(4)

        status = QHBoxLayout()
        status.setContentsMargins(12, 0, 10, 0)
        status.setSpacing(6)
        self.dot = QLabel()
        self.dot.setObjectName('live_bar_dot')
        self.dot.setFixedSize(8, 8)
        status.addWidget(self.dot)
        self.state = _text('live_bar_state')
        self.state.setWordWrap(False)
        status.addWidget(self.state)
        self.project = _text('live_bar_project')
        self.project.setWordWrap(False)
        self.project.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        status.addWidget(self.project, 1)
        self.faces = _Faces()
        status.addWidget(self.faces)
        layout.addLayout(status)

        controls = QHBoxLayout()
        controls.setContentsMargins(8, 0, 0, 0)
        controls.setSpacing(2)
        self.mic = self._icon('live_bar_mic.svg')
        self.mic.setCheckable(True)
        self.mic.toggled.connect(collab_service.voice_latch)
        self.call = self._icon('live_bar_call.svg')
        self.call.clicked.connect(lambda: self.panel.chat_card.toggle_call())
        self.chat = self._icon('live_bar_chat.svg')
        self.chat.setObjectName('live_bar_chat')
        self.chat.clicked.connect(self.show_chat)
        self.list = self._icon('live_bar_list.svg')
        self.list.clicked.connect(self.show_projects)
        for button in (self.mic, self.call, self.chat, self.list):
            controls.addWidget(button, 0, Qt.AlignVCenter)
        controls.addStretch()
        self.leave = self._icon('live_bar_leave.svg')
        self.leave.clicked.connect(self.ask_leave)
        controls.addWidget(self.leave, 0, Qt.AlignVCenter)
        controls.addSpacing(6)
        self.share = QPushButton()
        self.share.setProperty('class', 'addon_card_tab')     # the dialogs' light slanted tab, in the corner
        self.share.setFixedHeight(30)
        self.share.setCursor(Qt.PointingHandCursor)
        self.share.clicked.connect(self.start_sharing)
        controls.addWidget(self.share, 0, Qt.AlignBottom)
        layout.addLayout(controls)

        self._redraw = QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(100)
        self._redraw.timeout.connect(self.refresh)
        self._speaking = QTimer(self)                    # who is speaking, while in the call
        self._speaking.setInterval(250)
        self._speaking.timeout.connect(self._render_faces)
        collab_service.listeners.append(self._news)
        self.retranslate()

    def paintEvent(self, event):
        from PySide6.QtGui import QColor, QPainter
        QPainter(self).fillRect(self.rect(), QColor(0, 0, 0, 38))     # the header's shade

    def _icon(self, icon):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon
        button = QPushButton()
        button.setProperty('class', 'live_bar_icon')
        button.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS / icon)))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(30, 26)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def _news(self, kind):
        if kind in ('chat', 'state', 'peers', 'shared', 'stopped', 'voice'):
            self._redraw.start()

    # --- What it shows -----------------------------------------------------------------------------------

    def refresh(self):
        from PySide6.QtGui import QIcon
        connected = service.is_connected()
        self.setVisible(connected)
        if not connected:
            return
        shared = collab_state.shared()
        state = collab_service.state()
        if not shared:
            kind, text = 'off', _('collab.bar_off')
        elif collab_service.failure() == 'missing_scope':
            kind, text = 'trouble', _('collab.bar_offline')
        elif state == 'live':
            kind, text = 'live', _('collab.bar_live')
        elif state == 'joining':
            kind, text = 'joining', _('collab.joining')
        else:
            kind, text = 'trouble', _('collab.bar_offline')
        self.dot.setProperty('state', kind)
        self.dot.style().unpolish(self.dot)
        self.dot.style().polish(self.dot)
        self.state.setText(text)
        title = ((self.panel.live_card._project or {}).get('title') or '') if shared else ''
        self.project.setText(f'· {title}' if title else '')

        voice = collab_service.voice_state()
        on = voice['on']
        available = collab_service.voice_available()
        for button in (self.mic, self.call, self.chat, self.leave):
            button.setVisible(shared)
        self.list.setVisible(not shared)
        self.share.setVisible(not shared)
        self.mic.setEnabled(on)
        self.mic.blockSignals(True)
        self.mic.setChecked(on and voice['latched'])
        self.mic.blockSignals(False)
        self.mic.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS /
                                   ('live_bar_mic.svg' if on and voice['latched'] else 'live_bar_mic_off.svg'))))
        self.call.setEnabled(available)
        self.call.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS /
                                    ('live_bar_call_leave.svg' if on else 'live_bar_call.svg'))))
        self.call.setToolTip(_('collab.voice_leave') if on else _('collab.voice_join'))
        unread = collab_service.chat_unread() if shared else 0
        self.chat.setText(str(unread) if unread else '')
        self.chat.setFixedWidth(42 if unread else 30)
        manage = bool(((self.panel.live_card._project or {}).get('permissions') or {}).get('can_manage'))
        self.leave.setToolTip(_('collab.stop_sharing') if manage else _('collab.leave'))
        if on and not self._speaking.isActive():
            self._speaking.start()
        elif not on:
            self._speaking.stop()
        self._render_faces()

    def _render_faces(self):
        if not collab_state.shared():
            self.faces.set_people([])
            return
        voice = collab_service.voice_state()
        if voice['on']:
            people = [((p['name'] or p['handle'] or '?')[:1].upper(), p['speaking']) for p in voice['people']]
        else:
            people = [((handle or '?')[:1].upper(), False) for handle in self.panel.live_card._here()]
        self.faces.set_people(people)

    # --- What it does ------------------------------------------------------------------------------------

    def _show(self, widget):
        self.panel.scroll.ensureWidgetVisible(widget)

    def show_chat(self):
        self._show(self.panel.chat_card)
        self.panel.chat_card.field.setFocus()

    def show_projects(self):
        self.panel.live_card.load_projects()
        self._show(self.panel.live_card)

    def start_sharing(self):
        """Share it under the title the card holds (the project's own, unless changed there)."""
        card = self.panel.live_card
        card.refresh()
        if card.title_field.text().strip():
            card.share()
        self._show(card)

    def ask_leave(self):
        self.panel.live_card.ask_stop()
        self._show(self.panel.live_card.confirm)

    def retranslate(self):
        self.mic.setToolTip(_('collab.voice_latch'))
        self.chat.setToolTip(_('collab.chat_title'))
        self.list.setToolTip(_('collab.shared_with_you'))
        self.share.setText(_('collab.share'))
        self.refresh()
