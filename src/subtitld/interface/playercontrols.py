import os
import time
from bisect import bisect
import subprocess

from PySide6.QtWidgets import QPushButton, QLabel, QDoubleSpinBox, QSlider, QSpinBox, QComboBox, QWidget, QStylePainter, QStyleOptionTab, QStyle, QTabBar, QColorDialog, QHBoxLayout, QSizePolicy, QVBoxLayout, QLayout, QDial, QGraphicsOpacityEffect
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, Qt, QRect, QPoint, QThread, QSize, Signal, QEvent, QTimer, QObject
from PySide6.QtMultimedia import QMediaPlayer  # for the playback-state guard on the throttled timeline timer


class EnterAbsorbingDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox that fully consumes Return/Enter so neither global
    shortcuts nor the parent widget (e.g. a QPushButton container) reacts."""
    def event(self, ev):
        if ev.type() == QEvent.ShortcutOverride and ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            ev.accept()
            return True
        return super().event(ev)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.interpretText()
            self.editingFinished.emit()
            ev.accept()
            return
        super().keyPressEvent(ev)

import subtitld.modules.timecode as timecode
from subtitld.interface import timeline, left_panel
from subtitld.interface.translation import _

from subtitld.modules import subtitles
from subtitld.modules import session
from subtitld.modules import utils
from subtitld.modules import history
from subtitld.modules.shortcuts import shortcut

STEPS_LIST = ['Frames', 'Seconds']


def _attach_collapsible(button, *children):
    """Bind `children` to `button` so they can be hidden together when the
    button is unchecked. The button also gets an `expanded` Qt property that
    QSS reads to swap the wide-form padding for icon-only padding, so the
    button visually shrinks when its controls hide."""
    button._collapsible_children = list(children)


def _set_collapsed(button, collapsed, animate=True):
    children = getattr(button, '_collapsible_children', None)
    if children is None:
        return
    button.setProperty('expanded', not collapsed)
    button.style().unpolish(button)
    button.style().polish(button)
    for child in children:
        child.setVisible(not collapsed)
    button.adjustSize()


class _SplitterHandleButton(QPushButton):
    """Acts as the visible drag handle for the main vertical splitter. The
    splitter's own handle is hidden (handleWidth=0); this button replaces it
    and lives at the top-right of the playercontrols panel, slightly
    overlapping the panel's top edge so it visually sits *on* the seam.
    Parented to the top-level window (not the panel) so it isn't clipped by
    the panel's boundary."""

    def __init__(self, splitter, on_drag=None, parent=None):
        super().__init__(parent)
        self._splitter = splitter
        self._on_drag = on_drag
        self._drag_origin_y = None
        self._origin_sizes = None
        self.setObjectName('playercontrols_top_handle')
        self.setCursor(Qt.SizeVerCursor)
        self.setFixedSize(16, 7)
        self.setFocusPolicy(Qt.NoFocus)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_origin_y = event.globalPosition().y()
            self._origin_sizes = list(self._splitter.sizes())
            # QAbstractButton tracks the pressed state via isDown(); set it
            # explicitly so QSS :pressed selectors apply during the drag.
            self.setDown(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_origin_y is not None and self._origin_sizes and len(self._origin_sizes) >= 2:
            delta = event.globalPosition().y() - self._drag_origin_y
            sizes = list(self._origin_sizes)
            sizes[0] = max(0, int(sizes[0] + delta))
            sizes[-1] = max(0, int(sizes[-1] - delta))
            self._splitter.setSizes(sizes)
            if self._on_drag is not None:
                self._on_drag()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_origin_y = None
        self._origin_sizes = None
        self.setDown(False)
        super().mouseReleaseEvent(event)


class _ChildResizeWatcher(QObject):
    """Calls a callback whenever the watched widget resizes. Used to keep the
    floating splitter-handle button glued to the right edge of the panel."""

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self._cb = callback

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Show, QEvent.Move):
            self._cb()
        return False



class _OriginalDecodeThread(QThread):
    """Decodes the source media to a 48 kHz mono FLAC sidecar at
    `<cache>/audioseparation/<hash>_original.flac`. Used as the playback
    track until vocals/background separation finishes — and as the
    fallback audio source for ASR/clone-ref when separation hasn't been
    requested yet. Cheap (a straight ffmpeg copy/decode), so we always
    do this regardless of which separator provider is active."""

    decoded = Signal(str)
    error = Signal(str)

    def __init__(self, filename: str, output_path: str, parent=None):
        super().__init__(parent)
        self.filename = filename
        self.output_path = output_path

    def run(self):
        if not os.path.exists(self.output_path):
            cmd = [
                session.FFMPEG_EXECUTABLE,
                "-hide_banner", "-loglevel", "error",
                "-i", self.filename,
                "-vn",
                "-ar", '48000', '-y',
                self.output_path,
            ]
            try:
                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    startupinfo=session.STARTUPINFO,
                )
            except Exception as exc:  # noqa: BLE001
                self.error.emit(str(exc))
                return
        if os.path.exists(self.output_path):
            self.decoded.emit(self.output_path)
        else:
            self.error.emit('original decode produced no output')


class MusicAudioExtractorThread(QObject):
    """Player-controls helper that emits `original` (the 48 kHz mono
    decode of the source, used for playback) and `response` (vocals +
    background paths for the music/voice slider) signals.

    Historically this was a `QThread` doing the ffmpeg work inline; now
    it's a controller that:

      1. Spawns a small worker thread for the cheap original-decode step
         (always ffmpeg — independent of which separator is active).
      2. Looks up the active `AudioSeparatorProvider` via the addon
         manager and dispatches the vocals/background split to it.

    The user picks the separator provider via the global-options panel
    (`session.CONFIG['audio_separation']['engine']`); when none is
    configured, falls back to the first available provider, which is
    always the built-in `ffmpeg-separator`.
    """

    response = Signal(dict)
    original = Signal(str)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.filename = None
        self._decode_thread: _OriginalDecodeThread | None = None
        self._active_provider = None
        self._connected_provider = None

    def start(self):
        if not self.filename:
            return

        filename_hash = utils.get_cache_key(self.filename)
        original_filepath = os.path.join(
            session.PATH_SUBTITLD_DATA_AUDIOSEPARATION,
            f'{filename_hash}_original.flac',
        )
        vocals_filepath = os.path.join(
            session.PATH_SUBTITLD_DATA_AUDIOSEPARATION,
            f'{filename_hash}_vocals.flac',
        )
        background_filepath = os.path.join(
            session.PATH_SUBTITLD_DATA_AUDIOSEPARATION,
            f'{filename_hash}_background.flac',
        )

        # Fast path: all three cache files already exist on disk. This
        # is the normal case when reopening a USFX that bundles its
        # `assets/audio/{original,vocals,background}.flac` (the USFX
        # background extractor copies them to the cache during load) —
        # or when reopening any project whose separation has run before.
        # Without this short-circuit, every project open would re-run
        # ffmpeg decode + provider separation even though the answer is
        # already cached, wasting seconds-to-minutes of CPU.
        if (os.path.exists(original_filepath)
                and os.path.exists(vocals_filepath)
                and os.path.exists(background_filepath)):
            # Match the signal order of the slow path: `original` first
            # (sets up the original playback track), then `response`
            # (sets up the background/vocals tracks).
            self.original.emit(original_filepath)
            self.response.emit({
                'original': original_filepath,
                'vocals': vocals_filepath,
                'background': background_filepath,
            })
            return

        # Step 1: original decode. Always ffmpeg, on a worker thread so
        # the UI stays responsive while we wait. When done, emit
        # `original` and kick off step 2.
        thread = _OriginalDecodeThread(self.filename, original_filepath, parent=self)
        thread.decoded.connect(self._on_original_decoded)
        thread.error.connect(self.error)
        self._decode_thread = thread
        thread.start()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _on_original_decoded(self, original_path: str) -> None:
        self.original.emit(original_path)

        # Step 2: dispatch separation to the active provider.
        # Imported lazily so the module-level import graph stays tight
        # (this file already imports a lot of Qt/UI machinery).
        from subtitld.modules import addons
        from subtitld.modules.addons import registry
        from subtitld.modules.addons.provider import TASK_AUDIO_SEPARATE

        manager = addons.get_manager()
        configured_id = registry.default_for_task(TASK_AUDIO_SEPARATE)
        provider = None
        if configured_id:
            candidate = manager.get(configured_id)
            if candidate is not None and TASK_AUDIO_SEPARATE in candidate.tasks:
                provider = candidate
        if provider is None:
            provider = manager.default_for_task(TASK_AUDIO_SEPARATE)
        if provider is None:
            self.error.emit('no audio separator provider available')
            return

        # Disconnect any previous wiring so we don't double-fire when
        # the user runs separation a second time on a new file.
        self._disconnect_provider()
        provider.separation_ready.connect(self._on_separation_ready)
        provider.separation_error.connect(self._on_separation_error)
        self._connected_provider = provider

        provider.separate(
            self.filename,
            str(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION),
            options=registry.options_for(provider.id) or None,
        )

    def _on_separation_ready(self, input_path: str, vocals_path: str, background_path: str) -> None:
        # Filter — providers are app-singletons, this slot fires for
        # every separation finish. Drop anything that isn't ours.
        if input_path and self.filename and os.path.abspath(input_path) != os.path.abspath(self.filename):
            return
        default_volume = 0.5
        try:
            default_volume = float(session.CONFIG.get('videoplayer', {}).get('music_voice_separation_volume', 0.5))
        except (TypeError, ValueError):
            default_volume = 0.5
        self.response.emit({
            'vocals': vocals_path,
            'background': background_path,
            'volume': default_volume,
        })
        self._disconnect_provider()

    def _on_separation_error(self, input_path: str, message: str) -> None:
        if input_path and self.filename and os.path.abspath(input_path) != os.path.abspath(self.filename):
            return
        self.error.emit(message)
        self._disconnect_provider()

    def _disconnect_provider(self) -> None:
        provider = self._connected_provider
        if provider is None:
            return
        try:
            provider.separation_ready.disconnect(self._on_separation_ready)
        except (TypeError, RuntimeError):
            pass
        try:
            provider.separation_error.disconnect(self._on_separation_error)
        except (TypeError, RuntimeError):
            pass
        self._connected_provider = None


class QLeftTabBar(QTabBar):
    def tabSizeHint(self, index):
        s = QTabBar.tabSizeHint(self, index)
        s.transpose()
        return s

    def paintEvent(self, event):
        painter = QStylePainter(self)
        opt = QStyleOptionTab()

        for i in range(self.count()):
            self.initStyleOption(opt, i)
            painter.drawControl(QStyle.CE_TabBarTabShape, opt)
            painter.save()

            s = opt.rect.size()
            s.transpose()
            r = QRect(QPoint(), s)
            r.moveCenter(opt.rect.center())
            opt.rect = r

            c = self.tabRect(i).center()
            painter.translate(c)
            painter.rotate(90)
            painter.translate(-c)
            painter.drawControl(QStyle.CE_TabBarTabLabel, opt)
            painter.restore()
        event.accept()

def load(self):
    """Function to load player control widgets"""
    self.playercontrols_widget = QWidget()
    self.playercontrols_widget.setObjectName('playercontrols_widget')
    self.playercontrols_widget.setLayout(QVBoxLayout())
    self.playercontrols_widget.layout().setContentsMargins(0, 0, 0, 0)
    self.playercontrols_widget.layout().setSpacing(0)
    self.playercontrols_widget.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
    self.playercontrols_widget_animation = QPropertyAnimation(self.playercontrols_widget, b'minimumHeight')
    self.playercontrols_widget_animation.setEasingCurve(QEasingCurve.OutQuint)

    # Custom splitter handle: the main_vertical_splitter's own handle is
    # hidden (handleWidth=0 set in productionscreen.py). This button sits at
    # the top-right of the playercontrols panel, straddling the seam between
    # the two panes. Parented to the top-level window so the part above the
    # panel isn't clipped by the panel's boundary.
    def _handle_target_pos():
        panel = self.playercontrols_widget
        btn = self.playercontrols_handle_button
        anchor = panel.mapTo(self, QPoint(panel.width() - btn.width(), 0))
        return QPoint(anchor.x(), anchor.y() - 3)

    def _reposition_handle():
        btn = self.playercontrols_handle_button
        panel = self.playercontrols_widget
        if not panel.isVisible() or panel.width() == 0:
            btn.hide()
            return
        if self._handle_pending_show or self._handle_slidein_anim.state() == QPropertyAnimation.Running:
            # Don't override the slide-in trajectory mid-flight, and don't
            # show prematurely — the bottom_panel's slide-in is still going
            # or about to start.
            return
        if not btn.isVisible():
            return
        target = _handle_target_pos()
        btn.move(target)
        btn.raise_()

    def _slide_in_handle():
        btn = self.playercontrols_handle_button
        panel = self.playercontrols_widget
        if not panel.isVisible() or panel.width() == 0:
            return
        target = _handle_target_pos()
        start = QPoint(target.x() + 32, target.y())
        btn.move(start)
        btn.show()
        btn.raise_()
        anim = self._handle_slidein_anim
        anim.stop()
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.start()

    self.playercontrols_handle_button = _SplitterHandleButton(
        self.main_vertical_splitter, on_drag=_reposition_handle, parent=self
    )
    self.playercontrols_handle_button.hide()

    self._handle_slidein_anim = QPropertyAnimation(self.playercontrols_handle_button, b'pos', self)
    self._handle_slidein_anim.setDuration(450)
    self._handle_slidein_anim.setEasingCurve(QEasingCurve.OutQuint)

    self._handle_pending_show = False

    def _on_bottom_panel_anim_finished():
        if self._handle_pending_show:
            self._handle_pending_show = False
            _slide_in_handle()

    self.bottom_panel.animation.finished.connect(_on_bottom_panel_anim_finished)
    self._slide_in_handle = _slide_in_handle

    self._playercontrols_handle_watcher = _ChildResizeWatcher(_reposition_handle, self.playercontrols_widget)
    self.playercontrols_widget.installEventFilter(self._playercontrols_handle_watcher)
    self._main_window_handle_watcher = _ChildResizeWatcher(_reposition_handle, self)
    self.installEventFilter(self._main_window_handle_watcher)
    # self.playercontrols_widget_animation.finished.connect(lambda: self.playercontrols_widget.setMinimumHeight(0))

    self.playercontrols_widget_top_line = QWidget()
    self.playercontrols_widget_top_line.setObjectName('playercontrols_widget_top_line')
    self.playercontrols_widget_top_line.setLayout(QHBoxLayout())
    self.playercontrols_widget_top_line.layout().setContentsMargins(0, 0, 0, 0)
    self.playercontrols_widget_top_line.layout().setSpacing(0)
    # self.playercontrols_widget_top_line.setAttribute(Qt.WA_LayoutOnEntireRect)
    self.playercontrols_widget_top_line.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum))

    self.playercontrols_widget_left_top_line = QWidget()
    self.playercontrols_widget_left_top_line.setObjectName('playercontrols_widget_left_top_line')
    # self.playercontrols_widget_left_top_line.setAttribute(Qt.WA_TranslucentBackground)
    self.playercontrols_widget_left_top_line.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
    self.playercontrols_widget_left_top_line.setLayout(QHBoxLayout())
    self.playercontrols_widget_left_top_line.layout().setContentsMargins(0, 2, 0, 0)
    self.playercontrols_widget_left_top_line.layout().setSpacing(8)
    self.playercontrols_widget_left_top_line.setAttribute(Qt.WA_LayoutOnEntireRect)

    self.playercontrols_widget_left_top_line.layout().addStretch()

    self.send_text_buttons_container = QWidget()
    self.send_text_buttons_container.setLayout(QHBoxLayout())
    self.send_text_buttons_container.setObjectName('send_text_buttons_container')
    self.send_text_buttons_container.setFixedHeight(42)
    self.send_text_buttons_container.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.send_text_buttons_container.layout().setContentsMargins(0, 0, 0, 0)
    self.send_text_buttons_container.layout().setSpacing(0)

    self.send_text_to_last_subtitle_button = QPushButton()
    self.send_text_to_last_subtitle_button.setObjectName('send_text_to_last_subtitle_button')
    self.send_text_to_last_subtitle_button.setLayout(QHBoxLayout())
    self.send_text_to_last_subtitle_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.send_text_to_last_subtitle_button.layout().setContentsMargins(8, 0, 24, 3)
    self.send_text_to_last_subtitle_button.setIconSize(QSize(24, 20))
    self.send_text_to_last_subtitle_button.setFixedWidth(56)
    self.send_text_to_last_subtitle_button.clicked.connect(lambda: send_text_to_last_subtitle_button_clicked(self))
    self.send_text_buttons_container.layout().addWidget(self.send_text_to_last_subtitle_button)

    self.send_text_to_last_subtitle_and_slice_button = QPushButton()
    self.send_text_to_last_subtitle_and_slice_button.setObjectName('send_text_to_last_subtitle_and_slice_button')
    self.send_text_to_last_subtitle_and_slice_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.send_text_to_last_subtitle_and_slice_button.setIconSize(QSize(24, 20))
    self.send_text_to_last_subtitle_and_slice_button.setFixedWidth(24)
    self.send_text_to_last_subtitle_and_slice_button.clicked.connect(lambda: send_text_to_last_subtitle_and_slice_button_clicked(self))
    self.send_text_to_last_subtitle_button.layout().addWidget(self.send_text_to_last_subtitle_and_slice_button, 0, Qt.AlignLeft)

    self.send_text_to_next_subtitle_button = QPushButton()
    self.send_text_to_next_subtitle_button.setObjectName('send_text_to_next_subtitle_button')
    self.send_text_to_next_subtitle_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.send_text_to_next_subtitle_button.setLayout(QHBoxLayout())
    self.send_text_to_next_subtitle_button.setIconSize(QSize(24, 20))
    self.send_text_to_next_subtitle_button.setFixedWidth(56)
    self.send_text_to_next_subtitle_button.layout().setContentsMargins(24, 0, 8, 3)
    self.send_text_to_next_subtitle_button.clicked.connect(lambda: send_text_to_next_subtitle_button_clicked(self))
    self.send_text_buttons_container.layout().addWidget(self.send_text_to_next_subtitle_button)

    self.send_text_to_next_subtitle_and_slice_button = QPushButton()
    self.send_text_to_next_subtitle_and_slice_button.setObjectName('send_text_to_next_subtitle_and_slice_button')
    self.send_text_to_next_subtitle_and_slice_button.setIconSize(QSize(24, 20))
    self.send_text_to_next_subtitle_and_slice_button.setFixedWidth(24)
    self.send_text_to_next_subtitle_and_slice_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.send_text_to_next_subtitle_and_slice_button.clicked.connect(lambda: send_text_to_next_subtitle_and_slice_button_clicked(self))
    self.send_text_to_next_subtitle_button.layout().addWidget(self.send_text_to_next_subtitle_and_slice_button, 0, Qt.AlignRight)

    self.playercontrols_widget_left_top_line.layout().addWidget(self.send_text_buttons_container, 1, Qt.AlignTop)

    self.merge_slice_frame = QWidget()
    self.merge_slice_frame.setObjectName('merge_slice_frame')
    self.merge_slice_frame.setLayout(QHBoxLayout())
    self.merge_slice_frame.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.merge_slice_frame.setFixedHeight(42)
    # self.merge_slice_frame.layout().setSizeConstraint(QLayout.SetMinimumSize)
    self.merge_slice_frame.layout().setContentsMargins(0, 0, 0, 0)
    self.merge_slice_frame.layout().setSpacing(0)

    self.merge_back_selected_subtitle_button = QPushButton()
    self.merge_back_selected_subtitle_button.setObjectName('merge_back_selected_subtitle_button')
    self.merge_back_selected_subtitle_button.setIconSize(QSize(20, 20))
    self.merge_back_selected_subtitle_button.setFixedWidth(28)
    self.merge_back_selected_subtitle_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.merge_back_selected_subtitle_button.clicked.connect(lambda: merge_back_selected_subtitle_button_clicked(self))
    self.merge_slice_frame.layout().addWidget(self.merge_back_selected_subtitle_button)

    self.merge_slice_frame.layout().addSpacing(-1)

    self.slice_selected_subtitle_button = QPushButton()
    self.slice_selected_subtitle_button.setObjectName('slice_selected_subtitle_button')
    self.slice_selected_subtitle_button.setIconSize(QSize(32, 20))
    self.slice_selected_subtitle_button.setFixedWidth(30)
    self.slice_selected_subtitle_button.setCheckable(True)
    self.slice_selected_subtitle_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.slice_selected_subtitle_button.clicked.connect(lambda: slice_selected_subtitle_button_clicked(self))
    self.merge_slice_frame.layout().addWidget(self.slice_selected_subtitle_button)

    self.merge_slice_frame.layout().addSpacing(-1)

    self.merge_next_selected_subtitle_button = QPushButton()
    self.merge_next_selected_subtitle_button.setObjectName('merge_next_selected_subtitle_button')
    self.merge_next_selected_subtitle_button.setIconSize(QSize(20, 20))
    self.merge_next_selected_subtitle_button.setFixedWidth(28)
    self.merge_next_selected_subtitle_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.merge_next_selected_subtitle_button.clicked.connect(lambda: merge_next_selected_subtitle_button_clicked(self))
    self.merge_slice_frame.layout().addWidget(self.merge_next_selected_subtitle_button)

    self.playercontrols_widget_left_top_line.layout().addWidget(self.merge_slice_frame, 1, Qt.AlignTop)
    
    self.start_end_moving_frame = QWidget()
    self.start_end_moving_frame.setObjectName('start_end_moving_frame')
    self.start_end_moving_frame.setLayout(QHBoxLayout())
    self.start_end_moving_frame.setFixedHeight(42)
    self.start_end_moving_frame.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.start_end_moving_frame.layout().setContentsMargins(0, 0, 0, 0)
    self.start_end_moving_frame.layout().setSpacing(0)

    self.last_end_to_current_position_button = QPushButton()
    self.last_end_to_current_position_button.setObjectName('last_end_to_current_position_button')
    self.last_end_to_current_position_button.setIconSize(QSize(20, 20))
    self.last_end_to_current_position_button.setFixedWidth(32)
    self.last_end_to_current_position_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.last_end_to_current_position_button.clicked.connect(lambda: last_end_to_current_position_button_clicked(self))
    self.start_end_moving_frame.layout().addWidget(self.last_end_to_current_position_button, 1)

    self.last_start_to_current_position_button = QPushButton()
    self.last_start_to_current_position_button.setObjectName('last_start_to_current_position_button')
    self.last_start_to_current_position_button.setIconSize(QSize(28, 20))
    self.last_start_to_current_position_button.setFixedWidth(28)
    self.last_start_to_current_position_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.last_start_to_current_position_button.clicked.connect(lambda: last_start_to_current_position_button_clicked(self))
    self.start_end_moving_frame.layout().addWidget(self.last_start_to_current_position_button, 1)

    self.last_start_last_end_to_current_position_button = QPushButton()
    self.last_start_last_end_to_current_position_button.setObjectName('last_start_last_end_to_current_position_button')
    self.last_start_last_end_to_current_position_button.setIconSize(QSize(28, 20))
    self.last_start_last_end_to_current_position_button.setFixedWidth(28)
    self.last_start_last_end_to_current_position_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.last_start_last_end_to_current_position_button.clicked.connect(lambda: last_start_last_end_to_current_position_button_clicked(self))
    self.start_end_moving_frame.layout().addWidget(self.last_start_last_end_to_current_position_button, 1)

    self.next_start_next_end_to_current_position_button = QPushButton()
    self.next_start_next_end_to_current_position_button.setObjectName('next_start_next_end_to_current_position_button')
    self.next_start_next_end_to_current_position_button.setIconSize(QSize(28, 20))
    self.next_start_next_end_to_current_position_button.setFixedWidth(28)
    self.next_start_next_end_to_current_position_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.next_start_next_end_to_current_position_button.clicked.connect(lambda: next_start_next_end_to_current_position_button_clicked(self))
    self.start_end_moving_frame.layout().addWidget(self.next_start_next_end_to_current_position_button, 1)
    
    self.next_end_to_current_position_button = QPushButton()
    self.next_end_to_current_position_button.setObjectName('next_end_to_current_position_button')
    self.next_end_to_current_position_button.setIconSize(QSize(28, 20))
    self.next_end_to_current_position_button.setFixedWidth(28)
    self.next_end_to_current_position_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.next_end_to_current_position_button.clicked.connect(lambda: next_end_to_current_position_button_clicked(self))
    self.start_end_moving_frame.layout().addWidget(self.next_end_to_current_position_button, 1)

    self.next_start_to_current_position_button = QPushButton()
    self.next_start_to_current_position_button.setObjectName('next_start_to_current_position_button')
    self.next_start_to_current_position_button.setIconSize(QSize(20, 20))
    self.next_start_to_current_position_button.setFixedWidth(32)
    self.next_start_to_current_position_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.next_start_to_current_position_button.clicked.connect(lambda: next_start_to_current_position_button_clicked(self))
    self.start_end_moving_frame.layout().addWidget(self.next_start_to_current_position_button, 1)

    self.playercontrols_widget_left_top_line.layout().addWidget(self.start_end_moving_frame, 1, Qt.AlignTop)

    self.change_playback_speed = QPushButton()
    self.change_playback_speed.setObjectName('change_playback_speed')
    self.change_playback_speed.setCheckable(True)
    self.change_playback_speed.setLayout(QHBoxLayout())
    self.change_playback_speed.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed))
    self.change_playback_speed.setFixedHeight(48) 
    self.change_playback_speed.setIconSize(QSize(22, 22))
    self.change_playback_speed.layout().setContentsMargins(16, 15, 30, 16)
    self.change_playback_speed.layout().setSpacing(0)
    self.change_playback_speed.clicked.connect(lambda: change_playback_speed_clicked(self))

    self.change_playback_speed_decrease = QPushButton('-')
    self.change_playback_speed_decrease.setObjectName('change_playback_speed_decrease')
    self.change_playback_speed_decrease.setFixedWidth(20)
    self.change_playback_speed_decrease.setStyleSheet('QPushButton {padding:0;}')
    self.change_playback_speed_decrease.clicked.connect(lambda: change_playback_speed_decrease_clicked(self))
    self.change_playback_speed_decrease.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.change_playback_speed.layout().addWidget(self.change_playback_speed_decrease)

    self.change_playback_speed_slider = QSlider(Qt.Horizontal)
    self.change_playback_speed_slider.setMinimum(5)
    self.change_playback_speed_slider.setMaximum(300)
    self.change_playback_speed_slider.setPageStep(10)
    # self.change_playback_speed_slider.setFixedWidth(60)
    self.change_playback_speed_slider.setValue(int(session.CONFIG.get('playback_speed', 1) * 100))
    self.change_playback_speed_slider.sliderReleased.connect(lambda: change_playback_speed_slider(self))
    self.change_playback_speed_slider.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.change_playback_speed.layout().addWidget(self.change_playback_speed_slider)

    self.change_playback_speed_increase = QPushButton('+')
    self.change_playback_speed_increase.setObjectName('change_playback_speed_increase')
    self.change_playback_speed_increase.setFixedWidth(20)
    self.change_playback_speed_increase.setStyleSheet('QPushButton {padding:0;}')
    self.change_playback_speed_increase.clicked.connect(lambda: change_playback_speed_increase_clicked(self))
    self.change_playback_speed_increase.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.change_playback_speed.layout().addWidget(self.change_playback_speed_increase)

    self.change_playback_speed_label = QLabel()
    self.change_playback_speed_label.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.change_playback_speed_label.setObjectName('change_playback_speed_label')
    self.change_playback_speed_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    self.change_playback_speed_label.setFixedWidth(50)
    self.change_playback_speed.layout().addWidget(self.change_playback_speed_label)

    _attach_collapsible(self.change_playback_speed,
                        self.change_playback_speed_decrease,
                        self.change_playback_speed_slider,
                        self.change_playback_speed_increase,
                        self.change_playback_speed_label)

    self.playercontrols_widget_left_top_line.layout().addWidget(self.change_playback_speed, 1, Qt.AlignTop)

    self.playercontrols_widget_top_line.layout().addWidget(self.playercontrols_widget_left_top_line, 1)

    self.playercontrols_widget_center_top_line = QWidget()
    self.playercontrols_widget_center_top_line.setObjectName('playercontrols_widget_center_top_line')
    self.playercontrols_widget_center_top_line.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed))
    self.playercontrols_widget_center_top_line.setFixedHeight(51)
    self.playercontrols_widget_center_top_line.setLayout(QHBoxLayout())
    self.playercontrols_widget_center_top_line.layout().setContentsMargins(16, 6, 16, 1)
    self.playercontrols_widget_center_top_line.layout().setSpacing(0)
    self.playercontrols_widget_center_top_line.setAttribute(Qt.WA_LayoutOnEntireRect)

    self.playercontrols_stop_button = QPushButton()
    self.playercontrols_stop_button.setObjectName('playercontrols_stop_button')
    self.playercontrols_stop_button.setProperty('position', 'first')
    self.playercontrols_stop_button.setIconSize(QSize(20, 20))
    self.playercontrols_stop_button.clicked.connect(lambda: playercontrols_stop_button_clicked(self))
    self.playercontrols_stop_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.playercontrols_widget_center_top_line.layout().addWidget(self.playercontrols_stop_button)

    self.playercontrols_playpause_button = QPushButton()
    self.playercontrols_playpause_button.setObjectName('playercontrols_playpause_button')
    self.playercontrols_playpause_button.setCheckable(True)
    self.playercontrols_playpause_button.setIconSize(QSize(22, 24))
    self.playercontrols_playpause_button.setLayout(QHBoxLayout())
    self.playercontrols_playpause_button.layout().setContentsMargins(40, 0, 0, 5)
    self.playercontrols_playpause_button.layout().setSpacing(0)
    self.playercontrols_playpause_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.playercontrols_playpause_button.clicked.connect(lambda: playercontrols_playpause_button_clicked(self))

    self.playercontrols_play_from_last_start_button = QPushButton()
    self.playercontrols_play_from_last_start_button.setIconSize(QSize(24, 24))
    self.playercontrols_play_from_last_start_button.setObjectName('playercontrols_play_from_last_start_button')
    self.playercontrols_play_from_last_start_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.playercontrols_play_from_last_start_button.setFixedWidth(36)
    self.playercontrols_play_from_last_start_button.clicked.connect(lambda: playercontrols_play_from_last_start_button_clicked(self))
    self.playercontrols_playpause_button.layout().addWidget(self.playercontrols_play_from_last_start_button, 0, Qt.AlignRight)

    self.playercontrols_playpause_button.layout().addSpacing(-1)

    self.playercontrols_play_from_next_start_button = QPushButton()
    self.playercontrols_play_from_next_start_button.setIconSize(QSize(24, 24))
    self.playercontrols_play_from_next_start_button.setObjectName('playercontrols_play_from_next_start_button')
    self.playercontrols_play_from_next_start_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.playercontrols_play_from_next_start_button.setFixedWidth(36)
    self.playercontrols_play_from_next_start_button.clicked.connect(lambda: playercontrols_play_from_next_start_button_clicked(self))
    self.playercontrols_playpause_button.layout().addWidget(self.playercontrols_play_from_next_start_button, 0, Qt.AlignLeft)

    self.playercontrols_widget_center_top_line.layout().addWidget(self.playercontrols_playpause_button)

    self.playercontrols_record_button = QPushButton()
    self.playercontrols_record_button.setObjectName('playercontrols_record_button')
    self.playercontrols_record_button.setIconSize(QSize(24, 24))
    self.playercontrols_record_button.setCheckable(True)
    self.playercontrols_record_button.setProperty('position', 'last')
    self.playercontrols_record_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.playercontrols_record_button.setToolTip(_('record_button.tooltip'))
    self.playercontrols_record_button.clicked.connect(lambda: playercontrols_record_button_clicked(self))
    self.playercontrols_record_button.setLayout(QHBoxLayout())
    self.playercontrols_record_button.layout().setContentsMargins(40, 0, 0, 5)
    self.playercontrols_record_button.layout().setSpacing(0)

    self.playercontrols_record_transcript_button = QPushButton()
    self.playercontrols_record_transcript_button.setIconSize(QSize(24, 24))
    self.playercontrols_record_transcript_button.setObjectName('playercontrols_record_transcript_button')
    self.playercontrols_record_transcript_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.playercontrols_record_transcript_button.setFixedWidth(36)
    self.playercontrols_record_transcript_button.setCheckable(True)
    self.playercontrols_record_transcript_button.setToolTip(_('record_button.transcript_tooltip'))
    self.playercontrols_record_transcript_button.clicked.connect(lambda: playercontrols_record_transcript_button_clicked(self))
    self.playercontrols_record_button.layout().addWidget(self.playercontrols_record_transcript_button, 0, Qt.AlignRight)

    self.playercontrols_record_button.layout().addSpacing(-1)

    self.playercontrols_record_audio_button = QPushButton()
    self.playercontrols_record_audio_button.setIconSize(QSize(24, 24))
    self.playercontrols_record_audio_button.setObjectName('playercontrols_record_audio_button')
    self.playercontrols_record_audio_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.playercontrols_record_audio_button.setFixedWidth(36)
    self.playercontrols_record_audio_button.setCheckable(True)
    self.playercontrols_record_audio_button.setToolTip(_('record_button.wave_tooltip'))
    self.playercontrols_record_audio_button.clicked.connect(lambda: playercontrols_record_audio_button_clicked(self))
    self.playercontrols_record_button.layout().addWidget(self.playercontrols_record_audio_button, 0, Qt.AlignLeft)

    self.playercontrols_widget_center_top_line.layout().addWidget(self.playercontrols_record_button)

    # Record-mode controller (recording is tied to playback; see
    # record_controls.RecordController). Reflect persisted arm/mode state.
    from subtitld.interface.record_controls import RecordController
    self.record_controller = RecordController(self)
    self.playercontrols_record_button.setChecked(self.record_controller.armed)

    # Smooth opacity pulse while the record button is armed ("hot"), so it's
    # clearly live. Driven by _update_record_pulse from _update_record_mode_buttons.
    self._record_pulse_effect = QGraphicsOpacityEffect(self.playercontrols_record_button)
    self._record_pulse_effect.setOpacity(1.0)
    self.playercontrols_record_button.setGraphicsEffect(self._record_pulse_effect)
    self._record_pulse_anim = QPropertyAnimation(self._record_pulse_effect, b'opacity', self)
    self._record_pulse_anim.setDuration(1100)
    self._record_pulse_anim.setStartValue(1.0)
    self._record_pulse_anim.setKeyValueAt(0.5, 0.4)
    self._record_pulse_anim.setEndValue(1.0)
    self._record_pulse_anim.setEasingCurve(QEasingCurve.InOutSine)
    self._record_pulse_anim.setLoopCount(-1)
    self._record_pulse_running = False

    _update_record_mode_buttons(self)
    
    self.playercontrols_widget_top_line.layout().addSpacing(-30)

    self.playercontrols_widget_top_line.layout().addWidget(self.playercontrols_widget_center_top_line, 0)

    self.playercontrols_widget_top_line.layout().addSpacing(-30)

    self.playercontrols_widget_right_top_line = QWidget()
    self.playercontrols_widget_right_top_line.setObjectName('playercontrols_widget_right_top_line')
    # self.playercontrols_widget_right_top_line.setAttribute(Qt.WA_TranslucentBackground)
    self.playercontrols_widget_right_top_line.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
    self.playercontrols_widget_right_top_line.setLayout(QHBoxLayout())
    self.playercontrols_widget_right_top_line.layout().setContentsMargins(0, 2, 0, 0)
    self.playercontrols_widget_right_top_line.layout().setSpacing(8)
    self.playercontrols_widget_right_top_line.setAttribute(Qt.WA_LayoutOnEntireRect)

    self.repeat_playback = QPushButton()
    self.repeat_playback.setObjectName('repeat_playback')
    self.repeat_playback.setCheckable(True)
    self.repeat_playback.setLayout(QHBoxLayout())
    self.repeat_playback.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed))
    self.repeat_playback.setFixedHeight(48)
    self.repeat_playback.setIconSize(QSize(22, 22))
    self.repeat_playback.layout().setContentsMargins(50, 12, 16, 18)
    self.repeat_playback.layout().setSpacing(0)
    self.repeat_playback.clicked.connect(lambda: repeat_playback_clicked(self))

    self.repeat_playback_duration = EnterAbsorbingDoubleSpinBox()
    self.repeat_playback_duration.setProperty('class', 'spin_playercontrols')
    self.repeat_playback_duration.setMinimum(.1)
    self.repeat_playback_duration.setMaximum(60.)
    self.repeat_playback_duration.setFixedHeight(24)
    self.repeat_playback_duration.valueChanged.connect(lambda: repeat_playback_duration_changed(self))
    self.repeat_playback_duration.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
    self.repeat_playback.layout().addWidget(self.repeat_playback_duration, 0, alignment=Qt.AlignBottom)

    self.repeat_playback_x_label = QLabel('x')
    self.repeat_playback_x_label.setAlignment(Qt.AlignCenter)
    # self.repeat_playback_x_label.setFixedWidth(10)
    self.repeat_playback_x_label.setObjectName('repeat_playback_x_label')
    self.repeat_playback_x_label.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.repeat_playback.layout().addWidget(self.repeat_playback_x_label, 0)

    self.repeat_playback_times = QSpinBox()
    self.repeat_playback_times.setProperty('class', 'spin_playercontrols')
    self.repeat_playback_times.setMinimum(1)
    self.repeat_playback_times.setMaximum(20)
    self.repeat_playback_times.setFixedHeight(24)
    self.repeat_playback_times.valueChanged.connect(lambda: repeat_playback_times_changed(self))
    self.repeat_playback_times.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.repeat_playback.layout().addWidget(self.repeat_playback_times)

    _attach_collapsible(self.repeat_playback,
                        self.repeat_playback_duration,
                        self.repeat_playback_x_label,
                        self.repeat_playback_times)

    self.playercontrols_widget_right_top_line.layout().addWidget(self.repeat_playback)

    self.music_voice_separation_box = QWidget()
    self.music_voice_separation_box.setObjectName('music_voice_separation_box')
    self.music_voice_separation_box.setFixedHeight(42)
    self.music_voice_separation_box.setLayout(QHBoxLayout())
    self.music_voice_separation_box.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.music_voice_separation_box.layout().setContentsMargins(0, 0, 0, 0)
    self.music_voice_separation_box.layout().setSpacing(0)

    self.music_voice_separation_music_icon = QPushButton()
    self.music_voice_separation_music_icon.setObjectName('music_voice_separation_music_icon')
    self.music_voice_separation_music_icon.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.music_voice_separation_music_icon.setIconSize(QSize(24, 24))
    self.music_voice_separation_music_icon.clicked.connect(lambda: music_voice_separation_music_icon_clicked(self))
    self.music_voice_separation_box.layout().addWidget(self.music_voice_separation_music_icon)

    self.music_voice_separation_box.layout().addSpacing(-21)

    class CenterSnapDial(QDial):
        def __init__(self, center=None, threshold=3, parent=None):
            super().__init__(parent)
            self._center = center
            self._threshold = threshold
            self._middle_value = 50

            self.valueChanged.connect(self.handle_value_change)
    
        def handle_value_change(self, value):
            # Check if value is close to middle
            if abs(value - self._middle_value) <= self._threshold:
                # Snap to middle
                self.blockSignals(True)  # Prevent recursive signal
                self.setValue(self._middle_value)
                self.blockSignals(False)

    self.music_voice_separation_slider = CenterSnapDial(center=50, threshold=3)
    self.music_voice_separation_slider.setObjectName('music_voice_separation_slider')
    self.music_voice_separation_slider.setFixedSize(QSize(42, 42))
    self.music_voice_separation_slider.setRange(0,  100)
    self.music_voice_separation_slider.setValue(50)
    self.music_voice_separation_slider.valueChanged.connect(lambda: music_voice_separation_slider_changed(self))
    self.music_voice_separation_box.layout().addWidget(self.music_voice_separation_slider, 1)

    self.music_voice_separation_box.layout().addSpacing(-21)

    self.music_voice_separation_voice_icon = QPushButton()
    self.music_voice_separation_voice_icon.setObjectName('music_voice_separation_voice_icon')
    self.music_voice_separation_voice_icon.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.music_voice_separation_voice_icon.setIconSize(QSize(24, 24))
    self.music_voice_separation_voice_icon.clicked.connect(lambda: music_voice_separation_voice_icon_clicked(self))
    self.music_voice_separation_box.layout().addWidget(self.music_voice_separation_voice_icon)

    self.music_voice_separation_slider.raise_()

    def music_voice_separation_thread_finished(response):
        pending_volume = session.VIDEO.pop('music_voice_separation_volume_pending', None)
        if pending_volume is not None:
            try:
                response['volume'] = float(pending_volume)
            except (TypeError, ValueError):
                pass
        session.VIDEO['music_voice_separation'] = response
        self.preview_panel_player._audio_device.original_track.enabled = False

        background_source = self.preview_panel_player._audio_device.load_audio(session.VIDEO['music_voice_separation']['background'])
        background_clip = self.preview_panel_player._audio_device.load_clip(background_source)
        self.preview_panel_player._audio_device.background_sound = self.preview_panel_player._audio_device.generate_track()
        self.preview_panel_player._audio_device.background_sound.add_clip(background_clip)
        self.preview_panel_player._audio_device.add_track(self.preview_panel_player._audio_device.background_sound)

        vocals_source = self.preview_panel_player._audio_device.load_audio(session.VIDEO['music_voice_separation']['vocals'])
        vocals_clip = self.preview_panel_player._audio_device.load_clip(vocals_source)
        self.preview_panel_player._audio_device.vocals_sound = self.preview_panel_player._audio_device.generate_track()
        self.preview_panel_player._audio_device.vocals_sound.add_clip(vocals_clip)
        self.preview_panel_player._audio_device.add_track(self.preview_panel_player._audio_device.vocals_sound)

        music_voice_separation_box_update(self)

        # Re-run the timeline's onset detector now that the vocals
        # track exists — it picks up far more plosives on vocals-only
        # audio than on the full mix. Idempotent: if a vocals-onset
        # cache from a prior session is already loaded, this just
        # re-reads it.
        try:
            self.timeline_widget.on_vocals_separation_ready()
        except Exception:
            pass

    def music_voice_separation_thread_original_extracted(response):
        audio_source = self.preview_panel_player._audio_device.load_audio(response)
        clip = self.preview_panel_player._audio_device.load_clip(audio_source)
        self.preview_panel_player._audio_device.original_track = self.preview_panel_player._audio_device.generate_track()
        self.preview_panel_player._audio_device.original_track.add_clip(clip)
        self.preview_panel_player._audio_device.add_track(self.preview_panel_player._audio_device.original_track)
        self.preview_panel_player._audio_output = None
        # widget._media_player.setAudioOutput(widget._audio_output)

    self.music_voice_separation_thread = MusicAudioExtractorThread(parent=self)
    self.music_voice_separation_thread.response.connect(lambda response: music_voice_separation_thread_finished(response))
    self.music_voice_separation_thread.original.connect(lambda response: music_voice_separation_thread_original_extracted(response))   

    self.playercontrols_widget_right_top_line.layout().addWidget(self.music_voice_separation_box, 0, Qt.AlignTop)

    self.gap_hbox = QWidget()
    self.gap_hbox.setObjectName('gap_hbox')
    self.gap_hbox.setFixedHeight(42)
    self.gap_hbox.setLayout(QHBoxLayout())
    self.gap_hbox.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.gap_hbox.layout().setContentsMargins(0, 0, 0, 0)
    self.gap_hbox.layout().setSpacing(0)

    self.gap_add_subtitle_button = QPushButton()
    self.gap_add_subtitle_button.setObjectName('gap_add_subtitle_button')
    self.gap_add_subtitle_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.gap_add_subtitle_button.setIconSize(QSize(24, 24))
    self.gap_add_subtitle_button.clicked.connect(lambda: gap_add_subtitle_button_clicked(self))
    self.gap_hbox.layout().addWidget(self.gap_add_subtitle_button)

    self.gap_hbox.layout().addSpacing(-23)

    self.gap_subtitle_duration = QDoubleSpinBox()
    self.gap_subtitle_duration.setObjectName('gap_subtitle_duration')
    self.gap_subtitle_duration.setMinimum(.1)
    self.gap_subtitle_duration.setMaximum(60.)
    self.gap_subtitle_duration.setFixedSize(QSize(46, 26))
    self.gap_hbox.layout().addWidget(self.gap_subtitle_duration)

    self.gap_hbox.layout().addSpacing(-23)

    self.gap_remove_subtitle_button = QPushButton()
    self.gap_remove_subtitle_button.setObjectName('gap_remove_subtitle_button')
    self.gap_remove_subtitle_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.gap_remove_subtitle_button.setIconSize(QSize(24, 24))
    self.gap_remove_subtitle_button.clicked.connect(lambda: gap_remove_subtitle_button_clicked(self))
    self.gap_hbox.layout().addWidget(self.gap_remove_subtitle_button)

    self.gap_subtitle_duration.raise_()

    self.playercontrols_widget_right_top_line.layout().addWidget(self.gap_hbox, 0, Qt.AlignTop)

    self.add_remove_subtitle_frame = QWidget()
    self.add_remove_subtitle_frame.setLayout(QHBoxLayout())
    self.add_remove_subtitle_frame.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.add_remove_subtitle_frame.layout().setSizeConstraint(QLayout.SetMinimumSize)
    self.add_remove_subtitle_frame.layout().setContentsMargins(0, 0, 0, 0)
    self.add_remove_subtitle_frame.layout().setSpacing(0)

    self.add_subtitle_button = QPushButton()
    self.add_subtitle_button.setObjectName('add_subtitle_button')
    self.add_subtitle_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.add_subtitle_button.setIconSize(QSize(20, 20))
    self.add_subtitle_button.setFixedHeight(42)
    self.add_subtitle_button.setLayout(QHBoxLayout())
    self.add_subtitle_button.layout().setSizeConstraint(QLayout.SetMinimumSize)
    self.add_subtitle_button.layout().setContentsMargins(0, 0, 5, 5)
    self.add_subtitle_button.layout().setSpacing(0)
    self.add_subtitle_button.clicked.connect(lambda: add_subtitle_button_clicked(self))

    self.add_subtitle_button.layout().addStretch()

    self.add_subtitle_starting_from_last = QPushButton()
    self.add_subtitle_starting_from_last.setObjectName('add_subtitle_starting_from_last')
    self.add_subtitle_starting_from_last.setCheckable(True)
    self.add_subtitle_starting_from_last.setIconSize(QSize(20, 20))
    self.add_subtitle_starting_from_last.setFixedWidth(28)
    self.add_subtitle_starting_from_last.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.add_subtitle_starting_from_last.clicked.connect(lambda: add_subtitle_starting_from_last_clicked(self))
    self.add_subtitle_button.layout().addWidget(self.add_subtitle_starting_from_last, 1)

    self.add_subtitle_button.layout().addSpacing(-1)

    self.add_subtitle_and_play = QPushButton()
    self.add_subtitle_and_play.setObjectName('add_subtitle_and_play')
    self.add_subtitle_and_play.setCheckable(True)
    self.add_subtitle_and_play.setIconSize(QSize(20, 20))
    self.add_subtitle_and_play.setFixedWidth(20)
    self.add_subtitle_and_play.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.add_subtitle_and_play.clicked.connect(lambda: add_subtitle_and_play_clicked(self))
    self.add_subtitle_button.layout().addWidget(self.add_subtitle_and_play, 1)

    self.add_subtitle_button.layout().addSpacing(-1)

    self.add_subtitle_to_next_start = QPushButton()
    self.add_subtitle_to_next_start.setObjectName('add_subtitle_to_next_start')
    self.add_subtitle_to_next_start.setCheckable(True)
    self.add_subtitle_to_next_start.setIconSize(QSize(20, 20))
    self.add_subtitle_to_next_start.setFixedWidth(28)
    self.add_subtitle_to_next_start.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.add_subtitle_to_next_start.clicked.connect(lambda: add_subtitle_to_next_start_clicked(self))
    self.add_subtitle_button.layout().addWidget(self.add_subtitle_to_next_start, 1)

    self.add_subtitle_button.layout().addSpacing(6)

    self.add_subtitle_duration = QDoubleSpinBox()
    self.add_subtitle_duration.setObjectName('add_subtitle_duration')
    self.add_subtitle_duration.setMinimum(.1)
    self.add_subtitle_duration.setMaximum(60.)
    self.add_subtitle_duration.setValue(10.0)
    self.add_subtitle_duration.setFixedWidth(46)
    self.add_subtitle_duration.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.add_subtitle_duration.valueChanged.connect(lambda: add_subtitle_duration_changed(self))
    self.add_subtitle_button.layout().addWidget(self.add_subtitle_duration, 1)

    self.add_remove_subtitle_frame.layout().addWidget(self.add_subtitle_button)

    self.remove_selected_subtitle_button = QPushButton()
    self.remove_selected_subtitle_button.setObjectName('remove_selected_subtitle_button')
    self.remove_selected_subtitle_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.remove_selected_subtitle_button.setIconSize(QSize(20, 20))
    self.remove_selected_subtitle_button.setFixedHeight(42)
    self.remove_selected_subtitle_button.clicked.connect(lambda: remove_selected_subtitle_button_clicked(self))
    self.add_remove_subtitle_frame.layout().addWidget(self.remove_selected_subtitle_button)

    self.playercontrols_widget_right_top_line.layout().addWidget(self.add_remove_subtitle_frame, 0, Qt.AlignTop)
    
    self.playercontrols_widget_right_top_line.layout().addStretch()

    self.playercontrols_widget_top_line.layout().addWidget(self.playercontrols_widget_right_top_line, 1)

    self.playercontrols_widget_left_top_line.raise_()
    self.playercontrols_widget_right_top_line.raise_()

    self.playercontrols_widget.layout().addWidget(self.playercontrols_widget_top_line, 0)
    
    self.playercontrols_widget_bottom_line = QWidget()
    self.playercontrols_widget_bottom_line.setObjectName('playercontrols_widget_bottom_line')
    # self.playercontrols_widget_bottom_line.setFixedHeight(26)
    self.playercontrols_widget_bottom_line.setLayout(QHBoxLayout())
    self.playercontrols_widget_bottom_line.layout().setContentsMargins(0, 0, 0, 0)
    self.playercontrols_widget_bottom_line.layout().setSpacing(0)
    # self.playercontrols_widget_bottom_line.setAttribute(Qt.WA_LayoutOnEntireRect)
    self.playercontrols_widget_bottom_line.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum))

    self.playercontrols_widget_left_bottom_line = QWidget()
    self.playercontrols_widget_left_bottom_line.setObjectName('playercontrols_widget_left_bottom_line')
    # self.playercontrols_widget_left_bottom_line.setAttribute(Qt.WA_TranslucentBackground)
    # self.playercontrols_widget_left_bottom_line.setAttribute(Qt.WA_LayoutOnEntireRect)
    self.playercontrols_widget_left_bottom_line.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
    self.playercontrols_widget_left_bottom_line.setLayout(QHBoxLayout())
    self.playercontrols_widget_left_bottom_line.layout().setContentsMargins(0, 2, 0, 1)#0, 3, 0, 1)
    self.playercontrols_widget_left_bottom_line.layout().setSpacing(6)

    self.playercontrols_widget_left_bottom_line.layout().addStretch()

    self.timeline_speaker_container = QWidget()
    self.timeline_speaker_container.setObjectName('timeline_speaker_container')
    self.timeline_speaker_container.setLayout(QHBoxLayout())
    self.timeline_speaker_container.layout().setContentsMargins(0, 0, 0, 0)
    self.timeline_speaker_container.layout().setSpacing(0)

    self.timeline_show_speaker_color_button = QPushButton()
    self.timeline_show_speaker_color_button.setObjectName('timeline_show_speaker_color_button')
    self.timeline_show_speaker_color_button.setCheckable(True)
    self.timeline_show_speaker_color_button.setIconSize(QSize(16, 16))
    self.timeline_show_speaker_color_button.setFixedWidth(24)
    self.timeline_show_speaker_color_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.timeline_show_speaker_color_button.clicked.connect(lambda: timeline_show_speaker_color_button_clicked(self))
    self.timeline_speaker_container.layout().addWidget(self.timeline_show_speaker_color_button)

    self.timeline_show_speaker_tracks_button = QPushButton()
    self.timeline_show_speaker_tracks_button.setObjectName('timeline_show_speaker_tracks_button')
    self.timeline_show_speaker_tracks_button.setCheckable(True)
    self.timeline_show_speaker_tracks_button.setIconSize(QSize(16, 16))
    self.timeline_show_speaker_tracks_button.setFixedWidth(24)
    self.timeline_show_speaker_tracks_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.timeline_show_speaker_tracks_button.clicked.connect(lambda: timeline_show_speaker_tracks_button_clicked(self))
    self.timeline_speaker_container.layout().addWidget(self.timeline_show_speaker_tracks_button)

    self.playercontrols_widget_left_bottom_line.layout().addWidget(self.timeline_speaker_container)

    self.timelinescrolling_container = QWidget()
    self.timelinescrolling_container.setObjectName('timelinescrolling_container')
    self.timelinescrolling_container.setLayout(QHBoxLayout())
    self.timelinescrolling_container.layout().setContentsMargins(0, 0, 0, 0)
    self.timelinescrolling_container.layout().setSpacing(0)

    self.timelinescrolling_none_button = QPushButton()
    self.timelinescrolling_none_button.setObjectName('timelinescrolling_none_button')
    self.timelinescrolling_none_button.setCheckable(True)
    self.timelinescrolling_none_button.setIconSize(QSize(16, 16))
    self.timelinescrolling_none_button.setFixedWidth(24)
    self.timelinescrolling_none_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.timelinescrolling_none_button.clicked.connect(lambda: timelinescrolling_type_changed(self, 'none'))
    self.timelinescrolling_container.layout().addWidget(self.timelinescrolling_none_button)

    self.timelinescrolling_page_button = QPushButton()
    self.timelinescrolling_page_button.setObjectName('timelinescrolling_page_button')
    self.timelinescrolling_page_button.setCheckable(True)
    self.timelinescrolling_page_button.setIconSize(QSize(16, 16))
    self.timelinescrolling_page_button.setFixedWidth(24)
    self.timelinescrolling_page_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.timelinescrolling_page_button.clicked.connect(lambda: timelinescrolling_type_changed(self, 'page'))
    self.timelinescrolling_container.layout().addWidget(self.timelinescrolling_page_button)

    self.timelinescrolling_follow_button = QPushButton()
    self.timelinescrolling_follow_button.setObjectName('timelinescrolling_follow_button')
    self.timelinescrolling_follow_button.setCheckable(True)
    self.timelinescrolling_follow_button.setIconSize(QSize(16, 16))
    self.timelinescrolling_follow_button.setFixedWidth(24)
    self.timelinescrolling_follow_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.timelinescrolling_follow_button.clicked.connect(lambda: timelinescrolling_type_changed(self, 'follow'))
    self.timelinescrolling_container.layout().addWidget(self.timelinescrolling_follow_button)

    self.playercontrols_widget_left_bottom_line.layout().addWidget(self.timelinescrolling_container)

    self.snap_controls_container = QWidget()
    self.snap_controls_container.setObjectName('snap_controls_container')
    self.snap_controls_container.setLayout(QHBoxLayout())
    self.snap_controls_container.layout().setContentsMargins(0, 0, 0, 0)
    self.snap_controls_container.layout().setSpacing(0)

    self.snap_button = QPushButton()
    self.snap_button.setObjectName('snap_button')
    self.snap_button.setCheckable(True)
    self.snap_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.snap_button.clicked.connect(lambda: snap_button_clicked(self))
    self.snap_button.setLayout(QHBoxLayout())
    self.snap_button.layout().setSizeConstraint(QLayout.SetMinimumSize)
    self.snap_button.layout().setContentsMargins(4, 0, 0, 0)
    self.snap_button.layout().setSpacing(4)
    self.snap_controls_container.layout().addWidget(self.snap_button)

    self.snap_button_label = QLabel()
    self.snap_button_label.setObjectName('snap_button_label')
    self.snap_button_label.setAlignment(Qt.AlignCenter)
    self.snap_button_label.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.snap_button.layout().addWidget(self.snap_button_label)

    self.snap_value = QDoubleSpinBox()
    self.snap_value.setProperty('class', 'spin_playercontrols')
    self.snap_value.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.snap_value.valueChanged.connect(lambda: snap_value_changed(self))
    self.snap_button.layout().addWidget(self.snap_value)

    self.snap_grid_button = QPushButton()
    self.snap_grid_button.setObjectName('snap_grid_button')
    self.snap_grid_button.setCheckable(True)
    self.snap_grid_button.setIconSize(QSize(24, 16))
    self.snap_grid_button.setFixedWidth(28)
    self.snap_grid_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.snap_grid_button.clicked.connect(lambda: snap_grid_button_clicked(self))
    self.snap_controls_container.layout().addWidget(self.snap_grid_button)

    self.snap_move_button = QPushButton()
    self.snap_move_button.setObjectName('snap_move_button')
    self.snap_move_button.setCheckable(True)
    self.snap_move_button.setIconSize(QSize(24, 16))
    self.snap_move_button.setFixedWidth(24)
    self.snap_move_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.snap_move_button.clicked.connect(lambda: snap_move_button_clicked(self))
    self.snap_controls_container.layout().addWidget(self.snap_move_button)

    self.snap_limits_button = QPushButton()
    self.snap_limits_button.setObjectName('snap_limits_button')
    self.snap_limits_button.setCheckable(True)
    self.snap_limits_button.setIconSize(QSize(24, 16))
    self.snap_limits_button.setFixedWidth(24)
    self.snap_limits_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.snap_limits_button.clicked.connect(lambda: snap_limits_button_clicked(self))
    self.snap_controls_container.layout().addWidget(self.snap_limits_button)

    self.snap_move_nereast_button = QPushButton()
    self.snap_move_nereast_button.setObjectName('snap_move_nereast_button')
    self.snap_move_nereast_button.setCheckable(True)
    self.snap_move_nereast_button.setIconSize(QSize(24, 16))
    self.snap_move_nereast_button.setFixedWidth(28)
    self.snap_move_nereast_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.snap_move_nereast_button.clicked.connect(lambda: snap_move_nereast_button_clicked(self))
    self.snap_controls_container.layout().addWidget(self.snap_move_nereast_button)

    self.playercontrols_widget_left_bottom_line.layout().addWidget(self.snap_controls_container)

    self.move_start_container = QWidget()
    self.move_start_container.setObjectName('move_start_container')
    self.move_start_container.setLayout(QHBoxLayout())
    self.move_start_container.layout().setContentsMargins(0, 0, 0, 0)
    self.move_start_container.layout().setSpacing(0)

    self.move_start_back_subtitle = QPushButton()
    self.move_start_back_subtitle.setObjectName('move_start_back_subtitle')
    self.move_start_back_subtitle.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.move_start_back_subtitle.setFixedWidth(24)
    self.move_start_back_subtitle.clicked.connect(lambda: move_start_back_subtitle_clicked(self))
    self.move_start_container.layout().addWidget(self.move_start_back_subtitle)

    self.move_start_forward_subtitle = QPushButton()
    self.move_start_forward_subtitle.setObjectName('move_start_forward_subtitle')
    self.move_start_forward_subtitle.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.move_start_forward_subtitle.setFixedWidth(24)
    self.move_start_forward_subtitle.clicked.connect(lambda: move_start_forward_subtitle_clicked(self))
    self.move_start_container.layout().addWidget(self.move_start_forward_subtitle)

    self.playercontrols_widget_left_bottom_line.layout().addWidget(self.move_start_container)

    self.move_backward_subtitle = QPushButton()
    self.move_backward_subtitle.setObjectName('move_backward_subtitle')
    self.move_backward_subtitle.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.move_backward_subtitle.setFixedWidth(24)
    self.move_backward_subtitle.clicked.connect(lambda: move_backward_subtitle_clicked(self))
    self.playercontrols_widget_left_bottom_line.layout().addWidget(self.move_backward_subtitle)

    self.timeline_cursor_back_frame = QPushButton()
    self.timeline_cursor_back_frame.setObjectName('timeline_cursor_back_frame')
    self.timeline_cursor_back_frame.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.timeline_cursor_back_frame.setFixedWidth(24)
    self.timeline_cursor_back_frame.clicked.connect(lambda: timeline_cursor_back_frame_clicked(self))
    self.playercontrols_widget_left_bottom_line.layout().addWidget(self.timeline_cursor_back_frame)

    self.playercontrols_widget_bottom_line.layout().addWidget(self.playercontrols_widget_left_bottom_line)

    self.playercontrols_widget_bottom_line.layout().addSpacing(-12)

    self.playercontrols_widget_center_bottom_line = QWidget()
    self.playercontrols_widget_center_bottom_line.setObjectName('playercontrols_widget_center_bottom_line')
    # self.playercontrols_widget_center_bottom_line.setFixedHeight(26)
    # self.playercontrols_widget_center_bottom_line.setAttribute(Qt.WA_TranslucentBackground)
    # self.playercontrols_widget_center_bottom_line.setAttribute(Qt.WA_LayoutOnEntireRect)
    self.playercontrols_widget_center_bottom_line.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.playercontrols_widget_center_bottom_line.setLayout(QHBoxLayout())
    self.playercontrols_widget_center_bottom_line.layout().setContentsMargins(0, 0, 0, 0)
    self.playercontrols_widget_center_bottom_line.layout().setSpacing(0)

    class playercontrols_timecode_label(QLabel):
        def __init__(widget, *args):
            super(playercontrols_timecode_label, widget).__init__(*args)
            widget.setAlignment(Qt.AlignCenter)
            widget.setFixedWidth(120)
            widget.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
            widget.setObjectName('playercontrols_timecode_label')
        
        def update_time(widget):
            widget.setText(str(timecode.Timecode('1000', start_seconds=session.SUBTITLE.get('position', 0), fractional=True)))

    self.playercontrols_timecode_label = playercontrols_timecode_label()
    self.playercontrols_widget_center_bottom_line.layout().addWidget(self.playercontrols_timecode_label)
    self.preview_panel_player.position_changed_signal.connect(self.playercontrols_timecode_label.update_time)
    # Playhead repaint cadence during playback.
    #
    # Originally this was a direct connect to `timeline.update(self)` per
    # QMediaPlayer position update (~30/sec). Each call invalidates the
    # whole timeline → full paintEvent → 30-60 ms of Python+Qt work
    # holding the GIL, starving the audio callback (42.7 ms block budget).
    # That was the audio-chunkiness root cause.
    #
    # We then throttled to 4 Hz, which fixed the audio but made the
    # playhead visibly jerky (250 ms steps = ~8 px jumps at typical zoom).
    #
    # Now that the audio engine is allocation-free (no SoundFile opens, no
    # np.linspace / np.interp on the realtime thread — see
    # SubtitleDubClip.read fast path), the GIL pressure from a paint
    # event is bounded by the C++ paint pipeline, not the Python loop.
    # We can run at ~30 Hz comfortably AND we ask for a targeted region
    # invalidation — only the old + new playhead strips, not the whole
    # widget — so the paint loop short-circuits early most of the time.
    #
    # Subtle: QMediaPlayer.positionChanged fires at only ~10 Hz on most
    # backends (Qt6 doesn't expose setNotifyInterval). If we just read
    # session.SUBTITLE['position'] at 30 Hz, we'd be repainting the
    # *same* x three times then jumping a big chunk — visibly chunky.
    # So we extrapolate: anchor (last reported position, wall-clock
    # time) on each QMediaPlayer emit, then advance by (elapsed
    # wall-clock × playback_speed) on every timer tick. Result: the
    # playhead glides instead of stepping.
    import time as _time
    self._timeline_repaint_timer = QTimer(self)
    # 30 Hz: a deliberate trade against the previous 60 Hz. Each tick
    # schedules a paintEvent that runs the full timeline draw pipeline
    # (Qt only clips the output, the Python iteration still runs).
    # On a project with many dubs + onset markers enabled, that paint
    # can take 20-40 ms — at 60 Hz back-to-back paints leave no GIL
    # time for the sounddevice audio callback, so audio cuts when the
    # user also scrolls or drags. 30 Hz halves the paint pressure
    # while still feeling smooth (one frame per 2 display refreshes at
    # 60 Hz; the cursor moves ~2 px per tick at typical zoom, well
    # below the perceptual stepping threshold).
    self._timeline_repaint_timer.setInterval(33)  # ~30 Hz
    self._last_playhead_x = -1.0
    # Anchor state for position extrapolation. Set on every
    # QMediaPlayer positionChanged emit.
    self._pos_anchor_sec = 0.0
    self._pos_anchor_walltime = 0.0
    # Cap extrapolation so that if QMediaPlayer stalls (e.g. seeking,
    # buffering) the playhead doesn't fly off into the future.
    self._pos_extrapolation_cap_sec = 0.25

    def _is_playing():
        """True iff QMediaPlayer is in PlayingState right now. Used to
        gate extrapolation: when the player is paused/stopped, wall
        clock keeps advancing but playback does NOT, so multiplying
        elapsed × speed makes the cursor slide off the click position
        on every timer tick (see bug from 2026-05). Read live each
        call — playbackStateChanged is async and we want the answer
        for THIS tick, not the last one we observed."""
        mp = getattr(self.preview_panel_player, '_media_player', None)
        if mp is None:
            return False
        return mp.playbackState() == QMediaPlayer.PlayingState

    def _audio_engine():
        """Return the dub audio engine, or None if it's not wired yet
        (very early init / unit-test contexts). The engine's
        ``playhead`` getter is the master clock for the whole app:
        timeline cursor, subtitle highlight, video sync all read from
        here. Audio is first-class — every other clock chases this one."""
        try:
            return self.preview_panel_player._audio_device
        except AttributeError:
            return None

    def _smoothed_position_sec():
        """Return the playhead position to draw *now*, anchored on the
        audio engine's consumer playhead and extrapolated by wall-clock
        between callback ticks.

        The audio engine advances its playhead once per audio callback
        (~85 ms at blocksize=4096). The cursor refresh runs at 30 Hz
        (~33 ms). So between two audio callbacks the cursor would
        otherwise show the same x three times then jump — visibly
        chunky. We anchor on the audio engine's reading and extrapolate
        with wall-clock × speed so the cursor glides instead.

        When the player is NOT playing the anchor is correct and
        wall-clock extrapolation must NOT run, otherwise the cursor
        would drift away from the seek position. Return the raw audio
        engine playhead in that case.

        ``_pos_anchor_*`` are still used as the extrapolation memo so
        we don't have to acquire the audio-engine lock 30 times per
        second — we only re-anchor when the engine actually advanced.
        """
        engine = _audio_engine()
        if engine is None:
            return float(session.SUBTITLE.get('position', 0) or 0)
        ae_pos = engine.playhead
        if not _is_playing():
            # Paused — engine playhead is canonical, don't extrapolate.
            self._pos_anchor_sec = ae_pos
            self._pos_anchor_walltime = 0.0
            return ae_pos
        now = _time.perf_counter()
        # If the engine advanced (callback fired), re-anchor.
        if (self._pos_anchor_walltime <= 0
                or ae_pos != self._pos_anchor_sec):
            self._pos_anchor_sec = ae_pos
            self._pos_anchor_walltime = now
            return ae_pos
        elapsed = now - self._pos_anchor_walltime
        if elapsed < 0:
            elapsed = 0.0
        if elapsed > self._pos_extrapolation_cap_sec:
            elapsed = self._pos_extrapolation_cap_sec
        speed = float(session.CONFIG.get('playback_speed', 1.0) or 1.0)
        return self._pos_anchor_sec + elapsed * speed

    def _repaint_playhead_strip():
        from PySide6.QtCore import QRect
        tl_widget = getattr(self, 'timeline_widget', None)
        if tl_widget is None:
            timeline.update(self)
            return
        smoothed = _smoothed_position_sec()
        # Write the smoothed value back so timeline.paintEvent draws the
        # cursor at the interpolated x (and so anything else that polls
        # the current position sees the gliding value). The next
        # positionChanged emit will overwrite with the canonical value
        # and re-anchor — see _on_position_changed.
        session.SUBTITLE['position'] = smoothed
        wpp = getattr(tl_widget, 'width_proportion', 1.0) or 1.0
        new_x = smoothed * wpp
        old_x = self._last_playhead_x
        self._last_playhead_x = new_x
        # Strip wide enough to catch the cursor (2 px) + the speed/repeat
        # badge that extends ~80 px to the left of it, with safety pad.
        pad_left = 96
        pad_right = 8
        h = tl_widget.height()
        if old_x < 0:
            # First frame after play() — we don't know the previous x, so
            # invalidate broadly. Still cheaper than full update because
            # we cap the strip width.
            tl_widget.update(QRect(int(new_x) - pad_left, 0,
                                   pad_left + pad_right, h))
        else:
            left = int(min(old_x, new_x)) - pad_left
            right = int(max(old_x, new_x)) + pad_right
            tl_widget.update(QRect(left, 0, right - left, h))

    self._timeline_repaint_timer.timeout.connect(_repaint_playhead_strip)

    def _on_position_changed():
        """Used to drive the cursor anchor. Now the audio engine is the
        master clock (see ``_smoothed_position_sec``), so the only thing
        we do on QMediaPlayer's positionChanged is start the cursor
        refresh timer the first time we see "playing" — the audio
        engine itself provides the position values."""
        if _is_playing() and not self._timeline_repaint_timer.isActive():
            self._last_playhead_x = -1.0
            self._timeline_repaint_timer.start()

    def _stop_timeline_repaint_timer():
        self._timeline_repaint_timer.stop()
        self._last_playhead_x = -1.0
        self._pos_anchor_walltime = 0.0
        # One last full update so the playhead lands on the final frame
        # and any partial-strip artifacts disappear.
        timeline.update(self)
        # Stop the video-sync watchdog too — it has nothing to chase
        # while paused.
        if hasattr(self, '_av_sync_timer') and self._av_sync_timer is not None:
            self._av_sync_timer.stop()

    self.preview_panel_player.position_changed_signal.connect(_on_position_changed)
    # Stop the timer when playback pauses so the timeline isn't repainting
    # forever after.
    self.preview_panel_player._media_player.playbackStateChanged.connect(
        lambda state: _stop_timeline_repaint_timer() if state != QMediaPlayer.PlayingState else None
    )

    # ---- Audio/video sync watchdog -----------------------------------
    # The audio engine (SoundDeviceAudioEngine) is the master clock for
    # this app. QMediaPlayer runs the video on its own internal clock,
    # which drifts from the audio engine over time (different timebases,
    # different scheduling jitter). This 4 Hz watchdog measures the
    # drift between QMediaPlayer.position() and audio_engine.playhead,
    # and when drift exceeds ``_AV_SYNC_THRESHOLD_MS`` it hard-snaps the
    # video to the audio position. Audio is never touched.
    #
    # Why 4 Hz: the watchdog correction itself is a setPosition() call
    # which forces QMediaPlayer to decode a new keyframe — that's a
    # ~30-100 ms hiccup in the video stream. Doing it more than ~4
    # times a second would make the video visibly stutter. 4 Hz keeps
    # average drift bounded to ~250 ms before the next check, and the
    # threshold (100 ms below) means we only actually correct on real
    # drift, not on every check.
    #
    # The threshold is asymmetric in cost: 100 ms of A/V offset is
    # audible (lipsync). Lower threshold → more frequent video hiccups.
    # 100 ms is the standard pro broadcast tolerance.
    _AV_SYNC_THRESHOLD_MS = 100

    def _av_sync_tick():
        engine = _audio_engine()
        if engine is None:
            return
        mp = self.preview_panel_player._media_player
        if mp.playbackState() != QMediaPlayer.PlayingState:
            return
        audio_pos = engine.playhead          # seconds
        video_pos_ms = mp.position()         # ms
        drift_ms = video_pos_ms - audio_pos * 1000.0
        if abs(drift_ms) > _AV_SYNC_THRESHOLD_MS:
            # Snap the video to audio. Audio is never touched.
            mp.setPosition(int(audio_pos * 1000.0))

    self._av_sync_timer = QTimer(self)
    self._av_sync_timer.setInterval(250)  # 4 Hz
    self._av_sync_timer.timeout.connect(_av_sync_tick)

    def _on_playback_started():
        # When QMediaPlayer transitions to PlayingState, kick BOTH:
        # the 30 Hz cursor refresh (so the cursor starts moving even
        # if QMediaPlayer hasn't emitted positionChanged yet) and the
        # 4 Hz video-sync watchdog.
        if not self._timeline_repaint_timer.isActive():
            self._last_playhead_x = -1.0
            self._timeline_repaint_timer.start()
        self._av_sync_timer.start()

    self.preview_panel_player._media_player.playbackStateChanged.connect(
        lambda state: _on_playback_started()
        if state == QMediaPlayer.PlayingState else None
    )

    self.playercontrols_widget_bottom_line.layout().addWidget(self.playercontrols_widget_center_bottom_line)

    self.playercontrols_widget_left_bottom_line.raise_()

    self.playercontrols_widget_bottom_line.layout().addSpacing(-12)

    self.playercontrols_widget_right_bottom_line = QWidget()
    self.playercontrols_widget_right_bottom_line.setObjectName('playercontrols_widget_right_bottom_line')
    # self.playercontrols_widget_right_bottom_line.setAttribute(Qt.WA_TranslucentBackground)
    self.playercontrols_widget_right_bottom_line.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
    self.playercontrols_widget_right_bottom_line.setLayout(QHBoxLayout())
    self.playercontrols_widget_right_bottom_line.layout().setContentsMargins(0, 2, 0, 1)
    self.playercontrols_widget_right_bottom_line.layout().setSpacing(6)
    
    self.timeline_cursor_next_frame = QPushButton()
    self.timeline_cursor_next_frame.setObjectName('timeline_cursor_next_frame')
    self.timeline_cursor_next_frame.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.timeline_cursor_next_frame.setFixedWidth(24)
    self.timeline_cursor_next_frame.clicked.connect(lambda: timeline_cursor_next_frame_clicked(self))
    self.playercontrols_widget_right_bottom_line.layout().addWidget(self.timeline_cursor_next_frame)

    self.move_forward_subtitle = QPushButton()
    self.move_forward_subtitle.setObjectName('move_forward_subtitle')
    self.move_forward_subtitle.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.move_forward_subtitle.setFixedWidth(24)
    self.move_forward_subtitle.clicked.connect(lambda: move_forward_subtitle_clicked(self))
    self.playercontrols_widget_right_bottom_line.layout().addWidget(self.move_forward_subtitle)

    self.move_end_container = QWidget()
    self.move_end_container.setObjectName('move_end_container')
    self.move_end_container.setLayout(QHBoxLayout())
    self.move_end_container.layout().setContentsMargins(0, 0, 0, 0)
    self.move_end_container.layout().setSpacing(0)

    self.move_end_back_subtitle = QPushButton()
    self.move_end_back_subtitle.setObjectName('move_end_back_subtitle')
    self.move_end_back_subtitle.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.move_end_back_subtitle.setFixedWidth(24)
    self.move_end_back_subtitle.clicked.connect(lambda: move_end_back_subtitle_clicked(self))
    self.move_end_container.layout().addWidget(self.move_end_back_subtitle)

    self.move_end_forward_subtitle = QPushButton()
    self.move_end_forward_subtitle.setObjectName('move_end_forward_subtitle')
    self.move_end_forward_subtitle.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.move_end_forward_subtitle.setFixedWidth(24)
    self.move_end_forward_subtitle.clicked.connect(lambda: move_end_forward_subtitle_clicked(self))
    self.move_end_container.layout().addWidget(self.move_end_forward_subtitle)

    self.playercontrols_widget_right_bottom_line.layout().addWidget(self.move_end_container)

    self.grid_controls_container = QWidget()
    self.grid_controls_container.setObjectName('grid_controls_container')
    self.grid_controls_container.setLayout(QHBoxLayout())
    self.grid_controls_container.layout().setContentsMargins(0, 0, 0, 0)
    self.grid_controls_container.layout().setSpacing(0)

    self.grid_button = QPushButton()
    self.grid_button.setObjectName('grid_button')
    self.grid_button.setCheckable(True)
    self.grid_button.clicked.connect(lambda: grid_button_clicked(self))
    self.grid_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.grid_controls_container.layout().addWidget(self.grid_button)

    self.grid_frames_button = QPushButton()
    self.grid_frames_button.setObjectName('grid_frames_button')
    self.grid_frames_button.setCheckable(True)
    self.grid_frames_button.clicked.connect(lambda: grid_type_changed(self, 'frames'))
    self.grid_frames_button.setIconSize(QSize(16, 16))
    self.grid_frames_button.setFixedWidth(24)
    self.grid_frames_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.grid_controls_container.layout().addWidget(self.grid_frames_button)

    self.grid_seconds_button = QPushButton()
    self.grid_seconds_button.setObjectName('grid_seconds_button')
    self.grid_seconds_button.setCheckable(True)
    self.grid_seconds_button.clicked.connect(lambda: grid_type_changed(self, 'seconds'))
    self.grid_seconds_button.setIconSize(QSize(16, 16))
    self.grid_seconds_button.setFixedWidth(24)
    self.grid_seconds_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.grid_controls_container.layout().addWidget(self.grid_seconds_button)

    self.grid_scenes_button = QPushButton()
    self.grid_scenes_button.setObjectName('grid_scenes_button')
    self.grid_scenes_button.setCheckable(True)
    self.grid_scenes_button.setIconSize(QSize(16, 16))
    self.grid_scenes_button.setFixedWidth(24)
    self.grid_scenes_button.clicked.connect(lambda: grid_type_changed(self, 'scenes'))
    self.grid_scenes_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.grid_controls_container.layout().addWidget(self.grid_scenes_button)

    self.playercontrols_widget_right_bottom_line.layout().addWidget(self.grid_controls_container)

    # Onset markers (consonant-onset visual aid on the timeline — see
    # `subtitld.modules.onset_detection`). Single checkable toggle; the
    # actual detection runs once per audio file as a background pass and
    # caches to disk.
    self.onset_controls_container = QWidget()
    self.onset_controls_container.setObjectName('onset_controls_container')
    self.onset_controls_container.setLayout(QHBoxLayout())
    self.onset_controls_container.layout().setContentsMargins(0, 0, 0, 0)
    self.onset_controls_container.layout().setSpacing(0)

    self.onset_button = QPushButton()
    self.onset_button.setObjectName('onset_button')
    self.onset_button.setCheckable(True)
    self.onset_button.clicked.connect(lambda: onset_button_clicked(self))
    self.onset_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.onset_controls_container.layout().addWidget(self.onset_button)

    self.playercontrols_widget_right_bottom_line.layout().addWidget(self.onset_controls_container)

    self.step_button_container = QWidget()
    self.step_button_container.setObjectName('step_button_container')
    self.step_button_container.setLayout(QHBoxLayout())
    self.step_button_container.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.step_button_container.layout().setContentsMargins(0, 0, 0, 0)
    self.step_button_container.layout().setSpacing(0)

    self.step_button = QPushButton()
    self.step_button.setObjectName('step_button')   
    self.step_button.setCheckable(True)
    self.step_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.step_button.clicked.connect(lambda: update_step_buttons(self))
    self.step_button_container.layout().addWidget(self.step_button)
    
    self.step_value_f = QDoubleSpinBox()
    self.step_value_f.setObjectName('step_value_f')
    self.step_value_f.setMinimum(.001)
    self.step_value_f.setMaximum(999.999)
    self.step_value_f.valueChanged.connect(lambda: step_value_changed(self))
    self.step_value_f.setFixedHeight(24)
    self.step_value_f.setFixedWidth(50)
    self.step_value_f.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.step_button_container.layout().addWidget(self.step_value_f)

    self.step_value_i = QSpinBox()
    self.step_value_i.setObjectName('step_value_i')
    self.step_value_i.setMinimum(1)
    self.step_value_i.setMaximum(999)
    self.step_value_i.setFixedHeight(24)
    self.step_value_i.setFixedWidth(50)
    self.step_value_i.valueChanged.connect(lambda: step_value_changed(self))
    self.step_value_i.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.step_button_container.layout().addWidget(self.step_value_i)

    self.step_unit = QComboBox()
    self.step_unit.setObjectName('step_unit')
    self.step_unit.insertItems(0, STEPS_LIST)
    self.step_unit.activated.connect(lambda: step_value_changed(self))
    # self.step_unit.setFixedHeight(24)
    # self.step_unit.setFixedWidth(80)
    self.step_unit.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.step_button_container.layout().addWidget(self.step_unit, 1)

    self.playercontrols_widget_right_bottom_line.layout().addWidget(self.step_button_container)

    self.zoom_controls_container = QWidget()
    self.zoom_controls_container.setObjectName('zoom_controls_container')
    self.zoom_controls_container.setLayout(QHBoxLayout())
    self.zoom_controls_container.layout().setContentsMargins(0, 0, 0, 0)
    self.zoom_controls_container.layout().setSpacing(0)

    self.zoomin_button = QPushButton(parent=self.playercontrols_widget)
    self.zoomin_button.setObjectName('zoomin_button')
    self.zoomin_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.zoomin_button.clicked.connect(lambda: zoomin_button_clicked(self))
    self.zoom_controls_container.layout().addWidget(self.zoomin_button)

    self.zoomout_button = QPushButton(parent=self.playercontrols_widget)
    self.zoomout_button.setObjectName('zoomout_button')
    self.zoomout_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.zoomout_button.clicked.connect(lambda: zoomout_button_clicked(self))
    self.zoom_controls_container.layout().addWidget(self.zoomout_button)

    self.playercontrols_widget_right_bottom_line.layout().addWidget(self.zoom_controls_container)

    self.playercontrols_widget_right_bottom_line.layout().addStretch()

    self.playercontrols_widget_bottom_line.layout().addWidget(self.playercontrols_widget_right_bottom_line)

    self.playercontrols_widget.layout().addWidget(self.playercontrols_widget_bottom_line, 0)

    self.playercontrols_widget.layout().addSpacing(-25)

    timeline.load(self)
    
    self.playercontrols_widget.layout().addWidget(self.timeline_scroll, 1)

    self.playercontrols_widget_bottom_line.raise_()

    '''
    self.playercontrols_properties_panel_placeholder = QWidget(parent=self.playercontrols_widget)

    self.playercontrols_properties_panel = QLabel(parent=self.playercontrols_properties_panel_placeholder)
    self.playercontrols_properties_panel.setObjectName('player_controls_sub_panel')
    self.playercontrols_properties_panel.setStyleSheet('QLabel {border-top:0; border-right:0;}')
    # self.playercontrols_properties_panel_animation = QPropertyAnimation(self.playercontrols_properties_panel, b'geometry')
    # self.playercontrols_properties_panel_animation.setEasingCurve(QEasingCurve.OutCirc)

    self.playercontrols_properties_panel_tabwidget = QTabWidget(parent=self.playercontrols_properties_panel)
    self.playercontrols_properties_panel_tabwidget.setObjectName('playercontrols_properties_panel_tabwidget')
    self.playercontrols_properties_panel_tabwidget.setTabBar(QLeftTabBar(self.playercontrols_properties_panel_tabwidget))
    self.playercontrols_properties_panel_tabwidget.setTabPosition(QTabWidget.West)
    self.playercontrols_properties_panel_tabwidget.setStyleSheet(
                            #QTabBar:tab                                    { background: rgba(184,206,224,150); color: rgba(46,62,76,150); border: 1px solid rgba(106, 116, 131, 100); padding: 10px; border-top-left-radius: 2px; border-top-right-radius: 0; border-bottom-left-radius: 2px; border-left:0; padding-top: -16px; padding-left: 4px; padding-bottom:6px; padding-right:2px; }
                            #QTabBar:tab:selected                           { background: rgb(184,206,224); color: rgb(46,62,76); border: 1px solid rgb(106, 116, 131); border-right:0; }
                            #QTabWidget:pane                                { background: rgb(184,206,224); border: 1px solid rgb(106, 116, 131); border-bottom-right-radius: 2px;  border-top-left-radius: 0; border-top-right-radius: 2px; border-left: 0;}
                            )

    self.playercontrols_properties_panel_tabwidget_subtitles = QWidget()

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_title_normal = QLabel('Normal subtitle colors'.upper(), parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_title_normal.setObjectName('small_label')
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_title_normal.setStyleSheet(' QLabel {font-weight:bold;} ')

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_border_color_title_normal = QLabel('Border'.upper(), parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_border_color_title_normal.setObjectName('small_label')

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_border_color_button = QPushButton(parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_border_color_button.clicked.connect(lambda: playercontrols_properties_panel_tabwidget_subtitles_subtitle_border_color_button_clicked(self))

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_fill_color_title_normal = QLabel('Fill'.upper(), parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_fill_color_title_normal.setObjectName('small_label')

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_fill_color_button = QPushButton(parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_fill_color_button.clicked.connect(lambda: playercontrols_properties_panel_tabwidget_subtitles_subtitle_fill_color_button_clicked(self))

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_text_color_title_normal = QLabel('Text'.upper(), parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_text_color_title_normal.setObjectName('small_label')

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_text_color_button = QPushButton(parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_text_color_button.clicked.connect(lambda: playercontrols_properties_panel_tabwidget_subtitles_subtitle_text_color_button_clicked(self))

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_title_selected = QLabel('Selected subtitle colors'.upper(), parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_title_selected.setObjectName('small_label')
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_title_selected.setStyleSheet(' QLabel {font-weight:bold;} ')

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_border_color_title_selected = QLabel('Border'.upper(), parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_border_color_title_selected.setObjectName('small_label')

    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_border_color_button = QPushButton(parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_border_color_button.clicked.connect(lambda: playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_border_color_button_clicked(self))

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_fill_color_title_selected = QLabel('Fill'.upper(), parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_fill_color_title_selected.setObjectName('small_label')

    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_fill_color_button = QPushButton(parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_fill_color_button.clicked.connect(lambda: playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_fill_color_button_clicked(self))

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_text_color_title_selected = QLabel('Text'.upper(), parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_text_color_title_selected.setObjectName('small_label')

    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_text_color_button = QPushButton(parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_text_color_button.clicked.connect(lambda: playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_text_color_button_clicked(self))

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_title_arrows = QLabel('Arrows colors'.upper(), parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_title_arrows.setObjectName('small_label')
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_title_arrows.setStyleSheet(' QLabel {font-weight:bold;} ')

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_title_normal_arrows = QLabel('Normal'.upper(), parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_title_normal_arrows.setObjectName('small_label')

    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_arrow_normal_button = QPushButton(parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_arrow_normal_button.clicked.connect(lambda: playercontrols_properties_panel_tabwidget_subtitles_subtitle_arrow_normal_button_clicked(self))

    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_title_normal_arrows = QLabel('Selected'.upper(), parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_title_normal_arrows.setObjectName('small_label')

    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_arrow_normal_button = QPushButton(parent=self.playercontrols_properties_panel_tabwidget_subtitles)
    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_arrow_normal_button.clicked.connect(lambda: playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_arrow_normal_button_clicked(self))

    self.playercontrols_properties_panel_tabwidget.addTab(self.playercontrols_properties_panel_tabwidget_subtitles, '')
    self.playercontrols_properties_panel_tabwidget.setTabIcon(0, QIcon(os.path.join(session.PATH_SUBTITLD_GRAPHICS, 'playercontrols_properties_panel_subtitle_icon.svg')))

    self.playercontrols_properties_panel_tabwidget_waveform = QWidget()

    self.playercontrols_properties_panel_tabwidget_waveform_title_normal = QLabel('Waveform colors'.upper(), parent=self.playercontrols_properties_panel_tabwidget_waveform)
    self.playercontrols_properties_panel_tabwidget_waveform_title_normal.setObjectName('small_label')
    self.playercontrols_properties_panel_tabwidget_waveform_title_normal.setStyleSheet(' QLabel {font-weight:bold;} ')

    self.playercontrols_properties_panel_tabwidget_waveform_border_color_title_normal = QLabel('Border'.upper(), parent=self.playercontrols_properties_panel_tabwidget_waveform)
    self.playercontrols_properties_panel_tabwidget_waveform_border_color_title_normal.setObjectName('small_label')

    self.playercontrols_properties_panel_tabwidget_waveform_border_color_button = QPushButton(parent=self.playercontrols_properties_panel_tabwidget_waveform)
    self.playercontrols_properties_panel_tabwidget_waveform_border_color_button.clicked.connect(lambda: playercontrols_properties_panel_tabwidget_waveform_border_color_button_clicked(self))

    self.playercontrols_properties_panel_tabwidget_waveform_fill_color_title_normal = QLabel('Fill'.upper(), parent=self.playercontrols_properties_panel_tabwidget_waveform)
    self.playercontrols_properties_panel_tabwidget_waveform_fill_color_title_normal.setObjectName('small_label')

    self.playercontrols_properties_panel_tabwidget_waveform_fill_color_button = QPushButton(parent=self.playercontrols_properties_panel_tabwidget_waveform)
    self.playercontrols_properties_panel_tabwidget_waveform_fill_color_button.clicked.connect(lambda: playercontrols_properties_panel_tabwidget_waveform_fill_color_button_clicked(self))

    self.playercontrols_properties_panel_tabwidget.addTab(self.playercontrols_properties_panel_tabwidget_waveform, '')
    self.playercontrols_properties_panel_tabwidget.setTabIcon(1, QIcon(os.path.join(session.PATH_SUBTITLD_GRAPHICS, 'playercontrols_properties_panel_waveform_icon.svg')))

    self.playercontrols_properties_panel_tabwidget_background = QWidget()
    self.playercontrols_properties_panel_tabwidget_background.setObjectName('playercontrols_properties_panel_tabwidget_background')

    self.playercontrols_properties_panel_tabwidget_background_title_normal = QLabel('Background colors'.upper(), parent=self.playercontrols_properties_panel_tabwidget_background)
    self.playercontrols_properties_panel_tabwidget_background_title_normal.setObjectName('small_label')
    self.playercontrols_properties_panel_tabwidget_background_title_normal.setStyleSheet(' QLabel {font-weight:bold;} ')

    self.playercontrols_properties_panel_tabwidget_background_time_text_color_title_normal = QLabel('Time'.upper(), parent=self.playercontrols_properties_panel_tabwidget_background)
    self.playercontrols_properties_panel_tabwidget_background_time_text_color_title_normal.setObjectName('small_label')

    self.playercontrols_properties_panel_tabwidget_background_time_text_color_button = QPushButton(parent=self.playercontrols_properties_panel_tabwidget_background)
    self.playercontrols_properties_panel_tabwidget_background_time_text_color_button.clicked.connect(lambda: playercontrols_properties_panel_tabwidget_background_time_text_color_button_clicked(self))

    self.playercontrols_properties_panel_tabwidget_background_cursor_color_title_normal = QLabel('Cursor'.upper(), parent=self.playercontrols_properties_panel_tabwidget_background)
    self.playercontrols_properties_panel_tabwidget_background_cursor_color_title_normal.setObjectName('small_label')

    self.playercontrols_properties_panel_tabwidget_background_cursor_color_button = QPushButton(parent=self.playercontrols_properties_panel_tabwidget_background)
    self.playercontrols_properties_panel_tabwidget_background_cursor_color_button.clicked.connect(lambda: playercontrols_properties_panel_tabwidget_background_cursor_color_button_clicked(self))

    self.playercontrols_properties_panel_tabwidget_background_grid_color_title_normal = QLabel('Grid'.upper(), parent=self.playercontrols_properties_panel_tabwidget_background)
    self.playercontrols_properties_panel_tabwidget_background_grid_color_title_normal.setObjectName('small_label')

    self.playercontrols_properties_panel_tabwidget_background_grid_color_button = QPushButton(parent=self.playercontrols_properties_panel_tabwidget_background)
    self.playercontrols_properties_panel_tabwidget_background_grid_color_button.clicked.connect(lambda: playercontrols_properties_panel_tabwidget_background_grid_color_button_clicked(self))

    self.playercontrols_properties_panel_tabwidget.addTab(self.playercontrols_properties_panel_tabwidget_background, '')
    self.playercontrols_properties_panel_tabwidget.setTabIcon(2, QIcon(os.path.join(session.PATH_SUBTITLD_GRAPHICS, 'playercontrols_properties_panel_background_icon.svg')))

    self.playercontrols_widget_frame = QFrame(parent=self.playercontrols_widget)
    self.playercontrols_widget_frame.setLayout(QHBoxLayout())
    self.playercontrols_widget_frame.layout().setContentsMargins(0, 0, 0, 0)

    self.playercontrols_widget_frame_top_hbox = QHBoxLayout()
    self.playercontrols_widget_frame_top_hbox.setSpacing(0)

    self.playercontrols_widget_frame_top_left = QFrame()
    self.playercontrols_widget_frame_top_left.setAttribute(Qt.WA_LayoutOnEntireRect)
    self.playercontrols_widget_frame_top_left.setObjectName('playercontrols_widget_frame_top_left')
    self.playercontrols_widget_frame_top_left.setFixedHeight(60)
    self.playercontrols_widget_frame_top_left.setLayout(QHBoxLayout())
    self.playercontrols_widget_frame_top_left.layout().setContentsMargins(0, 7, 7, 13)
    self.playercontrols_widget_frame_top_left.layout().setSpacing(6)
    self.playercontrols_widget_frame_top_left.layout().addStretch()

    self.playercontrols_widget_frame_top_left.layout().addWidget(self.gap_hbox, 0)

    self.playercontrols_widget_frame_top_hbox.layout().addWidget(self.playercontrols_widget_frame_top_left, 1)

    self.playercontrols_widget_frame_top_middle = QFrame()
    self.playercontrols_widget_frame_top_middle.setFixedHeight(60)
    self.playercontrols_widget_frame_top_middle.setObjectName('playercontrols_widget_frame_top_middle')
    self.playercontrols_widget_frame_top_middle.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.playercontrols_widget_frame_top_middle.setLayout(QHBoxLayout())
    self.playercontrols_widget_frame_top_middle.layout().setContentsMargins(0, 10, 0, 6)
    self.playercontrols_widget_frame_top_middle.layout().setSpacing(0)

    self.playercontrols_widget_frame_top_hbox.layout().addWidget(self.playercontrols_widget_frame_top_middle)

    self.playercontrols_widget_frame_top_right = QFrame()
    self.playercontrols_widget_frame_top_right.setAttribute(Qt.WA_LayoutOnEntireRect)
    self.playercontrols_widget_frame_top_right.setObjectName('playercontrols_widget_frame_top_right')
    self.playercontrols_widget_frame_top_right.setFixedHeight(60)
    self.playercontrols_widget_frame_top_right.setLayout(QHBoxLayout())
    self.playercontrols_widget_frame_top_right.layout().setContentsMargins(7, 7, 0, 13)
    self.playercontrols_widget_frame_top_right.layout().setSpacing(6)

    self.playercontrols_widget_frame_top_right.layout().addStretch()

    self.playercontrols_widget_frame_top_hbox.layout().addWidget(self.playercontrols_widget_frame_top_right, 1)

    self.playercontrols_widget_frame.layout().addLayout(self.playercontrols_widget_frame_top_hbox)

    self.playercontrols_widget_frame_bottom_hbox = QHBoxLayout()
    self.playercontrols_widget_frame_bottom_hbox.setSpacing(0)

    self.playercontrols_widget_frame_bottom_left = QFrame()
    self.playercontrols_widget_frame_bottom_left.setAttribute(Qt.WA_LayoutOnEntireRect)
    self.playercontrols_widget_frame_bottom_left.setObjectName('playercontrols_widget_frame_bottom_left')
    self.playercontrols_widget_frame_bottom_left.setFixedHeight(26)
    self.playercontrols_widget_frame_bottom_left.setLayout(QHBoxLayout())
    self.playercontrols_widget_frame_bottom_left.layout().setContentsMargins(0, 0, 4, 2)
    self.playercontrols_widget_frame_bottom_left.layout().setSpacing(0)
    self.playercontrols_widget_frame_bottom_left.layout().addStretch()







    self.playercontrols_widget_frame_bottom_left.layout().addSpacing(4)

    self.playercontrols_widget_frame_bottom_left.layout().addSpacing(4)

    self.playercontrols_widget_frame_bottom_left.layout().addSpacing(2)


    self.playercontrols_widget_frame_bottom_hbox.layout().addWidget(self.playercontrols_widget_frame_bottom_left, 1)

    self.playercontrols_widget_frame_bottom_middle = QFrame()
    self.playercontrols_widget_frame_bottom_middle.setFixedHeight(26)
    self.playercontrols_widget_frame_bottom_middle.setObjectName('playercontrols_widget_frame_bottom_middle')
    self.playercontrols_widget_frame_bottom_middle.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.playercontrols_widget_frame_bottom_middle.setLayout(QHBoxLayout())
    self.playercontrols_widget_frame_bottom_middle.layout().setContentsMargins(0, 0, 0, 0)
    self.playercontrols_widget_frame_bottom_middle.layout().setSpacing(0)

    self.playercontrols_widget_frame_bottom_hbox.layout().addWidget(self.playercontrols_widget_frame_bottom_middle, 0)

    self.playercontrols_widget_frame_bottom_right_absolute = QFrame()
    self.playercontrols_widget_frame_bottom_right_absolute.setAttribute(Qt.WA_LayoutOnEntireRect)
    # self.playercontrols_widget_frame_bottom_right_absolute.setObjectName('playercontrols_widget_frame_bottom_right')
    self.playercontrols_widget_frame_bottom_right_absolute.setFixedHeight(26)
    self.playercontrols_widget_frame_bottom_right_absolute.setLayout(QHBoxLayout())
    self.playercontrols_widget_frame_bottom_right_absolute.layout().setContentsMargins(0, 0, 0, 0)
    self.playercontrols_widget_frame_bottom_right_absolute.layout().setSpacing(0)

    self.playercontrols_widget_frame_bottom_right = QFrame()
    # self.playercontrols_widget_frame_bottom_right.setAttribute(Qt.WA_LayoutOnEntireRect)
    self.playercontrols_widget_frame_bottom_right.setObjectName('playercontrols_widget_frame_bottom_right')
    # self.playercontrols_widget_frame_bottom_right.setFixedHeight(26)
    self.playercontrols_widget_frame_bottom_right.setLayout(QHBoxLayout())
    self.playercontrols_widget_frame_bottom_right.layout().setContentsMargins(0, 0, 0, 2)
    self.playercontrols_widget_frame_bottom_right.layout().setSpacing(0)

    self.playercontrols_widget_frame_bottom_right.layout().addSpacing(-8)

    self.playercontrols_widget_frame_bottom_right.layout().addStretch()

    self.playercontrols_widget_frame_bottom_right_absolute.layout().addWidget(self.playercontrols_widget_frame_bottom_right)

    self.playercontrols_widget_frame_bottom_right_corner = QFrame()
    self.playercontrols_widget_frame_bottom_right_corner.setObjectName('playercontrols_widget_frame_bottom_right_corner')
    self.playercontrols_widget_frame_bottom_right_corner.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.playercontrols_widget_frame_bottom_right_corner.setFixedWidth(35)
    self.playercontrols_widget_frame_bottom_right_corner.setLayout(QHBoxLayout())
    self.playercontrols_widget_frame_bottom_right_corner.layout().setContentsMargins(0, 0, 0, 0)
    self.playercontrols_widget_frame_bottom_right_corner.layout().setSpacing(0)

    self.playercontrols_properties_panel_toggle = QPushButton()
    self.playercontrols_properties_panel_toggle.setObjectName('playercontrols_properties_panel_toggle')
    self.playercontrols_properties_panel_toggle.setCheckable(True)
    self.playercontrols_widget_frame_bottom_right_corner.layout().addWidget(self.playercontrols_properties_panel_toggle)
    self.playercontrols_properties_panel_toggle.clicked.connect(lambda: playercontrols_properties_panel_toggle_pressed(self))
    self.playercontrols_properties_panel_toggle_animation = QPropertyAnimation(self.playercontrols_properties_panel_toggle, b'geometry')
    self.playercontrols_properties_panel_toggle_animation.setEasingCurve(QEasingCurve.OutCirc)

    self.playercontrols_widget_frame_bottom_right_absolute.layout().addWidget(self.playercontrols_widget_frame_bottom_right_corner, 0)

    self.playercontrols_widget_frame_bottom_hbox.layout().addWidget(self.playercontrols_widget_frame_bottom_right_absolute, 1)

    self.playercontrols_widget_frame.layout().addLayout(self.playercontrols_widget_frame_bottom_hbox)
    '''

def playercontrols_stop_button_clicked(self):
    playercontrols_playpause_button_clicked(self)
    self.preview_panel_player.stop()
    update_playercontrols_playpause_button(self)
    timeline.update(self)

@shortcut('playpause', 'Play/Pause', ['Space'])
def playercontrols_playpause_button_pressed(self):
    if self.playercontrols_widget.isVisible():
        playercontrols_playpause_button_clicked(self)
        update_playercontrols_playpause_button(self)


def playercontrols_playpause_button_clicked(self):
    """Function to call when play/pause button is clicked"""
    controller = getattr(self, 'record_controller', None)
    if self.preview_panel_player.is_paused():
        self.preview_panel_player.play()
        if session.CONFIG['repeat_activated']:
            session.REPEAT_DURATION_BUFFER = []
        # Armed record button → recording follows playback.
        if controller is not None and controller.armed:
            controller.on_play()
    else:
        self.preview_panel_player.pause()
        if controller is not None and controller.is_recording:
            controller.on_pause()


def playercontrols_record_button_clicked(self):
    """Arm / disarm recording. Armed → the next Play records; disarming while a
    take is running stops it. Recording itself is triggered from play/pause."""
    controller = getattr(self, 'record_controller', None)
    if controller is None:
        return
    armed = self.playercontrols_record_button.isChecked()
    controller.armed = armed
    if armed:
        if not self.preview_panel_player.is_paused():
            controller.on_play()   # already playing → start now
    elif controller.is_recording:
        controller.on_pause()      # disarmed mid-take → finalize
    _update_record_mode_buttons(self)


def playercontrols_record_transcript_button_clicked(self):
    from subtitld.interface.record_controls import MODE_TRANSCRIPT
    if getattr(self, 'record_controller', None) is not None:
        self.record_controller.mode = MODE_TRANSCRIPT
    _update_record_mode_buttons(self)


def playercontrols_record_audio_button_clicked(self):
    from subtitld.interface.record_controls import MODE_WAVE
    if getattr(self, 'record_controller', None) is not None:
        self.record_controller.mode = MODE_WAVE
    _update_record_mode_buttons(self)


def _update_record_mode_buttons(self):
    """Show which mode switch is active (exactly one checked)."""
    controller = getattr(self, 'record_controller', None)
    if controller is None:
        return
    from subtitld.interface.record_controls import MODE_TRANSCRIPT, MODE_WAVE
    mode = controller.mode
    self.playercontrols_record_transcript_button.setChecked(mode == MODE_TRANSCRIPT)
    self.playercontrols_record_audio_button.setChecked(mode == MODE_WAVE)
    _update_record_pulse(self)


def _update_record_pulse(self):
    """Run the opacity pulse while the record button is armed; stop + reset
    to full opacity otherwise. Guarded so repeated calls don't restart (and
    visibly jump) an already-running pulse."""
    anim = getattr(self, '_record_pulse_anim', None)
    effect = getattr(self, '_record_pulse_effect', None)
    controller = getattr(self, 'record_controller', None)
    if anim is None or effect is None or controller is None:
        return
    if controller.armed:
        if not self._record_pulse_running:
            anim.start()
            self._record_pulse_running = True
    else:
        if self._record_pulse_running:
            anim.stop()
            self._record_pulse_running = False
        effect.setOpacity(1.0)


def update_playercontrols_playpause_button(self):
    self.playercontrols_playpause_button.setChecked(not self.preview_panel_player.is_paused())


def show(self):
    update(self)
    # Hide the splitter handle while the bottom_panel slides up; the
    # bottom_panel.animation.finished hook (set up in load) will animate it
    # in once the panel is in place.
    self.playercontrols_handle_button.hide()
    self._handle_pending_show = True


def update(self):
    self.preview_panel_player.update_speed()
    self.playercontrols_timecode_label.update_time()
    update_playback_speed_buttons(self)
    update_playback_repeat_buttons(self)
    # self.add_subtitle_duration.setEnabled(not session.CONFIG.get('new_subtitle_to_next_start', False))
    self.add_subtitle_duration.setValue(session.CONFIG.get('default_new_subtitle_duration', 3.0))
    self.add_subtitle_starting_from_last.setChecked(session.CONFIG.get('new_subtitle_start_from_last', False))
    self.add_subtitle_and_play.setChecked(session.CONFIG.get('new_subtitle_and_play', False))
    self.add_subtitle_to_next_start.setChecked(session.CONFIG.get('new_subtitle_to_next_start', False))
    self.timeline_show_speaker_tracks_button.setChecked(session.CONFIG['timeline'].get('show_speaker_tracks', False))
    self.timeline_show_speaker_color_button.setChecked(session.CONFIG['timeline'].get('show_speaker_color', False))
    timelinescrolling_type_update(self)
    update_snap_buttons(self)
    update_grid_buttons(self)
    update_onset_button(self)
    update_step_buttons(self)
    music_voice_separation_box_update(self)


def _focus_is_text_widget(self):
    """Return True when an editable text widget owns focus — Ctrl+Z then
    belongs to that widget's own undo, not the document undo."""
    from PySide6.QtWidgets import QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox
    fw = self.focusWidget()
    if fw is None:
        return False
    return isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox))


def _refresh_after_history(self):
    """Fan out UI refresh after an undo/redo flips the document state."""
    from subtitld.interface import left_panel_subtitleslist
    left_panel_subtitleslist.update(self)
    self.timeline_widget.update()
    # The history restore deep-copies every subtitle dict, so each
    # `id(sub)` changes. The audio engine keys its `subtitle_clips`
    # map on those ids — without resyncing here, the engine still
    # holds clips bound to the pre-undo (orphaned) dicts and the
    # newly-restored dicts have no clip at all. Result: silent dub
    # playback after every undo/redo. Re-sync from the live segments
    # so each restored subtitle gets a fresh clip pointing at its
    # current dub data.
    preview = getattr(self, 'preview_panel_player', None)
    if preview is not None:
        device = getattr(preview, '_audio_device', None)
        if device is not None and hasattr(device, 'sync_subtitle_dubs'):
            device.sync_subtitle_dubs(session.SUBTITLE.get('segments', []) or [])


@shortcut('history_undo', 'Undo last action', ['Ctrl+Z'])
def history_undo_command(self):
    if _focus_is_text_widget(self):
        return
    if history.history_undo():
        _refresh_after_history(self)


@shortcut('history_redo', 'Redo last undone action', ['Ctrl+Shift+Z', 'Ctrl+Y'])
def history_redo_command(self):
    if _focus_is_text_widget(self):
        return
    if history.history_redo():
        _refresh_after_history(self)


def _dub_segment_at_cursor(self):
    """Locate the dub segment that contains the playback cursor. Returns
    (subtitle, dub, segment_index, offset_within_segment, segment) or None."""
    from subtitld.modules import dub_clip
    cursor = float(session.SUBTITLE.get('position', 0.0) or 0.0)
    for subtitle in session.SUBTITLE.get('segments', []) or []:
        if not subtitle.get('dubbing'):
            continue
        dub = subtitle['dubbing'][0]
        for idx, (t0, t1, seg) in enumerate(dub_clip.iter_segment_ranges(dub)):
            if t0 <= cursor < t1:
                return (subtitle, dub, idx, cursor - t0, seg)
    return None


def _refresh_dub_after_edit(self):
    """Push the updated dub state to the audio engine and repaint the
    timeline. Mirrors Timeline._refresh_dub_after_edit so the split
    shortcut behaves like the right-click menu items."""
    self.timeline_widget.update()
    preview = getattr(self, 'preview_panel_player', None)
    if preview is not None:
        device = getattr(preview, '_audio_device', None)
        if device is not None and hasattr(device, 'sync_subtitle_dubs'):
            device.sync_subtitle_dubs(session.SUBTITLE.get('segments', []) or [])


@shortcut('split_dub_at_cursor', 'Split dub clip at cursor', ['Shift+S'])
def split_dub_at_cursor(self):
    if _focus_is_text_widget(self):
        return
    from subtitld.modules import dub_clip
    hit = _dub_segment_at_cursor(self)
    if hit is None:
        return
    _subtitle, dub, idx, offset, seg = hit
    if seg.get('type', 'audio') != 'audio':
        return
    history.history_append()
    if dub_clip.split_audio_segment(dub, idx, offset):
        session.set_unsaved()
        _refresh_dub_after_edit(self)


@shortcut('zoom_in', 'Zoom in', ['+'])
def zoomin_button_clicked(self):
    """Function to call when zoonin button is clicked"""
    # Button/keyboard zoom has no meaningful pointer position over the
    # timeline — pivot on the playhead (clear any mouse pivot left by a
    # prior wheel-zoom).
    self._zoom_mouse_pivot_x = None
    session.CONFIG['timeline_zoom'] += 10.0
    zoom_buttons_update(self)


@shortcut('zoom_out', 'Zoom out', ['-'])
def zoomout_button_clicked(self):
    """Function to call when zoonout button is clicked"""
    self._zoom_mouse_pivot_x = None
    session.CONFIG['timeline_zoom'] -= 10.0
    zoom_buttons_update(self)


def zoom_buttons_update(self):
    """Function to update zoom buttons. Throttles the heavy `setGeometry` +
    repaint so continuous wheel-scroll feels smooth: applies immediately if
    the previous apply finished more than ~16ms ago (≈60fps), otherwise
    schedules a single follow-up apply for the remaining gap. Earlier
    versions debounced via timer.start() on every call, but that meant the
    geometry never applied while the user kept scrolling — felt stuck."""
    self.zoomout_button.setEnabled(True if session.CONFIG['timeline_zoom'] - 10.0 > 0.0 else False)
    self.zoomin_button.setEnabled(True if session.CONFIG['timeline_zoom'] + 10.0 < 500.0 else False)

    if not hasattr(self, '_zoom_apply_timer') or self._zoom_apply_timer is None:
        self._zoom_apply_timer = QTimer(self)
        self._zoom_apply_timer.setSingleShot(True)
        self._zoom_apply_timer.timeout.connect(lambda: _apply_zoom_geometry(self))
        self._zoom_last_apply_ms = 0

    interval_ms = 16
    now_ms = int(time.monotonic() * 1000)
    elapsed = now_ms - getattr(self, '_zoom_last_apply_ms', 0)
    if elapsed >= interval_ms and not self._zoom_apply_timer.isActive():
        self._zoom_last_apply_ms = now_ms
        _apply_zoom_geometry(self)
    elif not self._zoom_apply_timer.isActive():
        self._zoom_apply_timer.start(max(0, interval_ms - elapsed))


def _apply_zoom_geometry(self):
    self._zoom_last_apply_ms = int(time.monotonic() * 1000)
    scroll = self.timeline_scroll
    width_new = int(round(session.VIDEO.get('duration', 0.01) * session.CONFIG['timeline_zoom']))
    mouse_pivot_x = getattr(self, '_zoom_mouse_pivot_x', None)

    if mouse_pivot_x is not None:
        # Mouse-pivoted zoom: keep the timeline point under the cursor fixed
        # on screen. Work in whole-timeline fractions (zoom-invariant), so
        # this stays correct even when several wheel notches collapse into a
        # single throttled apply.
        width_old = max(1, self.timeline_widget.width())
        scroll_old = scroll.horizontalScrollBar().value()
        frac = (scroll_old + mouse_pivot_x) / width_old
        self.timeline_widget.setGeometry(0, 0, width_new, scroll.height() - 20)
        scroll.horizontalScrollBar().setValue(int(round(frac * width_new - mouse_pivot_x)))
        self.timeline_widget.update()
        return

    # Buttons/keyboard: pivot on the playhead (its viewport fraction is held).
    proportion = ((session.SUBTITLE.get('position', 0) * self.timeline_widget.width_proportion) - scroll.horizontalScrollBar().value()) / scroll.width()
    self.timeline_widget.setGeometry(0, 0, width_new, scroll.height() - 20)
    timeline.update_scrollbar(self, position=proportion)


def snap_button_clicked(self):
    """Function to call when snap button is clicked"""
    session.CONFIG['timeline']['snap'] = self.snap_button.isChecked()
    update_snap_buttons(self)


def snap_move_button_clicked(self):
    """Function to call when snap move button is clicked"""
    session.CONFIG['timeline']['snap_moving'] = self.snap_move_button.isChecked()


def snap_move_nereast_button_clicked(self):
    """Function to call when snap move next button is clicked"""
    session.CONFIG['timeline']['snap_move_nereast'] = self.snap_move_nereast_button.isChecked()


def snap_limits_button_clicked(self):
    """Function to call when snap limits button is clicked"""
    session.CONFIG['timeline']['snap_limits'] = self.snap_limits_button.isChecked()


def snap_grid_button_clicked(self):
    """Function to call when snap to grid button is clicked"""
    session.CONFIG['timeline']['snap_grid'] = self.snap_grid_button.isChecked()


def snap_value_changed(self):
    """Function to call when snap value is changed"""
    session.CONFIG['timeline']['snap_value'] = self.snap_value.value() if self.snap_value.value() else .1


def step_value_changed(self):
    """Function to set variables to settings"""
    session.CONFIG['timeline']['step_unit'] = self.step_unit.currentText()
    if session.CONFIG['timeline'].get('step_unit', 'Frames') == 'Seconds':
        session.CONFIG['timeline']['step_value'] = self.step_value_f.value()
    else:
        session.CONFIG['timeline']['step_value'] = self.step_value_i.value()
    update_step_information(self)


def update_step_buttons(self):
    """Function to update step widgets"""
    self.step_value_f.setEnabled(self.step_button.isChecked())
    self.step_value_i.setEnabled(self.step_button.isChecked())
    self.step_unit.setEnabled(self.step_button.isChecked())
    update_step_information(self)


def update_step_information(self):
    """Updates the widgets information"""
    self.step_unit.setCurrentIndex(STEPS_LIST.index(session.CONFIG['timeline'].get('step_unit', 'Frames')))
    self.step_value_f.setValue(float(session.CONFIG['timeline'].get('step_value', 1.0)))
    self.step_value_i.setValue(int(session.CONFIG['timeline'].get('step_value', 1)))
    self.step_value_f.setVisible(session.CONFIG['timeline'].get('step_unit', 'Frames') == 'Seconds')
    self.step_value_i.setVisible(session.CONFIG['timeline'].get('step_unit', 'Frames') == 'Frames')


def timelinescrolling_type_changed(self, scrollingtype='page'):
    session.CONFIG['timeline']['scrolling'] = scrollingtype
    timelinescrolling_type_update(self)
    

def timelinescrolling_type_update(self):
    self.timelinescrolling_none_button.setChecked(session.CONFIG['timeline'].get('scrolling', 'page') == 'none')
    self.timelinescrolling_page_button.setChecked(session.CONFIG['timeline'].get('scrolling', 'page') == 'page')
    self.timelinescrolling_follow_button.setChecked(session.CONFIG['timeline'].get('scrolling', 'page') == 'follow')


def add_subtitle_duration_changed(self):
    """Function to call when subtitle default duration is changed"""
    session.CONFIG['default_new_subtitle_duration'] = self.add_subtitle_duration.value()


def add_subtitle_starting_from_last_clicked(self):
    session.CONFIG['new_subtitle_start_from_last'] = self.add_subtitle_starting_from_last.isChecked()
    self.timeline_widget.setFocus(Qt.TabFocusReason)


def add_subtitle_and_play_clicked(self):
    session.CONFIG['new_subtitle_and_play'] = self.add_subtitle_and_play.isChecked()
    self.timeline_widget.setFocus(Qt.TabFocusReason)


def add_subtitle_to_next_start_clicked(self):
    session.CONFIG['new_subtitle_to_next_start'] = self.add_subtitle_to_next_start.isChecked()
    self.add_subtitle_duration.setEnabled(not session.CONFIG.get('new_subtitle_to_next_start', False))
    self.timeline_widget.setFocus(Qt.TabFocusReason)


def gap_add_subtitle_button_clicked(self):
    """Function to call when add gap button is clicked"""
    subtitles.set_gap(position=session.SUBTITLE.get('position', 0), gap=self.gap_subtitle_duration.value())
    left_panel.update(self)
    session.SUBTITLE['selected'] = None
    timeline.update(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)
    session.set_unsaved()


def gap_remove_subtitle_button_clicked(self):
    """Function to call when remove gap button is clicked"""
    subtitles.set_gap(position=session.SUBTITLE.get('position', 0), gap=-(self.gap_subtitle_duration.value()))
    left_panel.update(self)
    session.SUBTITLE['selected'] = None
    timeline.update(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)
    session.set_unsaved()


def update_snap_buttons(self):
    """Function to update snap buttons"""
    self.snap_button.setChecked(bool(session.CONFIG['timeline'].get('snap', True)))
    self.snap_limits_button.setEnabled(self.snap_button.isChecked())
    self.snap_limits_button.setChecked(bool(session.CONFIG['timeline'].get('snap_limits', True)))
    self.snap_move_button.setEnabled(self.snap_button.isChecked())
    self.snap_move_button.setChecked(bool(session.CONFIG['timeline'].get('snap_moving', True)))
    self.snap_grid_button.setEnabled(self.snap_button.isChecked())
    self.snap_grid_button.setChecked(bool(session.CONFIG['timeline'].get('snap_grid', False)))
    self.snap_move_nereast_button.setEnabled(self.snap_button.isChecked())
    self.snap_move_nereast_button.setChecked(bool(session.CONFIG['timeline'].get('snap_move_nereast', False)))
    self.snap_value.setEnabled(self.snap_button.isChecked())
    self.snap_value.setValue(float(session.CONFIG['timeline'].get('snap_value', .1)))
    self.timeline_widget.update()


def update_playback_speed_buttons(self, animate=False):
    if not session.CONFIG['playback_speed'] == 1.0 and not self.change_playback_speed.isChecked():
        self.change_playback_speed.setChecked(True)
    self.change_playback_speed_label.setText('x' + str(session.CONFIG['playback_speed']))
    self.change_playback_speed_slider.setValue(int(session.CONFIG['playback_speed'] * 100))
    _set_collapsed(self.change_playback_speed,
                   not self.change_playback_speed.isChecked(),
                   animate=animate)


def update_playback_repeat_buttons(self, animate=False):
    if session.CONFIG['repeat_activated']:
        self.repeat_playback.setChecked(True)
    self.repeat_playback_duration.setValue(float(session.CONFIG.get('playback_repeat_duration', 10.0)))
    self.repeat_playback_times.setValue(int(session.CONFIG.get('playback_repeat_times', 3)))
    _set_collapsed(self.repeat_playback,
                   not self.repeat_playback.isChecked(),
                   animate=animate)
    timeline.update(self)


def grid_button_clicked(self):
    """Function to call when grid button is clicked"""
    session.CONFIG['timeline']['show_grid'] = self.grid_button.isChecked()
    if not session.CONFIG['timeline'].get('grid_type', False):
        session.CONFIG['timeline']['grid_type'] = 'seconds'
    update_grid_buttons(self)


def grid_type_changed(self, gridtype):
    """Function to call when grid type button is clicked"""
    session.CONFIG['timeline']['grid_type'] = gridtype
    update_grid_buttons(self)


def update_grid_buttons(self):
    """Function to update grid buttons"""
    self.grid_button.setChecked(session.CONFIG['timeline'].get('show_grid', False))
    self.grid_frames_button.setEnabled(session.CONFIG['timeline'].get('show_grid', False))
    self.grid_frames_button.setChecked(True if session.CONFIG['timeline'].get('grid_type', False) == 'frames' else False)
    self.grid_seconds_button.setEnabled(session.CONFIG['timeline'].get('show_grid', False))
    self.grid_seconds_button.setChecked(True if session.CONFIG['timeline'].get('grid_type', False) == 'seconds' else False)
    self.grid_scenes_button.setEnabled(session.CONFIG['timeline'].get('show_grid', False))
    self.grid_scenes_button.setChecked(True if session.CONFIG['timeline'].get('grid_type', False) == 'scenes' else False)


def onset_button_clicked(self):
    """Function to call when the onset-markers (plosive view) button is clicked"""
    enabled = self.onset_button.isChecked()
    session.CONFIG['timeline']['show_onset_markers'] = enabled
    if not enabled:
        # Drop any selected plosive mark so a stale pivot doesn't linger
        # when the overlay is turned back on.
        self.timeline_widget.selected_onset = None
    self.timeline_widget.update()


def update_onset_button(self):
    """Sync the onset-markers toggle to CONFIG. Called from the same
    startup pass that syncs grid/snap buttons."""
    self.onset_button.setChecked(bool(session.CONFIG['timeline'].get('show_onset_markers', False)))
    self.timeline_widget.update()


def playercontrols_play_from_last_start_button_clicked(self):
    """Function to call when stop button is clicked"""
    subt = [item['start'] for item in session.SUBTITLE['segments']]
    last_subtitle = session.SUBTITLE['segments'][bisect(subt, session.SUBTITLE.get('position', 0)) - 1]
    self.preview_panel_player.seek(last_subtitle['start'])
    self.preview_panel_player.play()
    self.timeline_widget.setFocus(Qt.TabFocusReason)
    update_playercontrols_playpause_button(self)


def playercontrols_play_from_next_start_button_clicked(self):
    """Function to call when play from next subtitle button is clicked"""
    subt = [item['start'] for item in session.SUBTITLE['segments']]
    i = bisect(subt, session.SUBTITLE.get('position', 0))
    if i < len(session.SUBTITLE['segments']):
        next_subtitle = session.SUBTITLE['segments'][i]
        self.preview_panel_player.seek(next_subtitle['start'])
        self.preview_panel_player.play()
        self.timeline_widget.setFocus(Qt.TabFocusReason)
    update_playercontrols_playpause_button(self)


@shortcut('add_new_subtitle_to_current_position', 'Add new subtitle to current position', ['Enter'])
def add_subtitle_command(self):
    if self.focusWidget() is not self.timeline_widget:
        return
    add_subtitle_button_clicked(self)


def add_subtitle_button_clicked(self):
    duration = session.CONFIG['default_new_subtitle_duration']
    if session.CONFIG.get('new_subtitle_to_next_start', False):
        next_subtitle = subtitles.next_subtitle_current_position(position=session.SUBTITLE.get('position', 0))
        if next_subtitle:
            duration = max(duration, next_subtitle['start'] - session.SUBTITLE.get('position', 0))
    session.SUBTITLE['selected'] = subtitles.add_subtitle(position=session.SUBTITLE.get('position', 0), duration=duration, from_last_subtitle=self.add_subtitle_starting_from_last.isChecked())
    self.left_panel_subtitleslist_textedit.setFocus(Qt.TabFocusReason)
    if self.add_subtitle_and_play.isChecked():
        self.preview_panel_player.play()
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        update_playercontrols_playpause_button(self)
    timeline.update(self)
    self.timeline_widget.update()
    session.set_unsaved()


@shortcut('remove_current_subtitle', 'Remove current subtitle', ['*'])
def remove_selected_subtitle_button_clicked(self):
    """Function to call when remove selected subtitle button is clicked"""
    subtitles.remove_subtitle(selected_subtitle=session.SUBTITLE['selected'])
    session.SUBTITLE['selected'] = None
    left_panel.update(self)
    timeline.update(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)
    self.preview_panel_player._audio_device.sync_subtitle_dubs(session.SUBTITLE['segments'])
    session.set_unsaved()


@shortcut('toggle_lock_current_subtitle', 'Toggle lock on current subtitle', ['L'])
def toggle_lock_selected_subtitle(self):
    """Lock / unlock the selected subtitle. Locked subtitles and their dub
    clips cannot be dragged or resized on the timeline."""
    selected = session.SUBTITLE.get('selected')
    if not selected:
        return
    selected['locked'] = not selected.get('locked', False)
    self.timeline_widget.update()
    session.set_unsaved()


@shortcut('slice_current_subtitle', 'Slice current subtitle', ['/'])
def slice_selected_subtitle_command(self):
    if self.focusWidget() is not self.timeline_widget:
        return
    self.slice_selected_subtitle_button.toggle()
    slice_selected_subtitle_button_clicked(self)
    slice_selected_subtitle_button_update(self)


def slice_selected_subtitle_button_clicked(self):
    """Function to call when slice selected subtitle button is clicked"""
    if self.slice_selected_subtitle_button.isChecked() and session.SUBTITLE.get('selected', None) is not None and self.left_panel_subtitleslist_textedit.textCursor().position():
        pos = self.left_panel_subtitleslist_textedit.textCursor().position()
        last_text = self.left_panel_subtitleslist_textedit.toPlainText()[:pos]
        next_text = self.left_panel_subtitleslist_textedit.toPlainText()[pos:]
        session.SUBTITLE['selected'] = subtitles.slice_subtitle(selected_subtitle=session.SUBTITLE['selected'], position=session.SUBTITLE.get('position', 0), next_text=next_text, last_text=last_text)
        timeline.update(self)
        left_panel.update(self)
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        session.set_unsaved()
    elif not self.slice_selected_subtitle_button.isChecked():
        self.timeline_widget.is_smart_splicing = False
    else:
        self.timeline_widget.is_smart_splicing = {'mode': 'words'}
    self.timeline_widget.update()


def slice_selected_subtitle_button_update(self):
    self.slice_selected_subtitle_button.setChecked(bool(self.timeline_widget.is_smart_splicing))


@shortcut('select_subtitle_in_current_position', 'Select subtitle in current position', ['5'])
def select_subtitle_in_current_position(self):
    """Function to call when actual subtitle under cursor need to be selected"""
    subtitle = subtitles.subtitle_under_current_position(position=session.SUBTITLE.get('position', 0))
    if subtitle:
        session.SUBTITLE['selected'] = subtitle
        timeline.update(self)


@shortcut('select_next_subtitle_over_current_position', 'Select next subtitle over current position', ['8'])
def select_next_subtitle_over_current_position(self):
    """Function to call when next subtitle under cursor need to be selected"""
    subtitle = subtitles.next_subtitle_current_position(position=session.SUBTITLE.get('position', 0))
    if subtitle:
        session.SUBTITLE['selected'] = subtitle
        timeline.update(self)


@shortcut('select_last_subtitle_over_current_position', 'Select last subtitle over current position', ['2'])
def select_last_subtitle_over_current_position(self):
    """Function to call when last subtitle under cursor need to be selected"""
    subtitle = subtitles.last_subtitle_current_position(position=session.SUBTITLE.get('position', 0))
    if subtitle:
        session.SUBTITLE['selected'] = subtitle
        timeline.update(self)


def merge_back_selected_subtitle_button_clicked(self):
    """Function to merge selected subtitle with the last subtitle"""
    if session.SUBTITLE.get('selected', None) is not None:
        session.SUBTITLE['selected'] = subtitles.merge_back_subtitle(selected_subtitle=session.SUBTITLE['selected'])
        timeline.update(self)
        left_panel.update(self)
        self.preview_panel_player._audio_device.sync_subtitle_dubs(session.SUBTITLE['segments'])
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        session.set_unsaved()


def merge_next_selected_subtitle_button_clicked(self):
    """Function to merge selected subtitle with the next subtitle"""
    if session.SUBTITLE.get('selected', None) is not None:
        session.SUBTITLE['selected'] = subtitles.merge_next_subtitle(selected_subtitle=session.SUBTITLE['selected'])
        timeline.update(self)
        left_panel.update(self)
        self.preview_panel_player._audio_device.sync_subtitle_dubs(session.SUBTITLE['segments'])
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        session.set_unsaved()


@shortcut('move_step_backward_subtitle', 'Move subtitle a step backward', ['4'])
def move_backward_subtitle_clicked(self):
    """Function to move subtitle backward"""
    if session.SUBTITLE.get('selected', None) is not None:
        amount = (1.0 / session.VIDEO['framerate'])
        if self.step_button.isChecked():
            if session.CONFIG['timeline'].get('step_unit', 'Frames') == 'Frames':
                amount = (int(session.CONFIG['timeline'].get('step_value', 1)) / session.VIDEO['framerate'])
            else:
                amount = float(session.CONFIG['timeline'].get('step_value', 1.0))
        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], amount=-amount)
        timeline.update(self)
        left_panel.update(self)
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        session.set_unsaved()


@shortcut('move_step_forward_subtitle', 'Move subtitle a step forward', ['6'])
def move_forward_subtitle_clicked(self):
    """Function to move subtitle forward"""
    if session.SUBTITLE.get('selected', None) is not None:
        amount = (1.0 / session.VIDEO['framerate'])
        if self.step_button.isChecked():
            if session.CONFIG['timeline'].get('step_unit', 'Frames') == 'Frames':
                amount = (int(session.CONFIG['timeline'].get('step_value', 1)) / session.VIDEO['framerate'])
            else:
                amount = float(session.CONFIG['timeline'].get('step_value', 1.0))
        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], amount=amount)
        timeline.update(self)
        left_panel.update(self)
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        session.set_unsaved()


@shortcut('subtract_step_subtitle_start', 'Subtract a step to subtitle start', ['1'])
def move_start_back_subtitle_clicked(self):
    """Function to move starting position of selected subtitle backward"""
    if session.SUBTITLE.get('selected', None) is not None:
        amount = (1.0 / session.VIDEO['framerate'])
        if self.step_button.isChecked():
            if session.CONFIG['timeline'].get('step_unit', 'Frames') == 'Frames':
                amount = (int(session.CONFIG['timeline'].get('step_value', 1)) / session.VIDEO['framerate'])
            else:
                amount = float(session.CONFIG['timeline'].get('step_value', 1.0))
        subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], amount=-amount, move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False)))
        timeline.update(self)
        left_panel.update(self)
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        session.set_unsaved()


@shortcut('add_step_subtitle_start', 'Add a step to subtitle start', ['7'])
def move_start_forward_subtitle_clicked(self):
    """Function to move starting position of selected subtitle forward"""
    if session.SUBTITLE.get('selected', None) is not None:
        amount = (1.0 / session.VIDEO['framerate'])
        if self.step_button.isChecked():
            if session.CONFIG['timeline'].get('step_unit', 'Frames') == 'Frames':
                amount = (int(session.CONFIG['timeline'].get('step_value', 1)) / session.VIDEO['framerate'])
            else:
                amount = float(session.CONFIG['timeline'].get('step_value', 1.0))
        subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], amount=amount, move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False)))
        timeline.update(self)
        left_panel.update(self)
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        session.set_unsaved()


@shortcut('subtract_step_subtitle_end', 'Subtract a step to subtitle end', ['3'])
def move_end_back_subtitle_clicked(self):
    """Function to move ending position of selected subtitle backwards"""
    if session.SUBTITLE.get('selected', None) is not None:
        amount = (1.0 / session.VIDEO['framerate'])
        if self.step_button.isChecked():
            if session.CONFIG['timeline'].get('step_unit', 'Frames') == 'Frames':
                amount = (int(session.CONFIG['timeline'].get('step_value', 1)) / session.VIDEO['framerate'])
            else:
                amount = float(session.CONFIG['timeline'].get('step_value', 1.0))
        subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], amount=-amount)
        timeline.update(self)
        left_panel.update(self)
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        session.set_unsaved()


@shortcut('add_step_subtitle_end', 'Add a step to subtitle end', ['9'])
def move_end_forward_subtitle_clicked(self):
    """Function to move ending position of selected subtitle forward"""
    if session.SUBTITLE.get('selected', None) is not None:
        amount = (1.0 / session.VIDEO['framerate'])
        if self.step_button.isChecked():
            if session.CONFIG['timeline'].get('step_unit', 'Frames') == 'Frames':
                amount = (int(session.CONFIG['timeline'].get('step_value', 1)) / session.VIDEO['framerate'])
            else:
                amount = float(session.CONFIG['timeline'].get('step_value', 1.0))
        subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], amount=amount)
        timeline.update(self)
        left_panel.update(self)
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        session.set_unsaved()


def timeline_cursor_back_frame_clicked(self):
    """Function to move cursor one frame backward"""
    self.preview_panel_player.frameBackStep()
    self.timeline_widget.setFocus(Qt.TabFocusReason)
    session.set_unsaved()


def timeline_cursor_next_frame_clicked(self):
    """Function to move cursor one frame forward"""
    self.preview_panel_player.frameStep()
    self.timeline_widget.setFocus(Qt.TabFocusReason)
    session.set_unsaved()


def next_start_to_current_position_button_clicked(self):
    """Function to move cursor one frame backward"""
    subtitles.next_start_to_current_position(position=session.SUBTITLE.get('position', 0))
    timeline.update(self)
    left_panel.update(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)
    session.set_unsaved()


def last_end_to_current_position_button_clicked(self):
    """Function to move last ending position of selected subtitle to current cursor position"""
    subtitles.last_end_to_current_position(position=session.SUBTITLE.get('position', 0))
    timeline.update(self)
    left_panel.update(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)
    session.set_unsaved()


def last_start_to_current_position_button_clicked(self):
    """Function to move last starting position subtitle to current cursor position"""
    subtitles.last_start_to_current_position(position=session.SUBTITLE.get('position', 0))
    timeline.update(self)
    left_panel.update(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)
    session.set_unsaved()


def last_start_last_end_to_current_position_button_clicked(self):
    """Function to move starting position subtitle to current cursor position"""
    subtitles.subtitle_start_to_current_position(position=session.SUBTITLE.get('position', 0))
    subtitles.last_end_to_current_position(position=session.SUBTITLE.get('position', 0) - .001)
    timeline.update(self)
    left_panel.update(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)
    session.set_unsaved()


def next_start_next_end_to_current_position_button_clicked(self):
    """Function to move ending position subtitle to current cursor position"""
    subtitles.subtitle_end_to_current_position(position=session.SUBTITLE.get('position', 0))
    subtitles.next_start_to_current_position(position=session.SUBTITLE.get('position', 0))
    timeline.update(self)
    left_panel.update(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)
    session.set_unsaved()


def next_end_to_current_position_button_clicked(self):
    """Function to move next ending position to current cursor position"""
    subtitles.next_end_to_current_position(position=session.SUBTITLE.get('position', 0))
    timeline.update(self)
    left_panel.update(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)
    session.set_unsaved()


def change_playback_speed_clicked(self):
    """Function to call when playback speed button is clicked"""
    if not self.change_playback_speed.isChecked():
        session.CONFIG['playback_speed'] = 1.0
        self.preview_panel_player.update_speed()
    update_playback_speed_buttons(self, animate=True)
    self.timeline_widget.setFocus(Qt.TabFocusReason)


def change_playback_speed_decrease_clicked(self):
    """Function to call when playback speed decrease button is clicked"""
    self.change_playback_speed_slider.setValue(self.change_playback_speed_slider.value() - 10)
    session.CONFIG['playback_speed'] = self.change_playback_speed_slider.value() / 100.0
    self.preview_panel_player.update_speed()
    update_playback_speed_buttons(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)


def change_playback_speed_slider(self):
    """Function to call when playback speed slider button is changed"""
    session.CONFIG['playback_speed'] = self.change_playback_speed_slider.value() / 100.0
    self.preview_panel_player.update_speed()
    update_playback_speed_buttons(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)


def change_playback_speed_increase_clicked(self):
    """Function to call when playback speed increase button is clicked"""
    self.change_playback_speed_slider.setValue(self.change_playback_speed_slider.value() + 10)
    session.CONFIG['playback_speed'] = self.change_playback_speed_slider.value() / 100.0
    self.preview_panel_player.update_speed()
    update_playback_speed_buttons(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)


def repeat_playback_clicked(self):
    session.CONFIG['repeat_activated'] = self.repeat_playback.isChecked()
    session.REPEAT_DURATION_BUFFER = []
    update_playback_repeat_buttons(self, animate=True)
    self.timeline_widget.setFocus(Qt.TabFocusReason)


def repeat_playback_duration_changed(self):
    """Function to call when playback repeat duration is changed"""
    session.CONFIG['playback_repeat_duration'] = self.repeat_playback_duration.value()
    session.REPEAT_DURATION_BUFFER = []
    timeline.update(self)


def repeat_playback_times_changed(self):
    """Function to call when playback repeat number of times is changed"""
    session.CONFIG['playback_repeat_times'] = self.repeat_playback_times.value()
    session.REPEAT_DURATION_BUFFER = []
    timeline.update(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)


def playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self):
    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_border_color_button.setStyleSheet('background-color: {color};'.format(color=session.CONFIG['timeline'].get('selected_subtitle_border_color', '#cc3e5363')))
    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_fill_color_button.setStyleSheet('background-color: {color};'.format(color=session.CONFIG['timeline'].get('selected_subtitle_fill_color', '#cc3e5363')))
    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_text_color_button.setStyleSheet('background-color: {color};'.format(color=session.CONFIG['timeline'].get('selected_subtitle_text_color', '#ffffffff')))
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_border_color_button.setStyleSheet('background-color: {color};'.format(color=session.CONFIG['timeline'].get('subtitle_border_color', '#ff6a7483')))
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_fill_color_button.setStyleSheet('background-color: {color};'.format(color=session.CONFIG['timeline'].get('subtitle_fill_color', '#ccb8cee0')))
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_text_color_button.setStyleSheet('background-color: {color};'.format(color=session.CONFIG['timeline'].get('subtitle_text_color', '#ff304251')))
    self.playercontrols_properties_panel_tabwidget_subtitles_subtitle_arrow_normal_button.setStyleSheet('background-color: {color};'.format(color=session.CONFIG['timeline'].get('subtitle_arrow_color', '#ff969696')))
    self.playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_arrow_normal_button.setStyleSheet('background-color: {color};'.format(color=session.CONFIG['timeline'].get('selected_subtitle_arrow_color', '#ff969696')))
    self.playercontrols_properties_panel_tabwidget_waveform_border_color_button.setStyleSheet('background-color: {color};'.format(color=session.CONFIG['timeline'].get('waveform_border_color', '#ff153450')))
    self.playercontrols_properties_panel_tabwidget_waveform_fill_color_button.setStyleSheet('background-color: {color};'.format(color=session.CONFIG['timeline'].get('waveform_fill_color', '#cc153450')))
    self.playercontrols_properties_panel_tabwidget_background_grid_color_button.setStyleSheet('background-color: {color};'.format(color=session.CONFIG['timeline'].get('grid_color', '#336a7483')))
    self.playercontrols_properties_panel_tabwidget_background_time_text_color_button.setStyleSheet('background-color: {color};'.format(color=session.CONFIG['timeline'].get('time_text_color', '#806a7483')))
    self.playercontrols_properties_panel_tabwidget_background_cursor_color_button.setStyleSheet('background-color: {color};'.format(color=session.CONFIG['timeline'].get('cursor_color', '#ccff0000')))


def show_or_hide_playercontrols_properties_panel(self):
    """Function to show playercontrol properties panel"""
    panel_width = 365
    if self.playercontrols_properties_panel_toggle.isChecked():
        self.playercontrols_properties_panel_placeholder.setGeometry(self.playercontrols_widget.width() - panel_width, 5, panel_width, 180)
        self.playercontrols_properties_panel.setGeometry(0, 56, self.playercontrols_properties_panel_placeholder.width(), 140)
        # self.playercontrols_properties_panel_toggle.setGeometry(self.playercontrols_widget_bottom_right_corner.x(), self.playercontrols_widget_bottom_right_corner.y() + 100, self.playercontrols_widget_bottom_right_corner.width(), self.playercontrols_widget_bottom_right_corner.height())
    else:
        self.playercontrols_properties_panel_placeholder.setGeometry(self.playercontrols_widget.width() - panel_width, 5, panel_width, 80)
        self.playercontrols_properties_panel.setGeometry(0, 56, self.playercontrols_properties_panel_placeholder.width(), 140)
        # self.playercontrols_properties_panel_toggle.setGeometry(self.playercontrols_widget_bottom_right_corner.x(), self.playercontrols_widget_bottom_right_corner.y(), self.playercontrols_widget_bottom_right_corner.width(), self.playercontrols_widget_bottom_right_corner.height())


def playercontrols_properties_panel_toggle_pressed(self):
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    show_or_hide_playercontrols_properties_panel(self)


def playercontrols_properties_panel_tabwidget_subtitles_subtitle_border_color_button_clicked(self):
    """Function to show qcolordialog to choose subtitle border color"""
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['timeline']['subtitle_border_color'] = color.name(1)
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    self.timeline_widget.update()


def playercontrols_properties_panel_tabwidget_subtitles_subtitle_fill_color_button_clicked(self):
    """Function to show qcolordialog to choose subtitle fill color"""
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['timeline']['subtitle_fill_color'] = color.name(1)
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    self.timeline_widget.update()


def playercontrols_properties_panel_tabwidget_subtitles_subtitle_text_color_button_clicked(self):
    """Function to show qcolordialog to choose subtitle text color"""
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['timeline']['subtitle_text_color'] = color.name(1)
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    self.timeline_widget.update()


def playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_border_color_button_clicked(self):
    """Function to show qcolordialog to choose selected subtitle border color"""
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['timeline']['selected_subtitle_border_color'] = color.name(1)
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    self.timeline_widget.update()


def playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_fill_color_button_clicked(self):
    """Function to show qcolordialog to choose selected subtitle fill color"""
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['timeline']['selected_subtitle_fill_color'] = color.name(1)
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    self.timeline_widget.update()


def playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_text_color_button_clicked(self):
    """Function to show qcolordialog to choose selected subtitle text color"""
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['timeline']['selected_subtitle_text_color'] = color.name(1)
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    self.timeline_widget.update()


def playercontrols_properties_panel_tabwidget_subtitles_subtitle_arrow_normal_button_clicked(self):
    """Function to show qcolordialog to choose selected subtitle arrow color"""
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['timeline']['subtitle_arrow_color'] = color.name(1)
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    self.timeline_widget.update()


def playercontrols_properties_panel_tabwidget_subtitles_selected_subtitle_arrow_normal_button_clicked(self):
    """Function to show qcolordialog to choose selected selected subtitle arrow color"""
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['timeline']['selected_subtitle_arrow_color'] = color.name(1)
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    self.timeline_widget.update()


def playercontrols_properties_panel_tabwidget_waveform_border_color_button_clicked(self):
    """Function to show qcolordialog to choose waveform border color"""
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        for available_zoom in session.VIDEO['waveform']:
            if session.VIDEO['waveform'][available_zoom].get('qimages', []):
                session.VIDEO['waveform'][available_zoom]['qimages'] = []
        session.CONFIG['timeline']['waveform_border_color'] = color.name(1)
        self.thread_get_qimages.border_color = session.CONFIG['timeline'].get('waveform_border_color', '#ff153450')
        self.thread_get_qimages.fill_color = session.CONFIG['timeline'].get('waveform_fill_color', '#cc153450')
        self.thread_get_qimages.start(QThread.IdlePriority)
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    self.timeline_widget.update()


def playercontrols_properties_panel_tabwidget_waveform_fill_color_button_clicked(self):
    """Function to show qcolordialog to choose waveform fill color"""
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        for available_zoom in session.VIDEO['waveform']:
            if session.VIDEO['waveform'][available_zoom].get('qimages', []):
                session.VIDEO['waveform'][available_zoom]['qimages'] = []
        session.CONFIG['timeline']['waveform_fill_color'] = color.name(1)
        self.thread_get_qimages.border_color = session.CONFIG['timeline'].get('waveform_border_color', '#ff153450')
        self.thread_get_qimages.fill_color = session.CONFIG['timeline'].get('waveform_fill_color', '#cc153450')
        self.thread_get_qimages.start(QThread.IdlePriority)
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    self.timeline_widget.update()


def playercontrols_properties_panel_tabwidget_background_grid_color_button_clicked(self):
    """Function to show qcolordialog to choose background start color"""
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['timeline']['grid_color'] = color.name(1)
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    self.timeline_widget.update()


def playercontrols_properties_panel_tabwidget_background_time_text_color_button_clicked(self):
    """Function to show qcolordialog to choose background end color"""
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['timeline']['time_text_color'] = color.name(1)
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    self.timeline_widget.update()


def playercontrols_properties_panel_tabwidget_background_cursor_color_button_clicked(self):
    """Function to show qcolordialog to choose background end color"""
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['timeline']['cursor_color'] = color.name(1)
    playercontrols_properties_panel_tabwidget_subtitles_update_widgets(self)
    self.timeline_widget.update()


def send_text_to_last_subtitle_button_clicked(self):
    """Function to call when send text to last subtitle is clicked"""
    if session.SUBTITLE.get('selected', None) is not None:
        if self.left_panel_subtitleslist_textedit.textCursor().position():
            pos = self.left_panel_subtitleslist_textedit.textCursor().position()
            last_text = self.left_panel_subtitleslist_textedit.toPlainText()[:pos].strip()
            next_text = self.left_panel_subtitleslist_textedit.toPlainText()[pos:].strip()
            subtitles.send_text_to_last_subtitle(selected_subtitle=session.SUBTITLE['selected'], last_text=last_text, next_text=next_text)
        elif self.left_panel_subtitleslist_translation_textedit.textCursor().position():
            pos = self.left_panel_subtitleslist_translation_textedit.textCursor().position()
            last_text = self.left_panel_subtitleslist_translation_textedit.toPlainText()[:pos].strip()
            next_text = self.left_panel_subtitleslist_translation_textedit.toPlainText()[pos:].strip()
            subtitles.send_translated_text_to_last_subtitle(selected_subtitle=session.SUBTITLE['selected'], last_text=last_text, next_text=next_text)
        timeline.update(self)
        left_panel.update(self)
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        session.set_unsaved()


def send_text_to_next_subtitle_button_clicked(self):
    """Function to call when send text to last subtitle is clicked"""
    if session.SUBTITLE.get('selected', None) is not None:
        if self.left_panel_subtitleslist_textedit.textCursor().position():
            pos = self.left_panel_subtitleslist_textedit.textCursor().position()
            last_text = self.left_panel_subtitleslist_textedit.toPlainText()[:pos].strip()
            next_text = self.left_panel_subtitleslist_textedit.toPlainText()[pos:].strip()
            subtitles.send_text_to_next_subtitle(selected_subtitle=session.SUBTITLE['selected'], last_text=last_text, next_text=next_text)
        elif self.left_panel_subtitleslist_translation_textedit.textCursor().position():
            pos = self.left_panel_subtitleslist_translation_textedit.textCursor().position()
            last_text = self.left_panel_subtitleslist_translation_textedit.toPlainText()[:pos].strip()
            next_text = self.left_panel_subtitleslist_translation_textedit.toPlainText()[pos:].strip()            
            subtitles.send_translated_text_to_next_subtitle(selected_subtitle=session.SUBTITLE['selected'], last_text=last_text, next_text=next_text)
        timeline.update(self)
        left_panel.update(self)
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        session.set_unsaved()


def send_text_to_last_subtitle_and_slice_button_clicked(self):
    """Function to call when send text to last subtitle is clicked"""
    if session.SUBTITLE.get('selected', None) is not None:
        pos = self.left_panel_subtitleslist_textedit.textCursor().position()
        last_text = self.left_panel_subtitleslist_textedit.toPlainText()[:pos].strip()
        next_text = self.left_panel_subtitleslist_textedit.toPlainText()[pos:].strip()
        subtitles.send_text_to_last_subtitle(selected_subtitle=session.SUBTITLE['selected'], last_text=last_text, next_text=next_text)
        subtitles.subtitle_start_to_current_position(position=session.SUBTITLE.get('position', 0))
        subtitles.last_end_to_current_position(position=session.SUBTITLE.get('position', 0) - .001)
        timeline.update(self)
        left_panel.update(self)
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        session.set_unsaved()



def send_text_to_next_subtitle_and_slice_button_clicked(self):
    """Function to call when send text to last subtitle is clicked"""
    if session.SUBTITLE.get('selected', None) is not None:
        pos = self.left_panel_subtitleslist_textedit.textCursor().position()
        last_text = self.left_panel_subtitleslist_textedit.toPlainText()[:pos].strip()
        next_text = self.left_panel_subtitleslist_textedit.toPlainText()[pos:].strip()
        subtitles.send_text_to_next_subtitle(selected_subtitle=session.SUBTITLE['selected'], last_text=last_text, next_text=next_text)
        subtitles.subtitle_end_to_current_position(position=session.SUBTITLE.get('position', 0))
        subtitles.next_start_to_current_position(position=session.SUBTITLE.get('position', 0))
        timeline.update(self)
        left_panel.update(self)
        self.timeline_widget.setFocus(Qt.TabFocusReason)
        session.set_unsaved()


def music_voice_separation_activate_button_clicked(self):
    self.music_voice_separation_activate_button.setEnabled(False)
    self.music_voice_separation_activate_button.setText('...')
    self.music_voice_separation_thread.filename = session.VIDEO['filepath']
    self.music_voice_separation_thread.start()


def music_voice_separation_music_icon_clicked(self):
    if not 'volume' in session.VIDEO.get('music_voice_separation', {}):
        session.VIDEO['music_voice_separation']['volume'] = .5    
    session.VIDEO['music_voice_separation']['volume'] -= .05
    music_voice_separation_slider_update(self)
    music_voice_separation_buttons_update(self)

def music_voice_separation_box_update(self):
    self.music_voice_separation_box.setEnabled(bool(session.VIDEO.get('music_voice_separation', {})))
    music_voice_separation_slider_update(self)
    music_voice_separation_buttons_update(self)

def music_voice_separation_voice_icon_clicked(self):
    if not 'volume' in session.VIDEO.get('music_voice_separation', {}):
        session.VIDEO['music_voice_separation']['volume'] = .5
    session.VIDEO['music_voice_separation']['volume'] += .05
    music_voice_separation_slider_update(self)
    music_voice_separation_buttons_update(self)

def music_voice_separation_buttons_update(self):
    self.music_voice_separation_voice_icon.setEnabled(session.VIDEO.get('music_voice_separation', {}).get('volume', .5) < 1.0)
    self.music_voice_separation_music_icon.setEnabled(session.VIDEO.get('music_voice_separation', {}).get('volume', .5) > 0.0)

def music_voice_separation_slider_update(self):
    self.music_voice_separation_slider.setValue(int(session.VIDEO.get('music_voice_separation', {}).get('volume', .5) * 100))
    self.music_voice_separation_slider.setProperty('class', 'middle' if session.VIDEO.get('music_voice_separation', {}).get('volume', .5) == .5 else '')
    self.music_voice_separation_slider.style().unpolish(self.music_voice_separation_slider)
    self.music_voice_separation_slider.style().polish(self.music_voice_separation_slider)

def music_voice_separation_slider_changed(self):
    new_volume = self.music_voice_separation_slider.value() / 100.0
    if session.VIDEO.get('music_voice_separation', False):
        session.VIDEO['music_voice_separation']['volume'] = new_volume
    if not isinstance(session.CONFIG.get('videoplayer'), dict):
        session.CONFIG['videoplayer'] = {}
    session.CONFIG['videoplayer']['music_voice_separation_volume'] = new_volume
    session.set_unsaved()

    music_voice_separation_buttons_update(self)

    audio_device = self.preview_panel_player._audio_device
    original_track = getattr(audio_device, 'original_track', None)
    background_sound = getattr(audio_device, 'background_sound', None)
    vocals_sound = getattr(audio_device, 'vocals_sound', None)
    if original_track is None or background_sound is None or vocals_sound is None:
        return

    if session.VIDEO['music_voice_separation']['volume'] == .5:
        original_track.enabled = True
        background_sound.enabled = False
        vocals_sound.enabled = False
        self.music_voice_separation_slider.setProperty('class', 'middle')
        self.music_voice_separation_slider.style().unpolish(self.music_voice_separation_slider)
        self.music_voice_separation_slider.style().polish(self.music_voice_separation_slider)

        # At neutral we're playing the original mix as-is. Dubs sit on
        # top at full volume — matches behavior before the separation
        # slider was introduced.
        background_volume = 1.0
    else:
        original_track.enabled = False
        background_sound.enabled = True
        vocals_sound.enabled = True
        self.music_voice_separation_slider.setProperty('class', '')
        self.music_voice_separation_slider.style().unpolish(self.music_voice_separation_slider)
        self.music_voice_separation_slider.style().polish(self.music_voice_separation_slider)

        background_volume = 1 if session.VIDEO['music_voice_separation']['volume'] < .5 else (1 - ((session.VIDEO['music_voice_separation']['volume'] - .5) * 2))
        voice_volume = 1 if session.VIDEO['music_voice_separation']['volume'] > .5 else (session.VIDEO['music_voice_separation']['volume'] * 2)

        background_sound.gain = background_volume
        vocals_sound.gain = voice_volume

    # Dubs ride the background gain: at neutral / "music only" they
    # play full, at "voice only" they fade out so the user can hear the
    # isolated original vocals without the dubbed version on top.
    if hasattr(audio_device, 'set_dub_separation_gain'):
        audio_device.set_dub_separation_gain(background_volume)


def timeline_show_speaker_color_button_clicked(self):
    self.timeline_widget.show_speaker_color = self.timeline_show_speaker_color_button.isChecked()
    session.CONFIG['timeline']['show_speaker_color'] = self.timeline_show_speaker_color_button.isChecked()
    self.timeline_widget.update()


def timeline_show_speaker_tracks_button_clicked(self):
    self.timeline_widget.show_speaker_tracks = self.timeline_show_speaker_tracks_button.isChecked()
    session.CONFIG['timeline']['show_speaker_tracks'] = self.timeline_show_speaker_tracks_button.isChecked()
    self.timeline_widget.update()


@shortcut('timeline_escape_action', 'Escape timeline actions', ['Escape'])
def escape_actions(self):
    if self.timeline_widget.is_smart_splicing:
        self.timeline_widget.is_smart_splicing = False
        self.timeline_widget.update()
    slice_selected_subtitle_button_update(self)


def translate(self):
    self.add_subtitle_button.setText(' ' + _('playercontrols.add'))
    self.snap_button_label.setText(_('playercontrols.snap'))
    self.step_button.setText(_('playercontrols.step'))
    self.remove_selected_subtitle_button.setText(' ' + _('playercontrols.remove'))
    self.grid_button.setText(_('playercontrols.grid'))
    self.onset_button.setText(_('playercontrols.onset'))

    self.send_text_to_last_subtitle_button.setToolTip(_('playercontrols.send_text_to_last_subtitle'))
    self.send_text_to_last_subtitle_and_slice_button.setToolTip(_('playercontrols.send_text_to_last_subtitle_and_slice'))
    self.send_text_to_next_subtitle_button.setToolTip(_('playercontrols.send_text_to_next_subtitle'))
    self.send_text_to_next_subtitle_and_slice_button.setToolTip(_('playercontrols.send_text_to_next_subtitle_and_slice'))
    self.slice_selected_subtitle_button.setToolTip(_('playercontrols.slice_selected_subtitle'))
    self.merge_next_selected_subtitle_button.setToolTip(_('playercontrols.merge_next_selected_subtitle'))
    self.last_end_to_current_position_button.setToolTip(_('playercontrols.last_end_to_current_position'))
    self.last_start_to_current_position_button.setToolTip(_('playercontrols.last_start_to_current_position'))
    self.last_start_last_end_to_current_position_button.setToolTip(_('playercontrols.last_start_last_end_to_current_position'))
    self.next_start_next_end_to_current_position_button.setToolTip(_('playercontrols.next_start_next_end_to_current_position'))
    self.next_end_to_current_position_button.setToolTip(_('playercontrols.next_end_to_current_position'))
    self.next_start_to_current_position_button.setToolTip(_('playercontrols.next_start_to_current_position'))
    self.change_playback_speed.setToolTip(_('playercontrols.change_playback_speed'))
    self.change_playback_speed_decrease.setToolTip(_('playercontrols.change_playback_speed_decrease'))
    self.change_playback_speed_increase.setToolTip(_('playercontrols.change_playback_speed_increase'))
    self.playercontrols_stop_button.setToolTip(_('playercontrols.stop'))
    self.playercontrols_playpause_button.setToolTip(_('playercontrols.playpause'))
    self.playercontrols_play_from_last_start_button.setToolTip(_('playercontrols.play_from_last_start'))
    self.playercontrols_play_from_next_start_button.setToolTip(_('playercontrols.play_from_next_start'))
    self.repeat_playback.setToolTip(_('playercontrols.repeat_playback'))
    self.gap_add_subtitle_button.setToolTip(_('playercontrols.gap_add_subtitle'))
    self.gap_remove_subtitle_button.setToolTip(_('playercontrols.gap_remove_subtitle'))
    self.add_subtitle_button.setToolTip(_('playercontrols.add_subtitle'))
    self.add_subtitle_starting_from_last.setToolTip(_('playercontrols.add_subtitle_starting_from_last'))
    self.add_subtitle_and_play.setToolTip(_('playercontrols.add_subtitle_and_play'))
    self.add_subtitle_to_next_start.setToolTip(_('playercontrols.add_subtitle_to_next_start'))
    self.remove_selected_subtitle_button.setToolTip(_('playercontrols.remove_selected_subtitle'))
    self.timelinescrolling_none_button.setToolTip(_('playercontrols.timelinescrolling_none'))
    self.timelinescrolling_page_button.setToolTip(_('playercontrols.timelinescrolling_page'))
    self.timelinescrolling_follow_button.setToolTip(_('playercontrols.timelinescrolling_follow'))
    self.snap_button.setToolTip(_('playercontrols.snap'))
    self.snap_limits_button.setToolTip(_('playercontrols.snap_limits'))
    self.snap_grid_button.setToolTip(_('playercontrols.snap_grid'))
    self.snap_move_button.setToolTip(_('playercontrols.snap_move'))
    self.snap_move_nereast_button.setToolTip(_('playercontrols.snap_move_nereast'))
    self.move_start_back_subtitle.setToolTip(_('playercontrols.move_start_back_subtitle'))
    self.move_start_forward_subtitle.setToolTip(_('playercontrols.move_start_forward_subtitle'))
    self.move_backward_subtitle.setToolTip(_('playercontrols.move_backward_subtitle'))
    self.timeline_cursor_back_frame.setToolTip(_('playercontrols.timeline_cursor_back_frame'))
    self.timeline_cursor_next_frame.setToolTip(_('playercontrols.timeline_cursor_next_frame'))
    self.move_forward_subtitle.setToolTip(_('playercontrols.move_forward_subtitle'))
    self.move_end_back_subtitle.setToolTip(_('playercontrols.move_end_back_subtitle'))
    self.move_end_forward_subtitle.setToolTip(_('playercontrols.move_end_forward_subtitle'))
    self.grid_button.setToolTip(_('playercontrols.grid'))
    self.onset_button.setToolTip(_('playercontrols.onset_tooltip'))
    self.grid_frames_button.setToolTip(_('playercontrols.grid_frames'))
    self.grid_seconds_button.setToolTip(_('playercontrols.grid_seconds'))
    self.grid_scenes_button.setToolTip(_('playercontrols.grid_scenes'))
    self.step_button.setToolTip(_('playercontrols.step'))
    self.zoomin_button.setToolTip(_('playercontrols.zoom_in'))
    self.zoomout_button.setToolTip(_('playercontrols.zoom_out'))
    self.timeline_show_speaker_color_button.setToolTip(_('playercontrols.timeline_show_speaker_color'))
    self.timeline_show_speaker_tracks_button.setToolTip(_('playercontrols.timeline_show_speaker_tracks'))


