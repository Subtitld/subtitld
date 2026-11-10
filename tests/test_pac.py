"""Screen PAC and UniPac: reading files built here byte by byte (records
and their lengths, timecodes in frames, rows and alignment, Latin's accents
before their letter, the Cyrillic and Greek pages over ASCII, Hebrew,
Korean after W16, UniPac's UTF-8, italics, colours, comments, a STORY
header), finding the code page no file names, writing every code page and
reading it back, a second code page for the lines the first cannot hold,
and the export's options.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, pac, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def tc(hours, minutes, seconds, frames):
    return (hours * 100 + minutes).to_bytes(2, 'little') + (seconds * 100 + frames).to_bytes(2, 'little')


def record(number, start, end, row, lines, lead=b''):
    """A record: lines are (alignment, bytes)."""
    body = bytes([row]) + lead + b''.join(bytes([0xFE, alignment, 0x03]) + text for alignment, text in lines)
    return b'\x00' + number.to_bytes(2, 'little') + b'\x60' + tc(*start) + tc(*end) + len(body).to_bytes(2, 'little') + body


def pac_file(*records):
    return b'\x01' + bytes(19) + b''.join(records) + b'\xff' + bytes(11) + b'\x11\x00dummy end of file'


def texts(data, **options):
    return [s['text'] for s in pac.parse(data, **options)[0]]


print('reading Latin')
LATIN = pac_file(
    record(0, (0, 0, 1, 12), (0, 0, 2, 0), 10, [
        (2, b"You're a <jerk>, Celia."),
        (2, b'Caf\xe2e, \xe0ao, \xe5u, \x81, \xe7g \x80' + b'1')]),
    record(1, (0, 0, 3, 0), (0, 0, 4, 0), 1, [(1, b'\x03Yellow\x07 and <white'), (1, b'<whole line')]),
    record(2, (0, 0, 5, 0), (0, 0, 6, 0), 3, [(0, b'Lower than the top')]),
    record(3, (0, 0, 7, 0), (0, 0, 8, 0), 6, [(2, b'Middle\xffof it')]),
    record(4, (0, 0, 9, 0), (0, 0, 10, 0), 11, [(2, b'Before a comment\x1fC1 kept\x1fCnot this')]),
    record(5, (0, 1, 2, 24), (1, 0, 0, 0), 9, [(2, b'Higher than the bottom')]),
)
segments, settings = pac.parse(LATIN)
check('times: HHMM and SSFF at 25 frames', [(s['start'], s['end']) for s in segments],
      [(1.48, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0), (9.0, 10.0), (62.96, 3600.0)])
check('italics in place of a space, accents before their letter, ß, the accent alone, # moved', segments[0]['text'],
      "You're a <i>jerk</i>, Celia.\nCafé, ão, ü, ß, ǧ #1")
check('top left, a colour, italics to the end of the line and from the start of the next', segments[1]['text'],
      '{\\an7}<font color="#ffff00">Yellow</font> and <i>white</i>\n<i>whole line</i>')
check('right at row 3: the top, a quarter down', (segments[2]['text'], segments[2].get('position')),
      ('{\\an9}Lower than the top', {'v': '25%'}))
check('row 6 is the middle; FF a space', (segments[3]['text'], segments[3].get('position')), ('{\\an5}Middle of it', None))
check('1F C and a digit left out; 1F C and anything else a comment', segments[4]['text'], 'Before a comment kept')
check('row 9 for one line: the bottom, two rows up', (segments[5]['text'], segments[5].get('position')),
      ('Higher than the bottom', {'v': '16.7%'}))
check('Latin found, no language said', settings, {'code_page': 'latin', 'secondary_code_page': '', 'language': '',
                                                  'unicode': False})
check('at 30 frames', pac.parse(LATIN, fps=30)[0][0]['start'], 1.4)

print('finding the code page')
RUSSIAN = pac_file(record(0, (0, 0, 1, 0), (0, 0, 2, 0), 11, [(2, b'Z yt pyf. xnj \'nj^ yt pyf.')]))
cyrillic = pac.parse(RUSSIAN)
check('Cyrillic over ASCII, as a Russian typewriter lays it', (cyrillic[0][0]['text'], cyrillic[1]['code_page']),
      ('Я не знаю что это, не знаю', 'cyrillic'))
GREEK = pac_file(record(0, (0, 0, 1, 0), (0, 0, 2, 0), 11, [(2, b'Dem ha to j\xe2amy jai ma e\xe2imai ec\xe2y.')]))
greek = pac.parse(GREEK)
check('Greek over ASCII, its acute before the letter; Greek said', (greek[0][0]['text'], greek[1]['code_page'],
                                                                    greek[1]['language']),
      ('Δεν θα το κάνω και να είναι εγώ.', 'greek', 'el'))
HEBREW = pac_file(record(0, (0, 0, 1, 0), (0, 0, 2, 0), 11, [(1, b'\xa0\xb0\xa9 \xac\xa0 \xa9\xa5\xa3\xb2, cba 4202.')]))
hebrew = pac.parse(HEBREW)
check('Hebrew from A0, its Latin runs put back in reading order', (hebrew[0][0]['text'], hebrew[1]['code_page']),
      ('{\\an1}אני לא יודע, abc 2024.', 'hebrew'))
KOREAN = pac_file(record(0, (0, 0, 1, 0), (0, 0, 2, 0), 11, [
    (2, b'\x1fW16.' + '안녕하세요'.encode('cp949') + b'\x00 \x00<' + '친구'.encode('cp949') + b'\x00>'),
    (2, b'Hello')]))
korean = pac.parse(KOREAN)
check('Korean after W16, two bytes a character, one after 00; a Latin line as Latin',
      (korean[0][0]['text'], korean[1]['code_page'], korean[1]['language']),
      ('안녕하세요 <i>친구</i>\nHello', 'korean', 'ko'))
check('a code page asked for is the one read', texts(RUSSIAN, code_page='latin'), ["Z yt pyf. xnj 'nj÷ yt pyf."])
STORY = pac_file(record(0, (0, 0, 0, 0), (0, 0, 0, 0), 0, [(2, b'STORY:My film'), (2, b'LANG:GRC')]),
                 record(1, (0, 0, 1, 0), (0, 0, 2, 0), 11, [(2, b'Jai')]))
story = pac.parse(STORY)
check("a STORY header is no subtitle; its LANG: names the page and the language",
      ([s['text'] for s in story[0]], story[1]['code_page'], story[1]['language']), (['Και'], 'greek', 'el'))
UNIPAC = pac_file(record(0, (0, 0, 1, 0), (0, 0, 2, 0), 11, [
    (2, b'\x1f\xef\xbb\xbf' + 'Ação<!> Привет'.encode('utf-8') + b'\xff\x2e'),
    (2, b'\x1f\xef\xbb\xbf' + 'こんにちは'.encode('utf-8') + b'\x2e')], lead=b'\x80\x80\x80'))
unipac = pac.parse(UNIPAC)
check("UniPac: 80 80 80, UTF-8 to a full stop, FF its own full stop", (unipac[0][0]['text'], unipac[1]['unicode']),
      ('Ação <i>!</i> Привет.\nこんにちは', True))
for name, data in (('not PAC', b'1\n00:00:01,000 --> 00:00:02,000\nHello\n'),
                   ('a header and no records', b'\x01' + bytes(60))):
    try:
        pac.parse(data)
        check(f'{name}: refused', None, 'ValueError')
    except ValueError as error:
        check(f'{name}: refused', str(error).startswith('Not a PAC file'), True)
check('is_pac', (pac.is_pac(LATIN), pac.is_pac(UNIPAC), pac.is_pac(b'\x01' + bytes(60)), pac.is_pac(b'WEBVTT\n' * 10)),
      (True, True, False, False))

print('writing')
written = [
    {'start': 1.48, 'end': 2.0, 'text': "You're a <i>jerk</i>, Celia.\n#1 is <i>here</i>"},
    {'start': 3.0, 'end': 4.0, 'text': '{\\an7}<font color="#ffd700">Gold</font> and white'},
    {'start': 5.0, 'end': 6.0, 'text': '{\\an9}Lower than the top', 'position': {'v': '25%'}},
    {'start': 7.0, 'end': 8.0, 'text': '{\\an5}Middle'},
    {'start': 9.0, 'end': 10.0, 'text': 'Higher than the bottom', 'position': {'v': '16.7%'}},
    {'start': 11.0, 'end': 12.0, 'text': ' <i> </i> '},
    {'start': 62.96, 'end': 3600.0, 'text': 'Café, ão, ü, ß, ǧ, ő, ł, Ł, ç, ñ — “quoted‘'},
]
data = pac.write(written)
check('the header, the first record\'s number and its 60', data[:24], b'\x01' + bytes(22) + b'\x60')
check('times as HHMM and SSFF', data[24:32], tc(0, 0, 1, 12) + tc(0, 0, 2, 0))
check('the length counts the row and the lines', int.from_bytes(data[32:34], 'little'), len(data[34:data.index(b'\x00\x01\x00\x60')]))
check('row 10 for two lines at the bottom; FE, centred, 03; italics in place of spaces',
      data[34:34 + 4 + len(b"You're a<jerk>, Celia.")], b"\x0a\xfe\x02\x03You're a<jerk>, Celia.")
check('ends as PAC files end', data.endswith(b'\xff' + bytes(11) + b'\x11\x00dummy end of file'), True)
back, info = pac.parse(data)
check('read back: italics, colours, places, margins, accents; an empty subtitle left out', [
    (s['start'], s['end'], s['text'], s.get('position')) for s in back], [
    (1.48, 2.0, "You're a <i>jerk</i>, Celia.\n#1 is <i>here</i>", None),
    (3.0, 4.0, '{\\an7}<font color="#ffff00">Gold</font> and white', None),
    (5.0, 6.0, '{\\an9}Lower than the top', {'v': '25%'}),
    (7.0, 8.0, '{\\an5}Middle', None),
    (9.0, 10.0, 'Higher than the bottom', {'v': '16.7%'}),
    (62.96, 3600.0, 'Café, ão, ü, ß, ǧ, ő, ł, Ł, ç, ñ — “quoted‘', None)])
check('Latin chosen and found', info['code_page'], 'latin')
for fps, start in ((29.97, 1.467), (30, 1.467), (23.976, 1.459), (24, 1.458)):
    check(f'{fps} frames a second', pac.parse(pac.write([{'start': 1.466, 'end': 2, 'text': 'x'}], fps=fps), fps=fps)[0][0]['start'],
          start)
check('a time near the next second rounds into it', pac.write([{'start': 1.999, 'end': 3, 'text': 'x'}])[24:28], tc(0, 0, 2, 0))
check('what has no place: its letter without accents, a quote like it, else "?"; "<" and ">" left out',
      texts(pac.write([{'start': 1, 'end': 2, 'text': 'Ǎ ∑ ”1” < 2 > 0'}])), ['A ? "1" 2 0'])

print('every code page, there and back')
SAMPLES = {
    'latin': ('en', "You're a <i>jerk</i>, Celia.\nWhat do you know? [loud] 50% & more"),
    'latin-portuguese': ('pt-br', 'Ação, café — não é você?\n- Sim, sou eu. - Não!'),
    'latin-czech': ('cs-cz', 'Je to Říp, že ano? Čeština je tak hezká.'),
    'latin-turkish': ('tr-tr', 'Bu bir değil, ben çok iyi. Ağır şey ılık.'),
    'greek': ('el-gr', 'Δεν θα το κάνω, και να είναι εγώ;\nΌχι! <i>Ναι.</i> 2024'),
    'cyrillic': ('ru-ru', 'Я не знаю, что это. Ты и он?\nДа! <i>Нет.</i> 2024'),
    'hebrew': ('he-il', 'אני לא יודע מה זה, abc 2024.\n<i>כן</i> (לא)'),
    'arabic': ('ar-eg', 'أنا لا أعرف ما هذا؟ مع السلامة\nمَرحبا 2024'),
    'thai': ('th-th', 'สวัสดีครับ ผมไม่รู้\nเมื่อวาน 2024'),
    'korean': ('ko-kr', '안녕하세요, 저는 모르겠어요.\n<i>친구</i> Hello'),
    'japanese': ('ja-jp', 'こんにちは、私は知りません。\nカタカナ'),
    'chinese-simplified': ('cmn-hans-cn', '我们不知道这是什么，你说呢？'),
    'chinese-traditional': ('cmn-hant-tw', '我們不知道這是什麼，你說呢？'),
}
for page, (language, text) in SAMPLES.items():
    segments = [{'start': 1.0, 'end': 2.0, 'text': text}]
    data = pac.write(segments, language=language)
    back, info = pac.parse(data)
    check(f'{page}: chosen from the language, found again, read back',
          (info['code_page'], [s['text'] for s in back]), (page, [text]))
    data = pac.write(segments, unicode=True)
    back, info = pac.parse(data)
    check(f'{page}: as UniPac', ([s['text'] for s in back], info['unicode']), ([text], True))
check('no language: the page the characters ask for', [pac.choose_code_page([text]) for text in (
    'Hello', 'Привет', 'Γεια', 'שלום', 'مرحبا', 'สวัสดี', '안녕', 'こんにちは', '这们说', '這們說')], [
    'latin', 'cyrillic', 'greek', 'hebrew', 'arabic', 'thai', 'korean', 'japanese', 'chinese-simplified',
    'chinese-traditional'])
check('a page asked for that holds the text is the one written', pac.choose_code_page(['Hello'], 'en', 'latin-czech'),
      'latin-czech')
check('one that does not gives way', pac.choose_code_page(['Привет'], 'en', 'greek'), 'cyrillic')
check('Arabic: a vowel mark written before its letter', pac.write([{'start': 1, 'end': 2, 'text': 'مَ'}],
                                                                  language='ar')[38:40], b'\xe5\x98')
mixed = pac.write([{'start': 1, 'end': 2, 'text': 'אני לא יודע\nЯ не знаю, что это'}], language='he')
check('a line the first page cannot hold in a second one, marked 08', (mixed.count(b'\xfe\x02\x03'),
                                                                      mixed.count(b'\xfe\x0a\x03')), (1, 1))
mixed_back = pac.parse(mixed)
check('and read back, each line in its page', (mixed_back[0][0]['text'], mixed_back[1]['code_page'],
                                               mixed_back[1]['secondary_code_page']),
      ('אני לא יודע\nЯ не знаю, что это', 'hebrew', 'cyrillic'))

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.pac').write_bytes(GREEK)
session.FORMAT = {}
session.VIDEO = {}
session.SUBTITLE['language'] = 'pt-br'
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.pac'))
check("opens; its language the project's; the rate and the page kept to save it again", (
    fmt, len(opened), session.SUBTITLE['language'],
    {key: session.FORMAT['options'].get(key) for key in ('fps', 'pac_code_page')}),
      ('PAC', 1, 'el-gr', {'fps': 25.0, 'pac_code_page': 'greek'}))
session.FORMAT = {}
session.VIDEO = {'framerate': 30.0}
(folder / 'in.fpc').write_bytes(UNIPAC)
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.fpc'))
check("a .fpc opens as UniPac, at the video's rate", (fmt, opened[0]['text'][:4], session.FORMAT['options']['fps']),
      ('FPC', 'Ação', 30.0))
session.SUBTITLE['segments'] = [{'start': 1.5, 'end': 2.5, 'text': 'Γεια σου, <i>κόσμε</i>'},
                                {'start': 3.0, 'end': 4.0, 'text': '{\\an8}Δεύτερο'}]
session.SUBTITLE['language'] = 'el-gr'
session.VIDEO = {'framerate': 25.0}
session.FORMAT = {'options': {'pac_code_page': 'auto'}}
file_io.save_file(str(folder / 'out.pac'), 'PAC')
saved = (folder / 'out.pac').read_bytes()
check("saved: the video's rate, the language's page", (saved[24:28], pac.parse(saved)[1]['code_page'],
                                                       texts(saved)), (tc(0, 0, 1, 12), 'greek',
                                                                       ['Γεια σου, <i>κόσμε</i>', '{\\an8}Δεύτερο']))
session.FORMAT = {'options': {'fps': 30.0}}
file_io.save_file(str(folder / 'out.fpc'), 'FPC')
saved = (folder / 'out.fpc').read_bytes()
check('saved as UniPac at the rate asked', (saved[24:28], saved[35:38], texts(saved, fps=30)),
      (tc(0, 0, 1, 15), b'\x80\x80\x80', ['Γεια σου, <i>κόσμε</i>', '{\\an8}Δεύτερο']))
(folder / 'not.pac').write_bytes(b'Not a PAC file at all\n' * 10)
try:
    file_io.process_subtitles_file(str(folder / 'not.pac'))
    check('not PAC: says what it is not', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check('not PAC: says what it is not', str(error).startswith('Not a PAC file'), True)
check('both in the list of formats', (session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['PAC']['extensions'],
                                      session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['FPC']['extensions']),
      (['pac'], ['fpc']))

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.VIDEO = {'framerate': 29.97}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('PAC')
panel._refresh_options()
check("PAC: the frame rate (the video's) and the code page (automatic)", (
    panel.sub_options.isHidden(), panel.pac_options.isHidden(), panel.text_options.isHidden(),
    {key: panel.get_config()['options'].get(key) for key in ('fps', 'pac_code_page')}),
      (False, False, False, {'fps': 29.97, 'pac_code_page': 'auto'}))
check('every code page offered', [panel.pac_code_page_combo.combobox.itemData(index)
                                  for index in range(panel.pac_code_page_combo.combobox.count())],
      ['auto'] + list(pac.CODE_PAGES))
panel.pac_code_page_combo.combobox.setCurrentIndex(panel.pac_code_page_combo.combobox.findData('cyrillic'))
check('chosen', panel.get_config()['options'].get('pac_code_page'), 'cyrillic')
check('remembered', _SubtitlesPanel().pac_code_page_combo.combobox.currentData(), 'cyrillic')
panel.format_combo.setCurrentText('FPC')
panel._refresh_options()
check('UniPac: the frame rate, no code page', (panel.sub_options.isHidden(), panel.pac_options.isHidden(),
                                                'pac_code_page' in panel.get_config()['options']), (False, True, False))
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('not for SRT', (panel.sub_options.isHidden(), panel.pac_options.isHidden()), (True, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
