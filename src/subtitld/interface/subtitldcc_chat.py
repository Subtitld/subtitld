"""The editors' chat of a shared project, in the subtitld.cc panel (modules.collab_service, collab_chat)

Messages go through subtitld.cc and stay on the computers of the people in the project: this card shows the
conversation as this Subtitld has it, sends what is typed (it waits while offline), and can point a message at
the selected subtitle and the playhead, which a click on it goes back to. One's own messages can be edited and
deleted. Keeping the conversation in the project file is a choice per project.
"""

import datetime
import html

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QLineEdit, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from subtitld.interface.subtitldcc_live import _button, _clear, _row, _text
from subtitld.interface.translation import _
from subtitld.modules import collab_service, collab_state, session

SHOWN = 200  # the latest messages shown (the rest stays kept)


def _clock(milliseconds):
    when = datetime.datetime.fromtimestamp(milliseconds / 1000)
    if when.date() == datetime.date.today():
        return when.strftime('%H:%M')
    return when.strftime('%Y-%m-%d %H:%M')


def _timecode(milliseconds):
    seconds = int(milliseconds // 1000)
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


class ChatCard(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setObjectName('subtitldcc_card')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        self._editing = None  # the ID of the message being edited

        self.title = _text('subtitldcc_card_title')
        layout.addWidget(self.title)
        self.note = _text()
        layout.addWidget(self.note)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(180)
        self.scroll.setProperty('class', 'transparent_panel')
        inner = QWidget()
        inner.setProperty('class', 'transparent_panel')
        self.messages_box = QVBoxLayout(inner)
        self.messages_box.setContentsMargins(0, 0, 0, 0)
        self.messages_box.setSpacing(6)
        self.messages_box.addStretch()
        self.scroll.setWidget(inner)
        layout.addWidget(self.scroll)

        self.field = QLineEdit()
        self.field.setMaxLength(2000)
        self.field.returnPressed.connect(self.send)
        self.send_button = _button(primary=True)
        self.send_button.clicked.connect(self.send)
        self.cancel_button = _button()
        self.cancel_button.clicked.connect(self.cancel_edit)
        layout.addWidget(_row(self.field, self.send_button, self.cancel_button, stretch=False))
        self.attach = QCheckBox()
        layout.addWidget(self.attach)
        self.keep = QCheckBox()
        self.keep.toggled.connect(collab_service.set_chat_saved)
        layout.addWidget(self.keep)

        self._redraw = QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(100)
        self._redraw.timeout.connect(self.refresh)
        collab_service.listeners.append(self._news)
        self.retranslate()

    def retranslate(self):
        self.field.setPlaceholderText(_('collab.chat_placeholder'))
        self.cancel_button.setText(_('collab.chat_cancel'))
        self.attach.setText(_('collab.chat_attach'))
        self.attach.setToolTip(_('collab.chat_attach_tip'))
        self.keep.setText(_('collab.chat_keep'))
        self.keep.setToolTip(_('collab.chat_keep_tip'))
        self.refresh()

    def _news(self, kind):
        if kind in ('chat', 'state', 'peers', 'shared', 'stopped', 'document'):
            self._redraw.start()

    def showEvent(self, event):
        super().showEvent(event)
        collab_service.chat_mark_read()

    # --- Showing ------------------------------------------------------------------------------------

    def refresh(self):
        shared = collab_state.shared()
        self.setVisible(shared)
        if not shared:
            return
        unread = collab_service.chat_unread()
        if unread and self.isVisible() and self.window().isActiveWindow():
            collab_service.chat_mark_read()
            unread = 0
        self.title.setText(_('collab.chat_title') + (f" · {_('collab.chat_unread').format(count=unread)}" if unread else ''))
        messages = collab_service.chat_messages()
        self.note.setVisible(collab_service.chat_alone())
        self.note.setText(_('collab.chat_alone'))
        self.keep.blockSignals(True)
        self.keep.setChecked(collab_service.chat_saved())
        self.keep.blockSignals(False)
        self.send_button.setText(_('collab.chat_save') if self._editing else _('collab.chat_send'))
        self.cancel_button.setVisible(bool(self._editing))

        bar = self.scroll.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        _clear(self.messages_box)
        if not messages:
            empty = _text()
            empty.setText(_('collab.chat_empty'))
            self.messages_box.addWidget(empty)
        me = collab_service.chat_me()
        for message in messages[-SHOWN:]:
            self.messages_box.addWidget(self._message(message, me))
        self.messages_box.addStretch()
        if at_end:
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _message(self, message, me):
        author = message.get('author') or {}
        name = author.get('name') or author.get('handle') or me
        mine = message.get('pending') or author.get('handle') == me
        head = [f'<b>{html.escape(name)}</b>']
        head.append(_('collab.chat_sending') if message.get('pending') else _clock(message['at']))
        if message.get('cue_id') or message.get('at_ms') is not None:
            head.append(f'<a href="go:{message.get("cue_id") or ""}:{message.get("at_ms") or ""}">{self._place(message)}</a>')
        if message.get('edited'):
            head.append(_('collab.chat_edited'))
        if mine and not message.get('pending') and not message.get('deleted'):
            head.append(f'<a href="edit:{message["id"]}">{_("collab.chat_edit")}</a>')
            head.append(f'<a href="delete:{message["id"]}">{_("collab.chat_delete")}</a>')
        body = (f'<i>{_("collab.chat_deleted")}</i>' if message.get('deleted')
                else html.escape(message.get('text') or '').replace('\n', '<br>'))
        label = _text()
        label.setTextFormat(Qt.RichText)
        label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        label.setText(' · '.join(head) + '<br>' + body)
        label.linkActivated.connect(self._link)
        return label

    def _place(self, message):
        """'Line 12 · 00:01:23' for a message pointing at a subtitle and the playhead."""
        parts = []
        cue_id = message.get('cue_id')
        if cue_id:
            index = next((i for i, segment in enumerate(session.SUBTITLE.get('segments') or [])
                          if segment.get('id') == cue_id), None)
            parts.append(_('collab.chat_line').format(number=index + 1) if index is not None
                         else _('collab.chat_line_gone'))
        if message.get('at_ms') is not None:
            parts.append(_timecode(message['at_ms']))
        return html.escape(' · '.join(parts))

    # --- Acting -------------------------------------------------------------------------------------

    def send(self):
        text = self.field.text().strip()
        if not text:
            return
        if self._editing:
            collab_service.chat_edit(self._editing, text)
            self._editing = None
        else:
            cue_id = at_ms = None
            if self.attach.isChecked():
                selected = session.SUBTITLE.get('selected')
                cue_id = selected.get('id') if isinstance(selected, dict) else None
                at_ms = int(float(session.SUBTITLE.get('position') or 0) * 1000)
            collab_service.chat_say(text, cue_id=cue_id, at_ms=at_ms)
        self.field.clear()
        self.refresh()

    def cancel_edit(self):
        self._editing = None
        self.field.clear()
        self.refresh()

    def _link(self, href):
        kind, _sep, rest = href.partition(':')
        if kind == 'edit':
            message = next((m for m in collab_service.chat_messages() if m['id'] == rest), None)
            if message:
                self._editing = rest
                self.field.setText(message.get('text') or '')
                self.field.setFocus()
                self.refresh()
        elif kind == 'delete':
            collab_service.chat_delete(rest)
        elif kind == 'go':
            cue_id, _sep, at_ms = rest.partition(':')
            self.go(cue_id or None, int(at_ms) if at_ms else None)

    def go(self, cue_id, at_ms):
        """Select the subtitle a message points at and move the playhead where it says."""
        window = self.panel.window_
        segment = next((s for s in session.SUBTITLE.get('segments') or [] if cue_id and s.get('id') == cue_id), None)
        if segment is not None:
            session.SUBTITLE['selected'] = segment
        seconds = at_ms / 1000 if at_ms is not None else (segment or {}).get('start')
        if seconds is not None:
            try:
                window.preview_panel_player.set_position(float(seconds))
            except (AttributeError, RuntimeError, TypeError):
                pass
        try:
            from subtitld.interface import left_panel_subtitleslist
            left_panel_subtitleslist.update(window)
        except (AttributeError, RuntimeError, ImportError):
            pass
        try:
            window.timeline_widget.update()
        except (AttributeError, RuntimeError):
            pass
