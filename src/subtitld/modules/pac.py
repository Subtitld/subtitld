"""Screen PAC (.pac) and UniPac (.fpc): read and write the binary subtitle
files Screen Systems' WinCAPS and Cavena's tools exchange between
subtitling houses.

Screen never published the format. What is here is what has been worked
out from real files, most of it by Subtitle Edit (libse's Pac.cs, GPL-3 as
Subtitld is): its character tables are in modules.pac_tables, and every
detail below marked "a guess" is one no file has confirmed.

A file is a 20-byte header (01, then zeros; what else it could hold is not
known), then a record a subtitle:

    00, its number (two bytes, little-endian, from 0), 60
    the time in and the time out, two bytes each for HHMM and SSFF, as
        numbers: 10:01:02:12 is 1001 and 0212 (FFFF FFFF for no time)
    the length of what follows, two bytes
    the row it starts on, 0 at the top to 11 at the bottom
    (UniPac only: 80 80 80, whose meaning is not known)
    FE, the alignment (0 right, 1 left, 2 centre; 08 added for a line in
        the file's second code page), 03 — before each line
    the line's text

and after the last one FF, eleven zeros, 11 00 and "dummy end of file", as
Subtitle Edit writes it (a guess: what Screen's own tools put there is not
known; readers stop at the last record anyway).

The text is in a code page the file does not name: Latin, Latin Czech,
Latin Turkish or Latin Portuguese (Latin with an accent byte before the
letter it goes on), Greek, Cyrillic, Hebrew, Arabic or Thai, a byte or a
few a character; or, a line at a time, Chinese (Big5 or GB2312/GBK),
Korean or Japanese after 1F "W16" and a byte, two bytes a character and a
character of one byte written after a 00. UniPac writes each line as
UTF-8 between 1F EF BB BF and a full stop, its own full stops as FF.

Reading:

- each record by its 60 and its length, so that a record's text never
  passes for the next record; a first record that is a "STORY:" header is
  no subtitle, and its "LANG:" names the code page and the language;
- the code page, as nothing in the file says it, by trying each on the
  text: the one under which its letters are of its own script and the
  most of its words are common ones of a language written in it (a guess
  of a kind: a short file can fool it); lines marked for the second code
  page get theirs found the same way;
- italics as PAC writes them, "<" to ">" or the end of the line, each
  mark standing in a space; the colours 01 to 07 as teletext's (red,
  green, yellow, blue, magenta, cyan, white — a guess Subtitle Edit makes
  too, without reading them); FF a space; other codes (0B, 0D, 1D ...) and
  1F "C" with a digit left out, 1F "C" with anything else a comment to the
  end of the record; the alignment's 04, which Subtitle Edit takes for
  italics without reading it either, left alone;
- Hebrew's and Arabic's runs of Latin letters and digits in their order
  for reading (the files keep them reversed, as Subtitle Edit has it), and
  Arabic's vowel marks, written before their letter, after it;
- the frame rate, which no file says, from the caller: the video's, else
  25; the frames counting within a second of the clock, as Subtitle Edit
  counts them;
- the row and the alignment as one of the nine places (from row 5 the
  middle, from row 9 for the last line the bottom), and the row as a
  vertical margin (modules.position, the 12 rows taken as spanning the
  picture) when it is not where those places put a subtitle: row 1 at the
  top, row 6 in the middle, the last line on row 11 at the bottom.

Writing is the same backwards: a record a subtitle with text, in its order
of start; one code page for the file, the one asked for if the text fits
it, else the language's, else the first that holds every character, with
lines that need another written in a second one, marked; italics,
colours, places and margins as they read. UniPac writes every line as
UTF-8 and no colours.
"""

import unicodedata

from subtitld.modules import markup
from subtitld.modules import pac_tables
from subtitld.modules import position

LATIN_PAGES = ('latin', 'latin-czech', 'latin-turkish', 'latin-portuguese')
TABLE_PAGES = ('greek', 'cyrillic', 'hebrew', 'arabic', 'thai')
WIDE_PAGES = ('chinese-traditional', 'chinese-simplified', 'korean', 'japanese')
CODE_PAGES = LATIN_PAGES + TABLE_PAGES + WIDE_PAGES
ROWS = 12
DEFAULT_FPS = 25.0

_HEADER = b'\x01' + bytes(19)
_FOOTER = b'\xff' + bytes(11) + b'\x11\x00dummy end of file'
_MARKERS = range(0x60, 0x68)
_LINE, _UNICODE_START, _UNICODE_END, _UNICODE_STOP = 0xFE, b'\x1f\xef\xbb\xbf', 0x2E, 0xFF
_WIDE_START = b'\x1fW16'
_FPC_LEAD = b'\x80\x80\x80'
_SECONDARY = 0x08
# FFFF for the hours and minutes: a record with no time (655:35).
_NO_TIME = 655 * 3600
_ITALIC_ON, _ITALIC_OFF = '<', '>'
# Teletext's colours, as PAC's codes 01 to 07 are taken to be (a guess).
_COLORS = {0x01: '#ff0000', 0x02: '#00ff00', 0x03: '#ffff00', 0x04: '#0000ff', 0x05: '#ff00ff', 0x06: '#00ffff',
           0x07: ''}
# Codes left out of the text: 00, the colours, and what teletext's would be
# (start box, double height, new background ...).
_CODES = frozenset(range(0x00, 0x09)) | {0x0B, 0x0D, 0x17, 0x1D}
_CLOSING = ',.;:!?)]}\'"’”»…'
# What a character a page has no place for is written as, before giving up.
_SUBSTITUTES = {'”': '"', '“': '"', '„': '"', '’': "'", '‘': "'", '…': '...', '–': '-', '—': '-', '\u00a0': ' '}


def _by_bytes(table):
    return {key.to_bytes(1 if key < 0x100 else 2 if key < 0x10000 else 3, 'big'): char for key, char in table.items()}


_LATIN = _by_bytes(pac_tables.LATIN)
_LATIN_OVERRIDES = {'latin-turkish': _by_bytes(pac_tables.TURKISH),
                    'latin-portuguese': _by_bytes(pac_tables.PORTUGUESE)}
_LATIN_PRIORITY = _by_bytes(pac_tables.LATIN_PRIORITY)
_LATIN_CODECS = {'latin': 'iso8859_1', 'latin-czech': 'iso8859_2', 'latin-turkish': 'iso8859_9',
                 'latin-portuguese': 'iso8859_1'}
# The accents written before their letter, for a letter the tables do not
# list: each byte's as the tables use it (a guess for the letters they do
# not have: E7 is a caron in most, EA a breve in some, a caron in others).
_ACCENTS = {0xE0: '\u0303', 0xE1: '\u030a', 0xE2: '\u0301', 0xE3: '\u0300', 0xE4: '\u0302', 0xE5: '\u0308',
            0xE6: '\u0327', 0xE7: '\u030c', 0xE8: '\u0307', 0xE9: '\u0304', 0xEA: '\u0306', 0xEB: '\u0328',
            0xEC: '\u030b'}
_TURKISH_ACCENTS = {**_ACCENTS, 0xE7: '\u0306'}
_TABLES = {'greek': _by_bytes(pac_tables.GREEK), 'cyrillic': _by_bytes(pac_tables.CYRILLIC),
           'hebrew': _by_bytes(pac_tables.HEBREW), 'arabic': _by_bytes(pac_tables.ARABIC),
           'thai': _by_bytes(pac_tables.THAI)}
# Pages whose ASCII is ASCII where the table says nothing else; Greek and
# Cyrillic put their own letters there.
_ASCII_PAGES = ('hebrew', 'arabic', 'thai')
_WIDE_CODECS = {'chinese-traditional': 'cp950', 'chinese-simplified': 'gbk', 'korean': 'cp949', 'japanese': 'cp932'}
# Latin letters the Cyrillic table has no place for, written as the
# Cyrillic letters that look like them (a choice of Subtitld's).
_CYRILLIC_LOOKALIKES = {'A': 'А', 'B': 'В', 'C': 'С', 'E': 'Е', 'K': 'К', 'M': 'М', 'O': 'О', 'T': 'Т', 'I': 'І',
                        'S': 'Ѕ', 'a': 'а', 'x': 'х', 'y': 'у'}
# What the files keep reversed in a right-to-left line.
_MIRRORED = {'arabic': frozenset('0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'),
             'hebrew': frozenset('0123456789abcdefghijklmnopqrstuvwxyz')}
_BRACKETS = str.maketrans('()[]', ')(][')

# The "LANG:" codes of a STORY header (Screen's own), as code pages and
# languages.
_HEADER_LANGUAGES = {
    'DAR': ('arabic', 'ar'), 'OAR': ('arabic', 'ar'), 'ARA': ('arabic', 'ar'), 'ARB': ('arabic', 'ar'),
    'ISR': ('hebrew', 'he'), 'HEB': ('hebrew', 'he'),
    'GRC': ('greek', 'el'), 'GRE': ('greek', 'el'), 'ELL': ('greek', 'el'),
    'DRU': ('cyrillic', 'ru'), 'RUS': ('cyrillic', 'ru'), 'BGR': ('cyrillic', 'bg'), 'MKD': ('cyrillic', 'mk'),
    'MNG': ('cyrillic', 'mn'), 'UKR': ('cyrillic', 'uk'), 'THA': ('thai', 'th'),
    'TRD': ('chinese-traditional', 'cmn-hant-tw'), 'OCH': ('chinese-traditional', 'cmn-hant-tw'),
    'CHT': ('chinese-traditional', 'cmn-hant-tw'), 'SIM': ('chinese-simplified', 'cmn-hans-cn'),
    'CHS': ('chinese-simplified', 'cmn-hans-cn'), 'DJA': ('japanese', 'ja'), 'JPN': ('japanese', 'ja'),
    'DKO': ('korean', 'ko'), 'KOR': ('korean', 'ko'), 'TUR': ('latin-turkish', 'tr'),
    'POR': ('latin-portuguese', 'pt'), 'DPT': ('latin-portuguese', 'pt'), 'CSK': ('latin-czech', 'cs'),
    'CZE': ('latin-czech', 'cs'),
}
# The language a page is surely in, when the file says no other.
_PAGE_LANGUAGES = {'greek': 'el', 'hebrew': 'he', 'arabic': 'ar', 'thai': 'th', 'korean': 'ko', 'japanese': 'ja',
                   'chinese-traditional': 'cmn-hant-tw', 'chinese-simplified': 'cmn-hans-cn'}
# And the page a language is written in, for writing.
_LANGUAGE_PAGES = {
    'el': 'greek', 'ru': 'cyrillic', 'uk': 'cyrillic', 'bg': 'cyrillic', 'mk': 'cyrillic', 'be': 'cyrillic',
    'kk': 'cyrillic', 'mn': 'cyrillic', 'he': 'hebrew', 'iw': 'hebrew', 'yi': 'hebrew', 'ar': 'arabic',
    'fa': 'arabic', 'ur': 'arabic', 'th': 'thai', 'ko': 'korean', 'ja': 'japanese', 'tr': 'latin-turkish',
    'az': 'latin-turkish', 'cs': 'latin-czech', 'sk': 'latin-czech', 'pt': 'latin-portuguese',
}

# Common words, to tell which page a file's bytes are in: a page that
# reads them as common words of a language it writes is the page.
_WORDS = {
    'en': 'the you to and it of that is what in me this not for be have we on do are your no with he my was all '
          'so can just know but here get go like',
    'es': 'que de no la el es en lo un por me qué una te los se con para mi está si bien pero yo eso las sí su tu muy',
    'pt': 'que não de o é um eu se do da você me para com isso em uma está os mas por no na bem sim ele',
    'fr': 'de je est pas le vous la tu que un il et ne les ce en on ça une pour des qui moi bien',
    'de': 'ich sie das ist du nicht die und es der wir was zu ein er in mir mit ja wie den auf mich so',
    'it': 'non di che è la il un per mi ho sono in ma cosa si no ti lo bene questo',
    'nl': 'ik je het de is dat een niet en wat van we in ze op te zijn',
    'nordic': 'jag det är du inte att en och har vad er ikke jeg at og vi på',
    'pl': 'nie to się na jest że co ja do tak jak ale',
    'cs': 'je to se že na ne jsem co jak tak by za',
    'tr': 'bir ve bu ne için değil ben sen var mı çok da de',
    'el': 'και το να η ο δεν θα τα με σε που είναι τι την του μου εγώ εσύ από για ναι όχι αυτό ένα μας σου',
    'ru': 'и в не что я на с он это ты то а как мы да вы но так все она по мне у его из за же нет было тебя меня бы о '
          'если ще е се',
    'he': 'את של לא זה אני על מה הוא אתה יש עם כן היא אם רק',
    'ar': 'في من على أن لا ما هذا هو أنا أنت هل كان مع إلى عن',
}
_WORDS = {language: frozenset(words.split()) for language, words in _WORDS.items()}
_PAGE_WORDS = {'greek': ('el',), 'cyrillic': ('ru',), 'hebrew': ('he',), 'arabic': ('ar',), 'thai': ()}
_LATIN_WORDS = ('en', 'es', 'pt', 'fr', 'de', 'it', 'nl', 'nordic', 'pl', 'cs', 'tr')
_LATIN_VARIANTS = {'pt': 'latin-portuguese', 'tr': 'latin-turkish', 'cs': 'latin-czech'}
# And common characters, for the pages of two bytes a character.
_COMMON = {
    'korean': frozenset('이다는요고하에지가서어아게니도를나을의해리기사있거내그한자대데야우제수네말좋안시면구로일무'),
    'chinese-simplified': frozenset('的一是不了人我在有他中大上到你地出也就要下以生自着去可她小心多天能好都然起事只想看手用又'
                                    '如所面这们说来个时会没为过还对吗么样请谢让给国发现开关问见长头门东车马话学后里爱听动经体'),
    'chinese-traditional': frozenset('的一是不了人我在有他中大上到你地出也就要下以生自著去可她小心多天能好都然起事只想看手用又'
                                     '如所面這們說來個時會沒為過還對嗎麼樣請謝讓給國發現開關問見長頭門東車馬話學後裡愛聽動經體'),
}


# -- Reading -----------------------------------------------------------------

def _decode_latin(page, data):
    """(text, bytes it could not read) for `data` in a Latin page."""
    tables = [table for table in (_LATIN_OVERRIDES.get(page), _LATIN) if table]
    accents = _TURKISH_ACCENTS if page == 'latin-turkish' else _ACCENTS
    codec = _LATIN_CODECS.get(page, 'iso8859_1')
    out, unknown, index = [], 0, 0
    while index < len(data):
        pair, following = data[index:index + 2], data[index + 1:index + 3]
        found = None
        for table in tables:
            if len(pair) == 2 and pair in table and not (
                    len(following) == 2 and following in table
                    and _LATIN_PRIORITY.get(pair, 2) <= _LATIN_PRIORITY.get(following, 2)):
                found, size = table[pair], 2
                break
            if data[index:index + 1] in table:
                found, size = table[data[index:index + 1]], 1
                break
        if found is None:
            byte = data[index]
            size = 1
            if byte in accents and len(pair) == 2 and chr(pair[1]).isalpha() and pair[1] < 0x7F:
                found, size = unicodedata.normalize('NFC', chr(pair[1]) + accents[byte]), 2
            elif byte > 0x1F:
                found = bytes([byte]).decode(codec, 'replace')
            if not found or found == '\ufffd' or unicodedata.category(found) == 'Cc':
                found = ''
                unknown += 1
        out.append(found)
        index += size
    return ''.join(out), unknown


def _decode_table(page, data):
    """(text, bytes it could not read) for `data` in Greek, Cyrillic,
    Hebrew, Arabic or Thai."""
    table = _TABLES[page]
    out, unknown, index = [], 0, 0
    mark = ''                                   # an Arabic vowel mark, for the letter after it
    while index < len(data):
        byte = data[index]
        found, size = None, 1
        if 0x30 <= byte <= 0x39:
            found = chr(byte)
        else:
            for size in (3, 2, 1):
                key = data[index:index + size]
                if len(key) == size and key in table:
                    found = table[key]
                    break
            else:
                size = 1
        if found is None:
            if page in _ASCII_PAGES and 0x20 <= byte < 0x7F:
                found = chr(byte)
            else:
                found = ''
                unknown += 1
        if page == 'arabic' and size == 1 and byte in pac_tables.ARABIC_BEFORE_LETTER:
            out.append(mark)
            mark = found
        elif mark and byte >= 0x80:
            out.append(found + mark)
            mark = ''
        else:
            out.append(mark + found)
            mark = ''
        index += size
    out.append(mark)
    return ''.join(out), unknown


def _decode_narrow(page, data):
    if page in _TABLES:
        return _decode_table(page, data)
    # The pages of two bytes write single-byte lines in Latin.
    return _decode_latin(page if page in LATIN_PAGES else 'latin', data)


def _decode_wide(page, units):
    """(text, characters it could not read) for W16 `units` in a page of
    two bytes a character."""
    codec = _WIDE_CODECS.get(page, 'cp950')
    out, unknown = [], 0
    for unit in units:
        if len(unit) == 1:
            out.append(chr(unit[0]) if 0x20 <= unit[0] < 0x7F else '')
            continue
        try:
            out.append(unit.decode(codec))
        except UnicodeDecodeError:
            unknown += 1
    return ''.join(out), unknown


class _Line:
    """A line of a record: its alignment, whether it is in the second code
    page, and its pieces — ('narrow', bytes), ('wide', [units]),
    ('text', str) or ('color', '#rrggbb')."""

    def __init__(self, alignment):
        self.alignment = alignment & 0x03
        self.secondary = bool(alignment & _SECONDARY)
        self.pieces = []

    def add(self, kind, value):
        if self.pieces and self.pieces[-1][0] == kind and kind in ('narrow', 'wide', 'text'):
            self.pieces[-1] = (kind, self.pieces[-1][1] + value)
        else:
            self.pieces.append((kind, value))

    def has(self, kind):
        return any(piece[0] == kind for piece in self.pieces)


def _lines(body):
    """The _Lines of a record's text: `body` from its row on."""
    lines = []
    index = 1
    while index < len(body) and body[index] != _LINE:
        index += 1                              # UniPac's 80 80 80, and whatever else comes first
    line, wide = None, False
    while index < len(body):
        byte = body[index]
        if byte == _LINE and index + 1 < len(body):
            line = _Line(body[index + 1])
            lines.append(line)
            index += 3
            wide = False
        elif line is None:
            index += 1
        elif body.startswith(_WIDE_START, index):
            wide = True
            index += 5
        elif body.startswith(_UNICODE_START, index):
            index += len(_UNICODE_START)
            end = index
            while end < len(body) and body[end] not in (_UNICODE_END, _LINE):
                end += 1
            line.add('text', body[index:end].replace(bytes([_UNICODE_STOP]), b'.').decode('utf-8', 'replace'))
            index = end + (end < len(body) and body[end] == _UNICODE_END)
        elif body.startswith(b'\x1fC', index):
            if index + 2 < len(body) and 0x30 <= body[index + 2] <= 0x39:
                index += 3
            else:
                break                           # a comment, to the end of the record
        elif wide:
            if byte == 0x00 and index + 1 < len(body):
                line.add('wide', [body[index + 1:index + 2]])
                index += 2
            elif byte < 0x20:
                if byte in _COLORS:
                    line.add('color', _COLORS[byte])
                index += 1
            elif byte < 0x80:
                line.add('wide', [body[index:index + 1]])
                index += 1
            else:
                line.add('wide', [body[index:index + 2]])
                index += 2
        elif byte == _UNICODE_STOP:
            line.add('text', ' ')
            index += 1
        elif byte in _CODES:
            if byte in _COLORS:
                line.add('color', _COLORS[byte])
            index += 1
        else:
            line.add('narrow', body[index:index + 1])
            index += 1
    return lines


def _mirror(text, page):
    """A right-to-left line's runs of Latin letters and digits reversed, and
    its brackets turned, as PAC keeps them: the same both ways."""
    chars = _MIRRORED.get(page)
    if not chars:
        return text
    text = text.translate(_BRACKETS)
    out, run = [], []
    for char in text + '\0':
        if char in chars:
            run.append(char)
            continue
        out.extend(reversed(run))
        run = []
        out.append(char)
    return ''.join(out[:-1])


def _decoded(line, page):
    """([(text, colour)], what could not be read) for a _Line in `page`."""
    segments, unknown, color = [], 0, ''
    for kind, value in line.pieces:
        if kind == 'color':
            color = value
            continue
        if kind == 'narrow':
            text, missed = _decode_narrow(page, value)
            text = _mirror(text, page)
        elif kind == 'wide':
            text, missed = _decode_wide(page, value)
        else:
            text, missed = value, 0
        unknown += missed
        segments.append((text, color))
    return segments, unknown


def _runs(lines, pages):
    """[(text, Style)] for a subtitle's _Lines, each in its page."""
    runs = []
    for number, line in enumerate(lines):
        if number:
            runs.append(('\n', markup.PLAIN))
        segments, _unknown = _decoded(line, pages[line.secondary])
        chars = [(char, color) for text, color in segments for char in text]
        italic = False
        for index, (char, color) in enumerate(chars):
            if char in (_ITALIC_ON, _ITALIC_OFF):
                # A mark takes a cell, shown as a space between words (not
                # between a word and the punctuation closing it).
                before = chars[index - 1][0] if index else ' '
                after = chars[index + 1][0] if index + 1 < len(chars) else ' '
                if before.strip() and after.strip() and (char == _ITALIC_ON or after not in _CLOSING):
                    runs.append((' ', markup.Style(color=color)))
                italic = char == _ITALIC_ON
                continue
            runs.append((char, markup.Style(italic=italic, color=color)))
    return runs


def _letters(text):
    return [char for char in text if char.isalpha()]


def _in_script(page, char):
    code = ord(char)
    if page in LATIN_PAGES:
        return code < 0x250
    if page == 'greek':
        return 0x370 <= code <= 0x3FF or 0x1F00 <= code <= 0x1FFF
    if page == 'cyrillic':
        return 0x400 <= code <= 0x4FF
    if page == 'hebrew':
        return 0x590 <= code <= 0x5FF or 0xFB1D <= code <= 0xFB4F
    if page == 'arabic':
        return 0x600 <= code <= 0x6FF or 0x750 <= code <= 0x77F or 0xFB50 <= code <= 0xFDFF or 0xFE70 <= code <= 0xFEFF
    if page == 'thai':
        return 0xE00 <= code <= 0xE7F
    return False


def _word_hits(text, languages):
    """{language: how many of `text`'s words are its common ones}."""
    words = [word.strip('.,;:!?¡¿"\'()-–—…<>«»“”‘’').lower() for word in text.split()]
    return {language: sum(1 for word in words if word in _WORDS[language]) for language in languages}


def _narrow_page(lines):
    """(page, language) for `lines` of single-byte text: the page under
    which they read as their own script's letters, the most of their words
    common ones, the fewest bytes unread; Latin when nothing tells."""
    best = None
    for order, page in enumerate(('latin',) + TABLE_PAGES):
        texts, unknown, total = [], 0, 0
        for line in lines:
            segments, missed = _decoded(line, page)
            texts.append(''.join(text for text, _color in segments).replace(_ITALIC_ON, ' ').replace(_ITALIC_OFF, ' '))
            unknown += missed
            total += sum(len(value) for kind, value in line.pieces if kind == 'narrow')
        text = ' '.join(texts)
        letters = _letters(text)
        if page == 'latin':
            # Mostly ASCII: the other pages' letters, read as Latin, are
            # the accented letters of ISO 8859-1, and nothing else.
            plausible = sum(1 for char in letters if ord(char) < 0x80) >= 0.5 * len(letters)
        else:
            own = sum(1 for char in letters if _in_script(page, char))
            plausible = own > 0 and own >= 0.6 * len(letters)
        if not plausible:
            continue
        hits = _word_hits(text, _LATIN_WORDS if page == 'latin' else _PAGE_WORDS[page])
        words = max(1, len(text.split()))
        score = sum(hits.values()) / words - unknown / max(1, total)
        if best is None or score > best[0]:
            best = (score, page, hits)
    if best is None:
        return 'latin', ''
    _score, page, hits = best
    if page == 'latin':
        # The Latin pages differ in a few places: the language that reads
        # best chooses among them.
        language = max(hits, key=lambda name: hits[name])
        if hits[language] >= 2 and language in _LATIN_VARIANTS:
            return _LATIN_VARIANTS[language], ''
    return page, _PAGE_LANGUAGES.get(page, '')


def _wide_page(lines):
    """The page of two bytes a character `lines` are in: the one under
    which the most of their characters are common ones."""
    units = [unit for line in lines for kind, value in line.pieces if kind == 'wide' for unit in value if len(unit) == 2]
    best = None
    for page in ('chinese-traditional', 'chinese-simplified', 'korean', 'japanese'):
        text, unknown = _decode_wide(page, units)
        if page == 'japanese':
            # Kana, which no other of these pages gives (half-width
            # katakana, which Korean bytes read as Japanese do, are not).
            common = sum(1 for char in text if 0x3040 <= ord(char) <= 0x30FF)
        else:
            common = sum(1 for char in text if char in _COMMON[page])
        score = (common - 2 * unknown) / max(1, len(units))
        if best is None or score > best[0]:
            best = (score, page)
    return best[1]


def _page_of(lines):
    """(page, language) for a group of lines."""
    if any(line.has('wide') for line in lines):
        page = _wide_page(lines)
        return page, _PAGE_LANGUAGES.get(page, '')
    if any(line.has('narrow') for line in lines):
        return _narrow_page(lines)
    return 'latin', ''


def _seconds(data, at, fps):
    """The time at `at`, or None for a time that cannot be one."""
    high = data[at] | data[at + 1] << 8
    low = data[at + 2] | data[at + 3] << 8
    hours, minutes = divmod(high, 100)
    seconds, frames = divmod(low, 100)
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds + frames / fps


def _record_at(data, at, fps):
    """(start, end, body, where the record ends) for a record whose 60 is
    at `at`, or None when there is none there."""
    if at + 15 > len(data) or data[at] not in _MARKERS:
        return None
    length = data[at + 9] | data[at + 10] << 8
    end = at + 11 + length
    if length < 4 or end > len(data):
        return None
    body = data[at + 11:end]
    if body[1] != _LINE and not (len(body) > 7 and body[4] == _LINE):
        return None
    start, finish = _seconds(data, at + 1, fps), _seconds(data, at + 5, fps)
    if start is None or finish is None:
        return None
    return start, finish, body, end


def _records(data, fps):
    """Every record in `data`: the next where one ends, else the first
    found after it."""
    at = len(_HEADER) + 3
    while at < len(data):
        found = _record_at(data, at, fps)
        if not found:
            scan = at - 3
            while scan < len(data) and not found:
                scan += 1
                found = _record_at(data, scan, fps)
            if not found:
                return
        yield found[:3]
        at = found[3] + 3


def is_pac(data):
    """True when `data` begins as a PAC or UniPac file does: 01, then
    zeros, then a record."""
    return len(data) > 40 and data[0] == 1 and not any(data[1:20]) and \
        any(True for _record in _records(data[:4096] if len(data) > 4096 else data, DEFAULT_FPS))


def _story(lines):
    """The "LANG:" of a STORY header's lines, or None when they are not one."""
    text = ''.join(bytes(value).decode('latin-1') if kind == 'narrow' else value if kind == 'text' else ''
                   for line in lines for kind, value in line.pieces)
    if not text.upper().startswith('STORY:'):
        return None
    found = text.upper().find('LANG:')
    said = text[found + 5:].split() if found >= 0 else []
    return said[0][:8].upper() if said else ''


def parse(data, fps=DEFAULT_FPS, code_page=None):
    """(subtitles, {'code_page', 'secondary_code_page', 'language',
    'unicode'}) in PAC or UniPac bytes; frames at `fps` a second. The code
    page is found from the text unless `code_page` names one. Raises
    ValueError for bytes that are not PAC."""
    fps = float(fps or DEFAULT_FPS)
    if len(data) < 24 or data[0] != 1 or any(data[1:20]):
        raise ValueError('Not a PAC file: it does not begin as PAC files do')
    records = [(start, end, body[0], _lines(body)) for start, end, body in _records(data, fps)]
    if not records:
        raise ValueError('Not a PAC file: it holds no subtitle records')
    language, pages = '', [None, None]
    story = _story(records[0][3])
    if story is not None:
        records = records[1:]
        if story in _HEADER_LANGUAGES:
            pages[0], language = _HEADER_LANGUAGES[story]
    if code_page in CODE_PAGES:
        pages[0] = code_page
    for secondary in (False, True):
        group = [line for _s, _e, _r, lines in records for line in lines if line.secondary == secondary]
        if pages[secondary] is None and group:
            pages[secondary], found = _page_of(group)
            if not secondary:
                language = language or found
    secondary_page = pages[1] if pages[1] not in (None, pages[0]) else ''
    pages = [page or 'latin' for page in pages]

    segments = []
    for start, end, row, lines in records:
        if start >= _NO_TIME:
            continue
        body = markup.tidy(_runs(lines, pages))
        if not markup.plain(body).strip():
            continue
        count = body.count('\n') + 1
        alignment = next((line.alignment for line in lines if line.pieces), 2)
        column = 0 if alignment == 1 else 2 if alignment == 0 else 1
        if row <= 4:
            keypad, normal, down = 7 + column, 1, row / ROWS
        elif row + count - 1 >= 9:
            keypad, normal, down = 1 + column, ROWS - count, (ROWS - row - count) / ROWS
        else:
            keypad, normal, down = 4 + column, 6, (row + count / 2 - ROWS / 2) / ROWS
        place = position.NOWHERE if row == normal else position.Place(v=position.percent(down * 100))
        segments.append(position.store({
            'start': round(start, 3), 'end': round(max(start, end), 3),
            'text': (markup.with_alignment('', keypad) if keypad != 2 else '') + body}, place))
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    unicode = any(line.has('text') and not line.has('narrow') and not line.has('wide')
                  for _s, _e, _r, lines in records for line in lines)
    return segments, {'code_page': pages[0], 'secondary_code_page': secondary_page,
                      'language': language, 'unicode': unicode}


def read(path, fps=DEFAULT_FPS, code_page=None):
    """(subtitles, its settings) in the PAC or UniPac file at `path`, as
    parse() gives them."""
    with open(path, 'rb') as pac_file:
        return parse(pac_file.read(), fps=fps, code_page=code_page)


# -- Writing -----------------------------------------------------------------

_ENCODED = {}


def _reverse(table):
    """{text: bytes} for a table, its first entry for a text winning."""
    reverse = {}
    for key, char in table.items():
        reverse.setdefault(char, key)
    return reverse


_REVERSE = {name: _reverse(table) for name, table in dict(_TABLES, latin=_LATIN).items()}
_REVERSE_OVERRIDES = {name: _reverse(table) for name, table in _LATIN_OVERRIDES.items()}


def _encode_char(page, char):
    """The bytes `char` takes in `page`, or None when it has no place
    there. Every choice is read back to be sure it reads as `char`."""
    cached = _ENCODED.get((page, char))
    if cached is not None or (page, char) in _ENCODED:
        return cached
    found = None
    if char not in (_ITALIC_ON, _ITALIC_OFF):
        if page in LATIN_PAGES:
            candidates = [_REVERSE_OVERRIDES.get(page, {}).get(char), _REVERSE['latin'].get(char)]
            if 0x20 <= ord(char) < 0x7F:
                candidates.append(bytes([ord(char)]))
            decomposed = unicodedata.normalize('NFD', char)
            accents = {mark: byte for byte, mark in (_TURKISH_ACCENTS if page == 'latin-turkish' else _ACCENTS).items()}
            if len(decomposed) == 2 and decomposed[1] in accents and decomposed[0].isascii():
                candidates.append(bytes([accents[decomposed[1]], ord(decomposed[0])]))
            try:
                single = char.encode(_LATIN_CODECS[page])
                if len(single) == 1 and single[0] >= 0xA0 and single[0] not in _ACCENTS:
                    candidates.append(single)
            except UnicodeEncodeError:
                pass
            read_back = _decode_latin
        elif page in _TABLES:
            candidates = [_REVERSE[page].get(char)]
            if char.isdigit() and char.isascii() or page in _ASCII_PAGES and 0x20 <= ord(char) < 0x7F:
                candidates.append(bytes([ord(char)]))
            if page == 'cyrillic' and char in _CYRILLIC_LOOKALIKES:
                candidates.append(_REVERSE[page].get(_CYRILLIC_LOOKALIKES[char]))
            read_back = _decode_table
        else:
            candidates = []
            try:
                encoded = char.encode(_WIDE_CODECS[page])
                candidates.append(b'\x00' + encoded if len(encoded) == 1 else encoded)
            except UnicodeEncodeError:
                pass
            read_back = None
        for candidate in candidates:
            # Never a byte a reader takes for a code or a line's start.
            if not candidate or len(candidate) == 1 and candidate[0] in _CODES or candidate[0] in (_LINE, 0xFF):
                continue
            if read_back is None:
                found = candidate
                break
            text, unknown = read_back(page, candidate)
            if not unknown and (text == char or page == 'cyrillic' and text == _CYRILLIC_LOOKALIKES.get(char)):
                found = candidate
                break
        if found is None and char in _SUBSTITUTES:
            parts = [_encode_char(page, part) for part in _SUBSTITUTES[char]]
            found = b''.join(parts) if None not in parts else None
    _ENCODED[(page, char)] = found
    return found


def _fits(page, text):
    return all(_encode_char(page, char) is not None for char in text if char not in '\n' + _ITALIC_ON + _ITALIC_OFF)


def _language_page(language, text):
    """The page `language` is written in; for Chinese, Japanese and Korean
    text in no language it names, the page its characters ask for."""
    tag = str(language or '').lower().replace('_', '-')
    primary = tag.split('-')[0]
    if primary in _LANGUAGE_PAGES:
        return _LANGUAGE_PAGES[primary]
    if any(0x3040 <= ord(char) <= 0x30FF for char in text):
        return 'japanese'
    if any(0xAC00 <= ord(char) <= 0xD7A3 for char in text):
        return 'korean'
    if primary in ('zh', 'cmn', 'yue') or any(0x4E00 <= ord(char) <= 0x9FFF for char in text):
        if 'hant' in tag or tag.endswith(('-tw', '-hk', '-mo')):
            return 'chinese-traditional'
        if 'hans' in tag or tag.endswith(('-cn', '-sg')):
            return 'chinese-simplified'
        simplified = sum(1 for char in text if char in _COMMON['chinese-simplified'] - _COMMON['chinese-traditional'])
        traditional = sum(1 for char in text if char in _COMMON['chinese-traditional'] - _COMMON['chinese-simplified'])
        return 'chinese-traditional' if traditional > simplified else 'chinese-simplified'
    return ''


def _candidates(texts, language, preferred):
    return [page for page in (preferred, _language_page(language, '\n'.join(texts))) + CODE_PAGES
            if page in CODE_PAGES]


def _holds_letter(page, char):
    """Whether `page` writes the letter `char`, if only without its accents."""
    stripped = ''.join(c for c in unicodedata.normalize('NFKD', char) if not unicodedata.combining(c))
    return _encode_char(page, char) is not None or bool(stripped) and _fits(page, stripped)


def choose_code_page(texts, language='', preferred=None):
    """The code page to write `texts` in: `preferred` when it holds them,
    else the language's, else the first that holds every character, else
    the first that holds the most of their lines whole (the rest can go in
    a second page), else the one that holds the most of their letters."""
    candidates = _candidates(texts, language, preferred)
    for page in candidates:
        if all(_fits(page, text) for text in texts):
            return page
    whole = {page: sum(1 for text in texts if _fits(page, text)) for page in candidates}
    if max(whole.values()):
        return max(candidates, key=lambda page: whole[page])
    letters = [char for text in texts for char in text if char.isalpha()]
    return max(candidates, key=lambda page: sum(1 for char in letters if _holds_letter(page, char)))


def _subtitle_lines(text):
    """[[(char, Style)]] for each line of a subtitle's text, and its keypad."""
    lead, rest = markup.split_lead(text)
    lines = [[]]
    for run, style in markup.without_readings(markup.spans(rest)):
        for index, part in enumerate(run.replace('\r\n', '\n').replace('\r', '\n').split('\n')):
            if index:
                lines.append([])
            lines[-1].extend((char, style) for char in part if char not in (_ITALIC_ON, _ITALIC_OFF, '\t'))
    out = []
    for line in lines:
        while line and not line[0][0].strip():
            line.pop(0)
        while line and not line[-1][0].strip():
            line.pop()
        if line:
            out.append(line)
    return out, markup.alignment(lead)


def _color_code(color):
    """The colour code nearest `color` ('' is white)."""
    if not color:
        return 0x07
    rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    return min(_COLORS, key=lambda code: sum((a - b) ** 2 for a, b in zip(
        rgb, tuple(int((_COLORS[code] or '#ffffff')[i:i + 2], 16) for i in (1, 3, 5)))))


def _tokens(line):
    """A line as ('text', str), ('mark', '<' or '>') and ('color', code)
    tokens: italics as PAC marks them, each mark in place of the space
    before it; a colour where it changes."""
    tokens, italic, color = [], False, 0x07
    for char, style in line:
        if char.strip():
            wanted = _color_code(markup.color_hex(style.color))
            if style.italic != italic:
                if tokens and tokens[-1] == ('text', ' '):
                    tokens.pop()
                tokens.append(('mark', _ITALIC_ON if style.italic else _ITALIC_OFF))
                italic = style.italic
            if wanted != color:
                # Before the spaces that lead to it, which are no colour.
                spaces = []
                while tokens and tokens[-1] == ('text', ' '):
                    spaces.append(tokens.pop())
                tokens.append(('color', wanted))
                tokens.extend(spaces)
                color = wanted
        tokens.append(('text', char))
    joined = []
    for token in tokens:
        if token[0] == 'text' and joined and joined[-1][0] == 'text':
            joined[-1] = ('text', joined[-1][1] + token[1])
        else:
            joined.append(token)
    return joined


def _encode_text(page, text):
    """The bytes of `text` in a single-byte page: what has no place in it as
    its letter without accents, else "?"."""
    out = []
    index = 0
    while index < len(text):
        found = None
        if page in _TABLES:
            # Two characters may be one in the table (Arabic's lam-alef).
            pair = text[index:index + 2]
            found = _REVERSE[page].get(pair) if len(pair) == 2 else None
            if found:
                out.append(found)
                index += 2
                continue
        char = text[index]
        found = _encode_char(page, char)
        if found is None:
            stripped = ''.join(c for c in unicodedata.normalize('NFKD', char) if not unicodedata.combining(c))
            found = b''.join(_encode_char(page, c) or b'' for c in stripped) or _encode_char(page, '?') or b'?'
        if page == 'arabic' and len(found) == 1 and found[0] in pac_tables.ARABIC_BEFORE_LETTER and out \
                and out[-1][:1] >= b'\x80':
            out.insert(len(out) - 1, found)     # a vowel mark goes before its letter
        else:
            out.append(found)
        index += 1
    return b''.join(out)


def _encode_line(line, page, unicode):
    """A line's bytes after its FE, alignment, 03."""
    tokens = _tokens(line)
    if unicode:
        text = ''.join(value for kind, value in tokens if kind != 'color')
        return _UNICODE_START + text.encode('utf-8').replace(b'.', bytes([_UNICODE_STOP])) + bytes([_UNICODE_END])
    if page in WIDE_PAGES:
        text = ''.join(value for kind, value in tokens if kind != 'color')
        if all(_encode_char('latin', char) is not None for char in text if char not in _ITALIC_ON + _ITALIC_OFF):
            page = 'latin'                      # a line in Latin letters is written as one
        else:
            out = bytearray(_WIDE_START + b'.')
            for char in text:
                out += b'\x00' + char.encode('ascii') if char in (_ITALIC_ON, _ITALIC_OFF) else \
                    _encode_char(page, char) or b'\x00?'
            return bytes(out)
    out = bytearray()
    for kind, value in tokens:
        if kind == 'color':
            out.append(value)
        elif kind == 'mark':
            out += value.encode('ascii')
        else:
            out += _encode_text(page, _mirror(value, page))
    return bytes(out)


def _timecode(seconds, fps):
    """Four bytes for a time: HHMM and SSFF, the frames counting within a
    second of the clock."""
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    frames = int(round((seconds - whole) * fps))
    if frames >= round(fps):
        whole, frames = whole + 1, 0
    minutes, second = divmod(whole, 60)
    hours, minute = divmod(minutes, 60)
    return (min(hours, 654) * 100 + minute).to_bytes(2, 'little') + (second * 100 + frames).to_bytes(2, 'little')


def _row(lines, keypad, place):
    """The row a subtitle's first line goes on: where its vertical margin
    puts it, else where its keypad alone does."""
    count = len(lines)
    side = (keypad - 1) // 3                    # 0 bottom, 1 middle, 2 top
    down = position.as_percent(place.v) if place.v else None
    if down is None:
        row = (ROWS - count, 6, 1)[side]
    else:
        rows = down * ROWS / 100
        row = round((rows, ROWS / 2 + rows - count / 2, ROWS - count - rows)[2 - side])
    return max(0, min(ROWS - 1, ROWS - count, row)) if count <= ROWS else 0


def write(segments, fps=DEFAULT_FPS, code_page='auto', unicode=False, language=''):
    """PAC bytes for `segments` (UniPac's when `unicode`): frames at `fps` a
    second, in `code_page` when it holds the text (else as
    choose_code_page() picks one), lines it does not hold in a second code
    page where one holds them."""
    fps = float(fps or DEFAULT_FPS)
    subtitles = []
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        lines, keypad = _subtitle_lines(str(segment.get('text') or ''))
        if lines:
            subtitles.append((float(segment.get('start', 0)), float(segment.get('end', 0)), lines, keypad,
                              position.of(segment)))
    texts = [''.join(char for char, _style in line) for _s, _e, lines, _k, _p in subtitles for line in lines]
    page = secondary = None
    if not unicode:
        page = choose_code_page(texts, language, code_page if code_page in CODE_PAGES else None)
        misfits = [text for text in texts if not _fits(page, text)]
        if misfits:
            other = choose_code_page(misfits)
            if other != page and all(_fits(other, text) for text in misfits):
                secondary = other

    out = bytearray(_HEADER)
    for number, (start, end, lines, keypad, place) in enumerate(subtitles):
        alignment = 1 if keypad % 3 == 1 else 0 if keypad % 3 == 0 else 2
        body = bytearray([_row(lines, keypad, place)])
        if unicode:
            body += _FPC_LEAD
        for line in lines:
            text = ''.join(char for char, _style in line)
            in_secondary = secondary is not None and not _fits(page, text)
            body += bytes([_LINE, alignment | (_SECONDARY if in_secondary else 0), 0x03])
            body += _encode_line(line, secondary if in_secondary else page, unicode)
        out += b'\x00' + (number % 0x10000).to_bytes(2, 'little') + b'\x60'
        out += _timecode(start, fps) + _timecode(max(start, end), fps) + len(body).to_bytes(2, 'little') + body
    out += _FOOTER
    return bytes(out)


def save(path, segments, **options):
    """Write `segments` to `path` as PAC; `options` as write() takes them."""
    with open(path, 'wb') as pac_file:
        pac_file.write(write(segments, **options))
