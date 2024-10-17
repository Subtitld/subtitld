import os

from PySide6.QtWidgets import QWidget, QVBoxLayout, QGraphicsScene, QGraphicsView, QFrame
from PySide6.QtCore import Qt, QRect, QMargins, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QBrush
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

from subtitld.modules import session, subtitles
from subtitld.interface import playercontrols


class PlayerWidget(QWidget):
    position_changed_signal = Signal()
    def __init__(widget, parent=None):
        super().__init__(parent)
        widget.setLayout(QVBoxLayout())

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

                painter.setFont(QFont(session.CONFIG['videoplayer'].get('font_family', 'Ubuntu'), session.CONFIG['videoplayer'].get('font_size', 40)))

                title_safe_margin_qrect = widget.rect() - QMargins(
                    (session.CONFIG['videoplayer'].get('safe_margin_title_x', 10) / 100) * widget.width(),
                    (session.CONFIG['videoplayer'].get('safe_margin_title_y', 10) / 100) * widget.height(),
                    (session.CONFIG['videoplayer'].get('safe_margin_title_x', 10) / 100) * widget.width(),
                    (session.CONFIG['videoplayer'].get('safe_margin_title_y', 10) / 100) * widget.height()
                )

                if session.SUBTITLE.get('current', False):

                    if session.CONFIG['videoplayer'].get('backgroundbox_enabled', True):
                        text_rect = painter.boundingRect(title_safe_margin_qrect, Qt.AlignBottom | Qt.AlignHCenter | Qt.TextWordWrap, session.SUBTITLE['current']['text'])

                        painter.setPen(Qt.NoPen)
                        painter.setBrush(QBrush(QColor(session.CONFIG['videoplayer'].get('backgroundbox_color', '#55000000'))))

                        if session.CONFIG['videoplayer'].get('backgroundbox_border_radius', 5):
                            painter.drawRoundedRect(text_rect.marginsAdded(QMargins(session.CONFIG['videoplayer'].get('backgroundbox_padding', 10), session.CONFIG['videoplayer'].get('backgroundbox_padding', 10), session.CONFIG['videoplayer'].get('backgroundbox_padding', 10), session.CONFIG['videoplayer'].get('backgroundbox_padding', 10))), session.CONFIG['videoplayer'].get('backgroundbox_border_radius', 5), session.CONFIG['videoplayer'].get('backgroundbox_border_radius', 5))
                        else:
                            painter.drawRect(text_rect.marginsAdded(QMargins(session.CONFIG['videoplayer'].get('backgroundbox_padding', 10), session.CONFIG['videoplayer'].get('backgroundbox_padding', 10), session.CONFIG['videoplayer'].get('backgroundbox_padding', 10), session.CONFIG['videoplayer'].get('backgroundbox_padding', 10))))

                        painter.setBrush(Qt.NoBrush)

                    if session.CONFIG['videoplayer'].get('shadow_enabled', True):
                        painter.setPen(QPen(session.CONFIG['videoplayer'].get('shadow_color', '#ff000000')))
                        painter.drawText(title_safe_margin_qrect - QMargins(session.CONFIG['videoplayer'].get('shadow_x', 2), session.CONFIG['videoplayer'].get('shadow_y', 2), -session.CONFIG['videoplayer'].get('shadow_x', 2), -session.CONFIG['videoplayer'].get('shadow_y', 2)), Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, session.SUBTITLE['current']['text'])

                    painter.setPen(QPen(session.CONFIG['videoplayer'].get('color', '#ffffffff')))
                    painter.drawText(title_safe_margin_qrect, Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, session.SUBTITLE['current']['text'])

                if session.CONFIG['videoplayer'].get('safe_margin_action_enabled', False):
                    action_safe_margin_qrect = widget.rect() - QMargins(
                        (session.CONFIG['videoplayer'].get('safe_margin_action_x', 5) / 100) * widget.width(),
                        (session.CONFIG['videoplayer'].get('safe_margin_action_y', 5) / 100) * widget.height(),
                        (session.CONFIG['videoplayer'].get('safe_margin_action_x', 5) / 100) * widget.width(),
                        (session.CONFIG['videoplayer'].get('safe_margin_action_y', 5) / 100) * widget.height()
                    )

                    painter.setPen(QPen(QColor(session.CONFIG['videoplayer'].get('safe_margin_action_color', '#dd67FF4D')), 1, Qt.SolidLine))
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

                if session.CONFIG['videoplayer'].get('safe_margin_title_enabled', False):
                    painter.setPen(QPen(QColor(session.CONFIG['videoplayer'].get('safe_margin_title_color', '#ddff0000')), 1, Qt.SolidLine))
                    painter.drawRect(title_safe_margin_qrect)

        widget._graphics_video_item = graphicsvideoitem()
        widget._graphics_scene.addItem(widget._graphics_video_item)
        widget._media_player.setVideoOutput(widget._graphics_video_item)
        widget._media_player.positionChanged.connect(lambda position: widget.position_changed(position))
        
        widget.layout().addWidget(widget._graphics_view, 1)
    
    def update_subtitle_layer(widget):
        session.SUBTITLE['current'] = subtitles.subtitle_under_current_position()
        widget.update()

    def position_changed(widget, position):
        session.SUBTITLE['position'] = position / 1000.0
        widget.update_subtitle_layer()
        widget.position_changed_signal.emit()

    def resizeEvent(widget, event):
        widget._graphics_view.setSceneRect(QRect(0, 0, event.size().width(), event.size().height())) #widget._graphics_view.fitInView(event.size())
        widget._graphics_video_item.setSize(event.size())
        widget._graphics_view.fitInView(widget._graphics_view.sceneRect(), Qt.KeepAspectRatio)
        event.accept()

    def loadfile(widget, filepath) -> None:
        """Function to load a media file"""
        if os.path.isfile(filepath):
            widget._media_player.setSource(filepath)
            widget.play()
            widget.pause()
            
    def frameStep(widget):
        None

    def frameBackStep(widget):
        None

    def seek(widget, pos=0.0, method='absolute+exact') -> None:
        """Function to seek at some position"""
        widget._media_player.setPosition(int(pos*1000))
        

    def stop(widget) -> None:
        """Function to stop playback (fake stop, it is pause + position 0)"""
        widget._media_player.stop()

    def pause(widget) -> None:
        """Function to pause playback (fake pause, it just changes actual playback status)"""
        widget._media_player.pause()

    def play(widget) -> None:
        """Function to play (fake play, it just changes actual playback status)"""
        widget._media_player.play()

    def mute(widget) -> None:
        """Function to mute"""
        # widget._media_player.setMuted(True)
        None

    def is_paused(widget):
        return widget._media_player.playbackState() == QMediaPlayer.PausedState

    def volume(widget, vol: int) -> None:
        """Function to change volume"""
        # widget.property('volume', vol)
        None
    
    def set_position(widget, pos):
        widget.seek(pos)

    def get_position(widget):
        return widget._media_player.position()


def load(self):
    """Function to load player widgets"""

    self.player_widget = PlayerWidget()


def update_speed(self):
    """Function to change playback speed"""
    None




def resize_player_widget(self, just_get_qrect=False):
    """Function to resize player widget (to accomodate video ratio inside screen space)"""
    # None
    

    # if session.VIDEO.get('width', 1920) > session.VIDEO.get('height', 1080):
    #     wp = 1
    #     hp = session.VIDEO.get('height', 1080) / session.VIDEO.get('width', 1920)
    # else:
    #     wp = session.VIDEO.get('width', 1920) / session.VIDEO.get('height', 1080)
    #     hp = 1

    # session.VIDEO.get('width', 1920) / session.VIDEO.get('height', 1080)
    # if session.VIDEO.get('width', 640) > session.VIDEO.get('height', 480):
    #    heigth_proportion = ((self.player_widget_area.width()*.7)-6) / session.VIDEO.get('width', 640)
    #    self.player_widget.setGeometry((self.width()*.2) + 3, (self.player_widget_area.height()*.5)-((heigth_proportion*session.VIDEO.get('height', 480))*.5), (self.player_widget_area.width()*.7)-6, session.VIDEO.get('height', 480)*heigth_proportion)
    # else:
    #    width_proportion = (self.player_widget_area.height()-7) / session.VIDEO.get('height', 480)
    #    self.player_widget.setGeometry((self.width()*.2) + ((self.player_widget_area.width()*.7)*.5)-((width_proportion*session.VIDEO.get('width', 640))*.5), 3, session.VIDEO.get('width', 640)*width_proportion, self.player_widget_area.height()-6)
    # self.player_border.setGeometry(self.player_widget.x()-3, self.player_widget.y()-3, self.player_widget.width()+6, self.player_widget.height()+6)
    # self.player_subtitle_layer.setGeometry(self.player_widget.x(), self.player_widget.y(), self.player_widget.width(), self.player_widget.height())
    # self.player_subtitle_textedit.setGeometry(self.player_widget.x()+(self.player_widget.width()*.1), self.player_widget.y()+(self.player_widget.height()*.5), self.player_widget.width()*.8, self.player_widget.height()*.4)


def update_controls(self):
    playercontrols.playercontrols_stop_button_clicked(self)


def update_timelines(self):
    timeline.update(self)
    # if not self.subtitles_panel_findandreplace_panel.isVisible():
    #     subtitles_panel.update_subtitles_panel_widget_vision_content(self)
