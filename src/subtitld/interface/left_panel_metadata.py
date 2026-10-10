from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget, QLabel, QScrollArea, QStackedWidget, QPushButton, QTabWidget, QGridLayout, QSizePolicy
from PySide6.QtCore import Qt, QDateTime, QSize, QDate, QTime
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtMultimedia import QMediaMetaData, QtVideo

from subtitld.interface import utils
from subtitld.interface import left_panel
from subtitld.interface.translation import _


# Field tables — (key, label's key in the locale files, QMediaMetaData
# attribute) per tab. Tabs render rows in this order; the third column lets
# us look up raw values directly from QMediaMetaData (or pass `None` for
# fields we derive ourselves).
TAGS_FIELDS = [
    ('title', 'metadata_panel.field_title', QMediaMetaData.Title),
    ('author', 'metadata_panel.field_author', QMediaMetaData.Author),
    ('genre', 'metadata_panel.field_genre', QMediaMetaData.Genre),
    ('date', 'metadata_panel.field_date', QMediaMetaData.Date),
    ('description', 'metadata_panel.field_description', QMediaMetaData.Description),
    ('language', 'metadata_panel.field_language', QMediaMetaData.Language),
    ('url', 'metadata_panel.field_url', QMediaMetaData.Url),
    ('comment', 'metadata_panel.field_comment', QMediaMetaData.Comment),
    ('publisher', 'metadata_panel.field_publisher', QMediaMetaData.Publisher),
    ('copyright', 'metadata_panel.field_copyright', QMediaMetaData.Copyright),
    ('album_title', 'metadata_panel.field_album_title', QMediaMetaData.AlbumTitle),
    ('album_artist', 'metadata_panel.field_album_artist', QMediaMetaData.AlbumArtist),
    ('contributing_artist', 'metadata_panel.field_contributing_artist', QMediaMetaData.ContributingArtist),
    ('track_number', 'metadata_panel.field_track_number', QMediaMetaData.TrackNumber),
    ('composer', 'metadata_panel.field_composer', QMediaMetaData.Composer),
    ('lead_performer', 'metadata_panel.field_lead_performer', QMediaMetaData.LeadPerformer),
]

AUDIO_FIELDS = [
    ('audio_bitrate', 'metadata_panel.field_audio_bitrate', QMediaMetaData.AudioBitRate),
]

VIDEO_FIELDS = [
    ('duration', 'metadata_panel.field_duration', QMediaMetaData.Duration),
    ('media_type', 'metadata_panel.field_media_type', QMediaMetaData.MediaType),
    ('video_bitrate', 'metadata_panel.field_video_bitrate', QMediaMetaData.VideoBitRate),
    ('video_frame_rate', 'metadata_panel.field_video_frame_rate', QMediaMetaData.VideoFrameRate),
    ('orientation', 'metadata_panel.field_orientation', None),
    ('resolution', 'metadata_panel.field_resolution', QMediaMetaData.Resolution),
    ('has_hdr_content', 'metadata_panel.field_has_hdr_content', QMediaMetaData.HasHdrContent),
    ('thumbnail_image', 'metadata_panel.field_thumbnail_image', QMediaMetaData.ThumbnailImage),
    ('cover_art_image', 'metadata_panel.field_cover_art_image', QMediaMetaData.CoverArtImage),
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
    cells without rebuilding, and a `{label_key: label}` map for
    `translate()` to name the rows."""
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
    labels = {}
    for row, (key, label_key, _attr) in enumerate(fields):
        label = QLabel()
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
        labels[label_key] = label

    grid.setRowStretch(len(fields), 1)
    return tab, cells, labels


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

    self.left_panel_metadata_labels = {}

    tags_tab, self.left_panel_metadata_tag_cells, labels = _build_metadata_tab(TAGS_FIELDS)
    self.left_panel_metadata_labels.update(labels)
    self.left_panel_metadata_tabwidget.addTab(tags_tab, '')

    audio_tab, self.left_panel_metadata_audio_cells, labels = _build_metadata_tab(AUDIO_FIELDS)
    self.left_panel_metadata_labels.update(labels)
    self.left_panel_metadata_tabwidget.addTab(audio_tab, '')

    video_tab, self.left_panel_metadata_video_cells, labels = _build_metadata_tab(VIDEO_FIELDS)
    self.left_panel_metadata_labels.update(labels)
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
        orientation = _('metadata_panel.orientation_clockwise_90')
    elif orientation_value == QtVideo.Rotation.Clockwise270:
        orientation = _('metadata_panel.orientation_clockwise_270')
    elif orientation_value == QtVideo.Rotation.Clockwise180:
        orientation = _('metadata_panel.orientation_clockwise_180')
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
    # Qt ignores QSS text-transform on a label, so the caps are ours.
    for label_key, label in self.left_panel_metadata_labels.items():
        label.setText(_(label_key).upper())
    # The values hold texts too: "Not set", the orientation.
    update(self)
