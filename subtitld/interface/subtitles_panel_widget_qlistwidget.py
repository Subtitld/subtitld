from PySide6.QtWidgets import QHBoxLayout, QPushButton, QLabel, QSizePolicy, QTextEdit, QVBoxLayout, QWidget, QStyledItemDelegate, QStyle, QListView, QLineEdit, QFrame, QComboBox, QSplitter
from PySide6.QtGui import QFontMetrics, QFont, QColor
from PySide6.QtCore import Qt, QSize, QAbstractListModel, QRect, QMargins

from subtitld.interface import subtitles_panel, subtitles_panel_info, timeline
from subtitld.interface.translation import _
from subtitld.modules import utils, quality_check, subtitles, session


class subtitles_panel_qlistwidget_model(QAbstractListModel):
    def __init__(self, *args, subs=None, **kwargs):
        super(subtitles_panel_qlistwidget_model, self).__init__(*args, **kwargs)
        # self.subtitles = subs or []

    def data(self, index, role):
        if role == Qt.DisplayRole:
            return session.SUBTITLE['segments'][index.row()]['text']

        # if role == Qt.DecorationRole:
        #     status, _ = session.SUBTITLE['segments'][index.row()]
        #     if status:
        #         return tick

    def get_index(self, subtitle):
        index = session.SUBTITLE['segments'].index(subtitle)
        return self.index(index)

    def rowCount(self, _):
        return len(session.SUBTITLE['segments'])


class subtitles_panel_qlistwidget_delegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super(subtitles_panel_qlistwidget_delegate, self).__init__(parent)

    def get_number_width(self, index):
        number_width = QFontMetrics(QFont('Ubuntu', 8)).horizontalAdvance((len(str(index.model().rowCount(index)))) * '8')
        return number_width

    def get_text_height(self, option, index):
        row_text = index.data(Qt.DisplayRole)
        width = option.rect.width()
        height = QFontMetrics(QFont('Ubuntu', 11)).boundingRect(QRect(0, 0, width - (20 + 10 + self.get_number_width(index) + 10 + 10), 100), Qt.TextWordWrap, row_text).height()
        return height

    def paint(self, painter, option, index):
        row_text = index.data(Qt.DisplayRole)
        number_width = self.get_number_width(index)

        if option.state & QStyle.State_Selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(session.CONFIG.get('subtitle_list', {}).get('background_color', '#102e3e4c')))
            painter.drawRect(option.rect)
            # painter.setBrush(QColor('#55d43f'))
            # painter.drawRect(QRect(0, option.rect.y(), 20, option.rect.height()))

        sub_is_ok = True
        if session.CONFIG['quality_check'].get('enabled', False):
            sub_is_ok, _, _ = quality_check.check_subtitle(index.model().subtitles[index.row()], session.CONFIG['quality_check'])

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(session.CONFIG.get('subtitle_list', {}).get('number_background_color', '#aa2e3e4c') if sub_is_ok else session.CONFIG.get('subtitle_list', {}).get('number_background_warning_color', '#aa9e1a1a')))

        number_rect = QRect(20, option.rect.y(), 10 + number_width + 10, option.rect.height())
        text_rect = QRect(number_rect.right(), option.rect.y(), option.rect.width() - number_rect.right(), option.rect.height())

        painter.drawRect(number_rect)

        painter.setPen(QColor(session.CONFIG.get('subtitle_list', {}).get('number_color', '#ffffff') if sub_is_ok else session.CONFIG.get('subtitle_list', {}).get('number_warning_color', '#ffffff')))

        number_rect = number_rect.marginsRemoved(QMargins(10, 10, 10, 10))

        painter.setFont(QFont('Ubuntu', 8))
        painter.drawText(number_rect, 0, str(index.row() + 1))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(session.CONFIG.get('subtitle_list', {}).get('text_background_color', '#102e3e4c') if sub_is_ok else session.CONFIG.get('subtitle_list', {}).get('text_background_warning_color', '#109e1a1a')))

        painter.drawRect(text_rect)

        text_rect = text_rect.marginsRemoved(QMargins(10, 10, 10, 10))

        painter.setPen(QColor(session.CONFIG.get('subtitle_list', {}).get('text_color', '#2e3e4c') if sub_is_ok else session.CONFIG.get('subtitle_list', {}).get('text_warning_color', '#9e1a1a')))
        painter.setFont(QFont('Ubuntu', 11))
        painter.drawText(text_rect, Qt.TextWordWrap, row_text)

    def sizeHint(self, option, index):
        width = option.rect.width()
        height = self.get_text_height(option, index) + 20
        return QSize(width, height)


def add_widgets(self):
    self.subtitles_panel_simplelist_qsplitter = QSplitter(Qt.Vertical)

    self.subtitles_panel_simplelist_widget = QWidget()
    self.subtitles_panel_simplelist_widget.setObjectName('subtitles_panel_simplelist_widget')
    self.subtitles_panel_simplelist_widget.setLayout(QVBoxLayout())
    self.subtitles_panel_simplelist_widget.layout().setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_widget.layout().setSpacing(0)

    self.subtitles_panel_qlistwidget_model = subtitles_panel_qlistwidget_model()

    self.subtitles_panel_qlistwidget_delegate = subtitles_panel_qlistwidget_delegate()

    class subtitles_panel_qlistwidget(QListView):
        def __init__(widget, parent=None):
            super(subtitles_panel_qlistwidget, widget).__init__(parent)
        
        def resizeEvent(widget, event):
            widget.model().layoutChanged.emit()
            event.accept()
        
        def showEvent(widget, event):
            widget.update_content()
            event.accept()
        
        def update_content(widget):
            current_sub = subtitles.subtitle_under_current_position()
            index = session.SUBTITLE['segments'].index(current_sub) if current_sub else 0
            if current_sub and not (widget.verticalScrollBar().value() + widget.verticalScrollBar().pageStep() > index > widget.verticalScrollBar().value()):
                widget.verticalScrollBar().setValue(index - 1)

            widget.model().layoutChanged.emit()

            if session.SUBTITLE['selected']:
                widget.setCurrentIndex(widget.model().get_index(session.SUBTITLE['selected']))

            update_properties_widget(widget.window())


    self.subtitles_panel_qlistwidget = subtitles_panel_qlistwidget()
    self.subtitles_panel_qlistwidget.setViewMode(QListView.ListMode)
    self.subtitles_panel_qlistwidget.setObjectName('subtitles_panel_qlistwidget')
    self.subtitles_panel_qlistwidget.setContentsMargins(QMargins(0, 0, 0, 0))
    self.subtitles_panel_qlistwidget.setSpacing(0)
    self.subtitles_panel_qlistwidget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    self.subtitles_panel_qlistwidget.setFocusPolicy(Qt.NoFocus)
    self.subtitles_panel_qlistwidget.setModel(self.subtitles_panel_qlistwidget_model)
    self.subtitles_panel_qlistwidget.setItemDelegate(self.subtitles_panel_qlistwidget_delegate)
    # self.subtitles_panel_qlistwidget.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum))
    self.subtitles_panel_qlistwidget.clicked.connect(lambda: subtitles_panel_qlistwidget_item_clicked(self))
    self.subtitles_panel_simplelist_widget.layout().addWidget(self.subtitles_panel_qlistwidget, 1)

    self.properties_information = QFrame()
    self.properties_information.setObjectName('properties_information')
    self.properties_information.setLayout(QVBoxLayout())
    self.properties_information.layout().setContentsMargins(20, 0, 0, 0)
    self.properties_information.layout().setSpacing(5)

    self.properties_information_stats = QFrame()
    self.properties_information_stats.setObjectName('properties_information')
    self.properties_information_stats.setLayout(QHBoxLayout())
    self.properties_information_stats.layout().setContentsMargins(0, 0, 0, 0)
    self.properties_information_stats.layout().setSpacing(5)

    self.properties_information_word_counter = QLabel()
    self.properties_information_word_counter.setAlignment(Qt.AlignCenter)
    self.properties_information_word_counter.setObjectName('properties_information_word_counter')
    self.properties_information_stats.layout().addWidget(self.properties_information_word_counter)

    self.properties_information_stats.layout().addSpacing(-5)

    self.properties_information_wpm = QLabel()
    self.properties_information_wpm.setAlignment(Qt.AlignCenter)
    self.properties_information_wpm.setObjectName('properties_information_wpm')
    self.properties_information_stats.layout().addWidget(self.properties_information_wpm)

    self.properties_information_character_counter = QLabel()
    self.properties_information_character_counter.setAlignment(Qt.AlignCenter)
    self.properties_information_character_counter.setObjectName('properties_information_character_counter')
    self.properties_information_stats.layout().addWidget(self.properties_information_character_counter)

    self.properties_information_stats.layout().addSpacing(-5)

    self.properties_information_cps = QLabel()
    self.properties_information_cps.setAlignment(Qt.AlignCenter)
    self.properties_information_cps.setObjectName('properties_information_cps')
    self.properties_information_stats.layout().addWidget(self.properties_information_cps)

    self.properties_information_sub_duration = QLabel()
    self.properties_information_sub_duration.setAlignment(Qt.AlignCenter)
    self.properties_information_sub_duration.setObjectName('properties_information_sub_duration')
    self.properties_information_stats.layout().addWidget(self.properties_information_sub_duration)

    self.properties_information_number_of_lines = QLabel()
    self.properties_information_number_of_lines.setAlignment(Qt.AlignCenter)
    self.properties_information_number_of_lines.setObjectName('properties_information_number_of_lines')
    self.properties_information_stats.layout().addWidget(self.properties_information_number_of_lines)

    self.properties_information_stats.layout().addSpacing(-5)

    self.properties_information_cpl = QLabel()
    self.properties_information_cpl.setObjectName('properties_information_cpl')
    self.properties_information_cpl.setFixedWidth(30)
    self.properties_information_stats.layout().addWidget(self.properties_information_cpl)

    self.properties_information.layout().addWidget(self.properties_information_stats)

    self.properties_information_reason = QLabel()
    self.properties_information_reason.setObjectName('properties_information_reason')
    self.properties_information.layout().addWidget(self.properties_information_reason)

    # self.properties_information.layout().addStretch()

    self.subtitles_panel_simplelist_widget.layout().addWidget(self.properties_information)

    self.subtitles_panel_simplelist_qsplitter.addWidget(self.subtitles_panel_simplelist_widget)

    self.subtitles_panel_simplelist_properties = QFrame()
    self.subtitles_panel_simplelist_properties.setLayout(QVBoxLayout())
    self.subtitles_panel_simplelist_properties.layout().setContentsMargins(20, 0, 0, 0)
    self.subtitles_panel_simplelist_properties.layout().setSpacing(0)

    self.subtitles_panel_simplelist_properties_textedit_line = QHBoxLayout()
    self.subtitles_panel_simplelist_properties_textedit_line.setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_properties_textedit_line.setSpacing(0)

    self.properties_textedit = QTextEdit()
    self.properties_textedit.setObjectName('properties_textedit')
    self.properties_textedit.textChanged.connect(lambda: properties_textedit_changed(self))
    self.subtitles_panel_simplelist_properties_textedit_line.addWidget(self.properties_textedit)

    self.subtitles_panel_simplelist_properties_textedit_timings_column = QVBoxLayout()
    self.subtitles_panel_simplelist_properties_textedit_timings_column.setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_properties_textedit_timings_column.setSpacing(0)

    self.subtitles_panel_simplelist_properties_start_timing_frame = QFrame()
    self.subtitles_panel_simplelist_properties_start_timing_frame.setObjectName('subtitles_panel_simplelist_properties_start_timing_frame')
    self.subtitles_panel_simplelist_properties_start_timing_frame.setProperty('class', 'qframe_timings')
    self.subtitles_panel_simplelist_properties_start_timing_frame.setLayout(QVBoxLayout())
    self.subtitles_panel_simplelist_properties_start_timing_frame.layout().setContentsMargins(2, 2, 2, 2)
    self.subtitles_panel_simplelist_properties_start_timing_frame.layout().setSpacing(0)

    self.subtitles_panel_simplelist_properties_start_timing_label = QLabel()
    self.subtitles_panel_simplelist_properties_start_timing_label.setObjectName('subtitles_panel_simplelist_properties_start_timing_label')
    self.subtitles_panel_simplelist_properties_start_timing_label.setProperty('class', 'properties_timing_labels')
    self.subtitles_panel_simplelist_properties_start_timing_label.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.subtitles_panel_simplelist_properties_start_timing_frame.layout().addWidget(self.subtitles_panel_simplelist_properties_start_timing_label)

    self.subtitles_panel_simplelist_properties_start_timing_qlineedit = QLineEdit()
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setProperty('class', 'qlineedit_timings')
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setObjectName('subtitles_panel_simplelist_properties_start_timing_qlineedit')
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.textEdited.connect(lambda: subtitles_panel_simplelist_properties_start_timing_qlineedit_text_edited(self))
    self.subtitles_panel_simplelist_properties_start_timing_frame.layout().addWidget(self.subtitles_panel_simplelist_properties_start_timing_qlineedit)

    self.subtitles_panel_simplelist_properties_textedit_timings_column.addWidget(self.subtitles_panel_simplelist_properties_start_timing_frame)

    self.subtitles_panel_simplelist_properties_textedit_timings_column.addSpacing(-10)
    self.subtitles_panel_simplelist_properties_duration_timing_lock_button = QPushButton()
    self.subtitles_panel_simplelist_properties_duration_timing_lock_button.setObjectName('subtitles_panel_simplelist_properties_duration_timing_lock_button')
    self.subtitles_panel_simplelist_properties_duration_timing_lock_button.setProperty('class', 'subbutton_transparent')
    self.subtitles_panel_simplelist_properties_duration_timing_lock_button.setProperty('borderless_right', 'true')
    self.subtitles_panel_simplelist_properties_duration_timing_lock_button.setCheckable(True)
    self.subtitles_panel_simplelist_properties_duration_timing_lock_button.setFixedSize(QSize(20, 20))
    self.subtitles_panel_simplelist_properties_textedit_timings_column.addWidget(self.subtitles_panel_simplelist_properties_duration_timing_lock_button, 0, Qt.AlignRight)
    self.subtitles_panel_simplelist_properties_textedit_timings_column.addSpacing(-10)

    self.subtitles_panel_simplelist_properties_duration_timing_frame = QFrame()
    self.subtitles_panel_simplelist_properties_duration_timing_frame.setObjectName('subtitles_panel_simplelist_properties_duration_timing_frame')
    self.subtitles_panel_simplelist_properties_duration_timing_frame.setProperty('class', 'qframe_timings')
    self.subtitles_panel_simplelist_properties_duration_timing_frame.setLayout(QVBoxLayout())
    self.subtitles_panel_simplelist_properties_duration_timing_frame.layout().setContentsMargins(2, 2, 2, 2)
    self.subtitles_panel_simplelist_properties_duration_timing_frame.layout().setSpacing(0)

    self.subtitles_panel_simplelist_properties_duration_timing_label = QLabel()
    self.subtitles_panel_simplelist_properties_duration_timing_label.setObjectName('subtitles_panel_simplelist_properties_duration_timing_label')
    self.subtitles_panel_simplelist_properties_duration_timing_label.setProperty('class', 'properties_timing_labels')
    self.subtitles_panel_simplelist_properties_duration_timing_label.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.subtitles_panel_simplelist_properties_duration_timing_frame.layout().addWidget(self.subtitles_panel_simplelist_properties_duration_timing_label)

    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit = QLineEdit()
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setProperty('class', 'qlineedit_timings')
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setObjectName('subtitles_panel_simplelist_properties_duration_timing_qlineedit')
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.textEdited.connect(lambda: subtitles_panel_simplelist_properties_duration_timing_qlineedit_text_edited(self))
    self.subtitles_panel_simplelist_properties_duration_timing_frame.layout().addWidget(self.subtitles_panel_simplelist_properties_duration_timing_qlineedit)

    self.subtitles_panel_simplelist_properties_textedit_timings_column.addWidget(self.subtitles_panel_simplelist_properties_duration_timing_frame)

    self.subtitles_panel_simplelist_properties_ending_timing_frame = QFrame()
    self.subtitles_panel_simplelist_properties_ending_timing_frame.setObjectName('subtitles_panel_simplelist_properties_ending_timing_frame')
    self.subtitles_panel_simplelist_properties_ending_timing_frame.setProperty('class', 'qframe_timings')
    self.subtitles_panel_simplelist_properties_ending_timing_frame.setLayout(QVBoxLayout())
    self.subtitles_panel_simplelist_properties_ending_timing_frame.layout().setContentsMargins(2, 2, 2, 2)
    self.subtitles_panel_simplelist_properties_ending_timing_frame.layout().setSpacing(0)

    self.subtitles_panel_simplelist_properties_ending_timing_label = QLabel()
    self.subtitles_panel_simplelist_properties_ending_timing_label.setObjectName('subtitles_panel_simplelist_properties_ending_timing_label')
    self.subtitles_panel_simplelist_properties_ending_timing_label.setProperty('class', 'properties_timing_labels')
    self.subtitles_panel_simplelist_properties_ending_timing_label.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.subtitles_panel_simplelist_properties_ending_timing_frame.layout().addWidget(self.subtitles_panel_simplelist_properties_ending_timing_label)

    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit = QLineEdit()
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setProperty('class', 'qlineedit_timings')
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setObjectName('subtitles_panel_simplelist_properties_ending_timing_qlineedit')
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.textEdited.connect(lambda: subtitles_panel_simplelist_properties_ending_timing_qlineedit_text_edited(self))
    self.subtitles_panel_simplelist_properties_ending_timing_frame.layout().addWidget(self.subtitles_panel_simplelist_properties_ending_timing_qlineedit)

    self.subtitles_panel_simplelist_properties_textedit_timings_column.addWidget(self.subtitles_panel_simplelist_properties_ending_timing_frame)

    self.subtitles_panel_simplelist_properties_textedit_line.addLayout(self.subtitles_panel_simplelist_properties_textedit_timings_column)

    self.subtitles_panel_simplelist_properties.layout().addLayout(self.subtitles_panel_simplelist_properties_textedit_line, 1)

    self.subtitles_panel_simplelist_properties_buttons_line = QHBoxLayout()
    self.subtitles_panel_simplelist_properties_buttons_line.setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_properties_buttons_line.setSpacing(0)

    self.send_text_to_last_subtitle_button = QPushButton()
    self.send_text_to_last_subtitle_button.setObjectName('send_text_to_last_subtitle_button')
    self.send_text_to_last_subtitle_button.setLayout(QHBoxLayout(self.send_text_to_last_subtitle_button))
    self.send_text_to_last_subtitle_button.layout().setContentsMargins(3, 0, 3, 3)
    self.send_text_to_last_subtitle_button.setProperty('class', 'subbutton2_dark')
    self.send_text_to_last_subtitle_button.clicked.connect(lambda: send_text_to_last_subtitle_button_clicked(self))
    self.subtitles_panel_simplelist_properties_buttons_line.addWidget(self.send_text_to_last_subtitle_button)

    self.send_text_to_last_subtitle_and_slice_button = QPushButton()
    self.send_text_to_last_subtitle_and_slice_button.setObjectName('send_text_to_last_subtitle_and_slice_button')
    self.send_text_to_last_subtitle_and_slice_button.setProperty('class', 'subbutton2_dark')
    self.send_text_to_last_subtitle_and_slice_button.clicked.connect(lambda: send_text_to_last_subtitle_and_slice_button_clicked(self))
    self.send_text_to_last_subtitle_and_slice_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.send_text_to_last_subtitle_button.layout().addWidget(self.send_text_to_last_subtitle_and_slice_button, 0, Qt.AlignLeft)

    self.speaker_combobox = QComboBox()
    self.speaker_combobox.setObjectName('speaker_combobox')
    self.speaker_combobox.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.speaker_combobox.currentIndexChanged.connect(lambda: speaker_combobox_current_index_changed(self))
    self.subtitles_panel_simplelist_properties_buttons_line.addWidget(self.speaker_combobox, 0)

    self.send_text_to_next_subtitle_button = QPushButton()
    self.send_text_to_next_subtitle_button.setObjectName('send_text_to_next_subtitle_button')
    self.send_text_to_next_subtitle_button.setLayout(QHBoxLayout(self.send_text_to_next_subtitle_button))
    self.send_text_to_next_subtitle_button.layout().setContentsMargins(3, 0, 3, 3)
    self.send_text_to_next_subtitle_button.setProperty('class', 'subbutton2_dark')
    self.send_text_to_next_subtitle_button.clicked.connect(lambda: send_text_to_next_subtitle_button_clicked(self))
    self.subtitles_panel_simplelist_properties_buttons_line.addWidget(self.send_text_to_next_subtitle_button)

    self.send_text_to_next_subtitle_and_slice_button = QPushButton()
    self.send_text_to_next_subtitle_and_slice_button.setObjectName('send_text_to_next_subtitle_and_slice_button')
    self.send_text_to_next_subtitle_and_slice_button.setProperty('class', 'subbutton2_dark')
    self.send_text_to_next_subtitle_and_slice_button.clicked.connect(lambda: send_text_to_next_subtitle_and_slice_button_clicked(self))
    self.send_text_to_next_subtitle_and_slice_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
    self.send_text_to_next_subtitle_button.layout().addWidget(self.send_text_to_next_subtitle_and_slice_button, 0, Qt.AlignRight)

    self.subtitles_panel_simplelist_properties.layout().addLayout(self.subtitles_panel_simplelist_properties_buttons_line, 0)

    # self.subtitles_panel_simplelist_widget.layout().addWidget(self.subtitles_panel_simplelist_properties, 0)

    self.subtitles_panel_simplelist_qsplitter.addWidget(self.subtitles_panel_simplelist_properties)

    self.subtitles_panel_stackedwidgets.addWidget(self.subtitles_panel_simplelist_qsplitter)


def add_button(self):
    self.subtitles_panel_widget_button_list = QPushButton()
    self.subtitles_panel_widget_button_list.setObjectName('subtitles_panel_widget_button_list')
    self.subtitles_panel_widget_button_list.setProperty('class', 'subtitles_panel_left_button')
    self.subtitles_panel_widget_button_list.setCheckable(True)
    self.subtitles_panel_widget_button_list.setChecked(True)
    self.subtitles_panel_widget_button_list.setFixedWidth(23)
    self.subtitles_panel_widget_button_list.clicked.connect(lambda vision: subtitles_panel.update_subtitles_panel_widget_vision(self, 'list'))
    self.subtitles_panel_widget_buttons_vbox.addWidget(self.subtitles_panel_widget_button_list)


def subtitles_panel_markdown_qtextedit_cursorpositionchanged(self):
    position = self.subtitles_panel_markdown_qtextedit.textCursor().position()

    cursor = 0
    # markdown_text = ''
    for subtitle in sorted(session.SUBTITLE['segments']):
        cursor += len(str("{:.3f}".format(subtitle['start'])))
        next_index = session.SUBTITLE['segments'].index(subtitle) + 1
        if not next_index >= len(session.SUBTITLE['segments']) and not session.SUBTITLE['segments'][next_index]['start'] - 0.001 == subtitle['end']:
            cursor += len(' - ' + str("{:.3f}".format(subtitle['end'])))
        cursor += len('\n')

        cursor += len(str(subtitle['text']) + '\n\n')
        if cursor > position:
            session.SUBTITLE['selected'] = subtitle
            break

    if session.SUBTITLE['selected']:
        if not (session.SUBTITLE.get('position', 0) > session.SUBTITLE['selected']['start'] and session.SUBTITLE.get('position', 0) < session.SUBTITLE['selected']['end']):
            self.player_widget.seek(session.SUBTITLE['selected']['start'] + ((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) * .5))

    timeline.update(self)
    timeline.update_scrollbar(self, position='middle')

    # print(session.SUBTITLE['selected'])


def subtitles_panel_markdown_qtextedit_textchanged(self):
    subtitles_panel_markdown_qtextedit_update_subtitles_list(self)


def subtitles_panel_markdown_qtextedit_update_subtitles_list(self):
    sub_list = []

    last_time = []
    last_text = ''
    s = 0
    for line in self.subtitles_panel_markdown_qtextedit.toPlainText().split('\n'):
        if all(utils.is_float(t) for t in line.strip().replace(' - ', ' ').split(' ')):
            this_time = [
                float(line.strip().split(' - ')[0])
            ]

            if ' - ' in line.strip():
                this_time.append(float(line.strip().split(' - ')[-1]))
            else:
                this_time.append(0)

            if last_time:
                sub_list.append(
                    [
                        float(last_time[0]),
                        (float(last_time[1]) - float(last_time[0])) if last_time[1] else (float(this_time[0]) - .001 - float(last_time[0])),
                        last_text.strip()
                    ]
                )
                last_text = ''
                s += 1

            last_time = this_time

        else:
            last_text += line + '\n'

    if last_time:
        sub_list.append(
            [
                float(last_time[0]),
                (float(last_time[1]) - float(last_time[0])) if last_time[1] else (float(this_time[0]) - .001 - float(last_time[0])),
                last_text.strip()
            ]
        )
        last_text = ''

    session.SUBTITLE['selected'] = False

    # Sanitize subtitles so there is no overlaping subtitles?

    session.SUBTITLE['segments'] = sorted(sub_list)

    timeline.update(self)

def subtitles_panel_qlistwidget_item_clicked(self):
    """Function to call when a subtitle item on the list is clicked"""
    if self.subtitles_panel_qlistwidget.currentIndex():
        sub_index = self.subtitles_panel_qlistwidget.currentIndex().row()
        session.SUBTITLE['selected'] = session.SUBTITLE['segments'][sub_index]

    if session.SUBTITLE['selected']:
        self.properties_textedit.blockSignals(True)
        update_properties_widget(self)
        self.properties_textedit.blockSignals(False)

        if not session.SUBTITLE['selected']['start'] < session.SUBTITLE.get('position', 0) < session.SUBTITLE['selected']['end']:
            self.player_widget.seek(session.SUBTITLE['selected']['start'] + ((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) * .5))

        timeline.update_scrollbar(self, position='middle')


def send_text_to_next_subtitle_button_clicked(self):
    """Function to call when send text to next subtitle is clicked"""
    pos = self.properties_textedit.textCursor().position()
    last_text = self.properties_textedit.toPlainText()[:pos].strip()
    next_text = self.properties_textedit.toPlainText()[pos:].strip()
    subtitles.send_text_to_next_subtitle(selected_subtitle=session.SUBTITLE['selected'], last_text=last_text, next_text=next_text)
    subtitles_panel.update_subtitles_panel_widget_vision_content(self)
    timeline.update(self)

    self.timeline_widget.setFocus(Qt.TabFocusReason)


def speaker_combobox_current_index_changed(self):
    if session.SUBTITLE['selected']:
        session.SUBTITLE['selected']['speaker'] = self.speaker_combobox.currentText()


def send_text_to_last_subtitle_and_slice_button_clicked(self):
    """Function to send text to the last subtitle and slice at the same time"""
    position = session.SUBTITLE.get('position', 0)
    if not session.SUBTITLE['selected']['end'] > session.SUBTITLE.get('position', 0) > session.SUBTITLE['selected']['start']:
        position = session.SUBTITLE['selected']['start'] + ((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) * .5)
    subtitles.subtitle_start_to_current_position(position=position)
    subtitles.last_end_to_current_position(position=position - .001)
    send_text_to_last_subtitle_button_clicked(self)


def send_text_to_next_subtitle_and_slice_button_clicked(self):
    """Function to send text to the next subtitle and slice at the same time"""
    position = session.SUBTITLE.get('position', 0)
    if not session.SUBTITLE['selected']['end'] > session.SUBTITLE.get('position', 0) > session.SUBTITLE['selected']['start']:
        position = session.SUBTITLE['selected']['start'] + ((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) * .5)
    subtitles.subtitle_end_to_current_position(position=position)
    subtitles.next_start_to_current_position(position=position + .001)
    send_text_to_next_subtitle_button_clicked(self)


def send_text_to_last_subtitle_button_clicked(self):
    """Function to call when send text to last subtitle is clicked"""
    pos = self.properties_textedit.textCursor().position()
    last_text = self.properties_textedit.toPlainText()[:pos].strip()
    next_text = self.properties_textedit.toPlainText()[pos:].strip()
    subtitles.send_text_to_last_subtitle(selected_subtitle=session.SUBTITLE['selected'], last_text=last_text, next_text=next_text)
    subtitles_panel.update_subtitles_panel_widget_vision_content(self)
    timeline.update(self)
    self.timeline_widget.setFocus(Qt.TabFocusReason)


def properties_textedit_changed(self):
    """Function to call when properties textedit is changed"""
    old_selected_subtitle = session.SUBTITLE['selected']
    if old_selected_subtitle and old_selected_subtitle['text'] != self.properties_textedit.toPlainText():
        counter = session.SUBTITLE['segments'].index(old_selected_subtitle)
        subtitles.change_subtitle_text(selected_subtitle=session.SUBTITLE['segments'][counter], text=self.properties_textedit.toPlainText())
        session.CONFIG['unsaved'] = True
        subtitles_panel_info.update(self)
        timeline.update(self)
        update_properties_information(self)


def update_properties_information(self):
    if session.SUBTITLE['selected']:
        reasons = []
        issues = []

        if session.CONFIG['quality_check'].get('enabled', False):
            _, reasons, issues = quality_check.check_subtitle(session.SUBTITLE['selected'], session.CONFIG['quality_check'])

            n_words = len(session.SUBTITLE['selected']['text'].replace('\n', ' ').split(' '))
            n_char = len(session.SUBTITLE['selected']['text'].replace('\n', '').replace(' ', ''))

            self.properties_information_word_counter.setStyleSheet('QLabel { background-color: ' + ('#55d43f' if 'wpm' not in issues else '#aa9e1a1a') + '}')
            self.properties_information_wpm.setStyleSheet('QLabel { background-color: ' + ('#bb55d43f' if 'wpm' not in issues else '#bb9e1a1a') + '}')
            self.properties_information_character_counter.setStyleSheet('QLabel { background-color: ' + ('#55d43f' if 'cps' not in issues else '#aa9e1a1a') + '}')
            self.properties_information_cps.setStyleSheet('QLabel { background-color: ' + ('#bb55d43f' if 'cps' not in issues else '#bb9e1a1a') + '}')
            self.properties_information_sub_duration.setStyleSheet('QLabel { background-color: ' + ('#55d43f' if 'duration' not in issues else '#bb9e1a1a') + '}')
            self.properties_information_number_of_lines.setStyleSheet('QLabel { background-color: ' + ('#55d43f' if 'number_of_lines' not in issues else '#bb9e1a1a') + '}')
            self.properties_information_cpl.setStyleSheet('QLabel { background-color: ' + ('#bb55d43f' if 'cpl' not in issues else '#bb9e1a1a') + '}')

            self.properties_information_word_counter.setText(str(n_words))
            self.properties_information_wpm.setText(str(int(n_words / ((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) / 60))))
            self.properties_information_character_counter.setText(str(n_char))
            self.properties_information_cps.setText(str(int(n_char / ((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start'])))))
            self.properties_information_sub_duration.setText(str(round((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']), 3)))
            self.properties_information_number_of_lines.setText(str(int(len(session.SUBTITLE['selected']['text'].split('\n')))))

            self.properties_information_reason.setVisible(bool(reasons))
            self.properties_information_reason.setText('\n'.join(reasons))


def update_properties_widget(self):
    """Function to update properties panel widgets"""

    update_properties_information(self)
    self.subtitles_panel_simplelist_properties.setVisible(bool(session.SUBTITLE['selected']))
    self.subtitles_panel_simplelist_properties_duration_timing_lock_button.raise_()

    text = ''
    if session.SUBTITLE['selected']:
        text = session.SUBTITLE['selected']['text']

        self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setText(utils.get_timeline_time_str(session.SUBTITLE['selected']['start'], ms=True))
        self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setText(utils.get_timeline_time_str((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']), ms=True))
        self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setText(utils.get_timeline_time_str(session.SUBTITLE['selected']['end'], ms=True))

        self.speaker_combobox.setCurrentText(session.SUBTITLE['selected'].get('speaker', 'A'))

    if not self.properties_textedit.hasFocus():
        self.properties_textedit.setText(text)
    self.properties_information_stats.setVisible(bool(session.SUBTITLE['selected']) and session.CONFIG.get('quality_check', {}).get('show_statistics', False))

    

def subtitles_panel_simplelist_properties_start_timing_qlineedit_text_edited(self):
    if session.SUBTITLE['selected']:
        if not (self.subtitles_panel_simplelist_properties_start_timing_qlineedit.text() == '' or utils.convert_ffmpeg_timecode_to_seconds(self.subtitles_panel_simplelist_properties_start_timing_qlineedit.text()) > session.SUBTITLE['selected']['end']):
            new_start = utils.convert_ffmpeg_timecode_to_seconds(self.subtitles_panel_simplelist_properties_start_timing_qlineedit.text())
            duration = (session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) if self.subtitles_panel_simplelist_properties_duration_timing_lock_button.isChecked() else (session.SUBTITLE['selected']['end'] - new_start)
            session.SUBTITLE['selected']['start'] = new_start
            session.SUBTITLE['selected']['end'] = session.SUBTITLE['selected']['start'] + duration
    timeline.update(self)


def subtitles_panel_simplelist_properties_duration_timing_qlineedit_text_edited(self):
    if session.SUBTITLE['selected']:
        if self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.text() == '':
            self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setText(utils.get_timeline_time_str((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) - session.SUBTITLE['selected']['start'], ms=True))
        else:
            session.SUBTITLE['selected']['end'] = session.SUBTITLE['selected']['start'] + utils.convert_ffmpeg_timecode_to_seconds(self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.text())
    timeline.update(self)


def subtitles_panel_simplelist_properties_ending_timing_qlineedit_text_edited(self):
    if session.SUBTITLE['selected']:
        if self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.text() == '':
            self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setText(utils.get_timeline_time_str(session.SUBTITLE['selected']['end'], ms=True))
        else:
            session.SUBTITLE['selected']['end'] = session.SUBTITLE['selected']['start'] + utils.convert_ffmpeg_timecode_to_seconds(self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.text()) - utils.convert_ffmpeg_timecode_to_seconds(self.subtitles_panel_simplelist_properties_starting_timing_qlineedit.text())
    timeline.update(self)


def translate_widgets(self):
    self.subtitles_panel_simplelist_properties_start_timing_label.setText(_('subtitles_panel_widget_qlistwidget.start'))
    self.subtitles_panel_simplelist_properties_duration_timing_label.setText(_('subtitles_panel_widget_qlistwidget.duration'))
    self.subtitles_panel_simplelist_properties_ending_timing_label.setText(_('subtitles_panel_widget_qlistwidget.end'))
    self.send_text_to_last_subtitle_button.setText(_('subtitles_panel_widget_qlistwidget.send_to_last'))
    self.send_text_to_next_subtitle_button.setText(_('subtitles_panel_widget_qlistwidget.send_to_next'))


def update_speakers_list(self):
    self.speaker_combobox.clear()
    self.speaker_combobox.addItems(sorted(set([subtitle.get('speaker', 'A') for subtitle in session.SUBTITLE['segments']])))