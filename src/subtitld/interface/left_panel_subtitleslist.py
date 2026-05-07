from PySide6.QtWidgets import QVBoxLayout, QWidget, QHBoxLayout, QSplitter, QPushButton, QListView, QStyledItemDelegate, QFrame, QLabel, QTextEdit, QSizePolicy, QLineEdit, QStyle, QDialog, QListWidget, QListWidgetItem, QAbstractScrollArea, QGraphicsOpacityEffect
from PySide6.QtCore import Qt, QAbstractListModel, QRect, QMargins, QSize, Signal, QPoint, QPropertyAnimation, QEasingCurve, QObject, QEvent
from PySide6.QtGui import QColor, QFontMetrics, QFont, QIcon, QPixmap

from subtitld.interface import utils
from subtitld.interface import left_panel
from subtitld.interface import preview_panel
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import subtitles
from subtitld.modules import history
from subtitld.modules import utils as modules_utils
from subtitld.modules import quality_check


class _TextEditHistoryFilter(QObject):
    """Pushes one undo snapshot per editing session on the bound text edit.
    Heuristic: snapshot on the first keystroke after the editor gains focus
    (so a focus-only click doesn't waste a slot, and a continuous typing
    burst stays as a single undo entry until the user moves focus elsewhere
    and comes back). Also marks the document as unsaved on first edit since
    the textedit handlers mutate `selected['text']` directly without going
    through the `subtitles.change_subtitle_text` helper."""

    def __init__(self, window, textedit):
        super().__init__(textedit)
        self._window = window
        self._textedit = textedit
        self._needs_snapshot = True

    def eventFilter(self, obj, event):
        et = event.type()
        if obj is self._textedit:
            if et == QEvent.FocusIn:
                self._needs_snapshot = True
            elif et in (QEvent.KeyPress, QEvent.InputMethod) and self._needs_snapshot:
                if self._textedit.isReadOnly():
                    return False
                history.history_append()
                session.set_unsaved(True)
                self._needs_snapshot = False
        return False



TEXT_ALIGNMENTS = {
    'left' : Qt.AlignLeft,
    'center' : Qt.AlignHCenter,
    'right' : Qt.AlignRight
}

class subtitles_panel_qlistwidget(QListView):
    def __init__(widget, parent=None):
        super(subtitles_panel_qlistwidget, widget).__init__(parent)

        class model(QAbstractListModel):
            def __init__(self, *args, subs=None, **kwargs):
                super(model, self).__init__(*args, **kwargs)
                # self.subtitles = subs or []

            def data(self, index, role):
                if role == Qt.DisplayRole:
                    return session.SUBTITLE['segments'][index.row()] #['text']
            
            # def translated_data(self, index, role):
            #     if role == Qt.DisplayRole:
            #         return session.SUBTITLE['segments'][index.row()].get('translations', {}).get(session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us'), '')

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
                row_text = index.data(Qt.DisplayRole)['text']
                width = option.rect.width()
                if session.CONFIG['translation'].get('engine_options', {}).get('show_translations', False):
                    translated_text = index.data(Qt.DisplayRole).get('translations', {}).get(session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us'), '')
                    height_o = QFontMetrics(QFont('Montserrat', 10)).boundingRect(QRect(0, 0, (width/2) - (20 + 10 + self.get_number_width(index) + 10 + 10), 100), Qt.TextWordWrap, row_text).height()
                    height_t = QFontMetrics(QFont('Montserrat', 10)).boundingRect(QRect(width/2, 0, (width/2) - (20 + 10 + self.get_number_width(index) + 10 + 10), 100), Qt.TextWordWrap, translated_text).height()
                    height = max(height_o, height_t)
                else:
                    height = QFontMetrics(QFont('Montserrat', 10)).boundingRect(QRect(0, 0, width - (20 + 10 + self.get_number_width(index) + 10 + 10), 100), Qt.TextWordWrap, row_text).height()
                return height

            def paint(self, painter, option, index):
                segment = index.data(Qt.DisplayRole)
                row_text = segment['text']
                number_width = self.get_number_width(index)
                

                if option.state & QStyle.State_Selected:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(session.CONFIG.get('subtitle_list', {}).get('background_color', '#802e3e4c')))
                    painter.drawRect(option.rect)
                    # painter.setBrush(QColor('#55d43f'))
                    # painter.drawRect(QRect(0, option.rect.y(), 20, option.rect.height()))

                sub_is_ok = True
                if session.CONFIG.get('quality_check', {}).get('enabled', False):
                    sub_is_ok, _, _ = quality_check.check_subtitle(session.SUBTITLE['segments'][index.row()])

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

                painter.setPen(QColor(session.CONFIG.get('subtitle_list', {}).get('text_color', '#ffffff') if sub_is_ok else session.CONFIG.get('subtitle_list', {}).get('text_warning_color', '#9e1a1a')))
                painter.setFont(QFont('Montserrat', 10))

                qalignment = TEXT_ALIGNMENTS[session.CONFIG['default_values'].get('subtitle_alignment', 'left')]

                if session.CONFIG['translation'].get('engine_options', {}).get('show_translations', False):
                    original_rect = text_rect.marginsRemoved(QMargins(0, 0, text_rect.width()*.5, 0))
                    original_rect = original_rect.marginsRemoved(QMargins(10, 10, 10, 10))
                    painter.drawText(original_rect, qalignment | Qt.AlignTop | Qt.TextWordWrap, row_text)
                    translated_rect = text_rect.marginsRemoved(QMargins(text_rect.width()*.5, 0, 0, 0))
                    painter.setPen(QColor(session.CONFIG.get('subtitle_list', {}).get('text_warning_color', '#0dffffff')))
                    painter.drawLine(translated_rect.topLeft(), translated_rect.bottomLeft())
                    painter.setPen(QColor(session.CONFIG.get('subtitle_list', {}).get('text_color', '#ffffff') if sub_is_ok else session.CONFIG.get('subtitle_list', {}).get('text_warning_color', '#9e1a1a')))
                    translated_rect = translated_rect.marginsRemoved(QMargins(10, 10, 10, 10))
                    translated_text = segment.get('translations', {}).get(session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us'), '')
                    painter.drawText(translated_rect, qalignment | Qt.AlignTop | Qt.TextWordWrap, translated_text)   
                else:
                    original_rect = text_rect.marginsRemoved(QMargins(10, 10, 10, 10))
                    painter.drawText(original_rect, qalignment | Qt.AlignTop | Qt.TextWordWrap, row_text)

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

        if session.SUBTITLE.get('selected', None) is not None:
            widget.setCurrentIndex(widget.model.get_index(session.SUBTITLE['selected']))

        # widget.update_properties_widget(widget.window())
    
    def item_selected(widget, index):
        session.SUBTITLE['selected'] = session.SUBTITLE['segments'][index.row()]
        update(widget.window())

def load(self):
    tab_name = 'subtitles'
    
    left_panel_subtitles_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )
    
    left_panel_subtitles_panel.layout().setContentsMargins(0, 0, 0, 0)


    self.subtitles_panel_empty_state = QWidget()
    self.subtitles_panel_empty_state.setObjectName('subtitles_panel_empty_state')
    self.subtitles_panel_empty_state.setLayout(QVBoxLayout())
    self.subtitles_panel_empty_state.layout().setAlignment(Qt.AlignCenter)
    self.subtitles_panel_empty_state.layout().setSpacing(12)

    self.subtitles_panel_empty_state_label = QLabel()
    self.subtitles_panel_empty_state_label.setObjectName('subtitles_panel_empty_state_label')
    self.subtitles_panel_empty_state_label.setAlignment(Qt.AlignCenter)
    self.subtitles_panel_empty_state_label.setWordWrap(True)
    self.subtitles_panel_empty_state.layout().addWidget(self.subtitles_panel_empty_state_label)

    self.subtitles_panel_empty_state_button = QPushButton()
    self.subtitles_panel_empty_state_button.setObjectName('subtitles_panel_empty_state_button')
    self.subtitles_panel_empty_state_button.setProperty('class', 'primary')
    self.subtitles_panel_empty_state_button.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS / 'add_subtitle_icon.svg')))
    self.subtitles_panel_empty_state_button.setIconSize(QSize(20, 20))
    self.subtitles_panel_empty_state_button.setFixedSize(40, 40)
    self.subtitles_panel_empty_state_button.clicked.connect(lambda: subtitles_panel_empty_state_button_clicked(self))
    self.subtitles_panel_empty_state.layout().addWidget(self.subtitles_panel_empty_state_button, 0, Qt.AlignCenter)

    left_panel_subtitles_panel.layout().addWidget(self.subtitles_panel_empty_state)

    self.subtitles_panel_simplelist_qsplitter = QSplitter(Qt.Vertical)
    subtitles_panel_simplelist_qsplitter = self.subtitles_panel_simplelist_qsplitter

    self.subtitles_panel_qlistwidget = subtitles_panel_qlistwidget()
    subtitles_panel_simplelist_qsplitter.addWidget(self.subtitles_panel_qlistwidget)

    self.left_panel_subtitleslist_bottom_panel = QWidget()
    self.left_panel_subtitleslist_bottom_panel.setObjectName('left_panel_subtitleslist_bottom_panel')
    self.left_panel_subtitleslist_bottom_panel.setLayout(QVBoxLayout())
    self.left_panel_subtitleslist_bottom_panel.layout().setContentsMargins(0, 0, 0, 0)
    self.left_panel_subtitleslist_bottom_panel.layout().setSpacing(0)
    subtitles_panel_simplelist_qsplitter.addWidget(self.left_panel_subtitleslist_bottom_panel)

    self.left_panel_subtitleslist_textedit = QTextEdit()
    self.left_panel_subtitleslist_textedit.setObjectName('left_panel_subtitleslist_textedit')
    self.left_panel_subtitleslist_textedit.setAcceptRichText(False)
    self.left_panel_subtitleslist_textedit.textChanged.connect(lambda: left_panel_subtitleslist_textedit_changed(self))
    self.left_panel_subtitleslist_textedit.installEventFilter(_TextEditHistoryFilter(self, self.left_panel_subtitleslist_textedit))
    self.left_panel_subtitleslist_bottom_panel.layout().addWidget(self.left_panel_subtitleslist_textedit)

    self.left_panel_subtitleslist_translation_textedit = QTextEdit()
    self.left_panel_subtitleslist_translation_textedit.setObjectName('left_panel_subtitleslist_translation_textedit')
    self.left_panel_subtitleslist_translation_textedit.setAcceptRichText(False)
    self.left_panel_subtitleslist_translation_textedit.textChanged.connect(lambda: left_panel_subtitleslist_translation_textedit_changed(self))
    self.left_panel_subtitleslist_translation_textedit.installEventFilter(_TextEditHistoryFilter(self, self.left_panel_subtitleslist_translation_textedit))
    self.left_panel_subtitleslist_bottom_panel.layout().addWidget(self.left_panel_subtitleslist_translation_textedit)

    self.left_panel_subtitleslist_textedit_bottom_line = QHBoxLayout()
    self.left_panel_subtitleslist_textedit_bottom_line.setContentsMargins(0, 0, 0, 0)
    self.left_panel_subtitleslist_textedit_bottom_line.setSpacing(0)
    self.left_panel_subtitleslist_bottom_panel.layout().addLayout(self.left_panel_subtitleslist_textedit_bottom_line)

    self.properties_information = QWidget()
    self.properties_information.setObjectName('properties_information')
    self.properties_information.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum))
    self.properties_information.setLayout(QVBoxLayout())
    self.properties_information.layout().setContentsMargins(5, 5, 5, 5)
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
    self.properties_information_cpl.setAlignment(Qt.AlignCenter)
    self.properties_information_cpl.setObjectName('properties_information_cpl')
    self.properties_information_stats.layout().addWidget(self.properties_information_cpl)

    self.properties_information.layout().addWidget(self.properties_information_stats)

    # self.properties_information_reason = QLabel()
    # self.properties_information_reason.setObjectName('properties_information_reason')
    # self.properties_information.layout().addWidget(self.properties_information_reason)

    self.left_panel_subtitleslist_textedit_bottom_line.addWidget(self.properties_information, 1, Qt.AlignLeft | Qt.AlignBottom)

    # self.subtitles_panel_simplelist_qsplitter.addWidget(self.subtitles_panel_simplelist_widget)

    # self.subtitles_panel_simplelist_properties = QFrame()
    # self.subtitles_panel_simplelist_properties.setLayout(QVBoxLayout())
    # self.subtitles_panel_simplelist_properties.layout().setContentsMargins(20, 0, 0, 0)
    # self.subtitles_panel_simplelist_properties.layout().setSpacing(0)

    # self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_line = QHBoxLayout()
    # self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_line.setContentsMargins(0, 0, 0, 0)
    # self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_line.setSpacing(0)

    # self.subtitles_panel_simplelist_properties.layout().addLayout(self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_line, 1)

    # self.subtitles_panel_simplelist_qsplitter.addWidget(self.subtitles_panel_simplelist_properties)

    self.left_panel_subtitleslist_textedit_bottom_line.addStretch()
    
    self.left_panel_subtitleslist_regenerate_dub_button = QPushButton()
    self.left_panel_subtitleslist_regenerate_dub_button.setObjectName('left_panel_subtitleslist_regenerate_dub_button')
    self.left_panel_subtitleslist_regenerate_dub_button.setFixedHeight(36)
    self.left_panel_subtitleslist_regenerate_dub_button.setIconSize(QSize(16, 16))
    self.left_panel_subtitleslist_regenerate_dub_button.setCursor(Qt.PointingHandCursor)
    self.left_panel_subtitleslist_regenerate_dub_button.setVisible(False)
    self.left_panel_subtitleslist_regenerate_dub_button.clicked.connect(lambda: regenerate_dub_for_selected(self))
    self.left_panel_subtitleslist_textedit_bottom_line.addWidget(self.left_panel_subtitleslist_regenerate_dub_button, 0, Qt.AlignRight | Qt.AlignBottom)

    self.left_panel_subtitleslist_speaker_selector = SpeakerSelector()
    self.left_panel_subtitleslist_speaker_selector.setObjectName('left_panel_subtitleslist_speaker_selector')
    self.left_panel_subtitleslist_textedit_bottom_line.addWidget(self.left_panel_subtitleslist_speaker_selector, 0, Qt.AlignRight | Qt.AlignBottom)

    # self.left_panel_subtitleslist_textedit.layout().addLayout(self.left_panel_subtitleslist_textedit_bottom_line)
    
    # self.left_panel_subtitleslist_bottom_panel.layout().addWidget(self.left_panel_subtitleslist_textedit)

    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row = QHBoxLayout()
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.setObjectName('subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row')
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.setSpacing(0)

    self.subtitles_panel_simplelist_properties_start_timing_label = QLabel()
    self.subtitles_panel_simplelist_properties_start_timing_label.setObjectName('subtitles_panel_simplelist_properties_start_timing_label')
    self.subtitles_panel_simplelist_properties_start_timing_label.setProperty('class', 'properties_timing_labels')
    self.subtitles_panel_simplelist_properties_start_timing_label.setAlignment(Qt.AlignLeft)
    # self.subtitles_panel_simplelist_properties_start_timing_label.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    # self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.addWidget(self.subtitles_panel_simplelist_properties_start_timing_label)

    self.subtitles_panel_simplelist_properties_start_timing_qlineedit = QLineEdit()
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setProperty('class', 'qlineedit_timings')
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setObjectName('subtitles_panel_simplelist_properties_start_timing_qlineedit')
    # self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setLayout(QVBoxLayout())
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.layout().setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.layout().setSpacing(0)
    self.subtitles_panel_simplelist_properties_start_timing_qlineedit.layout().addWidget(self.subtitles_panel_simplelist_properties_start_timing_label, 0, Qt.AlignLeft | Qt.AlignTop)    
    # self.subtitles_panel_simplelist_properties_start_timing_qlineedit.textEdited.connect(lambda: subtitles_panel_simplelist_properties_start_timing_qlineedit_text_edited(self))
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.addWidget(self.subtitles_panel_simplelist_properties_start_timing_qlineedit, 1)

    self.subtitles_panel_simplelist_properties_duration_timing_label_row = QWidget()
    self.subtitles_panel_simplelist_properties_duration_timing_label_row.setLayout(QHBoxLayout())
    # self.subtitles_panel_simplelist_properties_duration_timing_label_row.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.subtitles_panel_simplelist_properties_duration_timing_label_row.layout().setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_properties_duration_timing_label_row.layout().setSpacing(0)

    self.subtitles_panel_simplelist_properties_duration_timing_label = QLabel()
    self.subtitles_panel_simplelist_properties_duration_timing_label.setObjectName('subtitles_panel_simplelist_properties_duration_timing_label')
    self.subtitles_panel_simplelist_properties_duration_timing_label.setProperty('class', 'properties_timing_labels')
    # self.subtitles_panel_simplelist_properties_duration_timing_label.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
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
    # self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setLayout(QVBoxLayout())
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setAlignment(Qt.AlignCenter)
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.layout().setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.layout().setSpacing(0)
    self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.layout().addWidget(self.subtitles_panel_simplelist_properties_duration_timing_label_row, 0, Qt.AlignCenter | Qt.AlignTop)
    # self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.textEdited.connect(lambda: subtitles_panel_simplelist_properties_duration_timing_qlineedit_text_edited(self))
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.addWidget(self.subtitles_panel_simplelist_properties_duration_timing_qlineedit, 1)

    self.subtitles_panel_simplelist_properties_ending_timing_label = QLabel()
    self.subtitles_panel_simplelist_properties_ending_timing_label.setObjectName('subtitles_panel_simplelist_properties_ending_timing_label')
    self.subtitles_panel_simplelist_properties_ending_timing_label.setProperty('class', 'properties_timing_labels')
    self.subtitles_panel_simplelist_properties_ending_timing_label.setAlignment(Qt.AlignRight)
    # self.subtitles_panel_simplelist_properties_ending_timing_label.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    # self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.addWidget(self.subtitles_panel_simplelist_properties_ending_timing_label)

    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit = QLineEdit()
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setProperty('class', 'qlineedit_timings')
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setObjectName('subtitles_panel_simplelist_properties_ending_timing_qlineedit')
    # self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setLayout(QVBoxLayout())
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setAlignment(Qt.AlignRight)
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.layout().setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.layout().setSpacing(0)
    self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.layout().addWidget(self.subtitles_panel_simplelist_properties_ending_timing_label, 0, Qt.AlignRight | Qt.AlignTop)
    # self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.textEdited.connect(lambda: subtitles_panel_simplelist_properties_ending_timing_qlineedit_text_edited(self))
    self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row.addWidget(self.subtitles_panel_simplelist_properties_ending_timing_qlineedit, 1)

    self.subtitles_panel_simplelist_properties_duration_timing_lock_button.raise_()

    self.left_panel_subtitleslist_bottom_panel.layout().addLayout(self.subtitles_panel_simplelist_left_panel_subtitleslist_textedit_timings_row)

    subtitles_panel_simplelist_qsplitter.setSizes([subtitles_panel_simplelist_qsplitter.height()*.8, subtitles_panel_simplelist_qsplitter.height()*.2])

    left_panel_subtitles_panel.layout().addWidget(subtitles_panel_simplelist_qsplitter)


    self.left_panel_subtitleslist_new_name_dialog = new_speaker_name_dialog(self, 'New speaker')


def subtitles_panel_empty_state_button_clicked(self):
    """Add the project's first subtitle at 5s with the configured default
    duration, select it, and refresh the timeline + panel so the empty-state
    UI is replaced by the regular subtitle list."""
    duration = session.CONFIG['default_new_subtitle_duration']
    subtitles.add_subtitle(position=5.0, duration=duration)
    if session.SUBTITLE.get('segments'):
        session.SUBTITLE['selected'] = session.SUBTITLE['segments'][0]
    self.timeline_widget.update()
    update(self)
    session.set_unsaved()


def left_panel_subtitleslist_textedit_changed(self):
    if 'selected' in session.SUBTITLE and session.SUBTITLE['selected']:
        session.SUBTITLE['selected']['text'] = self.left_panel_subtitleslist_textedit.toPlainText()
        session.set_unsaved(True)
    self.timeline_widget.update()
    self.preview_panel_player.update()


def left_panel_subtitleslist_translation_textedit_changed(self):
    if 'selected' in session.SUBTITLE and session.SUBTITLE['selected']:
        if not 'translations' in session.SUBTITLE['selected']:
            session.SUBTITLE['selected']['translations'] = {}
        session.SUBTITLE['selected']['translations'][session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us')] = self.left_panel_subtitleslist_translation_textedit.toPlainText()
        session.set_unsaved(True)
    self.timeline_widget.update()
    self.preview_panel_player.update()


def update(self):
    self.subtitles_panel_qlistwidget.update_content()

    has_segments = bool(session.SUBTITLE.get('segments'))
    self.subtitles_panel_empty_state.setVisible(not has_segments)
    self.subtitles_panel_simplelist_qsplitter.setVisible(has_segments)

    self.left_panel_subtitleslist_bottom_panel.setVisible(bool(session.SUBTITLE.get('selected', False)))
    self.properties_information.setVisible(bool(session.SUBTITLE.get('selected', False)) and session.CONFIG.get('quality_check', {}).get('show_statistics', False))
    
    if session.SUBTITLE.get('selected', None) is None:
        self.subtitles_panel_qlistwidget.clearSelection()
    else:
        # Block textChanged while we sync the editor to the model — otherwise
        # the textedit_changed handler fires and would (a) mark the document
        # unsaved on a no-op refresh and (b) overwrite `selected['text']`
        # with the same value but at the wrong moment in flow.
        self.left_panel_subtitleslist_textedit.blockSignals(True)
        self.left_panel_subtitleslist_textedit.setText(session.SUBTITLE['selected']['text'])
        self.left_panel_subtitleslist_textedit.blockSignals(False)

        self.left_panel_subtitleslist_translation_textedit.setVisible(session.CONFIG['translation'].get('engine_options', {}).get('show_translations', False))
        self.left_panel_subtitleslist_translation_textedit.blockSignals(True)
        self.left_panel_subtitleslist_translation_textedit.setText(session.SUBTITLE['selected'].get('translations', {}).get(session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us'), ''))
        self.left_panel_subtitleslist_translation_textedit.blockSignals(False)

        qalignment = TEXT_ALIGNMENTS[session.CONFIG['default_values'].get('subtitle_alignment', 'left')]
        self.left_panel_subtitleslist_textedit.setAlignment(qalignment)
        self.left_panel_subtitleslist_translation_textedit.setAlignment(qalignment)

        if self.preview_panel_player.is_paused():
            position = session.SUBTITLE.get('position', 0)
            if position > session.SUBTITLE['selected']['end'] or position < session.SUBTITLE['selected']['start']:
                position = session.SUBTITLE['selected']['start'] + ((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) / 2)
                if session.CONFIG.get('repeat_activated', False):
                    duration = session.CONFIG.get('playback_repeat_duration', 10.0)
                    times = session.CONFIG.get('playback_repeat_times', 1)
                    session.REPEAT_DURATION_BUFFER = [[position, position + duration] for _ in range(times)]
            self.preview_panel_player.set_position(position)
        
        self.left_panel_subtitleslist_speaker_selector.set_current_speaker(session.SUBTITLE['selected'].get('speaker', 'A'))

        dubbing_enabled = bool(session.CONFIG.get('dubbing', {}).get('enabled', False))
        has_dub = bool(session.SUBTITLE['selected'].get('dubbing'))
        self.left_panel_subtitleslist_regenerate_dub_button.setVisible(dubbing_enabled and has_dub)

        self.subtitles_panel_simplelist_properties_start_timing_qlineedit.setText(modules_utils.get_timeline_time_str(session.SUBTITLE['selected']['start'], ms=True))
        self.subtitles_panel_simplelist_properties_duration_timing_qlineedit.setText(modules_utils.get_timeline_time_str((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']), ms=True))
        self.subtitles_panel_simplelist_properties_ending_timing_qlineedit.setText(modules_utils.get_timeline_time_str(session.SUBTITLE['selected']['end'], ms=True))
                
        show_statistics = session.CONFIG.get('quality_check', {}).get('show_statistics', False)
        quality_check_enabled = session.CONFIG.get('quality_check', {}).get('enabled', False)

        if show_statistics:
            sel = session.SUBTITLE['selected']
            duration = sel['end'] - sel['start']
            n_words = len(sel['text'].replace('\n', ' ').split(' '))
            n_char = len(sel['text'].replace('\n', '').replace(' ', ''))

            self.properties_information_word_counter.setText(str(n_words))
            self.properties_information_wpm.setText(str(int(n_words / (duration / 60))) if duration > 0 else '0')
            self.properties_information_character_counter.setText(str(n_char))
            self.properties_information_cps.setText(str(int(n_char / duration)) if duration > 0 else '0')
            self.properties_information_sub_duration.setText(str(round(duration, 3)))
            self.properties_information_number_of_lines.setText(str(int(len(sel['text'].split('\n')))))
            self.properties_information_cpl.setText(str(int(n_char / len(sel['text'].split('\n')))) if len(sel['text'].split('\n')) > 0 else '0')

            stat_badges = (
                self.properties_information_word_counter,
                self.properties_information_wpm,
                self.properties_information_character_counter,
                self.properties_information_cps,
                self.properties_information_sub_duration,
                self.properties_information_number_of_lines,
                self.properties_information_cpl,
            )

            if quality_check_enabled:
                _, reasons, issues = quality_check.check_subtitle(sel)

                ok_solid = '#5a55d43f'
                ok_soft = '#3355d43f'
                bad_solid = '#a09e1a1a'
                bad_soft = '#7a9e1a1a'

                self.properties_information_word_counter.setStyleSheet('QLabel#properties_information_word_counter { background-color: ' + (ok_solid if 'wpm' not in issues else bad_solid) + '; color: #ffffff; }')
                self.properties_information_wpm.setStyleSheet('QLabel#properties_information_wpm { background-color: ' + (ok_soft if 'wpm' not in issues else bad_soft) + '; color: #ffffff; }')
                self.properties_information_character_counter.setStyleSheet('QLabel#properties_information_character_counter { background-color: ' + (ok_solid if 'cps' not in issues else bad_solid) + '; color: #ffffff; }')
                self.properties_information_cps.setStyleSheet('QLabel#properties_information_cps { background-color: ' + (ok_soft if 'cps' not in issues else bad_soft) + '; color: #ffffff; }')
                self.properties_information_sub_duration.setStyleSheet('QLabel#properties_information_sub_duration { background-color: ' + (ok_solid if 'duration' not in issues else bad_solid) + '; color: #ffffff; }')
                self.properties_information_number_of_lines.setStyleSheet('QLabel#properties_information_number_of_lines { background-color: ' + (ok_solid if 'number_of_lines' not in issues else bad_solid) + '; color: #ffffff; }')
                self.properties_information_cpl.setStyleSheet('QLabel#properties_information_cpl { background-color: ' + (ok_soft if 'cpl' not in issues else bad_soft) + '; color: #ffffff; }')

                self.properties_information.setToolTip('\n'.join(reasons))
            else:
                for badge in stat_badges:
                    badge.setStyleSheet('')
                    badge.style().unpolish(badge)
                    badge.style().polish(badge)
                    badge.update()
                self.properties_information.setToolTip('')

            # self.properties_information_reason.setVisible(bool(reasons))
            # self.properties_information_reason.setText('\n'.join(reasons))

            # self.properties_information_issues.setVisible(bool(issues))
            # self.properties_information_issues.setText('\n'.join(issues))

    # def update_properties_widget(self):
    # """Function to update properties panel widgets"""
    #  update(self)
    # self.subtitles_panel_simplelist_properties.setVisible(bool(session.SUBTITLE['selected']))
    

    # if not self.left_panel_subtitleslist_textedit.hasFocus():
    #     self.left_panel_subtitleslist_textedit.setText(text)
    # self.properties_information_stats.setVisible(bool(session.SUBTITLE['selected']) and session.CONFIG.get('quality_check', {}).get('show_statistics', False))
    
    
def show(self):    
    update(self)


def hide(self):
    pass


def _changed(self, selection):
    if session.SUBTITLE.get('selected', False):
        history.history_append()
        session.SUBTITLE['selected']['speaker'] = self.left_panel_subtitleslist_speaker_selector.currentText()
        session.set_unsaved(True)


def regenerate_dub_for_selected(self):
    """Regenerate the dub for the currently-selected subtitle using the latest
    text/speaker settings. Only meaningful when the subtitle already has a dub
    take; the button that triggers this is only visible in that case."""
    import secrets
    selected = session.SUBTITLE.get('selected')
    if not selected or not selected.get('dubbing'):
        return
    from subtitld.interface.left_panel_dubbing import EdgeTTSEngine
    speaker_name = selected.get('speaker', 'A')
    speaker_dubbing = session.SPEAKERS.get(speaker_name, {}).get('dubbing', {})
    overrides = selected.get('dubbing_options', {})
    selected['locked'] = True
    EdgeTTSEngine.generate_speeches([{
        'uid': secrets.token_hex(4),
        'text': selected['text'],
        'speaker': speaker_name,
        'start': selected['start'],
        'end': selected['end'],
        'voice': overrides.get('voice') or speaker_dubbing.get('voice', ''),
        'rate': overrides.get('rate', speaker_dubbing.get('rate', 0)),
        'pitch': overrides.get('pitch', speaker_dubbing.get('pitch', 0)),
    }])
    self.timeline_widget.update()


def on_clicked(self):
    new_name = None
    if self.left_panel_speakers_new_name_dialog.exec() == QDialog.Accepted:
        new_name = self.left_panel_speakers_new_name_dialog.name
        history.history_append()
        session.SUBTITLE['selected']['speaker'] = new_name
        session.SPEAKERS[new_name] = {}
        session.set_unsaved(True)
        self.left_panel_subtitleslist_speaker_selector.update_list(self)
        update(self)


def translate(self):
    self.subtitles_panel_simplelist_properties_start_timing_label.setText(_('left_panel_subtitleslist.start'))
    self.subtitles_panel_simplelist_properties_duration_timing_label.setText(_('left_panel_subtitleslist.duration'))
    self.subtitles_panel_simplelist_properties_ending_timing_label.setText(_('left_panel_subtitleslist.end'))
    self.left_panel_subtitleslist_speaker_selector.label.setText(_('left_panel_subtitleslist.speaker'))
    self.left_panel_subtitleslist_regenerate_dub_button.setToolTip(_('left_panel_subtitleslist.regenerate_dub_tooltip'))
    self.subtitles_panel_empty_state_label.setText(_('left_panel_subtitleslist.empty_state_label'))
    self.subtitles_panel_empty_state_button.setToolTip(_('left_panel_subtitleslist.empty_state_button'))

    # self.left_panel_speakers_new_name_dialog.set_title(_('subtitles_panel_widget_speakers.new_speaker'))
    # self.left_panel_speakers_new_name_dialog.input_label.setText(_('subtitles_panel_widget_speakers.enter_speaker_name'))


class SpeakerSelector(QWidget):
    def __init__(self, parent=None):
        super(SpeakerSelector, self).__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum))
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
        history.history_append()
        session.SUBTITLE['selected']['speaker'] = speaker
        session.set_unsaved(True)
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
        self.window().left_panel_subtitleslist_new_name_dialog.input.setText('')
        value = self.window().left_panel_subtitleslist_new_name_dialog.exec_and_get_values()
        
        if value:
            new_name = value.strip()
        else:
            pass
        
        subtitles_names = [subtitle.get('speaker', 'A') for subtitle in session.SUBTITLE['segments']]
        if new_name and new_name not in session.SPEAKERS and not new_name in subtitles_names:
            history.history_append()
            session.SUBTITLE['selected']['speaker'] = new_name
            session.SPEAKERS[new_name] = {}
            session.set_unsaved(True)
            self.set_current_speaker(new_name)


class new_speaker_name_dialog(utils.SimpleDialog):
    def __init__(self, parent=None, title=''):
        super().__init__(parent, title)

        self.input_line = QWidget()
        self.input_line.setLayout(QHBoxLayout())
        self.input_line.layout().setContentsMargins(0, 0, 0, 0)

        self.input_label = QLabel('Please enter your name:')
        self.input_line.layout().addWidget(self.input_label)

        self.input = QLineEdit()
        self.input_line.layout().addWidget(self.input)

        self.content.layout().addWidget(self.input_line)


    def exec_and_get_values(self):
        if self.exec() == QDialog.Accepted:
            return self.input.text()
        return None
