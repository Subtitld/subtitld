"""ARIB STD-B24 captions: the closed captions of Japan's ISDB broadcasts
(and, with ABNT NBR 15606-1's Latin sets, Brazil's ISDB-Tb), carried in
an MPEG transport stream, read and written.

What is here follows ARIB STD-B24 (Volume 1 Part 2 for the 8-unit code
and its control functions, Volume 3 for how captions are carried; the
English edition) and, where the standard leaves the receiver to decide,
libaribcaption (github.com/xqq/libaribcaption, src/decoder), which FFmpeg
decodes them with, and FFmpeg's mpegts demuxer for how a stream is found.
The tables are libaribcaption's (modules.aribb24_tables).

A caption stream is a PES stream of private data (stream_type 6) that
the PMT names with a stream identifier descriptor (0x52, component tag
0x30 to 0x37 for profile A, the full-segment service) and a data
component descriptor (0xFD, data_component_id 0x0008). Each PES packet
(synchronized, stream_id 0xBD, timed by its PTS) holds a data_identifier
0x80, a private_stream_id 0xFF, its own header length, then a data group:
its id (0x00 or 0x20 caption management, 0x01 to 0x08 or 0x21 to 0x28
the statements of language 1 to 8; group A or B), its size, its data and
a CRC-16.

- Caption management data: the time control mode, then each language's
  tag, display mode, ISO 639-2 code, format (the writing format the
  statements are laid out in: 960x540 or 1920x1080 or 720x480, across or
  down) and character coding (TCS: 0 the 8-unit code, 1 UTF-8);
- caption statement data: its data units, 0x20 the statement body (the
  text and its control codes), 0x30 and 0x31 DRCS patterns (characters
  drawn as pictures, one-byte and two-byte codes).

The statement body is ISO 2022's 8-unit code: four graphic sets G0 to G3
designated by ESC sequences (kanji, alphanumerics, hiragana, katakana,
JIS X 0201 katakana, JIS X 0213, ARIB's additional symbols, the DRCS sets
and the macros), invoked into GL and GR by locking and single shifts;
profile A starts with kanji, alphanumerics, hiragana and the macros. The
kanji set is JIS X 0208 (decoded through Python's euc_jis_2004, with the
three fullwidth signs ARIB keeps fullwidth), its rows 85 to 94 ARIB's
additional kanji and symbols. C0 and C1 control codes move the active
position (APS, APR, APF ...), clear the screen (CS), set the colour (BKF
to WHF, COL with the 128-colour CLUT), the size (SSZ, MSZ, NSZ, SZX),
flashing, underline (STL), and CSI sequences set the writing format
(SWF), the display area (SDF, SDP), the character size and spacing (SSM,
SHS, SVS), a position in dots (ACPS) and the font (MDF: bold, italic).
TIME waits (its tenths of a second end the statement). In UTF-8 the same
controls go between the characters, C1 as U+0080 to U+009F.

Reading gives a subtitle a statement with text: from its PTS until the
next statement of the language, or what its TIME says, or `TAIL` seconds
for the last one; a statement that clears the screen and writes nothing
ends the one before. Characters are laid out as the receiver lays them
(libaribcaption's active position), so a line is the characters of one
row; small characters on the row above characters they stand over are
that text's ruby (furigana), and small ones that stand over nothing are
written small (<font size="50%">). The colour (as the CLUT gives it),
underline, bold, italic and flashing are Subtitld's markup; where the
rows sit on the caption plane gives one of the nine places, and the
vertical writing formats 'vertical-rl'. A DRCS character is the
character libaribcaption knows the pattern for (by its MD5), else U+3013
(geta), as receivers show one they cannot draw. The management data's
language comes back with the subtitles, and a stream carrying two
languages is read by `language` (1 or 2). Times count from the earliest
start of the file's streams, as FFmpeg counts them.

Writing gives a transport stream with a caption stream as profile A has
it: a management data group in each second, then a statement for each
subtitle that clears the screen and writes its lines on the 960x540 plane
(format 7), each line placed with ACPS at the row of its place, centred
or to its side, small characters above a ruby's base, and a statement
that clears the screen where a subtitle ends before the next. Japanese is
written in the 8-unit code (kanji, ARIB's additional symbols and
alphanumerics in middle size), Portuguese and Spanish in ABNT NBR
15606-1's Latin sets, any other language in UTF-8 (TCS 1). A character
none of them has is written as U+3013. Colours are the eight of the
first palette, nearest to the run's.

What is lost on the way: the exact geometry (only the nine places are
kept), sizes other than the ruby's, backgrounds and enclosures, mosaics,
built-in sounds, DRCS patterns libaribcaption does not know, the
vertical writing formats' geometry (read as rows), roll-up mode and the
time control modes other than free (statements are timed by their PTS).
"""

import hashlib
import os
import subprocess
import tempfile
import unicodedata

from subtitld.modules import aribb24_tables, markup, teletext

# The last statement, with nothing after it to end it, lasts this long.
TAIL = 5.0
_PTS_WRAP = 1 << 33
DATA_COMPONENT_A, DATA_COMPONENT_C = 0x0008, 0x0012
COMPONENT_TAGS_A = frozenset(range(0x30, 0x38))
CAPTION_PID = 0x0130

# The writing formats (SWF, the management data's format less one):
# (plane width, height, vertical, character width, height, horizontal and
# vertical spacing), as libaribcaption sets them for profile A.
_FORMATS = {5: (1920, 1080, False, 36, 36, 4, 24), 7: (960, 540, False, 36, 36, 4, 24),
            8: (960, 540, True, 36, 36, 12, 24), 9: (720, 480, False, 36, 36, 4, 16),
            10: (720, 480, True, 36, 36, 8, 24)}
_DEFAULT_FORMAT = 7

# The graphic sets, by the final byte of the ESC sequence designating them.
KANJI, ALPHANUMERIC, HIRAGANA, KATAKANA, MOSAIC, JISX0201, X0213_1, X0213_2, ADDITIONAL, LATIN_EXTENSION, \
    LATIN_SPECIAL, MACRO, DRCS = range(13)
_G_SETS = {0x42: (KANJI, 2), 0x4A: (ALPHANUMERIC, 1), 0x36: (ALPHANUMERIC, 1), 0x30: (HIRAGANA, 1),
           0x37: (HIRAGANA, 1), 0x31: (KATAKANA, 1), 0x38: (KATAKANA, 1), 0x32: (MOSAIC, 1), 0x33: (MOSAIC, 1),
           0x34: (MOSAIC, 1), 0x35: (MOSAIC, 1), 0x49: (JISX0201, 1), 0x39: (X0213_1, 2), 0x3A: (X0213_2, 2),
           0x3B: (ADDITIONAL, 2), 0x4B: (LATIN_EXTENSION, 1), 0x4C: (LATIN_SPECIAL, 1)}

# Hiragana and katakana sets: 0x21 to 0x73 (0x76 for katakana) the kana in
# JIS order, then the signs both share.
_KANA_SIGNS = 'ゝゞー。「」、・'
_HIRAGANA = ''.join(chr(0x3041 + n) for n in range(0x53)) + '\u3000\u3000\u3000' + _KANA_SIGNS
_KATAKANA = ''.join(chr(0x30A1 + n) for n in range(0x56)) + 'ヽヾ' + _KANA_SIGNS[2:]
# ABNT NBR 15606-1's Latin extension and special sets (libaribcaption).
_LATIN_EXTENSION = ('¡¢£€¥Š§š©ª«¬ÿ®¯°±²³Žµ¶·ž¹º»ŒœŸ¿ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþ')
_LATIN_SPECIAL = ('♪' + '!' * 14 + '¤¦¨´¸¼½¾' + '!' * 8 + '…█‘’“”•™⅛⅜⅝⅞' + '!' * 51)
# The kanji set's signs ARIB keeps fullwidth where JIS X 0213 has the narrow ones.
_KANJI_OVERRIDES = {(0, 80): '￠', (0, 81): '￡', (1, 43): '￢'}

# The default macros (0x60 to 0x6F), which designate sets in one code.
_MACROS = (
    b'\x1b\x24\x42\x1b\x29\x4a\x1b\x2a\x30\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x24\x42\x1b\x29\x31\x1b\x2a\x30\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x24\x42\x1b\x29\x20\x41\x1b\x2a\x30\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x28\x32\x1b\x29\x34\x1b\x2a\x35\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x28\x32\x1b\x29\x33\x1b\x2a\x35\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x28\x32\x1b\x29\x20\x41\x1b\x2a\x35\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x28\x20\x41\x1b\x29\x20\x42\x1b\x2a\x20\x43\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x28\x20\x44\x1b\x29\x20\x45\x1b\x2a\x20\x46\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x28\x20\x47\x1b\x29\x20\x48\x1b\x2a\x20\x49\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x28\x20\x4a\x1b\x29\x20\x4b\x1b\x2a\x20\x4c\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x28\x20\x4d\x1b\x29\x20\x4e\x1b\x2a\x20\x4f\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x24\x42\x1b\x29\x20\x42\x1b\x2a\x30\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x24\x42\x1b\x29\x20\x43\x1b\x2a\x30\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x24\x42\x1b\x29\x20\x44\x1b\x2a\x30\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x28\x31\x1b\x29\x30\x1b\x2a\x4a\x1b\x2b\x20\x70\x0f\x1b\x7d',
    b'\x1b\x28\x4a\x1b\x29\x32\x1b\x2a\x20\x41\x1b\x2b\x20\x70\x0f\x1b\x7d',
)

# Encodings of the statements: by TCS and by the languages.
JIS, LATIN, UTF8 = 'jis', 'latin', 'utf-8'
GETA = '〓'


def _kanji(first, second):
    """The character of the kanji set at (first, second), each 0x21 to 0x7E."""
    ku, ten = first - 0x21, second - 0x21
    if ku >= 84:
        char = aribb24_tables.ADDITIONAL[(ku - 84) * 94 + ten]
        return GETA if char == '\ufffd' else char
    if (ku, ten) in _KANJI_OVERRIDES:
        return _KANJI_OVERRIDES[(ku, ten)]
    try:
        return bytes((first | 0x80, second | 0x80)).decode('euc_jis_2004')
    except UnicodeDecodeError:
        return GETA


def _color(index):
    r, g, b, a = aribb24_tables.CLUT[index & 0x7F]
    return f'#{r:02x}{g:02x}{b:02x}', a


class _Char:
    __slots__ = ('char', 'x', 'y', 'width', 'height', 'small', 'color', 'underline', 'bold', 'italic', 'flash')


class _Decoder:
    """The state of a receiver, as libaribcaption keeps it."""

    def __init__(self, encoding=JIS, swf=_DEFAULT_FORMAT, latin=False):
        self.encoding, self.swf = encoding, swf
        self.drcs = [dict() for _ in range(16)]
        self.reset()

    def reset(self):
        """CS: the sets, the format, the place and the look start again."""
        if self.encoding == LATIN:
            self.sets = [(ALPHANUMERIC, 1), (ALPHANUMERIC, 1), (LATIN_EXTENSION, 1), (LATIN_SPECIAL, 1)]
        else:
            self.sets = [(KANJI, 2), (ALPHANUMERIC, 1), (HIRAGANA, 1), (MACRO, 1)]
        self.gl, self.gr = 0, 2
        self.writing_format(self.swf)
        self.area_x = self.area_y = 0
        self.x = self.y = 0
        self.placed = False
        self.wrap = False
        self.scale = (0.5, 1.0) if self.encoding == LATIN else (1.0, 1.0)
        self.palette = 0
        self.color = _color(7)
        self.underline = self.bold = self.italic = self.flash = False

    def writing_format(self, swf):
        self.swf = swf if swf in _FORMATS else _DEFAULT_FORMAT
        width, height, self.vertical, self.char_w, self.char_h, self.space_h, self.space_v = _FORMATS[self.swf]
        if self.encoding == LATIN:
            self.space_h, self.space_v = 2, 16
        self.plane = self.area = (width, height)

    # Geometry, as libaribcaption's active position.
    def section(self):
        return (int((self.char_w + self.space_h) * self.scale[0]), int((self.char_h + self.space_v) * self.scale[1]))

    def set_position(self, column, row):
        width, height = self.section()
        self.placed, self.wrap = True, False
        self.x, self.y = self.area_x + column * width, self.area_y + (row + 1) * height

    def move(self, dx, dy):
        width, height = self.section()
        if not self.placed:
            self.set_position(0, 0)
        self._unwrap()
        right, bottom = self.area_x + self.area[0], self.area_y + self.area[1]
        while dx < 0:
            self.x -= width
            dx += 1
            if self.x < self.area_x:
                self.x, dy = right - width, dy - 1
        while dx > 0:
            self.x += width
            dx -= 1
            if self.x >= right:
                if dx == 0:
                    self.wrap = True
                    break
                self.x, dy = self.area_x, dy + 1
        while dy < 0:
            self.y -= height
            dy += 1
            if self.y < self.area_y:
                self.y = bottom
        while dy > 0:
            self.y += height
            dy -= 1
            if self.y > bottom:
                self.y = self.area_y + height

    def _unwrap(self):
        if self.wrap:
            self.wrap = False
            self.x = self.area_x
            self.move(0, 1)

    def newline(self):
        if not self.placed:
            self.set_position(0, 0)
        self.wrap = False
        self.x = self.area_x
        self.move(0, 1)

    def small(self):
        return abs(self.scale[0] - 0.5) < 0.01 and abs(self.scale[1] - 0.5) < 0.01 or \
            (self.encoding == JIS and self.char_w == 18 and self.char_h == 18)

    def middle(self):
        return abs(self.scale[0] * 2 - self.scale[1]) < 0.01

    def put(self, char, out):
        if not self.placed:
            self.set_position(0, 0)
        self._unwrap()
        width, height = self.section()
        item = _Char()
        item.char, item.x, item.y, item.width, item.height = char, self.x, self.y - height, width, height
        item.small = self.small() and self.encoding == JIS
        item.color = self.color
        item.underline, item.bold, item.italic, item.flash = self.underline, self.bold, self.italic, self.flash
        out.append(item)
        self.move(1, 0)

    # The statement body.
    def body(self, data, out, timing):
        index = 0
        while index < len(data):
            byte = data[index]
            if self.encoding == UTF8:
                if byte <= 0x1F:
                    index = self._c0(data, index, out, timing)
                elif byte == 0x7F:
                    index = self._c1(data, index, out, timing)
                elif byte == 0xC2 and index + 1 < len(data) and 0x80 <= data[index + 1] <= 0x9F:
                    index = self._c1(data, index + 1, out, timing)
                else:
                    index = self._utf8(data, index, out)
            elif byte <= 0x20:
                index = self._c0(data, index, out, timing)
            elif byte < 0x7F:
                index = self._graphic(data, index, self.sets[self.gl], out)
            elif byte <= 0xA0:
                index = self._c1(data, index, out, timing)
            elif byte < 0xFF:
                index = self._graphic(data, index, self.sets[self.gr], out)
            else:
                index += 1

    def _utf8(self, data, index, out):
        length = 1 if data[index] < 0x80 else 2 if data[index] < 0xE0 else 3 if data[index] < 0xF0 else 4
        char = bytes(data[index:index + length]).decode('utf-8', 'replace')
        code = ord(char[0]) if char else 0
        if 0xEC00 <= code <= 0xF8FF:
            char = self.drcs[0].get(code, GETA)
        self.put(char[:1] or GETA, out)
        return index + length

    def _c0(self, data, index, out, timing):
        byte = data[index]
        if byte == 0x08:
            self.move(-1, 0)
        elif byte == 0x09:
            self.move(1, 0)
        elif byte == 0x0A:
            self.move(0, 1)
        elif byte == 0x0B:
            self.move(0, -1)
        elif byte == 0x0C:
            self.reset()
            timing['cleared'] = True
            out.clear()
        elif byte == 0x0D:
            self.newline()
        elif byte == 0x0E:
            self.gl = 1
        elif byte == 0x0F:
            self.gl = 0
        elif byte == 0x16 and index + 1 < len(data):
            self.move(data[index + 1] & 0x3F, 0)
            return index + 2
        elif byte in (0x19, 0x1D) and index + 1 < len(data):
            return self._graphic(data, index + 1, self.sets[2 if byte == 0x19 else 3], out)
        elif byte == 0x1B and index + 1 < len(data):
            return self._escape(data, index + 1)
        elif byte == 0x1C and index + 2 < len(data):
            self.set_position(data[index + 2] & 0x3F, data[index + 1] & 0x3F)
            return index + 3
        elif byte == 0x20:
            self.put(' ' if self.encoding != JIS or self.middle() else '\u3000', out)
        return index + 1

    def _escape(self, data, index):
        first = data[index]
        shifts = {0x6E: ('gl', 2), 0x6F: ('gl', 3), 0x7E: ('gr', 1), 0x7D: ('gr', 2), 0x7C: ('gr', 3)}
        if first in shifts:
            setattr(self, shifts[first][0], shifts[first][1])
            return index + 1
        rest = data[index:index + 4]
        if first == 0x24 and len(rest) >= 2:
            if 0x28 <= rest[1] <= 0x2B and len(rest) >= 3:
                slot = rest[1] - 0x28
                if rest[2] == 0x20 and len(rest) >= 4:
                    self.sets[slot] = (DRCS + (rest[3] - 0x40), 2) if 0x40 <= rest[3] <= 0x4F else self.sets[slot]
                    return index + 4
                self.sets[slot] = _G_SETS.get(rest[2], self.sets[slot])
                return index + 3
            self.sets[0] = _G_SETS.get(rest[1], self.sets[0])
            return index + 2
        if 0x28 <= first <= 0x2B and len(rest) >= 2:
            slot = first - 0x28
            if rest[1] == 0x20 and len(rest) >= 3:
                if rest[2] == 0x70:
                    self.sets[slot] = (MACRO, 1)
                elif 0x40 <= rest[2] <= 0x4F:
                    self.sets[slot] = (DRCS + (rest[2] - 0x40), 1)
                return index + 3
            self.sets[slot] = _G_SETS.get(rest[1], self.sets[slot])
            return index + 2
        return index + 1

    def _graphic(self, data, index, designation, out):
        kind, size = designation
        first = data[index] & 0x7F
        if size == 2:
            if index + 1 >= len(data):
                return len(data)
            second = data[index + 1] & 0x7F
        if kind in (KANJI, X0213_1, ADDITIONAL):
            self.put(_kanji(first, second), out)
        elif kind == X0213_2:
            try:
                self.put(bytes((0x8F, first | 0x80, second | 0x80)).decode('euc_jis_2004'), out)
            except UnicodeDecodeError:
                self.put(GETA, out)
        elif kind == ALPHANUMERIC:
            char = chr(first)
            if self.encoding == JIS:
                char = {'\\': '¥', '~': '‾'}.get(char, char)
                if not self.middle():
                    # Normal size is fullwidth (libaribcaption's tables).
                    char = chr(ord(char) + 0xFEE0) if 0x21 <= ord(char) <= 0x7E else \
                        {'¥': '￥', '‾': '￣'}.get(char, char)
            self.put(char, out)
        elif kind in (HIRAGANA, KATAKANA):
            table = _HIRAGANA if kind == HIRAGANA else _KATAKANA
            self.put(table[first - 0x21] if first - 0x21 < len(table) else GETA, out)
        elif kind == JISX0201:
            char = chr(0xFF61 + first - 0x21) if first <= 0x5F else GETA
            self.put(char if self.middle() else unicodedata.normalize('NFKC', char), out)
        elif kind == LATIN_EXTENSION:
            self.put(_LATIN_EXTENSION[first - 0x21], out)
        elif kind == LATIN_SPECIAL:
            self.put(_LATIN_SPECIAL[first - 0x21], out)
        elif kind == MACRO:
            if 0x60 <= first <= 0x6F:
                self.body(_MACROS[first & 0x0F], out, {})
        elif kind >= DRCS:
            key = (first << 8 | second) if size == 2 else first
            self.put(self.drcs[kind - DRCS].get(key, GETA), out)
        return index + size

    def _c1(self, data, index, out, timing):
        byte = data[index]
        argument = data[index + 1] if index + 1 < len(data) else 0
        if 0x80 <= byte <= 0x87:
            self.color = _color(self.palette * 16 + byte - 0x80)
        elif byte == 0x88:
            self.scale = (0.5, 0.5)
        elif byte == 0x89:
            self.scale = (0.5, 1.0)
        elif byte == 0x8A:
            self.scale = (1.0, 1.0)
        elif byte == 0x8B:
            self.scale = {0x41: (self.scale[0], 2.0), 0x44: (2.0, self.scale[1]), 0x45: (2.0, 2.0)}.get(argument, self.scale)
            return index + 2
        elif byte == 0x90:
            if argument == 0x20:
                self.palette = (data[index + 2] if index + 2 < len(data) else 0) & 0x07
                return index + 3
            if 0x48 <= argument <= 0x4F:
                self.color = _color(self.palette * 16 + (argument & 0x0F))
            return index + 2
        elif byte == 0x91:
            self.flash = argument in (0x40, 0x47)
            return index + 2
        elif byte == 0x92:
            return index + (3 if argument == 0x20 else 2)
        elif byte in (0x93, 0x94, 0x97, 0x98):
            return index + 2
        elif byte == 0x95:
            # MACRO: defining macros is not used (ARIB TR-B14); skip to its end.
            end = data.find(b'\x95\x4f', index + 1)
            return end + 2 if end >= 0 else len(data)
        elif byte == 0x99:
            self.underline = False
        elif byte == 0x9A:
            self.underline = True
        elif byte == 0x9B:
            return self._csi(data, index + 1)
        elif byte == 0x9D:
            if argument == 0x20 and index + 2 < len(data):
                timing['wait'] = timing.get('wait', 0) + (data[index + 2] & 0x3F) / 10.0
            return index + 3
        return index + 1

    def _csi(self, data, index):
        params, current, count = [], 0, 0
        while index < len(data):
            byte = data[index]
            if 0x30 <= byte <= 0x39:
                current = current * 10 + (byte & 0x0F)
            elif byte == 0x3B:
                params.append(current)
                current = 0
            elif byte == 0x20:
                params.append(current)
                break
            index += 1
            count += 1
        index += 1
        if index >= len(data):
            return len(data)
        final = data[index]
        first = params[0] if params else 0
        second = params[1] if len(params) > 1 else 0
        if final == 0x53 and len(params) == 1:
            self.writing_format(first)
        elif final == 0x56:
            self.area = (first, second)
        elif final == 0x57:
            self.char_w, self.char_h = first, second
        elif final == 0x58:
            self.space_h = first
        elif final == 0x59:
            self.space_v = first
        elif final == 0x5F:
            self.area_x = first
            if len(params) >= 2:
                self.area_y = second
            if not self.placed:
                self.set_position(0, 0)
        elif final == 0x61:
            self.placed, self.wrap = True, False
            self.x, self.y = first, second
        elif final == 0x64:
            self.bold, self.italic = first in (1, 3), first in (2, 3)
        return index + 1

    def drcs_unit(self, data, two_bytes):
        """A DRCS data unit: each code's pattern, as the character
        libaribcaption knows it for (by the MD5 of its pixel bytes)."""
        index, count = 1, data[0] if data else 0
        for _ in range(count):
            if index + 3 > len(data):
                return
            code = data[index] << 8 | data[index + 1]
            fonts = data[index + 2]
            index += 3
            for _ in range(fonts):
                if index + 4 > len(data):
                    return
                mode = data[index] & 0x0F
                index += 1
                if mode in (0, 1):
                    depth, width, height = data[index] + 2, data[index + 1], data[index + 2]
                    index += 3
                    bits = (depth - 1).bit_length()
                    size = (width * height * bits + 7) // 8
                    pattern = bytes(data[index:index + size])
                    index += size
                    known = aribb24_tables.DRCS.get(hashlib.md5(pattern).hexdigest())
                    char = chr(known) if known else GETA
                    if two_bytes:
                        key = code if 0xEC00 <= code <= 0xF8FF else code & 0x7F7F
                        self.drcs[0][key] = char
                    else:
                        # The high byte is the set's final byte (0x41 is DRCS-1).
                        self.drcs[(code >> 8) & 0x0F][code & 0x7F] = char
                else:
                    if index + 4 > len(data):
                        return
                    index += 4 + (data[index + 2] << 8 | data[index + 3])


def _text_of(chars, decoder):
    """(text with markup, keypad) for the characters a statement shows."""
    if not chars:
        return '', None
    rows = {}
    for item in chars:
        rows.setdefault(item.y + item.height, []).append(item)
    order = sorted(rows)
    used = set()
    lines = []
    for number, bottom in enumerate(order):
        row = sorted(rows[bottom], key=lambda item: item.x)
        if all(item.small for item in row) and number + 1 < len(order):
            continue                                 # readings, placed with the row below them
        rubies = []
        if number > 0 and all(item.small for item in rows[order[number - 1]]):
            rubies = _runs_of(sorted(rows[order[number - 1]], key=lambda item: item.x))
        line, at = [], 0
        for run in rubies:
            if not any(_over(run, item) for item in row):
                # Small characters over nothing: small text, before the line.
                line += [(item, {'size': '50%'}) for item in run]
                used.add(id(run))
        while at < len(row):
            reading = next((run for run in rubies if id(run) not in used and _over(run, row[at])), None)
            if reading is None:
                line.append((row[at], {}))
                at += 1
                continue
            used.add(id(reading))
            base = [row[at]]
            at += 1
            while at < len(row) and _over(reading, row[at]):
                base.append(row[at])
                at += 1
            line += [(item, {'ruby': 'base'}) for item in base] + [(item, {'ruby': 'text'}) for item in reading]
        while line and line[-1][0].char in '\u3000 ':
            line.pop()
        lines.append(_styled(line))
    text = '\n'.join(line for line in lines if line)
    left = min(item.x for item in chars)
    right = max(item.x + item.width for item in chars)
    top = min(item.y for item in chars)
    bottom = max(item.y + item.height for item in chars)
    width, height = decoder.plane
    centre_y = (top + bottom) / 2 / height
    row = 2 if centre_y < 1 / 3 else 1 if centre_y < 2 / 3 else 0
    # Across: the side whose margin is clearly the narrower, else centred.
    gap = (width - right) - left
    column = 1 if abs(gap) < width * 0.08 else 0 if gap > 0 else 2
    return text, (1, 4, 7)[row] + column


def _runs_of(row):
    """Characters of a row in runs, a run broken where a gap opens."""
    runs = []
    for item in row:
        if runs and item.x <= runs[-1][-1].x + runs[-1][-1].width + 1:
            runs[-1].append(item)
        else:
            runs.append([item])
    return runs


def _over(run, item):
    """Whether a ruby run stands over the character `item`."""
    return run[0].x <= item.x + item.width / 2 <= run[-1].x + run[-1].width


def _styled(items):
    """Text with markup for [(character, what it is besides its look)]."""
    runs = []
    for item, extra in items:
        style = markup.Style(italic=item.italic, bold=item.bold, underline=item.underline, flash=item.flash,
                             color='' if item.color[0] == '#ffffff' else item.color[0], **extra)
        if runs and runs[-1][1] == style:
            runs[-1] = (runs[-1][0] + item.char, style)
        else:
            runs.append((item.char, style))
    return markup.from_spans(runs)


# --- Data groups -----------------------------------------------------------------

def data_group(payload):
    """(data_group_id, data) of a PES packet's caption data; None when it
    holds none."""
    if len(payload) < 3 or payload[0] not in (0x80, 0x81) or payload[1] != 0xFF:
        return None
    begin = 3 + (payload[2] & 0x0F)
    if begin + 5 > len(payload):
        return None
    size = payload[begin + 3] << 8 | payload[begin + 4]
    if begin + 5 + size > len(payload):
        return None
    return payload[begin] >> 2, bytes(payload[begin + 5:begin + 5 + size])


def management(data):
    """The languages of caption management data: [{'number' (1, 2),
    'language' (ISO 639-2), 'format' (SWF), 'tcs'}], and its data units."""
    if len(data) < 2:
        return [], b''
    offset = 1 + (5 if data[0] >> 6 == 0b10 else 0)
    languages = []
    count = data[offset] if offset < len(data) else 0
    offset += 1
    for _ in range(count):
        if offset + 5 > len(data):
            break
        tag, mode = data[offset] >> 5, data[offset] & 0x0F
        offset += 1 + (1 if mode in (0b1100, 0b1101, 0b1110) else 0)
        code = bytes(data[offset:offset + 3]).decode('latin-1', 'replace')
        info = data[offset + 3]
        offset += 4
        languages.append({'number': tag + 1, 'language': code, 'format': (info >> 4) - 1, 'tcs': (info >> 2) & 0x03})
    length = int.from_bytes(data[offset:offset + 3], 'big') if offset + 3 <= len(data) else 0
    return languages, bytes(data[offset + 3:offset + 3 + length])


def statement(data):
    """The data units of caption statement data."""
    if not data:
        return b''
    offset = 1 + (5 if data[0] >> 6 in (0b01, 0b10) else 0)
    length = int.from_bytes(data[offset:offset + 3], 'big') if offset + 3 <= len(data) else 0
    return bytes(data[offset + 3:offset + 3 + length])


def data_units(data):
    """(parameter, data) for each data unit."""
    out, index = [], 0
    while index + 5 <= len(data) and data[index] == 0x1F:
        size = int.from_bytes(data[index + 2:index + 5], 'big')
        out.append((data[index + 1], data[index + 5:index + 5 + size]))
        index += 5 + size
    return out


def encoding_for(languages, number=1):
    """The statements' encoding as libaribcaption detects it: UTF-8 for a
    TCS of 1, Latin (ABNT NBR 15606-1) for Portuguese and Spanish, the
    8-unit code with kanji for the rest."""
    if any(entry['tcs'] == 1 for entry in languages if entry['number'] == number):
        return UTF8
    codes = {entry['language'] for entry in languages}
    if 'jpn' in codes:
        return JIS
    if codes & {'por', 'spa'}:
        return LATIN
    if codes & {'eng', 'tgl'}:
        return UTF8
    return JIS


def decode(packets, language=1, origin=None):
    """(subtitles, ISO 639-2 language or '') of language `language` (1 or
    2) in `packets`, [(pts in 90 kHz ticks, PES packet data)], timed in
    seconds from `origin` (a PTS; the first packet's when None)."""
    decoder = _Decoder()
    languages, group = [], None
    base, previous, wraps = None, None, 0
    shown = []                                      # [start, end or None, text, keypad, vertical]
    for pts, payload in packets:
        if previous is not None and pts < previous - _PTS_WRAP // 2:
            wraps += 1
        previous = pts
        if base is None:
            base = origin if origin is not None else pts
        seconds = round((pts + wraps * _PTS_WRAP - base) / 90000.0, 3)
        found = data_group(payload)
        if found is None:
            continue
        group_id, data = found
        number = group_id & 0x0F
        if number == 0:
            if (group_id >> 5) == group:
                continue                             # sent again (ARIB TR-B14 4.2.4)
            listed, _units = management(data)
            if listed:
                group, languages = group_id >> 5, listed
                chosen = next((entry for entry in languages if entry['number'] == language), languages[0])
                encoding = encoding_for(languages, language)
                if encoding != decoder.encoding or chosen['format'] != decoder.swf:
                    drcs = decoder.drcs
                    decoder = _Decoder(encoding, chosen['format'])
                    decoder.drcs = drcs
            continue
        if number != language:
            continue
        chars, timing = [], {}
        for parameter, unit in data_units(statement(data)):
            if parameter == 0x20:
                decoder.body(unit, chars, timing)
            elif parameter in (0x30, 0x31):
                decoder.drcs_unit(unit, parameter == 0x31)
        text, keypad = _text_of(chars, decoder)
        if not text and not timing.get('cleared'):
            continue
        if shown and shown[-1][1] is None:
            shown[-1][1] = seconds
        if text:
            end = round(seconds + timing['wait'], 3) if timing.get('wait') else None
            shown.append([seconds, end, text, keypad, decoder.vertical])
    segments = []
    for start, end, text, keypad, vertical in shown:
        end = end if end is not None else start + TAIL
        segment = {'start': start, 'end': round(max(start, end), 3),
                   'text': markup.with_alignment('', keypad) + text}
        if vertical:
            segment['writing'] = 'vertical-rl'
        segments.append(segment)
    chosen = next((entry for entry in languages if entry['number'] == language), languages[0] if languages else None)
    return segments, (chosen['language'] if chosen else '')


# --- Transport stream ------------------------------------------------------------

def _pmt_streams(section):
    """{pid: {'component_tag', 'data_component_id'} or None} for the
    elementary streams of a PMT section: the caption streams as FFmpeg's
    demuxer finds them (stream_type 6, a stream identifier, a data
    component descriptor of 0x0008 for profile A)."""
    streams = {}
    if len(section) < 12 or section[0] != 0x02:
        return streams
    end = 3 + (((section[1] & 0x0F) << 8) | section[2]) - 4
    position = 12 + (((section[10] & 0x0F) << 8) | section[11])
    while position + 5 <= end:
        kind = section[position]
        pid = ((section[position + 1] & 0x1F) << 8) | section[position + 2]
        length = ((section[position + 3] & 0x0F) << 8) | section[position + 4]
        info = section[position + 5:position + 5 + length]
        tag = component = None
        index = 0
        while index + 2 <= len(info):
            code, size = info[index], info[index + 1]
            body = info[index + 2:index + 2 + size]
            if code == 0x52 and size >= 1:
                tag = body[0]
            elif code == 0xFD and size >= 2:
                component = body[0] << 8 | body[1]
            index += 2 + size
        captions = kind == 0x06 and component == DATA_COMPONENT_A and tag in COMPONENT_TAGS_A
        streams[pid] = {'component_tag': tag, 'data_component_id': component} if captions else None
        position += 5 + length
    return streams


def streams(path, limit=8 * 1024 * 1024):
    """The caption streams of the transport stream at `path`, from the PMT
    in its first `limit` bytes: {pid: {'component_tag', 'data_component_id'}}."""
    from subtitld.modules import dvbsub
    found = {}
    with open(path, 'rb') as stream:
        layout = dvbsub._layout(stream)
        if layout is None:
            return {}
        for pid, listed, _payload in _pmt_sections(stream, layout, limit):
            if pid is None:
                found.update({key: value for key, value in listed.items() if value is not None})
                if found:
                    break
    return found


def _pmt_sections(stream, layout, limit=None, progress=None, stopped=None):
    """(pid, start, payload) of each packet, and each complete PMT's
    caption streams in between as (None, streams, None), as dvbsub walks
    a transport stream."""
    pmt_pids, sections, read = set(), {}, 0
    for pid, start, payload in teletext._packets(stream, *layout, progress=progress, stopped=stopped):
        read += teletext.PACKET
        if limit is not None and read > limit:
            return
        if pid == 0 or pid in pmt_pids:
            if start:
                sections[pid] = bytearray(teletext._section_parts(payload, True))
            elif pid in sections:
                sections[pid] += payload
            section = sections.get(pid)
            if section and len(section) >= 3 and len(section) >= 3 + (((section[1] & 0x0F) << 8) | section[2]):
                if pid == 0 and section[0] == 0x00:
                    end = 3 + (((section[1] & 0x0F) << 8) | section[2]) - 4
                    for index in range(8, end - 3, 4):
                        if (section[index] << 8) | section[index + 1]:
                            pmt_pids.add(((section[index + 2] & 0x1F) << 8) | section[index + 3])
                elif section[0] == 0x02:
                    yield None, _pmt_streams(bytes(section)), None
                del sections[pid]
            continue
        yield pid, start, payload


def scan(path, progress=None, stopped=None):
    """({pid: info}, {pid: [(pts, PES packet data)]}, origin): the caption
    streams of the transport stream at `path`, their PES packets, and the
    earliest PTS of any of its streams, where FFmpeg counts times from."""
    listed, pending, packets, first = {}, {}, {}, {}

    def take(pid, pes):
        pts = teletext._pes_pts(pes)
        if pts is None:
            return
        first.setdefault(pid, pts)
        packets.setdefault(pid, []).append((pts, teletext._pes_payload(pes)))

    from subtitld.modules import dvbsub
    with open(path, 'rb') as stream:
        layout = dvbsub._layout(stream)
        if layout is None:
            return {}, {}, None
        for pid, start, payload in _pmt_sections(stream, layout, progress=progress, stopped=stopped):
            if pid is None:
                for key, info in start.items():
                    listed.setdefault(key, info)
                continue
            if pid not in listed:
                continue
            if listed[pid] is None:
                if start and pid not in first:
                    pts = teletext._pes_pts(payload)
                    if pts is not None:
                        first[pid] = pts
                continue
            if start:
                if pending.get(pid):
                    take(pid, bytes(pending[pid]))
                pending[pid] = bytearray(payload)
            elif pid in pending:
                pending[pid] += payload
    for pid, pes in pending.items():
        if pes:
            take(pid, bytes(pes))
    origin = min(first.values()) if first else None
    return {pid: info for pid, info in listed.items() if info is not None}, packets, origin


def read(path, pid=None, language=1, progress=None, stopped=None):
    """(subtitles, {'language', 'pid'}) of a caption stream of the transport
    stream at `path`: the stream `pid` (the first when None), its language
    `language` (1 or 2). Raises ValueError for a file with no captions."""
    listed, packets, origin = scan(path, progress=progress, stopped=stopped)
    if pid is None or not packets.get(pid):
        pid = next((found for found in list(listed) + list(packets) if packets.get(found)), None)
    if pid is None:
        raise ValueError('There are no ARIB captions in this file')
    segments, code = decode(packets[pid], int(language or 1), origin)
    return segments, {'language': code, 'pid': pid}


# --- Writing ---------------------------------------------------------------------

def crc16(data):
    """CRC-16 of a data group (ITU-T, polynomial 1021h, from 0)."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
        crc &= 0xFFFF
    return crc


def _data_group(group_id, data):
    head = bytes((group_id << 2, 0, 0)) + len(data).to_bytes(2, 'big') + data
    return head + crc16(head).to_bytes(2, 'big')


def _unit(parameter, data):
    return b'\x1f' + bytes((parameter,)) + len(data).to_bytes(3, 'big') + data


def management_data(language='jpn', encoding=JIS, swf=_DEFAULT_FORMAT):
    """A caption management data group: free time control, one language
    shown as it is received, its format and coding."""
    tcs = 1 if encoding == UTF8 else 0
    body = bytes((0x3F, 1, 0x1F)) + language.encode('latin-1', 'replace')[:3].ljust(3, b' ') + \
        bytes((((swf + 1) << 4) | (tcs << 2),)) + (0).to_bytes(3, 'big')
    return _data_group(0x00, body)


def statement_data(body, number=1):
    units = _unit(0x20, body) if body else b''
    return _data_group(number, bytes((0x3F,)) + len(units).to_bytes(3, 'big') + units)


def _csi(*params, final):
    return b'\x9b' + b';'.join(str(int(param)).encode('ascii') for param in params) + b'\x20' + bytes((final,))


_PALETTE_RGB = [aribb24_tables.CLUT[n][:3] for n in range(8)]


def _nearest(color):
    value = markup.color_hex(color) or '#ffffff'
    rgb = tuple(int(value[at:at + 2], 16) for at in (1, 3, 5))
    return min(range(8), key=lambda n: sum((a - b) ** 2 for a, b in zip(_PALETTE_RGB[n], rgb)))


class _Encoder:
    """Characters into the 8-unit code (or UTF-8), with the shifts and
    sizes they need; `width(text)` counts the dots a run takes."""

    def __init__(self, encoding):
        self.encoding = encoding
        self.gl = 0
        self.size = None

    def reset(self):
        self.gl, self.size = 0, None

    def _code(self, char):
        """(kind, bytes): 'wide' 2-byte kanji, 'narrow' alphanumerics."""
        if self.encoding == UTF8:
            return 'wide', char.encode('utf-8')
        if self.encoding == LATIN:
            if 0x21 <= ord(char) <= 0x7E:
                return 'g0', bytes((ord(char),))
            if char in _LATIN_EXTENSION:
                return 'g2', bytes((0xA1 + _LATIN_EXTENSION.index(char),))
            if char in _LATIN_SPECIAL and char != '!':
                return 'g3', bytes((0x21 + _LATIN_SPECIAL.index(char),))
            folded = unicodedata.normalize('NFKD', char).encode('ascii', 'ignore')
            return ('g0', folded[:1]) if folded else ('g0', b'?')
        if 0x21 <= ord(char) <= 0x7E and char not in '~\\':
            return 'narrow', bytes((ord(char),))
        # The alphanumeric set has a yen sign and an overline there.
        char = {'~': '\uff5e', '\\': '\uff3c'}.get(char, char)
        try:
            data = char.encode('euc_jis_2004')
        except UnicodeEncodeError:
            data = b''
        if len(data) == 2 and data[0] >= 0xA1:
            return 'wide', bytes((data[0] & 0x7F, data[1] & 0x7F))
        if char in aribb24_tables.ADDITIONAL:
            index = aribb24_tables.ADDITIONAL.index(char)
            return 'wide', bytes((0x21 + 84 + index // 94, 0x21 + index % 94))
        folded = unicodedata.normalize('NFKC', char)
        if folded != char and len(folded) == 1:
            return self._code(folded)
        return 'wide', bytes((0x22, 0x2E))          # 〓

    def text(self, text, small=False):
        """The bytes for `text`, in `small` (SSZ) or normal size, ASCII in
        middle size."""
        out = bytearray()
        for char in text:
            if self.encoding == UTF8:
                if char == ' ':
                    out += b'\x20'
                else:
                    out += char.encode('utf-8')
                continue
            if char == ' ':
                if self.encoding == JIS:
                    out += self._sized('middle' if not small else 'small') + b'\x20'
                else:
                    out += b'\x20'
                continue
            kind, data = self._code(char)
            if self.encoding == LATIN:
                if kind == 'g0':
                    out += self._shift(0) + data
                elif kind == 'g2':
                    out += data                      # GR holds G2
                else:
                    out += b'\x1d' + data            # SS3
                continue
            if kind == 'narrow':
                out += self._sized('small' if small else 'middle') + self._shift(1) + data
            else:
                out += self._sized('small' if small else 'normal') + self._shift(0) + data
        return bytes(out)

    def _shift(self, slot):
        if self.gl == slot:
            return b''
        self.gl = slot
        return b'\x0e' if slot == 1 else b'\x0f'

    def _sized(self, size):
        if self.size == size:
            return b''
        self.size = size
        return {'small': b'\x88', 'middle': b'\x89', 'normal': b'\x8a'}[size]

    def width(self, text, small=False):
        """Dots a run takes across, at 36x36 characters spaced 4 (the
        Latin sets' spacing 2, in middle size, as libaribcaption has it)."""
        if self.encoding == LATIN:
            return 19 * len(text)
        total = 0
        for char in text:
            half = self.encoding == UTF8 or char == ' ' or (0x21 <= ord(char) <= 0x7E and char not in '~\\')
            total += 20 if half or small else 40
        return total


def statement_body(segment, encoding=JIS):
    """The statement body for a subtitle: the screen cleared, each line
    placed with ACPS on the 960x540 plane where its place puts it, a
    ruby's reading in small size above its base, colours, bold, italic,
    underline and flashing."""
    width, height = _FORMATS[_DEFAULT_FORMAT][:2]
    encoder = _Encoder(encoding)
    text = str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
    keypad = markup.alignment(text)
    lines = [[]]
    for run, style in markup.spans(text):
        for index, piece in enumerate(run.split('\n')):
            if index:
                lines.append([])
            if piece:
                lines[-1].append((piece, style))
    lines = [line for line in lines if ''.join(piece for piece, _style in line).strip()]
    row_height, ruby_height = 60, 30
    heights = [row_height + (ruby_height if any(style.ruby == 'text' for _piece, style in line) else 0) for line in lines]
    total = sum(heights)
    row, column = (keypad - 1) // 3, (keypad - 1) % 3
    top = {0: height - 45 - total, 1: (height - total) // 2, 2: 45}[row]
    # In UTF-8 a C1 code is its UTF-8 form, U+0080 to U+009F.
    control = _utf8_controls if encoding == UTF8 else bytes
    out = bytearray(control(b'\x0c' + _csi(_DEFAULT_FORMAT, final=0x53) + _csi(width, height, final=0x56)
                            + _csi(0, 0, final=0x5F) + _csi(36, 36, final=0x57) + _csi(4, final=0x58)
                            + _csi(24, final=0x59) + (b'\x89' if encoding == UTF8 else b'')))
    encoder.reset()
    look = {'color': 7, 'bold': False, 'italic': False, 'underline': False, 'flash': False}
    for line, line_height in zip(lines, heights):
        groups = markup.ruby_groups(line)
        if encoding != JIS:
            # Only the 8-unit code's small size makes a ruby: elsewhere the
            # reading follows its base in brackets.
            groups = [('plain', group[1] + ([('(', markup.PLAIN)] + group[2] + [(')', markup.PLAIN)]
                                            if group[0] != 'plain' else [])) for group in groups]
        across = sum(encoder.width(''.join(p for p, _s in group[1])) for group in groups)
        left = {0: 48, 1: (width - across) // 2, 2: width - 48 - across}[column]
        bottom = top + line_height
        x = left
        for group in groups:
            base_text = ''.join(piece for piece, _style in group[1])
            base_width = encoder.width(base_text)
            if group[0] != 'plain':
                reading = ''.join(piece for piece, _style in group[2])
                reading_width = encoder.width(reading, small=True)
                out += control(_csi(max(0, x + (base_width - reading_width) // 2), bottom - row_height, final=0x61))
                out += control(_look_codes(look, group[2][0][1] if group[2] else markup.PLAIN))
                out += encoder.text(reading, small=True)
            out += control(_csi(x, bottom, final=0x61))
            for piece, style in group[1]:
                out += control(_look_codes(look, style))
                out += encoder.text(piece)
            x += base_width
        top = bottom
    return bytes(out)


def _utf8_controls(data):
    """Control codes as UTF-8 writes them: a C1 code (0x80 to 0x9F) as
    U+0080 to U+009F."""
    return b''.join(b'\xc2' + bytes((byte,)) if 0x80 <= byte <= 0x9F else bytes((byte,)) for byte in data)


def _look_codes(look, style):
    """The codes that change the look from `look` (updated) to `style`'s."""
    out = bytearray()
    color = _nearest(style.color) if style.color else 7
    if color != look['color']:
        out.append(0x80 + color)
        look['color'] = color
    if (style.bold, style.italic) != (look['bold'], look['italic']):
        out += _csi(1 * style.bold + 2 * style.italic, final=0x64)
        look['bold'], look['italic'] = style.bold, style.italic
    if style.underline != look['underline']:
        out.append(0x9A if style.underline else 0x99)
        look['underline'] = style.underline
    if style.flash != look['flash']:
        out += b'\x91' + (b'\x40' if style.flash else b'\x4f')
        look['flash'] = style.flash
    return bytes(out)


def encoding_of(language):
    """The coding a language's captions are written in: the 8-unit code
    for Japanese, ABNT NBR 15606-1's Latin sets for Portuguese and Spanish,
    UTF-8 (TCS 1) for the rest."""
    primary = str(language or '').lower().replace('_', '-').split('-')[0]
    if primary in ('ja', 'jpn', ''):
        return JIS
    if primary in ('pt', 'por', 'es', 'spa'):
        return LATIN
    return UTF8


def _pes(payload, pts):
    timestamp = bytes((0x21 | ((pts >> 29) & 0x0E), (pts >> 22) & 0xFF, 0x01 | ((pts >> 14) & 0xFE),
                       (pts >> 7) & 0xFF, 0x01 | ((pts << 1) & 0xFE)))
    body = b'\x80\xff\xf0' + payload
    header = b'\x84\x80\x05' + timestamp
    return b'\x00\x00\x01\xbd' + (len(header) + len(body)).to_bytes(2, 'big') + header + body


def transport_stream(segments, language='jpn', subtitld_language='ja', start=0.0):
    """The bytes of a transport stream with one caption stream (profile A,
    component tag 0x30) holding `segments`: the management data each
    second, a statement when a subtitle starts and one clearing the screen
    when it ends before the next starts. Times are seconds from `start`."""
    encoding = encoding_of(subtitld_language or language)
    counters = {}
    descriptors = bytes((0x52, 1, 0x30, 0xFD, 3)) + DATA_COMPONENT_A.to_bytes(2, 'big') + b'\x3d'
    stream_info = bytes((0x06, 0xE0 | (CAPTION_PID >> 8), CAPTION_PID & 0xFF, 0xF0, len(descriptors))) + descriptors
    pat = b'\x00' + teletext._section(0x00, 1, bytes((0, 1, 0xE0 | (teletext.PMT_PID >> 8), teletext.PMT_PID & 0xFF)))
    pmt = b'\x00' + teletext._section(0x02, 1, bytes((0xE0 | (teletext.PCR_PID >> 8), teletext.PCR_PID & 0xFF,
                                                      0xF0, 0x00)) + stream_info)
    timeline = sorted((s for s in segments if markup.plain(str(s.get('text') or '')).strip()),
                      key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0))))
    events = []
    for index, segment in enumerate(timeline):
        begin, end = float(segment.get('start', 0)), float(segment.get('end', 0))
        events.append((begin, 1, statement_body(segment, encoding)))
        following = float(timeline[index + 1]['start']) if index + 1 < len(timeline) else None
        if following is None or following - end > 0.02:
            events.append((end, 0, b'\x0c'))
    last = max([when for when, _order, _body in events] + [0.0])
    seconds = 0.0
    while seconds <= last:
        events.append((seconds, -1, None))
        seconds += 1.0
    events.sort(key=lambda event: (round(event[0], 3), event[1]))
    management = management_data(language, encoding)
    out = []
    for when, _order, body in events:
        pts = int(round((max(0.0, when) + start) * 90000)) % _PTS_WRAP
        out += teletext._ts_packets(0, pat, counters) + teletext._ts_packets(teletext.PMT_PID, pmt, counters)
        out.append(teletext._pcr_packet(pts))
        group = management if body is None else statement_data(body)
        out += teletext._ts_packets(CAPTION_PID, _pes(group, pts), counters)
    return b''.join(out)


def mux_command(video, captions, output, ffmpeg='ffmpeg'):
    """The FFmpeg command that writes `video` out as the transport stream
    `output`, its picture and sound copied, with the caption stream of the
    transport stream `captions`."""
    return [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y', '-nostdin', '-i', str(video),
            '-i', str(captions), '-map', '0:v?', '-map', '0:a?', '-map', '1:s',
            '-c', 'copy', '-f', 'mpegts', str(output)]


def write_video(video, output, tracks, ffmpeg='ffmpeg', startupinfo=None):
    """Write `video` out as the transport stream `output` with ARIB
    captions of the first of `tracks` ([{'segments', 'language'
    (Subtitld's code)}]). Raises ValueError for no tracks and RuntimeError
    with what FFmpeg said when it cannot write."""
    from subtitld.modules import usf
    if not tracks:
        raise ValueError('There are no subtitles to write into the video')
    track = tracks[0]
    language = usf.iso639_2(track.get('language')) if track.get('language') else 'jpn'
    handle, path = tempfile.mkstemp(prefix='subtitld_arib_', suffix='.ts')
    os.close(handle)
    try:
        with open(path, 'wb') as stream:
            stream.write(transport_stream(track.get('segments') or [], language, track.get('language') or 'ja',
                                          start=1.4))
        run = subprocess.run(mux_command(video, path, output, ffmpeg), capture_output=True, startupinfo=startupinfo,
                             check=False)
        if run.returncode != 0 or not os.path.isfile(output) or not os.path.getsize(output):
            said = (run.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
            raise RuntimeError(said[0] if said else 'FFmpeg could not write the video')
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return output

