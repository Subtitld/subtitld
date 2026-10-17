"""The picture a .usfx carries (thumbnail.png): what file managers show of it.

A card in Subtitld's own look, a little project at a glance: the video's
frame where the project was left, its subtitle set over it in a caption, and
under it the timeline there (the sound's waveform, the subtitles as blocks
with their speakers' colours, the playhead), with Subtitld's mark beside.
Without a video, the mark stands in for the frame. 4:3, so it fills a file
manager's square cell better than a wide frame would.

    render(frame, segments, position, ...) -> QImage    (512 x 384, rounded corners)
"""

import numpy as np

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPolygonF, QRadialGradient)

from subtitld.modules import markup
from subtitld.modules import session

WIDTH, HEIGHT = 512, 384
PAD = 14
RADIUS = 26
WINDOW = 12.0                   # seconds of timeline shown, around the position

BLOCK = QColor('#c8dbe9')       # the timeline's subtitle fill
BLOCK_CURRENT = QColor('#eef5fb')
PLAYHEAD = QColor('#ff3b3b')
WAVE = QColor(143, 176, 201, 95)
TEXT = QColor('#f2f6f9')


def _rounded(rect, radius):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _mark():
    image = QImage(str(session.PATH_SUBTITLD_GRAPHICS / 'subtitld.png'))
    return None if image.isNull() else image


def _draw_mark(painter, centre, size, opacity=1.0):
    mark = _mark()
    if mark is None:
        return
    painter.save()
    painter.setOpacity(opacity)
    painter.drawImage(QRectF(centre.x() - size / 2, centre.y() - size / 2, size, size), mark)
    painter.restore()


def _caption_lines(text, metrics, width):
    lines = [line.strip() for line in markup.plain(text or '').split('\n') if line.strip()]
    if len(lines) > 2:
        lines = [lines[0], ' '.join(lines[1:])]
    return [metrics.elidedText(line, Qt.ElideRight, width) for line in lines]


def _current(segments, position):
    """The subtitle at the position, else the next one in the window."""
    ahead = None
    for segment in segments:
        start, end = float(segment.get('start', 0)), float(segment.get('end', 0))
        if start <= position < end:
            return segment
        if position <= start < position + WINDOW / 2 and (ahead is None or start < float(ahead.get('start', 0))):
            ahead = segment
    return ahead


def _envelope(samples, samplerate, start, end, buckets):
    """The waveform's peaks from `start` to `end` s, in `buckets`, 0-1."""
    if samples is None or not len(samples) or end <= start:
        return None
    first, last = int(max(0, start) * samplerate), int(min(len(samples), end * samplerate))
    if last - first < buckets:
        return None
    chunk = np.abs(np.asarray(samples[first:last], dtype=np.float32))
    usable = len(chunk) // buckets * buckets
    peaks = chunk[:usable].reshape(buckets, -1).max(axis=1)
    top = float(np.percentile(peaks, 98)) or 1.0
    return np.clip(peaks / top, 0.0, 1.0)


def render(frame, segments, position, speakers=None, duration=None, samples=None, samplerate=48000):
    """The card, as a QImage with transparent corners. `frame` is a QImage
    of the video (or None); `segments` the subtitles; `position` where the
    project was left (s); `speakers` {name: {'color': ...}}; `samples` the
    sound (any int or float array, mono) at `samplerate`."""
    speakers = speakers or {}
    segments = [s for s in (segments or []) if isinstance(s, dict)]
    position = max(0.0, float(position or 0.0))

    image = QImage(WIDTH, HEIGHT, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)

    # The card: the slate glow of Subtitld's tiles.
    card = QRectF(0.5, 0.5, WIDTH - 1, HEIGHT - 1)
    glow = QRadialGradient(QPointF(WIDTH * 0.5, HEIGHT * 0.05), WIDTH * 0.95)
    glow.setColorAt(0.0, QColor('#46596a'))
    glow.setColorAt(1.0, QColor('#151c22'))
    painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
    painter.setBrush(QBrush(glow))
    painter.drawPath(_rounded(card, RADIUS))

    # The frame, filling its place (cropped, not letterboxed), or the mark.
    area = QRectF(PAD, PAD, WIDTH - 2 * PAD, 272)
    clip = _rounded(area, 16)
    painter.save()
    painter.setClipPath(clip)
    if frame is not None and not frame.isNull():
        scale = max(area.width() / frame.width(), area.height() / frame.height())
        w, h = frame.width() * scale, frame.height() * scale
        painter.drawImage(QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h), frame)
        # Darker toward the bottom, where the caption sits.
        shade = QLinearGradient(area.topLeft(), area.bottomLeft())
        shade.setColorAt(0.55, QColor(0, 0, 0, 0))
        shade.setColorAt(1.0, QColor(0, 0, 0, 120))
        painter.fillRect(area, QBrush(shade))
    else:
        empty = QRadialGradient(area.center(), area.width() * 0.6)
        empty.setColorAt(0.0, QColor('#2a3743'))
        empty.setColorAt(1.0, QColor('#10161b'))
        painter.fillRect(area, QBrush(empty))
        _draw_mark(painter, QPointF(area.center().x(), area.center().y() - 18), 132, 0.9)
    painter.restore()
    painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(clip)

    # The subtitle there, as a caption over the frame.
    current = _current(segments, position)
    if current is not None:
        font = QFont('Montserrat')
        font.setPixelSize(17)
        font.setWeight(QFont.DemiBold)
        metrics = QFontMetricsF(font)
        lines = _caption_lines(current.get('text'), metrics, area.width() - 56)
        if lines:
            line_h = metrics.height()
            text_w = max(metrics.horizontalAdvance(line) for line in lines)
            pill = QRectF(0, 0, text_w + 26, line_h * len(lines) + 14)
            pill.moveCenter(QPointF(area.center().x(), area.bottom() - 14 - pill.height() / 2))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(10, 14, 18, 205))
            painter.drawRoundedRect(pill, 10, 10)
            painter.setFont(font)
            painter.setPen(TEXT)
            for k, line in enumerate(lines):
                row = QRectF(pill.left(), pill.top() + 7 + k * line_h, pill.width(), line_h)
                painter.drawText(row, Qt.AlignHCenter | Qt.AlignVCenter, line)

    # The timeline there: a band with the waveform, the subtitles, the playhead.
    band = QRectF(PAD, area.bottom() + 10, WIDTH - 2 * PAD - 84, HEIGHT - PAD - area.bottom() - 10)
    badge = QRectF(band.right() + 10, band.top(), WIDTH - PAD - band.right() - 10, band.height())
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(6, 10, 14, 120))
    painter.drawRoundedRect(band, 14, 14)
    painter.drawRoundedRect(badge, 14, 14)
    _draw_mark(painter, badge.center(), 52)

    inner = band.adjusted(10, 8, -10, -8)
    start = position - WINDOW / 2
    if start < 0:
        start = 0.0
    if duration:
        start = max(0.0, min(start, float(duration) - WINDOW))
    end = start + WINDOW

    def x_at(t):
        return inner.left() + (t - start) / WINDOW * inner.width()

    painter.save()
    painter.setClipPath(_rounded(band, 14))
    peaks = _envelope(samples, samplerate, start, end, int(inner.width() // 2))
    if peaks is not None:
        middle, reach = inner.center().y(), inner.height() / 2
        top = [QPointF(inner.left() + i * 2, middle - p * reach) for i, p in enumerate(peaks)]
        bottom = [QPointF(inner.left() + i * 2, middle + p * reach) for i, p in reversed(list(enumerate(peaks)))]
        painter.setBrush(WAVE)
        painter.drawPolygon(QPolygonF(top + bottom))
    for segment in segments:
        s, e = float(segment.get('start', 0)), float(segment.get('end', 0))
        if e <= start or s >= end:
            continue
        block = QRectF(x_at(s) + 1, inner.top() + 2, max(3.0, x_at(e) - x_at(s) - 2), inner.height() - 4)
        painter.setBrush(BLOCK_CURRENT if segment is current else BLOCK)
        painter.setOpacity(1.0 if segment is current else 0.85)
        painter.drawRoundedRect(block, 6, 6)
        colour = (speakers.get(segment.get('speaker')) or {}).get('color')
        if colour:
            painter.save()
            painter.setClipPath(_rounded(block, 6))
            painter.fillRect(QRectF(block.left(), block.top(), block.width(), 5), QColor(colour))
            painter.restore()
        painter.setOpacity(1.0)
    playhead = x_at(position)
    if inner.left() <= playhead <= inner.right():
        painter.setPen(QPen(PLAYHEAD, 2.5))
        painter.drawLine(QPointF(playhead, band.top()), QPointF(playhead, band.bottom()))
        painter.setPen(Qt.NoPen)
        painter.setBrush(PLAYHEAD)
        painter.drawPolygon(QPolygonF([QPointF(playhead - 6, band.top()), QPointF(playhead + 6, band.top()),
                                       QPointF(playhead, band.top() + 8)]))
    painter.restore()
    painter.end()
    return image
