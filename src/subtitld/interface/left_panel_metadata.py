from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget, QLabel, QScrollArea, QStackedWidget, QPushButton, QTabWidget, QGridLayout, QSizePolicy
from PySide6.QtCore import Qt, QDateTime, QSize, QDate, QTime
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtMultimedia import QMediaMetaData, QtVideo

from subtitld.interface import utils
from subtitld.interface import left_panel
from subtitld.interface.translation import _


# Field tables — (key, label, QMediaMetaData attribute) per tab. Tabs render
# rows in this order; the third column lets us look up raw values directly
# from QMediaMetaData (or pass `None` for fields we derive ourselves).
TAGS_FIELDS = [
    ('title', 'TITLE', QMediaMetaData.Title),
    ('author', 'AUTHOR', QMediaMetaData.Author),
    ('genre', 'GENRE', QMediaMetaData.Genre),
    ('date', 'DATE', QMediaMetaData.Date),
    ('description', 'DESCRIPTION', QMediaMetaData.Description),
    ('language', 'LANGUAGE', QMediaMetaData.Language),
    ('url', 'URL', QMediaMetaData.Url),
    ('comment', 'COMMENT', QMediaMetaData.Comment),
    ('publisher', 'PUBLISHER', QMediaMetaData.Publisher),
    ('copyright', 'COPYRIGHT', QMediaMetaData.Copyright),
    ('album_title', 'ALBUM TITLE', QMediaMetaData.AlbumTitle),
    ('album_artist', 'ALBUM ARTIST', QMediaMetaData.AlbumArtist),
    ('contributing_artist', 'CONTRIBUTING ARTIST', QMediaMetaData.ContributingArtist),
    ('track_number', 'TRACK NUMBER', QMediaMetaData.TrackNumber),
    ('composer', 'COMPOSER', QMediaMetaData.Composer),
    ('lead_performer', 'LEAD PERFORMER', QMediaMetaData.LeadPerformer),
]

AUDIO_FIELDS = [
    ('audio_bitrate', 'AUDIO BITRATE', QMediaMetaData.AudioBitRate),
]

VIDEO_FIELDS = [
    ('duration', 'DURATION', QMediaMetaData.Duration),
    ('media_type', 'MEDIA TYPE', QMediaMetaData.MediaType),
    ('video_bitrate', 'VIDEO BITRATE', QMediaMetaData.VideoBitRate),
    ('video_frame_rate', 'VIDEO FRAME RATE', QMediaMetaData.VideoFrameRate),
    ('orientation', 'ORIENTATION', None),
    ('resolution', 'RESOLUTION', QMediaMetaData.Resolution),
    ('has_hdr_content', 'HAS HDR CONTENT', QMediaMetaData.HasHdrContent),
    ('thumbnail_image', 'THUMBNAIL IMAGE', QMediaMetaData.ThumbnailImage),
    ('cover_art_image', 'COVER ART IMAGE', QMediaMetaData.CoverArtImage),
]

# Cap rendered metadata images so a 4K cover doesn't blow up the panel.
IMAGE_MAX_WIDTH = 240


def _wrappable(text):
    """Insert zero-width spaces after URL-ish punctuation so QLabel's word
    wrap has somewhere to break long unbroken tokens (URLs, hashes, etc.)."""
    for ch in ('/', '?', '&', '=', '_', '-', '.', '#', ',', ':'):
        text = text.replace(ch, ch + '​')
    return text


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        else:
            child_layout = item.layout()
            if child_layout is not None:
                _clear_layout(child_layout)


def _empty_label():
    label = QLabel(_('metadata_panel.not_set'))
    label.setProperty('class', 'metadata_field_value')
    label.setProperty('empty', True)
    label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    return label


def _text_label(text):
    label = QLabel()
    label.setProperty('class', 'metadata_field_value')
    label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setText(text)
    return label


def _badge(text):
    badge = QLabel(str(text))
    badge.setProperty('class', 'metadata_badge')
    badge.setAlignment(Qt.AlignCenter)
    badge.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    return badge


def _badge_row(badges):
    row = QWidget()
    row.setProperty('class', 'transparent_panel')
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    for b in badges:
        layout.addWidget(b)
    layout.addStretch()
    return row


def _is_empty(raw):
    if raw is None:
        return True
    if isinstance(raw, str):
        return not raw.strip()
    if isinstance(raw, (list, tuple)):
        return not any(str(x).strip() for x in raw)
    if isinstance(raw, QImage):
        return raw.isNull()
    if isinstance(raw, QSize):
        return not raw.isValid() or (raw.width() == 0 and raw.height() == 0)
    if isinstance(raw, QDateTime):
        return not raw.isValid()
    if isinstance(raw, (QDate, QTime)):
        return not raw.isValid()
    return False


def _format_value(cell, raw):
    """Render `raw` into `cell` (a QWidget with a QVBoxLayout). Replaces any
    previous content so the same cell can switch between text / badges /
    images across reloads."""
    layout = cell.layout()
    _clear_layout(layout)

    if _is_empty(raw):
        layout.addWidget(_empty_label())
        return

    # QImage → render the actual image (capped width).
    if isinstance(raw, QImage):
        pixmap = QPixmap.fromImage(raw)
        if pixmap.width() > IMAGE_MAX_WIDTH:
            pixmap = pixmap.scaledToWidth(IMAGE_MAX_WIDTH, Qt.SmoothTransformation)
        image_label = QLabel()
        image_label.setProperty('class', 'metadata_field_image')
        image_label.setPixmap(pixmap)
        image_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(image_label, 0, Qt.AlignLeft | Qt.AlignTop)
        return

    # QSize → "1920 × 1080" badge.
    if isinstance(raw, QSize):
        layout.addWidget(_badge_row([_badge(f'{raw.width()} × {raw.height()}')]))
        return

    # QDateTime / QDate / QTime → human-readable string.
    if isinstance(raw, QDateTime):
        layout.addWidget(_text_label(raw.toString('yyyy-MM-dd HH:mm:ss')))
        return
    if isinstance(raw, QDate):
        layout.addWidget(_text_label(raw.toString('yyyy-MM-dd')))
        return
    if isinstance(raw, QTime):
        layout.addWidget(_text_label(raw.toString('HH:mm:ss')))
        return

    # List / tuple → one badge per non-empty item.
    if isinstance(raw, (list, tuple)):
        items = [str(x).strip() for x in raw if str(x).strip()]
        layout.addWidget(_badge_row([_badge(item) for item in items]))
        return

    # Default: plain text (with break hints for long URLs).
    layout.addWidget(_text_label(_wrappable(str(raw)).replace('\n', '<br>')))


def _build_metadata_tab(fields):
    """Build one metadata tab: a scrollable two-column grid. Returns the tab
    widget plus a `{key: value_cell}` map so `update()` can populate the
    cells without rebuilding."""
    tab = QWidget()
    tab.setProperty('class', 'transparent_panel')
    tab.setLayout(QVBoxLayout())
    tab.layout().setContentsMargins(0, 0, 0, 0)
    tab.layout().setSpacing(0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    tab.layout().addWidget(scroll)

    inner = QWidget()
    inner.setProperty('class', 'transparent_panel')
    grid = QGridLayout(inner)
    grid.setContentsMargins(14, 14, 14, 14)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(18)
    grid.setColumnStretch(0, 0)
    grid.setColumnStretch(1, 1)
    scroll.setWidget(inner)

    cells = {}
    for row, (key, label_text, _attr) in enumerate(fields):
        label = QLabel(label_text)
        label.setProperty('class', 'metadata_field_label')
        label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        label.setMinimumWidth(96)
        label.setMaximumWidth(120)
        label.setWordWrap(True)
        grid.addWidget(label, row, 0, Qt.AlignTop)

        cell = QWidget()
        cell.setProperty('class', 'transparent_panel')
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.setSpacing(4)
        grid.addWidget(cell, row, 1, Qt.AlignTop)

        cells[key] = cell

    grid.setRowStretch(len(fields), 1)
    return tab, cells


def load(self):
    tab_name = 'metadata'

    left_panel_metadata_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )
    left_panel_metadata_panel.layout().setContentsMargins(0, 0, 0, 0)

    self.left_panel_metadata_tabwidget = QTabWidget()
    self.left_panel_metadata_tabwidget.setObjectName('left_panel_metadata_tabwidget')
    left_panel_metadata_panel.layout().addWidget(self.left_panel_metadata_tabwidget)

    tags_tab, self.left_panel_metadata_tag_cells = _build_metadata_tab(TAGS_FIELDS)
    self.left_panel_metadata_tabwidget.addTab(tags_tab, '')

    audio_tab, self.left_panel_metadata_audio_cells = _build_metadata_tab(AUDIO_FIELDS)
    self.left_panel_metadata_tabwidget.addTab(audio_tab, '')

    video_tab, self.left_panel_metadata_video_cells = _build_metadata_tab(VIDEO_FIELDS)
    self.left_panel_metadata_tabwidget.addTab(video_tab, '')


def show(self):
    update(self)


def update(self):
    md = self.preview_panel_player._media_player.metaData()

    for key, _label, attr in TAGS_FIELDS:
        _format_value(self.left_panel_metadata_tag_cells[key], md.value(attr))

    for key, _label, attr in AUDIO_FIELDS:
        _format_value(self.left_panel_metadata_audio_cells[key], md.value(attr))

    orientation_value = md.value(QMediaMetaData.Orientation)
    if orientation_value == QtVideo.Rotation.Clockwise90:
        orientation = 'Clockwise 90°'
    elif orientation_value == QtVideo.Rotation.Clockwise270:
        orientation = 'Clockwise 270°'
    elif orientation_value == QtVideo.Rotation.Clockwise180:
        orientation = 'Clockwise 180°'
    elif orientation_value == QtVideo.Rotation.None_:
        orientation = '0°'
    else:
        orientation = None  # → 'Not set'

    for key, _label, attr in VIDEO_FIELDS:
        cell = self.left_panel_metadata_video_cells[key]
        if key == 'orientation':
            _format_value(cell, orientation)
        else:
            _format_value(cell, md.value(attr))


def hide(self):
    pass


def translate(self):
    self.left_panel_metadata_tabwidget.setTabText(0, _('metadata_panel.tab_tags'))
    self.left_panel_metadata_tabwidget.setTabText(1, _('metadata_panel.tab_audio'))
    self.left_panel_metadata_tabwidget.setTabText(2, _('metadata_panel.tab_video'))
