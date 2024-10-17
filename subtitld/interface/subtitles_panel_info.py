import os
import datetime

from PySide6.QtWidgets import QHBoxLayout, QLayout, QPushButton, QLabel, QSizePolicy, QProgressBar, QMessageBox, QFileDialog
from PySide6.QtCore import Qt

from subtitld.interface.translation import _
from subtitld.modules import utils, session, file_io


def load(self):
    self.subtitles_panel_widget_top_bar = QHBoxLayout()
    self.subtitles_panel_widget_top_bar.setSpacing(8)
    self.subtitles_panel_widget_top_bar.setContentsMargins(0, 0, 0, 0)

    self.toppanel_format_label = QLabel()
    self.toppanel_format_label.setObjectName('toppanel_format_label')
    self.toppanel_format_label.setProperty('class', 'unsaved')
    self.toppanel_format_label.setLayout(QHBoxLayout())
    # self.toppanel_format_label.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum))
    # self.toppanel_format_label.setMinimumHeight(40)
    self.toppanel_format_label.layout().setSpacing(8)
    self.toppanel_format_label.layout().setContentsMargins(20, 5, 5, 5)
    self.toppanel_format_label.layout().setSizeConstraint(QLayout.SetMinAndMaxSize)

    self.toppanel_format_label_text = QLabel()
    self.toppanel_format_label_text.setObjectName('toppanel_format_label_text')
    self.toppanel_format_label_text.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.toppanel_format_label.layout().addWidget(self.toppanel_format_label_text, 0)

    class toppanel_save_button(QPushButton):
        def __init__(widget, parent=None):
            super().__init__(parent)
            widget.key_modifiers = []

        def keyPressEvent(widget, event):
            widget.key_modifiers = event.modifiers()
            event.accept()

        def keyReleaseEvent(widget, event):
            widget.key_modifiers = []
            event.accept()

        # def mouseReleaseEvent(widget, event):
        #     toppanel_save_button_clicked(self)
        #     event.accept()

    self.toppanel_save_button = toppanel_save_button()
    self.toppanel_save_button.setObjectName('toppanel_save_button')
    self.toppanel_save_button.clicked.connect(lambda: toppanel_save_button_clicked(self))
    self.toppanel_save_button.setProperty('class', 'subbutton2_dark')
    # self.toppanel_save_button.setFixedSize(QSize(48, 48))
    self.toppanel_save_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.toppanel_format_label.layout().addWidget(self.toppanel_save_button, 0)

    self.subtitles_panel_widget_top_bar.addWidget(self.toppanel_format_label, 0)

    class toppanel_subtitle_file_info_label(QLabel):
        def enterEvent(widget, event):
            self.toppanel_open_button.setVisible(True)
            event.accept()

        def leaveEvent(widget, event):
            self.toppanel_open_button.setVisible(False)
            event.accept()

    self.toppanel_subtitle_file_info_label = toppanel_subtitle_file_info_label()
    self.toppanel_subtitle_file_info_label.setLayout(QHBoxLayout(self.toppanel_subtitle_file_info_label))
    self.toppanel_subtitle_file_info_label.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum))
    self.toppanel_subtitle_file_info_label.layout().setContentsMargins(0, 0, 0, 0)
    self.toppanel_subtitle_file_info_label.setObjectName('toppanel_subtitle_file_info_label')

    self.toppanel_open_button = QPushButton()
    self.toppanel_open_button.setObjectName('toppanel_open_button')
    self.toppanel_open_button.setProperty('class', 'subbutton2_dark')
    self.toppanel_open_button.clicked.connect(lambda: toppanel_open_button_clicked(self))
    # self.toppanel_open_button.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum))
    # self.toppanel_open_button.setProperty('class', 'button')
    self.toppanel_open_button.setVisible(False)
    self.toppanel_subtitle_file_info_label.layout().addWidget(self.toppanel_open_button, 0, Qt.AlignRight)

    self.subtitles_panel_widget_top_bar.addWidget(self.toppanel_subtitle_file_info_label, 1)

    self.toppanel_subtitle_file_progress_bar = QProgressBar()
    self.toppanel_subtitle_file_progress_bar.setObjectName('toppanel_subtitle_file_progress_bar')
    self.toppanel_subtitle_file_progress_bar.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
    self.toppanel_subtitle_file_progress_bar.setValue(40)
    self.toppanel_subtitle_file_progress_bar.setAlignment(Qt.AlignCenter)
    self.toppanel_subtitle_file_progress_bar.setVisible(False)
    self.subtitles_panel_widget_top_bar.addWidget(self.toppanel_subtitle_file_progress_bar, 1)

    self.subtitles_panel_widget_vbox.layout().addLayout(self.subtitles_panel_widget_top_bar)


def update(self):
    # self.toppanel_format_label.setObjectName('toppanel_format_label')
    self.toppanel_format_label.setProperty('class', 'unsaved' if session.CONFIG['unsaved'] else 'saved')
    self.toppanel_format_label.setStyleSheet(self.toppanel_format_label.styleSheet())
    self.toppanel_format_label_text.setText(utils.get_subtitle_format(session.SUBTITLE['subtitle_filepath']) or session.CONFIG['default_values'].get('subtitle_format', 'USF'))

    text = 'Actual video does not have saved subtitle file.'
    if session.SUBTITLE['subtitle_filepath']:
        text = '<b><small>' + 'Actual project:'.upper() + '</small></b><br><big>' + os.path.basename(session.SUBTITLE['subtitle_filepath']) + '</big>'
    self.toppanel_subtitle_file_info_label.setText(text)



def toppanel_open_button_clicked(self):
    """Function to call when open button on subtitles list panel is clicked"""
    if session.CONFIG['unsaved']:
        save_message_box = QMessageBox(self)

        save_message_box.setWindowTitle('Unsaved changes')
        save_message_box.setText('Do you want to save the changes you made on the subtitles?')

        save_message_box.addButton('Save', QMessageBox.AcceptRole)
        save_message_box.addButton("Don't save", QMessageBox.RejectRole)
        ret = save_message_box.exec_()

        if ret == QMessageBox.AcceptRole:
            toppanel_save_button_clicked(self)

    file_io.open_filepath(self)
    update(self)


def toppanel_save_button_clicked(self):
    """Function to call when save button on subtitles list panel is clicked"""

    actual_subtitle_file = False
    subtitle_format = utils.get_subtitle_format(session.SUBTITLE['subtitle_filepath'])
    if subtitle_format:
        actual_subtitle_file = session.SUBTITLE['subtitle_filepath']
    else:
        subtitle_format = session.CONFIG['default_values'].get('subtitle_format', 'USF')

    if not actual_subtitle_file:
        suggested_path = os.path.dirname(session.VIDEO['filepath'])
        suggested_filename = os.path.basename(session.VIDEO['filepath']).rsplit('.', 1)[0] + '.' + session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[subtitle_format]['extensions'][0]

        session.SUBTITLE['subtitle_filepath'] = os.path.join(suggested_path, suggested_filename)

    if self.toppanel_save_button.key_modifiers:
        if Qt.ShiftModifier in self.toppanel_save_button.key_modifiers:
            filedialog_title = 'Save subtitle as'
        if Qt.AltModifier in self.toppanel_save_button.key_modifiers:
            filedialog_title = 'Save a copy of the subtitle as'
        if Qt.ControlModifier in self.toppanel_save_button.key_modifiers:
            filedialog_title = 'Export as'

        supported_subtitle_files = ''
        for exttype in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS:
            supported_subtitle_files += session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[exttype]['description'] + ' ({})'.format(" ".join(["*.{}".format(fo) for fo in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[exttype]['extensions']])) + ';;'

        filedialog = QFileDialog.getSaveFileName(parent=self, caption=filedialog_title, dir=os.path.dirname(session.SUBTITLE['subtitle_filepath']), filter=supported_subtitle_files)

        if filedialog[0] and filedialog[1]:
            filepath = filedialog[0]
            selected_extensions = filedialog[1].split('(', 1)[-1].split(')', 1)[0].replace('*.', '').split(' ')
            if not filepath.rsplit('.', 1)[-1].lower() in selected_extensions:
                selected_extension = selected_extensions[0]
                filepath += f'.{selected_extension}'
            else:
                selected_extension = filepath.rsplit('.', 1)[-1].lower()
            selected_format = utils.get_format_from_extension(selected_extension)

            if Qt.ShiftModifier in self.toppanel_save_button.key_modifiers:
                session.SUBTITLE['subtitle_filepath'] = filepath
                session.CONFIG['recent_files'][session.SUBTITLE['subtitle_filepath']] = {
                    'last_opened': datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
                    'video_filepath': session.VIDEO['filepath']
                }
                file_io.save_file(session.SUBTITLE['subtitle_filepath'], selected_format, session.CONFIG['selected_language'])
                if session.CONFIG['default_values'].get('save_automatic_copy', False) and not subtitle_format == session.CONFIG['default_values'].get('subtitle_format', 'USF'):
                    file_io.save_file(session.SUBTITLE['subtitle_filepath'].rsplit('.', 1)[0] + '.{}'.format(session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[session.CONFIG['default_values'].get('subtitle_format', 'USF')]['extensions'][0]), session.CONFIG['default_values'].get('subtitle_format', 'USF'), session.CONFIG['selected_language'])
                session.CONFIG['unsaved'] = False

            if Qt.AltModifier in self.toppanel_save_button.key_modifiers:
                file_io.save_file(filepath, selected_format, session.CONFIG['selected_language'])

            if Qt.ControlModifier in self.toppanel_save_button.key_modifiers:
                file_io.save_file(filepath, selected_format, session.CONFIG['selected_language'])

    elif session.SUBTITLE['subtitle_filepath']:
        file_io.save_file(session.SUBTITLE['subtitle_filepath'], subtitle_format, session.CONFIG['selected_language'])
        if session.CONFIG['default_values'].get('save_automatic_copy', False) and not subtitle_format == session.CONFIG['default_values'].get('subtitle_format', 'USF'):
            file_io.save_file(session.SUBTITLE['subtitle_filepath'].rsplit('.', 1)[0] + '.{}'.format(session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[session.CONFIG['default_values'].get('subtitle_format', 'USF')]['extensions'][0]), session.CONFIG['default_values'].get('subtitle_format', 'USF'), session.CONFIG['selected_language'])
        session.CONFIG['unsaved'] = False

    update(self)
