import pathlib
import os
import datetime

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget, QSizePolicy, QLabel, QSpacerItem, QGraphicsOpacityEffect, QFileDialog
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QSize, QTimer

from subtitld.interface import utils
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import file_io
from subtitld.modules import utils as modules_utils


def load(self):
    self.titleBar_left_container = QWidget(self)
    self.titleBar_left_container.setObjectName('titleBar_left_container')
    self.titleBar_left_container.setLayout(QHBoxLayout())
    self.titleBar_left_container.layout().setContentsMargins(0, 0, 0, 0)
    self.titleBar_left_container.setSizePolicy(QSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.MinimumExpanding))
    self.titleBar_left_container.setAttribute(Qt.WA_StyledBackground, True)
    self.titleBar_left_container.opacity = QGraphicsOpacityEffect()
    self.titleBar_left_container.opacity.setOpacity(0)
    self.titleBar_left_container.setGraphicsEffect(self.titleBar_left_container.opacity)
    self.titleBar_left_container.animation = QPropertyAnimation(self.titleBar_left_container, b'pos')
    self.titleBar_left_container.animation.setEasingCurve(QEasingCurve.OutQuint)
    
    self.titleBar.layout().setAlignment(Qt.AlignTop)
    self.titleBar.setFixedHeight(50)
    self.titleBar.setObjectName('titleBar')
    self.titleBar.setAttribute(Qt.WA_StyledBackground, True)
        
    # Rearrange icons to top
    self.titleBar.layout().setAlignment(self.titleBar.minBtn, Qt.AlignTop)
    self.titleBar.layout().setAlignment(self.titleBar.maxBtn, Qt.AlignTop)
    self.titleBar.layout().setAlignment(self.titleBar.closeBtn, Qt.AlignTop)

    # Remove spacer
    for i in range(self.titleBar.layout().count()):
        item = self.titleBar.layout().itemAt(i)
        if isinstance(item, QSpacerItem):
            self.titleBar.layout().removeItem(item)

    # Add our bar
    self.titleBar.layout().insertWidget(0, self.titleBar_left_container)

    class titleBar_left_save_button(QPushButton):
        def __init__(widget, parent=None):
            super().__init__(parent)
            widget.key_modifiers = []

        def keyPressEvent(widget, event):
            widget.key_modifiers = event.modifiers()
            event.accept()

        def keyReleaseEvent(widget, event):
            widget.key_modifiers = []
            event.accept()
        
        def update_state(widget):
            widget.setProperty(
                'class',
                'unsaved' if session.UNSAVED else 'saved'
            )
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            super().update()

    self.titleBar_left_save_button = titleBar_left_save_button(self)
    self.titleBar_left_save_button.setObjectName('titleBar_left_save_button')
    self.titleBar_left_save_button.setProperty('class', 'saved')
    self.titleBar_left_save_button.setIconSize(QSize(16, 16))
    self.titleBar_left_save_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.titleBar_left_save_button.clicked.connect(lambda: toppanel_save_button_clicked(self))
    session._unsaved_change_callbacks.append(self.titleBar_left_save_button.update_state)
    self.titleBar_left_container.layout().addWidget(self.titleBar_left_save_button, alignment=Qt.AlignLeft | Qt.AlignVCenter)

    self.titleBar_left_information_container = QWidget(self)
    self.titleBar_left_information_container.setObjectName('titleBar_left_information_container')
    self.titleBar_left_information_container.setSizePolicy(QSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.MinimumExpanding))
    self.titleBar_left_information_container.setLayout(QHBoxLayout())
    self.titleBar_left_information_container.layout().setContentsMargins(0, 0, 0, 0)
    self.titleBar_left_container.layout().addWidget(self.titleBar_left_information_container)

    self.titleBar_left_information_label = QLabel(self)
    self.titleBar_left_information_label.setObjectName('titleBar_left_information_label')
    self.titleBar_left_information_label.setSizePolicy(QSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.MinimumExpanding))
    self.titleBar_left_information_container.layout().addWidget(self.titleBar_left_information_label, alignment=Qt.AlignLeft | Qt.AlignVCenter)
    self.titleBar_left_information_label.setText('<b>filename.usf</b><br /><small>subtitle format</small>')

    self.tilteBar_subtitld_label = QLabel(self)
    self.tilteBar_subtitld_label.setObjectName('tilteBar_subtitld_label')
    self.tilteBar_subtitld_label.setAttribute(Qt.WA_StyledBackground, True)
    self.titleBar.layout().insertWidget(1, self.tilteBar_subtitld_label, alignment=Qt.AlignRight | Qt.AlignTop)
    self.tilteBar_subtitld_label.setText('<b>SUBTITLD</b>  v25.08.01.01')


def update(self):
    filename = _('top_bar.untitled_file')
    filepath = pathlib.Path(session.VIDEO.get('filepath', '')).parent
    if session.SUBTITLE.get('filepath', ''):
        filename = pathlib.Path(session.SUBTITLE.get('filepath', '')).name
        filepath = pathlib.Path(session.SUBTITLE.get('filepath', '')).parent
            
    self.titleBar_left_information_label.setText(f'<b>{filename}</b><br /><small>{filepath}</small>')


def show(self):
    update(self)
    QTimer().singleShot(200, lambda: self.titleBar_left_container.opacity.setOpacity(1.0))
    utils.animate_element(self.titleBar_left_container.animation, duration=1000, effect='slide_from_left')


def translate(self):
    self.titleBar_left_save_button.setToolTip(_('top_bar.save'))


def toppanel_save_button_clicked(self):
    """Function to call when save button on subtitles list panel is clicked"""
    session.set_unsaved(False)
    actual_subtitle_file = False
    subtitle_format = modules_utils.get_subtitle_format(session.SUBTITLE['filepath'])
    if subtitle_format:
        actual_subtitle_file = session.SUBTITLE['filepath']
    else:
        subtitle_format = session.CONFIG['default_values'].get('subtitle_format', 'USF')

    if not actual_subtitle_file:
        suggested_path = os.path.dirname(session.VIDEO['filepath'])
        suggested_filename = os.path.basename(session.VIDEO['filepath']).rsplit('.', 1)[0] + '.' + session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[subtitle_format]['extensions'][0]

        session.SUBTITLE['filepath'] = os.path.join(suggested_path, suggested_filename)

    if self.titleBar_left_save_button.key_modifiers:
        if Qt.ShiftModifier in self.titleBar_left_save_button.key_modifiers:
            filedialog_title = 'Save subtitle as'
        if Qt.AltModifier in self.titleBar_left_save_button.key_modifiers:
            filedialog_title = 'Save a copy of the subtitle as'
        if Qt.ControlModifier in self.titleBar_left_save_button.key_modifiers:
            filedialog_title = 'Export as'

        supported_subtitle_files = ''
        for exttype in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS:
            supported_subtitle_files += session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[exttype]['description'] + ' ({})'.format(" ".join(["*.{}".format(fo) for fo in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[exttype]['extensions']])) + ';;'

        filedialog = QFileDialog.getSaveFileName(parent=self, caption=filedialog_title, dir=os.path.dirname(session.SUBTITLE['filepath']), filter=supported_subtitle_files)

        if filedialog[0] and filedialog[1]:
            filepath = filedialog[0]
            selected_extensions = filedialog[1].split('(', 1)[-1].split(')', 1)[0].replace('*.', '').split(' ')
            if not filepath.rsplit('.', 1)[-1].lower() in selected_extensions:
                selected_extension = selected_extensions[0]
                filepath += f'.{selected_extension}'
            else:
                selected_extension = filepath.rsplit('.', 1)[-1].lower()
            selected_format = modules_utils.get_format_from_extension(selected_extension)

            # if Qt.ShiftModifier in self.titleBar_left_save_button.key_modifiers:
            #     session.SUBTITLE['filepath'] = filepath
            #     session.CONFIG['recent_files'][session.SUBTITLE['filepath']] = {
            #         'last_opened': datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
            #         'video_filepath': session.VIDEO['filepath']
            #     }
            #     file_io.save_file(session.SUBTITLE['filepath'], selected_format, session.CONFIG['selected_language'])
            #     if session.CONFIG.get('default_values', {}).get('save_automatic_copy', False) and not subtitle_format == session.CONFIG.get('default_values', {}).get('subtitle_format', 'USF'):
            #         file_io.save_file(session.SUBTITLE['filepath'].rsplit('.', 1)[0] + '.{}'.format(session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[session.CONFIG.get('default_values', {}).get('subtitle_format', 'USF')]['extensions'][0]), session.CONFIG.get('default_values', {}).get('subtitle_format', 'USF'), session.CONFIG['selected_language'])

            # if Qt.AltModifier in self.titleBar_left_save_button.key_modifiers:
            #     file_io.save_file(filepath, selected_format, session.CONFIG['selected_language'])

            # if Qt.ControlModifier in self.titleBar_left_save_button.key_modifiers:
            file_io.save_file(filepath, selected_format, session.CONFIG['selected_language'])

    if session.SUBTITLE['filepath']:
        file_io.save_file(session.SUBTITLE['filepath'], subtitle_format, session.CONFIG['selected_language'])
        if session.CONFIG['save_automatic_copy'] and not subtitle_format == session.CONFIG.get('automatic_copy_format', 'USF'):
            file_io.save_file(session.SUBTITLE['filepath'].rsplit('.', 1)[0] + '.{}'.format(session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[session.CONFIG.get('automatic_copy_format', 'USF')]['extensions'][0]), session.CONFIG.get('automatic_copy_format', 'USF'), session.CONFIG['selected_language'])

    # update(self)
