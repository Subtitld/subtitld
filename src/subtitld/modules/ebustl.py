"""EBU STL (.stl): read the subtitle files Europe's broadcasters exchange,
write ones their teletext inserters and open-subtitling systems take.

EBU Tech 3264 (1991) is a binary format: a 1024-byte General Subtitle
Information block (GSI: the frame rate, the display, the character set,
the language, the programme's start ...), then a 128-byte Text and Timing
Information block (TTI) for each subtitle: its timecodes in and out, its
row and justification, and 112 bytes of text, in the character set the GSI
names, with teletext's control codes in it.

Reading:

- the frame rate "STL25.01" or "STL30.01" names (and STL23.01, STL24.01
  and STL50.01, as later files have them; STL30.01 is 29.97 a second);
- times from the programme's start (the GSI's TCP) when the file says it
  is in use, as subtitles of a 10:00:00:00 programme start at 0;
- text in ISO 6937 (its accents before their letter), or the Cyrillic,
  Arabic, Greek or Hebrew set the GSI names; a subtitle's extension
  blocks joined; comments and user data left out;
- teletext's colours, and the italics and underline of open subtitling,
  as formatting tags; a line break for any run of CR/LFs; the row
  (against the rows the GSI says it has) and the justification as one of
  the nine places;
- the language the GSI's code names, as ISO 639-1.

Writing gives one TTI block a subtitle (extension blocks when its text
does not fit), in its order of start, and a GSI that counts them: frames
at 25 ("STL25.01") or 30 ("STL30.01", for 29.97 video), from a programme
start of 00:00:00:00 or 10:00:00:00; ISO 6937 unless the text needs
Cyrillic, Greek, Arabic or Hebrew; the language's code; the title. For
teletext (level 1), each line is double height and boxed, as teletext
subtitle pages show only boxed text, with its colour (the box ends with
the row, so a row keeps 36 cells for the text); for open subtitles,
italics, underline and colours as their codes. Rows count up from the
bottom, or down from the top, as the subtitle sits.
"""

import datetime
import re
import struct
import unicodedata

from subtitld.modules import markup

GSI_SIZE, TTI_SIZE, TF_SIZE = 1024, 128, 112
LAST_ROW = 23
# Disk format codes: (timecode frames a second, frames a real second).
_RATES = {'STL23.01': (24, 24000 / 1001), 'STL24.01': (24, 24.0), 'STL25.01': (25, 25.0),
          'STL30.01': (30, 30000 / 1001), 'STL50.01': (50, 50.0)}
RATES = (25, 30)
DISPLAYS = ('teletext', 'open')
STARTS = ('00:00:00:00', '10:00:00:00')

# EBU Tech 3264's language codes, as ISO 639-1.
LANGUAGES = {
    '01': 'sq', '02': 'br', '03': 'ca', '04': 'hr', '05': 'cy', '06': 'cs', '07': 'da', '08': 'de', '09': 'en',
    '0A': 'es', '0B': 'eo', '0C': 'et', '0D': 'eu', '0E': 'fo', '0F': 'fr', '10': 'fy', '11': 'ga', '12': 'gd',
    '13': 'gl', '14': 'is', '15': 'it', '16': 'se', '17': 'la', '18': 'lv', '19': 'lb', '1A': 'lt', '1B': 'hu',
    '1C': 'mt', '1D': 'nl', '1E': 'no', '1F': 'oc', '20': 'pl', '21': 'pt', '22': 'ro', '23': 'rm', '24': 'sr',
    '25': 'sk', '26': 'sl', '27': 'fi', '28': 'sv', '29': 'tr', '2A': 'nl', '2B': 'wa',
    '45': 'zu', '46': 'vi', '47': 'uz', '48': 'ur', '49': 'uk', '4A': 'th', '4B': 'te', '4C': 'tt', '4D': 'ta',
    '4E': 'tg', '4F': 'sw', '51': 'so', '52': 'si', '53': 'sn', '56': 'ru', '57': 'qu', '58': 'ps', '59': 'pa',
    '5A': 'fa', '5D': 'ne', '5F': 'mr', '61': 'ms', '62': 'mg', '63': 'mk', '64': 'lo', '65': 'ko', '66': 'km',
    '67': 'kk', '68': 'kn', '69': 'ja', '6A': 'id', '6B': 'hi', '6C': 'he', '6D': 'ha', '6F': 'gu', '70': 'el',
    '71': 'ka', '75': 'zh', '76': 'my', '77': 'bg', '78': 'bn', '79': 'be', '7B': 'az', '7C': 'as', '7D': 'hy',
    '7E': 'ar', '7F': 'am',
}
_LANGUAGE_CODES = {language: code for code, language in reversed(list(LANGUAGES.items()))}
_LANGUAGE_CODES.update({'nb': '1E', 'nn': '1E', 'cmn': '75', 'yue': '75', 'zh': '75'})

# Character code tables: ISO 6937 for Latin, ISO 8859 for the others.
_CODECS = {'01': 'iso8859_5', '02': 'iso8859_6', '03': 'iso8859_7', '04': 'iso8859_8'}
_ISO6937 = {
    0xA0: '\u00a0', 0xA1: '¡', 0xA2: '¢', 0xA3: '£', 0xA4: '$', 0xA5: '¥', 0xA7: '§', 0xA8: '¤', 0xA9: '‘',
    0xAA: '“', 0xAB: '«', 0xAC: '←', 0xAD: '↑', 0xAE: '→', 0xAF: '↓', 0xB0: '°', 0xB1: '±', 0xB2: '²', 0xB3: '³',
    0xB4: '×', 0xB5: 'µ', 0xB6: '¶', 0xB7: '·', 0xB8: '÷', 0xB9: '’', 0xBA: '”', 0xBB: '»', 0xBC: '¼', 0xBD: '½',
    0xBE: '¾', 0xBF: '¿', 0xD0: '—', 0xD1: '¹', 0xD2: '®', 0xD3: '©', 0xD4: '™', 0xD5: '♪', 0xD6: '¬', 0xD7: '¦',
    0xDC: '⅛', 0xDD: '⅜', 0xDE: '⅝', 0xDF: '⅞', 0xE0: '\u2126', 0xE1: 'Æ', 0xE2: 'Ð', 0xE3: 'ª', 0xE4: 'Ħ', 0xE6: 'Ĳ',
    0xE7: 'Ŀ', 0xE8: 'Ł', 0xE9: 'Ø', 0xEA: 'Œ', 0xEB: 'º', 0xEC: 'Þ', 0xED: 'Ŧ', 0xEE: 'Ŋ', 0xEF: 'ŉ', 0xF0: 'ĸ',
    0xF1: 'æ', 0xF2: 'đ', 0xF3: 'ð', 0xF4: 'ħ', 0xF5: 'ı', 0xF6: 'ĳ', 0xF7: 'ŀ', 0xF8: 'ł', 0xF9: 'ø', 0xFA: 'œ',
    0xFB: 'ß', 0xFC: 'þ', 0xFD: 'ŧ', 0xFE: 'ŋ', 0xFF: '\u00ad',
}
_DIACRITICS = {0xC1: '̀', 0xC2: '́', 0xC3: '̂', 0xC4: '̃', 0xC5: '̄', 0xC6: '̆',
               0xC7: '̇', 0xC8: '̈', 0xCA: '̊', 0xCB: '̧', 0xCD: '̋', 0xCE: '̨',
               0xCF: '̌'}
# A diacritical mark before a space is the mark alone; before "g", the
# acute is how ISO 6937 writes \u0123, its cedilla drawn above.
_SPACING = {0xC1: '`', 0xC2: '\u00b4', 0xC3: '^', 0xC4: '~', 0xC5: '\u00af', 0xC6: '\u02d8', 0xC7: '\u02d9',
            0xC8: '\u00a8', 0xCA: '\u02da', 0xCB: '\u00b8', 0xCD: '\u02dd', 0xCE: '\u02db', 0xCF: '\u02c7'}
_PAIRS = {(0xC2, ord('g')): '\u0123'}
_TO_ISO6937 = {char: byte for byte, char in _ISO6937.items() if char not in ('$', '\u00a0')}
_TO_ISO6937.update({'\u0110': 0xE2, '\u2015': 0xD0, '\u03a9': 0xE0})
_TO_DIACRITIC = {mark: byte for byte, mark in _DIACRITICS.items()}
_TO_PAIR = {char: bytes(pair) for pair, char in _PAIRS.items()}
_TO_PAIR.update({char: bytes([byte, 0x20]) for byte, char in _SPACING.items() if char not in '`^~'})

# Teletext's alphanumeric colours.
_COLORS = {0x00: '#000000', 0x01: '#ff0000', 0x02: '#00ff00', 0x03: '#ffff00', 0x04: '#0000ff', 0x05: '#ff00ff',
           0x06: '#00ffff', 0x07: ''}
_CR_LF, _UNUSED = 0x8A, 0x8F
_ITALIC_ON, _ITALIC_OFF, _UNDERLINE_ON, _UNDERLINE_OFF = 0x80, 0x81, 0x82, 0x83
_DOUBLE_HEIGHT, _START_BOX = 0x0D, 0x0B
_CLOSING = ',.;:!?)]}’”»…'


def _decode_6937(data):
    out = []
    index = 0
    while index < len(data):
        byte = data[index]
        if byte in _DIACRITICS and index + 1 < len(data):
            following = data[index + 1]
            if (byte, following) in _PAIRS:
                out.append(_PAIRS[(byte, following)])
            elif following == 0x20:
                out.append(_SPACING[byte])
            else:
                base = chr(following) if 0x20 <= following < 0x7F else _ISO6937.get(following, '')
                out.append(unicodedata.normalize('NFC', base + _DIACRITICS[byte]) if base else '')
            index += 2
            continue
        out.append(chr(byte) if 0x20 <= byte < 0x7F else _ISO6937.get(byte, ''))
        index += 1
    return ''.join(out)


def _decoder(cct):
    codec = _CODECS.get(cct)
    if codec:
        return lambda data: data.decode(codec, 'replace')
    return _decode_6937


def _is_character(byte):
    return 0x20 <= byte <= 0x7F or 0xA0 <= byte <= 0xFF


def _text(field, decode, teletext):
    """Text with tags for a text field."""
    plain = markup.PLAIN
    style = plain
    runs, pending = [], bytearray()

    def flush():
        if pending:
            runs.append((decode(bytes(pending)), style))
            pending.clear()

    previous = None
    for index, byte in enumerate(field):
        if byte == _UNUSED:
            break
        if _is_character(byte):
            pending.append(byte)
        elif byte == _CR_LF:
            flush()
            if previous != _CR_LF:
                runs.append(('\n', plain))
            if teletext:
                style = plain                       # a teletext row starts white
        else:
            flush()
            before = style
            if byte in _COLORS:
                style = style._replace(color=_COLORS[byte])
            elif byte == _ITALIC_ON:
                style = style._replace(italic=True)
            elif byte == _ITALIC_OFF:
                style = style._replace(italic=False)
            elif byte == _UNDERLINE_ON:
                style = style._replace(underline=True)
            elif byte == _UNDERLINE_OFF:
                style = style._replace(underline=False)
            # A code takes a cell, shown as a space between letters, with what
            # the styles either side of it share.
            if index + 1 < len(field) and _is_character(field[index + 1]) and \
                    previous is not None and _is_character(previous):
                runs.append((' ', markup.Style(*((a if a == b else '') if isinstance(a, str) else a and b
                                                 for a, b in zip(before, style)))))
        previous = byte
    flush()
    return markup.tidy(runs)


def _frames(hours, minutes, seconds, frames, nominal):
    return ((hours * 60 + minutes) * 60 + seconds) * nominal + frames


def parse(data):
    """(subtitles, {'language', 'fps', 'display', 'start'}) in EBU STL
    bytes. Raises ValueError for bytes that are not EBU STL."""
    if len(data) < GSI_SIZE:
        raise ValueError('Not an EBU STL file: it is too short to hold its header')
    gsi = data[:GSI_SIZE]
    dfc = gsi[3:11].decode('ascii', 'replace')
    if dfc not in _RATES:
        raise ValueError(f'Not an EBU STL file: its disk format code is "{dfc.strip()}", not STL25.01 or STL30.01')
    nominal, rate = _RATES[dfc]
    teletext = gsi[11:12] in (b'1', b'2')
    cct = gsi[12:14].decode('ascii', 'replace')
    decode = _decoder(cct)
    try:
        rows = int(gsi[253:255]) or LAST_ROW
    except ValueError:
        rows = LAST_ROW
    start = 0
    if gsi[255:256] == b'1' and re.fullmatch(rb'\d{8}', gsi[256:264]):
        tcp = gsi[256:264]
        start = _frames(int(tcp[0:2]), int(tcp[2:4]), int(tcp[4:6]), int(tcp[6:8]), nominal)

    entries, field = [], b''
    for offset in range(GSI_SIZE, len(data) - TTI_SIZE + 1, TTI_SIZE):
        (_sgn, _sn, ebn, _cs, tci_h, tci_m, tci_s, tci_f, tco_h, tco_m, tco_s, tco_f, vp, jc, cf, tf) = \
            struct.unpack('<BHBBBBBBBBBBBBB112s', data[offset:offset + TTI_SIZE])
        if 0xEF < ebn < 0xFF:
            continue                                # user data, reserved
        field += tf.strip(bytes([_UNUSED]))
        if ebn != 0xFF:
            continue                                # more of this subtitle's text to come
        text, field = field, b''
        if cf:
            continue                                # a comment
        entries.append((_frames(tci_h, tci_m, tci_s, tci_f, nominal), _frames(tco_h, tco_m, tco_s, tco_f, nominal),
                        vp, jc, text))
    # Times from the programme's start, when the subtitles come after it.
    if entries and min(entry[0] for entry in entries) < start:
        start = 0
    segments = []
    for tci, tco, vp, jc, text in entries:
        body = _text(text, decode, teletext)
        if not markup.plain(body).strip():
            continue
        lines = body.count('\n') + 1
        height = 2 if teletext and _DOUBLE_HEIGHT in text else 1
        middle = (vp + vp + lines * height - 1) / 2 / rows
        vertical = 2 if middle < 1 / 3 else 0 if middle > 2 / 3 else 1
        horizontal = 0 if jc == 1 else 2 if jc == 3 else 1
        keypad = (1, 4, 7)[vertical] + horizontal
        begin = round((tci - start) / rate, 3)
        segments.append({'start': begin, 'end': max(begin, round((tco - start) / rate, 3)),
                         'text': (markup.with_alignment('', keypad) if keypad != 2 else '') + body})
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    language = LANGUAGES.get(gsi[14:16].decode('ascii', 'replace').upper(), '')
    return segments, {'language': language, 'fps': nominal, 'display': 'teletext' if teletext else 'open',
                      'start': STARTS[1] if start == _frames(10, 0, 0, 0, nominal) else STARTS[0]}


def read(path):
    """(subtitles, its settings) in the EBU STL file at `path`, as parse()
    gives them."""
    with open(path, 'rb') as stl_file:
        return parse(stl_file.read())


# Writing.
def _encode_6937(char):
    """ISO 6937 bytes for a character, or None."""
    if 0x20 <= ord(char) < 0x7F:
        return bytes([ord(char)])
    if char in _TO_PAIR:
        return _TO_PAIR[char]
    if char in _TO_ISO6937:
        return bytes([_TO_ISO6937[char]])
    decomposed = unicodedata.normalize('NFD', char)
    if len(decomposed) == 2 and decomposed[1] in _TO_DIACRITIC and 0x20 <= ord(decomposed[0]) < 0x7F:
        return bytes([_TO_DIACRITIC[decomposed[1]], ord(decomposed[0])])
    return None


def _character_table(texts):
    """The CCT that holds every character of `texts`: ISO 6937's Latin, else
    the first ISO 8859 set that does."""
    chars = {char for text in texts for char in text if char not in '\n'}
    if all(_encode_6937(char) is not None for char in chars):
        return '00'
    for cct in ('01', '03', '02', '04'):
        try:
            ''.join(chars).encode(_CODECS[cct])
            return cct
        except UnicodeEncodeError:
            continue
    return '00'


def _encoder(cct):
    if cct in _CODECS:
        codec = _CODECS[cct]
        return lambda char: char.encode(codec, 'replace')
    return lambda char: _encode_6937(char) or (
        b''.join(_encode_6937(c) or b'' for c in unicodedata.normalize('NFKD', char) if not unicodedata.combining(c)) or b'?')


def _color_code(color):
    """The teletext colour nearest `color` ('' is white)."""
    if not color:
        return 0x07
    rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    return min((code for code in range(1, 8)), key=lambda code: sum(
        (a - b) ** 2 for a, b in zip(rgb, tuple(int((_COLORS[code] or '#ffffff')[i:i + 2], 16) for i in (1, 3, 5)))))


def _lines(text):
    """[[char, Style]] for each line of a subtitle's text, punctuation
    against a word in its style, so that no code comes between them."""
    lead, rest = markup.split_lead(text)
    lines = [[]]
    for run, style in markup.spans(rest):
        for index, part in enumerate(run.split('\n')):
            if index:
                lines.append([])
            lines[-1].extend([char, style] for char in part)
    out = []
    for line in lines:
        while line and not line[0][0].strip():
            line.pop(0)
        while line and not line[-1][0].strip():
            line.pop()
        for index in range(1, len(line)):
            if line[index][0] in _CLOSING and line[index - 1][0].strip():
                line[index][1] = line[index - 1][1]
        if line:
            out.append(line)
    return out, markup.alignment(lead)


def _field(lines, encode, teletext):
    """The text field's bytes for the lines of a subtitle."""
    field = bytearray()
    for number, line in enumerate(lines):
        if number:
            field += bytes([_CR_LF, _CR_LF] if teletext else [_CR_LF])
        color = _color_code(line[0][1].color)
        if teletext:
            field += bytes([_DOUBLE_HEIGHT, color, _START_BOX, _START_BOX])
        elif color != 0x07:
            field.append(color)
        style = line[0][1]
        if not teletext:
            field += bytes(([_ITALIC_ON] if style.italic else []) + ([_UNDERLINE_ON] if style.underline else []))
        for char, char_style in line:
            if char.strip():
                codes = []
                if _color_code(char_style.color) != color:
                    color = _color_code(char_style.color)
                    codes.append(color)
                if not teletext:
                    for on, off, field_name in ((_ITALIC_ON, _ITALIC_OFF, 'italic'),
                                                (_UNDERLINE_ON, _UNDERLINE_OFF, 'underline')):
                        if getattr(char_style, field_name) != getattr(style, field_name):
                            codes.append(on if getattr(char_style, field_name) else off)
                    style = char_style
                # A code takes a cell, which readers show as a space: in place
                # of the space before it, where there is one.
                if codes and field and field[-1] == 0x20:
                    field.pop()
                field += bytes(codes)
            field += encode(char)
        if not teletext:
            field += bytes(([_ITALIC_OFF] if style.italic else []) + ([_UNDERLINE_OFF] if style.underline else []))
    return bytes(field)


def _timecode(frames, nominal):
    seconds, frame = divmod(max(0, frames), nominal)
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return min(hours, 99), minute, second, frame


def _gsi_field(value, size, encoding='cp850'):
    return str(value).encode(encoding, 'replace')[:size].ljust(size, b' ')


def write(segments, fps=25, display='teletext', start='00:00:00:00', language='', title=''):
    """EBU STL bytes for `segments`: frames at 25 or 30 (29.97) a second,
    for teletext or open subtitles, from a programme start of `start`
    ("HH:MM:SS:FF")."""
    nominal = 30 if int(round(float(fps))) == 30 else 25
    dfc = f'STL{nominal}.01'
    rate = _RATES[dfc][1]
    teletext = display != 'open'
    hours, minutes, seconds, frames = (int(part) for part in re.findall(r'\d+', start)[:4] or (0, 0, 0, 0))
    offset = _frames(hours, minutes, seconds, frames, nominal)

    subtitles = []
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        lines, keypad = _lines(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
        if lines:
            begin = round(float(segment.get('start', 0)) * rate)
            subtitles.append((begin, max(begin, round(float(segment.get('end', 0)) * rate)), lines, keypad))
    cct = _character_table(''.join(char for _b, _e, lines, _k in subtitles for line in lines for char, _s in line))
    encode = _encoder(cct)

    blocks = []
    for number, (begin, end, lines, keypad) in enumerate(subtitles, start=1):
        height = 2 if teletext else 1
        rows = len(lines) * height
        # The rows it takes: the last two (or one) of 23 at the bottom, from
        # the first at the top.
        if keypad >= 7:
            vp = 1
        elif keypad >= 4:
            vp = max(1, (LAST_ROW - rows) // 2 + 1)
        else:
            vp = max(1, LAST_ROW + 1 - rows)
        jc = 1 if keypad % 3 == 1 else 3 if keypad % 3 == 0 else 2
        field = _field(lines, encode, teletext)
        chunks = [field[i:i + TF_SIZE] for i in range(0, len(field), TF_SIZE)] or [b'']
        tci, tco = _timecode(begin + offset, nominal), _timecode(end + offset, nominal)
        for index, chunk in enumerate(chunks):
            ebn = 0xFF if index == len(chunks) - 1 else index
            blocks.append(struct.pack('<BHBBBBBBBBBBBBB112s', 0, number % 65536, ebn, 0, *tci, *tco, vp, jc, 0,
                                      chunk.ljust(TF_SIZE, bytes([_UNUSED]))))

    today = datetime.date.today().strftime('%y%m%d')
    first = _timecode(subtitles[0][0] + offset, nominal) if subtitles else _timecode(offset, nominal)
    primary = str(language or '').replace('_', '-').split('-')[0].lower()
    fields = [
        ('850', 3), (dfc, 8), ('1' if teletext else '0', 1), (cct, 2), (_LANGUAGE_CODES.get(primary, '00'), 2),
        (title, 32), ('', 32), ('', 32), ('', 32), ('', 32), ('', 32), ('', 16), (today, 6), (today, 6), ('00', 2),
        (f'{len(blocks):05d}', 5), (f'{len(subtitles):05d}', 5), ('001', 3), ('40', 2), (f'{LAST_ROW:02d}', 2),
        ('1', 1), ('%02d%02d%02d%02d' % _timecode(offset, nominal), 8), ('%02d%02d%02d%02d' % first, 8),
        ('1', 1), ('1', 1), ('', 3), ('', 32), ('', 32), ('', 32), ('', 75), ('', 576),
    ]
    gsi = b''.join(_gsi_field(value, size) for value, size in fields)
    assert len(gsi) == GSI_SIZE
    return gsi + b''.join(blocks)


def save(path, segments, **options):
    """Write `segments` to `path` as EBU STL; `options` as write() takes them."""
    with open(path, 'wb') as stl_file:
        stl_file.write(write(segments, **options))
