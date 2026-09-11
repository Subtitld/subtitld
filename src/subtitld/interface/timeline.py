import hashlib
import os
import time
from bisect import bisect
import numpy as np
import subprocess

from PySide6.QtWidgets import QWidget, QScrollArea, QSizePolicy
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath, QLinearGradient, QRadialGradient, QFontMetrics, QPixmap, QCursor, QBrush
from PySide6.QtCore import Qt, QRect, QRectF, QPointF, QLineF, QThread, Signal, QMarginsF, QTimer, QMargins

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
                # Pivot the zoom on the MOUSE cursor: the timeline point under
                # the pointer stays fixed on screen while everything scales
                # around it. `event.position().x()` is in TimelineScroll
                # viewport coords — the same space _apply_zoom_geometry works
                # in. Captured now (before the zoom applies) and consumed
                # there; cleared by the zoom buttons so those keep pivoting
                # on the playhead.
                try:
                    widget.window()._zoom_mouse_pivot_x = float(event.position().x())
                except Exception:
                    widget.window()._zoom_mouse_pivot_x = None
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

# Onset detection runs at a lower rate than the waveform-display
# pipeline — 16 kHz is plenty for the spectral-flux bands that fire on
# plosives/sibilants, and halves the FFT work vs. 48 kHz. Used by both
# the main-audio onset thread and the per-dub worker.
ONSET_DETECTION_SAMPLERATE = 16000


class OnsetDetectionThread(QThread):
    """Runs the spectral-flux onset detector on an already-decoded
    sample buffer (the main video's mono audio that the waveform pass
    just produced). Emits a numpy array of onset times in seconds."""
    finished = Signal(object)  # np.ndarray[float32]

    def __init__(self, samples, samplerate, parent=None):
        super().__init__(parent)
        self.samples = samples
        self.samplerate = int(samplerate)
        self._cancelled = False

    def run(self):
        try:
            from subtitld.modules.onset_detection import detect_onsets
            samples = self.samples
            # Downsample to ONSET_DETECTION_SAMPLERATE if needed — cheap
            # decimation (no anti-alias filter) is fine for the
            # broadband-burst detector; we don't care about precise
            # spectral content above the new Nyquist.
            if (samples is not None and self.samplerate > ONSET_DETECTION_SAMPLERATE
                    and self.samplerate % ONSET_DETECTION_SAMPLERATE == 0):
                step = self.samplerate // ONSET_DETECTION_SAMPLERATE
                samples = samples[::step]
                sr = ONSET_DETECTION_SAMPLERATE
            else:
                sr = self.samplerate
            onsets = detect_onsets(samples, sr)
            if not self._cancelled:
                self.finished.emit(onsets)
        except Exception:
            pass

    def cancel(self):
        self._cancelled = True


def _detect_onsets_via_ffmpeg(path):
    """Decode `path` to mono float32 PCM at ONSET_DETECTION_SAMPLERATE
    via ffmpeg, then run the detector. Returns onset times (seconds) or
    None on any failure. Shared by DubOnsetsWorker (per-dub) and
    BackgroundOnsetsFromFileWorker (vocals track)."""
    cmd = [
        session.FFMPEG_EXECUTABLE,
        "-v", "error",
        "-i", path,
        "-ac", "1",
        "-ar", str(ONSET_DETECTION_SAMPLERATE),
        "-f", "f32le", "-",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        startupinfo=session.STARTUPINFO,
    )
    stdout, _ = proc.communicate()
    if proc.returncode not in (0, None):
        return None
    samples = np.frombuffer(stdout, dtype=np.float32).copy()
    if samples.size == 0:
        return None
    from subtitld.modules.onset_detection import detect_onsets
    return detect_onsets(samples, ONSET_DETECTION_SAMPLERATE)


class DubOnsetsWorker(QThread):
    """Per-dub-file onset extractor. Decodes the dub WAV via ffmpeg at
    ONSET_DETECTION_SAMPLERATE, runs the detector, emits onset times in
    seconds (relative to the dub file)."""
    finished = Signal(str, object)  # path, np.ndarray[float32]

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            onsets = _detect_onsets_via_ffmpeg(self.path)
            if onsets is not None:
                self.finished.emit(self.path, onsets)
        except Exception:
            pass


class BackgroundOnsetsFromFileWorker(QThread):
    """Background-pass onset extractor that reads from a file path —
    used when the vocals-separated track is available (preferred over
    the full mix, since music transients otherwise crowd out speech
    plosives in the spectral-flux output)."""
    finished = Signal(object)  # np.ndarray[float32]

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            onsets = _detect_onsets_via_ffmpeg(self.path)
            if onsets is not None:
                self.finished.emit(onsets)
        except Exception:
            pass


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
        # Wall-clock timestamp (perf_counter seconds) of the last
        # drag-induced seek + full-widget repaint emitted from
        # mouseMoveEvent. While playback is running we throttle that
        # work to ~30 Hz: each mouse move would otherwise emit a
        # QMediaPlayer setPosition() + a SoundDeviceAudioEngine.seek()
        # AND schedule a full-widget paint, all on the main thread.
        # Mouse moves fire at the display refresh rate (60-120 Hz on
        # most Linux setups), so during a drag-while-playing the GIL
        # stays held long enough for the audio callback to miss its
        # window — playback hangs/stutters. Throttling halves the
        # work; the 60 Hz playhead-strip timer in playercontrols still
        # provides the smooth cursor animation, so the user doesn't
        # perceive the drag itself as laggy.
        widget._drag_throttle_last_t = 0.0
        # Same rationale as the drag throttle, but for bare hover: the
        # per-move hover pipeline (subtitle-under-cursor scan, adjacent
        # lookup, several per-subclip/handle hit-tests) is O(number of
        # subtitles) and runs on EVERY raw mouse-move. Capped to ~60 Hz
        # while playing so it can't pin the GIL and starve the mixer.
        widget._hover_throttle_last_t = 0.0
        # Global toggle (playercontrols button, next to "show speaker tracks"):
        # when on, every subtitle's alternate dub takes are drawn as waveform
        # clips stacked below its main dub clip (see _paint_dub_take_rows).
        # Rects for those rows are collected each paint for click hit-testing:
        # click a take to audition it (solo), or its ▲ to make it the default.
        widget.show_dub_takes = session.CONFIG['timeline'].get('show_dub_takes', False)
        widget._dub_take_rects = []
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
        # In-progress record take, owned by RecordController and painted as an
        # overlay. None whenever no take is running. See modules/live_peaks.
        widget.live_take = None
        widget.dub_workers = {}   # path -> DubPeaksWorker

        # Onset markers (visual aid for dub sync — see
        # `subtitld.modules.onset_detection`). `background_onsets` is
        # the array of onset times (seconds) detected in the main video
        # audio; `dub_onsets` mirrors `dub_peaks` per-file. Both can be
        # None / missing while their workers run — paint just skips.
        widget.background_onsets = None  # np.ndarray[float32] | None
        widget.background_onset_thread = None
        widget.dub_onsets = {}    # path -> np.ndarray[float32]
        widget.dub_onset_workers = {}  # path -> DubOnsetsWorker
        # A single selected dub-clip onset mark (plosive). Clicking a mark
        # selects it (replacing any prior selection — only one at a time).
        # When a mark is selected and the user stretches that clip, the
        # stretch pivots around the mark's timeline position instead of
        # anchoring at the clip's left edge (see _apply_subclip_stretch).
        #   {'subtitle', 'seg_path', 'source_time'} | None
        # `source_time` is the onset time in the dub file's own seconds,
        # which survives re-render (it scales with the file, tracked in
        # _apply_subclip_stretch), unlike a timeline x that shifts on any
        # edit. The subtitle ref lets us find the clip on redraw.
        widget.selected_onset = None
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

        # Per-subclip dub editing state.
        #   dub_subclip_hovered = {dub, segment_index} | None
        #     — set by mouseMoveEvent when over a subclip body, so paint
        #       can brighten it as a drag affordance.
        #   dub_subclip_edge_hovered = {dub, segment_index, side} | None
        #     — set when over a subclip's left/right edge. `side` is
        #       'left' or 'right'.
        #   dub_subclip_stretch_hovered = {dub, segment_index} | None
        #     — set when over a subclip's stretch handle (bars icon at
        #       top-right). Drives the brighter bar color in paint and
        #       the SizeHorCursor shape so the user can tell stretch
        #       from trim.
        #   dub_subclip_drag = {dub, segment_index, mode, ...} | None
        #     — set on mousePress when starting an action, cleared on
        #       release. `mode` is 'move' / 'trim_left' / 'trim_right' /
        #       'stretch'.
        widget.dub_subclip_hovered = None
        widget.dub_subclip_edge_hovered = None
        widget.dub_subclip_stretch_hovered = None
        widget.dub_subclip_drag = None

        widget._left_arrow_cursor = _make_arrow_cursor('left')
        widget._right_arrow_cursor = _make_arrow_cursor('right')

        # When the USFX Phase 2 extractor lands a dub WAV, swap our
        # hatched placeholder for the real waveform. `update()` re-runs
        # paintEvent, which re-calls `_request_dub_peaks(path)` for each
        # visible dub — the file is now on disk, so the peaks worker
        # spawns and the dub band fills in once it's done. Explicit
        # QueuedConnection because the signal fires from the extractor
        # worker thread; widgets must mutate from the main thread.
        try:
            from subtitld.modules.signals import SIGNALS as _SESSION_SIGNALS
            _SESSION_SIGNALS.usfx_member_ready.connect(
                widget._on_usfx_member_ready, Qt.QueuedConnection,
            )
        except Exception:
            pass

    def _on_usfx_member_ready(widget, arcname, _target_path):
        """Slot: a deferred USFX member finished extracting. For dub
        WAVs, request a repaint so the matching subtitle's hatched
        placeholder is replaced by the real waveform. For non-dub
        members (waveform.npy, FLAC stems), no-op — those are handled
        elsewhere (waveform manager / audio-separation paths)."""
        if arcname.startswith('assets/dubs/'):
            widget.update()

    def paintEvent(widget, event):
        # Guarantee painter.end() even if the (large) paint body raises. A
        # QPainter left un-ended stays bound to the widget — the traceback that
        # carries the failed frame keeps the painter alive — so the NEXT
        # paintEvent's QPainter(widget) fails with "A paint device can only be
        # painted by one painter at a time" and the console floods with
        # "Painter not active". try/finally makes a single bad frame
        # recoverable instead of permanently breaking the timeline, and the
        # except surfaces the real cause (de-duplicated so it can't flood).
        if not widget.isVisible() or widget.width() <= 0 or widget.height() <= 0:
            return
        painter = QPainter(widget)
        try:
            widget._paint_body(painter, event)
        except Exception as _paint_exc:
            msg = repr(_paint_exc)
            if getattr(widget, '_last_paint_error', None) != msg:
                widget._last_paint_error = msg
                import traceback, sys
                traceback.print_exc(file=sys.stderr)
        finally:
            painter.end()
        event.accept()

    def _paint_body(widget, painter, event):
        # Live record take (painted after the segment loop). Hoisted OUT of the
        # `if segments:` block below: a new project has no segments but a
        # transcript-mode take still has to draw its provisional cue.
        _live = widget.live_take
        _live_sub = _live.get('subtitle') if _live else None
        _live_target_rect = None

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

        # Onset markers (consonant-onset visual aid — see
        # `subtitld.modules.onset_detection`). Drawn AFTER the main
        # waveform so they read against it, but BEFORE the speaker
        # tracks / subtitles / dubs so the subtitle rect covers them.
        #
        # Suppressed during playback. The markers are a precision
        # alignment aid (pause + nudge dubs against plosives) — they
        # have no informational value while the playhead is moving.
        # Drawing them costs one QPointF allocation per visible
        # background onset + one QLineF per visible dub-onset; with
        # 60 s of speech in view that's ~600 + ~600 PyObject
        # allocations per paintEvent, all on the main thread. At 30 Hz
        # playhead refresh + ad-hoc scroll repaints, the GIL is held
        # long enough that the sounddevice callback misses its window
        # and audio cuts. Honoring the toggle the moment playback
        # stops feels instant to the user; the cost is zero when paused.
        _is_playing = False
        try:
            _is_playing = not widget.window().preview_panel_player.is_paused()
        except Exception:
            pass
        show_onset_markers = (
            not _is_playing
            and bool(session.CONFIG.get('timeline', {}).get('show_onset_markers', False))
        )
        if (show_onset_markers
                and widget.background_onsets is not None
                and len(widget.background_onsets) > 0
                and widget.width_proportion > 0):
            try:
                onsets = widget.background_onsets
                wpp = widget.width_proportion
                pm = widget._onset_marker_pixmap()
                pm_w = pm.width()
                # Cull to dirty rect. Each marker tail can leak `pm_w`
                # px to the right of its line, so widen the left search
                # to include onsets whose tail crosses into view.
                t_left = (iter_left - pm_w) / wpp
                t_right = iter_right / wpp
                lo = int(np.searchsorted(onsets, t_left, side='left'))
                hi = int(np.searchsorted(onsets, t_right, side='right'))
                for i in range(lo, hi):
                    painter.drawPixmap(QPointF(float(onsets[i]) * wpp, 0.0), pm)
            except Exception:
                # Never let onset overlay take down the paint event —
                # log once and continue so the rest of the timeline
                # still renders.
                import traceback, sys
                traceback.print_exc(file=sys.stderr)

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

            # Use the SCROLL VIEWPORT range here, NOT the dirty-strip
            # range. A subtitle's dub band can be drawn wider than the
            # subtitle's own [start, end] bounds — subclips with
            # negative offsets extend before the subtitle, stretched /
            # trimmed subclips after. If we filtered by the strip, a
            # paint event whose dirty rect is JUST the playhead strip
            # would skip every subtitle that doesn't overlap the strip
            # by its own bounds — and the off-strip pixels of any dub
            # band crossing the strip would never get refreshed,
            # producing the "split / vanishing left half" glitch the
            # user reported during playback. Qt still clips the actual
            # painting to the strip, so we just spend a few extra
            # checks iterating visible subtitles — cheap.
            visible_start_sec = scroll_position / wpp
            visible_end_sec = visible_right / wpp
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

            # Rects of the "≡" alternate-takes badges, rebuilt each paint and
            # hit-tested in mousePressEvent.
            widget._dub_take_rects = []
            # Collected during the loop, painted in a second pass AFTER it so
            # the take rows sit on top of every subtitle's content.
            _dub_takes_to_paint = []

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

                # Playlist takes: when the global toggle is on and this subtitle
                # has more than one dub take, queue its takes to be drawn as
                # waveform clips stacked below the main dub (second pass, so they
                # sit on top of neighbouring clips' waveforms).
                _dubs_list = subtitle.get('dubbing') or []
                if widget.show_dub_takes and dubbing_enabled and len(_dubs_list) > 1:
                    _dub_takes_to_paint.append(
                        (QRectF(subtitle_rect), subtitle, list(_dubs_list), speaker_color_str))

                if subtitle.get('dubbing') and dubbing_enabled:
                    dub = subtitle['dubbing'][0]
                    dub_path = dub.get('path')
                    if dub_path:
                        widget._request_dub_peaks(dub_path)
                        peaks = widget.dub_peaks.get(dub_path)
                        if peaks is None and not os.path.exists(dub_path):
                            # The dub's source WAV isn't on disk yet —
                            # almost certainly the USFX Phase 2 extractor
                            # is still streaming `assets/dubs/*` from the
                            # zip. Paint a hatched gray band at the
                            # subtitle's width as a "loading" indicator;
                            # when `_on_usfx_member_ready` fires we'll
                            # re-paint and `_request_dub_peaks` will
                            # spawn the real peaks worker. Falling back
                            # to the subtitle's own duration (rather than
                            # any dub-recorded width) is intentional: we
                            # have no way to know the real dub duration
                            # without reading the file, so the placeholder
                            # always sits flush under the subtitle.
                            band_ratio = 0.25
                            placeholder_x = subtitle['start'] * widget.width_proportion
                            placeholder_w = max(
                                0.0,
                                (subtitle['end'] - subtitle['start']) * widget.width_proportion,
                            )
                            if placeholder_w > 1:
                                placeholder_rect = QRectF(
                                    placeholder_x,
                                    subtitle_rect.top() + subtitle_rect.height() * (1.0 - band_ratio),
                                    placeholder_w,
                                    subtitle_rect.height() * band_ratio,
                                )
                                painter.save()
                                painter.setPen(Qt.NoPen)
                                # BDiagPattern is the standard Qt
                                # "candy-striped, not ready" look. The
                                # color is a desaturated speaker color so
                                # rows still read as belonging to the
                                # right speaker — just clearly muted vs
                                # the saturated fill used for ready clips.
                                hatch_color = QColor(speaker_color_str or '#1a73a8')
                                hatch_color.setAlpha(90)
                                painter.setBrush(QBrush(hatch_color, Qt.BDiagPattern))
                                painter.drawRoundedRect(
                                    placeholder_rect, 3.0, 3.0, Qt.AbsoluteSize,
                                )
                                painter.restore()
                        if peaks is not None:
                            mins, maxs, duration = peaks
                            # Independent-subclip model: the dub band is the
                            # bounding box of all subclips, but each subclip
                            # draws its OWN rounded rect inside the band so
                            # gaps and overlaps are visible. `dub_w` is just
                            # for the legacy stretch handle / lock badge
                            # anchoring; per-subclip rects use the offset
                            # field directly.
                            from subtitld.modules import dub_clip as _dub_clip
                            extent_lo, extent_hi = _dub_clip.clip_extent(dub)
                            total_dub_dur = max(0.0, extent_hi - extent_lo) or duration
                            dub_w = total_dub_dur * widget.width_proportion
                            if widget.dub_stretching is not None and widget.dub_stretching['subtitle'] is subtitle:
                                dub_w = widget.dub_stretching['current_width']
                            # Multi-subclip stretch (post-split, ffmpeg
                            # path) widens a single subclip rather than
                            # the whole dub — but `dub_inset` (and the
                            # `setClipRect` below) must still grow to
                            # cover the stretched subclip's new right
                            # edge, otherwise the waveform inside the
                            # stretched portion gets cropped.
                            elif (widget.dub_subclip_drag is not None
                                    and widget.dub_subclip_drag.get('mode') == 'stretch'
                                    and widget.dub_subclip_drag.get('dub') is dub):
                                drag = widget.dub_subclip_drag
                                drag_idx = drag.get('segment_index', -1)
                                seg_list = list(_dub_clip.iter_segment_ranges(dub))
                                if 0 <= drag_idx < len(seg_list):
                                    seg_t0_drag, _t1_old, _s = seg_list[drag_idx]
                                    new_t1 = seg_t0_drag + drag['current_width'] / widget.width_proportion
                                    new_hi = new_t1
                                    for _j, (_t0, _t1, _s2) in enumerate(seg_list):
                                        if _j == drag_idx:
                                            continue
                                        if _t1 > new_hi:
                                            new_hi = _t1
                                    dub_w = max(0.0, new_hi - extent_lo) * widget.width_proportion
                            if dub_w > 1:
                                band_ratio = 0.25
                                # `dub_inset` is the outer band — used for
                                # lock-badge / stretch-handle layout and for
                                # clipping the waveform paint. Anchor it at
                                # `extent_lo` (the actual leftmost subclip)
                                # rather than the dub origin (`dub['start']`),
                                # otherwise subclips offset rightward or
                                # leftward from the dub origin end up
                                # outside `dub_inset` and the `setClipRect`
                                # below crops their waveforms.
                                dub_inset_left = extent_lo * widget.width_proportion
                                dub_inset = QRectF(
                                    dub_inset_left,
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

                                # Iterate subclips: each gets its own
                                # rounded rect with corner squaring on any
                                # edge that's inside the subtitle (matches
                                # the legacy "tucked-in" look). Edges that
                                # extend outside the subtitle keep the
                                # rounded corner so the dub clearly "spills"
                                # past the subtitle bound. We also collect
                                # subclip ranges here for the waveform pass
                                # below so we only iterate dub_clip helpers
                                # once.
                                segment_ranges = list(_dub_clip.iter_segment_ranges(dub))
                                hovered_subclip = widget.dub_subclip_hovered
                                dragging_subclip = widget.dub_subclip_drag
                                # Live stretch preview: the legacy stretch
                                # interaction only exists for single-subclip
                                # dubs, but the per-subclip iter_segment_ranges
                                # is driven by the on-disk source duration —
                                # so without this override the dub rect would
                                # stay at its original width while only the
                                # stretch icon's `dub_inset` widened. Force
                                # the one subclip to follow `current_width`
                                # so the whole clip rectangle tracks the
                                # cursor live.
                                stretch_active_here = (
                                    widget.dub_stretching is not None
                                    and widget.dub_stretching['subtitle'] is subtitle
                                    and len(segment_ranges) == 1
                                )
                                # Live stretch-preview geometry for a subclip.
                                # Returns the (left, right) screen x to draw
                                # during a drag. Right edge follows the cursor
                                # (`current_width` from the ORIGINAL left).
                                # When a plosive mark is the pivot, the LEFT
                                # edge also moves so the clip scales about the
                                # pivot (mark stays put) — matching what
                                # `_apply_subclip_stretch` commits on release.
                                # No active drag on this subclip → unchanged.
                                def _stretch_preview_edges(sub_idx, sx0_default, sx1_default):
                                    drag = widget.dub_subclip_drag
                                    st = None
                                    if (drag is not None
                                            and drag.get('dub') is dub
                                            and drag.get('segment_index') == sub_idx
                                            and drag.get('mode') == 'stretch'):
                                        st = drag
                                    elif stretch_active_here and sub_idx == 0:
                                        st = widget.dub_stretching
                                    if st is None:
                                        return sx0_default, sx1_default
                                    new_right = sx0_default + st['current_width']
                                    pivot_x = st.get('pivot_x')
                                    if pivot_x is None:
                                        return sx0_default, new_right
                                    denom = sx1_default - pivot_x
                                    if abs(denom) < 1e-6:
                                        return sx0_default, new_right
                                    ratio = (new_right - pivot_x) / denom
                                    if ratio <= 0:
                                        return sx0_default, new_right
                                    new_left = pivot_x + (sx0_default - pivot_x) * ratio
                                    return new_left, new_right
                                for sub_idx, (seg_t0, seg_t1, seg) in enumerate(segment_ranges):
                                    sx0, sx1 = _stretch_preview_edges(
                                        sub_idx,
                                        seg_t0 * widget.width_proportion,
                                        seg_t1 * widget.width_proportion,
                                    )
                                    sw = max(0.0, sx1 - sx0)
                                    if sw < 1:
                                        continue
                                    sub_rect = QRectF(
                                        sx0, dub_inset.top(),
                                        sw, dub_inset.height(),
                                    )
                                    bl_inside = subtitle_start_x <= sx0 <= subtitle_end_x
                                    br_inside = subtitle_start_x <= sx1 <= subtitle_end_x
                                    r = 3.0
                                    px = sub_rect.x()
                                    py = sub_rect.y()
                                    pw = sub_rect.width()
                                    ph = sub_rect.height()
                                    sub_path = QPainterPath()
                                    sub_path.moveTo(px + r, py)
                                    sub_path.lineTo(px + pw - r, py)
                                    sub_path.arcTo(px + pw - 2 * r, py, 2 * r, 2 * r, 90, -90)
                                    if br_inside:
                                        sub_path.lineTo(px + pw, py + ph)
                                    else:
                                        sub_path.lineTo(px + pw, py + ph - r)
                                        sub_path.arcTo(px + pw - 2 * r, py + ph - 2 * r, 2 * r, 2 * r, 0, -90)
                                    if bl_inside:
                                        sub_path.lineTo(px, py + ph)
                                    else:
                                        sub_path.lineTo(px + r, py + ph)
                                        sub_path.arcTo(px, py + ph - 2 * r, 2 * r, 2 * r, 270, -90)
                                    sub_path.lineTo(px, py + r)
                                    sub_path.arcTo(px, py, 2 * r, 2 * r, 180, -90)
                                    sub_path.closeSubpath()
                                    # Highlight the hovered/dragged subclip
                                    # by brightening its fill — gives a
                                    # visual handle for the body-drag.
                                    is_active = False
                                    if hovered_subclip and hovered_subclip['dub'] is dub \
                                            and hovered_subclip['segment_index'] == sub_idx:
                                        is_active = True
                                    if dragging_subclip and dragging_subclip['dub'] is dub \
                                            and dragging_subclip['segment_index'] == sub_idx:
                                        is_active = True
                                    if is_active:
                                        hl = QColor(speaker_color_str or '#1a73a8')
                                        hl = hl.lighter(140)
                                        hl.setAlpha(230)
                                        painter.setBrush(hl)
                                    else:
                                        painter.setBrush(fill_color)
                                    painter.drawPath(sub_path)

                                    # Edge-hover highlight — matches the
                                    # subtitle edge style (8px L-shape with
                                    # a fading white gradient). Drawn when
                                    # the user is hovering the matching
                                    # edge of this subclip OR actively
                                    # trimming/stretching it. Skipped when
                                    # a body-move drag is in progress so
                                    # the dragged subclip doesn't grow an
                                    # extra highlight on top of its
                                    # brightened fill.
                                    edge_side = None
                                    eh = widget.dub_subclip_edge_hovered
                                    if eh and eh['dub'] is dub and eh['segment_index'] == sub_idx:
                                        edge_side = eh.get('side')
                                    if dragging_subclip and dragging_subclip['dub'] is dub \
                                            and dragging_subclip['segment_index'] == sub_idx:
                                        dm = dragging_subclip.get('mode')
                                        if dm == 'trim_left':
                                            edge_side = 'left'
                                        elif dm == 'trim_right':
                                            edge_side = 'right'
                                    if edge_side in ('left', 'right'):
                                        handle_w = min(8.0, pw * 0.4)
                                        er = 3.0
                                        if edge_side == 'right':
                                            ex_outer = px + pw
                                            ex_inner = ex_outer - handle_w
                                            edge_path = QPainterPath()
                                            edge_path.moveTo(ex_inner, py)
                                            edge_path.lineTo(ex_outer - er, py)
                                            edge_path.arcTo(ex_outer - 2 * er, py, 2 * er, 2 * er, 90, -90)
                                            edge_path.lineTo(ex_outer, py + ph - er)
                                            edge_path.arcTo(ex_outer - 2 * er, py + ph - 2 * er, 2 * er, 2 * er, 0, -90)
                                            edge_path.lineTo(ex_inner, py + ph)
                                            grad = QLinearGradient(ex_outer, 0, ex_inner, 0)
                                        else:
                                            ex_outer = px
                                            ex_inner = ex_outer + handle_w
                                            edge_path = QPainterPath()
                                            edge_path.moveTo(ex_inner, py)
                                            edge_path.lineTo(ex_outer + er, py)
                                            edge_path.arcTo(ex_outer, py, 2 * er, 2 * er, 90, 90)
                                            edge_path.lineTo(ex_outer, py + ph - er)
                                            edge_path.arcTo(ex_outer, py + ph - 2 * er, 2 * er, 2 * er, 180, 90)
                                            edge_path.lineTo(ex_inner, py + ph)
                                            grad = QLinearGradient(ex_outer, 0, ex_inner, 0)
                                        grad.setColorAt(0, QColor(255, 255, 255, 255))
                                        grad.setColorAt(1, QColor(255, 255, 255, 0))
                                        painter.setPen(QPen(QBrush(grad), 2))
                                        painter.setBrush(Qt.NoBrush)
                                        painter.drawPath(edge_path)
                                        painter.setPen(Qt.NoPen)

                                # Lock badge — anchors the clip to the subtitle.
                                # Padlock sits at the clip's VISIBLE left edge
                                # (`dub_inset_left` = extent_lo), not the dub
                                # origin (`dub_x` = dub['start']). For a clip
                                # cropped at the start the two diverge, and
                                # anchoring at the origin left the padlock
                                # floating away from the clip it locks.
                                clip_locked = bool(dub.get('locked'))
                                lock_hovered = (widget.dub_lock_hovered == id(subtitle))
                                if clip_locked or lock_hovered:
                                    badge_h = 14.0
                                    badge_r = badge_h / 2.0
                                    clip_left_x = dub_inset_left
                                    if clip_left_x < subtitle_start_x:
                                        circle_x = clip_left_x
                                        extent_to = subtitle_start_x
                                    elif clip_left_x > subtitle_end_x:
                                        circle_x = subtitle_end_x
                                        extent_to = clip_left_x
                                    else:
                                        circle_x = clip_left_x
                                        extent_to = clip_left_x
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

                                # Stretch handle (right-edge bars + ratio
                                # readout) drawn per subclip. Click + drag
                                # the bars = stretch (changes timeline
                                # duration via ffmpeg atempo; preserves the
                                # source content). Click + drag the bare
                                # right edge = trim (changes which slice
                                # of the source plays). Single-subclip
                                # dubs keep the legacy stretch behavior
                                # (provider re-synthesis where supported).
                                if not subtitle_locked:
                                    n_subs = len(segment_ranges)
                                    subtitle_id = id(subtitle)
                                    legacy_end_hovered = (
                                        n_subs == 1
                                        and widget.dub_hovered_handle
                                        and widget.dub_hovered_handle[0] == subtitle_id
                                        and widget.dub_hovered_handle[1] == 'end'
                                    )
                                    for sub_idx, (seg_t0, seg_t1, seg) in enumerate(segment_ranges):
                                        # Per-subclip width (with live drag override).
                                        # Use the shared pivot-aware edges so the
                                        # handle/ratio track the same rect the body
                                        # loop draws (left edge shifts on a pivot).
                                        sub_sx0, sub_sx1 = _stretch_preview_edges(
                                            sub_idx,
                                            seg_t0 * widget.width_proportion,
                                            seg_t1 * widget.width_proportion,
                                        )
                                        active_stretch_state = None
                                        if (widget.dub_subclip_drag
                                                and widget.dub_subclip_drag['dub'] is dub
                                                and widget.dub_subclip_drag['segment_index'] == sub_idx
                                                and widget.dub_subclip_drag.get('mode') == 'stretch'):
                                            active_stretch_state = widget.dub_subclip_drag
                                        elif n_subs == 1 and stretch_active_here:
                                            active_stretch_state = widget.dub_stretching
                                        sub_w_px = sub_sx1 - sub_sx0
                                        # Need at least ~30px to fit the bars+text legibly.
                                        if sub_w_px < 30:
                                            continue

                                        if active_stretch_state is not None:
                                            orig_w = active_stretch_state.get('original_width', sub_w_px) or sub_w_px
                                            stretch_ratio = (sub_w_px / orig_w) if orig_w > 0 else 1.0
                                        else:
                                            # Per-subclip `rate` falls back to the
                                            # dub-level `rate` for legacy projects.
                                            rate_value = seg.get('rate', dub.get('rate', 0)) or 0
                                            stretch_ratio = 100.0 / (100.0 + rate_value) if (100 + rate_value) > 0 else 1.0

                                        hovered_here = legacy_end_hovered and sub_idx == 0
                                        if (widget.dub_subclip_stretch_hovered
                                                and widget.dub_subclip_stretch_hovered['dub'] is dub
                                                and widget.dub_subclip_stretch_hovered['segment_index'] == sub_idx):
                                            hovered_here = True
                                        bar_color = QColor(255, 255, 255, 255) if hovered_here else QColor(255, 255, 255, 180)

                                        bar_h = 10.0
                                        bar_top = dub_inset.top() + 3
                                        bar_bottom = bar_top + bar_h
                                        right_x = sub_sx1 - 5
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

                                if duration > 0:
                                    painter.setClipRect(dub_inset)
                                    painter.setPen(Qt.NoPen)
                                    painter.setBrush(dub_waveform_color)
                                    # One waveform path per subclip — and
                                    # each subclip reads from ITS OWN
                                    # file's peaks rather than the dub's
                                    # main `dub_path`. After a stretch,
                                    # `seg['path']` points at a rate-
                                    # rendered cache file that has its
                                    # own peaks; falling back to the raw
                                    # dub peaks would show the wrong
                                    # waveform (and crop weirdly because
                                    # the file durations differ).
                                    cache_h = round(dub_inset.height())
                                    for sub_idx, (seg_t0, seg_t1, seg) in enumerate(segment_ranges):
                                        # Same stretch-preview override as
                                        # the rect-paint loop above: the
                                        # waveform has to move/widen with the
                                        # rect while the user drags (incl. the
                                        # pivot left-shift), or the peaks
                                        # visibly lag behind the cursor.
                                        sx0, sx1 = _stretch_preview_edges(
                                            sub_idx,
                                            seg_t0 * widget.width_proportion,
                                            seg_t1 * widget.width_proportion,
                                        )
                                        sw_px = max(0.0, sx1 - sx0)
                                        if sw_px < 1:
                                            continue
                                        seg_path = seg.get('path') or dub_path
                                        seg_peaks = widget.dub_peaks.get(seg_path)
                                        if seg_peaks is None:
                                            # Peaks not computed yet for
                                            # this subclip's file. Kick
                                            # off the worker; the next
                                            # paint after `finished` will
                                            # have them and the waveform
                                            # appears. Skipping silently
                                            # is fine — the band rect
                                            # already drew above so the
                                            # subclip is visible.
                                            if seg_path:
                                                widget._request_dub_peaks(seg_path)
                                                widget._request_dub_onsets(seg_path)
                                            continue
                                        seg_mins, seg_maxs, seg_duration = seg_peaks
                                        seg_count = len(seg_mins)
                                        if seg_count <= 0 or seg_duration <= 0:
                                            continue
                                        src_start = float(seg.get('start', 0.0))
                                        src_end = float(seg.get('end', seg_duration))
                                        b0 = max(0, int(round(src_start / seg_duration * seg_count)))
                                        b1 = min(seg_count, int(round(src_end / seg_duration * seg_count)))
                                        n = b1 - b0
                                        if n <= 0:
                                            continue
                                        cache_w = round(sw_px)
                                        cache_key = (seg_path, b0, b1, cache_w, cache_h)
                                        wf = widget._dub_waveform_path_cache.get(cache_key)
                                        if wf is None:
                                            scale = cache_h * 0.45
                                            pixel_per_bucket = cache_w / n
                                            wf = QPainterPath()
                                            wf.moveTo(0.0, -float(seg_maxs[b0]) * scale)
                                            x = pixel_per_bucket
                                            for j in range(1, n):
                                                wf.lineTo(x, -float(seg_maxs[b0 + j]) * scale)
                                                x += pixel_per_bucket
                                            x -= pixel_per_bucket
                                            for j in range(n - 1, -1, -1):
                                                wf.lineTo(x, -float(seg_mins[b0 + j]) * scale)
                                                x -= pixel_per_bucket
                                            wf.closeSubpath()
                                            if len(widget._dub_waveform_path_cache) > widget._DUB_PATH_CACHE_MAX:
                                                for k in list(widget._dub_waveform_path_cache)[:128]:
                                                    del widget._dub_waveform_path_cache[k]
                                            widget._dub_waveform_path_cache[cache_key] = wf
                                        center = dub_inset.center().y()
                                        painter.translate(sx0, center)
                                        painter.drawPath(wf)
                                        painter.translate(-sx0, -center)

                                        # Onset markers spanning the
                                        # whole timeline height (so the
                                        # user can sight-align dub edges
                                        # to the underlying video) —
                                        # line only, colored to match
                                        # the dub band. The dub waveform
                                        # `setClipRect(dub_inset)` would
                                        # crop them to the band, so we
                                        # disable clipping briefly. Same
                                        # source-coord → timeline
                                        # mapping as the waveform path:
                                        # the segment plays [src_start,
                                        # src_end] of its file, spread
                                        # over sw_px on screen.
                                        if show_onset_markers:
                                            try:
                                                seg_onsets = widget.dub_onsets.get(seg_path)
                                                if seg_onsets is None:
                                                    if seg_path:
                                                        widget._request_dub_onsets(seg_path)
                                                elif len(seg_onsets) > 0 and src_end > src_start:
                                                    lo_o = int(np.searchsorted(seg_onsets, src_start, side='left'))
                                                    hi_o = int(np.searchsorted(seg_onsets, src_end, side='right'))
                                                    if hi_o > lo_o:
                                                        onset_pen_color = QColor(speaker_color_str or '#1a73a8')
                                                        onset_pen_color.setAlpha(128)
                                                        normal_pen = QPen(onset_pen_color, 1)
                                                        # Selected mark (pivot) — amber, thicker,
                                                        # with a triangle handle at the top so it
                                                        # reads as "grabbed".
                                                        sel = widget.selected_onset
                                                        sel_src = (float(sel['source_time'])
                                                                   if sel and sel.get('seg_path') == seg_path
                                                                   and sel.get('subtitle') is subtitle
                                                                   else None)
                                                        sel_color = QColor('#ffd24a')
                                                        sel_pen = QPen(sel_color, 2)
                                                        painter.save()
                                                        try:
                                                            painter.setClipping(False)
                                                            y_top = 0.0
                                                            y_bot = float(widget.height())
                                                            scale_x = sw_px / (src_end - src_start)
                                                            for j in range(lo_o, hi_o):
                                                                src_t = float(seg_onsets[j])
                                                                ox = sx0 + (src_t - src_start) * scale_x
                                                                if sel_src is not None and abs(src_t - sel_src) < 1e-4:
                                                                    painter.setPen(sel_pen)
                                                                    painter.drawLine(QLineF(ox, y_top, ox, y_bot))
                                                                    tri = QPainterPath()
                                                                    tri.moveTo(ox - 4, y_top)
                                                                    tri.lineTo(ox + 4, y_top)
                                                                    tri.lineTo(ox, y_top + 6)
                                                                    tri.closeSubpath()
                                                                    painter.fillPath(tri, sel_color)
                                                                else:
                                                                    painter.setPen(normal_pen)
                                                                    painter.drawLine(QLineF(ox, y_top, ox, y_bot))
                                                        finally:
                                                            painter.restore()
                                            except Exception:
                                                import traceback, sys
                                                traceback.print_exc(file=sys.stderr)
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

                if _live_sub is not None and subtitle is _live_sub:
                    # Reuse the rect the loop already computed (speaker tracks,
                    # locking, etc.) rather than duplicating that geometry.
                    _live_target_rect = QRectF(subtitle_rect)
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

            # Second pass: draw each playlist's take clips on top of every
            # subtitle so their rows never get overpainted by a neighbouring
            # clip's waveform/text.
            for _tk_rect, _tk_sub, _tk_dubs, _tk_color in _dub_takes_to_paint:
                try:
                    _paint_dub_take_rows(widget, painter, _tk_sub, _tk_rect, _tk_dubs, _tk_color)
                except Exception:
                    import traceback, sys
                    traceback.print_exc(file=sys.stderr)

        if _live is not None:
            # Outside the `if segments:` block on purpose — a brand-new
            # project has no subtitles but a take must still be visible.
            # Guarded like _paint_dub_take_rows so a preview bug degrades one
            # overlay instead of taking down the whole frame.
            try:
                _paint_live_take(widget, painter, _live, _live_target_rect)
            except Exception:
                import traceback
                import sys as _sys
                traceback.print_exc(file=_sys.stderr)

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

    def mousePressEvent(widget, event):
        # Right- (and middle-) clicks must NOT initiate any drag — they're
        # for the context menu. The release event for right-click is
        # swallowed by the menu, so any drag flag set here would stay
        # active and the user would see the clip following the cursor
        # after they dismiss the menu.
        if event.button() != Qt.LeftButton:
            event.ignore()
            return

        # Dub-playlist take rows (drawn when "show dub takes" is on). Checked
        # before everything else so a click on a take (or its "make default"
        # button) wins over the dub-body hit-tests below. Two passes: the small
        # promote button beats the row body it sits inside.
        _press_pos = QPointF(event.pos())
        for _trect, _tsub, _tdub, _taction in getattr(widget, '_dub_take_rects', []):
            if _taction == 'promote' and _trect.contains(_press_pos):
                _dub_playlist_apply(widget, _tsub, _tdub, promote=True)
                event.accept()
                return
        for _trect, _tsub, _tdub, _taction in getattr(widget, '_dub_take_rects', []):
            if _taction == 'solo' and _trect.contains(_press_pos):
                _dub_playlist_apply(widget, _tsub, _tdub, promote=False)
                event.accept()
                return

        # Per-subclip stretch handle (the bars+ratio icon near each
        # subclip's top-right corner) — must win over the subclip
        # body / edge hit-test below since the handle sits inside the
        # subclip rect and partially overlaps the right edge zone.
        #
        # Routing splits by whether the dub has been split into
        # multiple subclips:
        #
        #   * Single un-split clip → set up `dub_stretching` so
        #     mouseRelease invokes `provider.stretch(subtitle, ratio)`.
        #     For Edge TTS this re-synthesises the line at the new
        #     rate (preserving pitch); for Piper/Coqui the provider
        #     internally falls back to ffmpeg atempo on the cached
        #     raw WAV. Going through the provider keeps engine-
        #     specific behaviour intact.
        #
        #   * Already-split (>1 subclips) → ffmpeg-only path via
        #     `dub_subclip_drag` mode='stretch'. Once the user has
        #     edited a take, the subclips no longer correspond to the
        #     "line as a whole" the provider was given, so engine
        #     re-synthesis would either silently overwrite the other
        #     subclips or produce out-of-sync audio. ffmpeg atempo on
        #     each subclip's own (possibly already-rate-rendered)
        #     source is the safe option.
        stretch_hit = widget._dub_subclip_stretch_handle_at_position(event.pos())
        if stretch_hit is not None:
            from subtitld.modules import history, dub_clip
            dub = stretch_hit['dub']
            seg_idx = stretch_hit['segment_index']
            n_subs = len(list(dub_clip.iter_segment_ranges(dub)))
            # If a plosive mark on this subclip is selected, the stretch
            # pivots around it (whole clip scales about the mark) instead
            # of anchoring at the left edge. `pivot_x` is its screen x —
            # the drag preview and release math both read it.
            pivot_timeline = widget._selected_onset_pivot(dub, seg_idx)
            pivot_x = (pivot_timeline * widget.width_proportion
                       if pivot_timeline is not None else None)
            history.history_append()
            if n_subs > 1:
                # ffmpeg-only path (post-split subclips).
                widget.dub_subclip_drag = {
                    'subtitle': stretch_hit['subtitle'],
                    'dub': dub,
                    'segment_index': seg_idx,
                    'mode': 'stretch',
                    'original_width': stretch_hit['current_width'],
                    'current_width': stretch_hit['current_width'],
                    'start_x': event.pos().x(),
                    'pivot_timeline': pivot_timeline,
                    'pivot_x': pivot_x,
                }
            else:
                # Legacy single-clip path → provider.stretch on release,
                # UNLESS a pivot is set — pivot stretches always go through
                # the ffmpeg subclip path (which can move the left edge to
                # keep the pivot fixed; provider re-synthesis can't).
                widget.dub_stretching = {
                    'subtitle': stretch_hit['subtitle'],
                    'dub': dub,
                    'segment_index': seg_idx,
                    'start_x': event.pos().x(),
                    'original_width': stretch_hit['current_width'],
                    'current_width': stretch_hit['current_width'],
                    'pivot_timeline': pivot_timeline,
                    'pivot_x': pivot_x,
                }
                widget.dub_stretch_active = True
            widget.is_cursor_pressing = True
            event.accept()
            return

        # Plosive-mark selection — a discrete click on a dub-clip onset
        # mark selects it (one at a time). Runs after the stretch-handle
        # check (so the handle still wins) but BEFORE the subclip body /
        # move handlers, so clicking a mark selects it rather than
        # starting a clip drag. Only active while the overlay is visible.
        if bool(session.CONFIG.get('timeline', {}).get('show_onset_markers', False)):
            onset_hit = widget._dub_onset_at_position(event.pos())
            if onset_hit is not None:
                widget.selected_onset = {
                    'subtitle': onset_hit['subtitle'],
                    'seg_path': onset_hit['seg_path'],
                    'source_time': onset_hit['source_time'],
                }
                widget.is_cursor_pressing = False
                widget.update()
                event.accept()
                return
            elif widget.selected_onset is not None:
                # Clicked away from any mark → deselect, then fall through
                # so the click still does its normal thing (move clip,
                # select subtitle, seek…). Grabbing the stretch handle
                # keeps the selection — that branch already returned above.
                widget.selected_onset = None
                widget.update()

        # Subclip edge / body interactions — run BEFORE the dub-stretch
        # and full-dub drag handlers so a click inside a subclip's
        # rounded rect wins over the full-clip handlers. The subclip
        # rects live inside the subtitle area, so otherwise the click
        # would be consumed by `_dub_hit_at_position`.
        subclip_hit = widget._dub_subclip_at_position(event.pos())
        if subclip_hit is not None:
            from subtitld.modules import history, dub_clip
            dub = subclip_hit['dub']
            sub_segments = list(dub_clip.iter_segment_ranges(dub))
            n_subs = len(sub_segments)
            side = subclip_hit['side']
            # Trim edges — both single-subclip and multi-subclip dubs
            # trim by edge drag. Stretching is a distinct gesture on the
            # bars handle (checked above) so the two don't conflict.
            if side == 'left':
                history.history_append()
                # Make sure the dub has a `segments` field so trim works
                # on freshly-loaded legacy dubs too. normalize_segments
                # opens the file once for duration probing (main thread,
                # OK) then leaves it cached on disk.
                dub_clip.normalize_segments(dub)
                widget.dub_subclip_drag = {
                    'subtitle': subclip_hit['subtitle'],
                    'dub': dub,
                    'segment_index': subclip_hit['segment_index'],
                    'mode': 'trim_left',
                }
                widget.is_cursor_pressing = True
                event.accept()
                return
            if side == 'right':
                history.history_append()
                dub_clip.normalize_segments(dub)
                source_max = widget._source_duration_for_path(
                    subclip_hit['segment'].get('path')
                )
                widget.dub_subclip_drag = {
                    'subtitle': subclip_hit['subtitle'],
                    'dub': dub,
                    'segment_index': subclip_hit['segment_index'],
                    'mode': 'trim_right',
                    'source_max': source_max,
                }
                widget.is_cursor_pressing = True
                event.accept()
                return
            # Body click on a multi-subclip dub → move that subclip
            # independently. Single-subclip dubs fall through to the
            # legacy `_dub_hit_at_position` path so dragging the body
            # still moves `dub['start']` (and any locked subtitle moves
            # with it — old behavior preserved).
            if side is None and n_subs > 1:
                history.history_append()
                base = float(dub.get('start', 0.0))
                seg_offset = float(subclip_hit['segment'].get('offset', 0.0))
                subclip_left_x = (base + seg_offset) * widget.width_proportion
                widget.dub_subclip_drag = {
                    'subtitle': subclip_hit['subtitle'],
                    'dub': dub,
                    'segment_index': subclip_hit['segment_index'],
                    'mode': 'move',
                    'click_offset_x': event.pos().x() - subclip_left_x,
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
        if widget.dub_subclip_drag is not None:
            drag = widget.dub_subclip_drag
            widget.dub_subclip_drag = None
            widget.is_cursor_pressing = False
            mode = drag.get('mode', 'move')
            # Stretch ends with an ffmpeg atempo re-render — apply only
            # if the user actually moved the cursor; tiny accidental
            # jiggles shouldn't churn ffmpeg.
            if mode == 'stretch':
                original_w = float(drag.get('original_width', 0.0))
                current_w = float(drag.get('current_width', original_w))
                if original_w > 0 and current_w > 0 and abs(current_w - original_w) > 2.0:
                    ratio, pivot = widget._stretch_ratio_and_pivot(
                        drag['dub'], drag['segment_index'],
                        original_w, current_w, drag.get('pivot_timeline'),
                    )
                    if ratio and ratio > 0:
                        widget._apply_subclip_stretch(
                            drag['dub'], drag['segment_index'], ratio,
                            pivot_timeline=pivot,
                        )
                # The clip's file re-rendered (new path + shifted onset
                # times), so a mark selection no longer maps to it — drop it.
                widget.selected_onset = None
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
            pivot_timeline = state.get('pivot_timeline')
            regenerated = False
            if (pivot_timeline is not None
                    and original_w > 0 and current_w > 0
                    and abs(current_w - original_w) > 2.0):
                # Pivot stretch — always via the ffmpeg subclip path, even
                # for a single un-split clip: only it can move the left
                # edge to keep the mark fixed (provider re-synthesis just
                # re-renders at a new rate, it can't reposition the clip).
                seg_idx = state.get('segment_index', 0)
                ratio, pivot = widget._stretch_ratio_and_pivot(
                    state['dub'], seg_idx, original_w, current_w, pivot_timeline,
                )
                if ratio and ratio > 0:
                    widget._apply_subclip_stretch(
                        state['dub'], seg_idx, ratio, pivot_timeline=pivot,
                    )
                    session.set_unsaved()
                    widget._refresh_dub_after_edit()
                    regenerated = True
                widget.selected_onset = None
                widget.dub_stretching = None
                widget.update()
                event.accept()
                return
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
        # Live subclip drag — convert mouse x to a timeline offset and
        # dispatch to the right dub_clip helper based on drag mode. The
        # helpers reject negative-duration / out-of-bounds states so we
        # don't need to clamp here beyond the bare "no negative offset"
        # guard.
        drag = widget.dub_subclip_drag
        if drag is not None:
            from subtitld.modules import dub_clip
            dub = drag['dub']
            idx = drag['segment_index']
            mode = drag.get('mode', 'move')
            base = float(dub.get('start', 0.0))
            time_at_x = max(0.0, event.pos().x() / widget.width_proportion)
            if mode == 'move':
                new_left_x = event.pos().x() - drag.get('click_offset_x', 0.0)
                new_offset = (new_left_x / widget.width_proportion) - base
                dub_clip.move_subclip(dub, idx, new_offset)
            elif mode == 'trim_left':
                new_offset = time_at_x - base
                dub_clip.trim_subclip_left(dub, idx, new_offset, source_min=0.0)
            elif mode == 'trim_right':
                new_end_offset = time_at_x - base
                dub_clip.trim_subclip_right(
                    dub, idx, new_end_offset, source_max=drag.get('source_max'),
                )
            elif mode == 'stretch':
                # Stretch: just update the preview width — the real
                # work (ffmpeg re-render, schema update) happens on
                # mouseRelease so we don't churn ffmpeg on every move
                # event. Clamp to a minimum so the user can't drag the
                # rect to zero / invert it during the live preview.
                delta = event.pos().x() - drag.get('start_x', event.pos().x())
                new_w = max(10.0, drag.get('original_width', 0.0) + delta)
                drag['current_width'] = new_w
            widget.update()
            event.accept()
            return

        # Hover-work throttle. A bare hover (no button pressed, no drag in
        # progress) still runs the full O(n_subtitles) hover pipeline
        # below — subtitle-under-cursor scan, adjacent lookup, and several
        # per-subclip / per-handle hit-tests — on EVERY raw mouse-move.
        # X11 delivers move events faster than the display refresh, so on
        # a large project this pins the GUI thread's GIL and the background
        # mixer thread can't refill the audio ring within its ~200 ms of
        # pre-fill → the user hears playback cut out just from moving the
        # mouse over the timeline. Cap the hover pipeline to ~60 Hz while
        # playing (imperceptible for cursor/hover feedback) so the mixer
        # always gets GIL time. Paused → no throttle (full responsiveness,
        # and there's no audio to protect).
        if (not widget.is_cursor_pressing
                and not widget.dub_stretch_active
                and not widget.dub_start_is_clicked):
            try:
                _hover_playing = not widget.window().preview_panel_player.is_paused()
            except Exception:
                _hover_playing = False
            if _hover_playing:
                _hover_now_t = time.perf_counter()
                if _hover_now_t - widget._hover_throttle_last_t < 0.016:  # ~60 Hz
                    event.accept()
                    return
                widget._hover_throttle_last_t = _hover_now_t

        # Hover detection for subclips — set hovered state + cursor when
        # over a subclip body, edge, or stretch handle. Only when no
        # other drag is in progress so the cursor doesn't flicker.
        if not widget.dub_stretch_active and not widget.dub_start_is_clicked:
            from subtitld.modules import dub_clip
            # Stretch handle takes priority — it sits inside the subclip
            # rect and partially overlaps the right-edge zone, so without
            # this the edge would always win and the bars icon would
            # never get a chance.
            stretch_hover_hit = widget._dub_subclip_stretch_handle_at_position(event.pos())
            new_stretch = None
            new_body = None
            new_edge = None
            if stretch_hover_hit is not None:
                sub_segments = list(dub_clip.iter_segment_ranges(stretch_hover_hit['dub']))
                if len(sub_segments) > 1:
                    new_stretch = {
                        'dub': stretch_hover_hit['dub'],
                        'segment_index': stretch_hover_hit['segment_index'],
                    }
            if new_stretch is None:
                sub_hit = widget._dub_subclip_at_position(event.pos())
                if sub_hit is not None:
                    sub_segments = list(dub_clip.iter_segment_ranges(sub_hit['dub']))
                    n_subs = len(sub_segments)
                    # Edge hover fires for any subclip count — both
                    # single-subclip and multi-subclip dubs are
                    # trimmable on their edges now.
                    if sub_hit['side'] in ('left', 'right'):
                        new_edge = {
                            'dub': sub_hit['dub'],
                            'segment_index': sub_hit['segment_index'],
                            'side': sub_hit['side'],
                        }
                    elif sub_hit['side'] is None and n_subs > 1:
                        new_body = {
                            'dub': sub_hit['dub'],
                            'segment_index': sub_hit['segment_index'],
                        }
            changed = False
            if new_body != widget.dub_subclip_hovered:
                widget.dub_subclip_hovered = new_body
                changed = True
            if new_edge != widget.dub_subclip_edge_hovered:
                widget.dub_subclip_edge_hovered = new_edge
                changed = True
            if new_stretch != widget.dub_subclip_stretch_hovered:
                widget.dub_subclip_stretch_hovered = new_stretch
                changed = True
            if changed:
                widget.update()
            # Cursor priority: stretch handle uses SizeHorCursor (it's
            # a "resize" gesture, not an edge drag). Edge hover uses
            # the custom left/right arrow cursors that match the
            # subtitle edge convention — points outward in the
            # direction the edge will be dragged. Body hover (multi-
            # subclip) uses the four-way move cursor. `_dub_cursor_set`
            # tracks whether WE set the cursor on a previous move event,
            # so we know to clear it on hover-leave (QCursor equality
            # is unreliable for matching our pixmap cursors).
            if new_stretch is not None:
                widget.setCursor(Qt.SizeHorCursor)
                widget._dub_cursor_set = True
            elif new_edge is not None:
                if new_edge['side'] == 'left':
                    widget.setCursor(widget._left_arrow_cursor)
                else:
                    widget.setCursor(widget._right_arrow_cursor)
                widget._dub_cursor_set = True
            elif new_body is not None:
                widget.setCursor(Qt.SizeAllCursor)
                widget._dub_cursor_set = True
            elif getattr(widget, '_dub_cursor_set', False):
                # Bare timeline (no dub element under the cursor) uses the
                # text I-beam cursor — same convention as DAWs and video
                # editors where the timeline is a "click to position the
                # playhead" surface.
                widget.setCursor(Qt.IBeamCursor)
                widget._dub_cursor_set = False

        if widget.dub_stretch_active and widget.dub_stretching is not None:
            state = widget.dub_stretching
            delta = event.pos().x() - state['start_x']
            new_w = max(10.0, state['original_width'] + delta)
            state['current_width'] = new_w
            widget.update()
            return

        if widget.dub_start_is_clicked and widget.dragging_dub is not None:
            # No clamp: a dub may be dragged past both ends of the timeline
            # (start < 0, or beyond the media duration). The model and audio
            # engine already tolerate out-of-bounds positions — this matches
            # the multi-subclip drag path (`move_subclip`), which never
            # clamped. The part outside [0, duration] is simply clipped by
            # the timeline viewport when drawn.
            new_start = (event.pos().x() - widget.dragging_dub_offset) / widget.width_proportion
            widget.dragging_dub['start'] = new_start
            widget.update()
            return

        if not session.SUBTITLE.get('segments'):
            if widget.is_cursor_pressing:
                # No subtitles to drag → a press-drag scrubs the playhead, just
                # like the non-empty path below (which this early return would
                # otherwise skip). Throttle to ~30 Hz while playing for the same
                # audio-callback reason as that path.
                is_paused = widget.window().preview_panel_player.is_paused()
                now_t = time.perf_counter()
                if is_paused or now_t - widget._drag_throttle_last_t >= 0.033:
                    widget._drag_throttle_last_t = now_t
                    session.SUBTITLE['position'] = (event.pos().x() / widget.width()) * session.VIDEO.get('duration', 60)
                    if session.CONFIG['repeat_activated']:
                        session.REPEAT_DURATION_BUFFER = []
                    widget.seek.emit(session.SUBTITLE.get('position', 0))
                    widget.update()
                return
            waveform_center_y = widget.waveform_y + (widget.waveform_height * 0.5)
            in_band = abs(event.pos().y() - waveform_center_y) <= 10
            new_x = event.pos().x() if in_band else None
            if new_x != widget.empty_state_hover_x:
                widget.empty_state_hover_x = new_x
                widget.update()
            if in_band:
                widget.setCursor(Qt.PointingHandCursor)
            else:
                # Empty project (no subtitles) — bare timeline → I-beam.
                widget.setCursor(Qt.IBeamCursor)
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
                # Don't override the four-way move cursor that the new
                # per-subclip body hover set just above — that hit-test
                # is more specific than _dub_hit_at_position (which fires
                # for the whole legacy band) and should win.
                if not getattr(widget, '_dub_cursor_set', False):
                    widget.setCursor(Qt.SizeHorCursor)
            else:
                # Bare timeline (no subtitle, no dub element) → I-beam.
                # If the new per-subclip hover code already chose a
                # cursor (edge arrow, stretch resize, body move) leave
                # that alone.
                if not getattr(widget, '_dub_cursor_set', False):
                    widget.setCursor(Qt.IBeamCursor)

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

        if session.SUBTITLE.get('selected', None) is not None and session.SUBTITLE['selected'] in session.SUBTITLE['segments']:
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


        # Throttle the heavy "drag while playing" branch to ~30 Hz.
        # Mouse moves fire at the display refresh rate; without a gate,
        # each one fires QMediaPlayer.setPosition() + audio-engine
        # seek() + a full-widget repaint, holding the GIL long enough
        # that the sounddevice callback misses its window and audio
        # hangs. Paused → no gate (full responsiveness); playing → gate.
        is_paused = widget.window().preview_panel_player.is_paused()
        should_throttle = (not is_paused) and widget.is_cursor_pressing
        emit_drag_work = True
        if should_throttle:
            now_t = time.perf_counter()
            if now_t - widget._drag_throttle_last_t < 0.033:  # ~30 Hz
                emit_drag_work = False
            else:
                widget._drag_throttle_last_t = now_t

        if (widget.is_cursor_pressing and emit_drag_work
                and not (widget.subtitle_start_is_clicked or widget.subtitle_end_is_clicked or widget.subtitle_is_clicked)):
            session.SUBTITLE['position'] = (event.pos().x() / widget.width()) * session.VIDEO.get('duration', 60)
            widget.seek.emit(session.SUBTITLE.get('position', 0))

        # Repaint on every move when the user is actively dragging
        # (edges, body, playhead — anything that sets is_cursor_pressing),
        # otherwise the drag lags behind the cursor during playback because
        # the only repaint is the 4Hz throttled timer in playercontrols.
        # When idle (no press) during playback we still defer to that
        # timer; the hover-state branches above already call update() on
        # state changes, so the cursor/hover visuals stay correct.
        if is_paused or (widget.is_cursor_pressing and emit_drag_work):
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
            # Onset markers ride alongside the waveform cache: load from
            # disk if present, otherwise kick off the background detector
            # on the now-loaded samples.
            widget._load_or_compute_background_onsets()
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
        widget._load_or_compute_background_onsets()
        QTimer.singleShot(1000, lambda: widget.update())

    def _background_onsets_cache_path(widget, source='mix'):
        """Cache file for the main-audio onset times. `source` is 'mix'
        (full audio) or 'vocals' — kept as separate files so vocals
        detection (the preferred path once separation has run) can be
        stored alongside an existing mix cache without overwriting.
        Shares the same cache key as the waveform pipeline so the
        cache invalidates whenever the waveform one does."""
        cache_dir = widget.waveform_manager.cache_dir
        if not cache_dir or not widget.waveform_manager.filepath:
            return None
        cache_key = utils.get_cache_key(widget.waveform_manager.filepath)
        if not cache_key:
            return None
        suffix = '_onsets_vocals' if source == 'vocals' else '_onsets'
        return os.path.join(cache_dir, f"{cache_key}{suffix}.npy")

    def _vocals_source_path(widget):
        """Return the cached vocals FLAC path for the current video, or
        None if separation hasn't produced one yet. Plosives stand out
        far more on the vocals-only track than on the full mix, so the
        background-onset pass switches over the moment it's available."""
        try:
            from subtitld.modules import bounce
            return bounce._vocals_audio_path()
        except Exception:
            return None

    def _load_or_compute_background_onsets(widget):
        # Prefer vocals when available. Both caches live alongside each
        # other, so we read whichever matches the source we're about to
        # use. If a vocals cache exists, that overrides the mix cache
        # even if the latter was populated earlier.
        vocals_path = widget._vocals_source_path()
        if vocals_path:
            cache_file = widget._background_onsets_cache_path(source='vocals')
            if cache_file and os.path.exists(cache_file):
                try:
                    widget.background_onsets = np.load(cache_file).astype(np.float32)
                    return
                except Exception:
                    pass
            prev = widget.background_onset_thread
            if prev is not None and prev.isRunning():
                if hasattr(prev, 'cancel'):
                    prev.cancel()
            thread = BackgroundOnsetsFromFileWorker(vocals_path, parent=widget)
            thread.finished.connect(lambda o: widget._on_background_onsets_ready(o, source='vocals'))
            widget.background_onset_thread = thread
            thread.start()
            return

        # No vocals yet — use the full-mix samples already in memory.
        cache_file = widget._background_onsets_cache_path(source='mix')
        if cache_file and os.path.exists(cache_file):
            try:
                widget.background_onsets = np.load(cache_file).astype(np.float32)
                return
            except Exception:
                pass
        samples = widget.waveform_manager.samples
        sr = session.VIDEO.get('samplerate', 48000)
        if samples is None or getattr(samples, 'size', 0) == 0:
            return
        if getattr(samples, 'dtype', None) == np.int16:
            samples_f = samples.astype(np.float32) / 32768.0
        else:
            samples_f = samples
        prev = widget.background_onset_thread
        if prev is not None and prev.isRunning():
            if hasattr(prev, 'cancel'):
                prev.cancel()
        thread = OnsetDetectionThread(samples_f, sr, parent=widget)
        thread.finished.connect(lambda o: widget._on_background_onsets_ready(o, source='mix'))
        widget.background_onset_thread = thread
        thread.start()

    def _on_background_onsets_ready(widget, onsets, source='mix'):
        widget.background_onsets = onsets
        cache_file = widget._background_onsets_cache_path(source=source)
        if cache_file is not None:
            try:
                np.save(cache_file, onsets)
            except Exception:
                pass
        widget.update()

    def on_vocals_separation_ready(widget):
        """Called by playercontrols when the vocals-separated track
        becomes available (either via the fast-path cache hit on reopen
        or after live separation finishes). Re-runs the background-onset
        pass against vocals so plosives stop competing with music
        transients in the spectral-flux output."""
        widget._load_or_compute_background_onsets()

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

            # On-screen rect must come from the dub's TIMELINE extent, not
            # the raw file duration. A stretched or left-cropped subclip
            # plays [start, end] of its file placed at `offset`, so its
            # visible width is `end - start` and its left edge sits at
            # `dub['start'] + offset` — NOT `dub['start']` + raw-file
            # length. Using the raw duration (from dub_peaks) + bare
            # `dub['start']` produced a hit rect that didn't match what
            # the user sees, so clicks fell through to whichever other
            # dub's (equally wrong) rect happened to cover that x — the
            # "wrong / last clip gets moved" bug.
            base = float(dub.get('start', subtitle.get('start', 0.0)))
            segments = dub.get('segments')
            lo = hi = None
            if segments:
                for seg in segments:
                    if seg.get('type', 'audio') != 'audio':
                        continue
                    seg_end = seg.get('end')
                    if seg_end is None:
                        continue
                    seg_t0 = base + float(seg.get('offset', 0.0))
                    seg_t1 = seg_t0 + (float(seg_end) - float(seg.get('start', 0.0)))
                    lo = seg_t0 if lo is None or seg_t0 < lo else lo
                    hi = seg_t1 if hi is None or seg_t1 > hi else hi
            if lo is None:
                # Legacy dub (no segments yet) — fall back to the cached
                # raw-file duration from dub_peaks. Correct here because an
                # un-edited dub has offset 0 and plays its whole file.
                dub_path = dub.get('path')
                peaks = widget.dub_peaks.get(dub_path) if dub_path else None
                if peaks is None:
                    continue
                lo, hi = base, base + peaks[2]

            dub_x = lo * widget.width_proportion
            dub_w = (hi - lo) * widget.width_proportion

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

    def _subclip_track_geometry(widget, subtitle):
        """Vertical geometry of the dub band for `subtitle`. Returns
        (band_top, band_bottom) — the same y-range the paint code uses,
        per-speaker-track aware so hit-tests line up with the right row."""
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
        return (sub_top + track_h * (1.0 - band_ratio), sub_top + track_h)

    def _dub_subclip_at_position(widget, pos, edge_threshold_px=5):
        """Hit-test against the per-subclip rects. Returns a dict:
            {subtitle, dub, segment_index, segment, segment_t0, segment_t1,
             time_at_x, side}
        where `side` is one of 'left', 'right', or None. 'left'/'right'
        means the cursor is within `edge_threshold_px` of that edge —
        the caller decides whether to treat that as a trim or a body
        action. None means the cursor is inside the body but not on an
        edge.

        Returns None when no subclip is under the cursor."""
        from subtitld.modules import dub_clip
        if widget.width_proportion <= 0:
            return None
        time_at_x = pos.x() / widget.width_proportion
        for subtitle in session.SUBTITLE.get('segments', []) or []:
            if not subtitle.get('dubbing'):
                continue
            if session.SPEAKERS.get(subtitle.get('speaker', 'A'), {}).get('hidden'):
                continue
            band_top, band_bottom = widget._subclip_track_geometry(subtitle)
            # Slight Y slack so the rect stays grabbable at the
            # sub-pixel boundary.
            if not (band_top - 2 <= pos.y() <= band_bottom + 2):
                continue
            dub = subtitle['dubbing'][0]
            for idx, (t0, t1, seg) in enumerate(dub_clip.iter_segment_ranges(dub)):
                sx0 = t0 * widget.width_proportion
                sx1 = t1 * widget.width_proportion
                if pos.x() < sx0 - edge_threshold_px:
                    continue
                if pos.x() > sx1 + edge_threshold_px:
                    continue
                side = None
                if abs(pos.x() - sx0) <= edge_threshold_px:
                    side = 'left'
                elif abs(pos.x() - sx1) <= edge_threshold_px:
                    side = 'right'
                return {
                    'subtitle': subtitle, 'dub': dub,
                    'segment_index': idx, 'segment': seg,
                    'segment_t0': t0, 'segment_t1': t1,
                    'time_at_x': time_at_x, 'side': side,
                }
        return None

    def _onset_timeline_pos(widget, seg_t0, seg_source_start, source_time):
        """Timeline seconds of a dub onset. A subclip maps its source
        region [start, end] 1:1 onto its timeline span (the playback rate
        is baked into the file), so an onset at `source_time` in the file
        lands at ``seg_t0 + (source_time - seg_source_start)``."""
        return seg_t0 + (source_time - seg_source_start)

    def _dub_onset_at_position(widget, pos, threshold_px=5):
        """Hit-test the per-dub-clip onset marks. Returns
            {subtitle, dub, segment_index, seg_path, source_time, timeline_pos}
        for the nearest mark within `threshold_px` of the cursor x (and
        inside the clip's dub band), or None. Only meaningful while the
        onset overlay is visible."""
        from subtitld.modules import dub_clip
        wpp = widget.width_proportion
        if wpp <= 0:
            return None
        best = None
        best_dx = threshold_px + 1
        for subtitle in session.SUBTITLE.get('segments', []) or []:
            if not subtitle.get('dubbing'):
                continue
            if session.SPEAKERS.get(subtitle.get('speaker', 'A'), {}).get('hidden'):
                continue
            band_top, band_bottom = widget._subclip_track_geometry(subtitle)
            if not (band_top - 2 <= pos.y() <= band_bottom + 2):
                continue
            dub = subtitle['dubbing'][0]
            for idx, (t0, t1, seg) in enumerate(dub_clip.iter_segment_ranges(dub)):
                seg_path = seg.get('path')
                onsets = widget.dub_onsets.get(seg_path) if seg_path else None
                if onsets is None or len(onsets) == 0:
                    continue
                src_start = float(seg.get('start', 0.0))
                src_end = float(seg.get('end', 0.0))
                lo = int(np.searchsorted(onsets, src_start, side='left'))
                hi = int(np.searchsorted(onsets, src_end, side='right'))
                for j in range(lo, hi):
                    src_time = float(onsets[j])
                    onset_x = widget._onset_timeline_pos(t0, src_start, src_time) * wpp
                    dx = abs(pos.x() - onset_x)
                    if dx <= threshold_px and dx < best_dx:
                        best_dx = dx
                        best = {
                            'subtitle': subtitle, 'dub': dub,
                            'segment_index': idx, 'seg_path': seg_path,
                            'source_time': src_time,
                            'timeline_pos': widget._onset_timeline_pos(t0, src_start, src_time),
                        }
        return best

    def _selected_onset_pivot(widget, dub, segment_index):
        """If the currently-selected onset belongs to `dub`'s subclip at
        `segment_index`, return its CURRENT timeline position (seconds) —
        the pivot the stretch should scale around. Else None."""
        sel = widget.selected_onset
        if not sel:
            return None
        from subtitld.modules import dub_clip
        ranges = list(dub_clip.iter_segment_ranges(dub))
        if not (0 <= segment_index < len(ranges)):
            return None
        t0, _t1, seg = ranges[segment_index]
        if seg.get('path') != sel.get('seg_path'):
            return None
        if sel.get('subtitle') is not None and sel['subtitle'] is not widget._subtitle_for_dub(dub):
            return None
        src_start = float(seg.get('start', 0.0))
        src_end = float(seg.get('end', 0.0))
        st = float(sel.get('source_time', 0.0))
        # Only a pivot if the mark still falls within the played region.
        if not (src_start - 1e-6 <= st <= src_end + 1e-6):
            return None
        return widget._onset_timeline_pos(t0, src_start, st)

    def _subtitle_for_dub(widget, dub):
        for subtitle in session.SUBTITLE.get('segments', []) or []:
            dubs = subtitle.get('dubbing') or []
            if dubs and dubs[0] is dub:
                return subtitle
        return None

    def _refresh_dub_after_edit(widget):
        """After a dub-subclip edit, push the new state to the audio
        engine and repaint the timeline."""
        widget.update()
        window = widget.window()
        preview = getattr(window, 'preview_panel_player', None)
        if preview is None:
            return
        device = getattr(preview, '_audio_device', None)
        if device is not None and hasattr(device, 'sync_subtitle_dubs'):
            device.sync_subtitle_dubs(session.SUBTITLE.get('segments', []) or [])

    def _dub_subclip_stretch_handle_at_position(widget, pos, halo_px=4):
        """Hit-test the per-subclip stretch handle (bars icon at the
        top-right of each subclip rect). Returns
        {subtitle, dub, segment_index, segment, current_width} or None.

        Matches the geometry in `paintEvent`: a ~14px-wide × 14px-tall
        zone anchored at the subclip's top-right corner. `halo_px` widens
        the hit area beyond the visible bars so a steady cursor doesn't
        need pixel-perfect aim."""
        from subtitld.modules import dub_clip
        if widget.width_proportion <= 0:
            return None
        for subtitle in session.SUBTITLE.get('segments', []) or []:
            if not subtitle.get('dubbing'):
                continue
            if session.SPEAKERS.get(subtitle.get('speaker', 'A'), {}).get('hidden'):
                continue
            band_top, band_bottom = widget._subclip_track_geometry(subtitle)
            # The handle sits ~3px below the band top with a height of ~10px.
            handle_top = band_top + 3 - halo_px
            handle_bottom = band_top + 3 + 10 + halo_px
            if not (handle_top <= pos.y() <= handle_bottom):
                continue
            dub = subtitle['dubbing'][0]
            for idx, (t0, t1, seg) in enumerate(dub_clip.iter_segment_ranges(dub)):
                sx0 = t0 * widget.width_proportion
                sx1 = t1 * widget.width_proportion
                if sx1 - sx0 < 30:
                    continue
                # Handle zone: ~14px from the right edge inward. Matches
                # the visible bars which span from (sx1 - 12) to (sx1 - 4).
                if (sx1 - 14 - halo_px) <= pos.x() <= (sx1 + halo_px):
                    return {
                        'subtitle': subtitle, 'dub': dub,
                        'segment_index': idx, 'segment': seg,
                        'current_width': sx1 - sx0,
                    }
        return None

    def _stretch_ratio_and_pivot(widget, dub, seg_idx, original_w, current_w,
                                 pivot_timeline):
        """Resolve the (new_dur / old_dur) ratio for a stretch release,
        plus the pivot to pass to `_apply_subclip_stretch`.

        Without a pivot: the plain left-anchored ratio `current_w /
        original_w`. With a pivot at timeline second P: the clip scales
        about P, so the ratio is measured from the pivot to the dragged
        right edge — `(new_right - P) / (old_right - P)`. Returns
        (ratio, pivot) or (None, None) if the geometry is degenerate."""
        from subtitld.modules import dub_clip
        wpp = widget.width_proportion
        fallback = (current_w / original_w, None) if original_w > 0 else (None, None)
        if pivot_timeline is None or wpp <= 0:
            return fallback
        ranges = list(dub_clip.iter_segment_ranges(dub))
        if not (0 <= seg_idx < len(ranges)):
            return fallback
        t0, t1, _seg = ranges[seg_idx]
        new_right_time = t0 + current_w / wpp
        denom = t1 - pivot_timeline
        if abs(denom) < 1e-6:
            # Pivot sits at (or past) the right edge — scaling about it is
            # undefined; fall back to a left-anchored stretch.
            return fallback
        ratio = (new_right_time - pivot_timeline) / denom
        if ratio <= 0:
            return (None, None)
        return (ratio, pivot_timeline)

    def _apply_subclip_stretch(widget, dub, segment_index, ratio, pivot_timeline=None):
        """Re-render a subclip at a new rate so it occupies `ratio` times
        its current timeline duration. `ratio > 1` slows the clip down
        (the file becomes longer, the user dragged the right edge out);
        `ratio < 1` speeds it up.

        `pivot_timeline` (seconds) is the fixed point the stretch scales
        around. When None (the default), the left edge stays put — the
        clip grows/shrinks rightward. When set (a selected plosive mark),
        the whole clip scales uniformly about that timeline position: both
        edges move proportionally away from it, and the mark stays put.
        We keep the mark fixed by moving the subclip's `offset` so its new
        left edge lands at ``pivot + (old_left - pivot) * ratio``.

        Uses `audio_stretch.stretch_by_rate` (host-side ffmpeg atempo).
        Never re-synthesizes via the TTS provider, even on dubs that
        were originally Edge-TTS generated — the user explicitly chose
        the per-subclip stretch UI, and ffmpeg is consistent across
        engines.

        Always stretches from the IMMUTABLE raw path (stored in
        `seg['raw_path']` once any stretch has been applied) so repeated
        stretches don't compound quality loss."""
        from pathlib import Path
        from subtitld.modules import audio_stretch, dub_clip
        # Materialise the segments list for legacy single-clip dubs
        # (those that still only have `dub['path']` and no `segments`
        # field). Without this the early-return on `not segments`
        # silently swallows every stretch on a freshly-loaded
        # un-edited dub. normalize_segments is main-thread-safe and
        # opens the source file once to fill `end` with a concrete
        # duration.
        segments = dub_clip.normalize_segments(dub)
        if not segments or not (0 <= segment_index < len(segments)):
            return
        seg = segments[segment_index]
        if seg.get('type', 'audio') != 'audio':
            return
        end = seg.get('end')
        if end is None:
            return
        old_start = float(seg.get('start', 0.0))
        old_end = float(end)
        old_offset = float(seg.get('offset', 0.0))
        if old_end <= old_start:
            return

        # `raw_path` is set on first stretch and preserved for all future
        # re-renders. The current `path` may already be a rate-rendered
        # cache file; raw is the master copy.
        raw_path = seg.get('raw_path') or seg.get('path')
        if not raw_path:
            return
        current_rate = int(seg.get('rate', 0) or 0)

        # current_factor = ratio of CURRENT file's playback speed to RAW.
        # rate_pct convention (subtitld UI units): factor = 1 + rate/100.
        # rate=0 → factor=1 (RAW). rate=+50 → factor=1.5 (1.5× faster, file
        # is 1/1.5 of raw duration).
        current_factor = 1.0 + current_rate / 100.0
        # User-facing `ratio` is new_timeline_dur / old_timeline_dur.
        # The CONTENT length (in raw seconds) is constant, so the new
        # file's factor relative to raw is current_factor / ratio.
        new_factor = current_factor / max(ratio, 1e-6)
        new_rate = int(round((new_factor - 1.0) * 100))
        # Clamp to audio_stretch's safe range. ±100 → factor in [0, 2];
        # going outside risks atempo chains that degrade quality.
        new_rate = max(-100, min(100, new_rate))

        try:
            new_path = audio_stretch.stretch_by_rate(Path(raw_path), new_rate)
        except Exception:
            return
        # Schema update: source coords for the same content shift by
        # current_factor / new_factor = ratio. So new_start = old_start
        # * ratio, new_end = old_end * ratio. Derivation:
        #   raw_start = old_start * current_factor
        #   new_start = raw_start / new_factor = old_start * ratio
        seg['raw_path'] = str(raw_path)
        seg['path'] = str(new_path)
        seg['rate'] = new_rate
        seg['start'] = old_start * ratio
        seg['end'] = old_end * ratio
        # Pivot: keep the selected mark's timeline position fixed by
        # moving the subclip's left edge to `pivot + (old_left - pivot) *
        # ratio`. Without a pivot, `offset` is untouched (left-edge
        # anchor). `offset` is a TIMELINE coordinate, so it is NOT scaled
        # the way `start`/`end` (source coords) are — see the addon
        # provider stretch for the same distinction.
        if pivot_timeline is not None:
            base = float(dub.get('start', 0.0))
            old_left = base + old_offset
            new_left = pivot_timeline + (old_left - pivot_timeline) * ratio
            seg['offset'] = new_left - base
        # Kick off the peaks worker for the new file immediately so the
        # waveform shows up as soon as ffmpeg's read-and-bucket pass
        # finishes — without this the next paint requests peaks on
        # demand, but the user sees a beat of "no waveform" while the
        # worker spins up. Idempotent (no-op if already cached or
        # in-flight).
        widget._request_dub_peaks(str(new_path))

    def _source_duration_for_path(widget, path):
        """File duration for a dub path — used as the upper bound when
        trimming a subclip's right edge. Cached via dub_peaks (already
        populated for every visible dub) so this is just a dict lookup
        in the hot path."""
        if not path:
            return None
        peaks = widget.dub_peaks.get(path)
        if peaks is not None:
            return float(peaks[2])
        return None

    def contextMenuEvent(widget, event):
        from PySide6.QtWidgets import QMenu
        from subtitld.modules import dub_clip, history
        hit = widget._dub_subclip_at_position(event.pos())
        if hit is None:
            event.ignore()
            return
        menu = QMenu(widget)
        act_split = menu.addAction(_('timeline.dub.split_here'))

        def do_split():
            history.history_append()
            offset = hit['time_at_x'] - hit['segment_t0']
            if dub_clip.split_audio_segment(hit['dub'], hit['segment_index'], offset):
                session.set_unsaved()
                widget._refresh_dub_after_edit()

        act_split.triggered.connect(do_split)

        # Remove is enabled only when more than one subclip exists —
        # removing the last subclip would leave an empty take.
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

    def _dub_cache_signature(widget, path):
        """Content-aware key for a dub file's peaks/onset caches.

        Keying by the bare filename stem (the old behaviour) served stale
        data whenever two dub files shared a basename but differed in
        content — e.g. a "Save As" / copied project keeps the original
        dub uids, so its files live under a different project-hash extract
        dir but reuse the same ``<uid>.wav`` names. The global
        ``dub_<uid>_peaks.npy`` cache then drew the OTHER project's
        waveform while the engine played this project's actual audio.

        The signature mixes the absolute path (unique per project +
        subclip) with size + mtime (invalidates on any re-render), all
        from a single ``stat`` — no file read, so it's cheap enough for
        the paint-driven `_request_dub_peaks` call site."""
        try:
            st = os.stat(path)
            raw = f'{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}'
        except OSError:
            raw = os.path.abspath(path)
        return hashlib.md5(raw.encode('utf-8')).hexdigest()[:16]

    def _dub_cache_path(widget, path):
        cache_dir = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform')
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f'dub_{widget._dub_cache_signature(path)}_peaks.npy')

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

    def _onset_marker_pixmap(widget):
        """Pre-rendered onset glyph: a thin vertical line at x=0 plus an
        ellipse-shaped radial gradient centered at the left edge / mid
        height of the pixmap, fading outward. Cached per widget height —
        building a fresh gradient per onset was the per-paint hot path
        on dense speech audio.

        Ellipse trick: QRadialGradient is intrinsically circular, so we
        build a unit-radius circle on a non-uniformly scaled painter
        (x scaled by `width`, y scaled by `h/2`). The asymmetric scale
        stretches the circle into the desired ellipse without needing
        a custom QImage / per-pixel fill."""
        h = max(1, widget.height())
        cache = getattr(widget, '_onset_marker_pm_cache', None)
        if cache is not None and cache[1] == h:
            return cache[0]
        base_str = session.CONFIG.get('timeline', {}).get('onset_marker_color', '#ffd9a0')
        base = QColor(base_str)
        line_color = QColor(base); line_color.setAlpha(85)
        # Both the line and the halo are kept subtle so they read as a
        # background guide rather than competing with the waveform /
        # subtitle text on top.
        grad_center = QColor(base); grad_center.setAlpha(28)
        grad_edge = QColor(base); grad_edge.setAlpha(0)
        width = 32
        pm = QPixmap(width, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)

        cy = h / 2.0
        p.save()
        p.translate(0.0, cy)
        # Asymmetric scale: 1 unit in x = `width` px, 1 unit in y = `cy`
        # px. After this transform the unit-radius circle below is drawn
        # as an ellipse with horizontal radius `width` and vertical
        # radius `cy`, centered at the (transformed) origin = pixmap
        # (0, h/2).
        p.scale(float(width), cy if cy > 0 else 1.0)
        grad = QRadialGradient(0.0, 0.0, 1.0)
        grad.setColorAt(0.0, grad_center)
        grad.setColorAt(1.0, grad_edge)
        # Fill the unit-half-square that covers the whole pixmap in
        # scaled coords: x in [0, 1], y in [-1, 1].
        p.fillRect(QRectF(0.0, -1.0, 1.0, 2.0), QBrush(grad))
        p.restore()

        # Vertical line on top of the gradient so the line stays sharp.
        p.fillRect(QRectF(0.0, 0.0, 1.0, float(h)), line_color)
        p.end()
        widget._onset_marker_pm_cache = (pm, h)
        return pm

    def _dub_onsets_cache_path(widget, path):
        cache_dir = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform')
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f'dub_{widget._dub_cache_signature(path)}_onsets.npy')

    def _request_dub_onsets(widget, path):
        if path in widget.dub_onsets or path in widget.dub_onset_workers:
            return
        cache_file = widget._dub_onsets_cache_path(path)
        if cache_file and os.path.exists(cache_file):
            try:
                widget.dub_onsets[path] = np.load(cache_file).astype(np.float32)
                return
            except Exception:
                pass
        if not os.path.exists(path):
            return
        worker = DubOnsetsWorker(path)
        worker.finished.connect(widget._on_dub_onsets_ready)
        widget.dub_onset_workers[path] = worker
        worker.start()

    def _on_dub_onsets_ready(widget, path, onsets):
        widget.dub_onsets[path] = onsets
        widget.dub_onset_workers.pop(path, None)
        cache_file = widget._dub_onsets_cache_path(path)
        if cache_file:
            try:
                np.save(cache_file, onsets)
            except Exception:
                pass
        widget.update()


def update_subtitles_panel_subtitle_selected(self):
    self.subtitles_panel_qlistwidget.update_content()
    left_panel.update(self)


def _paint_dub_take_rows(widget, painter, subtitle, subtitle_rect, dubs, speaker_color_str):
    """Draw a subtitle's dub playlist as waveform clips stacked in the lower
    part of its subtitle rect (default take on top, alternates below). Each
    row is a real waveform of that take; the audible take is accented with an
    amber outline. Clicking a row auditions that take (solo); its right-edge
    ▲ button makes it the default. Rows register their hit rects into
    ``widget._dub_take_rects`` for mousePressEvent."""
    from subtitld.modules import dub_clip as _dc
    n = len(dubs)
    if n < 2 or subtitle_rect.width() < 24 or subtitle_rect.height() < 22:
        return
    active = next((d for i, d in enumerate(dubs)
                   if not _dc.effective_muted(d, i)), dubs[0])

    pad = 2.0
    gap = 2.0
    # Reserve the top of the (tall) subtitle rect for its text; stack the take
    # rows in the space below, anchored to the clip's bottom.
    text_reserve = min(subtitle_rect.height() * 0.42, 40.0)
    region_top = subtitle_rect.top() + text_reserve
    region_bottom = subtitle_rect.bottom() - pad
    region_h = region_bottom - region_top
    if region_h < 12:
        # Thin lane (speaker-tracks mode) — use nearly the whole rect instead.
        region_top = subtitle_rect.top() + pad
        region_bottom = subtitle_rect.bottom() - pad
        region_h = region_bottom - region_top
        if region_h < 8:
            return
    row_h = max(10.0, min(40.0, (region_h - (n - 1) * gap) / n))
    max_rows = max(1, int((region_h + gap) // (row_h + gap)))
    visible_n = min(n, max_rows)
    y0 = region_bottom - (visible_n * row_h + (visible_n - 1) * gap)

    dub_waveform_color = QColor(session.CONFIG.get('timeline', {}).get('dub_waveform_color', '#ffffffff'))
    cache_max = getattr(widget, '_DUB_PATH_CACHE_MAX', 4096)

    painter.save()
    try:
        for i in range(visible_n):
            dub = dubs[i]
            is_active = dub is active
            is_default = (i == 0)
            ry = y0 + i * (row_h + gap)

            # Horizontal position from the take's clip extent, falling back to the
            # subtitle's own range for takes with no explicit placement.
            try:
                ext_lo, ext_hi = _dc.clip_extent(dub)
            except Exception:
                ext_lo, ext_hi = None, None
            if not ext_hi or (ext_lo is not None and ext_hi <= ext_lo):
                ext_lo = float(subtitle.get('start', 0.0))
                ext_hi = float(subtitle.get('end', ext_lo))
            rx = float(ext_lo) * widget.width_proportion
            rw = max(0.0, (float(ext_hi) - float(ext_lo)) * widget.width_proportion)
            if rw < 2:
                continue
            row = QRectF(rx, ry, rw, row_h)

            # Clip body — speaker colour like the main dub band, brighter for the
            # audible take. Kept near-opaque so the main dub's onset markers /
            # waveform (still drawn underneath) don't bleed through the stack.
            base = QColor(speaker_color_str or '#1a73a8')
            if is_active:
                base = base.lighter(130)
            else:
                base = base.darker(112)
            base.setAlpha(255)
            painter.setPen(Qt.NoPen)
            painter.setBrush(base)
            painter.drawRoundedRect(row, 3.0, 3.0, Qt.AbsoluteSize)

            # Waveform of this take.
            path = dub.get('path')
            peaks = widget.dub_peaks.get(path) if path else None
            if peaks is None and path:
                widget._request_dub_peaks(path)
            if peaks is not None and row_h >= 6:
                mins, maxs, duration = peaks
                count = len(mins)
                if count > 0:
                    cache_h = int(round(row_h))
                    cache_w = max(1, int(round(rw)))
                    cache_key = (path, 0, count, cache_w, cache_h, 'takerow')
                    wf = widget._dub_waveform_path_cache.get(cache_key)
                    if wf is None:
                        scale = cache_h * 0.42
                        ppb = cache_w / count
                        wf = QPainterPath()
                        wf.moveTo(0.0, -float(maxs[0]) * scale)
                        x = ppb
                        for j in range(1, count):
                            wf.lineTo(x, -float(maxs[j]) * scale)
                            x += ppb
                        x -= ppb
                        for j in range(count - 1, -1, -1):
                            wf.lineTo(x, -float(mins[j]) * scale)
                            x -= ppb
                        wf.closeSubpath()
                        if len(widget._dub_waveform_path_cache) > cache_max:
                            for k in list(widget._dub_waveform_path_cache)[:128]:
                                del widget._dub_waveform_path_cache[k]
                        widget._dub_waveform_path_cache[cache_key] = wf
                    painter.save()
                    try:
                        painter.setClipRect(row)
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(dub_waveform_color)
                        cy = row.center().y()
                        painter.translate(rx, cy)
                        painter.drawPath(wf)
                    finally:
                        # restore() alone undoes the clip + transform even if
                        # drawPath raised — never leave the painter wedged with a
                        # stuck clip/translate (that cascades into paint failures).
                        painter.restore()

            # Amber outline on the audible take (drawn over the waveform).
            if is_active:
                painter.setPen(QPen(QColor(255, 214, 74, 235), 1.5))
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(row, 3.0, 3.0, Qt.AbsoluteSize)

            # "Make default" ▲ button (skip the default row).
            promote_rect = None
            if not is_default and row_h >= 12 and rw > 30:
                btn_side = min(row_h - 2.0, 16.0)
                promote_rect = QRectF(row.right() - btn_side - 3.0,
                                      ry + (row_h - btn_side) * 0.5, btn_side, btn_side)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(12, 18, 26, 175))
                painter.drawRoundedRect(promote_rect, 2.0, 2.0, Qt.AbsoluteSize)
                painter.setPen(QColor(224, 236, 246, 235))
                painter.setFont(QFont('Montserrat', max(7, int(btn_side * 0.5)), QFont.Bold))
                painter.drawText(promote_rect, Qt.AlignCenter, '▲')

            if promote_rect is not None:
                widget._dub_take_rects.append((promote_rect, subtitle, dub, 'promote'))
            widget._dub_take_rects.append((QRectF(row), subtitle, dub, 'solo'))
    finally:
        # The inner save (the waveform clip) is already exception-safe; this
        # outer one was not. Anything escaping the loop left the painter with
        # a stuck saved state, which cascades into unbalanced save/restore
        # warnings on later paints.
        painter.restore()


# ---------------------------------------------------------------------------
# Live record take
#
# Painted as an overlay from state owned by RecordController; nothing here
# touches session.SUBTITLE. See modules/live_peaks for why the take is kept
# out of the document while it is being recorded.
# ---------------------------------------------------------------------------
_LIVE_BAND_RATIO = 0.25          # same band as a committed dub in _paint_body
_LIVE_ACCENT = QColor(255, 214, 74, 235)


def live_take_tick(widget, live):
    """Fold newly-recorded buckets into the live strip and invalidate.

    Called ~15Hz from RecordController._live_tick, on the GUI thread.
    """
    wpp = float(getattr(widget, 'width_proportion', 0) or 0)
    if wpp <= 0:
        return
    buf = live.get('buffer')
    if buf is None:
        return

    # Only the buckets not yet folded in — O(new), never O(take).
    start, mins, maxs = buf.read_since(live.get('buckets_drawn', 0))
    if mins is not None and len(mins):
        live['buckets_drawn'] = start + len(mins)

    edge_x = live['end'] * wpp
    last_x = live.get('last_edge_x')
    live['last_edge_x'] = edge_x
    if last_x is None:
        widget.update()
        return

    # While playing, playercontrols already invalidates a full-height strip
    # around the playhead every 33ms: [min(old,new) - 96, max(old,new) + 8].
    # If our growth edge falls inside that, we schedule nothing at all.
    # The window is ASYMMETRIC because that invalidation is: 96px of pad to
    # the LEFT of the head, only 8px to the right.
    head_x = float(session.SUBTITLE.get('position', 0) or 0) * wpp
    strip = getattr(widget.window(), '_timeline_repaint_timer', None)
    if strip is not None and strip.isActive():
        delta = edge_x - head_x
        if -80.0 <= delta <= 4.0:
            return

    lo = int(min(last_x, edge_x)) - 4
    hi = int(max(last_x, edge_x)) + 4
    top = int(getattr(widget, 'subtitle_y', 0) or 0)
    height = int(getattr(widget, 'subtitle_height', widget.height()) or widget.height())
    widget.update(QRect(lo, max(0, top - 2), max(8, hi - lo), height + 4))


def _paint_live_take(widget, painter, live, target_rect):
    """Draw the in-progress take: provisional cue, band and live waveform."""
    wpp = float(getattr(widget, 'width_proportion', 0) or 0)
    if wpp <= 0:
        return
    base = float(live.get('base', 0.0))
    end = float(live.get('end', base))
    if end <= base:
        return
    x0 = base * wpp
    x1 = end * wpp
    if x1 - x0 < 2.0:
        x1 = x0 + 2.0

    # Cues sealed by silence, still waiting for their transcription.
    top = float(getattr(widget, 'subtitle_y', 0) or 0)
    height = float(getattr(widget, 'subtitle_height', 30) or 30)
    for cue in (live.get('pending') or ()):
        cx0 = float(cue['start']) * wpp
        cx1 = float(cue['end']) * wpp
        if cx1 <= cx0:
            continue
        painter.save()
        try:
            painter.setPen(QPen(QColor(255, 214, 74, 120), 1.0, Qt.DashLine))
            painter.setBrush(QColor(255, 214, 74, 26))
            painter.drawRoundedRect(QRectF(cx0, top, cx1 - cx0, height),
                                    3.0, 3.0, Qt.AbsoluteSize)
        finally:
            painter.restore()

    # The cue currently being spoken takes priority over the take-wide span.
    cue_start = live.get('cue_start')
    cue_end = live.get('cue_end')
    if cue_start is not None and cue_end is not None and cue_end > cue_start:
        x0 = float(cue_start) * wpp
        x1 = float(cue_end) * wpp

    if target_rect is not None:
        cue_rect = QRectF(target_rect)
    else:
        # No target subtitle: a provisional cue drawn where a real one would be.
        cue_rect = QRectF(x0, top, x1 - x0, height)
        painter.save()
        try:
            pen = QPen(_LIVE_ACCENT, 1.5, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QColor(255, 214, 74, 40))
            painter.drawRoundedRect(cue_rect, 3.0, 3.0, Qt.AbsoluteSize)
        finally:
            painter.restore()

    # The audio band sits in the bottom quarter of the cue, exactly where a
    # committed dub is drawn, so the handover at commit is invisible.
    band = QRectF(x0,
                  cue_rect.top() + cue_rect.height() * (1.0 - _LIVE_BAND_RATIO),
                  x1 - x0,
                  cue_rect.height() * _LIVE_BAND_RATIO)
    buf = live.get('buffer')
    painter.save()
    try:
        painter.setClipRect(band.adjusted(-1, -1, 1, 1))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(26, 115, 168, 190))
        painter.drawRoundedRect(band, 2.0, 2.0, Qt.AbsoluteSize)

        if buf is not None:
            n = buf.bucket_count
            if n:
                _, mins, maxs = buf.read_since(0)
                if mins is not None and len(mins):
                    bucket_s = buf.bucket_seconds
                    cy = band.center().y()
                    half = band.height() * 0.5
                    peak = max(0.05, float(live.get('peak', 0.0)) or 0.05)
                    scale = half / peak
                    painter.setBrush(QColor(255, 255, 255, 210))
                    # One rect per pixel column, not per bucket: at low zoom
                    # many buckets share a column, at high zoom a column spans
                    # several pixels. Either way the cost tracks pixels shown.
                    step = max(1, int(round(1.0 / max(1e-6, bucket_s * wpp))))
                    for i in range(0, len(mins), step):
                        lo = float(mins[i:i + step].min())
                        hi = float(maxs[i:i + step].max())
                        cx = x0 + (i * bucket_s) * wpp
                        w = max(1.0, step * bucket_s * wpp)
                        top_y = cy - hi * scale
                        h = max(1.0, (hi - lo) * scale)
                        painter.drawRect(QRectF(cx, top_y, w, h))
    finally:
        painter.restore()

    # Leading edge marker, so the user can see it is live.
    painter.save()
    try:
        painter.setPen(QPen(_LIVE_ACCENT, 1.5))
        painter.drawLine(QLineF(x1, cue_rect.top(), x1, cue_rect.bottom()))
    finally:
        painter.restore()


def _dub_playlist_apply(widget, subtitle, dub, promote):
    from subtitld.modules import dub_clip
    if promote:
        dub_clip.promote_dub(subtitle, dub)
    else:
        dub_clip.solo_dub(subtitle, dub)
    # Re-sync so the newly-audible take is loaded, then refresh the views.
    try:
        widget.window().preview_panel_player._audio_device.sync_subtitle_dubs(
            session.SUBTITLE.get('segments', []))
    except Exception:
        pass
    session.set_unsaved()
    widget.update()
    try:
        left_panel.update(widget.window())
    except Exception:
        pass


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
