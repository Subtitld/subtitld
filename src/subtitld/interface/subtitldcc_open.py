"""Opening a project shared for live co-editing from a subtitld:// link (modules/subtitldcc_links.py): the
"Open in Subtitld" of the project's page on subtitld.cc, for someone with no file of it here.

On the start screen, where nothing is open, a card does what is needed, in place and in the background:
connect to subtitld.cc if Subtitld isn't, take the project (its subtitles come from subtitld.cc as the
connection catches up), and get its video from a collaborator who shares it, into a folder the person can
change, or use a copy they choose. Once the video is here the editor opens with it, still co-editing; the
waveform, the separated audio and the dub clips come as they do for anyone in the project (the Live
co-editing card).

With a project open in the editor, the subtitld.cc panel asks first, as its list of projects shared with you
does (LiveCard.open_link).
"""

import copy
import html
import os
import urllib.parse

from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QLabel, QProgressBar, QSizePolicy, QVBoxLayout, QWidget

from subtitld.interface.subtitldcc_live import _button, _row, _size, _text
from subtitld.interface.translation import _
from subtitld.modules import collab_media, collab_service, collab_state, session
from subtitld.modules import subtitldcc_links as links
from subtitld.modules import subtitldcc_service as service

TAB = 'subtitldcc'


def load(window):
    """The card, on the start screen above the recent files (hidden until a link comes)."""
    window.open_link_card = OpenCard(window)
    layout = window.start_screen.layout()
    layout.insertWidget(1, window.open_link_card, 0, Qt.AlignHCenter)
    layout.insertStretch(2)


def editor_shown(window):
    splitter = getattr(window, 'main_vertical_splitter', None)
    central = getattr(window, 'central_widget', None)
    return splitter is not None and central is not None and central.layout().currentWidget() is splitter


def open_link(window, text):
    """Follow a subtitld:// link: from the command line, from another Subtitld it was given to, or from
    macOS."""
    from subtitld.interface import left_panel

    if window.isMinimized():
        window.showMaximized()
    window.raise_()
    window.activateWindow()
    link = links.parse(text)
    problem = None
    if link is None:
        problem = _('open_link.not_a_link')
    elif not links.for_site(link, service.base_url()):
        problem = _('open_link.other_site').format(
            site=html.escape(link['site']), ours=html.escape(urllib.parse.urlsplit(service.base_url()).netloc))
    if editor_shown(window):
        left_panel.show_panel(window, TAB)
        if problem:
            window.subtitldcc_panel.show_message(problem, error=True)
        else:
            window.subtitldcc_panel.live_card.open_link(link['project'])
        return
    window.open_link_card.start(None if problem else link['project'], problem)


def _video_extensions():
    return [extension for kind in session.LIST_OF_SUPPORTED_VIDEO_EXTENSIONS.values() for extension in kind['extensions']]


class OpenCard(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.setObjectName('subtitldcc_card')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(460)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(8)

        self._doc = None          # the project the link names
        self._project = None      # what subtitld.cc says of it
        self._state = None        # 'problem', 'sign_in', 'loading', 'joined'
        self._problem = ''
        self._signing_in = None   # the sign-in call, while it runs
        self._cancel_sign_in = False
        self._asked = set()       # videos asked for from the collaborators, by hash
        self._video_error = ''
        self._opening = False

        self.kicker = _text('subtitldcc_kicker')
        layout.addWidget(self.kicker)
        self.title = _text('subtitldcc_card_title')
        layout.addWidget(self.title)
        self.status = _text()
        self.status.setTextFormat(Qt.RichText)
        layout.addWidget(self.status)
        self.video = _text()
        layout.addWidget(self.video)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        layout.addWidget(self.progress)
        self.folder = _text()
        self.folder_button = _button()
        self.folder_button.clicked.connect(self.choose_folder)
        self.folder_row = _row(self.folder, self.folder_button)
        self.folder_row.layout().setStretch(0, 1)
        layout.addWidget(self.folder_row)
        self.error = _text('subtitldcc_error')
        layout.addWidget(self.error)

        self.primary = _button(primary=True, small=False)
        self.primary.clicked.connect(lambda: self._primary_action and self._primary_action())
        self.secondary = _button(small=False)
        self.secondary.clicked.connect(lambda: self._secondary_action and self._secondary_action())
        self.cancel_button = _button(small=False)
        self.cancel_button.clicked.connect(self.cancel)
        layout.addWidget(_row(self.primary, self.secondary, self.cancel_button))
        self._primary_action = None
        self._secondary_action = None

        # Received bytes come many times a second: the card follows at most this often.
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(250)
        self._render_timer.timeout.connect(self._follow)
        collab_service.listeners.append(self._news)
        self.hide()
        self.retranslate()

    # --- What it is asked -----------------------------------------------------------------------------------

    def start(self, doc_id, problem=None):
        """Open shared project `doc_id` (or say what's wrong with the link)."""
        if doc_id and doc_id == self._doc and self._state in ('loading', 'joined'):
            self.show()
            return
        if self._state == 'joined':
            collab_service.drop()
        self._doc, self._project, self._problem = doc_id, None, problem or ''
        self._asked.clear()
        self._video_error = ''
        self._opening = False
        self.show()
        if problem:
            self._state = 'problem'
            self.render()
            return
        self._load()

    def _load(self):
        if not service.is_connected():
            self._state = 'sign_in'
            self.render()
            return
        self._state = 'loading'
        self.render()
        doc_id = self._doc
        collab_service.project(lambda project: self._loaded(doc_id, project),
                               lambda error: self._load_failed(doc_id, error), doc_id=doc_id)

    def _loaded(self, doc_id, project):
        if doc_id != self._doc or self._state != 'loading':
            return  # given up, or another link came meanwhile
        self._project = project
        self._state = 'joined'
        collab_service.take(project)
        self._follow()

    def _load_failed(self, doc_id, error):
        if doc_id != self._doc or self._state != 'loading':
            return
        code = service.error_code(error)
        if code == 'unauthorized':
            service.forget_account()
            self._state, self._problem = 'sign_in', ''
        elif code == 'missing_scope':
            self._state, self._problem = 'sign_in', _('collab.sign_in_again_text')
        elif code == 'not_found':
            self._state, self._problem = 'problem', _('open_link.not_found')
        else:
            self._state, self._problem = 'problem', service.error_message(error, _)
        self.render()

    # --- Connecting to subtitld.cc ----------------------------------------------------------------------

    def sign_in(self):
        if service.running(self._signing_in):
            return
        self._cancel_sign_in = False
        self._signing_in = service.call(
            lambda: service.sign_in(None, cancelled=lambda: self._cancel_sign_in), self._signed_in, self._sign_in_failed)
        self.render()

    def _signed_in(self, me):
        self._signing_in = None
        service.remember_account(me)
        self._problem = ''
        if self._doc:
            self._load()

    def _sign_in_failed(self, error):
        self._signing_in = None
        if service.error_code(error) != 'canceled':
            self._problem = service.error_message(error, _)
        self.render()

    def cancel_sign_in(self):
        self._cancel_sign_in = True

    # --- The project, live --------------------------------------------------------------------------------

    def _news(self, kind):
        if self._state != 'joined' or self._opening:
            return
        if kind == 'failed':
            failure = collab_service.failure()
            if failure == 'not_found':
                collab_service.drop()
                self._state, self._problem = 'problem', _('open_link.not_found')
            elif failure in ('missing_scope', 'sign_in_required'):
                collab_service.drop()
                self._state = 'sign_in'
                self._problem = _('collab.sign_in_again_text') if failure == 'missing_scope' else ''
            self.render()
            return
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _video_row(self):
        return next((row for row in collab_service.media_status() if row['kind'] == 'video'), None)

    def _follow(self):
        """Get the video as soon as a collaborator who shares it is online; open the editor once it is here."""
        if self._state != 'joined' or self._opening:
            return
        row = self._video_row()
        if row is not None and row['state'] == 'here' and row['path']:
            self._open_editor(row['path'])
            return
        if (row is not None and row['state'] == 'available' and row['hash'] not in self._asked
                and collab_service.transfers_available()):
            self._get(row)
        self.render()

    def _folder(self):
        folder = service.settings().get('media_folder')
        if folder and os.path.isdir(folder):
            return folder
        return QStandardPaths.writableLocation(QStandardPaths.MoviesLocation) or os.path.expanduser('~')

    def _get(self, row):
        self._asked.add(row['hash'])
        self._video_error = ''
        destination = collab_media.free_path(self._folder(), row['name'])
        collab_service.download_media(row['hash'], destination, lambda error, h=row['hash']: self._get_failed(h, error))

    def _get_failed(self, file_hash, error):
        if isinstance(error, OSError) and str(error) == 'not enough room':
            self._video_error = _('collab.media_no_room')
        else:
            self._video_error = service.error_message(error, _)
        self.render()

    def try_again(self):
        self._asked.clear()
        self._video_error = ''
        self._follow()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, _('collab.media_where'), self._folder())
        if not folder:
            return
        service.settings()['media_folder'] = folder
        row = self._video_row()
        if row is not None and row['state'] in ('downloading', 'checking'):
            # Into the new folder, from the start.
            collab_service.cancel_download(row['hash'])
            self._asked.discard(row['hash'])
        self._follow()

    def choose_video(self):
        start = self._folder()
        extensions = ' '.join(f'*.{extension}' for extension in _video_extensions())
        path = QFileDialog.getOpenFileName(self, _('open_link.choose_video'), start,
                                           f"{_('file_io.video_files')} ({extensions})")[0]
        if not path:
            return
        row = self._video_row()
        if row is not None and row['state'] in ('downloading', 'checking'):
            collab_service.cancel_download(row['hash'])
        self._open_editor(path)

    def _open_editor(self, path):
        """The video is here: the editor opens with it and the project, which stays shared and connected."""
        from subtitld.interface import left_panel, startscreen

        self._opening = True
        self._state = None
        self.hide()
        session.VIDEO = {'filepath': path}
        session.SUBTITLE['filepath'] = ''
        # Opening the editor starts the project anew: it is still this shared project.
        startscreen.load_productionscreen(self.window_, collab=copy.deepcopy(collab_state.STATE))
        QTimer.singleShot(0, lambda: left_panel.show_panel(self.window_, TAB))

    def cancel(self):
        """Give up: nothing stays open."""
        if self._signing_in is not None:
            self.cancel_sign_in()
        if self._state == 'joined':
            row = self._video_row()
            if row is not None and row['state'] in ('downloading', 'checking'):
                collab_service.cancel_download(row['hash'])
            collab_service.drop()
        self._doc, self._project, self._state = None, None, None
        self.hide()

    # --- What it shows ------------------------------------------------------------------------------------------

    def _actions(self, primary=None, secondary=None, cancel=True):
        for button, action in ((self.primary, primary), (self.secondary, secondary)):
            button.setVisible(action is not None)
            if action is not None:
                button.setText(action[0])
        self._primary_action = primary[1] if primary else None
        self._secondary_action = secondary[1] if secondary else None
        self.cancel_button.setVisible(cancel)

    def render(self):
        project = self._project or {}
        self.title.setText(html.escape(project.get('title') or '') or _('open_link.title'))
        self.video.hide()
        self.progress.hide()
        self.folder_row.hide()
        self.error.setVisible(bool(self._problem) and self._state != 'problem')
        self.error.setText(self._problem)
        state = self._state
        if state == 'problem':
            self.status.setText(self._problem)
            self._actions(secondary=(_('open_link.try_again'), self._load) if self._doc else None)
        elif state == 'sign_in':
            waiting = self._signing_in is not None
            self.status.setText(_('subtitldcc.browser_wait') if waiting else _('open_link.connect_text'))
            if waiting:
                self._actions(primary=(_('subtitldcc.cancel'), self.cancel_sign_in), cancel=False)
            else:
                self._actions(primary=(_('subtitldcc.connect'), self.sign_in))
        elif state == 'loading':
            self.status.setText(_('open_link.loading'))
            self._actions()
        elif state == 'joined':
            self._render_joined(project)

    def _render_joined(self, project):
        owner = (project.get('owner') or {}).get('handle', '')
        connection_state = collab_service.state()
        if connection_state == 'live':
            subtitles = _('open_link.subtitles').format(count=len(session.SUBTITLE.get('segments') or []))
        elif connection_state == 'joining':
            subtitles = _('open_link.subtitles_coming')
        else:
            subtitles = _('open_link.offline')
        self.status.setText(_('open_link.shared_by').format(handle=html.escape(owner)) + '<br>' + subtitles)

        row = self._video_row()
        choose = (_('open_link.choose_video'), self.choose_video)
        self.video.show()
        if self._video_error and row is not None:
            self.video.setText(_('open_link.video_failed').format(name=html.escape(row['name'])) + ' ' + self._video_error)
            self._actions(primary=(_('open_link.try_again'), self.try_again), secondary=choose)
            self.folder_row.show()
        elif row is None:
            listed = collab_service.media_listed()
            self.video.setText(_('open_link.video_none') if listed else _('open_link.video_looking'))
            self.video.setVisible(connection_state == 'live')
            self._actions(secondary=choose)
        elif not collab_service.transfers_available():
            self.video.setText(_('open_link.video_no_links'))
            self._actions(primary=choose)
        elif row['state'] in ('downloading', 'checking'):
            key = 'open_link.video_checking' if row['state'] == 'checking' else 'open_link.video_receiving'
            self.video.setText(_(key).format(name=html.escape(row['name']), size=_size(row['size']),
                                             percent=int(row['fraction'] * 100)))
            self.progress.setValue(int(row['fraction'] * 1000))
            self.progress.show()
            self.folder_row.show()
            self._actions(secondary=choose)
        else:  # no one who shares it is online
            self.video.setText(_('open_link.video_waiting').format(name=html.escape(row['name']), size=_size(row['size'])))
            self.folder_row.show()
            self._actions(secondary=choose)
        self.folder.setText(_('open_link.folder').format(folder=html.escape(self._folder())))

    def retranslate(self):
        self.kicker.setText(_('collab.title'))
        self.folder_button.setText(_('open_link.change_folder'))
        self.cancel_button.setText(_('subtitldcc.cancel'))
        if not self.isHidden():
            self.render()
