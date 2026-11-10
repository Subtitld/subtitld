"""Cavena 890 (.890): the binary subtitle files of Cavena's systems, which
the Nordic broadcasters exchange.

Cavena never published the format. What is here is reverse-engineered —
from Subtitle Edit's reader and writer (libse, Cavena890.cs, GPL-3), which
were worked out from broadcasters' files — and is marked below as known
(files read and written agree on it) or as a guess.

A file is a 388-byte header, then a 128-byte block a subtitle (the last
may leave off its four closing zeros). The header, as far as it is known:

     40  the translated programme title (28 bytes, ASCII)
     68  the translator (28)
    105  the translated episode title (11)
    146  the language of the first line, and of the second (a byte each)
    148  a comment (24)
    179  the number of subtitles (two bytes, low first)
    181  the font, at the right of 14 bytes ("HLV23N.V" ...)
    218  the original programme title (28)
    246  the font again (8)
    256  the start of the programme, "HH:MM:SS:FF"
    293  "BTD"

and the rest constants Subtitle Edit writes as it found them. A block:

      0  its number (two bytes, high first; 16, 32, 48 ...); blocks that
         share a number are one subtitle, its lines in order
      2  the start, then the end, as frame counts (three bytes each, high
         first)
      8  the alignment: 50 left, 52 right, 54 centre
     16  the first line (51 bytes)
     70  A0 for a box behind the text
     73  the second line (51 bytes)

A one-line subtitle is in the second line. Unused bytes of a line are 7F.

The language bytes choose the character table, and so do the fonts (known
from files; the language codes are EBU STL's for the Latin languages):

- Latin (English 09, Danish 07, Swedish 28, Norwegian 1E, Romanian 22 and
  the rest): Windows-1252, with æ ø å at 1B 1C 1D, Æ Ø Å at 5B 5C 5D,
  ł đ at 7C 7D, Đ at 02 and ♪ at EB; an accent as a byte before its
  letter (81 grave, 82 acute, 83 circumflex, 84 caron, 85 tilde,
  86 diaeresis, 87 cedilla, 89 breve, 8C ring, 8E dot, 8F ogonek); "@"
  also at E2, "-" at BE, and outside Danish, Swedish and Norwegian, "[" and
  "]" at E5 and E6. "[", "\\", "]", "|" and "}" have no place of their
  own, as their bytes are letters.
- Russian (56, font KYRIL4): letters on the ASCII bytes, Latin letters
  that look like Cyrillic ones written as them.
- Greek (70, GREEK4), Arabic (80, ARA19N), Hebrew (8F, HEBNOA): letters on
  the ASCII bytes, Latin letters moved above 90 for Greek and Hebrew.
  Hebrew is kept as it is drawn, from the right: a run of Latin letters or
  digits in it is stored back to front.
- Chinese (90 traditional, 91 simplified, CCKM44): UTF-16 (big-endian),
  25 characters a line.

In the text, 88 and 98 turn italics on and off, F1 to F6 and F8 colour the
text (red, green, yellow, blue, magenta, cyan, orange) and F7 makes it
white again.

Guesses: the frame rate, which the header does not say — 25 (PAL, as
Nordic television is), or the video's, or the one asked; the colours'
exact shades; the Russian capitals Subtitle Edit's table lacks (Ж Й Л Х
Ш Щ Ь Ю), placed 0x20 below their small letters as the others are, and
the Greek table's Latin Q to U (Subtitle Edit has V to Z twice there),
put after P as the rest of its alphabet runs; Hebrew's order, which
Subtitle Edit gets to by other turns; any letter with an accent beyond Subtitle Edit's list, written with the
accent byte its mark has; times counted from the programme start in the
header only when every subtitle comes after it; more than two lines as
further blocks under the same number, and a line longer than 51 bytes
broken at a space into more lines; italics written in Latin, Hebrew and
Chinese only, as 88 and 98 are letters in Greek; a code taking no cell, so
that one ending a style goes before the space and one starting it after.
Vertical placement is not known, so a subtitle at the top is written at
the bottom; bold and underline have no code known.

Writing gives a header with the title, the language of the text (its
table chosen by the language, else by the letters the text holds), the
fonts and the number of subtitles; the programme start 10:00:00:00, as
Subtitle Edit always writes it, with the times counted from 0 or from it,
as asked; a block a subtitle, more for one of more than two lines;
italics, colours, the box and the alignment.
"""

import re
import unicodedata

from subtitld.modules import ebustl
from subtitld.modules import markup

HEADER_SIZE, BLOCK_SIZE, LINE_SIZE = 388, 128, 51
DEFAULT_FPS = 25.0
STARTS = ('00:00:00:00', '10:00:00:00')

ENGLISH, DANISH, SWEDISH, NORWEGIAN, ROMANIAN = 0x09, 0x07, 0x28, 0x1E, 0x22
RUSSIAN, GREEK, ARABIC, HEBREW = 0x56, 0x70, 0x80, 0x8F
CHINESE_TRADITIONAL, CHINESE_SIMPLIFIED = 0x90, 0x91
_SCANDINAVIAN = (DANISH, SWEDISH, NORWEGIAN)
_FONTS = {CHINESE_TRADITIONAL: 'CCKM44.V', CHINESE_SIMPLIFIED: 'CCKM44.V', ARABIC: 'ARA19N.V', RUSSIAN: 'KYRIL4.V',
          HEBREW: 'HEBNOA.V', DANISH: 'VFONTL.V', GREEK: 'GREEK4.V'}
_DEFAULT_FONT = 'HLV23N.V'

_FILLER = 0x7F
_ITALIC_ON, _ITALIC_OFF = 0x88, 0x98
_BOX = 0xA0
_ALIGN = {1: 0x50, 2: 0x54, 0: 0x52}            # keypad % 3: left, centre, right
_COLORS = {0xF1: '#ff0000', 0xF2: '#00ff00', 0xF3: '#ffff00', 0xF4: '#0000ff', 0xF5: '#ff00ff', 0xF6: '#00ffff',
           0xF8: '#ff8000'}
_WHITE = 0xF7

# Latin: the bytes that are not Windows-1252's, and the accents.
_LATIN = {0x02: 'Đ', 0x1B: 'æ', 0x1C: 'ø', 0x1D: 'å', 0x1E: 'Æ', 0x1F: 'Ø', 0x5B: 'Æ', 0x5C: 'Ø', 0x5D: 'Å',
          0x7C: 'ł', 0x7D: 'đ', 0xEB: '♪', 0xE2: '@', 0xBE: '-'}
_BRACKETS = {0xE5: '[', 0xE6: ']'}
_ACCENTS = {0x81: '̀', 0x82: '́', 0x83: '̂', 0x84: '̌', 0x85: '̃', 0x86: '̈',
            0x87: '̧', 0x89: '̆', 0x8C: '̊', 0x8E: '̇', 0x8F: '̨'}
_TO_LATIN = {'æ': 0x1B, 'ø': 0x1C, 'å': 0x1D, 'Æ': 0x5B, 'Ø': 0x5C, 'Å': 0x5D, 'ł': 0x7C, 'đ': 0x7D, 'Đ': 0x02,
             '♪': 0xEB}
_TO_ACCENT = {mark: byte for byte, mark in _ACCENTS.items()}
# Bytes a Windows-1252 character cannot be written at: accents, codes, and
# the bytes read as something else.
_LATIN_TAKEN = set(range(0x80, 0xA0)) | {0xBE, 0xE2, 0xE5, 0xE6, 0xEB} | set(range(0xF1, 0xF9))
_SPELLED = {'…': '...', '‚': ',', '„': '"', '“': '"', '”': '"', '‘': "'", '’': "'", '–': '-', '—': '-',
            'ˆ': '^', '˜': '~', 'Œ': 'OE', 'œ': 'oe', 'ƒ': 'f', '‰': '%', '€': 'EUR', '•': '*', '™': 'TM',
            '÷': '/', 'ß': 'ss', 'Ł': 'L'}

# Russian, as Subtitle Edit has it; the capitals after the second line are
# guesses, 0x20 below their small letters.
_RUSSIAN = {
    0x42: 'Б', 0x45: 'Е', 0x5A: 'З', 0x56: 'В', 0x49: 'И', 0x4E: 'Н', 0x58: 'Ы', 0x51: 'Я', 0x53: 'С', 0x72: 'р',
    0x69: 'и', 0x71: 'я', 0x6E: 'н', 0x74: 'т', 0x5C: 'Э', 0x77: 'ю', 0x46: 'Ф', 0x5E: 'Ч', 0x44: 'Д', 0x62: 'б',
    0x73: 'с', 0x75: 'у', 0x64: 'д', 0x60: 'ж', 0x6A: 'й', 0x6C: 'л', 0x47: 'Г', 0x78: 'ы', 0x7A: 'з', 0x7E: 'ч',
    0x6D: 'м', 0x67: 'г', 0x79: 'ь', 0x70: 'п', 0x76: 'в', 0x55: 'У', 0x7D: 'щ', 0x66: 'ф', 0x7C: 'э', 0x7B: 'ш',
    0x50: 'П', 0x52: 'Р', 0x68: 'х', 0x41: 'А', 0x4B: 'К', 0x4D: 'М', 0x4F: 'О', 0x54: 'Т', 0x61: 'а', 0x65: 'е',
    0x6B: 'к', 0x6F: 'о',
    0x40: 'Ж', 0x4A: 'Й', 0x4C: 'Л', 0x48: 'Х', 0x5B: 'Ш', 0x5D: 'Щ', 0x59: 'Ь', 0x57: 'Ю',
}
_RUSSIAN_LOOKALIKES = {'A': 0x41, 'B': 0x56, 'E': 0x45, 'C': 0x53, 'H': 0x4E, 'K': 0x4B, 'M': 0x4D, 'O': 0x4F,
                       'P': 0x52, 'T': 0x54, 'X': 0x48, 'a': 0x61, 'c': 0x73, 'e': 0x65, 'k': 0x6B, 'o': 0x6F,
                       'p': 0x72, 'x': 0x68, 'y': 0x75}
_GREEK = dict(zip(range(0x41, 0x59), 'ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ'))
_GREEK.update({0x23: '£', 0x59: 'ά', 0x5A: 'έ', 0x5B: 'ή', 0x5C: 'ί', 0x5D: 'ό', 0x5F: '—', 0x79: 'ς', 0x7A: 'ϊ',
               0x7C: 'ύ', 0x7D: 'ώ', 0x7E: '§'})
_GREEK.update(zip(range(0x61, 0x79), 'αβγδεζηθικλμνξοπρστυφχψω'))
_GREEK.update(zip(range(0x91, 0xA1), 'ABCDEFGHIJKLMNOP'))
# Subtitle Edit has V to Z at A1 to A5 as well as at A6 to AB; the capitals
# run on from A as the small letters do, so Q to U are taken to be there.
_GREEK.update(zip(range(0xA1, 0xAB), 'QRSTUVWXYZ'))
_GREEK.update({0xAB: 'Z', 0xD9: 'δι'})
_GREEK.update(zip(range(0xB1, 0xCB), 'abcdefghijklmnopqrstuvwxyz'))
_ARABIC = {
    0x58: 'م', 0x41: 'ا', 0x4E: 'ص', 0x42: 'ب', 0x46: 'ح', 0x57: 'ل', 0x47: 'خ', 0x5D: 'ي', 0x4A: 'ر', 0x2C: '،',
    0x59: 'ن', 0x5A: 'و', 0x43: 'ت', 0x1D: '-', 0x49: 'ذ', 0x45: 'ج', 0x5B: 'ه', 0x56: 'ك', 0x21: '؟', 0x4C: 'س',
    0x52: 'ع', 0x5C: 'ة', 0x5E: 'ى', 0x61: 'أ', 0x48: 'د', 0x4D: 'ش', 0x60: 'ء', 0x68: 'ﻷ', 0x54: 'ف', 0x55: 'ق',
    0x22: '!', 0x67: 'ﻻ', 0x66: 'ؤ', 0x64: 'آ', 0x50: 'ط', 0x6A: 'ﻵ', 0x4F: 'ض', 0x6B: 'ﺋ', 0x44: 'ث', 0x51: 'ظ',
    0x53: 'غ', 0x4B: 'ز', 0x23: '"', 0x6C: 'ـ',
}
_HEBREW = dict(zip((0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47, 0x49, 0x4C, 0x4D, 0x4E, 0x4F, 0x50, 0x51, 0x52,
                    0x54, 0x56, 0x57, 0x58, 0x59, 0x5A, 0x4B, 0x4A, 0x48, 0x53, 0x55), 'אבגדהוזחילםמןנסעפצקרשתכךטףץ'))
_HEBREW.update(zip(range(0xB1, 0xCB), 'abcdefghijklmnopqrstuvwxyz'))
_HEBREW.update(zip((0x91, 0xDB, 0x93, 0xDC, 0x95, 0x96, 0x97, 0xAB, 0x99, 0x9A, 0x9B, 0x9C, 0xDD, 0xDE, 0x9F, 0xA0,
                    0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'))
_TABLES = {RUSSIAN: _RUSSIAN, GREEK: _GREEK, ARABIC: _ARABIC, HEBREW: _HEBREW}
# A Latin letter or a digit, and a run of them with what lies between,
# Hebrew aside: stored back to front in a Hebrew line.
_LEFT_TO_RIGHT = re.compile(r'[A-Za-z0-9](?:[^֐-׿]*[A-Za-z0-9])?')


def _encoder_table(table, plain_ascii):
    """{char: byte} for a table: its letters (the first byte for each), then
    the ASCII it leaves alone."""
    out = {}
    for byte, char in table.items():
        if len(char) == 1:
            out.setdefault(char, byte)
    if plain_ascii:
        for byte in range(0x20, 0x7F):
            if byte != 0x7C and byte not in table:
                out.setdefault(chr(byte), byte)
    return out


_TO_TABLE = {RUSSIAN: {**_encoder_table(_RUSSIAN, True), **_RUSSIAN_LOOKALIKES},
             GREEK: _encoder_table(_GREEK, False), ARABIC: _encoder_table(_ARABIC, True),
             HEBREW: _encoder_table(_HEBREW, True)}
# Greek's ASCII is its own bytes where the table does not put a letter.
for _byte in range(0x20, 0x41):
    _TO_TABLE[GREEK].setdefault(chr(_byte), _byte)


def _language_of(language_id):
    """The ISO 639-1 code for a language byte."""
    return {RUSSIAN: 'ru', GREEK: 'el', ARABIC: 'ar', HEBREW: 'he', CHINESE_TRADITIONAL: 'zh-tw',
            CHINESE_SIMPLIFIED: 'zh'}.get(language_id) or ebustl.LANGUAGES.get(f'{language_id:02X}', '')


def _hebrew_order(items):
    """A Hebrew line as stored, from as it is read, and back: its runs of
    Latin letters and digits turned round. `items` are [(char, Style)]."""
    items = list(items)
    for match in _LEFT_TO_RIGHT.finditer(''.join(char for char, _style in items)):
        items[match.start():match.end()] = items[match.start():match.end()][::-1]
    return items


# Reading.
def _runs(field, language_id):
    """[(text, Style)] for the bytes of a line."""
    runs = []
    style = markup.PLAIN
    if language_id in (CHINESE_TRADITIONAL, CHINESE_SIMPLIFIED):
        field = field.rstrip(b'\x00\x7f')
        if len(field) % 2:
            field += b'\x00'
        for char in field.decode('utf-16-be', 'replace'):
            code = ord(char)
            if code == _ITALIC_ON:
                style = style._replace(italic=True)
            elif code == _ITALIC_OFF:
                style = style._replace(italic=False)
            elif code in _COLORS:
                style = style._replace(color=_COLORS[code])
            elif code == _WHITE:
                style = style._replace(color='')
            elif code not in (0, _FILLER):
                runs.append((char, style))
        return runs
    table = _TABLES.get(language_id, {})
    index = 0
    while index < len(field):
        byte = field[index]
        index += 1
        if byte in (0, _FILLER):
            continue
        if byte in table:
            runs.append((table[byte], style))
        elif byte == _ITALIC_ON and language_id != GREEK:
            style = style._replace(italic=True)
        elif byte == _ITALIC_OFF and language_id != GREEK:
            style = style._replace(italic=False)
        elif byte in _COLORS:
            style = style._replace(color=_COLORS[byte])
        elif byte == _WHITE:
            style = style._replace(color='')
        elif table:
            char = bytes([byte]).decode('cp1252', 'ignore')
            runs.append(('-' if byte == 0xBE else char, style))
        elif byte in _LATIN:
            runs.append((_LATIN[byte], style))
        elif byte in _BRACKETS and language_id not in _SCANDINAVIAN:
            runs.append((_BRACKETS[byte], style))
        elif byte in _ACCENTS or byte == 0x80:
            # The accent goes on the letter after it.
            if index < len(field) and 0x20 <= field[index] < 0x7F:
                letter = chr(field[index])
                index += 1
                runs.append((unicodedata.normalize('NFC', letter + _ACCENTS.get(byte, '')), style))
        else:
            runs.append((bytes([byte]).decode('cp1252', 'ignore'), style))
    if language_id == HEBREW:
        runs = _hebrew_order((char, style) for text, style in runs for char in text)
    return runs


def _timecode_frames(text, nominal):
    numbers = re.findall(r'\d+', text)
    if len(numbers) != 4:
        return 0
    hours, minutes, seconds, frames = (int(number) for number in numbers)
    return ((hours * 60 + minutes) * 60 + seconds) * nominal + frames


def _text(data, start, size):
    return data[start:start + size].split(b'\x00')[0].decode('cp1252', 'replace').strip()


def is_890(data):
    """True when `data` is long enough to be a Cavena 890 file with a
    subtitle: the format has no signature to look for."""
    return len(data) >= HEADER_SIZE + BLOCK_SIZE - 4


def parse(data, fps=DEFAULT_FPS):
    """(subtitles, {'language', 'fps', 'start', 'title'}) in Cavena 890
    bytes, frames counted at `fps`. Raises ValueError for bytes that are
    not Cavena 890."""
    fps = float(fps or DEFAULT_FPS)
    if not is_890(data):
        raise ValueError('Not a Cavena 890 file: it is too short to hold its header and a subtitle')
    line1_id, line2_id = data[146] or ENGLISH, data[147] or ENGLISH
    fonts = data[187:193].decode('ascii', 'replace'), data[246:252].decode('ascii', 'replace')
    # The fonts say the table as well as the language bytes do.
    if line1_id == HEBREW or 'HEBNOA' in fonts:
        line1_id = line2_id = HEBREW
    if line1_id == GREEK or any(font.startswith('GREEK') for font in fonts):
        line1_id = line2_id = GREEK
    if line2_id == ARABIC or fonts[0] in ('ARABIC', 'ARA19N'):
        line1_id = line2_id = ARABIC
    elif line1_id == RUSSIAN or any(font.startswith('KYRIL') for font in fonts):
        line1_id = line2_id = RUSSIAN
    elif line1_id in (CHINESE_SIMPLIFIED, CHINESE_TRADITIONAL) or 'CCKM44' in fonts:
        line1_id = line2_id = line1_id if line1_id in (CHINESE_SIMPLIFIED, CHINESE_TRADITIONAL) else CHINESE_TRADITIONAL

    entries = []
    for offset in range(HEADER_SIZE, len(data) - (BLOCK_SIZE - 4) + 1, BLOCK_SIZE):
        block = data[offset:offset + BLOCK_SIZE]
        number = block[0] * 256 + block[1]
        begin = int.from_bytes(block[2:5], 'big')
        end = int.from_bytes(block[5:8], 'big')
        lines = [_runs(block[16:16 + LINE_SIZE], line1_id), _runs(block[73:73 + LINE_SIZE], line2_id)]
        boxed = 0xA0 <= block[70] <= 0xA9
        if entries and entries[-1]['number'] == number:
            entries[-1]['lines'].extend(lines)
            entries[-1]['boxed'] = entries[-1]['boxed'] or boxed
        else:
            entries.append({'number': number, 'begin': begin, 'end': end, 'align': block[8], 'lines': lines,
                            'boxed': boxed})
    nominal = int(round(fps))
    start = _timecode_frames(data[256:267].decode('ascii', 'replace'), nominal)
    # Times from the programme's start, when the subtitles come after it.
    if not entries or min(entry['begin'] for entry in entries) < start:
        start = 0

    segments = []
    for entry in entries:
        runs = []
        for line in entry['lines']:
            if ''.join(text for text, _style in line).strip():
                if runs:
                    runs.append(('\n', markup.PLAIN))
                runs.extend(line)
        if entry['boxed']:
            runs = [(text, style._replace(background='#000000')) for text, style in runs]
        body = markup.tidy(runs)
        if not markup.plain(body).strip():
            continue
        keypad = 1 if entry['align'] == 0x50 else 3 if entry['align'] == 0x52 else 2
        begin = round((entry['begin'] - start) / fps, 3)
        segments.append({'start': begin, 'end': max(begin, round((entry['end'] - start) / fps, 3)),
                         'text': (markup.with_alignment('', keypad) if keypad != 2 else '') + body})
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments, {'language': _language_of(data[146] or ENGLISH), 'fps': fps,
                      'start': STARTS[1] if start and start == _timecode_frames(STARTS[1], nominal) else STARTS[0],
                      'title': _text(data, 40, 28) or _text(data, 218, 28)}


def read(path, fps=DEFAULT_FPS):
    """(subtitles, its settings) in the Cavena 890 file at `path`, as
    parse() gives them."""
    with open(path, 'rb') as cavena_file:
        return parse(cavena_file.read(), fps)


# Writing.
def _encode_latin(char, language_id):
    """Bytes for a character in the Latin table, or None."""
    if char in _TO_LATIN:
        return bytes([_TO_LATIN[char]])
    if char in '[]':
        return None if language_id in _SCANDINAVIAN else bytes([0xE5 if char == '[' else 0xE6])
    if 0x20 <= ord(char) < 0x7F:
        return None if char in '\\|}' else bytes([ord(char)])
    decomposed = unicodedata.normalize('NFD', char)
    accent = None
    if len(decomposed) == 2 and decomposed[1] in _TO_ACCENT and 0x20 <= ord(decomposed[0]) < 0x7F:
        accent = bytes([_TO_ACCENT[decomposed[1]], ord(decomposed[0])])
        # The accents files have most; Windows-1252's own letter for the rest.
        if accent[0] <= 0x86:
            return accent
    try:
        single = char.encode('cp1252')
    except UnicodeEncodeError:
        single = b''
    if len(single) == 1 and single[0] >= 0xA0 and single[0] not in _LATIN_TAKEN:
        return single
    return accent


def _encode(char, language_id):
    """Bytes for a character in the table of `language_id`: its own, its
    letter without the accent, or "?"."""
    if language_id in (CHINESE_SIMPLIFIED, CHINESE_TRADITIONAL):
        return char.encode('utf-16-be', 'replace') if ord(char) < 0x10000 else '?'.encode('utf-16-be')
    if language_id in _TO_TABLE:
        found = _TO_TABLE[language_id].get(char)
        found = bytes([found]) if found is not None else None
    else:
        found = _encode_latin(char, language_id)
    if found is not None:
        return found
    spelled = _SPELLED.get(char)
    if spelled is None:
        base = ''.join(c for c in unicodedata.normalize('NFKD', char) if not unicodedata.combining(c))
        spelled = base if base and base != char else None
    if spelled is not None:
        return b''.join(_encode(c, language_id) for c in spelled)
    return b'?'


def _nearest_color(color):
    """The colour code nearest `color` ('' is white)."""
    if not color:
        return _WHITE
    rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    candidates = {**_COLORS, _WHITE: '#ffffff'}
    return min(candidates, key=lambda code: sum((a - b) ** 2 for a, b in zip(
        rgb, (int(candidates[code][i:i + 2], 16) for i in (1, 3, 5)))))


def _line_pieces(line, language_id):
    """[(bytes, is a space)] for a line of [(char, Style)]: each character's
    bytes, with the codes for its italics and colour before it."""
    chinese = language_id in (CHINESE_SIMPLIFIED, CHINESE_TRADITIONAL)
    italics = language_id in (CHINESE_SIMPLIFIED, CHINESE_TRADITIONAL, HEBREW) or language_id not in _TABLES

    def code(value):
        return bytes([0, value]) if chinese else bytes([value])

    pieces, italic, color = [], False, _WHITE
    for index, char_style in enumerate(line):
        char, style = char_style
        codes = b''
        if not char.strip():
            # Italics and a colour end before a space, not after it.
            following = next((s for c, s in line[index + 1:] if c.strip()), None)
            if italic and (following is None or not following.italic):
                italic = False
                codes += code(_ITALIC_OFF)
            if (following is not None and not chinese and color != _WHITE
                    and _nearest_color(following.color) == _WHITE):
                color = _WHITE
                codes += code(_WHITE)
        else:
            if italics and style.italic != italic:
                italic = style.italic
                codes += code(_ITALIC_ON if italic else _ITALIC_OFF)
            if not chinese and _nearest_color(style.color) != color:
                color = _nearest_color(style.color)
                codes += code(color)
        pieces.append((codes + _encode(char, language_id), not char.strip()))
    if italic:
        pieces.append((code(_ITALIC_OFF), False))
    return pieces


def _wrapped(pieces):
    """The bytes of a line, broken at spaces into lines of 51 bytes."""
    out, current, last_space = [], [], None
    for piece, is_space in pieces:
        if sum(len(p) for p, _s in current) + len(piece) > LINE_SIZE:
            if last_space is not None and not is_space:
                out.append(current[:last_space])
                current = current[last_space + 1:]
            elif current:
                out.append(current)
                current = []
            last_space = None
            if is_space:
                continue
        current.append((piece, is_space))
        if is_space:
            last_space = len(current) - 1
    out.append(current)
    lines = []
    for line in out:
        while line and line[0][1]:
            line = line[1:]
        while line and line[-1][1]:
            line = line[:-1]
        if line:
            lines.append(b''.join(piece for piece, _space in line))
    return lines


def _script_table(text):
    """The table the letters of `text` need, or None for Latin."""
    for char in text:
        code = ord(char)
        if 0x0590 <= code <= 0x05FF:
            return HEBREW
        if 0x0600 <= code <= 0x06FF or 0xFE70 <= code <= 0xFEFF:
            return ARABIC
        if 0x0400 <= code <= 0x04FF:
            return RUSSIAN
        if 0x0370 <= code <= 0x03FF:
            return GREEK
        if 0x4E00 <= code <= 0x9FFF or 0x3000 <= code <= 0x303F or 0xFF00 <= code <= 0xFFEF:
            return CHINESE_SIMPLIFIED
    return None


def language_id(language, text=''):
    """The language byte for a language tag ('da', 'zh-TW' ...), the table
    of the letters in `text` when the language's would not hold them."""
    tag = str(language or '').replace('_', '-').lower()
    primary = tag.split('-')[0]
    if primary == 'zh':
        chosen = CHINESE_TRADITIONAL if any(part in ('tw', 'hk', 'mo', 'hant') for part in tag.split('-')[1:]) \
            else CHINESE_SIMPLIFIED
    else:
        chosen = {'ru': RUSSIAN, 'uk': RUSSIAN, 'bg': RUSSIAN, 'be': RUSSIAN, 'el': GREEK, 'ar': ARABIC,
                  'he': HEBREW, 'iw': HEBREW}.get(primary)
        if chosen is None:
            code = {language: code for code, language in ebustl.LANGUAGES.items()}.get(
                'no' if primary in ('nb', 'nn') else primary)
            chosen = int(code, 16) if code and int(code, 16) < 0x30 else ENGLISH
    needed = _script_table(text)
    if needed is None:
        return chosen if chosen not in _TABLES and chosen not in (CHINESE_SIMPLIFIED, CHINESE_TRADITIONAL) else ENGLISH
    if needed == CHINESE_SIMPLIFIED and chosen in (CHINESE_SIMPLIFIED, CHINESE_TRADITIONAL):
        return chosen
    return needed


def _ascii(value, size):
    text = ''.join(c for c in unicodedata.normalize('NFKD', str(value or '')) if not unicodedata.combining(c))
    return text.encode('ascii', 'replace')[:size].ljust(size, b'\x00')


def _line_field(data, language_id):
    filler = b'\x00' if language_id in (CHINESE_SIMPLIFIED, CHINESE_TRADITIONAL) else bytes([_FILLER])
    return data[:LINE_SIZE].ljust(LINE_SIZE, filler)


def write(segments, fps=DEFAULT_FPS, language='', title='', start='00:00:00:00'):
    """Cavena 890 bytes for `segments`: frames at `fps`, in the table for
    `language` (else for the letters the text holds), from a programme start
    of `start` ("HH:MM:SS:FF")."""
    fps = float(fps or DEFAULT_FPS)
    nominal = int(round(fps))
    subtitles = []
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        lead, rest = markup.split_lead(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
        lines = [[]]
        for run, style in markup.without_readings(markup.spans(rest)):
            for index, part in enumerate(run.split('\n')):
                if index:
                    lines.append([])
                lines[-1].extend((char, style) for char in part)
        lines = [line for line in lines if ''.join(char for char, _style in line).strip()]
        if lines:
            subtitles.append((float(segment.get('start', 0)), float(segment.get('end', 0)), lines,
                              markup.alignment(lead)))
    text = ''.join(char for _b, _e, lines, _k in subtitles for line in lines for char, _s in line)
    chosen = language_id(language, text)
    offset = _timecode_frames(start, nominal) if start in STARTS else 0

    blocks = []
    for number, (begin, end, lines, keypad) in enumerate(subtitles, start=1):
        rows = []
        for line in lines:
            while line and not line[0][0].strip():
                line = line[1:]
            while line and not line[-1][0].strip():
                line = line[:-1]
            if chosen == HEBREW:
                line = _hebrew_order(line)
            rows.extend(_wrapped(_line_pieces(line, chosen)))
        boxed = all(style.background for line in lines for char, style in line if char.strip())
        frames_in = int(begin * fps + 0.5) + offset
        frames_out = max(frames_in, int(max(begin, end) * fps + 0.5) + offset)
        # Two lines a block, the last of a one-line block at the bottom.
        pairs = [rows[i:i + 2] for i in range(0, len(rows), 2)]
        for pair in pairs:
            first, second = (b'', pair[0]) if len(pair) == 1 else pair
            block = (bytes([(number * 16) // 256 % 256, (number * 16) % 256])
                     + min(frames_in, 0xFFFFFF).to_bytes(3, 'big') + min(frames_out, 0xFFFFFF).to_bytes(3, 'big')
                     + bytes([_ALIGN[keypad % 3]]) + bytes(7)
                     + _line_field(first, chosen) + bytes([0, 0, 0, _BOX if boxed else 0, 0, 0])
                     + _line_field(second, chosen) + bytes(4))
            blocks.append(block)

    count = len(subtitles)
    font = _FONTS.get(chosen, _DEFAULT_FONT).encode('ascii')
    # The programme start is 10:00:00:00 whatever the times count from, as
    # Subtitle Edit writes it: times from 0 come before it, and are read as
    # they are.
    header = (bytes(2) + bytes(20) + bytes(18) + _ascii(title, 28) + bytes(28) + bytes(9) + bytes(11) + bytes(18)
              + bytes([0xA0, 0x05, 0x04, 0x03, 0x06, 0x06, 0x08, 0x90, 0, 0, 0, 0, chosen, chosen])
              + bytes(24) + bytes([0x08, 0x90, 0, 0, 0, 0, 0]) + bytes([count % 256, count // 256 % 256])
              + font.rjust(14, b'\x00') + bytes(13) + bytes([count % 256, count // 256 % 256]) * 2 + bytes(6)
              + bytes(28) + font + bytes([0x3D, 0x8D]) + STARTS[1].encode('ascii') + bytes(26) + b'BTD' + bytes(92))
    assert len(header) == HEADER_SIZE
    data = header + b''.join(blocks)
    # The last block leaves off its four closing zeros.
    return data[:-4] if blocks else data


def save(path, segments, **options):
    """Write `segments` to `path` as Cavena 890; `options` as write() takes
    them."""
    with open(path, 'wb') as cavena_file:
        cavena_file.write(write(segments, **options))
