"""Left-panel "Video properties" tab.

For now it exposes source video info + the **proxy** feature: create a
downscaled, video-only H.264 stand-in for large (4K/8K) files and edit against
it. Only the preview picture uses the proxy — Subtitld's audio engine, the
timeline and the waveform keep using the original, so timing stays exact.
"""

import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
    QRadioButton, QButtonGroup, QTabWidget, QScrollArea,
)
from PySide6.QtCore import Qt

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import proxy


def _fmt_duration(seconds):
    seconds = int(seconds or 0)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}'


def _selected_scale(self):
    try:
        return int(session.CONFIG.get('proxy', {}).get('scale', 50))
    except (TypeError, ValueError):
        return 50


def _build_proxy_tab(self):
    """Build the "Proxy" tab: source-video facts plus the proxy controls.

    Scrollable like the metadata panel's tabs, so a narrow panel scrolls
    instead of squashing the action row.
    """
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
    scroll.setWidget(inner)

    # --- Source info ---------------------------------------------------------
    # Kept inside the tab: the source resolution is what makes the proxy
    # target below ("→ 160 × 120") mean anything.
    self.videoproperties_info_title = QLabel()
    self.videoproperties_info_title.setProperty('class', 'widget_label')
    body.addWidget(self.videoproperties_info_title)

    self.videoproperties_resolution_label = QLabel()
    self.videoproperties_framerate_label = QLabel()
    self.videoproperties_duration_label = QLabel()
    for lbl in (self.videoproperties_resolution_label,
                self.videoproperties_framerate_label,
                self.videoproperties_duration_label):
        body.addWidget(lbl)

    # --- Proxy ---------------------------------------------------------------
    # No "Proxy" heading here any more — the tab itself carries that label.
    self.videoproperties_proxy_help = QLabel()
    self.videoproperties_proxy_help.setWordWrap(True)
    self.videoproperties_proxy_help.setProperty('class', 'description')
    body.addWidget(self.videoproperties_proxy_help)

    scale_row = QHBoxLayout()
    self.videoproperties_scale_group = QButtonGroup(self)
    self.videoproperties_scale_buttons = {}
    for sc in proxy.SCALES:
        radio = QRadioButton(f'{sc}%')
        self.videoproperties_scale_group.addButton(radio)
        radio.toggled.connect(lambda checked, s=sc: _scale_toggled(self, s, checked))
        self.videoproperties_scale_buttons[sc] = radio
        scale_row.addWidget(radio)
    scale_row.addStretch()
    body.addLayout(scale_row)

    self.videoproperties_target_label = QLabel()
    body.addWidget(self.videoproperties_target_label)

    self.videoproperties_encoder_label = QLabel()
    self.videoproperties_encoder_label.setProperty('class', 'description')
    body.addWidget(self.videoproperties_encoder_label)

    action_row = QHBoxLayout()
    self.videoproperties_progress = QProgressBar()
    self.videoproperties_progress.setVisible(False)
    self.videoproperties_progress.setProperty('class', 'secondary')
    action_row.addWidget(self.videoproperties_progress, 1)
    self.videoproperties_cancel_button = QPushButton()
    self.videoproperties_cancel_button.setProperty('class', 'secondary')
    self.videoproperties_cancel_button.setVisible(False)
    self.videoproperties_cancel_button.clicked.connect(lambda: _cancel_clicked(self))
    action_row.addWidget(self.videoproperties_cancel_button)
    self.videoproperties_primary_button = QPushButton()
    self.videoproperties_primary_button.clicked.connect(lambda: _primary_clicked(self))
    action_row.addWidget(self.videoproperties_primary_button, 0, Qt.AlignRight)
    body.addLayout(action_row)

    self.videoproperties_status_label = QLabel()
    self.videoproperties_status_label.setProperty('class', 'description')
    self.videoproperties_status_label.setWordWrap(True)
    body.addWidget(self.videoproperties_status_label)

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

    # One tab for now ("Proxy"). The tab bar exists so further video tools can
    # join it later without restructuring the panel again. Text is set in
    # translate(), matching how the metadata panel labels its tabs.
    self.left_panel_videoproperties_tabwidget.addTab(_build_proxy_tab(self), '')

    self._videoproperties_encode_thread = None
    _sync_scale_radio(self)


def _sync_scale_radio(self):
    radio = (self.videoproperties_scale_buttons.get(_selected_scale(self))
             or self.videoproperties_scale_buttons.get(50))
    if radio is not None:
        radio.blockSignals(True)
        radio.setChecked(True)
        radio.blockSignals(False)


def _scale_toggled(self, scale, checked):
    if not checked:
        return
    session.CONFIG.setdefault('proxy', {})['scale'] = int(scale)
    update(self)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def _primary_clicked(self):
    video = session.VIDEO or {}
    original = video.get('filepath')
    if not original or self._videoproperties_encode_thread is not None:
        return
    scale = _selected_scale(self)
    active = video.get('proxy_filepath')
    this_proxy = proxy.proxy_path_for(original, scale)
    if active and os.path.abspath(active) == os.path.abspath(this_proxy):
        _revert(self)
        return
    existing = proxy.existing_proxy(original, scale)
    if existing:
        _activate(self, existing)
        return
    _start_encode(self, original, scale)


def _start_encode(self, original, scale):
    self.videoproperties_progress.setValue(0)
    self.videoproperties_status_label.setText('')
    self._videoproperties_encode_thread = proxy.encode_proxy_async(
        original, scale,
        duration=float((session.VIDEO or {}).get('duration', 0) or 0),
        on_progress=lambda f: self.videoproperties_progress.setValue(int(f * 100)),
        on_done=lambda ok, path, err: _on_done(self, ok, path, err),
        parent=self,
    )
    update(self)


def _on_done(self, ok, path, err):
    self._videoproperties_encode_thread = None
    if ok:
        _activate(self, path)  # auto-switch playback to the fresh proxy
    else:
        update(self)
        if err != 'cancelled':
            self.videoproperties_status_label.setText(
                f"{_('videoproperties_panel.proxy_error')} {err}")


def _cancel_clicked(self):
    thread = self._videoproperties_encode_thread
    if thread is not None:
        thread.cancel()


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


def update(self):
    video = session.VIDEO or {}
    original = video.get('filepath')
    has_video = bool(original and os.path.isfile(original))
    width = int(video.get('width', 0) or 0)
    height = int(video.get('height', 0) or 0)
    fps = float(video.get('framerate', 0) or 0)
    duration = float(video.get('duration', 0) or 0)
    scale = _selected_scale(self)
    encoding = self._videoproperties_encode_thread is not None

    if has_video:
        self.videoproperties_resolution_label.setText(
            f"{_('videoproperties_panel.resolution')}  {width} × {height}")
        self.videoproperties_framerate_label.setText(
            f"{_('videoproperties_panel.framerate')}  {fps:g} fps")
        self.videoproperties_duration_label.setText(
            f"{_('videoproperties_panel.duration')}  {_fmt_duration(duration)}")
    else:
        self.videoproperties_resolution_label.setText(_('videoproperties_panel.no_video'))
        self.videoproperties_framerate_label.setText('')
        self.videoproperties_duration_label.setText('')

    target_w, target_h = (proxy.scaled_dimensions(width, height, scale)
                          if has_video else (0, 0))
    suffix = (f"  ({_('videoproperties_panel.reencode_only')})" if scale == 100 else '')
    self.videoproperties_target_label.setText(f"→ {target_w} × {target_h}{suffix}")

    enc = session.CONFIG.get('proxy', {}).get('encoder')
    self.videoproperties_encoder_label.setText(
        proxy.encoder_label(enc) if enc else _('videoproperties_panel.encoder_auto'))

    self.videoproperties_progress.setVisible(encoding)
    self.videoproperties_cancel_button.setVisible(encoding)
    self.videoproperties_primary_button.setVisible(not encoding)
    for radio in self.videoproperties_scale_buttons.values():
        radio.setEnabled(has_video and not encoding)

    if not has_video:
        self.videoproperties_primary_button.setEnabled(False)
        self.videoproperties_primary_button.setText(_('videoproperties_panel.create_proxy'))
        self.videoproperties_status_label.setText('')
        return

    self.videoproperties_primary_button.setEnabled(True)
    active = video.get('proxy_filepath')
    this_proxy = proxy.proxy_path_for(original, scale)
    is_active_this = bool(active and os.path.abspath(active) == os.path.abspath(this_proxy))

    if is_active_this:
        self.videoproperties_primary_button.setText(_('videoproperties_panel.use_original'))
    elif proxy.existing_proxy(original, scale) is not None:
        self.videoproperties_primary_button.setText(_('videoproperties_panel.use_proxy'))
    else:
        self.videoproperties_primary_button.setText(_('videoproperties_panel.create_proxy'))

    if encoding:
        self.videoproperties_status_label.setText(_('videoproperties_panel.encoding'))
    elif active:
        self.videoproperties_status_label.setText(_('videoproperties_panel.using_proxy'))
    else:
        self.videoproperties_status_label.setText(_('videoproperties_panel.using_original'))


def hide(self):
    pass


def translate(self):
    self.left_panel_videoproperties_tabwidget.setTabText(
        0, _('videoproperties_panel.proxy_title'))
    self.videoproperties_info_title.setText(_('videoproperties_panel.info_title'))
    self.videoproperties_proxy_help.setText(_('videoproperties_panel.proxy_help'))
    self.videoproperties_cancel_button.setText(_('videoproperties_panel.cancel'))
    update(self)
