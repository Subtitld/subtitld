"""Scenarist SCC (.scc): read the CEA-608 captions broadcast files carry,
write pop-on captions every decoder shows as they were meant.

An SCC file is TV captions as they travel in line 21 of an NTSC picture:
after a timecode, the two bytes each frame carries, in hex ("9420"), at
29.97 frames a second. The bytes are commands (load a caption, show it,
clear the screen, go to row 15, column 4, turn italics on ...) and the
characters of the captions, two a frame, from CEA-608's own character set.

Reading decodes them as a caption decoder does, on its screen of 15 rows
of 32 columns:

- pop-on captions (loaded out of sight, shown at once) are subtitles from
  the frame they show to the frame they are cleared or replaced; paint-on
  ones, painted on screen, from their first characters to their last
  state; roll-up ones, line after line as in live captions, one subtitle a
  line, from its first character to the next line's;
- italics, underline and colours (white, green, blue, cyan, red, yellow,
  magenta) become formatting tags; where the caption sits on the screen,
  its rows and columns, one of the nine places;
- the spaces the codes that change a style take up on screen are not kept
  where they would double a space or stand before punctuation;
- timecodes with ";" are drop-frame, with ":" non-drop-frame;
- the first channel (CC1) is read, the second when the first is empty;
  control codes sent twice count once; parity bits are not checked.

Writing gives pop-on captions in CC1: each loaded in the frames before it
shows (RCL, ENM, a PAC for each row, its text) and shown (EOC) on the
frame it starts, on a line of its own so that decoders reading a line at
a time show it on time too; cleared (EDM) where it ends, unless the next
one replaces it. Lines longer than 32 columns are wrapped; each row is
centred, or put left or right, top, middle or bottom as the subtitle
asks. Italics, underline and colours go as PAC and mid-row codes, the
codes put where spaces are (punctuation against a word takes its style,
so that no code puts a space before it). When asked, ">> " marks each
change of speaker, as captioners do. A character CEA-608 has only in its extended
set goes after its plain fallback (É after E), as decoders expect; one it
lacks becomes its letter without accents, or "?".
"""

import re
import unicodedata

from subtitld.modules import markup
from subtitld.modules.srt import decode

LAST_DURATION = 4.0                      # seconds a caption still shown at the end lasts
_RATE = 30000 / 1001
_ROWS, _COLUMNS = 15, 32
_TIMECODE = r'(\d{1,2})[:;.,](\d{2})[:;.,](\d{2})([:;.,])(\d{2})'
_LINE = re.compile(r'^\s*(' + _TIMECODE + r')\s+(.*)$')
_WORD = re.compile(r'\b[0-9a-fA-F]{4}\b')

# CEA-608's characters: the basic set (ASCII but for ten), the special
# characters (0x11 0x30-0x3F) and the two extended sets (0x12, 0x13).
_BASIC = {code: chr(code) for code in range(0x20, 0x80)}
_BASIC.update({0x2A: 'á', 0x5C: 'é', 0x5E: 'í', 0x5F: 'ó', 0x60: 'ú', 0x7B: 'ç', 0x7C: '÷', 0x7D: 'Ñ', 0x7E: 'ñ',
               0x7F: '█'})
_SPECIAL = dict(zip(range(0x30, 0x40), '®°½¿™¢£♪à èâêîôû'))
_EXTENDED = {0x12: dict(zip(range(0x20, 0x40), 'ÁÉÓÚÜü‘¡*’—©℠•“”ÀÂÇÈÊËëÎÏïÔÙùÛ«»')),
             0x13: dict(zip(range(0x20, 0x40), 'ÃãÍÌìÒòÕõ{}\\^_¦~ÄäÖöß¥¤|ÅåØø┌┐└┘'))}
_COLORS = ('', '#00ff00', '#0000ff', '#00ffff', '#ff0000', '#ffff00', '#ff00ff')    # white first
# The first byte of a PAC and the second's base, by row.
_PAC_ROWS = {1: (0x11, 0x40), 2: (0x11, 0x60), 3: (0x12, 0x40), 4: (0x12, 0x60), 5: (0x15, 0x40), 6: (0x15, 0x60),
             7: (0x16, 0x40), 8: (0x16, 0x60), 9: (0x17, 0x40), 10: (0x17, 0x60), 11: (0x10, 0x40), 12: (0x13, 0x40),
             13: (0x13, 0x60), 14: (0x14, 0x40), 15: (0x14, 0x60)}
_ROW_OF_PAC = {pac: row for row, pac in _PAC_ROWS.items()}
_RCL, _BS, _DER, _RU2, _RU3, _RU4, _RDC, _TR, _RTD, _EDM, _CR, _ENM, _EOC = (
    0x20, 0x21, 0x24, 0x25, 0x26, 0x27, 0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x2E, 0x2F)
_OPENING = '([{¿¡“‘«'
_CLOSING = ',.;:!?)]}’”»…'


def frame_of(timecode):
    """The frame (at 29.97 a second, from 0) an SCC timecode names: drop-frame
    with ";" (or "." or ","), non-drop-frame with ":"."""
    hours, minutes, seconds, separator, frames = re.fullmatch(_TIMECODE, timecode.strip()).groups()
    count = (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 30 + int(frames)
    if separator != ':':
        all_minutes = int(hours) * 60 + int(minutes)
        count -= 2 * (all_minutes - all_minutes // 10)
    return count


def timecode(frame, drop_frame=True):
    """The SCC timecode of a frame: HH:MM:SS;FF drop-frame, HH:MM:SS:FF not."""
    frame = max(0, int(frame))
    if drop_frame:
        tens, rest = divmod(frame, 17982)          # frames in ten minutes
        frame += 18 * tens + (2 * ((rest - 2) // 1798) if rest >= 2 else 0)
    hours, rest = divmod(frame, 108000)
    minutes, rest = divmod(rest, 1800)
    seconds, frames = divmod(rest, 30)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}{";" if drop_frame else ":"}{frames:02d}'


def _seconds(frame):
    return round(frame / _RATE, 3)


class _Cell:
    __slots__ = ('char', 'style', 'attribute')

    def __init__(self, char, style, attribute=False):
        self.char, self.style, self.attribute = char, style, attribute


def _blank():
    return {row: [None] * _COLUMNS for row in range(1, _ROWS + 1)}


def _render(memory, rows=None):
    """(text with tags, keypad) for what a memory shows, or ('', 2)."""
    lines, edges, used = [], [], []
    for row in rows or range(1, _ROWS + 1):
        cells = memory[row]
        filled = [index for index, cell in enumerate(cells) if cell is not None and cell.char.strip()]
        if not filled:
            continue
        first, last = filled[0], filled[-1]
        span = [cells[index] or _Cell(' ', markup.PLAIN, True) for index in range(first, last + 1)]
        out = []
        for index, cell in enumerate(span):
            if cell.char != ' ':
                out.append((cell.char, cell.style))
                continue
            before = next((c for c in reversed(span[:index]) if c.char != ' '), None)
            after = next((c for c in span[index + 1:] if c.char != ' '), None)
            # A code's space before punctuation, or after an opening one,
            # is no space of the text's.
            if cell.attribute and ((after and after.char in _CLOSING) or (before and before.char in _OPENING)):
                continue
            # A space between two styles has what both have.
            if before and after:
                style = markup.Style(*((a if a == b else '') if isinstance(a, str) else a and b
                                       for a, b in zip(before.style, after.style)))
            else:
                style = markup.PLAIN
            out.append((' ', style))
        lines.append(out)
        edges.append((first, last))
        used.append(row)
    if not lines:
        return '', 2
    runs = []
    for index, line in enumerate(lines):
        if index:
            runs.append(('\n', markup.PLAIN))
        runs.extend(line)
    text = markup.tidy(runs)
    middle_row = (used[0] + used[-1]) / 2
    vertical = 2 if middle_row <= 5 else 0 if middle_row >= 10 else 1
    centre = (min(first for first, _last in edges) + max(last for _first, last in edges) + 1) / 2
    horizontal = 0 if centre < 12 else 2 if centre > 20 else 1
    return text, (1, 4, 7)[vertical] + horizontal


class _Decoder:
    """A CEA-608 decoder for one channel, its captions as subtitles."""

    def __init__(self, channel):
        self.channel = channel
        self.displayed, self.loading = _blank(), _blank()
        self.mode = 'pop'
        self.roll_rows = 2
        self.row, self.column = _ROWS, 0
        self.style = markup.PLAIN
        self.data_channel = 1
        self.last_code, self.skipped = None, False
        self.subtitles = []
        self.current = None                        # the pop-on or paint-on caption shown
        self.line = None                           # the roll-up line being written
        self.dirty = None                          # the frame paint-on first changed the screen

    # What a caption shows becomes subtitles.
    def _close(self, frame):
        if self.current:
            self.current['end'] = max(self.current['start'], _seconds(frame))
            self.current = None

    def _show(self, frame, rows=None, extend=False):
        """The screen changed at `frame`: the caption it shows replaces the last."""
        text, keypad = _render(self.displayed, rows)
        if self.current and text == self.current['body']:
            return
        old_lines, new_lines = markup.plain(self.current['body'] if self.current else '').split('\n'), \
            markup.plain(text).split('\n')
        if extend and self.current and text and len(new_lines) >= len(old_lines) and all(
                new.startswith(old) for old, new in zip(old_lines, new_lines)):
            self.current['body'], self.current['keypad'] = text, keypad
            return
        self._close(frame)
        if text:
            self.current = {'start': _seconds(frame), 'end': None, 'body': text, 'keypad': keypad}
            self.subtitles.append(self.current)

    def _end_line(self, frame, close=False):
        """The roll-up line written so far is a subtitle; with `close`, so is
        its end, at `frame`."""
        if self.line and self.line['start'] is not None:
            text, keypad = _render(self.displayed, [self.row])
            if text:
                # Roll-up lines start at the left edge, whatever their
                # length: only their row says where they are.
                self.line.update(body=text, keypad=(keypad - 1) // 3 * 3 + 2)
                self.subtitles.append(self.line)
        if close:
            for subtitle in self.subtitles:
                if subtitle['end'] is None and subtitle is not self.current:
                    subtitle['end'] = max(subtitle['start'], _seconds(frame))
        self.line = {'start': None, 'end': None}

    def batch_end(self):
        if self.dirty is not None and self.mode == 'paint':
            self._show(self.dirty, extend=True)
        self.dirty = None

    def finish(self, frame):
        self.batch_end()
        if self.mode == 'roll':
            self._end_line(frame)
        for subtitle in self.subtitles:
            if subtitle['end'] is None:
                subtitle['end'] = round(subtitle['start'] + LAST_DURATION, 3)

    # The decoder itself.
    def _memory(self):
        return self.loading if self.mode == 'pop' else self.displayed

    def _put(self, frame, char, attribute=False):
        if self.mode == 'text':
            return
        if self.mode == 'roll' and self.line is not None and self.line['start'] is None and char.strip():
            self.line['start'] = _seconds(frame)
            for subtitle in self.subtitles:
                if subtitle['end'] is None and subtitle is not self.current:
                    subtitle['end'] = max(subtitle['start'], self.line['start'])
        # The cursor goes one past the last column; what comes after takes
        # the last column's place.
        self._memory()[self.row][min(self.column, _COLUMNS - 1)] = _Cell(char, self.style, attribute)
        self.column = min(self.column + 1, _COLUMNS)
        if self.mode == 'paint' and self.dirty is None:
            self.dirty = frame

    def _changed(self, frame):
        if self.mode == 'paint' and self.dirty is None:
            self.dirty = frame

    def word(self, frame, first, second):
        first, second = first & 0x7F, second & 0x7F
        if 0x10 <= first <= 0x1F:
            code = (first, second)
            if code == self.last_code and not self.skipped:
                self.skipped = True
                return
            self.last_code, self.skipped = code, False
            self.data_channel = 2 if first & 0x08 else 1
            if self.data_channel == self.channel:
                self._code(frame, first & 0x17, second)
            return
        self.last_code, self.skipped = None, False
        if first < 0x10 or self.data_channel != self.channel:
            return                                 # extended data services, or the other channel
        for byte in (first, second):
            if byte >= 0x20:
                self._put(frame, _BASIC[byte])

    def _code(self, frame, first, second):
        if first in (0x14, 0x15) and 0x20 <= second <= 0x2F:
            self._command(frame, second)
        elif first == 0x11 and 0x20 <= second <= 0x2F:
            self._mid_row(frame, second)
        elif first == 0x11 and 0x30 <= second <= 0x3F:
            self._put(frame, _SPECIAL[second])
        elif first in _EXTENDED and 0x20 <= second <= 0x3F:
            # Sent after its plain fallback, which it replaces.
            self.column = max(self.column - 1, 0)
            self._put(frame, _EXTENDED[first][second])
        elif first == 0x17 and 0x21 <= second <= 0x23:
            self.column = min(self.column + second - 0x20, _COLUMNS)
        elif first == 0x17 and second in (0x2E, 0x2F):
            self._put(frame, ' ', attribute=True)
            self.style = markup.Style(False, False, bool(second & 1), False, '#000000')
        elif 0x40 <= second <= 0x7F and (first, second & 0x60) in _ROW_OF_PAC:
            self._pac(frame, _ROW_OF_PAC[(first, second & 0x60)], second & 0x1F)

    def _pac(self, frame, row, attribute):
        if self.mode == 'roll':
            if row != self.row:
                # The roll-up window moves to the new base row.
                rows = {r: self.displayed[r] for r in range(max(1, self.row - self.roll_rows + 1), self.row + 1)}
                for r in rows:
                    self.displayed[r] = [None] * _COLUMNS
                for r, cells in rows.items():
                    target = r - self.row + row
                    if 1 <= target <= _ROWS:
                        self.displayed[target] = cells
        self.row = row
        underline = bool(attribute & 1)
        if attribute >= 0x10:
            self.column = ((attribute - 0x10) >> 1) * 4
            self.style = markup.Style(False, False, underline, False, '')
        else:
            self.column = 0
            look = attribute >> 1
            self.style = markup.Style(look == 7, False, underline, False, _COLORS[look] if look < 7 else '')

    def _mid_row(self, frame, second):
        self._put(frame, ' ', attribute=True)
        look, underline = (second - 0x20) >> 1, bool(second & 1)
        if look == 7:
            self.style = markup.Style(True, False, underline, False, '')
        else:
            self.style = markup.Style(False, False, underline, False, _COLORS[look])

    def _command(self, frame, second):
        if second == _RCL:
            if self.mode == 'roll':
                self._end_line(frame, close=True)
            self.mode = 'pop'
        elif second in (_RU2, _RU3, _RU4):
            if self.mode != 'roll':
                self._close(frame)
                self.displayed, self.loading = _blank(), _blank()
                self.row, self.column = _ROWS, 0
                self.line = {'start': None, 'end': None}
            self.mode = 'roll'
            self.roll_rows = second - _RU2 + 2
        elif second == _RDC:
            if self.mode == 'roll':
                self._end_line(frame, close=True)
            self.mode = 'paint'
        elif second in (_TR, _RTD):
            if self.mode == 'roll':
                self._end_line(frame, close=True)
            self.mode = 'text'
        elif second == _EOC:
            if self.mode == 'roll':
                self._end_line(frame, close=True)
            self.displayed, self.loading = self.loading, self.displayed
            self.mode = 'pop'
            self._show(frame)
        elif second == _EDM:
            if self.mode == 'roll':
                self._end_line(frame, close=True)
                self.displayed = _blank()
            else:
                self.displayed = _blank()
                self._show(frame)
        elif second == _ENM:
            self.loading = _blank()
        elif second == _CR:
            if self.mode == 'roll':
                self._end_line(frame)
                top = max(1, self.row - self.roll_rows + 1)
                for r in range(top, self.row):
                    self.displayed[r] = self.displayed[r + 1]
                if top > 1:
                    self.displayed[top - 1] = [None] * _COLUMNS
                self.displayed[self.row] = [None] * _COLUMNS
                self.column = 0
        elif second == _BS:
            self.column = max(self.column - 1, 0)
            self._memory()[self.row][min(self.column, _COLUMNS - 1)] = None
            self._changed(frame)
        elif second == _DER:
            for column in range(self.column, _COLUMNS):
                self._memory()[self.row][column] = None
            self._changed(frame)


def _lines(text):
    """[(frame, [(first byte, second byte)])] for the lines of an SCC file."""
    lines = []
    for line in text.split('\n'):
        match = _LINE.match(line)
        if match:
            frame = frame_of(match.group(1))
            words = [(int(word[:2], 16), int(word[2:], 16)) for word in _WORD.findall(match.group(7))]
            if words:
                lines.append((frame, words))
    lines.sort(key=lambda line: line[0])
    return lines


def parse(text):
    """The subtitles in SCC text: [{'start', 'end', 'text'}], by start."""
    lines = _lines(text)
    for channel in (1, 2):
        decoder = _Decoder(channel)
        last = 0
        for frame, words in lines:
            for offset, (first, second) in enumerate(words):
                decoder.word(frame + offset, first, second)
            last = frame + len(words)
            decoder.batch_end()
        decoder.finish(last)
        subtitles = [subtitle for subtitle in decoder.subtitles if subtitle.get('body')]
        if subtitles:
            break
    segments = []
    for subtitle in subtitles:
        keypad = subtitle['keypad']
        segments.append({'start': subtitle['start'], 'end': max(subtitle['start'], subtitle['end']),
                         'text': (markup.with_alignment('', keypad) if keypad != 2 else '') + subtitle['body']})
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments


def read(path):
    """The subtitles in the SCC file at `path`."""
    with open(path, 'rb') as scc_file:
        return parse(decode(scc_file.read()))


# Writing.
def _odd(byte):
    return byte | 0x80 if bin(byte).count('1') % 2 == 0 else byte


def _word(first, second):
    return f'{_odd(first):02x}{_odd(second):02x}'


_TO_BASIC = {char: code for code, char in _BASIC.items() if code != 0x7F}
_TO_SPECIAL = {char: code for code, char in _SPECIAL.items() if code != 0x39}
_TO_EXTENDED = {char: (first, code) for first, table in _EXTENDED.items() for code, char in table.items()}
_FALLBACKS = {'‘': "'", '’': "'", '“': '"', '”': '"', '«': '"', '»': '"', '—': '-', '©': 'c', '℠': 's', '•': '.',
              '¡': '!', '*': '.', '{': '(', '}': ')', '\\': '/', '^': "'", '_': '-', '¦': '!', '|': '!', '~': '-',
              '¥': 'Y', '¤': 'o', 'ß': 's', 'Ø': 'O', 'ø': 'o', '┌': '+', '┐': '+', '└': '+', '┘': '+'}
_SUBSTITUTES = {'–': '-', '‑': '-', '…': '...', ' ': ' ', '\t': ' ', '`': "'", '´': "'", '€': 'EUR'}


def _cells(char):
    """What a character shows as and is sent as: [(shown, [unit])], a unit
    ('basic', byte) or ('code', first, second). An extended character goes
    after its fallback, which it replaces."""
    if char in _TO_BASIC:
        return [(char, [('basic', _TO_BASIC[char])])]
    if char in _TO_SPECIAL:
        return [(char, [('code', 0x11, _TO_SPECIAL[char])])]
    if char in _TO_EXTENDED:
        fallback = _FALLBACKS.get(char) or unicodedata.normalize('NFKD', char)[0]
        return [(char, [('basic', _TO_BASIC.get(fallback, 0x20)), ('code', *_TO_EXTENDED[char])])]
    if char in _SUBSTITUTES:
        return [cell for substitute in _SUBSTITUTES[char] for cell in _cells(substitute)]
    plain = ''.join(c for c in unicodedata.normalize('NFKD', char) if not unicodedata.combining(c))
    if plain and plain != char and all(c in _TO_BASIC or c in _TO_SPECIAL or c in _TO_EXTENDED for c in plain):
        return [cell for c in plain for cell in _cells(c)]
    return [('?', [('basic', 0x3F)])]


_PLAIN = (False, False, 0)                         # (italic, underline, colour index)


def _look(style):
    """(italic, underline, colour index) CEA-608 can show for a Style:
    italics are white, a colour the nearest of its seven."""
    if style.italic:
        return True, style.underline, 0
    color = 0
    if style.color:
        rgb = tuple(int(style.color[i:i + 2], 16) for i in (1, 3, 5))
        palette = [(255, 255, 255), (0, 255, 0), (0, 0, 255), (0, 255, 255), (255, 0, 0), (255, 255, 0), (255, 0, 255)]
        color = min(range(len(palette)), key=lambda i: sum((a - b) ** 2 for a, b in zip(rgb, palette[i])))
    return False, style.underline, color


def _wrap(cells):
    """Lines of at most 32 columns from [(shown, look, units)], broken at
    the last space that fits, else at 32."""
    lines = []
    while len(cells) > _COLUMNS:
        spaces = [index for index in range(1, _COLUMNS + 1) if cells[index][0] == ' ']
        cut = spaces[-1] if spaces else _COLUMNS
        lines.append(cells[:cut])
        cells = cells[cut:]
        while cells and cells[0][0] == ' ':
            cells = cells[1:]
    lines.append(cells)
    return [line for line in lines if line]


def _text_lines(text):
    """The lines a subtitle's text is written in, [(shown, look, units)]
    each, and the keypad it sits at."""
    lead, rest = markup.split_lead(text)
    lines, cells = [], []
    for run, style in markup.spans(rest) + [('\n', markup.PLAIN)]:
        look = _look(style)
        for char in run:
            if char == '\n':
                while cells and cells[0][0] == ' ':
                    cells.pop(0)
                while cells and cells[-1][0] == ' ':
                    cells.pop()
                lines.extend(_wrap(cells))
                cells = []
            else:
                cells.extend([shown, look, units] for shown, units in _cells(char))
    return [_punctuated(line) for line in lines[:_ROWS]], markup.alignment(lead)


def _punctuated(line):
    """`line` with punctuation in the style of the word it touches, where
    the style changes with no space between: a code that changes the style
    takes a column, which would put a space before the comma that closes
    an italic word."""
    for index in range(1, len(line)):
        shown, previous = line[index][0], line[index - 1]
        if shown in _CLOSING and previous[0] != ' ' and line[index][1] != previous[1]:
            line[index][1] = previous[1]
    for index in range(len(line) - 2, -1, -1):
        shown, following = line[index][0], line[index + 1]
        if shown in _OPENING and following[0] != ' ' and line[index][1] != following[1] and \
                (index == 0 or line[index - 1][0] == ' '):
            line[index][1] = following[1]
    return [tuple(cell) for cell in line]


def _mid_row(look):
    italic, underline, color = look
    return 0x11, (0x2E if italic else 0x20 + color * 2) + int(underline)


def _row(row, line, column):
    """The tokens, ('b', byte) or ('c', first, second), that write `line`
    at `row` from `column`: a PAC (and a tab offset) there, mid-row codes
    where the style changes, put in place of a space where there is one."""
    first_look = line[0][1]
    pac_first, pac_base = _PAC_ROWS[row]
    tokens = []
    if first_look != _PLAIN and column == 0:
        # At column 0 a PAC gives the row its colour, or italics.
        italic, underline, color = first_look
        tokens.append(('c', pac_first, pac_base + (7 if italic else color) * 2 + int(underline)))
    else:
        # Else the code before the text takes the column before it.
        start = column - 1 if first_look != _PLAIN else column
        tokens.append(('c', pac_first, pac_base + 0x10 + (start // 4) * 2))
        if start % 4:
            tokens.append(('c', 0x17, 0x20 + start % 4))
        if first_look != _PLAIN:
            tokens.append(('c', *_mid_row(first_look)))
    current = first_look
    for shown, look, units in line:
        if shown == ' ':
            if tokens[-1] != ('b', 0x20):
                tokens.append(('b', 0x20))
            continue
        if look != current:
            if tokens[-1] == ('b', 0x20):
                tokens.pop()
            tokens.append(('c', *_mid_row(look)))
            current = look
        tokens.extend(('b', unit[1]) if unit[0] == 'basic' else ('c', unit[1], unit[2]) for unit in units)
    return tokens


def _words(tokens):
    """Words for tokens: characters two to a word, codes sent twice, on a
    word of their own."""
    words, pending = [], []
    for token in tokens + [None]:
        if token is not None and token[0] == 'b':
            pending.append(token[1])
            if len(pending) == 2:
                words.append(_word(*pending))
                pending = []
            continue
        if pending:
            words.append(_word(pending[0], 0x00))
            pending = []
        if token is not None:
            words.extend([_word(token[1], token[2])] * 2)
    return words


def _load(text):
    """The words that load a subtitle as a pop-on caption, before its EOC."""
    lines, keypad = _text_lines(text)
    if not lines:
        return []
    count = len(lines)
    first_row = 1 if keypad >= 7 else max(1, 8 - count // 2) if keypad >= 4 else _ROWS - count + 1
    tokens = [('c', 0x14, _RCL), ('c', 0x14, _ENM)]
    for index, line in enumerate(lines):
        width = len(line)
        column = 0 if keypad % 3 == 1 else _COLUMNS - width if keypad % 3 == 0 else (_COLUMNS - width) // 2
        tokens.extend(_row(first_row + index, line, column))
    return _words(tokens)


def write(segments, drop_frame=True, speaker_changes=False):
    """SCC text for `segments`, as pop-on captions in CC1. With
    `speaker_changes`, ">> " begins each subtitle a new speaker says, as
    captions mark them."""
    eoc, edm = _word(0x14, _EOC), _word(0x14, _EDM)
    captions = []
    speaker = ''
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        text = str(segment.get('text') or '')
        if speaker_changes:
            said_by = str(segment.get('speaker') or '').strip()
            if said_by and said_by != speaker and markup.plain(text).strip():
                lead, rest = markup.split_lead(text)
                text = lead + '>> ' + rest.lstrip()
            speaker = said_by
        words = _load(text)
        if words:
            start = round(float(segment.get('start', 0)) * _RATE)
            captions.append([start, max(start + 1, round(float(segment.get('end', 0)) * _RATE)), words])

    frames, breaks = {}, set()                     # frame: word; the frames a line starts at
    loaded_from = 0                                # the first frame the next load may use
    for index, (start, end, words) in enumerate(captions):
        # Loaded after the last one was shown (until then the memory it
        # loads into holds that one), shown on its frame, or as soon after
        # as there is time to load it.
        shown = max(start, loaded_from + len(words))
        while True:
            slots = [frame for frame in range(loaded_from, shown) if frame not in frames]
            if len(slots) >= len(words) and shown not in frames and shown + 1 not in frames:
                break
            shown += 1
        for frame, word in zip(slots[-len(words):], words):
            frames[frame] = word
        frames[shown] = frames[shown + 1] = eoc
        breaks.add(shown)
        loaded_from = shown + 2
        # Cleared where it ends, unless the next one is shown by then.
        following = captions[index + 1][0] if index + 1 < len(captions) else None
        clear = max(end, shown + 2)
        if following is None or following > clear + 1:
            while clear in frames or clear + 1 in frames:
                clear += 1
            frames[clear] = frames[clear + 1] = edm
            breaks.add(clear)

    lines = ['Scenarist_SCC V1.0']
    group, group_start = [], 0
    for frame in sorted(frames):
        if group and (frame != group_start + len(group) or frame in breaks):
            lines += ['', f'{timecode(group_start, drop_frame)}\t{" ".join(group)}']
            group = []
        if not group:
            group_start = frame
        group.append(frames[frame])
    if group:
        lines += ['', f'{timecode(group_start, drop_frame)}\t{" ".join(group)}']
    return '\n'.join(lines) + '\n'


def save(path, segments, drop_frame=True, speaker_changes=False):
    """Write `segments` to `path` as SCC (ASCII, Windows line ends, as
    Scenarist wrote them)."""
    with open(path, 'w', encoding='ascii', newline='\r\n') as scc_file:
        scc_file.write(write(segments, drop_frame, speaker_changes))
