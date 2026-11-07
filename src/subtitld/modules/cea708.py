"""CEA-708, digital television's closed captions: decode the caption
services a video's caption data carries, and write subtitles as service
1's pop-on captions.

Each frame of an ATSC (North American digital TV) video carries caption
data: CEA-608's bytes, for analog decoders, and CEA-708's caption channel,
a stream of packets that holds up to 63 services, each its own captions
(service 1 the main ones, the others often another language). A service is
a run of commands and characters: define a window (where it sits, its
anchor, its rows and columns), set the pen (italics, underline, colours),
write text (its size, where it sits against the line, the edge around it,
its italics, underline and colours), then show, hide, clear or delete
windows.

Decoding follows a decoder's windows:

- a window loaded out of sight and then shown (DisplayWindows,
  ToggleWindows, or defined visible) is a subtitle from the moment it
  shows to the moment it is hidden, cleared, deleted or changed; text
  written into a window already on screen, as live captions are, is a
  subtitle a line, from its first character to the next line's, as the
  SCC module reads roll-up captions;
- italics, underline and the pen's colour become formatting tags (white,
  the default, none); where the window and its text sit on the screen,
  one of the nine places;
- characters: ASCII (its delete is ♪), Latin-1, the G2 symbols (… ‘ ’ “
  ” • ™ Š Œ ⅛ …) and 16-bit ones (P16);
- packets are read whole, in the frame their last byte comes in; Delay
  holds the commands after it for its time.

Writing gives service 1 pop-on captions, in step with the CEA-608 ones the
SCC module writes: each is loaded into a hidden window (two in turn) in
the frames before it shows, after the last one showed, its rows and
styles as the text has them, and shown in the frame the CEA-608 caption
is, the one before deleted in the same packet, or at its end. A window is
anchored by percentages of the screen, so 4:3 and 16:9 decoders put it
alike: at the corner, edge or middle the subtitle's nine-place code names,
and that far in — the margins the subtitle itself gives (modules.position),
else the title-safe tenth. Nothing is rounded on the way: a margin is a
percentage of the picture and so is an anchor. The pen sits on the
background its run asks for, with the opacity it asks for, else on solid
black as a CEA-608 pen does — which is why a pen on solid black is read as
having no background of its own, and an edge drawn in black as having no
colour of its own. A run's size, its place against the line and its edge
go out with it, and come back; the font and the text tag a pen may also
carries name the typeface — one of the eight it has words for, which a run
keeps as its face — and what kind of speech it is, which a subtitle here
has nowhere to put. Characters CEA-708 lacks become their letter
without accents, or a 16-bit character.
"""

import unicodedata

from subtitld.modules import markup, position

LAST_DURATION = 4.0                      # seconds a caption still shown at the end lasts
_COLUMNS = 32                            # a row's columns, as 4:3 decoders show them
_ROWS = 15
_LEVELS = (0x00, 0x55, 0xAA, 0xFF)       # the four levels of each colour component

# Characters: G0 is ASCII (0x7F the note), G1 Latin-1, G2 these (after EXT1).
_G2 = {0x20: ' ', 0x21: ' ', 0x25: '…', 0x2A: 'Š', 0x2C: 'Œ', 0x30: '█', 0x31: '‘', 0x32: '’', 0x33: '“', 0x34: '”',
       0x35: '•', 0x39: '™', 0x3A: 'š', 0x3C: 'œ', 0x3D: '℠', 0x3F: 'Ÿ', 0x76: '⅛', 0x77: '⅜', 0x78: '⅝', 0x79: '⅞',
       0x7A: '│', 0x7B: '┐', 0x7C: '└', 0x7D: '─', 0x7E: '┘', 0x7F: '┌'}
# Lengths of the C1 commands (0x80-0x9F), with their parameters.
_C1_LENGTHS = [1] * 8 + [2, 2, 2, 2, 2, 2, 1, 1, 3, 4, 3, 1, 1, 1, 1, 5] + [7] * 8
# The predefined window styles' justification: 3 and 6 are centred.
_STYLE_JUSTIFY = {1: 0, 2: 0, 3: 2, 4: 0, 5: 0, 6: 2, 7: 0}
_LEFT, _RIGHT, _CENTER = 0, 1, 2
_SIDE = {_LEFT: 0, _CENTER: 1, _RIGHT: 2}          # justification: the anchor column it matches


def _color(value):
    """'#rrggbb' for a 708 colour (two bits a component), '' for white."""
    rgb = (_LEVELS[(value >> 4) & 3], _LEVELS[(value >> 2) & 3], _LEVELS[value & 3])
    return '' if rgb == (255, 255, 255) else '#{:02x}{:02x}{:02x}'.format(*rgb)


# How opaque a colour is, by the two bits above it: solid, flashing,
# half-way, and not at all. A flashing background is drawn as it is solid.
_OPACITY = (255, 255, 0x80, 0x00)
# The three sizes a pen has, as a proportion of the one a subtitle is drawn
# at; standard says nothing of its own.
_PEN_SIZES = {0: '80%', 1: '', 2: '120%'}
_SIZE_CODES = {size: code for code, size in _PEN_SIZES.items()}
# Where a pen sits against the line, and the edge it draws.
_PEN_OFFSETS = {0: 'sub', 1: '', 2: 'super'}
_OFFSET_CODES = {offset: code for code, offset in _PEN_OFFSETS.items()}
_PEN_EDGES = {0: '', 1: 'raised', 2: 'depressed', 3: 'uniform', 4: 'left-shadow', 5: 'right-shadow'}
_EDGE_CODES = {edge: code for code, edge in _PEN_EDGES.items()}
# The eight typefaces a pen may ask for: the first is whatever the decoder
# draws by default, the rest are the ones CEA-708 names (markup.FACES).
_FONT_TAGS = {code: name for code, name in enumerate(('',) + markup.FACES)}
_FACE_CODES = {name: code for code, name in _FONT_TAGS.items() if name}


def _background(value):
    """'#rrggbb', or '#rrggbbaa' where it is not solid, for a 708 pen
    background. Solid black is what a caption's pen sits on when nothing
    says otherwise, so it reads as no background of its own."""
    rgb = (_LEVELS[(value >> 4) & 3], _LEVELS[(value >> 2) & 3], _LEVELS[value & 3])
    alpha = _OPACITY[(value >> 6) & 3]
    if rgb == (0, 0, 0) and alpha == 255:
        return ''
    return '#{:02x}{:02x}{:02x}'.format(*rgb) + ('' if alpha == 255 else f'{alpha:02x}')


def _edge_color(value):
    """'#rrggbb' for the colour an edge is drawn in, '' for black, which is
    what an edge is drawn in when nothing says otherwise."""
    rgb = (_LEVELS[(value >> 4) & 3], _LEVELS[(value >> 2) & 3], _LEVELS[value & 3])
    return '' if rgb == (0, 0, 0) else '#{:02x}{:02x}{:02x}'.format(*rgb)


def _settled(style):
    """A pen with no edge has no colour for one: a decoder is told the edge
    colour whether an edge is drawn or not."""
    return style if style.edge else style._replace(edge_color='')


def _pen_background(background):
    """The pen colour byte for a Style's background: each component to two
    bits, and the opacity it asks for."""
    if not background:
        return 0x00                              # solid black, what a pen sits on
    rgb = tuple(int(background[at:at + 2], 16) for at in (1, 3, 5))
    alpha = int(background[7:9], 16) if len(background) == 9 else 255
    opacity = 3 if alpha < 0x40 else 2 if alpha < 0xC0 else 0
    return opacity << 6 | (round(rgb[0] / 85) << 4) | (round(rgb[1] / 85) << 2) | round(rgb[2] / 85)


class _Window:
    def __init__(self):
        self.defined = False
        self.visible = False
        self.params = None
        self.relative = False
        self.anchor_v = self.anchor_h = self.anchor_point = 0
        self.rows, self.columns = 1, _COLUMNS
        self.justify = _LEFT
        self.cells = {}                          # row: {column: (char, Style)}
        self.pen_row = self.pen_column = 0
        self.style = markup.PLAIN
        self.shown = None                        # the subtitle a show opened
        self.line = None                         # the line written on screen: {'start', 'row'}
        self.live = []                           # its lines' subtitles still open

    def has_text(self):
        return any(char.strip() for row in self.cells.values() for char, _style in row.values())


def _placed(window, rows, first, last):
    """(one of the nine places, the Place beyond it) for text in `rows` and
    columns `first` to `last`: where on the screen the window puts it.

    A window is anchored by percentages of the picture, which is what a
    margin is a percentage of, so what is left over after the nine places
    is read straight off the window's own geometry. A window exactly where
    the nine places alone would put it leaves NOWHERE behind.
    """
    point_column, point_row = window.anchor_point % 3, min(window.anchor_point // 3, 2)
    if window.relative:
        x, y = window.anchor_h / 100, window.anchor_v / 100
        width = window.columns * 5 / 210
    else:
        grid = 210 if window.anchor_h + window.columns * 5 * (1 - point_column / 2) > 160 else 160
        x, y = window.anchor_h / grid, window.anchor_v / 75
        width = window.columns * 5 / grid
    height = window.rows / 15
    left, top = x - point_column * width / 2, y - point_row * height / 2
    if window.relative:
        # A window placed by percentages is anchored at the edge its text is
        # read from, which says where it sits better than its own middle.
        vertical = 2 - point_row
    else:
        middle = top + ((rows[0] + rows[-1]) / 2 + 0.5) / window.rows * height
        vertical = 2 if middle < 1 / 3 else 0 if middle > 2 / 3 else 1
    span = (last - first + 1) / window.columns * width
    if window.justify == _CENTER:
        text_left = left + (width - span) / 2
    elif window.justify == _RIGHT:
        text_left = left + width - span
    else:
        text_left = left + first / window.columns * width
    if window.relative and _SIDE.get(window.justify) == point_column:
        horizontal = point_column
    else:
        centre = text_left + span / 2
        horizontal = 0 if centre < 0.4 else 2 if centre > 0.6 else 1
    text_top = top + rows[0] / window.rows * height
    text_bottom = top + (rows[-1] + 1) / window.rows * height
    down = (1 - text_bottom, (text_top + text_bottom) / 2 - 0.5, text_top)[vertical]
    across = (text_left, text_left + span / 2 - 0.5, 1 - text_left - span)[horizontal]
    return (1, 4, 7)[vertical] + horizontal, _beyond(across * 100, down * 100, vertical, horizontal)


def _beyond(across, down, vertical, horizontal):
    """The Place two margins say, against the title-safe tenth the nine
    places alone put text at: nothing where they agree to within half a
    percent of the picture, which is finer than a window is anchored."""
    place = position.NOWHERE
    if abs(down - (0.0 if vertical == 1 else 10.0)) > 0.5:
        place = place._replace(v=position.percent(down))
    if abs(across - (0.0 if horizontal == 1 else 10.0)) > 0.5:
        place = place._replace(h=position.percent(across))
    return place


def _render(window, rows=None):
    """(text with tags, keypad, where it sits beyond it) for what a window
    holds, or ('', 2, NOWHERE)."""
    lines, used, edges = [], [], []
    for row in sorted(rows if rows is not None else window.cells):
        cells = window.cells.get(row, {})
        filled = [column for column, (char, _style) in cells.items() if char.strip()]
        if not filled:
            continue
        first, last = min(filled), max(filled)
        lines.append([cells.get(column, (' ', markup.PLAIN)) for column in range(first, last + 1)])
        used.append(row)
        edges.append((first, last))
    if not lines:
        return '', 2, position.NOWHERE
    runs = []
    for index, line in enumerate(lines):
        if index:
            runs.append(('\n', markup.PLAIN))
        for column, (char, style) in enumerate(line):
            if char == ' ':
                # A space between two styles has what both have.
                before = next((s for c, s in reversed(line[:column]) if c != ' '), markup.PLAIN)
                after = next((s for c, s in line[column + 1:] if c != ' '), markup.PLAIN)
                style = markup.Style(*((a if a == b else '') if isinstance(a, str) else a and b
                                       for a, b in zip(before, after)))
            runs.append((char, style))
    keypad, place = _placed(window, used, min(first for first, _last in edges),
                            max(last for _first, last in edges))
    return markup.tidy(runs), keypad, place


class _Service:
    """A decoder for one service, its captions as subtitles."""

    def __init__(self):
        self.windows = [_Window() for _ in range(8)]
        self.current = None
        self.delay = None                        # the time the commands are held until
        self.subtitles = []

    def _close(self, seconds, subtitle):
        if subtitle is not None and subtitle['end'] is None:
            subtitle['end'] = max(subtitle['start'], seconds)

    # What windows show becomes subtitles.
    def _show(self, seconds, window, extend=False):
        """The window, on screen, shows what it holds now."""
        text, keypad, place = _render(window)
        old = window.shown
        if old and text == old['body']:
            return
        if extend and old and text:
            old_lines, new_lines = markup.plain(old['body']).split('\n'), markup.plain(text).split('\n')
            if len(new_lines) >= len(old_lines) and all(new.startswith(o) for o, new in zip(old_lines, new_lines)):
                old['body'], old['keypad'], old['place'] = text, keypad, place
                return
        self._close(seconds, old)
        window.shown = None
        if text:
            window.shown = {'start': seconds, 'end': None, 'body': text, 'keypad': keypad, 'place': place}
            self.subtitles.append(window.shown)

    def _hide(self, seconds, window):
        self._end_line(seconds, window, close=True)
        self._close(seconds, window.shown)
        window.shown = None

    def _end_line(self, seconds, window, close=False):
        """The line written on screen so far is a subtitle; with `close`,
        the window's lines end at `seconds`."""
        if window.line is not None:
            text, keypad, place = _render(window, [window.line['row']])
            if text:
                # Lines written on screen start at the window's left,
                # whatever their length, so their own columns say nothing
                # about where they are: a window placed by percentages is
                # taken at its word, any other read as centred.
                if not window.relative:
                    keypad, place = (keypad - 1) // 3 * 3 + 2, place._replace(h='')
                subtitle = {'start': window.line['start'], 'end': None, 'body': text,
                            'keypad': keypad, 'place': place}
                self.subtitles.append(subtitle)
                window.live.append(subtitle)
            window.line = None
        if close:
            for subtitle in window.live:
                self._close(seconds, subtitle)
            window.live = []

    def _typed(self, seconds, window):
        """Text went into a window on screen."""
        if window.shown is not None:
            self._show(seconds, window, extend=True)
        elif window.line is None:
            window.line = {'start': seconds, 'row': window.pen_row}
            for subtitle in window.live:
                self._close(seconds, subtitle)
            window.live = []

    # The decoder itself.
    def _window(self):
        return self.windows[self.current] if self.current is not None and self.windows[self.current].defined else None

    def _put(self, seconds, char):
        window = self._window()
        if window is None:
            return
        if window.line is not None and window.line['row'] != window.pen_row:
            self._end_line(seconds, window)
        window.cells.setdefault(window.pen_row, {})[window.pen_column] = (char, window.style)
        window.pen_column += 1
        if window.visible and char.strip():
            self._typed(seconds, window)

    def _each(self, mask):
        return [(index, self.windows[index]) for index in range(8) if mask >> index & 1]

    def run(self, seconds, data):
        """Carry out a service block's commands and characters, at `seconds`."""
        index = 0
        while index < len(data):
            if self.delay is not None:
                seconds = max(seconds, self.delay)
            code = data[index]
            if code == 0x10:                                 # EXT1: the extended sets
                if index + 1 >= len(data):
                    return
                extended = data[index + 1]
                if extended < 0x20:
                    length = 1 + (1, 2, 3, 4)[extended >> 3]
                elif extended < 0x80:
                    self._put(seconds, _G2.get(extended, '_'))
                    length = 2
                elif extended < 0x88:
                    length = 6
                elif extended < 0x90:
                    length = 7
                elif extended < 0xA0:
                    return                                   # variable length: nothing of ours
                else:
                    length = 2                               # G3: the CC logo, no character
            elif code < 0x20:
                length = 1 if code < 0x10 else 2 if code < 0x18 else 3
                if index + length > len(data):
                    return
                self._c0(seconds, code, data[index + 1:index + length])
            elif code < 0x80:
                self._put(seconds, '♪' if code == 0x7F else chr(code))
                length = 1
            elif code < 0xA0:
                length = _C1_LENGTHS[code - 0x80]
                if index + length > len(data):
                    return
                self._c1(seconds, code, data[index + 1:index + length])
            else:
                self._put(seconds, ' ' if code == 0xA0 else bytes([code]).decode('latin-1'))
                length = 1
            index += length

    def _c0(self, seconds, code, parameters):
        window = self._window()
        if code == 0x18 and len(parameters) == 2:            # P16: a 16-bit character
            self._put(seconds, chr(parameters[0] << 8 | parameters[1]))
        if window is None:
            return
        if code == 0x0D:                                     # CR
            self._end_line(seconds, window)
            if window.pen_row + 1 < window.rows:
                window.pen_row += 1
            else:
                window.cells = {row - 1: cells for row, cells in window.cells.items() if row > 0}
                if window.visible and window.shown is not None:
                    self._show(seconds, window)
            window.pen_column = 0
        elif code == 0x0E:                                   # HCR: the row again
            window.cells.pop(window.pen_row, None)
            window.pen_column = 0
        elif code == 0x0C:                                   # FF: the window blank
            self._hide(seconds, window)
            window.cells, window.pen_row, window.pen_column = {}, 0, 0
        elif code == 0x08:                                   # BS
            window.pen_column = max(window.pen_column - 1, 0)
            window.cells.get(window.pen_row, {}).pop(window.pen_column, None)
            if window.visible and window.shown is not None:
                self._show(seconds, window)

    def _c1(self, seconds, code, parameters):
        if code <= 0x87:                                     # CW0-7
            self.current = code - 0x80
        elif code == 0x88:                                   # ClearWindows
            for _index, window in self._each(parameters[0]):
                if window.defined:
                    self._hide(seconds, window)
                    window.cells, window.pen_row, window.pen_column = {}, 0, 0
        elif code in (0x89, 0x8A, 0x8B):                     # Display, Hide, Toggle
            for _index, window in self._each(parameters[0]):
                if not window.defined:
                    continue
                visible = True if code == 0x89 else False if code == 0x8A else not window.visible
                if visible and not window.visible:
                    window.visible = True
                    self._show(seconds, window)
                elif window.visible and not visible:
                    window.visible = False
                    self._hide(seconds, window)
        elif code == 0x8C:                                   # DeleteWindows
            for index, window in self._each(parameters[0]):
                if window.defined:
                    self._hide(seconds, window)
                self.windows[index] = _Window()
                if self.current == index:
                    self.current = None
        elif code == 0x8D:                                   # Delay, in tenths of a second
            self.delay = seconds + parameters[0] / 10
        elif code == 0x8E:                                   # DelayCancel
            self.delay = None
        elif code == 0x8F:                                   # Reset
            for window in self.windows:
                if window.defined:
                    self._hide(seconds, window)
            self.windows, self.current, self.delay = [_Window() for _ in range(8)], None, None
        elif code == 0x90:                                   # SetPenAttributes
            window = self._window()
            if window is not None:
                window.style = _settled(window.style._replace(
                    italic=bool(parameters[1] & 0x80), underline=bool(parameters[1] & 0x40),
                    size=_PEN_SIZES.get(parameters[0] & 0x03, ''),
                    offset=_PEN_OFFSETS.get((parameters[0] >> 2) & 0x03, ''),
                    edge=_PEN_EDGES.get((parameters[1] >> 3) & 0x07, ''),
                    face=_FONT_TAGS.get(parameters[1] & 0x07, '')))
        elif code == 0x91:                                   # SetPenColor
            window = self._window()
            if window is not None:
                window.style = _settled(window.style._replace(color=_color(parameters[0] & 0x3F),
                                                              background=_background(parameters[1]),
                                                              edge_color=_edge_color(parameters[2] & 0x3F)))
        elif code == 0x92:                                   # SetPenLocation
            window = self._window()
            if window is not None:
                if window.line is not None and parameters[0] & 0x0F != window.pen_row:
                    self._end_line(seconds, window)
                window.pen_row, window.pen_column = parameters[0] & 0x0F, parameters[1] & 0x3F
        elif code == 0x97:                                   # SetWindowAttributes
            window = self._window()
            if window is not None:
                window.justify = parameters[2] & 0x03
        elif code >= 0x98:                                   # DefineWindow 0-7
            self._define(seconds, code - 0x98, parameters)

    def _define(self, seconds, index, parameters):
        window = self.windows[index]
        self.current = index
        if window.defined and window.params == bytes(parameters):
            return
        new = not window.defined
        window.defined, window.params = True, bytes(parameters)
        window.relative = bool(parameters[1] & 0x80)
        window.anchor_v, window.anchor_h = parameters[1] & 0x7F, parameters[2]
        window.anchor_point = min(parameters[3] >> 4, 8)
        rows = (parameters[3] & 0x0F) + 1
        if not new and rows < window.rows:
            # Fewer rows: the top ones go.
            drop = window.rows - rows
            window.cells = {row - drop: cells for row, cells in window.cells.items() if row >= drop}
            window.pen_row = max(window.pen_row - drop, 0)
        window.rows, window.columns = rows, (parameters[4] & 0x3F) + 1
        style = (parameters[5] >> 3) & 0x07
        if new or style:
            window.justify = _STYLE_JUSTIFY.get(style or 1, _LEFT)
        if new or parameters[5] & 0x07:
            window.style = markup.PLAIN
        visible = bool(parameters[0] & 0x20)
        if visible and not window.visible:
            window.visible = True
            self._show(seconds, window)
        elif window.visible and not visible:
            window.visible = False
            self._hide(seconds, window)
        elif window.visible and window.shown is not None:
            self._show(seconds, window)

    def finish(self, seconds):
        for window in self.windows:
            self._end_line(seconds, window)
        for subtitle in self.subtitles:
            if subtitle['end'] is None:
                subtitle['end'] = round(subtitle['start'] + LAST_DURATION, 3)


def packets(batches):
    """[(frame, packet)] for the caption channel in `batches` [(frame,
    [(cc_type, byte, byte)])]: each packet whole, at the frame its last
    byte came in."""
    out, packet, size = [], None, 0
    for frame, triplets in batches:
        for cc_type, first, second in triplets:
            if cc_type == 3:
                code = first & 0x3F
                packet, size = bytearray([first, second]), 128 if code == 0 else code * 2
            elif cc_type == 2 and packet is not None:
                packet += bytes([first, second])
            else:
                continue
            if len(packet) >= size:
                out.append((frame, bytes(packet[:size])))
                packet = None
    return out


def blocks(packet):
    """[(service, data)] of a caption channel packet's service blocks."""
    out, index = [], 1
    while index < len(packet):
        header = packet[index]
        service, size = header >> 5, header & 0x1F
        index += 1
        if service == 0:
            break                                            # the null block: padding
        if service == 7:
            if index >= len(packet):
                break
            service = packet[index] & 0x3F
            index += 1
        out.append((service, packet[index:index + size]))
        index += size
    return out


def services(batches):
    """The services that hold data in `batches`, by number."""
    return sorted({service for _frame, packet in packets(batches) for service, data in blocks(packet) if data})


def decode(batches, rate, service=1):
    """The subtitles of one service in caption data: `batches` [(frame,
    [(cc_type, byte, byte)])] (cc_type 3 a packet's start, 2 the rest) at
    `rate` frames a second. [{'start', 'end', 'text'}], by start."""
    decoder = _Service()
    last = 0
    for frame, packet in packets(batches):
        for number, data in blocks(packet):
            if number == service:
                decoder.run(round(frame / rate, 3), data)
        last = frame
    for frame, _triplets in batches[-1:]:
        last = max(last, frame)
    decoder.finish(round(last / rate, 3))
    segments = []
    for subtitle in decoder.subtitles:
        if not subtitle.get('body'):
            continue
        keypad = subtitle['keypad']
        segments.append(position.store(
            {'start': subtitle['start'], 'end': max(subtitle['start'], subtitle['end']),
             'text': (markup.with_alignment('', keypad) if keypad != 2 else '') + subtitle['body']},
            subtitle.get('place') or position.NOWHERE))
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments


# Writing.
_TO_G2 = {char: code for code, char in _G2.items() if code > 0x21}


def _char(char):
    """The bytes that write a character."""
    if char == '♪':
        return [0x7F]
    code = ord(char)
    if 0x20 <= code < 0x7F:
        return [code]
    if code == 0xA0 or char in '\t':
        return [0x20]
    if 0xA0 < code <= 0xFF:
        return [code]
    if char in _TO_G2:
        return [0x10, _TO_G2[char]]
    # A letter with an accent CEA-708 lacks is its letter; the rest, 16 bits.
    plain = ''.join(c for c in unicodedata.normalize('NFKD', char) if not unicodedata.combining(c))
    if plain and plain != char and all(0x20 <= ord(c) <= 0xFF for c in plain):
        return [byte for c in plain for byte in _char(c)]
    if code <= 0xFFFF:
        return [0x18, code >> 8, code & 0xFF]
    return [0x3F]


def _wrap(line):
    """Rows of at most 32 columns from [(char, Style)], broken at the last
    space that fits, else at 32."""
    rows = []
    while len(line) > _COLUMNS:
        spaces = [index for index in range(1, _COLUMNS + 1) if line[index][0] == ' ']
        cut = spaces[-1] if spaces else _COLUMNS
        rows.append(line[:cut])
        line = line[cut:]
        while line and line[0][0] == ' ':
            line = line[1:]
    rows.append(line)
    return rows


def _rows(text):
    """The rows a subtitle's text is written in, [(char, Style)] each, and
    the keypad it sits at."""
    lead, rest = markup.split_lead(text)
    rows, line = [], []
    for run, style in markup.without_readings(markup.spans(rest)) + [('\n', markup.PLAIN)]:
        for char in run:
            if char == '\n':
                while line and line[0][0] == ' ':
                    line.pop(0)
                while line and line[-1][0] == ' ':
                    line.pop()
                rows.extend(row for row in _wrap(line) if row)
                line = []
            elif char.isprintable() or char == '\t':
                line.append((' ' if char == '\t' else char, style))
    return rows[:_ROWS], markup.alignment(lead)


def _two_bits(color, default=(255, 255, 255)):
    """A colour as a pen writes it: each component to two bits."""
    rgb = tuple(int(color[at:at + 2], 16) for at in (1, 3, 5)) if color else default
    return (round(rgb[0] / 85) << 4) | (round(rgb[1] / 85) << 2) | round(rgb[2] / 85)


def _pen(style):
    """SetPenAttributes and SetPenColor for a Style: the size, the place
    against the line and the edge it asks for, its italics and underline,
    and its colour (two bits a component) on the background it asks for,
    else on black as CEA-608's pen sits."""
    attributes = (_SIZE_CODES.get(style.size, 1)
                  | _OFFSET_CODES.get(style.offset, 1) << 2)
    # A typeface it does not name is whatever the decoder draws anyway; the
    # face stays in the text for the formats that can carry any of them.
    face = _FACE_CODES.get(style.face.strip().lower(), 0)
    edge = _EDGE_CODES.get(style.edge, 0) << 3
    return [[0x90, attributes,
             edge | face | (0x80 if style.italic else 0) | (0x40 if style.underline else 0)],
            [0x91, _two_bits(style.color), _pen_background(style.background),
             _two_bits(style.edge_color, (0, 0, 0))]]


def _anchor(keypad, place):
    """(% of the screen down, % across) a window at `keypad` is anchored
    at: `place`'s own margins from the edge it is aligned to, else the
    title-safe tenth the nine places alone put it at.

    CEA-708 places a window by percentages of the picture, which is what a
    margin is a percentage of, so a place maps straight onto one.
    """
    across, down = position.margins(place, keypad)
    vertical, horizontal = (keypad - 1) // 3, (keypad - 1) % 3          # 0 bottom ... 2 top; 0 left ...
    return (_within(((100 - down), (50 + down), down)[vertical]),
            _within((across, 50 + across, 100 - across)[horizontal]))


def _within(percent):
    return max(0, min(100, int(round(percent))))


def _define(window, keypad, rows, columns, visible, place=position.NOWHERE):
    """DefineWindow and SetWindowAttributes for a window of `rows` rows and
    `columns` columns where `keypad` and `place` put it, placed by
    percentages of the screen so 4:3 and 16:9 decoders put it alike."""
    vertical, horizontal = (keypad - 1) // 3, (keypad - 1) % 3          # 0 bottom ... 2 top; 0 left ...
    anchor_point = (2 - vertical) * 3 + horizontal
    anchor_v, anchor_h = _anchor(keypad, place)
    return [[0x98 + window,
             0x38 if visible else 0x18,                                 # visible or not; rows and columns locked
             0x80 | anchor_v,                                           # relative: % of the screen's height
             anchor_h,
             anchor_point << 4 | (rows - 1),
             columns - 1,
             0x09],                                                     # window and pen style 1
            # Transparent fill, bottom-to-top scroll, the place's justification.
            [0x97, 0xC0, 0x00, 0x0C | (_LEFT, _CENTER, _RIGHT)[horizontal], 0x00]]


def _typed(rows, pen, first_row=0):
    """(the commands that write `rows` from `first_row`, the pen they leave
    set to): a pen change where the look changes, then the characters."""
    units = []
    for index, row in enumerate(rows):
        if index:
            units.append([0x0D])                                        # CR: the next row
        elif first_row:
            units.append([0x92, first_row, 0])                          # SetPenLocation
        looks = [style._replace(bold=False, strike=False, ruby='') for _char_, style in row]
        for column, (char, _style) in enumerate(row):
            look = looks[column]
            if char == ' ':
                # A space between two styles has what both have.
                before = next((looks[i] for i in range(column - 1, -1, -1) if row[i][0] != ' '), markup.PLAIN)
                after = next((looks[i] for i in range(column + 1, len(row)) if row[i][0] != ' '), markup.PLAIN)
                look = markup.Style(*((a if a == b else '') if isinstance(a, str) else a and b
                                      for a, b in zip(before, after)))._replace(bold=False, strike=False, ruby='')
            if look != pen:
                units.extend(_pen(look))
                pen = look
            units.append(_char(char))
    return units, pen


def _load(text, window, place=position.NOWHERE):
    """The commands that load a subtitle into a hidden window."""
    rows, keypad = _rows(text)
    if not rows:
        return []
    columns = max(len(row) for row in rows)
    units = _define(window, keypad, len(rows), columns, False, place)
    units.insert(1, [0x88, 1 << window])                                # ClearWindows
    pen = None
    for index, row in enumerate(rows):
        units.append([0x92, index, 0])                                  # each row from its left
        written, pen = _typed([row], pen)
        units.extend(written)
    return units


def _roll(text, window, count, define, place=position.NOWHERE):
    """The commands that type a subtitle as roll-up lines in a window of
    `count` rows, on screen as they are typed: the window defined the first
    time, then each line after a CR has rolled the ones before it up."""
    rows, keypad = _rows(text)
    if not rows:
        return []
    units = []
    if define:
        units = _define(window, keypad, count, _COLUMNS, True, place)
        units.append([0x88, 1 << window])                               # ClearWindows
        units.append([0x92, count - 1, 0])                              # the pen on the base row
    else:
        units.append([0x80 + window])                                   # CW: write in this window
    pen = None
    for row in rows:
        units.append([0x0D])                                            # CR: roll the rows up
        written, pen = _typed([row], pen)
        units.extend(written)
    return units


def _paint(text, window, place=position.NOWHERE):
    """The commands that paint a subtitle onto the screen as it is typed:
    the window defined where the subtitle sits, visible and empty, then its
    text."""
    rows, keypad = _rows(text)
    if not rows:
        return []
    columns = max(len(row) for row in rows)
    units = _define(window, keypad, len(rows), columns, True, place)
    units.insert(1, [0x88, 1 << window])                                # ClearWindows
    units.append([0x92, 0, 0])
    written, _pen_ = _typed(rows, None)
    units.extend(written)
    return units


def _packets(units, service=1):
    """The caption channel packets that carry `units` (commands and
    characters, each whole in a service block), without their header."""
    blocks_out, block = [], []
    for unit in units:
        if len(block) + len(unit) > 31:
            blocks_out.append(block)
            block = []
        block.extend(unit)
    if block:
        blocks_out.append(block)
    out, packet = [], []
    for block in blocks_out:
        # A block header holds a service number of three bits. The services
        # above the sixth are named by an extended service number instead:
        # the header says 7, and the byte after it says which one.
        data = ([service << 5 | len(block)] if service < 7 else
                [7 << 5 | len(block), service & 0x3F]) + block
        if len(packet) + len(data) > 127:
            out.append(packet)
            packet = []
        packet.extend(data)
    if packet:
        out.append(packet)
    # With its header a packet's length is even: a null block makes it so.
    return [packet + [0x00] if len(packet) % 2 == 0 else packet for packet in out]


def _queue(shows, capacity, service, mode):
    """[(frame, order, packets)] for one service's captions of `mode`: a
    pop-on caption's load in the frames before it shows and its show on its
    frame; a roll-up or paint-on caption's text from the frame it starts,
    since it is read as it arrives."""
    queue = []                                   # (frame, order, packets)
    rows = int(mode[4]) if mode.startswith('roll') else 0
    visible, defined = None, False
    for index, show in enumerate(shows):
        # A show says where its subtitle sits where it has somewhere to say.
        text, shown, cleared = show[:3]
        place = show[3] if len(show) > 3 else position.NOWHERE
        last = index == len(shows) - 1
        if rows:
            # One window of its own, defined once: its lines roll up in it.
            units = _roll(text, 0, rows, not defined, place)
            if not units:
                continue
            if not defined:
                units.insert(0, [0x8C, 0xFF])    # DeleteWindows: a clean start
                defined = True
            queue.append((shown, 1, _packets(units, service)))
            if last and cleared is not None:
                queue.append((cleared, 0, _packets([[0x8C, 0x01]], service)))
            continue
        window = index % 2
        if mode == 'paint':
            units = _paint(text, window, place)
            if not units:
                continue
            # The one before it goes as this one is defined, so that only
            # one caption is on screen.
            units.insert(0, [0x8C, 1 << visible] if visible is not None else [0x8C, 0xFF])
            queue.append((shown, 1, _packets(units, service)))
            visible = window
            if cleared is not None:
                queue.append((cleared, 0, _packets([[0x8C, 1 << window]], service)))
                visible = None
            continue
        units = _load(text, window, place)
        if not units:
            continue
        if not queue:
            units.insert(0, [0x8C, 0xFF])        # DeleteWindows: a clean start
        loads = _packets(units, service)
        frames_needed = -(-sum((len(packet) + 1) // 2 for packet in loads) // capacity) + 1
        earliest = shows[index - 1][1] + 1 if index else 0
        queue.append((max(earliest, shown - frames_needed), 1, loads))
        # The last one deleted, then this one shown, in one packet: on
        # screen at once, and decoders that note what a window showed when
        # none is left see each caption apart.
        show = [[0x8C, 1 << visible]] if visible is not None else []
        show.append([0x89, 1 << window])
        queue.append((shown, 2, _packets(show, service)))
        visible = window
        if cleared is not None:
            queue.append((cleared, 0, _packets([[0x8C, 1 << window]], service)))
            visible = None
    return queue


def frame_data(shows, capacity, mode='pop', service=1):
    """{frame: [(cc_type, byte, byte)]} carrying `shows` [(text, frame it
    shows, frame it is cleared or None)] as `service`'s captions of `mode`
    ('pop', 'roll2', 'roll3', 'roll4' or 'paint'), at most `capacity` a
    frame."""
    return frames_for({service: shows}, capacity, mode)


def frames_for(by_service, capacity, mode='pop'):
    """{frame: [(cc_type, byte, byte)]} carrying the captions of several
    services, `by_service` {service number: shows}, in one caption channel.
    Packets go one after another, each sent from its frame on; a service's
    windows are its own, so services do not tread on each other."""
    queue = []
    for service, shows in sorted(by_service.items()):
        queue.extend(_queue(shows, capacity, service, mode))
    queue.sort(key=lambda entry: (entry[0], entry[1]))
    items = [(frame, packet) for frame, _order, packets in queue for packet in packets]

    out, sequence, index, pending = {}, 0, 0, []
    frame = items[0][0] if items else 0
    while index < len(items) or pending:
        triplets = []
        while len(triplets) < capacity:
            if not pending:
                if index < len(items) and items[index][0] <= frame:
                    packet = items[index][1]
                    header = (sequence % 4) << 6 | ((len(packet) + 1) // 2 % 64)
                    data = [header] + packet
                    pending = [(3 if i == 0 else 2, data[i], data[i + 1]) for i in range(0, len(data), 2)]
                    sequence += 1
                    index += 1
                else:
                    break
            take = min(capacity - len(triplets), len(pending))
            triplets.extend(pending[:take])
            pending = pending[take:]
        if triplets:
            out[frame] = triplets
        frame += 1
        if not pending and index < len(items):
            frame = max(frame, items[index][0])
    return out
