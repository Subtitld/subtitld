"""The editors' chat of a shared project, in the subtitld.cc panel (modules.collab_service, collab_chat,
collab_voice)

Messages go through subtitld.cc and stay on the computers of the people in the project: this card shows the
conversation as this Subtitld has it, sends what is typed (it waits while offline), and can point a message at
the selected subtitle and the playhead, which a click on it goes back to. One's own messages can be edited and
deleted. Keeping the conversation in the project file is a choice per project.

The voice call is push-to-talk: a key held (F8 unless the person chose another; only while Subtitld has the
focus), the Talk button held, or "stay on the air". The project's sound goes down while someone speaks.
"""

import datetime
import html

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QKeySequenceEdit, QLineEdit, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from subtitld.interface.subtitldcc_live import _button, _clear, _row, _text
from subtitld.interface.translation import _
from subtitld.modules import collab_service, collab_state, session

SHOWN = 200  # the latest messages shown (the rest stays kept)
PTT_KEY = 'F8'
DUCKED = 0.3  # the project's sound while a collaborator speaks


def ptt_key():
    return (session.CONFIG.get('collab') or {}).get('ptt_key') or PTT_KEY


class _PushToTalk(QObject):
    """The push-to-talk key, application-wide while in a call: held, talking; let go (or Subtitld loses the
    focus), not."""

    def eventFilter(self, _watched, event):
        kind = event.type()
        if kind in (QEvent.KeyPress, QEvent.KeyRelease) and not event.isAutoRepeat():
            pressed = QKeySequence(event.keyCombination()).toString()
            if pressed == ptt_key():
                collab_service.voice_talk(kind == QEvent.KeyPress)
                return True
        elif kind == QEvent.ApplicationDeactivate:
            collab_service.voice_talk(False)
        return False


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

        # Voice: push-to-talk.
        self.voice_title = _text('subtitldcc_kicker')
        layout.addWidget(self.voice_title)
        self.voice_note = _text()
        layout.addWidget(self.voice_note)
        self.voice_people = _text()
        self.voice_people.setTextFormat(Qt.RichText)
        layout.addWidget(self.voice_people)
        self.join_button = _button(primary=True)
        self.join_button.clicked.connect(self.toggle_call)
        self.talk_button = _button()
        self.talk_button.pressed.connect(lambda: collab_service.voice_talk(True))
        self.talk_button.released.connect(lambda: collab_service.voice_talk(False))
        layout.addWidget(_row(self.join_button, self.talk_button))
        self.latch = QCheckBox()
        self.latch.toggled.connect(collab_service.voice_latch)
        layout.addWidget(self.latch)
        self.key_label = _text()
        self.key_edit = QKeySequenceEdit()
        self.key_edit.setMaximumSequenceLength(1)
        self.key_edit.editingFinished.connect(self._key_chosen)
        layout.addWidget(_row(self.key_label, self.key_edit, stretch=False))
        self._ptt = _PushToTalk(self)
        self._voice_timer = QTimer(self)
        self._voice_timer.setInterval(150)
        self._voice_timer.timeout.connect(self._voice_tick)

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
        self.voice_title.setText(_('collab.voice_title'))
        self.talk_button.setText(_('collab.voice_talk'))
        self.talk_button.setToolTip(_('collab.voice_talk_tip'))
        self.latch.setText(_('collab.voice_latch'))
        self.key_label.setText(_('collab.voice_key'))
        self.key_edit.setKeySequence(QKeySequence(ptt_key()))
        self.refresh()

    def _news(self, kind):
        if kind in ('chat', 'state', 'peers', 'shared', 'stopped', 'document', 'voice'):
            self._redraw.start()
        if kind == 'voice':
            self._voice_changed()

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

        self._render_voice()

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

    def _render_voice(self):
        available = collab_service.voice_available()
        state = collab_service.voice_state()
        on = state['on']
        self.voice_note.setVisible(not available or state['many'])
        self.voice_note.setText(_('collab.voice_unavailable') if not available else _('collab.voice_many'))
        self.join_button.setEnabled(available)
        self.join_button.setText(_('collab.voice_leave') if on else _('collab.voice_join'))
        for widget in (self.talk_button, self.latch, self.key_label, self.key_edit):
            widget.setVisible(on)
        self.latch.blockSignals(True)
        self.latch.setChecked(state['latched'])
        self.latch.blockSignals(False)
        people = []
        for person in state['people']:
            name = html.escape(person['name'] or person['handle'])
            if person['me']:
                name = _('collab.voice_you').format(name=name)
            people.append(f'<b>● {name}</b>' if person['speaking'] else name)
        self.voice_people.setVisible(bool(people))
        self.voice_people.setText(_('collab.voice_people').format(people=', '.join(people)))
        self.talk_button.setProperty('class', 'primary' if state['talking'] else '')
        self.talk_button.style().unpolish(self.talk_button)
        self.talk_button.style().polish(self.talk_button)

    def toggle_call(self):
        if collab_service.in_call():
            collab_service.voice_leave()
        elif not collab_service.voice_join():
            self.panel.show_message(_('collab.voice_failed'), error=True)

    def _voice_changed(self):
        app = QApplication.instance()
        if collab_service.in_call():
            if not self._voice_timer.isActive():
                app.installEventFilter(self._ptt)
                self._voice_timer.start()
        else:
            app.removeEventFilter(self._ptt)
            self._voice_timer.stop()
            self._duck(1.0)

    def _voice_tick(self):
        """Who is speaking, shown; the project's sound down while someone is."""
        state = collab_service.voice_state()
        self._duck(DUCKED if state['someone_speaking'] else 1.0)
        self._render_voice()

    def _duck(self, level):
        device = getattr(getattr(self.panel.window_, 'preview_panel_player', None), '_audio_device', None)
        if device is not None and hasattr(device, 'duck'):
            device.duck = level

    def _key_chosen(self):
        sequence = self.key_edit.keySequence().toString()
        if sequence:
            session.CONFIG.setdefault('collab', {})['ptt_key'] = sequence

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
