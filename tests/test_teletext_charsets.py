"""Teletext's character sets (EN 300 706 section 15): the G0 sets other than
Latin (Cyrillic's three options, Greek, Arabic, Hebrew) and the G2 sets
that go with them, as table 32 designates them by group and C12-C14; each
set checked against the tables of the specification, and against libzvbi's
own (where it is installed) but for the places the two are known to differ;
X/26 placing G2 characters and G0 letters with marks in those sets, and a
triplet designating other sets for those after it; packet X/28/0 Format 1
written and read; the set a page is written with, by its language and its
text; Arabic shaped and Arabic and Hebrew put on the page from the right,
read back in the order they are read; and a transport stream with a page
in each script read back, by this module and by FFmpeg's libzvbi decoder.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import ctypes
import ctypes.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import teletext as tt

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


SB, EB, DH = 0x0B, 0x0A, 0x0D


def row(*parts):
    """A row's 40 bytes: ints are codes, strings ASCII text; padded with spaces."""
    codes = []
    for part in parts:
        codes += [part] if isinstance(part, int) else [ord(c) for c in part]
    codes += [0x20] * (40 - len(codes))
    return bytes(tt.parity_encode(code) for code in codes[:40])


print('table 32: the sets each group and C12-C14 designate')
check('group 4: Cyrillic\'s three options beside the Baltic Latin sub-sets',
      [tt.subset_of(4, option) for option in range(7)],
      ['cyrillic-1', 'german', 'estonian', 'lettish', 'cyrillic-2', 'cyrillic-3', 'czech'])
check('group 6: Turkish and Greek; group 8: Latin and Arabic; group 10: Hebrew and Arabic',
      [tt.subset_of(6, 6), tt.subset_of(6, 7), tt.subset_of(8, 0), tt.subset_of(8, 4), tt.subset_of(8, 7),
       tt.subset_of(10, 5), tt.subset_of(10, 7)],
      ['turkish', 'greek', 'english', 'french', 'arabic', 'hebrew', 'arabic'])
check('the G2 set with each: the Latin sets of the Arabic group take Arabic\'s',
      [tt.g2_of(0, 0), tt.g2_of(4, 4), tt.g2_of(4, 2), tt.g2_of(6, 7), tt.g2_of(8, 0), tt.g2_of(10, 5)],
      ['latin', 'cyrillic', 'latin', 'greek', 'arabic', 'arabic'])
check('a place the table leaves empty: English', (tt.subset_of(3, 0), tt.subset_of(10, 0)), ('english', 'english'))

print('the G0 sets, against the specification\'s tables')
check('Cyrillic option 1 (Serbian/Croatian), 4/0 to 4/F', tt.G0_SETS['cyrillic-1'][0x20:0x30], 'ЧАБЦДЕФГХИЈКЛМНО')
check('and 5/0 to 5/F', tt.G0_SETS['cyrillic-1'][0x30:0x40], 'ПЌРСТУВЃЉЊЗЋЖЂШЏ')
check('Cyrillic option 2 (Russian/Bulgarian): ы at 2/6, 5/0 to 5/F',
      (tt.g0_char(0x26, 'cyrillic-2'), tt.G0_SETS['cyrillic-2'][0x30:0x40]), ('ы', 'ПЯРСТУЖВЬЪЗШЭЩЧЫ'))
check('Cyrillic option 3 (Ukrainian): ї at 2/6, І Є Ї in Ъ Э Ы\'s places',
      (tt.g0_char(0x26, 'cyrillic-3'), tt.g0_char(0x59, 'cyrillic-3'), tt.g0_char(0x5C, 'cyrillic-3'),
       tt.g0_char(0x5F, 'cyrillic-3'), tt.g0_char(0x79, 'cyrillic-3')), ('ї', 'І', 'Є', 'Ї', 'і'))
check('Greek: « » at 3/C and 3/E, ΐ at 4/0, the accented vowels',
      ''.join(tt.g0_char(code, 'greek') for code in (0x3C, 0x3E, 0x40, 0x41, 0x5C, 0x5F, 0x72, 0x7C, 0x7E)),
      '«»ΐΑάίςόώ')
check('Arabic: the brackets mirrored, its comma and question mark, letters in their forms',
      ''.join(tt.g0_char(code, 'arabic') for code in (0x28, 0x29, 0x2C, 0x3F, 0x41, 0x43, 0x47, 0x60, 0x7E)),
      ')(،؟ﺀﺏﺍـﻻ')
check('its 2/6, the tail that ends a letter, read as nothing', tt.g0_char(0x26, 'arabic'), '')
check('Hebrew: £ at 2/3, Latin capitals, the arrows, א to ת and ₪',
      ''.join(tt.g0_char(code, 'hebrew') for code in (0x23, 0x41, 0x5B, 0x5F, 0x60, 0x7A, 0x7B)), '£A←#את₪')
check('every set ends in the block', {table[-1] for table in tt.G0_SETS.values()}, {'■'})
check('the Latin G2 set: ‰ at 5/7, ɑ at 5/8, € at 5/6', (tt.G2_SETS['latin'][0x57], tt.G2_SETS['latin'][0x58],
                                                     tt.G2_SETS['latin'][0x56]), ('‰', 'ɑ', '€'))
check('the Cyrillic G2 set: Ł ł ß, the Latin letters Cyrillic does not look like',
      ''.join(tt.G2_SETS['cyrillic'][code] for code in (0x59, 0x5A, 0x5B, 0x60, 0x6F, 0x70, 0x7F)), 'ŁłßDZdz')
check('the Greek G2 set: Ί Ύ Ώ Ά Ή Έ and Latin letters',
      ''.join(tt.G2_SETS['greek'][code] for code in (0x59, 0x5A, 0x5B, 0x6E, 0x6F, 0x7E, 0x21, 0x60)), 'ΊΎΏΆΉΈaC')
check('the Arabic G2 set: hamzas, Persian letters, Arabic-Indic digits, French',
      ''.join(tt.G2_SETS['arabic'][code] for code in (0x23, 0x2B, 0x2F, 0x30, 0x39, 0x40, 0x60, 0x7E)), 'ﺃﭘﮔ٠٩àéç')

library = ctypes.util.find_library('zvbi')
if not library:
    print('  [SKIP] libzvbi is not installed: the sets are not checked against its tables')
else:
    zvbi = ctypes.CDLL(library)
    zvbi.vbi_teletext_unicode.restype = ctypes.c_uint
    # libzvbi's vbi_character_set: LATIN_G0 1 ... HEBREW_G0 11.
    numbers = {'cyrillic-1': 3, 'cyrillic-2': 4, 'cyrillic-3': 5, 'greek': 7, 'hebrew': 11}
    # Where libzvbi differs, and why: 4/A and 6/A drawn Й (Russian has no
    # other place for it) where it has Ѝ; Ukrainian's ї where it has the
    # Latin ï; Hebrew's £ at 2/3, as the table draws it.
    known = {('cyrillic-2', 0x4A), ('cyrillic-2', 0x6A), ('cyrillic-3', 0x4A), ('cyrillic-3', 0x6A),
             ('cyrillic-3', 0x26), ('hebrew', 0x23)}
    for name, number in numbers.items():
        differ = [f'{code:02X}' for code in range(0x20, 0x80) if (name, code) not in known
                  and chr(zvbi.vbi_teletext_unicode(number, 0, code)) != tt.g0_char(code, name)]
        check(f'libzvbi\'s {name} G0 set is the same', differ, [])
    for name, number in {'cyrillic': 6, 'greek': 8}.items():
        # Column 4 is marks, which libzvbi gives as spacing ones; 5/6 is
        # the euro, which it gives as the older ₠; 2/0 is a space, which
        # it gives as a no-break one; the places the table leaves empty it
        # gives as spaces; and it leaves out the Cyrillic set's $.
        differ = [f'{code:02X}' for code in range(0x21, 0x80) if code >> 4 != 4 and code != 0x56
                  and (name, code) != ('cyrillic', 0x24)
                  and tt.G2_SETS[name][code] != ''
                  and chr(zvbi.vbi_teletext_unicode(number, 0, code)) != tt.G2_SETS[name][code]]
        check(f'libzvbi\'s {name} G2 set is the same', differ, [])

print('X/26 in the other sets')


def x26(*triplets):
    """A packet X/26's 40 bytes from (address, mode, data) triplets."""
    triplets = list(triplets) + [(0x3F, 0x1F, 0)] * (13 - len(triplets))
    return bytes([tt.ham84_encode(0)]) + b''.join(tt.ham2418_encode(a | (m << 6) | (d << 11)) for a, m, d in triplets)


placed = tt._x26(x26((62, 0x04, 0), (5, 0x18, 0x65), (6, 0x0F, 0x60)), 'cyrillic-2', 'cyrillic')
check('a Cyrillic letter with a mark (е and ¨ is ё), a Latin letter from the Cyrillic G2 set',
      (placed[(22, 5)], placed[(22, 6)]), ('ё', 'D'))
placed = tt._x26(x26((62, 0x04, 0), (3, 0x12, 0x4F), (4, 0x0F, 0x59)), 'greek', 'greek')
check('Greek: Ο with an acute is Ό, and Ί from the G2 set', (placed[(22, 3)], placed[(22, 4)]), ('Ό', 'Ί'))
placed = tt._x26(x26((62, 0x04, 0), (1, 0x09, 0x41), (2, 0x08, (4 << 3) | 4), (3, 0x09, 0x41), (4, 0x0F, 0x60)))
check('a G0 character placed, then the sets a triplet designates for those after it',
      (placed[(22, 1)], placed[(22, 3)], placed[(22, 4)]), ('A', 'А', 'D'))
check('X/28/0 Format 1 written and read: group 4, Russian', tt._designation(
    bytes(tt._REVERSED[byte] for byte in tt._x28(8, 4, 4)[1:])[3:43]), (4 << 3) | 4)

print('a page read in each set')
check('a Russian page', tt.render({22: row(' ' * 14, DH, SB, SB, 0x70, 0x72, 0x69, 0x77, 0x65, 0x74, EB)}, 'cyrillic-2'),
      'привет')
check('a Greek page', tt.render({22: row(' ' * 14, DH, SB, SB, 0x4A, 0x61, 0x6B, 0x67, 0x6C, 0x5D, 0x71, 0x61, EB)},
                                'greek'), 'Καλημέρα')
check('a Hebrew page: from the right, in the order it is read',
      tt.render({22: row(' ' * 14, DH, SB, SB, 0x6D, 0x65, 0x6C, 0x79, EB)}, 'hebrew'), 'שלום')
check('Hebrew with a number and a bracket: the number left as it is, the bracket turned round',
      tt.render({22: row(' ' * 12, DH, SB, SB, '(12) ', 0x6D, 0x65, 0x6C, 0x79, EB)}, 'hebrew'), 'שלום (12)')
check('Arabic: the forms read as the letters, lam-alef as its two, the tail as nothing',
      tt.render({22: row(' ' * 14, DH, SB, SB, 0x46, 0x78, 0x55, 0x26, EB)}, 'arabic'), 'صلا')
check('a Latin page is left as it is', tt.render({22: row(' ' * 14, DH, SB, SB, '(12) ab', EB)}), '(12) ab')

print('the set a page is written with')
check('each language its own', [tt.designation_for(language, ['']) for language in
                                ('en', 'fr', 'pl', 'ru', 'bg', 'uk', 'mk', 'el', 'ar', 'fa', 'he', 'rus', 'heb')],
      [(0, 0), (0, 4), (1, 0), (4, 4), (4, 4), (4, 5), (4, 0), (6, 7), (8, 7), (8, 7), (10, 5), (4, 4), (10, 5)])
check('Serbian in Latin, or in Cyrillic', (tt.designation_for('sr', ['Đorđe']), tt.designation_for('sr', ['Ђорђе'])),
      ((3, 5), (4, 0)))
check('a language with no set of its own: the first that holds the text',
      (tt.designation_for('', ['Hello']), tt.designation_for('xx', ['Привет']), tt.designation_for('', ['שלום'])),
      ((0, 0), (4, 4), (10, 5)))
check('Latin capitals on a Cyrillic page: as the letters that look the same',
      tt.designation_for('ru', ['OK, TAXI']), (4, 4))

print('written and read back')
folder = Path(tempfile.mkdtemp())
samples = [
    ('888', 'rus', 'ru', 'Привет, мир! Ещё DJ.'),
    ('889', 'ukr', 'uk', 'Їжак і Євген'),
    ('890', 'srp', 'sr', 'Ђорђе и Љиљана, Џак'),
    ('891', 'srp', 'sr', 'Đorđe i Ljiljana'),
    ('892', 'mac', 'mk', 'Ќе дојдам, Ѓорѓи'),
    ('893', 'gre', 'el', 'Καλημέρα κόσμε; Ό,τι ΐ'),
    ('894', 'ara', 'ar', 'مرحبا بالعالم (2024)'),
    ('895', 'ara', 'ar', 'لا، شكرا. أنا هنا'),
    ('896', 'heb', 'he', 'שלום עולם 123 OK'),
    ('897', 'heb', 'he', '{\\an8}שלום (עולם)\nשורה שנייה'),
    ('898', 'pol', 'pl', 'Zażółć gęślą jaźń'),
    ('899', 'tur', 'tr', 'Ağaç ışık'),
]
pages = [{'page': page, 'language': iso, 'subtitld_language': code, 'type': 2,
          'segments': [{'start': 1.0, 'end': 2.0, 'text': text}]} for page, iso, code, text in samples]
(folder / 'scripts.ts').write_bytes(tt.transport_stream(pages, start=10.0))
for page, _iso, code, text in samples:
    check(f'{code}: {text!r}', [s['text'] for s in tt.read(folder / 'scripts.ts', page=page)[0]], [text])
check('Ukrainian\'s Ґ: written as Г',
      [s['text'] for s in tt.decode([(0, f) for f in tt.page_fields('Ґанок', language='uk')]
                                    + [(90000, f) for f in tt.page_fields('', language='uk')], origin=0)], ['Ганок'])
check('Persian\'s yeh and kaf: as Arabic\'s',
      [s['text'] for s in tt.decode([(0, f) for f in tt.page_fields('یک', language='fa')]
                                    + [(90000, f) for f in tt.page_fields('', language='fa')], origin=0)], ['يك'])
check('a Latin capital the Cyrillic G2 set leaves out: written as the Cyrillic letter that looks the same',
      [s['text'] for s in tt.decode([(0, f) for f in tt.page_fields('TAXI', language='ru')]
                                    + [(90000, f) for f in tt.page_fields('', language='ru')], origin=0)], ['ТАХI'])
check('the page\'s X/28/0 is sent only where a set is designated',
      [len(tt.page_fields('Hello', language='en')), len(tt.page_fields('Привет', language='ru'))], [2, 3])

ffmpeg = shutil.which('ffmpeg')
decoders = subprocess.run([ffmpeg, '-hide_banner', '-decoders'], capture_output=True, text=True).stdout if ffmpeg else ''
if 'libzvbi_teletextdec' not in decoders:
    print('  [SKIP] FFmpeg with libzvbi is not installed: the pages are not checked against it')
else:
    def zvbi_page(path, page):
        out = subprocess.run([ffmpeg, '-v', 'error', '-txt_format', 'text', '-txt_page', page, '-i', str(path),
                              '-map', '0:s:0', '-f', 'srt', '-'], capture_output=True, text=True).stdout
        return ' '.join(out.strip().split('\n')[2:]).strip()
    # libzvbi composes a mark over the Latin letter, whatever the set: ë
    # for ё, Ó for Ό. Otherwise it reads what is written.
    check('libzvbi reads the Russian page in Cyrillic option 2, by its X/28/0',
          zvbi_page(folder / 'scripts.ts', '888'), 'Привет, мир! Ещë DJ.')
    check('the Serbian Cyrillic page in option 1', zvbi_page(folder / 'scripts.ts', '890'), 'Ђорђе и Љиљана, Џак')
    check('the Greek page', zvbi_page(folder / 'scripts.ts', '893'), 'Καλημέρα κόσμε; Ó,τι ΐ')
    check('the Hebrew page, as it sits on the page: from the right',
          zvbi_page(folder / 'scripts.ts', '896'), ''.join(tt.visual_order('שלום עולם 123 OK')))
    arabic = zvbi_page(folder / 'scripts.ts', '895')
    check('the Arabic page: in libzvbi\'s own code points for the forms, a cell each, lam-alef one of them',
          len(arabic), len('لا، شكرا. أنا هنا') - 1)
    check('and a number in brackets, as it sits on the page', zvbi_page(folder / 'scripts.ts', '894')[:7], '(2024) ')
    check('the Turkish page, in its group', zvbi_page(folder / 'scripts.ts', '899'), 'Ağaç ışık')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
