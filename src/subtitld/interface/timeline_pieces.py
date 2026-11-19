"""The pieces of the selected subtitle on the timeline, when it is timed word
by word, syllable by syllable or letter by letter (modules.segment).

They are chips along the bottom of its box, where each is sung. Dragging
the edge between two that touch moves both; with Alt, only one, opening a
gap the way it is dragged. An edge of its own (a gap's, the first start,
the last end) moves alone. Edges snap to the playhead and to the onsets
heard in the audio, as the timeline's snapping is on. A click on a chip
takes the playhead to where it starts; a double-click cuts it a level
finer. Each drag is one undo step.

The timeline calls these from its paint and mouse events; each mouse one
says whether it took the event.
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen

from subtitld.modules import history, markup, segment as segments, session

_HEIGHT = 16                # a chip, at most
_EDGE = 5                   # px either side of an edge that grab it
_SNAP = 6                   # px to the playhead or an onset that snap to it
_TOUCHING = 0.0005          # s: two edges this close are one

_CHIP = QColor(48, 66, 81, 215)
_CHIP_HOVER = QColor(62, 83, 99, 235)
_TEXT = QColor('#dfe8f0')
_EDGE_COLOR = QColor('#ffffff')


def _selected():
    subtitle = session.SUBTITLE.selected
    return subtitle if isinstance(subtitle, dict) and segments.timed_pieces(subtitle) else None


def paint(widget, painter, subtitle, box):
    """Draw `subtitle`'s pieces along the bottom of `box`, its rect on the
    timeline, and keep where they are for the mouse."""
    pieces = segments.timed_pieces(subtitle)
    if not pieces:
        return
    height = min(_HEIGHT, box.height() * 0.3)
    strip = QRectF(box.left(), box.bottom() - height - 3, box.width(), height)
    widget._pieces_strip = (subtitle.get('id'), strip)
    start = float(subtitle['start'])
    hover = getattr(widget, '_pieces_hover', None)
    font = QFont('Montserrat')
    font.setPixelSize(9)
    painter.save()
    painter.setFont(font)
    metrics = QFontMetrics(font)
    for number, piece in enumerate(pieces):
        left = (start + piece['start']) * widget.width_proportion
        right = (start + piece['end']) * widget.width_proportion
        rect = QRectF(left, strip.top(), max(1.0, right - left), strip.height())
        painter.setPen(Qt.NoPen)
        painter.setBrush(_CHIP_HOVER if hover == ('piece', number) else _CHIP)
        painter.drawRoundedRect(rect, 3, 3)
        words = markup.plain(piece['text']).strip()
        if rect.width() > 10 and words:
            painter.setPen(_TEXT)
            painter.drawText(rect.adjusted(3, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft,
                             metrics.elidedText(words, Qt.ElideRight, int(rect.width() - 5)))
    if hover and hover[0] == 'edge':
        x = _edge_x(widget, subtitle, hover[1], hover[2])
        painter.setPen(QPen(_EDGE_COLOR, 2))
        painter.drawLine(int(x), int(strip.top() - 2), int(x), int(strip.bottom() + 2))
    painter.restore()


def _edge_x(widget, subtitle, number, side):
    piece = segments.timed_pieces(subtitle)[number]
    at = piece['start'] if side == 'start' else piece['end']
    return (float(subtitle['start']) + at) * widget.width_proportion


def hit(widget, pos):
    """('edge', piece, 'start' | 'end' | 'between') or ('piece', piece) for
    a point on the selected subtitle's pieces, else None. 'between' is the
    edge piece `piece` ends on where the next one starts."""
    subtitle = _selected()
    strip = getattr(widget, '_pieces_strip', None)
    if subtitle is None or strip is None or strip[0] != subtitle.get('id'):
        return None
    rect = strip[1]
    if not rect.adjusted(-_EDGE, -3, _EDGE, 3).contains(pos):
        return None
    pieces = segments.timed_pieces(subtitle)
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
    time = pos.x() / widget.width_proportion - float(subtitle['start'])
    for number, piece in enumerate(pieces):
        if piece['start'] <= time < piece['end']:
            return 'piece', number
    return None


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


def press(widget, event):
    """A press on the pieces: an edge starts a drag, a chip takes the
    playhead to it. Whether it took the press."""
    found = hit(widget, event.position())
    if found is None:
        return False
    subtitle = _selected()
    if found[0] == 'edge':
        history.history_append()
        widget._pieces_drag = {'number': found[1], 'side': found[2], 'x': event.position().x(),
                               'alone': bool(event.modifiers() & Qt.AltModifier), 'which': None}
    else:
        piece = segments.timed_pieces(subtitle)[found[1]]
        player = getattr(widget.window(), 'preview_panel_player', None)
        if player is not None:
            player.set_position(float(subtitle['start']) + piece['start'])
        session.SUBTITLE.position = float(subtitle['start']) + piece['start']
    widget.update()
    event.accept()
    return True


def move(widget, event):
    """A drag of an edge goes on, or the pointer over the pieces shows what
    a press would do. Whether it took the move."""
    drag = getattr(widget, '_pieces_drag', None)
    subtitle = _selected()
    if drag is None or subtitle is None:
        found = hit(widget, event.position()) if not event.buttons() else None
        if found != getattr(widget, '_pieces_hover', None):
            widget._pieces_hover = found
            widget.update()
        if found is not None:
            widget.setCursor(Qt.SplitHCursor if found[0] == 'edge' else Qt.PointingHandCursor)
            return True
        return False
    time = _snapped(widget, subtitle, event.position().x() / widget.width_proportion - float(subtitle['start']))
    number, side = drag['number'], drag['side']
    pieces = segments.timed_pieces(subtitle)
    if side == 'between':
        if drag['alone'] and drag['which'] is None and abs(event.position().x() - drag['x']) > 2:
            # Alt: the piece on the side dragged towards moves, the other
            # stays, and a gap opens between them.
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
    """The end of a drag of an edge. Whether it took the release."""
    if getattr(widget, '_pieces_drag', None) is None:
        return False
    widget._pieces_drag = None
    session.set_unsaved()
    _changed(widget)
    event.accept()
    return True


def double_click(widget, event):
    """A double-click on a chip cuts it a level finer, its syllables found
    in the subtitle's language. Whether it took the double-click."""
    found = hit(widget, event.position())
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
