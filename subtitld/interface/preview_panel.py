import os

from PySide6.QtWidgets import QVBoxLayout, QWidget, QVBoxLayout, QGraphicsScene, QGraphicsView, QFrame, QGraphicsOpacityEffect, QSizePolicy, QRubberBand
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, Qt, QRect, QRectF, QPoint, QSize, QMargins, QMarginsF, Signal, QTimer, QEvent
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QBrush
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QMediaMetaData
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

from subtitld.interface import utils
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import subtitles
from subtitld.modules import audioengine #AudaspaceAudioDevice
    

class PlayerWidget(QWidget):
    position_changed_signal = Signal()
    def __init__(widget, parent=None):
        super().__init__(parent)
        widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 0)

        widget._media_player = QMediaPlayer()
        
        widget._audio_output = QAudioOutput()
        widget._media_player.setAudioOutput(widget._audio_output)
        # widget._media_player.setAudioOutput(QAudioOutput())
        
        widget._audio_device = audioengine.SoundDeviceAudioEngine()

        widget._graphics_scene = QGraphicsScene()
        widget._graphics_view = QGraphicsView(widget._graphics_scene)
        widget._graphics_view.setFrameShadow(QFrame.Plain)
        widget._graphics_view.setFrameShape(QFrame.NoFrame)
        widget._graphics_view.setRenderHints(QPainter.Antialiasing)
        widget._graphics_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        widget._graphics_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
       
        class graphicsvideoitem(QGraphicsVideoItem):
            def __init__(self):
                super().__init__()
            
            def paint(self, painter, option, widget):
                super().paint(painter, option, widget)

                painter.setRenderHint(QPainter.Antialiasing)

                # Actual video content rect inside the item (letterboxed).
                item_rect = self.boundingRect()
                native = self.nativeSize()
                if native is not None and native.width() > 0 and native.height() > 0:
                    scale = min(item_rect.width() / native.width(), item_rect.height() / native.height())
                    vw = native.width() * scale
                    vh = native.height() * scale
                    vx = item_rect.x() + (item_rect.width() - vw) / 2.0
                    vy = item_rect.y() + (item_rect.height() - vh) / 2.0
                    video_rect = QRectF(vx, vy, vw, vh)
                else:
                    video_rect = QRectF(item_rect)

                painter.setFont(QFont(session.CONFIG.get('videoplayer', {}).get('font_family', 'Ubuntu'), session.CONFIG.get('videoplayer', {}).get('font_size', 40)))

                title_safe_margin_qrect = video_rect - QMarginsF(
                    (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_x', 10) / 100) * video_rect.width(),
                    (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_y', 10) / 100) * video_rect.height(),
                    (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_x', 10) / 100) * video_rect.width(),
                    (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_y', 10) / 100) * video_rect.height()
                )

                if session.SUBTITLE.get('current', False):

                    if session.CONFIG.get('videoplayer', {}).get('backgroundbox_enabled', True):
                        text_rect = painter.boundingRect(title_safe_margin_qrect, Qt.AlignBottom | Qt.AlignHCenter | Qt.TextWordWrap, session.SUBTITLE['current']['text'])

                        painter.setPen(Qt.NoPen)
                        painter.setBrush(QBrush(QColor(session.CONFIG.get('videoplayer', {}).get('backgroundbox_color', '#55000000'))))

                        if session.CONFIG.get('videoplayer', {}).get('backgroundbox_border_radius', 5):
                            painter.drawRoundedRect(text_rect.marginsAdded(QMargins(session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10), session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10), session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10), session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10))), session.CONFIG.get('videoplayer', {}).get('backgroundbox_border_radius', 5), session.CONFIG.get('videoplayer', {}).get('backgroundbox_border_radius', 5))
                        else:
                            painter.drawRect(text_rect.marginsAdded(QMargins(session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10), session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10), session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10), session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10))))

                        painter.setBrush(Qt.NoBrush)

                    if session.CONFIG.get('videoplayer', {}).get('shadow_enabled', True):
                        painter.setPen(QPen(session.CONFIG.get('videoplayer', {}).get('shadow_color', '#ff000000')))
                        painter.drawText(title_safe_margin_qrect - QMarginsF(session.CONFIG.get('videoplayer', {}).get('shadow_x', 2), session.CONFIG.get('videoplayer', {}).get('shadow_y', 2), -session.CONFIG.get('videoplayer', {}).get('shadow_x', 2), -session.CONFIG.get('videoplayer', {}).get('shadow_y', 2)), Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, session.SUBTITLE['current']['text'])

                    painter.setPen(QPen(session.CONFIG.get('videoplayer', {}).get('color', '#ffffffff')))
                    painter.drawText(title_safe_margin_qrect, Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, session.SUBTITLE['current']['text'])

                if session.CONFIG.get('videoplayer', {}).get('safe_margin_action_enabled', False):
                    action_safe_margin_qrect = video_rect - QMarginsF(
                        (session.CONFIG.get('videoplayer', {}).get('safe_margin_action_x', 5) / 100) * video_rect.width(),
                        (session.CONFIG.get('videoplayer', {}).get('safe_margin_action_y', 5) / 100) * video_rect.height(),
                        (session.CONFIG.get('videoplayer', {}).get('safe_margin_action_x', 5) / 100) * video_rect.width(),
                        (session.CONFIG.get('videoplayer', {}).get('safe_margin_action_y', 5) / 100) * video_rect.height()
                    )

                    painter.setPen(QPen(QColor(session.CONFIG.get('videoplayer', {}).get('safe_margin_action_color', '#dd67FF4D')), 1, Qt.SolidLine))
                    painter.drawRect(action_safe_margin_qrect)

                    center_x = video_rect.center().x()
                    center_y = video_rect.center().y()

                    painter.drawLine(
                        center_x,
                        action_safe_margin_qrect.y(),
                        center_x,
                        action_safe_margin_qrect.y() + (video_rect.height() * .025)
                    )
                    painter.drawLine(
                        center_x,
                        action_safe_margin_qrect.y() + action_safe_margin_qrect.height(),
                        center_x,
                        action_safe_margin_qrect.y() + action_safe_margin_qrect.height() - (video_rect.height() * .025)
                    )
                    painter.drawLine(
                        action_safe_margin_qrect.x(),
                        center_y,
                        action_safe_margin_qrect.x() + (video_rect.width() * .025),
                        center_y
                    )
                    painter.drawLine(
                        action_safe_margin_qrect.x() + action_safe_margin_qrect.width(),
                        center_y,
                        action_safe_margin_qrect.x() + action_safe_margin_qrect.width() - (video_rect.width() * .025),
                        center_y
                    )

                if session.CONFIG.get('videoplayer', {}).get('safe_margin_title_enabled', False):
                    painter.setPen(QPen(QColor(session.CONFIG.get('videoplayer', {}).get('safe_margin_title_color', '#ddff0000')), 1, Qt.SolidLine))
                    painter.drawRect(title_safe_margin_qrect)

        widget._graphics_video_item = graphicsvideoitem()
        widget._graphics_scene.addItem(widget._graphics_video_item)
        widget._media_player.setVideoOutput(widget._graphics_video_item)
        widget._media_player.positionChanged.connect(lambda position: widget.position_changed(position))

        widget._last_video_frame = None
        widget._graphics_video_item.videoSink().videoFrameChanged.connect(widget._on_video_frame)
        
        widget.layout().addWidget(widget._graphics_view, 1)

        QTimer.singleShot(0, widget._force_resize_update)
    
    def position_changed(widget, position):
        session.SUBTITLE['position'] = position / 1000.0
        widget.update_subtitle_layer()
        widget.position_changed_signal.emit()

    def update_subtitle_layer(widget):
        session.SUBTITLE['current'] = subtitles.subtitle_under_current_position()
        widget.update()

    def resizeEvent(widget, event):
        widget._graphics_view.setSceneRect(QRect(0, 0, event.size().width(), event.size().height()))
        widget._graphics_video_item.setSize(event.size())
        widget._graphics_view.fitInView(widget._graphics_view.sceneRect(), Qt.KeepAspectRatio)
        event.accept()

    def loadfile(widget, filepath):
        if os.path.isfile(filepath):
            widget._media_player.setSource(str(filepath))
            # widget._audio_device.load(filepath)
            widget.play()
            widget.pause()
            
    def frameStep(widget):
        fps = widget._media_player.metaData().value(QMediaMetaData.VideoFrameRate)
        widget._media_player.setPosition(widget._media_player.position() + int(1000/fps))

    def frameBackStep(widget):
        fps = widget._media_player.metaData().value(QMediaMetaData.VideoFrameRate)
        widget._media_player.setPosition(widget._media_player.position() - int(1000/fps))

    def seek(widget, pos=0.0, method='absolute+exact'):
        """Function to seek at some position"""
        widget._media_player.setPosition(int(pos*1000))
        widget._audio_device.seek(pos)
        
    def stop(widget):
        """Function to stop playback (fake stop, it is pause + position 0)"""
        widget._media_player.pause()
        widget._audio_device.stop()
        widget.seek(0)

    def pause(widget):
        """Function to pause playback (fake pause, it just changes actual playback status)"""
        widget._media_player.pause()
        widget._audio_device.pause()

    def play(widget):
        """Function to play (fake play, it just changes actual playback status)"""
        widget._media_player.play()
        widget._audio_device.play(session.SUBTITLE.get('position', 0))

    def mute(widget):
        """Function to mute"""
        None

    def is_paused(widget):
        return widget._media_player.playbackState() == QMediaPlayer.PausedState

    def volume(widget, vol: int):
        """Function to change volume"""
        None
    
    def set_position(widget, pos):
        widget.seek(pos)

    def get_position(widget):
        return widget._media_player.position()

    def update_speed(self):
        """Function to change playback speed"""
        self._media_player.setPlaybackRate(session.CONFIG['playback_speed'])
        self._audio_device.set_speed(session.CONFIG['playback_speed'])
        # self._audio_device.change_speed(session.CONFIG['playback_speed'])

    def _force_resize_update(widget):
        size = widget.size()
        widget._graphics_view.setSceneRect(QRect(0, 0, size.width(), size.height()))
        widget._graphics_video_item.setSize(size)
        widget._graphics_view.fitInView(widget._graphics_view.sceneRect(), Qt.KeepAspectRatio)
        widget.updateGeometry()

    def start_face_selection(widget, speaker_name):
        """Enter face-selection mode. User drags a 1:1 square on the video;
        on mouse release, the selected region becomes the speaker image.
        Right-click or Esc cancels."""
        widget._face_selection_speaker = speaker_name
        widget._face_selection_origin = None
        if getattr(widget, '_face_rubber_band', None) is None:
            widget._face_rubber_band = QRubberBand(QRubberBand.Rectangle, widget._graphics_view.viewport())
        viewport = widget._graphics_view.viewport()
        widget._face_selection_prev_focus_policy = viewport.focusPolicy()
        viewport.setFocusPolicy(Qt.StrongFocus)
        viewport.setFocus(Qt.OtherFocusReason)
        viewport.setCursor(Qt.CrossCursor)
        viewport.installEventFilter(widget)

    def _end_face_selection(widget):
        viewport = widget._graphics_view.viewport()
        viewport.removeEventFilter(widget)
        viewport.unsetCursor()
        prev_policy = getattr(widget, '_face_selection_prev_focus_policy', None)
        if prev_policy is not None:
            viewport.setFocusPolicy(prev_policy)
            widget._face_selection_prev_focus_policy = None
        if getattr(widget, '_face_rubber_band', None) is not None:
            widget._face_rubber_band.hide()
        widget._face_selection_speaker = None
        widget._face_selection_origin = None

    def _on_video_frame(widget, frame):
        if frame.isValid():
            widget._last_video_frame = frame

    def _video_rect_in_viewport(widget):
        frame = widget._last_video_frame
        if frame is None or not frame.isValid():
            return None
        size = frame.size()
        fw, fh = size.width(), size.height()
        if fw <= 0 or fh <= 0:
            return None
        vp = widget._graphics_view.viewport()
        vw, vh = vp.width(), vp.height()
        scale = min(vw / fw, vh / fh)
        video_w = fw * scale
        video_h = fh * scale
        video_x = (vw - video_w) / 2.0
        video_y = (vh - video_h) / 2.0
        return QRectF(video_x, video_y, video_w, video_h)

    @staticmethod
    def _clamp_point(pt, rect):
        return QPoint(
            int(max(rect.left(), min(rect.right(), pt.x()))),
            int(max(rect.top(), min(rect.bottom(), pt.y()))),
        )

    def _square_rect(widget, origin, current):
        video_rect = widget._video_rect_in_viewport()
        if video_rect is not None:
            origin = widget._clamp_point(origin, video_rect)
            current = widget._clamp_point(current, video_rect)
        dx = current.x() - origin.x()
        dy = current.y() - origin.y()
        sx = -1 if dx < 0 else 1
        sy = -1 if dy < 0 else 1
        size = max(abs(dx), abs(dy))
        if video_rect is not None:
            max_x = video_rect.right() - origin.x() if sx > 0 else origin.x() - video_rect.left()
            max_y = video_rect.bottom() - origin.y() if sy > 0 else origin.y() - video_rect.top()
            size = int(min(size, max_x, max_y))
        size = max(0, size)
        end = QPoint(origin.x() + sx * size, origin.y() + sy * size)
        top_left = QPoint(min(origin.x(), end.x()), min(origin.y(), end.y()))
        return QRect(top_left, QSize(size, size))

    def eventFilter(widget, obj, event):
        if not getattr(widget, '_face_selection_speaker', None):
            return super().eventFilter(obj, event)
        if obj is not widget._graphics_view.viewport():
            return super().eventFilter(obj, event)

        etype = event.type()
        if etype == QEvent.MouseButtonPress and event.button() == Qt.RightButton:
            widget._end_face_selection()
            return True
        if etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            widget._face_selection_origin = event.pos()
            widget._face_rubber_band.setGeometry(QRect(event.pos(), QSize()))
            widget._face_rubber_band.show()
            return True
        if etype == QEvent.MouseMove and widget._face_selection_origin is not None:
            widget._face_rubber_band.setGeometry(widget._square_rect(widget._face_selection_origin, event.pos()))
            return True
        if etype == QEvent.MouseButtonRelease and widget._face_selection_origin is not None:
            rect = widget._square_rect(widget._face_selection_origin, event.pos())
            widget._apply_face_selection(rect)
            widget._end_face_selection()
            return True
        if etype == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            widget._end_face_selection()
            return True
        return super().eventFilter(obj, event)

    def _apply_face_selection(widget, rect):
        frame = widget._last_video_frame
        if frame is None or not frame.isValid():
            return
        video_rect = widget._video_rect_in_viewport()
        if video_rect is None:
            return

        clipped = rect.intersected(video_rect.toRect())
        if clipped.width() < 4 or clipped.height() < 4:
            return

        frame_size = frame.size()
        fw, fh = frame_size.width(), frame_size.height()
        scale_x = fw / video_rect.width()
        scale_y = fh / video_rect.height()
        fx = int(round((clipped.x() - video_rect.x()) * scale_x))
        fy = int(round((clipped.y() - video_rect.y()) * scale_y))
        fw_sel = int(round(clipped.width() * scale_x))
        fh_sel = int(round(clipped.height() * scale_y))
        fx = max(0, min(fx, fw - 1))
        fy = max(0, min(fy, fh - 1))
        fw_sel = max(1, min(fw_sel, fw - fx))
        fh_sel = max(1, min(fh_sel, fh - fy))

        frame_image = frame.toImage()
        cropped = frame_image.copy(QRect(fx, fy, fw_sel, fh_sel))
        if cropped.isNull():
            return

        speaker_name = widget._face_selection_speaker
        from subtitld.modules import session as _session
        _session.SPEAKERS.setdefault(speaker_name, {})['image'] = cropped
        _session.set_unsaved()
        window = widget.window()
        from subtitld.interface import left_panel_speakers
        left_panel_speakers.update_speakers_list(window)
        widget.update()


def load(self):
    self.preview_panel_container = QWidget()
    self.preview_panel_container.setLayout(QVBoxLayout())
    self.preview_panel_container.layout().setContentsMargins(0, 0, 0, 0)
    self.preview_panel = QWidget(self.preview_panel_container)
    self.preview_panel.setLayout(QVBoxLayout())
    self.preview_panel.setObjectName('preview_panel')
    self.preview_panel.layout().setContentsMargins(0, 0, 0, 0)
    self.preview_panel.opacity = QGraphicsOpacityEffect()
    self.preview_panel.opacity.setOpacity(0)
    self.preview_panel.setGraphicsEffect(self.preview_panel.opacity)
    self.preview_panel.animation = QPropertyAnimation(self.preview_panel, b'pos')
    self.preview_panel.animation.setEasingCurve(QEasingCurve.OutCubic)

    self.preview_panel_player = PlayerWidget()
    self.preview_panel_player.setObjectName('preview_panel_player')
    self.preview_panel.layout().addWidget(self.preview_panel_player)

    self.preview_panel_container.layout().addWidget(self.preview_panel)

    self.main_horizontal_splitter.addWidget(self.preview_panel_container)
    
        
def show(self):
    self.preview_panel_player._force_resize_update()
    QTimer().singleShot(100, lambda: self.preview_panel.opacity.setOpacity(1.0))
    utils.animate_element(self.preview_panel.animation, duration=1000, effect='slide_from_right')


def hide(self):
    utils.animate_element(self.preview_panel.animation, duration=200, effect='slide_to_right')


def translate(self):
    pass
