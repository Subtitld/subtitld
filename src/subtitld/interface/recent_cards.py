"""The start screen's recent projects: one row each, with a frame of the
video on the left, the name, a small timeline of where the subtitles are
(and where you stopped), and when it was last opened.

What a row shows is remembered when the project is opened, saved and
closed (remember()): the video's length and a summary of where its
subtitles are go into the recent-files entry, a small picture of the
preview (a frame with a subtitle on it) into the cache. The start screen only reads them back, so it opens no file.
"""
import datetime
import hashlib
import pathlib
import re
import time

from PySide6.QtCore import Qt, QEventLoop, QRectF
from PySide6.QtGui import QPainter, QPainterPath, QColor, QFont, QFontMetrics, QImage, QLinearGradient
from PySide6.QtWidgets import QWidget

from subtitld.interface import theme
from subtitld.interface import shapes
from subtitld.interface import utils
from subtitld.modules import session

BINS = 48                             # steps in a row's timeline
FRAME_W, FRAME_H = 96, 54             # the frame kept for the left of a card, 16:9 (saved at twice that)
ROW_H = 61
GAP = 5                               # between two cards: the list's spacing, so a chosen row's light is the card's own
CARD_RADIUS = 4
FACE = QColor(255, 255, 255, 14)      # each card's background, a shade lighter than the list
TRACK = QColor(255, 255, 255, 14)     # the timeline's strip
CUE = QColor('#7f94a6')
MARK = QColor(235, 70, 70, 170)       # where you stopped: thin, faded red


def _cache_dir():
    return pathlib.Path(session.PATH_SUBTITLD_USER_CACHE) / 'recent'


def frame_path(subtitle_filepath):
    """Where the frame of a recent project is kept."""
    return _cache_dir() / (hashlib.sha1(str(subtitle_filepath).encode('utf-8')).hexdigest() + '.jpg')


def summary(segments, duration):
    """Where the subtitles are, as BINS digits: how much of each step of
    the video they cover, 0 to 9."""
    if not duration or duration <= 0:
        return ''
    step = duration / BINS
    cover = [0.0] * BINS
    for segment in segments or []:
        try:
            start, end = float(segment.get('start', 0)), float(segment.get('end', 0))
        except (AttributeError, TypeError, ValueError):
            continue
        start, end = max(0.0, start), min(duration, end)
        if end <= start:
            continue
        first, last = int(start / step), min(BINS - 1, int(end / step))
        for index in range(first, last + 1):
            left, right = index * step, (index + 1) * step
            cover[index] += max(0.0, min(end, right) - max(start, left))
    return ''.join(str(min(9, round(9 * c / step))) for c in cover)


def remember(window, look_ahead=False):
    """Main thread. Note the open project's length, where its subtitles are
    and a picture of its video, for its row on the start screen.

    The picture is the preview's: the frame with its subtitle drawn as the
    preview draws it. With no subtitle under the playhead the frame alone
    is kept only if there is no picture yet; with `look_ahead` (on closing)
    the preview goes to the next subtitle for it instead, and back."""
    filepath = session.SUBTITLE.filepath
    recent = session.CONFIG.get('recent_files') if isinstance(session.CONFIG, dict) else None
    if not filepath or not isinstance(recent, dict) or not isinstance(recent.get(str(filepath)), dict):
        return
    entry = recent[str(filepath)]
    try:
        duration = float(session.VIDEO.get('duration') or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration > 0:
        entry['duration'] = duration
        entry['cues'] = summary(session.SUBTITLE.segments, duration)
    player = getattr(window, 'preview_panel_player', None)
    if player is None:
        return
    segments = [s for s in session.SUBTITLE.segments if isinstance(s, dict)]
    position = float(session.SUBTITLE.position or 0.0)
    showing = _showing(segments, position)
    if showing is None and look_ahead:
        following = sorted((s for s in segments if float(s.get('start', 0)) > position), key=lambda s: float(s['start']))
        if following:
            image = _picture_at(player, following[0])
            if image is not None:
                _save(image, filepath)
                return
    if showing is None and frame_path(filepath).exists():
        return
    image = _frame_image(player)
    if image is not None:
        _save(_with_subtitle(player, image, showing, position), filepath)


def _showing(segments, at):
    return next((s for s in segments if float(s.get('start', 0)) <= at < float(s.get('end', 0))), None)


def _frame_image(player):
    frame = getattr(player, '_last_video_frame', None)
    if frame is None or not frame.isValid():
        return None
    image = frame.toImage()
    return None if image.isNull() else image


def _with_subtitle(player, image, segment, at):
    """`image` (a video frame) with `segment` drawn over it as the preview
    draws it at `at` seconds (the renderer's layers)."""
    renderer = getattr(player, '_subtitle_renderer', None)
    if segment is None or renderer is None:
        return image
    layers = renderer.layers([segment], at)
    if not layers:
        return image
    image = image.convertToFormat(QImage.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    for area, layer in layers:
        painter.drawImage(QRectF(area.x() * image.width(), area.y() * image.height(),
                                 area.width() * image.width(), area.height() * image.height()), layer)
    painter.end()
    return image


def _picture_at(player, segment, timeout=3.0):
    """The preview's picture in the middle of `segment`: it goes there, waits
    for the frame and the subtitle's layer (at most `timeout` s), and goes
    back where it was. None if either did not come."""
    back = float(session.SUBTITLE.position or 0.0)
    start, end = float(segment.get('start', 0)), float(segment.get('end', 0))
    target = start + (end - start) / 2
    renderer = getattr(player, '_subtitle_renderer', None)
    player.seek(target)
    image = None
    deadline = time.monotonic() + timeout
    loop = QEventLoop()
    while time.monotonic() < deadline:
        loop.processEvents(QEventLoop.AllEvents, 50)
        frame = getattr(player, '_last_video_frame', None)
        if frame is None or not frame.isValid() or abs(frame.startTime() / 1_000_000 - target) > 0.5:
            continue
        if renderer is not None and not renderer.layers([segment], target):
            continue
        image = _with_subtitle(player, _frame_image(player), segment, target)
        break
    player.seek(back)
    session.SUBTITLE.position = back
    return image


def _save(image, filepath):
    small = image.scaled(FRAME_W * 2, FRAME_H * 2, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    small = small.copy((small.width() - FRAME_W * 2) // 2, (small.height() - FRAME_H * 2) // 2, FRAME_W * 2, FRAME_H * 2)
    try:
        _cache_dir().mkdir(parents=True, exist_ok=True)
        small.save(str(frame_path(filepath)), 'JPG', 82)
    except OSError:
        pass


def clean_title(path):
    """The name to show: no extension, nor the [id] a downloaded video's
    name ends with."""
    return re.sub(r'\s*\[[A-Za-z0-9_-]{6,}\]$', '', pathlib.Path(path).stem) or pathlib.Path(path).name


class RecentCard(QWidget):
    """One recent project, painted as a card on a lighter face: the frame
    filling its left side; beside it the name, and under that the timeline
    with the age at its end."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setFixedHeight(ROW_H)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        subtitle = pathlib.Path(config['subtitle_filepath'])
        self.title = clean_title(subtitle)
        self.setToolTip(f"{subtitle.name}\n{pathlib.Path(config.get('video_filepath') or '').name}")
        self.frame = QImage(str(frame_path(subtitle)))
        self.retranslate()

    def retranslate(self):
        try:
            opened = datetime.datetime.fromtimestamp(float(self.config.get('last_opened', 0)))
            self.age = utils.friendly_time(opened)
        except (TypeError, ValueError, OSError):
            self.age = ''
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)

        # the card: a lighter face filling its row (the list keeps the gap between rows)
        card = QRectF(0, 0, self.width(), ROW_H)
        outline = QPainterPath()
        outline.addRoundedRect(card, CARD_RADIUS, CARD_RADIUS)

        painter.fillPath(outline, theme.color(FACE))

        # the frame fills the card's left side: round where the card is, square toward the name
        box = QRectF(card.left(), card.top(), card.height() * 16 / 9, card.height())
        clip = outline.intersected(shapes.rect_path(box))
        if not self.frame.isNull():
            painter.save()
            painter.setClipPath(clip)
            painter.drawImage(box, self.frame)
            painter.restore()
        else:
            empty = QLinearGradient(0, box.top(), 0, box.bottom())
            empty.setColorAt(0, theme.color(QColor('#2a3743')))
            empty.setColorAt(1, theme.color(QColor('#141b21')))
            painter.fillPath(clip, empty)

        # the name beside the frame, in up to two lines
        left = box.right() + 10
        right = card.right() - 9
        top = card.top() + 7
        name = QFont('Montserrat')
        name.setPixelSize(11)
        name.setWeight(QFont.Bold)
        metrics = QFontMetrics(name)
        painter.setFont(name)
        painter.setPen(theme.ink(QColor('#e3edf5')))
        for index, line in enumerate(self._wrap(self.title, metrics, right - left, 2)):
            painter.drawText(QRectF(left, top + index * 14, right - left, 14), Qt.AlignLeft | Qt.AlignVCenter, line)

        # under it, the timeline, and at its end how long ago it was opened
        small = QFont('Montserrat')
        small.setPixelSize(9)
        age = self.age.upper()
        age_w = QFontMetrics(small).horizontalAdvance(age)
        track = QRectF(left, card.bottom() - 13, right - left - (age_w + 8 if age else 0), 6)
        painter.setFont(small)
        painter.setPen(theme.ink(QColor(184, 206, 224, 130)))
        painter.drawText(QRectF(right - age_w, track.top() - 4, age_w, track.height() + 8), Qt.AlignRight | Qt.AlignVCenter, age)

        # the timeline: a flat strip, the subtitles on it, a thin red line where you stopped
        painter.fillRect(track, theme.color(TRACK))
        cues = str(self.config.get('cues') or '')
        if cues:
            step = track.width() / len(cues)
            for index, digit in enumerate(cues):
                if digit.isdigit() and digit != '0':
                    color = theme.color(QColor(CUE))
                    color.setAlpha(60 + int(digit) * 15)
                    painter.fillRect(QRectF(track.left() + index * step, track.top(), step, track.height()), color)
        try:
            duration = float(self.config.get('duration') or 0)
            position = float(self.config.get('last_position') or 0)
        except (TypeError, ValueError):
            duration = position = 0
        if duration > 0 and position > 0:
            x = round(track.left() + track.width() * min(1.0, position / duration))
            painter.fillRect(QRectF(x, track.top() - 3, 1, track.height() + 6), MARK)

    @staticmethod
    def _wrap(text, metrics, width, count):
        """`text` in at most `count` lines of `width`, the last one cut short."""
        words, lines, line = text.split(), [], ''
        for word in words:
            attempt = f'{line} {word}'.strip()
            if metrics.horizontalAdvance(attempt) <= width or not line:
                line = attempt
            else:
                lines.append(line)
                line = word
            if len(lines) == count:
                break
        if len(lines) < count and line:
            lines.append(line)
        if lines:
            lines[-1] = metrics.elidedText(lines[-1] if len(lines) < count or ' '.join(lines) == text
                                           else lines[-1] + '…', Qt.ElideRight, width)
        return lines[:count]
