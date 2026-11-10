"""Cheetah CAP: reading files laid out byte for byte as Subtitle Edit's
reader and writer have them (captions with an end and without one, the
style bytes for the places, italics, the line-21 extras and Windows-1252,
the older EA 10 format), writing what they take, round trips at 25 and
29.97, and the file and the export dialog.

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
from subtitld.modules import cheetahcap, file_io, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def header(count):
    return (bytes.fromhex('ea220100') + bytes([count, 0]) + bytes.fromhex('09a8af4f')).ljust(128, b'\x00')


# A file as Subtitle Edit writes one, in hex: the header, then
#   1. 00:00:01:00 to 00:00:02:12, two lines, the first italic, bottom centre;
#   2. 00:00:03:00 to 00:00:04:00, at the top right (03 01 1C), with the
#      line-21 extras (é at 83, ñ at 8F, ♪ at 81) and Windows-1252 (¾ at BE);
#   3. 00:00:05:00, no end (41, then 02 01 00 00), five bytes of style, left;
#   4. 00:00:06:00 to 00:00:07:00, in the middle (row 08), an unknown code
#      (C5) that is not text.
RECORDS = bytes.fromhex(
    '25 62 00000100 0000020c 120100000000030f10' + 'd0' + b'Hello,'.hex() + '00000000' + b'world.'.hex() + '00'
    '22 61 00000300 00000400 12010000000003011c' + b'Caf'.hex() + '83' + b' ma'.hex() + '8f' + b'ana '.hex() + '81be' + '00'
    '14 41 00000500 02010000 0000030f05' + b'Left'.hex() + '00'
    '1b 61 00000600 00000700 120100000000030810' + b'Mid'.hex() + 'c5' + b'dle'.hex() + '00'
    .replace(' ', ''))
SAMPLE = header(4) + RECORDS

print('reading')
segments, settings = cheetahcap.parse(SAMPLE)
check('times, italics on their line, the places, the extras and Windows-1252', [(s['start'], s['end'], s['text']) for s in segments], [
    (1.0, 2.48, '<i>Hello,</i>\nworld.'),
    (3.0, 4.0, '{\\an9}Café mañana ♪¾'),
    (5.0, 6.0, '{\\an1}Left'),
    (6.0, 7.0, '{\\an5}Middle')])
check('its settings: the rate it was read at', settings, {'fps': 25.0})
check('29.97: frames of a second at that rate', cheetahcap.parse(SAMPLE, 29.97)[0][0]['end'], round(2 + 12 / 29.97, 3))
last_open = header(1) + bytes.fromhex('14 41 00000500 02010000 0000030f10'.replace(' ', '') + b'Last'.hex() + '00')
check('the last caption without an end: long enough to read', cheetahcap.parse(last_open)[0][0]['end'], 6.0)
OLD = bytes.fromhex('ea10').ljust(128, b'\x00')
record = bytearray(178)
record[0:8] = bytes([0, 0, 1, 5, 0, 0, 3, 0])
record[0x1A:0x1A + 5] = b'First'
record[0x40:0x40 + 6] = b'Second'
OLD += bytes(record) + bytes(178)
check('the older format (EA 10): two lines of 38, and an empty record left out', cheetahcap.parse(OLD)[0],
      [{'start': 1.2, 'end': 3.0, 'text': 'First\nSecond'}])
try:
    cheetahcap.parse(b'NCI caption file'.ljust(512, b' '))
    check('not CAP: refused', None, 'ValueError')
except ValueError as error:
    check('not CAP: refused', str(error).startswith('Not a Cheetah CAP file'), True)

print('writing')
written = cheetahcap.write([{'start': 1.0, 'end': 2.0, 'text': 'Hello'}])
check('a header as Subtitle Edit writes it', (written[:10].hex(), written[:128] == header(1)), ('ea220100010009a8af4f', True))
check('a record as Subtitle Edit writes one: length, kind, start, end, style, text, a closing 00',
      written[128:].hex(), '1961' + '00000100' + '00000200' + '120100000000030f10' + b'Hello'.hex() + '00')
places = {keypad: cheetahcap.write([{'start': 1, 'end': 2, 'text': ('{\\an%d}' % keypad) + 'x'}])[128 + 17:128 + 19].hex()
          for keypad in range(1, 10)}
check('rows and columns for the nine places', places, {
    1: '0f02', 2: '0f10', 3: '0f1e', 4: '0802', 5: '0810', 6: '081e', 7: '0102', 8: '0110', 9: '011e'})
check('two lines: kind 62, four zeros between them, italics a line at a time',
      cheetahcap.write([{'start': 0, 'end': 1, 'text': 'one\n<i>two</i>'}])[129:130] + cheetahcap.write(
          [{'start': 0, 'end': 1, 'text': 'one\n<i>two</i>'}])[128 + 19:-1], b'\x62one\x00\x00\x00\x00\xd0two')
check('characters: the extras, Windows-1252 below C0, else without the accent, spelled or "?"',
      cheetahcap.write([{'start': 0, 'end': 1, 'text': 'é½ — ¾ Ü ß … “q” ア'}])[128 + 19:-1],
      b'\x83\x94 \x97 \xbe U ss ... "q" ?')

print('round trips')
trip = [
    {'start': 1.0, 'end': 2.48, 'text': '<i>Italic all through</i>\nand plain'},
    {'start': 3.04, 'end': 5.0, 'text': '{\\an7}Top left: á é í ó ú â ê î ô û à è Ñ ñ ç ¢ £ ¿ ½ ® ♪'},
    {'start': 5.0, 'end': 6.0, 'text': '{\\an6}Middle right, with Windows-1252: ¡ § « » ° ± µ ¶ ·'},
    {'start': 7.0, 'end': 9.96, 'text': '{\\an3}Bottom right'},
]
check('at 25', cheetahcap.parse(cheetahcap.write(trip, fps=25), 25)[0], trip)
ntsc = [{'start': round(1 + 15 / 29.97, 3), 'end': round(2 + 29 / 29.97, 3), 'text': 'At 29.97'},
        {'start': 3600.0, 'end': round(3601 + 1 / 29.97, 3), 'text': 'An hour in'}]
check('at 29.97, frames of a second', cheetahcap.parse(cheetahcap.write(ntsc, fps=29.97), 29.97)[0], ntsc)
check('formatting it has not (bold, colours) left out', cheetahcap.parse(cheetahcap.write(
    [{'start': 0, 'end': 1, 'text': '<b>Bold</b> <font color="#ff0000">red</font>'}]))[0][0]['text'], 'Bold red')
long_text = 'x' * 400
check('a caption longer than a record holds: cut where the length byte ends',
      len(cheetahcap.parse(cheetahcap.write([{'start': 0, 'end': 1, 'text': long_text}]))[0][0]['text']), 235)

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.cap').write_bytes(SAMPLE)
session.FORMAT = {}
session.VIDEO = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.cap'))
check('opens, at 25 without a video; the rate kept', (fmt, len(opened), session.FORMAT['options'].get('fps')), ('CAP', 4, 25.0))
session.VIDEO = {'framerate': 29.97}
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.cap'))
check("with a video, at the video's rate", (opened[0]['end'], session.FORMAT['options'].get('fps')), (round(2 + 12 / 29.97, 3), 29.97))
session.SUBTITLE['segments'] = trip
session.VIDEO = {'framerate': 25.0, 'filepath': '/videos/tears.mov'}
session.FORMAT = {'options': {}}
file_io.save_file(str(folder / 'out.cap'), 'CAP')
check('saved, and read back', cheetahcap.read(str(folder / 'out.cap'))[0], trip)
(folder / 'not.cap').write_bytes(b'Wireshark capture'.ljust(1024, b'\x00'))
try:
    file_io.process_subtitles_file(str(folder / 'not.cap'))
    check("a packet capture's .cap: says what it is not", None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check("a packet capture's .cap: says what it is not", str(error).startswith('Not a Cheetah CAP file'), True)
check('the extension is CAP\'s', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['CAP']['extensions'], ['cap'])

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.VIDEO = {'framerate': 29.97}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('CAP')
panel._refresh_options()
check("CAP: the frame rate, the video's", (panel.sub_options.isHidden(), panel.text_options.isHidden(),
      panel.get_config()['options'].get('fps')), (False, False, 29.97))
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('not for SRT', panel.sub_options.isHidden(), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
