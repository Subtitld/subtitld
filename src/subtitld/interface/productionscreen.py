from PySide6.QtWidgets import QSplitter
from PySide6.QtCore import Qt, QTimer

from subtitld.modules import file_io
from subtitld.modules import session

from subtitld.interface import left_panel
from subtitld.interface import preview_panel
from subtitld.interface import bottom_panel
from subtitld.interface import top_bar
from subtitld.interface.translation import _


# NOTE: there used to be a thin progress strip pinned to the bottom edge
# of the main window while the USFX Phase 2 extractor was streaming dubs
# / waveform / FLAC stems to disk. It's gone now — the per-dub
# `usfx_member_ready` signal already makes individual clips pop into the
# timeline as their bytes land, which is more legible than a generic
# progress bar (the user sees the specific things they're waiting on
# instead of an abstract percent). The Save-button gate still uses the
# `usfx_background_load_started` / `usfx_background_load_finished` signal
# pair — that one matters because saving mid-stream would re-zip dubs
# whose source bytes aren't on disk yet.


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
    # Suppress painting on all animated panels via setUpdatesEnabled(False)
    # before `setCurrentWidget` makes the production splitter visible.
    # The previous setVisible(False) approach didn't fully work: when the
    # animation's first valueChanged tick fired and called setVisible(True),
    # the resulting layout pass overrode the widget's pos to its laid-out
    # final spot — then Qt painted at FINAL before the animation's next
    # tick could move it back to start_pos, producing the one-frame "all
    # panels in place" flash the user kept reporting.
    #
    # setUpdatesEnabled(False) is the right tool: the widget still
    # participates in the layout (so size/geometry settles correctly while
    # the animation is starting), but Qt suppresses paint events entirely.
    # We re-enable updates ~80 ms later (~5 animation ticks at 60 Hz),
    # after the animation has firmly taken ownership of `pos` and the
    # layout's final-position setGeometry has been overridden by the
    # animation engine. By then the widget is mid-slide and the first
    # paint shows it correctly animating in.
    panels = [w for w in (
        getattr(self, 'bottom_panel', None),
        getattr(self, 'preview_panel', None),
        getattr(self, 'left_panel', None),
    ) if w is not None]
    for w in panels:
        w.setUpdatesEnabled(False)
    self.central_widget.layout().setCurrentWidget(self.main_vertical_splitter)
    # Defer the per-panel show() calls to the next event-loop tick so
    # the layout pass triggered by setCurrentWidget has run by then.
    # Each per-panel show() calls `animate_element`, which reads
    # `widget.pos()` (for the animation's end-value) and the parent's
    # geometry (for the slide's off-screen start). Running them
    # synchronously here captures pre-layout values — the preview's
    # endValue ends up at (0, 0) and the slide is barely visible. By
    # the time the singleShot(0) fires, the splitter has sized its
    # children to their real allocations.
    def _start_animations():
        left_panel.show(self)
        preview_panel.show(self)
        bottom_panel.show(self)
        # Re-enable per-panel paints once the animations are firmly
        # past the layout's "final position" overshoot.
        for w in panels:
            QTimer.singleShot(80, lambda w=w: w.setUpdatesEnabled(True))
    QTimer.singleShot(0, _start_animations)
    

def hide(self):
    left_panel.hide(self)
    preview_panel.hide(self)
    bottom_panel.hide(self)


def translate(self):
    top_bar.translate(self)
    left_panel.translate(self)
    preview_panel.translate(self)
    bottom_panel.translate(self)
    