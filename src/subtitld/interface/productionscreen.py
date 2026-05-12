from PySide6.QtWidgets import QSplitter, QWidget
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QPainter, QColor

from subtitld.modules import file_io
from subtitld.modules import session
from subtitld.modules.signals import SIGNALS as _SESSION_SIGNALS

from subtitld.interface import left_panel
from subtitld.interface import preview_panel
from subtitld.interface import bottom_panel
from subtitld.interface import top_bar
from subtitld.interface.translation import _


class _BackgroundLoadProgressBar(QWidget):
    """Thin progress strip pinned to the bottom edge of the host window.

    Visible only while a USFX background load is reporting progress; hides
    itself a short time after completion (so the eye registers "done"
    before the bar disappears). Transparent to mouse events so it never
    blocks clicks on the timeline directly above it.

    Wiring is via `signals.SIGNALS.usfx_background_load_*`, which the
    Phase 2 extractor emits from its worker thread. Qt auto-promotes
    those signal deliveries to `Qt.QueuedConnection`, so the slot runs
    on the main thread safely.
    """
    _HEIGHT_PX = 3
    _AUTO_HIDE_MS = 800

    def __init__(self, host):
        super().__init__(host)
        self._host = host
        self._progress = 0
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        host.installEventFilter(self)
        self._sync_geometry()
        self.hide()

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(self._AUTO_HIDE_MS)
        self._hide_timer.timeout.connect(self._fade_out)

        _SESSION_SIGNALS.usfx_background_load_progress.connect(self._on_progress)
        _SESSION_SIGNALS.usfx_background_load_finished.connect(self._on_finished)

    def _sync_geometry(self):
        r = self._host.rect()
        self.setGeometry(0, r.height() - self._HEIGHT_PX,
                         r.width(), self._HEIGHT_PX)
        self.raise_()

    def eventFilter(self, obj, event):
        if obj is self._host and event.type() == QEvent.Resize:
            self._sync_geometry()
        return False

    def _on_progress(self, pct):
        self._progress = max(0, min(100, int(pct)))
        if not self.isVisible():
            self._sync_geometry()
            self.show()
        self.update()
        if self._progress >= 100:
            self._hide_timer.start()

    def _on_finished(self):
        # If progress already hit 100 the hide timer is running; just
        # let it complete. If not (extraction aborted), force a hide.
        self._progress = 100
        self.update()
        self._hide_timer.start()

    def _fade_out(self):
        self.hide()
        self._progress = 0

    def paintEvent(self, _event):
        p = QPainter(self)
        # Subtle track + accent fill — same green as the save-wave overlay
        # so it reads as "Subtitld is doing something useful".
        p.fillRect(self.rect(), QColor(26, 26, 26, 180))
        if self._progress > 0:
            w = int(self.width() * self._progress / 100)
            p.fillRect(0, 0, w, self.height(), QColor('#5de845'))


def load(self):
    self.main_horizontal_splitter = QSplitter(Qt.Horizontal)
    self.main_horizontal_splitter.setHandleWidth(0)
    self.main_horizontal_splitter.splitterMoved.connect(lambda pos, index: main_horizontal_splitter_changed(self, pos, index))

    left_panel.load(self)
    
    preview_panel.load(self)
    
    self.main_horizontal_splitter.setSizes(session.CONFIG['interface_splitters'].get('main_horizontal', [25, 75]))

    self.main_vertical_splitter = QSplitter(Qt.Vertical)
    self.main_vertical_splitter.setObjectName('main_vertical_splitter')
    self.main_vertical_splitter.splitterMoved.connect(lambda pos, index: main_vertical_splitter_changed(self, pos, index))
    # Hide the native splitter handle — playercontrols renders its own
    # custom drag button at its top-right that drives the splitter sizes.
    self.main_vertical_splitter.setHandleWidth(0)

    self.main_vertical_splitter.addWidget(self.main_horizontal_splitter)
    
    bottom_panel.load(self)
    
    self.central_widget.layout().addWidget(self.main_vertical_splitter)

    self.main_vertical_splitter.setSizes(session.CONFIG['interface_splitters'].get('main_vertical', [70, 30]))

    # Pin a 3-px progress strip to the bottom of the main window. It
    # tracks geometry on resize, so it stays at the bottom edge no
    # matter how the user drags the splitters around. Wires itself to
    # the USFX Phase 2 signals at construction; nothing else to do here.
    self.background_load_progress_bar = _BackgroundLoadProgressBar(self)

    if session.CONFIG.get('autosave', {}).get('backup_enabled', True):
        self.autosave_backup_timer.start()
        # The regular timer interval defaults to 5 min — too long for a fresh
        # project the user just started editing. Fire an early dirty-check
        # 30s after the production screen loads so the first backup lands
        # quickly (autosave_backup_timer_timeout itself bails when nothing
        # is dirty, so this is a no-op for read-only browsing).
        QTimer.singleShot(30000, lambda: file_io.autosave_backup_timer_timeout())

    if session.CONFIG.get('autosave', {}).get('original_enabled', True) and session.SUBTITLE.get('filepath', '').lower().endswith('.usfx'):
        self.autosave_original_timer.start()


def main_horizontal_splitter_changed(self, pos, index):
    session.CONFIG['interface_splitters']['main_horizontal'] = self.main_horizontal_splitter.sizes()


def main_vertical_splitter_changed(self, pos, index):
    session.CONFIG['interface_splitters']['main_vertical'] = self.main_vertical_splitter.sizes()


def show(self):
    self.central_widget.layout().setCurrentWidget(self.main_vertical_splitter)
    left_panel.show(self)
    preview_panel.show(self)
    bottom_panel.show(self)
    

def hide(self):
    left_panel.hide(self)
    preview_panel.hide(self)
    bottom_panel.hide(self)


def translate(self):
    top_bar.translate(self)
    left_panel.translate(self)
    preview_panel.translate(self)
    bottom_panel.translate(self)
    