"""Timeline themes from subtitld.cc, browsed and installed in Subtitld (modules/theme_store.py).

A section of the Timeline tab, under the theme picker: the public themes on subtitld.cc (by look, popular,
new or best rated, or by name), each as a card drawn like an add-on's. On its band, the look it is drawn
for on the slanted label and its name; under it the preview subtitld.cc drew, who made it and whether it
comes with a counterpart for the other look. In the footer, a globe to its page on subtitld.cc and, on the
slanted tab in the corner, Install (the theme and its counterpart, and the timeline takes the one drawn
for the interface's look), Update when subtitld.cc has a newer version, Use for an installed one; the
trash beside the tab, shown while the pointer is on the card, removes it.

A subtitld:// theme link (subtitldcc_open.open_link) shows that theme and its pair here, or, with no
editor open, in a dialog holding the same section.

Everything from subtitld.cc comes in the background (subtitldcc_service.call), the first time the section
is shown; nothing needs signing in.
"""

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from subtitld.interface import shapes, utils
from subtitld.interface.addons_dialog import _AddonCard, _ElidedLabel, _graphic
from subtitld.interface.translation import _
from subtitld.modules import subtitldcc_service as service
from subtitld.modules import theme_store

LOOKS = ('', 'dark', 'light')
SORTS = ('popular', 'new', 'top')
PREVIEW_RATIO = 2 / 9                   # the previews subtitld.cc draws are 9:2
_previews = {}                          # (share_id, version) -> QPixmap, for as long as Subtitld runs


class _Preview(QWidget):
    """The preview, as wide as the card and 9:2."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap = None
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return round(width * PREVIEW_RATIO)

    def sizeHint(self):
        return QSize(360, self.heightForWidth(360))

    def minimumSizeHint(self):
        return QSize(90, self.heightForWidth(90))

    def set_pixmap(self, pixmap):
        self.pixmap = pixmap
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        painter.setClipPath(shapes.rect_path(rect))
        if self.pixmap is None or self.pixmap.isNull():
            painter.fillRect(rect, shapes.CONTACT)
        else:
            painter.drawPixmap(rect, self.pixmap, QRectF(self.pixmap.rect()))


class ThemeCard(_AddonCard):
    """One theme subtitld.cc lists, drawn as an add-on card is (its band, slanted label, contact line and
    corner tab). `act(info, action)`: 'install', 'update', 'use' or 'remove'."""

    act = Signal(dict, str)

    def __init__(self, info, parent=None):
        QFrame.__init__(self, parent)              # the add-on card's drawing, not its add-on
        self.info = info
        self.state = theme_store.state(info)
        self.setProperty('class', 'addon_card')
        self.setObjectName('theme_card')
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._kind = 'available' if self.state == 'install' else 'installed'

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 1)        # the contact line's row
        outer.setSpacing(0)

        # --- The band: the look on the label, the name, a newer version ----
        band = QWidget()
        band.setFixedHeight(self.BAND)
        band.setLayout(QHBoxLayout())
        band.layout().setContentsMargins(9, 0, 12, 0)
        band.layout().setSpacing(0)
        self._task_text = QLabel(_(f"theme_store.look_{info['look']}"))
        self._task_text.setProperty('class', 'addon_card_task')
        self._task_text.setContentsMargins(0, 0, 0, self.BAND - self.LABEL)
        band.layout().addWidget(self._task_text)
        band.layout().addSpacing(24)                # the label's padding and its slant
        title = _ElidedLabel(info['title'], least=60)
        title.setProperty('class', 'addon_card_title')
        band.layout().addWidget(title, 1)
        if self.state == 'update':
            version = QLabel(_('theme_store.newer').format(version=info['version']))
            version.setProperty('class', 'addon_card_version')
            band.layout().addWidget(version)
        outer.addWidget(band)

        # --- The preview and what it is ------------------------------------
        body = QWidget()
        body.setLayout(QVBoxLayout())
        body.layout().setContentsMargins(14, 12, 14, 10)
        body.layout().setSpacing(9)
        self.preview = _Preview()
        self.preview.setAccessibleName(_('theme_store.preview_of').format(name=info['title']))
        body.layout().addWidget(self.preview)
        about = [f"@{info['owner']}" if info.get('owner') else '']
        if info.get('counterpart'):
            about.append(_('theme_store.both_looks'))
        if info.get('rating'):
            about.append(f"★ {info['rating']['average']:.1f}")
        about.append(_('theme_store.downloads').format(count=info.get('downloads', 0)))
        meta = QLabel(' · '.join(part for part in about if part))
        meta.setProperty('class', 'addon_card_summary')
        meta.setWordWrap(True)
        body.layout().addWidget(meta)
        outer.addWidget(body)

        # --- The footer: its page, then the trash and the tab ---------------
        footer = QWidget()
        footer.setFixedHeight(30)
        footer.setLayout(QHBoxLayout())
        footer.layout().setContentsMargins(4, 0, 0, 0)
        footer.layout().setSpacing(0)
        self.site = self._button('theme_card_site.svg', _('theme_store.on_site'),
                                 lambda: QDesktopServices.openUrl(QUrl(info['url'])))
        footer.layout().addWidget(self.site, 0, Qt.AlignVCenter)
        footer.layout().addStretch()
        self._hover_buttons = []
        if self.state != 'install':
            self.remove = self._button('addon_card_remove.svg', _('theme_store.remove'),
                                       lambda: self.act.emit(self.info, 'remove'))
            self._hover_buttons.append(self.remove)
            footer.layout().addWidget(self.remove, 0, Qt.AlignVCenter)
            footer.layout().addSpacing(4)
        self.main = QPushButton(_(f'theme_store.action_{self.state}'))
        self.main.setObjectName(f'theme_store_{self.state}')
        self.main.setProperty('class', 'addon_card_tab')
        self.main.setFixedHeight(30)
        if self.state == 'install':
            self.main.setIcon(_graphic('addon_card_install.svg'))
        elif self.state == 'update':
            self.main.setIcon(_graphic('addon_card_update.svg'))
        self.main.setEnabled(self.state != 'in_use')
        self.main.clicked.connect(lambda: self.act.emit(self.info, self.state))
        footer.layout().addWidget(self.main)
        outer.addWidget(footer)
        for button in self._hover_buttons:
            button.setVisible(False)

    def _button(self, icon, text, slot):
        button = QPushButton()
        button.setProperty('class', 'addon_card_icon')
        button.setIcon(_graphic(icon))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(26, 26)
        button.setToolTip(text)
        button.setAccessibleName(text)
        button.clicked.connect(slot)
        return button

    def set_busy(self, text):
        self.main.setEnabled(False)
        self.main.setText(text)

    def set_preview(self, pixmap):
        if pixmap is not None and not pixmap.isNull():
            self.preview.set_pixmap(pixmap)


class ThemeBrowser(QWidget):
    """The themes subtitld.cc has, to install. `installed` after the timeline's theme or the list of
    installed themes changed. Loads the first time it is shown."""

    installed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('theme_store')
        self.setProperty('class', 'transparent_panel')
        self.share_id = None            # showing one theme (a link) instead of the list
        self.cards = []
        self.next_cursor = None
        self.generation = 0             # a new search makes the answers to the old one stale
        self.calls = []
        self.loaded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.heading = QLabel()
        self.heading.setProperty('class', 'widget_label')
        layout.addWidget(self.heading)

        # The search, then the look and the order under it: the panel is narrow.
        self.bar = QWidget()
        rows = QVBoxLayout(self.bar)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(8)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        self.search_field = utils.LabeledLineEdit()
        self.search = self.search_field.lineedit
        self.search.setObjectName('theme_store_search')
        self.search.setClearButtonEnabled(True)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(350)
        self.search_timer.timeout.connect(self.reload)
        self.search.textChanged.connect(lambda _text: self.search_timer.start())
        rows.addWidget(self.search_field)
        rows.addLayout(bar)
        self.look = utils.LabeledComboBox()
        for value in LOOKS:
            self.look.combobox.addItem('', value)
        self.look.combobox.activated.connect(lambda _index: self.reload())
        bar.addWidget(self.look)
        self.sort = utils.LabeledComboBox()
        for value in SORTS:
            self.sort.combobox.addItem('', value)
        self.sort.combobox.activated.connect(lambda _index: self.reload())
        bar.addWidget(self.sort)
        bar.addStretch()
        layout.addWidget(self.bar)

        self.showing_one = QWidget()
        one = QHBoxLayout(self.showing_one)
        one.setContentsMargins(0, 0, 0, 0)
        self.showing_one_label = QLabel()
        self.showing_one_label.setProperty('class', 'addon_card_aside')
        one.addWidget(self.showing_one_label, 1)
        self.show_all = QPushButton()
        self.show_all.setObjectName('theme_store_show_all')
        self.show_all.clicked.connect(lambda: self.show_theme(None))
        one.addWidget(self.show_all)
        self.showing_one.hide()
        layout.addWidget(self.showing_one)

        self.status = QLabel()
        self.status.setObjectName('theme_store_status')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.list = QVBoxLayout()
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(10)
        layout.addLayout(self.list)
        self.more = QPushButton()
        self.more.setObjectName('theme_store_more')
        self.more.clicked.connect(self.load_more)
        self.more.hide()
        layout.addWidget(self.more, 0, Qt.AlignHCenter)
        self.gallery = QPushButton()
        self.gallery.setObjectName('theme_store_gallery')
        self.gallery.setFlat(True)
        self.gallery.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(service.site_url('/themes/timeline'))))
        layout.addWidget(self.gallery, 0, Qt.AlignLeft)
        self.translate()

    def translate(self):
        self.heading.setText(_('theme_store.title'))
        self.search_field.setLabel(_('theme_store.search'))
        self.look.setLabel(_('theme_store.look'))
        for index, value in enumerate(LOOKS):
            self.look.combobox.setItemText(index, _(f'theme_store.look_{value or "any"}'))
        self.sort.setLabel(_('theme_store.sort'))
        for index, value in enumerate(SORTS):
            self.sort.combobox.setItemText(index, _(f'theme_store.sort_{value}'))
        self.showing_one_label.setText(_('theme_store.showing_one'))
        self.show_all.setText(_('theme_store.show_all'))
        self.more.setText(_('theme_store.more'))
        self.gallery.setText(_('theme_store.gallery'))

    def showEvent(self, event):
        super().showEvent(event)
        if not self.loaded:
            self.reload()

    def show_theme(self, share_id):
        """Only that theme and its pair (a link), or, with None, the list again."""
        self.share_id = share_id
        self.bar.setVisible(share_id is None)
        self.showing_one.setVisible(share_id is not None)
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

    def _clear(self):
        for card in self.cards:
            self.list.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self.cards = []

    def reload(self):
        self.loaded = True
        self.generation += 1
        self._clear()
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
            self.list.addWidget(card)
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
        self._clear()
        self._add(infos)


def follow(window):
    """The Timeline tab and the timeline after the browser changed the theme or the installed ones."""
    from subtitld.interface import left_panel_interface

    panel = getattr(window, 'global_panel_interface_timeline_theme_panel', None)
    if panel is not None:
        panel.build()
    if getattr(window, 'timeline_widget', None) is not None:
        left_panel_interface.refresh_timeline(window)


def open_link(window, share_id):
    """A subtitld:// theme link: that theme, to install, in the Timeline tab; in a dialog holding the same
    section while no editor is open (the start screen)."""
    from subtitld.interface import left_panel, subtitldcc_open

    browser = getattr(window, 'global_panel_interface_theme_browser', None)
    if subtitldcc_open.editor_shown(window) and browser is not None:
        left_panel.show_panel(window, 'interface')
        window.global_panel_interface_tabwidget.setCurrentWidget(window.global_panel_interface_tabwidget_timeline)
        browser.show_theme(share_id)
        return
    dialog = utils.SimpleDialog(window, title=_('theme_store.title'))
    dialog.setMinimumSize(520, 560)
    section = ThemeBrowser()
    section.heading.hide()
    section.gallery.hide()
    section.installed.connect(lambda: follow(window))
    dialog.content.layout().addWidget(section)
    dialog.content.layout().addStretch()
    dialog.accept_button.setText(_('theme_store.close'))
    dialog.reject_button.hide()
    section.show_theme(share_id)
    dialog.exec()
