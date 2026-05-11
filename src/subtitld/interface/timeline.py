import hashlib
import os
from bisect import bisect
import numpy as np
import subprocess

from PySide6.QtWidgets import QWidget, QScrollArea, QSizePolicy
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath, QLinearGradient, QFontMetrics, QPixmap, QCursor, QBrush
from PySide6.QtCore import Qt, QRectF, QPointF, QLineF, QThread, Signal, QMarginsF, QTimer, QMargins

from subtitld.modules import session
from subtitld.modules import utils
from subtitld.modules import subtitles
from subtitld.modules import quality_check

from subtitld.interface import left_panel
from subtitld.interface import playercontrols
from subtitld.interface.translation import _


class TimelineScroll(QScrollArea):
    """Class for timeline scroll area"""
    def __init__(widget, parent=None):
        super().__init__(parent)
        # widget.setWidgetResizable(True)
        widget.setObjectName('timeline_scroll')
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        widget.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
        widget.setMinimumHeight(70)

    def enterEvent(widget, event):
        event.accept()

    def leaveEvent(widget, event):
        event.accept()

    def wheelEvent(widget, event):
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta != 0:
                # Multiplicative step: each notch (≈120 units) scales zoom by
                # ~12%. Feels linear across the 10 → 490 range, where a fixed
                # additive step is too coarse at low zoom and too fine at high.
                notches = delta / 120.0
                current = session.CONFIG.get('timeline_zoom', 100.0)
                new_zoom = current * (1.12 ** notches)
                new_zoom = max(10.0, min(490.0, new_zoom))
                if abs(new_zoom - current) >= 0.5:
                    session.CONFIG['timeline_zoom'] = new_zoom
                    playercontrols.zoom_buttons_update(widget.window())
            event.accept()
            return
        widget.horizontalScrollBar().setValue(widget.horizontalScrollBar().value() + event.angleDelta().y())
        event.accept()
    
    def resizeEvent(widget, event):
        widget.window().timeline_widget.update_size()
        event.accept()


class AudioLoaderThread(QThread):
    """
    Thread that loads audio samples and normalizes them for waveform visualization.
    Emits (samples, samplerate) when done.
    """
    finished = Signal(object, int)

    def __init__(self, samplerate=48000, parent=None):
        super().__init__(parent)
        self.filepath = None
        self.target_samplerate = samplerate
        self.proc = None
        self._cancelled = False

    def run(self):
        try:
            if self.filepath:
                samples, samplerate = self._load_audio(self.filepath, self.target_samplerate)
                if not self._cancelled:
                    self.finished.emit(samples, samplerate)
        except Exception:
            # You might emit a signal for error if needed
            pass


    def _load_audio(self, filepath, samplerate=48000):
        cmd = [
            session.FFMPEG_EXECUTABLE,
            "-v", "error",
            "-i", filepath,
            "-ac", "1",
            "-ar", str(samplerate),
            "-f", "f32le", "-",
        ]

        # Use Popen to allow termination
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=session.STARTUPINFO
        )

        try:
            stdout, _ = self.proc.communicate()
        except Exception:
            if self.proc:
                self.proc.kill()
            raise

        if self._cancelled or self.proc.returncode not in (0, None):
            return np.array([], dtype=np.float32), samplerate

        raw = np.frombuffer(stdout, dtype=np.float32).copy()
        if raw.size > 0:
            raw /= np.max(np.abs(raw))
        return raw, samplerate

    def cancel(self):
        """Terminate ffmpeg process if running and mark as cancelled."""
        self._cancelled = True
        if self.proc and self.proc.poll() is None:  # still running
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class WaveformWorker(QThread):
    """
    Background worker to compute min/max buckets for a zoom level.
    Emits (zoom_key, (mins, maxs, samples_per_bucket)) when done.
    """
    finished = Signal(object, object)

    def __init__(self, samples, zoom_key, samples_per_bucket, parent=None):
        super().__init__(parent)
        self.samples = samples
        self.zoom_key = zoom_key
        self.samples_per_bucket = int(samples_per_bucket)

    def run(self):
        # Accept either int16 (the new compact storage) or float32 (legacy,
        # for caches saved before the dtype change). For int16 we compute
        # min/max in-place and normalize to float32 [-1, 1] just before
        # emit — that's all the display code expects. For ~172 M samples
        # (1 hour @ 48 kHz), the int16 path uses half the RAM of float32
        # during the bucket reshape.
        src = self.samples
        is_int16 = getattr(src, 'dtype', None) == np.int16
        if is_int16:
            arr = src  # no copy; min/max work on int16 directly
        else:
            arr = np.asarray(src, dtype=np.float32)
        length = (len(arr) // self.samples_per_bucket) * self.samples_per_bucket
        if length == 0:
            mins = np.array([], dtype=np.float32)
            maxs = np.array([], dtype=np.float32)
        else:
            arr = arr[:length]
            buckets = arr.reshape(-1, self.samples_per_bucket)
            mins = buckets.min(axis=1)
            maxs = buckets.max(axis=1)
            if is_int16:
                # Convert to float32 [-1, 1] for the display code. Scale
                # by 32768 (the int16 range) — anything that was clipped
                # at +/-1.0 before conversion stays at +/-1.0 after.
                mins = (mins.astype(np.float32) / 32768.0)
                maxs = (maxs.astype(np.float32) / 32768.0)
        self.finished.emit(self.zoom_key, (mins, maxs, self.samples_per_bucket))
    

class WaveformManager:
    def __init__(self, samples=None, filepath=None):
        self.samples = None
        self.filepath = filepath  # Store filepath for cache key
        self.levels = {}  # zoom_key -> (mins, maxs, samples_per_bucket)
        self.workers = {}  # zoom_key -> worker thread
        self.cache_dir = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform')
        self._cache_save_timer = None
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
        if samples is not None:
            self.set_samples(samples)


    def _cache_path(self):
        """Get the unified cache file path."""
        if not self.cache_dir or not self.filepath:
            return None
        cache_key = utils.get_cache_key(self.filepath)
        if not cache_key:
            return None
        return os.path.join(self.cache_dir, f"{cache_key}_waveform.npy")


    def _load_from_cache(self):
        """Load all cached data (samples + levels) from a single file."""
        cache_path = self._cache_path()
        if not cache_path or not os.path.exists(cache_path):
            return False

        try:
            data = np.load(cache_path, allow_pickle=True).item()
            samples = data.get('samples')
            # Migrate legacy float32 caches to int16 on read so we don't
            # pay double the RAM forever just because the cache was
            # written before the dtype change. The worker tolerates both
            # but ongoing storage costs half as much in int16.
            if samples is not None and samples.dtype != np.int16:
                samples_f = samples.astype(np.float32, copy=False)
                np.clip(samples_f, -1.0, 1.0, out=samples_f)
                samples = (samples_f * 32767.0).astype(np.int16)
                # Rewrite the cache in the new format on a future save.
                # _schedule_cache_save isn't invoked here on purpose — we
                # let the next worker_finished do it so we don't block
                # project-open on disk I/O.
            self.samples = samples
            self.levels = data.get('levels', {})
            return self.samples is not None
        except Exception:
            return False

    def _save_to_cache(self):
        """Save all data (samples + levels) to a single cache file."""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            cache_path = self._cache_path()
            if cache_path and self.samples is not None:
                data = {
                    'samples': self.samples,
                    'levels': self.levels
                }
                np.save(cache_path, data, allow_pickle=True)
        except Exception:
            pass  # Silently fail if cache write fails

    def set_samples(self, samples):
        # Compact storage: int16 instead of float32 cuts the RAM cost of
        # the raw waveform in half. For a 1-hour project at 48 kHz mono
        # that's 691 MB → 345 MB. The waveform is normalized to
        # [-1.0, 1.0] upstream (AudioLoaderThread divides by max abs), so
        # we can pack it into int16 by multiplying by 32767 and clipping
        # before cast. The worker handles both dtypes for backwards
        # compatibility with cached float32 samples on disk.
        arr = np.asarray(samples)
        if arr.dtype == np.int16:
            self.samples = arr
        else:
            arr_f = arr.astype(np.float32, copy=False)
            np.clip(arr_f, -1.0, 1.0, out=arr_f)
            self.samples = (arr_f * 32767.0).astype(np.int16)
        self._start_worker_if_missing(512)

    def _start_worker_if_missing(self, zoom_key):
        if zoom_key in self.levels or zoom_key in self.workers:
            return
        # No longer load individual levels from disk - all loaded at once via _load_from_cache
        samples_per_bucket = zoom_key
        worker = WaveformWorker(self.samples, zoom_key, samples_per_bucket)
        worker.finished.connect(self._on_worker_finished)
        self.workers[zoom_key] = worker
        worker.start()

    def _on_worker_finished(self, zoom_key, payload):
        mins, maxs, samples_per_bucket = payload
        mins = np.asarray(mins, dtype=np.float32)
        maxs = np.asarray(maxs, dtype=np.float32)
        self.levels[zoom_key] = (mins, maxs, samples_per_bucket)
        # Schedule a coalesced cache write rather than saving on every worker
        # finish — at high zoom many workers complete in quick succession and
        # each np.save() blocks the main thread with a large disk write,
        # which is what made Ctrl+wheel feel stuck.
        self._schedule_cache_save()
        if zoom_key in self.workers:
            del self.workers[zoom_key]

    def _schedule_cache_save(self):
        if self._cache_save_timer is None:
            self._cache_save_timer = QTimer()
            self._cache_save_timer.setSingleShot(True)
            self._cache_save_timer.timeout.connect(self._save_to_cache)
        self._cache_save_timer.start(2000)
            
    def get_level(self, samples_per_pixel, start_sample=0, end_sample=None):
        """
        Get the best available level for a target samples_per_pixel (int).
        - returns (mins_slice, maxs_slice, samples_per_bucket, available_zoom_key)
        If level is not yet ready, returns the best coarser level slice (fallback).
        Also triggers worker creation for exact level.
        """
        if self.samples is None:
            return ([], [], 1, None)

        target = max(1, int(samples_per_pixel))
        # choose a zoom_key that we store = samples_per_bucket
        # heuristic: pick power-of-two multiples
        candidate = target
        # normalize candidate to a reasonable bucket (power of two * base)
        # simple normalization: round to nearest 1,2,4,8,16... * 128
        base = 128
        factor = max(1, int(candidate // base) or 1)
        # find bucket as base * (lowest power of two >= factor)
        p = 1
        while p < factor:
            p <<= 1
        zoom_key = base * p

        # ensure at least base baseline
        if zoom_key < base:
            zoom_key = base

        # Trigger generation only for the normalised zoom_key. Earlier we
        # also spawned one for the exact `target`, but during a wheel-zoom
        # the target shifts every frame and the cost of starting a fresh
        # worker (plus the corresponding cache write) per frame stalls the
        # main thread. The normalised key is close enough visually.
        self._start_worker_if_missing(zoom_key)

        # choose best available level: prefer exact target, else nearest coarser (bigger samples_per_bucket)
        available_keys = sorted(self.levels.keys())
        if not available_keys:
            # nothing ready yet — return empty fallback
            return ([], [], 1, None)

        # find smallest key >= target (coarser or equal)
        chosen = None
        for k in available_keys:
            if k >= target:
                chosen = k
                break
        if not chosen:
            chosen = available_keys[-1]

        mins, maxs, spb = self.levels[chosen]
        # compute slices for start_sample:end_sample mapped to buckets
        if end_sample is None:
            end_sample = len(self.samples)
        start_bucket = start_sample // spb
        end_bucket = (end_sample // spb) + 1
        # clamp
        start_bucket = max(0, int(start_bucket))
        end_bucket = min(len(mins), int(end_bucket))
        mins_slice = mins[start_bucket:end_bucket]
        maxs_slice = maxs[start_bucket:end_bucket]
        return (mins_slice, maxs_slice, spb, chosen)

    def ensure_level(self, samples_per_pixel):
        self._start_worker_if_missing(int(max(1, samples_per_pixel)))


DUB_PEAKS_SAMPLERATE = 8000


class DubPeaksWorker(QThread):
    """Loads a small dub WAV via ffmpeg and computes min/max peaks + duration."""
    finished = Signal(str, object, object, float)  # path, mins, maxs, duration_sec

    def __init__(self, path, target_buckets=400, parent=None):
        super().__init__(parent)
        self.path = path
        self.target_buckets = int(target_buckets)
        self.proc = None

    def run(self):
        try:
            cmd = [
                session.FFMPEG_EXECUTABLE,
                "-v", "error",
                "-i", self.path,
                "-ac", "1",
                "-ar", str(DUB_PEAKS_SAMPLERATE),
                "-f", "f32le", "-",
            ]
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=session.STARTUPINFO,
            )
            stdout, _ = self.proc.communicate()
            if self.proc.returncode not in (0, None):
                return

            samples = np.frombuffer(stdout, dtype=np.float32).copy()
            if samples.size == 0:
                return

            duration = samples.size / float(DUB_PEAKS_SAMPLERATE)

            peak = float(np.max(np.abs(samples)))
            if peak > 0:
                samples /= peak

            spb = max(1, samples.size // self.target_buckets)
            length = (samples.size // spb) * spb
            buckets = samples[:length].reshape(-1, spb)
            mins = buckets.min(axis=1).astype(np.float32)
            maxs = buckets.max(axis=1).astype(np.float32)
            self.finished.emit(self.path, mins, maxs, duration)
        except Exception:
            pass


def _make_arrow_cursor(direction):
    """Build a QCursor with a single-direction arrow glyph. `direction` is
    'left' or 'right'."""
    size = 32
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(0, 0, 0, 220), 2))
    painter.setBrush(QColor(255, 255, 255, 240))
    path = QPainterPath()
    mid_y = size / 2
    if direction == 'left':
        # ◁—— : arrowhead on the left, shaft to the right
        path.moveTo(4, mid_y)
        path.lineTo(14, mid_y - 6)
        path.lineTo(14, mid_y - 2)
        path.lineTo(26, mid_y - 2)
        path.lineTo(26, mid_y + 2)
        path.lineTo(14, mid_y + 2)
        path.lineTo(14, mid_y + 6)
        path.closeSubpath()
    else:
        # ——▷ : arrowhead on the right, shaft to the left
        path.moveTo(size - 4, mid_y)
        path.lineTo(size - 14, mid_y - 6)
        path.lineTo(size - 14, mid_y - 2)
        path.lineTo(size - 26, mid_y - 2)
        path.lineTo(size - 26, mid_y + 2)
        path.lineTo(size - 14, mid_y + 2)
        path.lineTo(size - 14, mid_y + 6)
        path.closeSubpath()
    painter.drawPath(path)
    painter.end()
    hotspot_x = 4 if direction == 'left' else (size - 4)
    return QCursor(pixmap, hotspot_x, int(mid_y))


class Timeline(QWidget):
    seek = Signal(float)
    subtitle_clicked = Signal()    

    def __init__(widget, parent=None):
        super().__init__(parent)
        widget.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
        widget.setMouseTracking(True)
        widget.setObjectName('timeline_widget')
        widget.setAutoFillBackground(False)
        widget.setFocusPolicy(Qt.ClickFocus)
        widget.subtitle_is_clicked = False
        widget.subtitle_start_is_clicked = False
        widget.subtitle_end_is_clicked = False
        widget.subtitle_height = 50
        widget.subtitle_y = 55
        widget.offset = 0.0
        widget.show_limiters = False
        widget.show_tug_of_war = False
        widget.tug_of_war_pressed = False
        widget.is_cursor_pressing = False
        widget.is_smart_splicing = False
        widget.subtitle_under_the_cursor = False
        widget.show_speaker_color = session.CONFIG['timeline'].get('show_speaker_color', False)
        widget.show_speaker_tracks = session.CONFIG['timeline'].get('show_speaker_tracks', False)
        widget.width_proportion = widget.width() / session.VIDEO.get('duration', 0.01)
        widget.subtitle_alignment = {'left' : Qt.AlignLeft, 'center' : Qt.AlignCenter, 'right' : Qt.AlignRight}[session.CONFIG.get('default_values', {}).get('subtitle_alignment', 'left')]

        widget.audio_thread = AudioLoaderThread()
        widget.audio_thread.finished.connect(widget.on_waveform_loaded)

        widget.waveform_manager = WaveformManager(filepath=session.VIDEO.get("filepath"))
        widget.waveform_height = widget.height() - widget.subtitle_y - 10
        widget.waveform_y = 45

        widget.dub_peaks = {}     # path -> (mins, maxs, duration)
        widget.dub_workers = {}   # path -> DubPeaksWorker
        # Cache the constructed waveform QPainterPath per
        # (dub_path, rounded_width, rounded_height). Building one path is
        # ~800 Python→Qt lineTo() calls per dub; rebuilding 30 visible
        # dubs per paint at 10Hz dominates the audio thread (each paint
        # holds the GIL ~60ms). The path is geometry-only (relative to
        # 0,0), so we translate at draw time. Invalidated lazily — old
        # entries simply get evicted as the cache grows past _DUB_PATH_CACHE_MAX.
        widget._dub_waveform_path_cache = {}
        widget._DUB_PATH_CACHE_MAX = 512
        widget.dub_hovered_handle = None  # (subtitle_id, 'start' | 'end') or None
        widget.subtitle_edge_hovered = None  # (subtitle_id, 'start' | 'end') or None
        widget.dub_lock_hovered = None  # subtitle_id or None — clip-lock badge hover
        widget.dub_stretching = None  # dict {subtitle, dub, start_x, original_width, current_width}
        widget.dub_stretch_active = False  # True only during the initial drag
        widget.dub_start_is_clicked = False
        widget.empty_state_hover_x = None  # x in widget coords when hovering near waveform center, no segments
        widget.dragging_dub = None
        widget.dragging_dub_offset = 0.0

        # Per-segment dub editing state. `dub_segment_boundary_hovered` is
        # populated by mouseMoveEvent so paintEvent / mousePressEvent can
        # reuse the hit. `dub_segment_drag` holds the boundary being
        # dragged (set on mousePress, cleared on release).
        widget.dub_segment_boundary_hovered = None
        widget.dub_segment_drag = None

        widget._left_arrow_cursor = _make_arrow_cursor('left')
        widget._right_arrow_cursor = _make_arrow_cursor('right')

    def paintEvent(widget, event):
        if not widget.isVisible() or widget.width() <= 0 or widget.height() <= 0:
            return
        
        painter = QPainter(widget)
        scroll_position = widget.parent().parent().horizontalScrollBar().value()
        scroll_width = widget.parent().parent().width()

        painter.setRenderHint(QPainter.Antialiasing)

        grid_pen = QPen(QColor(session.CONFIG.get('timeline', {}).get('grid_color', '#336a7483')), 1, Qt.SolidLine)
        painter.setFont(QFont('Ubuntu Mono', 8))

        # Iterate only the visible range. Earlier we walked every second of
        # the video and skipped via an inline `if xpos >= scroll_position …`,
        # which made paint scale O(duration) — at zoom 490 on a 60-min video
        # that's 3,600 iterations per repaint per scroll tick, which is what
        # made Ctrl+wheel feel sluggish.
        duration = float(session.VIDEO.get('duration', 60))
        wpp = widget.width_proportion or 1.0
        visible_right = scroll_position + scroll_width

        # Region-targeted iteration. When the playhead-strip timer fires at
        # 30 Hz it calls `widget.update(QRect(...))` with a narrow strip
        # around the cursor — Qt's clip spares the rasterizer outside that
        # rect, but the *Python* loops (per-second grid, per-subtitle,
        # waveform buckets, …) would still walk the whole viewport and
        # hold the GIL away from the audio callback. Clip those loops to
        # the intersection of (viewport, event.rect()).
        #
        # event.rect() is in widget coordinates — same coord system as
        # scroll_position — so we intersect directly. Fallback to the full
        # viewport if the intersection is degenerate (defensive; Qt should
        # always give us a rect that overlaps the visible area).
        ev_rect = event.rect()
        iter_left = max(scroll_position, ev_rect.left())
        iter_right = min(visible_right, ev_rect.right() + 1)
        if iter_right <= iter_left:
            iter_left = scroll_position
            iter_right = visible_right

        sec_start = max(0, int(iter_left / wpp))
        sec_end = min(int(duration), int(iter_right / wpp) + 1)
        zoom = session.CONFIG.get('timeline_zoom', 1)
        timeline_cfg = session.CONFIG.get('timeline', {})
        show_grid = timeline_cfg.get('show_grid', False)
        grid_type = timeline_cfg.get('grid_type', False)
        text_color = QColor(timeline_cfg.get('time_text_color', '#806a7483'))

        for sec in range(sec_start, sec_end):
            xpos = sec * wpp
            if (zoom > 75) or (zoom > 50 and zoom <= 75 and not int((sec % 2))) or (zoom > 25 and zoom <= 50 and not int((sec % 4))) or (zoom <= 25 and not int((sec % 8))):
                lim_rect = QRectF(xpos + 3, 27, 50, 20)
                painter.setPen(text_color)
                painter.drawText(lim_rect, Qt.AlignLeft, utils.get_timeline_time_str(sec))

            if show_grid and grid_type == 'seconds':
                painter.setPen(grid_pen)
                painter.drawLine(xpos, 0, xpos, widget.height())

        if show_grid:
            if grid_type == 'frames':
                framerate = session.VIDEO.get('framerate') or 25
                pixels_per_frame = wpp / framerate
                # Skip if frames are sub-pixel — drawing them is wasted work.
                if pixels_per_frame >= 1.0:
                    painter.setPen(grid_pen)
                    # iter_left/iter_right narrows to the dirty strip — see
                    # the comment block at the top of paintEvent.
                    frame_start = max(0, int(iter_left / pixels_per_frame))
                    frame_end = int(iter_right / pixels_per_frame) + 1
                    h = widget.height()
                    for frame_i in range(frame_start, frame_end):
                        xpos = frame_i * pixels_per_frame
                        painter.drawLine(xpos, 0, xpos, h)
            elif grid_type == 'scenes' and session.VIDEO.get('scenes'):
                painter.setPen(grid_pen)
                h = widget.height()
                visible_start_sec = iter_left / wpp
                visible_end_sec = iter_right / wpp
                for scene in session.VIDEO['scenes']:
                    if scene < visible_start_sec or scene > visible_end_sec:
                        continue
                    xpos = scene * wpp
                    painter.drawLine(xpos, 0, xpos, h)

        if session.REPEAT_DURATION_BUFFER:
            rep_rect = QRectF(
                session.REPEAT_DURATION_BUFFER[0][0] * widget.width_proportion,
                0,
                (session.REPEAT_DURATION_BUFFER[0][1] - session.REPEAT_DURATION_BUFFER[0][0]) * widget.width_proportion,
                widget.height()
            )

            grad = QLinearGradient(0, 0, 0, 100)
            c1 = QColor(session.CONFIG.get('timeline', {}).get('cursor_color', '#ccff0000'))
            c1.setAlpha(80)
            grad.setColorAt(0, c1)
            c2 = QColor(session.CONFIG.get('timeline', {}).get('cursor_color', '#ccff0000'))
            c2.setAlpha(0)
            grad.setColorAt(1, c2)

            painter.fillRect(rep_rect, grad)

        if widget.waveform_manager.samples is not None:
            # iter_left/iter_right narrows the waveform bucket range to the
            # dirty strip; outside the strip Qt keeps the previously-painted
            # waveform from the backing store.
            visible_start_sec = iter_left / widget.width_proportion
            visible_end_sec = iter_right / widget.width_proportion
            sr = session.VIDEO.get('samplerate', 48000)
            start_sample = int(visible_start_sec * sr)
            end_sample = int(visible_end_sec * sr)
            samples_per_pixel = max(1, int(sr / widget.width_proportion))  # heuristic: how many samples map to one timeline pixel
            mins, maxs, spb, chosen = widget.waveform_manager.get_level(samples_per_pixel, start_sample, end_sample)
            if len(mins) > 0:
                center = widget.waveform_y + (widget.waveform_height * 0.5)
                scale = widget.waveform_height * 0.9 / 2.0  # scale factor to map normalized -1..1 to pixels
                buckets_count = len(mins)
                if buckets_count > 0:
                    pixel_per_bucket = (spb / sr) * widget.width_proportion
                    path = QPainterPath()
                    upper = []
                    x = start_sample_to_x = (start_sample / sr) * widget.width_proportion
                    for i in range(buckets_count):
                        val = float(maxs[i])
                        y = center - (val * scale)
                        upper.append((x, y))
                        x += pixel_per_bucket
                    lower = []
                    x = x - pixel_per_bucket  # last x
                    for i in range(buckets_count - 1, -1, -1):
                        val = float(mins[i])
                        y = center - (val * scale)
                        lower.append((x, y))
                        x -= pixel_per_bucket
                    if upper:
                        path.moveTo(upper[0][0], upper[0][1])
                        for (xx, yy) in upper[1:]:
                            path.lineTo(xx, yy)
                        for (xx, yy) in lower:
                            path.lineTo(xx, yy)
                        path.closeSubpath()
                        painter.setPen(QPen(QColor(session.CONFIG.get('timeline', {}).get('waveform_border_color', '#ff153450')), 1))
                        painter.setBrush(QColor(session.CONFIG.get('timeline', {}).get('waveform_fill_color', '#cc153450')))
                        painter.drawPath(path)

        if widget.show_speaker_tracks and widget.show_speaker_color:
            for i, speaker in enumerate(list(session.SPEAKERS.keys())):
                track_height = widget.subtitle_height / len(list(session.SPEAKERS.keys()))
                
                grad = QLinearGradient(0, widget.subtitle_y + (track_height*i), 0, widget.subtitle_y + (track_height*i) + track_height)

                c1 = QColor(session.SPEAKERS[speaker].get('color', '#b8cee0'))
                c1.setAlpha(0)
                grad.setColorAt(0, c1)
                c2 = QColor(session.SPEAKERS[speaker].get('color', '#b8cee0'))
                c2.setAlpha(15)
                grad.setColorAt(1, c2)

                track_rect = QRectF(
                    0,
                    widget.subtitle_y + (track_height*i),
                    widget.width(),
                    track_height
                )

                painter.fillRect(track_rect, grad)
                if session.SPEAKERS[speaker].get('image', None):
                    qimage = session.SPEAKERS[speaker]['image']
                    qimage_size = int(track_height * .3)
                    qimage = qimage.scaled(qimage_size, qimage_size)

                    x = scroll_position + 6
                    y = widget.subtitle_y + (track_height * (i+1)) - qimage_size + 6

                    painter.save()

                    path = QPainterPath()
                    path.addEllipse(x, y, qimage_size, qimage_size)
                    painter.setClipPath(path)

                    painter.drawImage(x, y, qimage)

                    painter.restore()


        if session.SUBTITLE['segments']:
            painter.setOpacity(1)
            painter.setFont(QFont('Montserrat', 10))
            painter.setPen(QColor(session.CONFIG.get('timeline', {}).get('time_text_color', '#304251')))

            ordered_segments = list(session.SUBTITLE['segments'])
            selected_subtitle = session.SUBTITLE.get('selected')
            if selected_subtitle in ordered_segments:
                ordered_segments.remove(selected_subtitle)
                ordered_segments.append(selected_subtitle)

            # Compare in seconds (cheap) instead of dividing each subtitle's
            # start/end by duration on every iteration. iter_left/iter_right
            # narrows to the dirty strip: at 30 Hz playhead repaints, this
            # is what keeps the per-subtitle loop cheap enough to stay
            # inside one audio block budget.
            visible_start_sec = iter_left / wpp
            visible_end_sec = iter_right / wpp
            speakers_keys = list(session.SPEAKERS.keys()) if widget.show_speaker_tracks and session.SPEAKERS else []
            speakers_index = {name: i for i, name in enumerate(speakers_keys)}
            speakers_count = len(speakers_keys)
            selected_fill = QColor(timeline_cfg.get('selected_subtitle_fill_color', '#cc3e5363'))
            unselected_fill = QColor(timeline_cfg.get('subtitle_fill_color', '#c8dbe9'))
            unselected_fill.setAlphaF(0.9)
            current_selected = session.SUBTITLE.get('selected', False)

            # ---- Per-paint invariants ----
            # Every subtitle in the loop was paying the cost of these dict
            # lookups + QColor allocations: with ~100 visible subtitles
            # this is ~600 dict-walks and ~6 QColor allocs per paint, on
            # the main thread, holding the GIL away from the audio
            # callback. Hoist once.
            dubbing_enabled = session.CONFIG.get('dubbing', {}).get('enabled', False)
            quality_check_enabled = session.CONFIG.get('quality_check', {}).get('enabled', False)
            translation_cfg = session.CONFIG.get('translation', {})
            translation_opts = translation_cfg.get('engine_options', {}) if isinstance(translation_cfg, dict) else {}
            show_translations = translation_opts.get('show_translations', False)
            translation_target_lang = translation_opts.get('target_language', 'en-us')
            dub_waveform_color = QColor(timeline_cfg.get('dub_waveform_color', '#ffffffff'))
            subtitle_border_color = QColor(timeline_cfg.get('subtitle_border_color', '#ff6a7483'))
            # Two text-color states (selected / unselected) for both the
            # normal and quality-check-failed cases.
            text_color_unselected = QColor(timeline_cfg.get('subtitle_text_color', '#304251'))
            text_color_selected = QColor(timeline_cfg.get('selected_subtitle_text_color', '#b8cee0'))
            qc_text_color_unselected = QColor(timeline_cfg.get('subtitle_text_color', '#ff304251'))
            qc_text_color_selected = QColor(timeline_cfg.get('selected_subtitle_text_color', '#ffffffff'))
            qc_failed_color = QColor('#9e1a1a')
            translation_separator_color = QPen(QColor(timeline_cfg.get('subtitle_text_color', '#40304251')), 1)
            smart_splice_line_color = QColor(timeline_cfg.get('subtitle_fill_color', '#ccb8cee0'))
            smart_splice_divider_color = QColor("#1a000000")
            subtitle_font = QFont('Montserrat', 10)

            for subtitle in ordered_segments:
                if subtitle['start'] > visible_end_sec:
                    continue
                if subtitle['end'] < visible_start_sec:
                    continue
                # Skip subtitles whose speaker is hidden via the eye toggle
                # in the speakers panel — keeps them in the model but stops
                # rendering them on the timeline.
                speaker_name = subtitle.get('speaker', 'A')
                speaker_data = session.SPEAKERS.get(speaker_name) or {}
                if speaker_data.get('hidden'):
                    continue
                speaker_color_str = speaker_data.get('color')

                painter.setPen(Qt.NoPen)
                if current_selected == subtitle:
                    painter.setBrush(selected_fill)
                else:
                    painter.setBrush(unselected_fill)

                subtitle_track = [0, 1]
                if speakers_count:
                    subtitle_track = [speakers_index.get(speaker_name, 0), speakers_count]

                subtitle_rect = QRectF(
                    subtitle['start'] * widget.width_proportion,
                    widget.subtitle_y + ((widget.subtitle_height / subtitle_track[1]) * subtitle_track[0]),
                    (subtitle['end'] - subtitle['start']) * widget.width_proportion,
                    widget.subtitle_height / subtitle_track[1]
                )

                subtitle_locked = subtitle.get('locked', False)
                full_subtitle_rect = QRectF(subtitle_rect)
                if subtitle_locked:
                    painter.save()
                    painter.setOpacity(0.18)

                painter.drawRoundedRect(subtitle_rect, 3.0, 3.0, Qt.AbsoluteSize)

                if subtitle.get('dubbing') and dubbing_enabled:
                    dub = subtitle['dubbing'][0]
                    dub_path = dub.get('path')
                    if dub_path:
                        widget._request_dub_peaks(dub_path)
                        peaks = widget.dub_peaks.get(dub_path)
                        if peaks is not None:
                            mins, maxs, duration = peaks
                            dub_start = dub.get('start', subtitle['start'])
                            dub_x = dub_start * widget.width_proportion
                            # Segment-aware width: silences inserted into the
                            # take stretch the band, trims shrink it. Falls
                            # back to source-file duration for legacy /
                            # not-yet-edited dubs.
                            from subtitld.modules import dub_clip as _dub_clip
                            total_dub_dur = _dub_clip.clip_total_duration(dub) or duration
                            dub_w = total_dub_dur * widget.width_proportion
                            if widget.dub_stretching is not None and widget.dub_stretching['subtitle'] is subtitle:
                                dub_w = widget.dub_stretching['current_width']
                            if dub_w > 1:
                                band_ratio = 0.25
                                dub_inset = QRectF(
                                    dub_x,
                                    subtitle_rect.top() + subtitle_rect.height() * (1.0 - band_ratio),
                                    dub_w,
                                    subtitle_rect.height() * band_ratio,
                                )
                                fill_color = QColor(speaker_color_str or '#1a73a8')
                                fill_color.setAlpha(204)
                                painter.save()
                                painter.setPen(Qt.NoPen)
                                painter.setBrush(fill_color)

                                subtitle_start_x = subtitle['start'] * widget.width_proportion
                                subtitle_end_x = subtitle['end'] * widget.width_proportion
                                bl_inside = subtitle_start_x <= dub_x <= subtitle_end_x
                                br_inside = subtitle_start_x <= (dub_x + dub_w) <= subtitle_end_x
                                r = 3.0
                                px = dub_inset.x()
                                py = dub_inset.y()
                                pw = dub_inset.width()
                                ph = dub_inset.height()
                                dub_path_shape = QPainterPath()
                                dub_path_shape.moveTo(px + r, py)
                                dub_path_shape.lineTo(px + pw - r, py)
                                dub_path_shape.arcTo(px + pw - 2 * r, py, 2 * r, 2 * r, 90, -90)
                                if br_inside:
                                    dub_path_shape.lineTo(px + pw, py + ph)
                                else:
                                    dub_path_shape.lineTo(px + pw, py + ph - r)
                                    dub_path_shape.arcTo(px + pw - 2 * r, py + ph - 2 * r, 2 * r, 2 * r, 0, -90)
                                if bl_inside:
                                    dub_path_shape.lineTo(px, py + ph)
                                else:
                                    dub_path_shape.lineTo(px + r, py + ph)
                                    dub_path_shape.arcTo(px, py + ph - 2 * r, 2 * r, 2 * r, 270, -90)
                                dub_path_shape.lineTo(px, py + r)
                                dub_path_shape.arcTo(px, py, 2 * r, 2 * r, 180, -90)
                                dub_path_shape.closeSubpath()
                                painter.drawPath(dub_path_shape)

                                # Render silence segments as a darker stripe
                                # over the dub band, plus a thin separator
                                # line at every internal segment boundary so
                                # split / gap edits are visible. Hovered
                                # boundary draws thicker so the user can see
                                # which one they'd grab.
                                segment_ranges = list(_dub_clip.iter_segment_ranges(dub))
                                if len(segment_ranges) > 1 or any(
                                        s.get('type') == 'silence' for _, _, s in segment_ranges):
                                    silence_brush = QColor(0, 0, 0, 110)
                                    hovered_boundary = widget.dub_segment_boundary_hovered
                                    hovered_idx = -1
                                    if hovered_boundary and hovered_boundary['dub'] is dub:
                                        hovered_idx = hovered_boundary['boundary_index']
                                    for seg_t0, seg_t1, seg in segment_ranges:
                                        if seg.get('type') != 'silence':
                                            continue
                                        sx0 = seg_t0 * widget.width_proportion
                                        sx1 = seg_t1 * widget.width_proportion
                                        sil_rect = QRectF(
                                            sx0, dub_inset.top(),
                                            max(0.0, sx1 - sx0), dub_inset.height(),
                                        )
                                        painter.save()
                                        painter.setPen(Qt.NoPen)
                                        painter.setBrush(silence_brush)
                                        painter.drawRect(sil_rect)
                                        painter.restore()
                                    for boundary_idx in range(len(segment_ranges) - 1):
                                        bx = segment_ranges[boundary_idx][1] * widget.width_proportion
                                        is_hovered = (boundary_idx == hovered_idx)
                                        line_color = QColor(255, 255, 255,
                                                            220 if is_hovered else 130)
                                        line_width = 2 if is_hovered else 1
                                        painter.setPen(QPen(line_color, line_width))
                                        painter.drawLine(int(bx), int(dub_inset.top()),
                                                         int(bx), int(dub_inset.bottom()))

                                # Lock badge — anchors the clip to the subtitle.
                                clip_locked = bool(dub.get('locked'))
                                lock_hovered = (widget.dub_lock_hovered == id(subtitle))
                                if clip_locked or lock_hovered:
                                    badge_h = 14.0
                                    badge_r = badge_h / 2.0
                                    if dub_x < subtitle_start_x:
                                        circle_x = dub_x
                                        extent_to = subtitle_start_x
                                    elif dub_x > subtitle_end_x:
                                        circle_x = subtitle_end_x
                                        extent_to = dub_x
                                    else:
                                        circle_x = dub_x
                                        extent_to = dub_x
                                    badge_left = min(circle_x, extent_to) - badge_r
                                    badge_right = max(circle_x, extent_to) + badge_r
                                    badge_cy = dub_inset.bottom()
                                    if clip_locked and lock_hovered:
                                        badge_alpha = 0.7
                                    elif clip_locked:
                                        badge_alpha = 1.0
                                    else:
                                        badge_alpha = 0.45
                                    painter.save()
                                    painter.setOpacity(badge_alpha)
                                    painter.setPen(Qt.NoPen)
                                    painter.setBrush(subtitle_border_color)
                                    badge_rect = QRectF(badge_left, badge_cy - badge_r, badge_right - badge_left, badge_h)
                                    painter.drawRoundedRect(badge_rect, badge_r, badge_r, Qt.AbsoluteSize)
                                    # Padlock glyph centered on circle_x
                                    painter.setPen(QPen(QColor(255, 255, 255, 230), 1.2))
                                    painter.setBrush(Qt.NoBrush)
                                    shackle = QRectF(circle_x - 2.4, badge_cy - 4.0, 4.8, 4.4)
                                    painter.drawArc(shackle, 0, 180 * 16)
                                    painter.setPen(Qt.NoPen)
                                    painter.setBrush(QColor(255, 255, 255, 230))
                                    body = QRectF(circle_x - 3.2, badge_cy - 0.5, 6.4, 5.0)
                                    painter.drawRoundedRect(body, 1.0, 1.0, Qt.AbsoluteSize)
                                    painter.restore()

                                if not subtitle_locked:
                                    subtitle_id = id(subtitle)
                                    hovered_edge = widget.dub_hovered_handle[1] if widget.dub_hovered_handle and widget.dub_hovered_handle[0] == subtitle_id else None
                                    bar_color = QColor(255, 255, 255, 255) if hovered_edge == 'end' else QColor(255, 255, 255, 180)

                                    if widget.dub_stretching is not None and widget.dub_stretching.get('subtitle') is subtitle:
                                        state = widget.dub_stretching
                                        stretch_ratio = (state['current_width'] / state['original_width']) if state.get('original_width', 0) > 0 else 1.0
                                    else:
                                        rate_value = dub.get('rate', 0) or 0
                                        stretch_ratio = 100.0 / (100.0 + rate_value) if (100 + rate_value) > 0 else 1.0

                                    bar_h = 10.0
                                    bar_top = dub_inset.top() + 3
                                    bar_bottom = bar_top + bar_h
                                    right_x = dub_inset.right() - 5
                                    mid_y = (bar_top + bar_bottom) / 2.0

                                    if abs(stretch_ratio - 1.0) < 0.02:
                                        right_x += 1
                                        left_x = right_x - 4
                                        left_offset = 0.0
                                        right_offset = 0.0
                                    elif stretch_ratio > 1.0:
                                        left_x = right_x - 4
                                        left_offset = -2.0
                                        right_offset = 2.0
                                    else:
                                        right_x += 1
                                        left_x = right_x - 7
                                        left_offset = 2.0
                                        right_offset = -2.0

                                    bar_pen = QPen(bar_color, 2)
                                    bar_pen.setCapStyle(Qt.RoundCap)
                                    bar_pen.setJoinStyle(Qt.RoundJoin)
                                    painter.setPen(bar_pen)
                                    painter.setBrush(Qt.NoBrush)
                                    bar_path = QPainterPath()
                                    bar_path.moveTo(right_x, bar_top)
                                    bar_path.lineTo(right_x + right_offset, mid_y)
                                    bar_path.lineTo(right_x, bar_bottom)
                                    bar_path.moveTo(left_x, bar_top)
                                    bar_path.lineTo(left_x + left_offset, mid_y)
                                    bar_path.lineTo(left_x, bar_bottom)
                                    painter.drawPath(bar_path)

                                    if abs(stretch_ratio - 1.0) > 0.02:
                                        text = f"{stretch_ratio:.3f}x"
                                        speed_font = QFont('Montserrat', 6)
                                        speed_font.setBold(True)
                                        painter.setFont(speed_font)
                                        fm = painter.fontMetrics()
                                        text_w = fm.horizontalAdvance(text)
                                        painter.setPen(QColor(255, 255, 255, 130))
                                        painter.drawText(QRectF(left_x - 4 - text_w, bar_top, text_w, bar_h), Qt.AlignLeft | Qt.AlignVCenter, text)

                                count = len(mins)
                                if count > 0:
                                    painter.setClipRect(dub_inset)
                                    # Build the waveform shape ONCE per
                                    # (dub_path, width, height), cached
                                    # relative to (0, vertical-center) and
                                    # translated at draw time. See cache
                                    # init in __init__.
                                    cache_w = round(dub_inset.width())
                                    cache_h = round(dub_inset.height())
                                    cache_key = (dub_path, cache_w, cache_h)
                                    wf = widget._dub_waveform_path_cache.get(cache_key)
                                    if wf is None:
                                        scale = cache_h * 0.45
                                        pixel_per_bucket = cache_w / count
                                        wf = QPainterPath()
                                        x0 = 0.0
                                        # Top edge, left → right.
                                        wf.moveTo(x0, -float(maxs[0]) * scale)
                                        x = x0 + pixel_per_bucket
                                        for i in range(1, count):
                                            wf.lineTo(x, -float(maxs[i]) * scale)
                                            x += pixel_per_bucket
                                        # Bottom edge, right → left.
                                        x -= pixel_per_bucket
                                        for i in range(count - 1, -1, -1):
                                            wf.lineTo(x, -float(mins[i]) * scale)
                                            x -= pixel_per_bucket
                                        wf.closeSubpath()
                                        # Crude eviction — drop arbitrary
                                        # entries so the cache can't grow
                                        # without bound on dub regen / zoom
                                        # changes.
                                        if len(widget._dub_waveform_path_cache) > widget._DUB_PATH_CACHE_MAX:
                                            for k in list(widget._dub_waveform_path_cache)[:128]:
                                                del widget._dub_waveform_path_cache[k]
                                        widget._dub_waveform_path_cache[cache_key] = wf
                                    center = dub_inset.center().y()
                                    painter.setPen(Qt.NoPen)
                                    painter.setBrush(dub_waveform_color)
                                    painter.translate(dub_inset.left(), center)
                                    painter.drawPath(wf)
                                    painter.translate(-dub_inset.left(), -center)
                                painter.restore()

                if widget.show_speaker_color and speaker_color_str:
                    sr = 3.0
                    strip_h = 3.0
                    sx = subtitle_rect.left()
                    sw = subtitle_rect.width()
                    sy = subtitle_rect.top()
                    strip = QPainterPath()
                    strip.moveTo(sx + sr, sy)
                    strip.lineTo(sx + sw - sr, sy)
                    strip.arcTo(sx + sw - 2 * sr, sy, 2 * sr, 2 * sr, 90, -90)
                    strip.lineTo(sx + sw, sy + strip_h)
                    strip.lineTo(sx, sy + strip_h)
                    strip.lineTo(sx, sy + sr)
                    strip.arcTo(sx, sy, 2 * sr, 2 * sr, 180, -90)
                    strip.closeSubpath()
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(speaker_color_str))
                    painter.drawPath(strip)

                subtitle_is_selected = current_selected == subtitle
                if quality_check_enabled:
                    approved, _qc_reasons, _qc_issues = quality_check.check_subtitle(subtitle)
                    if not approved:
                        painter.setPen(qc_failed_color)
                    elif subtitle_is_selected:
                        painter.setPen(qc_text_color_selected)
                    else:
                        painter.setPen(qc_text_color_unselected)
                else:
                    if subtitle_is_selected:
                        painter.setPen(text_color_selected)
                    else:
                        painter.setPen(text_color_unselected)

                if subtitle.get('dubbing'):
                    subtitle_rect.setHeight(subtitle_rect.height() * 0.75)

                subtitle_rect -= QMarginsF(26, 6, 26, 6)

                painter.setFont(subtitle_font)

                if show_translations:
                    original_subtitle_rect = subtitle_rect - QMarginsF(0, 0, 0, subtitle_rect.height()*.5)

                    if widget.is_smart_splicing and isinstance(widget.is_smart_splicing, dict) and 'position' in widget.is_smart_splicing and (subtitle_rect.x() < widget.is_smart_splicing.get('position', original_subtitle_rect.x() + (original_subtitle_rect.width() / 2)) < (subtitle_rect.x() + subtitle_rect.width())):
                        pos = widget.is_smart_splicing.get('position', original_subtitle_rect.x() + (original_subtitle_rect.width() / 2))
                        if 'left' in widget.is_smart_splicing and 'right' in widget.is_smart_splicing:
                            left_side = widget.is_smart_splicing['left']
                            right_side = widget.is_smart_splicing['right']
                            painter.drawText(original_subtitle_rect - QMarginsF(0, 0, (left_side[0] * original_subtitle_rect.width()) + 5, 0), Qt.AlignRight | Qt.AlignTop | Qt.TextWordWrap, left_side[1])
                            painter.drawText(original_subtitle_rect - QMarginsF((right_side[0] * original_subtitle_rect.width()) + 5, 0, 0, 0), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, right_side[1])
                            painter.setPen(smart_splice_divider_color)
                            painter.drawLine(original_subtitle_rect.x() + ((1 - left_side[0]) * original_subtitle_rect.width()), subtitle_rect.top(), original_subtitle_rect.x() + ((1 - left_side[0]) * original_subtitle_rect.width()), subtitle_rect.bottom())
                        if widget.is_smart_splicing['mode'] == 'split':
                            painter.setPen(smart_splice_line_color)
                            painter.drawLine(pos, subtitle_rect.top() - 6, pos, subtitle_rect.bottom() + 6)
                    else:
                        # painter.drawText(original_subtitle_rect, Qt.AlignLeft | Qt.TextWordWrap, subtitle['text'])
                        painter.drawText(original_subtitle_rect - QMarginsF(0, 5, 0, 5), widget.subtitle_alignment | Qt.TextWordWrap, subtitle['text'])

                    translated_subtitle_rect = subtitle_rect - QMarginsF(0, subtitle_rect.height()*.5, 0, 0)

                    if subtitle_is_selected:
                        painter.setPen(text_color_selected)
                    else:
                        painter.setPen(text_color_unselected)

                    painter.drawText(translated_subtitle_rect - QMarginsF(0, 5, 0, 5), widget.subtitle_alignment | Qt.TextWordWrap, subtitle.get('translations', {}).get(translation_target_lang, ''))

                    painter.setPen(translation_separator_color)
                    painter.setBrush(Qt.NoBrush)
                    painter.drawLine(translated_subtitle_rect.left(), translated_subtitle_rect.top(), translated_subtitle_rect.right(), translated_subtitle_rect.top())
                else:
                    original_subtitle_rect = subtitle_rect - QMarginsF(0, 5, 0, 5)
                    if widget.is_smart_splicing and isinstance(widget.is_smart_splicing, dict) and 'position' in widget.is_smart_splicing and (subtitle_rect.x() < widget.is_smart_splicing.get('position', original_subtitle_rect.x() + (original_subtitle_rect.width() / 2)) < (subtitle_rect.x() + subtitle_rect.width())):
                        pos = widget.is_smart_splicing.get('position', original_subtitle_rect.x() + (original_subtitle_rect.width() / 2))
                        if 'left' in widget.is_smart_splicing and 'right' in widget.is_smart_splicing:
                            left_side = widget.is_smart_splicing['left']
                            right_side = widget.is_smart_splicing['right']
                            painter.drawText(original_subtitle_rect - QMarginsF(0, 0, (left_side[0] * original_subtitle_rect.width()) + 5, 0), Qt.AlignRight | Qt.AlignTop | Qt.TextWordWrap, left_side[1])
                            painter.drawText(original_subtitle_rect - QMarginsF((right_side[0] * original_subtitle_rect.width()) + 5, 0, 0, 0), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, right_side[1])
                            painter.setPen(smart_splice_divider_color)
                            painter.drawLine(original_subtitle_rect.x() + ((1 - left_side[0]) * original_subtitle_rect.width()), subtitle_rect.top(), original_subtitle_rect.x() + ((1 - left_side[0]) * original_subtitle_rect.width()), subtitle_rect.bottom())
                        if widget.is_smart_splicing['mode'] == 'split':
                            painter.setPen(smart_splice_line_color)
                            painter.drawLine(pos, subtitle_rect.top() - 6, pos, subtitle_rect.bottom() + 6)
                    else:
                        painter.drawText(original_subtitle_rect, widget.subtitle_alignment | Qt.TextWordWrap, subtitle['text'])

                if subtitle == widget.subtitle_under_the_cursor and widget.show_limiters and ((subtitle['end'] - subtitle['start']) * widget.width_proportion) > 40:
                    track_height = widget.subtitle_height / subtitle_track[1]
                    limiter_height = track_height
                    limiter_top = widget.subtitle_y + (track_height * subtitle_track[0])

                    edge_hovered = widget.subtitle_edge_hovered
                    edge_hovered_side = edge_hovered[1] if edge_hovered and edge_hovered[0] == id(subtitle) else None

                    if edge_hovered_side in ('start', 'end'):
                        handle_w = 20.0
                        er = 3.0
                        ey = limiter_top
                        eh = limiter_height
                        if edge_hovered_side == 'end':
                            ex_outer = subtitle['end'] * widget.width_proportion
                            ex_inner = ex_outer - handle_w
                            left, right = ex_inner, ex_outer
                            edge_path = QPainterPath()
                            edge_path.moveTo(left, ey)
                            edge_path.lineTo(right - er, ey)
                            edge_path.arcTo(right - 2 * er, ey, 2 * er, 2 * er, 90, -90)
                            edge_path.lineTo(right, ey + eh - er)
                            edge_path.arcTo(right - 2 * er, ey + eh - 2 * er, 2 * er, 2 * er, 0, -90)
                            edge_path.lineTo(left, ey + eh)
                            grad = QLinearGradient(ex_outer, 0, ex_inner, 0)
                        else:
                            ex_outer = subtitle['start'] * widget.width_proportion
                            ex_inner = ex_outer + handle_w
                            left, right = ex_outer, ex_inner
                            edge_path = QPainterPath()
                            edge_path.moveTo(right, ey)
                            edge_path.lineTo(left + er, ey)
                            edge_path.arcTo(left, ey, 2 * er, 2 * er, 90, 90)
                            edge_path.lineTo(left, ey + eh - er)
                            edge_path.arcTo(left, ey + eh - 2 * er, 2 * er, 2 * er, 180, 90)
                            edge_path.lineTo(right, ey + eh)
                            grad = QLinearGradient(ex_outer, 0, ex_inner, 0)
                        grad.setColorAt(0, QColor(255, 255, 255, 255))
                        grad.setColorAt(1, QColor(255, 255, 255, 0))
                        edge_pen = QPen(QBrush(grad), 2)
                        painter.setPen(edge_pen)
                        painter.setBrush(Qt.NoBrush)
                        painter.drawPath(edge_path)

                if subtitle_locked:
                    painter.restore()
                    badge_size = 10.0
                    badge_margin = 4.0
                    badge_rect = QRectF(
                        full_subtitle_rect.right() - badge_size - badge_margin,
                        full_subtitle_rect.top() + badge_margin,
                        badge_size,
                        badge_size,
                    )
                    painter.save()
                    painter.setPen(QPen(QColor(255, 255, 255, 230), 1.2))
                    painter.setBrush(Qt.NoBrush)
                    shackle = QRectF(
                        badge_rect.center().x() - 2.2, badge_rect.top() + 1.6,
                        4.4, 4.0,
                    )
                    painter.drawArc(shackle, 0, 180 * 16)
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(255, 255, 255, 230))
                    body = QRectF(
                        badge_rect.center().x() - 3.0, badge_rect.top() + 4.2,
                        6.0, 4.6,
                    )
                    painter.drawRoundedRect(body, 1.0, 1.0, Qt.AbsoluteSize)
                    painter.restore()

            painter.setOpacity(1)

        if bool(widget.show_tug_of_war):
            tug_of_war_pen = QPen(QColor(session.CONFIG.get('timeline', {}).get('selected_subtitle_arrow_color', '#ff969696')), 4, Qt.SolidLine, Qt.RoundCap)
            painter.setPen(tug_of_war_pen)
            xpos = int(widget.show_tug_of_war * widget.width_proportion) - 4
            y_tug_pos = 0
            for _tug_i in range(6):
                painter.drawLine(xpos, int(widget.subtitle_y + 8 + y_tug_pos), int(xpos + 8), int(widget.subtitle_y + 8 + y_tug_pos))
                # painter.drawLine(xpos, widget.subtitle_y + 8 + y_tug_pos, xpos + 8, widget.subtitle_y + widget.subtitle_height - 8)
                y_tug_pos += (widget.subtitle_height - 8) / 6

        if session.SUBTITLE.get('position', 0) is not None:
            painter.setPen(QPen(QColor(session.CONFIG.get('timeline', {}).get('cursor_color', '#ccff0000')), 2, Qt.SolidLine))
            # Sub-pixel cursor position. Truncating to int (the old code
            # did `int(position * width_proportion)`) snaps the cursor
            # to the nearest pixel column every frame, which at 60 Hz
            # makes it visibly "step" instead of glide. QLineF with
            # antialiasing renders the 2-px-wide line at sub-pixel x
            # by distributing coverage across adjacent columns — so a
            # cursor at x=200.3 vs x=200.7 looks subtly different and
            # the eye reads the motion as continuous.
            cursor_pos_f = float(session.SUBTITLE.get('position', 0)) * widget.width_proportion
            cursor_pos = int(cursor_pos_f)  # kept for the badge geometry below
            painter.drawLine(QLineF(cursor_pos_f, 0.0, cursor_pos_f, float(widget.height())))

            if (session.REPEAT_DURATION_BUFFER and session.SUBTITLE.get('position', 0) > session.REPEAT_DURATION_BUFFER[0][0]) or (not session.CONFIG['playback_speed'] == 1.0):
                cfont = QFont('Ubuntu Mono', 10)
                cfont.setBold(True)

                text = ''
                if session.CONFIG['repeat_activated']:
                    text += f'⤺{len(session.REPEAT_DURATION_BUFFER)}'

                if not session.CONFIG['playback_speed'] == 1.0:
                    text += (' ' if text else '') + f"x{session.CONFIG['playback_speed']}"

                cfont_metr = QFontMetrics(cfont).horizontalAdvance(text)

                c_ind_color = QColor(session.CONFIG.get('timeline', {}).get('cursor_color', '#ccff0000'))
                c_ind_color.setAlpha(150)
                painter.setBrush(c_ind_color)
                painter.setPen(Qt.NoPen)

                path = QPainterPath()
                path.moveTo(cursor_pos, 25)
                path.lineTo(cursor_pos - cfont_metr - 12, 25)
                path.lineTo(cursor_pos - cfont_metr - 7, 47)
                path.lineTo(cursor_pos, 47)
                path.lineTo(cursor_pos, 25)
                painter.drawPath(path)

                painter.setFont(cfont)
                painter.setPen(QPen(QColor(session.CONFIG.get('timeline', {}).get('cursor_text_indicator', '#ffffffff'))))
                text_rect = path.boundingRect() + QMarginsF(5, 0, 5, 0)
                painter.drawText(text_rect, Qt.AlignCenter | Qt.TextWordWrap, text)

        # Off-screen cursor indicators (only when auto-scroll is disabled).
        if session.CONFIG.get('timeline', {}).get('scrolling', 'page') == 'none' and session.SUBTITLE.get('position', 0) is not None:
            cursor_pos = session.SUBTITLE.get('position', 0) * widget.width_proportion
            arrow_color = QColor(session.CONFIG.get('timeline', {}).get('cursor_color', '#ccff0000'))
            arrow_color.setAlpha(220)
            arrow_size = 6.0
            arrow_margin = 4.0
            arrow_y = 29.0
            painter.setPen(Qt.NoPen)
            painter.setBrush(arrow_color)
            if cursor_pos < scroll_position:
                ax = scroll_position + arrow_margin
                arrow = QPainterPath()
                arrow.moveTo(ax, arrow_y + arrow_size / 2)
                arrow.lineTo(ax + arrow_size, arrow_y)
                arrow.lineTo(ax + arrow_size, arrow_y + arrow_size)
                arrow.closeSubpath()
                painter.drawPath(arrow)
            elif cursor_pos > scroll_position + scroll_width:
                ax = scroll_position + scroll_width - arrow_margin - arrow_size
                arrow = QPainterPath()
                arrow.moveTo(ax + arrow_size, arrow_y + arrow_size / 2)
                arrow.lineTo(ax, arrow_y)
                arrow.lineTo(ax, arrow_y + arrow_size)
                arrow.closeSubpath()
                painter.drawPath(arrow)

        # Empty-state phantom subtitle: draw at the cursor position when the
        # project has no subtitles and the mouse is hovering near the waveform's
        # vertical center band (set by mouseMoveEvent).
        if widget.empty_state_hover_x is not None and not session.SUBTITLE.get('segments'):
            duration_default = float(session.CONFIG.get('default_new_subtitle_duration', 3.0) or 3.0)
            phantom_w = max(1.0, duration_default * widget.width_proportion)
            phantom_x = widget.empty_state_hover_x
            phantom_y = widget.subtitle_y
            phantom_h = widget.subtitle_height
            phantom_rect = QRectF(phantom_x, phantom_y, phantom_w, phantom_h)
            phantom_border = QColor(session.CONFIG.get('timeline', {}).get('subtitle_border_color', '#ff6a7483'))
            phantom_border.setAlphaF(0.35)
            painter.setPen(QPen(phantom_border, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(phantom_rect, 3.0, 3.0, Qt.AbsoluteSize)
            phantom_text_color = QColor(session.CONFIG.get('timeline', {}).get('subtitle_text_color', '#304251'))
            phantom_text_color.setAlphaF(0.85)
            painter.setPen(phantom_text_color)
            painter.setFont(QFont('Montserrat', 10))
            painter.drawText(phantom_rect - QMarginsF(8, 4, 8, 4), Qt.AlignCenter | Qt.TextWordWrap, _('timeline.empty_state_label'))

        painter.end()
        event.accept()

    def mousePressEvent(widget, event):
        # Right- (and middle-) clicks must NOT initiate any drag — they're
        # for the context menu. The release event for right-click is
        # swallowed by the menu, so any drag flag set here would stay
        # active and the user would see the clip following the cursor
        # after they dismiss the menu.
        if event.button() != Qt.LeftButton:
            event.ignore()
            return

        # Dub-segment boundary drag — must run BEFORE the dub-stretch /
        # dub-drag handlers so a boundary inside the band wins over the
        # full-clip handlers (the band lives inside the subtitle rect, so
        # otherwise the click would be consumed by drag-the-dub-start).
        boundary_hit = widget._dub_segment_boundary_at_position(event.pos())
        if boundary_hit is not None:
            from subtitld.modules import history
            history.history_append()
            widget.dub_segment_drag = {
                'subtitle': boundary_hit['subtitle'],
                'dub': boundary_hit['dub'],
                'boundary_index': boundary_hit['boundary_index'],
            }
            widget.is_cursor_pressing = True
            event.accept()
            return

        if widget.empty_state_hover_x is not None and not session.SUBTITLE.get('segments'):
            position = event.pos().x() / widget.width_proportion
            duration = float(session.CONFIG.get('default_new_subtitle_duration', 3.0) or 3.0)
            subtitles.add_subtitle(position=position, duration=duration)
            if session.SUBTITLE.get('segments'):
                session.SUBTITLE['selected'] = session.SUBTITLE['segments'][0]
            widget.empty_state_hover_x = None
            window = widget.window()
            left_panel.update(window)
            textedit = getattr(window, 'left_panel_subtitleslist_textedit', None)
            if textedit is not None:
                textedit.setFocus(Qt.MouseFocusReason)
            widget.update()
            session.set_unsaved()
            event.accept()
            return

        lock_hit = widget._dub_lock_at_position(event.pos())
        if lock_hit is not None:
            _, dub = lock_hit
            dub['locked'] = not dub.get('locked', False)
            session.set_unsaved()
            widget.update()
            event.accept()
            return

        dub_end_hit = widget._dub_end_handle_at_position(event.pos())
        if dub_end_hit is not None:
            parent_subtitle, dub = dub_end_hit
            if parent_subtitle.get('locked'):
                event.accept()
                return
            dub_path = dub.get('path')
            peaks = widget.dub_peaks.get(dub_path) if dub_path else None
            if peaks is not None:
                _, _, duration = peaks
            else:
                # Peaks worker may not have finished computing for a
                # freshly-regenerated dub; probe the file directly so the
                # stretch can start on the first press instead of being
                # silently consumed.
                duration = 0.0
                if dub_path:
                    try:
                        import soundfile as sf
                        with sf.SoundFile(dub_path, 'r') as f:
                            if f.samplerate > 0:
                                duration = f.frames / f.samplerate
                    except Exception:
                        pass
                if duration <= 0:
                    event.accept()
                    return
            original_width = duration * widget.width_proportion
            widget.dub_stretching = {
                'subtitle': parent_subtitle,
                'dub': dub,
                'start_x': event.pos().x(),
                'original_width': original_width,
                'current_width': original_width,
            }
            widget.dub_stretch_active = True
            widget.is_cursor_pressing = True
            event.accept()
            return

        dub_hit = widget._dub_hit_at_position(event.pos())
        if dub_hit is not None:
            parent_subtitle, dub = dub_hit
            if parent_subtitle.get('locked'):
                event.accept()
                return
            widget.dub_start_is_clicked = True
            widget.dragging_dub = dub
            widget.dragging_dub_offset = event.pos().x() - (dub.get('start', 0.0) * widget.width_proportion)
            widget.is_cursor_pressing = True
            event.accept()
            return

        scroll_position = widget.parent().parent().horizontalScrollBar().value()
        scroll_width = widget.parent().parent().width()

        cursor_is_out_of_view = bool(session.SUBTITLE.get('position', 0) * widget.width_proportion < widget.parent().parent().horizontalScrollBar().value() or session.SUBTITLE.get('position', 0) * widget.width_proportion > widget.parent().parent().width() + widget.parent().parent().horizontalScrollBar().value())
        
        cursor_time_position = event.pos().x() / widget.width_proportion
        widget.subtitle_under_the_cursor = subtitles.subtitle_under_current_position(position=cursor_time_position)

        widget.is_cursor_pressing = True
        session.SUBTITLE['selected'] = None

        for subtitle in session.SUBTITLE['segments']:
            if (subtitle['start'] / session.VIDEO.get('duration', 0.01)) > ((scroll_position + scroll_width) / widget.width()):
                break
            elif (subtitle['end']) / session.VIDEO.get('duration', 0.01) < (scroll_position / widget.width()):
                continue
            elif widget.subtitle_under_the_cursor:
                subtitle_track = [0, 1]
                if widget.show_speaker_tracks and session.SPEAKERS:
                    speakers_keys = list(session.SPEAKERS.keys())
                    speaker_name = widget.subtitle_under_the_cursor.get('speaker', 'A')
                    subtitle_track = [
                        speakers_keys.index(speaker_name) if speaker_name in speakers_keys else 0,
                        len(speakers_keys)
                    ]
                y = widget.subtitle_y + ((widget.subtitle_height / subtitle_track[1]) * subtitle_track[0])
                h = widget.subtitle_height / subtitle_track[1]
                widget.show_limiters = bool(y < event.pos().y() < (y + h)) and widget._dub_hit_at_position(event.pos()) is None

                if widget.show_limiters and subtitle == widget.subtitle_under_the_cursor:

                # if event.pos().y() > widget.subtitle_y and event.pos().y() < (widget.subtitle_height + widget.subtitle_y) and (((event.pos().x()) / widget.width_proportion) > subtitle['start'] and ((event.pos().x()) / widget.width_proportion) < (subtitle['end'])):
                    session.SUBTITLE['selected'] = subtitle
                    if subtitle.get('locked'):
                        # Locked subtitles can be selected but not dragged / resized.
                        break
                    if event.pos().x() / widget.width_proportion > (subtitle['end']) - (20 / widget.width_proportion):
                        widget.subtitle_end_is_clicked = True
                        widget.offset = ((session.SUBTITLE['selected']['end']) * widget.width_proportion) - event.pos().x()
                        widget.tug_of_war_pressed = widget.show_tug_of_war
                    else:
                        widget.offset = event.pos().x() - session.SUBTITLE['selected']['start'] * widget.width_proportion
                        if event.pos().x() / widget.width_proportion < subtitle['start'] + (20 / widget.width_proportion):
                            widget.subtitle_start_is_clicked = True
                            widget.tug_of_war_pressed = widget.show_tug_of_war
                        else:
                            widget.subtitle_is_clicked = True
                    break

        if (widget.subtitle_is_clicked or widget.subtitle_start_is_clicked or widget.subtitle_end_is_clicked):
            widget.subtitle_clicked.emit()
        else:
            session.SUBTITLE['position'] = (event.pos().x() / widget.width()) * session.VIDEO.get('duration', 60)
            if session.CONFIG['repeat_activated']:
                session.REPEAT_DURATION_BUFFER = []
            widget.seek.emit(session.SUBTITLE.get('position', 0))
            left_panel.update(widget.window())

        widget.update()

    def leaveEvent(widget, event):
        changed = False
        if widget.dub_hovered_handle is not None:
            widget.dub_hovered_handle = None
            changed = True
        if widget.subtitle_edge_hovered is not None:
            widget.subtitle_edge_hovered = None
            changed = True
        if widget.dub_lock_hovered is not None:
            widget.dub_lock_hovered = None
            changed = True
        if widget.empty_state_hover_x is not None:
            widget.empty_state_hover_x = None
            changed = True
        if changed:
            widget.update()
        event.accept()

    def mouseReleaseEvent(widget, event):
        if widget.dub_segment_drag is not None:
            widget.dub_segment_drag = None
            widget.is_cursor_pressing = False
            session.set_unsaved()
            widget._refresh_dub_after_edit()
            event.accept()
            return

        if widget.dub_stretch_active and widget.dub_stretching is not None:
            state = widget.dub_stretching
            widget.dub_stretch_active = False
            widget.is_cursor_pressing = False
            subtitle = state['subtitle']
            original_w = state['original_width']
            current_w = state['current_width']
            regenerated = False
            if original_w > 0 and current_w > 0 and abs(current_w - original_w) > 2.0:
                ratio = original_w / current_w
                # Route to the provider that produced the existing dub —
                # otherwise a Piper-generated dub would get re-rendered through
                # Edge TTS (cloud round-trip, wrong voice). Built-in EdgeTTS
                # does cloud re-synthesis; add-on TTS does host-side ffmpeg
                # stretch on the cached raw WAV — no engine re-run.
                dubs = subtitle.get('dubbing') or []
                engine_id = (dubs[0].get('engine') if dubs else None) or 'edge-tts'
                regenerated = False
                try:
                    from subtitld.modules.addons import get_manager
                    provider = get_manager().get(engine_id)
                except Exception:
                    provider = None
                if provider is not None and hasattr(provider, 'stretch'):
                    regenerated = provider.stretch(subtitle, ratio)
                else:
                    # Fallback for tests / pre-manager bootstrap.
                    from subtitld.interface.left_panel_dubbing import EdgeTTSEngine
                    regenerated = EdgeTTSEngine.stretch(subtitle, ratio)
            if not regenerated:
                widget.dub_stretching = None
            widget.update()
            event.accept()
            return

        if widget.dub_start_is_clicked:
            widget.dub_start_is_clicked = False
            widget.dragging_dub = None
            widget.is_cursor_pressing = False
            session.set_unsaved()
            widget.update()
            event.accept()
            return

        if (widget.subtitle_is_clicked or widget.subtitle_start_is_clicked or widget.subtitle_end_is_clicked):
            session.set_unsaved()
        widget.subtitle_is_clicked = False
        widget.subtitle_start_is_clicked = False
        widget.subtitle_end_is_clicked = False
        widget.is_cursor_pressing = False
        widget.tug_of_war_pressed = False
        subtitle_under_position = subtitles.subtitle_under_current_position(position=event.pos().x() / widget.width_proportion)
        if widget.is_smart_splicing and widget.is_smart_splicing['mode'] == 'words':
            widget.is_smart_splicing['boundaries'] = [
                subtitle_under_position['start'] * widget.width_proportion,
                subtitle_under_position['end'] * widget.width_proportion
            ]
            widget.is_smart_splicing['mode'] = 'split'
        elif widget.is_smart_splicing and widget.is_smart_splicing['mode'] == 'split':
            last_text = widget.is_smart_splicing['left'][1].strip()
            next_text = widget.is_smart_splicing['right'][1].strip()
            pos = widget.is_smart_splicing['position'] / widget.width_proportion
            session.SUBTITLE['selected'] = subtitles.slice_subtitle(selected_subtitle=subtitle_under_position, position=pos, next_text=next_text, last_text=last_text)
            left_panel.update(widget.window())
            widget.setFocus(Qt.TabFocusReason)
            session.set_unsaved()
            widget.is_smart_splicing = False
            playercontrols.slice_selected_subtitle_button_update(widget.window())        
        widget.update()
        if not subtitle_under_position:
            session.SUBTITLE['selected'] = None
            left_panel.update(widget.window())
        event.accept()

    def mouseMoveEvent(widget, event):
        # Dragging a dub-segment boundary live: convert mouse x to a
        # timeline position and ask dub_clip to apply the move. The helper
        # rejects negative-duration moves so we don't need to clamp here.
        if widget.dub_segment_drag is not None:
            from subtitld.modules import dub_clip
            new_t = max(0.0, event.pos().x() / widget.width_proportion)
            dub_clip.move_boundary(
                widget.dub_segment_drag['dub'],
                widget.dub_segment_drag['boundary_index'],
                new_t,
            )
            widget.update()
            event.accept()
            return

        # Hover detection for boundary handles — set the hovered state and
        # cursor when over an internal segment boundary, clear otherwise.
        # Only when no other drag is in progress, so the cursor doesn't
        # flicker between resize and the active drag's own shape.
        if not widget.dub_stretch_active and not widget.dub_start_is_clicked:
            boundary_hit = widget._dub_segment_boundary_at_position(event.pos())
            if boundary_hit != widget.dub_segment_boundary_hovered:
                widget.dub_segment_boundary_hovered = boundary_hit
                widget.update()
            if boundary_hit is not None:
                widget.setCursor(Qt.SizeHorCursor)
            elif widget.cursor().shape() == Qt.SizeHorCursor:
                widget.unsetCursor()

        if widget.dub_stretch_active and widget.dub_stretching is not None:
            state = widget.dub_stretching
            delta = event.pos().x() - state['start_x']
            new_w = max(10.0, state['original_width'] + delta)
            state['current_width'] = new_w
            widget.update()
            return

        if widget.dub_start_is_clicked and widget.dragging_dub is not None:
            new_start = (event.pos().x() - widget.dragging_dub_offset) / widget.width_proportion
            widget.dragging_dub['start'] = max(0.0, new_start)
            widget.update()
            return

        if not session.SUBTITLE.get('segments'):
            waveform_center_y = widget.waveform_y + (widget.waveform_height * 0.5)
            in_band = abs(event.pos().y() - waveform_center_y) <= 10
            new_x = event.pos().x() if in_band else None
            if new_x != widget.empty_state_hover_x:
                widget.empty_state_hover_x = new_x
                widget.update()
            if in_band:
                widget.setCursor(Qt.PointingHandCursor)
            else:
                widget.unsetCursor()
            return
        elif widget.empty_state_hover_x is not None:
            widget.empty_state_hover_x = None
            widget.update()

        if not widget.is_cursor_pressing:
            lock_hit = widget._dub_lock_at_position(event.pos())
            if lock_hit is not None:
                widget.setCursor(Qt.PointingHandCursor)
            elif widget._dub_end_handle_at_position(event.pos()) is not None:
                widget.setCursor(Qt.SplitHCursor)
            elif widget._dub_hit_at_position(event.pos()) is not None:
                widget.setCursor(Qt.SizeHorCursor)
            else:
                widget.unsetCursor()

            new_hover = None
            end_hit = widget._dub_end_handle_at_position(event.pos())
            if end_hit is not None and not end_hit[0].get('locked'):
                new_hover = (id(end_hit[0]), 'end')
            if new_hover != widget.dub_hovered_handle:
                widget.dub_hovered_handle = new_hover
                widget.update()

            new_lock_hover = id(lock_hit[0]) if lock_hit is not None else None
            if new_lock_hover != widget.dub_lock_hovered:
                widget.dub_lock_hovered = new_lock_hover
                widget.update()

        cursor_time_position = event.pos().x() / widget.width_proportion #(event.pos().x() / widget.width()) * session.VIDEO.get('duration', 60)
        cursor_tug_of_war_range = 10 / widget.width_proportion

        widget.subtitle_under_the_cursor = subtitles.subtitle_under_current_position(position=cursor_time_position)
        last, next = subtitles.get_adjacent_subtitles(position=cursor_time_position)
        
        if not widget.tug_of_war_pressed:
            widget.show_tug_of_war = False

        if widget.subtitle_under_the_cursor:
            subtitle_track = [0, 1]
            if widget.show_speaker_tracks and session.SPEAKERS:
                speakers_keys = list(session.SPEAKERS.keys())
                speaker_name = widget.subtitle_under_the_cursor.get('speaker', 'A')
                subtitle_track = [
                    speakers_keys.index(speaker_name) if speaker_name in speakers_keys else 0,
                    len(speakers_keys)
                ]
            y = widget.subtitle_y + ((widget.subtitle_height / subtitle_track[1]) * subtitle_track[0])
            h = widget.subtitle_height / subtitle_track[1]
            widget.show_limiters = bool(y < event.pos().y() < (y + h)) and widget._dub_hit_at_position(event.pos()) is None

            new_edge_hover = None
            if not widget.is_cursor_pressing and widget.show_limiters and not widget.subtitle_under_the_cursor.get('locked'):
                edge_range = 20 / widget.width_proportion if widget.width_proportion > 0 else 0
                near_start = cursor_time_position < widget.subtitle_under_the_cursor['start'] + edge_range
                near_end = cursor_time_position > widget.subtitle_under_the_cursor['end'] - edge_range
                if near_start:
                    widget.setCursor(widget._left_arrow_cursor)
                    new_edge_hover = (id(widget.subtitle_under_the_cursor), 'start')
                elif near_end:
                    widget.setCursor(widget._right_arrow_cursor)
                    new_edge_hover = (id(widget.subtitle_under_the_cursor), 'end')
                else:
                    widget.setCursor(Qt.SizeHorCursor)
            if new_edge_hover != widget.subtitle_edge_hovered:
                widget.subtitle_edge_hovered = new_edge_hover
                widget.update()

            if widget.show_limiters:
                if next and widget.subtitle_under_the_cursor['end'] - (cursor_tug_of_war_range * .5) < cursor_time_position < widget.subtitle_under_the_cursor['end'] + (cursor_tug_of_war_range*.5) and widget.subtitle_under_the_cursor['end'] + .001 > next['start'] - .02:
                    widget.show_tug_of_war = widget.subtitle_under_the_cursor['end'] + .0005
            
                if last and widget.subtitle_under_the_cursor['start'] - (cursor_tug_of_war_range*.5) < cursor_time_position < widget.subtitle_under_the_cursor['start'] + (cursor_tug_of_war_range*.5) and widget.subtitle_under_the_cursor['start'] - .001 < last['end'] + .02:
                    widget.show_tug_of_war = widget.subtitle_under_the_cursor['start'] - .0005

                if widget.is_smart_splicing:
                    cursor_position_in_subtitle = (event.pos().x() - (widget.subtitle_under_the_cursor['start'] * widget.width_proportion))
                    subtitle_width = ((widget.subtitle_under_the_cursor['end'] - widget.subtitle_under_the_cursor['start']) * widget.width_proportion)
                    
                    number_of_characters = len(widget.subtitle_under_the_cursor['text'].replace(' ', ''))
                    if isinstance(widget.is_smart_splicing, dict) and widget.is_smart_splicing['mode'] == 'words':
                        character_width = subtitle_width / number_of_characters
                        left_words = ''
                        right_words = ''
                        for word in widget.subtitle_under_the_cursor['text'].split():
                            if len((left_words + word).replace(' ', '')) * character_width > cursor_position_in_subtitle:
                                break
                            left_words += ' ' + word
                
                        right_words = widget.subtitle_under_the_cursor['text'][len(left_words):]
                        proportion = cursor_position_in_subtitle / subtitle_width
                        widget.is_smart_splicing = {
                            'mode': 'words',
                            'position': event.pos().x(),
                            'left': [1 - proportion, left_words],
                            'right': [proportion, right_words]
                        }
                    elif isinstance(widget.is_smart_splicing, dict) and widget.is_smart_splicing['mode'] == 'split' and (widget.is_smart_splicing['boundaries'][0] <= event.pos().x() <= widget.is_smart_splicing['boundaries'][1]):
                        widget.is_smart_splicing['position'] = event.pos().x()

        if session.SUBTITLE.get('selected', None) is not None:
            i = session.SUBTITLE['segments'].index(session.SUBTITLE['selected'])
            last = session.SUBTITLE['segments'][session.SUBTITLE['segments'].index(session.SUBTITLE['selected']) - 1] if session.SUBTITLE['segments'].index(session.SUBTITLE['selected']) > 0 else {'start': 0, 'end': 0, 'text': ''}
            nextsub = session.SUBTITLE['segments'][session.SUBTITLE['segments'].index(session.SUBTITLE['selected']) + 1] if session.SUBTITLE['segments'].index(session.SUBTITLE['selected']) < len(session.SUBTITLE['segments']) - 1 else {'start': session.VIDEO.get('duration', 60), 'end': 0, 'text': ''}
            scenes_list = session.VIDEO['scenes'] if len(session.VIDEO['scenes']) > 1 else [0.0]
            scenes_list.append(session.VIDEO.get('duration', 60))
            start_position = (event.pos().x() - widget.offset) / widget.width_proportion
            last_scene = scenes_list[bisect(scenes_list, start_position) - 1]
            next_scene = scenes_list[bisect(scenes_list, start_position)]

            if widget.subtitle_start_is_clicked:
                end = session.SUBTITLE['selected']['end']
                if not start_position > (end - session.CONFIG.get('default_values', {}).get('minimum_subtitle_width', 1)):
                    if not (bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed) and round(last['end'] + .001, 3) == round(session.SUBTITLE['selected']['start'], 3)) and session.CONFIG.get('timeline', {}).get('snap', True) and session.CONFIG.get('timeline', {}).get('snap_limits', True) and (last['end'] + session.CONFIG.get('timeline', {}).get('snap_value', .1)) > start_position:
                        subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=last['end'] + 0.001, move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                    elif session.CONFIG.get('timeline', {}).get('snap', True) and session.CONFIG.get('timeline', {}).get('snap_grid', False):
                        if session.CONFIG.get('timeline', {}).get('grid_type', False) == 'frames':
                            difference = start_position % (1.0 / session.VIDEO['framerate'])
                            subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], amount=difference, move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG.get('timeline', {}).get('grid_type', False) == 'seconds' and float(start_position) > float(float(int(start_position) + 1) - float(session.CONFIG.get('timeline', {}).get('snap_value', .1))):
                            subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=float(int(start_position) + 1), move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG.get('timeline', {}).get('grid_type', False) == 'seconds' and float(start_position) < float(float(int(start_position)) + float(session.CONFIG.get('timeline', {}).get('snap_value', .1))):
                            subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=float(int(start_position)), move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG.get('timeline', {}).get('grid_type', False) == 'scenes' and start_position > next_scene - session.CONFIG.get('timeline', {}).get('snap_value', .1):
                            subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=next_scene, move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG.get('timeline', {}).get('grid_type', False) == 'scenes' and start_position < last_scene + session.CONFIG.get('timeline', {}).get('snap_value', .1):
                            subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=last_scene, move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        else:
                            subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=start_position, move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                    else:
                        subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=start_position, move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                if widget.tug_of_war_pressed:
                    widget.show_tug_of_war = session.SUBTITLE['selected']['start']
            elif widget.subtitle_end_is_clicked:
                end_position = (event.pos().x() + widget.offset) / widget.width_proportion
                if not end_position < (session.SUBTITLE['selected']['start'] + session.CONFIG.get('default_values', {}).get('minimum_subtitle_width', 1)):
                    if not (bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed) and round(end_position, 3) >= round(nextsub['start'] - 0.001, 3)) and session.CONFIG.get('timeline', {}).get('snap', True) and session.CONFIG.get('timeline', {}).get('snap_limits', True) and (nextsub['start'] - session.CONFIG.get('timeline', {}).get('snap_value', .1)) < end_position:
                        subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=(nextsub['start'] - 0.001), move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                    elif session.CONFIG.get('timeline', {}).get('snap', True) and session.CONFIG.get('timeline', {}).get('snap_grid', False):
                        if session.CONFIG.get('timeline', {}).get('grid_type', False) == 'frames':
                            difference = end_position % (1.0 / session.VIDEO['framerate'])
                            subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], amount=difference, move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG.get('timeline', {}).get('grid_type', False) == 'seconds' and float(end_position) > float(float(int(end_position) + 1) - float(session.CONFIG.get('timeline', {}).get('snap_value', .1))):
                            subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=float(int(end_position) + 1), move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG.get('timeline', {}).get('grid_type', False) == 'seconds' and float(end_position) < float(float(int(end_position)) + float(session.CONFIG.get('timeline', {}).get('snap_value', .1))):
                            subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=float(int(end_position)), move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG.get('timeline', {}).get('grid_type', False) == 'scenes' and end_position > next_scene - session.CONFIG.get('timeline', {}).get('snap_value', .1):
                            subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=next_scene, move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG.get('timeline', {}).get('grid_type', False) == 'scenes' and end_position < last_scene + session.CONFIG.get('timeline', {}).get('snap_value', .1):
                            subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=last_scene, move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        else:
                            subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=end_position, move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                    else:
                        subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=end_position, move_nereast=bool(session.CONFIG.get('timeline', {}).get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                if widget.tug_of_war_pressed:
                    widget.show_tug_of_war = session.SUBTITLE['selected']['end']
            elif widget.subtitle_is_clicked:
                if session.CONFIG.get('timeline', {}).get('snap', True) and session.CONFIG.get('timeline', {}).get('snap_moving', True) and ((nextsub['start'] - session.CONFIG.get('timeline', {}).get('snap_value', .1)) < (start_position + (session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start'])) or (last['end'] + session.CONFIG.get('timeline', {}).get('snap_value', .1)) > start_position):
                    if (nextsub['start'] - session.CONFIG.get('timeline', {}).get('snap_value', .1)) < (start_position + (session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start'])):
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=nextsub['start'] - (session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) - 0.001)
                    else:
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=last['end'] + 0.001)
                elif session.CONFIG.get('timeline', {}).get('snap', True) and session.CONFIG.get('timeline', {}).get('snap_grid', False):
                    if session.CONFIG.get('timeline', {}).get('grid_type', False) == 'frames':
                        difference = start_position % (1.0 / session.VIDEO['framerate'])
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], amount=difference)
                    elif session.CONFIG.get('timeline', {}).get('grid_type', False) == 'seconds' and float(start_position) > float(float(int(start_position) + 1) - float(session.CONFIG.get('timeline', {}).get('snap_value', .1))):
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=float(int(start_position) + 1))
                    elif session.CONFIG.get('timeline', {}).get('grid_type', False) == 'seconds' and float(start_position) < float(float(int(start_position)) + float(session.CONFIG.get('timeline', {}).get('snap_value', .1))):
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=float(int(start_position)))
                    elif session.CONFIG.get('timeline', {}).get('grid_type', False) == 'scenes' and start_position > next_scene - session.CONFIG.get('timeline', {}).get('snap_value', .1):
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=next_scene)
                    elif session.CONFIG.get('timeline', {}).get('grid_type', False) == 'scenes' and start_position < last_scene + session.CONFIG.get('timeline', {}).get('snap_value', .1):
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=last_scene)
                    else:
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=start_position)
                else:
                    subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=start_position)


        if widget.is_cursor_pressing and not (widget.subtitle_start_is_clicked or widget.subtitle_end_is_clicked or widget.subtitle_is_clicked):
            session.SUBTITLE['position'] = (event.pos().x() / widget.width()) * session.VIDEO.get('duration', 60)
            widget.seek.emit(session.SUBTITLE.get('position', 0))

        if widget.window().preview_panel_player.is_paused():
            widget.update()

    def mouseDoubleClickEvent(widget, event):
        widget.update()
        event.accept()

    def resizeEvent(widget, event):
        """Function to call when timeline is resized"""
        widget.width_proportion = widget.width() / session.VIDEO.get('duration', 0.01)
        widget.subtitle_height = widget.height() - 80
        widget.waveform_height = widget.height() - widget.subtitle_y - 10
        event.accept()

    def update_size(widget):
        widget.setGeometry(0, 0, int(session.VIDEO.get('duration', 0.01) * session.CONFIG.get('timeline_zoom', 1)), widget.parent().parent().height())

    def load_waveform(widget):
        filepath = session.VIDEO.get("filepath")
        if not filepath:
            return
        
        # Update waveform manager filepath for cache key
        widget.waveform_manager.filepath = filepath
        
        # Try to load everything from cache (samples + levels)
        if widget.waveform_manager._load_from_cache():
            # Cache hit - start worker for base zoom level if needed
            widget.waveform_manager._start_worker_if_missing(512)
            QTimer.singleShot(100, lambda: widget.update())
            return
        
        # Cache miss - load audio from file
        widget.audio_thread.filepath = filepath
        widget.audio_thread.start()
    
    def on_waveform_loaded(widget, samples, samplerate):
        session.VIDEO["samplerate"] = samplerate
        widget.waveform_manager.set_samples(samples)
        # Save samples to cache (levels will be saved as workers complete)
        widget.waveform_manager._save_to_cache()
        QTimer.singleShot(1000, lambda: widget.update())

    def _dub_hit_at_position(widget, pos, handle_only=False, edge=None):
        """Return (subtitle, dub) if pos is over a dub clip.
        If `edge='start'`, restrict to the leftmost 8px. If `edge='end'`, restrict
        to the rightmost 8px. `handle_only=True` is an alias for edge='start'."""
        if handle_only and edge is None:
            edge = 'start'
        band_ratio = 0.25
        speakers = list(session.SPEAKERS.keys()) if widget.show_speaker_tracks and session.SPEAKERS else []

        for subtitle in session.SUBTITLE['segments']:
            if not subtitle.get('dubbing'):
                continue
            dub = subtitle['dubbing'][0]
            dub_path = dub.get('path')
            if not dub_path:
                continue
            peaks = widget.dub_peaks.get(dub_path)
            if peaks is None:
                continue
            _, _, duration = peaks

            dub_x = dub.get('start', subtitle['start']) * widget.width_proportion
            dub_w = duration * widget.width_proportion

            if edge == 'start':
                hit_left = dub_x
                hit_right = dub_x + 8
            elif edge == 'end':
                hit_left = dub_x + dub_w - 12
                hit_right = dub_x + dub_w
            else:
                hit_left = dub_x
                hit_right = dub_x + dub_w

            if speakers:
                speaker_name = subtitle.get('speaker', 'A')
                track_index = speakers.index(speaker_name) if speaker_name in speakers else 0
                track_count = len(speakers)
            else:
                track_index, track_count = 0, 1
            bar_y = widget.subtitle_y + ((widget.subtitle_height / track_count) * track_index)
            bar_h = widget.subtitle_height / track_count
            top = bar_y + bar_h * (1.0 - band_ratio)
            bottom = bar_y + bar_h - 2

            # Right-edge stretch handle only occupies the top 1/3 of the clip
            # height; the bottom 2/3 of that slice falls through to the body.
            if edge == 'end':
                bottom = top + (bottom - top) / 3.0

            if hit_left <= pos.x() <= hit_right and top <= pos.y() <= bottom:
                return (subtitle, dub)

        return None

    def _dub_handle_at_position(widget, pos):
        return widget._dub_hit_at_position(pos, edge='start')

    def _dub_end_handle_at_position(widget, pos):
        return widget._dub_hit_at_position(pos, edge='end')

    def _dub_lock_badge_rect(widget, subtitle):
        """Return the QRectF that the lock badge occupies for `subtitle`,
        or None if no first dub or peaks not yet loaded."""
        dubs = subtitle.get('dubbing') or []
        if not dubs:
            return None
        dub = dubs[0]
        dub_path = dub.get('path')
        if not dub_path:
            return None
        peaks = widget.dub_peaks.get(dub_path)
        if peaks is None:
            return None
        _, _, duration = peaks
        dub_x = dub.get('start', subtitle['start']) * widget.width_proportion
        dub_w = duration * widget.width_proportion
        if widget.dub_stretching is not None and widget.dub_stretching['subtitle'] is subtitle:
            dub_w = widget.dub_stretching['current_width']

        speakers = list(session.SPEAKERS.keys()) if widget.show_speaker_tracks and session.SPEAKERS else []
        if speakers:
            speaker_name = subtitle.get('speaker', 'A')
            track_index = speakers.index(speaker_name) if speaker_name in speakers else 0
            track_count = len(speakers)
        else:
            track_index, track_count = 0, 1
        bar_y = widget.subtitle_y + ((widget.subtitle_height / track_count) * track_index)
        bar_h = widget.subtitle_height / track_count
        dub_inset_bottom = bar_y + bar_h

        subtitle_start_x = subtitle['start'] * widget.width_proportion
        subtitle_end_x = subtitle['end'] * widget.width_proportion
        if dub_x < subtitle_start_x:
            circle_x = dub_x
            extent_to = subtitle_start_x
        elif dub_x > subtitle_end_x:
            circle_x = subtitle_end_x
            extent_to = dub_x
        else:
            circle_x = dub_x
            extent_to = dub_x
        badge_h = 14.0
        badge_r = badge_h / 2.0
        badge_left = min(circle_x, extent_to) - badge_r
        badge_right = max(circle_x, extent_to) + badge_r
        return QRectF(badge_left, dub_inset_bottom - badge_r, badge_right - badge_left, badge_h)

    def _dub_lock_at_position(widget, pos):
        """Return (subtitle, dub) when pos is inside a dub's lock-badge area."""
        for subtitle in session.SUBTITLE['segments']:
            rect = widget._dub_lock_badge_rect(subtitle)
            if rect is not None and rect.contains(pos):
                return (subtitle, subtitle['dubbing'][0])
        return None

    def _dub_band_rect_for(widget, subtitle):
        """Return (dub, dub_band_rect) for `subtitle`'s first dub take, or
        None when there's no dub or its width is 0. Geometry mirrors the
        paint code so hit-testing stays in sync."""
        from subtitld.modules import dub_clip
        dubs = subtitle.get('dubbing') or []
        if not dubs:
            return None
        dub = dubs[0]
        total_dur = dub_clip.clip_total_duration(dub)
        if total_dur <= 0:
            return None
        speakers = list(session.SPEAKERS.keys()) if widget.show_speaker_tracks and session.SPEAKERS else []
        if speakers:
            speaker_name = subtitle.get('speaker', 'A')
            track_index = speakers.index(speaker_name) if speaker_name in speakers else 0
            track_count = len(speakers)
        else:
            track_index, track_count = 0, 1
        track_h = widget.subtitle_height / track_count
        sub_top = widget.subtitle_y + track_h * track_index
        band_ratio = 0.25
        band_top = sub_top + track_h * (1.0 - band_ratio)
        band_bottom = sub_top + track_h
        dub_x = float(dub.get('start', subtitle['start'])) * widget.width_proportion
        dub_w = total_dur * widget.width_proportion
        return (dub, QRectF(dub_x, band_top, dub_w, band_bottom - band_top))

    def _dub_segment_at_position(widget, pos):
        """Find which dub segment is under `pos`. Returns a dict
        {subtitle, dub, segment_index, segment_t0, segment_t1, segment,
        time_at_x} or None."""
        from subtitld.modules import dub_clip
        if widget.width_proportion <= 0:
            return None
        time_at_x = pos.x() / widget.width_proportion
        for subtitle in session.SUBTITLE.get('segments', []) or []:
            info = widget._dub_band_rect_for(subtitle)
            if info is None:
                continue
            dub, rect = info
            if not rect.contains(QPointF(pos.x(), pos.y())):
                continue
            for idx, (t0, t1, seg) in enumerate(dub_clip.iter_segment_ranges(dub)):
                if t0 <= time_at_x < t1:
                    return {
                        'subtitle': subtitle, 'dub': dub,
                        'segment_index': idx, 'segment_t0': t0,
                        'segment_t1': t1, 'segment': seg,
                        'time_at_x': time_at_x,
                    }
        return None

    def _dub_segment_boundary_at_position(widget, pos, threshold_px=4):
        """Find an internal segment boundary near `pos`. Returns a dict
        {subtitle, dub, boundary_index, boundary_time} or None."""
        from subtitld.modules import dub_clip
        if widget.width_proportion <= 0:
            return None
        for subtitle in session.SUBTITLE.get('segments', []) or []:
            info = widget._dub_band_rect_for(subtitle)
            if info is None:
                continue
            dub, rect = info
            # Allow some Y slack so the boundary stays grabbable even at
            # the band's edges where the cursor crosses sub-pixel.
            if not (rect.top() - 4 <= pos.y() <= rect.bottom() + 4):
                continue
            ranges = list(dub_clip.iter_segment_ranges(dub))
            for idx in range(len(ranges) - 1):
                boundary_time = ranges[idx][1]
                if abs(boundary_time * widget.width_proportion - pos.x()) <= threshold_px:
                    return {
                        'subtitle': subtitle, 'dub': dub,
                        'boundary_index': idx, 'boundary_time': boundary_time,
                    }
        return None

    def _refresh_dub_after_edit(widget):
        """After a dub-segment edit, push the new state to the audio engine
        and repaint the timeline."""
        widget.update()
        window = widget.window()
        preview = getattr(window, 'preview_panel_player', None)
        if preview is None:
            return
        device = getattr(preview, '_audio_device', None)
        if device is not None and hasattr(device, 'sync_subtitle_dubs'):
            device.sync_subtitle_dubs(session.SUBTITLE.get('segments', []) or [])

    def contextMenuEvent(widget, event):
        from PySide6.QtWidgets import QMenu
        from subtitld.modules import dub_clip, history
        hit = widget._dub_segment_at_position(event.pos())
        if hit is None:
            event.ignore()
            return
        seg_type = hit['segment'].get('type', 'audio')
        menu = QMenu(widget)

        if seg_type == 'audio':
            act_split = menu.addAction(_('timeline.dub.split_here'))
            act_gap = menu.addAction(_('timeline.dub.insert_gap_here'))

            def do_split():
                history.history_append()
                offset = hit['time_at_x'] - hit['segment_t0']
                if dub_clip.split_audio_segment(hit['dub'], hit['segment_index'], offset):
                    session.set_unsaved()
                    widget._refresh_dub_after_edit()

            def do_gap():
                history.history_append()
                offset = hit['time_at_x'] - hit['segment_t0']
                seg_dur = hit['segment_t1'] - hit['segment_t0']
                inserted_at = hit['segment_index']
                # If the click landed inside an audio segment (not on its
                # outer edge), split first so the gap goes exactly at the
                # cursor instead of after the whole segment.
                if 0 < offset < seg_dur:
                    dub_clip.split_audio_segment(hit['dub'], hit['segment_index'], offset)
                dub_clip.insert_silence_after(hit['dub'], inserted_at, 0.5)
                session.set_unsaved()
                widget._refresh_dub_after_edit()

            act_split.triggered.connect(do_split)
            act_gap.triggered.connect(do_gap)

        # Removing is allowed for any segment as long as more than one
        # exists — removing the only segment would leave an empty take.
        if len(list(dub_clip.iter_segment_ranges(hit['dub']))) > 1:
            act_remove = menu.addAction(_('timeline.dub.remove_segment'))

            def do_remove():
                history.history_append()
                if dub_clip.remove_segment(hit['dub'], hit['segment_index']):
                    session.set_unsaved()
                    widget._refresh_dub_after_edit()

            act_remove.triggered.connect(do_remove)

        menu.exec(event.globalPos())
        event.accept()

    def _dub_cache_path(widget, path):
        cache_dir = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform')
        os.makedirs(cache_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(path))[0]
        return os.path.join(cache_dir, f'dub_{stem}_peaks.npy')

    def _request_dub_peaks(widget, path):
        if path in widget.dub_peaks or path in widget.dub_workers:
            return
        cache_file = widget._dub_cache_path(path)
        if cache_file and os.path.exists(cache_file):
            try:
                data = np.load(cache_file, allow_pickle=True).item()
                if 'duration' in data:
                    widget.dub_peaks[path] = (data['mins'], data['maxs'], float(data['duration']))
                    return
            except Exception:
                pass
        if not os.path.exists(path):
            return
        worker = DubPeaksWorker(path)
        worker.finished.connect(widget._on_dub_peaks_ready)
        widget.dub_workers[path] = worker
        worker.start()

    def _on_dub_peaks_ready(widget, path, mins, maxs, duration):
        widget.dub_peaks[path] = (mins, maxs, duration)
        widget.dub_workers.pop(path, None)
        cache_file = widget._dub_cache_path(path)
        if cache_file:
            try:
                np.save(cache_file, {'mins': mins, 'maxs': maxs, 'duration': duration}, allow_pickle=True)
            except Exception:
                pass
        widget.update()


def update_subtitles_panel_subtitle_selected(self):
    self.subtitles_panel_qlistwidget.update_content()
    left_panel.update(self)

def load(self):
    self.timeline_scroll = TimelineScroll()

    self.timeline_widget = Timeline()
    self.timeline_widget.setObjectName('timeline_widget')
    self.timeline_widget.seek.connect(lambda pos: self.preview_panel_player.seek(pos))
    self.timeline_widget.subtitle_clicked.connect(lambda: update_subtitles_panel_subtitle_selected(self))

    # Disabled: this used to fire `self.timeline_widget.update()` per
    # QMediaPlayer position update (~30Hz). With ~hundreds of dub clips
    # each paint takes 30-60ms, holding the GIL and starving the audio
    # thread. The throttled `_timeline_repaint_timer` in playercontrols
    # already drives the periodic repaint at 4Hz; we don't need a second
    # connection here.
    # self.preview_panel_player.position_changed_signal.connect(lambda: self.timeline_widget.update())

    self.timeline_scroll.setWidget(self.timeline_widget)


def update_scrollbar(self, position=0):
    """Function to update scrollbar of timeline"""
    current_position_in_timeline_widget = (session.SUBTITLE.get('position', 0) / session.VIDEO.get('duration', 0.01)) * self.timeline_widget.width()
    offset = 0

    if position == 'middle':
        if (self.timeline_widget.width() - (self.timeline_scroll.width() * .5)) > current_position_in_timeline_widget > self.timeline_scroll.width() * .5:
            offset = self.timeline_scroll.width() * .5
    elif isinstance(position, float):
        offset = self.timeline_scroll.width() * position
    elif isinstance(position, int):
        offset = position

    self.timeline_scroll.horizontalScrollBar().setValue(int(current_position_in_timeline_widget - offset))


def update(self):
    """Function to update timeline"""
    if session.CONFIG['repeat_activated']:
        if not session.REPEAT_DURATION_BUFFER:
            session.REPEAT_DURATION_BUFFER = [[session.SUBTITLE.get('position', 0), session.SUBTITLE.get('position', 0) + session.CONFIG['playback_repeat_duration']] for i in range(session.CONFIG['playback_repeat_times'])]
        else:
            last_pos = session.REPEAT_DURATION_BUFFER[0][1]
            if session.SUBTITLE.get('position', 0) > last_pos:
                self.preview_panel_player.set_position(session.REPEAT_DURATION_BUFFER[0][0])
                # self.timeline_widget.seek.emit(session.SUBTITLE.get('position', 0))
                del session.REPEAT_DURATION_BUFFER[0]
                if not len(session.REPEAT_DURATION_BUFFER):
                    for i in range(session.CONFIG['playback_repeat_times']):
                        session.REPEAT_DURATION_BUFFER.append([last_pos, last_pos + session.CONFIG['playback_repeat_duration']])

    if not self.preview_panel_player.is_paused():
        current_position_in_timeline_widget = (session.SUBTITLE.get('position', 0) * (self.timeline_widget.width() / session.VIDEO.get('duration', 0.01)))
        if session.CONFIG.get('timeline', {}).get('scrolling', 'page') == 'follow':
            update_scrollbar(self, position='middle')
        elif session.CONFIG.get('timeline', {}).get('scrolling', 'page') == 'page' and current_position_in_timeline_widget > self.timeline_scroll.width() + self.timeline_scroll.horizontalScrollBar().value():
            update_scrollbar(self)

    self.timeline_widget.update()


def load_audio_for_timeline(filepath: str, samplerate: int = 48000):
    """
    Uses ffmpeg to decode audio into mono float32 PCM for waveform visualization.
    Requires ffmpeg installed in PATH.
    """
    cmd = [
        session.FFMPEG_EXECUTABLE, "-v", "error", "-i", filepath,
        "-ac", "1",             # mono
        "-ar", str(samplerate), # resample rate
        "-f", "f32le", "-"      # raw 32-bit float to stdout
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
        startupinfo=session.STARTUPINFO,
    )
    raw = np.frombuffer(proc.stdout, dtype=np.float32).copy()
    # Normalize
    if raw.size > 0:
        raw /= np.max(np.abs(raw))
    return raw, samplerate
