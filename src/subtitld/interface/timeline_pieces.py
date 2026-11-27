"""The pieces of a subtitle on the timeline, when it is timed word by word,
syllable by syllable or letter by letter (modules.segment).

They are chips where its text would be, each where it is sung, its words
in it. On the selected subtitle, dragging the edge between two that touch
moves both; with Alt or Shift, only one, opening a gap the way it is
dragged. An edge of its own (a gap's, the first start, the last end) moves
alone. Edges snap to the playhead and to the onsets heard in the audio, as
the timeline's snapping is on; with Shift, they don't. A click on a chip takes the playhead to where
it starts, a drag from one moves the subtitle as a drag from its text
would, and a double-click cuts it a level finer. With Shift held, a drag
from a chip moves that piece alone, its length kept, between the ones
beside it, and the subtitle is drawn locked: it stays where it is. With Ctrl held, a click
cuts it in two where the pointer is, its time shared by letters. Each
change is one undo step.

The timeline calls these from its paint and mouse events; each mouse one
says whether it took the event.
"""

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QMouseEvent, QPen
from PySide6.QtWidgets import QApplication

from subtitld.interface import theme
from subtitld.interface.timed_text_strip import CtrlWatch
from subtitld.modules import history, markup, segment as segments, session

_EDGE = 5                   # px either side of an edge that grab it
_SNAP = 6                   # px to the playhead or an onset that snap to it
_TOUCHING = 0.0005          # s: two edges this close are one
_CLICK = 3                  # px a press may move and still be a click

# A chip is its words' colour, faint: lighter on a box drawn dark, darker on
# one drawn light.
_CHIP_ALPHA = 28
_CHIP_HOVER_ALPHA = 60
_EDGE_COLOR = QColor('#ffffff')


def _selected():
    subtitle = session.SUBTITLE.selected
    return subtitle if isinstance(subtitle, dict) and segments.timed_pieces(subtitle) else None


def _words(painter, rect, words):
    """`words` in `rect`: on as many lines as it takes when they all fit,
    broken where a line may break (after a space or a hyphen), else
    anywhere; else on one, cut short."""
    metrics = QFontMetrics(painter.font())
    area = rect.toAlignedRect()
    for wrap in (Qt.TextWordWrap, Qt.TextWrapAnywhere):
        bounds = metrics.boundingRect(area, wrap, words)
        if bounds.width() <= area.width() and bounds.height() <= area.height():
            painter.drawText(rect, Qt.AlignLeft | Qt.AlignTop | wrap, words)
            return
    painter.drawText(rect, Qt.AlignLeft | Qt.AlignTop, metrics.elidedText(words, Qt.ElideRight, area.width()))


def paint(widget, painter, subtitle, box):
    """Draw `subtitle`'s pieces in `box`, where its text would be, in the
    painter's font and pen, and keep where they are for the mouse while it
    is the selected one."""
    pieces = segments.timed_pieces(subtitle)
    if not pieces:
        return
    selected = subtitle is _selected()
    if selected:
        widget._pieces_strip = (subtitle.get('id'), QRectF(box))
    start = float(subtitle['start'])
    hover = getattr(widget, '_pieces_hover', None) if selected else None
    color = painter.pen().color()
    painter.save()
    for number, piece in enumerate(pieces):
        left = (start + piece['start']) * widget.width_proportion
        right = (start + piece['end']) * widget.width_proportion
        rect = QRectF(left, box.top(), max(1.0, right - left), box.height())
        shade = QColor(color)
        shade.setAlpha(_CHIP_HOVER_ALPHA if hover and hover[0] in ('piece', 'cut') and hover[1] == number else _CHIP_ALPHA)
        painter.setPen(Qt.NoPen)
        painter.setBrush(shade)
        # A pixel apart from the next one, that two touching read as two.
        painter.drawRoundedRect(rect.adjusted(0, 0, -1 if rect.width() > 3 else 0, 0), 3, 3)
        if rect.width() <= 10:
            continue
        painter.setPen(color)
        if hover and hover[0] == 'cut' and hover[1] == number:
            # Where it would be cut, and its two halves.
            text, x = piece['text'], hover[3]
            for part, side in ((text[:hover[2]], QRectF(rect.left(), rect.top(), x - rect.left(), rect.height())),
                               (text[hover[2]:], QRectF(x, rect.top(), rect.right() - x, rect.height()))):
                if side.width() > 8:
                    _words(painter, side.adjusted(3, 3, -2, -2), markup.plain(part).strip())
            painter.setPen(QPen(theme.ink(_EDGE_COLOR), 1))
            painter.drawLine(int(x), int(rect.top() + 2), int(x), int(rect.bottom() - 2))
            continue
        words = markup.plain(piece['text']).strip()
        if words:
            _words(painter, rect.adjusted(3, 3, -2, -2), words)
    if hover and hover[0] == 'edge':
        x = _edge_x(widget, subtitle, hover[1], hover[2])
        painter.setPen(QPen(theme.ink(_EDGE_COLOR), 2))
        painter.drawLine(int(x), int(box.top() - 2), int(x), int(box.bottom() + 2))
    painter.restore()


def _edge_x(widget, subtitle, number, side):
    piece = segments.timed_pieces(subtitle)[number]
    at = piece['start'] if side == 'start' else piece['end']
    return (float(subtitle['start']) + at) * widget.width_proportion


def _cut_at(widget, subtitle, number, x):
    """(index, x) of where piece `number` would be cut nearest `x`: before
    one of its letters, the first excepted, at the time it would take
    (split_piece shares the time by letters). None for a piece of one."""
    piece = segments.timed_pieces(subtitle)[number]
    letters = [(index, character) for index, character in segments.letters_of(piece['text']) if not character.isspace()]
    if len(letters) < 2:
        return None
    left = (float(subtitle['start']) + piece['start']) * widget.width_proportion
    width = (piece['end'] - piece['start']) * widget.width_proportion
    places = [(index, left + width * count / len(letters)) for count, (index, _character) in enumerate(letters) if count]
    return min(places, key=lambda place: abs(place[1] - x))


def hit(widget, pos, ctrl=False):
    """('edge', piece, 'start' | 'end' | 'between'), ('piece', piece) or,
    with `ctrl`, ('cut', piece, index in its text, x) for a point on the
    selected subtitle's pieces, else None. 'between' is the edge piece
    `piece` ends on where the next one starts."""
    subtitle = _selected()
    strip = getattr(widget, '_pieces_strip', None)
    if subtitle is None or strip is None or strip[0] != subtitle.get('id'):
        return None
    rect = strip[1]
    if not rect.adjusted(-_EDGE, -3, _EDGE, 3).contains(pos):
        return None
    pieces = segments.timed_pieces(subtitle)
    time = pos.x() / widget.width_proportion - float(subtitle['start'])
    inside = next((number for number, piece in enumerate(pieces) if piece['start'] <= time < piece['end']), None)
    if ctrl:
        cut = _cut_at(widget, subtitle, inside, pos.x()) if inside is not None and rect.contains(pos) else None
        return ('cut', inside, *cut) if cut else None
    nearest = None
    for number, piece in enumerate(pieces):
        for side in ('start', 'end'):
            distance = abs(_edge_x(widget, subtitle, number, side) - pos.x())
            if distance <= _EDGE and (nearest is None or distance < nearest[0]):
                nearest = (distance, number, side)
    if nearest is not None:
        _distance, number, side = nearest
        if side == 'start' and number and abs(pieces[number - 1]['end'] - pieces[number]['start']) < _TOUCHING:
            return 'edge', number - 1, 'between'
        if side == 'end' and number + 1 < len(pieces) and abs(pieces[number]['end'] - pieces[number + 1]['start']) < _TOUCHING:
            return 'edge', number, 'between'
        return 'edge', number, side
    return ('piece', inside) if inside is not None else None


def _snapped(widget, subtitle, time):
    """`time` (from the subtitle's start) moved to the playhead or an onset
    heard in the audio within a few pixels, as the timeline snaps."""
    if not session.CONFIG.get('timeline', {}).get('snap', True):
        return time
    start = float(subtitle['start'])
    targets = []
    position = session.SUBTITLE.position
    if position is not None:
        targets.append(float(position) - start)
    onsets = getattr(widget, 'background_onsets', None)
    if onsets is not None and len(onsets):
        near = (start + time - 1.0, start + time + 1.0)
        targets += [float(onset) - start for onset in onsets if near[0] <= onset <= near[1]]
    reach = _SNAP / widget.width_proportion
    close = [target for target in targets if abs(target - time) <= reach]
    return min(close, key=lambda target: abs(target - time)) if close else time


def _ctrl(event):
    return bool(event.modifiers() & Qt.ControlModifier)


def _shift(event):
    return bool(event.modifiers() & Qt.ShiftModifier)


def held(widget, subtitle):
    """Whether `subtitle` is drawn locked: the selected one, timed, while
    Shift is held over the timeline."""
    return bool(getattr(widget, '_pieces_shift', False)) and subtitle is _selected()


def _hold(widget, shift):
    if shift != getattr(widget, '_pieces_shift', False):
        widget._pieces_shift = shift
        widget.update()


def _hover(widget, pos, ctrl, shift=False):
    """Show what a press at `pos` (None: nowhere) would do. Whether it is
    the pieces' to show: an edge, a cut, or with `shift` a piece to move."""
    _hold(widget, shift)
    found = hit(widget, pos, ctrl) if pos is not None else None
    if found != getattr(widget, '_pieces_hover', None):
        widget._pieces_hover = found
        widget.update()
    if found is not None and found[0] in ('edge', 'cut'):
        widget.setCursor(Qt.SplitHCursor)
        return True
    if found is not None and found[0] == 'piece' and shift:
        widget.setCursor(Qt.SizeAllCursor)
        return True
    return False


def _watch(widget):
    """While the pointer is over the timeline, Ctrl or Shift pressed or let
    go shows again what a press would do, as a move of the pointer would."""
    watch = getattr(widget, '_pieces_ctrl', None)
    if watch is None:
        def changed(held):
            if widget.underMouse() and not QApplication.mouseButtons():
                key = Qt.ShiftModifier if watch.key == Qt.Key_Shift else Qt.ControlModifier
                modifiers = QApplication.keyboardModifiers() & (Qt.ControlModifier | Qt.ShiftModifier)
                modifiers = (modifiers | key) if held else (modifiers & ~key)
                at = widget.mapFromGlobal(QCursor.pos()).toPointF()
                widget.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, at, widget.mapToGlobal(at), Qt.NoButton,
                                                  Qt.NoButton, modifiers))
        watch = widget._pieces_ctrl = CtrlWatch(changed, widget, keys=(Qt.Key_Control, Qt.Key_Shift))
    watch.start()


def leave(widget):
    """The pointer left the timeline."""
    if getattr(widget, '_pieces_ctrl', None) is not None:
        widget._pieces_ctrl.stop()
    _hold(widget, False)
    if getattr(widget, '_pieces_hover', None) is not None:
        widget._pieces_hover = None
        widget.update()


def press(widget, event):
    """A press on the pieces: an edge starts a drag; with Ctrl, a chip is
    cut there; with Shift, a drag from a chip moves its piece. Else on a
    chip, the press is the timeline's (a drag moves the subtitle), and a
    click takes the playhead to it when let go (release). Whether it took
    the press."""
    found = hit(widget, event.position(), _ctrl(event))
    widget._pieces_click = None
    if found is None:
        return False
    if found[0] == 'piece':
        widget._pieces_click = (found[1], event.position().x())
        if not _shift(event):
            return False
        subtitle = _selected()
        grab = event.position().x() / widget.width_proportion - float(subtitle['start'])
        widget._pieces_drag = {'number': found[1], 'side': 'piece', 'x': event.position().x(),
                               'grab': grab - segments.timed_pieces(subtitle)[found[1]]['start'], 'moved': False}
        event.accept()
        return True
    history.history_append()
    if found[0] == 'cut':
        if segments.split_piece(_selected(), found[1], found[2]):
            session.set_unsaved()
        widget._pieces_hover = None
        _changed(widget)
    else:
        widget._pieces_drag = {'number': found[1], 'side': found[2], 'x': event.position().x(),
                               'alone': bool(event.modifiers() & (Qt.AltModifier | Qt.ShiftModifier)), 'which': None}
    widget.update()
    event.accept()
    return True


def move(widget, event):
    """A drag of an edge goes on, or the pointer over the pieces shows what
    a press would do. Whether it took the move."""
    drag = getattr(widget, '_pieces_drag', None)
    subtitle = _selected()
    if drag is None or subtitle is None:
        _watch(widget)
        return _hover(widget, None if event.buttons() else event.position(), _ctrl(event), _shift(event))
    _hold(widget, _shift(event))
    time = event.position().x() / widget.width_proportion - float(subtitle['start'])
    if not _shift(event):
        time = _snapped(widget, subtitle, time)
    number, side = drag['number'], drag['side']
    pieces = segments.timed_pieces(subtitle)
    if side == 'piece':
        if not drag['moved'] and abs(event.position().x() - drag['x']) <= _CLICK:
            event.accept()
            return True
        if not drag['moved']:
            # The undo step is pushed once it is a drag, not a click.
            history.history_append()
            drag['moved'] = True
        segments.move_piece(subtitle, number, time - drag['grab'])
    elif side == 'between':
        if drag['alone'] and drag['which'] is None and abs(event.position().x() - drag['x']) > 2:
            # Alt or Shift: the piece on the side dragged towards moves, the
            # other stays, and a gap opens between them.
            drag['which'] = 'after' if event.position().x() > drag['x'] else 'before'
        if drag['which'] == 'after':
            segments.time_piece(subtitle, number + 1, start=time)
        elif drag['which'] == 'before':
            segments.time_piece(subtitle, number, end=time)
        elif not drag['alone']:
            low, high = pieces[number]['start'], pieces[number + 1]['end']
            at = round(min(max(low, time), high), 3)
            pieces[number]['end'], pieces[number + 1]['start'] = at, at
    else:
        segments.time_piece(subtitle, number, **{side: time})
    widget.update()
    event.accept()
    return True


def release(widget, event):
    """The end of a drag of an edge, or of a click on a chip: the playhead
    to where it starts, the piece chosen in the subtitles list. Whether it
    took the release; a click's is the timeline's too."""
    click, widget._pieces_click = getattr(widget, '_pieces_click', None), None
    drag = getattr(widget, '_pieces_drag', None)
    if drag is not None and drag['side'] == 'piece' and not drag['moved']:
        # A Shift-click that moved nothing: a click.
        widget._pieces_drag = None
        widget.update()
    if getattr(widget, '_pieces_drag', None) is None:
        subtitle = _selected()
        pieces = segments.timed_pieces(subtitle)
        if click is not None and click[0] < len(pieces) and abs(event.position().x() - click[1]) <= _CLICK:
            at = float(subtitle['start']) + pieces[click[0]]['start']
            window = widget.window()
            if getattr(window, 'preview_panel_player', None) is not None:
                window.preview_panel_player.set_position(at)
            session.SUBTITLE.position = at
            if getattr(window, 'left_panel_subtitleslist_timed_text', None) is not None:
                window.left_panel_subtitleslist_timed_text.choose(click[0])
        return False
    widget._pieces_drag = None
    session.set_unsaved()
    _changed(widget)
    event.accept()
    return True


def double_click(widget, event):
    """A double-click on a chip cuts it a level finer, its syllables found
    in the subtitle's language. Whether it took the double-click."""
    found = hit(widget, event.position(), _ctrl(event))
    if found is None or found[0] != 'piece':
        return False
    history.history_append()
    if segments.refine_piece(_selected(), found[1], segments.hyphenator(session.SUBTITLE.language)) > 1:
        session.set_unsaved()
    _changed(widget)
    event.accept()
    return True


def _changed(widget):
    from subtitld.interface import left_panel
    widget.update()
    window = widget.window()
    try:
        left_panel.update(window)
    except AttributeError:
        pass


# --- tapping ----------------------------------------------------------------
_tapping = {'id': None, 'next': 0}


def tap(window):
    """A tap while the playhead is in a timed subtitle: the next of its
    pieces starts here and the one before ends here, one after another from
    the first; a tap after the last ends it. Tapping another subtitle, one
    finished, or the same one again before where the taps got to, starts
    over from its first piece. Whether there was a subtitle to tap."""
    position = session.SUBTITLE.position
    if position is None:
        return False
    position = float(position)
    subtitle = next((s for s in session.SUBTITLE.segments if segments.timed_pieces(s)
                     and float(s['start']) <= position < float(s['end'])), None)
    if subtitle is None:
        return False
    pieces = segments.timed_pieces(subtitle)
    at = position - float(subtitle['start'])
    number = _tapping['next'] if _tapping['id'] == subtitle.get('id') else 0
    if number and (number > len(pieces) or at < pieces[number - 1]['start']):
        number = 0
    history.history_append()
    segments.tap(subtitle, number, at)
    _tapping.update(id=subtitle.get('id'), next=number + 1)
    session.set_unsaved()
    timeline = getattr(window, 'timeline_widget', None)
    if timeline is not None:
        _changed(timeline)
    return True
