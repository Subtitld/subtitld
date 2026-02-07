import os
from bisect import bisect
import numpy as np
import subprocess

from PySide6.QtWidgets import QWidget, QScrollArea, QSizePolicy
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath, QLinearGradient, QFontMetrics
from PySide6.QtCore import Qt, QRectF, QThread, Signal, QMarginsF, QTimer

from subtitld.modules import session
from subtitld.modules import utils
from subtitld.modules import subtitles
from subtitld.modules import quality_check

from subtitld.interface import left_panel
from subtitld.interface.translation import _

# from subtitld.modules import history
# from subtitld.modules import utils
# from subtitld.interface import subtitles_panel, player


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
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

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
    def __init__(self, samples=None):
        self.samples = None
        self.levels = {}  # zoom_key -> (mins, maxs, samples_per_bucket)
        self.workers = {}  # zoom_key -> worker thread
        self.cache_dir = os.path.expanduser("~/.myapp_waveform_cache")
        if samples is not None:
            self.set_samples(samples)

    def set_samples(self, samples):
        self.samples = np.asarray(samples, dtype=np.float32)
        self._start_worker_if_missing(512)

    def _cache_path(self, zoom_key):
        if not self.cache_dir:
            return None
        return os.path.join(self.cache_dir, f'waveform_level_{zoom_key}.npy')

    def _start_worker_if_missing(self, zoom_key):
        if zoom_key in self.levels or zoom_key in self.workers:
            return
        # try load from disk cache if present
        cache_path = self._cache_path(zoom_key)
        if cache_path and os.path.exists(cache_path):
            try:
                arr = np.load(cache_path, allow_pickle=True)
                self.levels[zoom_key] = (arr[0], arr[1], int(arr[2]))
                return
            except Exception:
                pass
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
        # try write to disk cache
        cache_path = self._cache_path(zoom_key)
        if cache_path:
            try:
                np.save(cache_path, np.array([mins, maxs, samples_per_bucket], dtype=object), allow_pickle=True)
            except Exception:
                pass
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
        widget.width_proportion = widget.width() / session.VIDEO.get('duration', 0.01)

        widget.audio_thread = AudioLoaderThread()
        widget.audio_thread.finished.connect(widget.on_waveform_loaded)

        widget.waveform_manager = WaveformManager()
        widget.waveform_height = widget.height() - widget.subtitle_y - 10
        widget.waveform_y = 45

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

                    subtitle_rect = QRectF(
                        subtitle['start'] * widget.width_proportion,
                        widget.subtitle_y,
                        (subtitle['end'] - subtitle['start']) * widget.width_proportion,
                        widget.subtitle_height
                    )

                    painter.drawRoundedRect(subtitle_rect, 2.0, 2.0, Qt.AbsoluteSize)

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


                    subtitle_rect -= QMarginsF(22, 2, 22, 2)
                    painter.drawText(subtitle_rect, Qt.AlignCenter | Qt.TextWordWrap, subtitle['text'])

                    if widget.show_limiters and ((subtitle['end'] - subtitle['start']) * widget.width_proportion) > 40:
                        if session.SUBTITLE.get('selected', False) == subtitle:
                            painter.setBrush(QColor(session.CONFIG.get('timeline', {}).get('selected_subtitle_fill_color', '#cc3e5363')))
                        else:
                            painter.setBrush(QColor(session.CONFIG.get('timeline', {}).get('subtitle_fill_color', '#ccb8cee0')))

                        painter.setPen(Qt.NoPen)
                        lim_rect = QRectF(
                            (subtitle['start'] * widget.width_proportion) + 2,
                            widget.subtitle_y + 2,
                            18,
                            widget.subtitle_height - 4
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
                            widget.subtitle_y + 2,
                            18,
                            widget.subtitle_height - 4
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
        scroll_position = widget.parent().parent().horizontalScrollBar().value()
        scroll_width = widget.parent().parent().width()

        cursor_is_out_of_view = bool(session.SUBTITLE.get('position', 0) * widget.width_proportion < widget.parent().parent().horizontalScrollBar().value() or session.SUBTITLE.get('position', 0) * widget.width_proportion > widget.parent().parent().width() + widget.parent().parent().horizontalScrollBar().value())

        widget.is_cursor_pressing = True
        session.SUBTITLE['selected'] = None

        for subtitle in session.SUBTITLE['segments']:
            if (subtitle['start'] / session.VIDEO.get('duration', 0.01)) > ((scroll_position + scroll_width) / widget.width()):
                break
            elif (subtitle['end']) / session.VIDEO.get('duration', 0.01) < (scroll_position / widget.width()):
                continue
            else:
                if event.pos().y() > widget.subtitle_y and event.pos().y() < (widget.subtitle_height + widget.subtitle_y) and (((event.pos().x()) / widget.width_proportion) > subtitle['start'] and ((event.pos().x()) / widget.width_proportion) < (subtitle['end'])):
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
        if (widget.subtitle_is_clicked or widget.subtitle_start_is_clicked or widget.subtitle_end_is_clicked):
            session.set_unsaved()
        widget.subtitle_is_clicked = False
        widget.subtitle_start_is_clicked = False
        widget.subtitle_end_is_clicked = False
        widget.is_cursor_pressing = False
        widget.tug_of_war_pressed = False
        widget.update()
        # subtitles_panel.update_subtitles_panel_widget_vision_content(widget.window())
        event.accept()

    def mouseMoveEvent(widget, event):
        widget.show_limiters = bool(event.pos().y() > widget.subtitle_y and event.pos().y() < (widget.subtitle_height + widget.subtitle_y))

        cursor_time_position = event.pos().x() / widget.width_proportion #(event.pos().x() / widget.width()) * session.VIDEO.get('duration', 60)
        cursor_tug_of_war_range = 10 / widget.width_proportion

        subtitle_under_the_cursor = subtitles.subtitle_under_current_position(position=cursor_time_position)
        last, next = subtitles.get_adjacent_subtitles(position=cursor_time_position)
        
        if not widget.tug_of_war_pressed:
            widget.show_tug_of_war = False

        if subtitle_under_the_cursor:
            if next and subtitle_under_the_cursor['end'] - (cursor_tug_of_war_range * .5) < cursor_time_position < subtitle_under_the_cursor['end'] + (cursor_tug_of_war_range*.5) and subtitle_under_the_cursor['end'] + .001 > next['start'] - .02:
                widget.show_tug_of_war = subtitle_under_the_cursor['end'] + .0005
        
            if last and subtitle_under_the_cursor['start'] - (cursor_tug_of_war_range*.5) < cursor_time_position < subtitle_under_the_cursor['start'] + (cursor_tug_of_war_range*.5) and subtitle_under_the_cursor['start'] - .001 < last['end'] + .02:
                widget.show_tug_of_war = subtitle_under_the_cursor['start'] - .0005
            
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
        widget.audio_thread.filepath = filepath
        widget.audio_thread.start()
    
    def on_waveform_loaded(widget, samples, samplerate):
        session.VIDEO["samplerate"] = samplerate
        widget.waveform_manager.set_samples(samples)
        QTimer.singleShot(1000, lambda: widget.update()) 
     
        
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
        "ffmpeg", "-v", "error", "-i", filepath,
        "-ac", "1",             # mono
        "-ar", str(samplerate), # resample rate
        "-f", "f32le", "-"      # raw 32-bit float to stdout
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, check=True)
    raw = np.frombuffer(proc.stdout, dtype=np.float32).copy()
    # Normalize
    if raw.size > 0:
        raw /= np.max(np.abs(raw))
    return raw, samplerate
