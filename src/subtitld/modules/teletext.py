"""Teletext subtitles: the pages of EBU Teletext (EN 300 706) that DVB
broadcasts carry in an MPEG transport stream (EN 300 472), read as
subtitles, and written into one.

A teletext service is a stream of 45-byte packets, each addressed to a
magazine (the hundreds of a page number, 1 to 8) and a row: a page header
(row 0) names the page that the rows after it belong to, until the next
header in its magazine (or any header, in a service sent "serially").
Subtitles are on pages of their own (888, 777 ...) with the header's
Subtitle bit set: the decoder shows only the text between Start Box codes,
boxed over the picture. A new subtitle is a new transmission of the page,
its header's Erase Page bit set and its rows after it; one with no rows
takes the subtitle away. So a subtitle is shown from the header that
brings it until the header that erases or replaces it, and those times
are the presentation times (PTS) of the PES packets that carry them.

In a transport stream, the teletext stream is a PES stream of private data
(stream_type 6) whose descriptor in the PMT (EN 300 468's teletext
descriptor, 0x56) lists its pages: each one's language, magazine, number
and type (2 subtitles, 5 subtitles for the hard of hearing). Each PES
packet carries data units of 44 bytes, 0x02 for teletext and 0x03 for
teletext subtitles, each a teletext packet without its clock run-in, its
bits in the order they go over the air (least significant first), so each
byte is reversed before it is read.

What is read of a page, in the bytes the specification gives:

- Hamming 8/4 for the addresses and the header's control bits, Hamming
  24/18 for the enhancement triplets, one error put right and two found;
  odd parity for the characters, a character failing it read as a space;
- the G0 set the header's C12 to C14 bits choose among those of the group
  a packet X/28/0 or M/29/0 designates (table 32) or, without one, the
  group the page's language belongs to: Latin with a national option
  sub-set (Polish, Turkish, the Balkan and the Baltic languages have
  groups of their own), Cyrillic (Serbian and Macedonian, Russian and
  Bulgarian, Ukrainian), Greek, Arabic or Hebrew, with the G2 set that
  goes with it;
- characters of the G2 set and letters of the G0 set with a diacritical
  mark placed over the page by packet X/26 (Level 1.5), as broadcasters
  write what the G0 set lacks, and the G0 and G2 sets a triplet
  designates for those after it;
- Arabic and Hebrew, which sit on the page from the right, in the order
  they are read, Arabic's letters as themselves rather than the form
  each takes beside the others;
- the alphanumeric colours, a background given by New Background, Flash,
  the boxing of a subtitle page and double height (the row under one is
  not read);
- where a subtitle sits: at the top, middle or bottom of the page by the
  rows it is on, and to the left, middle or right by its columns.

Mosaic characters are spaces.

Writing gives a transport stream with one teletext stream: a page for each
language, 888 and up, each subtitle double height and boxed in its
colours, centred on the bottom rows (or the top, for one placed there),
erased when it ends. A page is written in the set its language has, or
the first that holds its text (Serbian in Latin or Cyrillic), designated
by packet X/28/0 but for western Europe's Latin, which needs none; what
the G0 set lacks is placed by X/26 from the G2 set or as a letter with a
mark, a Latin capital the Cyrillic and Greek sets have no place for as
the letter that looks the same. Arabic is shaped, each letter in the form
the sets have nearest the one it takes, and Arabic and Hebrew put on the
page from the right. `write_video` has FFmpeg copy that stream into a
transport stream of the video, its picture and sound copied as well.

The same teletext packets go in each frame's ancillary data as OP-47
(SMPTE RDD 8), the way an MXF carries subtitles beside its captions (SMPTE
ST 436) and an MCC file writes them: `op47_packet` and `op47_fields`
write and read a frame's subtitling distribution packet, `op47_frames`
spreads a page's transmissions over the frames, five packets a frame, and
`op47_decode` reads them back as subtitles.
"""

import functools
import os
import subprocess
import tempfile
import unicodedata

from subtitld.modules import ebustl
from subtitld.modules import markup

PACKET = 188
# The descriptor's teletext types that are subtitles: 2, and 5 for the
# hard of hearing.
SUBTITLE_TYPES = frozenset({0x02, 0x05})
DEFAULT_PAGE = 888
_PTS_WRAP = 1 << 33

# The bytes of DVB's teletext units are in the order they go over the air.
_REVERSED = bytes(int(f'{byte:08b}'[::-1], 2) for byte in range(256))


# --- Error protection -------------------------------------------------------

def ham84_encode(nibble):
    """The Hamming 8/4 byte for a value of 4 bits (EN 300 706 8.2)."""
    d1, d2, d3, d4 = ((nibble >> bit) & 1 for bit in range(4))
    p1 = 1 ^ d1 ^ d3 ^ d4
    p2 = 1 ^ d1 ^ d2 ^ d4
    p3 = 1 ^ d1 ^ d2 ^ d3
    p4 = 1 ^ p1 ^ d1 ^ p2 ^ d2 ^ p3 ^ d3 ^ d4
    bits = (p1, d1, p2, d2, p3, d3, p4, d4)
    return sum(bit << index for index, bit in enumerate(bits))


def _ham84_table():
    """Each byte's 4 bits, or -1: a byte one bit away from a code is that
    code (one error put right); two away from every code, two errors."""
    codes = [ham84_encode(nibble) for nibble in range(16)]
    table = []
    for byte in range(256):
        found = [nibble for nibble, code in enumerate(codes) if bin(code ^ byte).count('1') <= 1]
        table.append(found[0] if found else -1)
    return table


_HAM84 = _ham84_table()


def ham84(byte):
    """The 4 bits of a Hamming 8/4 byte, or None for one with two errors."""
    value = _HAM84[byte]
    return None if value < 0 else value


# Where the 18 data bits of a Hamming 24/18 triplet are, counted from 1 as
# the bits go over the air: the others are its protection bits.
_DATA_POSITIONS = (3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 17, 18, 19, 20, 21, 22, 23)


def ham2418_encode(value):
    """The three bytes of a Hamming 24/18 triplet for 18 bits (8.3)."""
    bits = [0] * 25
    for index, position in enumerate(_DATA_POSITIONS):
        bits[position] = (value >> index) & 1
    for test in range(5):
        check = 1 << test
        bits[check] = 1 ^ (sum(bits[position] for position in range(1, 24)
                               if position & check and position != check) & 1)
    bits[24] = 1 ^ (sum(bits[1:24]) & 1)
    word = sum(bits[position] << (position - 1) for position in range(1, 25))
    return bytes((word & 0xFF, (word >> 8) & 0xFF, (word >> 16) & 0xFF))


def ham2418(data):
    """The 18 bits of a Hamming 24/18 triplet's three bytes, or None for
    one with two errors. A failed test counts the position of the bit in
    error (8.3, note 2)."""
    word = data[0] | (data[1] << 8) | (data[2] << 16)
    bits = [0] + [(word >> index) & 1 for index in range(24)]
    syndrome = 0
    for test in range(5):
        check = 1 << test
        if not sum(bits[position] for position in range(1, 24) if position & check) & 1:
            syndrome |= check
    whole = sum(bits[1:25]) & 1
    if syndrome:
        if whole:                                   # odd overall: two errors
            return None
        if syndrome < 24:
            bits[syndrome] ^= 1
    value = 0
    for index, position in enumerate(_DATA_POSITIONS):
        value |= bits[position] << index
    return value


def parity_encode(code):
    """A 7-bit character with its odd parity bit."""
    code &= 0x7F
    return code | (0x80 if not bin(code).count('1') & 1 else 0)


def parity(byte):
    """The 7 bits of an odd parity byte, or None when it fails."""
    return byte & 0x7F if bin(byte).count('1') & 1 else None


# --- Characters --------------------------------------------------------------

# The places in the G0 set a national option sub-set takes (table 36), and
# each sub-set's characters for them, in that order.
NATIONAL_POSITIONS = (0x23, 0x24, 0x40, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F, 0x60, 0x7B, 0x7C, 0x7D, 0x7E)
SUBSETS = {
    'english': '£$@←½→↑#―¼‖¾÷',
    'german': '#$§ÄÖÜ^_°äöüß',
    'swedish': '#¤ÉÄÖÅÜ_éäöåü',                    # Swedish, Finnish, Hungarian
    'italian': '£$é°ç→↑#ùàòèì',
    'french': 'éïàëêùî#èâôûç',
    'spanish': 'ç$¡áéíóú¿üñèà',                    # Portuguese and Spanish
    'czech': '#ůčťžýířéáěúš',                       # Czech and Slovak
    'polish': '#ńąƵŚŁćóężśłź',
    'turkish': '₺ğİŞÖÇÜĞışöçü',
    'serbian': '#ËČĆŽĐŠëčćžđš',                     # Serbian, Croatian, Slovenian
    'romanian': '#¤ŢÂŞĂÎıţâşăî',
    'estonian': '#õŠÄÖŽÜÕšäöžü',
    'lettish': '#$ŠėęŽčūšąųžį',                     # Latvian and Lithuanian
}
# The G0 sets other than Latin (15.6.4 to 15.6.12), 2/0 to 7/F. They have
# no national option sub-sets: a page in one of them has its own G0 set.
_ASCII = ' !"#$%&\'()*+,-./0123456789:;<=>?'
_CYRILLIC_LOWER = 'юабцдефгхийклмнопярстужвьъзшэщч■'
G0_SETS = {
    'cyrillic-1': _ASCII + 'ЧАБЦДЕФГХИЈКЛМНОПЌРСТУВЃЉЊЗЋЖЂШЏчабцдефгхијклмнопќрстувѓљњзћжђш■',
    # 4/A is drawn Й, with a mark libzvbi takes for Ѝ's grave: Russian and
    # Bulgarian have no other place for Й.
    'cyrillic-2': _ASCII[:6] + 'ы' + _ASCII[7:] + 'ЮАБЦДЕФГХИЙКЛМНОПЯРСТУЖВЬЪЗШЭЩЧЫ' + _CYRILLIC_LOWER,
    'cyrillic-3': _ASCII[:6] + 'ї' + _ASCII[7:] + 'ЮАБЦДЕФГХИЙКЛМНОПЯРСТУЖВЬІЗШЄЩЧЇ'
                  + 'юабцдефгхийклмнопярстужвьізшєщч■',
    'greek': _ASCII[:28] + '«=»?' + 'ΐΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡ\u0374ΣΤΥΦΧΨΩΪΫάέήίΰαβγδεζηθικλμνξοπρςστυφχψωϊϋόύώ■',
    # Arabic is drawn as it is shown, each letter in the form it takes
    # beside the others (Unicode's presentation forms); 2/8 and 2/9, 3/C
    # and 3/E are mirrored, as the text runs from the right. 2/6 is a tail
    # that ends a letter, read as nothing (see _TAIL).
    'arabic': ' !"£$%ـﻲ)(*+،-./0123456789:؛>=<؟'
              'ﺔﺀﺒﺏﺘﺕﺎﺍﺑﺓﺗﺛﺟﺣﺧﺩﺫﺭﺯﺳﺷﺻﺿﻃﻇﻋﻏﺜﺠﺤﺨ#ـﻓﻗﻛﻟﻣﻧﻫﻭﻰﻳﺙﺝﺡﺥﻴﻯﻌﻐﻔﻑﻘﻕﻙﻠﻝﻤﻡﻨﻥﻻ■',
    'hebrew': _ASCII[:3] + '£' + _ASCII[4:] + '@ABCDEFGHIJKLMNOPQRSTUVWXYZ←½→↑#אבגדהוזחטיךכלםמןנסעףפץצקרשת₪‖¾÷■',
}
_TAIL = ('arabic', 0x26)
# The sets whose text runs from right to left.
RIGHT_TO_LEFT = frozenset({'arabic', 'hebrew'})

# Table 32: the G0 set or Latin national option sub-set each value of C12,
# C13 and C14 (read as a number, C12 the highest bit) chooses in each group
# the 4 bits above them designate. None is a place the table leaves empty.
GROUPS = {
    0: ('english', 'german', 'swedish', 'italian', 'french', 'spanish', 'czech', None),
    1: ('polish', 'german', 'swedish', 'italian', 'french', None, 'czech', None),
    2: ('english', 'german', 'swedish', 'italian', 'french', 'spanish', 'turkish', None),
    3: (None, None, None, None, None, 'serbian', None, 'romanian'),
    4: ('cyrillic-1', 'german', 'estonian', 'lettish', 'cyrillic-2', 'cyrillic-3', 'czech', None),
    6: (None, None, None, None, None, None, 'turkish', 'greek'),
    8: ('english', None, None, None, 'french', None, None, 'arabic'),
    10: (None, None, None, None, None, 'hebrew', None, 'arabic'),
}
# The group a page in a language is in, when no packet designates one.
_LANGUAGE_GROUPS = {'pl': 1, 'tr': 2, 'sr': 3, 'hr': 3, 'sl': 3, 'bs': 3, 'ro': 3, 'et': 4, 'lv': 4, 'lt': 4,
                    'ru': 4, 'bg': 4, 'uk': 4, 'be': 4, 'mk': 4, 'el': 6, 'ar': 8, 'fa': 8, 'ur': 8, 'he': 10,
                    'yi': 10}
# ISO 639-2 codes a descriptor gives, as their ISO 639-1 ones.
_TWO_LETTERS = {'pol': 'pl', 'tur': 'tr', 'srp': 'sr', 'scc': 'sr', 'hrv': 'hr', 'scr': 'hr', 'slv': 'sl',
                'bos': 'bs', 'ron': 'ro', 'rum': 'ro', 'est': 'et', 'lav': 'lv', 'lit': 'lt', 'eng': 'en',
                'ger': 'de', 'deu': 'de', 'swe': 'sv', 'fin': 'fi', 'hun': 'hu', 'ita': 'it', 'fre': 'fr',
                'fra': 'fr', 'por': 'pt', 'spa': 'es', 'cze': 'cs', 'ces': 'cs', 'slo': 'sk', 'slk': 'sk',
                'dut': 'nl', 'nld': 'nl', 'dan': 'da', 'nor': 'no', 'nob': 'no', 'cat': 'ca', 'rus': 'ru',
                'bul': 'bg', 'ukr': 'uk', 'bel': 'be', 'mac': 'mk', 'mkd': 'mk', 'gre': 'el', 'ell': 'el',
                'ara': 'ar', 'per': 'fa', 'fas': 'fa', 'urd': 'ur', 'heb': 'he', 'yid': 'yi'}
# The sets (group, C12-C14 value) a page in a language is written with, the
# first that holds its text: Serbian is written in Latin or in Cyrillic.
_WRITING = {'en': [(0, 0)], 'de': [(0, 1)], 'sv': [(0, 2)], 'fi': [(0, 2)], 'hu': [(0, 2)], 'it': [(0, 3)],
            'fr': [(0, 4)], 'pt': [(0, 5)], 'es': [(0, 5)], 'ca': [(0, 5)], 'cs': [(0, 6)], 'sk': [(0, 6)],
            'pl': [(1, 0)], 'tr': [(2, 6)], 'sr': [(3, 5), (4, 0)], 'hr': [(3, 5)], 'sl': [(3, 5)],
            'bs': [(3, 5), (4, 0)], 'ro': [(3, 7)], 'et': [(4, 2)], 'lv': [(4, 3)], 'lt': [(4, 3)],
            'mk': [(4, 0)], 'ru': [(4, 4)], 'bg': [(4, 4)], 'uk': [(4, 5)], 'be': [(4, 5), (4, 4)],
            'el': [(6, 7)], 'ar': [(8, 7)], 'fa': [(8, 7)], 'ur': [(8, 7)], 'he': [(10, 5)], 'yi': [(10, 5)]}
# Every set a page can be written with, in the order one is looked for
# when the language's own does not hold the text.
_DESIGNATIONS = [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (0, 6), (1, 0), (2, 6), (3, 5), (3, 7), (4, 2),
                 (4, 3), (4, 4), (4, 5), (4, 0), (6, 7), (8, 7), (10, 5)]

# The Latin G2 set (table 37) is ISO 6937's upper half, but for the euro at
# 5/6 and a few others.
_G2 = {code: ebustl._ISO6937.get(code | 0x80, '') for code in range(0x20, 0x80)}
_G2.update({0x20: ' ', 0x24: '$', 0x26: '#', 0x56: '€', 0x57: '‰', 0x58: 'ɑ', 0x7F: '■'})
# Its column 4: the diacritical marks a G0 letter takes from packet X/26,
# the same in the Cyrillic and Greek G2 sets.
_MARKS = {code & 0x0F: mark for code, mark in ebustl._DIACRITICS.items()}
_FROM_MARK = {mark: number for number, mark in _MARKS.items()}
_COLUMN_4 = {0x40 | number: '' for number in range(16)}


def _g2_set(text, marks=True):
    """A G2 set as {code: char} from its characters from 2/0, '·' for a
    place that shows nothing; with `marks`, column 4 is the diacritical
    marks, which are no character of their own."""
    table = {0x20 + index: '' if char == '·' else char for index, char in enumerate(text)}
    if marks:
        table.update(_COLUMN_4)
    return table


# The Cyrillic, Greek and Arabic G2 sets (tables 41, 43 and 45). Arabic's
# has letters in column 4, not marks.
G2_SETS = {
    'latin': _G2,
    'cyrillic': _g2_set(' ¡¢£$¥·§·‘“«←↑→↓°±²³×µ¶·÷’”»¼½¾¿' + '·' * 16 + '—¹®©™♪€‰ɑŁłß⅛⅜⅝⅞'
                        'DEFGIJKLNQRSUVWZdefgijklnqrsuvwz'),
    'greek': _g2_set(' ab£ehi§:‘“k←↑→↓°±²³xmnp÷’”t¼½¾x' + '·' * 16 + '?¹®©™♪€‰ɑΊΎΏ⅛⅜⅝⅞'
                     'CDFGJLQRSUVWYZΆΉcdfgjlqrsuvwyzΈ■'),
    'arabic': _g2_set(' ﻉﺁﺃﺅﺇﺋﺊﭼﭽﭺﭘﭙﭖﮊﮔ٠١٢٣٤٥٦٧٨٩ﻎﻍﻼﻬﻪﻩàABCDEFGHIJKLMNOPQRSTUVWXYZëêùîﻊéabcdefghijklmnopqrstuvwxyzâôûç·', marks=False),
}
_G2_OF = {'cyrillic-1': 'cyrillic', 'cyrillic-2': 'cyrillic', 'cyrillic-3': 'cyrillic', 'greek': 'greek',
          'arabic': 'arabic', 'hebrew': 'arabic'}

# Teletext's alphanumeric colours, white being a subtitle's own (''); as
# a background, black is the box's own.
COLORS = {0: '#000000', 1: '#ff0000', 2: '#00ff00', 3: '#ffff00', 4: '#0000ff', 5: '#ff00ff', 6: '#00ffff', 7: ''}
BACKGROUNDS = {**COLORS, 0: '', 7: '#ffffff'}
_NEAREST = {color: code for code, color in COLORS.items()}

# Spacing attributes (12.2), the ones read.
_FLASH, _STEADY, _END_BOX, _START_BOX, _NORMAL, _DOUBLE_HEIGHT = 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D
_DOUBLE_SIZE, _CONCEAL, _BLACK_BACKGROUND, _NEW_BACKGROUND = 0x0F, 0x18, 0x1C, 0x1D


def language_code(tag):
    """The ISO 639-1 code for a language a descriptor or Subtitld names
    ('fre', 'pt-br'), or ''."""
    primary = str(tag or '').replace('_', '-').split('-')[0].lower()
    return _TWO_LETTERS.get(primary, primary if len(primary) == 2 else '')


def subset_of(group, option):
    """The G0 set for a group and C12-C14 value: a Latin national option
    sub-set's name ('german') or another G0 set's ('cyrillic-2'); English
    for a place table 32 leaves empty."""
    return GROUPS.get(group, GROUPS[0])[option & 7] or 'english'


def g2_of(group, option):
    """The G2 set's name ('latin', 'cyrillic', 'greek', 'arabic') that goes
    with a group and C12-C14 value: Arabic's, too, for the Latin sets of the
    Arabic group."""
    subset = subset_of(group, option)
    return _G2_OF.get(subset, 'arabic' if group == 8 else 'latin')


def g0_char(code, subset='english'):
    """The character of a 7-bit code in the G0 set `subset`: the Latin one
    with that national option sub-set, or another G0 set."""
    if subset in G0_SETS:
        if (subset, code) == _TAIL:
            return ''
        return G0_SETS[subset][code - 0x20] if 0x20 <= code < 0x80 else ' '
    if code in NATIONAL_POSITIONS:
        return SUBSETS.get(subset, SUBSETS['english'])[NATIONAL_POSITIONS.index(code)]
    if code == 0x7F:
        return '■'                             # a block, the whole cell
    return chr(code) if 0x20 <= code < 0x7F else ' '


def _plain_char(code, subset='english'):
    """A G0 character as an X/26 triplet addresses it: without the national
    option sub-set (15.4, note)."""
    if subset in G0_SETS:
        return g0_char(code, subset)
    return '■' if code == 0x7F else chr(code) if 0x20 <= code < 0x7F else ' '


def _shown(char):
    """A character as Subtitld keeps it: Arabic's presentation forms as the
    letters they are (a lam-alef as its two), the rest as it is."""
    if char and ('ﭐ' <= char <= '﷿' or 'ﹰ' <= char <= '﻿'):
        return unicodedata.normalize('NFKC', char)
    return char


# --- Right to left -------------------------------------------------------------

_MIRRORED = dict(zip('()<>[]{}«»', ')(><][}{»«'))


def _direction(char):
    """'rtl' for a letter written from the right, 'ltr' for one written from
    the left or a digit, '' for what goes either way (spaces, punctuation)."""
    kind = unicodedata.bidirectional(char[:1]) if char else ''
    if kind in ('R', 'AL'):
        return 'rtl'
    if kind in ('L', 'EN', 'AN'):
        return 'ltr'
    return ''


def visual_order(items, char_of=lambda item: item, mirror=None):
    """`items` of a line with letters written from the right, turned from
    the order they are read in to the order they sit in on the page, or
    back again: the line taken from its end, but for a run written from the
    left (Latin words, numbers) kept as it is, and the brackets between
    turned round (`mirror(item)` turns one). A line with no such letter is
    left as it is."""
    kinds = [_direction(char_of(item)) for item in items]
    if 'rtl' not in kinds:
        return list(items)
    mirror = mirror or (lambda item: _MIRRORED.get(item, item))
    pieces, index = [], 0
    while index < len(items):
        if kinds[index] == 'ltr':
            end = last = index
            while end < len(items) and kinds[end] != 'rtl':
                if kinds[end] == 'ltr':
                    last = end
                end += 1
            pieces.append(list(items[index:last + 1]))
            index = last + 1
        else:
            pieces.append([mirror(items[index])])
            index += 1
    return [item for piece in reversed(pieces) for item in piece]


# --- Arabic letters' forms ------------------------------------------------------

# Letters that join the one after them as well as the one before, and those
# that join only the one before; the rest join neither.
_DUAL_JOINING = frozenset('بتثجحخسشصضطظعغفقكلمنهيئىپچگـ')
_RIGHT_JOINING = frozenset('اآأإدذرزوؤةژ')
# Persian's own kaf and yeh, which the sets draw as Arabic's.
_ARABIC_SUBSTITUTES = {'ک': 'ك', 'ی': 'ي'}
_FALLBACKS = {'isolated': ('isolated', 'final', 'initial', 'medial'),
              'final': ('final', 'isolated', 'medial', 'initial'),
              'initial': ('initial', 'medial', 'isolated', 'final'),
              'medial': ('medial', 'initial', 'final', 'isolated')}


def _arabic_forms():
    """{letter: {form: presentation form}} of the letters the Arabic G0
    and G2 sets draw, by the form each of their characters is."""
    forms = {}
    for char in G0_SETS['arabic'] + ''.join(G2_SETS['arabic'].values()):
        decomposition = unicodedata.decomposition(char).split()
        if decomposition and decomposition[0] in ('<isolated>', '<final>', '<initial>', '<medial>'):
            letter = ''.join(chr(int(code, 16)) for code in decomposition[1:])
            forms.setdefault(letter, {})[decomposition[0][1:-1]] = char
    return forms


_ARABIC_FORMS = _arabic_forms()


def shape_arabic(items, char_of=lambda item: item, with_char=lambda item, char: char):
    """`items` of a line in reading order with each Arabic letter turned to
    the form it takes beside the others (isolated, initial, medial, final),
    the nearest form the sets have when they lack that one, and a lam
    before an alef as their one character."""
    chars = [_ARABIC_SUBSTITUTES.get(char_of(item), char_of(item)) for item in items]
    out, index = [], 0
    while index < len(items):
        char = chars[index]
        before = chars[index - 1] if index else ''
        joins_before = before in _DUAL_JOINING and (char in _DUAL_JOINING or char in _RIGHT_JOINING)
        if char == 'ل' and index + 1 < len(items) and chars[index + 1] == 'ا':
            forms = _ARABIC_FORMS['لا']
            out.append(with_char(items[index], forms['final' if joins_before else 'isolated']))
            index += 2
            continue
        after = chars[index + 1] if index + 1 < len(items) else ''
        joins_after = char in _DUAL_JOINING and (after in _DUAL_JOINING or after in _RIGHT_JOINING)
        forms = _ARABIC_FORMS.get(char)
        if forms:
            form = {(False, False): 'isolated', (True, False): 'final', (False, True): 'initial',
                    (True, True): 'medial'}[(joins_before, joins_after)]
            char = next(forms[name] for name in _FALLBACKS[form] if name in forms)
        out.append(with_char(items[index], char))
        index += 1
    return out


# --- Transport stream ---------------------------------------------------------

def _packet_layout(data):
    """(size, offset) of the transport stream packets in `data`: 188 bytes,
    or 192 (Blu-ray's .m2ts, a timecode before each) or 204 (with
    Reed-Solomon bytes after); None for bytes that are no transport
    stream."""
    for size, offset in ((188, 0), (192, 4), (204, 0)):
        for start in range(min(size, len(data))):
            if all(start + offset + size * step < len(data) and data[start + offset + size * step] == 0x47
                   for step in range(5)):
                return size, start + offset
    return None


def is_transport_stream(path):
    """Whether the file at `path` is an MPEG transport stream."""
    try:
        with open(path, 'rb') as stream:
            return _packet_layout(stream.read(204 * 6)) is not None
    except OSError:
        return False


def _packets(stream, size=None, offset=None, progress=None, stopped=None):
    """(pid, payload_unit_start, payload) for each transport stream packet
    of an open file."""
    if size is None:
        layout = _packet_layout(stream.read(204 * 6))
        if layout is None:
            return
        size, offset = layout
    stream.seek(0, os.SEEK_END)
    total = stream.tell() or 1
    stream.seek(offset)
    while True:
        chunk = stream.read(size * 4096)
        if not chunk:
            return
        if stopped and stopped():
            raise RuntimeError('Stopped')
        if progress:
            progress(min(1.0, stream.tell() / total))
        for start in range(0, len(chunk) - PACKET + 1, size):
            packet = chunk[start:start + PACKET]
            if packet[0] != 0x47:
                continue
            pid = ((packet[1] & 0x1F) << 8) | packet[2]
            control = (packet[3] >> 4) & 0x03
            if not control & 0x01:
                continue                            # adaptation field alone
            begin = 4 + (packet[4] + 1 if control & 0x02 else 0)
            if begin < PACKET:
                yield pid, bool(packet[1] & 0x40), packet[begin:]


def _section_parts(payload, start):
    """The section bytes a packet holds: after its pointer field when one
    starts in it."""
    return payload[1 + payload[0]:] if start else payload


def _descriptor_pages(body):
    """The pages of a teletext descriptor's body: [{'language', 'type',
    'magazine', 'page'}], the page as the viewer types it (888)."""
    pages = []
    for start in range(0, len(body) - 4, 5):
        language = body[start:start + 3].decode('latin-1', 'replace').strip('\x00 ')
        kind, magazine = body[start + 3] >> 3, body[start + 3] & 0x07
        number = body[start + 4]
        pages.append({'language': language, 'type': kind, 'magazine': magazine or 8,
                      'page': f'{magazine or 8}{number:02X}'})
    return pages


def _pmt_streams(section):
    """{pid: {'type', 'pages'}} for the elementary streams of a PMT
    section; 'pages' for a teletext one."""
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
        pages = None
        index = 0
        while index + 2 <= len(info):
            tag, size = info[index], info[index + 1]
            if tag in (0x56, 0x46):                 # teletext; VBI teletext
                pages = (pages or []) + _descriptor_pages(info[index + 2:index + 2 + size])
            index += 2 + size
        streams[pid] = {'type': kind, 'pages': pages}
        position += 5 + length
    return streams


def _pes_pts(pes):
    """The PTS of a PES packet in 90 kHz ticks, or None."""
    if len(pes) < 14 or pes[0:3] != b'\x00\x00\x01' or not pes[7] & 0x80:
        return None
    b = pes[9:14]
    return (((b[0] >> 1) & 0x07) << 30) | (b[1] << 22) | ((b[2] >> 1) << 15) | (b[3] << 7) | (b[4] >> 1)


def _pes_payload(pes):
    if len(pes) < 9 or pes[0:3] != b'\x00\x00\x01':
        return b''
    return pes[9 + pes[8]:]


def units(payload):
    """The teletext packets of a PES packet's data (a data_identifier, then
    data units): each 44-byte data field of a unit 0x02 or 0x03."""
    found = []
    if not payload or not 0x10 <= payload[0] <= 0x1F:
        return found
    position = 1
    while position + 2 <= len(payload):
        unit, length = payload[position], payload[position + 1]
        field = payload[position + 2:position + 2 + length]
        if unit in (0x02, 0x03) and length == 0x2C and len(field) == 0x2C:
            found.append(field)
        position += 2 + length
    return found


def scan(path, progress=None, stopped=None):
    """What the transport stream at `path` holds of teletext:
    ({pid: [page]}, {pid: [(pts, 44-byte field)]}, origin), the pages each
    teletext stream's descriptor lists, the teletext packets of each, and
    the earliest PTS of any of its streams (where FFmpeg counts its times
    from), in 90 kHz ticks."""
    streams, pmt_pids, sections = {}, set(), {}
    pending, packets, first = {}, {}, {}
    with open(path, 'rb') as stream:
        for pid, start, payload in _packets(stream, progress=progress, stopped=stopped):
            if pid == 0 or pid in pmt_pids:
                if start:
                    sections[pid] = bytearray(_section_parts(payload, True))
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
                        for found, info in _pmt_streams(bytes(section)).items():
                            streams.setdefault(found, info)
                    del sections[pid]
                continue
            info = streams.get(pid)
            if info is None:
                continue
            if info['pages'] is None:
                # Another stream: its first PTS, for where times count from.
                if start and pid not in first:
                    pts = _pes_pts(payload)
                    if pts is not None:
                        first[pid] = pts
                continue
            if start:
                if pending.get(pid):
                    _take(bytes(pending[pid]), packets.setdefault(pid, []), first, pid)
                pending[pid] = bytearray(payload)
            elif pid in pending:
                pending[pid] += payload
    for pid, pes in pending.items():
        if pes:
            _take(bytes(pes), packets.setdefault(pid, []), first, pid)
    origin = min(first.values()) if first else None
    pages = {pid: info['pages'] for pid, info in streams.items() if info['pages'] is not None}
    return pages, packets, origin


def _take(pes, into, first, pid):
    pts = _pes_pts(pes)
    if pts is None:
        return
    first.setdefault(pid, pts)
    for field in units(_pes_payload(pes)):
        into.append((pts, field))


def streams(path, limit=8 * 1024 * 1024):
    """The teletext streams of the transport stream at `path`, from the
    PMT in its first `limit` bytes: {pid: [{'language', 'type', 'magazine',
    'page'}]}."""
    found, pmt_pids, sections = {}, set(), {}
    with open(path, 'rb') as stream:
        layout = _packet_layout(stream.read(204 * 6))
        if layout is None:
            return {}
        stream.seek(0)
        read = 0
        for pid, start, payload in _packets(stream, *layout):
            read += PACKET
            if read > limit:
                break
            if pid != 0 and pid not in pmt_pids:
                continue
            if start:
                sections[pid] = bytearray(_section_parts(payload, True))
            elif pid in sections:
                sections[pid] += payload
            section = sections.get(pid)
            if not section or len(section) < 3 or len(section) < 3 + (((section[1] & 0x0F) << 8) | section[2]):
                continue
            if pid == 0 and section[0] == 0x00:
                end = 3 + (((section[1] & 0x0F) << 8) | section[2]) - 4
                for index in range(8, end - 3, 4):
                    if (section[index] << 8) | section[index + 1]:
                        pmt_pids.add(((section[index + 2] & 0x1F) << 8) | section[index + 3])
            elif section[0] == 0x02:
                for pid_found, info in _pmt_streams(bytes(section)).items():
                    if info['pages'] is not None:
                        found[pid_found] = info['pages']
                pmt_pids.discard(pid)
                if not pmt_pids:
                    break
            del sections[pid]
    return found


# --- Pages ---------------------------------------------------------------------

def _address(field):
    """(magazine, row) of a teletext packet's 44-byte data field, its bytes
    already in reading order; None when the address cannot be read."""
    low, high = ham84(field[2]), ham84(field[3])
    if low is None or high is None:
        return None
    return (low & 0x07) or 8, (low >> 3) | (high << 1)


def _designation(data):
    """The 7 bits of triplet 1 of an X/28/0 Format 1 or M/29/0 packet that
    designate the G0 set and national option sub-set (15.2), or None."""
    if ham84(data[0]) not in (0, 4):
        return None
    triplet = ham2418(data[1:4])
    return None if triplet is None else (triplet >> 7) & 0x7F


def _x26(data, subset='english', g2='latin'):
    """The characters packet X/26 places: {(row, column): char}, from the
    page's G0 set `subset` and G2 set `g2`, or those a triplet designates
    for the ones after it (Table 34)."""
    placed = {}
    row = 0
    for start in range(1, 40, 3):
        triplet = ham2418(data[start:start + 3])
        if triplet is None:
            continue
        address, mode, value = triplet & 0x3F, (triplet >> 6) & 0x1F, triplet >> 11
        if address >= 40:
            if mode == 0x1F:                       # termination marker
                break
            if mode in (0x01, 0x04):               # full row colour; set active position
                row = 24 if address == 40 else address - 40
            elif mode == 0x07:                     # address display row 0
                row = 0
            continue
        if mode == 0x08:                           # modified G0 and G2 designation
            subset, g2 = subset_of(value >> 3, value & 7), g2_of(value >> 3, value & 7)
        elif mode == 0x09 and value >= 0x20:       # a G0 character
            placed[(row, address)] = _plain_char(value, subset)
        elif mode == 0x0F and value >= 0x20:       # a G2 character
            placed[(row, address)] = G2_SETS.get(g2, _G2).get(value, '')
        elif mode >= 0x10 and value >= 0x20:       # a G0 letter with a mark
            base = '@' if value == 0x2A and mode == 0x10 else _plain_char(value, subset)
            mark = _MARKS.get(mode & 0x0F, '') if mode != 0x10 else ''
            placed[(row, address)] = unicodedata.normalize('NFC', base + mark)
    return placed


def _row_cells(data, subset, boxed_page):
    """The cells of a row's 40 bytes: [(char, Style, boxed)] where char is
    '' for a cell that shows nothing (a spacing attribute, a mosaic, a
    concealed character), and whether the row is double height."""
    cells = []
    color, background, flash = 7, 0, False
    boxed = mosaic = concealed = double = False
    for byte in data:
        code = parity(byte)
        if code is None:
            code = 0x20                             # a character that failed its parity
        style = markup.Style(color=COLORS[color], background=BACKGROUNDS[background], flash=flash)
        if code >= 0x20:
            if concealed or (mosaic and not 0x40 <= code < 0x60):
                cells.append(('', style, boxed or not boxed_page))
            else:
                cells.append((g0_char(code, subset), style, boxed or not boxed_page))
            continue
        # A spacing attribute takes its cell, shown as a space; "Set-At"
        # ones apply to it, "Set-After" ones from the next cell.
        after = None
        if code <= 0x07:
            after = ('color', code)
        elif 0x10 <= code <= 0x17:
            after = ('mosaic', code - 0x10)
        elif code == _FLASH:
            after = ('flash', True)
        elif code == _STEADY:
            flash = False
        elif code == _START_BOX:
            after = ('box', True)
        elif code == _END_BOX:
            after = ('box', False)
        elif code in (_DOUBLE_HEIGHT, _DOUBLE_SIZE):
            double = True
        elif code == _CONCEAL:
            concealed = True
        elif code == _BLACK_BACKGROUND:
            background = 0
        elif code == _NEW_BACKGROUND:
            background = color
        style = markup.Style(color=COLORS[color], background=BACKGROUNDS[background], flash=flash)
        cells.append(('', style, boxed or not boxed_page))
        if after:
            what, value = after
            if what == 'color':
                color, mosaic, concealed = value, False, False
            elif what == 'mosaic':
                color, mosaic, concealed = value, True, False
            elif what == 'flash':
                flash = True
            elif what == 'box':
                boxed = value
    return cells, double


def render(rows, subset='english', placed=None, subtitle=True):
    """A page's rows ({row: 40 bytes}) as a subtitle's text, with tags for
    its colours and the place it sits as an ASS code; '' for a page that
    shows nothing. On a subtitle page only the boxed text shows."""
    placed = placed or {}
    lines, spans = [], []
    skip = None
    boxed_page = subtitle and _has_box(rows)
    for row in range(1, 25):
        if row == skip or row not in rows:
            continue
        cells, double = _row_cells(rows[row], subset, boxed_page)
        if double:
            skip = row + 1
        shown = []
        for column, (char, style, boxed) in enumerate(cells):
            if not boxed:
                shown.append((column, '', style))
                continue
            if (row, column) in placed and placed[(row, column)]:
                char = placed[(row, column)]
            shown.append((column, char, style))
        visible = [column for column, char, _style in shown if char.strip()]
        if not visible:
            continue
        first, last = visible[0], visible[-1]
        # Arabic and Hebrew sit on the page from the right: read, they are
        # taken from the end, their presentation forms as letters.
        shown[first:last + 1] = [(column, _shown(char), style) for column, char, style in visual_order(
            shown[first:last + 1], char_of=lambda cell: cell[1],
            mirror=lambda cell: (cell[0], _MIRRORED.get(cell[1], cell[1]), cell[2]))]
        runs = []
        for index in range(first, last + 1):
            _column, char, style = shown[index]
            if not char or not char.strip():
                # A space between words: the style both sides share.
                before = next((shown[k][2] for k in range(index - 1, first - 1, -1) if shown[k][1].strip()), style)
                after = next((shown[k][2] for k in range(index + 1, last + 1) if shown[k][1].strip()), style)
                runs.append((' ', before if before == after else markup.Style(
                    *((a if a == b else '') if isinstance(a, str) else a and b for a, b in zip(before, after)))))
            else:
                runs.append((char, style))
        lines.append((row, first, last, double))
        spans.append(runs)
    if not lines:
        return ''
    joined = []
    for number, runs in enumerate(spans):
        if number:
            joined.append(('\n', markup.PLAIN))
        joined += runs
    text = markup.tidy(joined)
    keypad = _keypad(lines)
    return (markup.with_alignment('', keypad) if keypad != 2 else '') + text


def _has_box(rows):
    return any(parity(byte) == _START_BOX for data in rows.values() for byte in data)


def _keypad(lines):
    """Where a subtitle on these rows ((row, first column, last column,
    double height)) sits, as a keypad number: top, middle or bottom of the
    page's 24 rows; left, centre or right by its columns."""
    top = lines[0][0]
    bottom = lines[-1][0] + (1 if lines[-1][3] else 0)
    middle = (top + bottom) / 2 / 24
    vertical = 2 if middle < 1 / 3 else 0 if middle > 2 / 3 else 1
    centres = [(first + last) / 2 for _row, first, last, _double in lines]
    lefts = [first for _row, first, _last, _double in lines]
    rights = [last for _row, _first, last, _double in lines]
    several = len(lines) > 1
    if all(abs(centre - 19.5) <= 3 for centre in centres):
        horizontal = 1
    elif max(rights) < 26 or (several and len(set(lefts)) == 1):
        horizontal = 0
    elif min(lefts) > 13 or (several and len(set(rights)) == 1):
        horizontal = 2
    else:
        horizontal = 1
    return (1, 4, 7)[vertical] + horizontal


class _Decoder:
    """Follows a teletext service for one page and gathers its subtitles."""

    def __init__(self, magazine, number, language=''):
        self.magazine, self.number = magazine, number
        self.group = _LANGUAGE_GROUPS.get(language_code(language), 0)
        self.magazine_group = {}                    # M/29/0's designation, by magazine
        self.current = {}                           # the page each magazine is sending
        self.receiving = False
        self.rows, self.enhancements = {}, {}       # X/26 packets, by their number
        self.page_group = None                      # X/28/0's designation
        self.option, self.subtitle = 0, True
        self.started = None                         # when this transmission's header came
        self.shown, self.since = '', None
        self.segments = []
        self.last = None

    def _sets(self):
        """The page's G0 set (or Latin sub-set) and G2 set."""
        group = self.page_group if self.page_group is not None else \
            self.magazine_group.get(self.magazine, self.group)
        return subset_of(group, self.option), g2_of(group, self.option)

    def _commit(self, until):
        """The transmission that began at self.started is complete: what it
        shows replaces what was shown, from when it began."""
        if self.started is None:
            return
        subset, g2 = self._sets()
        placed = {}
        for number in sorted(self.enhancements):
            placed.update(_x26(self.enhancements[number], subset, g2))
        text = render(self.rows, subset, placed, self.subtitle)
        if text != self.shown:
            if self.shown and self.since is not None and self.started > self.since:
                self.segments.append({'start': self.since, 'end': self.started, 'text': self.shown})
            self.shown, self.since = text, self.started
        self.started = None

    def feed(self, seconds, field):
        field = field[:1] + bytes(_REVERSED[byte] for byte in field[1:])
        address = _address(field)
        if address is None:
            return
        self.last = seconds
        magazine, row = address
        data = field[4:44]
        if row == 0:
            units_, tens = ham84(data[0]), ham84(data[1])
            controls = [ham84(byte) for byte in data[2:8]]
            if units_ is None or tens is None or None in controls:
                return
            serial = bool(controls[5] & 0x01)
            number = (tens << 4) | units_
            ours = magazine == self.magazine and number == self.number
            if serial:
                self.current.clear()
            self.current[magazine] = number
            if ours:
                self._commit(seconds)
                if controls[1] & 0x08:              # C4, erase page
                    self.rows, self.enhancements = {}, {}
                self.page_group = None
                self.subtitle = bool(controls[3] & 0x08)
                c12, c13, c14 = (controls[5] >> 1) & 1, (controls[5] >> 2) & 1, (controls[5] >> 3) & 1
                self.option = c12 * 4 + c13 * 2 + c14
                self.started = seconds
                self.receiving = True
            elif self.receiving and (serial or magazine == self.magazine):
                self.receiving = False
            return
        if row == 29:
            designation = _designation(data)
            if designation is not None:
                self.magazine_group[magazine] = designation >> 3
            return
        if not self.receiving or magazine != self.magazine or self.current.get(magazine) != self.number:
            return
        if row <= 24:
            self.rows[row] = bytes(data)
        elif row == 26:
            # Read when the page is complete, in the sets it ends up with.
            self.enhancements[ham84(data[0])] = bytes(data)
        elif row == 28:
            designation = _designation(data)
            if designation is not None:
                self.page_group = designation >> 3

    def finish(self):
        if self.started is not None:
            self._commit(self.last)
        if self.shown and self.since is not None and self.last is not None and self.last > self.since:
            self.segments.append({'start': self.since, 'end': self.last, 'text': self.shown})
        self.shown = ''
        return self.segments


def page_parts(page):
    """(magazine, page byte) for a page number as the viewer types it
    ('888', 801): magazine 8 is sent as 0."""
    text = str(page)
    return int(text[0]) or 8, int(text[1:3], 16)


def decode(packets, page=DEFAULT_PAGE, language='', origin=None):
    """The subtitles of teletext page `page` ('888') in `packets`, [(pts in
    90 kHz ticks, 44-byte data field)] as a DVB stream carries them: shown
    from the transmission that brings each until the one that erases or
    replaces it, in seconds from `origin` (a PTS; the first packet's when
    None). `language` (the
    descriptor's) chooses the group of national sub-sets, without a packet
    that designates one."""
    magazine, number = page_parts(page)
    decoder = _Decoder(magazine, number, language)
    base, previous, wraps = None, None, 0
    for pts, field in packets:
        if previous is not None and pts < previous - _PTS_WRAP // 2:
            wraps += 1
        previous = pts
        if base is None:
            base = origin if origin is not None else pts
        seconds = round((pts + wraps * _PTS_WRAP - base) / 90000.0, 3)
        decoder.feed(seconds, field)
    return [segment for segment in decoder.finish() if segment['end'] > segment['start']]


def read(path, page=None, pid=None, progress=None, stopped=None):
    """(subtitles, {'page', 'language', 'pid'}) of a teletext page in the
    transport stream at `path`: `page` ('888') in the stream `pid`, or the
    first subtitle page any descriptor lists. Times count from the earliest
    start of the file's streams, as FFmpeg counts them. Raises ValueError
    for a file with no teletext."""
    pages, packets, origin = scan(path, progress=progress, stopped=stopped)
    if pid is None or not packets.get(pid):
        # The stream that lists the page, else any that carries teletext.
        listing = [found for found, listed in pages.items() if page and any(p['page'] == str(page) for p in listed)]
        pid = next((found for found in listing + list(packets) if packets.get(found)), None)
    if pid is None:
        raise ValueError('There is no teletext in this file')
    listed = pages.get(pid) or []
    if page is None:
        chosen = next((p for p in listed if p['type'] in SUBTITLE_TYPES), listed[0] if listed else None)
        page = chosen['page'] if chosen else busiest_subtitle_page(packets[pid])
    entry = next((p for p in listed if p['page'] == str(page)), {})
    language = entry.get('language', '')
    segments = decode(packets[pid], page, language, origin)
    return segments, {'page': str(page), 'language': language, 'pid': pid}


def busiest_subtitle_page(packets):
    """The page with the Subtitle bit set that is sent most often, for a
    stream whose descriptor lists none: its 'mpp' number, or '888'."""
    counts = {}
    for _pts, field in packets:
        field = field[:1] + bytes(_REVERSED[byte] for byte in field[1:12])
        address = _address(field)
        if not address or address[1] != 0:
            continue
        units_, tens, flags = ham84(field[4]), ham84(field[5]), ham84(field[9])     # C6 in the 6th
        if None in (units_, tens, flags) or not flags & 0x08:
            continue
        key = f'{address[0]}{(tens << 4) | units_:02X}'
        counts[key] = counts.get(key, 0) + 1
    return max(counts, key=counts.get) if counts else str(DEFAULT_PAGE)


# --- Writing -------------------------------------------------------------------

def _encoder(subset):
    """{char: G0 code} for the G0 set `subset`: the Latin one with that
    national option sub-set, or another G0 set."""
    if subset in G0_SETS:
        return {char: 0x20 + index for index, char in reversed(list(enumerate(G0_SETS[subset])))
                if (subset, 0x20 + index) != _TAIL}
    table = {chr(code): code for code in range(0x20, 0x7F) if code not in NATIONAL_POSITIONS}
    table.update({char: code for code, char in zip(NATIONAL_POSITIONS, SUBSETS[subset])})
    return table


# Letters written as another that looks the same, where a set has no place
# of its own for them: Latin capitals in Cyrillic and Greek, and Ukrainian's
# Ґ, which the Ukrainian set draws as Г.
_LOOKALIKES = tuple(zip('ABCEHKMOPTXaceopxy', 'АВСЕНКМОРТХасеорху')) + \
    tuple(zip('ABEHIKMNOPTXYZo', 'ΑΒΕΗΙΚΜΝΟΡΤΧΥΖο')) + (('Ґ', 'Г'), ('ґ', 'г'))


@functools.lru_cache(maxsize=None)
def _encoders(group, option):
    """What a page of a group and C12-C14 value writes its text with:
    ({char: G0 code}, {char: G2 code}, {letter: G0 code an X/26 mark goes
    over}, the G0 set's name)."""
    subset = subset_of(group, option)
    supplementary = {char: code for code, char in sorted(G2_SETS[g2_of(group, option)].items(), reverse=True)
                     if char and code != 0x20}
    encode = _encoder(subset)
    if subset in G0_SETS:
        plain = dict(encode)
    else:
        plain = {chr(code): code for code in range(0x41, 0x7B) if chr(code).isalpha()}
    # A Latin letter the Cyrillic and Greek G2 sets leave out, because a
    # letter of the G0 set looks the same, is written as that letter.
    for latin, other in _LOOKALIKES:
        if latin not in encode and latin not in supplementary and other in encode:
            encode[latin] = encode[other]
    return encode, supplementary, plain, subset


def _encodable(char, encoders):
    """Whether a page written with `encoders` holds `char`: in its G0 set,
    its G2 set, as a letter of its G0 set with a mark, or (Arabic) as a
    letter one of the forms of which the sets draw."""
    encode, supplementary, plain, subset = encoders
    if char in encode or char in supplementary or char.isspace():
        return True
    if (subset == 'arabic' or 'ﺍ' in supplementary) and _ARABIC_SUBSTITUTES.get(char, char) in _ARABIC_FORMS:
        return True
    decomposed = unicodedata.normalize('NFD', char)
    if len(decomposed) == 1:
        return decomposed in encode or decomposed in supplementary
    return len(decomposed) == 2 and decomposed[1] in _FROM_MARK and decomposed[0] in plain


def designation_for(language='', texts=()):
    """(group, C12-C14 value) a page in `language` (Subtitld's code or ISO
    639-2) showing `texts` is written with: the first of the language's
    own sets that holds every character of them, else the first set that
    does, else the one that holds the most."""
    chars = set()
    for text in texts:
        chars.update(markup.plain(markup.split_lead(str(text or ''))[1]))
    chars.discard('\n')
    preferred = _WRITING.get(language_code(language), [])
    best = None
    for designation in preferred + [entry for entry in _DESIGNATIONS if entry not in preferred]:
        encoders = _encoders(*designation)
        missing = sum(1 for char in chars if not _encodable(char, encoders))
        if not missing:
            return designation
        if best is None or missing < best[0]:
            best = (missing, designation)
    return best[1]


def _row_bytes(runs, encode, column, supplementary=None, plain=None):
    """The 40 bytes of a subtitle row: double height, boxed, the text from
    `column` in its colours (in the order it sits in); and the X/26
    characters it needs {column: (mode, data)} for what the G0 set lacks,
    from the G2 set (`supplementary`, {char: code}) or as a letter of the
    G0 set (`plain`, {letter: code}) with a mark. Latin's by default."""
    if supplementary is None:
        _encode, supplementary, plain, _subset = _encoders(0, 0)
    cells = [0x20] * 40
    extra = {}
    position = max(0, column - 3)
    cells[position:position + 3] = [_DOUBLE_HEIGHT, _START_BOX, _START_BOX]
    position += 3
    color = 7
    background = ''
    for text, style in runs:
        wanted = _NEAREST.get(style.color, _nearest(style.color))
        wanted_background = style.background
        if (wanted != color or wanted_background != background) and position < 38:
            if wanted_background != background:
                if wanted_background:
                    # New Background takes the colour the text is in: the
                    # background's first, then the text's.
                    cells[position] = _NEAREST.get(wanted_background[:7], _nearest(wanted_background[:7]))
                    cells[position + 1] = _NEW_BACKGROUND
                    position += 2
                else:
                    cells[position] = _BLACK_BACKGROUND
                    position += 1
                background = wanted_background
            cells[position] = wanted
            position += 1
            color = wanted
        for char in text:
            if position >= 38:
                break
            decomposed = unicodedata.normalize('NFD', char)
            if len(decomposed) == 1 and char not in encode and char not in supplementary:
                char = decomposed                   # a character that is another (Greek ; for ;)
            if char in encode:
                cells[position] = encode[char]
            elif char in supplementary:
                cells[position] = 0x20
                extra[position] = (0x0F, supplementary[char])
            else:
                base, marks = decomposed[:1], decomposed[1:]
                if len(marks) == 1 and marks in _FROM_MARK and base in plain:
                    cells[position] = encode.get(base, 0x20)
                    extra[position] = (0x10 | _FROM_MARK[marks], plain[base])
                else:
                    cells[position] = encode.get(char, 0x3F)
            position += 1
    cells[position:position + 2] = [_END_BOX, _END_BOX][:max(0, 40 - position)]
    return bytes(parity_encode(code) for code in cells[:40]), extra


def _nearest(color):
    """The teletext colour nearest '#rrggbb' (white for none)."""
    if not color:
        return 7
    try:
        red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    except ValueError:
        return 7
    return ((1 if red >= 128 else 0) | (2 if green >= 128 else 0) | (4 if blue >= 128 else 0)) or 7


def _field(magazine, row, data):
    """A 44-byte data field for a teletext packet: field parity and line
    offset, then the framing code, address and data in over-the-air bit
    order."""
    address = bytes((ham84_encode((magazine & 7) | ((row & 1) << 3)), ham84_encode(row >> 1)))
    return b'\xe0' + bytes(_REVERSED[byte] for byte in b'\x27' + address + data)


def _header(magazine, number, erase, option):
    controls = [0, erase << 3, 0, 1 << 3, 0x01 | 0x02 | 0x04, ((option >> 2) & 1) << 1 | ((option >> 1) & 1) << 2 |
                (option & 1) << 3]
    data = bytes([ham84_encode(number & 0x0F), ham84_encode(number >> 4)] + [ham84_encode(c) for c in controls])
    return _field(magazine, 0, data + bytes(parity_encode(0x20) for _ in range(32)))


# Table 30's colours for CLUTs 2 and 3, which a packet X/28/0 Format 1
# defines again: the same, so a page that designates its sets keeps them.
_CLUTS_2_AND_3 = ((15, 0, 5), (15, 7, 0), (0, 15, 7), (15, 15, 11), (0, 12, 10), (5, 0, 0), (6, 5, 2), (12, 7, 7),
                  (3, 3, 3), (15, 7, 7), (7, 15, 7), (15, 15, 7), (7, 7, 15), (15, 7, 15), (7, 15, 15), (13, 13, 13))


def _x28(magazine, group, option):
    """Packet X/28/0 Format 1 (9.4.2.2, table 4) for a Level 1 page,
    designating its G0 and G2 sets and national option sub-set (table 32),
    the second G0 set the same; no side panels, the colours as they are."""
    bits = []

    def put(value, count):
        bits.extend((value >> index) & 1 for index in range(count))

    designation = (group << 3) | (option & 7)
    put(0, 4)                                       # a basic Level 1 page
    put(0, 3)                                       # 7 bits and odd parity
    put(designation, 7)
    put(designation, 7)
    put(0, 7)                                       # side panels
    for red, green, blue in _CLUTS_2_AND_3:
        put(red, 4)
        put(green, 4)
        put(blue, 4)
    put(0, 14)                                      # screen and row colours, no re-mapping
    triplets = [sum(bit << index for index, bit in enumerate(bits[start:start + 18])) for start in range(0, 234, 18)]
    return _field(magazine, 28, bytes([ham84_encode(0)]) + b''.join(ham2418_encode(value) for value in triplets))


def _visual_runs(runs, subset, supplementary):
    """A line's runs [(text, style)] in the order they sit on the page:
    Arabic shaped (in the Arabic sets), then a line with letters written
    from the right taken from its end."""
    cells = [(char, style) for text, style in runs for char in text]
    if subset == 'arabic' or 'ﺍ' in supplementary:
        cells = shape_arabic(cells, char_of=lambda cell: cell[0], with_char=lambda cell, char: (char, cell[1]))
    cells = visual_order(cells, char_of=lambda cell: cell[0],
                         mirror=lambda cell: (_MIRRORED.get(cell[0], cell[0]), cell[1]))
    out = []
    for char, style in cells:
        if out and out[-1][1] == style:
            out[-1] = (out[-1][0] + char, style)
        else:
            out.append((char, style))
    return out


def page_fields(text, page=DEFAULT_PAGE, language='', designation=None):
    """The teletext packets of one transmission of a subtitle page showing
    `text` (Subtitld's, with tags and a place), its Erase Page bit set: the
    header, an X/28/0 that designates its character sets (but for the
    Latin ones of western Europe, which need none), its X/26 for the
    characters the G0 set lacks, and the rows, double height and centred
    on the bottom rows, or the top ones for a subtitle placed there. The
    sets are `designation` (group, C12-C14 value), else those
    `designation_for` the language and the text gives. `text` '' is a
    transmission that erases."""
    magazine, number = page_parts(page)
    group, option = designation or designation_for(language, [text])
    encode, supplementary, plain, subset = _encoders(group, option)
    fields = [_header(magazine, number, 1, option)]
    if group:
        fields.append(_x28(magazine, group, option))
    lead, rest = markup.split_lead(text or '')
    lines = [line for line in rest.split('\n') if markup.plain(line).strip()][:11]
    if not lines:
        return fields
    keypad = markup.alignment(lead)
    first_row = 2 if keypad in (7, 8, 9) else 23 - 2 * len(lines)
    if keypad in (4, 5, 6):
        first_row = 12 - len(lines)
    triplets = []
    rows = []
    for index, line in enumerate(lines):
        runs = _visual_runs(markup.spans(line), subset, supplementary)
        width = len(''.join(chunk for chunk, _style in runs))
        if keypad in (1, 4, 7):
            column = 3
        elif keypad in (3, 6, 9):
            column = max(3, 37 - width)
        else:
            column = max(3, (40 - width) // 2)
        row = first_row + 2 * index
        data, extra = _row_bytes(runs, encode, column, supplementary, plain)
        rows.append(_field(magazine, row, data))
        if extra:
            triplets.append((40 + row if row != 24 else 40, 0x04, 0))       # set active position
            for place, (mode, value) in sorted(extra.items()):
                triplets.append((place, mode, value))
    if triplets:
        triplets.append((0x3F, 0x1F, 0))            # termination marker
        for start in range(0, len(triplets), 13):
            chunk = triplets[start:start + 13]
            chunk += [(0x3F, 0x1F, 0)] * (13 - len(chunk))
            body = bytes([ham84_encode(start // 13)]) + b''.join(
                ham2418_encode(address | (mode << 6) | (value << 11)) for address, mode, value in chunk)
            fields.append(_field(magazine, 26, body))
    return fields + rows


def _pes(fields, pts):
    """A PES packet of teletext subtitle units at `pts` (90 kHz ticks), its
    header 45 bytes and its length a whole number of transport packets, as
    EN 300 472 asks."""
    timestamp = bytes((0x21 | ((pts >> 29) & 0x0E), (pts >> 22) & 0xFF, 0x01 | ((pts >> 14) & 0xFE),
                       (pts >> 7) & 0xFF, 0x01 | ((pts << 1) & 0xFE)))
    header_data = timestamp + b'\xff' * (0x24 - len(timestamp))
    body = b'\x10' + b''.join(b'\x03\x2c' + field for field in fields)
    total = 9 + len(header_data) + len(body)
    while total % 184:
        body += b'\xff\x2c' + b'\xff' * 0x2C
        total += 46
    return b'\x00\x00\x01\xbd' + (total - 6).to_bytes(2, 'big') + b'\x84\x80\x24' + header_data + body


def _crc32(data):
    """MPEG-2's CRC-32 of a section."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7 if crc & 0x80000000 else crc << 1) & 0xFFFFFFFF
    return crc


def _section(table_id, extension, body):
    length = 5 + len(body) + 4
    head = bytes((table_id, 0xB0 | (length >> 8), length & 0xFF, extension >> 8, extension & 0xFF, 0xC1, 0, 0))
    section = head + body
    return section + _crc32(section).to_bytes(4, 'big')


def _ts_packets(pid, payload, counters, start=True):
    out = []
    first = True
    while payload or first:
        piece = payload[:184]
        payload = payload[184:]
        stuffing = 184 - len(piece)
        counter = counters.get(pid, 0)
        counters[pid] = (counter + 1) & 0x0F
        head = bytes((0x47, (0x40 if first and start else 0) | (pid >> 8), pid & 0xFF))
        if stuffing:
            adaptation = bytes([stuffing - 1]) + (b'\x00' + b'\xff' * (stuffing - 2) if stuffing > 1 else b'')
            out.append(head + bytes([0x30 | counter]) + adaptation + piece)
        else:
            out.append(head + bytes([0x10 | counter]) + piece)
        first = False
    return out


TELETEXT_PID, PCR_PID, PMT_PID = 0x0100, 0x0101, 0x1000


def _pcr_packet(pts):
    """A packet of the clock alone (an adaptation field with a PCR), at
    `pts`: FFmpeg times a teletext PES by the programme's clock."""
    clock = bytes(((pts >> 25) & 0xFF, (pts >> 17) & 0xFF, (pts >> 9) & 0xFF, (pts >> 1) & 0xFF,
                   ((pts & 1) << 7) | 0x7E, 0x00))
    return bytes((0x47, PCR_PID >> 8, PCR_PID & 0xFF, 0x20, 183, 0x10)) + clock + b'\xff' * 176


def _transmissions(pages, frame=0.04):
    """The transmissions of `pages` ([{'page', 'language', 'subtitld_language',
    'segments'}]), in the order they are sent: [(seconds, text, page,
    designation)], each page erased at 0, each subtitle sent when it starts
    and erased when it ends (or replaced, when the next one starts within
    `frame`), in the sets its page is written with."""
    events = []
    for order, entry in enumerate(pages):
        # One set for the whole page: the one its language and all its
        # subtitles ask for.
        timeline = sorted(entry.get('segments') or [], key=lambda s: float(s.get('start', 0)))
        designation = designation_for(entry.get('subtitld_language') or entry.get('language') or '',
                                      [segment.get('text') for segment in timeline])
        events.append((0.0, order, '', entry['page'], designation))
        for index, segment in enumerate(timeline):
            begin, end = float(segment.get('start', 0)), float(segment.get('end', 0))
            following = float(timeline[index + 1]['start']) if index + 1 < len(timeline) else None
            events.append((begin, order, str(segment.get('text') or ''), entry['page'], designation))
            if following is None or following - end > frame / 2:
                events.append((end, order, '', entry['page'], designation))
    events.sort(key=lambda event: (round(event[0], 3), event[1], event[2] != ''))
    return [(when, text, page, designation) for when, _order, text, page, designation in events]


def transport_stream(pages, start=0.0, frame=0.04):
    """The bytes of a transport stream with one teletext stream holding
    `pages`: [{'page' ('888'), 'language' (ISO 639-2, 'eng'), 'type' (2 or
    5), 'segments'}], each subtitle sent when it starts and erased when it
    ends (or when the next one starts). Times are seconds from `start`;
    every page is erased at `start` itself, so the stream's own start is
    the video's."""
    counters = {}
    descriptor = b''.join(entry['language'][:3].encode('latin-1').ljust(3, b' ')
                          + bytes(((entry.get('type') or 2) << 3 | (page_parts(entry['page'])[0] & 7),
                                   page_parts(entry['page'])[1]))
                          for entry in pages)
    pat = b'\x00' + _section(0x00, 1, bytes((0, 1, 0xE0 | (PMT_PID >> 8), PMT_PID & 0xFF)))
    stream_info = bytes((0x06, 0xE0 | (TELETEXT_PID >> 8), TELETEXT_PID & 0xFF, 0xF0, 2 + len(descriptor),
                         0x56, len(descriptor))) + descriptor
    pmt = b'\x00' + _section(0x02, 1, bytes((0xE0 | (PCR_PID >> 8), PCR_PID & 0xFF, 0xF0, 0x00)) + stream_info)
    out = []
    for when, text, page, designation in _transmissions(pages, frame):
        out += _ts_packets(0, pat, counters) + _ts_packets(PMT_PID, pmt, counters)
        pts = int(round((max(0.0, when) + start) * 90000)) % _PTS_WRAP
        out.append(_pcr_packet(pts))
        out += _ts_packets(TELETEXT_PID, _pes(page_fields(text, page, designation=designation), pts), counters)
    return b''.join(out)


def mux_command(video, teletext, output, ffmpeg='ffmpeg'):
    """The FFmpeg command that writes `video` out as the transport stream
    `output`, its picture and sound copied, with the teletext stream of the
    transport stream `teletext`. FFmpeg's demuxer moves a teletext packet
    to no later than 0.14 s after the clock that comes before it, and its
    muxer writes the clock 0.7 s behind the packets unless told otherwise:
    0.1 s keeps every page where it was written."""
    return [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y', '-nostdin', '-i', str(video),
            '-i', str(teletext), '-map', '0:v?', '-map', '0:a?', '-map', '1:0',
            '-c', 'copy', '-muxdelay', '0.1', '-f', 'mpegts', str(output)]


def write_video(video, output, tracks, ffmpeg='ffmpeg', startupinfo=None):
    """Write `video` out as the transport stream `output` with a teletext
    page for each of `tracks` ([{'segments', 'language' (Subtitld's code)}]):
    888 for the first, 889 and on for the others. Raises ValueError for no
    tracks and RuntimeError with what FFmpeg said when it cannot write."""
    from subtitld.modules import usf
    if not tracks:
        raise ValueError('There are no subtitles to write into the video')
    pages = [{'page': str(DEFAULT_PAGE + index), 'type': 2,
              'language': usf.iso639_2(track.get('language')) if track.get('language') else 'und',
              'subtitld_language': track.get('language') or '', 'segments': track.get('segments') or []}
             for index, track in enumerate(tracks)]
    handle, path = tempfile.mkstemp(prefix='subtitld_teletext_', suffix='.ts')
    os.close(handle)
    try:
        with open(path, 'wb') as stream:
            stream.write(transport_stream(pages, start=1.4))
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


# --- OP-47: teletext in each frame's ancillary data ------------------------------

# Free TV Australia's OP-47 (SMPTE RDD 8) carries a teletext service in the
# ancillary data of each frame of a high definition video, where SMPTE ST
# 436 puts it in an MXF and an MCC file writes it as text: a subtitling
# distribution packet (DID 43h, SDID 02h) holds up to five teletext packets
# as they would go over the air on five lines of the vertical interval,
# their bytes in reading order; a multipacket (SDID 03h) holds SDPs and
# others. A teletext packet here is the 44-byte data field DVB carries, as
# page_fields gives it and decode reads it.
OP47_DID, OP47_SDID, OP47_MULTIPACKET = 0x43, 0x02, 0x03
# The lines of the first field the five packets of a frame are said to be
# on (6 to 22 are the ones teletext takes).
_OP47_LINES = (7, 8, 9, 10, 11)


def op47_fields(packet):
    """The teletext packets in an ancillary data packet (DID, SDID, data
    count, then its words, as an MCC line or SMPTE ST 436 holds it): those
    of a subtitling distribution packet, or of each one a multipacket
    carries; [] for any other packet."""
    if len(packet) < 3 or packet[0] != OP47_DID:
        return []
    sdid, words = packet[1], bytes(packet[3:3 + packet[2]])
    if sdid == OP47_MULTIPACKET:
        # A priority word, then each inner packet: its line, DID, SDID,
        # count and words.
        found, index = [], 1
        while index + 4 <= len(words):
            count = words[index + 3]
            found += op47_fields(words[index + 1:index + 4 + count])
            index += 4 + count
        return found
    if sdid != OP47_SDID or len(words) < 9 or words[:2] != b'\x51\x15' or words[3] != 0x02:
        return []
    found, index = [], 9
    for descriptor in words[4:9]:
        if not descriptor:
            break                                   # the descriptors after an empty one are empty too
        vbi = words[index:index + 45]
        index += 45
        # The clock run-in, the framing code, then the packet's 42 bytes.
        if len(vbi) == 45 and vbi[2] == 0x27:
            found.append(b'\xe0' + bytes(_REVERSED[byte] for byte in vbi[2:]))
    return found


def op47_packet(fields, counter=0):
    """The ancillary data packet (DID, SDID, data count, words, checksum, as
    an MCC line holds it) of a subtitling distribution packet carrying up
    to five teletext packets `fields`, its footer counting `counter` (one
    more than the packet before it). Its length counts its words from the
    identifier to its own checksum, which makes their sum 0."""
    fields = list(fields)[:5]
    descriptors = bytes(0x80 | 0x60 | line for line, _field_ in zip(_OP47_LINES, fields)).ljust(5, b'\x00')
    packets = b''.join(b'\x55\x55' + bytes(_REVERSED[byte] for byte in field[1:44]) for field in fields)
    words = b'\x51\x15\x00\x02' + descriptors + packets + bytes((0x74, (counter >> 8) & 0xFF, counter & 0xFF))
    words = words[:2] + bytes([len(words) + 1]) + words[3:]
    words += bytes([-sum(words) & 0xFF])
    anc = bytes((OP47_DID, OP47_SDID, len(words))) + words
    return anc + bytes([sum(anc) & 0xFF])


def op47_frames(tracks, fps):
    """{frame: [teletext packets]} carrying `tracks` ([{'segments',
    'language' (Subtitld's code)}], a page each, 888 and up) at `fps`
    frames a second: each transmission from the frame its time falls on,
    five packets a frame, the next one after it when they crowd."""
    pages = [{'page': str(DEFAULT_PAGE + index), 'language': '', 'subtitld_language': track.get('language') or '',
              'segments': track.get('segments') or []} for index, track in enumerate(tracks)]
    out, cursor = {}, 0                             # the next free place, counted in packets
    for when, text, page, designation in _transmissions(pages, 1.0 / float(fps)):
        cursor = max(cursor, int(round(max(0.0, when) * float(fps))) * 5)
        for field in page_fields(text, page, designation=designation):
            out.setdefault(cursor // 5, []).append(field)
            cursor += 1
    return out


def op47_decode(batches, fps, page=None, language=''):
    """(subtitles, page) of the OP-47 teletext in `batches` [(frame, [teletext
    packets])] at `fps` frames a second: page `page` ('888'), or the subtitle
    page sent most often, timed from frame 0; ([], None) for none."""
    packets = [(int(round(frame * 90000 / float(fps))), field)
               for frame, fields in sorted(batches, key=lambda batch: batch[0]) for field in fields]
    if not packets:
        return [], None
    page = str(page) if page else busiest_subtitle_page(packets)
    return decode(packets, page, language, origin=0), page
