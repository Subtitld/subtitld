"""Subtitle and title themes from subtitld.cc, as cards (modules/theme_store.py, its part for
modules.themes).

Drawn as the timeline's themes are (interface/timeline_theme_browser.py), from the same pieces: a section
for each kind, the subtitle themes' under the subtitle style's settings in the Interface panel's Video
subtitles tab, the title themes' in its Titles tab. Each holds, first, the themes installed here from
subtitld.cc: Use (or a quiet In use mark for the one drawn with: the project's subtitle style, or the
theme new titles get), an update when subtitld.cc has a newer version, the globe to its page and the trash
while the pointer is on the card. Then, under its head, the public themes on subtitld.cc not installed
here, popular, new or best rated, or by name, each with Install; an installed theme goes up among the
installed and into the pickers, to be chosen there or with Use.

A card's picture is the still subtitld.cc drew of the theme, 16:9 (the timeline's are 9:2); an animated
theme says so on the card's slanted label, where a timeline theme names its look.

Everything from subtitld.cc comes in the background (subtitldcc_service.call), once the section's head
first comes into sight; nothing needs signing in.
"""

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from subtitld.interface.timeline_theme_browser import ThemeBrowser, ThemeCard, failure_text
from subtitld.interface.translation import _
from subtitld.modules import theme_store
from subtitld.modules import subtitldcc_service as service

PREVIEW_RATIO = 9 / 16                  # subtitld.cc draws subtitle and title themes 16:9
HEADINGS = {'subtitle': 'html_theme_store.subtitle_title', 'title': 'html_theme_store.title_title'}
remote = {}                             # share_id -> what subtitld.cc said of it last
_pictures = {}                          # (path, mtime) -> QPixmap


def picture(theme_id):
    """The picture kept beside an installed theme, or None."""
    path = theme_store.preview_file(theme_id)
    if path is None:
        return None
    key = (str(path), path.stat().st_mtime)
    if key not in _pictures:
        _pictures[key] = QPixmap(str(path))
    return _pictures[key]


def remote_info(info):
    """A card's info for a theme subtitld.cc lists: to install, so nothing to update or remove yet."""
    return {**info, 'state': theme_store.file_state(info), 'look': None, 'counterpart': None,
            'mark': _('html_theme_store.animated') if info.get('animated') else '',
            'slots': list(info.get('slots') or []), 'options': False, 'removable': False, 'update': None}


def installed_info(theme):
    """A card's info for a theme installed here from subtitld.cc (a modules.themes.Theme)."""
    record = theme_store.file_record(theme.id)
    share_id = record.get('share_id') or theme_store.share_id_from(theme.id)
    listed = remote.get(share_id)
    version = record.get('version')
    return {
        'theme_id': theme.id,
        'share_id': share_id,
        'version': version,
        'title': theme.name,
        'owner': record.get('owner') or '',
        'author': theme.author,
        'mark': _('html_theme_store.animated') if theme.animated else '',
        'slots': [slot['label'] for slot in theme.slots] if theme.kind == 'title' else [],
        'url': listed['url'] if listed and listed.get('url') else service.site_url(f'/{share_id}'),
        'options': False,
        'removable': True,
        'state': 'in_use' if theme_store.file_in_use(theme.id) else 'use',
        'update': listed['version'] if listed and version and listed['version'] > version else None,
    }


def install_file(owner, card, share_id, version, done):
    """Fetch and install a subtitle or title theme (the version given, or its latest) in the background,
    with its picture; `done()` after, on the UI thread."""
    card.set_busy(_('theme_store.installing'))

    def fetch():
        file = theme_store.fetch_file(theme_store.theme_id(share_id), version)
        return file, theme_store.fetch_preview(share_id, file[1])

    def fetched(answer):
        file, preview = answer
        try:
            theme_store.save_file(*file, preview=preview)
        except theme_store.StoreError as error:
            owner.install_failed(card, error)
            return
        done()

    owner.calls.append(service.call(fetch, fetched, lambda error: owner.install_failed(card, error)))


class HtmlThemeBrowser(ThemeBrowser):
    """The subtitle (`kind` 'subtitle') or title ('title') themes installed here from subtitld.cc, then
    the ones subtitld.cc has that aren't, to install. `changed` after one was installed, removed or put in
    use: the pickers list the themes again; `used` after one was put in use (the project's subtitles, or
    new titles, are drawn with it). Loads the first time its head is in sight."""

    changed = Signal()
    used = Signal()
    known = remote

    def __init__(self, kind, parent=None):
        self.kind = kind
        self.mine = []                  # the installed themes' cards
        super().__init__(parent)
        self.setObjectName(f'{kind}_theme_store')
        # These themes are drawn for no look of the interface: no look to pick.
        self.look.hide()
        self.bar.layout().setStretch(1, 0)
        self.gallery.clicked.disconnect()
        self.gallery.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(service.site_url(f'/themes/{self.kind}'))))

        # The installed ones, above the head.
        self.installed_box = QWidget()
        self.installed_box.setObjectName(f'{kind}_theme_store_installed')
        box = QVBoxLayout(self.installed_box)
        box.setContentsMargins(10, 0, 10, 14)
        box.setSpacing(10)
        self.installed_label = QLabel()
        self.installed_label.setProperty('class', 'widget_label')
        box.addWidget(self.installed_label)
        self.mine_list = QVBoxLayout()
        self.mine_list.setContentsMargins(0, 0, 0, 0)
        self.mine_list.setSpacing(10)
        box.addLayout(self.mine_list)
        self.layout().insertWidget(0, self.installed_box)
        self.translate()

    def translate(self):
        super().translate()
        self.heading.setText(_(HEADINGS[self.kind]))
        if hasattr(self, 'installed_label'):
            self.installed_label.setText(_('html_theme_store.installed'))
            self.build_installed()

    # --- The installed ones ---------------------------------------------------------------------------

    def build_installed(self):
        """A card for each theme of this kind installed here from subtitld.cc, in the interface's
        language."""
        for card in self.mine:
            self.mine_list.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self.mine = []
        for theme in theme_store.installed_files(self.kind):
            card = ThemeCard(installed_info(theme), ratio=PREVIEW_RATIO)
            card.set_preview(picture(theme.id))
            card.act.connect(self._act_installed)
            self.mine_list.addWidget(card)
            self.mine.append(card)
        self.installed_box.setVisible(bool(self.mine))

    def _act_installed(self, info, action):
        if action == 'use':
            theme_store.use_file(info['theme_id'])
            self.used.emit()
        elif action == 'remove':
            theme_store.remove_file(info['theme_id'])
            if self.loaded and not self.share_id:
                self.reload()           # it is on subtitld.cc to install again
        elif action == 'update':
            card = next(card for card in self.mine if card.info['theme_id'] == info['theme_id'])
            install_file(self, card, info['share_id'], info['update'], self._installed)
            return
        self.build_installed()
        self.changed.emit()

    def _installed(self):
        self.build_installed()
        self.changed.emit()

    # --- What subtitld.cc lists ----------------------------------------------------------------------

    def _query(self, cursor):
        kind = self.kind
        sort = self.sort.combobox.currentData() or 'popular'
        text = self.search.text().strip()
        return lambda: theme_store.browse(kind, sort=sort, q=text, cursor=cursor)

    def _state(self, info):
        return theme_store.file_state(info)

    def _card(self, info):
        return ThemeCard(remote_info(info), ratio=PREVIEW_RATIO)

    def _remember(self, infos):
        super()._remember(infos)
        self.build_installed()          # the installed ones' cards now know of their updates

    def _act(self, info, action):
        card = next(card for card in self.cards if card.info['share_id'] == info['share_id'])
        if action == 'use':
            theme_store.use_file(theme_store.theme_id(info['share_id']))
            self.used.emit()
            self._changed()
        elif action in ('install', 'update'):
            install_file(self, card, info['share_id'], info.get('version'), self._changed)

    def install_failed(self, card, error):
        if card in self.mine:
            if not card.main.isEnabled():
                card.main.setEnabled(True)
                card.main.setText(_(f'theme_store.action_{card.state}'))
            self.status.setText(_('theme_store.install_failed').format(error=failure_text(error)))
            self.status.show()
            return
        super().install_failed(card, error)

    def _changed(self):
        """An install: its card goes up to the installed ones, and into the pickers."""
        super()._changed()
        self.build_installed()
        self.changed.emit()
