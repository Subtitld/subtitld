import os
import subprocess

from PySide6.QtWidgets import QVBoxLayout, QWidget, QScrollArea, QFileDialog, QPushButton, QComboBox, QGroupBox, QTabWidget, QGridLayout, QHBoxLayout, QLabel, QSpinBox, QLineEdit, QSizePolicy, QTableWidgetItem, QColorDialog
from PySide6.QtCore import Qt, QThread, Signal, QSize, QMargins
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPixmap, QFontDatabase, QBrush

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import file_io
from subtitld.modules import utils
from subtitld.modules import shortcuts
from subtitld.modules import subtitles


class ThreadGenerateVideo(QThread):
    """Thread to generate burned video"""
    response = Signal(str)
    response_preview = Signal(QPixmap)
    original_file = ''
    final_file = ''
    is_preview = False
    is_burned = True
    burnedin_options = {}

    def run(self):
        """Run function of thread to generate burned video"""
        if self.original_file and (self.final_file or self.is_preview):
            commands = [
                session.FFMPEG_EXECUTABLE,
                '-i', self.original_file,
                '-y',
            ]

            if self.is_burned:
                vf_string = 'subtitles=filename=' + os.path.join(session.path_tmp, 'subtitle.srt').replace('\\', '\\\\\\\\').replace(':', '\\\:') + ":force_style='"
                vf_string += 'FontName=' + self.burnedin_options.get('ffmpeg_font_family', 'Ubuntu') + ','
                vf_string += 'FontSize=' + str(self.burnedin_options.get('ffmpeg_font_size', 40)) + ','
                if self.burnedin_options.get('ffmpeg_shadow_enabled', True):
                    vf_string += 'Shadow=' + str(self.burnedin_options.get('ffmpeg_shadow_distance', 2)) + ','
                if self.burnedin_options.get('ffmpeg_outline_enabled', True):
                    vf_string += 'Outline=' + str(self.burnedin_options.get('ffmpeg_outline_size', 2)) + ','
                vf_string += 'MarginV=' + str(self.burnedin_options.get('ffmpeg_margin_bottom', 0)) + ','
                vf_string += 'MarginL=' + str(self.burnedin_options.get('ffmpeg_margin_left', 0)) + ','
                vf_string += 'MarginR=' + str(self.burnedin_options.get('ffmpeg_margin_right', 0)) + ','
                pcolor = self.burnedin_options.get('ffmpeg_color', '#ffffffff').replace('#', '&H')
                pcolor = pcolor[:2] + pcolor[-2:] + pcolor[-4:-2] + pcolor[-6:-4]
                vf_string += 'PrimaryColour=' + pcolor + "'"

                commands += [
                    '-vf',
                    vf_string
                ]

            if self.burnedin_options.get('ffmpeg_custom_command', ''):
                commands += self.burnedin_options.get('ffmpeg_custom_command', '').split(' ')

            if self.is_preview:
                commands += [
                    '-ss', str(self.is_preview),
                    '-frames:v', '1',
                    os.path.join(session.path_tmp, 'preview.png')
                ]
            else:
                commands += [
                    self.final_file
                ]

            proc = subprocess.Popen(
                commands,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                startupinfo=session.STARTUPINFO
            )

            number_of_steps = 0.001
            current_step = 0.0
            while proc.poll() is None:
                # for output in proc.stdout.read().decode().split('\n'):
                output = proc.stdout.readline()
                if 'Duration: ' in output:
                    duration = int(utils.convert_ffmpeg_timecode_to_seconds(output.split('Duration: ', 1)[1].split(',', 1)[0]))
                    if duration > number_of_steps:
                        number_of_steps = duration
                if output[:6] == 'frame=':
                    current_step = int(utils.convert_ffmpeg_timecode_to_seconds(output.split('time=', 1)[1].split(' ', 1)[0]))

                if not self.is_preview:
                    self.response.emit(str(current_step) + '|' + str(number_of_steps))

            if self.is_preview:
                self.response_preview.emit(QPixmap(os.path.join(session.path_tmp, 'preview.png')))
            else:
                self.response.emit('end')



def load(self):
    tab_name = 'export'
    
    left_panel_export_panel = QWidget()
    left_panel_export_panel.setObjectName(f'left_panel_{tab_name}')
    left_panel_export_panel.setProperty('tab_name', tab_name)
    left_panel_export_panel.setLayout(QVBoxLayout())
    left_panel_export_panel.layout().setContentsMargins(10, 10, 10, 10)

    left_panel_export_panel_scroll = QScrollArea()
    left_panel_export_panel_scroll.setObjectName('left_panel_export_panel_scroll')
    left_panel_export_panel_scroll.setWidgetResizable(True)
    left_panel_export_panel_scroll.setFrameShape(QScrollArea.NoFrame)
    left_panel_export_panel.layout().addWidget(left_panel_export_panel_scroll)

    self.left_panel_export_panel_widget = QWidget()
    self.left_panel_export_panel_widget.setObjectName('left_panel_export_panel_widget')
    self.left_panel_export_panel_widget.setLayout(QVBoxLayout())
    self.left_panel_export_panel_widget.layout().setContentsMargins(0, 0, 0, 0)
    left_panel_export_panel_scroll.setWidget(self.left_panel_export_panel_widget)

    self.global_panel_export_tabwidget = QTabWidget()

    self.global_panel_export_text_tabwidget_panel = QWidget()
    self.global_panel_export_text_tabwidget_panel.setLayout(QVBoxLayout())
    self.global_panel_export_text_tabwidget_panel.layout().setContentsMargins(10, 10, 10, 10)
    self.global_panel_export_text_tabwidget_panel.layout().setSpacing(20)

    self.global_panel_export_content_txt_panel_export_button = QPushButton()
    self.global_panel_export_content_txt_panel_export_button.setProperty('class', 'button')
    self.global_panel_export_content_txt_panel_export_button.clicked.connect(lambda: global_subtitlesvideo_export_button_clicked(self))
    self.global_panel_export_text_tabwidget_panel.layout().addWidget(self.global_panel_export_content_txt_panel_export_button, 0)

    self.global_panel_export_tabwidget.addTab(self.global_panel_export_text_tabwidget_panel, 'Text')

    self.global_panel_export_video_tabwidget_panel = QWidget()
    self.global_panel_export_video_tabwidget_panel.setLayout(QVBoxLayout())
    self.global_panel_export_video_tabwidget_panel.layout().setContentsMargins(10, 10, 10, 10)
    self.global_panel_export_video_tabwidget_panel.layout().setSpacing(20)

    self.global_panel_export_video_tabwidget = QTabWidget()

    class global_panel_export_video_ffmpeg_panel(QWidget):
        """The main window (QWidget) class"""
        def __init__(widget):
            super().__init__()

        def showEvent(widget, event):
            update_preview(self)
            return event

    self.global_panel_export_video_ffmpeg_panel = global_panel_export_video_ffmpeg_panel()
    self.global_panel_export_video_ffmpeg_panel.setLayout(QGridLayout())
    self.global_panel_export_video_ffmpeg_panel.layout().setContentsMargins(10, 10, 10, 10)
    self.global_panel_export_video_ffmpeg_panel.layout().setSpacing(20)

    self.global_panel_export_video_ffmpeg_left_panel = QWidget()
    self.global_panel_export_video_ffmpeg_left_panel.setLayout(QVBoxLayout())
    self.global_panel_export_video_ffmpeg_left_panel.layout().setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_left_panel.layout().setSpacing(20)

    self.global_panel_export_video_ffmpeg_font_group = QGroupBox('Font')
    self.global_panel_export_video_ffmpeg_font_group.setLayout(QHBoxLayout())
    self.global_panel_export_video_ffmpeg_font_group.layout().setContentsMargins(10, 10, 10, 10)
    self.global_panel_export_video_ffmpeg_font_group.layout().setSpacing(20)

    self.global_panel_export_video_ffmpeg_fontsize_line = QVBoxLayout()
    self.global_panel_export_video_ffmpeg_fontsize_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_fontsize_line.setSpacing(2)

    self.global_panel_export_video_ffmpeg_fontsize_label = QLabel()
    self.global_panel_export_video_ffmpeg_fontsize_label.setProperty('class', 'widget_label')
    self.global_panel_export_video_ffmpeg_fontsize_line.addWidget(self.global_panel_export_video_ffmpeg_fontsize_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_fontsize_line_2 = QHBoxLayout()
    self.global_panel_export_video_ffmpeg_fontsize_line_2.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_fontsize_line_2.setSpacing(5)

    self.global_panel_export_video_ffmpeg_fontsize_spinbox = QSpinBox()
    self.global_panel_export_video_ffmpeg_fontsize_spinbox.setMinimum(1)
    self.global_panel_export_video_ffmpeg_fontsize_spinbox.setMaximum(999)
    self.global_panel_export_video_ffmpeg_fontsize_spinbox.valueChanged.connect(lambda: global_panel_export_video_ffmpeg_fontsize_spinbox_changed(self))
    self.global_panel_export_video_ffmpeg_fontsize_line_2.addWidget(self.global_panel_export_video_ffmpeg_fontsize_spinbox, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_fontsize_seconds_label = QLabel()
    self.global_panel_export_video_ffmpeg_fontsize_seconds_label.setProperty('class', 'units_label')
    self.global_panel_export_video_ffmpeg_fontsize_line_2.addWidget(self.global_panel_export_video_ffmpeg_fontsize_seconds_label, 0, Qt.AlignLeft)
    # self.global_panel_export_video_ffmpeg_fontsize_line_2.addStretch()

    self.global_panel_export_video_ffmpeg_fontsize_line.addLayout(self.global_panel_export_video_ffmpeg_fontsize_line_2)

    self.global_panel_export_video_ffmpeg_font_group.layout().addLayout(self.global_panel_export_video_ffmpeg_fontsize_line)

    self.global_panel_export_video_ffmpeg_fontfamily_line = QVBoxLayout()
    self.global_panel_export_video_ffmpeg_fontfamily_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_fontfamily_line.setSpacing(2)

    self.global_panel_export_video_ffmpeg_fontfamily_label = QLabel()
    self.global_panel_export_video_ffmpeg_fontfamily_label.setProperty('class', 'widget_label')
    self.global_panel_export_video_ffmpeg_fontfamily_line.addWidget(self.global_panel_export_video_ffmpeg_fontfamily_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_fontfamily_line_2 = QHBoxLayout()
    self.global_panel_export_video_ffmpeg_fontfamily_line_2.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_fontfamily_line_2.setSpacing(5)

    fonts = QFontDatabase().families()
    self.global_panel_export_video_ffmpeg_fontfamily_combobox = QComboBox()
    self.global_panel_export_video_ffmpeg_fontfamily_combobox.addItems(fonts)
    self.global_panel_export_video_ffmpeg_fontfamily_combobox.activated.connect(lambda: global_panel_export_video_ffmpeg_fontfamily_combobox_changed(self))
    self.global_panel_export_video_ffmpeg_fontfamily_line_2.addWidget(self.global_panel_export_video_ffmpeg_fontfamily_combobox, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_fontfamily_line.addLayout(self.global_panel_export_video_ffmpeg_fontfamily_line_2)

    self.global_panel_export_video_ffmpeg_font_group.layout().addLayout(self.global_panel_export_video_ffmpeg_fontfamily_line)

    self.global_panel_export_video_ffmpeg_color_vbox = QVBoxLayout()
    self.global_panel_export_video_ffmpeg_color_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_color_vbox.setSpacing(2)

    self.global_panel_export_video_ffmpeg_color_label = QLabel()
    self.global_panel_export_video_ffmpeg_color_label.setProperty('class', 'widget_label')
    self.global_panel_export_video_ffmpeg_color_vbox.addWidget(self.global_panel_export_video_ffmpeg_color_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_color_button = QPushButton()
    self.global_panel_export_video_ffmpeg_color_button.setProperty('class', 'color_pick_button')
    self.global_panel_export_video_ffmpeg_color_button.setFixedWidth(80)
    self.global_panel_export_video_ffmpeg_color_button.clicked.connect(lambda: global_panel_export_video_ffmpeg_color_button_clicked(self))

    self.global_panel_export_video_ffmpeg_color_vbox.addWidget(self.global_panel_export_video_ffmpeg_color_button, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_font_group.layout().addLayout(self.global_panel_export_video_ffmpeg_color_vbox)

    self.global_panel_export_video_ffmpeg_font_group.layout().addStretch()

    self.global_panel_export_video_ffmpeg_left_panel.layout().addWidget(self.global_panel_export_video_ffmpeg_font_group)

    self.global_panel_export_video_ffmpeg_outline_group = QGroupBox()
    self.global_panel_export_video_ffmpeg_outline_group.setCheckable(True)
    self.global_panel_export_video_ffmpeg_outline_group.setLayout(QHBoxLayout())
    self.global_panel_export_video_ffmpeg_outline_group.toggled.connect(lambda: global_panel_export_video_ffmpeg_outline_group_toggled(self))
    self.global_panel_export_video_ffmpeg_outline_group.layout().setContentsMargins(10, 10, 10, 10)
    self.global_panel_export_video_ffmpeg_outline_group.layout().setSpacing(20)

    self.global_panel_export_video_ffmpeg_outline_vbox = QVBoxLayout()
    self.global_panel_export_video_ffmpeg_outline_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_outline_vbox.setSpacing(2)

    self.global_panel_export_video_ffmpeg_outline_label = QLabel()
    self.global_panel_export_video_ffmpeg_outline_label.setProperty('class', 'widget_label')
    self.global_panel_export_video_ffmpeg_outline_vbox.addWidget(self.global_panel_export_video_ffmpeg_outline_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_outline_line = QHBoxLayout()
    self.global_panel_export_video_ffmpeg_outline_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_outline_line.setSpacing(5)

    self.global_panel_export_video_ffmpeg_outline_value = QSpinBox()
    self.global_panel_export_video_ffmpeg_outline_value.setMinimum(0)
    self.global_panel_export_video_ffmpeg_outline_value.setMaximum(99999)
    self.global_panel_export_video_ffmpeg_outline_value.valueChanged.connect(lambda: global_panel_export_video_ffmpeg_outline_value_changed(self))
    self.global_panel_export_video_ffmpeg_outline_line.addWidget(self.global_panel_export_video_ffmpeg_outline_value, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_outline_value_pixels_label = QLabel()
    self.global_panel_export_video_ffmpeg_outline_value_pixels_label.setProperty('class', 'units_label')
    self.global_panel_export_video_ffmpeg_outline_line.addWidget(self.global_panel_export_video_ffmpeg_outline_value_pixels_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_outline_vbox.addLayout(self.global_panel_export_video_ffmpeg_outline_line)

    self.global_panel_export_video_ffmpeg_outline_group.layout().addLayout(self.global_panel_export_video_ffmpeg_outline_vbox)

    self.global_panel_export_video_ffmpeg_outline_group.layout().addStretch()

    self.global_panel_export_video_ffmpeg_left_panel.layout().addWidget(self.global_panel_export_video_ffmpeg_outline_group)

    self.global_panel_export_video_ffmpeg_shadow_group = QGroupBox()
    self.global_panel_export_video_ffmpeg_shadow_group.setCheckable(True)
    self.global_panel_export_video_ffmpeg_shadow_group.setLayout(QHBoxLayout())
    self.global_panel_export_video_ffmpeg_shadow_group.toggled.connect(lambda: global_panel_export_video_ffmpeg_shadow_group_toggled(self))
    self.global_panel_export_video_ffmpeg_shadow_group.layout().setContentsMargins(10, 10, 10, 10)
    self.global_panel_export_video_ffmpeg_shadow_group.layout().setSpacing(20)

    self.global_panel_export_video_ffmpeg_shadow_vbox = QVBoxLayout()
    self.global_panel_export_video_ffmpeg_shadow_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_shadow_vbox.setSpacing(2)

    self.global_panel_export_video_ffmpeg_shadow_label = QLabel()
    self.global_panel_export_video_ffmpeg_shadow_label.setProperty('class', 'widget_label')
    self.global_panel_export_video_ffmpeg_shadow_vbox.addWidget(self.global_panel_export_video_ffmpeg_shadow_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_shadow_line = QHBoxLayout()
    self.global_panel_export_video_ffmpeg_shadow_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_shadow_line.setSpacing(5)

    self.global_panel_export_video_ffmpeg_shadow_distance = QSpinBox()
    self.global_panel_export_video_ffmpeg_shadow_distance.setMinimum(-99999)
    self.global_panel_export_video_ffmpeg_shadow_distance.setMaximum(99999)
    self.global_panel_export_video_ffmpeg_shadow_distance.valueChanged.connect(lambda: global_panel_export_video_ffmpeg_shadow_distance_changed(self))
    self.global_panel_export_video_ffmpeg_shadow_line.addWidget(self.global_panel_export_video_ffmpeg_shadow_distance, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_shadow_distance_pixels_label = QLabel()
    self.global_panel_export_video_ffmpeg_shadow_distance_pixels_label.setProperty('class', 'units_label')
    self.global_panel_export_video_ffmpeg_shadow_line.addWidget(self.global_panel_export_video_ffmpeg_shadow_distance_pixels_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_shadow_vbox.addLayout(self.global_panel_export_video_ffmpeg_shadow_line)

    self.global_panel_export_video_ffmpeg_shadow_group.layout().addLayout(self.global_panel_export_video_ffmpeg_shadow_vbox)

    self.global_panel_export_video_ffmpeg_shadow_group.layout().addStretch()

    self.global_panel_export_video_ffmpeg_left_panel.layout().addWidget(self.global_panel_export_video_ffmpeg_shadow_group)

    self.global_panel_export_video_ffmpeg_margins_group = QGroupBox()
    self.global_panel_export_video_ffmpeg_margins_group.setLayout(QGridLayout())
    self.global_panel_export_video_ffmpeg_margins_group.layout().setContentsMargins(10, 10, 10, 10)
    self.global_panel_export_video_ffmpeg_margins_group.layout().setSpacing(20)

    self.global_panel_export_video_ffmpeg_margins_left_vbox = QVBoxLayout()
    self.global_panel_export_video_ffmpeg_margins_left_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_margins_left_vbox.setSpacing(2)

    self.global_panel_export_video_ffmpeg_margins_left_label = QLabel()
    self.global_panel_export_video_ffmpeg_margins_left_label.setProperty('class', 'widget_label')
    self.global_panel_export_video_ffmpeg_margins_left_vbox.addWidget(self.global_panel_export_video_ffmpeg_margins_left_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_margins_left_line = QHBoxLayout()
    self.global_panel_export_video_ffmpeg_margins_left_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_margins_left_line.setSpacing(5)

    self.global_panel_export_video_ffmpeg_margins_left_distance = QSpinBox()
    self.global_panel_export_video_ffmpeg_margins_left_distance.setMinimum(-99999)
    self.global_panel_export_video_ffmpeg_margins_left_distance.setMaximum(99999)
    self.global_panel_export_video_ffmpeg_margins_left_distance.valueChanged.connect(lambda: global_panel_export_video_ffmpeg_margins_left_distance_changed(self))
    self.global_panel_export_video_ffmpeg_margins_left_line.addWidget(self.global_panel_export_video_ffmpeg_margins_left_distance, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_margins_left_distance_pixels_label = QLabel()
    self.global_panel_export_video_ffmpeg_margins_left_distance_pixels_label.setProperty('class', 'units_label')
    self.global_panel_export_video_ffmpeg_margins_left_line.addWidget(self.global_panel_export_video_ffmpeg_margins_left_distance_pixels_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_margins_left_line.addStretch()

    self.global_panel_export_video_ffmpeg_margins_left_vbox.addLayout(self.global_panel_export_video_ffmpeg_margins_left_line)

    self.global_panel_export_video_ffmpeg_margins_group.layout().addLayout(self.global_panel_export_video_ffmpeg_margins_left_vbox, 1, 1, 1, 1)

    self.global_panel_export_video_ffmpeg_margins_bottom_vbox = QVBoxLayout()
    self.global_panel_export_video_ffmpeg_margins_bottom_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_margins_bottom_vbox.setSpacing(2)

    self.global_panel_export_video_ffmpeg_margins_bottom_label = QLabel()
    self.global_panel_export_video_ffmpeg_margins_bottom_label.setProperty('class', 'widget_label')
    self.global_panel_export_video_ffmpeg_margins_bottom_vbox.addWidget(self.global_panel_export_video_ffmpeg_margins_bottom_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_margins_bottom_line = QHBoxLayout()
    self.global_panel_export_video_ffmpeg_margins_bottom_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_margins_bottom_line.setSpacing(5)

    self.global_panel_export_video_ffmpeg_margins_bottom_distance = QSpinBox()
    self.global_panel_export_video_ffmpeg_margins_bottom_distance.setMinimum(-99999)
    self.global_panel_export_video_ffmpeg_margins_bottom_distance.setMaximum(99999)
    self.global_panel_export_video_ffmpeg_margins_bottom_distance.valueChanged.connect(lambda: global_panel_export_video_ffmpeg_margins_bottom_distance_changed(self))
    self.global_panel_export_video_ffmpeg_margins_bottom_line.addWidget(self.global_panel_export_video_ffmpeg_margins_bottom_distance, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_margins_bottom_distance_pixels_label = QLabel()
    self.global_panel_export_video_ffmpeg_margins_bottom_distance_pixels_label.setProperty('class', 'units_label')
    self.global_panel_export_video_ffmpeg_margins_bottom_line.addWidget(self.global_panel_export_video_ffmpeg_margins_bottom_distance_pixels_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_margins_bottom_line.addStretch()

    self.global_panel_export_video_ffmpeg_margins_bottom_vbox.addLayout(self.global_panel_export_video_ffmpeg_margins_bottom_line)

    self.global_panel_export_video_ffmpeg_margins_group.layout().addLayout(self.global_panel_export_video_ffmpeg_margins_bottom_vbox, 2, 2, 1, 1)

    self.global_panel_export_video_ffmpeg_margins_right_vbox = QVBoxLayout()
    self.global_panel_export_video_ffmpeg_margins_right_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_margins_right_vbox.setSpacing(2)

    self.global_panel_export_video_ffmpeg_margins_right_label = QLabel()
    self.global_panel_export_video_ffmpeg_margins_right_label.setProperty('class', 'widget_label')
    self.global_panel_export_video_ffmpeg_margins_right_vbox.addWidget(self.global_panel_export_video_ffmpeg_margins_right_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_margins_right_line = QHBoxLayout()
    self.global_panel_export_video_ffmpeg_margins_right_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_margins_right_line.setSpacing(5)

    self.global_panel_export_video_ffmpeg_margins_right_distance = QSpinBox()
    self.global_panel_export_video_ffmpeg_margins_right_distance.setMinimum(-99999)
    self.global_panel_export_video_ffmpeg_margins_right_distance.setMaximum(99999)
    self.global_panel_export_video_ffmpeg_margins_right_distance.valueChanged.connect(lambda: global_panel_export_video_ffmpeg_margins_right_distance_changed(self))
    self.global_panel_export_video_ffmpeg_margins_right_line.addWidget(self.global_panel_export_video_ffmpeg_margins_right_distance, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_margins_right_distance_pixels_label = QLabel()
    self.global_panel_export_video_ffmpeg_margins_right_distance_pixels_label.setProperty('class', 'units_label')
    self.global_panel_export_video_ffmpeg_margins_right_line.addWidget(self.global_panel_export_video_ffmpeg_margins_right_distance_pixels_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_margins_right_line.addStretch()

    self.global_panel_export_video_ffmpeg_margins_right_vbox.addLayout(self.global_panel_export_video_ffmpeg_margins_right_line)

    self.global_panel_export_video_ffmpeg_margins_group.layout().addLayout(self.global_panel_export_video_ffmpeg_margins_right_vbox, 1, 3, 1, 1)

    self.global_panel_export_video_ffmpeg_left_panel.layout().addWidget(self.global_panel_export_video_ffmpeg_margins_group)

    self.global_panel_export_video_ffmpeg_final_line = QHBoxLayout()
    self.global_panel_export_video_ffmpeg_final_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_final_line.setSpacing(20)

    self.global_panel_export_video_ffmpeg_command_line = QVBoxLayout()
    self.global_panel_export_video_ffmpeg_command_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_command_line.setSpacing(5)

    self.global_panel_export_video_ffmpeg_margins_right_label = QLabel()
    self.global_panel_export_video_ffmpeg_margins_right_label.setProperty('class', 'widget_label')
    self.global_panel_export_video_ffmpeg_command_line.addWidget(self.global_panel_export_video_ffmpeg_margins_right_label, 0, Qt.AlignLeft)

    self.global_panel_export_video_ffmpeg_command_qlineedit = QLineEdit()
    self.global_panel_export_video_ffmpeg_command_qlineedit.textEdited.connect(lambda: global_panel_export_video_ffmpeg_command_qlineedit_textedited(self))
    self.global_panel_export_video_ffmpeg_command_line.addWidget(self.global_panel_export_video_ffmpeg_command_qlineedit)

    self.global_panel_export_video_ffmpeg_final_line.addLayout(self.global_panel_export_video_ffmpeg_command_line)

    self.global_panel_export_video_ffmpeg_export_button = QPushButton()
    self.global_panel_export_video_ffmpeg_export_button.setProperty('class', 'button_dark')
    self.global_panel_export_video_ffmpeg_export_button.clicked.connect(lambda: global_panel_export_video_ffmpeg_export_button_clicked(self))
    self.global_panel_export_video_ffmpeg_export_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.global_panel_export_video_ffmpeg_final_line.addWidget(self.global_panel_export_video_ffmpeg_export_button)

    self.global_panel_export_video_ffmpeg_left_panel.layout().addLayout(self.global_panel_export_video_ffmpeg_final_line)

    self.global_panel_export_video_ffmpeg_left_panel.layout().addStretch()

    self.global_panel_export_video_ffmpeg_panel.layout().addWidget(self.global_panel_export_video_ffmpeg_left_panel, 1, 1, 1, 2)

    self.global_panel_export_video_ffmpeg_preview_panel = QWidget()
    self.global_panel_export_video_ffmpeg_preview_panel.setLayout(QVBoxLayout())
    self.global_panel_export_video_ffmpeg_preview_panel.layout().setContentsMargins(0, 0, 0, 0)
    self.global_panel_export_video_ffmpeg_preview_panel.layout().setSpacing(20)

    self.global_panel_export_video_ffmpeg_preview_label = QLabel()
    self.global_panel_export_video_ffmpeg_preview_label.setProperty('class', 'widget_label')
    self.global_panel_export_video_ffmpeg_preview_panel.layout().addWidget(self.global_panel_export_video_ffmpeg_preview_label, 0)

    self.global_panel_export_video_ffmpeg_preview_image = QLabel()
    self.global_panel_export_video_ffmpeg_preview_image.setProperty('class', 'widget_label')
    self.global_panel_export_video_ffmpeg_preview_image.setAlignment(Qt.AlignTop)
    self.global_panel_export_video_ffmpeg_preview_panel.layout().addWidget(self.global_panel_export_video_ffmpeg_preview_image, 1)

    self.global_panel_export_video_ffmpeg_preview_panel.layout().addStretch()

    self.global_panel_export_video_ffmpeg_panel.layout().addWidget(self.global_panel_export_video_ffmpeg_preview_panel, 1, 3, 1, 1)

    self.global_panel_export_video_tabwidget.addTab(self.global_panel_export_video_ffmpeg_panel, 'FFMPEG')

    self.global_panel_export_video_tabwidget_panel.layout().addWidget(self.global_panel_export_video_tabwidget, 0)

    self.global_subtitlesvideo_video_generate_transparent_video_button = QPushButton()
    self.global_subtitlesvideo_video_generate_transparent_video_button.setProperty('class', 'button_dark')
    self.global_subtitlesvideo_video_generate_transparent_video_button.clicked.connect(lambda: global_subtitlesvideo_video_generate_transparent_video_button_clicked(self))
    self.global_subtitlesvideo_video_generate_transparent_video_button.setVisible(False)
    self.global_panel_export_video_tabwidget_panel.layout().addWidget(self.global_subtitlesvideo_video_generate_transparent_video_button)
    # self.global_subtitlesvideo_video_generate_transparent_video_button.setVisible(False)

    def thread_generated_burned_video_ended(response):
        print(response)
        # if '|' in response:
        #     subtitles_panel.update_processing_status(self, show_widgets=True, value=int((float(response.split('|')[0]) / float(response.split('|')[1])) * 100))
        # elif 'end' in response:
        #     subtitles_panel.update_processing_status(self, show_widgets=False, value=0)
        #     self.global_panel_export_video_ffmpeg_export_button.setEnabled(True)

    def thread_generated_burned_video_preview_response(response):
        self.global_panel_export_video_ffmpeg_preview_image.setPixmap(response.scaled(self.global_panel_export_video_ffmpeg_preview_image.width(), self.global_panel_export_video_ffmpeg_preview_image.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    self.thread_generated_burned_video = ThreadGenerateVideo(self)
    self.thread_generated_burned_video.response.connect(thread_generated_burned_video_ended)
    self.thread_generated_burned_video.response_preview.connect(thread_generated_burned_video_preview_response)

    self.global_panel_export_tabwidget.addTab(self.global_panel_export_video_tabwidget_panel, 'Video')

    self.left_panel_export_panel_widget.layout().addWidget(self.global_panel_export_tabwidget)
    
    left_panel_export_panel.update = update

    left_panel.add_panel(self, left_panel_export_panel)

    update(self)

def global_panel_tabwidget_shortkeys_table_update(self):
    """Function to update subtitlesvideo panel shorkeys table"""
    self.global_panel_tabwidget_shortkeys_table.clear()
    self.global_panel_tabwidget_shortkeys_table.setRowCount(len(shortcuts.shortcuts_dict))
    inverted_shortcuts_dict = {value: key for key, value in shortcuts.shortcuts_dict.items()}
    i = 0
    for item in shortcuts.shortcuts_dict:
        item_name = QTableWidgetItem(shortcuts.shortcuts_dict[item])
        self.global_panel_tabwidget_shortkeys_table.setItem(i, 0, item_name)
        item_name = QTableWidgetItem(session.CONFIG['shortcuts'].get(inverted_shortcuts_dict[shortcuts.shortcuts_dict[item]], [''])[0])
        self.global_panel_tabwidget_shortkeys_table.setItem(i, 1, item_name)
        i += 1


def global_panel_export_video_ffmpeg_export_button_clicked(self):
    """Function to generate buned video"""
    suggested_path = os.path.dirname(session.VIDEO['filepath'])
    extformat = os.path.basename(session.VIDEO['filepath']).rsplit('.', 1)[1]
    save_formats = 'Video file' + ' (.' + extformat + ')'
    suggested_name = os.path.basename(session.VIDEO['filepath']).rsplit('.', 1)[0] + '_subtitled.' + extformat

    generated_video_filepath = QFileDialog.getSaveFileName(parent=self, caption=_('global_panel_export.select_subtitle_file'), dir=os.path.join(suggested_path, suggested_name), filter=save_formats)[0]

    if generated_video_filepath:
        file_io.save_file(os.path.join(session.path_tmp, 'subtitle.srt'), subtitle_format='SRT', language='en')

        self.thread_generated_burned_video.original_file = session.VIDEO['filepath']
        self.thread_generated_burned_video.final_file = generated_video_filepath
        self.thread_generated_burned_video.is_preview = False
        self.thread_generated_burned_video.is_burned = True
        self.thread_generated_burned_video.burnedin_options = session.CONFIG['export']
        self.thread_generated_burned_video.start()

        self.global_panel_export_video_ffmpeg_export_button.setEnabled(False)


def global_subtitlesvideo_video_burn_pcolor_clicked(self):
    """Function to change color"""
    color = QColorDialog().getColor(options=QColorDialog.DontUseNativeDialog)
    if color.isValid():
        self.global_subtitlesvideo_video_burn_pcolor_selected_color = color.name()
    self.global_subtitlesvideo_video_burn_pcolor.setStyleSheet('background-color:' + self.global_subtitlesvideo_video_burn_pcolor_selected_color)
    update_preview(self)

def global_subtitlesvideo_video_generate_transparent_video_button_clicked(self):
    suggested_path = os.path.dirname(session.VIDEO['filepath'])
    extformat = 'mov'  # os.path.basename(session.VIDEO['filepath']).rsplit('.', 1)[1]
    save_formats = 'Video file' + ' (.' + extformat + ')'
    suggested_name = os.path.basename(session.VIDEO['filepath']).rsplit('.', 1)[0] + '_subtitled.' + extformat

    generated_video_filepath = QFileDialog.getSaveFileName(parent=self, caption=_('global_panel_export.select_video_file'), dir=os.path.join(suggested_path, suggested_name), filter=save_formats)[0]

    if generated_video_filepath:
        class layerWidget(QWidget):
            subtitle_text = ''
            font = 'Ubuntu'
            fontsize = 18

            def paintEvent(canvas, event):
                painter = QPainter(canvas)
                painter.setRenderHint(QPainter.Antialiasing)

                if canvas.subtitle_text:
                    font = QFont(canvas.font)
                    font.setPointSize(canvas.fontsize)
                    painter.setFont(font)

                    text_rect = painter.boundingRect(canvas.width() * .08, canvas.height() * .08, canvas.width() * .84, canvas.height() * .84, Qt.AlignBottom | Qt.AlignHCenter | Qt.TextWordWrap, canvas.subtitle_text)

                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QBrush(QColor('#cc000000')))
                    painter.drawRoundedRect(text_rect.marginsAdded(QMargins(10, 10, 10, 10)), 5, 5)

                    painter.setPen(QPen(QColor('#fff')))
                    painter.drawText(text_rect.adjusted(0, 2, 0, 2), Qt.AlignCenter | Qt.TextWordWrap, canvas.subtitle_text)

                painter.end()
                event.accept()

        layer = layerWidget(self)
        layer.setVisible(False)
        layer.setStyleSheet('background: transparent;')
        layer.resize(QSize(session.VIDEO.get('width', 1920), session.VIDEO.get('height', 1920)))
        layer.font = self.global_subtitlesvideo_video_burn_fontname.currentText()
        layer.fontsize = self.global_subtitlesvideo_video_burn_fontsize.value()
        final_text = ''

        i = 0
        last_position = .0

        for segment in session.SUBTITLE['segments']:
            if not last_position + .001 > segment['start'] - .001:
                filename = os.path.join(session.path_tmp, 'empty.png')
                layer.subtitle_text = ''
                layer.grab().save(filename, 'PNG')

                final_text += "file '" + filename + "'\n"
                final_text += 'duration {}\n'.format(segment['start'] - last_position)

            filename = os.path.join(session.path_tmp, '{}.png'.format(i))
            layer.subtitle_text = segment['text']
            layer.grab().save(filename, 'PNG')

            final_text += "file '" + filename + "'\n"
            final_text += 'duration {}\n'.format(segment['end'] - segment['start'])

            last_position = segment['end']
            i += 1
            print(i)

        if not session.SUBTITLE['segments'][-1]['start'] + session.SUBTITLE['segments'][-1]['end'] == session.VIDEO.get('duration', 60.0):
            filename = os.path.join(session.path_tmp, 'empty.png')
            layer.subtitle_text = ''
            layer.grab().save(filename, 'PNG')

            final_text += "file '" + filename + "'\n"
            final_text += 'duration {}\n'.format(session.SUBTITLE['segments'][0]['end'] - session.SUBTITLE['segments'][-1]['start'])
            final_text += "file '" + filename + "'\n"

        open(os.path.join(session.path_tmp, 'subtitles.txt'), 'w').write(final_text)
        subprocess.Popen([session.FFMPEG_EXECUTABLE, '-y', '-f', 'concat', '-safe', '0', '-i', os.path.join(session.path_tmp, 'subtitles.txt'), '-r', str(session.VIDEO.get('framerate', 24)), '-c:v', 'qtrle', '-an', generated_video_filepath], startupinfo=session.STARTUPINFO, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.read()


def global_subtitlesvideo_export_button_clicked(self):
    """Function to export subtitles"""
    suggested_path = os.path.dirname(session.VIDEO['filepath'])
    suggested_name = os.path.basename(session.VIDEO['filepath']).rsplit('.', 1)[0] + '.txt'

    supported_export_files = ';;'.join(['{description} ({extension})'.format(extension=' '.join(['.{ext}'.format(ext=ext) for ext in session.LIST_OF_SUPPORTED_EXPORT_EXTENSIONS[export_format]['extensions']]), description=session.LIST_OF_SUPPORTED_EXPORT_EXTENSIONS[export_format]['description']) for export_format in session.LIST_OF_SUPPORTED_EXPORT_EXTENSIONS])

    filedialog = QFileDialog.getSaveFileName(parent=self, caption='Export to file', dir=os.path.join(suggested_path, suggested_name), filter=supported_export_files)

    if filedialog[0] and filedialog[1]:
        filename = filedialog[0]
        exts = []
        for extformat in filedialog[1].split('(', 1)[1].split(')', 1)[0].split('*'):
            if extformat:
                exts.append(extformat.strip())
        if not filename.endswith(tuple(exts)):
            filename += exts[0]
        format_to_export = filedialog[1].rsplit('(', 1)[-1].split(')', 1)[0]

        file_io.export_file(filename=filename, export_format=format_to_export)



def global_panel_export_video_ffmpeg_color_button_clicked(self):
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['export']['ffmpeg_color'] = color.name(QColor.HexArgb)
    update_preview(self)


def global_panel_export_video_ffmpeg_fontsize_spinbox_changed(self):
    session.CONFIG['export']['ffmpeg_font_size'] = self.global_panel_export_video_ffmpeg_fontsize_spinbox.value()
    update_preview(self)


def global_panel_export_video_ffmpeg_fontfamily_combobox_changed(self):
    session.CONFIG['export']['ffmpeg_font_family'] = self.global_panel_export_video_ffmpeg_fontfamily_combobox.currentText()
    update_preview(self)


def global_panel_export_video_ffmpeg_outline_value_changed(self):
    session.CONFIG['export']['ffmpeg_outline_size'] = self.global_panel_export_video_ffmpeg_outline_value.value()
    update_preview(self)


def global_panel_export_video_ffmpeg_outline_group_toggled(self):
    session.CONFIG['export']['ffmpeg_outline_enabled'] = self.global_panel_export_video_ffmpeg_outline_group.isChecked()
    update_preview(self)


def global_panel_export_video_ffmpeg_shadow_distance_changed(self):
    session.CONFIG['export']['ffmpeg_shadow_distance'] = self.global_panel_export_video_ffmpeg_shadow_distance.value()
    update_preview(self)


def global_panel_export_video_ffmpeg_shadow_group_toggled(self):
    session.CONFIG['export']['ffmpeg_shadow_enabled'] = self.global_panel_export_video_ffmpeg_shadow_group.isChecked()
    update_preview(self)


def global_panel_export_video_ffmpeg_margins_left_distance_changed(self):
    session.CONFIG['export']['ffmpeg_margin_left'] = self.global_panel_export_video_ffmpeg_margins_left_distance.value()
    update_preview(self)


def global_panel_export_video_ffmpeg_margins_right_distance_changed(self):
    session.CONFIG['export']['ffmpeg_margin_right'] = self.global_panel_export_video_ffmpeg_margins_right_distance.value()
    update_preview(self)


def global_panel_export_video_ffmpeg_margins_bottom_distance_changed(self):
    session.CONFIG['export']['ffmpeg_margin_bottom'] = self.global_panel_export_video_ffmpeg_margins_bottom_distance.value()
    update_preview(self)


def global_panel_export_video_ffmpeg_command_qlineedit_textedited(self):
    session.CONFIG['export']['ffmpeg_custom_command'] = self.global_panel_export_video_ffmpeg_command_qlineedit.text()
    update_preview(self)


def update_preview(self):
    if self.global_panel_export_video_ffmpeg_panel.isVisible():
        file_io.save_file(os.path.join(session.path_tmp, 'subtitle.srt'), subtitle_format='SRT', language='en')
        self.thread_generated_burned_video.original_file = session.VIDEO['filepath']
        self.thread_generated_burned_video.is_preview = session.SUBTITLE.get('position', 0) if subtitles.is_current_position_above_subtitle(session.SUBTITLE.get('position', 0)) else session.SUBTITLE['segments'][0]['end']/2
        self.thread_generated_burned_video.is_burned = True
        self.thread_generated_burned_video.burnedin_options = session.CONFIG['export']
        self.thread_generated_burned_video.start()

    
def show(self):
    update(self)

def update(self):
    self.global_panel_export_video_ffmpeg_fontsize_spinbox.setValue(session.CONFIG['export'].get('ffmpeg_font_size', 40))
    self.global_panel_export_video_ffmpeg_fontfamily_combobox.setCurrentText(session.CONFIG['export'].get('ffmpeg_font_family', 'Ubuntu'))
    self.global_panel_export_video_ffmpeg_color_button.setStyleSheet('QPushButton { background-color: ' + session.CONFIG['export'].get('ffmpeg_color', '#ffffffff') + ' }')
    self.global_panel_export_video_ffmpeg_outline_group.setChecked(session.CONFIG['export'].get('ffmpeg_outline_enabled', True))
    self.global_panel_export_video_ffmpeg_outline_value.setValue(session.CONFIG['export'].get('ffmpeg_outline_size', 2))
    self.global_panel_export_video_ffmpeg_shadow_group.setChecked(session.CONFIG['export'].get('ffmpeg_shadow_enabled', True))
    self.global_panel_export_video_ffmpeg_shadow_distance.setValue(session.CONFIG['export'].get('ffmpeg_shadow_distance', 2))
    self.global_panel_export_video_ffmpeg_margins_left_distance.setValue(session.CONFIG['export'].get('ffmpeg_margin_left', 0))
    self.global_panel_export_video_ffmpeg_margins_right_distance.setValue(session.CONFIG['export'].get('ffmpeg_margin_right', 0))
    self.global_panel_export_video_ffmpeg_margins_bottom_distance.setValue(session.CONFIG['export'].get('ffmpeg_margin_bottom', 0))
    self.global_panel_export_video_ffmpeg_command_qlineedit.setText(session.CONFIG['export'].get('ffmpeg_custom_command', ''))

    
def hide(self):
    pass

    
def translate(self):    
    self.global_panel_export_content_txt_panel_export_button.setText(_('export_panel.export'))
    self.global_panel_export_video_ffmpeg_fontsize_label.setText(_('units.size'))
    self.global_panel_export_video_ffmpeg_fontsize_seconds_label.setText(_('units.pixels'))
    self.global_panel_export_video_ffmpeg_fontfamily_label.setText(_('units.font_family'))
    self.global_panel_export_video_ffmpeg_color_label.setText(_('units.color'))
    self.global_panel_export_video_ffmpeg_outline_group.setTitle(_('export_panel.outline'))
    self.global_panel_export_video_ffmpeg_outline_label.setText(_('export_panel.outline'))
    self.global_panel_export_video_ffmpeg_outline_value_pixels_label.setText(_('units.pixels'))
    self.global_panel_export_video_ffmpeg_shadow_group.setTitle(_('export_panel.shadow'))
    self.global_panel_export_video_ffmpeg_shadow_label.setText(_('export_panel.distance'))
    self.global_panel_export_video_ffmpeg_shadow_distance_pixels_label.setText(_('units.pixels'))
    self.global_panel_export_video_ffmpeg_margins_group.setTitle(_('units.margins'))
    self.global_panel_export_video_ffmpeg_margins_left_label.setText(_('units.left'))
    self.global_panel_export_video_ffmpeg_margins_left_distance_pixels_label.setText(_('units.pixels'))
    self.global_panel_export_video_ffmpeg_margins_bottom_label.setText(_('units.bottom'))
    self.global_panel_export_video_ffmpeg_margins_bottom_distance_pixels_label.setText(_('units.pixels'))
    self.global_panel_export_video_ffmpeg_margins_right_label.setText(_('units.right'))
    self.global_panel_export_video_ffmpeg_margins_right_distance_pixels_label.setText(_('units.pixels'))
    self.global_panel_export_video_ffmpeg_margins_right_label.setText(_('export_panel.custom_ffmpeg_commands'))
    self.global_panel_export_video_ffmpeg_export_button.setText(_('export_panel.export_video'))
    self.global_panel_export_video_ffmpeg_preview_label.setText(_('export_panel.preview'))
    self.global_subtitlesvideo_video_generate_transparent_video_button.setText(_('export_panel.generate_transparent_video'))



    
