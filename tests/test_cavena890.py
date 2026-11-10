"""Cavena 890: reading files built here byte by byte at the offsets Subtitle
Edit's reader and writer use (the 388-byte header, 128-byte blocks, the
last without its closing zeros), in each character table (Latin with its
accents before their letters, Danish and English apart, Russian, Greek,
Arabic, Hebrew stored from the right, Chinese in UTF-16), with italics,
colours, the box, the alignment, blocks that continue a subtitle and a
programme start of 10:00:00:00; writing what they take, round trips, and
the file and the export dialog.

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
from subtitld.modules import cavena890, file_io, markup, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def header(language, font, title=b'', count=0, som=b'10:00:00:00'):
    """A header, each field put at its offset."""
    data = bytearray(388)
    data[40:40 + len(title)] = title
    data[134:146] = bytes.fromhex('a005040306060890 00000000')
    data[146] = data[147] = language
    data[172:179] = bytes.fromhex('08900000000000')
    data[179:181] = data[208:210] = data[210:212] = bytes([count % 256, count // 256])
    data[195 - len(font):195] = font
    data[246:254] = font
    data[254:256] = b'\x3d\x8d'
    data[256:267] = som
    data[293:296] = b'BTD'
    return bytes(data)


def block(number, start, end, line1=b'', line2=b'', align=0x54, box=0, filler=b'\x7f'):
    return (number.to_bytes(2, 'big') + start.to_bytes(3, 'big') + end.to_bytes(3, 'big') + bytes([align]) + bytes(7)
            + line1.ljust(51, filler) + bytes([0, 0, 0, box, 0, 0]) + line2.ljust(51, filler) + bytes(4))


def build(head, *blocks):
    return (head + b''.join(blocks))[:-4]


TEN = 10 * 3600 * 25
DANISH = build(
    header(0x07, b'VFONTL.V', b'Tears of Steel', 5),
    block(16, TEN + 25, TEN + 62, b'\x88Hej\x98 med dig,', b'S\x1b, \x1c og \x1d \x5b\x5c\x5d'),
    block(32, TEN + 75, TEN + 100, line2=b'Caf\x82e na\x86ive \x84cesky \x8ca \xeb \xe5', align=0x50),
    block(48, TEN + 125, TEN + 150, line2=b'\xf3Gul\xf7 og \xf1r\x1cd'),
    block(64, TEN + 175, TEN + 200, line2=b'Boxed', align=0x52, box=0xA0),
    block(80, TEN + 225, TEN + 250, b'One', b'Two'),
    block(80, TEN + 225, TEN + 250, b'Three', b'Four'),
)

print('reading')
segments, settings = cavena890.parse(DANISH)
check('times from the 10:00:00:00 start; italics; æøå ÆØÅ; accents before letters; the alignment; colours; '
      'the box; a block continued', [(s['start'], s['end'], s['text']) for s in segments], [
          (1.0, 2.48, '<i>Hej</i> med dig,\nSæ, ø og å ÆØÅ'),
          (3.0, 4.0, '{\\an1}Café naïve česky å ♪ å'),
          (5.0, 6.0, '<font color="#ffff00">Gul</font> og <font color="#ff0000">rød</font>'),
          (7.0, 8.0, '{\\an3}<font background="#000000">Boxed</font>'),
          (9.0, 10.0, 'One\nTwo\nThree\nFour')])
check('its settings', settings, {'language': 'da', 'fps': 25.0, 'start': '10:00:00:00', 'title': 'Tears of Steel'})
ENGLISH = build(header(0x09, b'HLV23N.V', count=1),
                block(16, 25, 50, line2=b'\xe5x\xe6 \xe2 \x82E \x87s\x89a\x8ez\x8fe \xbe'))
segments, settings = cavena890.parse(ENGLISH)
check('English: E5 and E6 are brackets; @; the cedilla, breve, dot and ogonek; times as they are, before the start',
      (segments[0]['text'], segments[0]['start'], settings['start'], settings['language']),
      ('[x] @ É şăżę -', 1.0, '00:00:00:00', 'en'))
check('29.97: frames counted at that rate', cavena890.parse(ENGLISH, 29.97)[0][0]['end'], round(50 / 29.97, 3))
TABLES = {
    'Russian': (0x56, b'KYRIL4.V', b'\x50\x72\x69\x76\x65\x74, \x40\x6f\x72\x61', 'Привет, Жора', 'ru'),
    'Greek': (0x70, b'GREEK4.V', b'\x4a\x61\x6b\x67 \x91\x92 \xb1', 'Καλη AB a', 'el'),
    'Arabic': (0x80, b'ARA19N.V', b'\x58\x4a\x46\x42\x41', 'مرحبا', 'ar'),
    'Hebrew': (0x8f, b'HEBNOA.V', b'\x59\x4c\x45\x4d 21 \xb3\xb2\xb1', 'שלום abc 12', 'he'),
}
for name, (language, font, data, want, code) in TABLES.items():
    found, info = cavena890.parse(build(header(language, font, count=1), block(16, 25, 50, line2=data)))
    check(f'{name}: its table', (found[0]['text'], info['language']), (want, code))
check('the font says the table too, when the language byte does not', cavena890.parse(build(
    header(0x09, b'KYRIL4.V', count=1), block(16, 25, 50, line2=b'\x50\x72\x69\x76\x65\x74')))[0][0]['text'], 'Привет')
for language, code in ((0x91, 'zh'), (0x90, 'zh-tw')):
    found, info = cavena890.parse(build(header(language, b'CCKM44.V', count=1), block(
        16, 25, 50, line2=bytes.fromhex('00884f60597d0098'), filler=b'\x00')))
    check(f'Chinese ({code}): UTF-16, its italics a pair', (found[0]['text'], info['language']), ('<i>你好</i>', code))
try:
    cavena890.parse(b'too short')
    check('not 890: refused', None, 'ValueError')
except ValueError as error:
    check('not 890: refused', str(error).startswith('Not a Cavena 890 file'), True)

print('writing')
danish_segments = [{'start': 1.0, 'end': 2.48, 'text': '<i>Hej</i> med dig,\nSæ, ø og å ÆØÅ'},
                   {'start': 3.0, 'end': 4.0, 'text': '{\\an1}Café naïve česky ♪'}]
written = cavena890.write(danish_segments, language='da', title='Tears of Steel')
check('the header, field by field', written[:388], header(0x07, b'VFONTL.V', b'Tears of Steel', 2))
check('a block a subtitle, the last without its closing zeros', len(written), 388 + 128 * 2 - 4)
first = written[388:388 + 128]
check('number, start and end in frames, centred',
      (first[0:2].hex(), first[2:5].hex(), first[5:8].hex(), first[8]), ('0010', '000019', '00003e', 0x54))
check('the lines: italics as 88 and 98, æøå ÆØÅ at their bytes, filled with 7F', (
    first[16:16 + 51].rstrip(b'\x7f'), first[73:73 + 51].rstrip(b'\x7f')),
      (b'\x88Hej\x98 med dig,', b'S\x1b, \x1c og \x1d \x5b\x5c\x5d'))
second = written[388 + 128:]
check('one line: in the second, the first all 7F; left; accents before their letters',
      (second[16:16 + 51], second[8], second[73:73 + 51].rstrip(b'\x7f')),
      (b'\x7f' * 51, 0x50, b'Caf\x82e na\x86ive \x84cesky \xeb'))
check('brackets: E5 and E6 in English, none in Danish', (
    cavena890.write([{'start': 0, 'end': 1, 'text': '[x]'}], language='en')[388 + 73:388 + 76],
    cavena890.write([{'start': 0, 'end': 1, 'text': '[x]'}], language='da')[388 + 73:388 + 76]),
      (b'\xe5x\xe6', b'?x?'))
check('the alignment for the nine places (the top not known: at the bottom)',
      [cavena890.write([{'start': 0, 'end': 1, 'text': '{\\an%d}x' % keypad}])[388 + 8] for keypad in range(1, 10)],
      [0x50, 0x54, 0x52] * 3)
check('colours, and white again', cavena890.write([{'start': 0, 'end': 1, 'text': 'a <font color="#00ffff">b</font> c'}])[
    388 + 73:388 + 80], b'a \xf6b\xf7 c')
boxed = cavena890.write([{'start': 0, 'end': 1, 'text': '<font background="#000000">In a box</font>'}])
check('a box behind all of it', boxed[388 + 70], 0xA0)
many = cavena890.write([{'start': 0, 'end': 1, 'text': 'One\nTwo\nThree'}])
check('three lines: a second block with the same number', (len(many), many[388:390], many[388 + 128:388 + 130]),
      (388 + 256 - 4, b'\x00\x10', b'\x00\x10'))
check('a line longer than 51 bytes: broken at a space', cavena890.parse(cavena890.write(
    [{'start': 0, 'end': 1, 'text': 'A very long line that goes on past fifty one bytes of text for sure'}]))[0][0]['text'],
      'A very long line that goes on past fifty one bytes\nof text for sure')
check('the table by the letters when the language would not hold them; Latin languages by their code', [
    cavena890.write([{'start': 0, 'end': 1, 'text': text}], language=language)[146]
    for language, text in (('en', 'Привет'), ('ru', 'Hello'), ('pt-BR', 'Olá'), ('nb', 'Hei'), ('zh-Hant', '你好'),
                           ('', '你好'), ('ja', 'Hello'), ('', 'שלום'))],
      [0x56, 0x09, 0x21, 0x1E, 0x90, 0x91, 0x09, 0x8F])
check('Hebrew: stored from the right, its Latin runs turned round', cavena890.write(
    [{'start': 0, 'end': 1, 'text': 'שלום abc 12'}], language='he')[388 + 73:388 + 84], b'\x59\x4c\x45\x4d 21 \xb3\xb2\xb1')
check('Russian: Ж at 40 (a guess), lookalikes as Cyrillic', cavena890.write(
    [{'start': 0, 'end': 1, 'text': 'Жора OK'}], language='ru')[388 + 73:388 + 80], b'\x40\x6f\x72\x61 \x4f\x4b')
check('Chinese: UTF-16, filled with zeros', cavena890.write(
    [{'start': 0, 'end': 1, 'text': '<i>你好</i>'}], language='zh')[388 + 73:388 + 73 + 51],
      bytes.fromhex('00884f60597d0098').ljust(51, b'\x00'))
check('Greek: no italics (98 is a letter there)', cavena890.parse(cavena890.write(
    [{'start': 0, 'end': 1, 'text': '<i>Καλημέρα</i>'}], language='el'))[0][0]['text'], 'Καλημέρα')
ten = cavena890.write([{'start': 1.0, 'end': 2.0, 'text': 'x'}], start='10:00:00:00')
check('from a 10:00:00:00 start: frames counted from it, read back from it', (
    int.from_bytes(ten[390:393], 'big'), ten[256:267], cavena890.parse(ten)[1]['start'], cavena890.parse(ten)[0][0]['start']),
      (TEN + 25, b'10:00:00:00', '10:00:00:00', 1.0))

print('round trips')
trips = {
    'da': [{'start': 1.0, 'end': 2.48, 'text': '<i>Hej</i> med dig,\nSæ, ø og å ÆØÅ'},
           {'start': 3.0, 'end': 4.0, 'text': '{\\an1}<font color="#ffff00">Gul</font> og rød'},
           {'start': 5.0, 'end': 6.0, 'text': '{\\an3}<font background="#000000">Boxed</font>'}],
    'en': [{'start': 1.0, 'end': 2.0, 'text': '[Café] naïve: À È Ì Ò Ù\nà è ì ò ù Á É Í Ó Ú\ná é í ó ú â ê î ô û'},
           {'start': 2.0, 'end': 3.0, 'text': 'ã õ ñ ä ë ï ö ü ÿ\nČeština ŠŽ č ď ě ň ř\nš ť ž; Polski ą ę ż\nł đ Đ; Română ă ş ţ Ă Ş Ţ ♪'}],
    'ru': [{'start': 1.0, 'end': 2.0, 'text': 'Съешь же ещё этих мягких французских булок'.replace('ъ', 'ь').replace('ё', 'е').replace('ц', 'с')},
           {'start': 2.0, 'end': 3.0, 'text': 'ЖЙЛХШЩЬЮ АБВГДЕЗИКМНОПРСТУФЧЫЭЯ'}],
    'el': [{'start': 1.0, 'end': 2.0, 'text': 'Ξεσκεπάζω την ψυχοφθόρα βδελυγμία'}],
    'ar': [{'start': 1.0, 'end': 2.0, 'text': 'مرحبا بكم'}],
    'he': [{'start': 1.0, 'end': 2.0, 'text': '<i>שלום</i> עולם, ABC 2024!'}],
    'zh': [{'start': 1.0, 'end': 2.0, 'text': '早上好\n<i>你好世界</i>'}],
    'zh-TW': [{'start': 1.0, 'end': 2.0, 'text': '早安，世界'}],
}
for language, trip in trips.items():
    check(f'{language}: written and read back', cavena890.parse(cavena890.write(trip, language=language))[0], trip)
ntsc = [{'start': round(frames / 29.97, 3), 'end': round((frames + 30) / 29.97, 3), 'text': text}
        for frames, text in ((30, 'At 29.97'), (107892, 'An hour in'))]
check('at 29.97, frames counted at that rate', cavena890.parse(cavena890.write(ntsc, fps=29.97), 29.97)[0], ntsc)
check('what it has not (bold, underline) left out', cavena890.parse(cavena890.write(
    [{'start': 0, 'end': 1, 'text': '<b>Bold</b> <u>under</u>'}]))[0][0]['text'], 'Bold under')
check('each line a background, so a box', [style.background for _run, style in markup.spans(
    cavena890.parse(cavena890.write([{'start': 0, 'end': 1, 'text': '<font background="#000000">a\nb</font>'}]))[0][0]['text'])],
      ['#000000'])

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.890').write_bytes(DANISH)
session.FORMAT = {}
session.VIDEO = {}
session.SUBTITLE['language'] = 'en'
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.890'))
check("opens; its language the project's, its title too; its rate and start kept", (
    fmt, len(opened), session.SUBTITLE['language'], session.SUBTITLE.get('metadata'),
    {key: session.FORMAT['options'].get(key) for key in ('fps', 'start')}),
      ('CAVENA890', 5, 'da-dk', {'title': 'Tears of Steel'}, {'fps': 25.0, 'start': '10:00:00:00'}))
session.SUBTITLE['segments'] = trips['da']
session.VIDEO = {'framerate': 25.0, 'filepath': '/videos/tears.mov'}
session.FORMAT['options'] = dict(session.FORMAT['options'])
file_io.save_file(str(folder / 'out.890'), 'CAVENA890')
saved = (folder / 'out.890').read_bytes()
back, info = cavena890.parse(saved)
check('saved as it was opened: Danish, the title, from 10:00:00:00', (back, info),
      (trips['da'], {'language': 'da', 'fps': 25.0, 'start': '10:00:00:00', 'title': 'Tears of Steel'}))
session.SUBTITLE.pop('metadata', None)
session.SUBTITLE['language'] = 'pt-br'
session.FORMAT = {'options': {}}
file_io.save_file(str(folder / 'plain.890'), 'CAVENA890')
check("no title of its own: the video's name; Portuguese", (cavena890.read(str(folder / 'plain.890'))[1]['title'],
      (folder / 'plain.890').read_bytes()[146]), ('tears', 0x21))
check('the extension is 890\'s', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['CAVENA890']['extensions'], ['890'])

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.VIDEO = {'framerate': 25.0}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('CAVENA890')
panel._refresh_options()
check("Cavena 890: the frame rate, the video's, and the programme start",
      (panel.sub_options.isHidden(), panel.cavena_options.isHidden(), panel.text_options.isHidden(),
       {key: panel.get_config()['options'].get(key) for key in ('fps', 'start')}),
      (False, False, False, {'fps': 25.0, 'start': '00:00:00:00'}))
panel.cavena_start_combo.combobox.setCurrentIndex(1)
check('chosen, and remembered', (panel.get_config()['options'].get('start'),
                                 _SubtitlesPanel().cavena_start_combo.combobox.currentData()), ('10:00:00:00', '10:00:00:00'))
panel.format_combo.setCurrentText('CAP')
panel._refresh_options()
check('not for CAP', panel.cavena_options.isHidden(), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
