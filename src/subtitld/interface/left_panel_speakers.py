import cv2
import mediapipe as mp
import numpy as np
from autohex import AutoHex

from PySide6.QtWidgets import QVBoxLayout, QWidget, QScrollArea, QHBoxLayout, QDialog, QPushButton, QLabel, QLineEdit, QSizePolicy, QColorDialog, QComboBox, QCheckBox, QStackedWidget
from PySide6.QtGui import QImage, QPixmap, QPainter, QPainterPath, QColor, QPolygonF, QCursor
from PySide6.QtCore import QThread, QTimer, Signal, Qt, QSize, QRect, QPoint

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
        
        widget.setAttribute(Qt.WA_StyledBackground, True)
        widget.setObjectName('left_panel_speakers_panel_content_item')
        widget.setProperty('class', '')
        widget.setProperty('speaker_name', widget.speaker_name)
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 0)
        widget.layout().setSpacing(0)

        up_line = QWidget()
        up_line.setLayout(QHBoxLayout())
        up_line.layout().setContentsMargins(0, 0, 10, 0)

        widget.speaker_icon = RoundedCornerLabel()
        widget.speaker_icon.setFixedSize(42, 42)
        widget.speaker_icon.setScaledContents(True)
        up_line.layout().addWidget(widget.speaker_icon)

        widget.name_label = QLabel()
        up_line.layout().addWidget(widget.name_label)

        widget.rename_button = QPushButton()
        widget.rename_button.setObjectName('left_panel_speakers_panel_content_item_rename_button')
        widget.rename_button.setFixedSize(24, 24)
        widget.rename_button.setIconSize(QSize(16, 16))
        widget.rename_button.setVisible(False)
        widget.rename_button.clicked.connect(lambda: rename_button_clicked(widget))
        up_line.layout().addWidget(widget.rename_button)

        widget.change_color_button = QPushButton()
        widget.change_color_button.setObjectName('left_panel_speakers_panel_content_item_change_color_button')
        widget.change_color_button.setFixedSize(24, 24)
        widget.change_color_button.setIconSize(QSize(16, 16))
        widget.change_color_button.setVisible(False)
        widget.change_color_button.clicked.connect(lambda: change_color_button_clicked(widget))
        up_line.layout().addWidget(widget.change_color_button)

        widget.change_image_button = QPushButton()
        widget.change_image_button.setObjectName('left_panel_speakers_panel_content_item_change_image_button')
        widget.change_image_button.setFixedSize(24, 24)
        widget.change_image_button.setIconSize(QSize(16, 16))
        widget.change_image_button.setVisible(False)
        widget.change_image_button.setToolTip(_('left_panel_speakers.change_image_tooltip'))
        widget.change_image_button.clicked.connect(lambda: change_image_button_clicked(widget))
        up_line.layout().addWidget(widget.change_image_button)

        widget.visibility_button = QPushButton()
        widget.visibility_button.setObjectName('left_panel_speakers_panel_content_item_visibility_button')
        widget.visibility_button.setFixedSize(24, 24)
        widget.visibility_button.setIconSize(QSize(16, 16))
        widget.visibility_button.setCheckable(True)
        widget.visibility_button.setToolTip(_('left_panel_speakers.toggle_visibility_tooltip'))
        widget.visibility_button.clicked.connect(lambda: toggle_visibility_button_clicked(widget))
        up_line.layout().addWidget(widget.visibility_button)

        widget.export_button = QPushButton()
        widget.export_button.setObjectName('left_panel_speakers_panel_content_item_export_button')
        widget.export_button.setFixedSize(24, 24)
        widget.export_button.setIconSize(QSize(16, 16))
        widget.export_button.setVisible(False)
        widget.export_button.clicked.connect(lambda: export_button_clicked(widget))
        up_line.layout().addWidget(widget.export_button)

        widget.remove_button = QPushButton()
        widget.remove_button.setObjectName('left_panel_speakers_panel_content_item_remove_button')
        widget.remove_button.setFixedSize(24, 24)
        widget.remove_button.setIconSize(QSize(16, 16))
        widget.remove_button.setVisible(False)
        widget.remove_button.clicked.connect(lambda: remove_button_clicked(widget))
        up_line.layout().addWidget(widget.remove_button)

        widget.layout().addWidget(up_line)

        widget.dubbing_line = QWidget()
        widget.dubbing_line.setLayout(QHBoxLayout())
        widget.dubbing_line.layout().setContentsMargins(0, 0, 0, 0)
        widget.dubbing_line.layout().setSpacing(0)
        widget.layout().addWidget(widget.dubbing_line)

        widget.dubbing_container = dubbing_container(parent=widget)
        widget.dubbing_container.setProperty('speaker', widget.speaker_name)
        widget.dubbing_container.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)
        widget.dubbing_line.layout().addWidget(widget.dubbing_container)
        
        class small_timeline(QLabel):
            def __init__(widget, speaker_name=False, timeline=[], duration=30, parent=None):
                # Pass parent through so this isn't briefly a top-level
                # window before the addWidget() reparenting — same
                # rationale as `speakers_list_item.__init__`.
                super().__init__(parent)
                widget.timeline = timeline
                widget.duration = duration
                widget.speaker_name = speaker_name
                
            def paintEvent(widget, event):
                if widget.speaker_name:
                    painter = QPainter(widget)
                    painter.setRenderHint(QPainter.Antialiasing)
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(session.SPEAKERS[widget.speaker_name].get('color', '#b8cee0')))
                    painter.setOpacity(0.5)
                    for segment in widget.timeline:
                        start_x = (segment[0] / widget.duration) * widget.width()
                        end_x = (segment[1] / widget.duration) * widget.width()
                        painter.drawRect(start_x, 0, end_x - start_x, widget.height())

                return super().paintEvent(event)

            def update(widget, timeline=False, duration=False, color=False):
                if timeline:
                    widget.timeline = timeline
                if duration:
                    widget.duration = duration
                if color:
                    widget.color = color

        widget.bottom_line = small_timeline(speaker_name=widget.speaker_name, parent=widget)
        widget.bottom_line.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        widget.bottom_line.setObjectName('left_panel_speakers_panel_content_item_bottom_line')
        widget.bottom_line.setFixedHeight(5)
        widget.layout().addWidget(widget.bottom_line)
        
        widget.update()
        widget.translate()

    def enterEvent(widget, event):
        widget.rename_button.setVisible(True)
        widget.change_color_button.setVisible(True)
        widget.change_image_button.setVisible(True)
        widget.export_button.setVisible(False)
        widget.remove_button.setVisible(True)
        event.accept()

    def leaveEvent(widget, event):
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
                sib_widget.style().unpolish(sib_widget)
                sib_widget.style().polish(sib_widget)
            
        return super().mousePressEvent(event)

    def translate(widget):
        widget.dubbing_container.combobox.setLabel(_('subtitles_panel_widget_dubbing.dubbing_engine'))
        widget.dubbing_container.combobox.combobox.setPlaceholderText(_('subtitles_panel_widget_dubbing.no_engine_selected'))
        for i in range(widget.dubbing_container.content.count()):
            w = widget.dubbing_container.content.widget(i)
            w.translate()

    def update(widget):
        widget.speaker_icon.setPixmap(
            QPixmap(str(session.PATH_SUBTITLD_GRAPHICS / 'left_panel_speakers.svg')) if not widget.speaker_data.get('image', None)
            else QPixmap.fromImage(widget.speaker_data['image']).scaled(36, 36)
        )
        widget.bottom_line.update(
            timeline=[[segment['start'], segment['end']] for segment in session.SUBTITLE['segments'] if segment.get('speaker', 'A') == widget.speaker_name],
            duration=session.VIDEO.get('duration', 60)
        )
        speaker_time = round(sum([segment['end'] - segment['start'] for segment in session.SUBTITLE['segments'] if segment.get('speaker', 'A') == widget.speaker_name]), 3)
        total_speaking_time = sum([segment['end'] - segment['start'] for segment in session.SUBTITLE['segments']])
        percentage = int(round((speaker_time / total_speaking_time) * 100, 0))
        widget.name_label.setText('<b>' + widget.speaker_name + '</b><br><small>' + f'{speaker_time} sec. ({percentage}%)' + '</small>')

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
    left_panel_speakers_panel_content.layout().addWidget(self.left_panel_speakers_list)

    self.left_panel_speakers_add_button = QPushButton()
    self.left_panel_speakers_add_button.clicked.connect(lambda: left_panel_speakers_add_speaker_button_clicked(self))
    left_panel_speakers_panel_content.layout().addWidget(self.left_panel_speakers_add_button, 0, Qt.AlignmentFlag.AlignRight)

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
            widget.style().unpolish(widget)
            widget.style().polish(widget)


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
