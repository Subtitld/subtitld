"""Host side of the *video manipulation* plugin family.

Feeds live playback frames to the active ``VideoProvider`` (currently the
built-in mouth crop) on a worker thread and shows whatever image the plugin
returns in a small resizable/movable panel that floats over the video preview
— a PiP you can drag to a corner or enlarge into a working panel.

Wiring lives in ``preview_panel.py``: it creates one ``VideoManipulationController``
per player, taps the frame stream (throttled) into ``controller.on_frame``,
and toggles it via ``controller.set_enabled``.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QThread, QMutex, QWaitCondition, Signal, QRect, QRectF, QPoint, QSize
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QImage, QPixmap
from PySide6.QtWidgets import QWidget

from subtitld.modules import session
from subtitld.modules import mouth_tracker
from subtitld.modules import addons
from subtitld.modules.addons.provider import TASK_VIDEO_MANIPULATE

# Frame-submission cadence. Detection runs on the worker; converting frames
# and running it faster than this buys nothing the eye can use and only steals
# CPU/GIL from playback.
_TARGET_HZ = 12.0
_MIN_SUBMIT_INTERVAL = 1.0 / _TARGET_HZ


class _VideoFrameWorker(QThread):
    """Runs the active provider's ``process_frame`` off the main thread.

    The main thread hands over the latest RGB frame + context via ``submit``;
    the worker always processes the newest pending frame (older ones are
    dropped), so a slow detector degrades to a lower refresh rate rather than
    a growing backlog.
    """

    output_ready = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('subtitld-mouth-worker')
        self._provider = None
        self._pending = None            # (rgb, context) or None
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._running = True

    def set_provider(self, provider):
        self._mutex.lock()
        self._provider = provider
        self._mutex.unlock()

    def submit(self, rgb, context):
        self._mutex.lock()
        self._pending = (rgb, context)
        self._cond.wakeAll()
        self._mutex.unlock()

    def stop(self):
        self._mutex.lock()
        self._running = False
        self._cond.wakeAll()
        self._mutex.unlock()

    def run(self):
        while True:
            self._mutex.lock()
            while self._running and self._pending is None:
                self._cond.wait(self._mutex)
            if not self._running:
                self._mutex.unlock()
                return
            rgb, context = self._pending
            self._pending = None
            provider = self._provider
            self._mutex.unlock()

            if provider is None:
                continue
            try:
                result = provider.process_frame(rgb, context)
            except Exception:
                import traceback
                import sys as _sys
                traceback.print_exc(file=_sys.stderr)
                continue
            if result is not None:
                self.output_ready.emit(result)


class VideoOutputView(QWidget):
    """Floating, movable, resizable panel that shows the plugin's output image.

    Drag the header to move it; drag the bottom-right grip to resize. Lives as
    a child of the preview's viewport so it always paints over the video.
    """

    _HEADER_H = 20
    _GRIP = 14
    _MIN_W = 120
    _MIN_H = 90

    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('video_output_view')
        self._image = None            # QImage (current output)
        self._label = ''
        self._empty_text = ''
        self._drag_mode = None        # 'move' | 'resize' | None
        self._drag_origin = QPoint()
        self._start_geom = QRect()
        self.setMouseTracking(True)
        self.setCursor(Qt.ArrowCursor)

    # ---- content --------------------------------------------------------
    def set_output(self, image, label=''):
        self._image = image
        self._label = label or ''
        self.update()

    def set_empty_text(self, text):
        self._empty_text = text or ''
        self.update()

    def clear_output(self):
        self._image = None
        self.update()

    # ---- geometry helpers ----------------------------------------------
    def _grip_rect(self):
        return QRect(self.width() - self._GRIP, self.height() - self._GRIP,
                     self._GRIP, self._GRIP)

    def _header_rect(self):
        return QRect(0, 0, self.width(), self._HEADER_H)

    # ---- painting -------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            body = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
            p.setPen(QPen(QColor(0, 0, 0, 150), 1))
            p.setBrush(QColor(14, 18, 24, 235))
            p.drawRoundedRect(body, 6, 6)

            # Header bar.
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(43, 104, 156, 235))
            p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self._HEADER_H), 6, 6)
            p.fillRect(QRect(1, self._HEADER_H - 6, self.width() - 2, 6),
                       QColor(43, 104, 156, 235))
            p.setPen(QColor(235, 244, 250, 235))
            p.setFont(QFont('Montserrat', 8, QFont.Bold))
            title = self._label or self._title_fallback()
            p.drawText(QRect(8, 0, self.width() - 30, self._HEADER_H),
                       Qt.AlignVCenter | Qt.AlignLeft, title)
            # Close "x".
            p.drawText(QRect(self.width() - 20, 0, 16, self._HEADER_H),
                       Qt.AlignCenter, '✕')

            # Content area.
            content = QRect(4, self._HEADER_H + 2, self.width() - 8,
                            self.height() - self._HEADER_H - 6)
            if self._image is not None and not self._image.isNull():
                pm = QPixmap.fromImage(self._image).scaled(
                    content.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = content.x() + (content.width() - pm.width()) // 2
                y = content.y() + (content.height() - pm.height()) // 2
                p.drawPixmap(x, y, pm)
            else:
                p.setPen(QColor(150, 168, 184, 200))
                p.setFont(QFont('Montserrat', 8))
                p.drawText(content, Qt.AlignCenter | Qt.TextWordWrap,
                           self._empty_text or '')

            # Resize grip (two subtle diagonal ticks).
            g = self._grip_rect()
            p.setPen(QPen(QColor(180, 200, 216, 160), 1))
            for off in (3, 7):
                p.drawLine(g.right() - off, g.bottom() - 1,
                           g.right() - 1, g.bottom() - off)
        finally:
            p.end()

    def _title_fallback(self):
        return ''

    # ---- interaction ----------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        if QRect(self.width() - 20, 0, 20, self._HEADER_H).contains(pos):
            self.closed.emit()
            return
        if self._grip_rect().contains(pos):
            self._drag_mode = 'resize'
        elif self._header_rect().contains(pos):
            self._drag_mode = 'move'
        else:
            self._drag_mode = None
            return
        self._drag_origin = event.globalPosition().toPoint()
        self._start_geom = self.geometry()
        event.accept()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._drag_mode is None:
            # Hover cursor feedback.
            if self._grip_rect().contains(pos):
                self.setCursor(Qt.SizeFDiagCursor)
            elif self._header_rect().contains(pos):
                self.setCursor(Qt.SizeAllCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            return
        delta = event.globalPosition().toPoint() - self._drag_origin
        if self._drag_mode == 'move':
            new_top_left = self._start_geom.topLeft() + delta
            self._move_within_parent(new_top_left)
        elif self._drag_mode == 'resize':
            w = max(self._MIN_W, self._start_geom.width() + delta.x())
            h = max(self._MIN_H, self._start_geom.height() + delta.y())
            if self.parent() is not None:
                w = min(w, self.parent().width() - self.x())
                h = min(h, self.parent().height() - self.y())
            self.resize(w, h)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_mode is not None:
            self._drag_mode = None
            self._persist_geometry()
            event.accept()

    def _move_within_parent(self, top_left):
        if self.parent() is not None:
            max_x = max(0, self.parent().width() - self.width())
            max_y = max(0, self.parent().height() - self.height())
            top_left = QPoint(max(0, min(max_x, top_left.x())),
                              max(0, min(max_y, top_left.y())))
        self.move(top_left)

    def _persist_geometry(self):
        try:
            session.CONFIG.setdefault('video_manipulation', {})['geometry'] = [
                self.x(), self.y(), self.width(), self.height()]
        except Exception:
            pass


class VideoManipulationController:
    """Owns the worker + output panel for one player and mediates the frame
    flow. The preview panel calls ``on_frame`` (throttled) and ``set_enabled``."""

    def __init__(self, player_widget):
        self._player = player_widget
        self._view = None
        self._worker = None
        self._enabled = False
        self._last_submit_t = 0.0
        self._provider = None
        self._ref_cache = {}          # speaker name -> (QImage id, rgb ndarray)

    # ---- setup ----------------------------------------------------------
    def _host_widget(self):
        # Parent the overlay to the plain PlayerWidget and raise it above the
        # video — NOT to the QGraphicsView's viewport. A child QWidget on a
        # QGraphicsView viewport clashes with the view's own active painter
        # ("A paint device can only be painted by one painter at a time") and
        # can abort the process. The PlayerWidget fills the same area, so the
        # panel still floats over the video.
        return self._player

    def _ensure_view(self):
        if self._view is None:
            self._view = VideoOutputView(self._host_widget())
            self._view.set_empty_text(_('video_manipulation.searching'))
            self._view.closed.connect(lambda: self.set_enabled(False))
            self._restore_geometry()
        return self._view

    def _ensure_worker(self):
        if self._worker is None:
            self._worker = _VideoFrameWorker()
            self._worker.output_ready.connect(self._on_output)
            self._worker.start()
            # Stop the thread cleanly on app quit so Qt doesn't warn about a
            # running QThread being destroyed.
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.shutdown)
        return self._worker

    def _restore_geometry(self):
        cfg = session.CONFIG.get('video_manipulation', {})
        geom = cfg.get('geometry')
        host = self._host_widget()
        if isinstance(geom, (list, tuple)) and len(geom) == 4:
            x, y, w, h = [int(v) for v in geom]
        else:
            w, h = 240, 172
            x = max(0, host.width() - w - 12)
            y = max(0, host.height() - h - 12)
        self._view.setGeometry(x, y, w, h)

    def reposition(self):
        """Keep the panel inside the preview after a resize."""
        if self._view is not None and self._view.isVisible():
            self._view._move_within_parent(self._view.pos())

    # ---- enable / disable ----------------------------------------------
    def is_enabled(self):
        return self._enabled

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        self._enabled = enabled
        session.CONFIG.setdefault('video_manipulation', {})['enabled'] = enabled
        if enabled:
            self._provider = self._resolve_provider()
            view = self._ensure_view()
            worker = self._ensure_worker()
            worker.set_provider(self._provider)
            if self._provider is not None and not self._provider.is_available():
                view.set_empty_text(_('video_manipulation.unavailable'))
                view.clear_output()
            else:
                view.set_empty_text(_('video_manipulation.searching'))
            view.show()
            view.raise_()
        elif self._view is not None:
            self._view.hide()
        # Reflect state on the toolbar toggle, if present.
        try:
            btn = getattr(self._player.window(), 'timeline_show_mouth_button', None)
            if btn is not None and btn.isChecked() != enabled:
                btn.blockSignals(True)
                btn.setChecked(enabled)
                btn.blockSignals(False)
        except Exception:
            pass

    def _resolve_provider(self):
        mgr = addons.get_manager()
        prov = mgr.default_for_task(TASK_VIDEO_MANIPULATE)
        if prov is None:
            providers = mgr.providers_for_task(TASK_VIDEO_MANIPULATE)
            prov = providers[0] if providers else None
        return prov

    # ---- per-frame ------------------------------------------------------
    def on_frame(self, qvideoframe):
        """Called from the preview's videoFrameChanged (already on the main
        thread). Throttled; converts the frame and hands it to the worker."""
        if not self._enabled or self._provider is None:
            return
        if not self._provider.is_available():
            return
        now = time.perf_counter()
        if now - self._last_submit_t < _MIN_SUBMIT_INTERVAL:
            return
        self._last_submit_t = now
        try:
            image = qvideoframe.toImage()
        except Exception:
            return
        rgb = mouth_tracker.qimage_to_rgb(image)
        if rgb is None:
            return
        self._ensure_worker().submit(rgb, self._build_context())

    def _build_context(self):
        current = session.SUBTITLE.get('current')
        speaker = current.get('speaker') if isinstance(current, dict) else None
        return {
            'playhead': float(session.SUBTITLE.get('position', 0.0) or 0.0),
            'active_speaker': speaker,
            'reference_rgb': self._speaker_reference_rgb(speaker),
            'config': self._provider_config(),
        }

    def _provider_config(self):
        cfg = session.CONFIG.get('video_manipulation', {})
        opts = cfg.get('options', {}) if isinstance(cfg.get('options'), dict) else {}
        return {
            'zoom': float(opts.get('zoom', 1.0) or 1.0),
            'match_speaker': bool(opts.get('match_speaker', True)),
        }

    def _speaker_reference_rgb(self, speaker):
        """RGB ndarray of the speaker's stored face, cached until it changes."""
        if not speaker:
            return None
        data = session.SPEAKERS.get(speaker) or {}
        qimg = data.get('image')
        if qimg is None or qimg.isNull():
            return None
        cached = self._ref_cache.get(speaker)
        key = qimg.cacheKey()
        if cached is not None and cached[0] == key:
            return cached[1]
        rgb = mouth_tracker.qimage_to_rgb(qimg)
        self._ref_cache[speaker] = (key, rgb)
        return rgb

    def _on_output(self, result):
        if not self._enabled or self._view is None:
            return
        rgb = result.get('image')
        if result.get('found') and rgb is not None:
            qimg = mouth_tracker.rgb_to_qimage(rgb)
            self._view.set_output(qimg, result.get('label') or '')
        else:
            # Keep the last good crop but drop the label when the face is lost.
            self._view.set_output(self._view._image, '')

    # ---- teardown -------------------------------------------------------
    def shutdown(self):
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(1000)
            self._worker = None


# Local import kept at the bottom to avoid a circular import at module load
# (translation -> interface package).
from subtitld.interface.translation import _  # noqa: E402
