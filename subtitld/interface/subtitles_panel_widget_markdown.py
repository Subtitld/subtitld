"""Module for subtitle list panel

"""
from PySide6.QtWidgets import QFrame, QTextEdit, QVBoxLayout, QPushButton
from PySide6.QtGui import QTextCursor

from subtitld.interface import subtitles_panel
from subtitld.modules import session, utils


def add_widgets(self):
    self.subtitles_panel_markdown_widget = QFrame()
    self.subtitles_panel_markdown_widget.setObjectName('subtitles_panel_markdown_widget')
    self.subtitles_panel_markdown_widget.setLayout(QVBoxLayout())
    self.subtitles_panel_markdown_widget.layout().setContentsMargins(20, 0, 0, 0)
    self.subtitles_panel_markdown_widget.layout().setSpacing(0)

    class subtitles_panel_markdown_qtextedit(QTextEdit):
        def focusOutEvent(widget, event):
            update_subtitles_panel_markdown(self)
            event.accept()

    self.subtitles_panel_markdown_qtextedit = subtitles_panel_markdown_qtextedit()
    self.subtitles_panel_markdown_qtextedit.setObjectName('subtitles_panel_markdown_qtextedit')
    # self.subtitles_panel_markdown_qtextedit.focusInEvent.connect(lambda: subtitles_panel_markdown_qtextedit_cursorpositionchanged(self))
    # self.subtitles_panel_markdown_qtextedit.focusOutEvent.connect(lambda: update_subtitles_panel_markdown(self))
    self.subtitles_panel_markdown_qtextedit.cursorPositionChanged.connect(lambda: subtitles_panel_markdown_qtextedit_cursorpositionchanged(self))
    self.subtitles_panel_markdown_qtextedit.textChanged.connect(lambda: subtitles_panel_markdown_qtextedit_textchanged(self))
    self.subtitles_panel_markdown_widget.layout().addWidget(self.subtitles_panel_markdown_qtextedit)

    self.subtitles_panel_stackedwidgets.addWidget(self.subtitles_panel_markdown_widget)


def add_button(self):
    self.subtitles_panel_widget_button_markdown = QPushButton()
    self.subtitles_panel_widget_button_markdown.setObjectName('subtitles_panel_widget_button_markdown')
    self.subtitles_panel_widget_button_markdown.setProperty('class', 'subtitles_panel_left_button')
    self.subtitles_panel_widget_button_markdown.setCheckable(True)
    self.subtitles_panel_widget_button_markdown.setFixedWidth(23)
    self.subtitles_panel_widget_button_markdown.clicked.connect(lambda vision: subtitles_panel.update_subtitles_panel_widget_vision(self, 'markdown'))
    self.subtitles_panel_widget_buttons_vbox.addWidget(self.subtitles_panel_widget_button_markdown)


def subtitles_panel_markdown_qtextedit_cursorpositionchanged(self):
    # print('blah')
    position = self.subtitles_panel_markdown_qtextedit.textCursor().position()
    # print(position)
    cursor = 0
    # # markdown_text = ''
    for subtitle in sorted(session.SUBTITLE['segments']):
        cursor += len(str("{:.3f}".format(subtitle['start'])))
        next_index = session.SUBTITLE['segments'].index(subtitle) + 1
        if not next_index >= len(session.SUBTITLE['segments']) and not session.SUBTITLE['segments'][next_index]['start'] - 0.001 == subtitle['end']:
            cursor += len(' - ' + str("{:.3f}".format(subtitle['end'])))
        cursor += len('\n')

        cursor += len(str(subtitle[2]) + '\n\n')
        if cursor > position:
            session.SUBTITLE['selected'] = subtitle
            break

    if session.SUBTITLE['selected']:
        if not session.SUBTITLE['selected'][0] < session.SUBTITLE.get('position', 0) < session.SUBTITLE['selected'][0] + session.SUBTITLE['selected'][1]:
            self.player_widget.seek(session.SUBTITLE['selected'][0] + (session.SUBTITLE['selected'][1] * .5))

            timeline.update_scrollbar(self, position='middle')


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


def update_subtitles_panel_markdown(self, selection=False):
    cursor = self.subtitles_panel_markdown_qtextedit.textCursor()
    # old_scrollbar_value = self.subtitles_panel_markdown_qtextedit.verticalScrollBar().value()

    selected_subtitle_found = False
    position = 0
    markdown_text = ''
    for subtitle in sorted(session.SUBTITLE['segments']):
        markdown_text += '<small><b>' + str("{:.3f}".format(subtitle['start']))
        if not selected_subtitle_found:
            position += len(str("{:.3f}".format(subtitle['start'])))

        next_index = session.SUBTITLE['segments'].index(subtitle) + 1
        if next_index < len(session.SUBTITLE['segments']) and not session.SUBTITLE['segments'][next_index]['start'] - 0.001 == subtitle['end'] or (next_index == len(session.SUBTITLE['segments']) and not session.VIDEO['duration'] - 0.001 == subtitle['end']):
            markdown_text += ' - ' + str("{:.3f}".format(subtitle['end']))
            if not selected_subtitle_found:
                position += len(' - ' + str("{:.3f}".format(subtitle['end'])))

        markdown_text += '</b></small><br/>'
        if not selected_subtitle_found:
            position += len('\n')

        markdown_text += str(subtitle[2]) + '<br/><br/>'

        if session.SUBTITLE['selected'] == subtitle:
            selected_subtitle_found = True
            # position -= 2

        if not selected_subtitle_found:
            position += len(str(subtitle['text']) + '\n\n')

    self.subtitles_panel_markdown_qtextedit.blockSignals(True)

    self.subtitles_panel_markdown_qtextedit.setHtml(markdown_text)

    if selection:
        cursor.setPosition(position + selection[0])
        cursor.setPosition(position + selection[0] + selection[1], QTextCursor.KeepAnchor)
    elif position < len(markdown_text):
        cursor.setPosition(position)

    self.subtitles_panel_markdown_qtextedit.setTextCursor(cursor)
    # self.subtitles_panel_markdown_qtextedit.verticalScrollBar().setValue(old_scrollbar_value)

    # self.subtitles_panel_markdown_qtextedit.setFocus(Qt.TabFocusReason)

    self.subtitles_panel_markdown_qtextedit.blockSignals(False)
