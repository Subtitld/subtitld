"""Timeline theme cards, and the themes on subtitld.cc to install (modules/theme_store.py).

A card is drawn like an add-on's: the look the theme is drawn for on the slanted label, its name on the
band, the theme's picture from edge to edge under it, who made it, and the footer: a globe to its page on
subtitld.cc, then, at the right, its options, an update, the trash (while the pointer is on the card) and
the slanted tab in the corner: Use, Install, or, for the theme the timeline wears, a quiet In use mark in
its place. The Timeline tab lists the installed themes so (interface/timeline_theme_panel.py) and, under
them, this section: the public themes on subtitld.cc not installed here, by look, popular, new or best
rated, or by name.

A subtitld:// theme link (subtitldcc_open.open_link) shows that theme and its pair in the section, or,
with no editor open, in a dialog holding the same section.

Everything from subtitld.cc comes in the background (subtitldcc_service.call), once the section's head
first comes into sight; nothing needs signing in.
"""

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
                               QWidget)

from subtitld.interface import shapes, theme, utils
from subtitld.interface.addons_dialog import _AddonCard, _ElidedLabel, _graphic
from subtitld.interface.translation import _
from subtitld.modules import subtitldcc_service as service
from subtitld.modules import theme_store

LOOKS = ('', 'dark', 'light')
SORTS = ('popular', 'new', 'top')
PREVIEW_RATIO = 2 / 9                   # the pictures Subtitld draws of a theme are 9:2
CHECK = 8                               # the squares under a transparent picture, and their colours
CHECK_COLORS = (QColor('#6b6f75'), QColor('#55595e'))
_previews = {}                          # (share_id, version) -> QPixmap, for as long as Subtitld runs
remote = {}                             # share_id -> what subtitld.cc said of it last


class _Preview(QWidget):
    """The theme's picture, as wide as the card and 9:2 (or `ratio`, height to width); its key colours in
    bands while there is none."""

    def __init__(self, colors=(), parent=None, ratio=PREVIEW_RATIO):
        super().__init__(parent)
        self.pixmap = None
        self.ratio = ratio
        self.colors = [QColor(color) for color in colors]
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return round(width * self.ratio)

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
        rect = QRectF(self.rect())
        if self.pixmap is not None and not self.pixmap.isNull():
            if self.pixmap.hasAlphaChannel():
                # A subtitle or title theme's picture is the theme alone: what is transparent shows the
                # checkerboard image editors use for transparency (subtitld.cc's pages do the same).
                for row, y in enumerate(range(0, self.height(), CHECK)):
                    for column, x in enumerate(range(0, self.width(), CHECK)):
                        painter.fillRect(x, y, CHECK, CHECK, CHECK_COLORS[(row + column) % 2])
            painter.drawPixmap(rect, self.pixmap, QRectF(self.pixmap.rect()))
        elif self.colors:
            ground, *rest = self.colors
            painter.fillRect(rect, ground)
            band = rect.height() / 3
            for index, color in enumerate(rest[:3]):
                width = rect.width() * (0.42 - 0.08 * index)
                painter.fillRect(QRectF(rect.width() * (0.06 + 0.3 * index), band, width, band), color)
        else:
            painter.fillRect(rect, shapes.CONTACT)


class _Head(QWidget):
    """The section's head, set apart from the cards above: a line along its top, and a shade under it
    that fades out downwards."""

    LINE = QColor(0, 0, 0, 90)
    SHADE = QColor(0, 0, 0, 46)

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = QRectF(self.rect())
        shade = QLinearGradient(0, 0, 0, rect.height())
        shade.setColorAt(0, theme.surface(self.SHADE))
        shade.setColorAt(1, QColor(0, 0, 0, 0))
        painter.fillRect(rect, shade)
        painter.fillRect(QRectF(0, 0, rect.width(), 1), theme.color(self.LINE))


class ThemeCard(_AddonCard):
    """A theme as a card, drawn as an add-on card is (band, slanted label, contact line, corner tab).

    `info`: title, owner (or author), look, counterpart, rating, downloads, url (its page on subtitld.cc),
    and state: 'install', 'update', 'use' or 'in_use'; with `options` (it has some), `removable` and
    `update` (subtitld.cc has a newer version of an installed one). A subtitle or title theme's has `mark`
    (said on the slanted label in the look's place: that it is animated) and `slots` (a title's texts).
    `ratio`: its picture's height to its width. `act(info, action)`: 'install', 'update', 'use', 'remove'
    or 'options'."""

    act = Signal(dict, str)
    COLOURS = {**_AddonCard.COLOURS, 'in_use': ('#33475a', '#4a6880')}

    def __init__(self, info, colors=(), parent=None, ratio=PREVIEW_RATIO):
        QFrame.__init__(self, parent)              # the add-on card's drawing, not its add-on
        self.info = info
        self.state = info['state']
        self.setProperty('class', 'addon_card')
        self.setObjectName('theme_card')
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._kind = {'install': 'available', 'in_use': 'in_use'}.get(self.state, 'installed')

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 1)        # the contact line's row
        outer.setSpacing(0)

        # --- The band: the look on the label, the name, a newer version ----
        band = QWidget()
        band.setFixedHeight(self.BAND)
        band.setLayout(QHBoxLayout())
        band.layout().setContentsMargins(9, 0, 12, 0)
        band.layout().setSpacing(0)
        mark = info.get('mark') or (_(f"theme_store.look_{info['look']}") if info.get('look') else '')
        self._task_text = QLabel(mark)
        self._task_text.setProperty('class', 'addon_card_task')
        self._task_text.setContentsMargins(0, 0, 0, self.BAND - self.LABEL)
        band.layout().addWidget(self._task_text)
        band.layout().addSpacing(24 if mark else 5)   # the label's padding and its slant
        title = _ElidedLabel(info['title'], least=60)
        title.setProperty('class', 'addon_card_title')
        band.layout().addWidget(title, 1)
        if info.get('update'):
            version = QLabel(_('theme_store.newer').format(version=info['update']))
            version.setProperty('class', 'addon_card_version')
            band.layout().addWidget(version)
        outer.addWidget(band)

        # --- The picture, edge to edge, and what it is ----------------------
        self.preview = _Preview(colors, ratio=ratio)
        self.preview.setAccessibleName(_('theme_store.preview_of').format(name=info['title']))
        outer.addWidget(self.preview)
        about = [f"@{info['owner']}" if info.get('owner') else info.get('author', '')]
        if info.get('counterpart'):
            about.append(_('theme_store.both_looks'))
        if info.get('slots'):
            about.append(_('html_theme_store.slots').format(slots=', '.join(info['slots'])))
        if info.get('rating'):
            about.append(f"★ {info['rating']['average']:.1f}")
        if 'downloads' in info:
            about.append(_('theme_store.downloads').format(count=info['downloads']))
        meta = QLabel(' · '.join(part for part in about if part))
        meta.setProperty('class', 'addon_card_summary')
        meta.setWordWrap(True)
        meta.setContentsMargins(14, 9, 14, 4)
        outer.addWidget(meta)

        # --- The footer: its page; its options, update, trash and the tab ---
        footer = QWidget()
        footer.setFixedHeight(30)
        footer.setLayout(QHBoxLayout())
        footer.layout().setContentsMargins(4, 0, 0, 0)
        footer.layout().setSpacing(2)
        self.site = None
        if info.get('url'):
            self.site = self._button('theme_card_site.svg', _('theme_store.on_site'),
                                     lambda: QDesktopServices.openUrl(QUrl(info['url'])))
            footer.layout().addWidget(self.site, 0, Qt.AlignVCenter)
        footer.layout().addStretch()
        self._hover_buttons = []
        self.update_button = self.options = self.remove = None
        if info.get('update'):
            self.update_button = self._button('addon_card_update.svg', _('theme_store.action_update'),
                                              lambda: self.act.emit(self.info, 'update'))
            footer.layout().addWidget(self.update_button, 0, Qt.AlignVCenter)
        if info.get('options'):
            self.options = self._button('theme_card_options.svg', _('theme_store.options'),
                                        lambda: self.act.emit(self.info, 'options'))
            footer.layout().addWidget(self.options, 0, Qt.AlignVCenter)
        if info.get('removable'):
            self.remove = self._button('addon_card_remove.svg', _('theme_store.remove'),
                                       lambda: self.act.emit(self.info, 'remove'))
            self._hover_buttons.append(self.remove)
            footer.layout().addWidget(self.remove, 0, Qt.AlignVCenter)
        footer.layout().addSpacing(4)
        if self.state == 'in_use':
            # Not a button: a mark in the tab's place, saying what is so.
            self.main = QLabel(_('theme_store.action_in_use'))
            self.main.setObjectName('theme_store_in_use')
            self.main.setProperty('class', 'theme_card_in_use')
            icon = QLabel()
            icon.setPixmap(theme.icon('theme_card_in_use.svg').pixmap(QSize(16, 16), self.devicePixelRatioF()))
            icon.setProperty('class', 'theme_card_in_use')
            footer.layout().addWidget(icon, 0, Qt.AlignVCenter)
            footer.layout().addSpacing(5)
            footer.layout().addWidget(self.main, 0, Qt.AlignVCenter)
            footer.layout().addSpacing(13)
        else:
            self.main = QPushButton(_(f'theme_store.action_{self.state}'))
            self.main.setObjectName(f'theme_store_{self.state}')
            self.main.setProperty('class', 'addon_card_tab')
            self.main.setFixedHeight(30)
            if self.state == 'install':
                self.main.setIcon(_graphic('addon_card_install.svg'))
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
        if isinstance(self.main, QPushButton):
            self.main.setEnabled(False)
            self.main.setText(text)

    def set_preview(self, pixmap):
        if pixmap is not None and not pixmap.isNull():
            self.preview.set_pixmap(pixmap)


def remote_info(info):
    """A card's info for a theme subtitld.cc lists: to install, so nothing to set, update or remove yet."""
    return {**info, 'state': theme_store.state(info), 'options': False, 'removable': False, 'update': None}


def install(owner, card, share_id, done, use=True):
    """Fetch and install a theme (and its pair) in the background; `done()` after, on the UI thread.
    With `use`, the timeline takes it (theme_store.install)."""
    card.set_busy(_('theme_store.installing'))

    def installed(files):
        try:
            theme_store.install(files, use=use)
        except theme_store.StoreError as error:
            owner.install_failed(card, error)
            return
        done()

    owner.calls.append(service.call(lambda: theme_store.fetch(share_id), installed,
                                    lambda error: owner.install_failed(card, error)))


def failure_text(error):
    if isinstance(error, theme_store.StoreError):
        return str(error)
    return service.error_message(error, _) or str(error)


class ThemeBrowser(QWidget):
    """The themes subtitld.cc has that aren't installed here, to install. `installed` after one was;
    `listed` when subtitld.cc answered (the installed themes' cards then know of their updates). Loads the
    first time it is shown."""

    installed = Signal()
    listed = Signal()
    known = remote                      # what subtitld.cc said of each theme it listed, by share ID

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

        # The head: its name, then the search, the look and the order on one line.
        self.head = _Head()
        self.head.setObjectName('theme_store_head')
        head = QVBoxLayout(self.head)
        head.setContentsMargins(10, 14, 10, 18)
        head.setSpacing(10)
        layout.addWidget(self.head)
        self.heading = QLabel()
        self.heading.setProperty('class', 'widget_label')
        head.addWidget(self.heading)

        self.bar = QWidget()
        bar = QHBoxLayout(self.bar)
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
        self.search.setMinimumWidth(40)
        bar.addWidget(self.search_field, 1)
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
        # Narrow enough for one line in the panel: a long choice is cut short on the closed field.
        for field in (self.look, self.sort):
            field.combobox.setSizeAdjustPolicy(
                field.combobox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            field.combobox.setMinimumContentsLength(12)
            field.setSizePolicy(QSizePolicy.Expanding, field.sizePolicy().verticalPolicy())
        # Room to spare goes to all three, the search taking the most.
        bar.setStretch(0, 3)
        bar.setStretch(1, 2)
        bar.setStretch(2, 2)
        head.addWidget(self.bar)

        self.showing_one = QWidget()
        head.addWidget(self.showing_one)
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

        # The rest keeps the panel's 10px sides; the head above runs from edge to edge.
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(10, 0, 10, 0)
        body.setSpacing(10)
        layout.addWidget(self.body)
        self.status = QLabel()
        self.status.setObjectName('theme_store_status')
        self.status.setWordWrap(True)
        body.addWidget(self.status)

        self.list = QVBoxLayout()
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(10)
        body.addLayout(self.list)
        self.more = QPushButton()
        self.more.setObjectName('theme_store_more')
        self.more.clicked.connect(self.load_more)
        self.more.hide()
        body.addWidget(self.more, 0, Qt.AlignHCenter)
        self.gallery = QPushButton()
        self.gallery.setObjectName('theme_store_gallery')
        self.gallery.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(service.site_url('/themes/timeline'))))
        body.addWidget(self.gallery, 0, Qt.AlignLeft)
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
        """Fetched once its head is in sight: in the Timeline tab, when the panel is scrolled down to it,
        not each time the tab opens."""
        super().showEvent(event)
        if self.loaded:
            return
        scroll = self._scroll_area()
        if scroll is not None and not getattr(self, '_watching', False):
            self._watching = True
            scroll.verticalScrollBar().valueChanged.connect(lambda _value: self._load_if_seen())
            scroll.verticalScrollBar().rangeChanged.connect(lambda _low, _high: self._load_if_seen())
        QTimer.singleShot(0, self._load_if_seen)

    def _scroll_area(self):
        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        return parent

    def _load_if_seen(self):
        if self.loaded or not self.isVisible():
            return
        if self._scroll_area() is None or not self.head.visibleRegion().isEmpty():
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

    def _remember(self, infos):
        for info in infos:
            self.known[info['share_id']] = info
        self.listed.emit()

    def _listed(self, answer):
        self.next_cursor = answer.get('next_cursor')
        results = answer.get('results') or []
        self._remember(results)
        # Installed ones have their card above, with the installed themes.
        self._add([info for info in results if self._state(info) == 'install'])
        self.more.setEnabled(True)
        self.more.setVisible(bool(self.next_cursor))
        self._say_if_empty()

    def _say_if_empty(self):
        if self.cards:
            self.status.hide()
            return
        searching = self.search.text().strip() or self.look.combobox.currentData()
        self.status.setText(_('theme_store.no_matches' if searching else 'theme_store.all_installed'
                              if self.known else 'theme_store.empty'))
        self.status.show()

    def _one(self, info):
        pair = info.pop('pair', None)
        infos = [info, pair] if pair else [info]
        self._remember(infos)
        self._add(infos)
        self.status.hide()

    def _failed(self, error):
        self.more.setEnabled(True)
        if service.error_code(error) == 'not_found':
            self.status.setText(_('theme_store.not_found'))
        else:
            self.status.setText(_('theme_store.unreachable').format(error=failure_text(error)))
        self.status.show()

    def _state(self, info):
        return theme_store.state(info)

    def _card(self, info):
        return ThemeCard(remote_info(info), (info.get('colors') or {}).values())

    def _add(self, infos):
        for info in infos:
            card = self._card(info)
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
        card = next(card for card in self.cards if card.info['share_id'] == info['share_id'])
        if action == 'use':
            theme_store.use(theme_store.theme_id(info['share_id']))
            self._changed()
        elif action in ('install', 'update'):
            install(self, card, info['share_id'], self._changed)

    def install_failed(self, card, error):
        if isinstance(card.main, QPushButton):
            card.main.setEnabled(True)
            card.main.setText(_(f'theme_store.action_{card.state}'))
        self.status.setText(_('theme_store.install_failed').format(error=failure_text(error)))
        self.status.show()

    def _changed(self):
        """An install: its card goes up to the installed themes (and its pair's with it)."""
        self.installed.emit()
        infos = [card.info for card in self.cards if self._state(card.info) == 'install']
        if self.share_id:
            infos = [card.info for card in self.cards]
        self._clear()
        self._add([{k: v for k, v in info.items() if k != 'state'} for info in infos])
        self._say_if_empty()


def follow(window):
    """The Timeline tab and the timeline after a theme was installed, removed, used or set."""
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
