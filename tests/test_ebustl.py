"""EBU STL (Tech 3264): reading what broadcasters exchange, built here
block by block (teletext's double height, colours and boxes, open
subtitling's italics, ISO 6937's accents before their letters, another
character table, extension blocks, comments and user data, a programme
that starts at 10:00:00:00, rows and justification), writing what
inserters take, and the export's options.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, struct, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import ebustl, file_io, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def gsi(dfc='STL25.01', dsc='1', cct='00', lc='09', mnr='23', tcs='1', tcp='00000000'):
    fields = [('850', 3), (dfc, 8), (dsc, 1), (cct, 2), (lc, 2), ('Tears of Steel', 32), ('', 32 * 5), ('', 16),
              ('261011', 12), ('00', 2), ('00000', 10), ('001', 3), ('40', 2), (mnr, 2), (tcs, 1), (tcp, 8),
              ('00000000', 8), ('11', 2), ('', 3 + 32 * 3 + 75 + 576)]
    data = b''.join(value.encode('ascii').ljust(size, b' ') for value, size in fields)
    assert len(data) == 1024
    return data


def tti(sn, tci, tco, text, vp=22, jc=2, ebn=0xFF, cf=0, cs=0):
    return struct.pack('<BHBBBBBBBBBBBBB112s', 0, sn, ebn, cs, *tci, *tco, vp, jc, cf, text.ljust(112, b'\x8f'))


DH, WHITE, YELLOW, CYAN, BOX, CRLF = b'\x0d', b'\x07', b'\x03', b'\x06', b'\x0b\x0b', b'\x8a'
TELETEXT = gsi(tcp='10000000') + b''.join([
    tti(1, (10, 0, 1, 0), (10, 0, 2, 12), DH + WHITE + BOX + b"You're a jerk," + CRLF + CRLF + DH + WHITE + BOX + b'Celia.', vp=20),
    tti(2, (10, 0, 3, 0), (10, 0, 4, 0), DH + YELLOW + BOX + b'Yellow at the top', vp=1),
    tti(3, (10, 0, 5, 0), (10, 0, 6, 0), DH + WHITE + BOX + b'Plain, ' + CYAN + b'cyan', vp=22, jc=1),
    tti(4, (10, 0, 7, 0), (10, 0, 8, 0), b'A comment, not a subtitle', cf=1),
    tti(5, (10, 0, 9, 0), (10, 0, 10, 0), b'Caf\xc2e, \xc4ao \xd5 \xe8\xc2od\xc2z \xc2g ' + b'x' * 85, ebn=0x00),
    tti(5, (10, 0, 9, 0), (10, 0, 10, 0), b'and the rest', ebn=0xFF),
    tti(6, (10, 0, 11, 0), (10, 0, 12, 0), b'User data', ebn=0xFE),
    tti(7, (10, 0, 13, 0), (10, 0, 14, 0), DH + WHITE + BOX + b'Middle', vp=11, jc=3),
])

print('reading')
segments, settings = ebustl.parse(TELETEXT)
check('from the programme start; double height and boxes go; CR/LFs one break; colours; rows and justification as places',
      [(s['start'], s['end'], s['text']) for s in segments][:3], [
          (1.0, 2.48, "You're a jerk,\nCelia."),
          (3.0, 4.0, '{\\an8}<font color="#ffff00">Yellow at the top</font>'),
          (5.0, 6.0, '{\\an1}Plain, <font color="#00ffff">cyan</font>')])
check('ISO 6937: accents before their letter, ♪, Ł, ģ; an extension block joined; comments and user data left out',
      [s['text'][:24] for s in segments][3:], ['Café, ão ♪ Łódź ģ xxxxxx', '{\\an6}Middle'])
check('the extension block\'s text at the end', segments[3]['text'].endswith('xand the rest'), True)
check('its settings', settings, {'language': 'en', 'fps': 25, 'display': 'teletext', 'start': '10:00:00:00'})
OPEN = gsi(dfc='STL30.01', dsc='0', cct='01', lc='56', tcs='0', tcp='10000000') + tti(
    1, (0, 0, 1, 0), (0, 0, 2, 15), b'\x80' + 'Привет'.encode('iso8859_5') + b'\x81 ' + 'мир'.encode('iso8859_5'), vp=22)
opened = ebustl.parse(OPEN)
check('STL30.01 at 29.97; Cyrillic; italics; a start not in use; Russian', (
    [(s['start'], s['end'], s['text']) for s in opened[0]], opened[1]['language'], opened[1]['start']),
      ([(1.001, 2.502, '<i>Привет</i> мир')], 'ru', '00:00:00:00'))
check('a start after the subtitles is not subtracted', ebustl.parse(gsi(tcp='10000000') + tti(1, (0, 0, 1, 0), (0, 0, 2, 0), b'Early'))[0][0]['start'], 1.0)
try:
    ebustl.parse(b'Not STL at all'.ljust(2048, b' '))
    check('not STL: refused', None, 'ValueError')
except ValueError as error:
    check('not STL: refused', str(error).startswith('Not an EBU STL file'), True)

print('writing')
written_segments = [
    {'start': 1.0, 'end': 2.5, 'text': "You're a <i>jerk</i>,\nCelia."},
    {'start': 2.5, 'end': 4.0, 'text': '{\\an8}<font color="#ffd700">Gold at the top</font>'},
    {'start': 5.0, 'end': 7.0, 'text': 'Ação, café, naïve — “quotes” ♪ ½ Łódź ģ ő'},
    {'start': 8.0, 'end': 10.0, 'text': '{\\an1}Left <u>under</u> and <font color="#00ff00">green tail</font>'},
    {'start': 12.0, 'end': 13.0, 'text': 'A long line ' * 12},
    {'start': 14.0, 'end': 15.0, 'text': ' <i> </i> '},
]
teletext = ebustl.write(written_segments, fps=25, display='teletext', start='10:00:00:00', language='pt-br', title='Tears')
check('a GSI for teletext at 25, ISO 6937, Portuguese, the start and the counts', (
    teletext[3:11], teletext[11:12], teletext[12:14], teletext[14:16], teletext[16:21], teletext[238:248], teletext[255:264]),
      (b'STL25.01', b'1', b'00', b'21', b'Tears', b'0000600005', b'110000000'))
blocks = [teletext[1024 + i * 128:1024 + (i + 1) * 128] for i in range((len(teletext) - 1024) // 128)]
check('a block a subtitle, the long one two (5 subtitles, 6 blocks); times from 10:00:00:00; the rows at the bottom, the top', (
    len(blocks), [block[3] for block in blocks], list(blocks[0][5:13]), [block[13] for block in blocks]),
      (6, [255, 255, 255, 255, 0, 255], [10, 0, 1, 0, 10, 0, 2, 12], [20, 1, 22, 22, 22, 22]))
check('teletext: double height, the colour, the box, CR/LF twice', blocks[0][16:16 + 31].rstrip(b'\x8f'),
      b"\x0d\x07\x0b\x0bYou're a jerk,\x8a\x8a\x0d\x07\x0b\x0bCelia.")
check('read back, teletext (no italics in teletext)', [s['text'] for s in ebustl.parse(teletext)[0]], [
    "You're a jerk,\nCelia.", '{\\an8}<font color="#ffff00">Gold at the top</font>',
    'Ação, café, naïve — “quotes” ♪ ½ Łódź ģ ő', '{\\an1}Left under and <font color="#00ff00">green tail</font>',
    ('A long line ' * 12).strip()])
open_written = ebustl.write(written_segments, fps=30, display='open')
check('open subtitles at 30: italics and underline in place of spaces', (open_written[3:11], open_written[11:12],
      [s['text'] for s in ebustl.parse(open_written)[0]][:4:3]),
      (b'STL30.01', b'0', ["You're a <i>jerk,</i>\nCelia.", '{\\an1}Left <u>under</u> and <font color="#00ff00">green tail</font>']))
russian = ebustl.write([{'start': 1, 'end': 2, 'text': 'Привет, мир'}], language='ru')
check('Cyrillic in its own table', (russian[12:14], russian[14:16], ebustl.parse(russian)[0][0]['text']), (b'01', b'56', 'Привет, мир'))

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.stl').write_bytes(TELETEXT)
session.FORMAT = {}
session.SUBTITLE['language'] = 'pt-br'
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.stl'))
check('opens; its language the project\'s; its settings kept to save it again', (
    fmt, len(opened), session.SUBTITLE['language'], {key: session.FORMAT['options'].get(key) for key in ('stl_fps', 'display', 'start')}),
      ('STL', 5, 'en-us', {'stl_fps': 25, 'display': 'teletext', 'start': '10:00:00:00'}))
session.SUBTITLE['segments'] = written_segments
session.VIDEO = {'framerate': 29.97, 'filepath': '/videos/tears.mov'}
session.FORMAT = {'options': {'display': 'open', 'start': '00:00:00:00'}}
file_io.save_file(str(folder / 'out.stl'), 'STL')
saved = (folder / 'out.stl').read_bytes()
check("saved: the video's rate, the display asked, the title from the video", (saved[3:11], saved[11:12], saved[16:21]),
      (b'STL30.01', b'0', b'tears'))
(folder / 'not.stl').write_bytes(b'solid cube\nfacet normal 0 0 1\n' * 100)
try:
    file_io.process_subtitles_file(str(folder / 'not.stl'))
    check('a 3D model\'s .stl: says what it is not', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check("a 3D model's .stl: says what it is not", str(error).startswith('Not an EBU STL file'), True)

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('STL')
panel._refresh_options()
check("STL: its options, the rate the video's (29.97: 30)", (panel.stl_options.isHidden(), panel.text_options.isHidden(),
      {key: panel.get_config()['options'].get(key) for key in ('stl_fps', 'display', 'start')}),
      (False, False, {'stl_fps': 30, 'display': 'teletext', 'start': '00:00:00:00'}))
panel.stl_display_combo.combobox.setCurrentIndex(1)
panel.stl_start_combo.combobox.setCurrentIndex(1)
check('chosen', {key: panel.get_config()['options'].get(key) for key in ('display', 'start')},
      {'display': 'open', 'start': '10:00:00:00'})
check('remembered', (_SubtitlesPanel().stl_display_combo.combobox.currentData(), _SubtitlesPanel().stl_start_combo.combobox.currentData()),
      ('open', '10:00:00:00'))
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('not for SRT', panel.stl_options.isHidden(), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
