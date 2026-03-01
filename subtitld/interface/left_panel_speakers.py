import cv2
import mediapipe as mp
import numpy as np
from colorhash import ColorHash

from PySide6.QtWidgets import QVBoxLayout, QWidget, QScrollArea, QHBoxLayout, QDialog, QPushButton, QLabel, QLineEdit, QListWidgetItem, QSizePolicy
from PySide6.QtGui import QImage, QPixmap, QPainter, QBrush, QPen, QPainterPath, QColor
from PySide6.QtCore import QThread, Signal, Qt, QRect, QPoint, QSize

from subtitld.interface import utils
from subtitld.interface import left_panel
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import subtitles
import random


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

    add_button = QPushButton('Add speaker')
    add_button.clicked.connect(lambda: left_panel_speakers_add_speaker_button_clicked(self))
    left_panel_speakers_panel_content.layout().addWidget(add_button)

    left_panel_speakers_panel_content.layout().addStretch()

    def handle_face_result(data):
        if data["image"] is not None:
            if not data["name"] in session.SPEAKERS:
                session.SPEAKERS[data["name"]] = {}
            session.SPEAKERS[data["name"]]['image'] = data["image"]
            update_speakers_list(self)

    self.left_panel_speakers_image_test_thread = FaceExtractorThread(parent=self)
    self.left_panel_speakers_image_test_thread.result.connect(handle_face_result)
    
    self.left_panel_speakers_new_name_dialog = new_speaker_name_dialog(self, 'New speaker')

    update(self)

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
    def __init__(widget, speaker_name, speaker_data):
        super().__init__()
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
        up_line.layout().addWidget(widget.rename_button)

        widget.change_color_button = QPushButton()
        widget.change_color_button.setObjectName('left_panel_speakers_panel_content_item_change_color_button')
        widget.change_color_button.setFixedSize(24, 24)
        widget.change_color_button.setIconSize(QSize(16, 16))
        widget.change_color_button.setVisible(False)
        up_line.layout().addWidget(widget.change_color_button)

        widget.export_button = QPushButton()
        widget.export_button.setObjectName('left_panel_speakers_panel_content_item_export_button')
        widget.export_button.setFixedSize(24, 24)
        widget.export_button.setIconSize(QSize(16, 16))
        widget.export_button.setVisible(False)
        up_line.layout().addWidget(widget.export_button)

        widget.remove_button = QPushButton()
        widget.remove_button.setObjectName('left_panel_speakers_panel_content_item_remove_button')
        widget.remove_button.setFixedSize(24, 24)
        widget.remove_button.setIconSize(QSize(16, 16))
        widget.remove_button.setVisible(False)
        up_line.layout().addWidget(widget.remove_button)

        widget.layout().addWidget(up_line)

        class small_timeline(QLabel):
            def __init__(widget, speaker_name=False, timeline=[], duration=30):
                super().__init__()
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

        widget.bottom_line = small_timeline(speaker_name=widget.speaker_name)
        widget.bottom_line.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        widget.bottom_line.setObjectName('left_panel_speakers_panel_content_item_bottom_line')
        widget.bottom_line.setFixedHeight(5)
        widget.layout().addWidget(widget.bottom_line)

        widget.update()

    def enterEvent(widget, event):
        widget.rename_button.setVisible(True)
        widget.change_color_button.setVisible(True)
        widget.export_button.setVisible(True)
        widget.remove_button.setVisible(True)
        event.accept()

    def leaveEvent(widget, event):
        widget.rename_button.setVisible(False)
        widget.change_color_button.setVisible(False)
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
        

def update_speakers_list(self):
    while self.left_panel_speakers_list.layout().count():
        item = self.left_panel_speakers_list.layout().takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue    

    for speaker_name, speaker_data in session.SPEAKERS.items():
        if not speaker_data.get('color', False):
            speaker_data['color'] = f'{ColorHash(speaker_name, saturation=[0.6], lightness=[0.7]).hex}'
        
        widget = speakers_list_item(speaker_name, speaker_data)
        
        if not speaker_data.get('image', None) and not self.left_panel_speakers_image_test_thread.isRunning():
            self.left_panel_speakers_image_test_thread.name = speaker_name
            self.left_panel_speakers_image_test_thread.start()

        self.left_panel_speakers_list.layout().addWidget(widget)
    

def update(self):
    update_speakers_list(self)

def left_panel_speakers_remove_speaker_button_clicked(self):
    selected_items = self.left_panel_speakers_list.selectedItems()
    if not selected_items:
        return
    for item in selected_items:
        speaker_name = item.text()
        # Check if the speaker is used in any subtitle segment
        if any(subtitle.get('speaker', 'A') == speaker_name for subtitle in session.SUBTITLE['segments']):
            continue  # Skip removal if speaker is in use
        # if speaker_name in session.SPEAKERS:
        #     session.SPEAKERS.remove(speaker_name)
        #     update_speakers_list(self)


def left_panel_speakers_add_speaker_button_clicked(self):
    new_name = None
    values = self.left_panel_speakers_new_name_dialog.exec_and_get_values()
    
    if values:
        # Get the value from the first input widget (the name input field)
        # values = self.left_panel_speakers_new_name_dialog.get_values()
        new_name = values[0].strip()  # Get the first input value and strip whitespace
    else:
        pass
    subtitles_names = [subtitle.get('speaker', 'A') for subtitle in session.SUBTITLE['segments']]

    # if new_name and new_name not in session.SPEAKERS and not new_name in subtitles_names:
    #     session.SPEAKERS.append(new_name)
    #     update_speakers_list(self)
    

def translate(self):
    self.left_panel_speakers_new_name_dialog.set_title(_('subtitles_panel_widget_speakers.new_speaker'))
    self.left_panel_speakers_new_name_dialog.input_label.setText(_('subtitles_panel_widget_speakers.enter_speaker_name'))
    


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
