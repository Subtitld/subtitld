"""Live co-editing, in the subtitld.cc panel (interface/left_panel_subtitldcc.py).

Not shared yet: share the open project (as yourself or a group you publish as), or open one shared with you.
Shared: whether it is connected (changes made offline wait and go when it is back), who else is in it, who it
is shared with (the owner adds and removes people), and leaving it or, for the owner, stopping sharing it.
Either way the project stays in the editor as it is.

Questions are asked in place; calls to subtitld.cc run in the background (modules/collab_service.py).
"""

import html
import os

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from subtitld.interface.account_header import AccountHeader
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
            item.widget().hide()                        # gone now, not when the deletion comes round
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
    for path in (session.SUBTITLE.filepath, (session.VIDEO or {}).get('filepath')):
        if path:
            return os.path.splitext(os.path.basename(path))[0]
    return ''


def _shared_ago(stamp):
    """How long ago subtitld.cc says a project was shared (its `created_at`, ISO 8601), in words; '' unknown."""
    if not stamp:
        return ''
    from datetime import datetime
    from subtitld.interface import utils
    try:
        when = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
    except ValueError:
        return ''
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)   # friendly_time counts from the local, naive now
    return utils.friendly_time(when)


class _HoverRow(QWidget):
    """A line of a list: its text across the line, and its button at the end, shown while the pointer is on
    the line (its room kept, so the text does not move)."""

    def __init__(self, label, button):
        super().__init__()
        self.setProperty('class', 'transparent_panel')
        self.setAttribute(Qt.WA_Hover)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(label, 1)
        layout.addWidget(button, 0, Qt.AlignVCenter)
        policy = button.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        button.setSizePolicy(policy)
        button.setVisible(False)
        self.button = button

    def enterEvent(self, event):
        super().enterEvent(event)
        self.button.setVisible(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.button.setVisible(False)


class _MemberBadge(QWidget):
    """Someone it is shared with: their initial in a circle and their @handle, as wide as that. For whoever
    manages it, the circle turns into a × removing them while the pointer is on the badge."""
    AVATAR = 18

    def __init__(self, member, manage):
        super().__init__()
        self.setObjectName('live_member_badge')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setToolTip(member.get('name') or '')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 8, 3)
        layout.setSpacing(5)
        corner = QWidget()                              # the circle, or in its place the ×
        corner.setProperty('class', 'transparent_panel')
        corner.setFixedSize(self.AVATAR, self.AVATAR)
        self.avatar = QLabel(corner)
        self.avatar.setFixedSize(self.AVATAR, self.AVATAR)
        self.avatar.setPixmap(self._avatar((member.get('name') or member['handle'])[:1].upper()))
        self.remove = QPushButton('×', corner)
        self.remove.setObjectName('live_member_remove')
        self.remove.setFixedSize(self.AVATAR, self.AVATAR)
        self.remove.setCursor(Qt.PointingHandCursor)
        self.remove.setToolTip(_('collab.remove'))
        self.remove.setAccessibleName(_('collab.remove'))
        self.remove.setVisible(False)
        layout.addWidget(corner)
        handle = QLabel(f"@{member['handle']}")
        handle.setObjectName('live_member_handle')
        layout.addWidget(handle)
        self._manage = manage

    def _avatar(self, initial):
        from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
        ratio = self.devicePixelRatioF() or 1
        pixmap = QPixmap(int(self.AVATAR * ratio), int(self.AVATAR * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#4a6376'))
        painter.drawEllipse(0, 0, self.AVATAR, self.AVATAR)
        font = QFont('Montserrat')
        font.setPixelSize(9)
        font.setWeight(QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor('#e3edf5'))
        painter.drawText(0, 0, self.AVATAR, self.AVATAR, Qt.AlignCenter, initial)
        painter.end()
        return pixmap

    def enterEvent(self, event):
        super().enterEvent(event)
        self.remove.setVisible(self._manage)
        self.avatar.setVisible(not self._manage)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.remove.setVisible(False)
        self.avatar.setVisible(True)


class _SectionCard(QWidget):
    """A card in the live bar's drawer: a header (its title, + or −) that opens a list under it, as tall as
    the list up to HEIGHT, then scrolling. `box` is the list's layout; `opened` says it opened or closed."""
    HEIGHT = 180

    def __init__(self):
        from PySide6.QtCore import Signal
        super().__init__()
        self.setObjectName('live_section_card')
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = QPushButton()
        self.header.setObjectName('live_section_toggle')
        self.header.setCheckable(True)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setFixedHeight(26)
        header = QHBoxLayout(self.header)
        header.setContentsMargins(10, 0, 10, 0)
        self.title = QLabel()
        self.title.setObjectName('live_section_title')
        self.sign = QLabel('+')
        self.sign.setObjectName('live_section_title')
        for label in (self.title, self.sign):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.sign)
        self.header.toggled.connect(self._toggled)
        layout.addWidget(self.header)
        from PySide6.QtWidgets import QScrollArea
        self.body = QScrollArea()
        self.body.setObjectName('live_section_list')
        self.body.setWidgetResizable(True)
        self.body.setFrameShape(QScrollArea.NoFrame)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setProperty('class', 'transparent_panel')
        self.box = QVBoxLayout(inner)
        self.box.setContentsMargins(10, 8, 10, 10)
        self.box.setSpacing(6)
        self.body.setWidget(inner)
        self.body.setVisible(False)
        layout.addWidget(self.body)
        self.body.viewport().installEventFilter(self)   # a new width: rows that wrap (badges) measured again
        self.on_open = None                              # called with True when it opens

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize and event.size().width() != event.oldSize().width():
            self.fit()
        return super().eventFilter(watched, event)

    def set_title(self, text):
        self.title.setText(text.upper())

    def _toggled(self, opened):
        self.sign.setText('−' if opened else '+')
        self.body.setVisible(opened)
        if opened:
            if self.on_open is not None:
                self.on_open()
            self.fit()

    def fit(self):
        """The list as tall as its rows, up to HEIGHT."""
        QTimer.singleShot(0, self._fit)

    def _fit(self):
        content = self.body.widget()
        width = self.body.viewport().width()
        need = content.heightForWidth(width) if content.hasHeightForWidth() else content.sizeHint().height()
        self.body.setFixedHeight(min(max(need, 0), self.HEIGHT))


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
        self._shown_conflicts = 0

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
        # The live bar's Share live shares it, under the project's own title: no form of its own here.
        self.title_field.setVisible(False)
        self.share_button.setVisible(False)
        # The projects shared with you: a card whose header opens (and loads them again) the list.
        self.projects_card = _SectionCard()
        self.list_button = self.projects_card.header
        self.projects_card.on_open = self.load_projects
        self.projects_box = self.projects_card.box
        self.projects_panel = self.projects_card.body
        share.addWidget(self.projects_card)
        layout.addWidget(self.share_box)

        # Shared: who is here, who it's shared with.
        self.shared_box = QWidget()
        self.shared_box.setProperty('class', 'transparent_panel')
        shared = QVBoxLayout(self.shared_box)
        shared.setContentsMargins(0, 0, 0, 0)
        shared.setSpacing(6)
        self.here = _text()                             # who is here: the live bar's faces say it
        self.here.setVisible(False)
        shared.addWidget(self.here)
        self.publishes_note = ''                         # where it publishes (the live bar's link says it)
        # Its page on subtitld.cc, where collaborators open it in Subtitld (subtitldcc_open.py).
        # (the live bar's link copies it; this button stays for a link to the project already open)
        self.copy_link_button = _button()
        self.copy_link_button.clicked.connect(self.copy_link)
        shared.addWidget(_row(self.copy_link_button))
        self.conflicts = _text()
        shared.addWidget(self.conflicts)
        self.conflicts_box = QVBoxLayout()
        shared.addLayout(self.conflicts_box)
        # Who it is shared with: its own header (the owner on a tab hanging from the top-left corner, the
        # title, and for whoever manages it a field adding someone, Enter), then a badge a person, wrapping.
        from subtitld.interface.left_panel_videoproperties import _FlowLayout
        self.members_card = _SectionCard()
        self.members_card.header.setVisible(False)
        self.members_card.header.setChecked(True)          # always open
        head = QWidget()
        head.setObjectName('live_members_head')
        head.setAttribute(Qt.WA_StyledBackground, True)
        head.setFixedHeight(28)                         # the strip is 26 (the QSS): the owner's tab hangs 2 below
        head_line = QHBoxLayout(head)
        head_line.setContentsMargins(0, 0, 0, 0)        # the tab and the field reach its edges
        head_line.setSpacing(0)
        self.owner_tab = QLabel()
        self.owner_tab.setObjectName('live_owner_tab')
        self.owner_tab.setFixedHeight(28)
        head_line.addWidget(self.owner_tab, 0, Qt.AlignTop)
        head_line.addSpacing(8)
        self.members_title = QLabel()
        self.members_title.setObjectName('live_section_title')
        self.members_title.setFixedHeight(26)
        head_line.addWidget(self.members_title, 0, Qt.AlignTop)
        head_line.addSpacing(8)
        self.member_field = QLineEdit()
        self.member_field.setObjectName('live_member_field')
        self.member_field.setFixedHeight(26)
        self.member_field.returnPressed.connect(self.add_member)
        head_line.addWidget(self.member_field, 1, Qt.AlignTop)
        # Add: once something is typed, a button inside the field filling its right end (_place_add)
        self.add_button = QPushButton(self.member_field)
        self.add_button.setObjectName('live_member_add')
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.setFixedHeight(26)
        self.add_button.clicked.connect(self.add_member)
        self.add_button.setVisible(False)
        self.member_field.textChanged.connect(self._show_add)
        self.member_field.installEventFilter(self)
        self.head_stretch = QWidget()                      # the field's room, for whoever cannot add people
        self.head_stretch.setProperty('class', 'transparent_panel')
        head_line.addWidget(self.head_stretch, 1)
        self.members_card.layout().insertWidget(0, head)
        badges = QWidget()
        badges.setProperty('class', 'transparent_panel')
        self.members_box = _FlowLayout(badges, spacing=4)
        self.members_card.box.addWidget(badges)
        self.members_card.box.setContentsMargins(6, 4, 6, 6)    # (6 under the strip: its tab hangs into it)
        # (the live bar shows it, in a pop-up at its foot's left corner: LiveBar.show_people)
        # Media: the project's files, sent between collaborators (modules/collab_transfer.py): a card that
        # opens on whether this computer shares its own, then one line a file (its name, size and where it is).
        self.media_card = _SectionCard()
        self.media_title = self.media_card.title
        self.share_media = QCheckBox()
        self.share_media.toggled.connect(collab_service.set_sharing_media)
        self.media_card.box.addWidget(self.share_media)
        self.media_note = _text()
        self.media_card.box.addWidget(self.media_note)
        self.media_box = QVBoxLayout()
        self.media_box.setSpacing(2)
        self.media_card.box.addLayout(self.media_box)
        self.dubs = _text()
        self.media_card.box.addWidget(self.dubs)
        # (the live bar shows it, in a dialog: LiveBar.show_media)
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
            player._audio_device.sync_subtitle_dubs(session.SUBTITLE.segments)
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

    def _render_shared(self):
        state = collab_service.state()
        waiting = len(collab_state.STATE.get('pending') or [])
        failure = collab_service.failure()
        # all well (live, nothing waiting): the live bar's dot says it; the card says only what is not
        self.status.setVisible(not (failure != 'missing_scope' and state == 'live' and not waiting))
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
        new, self._shown_conflicts = len(found) > self._shown_conflicts, len(found)
        self.conflicts.setVisible(bool(found))
        self.conflicts.setText(_('collab.conflicts').format(count=len(found)))
        if new:
            self.reveal(self.conflicts)                 # new ones need an answer (shown first, so the drawer has it)
        _clear(self.conflicts_box)
        for conflict in found[:10]:
            self.conflicts_box.addWidget(self._conflict_row(conflict))

        project = self._project or {}
        manage = bool((project.get('permissions') or {}).get('can_manage'))
        _clear(self.members_box)
        if project:
            owner = (project.get('owner') or {}).get('handle', '')
            self.owner_tab.setText(f'@{owner}')
            self.owner_tab.setToolTip(_('collab.owner').format(handle=owner))
            for member in project.get('members') or []:
                badge = _MemberBadge(member, manage)
                badge.remove.clicked.connect(lambda _c=False, handle=member['handle']: self.remove_member(handle))
                self.members_box.addWidget(badge)
        self.members_card.setVisible(bool(project))
        self.members_card.fit()
        # where it publishes: said by the live bar's link (its tooltip), not in the drawer, which opens only on
        # what needs an answer
        subtitle = project.get('subtitle')
        self.publishes_note = _('collab.publishes_to').format(
            title=subtitle.get('title') or '', version=subtitle.get('version') or 0) if subtitle else ''
        self.copy_link_button.setVisible(False)        # the live bar's link copies it
        self.member_field.setVisible(manage)
        self._show_add()
        self.head_stretch.setVisible(not manage)
        self.stop_button.setText(_('collab.stop_sharing') if manage else _('collab.leave'))
        self.stop_button.setVisible(False)             # the live bar's status line has it, on hover
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
        self.media_card.setVisible(bool(rows) or available)
        _clear(self.media_box)
        matches = collab_service.video_matches_project()
        current = (session.VIDEO or {}).get('filepath')
        for row in rows:
            # one line a file: its name, its size, and where it is (the path here, or its state)
            label = _text('live_media_line')
            label.setWordWrap(False)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            where = row['path'] if row['state'] == 'here' and row.get('path') else self._media_state(row)
            line = f"{row['name']} · {_size(row['size'])} · {where}"
            label.setText(html.escape(line))
            label.setToolTip(line)
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
            line_row = QWidget()
            line_row.setProperty('class', 'transparent_panel')
            line_layout = QHBoxLayout(line_row)
            line_layout.setContentsMargins(0, 0, 0, 0)
            line_layout.setSpacing(6)
            line_layout.addWidget(widgets[0], 1)          # the line takes the room the button leaves
            for button in widgets[1:]:
                line_layout.addWidget(button)
            self.media_box.addWidget(line_row)
        dubs = collab_service.dubs_status()
        self.dubs.setVisible(bool(dubs['missing']))
        if dubs['missing']:
            key = 'collab.dubs_coming' if dubs['coming'] else 'collab.dubs_waiting'
            self.dubs.setText(_(key).format(count=dubs['missing']))
        self.media_card.fit()

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
            start = os.path.dirname(session.SUBTITLE.filepath or (session.VIDEO or {}).get('filepath') or '') or os.path.expanduser('~')
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
            owner = html.escape((project.get('owner') or {}).get('handle', ''))
            age = _shared_ago(project.get('created_at'))
            label.setText(f"<b>{html.escape(project['title'])}</b><br>@{owner}" + (f" · {html.escape(age)}" if age else ''))
            open_button = _button()
            open_button.setText(_('collab.open'))
            open_button.clicked.connect(lambda _c=False, p=project: self.ask_join(p))
            self.projects_box.addWidget(_HoverRow(label, open_button))
        self.projects_card.fit()
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
            self.panel.show_message(_('open_link.already_open'))   # its link is in the live bar, to copy
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

    def _show_add(self):
        shown = not self.member_field.isHidden() and bool(self.member_field.text().strip())
        self.add_button.setVisible(shown)
        self._place_add()

    def _place_add(self):
        field, button = self.member_field, self.add_button
        button.adjustSize()
        button.move(field.width() - button.width(), 0)  # flush with its top, right and bottom
        # the text stops short of it
        field.setTextMargins(0, 0, button.width() if button.isVisibleTo(field) else 0, 0)

    def eventFilter(self, watched, event):
        if watched is self.member_field and event.type() == QEvent.Resize:
            self._place_add()
        return super().eventFilter(watched, event)

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
        self.reveal(self.confirm)

    def has_content(self):
        """Whether the card has anything to show: not shared, its sharing; shared, only what needs saying (a
        problem, conflicts, where it publishes, a question): the rest is in the live bar."""
        if not collab_state.shared():
            return True
        return any(not w.isHidden() for w in (self.status, self.conflicts, self.sign_in_button,
                                              self.copy_link_button, self.stop_button, self.confirm))

    def reveal(self, widget=None):
        """The card lives in the live bar's drawer (LiveBar): opened, and scrolled to `widget`, when it has
        something to ask or show."""
        bar = getattr(self.panel, 'live_bar', None)
        if bar is not None:
            bar._show_in_drawer(widget or self)

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
        self.projects_card.set_title(_('collab.shared_with_you'))
        self.members_title.setText(_('collab.members').upper())
        self.media_card.set_title(_('collab.media'))
        self.copy_link_button.setText(_('collab.copy_link'))
        self.copy_link_button.setToolTip(_('collab.copy_link_tip'))
        self.member_field.setPlaceholderText(_('collab.member_add_placeholder'))
        self.member_field.setToolTip(_('collab.member_placeholder'))
        self.add_button.setText(_('collab.add').upper())
        self.sign_in_button.setText(_('collab.sign_in_again'))
        self.share_media.setText(_('collab.media_share'))
        self.share_media.setToolTip(_('collab.media_share_tip'))
        self.confirm_no.setText(_('subtitldcc.cancel'))
        self.refresh(load=False)


class _LinkButton(QPushButton):
    """The project's link, quietly, in the live bar's status line; hovered, a copy icon says a click copies it."""

    def __init__(self):
        super().__init__()
        self.setObjectName('live_bar_link')
        self.setCursor(Qt.PointingHandCursor)
        self.setLayoutDirection(Qt.RightToLeft)          # the icon after the link
        from PySide6.QtCore import QSize
        self.setIconSize(QSize(12, 12))

    def enterEvent(self, event):
        from PySide6.QtGui import QIcon
        super().enterEvent(event)
        self.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS / 'live_bar_copy.svg')))

    def leaveEvent(self, event):
        from PySide6.QtGui import QIcon
        super().leaveEvent(event)
        self.setIcon(QIcon())


class _Ripple(QWidget):
    """A ring growing out of a widget and fading, twice: something new there (a chat message). Laid over the
    window, the pointer goes through it; it goes away when done."""
    PULSES = 2
    DURATION = 700                                      # ms, a pulse
    GROW = 90                                           # px the ring grows past the icon
    WIDTH = 3.5                                         # the ring's thickness
    BLUR = 3                                            # px its edges soften
    COLOUR = '#8fe57f'

    def __init__(self, target, centre=None, base=12):
        """Around `target`'s icon: at `centre` (in the target, its middle by default), from a ring of `base`
        px (about the icon's size)."""
        from PySide6.QtCore import QPoint, QVariantAnimation
        window = target.window()
        super().__init__(window)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._base = base
        size = int(2 * (self._base + self.GROW + self.WIDTH + self.BLUR * 2))
        centre = target.mapTo(window, centre or QPoint(target.width() // 2, target.height() // 2))
        self.setGeometry(centre.x() - size // 2, centre.y() - size // 2, size, size)
        self._phase = 0.0
        from PySide6.QtWidgets import QGraphicsBlurEffect
        blur = QGraphicsBlurEffect(self)
        blur.setBlurRadius(self.BLUR)
        self.setGraphicsEffect(blur)                    # soft, not a hard line
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(self.DURATION)
        self._animation.setLoopCount(self.PULSES)
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self.deleteLater)
        self.show()
        self.raise_()
        self._animation.start()

    def _step(self, value):
        self._phase = float(value)
        self.update()

    def paintEvent(self, event):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QColor, QPainter, QPen
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # it starts slowly and speeds up as it grows (ease-in); it fades fast at first, then slower (ease-out)
        grown = self._phase ** 3
        colour = QColor(self.COLOUR)
        colour.setAlphaF(0.85 * (1 - self._phase) ** 2)
        painter.setPen(QPen(colour, self.WIDTH))
        painter.setBrush(Qt.NoBrush)
        radius = self._base + self.GROW * grown
        painter.drawEllipse(QPointF(self.width() / 2, self.height() / 2), radius, radius)


class _Faces(QWidget):
    """Who is in the project: a small circle each, with an initial, overlapping; ringed green while speaking."""
    SIZE = 20
    STEP = 14
    MARGIN = 8                                          # from the bar's left edge (the line has none)

    def __init__(self):
        super().__init__()
        self._people = []                               # [(initial, speaking)]
        self.on_click = None
        self.setVisible(False)                          # nobody yet: it takes no room until someone is there
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(self.SIZE)

    def mousePressEvent(self, event):
        if self.on_click is not None:
            self.on_click()

    def set_people(self, people):
        people = people[:6]
        if people != self._people:
            self._people = people
            self.setFixedWidth(self.MARGIN + self.SIZE + self.STEP * (len(people) - 1) if people else 0)
            self.setVisible(bool(people))               # nobody: no room taken (the people button is at the edge)
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
            rect = QRectF(self.MARGIN + index * self.STEP + 1, 1, self.SIZE - 2, self.SIZE - 2)
            painter.setPen(QPen(QColor('#55d43f') if speaking else QColor('#1c252c'), 1.6))
            painter.setBrush(QColor('#4a6376'))
            painter.drawEllipse(rect)
            painter.setPen(QColor('#e3edf5'))
            painter.drawText(rect, Qt.AlignCenter, initial)


class LiveBar(QWidget):
    """The panel's footer for live co-editing, always in view under the cards, like a call's bar: whether
    the open project is shared and connected, its title and who is in it; under that, the microphone, the
    call, the chat (with what is unread) and leaving. Not shared, it offers to share it (the light tab in its
    corner). The toggle at the status line's start opens a drawer under that line with the co-editing card
    (sharing, the projects shared with you, members, media). Hidden while no one is signed in to subtitld.cc. The cards keep
    the details (who it is shared with, the media, the chat itself); the bar's questions are asked there."""

    DRAWER_HEIGHT = 480                                 # at most: past it, the drawer scrolls
    RISE = 4                                            # Share live stands this much above the bar's shade
    SLANT = 13                                          # the call tab's left slice (slant and top curve): the chat tab reaches under it

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setObjectName('live_bar')
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)   # as tall as it needs
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # The drawer: the live co-editing card (sharing, who it is shared with, the media), opened from the
        # toggle at the status line's end, under that line (which stays on top).
        from PySide6.QtWidgets import QScrollArea
        self.drawer = QScrollArea()
        self.drawer.setObjectName('live_bar_drawer')
        self.drawer.setWidgetResizable(True)
        self.drawer.setFrameShape(QScrollArea.NoFrame)
        self.drawer.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.drawer.setMaximumHeight(self.DRAWER_HEIGHT)
        # the live card, and the chat under it
        self.drawer_content = QWidget()
        self.drawer_content.setProperty('class', 'transparent_panel')
        content = QVBoxLayout(self.drawer_content)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        # each in a holder the bar shows or hides (the cards show or hide themselves by their own state):
        # the live card with the toggle, the chat with the chat tab
        self.live_holder = self._holder(panel.live_card)
        self.chat_holder = self._holder(panel.chat_card)
        content.addWidget(self.live_holder)
        content.addWidget(self.chat_holder)
        self.drawer.setWidget(self.drawer_content)
        self.drawer.setVisible(False)
        self.drawer_content.installEventFilter(self)    # its content changes: the drawer follows (_fit_drawer)
        panel.live_card.title.setVisible(False)         # the bar says what it is

        # The status line: the drawer's toggle, whether it is shared and connected, the project, who is in it.
        # Not shared and closed, it shares the bottom line with Share live (_arrange).
        self.status_line = QWidget()
        self.status_line.setFixedHeight(26)             # the shade's height
        status = QHBoxLayout(self.status_line)
        status.setContentsMargins(0, 0, 0, 0)
        status.setSpacing(6)
        self.toggle = self._icon('live_bar_menu.svg')
        self.toggle.setObjectName('live_bar_toggle')
        self.toggle.setFixedSize(30, 26)                # the shade's full height, from its left edge
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self.open_drawer)
        status.addWidget(self.toggle, 0, Qt.AlignBottom)
        self.dot = QLabel()
        self.dot.setObjectName('live_bar_dot')
        self.dot.setFixedSize(8, 8)
        status.addWidget(self.dot)
        self.state = _text('live_bar_state')
        self.state.setWordWrap(False)
        status.addWidget(self.state)
        # shared: its link, copied when clicked
        self.link = _LinkButton()
        self.link.clicked.connect(lambda: self.panel.live_card.copy_link())
        status.addWidget(self.link)
        status.addStretch(1)
        # stopping sharing it (or leaving it): at the line's end, a word, only while the pointer is on the bar
        self.leave = QPushButton()
        self.leave.setObjectName('live_bar_leave')
        self.leave.setCursor(Qt.PointingHandCursor)
        self.leave.setFixedHeight(26)                   # the line's full height, to its right edge
        self.leave.clicked.connect(lambda: self.panel.live_card.ask_stop())
        self.leave.setVisible(False)
        status.addWidget(self.leave, 0, Qt.AlignVCenter)
        layout.addWidget(self.status_line)
        layout.addWidget(self.drawer)

        self.controls = controls = QHBoxLayout()
        controls.setContentsMargins(8, 0, 0, 0)
        controls.setSpacing(2)
        # On the left, who is in it.
        self.faces = _Faces()
        self.faces.on_click = lambda: self.people.toggle()
        controls.addWidget(self.faces, 0, Qt.AlignVCenter)
        # no one in it: who it is shared with, from an icon
        self.people = self._icon('live_bar_people.svg')
        self.people.setObjectName('live_bar_people')
        self.people.setCheckable(True)
        self.people.toggled.connect(self._open_people)
        self.people.setFixedSize(30, 26)               # the shade's height, from the bar's left edge and foot
        controls.addWidget(self.people, 0, Qt.AlignBottom)
        controls.addStretch()
        # On the right: hanging up (in the call), the chat, and the call's tab in the corner.
        self.hang_up = self._icon('live_bar_call_leave.svg')
        self.hang_up.setObjectName('live_bar_hang_up')
        self.hang_up.clicked.connect(lambda: collab_service.voice_leave())
        controls.addWidget(self.hang_up, 0, Qt.AlignVCenter)
        # the project's media, in a dialog
        self.media = self._icon('live_bar_media.svg')
        self.media.clicked.connect(self.show_media)
        controls.addWidget(self.media, 0, Qt.AlignVCenter)
        self._build_windows()
        # The chat: a slanted tab like the call's, bare until checked (then the call tab's blue, very faint);
        # checked, the chat shows in the drawer.
        from PySide6.QtCore import QSize as _QSize
        from PySide6.QtGui import QIcon as _QIcon
        self.chat = QPushButton(self)                   # placed by hand (_place_chat), its slant on the call tab's
        self.chat.setObjectName('live_bar_chat')
        self.chat.setCheckable(True)
        self.chat.setCursor(Qt.PointingHandCursor)
        self.chat.setIcon(_QIcon(str(session.PATH_SUBTITLD_GRAPHICS / 'live_bar_chat.svg')))
        self.chat.setIconSize(_QSize(16, 16))
        self.chat.setFixedHeight(30)
        self.chat.toggled.connect(self.open_chat)
        self.chat_place = QWidget()                     # the room it takes in the line, less the shared slant
        self.chat_place.setFixedHeight(30)
        self.chat_place.installEventFilter(self)
        controls.addWidget(self.chat_place, 0, Qt.AlignBottom)
        # The call's tab: out of the call, it joins it (light, like Share live); in it, green, it is the
        # microphone: held, talking (push to talk), unless the lock keeps it on the air.
        self.call = QPushButton()
        self.call.setProperty('class', 'live_bar_tab')
        self.call.setObjectName('live_bar_call')
        self.call.setFixedSize(62, 30)
        self.call.setCursor(Qt.PointingHandCursor)
        from PySide6.QtCore import QSize
        self.call.setIconSize(QSize(16, 16))
        self.call.pressed.connect(self._call_pressed)
        self.call.released.connect(self._call_released)
        self.call.clicked.connect(self._call_clicked)
        controls.addWidget(self.call, 0, Qt.AlignBottom)
        # In the call, the lock sits inside the tab at its left, an inner badge along the slant (like the
        # title bar's quick export inside its tab): on, it keeps the microphone on the air without holding.
        from PySide6.QtCore import QSize as _QSize
        self.lock = QPushButton(self.call)
        self.lock.setObjectName('live_bar_lock')
        self.lock.setCheckable(True)
        self.lock.setCursor(Qt.PointingHandCursor)
        self.lock.setIconSize(_QSize(12, 12))
        self.lock.setGeometry(1, 6, 24, 20)              # its slant on the tab's own (x = 1 at its foot, y 26)
        self.lock.toggled.connect(collab_service.voice_latch)
        self.share = QPushButton()
        self.share.setProperty('class', 'live_bar_tab')       # the dialogs' light slanted tab, in the corner
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
        self._unread_seen = 0                           # unread messages already told about (_news)
        self._chime = None
        collab_service.listeners.append(self._news)
        self.retranslate()

    def paintEvent(self, event):
        from PySide6.QtGui import QColor, QPainter
        painter = QPainter(self)
        # the header's shade, under the strip Share live rises into (the cards' ground continues there)
        painter.fillRect(self.rect().adjusted(0, 0, 0, -(self.height() - self.RISE)), QColor(184, 206, 224, 15))
        painter.fillRect(self.rect().adjusted(0, self.RISE, 0, 0), AccountHeader.SHADE)
        if self.people_panel.isVisible():
            # open: the panel and the line under it in one lighter colour, as the dubbing range does
            painter.fillRect(self.rect().adjusted(0, self.people_panel.y(), 0, 0), QColor('#2f404d'))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_shadow()

    def showEvent(self, event):
        super().showEvent(event)
        self._place_shadow()
        if self.chat_holder.isVisible():                # back on the panel, the chat open: what came is read
            collab_service.chat_mark_read()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._place_shadow()                            # no bar (no one signed in): no shadow over the cards

    def _place_shadow(self):
        if getattr(self.panel, 'live_bar', None) is self and hasattr(self.panel, 'footer_shadow'):
            self.panel._place_shadows()                 # its shadow rises from wherever its top is now

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
        if kind == 'chat':
            unread = collab_service.chat_unread()
            if unread > self._unread_seen:
                self._message_arrived()
            self._unread_seen = unread
            if unread and self.chat_holder.isVisible():     # the chat is open in front of them
                collab_service.chat_mark_read()         # read as it arrives: no count, no green
        if kind in ('chat', 'state', 'peers', 'shared', 'stopped', 'voice', 'conflicts', 'document'):
            self._redraw.start()

    def _mark_navigation(self, live):
        """Live: the subtitld.cc panel's button in the sidebar takes the green bar it shows when subtitles were
        found for the video (left_panel_subtitldcc._mark_navigation), seen from any panel."""
        from subtitld.interface import left_panel
        window = self.window()
        for button in (window.findChildren(left_panel.navigation_button) if window is not None else []):
            if button.tab_name == 'subtitldcc' and button.property('live') != live:
                button.setProperty('live', live)
                button.style().unpolish(button)
                button.style().polish(button)

    def _chat_icon_centre(self):
        """Where the chat tab draws its icon: icon and count centred between its 13px slanted ends."""
        from PySide6.QtCore import QPoint
        chat = self.chat
        text = chat.text()
        content = 16 + (4 + chat.fontMetrics().horizontalAdvance(text) if text else 0)
        left = 13 + (chat.width() - 26 - content) / 2
        return QPoint(int(left + 8), chat.height() // 2)

    def _message_arrived(self):
        """Someone wrote: a soft chime, and, unless the chat is in view, a ring around where it is: its tab in
        the bar, or, with another panel open, the subtitld.cc panel's button."""
        if self._chime is None:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect
            self._chime = QSoundEffect(self)
            self._chime.setSource(QUrl.fromLocalFile(str(session.PATH_SUBTITLD_GRAPHICS / 'chat_message.wav')))
            self._chime.setVolume(0.4)
        self._chime.play()
        # the bar first takes in the new count (the chat tab widens, so it moves), then the ring finds its icon
        self.refresh()
        QTimer.singleShot(60, self._ring)

    def _ring(self):
        if self.panel.isVisible():
            if self.chat.isVisible() and not self.chat.isChecked():
                _Ripple(self.chat, self._chat_icon_centre())
            return
        from subtitld.interface import left_panel
        button = next((b for b in self.window().findChildren(left_panel.navigation_button)
                       if b.tab_name == 'subtitldcc' and b.isVisible()), None)
        if button is not None:
            _Ripple(button, self._icon_centre(button))

    @staticmethod
    def _icon_centre(button):
        """The middle of where `button` draws its icon: its contents once its style's borders and padding are
        off (a sidebar button has a 10px bar down its left)."""
        from PySide6.QtWidgets import QStyle, QStyleOptionButton
        option = QStyleOptionButton()
        button.initStyleOption(option)
        return button.style().subElementRect(QStyle.SE_PushButtonContents, option, button).center()

    # --- What it shows -----------------------------------------------------------------------------------

    def refresh(self):
        from PySide6.QtGui import QIcon
        connected = service.is_connected()
        self.setVisible(connected)
        if not connected:
            self._mark_navigation(False)
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
        self._mark_navigation(kind == 'live')
        self.dot.setProperty('state', kind)
        self.dot.style().unpolish(self.dot)
        self.dot.style().polish(self.dot)
        self.state.setText(text)
        url = ((self.panel.live_card._project or {}).get('url') or '') if shared else ''
        self.link.setText(url.split('://', 1)[-1])
        note = self.panel.live_card.publishes_note if shared else ''
        self.link.setToolTip(_('collab.copy_link') + (f'\n\n{note}' if note else ''))
        self.link.setVisible(bool(url))

        voice = collab_service.voice_state()
        on = voice['on']
        available = collab_service.voice_available()
        self.chat.setVisible(shared)
        self.chat_place.setVisible(shared)
        self.media.setVisible(shared)
        self.leave.setVisible(shared and self.underMouse())
        manage = bool(((self.panel.live_card._project or {}).get('permissions') or {}).get('can_manage'))
        self.leave.setText(_('collab.stop_sharing') if manage else _('collab.leave'))
        self.call.setVisible(shared)
        self.hang_up.setVisible(shared and on)
        self.lock.setVisible(shared and on)
        self.share.setVisible(not shared)
        # shared with nothing in the card to say: no toggle (it would open on nothing)
        has = self.panel.live_card.has_content()
        self.toggle.setVisible(has)
        self.status_line.layout().setContentsMargins(0 if has else 12, 0, 0, 0)   # Stop sharing meets the right edge
        if not has and self.toggle.isChecked():
            self.toggle.setChecked(False)
        self._arrange(shared)
        self._show_drawer()
        self.lock.blockSignals(True)
        self.lock.setChecked(on and voice['latched'])
        self.lock.blockSignals(False)
        self.lock.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS /
                                    ('live_bar_lock_on.svg' if on and voice['latched'] else 'live_bar_lock.svg'))))
        self.call.setEnabled(available)
        self.call.setFixedWidth(56 if on else 46)        # in the call it holds the lock too
        self.call.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS / ('live_bar_talk.svg' if on else 'live_bar_join.svg'))))
        self.call.setToolTip(_('collab.voice_talk') if on else _('collab.voice_join'))
        state = 'off' if not on else ('talking' if voice['talking'] or voice['latched'] else 'call')
        if self.call.property('call') != state:
            self.call.setProperty('call', state)
            self.call.style().unpolish(self.call)
            self.call.style().polish(self.call)
        unread = collab_service.chat_unread() if shared else 0
        self.chat.setText(str(unread) if unread else '')
        if self.chat.property('unread') != bool(unread):  # something new: the icon and its count turn green
            from PySide6.QtGui import QIcon
            self.chat.setProperty('unread', bool(unread))
            self.chat.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS /
                                        ('live_bar_chat_new.svg' if unread else 'live_bar_chat.svg'))))
            self.chat.style().unpolish(self.chat)
            self.chat.style().polish(self.chat)
        self.chat.setFixedWidth(62 if unread else 50)
        self.chat_place.setFixedWidth(self.chat.width() - self.SLANT - self.controls.spacing())
        QTimer.singleShot(0, self._place_chat)
        if on and not self._speaking.isActive():
            self._speaking.start()
        elif not on:
            self._speaking.stop()
        self._render_faces()

    def _render_faces(self):
        if not collab_state.shared():
            self.faces.set_people([])
            self.people.setVisible(False)
            self.people.setChecked(False)
            return
        voice = collab_service.voice_state()
        if voice['on']:
            people = [((p['name'] or p['handle'] or '?')[:1].upper(), p['speaking']) for p in voice['people']]
        else:
            people = [((handle or '?')[:1].upper(), False) for handle in self.panel.live_card._here()]
        self.faces.set_people(people)
        self.people.setVisible(collab_state.shared() and (not people or self.people.isChecked()))

    # --- What it does ------------------------------------------------------------------------------------

    def enterEvent(self, event):
        super().enterEvent(event)
        self.leave.setVisible(collab_state.shared())

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.leave.setVisible(False)

    def _place_chat(self):
        """The chat tab over its place, reaching SLANT px under the call tab's slant, so their slants meet."""
        place = self.chat_place
        if not place.isVisible():
            return
        self.chat.setGeometry(place.x(), place.y(), self.chat.width(), 30)
        self.chat.raise_()
        self.call.raise_()

    def _build_windows(self):
        """The media's dialog and the pop-up of who it is shared with, holding their cards from the start: both
        windows of the live card's, so the cards stay among its widgets."""
        from PySide6.QtWidgets import QFrame
        from subtitld.interface import utils
        card = self.panel.live_card.media_card
        dialog = utils.SimpleDialog(self.panel.live_card, _('collab.media'))
        dialog.reject_button.setVisible(False)
        dialog.accept_button.clicked.connect(dialog.accept)
        card.header.setVisible(False)
        card.HEIGHT = 360
        dialog.content.layout().addWidget(card)
        dialog.setMinimumWidth(460)
        self._media_dialog = dialog
        # who it is shared with: a panel opening above the foot's line, in the line's colour while open, like
        # the dubbing panel's range (scope_selector.ScopeFooter.as_corner_tab); the people icon opens it
        card = self.panel.live_card.members_card
        self.people_panel = QWidget(self)
        self.people_panel.setObjectName('live_people_panel')
        box = QVBoxLayout(self.people_panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(card)
        self.people_panel.setVisible(False)
        self.layout().insertWidget(self.layout().indexOf(self.drawer) + 1, self.people_panel)

    def show_media(self):
        """The project's media (modules/collab_transfer.py), in a dialog: whether this computer shares its own,
        and one line a file."""
        card = self.panel.live_card.media_card
        card.header.setChecked(True)
        self.panel.live_card._render_media()
        card.fit()
        self._media_dialog.show()
        self._media_dialog.raise_()

    def show_people(self):
        """Who it is shared with (the owner adds and removes people), opened above the foot's line."""
        self.people.setChecked(True)

    def _open_people(self, opened):
        self.people_panel.setVisible(opened)
        if opened:
            self.panel.live_card.members_card.fit()
        self.update()

    def _call_pressed(self):
        if collab_service.in_call():
            collab_service.voice_talk(True)            # held: on the air
            self._redraw.start()

    def _call_released(self):
        if collab_service.in_call():
            collab_service.voice_talk(False)
            self._redraw.start()

    def _call_clicked(self):
        if not collab_service.in_call():               # out of the call, a click joins it
            self.panel.chat_card.toggle_call()

    def show_chat(self):
        self.chat.setChecked(True)
        self.drawer.ensureWidgetVisible(self.panel.chat_card.field)
        self.panel.chat_card.field.setFocus()

    def _arrange(self, shared):
        """Not shared and closed: one line, the status at the left of Share live. Otherwise the status line
        on top (over the open drawer), the buttons at the foot."""
        merged = not shared and not self.toggle.isChecked()
        layout = self.layout()
        if merged and self.controls.indexOf(self.status_line) < 0:
            layout.removeWidget(self.status_line)
            self.controls.insertWidget(0, self.status_line, 1, Qt.AlignBottom)
        elif not merged and layout.indexOf(self.status_line) < 0:
            self.controls.removeWidget(self.status_line)
            layout.insertWidget(0, self.status_line)
        self.status_line.show()
        self.controls.setContentsMargins(0, 0, 0, 0)    # the people button meets the bar's left edge
        # one line: Share live rises into the strip above the shade; otherwise the status line starts under it
        layout.setContentsMargins(0, 0 if merged else self.RISE, 0, 0)

    def eventFilter(self, watched, event):
        from PySide6.QtCore import QEvent
        if watched is getattr(self, 'chat_place', None) and event.type() in (QEvent.Move, QEvent.Resize, QEvent.Show):
            QTimer.singleShot(0, self._place_chat)
        if watched is getattr(self, 'drawer_content', None) and event.type() in (QEvent.LayoutRequest, QEvent.Resize):
            QTimer.singleShot(0, self._fit_drawer)
        return super().eventFilter(watched, event)

    def _fit_drawer(self):
        """As tall as the card's content at its width, up to DRAWER_HEIGHT (then it scrolls); the cards above
        give up the room."""
        if not self.drawer.isVisible():
            return
        card = self.drawer_content
        width = self.drawer.viewport().width()
        need = card.heightForWidth(width) if card.hasHeightForWidth() else card.sizeHint().height()
        height = min(max(need, 0), self.DRAWER_HEIGHT)
        if self.drawer.height() != height or self.drawer.minimumHeight() != height:
            self.drawer.setFixedHeight(height)

    @staticmethod
    def _holder(card):
        holder = QWidget()
        holder.setProperty('class', 'transparent_panel')
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(card)
        holder.setVisible(False)
        return holder

    def _show_drawer(self):
        """The drawer shows what its two switches ask for: the co-editing card (the toggle), the chat (the
        chat tab); hidden when neither."""
        live = self.toggle.isChecked() and self.panel.live_card.has_content()
        chat = self.chat.isChecked() and self.chat.isVisible()
        self.live_holder.setVisible(live)
        self.chat_holder.setVisible(chat)
        self.drawer.setVisible(live or chat)
        if live or chat:
            QTimer.singleShot(0, self._fit_drawer)

    def open_drawer(self, opened):
        from PySide6.QtGui import QIcon
        self.toggle.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS / ('live_bar_menu_open.svg' if opened else 'live_bar_menu.svg'))))
        self._show_drawer()
        self._arrange(collab_state.shared())
        if opened:
            self.panel.live_card.refresh()

    def open_chat(self, opened):
        self._show_drawer()
        if opened:
            collab_service.chat_mark_read()
            self.panel.chat_card.refresh()
            self.panel.chat_card._to_bottom()           # its latest message in view
            self.panel.chat_card.field.setFocus()

    def _show_in_drawer(self, widget):
        in_chat = self.panel.chat_card.isAncestorOf(widget) or widget is self.panel.chat_card
        if not in_chat:
            self.toggle.setVisible(True)                # the card has something to show now
        (self.chat if in_chat else self.toggle).setChecked(True)
        self._show_drawer()
        self.drawer.ensureWidgetVisible(widget)

    def start_sharing(self):
        """Share it under the title the card holds (the project's own, unless changed there). The drawer stays
        as it is: opening it here, only for it to close once shared (nothing left in it), made the bar jump."""
        card = self.panel.live_card
        card.refresh()
        if card.title_field.text().strip():
            card.share()

    def retranslate(self):
        self.lock.setToolTip(_('collab.voice_latch'))
        self.hang_up.setToolTip(_('collab.voice_leave'))
        self.chat.setToolTip(_('collab.chat_title'))
        self.toggle.setToolTip(_('collab.title'))
        self.share.setText(_('collab.share'))
        self._media_dialog.retranslate()
        self._media_dialog.set_title(_('collab.media'))
        self.refresh()
