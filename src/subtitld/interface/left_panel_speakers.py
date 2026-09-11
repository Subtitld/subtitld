import cv2
import mediapipe as mp
import numpy as np
from autohex import AutoHex

from PySide6.QtWidgets import QVBoxLayout, QWidget, QScrollArea, QHBoxLayout, QDialog, QPushButton, QLabel, QLineEdit, QSizePolicy, QColorDialog, QComboBox, QCheckBox, QStackedWidget
from PySide6.QtGui import QImage, QPixmap, QPainter, QPainterPath, QColor, QPolygonF, QCursor, QBrush
from PySide6.QtCore import QThread, QTimer, Signal, Qt, QSize, QRect, QRectF, QPoint

from subtitld.interface import utils
from subtitld.interface import left_panel
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import subtitles
from subtitld.modules import history
from subtitld.modules.signals import SIGNALS as _SESSION_SIGNALS


class FaceExtractorThread(QThread):
    result = Signal(dict)
    progress = Signal(float)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.name = None
        self.step = 0.3
        self._running = True

    def run(self):
        intervals = subtitles.get_speaker_intervals(self.name)
        if not self.name or not intervals:
            return

        cap = cv2.VideoCapture(session.VIDEO['filepath'])
        if not cap.isOpened():
            self.error.emit(f"Cannot open video: {session.VIDEO['filepath']}")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            self.error.emit("Invalid FPS in video file.")
            cap.release()
            return

        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        total_duration = frame_count / fps if fps else 0

        mp_face_detection = mp.solutions.face_detection

        try:
            with mp_face_detection.FaceDetection(
                model_selection=1,
                min_detection_confidence=0.5
            ) as face_detector:
            
                face_found = False
                
                for start_time, end_time in intervals:
                    if not self._running or face_found:
                        return                        

                    if end_time <= start_time:
                        continue

                    start_time = max(0, start_time)
                    end_time = min(end_time, total_duration)
                    total_range = end_time - start_time
                    t = start_time

                    while t <= end_time and self._running and not face_found:
                        frame_idx = int(t * fps)
                        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                        ret, frame = cap.read()
                        if not ret:
                            break

                        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        results = face_detector.process(rgb)

                        if results.detections:
                            det = results.detections[0]
                            box = det.location_data.relative_bounding_box
                            h, w, _ = rgb.shape
                            x1 = int(box.xmin * w)
                            y1 = int(box.ymin * h)
                            x2 = int((box.xmin + box.width) * w)
                            y2 = int((box.ymin + box.height) * h)
                            x1, y1 = max(0, x1), max(0, y1)
                            x2, y2 = min(w, x2), min(h, y2)

                            face_crop = rgb[y1:y2, x1:x2]
                            qimg = self._to_qimage(face_crop)
                            if qimg:
                                self.result.emit({
                                    "name": self.name,
                                    "image": qimg,
                                    "timestamp": round(t, 3)
                                })
                                face_found = True
                                break

                        progress_value = (t - start_time) / total_range
                        self.progress.emit(min(max(progress_value, 0), 1))
                        t += self.step

                    if not face_found:
                        self.result.emit({
                            "name": self.name,
                            "image": None,
                            "timestamp": None
                        })

        except Exception as e:
            self.error.emit(str(e))
        finally:
            cap.release()

    # ---------------------------------
    # Public control methods
    # ---------------------------------
    def stop(self):
        """Request thread stop gracefully."""
        self._running = False

    # ---------------------------------
    # Internal utility
    # ---------------------------------
    @staticmethod
    def _to_qimage(image: np.ndarray) -> QImage | None:
        if image is None or image.size == 0 or image.ndim != 3:
            return None
        image = np.ascontiguousarray(image)
        h, w, ch = image.shape
        if ch != 3:
            return None
        bytes_per_line = ch * w
        return QImage(image.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()


class AccordionArrowWidget(QWidget):
    """Custom widget that draws the accordion arrow icon."""
    
    def __init__(self, expanded=True, parent=None):
        super().__init__(parent)
        self._expanded = expanded
        self.setFixedSize(20, 20)
    
    def set_expanded(self, expanded: bool):
        self._expanded = expanded
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1e40af"))
        
        if self._expanded:
            arrow = QPolygonF([
                QPoint(4, 12),
                QPoint(16, 12),
                QPoint(10, 6)
            ])
        else:
            arrow = QPolygonF([
                QPoint(4, 8),
                QPoint(16, 8),
                QPoint(10, 14)
            ])
        
        painter.drawPolygon(arrow)


def _stacked_resize_to_current(stacked):
    """Make a QStackedWidget size to its *current* page rather than to the
    largest page across the stack. We flip each page's size policy so only
    the visible one contributes to the stack's sizeHint — Qt then collapses
    the empty rows that would otherwise be reserved for taller siblings.

    The parent is signalled via updateGeometry() rather than adjustSize() —
    adjustSize() snaps the parent's width down to its sizeHint, which then
    shrinks any siblings (like the dubbing-engine combobox) whose policy
    allows them to follow the parent's narrower width. updateGeometry()
    invalidates the layout cache and lets Qt reflow vertically while
    leaving the parent's horizontal extent alone."""
    current = stacked.currentWidget()
    for i in range(stacked.count()):
        page = stacked.widget(i)
        if page is None:
            continue
        policy = page.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Preferred if page is current else QSizePolicy.Ignored)
        page.setSizePolicy(policy)
    if current is not None:
        current.adjustSize()
    stacked.adjustSize()
    parent = stacked.parentWidget()
    if parent is not None:
        parent.updateGeometry()


class dubbing_container(QWidget):
    def __init__(widget, parent=None):
        super().__init__(parent)
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(10, 10, 10, 10)
        widget.layout().setSpacing(0)

        widget.setProperty('is_expanded', False)
        
        header_line = QHBoxLayout()
        header_line.setContentsMargins(0, 0, 0, 0)
        header_line.setSpacing(0)
        widget.layout().addLayout(header_line)

        widget.combobox = utils.LabeledComboBox()
        widget.combobox.activated.connect(lambda: widget.combobox_changed())
        header_line.addWidget(widget.combobox, 1)

        widget.hideexpand_button = QPushButton()
        widget.hideexpand_button.setObjectName('dubbing_container_hideexpand_button')
        widget.hideexpand_button.setCheckable(True)
        widget.hideexpand_button.setFixedSize(24, 24)
        widget.hideexpand_button.setIconSize(QSize(16, 16))
        widget.combobox.bottom_line.addWidget(widget.hideexpand_button, 0)

        widget.hideexpand_button.clicked.connect(lambda: widget.hideexpand_button_clicked())

        widget.content = QStackedWidget()
        widget.content.setObjectName('left_panel_speakers_panel_content_item_dubbing_content')
        widget.content.setVisible(False)
        # Without this, the stacked widget reserves space for the *tallest*
        # page across all engines, leaving big gaps when the active engine
        # has a shorter panel. `setCurrentChanged` flips each page's size
        # policy so only the visible one contributes to the stack's sizeHint.
        widget.content.currentChanged.connect(lambda i, sw=widget.content: _stacked_resize_to_current(sw))
        widget.layout().addWidget(widget.content)

        widget.update()

    def combobox_changed(widget):
        # The combobox stores the engine id as user data behind a localized
        # display name — read the data, not the displayed text.
        inner = widget.combobox.combobox
        value = inner.currentData()
        if not value:
            value = inner.currentText()
        speaker_name = widget.property('speaker')

        if speaker_name and speaker_name in session.SPEAKERS:
            if not 'dubbing' in session.SPEAKERS[speaker_name]:
                session.SPEAKERS[speaker_name]['dubbing'] = {}
            session.SPEAKERS[speaker_name]['dubbing']['engine'] = value
            session.set_unsaved(True)
        widget.update()

    def hideexpand_button_clicked(widget):
        widget.setProperty('is_expanded', not widget.property('is_expanded'))
        widget.update()
    
    def update(widget):
        idx = widget.combobox.combobox.currentIndex()
        has_engine = idx >= 0
        widget.hideexpand_button.setEnabled(has_engine)
        if not has_engine:
            widget.setProperty('is_expanded', False)

        # Engine panels were added to the QStackedWidget in the same order
        # as combobox items in `update_dubbing_options`, so the indices map
        # 1:1. Keep them in sync so selecting an engine swaps the panel.
        if has_engine and widget.content.currentIndex() != idx:
            widget.content.setCurrentIndex(idx)
        elif has_engine:
            # currentChanged didn't fire (already at idx) — recompute size
            # anyway so first-show / re-expand picks up the right height.
            _stacked_resize_to_current(widget.content)

        if widget.property('is_expanded'):
            if not widget.hideexpand_button.isChecked():
                widget.hideexpand_button.setChecked(True)
            if not widget.content.isVisible():
                widget.content.setVisible(True)
        else:
            if widget.hideexpand_button.isChecked():
                widget.hideexpand_button.setChecked(False)
            if widget.content.isVisible():
                widget.content.setVisible(False)


# --- Speaker card geometry -------------------------------------------------
# The avatar sits flush at the card's left edge and paints OVER the timeline
# strip, which is itself inset from the left. `_INFO_LEFT` (avatar + gap) is
# load-bearing, not cosmetic: child widgets always paint after their parent,
# so anything that reached left of it would render on top of the avatar.
_AVATAR_SIZE = 48
_TIMELINE_HEIGHT = 6
_CORNER_RADIUS = 4
# The item's BODY (background, timeline strip, text, option rows) is inset
# this far from the list's left edge, so a strip of list background shows
# behind the avatar. The avatar itself is NOT inset — it sits flush against
# the list edge and overhangs the body, which is why it is a free-standing
# child positioned by hand rather than a laid-out widget.
_BODY_LEFT_MARGIN = 10
# The avatar hangs 3px lower than the rest of the header band.
_AVATAR_TOP_OFFSET = 3
# Gap between the avatar and the text column.
_INFO_GAP = 10
_INFO_LEFT = _AVATAR_SIZE + _INFO_GAP


def _format_speaker_time(seconds):
    """Human duration in the panel's "1h 28min" style.

    Shows the two most significant non-zero units, so an hour-long speaker
    reads "1h 28min" while a twenty-second one still reads "20s" rather than
    a useless "0h 0min".
    """
    total = int(round(float(seconds or 0)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f'{hours}h {minutes}min' if minutes else f'{hours}h'
    if minutes:
        return f'{minutes}min {secs}s' if secs else f'{minutes}min'
    return f'{secs}s'


def _rounded_path(rect, top_left=0, top_right=0, bottom_right=0, bottom_left=0):
    """A rect path with independently rounded corners.

    Qt gives no per-corner path primitive, so the spec's "top-left radius and
    no right radius" (timeline) and "right corners only" (avatar) are both
    built here rather than open-coded twice.
    """
    path = QPainterPath()
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    path.moveTo(x + top_left, y)
    path.lineTo(x + w - top_right, y)
    if top_right:
        path.arcTo(QRectF(x + w - 2 * top_right, y, 2 * top_right, 2 * top_right), 90, -90)
    path.lineTo(x + w, y + h - bottom_right)
    if bottom_right:
        path.arcTo(QRectF(x + w - 2 * bottom_right, y + h - 2 * bottom_right,
                          2 * bottom_right, 2 * bottom_right), 0, -90)
    path.lineTo(x + bottom_left, y + h)
    if bottom_left:
        path.arcTo(QRectF(x, y + h - 2 * bottom_left, 2 * bottom_left, 2 * bottom_left), 270, -90)
    path.lineTo(x, y + top_left)
    if top_left:
        path.arcTo(QRectF(x, y, 2 * top_left, 2 * top_left), 180, -90)
    path.closeSubpath()
    return path


class ShareBar(QWidget):
    """1px rule showing this speaker's share of the programme.

    A QProgressBar would mean fighting its groove/chunk sub-controls and
    min-height at 1px, so this is two fillRects. Integer rects and no
    antialiasing keep the hairline on a device pixel.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ratio = 0.0
        self._color = QColor('#b8cee0')
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_share(self, ratio, color):
        ratio = max(0.0, min(1.0, float(ratio or 0.0)))
        color = QColor(color)
        if ratio == self._ratio and color == self._color:
            return
        self._ratio, self._color = ratio, color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor(255, 255, 255, 38))
            filled = int(round(self.width() * self._ratio))
            if filled > 0:
                painter.fillRect(QRect(0, 0, filled, self.height()), self._color)
        finally:
            painter.end()


class _ElidingLabel(QLabel):
    """QLabel that elides its own text to its own width.

    Eliding from the parent's resizeEvent reads a stale width (children are
    laid out after the parent is resized) and misses relayouts the parent
    never sees at all — notably the hover reveal, which shrinks this label by
    ~96px of action buttons without changing the header's size.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ''
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def setFullText(self, text):
        text = text or ''
        if text != self._full_text:
            self._full_text = text
            self._apply()

    def _apply(self):
        self.setText(self.fontMetrics().elidedText(
            self._full_text, Qt.ElideRight, max(0, self.width())))

    def resizeEvent(self, event):
        self._apply()
        return super().resizeEvent(event)


class SpeakerHeader(QWidget):
    """The card's top band: timeline strip, then the avatar painted over it.

    The strip spans the full width of this header, which is the body's width —
    the body already sits 10px in from the list, so the strip needs no inset of
    its own. The avatar is a sibling pinned outside the body and raised, so it
    overlaps the strip's left end without any painting order to coordinate.

    The inverse of that rule is the constraint to remember: child widgets
    paint AFTER their parent, so any child straying left of `_INFO_LEFT`
    would land on top of the avatar. The layout's left margin is what keeps
    the text stack and the action buttons clear of it.

    Deliberately has no styled background, so the card's QSS colour — including
    the selected state — shows through beneath the 50%-alpha strip.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor('#b8cee0')
        self._timeline = []
        self._duration = 60.0
        self._avatar = None          # cached, already rounded
        self._avatar_key = None      # cache key: what the pixmap was built from
        # Set by the card: a free-standing QLabel that lives OUTSIDE this
        # header's layout so it can sit flush at the list edge while the
        # body is inset.
        self.avatar_label = None

        self.setMinimumHeight(_AVATAR_SIZE)
        # Hug the content: the card should be no taller than it needs to be
        # when every option section is collapsed.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout = QHBoxLayout(self)
        # Top margin clears the strip so the name's glyphs never sit under it.
        layout.setContentsMargins(_INFO_LEFT - _BODY_LEFT_MARGIN, _TIMELINE_HEIGHT + 2, 4, 2)
        layout.setSpacing(0)

    # -- data ---------------------------------------------------------------
    def set_color(self, color):
        color = QColor(color)
        if color != self._color:
            self._color = color
            self.update()   # timeline only — the avatar chip is colour-agnostic

    def set_timeline(self, timeline, duration):
        self._timeline = timeline or []
        self._duration = float(duration or 60.0) or 60.0
        self.update()

    def set_avatar(self, image, color):
        """Build the rounded avatar pixmap, but only when its inputs change.

        `speakers_list_item.update()` runs on every speakers_changed, every
        face-recognition result and every playback refresh; scaling and
        re-masking a pixmap on each of those was measurable with many
        speakers, so the result is cached against its inputs.
        """
        key = (id(image) if image is not None else None,)   # colour no longer affects it
        if key == self._avatar_key:
            return
        self._avatar_key = key

        size = _AVATAR_SIZE
        canvas = QPixmap(size, size)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            path = _rounded_path(QRectF(0, 0, size, size),
                                 top_right=_CORNER_RADIUS, bottom_right=_CORNER_RADIUS)
            if image is not None:
                source = QPixmap.fromImage(image).scaled(
                    size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                # Fill the path with the photo as a texture brush rather than
                # clipping to it: a clip region is not antialiased in Qt's
                # raster engine, which would stair-step the rounded corners.
                brush = QBrush(source)
                offset_x = (size - source.width()) / 2.0
                offset_y = (size - source.height()) / 2.0
                transform = brush.transform()
                transform.translate(offset_x, offset_y)
                brush.setTransform(transform)
                painter.setBrush(brush)
            else:
                # Neutral chip, NOT the speaker colour — that colour identifies
                # the mini timeline and nothing else.
                painter.setBrush(QColor('#3e5363'))
            painter.setPen(Qt.NoPen)
            painter.drawPath(path)

            if image is None:
                # No face yet: keep the speaker glyph over the colour chip so
                # the slot still reads as a person, as it did before.
                glyph = QPixmap(str(session.PATH_SUBTITLD_GRAPHICS / 'left_panel_speakers.svg'))
                if not glyph.isNull():
                    glyph = glyph.scaled(22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    painter.setOpacity(0.55)
                    painter.drawPixmap(int((size - glyph.width()) / 2),
                                       int((size - glyph.height()) / 2), glyph)
        finally:
            painter.end()
        self._avatar = canvas
        if self.avatar_label is not None:
            self.avatar_label.setPixmap(canvas)
        self.update()

    # -- painting -----------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            # Only the strip is painted here now — the avatar is a separate
            # widget so it can overhang the body's left margin.
            self._paint_timeline(painter)
        finally:
            painter.end()

    def _paint_timeline(self, painter):
        band = QRectF(0.0, 0.0, float(self.width()), float(_TIMELINE_HEIGHT))
        if band.width() <= 0:
            return
        # Top-left radius only — square on the right, per the spec.
        path = _rounded_path(band, top_left=_CORNER_RADIUS)

        base = QColor(self._color)
        base.setAlphaF(0.5)
        painter.fillPath(path, base)

        if not self._timeline or self._duration <= 0:
            return
        painter.save()
        try:
            # Clip so a cue at t=0 respects the rounded corner and the inset.
            painter.setClipPath(path)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._color)   # full opacity: "no opacity"
            scale = band.width() / self._duration
            run_start = run_end = None
            for start, end in self._timeline:
                x0 = band.left() + start * scale
                x1 = band.left() + end * scale
                if x1 - x0 < 1.0:
                    x1 = x0 + 1.0          # keep very short cues visible
                if run_end is not None and x0 - run_end <= 1.0:
                    run_end = max(run_end, x1)   # coalesce touching cues
                    continue
                if run_end is not None:
                    painter.drawRect(QRectF(run_start, band.top(),
                                            run_end - run_start, band.height()))
                run_start, run_end = x0, x1
            if run_end is not None:
                painter.drawRect(QRectF(run_start, band.top(),
                                        run_end - run_start, band.height()))
        finally:
            painter.restore()


class _SectionHeader(QWidget):
    """Clickable header row of a CollapsibleSection."""
    clicked = Signal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        # Deliberately not accepted, so the press still reaches the card and
        # clicking a section header selects the speaker too.
        return super().mousePressEvent(event)


class CollapsibleSection(QWidget):
    """One collapsible row in a card's options container.

    Knows nothing about dubbing — the options container takes any number of
    these, so future speaker options drop in without touching the card.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)

        self.header = _SectionHeader()
        self.header.setObjectName('speaker_option_section_header')
        self.header.setAttribute(Qt.WA_StyledBackground, True)
        self.header.setFixedHeight(22)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setLayout(QHBoxLayout())
        # 10px left lines the title up with the timeline's own 10px inset.
        self.header.layout().setContentsMargins(10, 0, 2, 0)
        self.header.layout().setSpacing(6)

        self.title_label = QLabel()
        self.title_label.setObjectName('speaker_option_section_title')
        self.header.layout().addWidget(self.title_label, 1)

        self.toggle_button = QPushButton()
        self.toggle_button.setObjectName('speaker_option_section_toggle')
        self.toggle_button.setCheckable(True)
        self.toggle_button.setFixedSize(20, 20)
        self.toggle_button.setIconSize(QSize(16, 16))
        self.header.layout().addWidget(self.toggle_button, 0)

        self.layout().addWidget(self.header)

        self.body = QWidget()
        self.body.setObjectName('speaker_option_section_body')
        self.body.setLayout(QVBoxLayout())
        self.body.layout().setContentsMargins(10, 6, 10, 10)
        self.body.layout().setSpacing(6)
        self.body.setVisible(False)
        self.layout().addWidget(self.body)

        self.header.clicked.connect(lambda: self.set_expanded(not self.is_expanded()))
        self.toggle_button.clicked.connect(lambda: self.set_expanded(self.toggle_button.isChecked()))
        self.set_expanded(False)

    def set_title(self, text):
        # Uppercased here rather than trusting QSS text-transform.
        self.title_label.setText((text or '').upper())

    def set_content(self, content):
        self.body.layout().addWidget(content)

    def is_expanded(self):
        return bool(self.property('expanded'))

    def set_expanded(self, expanded):
        expanded = bool(expanded)
        self.setProperty('expanded', expanded)
        self.toggle_button.setChecked(expanded)
        self.body.setVisible(expanded)
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()


class RoundedCornerLabel(QLabel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.radius_top_left = 2  # 2px radius for top-left corner

    def paintEvent(self, event):
        pixmap = self.pixmap()
        if pixmap:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)

            # Create a rounded rectangle path with only top-left corner rounded
            path = QPainterPath()
            size = self.size()

            # Start from top-left with rounded corner
            path.moveTo(self.radius_top_left, 0)
            path.lineTo(size.width(), 0)  # Top edge
            path.lineTo(size.width(), size.height())  # Right edge
            path.lineTo(0, size.height())  # Bottom edge
            path.lineTo(0, self.radius_top_left)  # Left edge going up to the arc start
            # Arc from left edge to top edge (counter-clockwise for proper curve)
            path.arcTo(0, 0, self.radius_top_left * 2, self.radius_top_left * 2, 180, -90)  # Top-left arc
            path.closeSubpath()

            # Clip the drawing area to the path
            painter.setClipPath(path)

            # Draw the pixmap
            painter.drawPixmap(self.rect(), pixmap)
        else:
            # If no pixmap, just draw normally
            super().paintEvent(event)


class speakers_list_item(QWidget):
    def __init__(widget, speaker_name, speaker_data, parent=None):
        # Pass the parent through to QWidget. Without it the new widget
        # is briefly a TOP-LEVEL window — the addWidget() reparenting
        # below happens after construction. With many speakers populated
        # at once (e.g. right after transcription), each list item
        # flashes a borderless window on the screen before being absorbed
        # into the speakers panel. Constructing as a child of the panel
        # avoids the top-level state entirely.
        super().__init__(parent)
        widget.speaker_name = speaker_name
        widget.speaker_data = speaker_data
        
        widget.setObjectName('left_panel_speakers_panel_content_item')
        widget.setProperty('class', '')
        widget.setProperty('speaker_name', widget.speaker_name)
        widget.setLayout(QVBoxLayout())
        # Inset the body, not the whole card: the avatar is a child of the card
        # (not of the body) so it still starts at x=0 and overhangs this margin,
        # leaving a strip of list background behind it.
        widget.layout().setContentsMargins(_BODY_LEFT_MARGIN, 0, 0, 0)
        widget.layout().setSpacing(0)
        # Shrink to fit: with every section collapsed the card is just the
        # header plus the section headers, not a fixed block.
        widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        # Carries the background / hover / selected fill. Separate from the
        # card so the card itself can stay transparent where the avatar
        # overhangs.
        widget.body = QWidget()
        widget.body.setObjectName('speaker_card_body')
        widget.body.setAttribute(Qt.WA_StyledBackground, True)
        widget.body.setLayout(QVBoxLayout())
        widget.body.layout().setContentsMargins(0, 0, 0, 0)
        widget.body.layout().setSpacing(0)
        widget.layout().addWidget(widget.body)

        # --- Header band: strip (painted) and the info stack ---------------
        widget.header = SpeakerHeader(parent=widget.body)
        widget.body.layout().addWidget(widget.header)

        # The avatar is deliberately NOT in any layout. The body is inset by
        # `_BODY_LEFT_MARGIN` (via the QSS margin on this card), and the avatar
        # has to sit flush at the list edge, overhanging that inset — which a
        # laid-out child cannot do, since layouts clamp children to the
        # contents rect.
        widget.avatar_label = QLabel(widget)
        widget.avatar_label.setObjectName('speaker_card_avatar')
        widget.avatar_label.setFixedSize(_AVATAR_SIZE, _AVATAR_SIZE)
        widget.avatar_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        widget.header.avatar_label = widget.avatar_label
        header_row = widget.header.layout()

        info_stack = QVBoxLayout()
        info_stack.setContentsMargins(0, 0, 0, 0)
        info_stack.setSpacing(2)
        info_stack.addStretch()

        # Name and the action buttons share one line, sitting above the
        # progress bar rather than floating beside the whole stack.
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(0)

        widget.name_label = _ElidingLabel()
        widget.name_label.setObjectName('speaker_card_name')
        name_row.addWidget(widget.name_label, 1)

        # Action buttons keep their objectNames (and therefore their QSS
        # icons) and their hover-reveal behaviour.
        actions = QWidget()
        actions.setObjectName('speaker_card_actions')
        actions.setLayout(QHBoxLayout())
        actions.layout().setContentsMargins(0, 0, 0, 0)
        actions.layout().setSpacing(0)
        name_row.addWidget(actions, 0, Qt.AlignVCenter)
        info_stack.addLayout(name_row)

        widget.share_bar = ShareBar()
        info_stack.addWidget(widget.share_bar)

        widget.stats_label = QLabel()
        widget.stats_label.setObjectName('speaker_card_stats')
        info_stack.addWidget(widget.stats_label)
        info_stack.addStretch()
        header_row.addLayout(info_stack, 1)

        def _action(name, handler, checkable=False, visible=False, tooltip=None):
            button = QPushButton()
            button.setObjectName(f'left_panel_speakers_panel_content_item_{name}_button')
            button.setFixedSize(20, 20)
            button.setIconSize(QSize(14, 14))
            if checkable:
                button.setCheckable(True)
            button.setVisible(visible)
            if tooltip:
                button.setToolTip(tooltip)
            button.clicked.connect(handler)
            actions.layout().addWidget(button)
            return button

        widget.rename_button = _action('rename', lambda: rename_button_clicked(widget))
        widget.change_color_button = _action('change_color', lambda: change_color_button_clicked(widget))
        widget.change_image_button = _action('change_image', lambda: change_image_button_clicked(widget),
                                             tooltip=_('left_panel_speakers.change_image_tooltip'))
        widget.visibility_button = _action('visibility', lambda: toggle_visibility_button_clicked(widget),
                                           checkable=True, visible=True,
                                           tooltip=_('left_panel_speakers.toggle_visibility_tooltip'))
        widget.export_button = _action('export', lambda: export_button_clicked(widget))
        widget.remove_button = _action('remove', lambda: remove_button_clicked(widget))

        # --- Options container: collapsible sections -----------------------
        widget.options = QWidget()
        widget.options.setObjectName('speaker_card_options')
        widget.options.setLayout(QVBoxLayout())
        widget.options.layout().setContentsMargins(0, 0, 0, 0)
        widget.options.layout().setSpacing(1)
        widget.body.layout().addWidget(widget.options)

        widget.dubbing_line = CollapsibleSection(parent=widget)
        widget.dubbing_line.setObjectName('speaker_option_section')
        widget.options.layout().addWidget(widget.dubbing_line)

        widget.dubbing_container = dubbing_container(parent=widget)
        widget.dubbing_container.setProperty('speaker', widget.speaker_name)
        widget.dubbing_container.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)
        widget.dubbing_container.layout().setContentsMargins(0, 0, 0, 0)
        # The section's own chevron is the single collapse control the mockup
        # shows. dubbing_container's internal hide/expand button would be a
        # second, nested one, so it is retired and its content pinned open —
        # everything it holds is revealed by expanding the section instead.
        widget.dubbing_container.hideexpand_button.setVisible(False)
        widget.dubbing_container.setProperty('is_expanded', True)
        widget.dubbing_container.content.setVisible(True)
        widget.dubbing_line.set_content(widget.dubbing_container)

        widget.update()
        widget.translate()

    def resizeEvent(widget, event):
        # Pinned to the card's own left edge (x=0), outside the body's inset,
        # and raised so the header's strip cannot paint over it.
        widget.avatar_label.move(0, _AVATAR_TOP_OFFSET)
        widget.avatar_label.raise_()
        return super().resizeEvent(event)

    def enterEvent(widget, event):
        widget.rename_button.setVisible(True)
        widget.change_color_button.setVisible(True)
        widget.change_image_button.setVisible(True)
        widget.export_button.setVisible(False)
        widget.remove_button.setVisible(True)
        event.accept()

    def leaveEvent(widget, event):
        # The action buttons are children of the header, and moving onto a
        # child delivers Leave to the parent — without this guard the button
        # vanishes from under the pointer just as it is about to be clicked.
        if widget.rect().contains(widget.mapFromGlobal(QCursor.pos())):
            return
        widget.rename_button.setVisible(False)
        widget.change_color_button.setVisible(False)
        widget.change_image_button.setVisible(False)
        widget.export_button.setVisible(False)
        widget.remove_button.setVisible(False)
        event.accept()

    def mousePressEvent(widget, event):
        for sib_widget in widget.parent().children():
            if sib_widget.objectName() == 'left_panel_speakers_panel_content_item':
                if sib_widget.property('speaker_name') == widget.property('speaker_name'):
                    sib_widget.setProperty('selected', 'true')
                else:
                    sib_widget.setProperty('selected', 'false')
                _repolish_card(sib_widget)
            
        return super().mousePressEvent(event)

    def translate(widget):
        widget.dubbing_line.set_title(_('left_panel.tab_dubbing'))
        widget.dubbing_container.combobox.setLabel(_('subtitles_panel_widget_dubbing.dubbing_engine'))
        widget.dubbing_container.combobox.combobox.setPlaceholderText(_('subtitles_panel_widget_dubbing.no_engine_selected'))
        for i in range(widget.dubbing_container.content.count()):
            w = widget.dubbing_container.content.widget(i)
            w.translate()

    def update(widget):
        color = session.SPEAKERS.get(widget.speaker_name, {}).get('color', '#b8cee0')
        widget.header.set_color(color)
        widget.header.set_avatar(widget.speaker_data.get('image', None), color)

        mine = [s for s in session.SUBTITLE['segments']
                if s.get('speaker', 'A') == widget.speaker_name]
        widget.header.set_timeline(
            [[s['start'], s['end']] for s in mine],
            session.VIDEO.get('duration', 60),
        )

        speaker_time = round(sum(s['end'] - s['start'] for s in mine), 3)
        total_speaking_time = sum(s['end'] - s['start'] for s in session.SUBTITLE['segments'])
        # `total_speaking_time` is 0 when the cue list is empty or every
        # cue is zero-duration (some providers' placeholder finish callback
        # emits a single ``start=end=0.0`` segment until real per-utterance
        # timing is available).
        # Render 0% rather than crash the speakers panel — the user
        # sees 0% until real timings land.
        ratio = (speaker_time / total_speaking_time) if total_speaking_time > 0 else 0.0
        percentage = int(round(ratio * 100, 0))

        widget.name_label.setFullText(widget.speaker_name)
        widget.share_bar.set_share(ratio, color)
        widget.stats_label.setText(
            f'{_format_speaker_time(speaker_time)} <b>({percentage}%)</b>')

        widget.dubbing_line.setVisible(session.CONFIG['dubbing'].get('enabled', False))

        if session.CONFIG['dubbing'].get('enabled', False):
            for i in range(widget.dubbing_container.content.count()):
                w = widget.dubbing_container.content.widget(i)
                w.update()

    def update_dubbing_options(widget, options):
        from subtitld.interface.left_panel_dubbing import _addon_display_name
        inner = widget.dubbing_container.combobox.combobox
        widget.dubbing_container.combobox.clear()

        while widget.dubbing_container.content.count():
            w = widget.dubbing_container.content.widget(0)
            widget.dubbing_container.content.removeWidget(w)
            w.deleteLater()

        for option_id, engine in options.items():
            # Engine objects carry an `id` + `display_name`. Use those to
            # build a localizable label; fall back to the dict key for
            # ill-formed engines.
            label = _addon_display_name(engine) if hasattr(engine, 'id') else option_id
            inner.addItem(label, option_id)

            # Parent the panel to the stacked widget up-front so it never
            # exists as a parentless QWidget, which would otherwise be
            # eligible to flash as a top-level window before addWidget
            # reparents it (the source of the multi-window pop-up bug
            # when many speakers were rebuilt in quick succession).
            new_dubbing_panel = engine.speaker_panel(widget.dubbing_container.content)
            new_dubbing_panel.setProperty('speaker', widget.speaker_name)
            widget.dubbing_container.content.addWidget(new_dubbing_panel)
            new_dubbing_panel.update()

        saved_engine = session.SPEAKERS.get(widget.speaker_name, {}).get('dubbing', {}).get('engine')
        if saved_engine and saved_engine in options:
            for i in range(inner.count()):
                if inner.itemData(i) == saved_engine:
                    inner.setCurrentIndex(i)
                    break
        else:
            inner.setCurrentIndex(-1)

        widget.dubbing_container.update()
        widget.update()
        widget.translate()

class new_speaker_name_dialog(utils.SimpleDialog):
    def __init__(self, parent=None, title=''):
        super().__init__(parent, title)

        self.input_line = QWidget()
        self.input_line.setLayout(QHBoxLayout())
        self.input_line.layout().setContentsMargins(0, 0, 0, 0)

        self.input_label = QLabel('Please enter your name:')
        self.input_line.layout().addWidget(self.input_label)

        self.input = QLineEdit()
        self.input_line.layout().addWidget(self.input)

        self.content.layout().addWidget(self.input_line)


    def exec_and_get_values(self):
        if self.exec() == QDialog.Accepted:
            return self.input.text()
        return None


class rename_speaker_name_dialog(utils.SimpleDialog):
    def __init__(self, parent=None, title=''):
        super().__init__(parent, title)

        self.input_line = QWidget()
        self.input_line.setLayout(QHBoxLayout())
        self.input_line.layout().setContentsMargins(0, 0, 0, 0)

        self.input_label = QLabel('Please enter your name:')
        self.input_line.layout().addWidget(self.input_label)

        self.input = QLineEdit()
        self.input_line.layout().addWidget(self.input)

        self.content.layout().addWidget(self.input_line)


    def exec_and_get_values(self):
        if self.exec() == QDialog.Accepted:
            return self.input.text()
        return None


class remove_speaker_name_dialog(utils.SimpleDialog):
    REASSIGN = 'reassign'
    DELETE = 'delete'

    def __init__(self, parent=None, title=''):
        super().__init__(parent, title)

        self.prompt_label = QLabel()
        self.prompt_label.setWordWrap(True)
        self.content.layout().addWidget(self.prompt_label)

        self.input_line = QWidget()
        self.input_line.setLayout(QHBoxLayout())
        self.input_line.layout().setContentsMargins(0, 0, 0, 0)

        self.input_label = QLabel('Please select the speaker to replace with the removed speaker:')
        self.input_line.layout().addWidget(self.input_label)

        self.select = QComboBox()
        self.input_line.layout().addWidget(self.select)

        self.content.layout().addWidget(self.input_line)

        self.delete_button = QPushButton()
        self.delete_button.setProperty('class', 'danger')
        self.accept_button.parent().layout().insertWidget(1, self.delete_button)
        self.delete_button.clicked.connect(self._delete_clicked)
        self._action = None

    def _delete_clicked(self):
        self._action = self.DELETE
        self.accept()

    def exec_and_get_values(self):
        self._action = None
        if self.exec() == QDialog.Accepted:
            action = self._action or self.REASSIGN
            target = self.select.currentText() if action == self.REASSIGN else None
            return (action, target)
        return None


def load(self):
    tab_name = 'speakers'
    
    left_panel_speakers_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )
    # Full-bleed list: the cards reach both panel edges so the avatar can sit
    # flush against the left. The add button re-inserts its own inset below,
    # otherwise it would end up jammed into the corner.
    left_panel_speakers_panel.layout().setContentsMargins(0, 0, 0, 0)

    left_panel_speakers_panel_scroll = QScrollArea()
    left_panel_speakers_panel_scroll.setObjectName('left_panel_speakers_panel_scroll')
    left_panel_speakers_panel_scroll.setWidgetResizable(True)
    left_panel_speakers_panel_scroll.setFrameShape(QScrollArea.NoFrame)
    left_panel_speakers_panel.layout().addWidget(left_panel_speakers_panel_scroll)

    left_panel_speakers_panel_content = QWidget()
    left_panel_speakers_panel_content.setProperty('class', 'transparent_panel')
    left_panel_speakers_panel_content.setObjectName('left_panel_speakers_panel_content')
    left_panel_speakers_panel_content.setLayout(QVBoxLayout())
    left_panel_speakers_panel_content.layout().setContentsMargins(0, 0, 0, 0)
    left_panel_speakers_panel_content.layout().setSpacing(10)
    left_panel_speakers_panel_scroll.setWidget(left_panel_speakers_panel_content)

    self.left_panel_speakers_list = QWidget()
    self.left_panel_speakers_list.setObjectName('left_panel_speakers_list')
    self.left_panel_speakers_list.setLayout(QVBoxLayout())
    self.left_panel_speakers_list.layout().setContentsMargins(0, 0, 0, 0)
    self.left_panel_speakers_list.layout().setSpacing(6)
    left_panel_speakers_panel_content.layout().addWidget(self.left_panel_speakers_list)

    self.left_panel_speakers_add_button = QPushButton()
    self.left_panel_speakers_add_button.clicked.connect(lambda: left_panel_speakers_add_speaker_button_clicked(self))
    add_button_row = QWidget()
    add_button_row.setProperty('class', 'transparent_panel')
    add_button_row.setLayout(QHBoxLayout())
    add_button_row.layout().setContentsMargins(10, 10, 10, 10)
    add_button_row.layout().addStretch()
    add_button_row.layout().addWidget(self.left_panel_speakers_add_button)
    left_panel_speakers_panel_content.layout().addWidget(add_button_row)

    left_panel_speakers_panel_content.layout().addStretch()

    def handle_face_result(data):
        if data["image"] is not None:
            if not data["name"] in session.SPEAKERS:
                session.SPEAKERS[data["name"]] = {}
            session.SPEAKERS[data["name"]]['image'] = data["image"]
            update_speakers_list(self)

    self.left_panel_speakers_image_test_thread = FaceExtractorThread(parent=self)
    self.left_panel_speakers_image_test_thread.result.connect(handle_face_result)

    # Off-main-thread speaker-thumbnail decode (file_io._SpeakerImageLoader)
    # writes into session.SPEAKERS[name]['image'] from a worker thread and
    # emits `speaker_image_ready(name)` on the global signal hub. We
    # re-render the speakers list on each arrival so the placeholder icon
    # is replaced as soon as the decode lands. The signal delivery is
    # queued (cross-thread), so this slot always runs on the main thread.
    _SESSION_SIGNALS.speaker_image_ready.connect(lambda _name: update_speakers_list(self))
    # Re-render whenever transcription (or anything else) mutates
    # `session.SPEAKERS`. Without this, after-transcription panel
    # selection shows an empty list until the user re-selects the panel.
    _SESSION_SIGNALS.speakers_changed.connect(lambda: update_speakers_list(self))

    self.left_panel_speakers_new_name_dialog = new_speaker_name_dialog(self, 'New speaker')

    self.left_panel_speakers_rename_dialog = rename_speaker_name_dialog(parent=self)
    self.left_panel_speakers_remove_dialog = remove_speaker_name_dialog(parent=self)

    self.left_panel_speakers_list_of_available_dubbing_engine = {}

    update(self)



def _repolish_card(card):
    """Re-evaluate a card's stylesheet, children included.

    Qt's unpolish/polish does NOT cascade, so repolishing only the card
    reapplies its `[selected=true]` background while the name and stats
    labels keep their cached light ink — unreadable against the light
    selection tint. The labels' colour depends on an ancestor's property,
    so they have to be repolished explicitly.
    """
    section = getattr(card, 'dubbing_line', None)
    for target in (card, getattr(card, 'body', None),
                   getattr(card, 'name_label', None), getattr(card, 'stats_label', None),
                   getattr(section, 'header', None), getattr(section, 'title_label', None)):
        if target is None:
            continue
        target.style().unpolish(target)
        target.style().polish(target)
        target.update()


def change_color_button_clicked(widget):
    color_dialog = QColorDialog(session.SPEAKERS[widget.speaker_name]['color'], widget)
    color = color_dialog.getColor()
    if color.isValid():
        session.SPEAKERS[widget.speaker_name]['color'] = f'{color.name()}'
        widget.window().timeline_widget.update()
        session.set_unsaved()


def change_image_button_clicked(widget):
    window = widget.window()
    preview = getattr(window, 'preview_panel_player', None)
    if preview is None or not hasattr(preview, 'start_face_selection'):
        return
    preview.start_face_selection(widget.speaker_name)


def export_button_clicked(widget):
    pass


def toggle_visibility_button_clicked(widget):
    """Flip the speaker's hidden flag and refresh affected views."""
    speaker_name = widget.speaker_name
    if speaker_name not in session.SPEAKERS:
        return
    new_hidden = bool(widget.visibility_button.isChecked())
    session.SPEAKERS[speaker_name]['hidden'] = new_hidden
    session.set_unsaved(True)
    _apply_speaker_visibility(widget.window())


def _apply_speaker_visibility(window):
    """Push the per-speaker `hidden` flag out to timeline rendering and the
    audio engine. Subtitles for hidden speakers stay in the model; only the
    rendering / playback ignore them."""
    timeline_widget = getattr(window, 'timeline_widget', None)
    if timeline_widget is not None:
        timeline_widget.update()
    preview = getattr(window, 'preview_panel_player', None)
    if preview is not None:
        engine = getattr(preview, '_audio_device', None)
        if engine is not None and hasattr(engine, 'speaker_tracks'):
            dub_enabled = bool(session.CONFIG.get('dubbing', {}).get('enabled', False))
            for name, track in engine.speaker_tracks.items():
                hidden = bool(session.SPEAKERS.get(name, {}).get('hidden', False))
                track.enabled = dub_enabled and not hidden


def highlight_speaker_for_selection(window):
    """Mark the speaker matching the currently-selected subtitle. Called by
    the subtitles list whenever the selection changes so the user sees which
    speaker the active subtitle belongs to."""
    speakers_list = getattr(window, 'left_panel_speakers_list', None)
    if speakers_list is None:
        return
    selected_speaker = (session.SUBTITLE.get('selected') or {}).get('speaker', '') or ''
    layout = speakers_list.layout()
    for i in range(layout.count()):
        item = layout.itemAt(i)
        widget = item.widget() if item is not None else None
        if not isinstance(widget, speakers_list_item):
            continue
        is_match = widget.speaker_name == selected_speaker
        if widget.property('selected_speaker') != is_match:
            widget.setProperty('selected_speaker', is_match)
            _repolish_card(widget)


def remove_button_clicked(widget):
    speaker_name = widget.speaker_name
    used_segments = [s for s in session.SUBTITLE['segments'] if s.get('speaker', 'A') == speaker_name]
    other_speakers = [name for name in session.SPEAKERS.keys() if name != speaker_name]

    if len(session.SPEAKERS) < 2:
        no_remove_alert = utils.SimpleDialog(widget.window(), _('subtitles_panel_widget_speakers.remove_speaker'))
        no_remove_alert.content.layout().addWidget(QLabel(_('subtitles_panel_widget_speakers.cannot_remove_last_speaker')))
        no_remove_alert.reject_button.setVisible(False)
        no_remove_alert.exec()
        return

    if not used_segments:
        remove_alert = utils.SimpleDialog(widget.window(), _('subtitles_panel_widget_speakers.remove_speaker'))
        remove_alert.content.layout().addWidget(QLabel(_('subtitles_panel_widget_speakers.sure_to_remove_speaker')))
        if remove_alert.exec():
            del session.SPEAKERS[speaker_name]
            update_speakers_list(widget.window())
            session.set_unsaved()
        return

    dialog = widget.window().left_panel_speakers_remove_dialog
    dialog.set_title(_('subtitles_panel_widget_speakers.remove_speaker'))
    dialog.prompt_label.setText(_('subtitles_panel_widget_speakers.remove_speaker_with_subtitles_prompt').format(speaker=speaker_name, count=len(used_segments)))
    dialog.input_label.setText(_('subtitles_panel_widget_speakers.reassign_to'))
    dialog.select.clear()
    dialog.select.addItems(other_speakers)
    dialog.accept_button.setText(_('subtitles_panel_widget_speakers.reassign_subtitles'))
    dialog.delete_button.setText(_('subtitles_panel_widget_speakers.delete_subtitles'))

    result = dialog.exec_and_get_values()
    if not result:
        return

    action, target = result
    if action == dialog.DELETE:
        for segment in list(used_segments):
            session.SUBTITLE['segments'].remove(segment)
        if session.SUBTITLE.get('selected') in used_segments:
            session.SUBTITLE['selected'] = None
    elif action == dialog.REASSIGN and target:
        for segment in used_segments:
            segment['speaker'] = target
    else:
        return

    del session.SPEAKERS[speaker_name]
    update_speakers_list(widget.window())
    session.set_unsaved()
    timeline_widget = getattr(widget.window(), 'timeline_widget', None)
    if timeline_widget is not None:
        timeline_widget.update()


def rename_button_clicked(widget):
    widget.window().left_panel_speakers_rename_dialog.set_title(_('subtitles_panel_widget_speakers.rename_speaker'))
    widget.window().left_panel_speakers_rename_dialog.input_label.setText(_('subtitles_panel_widget_speakers.enter_speaker_name'))
    widget.window().left_panel_speakers_rename_dialog.input.setText(widget.speaker_name)
    widget.window().left_panel_speakers_rename_dialog.input.selectAll()

    value = widget.window().left_panel_speakers_rename_dialog.exec_and_get_values()
    
    if value:
        new_name = value.strip()
    else:
        return

    history.history_append()
    old_speaker = session.SPEAKERS[widget.speaker_name]
    session.SPEAKERS[new_name] = old_speaker

    for segment in session.SUBTITLE['segments']:
        if segment.get('speaker') == widget.speaker_name:
            segment['speaker'] = new_name

    del session.SPEAKERS[widget.speaker_name]

    session.set_unsaved(True)
    update_speakers_list(widget.window())


def update_speakers_list(self):
    # Throttle: transcription emits one `speakers_changed` per new
    # speaker, face recognition emits one result per completed scan —
    # so this function gets called in rapid bursts. Each call iterates
    # session.SPEAKERS and touches a widget per speaker; without
    # throttling, a 50-speaker transcription does ~50 sequential passes.
    # Coalesce into one pass via a 30 ms single-shot timer (under
    # human-perception threshold, so feels instant).
    if getattr(self, '_speakers_list_update_pending', False):
        return
    self._speakers_list_update_pending = True
    QTimer.singleShot(30, lambda: _update_speakers_list_actual(self))


def _update_speakers_list_actual(self):
    self._speakers_list_update_pending = False
    # Differential update — keep a per-window registry of name → widget
    # so we don't tear down and rebuild every item on each call. The
    # previous implementation did a full destroy + rebuild; each
    # speakers_list_item creates ~10 sub-widgets plus a dubbing_container
    # that builds an engine panel for every registered TTS provider, so
    # rebuilding 50 speakers takes hundreds of ms.
    layout = self.left_panel_speakers_list.layout()
    registry = getattr(self, '_speakers_list_widgets', None)
    if registry is None:
        registry = {}
        self._speakers_list_widgets = registry

    current_names = list(session.SPEAKERS.keys())
    current_name_set = set(current_names)
    available_engines = self.left_panel_speakers_list_of_available_dubbing_engine

    # Remove widgets for speakers no longer present.
    for name in list(registry):
        if name not in current_name_set:
            stale = registry.pop(name)
            layout.removeWidget(stale)
            stale.setParent(None)
            stale.deleteLater()

    # Add / refresh widgets for each current speaker, in order.
    for index, speaker_name in enumerate(current_names):
        speaker_data = session.SPEAKERS[speaker_name]
        if not speaker_data.get('color', False):
            gen = AutoHex()
            speaker_data['color'] = f'{gen.gen(speaker_name)}'

        widget = registry.get(speaker_name)
        if widget is None:
            # New speaker — construct once, parented to the list, and
            # build its engine panels.
            widget = speakers_list_item(speaker_name, speaker_data,
                                        parent=self.left_panel_speakers_list)
            registry[speaker_name] = widget
            layout.insertWidget(index, widget)
            widget.update_dubbing_options(available_engines)
        else:
            # Existing widget — refresh data without recreating it.
            # `update()` re-renders the icon + name + time stats from
            # the current speaker_data / session.SUBTITLE; the engine
            # panels' own `update()` runs inside it.
            widget.speaker_data = speaker_data
            widget.update()
            # Layout order might have changed (rename, reorder). Cheap
            # to re-pin via insertWidget — Qt detaches first if already
            # in the layout, so this is a no-op when the position is
            # already correct.
            if layout.indexOf(widget) != index:
                layout.insertWidget(index, widget)

        widget.visibility_button.setChecked(bool(speaker_data.get('hidden', False)))

        if not speaker_data.get('image', None) and not self.left_panel_speakers_image_test_thread.isRunning():
            self.left_panel_speakers_image_test_thread.name = speaker_name
            self.left_panel_speakers_image_test_thread.start()

    highlight_speaker_for_selection(self)
    _apply_speaker_visibility(self)
    

def update(self):
    update_speakers_list(self)


def left_panel_speakers_remove_speaker_button_clicked(self):
    selected_items = self.left_panel_speakers_list.selectedItems()
    if not selected_items:
        return
    for item in selected_items:
        speaker_name = item.text()
        if any(subtitle.get('speaker', 'A') == speaker_name for subtitle in session.SUBTITLE['segments']):
            continue


def left_panel_speakers_add_speaker_button_clicked(self):
    new_name = None
    value = self.left_panel_speakers_new_name_dialog.exec_and_get_values()
    
    if value:
        new_name = value.strip()
    else:
        pass
    subtitles_names = [subtitle.get('speaker', 'A') for subtitle in session.SUBTITLE['segments']]

    if new_name and new_name not in session.SPEAKERS and not new_name in subtitles_names:
        session.SPEAKERS[new_name] = {}
        update_speakers_list(self)
    

def translate(self):
    self.left_panel_speakers_add_button.setText(_('subtitles_panel_widget_speakers.add_speaker'))
    self.left_panel_speakers_new_name_dialog.set_title(_('subtitles_panel_widget_speakers.new_speaker'))
    self.left_panel_speakers_new_name_dialog.input_label.setText(_('subtitles_panel_widget_speakers.enter_speaker_name'))
