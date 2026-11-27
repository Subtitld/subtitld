"""The themes a project draws with that are not here (modules.themes.missing), said once it is open: the
ones from subtitld.cc to download. Those are named as subtitld.cc names them, asked in the background as the
dialog opens (by its id until the answer comes); one subtitld.cc no longer has is said so, and not offered.
Until they are here, Subtitld's own draw its subtitles and titles, and the project keeps naming them.
"""

from PySide6.QtWidgets import QLabel

from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import session, theme_store, themes
from subtitld.modules import subtitldcc_service as service


def offer(window, done=None):
    """Say which of the open project's themes are missing, if any; `done()` after any was installed."""
    missing = themes.missing(session.SUBTITLE.segments, session.SUBTITLE.theme)
    if missing:
        MissingThemesDialog(window, list(missing), done).exec()


class MissingThemesDialog(utils.SimpleDialog):
    def __init__(self, parent, theme_ids, done=None):
        super().__init__(parent, title=_('missing_themes.title'))
        self.setObjectName('missing_themes_dialog')
        self.done_callback = done
        self.calls = []
        self.downloadable = [theme_id for theme_id in theme_ids if theme_store.share_id_from(theme_id)]
        self.installed = []
        self.lines = {}
        layout = self.content.layout()
        message = QLabel(_('missing_themes.message'))
        message.setWordWrap(True)
        layout.addWidget(message)
        for theme_id in theme_ids:
            where = 'missing_themes.on_subtitldcc' if theme_id in self.downloadable else 'missing_themes.elsewhere'
            line = QLabel(_(where).format(theme=theme_id))
            line.setWordWrap(True)
            line.setProperty('class', 'dialog_detail')
            layout.addWidget(line)
            self.lines[theme_id] = line
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName('missing_themes_status')
        self.status.hide()
        layout.addWidget(self.status)
        self.accept_button.clicked.disconnect()
        if self.downloadable:
            self.accept_button.setText(_('missing_themes.download'))
            self.accept_button.clicked.connect(self.download)
            self.reject_button.setText(_('missing_themes.not_now'))
        else:
            self.accept_button.clicked.connect(self.accept)
            self.reject_button.hide()
        for theme_id in self.downloadable:
            self._ask_about(theme_id)

    def _fit(self):
        """Grow to the text it holds now (a name came, a status line appeared)."""
        if self.isVisible():
            self._prepare_text_content()

    def _ask_about(self, theme_id):
        """What subtitld.cc calls a theme, and who published it, in place of its id."""
        share_id = theme_store.share_id_from(theme_id)

        def answered(info):
            owner = info.get('owner')
            text = _('missing_themes.named_by') if owner else _('missing_themes.named')
            self.lines[theme_id].setText(text.format(name=info.get('title') or theme_id, owner=owner or ''))
            self._fit()

        def failed(error):
            if service.error_code(error) not in ('not_found', 'taken_down'):
                return              # offline, or the site is down: the id stays, Download may still work later
            self.lines[theme_id].setText(_('missing_themes.gone').format(theme=theme_id))
            if theme_id in self.downloadable:
                self.downloadable.remove(theme_id)
            if not self.downloadable and self.accept_button.isEnabled():
                self.accept_button.setText(_('missing_themes.close'))
                self.accept_button.clicked.disconnect()
                self.accept_button.clicked.connect(self.accept)
                self.reject_button.hide()
            self._fit()

        self.calls.append(service.call(lambda: service.client().theme(share_id), answered, failed))

    def download(self):
        """Each one, one after the other, in the background."""
        self.accept_button.setEnabled(False)
        self.status.setText(_('missing_themes.downloading'))
        self.status.show()
        self._fit()
        self._next(list(self.downloadable), [])

    def _next(self, left, failed):
        if not left:
            self._finished(failed)
            return
        theme_id = left.pop(0)

        def fetched(file):
            try:
                theme_store.save_file(*file)
            except theme_store.StoreError as error:
                failed.append((theme_id, str(error)))
            else:
                self.installed.append(theme_id)
            self._next(left, failed)

        def failure(error):
            failed.append((theme_id, service.error_message(error, _) or str(error)))
            self._next(left, failed)

        self.calls.append(service.call(lambda: theme_store.fetch_file(theme_id), fetched, failure))

    def _finished(self, failed):
        if self.installed and self.done_callback is not None:
            self.done_callback()
        if not failed:
            self.accept()
            return
        self.status.setText('\n'.join(_('missing_themes.failed').format(theme=theme_id, error=error)
                                      for theme_id, error in failed))
        self.accept_button.setText(_('missing_themes.close'))
        self.accept_button.setEnabled(True)
        self.accept_button.clicked.disconnect()
        self.accept_button.clicked.connect(self.accept)
        self.reject_button.hide()
        self._fit()
