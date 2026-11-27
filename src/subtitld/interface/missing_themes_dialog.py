"""The themes a project draws with that are not here (modules.themes.missing), said once it is open: the
ones from subtitld.cc to download, by their ids. Until they are here, Subtitld's own draw its subtitles
and titles, and the project keeps naming them.
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

    def download(self):
        """Each one, one after the other, in the background."""
        self.accept_button.setEnabled(False)
        self.status.setText(_('missing_themes.downloading'))
        self.status.show()
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
