import hashlib
import os
from bisect import bisect
import numpy as np
import subprocess

from PySide6.QtWidgets import QWidget, QScrollArea, QSizePolicy
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath, QLinearGradient, QFontMetrics
from PySide6.QtCore import Qt, QRectF, QThread, Signal, QMarginsF, QTimer, QMargins

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
                step = 10.0 if delta > 0 else -10.0
                new_zoom = session.CONFIG.get('timeline_zoom', 100.0) + step
                new_zoom = max(10.0, min(490.0, new_zoom))
                if new_zoom != session.CONFIG.get('timeline_zoom', 100.0):
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
        arr = np.asarray(self.samples, dtype=np.float32)
        length = (len(arr) // self.samples_per_bucket) * self.samples_per_bucket
        if length == 0:
            mins = np.array([], dtype=np.float32)
            maxs = np.array([], dtype=np.float32)
        else:
            arr = arr[:length]
            buckets = arr.reshape(-1, self.samples_per_bucket)
            mins = buckets.min(axis=1)
            maxs = buckets.max(axis=1)
        self.finished.emit(self.zoom_key, (mins, maxs, self.samples_per_bucket))
    

class WaveformManager:
    def __init__(self, samples=None, filepath=None):
        self.samples = None
        self.filepath = filepath  # Store filepath for cache key
        self.levels = {}  # zoom_key -> (mins, maxs, samples_per_bucket)
        self.workers = {}  # zoom_key -> worker thread
        self.cache_dir = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform')
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
            self.samples = data.get('samples')
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
        self.samples = np.asarray(samples, dtype=np.float32)
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
        # store; convert to numpy if possible for speed

        mins = np.asarray(mins, dtype=np.float32)
        maxs = np.asarray(maxs, dtype=np.float32)
        self.levels[zoom_key] = (mins, maxs, samples_per_bucket)
        # save to unified cache
        self._save_to_cache()
        # cleanup worker ref
        if zoom_key in self.workers:
            del self.workers[zoom_key]
            
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

        # trigger generation for exact target (use target as zoom_key too), and for our normalised zoom_key
        self._start_worker_if_missing(target)
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


class Timeline(QWidget):
    seek = Signal(float)
    subtitle_clicked = Signal()    

    def __init__(widget, parent=None):
        super().__init__(parent)
        widget.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
        widget.setMouseTracking(True)
        widget.setObjectName('timeline_widget')
        widget.setAutoFillBackground(False)
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
        widget.dub_start_is_clicked = False
        widget.dragging_dub = None
        widget.dragging_dub_offset = 0.0

    def paintEvent(widget, event):
        if not widget.isVisible() or widget.width() <= 0 or widget.height() <= 0:
            return
        
        painter = QPainter(widget)
        scroll_position = widget.parent().parent().horizontalScrollBar().value()
        scroll_width = widget.parent().parent().width()

        painter.setRenderHint(QPainter.Antialiasing)

        grid_pen = QPen(QColor(session.CONFIG.get('timeline', {}).get('grid_color', '#336a7483')), 1, Qt.SolidLine)
        painter.setFont(QFont('Ubuntu Mono', 8))
        
        xpos = 0
        for sec in range(int(session.VIDEO.get('duration', 60))):
            if xpos >= scroll_position and xpos <= (scroll_position + widget.parent().parent().width()):
                if (session.CONFIG.get('timeline_zoom', 1) > 75) or (session.CONFIG.get('timeline_zoom', 1) > 50 and session.CONFIG.get('timeline_zoom', 1) <= 75 and not int((sec % 2))) or (session.CONFIG.get('timeline_zoom', 1) > 25 and session.CONFIG.get('timeline_zoom', 1) <= 50 and not int((sec % 4))) or (session.CONFIG.get('timeline_zoom', 1) <= 25 and not int((sec % 8))):
                    lim_rect = QRectF(
                        xpos + 3,
                        27,
                        50,
                        20
                    )
                    painter.setPen(QColor(session.CONFIG.get('timeline', {}).get('time_text_color', '#806a7483')))
                    painter.drawText(lim_rect, Qt.AlignLeft, utils.get_timeline_time_str(sec))

                if session.CONFIG.get('timeline', {}).get('show_grid', False) and session.CONFIG.get('timeline', {}).get('grid_type', False) == 'seconds':
                    painter.setPen(grid_pen)
                    painter.drawLine(xpos, 0, xpos, widget.height())
            
            xpos += widget.width_proportion

        if session.CONFIG.get('timeline', {}).get('show_grid', False):
            if session.CONFIG.get('timeline', {}).get('grid_type', False) == 'frames':
                painter.setPen(grid_pen)
                xpos = 0.0
                for _ in range(int(session.VIDEO.get('duration', 60) * session.VIDEO['framerate'])):
                    if xpos >= scroll_position and xpos <= (scroll_position + widget.parent().parent().width()):
                        painter.drawLine(xpos, 0, xpos, widget.height())
                    xpos += widget.width_proportion / session.VIDEO['framerate']
            elif session.CONFIG.get('timeline', {}).get('grid_type', False) == 'scenes' and session.VIDEO['scenes']:
                painter.setPen(grid_pen)
                for scene in session.VIDEO['scenes']:
                    xpos = (scene * widget.width_proportion)
                    if xpos >= scroll_position and xpos <= (scroll_position + widget.parent().parent().width()):
                        painter.drawLine(xpos, 0, xpos, widget.height())

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
            visible_start_sec = scroll_position / widget.width_proportion
            visible_end_sec = (scroll_position + scroll_width) / widget.width_proportion
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

            for subtitle in session.SUBTITLE['segments']:
                if (subtitle['start'] / session.VIDEO.get('duration', 0.01)) > ((scroll_position + scroll_width) / widget.width()):
                    break
                elif (subtitle['end']) / session.VIDEO.get('duration', 0.01) < (scroll_position / widget.width()):
                    continue
                else:
                    if session.SUBTITLE.get('selected', False) == subtitle:
                        painter.setPen(QColor(session.CONFIG.get('timeline', {}).get('selected_subtitle_border_color', '#ff304251')))
                        painter.setBrush(QColor(session.CONFIG.get('timeline', {}).get('selected_subtitle_fill_color', '#cc3e5363')))
                    else:
                        painter.setPen(QColor(session.CONFIG.get('timeline', {}).get('subtitle_border_color', '#ff6a7483')))
                        painter.setBrush(QColor(session.CONFIG.get('timeline', {}).get('subtitle_fill_color', '#ccb8cee0')))

                    subtitle_track = [0, 1] # [index, number of tracks]
                    if widget.show_speaker_tracks and session.SPEAKERS:
                        speakers_keys = list(session.SPEAKERS.keys())
                        speaker_name = subtitle.get('speaker', 'A')
                        subtitle_track = [
                            speakers_keys.index(speaker_name) if speaker_name in speakers_keys else 0,
                            len(speakers_keys)
                        ]

                    subtitle_rect = QRectF(
                        subtitle['start'] * widget.width_proportion,
                        widget.subtitle_y + ((widget.subtitle_height / subtitle_track[1]) * subtitle_track[0]),
                        (subtitle['end'] - subtitle['start']) * widget.width_proportion,
                        widget.subtitle_height / subtitle_track[1]
                    )

                    painter.drawRoundedRect(subtitle_rect, 2.0, 2.0, Qt.AbsoluteSize)

                    if subtitle.get('dubbing'):
                        dub = subtitle['dubbing'][0]
                        dub_path = dub.get('path')
                        if dub_path:
                            widget._request_dub_peaks(dub_path)
                            peaks = widget.dub_peaks.get(dub_path)
                            if peaks is not None:
                                mins, maxs, duration = peaks
                                dub_start = dub.get('start', subtitle['start'])
                                dub_x = dub_start * widget.width_proportion
                                dub_w = duration * widget.width_proportion
                                if dub_w > 1:
                                    show_translations = session.CONFIG['translation'].get('engine_options', {}).get('show_translations', False)
                                    band_ratio = 0.25 if show_translations else 0.5
                                    dub_inset = QRectF(
                                        dub_x,
                                        subtitle_rect.top() + subtitle_rect.height() * (1.0 - band_ratio),
                                        dub_w,
                                        subtitle_rect.height() * band_ratio - 2,
                                    )
                                    speaker_color = QColor(session.SPEAKERS.get(subtitle.get('speaker', 'A'), {}).get('color', '#1a73a8'))
                                    border_color = QColor(speaker_color)
                                    border_color.setAlpha(255)
                                    fill_color = QColor(speaker_color)
                                    fill_color.setAlpha(204)
                                    painter.save()
                                    painter.setPen(QPen(border_color, 1))
                                    painter.setBrush(fill_color)
                                    painter.drawRoundedRect(dub_inset, 2.0, 2.0, Qt.AbsoluteSize)

                                    handle_w = 4
                                    handle_rect = QRectF(dub_inset.left() + 1, dub_inset.top() + 2, handle_w, max(0.0, dub_inset.height() - 4))
                                    painter.setPen(Qt.NoPen)
                                    painter.setBrush(QColor(255, 255, 255, 180))
                                    painter.drawRoundedRect(handle_rect, 1.5, 1.5, Qt.AbsoluteSize)

                                    count = len(mins)
                                    if count > 0:
                                        painter.setClipRect(dub_inset)
                                        center = dub_inset.center().y()
                                        scale = dub_inset.height() * 0.45
                                        pixel_per_bucket = dub_inset.width() / count
                                        wf = QPainterPath()
                                        upper = []
                                        x = dub_inset.left()
                                        for i in range(count):
                                            upper.append((x, center - float(maxs[i]) * scale))
                                            x += pixel_per_bucket
                                        lower = []
                                        x -= pixel_per_bucket
                                        for i in range(count - 1, -1, -1):
                                            lower.append((x, center - float(mins[i]) * scale))
                                            x -= pixel_per_bucket
                                        wf.moveTo(upper[0][0], upper[0][1])
                                        for (xx, yy) in upper[1:]:
                                            wf.lineTo(xx, yy)
                                        for (xx, yy) in lower:
                                            wf.lineTo(xx, yy)
                                        wf.closeSubpath()
                                        painter.setPen(Qt.NoPen)
                                        painter.setBrush(QColor(session.CONFIG.get('timeline', {}).get('dub_waveform_color', '#ffffffff')))
                                        painter.drawPath(wf)
                                    painter.restore()

                    if widget.show_speaker_color and session.SPEAKERS.get(subtitle.get('speaker', 'A'), {}).get('color', None):
                        pen = QPen(QColor(session.SPEAKERS[subtitle.get('speaker', 'A')].get('color', '#b8cee0')))
                        pen.setWidth(4)
                        pen.setCapStyle(Qt.RoundCap)
                        painter.setPen(pen)
                        painter.drawLine(subtitle_rect.left() + 4, subtitle_rect.top() + 3, subtitle_rect.right() - 2, subtitle_rect.top() + 3)

                    if session.CONFIG.get('quality_check', {}).get('enabled', False):
                        approved, _, _ = quality_check.check_subtitle(subtitle)
                        if not approved:
                            painter.setPen(QColor('#9e1a1a'))
                        elif session.SUBTITLE.get('selected', False) == subtitle:
                            painter.setPen(QColor(session.CONFIG.get('timeline', {}).get('selected_subtitle_text_color', '#ffffffff')))
                        else:
                            painter.setPen(QColor(session.CONFIG.get('timeline', {}).get('subtitle_text_color', '#ff304251')))
                    else:
                        if session.SUBTITLE.get('selected', False) == subtitle:
                            painter.setPen(QColor(session.CONFIG.get('timeline', {}).get('selected_subtitle_text_color', '#b8cee0')))
                        else:
                            painter.setPen(QColor(session.CONFIG.get('timeline', {}).get('subtitle_text_color', '#304251')))

                    if subtitle.get('dubbing'):
                        subtitle_rect.setHeight(subtitle_rect.height() * 0.75)

                    subtitle_rect -= QMarginsF(26, 6, 26, 6)

                    painter.setFont(QFont('Montserrat', 10))

                    if session.CONFIG['translation'].get('engine_options', {}).get('show_translations', False):                        
                        original_subtitle_rect = subtitle_rect - QMarginsF(0, 0, 0, subtitle_rect.height()*.5)

                        if widget.is_smart_splicing and isinstance(widget.is_smart_splicing, dict) and (subtitle_rect.x() < widget.is_smart_splicing.get('position', original_subtitle_rect.x() + (original_subtitle_rect.width() / 2)) < (subtitle_rect.x() + subtitle_rect.width())):
                            pos = widget.is_smart_splicing.get('position', original_subtitle_rect.x() + (original_subtitle_rect.width() / 2))
                            if 'left' in widget.is_smart_splicing and 'right' in widget.is_smart_splicing:
                                left_side = widget.is_smart_splicing['left']
                                right_side = widget.is_smart_splicing['right']
                                painter.drawText(original_subtitle_rect - QMarginsF(0, 0, (left_side[0] * original_subtitle_rect.width()) + 5, 0), Qt.AlignRight | Qt.AlignTop | Qt.TextWordWrap, left_side[1])
                                painter.drawText(original_subtitle_rect - QMarginsF((right_side[0] * original_subtitle_rect.width()) + 5, 0, 0, 0), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, right_side[1])
                                painter.setPen(QColor("#1a000000"))
                                painter.drawLine(original_subtitle_rect.x() + ((1 - left_side[0]) * original_subtitle_rect.width()), subtitle_rect.top(), original_subtitle_rect.x() + ((1 - left_side[0]) * original_subtitle_rect.width()), subtitle_rect.bottom())
                            if widget.is_smart_splicing['mode'] == 'split':
                                painter.setPen(QColor(session.CONFIG.get('timeline', {}).get('subtitle_fill_color', '#ccb8cee0')))
                                painter.drawLine(pos, subtitle_rect.top() - 6, pos, subtitle_rect.bottom() + 6)
                        else:
                            # painter.drawText(original_subtitle_rect, Qt.AlignLeft | Qt.TextWordWrap, subtitle['text'])
                            painter.drawText(original_subtitle_rect - QMarginsF(0, 5, 0, 5), widget.subtitle_alignment | Qt.TextWordWrap, subtitle['text'])

                        translated_subtitle_rect = subtitle_rect - QMarginsF(0, subtitle_rect.height()*.5, 0, 0)

                        if session.SUBTITLE.get('selected', False) == subtitle:
                            painter.setPen(QColor(session.CONFIG.get('timeline', {}).get('selected_subtitle_text_color', '#b8cee0')))
                        else:
                            painter.setPen(QColor(session.CONFIG.get('timeline', {}).get('subtitle_text_color', '#304251')))

                        painter.drawText(translated_subtitle_rect - QMarginsF(0, 5, 0, 5), widget.subtitle_alignment | Qt.TextWordWrap, subtitle.get('translations', {}).get(session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us'), ''))
                        
                        painter.setBrush(QColor(session.CONFIG.get('timeline', {}).get('subtitle_text_color', "#40304251")))
                        painter.setPen(Qt.NoPen)

                        r = 3

                        lfont = QFont('Montserrat', 6)
                        lfont.setBold(True)
                        painter.setFont(lfont)

                        language_name = session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us').upper()

                        lang_text_metrics = QFontMetrics(lfont).boundingRect(language_name)
                        lang_text_metrics += QMargins(1, 2, 1, 2)

                        badge = QPainterPath()
                        badge.moveTo(translated_subtitle_rect.left(), translated_subtitle_rect.top() - 1)
                        badge.lineTo(translated_subtitle_rect.right(), translated_subtitle_rect.top() - 1)
                        badge.lineTo(translated_subtitle_rect.right(), translated_subtitle_rect.top() + lang_text_metrics.height() - r)
                        badge.arcTo(translated_subtitle_rect.right() - (r*2), translated_subtitle_rect.top() + lang_text_metrics.height() - (r*2), (r*2), (r*2), 0, -90)
                        badge.lineTo(translated_subtitle_rect.right() - r, translated_subtitle_rect.top() + lang_text_metrics.height())
                        badge.arcTo(translated_subtitle_rect.right() - lang_text_metrics.width() - r, translated_subtitle_rect.top() + lang_text_metrics.height() - (r*2), (r*2), (r*2), -90, -90)
                        badge.lineTo(translated_subtitle_rect.right() - lang_text_metrics.width() - r, translated_subtitle_rect.top())
                        badge.lineTo(translated_subtitle_rect.left(), translated_subtitle_rect.top())
                        badge.closeSubpath()

                        painter.drawPath(badge)

                        painter.setPen(QColor('#80ffffff'))
                        painter.drawText(translated_subtitle_rect.right() - lang_text_metrics.width(), translated_subtitle_rect.top(), lang_text_metrics.width(), lang_text_metrics.height(), Qt.AlignLeft | Qt.AlignVCenter, language_name)
                    else:
                        original_subtitle_rect = subtitle_rect - QMarginsF(0, 5, 0, 5)
                        if widget.is_smart_splicing and isinstance(widget.is_smart_splicing, dict) and (subtitle_rect.x() < widget.is_smart_splicing.get('position', original_subtitle_rect.x() + (original_subtitle_rect.width() / 2)) < (subtitle_rect.x() + subtitle_rect.width())):
                            pos = widget.is_smart_splicing.get('position', original_subtitle_rect.x() + (original_subtitle_rect.width() / 2))
                            if 'left' in widget.is_smart_splicing and 'right' in widget.is_smart_splicing:
                                left_side = widget.is_smart_splicing['left']
                                right_side = widget.is_smart_splicing['right']
                                painter.drawText(original_subtitle_rect - QMarginsF(0, 0, (left_side[0] * original_subtitle_rect.width()) + 5, 0), Qt.AlignRight | Qt.AlignTop | Qt.TextWordWrap, left_side[1])
                                painter.drawText(original_subtitle_rect - QMarginsF((right_side[0] * original_subtitle_rect.width()) + 5, 0, 0, 0), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, right_side[1])
                                painter.setPen(QColor("#1a000000"))
                                painter.drawLine(original_subtitle_rect.x() + ((1 - left_side[0]) * original_subtitle_rect.width()), subtitle_rect.top(), original_subtitle_rect.x() + ((1 - left_side[0]) * original_subtitle_rect.width()), subtitle_rect.bottom())
                            if widget.is_smart_splicing['mode'] == 'split':
                                painter.setPen(QColor(session.CONFIG.get('timeline', {}).get('subtitle_fill_color', '#ccb8cee0')))
                                painter.drawLine(pos, subtitle_rect.top() - 6, pos, subtitle_rect.bottom() + 6)
                        else:
                            painter.drawText(original_subtitle_rect, widget.subtitle_alignment | Qt.TextWordWrap, subtitle['text'])

                    if subtitle == widget.subtitle_under_the_cursor and widget.show_limiters and ((subtitle['end'] - subtitle['start']) * widget.width_proportion) > 40:
                        if session.SUBTITLE.get('selected', False) == subtitle:
                            painter.setBrush(QColor(session.CONFIG.get('timeline', {}).get('selected_subtitle_fill_color', '#cc3e5363')))
                        else:
                            painter.setBrush(QColor(session.CONFIG.get('timeline', {}).get('subtitle_fill_color', '#ccb8cee0')))

                        painter.setPen(Qt.NoPen)
                        lim_rect = QRectF(
                            (subtitle['start'] * widget.width_proportion) + 2,
                            widget.subtitle_y + ((widget.subtitle_height / subtitle_track[1]) * subtitle_track[0]) + 2,
                            18,
                            widget.subtitle_height / subtitle_track[1] - 4
                        )

                        painter.drawRoundedRect(lim_rect, 1.0, 1.0, Qt.AbsoluteSize)

                        lx = 1
                        for _ in range(2):
                            if session.SUBTITLE.get('selected', False) == subtitle:
                                lpen = QPen(QColor('#07000000') if lx % 2 else QColor(session.CONFIG.get('timeline', {}).get('selected_subtitle_arrow_color', '#ff969696')), 2)
                            else:
                                lpen = QPen(QColor('#07000000') if lx % 2 else QColor(session.CONFIG.get('timeline', {}).get('subtitle_arrow_color', '#ff969696')), 2)

                            painter.setPen(lpen)
                            painter.setBrush(Qt.NoBrush)
                            path = QPainterPath()
                            path.moveTo(lim_rect.center().x() + 2 + lx, lim_rect.center().y() - 10)
                            path.lineTo(lim_rect.center().x() - 1 + lx, lim_rect.center().y())
                            path.lineTo(lim_rect.center().x() + 2 + lx, lim_rect.center().y() + 10)
                            painter.drawPath(path)
                            lx -= 1

                        if session.SUBTITLE.get('selected', False) == subtitle:
                            painter.setBrush(QColor(session.CONFIG.get('timeline', {}).get('selected_subtitle_fill_color', '#cc3e5363')))
                        else:
                            painter.setBrush(QColor(session.CONFIG.get('timeline', {}).get('subtitle_fill_color', '#ccb8cee0')))

                        painter.setPen(Qt.NoPen)
                        lim_rect = QRectF(
                            (subtitle['start'] * widget.width_proportion) + ((subtitle['end'] - subtitle['start']) * widget.width_proportion) - 20,
                            widget.subtitle_y + ((widget.subtitle_height / subtitle_track[1]) * subtitle_track[0]) + 2,
                            18,
                            widget.subtitle_height / subtitle_track[1] - 4
                        )

                        painter.drawRoundedRect(lim_rect, 1.0, 1.0, Qt.AbsoluteSize)

                        lx = 1
                        for _ in range(2):
                            if session.SUBTITLE.get('selected', False) == subtitle:
                                lpen = QPen(QColor('#07000000') if lx % 2 else QColor(session.CONFIG.get('timeline', {}).get('selected_subtitle_arrow_color', '#ff969696')), 2)
                            else:
                                lpen = QPen(QColor('#07000000') if lx % 2 else QColor(session.CONFIG.get('timeline', {}).get('subtitle_arrow_color', '#ff969696')), 2)

                            painter.setPen(lpen)
                            painter.setBrush(Qt.NoBrush)
                            path = QPainterPath()
                            path.moveTo(lim_rect.center().x() + lx, lim_rect.center().y() - 10)
                            path.lineTo(lim_rect.center().x() + 3 + lx, lim_rect.center().y())
                            path.lineTo(lim_rect.center().x() + lx, lim_rect.center().y() + 10)
                            painter.drawPath(path)
                            lx -= 1

            painter.setOpacity(1)

        if bool(widget.show_tug_of_war):
            tug_of_war_pen = QPen(QColor(session.CONFIG.get('timeline', {}).get('selected_subtitle_arrow_color', '#ff969696')), 4, Qt.SolidLine, Qt.RoundCap)
            painter.setPen(tug_of_war_pen)
            xpos = int(widget.show_tug_of_war * widget.width_proportion) - 4
            y_tug_pos = 0
            for _ in range(6):
                painter.drawLine(xpos, int(widget.subtitle_y + 8 + y_tug_pos), int(xpos + 8), int(widget.subtitle_y + 8 + y_tug_pos))
                # painter.drawLine(xpos, widget.subtitle_y + 8 + y_tug_pos, xpos + 8, widget.subtitle_y + widget.subtitle_height - 8)
                y_tug_pos += (widget.subtitle_height - 8) / 6

        if session.SUBTITLE.get('position', 0) is not None:
            painter.setPen(QPen(QColor(session.CONFIG.get('timeline', {}).get('cursor_color', '#ccff0000')), 2, Qt.SolidLine))
            cursor_pos = int(session.SUBTITLE.get('position', 0) * widget.width_proportion)
            painter.drawLine(cursor_pos, 0, cursor_pos, widget.height())

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

        painter.end()
        event.accept()

    def mousePressEvent(widget, event):
        dub_hit = widget._dub_handle_at_position(event.pos())
        if dub_hit is not None:
            _, dub = dub_hit
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

        widget.update()

    def mouseReleaseEvent(widget, event):
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
        if widget.dub_start_is_clicked and widget.dragging_dub is not None:
            new_start = (event.pos().x() - widget.dragging_dub_offset) / widget.width_proportion
            widget.dragging_dub['start'] = max(0.0, new_start)
            widget.update()
            return

        if not widget.is_cursor_pressing:
            if widget._dub_handle_at_position(event.pos()) is not None:
                widget.setCursor(Qt.SizeHorCursor)
            else:
                widget.unsetCursor()

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

    def _dub_hit_at_position(widget, pos, handle_only=False):
        """Return (subtitle, dub) if pos is over a dub clip; if handle_only, restrict to the leftmost 8px."""
        show_translations = session.CONFIG['translation'].get('engine_options', {}).get('show_translations', False)
        band_ratio = 0.25 if show_translations else 0.5
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
            hit_w = 8 if handle_only else duration * widget.width_proportion

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

            if dub_x <= pos.x() <= dub_x + hit_w and top <= pos.y() <= bottom:
                return (subtitle, dub)

        return None

    def _dub_handle_at_position(widget, pos):
        return widget._dub_hit_at_position(pos, handle_only=True)

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

    self.preview_panel_player.position_changed_signal.connect(lambda: self.timeline_widget.update())

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
