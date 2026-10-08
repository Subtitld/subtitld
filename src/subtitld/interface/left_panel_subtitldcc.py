"""The subtitld.cc panel.

- **Account**: connect (the browser opens subtitld.cc; or a code typed at subtitld.cc/device) and
  disconnect. Finding and opening public subtitles works without it.
- **For this video**: when a video opens, subtitles that match it (by fingerprint, never by name).
- **Search** subtitld.cc by title.
- **Open** a subtitle in the editor. The editor then remembers where it came from (USF ``origin``), so
  this panel can show it, let you rate it or confirm it works with your video, and publish your changes
  as its next version.
- **Publish** the subtitles in the editor (interface/subtitldcc_publish.py).

The network side is modules/subtitldcc_service.py; every call runs off the UI thread.
"""

from PySide6.QtCore import Qt, QUrl, QTimer, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
    QWidget,
)

from subtitld.interface import left_panel
from subtitld.interface.subtitldcc_publish import PublishDialog, ask
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import subtitldcc_service as service

TAB = 'subtitldcc'
STARS = 5


def load(self):
    panel = left_panel.left_panel(parent=self, tab_name=TAB, update_callback=update, translate_callback=translate)
    panel.layout().setContentsMargins(0, 0, 0, 0)
    self.subtitldcc_panel = SubtitldccPanel(self)
    panel.layout().addWidget(self.subtitldcc_panel)
    application = QApplication.instance()
    if application is not None:
        application.aboutToQuit.connect(self.subtitldcc_panel.shutdown)


def update(self):
    self.subtitldcc_panel.refresh()


def translate(self):
    self.subtitldcc_panel.retranslate()


def video_opened(self):
    """A video was opened (start screen, recent files, an autosave backup): look it up."""
    if hasattr(self, 'subtitldcc_panel'):
        self.subtitldcc_panel.video_opened()


# --- Small builders --------------------------------------------------------------------------------------


def _label(object_name='', wrap=True, selectable=False):
    label = QLabel()
    if object_name:
        label.setObjectName(object_name)
    label.setWordWrap(wrap)
    if selectable:
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


def _button(css_class='', small=False):
    button = QPushButton()
    if css_class:
        button.setProperty('class', css_class)
    if small:
        button.setObjectName('subtitldcc_small_button')
    button.setCursor(Qt.PointingHandCursor)
    return button


def _card():
    """A card (styled like the Subtitld Cloud one) and the layout to fill."""
    card = QWidget()
    card.setObjectName('subtitldcc_card')
    card.setAttribute(Qt.WA_StyledBackground, True)
    # Preferred, not Maximum: wrapped labels need their full height (see cloud_dashboard.py).
    card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(12, 10, 12, 12)
    layout.setSpacing(8)
    return card, layout


def _row(*widgets, stretch_after=True):
    row = QWidget()
    row.setProperty('class', 'transparent_panel')
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    for widget in widgets:
        layout.addWidget(widget)
    if stretch_after:
        layout.addStretch()
    return row


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()


def language_name(code):
    names = (service.cached_meta() or {}).get('language_names') or {}
    return names.get(code, code or '')


def describe_subtitle(result):
    """"Português (Brasil) · v3 · ★ 4.5 (12) · 1.234 downloads · @marina"."""
    parts = []
    if result.get('language'):
        parts.append(language_name(result['language']))
    if result.get('version'):
        parts.append(f"v{result['version']}")
    rating = result.get('rating')
    if rating:
        parts.append(f"★ {rating['average']:.1f} ({rating['count']})")
    parts.append(_('subtitldcc.downloads').format(count=f"{result.get('downloads', 0):,}"))
    parts.append(f"@{result['owner']}" if result.get('owner') else _('subtitldcc.deleted_user'))
    return ' · '.join(parts)


class ResultRow(QWidget):
    """One subtitle in a list: title, details, Open and a link to its page."""

    def __init__(self, panel, result, match=None):
        super().__init__()
        self.result = result
        self.setObjectName('subtitldcc_result')
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(3)

        title = _label('subtitldcc_result_title')
        title.setText(result.get('title') or result.get('share_id'))
        layout.addWidget(title)
        details = _label('subtitldcc_result_details')
        details.setText(describe_subtitle(result))
        layout.addWidget(details)

        badges = []
        if match:
            badge = QLabel(_(f'subtitldcc.match_{match}'))
            badge.setObjectName('subtitldcc_badge')
            badge.setProperty('kind', match)
            badge.setToolTip(_(f'subtitldcc.match_{match}_tip'))
            badges.append(badge)
        for flag in result.get('flags') or []:
            badge = QLabel(_(f'subtitldcc.flag_{flag}'))
            badge.setObjectName('subtitldcc_badge')
            badges.append(badge)
        self.open_button = _button('primary', small=True)
        self.open_button.setText(_('subtitldcc.open'))
        self.open_button.setToolTip(_('subtitldcc.open_tip'))
        self.open_button.clicked.connect(lambda: panel.open_subtitle(self))
        page_button = _button(small=True)
        page_button.setText(_('subtitldcc.page'))
        page_button.setToolTip(result.get('url', ''))
        page_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(result['url'])))
        actions = _row(*badges)
        actions.layout().addWidget(page_button)
        actions.layout().addWidget(self.open_button)
        layout.addWidget(actions)

    def set_busy(self, busy):
        self.open_button.setEnabled(not busy)
        self.open_button.setText(_('subtitldcc.opening') if busy else _('subtitldcc.open'))


# --- The panel -------------------------------------------------------------------------------------------


class SubtitldccPanel(QWidget):
    # The device code arrives on the sign-in thread; this carries it to the UI thread.
    device_code = Signal(str, str, str)

    def __init__(self, window):
        super().__init__()
        self.device_code.connect(self._show_device_code)
        self.window_ = window
        self.setProperty('class', 'transparent_panel')
        self._sign_in_call = None
        self._cancel_sign_in = False
        self._lookup_call = None
        self._lookup_path = None
        self._video = None  # fingerprint of the open video
        self._matches = None
        self._lookup_error = None
        self._search_cursor = None
        self._search_query = ''
        self._origin_info = None  # GET /assets/{id} for the subtitle the editor holds
        self._origin_loading = None
        self._busy_row = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        inner = QWidget()
        inner.setProperty('class', 'transparent_panel')
        self.cards = QVBoxLayout(inner)
        self.cards.setContentsMargins(10, 10, 10, 10)
        self.cards.setSpacing(10)
        scroll.setWidget(inner)

        self._build_account()
        self._build_origin()
        self._build_video()
        self._build_search()
        self._build_publish()
        self.cards.addStretch()

        self.message = _label('subtitldcc_message')
        self.message.hide()
        self.cards.insertWidget(0, self.message)
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self.message.hide)

        self.retranslate()

    # --- Account -----------------------------------------------------------------------------------------

    def _build_account(self):
        card, layout = _card()
        self.account_title = _label('subtitldcc_card_title')
        layout.addWidget(self.account_title)
        self.account_text = _label('subtitldcc_text')
        layout.addWidget(self.account_text)
        self.account_code = _label('subtitldcc_code', selectable=True)
        self.account_code.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.account_code)

        self.connect_button = _button('primary')
        self.connect_button.clicked.connect(lambda: self.sign_in(device=False))
        self.code_button = _button()
        self.code_button.clicked.connect(lambda: self.sign_in(device=True))
        self.device_page_button = _button()
        self.device_page_button.clicked.connect(self._open_device_page)
        self.cancel_button = _button()
        self.cancel_button.clicked.connect(self.cancel_sign_in)
        self.disconnect_button = _button()
        self.disconnect_button.clicked.connect(self.sign_out)
        layout.addWidget(_row(self.connect_button, self.code_button, self.device_page_button,
                              self.cancel_button, self.disconnect_button))
        self.cards.addWidget(card)
        self._device_uri = ''

    def _render_account(self, waiting=None, code=None):
        account = service.account()
        connected = service.is_connected()
        self.account_code.setVisible(bool(code))
        self.device_page_button.setVisible(bool(code))
        self.cancel_button.setVisible(bool(waiting))
        self.connect_button.setVisible(not connected and not waiting)
        self.code_button.setVisible(not connected and not waiting)
        self.disconnect_button.setVisible(connected and not waiting)
        if code:
            self.account_code.setText(code)
            self.account_text.setText(_('subtitldcc.device_wait').format(uri=self._device_uri))
        elif waiting:
            self.account_text.setText(_('subtitldcc.browser_wait'))
        elif connected:
            handle = account.get('handle') or ''
            name = account.get('name') or ''
            text = f'<b>@{handle}</b>' + (f' · {name}' if name and name != handle else '')
            self.account_text.setText(_('subtitldcc.connected_as').format(account=text))
        else:
            self.account_text.setText(_('subtitldcc.connect_why'))

    def sign_in(self, device):
        if self._sign_in_call is not None:
            return
        self._cancel_sign_in = False

        def shown(code, uri, full_uri):
            self.device_code.emit(code, uri, full_uri)

        function = (lambda: service.sign_in(shown if device else None, cancelled=lambda: self._cancel_sign_in))
        self._render_account(waiting=True)
        self._sign_in_call = service.call(function, self._signed_in, self._sign_in_failed)
        self._sign_in_call.finished.connect(self._sign_in_finished)

    def _show_device_code(self, code, uri, full_uri):
        self._device_uri, self._device_full_uri = uri, full_uri
        self._render_account(waiting=True, code=code)

    def _sign_in_finished(self):
        # Runs after _signed_in / _sign_in_failed (finished comes last): redraw without "waiting".
        self._sign_in_call = None
        self._render_account()

    def _signed_in(self, me):
        service.remember_account(me)
        self._render_account()
        self.show_message(_('subtitldcc.connected').format(handle=me.get('handle', '')))
        self.refresh()
        if self._video:  # shared subtitles may match too now
            self._run_lookup()

    def _sign_in_failed(self, error):
        self._render_account()
        if service.error_code(error) != 'canceled':
            self.show_message(service.error_message(error, _), error=True)

    def cancel_sign_in(self):
        self._cancel_sign_in = True

    def _open_device_page(self):
        QDesktopServices.openUrl(QUrl(getattr(self, '_device_full_uri', '') or self._device_uri))

    def sign_out(self):
        if not ask(self.window_, _('subtitldcc.disconnect_title'), _('subtitldcc.disconnect_text'),
                   _('subtitldcc.disconnect')):
            return
        self.disconnect_button.setEnabled(False)

        def done(_result=None):
            self.disconnect_button.setEnabled(True)
            self._origin_info = None
            self.refresh()

        service.call(service.sign_out, done, done)

    def _token_expired(self):
        """The token stopped working (revoked on the site): show the connect card again."""
        service.forget_account()
        self.refresh()
        self.show_message(_('subtitldcc.error_unauthorized'), error=True)

    # --- The subtitle in the editor ----------------------------------------------------------------------

    def _build_origin(self):
        card, layout = _card()
        self.origin_card = card
        self.origin_kicker = _label('subtitldcc_kicker')
        layout.addWidget(self.origin_kicker)
        self.origin_title = _label('subtitldcc_card_title')
        layout.addWidget(self.origin_title)
        self.origin_details = _label('subtitldcc_text')
        layout.addWidget(self.origin_details)

        self.stars = []
        for value in range(1, STARS + 1):
            star = _button(small=True)
            star.setObjectName('subtitldcc_star')
            star.setCheckable(True)
            star.setText('★')
            star.clicked.connect(lambda _checked=False, v=value: self.rate(v))
            self.stars.append(star)
        self.rate_label = _label('subtitldcc_wlabel', wrap=False)
        self.rating_row = _row(self.rate_label, *self.stars)
        layout.addWidget(self.rating_row)

        self.works_button = _button(small=True)
        self.works_button.clicked.connect(self.confirm_works)
        self.origin_page_button = _button(small=True)
        self.origin_page_button.clicked.connect(self._open_origin_page)
        layout.addWidget(_row(self.works_button, self.origin_page_button))
        self.cards.addWidget(card)

    def _render_origin(self):
        origin = service.origin()
        self.origin_card.setVisible(bool(origin))
        if not origin:
            return
        info = self._origin_info or {}
        version = origin.get('version')
        self.origin_title.setText(info.get('title') or origin['share_id'])
        details = [f'subtitld.cc/{origin["share_id"]}']
        if version:
            details.append(_('subtitldcc.opened_version').format(version=version))
        latest = info.get('version')
        if latest and version and latest > version:
            details.append(_('subtitldcc.newer_version').format(version=latest))
        self.origin_details.setText(' · '.join(details))
        permissions = info.get('permissions') or {}
        can_rate = bool(permissions.get('can_rate'))
        self.rating_row.setVisible(can_rate)
        mine = (info.get('my_rating') or {}).get('stars') or 0
        for index, star in enumerate(self.stars):
            star.setChecked(index < mine)
        self.works_button.setVisible(bool(info) and service.is_connected() and bool(session.VIDEO.get('filepath')))

    def _load_origin(self):
        origin = service.origin()
        if not origin:
            self._origin_info = None
            self._render_origin()
            return
        share_id = origin['share_id']
        if self._origin_info and self._origin_info.get('share_id') == share_id and self._origin_loading is None:
            self._render_origin()
            return
        if self._origin_loading == share_id:
            return
        self._origin_loading = share_id

        def loaded(info):
            self._origin_loading = None
            self._origin_info = info
            self._render_origin()
            self._render_publish()

        def failed(error):
            self._origin_loading = None
            if service.error_code(error) == 'unauthorized':
                self._token_expired()
            self._origin_info = {'share_id': share_id, 'missing': True}
            self._render_origin()
            self._render_publish()

        service.call(lambda: service.client().asset(share_id), loaded, failed)
        self._render_origin()

    def _open_origin_page(self):
        origin = service.origin()
        if origin:
            path = origin['share_id'] + (f"/v{origin['version']}" if origin.get('version') else '')
            QDesktopServices.openUrl(QUrl(service.site_url(path)))

    def rate(self, stars):
        origin = service.origin()
        if not origin:
            return
        share_id = origin['share_id']

        def rated(_answer):
            info = dict(self._origin_info or {})
            info['my_rating'] = {'stars': stars}
            self._origin_info = info
            self._render_origin()
            self.show_message(_('subtitldcc.rated'))

        service.call(lambda: service.client().rate(share_id, stars), rated, self._failed)

    def confirm_works(self):
        origin = service.origin()
        if not origin:
            return
        self.works_button.setEnabled(False)

        def work():
            video = self._video or service.video_fingerprint(session.VIDEO['filepath'], session.VIDEO.get('duration'))
            return service.client().confirm_match(origin['share_id'], video)

        def done(answer):
            self.works_button.setEnabled(True)
            key = 'subtitldcc.works_counted' if answer.get('counted') else 'subtitldcc.works_already'
            self.show_message(_(key).format(count=answer.get('confirmations', 1)))

        def failed(error):
            self.works_button.setEnabled(True)
            self._failed(error)

        service.call(work, done, failed)

    # --- For this video ----------------------------------------------------------------------------------

    def _build_video(self):
        card, layout = _card()
        header = _row(stretch_after=False)
        self.video_title = _label('subtitldcc_card_title')
        header.layout().addWidget(self.video_title, 1)
        self.lookup_button = _button(small=True)
        self.lookup_button.clicked.connect(self._run_lookup)
        header.layout().addWidget(self.lookup_button)
        layout.addWidget(header)
        self.video_status = _label('subtitldcc_text')
        layout.addWidget(self.video_status)
        self.video_results = QVBoxLayout()
        self.video_results.setSpacing(6)
        layout.addLayout(self.video_results)
        self.auto_lookup = QCheckBox()
        self.auto_lookup.setChecked(bool(service.settings().get('auto_lookup', True)))
        self.auto_lookup.toggled.connect(self._auto_lookup_toggled)
        layout.addWidget(self.auto_lookup)
        self.cards.addWidget(card)

    def _auto_lookup_toggled(self, checked):
        service.settings()['auto_lookup'] = bool(checked)
        if checked and self._matches is None and session.VIDEO.get('filepath'):
            self._run_lookup()
        self._render_video()

    def video_opened(self):
        path = session.VIDEO.get('filepath')
        if path != self._lookup_path:
            self._video, self._matches, self._lookup_error = None, None, None
        self._origin_info = None
        self._render_account(waiting=self._sign_in_call is not None)
        self._load_origin()
        self._render_publish()
        if path and service.settings().get('auto_lookup', True):
            self._run_lookup()
        else:
            self._render_video()

    def _run_lookup(self):
        path = session.VIDEO.get('filepath')
        if not path or (self._lookup_call is not None and self._lookup_path == path):
            return
        self._lookup_path = path
        self._lookup_error = None
        self._matches = None
        duration = session.VIDEO.get('duration')
        known = self._video

        def work():
            video = known or service.video_fingerprint(path, duration)
            matches = service.lookup(video)
            service.meta()  # language names for the results (fetched once)
            return video, matches

        def done(answer):
            if path != session.VIDEO.get('filepath'):
                return  # another video was opened meanwhile
            self._video, self._matches = answer
            self._render_video()

        def failed(error):
            if service.error_code(error) == 'unauthorized':
                self._token_expired()
                self._lookup_call = None
                self._run_lookup()
                return
            self._lookup_error = error
            self._render_video()

        def finished():
            self._lookup_call = None
            self._render_video()

        self._lookup_call = service.call(work, done, failed)
        self._lookup_call.finished.connect(finished)
        self._render_video()

    def _render_video(self):
        _clear(self.video_results)
        looking = self._lookup_call is not None
        self.lookup_button.setEnabled(bool(session.VIDEO.get('filepath')) and not looking)
        matches = self._matches or {}
        exact, probable = matches.get('exact') or [], matches.get('probable') or []
        if not session.VIDEO.get('filepath'):
            status = _('subtitldcc.no_video')
        elif looking:
            status = _('subtitldcc.looking_up')
        elif self._lookup_error is not None:
            status = service.error_message(self._lookup_error, _)
        elif self._matches is None:
            status = _('subtitldcc.lookup_off')
        elif not exact and not probable:
            status = _('subtitldcc.no_matches')
        else:
            status = _('subtitldcc.matches').format(count=len(exact) + len(probable))
        self.video_status.setText(status)
        for result in exact:
            self.video_results.addWidget(ResultRow(self, result, 'exact'))
        for result in probable:
            self.video_results.addWidget(ResultRow(self, result, 'probable'))
        self._mark_navigation(len(exact) + len(probable))

    def _mark_navigation(self, count):
        """Tell about matches on the panel's button in the navigation bar, even while it's not open."""
        for button in self.window_.findChildren(left_panel.navigation_button):
            if button.tab_name == TAB:
                button.setProperty('has_matches', 'true' if count else 'false')
                button.setToolTip(
                    _('subtitldcc.nav_matches').format(count=count) if count else _(f'left_panel.tab_{TAB}'))
                button.style().unpolish(button)
                button.style().polish(button)

    # --- Search ------------------------------------------------------------------------------------------

    def _build_search(self):
        card, layout = _card()
        self.search_title = _label('subtitldcc_card_title')
        layout.addWidget(self.search_title)
        self.search_field = QLineEdit()
        self.search_field.setObjectName('subtitldcc_search')
        self.search_field.setClearButtonEnabled(True)
        self.search_field.returnPressed.connect(self.search)
        self.search_button = _button(small=True)
        self.search_button.clicked.connect(self.search)
        row = _row(stretch_after=False)
        row.layout().addWidget(self.search_field, 1)
        row.layout().addWidget(self.search_button)
        layout.addWidget(row)
        self.search_status = _label('subtitldcc_text')
        self.search_status.hide()
        layout.addWidget(self.search_status)
        self.search_results = QVBoxLayout()
        self.search_results.setSpacing(6)
        layout.addLayout(self.search_results)
        self.more_button = _button(small=True)
        self.more_button.clicked.connect(lambda: self.search(more=True))
        self.more_button.hide()
        layout.addWidget(self.more_button)
        self.cards.addWidget(card)

    def search(self, more=False):
        query = self.search_field.text().strip() if not more else self._search_query
        if not query:
            return
        cursor = self._search_cursor if more else None
        if not more:
            _clear(self.search_results)
            self._search_query = query
        self.search_button.setEnabled(False)
        self.more_button.setEnabled(False)
        self.search_status.setText(_('subtitldcc.searching'))
        self.search_status.show()

        def done(page):
            self.search_button.setEnabled(True)
            self.more_button.setEnabled(True)
            results = page.get('results') or []
            for result in results:
                self.search_results.addWidget(ResultRow(self, result))
            self._search_cursor = page.get('next_cursor')
            self.more_button.setVisible(bool(self._search_cursor))
            total = page.get('total')
            if not more:
                if not results:
                    self.search_status.setText(_('subtitldcc.no_results').format(query=query))
                elif total is not None:
                    self.search_status.setText(_('subtitldcc.results').format(count=f'{total:,}'))
                else:
                    self.search_status.hide()

        def failed(error):
            self.search_button.setEnabled(True)
            self.more_button.setEnabled(True)
            if service.error_code(error) == 'unauthorized':
                self._token_expired()
            self.search_status.setText(service.error_message(error, _))

        service.call(lambda: service.client().search(query, cursor=cursor), done, failed)

    # --- Opening -----------------------------------------------------------------------------------------

    def open_subtitle(self, row):
        result = row.result
        if session.SUBTITLE.get('segments'):
            key = 'subtitldcc.replace_unsaved' if session.UNSAVED else 'subtitldcc.replace_text'
            if not ask(self.window_, _('subtitldcc.replace_title'), _(key).format(title=result.get('title', '')),
                       _('subtitldcc.replace')):
                return
        row.set_busy(True)
        share_id = result['share_id']

        def opened(data):
            row.set_busy(False)
            service.open_in_editor(self.window_, data)
            if not service.origin():
                service.set_origin(share_id, result.get('version'))
            self._origin_info = None
            self._load_origin()
            self._render_publish()
            self.show_message(_('subtitldcc.opened').format(title=result.get('title', share_id)))

        def failed(error):
            row.set_busy(False)
            self._failed(error)

        service.call(lambda: service.download_usf(share_id), opened, failed)

    # --- Publishing --------------------------------------------------------------------------------------

    def _build_publish(self):
        card, layout = _card()
        self.publish_title = _label('subtitldcc_card_title')
        layout.addWidget(self.publish_title)
        self.publish_text = _label('subtitldcc_text')
        layout.addWidget(self.publish_text)
        self.publish_version_button = _button('primary')
        self.publish_version_button.clicked.connect(lambda: self.publish(as_version=True))
        self.publish_new_button = _button()
        self.publish_new_button.clicked.connect(lambda: self.publish(as_version=False))
        layout.addWidget(_row(self.publish_version_button, self.publish_new_button))
        self.cards.addWidget(card)

    def _can_publish_version(self):
        info = self._origin_info or {}
        return bool(service.origin()) and bool((info.get('permissions') or {}).get('can_edit'))

    def _render_publish(self):
        connected = service.is_connected()
        has_text = bool(session.SUBTITLE.get('segments'))
        as_version = connected and self._can_publish_version()
        self.publish_version_button.setVisible(as_version)
        self.publish_new_button.setProperty('class', '' if as_version else 'primary')
        self.publish_new_button.style().unpolish(self.publish_new_button)
        self.publish_new_button.style().polish(self.publish_new_button)
        self.publish_version_button.setEnabled(has_text)
        self.publish_new_button.setEnabled(connected and has_text)
        if service.outdated():
            self.publish_version_button.setEnabled(False)
            self.publish_new_button.setEnabled(False)
            self.publish_text.setText(_('subtitldcc.outdated'))
        elif not connected:
            self.publish_text.setText(_('subtitldcc.publish_connect'))
        elif not has_text:
            self.publish_text.setText(_('subtitldcc.publish_empty'))
        elif as_version:
            info = self._origin_info or {}
            latest = info.get('version') or (service.origin() or {}).get('version') or 0
            self.publish_text.setText(_('subtitldcc.publish_version_text').format(
                title=info.get('title', ''), version=latest + 1))
        else:
            self.publish_text.setText(_('subtitldcc.publish_text'))

    def publish(self, as_version):
        target = None
        if as_version and self._can_publish_version():
            target = dict(self._origin_info, opened_version=(service.origin() or {}).get('version'))
        dialog = PublishDialog(self.window_, target=target, video=self._video)
        dialog.exec()
        if dialog.published:
            share_id, version = dialog.published
            service.set_origin(share_id, version)
            session.set_unsaved(True)  # the origin is new: saving keeps it with the project
            self._origin_info = None
            self._load_origin()
            self._render_publish()
            if self._video and session.VIDEO.get('filepath'):
                self._run_lookup()  # it may be among the matches now, or a version newer

    # --- Shared ------------------------------------------------------------------------------------------

    def shutdown(self):
        """Subtitld is closing: stop waiting for a sign-in and let running calls end."""
        self._cancel_sign_in = True
        service.wait_for_calls()

    def _failed(self, error):
        if service.error_code(error) == 'unauthorized':
            self._token_expired()
            return
        self.show_message(service.error_message(error, _), error=True)

    def show_message(self, text, error=False):
        self.message.setText(text)
        self.message.setProperty('error', 'true' if error else 'false')
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.show()
        self._message_timer.start(8000 if error else 5000)

    def _render_lists(self):
        if self._matches:
            self._render_video()
        self._render_publish()

    def refresh(self):
        """Called when the panel is shown (and after changes). Nothing goes to subtitld.cc before the
        panel is opened or a video is looked up."""
        connected = service.is_connected()
        if self.isVisible():
            if service.cached_meta() is None:
                service.call(service.meta, lambda _meta: self._render_lists())
            if connected and not service.account():
                service.call(lambda: service.client().me(), self._account_loaded, self._account_failed)
        self._render_account(waiting=self._sign_in_call is not None)
        self._load_origin()
        self._render_video()
        self._render_publish()

    def _account_loaded(self, me):
        service.remember_account(me)
        self._render_account()

    def _account_failed(self, error):
        if service.error_code(error) == 'unauthorized':
            self._token_expired()

    def retranslate(self):
        self.account_title.setText('subtitld.cc')
        self.connect_button.setText(_('subtitldcc.connect'))
        self.connect_button.setToolTip(_('subtitldcc.connect_tip'))
        self.code_button.setText(_('subtitldcc.use_code'))
        self.code_button.setToolTip(_('subtitldcc.use_code_tip'))
        self.device_page_button.setText(_('subtitldcc.open_device_page'))
        self.cancel_button.setText(_('subtitldcc.cancel'))
        self.disconnect_button.setText(_('subtitldcc.disconnect'))
        self.origin_kicker.setText(_('subtitldcc.in_editor'))
        self.rate_label.setText(_('subtitldcc.your_rating'))
        self.works_button.setText(_('subtitldcc.works'))
        self.works_button.setToolTip(_('subtitldcc.works_tip'))
        self.origin_page_button.setText(_('subtitldcc.page'))
        self.video_title.setText(_('subtitldcc.for_this_video'))
        self.lookup_button.setText(_('subtitldcc.look_up'))
        self.auto_lookup.setText(_('subtitldcc.auto_lookup'))
        self.auto_lookup.setToolTip(_('subtitldcc.auto_lookup_tip'))
        self.search_title.setText(_('subtitldcc.search_title'))
        self.search_field.setPlaceholderText(_('subtitldcc.search_placeholder'))
        self.search_button.setText(_('subtitldcc.search'))
        self.more_button.setText(_('subtitldcc.more'))
        self.publish_title.setText(_('subtitldcc.publish_title'))
        self.publish_version_button.setText(_('subtitldcc.publish_version'))
        self.publish_new_button.setText(_('subtitldcc.publish_new'))
        self.refresh()
