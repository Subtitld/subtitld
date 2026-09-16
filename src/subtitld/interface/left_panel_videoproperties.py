"""Left-panel "Video properties" tab.

Two tabs: **Source** shows the loaded video's facts as cards, and **Proxy**
creates a downscaled, video-only H.264 stand-in for large (4K/8K) files and
edits against it. Only the preview picture uses the proxy — Subtitld's audio
engine, the timeline and the waveform keep using the original, so timing stays
exact.
"""

import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
    QButtonGroup, QTabWidget, QScrollArea, QGroupBox, QLayout, QSizePolicy,
    QStackedWidget, QGridLayout,
)
from PySide6.QtCore import Qt, QRect, QRectF, QSize, QPoint, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import proxy


_TAB_SOURCE = 0
_TAB_PROXY = 1

# Every card shares one height, so a row of them reads as one strip. The
# resolution card sets it: title, then the frame drawing.
_CARD_HEIGHT = 124
_FRAME_AREA = QSize(170, 80)

_STATUS_PAGE_TEXT = 0
_STATUS_PAGE_PROGRESS = 1
# Narrowest the status text may get before the row folds onto two lines.
_STATUS_MIN_WIDTH = 150
_ROW_SPACING = 14


def _fmt_duration(seconds):
    seconds = int(seconds or 0)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}'


def _fmt_size(num_bytes):
    mb = float(num_bytes or 0) / (1024 * 1024)
    if mb >= 1024:
        return f'{mb / 1024:.1f} GB'
    return f'{mb:.1f} MB'


def _selected_scale(self):
    try:
        return int(session.CONFIG.get('proxy', {}).get('scale', 50))
    except (TypeError, ValueError):
        return 50


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------
class _FlowLayout(QLayout):
    """Lays cards out left to right and wraps them when the panel is narrow."""

    def __init__(self, parent=None, spacing=10):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _arrange(self, rect, apply):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_height = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x, y = area.x(), y + line_height + self._spacing
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()


class _ResolutionFrame(QWidget):
    """A frame drawn in the video's own aspect ratio, labelled with its width
    along the top edge and its height along the right edge."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._w = 0
        self._h = 0
        self._fit()

    def set_dimensions(self, width, height):
        self._w, self._h = int(width or 0), int(height or 0)
        self._fit()
        self.update()

    def _ratio(self):
        ratio = (self._w / self._h) if self._w > 0 and self._h > 0 else 16 / 9
        # Clamp so extreme ratios still leave room for both labels.
        return min(max(ratio, 0.5), 4.0)

    def _fit(self):
        # The widget IS the frame, so the card around it hugs the drawing.
        ratio = self._ratio()
        width = min(_FRAME_AREA.width(), round(_FRAME_AREA.height() * ratio))
        self.setFixedSize(QSize(width, round(width / ratio)))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        p.setPen(QPen(QColor(184, 206, 224, 64), 1))
        p.setBrush(QColor(10, 14, 18, 70))
        p.drawRoundedRect(box, 3, 3)

        if self._w <= 0 or self._h <= 0:
            return
        font = QFont(self.font())
        font.setFamily('Montserrat')
        font.setPixelSize(9)
        font.setWeight(QFont.Medium)
        p.setFont(font)
        p.setPen(QColor('#c8dbe9'))
        inner = box.adjusted(6, 5, -6, -5)
        p.drawText(inner, Qt.AlignHCenter | Qt.AlignTop, f'{self._w}px')
        p.drawText(inner, Qt.AlignRight | Qt.AlignVCenter, f'{self._h}px')


class _ElidedLabel(QLabel):
    """One line that ends in an ellipsis instead of widening its row."""

    def minimumSizeHint(self):
        return QSize(1, super().minimumSizeHint().height())

    def sizeHint(self):
        return QSize(1, super().sizeHint().height())

    def paintEvent(self, event):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, rect.width())
        p.drawText(rect, int(self.alignment()), text)


class _ControlsRow(QWidget):
    """Scale strip, status and actions on one line. On a panel too narrow for
    that, the status and actions fold below the strip (and, narrower still,
    below each other) instead of pushing the tab wider than the panel."""

    _ONE_LINE, _TWO_LINES, _THREE_LINES = range(3)

    def __init__(self, segments, status, actions, parent=None):
        super().__init__(parent)
        self._segments, self._status, self._actions = segments, status, actions
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(_ROW_SPACING)
        self._grid.setVerticalSpacing(10)
        self._mode = None
        self._place(self._ONE_LINE)

    def _widths(self):
        seg = self._segments.sizeHint().width()
        act = self._actions.sizeHint().width()
        return (seg + _STATUS_MIN_WIDTH + act + 2 * _ROW_SPACING,
                max(seg, _STATUS_MIN_WIDTH + _ROW_SPACING + act),
                max(seg, act))

    def _place(self, mode):
        if mode == self._mode:
            return
        self._mode = mode
        grid = self._grid
        for widget in (self._segments, self._status, self._actions):
            grid.removeWidget(widget)
        for column in range(3):
            grid.setColumnStretch(column, 0)
        left = Qt.AlignLeft | Qt.AlignVCenter
        if mode == self._ONE_LINE:
            grid.addWidget(self._segments, 0, 0, Qt.AlignVCenter)
            grid.addWidget(self._status, 0, 1, Qt.AlignVCenter)
            grid.addWidget(self._actions, 0, 2, Qt.AlignVCenter)
            grid.setColumnStretch(1, 1)
        elif mode == self._TWO_LINES:
            grid.addWidget(self._segments, 0, 0, 1, 2, left)
            grid.addWidget(self._status, 1, 0, Qt.AlignVCenter)
            grid.addWidget(self._actions, 1, 1, Qt.AlignVCenter)
            grid.setColumnStretch(0, 1)
        else:
            grid.addWidget(self._segments, 0, 0, left)
            grid.addWidget(self._status, 1, 0)
            grid.addWidget(self._actions, 2, 0, left)
            grid.setColumnStretch(0, 1)

    def resizeEvent(self, event):
        one_line, two_lines, _three = self._widths()
        width = event.size().width()
        self._place(self._ONE_LINE if width >= one_line
                    else self._TWO_LINES if width >= two_lines
                    else self._THREE_LINES)
        super().resizeEvent(event)

    def minimumSizeHint(self):
        return QSize(self._widths()[2], super().minimumSizeHint().height())


def _card(object_name):
    card = QGroupBox()
    card.setObjectName(object_name)
    card.setProperty('class', 'videoproperties_card')
    card.setFixedHeight(_CARD_HEIGHT)
    card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    card.setLayout(QVBoxLayout())
    card.layout().setContentsMargins(0, 0, 0, 0)
    card.layout().setSpacing(0)
    return card


def _resolution_card(object_name):
    card = _card(object_name)
    card.frame = _ResolutionFrame()
    card.layout().addWidget(card.frame, 0, Qt.AlignLeft | Qt.AlignVCenter)
    return card


def _value_card(object_name):
    card = _card(object_name)
    card.value = QLabel()
    card.value.setObjectName('videoproperties_card_value')
    card.layout().addWidget(card.value, 0, Qt.AlignLeft | Qt.AlignVCenter)
    return card


def _scroll_tab():
    """A tab whose content scrolls, like the metadata panel's tabs, so a
    narrow panel scrolls instead of squashing its rows."""
    tab = QWidget()
    tab.setProperty('class', 'transparent_panel')
    tab.setLayout(QVBoxLayout())
    tab.layout().setContentsMargins(0, 0, 0, 0)
    tab.layout().setSpacing(0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    tab.layout().addWidget(scroll)

    inner = QWidget()
    inner.setProperty('class', 'transparent_panel')
    body = QVBoxLayout(inner)
    body.setContentsMargins(14, 14, 14, 14)
    body.setSpacing(12)
    scroll.setWidget(inner)
    return tab, body


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
def _build_source_tab(self):
    tab, body = _scroll_tab()

    self.videoproperties_no_video_label = QLabel()
    self.videoproperties_no_video_label.setObjectName('videoproperties_hint')
    body.addWidget(self.videoproperties_no_video_label)

    self.videoproperties_source_cards = QWidget()
    self.videoproperties_source_cards.setProperty('class', 'transparent_panel')
    cards = _FlowLayout(self.videoproperties_source_cards)
    self.videoproperties_resolution_card = _resolution_card('videoproperties_resolution_card')
    self.videoproperties_framerate_card = _value_card('videoproperties_framerate_card')
    self.videoproperties_duration_card = _value_card('videoproperties_duration_card')
    self.videoproperties_codec_card = _value_card('videoproperties_codec_card')
    for card in (self.videoproperties_resolution_card, self.videoproperties_framerate_card,
                 self.videoproperties_duration_card, self.videoproperties_codec_card):
        cards.addWidget(card)
    body.addWidget(self.videoproperties_source_cards)

    body.addStretch()
    return tab


def _build_proxy_tab(self):
    tab, body = _scroll_tab()

    self.videoproperties_proxy_help = QLabel()
    self.videoproperties_proxy_help.setObjectName('videoproperties_help')
    self.videoproperties_proxy_help.setWordWrap(True)
    body.addWidget(self.videoproperties_proxy_help)

    # Scale: one joined, exclusive strip of buttons.
    segments = QWidget()
    segments.setObjectName('videoproperties_scale_segments')
    segments.setAttribute(Qt.WA_StyledBackground, True)
    segments.setLayout(QHBoxLayout())
    segments.layout().setContentsMargins(1, 1, 1, 1)
    segments.layout().setSpacing(0)
    self.videoproperties_scale_group = QButtonGroup(self)
    self.videoproperties_scale_group.setExclusive(True)
    self.videoproperties_scale_buttons = {}
    for index, sc in enumerate(proxy.SCALES):
        button = QPushButton(f'{sc}%')
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setProperty('segment', 'first' if index == 0
                           else 'last' if index == len(proxy.SCALES) - 1 else 'middle')
        self.videoproperties_scale_group.addButton(button)
        button.toggled.connect(lambda checked, s=sc: _scale_toggled(self, s, checked))
        self.videoproperties_scale_buttons[sc] = button
        segments.layout().addWidget(button)

    # Status: a bold state line over a detail line, or over the progress bar
    # while encoding. The two share one slot so the row never re-lays out.
    status = QWidget()
    status.setProperty('class', 'transparent_panel')
    status.setLayout(QVBoxLayout())
    status.layout().setContentsMargins(0, 0, 0, 0)
    status.layout().setSpacing(1)
    self.videoproperties_status_title = QLabel()
    self.videoproperties_status_title.setObjectName('videoproperties_status_title')
    status.layout().addWidget(self.videoproperties_status_title)
    self.videoproperties_status_detail_stack = QStackedWidget()
    self.videoproperties_status_detail = _ElidedLabel()
    self.videoproperties_status_detail.setObjectName('videoproperties_status_detail')
    self.videoproperties_status_detail_stack.addWidget(self.videoproperties_status_detail)
    self.videoproperties_progress = QProgressBar()
    self.videoproperties_progress.setObjectName('videoproperties_progress')
    self.videoproperties_progress.setTextVisible(False)
    progress_page = QWidget()
    progress_page.setLayout(QVBoxLayout())
    progress_page.layout().setContentsMargins(0, 0, 0, 0)
    progress_page.layout().addWidget(self.videoproperties_progress, 0, Qt.AlignVCenter)
    self.videoproperties_status_detail_stack.addWidget(progress_page)
    self.videoproperties_status_detail_stack.setFixedHeight(
        self.videoproperties_status_detail.sizeHint().height())
    status.layout().addWidget(self.videoproperties_status_detail_stack)
    status.setMinimumWidth(1)
    status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    actions = QWidget()
    actions.setProperty('class', 'transparent_panel')
    actions.setLayout(QHBoxLayout())
    actions.layout().setContentsMargins(0, 0, 0, 0)
    actions.layout().setSpacing(8)
    self.videoproperties_primary_button = QPushButton()
    self.videoproperties_primary_button.clicked.connect(lambda: _primary_clicked(self))
    actions.layout().addWidget(self.videoproperties_primary_button)

    self.videoproperties_delete_button = QPushButton()
    self.videoproperties_delete_button.setObjectName('videoproperties_delete_button')
    self.videoproperties_delete_button.setCursor(Qt.PointingHandCursor)
    self.videoproperties_delete_button.setIconSize(QSize(16, 16))
    self.videoproperties_delete_button.setFixedSize(QSize(34, 34))
    self.videoproperties_delete_button.clicked.connect(lambda: _delete_clicked(self))
    # Keep the slot when hidden, so the buttons beside it don't shift.
    policy = self.videoproperties_delete_button.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    self.videoproperties_delete_button.setSizePolicy(policy)
    actions.layout().addWidget(self.videoproperties_delete_button)

    self.videoproperties_controls_row = _ControlsRow(segments, status, actions)
    body.addWidget(self.videoproperties_controls_row)

    self.videoproperties_proxy_cards = QWidget()
    self.videoproperties_proxy_cards.setProperty('class', 'transparent_panel')
    cards = _FlowLayout(self.videoproperties_proxy_cards)
    self.videoproperties_proxy_resolution_card = _resolution_card(
        'videoproperties_proxy_resolution_card')
    cards.addWidget(self.videoproperties_proxy_resolution_card)
    body.addWidget(self.videoproperties_proxy_cards)

    body.addStretch()
    return tab


def load(self):
    tab_name = 'videoproperties'
    panel = left_panel.left_panel(
        parent=self, tab_name=tab_name,
        update_callback=update, translate_callback=translate,
    )
    # Tabs own the panel's whole area, flush to its edges — same structure as
    # the metadata panel.
    panel.layout().setContentsMargins(0, 0, 0, 0)

    self.left_panel_videoproperties_tabwidget = QTabWidget()
    self.left_panel_videoproperties_tabwidget.setObjectName(
        'left_panel_videoproperties_tabwidget')
    panel.layout().addWidget(self.left_panel_videoproperties_tabwidget)

    # Tab texts are set in translate(), like the metadata panel's.
    self.left_panel_videoproperties_tabwidget.insertTab(_TAB_SOURCE, _build_source_tab(self), '')
    self.left_panel_videoproperties_tabwidget.insertTab(_TAB_PROXY, _build_proxy_tab(self), '')

    self._videoproperties_encode_thread = None
    self._videoproperties_error = None   # (source, scale, message) of the last failure
    _sync_scale_buttons(self)


def _sync_scale_buttons(self):
    button = (self.videoproperties_scale_buttons.get(_selected_scale(self))
              or self.videoproperties_scale_buttons.get(50))
    if button is not None:
        button.blockSignals(True)
        button.setChecked(True)
        button.blockSignals(False)


def _scale_toggled(self, scale, checked):
    if not checked:
        return
    session.CONFIG.setdefault('proxy', {})['scale'] = int(scale)
    update(self)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def _is_active(path):
    active = (session.VIDEO or {}).get('proxy_filepath')
    return bool(active and os.path.abspath(active) == os.path.abspath(path))


def _primary_clicked(self):
    video = session.VIDEO or {}
    original = video.get('filepath')
    if not original:
        return
    if self._videoproperties_encode_thread is not None:
        _cancel_clicked(self)
        return
    scale = _selected_scale(self)
    if _is_active(proxy.proxy_path_for(original, scale)):
        _revert(self)
        return
    existing = proxy.existing_proxy(original, scale)
    if existing:
        _activate(self, existing)
        return
    _start_encode(self, original, scale)


def _start_encode(self, original, scale):
    self._videoproperties_error = None
    self.videoproperties_progress.setValue(0)
    self._videoproperties_encode_thread = proxy.encode_proxy_async(
        original, scale,
        duration=float((session.VIDEO or {}).get('duration', 0) or 0),
        on_progress=lambda f: self.videoproperties_progress.setValue(int(f * 100)),
        on_done=lambda ok, path, err: _on_done(self, original, scale, ok, path, err),
        parent=self,
    )
    update(self)


def _on_done(self, original, scale, ok, path, err):
    self._videoproperties_encode_thread = None
    if ok:
        _activate(self, path)  # auto-switch playback to the fresh proxy
        return
    if err != 'cancelled':
        self._videoproperties_error = (original, scale, err)
    update(self)


def _cancel_clicked(self):
    thread = self._videoproperties_encode_thread
    if thread is not None:
        thread.cancel()


def _delete_clicked(self):
    original = (session.VIDEO or {}).get('filepath')
    if not original or self._videoproperties_encode_thread is not None:
        return
    scale = _selected_scale(self)
    path = proxy.existing_proxy(original, scale)
    if not path:
        return
    if _is_active(path):
        _revert(self)
    self._videoproperties_error = None
    if not proxy.delete_proxy(original, scale):
        # The player may still hold the file for a moment after switching
        # back to the original (Windows refuses to delete an open file).
        def _retry():
            if not proxy.delete_proxy(original, scale):
                self._videoproperties_error = (
                    original, scale, _('videoproperties_panel.delete_failed'))
            update(self)
        QTimer.singleShot(500, _retry)
    update(self)


def _activate(self, proxy_path):
    # Proxy vs. original is a view preference, not saved project data, so we
    # deliberately do NOT mark the project unsaved here.
    pos = float(session.SUBTITLE.get('position', 0) or 0)
    session.VIDEO['proxy_filepath'] = proxy_path
    self.preview_panel_player.loadfile(proxy_path)
    self.preview_panel_player.seek(pos)
    update(self)


def _revert(self):
    pos = float(session.SUBTITLE.get('position', 0) or 0)
    session.VIDEO.pop('proxy_filepath', None)
    self.preview_panel_player.loadfile(session.VIDEO.get('filepath'))
    self.preview_panel_player.seek(pos)
    update(self)


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------
def show(self):
    update(self)


def _set_status(self, title, detail='', tooltip=''):
    self.videoproperties_status_title.setText(title)
    self.videoproperties_status_detail.setText(detail)
    self.videoproperties_status_detail.setToolTip(tooltip)
    self.videoproperties_status_detail_stack.setCurrentIndex(_STATUS_PAGE_TEXT)


def _update_source_tab(self, video, has_video):
    self.videoproperties_no_video_label.setVisible(not has_video)
    self.videoproperties_source_cards.setVisible(has_video)
    if not has_video:
        return
    width = int(video.get('width', 0) or 0)
    height = int(video.get('height', 0) or 0)
    fps = float(video.get('framerate', 0) or 0)
    self.videoproperties_resolution_card.frame.set_dimensions(width, height)
    self.videoproperties_framerate_card.value.setText(f'{fps:g}fps' if fps else '—')
    self.videoproperties_duration_card.value.setText(
        _fmt_duration(float(video.get('duration', 0) or 0)))
    self.videoproperties_codec_card.value.setText(str(video.get('codec') or '—'))


def update(self):
    video = session.VIDEO or {}
    original = video.get('filepath')
    has_video = bool(original and os.path.isfile(original))
    scale = _selected_scale(self)
    encoding = self._videoproperties_encode_thread is not None

    _update_source_tab(self, video, has_video)

    width = int(video.get('width', 0) or 0)
    height = int(video.get('height', 0) or 0)
    self.videoproperties_proxy_cards.setVisible(has_video)
    if has_video:
        self.videoproperties_proxy_resolution_card.frame.set_dimensions(
            *proxy.scaled_dimensions(width, height, scale))

    for button in self.videoproperties_scale_buttons.values():
        button.setEnabled(has_video and not encoding)

    primary = self.videoproperties_primary_button
    delete = self.videoproperties_delete_button
    enc = session.CONFIG.get('proxy', {}).get('encoder')
    primary.setToolTip(proxy.encoder_label(enc) if enc
                       else _('videoproperties_panel.encoder_auto'))

    if not has_video:
        primary.setEnabled(False)
        primary.setText(_('videoproperties_panel.create_proxy'))
        delete.setVisible(False)
        _set_status(self, _('videoproperties_panel.no_video'))
        return

    primary.setEnabled(True)
    if encoding:
        primary.setText(_('videoproperties_panel.cancel'))
        primary.setToolTip('')
        delete.setVisible(False)
        self.videoproperties_status_title.setText(_('videoproperties_panel.encoding'))
        self.videoproperties_status_detail_stack.setCurrentIndex(_STATUS_PAGE_PROGRESS)
        return

    existing = proxy.existing_proxy(original, scale)
    delete.setVisible(existing is not None)
    error = self._videoproperties_error
    if error and error[:2] != (original, scale):
        error = None

    if existing is None:
        primary.setText(_('videoproperties_panel.create_proxy'))
        if error:
            _set_status(self, _('videoproperties_panel.proxy_error'), error[2], error[2])
        else:
            _set_status(self, _('videoproperties_panel.not_generated'),
                        _('videoproperties_panel.not_generated_hint').format(
                            button=_('videoproperties_panel.create_proxy')))
        return

    try:
        size = _fmt_size(os.path.getsize(existing))
    except OSError:
        size = ''
    if _is_active(existing):
        primary.setText(_('videoproperties_panel.use_original'))
        _set_status(self, _('videoproperties_panel.in_use'), size)
    else:
        primary.setText(_('videoproperties_panel.use_proxy'))
        if error:
            _set_status(self, _('videoproperties_panel.proxy_error'), error[2], error[2])
        else:
            _set_status(self, _('videoproperties_panel.ready'), size)


def hide(self):
    pass


def translate(self):
    tabs = self.left_panel_videoproperties_tabwidget
    tabs.setTabText(_TAB_SOURCE, _('videoproperties_panel.source_title'))
    tabs.setTabText(_TAB_PROXY, _('videoproperties_panel.proxy_title'))
    self.videoproperties_no_video_label.setText(_('videoproperties_panel.no_video'))
    self.videoproperties_resolution_card.setTitle(_('videoproperties_panel.resolution'))
    self.videoproperties_framerate_card.setTitle(_('videoproperties_panel.framerate'))
    self.videoproperties_duration_card.setTitle(_('videoproperties_panel.duration'))
    self.videoproperties_codec_card.setTitle(_('videoproperties_panel.codec'))
    self.videoproperties_proxy_resolution_card.setTitle(_('videoproperties_panel.resolution'))
    self.videoproperties_proxy_help.setText(_('videoproperties_panel.proxy_help'))
    self.videoproperties_delete_button.setToolTip(_('videoproperties_panel.delete_proxy'))
    # One width for every label the button can wear, so switching state
    # never shifts the row.
    primary = self.videoproperties_primary_button
    primary.setMinimumWidth(0)
    widest = 0
    for key in ('create_proxy', 'use_proxy', 'use_original', 'cancel'):
        primary.setText(_(f'videoproperties_panel.{key}'))
        primary.ensurePolished()
        widest = max(widest, primary.sizeHint().width())
    primary.setMinimumWidth(widest)
    full = self.videoproperties_scale_buttons.get(100)
    if full is not None:
        full.setToolTip(_('videoproperties_panel.reencode_only'))
    update(self)
