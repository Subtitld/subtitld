import cv2
import mediapipe as mp
import numpy as np

from PySide6.QtWidgets import QVBoxLayout, QWidget, QListWidget, QHBoxLayout, QDialog, QPushButton, QLabel, QLineEdit, QListWidgetItem
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtCore import QThread, Signal

from subtitld.interface import utils
from subtitld.interface import left_panel
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import subtitles


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
    left_panel.add_button(self, tab_name)
    
    class left_panel_speakers_panel_qwidget(QWidget):
        def __init__(self):
            super().__init__()
            self.setObjectName(f'left_panel_{tab_name}')
            self.setLayout(QVBoxLayout())
            self.layout().setContentsMargins(0, 0, 0, 0)
        
        def showEvent(self, event):
            update_speakers_list(self.window())
            return super().showEvent(event)

    left_panel_speakers_panel = left_panel_speakers_panel_qwidget()

    self.left_panel_speakers_list = QListWidget()
    self.left_panel_speakers_list.setObjectName('left_panel_speakers_list')
    # self.left_panel_speakers_list.itemSelectionChanged.connect(lambda: left_panel_speakers_list_item_selection_changed(self))
    left_panel_speakers_panel.layout().addWidget(self.left_panel_speakers_list)

    bottom_line = QHBoxLayout()
    bottom_line.setContentsMargins(0, 0, 0, 0)

    self.left_panel_speakers_add_speaker_button = QPushButton()
    self.left_panel_speakers_add_speaker_button.setObjectName('left_panel_speakers_add_speaker_button')
    self.left_panel_speakers_add_speaker_button.clicked.connect(lambda: left_panel_speakers_add_speaker_button_clicked(self))
    bottom_line.addWidget(self.left_panel_speakers_add_speaker_button)
    bottom_line.addStretch()

    self.left_panel_speakers_remove_speaker_button = QPushButton()
    self.left_panel_speakers_remove_speaker_button.setObjectName('left_panel_speakers_remove_speaker_button')
    self.left_panel_speakers_remove_speaker_button.clicked.connect(lambda: left_panel_speakers_remove_speaker_button_clicked(self))
    self.left_panel_speakers_remove_speaker_button.setVisible(False)
    bottom_line.addWidget(self.left_panel_speakers_remove_speaker_button)
    bottom_line.addStretch()

    left_panel_speakers_panel.layout().addLayout(bottom_line)

    self.left_panel_speakers_image_test = QLabel()
    left_panel_speakers_panel.layout().addWidget(self.left_panel_speakers_image_test)
    
    left_panel.add_panel(self, left_panel_speakers_panel)

    def handle_face_result(data):
        if data["image"] is not None:
            if not data["name"] in session.SPEAKERS:
                session.SPEAKERS[data["name"]] = {}
            session.SPEAKERS[data["name"]]['image'] = data["image"]
            update_speakers_list(self)

    self.left_panel_speakers_image_test_thread = FaceExtractorThread(parent=self)
    self.left_panel_speakers_image_test_thread.result.connect(handle_face_result)
    
    self.left_panel_speakers_new_name_dialog = NewNameDialog(self)
    

def update_speakers_list(self):
    self.left_panel_speakers_list.clear()

    for speaker_name, speaker_data in session.SPEAKERS.items():
        widget = QWidget()
        widget.setLayout(QHBoxLayout())
        widget.layout().setContentsMargins(10, 10, 10, 10)
        speaker_icon = QLabel()
        speaker_icon.setFixedSize(32, 32)
        speaker_icon.setScaledContents(True)
        speaker_icon.setPixmap(
            QPixmap(str(session.PATH_SUBTITLD_GRAPHICS / 'left_panel_speakers.svg')) if not speaker_data.get('image', None)
            else QPixmap.fromImage(speaker_data['image']).scaled(32, 32)
        )
        widget.layout().addWidget(speaker_icon)
        label = QLabel(speaker_name)
        widget.layout().addWidget(label)
        item = QListWidgetItem()
        self.left_panel_speakers_list.addItem(item)
        self.left_panel_speakers_list.setItemWidget(item, widget)
        item.setSizeHint(widget.sizeHint())

        if not speaker_data.get('image', None) and not self.left_panel_speakers_image_test_thread.isRunning():
            self.left_panel_speakers_image_test_thread.name = speaker_name
            self.left_panel_speakers_image_test_thread.start()


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


# def left_panel_speakers_list_item_selection_changed(self):
#     selected_items = self.left_panel_speakers_list.selectedItems()
#     if not selected_items:
#         self.left_panel_speakers_remove_speaker_button.setVisible(False)
#         return
#     can_remove = True
#     for item in selected_items:
#         speaker_name = item.text()
#         if any(subtitle.get('speaker', 'A') == speaker_name for subtitle in session.SUBTITLE['segments']):
#             can_remove = False
#             break
#     self.left_panel_speakers_image_test_thread.intervals = [[0.0, max(5.0, session.VIDEO.get('duration', 30.0))]]
#     self.left_panel_speakers_image_test_thread.name = selected_items[0].text()
    
#     self.left_panel_speakers_image_test_thread.start()

#     self.left_panel_speakers_remove_speaker_button.setVisible(can_remove)


def left_panel_speakers_add_speaker_button_clicked(self):
    new_name = None
    if self.left_panel_speakers_new_name_dialog.exec() == QDialog.Accepted:
        new_name = self.left_panel_speakers_new_name_dialog.name
    
    subtitles_names = [subtitle.get('speaker', 'A') for subtitle in session.SUBTITLE['segments']]

    # if new_name and new_name not in session.SPEAKERS and not new_name in subtitles_names:
    #     session.SPEAKERS.append(new_name)
    #     update_speakers_list(self)
    

def translate(self):
    self.left_panel_speakers_add_speaker_button.setText(_('Add Speaker'))





class NewNameDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Enter Your Name")
        self.name = None

        layout = QVBoxLayout(self)

        self.label = QLabel("Please enter your name:")
        self.input = QLineEdit()
        self.ok_button = QPushButton("OK")
        self.cancel_button = QPushButton("Cancel")

        layout.addWidget(self.label)
        layout.addWidget(self.input)
        layout.addWidget(self.ok_button)
        layout.addWidget(self.cancel_button)

        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)

    def accept(self):
        self.name = self.input.text().strip()
        super().accept()

    
