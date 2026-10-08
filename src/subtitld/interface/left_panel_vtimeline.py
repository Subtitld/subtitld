"""The vertical timeline: the timeline on its side, as a left-panel tab.

Time runs down the tab. Across it, side by side: the time ruler, the
waveform, and the subtitles, each a block from its start to its end with its
text wrapped inside and its dub clips along its right side. It is drawn in
the timeline's colours, follows the timeline's options (snap, grid,
scrolling, speaker colours and tracks, onset markers) and edits as the
timeline does: a click on a subtitle selects it, dragging it moves it,
dragging its top or bottom edge moves its start or end, and dragging where
two subtitles meet moves both; a click anywhere else seeks there. Ctrl+wheel
zooms, around the pointer.
"""

import os
import time
from bisect import bisect_left

import numpy as np
from PySide6.QtCore import QEvent, QRectF, QMarginsF, QPointF, QSize, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetrics, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.modules import quality_check, session, subtitles, utils


TOP = 10                # px above 0 s, so its label is not cut
BOTTOM = 30             # px below the end
RULER_WIDTH = 46
WAVEFORM_WIDTH = 52
LANE_GAP = 6
RIGHT_MARGIN = 8
EDGE = 20               # px from a block's top or bottom that grab that edge, as on the timeline
EDGE_SHOWN = 40         # px a block needs before its edges light up, as on the timeline
TUG_REACH = 5           # px either side of where two subtitles meet that grab both
TEXT_PADDING = QMarginsF(16, 11, 14, 11)
CLIP_WIDTH = 24         # px along a block's right side for its dub clip (at most a quarter of it)
AUTOSCROLL_ZONE = 28    # px from the view's top or bottom that scroll a drag
FOLLOW_DEADBAND = 2     # px of drift "follow" lets be, as the timeline does

DEFAULT_ZOOM = 40.0     # px per second
ZOOM_RANGE = (8.0, 400.0)
ZOOM_STEP = 1.25

# Seconds between ruler labels: the first that leaves room for one.
LABEL_STEPS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)
LABEL_SPACING = 34      # px


def _config():
    config = session.CONFIG.setdefault('vertical_timeline', {})
    try:
        config['zoom'] = max(ZOOM_RANGE[0], min(ZOOM_RANGE[1], float(config.get('zoom', DEFAULT_ZOOM))))
    except (TypeError, ValueError):
        config['zoom'] = DEFAULT_ZOOM
    return config


def _timeline_config():
    return session.CONFIG.get('timeline') or {}


def _duration():
    """The video's length; without a video, a little past the last subtitle."""
    duration = float(session.VIDEO.get('duration') or 0.0)
    if duration > 0:
        return duration
    segments = session.SUBTITLE.get('segments') or []
    return max([60.0] + [segment['end'] + 10.0 for segment in segments])


def _neighbours(subtitle):
    """The subtitles before and after this one in the list, or None."""
    segments = session.SUBTITLE.get('segments') or []
    try:
        index = segments.index(subtitle)
    except ValueError:
        return None, None
    return (segments[index - 1] if index > 0 else None,
            segments[index + 1] if index + 1 < len(segments) else None)


def _glued(earlier, later):
    """Whether two subtitles touch, as the timeline leaves them when it
    snaps one to the other (a millisecond apart)."""
    return round(earlier['end'] + .001, 3) == round(later['start'], 3)


def _shortest(subtitle):
    """How short a drag may make the subtitle: the minimum length, or its
    own length if it is shorter already."""
    minimum = float((session.CONFIG.get('default_values') or {}).get('minimum_subtitle_width', 1.0) or 0.0)
    return min(minimum, subtitle['end'] - subtitle['start'])


def _pushes(push):
    """Whether a drag moves a touching neighbour's edge with it: the
    timeline's "move nearest" option, or a drag where two subtitles meet."""
    return bool(_timeline_config().get('snap_move_nereast', False)) if push is None else bool(push)


def _snap_to_grid(position, cfg):
    """The position on the grid line it is close to, as the timeline
    snaps; or the position itself."""
    value = float(cfg.get('snap_value', .1) or .1)
    grid_type = cfg.get('grid_type', False)
    if grid_type == 'frames':
        step = 1.0 / float(session.VIDEO.get('framerate') or 25)
        return round(position / step) * step
    if grid_type == 'seconds':
        nearest = float(round(position))
        return nearest if abs(position - nearest) < value else position
    if grid_type == 'scenes':
        scenes = session.VIDEO.get('scenes') or []
        if scenes:
            nearest = min(scenes, key=lambda scene: abs(scene - position))
            if abs(position - nearest) < value:
                return float(nearest)
    return position


def snap_start(subtitle, position, push=None):
    """Where dragging the subtitle's start to `position` puts it."""
    cfg = _timeline_config()
    previous, _following = _neighbours(subtitle)
    pushing = _pushes(push) and previous is not None and _glued(previous, subtitle)
    position = max(0.0, min(position, subtitle['end'] - _shortest(subtitle)))
    if pushing:     # the previous one gives way, down to its own minimum
        position = max(position, previous['start'] + _shortest(previous) + .001)
    if not cfg.get('snap', True):
        return position
    value = float(cfg.get('snap_value', .1) or .1)
    if previous is not None and not pushing and cfg.get('snap_limits', True) and position < previous['end'] + value:
        return previous['end'] + .001
    if cfg.get('snap_grid', False):
        return _snap_to_grid(position, cfg)
    return position


def snap_end(subtitle, position, push=None):
    """Where dragging the subtitle's end to `position` puts it."""
    cfg = _timeline_config()
    _previous, following = _neighbours(subtitle)
    pushing = _pushes(push) and following is not None and _glued(subtitle, following)
    position = max(subtitle['start'] + _shortest(subtitle), min(position, _duration()))
    if pushing:
        position = min(position, following['end'] - _shortest(following) - .001)
    if not cfg.get('snap', True):
        return position
    value = float(cfg.get('snap_value', .1) or .1)
    if following is not None and not pushing and cfg.get('snap_limits', True) and position > following['start'] - value:
        return following['start'] - .001
    if cfg.get('snap_grid', False):
        return _snap_to_grid(position, cfg)
    return position


def snap_move(subtitle, position):
    """Where dragging the whole subtitle to start at `position` puts it."""
    cfg = _timeline_config()
    length = subtitle['end'] - subtitle['start']
    position = max(0.0, min(position, _duration() - length))
    if not cfg.get('snap', True):
        return position
    previous, following = _neighbours(subtitle)
    value = float(cfg.get('snap_value', .1) or .1)
    if cfg.get('snap_moving', True):
        if following is not None and position + length > following['start'] - value:
            return following['start'] - length - .001
        if previous is not None and position < previous['end'] + value:
            return previous['end'] + .001
    if cfg.get('snap_grid', False):
        return _snap_to_grid(position, cfg)
    return position


def _move(mode, subtitle, position, record, push=None):
    """Apply a drag through the subtitle helpers, as the timeline does —
    they also carry locked dubs along and push a touching neighbour."""
    push = _pushes(push)
    if mode == 'body':
        delta = position - subtitle['start']
        subtitles.move_subtitle(selected_subtitle=subtitle, amount=delta, record=record)
    elif mode == 'start':
        subtitles.move_start_subtitle(selected_subtitle=subtitle, absolute_time=position, move_nereast=push, record=record)
    elif mode == 'end':
        subtitles.move_end_subtitle(selected_subtitle=subtitle, absolute_time=position, move_nereast=push, record=record)


def _arrow_cursor(direction):
    """The timeline's edge cursors, turned: an arrow pointing 'up' (a
    start) or 'down' (an end)."""
    size = 32
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(0, 0, 0, 220), 2))
    painter.setBrush(QColor(255, 255, 255, 240))
    mid = size / 2
    tip, head, tail = (4, 14, 26) if direction == 'up' else (size - 4, size - 14, size - 26)
    painter.drawPolygon(QPolygonF([QPointF(mid, tip), QPointF(mid - 6, head), QPointF(mid - 2, head),
                                   QPointF(mid - 2, tail), QPointF(mid + 2, tail), QPointF(mid + 2, head),
                                   QPointF(mid + 6, head)]))
    painter.end()
    return QCursor(pixmap, int(mid), tip)


def _rounded_path(rect, radius=3.0, square_top_right=False, square_bottom_right=False):
    """A rounded rectangle, with either right-hand corner left square."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    r = max(0.0, min(radius, w / 2, h / 2))
    path = QPainterPath()
    path.moveTo(x + r, y)
    if square_top_right:
        path.lineTo(x + w, y)
    else:
        path.lineTo(x + w - r, y)
        path.arcTo(x + w - 2 * r, y, 2 * r, 2 * r, 90, -90)
    if square_bottom_right:
        path.lineTo(x + w, y + h)
    else:
        path.lineTo(x + w, y + h - r)
        path.arcTo(x + w - 2 * r, y + h - 2 * r, 2 * r, 2 * r, 0, -90)
    path.lineTo(x + r, y + h)
    path.arcTo(x, y + h - 2 * r, 2 * r, 2 * r, 270, -90)
    path.lineTo(x, y + r)
    path.arcTo(x, y, 2 * r, 2 * r, 180, -90)
    path.closeSubpath()
    return path


def _paint_padlock(painter, cx, cy):
    """The timeline's padlock glyph, centred on (cx, cy)."""
    painter.setPen(QPen(QColor(255, 255, 255, 230), 1.2))
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(QRectF(cx - 2.4, cy - 4.0, 4.8, 4.4), 0, 180 * 16)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(255, 255, 255, 230))
    painter.drawRoundedRect(QRectF(cx - 3.2, cy - 0.5, 6.4, 5.0), 1.0, 1.0)


class VerticalTimeline(QWidget):
    def __init__(self, window, scroll, parent=None):
        super().__init__(parent)
        self.window_ref = window
        self.scroll = scroll
        self.setObjectName('vtimeline_canvas')
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)

        self.mode = None            # 'seek', 'start', 'end', 'body' or 'locked' while pressed
        self.grabbed = None         # the subtitle being dragged
        self.grab_offset = 0.0      # px from the grabbed edge, or s from the start
        self.push = None            # a drag where two subtitles meet moves both
        self.recorded = False       # this drag has its undo step
        self.moved = False
        self.hover = None           # (id(subtitle), zone) under the pointer
        self.tug = None             # (earlier, later) meeting under the pointer
        self.last_pointer_y = None
        self._last_seek_time = 0.0
        self._last_paint_error = None
        self._ruler_label = None
        self._ruler_width = RULER_WIDTH
        self._onset_pixmap = None
        self._clip_paths = {}
        self._up_cursor = _arrow_cursor('up')
        self._down_cursor = _arrow_cursor('down')

    # -- geometry --------------------------------------------------------------

    @property
    def pps(self):
        return _config()['zoom']

    def y_at(self, seconds):
        return TOP + seconds * self.pps

    def time_at(self, y):
        return (y - TOP) / self.pps

    def full_height(self):
        return int(TOP + _duration() * self.pps + BOTTOM)

    def ruler_width(self):
        """Wide enough for the longest label: an hour in, they gain a field."""
        label = utils.get_timeline_time_str(int(_duration()))
        if label != self._ruler_label:
            self._ruler_label = label
            self._ruler_width = max(RULER_WIDTH, QFontMetrics(QFont('Ubuntu Mono', 8)).horizontalAdvance(label) + 18)
        return self._ruler_width

    def lanes(self):
        """x ranges of the ruler, the waveform and the subtitles."""
        ruler = (0.0, float(self.ruler_width()))
        waveform = (ruler[1], ruler[1] + WAVEFORM_WIDTH)
        subs = (waveform[1] + LANE_GAP, max(waveform[1] + LANE_GAP + 20.0, self.width() - RIGHT_MARGIN))
        return ruler, waveform, subs

    def timeline(self):
        return getattr(self.window_ref, 'timeline_widget', None)

    def option(self, name, default=False):
        """A timeline display option: what the timeline shows now, which
        its buttons set before they save it."""
        return getattr(self.timeline(), name, _timeline_config().get(name, default))

    def speaker_columns(self):
        """The subtitles lane split per speaker, as the timeline splits its
        track when it shows speaker tracks: {name: (left, right)}."""
        _ruler, _waveform, (left, right) = self.lanes()
        names = list(session.SPEAKERS.keys()) if self.option('show_speaker_tracks') and session.SPEAKERS else []
        if not names:
            return {}
        width = (right - left) / len(names)
        return {name: (left + width * i, left + width * (i + 1)) for i, name in enumerate(names)}

    def block_rect(self, subtitle, columns=None):
        _ruler, _waveform, (left, right) = self.lanes()
        if columns:
            left, right = columns.get(subtitle.get('speaker', 'A'), next(iter(columns.values())))
            left += 1
            right -= 1
        top = self.y_at(subtitle['start'])
        return QRectF(left, top, right - left, max(1.0, (subtitle['end'] - subtitle['start']) * self.pps))

    def visible_segments(self, top_time, bottom_time):
        """The shown subtitles that reach into [top_time, bottom_time]."""
        segments = session.SUBTITLE.get('segments') or []
        starts = [segment['start'] for segment in segments]
        # A subtitle long enough to start above the view still reaches in:
        # look back from the first that starts inside it.
        index = max(0, bisect_left(starts, top_time) - 1)
        while index > 0 and segments[index - 1]['end'] > top_time:
            index -= 1
        result = []
        for segment in segments[index:]:
            if segment['start'] > bottom_time:
                break
            if segment['end'] < top_time:
                continue
            if (session.SPEAKERS.get(segment.get('speaker', 'A')) or {}).get('hidden'):
                continue
            result.append(segment)
        return result

    def edge_zone(self, rect):
        """px from a block's top or bottom that grab that edge: the
        timeline's, leaving the middle third of a short block to move it."""
        return min(EDGE, rect.height() / 3)

    def hit(self, pos):
        """(subtitle, zone) under a point: zone 'start' or 'end' near the
        block's top or bottom edge, else 'body'. The selected subtitle is
        drawn on top, so it wins."""
        x, y = pos.x(), pos.y()
        _ruler, _waveform, (left, right) = self.lanes()
        if not left <= x <= right:
            return None, None
        seconds = self.time_at(y)
        columns = self.speaker_columns()
        candidates = self.visible_segments(seconds - 0.5, seconds + 0.5)
        selected = session.SUBTITLE.get('selected')
        if selected in candidates:
            candidates.remove(selected)
            candidates.append(selected)
        for subtitle in reversed(candidates):
            rect = self.block_rect(subtitle, columns)
            if not (rect.left() <= x <= rect.right() and rect.top() <= y <= rect.bottom()):
                continue
            edge = self.edge_zone(rect)
            if y - rect.top() < edge:
                return subtitle, 'start'
            if rect.bottom() - y < edge:
                return subtitle, 'end'
            return subtitle, 'body'
        return None, None

    def junction_at(self, pos):
        """(earlier, later) when the point is where two touching subtitles
        meet: the timeline's tug of war, which drags both edges at once."""
        x, y = pos.x(), pos.y()
        seconds = self.time_at(y)
        reach = TUG_REACH / self.pps
        columns = self.speaker_columns()
        shown = self.visible_segments(seconds - reach - .01, seconds + reach + .01)
        for earlier, later in zip(shown, shown[1:]):
            if not _glued(earlier, later) or earlier.get('locked') or later.get('locked'):
                continue
            if abs(seconds - (earlier['end'] + later['start']) / 2) > reach:
                continue
            spans = [self.block_rect(earlier, columns), self.block_rect(later, columns)]
            if any(rect.left() <= x <= rect.right() for rect in spans):
                return earlier, later
        return None

    # -- painting --------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            self._paint(painter, event.rect())
        except Exception as error:      # one bad frame must not stop the next
            message = repr(error)
            if message != self._last_paint_error:
                self._last_paint_error = message
                import sys
                import traceback
                traceback.print_exc(file=sys.stderr)
        finally:
            painter.end()

    def view_times(self):
        """The times at the view's top and bottom."""
        bar = self.scroll.verticalScrollBar()
        return (max(0.0, self.time_at(bar.value())),
                min(_duration(), self.time_at(bar.value() + self.scroll.viewport().height())))

    def _paint(self, painter, rect):
        painter.setRenderHint(QPainter.Antialiasing)
        cfg = _timeline_config()
        duration = _duration()
        ruler, waveform, subs = self.lanes()
        top_time = max(0.0, self.time_at(rect.top()) - 1.0)
        bottom_time = min(duration, self.time_at(rect.bottom() + 1) + 1.0)
        end_y = self.y_at(duration)

        # The lanes: the waveform's well and the subtitles' track.
        painter.fillRect(QRectF(waveform[0], TOP, waveform[1] - waveform[0], end_y - TOP), QColor(0, 0, 0, 38))
        painter.fillRect(QRectF(subs[0] - 2, TOP, subs[1] - subs[0] + 4, end_y - TOP), QColor(255, 255, 255, 6))

        self._paint_ruler(painter, cfg, top_time, bottom_time, ruler, (waveform[0], subs[1]))

        if session.REPEAT_DURATION_BUFFER:
            start, stop = session.REPEAT_DURATION_BUFFER[0][0], session.REPEAT_DURATION_BUFFER[0][1]
            gradient = QLinearGradient(waveform[0], 0, subs[1], 0)
            color = QColor(cfg.get('cursor_color', '#ccff0000'))
            color.setAlpha(80)
            gradient.setColorAt(0, color)
            color.setAlpha(0)
            gradient.setColorAt(1, color)
            painter.fillRect(QRectF(waveform[0], self.y_at(start), subs[1] - waveform[0], (stop - start) * self.pps), gradient)

        self._paint_waveform(painter, cfg, top_time, bottom_time, waveform)
        onsets = self.show_onsets(cfg)
        if onsets:
            self._paint_onsets(painter, cfg, top_time, bottom_time)
        self._paint_speaker_tracks(painter, top_time, bottom_time)
        # The subtitles of the whole view, not only of the rows being
        # redrawn: a dub clip can reach past its subtitle into those rows.
        view_top, view_bottom = self.view_times()
        self._paint_subtitles(painter, cfg, min(top_time, view_top), max(bottom_time, view_bottom), onsets)
        self._paint_tug(painter, cfg)
        self._paint_playhead(painter, cfg)

    def show_onsets(self, cfg):
        """The timeline's onset markers: an alignment aid, so off while the
        video plays, as there."""
        return bool(cfg.get('show_onset_markers', False)) and not _is_playing(self.window_ref)

    def _paint_ruler(self, painter, cfg, top_time, bottom_time, ruler, grid_span):
        text_color = QColor(cfg.get('time_text_color', '#806a7483'))
        tick_color = QColor(text_color)
        tick_color.setAlphaF(min(1.0, tick_color.alphaF() * 0.8))
        step = next((s for s in LABEL_STEPS if s * self.pps >= LABEL_SPACING), LABEL_STEPS[-1])
        minor = step / 5 if step >= 5 else (step / 2 if step >= 2 else (1.0 if self.pps >= 60 else None))
        if minor is not None and minor * self.pps < 5:
            minor = None
        painter.setFont(QFont('Ubuntu Mono', 8))
        metrics = painter.fontMetrics()
        right = ruler[1] - 4

        if minor:
            painter.setPen(QPen(tick_color, 1))
            first = int(top_time / minor)
            for i in range(first, int(bottom_time / minor) + 2):
                y = self.y_at(i * minor)
                painter.drawLine(QPointF(right - 3, y), QPointF(right, y))

        first = int(top_time // step) * step
        seconds = first
        while seconds <= bottom_time + step:
            y = self.y_at(seconds)
            painter.setPen(QPen(tick_color, 1))
            painter.drawLine(QPointF(right - 7, y), QPointF(right, y))
            painter.setPen(text_color)
            label = utils.get_timeline_time_str(seconds)
            painter.drawText(QRectF(0, y - metrics.height() / 2, right - 10, metrics.height()),
                             Qt.AlignRight | Qt.AlignVCenter, label)
            seconds += step

        if not cfg.get('show_grid', False):
            return
        painter.setPen(QPen(QColor(cfg.get('grid_color', '#336a7483')), 1))
        left, right = grid_span
        grid_type = cfg.get('grid_type', False)
        if grid_type == 'seconds':
            for second in range(int(top_time), int(bottom_time) + 2):
                y = self.y_at(second)
                painter.drawLine(QPointF(left, y), QPointF(right, y))
        elif grid_type == 'frames':
            framerate = float(session.VIDEO.get('framerate') or 25)
            if self.pps / framerate >= 1.0:     # frames under a pixel apart would only fill the lane
                for frame in range(int(top_time * framerate), int(bottom_time * framerate) + 2):
                    y = self.y_at(frame / framerate)
                    painter.drawLine(QPointF(left, y), QPointF(right, y))
        elif grid_type == 'scenes':
            for scene in session.VIDEO.get('scenes') or []:
                if top_time <= scene <= bottom_time:
                    y = self.y_at(scene)
                    painter.drawLine(QPointF(left, y), QPointF(right, y))

    def _paint_waveform(self, painter, cfg, top_time, bottom_time, lane):
        manager = getattr(self.timeline(), 'waveform_manager', None)
        if manager is None or getattr(manager, 'samples', None) is None:
            return
        rate = session.VIDEO.get('samplerate', 48000) or 48000
        start_sample = int(top_time * rate)
        end_sample = int(bottom_time * rate)
        mins, maxs, per_bucket, _chosen = manager.get_level(max(1, int(rate / self.pps)), start_sample, end_sample)
        count = len(mins)
        if not count:
            return
        center = (lane[0] + lane[1]) / 2
        scale = (lane[1] - lane[0]) * 0.9 / 2
        step = (per_bucket / rate) * self.pps
        y = self.y_at((start_sample // per_bucket) * per_bucket / rate)
        right_side = []
        for i in range(count):
            right_side.append(QPointF(center + float(maxs[i]) * scale, y))
            y += step
        left_side = []
        for i in range(count - 1, -1, -1):
            y -= step
            left_side.append(QPointF(center + float(mins[i]) * scale, y))
        path = QPainterPath()
        path.addPolygon(QPolygonF(right_side + left_side))
        path.closeSubpath()
        painter.setPen(QPen(QColor(cfg.get('waveform_border_color', '#ff153450')), 1))
        painter.setBrush(QColor(cfg.get('waveform_fill_color', '#cc153450')))
        painter.drawPath(path)

    def _onset_marker(self, cfg):
        """The timeline's onset glyph, turned: a thin line across the
        canvas with a soft glow fading down from it. Kept per width."""
        width = max(1, self.width())
        color = cfg.get('onset_marker_color', '#ffd9a0')
        if self._onset_pixmap is not None and self._onset_pixmap[1:] == (width, color):
            return self._onset_pixmap[0]
        base = QColor(color)
        glow, fade, line = QColor(base), QColor(base), QColor(base)
        glow.setAlpha(28)
        fade.setAlpha(0)
        line.setAlpha(85)
        depth = 32
        pixmap = QPixmap(width, depth)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.save()
        painter.translate(width / 2.0, 0.0)
        painter.scale(width / 2.0, float(depth))    # the unit circle as a half-ellipse below the line
        gradient = QRadialGradient(0.0, 0.0, 1.0)
        gradient.setColorAt(0.0, glow)
        gradient.setColorAt(1.0, fade)
        painter.fillRect(QRectF(-1.0, 0.0, 2.0, 1.0), QBrush(gradient))
        painter.restore()
        painter.fillRect(QRectF(0.0, 0.0, float(width), 1.0), line)
        painter.end()
        self._onset_pixmap = (pixmap, width, color)
        return pixmap

    def _paint_onsets(self, painter, cfg, top_time, bottom_time):
        onsets = getattr(self.timeline(), 'background_onsets', None)
        if onsets is None or not len(onsets):
            return
        pixmap = self._onset_marker(cfg)
        first = int(np.searchsorted(onsets, top_time - pixmap.height() / self.pps, side='left'))
        last = int(np.searchsorted(onsets, bottom_time, side='right'))
        for i in range(first, last):
            painter.drawPixmap(QPointF(0.0, self.y_at(float(onsets[i]))), pixmap)

    def _paint_speaker_tracks(self, painter, top_time, bottom_time):
        if not self.option('show_speaker_color'):
            return
        top, bottom = self.y_at(top_time), self.y_at(bottom_time)
        for name, (left, right) in self.speaker_columns().items():
            color = QColor((session.SPEAKERS.get(name) or {}).get('color', '#b8cee0'))
            gradient = QLinearGradient(left, 0, right, 0)
            color.setAlpha(0)
            gradient.setColorAt(0, color)
            color.setAlpha(15)
            gradient.setColorAt(1, color)
            painter.fillRect(QRectF(left, top, right - left, bottom - top), gradient)

    def _paint_subtitles(self, painter, cfg, top_time, bottom_time, onsets):
        segments = self.visible_segments(top_time, bottom_time)
        if not segments:
            return
        selected = session.SUBTITLE.get('selected')
        if selected in segments:          # drawn last, over its neighbours
            segments.remove(selected)
            segments.append(selected)

        show_color = self.option('show_speaker_color')
        alignment = getattr(self.timeline(), 'subtitle_alignment', None)
        if alignment is None:
            alignment = {'left': Qt.AlignLeft, 'center': Qt.AlignHCenter, 'right': Qt.AlignRight}.get(
                (session.CONFIG.get('default_values') or {}).get('subtitle_alignment', 'left'), Qt.AlignLeft)
        selected_fill = QColor(cfg.get('selected_subtitle_fill_color', '#cc3e5363'))
        fill = QColor(cfg.get('subtitle_fill_color', '#c8dbe9'))
        fill.setAlphaF(0.9)
        text_color = QColor(cfg.get('subtitle_text_color', '#304251'))
        selected_text_color = QColor(cfg.get('selected_subtitle_text_color', '#b8cee0'))
        failed_color = QColor('#9e1a1a')
        separator = QPen(QColor(cfg.get('subtitle_text_color', '#40304251')), 1)
        quality = (session.CONFIG.get('quality_check') or {}).get('enabled', False)
        dubbing = (session.CONFIG.get('dubbing') or {}).get('enabled', False)
        translation = session.CONFIG.get('translation') or {}
        options = translation.get('engine_options', {}) if isinstance(translation, dict) else {}
        show_translations = options.get('show_translations', False)
        target_language = options.get('target_language', 'en-us')
        font = QFont('Montserrat', 10)
        columns = self.speaker_columns()

        for subtitle in segments:
            rect = self.block_rect(subtitle, columns)
            is_selected = subtitle is selected
            locked = subtitle.get('locked', False)
            speaker_color = (session.SPEAKERS.get(subtitle.get('speaker', 'A')) or {}).get('color')
            painter.save()
            if locked:
                painter.setOpacity(0.18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(selected_fill if is_selected else fill)
            painter.drawRoundedRect(rect, 3.0, 3.0)

            if subtitle.get('dubbing') and dubbing:
                self._paint_clip(painter, cfg, subtitle, rect, speaker_color, locked, onsets)

            if show_color and speaker_color:
                # The timeline's stripe along a block's top, here along its
                # left side: the side it starts from across the lane.
                stripe = QPainterPath()
                stripe.addRoundedRect(QRectF(rect.left(), rect.top(), 6.0, rect.height()), 3.0, 3.0)
                clip = QPainterPath()
                clip.addRect(QRectF(rect.left(), rect.top(), 3.0, rect.height()))
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(speaker_color))
                painter.drawPath(stripe.intersected(clip))

            text_rect = QRectF(rect)
            if subtitle.get('dubbing'):     # the clip has the right-hand side
                text_rect.setWidth(rect.width() - self.clip_width(rect))
            inner = text_rect - TEXT_PADDING
            if inner.height() >= 8 and inner.width() >= 8:
                painter.save()
                painter.setFont(font)
                if quality and not quality_check.check_subtitle(subtitle)[0]:
                    painter.setPen(failed_color)
                else:
                    painter.setPen(selected_text_color if is_selected else text_color)
                painter.setClipRect(text_rect)
                flags = alignment | Qt.AlignTop | Qt.TextWordWrap
                if show_translations:
                    half = inner.height() / 2
                    painter.drawText(QRectF(inner.left(), inner.top(), inner.width(), half), flags, subtitle.get('text', ''))
                    lower = QRectF(inner.left(), inner.top() + half, inner.width(), half)
                    painter.setPen(separator)
                    painter.drawLine(QPointF(lower.left(), lower.top()), QPointF(lower.right(), lower.top()))
                    painter.setPen(selected_text_color if is_selected else text_color)
                    painter.drawText(lower - QMarginsF(0, 5, 0, 0), flags,
                                     (subtitle.get('translations') or {}).get(target_language, ''))
                else:
                    painter.drawText(inner, flags, subtitle.get('text', ''))
                painter.restore()

            if not locked:
                self._paint_edge(painter, subtitle, rect)
            painter.restore()

            if locked:      # the timeline's badge, at full strength over the faded block
                painter.save()
                _paint_padlock(painter, rect.right() - 9.0, rect.top() + 8.0)
                painter.restore()

    @staticmethod
    def clip_width(rect):
        return min(CLIP_WIDTH, rect.width() / 4)

    def _paint_edge(self, painter, subtitle, rect):
        """The timeline's edge handle on the hovered (or dragged) edge: the
        block's outline there, white at the edge and fading inwards."""
        if rect.height() <= EDGE_SHOWN:
            return
        side = None
        if self.mode in ('start', 'end') and self.grabbed is subtitle:
            side = self.mode
        elif self.mode is None and self.hover is not None and self.hover[0] == id(subtitle):
            side = self.hover[1]
        if side not in ('start', 'end'):
            return
        reach = float(EDGE)
        r = 3.0
        left, right = rect.left(), rect.right()
        path = QPainterPath()
        if side == 'start':
            top = rect.top()
            path.moveTo(left, top + reach)
            path.lineTo(left, top + r)
            path.arcTo(left, top, 2 * r, 2 * r, 180, -90)
            path.lineTo(right - r, top)
            path.arcTo(right - 2 * r, top, 2 * r, 2 * r, 90, -90)
            path.lineTo(right, top + reach)
            gradient = QLinearGradient(0, top, 0, top + reach)
        else:
            bottom = rect.bottom()
            path.moveTo(left, bottom - reach)
            path.lineTo(left, bottom - r)
            path.arcTo(left, bottom - 2 * r, 2 * r, 2 * r, 180, 90)
            path.lineTo(right - r, bottom)
            path.arcTo(right - 2 * r, bottom - 2 * r, 2 * r, 2 * r, 270, 90)
            path.lineTo(right, bottom - reach)
            gradient = QLinearGradient(0, bottom, 0, bottom - reach)
        gradient.setColorAt(0, QColor(255, 255, 255, 255))
        gradient.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setPen(QPen(QBrush(gradient), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def _paint_clip(self, painter, cfg, subtitle, rect, speaker_color, locked, onsets):
        """The subtitle's dub clip, as the timeline draws it along a
        block's bottom, here along its right side: a band per subclip in
        the speaker's colour, reaching past the block where the clip does,
        with its waveform, the stretch it plays at, and its lock."""
        from subtitld.modules import dub_clip
        timeline = self.timeline()
        dub = subtitle['dubbing'][0]
        path = dub.get('path')
        if not path:
            return
        peaks_of = getattr(timeline, 'dub_peaks', None)
        if peaks_of is None:
            return
        request = getattr(timeline, '_request_dub_peaks', None)
        if request is not None:
            request(path)
        width = self.clip_width(rect)
        left = rect.right() - width
        base = QColor(speaker_color or '#1a73a8')

        if peaks_of.get(path) is None:
            if not os.path.exists(path):
                # Still on its way out of the project file: the timeline's
                # hatched "not ready" band, under the subtitle.
                hatch = QColor(base)
                hatch.setAlpha(90)
                painter.save()
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(hatch, Qt.BDiagPattern))
                painter.drawRoundedRect(QRectF(left, rect.top(), width, rect.height()), 3.0, 3.0)
                painter.restore()
            return

        ranges = list(dub_clip.iter_segment_ranges(dub))
        if not ranges:
            return
        low, high = dub_clip.clip_extent(dub)
        band = QRectF(left, self.y_at(low), width, max(0.0, (high - low) * self.pps))
        if band.height() <= 1:
            return
        fill = QColor(base)
        fill.setAlpha(204)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        for start, end, _segment in ranges:
            piece = QRectF(left, self.y_at(start), width, (end - start) * self.pps)
            if piece.height() < 1:
                continue
            painter.drawPath(_rounded_path(piece,
                                           square_top_right=subtitle['start'] <= start <= subtitle['end'],
                                           square_bottom_right=subtitle['start'] <= end <= subtitle['end']))

        if dub.get('locked'):
            # The lock badge: at the clip's top, on the block's edge, and
            # stretched back to the subtitle's start when the clip is not
            # where the subtitle starts.
            radius = 7.0
            cx = band.right()
            cy = band.top()
            reach_to = min(max(cy, rect.top()), rect.bottom())
            painter.save()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(cfg.get('subtitle_border_color', '#ff6a7483')))
            painter.drawRoundedRect(QRectF(cx - radius, min(cy, reach_to) - radius, 2 * radius,
                                           abs(cy - reach_to) + 2 * radius), radius, radius)
            _paint_padlock(painter, cx, cy)
            painter.restore()

        painter.save()
        painter.setClipRect(band, Qt.IntersectClip)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(cfg.get('dub_waveform_color', '#ffffffff')))
        center = band.center().x()
        for start, end, segment in ranges:
            height = (end - start) * self.pps
            if height < 1:
                continue
            segment_path = segment.get('path') or path
            peaks = peaks_of.get(segment_path)
            if peaks is None:
                if request is not None and segment_path:
                    request(segment_path)
                continue
            shape = self._clip_waveform(segment_path, peaks, segment, width, height)
            if shape is not None:
                top = self.y_at(start)
                painter.translate(center, top)
                painter.drawPath(shape)
                painter.translate(-center, -top)
        painter.restore()

        if not locked:      # over the waveform, which would hide them in a strip this narrow
            for start, end, segment in ranges:
                self._paint_stretch(painter, dub, segment, band.center().x(), self.y_at(end), (end - start) * self.pps)
        painter.restore()

        if onsets:
            self._paint_clip_onsets(painter, subtitle, ranges, path, base)

    def _clip_waveform(self, segment_path, peaks, segment, width, height):
        """A subclip's waveform, down from (0, 0) and across 0: the part of
        its file it plays, at about a row per pixel. Kept per size."""
        mins, maxs, duration = peaks
        count = len(mins)
        if count <= 0 or duration <= 0:
            return None
        source_start = float(segment.get('start', 0.0))
        source_end = float(segment.get('end', duration))
        first = max(0, int(round(source_start / duration * count)))
        last = min(count, int(round(source_end / duration * count)))
        if last <= first:
            return None
        key = (segment_path, first, last, round(width), round(height))
        shape = self._clip_paths.get(key)
        if shape is not None:
            return shape
        highs = np.asarray(maxs[first:last], dtype=float)
        lows = np.asarray(mins[first:last], dtype=float)
        rows = max(2, int(height))
        if len(highs) > rows:
            cuts = np.linspace(0, len(highs), rows + 1).astype(int)[:-1]
            highs = np.maximum.reduceat(highs, cuts)
            lows = np.minimum.reduceat(lows, cuts)
        step = height / len(highs)
        scale = width * 0.45
        points = [QPointF(float(value) * scale, i * step) for i, value in enumerate(highs)]
        points += [QPointF(float(lows[i]) * scale, i * step) for i in range(len(lows) - 1, -1, -1)]
        shape = QPainterPath()
        shape.addPolygon(QPolygonF(points))
        shape.closeSubpath()
        if len(self._clip_paths) > 512:
            for stale in list(self._clip_paths)[:128]:
                del self._clip_paths[stale]
        self._clip_paths[key] = shape
        return shape

    def _paint_stretch(self, painter, dub, segment, center, bottom, height):
        """The timeline's stretch mark at a subclip's end: two bars, bent
        apart when it plays slower, together when faster, and the ratio
        along the strip above them. Outlined in dark, to read over the
        waveform."""
        if height < 30:
            return
        rate = segment.get('rate', dub.get('rate', 0)) or 0
        ratio = 100.0 / (100.0 + rate) if (100 + rate) > 0 else 1.0
        x0, x1 = center - 5.0, center + 5.0
        mid = center
        near = bottom - 5.0
        if abs(ratio - 1.0) < 0.02:
            near += 1
            far, bend_near, bend_far = near - 4, 0.0, 0.0
        elif ratio > 1.0:
            far, bend_near, bend_far = near - 4, 2.0, -2.0
        else:
            near += 1
            far, bend_near, bend_far = near - 7, -2.0, 2.0
        bars = QPainterPath()
        bars.moveTo(x0, near)
        bars.lineTo(mid, near + bend_near)
        bars.lineTo(x1, near)
        bars.moveTo(x0, far)
        bars.lineTo(mid, far + bend_far)
        bars.lineTo(x1, far)
        painter.save()
        painter.setBrush(Qt.NoBrush)
        for color, thickness in ((QColor(0, 0, 0, 110), 4), (QColor(255, 255, 255, 230), 2)):
            pen = QPen(color, thickness)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(bars)
        if abs(ratio - 1.0) > 0.02 and height >= 60:
            font = QFont('Montserrat', 6)
            font.setBold(True)
            painter.setFont(font)
            painter.translate(center, far - 6)
            painter.rotate(-90)         # reads up the strip, from the bars
            label = QRectF(0, -6, 40, 12)
            painter.setPen(QColor(0, 0, 0, 150))
            painter.drawText(label.translated(0.6, 0.6), Qt.AlignLeft | Qt.AlignVCenter, f'{ratio:.3f}x')
            painter.setPen(QColor(255, 255, 255, 235))
            painter.drawText(label, Qt.AlignLeft | Qt.AlignVCenter, f'{ratio:.3f}x')
        painter.restore()

    def _paint_clip_onsets(self, painter, subtitle, ranges, path, base):
        """The clip's onsets, as the timeline draws them: a line across the
        whole canvas each, in the clip's colour; the one chosen as the
        stretch pivot in amber, with a tab."""
        timeline = self.timeline()
        onsets_of = getattr(timeline, 'dub_onsets', None)
        if onsets_of is None:
            return
        request = getattr(timeline, '_request_dub_onsets', None)
        chosen = getattr(timeline, 'selected_onset', None)
        color = QColor(base)
        color.setAlpha(128)
        amber = QColor('#ffd24a')
        painter.save()
        for start, end, segment in ranges:
            segment_path = segment.get('path') or path
            onsets = onsets_of.get(segment_path)
            if onsets is None:
                if request is not None and segment_path:
                    request(segment_path)
                continue
            source_start = float(segment.get('start', 0.0))
            source_end = float(segment.get('end', 0.0) or 0.0)
            if not len(onsets) or source_end <= source_start:
                continue
            pivot = (float(chosen['source_time']) if chosen and chosen.get('seg_path') == segment_path
                     and chosen.get('subtitle') is subtitle else None)
            first = int(np.searchsorted(onsets, source_start, side='left'))
            last = int(np.searchsorted(onsets, source_end, side='right'))
            scale = (end - start) / (source_end - source_start)
            for i in range(first, last):
                source_time = float(onsets[i])
                y = self.y_at(start + (source_time - source_start) * scale)
                if pivot is not None and abs(source_time - pivot) < 1e-4:
                    painter.setPen(QPen(amber, 2))
                    painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
                    tab = QPainterPath()
                    tab.moveTo(0, y - 4)
                    tab.lineTo(0, y + 4)
                    tab.lineTo(6, y)
                    tab.closeSubpath()
                    painter.fillPath(tab, amber)
                else:
                    painter.setPen(QPen(color, 1))
                    painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
        painter.restore()

    def tug_pair(self):
        """(earlier, later) whose meeting shows the tug of war: hovered, or
        being dragged."""
        if self.mode is None:
            return self.tug
        if self.push and self.grabbed is not None and self.mode in ('start', 'end'):
            previous, following = _neighbours(self.grabbed)
            return (previous, self.grabbed) if self.mode == 'start' else (self.grabbed, following)
        return None

    def _paint_tug(self, painter, cfg):
        """The timeline's tug-of-war mark where two subtitles meet: a row
        of short bars across the meeting."""
        pair = self.tug_pair()
        if not pair or None in pair:
            return
        earlier, later = pair
        columns = self.speaker_columns()
        span = self.block_rect(later, columns)
        y = self.y_at((earlier['end'] + later['start']) / 2)
        painter.save()
        painter.setPen(QPen(QColor(cfg.get('selected_subtitle_arrow_color', '#ff969696')), 4, Qt.SolidLine, Qt.RoundCap))
        step = (span.width() - 8) / 6
        x = span.left() + 8
        for _bar in range(6):
            painter.drawLine(QPointF(x, y - 4), QPointF(x, y + 4))
            x += step
        painter.restore()

    def _paint_playhead(self, painter, cfg):
        position = session.SUBTITLE.get('position')
        if position is None:
            return
        color = QColor(cfg.get('cursor_color', '#ccff0000'))
        y = self.y_at(float(position))
        edge = self.lanes()[0][1]
        painter.setPen(QPen(color, 2))
        painter.drawLine(QPointF(edge - 4, y), QPointF(self.width(), y))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(edge - 10, y - 5), QPointF(edge - 3, y), QPointF(edge - 10, y + 5)]))

    def playhead_strip(self, position):
        """The rows the playhead at `position` covers."""
        y = self.y_at(position)
        return QRectF(0, y - 7, self.width(), 14).toAlignedRect()

    # -- mouse -----------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            event.ignore()
            return
        pos = event.position()
        self.moved = False
        self.recorded = False
        self.push = None
        self.last_pointer_y = pos.y()
        junction = self.junction_at(pos)
        if junction is not None:
            # Where two touching subtitles meet: the edge on the pointer's
            # side is dragged, and the other follows it.
            earlier, later = junction
            meeting = self.y_at((earlier['end'] + later['start']) / 2)
            subtitle, zone = (earlier, 'end') if pos.y() <= meeting else (later, 'start')
            self.push = True
        else:
            subtitle, zone = self.hit(pos)
        self.tug = None
        if subtitle is not None:
            selection_changed = session.SUBTITLE.get('selected') is not subtitle
            session.SUBTITLE['selected'] = subtitle
            self.grabbed = subtitle
            if subtitle.get('locked'):
                self.mode = 'locked'    # selected, but held in place
            else:
                self.mode = zone
                if zone == 'start':
                    self.grab_offset = pos.y() - self.y_at(subtitle['start'])
                elif zone == 'end':
                    self.grab_offset = self.y_at(subtitle['end']) - pos.y()
                else:
                    self.grab_offset = self.time_at(pos.y()) - subtitle['start']
            self.update()
            _selection_changed(self.window_ref, refresh_list=selection_changed)
        else:
            self.mode = 'seek'
            self.grabbed = None
            self.seek_to(self.time_at(pos.y()))
        event.accept()

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self.mode is None:
            self.hover_at(pos)
            return
        self.last_pointer_y = pos.y()
        self.drag_to(pos.y())
        self.scroll.autoscroll_for(self.mapTo(self.scroll.viewport(), pos.toPoint()).y())
        event.accept()

    def hover_at(self, pos):
        """Show what a press here would grab: the edge handle, the tug of
        war, and the cursor for it."""
        subtitle, zone = self.hit(pos)
        tug = self.junction_at(pos)
        hover = (id(subtitle), zone) if subtitle is not None and not subtitle.get('locked') else None
        if hover != self.hover or tug != self.tug:
            self.hover = hover
            self.tug = tug
            self.update()
        if tug is not None:
            meeting = self.y_at((tug[0]['end'] + tug[1]['start']) / 2)
            self.setCursor(self._down_cursor if pos.y() <= meeting else self._up_cursor)
        elif hover is None:
            self.unsetCursor()
        elif zone == 'start':
            self.setCursor(self._up_cursor)
        elif zone == 'end':
            self.setCursor(self._down_cursor)
        else:
            self.setCursor(Qt.SizeVerCursor)

    def drag_to(self, y):
        """Carry the press on to a pointer at `y` (widget coordinates)."""
        if self.mode == 'seek':
            playing = _is_playing(self.window_ref)
            now = time.perf_counter()
            if not playing or now - self._last_seek_time >= 0.033:   # ~30 Hz while playing
                self._last_seek_time = now
                self.seek_to(self.time_at(y))
            return
        subtitle = self.grabbed
        if subtitle is None or self.mode not in ('start', 'end', 'body'):
            return
        if self.mode == 'start':
            target = snap_start(subtitle, self.time_at(y - self.grab_offset), self.push)
            current = subtitle['start']
        elif self.mode == 'end':
            target = snap_end(subtitle, self.time_at(y + self.grab_offset), self.push)
            current = subtitle['end']
        else:
            target, current = snap_move(subtitle, self.time_at(y) - self.grab_offset), subtitle['start']
        if abs(target - current) < 1e-6:
            return
        _move(self.mode, subtitle, target, record=not self.recorded, push=self.push)
        self.recorded = True
        self.moved = True
        self.update()
        timeline = self.timeline()
        if timeline is not None:
            timeline.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            event.ignore()
            return
        mode, self.mode = self.mode, None
        self.grabbed = None
        self.push = None
        self.scroll.autoscroll_for(None)
        if self.moved:
            self.moved = False
            _subtitles_changed(self.window_ref)
        elif mode == 'seek' and session.SUBTITLE.get('selected') is not None:
            # A click off the subtitles drops the selection, as on the timeline.
            session.SUBTITLE['selected'] = None
            _selection_changed(self.window_ref, refresh_list=True)
        self.hover_at(event.position())
        self.update()
        event.accept()

    def leaveEvent(self, event):
        if self.hover is not None or self.tug is not None:
            self.hover = None
            self.tug = None
            self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            notches = event.angleDelta().y() / 120.0
            if notches:
                anchor = self.mapTo(self.scroll.viewport(), event.position().toPoint()).y()
                self.scroll.set_zoom(self.pps * (1.12 ** notches), anchor)
            event.accept()
            return
        self.scroll.wheelEvent(event)       # the scroll area scrolls

    def seek_to(self, seconds):
        seconds = max(0.0, min(_duration(), seconds))
        session.SUBTITLE['position'] = seconds
        if session.CONFIG.get('repeat_activated'):
            session.REPEAT_DURATION_BUFFER = []
        player = getattr(self.window_ref, 'preview_panel_player', None)
        if player is not None:
            player.seek(seconds)
        self.scroll.note_position()
        self.update()
        timeline = self.timeline()
        if timeline is not None:
            timeline.update()


class VerticalTimelineScroll(QScrollArea):
    """The canvas in a scroll area that follows the playhead and the
    selection, and scrolls a drag held near its top or bottom."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window_ref = window
        self.setObjectName('vtimeline_scroll')
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # The canvas is sized here, not by the scroll area: a zoom resizes it
        # and sets the scroll position in one go, so the range must follow
        # at once.
        self.setWidgetResizable(False)
        self.canvas = VerticalTimeline(window, self)
        self.setWidget(self.canvas)
        self.viewport().installEventFilter(self)

        self._last_position = None
        self._selected_id = None
        self._signature = None
        self.follow_timer = QTimer(self, interval=33)
        self.follow_timer.timeout.connect(self.tick)
        self._autoscroll_speed = 0
        self.autoscroll_timer = QTimer(self, interval=30)
        self.autoscroll_timer.timeout.connect(self._autoscroll)

    def eventFilter(self, watched, event):
        if watched is self.viewport() and event.type() == QEvent.Resize:
            self.fit_canvas()
        return super().eventFilter(watched, event)

    def fit_canvas(self):
        self.canvas.resize(self.viewport().width(), self.canvas.full_height())

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_canvas()
        self.follow_timer.start()
        self._last_position = None
        self.tick()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.follow_timer.stop()
        self.autoscroll_timer.stop()

    # -- zoom ------------------------------------------------------------------

    def set_zoom(self, pps, anchor=None):
        """Zoom to `pps` px per second, keeping the time at `anchor` (px
        down the view) where it is; by default the playhead's, when it is
        in view, else the middle's."""
        pps = max(ZOOM_RANGE[0], min(ZOOM_RANGE[1], float(pps)))
        bar = self.verticalScrollBar()
        if anchor is None:
            playhead = self.canvas.y_at(float(session.SUBTITLE.get('position') or 0.0)) - bar.value()
            anchor = playhead if 0 <= playhead <= self.viewport().height() else self.viewport().height() / 2
        seconds = self.canvas.time_at(bar.value() + anchor)
        _config()['zoom'] = pps
        self.fit_canvas()
        bar.setValue(int(round(self.canvas.y_at(seconds) - anchor)))
        self.canvas.update()
        panel = self.parent()
        while panel is not None and not isinstance(panel, VerticalTimelinePanel):
            panel = panel.parent()
        if panel is not None:
            panel.update_zoom_buttons()

    # -- following -------------------------------------------------------------

    def note_position(self):
        """The playhead moved here: no need to bring it into view."""
        self._last_position = session.SUBTITLE.get('position')

    def ensure_time_visible(self, start, end=None, center=False):
        bar = self.verticalScrollBar()
        height = self.viewport().height()
        top = self.canvas.y_at(start)
        bottom = self.canvas.y_at(end if end is not None else start)
        margin = 24
        if center:
            bar.setValue(int((top + bottom) / 2 - height / 2))
        elif top < bar.value() + margin or bottom > bar.value() + height - margin:
            if bottom - top > height - 2 * margin:
                bar.setValue(int(top - margin))
            elif top < bar.value() + margin:
                bar.setValue(int(top - margin))
            else:
                bar.setValue(int(bottom - height + margin))

    def tick(self):
        """Repaint what moved: the playhead, and the subtitles when they
        changed somewhere else (a drag on the timeline). While the video
        plays, follow the playhead as the timeline's scrolling setting says;
        while it is paused, bring a playhead moved elsewhere into view."""
        canvas = self.canvas
        if canvas.height() != canvas.full_height():
            self.fit_canvas()
        position = session.SUBTITLE.get('position')
        if position != self._last_position:
            previous, self._last_position = self._last_position, position
            if previous is not None:
                canvas.update(canvas.playhead_strip(float(previous)))
            if position is not None:
                canvas.update(canvas.playhead_strip(float(position)))
                if canvas.mode is None:
                    self._follow(float(position))

        signature = self._signature_now()
        if signature != self._signature:
            self._signature = signature
            canvas.update()

    def _signature_now(self):
        """What the canvas shows that can change without a word to it: the
        selected subtitle (dragged on the timeline), the timeline's options
        (its buttons), and the clip waveforms and onsets as they arrive."""
        selected = session.SUBTITLE.get('selected') or None
        segments = session.SUBTITLE.get('segments') or []
        timeline = self.canvas.timeline()
        options = tuple(sorted((key, value) for key, value in _timeline_config().items()
                               if isinstance(value, (str, int, float, bool, type(None)))))
        others = (session.CONFIG.get('dubbing') or {}).get('enabled'),             (session.CONFIG.get('quality_check') or {}).get('enabled'),             repr((session.CONFIG.get('translation') or {}).get('engine_options'))
        return (id(selected), selected and selected.get('start'), selected and selected.get('end'),
                selected and selected.get('text'), selected and repr(selected.get('dubbing')),
                len(segments), id(segments), options, others,
                getattr(timeline, 'show_speaker_color', None), getattr(timeline, 'show_speaker_tracks', None),
                len(getattr(timeline, 'dub_peaks', None) or ()), len(getattr(timeline, 'dub_onsets', None) or ()),
                id(getattr(timeline, 'background_onsets', None)), repr(getattr(timeline, 'selected_onset', None)),
                _is_playing(self.window_ref))

    def _follow(self, position):
        """The timeline's scrolling setting: "follow" keeps the playhead in
        the middle, "page" turns the page when it runs off the bottom (it
        starts the next one, as the timeline's starts at its left), "none"
        leaves the view be. Paused, a playhead moved off the view elsewhere
        is brought back, unless the setting is "none"."""
        scrolling = _timeline_config().get('scrolling', 'page')
        if scrolling == 'none':
            return
        bar = self.verticalScrollBar()
        height = self.viewport().height()
        y = self.canvas.y_at(position)
        in_view = bar.value() <= y <= bar.value() + height
        if _is_playing(self.window_ref):
            if scrolling == 'follow':
                target = int(y - height / 2)
                if abs(bar.value() - target) >= FOLLOW_DEADBAND:
                    bar.setValue(target)
            elif not in_view:
                bar.setValue(int(y))
        elif not in_view:
            bar.setValue(int(y - height / 3))

    def sync_selection(self):
        """The selection changed: bring it into view, unless it was made
        here (it is in view already)."""
        selected = session.SUBTITLE.get('selected')
        changed = id(selected) != self._selected_id
        self._selected_id = id(selected)
        if changed and selected and self.canvas.mode is None:
            self.ensure_time_visible(selected['start'], selected['end'])
        self.canvas.update()

    # -- dragging past the edge ------------------------------------------------

    def autoscroll_for(self, y):
        """A drag at `y` px down the view (None: it ended)."""
        if y is None:
            self._autoscroll_speed = 0
        elif y < AUTOSCROLL_ZONE:
            self._autoscroll_speed = -max(2, int((AUTOSCROLL_ZONE - y) / 2))
        elif y > self.viewport().height() - AUTOSCROLL_ZONE:
            self._autoscroll_speed = max(2, int((y - self.viewport().height() + AUTOSCROLL_ZONE) / 2))
        else:
            self._autoscroll_speed = 0
        if self._autoscroll_speed:
            self.autoscroll_timer.start()
        else:
            self.autoscroll_timer.stop()

    def _autoscroll(self):
        canvas = self.canvas
        if not self._autoscroll_speed or canvas.mode is None or canvas.last_pointer_y is None:
            self.autoscroll_timer.stop()
            return
        bar = self.verticalScrollBar()
        before = bar.value()
        bar.setValue(before + self._autoscroll_speed)
        moved = bar.value() - before
        if moved:
            canvas.last_pointer_y += moved
            canvas.drag_to(canvas.last_pointer_y)


class VerticalTimelinePanel(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window_ref = window
        self.setObjectName('vtimeline_panel')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)

        self.scroll = VerticalTimelineScroll(window)
        self.canvas = self.scroll.canvas
        self.layout().addWidget(self.scroll, 1)

        status = QWidget(objectName='vtimeline_panel_status')
        status.setAttribute(Qt.WA_StyledBackground, True)
        status.setLayout(QHBoxLayout())
        status.layout().setContentsMargins(15, 0, 10, 0)
        status.layout().setSpacing(8)
        self.position_label = QLabel(objectName='vtimeline_panel_position_label')
        status.layout().addWidget(self.position_label)
        self.selected_label = QLabel(objectName='vtimeline_panel_selected_label')
        status.layout().addWidget(self.selected_label, 1)

        zoom = QWidget(objectName='vtimeline_panel_zoom')
        zoom.setLayout(QHBoxLayout())
        zoom.layout().setContentsMargins(0, 0, 0, 0)
        zoom.layout().setSpacing(0)
        self.zoom_out_button = QPushButton(objectName='vtimeline_panel_zoom_button')
        self.zoom_out_button.setProperty('position', 'first')
        self.zoom_out_button.setIcon(_icon('zoom_out_icon'))
        self.zoom_out_button.clicked.connect(lambda: self.scroll.set_zoom(self.canvas.pps / ZOOM_STEP))
        self.zoom_in_button = QPushButton(objectName='vtimeline_panel_zoom_button')
        self.zoom_in_button.setProperty('position', 'last')
        self.zoom_in_button.setIcon(_icon('zoom_in_icon'))
        self.zoom_in_button.clicked.connect(lambda: self.scroll.set_zoom(self.canvas.pps * ZOOM_STEP))
        for button in (self.zoom_out_button, self.zoom_in_button):
            button.setCursor(Qt.PointingHandCursor)
            button.setIconSize(QSize(12, 12))
            zoom.layout().addWidget(button)
        status.layout().addWidget(zoom, 0, Qt.AlignVCenter)
        self.layout().addWidget(status)

        self.label_timer = QTimer(self, interval=100)
        self.label_timer.timeout.connect(self.update_labels)

        self.translate()
        self.update_zoom_buttons()
        self.update_labels()

    def showEvent(self, event):
        super().showEvent(event)
        self.label_timer.start()
        self.scroll.sync_selection()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.label_timer.stop()

    def update_zoom_buttons(self):
        pps = self.canvas.pps
        self.zoom_out_button.setEnabled(pps > ZOOM_RANGE[0] + 1e-6)
        self.zoom_in_button.setEnabled(pps < ZOOM_RANGE[1] - 1e-6)

    def update_labels(self):
        position = float(session.SUBTITLE.get('position') or 0.0)
        self.position_label.setText(_clock(position))
        selected = session.SUBTITLE.get('selected')
        if selected:
            length = selected['end'] - selected['start']
            self.selected_label.setText(_('vtimeline_panel.selected_length').format(f'{length:.3f}'))
        else:
            self.selected_label.setText('')

    def translate(self):
        self.zoom_out_button.setToolTip(_('vtimeline_panel.zoom_out'))
        self.zoom_in_button.setToolTip(_('vtimeline_panel.zoom_in'))
        self.update_labels()


def _clock(seconds):
    """hh:mm:ss.mmm, rounded to the millisecond."""
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}'


def _icon(name):
    icon = QIcon(str(session.PATH_SUBTITLD_GRAPHICS / f'{name}.svg'))
    icon.addFile(str(session.PATH_SUBTITLD_GRAPHICS / f'{name}_disabled.svg'), QSize(), QIcon.Disabled)
    return icon


def _is_playing(window):
    player = getattr(window, 'preview_panel_player', None)
    try:
        return player is not None and not player.is_paused()
    except Exception:
        return False


def _selection_changed(window, refresh_list=True):
    """A subtitle was selected here: the subtitle list and the timeline
    show it, as after a click on the timeline."""
    if refresh_list:
        listing = getattr(window, 'subtitles_panel_qlistwidget', None)
        if listing is not None:
            try:
                listing.update_content()
            except Exception:
                pass
    try:
        left_panel.update(window)
    except Exception:
        pass
    timeline = getattr(window, 'timeline_widget', None)
    if timeline is not None:
        timeline.update()


def _subtitles_changed(window):
    """A drag here changed a subtitle's timing: everything that shows the
    subtitles is told, as after a drag on the timeline."""
    session.set_unsaved(True)
    timeline = getattr(window, 'timeline_widget', None)
    if timeline is not None:
        timeline.update()
    player = getattr(window, 'preview_panel_player', None)
    if player is not None:
        player.update()
        device = getattr(player, '_audio_device', None)
        if device is not None and hasattr(device, 'sync_subtitle_dubs'):
            device.sync_subtitle_dubs(session.SUBTITLE.get('segments') or [])


# ---------------------------------------------------------------------------
# Window wiring
# ---------------------------------------------------------------------------

def load(self):
    tab = left_panel.left_panel(
        parent=self,
        tab_name='vtimeline',
        update_callback=update,
        translate_callback=translate
    )
    tab.layout().setContentsMargins(0, 0, 0, 0)
    self.vtimeline_panel = VerticalTimelinePanel(self)
    tab.layout().addWidget(self.vtimeline_panel)
    session._document_change_callbacks.append(_document_changed(self))


def _document_changed(window):
    def callback():
        panel = getattr(window, 'vtimeline_panel', None)
        if panel is not None and panel.isVisible():
            panel.scroll.fit_canvas()
            panel.canvas.update()
    return callback


def update(self):
    """The left panel's refresh, while this tab is the one shown — run on
    every change of the selection."""
    self.vtimeline_panel.scroll.fit_canvas()
    self.vtimeline_panel.scroll.sync_selection()
    self.vtimeline_panel.update_labels()


def translate(self):
    self.vtimeline_panel.translate()
