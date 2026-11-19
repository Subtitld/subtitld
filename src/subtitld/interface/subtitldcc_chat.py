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
from PySide6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QKeySequenceEdit, QLineEdit, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

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


class _ChatField(QLineEdit):
    """The message field: Enter sends (returnPressed), before the editor's own Enter shortcut (add a subtitle)
    can take it."""

    def event(self, event):
        if event.type() == QEvent.ShortcutOverride and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.accept()
            return True
        return super().event(event)


BUTTONS = 34                                            # a bubble's Edit and Delete, less its own right padding


class _Bubble(QWidget):
    """A chat message's bubble; its own buttons (Edit, Delete) flush in its bottom-right corner, shown while the
    pointer is on it."""

    def __init__(self):
        super().__init__()
        self.buttons = []
        self.foot = None                                 # the line of its time: moves left of the buttons

    def add_button(self, icon, name, action):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QPushButton
        button = QPushButton(self)
        button.setObjectName('chat_bubble_button')
        button.setProperty('first', not self.buttons)    # the first one rounds its top-left corner
        button.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS / icon)))
        button.setIconSize(QSize(12, 12))
        button.setFixedSize(22, 20)
        button.setToolTip(name)
        button.setAccessibleName(name)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(action)
        button.setVisible(False)
        self.buttons.append(button)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        x = self.width()
        for button in reversed(self.buttons):
            x -= button.width()
            button.move(x, self.height() - button.height())
            button.raise_()

    def enterEvent(self, event):
        super().enterEvent(event)
        for button in self.buttons:
            button.setVisible(True)
        if self.buttons and self.foot is not None:
            self.foot.setContentsMargins(0, 0, BUTTONS, 0)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        for button in self.buttons:
            button.setVisible(False)
        if self.foot is not None:
            self.foot.setContentsMargins(0, 0, 0, 0)


class ChatCard(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setObjectName('subtitldcc_card')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)           # in the live bar's drawer, edge to edge
        layout.setSpacing(0)                            # the well, then the field right under it
        self._editing = None  # the ID of the message being edited
        self._shown_peers = None
        self._shown_messages = None  # what the bubbles were last built from (refresh)

        # (no title nor note: the live bar's chat tab says what this is; kept for its unread count)
        self.title = _text('subtitldcc_card_title')
        self.title.setVisible(False)
        layout.addWidget(self.title)
        self.note = _text()
        self.note.setVisible(False)
        layout.addWidget(self.note)

        # The messages in a well (clear at the top, darkening to its foot); under it, on the bar's ground, edge
        # to edge, the field and Send.
        self.well = QWidget()
        self.well.setObjectName('chat_well')
        self.well.setAttribute(Qt.WA_StyledBackground, True)
        well = QVBoxLayout(self.well)
        well.setContentsMargins(0, 0, 0, 0)
        well.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(180)
        self.scroll.setObjectName('chat_scroll')
        self.scroll.setProperty('class', 'transparent_panel')
        inner = QWidget()
        inner.setProperty('class', 'transparent_panel')
        self.messages_box = QVBoxLayout(inner)
        self.messages_box.setContentsMargins(10, 10, 10, 10)
        self.messages_box.setSpacing(3)
        self.messages_box.addStretch()
        self.scroll.setWidget(inner)
        self.scroll.installEventFilter(self)             # bubbles keep to a share of its width
        # the messages fade out as they scroll up past its top edge: no seam there
        from PySide6.QtGui import QBrush, QColor, QLinearGradient
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        fade = QLinearGradient(0, 0, 0, self.FADE)
        fade.setColorAt(0, QColor(0, 0, 0, 0))
        fade.setColorAt(1, QColor(0, 0, 0, 255))
        mask = QGraphicsOpacityEffect(self.scroll)
        mask.setOpacity(1.0)
        mask.setOpacityMask(QBrush(fade))
        self.scroll.setGraphicsEffect(mask)
        well.addWidget(self.scroll)

        self.field = _ChatField()
        self.field.setObjectName('chat_field')
        self.field.setMaxLength(2000)
        self.field.returnPressed.connect(self.send)
        self.send_button = _button(primary=True)
        self.send_button.setObjectName('chat_send')
        self.send_button.clicked.connect(self.send)
        self.cancel_button = _button()
        self.cancel_button.setObjectName('chat_cancel')
        self.cancel_button.clicked.connect(self.cancel_edit)
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(0)
        foot.addWidget(self.field, 1)
        foot.addWidget(self.cancel_button)
        foot.addWidget(self.send_button)
        self.well.installEventFilter(self)
        layout.addWidget(self.well)
        layout.addLayout(foot)                          # under the well, on the bar's own ground
        # Pointing the message at the selected subtitle and the playhead: a checkable icon inside the field.
        from PySide6.QtGui import QAction
        self.attach = QAction(self)
        self.attach.setCheckable(True)
        self.attach.toggled.connect(self._show_attach)
        self.field.addAction(self.attach, QLineEdit.TrailingPosition)
        self._show_attach(False)
        # half there until the pointer is on it, like the save icon (the field draws it as a tool button)
        from PySide6.QtWidgets import QGraphicsOpacityEffect, QToolButton
        self.attach_button = next((b for b in self.field.findChildren(QToolButton) if b.defaultAction() is self.attach), None)
        if self.attach_button is not None:
            self.attach_button.setGraphicsEffect(QGraphicsOpacityEffect(self.attach_button))
            self.attach_button.graphicsEffect().setOpacity(.5)
            self.attach_button.installEventFilter(self)
        # Keeping the chat in the project file: an icon in the well's top-right corner (placed in eventFilter).
        from PySide6.QtCore import QSize
        self.keep = QPushButton(self.well)
        self.keep.setObjectName('chat_keep')
        self.keep.setCheckable(True)
        self.keep.setCursor(Qt.PointingHandCursor)
        self.keep.setIconSize(QSize(14, 14))
        self.keep.setFixedSize(30, 26)
        self.keep.toggled.connect(collab_service.set_chat_saved)
        self.keep.toggled.connect(self._show_keep)
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        self.keep.setGraphicsEffect(QGraphicsOpacityEffect(self.keep))
        self.keep.graphicsEffect().setOpacity(.5)        # half there until the pointer is on it
        self.keep.installEventFilter(self)
        self.keep.setVisible(False)                     # shown while the pointer is on the chat (eventFilter)
        self._show_keep(False)

        # Voice: push-to-talk. The live bar's call tab joins, talks and locks (subtitldcc_live.LiveBar); these
        # stay as the call's state, out of sight.
        self.voice_box = QWidget()
        voice = QVBoxLayout(self.voice_box)
        voice.setContentsMargins(0, 0, 0, 0)
        self.voice_box.setVisible(False)
        layout.addWidget(self.voice_box)
        self.voice_title = _text('subtitldcc_kicker')
        voice.addWidget(self.voice_title)
        self.voice_note = _text()
        voice.addWidget(self.voice_note)
        self.voice_people = _text()
        self.voice_people.setTextFormat(Qt.RichText)
        voice.addWidget(self.voice_people)
        self.join_button = _button(primary=True)
        self.join_button.clicked.connect(self.toggle_call)
        self.talk_button = _button()
        self.talk_button.pressed.connect(lambda: collab_service.voice_talk(True))
        self.talk_button.released.connect(lambda: collab_service.voice_talk(False))
        voice.addWidget(_row(self.join_button, self.talk_button))
        self.latch = QCheckBox()
        self.latch.toggled.connect(collab_service.voice_latch)
        voice.addWidget(self.latch)
        self.key_label = _text()
        self.key_edit = QKeySequenceEdit()
        self.key_edit.setMaximumSequenceLength(1)
        self.key_edit.editingFinished.connect(self._key_chosen)
        voice.addWidget(_row(self.key_label, self.key_edit, stretch=False))
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
        self.attach.setToolTip(f"{_('collab.chat_attach')}\n{_('collab.chat_attach_tip')}")
        self.keep.setToolTip(f"{_('collab.chat_keep')}\n{_('collab.chat_keep_tip')}")
        self.keep.setAccessibleName(_('collab.chat_keep'))
        self.voice_title.setText(_('collab.voice_title'))
        self.talk_button.setText(_('collab.voice_talk'))
        self.talk_button.setToolTip(_('collab.voice_talk_tip'))
        self.latch.setText(_('collab.voice_latch'))
        self.key_label.setText(_('collab.voice_key'))
        self.key_edit.setKeySequence(QKeySequence(ptt_key()))
        self._shown_messages = None                     # built again, in the new language
        self.refresh()

    def _show_attach(self, on):
        from PySide6.QtGui import QIcon
        self.attach.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS / ('chat_point_on.svg' if on else 'chat_point.svg'))))

    def _show_keep(self, kept):
        from PySide6.QtGui import QIcon
        self.keep.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS / ('chat_keep_on.svg' if kept else 'chat_keep.svg'))))

    def eventFilter(self, watched, event):
        if watched in (getattr(self, 'keep', None), getattr(self, 'attach_button', None)) \
                and event.type() in (QEvent.Enter, QEvent.Leave):
            watched.graphicsEffect().setOpacity(1.0 if event.type() == QEvent.Enter else .5)
        if watched is getattr(self, 'scroll', None) and event.type() == QEvent.Resize \
                and event.size().width() != event.oldSize().width():
            self._redraw.start()                        # the bubbles measured again for the new width
        if watched is getattr(self, 'well', None) and event.type() in (QEvent.Enter, QEvent.Leave):
            self.keep.setVisible(event.type() == QEvent.Enter)
        if watched is getattr(self, 'well', None) and event.type() == QEvent.Resize:
            self.keep.move(self.well.width() - self.keep.width(), 0)   # in the well's top-right corner
            self.keep.raise_()
        return super().eventFilter(watched, event)

    def _news(self, kind):
        if kind == 'peers':
            # Presence comes several times a second while someone plays the video; the card shows whether
            # anyone is online and who is in the call.
            shown = (collab_service.chat_alone(), [(p['handle'], p['me']) for p in collab_service.voice_state()['people']])
            if shown == self._shown_peers:
                return
            self._shown_peers = shown
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
        self.note.setText(_('collab.chat_alone'))
        self.keep.blockSignals(True)
        self.keep.setChecked(collab_service.chat_saved())
        self.keep.blockSignals(False)
        self._show_keep(self.keep.isChecked())
        self.send_button.setText(_('collab.chat_save') if self._editing else _('collab.chat_send'))
        self.cancel_button.setVisible(bool(self._editing))

        self._render_voice()

        # Built again only when what they show changed: the rest of the news (a click on the timeline goes
        # out as a change to the document) left the list empty for a moment, the scroll back at the top.
        me = collab_service.chat_me()
        shown = (repr(messages[-SHOWN:]), me, self.scroll.viewport().width())
        if shown == self._shown_messages:
            return
        self._shown_messages = shown
        self.scroll.setUpdatesEnabled(False)            # not seen until it is at the latest message again
        _clear(self.messages_box)
        if not messages:
            empty = _text('chat_empty')
            empty.setText(_('collab.chat_empty'))
            self.messages_box.addWidget(empty)
        previous = None
        for message in messages[-SHOWN:]:
            author = (message.get('author') or {}).get('handle')
            self.messages_box.addWidget(self._message(message, me, first=author != previous))
            previous = author
        self.messages_box.addStretch()
        self._to_bottom()

    def _to_bottom(self):
        """The latest message in view, always: once the rows are laid out (their heights come a pass later)."""
        bar = self.scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

        def settled():
            bar.setValue(bar.maximum())
            self.scroll.setUpdatesEnabled(True)
        QTimer.singleShot(60, settled)

    BUBBLE = 0.78                                       # at most this much of the chat's width
    FADE = 28                                           # px over which the messages fade out at the top

    def _message(self, message, me, first=True):
        """A message as a chat app shows one: a bubble, its own on the right (lighter), the others' on the left
        with their name on top (the first of a run); at its foot, small, the time, the place it points at, and
        for its own, Edit and Delete."""
        author = message.get('author') or {}
        name = author.get('name') or author.get('handle') or me
        mine = bool(message.get('pending') or author.get('handle') == me)
        foot = [_('collab.chat_sending') if message.get('pending') else _clock(message['at'])]
        if message.get('cue_id') or message.get('at_ms') is not None:
            foot.insert(0, f'<a href="go:{message.get("cue_id") or ""}:{message.get("at_ms") or ""}" '
                           f'style="color:#9fc4e4;">{self._place(message)}</a>')
        if message.get('edited'):
            foot.append(_('collab.chat_edited'))
        editable = mine and not message.get('pending') and not message.get('deleted')
        body = (f'<i>{_("collab.chat_deleted")}</i>' if message.get('deleted')
                else html.escape(message.get('text') or '').replace('\n', '<br>'))
        head = '' if mine or not first else f'<div style="color:#8fd19e; font-weight:700;">{html.escape(name)}</div>'
        # the foot's words on the right, in a line of their own: its own's Edit and Delete take that corner
        # while hovered, and the words then move to their left (_Bubble)
        meta_html = f'<div align="right" style="font-size:9px; color:rgba(184,206,224,0.55);">{" · ".join(foot)}</div>'
        label = _text('chat_bubble_text')
        meta = _text('chat_bubble_text')
        for text_label, text in ((label, head + f'<div>{body}</div>'), (meta, meta_html)):
            text_label.setTextFormat(Qt.RichText)
            text_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
            text_label.setText(text)
            text_label.linkActivated.connect(self._link)
        meta.setWordWrap(False)
        # as wide as its words (or its foot, with room for the buttons), up to BUBBLE of the chat: measured, so
        # the bubble is as tall as they need
        from PySide6.QtGui import QTextDocument
        widths = []
        for text_label, extra in ((label, 0), (meta, BUTTONS if editable else 0)):
            text_label.ensurePolished()
            document = QTextDocument()
            document.setDefaultFont(text_label.font())
            document.setHtml(text_label.text())
            widths.append(int(document.idealWidth()) + 4 + extra)
        widest = max(140, int(self.scroll.viewport().width() * self.BUBBLE)) - 20
        width = min(max(widths), widest)
        label.setFixedWidth(width)
        meta.setFixedWidth(width)

        bubble = _Bubble()
        if editable:
            bubble.add_button('chat_edit.svg', _('collab.chat_edit'), lambda: self._link(f'edit:{message["id"]}'))
            bubble.add_button('chat_delete.svg', _('collab.chat_delete'), lambda: self._link(f'delete:{message["id"]}'))
        bubble.setObjectName('chat_bubble')
        bubble.setProperty('mine', mine)
        bubble.setAttribute(Qt.WA_StyledBackground, True)
        bubble.foot = meta
        inside = QVBoxLayout(bubble)
        inside.setContentsMargins(10, 6, 10, 5)
        inside.setSpacing(0)
        inside.addWidget(label)
        inside.addWidget(meta)
        row = QWidget()
        row.setProperty('class', 'transparent_panel')
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0 if not first else 4, 0, 0)
        line.setSpacing(0)
        if mine:
            line.addStretch(1)
        line.addWidget(bubble)
        if not mine:
            line.addStretch(1)
        return row

    def _place(self, message):
        """'Line 12 · 00:01:23' for a message pointing at a subtitle and the playhead."""
        parts = []
        cue_id = message.get('cue_id')
        if cue_id:
            index = next((i for i, segment in enumerate(session.SUBTITLE.segments)
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
                selected = session.SUBTITLE.selected
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
        segment = next((s for s in session.SUBTITLE.segments if cue_id and s.get('id') == cue_id), None)
        if segment is not None:
            session.SUBTITLE.selected = segment
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
