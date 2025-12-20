from PySide6.QtWidgets import QVBoxLayout, QWidget, QHBoxLayout, QSplitter, QPushButton, QListView, QStyledItemDelegate, QFrame, QLabel, QTextEdit, QSizePolicy, QLineEdit, QStyle, QDialog, QListWidget, QListWidgetItem, QAbstractScrollArea, QGraphicsOpacityEffect
from PySide6.QtCore import Qt, QAbstractListModel, QRect, QMargins, QSize, Signal, QPoint, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFontMetrics, QFont, QIcon, QPixmap

from subtitld.interface import utils
from subtitld.interface import left_panel
from subtitld.interface import preview_panel
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import subtitles
from subtitld.modules import utils as modules_utils


class subtitles_panel_qlistwidget(QListView):
    def __init__(widget, parent=None):
        super(subtitles_panel_qlistwidget, widget).__init__(parent)

        class model(QAbstractListModel):
            def __init__(self, *args, subs=None, **kwargs):
                super(model, self).__init__(*args, **kwargs)
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

        class delegate(QStyledItemDelegate):
            def __init__(self, parent=None):
                super(delegate, self).__init__(parent)

            def get_number_width(self, index):
                number_width = QFontMetrics(QFont('Montserrat', 8)).horizontalAdvance((len(str(index.model().rowCount(index)))) * '8')
                return number_width

            def get_text_height(self, option, index):
                row_text = index.data(Qt.DisplayRole)
                width = option.rect.width()
                height = QFontMetrics(QFont('Montserrat', 10)).boundingRect(QRect(0, 0, width - (20 + 10 + self.get_number_width(index) + 10 + 10), 100), Qt.TextWordWrap, row_text).height()
                return height

            def paint(self, painter, option, index):
                row_text = index.data(Qt.DisplayRole)
                number_width = self.get_number_width(index)

                if option.state & QStyle.State_Selected:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(session.CONFIG.get('subtitle_list', {}).get('background_color', '#802e3e4c')))
                    painter.drawRect(option.rect)
                    # painter.setBrush(QColor('#55d43f'))
                    # painter.drawRect(QRect(0, option.rect.y(), 20, option.rect.height()))

                sub_is_ok = True
                # if session.CONFIG['quality_check'].get('enabled', False):
                #     sub_is_ok, _, _ = quality_check.check_subtitle(index.model.subtitles[index.row()], session.CONFIG['quality_check'])

                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(session.CONFIG.get('subtitle_list', {}).get('number_background_color', '#aa2e3e4c') if sub_is_ok else session.CONFIG.get('subtitle_list', {}).get('number_background_warning_color', '#aa9e1a1a')))

                number_rect = QRect(0, option.rect.y(), 10 + number_width + 10, option.rect.height())
                text_rect = QRect(number_rect.right(), option.rect.y(), option.rect.width() - number_rect.right(), option.rect.height())

                painter.drawRect(number_rect)

                painter.setPen(QColor(session.CONFIG.get('subtitle_list', {}).get('number_color', '#ffffff') if sub_is_ok else session.CONFIG.get('subtitle_list', {}).get('number_warning_color', '#ffffff')))

                number_rect = number_rect.marginsRemoved(QMargins(10, 10, 10, 10))

                painter.setFont(QFont('Montserrat', 8))
                painter.drawText(number_rect, 0, str(index.row() + 1))

                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(session.CONFIG.get('subtitle_list', {}).get('text_background_color', '#102e3e4c') if sub_is_ok else session.CONFIG.get('subtitle_list', {}).get('text_background_warning_color', '#109e1a1a')))

                painter.drawRect(text_rect)

                text_rect = text_rect.marginsRemoved(QMargins(10, 10, 10, 10))

                painter.setPen(QColor(session.CONFIG.get('subtitle_list', {}).get('text_color', '#ffffff') if sub_is_ok else session.CONFIG.get('subtitle_list', {}).get('text_warning_color', '#9e1a1a')))
                painter.setFont(QFont('Montserrat', 10))
                painter.drawText(text_rect, Qt.TextWordWrap, row_text)

            def sizeHint(self, option, index):
                width = option.rect.width()
                height = self.get_text_height(option, index) + 20
                return QSize(width, height)

        widget.model = model()
        widget.delegate = delegate()
        widget.setViewMode(QListView.ListMode)
        widget.setObjectName('subtitles_panel_qlistwidget')
        widget.setContentsMargins(QMargins(0, 0, 0, 0))
        widget.setSpacing(0)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        widget.setFocusPolicy(Qt.NoFocus)
        widget.setModel(widget.model)
        widget.setItemDelegate(widget.delegate)
        widget.clicked.connect(lambda index: widget.item_selected(index))

    def resizeEvent(widget, event):
        widget.model.layoutChanged.emit()
        event.accept()
    
    def showEvent(widget, event):
        widget.update_content()
        event.accept()
    
    def update_content(widget):
        current_sub = subtitles.subtitle_under_current_position()
        index = session.SUBTITLE['segments'].index(current_sub) if current_sub else 0
        if current_sub and not (widget.verticalScrollBar().value() + widget.verticalScrollBar().pageStep() > index > widget.verticalScrollBar().value()):
            widget.verticalScrollBar().setValue(index - 1)

        widget.model.layoutChanged.emit()

        if 'selected' in session.SUBTITLE and session.SUBTITLE['selected']:
            widget.setCurrentIndex(widget.model.get_index(session.SUBTITLE['selected']))

        # widget.update_properties_widget(widget.window())
    
    def item_selected(widget, index):
        session.SUBTITLE['selected'] = session.SUBTITLE['segments'][index.row()]
        update(widget.window())

def load(self):
    tab_name = 'subtitles'
    left_panel.add_button(self, tab_name)
    
    left_panel_subtitles_panel = QWidget()
    left_panel_subtitles_panel.setObjectName(f'left_panel_{tab_name}')
    left_panel_subtitles_panel.setLayout(QVBoxLayout())
    left_panel_subtitles_panel.layout().setContentsMargins(0, 0, 0, 0)
    
    left_panel.add_panel(self, left_panel_subtitles_panel)

    subtitles_panel_simplelist_qsplitter = QSplitter(Qt.Vertical)
    
    self.subtitles_panel_qlistwidget = subtitles_panel_qlistwidget()

    subtitles_panel_simplelist_qsplitter.addWidget(self.subtitles_panel_qlistwidget)

    self.left_panel_subtitleslist_bottom_panel = QWidget()
    self.left_panel_subtitleslist_bottom_panel.setObjectName('left_panel_subtitleslist_bottom_panel')
    self.left_panel_subtitleslist_bottom_panel.setLayout(QVBoxLayout())
    self.left_panel_subtitleslist_bottom_panel.layout().setContentsMargins(0, 0, 0, 0)
    self.left_panel_subtitleslist_bottom_panel.layout().setSpacing(0)

    self.left_panel_subtitleslist_speaker_selector = SpeakerSelector()
    self.left_panel_subtitleslist_speaker_selector.setObjectName('left_panel_subtitleslist_speaker_selector')
    
    self.left_panel_subtitleslist_textedit = QTextEdit()
    self.left_panel_subtitleslist_textedit.setObjectName('left_panel_subtitleslist_textedit')
    self.left_panel_subtitleslist_textedit.setAttribute(Qt.WA_LayoutOnEntireRect)
    self.left_panel_subtitleslist_textedit.setLayout(QVBoxLayout())
    self.left_panel_subtitleslist_textedit.layout().setContentsMargins(0, 0, 0, 0)
    self.left_panel_subtitleslist_textedit.layout().setSpacing(0)
    self.left_panel_subtitleslist_textedit.textChanged.connect(lambda: left_panel_subtitleslist_textedit_changed(self))
    self.left_panel_subtitleslist_textedit.layout().addWidget(self.left_panel_subtitleslist_speaker_selector, 0, Qt.AlignBottom | Qt.AlignRight)
    self.left_panel_subtitleslist_bottom_panel.layout().addWidget(self.left_panel_subtitleslist_textedit)

    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row = QHBoxLayout()
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.setObjectName('subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row')
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.setSpacing(0)

    self.subtitles_panel_simplelist_properties_start_timing_label = QLabel()
    self.subtitles_panel_simplelist_properties_start_timing_label.setObjectName('subtitles_panel_simplelist_properties_start_timing_label')
    self.subtitles_panel_simplelist_properties_start_timing_label.setProperty('class', 'properties_timing_labels')
    self.subtitles_panel_simplelist_properties_start_timing_label.setAlignment(Qt.AlignLeft)
    self.subtitles_panel_simplelist_properties_start_timing_label.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    # self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.addWidget(self.subtitles_panel_simplelist_properties_start_timing_label)

    self.subtitles_panel_simplelist_properties_start_timing_qlineedit = QLineEdit()
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setProperty('class', 'qlineedit_timings')
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setObjectName('subtitles_panel_simplelist_properties_start_timing_qlineedit')
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setLayout(QVBoxLayout())
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.layout().setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.layout().setSpacing(0)
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.layout().addWidget(self.subtitles_panel_simplelist_properties_start_timing_label, 0, Qt.AlignLeft | Qt.AlignTop)    
    # self.subtitles_panel_simplelist_properties_start_timing_qlineedit.textEdited.connect(lambda: subtitles_panel_simplelist_properties_start_timing_qlineedit_text_edited(self))
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.addWidget(self.subtitles_panel_simplelist_properties_start_timing_qlineedit)

    self.subtitles_panel_simplelist_properties_duration_timing_label_row = QWidget()
    self.subtitles_panel_simplelist_properties_duration_timing_label_row.setLayout(QHBoxLayout())
    self.subtitles_panel_simplelist_properties_duration_timing_label_row.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.subtitles_panel_simplelist_properties_duration_timing_label_row.layout().setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_properties_duration_timing_label_row.layout().setSpacing(0)

    self.subtitles_panel_simplelist_properties_duration_timing_label = QLabel()
    self.subtitles_panel_simplelist_properties_duration_timing_label.setObjectName('subtitles_panel_simplelist_properties_duration_timing_label')
    self.subtitles_panel_simplelist_properties_duration_timing_label.setProperty('class', 'properties_timing_labels')
    self.subtitles_panel_simplelist_properties_duration_timing_label.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.subtitles_panel_simplelist_properties_duration_timing_label.setAlignment(Qt.AlignCenter)
    self.subtitles_panel_simplelist_properties_duration_timing_label_row.layout().addWidget(self.subtitles_panel_simplelist_properties_duration_timing_label, 0, Qt.AlignTop)
    
    self.subtitles_panel_simplelist_properties_duration_timing_lock_button = QPushButton()
    self.subtitles_panel_simplelist_properties_duration_timing_lock_button.setObjectName('subtitles_panel_simplelist_properties_duration_timing_lock_button')
    self.subtitles_panel_simplelist_properties_duration_timing_lock_button.setCheckable(True)
    self.subtitles_panel_simplelist_properties_duration_timing_lock_button.setFixedSize(QSize(20, 16))
    self.subtitles_panel_simplelist_properties_duration_timing_label_row.layout().addWidget(self.subtitles_panel_simplelist_properties_duration_timing_lock_button, 0, Qt.AlignTop)
    
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit = QLineEdit()
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setProperty('class', 'qlineedit_timings')
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setObjectName('subtitles_panel_simplelist_properties_duration_timing_qlineedit')
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setLayout(QVBoxLayout())
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setAlignment(Qt.AlignCenter)
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.layout().setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.layout().setSpacing(0)
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.layout().addWidget(self.subtitles_panel_simplelist_properties_duration_timing_label_row, 0, Qt.AlignCenter | Qt.AlignTop)
    # self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.textEdited.connect(lambda: subtitles_panel_simplelist_properties_duration_timing_qlineedit_text_edited(self))
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.addWidget(self.subtitles_panel_simplelist_properties_duration_timing_qlineedit)

    self.subtitles_panel_simplelist_properties_ending_timing_label = QLabel()
    self.subtitles_panel_simplelist_properties_ending_timing_label.setObjectName('subtitles_panel_simplelist_properties_ending_timing_label')
    self.subtitles_panel_simplelist_properties_ending_timing_label.setProperty('class', 'properties_timing_labels')
    self.subtitles_panel_simplelist_properties_ending_timing_label.setAlignment(Qt.AlignRight)
    self.subtitles_panel_simplelist_properties_ending_timing_label.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    # self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.addWidget(self.subtitles_panel_simplelist_properties_ending_timing_label)

    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit = QLineEdit()
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setProperty('class', 'qlineedit_timings')
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setObjectName('subtitles_panel_simplelist_properties_ending_timing_qlineedit')
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setLayout(QVBoxLayout())
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setAlignment(Qt.AlignRight)
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.layout().setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.layout().setSpacing(0)
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.layout().addWidget(self.subtitles_panel_simplelist_properties_ending_timing_label, 0, Qt.AlignRight | Qt.AlignTop)
    # self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.textEdited.connect(lambda: subtitles_panel_simplelist_properties_ending_timing_qlineedit_text_edited(self))
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.addWidget(self.subtitles_panel_simplelist_properties_ending_timing_qlineedit)

    self.subtitles_panel_simplelist_properties_duration_timing_lock_button.raise_()

    self.left_panel_subtitleslist_bottom_panel.layout().addLayout(self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row)

    subtitles_panel_simplelist_qsplitter.addWidget(self.left_panel_subtitleslist_bottom_panel)

    subtitles_panel_simplelist_qsplitter.setSizes([subtitles_panel_simplelist_qsplitter.height()*.8, subtitles_panel_simplelist_qsplitter.height()*.2])

    left_panel_subtitles_panel.layout().addWidget(subtitles_panel_simplelist_qsplitter)


def left_panel_subtitleslist_textedit_changed(self):
    if session.SUBTITLE['selected']:
        session.SUBTITLE['selected']['text'] = self.left_panel_subtitleslist_textedit.toPlainText()
    self.timeline_widget.update()
    self.preview_panel_player.update()


def update(self):
    self.left_panel_subtitleslist_bottom_panel.setVisible(bool(session.SUBTITLE.get('selected', False)))
    
    if session.SUBTITLE.get('selected', False):
        self.left_panel_subtitleslist_textedit.setText(session.SUBTITLE['selected']['text'])

        if self.preview_panel_player.is_paused():
            position = session.SUBTITLE.get('position', 0)
            if position > session.SUBTITLE['selected']['end'] or position < session.SUBTITLE['selected']['start']:
                position = session.SUBTITLE['selected']['start'] + ((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) / 2)
            self.preview_panel_player.set_position(position)
        
        self.left_panel_subtitleslist_speaker_selector.set_current_speaker(session.SUBTITLE['selected'].get('speaker', 'A'))

        self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setText(modules_utils.get_timeline_time_str(session.SUBTITLE['selected']['start'], ms=True))
        self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setText(modules_utils.get_timeline_time_str((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']), ms=True))
        self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setText(modules_utils.get_timeline_time_str(session.SUBTITLE['selected']['end'], ms=True))
    
    
    # if session.SUBTITLE['selected']:
        # text = session.SUBTITLE['selected']['text']
    # text = ''
        # reasons = []
        # issues = []

        # if session.CONFIG['quality_check'].get('enabled', False):
        #     _, reasons, issues = quality_check.check_subtitle(session.SUBTITLE['selected'], session.CONFIG['quality_check'])

        #     n_words = len(session.SUBTITLE['selected']['text'].replace('\n', ' ').split(' '))
        #     n_char = len(session.SUBTITLE['selected']['text'].replace('\n', '').replace(' ', ''))

        #     self.properties_information_word_counter.setStyleSheet('QLabel { background-color: ' + ('#55d43f' if 'wpm' not in issues else '#aa9e1a1a') + '}')
        #     self.properties_information_wpm.setStyleSheet('QLabel { background-color: ' + ('#bb55d43f' if 'wpm' not in issues else '#bb9e1a1a') + '}')
        #     self.properties_information_character_counter.setStyleSheet('QLabel { background-color: ' + ('#55d43f' if 'cps' not in issues else '#aa9e1a1a') + '}')
        #     self.properties_information_cps.setStyleSheet('QLabel { background-color: ' + ('#bb55d43f' if 'cps' not in issues else '#bb9e1a1a') + '}')
        #     self.properties_information_sub_duration.setStyleSheet('QLabel { background-color: ' + ('#55d43f' if 'duration' not in issues else '#bb9e1a1a') + '}')
        #     self.properties_information_number_of_lines.setStyleSheet('QLabel { background-color: ' + ('#55d43f' if 'number_of_lines' not in issues else '#bb9e1a1a') + '}')
        #     self.properties_information_cpl.setStyleSheet('QLabel { background-color: ' + ('#bb55d43f' if 'cpl' not in issues else '#bb9e1a1a') + '}')

        #     self.properties_information_word_counter.setText(str(n_words))
        #     self.properties_information_wpm.setText(str(int(n_words / ((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) / 60))))
        #     self.properties_information_character_counter.setText(str(n_char))
        #     self.properties_information_cps.setText(str(int(n_char / ((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start'])))))
        #     self.properties_information_sub_duration.setText(str(round((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']), 3)))
        #     self.properties_information_number_of_lines.setText(str(int(len(session.SUBTITLE['selected']['text'].split('\n')))))

        #     self.properties_information_reason.setVisible(bool(reasons))
        #     self.properties_information_reason.setText('\n'.join(reasons))

        #     self.properties_information_issues.setVisible(bool(issues))
        #     self.properties_information_issues.setText('\n'.join(issues))

    # def update_properties_widget(self):
    # """Function to update properties panel widgets"""
    #  update(self)
    # self.subtitles_panel_simplelist_properties.setVisible(bool(session.SUBTITLE['selected']))
    

    # if not self.left_panel_subtitleslist_textedit.hasFocus():
    #     self.left_panel_subtitleslist_textedit.setText(text)
    # self.properties_information_stats.setVisible(bool(session.SUBTITLE['selected']) and session.CONFIG.get('quality_check', {}).get('show_statistics', False))
    

def loads(self):
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

    self.subtitles_panel_simplelist_widget.layout().addWidget(self.properties_information)

    self.subtitles_panel_simplelist_qsplitter.addWidget(self.subtitles_panel_simplelist_widget)

    self.subtitles_panel_simplelist_properties = QFrame()
    self.subtitles_panel_simplelist_properties.setLayout(QVBoxLayout())
    self.subtitles_panel_simplelist_properties.layout().setContentsMargins(20, 0, 0, 0)
    self.subtitles_panel_simplelist_properties.layout().setSpacing(0)

    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_line = QHBoxLayout()
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_line.setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_line.setSpacing(0)

    self.subtitles_panel_simplelist_properties.layout().addLayout(self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_line, 1)

    self.subtitles_panel_simplelist_qsplitter.addWidget(self.subtitles_panel_simplelist_properties)



    
def show(self):
    update(self)


def hide(self):
    pass


def _changed(self, selection):
    if session.SUBTITLE.get('selected', False):
        session.SUBTITLE['selected']['speaker'] = self.left_panel_subtitleslist_speaker_selector.currentText()


def on_clicked(self):
    new_name = None
    if self.left_panel_speakers_new_name_dialog.exec() == QDialog.Accepted:
        new_name = self.left_panel_speakers_new_name_dialog.name
        session.SUBTITLE['selected']['speaker'] = new_name
        session.SPEAKERS[new_name] = {}
        self.left_panel_subtitleslist_speaker_selector.update_list(self)
        update(self)


def translate(self):
    self.subtitles_panel_simplelist_properties_start_timing_label.setText(_('left_panel_subtitleslist.start'))
    self.subtitles_panel_simplelist_properties_duration_timing_label.setText(_('left_panel_subtitleslist.duration'))
    self.subtitles_panel_simplelist_properties_ending_timing_label.setText(_('left_panel_subtitleslist.end'))
    self.left_panel_subtitleslist_speaker_selector.label.setText(_('left_panel_subtitleslist.speaker'))


class SpeakerSelector(QWidget):
    def __init__(self, parent=None):
        super(SpeakerSelector, self).__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum))
        self.setLayout(QHBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)

        self.left_container = QVBoxLayout()
        self.left_container.setContentsMargins(0, 0, 0, 0)
        self.left_container.setSpacing(0)
        
        self.top_line = QHBoxLayout()
        self.top_line.setContentsMargins(0, 0, 0, 0)
        self.top_line.setSpacing(0)

        self.label = QLabel()
        self.label.setObjectName('label')
        self.label.setProperty('class', 'properties_timing_labels')
        self.label.setAlignment(Qt.AlignLeft)
        self.label.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum))
        self.top_line.addWidget(self.label, 1)

        self.add_button = QPushButton()
        self.add_button.setObjectName('add_button')
        self.add_button.setFixedSize(20, 14)
        self.add_button.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS / 'plus_icon.svg')))
        self.add_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
        self.add_button.clicked.connect(lambda: self.add_button_clicked())
        # self.add_button.opacity = QGraphicsOpacityEffect()
        # self.add_button.setGraphicsEffect(self.add_button.opacity)
        # self.add_button.opacity.setOpacity(0)   
        self.top_line.addWidget(self.add_button, 0, Qt.AlignRight | Qt.AlignTop)

        self.left_container.layout().addLayout(self.top_line)

        class SelectorButton(QWidget):
            clicked = Signal()
            def __init__(widget, parent=None):
                super().__init__(parent)
                widget.setObjectName('speaker_selector_button')
                widget.setAttribute(Qt.WA_StyledBackground, True)
                widget.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum))
                widget.setLayout(QHBoxLayout())
                widget.layout().setContentsMargins(0, 0, 2, 0)
                widget.layout().setSpacing(0)

            def mouseReleaseEvent(widget, event):
                if event.button() == Qt.LeftButton:
                    widget.clicked.emit()
                    return
                return super().mouseReleaseEvent(event)

        self.selector_button = SelectorButton()
        self.selector_button.clicked.connect(lambda: self.selector_button_clicked())

        self.selector_button_label = QLabel()
        self.selector_button_label.setObjectName('selector_button_label')
        self.selector_button_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.selector_button_label.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
        self.selector_button.layout().addWidget(self.selector_button_label, 1, Qt.AlignLeft | Qt.AlignVCenter)

        self.selector_button_icon = QLabel()
        self.selector_button_icon.setObjectName('selector_button_icon')
        self.selector_button_icon.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
        self.selector_button_icon.setPixmap(QPixmap(str(session.PATH_SUBTITLD_GRAPHICS / 'combobox_down_arrow.svg')).scaled(16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.selector_button.layout().addWidget(self.selector_button_icon, 0)

        self.left_container.layout().addWidget(self.selector_button, 1)

        self.layout().addLayout(self.left_container, 1)

        # class SquareLabel(QLabel):
        #     def __init__(self, *args, **kwargs):
        #         super().__init__(*args, **kwargs)
        #         self.setScaledContents(True)
        #         self.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))

        #     def sizeHint(self):
        #         size = super().sizeHint()
        #         size.setWidth(size.height())
        #         return size

        #     def resizeEvent(self, event):
        #         size = event.size().height()
        #         self.setFixedSize(size, size)
        #         super().resizeEvent(event)

        self.image = QLabel()
        self.image.setObjectName('speaker_selector_image')
        self.image.setFixedSize(36, 36)
        self.image.setScaledContents(True)
        self.image.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
        self.layout().addWidget(self.image, 0)

        class list(QWidget):
            def __init__(widget, parent=None):
                super().__init__(parent)
                widget.setObjectName('speaker_selector_list')
                widget.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
                widget.setAttribute(Qt.WA_TranslucentBackground)
                widget.setAttribute(Qt.WA_StyledBackground, True)
                # widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                # widget.setFocusPolicy(Qt.NoFocus)
                widget.setLayout(QVBoxLayout())
                widget.layout().setContentsMargins(0, 0, 0, 0)
                widget.layout().setSpacing(0)

                widget.list_widget = QListWidget()
                widget.list_widget.setObjectName('speaker_selector_list_widget')
                widget.list_widget.setFocusPolicy(Qt.NoFocus)
                widget.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                widget.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
                widget.list_widget.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
                widget.list_widget.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
                widget.list_widget.setMaximumHeight(400)
                widget.list_widget.itemClicked.connect(lambda item: widget.parent().speaker_selected(item))
                widget.layout().addWidget(widget.list_widget)
                
            def set_position(widget, point):
                widget.adjustSize()
                
                x = point.x() - (widget.width() / 2)
                if x < 0:
                    x = 0
                elif x + widget.width() > widget.window().screen().geometry().width():
                    x = widget.window().screen().geometry().width() - widget.width()

                widget.move(int(x), point.y() + 20)

            def showEvent(widget, event):
                widget.list_widget.clear()
                widget.list_widget.setMinimumWidth(widget.parent().width())
                for speaker_name, speaker_data in session.SPEAKERS.items():
                    item = QListWidgetItem()
                    wdg = QWidget()
                    wdg.setLayout(QHBoxLayout())
                    wdg.layout().setContentsMargins(6, 0, 0, 0)
                    wdg.layout().setSpacing(5)
                    wdg.setProperty('speaker_name', speaker_name)
                    label = QLabel(speaker_name)
                    label.setObjectName('speaker_selector_list_item_label')
                    label.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum))
                    wdg.layout().addWidget(label, 1)
                    icon_label = QLabel()
                    icon_label.setObjectName('speaker_selector_list_item_icon')
                    icon_label.setFixedSize(36, 36)
                    icon_label.setScaledContents(True)
                    icon_label.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
                    if speaker_data.get('image', None):
                        icon_label.setPixmap(QPixmap.fromImage(speaker_data['image']).scaled(36, 36))
                    else:
                        icon_label.setPixmap(QPixmap(str(session.PATH_SUBTITLD_GRAPHICS / 'unknown_speaker.svg')).scaled(36, 36))
                        if not self.window().left_panel_speakers_image_test_thread.isRunning():
                            self.window().left_panel_speakers_image_test_thread.name = speaker_name
                            self.window().left_panel_speakers_image_test_thread.start()
                    wdg.layout().addWidget(icon_label, 0)
                    item.setSizeHint(wdg.sizeHint())
                    widget.list_widget.addItem(item)
                    widget.list_widget.setItemWidget(item, wdg)
                    # pixmap = QPixmap(str(session.PATH_SUBTITLD_GRAPHICS / 'unknown_speaker.svg')).scaled(32, 32)
                    # if session.SPEAKERS[speaker_name].get('image', None):
                    #     pixmap = QPixmap.fromImage(session.SPEAKERS[speaker_name]['image']).scaled(32, 32)
                    # icon = QIcon(pixmap)
                    # item.setIcon(icon)
                    # widget.list_widget.addItem(item)

                widget.adjustSize()

                pos = widget.parent().mapToGlobal(QPoint(0, 0))
                x, y = pos.x(), pos.y()
                x += widget.parent().width()
                y += widget.parent().height() / 2

                x -= widget.width() + 1
                y -= widget.height() / 2

                widget.move(int(x), int(y))

                return super().showEvent(event)

        self.selector_list = list(parent=self)
        self.selector_list.hide()

        class NewNameDialog(QDialog):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.setWindowTitle("Enter Your Name")
                self.name = None

                layout = QVBoxLayout(self)

                self.label = QLabel("Please enter your name:")
                self.input = QLineEdit()
                self.ok_button = QPushButton("OK")
                self.cancel_button = QPushButton("Cancel")

                layout.addWidget(self.label)
                layout.addWidget(self.input)
                layout.addWidget(self.ok_button)
                layout.addWidget(self.cancel_button)

                self.ok_button.clicked.connect(self.accept)
                self.cancel_button.clicked.connect(self.reject)

            def accept(self):
                self.name = self.input.text().strip()
                super().accept()

        self.new_name_dialog = NewNameDialog(parent=self)

    ## start animation on mouse hover:
    def enterEvent(self, event):
        # self.add_button.opacity.setOpacity(1)  
        super().enterEvent(event)

    def leaveEvent(self, event):
        # self.add_button.opacity.setOpacity(0)  
        super().leaveEvent(event)

    def speaker_selected(self, item):
        speaker = self.selector_list.list_widget.itemWidget(item).property('speaker_name')
        session.SUBTITLE['selected']['speaker'] = speaker
        self.set_current_speaker(speaker)
        self.selector_list.hide()

    def set_current_speaker(self, speaker_name):
        self.selector_button_label.setText(speaker_name)
        if session.SPEAKERS.get(speaker_name, {}).get('image', None):
            self.image.setPixmap(QPixmap.fromImage(session.SPEAKERS[speaker_name]['image']))
        else:
            self.image.setPixmap(QPixmap(str(session.PATH_SUBTITLD_GRAPHICS / 'unknown_speaker.svg')))
            if not self.window().left_panel_speakers_image_test_thread.isRunning():
                self.window().left_panel_speakers_image_test_thread.name = speaker_name
                self.window().left_panel_speakers_image_test_thread.start()

    def selector_button_clicked(self):
        if self.selector_list.isVisible():
            self.selector_list.hide()
        else:
            self.selector_list.show()

    def add_button_clicked(self):
        new_name = None
        if self.new_name_dialog.exec() == QDialog.Accepted:
            new_name = self.new_name_dialog.name
            if new_name:
                session.SUBTITLE['selected']['speaker'] = new_name
                session.SPEAKERS[new_name] = {}
                self.set_current_speaker(new_name)