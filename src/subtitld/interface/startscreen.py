import pathlib
import datetime

from PySide6.QtWidgets import QPushButton, QLabel, QListWidget, QVBoxLayout, QWidget, QHBoxLayout, QSizePolicy, QFileDialog, QListWidgetItem, QAbstractItemView, QGraphicsOpacityEffect
from PySide6.QtCore import QPropertyAnimation, Qt, QEasingCurve, QTimer
from PySide6.QtGui import QPixmap

from subtitld.modules import file_io
from subtitld.modules import session

from subtitld.interface import productionscreen
from subtitld.interface import utils
from subtitld.interface import top_bar
from subtitld.interface import playercontrols

from subtitld.interface.translation import _

from subtitld import __version__


list_of_supported_extensions = []
list_of_supported_subtitle_extensions = []
list_of_supported_video_extensions = []
for exttype in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS:
    for ext in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[exttype]['extensions']:
        list_of_supported_extensions.append(ext)
        list_of_supported_subtitle_extensions.append(ext)
for exttype in session.LIST_OF_SUPPORTED_VIDEO_EXTENSIONS:
    for ext in session.LIST_OF_SUPPORTED_VIDEO_EXTENSIONS[exttype]['extensions']:
        list_of_supported_extensions.append(ext)
        list_of_supported_video_extensions.append(ext)


def load(self):
    self.start_screen = QWidget()
    self.start_screen.setLayout(QVBoxLayout())
    self.start_screen.layout().addStretch()
    self.start_screen.layout().setContentsMargins(0, 0, 0, 0)

    self.start_screen_bottom_line_container = QWidget()
    self.start_screen_bottom_line_container.setLayout(QVBoxLayout())
    self.start_screen_bottom_line_container.layout().setContentsMargins(0, 0, 0, 0)
    
    self.start_screen_bottom_line = QWidget()
    self.start_screen_bottom_line.setObjectName('start_screen_bottom_line')
    self.start_screen_bottom_line.setSizePolicy(QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum))
    self.start_screen_bottom_line.setLayout(QHBoxLayout())
    self.start_screen_bottom_line.layout().setContentsMargins(0, 2, 0, 0)
    self.start_screen_bottom_line_animation = QPropertyAnimation(self.start_screen_bottom_line, b'pos')
    self.start_screen_bottom_line_animation.setEasingCurve(QEasingCurve.OutCubic)
    self.start_screen_bottom_line_container.layout().addWidget(self.start_screen_bottom_line)

    self.start_screen_bottom_open_file_column = QWidget()
    self.start_screen_bottom_open_file_column.setLayout(QVBoxLayout())

    self.start_screen_open_label = QLabel()
    self.start_screen_open_label.setAlignment(Qt.AlignRight)
    self.start_screen_open_label.setObjectName('start_screen_open_label')
    self.start_screen_bottom_open_file_column.layout().addWidget(self.start_screen_open_label)

    self.start_screen_open_button = QPushButton()
    self.start_screen_open_button.setObjectName('start_screen_open_button')
    self.start_screen_open_button.clicked.connect(lambda: start_screen_open_button_clicked(self))
    self.start_screen_open_button.setProperty('class', 'button_dark')
    self.start_screen_bottom_open_file_column.layout().addWidget(self.start_screen_open_button, 0, Qt.AlignRight)

    self.start_screen_bottom_open_file_column.layout().addStretch()

    self.start_screen_bottom_line.layout().addWidget(self.start_screen_bottom_open_file_column, 1)

    self.start_screen_recentfiles_background = QLabel()
    self.start_screen_recentfiles_background.setObjectName('start_screen_recentfiles_background')
    self.start_screen_recentfiles_background.setLayout(QVBoxLayout())
    self.start_screen_recentfiles_background.layout().setContentsMargins(10, 10, 10, 10)
    self.start_screen_recentfiles_background.setAutoFillBackground(True)
    self.start_screen_recentfiles_background.setFixedSize(350, 200)

    self.start_screen_recent_label = QLabel()
    self.start_screen_recent_label.setAlignment(Qt.AlignCenter)
    self.start_screen_recent_label.setObjectName('start_screen_recent_label')
    self.start_screen_recentfiles_background.layout().addWidget(self.start_screen_recent_label)

    self.start_screen_recent_listwidget = QListWidget()
    self.start_screen_recent_listwidget.setObjectName('start_screen_recent_listwidget')
    self.start_screen_recent_listwidget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    self.start_screen_recent_listwidget.setFocusPolicy(Qt.NoFocus)
    self.start_screen_recent_listwidget.setUniformItemSizes(False)
    self.start_screen_recent_listwidget.setViewportMargins(0, 0, 10, 0)
    self.start_screen_recent_listwidget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    self.start_screen_recent_listwidget.itemDoubleClicked.connect(lambda item: start_screen_recent_listwidget_item_clicked(self, item))
    self.start_screen_recentfiles_background.layout().addWidget(self.start_screen_recent_listwidget)

    self.start_screen_bottom_line.layout().addWidget(self.start_screen_recentfiles_background, 0)

    self.start_screen_bottom_version_column = QWidget()
    self.start_screen_bottom_version_column.setLayout(QVBoxLayout())

    self.start_screen_adver_label = QLabel()
    self.start_screen_adver_label.setObjectName('start_screen_adver_label')
    self.start_screen_bottom_version_column.layout().addWidget(self.start_screen_adver_label, 0, Qt.AlignLeft)

    self.start_screen_adver_label_details = QLabel()
    self.start_screen_adver_label_details.setObjectName('start_screen_adver_label_details')
    self.start_screen_bottom_version_column.layout().addWidget(self.start_screen_adver_label_details, 0, Qt.AlignLeft)
    self.start_screen_bottom_version_column.layout().addStretch()

    self.start_screen_bottom_line.layout().addWidget(self.start_screen_bottom_version_column, 1)

    self.start_screen.layout().addWidget(self.start_screen_bottom_line_container, 0)

    self.central_widget.layout().addWidget(self.start_screen)


def show(self):
    utils.animate_element(self.start_screen_bottom_line_animation, duration=1000, effect='slide_from_bottom')
    update_recent_files_list(self)


def hide(self):
    utils.animate_element(self.start_screen_bottom_line_animation, duration=200, effect='slide_to_bottom')
    

def start_screen_open_button_clicked(self):
    all_supported_subtitle_files = _('file_io.all_supported_files') + ' ({})'.format(" ".join(["*.{}".format(fo) for fo in list_of_supported_extensions]))
    
    selected_files = QFileDialog.getOpenFileNames(
        parent=self, 
        caption=_('file_io.all_supported_files'), 
        dir=str(session.PATH_HOME), 
        filter=all_supported_subtitle_files
    )[0]

    selected_subtitle_filepath = False
    selected_video_filepath = False
    
    for filepath in selected_files:
        if filepath.rsplit('.', 1)[-1].upper() in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS.keys():
            selected_subtitle_filepath = filepath
            break 
    
    for filepath in selected_files:
        if filepath.rsplit('.', 1)[-1].upper() in session.LIST_OF_SUPPORTED_VIDEO_EXTENSIONS.keys():
            selected_video_filepath = filepath
            break

    if not selected_video_filepath and selected_subtitle_filepath and selected_subtitle_filepath.lower().endswith('.usfx'):
        peeked = file_io.peek_usfx_video(selected_subtitle_filepath)
        if peeked:
            selected_video_filepath = peeked

    if not selected_video_filepath:
        supported_video_files = _('file_io.video_files') + ' ({})'.format(" ".join(["*.{}".format(fo) for fo in list_of_supported_video_extensions]))
        selected_video_filepath = QFileDialog.getOpenFileName(
            parent=self,
            caption=_('file_io.all_supported_files'),
            dir=str(session.PATH_HOME),
            filter=supported_video_files
        )[0]
        
    if selected_video_filepath:
        session.VIDEO['filepath'] = selected_video_filepath
        if selected_subtitle_filepath:
            session.SUBTITLE['filepath'] = selected_subtitle_filepath
            

        load_productionscreen(self)
        

def load_productionscreen(self):
    if session.VIDEO.get('filepath', False):
        top_bar.show(self)
        QTimer().singleShot(200, lambda: productionscreen.show(self))

        self.preview_panel_player.loadfile(session.VIDEO['filepath'])

        session.VIDEO = file_io.process_video_file(session.VIDEO['filepath'])

        has_audio = bool(session.VIDEO.get('audio_is_present', False))
        self.music_voice_separation_box.setVisible(has_audio)
        if hasattr(self, 'global_panel_import_start_transcription_button'):
            self.global_panel_import_start_transcription_button.setEnabled(has_audio)
            self.global_panel_import_start_transcription_button.setToolTip('' if has_audio else _('startscreen.no_audio_in_video'))
        if has_audio:
            self.music_voice_separation_thread.filename = session.VIDEO['filepath']
            self.music_voice_separation_thread.start()

        if session.SUBTITLE.get('filepath', False) and pathlib.Path(session.SUBTITLE['filepath']).exists():
            session.SUBTITLE['segments'], session.CONFIG['format_to_save'] = file_io.process_subtitles_file(session.SUBTITLE['filepath'])
            for name in {segment.get('speaker', 'A') for segment in session.SUBTITLE['segments']}:
                session.SPEAKERS.setdefault(name, {})
            self.preview_panel_player._audio_device.sync_subtitle_dubs(session.SUBTITLE['segments'])

            if session.CONFIG.get('recent_files', False) and session.SUBTITLE['filepath'] in session.CONFIG['recent_files']:
                self.preview_panel_player.seek(session.CONFIG['recent_files'][str(session.SUBTITLE['filepath'])].get('last_position', 0))

            session.add_to_recent_files(session.SUBTITLE['filepath'], session.VIDEO.get('filepath', ''))

        QTimer.singleShot(0, self.timeline_widget.load_waveform)

def start_screen_recent_listwidget_item_clicked(self, item):
    config = item.data(Qt.UserRole)
    session.VIDEO['filepath'] = config['video_filepath']
    session.SUBTITLE['filepath'] = config['subtitle_filepath']
    session.SUBTITLE['segments'], session.CONFIG['format_to_save'] = file_io.process_subtitles_file(session.SUBTITLE['filepath'])
    for name in {segment.get('speaker', 'A') for segment in session.SUBTITLE['segments']}:
        session.SPEAKERS.setdefault(name, {})
    session.VIDEO = file_io.process_video_file(session.VIDEO['filepath'])
    self.preview_panel_player._audio_device.sync_subtitle_dubs(session.SUBTITLE['segments'])
    load_productionscreen(self)
    

def translate(self):
    self.start_screen_open_label.setText(_('startscreen.open_subtitle_or_video'))
    self.start_screen_open_button.setText(_('startscreen.open'))
    self.start_screen_open_button.setToolTip(_('startscreen.open_tooltip'))
    self.start_screen_recent_label.setText(_('startscreen.recent_subitles'))
    self.start_screen_adver_label.setText((_('startscreen.version_number').format(__version__)))
    self.start_screen_adver_label_details.setText(_('startscreen.visit_website'))


def update_recent_files_list(self):
    self.start_screen_recentfiles_background.setVisible(bool(session.CONFIG.get('recent_files', False)))
    
    recent_files = session.CONFIG.get('recent_files', {})
    if recent_files:
        sorted_files = sorted(
            recent_files.items(),
            key=lambda item: float(item[1].get('last_opened', 0)),
            reverse=True
        )

        for filepath, config in sorted_files:
            subtitle_path = pathlib.Path(filepath)
            video_path = pathlib.Path(config['video_filepath'])
            
            config['subtitle_filepath'] = str(subtitle_path)

            if subtitle_path.exists() and video_path.exists():    
                try:            
                    item_widget = QWidget()
                    item_widget.setLayout(QHBoxLayout())
                    item_widget.layout().setContentsMargins(5, 5, 5, 5)
                                    
                    item_icon = QLabel()
                    item_icon.opacity = QGraphicsOpacityEffect()
                    item_icon.opacity.setOpacity(0.5)
                    item_icon.setGraphicsEffect(item_icon.opacity)
                    item_icon.setFixedSize(24, 24)
                    item_icon.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
                    item_icon.setAlignment(Qt.AlignCenter)
                    item_icon.setPixmap(QPixmap(session.PATH_SUBTITLD_GRAPHICS / 'file.svg').scaled(16, 16, Qt.KeepAspectRatioByExpanding))
                    item_widget.layout().addWidget(item_icon, 0, Qt.AlignTop)

                    item_widget_title_line = QLabel()
                    item_widget_title_line.setAlignment(Qt.AlignLeft)
                    item_widget_title_line.setWordWrap(True)
                    item_widget_title_line.setText(f'<b>{subtitle_path.name}</b><br /><small>{video_path.name}</small>')
                    
                    item_widget.layout().addWidget(item_widget_title_line, 1)

                    age = utils.friendly_time(datetime.datetime.fromtimestamp(float(config['last_opened'])))
                    item_widget_age_line = QLabel()
                    item_widget_age_line.setProperty('class', 'age')
                    item_widget_age_line.setAlignment(Qt.AlignRight)
                    item_widget_age_line.setText(age)
                    item_widget.layout().addWidget(item_widget_age_line, 0, Qt.AlignRight | Qt.AlignTop)
                    
                    list_widget_item = QListWidgetItem(self.start_screen_recent_listwidget)
                    list_widget_item.setData(Qt.UserRole, config)
                    list_widget_item.setSizeHint(item_widget.sizeHint())
                    self.start_screen_recent_listwidget.addItem(list_widget_item)
                    self.start_screen_recent_listwidget.setItemWidget(list_widget_item, item_widget)
                except Exception as e:
                    continue