from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel, QScrollArea, QStackedWidget, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaMetaData, QtVideo

from subtitld.interface import utils
from subtitld.interface import left_panel
from subtitld.interface.translation import _


def load(self):
    tab_name = 'metadata'
    
    left_panel_metadata_panel = QWidget()
    left_panel_metadata_panel.setObjectName(f'left_panel_{tab_name}')
    left_panel_metadata_panel.setProperty('tab_name', tab_name)
    left_panel_metadata_panel.setLayout(QVBoxLayout())
    left_panel_metadata_panel.layout().setContentsMargins(10, 10, 10, 10)

    left_panel_metadata_panel_scroll = QScrollArea()
    left_panel_metadata_panel_scroll.setObjectName('left_panel_metadata_panel_scroll')
    left_panel_metadata_panel_scroll.setWidgetResizable(True)
    left_panel_metadata_panel_scroll.setFrameShape(QScrollArea.NoFrame)
    left_panel_metadata_panel.layout().addWidget(left_panel_metadata_panel_scroll)

    self.left_panel_metadata_panel_info = QLabel()
    self.left_panel_metadata_panel_info.setObjectName('left_panel_metadata_panel_info')
    self.left_panel_metadata_panel_info.setWordWrap(True)
    self.left_panel_metadata_panel_info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    left_panel_metadata_panel_scroll.setWidget(self.left_panel_metadata_panel_info)

    left_panel_metadata_panel.update = update
    
    left_panel.add_panel(self, left_panel_metadata_panel)

    
def show(self):
    update(self)

def update(self):
    final_text = f'<small><b>TITLE</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Title)}<br><br>'
    final_text += f'<small><b>AUTHOR</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Author)}<br><br>'
    final_text += f'<small><b>COMMENT</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Comment)}<br><br>'
    description = (self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Description) or '').replace('\n', '<br>')
    final_text += f"<small><b>DESCRIPTION</b></small><br>{description}<br><br>"
    final_text += f'<small><b>GENRE</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Genre)}<br><br>'
    final_text += f'<small><b>DATE</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Date)}<br><br>'
    final_text += f'<small><b>LANGUAGE</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Language)}<br><br>'
    final_text += f'<small><b>PUBLISHER</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Publisher)}<br><br>'
    final_text += f'<small><b>COPYRIGHT</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Copyright)}<br><br>'
    final_text += f'<small><b>URL</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Url)}<br><br>'
    final_text += f'<small><b>DURATION</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Duration)}<br><br>'
    final_text += f'<small><b>MEDIA TYPE</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.MediaType)}<br><br>'
    # final_text += f'<small><b>FILE FORMAT</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.FileFormat)}<br><br>'
    final_text += f'<small><b>AUDIO BITRATE</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.AudioBitRate)}<br><br>'
    # final_text += f'<small><b>AUDIO CODEC</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.AudioCodec)}<br><br>'
    final_text += f'<small><b>VIDEO BITRATE</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.VideoBitRate)}<br><br>'
    # final_text += f'<small><b>VIDEO CODEC</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.VideoCodec)}<br><br>'
    final_text += f'<small><b>VIDEO FRAME RATE</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.VideoFrameRate)}<br><br>'
    final_text += f'<small><b>ALBUM TITLE</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.AlbumTitle)}<br><br>'
    final_text += f'<small><b>ALBUM ARTIST</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.AlbumArtist)}<br><br>'
    final_text += f'<small><b>CONTRIBUTING ARTIST</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.ContributingArtist)}<br><br>'
    final_text += f'<small><b>TRACK NUMBER</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.TrackNumber)}<br><br>'
    final_text += f'<small><b>COMPOSER</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Composer)}<br><br>'
    final_text += f'<small><b>LEAD PERFORMER</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.LeadPerformer)}<br><br>'
    final_text += f'<small><b>THUMBNAIL IMAGE</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.ThumbnailImage)}<br><br>'
    final_text += f'<small><b>COVER ART IMAGE</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.CoverArtImage)}<br><br>'
    orientation = 'Clockwise 90' if self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Orientation) == QtVideo.Rotation.Clockwise90 else 'Clockwise 270' if self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Orientation) == QtVideo.Rotation.Clockwise270 else 'Clockwise 180' if self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Orientation) == QtVideo.Rotation.Clockwise180 else 'Unknown'
    final_text += f'<small><b>ORIENTATION</b></small><br>{orientation}<br><br>'
    final_text += f'<small><b>RESOLUTION</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.Resolution)}<br><br>'
    final_text += f'<small><b>HAS HDR CONTENT</b></small><br>{self.preview_panel_player._media_player.metaData().value(QMediaMetaData.HasHdrContent)}<br><br>'
    self.left_panel_metadata_panel_info.setText(final_text)
    
def hide(self):
    pass
    
def translate(self):
    pass