"""Opening a video from the internet, from the start screen's "Open a link".

A card takes the link and does what it needs, in place: a link straight to a video file is downloaded
(modules/media_links.py); a link to a web page (YouTube, Vimeo, and the many other websites yt-dlp knows) needs
an add-on for `media.download`, which the card offers to install when there is none, then uses. The video goes
into a folder the person can change (their Videos folder at first), and the editor opens with it, as when a
video file is opened.

A subtitld:// link typed in opens as one clicked would (interface/subtitldcc_open.py).
"""

import html

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QProgressBar, QSizePolicy, QVBoxLayout, QWidget

from subtitld.interface import utils
from subtitld.interface.subtitldcc_live import _button, _row, _size, _text
from subtitld.interface.translation import _
from subtitld.modules import media_links
from subtitld.modules.addons import installer, protocol
from subtitld.modules.addons.manager import get_manager
from subtitld.modules.addons.provider import TASK_MEDIA_DOWNLOAD
from subtitld.modules.subtitldcc_links import is_link as is_subtitld_link


def load(window):
    """The card, on the start screen above the recent files (hidden until "Open a link")."""
    window.open_url_card = OpenUrlCard(window)
    layout = window.start_screen.layout()
    layout.insertWidget(1, window.open_url_card, 0, Qt.AlignHCenter)


def show(window):
    window.open_url_card.begin()


class _Job(QThread):
    """`function(progress, cancelled)` off the UI thread; `progress(*values)` and the outcome arrive on it."""

    moved = Signal(object)
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, function):
        super().__init__()
        self._function = function
        self.stopped = False

    def run(self):
        try:
            result = self._function(lambda *values: self.moved.emit(values), lambda: self.stopped)
        except BaseException as error:  # said to the person by the card
            self.failed.emit(error)
        else:
            self.succeeded.emit(result)


# Jobs still running, the card's or ones it gave up on: a QThread let go of while it runs would crash.
_running = set()
_waits_at_quit = []


def _keep(job):
    if not _waits_at_quit:
        QApplication.instance().aboutToQuit.connect(_wait_for_jobs)
        _waits_at_quit.append(True)
    _running.add(job)
    job.finished.connect(lambda: _running.discard(job))


def _wait_for_jobs():
    for job in list(_running):
        job.stopped = True
        job.wait(3000)


def _provider():
    providers = get_manager().providers_for_task(TASK_MEDIA_DOWNLOAD)
    return providers[0] if providers else None


def _catalog_entry():
    """The add-on to install for web pages: one in the catalog for `media.download` this Subtitld can use."""
    catalog = installer.fetch_catalog()
    for entry in catalog.get('addons', []):
        if (entry.get('tasks') or [None])[0] == TASK_MEDIA_DOWNLOAD:
            usable = installer.compatible_entry(entry)
            if usable is not None:
                return usable
    return None


def _error_text(error):
    reason = getattr(error, 'reason', None)
    return str(reason if reason is not None else error) or error.__class__.__name__


class OpenUrlCard(QWidget):
    """On the start screen, drawn as a dialog (utils.build_dialog_chrome) without being one: the title, the
    close notch and the footer with Cancel and the tab of the action at hand."""

    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.setObjectName('open_url_card')
        self.setFixedWidth(460)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)
        utils.build_dialog_chrome(self, '', self.cancel)
        layout = self.content.layout()
        layout.setSpacing(8)

        self._state = None     # 'enter', 'looking', 'file', 'need_addon', 'no_addon', 'installing', 'page', 'failed'
        self._url = ''
        self._file = None      # what media_links.probe found: the file at the link
        self._entry = None     # the add-on to install, from the catalog
        self._job = None
        self._provider = None  # the add-on getting the video, while it does
        self._problem = ''
        self._detail = ''

        self.field = utils.LabeledLineEdit()
        self.field.lineedit.returnPressed.connect(self.open)
        layout.addWidget(self.field)
        self.status = _text()
        self.status.setTextFormat(Qt.RichText)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        layout.addWidget(self.progress)
        self.detail = _text()
        layout.addWidget(self.detail)
        self.folder = _text()
        self.folder_button = _button()
        self.folder_button.clicked.connect(self.choose_folder)
        self.folder_row = _row(self.folder, self.folder_button)
        self.folder_row.layout().setStretch(0, 1)
        layout.addWidget(self.folder_row)
        self.error = _text('subtitldcc_error')
        layout.addWidget(self.error)

        # The footer's buttons: the action at hand on the tab, Cancel on the left.
        self.primary = self.accept_button
        self.primary.clicked.connect(lambda: self._primary_action and self._primary_action())
        self.cancel_button = self.reject_button
        self.cancel_button.clicked.connect(self.cancel)
        self._primary_action = None

        self.hide()
        self.retranslate()

    # --- What it is asked -----------------------------------------------------------------------------------

    def begin(self):
        """Ask for a link (the one being opened stays, if one is)."""
        if self._state not in (None, 'failed', 'no_addon'):
            self.show()
            return
        if self._state is None:
            self.field.setText('')
        self._state, self._problem = 'enter', ''
        self.show()
        self.render()
        self.field.lineedit.setFocus()
        self.field.lineedit.selectAll()

    def open(self):
        text = self.field.text().strip()
        if is_subtitld_link(text):
            from subtitld.interface import subtitldcc_open

            self.cancel()
            subtitldcc_open.open_link(self.window_, text)
            return
        url = media_links.parse(text)
        if url is None:
            self._state, self._problem = 'enter', _('open_url.not_a_link')
            self.render()
            return
        self._url, self._file, self._problem, self._detail = url, None, '', ''
        self._state = 'looking'
        self.render()
        self._start(lambda progress, cancelled: media_links.probe(url), self._probed, self._failed)

    def _start(self, function, on_success, on_failure, on_progress=None):
        self._stop_job()
        job = self._job = _Job(function)
        job.succeeded.connect(lambda result: job is self._job and on_success(result))
        job.failed.connect(lambda error: job is self._job and on_failure(error))
        if on_progress is not None:
            job.moved.connect(lambda values: job is self._job and on_progress(*values))
        _keep(job)
        job.start()

    def _stop_job(self):
        if self._job is not None:
            self._job.stopped = True
            self._job = None

    def _probed(self, found):
        kind, info = found
        self._job = None
        if kind == 'file':
            self._download_file(info)
        else:
            self._url = info['url']
            self._use_addon()

    # --- A video file ------------------------------------------------------------------------------------

    def _download_file(self, file):
        self._file = file
        self._state = 'file'
        self.progress.setRange(0, 1000 if file.get('size') else 0)
        self.render()
        folder = media_links.folder()
        self._start(lambda progress, cancelled: media_links.download(file, folder, progress, cancelled),
                    self._open_editor, self._failed, self._file_moved)

    def _file_moved(self, done, total):
        if total:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(done / total * 1000))
            self.detail.setText(_('open_url.received').format(done=_size(done), total=_size(total)))
        else:
            self.detail.setText(_size(done))
        if self.detail.isHidden():
            self.detail.show()
            self._fit()

    # --- A web page: the add-on ----------------------------------------------------------------------------------

    def _use_addon(self):
        provider = _provider()
        if provider is None:
            self._state = 'looking'
            self.render()
            self._start(lambda progress, cancelled: _catalog_entry(), self._found_entry, self._no_catalog)
            return
        self._provider = provider
        provider.progress.connect(self._page_moved)
        provider.finished.connect(self._page_done)
        provider.error.connect(self._page_failed)
        self._state, self._detail = 'page', ''
        self.progress.setRange(0, 0)
        self.render()
        provider.download(self._url, media_links.folder())

    def _found_entry(self, entry):
        self._job = None
        self._entry = entry
        self._state = 'need_addon' if entry is not None else 'no_addon'
        self.render()

    def _no_catalog(self, error):
        self._job = None
        self._entry = None
        self._state, self._problem = 'no_addon', _error_text(error)
        self.render()

    def install(self):
        entry = self._entry
        if entry is None:
            return
        self._state, self._problem, self._detail = 'installing', '', ''
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.render()
        self._start(lambda progress, cancelled: installer.download_and_install(entry, on_progress=progress),
                    self._installed, self._install_failed, self._install_moved)

    def _install_moved(self, value, message):
        self.progress.setValue(int(value * 1000))

    def _installed(self, addon_id):
        self._job = None
        get_manager().discover()
        if _provider() is None:
            self._state, self._problem = 'failed', _('open_url.install_failed').format(error=addon_id)
            self.render()
            return
        self._use_addon()

    def _install_failed(self, error):
        self._job = None
        self._state = 'failed'
        self._problem = _('open_url.install_failed').format(error=html.escape(_error_text(error)))
        self.render()

    def _release_provider(self):
        provider, self._provider = self._provider, None
        if provider is None:
            return
        for signal, slot in ((provider.progress, self._page_moved), (provider.finished, self._page_done),
                             (provider.error, self._page_failed)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        return provider

    def _page_moved(self, value, message):
        if value >= 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(value * 1000))
        else:
            self.progress.setRange(0, 0)
        if not message and value >= 0:
            message = f'{int(value * 100)} %'
        shown = self.detail.isVisible()
        self._detail = message
        self.detail.setText(html.escape(message))
        self.detail.setVisible(bool(message))
        if shown != bool(message):
            self._fit()

    def _page_done(self, path, info):
        self._release_provider()
        self._open_editor(path)

    def _page_failed(self, code, message):
        provider = self._release_provider()
        if code == protocol.ErrorCode.CANCELLED:
            return
        name = html.escape(provider.display_name if provider is not None else '')
        if code == protocol.ErrorCode.UNSUPPORTED_URL:
            self._problem = _('open_url.unsupported').format(addon=name)
        else:
            self._problem = _('open_url.failed').format(error=html.escape(message))
        self._state = 'failed'
        self.render()

    # --- Done, or given up ------------------------------------------------------------------------------------

    def _failed(self, error):
        self._job = None
        if isinstance(error, media_links.Cancelled):
            return
        key = 'open_url.unreachable' if self._state == 'looking' else 'open_url.failed'
        self._state, self._problem = 'failed', _(key).format(error=html.escape(_error_text(error)))
        self.render()

    def _open_editor(self, path):
        """The video is here: the editor opens with it, as for a video file opened."""
        from subtitld.interface import startscreen

        self._job = None
        self._state = None
        self.hide()
        startscreen.open_files(self.window_, [path], ask=False)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, _('open_url.folder_title'), media_links.folder())
        if folder:
            media_links.set_folder(folder)
            self.render()

    def cancel(self):
        """Give up: nothing is downloaded (what was is removed), the add-on stays if it was installed."""
        self._stop_job()
        provider = self._release_provider()
        if provider is not None:
            provider.cancel()
        self._state, self._problem = None, ''
        self.hide()

    # --- What it shows ------------------------------------------------------------------------------------------

    def _actions(self, primary=None, cancel=True):
        self.primary.setVisible(primary is not None)
        self.bottom_right.setVisible(primary is not None)
        if primary is not None:
            self.primary.setText(primary[0])
        self._primary_action = primary[1] if primary else None
        self.cancel_button.setVisible(cancel)

    def render(self):
        state = self._state
        typing = self.field.lineedit.hasFocus()
        self.field.setVisible(state in ('enter', 'failed', 'no_addon'))
        if typing and self.field.isHidden():
            # Not to the next button in line, Cancel, where the next Enter would give up the download.
            self.setFocus()
        self.status.setVisible(state != 'failed')
        self.progress.setVisible(state in ('file', 'installing', 'page'))
        self.detail.setVisible(state in ('file', 'page') and bool(self.detail.text()))
        if state not in ('file', 'page'):
            self.detail.setText('')
        self.folder_row.setVisible(state in ('enter', 'failed', 'no_addon', 'file', 'page', 'need_addon'))
        self.folder_button.setVisible(state in ('enter', 'failed', 'no_addon', 'need_addon'))
        self.folder.setText(_('open_url.folder').format(folder=html.escape(media_links.folder())))
        self.error.setVisible(bool(self._problem) and state in ('enter', 'failed', 'no_addon'))
        self.error.setText(self._problem)
        again = (_('open_url.open'), self.open)
        if state == 'enter':
            self.status.setText(_('open_url.hint'))
            self._actions(primary=again)
        elif state == 'looking':
            self.status.setText(_('open_url.looking'))
            self._actions()
        elif state == 'file':
            name = html.escape((self._file or {}).get('name') or '')
            self.status.setText(_('open_url.downloading').format(name=name))
            self._actions()
        elif state == 'need_addon':
            entry = self._entry or {}
            name = html.escape(entry.get('display_name') or entry.get('id') or '')
            self.status.setText(_('open_url.page_needs_addon').format(addon=f'<b>{name}</b>'))
            self._actions(primary=(_('open_url.install'), self.install))
        elif state == 'no_addon':
            self.status.setText(_('open_url.no_addon'))
            self._actions(primary=(_('open_url.try_again'), self.open))
        elif state == 'installing':
            name = html.escape((self._entry or {}).get('display_name') or '')
            self.status.setText(_('open_url.installing').format(addon=name))
            self._actions()
        elif state == 'page':
            name = html.escape(self._provider.display_name if self._provider is not None else '')
            self.status.setText(_('open_url.getting').format(addon=name))
            self._actions()
        elif state == 'failed':
            self._actions(primary=(_('open_url.try_again'), self.open))
        self._fit()

    def _fit(self):
        """As tall as its texts need at its width: centred on the start screen, the card gets its size hint,
        which doesn't follow wrapped text."""
        layout = self.layout()
        layout.activate()
        height = layout.totalHeightForWidth(self.width()) if layout.hasHeightForWidth() else layout.sizeHint().height()
        self.setFixedHeight(max(height, layout.minimumSize().height()))

    def retranslate(self):
        self.title_line.label.setText(_('open_url.title'))
        self.primary.setText(_('open_url.open'))   # render() names the action at hand; this one while there is none
        self.field.setLabel(_('open_url.field'))
        self.field.lineedit.setPlaceholderText('https://')
        self.folder_button.setText(_('open_url.change_folder'))
        self.cancel_button.setText(_('open_url.cancel'))
        if not self.isHidden():
            self.render()
