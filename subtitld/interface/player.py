import os
import mlt7 as mlt
import datetime

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QGraphicsOpacityEffect
from PySide6.QtCore import QThread, Qt, QRect, QPropertyAnimation, QEasingCurve, QMargins
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QBrush
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from subtitld.modules import globals
from subtitld.interface import playercontrols, subtitles_panel

from PIL import Image, ImageDraw, ImageFont


# Function to create an image subtitle
def create_subtitle_image(text, filename, width=640, height=480):
    # Create a blank image with transparent background
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Load a font (adjust path if needed)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 40)
    except IOError:
        # Fallback to a default font if not found
        font = ImageFont.load_default()

    # Calculate text size and position using textbbox (newer versions of PIL)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]  # Calculate the width from the bounding box
    text_height = bbox[3] - bbox[1]  # Calculate the height from the bounding box

    # Center the text horizontally and position it near the bottom
    x = (width - text_width) // 2
    y = height - 200  # Place the text at the bottom of the screen

    # Draw the text on the image
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))

    # Save the image
    img.save(filename)

class fill_mlt_playlist_thread(QThread):
    subtitles = []
    playlist_sub = None
    profile = None
    subtitles_mlt_objects_dict = {}
    style = {}

    def run(self):
        if self.playlist_sub is not None:
            self.playlist_sub.clear()
            
            last_time_parse = 0
            bi = 0
            for i, subtitle in enumerate(globals.SESSION['segments']):
                if not last_time_parse == subtitle['start']:
                    self.playlist_sub.insert_blank(bi + i, int((subtitle['start'] - last_time_parse) * self.profile.fps()) - 1)
                    bi += 1
                
                image_producer = mlt.Producer(self.profile, 'color')
                image_producer.set('resource', '#00000000')
                image_producer.set('mlt_image_format', "rgba")


                image_producer_filter = mlt.Filter(self.profile, 'text')

                image_producer_filter.set('argument', subtitle['text'])
                image_producer_filter.set('family', self.style.get('font_family', 'Ubuntu'))
                image_producer_filter.set('size', self.style.get('font_size', 40))
                image_producer_filter.set('style', 'normal')
                image_producer_filter.set('fgcolour', self.style.get('color', '#ffffffff'))
                image_producer_filter.set('bgcolour', self.style.get('backgroundbox_color', '#55000000'))
                image_producer_filter.set('olcolour', '#aa000000')
                image_producer_filter.set('halign', 'center')
                image_producer_filter.set('valign', 'bottom')
                image_producer_filter.set('pad', self.style.get('backgroundbox_padding', 10))
                image_producer_filter.set('outline', '3')
                image_producer_filter.set('opacity', '1.0')

                image_producer.attach(image_producer_filter)
                
                image_producer.set('out', int((subtitle['end'] - subtitle['start']) * self.profile.fps()))

                self.playlist_sub.append(image_producer)
                last_time_parse = subtitle['end']


class MltWidget(QOpenGLWidget):
    """Main MPV widget class"""
    def __init__(widget, parent=None):
        super().__init__(parent)
        mlt.Factory().init()
        widget.profile = mlt.Profile('cif_15') #hdv_720_30p
        widget.producer = None
        widget.consumer = mlt.Consumer(widget.profile, 'sdl2')
        widget.consumer.set('window_id', widget.winId())

        widget.consumer.set("real_time", 1)
        widget.consumer.set("rescale", "bilinear") # MLT options "nearest", "bilinear", "bicubic", "hyper"
        # widget.consumer.set("resize", 0)
        # widget.consumer.set("display_ratio", "1.0")
        # widget.consumer.set("progressive", 1)
        # widget.consumer.set('preview_enabled', 1)
        
        # widget.consumer.set('preview_format', )
        widget.playlist = mlt.Playlist(widget.profile)
        widget.playlist_sub = mlt.Playlist(widget.profile)

        widget.tractor = mlt.Tractor()
        multitrack = widget.tractor.multitrack()

        multitrack.connect(widget.playlist, 0)
        multitrack.connect(widget.playlist_sub, 1)

        widget.transition = mlt.Transition(widget.profile, "composite")
        widget.transition.set("0", "1.0")  # Blend amount (between 0.0 and 1.0)
        widget.transition.set("1", "normal")  # Blend amount (between 0.0 and 1.0)
        widget.transition.set('in', 0)
        widget.transition.set("a_track", 0)  # Blend video track
        widget.transition.set("b_track", 1)  # With subtitle track

        def fill_mlt_playlist_thread_finished():
            widget.fill_mlt_playlist_thread.style = widget.window().player_subtitle_layer.style

        widget.fill_mlt_playlist_thread = fill_mlt_playlist_thread(parent=widget)
        widget.fill_mlt_playlist_thread.setTerminationEnabled(True)
        widget.fill_mlt_playlist_thread.finished.connect(fill_mlt_playlist_thread_finished)
        # widget.fill_mlt_playlist_thread.subtitles_mlt_objects_dict = widget.subtitles_mlt_objects_dict
        # widget.fill_mlt_playlist_thread.finished.connect(fill_mlt_playlist_thread_finished)
        # widget.transition.set("length", 500)  # Transition length (adjust based on your preference)


        # widget.transition.set("length", 25)  # Duration of the transition (adjust as needed)
        # widget.transition.set("start", 0)  # Start time of the transition
        # widget.transition.set("in", 0)
        # widget.transition.set("0", 1)  # Blend amount (between 0.0 and 1.0)
        # widget.transition.set("1", "normal")  # Blend amount (between 0.0 and 1.0)
        
        # field = mlt.Field()
        
        # widget.tractor.insert_track(field, 2)

        # widget.tractor.plant_transition(widget.tractor.field(), widget.transition)
                
        widget.tractor.plant_transition(widget.transition, 0, 1)
        
        widget.consumer.connect(widget.tractor)

        widget.consumer_xml = mlt.Consumer(widget.profile, 'xml')
        widget.consumer_xml.set('resource', '/tmp/test.mlt')
        widget.consumer_xml.connect(widget.tractor)

        widget.playback_speed = 1.0

        widget.is_paused = True
        widget.filepath = False
        widget.duration_in_frames = 60*30
        widget.duration = 60
        
    def fix_size(widget):
        """Function to resize player widget (to accomodate video ratio inside screen space)"""
        None
        # widget.resize(widget.size())
        # widget.consumer.set('window_width', widget.width())
        # widget.consumer.set('window_height', widget.height())
        # widget.consumer.set('window_y', 500)
        # widget.consumer.set('rect_x', 0)
        # widget.consumer.set('rect_y', 0)

    def loadfile(widget, filepath) -> None:
        """Function to load a media file"""
        if os.path.isfile(filepath):
            widget.filepath = filepath
            widget.producer = mlt.Producer(widget.profile, widget.filepath)
            widget.profile.from_producer(widget.producer)
            widget.producer = mlt.Producer(widget.profile, widget.filepath)

            widget.playlist.append(widget.producer)

            # blank = widget.playlist_sub.blank(0)
            # widget.playlist_sub.insert_blank(0, int((globals.SESSION['segments'][0]['start'] - 0.001) * widget.profile.fps()))
            # widget.playlist_sub.append(blank)

            widget.fill_mlt_playlist_thread.playlist_sub = widget.playlist_sub
            widget.fill_mlt_playlist_thread.profile = widget.profile
            widget.fill_mlt_playlist_thread.subtitles = globals.SESSION['segments']
            widget.fill_mlt_playlist_thread.start()
            
            widget.transition.set('out', widget.tractor.get_length())

            widget.consumer.start()
            widget.pause()

            widget.consumer_xml.start()


    def frameStep(widget) -> None:
        """Function to move forward one step (frame)"""
        None

    def resizeEvent(widget, event):
        widget.consumer.set('window_width', widget.width())
        widget.consumer.set('window_height', widget.height())
        event.accept()

    def frameBackStep(widget) -> None:
        """Function to move backward one step (frame)"""
        None

    def seek(widget, pos=0.0, method='absolute') -> None:
        """Function to seek at some position"""
        if method == 'absolute':
            widget.tractor.seek(int(pos * widget.profile.fps()))
        elif method == 'relative':
            widget.tractor.seek(int((pos * widget.duration) * widget.profile.fps()))
        

    def stop(widget) -> None:
        """Function to stop playback (fake stop, it is pause + position 0)"""
        if not widget.consumer.is_stopped():
            widget.consumer.stop()

    def playpause(widget):
        if widget.is_paused:
            widget.tractor.set_speed(widget.playback_speed)
            widget.consumer.set('volume', 1)
            # widget.tractor.play()
        else:
            widget.consumer.set('volume', 0)
            widget.tractor.pause()
            # widget.tractor.seek(widget.tractor.position())
            # widget.consumer.purge()
            # widget.consumer.start()
        widget.is_paused = not widget.is_paused

    def pause(widget) -> None:
        """Function to pause playback (fake pause, it just changes actual playback status)"""
        widget.consumer.set('volume', 0)
        widget.tractor.pause()

    def play(widget) -> None:
        """Function to play (fake play, it just changes actual playback status)"""
        widget.tractor.set_speed(widget.playback_speed)

    def mute(widget) -> None:
        """Function to mute"""
        None

    def volume(widget, vol: int) -> None:
        """Function to change volume"""
        None

    def position(widget):
        # print(widget.consumer.frames_to_time(widget.tractor.position()))
        # print(widget.tractor.frames_to_time(widget.tractor.position()))
        return widget.tractor.position() / widget.profile.fps()

    def set_position(widget, position):
        widget.tractor.seek(position * widget.profile.fps())

    def closeEvent(widget, event):
        """Function to call when player widget is closed"""
        if widget.consumer:
            widget.consumer.stop()
        event.accept()

class PlayerSubtitleLayer(QLabel):
    """Lass of subtitle layer"""
    def __init__(widget, parent=None):
        super().__init__(parent)
        widget.subtitle_text = ''
        widget.action_safe_margin = .9
        widget.title_safe_margin = .8
        widget.style = {}

    def paintEvent(widget, event):
        """Function to paint subtitle layer"""
        painter = QPainter(widget)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.setFont(QFont(widget.style.get('font_family', 'Ubuntu'), widget.style.get('font_size', 40)))

        title_safe_margin_qrect = widget.rect() - QMargins(
            (widget.style.get('safe_margin_title_x', 10) / 100) * widget.width(),
            (widget.style.get('safe_margin_title_y', 10) / 100) * widget.height(),
            (widget.style.get('safe_margin_title_x', 10) / 100) * widget.width(),
            (widget.style.get('safe_margin_title_y', 10) / 100) * widget.height()
        )

        # QRect(
        #     widget.width() * (widget.style.get('safe_margin_title_x', 10) / 100.0),
        #     widget.height() * (widget.style.get('safe_margin_title_y', 10) / 100.0),
        #     widget.width() * (1.0 - ((widget.style.get('safe_margin_title_x', 10) * 2) / 100)),
        #     widget.height() * (1.0 - ((widget.style.get('safe_margin_title_y', 10) * 2) / 100))
        # )

        if widget.subtitle_text:

            if widget.style.get('backgroundbox_enabled', True):
                text_rect = painter.boundingRect(title_safe_margin_qrect, Qt.AlignBottom | Qt.AlignHCenter | Qt.TextWordWrap, widget.subtitle_text)

                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(QColor(widget.style.get('backgroundbox_color', '#55000000'))))

                if widget.style.get('backgroundbox_border_radius', 5):
                    painter.drawRoundedRect(text_rect.marginsAdded(QMargins(widget.style.get('backgroundbox_padding', 10), widget.style.get('backgroundbox_padding', 10), widget.style.get('backgroundbox_padding', 10), widget.style.get('backgroundbox_padding', 10))), widget.style.get('backgroundbox_border_radius', 5), widget.style.get('backgroundbox_border_radius', 5))
                else:
                    painter.drawRect(text_rect.marginsAdded(QMargins(widget.style.get('backgroundbox_padding', 10), widget.style.get('backgroundbox_padding', 10), widget.style.get('backgroundbox_padding', 10), widget.style.get('backgroundbox_padding', 10))))

                painter.setBrush(Qt.NoBrush)

            if widget.style.get('shadow_enabled', True):
                painter.setPen(QPen(widget.style.get('shadow_color', '#ff000000')))
                painter.drawText(title_safe_margin_qrect - QMargins(widget.style.get('shadow_x', 2), widget.style.get('shadow_y', 2), -widget.style.get('shadow_x', 2), -widget.style.get('shadow_y', 2)), Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, widget.subtitle_text)

            painter.setPen(QPen(widget.style.get('color', '#ffffffff')))
            painter.drawText(title_safe_margin_qrect, Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, widget.subtitle_text)

        if widget.style.get('safe_margin_action_enabled', False):
            action_safe_margin_qrect = widget.rect() - QMargins(
                (widget.style.get('safe_margin_action_x', 5) / 100) * widget.width(),
                (widget.style.get('safe_margin_action_y', 5) / 100) * widget.height(),
                (widget.style.get('safe_margin_action_x', 5) / 100) * widget.width(),
                (widget.style.get('safe_margin_action_y', 5) / 100) * widget.height()
            )

            painter.setPen(QPen(QColor(widget.style.get('safe_margin_action_color', '#dd67FF4D')), 1, Qt.SolidLine))
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

        if widget.style.get('safe_margin_title_enabled', False):
            painter.setPen(QPen(QColor(widget.style.get('safe_margin_title_color', '#ddff0000')), 1, Qt.SolidLine))
            painter.drawRect(title_safe_margin_qrect)

        painter.end()
        event.accept()

    def setSubtitleText(widget, text):
        """Function to change subtitle layer text"""
        widget.subtitle_text = text

    def update_style(widget, style):
        widget.style = style
        widget.update()


class PlayerWidgetArea(QWidget):
    """Class to reimplement enter and leave events on subtitle layer"""
    def enterEvent(self, event):
        """Function to call when cursor enter player widget area"""
        event.accept()

    def leaveEvent(self, event):
        """Function to call when cursor leave player widget area"""
        event.accept()


def load(self):
    """Function to load player widgets"""

    # self.layer_player = QWidget(self)
    # self.layer_player.setLayout(QVBoxLayout())
    # self.layer_player.setContentsMargins(self.subtitles_panel_width_proportion * self.width(), 20, 20, 200)
    # self.layer_player.layout().setSpacing(0)

    # self.layer_player_left_spacer = QWidget()
    # self.layer_player_left_spacer.setMaximumWidth(0)
    # # sizePolicy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    # # self.layer_player_left_spacer.setSizePolicy(sizePolicy)
    # self.layer_player_left_spacer_animation = QPropertyAnimation(self.layer_player_left_spacer, b'maximumWidth')
    # self.layer_player_left_spacer_animation.setEasingCurve(QEasingCurve.OutCirc)
    # self.layer_player_hbox.addWidget(self.layer_player_left_spacer)

    # self.layer_player_vbox = QVBoxLayout()
    # self.layer_player_vbox.setContentsMargins(0, 0, 0, 0)

    # self.videoinfo_label = QLabel()  # parent=self.layer_player
    # # self.videoinfo_label.setObjectName('videoinfo_label')
    # # self.videoinfo_label.setFixedHeight(20)
    # # sizePolicy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    # # self.videoinfo_label.setSizePolicy(sizePolicy)
    # # layer_player.layout().addWidget(self.videoinfo_label)

    self.player_widget = MltWidget()
    # sizePolicy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    # sizePolicy.setWidthForHeight(self.player_widget.sizePolicy().hasWidthForHeight())
    # self.player_widget.setSizePolicy(sizePolicy)
    # self.player_widget_transparency = QGraphicsOpacityEffect()
    # self.player_widget.setGraphicsEffect(self.player_widget_transparency)
    # self.player_widget_transparency_animation = QPropertyAnimation(self.player_widget_transparency, b'opacity')
    # self.player_widget_transparency_animation.setEasingCurve(QEasingCurve.OutExpo)
    # self.player_widget_transparency.setOpacity(1)
    # self.player_widget_animation = QPropertyAnimation(self.player_widget, b'geometry')
    # self.player_widget_animation.setEasingCurve(QEasingCurve.OutCirc)
    # self.player_widget.positionChanged.connect(lambda: update_timelines(self))
    # self.player_widget.eofReached.connect(lambda: eof_reached(self))
    self.player_widget.setLayout(QVBoxLayout(self.player_widget))

    self.player_subtitle_layer = PlayerSubtitleLayer()
    self.player_subtitle_layer.setWordWrap(True)
    self.player_subtitle_layer.update_style(self.settings['videoplayer'])
    self.player_subtitle_layer.setObjectName('player_subtitle_layer')
    self.player_widget.layout().addWidget(self.player_subtitle_layer)

    # class player_border(QLabel):
    #     def resizeEvent(widget, e):
    #         if self.video_metadata:
    #             aspect_ratio = self.video_metadata.get('height', 1080) / self.video_metadata.get('width', 1920)
    #             widget.setFixedHeight(widget.height() * aspect_ratio) #self.video_metadata.get('width', 1920), self.video_metadata.get('height', 1080))
    #            # w = widget.width()
    #            # h = widget.height()
    #            # if w / h > aspect_ratio:  # too wide
    #            #     widget.setFixedHeight(h)
    #            #     widget.layout().setDirection(QBoxLayout.LeftToRight)
    #            #     widget_stretch = h * aspect_ratio
    #            #     outer_stretch = (w - widget_stretch) / 2 + 0.5
    #            # else:  # too tall
    #            #     widget.layout().setDirection(QBoxLayout.TopToBottom)
    #            #     widget_stretch = w / aspect_ratio
    #            #     outer_stretch = (h - widget_stretch) / 2 + 0.5

    self.player_border = QLabel(self)  # parent=self.layer_player
    self.player_border_transparency = QGraphicsOpacityEffect()
    self.player_border.setGraphicsEffect(self.player_border_transparency)
    self.player_border_transparency_animation = QPropertyAnimation(self.player_border_transparency, b'opacity')
    self.player_border_transparency_animation.setEasingCurve(QEasingCurve.OutCirc)
    self.player_border_transparency.setOpacity(0)
    self.player_border_animation = QPropertyAnimation(self.player_border, b'geometry')
    self.player_border_animation.setEasingCurve(QEasingCurve.OutCirc)
    self.player_border_animation.finished.connect(lambda: self.player_widget.fix_size())

    self.player_border.setObjectName('player_border')
    self.player_border.setLayout(QVBoxLayout(self.player_border))
    self.player_border.layout().setContentsMargins(1, 1, 1, 1)
    self.player_border.layout().addWidget(self.player_widget)

    # self.layer_player.layout().addWidget(self.player_border)

    # class player_ratio_widget(QLabel):
    #     def __init__(widget, child):
    #         super().__init__()
    #         widget.aspect_ratio = 1.77777#child.size().width() / child.size().height()
    #         widget.setLayout(QBoxLayout(QBoxLayout.LeftToRight))
    #         #  add spacer, then widget, then spacer
    #         widget.layout().addItem(QSpacerItem(0, 0))
    #         widget.layout().addWidget(child)
    #         widget.layout().addItem(QSpacerItem(0, 0))
    #         #sizePolicy = QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    #         #widget.setSizePolicy(sizePolicy)

    #     def resizeEvent(widget, e):
    #         w = e.size().width()
    #         h = e.size().height()
    #         if w / h > widget.aspect_ratio:  # too wide
    #             widget.layout().setDirection(QBoxLayout.LeftToRight)
    #             widget_stretch = h * widget.aspect_ratio
    #             outer_stretch = (w - widget_stretch) / 2 + 0.5
    #         else:  # too tall
    #             widget.layout().setDirection(QBoxLayout.TopToBottom)
    #             widget_stretch = w / widget.aspect_ratio
    #             outer_stretch = (h - widget_stretch) / 2 + 0.5

    #         widget.layout().setStretch(0, outer_stretch)
    #         widget.layout().setStretch(1, widget_stretch)
    #         widget.layout().setStretch(2, outer_stretch)

    # self.player_ratio_widget = player_ratio_widget(child=self.player_border)#QLabel()

    # self.layer_player.layout().addWidget(self.player_ratio_widget)

    # self.layer_player_hbox.addLayout(self.layer_player_vbox)


def update(self):
    """Function to update player widgets"""
    # self.layer_player.setVisible(bool(self.video_metadata))
    # self.player_border.setVisible(bool(self.video_metadata))
    update_safety_margins_subtitle_layer(self)


def resized(self):
    """Function to resize player widgets"""
    # self.layer_player.setGeometry(0, 0, self.width(), self.height())
    # self.layer_player.layout().setContentsMargins(self.width()*self.subtitles_panel_width_proportion, 20, 20, 200)
    # TODO
    # self.layer_player_left_spacer.setMaximumWidth(self.width()*self.subtitles_panel_width_proportion)
    # self.player_widget_area.setGeometry(0, 0, self.width(), self.height()-self.playercontrols_widget.height())
    # self.videoinfo_label.setGeometry(self.player_widget_area.x(), 20, self.player_widget_area.width(), 50)

    resize_player_widget(self)


def update_safety_margins_subtitle_layer(self):
    """Function to update margins preferences"""
    self.player_subtitle_layer.show_action_safe_margin = self.settings['safety_margins'].get('show_action_safe_margins', False)
    self.player_subtitle_layer.show_title_safe_margin = self.settings['safety_margins'].get('show_title_safe_margins', False)
    self.player_subtitle_layer.update()

# def player_subtitle_textedit_changed(self):
#     old_selected_subtitle = self.selected_subtitle
#     counter = globals.SESSION['segments'].index(old_selected_subtitle)
#     globals.SESSION['segments'][counter]['text'] = self.player_subtitle_textedit.toPlainText()
#     self.subtitles_panel.update_subtitles_panel_qlistwidget(self)
#     self.timeline.update(self)
#     update_subtitle_layer(self)


# def eof_reached(self):
#     self.player_widget.playpause()
#     self.playercontrols_playpause_button.setChecked(False)
#     # playercontrols.playercontrols_playpause_button_update(self)


def update_speed(self):
    """Function to change playback speed"""
    self.player_widget.playback_speed = self.playback_speed if self.change_playback_speed.isChecked() else 1.0
    self.player_widget.play()


def update_subtitle_layer(self):
    """Function to update subtitle layer"""
    None

    # text = ''
    # for subtitle in globals.SESSION['segments']:
    #     if self.player_widget.position() and (self.player_widget.position() > subtitle['start'] and self.player_widget.position() < subtitle['end']):
    #         text = subtitle['text']
    #         break
    # self.player_subtitle_layer.setSubtitleText(text)
    # self.player_subtitle_layer.update()


def resize_player_widget(self, just_get_qrect=False):
    """Function to resize player widget (to accomodate video ratio inside screen space)"""
    # None
    if self.video_metadata:
        x = int(self.subtitles_panel_width_proportion * self.width())
        y = 20
        w = self.width() - x - 20
        h = self.height() - y - 20 - 200

        if w / h > self.video_metadata.get('width', 1920) / self.video_metadata.get('height', 1080):
            aspect_ratio = self.video_metadata.get('width', 1920) / self.video_metadata.get('height', 1080)
            new_h = h
            new_w = h * aspect_ratio
            qrect = QRect(int(x + (w - new_w) / 2), y, int(new_w), int(new_h))
        else:
            aspect_ratio = self.video_metadata.get('height', 1080) / self.video_metadata.get('width', 1920)
            new_h = w * aspect_ratio
            new_w = w
            qrect = QRect(x, int(y + ((h - new_h) / 2)), int(new_w), int(new_h))

        if just_get_qrect:
            return [int(qrect.x()), int(qrect.y()), int(qrect.width()), int(qrect.height())]
        else:
            self.player_border.setGeometry(qrect)

    # if self.video_metadata.get('width', 1920) > self.video_metadata.get('height', 1080):
    #     wp = 1
    #     hp = self.video_metadata.get('height', 1080) / self.video_metadata.get('width', 1920)
    # else:
    #     wp = self.video_metadata.get('width', 1920) / self.video_metadata.get('height', 1080)
    #     hp = 1

    # self.video_metadata.get('width', 1920) / self.video_metadata.get('height', 1080)
    # if self.video_metadata.get('width', 640) > self.video_metadata.get('height', 480):
    #    heigth_proportion = ((self.player_widget_area.width()*.7)-6) / self.video_metadata.get('width', 640)
    #    self.player_widget.setGeometry((self.width()*.2) + 3, (self.player_widget_area.height()*.5)-((heigth_proportion*self.video_metadata.get('height', 480))*.5), (self.player_widget_area.width()*.7)-6, self.video_metadata.get('height', 480)*heigth_proportion)
    # else:
    #    width_proportion = (self.player_widget_area.height()-7) / self.video_metadata.get('height', 480)
    #    self.player_widget.setGeometry((self.width()*.2) + ((self.player_widget_area.width()*.7)*.5)-((width_proportion*self.video_metadata.get('width', 640))*.5), 3, self.video_metadata.get('width', 640)*width_proportion, self.player_widget_area.height()-6)
    # self.player_border.setGeometry(self.player_widget.x()-3, self.player_widget.y()-3, self.player_widget.width()+6, self.player_widget.height()+6)
    # self.player_subtitle_layer.setGeometry(self.player_widget.x(), self.player_widget.y(), self.player_widget.width(), self.player_widget.height())
    # self.player_subtitle_textedit.setGeometry(self.player_widget.x()+(self.player_widget.width()*.1), self.player_widget.y()+(self.player_widget.height()*.5), self.player_widget.width()*.8, self.player_widget.height()*.4)


def update_controls(self):
    playercontrols.playercontrols_stop_button_clicked(self)


def update_timelines(self):
    self.timeline.update(self)
    if not self.subtitles_panel_findandreplace_panel.isVisible():
        subtitles_panel.update_subtitles_panel_widget_vision_content(self)


def update_subtitle_text(self, subtitle=None, text=None):
    if subtitle is None:
        subtitle = self.selected_subtitle
    if text is None:
        text = self.properties_textedit.toPlainText()
    if subtitle and text is not None:
        producer = self.player_widget.playlist_sub.get_clip_at(int(subtitle['start'] * self.player_widget.profile.fps()) + 1)
        for i in range(producer.parent().filter_count()):
            if producer.parent().filter(i).get('argument'):
                producer.parent().filter(i).set('argument', text)