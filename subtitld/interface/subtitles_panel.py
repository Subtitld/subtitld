"""Module for subtitle list panel

"""

# from multiprocessing.spawn import old_main_modules
import os
import datetime

from PySide6.QtWidgets import QHBoxLayout, QLayout, QPushButton, QLabel, QMessageBox, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget, QLineEdit, QProgressBar, QFileDialog, QSplitter
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, Qt, QSize, QRect
from PySide6.QtGui import QTextCursor

from subtitld.interface import subtitles_panel_widget_markdown, subtitles_panel_widget_qlistwidget, subtitles_panel_widget_timeline, timeline, subtitles_panel_info
from subtitld.interface.translation import _
from subtitld.modules import file_io, subtitles, session, utils


def load(self):
    """Function to load subtitles list widgets"""
    self.subtitles_panel_widget = QLabel()
    self.subtitles_panel_widget.setObjectName('subtitles_panel_widget')
    self.subtitles_panel_widget_animation = QPropertyAnimation(self.subtitles_panel_widget, b'maximumWidth')
    self.subtitles_panel_widget_animation.setEasingCurve(QEasingCurve.OutQuint)
    self.subtitles_panel_widget_animation.finished.connect(lambda: self.subtitles_panel_widget.setMaximumWidth(self.window().width()))
    self.subtitles_panel_widget.setAttribute(Qt.WA_LayoutOnEntireRect)
    self.subtitles_panel_widget.setLayout(QHBoxLayout())
    self.subtitles_panel_widget.layout().setContentsMargins(0, 0, 2, 20)
    self.subtitles_panel_widget.layout().setSpacing(0)

    self.subtitles_panel_widget_vbox = QVBoxLayout()
    self.subtitles_panel_widget_vbox.setContentsMargins(0, 20, 0, 0)
    self.subtitles_panel_widget_vbox.setSpacing(20)

    subtitles_panel_info.load(self)

    self.subtitles_panel_stackedwidgets = QStackedWidget()

    subtitles_panel_widget_qlistwidget.add_widgets(self)

    subtitles_panel_widget_markdown.add_widgets(self)

    subtitles_panel_widget_timeline.add_widgets(self)

    self.subtitles_panel_widget_vbox.layout().addWidget(self.subtitles_panel_stackedwidgets)
    self.subtitles_panel_widget.layout().addLayout(self.subtitles_panel_widget_vbox)

    self.subtitles_panel_widget_buttons_vbox = QVBoxLayout()
    self.subtitles_panel_widget_buttons_vbox.setContentsMargins(10, 0, 0, 0)
    self.subtitles_panel_widget_buttons_vbox.setSpacing(0)

    self.subtitles_panel_widget_buttons_global_panel_placeholder = QPushButton()
    self.subtitles_panel_widget_buttons_global_panel_placeholder.setObjectName('subtitles_panel_widget_buttons_global_panel_placeholder')
    self.subtitles_panel_widget_buttons_global_panel_placeholder.setFixedSize(QSize(22, 70))
    self.subtitles_panel_widget_buttons_global_panel_placeholder.clicked.connect(lambda: subtitles_panel_widget_buttons_global_panel_placeholder_clicked(self))
    self.subtitles_panel_widget_buttons_vbox.addWidget(self.subtitles_panel_widget_buttons_global_panel_placeholder)

    subtitles_panel_widget_qlistwidget.add_button(self)

    self.subtitles_panel_widget_buttons_vbox.addSpacing(-10)

    subtitles_panel_widget_markdown.add_button(self)

    self.subtitles_panel_widget_buttons_vbox.addSpacing(-10)

    subtitles_panel_widget_timeline.add_button(self)

    self.subtitles_panel_widget_buttons_vbox.addStretch()

    self.subtitles_panel_findandreplace_list = []
    self.subtitles_panel_findandreplace_index = None

    self.subtitles_panel_findandreplace_toggle_button = QPushButton()  # It will be 'Find and replace'
    self.subtitles_panel_findandreplace_toggle_button.setObjectName('subtitles_panel_findandreplace_toggle_button')
    self.subtitles_panel_findandreplace_toggle_button.setProperty('class', 'button_dark')
    self.subtitles_panel_findandreplace_toggle_button.setProperty('borderless_right', 'true')
    self.subtitles_panel_findandreplace_toggle_button.setFixedWidth(23)
    self.subtitles_panel_findandreplace_toggle_button.clicked.connect(lambda: subtitles_panel_findandreplace_toggle_button_clicked(self))
    self.subtitles_panel_widget_buttons_vbox.addWidget(self.subtitles_panel_findandreplace_toggle_button)

    self.subtitles_panel_findandreplace_panel = QWidget(self)
    self.subtitles_panel_findandreplace_panel.setWindowFlags(Qt.Tool)
    self.subtitles_panel_findandreplace_panel.setObjectName('subtitles_panel_findandreplace_panel')
    self.subtitles_panel_findandreplace_panel.setLayout(QVBoxLayout())
    self.subtitles_panel_findandreplace_panel.layout().setSpacing(5)
    self.subtitles_panel_findandreplace_panel.layout().setContentsMargins(10, 10, 10, 10)
    self.subtitles_panel_findandreplace_panel.setVisible(False)

    self.subtitles_panel_findandreplace_find_line = QWidget(self)
    self.subtitles_panel_findandreplace_find_line.setLayout(QHBoxLayout())
    # self.subtitles_panel_findandreplace_find_line.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum))
    self.subtitles_panel_findandreplace_find_line.layout().setSpacing(0)
    self.subtitles_panel_findandreplace_find_line.layout().setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_findandreplace_find_line.layout().setSizeConstraint(QLayout.SetMaximumSize)

    self.subtitles_panel_findandreplace_findback_button = QPushButton()
    self.subtitles_panel_findandreplace_findback_button.setObjectName('subtitles_panel_findandreplace_findback_button')
    self.subtitles_panel_findandreplace_findback_button.setProperty('class', 'button_dark')
    self.subtitles_panel_findandreplace_findback_button.setProperty('borderless_right', True)
    self.subtitles_panel_findandreplace_findback_button.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
    self.subtitles_panel_findandreplace_findback_button.clicked.connect(lambda: subtitles_panel_findandreplace_findback_field_clicked(self))
    self.subtitles_panel_findandreplace_find_line.layout().addWidget(self.subtitles_panel_findandreplace_findback_button, 0)

    self.subtitles_panel_findandreplace_find_field = QLineEdit()
    self.subtitles_panel_findandreplace_find_field.setObjectName('subtitles_panel_findandreplace_find_field')
    self.subtitles_panel_findandreplace_find_field.setProperty('borderless_right', True)
    self.subtitles_panel_findandreplace_find_field.setProperty('borderless_left', True)
    self.subtitles_panel_findandreplace_find_field.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum))
    self.subtitles_panel_findandreplace_find_field.setFixedWidth(150)
    self.subtitles_panel_findandreplace_find_field.textChanged.connect(lambda: subtitles_panel_findandreplace_find_field_textchanged(self))
    self.subtitles_panel_findandreplace_find_line.layout().addWidget(self.subtitles_panel_findandreplace_find_field, 1)

    self.subtitles_panel_findandreplace_findnext_button = QPushButton()
    self.subtitles_panel_findandreplace_findnext_button.setObjectName('subtitles_panel_findandreplace_findnext_button')
    self.subtitles_panel_findandreplace_findnext_button.setProperty('class', 'button_dark')
    self.subtitles_panel_findandreplace_findnext_button.setProperty('borderless_left', True)
    self.subtitles_panel_findandreplace_findnext_button.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
    self.subtitles_panel_findandreplace_findnext_button.clicked.connect(lambda: subtitles_panel_findandreplace_findnext_field_clicked(self))
    self.subtitles_panel_findandreplace_find_line.layout().addWidget(self.subtitles_panel_findandreplace_findnext_button, 0)

    self.subtitles_panel_findandreplace_find_line.layout().addStretch()

    self.subtitles_panel_findandreplace_casesensitive = QPushButton()
    self.subtitles_panel_findandreplace_casesensitive.setObjectName('subtitles_panel_findandreplace_casesensitive')
    self.subtitles_panel_findandreplace_casesensitive.setProperty('class', 'button')
    self.subtitles_panel_findandreplace_casesensitive.setCheckable(True)
    self.subtitles_panel_findandreplace_casesensitive.clicked.connect(lambda: subtitles_panel_findandreplace_casesensitive_clicked(self))
    self.subtitles_panel_findandreplace_find_line.layout().addWidget(self.subtitles_panel_findandreplace_casesensitive, 0)

    self.subtitles_panel_findandreplace_panel.layout().addWidget(self.subtitles_panel_findandreplace_find_line)

    self.subtitles_panel_findandreplace_information_label = QLabel()
    self.subtitles_panel_findandreplace_information_label.setProperty('class', 'qlabel_for_buttons')
    self.subtitles_panel_findandreplace_panel.layout().addWidget(self.subtitles_panel_findandreplace_information_label)

    self.subtitles_panel_findandreplace_replace_line = QWidget(self)
    self.subtitles_panel_findandreplace_replace_line.setLayout(QHBoxLayout())
    self.subtitles_panel_findandreplace_replace_line.layout().setSpacing(0)
    self.subtitles_panel_findandreplace_replace_line.layout().setContentsMargins(0, 0, 0, 0)
    self.subtitles_panel_findandreplace_replace_line.layout().setSizeConstraint(QLayout.SetMaximumSize)

    self.subtitles_panel_findandreplace_replace_field = QLineEdit()
    self.subtitles_panel_findandreplace_replace_field.setObjectName('subtitles_panel_findandreplace_replace_field')
    self.subtitles_panel_findandreplace_replace_field.setProperty('borderless_right', True)
    self.subtitles_panel_findandreplace_replace_field.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum))
    self.subtitles_panel_findandreplace_replace_field.setFixedWidth(150)
    # self.subtitles_panel_findandreplace_replace_field.setFixedHeight(25)
    # self.subtitles_panel_findandreplace_replace_field.setVisible(False)
    self.subtitles_panel_findandreplace_replace_line.layout().addWidget(self.subtitles_panel_findandreplace_replace_field, 1)

    self.subtitles_panel_findandreplace_replaceandfindnext_button = QPushButton()
    self.subtitles_panel_findandreplace_replaceandfindnext_button.setProperty('class', 'button_dark')
    self.subtitles_panel_findandreplace_replaceandfindnext_button.setProperty('borderless_left', True)
    self.subtitles_panel_findandreplace_replaceandfindnext_button.setProperty('borderless_right', True)
    self.subtitles_panel_findandreplace_replaceandfindnext_button.setLayout(QHBoxLayout())
    self.subtitles_panel_findandreplace_replaceandfindnext_button.layout().setContentsMargins(0, 2, 4, 2)
    self.subtitles_panel_findandreplace_replaceandfindnext_button.layout().setSizeConstraint(QLayout.SetMinimumSize)
    self.subtitles_panel_findandreplace_replaceandfindnext_button.clicked.connect(lambda: subtitles_panel_findandreplace_replaceandfindnext_button_clicked(self))

    self.subtitles_panel_findandreplace_replace_button = QPushButton()
    self.subtitles_panel_findandreplace_replace_button.setProperty('class', 'button_dark')
    self.subtitles_panel_findandreplace_replace_button.setProperty('borderless_left', True)
    self.subtitles_panel_findandreplace_replace_button.setObjectName('subtitles_panel_findandreplace_replace_button')
    self.subtitles_panel_findandreplace_replace_button.clicked.connect(lambda: subtitles_panel_findandreplace_replace_button_clicked(self))
    self.subtitles_panel_findandreplace_replaceandfindnext_button.layout().addWidget(self.subtitles_panel_findandreplace_replace_button, 0)

    self.subtitles_panel_findandreplace_replaceandfindnext_button_label = QLabel()
    self.subtitles_panel_findandreplace_replaceandfindnext_button_label.setProperty('class', 'qlabel_for_buttons')
    self.subtitles_panel_findandreplace_replaceandfindnext_button.layout().addWidget(self.subtitles_panel_findandreplace_replaceandfindnext_button_label, 0)

    self.subtitles_panel_findandreplace_replace_line.layout().addWidget(self.subtitles_panel_findandreplace_replaceandfindnext_button, 0)

    self.subtitles_panel_findandreplace_replace_line.layout().addSpacing(1)

    self.subtitles_panel_findandreplace_replaceall_button = QPushButton()
    self.subtitles_panel_findandreplace_replaceall_button.setProperty('class', 'button_dark')
    self.subtitles_panel_findandreplace_replaceall_button.setProperty('borderless_left', True)
    self.subtitles_panel_findandreplace_replaceall_button.clicked.connect(lambda: subtitles_panel_findandreplace_replaceall_button_clicked(self))
    self.subtitles_panel_findandreplace_replace_line.layout().addWidget(self.subtitles_panel_findandreplace_replaceall_button, 0)

    # self.subtitles_panel_findandreplace_replace_line.layout().addStretch()

    self.subtitles_panel_findandreplace_panel.layout().addWidget(self.subtitles_panel_findandreplace_replace_line)

    self.subtitles_panel_findandreplace_panel.setFixedHeight(self.subtitles_panel_findandreplace_panel.minimumSizeHint().height())

    self.subtitles_panel_widget.layout().addLayout(self.subtitles_panel_widget_buttons_vbox)

    update_subtitles_panel_widget_vision(self)

    subtitles_panel_widget_buttons_global_panel_placeholder_update(self)


def resized(self):
    """Function to call when resizing subtitles list"""
    x = int(-((self.width() * session.CONFIG['subtitles_panel_width_proportion']) - 15))
    if (session.SUBTITLE['segments'] or session.VIDEO) and not self.subtitles_panel_toggle_button.isChecked():
        x = 0
    self.subtitles_panel_widget.setGeometry(x, 0, int((self.width() * session.CONFIG['subtitles_panel_width_proportion']) - 15), int(self.height()))

    # x = self.subtitles_panel_widget.x() + self.subtitles_panel_widget.width()
    # if (session.SUBTITLE['segments'] or session.VIDEO) and self.subtitles_panel_toggle_button.isChecked():
    #     x = self.global_panel_widget.x() + self.global_panel_widget.width() - self.subtitles_panel_toggle_button.width()
    # x -= self.subtitles_panel_toggle_button.width()

    # self.subtitles_panel_toggle_button.move(self.global_panel_widget.x() + self.global_panel_widget.width() - self.subtitles_panel_toggle_button.width(), self.subtitles_panel_widget.y())
    subtitles_panel_widget_timeline.timeline_resized(self)


def update_subtitles_panel_widget_vision_content(self):
    if self.subtitles_panel_stackedwidgets.currentWidget() == self.subtitles_panel_markdown_widget:
        if not self.subtitles_panel_markdown_qtextedit.hasFocus():
            subtitles_panel_widget_markdown.update_subtitles_panel_markdown(self)

    elif self.subtitles_panel_stackedwidgets.currentWidget() == self.subtitles_panel_timeline_widget:
        subtitles_panel_widget_timeline.update_subtitles_panel_timeline(self)



def show(self):
    self.generate_effect(self.subtitles_panel_widget_animation, 'maximumWidth', 2000, 0, int(session.CONFIG.get('subtitles_list_panel_width', .4) * self.window().width()))
    self.subtitles_panel_simplelist_qsplitter.setSizes([
        int(self.subtitles_panel_simplelist_qsplitter.height() * session.CONFIG.get('subtitles_list_textedit_height', .7)),
        int(self.subtitles_panel_simplelist_qsplitter.height() * session.CONFIG.get('subtitles_list_textedit_height', .3))
    ])
    """Function to show subtitle list panel"""
    # self.generate_effect(
    #     self.subtitles_panel_widget_animation,
    #     'geometry',
    #     700,
    #     [int(self.subtitles_panel_widget.x()), int(self.subtitles_panel_widget.y()), int(self.subtitles_panel_widget.width()), int(self.subtitles_panel_widget.height())],
    #     [0, int(self.subtitles_panel_widget.y()), int(self.subtitles_panel_widget.width()), int(self.subtitles_panel_widget.height())]
    # )
    # self.global_panel.hide_global_panel(self)
    # update_subtitles_panel_widget_vision_content(self)


def hide(self):
    """Function to hide subtitle list panel"""
    None
    # self.generate_effect(self.subtitles_panel_widget_animation, 'geometry', 700, [self.subtitles_panel_widget.x(), self.subtitles_panel_widget.y(), self.subtitles_panel_widget.width(), self.subtitles_panel_widget.height()], [-self.subtitles_panel_widget.width(), self.subtitles_panel_widget.y(), self.subtitles_panel_widget.width(), self.subtitles_panel_widget.height()])

def subtitles_panel_findandreplace_toggle_button_clicked(self):
    self.subtitles_panel_findandreplace_panel.setVisible(True)
    subtitles_panel_findandreplace_find_field_textchanged(self)


def subtitles_panel_findandreplace_find_field_textchanged(self):
    self.subtitles_panel_findandreplace_findback_button.setEnabled(bool(self.subtitles_panel_findandreplace_find_field.text()))
    self.subtitles_panel_findandreplace_findnext_button.setEnabled(bool(self.subtitles_panel_findandreplace_find_field.text()))
    self.subtitles_panel_findandreplace_replaceandfindnext_button.setEnabled(bool(self.subtitles_panel_findandreplace_find_field.text()))
    self.subtitles_panel_findandreplace_replace_button.setEnabled(bool(self.subtitles_panel_findandreplace_find_field.text()))
    self.subtitles_panel_findandreplace_replaceall_button.setEnabled(bool(self.subtitles_panel_findandreplace_find_field.text()))

    subtitles_panel_findandreplace_perform_search(self)

    text = 'Type the text to find'
    if bool(self.subtitles_panel_findandreplace_find_field.text()):
        if self.subtitles_panel_findandreplace_list:
            text = 'Found {} matches'.format(len(self.subtitles_panel_findandreplace_list))
        else:
            text = 'No matches found for "{}"'.format(self.subtitles_panel_findandreplace_find_field.text())

    self.subtitles_panel_findandreplace_information_label.setText(text)


def subtitles_panel_findandreplace_replaceandfindnext_button_clicked(self):
    subtitles_panel_findandreplace_replace_button_clicked(self)
    subtitles_panel_findandreplace_findnext_field_clicked(self)


def subtitles_panel_findandreplace_replace_button_clicked(self):
    if session.SUBTITLE['selected']:
        subtitles.change_subtitle_text(selected_subtitle=session.SUBTITLE['selected'], text=session.SUBTITLE['selected']['text'][:self.subtitles_panel_findandreplace_list[self.subtitles_panel_findandreplace_index][1]] + self.subtitles_panel_findandreplace_replace_field.text() + session.SUBTITLE['selected']['text'][self.subtitles_panel_findandreplace_list[self.subtitles_panel_findandreplace_index][1] + self.subtitles_panel_findandreplace_list[self.subtitles_panel_findandreplace_index][2]:])
        session.CONFIG['unsaved'] = True
        subtitles_panel_info.update(self)

    ind = self.subtitles_panel_findandreplace_index
    subtitles_panel_findandreplace_find_field_textchanged(self)
    self.subtitles_panel_findandreplace_index = ind

    if self.subtitles_panel_stackedwidgets.currentWidget() == self.subtitles_panel_simplelist_qsplitter:
        subtitles_panel_widget_qlistwidget.update_properties_widget(self)
    elif self.subtitles_panel_stackedwidgets.currentWidget() == self.subtitles_panel_markdown_widget:
        subtitles_panel_widget_markdown.update_subtitles_panel_markdown(self)
    elif self.subtitles_panel_stackedwidgets.currentWidget() == self.subtitles_panel_timeline_widget:
        subtitles_panel_widget_timeline.update_subtitles_panel_timeline(self)


def subtitles_panel_findandreplace_replaceall_button_clicked(self):
    while self.subtitles_panel_findandreplace_list:
        subtitles_panel_findandreplace_replaceandfindnext_button_clicked(self)
    subtitles_panel_findandreplace_update(self)


def subtitles_panel_findandreplace_casesensitive_clicked(self):
    subtitles_panel_findandreplace_find_field_textchanged(self)


def subtitles_panel_findandreplace_perform_search(self):
    self.subtitles_panel_findandreplace_list = []
    self.subtitles_panel_findandreplace_index = 0

    text_to_search = self.subtitles_panel_findandreplace_find_field.text() if self.subtitles_panel_findandreplace_casesensitive.isChecked() else self.subtitles_panel_findandreplace_find_field.text().lower()
    for subtitle in session.SUBTITLE['segments']:
        if text_to_search in (subtitle['text'] if self.subtitles_panel_findandreplace_casesensitive.isChecked() else subtitle['text'].lower()):
            s = 0
            for _ in range((subtitle['text'] if self.subtitles_panel_findandreplace_casesensitive.isChecked() else subtitle['text'].lower()).count(text_to_search)):
                self.subtitles_panel_findandreplace_list.append([session.SUBTITLE['segments'].index(subtitle), subtitle['text'].find(text_to_search, s), len(text_to_search)])
                s += subtitle['text'].find(text_to_search, s) + len(text_to_search)


def subtitles_panel_findandreplace_findback_field_clicked(self):
    self.subtitles_panel_findandreplace_index -= 1
    if self.subtitles_panel_findandreplace_index < 0:
        self.subtitles_panel_findandreplace_index = len(self.subtitles_panel_findandreplace_list) - 1
    subtitles_panel_findandreplace_update(self)


def subtitles_panel_findandreplace_findnext_field_clicked(self):
    self.subtitles_panel_findandreplace_index += 1
    if self.subtitles_panel_findandreplace_index >= len(self.subtitles_panel_findandreplace_list):
        self.subtitles_panel_findandreplace_index = 0
    subtitles_panel_findandreplace_update(self)


def subtitles_panel_findandreplace_update(self):
    if self.subtitles_panel_findandreplace_list:
        session.SUBTITLE['selected'] = session.SUBTITLE['segments'][self.subtitles_panel_findandreplace_list[self.subtitles_panel_findandreplace_index][0]]

        if not session.SUBTITLE['selected']['start'] < session.SUBTITLE.get('position', 0) < session.SUBTITLE['selected']['end']:
            self.player_widget.seek(session.SUBTITLE['selected']['start'] + ((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) * .5))
            timeline.update_scrollbar(self, position='middle')

        update_subtitles_panel_widget_vision_content(self)
        if self.subtitles_panel_stackedwidgets.currentWidget() == self.subtitles_panel_simplelist_qsplitter:
            c = self.properties_textedit.textCursor()
            c.setPosition(self.subtitles_panel_findandreplace_list[self.subtitles_panel_findandreplace_index][1])
            c.setPosition(self.subtitles_panel_findandreplace_list[self.subtitles_panel_findandreplace_index][1] + self.subtitles_panel_findandreplace_list[self.subtitles_panel_findandreplace_index][2], QTextCursor.KeepAnchor)
            self.properties_textedit.setTextCursor(c)
        elif self.subtitles_panel_stackedwidgets.currentWidget() == self.subtitles_panel_markdown_widget:
            subtitles_panel_widget_markdown.update_subtitles_panel_markdown(self, selection=[self.subtitles_panel_findandreplace_list[self.subtitles_panel_findandreplace_index][1], self.subtitles_panel_findandreplace_list[self.subtitles_panel_findandreplace_index][2]])
        elif self.subtitles_panel_stackedwidgets.currentWidget() == self.subtitles_panel_timeline_widget:
            self.subtitles_panel_timeline_widget_timeline.show_editing_widgets = True
            self.subtitles_panel_timeline_widget_timeline.update_editing_widgets()
            c = self.subtitles_panel_timeline_widget_timeline.text_qtextedit.textCursor()
            c.setPosition(self.subtitles_panel_findandreplace_list[self.subtitles_panel_findandreplace_index][1])
            c.setPosition(self.subtitles_panel_findandreplace_list[self.subtitles_panel_findandreplace_index][1] + self.subtitles_panel_findandreplace_list[self.subtitles_panel_findandreplace_index][2], QTextCursor.KeepAnchor)
            self.subtitles_panel_timeline_widget_timeline.text_qtextedit.setTextCursor(c)
            subtitles_panel_widget_timeline.update_scrollbar(self, position='middle')


def update_subtitles_panel_widget_vision(self, vision='list'):
    if vision == 'list':
        self.subtitles_panel_stackedwidgets.setCurrentWidget(self.subtitles_panel_simplelist_qsplitter)
        self.subtitles_panel_widget_button_list.setEnabled(False)
    else:
        self.subtitles_panel_widget_button_list.setEnabled(True)
        self.subtitles_panel_widget_button_list.setChecked(False)

    if vision == 'markdown':
        self.subtitles_panel_stackedwidgets.setCurrentWidget(self.subtitles_panel_markdown_widget)
        self.subtitles_panel_widget_button_markdown.setEnabled(False)
    else:
        self.subtitles_panel_widget_button_markdown.setEnabled(True)
        self.subtitles_panel_widget_button_markdown.setChecked(False)

    if vision == 'timeline':
        self.subtitles_panel_stackedwidgets.setCurrentWidget(self.subtitles_panel_timeline_widget)
        self.subtitles_panel_widget_button_timeline.setEnabled(False)
        subtitles_panel_widget_timeline.timeline_resized(self)
    else:
        self.subtitles_panel_widget_button_timeline.setEnabled(True)
        self.subtitles_panel_widget_button_timeline.setChecked(False)

    update_subtitles_panel_widget_vision_content(self)


def update_processing_status(self, show_widgets=False, value=0):
    self.toppanel_subtitle_file_progress_bar.setVisible(show_widgets)
    self.toppanel_subtitle_file_progress_bar.setValue(value)
    self.toppanel_subtitle_file_info_label.setVisible(not show_widgets)


def translate_widgets(self):
    self.subtitles_panel_findandreplace_replaceandfindnext_button_label.setText(_('subtitles_panel.and_find_next'))
    self.toppanel_open_button.setText(_('subtitles_panel.open_different_file'))
    self.subtitles_panel_findandreplace_findnext_button.setText(_('subtitles_panel.find'))
    self.subtitles_panel_findandreplace_replace_button.setText(_('subtitles_panel.replace'))
    self.subtitles_panel_findandreplace_replaceall_button.setText(_('subtitles_panel.replace_all'))
    subtitles_panel_widget_qlistwidget.translate_widgets(self)


def subtitles_panel_widget_buttons_global_panel_placeholder_clicked(self):
    self.global_panel_widget.setProperty('shown', True)
    subtitles_panel_widget_buttons_global_panel_placeholder_update(self)
    self.global_panel.subtitles_panel_toggled(self)


def subtitles_panel_widget_buttons_global_panel_placeholder_update(self):
    self.subtitles_panel_toggle_button.setEnabled(self.global_panel_widget.property('shown'))
    self.subtitles_panel_widget_buttons_global_panel_placeholder.setEnabled(not self.global_panel_widget.property('shown'))