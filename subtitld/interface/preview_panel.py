import os

from PySide6.QtWidgets import QVBoxLayout, QWidget, QVBoxLayout, QGraphicsScene, QGraphicsView, QFrame, QGraphicsOpacityEffect, QSizePolicy
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, Qt, QRect, QMargins, Signal, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QBrush
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

from subtitld.interface import utils
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import subtitles
    

class PlayerWidget(QWidget):
    position_changed_signal = Signal()
    def __init__(widget, parent=None):
        super().__init__(parent)
        widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 0)
        
        widget._audio_output = QAudioOutput()
        widget._media_player = QMediaPlayer()
        widget._media_player.setAudioOutput(widget._audio_output)
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

                painter.setFont(QFont(session.CONFIG.get('videoplayer', {}).get('font_family', 'Ubuntu'), session.CONFIG.get('videoplayer', {}).get('font_size', 40)))

                title_safe_margin_qrect = widget.rect() - QMargins(
                    (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_x', 10) / 100) * widget.width(),
                    (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_y', 10) / 100) * widget.height(),
                    (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_x', 10) / 100) * widget.width(),
                    (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_y', 10) / 100) * widget.height()
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
                        painter.drawText(title_safe_margin_qrect - QMargins(session.CONFIG.get('videoplayer', {}).get('shadow_x', 2), session.CONFIG.get('videoplayer', {}).get('shadow_y', 2), -session.CONFIG.get('videoplayer', {}).get('shadow_x', 2), -session.CONFIG.get('videoplayer', {}).get('shadow_y', 2)), Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, session.SUBTITLE['current']['text'])

                    painter.setPen(QPen(session.CONFIG.get('videoplayer', {}).get('color', '#ffffffff')))
                    painter.drawText(title_safe_margin_qrect, Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, session.SUBTITLE['current']['text'])

                if session.CONFIG.get('videoplayer', {}).get('safe_margin_action_enabled', False):
                    action_safe_margin_qrect = widget.rect() - QMargins(
                        (session.CONFIG.get('videoplayer', {}).get('safe_margin_action_x', 5) / 100) * widget.width(),
                        (session.CONFIG.get('videoplayer', {}).get('safe_margin_action_y', 5) / 100) * widget.height(),
                        (session.CONFIG.get('videoplayer', {}).get('safe_margin_action_x', 5) / 100) * widget.width(),
                        (session.CONFIG.get('videoplayer', {}).get('safe_margin_action_y', 5) / 100) * widget.height()
                    )

                    painter.setPen(QPen(QColor(session.CONFIG.get('videoplayer', {}).get('safe_margin_action_color', '#dd67FF4D')), 1, Qt.SolidLine))
                    painter.drawRect(action_safe_margin_qrect)

                    painter.drawLine(
                        widget.width() * .5,
                        action_safe_margin_qrect.y(),
                        widget.width() * .5,
                        action_safe_margin_qrect.y() + (widget.height() * .025)
                    )
                    painter.drawLine(
                        widget.width() * .5,
                        action_safe_margin_qrect.y() + action_safe_margin_qrect.height(),
                        widget.width() * .5,
                        action_safe_margin_qrect.y() + action_safe_margin_qrect.height() - (widget.height() * .025)
                    )
                    painter.drawLine(
                        action_safe_margin_qrect.x(),
                        widget.height() * .5,
                        action_safe_margin_qrect.x() + (widget.width() * .025),
                        widget.height() * .5
                    )
                    painter.drawLine(
                        action_safe_margin_qrect.x() + action_safe_margin_qrect.width(),
                        widget.height() * .5,
                        action_safe_margin_qrect.x() + action_safe_margin_qrect.width() - (widget.width() * .025),
                        widget.height() * .5
                    )

                if session.CONFIG.get('videoplayer', {}).get('safe_margin_title_enabled', False):
                    painter.setPen(QPen(QColor(session.CONFIG.get('videoplayer', {}).get('safe_margin_title_color', '#ddff0000')), 1, Qt.SolidLine))
                    painter.drawRect(title_safe_margin_qrect)

        widget._graphics_video_item = graphicsvideoitem()
        widget._graphics_scene.addItem(widget._graphics_video_item)
        widget._media_player.setVideoOutput(widget._graphics_video_item)
        widget._media_player.positionChanged.connect(lambda position: widget.position_changed(position))
        
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
        """Function to load a media file"""
        if os.path.isfile(filepath):
            widget._media_player.setSource(str(filepath))
            widget.play()
            widget.pause()
            
    def frameStep(widget):
        None

    def frameBackStep(widget):
        None

    def seek(widget, pos=0.0, method='absolute+exact'):
        """Function to seek at some position"""
        widget._media_player.setPosition(int(pos*1000))
        # widget.audio_device.seek_audio(pos)
        
    def stop(widget):
        """Function to stop playback (fake stop, it is pause + position 0)"""
        widget._media_player.pause()
        widget.seek(0)
        # widget._media_player.stop()
        # widget.audio

    def pause(widget):
        """Function to pause playback (fake pause, it just changes actual playback status)"""
        widget._media_player.pause()
        # widget.audio_device.pause()

    def play(widget):
        """Function to play (fake play, it just changes actual playback status)"""
        widget._media_player.play()
        # widget.audio_device.play()
        # widget._sound_effect.play()

    def mute(widget):
        """Function to mute"""
        # widget._media_player.setMuted(True)
        None

    def is_paused(widget):
        return widget._media_player.playbackState() == QMediaPlayer.PausedState

    def volume(widget, vol: int):
        """Function to change volume"""
        # widget.property('volume', vol)
        None
    
    def set_position(widget, pos):
        widget.seek(pos)

    def get_position(widget):
        return widget._media_player.position()

    def update_speed(self):
        """Function to change playback speed"""
        self._media_player.setPlaybackRate(session.CONFIG['playback_speed'])
        # self.audio_device.change_speed(session.CONFIG['playback_speed'])

    def _force_resize_update(widget):
        size = widget.size()
        widget._graphics_view.setSceneRect(QRect(0, 0, size.width(), size.height()))
        widget._graphics_video_item.setSize(size)
        widget._graphics_view.fitInView(widget._graphics_view.sceneRect(), Qt.KeepAspectRatio)
        widget.updateGeometry()


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