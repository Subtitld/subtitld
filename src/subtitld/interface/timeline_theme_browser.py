"""Timeline themes from subtitld.cc, browsed and installed in Subtitld (modules/theme_store.py).

The dialog lists the public themes there (by look, popular, new or best rated, or by name), each as a card:
the preview subtitld.cc drew, the name, who made it, its look and whether it comes with a counterpart for the
other look. Its button installs it (with its counterpart) and puts it on the timeline, updates it when
subtitld.cc has a newer version, or puts an installed one on the timeline; an installed one can be removed.

A subtitld:// theme link (subtitldcc_open.open_link) opens the same dialog on that one theme.

Everything from subtitld.cc comes in the background (subtitldcc_service.call); nothing needs signing in.
"""

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from subtitld.interface import shapes, theme, utils
from subtitld.interface.translation import _
from subtitld.modules import subtitldcc_service as service
from subtitld.modules import theme_store

COLUMNS = 2
PREVIEW_WIDTH = 360                     # a card's preview, at 9:2 like the ones subtitld.cc draws
PREVIEW_HEIGHT = 80
LOOKS = ('', 'dark', 'light')
SORTS = ('popular', 'new', 'top')
_previews = {}                          # (share_id, version) -> QPixmap, for as long as Subtitld runs


class ThemeCard(QFrame):
    """One theme subtitld.cc lists. `act(info, action)`: 'install', 'update', 'use' or 'remove'."""

    act = Signal(dict, str)

    RADIUS = 4
    COLOUR = '#2c3d4b'
    IN_USE = '#33475a'

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self.state = theme_store.state(info)
        self.setProperty('class', 'theme_card')
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.preview = QLabel()
        self.preview.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setAccessibleName(_('theme_store.preview_of').format(name=info['title']))
        layout.addWidget(self.preview, 0, Qt.AlignHCenter)

        title = QLabel(info['title'])
        title.setProperty('class', 'addon_card_title')
        layout.addWidget(title)
        about = [f"@{info['owner']}" if info.get('owner') else '', _(f"theme_store.look_{info['look']}")]
        if info.get('counterpart'):
            about.append(_('theme_store.both_looks'))
        if info.get('rating'):
            about.append(f"★ {info['rating']['average']:.1f}")
        about.append(_('theme_store.downloads').format(count=info.get('downloads', 0)))
        meta = QLabel(' · '.join(part for part in about if part))
        meta.setProperty('class', 'addon_card_aside')
        meta.setWordWrap(True)
        layout.addWidget(meta)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 2, 0, 0)
        buttons.setSpacing(6)
        page = QPushButton(_('theme_store.on_site'))
        page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(info['url'])))
        buttons.addWidget(page)
        buttons.addStretch()
        if self.state != 'install':
            remove = QPushButton(_('theme_store.remove'))
            remove.setObjectName('theme_store_remove')
            remove.clicked.connect(lambda: self.act.emit(self.info, 'remove'))
            buttons.addWidget(remove)
        self.main = QPushButton(_(f'theme_store.action_{self.state}'))
        self.main.setObjectName(f'theme_store_{self.state}')
        self.main.setEnabled(self.state != 'in_use')
        self.main.clicked.connect(lambda: self.act.emit(self.info, self.state))
        buttons.addWidget(self.main)
        layout.addLayout(buttons)

    def set_busy(self, text):
        self.main.setEnabled(False)
        self.main.setText(text)

    def set_preview(self, pixmap):
        if pixmap is None or pixmap.isNull():
            return
        ratio = self.devicePixelRatioF()
        scaled = pixmap.scaled(int(PREVIEW_WIDTH * ratio), int(PREVIEW_HEIGHT * ratio), Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        self.preview.setPixmap(scaled)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(0, 0, self.width(), self.height() - 1), self.RADIUS, self.RADIUS)
        painter.fillPath(shape.translated(0, 1).subtracted(shape), theme.color(shapes.CONTACT))
        painter.fillPath(shape, theme.surface(QColor(self.IN_USE if self.state == 'in_use' else self.COLOUR)))


class ThemeBrowser(utils.SimpleDialog):
    """The themes subtitld.cc has, to install. `installed` after the timeline's theme or the list of
    installed themes changed. With `share_id`, only that theme (and its counterpart)."""

    installed = Signal()

    def __init__(self, parent=None, share_id=None):
        super().__init__(parent, title=_('theme_store.title'))
        self.setObjectName('theme_store_dialog')
        self.setMinimumSize(820, 600)
        self.share_id = share_id
        self.cards = []
        self.next_cursor = None
        self.generation = 0             # a new search makes the answers to the old one stale
        self.calls = []

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search_field = utils.LabeledLineEdit()
        self.search_field.setLabel(_('theme_store.search'))
        self.search = self.search_field.lineedit
        self.search.setObjectName('theme_store_search')
        self.search.setClearButtonEnabled(True)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(350)
        self.search_timer.timeout.connect(self.reload)
        self.search.textChanged.connect(lambda _text: self.search_timer.start())
        bar.addWidget(self.search_field, 1)
        self.look = utils.LabeledComboBox()
        self.look.setLabel(_('theme_store.look'))
        for value in LOOKS:
            self.look.combobox.addItem(_(f'theme_store.look_{value or "any"}'), value)
        self.look.combobox.activated.connect(lambda _index: self.reload())
        bar.addWidget(self.look)
        self.sort = utils.LabeledComboBox()
        self.sort.setLabel(_('theme_store.sort'))
        for value in SORTS:
            self.sort.combobox.addItem(_(f'theme_store.sort_{value}'), value)
        self.sort.combobox.activated.connect(lambda _index: self.reload())
        bar.addWidget(self.sort)
        self.bar = QWidget()
        self.bar.setLayout(bar)
        bar.setContentsMargins(0, 0, 0, 0)
        self.content.layout().addWidget(self.bar)

        self.status = QLabel()
        self.status.setObjectName('theme_store_status')
        self.status.setWordWrap(True)
        self.content.layout().addWidget(self.status)

        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignTop)
        self.more = QPushButton(_('theme_store.more'))
        self.more.setObjectName('theme_store_more')
        self.more.clicked.connect(self.load_more)
        page = QWidget()
        page.setObjectName('theme_store_page')
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 4, 0)
        page_layout.addWidget(self.grid_widget)
        page_layout.addWidget(self.more, 0, Qt.AlignHCenter)
        page_layout.addStretch()
        scroll = QScrollArea()
        scroll.setObjectName('theme_store_scroll')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        scroll.viewport().setAutoFillBackground(False)
        self.content.layout().addWidget(scroll, 1)

        self.accept_button.setText(_('theme_store.close'))
        self.reject_button.setText(_('theme_store.gallery'))
        self.reject_button.clicked.disconnect()
        self.reject_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(service.site_url('/themes/timeline'))))
        if share_id:
            self.bar.hide()
            self.reject_button.hide()
        self.reload()

    # --- Loading --------------------------------------------------------------------------------------

    def _call(self, function, on_success, on_failure=None):
        generation = self.generation

        def succeeded(result):
            if generation == self.generation:
                on_success(result)

        def failed(error):
            if generation == self.generation:
                (on_failure or self._failed)(error)

        self.calls.append(service.call(function, succeeded, failed))

    def reload(self):
        self.generation += 1
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        self.next_cursor = None
        self.more.hide()
        self.status.setText(_('theme_store.loading'))
        self.status.show()
        if self.share_id:
            share_id = self.share_id
            self._call(lambda: service.client().theme(share_id), self._one)
        else:
            self._page(None)

    def _query(self, cursor):
        look = self.look.combobox.currentData() or ''
        sort = self.sort.combobox.currentData() or 'popular'
        text = self.search.text().strip()
        return lambda: service.client().themes(look=look, sort=sort, q=text, cursor=cursor)

    def _page(self, cursor):
        self._call(self._query(cursor), self._listed)

    def load_more(self):
        if self.next_cursor:
            self.more.setEnabled(False)
            self._page(self.next_cursor)

    def _listed(self, answer):
        self.next_cursor = answer.get('next_cursor')
        self._add(answer.get('results') or [])
        self.more.setEnabled(True)
        self.more.setVisible(bool(self.next_cursor))
        if not self.cards:
            searching = self.search.text().strip() or self.look.combobox.currentData()
            self.status.setText(_('theme_store.no_matches' if searching else 'theme_store.empty'))
        else:
            self.status.hide()

    def _one(self, info):
        pair = info.pop('pair', None)
        self._add([info, pair] if pair else [info])
        self.status.hide()

    def _failed(self, error):
        self.more.setEnabled(True)
        if service.error_code(error) == 'not_found':
            self.status.setText(_('theme_store.not_found'))
        else:
            self.status.setText(_('theme_store.unreachable').format(
                error=service.error_message(error, _) or str(error)))
        self.status.show()

    def _add(self, infos):
        for info in infos:
            card = ThemeCard(info)
            card.act.connect(self._act)
            index = len(self.cards)
            self.grid.addWidget(card, index // COLUMNS, index % COLUMNS)
            self.cards.append(card)
            key = (info['share_id'], info['version'])
            if key in _previews:
                card.set_preview(_previews[key])
            else:
                self._call(lambda share_id=info['share_id'], version=info['version']:
                           service.client().theme_preview(share_id, version),
                           lambda data, key=key: self._preview(key, data), lambda _error: None)

    def _preview(self, key, data):
        pixmap = QPixmap()
        if pixmap.loadFromData(data, 'PNG'):
            _previews[key] = pixmap
            for card in self.cards:
                if (card.info['share_id'], card.info['version']) == key:
                    card.set_preview(pixmap)

    # --- Installing -----------------------------------------------------------------------------------

    def _act(self, info, action):
        share_id = info['share_id']
        if action in ('use', 'remove'):
            theme_store.use(share_id) if action == 'use' else theme_store.remove(share_id)
            self._changed()
            return
        card = next(card for card in self.cards if card.info['share_id'] == share_id)
        card.set_busy(_('theme_store.installing'))

        def done(files):
            try:
                theme_store.install(files)
            except theme_store.StoreError as error:
                self._install_failed(card, error)
                return
            self._changed()

        self._call(lambda: theme_store.fetch(share_id), done, lambda error: self._install_failed(card, error))

    def _install_failed(self, card, error):
        card.main.setEnabled(True)
        card.main.setText(_(f'theme_store.action_{card.state}'))
        self.status.setText(_('theme_store.install_failed').format(
            error=service.error_message(error, _) if not isinstance(error, theme_store.StoreError) else str(error)))
        self.status.show()

    def _changed(self):
        """Every card's button follows (one theme's install may change its pair's, and which is in use)."""
        self.installed.emit()
        infos = [card.info for card in self.cards]
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        generation_cursor = self.next_cursor
        self._add(infos)
        self.next_cursor = generation_cursor


def browse(window, share_id=None):
    """Open the browser; the Timeline tab and the timeline follow what it installs."""
    from subtitld.interface import left_panel_interface

    dialog = ThemeBrowser(window, share_id=share_id)

    def follow():
        panel = getattr(window, 'global_panel_interface_timeline_theme_panel', None)
        if panel is not None:
            panel.build()
        if getattr(window, 'timeline_widget', None) is not None:
            left_panel_interface.refresh_timeline(window)

    dialog.installed.connect(follow)
    dialog.exec()


def open_link(window, share_id):
    """A subtitld:// theme link: that theme, to install."""
    browse(window, share_id)
