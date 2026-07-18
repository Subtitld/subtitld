"""Subtitld Cloud account dashboard.

Embedded in the Global settings → "Subtitld Cloud" tab. Implements the
"Option D" handoff design: a single card with three states —

  * **not connected** — centered empty card with a cloud glyph, blurb,
    an API-key field + Connect button, and a "create account" link;
  * **connected** — identity header (avatar, email, status, key/refresh
    icon actions), a balance ring with Top-up / Open-portal buttons, and
    a recent-usage history table;
  * **low balance** — the connected card with the red status treatment.

All account data comes from ``GET /api/v1/account`` via
``subtitld_cloud_shared.fetch_account`` (see that function's docstring
for the payload contract). Every field is optional; the card degrades
gracefully (hides the usage band when empty, shows "—" for an absent
balance, etc.). The fetch runs on a background QThread so the UI never
blocks.

Colors / type / radii follow the app QSS tokens; the bespoke object
names used here are styled in ``graphics/stylesheet.qss`` under the
"Subtitld Cloud dashboard" section.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QSize, QThread, Signal, QRectF, QUrl, QTimer
from PySide6.QtGui import QIcon, QPainter, QPen, QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QSizePolicy,
)

from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import session


def _g(name: str) -> str:
    """Absolute path to a bundled graphic."""
    return os.path.join(session.PATH_SUBTITLD_GRAPHICS, name)


# ---------------------------------------------------------------------------
# Background account fetch
# ---------------------------------------------------------------------------
class _CloudAccountWorker(QThread):
    """Fetches the account summary off the UI thread. Emits exactly one of
    ``loaded`` (account dict) / ``failed`` (reason). ``reason`` is one of
    the symbolic strings below so the panel can branch on cause (bad key
    → empty state, network → keep last view + toast)."""

    loaded = Signal(dict)
    failed = Signal(str)  # 'no_key' | 'bad_key' | 'no_endpoint' | 'http' | 'network'

    def run(self):
        import urllib.error
        from subtitld.modules.addons.builtin import subtitld_cloud_shared as cloud
        if not cloud.is_configured():
            self.failed.emit('no_key')
            return
        try:
            data = cloud.fetch_account()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                self.failed.emit('bad_key')
            elif exc.code == 404:
                self.failed.emit('no_endpoint')
            else:
                self.failed.emit('http')
            return
        except Exception:
            self.failed.emit('network')
            return
        self.loaded.emit(data if isinstance(data, dict) else {})


# ---------------------------------------------------------------------------
# Balance ring — custom-painted donut with a centered value/label stack
# ---------------------------------------------------------------------------
class _BalanceRing(QWidget):
    """78×78 donut. Track + progress arc (starts at 12 o'clock, clockwise),
    value + unit stacked in the center. ``fraction`` is 0..1; ``accent``
    lets the low-balance state recolor the arc + value red."""

    _SIZE = 78
    _RADIUS = 30
    _STROKE = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self._SIZE, self._SIZE)
        self._fraction = 0.62
        self._value = '—'
        self._unit = ''
        self._accent = QColor('#b8cee0')
        self._value_color = QColor('#ffffff')

    def set_data(self, fraction, value, unit, accent='#b8cee0', value_color='#ffffff'):
        self._fraction = max(0.0, min(1.0, float(fraction)))
        self._value = value
        self._unit = unit
        self._accent = QColor(accent)
        self._value_color = QColor(value_color)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx = cy = self._SIZE / 2.0
        rect = QRectF(cx - self._RADIUS, cy - self._RADIUS,
                      self._RADIUS * 2, self._RADIUS * 2)

        # Track.
        track = QPen(QColor(0, 0, 0, 77), self._STROKE)  # rgba(0,0,0,.3)
        track.setCapStyle(Qt.FlatCap)
        p.setPen(track)
        p.drawArc(rect, 0, 360 * 16)

        # Progress arc — start at 12 o'clock (90°), sweep clockwise.
        if self._fraction > 0:
            arc = QPen(self._accent, self._STROKE)
            arc.setCapStyle(Qt.RoundCap)
            p.setPen(arc)
            span = int(-360 * self._fraction * 16)
            p.drawArc(rect, 90 * 16, span)

        # Center value.
        p.setPen(self._value_color)
        vf = QFont('Montserrat')
        vf.setPixelSize(15)
        vf.setWeight(QFont.Bold)
        p.setFont(vf)
        value_rect = QRectF(0, cy - 16, self._SIZE, 18)
        p.drawText(value_rect, Qt.AlignHCenter | Qt.AlignBottom, str(self._value))

        # Center unit.
        if self._unit:
            p.setPen(QColor(184, 206, 224, 128))  # rgba(184,206,224,.5)
            uf = QFont('Montserrat')
            uf.setPixelSize(8)
            uf.setWeight(QFont.Bold)
            uf.setLetterSpacing(QFont.AbsoluteSpacing, 0.6)
            p.setFont(uf)
            unit_rect = QRectF(0, cy + 1, self._SIZE, 12)
            p.drawText(unit_rect, Qt.AlignHCenter | Qt.AlignTop, self._unit.upper())
        p.end()


# ---------------------------------------------------------------------------
# Small builders
# ---------------------------------------------------------------------------
def _icon_button(icon_file, tooltip):
    btn = QPushButton()
    btn.setObjectName('cloud_icon_button')
    btn.setIcon(QIcon(_g(icon_file)))
    btn.setIconSize(QSize(14, 14))
    btn.setFixedSize(QSize(28, 28))
    btn.setFlat(True)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setToolTip(tooltip)
    return btn


def _mark_label(svg_file):
    lbl = QLabel()
    lbl.setPixmap(QIcon(_g(svg_file)).pixmap(QSize(14, 14)))
    lbl.setFixedSize(QSize(16, 14))
    return lbl


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------
class CloudDashboardPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('cloud_dashboard_panel')
        self._worker = None
        self._account = {}

        # Plain vertical layout with the two state cards stacked and a
        # trailing stretch (top-aligns the card). We toggle visibility
        # rather than use QStackedLayout — the latter caches the hidden
        # page's size hint, so usage rows appended while the connected
        # card is hidden render at zero height until a manual relayout.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._connected_card = self._build_connected_card()
        self._empty_card = self._build_empty_card()
        self._loading_card = self._build_loading_card()
        self._error_card = self._build_error_card()
        root.addWidget(self._connected_card)
        root.addWidget(self._empty_card)
        root.addWidget(self._loading_card)
        root.addWidget(self._error_card)
        root.addStretch()

        # Animated ellipsis on the loading message so it never reads as
        # frozen during a slow fetch.
        self._loading_dots = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(400)
        self._loading_timer.timeout.connect(self._tick_loading)

        self.retranslate()

        # Decide the initial view without a network call; refresh() (on
        # show) updates it once the fetch returns.
        self._show_initial_state()

    # ---- state plumbing --------------------------------------------------
    def _set_state(self, widget):
        for card in (self._connected_card, self._empty_card,
                     self._loading_card, self._error_card):
            card.setVisible(card is widget)
        if widget is self._loading_card:
            self._loading_dots = 0
            self._tick_loading()
            self._loading_timer.start()
        else:
            self._loading_timer.stop()

    def _tick_loading(self):
        self._loading_dots = (self._loading_dots + 1) % 4
        self._loading_label.setText(
            _('cloud_dashboard.loading') + '.' * self._loading_dots
        )

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _show_initial_state(self):
        from subtitld.modules.addons.builtin import subtitld_cloud_shared as cloud
        # Configured → go straight to the loading card; showEvent fires
        # refresh() which keeps it until the fetch returns. Not
        # configured → the connect/empty card.
        self._set_state(self._loading_card if cloud.is_configured() else self._empty_card)

    def refresh(self):
        from subtitld.modules.addons.builtin import subtitld_cloud_shared as cloud
        if not cloud.is_configured():
            self._set_state(self._empty_card)
            return
        # One request at a time. A live ``self._worker`` means a fetch is
        # already running (we only clear it from the thread's ``finished``
        # signal — see _on_worker_finished).
        if self._worker is not None:
            return
        # First fetch (no cached account) → show the loading card instead
        # of a blank connected card. A re-fetch over existing data keeps
        # the current dashboard visible (the disabled refresh button is
        # the only "busy" cue), so the numbers don't flicker to a spinner.
        if not self._account:
            self._set_state(self._loading_card)
        self._refresh_button.setEnabled(False)
        worker = _CloudAccountWorker()
        worker.loaded.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        # Lifecycle: clear our reference + delete the QThread only once
        # it has truly finished. Dropping the last reference from inside
        # ``loaded``/``failed`` (which fire while run() is still on the
        # stack) destroys the QThread mid-run → "QThread: Destroyed while
        # thread is still running" → hard crash. ``finished`` fires after
        # run() returns, so cleanup here is safe.
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_worker_finished(self):
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self._refresh_button.setEnabled(True)

    def _on_loaded(self, data):
        self._account = data or {}
        self._populate_connected(self._account)
        self._set_state(self._connected_card)

    def _on_failed(self, reason):
        if reason in ('no_key', 'bad_key'):
            # No usable key → drop to the connect/empty state, with an
            # inline error when the key was present but rejected.
            self._empty_error.setText(
                _('cloud_dashboard.error_bad_key') if reason == 'bad_key' else ''
            )
            self._empty_error.setVisible(reason == 'bad_key')
            self._set_state(self._empty_card)
            return
        # network / http / no_endpoint — the server is unreachable or
        # erroring. If we already have a populated dashboard, keep it
        # (a transient blip shouldn't wipe good data); otherwise show the
        # error card with a Retry button instead of a blank shell.
        if self._account:
            self._set_state(self._connected_card)
            return
        self._error_body.setText(_({
            'http': 'cloud_dashboard.error_server',
            'no_endpoint': 'cloud_dashboard.error_unavailable',
        }.get(reason, 'cloud_dashboard.error_network')))
        self._set_state(self._error_card)

    # ---- connected card --------------------------------------------------
    def _build_connected_card(self):
        card = QWidget()
        card.setObjectName('cloud_card')
        card.setAttribute(Qt.WA_StyledBackground, True)
        # Preferred (not Maximum) vertical: the root layout's trailing
        # stretch squeezes a Maximum-policy widget toward its minimum
        # hint, which collapses wrapped/variable content. Preferred lets
        # the stretch absorb the extra while the card keeps its sizeHint.
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- Band 1: identity header ---
        header = QWidget()
        header.setObjectName('cloud_header_band')
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 12, 12, 12)
        h.setSpacing(11)

        self._avatar = QLabel()
        self._avatar.setObjectName('cloud_avatar')
        self._avatar.setFixedSize(QSize(30, 30))
        self._avatar.setAlignment(Qt.AlignCenter)
        h.addWidget(self._avatar)

        ident = QVBoxLayout()
        ident.setContentsMargins(0, 0, 0, 0)
        ident.setSpacing(3)

        # Email + API-key-name badge on one row. The badge names which
        # key is in use (from the account payload's ``key_name``); hidden
        # when the cloud doesn't send one.
        email_row = QHBoxLayout()
        email_row.setContentsMargins(0, 0, 0, 0)
        email_row.setSpacing(7)
        self._email_label = QLabel()
        self._email_label.setObjectName('cloud_email')
        email_row.addWidget(self._email_label)
        self._key_badge = QLabel()
        self._key_badge.setObjectName('cloud_key_badge')
        self._key_badge.setVisible(False)
        email_row.addWidget(self._key_badge)
        email_row.addStretch()
        ident.addLayout(email_row)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)
        self._status_dot = QLabel()
        self._status_dot.setObjectName('cloud_status_dot')
        self._status_dot.setFixedSize(QSize(7, 7))
        self._status_label = QLabel()
        self._status_label.setObjectName('cloud_status_label')
        status_row.addWidget(self._status_dot)
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        ident.addLayout(status_row)
        h.addLayout(ident, 1)

        self._key_button = _icon_button('cloud_key_icon.svg', _('cloud_dashboard.change_key'))
        self._key_button.clicked.connect(self._change_api_key)
        self._refresh_button = _icon_button('cloud_refresh_icon.svg', _('cloud_dashboard.refresh'))
        self._refresh_button.clicked.connect(self.refresh)
        h.addWidget(self._key_button)
        h.addWidget(self._refresh_button)
        outer.addWidget(header)

        # --- Band 2: balance ring + actions ---
        balance = QWidget()
        balance.setObjectName('cloud_balance_band')
        b = QHBoxLayout(balance)
        b.setContentsMargins(16, 18, 16, 18)
        b.setSpacing(18)
        self._ring = _BalanceRing()
        b.addWidget(self._ring)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self._topup_button = QPushButton()
        self._topup_button.setProperty('class', 'secondary')
        self._topup_button.setCursor(Qt.PointingHandCursor)
        self._topup_button.clicked.connect(self._open_topup)
        self._portal_button = QPushButton()
        self._portal_button.setCursor(Qt.PointingHandCursor)
        self._portal_button.clicked.connect(self._open_portal)
        actions.addWidget(self._topup_button, 1)
        actions.addWidget(self._portal_button)
        b.addLayout(actions, 1)
        outer.addWidget(balance)

        # --- Band 3: usage history ---
        self._usage_container = QWidget()
        self._usage_container.setObjectName('cloud_usage_container')
        u = QVBoxLayout(self._usage_container)
        u.setContentsMargins(0, 0, 0, 0)
        u.setSpacing(0)

        usage_header = QWidget()
        usage_header.setObjectName('cloud_usage_header_band')
        uh = QHBoxLayout(usage_header)
        uh.setContentsMargins(16, 9, 16, 9)
        uh.setSpacing(8)
        self._usage_title = QLabel()
        self._usage_title.setObjectName('cloud_wlabel')
        self._usage_period = QLabel()
        self._usage_period.setObjectName('cloud_subtle_label')
        uh.addWidget(self._usage_title)
        uh.addStretch()
        uh.addWidget(self._usage_period)
        u.addWidget(usage_header)

        self._usage_rows = QWidget()
        self._usage_rows.setObjectName('cloud_usage_rows')
        self._usage_rows_layout = QVBoxLayout(self._usage_rows)
        self._usage_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._usage_rows_layout.setSpacing(0)
        u.addWidget(self._usage_rows)
        outer.addWidget(self._usage_container)
        return card

    def _populate_connected(self, data):
        email = str(data.get('email') or '')
        self._avatar.setText((email[:1] or '?').upper())
        self._email_label.setText(email or '—')

        # API-key-name badge — names which key is active (e.g. "Laptop",
        # "CI"). Cloud sends it as ``key_name``; hide the badge if absent.
        key_name = str(data.get('key_name') or '').strip()
        self._key_badge.setText(key_name)
        self._key_badge.setVisible(bool(key_name))

        low = self._is_low_balance(data)
        self._status_dot.setProperty('state', 'low' if low else 'ok')
        self._status_dot.style().unpolish(self._status_dot)
        self._status_dot.style().polish(self._status_dot)
        self._status_label.setText(
            _('cloud_dashboard.status_low') if low else _('cloud_dashboard.status_connected')
        )
        self._status_label.setProperty('state', 'low' if low else 'ok')
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)

        # Ring: value + unit + fill.
        balance = data.get('balance')
        currency = str(data.get('currency') or 'USD')
        if balance is not None:
            try:
                value = f'${float(balance):.2f}'
            except (TypeError, ValueError):
                value = str(balance)
        elif data.get('credits') is not None:
            value = str(data.get('credits'))
            currency = _('cloud_dashboard.credits_unit')
        else:
            value = '—'
            currency = ''
        frac = data.get('ring_fraction')
        if frac is None:
            frac = 0.0 if balance in (None, 0) else 0.62
        accent = '#c55252' if low else '#b8cee0'
        value_color = '#c55252' if low else '#ffffff'
        self._ring.set_data(frac, value, currency, accent=accent, value_color=value_color)

        self._populate_usage(data.get('usage') or [])

        # Rows are appended above; if the card was hidden at the time
        # (e.g. populated from _on_loaded before _set_state shows it),
        # the layout chain caches the pre-population size hint and the
        # usage band renders at zero height. Walk the chain invalidating
        # so the card claims its full height on the next layout pass.
        for w in (self._usage_rows, self._usage_container, self._connected_card):
            if w.layout() is not None:
                w.layout().invalidate()
            w.updateGeometry()

    def _is_low_balance(self, data):
        if 'low_balance' in data:
            return bool(data.get('low_balance'))
        bal = data.get('balance')
        try:
            return bal is not None and float(bal) < 1.0
        except (TypeError, ValueError):
            return False

    def _populate_usage(self, usage):
        # Clear existing rows.
        while self._usage_rows_layout.count():
            item = self._usage_rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not usage:
            self._usage_container.setVisible(False)
            return
        self._usage_container.setVisible(True)
        for i, job in enumerate(usage):
            self._usage_rows_layout.addWidget(
                self._build_usage_row(job, last=(i == len(usage) - 1))
            )

    def _build_usage_row(self, job, last):
        row = QWidget()
        row.setObjectName('cloud_usage_row')
        row.setProperty('last', 'yes' if last else 'no')
        r = QHBoxLayout(row)
        r.setContentsMargins(16, 9, 16, 9)
        r.setSpacing(12)

        kind = str(job.get('kind') or '')
        mark = 'cloud_wave_mark.svg' if kind.lower().startswith('transcription') else 'cloud_dub_mark.svg'
        r.addWidget(_mark_label(mark))

        file_label = QLabel(str(job.get('file') or ''))
        file_label.setObjectName('cloud_usage_file')
        file_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        r.addWidget(file_label, 1)

        minutes = job.get('minutes')
        if minutes is not None:
            dur = QLabel(_('cloud_dashboard.minutes').format(n=minutes))
            dur.setObjectName('cloud_usage_duration')
            r.addWidget(dur)

        cost = job.get('cost')
        if cost is not None:
            try:
                cost_text = f'−${float(cost):.2f}'
            except (TypeError, ValueError):
                cost_text = str(cost)
            cost_label = QLabel(cost_text)
            cost_label.setObjectName('cloud_usage_cost')
            cost_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            r.addWidget(cost_label)
        return row

    # ---- empty / not-connected card --------------------------------------
    def _build_empty_card(self):
        card = QWidget()
        card.setObjectName('cloud_card')
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        c = QVBoxLayout(card)
        c.setContentsMargins(24, 28, 24, 28)
        c.setSpacing(0)
        c.setAlignment(Qt.AlignHCenter)

        tile = QLabel()
        tile.setObjectName('cloud_icon_tile')
        tile.setFixedSize(QSize(44, 44))
        tile.setAlignment(Qt.AlignCenter)
        tile.setPixmap(QIcon(_g('cloud_glyph_icon.svg')).pixmap(QSize(22, 22)))
        c.addWidget(tile, 0, Qt.AlignHCenter)
        c.addSpacing(16)

        self._empty_title = QLabel()
        self._empty_title.setObjectName('cloud_empty_title')
        self._empty_title.setAlignment(Qt.AlignHCenter)
        c.addWidget(self._empty_title)
        c.addSpacing(8)

        self._empty_body = QLabel()
        self._empty_body.setObjectName('cloud_empty_body')
        self._empty_body.setAlignment(Qt.AlignHCenter)
        self._empty_body.setWordWrap(True)
        # Fixed width so heightForWidth is deterministic — a wrapped
        # QLabel added with AlignHCenter otherwise shrinks to a narrow
        # sizeHint width and clips. 320px matches the design max-width.
        self._empty_body.setFixedWidth(320)
        c.addWidget(self._empty_body, 0, Qt.AlignHCenter)
        c.addSpacing(18)

        connect_row = QHBoxLayout()
        connect_row.setContentsMargins(0, 0, 0, 0)
        connect_row.setSpacing(8)
        connect_row.addStretch()
        self._empty_key_input = QLineEdit()
        self._empty_key_input.setObjectName('cloud_key_field')
        self._empty_key_input.setEchoMode(QLineEdit.Password)
        self._empty_key_input.setMaximumWidth(240)
        self._empty_key_input.returnPressed.connect(self._connect_from_empty)
        connect_row.addWidget(self._empty_key_input, 1)
        self._empty_connect_button = QPushButton()
        self._empty_connect_button.setProperty('class', 'secondary')
        self._empty_connect_button.setCursor(Qt.PointingHandCursor)
        self._empty_connect_button.clicked.connect(self._connect_from_empty)
        connect_row.addWidget(self._empty_connect_button)
        connect_row.addStretch()
        c.addLayout(connect_row)

        self._empty_error = QLabel()
        self._empty_error.setObjectName('cloud_empty_error')
        self._empty_error.setAlignment(Qt.AlignHCenter)
        self._empty_error.setWordWrap(True)
        self._empty_error.setVisible(False)
        c.addSpacing(8)
        c.addWidget(self._empty_error, 0, Qt.AlignHCenter)

        c.addSpacing(14)
        self._empty_signup_button = QPushButton()
        self._empty_signup_button.setObjectName('cloud_link_button')
        self._empty_signup_button.setFlat(True)
        self._empty_signup_button.setCursor(Qt.PointingHandCursor)
        self._empty_signup_button.clicked.connect(self._open_signup)
        c.addWidget(self._empty_signup_button, 0, Qt.AlignHCenter)
        return card

    # ---- loading card ----------------------------------------------------
    def _build_loading_card(self):
        card = QWidget()
        card.setObjectName('cloud_card')
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        c = QVBoxLayout(card)
        c.setContentsMargins(24, 32, 24, 32)
        c.setSpacing(0)
        c.setAlignment(Qt.AlignHCenter)

        tile = QLabel()
        tile.setObjectName('cloud_icon_tile')
        tile.setFixedSize(QSize(44, 44))
        tile.setAlignment(Qt.AlignCenter)
        tile.setPixmap(QIcon(_g('cloud_glyph_icon.svg')).pixmap(QSize(22, 22)))
        c.addWidget(tile, 0, Qt.AlignHCenter)
        c.addSpacing(16)

        self._loading_label = QLabel()
        self._loading_label.setObjectName('cloud_empty_body')
        self._loading_label.setAlignment(Qt.AlignHCenter)
        c.addWidget(self._loading_label, 0, Qt.AlignHCenter)
        return card

    # ---- error / unreachable card ----------------------------------------
    def _build_error_card(self):
        card = QWidget()
        card.setObjectName('cloud_card')
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        c = QVBoxLayout(card)
        c.setContentsMargins(24, 28, 24, 28)
        c.setSpacing(0)
        c.setAlignment(Qt.AlignHCenter)

        tile = QLabel()
        tile.setObjectName('cloud_icon_tile')
        tile.setFixedSize(QSize(44, 44))
        tile.setAlignment(Qt.AlignCenter)
        tile.setPixmap(QIcon(_g('cloud_glyph_icon.svg')).pixmap(QSize(22, 22)))
        c.addWidget(tile, 0, Qt.AlignHCenter)
        c.addSpacing(16)

        self._error_title = QLabel()
        self._error_title.setObjectName('cloud_empty_title')
        self._error_title.setAlignment(Qt.AlignHCenter)
        c.addWidget(self._error_title)
        c.addSpacing(8)

        self._error_body = QLabel()
        self._error_body.setObjectName('cloud_empty_body')
        self._error_body.setAlignment(Qt.AlignHCenter)
        self._error_body.setWordWrap(True)
        self._error_body.setFixedWidth(320)
        c.addWidget(self._error_body, 0, Qt.AlignHCenter)
        c.addSpacing(18)

        self._error_retry_button = QPushButton()
        self._error_retry_button.setProperty('class', 'secondary')
        self._error_retry_button.setCursor(Qt.PointingHandCursor)
        self._error_retry_button.clicked.connect(self.refresh)
        c.addWidget(self._error_retry_button, 0, Qt.AlignHCenter)
        return card

    def _connect_from_empty(self):
        from subtitld.modules.addons.builtin import subtitld_cloud_shared as cloud
        key = self._empty_key_input.text().strip()
        if not key:
            return
        cloud.write_config(api_key=key)
        self._empty_error.setVisible(False)
        self._empty_key_input.clear()  # never keep the raw key on screen
        self.refresh()

    # ---- actions ---------------------------------------------------------
    def _change_api_key(self):
        from subtitld.modules.addons.builtin import subtitld_cloud_shared as cloud
        dialog = utils.SimpleDialog(self.window(), title=_('cloud_dashboard.change_key'))
        field = QLineEdit()
        field.setObjectName('cloud_key_field')
        field.setEchoMode(QLineEdit.Password)
        field.setPlaceholderText(_('cloud_dashboard.key_placeholder'))
        field.setText(cloud.read_api_key())
        dialog.content.layout().addWidget(field)
        field.returnPressed.connect(dialog.accept)
        if dialog.exec():
            cloud.write_config(api_key=field.text().strip())
            self.refresh()

    def _open_portal(self):
        from subtitld.modules.addons.builtin import subtitld_cloud_shared as cloud
        QDesktopServices.openUrl(QUrl(cloud.read_dashboard_url()))

    def _open_topup(self):
        from subtitld.modules.addons.builtin import subtitld_cloud_shared as cloud
        QDesktopServices.openUrl(QUrl(cloud.read_topup_url()))

    def _open_signup(self):
        from subtitld.modules.addons.builtin import subtitld_cloud_shared as cloud
        QDesktopServices.openUrl(QUrl(cloud.read_signup_url()))

    # ---- i18n ------------------------------------------------------------
    def retranslate(self):
        self._status_label.setText(_('cloud_dashboard.status_connected'))
        self._topup_button.setText(_('cloud_dashboard.top_up'))
        self._portal_button.setText(_('cloud_dashboard.open_portal'))
        self._usage_title.setText(_('cloud_dashboard.recent_usage'))
        self._usage_period.setText(_('cloud_dashboard.last_7_days'))
        self._key_button.setToolTip(_('cloud_dashboard.change_key'))
        self._refresh_button.setToolTip(_('cloud_dashboard.refresh'))
        self._empty_title.setText(_('cloud_dashboard.connect_title'))
        self._empty_body.setText(_('cloud_dashboard.connect_body'))
        self._empty_key_input.setPlaceholderText(_('cloud_dashboard.key_placeholder'))
        self._empty_connect_button.setText(_('cloud_dashboard.connect'))
        self._empty_signup_button.setText(_('cloud_dashboard.create_account'))
        self._loading_label.setText(_('cloud_dashboard.loading'))
        self._error_title.setText(_('cloud_dashboard.error_title'))
        if not self._error_body.text():
            self._error_body.setText(_('cloud_dashboard.error_network'))
        self._error_retry_button.setText(_('cloud_dashboard.retry'))
